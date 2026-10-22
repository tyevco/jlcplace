"""Pinout, not rotation: two parts in one package can name their pins differently (a MOSFET's G/S/D against another's
G/D/S, a regulator's GND/VOUT/VIN against GND/VIN/VOUT). When the EasyEDA symbol's pin names, through the placement,
are a permutation of the KiCad pin functions, the part is wired wrong whatever the rotation: red in the fit and in
the check of its row, with or without a correction. Names that differ without being a permutation (B/E/C against
G/D/S) are yellow in both. Hand-built footprints, then every cached real record with named pins on a KiCad part made
of its own pads; the real boards are covered by the golden tests of test_fit, test_check and test_e2e, whose verdicts
this must not change."""
import dataclasses
import json
import os
from pathlib import Path

import pytest

from jlcplace import convention as cv
from jlcplace.check import check_row
from jlcplace.easyeda import from_response
from jlcplace.fit import fit_part
from jlcplace.model import GREEN, RED, TOP, VERIFIED, YELLOW, Correction, CplRow, EEFootprint, EEPad, NotFound, \
    Pad, Part
from jlcplace.parts import PinDiff, compare_names, name_tokens, norm_number, pin, pin_diffs, pinout_text
from jlcplace.pipeline import Resolved, check

ORIGIN = (100.0, 100.0)
SOT23 = [("1", -0.9375, -0.95), ("2", -0.9375, 0.95), ("3", 0.9375, 0.0)]       # KiCad's SOT-23, library frame


def sot23(names=("G", "D", "S"), nets=("/G", "/D", "/S"), ref="Q1", lcsc="C2500"):
    """KiCad's SOT-23 with a Q_NMOS_GDS symbol's pin functions (pad 1 G, 2 D, 3 S) by default."""
    pads = [Pad(n, x, y, 1.475, 0.6, shape="roundrect", rr=0.25, net=net, pin_name=name)
            for (n, x, y), name, net in zip(SOT23, names, nets)]
    return Part(ref, TOP, 120.0, 80.0, 0.0, footprint="Package_TO_SOT_SMD:SOT-23", lcsc=lcsc, pads=pads)


def ee_sot23(names, lcsc="C2500"):
    """EasyEDA's SOT-23 (drawn half a turn from KiCad's, as its "-BR" titles are) with the LCSC part's pin names."""
    return EEFootprint(lcsc, "SOT-23-3_L2.9-W1.6-P1.90-LS2.8-BR",
                       pads=[EEPad(n, -x, -y, 1.475, 0.6) for n, x, y in SOT23], pin_names=dict(zip("123", names)))


def row_for(part, corr):
    x, y, a = cv.cpl_row_for(part, corr, ORIGIN)
    return CplRow(part.ref, x, y, part.layer, a)


def turned(row, da):
    return dataclasses.replace(row, rotation=cv.norm_angle(row.rotation + da))


def has(c, text):
    return any(text in r for r in c.reasons)


# ------------------------------------------------------------------------------------------------------- parts

def test_pin_diffs_tell_a_permutation_from_other_names():
    ee = [pin("1", "G"), pin("2", "S"), pin("3", "D")]
    kicad = [pin("1", "G", "/G"), pin("2", "D", "/D"), pin("3", "S", "/S")]
    diffs = pin_diffs(list(zip(ee, kicad)), ee, kicad)
    assert diffs == [PinDiff("2", "S", "2", "D", "swapped"), PinDiff("3", "D", "3", "S", "swapped")]
    assert pinout_text(diffs) == ("pad 2 is S on the LCSC part, D on the board; pad 3 is D on the LCSC part, S on the "
                                  "board")
    # the same permutation between two pads of one net: the pins sit on the nets their functions are on
    joined = [pin("1", "G", "/G"), pin("2", "D", "/X"), pin("3", "S", "/X")]
    assert {d.kind for d in pin_diffs(list(zip(ee, joined)), ee, joined)} == {"same net"}
    # a bipolar transistor's B/E/C on a MOSFET symbol: disjoint names, nothing permuted
    bjt = [pin("1", "B"), pin("2", "E"), pin("3", "C")]
    assert {d.kind for d in pin_diffs(list(zip(bjt, kicad)), bjt, kicad)} == {"differs"}
    # synonyms and alternate functions agree (VI ~ VIN, EP ~ GND, PA13/SWDIO ~ SWDIO); numbers say nothing
    ee = [pin("1", "VIN"), pin("2", "EP"), pin("3", "PA13/SWDIO"), pin("4", "4")]
    kicad = [pin("1", "VI", 1), pin("2", "GND", 2), pin("3", "SWDIO", 3), pin("4", "OUT", 4)]
    assert pin_diffs(list(zip(ee, kicad)), ee, kicad) == []
    # the EasyEDA pad number is said where it is not the KiCad one
    assert pinout_text([PinDiff("4", "VOUT", "2", "VI", "swapped")]) == \
        "pad 2 (EasyEDA pad 4) is VOUT on the LCSC part, VI on the board"


# ------------------------------------------------------------------------------------------------------- fit

def test_a_permuted_pinout_is_red_in_the_fit():
    """The measured case: an LCSC MOSFET whose pins are G, S, D on a symbol whose pads are G, D, S. Only one
    placement lands every pad, so no rotation can put S on the board's S: the fit was yellow "2 pin name(s) differ"
    and its row green."""
    c = fit_part(sot23(), ee_sot23(("G", "S", "D")))
    assert c.verdict == RED and c.rot == 180.0 and not c.mirror, c.reasons
    assert c.reasons[0].startswith("pinout differs from the symbol: pad 2 is S on the LCSC part, D on the board; "
                                   "pad 3 is D on the LCSC part, S on the board"), c.reasons
    assert "whatever the rotation" in c.reasons[0]
    c = fit_part(sot23(), ee_sot23(("G", "D", "S")))
    assert c.verdict == GREEN and has(c, "3 pin names agree"), c.reasons


def test_a_regulator_with_another_pinout_is_red():
    """KiCad's SOT-223-3_TabPin2 with a GND / VIN / VOUT regulator (tab VIN) against an LCSC regulator whose pins are
    GND / VOUT / VIN (tab 4 = VOUT, as AMS1117's): the tab's 4 is a number KiCad does not have, VI is VIN, VO VOUT."""
    pads = [Pad("1", -3.15, -2.3, 2.0, 1.5, pin_name="GND", net="GND"),
            Pad("2", -3.15, 0.0, 2.0, 1.5, pin_name="VI", net="+5V"),
            Pad("3", -3.15, 2.3, 2.0, 1.5, pin_name="VO", net="+3V3"),
            Pad("2", 3.15, 0.0, 2.0, 3.8, pin_name="VI", net="+5V")]
    part = Part("U8", TOP, 110.0, 90.0, 0.0, footprint="Package_TO_SOT_SMD:SOT-223-3_TabPin2", lcsc="C6186",
                pads=pads)
    ee = EEFootprint("C6186", "SOT-223-3_L6.5-W3.4-P2.30-LS7.0-BR",
                     pin_names={"1": "GND", "2": "VOUT", "3": "VIN", "4": "VOUT"},
                     pads=[EEPad(n, -p.x, -p.y, p.w, p.h) for n, p in zip("1234", pads)])
    c = fit_part(part, ee)
    assert c.verdict == RED and c.rot == 180.0 and c.reasons[0].startswith(
        "pinout differs from the symbol: pad 2 is VOUT on the LCSC part, VI on the board"), c.reasons
    assert "pad 2 (EasyEDA pad 4) is VOUT on the LCSC part, VI on the board" in c.reasons[0]
    res = check_row(part, row_for(part, c), ee, ORIGIN, corr=c)
    assert (res.status, res.verdict) == ("pinout", RED), res.messages


def test_names_a_rotation_fixes_are_not_a_pinout():
    """A SOT-23-6 whose EasyEDA names fit KiCad's half a turn from where the pad numbers put it: the names pick the
    placement (yellow, as before), and the row the numbers would give is the fit's business (wrong-pad), not a
    pinout: some placement wires the part right."""
    xy = [("1", -1.1375, -0.95), ("2", -1.1375, 0.0), ("3", -1.1375, 0.95),
          ("4", 1.1375, 0.95), ("5", 1.1375, 0.0), ("6", 1.1375, -0.95)]
    names = ("SW", "GND", "FB", "EN", "VIN", "BST")
    pads = [Pad(n, x, y, 1.325, 0.6, net=f"/{name}", pin_name=name) for (n, x, y), name in zip(xy, names)]
    part = Part("U3", TOP, 120.0, 80.0, 0.0, footprint="Package_TO_SOT_SMD:SOT-23-6", lcsc="C7722", pads=pads)
    ee = EEFootprint("C7722", "SOT-23-6", pads=[EEPad(n, x, y, 1.325, 0.6) for n, x, y in xy],
                     pin_names={n: names[(int(n) + 2) % 6] for n, _, _ in xy})       # pad 1 named as KiCad's 4
    c = fit_part(part, ee)
    assert c.verdict == YELLOW and c.rot == 180.0 and has(c, "pin names pick rot 180"), c.reasons
    assert not has(c, "pinout differs")
    res = check_row(part, row_for(part, c), ee, ORIGIN, corr=c)
    assert res.verdict == YELLOW and res.status != "pinout" and not any("pinout" in m for m in res.messages)
    res = check_row(part, turned(row_for(part, c), 180), ee, ORIGIN, corr=c)      # by the numbers
    assert (res.status, res.verdict) == ("wrong-pad", YELLOW), res.messages
    assert any(m.startswith("through this row the pin names differ from the symbol") for m in res.messages)


def test_a_drawn_plus_vouches_for_a_polar_part_against_its_names():
    """D12 (JLC's preview data): EasyEDA's symbol names the cathode pad A, its footprint draws the + beside the other
    pad, as KiCad's numbers have it. The names are a permutation through the fit's placement, but the + (right in
    every such case of the preview data) and a half turn that would satisfy the names make it a polarity question,
    not a pinout: yellow, as before."""
    pads = [Pad(n, x, 0.0, 1.5, 1.5, net=net, pin_name=name)
            for n, x, net, name in (("1", -2.0, "/K", "K"), ("2", 2.0, "/A", "A"))]
    part = Part("D12", TOP, 115.0, 70.0, 0.0, footprint="Diode_SMD:D_SMA", lcsc="C2836396", pads=pads)
    ee = EEFootprint("C2836396", "DO-214AC_L4.3-W2.7-LS5.0-RD", pads=[EEPad(n, x, 0.0, 1.5, 1.5)
                                                                      for n, x in (("1", -2.0), ("2", 2.0))],
                     pin_names={"1": "A", "2": "K"}, plus=[(3.0, 0.0, 0.6)])
    c = fit_part(part, ee)
    assert c.verdict == YELLOW and c.rot == 0.0 and c.doubt == "polarity" and not has(c, "pinout differs"), c.reasons
    res = check_row(part, row_for(part, c), ee, ORIGIN, corr=c)
    assert (res.status, res.verdict) == ("ok", YELLOW) and "fit was unsure" in res.messages[0], res.messages
    verified = dataclasses.replace(c, verdict=VERIFIED, status="preview-verified")
    assert check_row(part, row_for(part, c), ee, ORIGIN, corr=verified).verdict == VERIFIED


def test_a_two_terminal_part_s_reversed_names_are_its_polarity():
    """A half turn always lands two pads the other way round, so a diode whose names are reversed is a polarity
    question (yellow without a correction to say more), never a pinout; JLC's preview shows a polarity, so a preview
    verification of the row settles it (a typed-in correction has no alternatives to consult)."""
    pads = [Pad(n, x, 0.0, 1.5, 1.5, net=net, pin_name=name)
            for n, x, net, name in (("1", -2.0, "/K", "K"), ("2", 2.0, "/A", "A"))]
    part = Part("D12", TOP, 115.0, 70.0, 0.0, footprint="Diode_SMD:D_SMA", lcsc="C2836396", pads=pads)
    ee = EEFootprint("C2836396", "DO-214AC", pads=[EEPad("1", -2.0, 0.0, 1.5, 1.5), EEPad("2", 2.0, 0.0, 1.5, 1.5)],
                     pin_names={"1": "A", "2": "K"})
    row = row_for(part, Correction(0.0))
    res = check_row(part, row, ee, ORIGIN)
    assert (res.status, res.verdict) == ("ok", YELLOW), res.messages
    assert any(m.startswith("through this row the pin names differ from the symbol") for m in res.messages)
    typed = Correction(0.0, verdict=VERIFIED, status="preview-verified", method="manual")
    res = check_row(part, row, ee, ORIGIN, corr=typed)
    assert res.verdict == VERIFIED and any(m.endswith("accepted by the preview-verified correction")
                                           for m in res.messages), res.messages


# ------------------------------------------------------------------------------------------------------- check

@pytest.mark.parametrize("stored", ["fit", "none", "old yellow", "green"])
def test_a_permuted_pinout_is_red_in_the_check(stored):
    """The row lands every pad on its own number and net, so nothing in the landing test minds: the check reads the
    names itself, from the EasyEDA record, whatever correction it is given (this fit's, none, one stored before this
    rule, or a green one typed in)."""
    part, ee = sot23(), ee_sot23(("G", "S", "D"))
    fitted = fit_part(part, ee)
    corr = {"fit": fitted, "none": None,
            "old yellow": dataclasses.replace(fitted, verdict=YELLOW, reasons=["2 pin name(s) differ (1 agree)"]),
            "green": dataclasses.replace(fitted, verdict=GREEN, reasons=[], alternatives=[])}[stored]
    res = check_row(part, row_for(part, fitted), ee, ORIGIN, corr=corr)
    assert (res.status, res.verdict) == ("pinout", RED), res.messages
    assert res.messages[0].startswith("pinout differs from the symbol: pad 2 is S on the LCSC part, D on the board; "
                                      "pad 3 is D on the LCSC part, S on the board"), res.messages
    assert "check the LCSC part's pinout against the symbol" in res.messages[0]
    assert all(lnd.kicad_number == lnd.ee_number for lnd in res.landings)


SOT236 = [("1", -1.1375, -0.95), ("2", -1.1375, 0.0), ("3", -1.1375, 0.95), ("4", 1.1375, 0.95), ("5", 1.1375, 0.0),
          ("6", 1.1375, -0.95)]


def test_a_row_by_pad_numbers_is_red_when_no_placement_wires_the_part_right():
    """A SOT-23-6 whose LCSC pinout is permuted under both placements that land its pads (half a turn puts 4 names
    right and swaps FB and GND; 0 deg puts none right): the fit is red. A CPL row from another tool that places it
    by its pad numbers is red too, never yellow "another placement puts them on their own functions": none does."""
    kicad = ("SW", "GND", "FB", "EN", "VIN", "BST")
    pads = [Pad(n, x, y, 1.325, 0.6, net=f"/{name}", pin_name=name) for (n, x, y), name in zip(SOT236, kicad)]
    part = Part("U3", TOP, 120.0, 80.0, 0.0, footprint="Package_TO_SOT_SMD:SOT-23-6", lcsc="C7722", pads=pads)
    ee = EEFootprint("C7722", "SOT-23-6", pads=[EEPad(n, x, y, 1.325, 0.6) for n, x, y in SOT236],
                     pin_names={"1": "EN", "2": "VIN", "3": "BST", "4": "SW", "5": "FB", "6": "GND"})
    c = fit_part(part, ee)
    assert c.verdict == RED and has(c, "pinout differs from the symbol"), c.reasons
    numbers = row_for(part, Correction(0.0))
    for corr in (c, None):
        res = check_row(part, numbers, ee, ORIGIN, corr=corr)
        assert (res.status, res.verdict) == ("pinout", RED), res.messages
        assert not any("another placement puts them" in m for m in res.messages), res.messages
    from jlcplace.model import Board
    run = check(Board("t.kicad_pcb", ORIGIN, [part]), [numbers], {"U3": Resolved(part, ee, c, "fit")})
    assert run.exit_code() == 2


def test_names_that_differ_without_a_permutation_make_the_row_yellow():
    """A bipolar transistor (B/E/C) where the symbol is a MOSFET (G/D/S): the names say nothing about the placement,
    but the part may well be the wrong one. Yellow in the fit (as before) and now in the row."""
    part, ee = sot23(), ee_sot23(("B", "E", "C"))
    c = fit_part(part, ee)
    assert c.verdict == YELLOW and has(c, "3 pin name(s) differ (0 agree)") and not has(c, "pinout differs")
    assert has(c, "check the LCSC part's pinout against the symbol"), c.reasons
    for corr in (c, None):
        res = check_row(part, row_for(part, c), ee, ORIGIN, corr=corr)
        assert (res.status, res.verdict) == ("ok", YELLOW), res.messages
        msg = next(m for m in res.messages if "pin names differ" in m)
        assert msg.startswith("3 pin names differ from the symbol: pad 1 is B on the LCSC part, G on the board")
        assert msg.endswith("check the LCSC part's pinout against the symbol")


def test_harmless_name_differences_leave_the_row_green():
    """Names swapped between two pads of one net, names a non-polar part's terminals carry, and a part without
    informative names: nothing for the row to mind."""
    part, ee = sot23(nets=("/G", "/X", "/X")), ee_sot23(("G", "S", "D"))
    c = fit_part(part, ee)
    assert c.verdict != RED and not has(c, "pinout differs"), c.reasons
    res = check_row(part, row_for(part, c), ee, ORIGIN, corr=c)
    assert res.verdict == GREEN and res.status == "ok", res.messages
    assert any(m.startswith("EasyEDA pin names on KiCad pads of other functions, on their own nets") for m in
               res.messages), res.messages
    pads = [Pad(n, x, 0.0, 0.6, 0.6, net=net, pin_name=name)
            for n, x, net, name in (("1", -0.5, "/A", "P"), ("2", 0.5, "/B", "N"))]
    r1 = Part("R1", TOP, 110.0, 95.0, 0.0, footprint="Resistor_SMD:R_0402_1005Metric", lcsc="C25744", pads=pads)
    ee = EEFootprint("C25744", "R0402", pads=[EEPad("1", -0.5, 0.0, 0.6, 0.6), EEPad("2", 0.5, 0.0, 0.6, 0.6)],
                     pin_names={"1": "N", "2": "P"})
    c = fit_part(r1, ee)
    res = check_row(r1, row_for(r1, c), ee, ORIGIN, corr=c)
    assert res.verdict == GREEN and not any("pin" in m for m in res.messages), res.messages


def test_a_person_can_settle_a_pinout():
    """A pinout red until someone records a decision: a manual correction (the datasheet says EasyEDA's symbol is
    wrong) makes the row yellow, an assembled board that works makes it verified; JLC's preview shows where the body
    sits, not the pin functions, so a preview verification settles nothing about them."""
    part, ee = sot23(), ee_sot23(("G", "S", "D"))
    c = fit_part(part, ee)
    row = row_for(part, c)
    manual = dataclasses.replace(c, status="manual", verdict=YELLOW,
                                 reasons=["accepted manually (me): EasyEDA's symbol is wrong"] + c.reasons)
    res = check_row(part, row, ee, ORIGIN, corr=manual)
    assert (res.status, res.verdict) == ("ok", YELLOW), res.messages
    assert any(m.startswith("pinout differs from the symbol") and m.endswith("accepted by the manual correction")
               for m in res.messages), res.messages
    board = dataclasses.replace(c, status="board-verified", verdict=VERIFIED)
    assert check_row(part, row, ee, ORIGIN, corr=board).verdict == VERIFIED
    preview = dataclasses.replace(c, status="preview-verified", verdict=VERIFIED)
    res = check_row(part, row, ee, ORIGIN, corr=preview)
    assert (res.status, res.verdict) == ("pinout", RED) and "JLC's preview does not show" in res.messages[0]
    # the row turned off its pads is off copper first; the names are only a note on it then
    res = check_row(part, turned(row, 90), ee, ORIGIN, corr=c)
    assert res.status == "off-copper"


def test_a_stored_pinout_correction_keeps_the_row_red_without_the_record():
    """In CI without EasyEDA records the row is simulated with the KiCad pads through the stored correction, which
    carry no pin names: the stored red verdict caps it, and says why."""
    part, ee = sot23(), ee_sot23(("G", "S", "D"))
    c = fit_part(part, ee)
    from jlcplace.model import Board
    board = Board("t.kicad_pcb", ORIGIN, [part])
    run = check(board, [row_for(part, c)], {"Q1": Resolved(part, NotFound("C2500", "not cached"), c, "db")})
    res = run.results[0]
    assert res.verdict == RED and res.messages[0].startswith("red because the stored auto correction is red: pinout "
                                                             "differs from the symbol"), res.messages


# ------------------------------------------------------------------------------------------------------- vocabularies

def part_and_record(kicad, ee, ref="U1"):
    """A KiCad part, kicad = [(number, x, y, w, h, pin function, net)], and an EasyEDA record, ee = [(number, x, y,
    w, h, pin name)]: the two libraries' spellings of one part (hand-made: no EasyEDA data in the repository)."""
    pads = [Pad(n, x, y, w, h, net=net, pin_name=name) for n, x, y, w, h, name, net in kicad]
    part = Part(ref, TOP, 120.0, 80.0, 0.0, footprint="Test:Test", lcsc="C1", pads=pads)
    rec = EEFootprint("C1", "TEST", pads=[EEPad(n, x, y, w, h) for n, x, y, w, h, _ in ee],
                      pin_names={n: name for n, _, _, _, _, name in ee})
    return part, rec


def judged(part, ee):
    """(fit verdict, row verdict, row status, row messages) of jlcplace's own row."""
    c = fit_part(part, ee)
    res = check_row(part, row_for(part, c), ee, ORIGIN, corr=c)
    return c.verdict, res.verdict, res.status, res.messages


def swap_names(spec, a, b):
    """The KiCad side with the functions (and nets: the schematic wires by name) of pads a and b exchanged."""
    out = [list(q) for q in spec]
    ia, ib = (next(i for i, q in enumerate(out) if q[0] == n) for n in (a, b))
    out[ia][5:], out[ib][5:] = out[ib][5:], out[ia][5:]
    return [tuple(q) for q in out]


# a SOT-223 LDO on KiCad's SOT-223-3_TabPin2 (the tab is pad 2, VO): EasyEDA numbers the tab 4 and names it TAB;
# EasyEDA's footprint is drawn half a turn from KiCad's
SOT223_KICAD = [("1", -3.15, -2.3, 2.0, 1.5, "GND", "GND"), ("2", -3.15, 0.0, 2.0, 1.5, "VO", "+3V3"),
                ("3", -3.15, 2.3, 2.0, 1.5, "VI", "+5V"), ("2", 3.15, 0.0, 2.0, 3.8, "VO", "+3V3")]
SOT223_EE = [("1", 3.15, 2.3, 2.0, 1.5, "GND"), ("2", 3.15, 0.0, 2.0, 1.5, "Out"), ("3", 3.15, -2.3, 2.0, 1.5, "In"),
             ("4", -3.15, 0.0, 2.0, 3.8, "TAB")]
# a TO-263 1086 LDO whose tab (EasyEDA pad 4, "EP") is its output, on KiCad's TO-263-3_TabPin2 with LM1084 names
TO263_KICAD = [("1", 5.13, 2.54, 4.5, 1.5, "GND", "GND"), ("2", 5.13, 0.0, 4.5, 1.5, "VO", "+3V3"),
               ("3", 5.13, -2.54, 4.5, 1.5, "VI", "+5V"), ("2", -5.13, 0.0, 10.0, 10.8, "VO", "+3V3")]
TO263_EE = [("1", -5.13, -2.54, 4.5, 1.5, "GND"), ("2", -5.13, 0.0, 4.5, 1.5, "Vout"),
            ("3", -5.13, 2.54, 4.5, 1.5, "Vin"), ("4", 5.13, 0.0, 10.0, 10.8, "EP")]


@pytest.mark.parametrize("kicad, ee", [(SOT223_KICAD, SOT223_EE), (TO263_KICAD, TO263_EE)], ids=["SOT-223", "TO-263"])
def test_a_tab_named_by_where_it_is_is_not_a_ground(kicad, ee):
    """TAB, EP, PAD, EPAD and THERMAL say where a pad is, not what it is: a regulator's tab is often its output. A
    correct board is not red (as before the pinout check), and the CPL is written."""
    fit, row, status, msgs = judged(*part_and_record(kicad, ee))
    assert fit != RED and row == GREEN and status == "ok", msgs


# a TO-252 regulator, EasyEDA's names spelled out (Input, Output) against KiCad's VI, VO
TO252_KICAD = [("1", 3.3, 2.28, 2.8, 1.3, "VI", "VIN"), ("3", 3.3, -2.28, 2.8, 1.3, "VO", "VOUT"),
               ("2", -3.3, 0.0, 6.5, 6.0, "GND", "GND")]
TO252_EE = [("1", -3.3, -2.28, 2.8, 1.3, "Input"), ("3", -3.3, 2.28, 2.8, 1.3, "Output"),
            ("2", 3.3, 0.0, 6.5, 6.0, "GND")]
# an LM358 in SOIC-8: EasyEDA's 1OUT, 1IN-, 1IN+, GND, ... against KiCad's ~, -, +, V-, ...
LM358_KICAD = [(str(k + 1), -1.905 + 1.27 * (k % 4) if k < 4 else 1.905 - 1.27 * (k - 4), 2.7 if k < 4 else -2.7, 0.6,
                1.95, name, f"N{k + 1}") for k, name in enumerate(("~", "-", "+", "V-", "+", "-", "~", "V+"))]
LM358_EE = [(n, x, y, w, h, name) for (n, x, y, w, h, _, _), name in zip(LM358_KICAD, (
    "1OUT", "1IN-", "1IN+", "GND", "2IN+", "2IN-", "2OUT", "VCC"))]
# the signal pins of a USB-C receptacle: EasyEDA's DP1 / DN1 / DP2 / DN2 against KiCad's D+ / D-
USBC = [("B8", "SBU2", "SBU2"), ("A5", "CC1", "CC1"), ("B7", "D-", "DM"), ("A6", "D+", "DP"), ("A7", "D-", "DM"),
        ("B6", "D+", "DP"), ("A8", "SBU1", "SBU1"), ("B5", "CC2", "CC2")]
USBC_KICAD = [(n, -1.75 + 0.5 * k, -2.47, 0.3, 1.3, name, net) for k, (n, name, net) in enumerate(USBC)]
USBC_EE = [(n, -1.75 + 0.5 * k, -2.47, 0.3, 1.3, {"D+": "DP", "D-": "DN"}.get(name, name)
            + ("1" if name[0] == "D" and n[0] == "A" else "2" if name[0] == "D" else ""))
           for k, (n, name, _) in enumerate(USBC)]


@pytest.mark.parametrize("kicad, ee, a, b", [(TO252_KICAD, TO252_EE, "1", "3"), (LM358_KICAD, LM358_EE, "4", "8"),
                                             (USBC_KICAD, USBC_EE, "A6", "A7")],
                         ids=["regulator VI/VO", "op-amp supplies", "USB-C D+/D-"])
def test_a_permutation_spelled_otherwise_by_the_two_libraries_is_red(kicad, ee, a, b):
    """The libraries spell the pins differently (Input / VI, GND / V-, VCC / V+, DP1 / D+), which says nothing: the
    right board is green. With two of the board's pins swapped (their nets follow their names), the LCSC part sits
    on the wrong nets: red pinout, never the same yellow as the right board."""
    fit, row, status, msgs = judged(*part_and_record(kicad, ee))
    assert row == GREEN and fit != RED, msgs
    fit, row, status, msgs = judged(*part_and_record(swap_names(kicad, a, b), ee))
    assert (fit, row, status) == (RED, RED, "pinout"), msgs


def test_a_few_names_spelled_otherwise_are_a_message_not_a_verdict():
    """One name of five differs (ALERT against INT), the rest agree: a message, no yellow and no checklist item; most
    names differing (a bipolar transistor's B/E/C where the symbol is a MOSFET's G/D/S) stays yellow."""
    names = (("VDD", "VDD"), ("GND", "GND"), ("SDA", "SDA"), ("SCL", "SCL"), ("ALERT", "INT"))
    kicad = [(str(k + 1), -1.0 + 0.5 * k, 0.0, 0.25, 0.8, kn, f"N{k}") for k, (kn, _) in enumerate(names)]
    ee = [(str(k + 1), -1.0 + 0.5 * k, 0.0, 0.25, 0.8, en) for k, (_, en) in enumerate(names)]
    fit, row, status, msgs = judged(*part_and_record(kicad, ee))
    assert row == GREEN and status == "ok", msgs
    assert any(m.startswith("1 pin name differs from the symbol (4 agree): pad 5 is INT on the LCSC part, ALERT on "
                            "the board") for m in msgs), msgs
    assert not any("check the LCSC part's pinout" in m for m in msgs)


@pytest.mark.parametrize("kicad, ee", [
    # KiCad's LED_RGB names (A, RK, GK, BK) against EasyEDA's (+, R-, G-, B-)
    ([("1", 0.33, -0.52, 0.36, 0.69, "A", "VDD"), ("2", 0.33, 0.52, 0.36, 0.69, "RK", "R"),
      ("3", -0.33, 0.52, 0.36, 0.69, "GK", "G"), ("4", -0.33, -0.52, 0.36, 0.69, "BK", "B")],
     [("1", 0.33, -0.52, 0.36, 0.69, "+"), ("2", 0.33, 0.52, 0.36, 0.69, "R-"), ("3", -0.33, 0.52, 0.36, 0.69, "G-"),
      ("4", -0.33, -0.52, 0.36, 0.69, "B-")]),
    # a crystal with ground pins: KiCad's Crystal_GND24 calls them G
    ([("1", -1.1, 0.85, 1.4, 1.2, "1", "X1"), ("2", 1.1, 0.85, 1.4, 1.2, "G", "GND"),
      ("3", 1.1, -0.85, 1.4, 1.2, "3", "X2"), ("4", -1.1, -0.85, 1.4, 1.2, "G", "GND")],
     [("1", -1.1, 0.85, 1.4, 1.2, "OSC1"), ("2", 1.1, 0.85, 1.4, 1.2, "GND"), ("3", 1.1, -0.85, 1.4, 1.2, "OSC2"),
      ("4", -1.1, -0.85, 1.4, 1.2, "GND")]),
    # an nRF52's names, '_' in EasyEDA where KiCad has '/'
    ([(str(k + 1), -1.0 + 0.5 * k, 0.0, 0.25, 0.8, name.replace("_", "/"), f"N{k}") for k, name in enumerate(
        ("P0.00_XL1", "P0.01_XL2", "P0.02_AIN0", "P0.03_AIN1", "VDD"))],
     [(str(k + 1), -1.0 + 0.5 * k, 0.0, 0.25, 0.8, name) for k, name in enumerate(
         ("P0.00_XL1", "P0.01_XL2", "P0.02_AIN0", "P0.03_AIN1", "VDD"))]),
    # an LMV321: IN+ / VSS / IN- / OUT / VDD against KiCad's + / V- / - / ~ / V+
    ([(n, x, y, 0.6, 1.0, name, f"N{n}") for n, x, y, name in (("1", -1.1, -0.95, "+"), ("2", -1.1, 0.0, "V-"),
                                                             ("3", -1.1, 0.95, "-"), ("4", 1.1, 0.95, "~"),
                                                             ("5", 1.1, -0.95, "V+"))],
     [(n, x, y, 0.6, 1.0, name) for n, x, y, name in (("1", -1.1, -0.95, "IN+"), ("2", -1.1, 0.0, "VSS"),
                                                      ("3", -1.1, 0.95, "IN-"), ("4", 1.1, 0.95, "OUT"),
                                                      ("5", 1.1, -0.95, "VDD"))]),
], ids=["RGB LED", "crystal", "nRF52", "LMV321"])
def test_correct_parts_whose_libraries_spell_pins_otherwise_stay_green(kicad, ee):
    """No noise: the libraries' vocabularies differ ('-' for K, G for GND, '_' for '/', V+ for VDD) on parts that are
    wired right; their rows stay green, with no checklist item asking for the pinout."""
    fit, row, status, msgs = judged(*part_and_record(kicad, ee))
    assert row == GREEN and status == "ok" and not any("pinout" in m for m in msgs), msgs


# ------------------------------------------------------------------------------------------------------- real records

SCRATCH = Path(os.environ.get("JLCPLACE_TEST_EASYEDA", "/tmp/claude-0/-home-user-dial-me-spinner/"
                              "3708159a-bc3b-5167-8507-f5b08e1ce6c9/scratchpad/jlc"))
RECORDS = [SCRATCH / "ee_raw", SCRATCH / "src/z2amiller_jlccheck/tests/fixtures/jlcfootprint/easyeda"]


@pytest.mark.cache
@pytest.mark.skipif(not RECORDS[0].is_dir(), reason=f"needs cached EasyEDA records in {RECORDS[0]}")
def test_real_records_are_quiet_when_the_names_match_and_red_when_two_pins_swap():
    """Every cached record with three or more named pads, on a KiCad part made of its own pads: with the LCSC part's
    names as the pin functions nothing is said about pins, and with the functions of two pins swapped the row is red
    "pinout". Names equal up to a trailing index (INT1 / INT2, P1.01 / P1.03) compare as unknown (parts.compare_names,
    for symmetric connectors), so such a swap is not seen."""
    seen = 0
    for f in sorted(f for d in RECORDS if d.is_dir() for f in d.glob("C*.json")):
        ee = from_response(f.stem, json.loads(f.read_text(encoding="utf-8")))
        if not isinstance(ee, EEFootprint):
            continue
        pads = [p for p in ee.pads if p.layer != "bottom"]
        names = {norm_number(k): v for k, v in ee.pin_names.items()}
        own = [names.get(norm_number(p.number), "") for p in pads]
        named = [i for i, p in enumerate(pads) if name_tokens(own[i], p.number)]
        pair = next(((i, j) for i in named for j in named if i < j and compare_names(
            name_tokens(own[i], pads[i].number), name_tokens(own[j], pads[j].number)) < 0), None)
        if len(named) < 3 or pair is None:
            continue

        def part(functions, ee=ee, pads=pads):
            return Part("U1", TOP, 50.0, -50.0, 0.0, footprint="Test:Test", lcsc=ee.lcsc, pads=[
                Pad(p.number, p.x, p.y, max(p.w, 0.05), max(p.h, 0.05), angle=p.angle, net=f"N{i}",
                    pin_name=functions[i], kind="tht" if p.layer == "multi" else "smd") for i, p in enumerate(pads)])

        quiet = part(own)
        c = fit_part(quiet, ee)
        res = check_row(quiet, row_for(quiet, c), ee, ORIGIN, corr=c, alt_readings=False)
        assert c.verdict == GREEN and res.verdict == GREEN and not any(
            "pinout" in m or "pin names differ" in m for m in res.messages + c.reasons), (ee.lcsc, c.reasons, res)
        swapped = list(own)
        swapped[pair[0]], swapped[pair[1]] = own[pair[1]], own[pair[0]]
        wrong = part(swapped)
        c = fit_part(wrong, ee)
        res = check_row(wrong, row_for(wrong, c), ee, ORIGIN, corr=c, alt_readings=False)
        assert c.verdict == RED and (res.status, res.verdict) == ("pinout", RED), (ee.lcsc, c.reasons, res.messages)
        seen += 1
    assert seen >= 20
