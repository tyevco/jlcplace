"""The common offset: JLC lays the CPL over the Gerbers from the Gerbers' own origin, and the most common failure is a
CPL exported from another origin than the Gerbers, which moves every part by the same vector. pipeline.common_offset
finds that vector (and a y axis left pointing down, and numbers in another unit) and the origin the CPL was written
from; it must stay quiet on a correct CPL and on errors that are scattered."""
import dataclasses
import os
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from jlcplace import convention as cv
from jlcplace.model import BOTTOM, TOP, Board, Correction, CplRow, EEFootprint, EEPad, Pad, Part
from jlcplace.pipeline import Offset, Resolved, check, common_offset, offset_hints

ORIGIN = (100.0, 100.0)                     # the board's aux origin, as on dial-me-spinner
ORIGINS = {"aux": ORIGIN, "grid": (50.0, 150.0), "absolute": (0.0, 0.0)}


def two_pad(ref, x, y, angle=0.0, layer=TOP):
    pads = [Pad("1", -0.5, 0.0, 0.6, 0.6, net=f"{ref}a"), Pad("2", 0.5, 0.0, 0.6, 0.6, net=f"{ref}b")]
    return Part(ref, layer, x, y, angle, footprint="Resistor_SMD:R_0402_1005Metric", lcsc="C25744", pads=pads)


def sot23_6(ref, x, y, angle=0.0, layer=TOP):
    xy = [("1", -1.1375, -0.95), ("2", -1.1375, 0.0), ("3", -1.1375, 0.95),
          ("4", 1.1375, 0.95), ("5", 1.1375, 0.0), ("6", 1.1375, -0.95)]
    pads = [Pad(n, px, py, 1.325, 0.6, net=f"{ref}{n}") for n, px, py in xy]
    return Part(ref, layer, x, y, angle, footprint="Package_TO_SOT_SMD:SOT-23-6", lcsc="C7519", pads=pads)


def ee_for(part, corr):
    pads = []
    for p in part.copper_pads():
        v = cv.rot((p.x - corr.dx, p.y - corr.dy), -corr.rot)
        pads.append(EEPad(p.number, v[0], v[1], p.w, p.h))
    return EEFootprint(part.lcsc, "test", pads=pads)


def board(n=12, seed=5):
    """A board of resistors and SOT-23-6 parts on both sides, each with a correction whose EasyEDA origin is off its
    anchor (as connectors' are), so "where a row belongs" is the correction's row, not the part's anchor."""
    rnd = random.Random(seed)
    parts, resolved = [], {}
    for i in range(n):
        make = sot23_6 if i % 3 == 0 else two_pad
        p = make(f"{'U' if i % 3 == 0 else 'R'}{i + 1}", rnd.uniform(110, 170), rnd.uniform(50, 95),
                 rnd.choice([0.0, 90.0, 180.0, 270.0]), BOTTOM if i % 4 == 1 else TOP)
        corr = Correction(rot=180.0 if i % 3 == 0 else 0.0, dx=0.3 if i % 3 == 0 else 0.0, verdict="green")
        parts.append(p)
        resolved[p.ref] = Resolved(p, ee_for(p, corr), corr, "fit")
    return Board("t.kicad_pcb", ORIGIN, parts), resolved


def rows_from(b, resolved, origin=ORIGIN, scale=1.0, y_up=True):
    """The rows a writer makes from `origin`, its numbers divided by `scale` (written in another unit), y negated
    or not."""
    out = []
    for ref, r in resolved.items():
        x, y, a = cv.cpl_row_for(r.part, r.corr, origin)
        if not y_up:
            y = -y
        out.append(CplRow(ref, x / scale, y / scale, r.part.layer, a))
    return out


def run_of(rows, b=None, resolved=None):
    if b is None:
        b, resolved = board()
    return check(b, rows, resolved)


# ------------------------------------------------------------------------------------------------------- quiet

def test_a_correct_cpl_has_no_common_offset():
    b, resolved = board()
    run = check(b, rows_from(b, resolved), resolved)
    assert all(r.verdict == "green" for r in run.results)
    assert common_offset(run, origins=ORIGINS) is None and offset_hints(run, origins=ORIGINS) == []
    # nor do rows a tenth of a millimetre off (every centre still on its pad): not displaced
    rows = [dataclasses.replace(r, x=r.x + 0.1) for r in rows_from(b, resolved)]
    assert common_offset(check(b, rows, resolved), origins=ORIGINS) is None


def test_scattered_errors_have_no_common_offset():
    b, resolved = board(n=20)
    rnd = random.Random(2)
    rows = [dataclasses.replace(r, x=r.x + rnd.uniform(-5, 5), y=r.y + rnd.uniform(-5, 5))
            for r in rows_from(b, resolved)]
    assert common_offset(check(b, rows, resolved), origins=ORIGINS) is None
    # a few rows wrong by one vector (the old dial-me-spinner CPLs: 7 of 201) is not an origin
    rows = rows_from(b, resolved)
    for r in rows[:5]:
        r.x += 2.0
    assert common_offset(check(b, rows, resolved), origins=ORIGINS) is None


def test_too_few_rows_or_too_small_a_share():
    b, resolved = board(n=10)
    rows = rows_from(b, resolved)
    for r in rows[:5]:                     # half the rows moved alike: under 60 %
        r.x, r.y = r.x + 3.0, r.y - 1.0
    assert common_offset(check(b, rows, resolved), origins=ORIGINS) is None
    for r in rows[5:7]:                    # 7 of 10
        r.x, r.y = r.x + 3.0, r.y - 1.0
    off = common_offset(check(b, rows, resolved), origins=ORIGINS)
    assert (off.rows, off.total) == (7, 10) and off.dx == pytest.approx(3.0) and off.dy == pytest.approx(-1.0)
    b, resolved = board(n=2)
    rows = [dataclasses.replace(r, x=r.x + 3.0) for r in rows_from(b, resolved)]
    assert common_offset(check(b, rows, resolved), origins=ORIGINS) is None          # 2 rows: not enough


def test_rows_displaced_alike_beside_rows_in_place_are_no_origin_mismatch():
    """7 of 10 rows displaced alike while 3 sit exactly on their parts: an origin error moves every row, so this is
    not one, and following 'shift every row' or '--origin' would break the 3. Said as rows displaced alike."""
    b, resolved = board(n=10)
    rows = rows_from(b, resolved)
    for r in rows[:7]:
        r.x, r.y = r.x + 5.0, r.y + 5.0
    run = check(b, rows, resolved)
    off = common_offset(run, origins=ORIGINS)
    assert (off.rows, off.total, len(off.in_place), off.origin_error) == (7, 10, 3, False)
    hint = offset_hints(run, origins=ORIGINS)
    assert len(hint) == 1 and hint[0].startswith("jlcplace: 7 of 10 rows are displaced alike from where they belong, "
                                                 "by (+5.000, +5.000) mm ("), hint
    assert "while 3 sit on their parts (a CPL from another origin would move them all)" in hint[0]
    from jlcplace import report
    run.offset = off
    d = report.to_dict(run)["origin"]["offset"]               # what a --json consumer reads
    assert (d["origin_error"], sorted(d["in_place"]), d["rows"]) == (False, sorted(r.ref for r in rows[7:]), 7), d
    assert "--origin" not in hint[0] and "every row" not in hint[0] and "origin mismatch:" not in hint[0]


def test_an_origin_mismatch_says_which_rows_sit_on_their_parts_already():
    """Every row but one written from the page origin (the one fixed by hand): an origin mismatch, whose fix leaves
    out the row that is right."""
    b, resolved = board(n=12)
    rows = rows_from(b, resolved, (0.0, 0.0))
    rows[1] = rows_from(b, resolved)[1]
    run = check(b, rows, resolved)
    off = common_offset(run, origins=ORIGINS)
    assert (off.rows, off.in_place, off.origin_error, off.origin_kind) == (11, [rows[1].ref], True, "absolute")
    hint = offset_hints(run, origins=ORIGINS)[0]
    assert hint.startswith("jlcplace: origin mismatch: 11 of 12 rows would sit on their parts if shifted by") and \
        f"(1 row sits on its part already: {rows[1].ref})" in hint, hint


def test_the_origin_finding_counts_the_parts_jlc_would_put_off():
    """From the Gerbers' origin, a row a little off the common vector is outside its cluster but displaced all the
    same: JLC would put every part off. Only rows in place make it "N of the M parts"."""
    from jlcplace.pipeline import origin_findings
    b, resolved = board(n=12)
    b = dataclasses.replace(b, origin_kind="gerber")
    rows = rows_from(b, resolved, (0.0, 0.0))
    rows[2] = dataclasses.replace(rows[2], x=rows[2].x + 0.2)
    (f,) = origin_findings(check(b, rows, resolved), origins=ORIGINS)
    assert "(11 of 12 rows): JLC would put every part 141.42 mm off" in f.message, f.message
    rows[1] = rows_from(b, resolved)[1]                          # fixed by hand: in place
    (f,) = origin_findings(check(b, rows, resolved), origins=ORIGINS)
    assert "(10 of 12 rows): JLC would put 11 of the 12 parts 141.42 mm off" in f.message, f.message


# ------------------------------------------------------------------------------------------------------- origins

@pytest.mark.parametrize("kind", ["absolute", "grid"])
def test_a_cpl_from_another_known_origin(kind):
    """KiCad's position file without 'use drill/place file origin' is relative to the page origin: every row is
    displaced by the distance between the two origins, in the CPL's axes (y up)."""
    b, resolved = board()
    run = check(b, rows_from(b, resolved, ORIGINS[kind]), resolved)
    assert all(r.verdict == "red" for r in run.results)
    off = common_offset(run, origins=ORIGINS)
    ox, oy = ORIGINS[kind]
    assert isinstance(off, Offset) and (off.rows, off.total) == (12, 12) and (off.scale, off.y_up) == (1.0, True)
    assert (off.dx, off.dy) == pytest.approx((ORIGIN[0] - ox, oy - ORIGIN[1]))
    assert off.origin == pytest.approx((ox, oy)) and off.origin_kind == kind
    hint = offset_hints(run, origins=ORIGINS)
    assert len(hint) == 1 and hint[0].startswith(f"jlcplace: origin mismatch: 12 of 12 rows would sit on their parts "
                                                 f"if shifted by ({ox - ORIGIN[0]:.3f}, {ORIGIN[1] - oy:.3f}) mm")
    assert f"--origin {kind}" in hint[0] and "Gerbers" in hint[0], hint
    assert ("page origin (0, 0)" if kind == "absolute" else "grid origin (50, 150)") in hint[0]
    assert "not the board's aux origin (100, 100)" in hint[0]


def test_a_cpl_from_an_origin_the_board_does_not_name():
    b, resolved = board()
    run = check(b, rows_from(b, resolved, (37.5, 12.25)), resolved)
    off = common_offset(run, origins=ORIGINS)
    assert off.origin == pytest.approx((37.5, 12.25)) and off.origin_kind == ""
    hint = offset_hints(run, origins=ORIGINS)[0]
    assert "(37.500, 12.250) on the board, which is none of its origins" in hint and "--origin 37.5,12.25" in hint
    # an origin the caller knows of (the one the Gerbers were plotted from) is named, and given as numbers
    hint = offset_hints(run, origins={**ORIGINS, "gerber": (37.5, 12.25)})[0]
    assert "written from the Gerbers' origin (37.5, 12.25)" in hint and "--origin 37.5,12.25" in hint, hint


def test_the_default_origins_are_the_page_and_the_board_s_own():
    b, resolved = board()
    run = check(b, rows_from(b, resolved, (0.0, 0.0)), resolved)
    assert common_offset(run).origin_kind == "absolute"


# ------------------------------------------------------------------------------------------------------- y and units

def test_a_y_axis_left_pointing_down():
    """Rows written in sheet coordinates (y down) instead of JLC's y up: no single vector moves them onto their parts,
    but with Mid Y negated they sit on them (here from the aux origin, then from the page origin)."""
    b, resolved = board()
    for origin, kind in ((ORIGIN, "aux"), ((0.0, 0.0), "absolute")):
        run = check(b, rows_from(b, resolved, origin, y_up=False), resolved)
        off = common_offset(run, origins=ORIGINS)
        assert off.y_up is False and off.scale == 1.0 and off.rows == 12, off
        assert off.origin == pytest.approx(origin) and off.origin_kind == kind
        hint = offset_hints(run, origins=ORIGINS)[0]
        assert "12 of 12 rows would sit on their parts with Mid Y negated" in hint and "grows upward" in hint, hint
        assert ("--origin absolute" in hint) == (kind == "absolute"), hint


@pytest.mark.parametrize("unit, read_as", [("in", "mm"), ("mil", "mm"), ("mm", "in")])
def test_numbers_in_another_unit(unit, read_as):
    """KiCad's CSV position file says no unit: one exported in inches, read as mm, puts every part near the origin
    (and a mm file read with --units in puts them 25 times too far)."""
    b, resolved = board()
    units = {"mm": 1.0, "in": 25.4, "mil": 0.0254}
    rows = rows_from(b, resolved, scale=units[unit] / units[read_as])
    run = check(b, rows, resolved)
    off = common_offset(run, units=read_as, origins=ORIGINS)
    assert off.scale == pytest.approx(units[unit] / units[read_as]) and off.unit == unit and off.y_up is True
    assert (off.dx, off.dy) == pytest.approx((0.0, 0.0), abs=1e-3) and off.origin_kind == "aux"
    hint = offset_hints(run, units=read_as, origins=ORIGINS)[0]
    words = {"in": "inches", "mil": "mils", "mm": "millimetres"}[unit]
    fix = (f"they are in mm, as JLC reads them: check without --units {read_as}" if unit == "mm" else
           "JLC reads a CPL's numbers in mm: write it again in mm (KiCad: --units mm; --units only tells jlcplace how "
           "to read a position file)")
    assert f"12 of 12 rows would sit on their parts if the CPL's numbers were in {words}: {fix}" in hint, hint
    assert "pass --units" not in hint
    # inches from the page origin: both said, in one line, the origin as the board names it
    run = check(b, rows_from(b, resolved, (0.0, 0.0), scale=25.4), resolved)
    hint = offset_hints(run, origins=ORIGINS)
    assert len(hint) == 1 and "write it again in mm" in hint[0] and "--origin absolute" in hint[0], hint
    assert "relative to the page origin (0, 0)," in hint[0], hint


# ------------------------------------------------------------------------------------------------------- fix

@pytest.mark.parametrize("origin, scale, y_up", [((0.0, 0.0), 1.0, True), ((37.5, 12.25), 1.0, False),
                                                 ((0.0, 0.0), 25.4, True)])
def test_fix_puts_the_rows_back(origin, scale, y_up):
    b, resolved = board()
    good = {r.ref: r for r in rows_from(b, resolved)}
    rows = rows_from(b, resolved, origin, scale, y_up)
    off = common_offset(check(b, rows, resolved), origins=ORIGINS)
    for r in rows:
        f = off.fix(r)
        assert (f.ref, f.layer, f.rotation) == (r.ref, r.layer, r.rotation)
        assert (f.x, f.y) == pytest.approx((good[r.ref].x, good[r.ref].y), abs=1e-3)
    fixed = check(b, [off.fix(r) for r in rows], resolved)
    assert all(r.verdict == "green" for r in fixed.results) and common_offset(fixed, origins=ORIGINS) is None


def test_rows_without_a_correction_belong_at_their_part():
    """With no correction known (no EasyEDA record, no database), a row belongs at its part's anchor."""
    b, resolved = board()
    bare = {ref: dataclasses.replace(r, corr=None, ee=None, source="") for ref, r in resolved.items()}
    rows = [CplRow(p.ref, *cv.board_to_cpl((p.x, p.y), (0.0, 0.0)), p.layer, p.angle) for p in b.parts]
    off = common_offset(check(b, rows, bare), origins=ORIGINS)
    assert off.origin_kind == "absolute" and off.rows == 12


# ------------------------------------------------------------------------------------------------------- real boards

DIAL = Path(os.environ.get("JLCPLACE_DIAL_ME_SPINNER", "/home/user/dial-me-spinner")) / "hardware"
SCRATCH = Path(os.environ.get("JLCPLACE_TEST_EASYEDA", "/tmp/claude-0/-home-user-dial-me-spinner/"
                              "3708159a-bc3b-5167-8507-f5b08e1ce6c9/scratchpad/jlc"))


def pcb(b):
    return DIAL / f"kicad/{b}/dial-me-spinner-{b}.kicad_pcb"


@pytest.mark.cache
@pytest.mark.skipif(shutil.which("kicad-cli") is None, reason="needs kicad-cli")
@pytest.mark.skipif(not (pcb("main").is_file() and (SCRATCH / "ee_raw").is_dir()),
                    reason=f"needs the dial-me-spinner boards under {DIAL} and cached EasyEDA records")
def test_dial_me_spinner_cpls(tmp_path):
    """The released CPLs and the old ones (7 rows wrong) have no common offset; KiCad's position file exported from
    the page origin (aux origin 100, 100) and in inches does, and says which."""
    from jlcplace.board import load_board
    from jlcplace.cpl import read_cpl
    from jlcplace.easyeda import EEClient
    from jlcplace.pipeline import resolve
    cache = tmp_path / "cache"
    shutil.copytree(SCRATCH / "ee_raw", cache, ignore=shutil.ignore_patterns("__pycache__"))
    for b in ("main", "base"):
        board = load_board(pcb(b), reader="sexpr")
        resolved = resolve(board, EEClient(cache, offline=True))
        for cpl in (DIAL / f"fab/{b}/dial-me-spinner-{b}-CPL-JLCPCB.csv",
                    SCRATCH / f"fab-before/{b}/dial-me-spinner-{b}-CPL-JLCPCB.csv"):
            if cpl.is_file():
                assert common_offset(check(board, read_cpl(cpl), resolved)) is None, cpl
        for args, units, want in (([], "mm", ("absolute", 1.0)), (["--use-drill-file-origin", "--units", "in"],
                                                                  "mm", ("aux", 25.4)),
                                  (["--use-drill-file-origin"], "mm", None)):
            pos = tmp_path / f"{b}.csv"
            subprocess.run(["kicad-cli", "pcb", "export", "pos", "--format", "csv", "--side", "both",
                            "--exclude-dnp", *(args if "--units" in args else args + ["--units", "mm"]), "-o",
                            str(pos), str(pcb(b))], check=True, capture_output=True)
            run = check(board, read_cpl(pos, units=units), resolved)
            off = common_offset(run, units=units)
            if want is None:
                assert off is None, (b, args, off)
                continue
            assert off.origin_kind == want[0] and off.scale == pytest.approx(want[1]), (b, args, off)
            assert off.rows >= 0.6 * off.total and off.total == len(run.rows), (b, args, off)
