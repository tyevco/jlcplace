"""fit: the number and geometric fits and the verdict rules.

Synthetic EasyEDA footprints are made from KiCad pads (hand-built, and library footprints through pcbnew) by a known
correction: ee = M . R(-alpha) . (library - d), so that library = R(alpha) . M . ee + d, and the fit must give alpha
and d back. On real data the dial-me-spinner parts must reproduce the cross-checked jlc_offsets.json, and the
z2amiller fixtures' JLC-preview ground truth must never be contradicted by a green verdict.
"""
import csv
import json
import math
import os
from pathlib import Path

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from jlcplace import convention as cv
from jlcplace import fit, geom
from jlcplace.model import BOTTOM, GREEN, RED, TOP, YELLOW, EEFootprint, EEPad, Pad, Part

SCRATCH = Path(os.environ.get("JLCPLACE_TEST_EASYEDA", "/tmp/claude-0/-home-user-dial-me-spinner/"
                              "3708159a-bc3b-5167-8507-f5b08e1ce6c9/scratchpad/jlc"))
EE_RAW = SCRATCH / "ee_raw"
Z_FIX = SCRATCH / "src/z2amiller_jlccheck/tests/fixtures/jlcfootprint"
DIAL = Path(os.environ.get("JLCPLACE_DIAL_ME_SPINNER", "/home/user/dial-me-spinner"))
OFFSETS = DIAL / "hardware/design/jlc_offsets.json"
BOARDS = [DIAL / f"hardware/kicad/{s}/dial-me-spinner-{s}.kicad_pcb" for s in ("main", "base")]
FP_DIR = Path(os.environ.get("KICAD_FOOTPRINT_DIR", "/usr/share/kicad/footprints"))

EE_SHAPE = {"rect": "rect", "roundrect": "rect", "chamfered": "rect", "trapezoid": "rect", "oval": "oval",
            "circle": "ellipse"}


# ------------------------------------------------------------------------------------------------------- builders

def P(number, x, y, w=0.6, h=0.6, angle=0.0, shape="rect", **kw):
    return Pad(number=number, x=x, y=y, w=w, h=h, angle=angle, shape=shape, **kw)


def kpart(pads, ref="U1", footprint="", nets=True, layer=TOP):
    """A part at the board origin; by default every numbered pad gets the net of its number."""
    if nets:
        for p in pads:
            if p.number and not p.net:
                p.net = f"N_{p.number}"
    return Part(ref=ref, layer=layer, x=0.0, y=0.0, angle=0.0, footprint=footprint, pads=pads)


def to_ee(pads, alpha=0.0, d=(0.0, 0.0), mirror=False, *, numbers=None, names=None, drop=(), extra=(),
          title="TEST"):
    """The EasyEDA footprint whose pads are the KiCad copper pads moved by the inverse of the correction.
    `numbers` renumbers the copper pads (in order), `drop` leaves some out, `extra` (ee frame) adds pads."""

    def inv(q):
        r = cv.rot((q[0] - d[0], q[1] - d[1]), -alpha)
        return (-r[0], r[1]) if mirror else r

    out = []
    for k, p in enumerate(q for q in pads if q.copper):
        if k in drop:
            continue
        x, y = inv((p.x, p.y))
        poly = [inv(v) for v in p.poly] if p.poly else None
        # M . R(phi - alpha) applied to a shape symmetric under M is that shape turned by alpha - phi
        a = alpha - p.angle if mirror else p.angle - alpha
        out.append(EEPad(number=numbers[k] if numbers is not None else p.number, x=x, y=y, w=p.w, h=p.h,
                         angle=cv.norm_angle(a), shape="polygon" if poly else EE_SHAPE.get(p.shape, "rect"),
                         layer="multi" if p.kind == "tht" else TOP, poly=poly))
    return EEFootprint(lcsc="C1", title=title, uuid="test", utime=1, pads=out + list(extra),
                       pin_names=dict(names or {}))


def adiff(a, b):
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def assert_recovers(c, alpha, d, mirror=False):
    assert c.mirror == mirror, (c.mirror, c.reasons)
    assert adiff(c.rot, alpha) < 0.01, (c.rot, alpha, c.reasons)
    assert abs(c.dx - d[0]) < 1e-3 and abs(c.dy - d[1]) < 1e-3, ((c.dx, c.dy), d, c.reasons)


def has(c, text):
    return any(text in r for r in c.reasons)


# hand-built footprints (library frame), dimensions as KiCad's

def r0402():
    return [P("1", -0.51, 0, 0.54, 0.64, shape="roundrect", rr=0.25),
            P("2", 0.51, 0, 0.54, 0.64, shape="roundrect", rr=0.25)]


def sot23_6():
    xs, ys = (-1.1375, 1.1375), (-0.95, 0.0, 0.95)
    pads = [P(str(i + 1), xs[0], ys[i], 1.325, 0.6) for i in range(3)]
    return pads + [P(str(i + 4), xs[1], ys[2 - i], 1.325, 0.6) for i in range(3)]


def soic8():
    ys = (-1.905, -0.635, 0.635, 1.905)
    return ([P(str(i + 1), -2.475, ys[i], 1.95, 0.6) for i in range(4)]
            + [P(str(i + 5), 2.475, ys[3 - i], 1.95, 0.6) for i in range(4)])


def qfn16_ep():
    pads, k = [], 1
    for side in range(4):                      # counter-clockwise from the top of the left side, as KiCad numbers
        for i in range(4):
            t = -0.75 + 0.5 * i
            x, y, w, h = {0: (-1.45, t, 0.8, 0.25), 1: (t, 1.45, 0.25, 0.8), 2: (1.45, -t, 0.8, 0.25),
                          3: (-t, -1.45, 0.25, 0.8)}[side]
            pads.append(P(str(k), x, y, w, h))
            k += 1
    return pads + [P("17", 0, 0, 1.7, 1.7)]


def switch_1122():
    return [P("1", -3.0, -1.875, 1.0, 0.75), P("1", 3.0, -1.875, 1.0, 0.75),
            P("2", -3.0, 1.875, 1.0, 0.75), P("2", 3.0, 1.875, 1.0, 0.75)]


def jst_sh4():
    return ([P(str(i + 1), -1.5 + i, -2.0, 0.6, 1.55) for i in range(4)]
            + [P("MP", -2.8, 1.875, 1.2, 1.8), P("MP", 2.8, 1.875, 1.2, 1.8)])


def usb_c():
    """HRO TYPE-C-31-M-12: 16 SMD pads, A1/B12, A4/B9, A9/B4, A12/B1 drawn on top of each other; 4 shield THT."""
    row = [("A1", -3.25, 0.6), ("B12", -3.25, 0.6), ("A4", -2.45, 0.6), ("B9", -2.45, 0.6), ("B8", -1.75, 0.3),
           ("A5", -1.25, 0.3), ("B7", -0.75, 0.3), ("A6", -0.25, 0.3), ("A7", 0.25, 0.3), ("B6", 0.75, 0.3),
           ("A8", 1.25, 0.3), ("B5", 1.75, 0.3), ("A9", 2.45, 0.6), ("B4", 2.45, 0.6), ("A12", 3.25, 0.6),
           ("B1", 3.25, 0.6)]
    nets = {"A1": "GND", "B12": "GND", "A12": "GND", "B1": "GND", "A4": "VBUS", "B9": "VBUS", "A9": "VBUS",
            "B4": "VBUS", "A6": "DP", "B6": "DP", "A7": "DM", "B7": "DM"}
    pads = [P(n, x, -4.045, w, 1.45, net=nets.get(n, "")) for n, x, w in row]
    return pads + [P("S1", sx, sy, 1.0, h, shape="oval", kind="tht", net="GND")
                   for sx in (-4.32, 4.32) for sy, h in ((-3.13, 2.1), (1.05, 1.6))]


def grid(rows, cols, pitch=0.5, missing=(), d=0.25):
    pads = []
    for r in range(rows):
        for c in range(cols):
            if (r, c) not in missing:
                pads.append(P(f"{'ABCDEFGH'[r]}{c + 1}", (c - (cols - 1) / 2) * pitch, (r - (rows - 1) / 2) * pitch,
                              d, d, shape="circle"))
    return pads


# ------------------------------------------------------------------------------------------------------- names

def test_name_tokens_and_comparison():
    t = fit.name_tokens
    assert t("~{RESET}") == t("/RESET") == t("RESET#") == t("nRESET") == {"RESET"}
    assert t("P0.18/nRESET") == {"P0.18", "RESET"} and t("GND(TEST)") == {"GND", "TEST"}
    assert t("EP") == t("PAD") == t("THERMAL") == {"GND"}
    assert t("DP") == {"D+"} and t("DM") == {"D-"} and t("EH") == t("SHIELD") == {"SHELL"}
    assert t("K") == t("C") == t("-") == {"CATHODE"} and t("A") == t("+") == {"ANODE"}
    assert t("1", "1") == t("~") == t("") == t("PIN3") == t("NC") == frozenset()
    assert t("A1", "A1") == frozenset() and t("A1", "1") == {"A1"}
    c = fit.compare_names
    assert c(t("GND"), t("EP")) == 1 and c(t("VDD"), t("GND")) == -1
    assert c(t("CC1"), t("CC2")) == 0 and c(t("DP1"), t("D+")) == 0      # an index apart: unknown, not a mismatch
    assert c(t("SDA/SDI/SDO"), t("SDA")) == 1 and c(t(""), t("GND")) == 0
    assert fit.norm_number(" 01 ") == "1" and fit.norm_number("a1") == "A1" and fit.norm_number("0") == "0"


# ------------------------------------------------------------------------------------------------------- two pads

@pytest.mark.parametrize("alpha", [0, 90, 180, 270, 37])
def test_resistor_any_rotation(alpha):
    pads = r0402()
    d = (0.123, -0.456)
    c = fit.fit_part(kpart(pads, "R1", "Resistor_SMD:R_0402_1005Metric"), to_ee(pads, alpha, d))
    assert_recovers(c, alpha, d)
    assert c.verdict == (GREEN if alpha % 90 == 0 else YELLOW), c.reasons
    assert c.method == "number" and c.matched == 2 and c.rms < 1e-9
    # the terminal swap lands too, and is harmless for a resistor
    swap = [a for a in c.alternatives if not a["mirror"] and adiff(a["rot"], alpha + 180) < 0.01]
    assert swap and swap[0]["equivalent"] and swap[0]["nets_changed"] == ["1", "2"]


def test_diode_polarity_needs_a_second_source():
    pads = r0402()
    part = kpart(pads, "D1", "Diode_SMD:D_SOD-123")
    c = fit.fit_part(part, to_ee(pads, 90, (0.2, 0.1)))
    assert_recovers(c, 90, (0.2, 0.1))
    assert c.verdict == YELLOW and has(c, "polarity decided by pad numbers only")
    # pin names on both sides that agree with the numbering: green
    pads[0].pin_name, pads[1].pin_name = "K", "A"
    c = fit.fit_part(part, to_ee(pads, 90, (0.2, 0.1), names={"1": "K", "2": "A"}))
    assert c.verdict == GREEN and has(c, "ruled out by pad numbers and pin names")
    # EasyEDA numbers the anode 1: the names win, and it is not green
    c = fit.fit_part(part, to_ee(pads, 90, (0.2, 0.1), numbers=["2", "1"], names={"2": "K", "1": "A"}))
    assert_recovers(c, 90, (0.2, 0.1))
    assert c.verdict == YELLOW and has(c, "following the names")


def test_two_pads_without_common_numbers():
    pads = r0402()
    # a resistor: both ways round are the same part
    c = fit.fit_part(kpart(pads, "R7"), to_ee(pads, 0, (0, 0), numbers=["A", "B"]))
    assert c.verdict == YELLOW and has(c, "do not overlap") and adiff(c.rot, 0) < 0.01
    # a diode: nothing says which way round
    c = fit.fit_part(kpart(r0402(), "D7"), to_ee(pads, 0, (0, 0), numbers=["A", "B"]))
    assert c.verdict == RED and has(c, "ambiguous")


# ------------------------------------------------------------------------------------------------------- symmetry

def test_symmetric_package_only_numbers_tell():
    pads = sot23_6()
    d = (0.05, -0.3)
    c = fit.fit_part(kpart(pads), to_ee(pads, 180, d))
    assert_recovers(c, 180, d)
    assert c.verdict == GREEN and has(c, "ruled out by pad numbers")
    other = [a for a in c.alternatives if not a["mirror"] and adiff(a["rot"], 0) < 0.01]
    assert other and not other[0]["equivalent"] and other[0]["matched"] == 0
    # numbering that does not overlap leaves 0 and 180 unseparated
    letters = list("ABCDEF")
    c = fit.fit_part(kpart(sot23_6()), to_ee(pads, 180, d, numbers=letters))
    assert c.verdict == RED and has(c, "ambiguous")
    # ... unless the pin names pick one
    names = ["VIN", "GND", "EN", "NC", "FB", "SW"]
    pads = sot23_6()
    for p, n in zip(pads, names):
        p.pin_name = n
    c = fit.fit_part(kpart(pads), to_ee(pads, 180, d, numbers=letters, names=dict(zip(letters, names))))
    assert_recovers(c, 180, d)
    assert c.verdict == YELLOW and has(c, "pin names pick")


def test_net_equivalent_alternatives_do_not_count():
    pads = sot23_6()
    for p in pads:                              # pins 180 degrees apart share nets: either way round is the same
        p.net = {"1": "A", "4": "A", "2": "B", "5": "B", "3": "C", "6": "C"}[p.number]
    c = fit.fit_part(kpart(pads), to_ee(pads, 0, (0, 0), numbers=list("ABCDEF")))
    assert c.verdict == YELLOW and has(c, "electrically equivalent")


# ------------------------------------------------------------------------------------------------------- numbering

def test_switch_numbered_1122_against_1234():
    pads = switch_1122()
    part = kpart(pads, "SW1", "Button_Switch_SMD:SW_Push_1P1T_XKB_TS-1187A")
    ee = to_ee(pads, 0, (0, 0.0125), numbers=["1", "2", "3", "4"])
    c = fit.fit_part(part, ee)
    # EE 2 lands on KiCad 1 while KiCad 2 is another net: only the switch's internal pairing makes that right
    assert c.verdict == RED and has(c, "numbering conflict") and c.method == "geometry"
    assert_recovers(c, 0, (0, 0.0125))
    # pin names that pair the pins pick one placement: yellow, as the contract's example
    for p in pads:
        p.pin_name = "IN" if p.number == "1" else "OUT"
    names = {"1": "IN", "2": "IN", "3": "OUT", "4": "OUT"}
    for alpha in (0, 180):
        c = fit.fit_part(part, to_ee(pads, alpha, (0, 0.0125), numbers=["1", "2", "3", "4"], names=names))
        assert_recovers(c, alpha, (0, 0.0125))
        assert c.verdict == YELLOW and has(c, "pin names pick")
    # numbered the same on both sides it is an ordinary fit (repeated numbers paired by position)
    c = fit.fit_part(part, to_ee(pads, 90, (0.4, 0.2)))
    assert_recovers(c, 90, (0.4, 0.2))
    assert c.verdict == GREEN and c.matched == 4


@pytest.mark.parametrize("alpha", [0, 90, 37])
def test_repeated_numbers_paired_by_position(alpha):
    pads = jst_sh4()
    d = (-0.3, 0.71)
    c = fit.fit_part(kpart(pads, "J1"), to_ee(pads, alpha, d))
    assert_recovers(c, alpha, d)
    assert c.matched == 6 and c.verdict == (GREEN if alpha % 90 == 0 else YELLOW)
    # EasyEDA numbers the mounting pads 5, 6: still exact from pins 1-4, but only 4/6 matched by number
    c = fit.fit_part(kpart(jst_sh4(), "J1"), to_ee(pads, alpha, d, numbers=["1", "2", "3", "4", "5", "6"]))
    assert_recovers(c, alpha, d)
    assert c.verdict == YELLOW and has(c, "only 4 of 6")


def test_number_repeated_on_one_side_only_is_left_out():
    """SOT-223 with KiCad's pin 2 and tab both '2', EasyEDA's tab '4': the centroid of KiCad's 2 is not a pad."""
    pads = [P("1", -2.3, 3.15, 0.95, 2.0), P("2", 0, 3.15, 0.95, 2.0), P("3", 2.3, 3.15, 0.95, 2.0),
            P("2", 0, -3.15, 3.25, 2.0)]
    d = (0.0, -0.18)
    c = fit.fit_part(kpart(pads), to_ee(pads, 180, d, numbers=["1", "2", "3", "4"]))
    assert_recovers(c, 180, d)
    assert c.verdict == YELLOW and c.matched == 3 and not has(c, "conflict")


def test_numbers_repeated_symmetrically():
    """A battery holder numbered 1, 2, 2: no pair fit exists. Symmetric, the pads cannot orient the body; asymmetric,
    the one placement that puts every pad on its own number is the number fit."""
    sym = [P("1", 0, 0, 3.0, 3.0), P("2", -6.0, 0, 2.0, 3.0), P("2", 6.0, 0, 2.0, 3.0)]
    c = fit.fit_part(kpart(sym, "BT1"), to_ee(sym, 90, (0.5, 0.25)))
    assert c.verdict == YELLOW and has(c, "do not fix the body's orientation")
    assert adiff(c.rot, 90) < 0.01 or adiff(c.rot, 270) < 0.01
    asym = [P("1", 0, 0, 1.5, 1.5), P("2", -6.0, 0, 1.5, 2.0), P("2", 5.0, 2.5, 1.5, 2.0)]
    c = fit.fit_part(kpart(asym, "BT1"), to_ee(asym, 90, (0.5, 0.25)))
    assert_recovers(c, 90, (0.5, 0.25))
    assert c.verdict == GREEN and c.method == "number"
    # one pad fixes no rotation, however well it matches
    one = [P("1", 0, 0, 3.0, 2.0)]
    c = fit.fit_part(kpart(one, "BT1"), to_ee(one, 90, (0.5, 0.25)))
    assert c.verdict == YELLOW and has(c, "does not fix the rotation")


def test_usb_c_coincident_pads_and_joined_numbers():
    pads = usb_c()
    d = (0.0, -1.571)
    numbers = []
    for p in pads:                            # EasyEDA draws each doubled land once, as "A1B12"; shields are 1-4
        numbers.append(p.number)
    joined = {"A1": "A1B12", "A4": "A4B9", "A9": "A9B4", "A12": "A12B1"}
    shields = iter("1234")
    numbers = [joined.get(n, next(shields) if n == "S1" else n) for n in numbers]
    keep = [k for k, n in enumerate(numbers) if n not in ("B12", "B9", "B4", "B1")]
    drop = [k for k in range(len(pads)) if k not in keep]
    ee = to_ee(pads, 270, d, numbers=numbers, drop=drop)
    assert len(ee.pads) == 16
    c = fit.fit_part(kpart(pads, "J1"), ee)
    assert_recovers(c, 270, d)
    assert c.matched == 12 and c.verdict == YELLOW and has(c, "only 12 of 16")
    land = fit.ee_landings(kpart(usb_c(), "J1"), ee, c)
    for e, x in zip(ee.pads, land):
        assert x.kicad_number is not None
        if e.number[0] in "AB":
            assert x.kicad_number in e.number and x.dist < 1e-6
    # drawn the KiCad way (every pad, coincident duplicates too) it matches completely
    c = fit.fit_part(kpart(usb_c(), "J1"), to_ee(usb_c(), 90, (0.2, 0.3)))
    assert_recovers(c, 90, (0.2, 0.3))
    assert c.verdict == GREEN and c.matched == 20
    land = fit.ee_landings(kpart(usb_c(), "J1"), to_ee(usb_c(), 90, (0.2, 0.3)), c)
    assert [x.kicad_number for x in land] == [p.number for p in usb_c()]     # A1 on A1, B12 on B12


def test_letters_against_digits():
    # a pattern with one way to land: placed by geometry, yellow
    pads = grid(3, 2, missing={(1, 1)})
    d = (0.11, 0.07)
    c = fit.fit_part(kpart(pads), to_ee(pads, 90, d, numbers=[str(i + 1) for i in range(5)]))
    assert_recovers(c, 90, d)
    assert c.verdict == YELLOW and has(c, "do not overlap") and c.method == "geometry"
    # a full grid lands four ways round
    pads = grid(3, 3)
    c = fit.fit_part(kpart(pads), to_ee(pads, 0, d, numbers=[str(i + 1) for i in range(9)]))
    assert c.verdict == RED and has(c, "ambiguous")
    assert sum(not a["mirror"] for a in c.alternatives) >= 3


# ------------------------------------------------------------------------------------------------------- missing, extra

def test_extra_easyeda_tab_is_red_but_the_fit_holds():
    pads = sot23_6()
    d = (0.3, 0.2)
    tab = EEPad("7", 0.0, 3.0, 2.0, 1.0)
    c = fit.fit_part(kpart(pads), to_ee(pads, 90, d, extra=[tab]))
    assert c.verdict == RED and has(c, "1 of 7 are off copper: 7")
    assert_recovers(c, 90, d)
    # without numbers in common the best partial geometric fit is still the right one
    c = fit.fit_part(kpart(sot23_6()), to_ee(pads, 90, d, numbers=list("ABCDEF"), extra=[tab]))
    assert c.verdict == RED and c.method == "geometry"
    assert adiff(c.rot, 90) < 0.01 or adiff(c.rot, 270) < 0.01


def test_missing_easyeda_pad_leaves_a_kicad_pad_uncovered():
    pads = qfn16_ep()
    d = (-0.2, 0.15)
    c = fit.fit_part(kpart(pads), to_ee(pads, 270, d, drop=[16]))
    assert_recovers(c, 270, d)
    assert c.verdict == YELLOW and has(c, "KiCad pads no EasyEDA pad lands on: 17")
    c = fit.fit_part(kpart(qfn16_ep()), to_ee(pads, 270, d))
    assert c.verdict == GREEN and c.matched == 17


def test_paste_only_and_far_side_pads_are_ignored():
    pads = qfn16_ep()
    ee = to_ee(pads, 180, (0.1, 0.1))
    plain = fit.fit_part(kpart(pads), ee)
    extra = qfn16_ep() + [P("", x, y, 0.6, 0.6, copper=False) for x in (-0.4, 0.4) for y in (-0.4, 0.4)]
    extra += [P("17", 3.0, 3.0, 1.0, 1.0, copper=False)]          # e.g. a pad on the far side
    other = fit.fit_part(kpart(extra), ee)
    assert (other.rot, other.dx, other.dy, other.verdict) == (plain.rot, plain.dx, plain.dy, plain.verdict) == \
        (180.0, 0.1, 0.1, GREEN)
    assert other.kicad_hash == plain.kicad_hash


def test_no_pads():
    c = fit.fit_part(kpart(sot23_6()), EEFootprint("C1", "EMPTY"))
    assert c.verdict == RED and has(c, "no pads")
    c = fit.fit_part(kpart([P("1", 0, 0, copper=False)]), to_ee(sot23_6()))
    assert c.verdict == RED and has(c, "no soldered copper")


# ------------------------------------------------------------------------------------------------------- mirror, snap

@pytest.mark.parametrize("make,alpha", [(soic8, 0), (soic8, 90), (sot23_6, 180), (qfn16_ep, 37)])
def test_mirrored_footprint_is_red(make, alpha):
    pads = make()
    d = (0.25, -0.5)
    c = fit.fit_part(kpart(pads), to_ee(pads, alpha, d, mirror=True))
    assert c.verdict == RED and c.mirror and has(c, "mirror")
    assert_recovers(c, alpha, d, mirror=True)
    with pytest.raises(ValueError):
        cv.cpl_row_for(kpart(make()), c, (0, 0))


def test_symmetric_pattern_is_not_a_mirror():
    for pads in (r0402(), jst_sh4()):
        c = fit.fit_part(kpart(pads, "R1"), to_ee(pads, 90, (0.1, 0.0)))
        assert not c.mirror and c.verdict == GREEN


def test_snap():
    pads = soic8()
    ee = to_ee(pads, 90.3, (0.2, -0.1))
    c = fit.fit_part(kpart(pads), ee)
    assert c.rot == 90.0 and c.verdict == GREEN
    # the translation is refitted for the snapped angle: the mean over the pads, i.e. where the centroids meet
    src = [(e.x, e.y) for e in ee.pads]
    k = geom.centroid([(p.x, p.y) for p in pads])
    e = cv.rot(geom.centroid(src), 90.0)
    assert abs(c.dx - (k[0] - e[0])) < 1e-4 and abs(c.dy - (k[1] - e[1])) < 1e-4
    c = fit.fit_part(kpart(pads), ee, snap=0.1)
    assert_recovers(c, 90.3, (0.2, -0.1))
    assert c.verdict == YELLOW and has(c, "not a multiple of 90")


# ------------------------------------------------------------------------------------------------------- landings

def test_landings_and_net_map():
    pads = sot23_6()
    part = kpart(pads)
    ee = to_ee(pads, 90, (0.5, 0.5), extra=[EEPad("7", 0.0, 4.0, 0.5, 0.5)])
    c = fit.fit_part(part, ee)
    land = fit.ee_landings(part, ee, c)
    assert [x.kicad_number for x in land] == ["1", "2", "3", "4", "5", "6", None]
    assert all(x.dist < 1e-6 for x in land[:6]) and land[6].dist > 2.0
    assert [x.net for x in land[:6]] == [f"N_{i}" for i in range(1, 7)]
    nm = fit.net_map(part, ee, c)
    assert nm == {0: "N_1", 1: "N_2", 2: "N_3", 3: "N_4", 4: "N_5", 5: "N_6", 6: None}


def test_bottom_side_is_the_same_fit():
    """The fit lives in the library frame: the side the part is on does not enter."""
    pads = soic8()
    top = fit.fit_part(kpart(pads), to_ee(pads, 270, (0.1, 0.2)))
    bot = fit.fit_part(kpart(soic8(), layer=BOTTOM), to_ee(pads, 270, (0.1, 0.2)))
    assert (top.rot, top.dx, top.dy, top.verdict) == (bot.rot, bot.dx, bot.dy, bot.verdict)


# ------------------------------------------------------------------------------------------------------- properties

@st.composite
def pad_sets(draw):
    n = draw(st.integers(3, 10))
    cells = draw(st.lists(st.tuples(st.integers(0, 9), st.integers(0, 9)), min_size=n, max_size=n, unique=True))
    pads = []
    for k, (i, j) in enumerate(cells):
        w, h = draw(st.floats(0.25, 0.6)), draw(st.floats(0.25, 0.6))
        pads.append(P(str(k + 1), 0.8 * i - 3.6, 0.8 * j - 3.6, w, h, angle=draw(st.sampled_from([0.0, 90.0]))))
    return pads


@settings(derandomize=True, max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(pads=pad_sets(), alpha=st.one_of(st.sampled_from([0.0, 90.0, 180.0, 270.0]), st.floats(0, 359.9)),
       dx=st.floats(-3, 3), dy=st.floats(-3, 3), mirror=st.booleans(), seed=st.integers(0, 2 ** 16))
def test_property_recovers_any_transform(pads, alpha, dx, dy, mirror, seed):
    pts = [(p.x, p.y) for p in pads]
    area = max(abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))
               for a in pts for b in pts for c in pts)
    assume(area > 0.1)                         # a mirror image of collinear pads is the pads themselves
    assume(abs(abs(alpha - 90 * round(alpha / 90)) - 0.5) > 1e-6)   # exactly at the snap limit float noise decides
    snapped = fit._snap_angle(alpha, 0.5)
    ee = to_ee(pads, alpha, (dx, dy), mirror)
    import random
    rnd = random.Random(seed)
    rnd.shuffle(ee.pads)
    kp = list(pads)
    rnd.shuffle(kp)
    c = fit.fit_part(kpart(kp), ee)
    if snapped % 90 == 0 and adiff(snapped, alpha) > 1e-9:
        alpha = snapped                       # within snap of a multiple of 90: snapped (translation refitted)
        assert c.mirror == mirror and adiff(c.rot, alpha) < 1e-6
    else:
        assert_recovers(c, alpha, (dx, dy), mirror)
    if mirror:
        assert c.verdict == RED
    else:
        assert c.verdict == (GREEN if c.rot % 90 == 0 else YELLOW), c.reasons


# ------------------------------------------------------------------------------------------------------- KiCad library

_FP_CACHE: dict = {}


def library_part(lib, name, ref="U1", nets=True):
    pcbnew = pytest.importorskip("pcbnew")
    if (lib, name) not in _FP_CACHE:
        path = FP_DIR / f"{lib}.pretty"
        if not (path / f"{name}.kicad_mod").is_file():
            pytest.skip(f"{lib}:{name} not in {FP_DIR}")
        # one plugin instance caches each parsed library; pcbnew.FootprintLoad re-parses (and leaks) it every call
        io = _FP_CACHE.setdefault("io", pcbnew.PCB_IO_KICAD_SEXPR())
        _FP_CACHE[(lib, name)] = io.FootprintLoad(str(path), name)
    part = part_from_footprint(_FP_CACHE[(lib, name)], pcbnew, ref=ref)
    part.footprint = f"{lib}:{name}"
    if nets:
        for p in part.pads:
            p.net = f"N_{p.number}" if p.number else ""
    return part


def part_from_footprint(fp, pcbnew, ref=None):
    """model.Part from a pcbnew footprint, pads in the library frame. GetFPRelativePosition/Orientation give the
    as-placed local frame, mirrored in y on the bottom: library = (x, -y), angle -a (checked against the board pads
    below, and against HitTest in the scratch exploration)."""
    shapes = {0: "circle", 1: "rect", 2: "oval", 3: "trapezoid", 4: "roundrect", 5: "chamfered", 6: "custom"}
    kinds = {0: "tht", 1: "smd", 2: "connect", 3: "npth"}
    bottom = fp.IsFlipped()
    s = -1.0 if bottom else 1.0
    mm = pcbnew.ToMM
    pos = fp.GetPosition()
    part = Part(ref=ref or fp.GetReference(), layer=BOTTOM if bottom else TOP, x=mm(pos.x), y=mm(pos.y),
                angle=cv.norm_angle(fp.GetOrientationDegrees()), footprint=fp.GetFPIDAsString(), value=fp.GetValue())
    side = pcbnew.B_Cu if bottom else pcbnew.F_Cu
    for p in fp.Pads():
        lay = p.GetPrincipalLayer()
        kind, shape = kinds[p.GetAttribute()], shapes[p.GetShape(lay)]
        rp, sz = p.GetFPRelativePosition(), p.GetSize(lay)
        poly = None
        if shape in ("trapezoid", "chamfered", "custom"):
            o = p.GetEffectivePolygon(lay, pcbnew.ERROR_INSIDE).Outline(0)
            poly = [cv.board_to_library(part, (mm(o.CPoint(i).x), mm(o.CPoint(i).y))) for i in range(o.PointCount())]
        part.pads.append(Pad(number=p.GetNumber(), x=mm(rp.x), y=s * mm(rp.y), w=mm(sz.x), h=mm(sz.y),
                             angle=cv.norm_angle(s * p.GetFPRelativeOrientation().AsDegrees()), shape=shape, kind=kind,
                             copper=kind == "tht" or (kind in ("smd", "connect") and p.IsOnLayer(side)),
                             net=p.GetNetname(), pin_name=p.GetPinFunction(), pin_type=p.GetPinType(),
                             rr=p.GetRoundRectRadiusRatio(lay) if shape == "roundrect" else 0.0, poly=poly))
    return part


# (lib, footprint, ref, polar two-terminal)
LIBRARY_SAMPLE = [
    ("Package_TO_SOT_SMD", "SOT-23-3", "Q1", False),
    ("Package_TO_SOT_SMD", "SOT-23-5", "U1", False),
    ("Package_TO_SOT_SMD", "SOT-23-6", "U1", False),
    ("Package_TO_SOT_SMD", "SOT-223-3_TabPin2", "U1", False),
    ("Package_TO_SOT_SMD", "TO-252-2", "U1", False),
    ("Package_SO", "SOIC-8_3.9x4.9mm_P1.27mm", "U1", False),
    ("Package_SO", "TSSOP-16_4.4x5mm_P0.65mm", "U1", False),
    ("Package_DFN_QFN", "QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm", "U1", False),
    ("Resistor_SMD", "R_0402_1005Metric", "R1", False),
    ("Capacitor_SMD", "C_0603_1608Metric", "C1", False),
    ("Diode_SMD", "D_SOD-123", "D1", True),
    ("Diode_SMD", "D_SMA", "D1", True),
    ("LED_SMD", "LED_0603_1608Metric", "LED1", True),
    ("Connector_USB", "USB_C_Receptacle_HRO_TYPE-C-31-M-12", "J1", False),
    ("Connector_USB", "USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal", "J1", False),
    ("Connector_JST", "JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal", "J1", False),
    ("Button_Switch_SMD", "SW_Push_1P1T_XKB_TS-1187A", "SW1", False),
    ("Button_Switch_SMD", "SW_SPST_TL3342", "SW1", False),
    ("Package_BGA", "BGA-64_9.0x9.0mm_Layout10x10_P0.8mm", "U1", False),
    ("Package_BGA", "Texas_DSBGA-5_0.8875x1.3875mm_Layout2x3_P0.5mm", "U1", False),
    ("Package_DIP", "DIP-8_W7.62mm", "U1", False),
    ("Connector_PinHeader_2.54mm", "PinHeader_1x04_P2.54mm_Vertical", "J1", False),
    ("Connector_PinHeader_2.54mm", "PinHeader_2x05_P2.54mm_Vertical", "J1", False),
    ("Crystal", "Crystal_SMD_3225-4Pin_3.2x2.5mm", "Y1", False),
]


@pytest.mark.pcbnew
@pytest.mark.parametrize("lib,name,ref,polar", LIBRARY_SAMPLE, ids=[s[1] for s in LIBRARY_SAMPLE])
def test_library_footprints(lib, name, ref, polar):
    part = library_part(lib, name, ref)
    assert part.copper_pads()
    for alpha, d in ((0, (0.0, 0.0)), (90, (0.37, -0.81)), (180, (-1.2, 0.4)), (270, (2.5, 1.5)), (37, (0.3, 0.3))):
        c = fit.fit_part(part, to_ee(part.pads, alpha, d))
        assert_recovers(c, alpha, d)
        want = YELLOW if alpha % 90 or polar else GREEN
        assert c.verdict == want, (alpha, c.verdict, c.reasons)
        assert c.matched == len(part.copper_pads()) and c.rms < 1e-6


ASYMMETRIC = ["SOT-23-3", "SOT-23-5", "SOIC-8_3.9x4.9mm_P1.27mm", "TSSOP-16_4.4x5mm_P0.65mm",
              "QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm", "Texas_DSBGA-5_0.8875x1.3875mm_Layout2x3_P0.5mm",
              "DIP-8_W7.62mm", "PinHeader_2x05_P2.54mm_Vertical"]


@pytest.mark.pcbnew
@pytest.mark.parametrize("lib,name,ref,polar", [s for s in LIBRARY_SAMPLE if s[1] in ASYMMETRIC],
                         ids=ASYMMETRIC)
def test_library_footprints_mirrored(lib, name, ref, polar):
    part = library_part(lib, name, ref)
    for alpha in (0, 90, 37):
        c = fit.fit_part(part, to_ee(part.pads, alpha, (0.2, -0.4), mirror=True))
        assert c.verdict == RED and has(c, "mirror"), c.reasons
        assert_recovers(c, alpha, (0.2, -0.4), mirror=True)


@pytest.mark.pcbnew
def test_library_renumbered():
    # KiCad's 1,1,2,2 tactile switch against EasyEDA's 1-4
    sw = library_part("Button_Switch_SMD", "SW_Push_1P1T_XKB_TS-1187A", "SW1")
    c = fit.fit_part(sw, to_ee(sw.pads, 0, (0.0, 0.025), numbers=["1", "2", "3", "4"]))
    assert c.verdict == RED and has(c, "numbering conflict")
    assert_recovers(c, 0, (0.0, 0.025))
    # ball letters against digits: a symmetric array is ambiguous, an asymmetric one is placed by geometry
    bga = library_part("Package_BGA", "BGA-64_9.0x9.0mm_Layout10x10_P0.8mm")
    c = fit.fit_part(bga, to_ee(bga.pads, 90, (0.1, 0.1), numbers=[str(i + 1) for i in range(64)]))
    assert c.verdict == RED and has(c, "ambiguous")
    cs = library_part("Package_BGA", "Texas_DSBGA-5_0.8875x1.3875mm_Layout2x3_P0.5mm")
    c = fit.fit_part(cs, to_ee(cs.pads, 270, (0.05, -0.02), numbers=["1", "2", "3", "4", "5"]))
    assert c.verdict == YELLOW and has(c, "do not overlap")
    assert_recovers(c, 270, (0.05, -0.02))
    # an EasyEDA footprint without the exposed pad, and one with an extra tab
    qfn = library_part("Package_DFN_QFN", "QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm")
    ep = [k for k, p in enumerate(qfn.copper_pads()) if p.number == "33"]
    c = fit.fit_part(qfn, to_ee(qfn.pads, 180, (0.3, 0.3), drop=ep))
    assert c.verdict == YELLOW and has(c, "lands on: 33")
    assert_recovers(c, 180, (0.3, 0.3))
    sot = library_part("Package_TO_SOT_SMD", "SOT-23-3", "Q1")
    c = fit.fit_part(sot, to_ee(sot.pads, 90, (0.3, 0.3), extra=[EEPad("4", 0.0, -3.0, 1.0, 1.0)]))
    assert c.verdict == RED and has(c, "off copper: 4")
    assert_recovers(c, 90, (0.3, 0.3))


# ------------------------------------------------------------------------------------------------------- real data

def parse_record():
    """jlcplace.easyeda.parse_record, written in parallel: skip until it is there."""
    try:
        from jlcplace.easyeda import parse_record as pr
    except Exception as e:  # noqa: BLE001 - missing, half-written or broken: not this module's test
        pytest.skip(f"jlcplace.easyeda.parse_record unavailable: {e}")

    def parse(code, path):
        try:
            return pr(code, json.loads(Path(path).read_text())["result"])
        except NotImplementedError:
            pytest.skip("jlcplace.easyeda.parse_record not implemented yet")

    return parse


def board_parts(pcbnew):
    """(board name, Part, LCSC code) for every part with a code, checking the library frame on every pad."""
    out = []
    for path in BOARDS:
        b = pcbnew.LoadBoard(str(path))
        for fp in b.GetFootprints():
            code = fp.GetFieldText("LCSC") if fp.HasFieldByName("LCSC") else ""
            part = part_from_footprint(fp, pcbnew)
            for p, q in zip(fp.Pads(), part.pads):
                c = cv.library_to_board(part, (q.x, q.y))
                assert math.dist(c, (pcbnew.ToMM(p.GetPosition().x), pcbnew.ToMM(p.GetPosition().y))) < 1e-4
                for u in geom.pad_outline(q, arc_steps=2):     # the outline shrunk to 90 % is on pcbnew's pad
                    v = cv.library_to_board(part, (q.x + 0.9 * (u[0] - q.x), q.y + 0.9 * (u[1] - q.y)))
                    assert p.HitTest(pcbnew.VECTOR2I(pcbnew.FromMM(v[0]), pcbnew.FromMM(v[1]))), (part.ref, q)
            if code:
                out.append((path.stem, part, code))
    return out


# Not green on dial-me-spinner, and why (the corrections themselves all match jlc_offsets.json):
DIAL_NOT_GREEN = {
    "C318884": (RED, "numbering conflict"),            # SW1: KiCad 1,1,2,2 vs EasyEDA 1-4 (manual in jlc_offsets)
    "C165948": (YELLOW, "only 12 of 16"),             # USB-C: EasyEDA's shield pads 1-4 vs KiCad S1
    "C160404": (YELLOW, "only 4 of 6"),               # JST SH 4: mounting pads 5, 6 vs MP
    "C8598": (YELLOW, "polarity decided by pad numbers only"),   # SOD-123: no KiCad pin names on these boards
    "C151296": (YELLOW, "polarity decided by pad numbers only"),
    "C7189": (YELLOW, "polarity decided by pad numbers only"),   # tantalum
    "C910763": (YELLOW, "polarity decided by pad numbers only"),  # buzzer (BZ: not known to be non-polar)
    "C2500": (YELLOW, "rms 0.281"),                   # SOT-23 land patterns differ
    "C2681320": (YELLOW, "rms 0.21"),
    "C3715882": (YELLOW, "rms 0.21"),
    "C189624": (YELLOW, "rms 0.113"),
}


@pytest.mark.pcbnew
@pytest.mark.cache
@pytest.mark.skipif(not (EE_RAW.is_dir() and OFFSETS.is_file() and all(b.is_file() for b in BOARDS)),
                    reason="dial-me-spinner boards, jlc_offsets.json or the cached EasyEDA records are absent")
def test_dial_me_spinner_reproduces_jlc_offsets():
    pcbnew = pytest.importorskip("pcbnew")
    parse = parse_record()
    offsets = json.loads(OFFSETS.read_text())
    seen, verdicts = set(), {}
    for board, part, code in board_parts(pcbnew):
        raw = EE_RAW / f"{code}.json"
        if not raw.is_file() or not json.loads(raw.read_text()).get("success"):
            continue
        c = fit.fit_part(part, parse(code, raw))
        o = offsets[code]
        assert (c.rot, c.mirror) == (cv.norm_angle(o["rot"]), False), (board, part.ref, code, c.rot, c.reasons)
        assert abs(c.dx - o["dx"]) <= 0.01 and abs(c.dy - o["dy"]) <= 0.01, (board, part.ref, code, c.dx, c.dy)
        assert verdicts.setdefault(code, c.verdict) == c.verdict          # the same footprint, the same verdict
        want, why = DIAL_NOT_GREEN.get(code, (GREEN, ""))
        assert c.verdict == want and has(c, why), (board, part.ref, code, c.verdict, c.reasons)
        seen.add(code)
    assert len(seen) >= 55 and set(DIAL_NOT_GREEN) <= seen


@pytest.mark.pcbnew
@pytest.mark.cache
@pytest.mark.skipif(not (Z_FIX / "truth.csv").is_file(), reason=f"z2amiller ground truth not in {Z_FIX}")
def test_jlc_preview_truth_is_never_contradicted_by_green():
    """The z2amiller fixtures record the rotation JLC's preview showed for each part (observed CPL angle). With the
    case list's pin functions on the KiCad pads, every green fit must agree with it, and the parts the fixture expects
    to be refused must be red."""
    pytest.importorskip("pcbnew")
    parse = parse_record()
    wrong, right, red_expected = [], [], []
    for board, truth_file in (("corner_case", "truth.csv"), ("pin_functions_and_marks",
                                                             "truth_pin_functions_and_marks.csv")):
        truth = {r["reference"]: r for r in csv.DictReader(open(Z_FIX / truth_file))}
        for r in csv.DictReader(open(Z_FIX / "boards" / board / "cases.csv")):
            raw = Z_FIX / "easyeda" / f"{r['lcsc']}.json"
            if not raw.is_file():
                continue
            part = library_part(r["lib"], r["footprint"], r["reference"])
            pf = dict(x.split("=") for x in r["pinfunctions"].split(";")) if r["pinfunctions"] else {}
            for p in part.pads:
                p.pin_name = pf.get(p.number, "")
            c = fit.fit_part(part, parse(r["lcsc"], raw))
            t = truth.get(r["reference"], {})
            if r["expect"] == "red":
                red_expected.append((r["reference"], c.verdict))
                continue
            theta, obs = float(r["rotation"]), float(t["observed_rotation"])
            alpha = obs - theta if r["layer"] == "F" else obs - 180.0 + theta     # jlc_angle, solved for alpha
            ok = (adiff(c.rot, alpha) < 0.01 and abs(c.dx - float(t["origin_dx_mm"])) <= 0.011
                  and abs(c.dy - float(t["origin_dy_mm"])) <= 0.011)
            (right if ok else wrong).append((r["reference"], c.verdict, c.rot, alpha))
    assert all(v == RED for _, v in red_expected) and len(red_expected) == 2, red_expected
    assert not [w for w in wrong if w[1] == GREEN], wrong
    # D12: EasyEDA's symbol names the cathode pad A; the names win (yellow) and the preview disagrees
    assert [w[0] for w in wrong] == ["D12"] and len(right) >= 45, (wrong, len(right))


OLD_CPL = SCRATCH / "fab-before"


def read_cpl_csv(path):
    """Designator -> (Mid X, Mid Y, layer, rotation) of a JLC CPL as jlc_cpl.py wrote it."""
    return {r["Designator"]: (float(r["Mid X"].removesuffix("mm")), float(r["Mid Y"].removesuffix("mm")),
                              r["Layer"], float(r["Rotation"])) for r in csv.DictReader(open(path))}


@pytest.mark.pcbnew
@pytest.mark.cache
@pytest.mark.skipif(not (EE_RAW.is_dir() and OLD_CPL.is_dir() and all(b.is_file() for b in BOARDS)),
                    reason="dial-me-spinner boards, CPLs or the cached EasyEDA records are absent")
def test_dial_me_spinner_rows_from_the_fits():
    """The fits, turned into rows by convention.cpl_row_for, give the released CPLs back and differ from the old buggy
    ones in exactly the 7 rows that were wrong (repeated pad numbers had broken an origin fit)."""
    pcbnew = pytest.importorskip("pcbnew")
    parse = parse_record()
    differ_old, n = set(), 0
    for path in BOARDS:
        side = path.stem.rsplit("-", 1)[1]
        released = read_cpl_csv(path.parents[2] / "fab" / side / f"{path.stem}-CPL-JLCPCB.csv")
        old = read_cpl_csv(OLD_CPL / side / f"{path.stem}-CPL-JLCPCB.csv")
        b = pcbnew.LoadBoard(str(path))
        ao = b.GetDesignSettings().GetAuxOrigin()
        origin = (pcbnew.ToMM(ao.x), pcbnew.ToMM(ao.y))
        assert origin == (100.0, 100.0)
        for fp in b.GetFootprints():
            code = fp.GetFieldText("LCSC") if fp.HasFieldByName("LCSC") else ""
            raw = EE_RAW / f"{code}.json"
            if not code or fp.GetReference() not in released or not raw.is_file() \
                    or not json.loads(raw.read_text()).get("success"):
                continue
            part = part_from_footprint(fp, pcbnew)
            x, y, a = cv.cpl_row_for(part, fit.fit_part(part, parse(code, raw)), origin)
            rx, ry, layer, ra = released[part.ref]
            assert layer.lower() == part.layer and math.hypot(x - rx, y - ry) <= 0.01 and adiff(a, ra) <= 0.01, \
                (side, part.ref, code, (x, y, a), released[part.ref])
            ox, oy, _, oa = old[part.ref]
            if math.hypot(x - ox, y - oy) > 0.01 or adiff(a, oa) > 0.01:
                differ_old.add(f"{side}:{part.ref}")
            n += 1
    assert n >= 195
    assert differ_old == {"main:J1", "main:J20", "main:J40", "base:J1", "base:J2", "base:J3", "base:SW1"}
