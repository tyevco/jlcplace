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
    # a polar part: the fit chose its polarity against the numbers, so the row that follows them reverses it (red
    # even against a fitted correction; a multi-pin part would be yellow, the numbers may be right after all)
    assert (res.status, res.verdict) == ("wrong-pad", RED)
    assert res.messages[0] == "the pads land by number, but the correction puts them on other nets: 1 on VBAT, " \
                              "not SW, 2 on SW, not VBAT"
    verified = dataclasses.replace(fit, verdict=VERIFIED, status="board-verified")
    assert check_row(part, by_number, ee, ORIGIN, corr=verified).verdict == RED
    # a red correction of a polar part (names against numbers, nothing to settle it) leaves the polarity open: a row
    # that lands it by number is not vouched for either
    res = check_row(part, by_number, ee, ORIGIN, corr=dataclasses.replace(fit, verdict=RED))
    assert (res.status, res.verdict) == ("wrong-pad", RED) and "polarity needs a decision" in res.messages[0]
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
    assert res.messages == ["numbers only one side has: 3->MP, 4->MP", "KiCad pads that no EasyEDA pad lands on: EP"]


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
    assert res.messages == [f"placement not simulated: {why}; a two-pad non-polar part, and the row point is on it"]
    for part in (sot23_6(), two_pad("C9", footprint="Capacitor_Tantalum_SMD:CP_EIA-3216-18_Kemet-A"),
                 two_pad("D2", footprint="Diode_SMD:D_SOD-123"), switch()[0]):
        res = check_row(part, row_for(part, Correction(0)), ee, ORIGIN, corr=Correction(0))
        assert (res.status, res.verdict) == ("not-simulated", RED), part.ref
        assert res.messages[0].endswith(("a polar two-pad part", "copper pads)")), res.messages
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


# ------------------------------------------------------------------------------------------------------- review

def sod123(ref="D21", numbers=("1", "2"), names=("", ""), nets=("VBAT", "SW"), footprint="Diode_SMD:D_SOD-123"):
    pads = [Pad(numbers[0], -1.65, 0.0, 0.9, 1.2, shape="roundrect", rr=0.25, net=nets[0], pin_name=names[0]),
            Pad(numbers[1], 1.65, 0.0, 0.9, 1.2, shape="roundrect", rr=0.25, net=nets[1], pin_name=names[1])]
    return Part(ref, TOP, 115.0, 75.0, 0.0, footprint=footprint, lcsc="C151296", pads=pads)


def ee_two(numbers=("1", "2"), names=None, half=1.6, w=0.9, h=1.2, title="SOD-123F", tags=()):
    ee = EEFootprint("C151296", title, pads=[EEPad(numbers[0], -half, 0.0, w, h), EEPad(numbers[1], half, 0.0, w, h)],
                     pin_names=dict(names or {numbers[0]: numbers[0], numbers[1]: numbers[1]}))
    if tags:
        ee.tags = list(tags)                 # (set after construction: an older EEFootprint has no such field)
    return ee


def test_red_correction_without_number_evidence_is_red():
    """logic-1: KiCad numbers the diode's pads K / A, EasyEDA 1 / 2, and no pin name says which is which. The fit
    cannot tell the two placements apart (red); nothing else vouches for either row, so neither may be green."""
    from jlcplace.fit import fit_part
    part, ee = sod123(numbers=("K", "A")), ee_two()
    corr = fit_part(part, ee)
    assert corr.verdict == RED and "ambiguous" in corr.reasons[0]
    for da in (0.0, 180.0):
        res = check_row(part, turned(row_for(part, corr), da), ee, ORIGIN, corr=corr)
        assert res.verdict == RED, (da, res.status, res.messages)
        assert "nothing vouches" in res.messages[0] or "polarity needs a decision" in res.messages[0]
    # without any correction (a library caller) the numbers decide nothing either: not green
    res = check_row(part, turned(row_for(part, corr), 180), ee, ORIGIN)
    assert res.verdict == YELLOW and "do not decide" in res.messages[0]
    # BGA balls lettered in KiCad, numbered in EasyEDA: every quarter turn lands every ball
    balls = [("A1", -0.4, -0.4), ("A2", 0.4, -0.4), ("B1", -0.4, 0.4), ("B2", 0.4, 0.4)]
    bga = Part("U7", TOP, 100.0, 90.0, 0.0, footprint="Package_BGA:Fake_2x2", lcsc="C9",
               pads=[Pad(n, x, y, 0.3, 0.3, shape="circle", net=f"N{n}") for n, x, y in balls])
    ee = EEFootprint("C9", "BGA-4", pads=[EEPad(str(k + 1), x, y, 0.3, 0.3, shape="ellipse")
                                          for k, (_, x, y) in enumerate(balls)])
    corr = fit_part(bga, ee)
    assert corr.verdict == RED
    for da in (0.0, 90.0, 180.0, 270.0):
        assert check_row(bga, turned(row_for(bga, corr), da), ee, ORIGIN, corr=corr).verdict == RED, da


def test_letter_numbered_pads_keep_their_pin_names():
    """logic-8: KiCad numbers the diode's pads K / A and names its pins K / A; EasyEDA numbers 1 / 2 and names them
    - / +. The names were thrown away as 'repeating the pad number', the fit was red and either row passed."""
    from jlcplace.fit import fit_part
    part = sod123(numbers=("K", "A"), names=("K", "A"))
    ee = ee_two(names={"1": "-", "2": "+"})
    corr = fit_part(part, ee)
    assert corr.verdict == YELLOW and corr.rot == 0.0 and any("pin names pick rot 0" in r for r in corr.reasons)
    assert check_row(part, row_for(part, corr), ee, ORIGIN, corr=corr).verdict != RED
    res = check_row(part, turned(row_for(part, corr), 180), ee, ORIGIN, corr=corr)
    assert (res.status, res.verdict) == ("wrong-pad", RED), res.messages


def test_reversed_tantalum_on_a_generic_capacitor_footprint_is_not_green():
    """ee-1 / geo-2 / logic-3: a tantalum (EasyEDA category 'Tantalum Capacitors', or with no category its CAP-SMD_
    title) on KiCad's non-polar C_1206 footprint. Turned 180 degrees its + is on the other net."""
    from jlcplace.fit import fit_part
    pads = [Pad("1", -1.475, 0.0, 1.15, 1.8, shape="roundrect", rr=0.2, net="+5V"),
            Pad("2", 1.475, 0.0, 1.15, 1.8, shape="roundrect", rr=0.2, net="GND")]
    part = Part("C5", TOP, 105.0, 95.0, 0.0, footprint="Capacitor_SMD:C_1206_3216Metric", lcsc="C7171", pads=pads)
    for title, tags in (("CAP-SMD_L3.2-W1.6-RD-C7171", ["Tantalum Capacitors"]), ("CAP-SMD_L3.2-W1.6-RD", []),
                        ("CASE-A_3216", [])):
        ee = ee_two(half=1.53, w=1.2, h=1.8, title=title, tags=tags)
        corr = fit_part(part, ee)
        assert corr.verdict == YELLOW and any("polarity decided by pad numbers only" in r for r in corr.reasons)
        res = check_row(part, turned(row_for(part, corr), 180), ee, ORIGIN, corr=corr)
        assert (res.status, res.verdict) == ("wrong-pad", RED), (title, res.messages)
        # even a preview-verified correction did not keep the turned row from passing as a terminal swap
        verified = dataclasses.replace(corr, verdict=VERIFIED, status="preview-verified")
        assert check_row(part, row_for(part, verified), ee, ORIGIN, corr=verified).verdict == VERIFIED
        assert check_row(part, turned(row_for(part, verified), 180), ee, ORIGIN, corr=verified).verdict == RED
    ee = ee_two(half=1.53, w=1.2, h=1.8, title="C1206", tags=["Multilayer Ceramic Capacitors MLCC - SMD/SMT"])
    corr = fit_part(part, ee)
    assert corr.verdict == GREEN
    for da in (0.0, 180.0):
        assert check_row(part, turned(row_for(part, corr), da), ee, ORIGIN, corr=corr).verdict == GREEN


def test_reversed_two_pin_connector_with_an_x_reference_is_red():
    """logic-2: X1 is a 2-pin battery connector (IEC 81346 letter for connectors), not a crystal."""
    from jlcplace.fit import fit_part
    pads = [Pad(n, x, 0.0, 1.2, 1.75, shape="oval", kind="tht", net=net)
            for n, x, net in (("1", -1.0, "VBAT"), ("2", 1.0, "GND"))]
    part = Part("X1", TOP, 100.0, 100.0, 0.0, footprint="Connector_JST:JST_PH_B2B-PH-K_1x02_P2.00mm_Vertical",
                lcsc="C131337", pads=pads)
    ee = EEFootprint("C131337", "CONN-TH_2P-P2.00_S2B-PH-K-S",
                     pads=[EEPad(n, x, 0.0, 1.2, 1.75, shape="oval", layer="multi", hole=0.75)
                           for n, x in (("1", -1.0), ("2", 1.0))], pin_names={"1": "1", "2": "2"})
    corr = fit_part(part, ee)
    assert corr.verdict == YELLOW and any("polarity decided by pad numbers only" in r for r in corr.reasons)
    res = check_row(part, turned(row_for(part, corr), 180), ee, ORIGIN, corr=corr)
    assert (res.status, res.verdict) == ("wrong-pad", RED)
    res = check_row(part, row_for(part, corr), NotFound("C131337", "Component not found"), ORIGIN)
    assert (res.status, res.verdict) == ("not-simulated", RED)


def test_polar_part_rows_against_the_polarity_decision():
    """truth-2: EasyEDA numbers this diode's anode 1, against KiCad (pad 1 = K). Without a drawn + nothing settles
    names against numbers: the fit is red and so is either row. With a + beside its anode the fit follows the names;
    the row that lands it by number reverses it and is red, not yellow. Once verified, only the verified row passes."""
    from jlcplace.fit import fit_part
    part = sod123("D5", names=("K", "A"), nets=("VBAT", "SW"))
    ee = ee_two(numbers=("2", "1"), names={"2": "K", "1": "A"})          # EasyEDA's pad 2 (K) where KiCad has 1
    corr = fit_part(part, ee)
    assert corr.verdict == RED
    by_number = turned(row_for(part, corr), 180)
    assert all(lnd.kicad_number == lnd.ee_number for lnd in place_row(part, by_number, ee, ORIGIN))
    for row in (row_for(part, corr), by_number):
        assert check_row(part, row, ee, ORIGIN, corr=corr).verdict == RED
    marked = dataclasses.replace(ee, plus=[(1.6 + 0.6, 0.0, 0.4)])        # "+" beside EasyEDA pad 1, the anode
    corr = fit_part(part, marked)
    assert corr.verdict == YELLOW and corr.rot == 0.0
    assert check_row(part, row_for(part, corr), marked, ORIGIN, corr=corr).verdict == YELLOW
    res = check_row(part, by_number, marked, ORIGIN, corr=corr)
    assert (res.status, res.verdict) == ("wrong-pad", RED)
    verified = dataclasses.replace(corr, verdict=VERIFIED, status="preview-verified")
    assert check_row(part, row_for(part, verified), marked, ORIGIN, corr=verified).verdict == VERIFIED
    assert check_row(part, by_number, marked, ORIGIN, corr=verified).verdict == RED


def test_polarity_in_doubt_is_red_whichever_way_the_row_goes():
    """ee-2 (C2286 on KiCad's LED_0603, no pin functions): EasyEDA numbers the LED's anode 1 and draws its + there;
    KiCad's library puts the cathode on pad 1. The numbers say one way, the + the other: the fit is red, and the row
    that follows the numbers must not pass because the numbers 'decide' it."""
    from jlcplace.fit import fit_part
    led = sod123("LED1", footprint="LED_SMD:LED_0603_1608Metric")
    for p, x in zip(led.pads, (-0.7875, 0.7875)):
        p.x, p.w, p.h = x, 0.875, 0.95
    ee = ee_two(numbers=("2", "1"), half=0.75, w=0.8, h=0.8, title="LED-SMD_L1.6-W0.8-R-RD")
    ee.plus = [(1.35, 0.0, 0.3)]                                        # beside EasyEDA pad 1, the anode
    corr = fit_part(led, ee)
    assert corr.verdict == RED and corr.doubt == "polarity" and any("drawn" in r or "+ beside" in r
                                                                    for r in corr.reasons), corr.reasons
    res = check_row(led, row_for(led, corr), ee, ORIGIN, corr=corr)          # by the numbers
    assert res.verdict == RED and "the polarity needs a decision" in res.messages[0], res.messages
    res = check_row(led, turned(row_for(led, corr), 180), ee, ORIGIN, corr=corr)   # as the + says
    assert (res.status, res.verdict) == ("wrong-pad", RED), res.messages
    # once someone has looked (JLC's preview), the verified orientation passes and the other does not
    good = dataclasses.replace(corr, rot=cv.norm_angle(corr.rot + 180.0), verdict=VERIFIED, status="preview-verified")
    assert check_row(led, row_for(led, good), ee, ORIGIN, corr=good).verdict == VERIFIED
    assert check_row(led, turned(row_for(led, good), 180), ee, ORIGIN, corr=good).verdict == RED


def test_rows_off_a_known_correction_are_yellow():
    """truth-4 / logic-4: every pad centre still on its own pad, but the row is off where the correction puts the
    part: yellow ("offset") beyond max(0.15 mm, a quarter of the smallest pad it lands on) or 2 degrees, and the
    message comes first so the summary and the annotations show it."""
    part = sot23_6(angle=0.0)
    corr = Correction(rot=180.0, dx=0.02, verdict=GREEN)
    ee, row = ee_for(part, corr), row_for(part, corr)
    for dx, want in ((0.1, GREEN), (0.14, GREEN), (0.3, YELLOW), (0.5, YELLOW)):
        res = check_row(part, turned(row, dx=dx), ee, ORIGIN, corr=corr, alt_readings=False)
        assert all(lnd.kicad_number == lnd.ee_number for lnd in res.landings)
        assert res.verdict == want, (dx, res.messages)
        if want == YELLOW:
            assert res.status == "offset" and res.messages[0].startswith("differs from the row jlcplace writes")
    res = check_row(part, turned(row, da=10.0), ee, ORIGIN, corr=corr, alt_readings=False)
    assert (res.status, res.verdict) == ("offset", YELLOW)
    # a DPAK-like part: the lead pads are 1.2 mm wide, so 0.25 mm is within a quarter pad; 0.725 mm (a pad-box
    # centre instead of EasyEDA's origin) is not
    dpak = Part("U9", TOP, 130.0, 60.0, 90.0, footprint="Package_TO_SOT_SMD:TO-252-2", lcsc="C58069",
                pads=[Pad("1", -4.2, -2.28, 2.2, 1.2, net="IN"), Pad("3", -4.2, 2.28, 2.2, 1.2, net="OUT"),
                      Pad("2", 2.1, 0.0, 6.4, 5.8, net="GND")])
    corr = Correction(rot=0.0, dx=-1.5649, verdict=GREEN)
    ee, row = ee_for(dpak, corr), row_for(dpak, corr)
    assert check_row(dpak, turned(row, dy=0.25), ee, ORIGIN, corr=corr).verdict == GREEN
    res = check_row(dpak, turned(row, dy=0.725), ee, ORIGIN, corr=corr)
    assert (res.status, res.verdict) == ("offset", YELLOW) and "0.725 mm" in res.messages[0]
    # against a verified correction the row is no longer verified, and a red correction places nothing to compare
    verified = dataclasses.replace(corr, verdict=VERIFIED)
    assert check_row(dpak, turned(row, dy=0.725), ee, ORIGIN, corr=verified).verdict == YELLOW
    assert check_row(dpak, turned(row, dy=0.725), ee, ORIGIN, corr=dataclasses.replace(corr, verdict=RED)).status \
        != "offset"


def test_unsimulated_row_far_from_its_part_is_red():
    """ci-1: no EasyEDA record for a resistor, and its row 25 mm off and turned: not 'probably fine'."""
    r = two_pad("R1", angle=90.0)
    row = row_for(r, Correction(0))
    for da in (0.0, 180.0, 37.0):
        res = check_row(r, turned(row, da), None, ORIGIN)
        assert (res.status, res.verdict) == ("not-simulated", YELLOW), da
    res = check_row(r, turned(row, 37.0, dx=25.0), None, ORIGIN)
    assert (res.status, res.verdict) == ("not-simulated", RED) and "mm off the part's pads" in res.messages[0]
    res = check_row(r, turned(row, dx=0.8), NotFound("C1", "Component not found"), ORIGIN)
    assert res.verdict == RED                                   # beside the pads (a 0.62 mm wide part), not on them


def test_pads_of_covered_pins_are_not_uncovered():
    """logic-6 / ee-4 / truth-6: thermal vias inside an exposed pad, an ESP32 module's GND vias between its GND pads,
    a DPAK's cut middle lead numbered like its tab: KiCad copper of a pin an EasyEDA pad covers. A pad of another
    number that nothing lands on still counts."""
    from jlcplace.fit import fit_part
    leads = [Pad(str(k + 1), x, y, 0.8, 0.3, net=f"P{k + 1}") for k, (x, y) in
             enumerate(((-1.45, -0.25), (-1.45, 0.25), (1.45, 0.25), (1.45, -0.25)))]
    qfn = leads + [Pad("5", 0.0, 0.0, 1.7, 1.7, net="GND")] + [
        Pad("5", x, y, 0.5, 0.5, shape="circle", kind="tht", net="GND") for x in (-0.5, 0.5) for y in (-0.5, 0.5)]
    esp = leads + [Pad("39", x, 0.0, 0.9, 0.9, net="GND") for x in (-1.5, 0.0, 1.5)] + [
        Pad("39", x, 0.0, 0.6, 0.6, shape="circle", kind="tht", net="GND") for x in (-0.75, 0.75)]
    dpak = [Pad("1", -5.04, -2.28, 2.2, 1.2, net="IN"), Pad("2", -5.04, 0.0, 2.2, 1.2, net="OUT"),
            Pad("3", -5.04, 2.28, 2.2, 1.2, net="ADJ"), Pad("2", 1.26, 0.0, 6.4, 5.8, net="OUT")]
    for name, pads, smd in (("qfn", qfn, 5), ("esp", esp, 7), ("dpak", dpak, None)):
        part = Part("U1", TOP, 110.0, 70.0, 0.0, footprint=name, lcsc="C2", pads=pads)
        corr = Correction(rot=90.0, dx=0.1)
        keep = [p for p in pads if p.kind == "smd"][:smd] if smd else [p for i, p in enumerate(pads) if i != 1]
        ee = ee_for(Part("U1", TOP, 0, 0, 0, pads=keep), corr)
        for e, p in zip(ee.pads, keep):
            e.w, e.h = p.w, p.h
        c = fit_part(part, ee)
        assert c.verdict == GREEN and c.rot == 90.0, (name, c.reasons)
        res = check_row(part, row_for(part, c), ee, ORIGIN, corr=c)
        assert (res.verdict, res.uncovered) == (GREEN, []), (name, res.messages)
    # without EasyEDA pad 3 the DPAK's pad 3 is uncovered: a pad of its own, yellow, and the fit names it once
    keep = [dpak[0], dpak[3]]
    ee = ee_for(Part("U1", TOP, 0, 0, 0, pads=keep), Correction(0))
    part = Part("U1", TOP, 110.0, 70.0, 0.0, footprint="dpak", lcsc="C2", pads=dpak)
    res = check_row(part, row_for(part, Correction(0)), ee, ORIGIN, corr=Correction(0, verdict=GREEN))
    assert (res.verdict, res.uncovered) == (YELLOW, ["3"])


def test_a_row_the_bom_lists_is_simulated_though_the_board_excludes_it_from_its_bom():
    """logic-10: JLC places what its BOM lists. D21 is excluded from the board's BOM only, but the JLC BOM lists it
    and its row is reversed: JLC places it reversed."""
    part = sod123("D21", names=("K", "A"))
    part.exclude_bom = True
    corr = Correction(0.0, verdict=GREEN)
    ee = ee_for(part, corr)
    ee.pin_names = {"1": "K", "2": "A"}
    board = Board("b", ORIGIN, [part])
    row = turned(row_for(part, corr), 180)
    res = check_cpl(board, [row], lambda code: ee, corrections={"D21": corr})[0]
    assert (res.status, res.verdict) == ("not-placed", YELLOW)
    res = check_cpl(board, [row], lambda code: ee, corrections={"D21": corr}, bom_refs={"D21"})[0]
    assert (res.status, res.verdict) == ("wrong-pad", RED)
    assert "the BOM lists it: JLC will place it" in res.messages[-1]


def test_a_reference_two_placed_parts_share_is_red():
    """cli-9 / logic-7: two assembled footprints called R7 (a copied block, an unannotated board). Board.part gave
    the first, so the one row was checked against it (green, or off-copper when it was the other one's) and the
    second R7 was never checked nor reported missing."""
    first, second = two_pad("R7", x=110.0), two_pad("R7", x=118.0, angle=90.0, nets=("N3", "N4"))
    hole = Part("H1", TOP, 140.0, 60.0, 0.0, footprint="MountingHole:MountingHole_3.2mm", exclude_bom=True,
                exclude_pos=True)
    board = Board("b", ORIGIN, [first, second, two_pad("R8", x=125.0), hole, dataclasses.replace(hole)])
    corr = Correction(0.0, verdict=GREEN)
    ee = ee_for(first, corr)
    for rows in ([row_for(first, corr)], [row_for(second, corr)], [row_for(first, corr), row_for(second, corr)]):
        res = check_cpl(board, rows + [row_for(board.parts[2], corr)], lambda code: ee, corrections={"C25744": corr})
        got = [(r.ref, r.status, r.verdict) for r in res]
        assert got == [("R7", "duplicate-ref", RED)] * len(rows) + [("R8", "ok", GREEN)], got
        assert res[0].messages[0].startswith("R7 is on the board 2 times: JLC cannot tell them apart")
        assert not res[0].landings and res[0].row is rows[0]                 # never simulated against either
    # no row at all: still reported once, not as two missing rows (also when the first copy has no code)
    res = check_cpl(board, [row_for(board.parts[2], corr)], lambda code: ee, corrections={"C25744": corr})
    assert [(r.ref, r.status, r.verdict) for r in res] == [("R8", "ok", GREEN), ("R7", "duplicate-ref", RED)]
    first.lcsc = ""
    res = check_cpl(board, [row_for(board.parts[2], corr)], lambda code: ee, corrections={"C25744": corr})
    assert [(r.ref, r.status, r.verdict) for r in res] == [("R8", "ok", GREEN), ("R7", "duplicate-ref", RED)]
    first.lcsc = "C25744"
    # a second footprint JLC does not place (DNP) is no ambiguity: the row is checked against the placed one
    board.parts[0].dnp = True
    res = check_cpl(board, [row_for(second, corr), row_for(board.parts[2], corr)], lambda code: ee,
                    corrections={"C25744": corr})
    assert [(r.ref, r.status, r.verdict) for r in res] == [("R7", "ok", GREEN), ("R8", "ok", GREEN)]


def test_a_one_pad_part_without_a_footprint_says_why():
    """logic-11: not 'the part has polarity or more than two pads'."""
    tp = Part("TP1", TOP, 100.0, 100.0, 0.0, footprint="TestPoint:TestPoint_Pad_1.0x1.0mm", lcsc="C5",
              pads=[Pad("1", 0.0, 0.0, 1.0, 1.0, net="X")])
    res = check_row(tp, CplRow("TP1", 0.0, 0.0, TOP, 0.0), NotFound("C5", "Component not found"), ORIGIN)
    assert (res.status, res.verdict) == ("not-simulated", RED)
    assert "one-pad part" in res.messages[0] and "polarity" not in res.messages[0] \
        and "more than two" not in res.messages[0]


def test_bidirectional_tvs_may_be_turned_round():
    """truth-7: D13 (pin functions A1 / A2) at 90 and at 270 degrees is the same part."""
    from jlcplace.fit import fit_part
    part = sod123("D13", names=("A1", "A2"), footprint="Diode_SMD:D_SMF")
    part.angle = 90.0
    ee = ee_two(names={"1": "C", "2": "A"}, title="SMF_L2.8-W1.8-LS3.7-BI")
    ee.tokens = ["BI"]
    corr = fit_part(part, ee)
    assert corr.verdict != RED
    for da in (0.0, 180.0):
        res = check_row(part, turned(row_for(part, corr), da), ee, ORIGIN, corr=corr)
        assert res.verdict == GREEN, (da, res.status, res.messages)
    # a unidirectional TVS on the same footprint turned round is reversed
    uni = sod123("D14", names=("K", "A"), footprint="Diode_SMD:D_SMF")
    ee = ee_two(names={"1": "K", "2": "A"}, title="SMF_L2.8-W1.8-LS3.7-RD")
    corr = fit_part(uni, ee)
    assert check_row(uni, turned(row_for(uni, corr), 180), ee, ORIGIN, corr=corr).verdict == RED


# ------------------------------------------------------------------------------------------------------- golden

# CONTRACT.md: JLCPLACE_DIAL_ME_SPINNER (a dial-me-spinner checkout), JLCPLACE_TEST_EASYEDA (holding fab-before/, the
# old CPLs); the defaults are the development machine's
DMS = os.environ.get("JLCPLACE_DIAL_ME_SPINNER", "/home/user/dial-me-spinner") + "/hardware"
OFFSETS = f"{DMS}/design/jlc_offsets.json"
BEFORE = os.environ.get("JLCPLACE_TEST_EASYEDA", "/tmp/claude-0/-home-user-dial-me-spinner/"
                        "3708159a-bc3b-5167-8507-f5b08e1ce6c9/scratchpad/jlc") + "/fab-before"
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
        pytest.skip(f"needs the dial-me-spinner repository at {DMS} (set JLCPLACE_DIAL_ME_SPINNER)")
    with open(OFFSETS, encoding="utf-8") as f:
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
        pytest.skip(f"needs the old (buggy) CPLs in {BEFORE} (set JLCPLACE_TEST_EASYEDA)")
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


def test_a_one_pad_part_turned_a_quarter_from_its_correction_is_not_green():
    """A one-land part puts its pad centre on the land whatever the row's angle, so the landing test passes any
    orientation. A row a quarter turn from a verified correction (the 3.4 x 1.8 pad now across its land) was green,
    with only a 'differs ... 90 deg away' message; half a turn puts the same copper down."""
    from jlcplace.fit import fit_part
    from jlcplace.model import EEPad
    part = Part("TP1", TOP, 10.0, 10.0, 0.0, footprint="TestPoint:TestPoint_Keystone_5015_Micro_Mini", lcsc="C9999",
                pads=[Pad("1", 0.0, 0.0, 3.4, 1.8, shape="roundrect", rr=0.25, net="/SIG")])
    ee = EEFootprint("C9999", "TP-SMD_L3.4-W1.8", pads=[EEPad("1", 0.0, 0.0, 1.8, 3.4)])
    fitted = fit_part(part, ee)
    assert fitted.rot in (90.0, 270.0)
    corr = dataclasses.replace(fitted, status="preview-verified", verdict=VERIFIED, doubt="")
    origin = (0.0, 0.0)

    def at(a):
        return check_row(part, CplRow("TP1", 10.0, -10.0, TOP, a), ee, origin, corr=corr)

    assert at(fitted.rot).verdict == VERIFIED
    assert at((fitted.rot + 180.0) % 360.0).verdict == GREEN
    for a in (0.0, 180.0):
        res = at(a)
        assert res.verdict == YELLOW and res.status == "offset" and "a quarter turn" in res.messages[0], res
