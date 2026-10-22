"""Simulating CPL rows: synthetic footprints for every rule, then the dial-me-spinner boards (golden)."""
import dataclasses
import json
import math
import os
import random
from types import SimpleNamespace

import pytest

from jlcplace import convention as cv
from jlcplace import geom
from jlcplace.check import check_bom, check_cpl, check_row, nonpolar, place_row
from jlcplace.cpl import natural_key, read_bom, read_cpl, rows_for_board, write_cpl
from jlcplace.model import BOTTOM, GREEN, RED, TOP, VERIFIED, YELLOW, Board, Correction, CplRow, EEFootprint, \
    EEPad, NotFound, Pad, Part

ORIGIN = (100.0, 100.0)
SOT23_6 = [("1", -1.1375, -0.95), ("2", -1.1375, 0.0), ("3", -1.1375, 0.95),
           ("4", 1.1375, 0.95), ("5", 1.1375, 0.0), ("6", 1.1375, -0.95)]


def sot23_6(ref="U1", layer=TOP, x=120.0, y=80.0, angle=0.0, nets=("A", "GND", "B", "C", "VCC", "D"), lcsc="C7519"):
    pads = [Pad(n, px, py, 1.325, 0.6, shape="roundrect", rr=0.25, net=net) for (n, px, py), net in zip(SOT23_6, nets)]
    return Part(ref, layer, x, y, angle, footprint="Package_TO_SOT_SMD:SOT-23-6", lcsc=lcsc, pads=pads)


def two_pad(ref="R1", layer=TOP, x=110.0, y=95.0, angle=0.0, footprint="Resistor_SMD:R_0402_1005Metric",
            lcsc="C25744", nets=("N1", "N2")):
    pads = [Pad("1", -0.48, 0.0, 0.56, 0.62, shape="roundrect", rr=0.25, net=nets[0]),
            Pad("2", 0.48, 0.0, 0.56, 0.62, shape="roundrect", rr=0.25, net=nets[1])]
    return Part(ref, layer, x, y, angle, footprint=footprint, lcsc=lcsc, pads=pads)


def switch(ref="SW1", layer=TOP, angle=0.0):
    """TS-1187A: KiCad numbers the paired pads 1,1,2,2; EasyEDA numbers them 1-4."""
    pads = [Pad(n, px, py, 1.5, 1.0, net=net) for n, px, py, net in
            (("1", -3.1, -1.85, "S1"), ("1", 3.1, -1.85, "S1"), ("2", -3.1, 1.85, "S2"), ("2", 3.1, 1.85, "S2"))]
    part = Part(ref, layer, 130.0, 70.0, angle, footprint="Button_Switch_SMD:SW_Push_1P1T_XKB_TS-1187A",
                lcsc="C318884", pads=pads)
    ee = EEFootprint("C318884", "SW-SMD_4P", pads=[EEPad(n, x, y, 1.5, 1.0) for n, x, y in
                                                   (("1", -3.1, -1.85), ("2", 3.1, -1.85), ("3", -3.1, 1.85),
                                                    ("4", 3.1, 1.85))])
    return part, ee


def ee_for(part, corr, numbers=None):
    """The EasyEDA footprint that corr maps onto the part's copper pads: e = M . R(-rot) . (l - d)."""
    pads = []
    for i, p in enumerate(part.copper_pads()):
        v = cv.rot((p.x - corr.dx, p.y - corr.dy), -corr.rot)
        if corr.mirror:
            v = (-v[0], v[1])
        pads.append(EEPad(numbers[i] if numbers else p.number, v[0], v[1], 0.01, 0.01))
    return EEFootprint(part.lcsc, "test", pads=pads)


def row_for(part, corr, reading=cv.DEFAULT, origin=ORIGIN):
    x, y, a = cv.cpl_row_for(part, corr, origin, reading)
    return CplRow(part.ref, x, y, part.layer, a)


def turned(row, da=0.0, dx=0.0, dy=0.0):
    return dataclasses.replace(row, x=row.x + dx, y=row.y + dy, rotation=cv.norm_angle(row.rotation + da))


# ------------------------------------------------------------------------------------------------------- landing

@pytest.mark.parametrize("layer", [TOP, BOTTOM])
@pytest.mark.parametrize("reading", list(cv.BOTTOM_READINGS))
def test_correct_rows_land_every_pad_on_its_own_number(layer, reading):
    rnd = random.Random(11)
    for _ in range(40):
        part = sot23_6(layer=layer, x=rnd.uniform(60, 140), y=rnd.uniform(60, 140),
                       angle=rnd.choice([0, 90, 180, 270, rnd.uniform(0, 360)]))
        corr = Correction(rot=rnd.choice([0, 90, 180, 270, rnd.uniform(0, 360)]), dx=rnd.uniform(-1, 1),
                          dy=rnd.uniform(-1, 1))
        ee = ee_for(part, corr)
        row = row_for(part, corr, reading)
        lands = place_row(part, row, ee, ORIGIN, reading)
        assert [(lnd.ee_number, lnd.kicad_number) for lnd in lands] == [(n, n) for n, _, _ in SOT23_6]
        assert all(lnd.dist < 1e-3 and lnd.net == p.net for lnd, p in zip(lands, part.pads))
        res = check_row(part, row, ee, ORIGIN, corr=corr, reading=reading, alt_readings=False)
        assert (res.status, res.verdict, res.messages, res.uncovered) == ("ok", GREEN, [], [])
        assert res.expected == row and res.row is row and res.lcsc == "C7519"


@pytest.mark.parametrize("layer", [TOP, BOTTOM])
def test_turned_or_shifted_rows_are_red(layer):
    part = sot23_6(layer=layer, angle=30.0)
    corr = Correction(rot=180.0, dx=0.02)
    ee, row = ee_for(part, corr), row_for(part, corr)
    res = check_row(part, turned(row, 180), ee, ORIGIN, corr=corr)
    assert (res.status, res.verdict) == ("wrong-pad", RED)          # a turned SOT-23-6 still lands on copper
    assert all(lnd.kicad_number is not None for lnd in res.landings)
    assert "1->4 (C, not A)" in res.messages[0]
    assert any("differs from the row jlcplace writes" in m for m in res.messages)
    res = check_row(part, turned(row, 90), ee, ORIGIN, corr=corr)
    assert (res.status, res.verdict) == ("off-copper", RED)
    for dx, dy in ((1.0, 0.0), (0.0, 0.95), (-0.6, 0.6)):
        res = check_row(part, turned(row, dx=dx, dy=dy), ee, ORIGIN)
        assert res.verdict == RED and res.status in ("off-copper", "wrong-pad"), (dx, dy, res)
    # pad centres still on their own pads: only the correction knows the row is 0.42 mm off
    sloppy = turned(row, dx=0.3, dy=0.3)
    assert check_row(part, sloppy, ee, ORIGIN, alt_readings=False).messages == []
    res = check_row(part, sloppy, ee, ORIGIN, corr=corr, alt_readings=False)
    assert res.verdict == GREEN and res.messages[0].endswith("0.424 mm and 0 deg away")


def test_swaps_within_a_net_are_harmless():
    part = sot23_6(nets=("A", "GND", "B", "A", "GND", "B"))     # 180 deg maps 1<->4, 2<->5, 3<->6
    corr = Correction(rot=0.0)
    res = check_row(part, turned(row_for(part, corr), 180), ee_for(part, corr), ORIGIN)
    assert (res.status, res.verdict) == ("ok", GREEN)
    assert res.messages[0].startswith("EasyEDA pads on other-numbered KiCad pads of the same net: 1->4, 2->5")


@pytest.mark.parametrize("ref, footprint, polar", [
    ("R1", "Resistor_SMD:R_0402_1005Metric", False),
    ("C3", "Capacitor_SMD:C_0603_1608Metric", False),
    ("Y1", "Crystal:Crystal_SMD_2012-2Pin_2.0x1.2mm", False),
    ("C7", "Capacitor_Tantalum_SMD:CP_EIA-3216-18_Kemet-A", True),
    ("D1", "Diode_SMD:D_SOD-123", True),
    ("LED1", "LED_SMD:LED_0603_1608Metric", True),
])
def test_two_terminal_parts_turned_180(ref, footprint, polar):
    part = two_pad(ref, footprint=footprint)
    corr = Correction(rot=0.0)
    assert nonpolar(part) is not polar
    res = check_row(part, turned(row_for(part, corr), 180), ee_for(part, corr), ORIGIN, corr=corr)
    if polar:
        assert (res.status, res.verdict) == ("wrong-pad", RED)
    else:
        assert (res.status, res.verdict) == ("renumbered", GREEN)
        assert res.messages[0] == "the two terminals are swapped (non-polar part): 1->2, 2->1"
    res = check_row(part, turned(row_for(part, corr), 90), ee_for(part, corr), ORIGIN)
    assert (res.status, res.verdict) == ("off-copper", RED)


def test_switch_numbering_is_accepted_only_by_its_correction():
    part, ee = switch()
    corr = Correction(rot=0.0, verdict=YELLOW, method="manual", status="manual")
    row = row_for(part, corr)
    res = check_row(part, row, ee, ORIGIN)
    assert (res.status, res.verdict) == ("wrong-pad", RED)             # pads 1 and 2 of the switch shorted
    assert [(lnd.ee_number, lnd.kicad_number) for lnd in res.landings] == [("1", "1"), ("2", "1"), ("3", "2"),
                                                                         ("4", "2")]
    res = check_row(part, row, ee, ORIGIN, corr=corr)
    assert (res.status, res.verdict) == ("renumbered", YELLOW)
    assert res.messages == ["numbering accepted by the correction (manual): 2->1",
                            "numbers only one side has: 3->2, 4->2"]
    green = dataclasses.replace(corr, verdict=GREEN)
    assert check_row(part, row, ee, ORIGIN, corr=green).verdict == GREEN
    verified = dataclasses.replace(corr, verdict=VERIFIED, status="board-verified")
    assert check_row(part, row, ee, ORIGIN, corr=verified).verdict == VERIFIED
    # a correction that maps the pads elsewhere vouches for nothing here
    res = check_row(part, row, ee, ORIGIN, corr=Correction(rot=90.0, verdict=GREEN))
    assert (res.status, res.verdict) == ("wrong-pad", RED)
    # turned 180 the switch only exchanges its two terminals, but by the numbers EasyEDA pad 2 now sits on S2 next
    # to pad 1: only the correction (EasyEDA 1, 2 = KiCad 1) can say it is harmless
    res = check_row(part, turned(row, 180), ee, ORIGIN)
    assert (res.status, res.verdict) == ("wrong-pad", RED)
    res = check_row(part, turned(row, 180), ee, ORIGIN, corr=corr)
    assert (res.status, res.verdict) == ("renumbered", YELLOW), res.messages
    # a quarter turn lands it off copper
    assert check_row(part, turned(row, 90), ee, ORIGIN, corr=corr).status == "off-copper"


def test_correction_overrules_numbers_for_polarity():
    """EasyEDA numbers this diode the other way round (the fit took its polarity from the pin names: EasyEDA pad 1
    belongs on KiCad pad 2). A row that lands it by number reverses it."""
    part = two_pad("D1", footprint="Diode_SMD:D_SOD-123", nets=("VBAT", "SW"))
    fit = Correction(rot=180.0, verdict=YELLOW, reasons=["names pick the member the numbers do not"])
    ee = ee_for(part, fit, numbers=["2", "1"])                     # EasyEDA's pad 2 sits where KiCad has 1
    by_number = row_for(part, Correction(rot=0.0))
    assert [lnd.kicad_number for lnd in place_row(part, by_number, ee, ORIGIN)] == ["2", "1"]
    assert check_row(part, by_number, ee, ORIGIN).verdict == GREEN          # the numbers alone are satisfied
    res = check_row(part, by_number, ee, ORIGIN, corr=fit)
    assert (res.status, res.verdict) == ("wrong-pad", YELLOW)
    assert res.messages[0] == "the pads land by number, but the correction puts them on other nets: 1 on VBAT, " \
                              "not SW, 2 on SW, not VBAT"
    verified = dataclasses.replace(fit, verdict=VERIFIED, status="board-verified")
    assert check_row(part, by_number, ee, ORIGIN, corr=verified).verdict == RED
    assert check_row(part, by_number, ee, ORIGIN, corr=dataclasses.replace(fit, verdict=RED)).verdict == GREEN
    res = check_row(part, row_for(part, fit), ee, ORIGIN, corr=fit)          # the row the correction makes
    assert (res.status, res.verdict) == ("renumbered", YELLOW)
    assert check_row(part, row_for(part, verified), ee, ORIGIN, corr=verified).verdict == VERIFIED
    # a resistor may go either way round, whatever the correction says
    r = two_pad("R9")
    assert check_row(r, row_for(r, Correction(0.0)), ee_for(r, Correction(180.0), numbers=["2", "1"]), ORIGIN,
                     corr=dataclasses.replace(verified, rot=180.0)).verdict == GREEN


def test_unknown_readings_are_refused():
    part = sot23_6(layer=BOTTOM)
    row, ee = row_for(part, Correction(0)), ee_for(part, Correction(0))
    with pytest.raises(ValueError, match="unknown reading"):
        check_row(part, row, ee, ORIGIN, reading="jlc-2023")
    with pytest.raises(ValueError, match="unknown readings"):
        check_row(part, row, ee, ORIGIN, alt_readings=["ccw-from-top", "sideways"])


def test_overlapping_pads_prefer_the_same_number():
    pads = [Pad("A1", 0.0, 0.0, 0.3, 1.0, net="GND"), Pad("B12", 0.0, 0.0, 0.3, 1.0, net="CC"),
            Pad("A5", 0.5, 0.0, 0.3, 1.0, net="X"), Pad("B5", 1.0, 0.0, 0.3, 1.0, net="Y")]
    part = Part("J1", TOP, 120.0, 90.0, 0.0, lcsc="C165948", pads=pads)
    ee = EEFootprint("C165948", "usb", pads=[EEPad("B12", 0, 0, .1, .1), EEPad("A1", 0, 0, .1, .1),
                                            EEPad("A1B12", 0, 0, .1, .1), EEPad("A5", .5, 0, .1, .1),
                                            EEPad("B5", 1.0, 0, .1, .1)])
    res = check_row(part, row_for(part, Correction(0)), ee, ORIGIN)
    assert [lnd.kicad_number for lnd in res.landings] == ["B12", "A1", "A1", "A5", "B5"]
    assert [lnd.net for lnd in res.landings][:2] == ["CC", "GND"]
    assert (res.status, res.verdict) == ("ok", GREEN)
    assert res.messages == ["numbers only one side has: A1B12->A1"]


def test_numbers_one_side_lacks_and_uncovered_pads():
    pads = [Pad("1", -0.5, 0, 0.6, 1.5, net="A"), Pad("2", 0.5, 0, 0.6, 1.5, net="B"),
            Pad("MP", -2.0, -2.0, 1.2, 1.8, net="GND"), Pad("MP", 2.0, -2.0, 1.2, 1.8, net="GND"),
            Pad("", 0.0, -2.0, 0.3, 0.3, net="GND", kind="connect"),                        # not soldered
            Pad("3", 0.0, 3.0, 1.0, 1.0, kind="npth", copper=False),                       # no copper
            Pad("EP", 0.0, -4.0, 1.0, 1.0, net="GND")]
    part = Part("J2", TOP, 100.0, 120.0, 90.0, lcsc="C160404", pads=pads)
    ee = EEFootprint("C160404", "jst", pads=[EEPad("1", -0.5, 0, .1, .1), EEPad("2", 0.5, 0, .1, .1),
                                            EEPad("3", -2.0, -2.0, .1, .1), EEPad("4", 2.0, -2.0, .1, .1),
                                            EEPad("9", 0.0, 0.0, .1, .1, layer=BOTTOM)])   # far side: ignored
    res = check_row(part, row_for(part, Correction(0)), ee, ORIGIN)
    assert [lnd.ee_number for lnd in res.landings] == ["1", "2", "3", "4"]
    assert (res.status, res.verdict, res.uncovered) == ("ok", YELLOW, ["EP"])
    assert res.messages == ["numbers only one side has: 3->MP, 4->MP", "KiCad pads no EasyEDA pad lands on: EP"]


@pytest.mark.parametrize("ee, why", [(None, "no EasyEDA footprint"),
                                     (NotFound("C1", "Component not found"),
                                      "no EasyEDA footprint (Component not found)"),
                                     (EEFootprint("C1", "x"), "the EasyEDA footprint has no pads"),
                                     (EEFootprint("C1", "x", pads=[EEPad("1", 0, 0, 1, 1, layer=BOTTOM)]),
                                      "the EasyEDA footprint has no pads")])
def test_not_simulated(ee, why):
    r = two_pad()
    res = check_row(r, row_for(r, Correction(0)), ee, ORIGIN)
    assert (res.status, res.verdict, res.landings) == ("not-simulated", YELLOW, [])
    assert res.messages == [f"placement not simulated: {why}"]
    for part in (sot23_6(), two_pad("C9", footprint="Capacitor_Tantalum_SMD:CP_EIA-3216-18_Kemet-A"),
                 two_pad("D2", footprint="Diode_SMD:D_SOD-123"), switch()[0]):
        res = check_row(part, row_for(part, Correction(0)), ee, ORIGIN, corr=Correction(0))
        assert (res.status, res.verdict) == ("not-simulated", RED), part.ref
        assert res.messages[0].endswith("the part has polarity or more than two pads")
        assert res.expected is not None


def test_expected_row_tolerances_and_mirror():
    part = sot23_6(layer=BOTTOM, angle=22.0)
    corr = Correction(rot=180.0, dx=0.021, dy=-0.01)
    ee, row = ee_for(part, corr), row_for(part, corr)

    def differs(r, c=corr):
        res = check_row(part, r, ee, ORIGIN, corr=c, alt_readings=False)
        return [m for m in res.messages if "differs from the row jlcplace writes" in m], res

    assert differs(row)[0] == [] and differs(row)[1].expected == row
    assert differs(turned(row, dx=0.03, dy=0.03))[0] == []
    assert differs(turned(row, 0.4))[0] == []
    (m,), res = differs(turned(row, dx=0.06))
    assert m.endswith("0.060 mm and 0 deg away") and res.verdict == GREEN       # a message, not a verdict
    assert len(differs(turned(row, 0.6))[0]) == 1
    assert len(differs(turned(row, 359.0))[0]) == 1
    _, res = differs(row, dataclasses.replace(corr, mirror=True))
    assert res.expected is None and any("mirror image" in m for m in res.messages)


def test_verified_correction_makes_a_matching_row_verified():
    part = sot23_6()
    corr = Correction(rot=90.0, verdict=VERIFIED, status="board-verified")
    ee, row = ee_for(part, corr), row_for(part, corr)
    assert check_row(part, row, ee, ORIGIN, corr=corr).verdict == VERIFIED
    assert check_row(part, turned(row, dx=0.1), ee, ORIGIN, corr=corr).verdict == GREEN
    assert check_row(part, turned(row, 180), ee, ORIGIN, corr=corr).verdict == RED


# ------------------------------------------------------------------------------------------------------- readings

def test_alternative_readings_turn_bottom_parts_about_the_row_point():
    """The module docstring's claim: relative to jlc-2022 the readings turn the part by 2A, 2A + 180 and 180."""
    rnd = random.Random(4)
    turn = {"ccw-from-top": lambda A: 2 * A, "ccw-from-top+180": lambda A: 2 * A + 180, "jlc-2022+180": lambda A: 180}
    for _ in range(300):
        C, A, e = (rnd.uniform(-50, 50), rnd.uniform(-50, 50)), rnd.uniform(0, 360), (rnd.uniform(-5, 5),
                                                                                     rnd.uniform(-5, 5))
        base = cv.ee_to_board(e, C, A, BOTTOM)
        for name, f in turn.items():
            r = cv.rot((base[0] - C[0], base[1] - C[1]), f(A))
            assert math.dist(cv.ee_to_board(e, C, A, BOTTOM, name), (C[0] + r[0], C[1] + r[1])) < 1e-9


@pytest.mark.parametrize("alpha, sensitive", [
    (90.0, {"ccw-from-top", "jlc-2022+180"}),            # A = 270: 2A = 180 turns it
    (180.0, {"ccw-from-top+180", "jlc-2022+180"}),       # A = 0: only the +180 readings turn it
])
def test_convention_sensitivity(alpha, sensitive):
    part = sot23_6(layer=BOTTOM)
    corr = Correction(rot=alpha)
    ee, row = ee_for(part, corr), row_for(part, corr)
    for name in cv.BOTTOM_READINGS:
        res = check_row(part, row, ee, ORIGIN, corr=corr, alt_readings=[name])
        assert res.convention_sensitive is (name in sensitive), name
        assert res.verdict == GREEN                                       # a checklist item, not a verdict
    res = check_row(part, row, ee, ORIGIN, corr=corr)
    assert res.convention_sensitive and res.messages[-1].startswith("bottom reading matters")
    assert not check_row(part, row, ee, ORIGIN, corr=corr, alt_readings=False).convention_sensitive
    top = sot23_6()
    assert not check_row(top, row_for(top, corr), ee_for(top, corr), ORIGIN).convention_sensitive


def test_convention_sensitivity_ignores_terminal_swaps_of_nonpolar_parts():
    for ref, fp, sensitive in (("R1", "Resistor_SMD:R_0402_1005Metric", False), ("D1", "Diode_SMD:D_0402", True)):
        part = two_pad(ref, layer=BOTTOM, angle=90.0, footprint=fp)        # A = 90: ccw-from-top turns it 180
        corr = Correction(0.0)
        res = check_row(part, row_for(part, corr), ee_for(part, corr), ORIGIN, alt_readings=["ccw-from-top"])
        assert res.convention_sensitive is sensitive, ref


# ------------------------------------------------------------------------------------------------------- CPL / BOM

def test_check_cpl_statuses_and_lookup():
    parts = [sot23_6("U1", lcsc="C10"), sot23_6("U2", lcsc="C10"), sot23_6("U3", lcsc=""), sot23_6("U4", lcsc=""),
             two_pad("R1"), two_pad("R2"), two_pad("R3"), sot23_6("U5", layer=BOTTOM, lcsc="C11"),
             sot23_6("U6", lcsc="C10", x=140), sot23_6("U7", lcsc="C12"), two_pad("R4")]
    parts[4].dnp = True
    parts[5].exclude_bom = True
    parts[6].board_only = True
    parts[10].exclude_pos = True
    board = Board("x", ORIGIN, parts)
    corr = Correction(rot=270.0)
    ee = ee_for(parts[0], corr)
    calls = []

    def lookup(code):
        calls.append(code)
        if code == "C12":
            raise OSError("cache unreadable")
        return ee

    rows = [row_for(parts[0], corr), row_for(parts[3], corr), row_for(parts[4], corr), row_for(parts[5], corr),
            dataclasses.replace(row_for(parts[7], corr), layer=TOP), row_for(parts[8], corr),
            row_for(parts[8], corr), CplRow("X1", 0, 0, TOP, 0), row_for(parts[9], corr)]
    res = check_cpl(board, rows, lookup, corrections={"C10": corr})
    got = [(r.ref, r.status, r.verdict) for r in res]
    assert got == [("U1", "ok", GREEN), ("U4", "not-simulated", RED), ("R1", "not-placed", RED),
                   ("R2", "not-placed", YELLOW), ("U5", "layer-mismatch", RED), ("U6", "duplicate", RED),
                   ("U6", "duplicate", RED), ("X1", "not-on-board", RED), ("U7", "not-simulated", RED),
                   ("U2", "no-row", RED)]
    assert sorted(calls) == ["C10", "C12"]                       # looked up once per code
    assert res[1].messages[0] == "the board part has no LCSC code"
    assert res[2].messages == ["the board part is DNP: JLC must not place it"]
    assert res[5].messages[0] == "2 rows for U6" and res[5].landings
    assert "lookup failed (OSError: cache unreadable)" in res[8].messages[0]
    assert all(r.row is rows[i] for i, r in enumerate(res[:9]))
    assert res[0].expected == rows[0]


def test_check_bom_messages():
    parts = [two_pad("R1", lcsc="C1"), two_pad("R2", lcsc="C1"), two_pad("R3", lcsc="C2"), sot23_6("U1", lcsc="C3"),
             sot23_6("U2", lcsc="C4"), sot23_6("J1", lcsc=""), two_pad("R4", lcsc="C5")]
    parts[2].dnp = True
    board = Board("x", ORIGIN, parts)
    bom = [{"designators": ["R1", "R2"], "lcsc": "C1", "comment": "10k", "qty": 3},
           {"designators": ["R3"], "lcsc": "C2", "comment": "dnp"},
           {"designators": ["U1"], "lcsc": "C9", "comment": "reg"},
           {"designators": ["R1"], "lcsc": "C1", "comment": "again", "qty": 1},
           {"designators": ["X9"], "lcsc": "xyz", "comment": "ghost"},
           {"designators": ["R4"], "lcsc": "", "comment": "nocode"},
           {"designators": [], "lcsc": "C1", "comment": "empty"}]
    cpl = [CplRow(r, 0, 0, TOP, 0) for r in ("R1", "R2", "U1", "Q7", "R4")]
    msgs = check_bom(board, bom, cpl)
    for want in ("BOM line 1 (10k): Qty 3 but 2 designators", "R3: in the BOM but DNP on the board",
                 "R3: in the BOM but the CPL has no row for it", "U1: the BOM says C9, the board says C3",
                 "R1: 2 times in the BOM", "BOM line 5 (ghost): 'xyz' is not an LCSC code",
                 "X9: in the BOM but not on the board", "BOM line 6 (nocode): no LCSC code",
                 "BOM line 7 (empty): no designators", "Q7: a CPL row but no BOM line (JLC will not place it)",
                 "U2 (C4): JLC should assemble it but the BOM does not list it"):
        assert want in msgs, want
    assert not [m for m in msgs if m.startswith("J1")]           # no code, not in the BOM: nothing to say
    assert len(msgs) == len(set(msgs)) == 11
    clean = check_bom(board, [{"designators": ["R1", "R2"], "lcsc": "C1", "qty": 2}, {"designators": ["U1"],
                                                                                      "lcsc": "C3"},
                              {"designators": ["U2"], "lcsc": "C4"}, {"designators": ["R4"], "lcsc": "C5"}],
                      [CplRow(r, 0, 0, TOP, 0) for r in ("R1", "R2", "U1", "U2", "R4")])
    assert clean == []


# ------------------------------------------------------------------------------------------------------- golden

DMS = "/home/user/dial-me-spinner/hardware"
OFFSETS = f"{DMS}/design/jlc_offsets.json"
BEFORE = "/tmp/claude-0/-home-user-dial-me-spinner/3708159a-bc3b-5167-8507-f5b08e1ce6c9/scratchpad/jlc/fab-before"
BOARDS = ("main", "base")


def pcb_path(name):
    return f"{DMS}/kicad/{name}/dial-me-spinner-{name}.kicad_pcb"


def cpl_path(name, before=False):
    return f"{BEFORE}/{name}/dial-me-spinner-{name}-CPL-JLCPCB.csv" if before else \
        f"{DMS}/fab/{name}/dial-me-spinner-{name}-CPL-JLCPCB.csv"


def part_from_fp(pcbnew, fp, mirror_pad_angle=True) -> Part:
    """A pcbnew footprint as a model.Part, pads in the library frame. GetFPRelativePosition is the as-placed local
    frame, y already mirrored on the bottom, so library = (x, -y) there; the relative pad angle is mirrored with it
    (psi_lib = -psi_rel), which test_pad_frames_match_pcbnew proves against HitTest."""
    bottom = fp.IsFlipped()
    L = pcbnew.B_Cu if bottom else pcbnew.F_Cu
    pos, attrs = fp.GetPosition(), fp.GetAttributes()
    part = Part(fp.GetReference(), BOTTOM if bottom else TOP, pcbnew.ToMM(pos.x), pcbnew.ToMM(pos.y),
                cv.norm_angle(fp.GetOrientationDegrees()),
                footprint=f"{fp.GetFPID().GetLibNickname()}:{fp.GetFPID().GetLibItemName()}", value=fp.GetValue(),
                lcsc=fp.GetFieldText("LCSC") if fp.HasFieldByName("LCSC") else "", dnp=fp.IsDNP(),
                exclude_pos=bool(attrs & pcbnew.FP_EXCLUDE_FROM_POS_FILES),
                exclude_bom=bool(attrs & pcbnew.FP_EXCLUDE_FROM_BOM), board_only=bool(attrs & pcbnew.FP_BOARD_ONLY),
                tht=bool(attrs & pcbnew.FP_THROUGH_HOLE))
    shapes = {pcbnew.PAD_SHAPE_RECTANGLE: "rect", pcbnew.PAD_SHAPE_ROUNDRECT: "roundrect",
              pcbnew.PAD_SHAPE_OVAL: "oval", pcbnew.PAD_SHAPE_CIRCLE: "circle",
              pcbnew.PAD_SHAPE_TRAPEZOID: "trapezoid", pcbnew.PAD_SHAPE_CHAMFERED_RECT: "chamfered",
              pcbnew.PAD_SHAPE_CUSTOM: "custom"}
    kinds = {pcbnew.PAD_ATTRIB_SMD: "smd", pcbnew.PAD_ATTRIB_PTH: "tht", pcbnew.PAD_ATTRIB_NPTH: "npth",
             pcbnew.PAD_ATTRIB_CONN: "connect"}
    for p in fp.Pads():
        r = p.GetFPRelativePosition()
        fx, fy = pcbnew.ToMM(r.x), pcbnew.ToMM(r.y)
        rel = p.GetFPRelativeOrientation().AsDegrees()
        kind = kinds[p.GetAttribute()]
        shape = shapes[p.GetShape(L)]
        size = p.GetSize(L)
        pad = Pad(p.GetNumber(), fx, -fy if bottom else fy, pcbnew.ToMM(size.x), pcbnew.ToMM(size.y),
                  cv.norm_angle(-rel if bottom and mirror_pad_angle else rel), shape, kind,
                  kind == "tht" or (kind in ("smd", "connect") and p.IsOnLayer(L)), p.GetNetname(),
                  p.GetPinFunction(), p.GetPinType(), p.GetRoundRectRadiusRatio(L) if shape == "roundrect" else 0.0)
        if shape in ("trapezoid", "chamfered", "custom"):
            o = p.GetEffectivePolygon(L, pcbnew.ERROR_INSIDE).Outline(0)
            pad.poly = [cv.board_to_library(part, (pcbnew.ToMM(o.CPoint(i).x), pcbnew.ToMM(o.CPoint(i).y)))
                        for i in range(o.PointCount())]
        part.pads.append(pad)
    return part


def board_from_pcbnew(pcbnew, kb, path) -> Board:
    org = kb.GetDesignSettings().GetAuxOrigin()
    return Board(path, (pcbnew.ToMM(org.x), pcbnew.ToMM(org.y)),
                 [part_from_fp(pcbnew, fp) for fp in kb.GetFootprints()], source="test")


def corrections_from_offsets(offsets) -> dict[str, Correction]:
    out = {}
    for code, e in offsets.items():
        if "rot" in e:
            ok = bool(e.get("renumber_ok"))
            out[code] = Correction(e["rot"], e["dx"], e["dy"], method="manual" if ok else "number",
                                   verdict=YELLOW if ok else GREEN, status="manual" if ok else "auto",
                                   reasons=[e["note"]] if "note" in e else [])
    return out


def ee_lookup_from_offsets(offsets):
    def lookup(code):
        e = offsets.get(code) or {}
        if "ee" in e:
            return EEFootprint(code, e.get("title", ""), pads=[EEPad(n, x, y, 0.01, 0.01) for n, x, y in e["ee"]])
        return NotFound(code, e["ee_error"]) if "ee_error" in e else None
    return lookup


@pytest.fixture(scope="module")
def dms():
    pcbnew = pytest.importorskip("pcbnew")
    need = [OFFSETS] + [pcb_path(b) for b in BOARDS] + [cpl_path(b) for b in BOARDS]
    if not all(os.path.exists(p) for p in need):
        pytest.skip("needs the dial-me-spinner repository at /home/user/dial-me-spinner")
    with open(OFFSETS) as f:
        offsets = json.load(f)
    kbs = {b: pcbnew.LoadBoard(pcb_path(b)) for b in BOARDS}
    return SimpleNamespace(pcbnew=pcbnew, kb=kbs, boards={b: board_from_pcbnew(pcbnew, kbs[b], pcb_path(b))
                                                         for b in BOARDS},
                           corrs=corrections_from_offsets(offsets), lookup=ee_lookup_from_offsets(offsets))


def _hittest_mismatches(pcbnew, fp, part) -> int:
    """Scaled pad outlines (library frame) mapped to the board: 0.9x must hit the pcbnew pad, 1.1x must not."""
    L = pcbnew.B_Cu if fp.IsFlipped() else pcbnew.F_Cu
    bad = 0
    for kp, pad in zip(fp.Pads(), part.pads):
        if not pad.copper:
            continue
        for s, want in ((0.9, True), (1.1, False)):
            for u in geom.pad_outline(pad):
                q = cv.library_to_board(part, (pad.x + s * (u[0] - pad.x), pad.y + s * (u[1] - pad.y)))
                bad += kp.HitTest(pcbnew.VECTOR2I(pcbnew.FromMM(q[0]), pcbnew.FromMM(q[1])), 0, L) != want
    return bad


@pytest.mark.pcbnew
def test_pad_frames_match_pcbnew(dms):
    """The test helper's library frame (bottom: (x, -y) and the pad angle negated) against pcbnew's HitTest, on the
    real boards and with every footprint and pad turned to odd angles; the unmirrored angle must fail there."""
    pcbnew = dms.pcbnew
    for name in BOARDS:
        for fp in dms.kb[name].GetFootprints():
            assert _hittest_mismatches(pcbnew, fp, dms.boards[name].part(fp.GetReference())) == 0
    kb = pcbnew.LoadBoard(pcb_path("main"))           # a private copy, turned in memory
    rnd = random.Random(3)
    wrong = 0
    for fp in kb.GetFootprints():
        fp.SetOrientationDegrees(rnd.uniform(-180, 180))
        for kp in fp.Pads():
            kp.SetFPRelativeOrientation(pcbnew.EDA_ANGLE(rnd.uniform(-180, 180), pcbnew.DEGREES_T))
        assert _hittest_mismatches(pcbnew, fp, part_from_fp(pcbnew, fp)) == 0, fp.GetReference()
        if fp.IsFlipped():
            wrong += _hittest_mismatches(pcbnew, fp, part_from_fp(pcbnew, fp, mirror_pad_angle=False))
    assert wrong > 100


@pytest.mark.pcbnew
def test_committed_cpls_check_clean(dms):
    yellows = {}
    for name in BOARDS:
        rows = read_cpl(cpl_path(name))
        res = check_cpl(dms.boards[name], rows, dms.lookup, corrections=dms.corrs)
        assert len(res) == len(rows) and not [r for r in res if r.verdict == RED], name
        assert all(r.expected is None or not [m for m in r.messages if "differs" in m] for r in res)
        yellows.update({(name, r.ref): r.status for r in res if r.verdict == YELLOW})
    # R26's code has no EasyEDA footprint (two-pad resistor: yellow); SW1's 1-4 vs 1,1,2,2 numbering is accepted
    # by its manual correction, which is yellow
    assert yellows == {("main", "R26"): "not-simulated", ("base", "SW1"): "renumbered"}


@pytest.mark.pcbnew
def test_old_cpls_are_red_on_exactly_the_wrong_rows(dms):
    if not all(os.path.exists(cpl_path(b, before=True)) for b in BOARDS):
        pytest.skip("needs the old (buggy) CPLs in the dial-me-spinner session scratchpad")
    red = {}
    for name in BOARDS:
        res = check_cpl(dms.boards[name], read_cpl(cpl_path(name, before=True)), dms.lookup, corrections=dms.corrs)
        red[name] = {r.ref for r in res if r.verdict == RED}
        assert all(r.status == "off-copper" for r in res if r.verdict == RED)
    assert red == {"main": {"J1", "J20", "J40"}, "base": {"J1", "J2", "J3", "SW1"}}


@pytest.mark.pcbnew
def test_rows_for_board_reproduce_the_committed_cpls(dms, tmp_path):
    """Byte for byte, once put in the committed files' plain string order (jlcplace sorts naturally)."""
    for name in BOARDS:
        rows, missing = rows_for_board(dms.boards[name], dms.corrs)
        assert missing == []
        assert [r.ref for r in rows] == sorted((r.ref for r in rows), key=natural_key)
        out = tmp_path / f"{name}.csv"
        write_cpl(out, sorted(rows, key=lambda r: r.ref))
        with open(cpl_path(name), "rb") as f:
            assert out.read_bytes() == f.read(), name


@pytest.mark.pcbnew
def test_mutated_rows_are_red(dms):
    main = dms.boards["main"]
    rows = {r.ref: r for r in read_cpl(cpl_path("main"))}

    def check(ref, row, origin=main.origin):
        p = main.part(ref)
        return check_row(p, row, dms.lookup(p.lcsc), origin, corr=dms.corrs[p.lcsc])

    assert main.part("U2").lcsc == "C2681320" and main.part("U1").lcsc == "C841192"
    assert check("U2", turned(rows["U2"], 180)).status == "wrong-pad"           # SOT-23-6 turned: still on copper
    assert check("U12", turned(rows["U12"], 180)).status == "wrong-pad"         # the same on the bottom
    assert check("U1", turned(rows["U1"], 90)).verdict == RED                   # SOT-23-5 a quarter turn off
    assert check("U2", turned(rows["U2"], dy=0.95)).verdict == RED              # one pin pitch along the row
    assert check("J20", turned(rows["J20"], dx=0.5)).verdict == RED             # one FPC pitch
    assert check("U10", turned(rows["U10"], dx=0.3)).status == "off-copper"
    for ref in ("U2", "U10", "J20", "U11"):                                      # the CPL origin moved 1 mm
        assert check(ref, rows[ref], (main.origin[0], main.origin[1] + 1.0)).verdict == RED, ref
    res = check("C101", turned(rows["C101"], 180))                             # a capacitor turned: harmless
    assert (res.status, res.verdict) == ("renumbered", GREEN)


@pytest.mark.pcbnew
def test_convention_sensitive_bottom_parts(dms):
    main = dms.boards["main"]
    rows = read_cpl(cpl_path("main"))
    every = {r.ref for r in check_cpl(main, rows, dms.lookup, corrections=dms.corrs) if r.convention_sensitive}
    assert every == {"BZ1", "J1", "J40", "U10", "U11", "U12", "U30", "U32", "U40", "U41"}
    ccw = {r.ref for r in rows
           if check_row(main.part(r.ref), r, dms.lookup(main.part(r.ref).lcsc), main.origin,
                        alt_readings=["ccw-from-top"]).convention_sensitive}
    assert ccw == {"BZ1", "J1", "J40", "U10", "U11", "U12", "U32"}
    assert not [r for r in rows if r.layer == TOP and r.ref in every]


@pytest.mark.pcbnew
def test_bom_agrees_with_board_and_cpl(dms):
    for name in BOARDS:
        bom = read_bom(f"{DMS}/fab/{name}/dial-me-spinner-{name}-BOM-JLCPCB.csv")
        assert check_bom(dms.boards[name], bom, read_cpl(cpl_path(name))) == [], name
