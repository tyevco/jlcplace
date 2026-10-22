"""Reports of a checked CPL (pipeline.Run): the terminal summary, JSON, the preview checklist (Markdown or CSV),
JUnit XML, GitHub annotation lines, and one self-contained HTML page (inline CSS and SVG, no external resources).

Drawings are in the board frame (y down, as KiCad shows it). The bottom side is drawn seen from below, as JLC's
placement preview shows it: x is mirrored (x -> -x) while text stays readable. KiCad copper pads are filled and
numbered; the EasyEDA pads are outlined where JLC puts them for the CPL row (convention.ee_to_board under the run's
bottom reading); "+" marks the row's point and "o" the point jlcplace would write.

The JSON, checklist, JUnit and annotation outputs hold derived data only (verdicts, pad numbers, distances). The HTML
draws EasyEDA pad outlines: it is for the user's own review, like the cache, and is not meant to be published. As the
records' licence asks, it declares their source, the JLCEDA/EasyEDA Official Library with links to its home pages,
above the board views and the part cards (where the outlines are drawn) and in its footer.
"""

from __future__ import annotations

import csv
import datetime
import html as _html
import io
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from . import __version__, geom
from .convention import BOTTOM_READINGS, cpl_to_board, ee_to_board, library_to_board
from .cpl import assembled, natural_key
from .model import BOTTOM, FORMULA_START, GREEN, RED, TOP, VERIFIED, YELLOW, CplRow, EEFootprint, NotFound, \
    RowResult, printable
from .parts import norm_number
from .pipeline import CheckItem, Run, key_reasons, proxy_footprint

TOOL = f"jlcplace {__version__}"
_ANSI = {RED: "\033[1;31m", YELLOW: "\033[1;33m", GREEN: "\033[32m", VERIFIED: "\033[1;32m"}
_ORDER = {RED: 0, YELLOW: 1, GREEN: 2, VERIFIED: 3}           # report order: worst first
SHOW = 12                                                      # checklist refs per category in the summary
#: where the drawn EasyEDA pad outlines come from, as the library's own notice asks it to be declared
SOURCE_LIBRARY = "JLCEDA/EasyEDA Official Library"
SOURCE_LINKS = ("https://lceda.cn/", "https://easyeda.com")
SOURCE_NOTICE = (f"The EasyEDA pad outlines drawn here are derived from footprints of the {SOURCE_LIBRARY} "
                 f"({', '.join(SOURCE_LINKS)}), whose copyright and related rights belong to JLCEDA/EasyEDA.")


def _sorted(results: list[RowResult]) -> list[tuple[int, RowResult]]:
    return sorted(enumerate(results), key=lambda ir: (_ORDER.get(ir[1].verdict, 0), natural_key(ir[1].ref), ir[0]))


def _name(path) -> str:
    return Path(path).name if path else ""


def _layer(run: Run, res: RowResult) -> str:
    part = run.board.part(res.ref)
    return part.layer if part is not None else (res.row.layer if res.row else "")


# ------------------------------------------------------------------------------------------------------- terminal

def plural(n: int, word: str, many: str = "") -> str:
    """'1 code', '2 codes'."""
    return f"{n} {word if n == 1 else many or word + 's'}"


def ref_ranges(refs, dash: str = "\u2013") -> str:
    """References with runs of three or more consecutive numbers (one prefix) as ranges: 'C102–C112 C114 C116–C148'."""
    out: list[str] = []
    run: list[tuple[str, int, str]] = []

    def flush():
        if len(run) >= 3:
            out.append(f"{run[0][2]}{dash}{run[-1][2]}")
        else:
            out.extend(r[2] for r in run)
        run.clear()

    for ref in refs:
        m = re.fullmatch(r"(.*?)(\d+)", ref)
        if m is None:
            flush()
            out.append(ref)
            continue
        if not (run and m.group(1) == run[-1][0] and int(m.group(2)) == run[-1][1] + 1):
            flush()
        run.append((m.group(1), int(m.group(2)), ref))
    flush()
    return " ".join(out)


def _label(it: CheckItem, dash: str = "\u2013") -> str:
    """The item's part; a group's parts ('U40 U41'), with the one to look at when it is named ('C107 of C102–C112
    C114–C148')."""
    if len(it.refs) <= 1:
        return it.ref
    return f"{it.ref} of {ref_ranges(it.refs, dash)}" if it.category == "odd angle" else ref_ranges(it.refs, dash)


def _parts(items: list[CheckItem]) -> str:
    """'12 items' or '12 items, 99 parts'."""
    n = sum(max(1, len(it.refs)) for it in items)
    return plural(len(items), "item") + (f", {plural(n, 'part')}" if n != len(items) else "")


def headline(res: RowResult) -> str:
    """'status: first message', the status left out when the message already says it ('2 of 2 EasyEDA pads land off
    copper', 'placement not simulated: ...')."""
    msg = res.messages[0] if res.messages else ""
    words = res.status.replace("-", " ")
    if msg and re.search(rf"\b{re.escape(words)}\b", msg, re.I):
        return msg
    return f"{res.status}: {msg}" if msg else res.status


def summary(run: Run, *, color: bool = False) -> str:
    """One line per red or yellow result with its first reason (and the correction's, where the numbering rests on
    it), BOM issues, a count line, and the preview checklist grouped by category. Text from files and from EasyEDA
    is shown with its control characters as \\xNN (model.printable): it goes to terminals and CI logs."""
    def paint(v: str, text: str) -> str:
        return f"{_ANSI[v]}{text}\033[0m" if color and v in _ANSI else text

    b = run.board
    out = [printable(f"{TOOL}: {_name(b.path)} ({b.source}, {len(b.parts)} parts, origin {b.origin_kind} "
                     f"{b.origin[0]:g}, {b.origin[1]:g}) vs {_name(run.cpl_path) or 'the rows jlcplace would write'} "
                     f"({len(run.rows)} rows), bottom reading {run.reading}")]
    bad = [r for _, r in _sorted(run.results) if r.verdict in (RED, YELLOW)]
    w = max((len(printable(r.ref)) for r in bad), default=0)
    for r in bad:
        out.append(f"{paint(r.verdict, f'{r.verdict.upper():<6}')}  "
                   + printable(f"{printable(r.ref):<{w}}  {r.lcsc or '-':<10} {_layer(run, r):<6}  {headline(r)}"))
        info = run.resolved.get(r.ref)
        if (info is not None and info.corr is not None and r.status in ("renumbered", "wrong-pad")
                and info.corr.verdict in (RED, YELLOW) and info.corr.reasons):
            out.append(printable(f"{'':6}  {'':<{w}}  correction ({info.source}, {info.corr.status}, "
                                 f"{info.corr.verdict}): {key_reasons(info.corr, 1)[0]}"))
    for m in run.bom_messages:
        out.append(f"{paint(YELLOW, 'BOM   ')}  {printable(m)}")
    c = run.counts()
    out.append(f"{len(run.results)} results: "
               + ", ".join(paint(v, f"{c[v]} {v}") for v in (RED, YELLOW, GREEN, VERIFIED))
               + (f"; {plural(len(run.bom_messages), 'BOM issue')}" if run.bom_messages else ""))
    items = run.checklist()
    if items:
        out.append(f"Preview checklist ({_parts(items)}): look at these in JLC's placement preview")
        groups: dict[str, list[CheckItem]] = {}
        for it in items:
            groups.setdefault(it.category, []).append(it)
        cw = max(len(k) for k in groups)
        for cat, its in groups.items():
            if cat == "uncertain correction":
                lines = [f"{_label(it, '..')}: {it.what}" for it in its]
            elif cat == "odd angle":
                single = [it for it in its if len(it.refs) <= 1]
                rots = {r.ref: r.row.rotation for r in run.results if r.row is not None}
                lines = []
                for it in its:
                    a = [rots[r] for r in it.refs if r in rots]
                    if len(it.refs) > 1 and a:
                        lines.append(f"{_label(it, '..')} ({it.lcsc}, {len(it.refs)} parts at {min(a):g} to "
                                     f"{max(a):g} deg: look at {it.ref})")
                if single:
                    lines.append(", ".join(f"{it.ref} ({it.rotation:g})" for it in single[:SHOW])
                                 + (f" and {len(single) - SHOW} more" if len(single) > SHOW else ""))
            else:
                lines = [", ".join(_label(it, "..") + (" (look at it first: it settles the bottom reading)"
                                                        if it.first else "") for it in its[:2 * SHOW])
                         + (f" and {len(its) - 2 * SHOW} more" if len(its) > 2 * SHOW else "")]
            for k, ln in enumerate(lines):
                out.append(printable(f"  {cat if k == 0 else '':<{cw}}  {ln}"))
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------------------------------------------- JSON

def _row(r: CplRow | None):
    return None if r is None else {"ref": r.ref, "x": r.x, "y": r.y, "layer": r.layer, "rotation": r.rotation}


def _corr(info) -> dict | None:
    if info is None or info.corr is None:
        return None
    c = info.corr
    return {"rot": c.rot, "dx": c.dx, "dy": c.dy, "mirror": c.mirror, "method": c.method, "rms": c.rms,
            "verdict": c.verdict, "status": c.status, "score": c.score, "matched": c.matched, "source": info.source,
            "reasons": list(c.reasons), "alternatives": len(c.alternatives)}


def _ee(info) -> dict | None:
    if info is None:
        return None
    ee = info.ee
    if isinstance(ee, EEFootprint):
        return {"found": True, "title": ee.title, "pads": len(ee.pads), "key": ee.key(), "proxy": info.proxy}
    return {"found": False, "message": ee.message if isinstance(ee, NotFound) else "", "proxy": info.proxy}


def to_dict(run: Run) -> dict:
    """The run as JSON-ready data: verdicts, messages, pad numbers and distances, no EasyEDA geometry."""
    b = run.board
    lines = run.row_lines or [None] * len(run.results)
    results = []
    for res, line in zip(run.results, lines):
        info = run.resolved.get(res.ref)
        results.append({"ref": res.ref, "lcsc": res.lcsc, "layer": _layer(run, res), "status": res.status,
                        "verdict": res.verdict, "messages": list(res.messages), "uncovered": list(res.uncovered),
                        "convention_sensitive": res.convention_sensitive, "line": line, "row": _row(res.row),
                        "expected": _row(res.expected),
                        "landings": [{"ee": lnd.ee_number, "kicad": lnd.kicad_number, "net": lnd.net,
                                      "dist": lnd.dist if math.isfinite(lnd.dist) else None}   # inf: no copper
                                     for lnd in res.landings],
                        "correction": _corr(info), "easyeda": _ee(info)})
    return {"tool": TOOL, "board": {"path": b.path, "source": b.source, "kicad_version": b.kicad_version,
                                    "origin": list(b.origin), "origin_kind": b.origin_kind, "parts": len(b.parts)},
            "cpl": run.cpl_path, "reading": run.reading,
            "summary": {"results": len(run.results), "rows": len(run.rows), **run.counts(),
                        "bom_issues": len(run.bom_messages), "worst": run.worst()},
            "results": results, "bom": list(run.bom_messages), "checklist": [_item(it) for it in run.checklist()]}


def _item(it: CheckItem) -> dict:
    return {"category": it.category, "ref": it.ref, "refs": list(it.refs) or [it.ref], "lcsc": it.lcsc,
            "layer": it.layer, "rotation": it.rotation, "what": it.what, "verdict": it.verdict, "also": list(it.also),
            "first": it.first}


# ------------------------------------------------------------------------------------------------------- checklist

_MARKDOWN = re.compile(r"([\\`*_\[\]<>])")


def _md(s) -> str:
    """Text for Markdown that renders as itself: no links, images, HTML, code or emphasis from a reference, a file
    name or a message (the checklist is posted to job summaries and pull requests)."""
    return _MARKDOWN.sub(r"\\\1", printable(s))


def checklist_markdown(run: Run) -> str:
    """The preview checklist as Markdown tick boxes, by category; references, file names and messages escaped."""
    items = run.checklist()
    out = [f"# JLC preview checklist: {_md(_name(run.board.path))}", "",
           f"CPL {_md(_name(run.cpl_path) or 'made by jlcplace')}, bottom reading `{run.reading}`, {TOOL}"
           + (f"; {_parts(items)}" if items else "") + ".", "",
           "Open the order's placement preview at JLC and look at each part below. Tick it when the part sits on "
           "its pads with pin 1 / polarity where the board wants it. An item for several parts names the one to "
           "look at."]
    if not items:
        out += ["", "Nothing to check: every row was simulated, no bottom part depends on the reading, every angle is "
                    "a multiple of 90 and every correction is green or verified."]
    cat = None
    for it in items:
        if it.category != cat:
            cat = it.category
            out += ["", f"## {cat.capitalize()}", ""]
        rot = f", rot {it.rotation:g}" if it.rotation is not None else ""
        head = f"**{_md(it.ref)}**"
        if len(it.refs) > 1:
            head = f"{head} of {_md(ref_ranges(it.refs))}" if it.category == "odd angle" else \
                f"**{_md(ref_ranges(it.refs))}**"
        n = f", {len(it.refs)} parts" if len(it.refs) > 1 else ""
        flag = " **(red: fix the row first)**" if it.verdict == RED else ""
        out.append(f"- [ ] {head} ({_md(it.lcsc or 'no code')}, {it.layer}{rot}{n}){flag}: {_md(it.what)}")
    return "\n".join(out) + "\n"


def _cell(s: str) -> str:
    """A CSV cell a spreadsheet shows as text: one that starts like a formula (= + - @, tab, CR) gets a leading
    apostrophe."""
    return "'" + s if s.startswith(FORMULA_START + ("\t", "\r")) else s


def checklist_csv(run: Run) -> str:
    """The preview checklist as CSV for a spreadsheet (a cell that would be a formula starts with an apostrophe)."""
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["category", "ref", "lcsc", "layer", "rotation", "row verdict", "what", "refs", "checked"])
    for it in run.checklist():
        w.writerow([_cell(printable(v)) for v in (it.category, it.ref, it.lcsc, it.layer,
                                                   "" if it.rotation is None else f"{it.rotation:g}", it.verdict,
                                                   it.what, " ".join(it.refs or [it.ref]), "")])
    return buf.getvalue()


# ------------------------------------------------------------------------------------------------------- CI

def _fails(verdict: str, fail_on: str) -> bool:
    return verdict == RED or (fail_on == YELLOW and verdict == YELLOW)


_XML_INVALID = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def _xml(s: str) -> str:
    """s with the characters XML 1.0 cannot hold replaced by U+FFFD (ElementTree writes them as they are, and every
    JUnit reader then refuses the file)."""
    return _XML_INVALID.sub("\ufffd", s)


def junit(run: Run, *, fail_on: str = RED) -> str:
    """One testsuite per run and one testcase per result (classname jlcplace.<board stem>, name = ref): red results
    fail, yellow ones too with fail_on yellow (otherwise their messages go to system-out); BOM issues are one more
    testcase. Characters XML cannot hold become U+FFFD."""
    stem = _xml(Path(run.board.path).stem or "board")
    cases = []
    for res in run.results:
        tc = ET.Element("testcase", classname=f"jlcplace.{stem}", name=_xml(res.ref))
        text = _xml("\n".join(res.messages))
        if _fails(res.verdict, fail_on):
            ET.SubElement(tc, "failure", type=res.verdict,
                          message=_xml(headline(res)) if res.messages else f"{res.status}: {res.verdict}").text = text
        elif text:
            ET.SubElement(tc, "system-out").text = f"{res.verdict} {res.status}\n{text}"
        cases.append(tc)
    if run.bom_messages:
        tc = ET.Element("testcase", classname=f"jlcplace.{stem}", name="BOM")
        tag = "failure" if fail_on == YELLOW else "system-out"
        el = ET.SubElement(tc, tag, type=YELLOW, message=_xml(run.bom_messages[0])) if tag == "failure" else \
            ET.SubElement(tc, tag)
        el.text = _xml("\n".join(run.bom_messages))
        cases.append(tc)
    failures = sum(1 for tc in cases if tc.find("failure") is not None)
    suite = ET.Element("testsuite", name=_xml(f"jlcplace {_name(run.cpl_path) or stem}"), tests=str(len(cases)),
                       failures=str(failures), errors="0", skipped="0",
                       timestamp=datetime.datetime.now().replace(microsecond=0).isoformat())
    suite.extend(cases)
    root = ET.Element("testsuites", name="jlcplace", tests=str(len(cases)), failures=str(failures))
    root.append(suite)
    ET.indent(root)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def _gh(s: str, prop: bool = False) -> str:
    """GitHub workflow-command escaping (data; properties also escape ':' and ','); other control characters (ESC)
    are shown as \\xNN."""
    s = printable(s.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A"))
    return s.replace(":", "%3A").replace(",", "%2C") if prop else s


def github_annotations(run: Run, *, fail_on: str = RED) -> list[str]:
    """`::error` for results that fail (red, or yellow with fail_on yellow), `::warning` for the other yellow ones
    and BOM issues, at the CPL row's line when known."""
    out = []
    lines = run.row_lines or [None] * len(run.results)
    for res, line in zip(run.results, lines):
        if res.verdict not in (RED, YELLOW):
            continue
        props = []
        if run.cpl_path:
            props.append(f"file={_gh(str(run.cpl_path), True)}")
            if line:
                props.append(f"line={line}")
        props.append(f"title={_gh(f'jlcplace {res.ref} {res.status}', True)}")
        kind = "error" if _fails(res.verdict, fail_on) else "warning"
        out.append(f"::{kind} {','.join(props)}::{_gh('; '.join(res.messages) or res.status)}")
    for m in run.bom_messages:
        out.append(f"::{'error' if fail_on == YELLOW else 'warning'} title=jlcplace BOM::{_gh(m)}")
    return out


# ------------------------------------------------------------------------------------------------------- SVG

def _f(v: float) -> str:
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _path(pts) -> str:
    return "M" + "L".join(f"{_f(x)} {_f(y)}" for x, y in pts) + "Z"


def _esc(s) -> str:
    return _html.escape(str(s), quote=True)


class _Box:
    """Axis-aligned bounds in view coordinates."""

    def __init__(self, x0=math.inf, y0=math.inf, x1=-math.inf, y1=-math.inf):
        self.x0, self.y0, self.x1, self.y1 = x0, y0, x1, y1

    def add(self, pts) -> "_Box":
        for x, y in pts:
            self.x0, self.y0, self.x1, self.y1 = min(self.x0, x), min(self.y0, y), max(self.x1, x), max(self.y1, y)
        return self

    @property
    def ok(self) -> bool:
        return self.x0 <= self.x1

    @property
    def size(self) -> float:
        return max(self.x1 - self.x0, self.y1 - self.y0) if self.ok else 0.0

    def grown(self, d: float) -> "_Box":
        return _Box(self.x0 - d, self.y0 - d, self.x1 + d, self.y1 + d)

    def holds(self, p) -> bool:
        return self.x0 <= p[0] <= self.x1 and self.y0 <= p[1] <= self.y1

    def viewbox(self) -> str:
        return f"{_f(self.x0)} {_f(self.y0)} {_f(self.x1 - self.x0)} {_f(self.y1 - self.y0)}"


def _v(q, mirror: bool):
    """Board point -> view point: the bottom is seen from below (x mirrored)."""
    return (-q[0], q[1]) if mirror else (q[0], q[1])


def _kicad_outline(part, pad, mirror: bool):
    return [_v(library_to_board(part, u), mirror) for u in geom.pad_outline(pad, arc_steps=4)]


def _ee_pads(run: Run, res: RowResult):
    """The EasyEDA pads the check placed for this result, in the order of res.landings, or []."""
    info = run.resolved.get(res.ref)
    if info is None or res.row is None or not res.landings:
        return []
    ee = proxy_footprint(info.part, info.corr) if info.proxy else info.ee
    if not isinstance(ee, EEFootprint):
        return []
    pads = [p for p in ee.pads if p.layer != BOTTOM]        # as check places them: EasyEDA's bottom layer is far
    return pads if len(pads) == len(res.landings) else []


def _ee_outline(run: Run, row: CplRow, pad, mirror: bool):
    C = cpl_to_board((row.x, row.y), run.board.origin)
    return [_v(ee_to_board(u, C, row.rotation, row.layer, run.reading), mirror)
            for u in geom.pad_outline(pad, arc_steps=4)]


def _landing_class(lnd) -> str:
    if lnd.kicad_number is None:
        return "off"
    return "own" if norm_number(lnd.ee_number) == norm_number(lnd.kicad_number) else "other"


def _point(run: Run, row: CplRow, mirror: bool):
    return _v(cpl_to_board((row.x, row.y), run.board.origin), mirror)


def _marker(p, r: float, cls: str, title: str) -> str:
    x, y = p
    if cls == "exp":
        return f'<circle class="exp" cx="{_f(x)}" cy="{_f(y)}" r="{_f(r * 0.7)}"><title>{_esc(title)}</title></circle>'
    return (f'<path class="cpl" d="M{_f(x - r)} {_f(y)}H{_f(x + r)}M{_f(x)} {_f(y - r)}V{_f(y + r)}">'
            f'<title>{_esc(title)}</title></path>')


def _text(p, text: str, size: float, cls: str = "kn") -> str:
    return f'<text class="{cls}" x="{_f(p[0])}" y="{_f(p[1])}" font-size="{_f(size)}">{_esc(text)}</text>'


def _number_size(pad, number: str) -> float:
    lo, hi = sorted((pad.w or 0.5, pad.h or 0.5))
    return max(0.1, min(1.0, 0.5 * lo, 1.2 * hi / max(1, len(number))))


GRID = 5.0                                     # mm: cell size of the neighbour index


def _near(side_pads, side: str, view: "_Box"):
    """The pads on a side whose centres fall in the grid cells the view touches (side_pads[side + "#grid"])."""
    grid = side_pads.get(side + "#grid", {})
    for i in range(math.floor(view.x0 / GRID), math.floor(view.x1 / GRID) + 1):
        for j in range(math.floor(view.y0 / GRID), math.floor(view.y1 / GRID) + 1):
            yield from grid.get((i, j), ())


def _side_pads(run: Run) -> dict[str, list]:
    """Per side: (part, view centre, view outline) of every copper pad, and a grid index of them ("<side>#grid")."""
    out: dict = {TOP: [], BOTTOM: [], TOP + "#grid": {}, BOTTOM + "#grid": {}}
    for part in run.board.parts:
        mirror = part.layer == BOTTOM
        for p in part.copper_pads():
            c = _v(library_to_board(part, (p.x, p.y)), mirror)
            item = (part, c, _kicad_outline(part, p, mirror))
            out[part.layer].append(item)
            out[part.layer + "#grid"].setdefault((math.floor(c[0] / GRID), math.floor(c[1] / GRID)), []).append(item)
    return out


def _part_svg(run: Run, res: RowResult, side_pads) -> str:
    """The part's KiCad pads (filled, numbered; neighbours' pads faint), its EasyEDA pads as the row places them
    (outlined by where they land), the row's point and jlcplace's."""
    part = run.board.part(res.ref)
    if part is None:
        return ""
    mirror = part.layer == BOTTOM
    kicad = [(p, _kicad_outline(part, p, mirror)) for p in part.copper_pads()]
    box = _Box()
    for _, o in kicad:
        box.add(o)
    if not box.ok:
        c = _v((part.x, part.y), mirror)
        box.add([(c[0] - 1, c[1] - 1), (c[0] + 1, c[1] + 1)])
    size = max(box.size, 1.0)
    near = box.grown(2.0 * size + 2.0)          # EasyEDA pads further out than this are reported, not drawn
    view = box.grown(0.12 * size + 0.5)
    ee, far = [], 0.0
    for pad, lnd in zip(_ee_pads(run, res), res.landings):
        o = _ee_outline(run, res.row, pad, mirror)
        if near.holds((sum(x for x, _ in o) / len(o), sum(y for _, y in o) / len(o))):
            view.add(o)
            ee.append((lnd, o))
        else:
            far = max(far, lnd.dist)
    marks = []
    for r, cls, what in ((res.row, "cpl", "CPL row"), (res.expected, "exp", "jlcplace's row")):
        if r is not None:
            p = _point(run, r, mirror)
            if near.holds(p):
                view.add([p])
            marks.append((p, cls, f"{what} ({r.x:.4f}, {r.y:.4f}), rot {r.rotation:g}"))
    view = view.grown(0.05 * view.size)
    out = [f'<svg class="ov" viewBox="{view.viewbox()}" role="img" aria-label="{_esc(res.ref)}: pads">']
    for q, c, o in _near(side_pads, part.layer, view):
        if q is not part and view.holds(c):
            out.append(f'<path class="nb" d="{_path(o)}"><title>{_esc(q.ref)}</title></path>')
    for p, o in kicad:
        tip = f"KiCad {part.ref} pad {p.number or '(unnumbered)'}" + (f", net {p.net}" if p.net else "") \
            + (f", pin {p.pin_name}" if p.pin_name else "")
        out.append(f'<path class="kp" d="{_path(o)}"><title>{_esc(tip)}</title></path>')
    for p, _ in kicad:
        if p.number:
            out.append(_text(_v(library_to_board(part, (p.x, p.y)), mirror), p.number, _number_size(p, p.number)))
    for lnd, o in ee:
        where = (f"KiCad pad {lnd.kicad_number}" + (f" ({lnd.net})" if lnd.net else "")
                 if lnd.kicad_number is not None else f"no copper (nearest pad centre {lnd.dist:.2f} mm away)")
        out.append(f'<path class="ee {_landing_class(lnd)}" d="{_path(o)}"><title>'
                   f'{_esc(f"EasyEDA pad {lnd.ee_number or chr(63)} on {where}")}</title></path>')
    for p, cls, tip in marks:
        if view.holds(p):
            out.append(_marker(p, 0.04 * view.size + 0.1, cls, tip))
    out.append("</svg>")
    if far:
        out.append(f'<p class="note">EasyEDA pads up to {far:.1f} mm from a KiCad pad are outside this view.</p>')
    return "".join(out)


def _board_svg(run: Run, side: str, side_pads, anchors: dict[str, str]) -> str:
    """Every copper pad on one side, the EasyEDA pads of every row on it (coloured by the row's verdict), labels on
    red and yellow parts (linked to their cards) and the CPL origin."""
    mirror = side == BOTTOM
    box = _Box()
    for _, _, o in side_pads[side]:
        box.add(o)
    results = [r for r in run.results if r.row is not None and r.row.layer == side and run.board.part(r.ref)]
    ee = [(res, _ee_outline(run, res.row, pad, mirror)) for res in results for pad in _ee_pads(run, res)]
    if not box.ok:
        for _, o in ee:
            box.add(o)
    if not box.ok:
        return f'<p class="note">Nothing on the {side}.</p>'
    view = box.grown(0.03 * box.size + 1.0)
    reach = view.grown(0.25 * view.size)        # rows far off the board stretch the view only so much
    for _, o in ee:
        view.add([q for q in o if reach.holds(q)])
    fs = 0.018 * view.size
    org = _v(run.board.origin, mirror)
    out = [f'<svg class="board" viewBox="{view.viewbox()}" role="img" aria-label="{side} side">']
    for part, _, o in side_pads[side]:
        out.append(f'<path class="{"kp" if assembled(part) else "kp dim"}" d="{_path(o)}"/>')
    for res, o in ee:
        out.append(f'<path class="ee v-{res.verdict}" d="{_path(o)}"/>')
    for res in results:
        if res.verdict in (RED, YELLOW):
            # above the part's copper and the pads JLC would put down (a label on them is lost in their outlines),
            # with a halo in the background colour; below them when there is no room above
            part = run.board.part(res.ref)
            box = _Box()
            for p in part.copper_pads():
                box.add(_kicad_outline(part, p, mirror))
            for r, o in ee:
                if r is res:
                    box.add(q for q in o if view.holds(q))
            if not box.ok:
                box.add([_point(run, res.row, mirror)])
            x = min(max((box.x0 + box.x1) / 2, view.x0 + 1.5 * fs), view.x1 - 1.5 * fs)
            y = box.y0 - 0.35 * fs if box.y0 - 1.2 * fs > view.y0 else box.y1 + 1.0 * fs
            label = (f'<text class="ref v-{res.verdict}" x="{_f(x)}" y="{_f(y)}" font-size="{_f(fs)}" '
                     f'stroke-width="{_f(0.3 * fs)}">{_esc(res.ref)}</text>')
            out.append(f'<a href="#{anchors.get(res.ref, "")}">{label}<title>{_esc(res.ref)}: '
                       f'{_esc(res.status)}</title></a>')
    ox, oy = run.board.origin
    out.append(_marker(org, 0.02 * view.size, "cpl", f"CPL origin ({ox:g}, {oy:g})"))
    out.append("</svg>")
    return "".join(out)


# ------------------------------------------------------------------------------------------------------- HTML

_DARK = ("--bg:#17181a;--fg:#e8e8e6;--muted:#a0a3a8;--card:#212326;--line:#3a3d42;--cu:#8f7420;--cu-fg:#fff3c4;"
         "--nb:#2d2f33;--own:#78aeff;--other:#f0a93a;--off:#ff6b6b;--red:#ff6b6b;--yellow:#f0b429;--green:#6cc070;"
         "--verified:#7fd88a;--red-bg:#3b2224;--yellow-bg:#3a3020;--green-bg:#1f3322;--mark:#e17cf2")
_CSS = """
:root{--bg:#f7f7f5;--fg:#1d1f21;--muted:#5f6368;--card:#fff;--line:#d9d9d4;--cu:#d4ad3a;--cu-fg:#3a2e00;--nb:#e6e4dc;
--own:#1a5fb4;--other:#c27c0e;--off:#c01c28;--red:#c01c28;--yellow:#946200;--green:#2e7d32;--verified:#146c2e;
--red-bg:#fbe9eb;--yellow-bg:#fdf3dc;--green-bg:#e9f5ea;--mark:#9c27b0}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){DARK}}
:root[data-theme="dark"]{DARK}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1200px;margin:0 auto;padding:16px}
h1{font-size:20px;margin:8px 0 4px}h2{font-size:16px;margin:24px 0 8px}
.sub,.note{color:var(--muted)}.note{font-size:12px;margin:4px 0}a{color:var(--own)}
code{font:12px ui-monospace,SFMono-Regular,Menlo,monospace;overflow-wrap:anywhere}
.counts{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}
.badge{display:inline-block;padding:1px 8px;border-radius:10px;font-weight:600;font-size:12px}
.b-red{background:var(--red-bg);color:var(--red)}.b-yellow{background:var(--yellow-bg);color:var(--yellow)}
.b-green{background:var(--green-bg);color:var(--green)}.b-verified{background:var(--green-bg);color:var(--verified)}
.legend{display:flex;flex-wrap:wrap;gap:14px;font-size:12px;color:var(--muted);margin:8px 0}
.legend span::before{content:"";display:inline-block;width:12px;height:9px;margin-right:5px;vertical-align:-1px;
border:2px solid transparent}
.l-kp::before{background:var(--cu)}.l-own::before{border-color:var(--own)!important}
.l-other::before{border-color:var(--other)!important}.l-off::before{border-color:var(--off)!important;
border-style:dashed!important}.l-mark::before{display:none!important}.l-vgreen::before{border-color:var(--own)!important;
opacity:.7}.l-vyellow::before{border-color:var(--other)!important}.l-vred::before{border-color:var(--off)!important}
.boards{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}
figure{margin:0;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px}
figcaption{font-size:12px;color:var(--muted);margin-bottom:4px}
svg{display:block;width:100%;height:auto}svg.board{max-height:75vh}svg.ov{max-height:340px}
.kp{fill:var(--cu);stroke:none}.kp.dim{opacity:.35}.nb{fill:var(--nb)}
.kn{fill:var(--cu-fg);text-anchor:middle;dominant-baseline:central}
.ee{fill:none;stroke-width:1.6;vector-effect:non-scaling-stroke}
.ee.own{stroke:var(--own)}.ee.other{stroke:var(--other)}.ee.off{stroke:var(--off);stroke-dasharray:3 2}
.ee.v-green,.ee.v-verified{stroke:var(--own);opacity:.7}.ee.v-yellow{stroke:var(--other)}.ee.v-red{stroke:var(--off)}
.cpl{stroke:var(--mark);stroke-width:1.5;vector-effect:non-scaling-stroke;fill:none}
.exp{stroke:var(--mark);stroke-width:1.2;vector-effect:non-scaling-stroke;fill:none;stroke-dasharray:2 2}
.ref{text-anchor:middle;font-weight:700;paint-order:stroke;stroke:var(--card);stroke-linejoin:round}
.ref.v-red{fill:var(--red)}.ref.v-yellow{fill:var(--yellow)}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{text-align:left;padding:4px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600}
.tablewrap{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:8px}
@media (max-width:700px){table.cl tr:first-child{display:none}table.cl tr{display:block;padding:6px 0;
border-bottom:1px solid var(--line)}table.cl td{display:inline-block;border:0;padding:1px 6px}
table.cl td:last-child{display:block}}
details.card{background:var(--card);border:1px solid var(--line);border-left:5px solid var(--line);border-radius:8px;
margin:10px 0}
details.card.red{border-left-color:var(--red)}details.card.yellow{border-left-color:var(--yellow)}
details.card.green{border-left-color:var(--green)}details.card.verified{border-left-color:var(--verified)}
details.card>summary{cursor:pointer;padding:8px 12px}details.card>summary b{font-size:15px}
.cbody{display:grid;grid-template-columns:minmax(220px,340px) minmax(0,1fr);gap:16px;padding:0 12px 12px}
@media (max-width:700px){.cbody{grid-template-columns:minmax(0,1fr)}}
dl{display:grid;grid-template-columns:max-content minmax(0,1fr);gap:2px 10px;margin:0 0 8px;font-size:13px}
dt{color:var(--muted)}dd{margin:0;overflow-wrap:anywhere}
ul{margin:4px 0 8px;padding-left:18px}li{margin:2px 0;overflow-wrap:anywhere}
details.sub>summary{cursor:pointer;color:var(--muted);font-size:12px}
footer{color:var(--muted);font-size:12px;margin:32px 0 16px;border-top:1px solid var(--line);padding-top:8px}
.source{background:var(--card);border:1px solid var(--line);border-left:5px solid var(--own);border-radius:8px;
padding:8px 12px;margin:12px 0}
""".replace("DARK", _DARK)


def _fmt_row(r: CplRow | None) -> str:
    return "&mdash;" if r is None else _esc(f"X {r.x:.4f}  Y {r.y:.4f}  {r.layer}  rot {r.rotation:g}")


def _badge(v: str) -> str:
    k = v if v in _ORDER else RED
    return f'<span class="badge b-{k}">{_esc((v or "?").upper())}</span>'


def _card(run: Run, res: RowResult, anchor: str, side_pads) -> str:
    part = run.board.part(res.ref)
    info = run.resolved.get(res.ref)
    v = res.verdict if res.verdict in _ORDER else RED
    head = (f"<summary>{_badge(v)} <b>{_esc(res.ref)}</b> <span class=\"sub\">{_esc(res.lcsc or 'no code')} "
            f"&middot; {_esc(_layer(run, res))} &middot; {_esc(res.status)}"
            + (" &middot; bottom reading matters" if res.convention_sensitive else "")
            + (f" &mdash; {_esc(res.messages[0])}" if res.messages and v in (RED, YELLOW) else "")
            + "</span></summary>")
    facts = [f"<dt>CPL row</dt><dd>{_fmt_row(res.row)}</dd>", f"<dt>jlcplace row</dt><dd>{_fmt_row(res.expected)}</dd>"]
    if part is not None:
        facts.append(f"<dt>KiCad</dt><dd>{_esc(part.footprint)} &middot; {_esc(part.value)} &middot; at "
                     f"({part.x:.4f}, {part.y:.4f}), {part.angle:g}&deg;</dd>")
    if info is not None:
        ee = info.ee
        if isinstance(ee, EEFootprint):
            facts.append(f"<dt>EasyEDA</dt><dd>{_esc(ee.title)} &middot; {len(ee.pads)} pads</dd>")
        elif isinstance(ee, NotFound):
            proxy = " (simulated with the KiCad pads through the stored correction)" if info.proxy else ""
            facts.append(f"<dt>EasyEDA</dt><dd>{_esc(ee.message)}{proxy}</dd>")
        c = info.corr
        if c is not None:
            facts.append(f"<dt>Correction</dt><dd>rot {c.rot:g}, dx {c.dx:.4f}, dy {c.dy:.4f}"
                         f"{', mirrored' if c.mirror else ''} &middot; {_esc(info.source)}/{_esc(c.method)} &middot; "
                         f"{_esc(c.status)} {_badge(c.verdict)} &middot; rms {c.rms:.3f} mm, {c.matched} matched</dd>")
    body = [f'<div class="cbody"><div>{_part_svg(run, res, side_pads)}</div><div><dl>{"".join(facts)}</dl>']
    if res.messages:
        body.append("<b>Check</b><ul>" + "".join(f"<li>{_esc(m)}</li>" for m in res.messages) + "</ul>")
    if info is not None and info.corr is not None and info.corr.reasons:
        body.append("<b>Correction</b><ul>" + "".join(f"<li>{_esc(m)}</li>" for m in info.corr.reasons) + "</ul>")
    if res.landings:
        rows = "".join(f"<tr><td>{_esc(lnd.ee_number or '?')}</td><td>{_esc(lnd.kicad_number or 'off copper')}</td>"
                       f"<td>{_esc(lnd.net)}</td><td>{lnd.dist:.3f}</td></tr>" for lnd in res.landings)
        body.append(f'<details class="sub"><summary>Where the {len(res.landings)} EasyEDA pads land</summary>'
                    f'<div class="tablewrap"><table><tr><th>EasyEDA pad</th><th>KiCad pad</th><th>net</th>'
                    f'<th>nearest pad centre, mm</th></tr>{rows}</table></div></details>')
    body.append("</div></div>")
    return (f'<details class="card {v}" id="{anchor}"{" open" if v in (RED, YELLOW) else ""}>{head}'
            + "".join(body) + "</details>")


def _source(lead: str = "Source of the EasyEDA pad outlines") -> str:
    """The licence's source declaration, with links: above the drawings, not only in the footer."""
    links = ", ".join(f'<a href="{_esc(u)}">{_esc(u)}</a>' for u in SOURCE_LINKS)
    return (f'<p class="source"><b>{_esc(lead)}:</b> footprints of the <b>{_esc(SOURCE_LIBRARY)}</b> ({links}). The '
            f'copyright and related rights in that library belong to JLCEDA/EasyEDA. This page is for your own '
            f'review: please do not publish it.</p>')


def html(run: Run) -> str:
    """The whole report as one HTML page with no external resources: counts, top and bottom overlays, the preview
    checklist, BOM issues, and one card per result (red, yellow, green, verified)."""
    b = run.board
    c = run.counts()
    side_pads = _side_pads(run)
    order = _sorted(run.results)
    anchors: dict[str, str] = {}
    for i, res in order:
        anchors.setdefault(res.ref, f"r{i}")
    title = f"jlcplace: {_name(b.path)}"
    alts = ", ".join(k for k in BOTTOM_READINGS if k != run.reading)
    out = ['<!doctype html><html lang="en"><head><meta charset="utf-8">'
           '<meta name="robots" content="noindex">'
           f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{_esc(title)}</title>'
           f'<style>{_CSS}</style></head><body><main>',
           f"<h1>{_esc(title)}</h1>",
           f'<p class="sub">CPL <code>{_esc(_name(run.cpl_path) or "made by jlcplace")}</code> ({len(run.rows)} '
           f'rows) against <code>{_esc(_name(b.path))}</code> ({_esc(b.source)}, {len(b.parts)} parts). Origin '
           f'{_esc(b.origin_kind)} ({b.origin[0]:g}, {b.origin[1]:g}); bottom reading <code>{_esc(run.reading)}'
           f'</code>, alternatives checked: {_esc(alts)}. {datetime.datetime.now():%Y-%m-%d %H:%M}, '
           f'{_esc(TOOL)}.</p>',
           '<div class="counts">'
           + "".join(f'<span class="badge b-{v}">{c[v]} {v}</span>' for v in (RED, YELLOW, GREEN, VERIFIED))
           + (f'<span class="badge b-yellow">{plural(len(run.bom_messages), "BOM issue")}</span>'
              if run.bom_messages else "")
           + "</div>",
           _source(),
           "<h2>Board</h2>",
           '<div class="legend"><span class="l-kp">KiCad copper pad</span><span class="l-vgreen">EasyEDA pads as JLC '
           'places them: green row</span><span class="l-vyellow">yellow row</span><span class="l-vred">red row'
           '</span><span class="l-mark">+ the CPL origin</span></div>',
           f'<div class="boards"><figure><figcaption>Top, seen from above</figcaption>'
           f'{_board_svg(run, TOP, side_pads, anchors)}</figure><figure><figcaption>Bottom, seen from below as '
           f'JLC&rsquo;s preview shows it (x mirrored)</figcaption>{_board_svg(run, BOTTOM, side_pads, anchors)}'
           f'</figure></div>',
           '<p class="note">EasyEDA pads are outlined in their row&rsquo;s verdict colour; red and yellow parts are '
           'labelled and link to their cards.</p>']
    items = run.checklist()
    out.append(f"<h2>Preview checklist ({_esc(_parts(items))})</h2>")
    if items:
        def link(ref: str) -> str:
            return f'<a href="#{anchors.get(ref, "")}">{_esc(ref)}</a>'

        def ref_cell(it: CheckItem) -> str:
            first = '<br><span class="sub">look at this one first</span>' if it.first else ""
            if len(it.refs) > 1 and it.category != "odd angle":
                return " ".join(link(r) for r in it.refs) + first
            many = (f'<br><span class="sub">of {_esc(ref_ranges(it.refs))} ({len(it.refs)} parts)</span>'
                    if len(it.refs) > 1 else "")
            return link(it.ref) + many + first

        rows = "".join(f'<tr><td>{_esc(it.category)}</td><td>{_badge(it.verdict)}</td><td>{ref_cell(it)}</td>'
                       f'<td>{_esc(it.lcsc)}</td><td>{_esc(it.layer)}</td><td>'
                       f'{"" if it.rotation is None else f"{it.rotation:g}"}</td><td>{_esc(it.what)}</td></tr>'
                       for it in items)
        out.append('<p class="note">Look at these parts in JLC&rsquo;s placement preview before ordering. A red row '
                   'is wrong already: fix the row first. An item for several parts names the one to look at.</p>'
                   '<div class="tablewrap"><table class="cl"><tr><th>check</th><th>row</th><th>ref</th><th>LCSC</th>'
                   f'<th>side</th><th>rot</th><th>what</th></tr>{rows}</table></div>')
    else:
        out.append('<p class="note">Nothing to look at in the preview.</p>')
    if run.bom_messages:
        out.append("<h2>BOM</h2><ul>" + "".join(f"<li>{_esc(m)}</li>" for m in run.bom_messages) + "</ul>")
    out.append(f"<h2>Parts ({len(run.results)})</h2>")
    out.append(_source("EasyEDA pads outlined on the cards"))
    out.append('<div class="legend"><span class="l-kp">KiCad copper pad</span><span class="l-own">EasyEDA pad as JLC '
               'places it, on its own number</span><span class="l-other">on another number</span><span class="l-off">'
               'off copper</span><span class="l-mark">+ the CPL row&rsquo;s point, &#9675; jlcplace&rsquo;s row '
               'point</span></div>')
    out.extend(_card(run, res, f"r{i}", side_pads) for i, res in order)
    notice = _esc(SOURCE_NOTICE)
    for u in SOURCE_LINKS:
        notice = notice.replace(_esc(u), f'<a href="{_esc(u)}">{_esc(u)}</a>')
    out.append(f"<footer>Made by {_esc(TOOL)}. {notice} This page is for your own review: please do not publish it."
               "</footer></main></body></html>")
    return "".join(out) + "\n"
