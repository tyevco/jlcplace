"""Reports of a checked CPL (pipeline.Run): the terminal summary, JSON, the preview checklist (Markdown or CSV),
JUnit XML, GitHub annotation lines, one self-contained HTML page (inline CSS and SVG, no external resources), and
overlay images of the whole board, one standalone SVG per side (PNG when a renderer is at hand).

Drawings are in the board frame (y down, as KiCad shows it). The bottom side is drawn seen from below, as JLC's
placement preview shows it: x is mirrored (x -> -x) while text stays readable. KiCad copper pads are filled and
numbered; the EasyEDA pads are outlined where JLC puts them for the CPL row (convention.ee_to_board under the run's
bottom reading); "+" marks the row's point and "o" the point jlcplace would write.

The JSON, checklist, JUnit and annotation outputs hold derived data only (verdicts, pad numbers, distances). The HTML
and the overlay images draw EasyEDA pad outlines: they are for the user's own review, like the cache, and are not
meant to be published. As the records' licence asks, they declare their source, the JLCEDA/EasyEDA Official Library
with links to its home pages, where the outlines are drawn (the HTML above its board views and part cards and in its
footer; each image in visible text under the drawing).
"""

from __future__ import annotations

import csv
import datetime
import html as _html
import io
import math
import os
import re
import shutil
import subprocess
import tempfile
import textwrap
import xml.etree.ElementTree as ET
from pathlib import Path

from . import __version__, geom
from . import board as _kb
from .convention import BOTTOM_READINGS, cpl_to_board, ee_to_board, library_to_board, rot
from .cpl import assembled, atomic_write, natural_key
from .model import BOTTOM, FORMULA_START, GREEN, RED, TOP, VERIFIED, YELLOW, CplRow, EEFootprint, NotFound, \
    RowResult, printable
from .parts import norm_number
from .check import jlc_part
from .pipeline import CheckItem, Finding, Run, gerber_notes, key_reasons, origin_lines, proxy_footprint

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
    part = jlc_part(run.board, res.ref)
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


def headline(res: RowResult | Finding) -> str:
    """'status: first message', the status left out when the message already says it ('2 of 2 EasyEDA pads land off
    copper', 'placement not simulated: ...', 'origin mismatch: ...')."""
    msg = res.message if isinstance(res, Finding) else res.messages[0] if res.messages else ""
    words = res.status.replace("-", " ")
    if msg and re.search(rf"\b{re.escape(words)}\b", msg, re.I):
        return msg
    return f"{res.status}: {msg}" if msg else res.status


def _origin(run: Run) -> str:
    """The CPL origin the rows were checked from: 'aux 100, 100', or '0, 0 from the Gerbers (absolute)'."""
    b = run.board
    if b.origin_kind != "gerber":
        return f"{b.origin_kind} {b.origin[0]:g}, {b.origin[1]:g}"
    named = run.gerbers.named if run.gerbers is not None else ""
    return f"{b.origin[0]:g}, {b.origin[1]:g} from the Gerbers" + (f" ({named})" if named else "")


def _gerbers(run: Run) -> str:
    """' over the Gerbers gerbers.zip' when the run was checked against a Gerber set, else ''."""
    return f" over the Gerbers {_name(run.gerbers_path)}" if run.gerbers_path else ""


def summary(run: Run, *, color: bool = False) -> str:
    """One line per red or yellow result with its first reason (and the correction's, where the numbering rests on
    it), BOM issues, a count line, and the preview checklist grouped by category. Text from files and from EasyEDA
    is shown with its control characters as \\xNN (model.printable): it goes to terminals and CI logs."""
    def paint(v: str, text: str) -> str:
        return f"{_ANSI[v]}{text}\033[0m" if color and v in _ANSI else text

    b = run.board
    out = [printable(f"{TOOL}: {_name(b.path)} ({b.source}, {len(b.parts)} parts, origin {_origin(run)}) vs "
                     f"{_name(run.cpl_path) or 'the rows jlcplace would write'} ({len(run.rows)} rows){_gerbers(run)}, "
                     f"bottom reading {run.reading}")]
    out += [printable(n) for n in run.lead]    # e.g. the record cache is empty: what to do before reading the rows
    for f in run.findings:                     # the whole run first: every row below follows from it
        out.append(f"{paint(f.verdict, f'{f.verdict.upper():<6}')}  {printable(headline(f))}")
    bad = [r for _, r in _sorted(run.results) if r.verdict in (RED, YELLOW)]
    w = max((len(printable(r.ref)) for r in bad), default=0)
    for r in bad:
        out.append(f"{paint(r.verdict, f'{r.verdict.upper():<6}')}  "
                   + printable(f"{printable(r.ref):<{w}}  {r.lcsc or '-':<10} {_layer(run, r):<6}  {headline(r)}"))
        info = run.resolved.get(r.ref)
        if (info is not None and info.corr is not None and r.status in ("renumbered", "wrong-pad", "reversed")
                and info.corr.verdict in (RED, YELLOW) and info.corr.reasons):
            out.append(printable(f"{'':6}  {'':<{w}}  correction ({info.source}, {info.corr.status}, "
                                 f"{info.corr.verdict}): {key_reasons(info.corr, 1)[0]}"))
    for m in run.bom_messages:
        out.append(f"{paint(YELLOW, 'BOM   ')}  {printable(m)}")
    c = run.counts()
    out.append(f"{len(run.results)} results: "
               + ", ".join(paint(v, f"{c[v]} {v}") for v in (RED, YELLOW, GREEN, VERIFIED))
               + (f"; {plural(len(run.bom_messages), 'BOM issue')}" if run.bom_messages else "")
               + "".join(f"; {f.status} {paint(f.verdict, f.verdict)}" for f in run.findings))
    if run.gerbers is None and not run.findings:      # once, short: the most common way to ruin every row
        out.append(printable(f"Gerbers not checked: plot them from this CPL's origin ({_origin(run)}), as JLC lays the "
                             f"CPL over them from their own 0, 0 (--gerbers checks it)"))
    items = run.checklist()
    if items:
        out.append(f"Preview checklist ({_parts(items)}): look at these in JLC's placement preview")
        groups: dict[str, list[CheckItem]] = {}
        for it in items:
            groups.setdefault(it.category, []).append(it)
        cw = max(len(k) for k in groups)
        for cat, its in groups.items():
            if cat in ("pinout", "uncertain correction"):
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
                        "bom_issues": len(run.bom_messages), "findings": len(run.findings), "worst": run.worst()},
            "findings": [{"status": f.status, "verdict": f.verdict, "message": f.message} for f in run.findings],
            "origin": _origin_dict(run), "results": results, "bom": list(run.bom_messages),
            "checklist": [_item(it) for it in run.checklist()]}


def _origin_dict(run: Run) -> dict:
    """The CPL origin the rows were checked from, the Gerbers' check (derived numbers: where their (0, 0) lies, how
    it was found, how much of the board they cover), and the rows' common offset."""
    b, g, off = run.board, run.gerbers, run.offset
    gerbers = None if g is None else {
        "path": run.gerbers_path, "origin": list(g.origin) if g.origin is not None else None, "named": g.named,
        "method": g.method, "flip": g.flip, "scale": g.scale, "residual": g.residual, "matched": g.matched,
        "board_pads": g.board_pads, "gerber_pads": g.gerber_pads, "pin_pairs": g.pin_pairs, "compared": g.compared,
        "coverage": g.coverage, "missing": g.missing[:50], "extra": len(g.extra), "mismatched": g.mismatched[:50],
        "belongs": g.belongs, "stale": g.stale, "notes": gerber_notes(g)}
    offset = None if off is None else {
        "dx": off.dx, "dy": off.dy, "rows": off.rows, "total": off.total, "origin": list(off.origin),
        "origin_kind": off.origin_kind, "scale": off.scale, "unit": off.unit, "y_up": off.y_up, "refs": off.refs,
        "in_place": off.in_place, "origin_error": off.origin_error}
    lines = origin_lines(run)
    return {"x": b.origin[0], "y": b.origin[1], "kind": b.origin_kind, "from_gerbers": b.origin_kind == "gerber",
            "verdict": (RED if any(f.status == "origin" for f in run.findings) else
                        "unchecked" if g is None else GREEN if b.origin_kind == "gerber" else RED),
            "note": lines[0][1] if lines else "", "gerbers": gerbers, "offset": offset}


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
    for v, text in origin_lines(run):          # every part's place hangs on it: before the parts
        out += ["", f"**Origin{f' ({v})' if v in (RED, YELLOW) else ''}:** {_md(text)}"]
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
        flag = "" if it.verdict != RED else " **(red: the LCSC part or the symbol is wrong)**" \
            if it.category == "pinout" else " **(red: fix the row first)**"
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
    for v, text in origin_lines(run):          # last: the items' rows keep their places for whoever reads the file
        w.writerow([_cell(printable(x)) for x in ("origin", "", "", "", "", v, text, "", "")])
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
    for f in run.findings or ([Finding("origin", GREEN, origin_lines(run)[0][1])] if run.gerbers else []):
        # the origin is a case of its own when the Gerbers were checked (or something about the whole run is wrong)
        tc = ET.Element("testcase", classname=f"jlcplace.{stem}", name=f.status)
        if _fails(f.verdict, fail_on):
            ET.SubElement(tc, "failure", type=f.verdict, message=_xml(headline(f))).text = _xml(f.message)
        else:
            ET.SubElement(tc, "system-out").text = _xml(f"{f.verdict} {f.status}\n{f.message}")
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
    for f in run.findings:
        props = ([f"file={_gh(str(run.cpl_path), True)}"] if run.cpl_path else []) + \
            [f"title={_gh(f'jlcplace {f.status}', True)}"]
        out.append(f"::{'error' if _fails(f.verdict, fail_on) else 'warning'} {','.join(props)}::{_gh(f.message)}")
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
    part = jlc_part(run.board, res.ref)
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
            part = jlc_part(run.board, res.ref)
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
.finding{background:var(--red-bg);border:1px solid var(--line);border-left:5px solid var(--red);border-radius:8px;
padding:8px 12px;margin:12px 0;overflow-wrap:anywhere}
""".replace("DARK", _DARK)


def _fmt_row(r: CplRow | None) -> str:
    return "&mdash;" if r is None else _esc(f"X {r.x:.4f}  Y {r.y:.4f}  {r.layer}  rot {r.rotation:g}")


def _badge(v: str) -> str:
    k = v if v in _ORDER else RED
    return f'<span class="badge b-{k}">{_esc((v or "?").upper())}</span>'


def _card(run: Run, res: RowResult, anchor: str, side_pads) -> str:
    part = jlc_part(run.board, res.ref)
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
           f'rows) against <code>{_esc(_name(b.path))}</code> ({_esc(b.source)}, {len(b.parts)} parts)'
           + (f' over the Gerbers <code>{_esc(_name(run.gerbers_path))}</code>' if run.gerbers_path else '')
           + f'. Origin {_esc(_origin(run))}; bottom reading <code>{_esc(run.reading)}'
           f'</code>, alternatives checked: {_esc(alts)}. {datetime.datetime.now():%Y-%m-%d %H:%M}, '
           f'{_esc(TOOL)}.</p>',
           "".join(f'<p class="finding">{_badge(f.verdict)} <b>{_esc(f.status)}</b>: {_esc(f.message)}</p>'
                   for f in run.findings),
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
    out.extend(f'<p class="note">{_badge(v) + " " if v else ""}<b>Origin:</b> {_esc(text)}</p>'
               for v, text in origin_lines(run) if not run.findings)
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


# ------------------------------------------------------------------------------------------------------- overlay
#
# One standalone SVG per side of the whole board, in px: a view point v (board frame, x mirrored on the bottom) is
# drawn at  px = corner + k . (v - view corner),  so text, strokes and circles keep their size whatever the board's.

OVERLAY_WIDTH = 1600                           # px: an overlay image's width
OVERLAY_MIN_VIEW = 20.0                        # mm: the least an overlay shows round a tiny board
_EDGE = {"line", "arc", "circle", "rect", "poly", "curve"}      # the gr_* / fp_* drawings an outline is made of
_OV = {"page": "#101214", "mask": "#14301f", "edge": "#d9cf7a", "cu": "#c9a227", "fg": "#e8e8e6", "muted": "#a3a7ad",
       "panel": "#1b1e22", GREEN: "#5aa9ff", VERIFIED: "#5aa9ff", YELLOW: "#ffc233", RED: "#ff4d4f",
       "origin": "#e17cf2", "gerber": "#4dd0e1"}
_FONT = "DejaVu Sans, Verdana, Arial, Helvetica, sans-serif"
_M = 24                                        # px: the image's margin
_LIST = 12                                     # problem parts listed under the drawing
#: more red and yellow parts than this on a side are not circled: the circles would hide the board (a CPL from
#: another origin makes every row red, and the red outlines then show the shift better than any circle)
CIRCLES = 40


def _edge_points(node, err: float = 0.01) -> list | None:
    """A gr_* / fp_* drawing on Edge.Cuts as a polyline (closed shapes end where they start), in the coordinates the
    file gives it; None for another layer or kind."""
    kind = node[0][3:]
    if kind not in _EDGE or _kb._atoms(_kb._kid(node, "layer"))[:1] != ["Edge.Cuts"]:
        return None

    def xy(key):
        return _kb._xy(_kb._kid(node, key))

    if kind == "line":
        return [xy("start"), xy("end")]
    if kind == "arc":
        a = _kb._arc_params(node)
        return _kb._arc(*a, err) if a else [xy("start"), xy("end")]       # collinear points: a straight line
    if kind == "circle":
        c = xy("center")
        pts = _kb._circle(c, math.dist(c, xy("end")), err)
        return pts + pts[:1]
    if kind == "rect":
        (x0, y0), (x1, y1) = xy("start"), xy("end")
        return [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    if kind == "poly":
        pts = _kb._poly_points(_kb._kid(node, "pts"), err)
        return pts + pts[:1] if pts else None
    q = [_kb._xy(c) for c in _kb._kids(_kb._kid(node, "pts"), "xy")]
    return _kb._bezier(q, err) if len(q) == 4 else None


def board_outline(path) -> list[list[tuple[float, float]]] | None:
    """The board outline as polylines in the board frame: the Edge.Cuts drawings of a .kicad_pcb file (lines, arcs,
    circles, rectangles, polygons, curves; a footprint's at board = P + R(theta) . s, s as the file stores it, as
    for its pads). None for a file that is not a board file (a JSON snapshot) or that draws no outline."""
    p = Path(path)
    if p.suffix.lower() != ".kicad_pcb" or not p.is_file():
        return None
    text = p.read_text(encoding="utf-8", errors="replace")
    out = []
    depth, start = 0, 0
    for t in _kb._SCAN.finditer(text):            # top-level nodes only, and only those that name Edge.Cuts parsed
        c = t.group()
        if c == "(":
            depth += 1
            start = t.start() if depth == 2 else start
        elif c == ")":
            if depth == 2 and "Edge.Cuts" in text[start:t.end()]:
                node = _kb.parse_sexpr(text, start, t.end())[0]
                if node[0] in ("footprint", "module"):
                    x, y, a = _kb._nums(_kb._kid(node, "at"), 3)
                    for g in node[1:]:
                        if isinstance(g, list) and g and isinstance(g[0], str) and g[0].startswith("fp_"):
                            pts = _edge_points(g)
                            if pts:
                                out.append([(x + u[0], y + u[1]) for u in (rot(q, a) for q in pts)])
                elif node[0].startswith("gr_"):
                    pts = _edge_points(node)
                    if pts:
                        out.append(pts)
            depth -= 1
    return out or None


def gerber_outline(gerbers, check) -> list[list[tuple[float, float]]] | None:
    """The Gerbers' board outline (the centre lines of their profile layer's strokes) in the board frame, through the
    map gerber.gerber_origin found (check: its OriginCheck); None when the set has no profile layer or the check no
    origin. `gerbers` is a GerberSet or a path gerber.read_gerbers takes."""
    from .gerber import MAX_PRIMITIVE, GerberSet, gerber_to_board, read_gerbers
    gs = gerbers if isinstance(gerbers, GerberSet) else read_gerbers(gerbers)
    if check is None or check.origin is None or gs.outline is None:
        return None
    draws = gs.outline.draws
    most = max(1, min(MAX_PRIMITIVE, 100_000 // max(1, len(draws))))    # many arcs: each drawn coarser
    return [[gerber_to_board(q, check.origin, check.flip, check.scale) for q in d.points(most)]
            for d in draws] or None


_rings = geom.rings              # the outline's closed rings (an outline drawn as separate pieces)


def _pxpath(rings, closed: bool = True) -> str:
    return "".join("M" + "L".join(f"{_f(x)} {_f(y)}" for x, y in r) + ("Z" if closed else "") for r in rings)


def _enclose(pts, pad: float = 7.0) -> tuple[float, float, float]:
    """(cx, cy, r) of a circle round px points: the centre of their box, a margin outside the farthest."""
    xs, ys = [x for x, _ in pts], [y for _, y in pts]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    return cx, cy, max(12.0, max(math.hypot(x - cx, y - cy) for x, y in pts) + pad)


def _ov_text(x, y, text: str, size: float, fill: str, *, weight: int = 400, anchor: str = "start", cls: str = "",
             ref: str | None = None, halo: bool = False) -> str:
    """A text element (printable, escaped); with a halo in the page colour drawn beneath it as its own element (a
    renderer without paint-order would draw the stroke over the glyphs)."""
    attrs = (f'x="{_f(x)}" y="{_f(y)}" font-size="{_f(size)}" font-weight="{weight}" text-anchor="{anchor}"')
    body = _esc(printable(text))
    under = (f'<text {attrs} fill="{_OV["page"]}" stroke="{_OV["page"]}" stroke-width="{_f(size / 3.5)}" '
             f'stroke-linejoin="round">{body}</text>' if halo else "")
    data = f' data-ref="{_esc(ref)}"' if ref is not None else ""
    klass = f' class="{cls}"' if cls else ""
    return f'{under}<text{klass}{data} {attrs} fill="{fill}">{body}</text>'


def _wrap(text: str, size: float, room: float) -> list[str]:
    return textwrap.wrap(printable(text), max(20, int(room / (0.56 * size))), break_long_words=False,
                         break_on_hyphens=False) or [""]


def overlay_svg(run: Run, side: str, *, outline=None, gerber_origin=None, notes=(), width: int = OVERLAY_WIDTH) -> str:
    """One side of the whole board as JLC's placement preview shows it, as a standalone SVG image in px: the KiCad
    copper pads, the EasyEDA pads where JLC puts them for every CPL row on this side (the run's CPL origin and bottom
    reading), outlined in the row's verdict colour, red and yellow parts circled in theirs and labelled (pads JLC
    would put down away from their part get a second circle, joined to the part's; none past CIRCLES, which the
    header says), the board outline (`outline`: board-frame polylines, from board_outline or gerber_outline; filled
    as the board where it closes), the CPL origin (+), the Gerbers' (0, 0) (`gerber_origin`, a board point: x), a
    header with `notes` (an origin mismatch), a legend, the red and yellow parts' reasons, and the source of the
    EasyEDA outlines. The bottom is seen from below (x mirrored)."""
    mirror, b, W = side == BOTTOM, run.board, int(width)
    side_pads = _side_pads(run)
    rows = [r for r in run.results if r.row is not None and r.row.layer == side and b.part(r.ref)]
    ee = [(res, pads) for res in rows
          for pads in [[_ee_outline(run, res.row, pad, mirror) for pad in _ee_pads(run, res)]] if pads]
    edge = [[_v(q, mirror) for q in ln] for ln in outline or () if len(ln) >= 2]
    empty = not side_pads[side] and not ee
    box = _Box()
    for _, _, o in side_pads[side]:
        box.add(o)
    for ln in edge:
        box.add(ln)
    for _, pads in ee if not box.ok else ():
        for o in pads:
            box.add(o)
    if not box.ok:
        o = _v(b.origin, mirror)
        box.add([(o[0] - 20.0, o[1] - 10.0), (o[0] + 20.0, o[1] + 10.0)])
    view = box.grown(0.03 * box.size + 1.0)
    if view.size < OVERLAY_MIN_VIEW:            # a tiny board: room round it for the circles and their labels
        cx, cy, h = (view.x0 + view.x1) / 2, (view.y0 + view.y1) / 2, OVERLAY_MIN_VIEW / 2
        view = _Box(min(view.x0, cx - h), min(view.y0, cy - h), max(view.x1, cx + h), max(view.y1, cy + h))
    reach = view.grown(0.25 * view.size)        # rows far off the board stretch the view only so much
    far = []                                    # rows whose EasyEDA pads all land beyond it: (pads, part) centres
    for res, pads in ee:
        pts = [q for o in pads for q in o]
        part = jlc_part(b, res.ref)
        if pts and part is not None and not any(reach.holds(q) for q in pts):
            far.append(((sum(x for x, _ in pts) / len(pts), sum(y for _, y in pts) / len(pts)),
                        _v((part.x, part.y), mirror)))
    for _, pads in ee:
        for o in pads:
            view.add([q for q in o if reach.holds(q)])
    aw = W - 2 * _M
    k = min(aw / (view.x1 - view.x0), 1.25 * W / (view.y1 - view.y0))
    bw, bh = k * (view.x1 - view.x0), k * (view.y1 - view.y0)

    mine = [r for r in run.results if _layer(run, r) == side]
    c = {v: sum(1 for r in mine if r.verdict == v) for v in (RED, YELLOW, GREEN, VERIFIED)}
    notes = [*notes]
    problems = [r for _, r in _sorted(run.results) if r.verdict in (RED, YELLOW)
                and (_layer(run, r) == side or r.row is not None and r.row.layer == side)]
    circle = len(problems) <= CIRCLES
    if not circle:
        notes.append(f"{len(problems)} red and yellow parts on this side: too many to circle"
                     + ("; their EasyEDA pads are outlined in their colour" if 2 * len(far) < len(problems) else ""))
    shift = None
    if far:                                     # where JLC puts them, which is what its preview would show
        shift = (sum(e[0] - a[0] for e, a in far) / len(far), sum(e[1] - a[1] for e, a in far) / len(far))
        notes.append(f"the EasyEDA pads of {plural(len(far), 'row')} land {math.hypot(*shift):.1f} mm from their "
                     "parts, off this view: the arrow points to them")
    go = _v(gerber_origin, mirror) if gerber_origin is not None else None
    if go is not None and not view.holds(go):
        notes.append(f"the Gerbers' (0, 0) is at board ({gerber_origin[0]:g}, {gerber_origin[1]:g}), off this view")
    out = ['<g class="header">',
           _ov_text(_M, 34, f"{_name(b.path) or 'board'}: {side}, " + (
               "seen from below as JLC's preview shows it (x mirrored)" if mirror else "seen from above"),
               20, _OV["fg"], weight=700),
           _ov_text(_M, 58, f"CPL {_name(run.cpl_path) or 'the rows jlcplace would write'}: "
                            f"{plural(len(mine), 'result')} on this side, {c[RED]} red, {c[YELLOW]} yellow, "
                            f"{c[GREEN]} green, {c[VERIFIED]} verified; origin {_origin(run)}, bottom reading "
                            f"{run.reading}; {TOOL}", 14, _OV["muted"])]
    y = 58
    for n in notes:
        for ln in _wrap(n, 14, aw):
            y += 20
            out.append(_ov_text(_M, y, ln, 14, _OV[YELLOW]))
    out.append("</g>")
    top, left = y + 16, _M + (aw - bw) / 2

    def P(q):
        return left + k * (q[0] - view.x0), top + k * (q[1] - view.y0)

    out.append(f'<clipPath id="area"><rect x="{_f(left)}" y="{_f(top)}" width="{_f(bw)}" height="{_f(bh)}"/>'
               f'</clipPath><g clip-path="url(#area)">')
    rings = _rings(edge)
    if rings:
        out.append(f'<path class="mask" d="{_pxpath([[P(q) for q in r] for r in rings])}" fill="{_OV["mask"]}" '
                   f'fill-rule="evenodd"/>')
    if edge:
        out.append(f'<path class="edge" d="{_pxpath([[P(q) for q in ln] for ln in edge], False)}" fill="none" '
                   f'stroke="{_OV["edge"]}" stroke-width="1.5"/>')
    parts: dict[int, tuple] = {}
    for part, _, o in side_pads[side]:
        parts.setdefault(id(part), (part, []))[1].append([P(q) for q in o])
    for part, outlines in parts.values():
        out.append(f'<path class="kp" data-ref="{_esc(part.ref)}" d="{_pxpath(outlines)}" fill="{_OV["cu"]}"'
                   + ("" if assembled(part) else ' fill-opacity="0.4"') + f'><title>{_esc(part.ref)}</title></path>')
    width_of = {RED: 2.0, YELLOW: 1.6}
    for res, pads in sorted(ee, key=lambda e: -_ORDER.get(e[0].verdict, 0)):     # problems drawn last, on top
        v = res.verdict if res.verdict in _ORDER else RED
        d = _pxpath([[P(q) for q in o] for o in pads])
        out.append(f'<path class="ee v-{v}" data-ref="{_esc(res.ref)}" d="{d}" '
                   f'fill="none" stroke="{_OV[v]}" stroke-width="{width_of.get(v, 1.0)}" stroke-linejoin="round"'
                   + (' stroke-opacity="0.85"' if v not in width_of else "") + "/>")
    if empty:
        out.append(_ov_text(left + bw / 2, top + bh / 2, f"Nothing on the {side}.", 18, _OV["muted"], anchor="middle"))
    marks = [(_v(b.origin, mirror), "cpl-origin", _OV["origin"], "M{x0} {y}H{x1}M{x} {y0}V{y1}",
              f"CPL origin, board ({b.origin[0]:g}, {b.origin[1]:g})")]
    if go is not None:
        marks.append((go, "gerber-origin", _OV["gerber"], "M{x0} {y0}L{x1} {y1}M{x0} {y1}L{x1} {y0}",
                      f"Gerbers' (0, 0), board ({gerber_origin[0]:g}, {gerber_origin[1]:g})"))
    for q, cls, colour, shape, tip in marks:
        if view.holds(q):
            x, y0 = P(q)
            d = shape.format(x=_f(x), y=_f(y0), x0=_f(x - 9), x1=_f(x + 9), y0=_f(y0 - 9), y1=_f(y0 + 9))
            out.append(f'<path class="{cls}" d="{d}" stroke="{colour}" stroke-width="2.5" fill="none">'
                       f'<title>{_esc(tip)}</title></path>')
    if shift is not None and math.hypot(*shift) > 0:       # an arrow from the middle of the drawing to the far pads
        n = math.hypot(*shift)
        u, L = (shift[0] / n, shift[1] / n), 0.4 * min(bw, bh)
        x0, y0 = left + bw / 2, top + bh / 2
        x1, y1 = x0 + L * u[0], y0 + L * u[1]
        hx, hy = -u[1] * 9, u[0] * 9
        out.append(f'<path class="offview" d="M{_f(x0)} {_f(y0)}L{_f(x1)} {_f(y1)}M{_f(x1 - 16 * u[0] + hx)} '
                   f'{_f(y1 - 16 * u[1] + hy)}L{_f(x1)} {_f(y1)}L{_f(x1 - 16 * u[0] - hx)} {_f(y1 - 16 * u[1] - hy)}"'
                   f' stroke="{_OV[RED]}" stroke-width="3" fill="none"><title>{_esc(printable(notes[-1]))}</title>'
                   '</path>')
        text = f"EasyEDA pads of {plural(len(far), 'row')}: {n:.0f} mm this way"
        right = u[0] >= 0 and x1 + 8 + 0.6 * 15 * len(text) <= left + bw     # the label must stay in the drawing
        out.append(_ov_text(x1 + 8 if right else x1 - 8, y1 + (0 if right == (u[0] >= 0) else 24 if u[1] < 0 else -14),
                            text, 15, _OV[RED], weight=700, halo=True, anchor="start" if right else "end"))
    out.append("</g>")

    # problem parts: a circle round the part's copper and the pads JLC puts down for its row, two when they are apart
    taken: list[tuple[float, float, float, float]] = []
    area = (left, top, left + bw, top + bh)
    listed, rounds, labels = [], [], []
    for res in problems:
        part = jlc_part(b, res.ref)
        K = []
        if part is not None and part.layer == side:
            K = [P(_v(library_to_board(part, u), mirror)) for p in part.copper_pads() for u in geom.pad_outline(p, 4)]
            K = K or [P(_v((part.x, part.y), mirror))]
        E = []
        if res.row is not None and res.row.layer == side:
            E = [P(q) for r, pads in ee if r is res for o in pads for q in o if reach.holds(q)]
            q = _point(run, res.row, mirror)
            E = E or ([P(q)] if reach.holds(q) else [])
        if _layer(run, res) == side or K or E:
            listed.append(res)
        if not (circle and (K or E)):
            continue
        v, colour = res.verdict, _OV[res.verdict]
        tip = f"<title>{_esc(printable(f'{res.ref}: {headline(res)}'))}</title>"
        circles = [(*_enclose(K or E), "")]
        if K and E:
            ck, ce = _enclose(K), _enclose(E)
            if math.hypot(ck[0] - ce[0], ck[1] - ce[1]) > ck[2] + ce[2] + 6:
                circles = [(*ck, ""), (*ce, " jlc")]
                d = math.hypot(ck[0] - ce[0], ck[1] - ce[1])
                u = ((ce[0] - ck[0]) / d, (ce[1] - ck[1]) / d)
                rounds.append(f'<line class="link {v}" data-ref="{_esc(res.ref)}" x1="{_f(ck[0] + ck[2] * u[0])}" '
                              f'y1="{_f(ck[1] + ck[2] * u[1])}" x2="{_f(ce[0] - ce[2] * u[0])}" '
                              f'y2="{_f(ce[1] - ce[2] * u[1])}" stroke="{colour}" stroke-width="2" '
                              f'stroke-dasharray="6 4"/>')
            else:
                circles = [(*_enclose(K + E), "")]
        for cx, cy, r, extra in circles:
            dash = ' stroke-dasharray="9 6"' if v == YELLOW else ""
            rounds.append(f'<circle class="problem {v}{extra}" data-ref="{_esc(res.ref)}" cx="{_f(cx)}" '
                          f'cy="{_f(cy)}" r="{_f(r)}" fill="none" stroke="{colour}" '
                          f'stroke-width="{3 if not extra else 2}"{dash}>{tip}</circle>')
        cx, cy, r, _ = circles[0]
        label = f"{res.ref} {res.status if res.status != 'ok' else 'check'}"
        fs = 15.0
        w, h = 0.6 * fs * len(printable(label)), 1.25 * fs
        first = None
        for ang in (-45, -135, 45, 135, -90, 90, 0, 180):
            a = math.radians(ang)
            ax, ay = cx + (r + 3) * math.cos(a), cy + (r + 3) * math.sin(a)
            x0 = ax if math.cos(a) > 0.3 else ax - w if math.cos(a) < -0.3 else ax - w / 2
            y0 = ay - h if math.sin(a) < -0.3 else ay if math.sin(a) > 0.3 else ay - h / 2
            cand = (x0, y0, x0 + w, y0 + h)
            first = first or cand
            inside = area[0] <= cand[0] and cand[2] <= area[2] and area[1] <= cand[1] and cand[3] <= area[3]
            if inside and not any(cand[0] < t[2] and t[0] < cand[2] and cand[1] < t[3] and t[1] < cand[3]
                                  for t in taken):
                break
        else:
            cand = first
        taken.append(cand)
        labels.append(_ov_text(cand[0], cand[3] - 0.3 * fs, label, fs, colour, weight=700, cls=f"label {v}",
                               ref=res.ref, halo=True))
    # circles stay in the drawing (a part's circle on a small board would run over the header and the legend)
    out += ['<g class="circles" clip-path="url(#area)">', *rounds, "</g>", *labels]

    # under the drawing: the legend, the circled parts' reasons, and where the EasyEDA outlines come from
    y = top + bh + 30
    legend = [("cu", "KiCad copper pad"), (GREEN, "EasyEDA pads as JLC places them, green row"),
              (YELLOW, "yellow row"), (RED, "red row"), ("c-red", "red part, circled"),
              ("c-yellow", "yellow part, circled")]
    legend += [("edge", "board outline")] if edge else []
    legend += [("cpl-origin", "CPL origin")] + ([("gerber-origin", "Gerbers' (0, 0)")] if go is not None else [])
    out.append('<g class="legend">')
    x = _M
    for key, text in legend:
        tw = 26 + 0.56 * 13 * len(text) + 22
        if x + tw > W - _M:
            x, y = _M, y + 24
        sy = y - 5
        if key == "cu":
            out.append(f'<rect x="{_f(x)}" y="{_f(sy - 6)}" width="16" height="12" fill="{_OV["cu"]}"/>')
        elif key in (GREEN, YELLOW, RED):
            out.append(f'<rect x="{_f(x + 1)}" y="{_f(sy - 6)}" width="14" height="12" fill="none" '
                       f'stroke="{_OV[key]}" stroke-width="2"/>')
        elif key.startswith("c-"):
            v = key[2:]
            out.append(f'<circle cx="{_f(x + 8)}" cy="{_f(sy)}" r="8" fill="none" stroke="{_OV[v]}" stroke-width="2.5"'
                       + (' stroke-dasharray="5 3"' if v == YELLOW else "") + "/>")
        elif key == "edge":
            out.append(f'<path d="M{_f(x)} {_f(sy)}H{_f(x + 16)}" stroke="{_OV["edge"]}" stroke-width="2"/>')
        else:
            colour = _OV["origin" if key == "cpl-origin" else "gerber"]
            d = (f"M{_f(x + 2)} {_f(sy)}H{_f(x + 14)}M{_f(x + 8)} {_f(sy - 6)}V{_f(sy + 6)}" if key == "cpl-origin"
                 else f"M{_f(x + 3)} {_f(sy - 5)}L{_f(x + 13)} {_f(sy + 5)}M{_f(x + 3)} {_f(sy + 5)}"
                      f"L{_f(x + 13)} {_f(sy - 5)}")
            out.append(f'<path d="{d}" stroke="{colour}" stroke-width="2.5" fill="none"/>')
        out.append(_ov_text(x + 24, y, text, 13, _OV["muted"]))
        x += tw
    out.append("</g>")

    out.append('<g class="problems">')
    if listed:
        y += 30
        out.append(_ov_text(_M, y, f"{'Circled' if circle else 'Red and yellow'} on the {side} ({len(listed)}): look "
                                   f"at them before ordering", 14, _OV["fg"], weight=700))
        room = int((aw - 20) / (0.56 * 13))
        for res in listed[:_LIST]:
            y += 20
            why = printable(headline(res))
            ref, v = printable(res.ref), res.verdict
            if len(ref) + len(v) + len(why) + 2 > room:
                why = why[:max(10, room - len(ref) - len(v) - 3)] + "…"
            out.append(f'<text x="{_M + 10}" y="{_f(y)}" font-size="13"><tspan font-weight="700" fill="{_OV[v]}">'
                       f'{_esc(ref)}</tspan><tspan fill="{_OV[v]}"> {v}</tspan><tspan fill="{_OV["fg"]}"> '
                       f'{_esc(why)}</tspan></text>')
        if len(listed) > _LIST:
            y += 20
            out.append(_ov_text(_M + 10, y, f"and {len(listed) - _LIST} more: see the summary or the checklist", 13,
                                _OV["muted"]))
    out.append("</g>")

    notice = _wrap(f"EasyEDA pad outlines: derived from footprints of the {SOURCE_LIBRARY} ({', '.join(SOURCE_LINKS)}"
                   f"), whose copyright and related rights belong to JLCEDA/EasyEDA. This image is for your own "
                   f"review: please do not publish it (for example as a CI artifact of a public repository).", 13,
                   aw - 24)
    y += 22
    out.append(f'<g class="source"><rect x="{_M}" y="{_f(y)}" width="{aw}" height="{12 + 19 * len(notice)}" '
               f'fill="{_OV["panel"]}" rx="6"/><rect x="{_M}" y="{_f(y)}" width="5" height="{12 + 19 * len(notice)}" '
               f'fill="{_OV[GREEN]}"/>')
    for i, ln in enumerate(notice):
        out.append(_ov_text(_M + 16, y + 21 + 19 * i, ln, 13, _OV["fg"]))
    out.append("</g>")
    H = math.ceil(y + 12 + 19 * len(notice) + _M)
    head = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
            f'data-side="{side}" font-family="{_FONT}"><title>{_esc(printable(f"jlcplace: {_name(b.path)}, {side}"))}'
            f'</title><desc>{_esc(SOURCE_NOTICE)} This image is for your own review: please do not publish it.</desc>'
            f'<rect width="{W}" height="{H}" fill="{_OV["page"]}"/>')
    return head + "".join(out) + "</svg>\n"


def write_overlays(run: Run, directory, *, outline=None, gerber_origin=None, notes=(), png: bool = False,
                   width: int = OVERLAY_WIDTH) -> dict[str, Path]:
    """top.svg and bottom.svg (overlay_svg) in directory, made if missing; with png, top.png and bottom.png too when
    a renderer is at hand (svg_to_png). Returns {file name: path} of the files written."""
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    out: dict[str, Path] = {}
    for side in (TOP, BOTTOM):
        svg = d / f"{side}.svg"
        atomic_write(svg, overlay_svg(run, side, outline=outline, gerber_origin=gerber_origin, notes=notes,
                                      width=width))
        out[svg.name] = svg
    for side in (TOP, BOTTOM) if png else ():
        if svg_to_png(d / f"{side}.svg", d / f"{side}.png"):
            out[f"{side}.png"] = d / f"{side}.png"
    return out


_SHELLS = tuple(f"chromium_headless_shell-*/chrome-*/{name}{exe}"
                for name in ("chrome-headless-shell", "headless_shell") for exe in ("", ".exe"))
_CHROMIUMS = ("chromium-*/chrome-*/chrome", "chromium-*/chrome-*/chrome.exe",
              "chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium")
_ON_PATH = ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "chrome", "chrome-headless-shell")
_MAC_APPS = ("Google Chrome.app/Contents/MacOS/Google Chrome", "Chromium.app/Contents/MacOS/Chromium",
             "Microsoft Edge.app/Contents/MacOS/Microsoft Edge")
_WIN_APPS = ("Google/Chrome/Application/chrome.exe", "Chromium/Application/chrome.exe",
             "Microsoft/Edge/Application/msedge.exe")


def _playwright(patterns) -> str:
    """The newest browser Playwright installed that matches: under $PLAYWRIGHT_BROWSERS_PATH, else its default
    caches (Linux, macOS, Windows)."""
    roots = [Path(p) for p in [os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")] if p and p != "0"]
    try:
        home = Path.home()
        roots += [home / ".cache/ms-playwright", home / "Library/Caches/ms-playwright"]
    except RuntimeError:                          # no home directory to be found
        pass
    roots += [Path(p) / "ms-playwright" for p in [os.environ.get("LOCALAPPDATA", "")] if p]
    hits = [q for r in roots if r.is_dir() for pat in patterns for q in r.glob(pat) if q.is_file()]

    def version(q: Path) -> int:
        m = next((m for m in (re.fullmatch(r"chromium[a-z_]*-(\d+)", s) for s in q.parts) if m), None)
        return int(m.group(1)) if m else -1
    return str(max(hits, key=version)) if hits else ""


def _installed(mac_roots=None) -> str:
    """A Chrome, Chromium or Edge installed where macOS and Windows put them, which is not on PATH there."""
    if mac_roots is None:
        mac_roots = [Path("/Applications")]
        try:
            mac_roots.append(Path.home() / "Applications")
        except RuntimeError:                      # no home directory to be found
            pass
    hits = [Path(r) / app for r in mac_roots for app in _MAC_APPS]
    hits += [Path(base) / app for base in (os.environ.get(v, "") for v in ("ProgramFiles", "ProgramFiles(x86)",
                                                                           "LOCALAPPDATA")) if base
             for app in _WIN_APPS]
    return next((str(q) for q in hits if q.is_file()), "")


def png_renderer() -> str:
    """What svg_to_png renders with: "cairosvg" when it imports, else a headless Chromium: $JLCPLACE_CHROMIUM, a
    headless shell Playwright installed, a Chrome or Chromium on PATH, one installed where macOS and Windows put it
    (Edge too), Playwright's full Chromium; "" for none."""
    try:
        import cairosvg  # noqa: F401
        return "cairosvg"
    except (ImportError, OSError):                # OSError: cairosvg without its cairo library
        pass
    if os.environ.get("JLCPLACE_CHROMIUM"):
        return os.environ["JLCPLACE_CHROMIUM"]
    return (_playwright(_SHELLS) or next((p for p in map(shutil.which, _ON_PATH) if p), "") or _installed()
            or _playwright(_CHROMIUMS))


def svg_to_png(svg, png, *, timeout: float = 120.0) -> str:
    """Render an overlay SVG to PNG at its own size (its width and height, px) with png_renderer(). Returns the
    renderer used, "" when there is none (nothing is written); RuntimeError when it fails."""
    how = png_renderer()
    if not how:
        return ""
    svg, png = Path(svg), Path(png)
    if png.exists():
        png.unlink()                              # a failed render must not leave an older image looking new
    if how == "cairosvg":
        import cairosvg
        cairosvg.svg2png(url=str(svg), write_to=str(png))
        return how
    m = re.search(r'<svg\b[^>]*?\swidth="(\d+)"[^>]*?\sheight="(\d+)"', svg.read_text(encoding="utf-8")[:4096])
    w, h = (int(m.group(1)), int(m.group(2))) if m else (OVERLAY_WIDTH, OVERLAY_WIDTH)
    with tempfile.TemporaryDirectory() as profile:
        # --no-sandbox, as Playwright launches Chromium: the page is this file (no script, no network), and CI
        # containers run as root or without user namespaces, where the sandbox cannot start
        # --use-mock-keychain: a Chrome on macOS must not ask for the login keychain (other systems ignore it)
        cmd = [how, "--headless", "--no-sandbox", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
               "--use-mock-keychain", "--force-device-scale-factor=1", f"--window-size={w},{h}",
               f"--user-data-dir={profile}", f"--screenshot={png.resolve()}", svg.resolve().as_uri()]
        try:
            p = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace", timeout=timeout,
                               check=False)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise RuntimeError(f"{how}: {e}") from None
    if p.returncode != 0 or not png.is_file():
        raise RuntimeError(f"{how} exited with {p.returncode}: {(p.stderr or '').strip()[-400:]}")
    return how
