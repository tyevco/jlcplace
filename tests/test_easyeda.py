"""EasyEDA records: parsing (synthetic records and every cached one), the pad rotation sense, agreement with the KiCad
boards, and the client's cache, back-off and circuit breaker (with a fake network)."""
import collections
import email.message
import email.utils
import io
import json
import logging
import math
import os
import random
import shutil
import time
import urllib.error
from pathlib import Path

import pytest

from jlcplace import convention as cv
from jlcplace import easyeda as ee
from jlcplace import geom
from jlcplace.convention import rot
from jlcplace.model import BOTTOM, TOP, Correction, EEFootprint, NotFound, Part

SCRATCH = Path(os.environ.get("JLCPLACE_TEST_EASYEDA", "/tmp/claude-0/-home-user-dial-me-spinner/"
                              "3708159a-bc3b-5167-8507-f5b08e1ce6c9/scratchpad/jlc"))
EE_RAW = SCRATCH / "ee_raw"
EE_DL = SCRATCH / "dl"
Z_FIX = SCRATCH / "src/z2amiller_jlccheck/tests/fixtures/jlcfootprint/easyeda"
DIAL = Path(os.environ.get("JLCPLACE_DIAL_ME_SPINNER", "/home/user/dial-me-spinner"))
OFFSETS = DIAL / "hardware/design/jlc_offsets.json"
BOARDS = [DIAL / f"hardware/kicad/{s}/dial-me-spinner-{s}.kicad_pcb" for s in ("main", "base")]

need_raw = pytest.mark.skipif(not EE_RAW.is_dir(), reason=f"no cached EasyEDA records in {EE_RAW}")
need_offsets = pytest.mark.skipif(not OFFSETS.is_file(), reason=f"{OFFSETS} absent")


def cached_files(dirs=(EE_RAW, EE_DL, Z_FIX)):
    return [f for d in dirs if d.is_dir() for f in sorted(d.glob("C*.json"))]


def load(path):
    return json.loads(Path(path).read_text())


# ------------------------------------------------------------------------------------------------------- builders

HX, HY = 4000.0005, 3000.0             # a typical EasyEDA footprint origin, canvas units


def fmt(v):
    return f"{v:.4f}"


def corners(x, y, w, h, r, sense=1):
    """EasyEDA's corner list for a RECT pad drawn at rotation r: TL, TR, BR, BL turned by R(sense * r)."""
    return [(x + q[0], y + q[1]) for q in (rot(u, sense * r) for u in ((-w / 2, -h / 2), (w / 2, -h / 2),
                                                                        (w / 2, h / 2), (-w / 2, h / 2)))]


def spine(x, y, w, h, r, sense=1):
    e = abs(w - h) / 2
    us = ((0, -e), (0, e)) if h > w else ((-e, 0), (e, 0))
    return [(x + q[0], y + q[1]) for q in (rot(u, sense * r) for u in us)]


def pts(points):
    return " ".join(f"{fmt(a)} {fmt(b)}" for a, b in points)


def pad(shape="RECT", x=HX, y=HY, w=4.0, h=2.0, layer=1, number="1", hole_r=0.0, points=None, r=0.0, hole_len=0.0,
        plated="Y"):
    if points is None:
        points = {"RECT": pts(corners(x, y, w, h, r)), "OVAL": pts(spine(x, y, w, h, r))}.get(shape, "")
    return "~".join(["PAD", shape, fmt(x), fmt(y), fmt(w), fmt(h), str(layer), "", number, fmt(hole_r), points,
                     fmt(r) if r else "0", "gge1", fmt(hole_len), "", plated, "0", "0", "0.2", f"{fmt(x)},{fmt(y)}"])


def pin(number, name, spice=None):
    """A symbol pin: number label `number`, name label `name`."""
    spice = number if spice is None else spice
    return (f"P~show~0~{spice}~0~0~0~gge{spice}~0^^0~0^^M 0 0 h10~#000000^^1~0~0~0~{name}~start~~~#000000^^"
            f"1~0~0~0~{number}~end~~~#000000^^0~0~0^^0~M 0 0")


def record(shapes, *, title="SOT-23-3_L2.9-W1.6-P1.90-LS2.8-BR", symbol=(), head=(HX, HY), **result):
    res = {"uuid": "sym", "title": "PART",
           "dataStr": {"head": {"x": 0, "y": 0, "c_para": {"package": title}}, "shape": list(symbol)},
           "packageDetail": {"uuid": "fp-uuid", "title": title, "updateTime": 1700000123,
                             "dataStr": {"head": {"x": head[0], "y": head[1], "utime": 1700000000, "uuid": "fp-uuid",
                                                  "c_para": {"package": title}}, "shape": list(shapes)}}}
    res.update(result)
    return {"success": True, "code": 0, "result": res}


def parse(shapes, **kw):
    r = ee.from_response("C1", record(shapes, **kw))
    assert isinstance(r, EEFootprint)
    return r


def approx(*v, abs=1e-6):
    return pytest.approx(v, abs=abs)


# ------------------------------------------------------------------------------------------------------- parsing

def test_units_origin_and_sizes():
    p = parse([pad(x=HX + 10, y=HY - 5, w=4, h=2)]).pads[0]
    assert (p.x, p.y, p.w, p.h) == approx(2.54, -1.27, 1.016, 0.508)
    assert (p.number, p.shape, p.layer, p.hole, p.angle, p.poly) == ("1", "rect", TOP, 0.0, 0.0, None)


@pytest.mark.parametrize("layer,expected", [(1, TOP), (2, BOTTOM), (11, "multi")])
@pytest.mark.parametrize("shape", ["RECT", "OVAL", "ELLIPSE", "POLYGON"])
def test_every_shape_on_every_copper_layer(shape, layer, expected):
    hole_r = 1.5 if layer == 11 else 0.0
    if shape == "POLYGON":
        outline = [(HX + 1, HY - 2), (HX + 5, HY - 2), (HX + 5, HY + 2), (HX + 2, HY + 2), (HX + 1, HY + 1)]
        p = parse([pad(shape, x=HX + 3, y=HY, w=4, h=4, layer=layer, hole_r=hole_r, points=pts(outline))]).pads[0]
        assert p.poly == [approx((a - HX) * 0.254, (b - HY) * 0.254) for a, b in outline]
        assert (p.w, p.h) == approx(4 * 0.254, 4 * 0.254)
        assert geom.pad_contains(p, (p.x, p.y))
    else:
        p = parse([pad(shape, w=6, h=3, layer=layer, hole_r=hole_r)]).pads[0]
        assert p.poly is None and (p.w, p.h) == approx(6 * 0.254, 3 * 0.254)
    assert p.shape == shape.lower() and p.layer == expected
    assert p.hole == pytest.approx(2 * hole_r * 0.254)


def test_pads_without_copper_are_left_out():
    shapes = [pad(number="1"), pad(number="2", layer=3), pad(number="3", layer=11, hole_r=2, plated="N"),
              pad(number="4", layer=1, plated="N")]          # unplated but no hole: still an SMD copper pad
    assert [p.number for p in parse(shapes).pads] == ["1", "4"]


@pytest.mark.parametrize("r", [0, 30, 90, 145, 180, 270, 333.5])
def test_rotation_is_kept_in_R_sense(r):
    """A pad drawn at rotation r (points in R's sense) parses to angle r, and its outline is EasyEDA's."""
    x, y, w, h = HX + 7, HY + 3, 5.0, 2.0
    f = parse([pad("RECT", x, y, w, h, r=r), pad("OVAL", x, y, w, h, number="2", r=r)])
    rect, oval = f.pads
    assert rect.angle == oval.angle == cv.norm_angle(r)
    want = [((a - HX) * 0.254, (b - HY) * 0.254) for a, b in corners(x, y, w, h, r)]
    out = geom.pad_outline(rect)
    assert max(min(math.dist(p, q) for q in out) for p in want) < 1e-4
    for a, b in spine(x, y, w, h, r):
        assert geom.pad_contains(oval, ((a - HX) * 0.254, (b - HY) * 0.254), tol=1e-6)


@pytest.mark.parametrize("shape", ["RECT", "OVAL"])
def test_outline_wins_when_it_shows_the_other_sense(shape, caplog):
    x, y, w, h, r = HX, HY, 5.0, 2.0, 30.0
    other = corners(x, y, w, h, r, -1) if shape == "RECT" else spine(x, y, w, h, r, -1)
    with caplog.at_level(logging.WARNING, logger="jlcplace.easyeda"):
        p = parse([pad(shape, x, y, w, h, r=r, points=pts(other))]).pads[0]
    assert p.angle == 330.0 and "other sense" in caplog.text
    # an outline that matches neither sense (stale) does not override the field
    p = parse([pad(shape, x, y, w, h, r=r, points=pts(corners(x + 50, y, w, h, 0)[:4 if shape == "RECT" else 2]))])
    assert p.pads[0].angle == 30.0


def with_field(i, value, shape="RECT"):
    f = pad(shape).split("~")
    f[i] = value
    return "~".join(f)


@pytest.mark.parametrize("bad", [
    with_field(2, "abc"), with_field(4, "nan"), with_field(11, "ninety"), with_field(9, "inf"),
    pad("POLYGON", points=f"{HX} {HY} {HX + 1} {HY}"), pad("POLYGON", points="1 2 3"), "PAD~RECT~1~2~3"])
def test_malformed_pads_raise(bad):
    with pytest.raises(ee.EEError):
        ee.from_response("C1", record([bad]))


def test_footprint_without_origin_raises():
    rec = record([pad()])
    del rec["result"]["packageDetail"]["dataStr"]["head"]["x"]
    with pytest.raises(ee.EEError, match="origin"):
        ee.from_response("C1", rec)


def test_marks_tokens_title_uuid_utime():
    shapes = [pad(), f"CIRCLE~{HX + 5}~{HY + 6}~0.118~0.2362~101~gge1~0~~", f"CIRCLE~{HX}~{HY}~0.4~0.8~12~gge2~0~~"]
    f = parse(shapes, title="SOT-23-6_L2.9-W1.6-P0.95-LS2.8-BL")
    assert f.marks == [approx(5 * 0.254, 6 * 0.254)]
    assert (f.title, f.package, f.tokens) == ("SOT-23-6_L2.9-W1.6-P0.95-LS2.8-BL",) * 2 + (["BL"],)
    assert (f.uuid, f.utime, f.key(), f.lcsc) == ("fp-uuid", 1700000123, "fp-uuid:1700000123", "C1")
    assert ee.title_tokens("SW-SMD_4P-L5.1-W5.1-P3.70-LS6.5-TL_H1.5") == ["TL"]
    assert ee.title_tokens("LED_RD-1_cw_TL-TL") == ["RD", "CW", "TL"]
    assert ee.title_tokens("R0402") == []
    rec = record([pad()])
    del rec["result"]["packageDetail"]["title"]
    assert ee.from_response("C1", rec).title == "SOT-23-3_L2.9-W1.6-P1.90-LS2.8-BR"   # from head.c_para.package


def _plus(x_mm, y_mm, size_mm, layer=3, filled=False):
    """A "+" drawn at (x, y) mm from the origin: two tracks, or two thin filled rectangles (SOLIDREGION)."""
    x, y, h, t = HX + x_mm / 0.254, HY + y_mm / 0.254, size_mm / 2 / 0.254, 0.05 / 0.254
    if filled:
        return [(f"SOLIDREGION~{layer}~~M {x - h} {y - t} L {x + h} {y - t} L {x + h} {y + t} L {x - h} {y + t} "
                 "Z~solid~ggs1~~~~0"),
                (f"SOLIDREGION~{layer}~~M {x - t} {y - h} L {x + t} {y - h} L {x + t} {y + h} L {x - t} {y + h} "
                 "Z~solid~ggs2~~~~0")]
    return [f"TRACK~0.6~{layer}~~{x - h} {y} {x + h} {y}~ggt1~0", f"TRACK~0.6~{layer}~~{x} {y - h} {x} {y + h}~ggt2~0"]


def test_part_categories_bidirectional_token_and_plus_marks():
    """ee-1 / truth-7 / geo-4: result.tags (EasyEDA's part categories), the -BI title token, and the "+" a footprint
    draws beside its positive pad (silkscreen or document layer), which parts.ee_positive_pad reads."""
    from jlcplace.parts import ee_positive_pad
    pads = [pad(x=HX - 1.5 / 0.254, number="1"), pad(x=HX + 1.5 / 0.254, number="2")]
    f = parse(pads, title="SMF_L2.8-W1.8-LS3.7-BI", tags=["TVS", 7, None])
    assert (f.tags, f.tokens, f.plus) == (["TVS", "7"], ["BI"], [])
    for layer, filled in ((3, False), (12, False), (3, True)):
        f = parse(pads + _plus(-2.6, 0.0, 0.5, layer, filled), title="CAP-SMD_L3.2-W1.6-RD")
        assert len(f.plus) == 1 and f.plus[0][:2] == approx(-2.6, 0.0, abs=1e-3), (layer, filled, f.plus)
        assert ee_positive_pad(f) == "1"
    assert ee_positive_pad(parse(pads + _plus(2.6, 0.0, 0.5))) == "2"
    # a cross at the origin is no polarity mark; nor are marks beside both pads, a "+" on a copper layer, or a
    # diode's drawn symbol (a polyline, a bar meeting it at its end)
    assert ee_positive_pad(parse(pads + _plus(0.0, 0.0, 0.5))) is None
    assert ee_positive_pad(parse(pads + _plus(-2.6, 0.0, 0.5) + _plus(2.6, 0.0, 0.5))) is None
    assert parse(pads + _plus(-2.6, 0.0, 0.5, layer=1)).plus == []
    x0, y0 = HX + 2.4 / 0.254, HY
    tri = [f"TRACK~0.6~3~~{x0} {y0 - 2} {x0} {y0 + 2} {x0 + 3} {y0} {x0} {y0 - 2}~ggd1~0",
           f"TRACK~0.6~3~~{x0 + 3} {y0 - 2} {x0 + 3} {y0 + 2}~ggd2~0",
           f"TRACK~0.6~3~~{x0 + 1.5} {y0} {x0 + 4.5} {y0}~ggd3~0"]
    assert parse(pads + tri).plus == []
    # malformed shapes are skipped, not fatal
    assert parse(pads + ["TRACK~0.6~3~~1 2 x y~gg1~0", "SOLIDREGION~3~~M 1 Q 2 3 Z~solid~gg2~~~~0"]).plus == []


def test_datastr_given_as_a_json_string():
    rec = record([pad()], symbol=[pin("1", "A")])
    rec["result"]["packageDetail"]["dataStr"] = json.dumps(rec["result"]["packageDetail"]["dataStr"])
    rec["result"]["dataStr"] = json.dumps(rec["result"]["dataStr"])
    f = ee.from_response("C1", rec)
    assert len(f.pads) == 1 and f.pin_names == {"1": "A"}


def test_pin_names_single_and_multi_part():
    f = parse([pad()], symbol=[pin("1", "OUT"), pin("2", "IN-"), pin("", "IN+", spice="3")])
    assert f.pin_names == {"1": "OUT", "2": "IN-", "3": "IN+"}         # empty number label: the spice number

    def unit(*p):
        return {"dataStr": {"head": {"x": 0, "y": 0}, "shape": list(p)}}

    # result.subparts (as easyeda2kicad reads them); the main dataStr holds no pins
    f = parse([pad()], subparts=[unit(pin("1", "1A"), pin("7", "")), unit(pin("4", "2A"), pin("7", "GND"))])
    assert f.pin_names == {"1": "1A", "7": "GND", "4": "2A"}            # first non-empty name wins
    # dataStr.subparts, nested
    rec = record([pad()], symbol=[pin("8", "VCC")])
    rec["result"]["dataStr"]["subparts"] = [unit(pin("1", "A")), {"head": {}, "shape": [pin("2", "B")],
                                                                    "subparts": [unit(pin("3", "C"))]}]
    assert ee.from_response("C1", rec).pin_names == {"8": "VCC", "1": "A", "2": "B", "3": "C"}


def test_pin_names_prefer_the_units_over_a_main_symbol_that_repeats_one():
    """ee-8: C111548's main symbol repeats its first unit with the '/' between a pin's functions lost
    ('PA0MCI0_CKTCLK3' against the unit's 'PA0/MCI0_CK/TCLK3'). The unit's name is the one that splits into
    functions KiCad's pin names can match; the main symbol still names what no unit does."""
    def unit(*p):
        return {"dataStr": {"head": {"x": 0, "y": 0}, "shape": list(p)}}

    f = parse([pad()], symbol=[pin("L1", "PA0MCI0_CKTCLK3"), pin("A1", "VSS"), pin("B2", "NC")],
              subparts=[unit(pin("L1", "PA0/MCI0_CK/TCLK3"), pin("A1", "")), unit(pin("C3", "PB1"))])
    assert f.pin_names == {"L1": "PA0/MCI0_CK/TCLK3", "A1": "VSS", "B2": "NC", "C3": "PB1"}


MULTI_UNIT = ("C20916", "C111548", "C108023")          # real multi-unit symbols (BGA-324 x2, a 3-unit module)


@pytest.mark.cache
def test_multi_unit_records_name_every_pad():
    """ee-8: every multi-unit record in the local caches (the CI e2e job fetches MULTI_UNIT; nothing of them is in
    this repository) names exactly its footprint's pads, through all of its units."""
    found = []
    for f in cached_files() + cached_files((ee.default_cache_dir(),)):
        rec = load(f)
        res = rec.get("result") if isinstance(rec, dict) else None
        if not isinstance(res, dict) or len(ee._units({"dataStr": res.get("dataStr"),
                                                       "subparts": res.get("subparts")}, "symbol")) < 2:
            continue
        fp = ee.from_response(f.stem, rec)
        numbers = {p.number for p in fp.pads if p.number}
        names = ee.pin_names(res)
        assert set(names) == numbers, f.stem
        assert all(names.values()), f.stem
        by_units: dict[str, str] = {}            # a unit's name wins over the main symbol's
        for u in ee._units({"dataStr": res.get("dataStr"), "subparts": res.get("subparts")}, "symbol")[1:]:
            for s in u.get("shape") or []:
                p = ee._pin(s) if isinstance(s, str) and s.startswith("P~") else None
                if p and p[1]:
                    by_units.setdefault(p[0], p[1])
        assert {k: names[k] for k in by_units} == by_units, f.stem
        found.append(f.stem)
    if not found:
        pytest.skip(f"no multi-unit EasyEDA record cached (`jlcplace fetch {' '.join(MULTI_UNIT)}` into {EE_RAW})")


def test_response_envelopes():
    nf = ee.from_response("c4167968", {"success": False, "code": 404, "message": "Component not found"})
    assert nf == NotFound("C4167968", "Component not found")
    rec = record([pad()])
    del rec["result"]["packageDetail"]
    nf = ee.from_response("C1", rec)
    assert isinstance(nf, NotFound) and "packageDetail missing" in nf.message
    rec["result"]["packageDetail"] = {"uuid": "x", "title": "y", "dataStr": ""}
    assert isinstance(ee.from_response("C1", rec), NotFound)
    assert isinstance(ee.from_response("C1", {"success": True, "result": None}), NotFound)
    with pytest.raises(ee.EEError, match="500"):
        ee.from_response("C1", {"success": False, "code": 500, "message": "server"})
    with pytest.raises(ee.EEError):
        ee.from_response("C1", ["not", "a", "dict"])
    with pytest.raises(ee.EEError, match="packageDetail"):
        ee.parse_record("C1", rec["result"] | {"packageDetail": None})


@pytest.mark.parametrize("bad", ["../C1", "C12a", "", "C", "1234", "C1/../../x", "C1\n", "C0402", "C0", "C１２"])
def test_codes_are_validated(bad):
    with pytest.raises(ee.EEError):
        ee.check_code(bad if bad != "C1\n" else "C1\nC2")
    assert ee.check_code(" c2040 ") == "C2040"


# ------------------------------------------------------------------------------------------------------- cached records

@pytest.mark.cache
@need_raw
def test_every_cached_record_parses():
    files = cached_files()
    seen = collections.Counter()
    for f in files:
        resp = load(f)
        code = f.stem
        res = ee.from_response(code, resp)
        if code == "C4167968":
            assert res == NotFound("C4167968", "Component not found")
            continue
        assert isinstance(res, EEFootprint), (f, res)
        rows = [s.split("~") for s in resp["result"]["packageDetail"]["dataStr"]["shape"] if s.startswith("PAD~")]
        assert len(res.pads) == sum(r[6] in ("1", "2", "11") for r in rows), f
        assert res.title and res.uuid and res.utime > 0 and res.lcsc == code
        for p in res.pads:
            assert geom.pad_contains(p, (p.x, p.y)), (f, p)
            assert p.layer in (TOP, BOTTOM, "multi") and (p.hole > 0) == (p.layer == "multi")
        if f.parent == EE_RAW:
            assert len(res.marks) == 1, f          # every LCSC footprint here has one polarity dot
        seen[f.parent.name] += 1
    assert seen["ee_raw"] >= 50, seen


def _pad_rows(files):
    """(code, parsed pad, EasyEDA's point list in the ee frame) for every copper pad."""
    for f in files:
        resp = load(f)
        if not resp.get("success"):
            continue
        res = ee.from_response(f.stem, resp)
        ds = resp["result"]["packageDetail"]["dataStr"]
        ox, oy = float(ds["head"]["x"]), float(ds["head"]["y"])
        rows = [s.split("~") for s in ds["shape"] if s.startswith("PAD~") and s.split("~")[6] in ("1", "2", "11")]
        for row, p in zip(rows, res.pads, strict=True):
            v = [float(t) for t in row[10].split()]
            listed = [((v[i] - ox) * 0.254, (v[i + 1] - oy) * 0.254) for i in range(0, len(v), 2)]
            yield f.stem, p, listed


@pytest.mark.cache
@need_raw
def test_rotation_sense_from_easyeda_corner_order():
    """EasyEDA's PAD rotation has R's sense (counter-clockwise on screen, y down), so EEPad.angle = r.

    A pad drawn at r = 0 lists its corners TL, TR, BR, BL (the plurality below; edited pads keep a rotated or mirrored
    list). Every rotated pad lists exactly R(r) applied to that sequence. The opposite sense would put the first corner
    on the other side of the diagonal (R(-90) . TL = TR, not BL), and no pad does that."""
    files = cached_files((EE_RAW, Z_FIX))
    rotated, codes, starts0 = collections.Counter(), set(), collections.Counter()
    for code, p, listed in _pad_rows(files):
        if p.shape != "rect":
            continue
        assert len(listed) == 4
        if p.angle == 0:
            first = (round(math.copysign(1, listed[0][0] - p.x)), round(math.copysign(1, listed[0][1] - p.y)))
            starts0[first] += 1
            continue
        if p.angle not in (90.0, 270.0):
            continue
        model = corners(p.x, p.y, p.w, p.h, p.angle)
        other = corners(p.x, p.y, p.w, p.h, p.angle, -1)
        assert max(math.dist(a, b) for a, b in zip(model, listed, strict=True)) < 2e-3, (code, p.number, listed)
        assert min(math.dist(a, b) for a, b in zip(other, listed, strict=True)) > 0.1, (code, p.number)
        rotated[p.angle] += 1
        codes.add(code)
    assert rotated[90.0] >= 5 and rotated[270.0] >= 5 and len(codes) >= 3, (rotated, codes)
    assert starts0.most_common(1)[0][0] == (-1, -1)          # TL first is how a pad at r = 0 is drawn


@pytest.mark.cache
@need_raw
def test_cached_point_lists_are_the_parsed_outlines():
    """Sense-independent at multiples of 90: EasyEDA's own outline of every pad equals the parsed geometry."""
    n = collections.Counter()
    for code, p, listed in _pad_rows(cached_files((EE_RAW, Z_FIX))):
        if p.shape == "rect":
            out = geom.pad_outline(p)
            assert max(min(math.dist(a, b) for b in out) for a in listed) < 2e-3, (code, p.number)
        elif p.shape == "polygon":
            assert max(math.dist(a, b) for a, b in zip(listed, p.poly, strict=True)) < 1e-5
        elif p.shape == "oval":
            assert len(listed) == 2 and math.dist(*listed) == pytest.approx(abs(p.w - p.h), abs=1e-5)
            assert all(geom.pad_contains(p, q, tol=1e-6) for q in listed)
        n[p.shape] += 1
    assert n["rect"] > 300 and n["polygon"] >= 4 and n["oval"] >= 4, n


@pytest.mark.cache
@need_raw
@need_offsets
def test_parsed_centres_match_jlc_offsets_ee_lists():
    """jlc_offsets.json's `ee` lists came from an earlier parser; this one gives the same pad centres (4 decimals)."""
    offs = load(OFFSETS)
    checked = 0
    for code, o in offs.items():
        if not isinstance(o, dict) or "ee" not in o or not (EE_RAW / f"{code}.json").exists():
            continue
        res = ee.from_response(code, load(EE_RAW / f"{code}.json"))
        mine = sorted((p.number, p.x, p.y) for p in res.pads)
        theirs = sorted((n, x, y) for n, x, y in o["ee"])
        assert [m[0] for m in mine] == [t[0] for t in theirs], code
        for m, t in zip(mine, theirs, strict=True):
            assert abs(m[1] - t[1]) <= 6e-5 and abs(m[2] - t[2]) <= 6e-5, (code, m, t)
        checked += 1
    assert checked >= 50


@pytest.mark.pcbnew
@pytest.mark.cache
@need_raw
@need_offsets
def test_ee_pads_land_on_the_kicad_pads():
    """Every EE pad, mapped with the stored correction (library = R(rot) . ee + d) and the part's placement, lies on a
    copper pad of the KiCad footprint (pcbnew's own hit test), and every elongated pad points the same way."""
    pcbnew = pytest.importorskip("pcbnew")
    if not all(b.exists() for b in BOARDS):
        pytest.skip("dial-me-spinner boards absent")
    offs = load(OFFSETS)

    def nm(v):
        return int(round(v * 1e6))

    landed, aligned, codes = 0, 0, set()
    for path in BOARDS:
        board = pcbnew.LoadBoard(str(path))
        for fp in board.GetFootprints():
            code = fp.GetFieldText("LCSC") if fp.HasFieldByName("LCSC") else ""
            o = offs.get(code)
            if not isinstance(o, dict) or "ee" not in o:
                continue
            res = ee.from_response(code, load(EE_RAW / f"{code}.json"))
            flip = fp.IsFlipped()
            part = Part(fp.GetReference(), BOTTOM if flip else TOP, fp.GetPosition().x / 1e6,
                        fp.GetPosition().y / 1e6, fp.GetOrientationDegrees())
            corr = Correction(rot=o["rot"], dx=o["dx"], dy=o["dy"])
            layer = pcbnew.B_Cu if flip else pcbnew.F_Cu
            copper = [k for k in fp.Pads() if k.GetAttribute() != pcbnew.PAD_ATTRIB_NPTH and k.IsOnLayer(layer)]

            def to_board(e, part=part, corr=corr):
                return cv.library_to_board(part, cv.ee_to_library(corr, e))

            for p in res.pads:
                q = to_board((p.x, p.y))
                hit = [k for k in copper if k.HitTest(pcbnew.VECTOR2I(nm(q[0]), nm(q[1])), 0, layer)]
                assert hit, (path.name, part.ref, code, p.number, q)
                landed += 1
                k = hit[0]
                sx, sy = k.GetSize().x, k.GetSize().y
                if p.shape == "polygon" or max(p.w, p.h) < 1.3 * min(p.w, p.h) or max(sx, sy) < 1.3 * min(sx, sy):
                    continue
                tip = rot((1.0, 0.0) if p.w > p.h else (0.0, 1.0), p.angle)
                a, b = q, to_board((p.x + tip[0], p.y + tip[1]))
                ee_axis = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
                kv = rot((1.0, 0.0) if sx > sy else (0.0, 1.0), k.GetOrientationDegrees())
                d = (ee_axis - math.degrees(math.atan2(kv[1], kv[0]))) % 180
                assert min(d, 180 - d) < 0.01, (part.ref, code, p.number)
                aligned += 1
            codes.add(code)
    want = {c for c, o in offs.items() if isinstance(o, dict) and "ee" in o}
    assert codes == want, want - codes
    assert landed >= 700 and aligned >= 200, (landed, aligned)


@pytest.mark.cache
@need_raw
def test_c4167968_is_not_found(tmp_path):
    """The board's R26 code: EasyEDA answers 'Component not found' (cached), so the part cannot be simulated."""
    def no_network(*a, **k):
        raise AssertionError("no request expected")

    assert ee.EEClient(EE_RAW, offline=True, opener=no_network).get("C4167968") == \
        NotFound("C4167968", "Component not found")
    shutil.copy(EE_RAW / "C4167968.json", tmp_path / "C4167968.json")      # fresh mtime: inside the TTL
    assert ee.EEClient(tmp_path, opener=no_network).get("C4167968") == NotFound("C4167968", "Component not found")


@pytest.mark.network
@pytest.mark.skipif(os.environ.get("JLCPLACE_NETWORK_TESTS") != "1", reason="set JLCPLACE_NETWORK_TESTS=1")
@need_raw
def test_live_record_matches_the_cached_one(tmp_path):
    live = ee.EEClient(tmp_path, min_interval=1.0, max_failures=2).get("C2500")
    cached = ee.EEClient(EE_RAW, offline=True).get("C2500")
    assert isinstance(live, EEFootprint)
    assert [(p.number, p.x, p.y, p.w, p.h, p.angle) for p in live.pads] == \
        [(p.number, p.x, p.y, p.w, p.h, p.angle) for p in cached.pads]


# ------------------------------------------------------------------------------------------------------- client

class Reply:
    def __init__(self, body, status=200):
        self.body, self.status = body, status

    def read(self):
        return self.body

    def getcode(self):
        return self.status

    def close(self):
        pass


def http_error(url, status, body=b"", headers=None):
    m = email.message.Message()
    for k, v in (headers or {}).items():
        m[k] = v
    return urllib.error.HTTPError(url, status, "error", m, io.BytesIO(body))


class FakeNet:
    """An injectable urlopen: answers in order, (status, body[, headers]) or an exception; records the requests."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.urls, self.timeouts, self.headers = [], [], []

    def __call__(self, req, timeout=None):
        self.urls.append(req.full_url)
        self.timeouts.append(timeout)
        self.headers.append({k.lower(): v for k, v in req.header_items()})
        if not self.answers:
            raise AssertionError(f"unexpected request {req.full_url}")
        a = self.answers.pop(0)
        if isinstance(a, BaseException):
            raise a
        status, body, *hdrs = a
        if status >= 400:
            raise http_error(req.full_url, status, body, hdrs[0] if hdrs else None)
        return Reply(body, status)


class Clock:
    """Fake time: sleep() advances both the monotonic clock and the wall clock."""

    def __init__(self):
        self.t, self.t0, self.sleeps = 0.0, time.time(), []

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s

    def clock(self):
        return self.t

    def now(self):
        return self.t0 + self.t


class MaxJitter:
    def uniform(self, a, b):
        return b


def make(tmp_path, net, *, rng=None, **kw):
    clk = Clock()
    c = ee.EEClient(tmp_path / "cache", opener=net, sleep=clk.sleep, clock=clk.clock, now=clk.now,
                    rng=rng or MaxJitter(), **kw)
    return c, clk


OK = json.dumps(record([pad(number="1"), pad(number="2", x=HX + 10)]), indent=1).encode() + b"\n"
NF = b'{"success":false,"code":404,"message":"Component not found"}'
HTML = b"<html><head><title>403 Forbidden</title></head><body>Request blocked. (CloudFront)</body></html>"


def test_fetch_stores_the_raw_response_verbatim_and_parses_from_the_file(tmp_path):
    net = FakeNet((200, OK))
    c, clk = make(tmp_path, net)
    f = c.get("c2500")
    assert isinstance(f, EEFootprint) and [p.number for p in f.pads] == ["1", "2"]
    assert net.urls == [ee.API_URL.format(code="C2500")] and net.timeouts == [30.0]
    assert net.headers[0]["user-agent"].startswith("jlcplace/")
    path = tmp_path / "cache" / "C2500.json"
    assert path.read_bytes() == OK and os.listdir(tmp_path / "cache") == ["C2500.json"]
    assert c.raw("C2500") == json.loads(OK)
    # a cache hit makes no request; parsing reads the file, so an edited file is what comes back
    rec = json.loads(OK)
    rec["result"]["packageDetail"]["dataStr"]["shape"].pop()
    path.write_text(json.dumps(rec))
    assert len(c.get("C2500").pads) == 1 and c.requests == 1 and clk.sleeps == []


def test_cache_hit_needs_no_network(tmp_path):
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / "C7.json").write_bytes(OK)
    c, _ = make(tmp_path, FakeNet())
    assert isinstance(c.get("C7"), EEFootprint) and c.requests == 0
    assert c.raw("C8") is None


def test_not_found_is_cached_and_refetched_after_the_ttl(tmp_path):
    net = FakeNet((200, NF), (200, OK))
    c, clk = make(tmp_path, net)
    assert c.get("C4167968") == NotFound("C4167968", "Component not found")
    assert c.get("C4167968") == NotFound("C4167968", "Component not found") and c.requests == 1
    path = c.cache_path("C4167968")
    assert path.read_bytes() == NF
    old = clk.now() - 8 * 86400
    os.utime(path, (old, old))
    offline = ee.EEClient(tmp_path / "cache", offline=True, opener=FakeNet())
    assert isinstance(offline.get("C4167968"), NotFound)                  # offline: the old answer stands
    assert isinstance(c.get("C4167968"), EEFootprint) and c.requests == 2  # online: fetched again
    assert path.read_bytes() == OK


def test_stale_not_found_survives_a_failed_refetch(tmp_path):
    c, _ = make(tmp_path, FakeNet((404, HTML)))
    c.cache_dir.mkdir()
    path = c.cache_path("C9")
    path.write_bytes(NF)
    old = time.time() - 30 * 86400
    os.utime(path, (old, old))
    assert c.get("C9") == NotFound("C9", "Component not found") and c.requests == 1


def test_success_without_package_detail_is_a_cached_not_found(tmp_path):
    body = json.dumps({"success": True, "code": 0, "result": {"uuid": "u", "title": "x", "dataStr": {}}}).encode()
    c, _ = make(tmp_path, FakeNet((200, body)))
    nf = c.get("C55")
    assert isinstance(nf, NotFound) and "no footprint" in nf.message and "packageDetail" in nf.message
    assert c.cache_path("C55").read_bytes() == body
    assert isinstance(c.get("C55"), NotFound) and c.requests == 1


def test_403_html_page_backs_off_with_jitter_then_succeeds(tmp_path):
    net = FakeNet((403, HTML), (403, HTML), (200, OK))
    c, clk = make(tmp_path, net)
    assert isinstance(c.get("C1"), EEFootprint)
    assert clk.sleeps == [3.0 * 1.25, 6.0 * 1.25] and c.failures == 0
    assert os.listdir(c.cache_dir) == ["C1.json"]                          # the HTML was never stored
    # real jitter stays within [base, 1.25 base]
    c, clk = make(tmp_path / "b", FakeNet((429, b""), (503, HTML), (500, b"{}"), (200, OK)), rng=random.Random(7))
    c.get("C1")
    for s, base in zip(clk.sleeps, ee.BACKOFF[:3], strict=True):
        assert base <= s <= 1.25 * base
    assert len(clk.sleeps) == 3


def test_retry_after_seconds_and_http_date(tmp_path):
    net = FakeNet((429, b"", {"Retry-After": "20"}), (200, OK))
    c, clk = make(tmp_path, net)
    c.get("C1")
    assert clk.sleeps == [20.0]
    clk2 = Clock()
    date = email.utils.formatdate(clk2.now() + 40, usegmt=True)
    net = FakeNet((503, HTML, {"Retry-After": date}), (200, OK))
    c = ee.EEClient(tmp_path / "d", opener=net, sleep=clk2.sleep, clock=clk2.clock, now=clk2.now, rng=MaxJitter())
    c.get("C1")
    assert clk2.sleeps == [pytest.approx(40, abs=1.0)]
    # a short Retry-After never shortens the back-off
    c, clk = make(tmp_path / "e", FakeNet((429, b"", {"Retry-After": "1"}), (200, OK)))
    c.get("C1")
    assert clk.sleeps == [3.75]


def test_retry_after_beyond_max_wait_gives_up(tmp_path):
    c, clk = make(tmp_path, FakeNet((429, b"", {"Retry-After": "3600"})))
    with pytest.raises(ee.EEError, match="3600"):
        c.get("C1")
    assert clk.sleeps == [] and c.disabled


def test_max_failures_stops_the_network(tmp_path):
    net = FakeNet(*[(503, HTML)] * 5)
    c, clk = make(tmp_path, net)
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / "C77.json").write_bytes(OK)
    with pytest.raises(ee.EEError, match="5 consecutive failures"):
        c.get("C1")
    assert c.requests == 5 and clk.sleeps == [b * 1.25 for b in ee.BACKOFF[:4]] and c.disabled
    with pytest.raises(ee.EEError, match="disabled"):
        c.get("C2")
    assert c.requests == 5 and not net.answers
    assert isinstance(c.get("C77"), EEFootprint)                           # the cache still works
    assert not (tmp_path / "cache" / "C1.json").exists()
    # longer runs use 48 s and then keep it
    c, clk = make(tmp_path / "b", FakeNet(*[(503, b"")] * 7), max_failures=7)
    with pytest.raises(ee.EEError):
        c.get("C1")
    assert clk.sleeps == [b * 1.25 for b in ee.BACKOFF + (48.0,)]


def test_a_success_resets_the_failure_count(tmp_path):
    net = FakeNet((503, b""), (200, OK), *[(503, b"")] * 4, (200, OK))
    c, _ = make(tmp_path, net)
    assert isinstance(c.get("C1"), EEFootprint) and c.failures == 0
    assert isinstance(c.get("C2"), EEFootprint) and c.requests == 7


def test_min_interval_between_requests(tmp_path):
    c, clk = make(tmp_path, FakeNet((200, OK), (200, NF), (200, OK)), min_interval=0.7)
    c.get("C1")
    c.get("C2")
    clk.t += 5.0
    c.get("C3")
    assert clk.sleeps == [pytest.approx(0.7)]


def test_offline_never_uses_the_network(tmp_path):
    net = FakeNet()
    c = ee.EEClient(tmp_path, offline=True, opener=net)
    with pytest.raises(ee.EEError, match="not in the cache"):
        c.get("C1")
    with pytest.raises(ee.EEError, match="offline"):
        c.get("C1", refresh=True)
    (tmp_path / "C2.json").write_text("{truncated")
    with pytest.raises(ee.EEError, match="not valid JSON"):
        c.get("C2")
    assert net.urls == []


def test_refresh_refetches_a_cached_record(tmp_path):
    c, _ = make(tmp_path, FakeNet((200, OK), (200, NF)))
    assert isinstance(c.get("C1"), EEFootprint)
    assert isinstance(c.get("C1", refresh=True), NotFound) and c.cache_path("C1").read_bytes() == NF


def test_corrupt_cache_file_is_refetched_online(tmp_path):
    c, _ = make(tmp_path, FakeNet((200, OK)))
    c.cache_dir.mkdir()
    c.cache_path("C1").write_text("<html>")
    assert isinstance(c.get("C1"), EEFootprint) and c.cache_path("C1").read_bytes() == OK


def test_non_json_200_is_retried_and_never_cached(tmp_path):
    c, clk = make(tmp_path, FakeNet((200, b"<html>captive portal</html>"), (200, OK)))
    assert isinstance(c.get("C1"), EEFootprint) and len(clk.sleeps) == 1
    assert c.cache_path("C1").read_bytes() == OK


def test_http_404_page_is_an_error_but_a_json_404_is_not_found(tmp_path):
    c, _ = make(tmp_path, FakeNet((404, b"<html>Not Found</html>")))
    with pytest.raises(ee.EEError, match="404"):
        c.get("C1")
    assert c.requests == 1 and not c.cache_path("C1").exists()
    c, _ = make(tmp_path / "b", FakeNet((404, NF)))
    assert c.get("C1") == NotFound("C1", "Component not found") and c.cache_path("C1").read_bytes() == NF


def test_errors_inside_a_200(tmp_path):
    bad = json.dumps({"success": False, "code": 400, "message": "bad request"}).encode()
    c, _ = make(tmp_path, FakeNet((200, bad)))
    with pytest.raises(ee.EEError, match="bad request"):
        c.get("C1")
    assert c.requests == 1
    busy = json.dumps({"success": False, "code": 429, "message": "too many"}).encode()
    c, clk = make(tmp_path / "b", FakeNet((200, busy), (200, OK)))
    assert isinstance(c.get("C1"), EEFootprint) and c.requests == 2 and len(clk.sleeps) == 1
    c, _ = make(tmp_path / "c", FakeNet((200, b'{"result": 1}')))         # not an API answer, not rate limiting
    with pytest.raises(ee.EEError, match="unexpected JSON"):
        c.get("C1")
    assert c.requests == 1 and not c.cache_path("C1").exists()


def test_network_errors_are_retried(tmp_path):
    c, clk = make(tmp_path, FakeNet(urllib.error.URLError("timed out"), TimeoutError("slow"), (200, OK)))
    assert isinstance(c.get("C1"), EEFootprint) and len(clk.sleeps) == 2


def test_the_cache_dir_holds_only_raw_responses(tmp_path):
    net = FakeNet((200, OK), (403, HTML), (200, NF), (400, b"<html>bad</html>"), (200, b"<html>x</html>"), (200, OK))
    c, _ = make(tmp_path, net)
    c.get("C1")
    c.get("C2")
    with pytest.raises(ee.EEError):
        c.get("C3")
    c.get("C4")
    assert {p.name: p.read_bytes() for p in c.cache_dir.iterdir()} == {"C1.json": OK, "C2.json": NF, "C4.json": OK}


def test_cache_write_failure_is_an_eeerror(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    c = ee.EEClient(blocker / "cache", opener=FakeNet((200, OK)), sleep=lambda s: None)
    with pytest.raises(ee.EEError, match="cache"):
        c.get("C1")


def test_default_cache_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("JLCPLACE_CACHE", str(tmp_path / "a"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "x"))
    assert ee.default_cache_dir() == tmp_path / "a"
    monkeypatch.delenv("JLCPLACE_CACHE")
    assert ee.default_cache_dir() == tmp_path / "x" / "jlcplace"
    monkeypatch.delenv("XDG_CACHE_HOME")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert ee.default_cache_dir() == tmp_path / "home" / ".cache" / "jlcplace"
    assert ee.EEClient().cache_dir == tmp_path / "home" / ".cache" / "jlcplace"


# ------------------------------------------------------------------------------------------------------- review: net-1

def test_retry_waits_have_a_budget_for_the_whole_run(tmp_path):
    """net-1: each success reset the failure count, so a server answering 429 with Retry-After: 290 three times per
    code held 10 codes for 2.4 hours. The waits of one client add up to at most max_total_wait (600 s by default);
    then the API is disabled for the run, and the cache still serves."""
    answers = []
    for _ in range(10):
        answers += [(429, b"", {"Retry-After": "290"})] * 3 + [(200, OK)]
    net = FakeNet(*answers)
    c, clk = make(tmp_path, net)
    assert c.max_total_wait == 600.0
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / "C77.json").write_bytes(OK)
    errors = []
    for i in range(1, 11):
        try:
            c.get(f"C{i}")
        except ee.EEError as e:
            errors.append(str(e))
    assert sum(clk.sleeps) <= 600.0 and clk.sleeps == [290.0, 290.0], clk.sleeps
    assert len(errors) == 10 and "580 s" in errors[0] and all("disabled" in e for e in errors), errors[:2]
    assert c.disabled and c.requests == 3 and c.waited == 580.0
    assert isinstance(c.get("C77"), EEFootprint)
    # a smaller budget: the network is down, the third wait would pass it
    c, clk = make(tmp_path / "b", FakeNet(*[(503, b"")] * 5), max_total_wait=20.0)
    with pytest.raises(ee.EEError, match="disabled"):
        c.get("C1")
    assert c.requests == 3 and clk.sleeps == [3.75, 7.5] and c.disabled
