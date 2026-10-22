"""gerber.py: the RS-274X / X2 reader, layer functions from attributes, job files and names, and the origin check.

The conventions are proven, not assumed:
  * Gerber y is up and KiCad plots board point b for plot origin o at g = (bx - ox, oy - by): every TO.P pad of the
    Gerber sets kicad-cli plots from the dial-me-spinner boards and KiCad's demo boards (and the released sets) is
    there, from the drill/place origin and from the page origin;
  * gerber_origin recovers that origin to 1e-3 mm, and says how: x2-pins with KiCad's X2 attributes or its X1
    comments (--no-x2), pad-voting without the netlist attributes (--no-netlist), with regions instead of aperture
    macros, and with every attribute stripped (a set named by its files only);
  * synthetic sets cover every aperture and macro primitive, arcs, regions, polarity, step and repeat, aperture
    transformations and blocks, inch units, leading and trailing zero omission, and the voting's robustness to
    missing pads, fiducials and vias, mirrored and scaled sets, stale and foreign Gerbers.
"""
import math
import os
import random
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

import numpy as np
import pytest

from jlcplace import gerber as gb
from jlcplace.board import load_board
from jlcplace.convention import board_to_cpl, library_to_board
from jlcplace.cpl import read_cpl
from jlcplace.model import BOTTOM, RED, TOP, YELLOW, Board, CplRow, Pad, Part

DIAL = Path(os.environ.get("JLCPLACE_DIAL_ME_SPINNER", "/home/user/dial-me-spinner")) / "hardware"
JLC = Path(os.environ.get("JLCPLACE_TEST_EASYEDA", "/tmp/claude-0/-home-user-dial-me-spinner/"
                          "3708159a-bc3b-5167-8507-f5b08e1ce6c9/scratchpad/jlc"))
BOARDS = ("main", "base")
DEMOS = Path("/usr/share/kicad/demos")
# demo boards with their own drill/place origin, and boards without one (an origin is set in a copy), custom pads,
# chamfered and offset pads among them
DEMO_BOARDS = [DEMOS / p for p in ("complex_hierarchy/complex_hierarchy.kicad_pcb",
                                   "custom_pads_test/custom_pads_test.kicad_pcb",
                                   "royalblue54L_feather/RoyalBlue54L-Feather.kicad_pcb",
                                   "stickhub/StickHub.kicad_pcb",
                                   "kit-dev-coldfire-xilinx_5213/kit-dev-coldfire-xilinx_5213.kicad_pcb")]
KICAD_CLI = shutil.which("kicad-cli")
# Gerber sets other tools wrote: kicad-jlcpcb-tools' golden outputs (KiCad 9 and 10, its own file names, with its
# CPLs), and the fab outputs shipped with KiCad's demos (KiCad 7.0.9 with X1 attribute comments; a 2-pad antenna)
GOLDEN = JLC / "src/Bouni_kicad-jlcpcb-tools/tests/golden"
THIRD_PARTY = [pytest.param(GOLDEN / f"{b}/{b}.kicad_pcb", GOLDEN / f"{b}/{v}/GERBER-{b}", o,
                            GOLDEN / f"{b}/{v}/CPL-{b}.csv", id=f"jlcpcb-tools-{b}-kicad{v}")
               for b, o in (("two_layer", (100.0, 136.0)), ("four_layer", (100.0, 124.0))) for v in ("9", "10")]
THIRD_PARTY += [pytest.param(b, g, o, None, id=b.stem) for b, g, o in (
    (DEMOS / "tiny_tapeout/tinytapeout-demo.kicad_pcb", DEMOS / "tiny_tapeout/pcba/gerber/v0p9p4", (56.5, 134.5)),
    (DEMOS / "royalblue54L_feather/nfc_antenna/RoyalBlue54L-NFC-Antenna/RoyalBlue54L-NFC-Antenna.kicad_pcb",
     DEMOS / "royalblue54L_feather/nfc_antenna/RoyalBlue54L-NFC-Antenna/jlcpcb/production_files/"
     "GERBER-RoyalBlue54L-NFC-Antenna.zip", (0.0, 0.0))) if b.exists()]      # no drill/place origin: (0, 0)


def pcb(b: str) -> Path:
    return DIAL / f"kicad/{b}/dial-me-spinner-{b}.kicad_pcb"


def need(path: Path) -> None:
    if not path.exists():
        pytest.skip(f"{path} is not on this machine (JLCPLACE_DIAL_ME_SPINNER / JLCPLACE_TEST_EASYEDA say where)")


def one(text: str, name: str = "t.gbr") -> gb.GerberLayer:
    return gb.parse_gerber(text, name)


def area(poly) -> float:
    a = np.asarray(poly, float)
    return 0.5 * abs(float(np.dot(a[:, 0], np.roll(a[:, 1], -1)) - np.dot(a[:, 1], np.roll(a[:, 0], -1))))


MM = "%FSLAX46Y46*%\n%MOMM*%\n"


# ------------------------------------------------------------------------------------------------------- coordinates

def test_coordinate_formats_units_and_modal_coordinates():
    """Leading zeros omitted (L) and trailing zeros omitted (T), inch and mm, explicit decimal points, signs,
    coordinates that keep their last value, the deprecated modal D code, and incremental coordinates."""
    la = one("%FSLAX24Y24*%%MOIN*%%ADD10C,0.01*%D10*X15000Y-5000D03*Y2500D03*X-1.5Y.25D03*X20000*%LPD*%M02*")
    pts = [(round(p.x, 9), round(p.y, 9)) for p in la.flashes]
    assert pts == [(38.1, -12.7), (38.1, 6.35), (-38.1, 6.35), (50.8, 6.35)]   # the last: D03 repeated (modal)
    assert la.units == "in" and la.flashes[0].w == pytest.approx(0.254)
    la = one("%FSTAX24Y24*%%MOIN*%%ADD10C,0.01*%D10*X015Y-0005D03*X0015Y15D03*M02*")
    assert [(round(p.x, 9), round(p.y, 9)) for p in la.flashes] == [(38.1, -1.27), (3.81, 381.0)]
    la = one("%FSLAX46Y46*%%MOMM*%%ADD10C,0.1*%D10*X-3871783Y-28381273D03*")
    assert (la.flashes[0].x, la.flashes[0].y) == (pytest.approx(-3.871783), pytest.approx(-28.381273))
    la = one("%FSLIX33Y33*%%MOMM*%%ADD10C,0.1*%D10*X1000Y2000D03*X500Y-500D03*")       # incremental
    assert [(round(p.x, 9), round(p.y, 9)) for p in la.flashes] == [(1.0, 2.0), (1.5, 1.5)]
    la = one("%FSLAX33Y33*%G70*%ADD10C,0.1*%D10*X1000Y0D03*G71*X1000Y0D03*")      # deprecated unit codes
    assert [round(p.x, 9) for p in la.flashes] == [25.4, 1.0]


def test_errors_name_the_file_and_the_block():
    with pytest.raises(gb.GerberError, match=r"x\.gtl: block \d+: .*D11 is not defined"):
        one(MM + "%ADD10C,0.1*%D11*X0Y0D03*", "x.gtl")
    with pytest.raises(gb.GerberError, match=r"before the format statement"):
        one("%MOMM*%%ADD10C,0.1*%D10*X100Y100D03*")
    with pytest.raises(gb.GerberError, match="undefined macro"):
        one(MM + "%ADD10Nope,1*%")
    assert gb.GerberError.__mro__[1] is ValueError


# ------------------------------------------------------------------------------------------------------- apertures

def test_standard_apertures_and_holes():
    la = one(MM + "%ADD10C,1.5*%%ADD11R,2X1X0.4*%%ADD12O,1X3*%%ADD13P,2X6X30*%%ADD14C,1.5X0.5*%"
                  "D10*X1000000Y2000000D03*D11*X5000000Y0D03*D12*X0Y0D03*D13*X0Y-5000000D03*D14*X9000000Y0D03*")
    c, r, o, p, h = la.flashes
    assert (c.x, c.y, c.w, c.h, c.aperture) == (pytest.approx(1), pytest.approx(2), pytest.approx(1.5),
                                                pytest.approx(1.5), "C")
    assert area(c.poly) == pytest.approx(math.pi * 0.75 ** 2, rel=2e-3)
    assert (r.w, r.h, area(r.poly)) == (pytest.approx(2), pytest.approx(1), pytest.approx(2))
    assert not r.contains((5.0, 0.0)) and r.contains((5.5, 0.0))                    # the hole is clear
    assert (o.w, o.h) == (pytest.approx(1), pytest.approx(3))
    assert area(o.poly) == pytest.approx(2 * 1 + math.pi * 0.25, rel=2e-3)
    assert len(p.poly) == 6 and p.poly[0] == pytest.approx((math.cos(math.pi / 6), -5 + math.sin(math.pi / 6)))
    assert not h.contains((9.0, 0.0)) and h.contains((9.6, 0.0))
    assert [f.params for f in la.flashes][:2] == [(1.5,), (2.0, 1.0, 0.4)]


KICAD_MACROS = """%AMRoundRect*
0 Rectangle with rounded corners*
0 $1 Rounding radius*
0 $2 $3 $4 $5 $6 $7 $8 $9 X,Y pos of 4 corners*
4,1,4,$2,$3,$4,$5,$6,$7,$8,$9,$2,$3,0*
1,1,$1+$1,$2,$3*
1,1,$1+$1,$4,$5*
1,1,$1+$1,$6,$7*
1,1,$1+$1,$8,$9*
20,1,$1+$1,$2,$3,$4,$5,0*
20,1,$1+$1,$4,$5,$6,$7,0*
20,1,$1+$1,$6,$7,$8,$9,0*
20,1,$1+$1,$8,$9,$2,$3,0*%
%AMRotRect*
0 Rectangle, with rotation*
21,1,$1,$2,0,0,$3*%
%AMHorizOval*
20,1,$1,$2,$3,$4,$5,0*
1,1,$1,$2,$3*
1,1,$1,$4,$5*%
%AMOutline4P*
4,1,4,$1,$2,$3,$4,$5,$6,$7,$8,$1,$2,$9*%
%AMFreePoly0*
4,1,6,-1.000000,-0.500000,3.000000,-0.500000,3.000000,0.500000,0.500000,0.500000,
0.500000,2.000000,-1.000000,2.000000,-1.000000,-0.500000,$1*%
"""


def test_kicad_aperture_macros():
    """KiCad 6-9's macros, with the parameters KiCad writes: a rounded rectangle (a polygon, four circles and four
    lines: its convex hull is the pad), a rotated rectangle, an oval, a trapezoid and a custom pad's outline turned
    by its parameter, flashed away from the origin."""
    la = one(MM + KICAD_MACROS + "%ADD10RoundRect,0.25X-0.475X-0.25X0.475X-0.25X0.475X0.25X-0.475X0.25X0*%"
                                 "%ADD11RotRect,0.7X0.7X30*%%ADD12HorizOval,1.5X-0.5X0X0.5X0X0*%"
                                 "%ADD13Outline4P,-1X-0.5X1X-0.5X0.5X0.5X-0.5X0.5X180*%%ADD14FreePoly0,90*%"
                                 "D10*X10000000Y5000000D03*D11*X0Y0D03*D12*X0Y0D03*D13*X0Y0D03*D14*X2000000Y0D03*")
    rr, rot, oval, trap, free = la.flashes
    assert (rr.x, rr.y, rr.w, rr.h) == (pytest.approx(10), pytest.approx(5), pytest.approx(1.45), pytest.approx(1.0))
    assert area(rr.poly) == pytest.approx(1.45 * 1.0 - (4 - math.pi) * 0.25 ** 2, rel=1e-3)
    assert rr.aperture == "RoundRect" and rr.at == (10.0, 5.0)
    half = 0.35 * (math.cos(math.radians(30)) + math.sin(math.radians(30)))
    assert (rot.w, rot.h) == (pytest.approx(2 * half), pytest.approx(2 * half))
    assert area(rot.poly) == pytest.approx(0.49)
    assert (oval.w, oval.h) == (pytest.approx(2.5), pytest.approx(1.5))
    assert area(trap.poly) == pytest.approx(1.5) and trap.poly[0] == pytest.approx((1.0, 0.5))   # turned 180
    # the custom outline turned 90 degrees counter-clockwise: (x, y) -> (-y, x), then moved to (2, 0)
    assert (free.x, free.y, free.w, free.h) == (pytest.approx(2 - 0.75), pytest.approx(1.0), pytest.approx(2.5),
                                                pytest.approx(4.0))
    assert free.at == (2.0, 0.0) and free.contains((2.0 - 1.5, 0.0)) and not free.contains((2.0 - 1.5, 1.5))


def test_every_macro_primitive_variables_and_arithmetic():
    """Primitives 1, 2, 20, 21, 22, 4, 5, 6 and 7; rotation about the macro's origin; exposure off erasing what
    the macro drew before; variables defined in the macro; + - x / and parentheses with their precedence."""
    macros = """%AMCIRC*1,1,$1,$2,$3,90*%
%AMVEC*2,1,0.5,0,0,2,0,0*20,1,0.5,0,1,2,1,90*%
%AMLL*22,1,2,1,1,1,0*%
%AMPOLY*5,1,8,0,0,2,22.5*%
%AMTHERM*7,0,0,2,1,0.2,0*%
%AMMOIRE*6,0,0,3,0.25,0.25,2,0.1,4,0*%
%AMMATH*$3=($1+$2)/2*$4=-$3x2+1*21,1,$4,$3,0,0,0*1,0,0.2,0,0*%
"""
    la = one(MM + macros + "%ADD10CIRC,1X2X0*%%ADD11VEC*%%ADD12LL*%%ADD13POLY*%%ADD14THERM*%%ADD15MOIRE*%"
                           "%ADD16MATH,1X3*%" + "".join(f"D{d}*X0Y0D03*" for d in range(10, 17)))
    circ, vec, ll, poly, therm, moire, mth = la.flashes
    assert (circ.x, circ.y, circ.w) == (pytest.approx(0), pytest.approx(2), pytest.approx(1))  # (2, 0) turned 90
    assert vec.contains((1.0, 0.0)) and vec.contains((-1.0, 1.0)) and not vec.contains((1.0, 1.0))
    assert (ll.x, ll.y, ll.w, ll.h) == (pytest.approx(2), pytest.approx(1.5), pytest.approx(2), pytest.approx(1))
    assert len(poly.poly) == 8 and poly.w == pytest.approx(2 * math.cos(math.radians(22.5)))
    assert therm.contains((0.75, 0.3)) and not therm.contains((0.0, 0.0)) and not therm.contains((0.75, 0.0))
    assert moire.w == pytest.approx(4.0) and moire.contains((1.4, 0.0)) and not moire.contains((1.1, 0.3))
    assert moire.contains((1.9, 0.0)) and moire.contains((0.0, 0.0))                 # the crosshair
    # $3 = 2, $4 = -2 x 2 + 1 = -3 (a negative width draws nothing smaller: |w| is the box)
    assert (mth.w, mth.h) == (pytest.approx(3), pytest.approx(2)) and not mth.contains((0.0, 0.0))


def test_other_tools_conventions():
    """Eagle's octagon macro (X multiplies inside a macro), Altium-style 2:5 inch files with G54 aperture selection
    and the deprecated IP / LN / OF / SF commands at their identity values."""
    la = one("%FSLAX24Y24*%%MOIN*%%AMOC8*5,1,8,0,0,1.08239X$1,22.5*%%ADD10OC8,0.06*%G54D10*X10000Y10000D03*")
    (oc,) = la.flashes
    r = 1.08239 * 0.06 / 2 * 25.4
    assert len(oc.poly) == 8 and oc.w == pytest.approx(2 * r * math.cos(math.radians(22.5)))
    assert (oc.x, oc.y) == (pytest.approx(25.4), pytest.approx(25.4))
    la = one("%FSLAX25Y25*%%MOIN*%%IPPOS*%%LNTOP*%%OFA0B0*%%SFA1B1*%%ADD11R,0.05X0.02*%G54D11*G01*"
             "X150000Y-50000D03*M02*", "x.GTL")
    assert (la.kind, la.side, la.units, la.notes) == ("copper", TOP, "in", [])
    assert (la.flashes[0].x, la.flashes[0].y, la.flashes[0].w) == pytest.approx((38.1, -12.7, 1.27))


def test_aperture_transformations_and_blocks():
    """LM / LR / LS change the apertures flashed after them (mirror, then scale, then rotate), not the coordinates;
    an AB block is an aperture made of objects, flashed like one."""
    tri = "%AMTRI*4,1,3,0,0,2,0,0,1,0,0,0*%"
    la = one(MM + tri + "%ADD10R,2X1*%%ADD11TRI*%D10*X0Y0D03*%LR90*%X0Y0D03*%LS2*%X0Y0D03*%LR0*%%LS1*%"
                        "D11*%LMX*%X0Y0D03*%LMN*%X0Y0D03*")
    r0, r90, r90s2, mirrored, plain = la.flashes
    assert (r0.w, r0.h, r90.w, r90.h, r90s2.w, r90s2.h) == pytest.approx((2, 1, 1, 2, 2, 4))
    assert mirrored.contains((-1.5, 0.2)) and not mirrored.contains((1.5, 0.2)) and plain.contains((1.5, 0.2))
    la = one(MM + "%ADD10C,0.5*%%ABD20*%D10*X0Y0D03*X1000000Y0D03*%AB*%D20*X5000000Y5000000D03*"
                  "X-5000000Y0D03*")
    assert sorted((round(p.x, 6), round(p.y, 6)) for p in la.flashes) == [(-5, 0), (-4, 0), (5, 5), (6, 5)]


# ------------------------------------------------------------------------------------------------------- objects

def test_arcs_regions_and_extents():
    """G75 arcs (a full circle as KiCad writes a round outline: two half arcs), a G74 single quadrant arc with
    unsigned offsets, and G36 / G37 regions with arc segments, one object per contour."""
    prof = one(MM + "%TF.FileFunction,Profile,NP*%%ADD10C,0.1*%D10*X32000000Y0D02*G75*G02*X-32000000Y0I-32000000"
                    "J0D01*G02*X32000000Y0I32000000J0D01*G01*M02*", "e.gm1")
    assert (prof.kind, prof.side) == ("profile", "")
    assert prof.bbox() == pytest.approx((-32, -32, 32, 32), abs=1e-9)
    gs = gb.GerberSet(path="", layers=[prof])
    assert gs.extents == pytest.approx((-32, -32, 32, 32), abs=1e-9)
    q = one(MM + "%ADD10C,0.1*%D10*X1000000Y0D02*G74*G03*X0Y1000000I1000000J0D01*")      # quarter, centre (0, 0)
    (d,) = q.draws
    assert d.arc[:2] == pytest.approx((0, 0)) and d.arc[2] is True
    assert max(abs(math.hypot(*p) - 1.0) for p in d.points()) < 1e-6
    # a rounded rectangle region (as KiCad plots pads with --disable-aperture-macros), then a two-contour region
    rr = ("G36*G01*X-500000Y-250000D02*X500000Y-250000D01*G75*G03*X750000Y0I0J250000D01*G01*X750000Y0D01*"
          "G03*X500000Y250000I-250000J0D01*G01*X-500000Y250000D01*G03*X-750000Y0I0J-250000D01*"
          "G03*X-500000Y-250000I250000J0D01*G37*")
    la = one(MM + rr + "G01*G36*X0Y0D02*X1000000Y0D01*X1000000Y1000000D01*X0Y0D01*X5000000Y0D02*X6000000Y0D01*"
                       "X6000000Y1000000D01*X5000000Y0D01*G37*")
    a, t1, t2 = la.regions
    assert (a.w, a.h) == (pytest.approx(1.5), pytest.approx(0.5)) and a.kind == "region" and a.at is None
    assert area(a.poly) == pytest.approx(1.0 * 0.5 + math.pi * 0.25 ** 2, rel=5e-4)
    assert (t1.x, t2.x) == (pytest.approx(0.5), pytest.approx(5.5)) and area(t2.poly) == pytest.approx(0.5)


def test_polarity_and_step_and_repeat():
    la = one(MM + "%ADD10C,1*%%ADD11C,0.2*%D10*X0Y0D03*%LPC*%D11*X0Y0D03*%LPD*%"
                  "%SRX3Y2I5.0J4.0*%D10*X10000000Y0D03*%SR*%X0Y-10000000D03*", "p.gtl")
    assert [p.dark for p in la.flashes[:2]] == [True, False]
    assert len(la.pads()) == 1 + 6 + 1                                   # the clear flash is no pad
    stepped = sorted((round(p.x, 6), round(p.y, 6)) for p in la.flashes[2:8])
    assert stepped == [(10, 0), (10, 4), (15, 0), (15, 4), (20, 0), (20, 4)]
    assert (la.flashes[-1].x, la.flashes[-1].y) == (pytest.approx(0), pytest.approx(-10))


def test_x2_attributes_x1_comments_and_scopes():
    """TF names the layer; TA attaches to the apertures defined after it (and to regions); TO attaches to every
    object until TD deletes it; KiCad's X1 form (G04 #@! ...) is read alike; \\u escapes are decoded."""
    body = ("%TF.GenerationSoftware,KiCad,Pcbnew,9.0.9*%%TF.FileFunction,Copper,L4,Bot*%%TF.FilePolarity,Positive*%"
            + MM + "%TA.AperFunction,SMDPad,CuDef*%%ADD10C,1*%%TD*%%TA.AperFunction,ViaPad*%%ADD11C,0.6*%%TD*%"
            "%ADD12C,1*%D10*%TO.P,U1,3,VDD*%%TO.N,/a\\u002Cb*%X0Y0D03*%TO.P,U1,4*%X1000000Y0D03*%TD*%D11*"
            "X2000000Y0D03*D12*X3000000Y0D03*%TO.C,FID1*%X4000000Y0D03*%TD.C*%"
            "%TA.AperFunction,Conductor*%%TO.N,GND*%G36*X0Y0D02*X20000000Y0D01*X20000000Y20000000D01*X0Y0D01*G37*"
            "%TD*%%TA.AperFunction,SMDPad,CuDef*%%TO.P,J1,1*%G36*X0Y5000000D02*X1000000Y5000000D01*"
            "X1000000Y6000000D01*X0Y5000000D01*G37*%TD*%")
    la = one(body, "board-B_Cu.gbr")
    assert (la.function, la.kind, la.side, la.source) == ("Copper,L4,Bot", "copper", BOTTOM, "x2")
    assert la.software == "KiCad,Pcbnew,9.0.9" and la.attributes["FilePolarity"] == "Positive"
    u3, u4, via, plain, fid = la.flashes
    assert (u3.ref, u3.pin, u3.pin_name, u3.net, u3.function) == ("U1", "3", "VDD", "/a,b", "SMDPad,CuDef")
    assert (u4.ref, u4.pin, u4.net) == ("U1", "4", "/a,b")
    assert (via.ref, via.function, plain.function, fid.ref, fid.pin) == ("", "ViaPad", "", "FID1", "")
    zone, pad = la.regions
    assert (zone.function, zone.net, pad.ref, pad.pin) == ("Conductor", "GND", "J1", "1")
    assert la.pads() == [u3, u4, plain, fid, pad]                       # not the via, not the zone
    x1 = re.sub(r"%(T[FAOD][^*]*)\*%", lambda m: f"G04 #@! {m.group(1)}*\n", body)
    assert "%TO" not in x1 and "G04 #@! TO.P,U1,3,VDD*" in x1
    la1 = one(x1, "board-B_Cu.gbr")
    assert (la1.function, la1.source) == ("Copper,L4,Bot", "comment")
    assert [(p.ref, p.pin, p.net, p.function) for p in la1.pads()] == [(p.ref, p.pin, p.net, p.function)
                                                                     for p in la.pads()]
    plain_comment = one(MM + "G04 TF.FileFunction,Copper,L1,Top is only a comment*%ADD10C,1*%D10*X0Y0D03*", "x.gbr")
    assert plain_comment.function == "" and plain_comment.kind == "other"


@pytest.mark.parametrize("name, function", [
    ("b-F_Cu.gtl", "Copper,L1,Top"), ("b-B_Cu.gbl", "Copper,Bot"), ("b-F_Paste.gtp", "Paste,Top"),
    ("b-B_Paste.gbp", "Paste,Bot"), ("b-F_Mask.gts", "Soldermask,Top"), ("b-B_Silkscreen.gbo", "Legend,Bot"),
    ("b-Edge_Cuts.gm1", "Profile,NP"), ("b.GKO", "Profile,NP"), ("b-In1_Cu.g1", "Copper,Inr"),
    ("b.GP2", "Copper,Inr"), ("b.cmp", "Copper,L1,Top"), ("b.sol", "Copper,Bot"), ("b.crc", "Paste,Top"),
    ("b-F_Cu.gbr", "Copper,L1,Top"), ("b-F.Cu.gbr", "Copper,L1,Top"), ("b-B_Paste.gbr", "Paste,Bot"),
    ("b-Edge_Cuts.gbr", "Profile,NP"), ("b-In2_Cu.gbr", "Copper,Inr"), ("copper_top.gbr", "Copper,L1,Top"),
    ("solderpaste_bottom.gbr", "Paste,Bot"), ("Gerber_TopPasteMaskLayer.gbr", "Paste,Top"),
    ("profile.gbr", "Profile,NP"), ("b-Top_layer.gbr", "Copper,L1,Top"), ("readme.gbr", ""),
])
def test_layer_functions_from_file_names(name, function):
    """X1 sets say what a file is by its name only: Protel extensions, Eagle's, KiCad's layer names."""
    assert gb.classify(name) == function


def test_function_kinds():
    assert gb.function_kind("Copper,L1,Top") == ("copper", TOP)
    assert gb.function_kind("Copper,L4,Bot,Signal") == ("copper", BOTTOM)
    assert gb.function_kind("Copper,L2,Inr") == ("copper", "inner")
    assert gb.function_kind("Paste,Bot") == ("paste", BOTTOM)
    assert gb.function_kind("SolderPaste,Top") == ("paste", TOP)                    # the job file's spelling
    assert gb.function_kind("Profile,NP") == ("profile", "")
    assert gb.function_kind("Plated,1,4,PTH") == ("drill", "")
    assert gb.function_kind("") == ("other", "")


def test_directories_zips_job_files_and_other_files(tmp_path):
    """A directory's files or a zip's members; Excellon and documents are skipped by name; a job file names the
    function of a file that has no X2 attributes and no telling name."""
    files = {
        "a.gbr": MM + "%ADD10C,1*%D10*X0Y0D03*",
        "b-F_Paste.gtp": "%TF.FileFunction,Paste,Top*%" + MM + "%ADD10C,1*%D10*X0Y0D03*",
        "c.drl": "M48\nMETRIC\nT1C0.3\n%\nT1\nX1Y1\nM30\n",
        "notes.md": "# notes\n",
        "x-job.gbrjob": '{"FilesAttributes": [{"Path": "a.gbr", "FileFunction": "Copper,L1,Top"}]}',
    }
    d = tmp_path / "set"
    d.mkdir()
    for n, t in files.items():
        (d / n).write_text(t, encoding="utf-8")
    (d / "sub").mkdir()
    (d / "sub" / "z.gtl").write_text(MM, encoding="utf-8")
    z = tmp_path / "set.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for n, t in files.items():
            zf.writestr(f"gerbers/{n}", t)
    (d / "inner.zip").write_bytes(z.read_bytes())
    for path in (d, z):
        gs = gb.read_gerbers(path)
        assert [(Path(la.name).name, la.kind, la.side, la.source) for la in gs.layers] == [
            ("a.gbr", "copper", TOP, "job"), ("b-F_Paste.gtp", "paste", TOP, "x2")]
        skipped = {Path(n).name: why for n, why in gs.skipped}
        assert skipped["c.drl"] == "Excellon drill file" and skipped["notes.md"] == "not a Gerber file"
        assert skipped["x-job.gbrjob"] == "Gerber job file"
        assert len(gs.pads(TOP)) == 2 and gs.pads(BOTTOM) == []
    assert dict(gb.read_gerbers(d).skipped)["inner.zip"].startswith("archive")
    with pytest.raises(gb.GerberError, match="not a zip"):
        (tmp_path / "bad.zip").write_bytes(b"nope")
        gb.read_gerbers(tmp_path / "bad.zip")
    with pytest.raises(FileNotFoundError):
        gb.read_gerbers(tmp_path / "absent")


def _patched_zip(path: Path, src: Path, *, flags: int = 0, method: int | None = None) -> Path:
    """A copy of zip `src` whose members' central directory entries carry other flag bits or another compression
    method: what an encrypted zip (bit 0) or a Deflate64 one (method 9, Windows Explorer and 7-Zip) says."""
    data = bytearray(src.read_bytes())
    i = data.find(b"PK\x01\x02")
    while i >= 0:
        data[i + 8:i + 10] = (int.from_bytes(data[i + 8:i + 10], "little") | flags).to_bytes(2, "little")
        if method is not None:
            data[i + 10:i + 12] = method.to_bytes(2, "little")
        i = data.find(b"PK\x01\x02", i + 4)
    path.write_bytes(bytes(data))
    return path


def test_a_zip_that_cannot_be_read_is_a_gerber_error(tmp_path):
    """An encrypted zip, or one compressed with a method Python cannot read (Deflate64), names the file and why: bad
    input, not a crash."""
    z = tmp_path / "set.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("gerbers/b-F_Cu.gtl", "%TF.FileFunction,Copper,L1,Top*%" + MM + "%ADD10C,1*%D10*X0Y0D03*")
    for name, kw, why in (("enc.zip", {"flags": 1}, "encrypted"), ("d64.zip", {"method": 9}, "not supported")):
        with pytest.raises(gb.GerberError, match=rf"{name}: gerbers/b-F_Cu.gtl: cannot be read \(.*{why}"):
            gb.read_gerbers(_patched_zip(tmp_path / name, z, **kw))


def test_untrusted_files_cannot_expand_without_bound():
    """A few bytes of step and repeat, macro primitives with a huge vertex or ring count, an outline that names
    more points than it gives, nested blocks: each would take gigabytes; each is a GerberError at once."""
    blocks = MM + "%ADD10C,0.5*%%ABD11*%D10*" + "".join(f"X{k}000000Y0D03*" for k in range(20)) + "%AB*%"
    for n in range(12, 17):                        # each block flashes the one before 20 times: 20^6 flashes
        blocks += f"%ABD{n}*%D{n - 1}*" + "".join(f"X0Y{k}000000D03*" for k in range(20)) + "%AB*%"
    cases = {"sr": (MM + "%ADD10C,0.5*%%SRX3000Y3000I1.0J1.0*%D10*X0Y0D03*%SR*%M02*", "expands to more than"),
             "poly": (MM + "%AMBIG*5,1,$1,0,0,1,0*%%ADD10BIG,50000000*%D10*X0Y0D03*M02*", "polygon of 50000000"),
             "P": (MM + "%ADD10P,1X50000000*%D10*X0Y0D03*M02*", "polygon of 5e"),
             "outline": (MM + "%AMOUT*4,1,$1,0,0,0,0*%%ADD10OUT,300000000*%D10*X0Y0D03*M02*", "outline of 300000000"),
             "moire": (MM + "%AMMO*6,0,0,1,0,0,100000000,0,0,0*%%ADD10MO*%D10*X0Y0D03*M02*", "moire of 1e"),
             "blocks": (blocks + "D16*X0Y0D03*M02*", "expands to more than")}
    for name, (text, why) in cases.items():
        with pytest.raises(gb.GerberError, match=why):
            one(text, f"{name}.gtl")
    # a large but real step and repeat (a panel of 30 x 30) still reads
    assert len(one(MM + "%ADD10C,0.5*%%SRX30Y30I1.0J1.0*%D10*X0Y0D03*%SR*%M02*").flashes) == 900


def test_only_the_layers_the_check_uses_are_read(tmp_path):
    """Outer copper, paste, the profile and files nothing names are parsed; inner copper, mask, legend and drawings
    (named by their attributes, the job file or their names) are listed as not read, whatever they hold."""
    bomb = "%SRX3000Y3000I1.0J1.0*%D10*X0Y0D03*%SR*%M02*"            # never parsed: it would be refused
    files = {"b-F_Cu.gtl": "%TF.FileFunction,Copper,L1,Top*%" + MM + "%ADD10C,1*%D10*X0Y0D03*",
             "b-In1_Cu.g1": "%TF.FileFunction,Copper,L2,Inr*%" + MM + "%ADD10C,1*%" + bomb,
             "b-F_Silkscreen.gto": MM + "%ADD10C,1*%" + bomb,
             "b-F_Mask.gts": "G04 #@! TF.FileFunction,Soldermask,Top*\n" + MM + "%ADD10C,1*%" + bomb,
             "odd.gbr": MM + "%ADD10C,1*%D10*X0Y0D03*",
             "x.gbr": MM + "%ADD10C,1*%" + bomb,
             "b-job.gbrjob": '{"FilesAttributes": [{"Path": "x.gbr", "FileFunction": "AssemblyDrawing,Top"}]}'}
    for n, t in files.items():
        (tmp_path / n).write_text(t, encoding="utf-8")
    gs = gb.read_gerbers(tmp_path)
    assert [(la.name, la.kind) for la in gs.layers] == [("b-F_Cu.gtl", "copper"), ("odd.gbr", "other")]
    skipped = dict(gs.skipped)
    assert skipped["b-In1_Cu.g1"] == "not read: an inner copper layer (Copper,L2,Inr), which the check does not use"
    assert skipped["b-F_Mask.gts"].startswith("not read: a mask layer (Soldermask,Top)")
    assert skipped["b-F_Silkscreen.gto"].startswith("not read: a legend layer") and "x.gbr" in skipped


def test_a_directory_whose_gerbers_are_in_a_subdirectory_names_it(tmp_path):
    (tmp_path / "fab" / "gerbers").mkdir(parents=True)
    (tmp_path / "fab" / "gerbers" / "b-F_Cu.gtl").write_text(MM + "%ADD10C,1*%D10*X0Y0D03*", encoding="utf-8")
    (tmp_path / "fab" / "readme.txt").write_text("ordered 2026-10-01", encoding="utf-8")
    gs = gb.read_gerbers(tmp_path / "fab")
    assert not gs.layers and dict(gs.skipped)["gerbers/"] == "a directory of Gerber files: pass it as the path"
    res = gb.gerber_origin(synthetic_board(), gs)
    assert res.messages == ["no Gerber files were read: the Gerbers are in gerbers/ (pass it as the path)"]


# --------------------------------------------------------------------------------------------------- synthetic boards

def synthetic_board(seed: int = 7, origin=(37.5, 112.25)) -> Board:
    """A board of resistors, SOICs, QFNs (an exposed pad and pads sharing a number), a pin header (plated) and
    fiducials, on both sides at any angle."""
    rng = random.Random(seed)
    parts = []
    kinds = ["R", "U", "Q", "J"]
    for i in range(48):
        k = kinds[i % 4]
        layer = BOTTOM if rng.random() < 0.35 else TOP
        x, y = rng.uniform(20, 120), rng.uniform(60, 140)
        angle = rng.choice([0, 90, 180, 270, rng.uniform(0, 360)])
        if k == "R":
            pads = [Pad("1", -0.8, 0, 0.9, 0.95), Pad("2", 0.8, 0, 0.9, 0.95)]
        elif k == "U":
            pads = [Pad(str(n + 1), -2.7, -1.905 + 1.27 * n, 1.5, 0.6) for n in range(4)]
            pads += [Pad(str(n + 5), 2.7, 1.905 - 1.27 * n, 1.5, 0.6) for n in range(4)]
        elif k == "Q":
            pads = [Pad(str(n + 1), -1.5 + n * 0.5, 2.0, 0.25, 0.8) for n in range(7)]
            pads += [Pad("9", 0, 0, 2.5, 2.5), Pad("GND", -1.5, -2.0, 0.25, 0.8), Pad("GND", 1.5, -2.0, 0.25, 0.8)]
        else:
            pads = [Pad(str(n + 1), 0, n * 2.54, 1.7, 1.7, shape="circle", kind="tht") for n in range(6)]
        parts.append(Part(f"{k}{i}", layer, x, y, angle, pads=pads))
    parts.append(Part("FID1", TOP, 25.0, 65.0, 0, pads=[Pad("1", 0, 0, 1, 1, shape="circle")]))
    parts.append(Part("FID2", TOP, 115.0, 135.0, 0, pads=[Pad("1", 0, 0, 1, 1, shape="circle")]))
    return Board(path="synthetic.kicad_pcb", origin=origin, parts=parts, source="test", origin_kind="aux")


def write_set(board: Board, origin, *, flip=(1, -1), scale=1.0, unit="MM", x2=True, drop=0.0, extra=0, seed=3,
              move=None, paste=True, pads=None, nets=False, stem="syn", paste_origin=None) -> gb.GerberSet:
    """A KiCad-like Gerber set of the board plotted from `origin`: g = scale . F . (b - origin), F = diag(flip);
    `unit` is what the files say (coordinates are written in mm whatever it says, to make a scaled set); `drop` a
    fraction of the pads left out, `extra` vias and fiducials added; `move` = {ref: (dx, dy)} moves parts first;
    `pads` the BoardPads to plot (default gb.board_pads(board)); `nets` adds TO.N; `stem` names the files;
    `paste_origin` plots the paste from another origin."""
    return gb.read_gerber_text(set_texts(board, origin, flip=flip, scale=scale, unit=unit, x2=x2, drop=drop,
                                         extra=extra, seed=seed, move=move, paste=paste, pads=pads, nets=nets,
                                         stem=stem, paste_origin=paste_origin))


def set_texts(board: Board, origin, *, flip=(1, -1), scale=1.0, unit="MM", x2=True, drop=0.0, extra=0, seed=3,
              move=None, paste=True, pads=None, nets=False, stem="syn", paste_origin=None) -> dict[str, str]:
    """write_set's files, {name: text}."""
    rng = random.Random(seed)
    texts = {}
    for side, ext in ((TOP, "F"), (BOTTOM, "B")):
        for layer in ("Cu", "Paste") if paste else ("Cu",):
            at = paste_origin if layer == "Paste" and paste_origin is not None else origin
            out = [f"%TF.FileFunction,{'Copper,L1,Top' if side == TOP else 'Copper,L2,Bot'}*%" if layer == "Cu"
                   else f"%TF.FileFunction,Paste,{'Top' if side == TOP else 'Bot'}*%", "%FSLAX46Y46*%",
                   f"%MO{unit}*%", "%TA.AperFunction,SMDPad,CuDef*%" if layer == "Cu" else "", "%ADD10C,0.400000*%",
                   "%TD*%", "%TA.AperFunction,ViaPad*%", "%ADD11C,0.600000*%", "%TD*%", "D10*"]
            for bp in gb.board_pads(board) if pads is None else pads:
                if bp.side not in (side, "both") or (layer == "Paste" and bp.kind == "tht") or rng.random() < drop:
                    continue
                bx, by = bp.x, bp.y
                if move and bp.ref in move:
                    bx, by = bx + move[bp.ref][0], by + move[bp.ref][1]
                g = (scale * flip[0] * (bx - at[0]), scale * flip[1] * (by - at[1]))
                if x2 and layer == "Cu":
                    out.append(f"%TO.P,{bp.ref},{bp.number}*%" + (f"%TO.N,{bp.net}*%" if nets and bp.net else ""))
                out.append(f"X{round(g[0] * 1e6)}Y{round(g[1] * 1e6)}D03*")
                if x2 and layer == "Cu":
                    out.append("%TD*%")
            if layer == "Cu":
                out.append("D11*")
                for _ in range(extra):
                    out.append(f"X{round(rng.uniform(-200, 200) * 1e6)}Y{round(rng.uniform(-200, 200) * 1e6)}D03*")
            out.append("M02*")
            texts[f"{stem}-{ext}_{layer}.gbr"] = "\n".join(out)
    return texts


def profile(box, origin, name: str = "syn-Edge_Cuts.gbr") -> dict[str, str]:
    """A KiCad-like profile layer: the board-frame rectangle box = (x0, y0, x1, y1) plotted from `origin`."""
    x0, y0, x1, y1 = box
    pts = [board_to_cpl(q, origin) for q in ((x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0))]
    return {name: "%TF.FileFunction,Profile,NP*%" + MM + "%ADD10C,0.100000*%D10*" + "".join(
        f"X{round(x * 1e6)}Y{round(y * 1e6)}D0{2 if k == 0 else 1}*" for k, (x, y) in enumerate(pts)) + "M02*"}


def test_board_pads_are_the_convention_s_board_frame():
    board = synthetic_board()
    pads = gb.board_pads(board)
    i = 0
    for part in board.parts:
        for pad in part.pads:
            assert (pads[i].x, pads[i].y) == pytest.approx(library_to_board(part, (pad.x, pad.y)), abs=1e-12)
            assert pads[i].side == ("both" if pad.kind == "tht" else part.layer)
            i += 1
    assert i == len(pads)


@pytest.mark.parametrize("x2", [True, False])
def test_origin_recovered_from_any_origin(x2):
    board = synthetic_board()
    for origin in [(0.0, 0.0), board.origin, (-12.345678, 250.5)]:
        res = gb.gerber_origin(board, write_set(board, origin, x2=x2))
        assert res.origin == pytest.approx(origin, abs=1e-6)
        assert (res.method, res.flip, res.scale) == ("x2-pins" if x2 else "pad-voting", "none", 1.0)
        assert res.matched == res.board_pads and res.residual < 1e-6
        assert res.coverage == 1.0 and res.belongs and not res.stale and not res.extra
        assert res.verdict == ("matches" if origin == board.origin else "does-not-match")
        assert res.ok == (origin == board.origin)


def test_origin_mismatch_says_by_how_much_and_the_shift_corrects_the_cpl():
    board = synthetic_board()
    res = gb.gerber_origin(board, write_set(board, (0.0, 0.0)))
    assert res.named == "absolute" and res.cpl_origin_kind == "aux"
    assert res.shift == pytest.approx((37.5, -112.25))
    assert res.messages[0].startswith("origin mismatch: the Gerbers' (0, 0) is board (0.0000, 0.0000) (the page "
                                      "origin), the CPL's origin is (37.5000, 112.2500) (the drill/place file origin)")
    assert "--use-drill-file-origin" in res.messages[0] and "(+37.5000, -112.2500)" in res.messages[0]
    # the shifted rows put every part where the Gerbers' origin puts it
    rows = [CplRow(p.ref, *board_to_cpl((p.x, p.y), board.origin), p.layer, 0.0) for p in board.parts]
    fixed = gb.shifted_rows(rows, res)
    for p, r in zip(board.parts, fixed):
        assert (r.x, r.y) == pytest.approx(gb.board_to_gerber((p.x, p.y), res.origin), abs=1e-4)
    # a CPL origin given explicitly
    res = gb.gerber_origin(board, write_set(board, (0.0, 0.0)), (0, 0))
    assert res.verdict == "matches" and res.cpl_origin_kind == "user" and res.shift == (0.0, 0.0)


def test_voting_survives_missing_pads_fiducials_vias_and_other_copper():
    board = synthetic_board()
    res = gb.gerber_origin(board, write_set(board, (3.0, 4.0), x2=False, drop=0.4, extra=300))
    assert res.origin == pytest.approx((3.0, 4.0), abs=1e-6) and res.method == "pad-voting"
    assert 0.5 * res.board_pads < res.matched < res.board_pads
    assert res.stale and res.missing                          # pads with no Gerber pad: older Gerbers
    # pads on one side only (a set without its bottom files), and only one side's paste
    gs = write_set(board, (3.0, 4.0), x2=False)
    gs.layers = [la for la in gs.layers if la.side == TOP]
    res = gb.gerber_origin(board, gs)
    assert res.origin == pytest.approx((3.0, 4.0), abs=1e-6)


def led_matrix(n: int, pitch: float, origin, package: str = "D3.0mm") -> Board:
    """n x n LEDs at one pitch from (105, 105), a periodic layout: KiCad's THT LED_D3.0mm, or SMD 0603 and
    WS2812B-5050 lands."""
    pads = {"D3.0mm": [Pad("1", 0, 0, 1.8, 1.8, kind="tht"), Pad("2", 2.54, 0, 1.8, 1.8, shape="circle", kind="tht")],
            "0603": [Pad("1", -0.7875, 0, 0.875, 0.95), Pad("2", 0.7875, 0, 0.875, 0.95)],
            "5050": [Pad(str(k + 1), x, y, 1.5, 1.0) for k, (x, y) in enumerate(
                ((-2.45, -1.6), (-2.45, 1.6), (2.45, 1.6), (2.45, -1.6)))]}[package]
    parts = [Part(f"D{r * n + c + 1}", TOP, 105 + pitch * c, 105 + pitch * r, 0.0, pads=[Pad(**vars(p)) for p in pads])
             for r in range(n) for c in range(n)]
    return Board(path="matrix.kicad_pcb", origin=origin, parts=parts, source="test", origin_kind="aux")


@pytest.mark.parametrize("n, pitch, origin, package", [
    (16, 5.08, (100.0, 100.0), "D3.0mm"), (24, 5.08, (50.0, 150.0), "D3.0mm"), (24, 5.08, (100.0, 190.0), "D3.0mm"),
    (24, 7.62, (100.0, 100.0), "D3.0mm"), (16, 5.08, (100.0, 100.0), "0603"), (24, 5.08, (50.0, 150.0), "0603"),
    (16, 10.0, (100.0, 190.0), "5050"), (24, 10.0, (100.0, 100.0), "5050")])
def test_voting_finds_a_round_origin_on_a_periodic_layout(n, pitch, origin, package):
    """A periodic layout plotted from a round origin with no pin attributes (kicad-cli --no-netlist): the true
    translation lies on a vote-cell boundary, so its votes split over up to four cells, while a translation one pitch
    off collects one cell's worth. The origin is the one that matches every pad, never one a pitch away."""
    board = led_matrix(n, pitch, origin, package)
    res = gb.gerber_origin(board, write_set(board, origin, x2=False))
    assert res.origin == pytest.approx(origin, abs=1e-6) and res.method == "pad-voting"
    assert res.matched == res.board_pads and res.verdict == "matches" and not res.stale and res.ok
    page = gb.gerber_origin(board, write_set(board, (0.0, 0.0), x2=False))   # the page origin is round too
    assert page.origin == pytest.approx((0.0, 0.0), abs=1e-6) and page.named == "absolute"


@pytest.mark.parametrize("x2", [True, False])
def test_mirrored_and_scaled_sets_are_found(x2):
    """A set whose y axis is not inverted, one mirrored left to right, one turned half a turn, and sets whose
    unit is wrong both ways: found as such, never as a matching origin."""
    board = synthetic_board()
    o = board.origin
    for flip, name in (((1, 1), "y"), ((-1, -1), "x"), ((-1, 1), "xy")):
        res = gb.gerber_origin(board, write_set(board, o, flip=flip, x2=x2))
        assert (res.flip, res.scale, res.verdict) == (name, 1.0, "does-not-match")
        assert res.origin == pytest.approx(o, abs=1e-6) and res.matched == res.board_pads
        with pytest.raises(ValueError):
            gb.shifted_rows([], res)
    assert "not inverted" in gb.gerber_origin(board, write_set(board, o, flip=(1, 1), x2=x2)).messages[0]
    res = gb.gerber_origin(board, write_set(board, o, unit="IN", x2=x2))           # mm written, inches declared
    assert res.scale == pytest.approx(25.4) and res.origin == pytest.approx(o, abs=1e-6)
    assert res.verdict == "does-not-match" and "25.4 times too large" in res.messages[-1]
    res = gb.gerber_origin(board, write_set(board, o, scale=1 / 25.4, x2=x2))      # inches written, mm declared
    assert res.scale == pytest.approx(1 / 25.4) and res.origin == pytest.approx(o, abs=1e-4)   # 1e-6 in steps
    assert "25.4 times too small" in res.messages[-1]


def test_pads_on_the_far_copper_are_compared_on_the_side_the_gerbers_plot_them():
    """A bottom edge-mount connector whose pads 6-9 are on the top copper and paste (KiCad's DSUB EdgeMount
    footprints): fresh Gerbers have them on the top, and are not stale."""
    board = synthetic_board()
    pads = [Pad(str(n + 1), -5.54 + 2.77 * n, -1.5, 1.0, 3.0, cu=BOTTOM) for n in range(5)]
    pads += [Pad(str(n + 6), -4.155 + 2.77 * n, -1.5, 1.0, 3.0, copper=False, cu=TOP) for n in range(4)]
    j = Part("J99", BOTTOM, 70.0, 100.0, 90.0, pads=pads)
    board.parts.append(j)
    plot = [p for p in gb.board_pads(board) if p.ref != "J99"]
    plot += [gb.BoardPad("J99", p.number, p.cu, *library_to_board(j, (p.x, p.y)), 1.0, 1.0) for p in pads]
    assert [(p.side, p.copper) for p in gb.board_pads(board) if p.ref == "J99"] == [(BOTTOM, True)] * 5 + [
        (TOP, True)] * 4
    for x2 in (True, False):
        res = gb.gerber_origin(board, write_set(board, board.origin, pads=plot, x2=x2))
        assert res.ok and not res.extra and not res.missing and res.matched == res.board_pads, res.messages


def test_a_set_without_one_side_is_red_and_one_without_its_paste_yellow():
    """Bottom files left out of the set: the top is checked (not stale), and the missing side is a problem of its
    own, red, naming the side and the files read: JLC could not make the bottom. A side with copper but no paste is
    yellow: its SMD pads would get no paste."""
    board = synthetic_board()
    gs = write_set(board, board.origin)
    gs.layers = [la for la in gs.layers if la.side == TOP]
    res = gb.gerber_origin(board, gs)
    assert res.verdict == "matches" and res.belongs and not res.stale and res.coverage == 1.0
    assert res.compared == sum(1 for p in gb.board_pads(board) if p.side in (TOP, "both"))
    bottom = sum(1 for p in gb.board_pads(board) if p.side == BOTTOM and p.kind == "smd")
    assert res.problems == [(RED, BOTTOM, f"the Gerbers have no bottom copper or paste layer: the board's {bottom} "
                                         "bottom SMD pads are on no layer of the set (read: syn-F_Cu.gbr, "
                                         "syn-F_Paste.gbr)")]
    gs = write_set(board, board.origin)
    gs.layers = [la for la in gs.layers if (la.kind, la.side) != ("paste", BOTTOM)]
    res = gb.gerber_origin(board, gs)
    assert res.ok and res.problems == [(YELLOW, BOTTOM, f"the Gerbers have no bottom paste layer: the board's "
                                                        f"{bottom} bottom SMD pads would get no solder paste")]


def tht_board(n: int = 12, seed: int = 5, keep: int | None = None) -> Board:
    """Plated through-hole headers only (no SMD pad); `keep` of them where seed 5 puts them, the rest elsewhere."""
    rng, other = random.Random(seed), random.Random(seed + 1)
    parts = []
    for i in range(n):
        x, y = rng.uniform(20, 120), rng.uniform(60, 140)
        if keep is not None and i >= keep:
            x, y = other.uniform(20, 120), other.uniform(60, 140)
        parts.append(Part(f"J{i}", TOP, x, y, rng.choice([0, 90]), pads=[
            Pad(str(k + 1), 0, k * 2.54, 1.7, 1.7, shape="circle", kind="tht", cu="both") for k in range(6)]))
    return Board(path="tht.kicad_pcb", origin=(30.0, 150.0), parts=parts, source="test", origin_kind="aux")


@pytest.mark.parametrize("x2", [True, False])
def test_plated_through_hole_pads_count_for_the_coverage(x2):
    """A board without SMD pads: another board's Gerbers that share most of its headers, and Gerbers plotted before
    a header moved, are not this board's as it is; its plated pads with no Gerber pad are named."""
    board = tht_board()
    res = gb.gerber_origin(board, write_set(board, board.origin, x2=x2))
    assert res.ok and res.compared == 72 and res.coverage == 1.0
    res = gb.gerber_origin(board, write_set(tht_board(keep=8), board.origin, x2=x2))
    assert not res.ok and res.stale and res.coverage == pytest.approx(48 / 72)
    assert sorted({m.split(":")[0] for m in res.missing}) == ["J10", "J11", "J8", "J9"]
    res = gb.gerber_origin(board, write_set(board, board.origin, x2=x2, move={"J3": (20.0, 0.0)}))
    assert res.stale and sorted(res.missing) == [f"J3:{k}" for k in range(1, 7)]
    assert "6 of 72 board pads have no Gerber pad at their place (J3:1" in res.messages[-1]


def test_pin_attributes_that_name_another_pad_or_net_make_the_set_stale():
    """Parts of one footprint swapped after plotting: every pad still has a Gerber pad, but the Gerbers' pin
    attributes put R2's pins on the board's R1 (the copper routes the old nets). A net changed alike."""
    board = synthetic_board()
    for part in board.parts:
        for pad in part.pads:
            pad.net = f"N-{part.ref}-{pad.number}"
    twin = synthetic_board()
    rs = [p for p in twin.parts if p.ref.startswith("R")]
    a, b = next((p, q) for p in rs for q in rs if p.ref < q.ref and p.layer == q.layer)
    (a.x, a.y, a.angle), (b.x, b.y, b.angle) = (b.x, b.y, b.angle), (a.x, a.y, a.angle)
    for part in twin.parts:
        for pad in part.pads:
            pad.net = f"N-{part.ref}-{pad.number}"
    res = gb.gerber_origin(board, write_set(board, board.origin, nets=True))
    assert res.ok and not res.mismatched
    res = gb.gerber_origin(board, write_set(twin, board.origin, nets=True))
    assert res.matched == res.board_pads and not res.missing and res.stale and not res.ok
    assert f"{a.ref}:1 is where the Gerbers have {b.ref}:1" in res.mismatched and len(res.mismatched) == 4
    assert "the Gerbers' pin attributes name other pads than the board's at their place (4 pads: " \
           f"{res.mismatched[0]}" in res.messages[-1]
    twin = synthetic_board()
    for part in twin.parts:
        for pad in part.pads:
            pad.net = f"N-{part.ref}-{pad.number}"
    twin.part("U1").pads[0].net = "OLD"
    res = gb.gerber_origin(board, write_set(twin, board.origin, nets=True))
    assert res.stale and res.mismatched == ["U1:1 is on net OLD in the Gerbers, N-U1-1 on the board"]


def test_two_plots_in_one_set_are_a_problem():
    """KiCad never deletes an old plot: a folder holding a renamed project's old plot beside the new one has two of
    every layer, and JLC may build from either, though nothing in the old one is extra."""
    board = synthetic_board()
    old = synthetic_board()
    old.parts = [p for p in old.parts if p.ref != "R4"]
    texts = {**set_texts(board, board.origin), **set_texts(old, board.origin, stem="old")}
    res = gb.gerber_origin(board, gb.read_gerber_text(texts))
    assert not res.stale and res.problems == [(RED, "", "the Gerbers hold more than one plot (2 top copper layers: "
                                                        "old-F_Cu.gbr, syn-F_Cu.gbr; 2 top paste layers: "
                                                        "old-F_Paste.gbr, syn-F_Paste.gbr; 2 bottom copper layers: "
                                                        "old-B_Cu.gbr, syn-B_Cu.gbr; 2 bottom paste layers: "
                                                        "old-B_Paste.gbr, syn-B_Paste.gbr): JLC may make the board "
                                                        "from either; send one plot")]


def test_an_outline_plotted_from_another_origin_is_a_problem():
    """The profile layer from another origin than the copper: JLC's preview judges the parts against it."""
    board = synthetic_board()
    edge = [[(15.0, 55.0), (125.0, 55.0), (125.0, 145.0), (15.0, 145.0), (15.0, 55.0)]]
    box = (15.0, 55.0, 125.0, 145.0)
    for at, want in ((board.origin, []), ((0.0, 0.0), [(RED, "", "the board outline (syn-Edge_Cuts.gbr) was plotted "
                                                       "from another origin than the copper, board (0, 0) (the page "
                                                       "origin): it lies (+37.500, -112.250) mm off the board's "
                                                       "Edge.Cuts; plot every layer from one origin")])):
        gs = gb.read_gerber_text({**set_texts(board, board.origin), **profile(box, at)})
        assert gb.gerber_origin(board, gs, edge=edge).problems == want
        without = gb.gerber_origin(board, gs).problems          # a snapshot: no Edge.Cuts, the pads must be inside
        assert without == ([] if not want else [(RED, "", "the board outline (syn-Edge_Cuts.gbr) lies away from the "
                                                          "copper: 0% of the board's pads are inside it; plot every "
                                                          "layer from one origin")])
    gs = gb.read_gerber_text({**set_texts(board, board.origin), **profile((15.0, 55.0, 105.0, 145.0), board.origin)})
    assert gb.gerber_origin(board, gs, edge=edge).problems == [(RED, "", "the board outline (syn-Edge_Cuts.gbr) is "
                                                                         "not the board's Edge.Cuts: 90 x 90 mm "
                                                                         "against 110 x 90 mm")]


def test_messages_say_what_the_set_lacks_or_mixes():
    """Copper files whose names say nothing (KiCad's French demos: Dessus, Dessous) are named, an empty paste layer
    is not 'not named'; paste plotted from another origin than the copper is not 'older'; pin attributes that fit
    only scaled (a coordinate format off by 10) say the scale."""
    board = tht_board()
    texts = {n.replace("syn-F_Cu", "Dessus").replace("syn-B_Cu", "Dessous"): t.replace(
        "%TF.FileFunction,Copper,L1,Top*%", "").replace("%TF.FileFunction,Copper,L2,Bot*%", "")
        for n, t in set_texts(board, board.origin, x2=False).items()}
    res = gb.gerber_origin(board, gb.read_gerber_text(texts))
    assert res.messages == ["the Gerbers have no copper or paste pads to compare (top copper: none; bottom copper: "
                            "none; top paste: syn-F_Paste.gbr (0 pads); bottom paste: syn-B_Paste.gbr (0 pads); not "
                            "named as a layer, with pads: Dessous.gbr (72 flashes), Dessus.gbr (72 flashes): name "
                            "copper and paste files as KiCad does (-F_Cu, -B_Paste) or plot them with X2 attributes)"]
    board = synthetic_board()
    res = gb.gerber_origin(board, write_set(board, (0.0, 0.0), paste_origin=board.origin))
    assert res.origin == pytest.approx((0.0, 0.0), abs=1e-6) and res.stale
    assert res.messages[-1].startswith("the paste layers (syn-B_Paste.gbr, syn-F_Paste.gbr) were plotted from "
                                       "another origin than the copper, board (37.5, 112.25) (the drill/place file "
                                       "origin): plot every layer from one origin")
    res = gb.gerber_origin(board, write_set(board, board.origin, scale=10.0))
    assert res.origin is None and "the pin attributes fit the board scaled by 10 (" in res.messages[-1]
    assert "a coordinate format (FS) that does not match the data?" in res.messages[-1]


@pytest.mark.parametrize("x2", [True, False])
def test_boards_with_fewer_pads_than_min_matched(x2):
    """A two-pad board (an antenna) whose Gerbers hold its two pads: all of them is enough."""
    board = Board("ant.kicad_pcb", (10.0, 20.0), [Part("J1", TOP, 15.0, 25.0, 0.0, pads=[
        Pad("1", -1.27, 0, 1.7, 1.7, shape="circle", kind="tht"), Pad("2", 1.27, 0, 1.7, 3.0, kind="tht")])])
    res = gb.gerber_origin(board, write_set(board, (10.0, 20.0), x2=x2, paste=False))
    assert res.origin == pytest.approx((10.0, 20.0), abs=1e-9) and res.verdict == "matches"
    assert res.method == ("x2-pins" if x2 else "pad-voting") and res.matched == 2


def test_nothing_to_compare():
    board = synthetic_board()
    res = gb.gerber_origin(board, gb.read_gerber_text({"a.gm1": MM + "%ADD10C,0.1*%D10*X0Y0D02*X1000Y0D01*"}))
    assert res.verdict == "unknown" and "no copper or paste pads" in res.messages[0]
    res = gb.gerber_origin(Board("x", (0, 0), []), write_set(board, (0, 0)))
    assert res.verdict == "unknown" and "no copper pads" in res.messages[0]


# ------------------------------------------------------------------------------------------------------- released sets

def board_pad_index(board: Board) -> dict:
    """(ref, number) -> board pad, for pads that are alone with their number."""
    idx: dict = {}
    for p in gb.board_pads(board):
        idx.setdefault((p.ref, p.number), []).append(p)
    return {k: v[0] for k, v in idx.items() if len(v) == 1}


def assert_kicad_plot_map(board: Board, gs: gb.GerberSet, origin) -> int:
    """Every TO.P pad of the outer copper is at g = (bx - ox, oy - by) of its board pad: Gerber y up, KiCad's map.
    Returns how many pads were compared."""
    idx = board_pad_index(board)
    errs = []
    for la in gs.find("copper"):
        if la.side not in (TOP, BOTTOM):
            continue
        for p in la.pads():
            bp = idx.get((p.ref, p.pin))
            if bp and p.ref and bp.side in (la.side, "both"):
                g = board_to_cpl((bp.x, bp.y), origin)
                errs.append(math.hypot(p.x - g[0], p.y - g[1]))
    assert errs and float(np.median(errs)) < 1e-6 and max(errs) < 2e-3, (len(errs), max(errs))
    return len(errs)


@pytest.mark.parametrize("b", BOARDS)
def test_released_gerbers_are_plotted_from_the_drill_place_origin(b):
    """The released fab outputs (directory and zip): the Gerbers' origin is the board's aux origin (100, 100), the
    released CPL's origin, found from the X2 pin attributes; every pad has its Gerber pad."""
    need(pcb(b))
    need(DIAL / f"fab/{b}/gerbers")
    board = load_board(pcb(b), reader="sexpr")
    assert board.origin == (100.0, 100.0)
    for path in (DIAL / f"fab/{b}/gerbers", DIAL / f"fab/{b}/gerbers.zip"):
        gs = gb.read_gerbers(path)
        assert {(la.kind, la.side) for la in gs.layers} >= {("copper", TOP), ("copper", BOTTOM), ("paste", TOP),
                                                             ("paste", BOTTOM), ("profile", "")}
        assert all(la.source == "x2" for la in gs.layers) and gs.x2_pins
        assert assert_kicad_plot_map(board, gs, board.origin) > 50
        res = gb.gerber_origin(board, gs)
        assert res.origin == pytest.approx((100.0, 100.0), abs=1e-3)
        assert (res.method, res.flip, res.scale, res.verdict, res.named) == ("x2-pins", "none", 1.0, "matches", "aux")
        assert res.matched == res.board_pads and res.coverage == 1.0 and res.ok
    if b == "main":                                 # a round board of 64 mm, centred on its aux origin
        assert gs.extents == pytest.approx((-32, -32, 32, 32), abs=1e-6)
    rows = read_cpl(DIAL / f"fab/{b}/dial-me-spinner-{b}-CPL-JLCPCB.csv")
    assert gb.shifted_rows(rows, res) == rows                            # nothing to shift


@pytest.mark.parametrize("b", BOARDS)
def test_old_order_gerbers_had_the_cpl_origin_too(b):
    """The first order's 7 bad rows were not an origin problem: its Gerbers were plotted from the CPL's origin."""
    need(pcb(b))
    need(JLC / f"fab-before/{b}/gerbers")
    res = gb.gerber_origin(load_board(pcb(b), reader="sexpr"), gb.read_gerbers(JLC / f"fab-before/{b}/gerbers"))
    assert res.verdict == "matches" and res.origin == pytest.approx((100.0, 100.0), abs=1e-3) and res.belongs


def test_released_gerbers_of_the_other_board_and_of_a_changed_board():
    """The base board's Gerbers are not the main board's; Gerbers plotted before a part moved and another was
    removed are stale, naming the moved part's pads and the removed part's paste."""
    for b in BOARDS:
        need(pcb(b))
        need(DIAL / f"fab/{b}/gerbers")
    main = load_board(pcb("main"), reader="sexpr")
    res = gb.gerber_origin(main, gb.read_gerbers(DIAL / "fab/base/gerbers"))
    assert res.verdict == "unknown" and not res.belongs
    board = load_board(pcb("main"), reader="sexpr")
    board.part("R2").x += 0.5
    board.parts = [p for p in board.parts if p.ref != "C7"]
    res = gb.gerber_origin(board, gb.read_gerbers(DIAL / "fab/main/gerbers"))
    assert res.verdict == "matches" and res.belongs and res.stale
    assert sorted(res.missing) == ["R2:1", "R2:2"]
    assert len(res.extra) == 4 and "older than the board" in res.messages[-1]


def test_released_gerbers_mirrored_and_with_the_wrong_unit(tmp_path):
    """The released main set edited as a broken exporter would write it: every Y negated (y not inverted), and
    %MOIN% over millimetre coordinates. The check names both."""
    need(pcb("main"))
    need(DIAL / "fab/main/gerbers")
    board = load_board(pcb("main"), reader="sexpr")
    src = DIAL / "fab/main/gerbers"
    for case in ("mirrored", "inch"):
        d = tmp_path / case
        d.mkdir()
        for f in src.glob("*_Cu.g?l"):
            t = f.read_text(encoding="utf-8")
            if case == "mirrored":                       # the coordinate data only, not %FSLAX46Y46*%
                t = re.sub(r"(?m)^(X-?\d+)?Y(-?)(\d+)", lambda m: (m.group(1) or "") + "Y"
                           + ("" if m.group(2) else "-") + m.group(3), t)
            else:
                t = t.replace("%MOMM*%", "%MOIN*%")
            (d / f.name).write_text(t, encoding="utf-8")
        res = gb.gerber_origin(board, gb.read_gerbers(d))
        assert res.origin == pytest.approx((100.0, 100.0), abs=1e-3) and res.verdict == "does-not-match"
        assert (res.flip, res.scale) == (("y", 1.0) if case == "mirrored" else ("none", pytest.approx(25.4)))


RENAMED = {"tinytapeout-demo": ["J18:1 is on net GND in the Gerbers, unconnected-(J18-Pad1) on the board",
                                 "J16:1 is on net out7 in the Gerbers, unconnected-(J16-Pad1) on the board"]}


@pytest.mark.parametrize("board_path, gerbers, origin, cpl", THIRD_PARTY)
def test_gerber_sets_other_tools_wrote(board_path, gerbers, origin, cpl):
    """Sets made by other tools and other KiCad versions: the origin they were plotted from, their layers named by
    X2 attributes or X1 comments, every pad there; a CPL written with them sits on its parts from that origin (to
    within the position corrections the tool applies itself)."""
    need(board_path)
    need(gerbers)
    board = load_board(board_path, reader="sexpr")
    gs = gb.read_gerbers(gerbers)
    assert {la.source for la in gs.layers if la.kind in ("copper", "paste")} <= {"x2", "comment"}
    res = gb.gerber_origin(board, gs)
    assert res.origin == pytest.approx(origin, abs=1e-3) and res.verdict == "matches", res.messages
    assert res.coverage == 1.0 and res.belongs and not res.missing and not res.extra
    # KiCad's tiny_tapeout demo changed after its fab outputs were plotted: two pins it now leaves unconnected
    assert res.mismatched == RENAMED.get(board_path.stem, []) and res.stale == bool(res.mismatched)
    if cpl:
        rows = [r for r in read_cpl(cpl) if board.part(r.ref)]
        far = max(math.dist(board_to_cpl((board.part(r.ref).x, board.part(r.ref).y), res.origin), (r.x, r.y))
                  for r in rows)
        assert rows and far < 3.0


# ------------------------------------------------------------------------------------------------------- kicad-cli

VARIANTS = {   # name: (kicad-cli options, the origin it plots from, the method expected)
    "drill": (["--use-drill-file-origin"], "aux", "x2-pins"),
    "page": ([], "absolute", "x2-pins"),
    "no-x2": (["--use-drill-file-origin", "--no-x2"], "aux", "x2-pins"),           # attributes as G04 #@! comments
    "no-netlist": (["--use-drill-file-origin", "--no-netlist"], "aux", "pad-voting"),
    "x1-no-netlist": (["--no-x2", "--no-netlist"], "absolute", "pad-voting"),
    "regions": (["--use-drill-file-origin", "--disable-aperture-macros", "--no-netlist", "--precision", "5"], "aux",
                "pad-voting"),
    "bare-x1": (["--use-drill-file-origin", "--no-x2", "--no-netlist"], "aux", "pad-voting"),   # comments stripped
    "bare-x1-kicad-names": (["--no-x2", "--no-netlist", "--no-protel-ext"], "absolute", "pad-voting"),
}
LAYERS = "F.Cu,B.Cu,F.Paste,B.Paste,Edge.Cuts,F.Mask"
DEMO_VARIANTS = ("drill", "page", "no-x2", "x1-no-netlist", "bare-x1-kicad-names")


def kicad_boards():
    out = [pytest.param(pcb(b), id=f"dial-me-spinner-{b}") for b in BOARDS]
    out += [pytest.param(p, id=p.stem) for p in DEMO_BOARDS if p.exists()]
    return out


def kicad_plots():
    """(board, variant): every variant of the dial-me-spinner boards, the main ones of the demo boards."""
    out = [pytest.param(pcb(b), v, id=f"dial-me-spinner-{b}-{v}") for b in BOARDS for v in VARIANTS]
    out += [pytest.param(p, v, id=f"{p.stem}-{v}") for p in DEMO_BOARDS if p.exists() for v in DEMO_VARIANTS]
    return out


@pytest.fixture(scope="module")
def plotted(tmp_path_factory):
    """board path -> (Board, its aux origin, {variant: GerberSet}); boards without a drill/place origin get one
    (a copy), so that the two origins differ."""
    if not KICAD_CLI:
        pytest.skip("kicad-cli is not on this machine")
    cache: dict = {}

    def plot(src: Path):
        if src in cache:
            return cache[src]
        need(src)
        d = tmp_path_factory.mktemp(src.stem.replace(" ", "_"))
        text = src.read_text(encoding="utf-8")
        if not re.search(r"\(aux_axis_origin ", text):
            text = text.replace("(setup", "(setup\n\t\t(aux_axis_origin 123.4567 76.5432)", 1)
        path = d / src.name
        path.write_text(text, encoding="utf-8")
        board = load_board(path, reader="sexpr")
        sets = {}
        for v, (opts, _, _) in VARIANTS.items():
            if src.parent.parent == DEMOS and v not in DEMO_VARIANTS:
                continue
            out = d / v
            subprocess.run([KICAD_CLI, "pcb", "export", "gerbers", "-l", LAYERS, *opts, "-o", str(out), str(path)],
                           check=True, capture_output=True)
            if v.startswith("bare"):                    # what a tool without KiCad's attributes writes
                for f in out.iterdir():
                    if f.suffix == ".gbrjob":
                        f.unlink()
                    else:
                        lines = f.read_text(encoding="utf-8").splitlines(True)
                        f.write_text("".join(x for x in lines if not x.startswith("G04 #@!")), encoding="utf-8")
            sets[v] = gb.read_gerbers(out)
        cache[src] = (board, board.origin, sets)
        return cache[src]
    return plot


@pytest.mark.pcbnew
@pytest.mark.parametrize("src", kicad_boards())
def test_kicad_plots_board_points_at_x_minus_ox_and_oy_minus_y(plotted, src):
    """The plot map, pad by pad: from the drill/place origin and from the page origin, every TO.P pad of the outer
    copper sits at (bx - ox, oy - by): Gerber y is up, board y is down."""
    board, aux, sets = plotted(src)
    n = assert_kicad_plot_map(board, sets["drill"], aux)
    assert assert_kicad_plot_map(board, sets["page"], (0.0, 0.0)) == n
    assert assert_kicad_plot_map(board, sets["no-x2"], aux) == n                     # X1 comments, same pads


@pytest.mark.pcbnew
@pytest.mark.parametrize("src, variant", kicad_plots())
def test_gerber_origin_on_kicad_plots(plotted, src, variant):
    """gerber_origin finds the origin kicad-cli plotted from, to 1e-3 mm, by the method the attributes allow; the
    set is the board's, complete."""
    board, aux, sets = plotted(src)
    gs = sets[variant]
    opts, origin, method = VARIANTS[variant]
    expected = aux if origin == "aux" else (0.0, 0.0)
    res = gb.gerber_origin(board, gs)
    assert res.origin == pytest.approx(expected, abs=1e-3)
    assert (res.method, res.flip, res.scale) == (method, "none", 1.0)
    assert res.verdict == ("matches" if origin == "aux" else "does-not-match")
    assert res.named == origin
    assert res.coverage == 1.0 and res.belongs and not res.stale, (res.missing[:5], res.extra[:5])
    if variant.startswith("bare"):
        assert {la.source for la in gs.layers} == {"name"} and not gs.x2_pins
    if variant == "regions":                        # rounded rectangles plotted as regions, not macros
        assert any(la.regions for la in gs.find("paste")) and not any(la.regions for la in sets["drill"].find("paste"))


def paste_graphics_board(path: Path) -> None:
    """A KiCad 9 board whose footprints draw their stencil openings as filled graphics on their paste layer, as
    KiCad's own InvenSense ICS-43434, SMA and Garmin footprints do: an SMD pad on copper and mask only, windowed by two
    fp_rect, a ring-like pad opened by an fp_poly and an fp_circle, none centred on a pad; on both sides, turned; and
    ordinary parts. Its drill/place origin is (100, 140)."""
    def pad(n, x, y, w, h, side, paste=True):
        layers = f'"{side}.Cu" "{side}.Mask"' + (f' "{side}.Paste"' if paste else "")
        return f'(pad "{n}" smd rect (at {x} {y}) (size {w} {h}) (layers {layers}))\n'

    def fill(head, layer):
        return f"({head} (stroke (width 0) (type solid)) (fill yes) (layer \"{layer}\"))\n"

    def footprint(ref, x, y, a, side, body):
        return (f'(footprint "T:{ref}" (layer "{side}.Cu") (at {x} {y} {a})\n'
                f'(property "Reference" "{ref}" (at 0 0 0) (layer "{side}.SilkS"))\n'
                f'(property "Value" "v" (at 0 0 0) (layer "{side}.Fab"))\n(attr smd)\n{body})\n')

    parts = []
    for ref, x, y, a, side in (("U1", 120, 110, 0, "F"), ("U2", 130, 112, 30, "F"), ("U3", 140, 115, 90, "B"),
                               ("U4", 125, 125, 137.5, "B")):
        p = f"{side}.Paste"
        parts.append(footprint(ref, x, y, a, side, pad("1", 0, 0, 2.0, 1.2, side, paste=False)
                               + pad("2", 3, 0.5, 1.0, 1.0, side) + pad("3", 0, 2.0, 2.4, 1.0, side, paste=False)
                               + fill("fp_rect (start -0.9 -0.5) (end -0.1 0.1)", p)
                               + fill("fp_rect (start 0.15 -0.5) (end 0.8 0.3)", p)
                               + fill("fp_poly (pts (xy -1.0 1.6) (xy -0.2 1.6) (xy -0.2 2.4) (xy -0.5 2.2))", p)
                               + fill("fp_circle (center 0.6 2.0) (end 0.9 2.0)", p)))
    for ref, x, y, a, side in (("R1", 112, 120, 0, "F"), ("R2", 145, 125, 45, "F"), ("R3", 135, 128, 0, "B")):
        parts.append(footprint(ref, x, y, a, side, pad("1", -0.8, 0, 0.9, 0.95, side)
                               + pad("2", 0.8, 0, 0.9, 0.95, side)))
    layers = "".join(f'({n} "{name}" {kind})\n' for n, name, kind in (
        (0, "F.Cu", "signal"), (2, "B.Cu", "signal"), (13, "F.Paste", "user"), (15, "B.Paste", "user"),
        (5, "F.SilkS", "user"), (7, "B.SilkS", "user"), (1, "F.Mask", "user"), (3, "B.Mask", "user"),
        (25, "Edge.Cuts", "user"), (35, "F.Fab", "user"), (33, "B.Fab", "user")))
    edge = "".join(f'(gr_line (start {a[0]} {a[1]}) (end {b[0]} {b[1]}) (stroke (width 0.1) (type solid)) '
                   f'(layer "Edge.Cuts"))\n' for a, b in (((100, 100), (160, 100)), ((160, 100), (160, 140)),
                                                         ((160, 140), (100, 140)), ((100, 140), (100, 100))))
    path.write_text(f'(kicad_pcb (version 20241229) (generator "test") (generator_version "9.0")\n'
                    f'(general (thickness 1.6))\n(paper "A4")\n(layers\n{layers})\n'
                    f'(setup (aux_axis_origin 100 140))\n(net 0 "")\n{"".join(parts)}{edge})\n', encoding="utf-8")


@pytest.mark.parametrize("reader", ["sexpr", "pcbnew"])
def test_stencil_openings_drawn_as_footprint_graphics_are_the_boards_paste(tmp_path, reader):
    """KiCad plots a footprint's filled graphics on its paste layer as paste openings: a fresh plot of a board whose
    parts draw them is neither older than the board nor another plot, with or without attributes (JLC's guide plots
    with --no-x2 --no-netlist)."""
    if not KICAD_CLI:
        pytest.skip("kicad-cli is not on this machine")
    if reader == "pcbnew":
        pytest.importorskip("pcbnew")
    path = tmp_path / "paste.kicad_pcb"
    paste_graphics_board(path)
    board = load_board(path, reader=reader)
    if reader == "pcbnew":                          # the two readers find the same openings
        sexpr = load_board(path, reader="sexpr")
        for a, b in zip(board.parts, sexpr.parts):
            assert len(a.pads) == len(b.pads) == len(a.copper_pads()) + 4 * a.ref.startswith("U")
            for p, q in zip(a.pads, b.pads):
                assert (p.number, p.copper, p.x, p.y) == (q.number, q.copper, pytest.approx(q.x, abs=1e-4),
                                                          pytest.approx(q.y, abs=1e-4))
    for opts in ([], ["--no-x2", "--no-netlist"]):
        out = tmp_path / ("plain" if not opts else "bare")
        subprocess.run([KICAD_CLI, "pcb", "export", "gerbers", "-l", "F.Cu,B.Cu,F.Paste,B.Paste,Edge.Cuts",
                        "--use-drill-file-origin", *opts, "-o", str(out), str(path)], check=True, capture_output=True)
        res = gb.gerber_origin(board, out)
        assert res.origin == pytest.approx((100.0, 140.0), abs=1e-3) and res.verdict == "matches"
        assert res.coverage == 1.0 and res.extra == [] and not res.stale and res.problems == [], res.messages
