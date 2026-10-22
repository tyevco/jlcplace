"""The modules put together for one board: its EasyEDA footprints, a correction per part (from the database, else
fitted), the rows jlcplace writes, and the check of any CPL against the board. cli.py and report.py work on this.

Where a part's correction comes from (`Resolved.source`):
  db        the correction database has an entry for the code on this KiCad footprint and pin functions
            (parts.kicad_hash) and EasyEDA footprint (key, or an entry that matches any key);
  fit       fitted now (fit.fit_part); a database entry for an older EasyEDA footprint ("stale") is compared with
            the refit and the result recorded in the reasons;
  fallback  `jlcplace cpl` only: no EasyEDA footprint and no entry, so the row is KiCad's own position and angle
            (rot 0, no offset); the check calls it not-simulated (yellow for a non-polar two-pad part, else red).

Without an EasyEDA record (offline and not cached, or not found) a part with a database correction is still checked:
its KiCad copper pads are taken through the inverse correction, e = M . R(-rot) . (l - d), and stand in for the
EasyEDA pads. That simulation is exactly as good as the stored correction, so the row's verdict is capped by the
correction's: CI can check CPLs offline from a committed database without any EasyEDA data.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace

from . import geom
from .check import check_cpl, check_row, nonpolar
from .convention import DEFAULT, rot
from .cpl import assembled, natural_key, read_text
from .easyeda import EEError
from .fit import fit_part
from .model import GREEN, RED, VERDICT_ORDER, VERIFIED, YELLOW, Board, Correction, CplRow, EEFootprint, \
    EEPad, NotFound, Part, RowResult
from .parts import ee_key, kicad_hash

_CODE = re.compile(r"C[1-9][0-9]*", re.I)


@dataclass
class Resolved:
    """What jlcplace knows about one part JLC assembles."""
    part: Part
    ee: EEFootprint | NotFound | None = None
    corr: Correction | None = None
    source: str = ""               # db | fit | fallback | "" (no correction)
    proxy: bool = False            # the check used the KiCad pads through the stored correction (no EasyEDA record)


@dataclass
class CheckItem:
    """One thing to look at in JLC's placement preview."""
    category: str                  # bottom reading | not simulated | odd angle | uncertain correction
    ref: str
    lcsc: str
    layer: str
    rotation: float | None
    what: str
    verdict: str = GREEN           # the row's verdict


@dataclass
class Run:
    """A CPL checked against a board."""
    board: Board
    rows: list[CplRow]
    results: list[RowResult]
    resolved: dict[str, Resolved]
    reading: str = DEFAULT
    cpl_path: str = ""
    bom_messages: list[str] = field(default_factory=list)
    row_lines: list[int | None] = field(default_factory=list)     # CPL file line of each result (None: no row)

    def counts(self) -> dict[str, int]:
        c = {RED: 0, YELLOW: 0, GREEN: 0, VERIFIED: 0}
        for r in self.results:
            c[r.verdict if r.verdict in c else RED] += 1
        return c

    def worst(self) -> str:
        v = min((r.verdict for r in self.results), key=lambda x: VERDICT_ORDER.get(x, 0), default=VERIFIED)
        if self.bom_messages and VERDICT_ORDER.get(v, 0) > VERDICT_ORDER[YELLOW]:
            v = YELLOW
        return v

    def exit_code(self, fail_on: str = RED) -> int:
        """2 if any row is red, 1 if something is yellow (rows or BOM) and fail_on is yellow, else 0."""
        w = self.worst()
        if w == RED:
            return 2
        return 1 if fail_on == YELLOW and w == YELLOW else 0

    def checklist(self) -> list[CheckItem]:
        return checklist(self)


# ------------------------------------------------------------------------------------------------------- inputs

def is_code(text: str) -> bool:
    return bool(_CODE.fullmatch(str(text).strip()))


def apply_bom_codes(board: Board, bom_rows: list[dict]) -> list[tuple[str, str, str]]:
    """Give every part its BOM line's code: JLC assembles from the BOM's LCSC column and never sees the board, so
    the BOM's code is the part JLC places, and the one to simulate, even where the board names another. Returns
    (ref, board code or "", BOM code) for every part changed."""
    changed = []
    for b in bom_rows:
        code = (b.get("lcsc") or "").upper()
        if not is_code(code):
            continue
        for ref in b.get("designators") or []:
            part = board.part(ref)
            if part is not None and part.lcsc != code:
                changed.append((ref, part.lcsc, code))
                part.lcsc, part.lcsc_field = code, "BOM"
    return changed


def bom_overrides(changed: list[tuple[str, str, str]]) -> list[str]:
    """BOM messages for the parts whose board code the BOM's replaced (apply_bom_codes)."""
    return [f"{ref}: JLC places {new} from the BOM; the board says {old} (checked with {new})"
            for ref, old, new in changed if old]


def lookup_ee(client, code: str) -> EEFootprint | NotFound:
    """The client's footprint for a code; an unavailable record (offline cache miss, network failure, unreadable
    response) is a NotFound whose message says why."""
    try:
        return client.get(code)
    except EEError as e:
        msg = str(e)
        if "offline" in msg and "not in the cache" in msg:
            msg = "no EasyEDA record in the cache (`jlcplace fetch` or --online downloads it)"
        return NotFound(code, msg)
    except OSError as e:
        return NotFound(code, f"lookup failed ({type(e).__name__}: {e})")


# ------------------------------------------------------------------------------------------------------- corrections

def _same(a: Correction, b: Correction) -> bool:
    da = abs((a.rot - b.rot + 180.0) % 360.0 - 180.0)
    return da < 0.01 and abs(a.dx - b.dx) < 0.01 and abs(a.dy - b.dy) < 0.01 and a.mirror == b.mirror


def describe(c: Correction) -> str:
    return f"rot {c.rot:g}, dx {c.dx:.4f}, dy {c.dy:.4f}" + (", mirrored" if c.mirror else "")


_LATER = ("accepted manually", "verification failed", "derived from presses", "typed in by", "taken from the row",
          "the EasyEDA footprint changed")


def key_reasons(c: Correction, n: int = 2) -> list[str]:
    """The reasons that say most about a correction: what a person or a later refit added (latest first), then the
    fit's own."""
    later = [r for r in reversed(c.reasons) if r.startswith(_LATER)]
    return (later + [r for r in c.reasons if r not in later])[:n]


def correction(part: Part, ee, db=None, *, snap: float = 0.5) -> tuple[Correction | None, str]:
    """(correction, source) for a part: the database entry for its code, KiCad key and EasyEDA key; else a fit
    (a stale entry is compared with the refit: a refit that differs is at best yellow); else (None, "")."""
    key = ee_key(ee) if isinstance(ee, EEFootprint) else ""
    stored = None
    if db is not None:
        stored = db.lookup(part.lcsc, ee_key=key, kicad_hash=kicad_hash(part))
        if stored is not None and stored.status != "stale":
            return stored, "db"
    if not isinstance(ee, EEFootprint):
        return None, ""
    fitted = fit_part(part, ee, snap=snap)
    if stored is not None:
        if _same(stored, fitted):
            fitted.reasons.append(f"the EasyEDA footprint changed since the stored correction ({describe(stored)}); "
                                  "the refit gives the same placement")
        else:
            fitted.reasons.append(f"the EasyEDA footprint changed since the stored correction ({describe(stored)}); "
                                  "the refit differs: check it in JLC's preview, then confirm it again")
            if fitted.verdict == GREEN:
                fitted.verdict = YELLOW
    return fitted, "fit"


def fallback(part: Part, ee) -> Correction:
    """No EasyEDA footprint and no stored correction: KiCad's own position and angle (rot 0, no offset). Right for
    the usual centred two-pad passive; the check says not-simulated (red unless non-polar with two pads)."""
    why = ee.message if isinstance(ee, NotFound) else "no EasyEDA footprint"
    ok = nonpolar(part) and len(part.copper_pads()) == 2
    return Correction(rot=0.0, method="none", verdict=YELLOW if ok else RED, kicad_hash=kicad_hash(part),
                      reasons=[f"no EasyEDA footprint ({why}): the row is KiCad's own position and angle"])


def bom_listed(part: Part, bom_rows) -> bool:
    """JLC places the part although the board excludes it from its BOM: the JLC BOM lists it (and nothing else keeps
    it off the board: not DNP, not excluded from the position files, not board-only)."""
    return (bom_rows is not None and part.placed and part.exclude_bom
            and any(part.ref in (b.get("designators") or []) for b in bom_rows))


def resolve(board: Board, client, db=None, *, refs=None, snap: float = 0.5, use_fallback: bool = False,
            bom_rows=None) -> dict[str, Resolved]:
    """A Resolved for every part JLC assembles that has an LCSC code (or for `refs`, any part with a code), each
    EasyEDA record looked up once per code. With `bom_rows`, also the parts the board only excludes from its BOM but
    the JLC BOM lists (JLC places what its BOM lists)."""
    want = set(refs) if refs else None
    ees: dict[str, EEFootprint | NotFound] = {}
    out: dict[str, Resolved] = {}
    for part in sorted(board.parts, key=lambda p: natural_key(p.ref)):
        if not part.lcsc or (want is not None and part.ref not in want) or (
                want is None and not assembled(part) and not bom_listed(part, bom_rows)):
            continue
        if part.ref in out:
            # a second part with this reference (check reports it, "duplicate-ref"): it must not replace the first
            # one's footprint and correction
            continue
        if part.lcsc not in ees:
            ees[part.lcsc] = lookup_ee(client, part.lcsc)
        ee = ees[part.lcsc]
        corr, source = correction(part, ee, db, snap=snap)
        if corr is None and use_fallback:
            corr, source = fallback(part, ee), "fallback"
        out[part.ref] = Resolved(part, ee, corr, source)
    return out


# ------------------------------------------------------------------------------------------------------- checking

def proxy_footprint(part: Part, corr: Correction) -> EEFootprint:
    """The part's own copper pads in the ee frame through the inverse correction, e = M . R(-rot) . (l - d), numbered
    as in KiCad: what the EasyEDA pads are, as far as the correction is right."""
    pads = []
    for p in part.copper_pads():
        def to_ee(q):
            v = rot((q[0] - corr.dx, q[1] - corr.dy), -corr.rot)
            return (-v[0], v[1]) if corr.mirror else v
        x, y = to_ee((p.x, p.y))
        a = (p.angle or 0.0) - corr.rot          # library angle = rot + ee angle (rot - ee angle when mirrored)
        simple = not p.poly and p.shape in ("rect", "oval", "circle")
        poly = None if simple else [to_ee(q) for q in geom.pad_outline(p)]
        pads.append(EEPad(p.number, x, y, p.w, p.h, -a if corr.mirror else a, p.shape if simple else "polygon",
                          "multi" if p.kind == "tht" else "top", poly=poly))
    return EEFootprint(part.lcsc, "KiCad pads through the stored correction", pads=pads, source="kicad-proxy")


def _worst(a: str, b: str) -> str:
    return min(a, b, key=lambda v: VERDICT_ORDER.get(v, 0))


def check(board: Board, rows: list[CplRow], resolved: dict[str, Resolved], *, reading: str = DEFAULT,
          bom_rows=None, cpl_path: str = "") -> Run:
    """check.check_cpl with the resolved footprints and corrections, then: rows of parts with no EasyEDA record but a
    stored correction are simulated with proxy_footprint (verdict capped by the correction's); with a BOM, a row for
    a part without a code that the BOM does not list is yellow (JLC does not place it)."""
    ees = {r.part.lcsc: r.ee for r in resolved.values() if r.part.lcsc}
    corrections = {ref: r.corr for ref, r in resolved.items() if r.corr is not None}
    bom_refs = {d for b in bom_rows or [] for d in b.get("designators") or []}
    results = check_cpl(board, rows, lambda code: ees.get(code), corrections=corrections, reading=reading,
                        bom_refs=bom_refs if bom_rows is not None else None)
    for i, res in enumerate(results):
        info = resolved.get(res.ref)
        if res.status == "not-simulated" and info is not None and info.source == "db" and res.row is not None:
            why = info.ee.message if isinstance(info.ee, NotFound) else "no EasyEDA record"
            new = check_row(info.part, res.row, proxy_footprint(info.part, info.corr), board.origin, corr=info.corr,
                            reading=reading)
            cap = info.corr.verdict if info.corr.verdict in VERDICT_ORDER else YELLOW
            if VERDICT_ORDER[cap] < VERDICT_ORDER.get(new.verdict, 0):
                # the simulation is only as good as the stored correction: say so where the verdict is read
                new.verdict = cap
                new.messages.insert(0, f"{cap} because the stored {info.corr.status} correction is {cap}"
                                       + (f": {key_reasons(info.corr, 1)[0]}" if info.corr.reasons else ""))
            new.messages.append(f"simulated with the KiCad pads through the stored {info.corr.status} correction: "
                                f"{why}")
            results[i] = res = new
            info.proxy = True
        part = board.part(res.ref)
        if (bom_rows is not None and part is not None and not part.lcsc and res.ref not in bom_refs
                and res.status == "not-simulated"):
            res.verdict = YELLOW
            res.messages.append("not in the BOM: JLC does not place it")
        if res.status == "no-row" and info is not None:
            if info.corr is not None and info.corr.mirror:
                res.messages.append("no row is possible: the EasyEDA footprint is a mirror image of the KiCad one")
            elif info.corr is None:
                why = info.ee.message if isinstance(info.ee, NotFound) else "no correction"
                res.messages.append(f"no correction to make a row from ({why})")
    run = Run(board, rows, results, resolved, reading, cpl_path)
    if cpl_path:
        run.row_lines = row_lines(cpl_path, results)
    return run


def row_lines(path, results: list[RowResult]) -> list[int | None]:
    """The 1-based line of each result's row in the CPL file (the n-th row of a ref on the n-th line starting with
    it), None for results without a row or when the file cannot be read."""
    try:
        lines = read_text(path).splitlines()      # as read_cpl decodes it (UTF-16, CR-only line ends)
    except OSError:
        return [None] * len(results)
    at: dict[str, list[int]] = {}
    for n, ln in enumerate(lines, 1):
        m = re.match(r'\s*"?([^",;\t\s]+)', ln)
        if m:
            at.setdefault(m.group(1), []).append(n)
    used: dict[str, int] = {}
    out = []
    for res in results:
        if res.row is None:
            out.append(None)
            continue
        k = used.get(res.ref, 0)
        cand = at.get(res.ref, [])
        out.append(cand[k] if k < len(cand) else None)
        used[res.ref] = k + 1
    return out


# ------------------------------------------------------------------------------------------------------- checklist

def checklist(run: Run) -> list[CheckItem]:
    """What to look at in JLC's placement preview: bottom parts whose pin -> net mapping depends on how JLC reads a
    bottom rotation, parts not simulated, rows at angles that are not multiples of 90, and parts whose correction is
    neither green nor verified. Sorted by category, then ref."""
    items: list[CheckItem] = []
    for res in run.results:
        row = res.row
        part = run.board.part(res.ref)
        layer = row.layer if row else (part.layer if part else "")
        a = row.rotation if row else None

        def add(cat, what):
            items.append(CheckItem(cat, res.ref, res.lcsc, layer, a, what, res.verdict))

        if res.convention_sensitive:
            m = next((m for m in res.messages if m.startswith("bottom reading matters")), "")
            add("bottom reading", "check pin 1 / polarity: read another way, the bottom rotation would put pads "
                                  "elsewhere (" + (m.split(": ", 1)[-1] if m else "pin -> net changes") + ")")
        if res.status == "not-simulated":
            add("not simulated", res.messages[0] if res.messages else "no EasyEDA footprint")
        if row is not None and part is not None and not math.isclose((a or 0.0) % 90.0, 0.0, abs_tol=1e-6) \
                and not math.isclose((a or 0.0) % 90.0, 90.0, abs_tol=1e-6):
            add("odd angle", f"rotation {a:g} is not a multiple of 90: check the part sits square on its pads")
        info = run.resolved.get(res.ref)
        if info is not None and info.corr is not None and info.corr.verdict in (YELLOW, RED) \
                and info.source != "fallback" and res.status not in ("not-simulated", "no-row", "duplicate-ref"):
            add("uncertain correction", f"{info.corr.verdict} {info.source} correction ({info.corr.status}): "
                                        + "; ".join(key_reasons(info.corr)))
    order = {"bottom reading": 0, "not simulated": 1, "odd angle": 2, "uncertain correction": 3}
    return sorted(items, key=lambda it: (order.get(it.category, 9), _rank_doubt(run, it), natural_key(it.ref)))


def _rank_doubt(run: Run, it: CheckItem) -> int:
    """Uncertain corrections whose choice of placement is in doubt (a polarity, names against numbers, an
    orientation the pads do not fix) first: they are the ones that can be the wrong way round."""
    info = run.resolved.get(it.ref)
    if it.category != "uncertain correction" or info is None or info.corr is None:
        return 0
    return {"polarity": 0, "names": 1, "orientation": 2}.get(info.corr.doubt, 3)


# ------------------------------------------------------------------------------------------------------- misc

def with_bom_codes(board: Board, bom_rows) -> Board:
    """A copy of the board whose parts carry their BOM codes (apply_bom_codes; the original is not changed)."""
    b = replace(board, parts=[replace(p) for p in board.parts])
    apply_bom_codes(b, bom_rows)
    return b
