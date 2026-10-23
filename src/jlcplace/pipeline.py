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

JLC lays the CPL over the Gerbers from the Gerbers' own origin. A CPL exported from another origin than the board's
CPL origin moves every part by one vector: common_offset finds it (and a Y left pointing down, and numbers in another
unit) with the origin the CPL was written from, and offset_hints says it. With the Gerbers (gerber.gerber_origin), the
rows are checked from the Gerbers' (0, 0), as JLC reads them, and a CPL written from elsewhere, or Gerbers that are
not the board's, are a red Finding for the whole run (origin_findings, gerber_findings).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace

import numpy as np

from . import geom
from .check import check_cpl, check_row, jlc_part, listed_note, nonpolar
from .convention import BOTTOM_READINGS, DEFAULT, board_to_cpl, cpl_row_for, rot
from .cpl import UNITS, assembled, jlc_places, natural_key, read_text
from .easyeda import EEError
from .fit import fit_part
from .gerber import ORIGIN_TOL, OriginCheck
from .model import GREEN, RED, VERDICT_ORDER, VERIFIED, YELLOW, Board, Correction, CplRow, EEFootprint, \
    EEPad, NotFound, Part, RowResult
from .parts import ee_key, kicad_hash, norm_number, trimmer

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
    """One thing to look at in JLC's placement preview: one part (its other categories folded in, `also`), or a
    group of alike parts (`refs`; `ref` is the one to look at)."""
    category: str                  # uncertain correction | not simulated | bottom reading | odd angle
    ref: str                       # the part to look at
    lcsc: str
    layer: str
    rotation: float | None         # its row's rotation (None: no row, or a group's rows differ)
    what: str
    verdict: str = GREEN           # the row's verdict (a group: the worst)
    refs: list[str] = field(default_factory=list)      # every part the item stands for, natural order
    also: list[str] = field(default_factory=list)      # the part's other categories, in `what` after "; also "
    first: bool = False            # bottom reading: the part that settles the reading for every other


@dataclass
class Finding:
    """Something wrong with the run as a whole rather than with one row (origin_findings, gerber_findings)."""
    status: str                    # origin (the CPL is not relative to the Gerbers' origin) | gerbers (not usable)
    verdict: str
    message: str                   # what JLC will do, by how much, and the fix


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
    findings: list[Finding] = field(default_factory=list)         # about the whole run: origin, gerbers
    gerbers: OriginCheck | None = None                             # the --gerbers set's origin check
    gerbers_path: str = ""
    offset: Offset | None = None                                   # common_offset of the rows, once computed

    def counts(self) -> dict[str, int]:
        c = {RED: 0, YELLOW: 0, GREEN: 0, VERIFIED: 0}
        for r in self.results:
            c[r.verdict if r.verdict in c else RED] += 1
        return c

    def worst(self) -> str:
        v = min([r.verdict for r in self.results] + [f.verdict for f in self.findings],
                key=lambda x: VERDICT_ORDER.get(x, 0), default=VERIFIED)
        if self.bom_messages and VERDICT_ORDER.get(v, 0) > VERDICT_ORDER[YELLOW]:
            v = YELLOW
        return v

    def exit_code(self, fail_on: str = RED) -> int:
        """2 if any row or finding is red, 1 if something is yellow (rows or BOM) and fail_on is yellow, else 0."""
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
    (ref, board code or "", BOM code) for every reference changed, the board code of the part JLC places. Every
    part with the reference takes the code: the first one can be a DNP alternate footprint of the placed one."""
    changed = []
    for b in bom_rows:
        code = (b.get("lcsc") or "").upper()
        if not is_code(code):
            continue
        for ref in b.get("designators") or []:
            main = jlc_part(board, ref)
            if main is not None and main.lcsc != code:
                changed.append((ref, main.lcsc, code))
            for part in board.parts:
                if part.ref == ref and part.lcsc != code:
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
#: reasons that only inform (what the fit ruled out, what agrees): they never make a correction yellow
_INFO = re.compile(r"fits by pad numbers|\d+ pin names agree|rot [^;]* also [^;]*; ruled out by"
                   r"|the EasyEDA footprint changed .*the refit gives the same placement")
#: yellow reasons JLC's preview cannot settle: a land-pattern residual with every pad landing (CONTRACT "Open"), and a
#: non-90 angle (the checklist's odd-angle item has it)
_QUIET = re.compile(r"rms [\d.]+ mm: the land patterns differ|angle \S+ deg is not a multiple of 90")


def key_reasons(c: Correction, n: int = 2) -> list[str]:
    """The reasons that say most about a correction: what a person or a later refit added (latest first), then the
    fit's own, those that make it yellow or red before those that only inform ("rot 0 also lands every pad, on other
    nets; ruled out by pad numbers")."""
    later = [r for r in reversed(c.reasons) if r.startswith(_LATER)]
    rest = [r for r in c.reasons if r not in later]
    return (later + [r for r in rest if not _INFO.match(r)] + [r for r in rest if _INFO.match(r)])[:n]


def needs_preview(c: Correction) -> bool:
    """Whether JLC's preview can settle something about a correction that is not green: it is red, its choice of
    placement is in doubt, or a reason other than a land-pattern residual or a non-90 angle makes it yellow."""
    return c.verdict == RED or bool(c.doubt) or any(not _INFO.match(r) and not _QUIET.match(r) for r in c.reasons)


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
    the usual centred two-pad passive; the check says not-simulated (red unless non-polar with two pads, or a two-pad
    trimmer)."""
    why = ee.message if isinstance(ee, NotFound) else "no EasyEDA footprint"
    ok = (nonpolar(part) or trimmer(part)) and len(part.copper_pads()) == 2
    return Correction(rot=0.0, method="none", verdict=YELLOW if ok else RED, kicad_hash=kicad_hash(part),
                      reasons=[f"no EasyEDA footprint ({why}): the row is KiCad's own position and angle"])


def bom_refs(bom_rows) -> set[str] | None:
    """The designators a JLC BOM lists (None: no BOM)."""
    return None if bom_rows is None else {d for b in bom_rows for d in b.get("designators") or []}


def bom_listed(part: Part, bom_rows) -> bool:
    """JLC places the part although the board excludes it from its position files or its BOM: the JLC BOM lists it,
    and it is neither DNP nor board-only (cpl.jlc_places: JLC never sees KiCad's attributes)."""
    return bom_rows is not None and not assembled(part) and jlc_places(part, bom_refs(bom_rows))


def resolve(board: Board, client, db=None, *, refs=None, snap: float = 0.5, use_fallback: bool = False,
            bom_rows=None, cpl_refs=None) -> dict[str, Resolved]:
    """A Resolved for every part JLC places that has an LCSC code (or for `refs`, any part with a code), each EasyEDA
    record looked up once per code: the parts JLC assembles, and the ones the board excludes from its position files
    or its BOM that the JLC BOM (`bom_rows`) lists or, with no BOM, the CPL's rows (`cpl_refs`) list where only the
    position files exclude them (cpl.jlc_places: JLC places what its BOM and CPL list)."""
    want = set(refs) if refs else None
    listed = bom_refs(bom_rows)
    ees: dict[str, EEFootprint | NotFound] = {}
    out: dict[str, Resolved] = {}
    for part in sorted(board.parts, key=lambda p: natural_key(p.ref)):
        if not part.lcsc or (want is not None and part.ref not in want) or (
                want is None and not jlc_places(part, listed, cpl_refs)):
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
    listed = bom_refs(bom_rows) or set()
    results = check_cpl(board, rows, lambda code: ees.get(code), corrections=corrections, reading=reading,
                        bom_refs=bom_refs(bom_rows))
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
            if not assembled(info.part):       # JLC places it all the same: its BOM or CPL lists it (check_cpl)
                listed_note(new, info.part, bom_refs(bom_rows))
            results[i] = res = new
            info.proxy = True
        part = jlc_part(board, res.ref)
        if (bom_rows is not None and part is not None and not part.lcsc and res.ref not in listed
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

CATEGORIES = ("pinout", "uncertain correction", "not simulated", "bottom reading", "odd angle")    # checklist order
_VRANK = {RED: 0, YELLOW: 1, GREEN: 2, VERIFIED: 3}


#: a row message that asks for the LCSC part's pinout to be checked (check._pinout), and a fit reason about pin names
_PINOUT = re.compile(r"check the LCSC part's pinout against the symbol")
_PIN_REASON = re.compile(r"pinout differs|\d+ pin name\S* differ")


def _off90(a) -> float:
    """How far an angle is from the nearest multiple of 90 (0 to 45)."""
    r = (a or 0.0) % 90.0
    return min(r, 90.0 - r)


def _pin_words(moved: list[tuple[str, str | None]]) -> str:
    """What one reading does to a part, in pins: 'pins 1 and 2 would swap', 'pin 1 would land off copper (14 pins
    move)', 'pin 2 would sit on pad 34 (60 pins move)'."""
    if len(moved) == 2 and moved[0][1] is not None and moved[1][1] is not None \
            and norm_number(moved[0][1]) == norm_number(moved[1][0]) \
            and norm_number(moved[1][1]) == norm_number(moved[0][0]):
        return f"pins {moved[0][0]} and {moved[1][0]} would swap"
    e, k = next(((e, k) for e, k in moved if norm_number(e) == "1"), moved[0])
    where = f"sit on pad {k}" if k is not None else "land off copper"
    return f"pin {e or '?'} would {where}" + (f" ({len(moved)} pins move)" if len(moved) > 1 else "")


def moves_text(res: RowResult) -> str:
    """'as ccw-from-top or ccw-from-top+180, pin 1 would land off copper; as jlc-2022+180, pins 1 and 2 would swap'
    (the readings with the same effect said once)."""
    said: dict[str, list[str]] = {}
    for name in BOTTOM_READINGS:
        if res.reading_moves.get(name):
            said.setdefault(_pin_words(res.reading_moves[name]), []).append(name)
    if not said:                                   # a result made without the moves: the check's own message
        m = next((m for m in res.messages if m.startswith("bottom reading matters")), "")
        return m.split(": ", 1)[-1] if m else "pins would move"
    return "; ".join(f"as {' or '.join(names)}, {words}" for words, names in said.items())


def _found(run: Run, res: RowResult) -> list[tuple[str, str, str]]:
    """(category, what as an item, what as a note on another item) for every reason to look at a result's part."""
    row = res.row
    part = jlc_part(run.board, res.ref)
    a = row.rotation if row else None
    out = []
    info = run.resolved.get(res.ref)
    c = info.corr if info is not None else None
    pins = [m for m in res.messages if _PINOUT.search(m) and "accepted by the" not in m]
    if res.status == "pinout" or pins:
        # the datasheet settles it, not the preview: JLC's preview shows where pads land, not what the pins are
        m = res.messages[0] if res.status == "pinout" else pins[0]
        out.append(("pinout", f"{m} (in its datasheet: JLC's preview does not show pin functions)",
                    "the pinout differs from the symbol"))
    if c is not None and c.verdict in (YELLOW, RED) and info.source != "fallback" \
            and res.status not in ("not-simulated", "no-row", "duplicate-ref") \
            and (res.verdict not in (GREEN, VERIFIED) or needs_preview(c)):
        # a green row with only a land-pattern residual against it: nothing the preview could settle; the reasons
        # the preview can settle first, and only those when there are any (with a pinout item, only those other than
        # its pin names)
        why = key_reasons(c, len(c.reasons))
        if out:
            why = [r for r in why if not _PIN_REASON.match(r) and not _QUIET.match(r) and not _INFO.match(r)]
        why = [r for r in why if not _QUIET.match(r) and not _INFO.match(r)][:2] or why[:2]
        if why:
            what = f"{c.verdict} {info.source} correction ({c.status}): " + "; ".join(why)
            out.append(("uncertain correction", what, what))
    if res.status == "not-simulated" and res.lcsc:
        # a part without a code is no item: JLC places nothing it has no code for, so its preview never shows it
        m = res.messages[0] if res.messages else "no EasyEDA footprint"
        out.append(("not simulated", m, m))
    if res.convention_sensitive:
        moves = moves_text(res)
        out.append(("bottom reading", f"if JLC read the bottom rotation another way: {moves}",
                    f"the bottom reading matters ({moves})"))
    if row is not None and part is not None and _off90(a) > 1e-6:
        out.append(("odd angle", f"rotation {a:g} is not a multiple of 90: check the part sits square on its pads",
                    f"rotation {a:g} is not a multiple of 90"))
    return sorted(out, key=lambda f: CATEGORIES.index(f[0]))


def _merge(group: list[CheckItem]) -> CheckItem:
    """One item for alike parts: one code on one side at odd angles (named: the one furthest from a multiple of 90),
    or one code whose rows move alike under the other bottom readings, or that share a doubtful correction."""
    it = group[0]
    if len(group) == 1:
        return it
    refs = sorted((r for g in group for r in g.refs), key=natural_key)
    rots = {g.rotation for g in group}
    if it.category == "odd angle":
        look = max(sorted(group, key=lambda g: natural_key(g.ref)), key=lambda g: _off90(g.rotation))
        lo, hi = min(g.rotation for g in group), max(g.rotation for g in group)
        what = (f"{len(refs)} parts of one code at {lo:g} to {hi:g} degrees, none a multiple of 90: check that one, "
                f"{look.ref} at {look.rotation:g}, sits square on its pads; the others are the same part on the "
                f"same side")
        return replace(it, ref=look.ref, rotation=None, what=what, refs=refs)
    what = it.what + (f" ({len(refs)} parts of one code at one rotation: they move alike)"
                      if it.category == "bottom reading" else f" ({len(refs)} parts share this correction)")
    return replace(it, ref=refs[0], rotation=it.rotation if len(rots) == 1 else None, what=what, refs=refs)


def checklist(run: Run) -> list[CheckItem]:
    """What to look at in JLC's placement preview, one item per part (its other reasons folded into it) or per group
    of alike parts, in CATEGORIES order:
      pinout                the LCSC part's pin names differ from the symbol's (red: a permutation no placement
                            fixes): its datasheet settles it, the preview cannot;
      uncertain correction  a correction that is not green and that the preview can settle: red, a choice of
                            placement in doubt (polarity, then names, then orientation first), or a reason other
                            than a land-pattern residual or a non-90 angle (on a row that is not green, any);
      not simulated         no EasyEDA footprint (a part with a code: JLC places no other);
      bottom reading        bottom parts whose pins move under another reading of the bottom rotation, said in pins;
                            first a green part that moves under every other reading: it settles the reading for all;
      odd angle             rows at angles that are not multiples of 90, one item per code and side.
    Within a category, red rows (fix the row first) before yellow before green."""
    items: list[CheckItem] = []
    by_ref: dict[str, RowResult] = {}
    for res in run.results:
        found = _found(run, res)
        if not found:
            continue
        part = jlc_part(run.board, res.ref)
        layer = res.row.layer if res.row else (part.layer if part else "")
        cat, what, _ = found[0]
        notes = [n for _, _, n in found[1:]]
        items.append(CheckItem(cat, res.ref, res.lcsc, layer, res.row.rotation if res.row else None,
                               what + ("; also " + "; ".join(notes) if notes else ""), res.verdict, refs=[res.ref],
                               also=[c for c, _, _ in found[1:]]))
        by_ref.setdefault(res.ref, res)
    groups: dict[tuple, list[CheckItem]] = {}
    for it in items:
        if not it.lcsc or it.category == "not simulated":
            key: tuple = (id(it),)                 # every row nothing simulated needs its own look
        elif it.category == "odd angle":
            key = (it.category, it.lcsc, it.layer, it.verdict)
        elif it.category == "bottom reading":
            key = (it.category, it.lcsc, it.layer, it.verdict, it.rotation, it.what)
        else:
            key = (it.category, it.lcsc, it.layer, it.verdict, it.what)
        groups.setdefault(key, []).append(it)
    items = [_merge(g) for g in groups.values()]

    # the bottom reading is one question for the whole board: a part whose pins move under every other reading
    # answers it when its row is green; the one with the most of its pads off copper under every reading (a part
    # off its pads is the easiest to see), then the most pads, first
    others = len(BOTTOM_READINGS) - 1

    def settles(it: CheckItem) -> tuple | None:
        rs = [by_ref[r] for r in it.refs]
        if it.category != "bottom reading" or it.verdict not in (GREEN, VERIFIED) or \
                any(len([m for m in r.reading_moves.values() if m]) < others for r in rs):
            return None
        r = rs[0]
        off = min(sum(k is None for _, k in m) for m in r.reading_moves.values()) / max(1, len(r.landings))
        return (-off, -len(r.landings), natural_key(r.ref))

    lead = min((it for it in items if settles(it) is not None), key=settles, default=None)
    if lead is not None:
        lead.first = True
        lead.what = ("look at this one first: it moves under every other reading, so it shows how JLC reads the "
                     "bottom side; when it sits right, the other bottom parts follow. "
                     + lead.what[0].upper() + lead.what[1:])
    return sorted(items, key=lambda it: (CATEGORIES.index(it.category), not it.first, _VRANK.get(it.verdict, 0),
                                         _rank_doubt(run, it), natural_key(it.refs[0])))


def _rank_doubt(run: Run, it: CheckItem) -> int:
    """Uncertain corrections whose choice of placement is in doubt (a polarity, names against numbers, an
    orientation the pads do not fix) first: they are the ones that can be the wrong way round."""
    info = run.resolved.get(it.ref)
    if it.category != "uncertain correction" or info is None or info.corr is None:
        return 0
    return {"polarity": 0, "names": 1, "orientation": 2}.get(info.corr.doubt, 3)


# ------------------------------------------------------------------------------------------------------- offset

OFFSET_TOL = 0.25          # mm: rows displaced by vectors this close are displaced alike (and this little, not at all)
OFFSET_TOL_GERBERS = 0.05  # ... when the Gerbers fix the CPL origin (to a micron): an origin 0.1 mm off is one too
OFFSET_SHARE = 0.6         # ... it is the CPL's when this share of the rows with a known part position are ...
OFFSET_MIN = 3             # ... and at least this many
OFFSET_IN_PLACE = 0.1      # an origin error moves every row: more than this share of the rows in place says it is not
_ORIGIN_WORDS = {"absolute": "page origin", "grid": "grid origin", "aux": "aux origin", "user": "given origin",
                 "gerber": "Gerbers' origin"}
_UNIT_WORDS = {"in": "inches", "mil": "mils", "mm": "millimetres"}


@dataclass
class Offset:
    """Most rows of a CPL displaced from where they belong by one vector: the CPL was written from another origin,
    possibly also in another unit or with its y axis pointing down. With s = scale and f = 1 if y_up else -1, a row
    (x, y) as read belongs at (s x, f s y) - (dx, dy) in the cpl frame of the board's CPL origin O; the writer's
    origin is then origin = (O.x - dx, O.y + dy) in the board frame."""
    dx: float                      # mm, cpl frame (y up): the displacement, after the scale and y corrections
    dy: float
    rows: int                      # rows displaced by it (within OFFSET_TOL)
    total: int                     # rows with a known part position
    origin: tuple[float, float]    # the origin the CPL was written from, board frame
    origin_kind: str = ""          # the named origin that is (absolute, grid, aux, ...), "" none
    scale: float = 1.0             # the CPL's numbers, as read, times this are mm (25.4: written in inches, read as mm)
    unit: str = ""                 # the unit the numbers were written in, when scale is not 1
    y_up: bool = True              # False: written with y growing downward (sheet coordinates, y not negated)
    refs: list[str] = field(default_factory=list)      # the displaced rows' references
    in_place: list[str] = field(default_factory=list)  # the rows that sit where they belong as read

    @property
    def origin_error(self) -> bool:
        """An origin (or unit, or y axis) error moves every row: at most OFFSET_IN_PLACE of them may sit where they
        belong. Rows displaced alike beside rows in place are something else (parts whose EasyEDA origin is not
        KiCad's anchor, rows edited by hand): no origin to name, no shift for every row."""
        return len(self.in_place) <= OFFSET_IN_PLACE * self.total

    def fix(self, row: CplRow) -> CplRow:
        """The row as it would be written from the board's CPL origin, in mm, y up (positions only: a writer that
        got the origin wrong got the angles right)."""
        f = 1.0 if self.y_up else -1.0
        return replace(row, x=round(self.scale * row.x - self.dx, 4) + 0.0,
                       y=round(f * self.scale * row.y - self.dy, 4) + 0.0)


def _belongs(run: Run, res: RowResult) -> tuple[float, float] | None:
    """Where a result's row belongs in the cpl frame: the row of its correction, else its part's anchor; None when
    the reference names no one part."""
    if res.status in ("not-on-board", "duplicate-ref"):
        return None
    info = run.resolved.get(res.ref)
    part = info.part if info is not None else jlc_part(run.board, res.ref)
    if part is None:
        return None
    if info is not None and info.corr is not None and not info.corr.mirror:
        x, y, _ = cpl_row_for(part, info.corr, run.board.origin, run.reading)
        return x, y
    return board_to_cpl((part.x, part.y), run.board.origin)


def _densest(P: np.ndarray, tol: float) -> tuple[np.ndarray, np.ndarray]:
    """(centre, members) of the densest group of points P (n x 2) within tol of a centre: the point with the most
    others within tol, then the median of those, twice."""
    counts = np.concatenate([(np.linalg.norm(P[k:k + 256, None] - P[None], axis=2) <= tol).sum(1)
                             for k in range(0, len(P), 256)])
    c = P[int(np.argmax(counts))]
    for _ in range(2):
        c = np.median(P[np.linalg.norm(P - c, axis=1) <= tol], axis=0)
    return c, np.linalg.norm(P - c, axis=1) <= tol


def common_offset(run: Run, *, units: str = "mm", origins: dict | None = None) -> Offset | None:
    """The vector by which most rows are displaced from where they belong (_belongs), or None: at least OFFSET_SHARE
    of the rows with a known part position, and OFFSET_MIN, within OFFSET_TOL of one vector. The hypotheses, simplest
    first (the one explaining the most rows wins): the rows as read, displaced by more than OFFSET_TOL; Mid Y not
    negated; the numbers in another unit than `units` (the one they were read in), Y negated or not. A CPL whose rows
    are where they belong as read has none, and so has one whose errors are scattered. `origins`: the board's named
    origins (board frame) to name the writer's by, after the board's own CPL origin and the page origin
    (absolute). From the Gerbers' origin (origin_kind "gerber"), alike and in place are within OFFSET_TOL_GERBERS."""
    tol = offset_tol(run.board)
    pts = [(r.row, b) for r in run.results if r.row is not None for b in [_belongs(run, r)] if b is not None]
    need = max(OFFSET_MIN, math.ceil(OFFSET_SHARE * len(pts) - 1e-9))
    if len(pts) < need:
        return None
    R = np.array([(row.x, row.y) for row, _ in pts], float)
    B = np.array([b for _, b in pts], float)
    hyps = [(1.0, True, ""), (1.0, False, "")] + [(UNITS[u] / UNITS[units], y, u) for u in UNITS if u != units
                                                 for y in (True, False)]
    best = None
    for s, y_up, unit in hyps:
        c, members = _densest(R * (s, s if y_up else -s) - B, tol)
        n = int(members.sum())
        if n < need:
            continue
        if (s, y_up) == (1.0, True) and math.hypot(*c) <= tol:
            return None                                 # the rows are where they belong
        if best is None or n > best[0]:
            best = (n, s, y_up, unit, c, members)
    if best is None:
        return None
    n, s, y_up, unit, c, members = best
    org = run.board.origin
    origin = (round(float(org[0] - c[0]), 4) + 0.0, round(float(org[1] + c[1]), 4) + 0.0)
    named = {run.board.origin_kind: org, "absolute": (0.0, 0.0), **(origins or {})}
    kind = next((k for k, q in named.items() if math.dist(q, origin) <= tol), "")
    if kind:                                        # as the board names it, not as inch numbers round it
        origin = (float(named[kind][0]) + 0.0, float(named[kind][1]) + 0.0)
    here = np.linalg.norm(R - B, axis=1) <= tol
    return Offset(round(float(c[0]), 4) + 0.0, round(float(c[1]), 4) + 0.0, n, len(pts), origin, kind, s, unit, y_up,
                  [row.ref for (row, _), m in zip(pts, members) if m],
                  [row.ref for (row, _), h, m in zip(pts, here, members) if h and not m])


def offset_tol(board: Board) -> float:
    """How close rows must be displaced to be displaced alike, or to be in place: OFFSET_TOL, or OFFSET_TOL_GERBERS
    from the Gerbers' origin."""
    return OFFSET_TOL_GERBERS if board.origin_kind == "gerber" else OFFSET_TOL


_UNSET = object()


def _rows(refs: list[str], n: int = 6) -> str:
    refs = sorted(refs, key=natural_key)
    return ", ".join(refs[:n]) + (f" and {len(refs) - n} more" if len(refs) > n else "")


def offset_hints(run: Run, *, units: str = "mm", origins: dict | None = None, off=_UNSET) -> list[str]:
    """The common offset (common_offset, or `off` when already computed) as a line for the terminal: what the rows
    would need to sit on their parts, the origin the CPL was written from, and how to fix it; [] when there is
    none. Rows displaced alike beside rows in place (not Offset.origin_error) are said as such, with no origin and no
    fix for every row."""
    if off is _UNSET:
        off = common_offset(run, units=units, origins=origins)
    if off is None:
        return []
    b = run.board
    plain = off.scale == 1.0 and off.y_up
    if not off.origin_error:
        how = (f"by ({_pm(off.dx)}, {_pm(off.dy)}) mm" if plain else
               f"as if their numbers were in {_UNIT_WORDS.get(off.unit, off.unit)}" if off.scale != 1.0 else
               "as if Mid Y grew downward")
        k = len(off.in_place)
        return [f"jlcplace: {off.rows} of {off.total} rows are displaced alike from where they belong, {how} "
                f"({_rows(off.refs)}), while {k} sit{'s' if k == 1 else ''} on "
                f"{'its part' if k == 1 else 'their parts'} (a CPL from another origin would move them all): look at "
                f"those {off.rows} rows (a footprint whose EasyEDA origin is not KiCad's anchor, rows edited by hand)"]
    n = f"{off.rows} of {off.total} row{'' if off.total == 1 else 's'} would sit on their parts"
    word = _ORIGIN_WORDS.get(b.origin_kind, b.origin_kind + " origin")
    own = f"the board's {word} ({b.origin[0]:g}, {b.origin[1]:g})"
    ox, oy = off.origin
    where = (f"the {_ORIGIN_WORDS.get(off.origin_kind, off.origin_kind + ' origin')} ({ox:g}, {oy:g})"
             if off.origin_kind else f"({ox:.3f}, {oy:.3f}) on the board, which is none of its origins")
    option = (f"--origin {off.origin_kind}" if off.origin_kind in ("absolute", "grid", "aux") else
              f"--origin {round(ox, 3):g},{round(oy, 3):g}")
    moved = plain or math.dist(off.origin, b.origin) > offset_tol(b)
    needs = ([f"if shifted by ({round(-off.dx, 3) + 0.0:.3f}, {round(-off.dy, 3) + 0.0:.3f}) mm"] if plain else
             [f"if the CPL's numbers were in {_UNIT_WORDS.get(off.unit, off.unit)}"] if off.scale != 1.0 else [])
    needs += [] if off.y_up else ["with Mid Y negated"]
    fixes = ([f"they are in mm, as JLC reads them: check without --units {units}"] if off.unit == "mm" else
             ["JLC reads a CPL's numbers in mm: write it again in mm (KiCad: --units mm; --units only tells jlcplace "
              "how to read a position file)"]) if off.scale != 1.0 else []
    if not off.y_up:
        fixes.append("the CPL's y grows downward, as the sheet's does, but JLC's Mid Y grows upward from the origin: "
                     "write it again with Y negated (KiCad's position file does)")
    text = f"{n} " + " and ".join(needs)
    if off.in_place:
        k = len(off.in_place)
        text += f" ({k} row{' sits on its part' if k == 1 else 's sit on their parts'} already: {_rows(off.in_place)})"
    if moved:
        text = (f"origin mismatch: {text}" + (f": the CPL was written from {where}" if plain
                                              else f" and relative to {where}") + f", not {own}")
    line = f"jlcplace: {text}" + (": " + "; ".join(fixes) if fixes else "")
    if moved:
        line += (". JLC lays the CPL over the Gerbers from the Gerbers' own origin: export the Gerbers, drill files "
                 "and CPL from one origin (KiCad: 'Use drill/place file origin' in all three); if the Gerbers use "
                 f"that origin too, check with {option}")
    return [line]


# ------------------------------------------------------------------------------------------------------- Gerbers
#
# JLC lays the CPL over the Gerbers from the Gerbers' own (0, 0) (gerber.gerber_origin finds that board point): with
# --gerbers, that point is the CPL origin of the check, whatever --origin says, and the run is red when the CPL was
# evidently written from another origin, when --origin names another, or when the Gerbers are not this board's as
# it is (mirrored, scaled, another board's, older than it).

#: what the board origins are called, and how KiCad plots or writes from them
_PLACES = {"aux": "the aux (drill/place file) origin", "absolute": "the page origin", "grid": "the grid origin"}
_TICK = {"aux": "ticked", "absolute": "unticked"}


def _pm(v: float) -> str:
    """'+1.270', never '-0.000'."""
    return f"{round(v, 3) + 0.0:+.3f}"


def _place(kind: str, at) -> str:
    """'board (100, 100) (the aux (drill/place file) origin)': a board point, and the origin it is."""
    name = _PLACES.get(kind, "" if kind else "none of the board's origins")      # user: the point says it
    return f"board ({at[0]:g}, {at[1]:g})" + (f" ({name})" if name else "")


def jlc_origin(check: OriginCheck | None) -> tuple[float, float] | None:
    """The board point JLC reads the CPL's rows from: the Gerbers' (0, 0), when the set is this board's and a plain
    shift of it (not mirrored, not scaled); None otherwise (no origin JLC's placement can be judged from)."""
    if check is None or check.origin is None or check.flip != "none" or abs(check.scale - 1.0) > 1e-9 \
            or not check.belongs:
        return None
    return check.origin


def gerber_notes(check: OriginCheck) -> list[str]:
    """The check's messages about the set itself: not its verdict against the CPL origin it was asked about (with
    --gerbers the CPL origin is the Gerbers' own, and origin_findings judges the rows against it)."""
    return [m for m in check.messages if not m.startswith(("origin mismatch:", "the Gerbers' (0, 0) is the CPL"))]


def gerber_findings(check: OriginCheck, rows=None) -> list[Finding]:
    """A red "gerbers" finding when the set cannot be what JLC makes this board from: nothing on it matches the
    board, it is mirrored or scaled, it is another board's, or pads moved, came, went or were renamed since it was
    plotted; and one per problem of the set as a whole (OriginCheck.problems): a side the board has SMD pads on
    without its copper layer (red) or its paste layer (yellow, when `rows`, the CPL's, place parts on that side or
    are not given), two plots in one set, the outline plotted from another origin (red)."""
    out = []
    if jlc_origin(check) is None or check.stale:
        notes = [m for m in gerber_notes(check) if "were not checked" not in m and not m.startswith("the Gerbers' pin")]
        out.append(Finding("gerbers", RED, "; ".join(notes or check.messages or ["no Gerber pads to compare"])))
    sides = None if rows is None else {r.layer for r in rows}
    for verdict, side, message in check.problems:
        if verdict != RED and side and sides is not None and side not in sides:
            continue                               # no paste where nothing is placed
        out.append(Finding("gerbers", verdict, message))
    return out


def origin_findings(run: Run, *, given=None, given_kind: str = "", units: str = "mm",
                    origins: dict | None = None) -> list[Finding]:
    """For a run checked from the Gerbers' origin (board.origin_kind "gerber"): a red "origin" finding when the rows
    are displaced alike from where they belong (common_offset, also stored as run.offset) by the vector between the
    Gerbers' origin and the one the CPL was written from, or, failing that, when `given` (the --origin point, board
    frame; `given_kind` its kind) is not the Gerbers' origin. Every part then lands off by one vector d (cpl frame):
    the message says d, |d|, and the fixes (one origin for the Gerbers and the CPL, or every row shifted by -d)."""
    b = run.board
    off = run.offset = common_offset(run, units=units, origins=origins)
    if b.origin_kind != "gerber":
        return []
    at, named = b.origin, run.gerbers.named if run.gerbers is not None else ""
    gerbers = f"JLC lays the CPL over the Gerbers from their (0, 0), {_place(named, at)}"
    if off is not None and off.origin_error and math.dist(off.origin, at) > OFFSET_TOL_GERBERS:
        plain = off.scale == 1.0 and off.y_up
        x, y = off.origin
        where = (f"{_PLACES[off.origin_kind]} ({x:g}, {y:g})" if off.origin_kind in _PLACES
                 else f"the --origin point ({x:g}, {y:g})" if off.origin_kind == "user"
                 else f"board ({x:.3f}, {y:.3f}), none of the board's origins")
        how = ([f"in {_UNIT_WORDS.get(off.unit, off.unit)}"] if off.scale != 1.0 else []) + \
            ([] if off.y_up else ["with Mid Y growing downward"])
        one = []                                   # ways to put the CPL on the Gerbers' origin: any one does
        if off.origin_kind in _TICK:
            one.append(f"plot the Gerbers and drill files from {_PLACES[off.origin_kind]} (KiCad: 'Use drill/place "
                       f"file origin' {_TICK[off.origin_kind]})")
        one.append("write the CPL from the Gerbers' origin (`jlcplace cpl BOARD --gerbers GERBERS` does"
                   + (f"; KiCad's position file: 'Use drill/place file origin' {_TICK[named]})" if named in _TICK
                      else ")"))
        but = (f" but the {len(off.in_place)} that sit on their parts already ({_rows(off.in_place)})"
               if off.in_place else "")
        if plain:
            one.append(f"shift every row{but} by ({_pm(-off.dx)}, {_pm(-off.dy)}) mm (`check --fixed-cpl FILE` "
                       "writes it)")
        each = (["write the CPL's numbers in mm (KiCad: --units mm)"] if off.scale != 1.0 else []) + \
            ([] if off.y_up else ["negate Mid Y (JLC's y grows upward from the origin)"])
        fix = " and ".join(each + [("either " if each and len(one) > 1 else "") + ", or ".join(one)]) + (
            "; `check --fixed-cpl FILE` writes the CPL so, from the Gerbers' origin" if each else "")
        # every row not in place is displaced, also one a little off the common vector (a part whose EasyEDA origin
        # is not KiCad's anchor): only rows in place make it "N of the M"
        parts = "every part" if not off.in_place else f"{off.total - len(off.in_place)} of the {off.total} parts"
        does = (f"JLC would put {parts} {math.hypot(off.dx, off.dy):.2f} mm off, displaced by ({_pm(off.dx)}, "
                f"{_pm(off.dy)}) mm" if plain else "JLC would put the parts off their pads")
        return [Finding("origin", RED, f"origin mismatch: {gerbers}, but the CPL was written from {where}"
                        + (" " + " and ".join(how) if how else "") + f" ({off.rows} of {off.total} rows): {does}; "
                        "fix: " + fix)]
    if given is not None and math.dist(given, at) > ORIGIN_TOL:
        d = (at[0] - given[0], given[1] - at[1])         # where a row written from `given` lands, less where it belongs
        return [Finding("origin", RED, f"origin mismatch: --origin puts the CPL's origin at {_place(given_kind, given)}"
                        f", but {gerbers}: a CPL written from --origin's point would put every part "
                        f"{math.hypot(*d):.2f} mm off, displaced by ({_pm(d[0])}, {_pm(d[1])}) mm; the rows are "
                        "checked from the Gerbers' origin, as JLC reads them: drop --origin, or plot the Gerbers "
                        "from that origin too")]
    return []


def origin_lines(run: Run) -> list[tuple[str, str]]:
    """(verdict, text) about the origin for the checklist: the findings; else what the Gerbers' check found; else a
    reminder that the Gerbers must be plotted from the CPL's origin (they were not given)."""
    if run.findings:
        return [(f.verdict, f.message) for f in run.findings]
    b = run.board
    if b.origin_kind == "gerber":
        named = run.gerbers.named if run.gerbers is not None else ""
        return [(GREEN, f"the Gerbers' (0, 0) is this CPL's origin, {_place(named, b.origin)}: the rows were checked "
                        "from it, as JLC reads them")]
    word = _ORIGIN_WORDS.get(b.origin_kind, b.origin_kind + " origin")
    return [("", f"the Gerbers were not checked: plot them and the drill files from this CPL's origin, the {word} "
                 f"({b.origin[0]:g}, {b.origin[1]:g}), as JLC lays the CPL over the Gerbers from their own (0, 0) "
                 "(--gerbers checks it)")]


# ------------------------------------------------------------------------------------------------------- misc

def with_bom_codes(board: Board, bom_rows) -> Board:
    """A copy of the board whose parts carry their BOM codes (apply_bom_codes; the original is not changed)."""
    b = replace(board, parts=[replace(p) for p in board.parts])
    apply_bom_codes(b, bom_rows)
    return b
