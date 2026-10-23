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
fitted one otherwise. A polar two-terminal part turned the other way round than a correction that is not red puts
it, by its numbers or not, is "reversed" (red). The numbers decide a row only when most of the EasyEDA pads whose
number KiCad has (EVIDENCE) land on it: when they do not (KiCad's K / A against EasyEDA's 1 / 2, BGA letters against
digits), a row that differs from the correction is red, and so is any row when the correction is red, since nothing
vouches for it.
A row that lands the pads as a fitted correction does whose choice among several placements is in doubt
(Correction.doubt: a polarity only the pad numbers decide, an orientation the pads do not fix) is yellow. A row that
lands every pad on its own pad but sits off where a (not red) correction puts the part is yellow ("offset").
A stored correction whose numbers were verified wrong (`confirm --failed`: Correction.failure) makes its own row
red ("verified-wrong"), whatever the pads land on: what was wrong may be the footprint's numbering, or a body its pads
do not orient. A row away from it is yellow: nothing verified vouches for it either.

Pin names
---------
With the EasyEDA record, the symbol's pin names through the row are compared with the KiCad pin functions of the pads
they land on (parts.pin_diffs), whatever the correction says. Names permuted (a pin on the net of another pin's
function) with no placement known to do better by them (the correction's own, its alternatives; a two-terminal part's
half turn always does: its names are its polarity) are red, status "pinout": the part is wired wrong whatever the
rotation. Other differences are yellow ("check the LCSC part's pinout against the symbol"). Names swapped between pads
of one net, a non-polar two-terminal part's names, and a polar one's whose drawn + sits on KiCad's positive pad say
nothing against the row. A manual or board-verified correction that the row lands as settles them (manual: yellow).

Bottom rows are placed again under every other reading in convention.BOTTOM_READINGS: a row whose pin -> net mapping
changes, or whose pads leave copper, under one of them is `convention_sensitive` (look at it in JLC's preview), and
`reading_moves` says, per such reading, which EasyEDA pads move and where to (the checklist says it in pins).
Relative to jlc-2022, the readings turn a bottom part by 2A (ccw-from-top), 2A + 180 (ccw-from-top+180) and 180
(jlc-2022+180) degrees about its row point, A the row's angle.
"""

from __future__ import annotations

import math
from collections import Counter

from . import geom
from .convention import BOTTOM_READINGS, DEFAULT, board_to_library, cpl_row_for, cpl_to_board, ee_to_board, \
    ee_to_library
from .cpl import assembled, correction_for, jlc_places, natural_key
from .lcsc import normalize as normalize_lcsc
from .model import BOTTOM, GREEN, RED, VERDICT_ORDER, VERIFIED, YELLOW, Board, Correction, CplRow, EEFootprint, \
    NotFound, Pad, PadLanding, Part, RowResult
from .parts import BODY_TOL, BODY_WORDS, anchor_pin, body_centre, compare_names, ee_positive_pad, kicad_positive_pad, \
    norm_number, pad_size, pin, pin_diffs, pinout_text, trimmer, uncovered_exemption
from .parts import nonpolar as _nonpolar

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
    """A part whose placement is safe to leave unchecked: two pads, no polarity; or a trimmer's two pads (which is its
    rotor goes on the checklist, not against the row)."""
    return (nonpolar(part, None, corr) or trimmer(part)) and len(part.copper_pads()) == 2


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


def _row_point(part: Part, row: CplRow, origin) -> str:
    """Where a row point lies on a part that cannot be simulated, from its body (parts.body_centre) and KiCad's
    anchor: JLC places a part by its centre, and KiCad's anchor is pin 1 on most through-hole connectors.
    '; the row point is the centre of its courtyard', '; the row point is KiCad's anchor, pin 1, 13.50 mm from the
    centre of its courtyard: JLC places a part by its centre', or ''."""
    lp = board_to_library(part, cpl_to_board((row.x, row.y), origin))
    c, pin = body_centre(part), anchor_pin(part)
    words = BODY_WORDS.get(part.body_layer, "body")
    if c is not None and math.dist(lp, c) <= BODY_TOL:
        return f"; the row point is the centre of its {words}"
    if math.hypot(*lp) <= BODY_TOL and (pin or c is not None):
        said = ["KiCad's anchor"] + ([f"pin {pin}"] if pin else []) + (
            [f"{math.dist(lp, c):.2f} mm from the centre of its {words}"] if c is not None else [])
        return f"; the row point is {', '.join(said)}: JLC places a part by its centre"
    if c is not None:
        return f"; the row point is {math.dist(lp, c):.2f} mm from the centre of its {words}"
    return ""


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


_SOURCE_WORDS = {"preview": "JLC's preview", "board": "the assembled board", "manual": "a manual check"}


def failure_text(v: dict) -> str:
    """'verified wrong by JLC's preview on 2026-10-23 (U3, ann: pin 1 at the wrong corner)': a failed verification
    (Correction.failure) as the check and the checklist say it."""
    src = str(v.get("source") or "")
    where = _SOURCE_WORDS.get(src.removesuffix("-verified"), src or "a verification")
    said = ": ".join(t for t in (", ".join(str(v[k]) for k in ("ref", "by") if v.get(k)), str(v.get("note") or ""))
                     if t)
    return (f"verified wrong by {where}" + (f" on {v['date']}" if v.get("date") else "")
            + (f" ({said})" if said else ""))


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
    the correction gives them, red against a verified correction or when the numbers do not decide the row, and
    yellow against a fitted one otherwise; reversed red: a polar two-terminal part the other way round than a
    correction that is not red puts it (its two terminals exchanged, whether the row follows the numbers or not);
    renumbered (harmless) green, or the correction's verdict when the correction is what accepts it. Against a red
    correction the pad numbers must decide the row: red when fewer than EVIDENCE of the EasyEDA pads whose number
    KiCad has land on it (nothing vouches for the placement), and red for a polar two-terminal part whose pads land
    otherwise than the correction puts them (its polarity needs a decision). A row that lands the pads as a fitted
    correction does whose choice of placement is uncertain (`corr.doubt`) is yellow. KiCad soldered pads no EasyEDA
    pad lands on: yellow (`uncovered`; vias and stubs of a covered pin excepted). not-simulated (no EasyEDA
    footprint): yellow for a non-polar two-pad part whose row point is on it, red otherwise. A row that matches the
    expected row of a verified correction and has no issue is VERIFIED. A row more than ROW_TOL / ANGLE_TOL from the
    expected row gets a message; more than OFFSET_FLOOR mm and OFFSET_PAD of the smallest pad it lands on, or
    OFFSET_ANGLE degrees, from that of a correction that is not red, it is yellow ("offset"). With every pad on copper,
    pin names that the row permutes beyond what any known placement fixes are red ("pinout"), and other name
    differences yellow (_pinout). A correction verified wrong (`corr.failure`): its row (within the offset tolerances)
    is red, status "verified-wrong", saying by what, when and what to do; a row away from it is yellow."""
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
                f"; the row point is {far:.2f} mm off the part's pads" if far is not None else
                "; trimmer: which pad is the rotor (its two pads, and the row point is on them): look at it in JLC's "
                "preview" if trimmer(part) else "; a two-pad non-polar part, and the row point is on it"))
        else:
            res.verdict = RED
            res.messages.insert(0, f"placement not simulated: {why}; {_not_harmless(part)}"
                                   + _row_point(part, row, origin))
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
    # a polar two-terminal part turned the other way round than a correction that is not red puts it: its polarity
    # reversed, whether the row follows the pad numbers or not
    flipped = (polar2 and want is not None and None not in want and not off and not as_corr
               and corr.verdict != RED and _swap_of(want, got))

    def reversed_msg() -> str:
        idx = sorted((i for i in range(len(lands)) if got[i] != want[i]), key=lambda i: natural_key(
            lands[i].ee_number))
        return (f"polarity reversed: the row puts the part the other way round than the "
                f"{'verified ' if corr.verdict == VERIFIED else ''}correction does"
                + (" (the pads land by number)" if decided else "") + ": "
                + ", ".join(f"{lands[i].ee_number} on {got[i]}, not {want[i]}" for i in idx[:6]))
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
        elif flipped:
            status = "reversed" if status == "ok" else status
            issues.append(RED)
            msgs.append(reversed_msg())
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
        elif flipped:
            # the fit took this polarity from pin names or a drawn + against the numbers, or it was verified: the row
            # that follows the numbers reverses it
            status = "reversed" if status == "ok" else status
            issues.append(RED)
            msgs.append(reversed_msg())
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

    sizes = [pad_size(p) for _, _, p in placed if p is not None]
    tol = max(OFFSET_FLOOR, OFFSET_PAD * min(sizes)) if sizes else OFFSET_FLOOR
    if dev is not None and not off and corr.verdict in (GREEN, YELLOW, VERIFIED) and dev[1] <= 45.0:
        d, da = dev
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
    elif dev is not None and not off and as_corr and not conflicts and corr.verdict in (GREEN, YELLOW, VERIFIED) \
            and 45.0 < dev[1] < 135.0:
        # every pad lands as the correction puts it, a quarter turn away: only the symmetry of the lands lets it
        # (one land: a long pad now lies across it); the landing test cannot tell, the correction can
        issues.append(YELLOW)
        status = "offset" if status == "ok" else status
        moved = next((m for m in res.messages if m.startswith("differs from the row jlcplace writes")), "")
        if moved:
            res.messages.remove(moved)
        msgs.insert(0, f"{moved or 'differs from the row jlcplace writes'}: a quarter turn from where the correction "
                       f"puts the part, though every pad lands as it does (the lands are alike that way): check the "
                       f"part's shape on its lands")

    if not off and not flip_ok:
        issue, msg, red = _pinout(part, ee, placed, corr, as_corr, polar2)
        if red:
            issues.append(RED)
            status = "pinout" if status in ("ok", "renumbered", "offset") else status
            msgs.insert(0, msg)
        elif msg:
            issues += [issue] if issue else []
            msgs.append(msg)

    fail = corr.failure if corr is not None else None
    if fail is not None and dev is not None:
        d, da = dev
        if d <= tol and da <= OFFSET_ANGLE:
            # the row JLC's preview, the board or a person showed wrong: its pads may land on their own numbers all
            # the same (what was wrong is then the footprint's numbering, or a body its pads do not orient)
            issues.append(RED)
            text = (f"{failure_text(fail)}: the row is the stored correction's; fix the correction (`jlcplace "
                    f"confirm` with the presses that put the part right in the preview, or --rot/--dx/--dy) and "
                    f"confirm it again")
            if status in ("ok", "renumbered"):
                status = "verified-wrong"
                msgs.insert(0, text)
            else:
                msgs.append(text)
        else:
            # not the row that was wrong, but nothing verified vouches for it either
            issues.append(YELLOW)
            msgs.append(f"the stored correction was {failure_text(fail)}; this row is {d:.3f} mm and {da:g} deg from "
                        f"its row: look at it in JLC's preview, then record it (`jlcplace confirm --cpl`)")

    if row.layer == BOTTOM and alt_readings:
        _sensitivity(res, part, row, ee, origin, reading, alt_readings, flip_ok)

    res.status = status
    res.verdict = _worst(top, *issues)
    res.messages[:0] = msgs
    return res


def _pinout(part: Part, ee: EEFootprint, placed, corr: Correction | None, as_corr: bool, polar2: bool
            ) -> tuple[str, str, bool]:
    """(issue verdict or "", message, red) for the EasyEDA symbol's pin names through the row against the KiCad pin
    functions of the pads they land on (parts.pin_diffs), read from the record whatever the correction says.
    Red ("pinout"): names permuted (a pin on the net of another pin's function) and no placement known to do better
    by the names: not the correction's, not one of its alternatives, and the part has more than two terminals (a
    two-terminal part's half turn always lands it the other way round: its names are its polarity). A polar
    two-terminal part whose drawn + the row puts on KiCad's positive pad is vouched for (its polarity follows the +).
    Yellow: names that differ otherwise, or that a known placement would put right. Same-net differences are a
    message. A row that lands as a manual or board-verified correction is settled by it (yellow for a manual one);
    JLC's preview shows a polarity but not pin functions, so a preview verification settles only a two-terminal
    part's names."""
    if ee.source == "kicad-proxy" or not ee.pin_names:
        return "", "", False
    names = {norm_number(k): v for k, v in ee.pin_names.items()}
    ee_pins = [pin(e.number, names.get(norm_number(e.number), "")) for e in _ee_pads(ee)]
    pads = part.copper_pads()
    kicad = {id(p): pin(p.number, p.pin_name, _key(p.number, p.net)) for p in pads}
    pairs = [(ee_pins[i], kicad[id(p)]) for i, (_, _, p) in enumerate(placed) if p is not None]
    diffs = pin_diffs(pairs, ee_pins, kicad.values())
    harmful = [d for d in diffs if d.kind != "same net"]
    if not harmful:
        return "", ("EasyEDA pin names on KiCad pads of other functions, on their own nets: " + pinout_text(diffs)
                    if diffs else ""), False

    def score(ps) -> int:
        return sum(compare_names(e.tokens, k.tokens) for e, k in ps)

    def wires(c: Correction) -> bool:
        """The placement library = R(rot) . ee + d lands every EasyEDA pad, permutes no pin names and agrees with
        more of them than the row does."""
        through = []
        for e, ep in zip(_ee_pads(ee), ee_pins):
            p = _land(pads, ee_to_library(c, (e.x, e.y)), e.number)[0]
            if p is None:
                return False
            through.append((ep, kicad[id(p)]))
        return score(through) > score(pairs) and not any(d.kind == "swapped" for d in
                                                         pin_diffs(through, ee_pins, kicad.values()))

    # a placement known to wire the part better: the correction's own, or one of its alternatives; a two-terminal
    # part's half turn always lands its pads the other way round (its names are its polarity)
    better = polar2
    if corr is not None and not better:
        known = ([corr] if not corr.mirror else []) + [
            Correction(rot=a["rot"], dx=a["dx"], dy=a["dy"]) for a in corr.alternatives
            if isinstance(a, dict) and not a.get("mirror") and all(k in a for k in ("rot", "dx", "dy"))]
        better = any(wires(c) for c in known)
    e_pos, k_pos = ee_positive_pad(ee), kicad_positive_pad(part)
    on_pos = [norm_number(k.number) == k_pos[0] for e, k in pairs if norm_number(e.number) == e_pos] if k_pos else []
    if polar2 and on_pos and all(on_pos):
        return "", (f"the pin names differ from the symbol ({pinout_text(harmful)}), but the + drawn on the EasyEDA "
                    f"footprint is on KiCad's positive pad {k_pos[0]} (by {k_pos[1]}): the polarity follows the +"
                    ), False
    swapped = [d for d in harmful if d.kind == "swapped"]
    n = len(harmful)
    agree = sum(1 for e, k in pairs if compare_names(e.tokens, k.tokens) > 0)
    if swapped and not better:
        text = f"pinout differs from the symbol: {pinout_text(swapped)}"
    elif swapped:
        text = (f"through this row the pin names differ from the symbol ({pinout_text(harmful)}); another placement "
                f"puts them on their own functions")
    elif n <= agree:
        # most informative names agree: the libraries spell a few pins otherwise (ALERT / INT), said but not judged
        return "", (f"{n} pin name{'s differ' if n > 1 else ' differs'} from the symbol ({agree} agree): "
                    f"{pinout_text(harmful)}"), False
    else:
        text = (f"{n} pin name{'s differ' if n > 1 else ' differs'} from the symbol: {pinout_text(harmful)}: check the "
                f"LCSC part's pinout against the symbol")
    if as_corr and (corr.status in ("manual", "board-verified") or polar2 and corr.status == "preview-verified"):
        return (YELLOW if corr.status == "manual" else ""), f"{text}: accepted by the {corr.status} correction", False
    if not (swapped and not better):
        return YELLOW, text, False
    text += ": the LCSC part's pins land on the nets of other pins; check the LCSC part's pinout against the symbol"
    if corr is not None and corr.status == "preview-verified":
        text += ("; JLC's preview does not show pin functions, so its verification does not settle this: an assembled "
                 "board that works does (`jlcplace confirm --source board`), or editing the entry")
    return RED, text, True


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
        res.reading_moves[name] = [(a.ee_number, a.kicad_number) for a in moved]
        moved = [f"{a.ee_number}->{a.kicad_number or 'off'}" for a in moved]
        changed.append(f"{name}: " + ", ".join(moved[:6]) + (" ..." if len(moved) > 6 else ""))
    if changed:
        res.convention_sensitive = True
        res.messages.append("bottom reading matters, check it in JLC's preview: " + "; ".join(changed))


# ------------------------------------------------------------------------------------------------------- whole CPL

def check_cpl(board: Board, rows: list[CplRow], ee_lookup, *, corrections=None, reading: str = DEFAULT,
              bom_refs=None) -> list[RowResult]:
    """One RowResult per CPL row (in order), then one per part JLC places that has an LCSC code and no row
    ("no-row", red). Rows for refs not on the board, for DNP or board-only parts, on the wrong layer, or repeated are
    red ("not-on-board", "not-placed", "layer-mismatch", "duplicate"). JLC never sees KiCad's attributes and places
    what its BOM and CPL list (cpl.jlc_places): a row for a part the board excludes from its position files or its
    BOM is simulated like any other when the BOM lists it (`bom_refs`, the designators a JLC BOM lists) or, with no
    BOM given, when the board only excludes it from its position files (the CPL lists it); an exclusion from the
    position files is then yellow at least, a board inconsistency ("the BOM and the CPL list it: JLC places it").
    Such a row that nothing else lists is yellow ("not-placed": JLC places only what its BOM lists), and a part the
    BOM lists with no row is "no-row" like any other. `ee_lookup(code)` returns an EEFootprint, NotFound or None; an
    exception from it counts as no footprint. `corrections` is keyed by reference or code. A reference that more than
    one part JLC places carries ("duplicate-ref", red: JLC cannot tell them apart) is never simulated: every row of
    it gets that result, and a reference with no row gets it once."""
    count = Counter(r.ref for r in rows)
    cache: dict[str, object] = {}
    placed: dict[str, list[Part]] = {}           # the parts JLC places, by reference
    for p in board.parts:
        if jlc_places(p, bom_refs, count):
            placed.setdefault(p.ref, []).append(p)
    shared = {ref: ps for ref, ps in placed.items() if len(ps) > 1}

    def ambiguous(ref: str, row: CplRow | None) -> RowResult:
        ps = shared[ref]
        msg = f"{ref} is on the board {len(ps)} times: JLC cannot tell them apart"
        return RowResult(ref, ps[0].lcsc, status="duplicate-ref", verdict=RED, row=row,
                         messages=[msg + (f"; {count[ref]} rows for {ref}" if count[ref] > 1 else "")])

    def lookup(code: str):
        if code not in cache:
            try:
                cache[code] = ee_lookup(code)
            except Exception as e:  # noqa: BLE001 - the lookup does I/O (cache, network); the row says why
                cache[code] = NotFound(code, f"lookup failed ({type(e).__name__}: {e})")
        return cache[code]

    out = []
    for row in rows:
        if row.ref in shared:
            out.append(ambiguous(row.ref, row))
            continue
        part = placed[row.ref][0] if row.ref in placed else board.part(row.ref)
        if part is None:
            out.append(RowResult(row.ref, status="not-on-board", verdict=RED, row=row,
                                 messages=[f"{row.ref} is not on the board"]))
            continue
        listed = not assembled(part) and jlc_places(part, bom_refs, count)
        if not assembled(part) and not listed:
            flags = [n for n, f in (("DNP", part.dnp), ("excluded from position files", part.exclude_pos),
                                    ("board-only", part.board_only), ("excluded from the BOM", part.exclude_bom)) if f]
            must = part.dnp or part.board_only
            why = (": JLC must not place it" if must or bom_refs is None else
                   " and the BOM does not list it: JLC does not place it")
            out.append(RowResult(row.ref, part.lcsc, status="not-placed", verdict=RED if must else YELLOW, row=row,
                                 messages=[f"the board part is {', '.join(flags)}{why}"]))
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
            listed_note(res, part, bom_refs)
        if count[row.ref] > 1:
            res.status, res.verdict = "duplicate", RED
            res.messages.insert(0, f"{count[row.ref]} rows for {row.ref}")
        out.append(res)
    reported = set()
    for part in sorted(board.parts, key=lambda p: natural_key(p.ref)):
        if jlc_places(part, bom_refs) and part.lcsc and part.ref not in count:
            if part.ref in shared:
                if part.ref not in reported:
                    reported.add(part.ref)
                    out.append(ambiguous(part.ref, None))
                continue
            why = "it has an LCSC code" if assembled(part) else "the BOM lists it"
            out.append(RowResult(part.ref, part.lcsc, status="no-row", verdict=RED,
                                 messages=[f"JLC assembles it ({why}) but the CPL has no row"
                                           + (" (the board excludes it from the position files, so KiCad's position "
                                              "file leaves it out)" if part.exclude_pos else "")]))
    return out


def listed_note(res: RowResult, part: Part, bom_refs=None) -> None:
    """What a row JLC places though the board excludes its part says (check_cpl, and pipeline's proxy simulation): an
    exclusion from the position files is a board inconsistency, yellow at least (first when the row is green);
    `bom_refs` None: no BOM was given."""
    if part.exclude_pos:
        # KiCad's attribute keeps the part out of KiCad's own position file, not off JLC's machine
        lists = "the BOM and the CPL list it" if bom_refs is not None else "the CPL lists it"
        res.messages.insert(0 if res.verdict in (GREEN, VERIFIED) else len(res.messages),
                            f"the board excludes it from the position files, but {lists}: JLC places it (it never sees "
                            f"KiCad's attributes); untick 'Exclude from position files' if it is to be assembled, else "
                            f"drop its row and BOM line")
        res.verdict = _worst(res.verdict, YELLOW)
    else:
        res.messages.append("the board excludes it from the BOM, but the BOM lists it: JLC will place it")


def jlc_part(board: Board, ref: str) -> Part | None:
    """The part JLC places for a reference: the first one assembled, else the first placed, else the first (a board
    can carry a reference twice, a DNP alternate footprint for one; Board.part gives the first)."""
    ps = [p for p in board.parts if p.ref == ref]
    return next((p for p in ps if assembled(p)), next((p for p in ps if p.placed), ps[0] if ps else None))


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
            part = jlc_part(board, d)
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
