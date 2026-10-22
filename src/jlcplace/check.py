"""Simulate a CPL against the board: put JLC's EasyEDA pads down where each row says, and see what they land on.

For a row (C, A, layer), EasyEDA pad centre e goes to board point  q = convention.ee_to_board(e, C, A, layer,
reading),  i.e. library point  l = board_to_library(part, q).  It lands on the KiCad copper pad that contains l
(pads drawn over each other, USB-C A1/B12: the one with its own number, else the nearest centre), or off copper.
Only the pad centres are placed: a centre on copper is what the pick-and-place gets right or wrong; how well the
lands fit is the fit's business.

Pin -> net
----------
A landing's net key is the KiCad pad's net, or "#<number>" for a pad without a net (it is its own net). An EasyEDA
pad on another-numbered KiCad pad is
  harmless      when its own number does not exist in KiCad (MP, S1, A1B12: one side lacks the name), when the pad it
                lands on has the net its own number has, or when a non-polar two-terminal part (R, C, L, a switch
                numbered 1,1,2,2, ...) has its two terminals swapped as a whole;
  accepted      when the row gives every EasyEDA pad the net the correction (fit / db) gives it: the numbering
                difference is then the correction's to vouch for, and the row takes its verdict;
  wrong-pad     otherwise (a turned SOT-23-6 lands every pad on copper; this is the only sign of it).
"Non-polar" is parts.nonpolar, the rule the fit uses too: the reference prefix, the KiCad footprint name and pin
functions, and the EasyEDA record's title, part categories and pin names.
A known correction also overrules the numbers the other way: a row that lands every pad on its own number but on
other nets than the correction does is red against a verified correction, red for a polar two-terminal part (its
polarity the fit took from pin names or a drawn + against the numbers: the row reverses it) and yellow against a
fitted one otherwise. The numbers decide a row only when most of the EasyEDA pads whose number KiCad has (EVIDENCE)
land on it: when they do not (KiCad's K / A against EasyEDA's 1 / 2, BGA letters against digits), a row that differs
from the correction is red, and so is any row when the correction is red, since nothing vouches for it.
A row that lands the pads as a fitted correction does whose choice among several placements is in doubt
(Correction.doubt: a polarity only the pad numbers decide, an orientation the pads do not fix) is yellow. A row that
lands every pad on its own pad but sits off where a (not red) correction puts the part is yellow ("offset").

Bottom rows are placed again under every other reading in convention.BOTTOM_READINGS: a row whose pin -> net mapping
changes, or whose pads leave copper, under one of them is `convention_sensitive` (look at it in JLC's preview).
Relative to jlc-2022, the readings turn a bottom part by 2A (ccw-from-top), 2A + 180 (ccw-from-top+180) and 180
(jlc-2022+180) degrees about its row point, A the row's angle.
"""

from __future__ import annotations

import math
from collections import Counter

from . import geom
from .convention import BOTTOM_READINGS, DEFAULT, board_to_library, cpl_row_for, cpl_to_board, ee_to_board, \
    ee_to_library
from .cpl import assembled, correction_for, natural_key
from .lcsc import normalize as normalize_lcsc
from .model import BOTTOM, GREEN, RED, VERDICT_ORDER, VERIFIED, YELLOW, Board, Correction, CplRow, EEFootprint, \
    NotFound, Pad, PadLanding, Part, RowResult
from .parts import nonpolar as _nonpolar
from .parts import norm_number, pad_size, uncovered_exemption

LAND_TOL = 1e-3            # mm: a centre this close outside a pad's edge is on it (CPL values carry 1e-4 mm)
ROW_TOL = 0.05             # mm: a row further than this from the one jlcplace writes gets a message ...
ANGLE_TOL = 0.5            # ... or turned more than this many degrees
OFFSET_FLOOR = 0.15        # mm: a row further than this from the row of a known (not red) correction ...
OFFSET_PAD = 0.25          # ... and than this fraction of the smallest KiCad pad its pads land on, is yellow "offset"
OFFSET_ANGLE = 2.0         # degrees: so is one turned more than this (less than 45: a different orientation is the
                           # landing test's business)
EVIDENCE = 0.8             # fraction of the numbered EasyEDA pads on their own number for the numbers to decide a row
UNSIM_MARGIN = 0.1         # mm: a not-simulated row point this far outside the part's pads is off the part

_SOLDERED = ("smd", "tht")


# ------------------------------------------------------------------------------------------------------- helpers

def _key(number, net) -> str:
    return net if net else "#" + norm_number(number)


def _lkey(landing: PadLanding) -> str | None:
    """Net key of what an EasyEDA pad lands on; None off copper."""
    return None if landing.kicad_number is None else _key(landing.kicad_number, landing.net)


def nonpolar(part: Part, ee=None, corr: Correction | None = None) -> bool:
    """Two numbered terminals that may be swapped (parts.nonpolar, the rule fit uses: the reference prefix, the KiCad
    footprint name and pin functions, and with `ee` the EasyEDA title, part categories and pin names). A correction
    whose fit judged the part polar (`corr.polar`) keeps it polar, so the check of a row simulated without the
    EasyEDA record (pipeline's proxy) judges the part as the fit did."""
    if corr is not None and corr.polar:
        return False
    return _nonpolar(part, ee if isinstance(ee, EEFootprint) and ee.source != "kicad-proxy" else None)


def _harmless_unsimulated(part: Part, corr: Correction | None = None) -> bool:
    """A part whose placement is safe to leave unchecked: two pads, no polarity."""
    return nonpolar(part, None, corr) and len(part.copper_pads()) == 2


def _not_harmless(part: Part) -> str:
    """Why a part without an EasyEDA footprint cannot be left unchecked."""
    pads = part.copper_pads()
    if len(pads) == 1:
        return "a one-pad part, whose placement cannot be checked without its footprint"
    if len(pads) == 2 and len({norm_number(p.number) for p in pads if p.number}) == 2:
        return "a polar two-pad part"
    return f"not a two-pad non-polar part ({len(pads)} copper pad{'' if len(pads) == 1 else 's'})"


def _off_part(part: Part, row: CplRow, origin) -> float | None:
    """How far the row's point lies outside the part's copper (its pads' bounding box grown by UNSIM_MARGIN), in mm;
    None when it is on the part."""
    l = board_to_library(part, cpl_to_board((row.x, row.y), origin))
    pts = [q for p in part.copper_pads() for q in geom.pad_outline(p)]
    if not pts:
        return None
    x0, y0 = min(q[0] for q in pts) - UNSIM_MARGIN, min(q[1] for q in pts) - UNSIM_MARGIN
    x1, y1 = max(q[0] for q in pts) + UNSIM_MARGIN, max(q[1] for q in pts) + UNSIM_MARGIN
    d = math.hypot(max(x0 - l[0], 0.0, l[0] - x1), max(y0 - l[1], 0.0, l[1] - y1))
    return d if d > 0 else None


def _ee_pads(ee: EEFootprint):
    # pads on EasyEDA's bottom layer are on the far side of the part: they never meet the part's copper
    return [p for p in ee.pads if p.layer != BOTTOM]


def _land(pads: list[Pad], l, number: str) -> tuple[Pad | None, float]:
    """(the copper pad library point l lands on, distance to the nearest pad centre)."""
    if not pads:
        return None, math.inf
    hits = [p for p in pads if geom.pad_contains(p, l, LAND_TOL)]
    dist = min(math.dist(l, (p.x, p.y)) for p in pads)
    if not hits:
        return None, dist
    n = norm_number(number)
    same = [p for p in hits if n and norm_number(p.number) == n]
    return min(same or hits, key=lambda p: math.dist(l, (p.x, p.y))), dist


def _place(part: Part, row: CplRow, ee: EEFootprint, origin, reading: str):
    """[(library point, PadLanding, KiCad pad or None)] for every EasyEDA pad, placed as JLC reads the row."""
    C = cpl_to_board((row.x, row.y), origin)
    pads = part.copper_pads()
    if row.layer != part.layer:      # placed on the far side: only through-hole copper is there too
        pads = [p for p in pads if p.kind == "tht"]
    out = []
    for e in _ee_pads(ee):
        l = board_to_library(part, ee_to_board((e.x, e.y), C, row.rotation, row.layer, reading))
        pad, d = _land(pads, l, e.number)
        out.append((l, PadLanding(e.number, pad.number if pad else None, round(d, 4) + 0.0, pad.net if pad else ""),
                    pad))
    return out


def _swap_of(a: list, b: list) -> bool:
    """b is a with the two terminals of a two-terminal part exchanged everywhere."""
    keys = set(a) | set(b)
    if None in keys or len(keys) != 2:
        return False
    t1, t2 = keys
    other = {t1: t2, t2: t1}
    return all(y == other[x] for x, y in zip(a, b))


def _pairs(landings, idx) -> str:
    idx = sorted(idx, key=lambda i: natural_key(landings[i].ee_number))
    s = [f"{landings[i].ee_number}->{landings[i].kicad_number}" for i in idx]
    return ", ".join(s[:8]) + (" ..." if len(s) > 8 else "")


def _worst(*verdicts: str) -> str:
    return min(verdicts, key=lambda v: VERDICT_ORDER.get(v, 0))


_DOUBT_WORDS = {"polarity": ("polarity", "pin names pick", "+ "), "names": ("pin names pick",),
                "orientation": ("orientation", "one land")}


def _doubt_reason(corr: Correction) -> str:
    """The correction's reason that says why its choice of placement is uncertain."""
    words = _DOUBT_WORDS.get(corr.doubt, ())
    return next((r for r in corr.reasons if any(w in r for w in words)), corr.reasons[0] if corr.reasons else
                corr.doubt)


# ------------------------------------------------------------------------------------------------------- one row

def place_row(part: Part, row: CplRow, ee: EEFootprint, origin, reading: str = DEFAULT) -> list[PadLanding]:
    """Where each EasyEDA pad (centre) lands when JLC places `ee` as `row` says, under `reading` for the bottom.
    The CPL origin `origin` is in the board frame."""
    return [lnd for _, lnd, _ in _place(part, row, ee, origin, reading)]


def check_row(part: Part, row: CplRow, ee, origin, *, corr: Correction | None = None, reading: str = DEFAULT,
              alt_readings=True) -> RowResult:
    """Simulate one row. `ee` is an EEFootprint, NotFound or None; `corr` the part's correction if known (for the
    expected row, and as the authority on which net each EasyEDA pad belongs to). `alt_readings`: True = every other
    bottom reading, False = none, or an iterable of reading names.

    Status / verdict: off-copper red; wrong-pad red (by number), or, when the pads land by number on other nets than
    the correction gives them, red against a verified correction, for a polar two-terminal part, or when the
    numbers do not decide the row, and yellow against a fitted one otherwise; renumbered
    (harmless) green, or the correction's verdict when the correction is what accepts it. Against a red correction
    the pad numbers must decide the row: red when fewer than EVIDENCE of the EasyEDA pads whose number KiCad has land
    on it (nothing vouches for the placement), and red for a polar two-terminal part whose pads land otherwise than
    the correction puts them (its polarity needs a decision). A row that lands the pads as a fitted correction does
    whose choice of placement is uncertain (`corr.doubt`) is yellow. KiCad soldered pads no EasyEDA pad lands on:
    yellow (`uncovered`; vias and stubs of a covered pin excepted). not-simulated (no EasyEDA footprint): yellow for a
    non-polar two-pad part whose row point is on it, red otherwise. A row that matches the expected row of a verified
    correction and has no issue is VERIFIED. A row more than ROW_TOL / ANGLE_TOL from the expected row gets a
    message; more than OFFSET_FLOOR mm and OFFSET_PAD of the smallest pad it lands on, or OFFSET_ANGLE degrees, from
    that of a correction that is not red, it is yellow ("offset")."""
    if reading not in BOTTOM_READINGS:
        raise ValueError(f"unknown reading {reading!r}: one of {', '.join(BOTTOM_READINGS)}")
    if alt_readings is not True and alt_readings:
        alt_readings = list(alt_readings)
        unknown = [r for r in alt_readings if r not in BOTTOM_READINGS]
        if unknown:
            raise ValueError(f"unknown readings {unknown}: one of {', '.join(BOTTOM_READINGS)}")
    res = RowResult(part.ref, lcsc=part.lcsc, row=row)
    top = GREEN
    dev = _expected(res, part, row, corr, origin, reading) if corr is not None else None
    if dev is not None and dev[0] <= ROW_TOL and dev[1] <= ANGLE_TOL and corr.verdict == VERIFIED:
        top = VERIFIED
    if not isinstance(ee, EEFootprint) or not _ee_pads(ee):
        why = (f"no EasyEDA footprint ({ee.message})" if isinstance(ee, NotFound) else
               "no EasyEDA footprint" if ee is None else "the EasyEDA footprint has no pads")
        res.status = "not-simulated"
        if _harmless_unsimulated(part, corr):
            far = _off_part(part, row, origin)
            res.verdict = YELLOW if far is None else RED
            res.messages.insert(0, f"placement not simulated: {why}" + (
                "; a two-pad non-polar part, and the row point is on it" if far is None else
                f"; the row point is {far:.2f} mm off the part's pads"))
        else:
            res.verdict = RED
            res.messages.insert(0, f"placement not simulated: {why}; {_not_harmless(part)}")
        return res

    placed = _place(part, row, ee, origin, reading)
    lands = res.landings = [lnd for _, lnd, _ in placed]
    pads = part.copper_pads()
    numbers = {norm_number(p.number) for p in pads if p.number}
    nets_of: dict[str, set[str]] = {}
    for p in pads:
        if p.number:
            nets_of.setdefault(norm_number(p.number), set()).add(_key(p.number, p.net))
    issues, status, msgs = [], "ok", []
    want = _corr_keys(part, ee, corr) if corr is not None else None
    got = [_lkey(lnd) for lnd in lands]
    flip_ok = nonpolar(part, ee, corr)
    polar2 = len(numbers) == 2 and not flip_ok
    as_corr = want is not None and None not in want and (got == want or (flip_ok and _swap_of(want, got)))
    # the numbers decide a row when the EasyEDA pads whose number KiCad has land on that number, or on its net
    known = [lnd for lnd in lands if norm_number(lnd.ee_number) in numbers]
    own = sum(1 for lnd in known if lnd.kicad_number is not None and (
        norm_number(lnd.kicad_number) == norm_number(lnd.ee_number)
        or _lkey(lnd) in nets_of[norm_number(lnd.ee_number)]))
    decided = bool(known) and own >= EVIDENCE * len(known) and own >= min(2, len(numbers))

    off = [i for i, lnd in enumerate(lands) if lnd.kicad_number is None]
    if off:
        status = "off-copper"
        issues.append(RED)
        msgs.append(f"{len(off)} of {len(lands)} EasyEDA pads land off copper: "
                    + ", ".join(sorted((lands[i].ee_number or "?" for i in off), key=natural_key)[:8])
                    + (" ..." if len(off) > 8 else "")
                    + f" (nearest KiCad pad centre up to {max(lands[i].dist for i in off):.2f} mm away)")

    lacks, same_net, conflicts = [], [], []
    for i, lnd in enumerate(lands):
        n = norm_number(lnd.ee_number)
        if lnd.kicad_number is None or not n or n == norm_number(lnd.kicad_number):
            continue
        if n not in numbers:
            lacks.append(i)
        elif _lkey(lnd) in nets_of[n]:
            same_net.append(i)
        else:
            conflicts.append(i)
    if conflicts:
        known_i = [i for i, lnd in enumerate(lands) if norm_number(lnd.ee_number) in numbers]
        own_nets = [nets_of[norm_number(lands[i].ee_number)] for i in known_i]
        expect = [next(iter(k)) if len(k) == 1 else None for k in own_nets]
        if not off and flip_ok and _swap_of(expect, [got[i] for i in known_i]):
            status = "renumbered" if status == "ok" else status
            msgs.append(f"the two terminals are swapped (non-polar part): {_pairs(lands, conflicts)}")
        elif as_corr:
            status = "renumbered" if status == "ok" else status
            issues.append(corr.verdict if corr.verdict in VERDICT_ORDER else YELLOW)
            if corr.verdict == RED:
                msgs.append(f"the pads land as the correction ({corr.status or corr.method or 'given'}) puts them, "
                            f"but it is red, so nothing vouches for this numbering: {_pairs(lands, conflicts)}")
            else:
                msgs.append(f"numbering accepted by the correction ({corr.status or corr.method or 'given'}): "
                            f"{_pairs(lands, conflicts)}")
        else:
            status = "wrong-pad" if status == "ok" else status
            issues.append(RED)
            conflicts.sort(key=lambda i: natural_key(lands[i].ee_number))
            detail = [f"{lands[i].ee_number}->{lands[i].kicad_number} ({lands[i].net or 'no net'}, not "
                      f"{' / '.join(sorted(nets_of[norm_number(lands[i].ee_number)]))})" for i in conflicts[:6]]
            msgs.append("EasyEDA pads land on other-numbered KiCad pads of other nets: " + ", ".join(detail)
                        + (" ..." if len(conflicts) > 6 else ""))
    elif want is not None and not off and corr.verdict == RED:
        why = corr.reasons[0] if corr.reasons else "no reason given"
        if polar2 and None not in want and (not as_corr or corr.doubt == "polarity"):
            # the numbers say one way, the red correction (names against numbers, a drawn + against them) the
            # other, or it could not decide between them: a reversed diode would pass if the numbers alone decided
            issues.append(RED)
            if as_corr:
                msgs.append(f"the pads land as the correction puts them, but it is red: the polarity needs a decision "
                            f"({why})")
            else:
                status = "wrong-pad"
                msgs.append(f"the pads land by number, but the correction is red and puts them on other nets: the "
                            f"polarity needs a decision ({why})")
        elif not decided:
            issues.append(RED)
            msgs.append(f"the pad numbers do not decide this placement ({own} of {len(known)} EasyEDA pads with a "
                        f"number KiCad has land on that number) and the correction is red ({why}): nothing vouches "
                        f"for it")
    elif want is not None and not off:
        if None in want:
            msgs.append("the correction itself puts EasyEDA pads off copper")
        elif not as_corr:
            # against a fitted correction the numbers might be right after all (yellow), unless the correction was
            # verified, the part is polar with two terminals (the fit decided its polarity against the numbers, by
            # names or a drawn +: the row is that part reversed) or the numbers do not decide the row
            status = "wrong-pad" if status == "ok" else status
            issues.append(RED if corr.verdict == VERIFIED or polar2 or not decided else YELLOW)
            idx = sorted((i for i in range(len(lands)) if got[i] != want[i]), key=lambda i: natural_key(
                lands[i].ee_number))
            msgs.append(("the pads land by number" if decided else "the pad numbers do not decide this placement")
                        + f", but the {'verified ' if corr.verdict == VERIFIED else ''}correction puts them on "
                        f"other nets: " + ", ".join(f"{lands[i].ee_number} on {got[i]}, not {want[i]}"
                                                    for i in idx[:6]) + (" ..." if len(idx) > 6 else ""))
    elif want is None and not off and not decided:
        issues.append(YELLOW)
        msgs.append(f"the pad numbers do not decide this placement ({own} of {len(known)} EasyEDA pads with a "
                    f"number KiCad has land on that number), and no correction is known")
    if as_corr and not off and not conflicts and corr.verdict == YELLOW and corr.doubt and corr.status == "auto":
        # the fit chose this placement among others that land every pad, and could be wrong (a polarity only the pad
        # numbers decide, names against numbers, a body the pads do not orient)
        issues.append(YELLOW)
        msgs.append(f"lands as the correction puts it, but the fit was unsure of that placement: {_doubt_reason(corr)}")
    elif as_corr and conflicts and corr.verdict == YELLOW and corr.doubt and corr.status == "auto":
        msgs.append(f"the fit was unsure of that placement: {_doubt_reason(corr)}")
    if same_net:
        msgs.append(f"EasyEDA pads on other-numbered KiCad pads of the same net: {_pairs(lands, same_net)}")
    if lacks:
        msgs.append(f"numbers only one side has: {_pairs(lands, lacks)}")

    covered = {id(p) for l, _, _ in placed for p in pads if geom.pad_contains(p, l, LAND_TOL)}
    cov = [p for p in pads if id(p) in covered]
    unc, excused = [], []
    for p in pads:
        if p.number and p.kind in _SOLDERED and id(p) not in covered:
            (excused if uncovered_exemption(p, cov) else unc).append(p.number)
    res.uncovered = sorted(set(unc), key=natural_key)
    if res.uncovered:
        issues.append(YELLOW)
        msgs.append("KiCad pads that no EasyEDA pad lands on: " + ", ".join(res.uncovered))
    if excused:
        msgs.append("KiCad pads that no EasyEDA pad lands on, of pins an EasyEDA pad covers (vias, a cut lead's "
                    "stub): " + ", ".join(sorted(set(excused), key=natural_key)))

    if dev is not None and not off and corr.verdict in (GREEN, YELLOW, VERIFIED) and dev[1] <= 45.0:
        d, da = dev
        sizes = [pad_size(p) for _, _, p in placed if p is not None]
        tol = max(OFFSET_FLOOR, OFFSET_PAD * min(sizes)) if sizes else OFFSET_FLOOR
        if d > tol or da > OFFSET_ANGLE:
            # every centre is on its own pad, but the part sits off where the correction puts it
            issues.append(YELLOW)
            status = "offset" if status == "ok" else status
            moved = next((m for m in res.messages if m.startswith("differs from the row jlcplace writes")), "")
            if moved:
                res.messages.remove(moved)
            msgs.insert(0, f"{moved}, more than {tol:.2f} mm or {OFFSET_ANGLE:g} deg: the part sits off where the "
                           f"correction puts it" if moved else
                        f"{d:.3f} mm and {da:g} deg from the row jlcplace writes")

    if row.layer == BOTTOM and alt_readings:
        _sensitivity(res, part, row, ee, origin, reading, alt_readings, flip_ok)

    res.status = status
    res.verdict = _worst(top, *issues)
    res.messages[:0] = msgs
    return res


def _corr_keys(part: Part, ee: EEFootprint, corr: Correction) -> list[str | None]:
    """The net key each EasyEDA pad gets under the correction itself (library = R(rot) . M . e + d). Rows are
    compared by nets, not numbers: a switch whose EasyEDA pads 1, 2 are one terminal, like KiCad's 1, 1, is fine
    turned 180 degrees, which only the correction can tell."""
    pads = part.copper_pads()
    out = []
    for e in _ee_pads(ee):
        pad, _ = _land(pads, ee_to_library(corr, (e.x, e.y)), e.number)
        out.append(_key(pad.number, pad.net) if pad else None)
    return out


def _expected(res: RowResult, part: Part, row: CplRow, corr: Correction, origin, reading: str
              ) -> tuple[float, float] | None:
    """Fill res.expected (the row jlcplace writes); (distance in mm, angle in degrees) of the given row from it, or
    None when the correction makes no row (mirrored) or the row is on the other layer."""
    try:
        x, y, a = cpl_row_for(part, corr, origin, reading)
    except ValueError as e:
        res.messages.append(str(e))
        return None
    res.expected = CplRow(part.ref, x, y, part.layer, a)
    d = math.hypot(row.x - x, row.y - y)
    da = abs((row.rotation - a + 180.0) % 360.0 - 180.0)
    if d > ROW_TOL or da > ANGLE_TOL or row.layer != part.layer:
        res.messages.append(f"differs from the row jlcplace writes ({x:.4f}, {y:.4f}, {part.layer}, {a:g}): "
                            f"{d:.3f} mm and {da:g} deg away")
    return None if row.layer != part.layer else (d, da)


def _sensitivity(res: RowResult, part: Part, row: CplRow, ee: EEFootprint, origin, reading: str,
                 alt_readings, flip_ok: bool) -> None:
    names = list(BOTTOM_READINGS) if alt_readings is True else alt_readings
    base = [_lkey(lnd) for lnd in res.landings]
    changed = []
    for name in names:
        if name == reading:
            continue
        alt = place_row(part, row, ee, origin, name)
        keys = [_lkey(lnd) for lnd in alt]
        if keys == base or (flip_ok and _swap_of(base, keys)):
            continue
        moved = sorted((a for a, k, b in zip(alt, keys, base) if k != b), key=lambda a: natural_key(a.ee_number))
        moved = [f"{a.ee_number}->{a.kicad_number or 'off'}" for a in moved]
        changed.append(f"{name}: " + ", ".join(moved[:6]) + (" ..." if len(moved) > 6 else ""))
    if changed:
        res.convention_sensitive = True
        res.messages.append("bottom reading matters, check it in JLC's preview: " + "; ".join(changed))


# ------------------------------------------------------------------------------------------------------- whole CPL

def check_cpl(board: Board, rows: list[CplRow], ee_lookup, *, corrections=None, reading: str = DEFAULT,
              bom_refs=None) -> list[RowResult]:
    """One RowResult per CPL row (in order), then one per part JLC assembles that has an LCSC code and no row
    ("no-row", red). Rows for refs not on the board, for parts JLC does not assemble, on the wrong layer, or repeated
    are red ("not-on-board", "not-placed", "layer-mismatch", "duplicate"); a row for a part only excluded from the
    BOM is yellow ("not-placed": JLC places only what the BOM lists). `ee_lookup(code)` returns an EEFootprint,
    NotFound or None; an exception from it counts as no footprint. `corrections` is keyed by reference or code.
    `bom_refs`, the designators a JLC BOM lists: a part only excluded from the board's BOM that the BOM lists is
    simulated like any other (JLC places what the BOM lists), with a message."""
    count = Counter(r.ref for r in rows)
    cache: dict[str, object] = {}

    def lookup(code: str):
        if code not in cache:
            try:
                cache[code] = ee_lookup(code)
            except Exception as e:  # noqa: BLE001 - the lookup does I/O (cache, network); the row says why
                cache[code] = NotFound(code, f"lookup failed ({type(e).__name__}: {e})")
        return cache[code]

    out = []
    for row in rows:
        part = board.part(row.ref)
        if part is None:
            out.append(RowResult(row.ref, status="not-on-board", verdict=RED, row=row,
                                 messages=[f"{row.ref} is not on the board"]))
            continue
        listed = False
        if not assembled(part):
            flags = [n for n, f in (("DNP", part.dnp), ("excluded from position files", part.exclude_pos),
                                    ("board-only", part.board_only), ("excluded from the BOM", part.exclude_bom)) if f]
            only_bom = flags == ["excluded from the BOM"]
            listed = only_bom and bom_refs is not None and row.ref in bom_refs
            if not listed:
                out.append(RowResult(row.ref, part.lcsc, status="not-placed", verdict=YELLOW if only_bom else RED,
                                     row=row,
                                     messages=[f"the board part is {', '.join(flags)}: JLC must not place it"]))
                continue
        if row.layer != part.layer:
            out.append(RowResult(row.ref, part.lcsc, status="layer-mismatch", verdict=RED, row=row,
                                 messages=[f"the row says {row.layer}, the part is on the {part.layer}"]))
            continue
        res = check_row(part, row, lookup(part.lcsc) if part.lcsc else None, board.origin,
                        corr=correction_for(corrections, part), reading=reading)
        if not part.lcsc:
            res.messages.insert(0, "the board part has no LCSC code")
        if listed:
            res.messages.append("the board excludes it from the BOM, but the BOM lists it: JLC will place it")
        if count[row.ref] > 1:
            res.status, res.verdict = "duplicate", RED
            res.messages.insert(0, f"{count[row.ref]} rows for {row.ref}")
        out.append(res)
    for part in sorted(board.parts, key=lambda p: natural_key(p.ref)):
        if assembled(part) and part.lcsc and part.ref not in count:
            out.append(RowResult(part.ref, part.lcsc, status="no-row", verdict=RED,
                                 messages=["JLC assembles it (it has an LCSC code) but the CPL has no row"]))
    return out


def check_bom(board: Board, bom_rows: list[dict], cpl_rows: list[CplRow]) -> list[str]:
    """Consistency of a JLC BOM (cpl.read_bom rows) with the board and the CPL, as messages: every CPL ref in the
    BOM and every BOM designator in the CPL; BOM code == board code; DNP/excluded parts not in the BOM and assembled
    coded parts in it; Qty == number of designators; no designator twice."""
    msgs: list[str] = []
    seen: Counter = Counter()
    cpl_refs = {r.ref for r in cpl_rows}
    for i, b in enumerate(bom_rows, 1):
        des = list(b.get("designators") or [])
        label = f"BOM line {i} ({b.get('comment') or b.get('lcsc') or ', '.join(des[:3]) or '?'})"
        if not des:
            msgs.append(f"{label}: no designators")
        if b.get("qty") is not None and b["qty"] != len(des):
            msgs.append(f"{label}: Qty {b['qty']} but {len(des)} designators")
        raw = str(b.get("lcsc") or "").strip()
        code = normalize_lcsc(raw)
        if not code:
            msgs.append(f"{label}: {raw!r} is not an LCSC code" if raw else f"{label}: no LCSC code")
        for d in des:
            seen[d] += 1
            part = board.part(d)
            if part is None:
                msgs.append(f"{d}: in the BOM but not on the board")
                continue
            if not assembled(part):
                flags = [n for n, f in (("DNP", part.dnp), ("excluded from position files", part.exclude_pos),
                                        ("board-only", part.board_only), ("excluded from the BOM", part.exclude_bom))
                         if f]
                msgs.append(f"{d}: in the BOM but {', '.join(flags)} on the board")
            if code and part.lcsc != code:
                msgs.append(f"{d}: the BOM says {code}, the board says {part.lcsc or 'no LCSC code'}")
            if d not in cpl_refs:
                msgs.append(f"{d}: in the BOM but the CPL has no row for it")
    for d, n in seen.items():
        if n > 1:
            msgs.append(f"{d}: {n} times in the BOM")
    for ref in sorted(cpl_refs - set(seen), key=natural_key):
        msgs.append(f"{ref}: a CPL row but no BOM line (JLC will not place it)")
    for part in sorted(board.parts, key=lambda p: natural_key(p.ref)):
        if assembled(part) and part.lcsc and part.ref not in seen:
            msgs.append(f"{part.ref} ({part.lcsc}): JLC should assemble it but the BOM does not list it")
    return list(dict.fromkeys(msgs))
