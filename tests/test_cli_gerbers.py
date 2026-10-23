"""The command line with the Gerbers (--gerbers), the overlay images (--overlay) and the run-level findings, on the
synthetic board of test_cli: JLC lays the CPL over the Gerbers from the Gerbers' own (0, 0), so with --gerbers that
point is the CPL origin; a CPL written from another origin, an --origin naming another, and Gerbers that are not this
board's as it is are red for the whole run, said first, with what JLC will do and the fix. Without --gerbers the
summary and the checklist remind once that the Gerbers must share the CPL's origin. Pinout rows get a checklist item
of their own. No KiCad, no EasyEDA data, no network."""
import csv
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from jlcplace import report
from jlcplace.check import check_row
from jlcplace.cpl import read_cpl, write_cpl
from jlcplace.fit import fit_part
from jlcplace.gerber import board_pads
from jlcplace.model import BOTTOM, RED, TOP, Board, CplRow, EEFootprint, EEPad, Pad, Part
from jlcplace.pipeline import Resolved, check

from test_cli import ORIGIN, coax, env, offline, record, run, two  # noqa: F401  (env is a fixture)

PAGE = (0.0, 0.0)


def plot(board: Board, origin, directory: Path, *, move=None) -> Path:
    """A KiCad-like Gerber set of the board plotted from `origin` (g = (bx - ox, oy - by)): copper with TO.P
    attributes and paste, both sides; `move` = {ref: (dx, dy)} moves a part's pads first (an older board)."""
    directory.mkdir(parents=True)
    for side, cu, paste in ((TOP, "Copper,L1,Top", "Paste,Top"), (BOTTOM, "Copper,L2,Bot", "Paste,Bot")):
        for function, name in ((cu, "Cu"), (paste, "Paste")):
            out = [f"%TF.FileFunction,{function}*%", "%FSLAX46Y46*%", "%MOMM*%", "%ADD10C,0.400000*%", "D10*"]
            for bp in board_pads(board):
                if bp.side not in (side, "both"):
                    continue
                dx, dy = (move or {}).get(bp.ref, (0.0, 0.0))
                gx, gy = bp.x + dx - origin[0], origin[1] - bp.y - dy
                out += ([f"%TO.P,{bp.ref},{bp.number}*%"] if name == "Cu" else []) + \
                    [f"X{round(gx * 1e6)}Y{round(gy * 1e6)}D03*"] + (["%TD*%"] if name == "Cu" else [])
            out.append("M02*")
            (directory / f"board-{side}_{name}.gbr").write_text("\n".join(out) + "\n", encoding="utf-8")
    return directory


def shifted(src, dst, dx, dy) -> str:
    """The CPL at src with every row moved by (dx, dy) in the cpl frame: written from another origin."""
    write_cpl(dst, [CplRow(r.ref, r.x + dx, r.y + dy, r.layer, r.rotation) for r in read_cpl(src)])
    return str(dst)


@pytest.fixture
def made(env, capsys):  # noqa: F811
    """The board, jlcplace's CPL from its aux origin (100, 100), and Gerber sets plotted from the aux origin and
    from the page origin."""
    board = env.make()
    assert run(capsys, "cpl", env.board, "-o", env.out, *offline(env))[0] == 0
    env.aux = plot(board, ORIGIN, env.tmp / "g-aux")
    env.page = plot(board, PAGE, env.tmp / "g-page")
    env.board_obj = board
    return env


def lines(out: str) -> list[str]:
    return out.splitlines()


# ------------------------------------------------------------------------------------------------------- --gerbers

def test_gerbers_from_the_cpls_origin_are_clean_and_named_in_the_header(made, capsys):
    e = made
    t = e.tmp
    code, out, err = run(capsys, "check", e.board, e.out, "--gerbers", e.aux, *offline(e), "--json", t / "r.json",
                         "--junit", t / "r.xml", "--checklist", t / "c.md")
    assert code == 0, out + err
    head = lines(out)[0]
    assert "origin 100, 100 from the Gerbers (aux)" in head and "over the Gerbers g-aux" in head, head
    assert "4 results: 0 red, 0 yellow, 4 green, 0 verified" in out and "Gerbers not checked" not in out
    d = json.loads((t / "r.json").read_text(encoding="utf-8"))
    o = d["origin"]
    assert (o["x"], o["y"], o["kind"], o["from_gerbers"], o["verdict"]) == (100.0, 100.0, "gerber", True, "green")
    g = o["gerbers"]
    assert (g["named"], g["method"], g["flip"], g["scale"], g["coverage"], g["stale"]) == \
        ("aux", "x2-pins", "none", 1.0, 1.0, False)
    assert d["findings"] == [] and d["summary"]["findings"] == 0 and o["offset"] is None
    cases = {tc.get("name"): tc for tc in ET.fromstring((t / "r.xml").read_text(encoding="utf-8")).iter("testcase")}
    assert "origin" in cases and cases["origin"].find("failure") is None
    assert "**Origin:** the Gerbers' (0, 0) is this CPL's origin" in (t / "c.md").read_text(encoding="utf-8")


def test_gerbers_that_run_out_of_memory_are_bad_input(made, capsys, monkeypatch):
    """What the expansion budget does not foresee and exhausts the memory is bad input (exit 3) naming --gerbers, not
    an internal error."""
    import jlcplace.cli

    def exhausted(path):
        raise MemoryError()
    monkeypatch.setattr(jlcplace.cli, "read_gerbers", exhausted)
    code, out, err = run(capsys, "check", made.board, made.out, "--gerbers", made.aux, *offline(made))
    assert code == 3 and f"--gerbers {made.aux}: the files expand past this machine's memory" in err, err


def test_a_cpl_from_another_origin_than_the_gerbers_is_red_first_with_the_shift_and_the_fix(made, capsys):
    """The most common failure: Gerbers plotted from the page origin, the CPL from the drill/place origin. Every row
    is red, from the Gerbers' origin, as JLC reads them; the first line says why, by how much and what to do."""
    e = made
    t = e.tmp
    code, out, _ = run(capsys, "check", e.board, e.out, "--gerbers", e.page, *offline(e), "--json", t / "r.json",
                       "--junit", t / "r.xml", "--github-annotations", "--checklist", t / "c.csv", "--report",
                       t / "r.html")
    assert code == 2
    first = lines(out)[1]
    assert first.startswith("RED     origin mismatch: JLC lays the CPL over the Gerbers from their (0, 0), board "
                            "(0, 0) (the page origin), but the CPL was written from the aux (drill/place file) origin "
                            "(100, 100) (4 of 4 rows): JLC would put every part 141.42 mm off, displaced by "
                            "(-100.000, +100.000) mm; fix: "), first
    assert "plot the Gerbers and drill files from the aux (drill/place file) origin (KiCad: 'Use drill/place file " \
           "origin' ticked)" in first
    assert "shift every row by (+100.000, -100.000) mm" in first and "jlcplace cpl BOARD --gerbers" in first
    assert "origin 0, 0 from the Gerbers (absolute)" in lines(out)[0]
    assert "4 results: 4 red" in out and "; origin red" in out
    assert sum("would sit on their parts" in ln for ln in lines(out)) == 0          # said once, by the finding
    d = json.loads((t / "r.json").read_text(encoding="utf-8"))
    assert [(f["status"], f["verdict"]) for f in d["findings"]] == [("origin", RED)]
    assert d["summary"]["worst"] == RED and d["origin"]["verdict"] == RED
    off = d["origin"]["offset"]
    assert (off["dx"], off["dy"], off["origin"], off["origin_kind"]) == (-100.0, 100.0, [100.0, 100.0], "aux")
    suite = ET.fromstring((t / "r.xml").read_text(encoding="utf-8"))
    origin = next(tc for tc in suite.iter("testcase") if tc.get("name") == "origin")
    assert origin.find("failure").get("message").startswith("origin mismatch: JLC lays the CPL")
    ann = [ln for ln in lines(out) if ln.startswith("::")]
    assert ann[0].startswith("::error file=") and ",title=jlcplace origin::origin mismatch: " in ann[0], ann[0]
    rows = list(csv.DictReader((t / "c.csv").open(encoding="utf-8")))
    assert (rows[-1]["category"], rows[-1]["row verdict"]) == ("origin", RED)
    page = (t / "r.html").read_text(encoding="utf-8")
    assert '<p class="finding">' in page and page.index('class="finding"') < page.index("<h2>Board</h2>")
    # -q: the finding is still said
    code, out, _ = run(capsys, "check", e.board, e.out, "--gerbers", e.page, *offline(e), "-q")
    assert code == 2 and out.startswith("jlcplace: red: origin mismatch: JLC lays the CPL"), out


def test_a_small_common_offset_is_an_origin_mismatch_when_the_gerbers_fix_the_origin(made, capsys):
    """The Gerbers' origin is known to a micron: every row 0.2 mm off it is the CPL's origin 0.2 mm off, said as
    such (the rows alone would look like part problems). Without the Gerbers, or below 0.05 mm, nothing is said."""
    e = made
    small = shifted(e.out, e.tmp / "small.csv", 0.2, 0.0)
    code, out, _ = run(capsys, "check", e.board, small, "--gerbers", e.aux, *offline(e))
    first = lines(out)[1]
    assert code == 2 and first.startswith("RED     origin mismatch: JLC lays the CPL over the Gerbers from their "
                                          "(0, 0), board (100, 100) (the aux (drill/place file) origin), but the CPL "
                                          "was written from board (99.800, 100.000), none of the board's origins"), out
    assert "JLC would put every part 0.20 mm off" in first and "shift every row by (-0.200, +0.000) mm" in first
    code, out, _ = run(capsys, "check", e.board, small, *offline(e))
    assert "origin mismatch" not in out
    tiny = shifted(e.out, e.tmp / "tiny.csv", 0.03, 0.0)
    code, out, _ = run(capsys, "check", e.board, tiny, "--gerbers", e.aux, *offline(e))
    assert code == 0 and "origin mismatch" not in out, out


def test_rows_displaced_alike_beside_a_row_in_place_are_no_origin_finding(made, capsys):
    """3 of 4 rows 5 mm off and one where it belongs: not an origin error (it would move every row), so no 'every
    part' and no 'shift every row', which would break the row that is right."""
    e = made
    rows = read_cpl(e.out)
    some = e.tmp / "some.csv"
    write_cpl(some, [CplRow(r.ref, r.x + 5.0, r.y + 5.0, r.layer, r.rotation) if k else r for k, r in enumerate(rows)])
    code, out, _ = run(capsys, "check", e.board, some, "--gerbers", e.aux, *offline(e))
    assert code == 2 and "origin mismatch" not in out and "every row" not in out and "every part" not in out, out
    assert "jlcplace: 3 of 4 rows are displaced alike from where they belong, by (+5.000, +5.000) mm" in out, out


def test_an_mm_file_read_with_units_in_is_not_said_to_be_in_inches(made, capsys):
    """A position file in mm checked with a wrong --units in: the rows sit on their parts read in mm, so the red
    finding (what annotations and JUnit carry) says --units misread an mm file, not that the file is in inches."""
    e = made
    mm = e.tmp / "mm.csv"
    mm.write_text("Designator,Mid X,Mid Y,Layer,Rotation\n" + "".join(
        f"{r.ref},{r.x:.4f},{r.y:.4f},{r.layer},{r.rotation:g}\n" for r in read_cpl(e.out)), encoding="utf-8")
    for unit in ("in", "mil"):
        code, out, err = run(capsys, "check", e.board, mm, "--units", unit, *offline(e), "-q")
        assert code == 2 and "are in inches" not in out + err and "are in mils" not in out + err, out + err
        assert f"jlcplace: red: the CPL's numbers are in mm already (4 of 4 rows sit on their parts read so), as JLC " \
               f"reads them: --units {unit} misread them; check without it" in out + err, out + err


def test_a_cpl_in_inches_is_red_however_it_is_read(made, capsys):
    """--units in makes jlcplace read the CPL's numbers as inches, not JLC: a CPL whose bare numbers are inches is
    red, and the fix is to write it in mm. Inches from another origin than the Gerbers': both fixes, joined."""
    e = made
    rows = read_cpl(e.out)
    inch = e.tmp / "inch.csv"
    inch.write_text("Designator,Mid X,Mid Y,Layer,Rotation\n" + "".join(
        f"{r.ref},{r.x / 25.4:.6f},{r.y / 25.4:.6f},{r.layer},{r.rotation:g}\n" for r in rows), encoding="utf-8")
    code, out, _ = run(capsys, "check", e.board, inch, "--gerbers", e.aux, "--units", "in", *offline(e))
    assert code == 2 and lines(out)[1].startswith("RED     the CPL's numbers are in inches (read so because of "
                                                  "--units in), but JLC reads a CPL's numbers in mm: write it again "
                                                  "in mm"), out
    code, out, _ = run(capsys, "check", e.board, inch, "--gerbers", e.aux, *offline(e))
    assert code == 2 and "pass --units" not in out and "write it again in mm" in out, out
    page = e.tmp / "inch-page.csv"
    page.write_text("Designator,Mid X,Mid Y,Layer,Rotation\n" + "".join(
        f"{r.ref},{(r.x + 100) / 25.4:.6f},{(r.y - 100) / 25.4:.6f},{r.layer},{r.rotation:g}\n" for r in rows),
        encoding="utf-8")
    code, out, _ = run(capsys, "check", e.board, page, "--gerbers", e.aux, *offline(e))
    first = lines(out)[1]
    assert first.startswith("RED     origin mismatch: JLC lays the CPL over the Gerbers from their (0, 0), board "
                            "(100, 100) (the aux (drill/place file) origin), but the CPL was written from the page "
                            "origin (0, 0) in inches (4 of 4 rows)"), first
    assert "fix: write the CPL's numbers in mm (KiCad: --units mm) and either plot the Gerbers" in first, first


def test_fixed_cpl_writes_the_cpl_with_the_common_offset_taken_out(made, capsys):
    """'shift every row by ...' is a fix the tool carries out: --fixed-cpl writes the CPL's own rows (their angles
    and columns kept) moved onto the Gerbers' origin, or in mm, and checks what it wrote; the run's exit code stays
    the given CPL's. With nothing to take out, nothing is written."""
    e = made
    fixed = e.tmp / "fixed.csv"
    code, out, _ = run(capsys, "check", e.board, e.out, "--gerbers", e.page, *offline(e), "--fixed-cpl", fixed)
    assert code == 2 and fixed.is_file(), out
    assert "jlcplace: wrote " + str(fixed) + " (4 rows moved by (+100.000, -100.000) mm): its check: 0 red" in out, out
    given = {r.ref: r for r in read_cpl(e.out)}
    for r in read_cpl(fixed):
        assert (r.x, r.y, r.rotation) == pytest.approx((given[r.ref].x + 100, given[r.ref].y - 100,
                                                        given[r.ref].rotation)), r
    assert run(capsys, "check", e.board, fixed, "--gerbers", e.page, *offline(e))[0] == 0
    assert "(`check --fixed-cpl FILE` writes it)" in lines(out)[1]     # the finding says how
    inch = e.tmp / "inch.csv"
    inch.write_text("Designator,Mid X,Mid Y,Layer,Rotation\n" + "".join(
        f"{r.ref},{r.x / 25.4:.6f},{r.y / 25.4:.6f},{r.layer},{r.rotation:g}\n" for r in given.values()),
        encoding="utf-8")
    code, out, _ = run(capsys, "check", e.board, inch, "--gerbers", e.aux, *offline(e), "--fixed-cpl", fixed)
    assert "(4 rows in mm)" in out and run(capsys, "check", e.board, fixed, "--gerbers", e.aux, *offline(e))[0] == 0
    fixed.unlink()
    code, out, _ = run(capsys, "check", e.board, e.out, "--gerbers", e.aux, *offline(e), "--fixed-cpl", fixed)
    assert code == 0 and not fixed.exists() and "--fixed-cpl: no common offset to take out" in out, out


def test_fixed_cpl_without_the_gerbers_says_which_origin_it_assumed(made, capsys):
    """Without --gerbers nothing says which origin JLC reads the rows from: a CPL and Gerbers both from the page
    origin are a right order, which --fixed-cpl would move onto the aux origin. The file is written, as asked, but
    the note says it is right only for Gerbers plotted from that origin, unchecked; with the Gerbers it is either not
    needed (the page set) or right (the aux set). A unit fix from the CPL's own origin needs no Gerbers."""
    e = made
    page = shifted(e.out, e.tmp / "page.csv", ORIGIN[0], -ORIGIN[1])
    fixed = e.tmp / "fixed.csv"
    code, out, _ = run(capsys, "check", e.board, page, *offline(e), "-q", "--fixed-cpl", fixed)
    assert code == 2 and fixed.is_file(), out
    assert out.rstrip().endswith("right only if the Gerbers and drill files are plotted from the board's aux origin "
                                 "(100, 100), which was not checked: pass --gerbers"), out
    fixed.unlink()
    code, out, _ = run(capsys, "check", e.board, page, "--gerbers", e.page, *offline(e), "-q", "--fixed-cpl", fixed)
    assert code == 0 and not fixed.exists() and "no common offset to take out" in out, out
    code, out, _ = run(capsys, "check", e.board, page, "--gerbers", e.aux, *offline(e), "-q", "--fixed-cpl", fixed)
    assert code == 2 and fixed.is_file() and "its check: 0 red" in out and "not checked" not in out, out
    inch = e.tmp / "inch.csv"
    inch.write_text("Designator,Mid X,Mid Y,Layer,Rotation\n" + "".join(
        f"{r.ref},{r.x / 25.4:.6f},{r.y / 25.4:.6f},{r.layer},{r.rotation:g}\n" for r in read_cpl(e.out)),
        encoding="utf-8")
    code, out, _ = run(capsys, "check", e.board, inch, *offline(e), "-q", "--fixed-cpl", fixed)
    assert "(4 rows in mm)" in out and "not checked" not in out, out


def test_a_fixed_cpl_that_cannot_be_written_leaves_the_other_outputs_written(made, capsys):
    """Exit 3 when one output cannot be written, but the others still are: a --fixed-cpl path under a file is said
    plainly, after the summary, the JSON and the JUnit a CI job reads."""
    e = made
    (e.tmp / "afile").write_text("", encoding="utf-8")
    code, out, err = run(capsys, "check", e.board, e.out, "--gerbers", e.page, *offline(e), "--fixed-cpl",
                         e.tmp / "afile" / "f.csv", "--json", e.tmp / "r.json", "--junit", e.tmp / "r.xml")
    assert code == 3 and (e.tmp / "r.json").is_file() and (e.tmp / "r.xml").is_file(), out + err
    assert "4 results: 4 red" in out and f"--fixed-cpl {e.tmp / 'afile' / 'f.csv'}: cannot write: a directory on " \
        "its path is a file (not a directory)" in err, err


def test_the_json_offset_says_whether_it_is_an_origin_error_and_which_rows_sit_in_place(made, capsys):
    """A consumer of --json tells an origin error (every row moved: --fixed-cpl applies) from rows displaced alike
    beside rows in place (test_offset), and sees which rows the fix keeps."""
    e = made
    page = shifted(e.out, e.tmp / "page.csv", ORIGIN[0], -ORIGIN[1])
    run(capsys, "check", e.board, page, *offline(e), "-q", "--json", e.tmp / "r.json")
    off = json.loads((e.tmp / "r.json").read_text(encoding="utf-8"))["origin"]["offset"]
    assert (off["in_place"], off["origin_error"], off["rows"], off["total"]) == ([], True, 4, 4), off


def test_kicads_page_origin_cpl_over_page_origin_gerbers_is_consistent(made, capsys):
    """KiCad's position file and Gerbers both from the page origin: one origin, nothing to say about it."""
    e = made
    page = shifted(e.out, e.tmp / "page.csv", ORIGIN[0], -ORIGIN[1])
    code, out, _ = run(capsys, "check", e.board, page, "--gerbers", e.page, *offline(e))
    assert code == 0 and "origin mismatch" not in out and "origin 0, 0 from the Gerbers (absolute)" in out, out
    # without the Gerbers the same CPL is red, with the old hint: which origin is JLC's is not known
    code, out, _ = run(capsys, "check", e.board, page, *offline(e))
    assert code == 2 and "origin mismatch: 4 of 4 rows would sit on their parts" in out and "--origin absolute" in out


def test_an_origin_option_that_is_not_the_gerbers_is_red(made, capsys):
    e = made
    code, out, _ = run(capsys, "check", e.board, e.out, "--gerbers", e.aux, "--origin", "absolute", *offline(e))
    assert code == 2, out
    first = lines(out)[1]
    # a snapshot keeps only the origin it is read with: (100, 100) is not known as its aux origin here
    assert first.startswith("RED     origin mismatch: --origin puts the CPL's origin at board (0, 0) (the page "
                            "origin), but JLC lays the CPL over the Gerbers from their (0, 0), board (100, 100) "), out
    assert first.endswith(": a CPL written from --origin's point would put every part 141.42 mm off, displaced by "
                          "(+100.000, -100.000) mm; the rows are checked from the Gerbers' origin, as JLC reads them: "
                          "drop --origin, or plot the Gerbers from that origin too"), first
    assert "4 results: 0 red, 0 yellow, 4 green" in out                       # as JLC reads them: on their parts
    code, out, _ = run(capsys, "check", e.board, e.out, "--gerbers", e.aux, "--origin", "aux", *offline(e))
    assert code == 0 and "origin mismatch" not in out


def test_gerbers_that_are_not_this_boards_as_it_is_are_red(made, capsys):
    e = made
    old = plot(e.board_obj, ORIGIN, e.tmp / "g-old", move={"R1": (1.0, 0.0)})
    code, out, _ = run(capsys, "check", e.board, e.out, "--gerbers", old, *offline(e))
    assert code == 2 and lines(out)[1].startswith("RED     the Gerbers are older than the board, re-plot them: 2 of "
                                                  "16 board pads have no Gerber pad at their place (R1:1, R1:2); 2 "
                                                  "Gerber paste pads lie on no board pad"), out
    assert "; gerbers red" in out
    other = Board("other.kicad_pcb", ORIGIN, [Part(f"R{i}", TOP, 10.0 + 7 * i, 20.0 + 3 * (i % 5), 0.0, pads=[
        Pad("1", -0.5, 0, 0.6, 0.6), Pad("2", 0.5, 0, 0.6, 0.6)]) for i in range(12)])
    code, out, _ = run(capsys, "check", e.board, e.out, "--gerbers", plot(other, ORIGIN, e.tmp / "g-other"),
                       *offline(e), "--json", e.tmp / "r.json")
    assert code == 2 and lines(out)[1].startswith("RED     the Gerbers' pads do not match the board's"), out
    assert "origin aux 100, 100" in lines(out)[0]                    # no origin to take from them: --origin's
    d = json.loads((e.tmp / "r.json").read_text(encoding="utf-8"))
    assert d["findings"][0]["status"] == "gerbers" and d["origin"]["from_gerbers"] is False


def profile(directory: Path, origin, rect) -> None:
    """A profile layer (KiCad's Edge_Cuts) holding the board-frame rectangle `rect` (x0, y0, x1, y1), plotted from
    `origin`."""
    x0, y0, x1, y1 = rect
    out = ["%TF.FileFunction,Profile,NP*%", "%FSLAX46Y46*%", "%MOMM*%", "%ADD10C,0.100000*%", "D10*"]
    for i, (bx, by) in enumerate(((x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0))):
        out.append(f"X{round((bx - origin[0]) * 1e6)}Y{round((origin[1] - by) * 1e6)}D0{2 if i == 0 else 1}*")
    (directory / "board-Edge_Cuts.gm1").write_text("\n".join(out + ["M02*"]) + "\n", encoding="utf-8")


def test_rows_are_not_judged_against_an_outline_the_gerbers_check_rejects(env, capsys):  # noqa: F811
    """The Gerbers' outline plotted from the page origin while the copper is from the aux origin (or another board's
    outline): the `gerbers` finding says so, and every correct row was yellow too, 'outside the board outline (the
    Gerbers' outline)'. Such rows are judged against the board's Edge.Cuts; an outline the check accepts is used."""
    from jlcplace.board import load_board
    from test_cli import OUTLINED, R0402
    path = env.tmp / "outlined.kicad_pcb"
    path.write_text(OUTLINED.format(parts="\n".join(R0402.format(ref=r, x=x, y=y) for r, x, y in (
        ("R1", 110, 90), ("R2", 130, 90), ("R4", 105, 95), ("R5", 105, 85)))), encoding="utf-8")
    assert run(capsys, "cpl", path, "-o", env.out, "--reader", "sexpr", *offline(env))[0] == 0
    board = load_board(path, reader="sexpr")
    for name, origin, rect, finding in (
            ("g-moved", PAGE, (100, 80, 120, 100), "was plotted from another origin than the copper"),
            ("g-other", ORIGIN, (100, 80, 130, 95), "is not the board's Edge.Cuts"),
            ("g-good", ORIGIN, (100, 80, 120, 100), None)):
        gerbers = plot(board, ORIGIN, env.tmp / name)
        profile(gerbers, origin, rect)
        code, out, _ = run(capsys, "check", path, env.out, "--reader", "sexpr", "--gerbers", gerbers, *offline(env))
        flagged = [ln for ln in lines(out) if ln.startswith(("RED", "YELLOW"))]
        what = "Edge.Cuts" if finding else "the Gerbers' outline"
        assert flagged[-1] == ("YELLOW  R2  C1002      top     ok: the row point is outside the board outline "
                               f"({what}), 10.00 mm beyond its edge: look at it in JLC's preview"), (name, out)
        assert "4 results: 0 red, 1 yellow, 3 green" in out, (name, out)
        assert (finding is None) == (code == 1 or code == 0) and (finding is None or finding in flagged[0]), out


def test_unreadable_gerbers_are_bad_input(made, capsys):
    e = made
    code, _, err = run(capsys, "check", e.board, e.out, "--gerbers", e.tmp / "nowhere", *offline(e))
    assert code == 3 and "--gerbers" in err and "no such file" in err, err
    bad = e.tmp / "bad.gbr"
    bad.write_text("%FSLAX46Y46*%%MOMM*%D11*X0Y0D03*", encoding="utf-8")
    code, _, err = run(capsys, "check", e.board, e.out, "--gerbers", bad, *offline(e))
    assert code == 3 and "bad.gbr: block" in err, err
    zipped = e.tmp / "g.zip"
    zipped.write_text("not a zip", encoding="utf-8")
    assert run(capsys, "check", e.board, e.out, "--gerbers", zipped, *offline(e))[0] == 3


def test_a_side_without_its_copper_layer_is_not_called_older_than_the_board(made, capsys):
    """field: a set without its top copper layer was also 'older than the board, re-plot them', for the top pads
    with no paste (test points, fiducials), which its paste layer alone cannot show; and the count line said
    'gerbers red' once per finding."""
    e = made
    (e.aux / "board-top_Cu.gbr").unlink()
    paste = e.aux / "board-top_Paste.gbr"
    text = paste.read_text(encoding="utf-8").splitlines()
    first = next(i for i, ln in enumerate(text) if ln.endswith("D03*"))
    paste.write_text("\n".join(text[:first] + text[first + 1:]) + "\n", encoding="utf-8")      # a pad with no paste
    code, out, _ = run(capsys, "check", e.board, e.out, "--gerbers", e.aux, *offline(e))
    assert code == 2 and lines(out)[1].startswith("RED     the Gerbers have no top copper layer: "), out
    assert "older than the board" not in out and out.count("; gerbers red") == 1, out
    # a part moved since the plot is still said; the count line names the kind of finding once
    old = plot(e.board_obj, ORIGIN, e.tmp / "g-old", move={"R2": (1.0, 0.0)})
    (old / "board-top_Cu.gbr").unlink()
    code, out, _ = run(capsys, "check", e.board, e.out, "--gerbers", old, *offline(e))
    assert "older than the board" in lines(out)[1] and "no top copper layer" in lines(out)[2], out
    assert next(ln for ln in lines(out) if " results: " in ln).endswith(" verified; gerbers red"), out


def test_a_side_missing_from_the_gerbers_is_said(made, capsys):
    """A set without the bottom copper and paste: the bottom was never checked, and JLC could not make it: red. A
    set without the bottom paste: the bottom parts would get no paste, yellow while the CPL places parts there."""
    e = made
    for f in e.aux.glob("board-bottom_*.gbr"):
        f.unlink()
    code, out, _ = run(capsys, "check", e.board, e.out, "--gerbers", e.aux, *offline(e))
    assert code == 2 and lines(out)[1].startswith("RED     the Gerbers have no bottom copper or paste layer: "
                                                  "the board's "), out
    assert "(read: board-top_Cu.gbr, board-top_Paste.gbr)" in lines(out)[1] and "; gerbers red" in out
    e.aux = plot(e.board_obj, ORIGIN, e.tmp / "g-aux2")
    (e.aux / "board-bottom_Paste.gbr").unlink()
    code, out, _ = run(capsys, "check", e.board, e.out, "--gerbers", e.aux, *offline(e))
    assert code == 0 and lines(out)[1].startswith("YELLOW  the Gerbers have no bottom paste layer: the "
                                                  "board's "), out
    assert run(capsys, "check", e.board, e.out, "--gerbers", e.aux, *offline(e), "--fail-on", "yellow")[0] == 1
    top = e.tmp / "top.csv"
    write_cpl(top, [r for r in read_cpl(e.out) if r.layer == TOP])
    code, out, _ = run(capsys, "check", e.board, top, "--gerbers", e.aux, *offline(e))
    assert "no bottom paste layer" not in out, out                       # nothing is placed on the bottom


def test_a_zip_that_cannot_be_read_is_bad_input(made, capsys):
    import zipfile
    from test_gerber import _patched_zip
    e = made
    z = e.tmp / "g.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(e.aux.iterdir()):
            zf.write(f, f.name)
    assert run(capsys, "check", e.board, e.out, "--gerbers", z, *offline(e))[0] == 0
    code, _, err = run(capsys, "check", e.board, e.out, "--gerbers", _patched_zip(e.tmp / "enc.zip", z, flags=1),
                       *offline(e))
    assert code == 3 and "enc.zip: board-bottom_Cu.gbr: cannot be read" in err and "encrypted" in err, err
    code, _, err = run(capsys, "check", e.board, e.out, "--gerbers", _patched_zip(e.tmp / "d64.zip", z, method=9),
                       *offline(e))
    assert code == 3 and "cannot be read" in err and "internal error" not in err, err


def test_cpl_writes_the_rows_from_the_gerbers_origin(made, capsys):
    e = made
    out_page = e.tmp / "from-page.csv"
    code, out, _ = run(capsys, "cpl", e.board, "-o", out_page, "--gerbers", e.page, *offline(e))
    assert code == 0 and f"wrote {out_page} (4 rows)" in out, out
    aux = {r.ref: r for r in read_cpl(e.out)}
    for r in read_cpl(out_page):
        assert (r.x, r.y, r.rotation) == pytest.approx((aux[r.ref].x + 100.0, aux[r.ref].y - 100.0,
                                                        aux[r.ref].rotation), abs=2e-4), r
    assert run(capsys, "check", e.board, out_page, "--gerbers", e.page, *offline(e))[0] == 0
    # --origin naming another origin than the Gerbers': red, nothing written
    out_aux = e.tmp / "from-aux.csv"
    code, out, _ = run(capsys, "cpl", e.board, "-o", out_aux, "--gerbers", e.page, "--origin", "aux", *offline(e))
    assert code == 2 and "origin mismatch: --origin puts" in out and not out_aux.exists(), out
    assert lines(out)[-1] == f"jlcplace: {out_aux} not written: the origin is red (--force writes it anyway)"


def test_confirm_and_show_read_the_rows_from_the_gerbers_origin(made, capsys):
    """field: a CPL that `cpl --gerbers` wrote from page-origin Gerbers, confirmed right in the preview, was read from
    the aux origin: confirm stored a verified correction 141 mm off with exit 0, and every later `cpl` wrote it. The
    rows are read from the Gerbers' (0, 0) with --gerbers, and a row that puts every pad off copper is not taken for
    what was seen right."""
    e = made
    page = e.tmp / "from-page.csv"
    assert run(capsys, "cpl", e.board, "-o", page, "--gerbers", e.page, *offline(e))[0] == 0
    db = e.tmp / "db"
    confirm = ["confirm", e.board, "--ref", "U1", "--db", db, "--by", "ann", "--source", "preview", "--cpl", page,
               *offline(e)]
    code, out, err = run(capsys, *confirm)
    assert code == 3 and not db.exists(), out
    assert err.strip() == (f"jlcplace: U1: read from the aux origin (100, 100), the row in {page.name} puts every "
                           "EasyEDA pad off copper (the nearest 138.47 mm from a KiCad pad centre), so it is not "
                           "what was seen right: the rows are usually read from another origin than the CPL's: give "
                           "the Gerbers it was ordered with (--gerbers), or the origin `jlcplace check` names on its "
                           "first line (--origin); nothing recorded"), err
    code, out, err = run(capsys, *confirm, "--origin", "aux")              # an --origin given is not asked for
    assert code == 3 and err.strip().endswith("usually read from another origin than the CPL's: give the Gerbers it "
                                              "was ordered with (--gerbers); nothing recorded"), err
    assert run(capsys, *confirm, "--gerbers", e.aux, "--origin", "aux")[0] == 3
    # a failure of a row read so is refused as not the correction's, saying the rows may be read from another origin
    code, out, err = run(capsys, *confirm, "--failed")
    assert code == 3 and not db.exists() and err.strip().endswith(
        "its failure says nothing about that correction; read from the aux origin (100, 100), it puts every EasyEDA "
        "pad off copper (the nearest 138.47 mm from a KiCad pad centre): the rows may be read from another origin "
        "than the CPL's (give the Gerbers it was ordered with, --gerbers, or the origin `jlcplace check` names on its "
        "first line, --origin); nothing recorded"), err
    code, out, _ = run(capsys, *confirm, "--gerbers", e.page)
    row = next(r for r in read_cpl(page) if r.ref == "U1")
    assert code == 0 and f"; row X {row.x:.4f} Y {row.y:.4f} rot {row.rotation:g} " in out, out
    assert "replaces" not in out, out                                 # the fitted correction, verified
    for origin in (["--gerbers", e.page], ["--origin", "absolute"]):
        code, out, _ = run(capsys, "show", e.board, "--ref", "U1", "--db", db, *origin, *offline(e), "--json", "-")
        assert code == 0 and json.loads(out)["rows"]["jlc-2022"] == pytest.approx(
            {"x": row.x, "y": row.y, "rotation": row.rotation}, abs=2e-4), out
    code, out, _ = run(capsys, "check", e.board, page, "--gerbers", e.page, "--db", db, *offline(e))
    assert code == 0 and "2 green, 2 verified" in out, out             # U1, and U2 of its entry


def test_confirm_says_why_a_row_seen_right_puts_every_pad_off_copper(made, capsys):
    """The refusal blamed the origin and asked for --gerbers also when they were given: read from the Gerbers' (0, 0),
    JLC's own origin, a row that puts every pad off copper is off itself (JLC moved the part), or moved off by the
    presses (they are wrong), or, for a bottom part, of the --reading the CPL was written with, which the refusal
    names when it lands the pads. A land pattern that looks the same turned half a turn lands under two readings:
    the pads' numbers pick one (a polar part's polarity), and readings they do not tell apart are both named."""
    e = made
    (Path(e.cache) / "C1006.json").write_text(json.dumps(record([("3", -0.48, 0.0, 0.6, 0.6, 0), (
        "4", 0.48, 0.0, 0.6, 0.6, 0)], uuid="r-other")), encoding="utf-8")         # numbers that are not KiCad's
    gerbers = plot(e.make(two("R7", BOTTOM, 105.0, 70.0, 45.0, "C1002"), two("R8", BOTTOM, 112.0, 70.0, 45.0, "C1006")),
                   PAGE, e.tmp / "g-page-2")
    page = e.tmp / "from-page.csv"
    assert run(capsys, "cpl", e.board, "-o", page, "--gerbers", gerbers, *offline(e))[0] == 0
    db = e.tmp / "db"
    confirm = ["confirm", e.board, "--db", db, "--by", "ann", "--source", "preview", "--gerbers", gerbers, *offline(e)]
    moved = e.tmp / "moved.csv"
    write_cpl(moved, [CplRow(r.ref, r.x + 3.0, r.y, r.layer, r.rotation) if r.ref == "U1" else r
                      for r in read_cpl(page)])
    code, out, err = run(capsys, *confirm, "--ref", "U1", "--cpl", moved)
    assert code == 3 and not db.exists() and err.strip() == (
        "jlcplace: U1: read from the Gerbers' (0, 0), the row in moved.csv puts every EasyEDA pad off copper (the "
        "nearest 0.72 mm from a KiCad pad centre), so it is not what was seen right: the row itself is off "
        "(`jlcplace check` reports it so): if the part came out right, JLC moved it and there is nothing to record; "
        "nothing recorded"), err
    # moved off by the presses, a row that lands is not off itself, nor JLC's doing: the presses are wrong
    wrong = ("the presses are probably miscounted or of the wrong sign: confirm again with those that were made; "
             "nothing recorded")
    code, out, err = run(capsys, *confirm, "--ref", "U1", "--cpl", page, "--right", "47")
    assert code == 3 and "read from the Gerbers' (0, 0) and moved by the presses" in err and err.strip().endswith(
        f"so it is not what was seen right: without the presses the row is on the part, and they move it off: "
        f"{wrong}"), err
    code, out, err = run(capsys, *confirm, "--ref", "U1", "--cpl", moved, "--right", "2")
    assert code == 3 and not db.exists() and err.strip().endswith(
        f"so it is not what was seen right: the row is off without the presses too (`jlcplace check` reports it so), "
        f"and they do not put it on the part: {wrong}"), err
    # R7 at 45 on the bottom: the other reading turns its row a quarter turn, and every pad off copper; ccw-from-top
    # and ccw-from-top+180 both land its pads, on their own numbers under the one the CPL was written with only
    for reading in ("ccw-from-top", "ccw-from-top+180"):
        other = e.tmp / f"{reading}.csv"
        assert run(capsys, "cpl", e.board, "-o", other, "--gerbers", gerbers, "--reading", reading,
                   *offline(e))[0] == 0
        code, out, err = run(capsys, *confirm, "--ref", "R7", "--cpl", other)
        assert code == 3 and not db.exists() and (
            f"so it is not what was seen right: the CPL may have been written with --reading {reading}, under which "
            f"the row lands every EasyEDA pad on the KiCad pad of its number: pass the same --reading; otherwise the "
            f"row itself") in err, err
        code, out, err = run(capsys, *confirm, "--ref", "R8", "--cpl", other)
        assert code == 3 and not db.exists() and (
            "--reading ccw-from-top or ccw-from-top+180, under which the row lands every EasyEDA pad on copper: pass "
            "the one it was written with; otherwise") in err, err
    code, out, _ = run(capsys, *confirm, "--ref", "R7", "--cpl", other, "--reading", "ccw-from-top+180")
    assert code == 0 and out.startswith("R7 C1002: preview-verified, verified: rot 0, dx 0.0000, dy 0.0000;"), out


def test_confirm_refuses_a_row_off_a_part_without_an_easyeda_footprint(made, capsys):
    """field: the refusal of a row seen right that puts every pad off copper ran only for parts with an EasyEDA
    footprint: one with none (the checklist's 'not simulated' item, which only the preview can check), read from the
    aux origin, was stored preview-verified 141 mm off with exit 0. Its row point is refused off the part as check's
    rule for it says (a two-pad non-polar part off its pads), or off its pads and its body; cpl's row of a connector
    at the centre of its courtyard, off its pads, is taken. When one --ref is refused, none is recorded."""
    e = made
    board = e.make(two("R3", TOP, 115.0, 70.0, 0.0, "C1005"), coax())
    gerbers = plot(board, PAGE, e.tmp / "g-page-2")
    page = e.tmp / "from-page.csv"
    assert run(capsys, "cpl", e.board, "-o", page, "--gerbers", gerbers, "--force", *offline(e))[0] == 2   # J1 red
    db = e.tmp / "db"
    confirm = ["confirm", e.board, "--db", db, "--by", "ann", "--source", "preview", *offline(e)]
    for ref, what in (("R3", "140.52 mm off the part's pads"), ("J1", "124.10 mm off the part's pads and its "
                                                                       "courtyard")):
        code, out, err = run(capsys, *confirm, "--ref", ref, "--cpl", page)
        assert code == 3 and not db.exists() and err.strip() == (
            f"jlcplace: {ref}: read from the aux origin (100, 100), the row in from-page.csv puts the row point "
            f"{what}, so it is not what was seen right: the rows are usually read from another origin than the CPL's: "
            f"give the Gerbers it was ordered with (--gerbers), or the origin `jlcplace check` names on its first "
            f"line (--origin); nothing recorded"), err
    # nothing to fit and nothing stored: its failure would store that row as the part's correction, 141 mm off
    code, out, err = run(capsys, *confirm, "--ref", "R3", "--cpl", page, "--failed")
    assert code == 3 and not db.exists() and err.strip() == (
        "jlcplace: R3: with no EasyEDA footprint and no stored correction, its failure would store the row in "
        "from-page.csv as its correction; read from the aux origin (100, 100), it puts the row point 140.52 mm off "
        "the part's pads: the rows may be read from another origin than the CPL's (give the Gerbers it was ordered "
        "with, --gerbers, or the origin `jlcplace check` names on its first line, --origin); nothing recorded"), err
    moved = e.tmp / "moved.csv"
    write_cpl(moved, [CplRow(r.ref, r.x + 3.0, r.y, r.layer, r.rotation) if r.ref == "R3" else r
                      for r in read_cpl(page)])
    code, out, err = run(capsys, *confirm, "--ref", "U1", "--ref", "J1", "--ref", "R3", "--cpl", moved, "--gerbers",
                         gerbers)
    assert code == 3 and not db.exists() and err.strip() == (
        "jlcplace: R3: read from the Gerbers' (0, 0), the row in moved.csv puts the row point 2.14 mm off the part's "
        "pads, so it is not what was seen right: the row itself is off (`jlcplace check` reports it so): if the part "
        "came out right, JLC moved it and there is nothing to record; nothing recorded"), err
    code, out, _ = run(capsys, *confirm, "--ref", "R3", "--ref", "J1", "--cpl", page, "--gerbers", gerbers)
    assert code == 0 and out.startswith("R3 C1005: preview-verified, verified: rot 0, dx 0.0000, dy 0.0000;"), out
    assert out.splitlines()[1].startswith("J1 C1003: preview-verified, verified: rot 0, dx 0.0000, dy -13.5000;"), out
    code, out, _ = run(capsys, "check", e.board, page, "--gerbers", gerbers, "--db", db, *offline(e), "--json", "-")
    assert {r["ref"]: r["verdict"] for r in json.loads(out)["results"]}["R3"] == "verified", out


EDGE = """  (footprint "Connector_Coaxial:SMA_EdgeMount_Test" (layer "F.Cu") (at {x} {y} 0)
    (property "Reference" "{ref}") (property "LCSC" "C1003")
    (pad "1" thru_hole circle (at 0 0) (size 2 2) (drill 1) (layers "*.Cu" "*.Mask") (net 0 ""))
    (pad "2" thru_hole circle (at -2.5 0) (size 2 2) (drill 1) (layers "*.Cu" "*.Mask") (net 0 ""))
    (pad "3" thru_hole circle (at 2.5 0) (size 2 2) (drill 1) (layers "*.Cu" "*.Mask") (net 0 ""))
    (fp_rect (start -4 -12) (end 4 1.5) (stroke (width 0.05) (type solid)) (fill no) (layer "F.CrtYd")))"""


def test_confirm_takes_the_row_of_a_part_whose_body_overhangs_the_board_edge(env, capsys):  # noqa: F811
    """A part without an EasyEDA footprint whose row point lay off its pads and outside the board outline was refused,
    also on its body: cpl's own row of an edge-mount connector, at the centre of its courtyard beyond the board's
    edge, could not be recorded (and the refusal said the check calls it off). A row read from another origin is off
    its body too."""
    from test_cli import OUTLINED
    path = env.tmp / "edge.kicad_pcb"
    path.write_text(OUTLINED.format(parts=EDGE.format(ref="J1", x=110, y=81)), encoding="utf-8")
    assert run(capsys, "cpl", path, "-o", env.out, "--reader", "sexpr", "--force", *offline(env))[0] == 2  # J1 red
    db = env.tmp / "db"
    confirm = ["confirm", path, "--reader", "sexpr", "--ref", "J1", "--db", db, "--by", "ann", "--source", "preview",
               *offline(env)]
    code, out, err = run(capsys, *confirm, "--cpl", shifted(env.out, env.tmp / "page.csv", 100.0, -100.0))
    assert code == 3 and not db.exists() and "off the part's pads and its courtyard, so it is not" in err, err
    code, out, err = run(capsys, *confirm, "--cpl", env.out)
    assert code == 0 and out.startswith("J1 C1003: preview-verified, verified: rot 0, dx 0.0000, dy -5.2500; row X "
                                        "10.0000 Y 24.2500 rot 0"), out + err


MODULE_PADS = [((i - 1.5) * 2.54, 12.0) for i in range(4)]
MODULE = """  (footprint "Custom:Display_Module_Test" (layer "F.Cu") (at {x} {y} 0)
    (property "Reference" "{ref}") (property "LCSC" "C1003")
""" + "\n".join(f'    (pad "{i + 1}" smd rect (at {x:g} {y:g}) (size 1.2 2) (layers "F.Cu" "F.Paste" "F.Mask") (net 0 ""))'
                for i, (x, y) in enumerate(MODULE_PADS)) + ")"


def module(ref="M1", x=115.0, y=60.0) -> Part:
    """A made-up display module with no body drawn: KiCad's anchor is its centre, its header's four pads along one
    edge 12 mm away (MODULE, as a .kicad_pcb has it)."""
    return Part(ref, TOP, x, y, 0.0, footprint="Custom:Display_Module_Test", lcsc="C1003",
                pads=[Pad(str(i + 1), px, py, 1.2, 2.0, net=f"{ref}_{i + 1}") for i, (px, py) in enumerate(MODULE_PADS)])


def test_confirm_refuses_a_far_off_row_of_a_part_without_a_body_or_an_outline(env, capsys):  # noqa: F811
    """A part without an EasyEDA footprint, a body or two non-polar pads was refused off its pads only outside the
    board outline: on a snapshot board, which has none, its row read from another origin was stored preview-verified
    141 mm off (also when a wrong origin lands inside an outline). A row further off its pads and KiCad's anchor than
    NEAR_MM and their size is refused; cpl's own row, the anchor, is taken, also where it lies off the pads (a
    module's centre), on a snapshot and inside a board's outline."""
    env.make(coax(body=None), two("D9", TOP, 115.0, 70.0, 33.0, "C1003", footprint="LED_SMD:LED_0603_1608Metric",
                                  half=0.7875), module())
    assert run(capsys, "cpl", env.board, "-o", env.out, "--force", *offline(env))[0] == 2   # J1, D9, M1 red
    page = shifted(env.out, env.tmp / "page.csv", 100.0, -100.0)
    db = env.tmp / "db"
    confirm = ["confirm", env.board, "--db", db, "--by", "ann", "--source", "preview", *offline(env)]
    for ref, mm in (("J1", "132.09"), ("D9", "140.78"), ("M1", "129.11")):
        code, out, err = run(capsys, *confirm, "--ref", ref, "--cpl", page)
        assert code == 3 and not db.exists() and err.strip() == (
            f"jlcplace: {ref}: read from the aux origin (100, 100), the row in page.csv puts the row point {mm} mm off "
            f"the part's pads, so it is not what was seen right: the rows are usually read from another origin than "
            f"the CPL's: give the Gerbers it was ordered with (--gerbers), or the origin `jlcplace check` names on "
            f"its first line (--origin); nothing recorded"), err
    code, out, err = run(capsys, *confirm, "--ref", "J1", "--ref", "D9", "--ref", "M1", "--cpl", env.out)
    assert code == 0 and out.startswith("J1 C1003: preview-verified, verified: rot 0, dx 0.0000, dy 0.0000;"), out
    for line, ref in zip(out.splitlines()[1:], ("D9", "M1")):
        assert line.startswith(f"{ref} C1003: preview-verified, verified: rot 0, dx 0.0000, dy 0.0000;"), out
    from test_cli import OUTLINED
    path = env.tmp / "module.kicad_pcb"
    path.write_text(OUTLINED.format(parts=MODULE.format(ref="M1", x=110, y=86)), encoding="utf-8")
    assert run(capsys, "cpl", path, "-o", env.out, "--reader", "sexpr", "--force", *offline(env))[0] == 2  # M1 red
    confirm = ["confirm", path, "--reader", "sexpr", "--ref", "M1", "--db", env.tmp / "db2", "--by", "ann",
               "--source", "preview", *offline(env)]
    code, out, err = run(capsys, *confirm, "--cpl", shifted(env.out, env.tmp / "page.csv", 100.0, -100.0))
    assert code == 3 and "off the part's pads, outside the board outline, so it is not" in err, err
    code, out, err = run(capsys, *confirm, "--cpl", env.out)
    assert code == 0 and out.startswith("M1 C1003: preview-verified, verified: rot 0, dx 0.0000, dy 0.0000; row X "
                                        "10.0000 Y 14.0000 rot 0"), out + err


def test_without_gerbers_the_origin_is_a_reminder_said_once(made, capsys):
    e = made
    t = e.tmp
    code, out, _ = run(capsys, "check", e.board, e.out, *offline(e), "--json", t / "r.json", "--checklist", t / "c.md",
                       "--junit", t / "r.xml", "--report", t / "r.html")
    assert code == 0
    said = [ln for ln in lines(out) if "Gerbers" in ln]
    assert said == ["Gerbers not checked: plot them from this CPL's origin (aux 100, 100), as JLC lays the CPL over "
                    "them from their own 0, 0 (--gerbers checks it)"], said
    d = json.loads((t / "r.json").read_text(encoding="utf-8"))
    assert d["origin"]["verdict"] == "unchecked" and d["origin"]["gerbers"] is None
    md = (t / "c.md").read_text(encoding="utf-8")
    assert md.count("**Origin:** the Gerbers were not checked: plot them and the drill files from this CPL's "
                    "origin, the aux origin (100, 100)") == 1, md
    suite = ET.fromstring((t / "r.xml").read_text(encoding="utf-8"))
    assert suite.get("tests") == "4"                                    # no origin case: nothing was checked
    assert "the Gerbers were not checked" in (t / "r.html").read_text(encoding="utf-8")


# ------------------------------------------------------------------------------------------------------- --overlay

def test_overlay_writes_both_sides_with_the_origin_finding(made, capsys, monkeypatch):
    e = made
    monkeypatch.setattr(report, "png_renderer", lambda: "")
    d = e.tmp / "ov"
    code, out, err = run(capsys, "check", e.board, e.out, "--gerbers", e.page, *offline(e), "--overlay", d)
    assert code == 2 and sorted(p.name for p in d.iterdir()) == ["bottom.svg", "top.svg"]
    assert "SVGs only" in err and "no PNG renderer" in err, err
    top = (d / "top.svg").read_text(encoding="utf-8")
    assert "origin mismatch: JLC lays the CPL" in top and "JLCEDA/EasyEDA Official Library" in top
    assert 'data-side="bottom"' in (d / "bottom.svg").read_text(encoding="utf-8")
    # a PNG renderer that works: the PNGs too, and nothing said
    calls = []

    def fake(svg, png, *, timeout=120.0):
        calls.append(Path(svg).name)
        Path(png).write_bytes(b"\x89PNG\r\n\x1a\n")
        return "fake"
    monkeypatch.setattr(report, "svg_to_png", fake)
    code, out, err = run(capsys, "cpl", e.board, "-o", e.tmp / "w.csv", *offline(e), "--overlay", e.tmp / "ov2")
    assert code == 0 and calls == ["top.svg", "bottom.svg"] and "PNG" not in err, err
    assert sorted(p.name for p in (e.tmp / "ov2").iterdir()) == ["bottom.png", "bottom.svg", "top.png", "top.svg"]


def test_overlay_that_cannot_be_written_is_bad_input_after_the_other_outputs(made, capsys, monkeypatch):
    e = made
    monkeypatch.setattr(report, "png_renderer", lambda: "")
    blocker = e.tmp / "afile"
    blocker.write_text("x\n", encoding="utf-8")
    code, _, err = run(capsys, "check", e.board, e.out, *offline(e), "--overlay", blocker, "--json", e.tmp / "r.json")
    assert code == 3 and "--overlay" in err and (e.tmp / "r.json").is_file(), err


# ------------------------------------------------------------------------------------------------------- pinout

SOT23 = [("1", -0.9375, -0.95), ("2", -0.9375, 0.95), ("3", 0.9375, 0.0)]


def test_a_pinout_row_is_red_everywhere_and_a_checklist_item_of_its_own():
    """The measured case (an LCSC MOSFET whose pins are G, S, D on a G, D, S symbol): status pinout, red, in the
    summary, the annotations, JUnit, and the checklist's first category, which says the datasheet settles it."""
    pads = [Pad(n, x, y, 1.475, 0.6, net=f"/{name}", pin_name=name) for (n, x, y), name in zip(SOT23, "GDS")]
    part = Part("Q1", TOP, 120.0, 80.0, 0.0, footprint="Package_TO_SOT_SMD:SOT-23", lcsc="C2500", pads=pads)
    ee = EEFootprint("C2500", "SOT-23-3_L2.9-W1.6-P1.90-LS2.8-BR", pin_names=dict(zip("123", "GSD")),
                     pads=[EEPad(n, -x, -y, 1.475, 0.6) for n, x, y in SOT23])
    corr = fit_part(part, ee)
    from jlcplace.convention import cpl_row_for
    x, y, a = cpl_row_for(part, corr, ORIGIN)
    row = CplRow("Q1", x, y, TOP, a)
    assert check_row(part, row, ee, ORIGIN, corr=corr).status == "pinout"
    r = check(Board("b.kicad_pcb", ORIGIN, [part]), [row], {"Q1": Resolved(part, ee, corr, "fit")}, cpl_path="q.csv")
    (it,) = r.checklist()
    assert (it.category, it.ref, it.verdict, it.also) == ("pinout", "Q1", RED, []), it
    assert it.what.startswith("pinout differs from the symbol: pad 2 is S on the LCSC part, D on the board") and \
        it.what.endswith("(in its datasheet: JLC's preview does not show pin functions)"), it.what
    text = report.summary(r)
    assert "RED     Q1  C2500      top     pinout differs from the symbol" in text and "  pinout  Q1: pinout" in text
    md = report.checklist_markdown(r)
    assert "## Pinout" in md and "**Q1** (C2500, top, rot 180) **(red: the LCSC part or the symbol is wrong)**" in md
    assert any(ln.startswith("::error file=q.csv,title=jlcplace Q1 pinout::pinout differs")
               for ln in report.github_annotations(r))
    case = next(tc for tc in ET.fromstring(report.junit(r)).iter("testcase") if tc.get("name") == "Q1")
    assert case.find("failure").get("message").startswith("pinout differs from the symbol")
    assert "pinout" in report.html(r)


def test_a_preview_verified_pinout_says_how_it_is_settled(env, capsys):  # noqa: F811
    """A preview verification does not settle a pinout (JLC's preview shows no pin functions): the red row says what
    does, and a manual confirmation, which ranks below the preview's, says that it changed nothing and what would."""
    from test_cli import HX, HY, save_snapshot
    pads = [Pad(n, x, y, 1.475, 0.6, net=f"/{name}", pin_name=name) for (n, x, y), name in zip(SOT23, "GDS")]
    part = Part("Q1", TOP, 120.0, 80.0, 0.0, footprint="Package_TO_SOT_SMD:SOT-23", lcsc="C2500", pads=pads)
    save_snapshot(Board(str(env.tmp / "q.kicad_pcb"), ORIGIN, [part], source="test"), env.tmp / "q.json")
    shapes = ["~".join(["PAD", "RECT", f"{HX - x / 0.254:.6f}", f"{HY - y / 0.254:.6f}", f"{1.475 / 0.254:.6f}",
                        f"{0.6 / 0.254:.6f}", "1", "", n, "0", "", "0", f"gge{n}", "0", "", "Y", "0", "0", "0.2",
                        "0,0"]) for n, x, y in SOT23]
    pins = [f"P~show~0~{n}~0~0~0~p{n}~0^^0^^0^^1~0~0~0~{name}^^1~0~0~0~{n}" for n, name in zip("123", "GSD")]
    (Path(env.cache) / "C2500.json").write_text(json.dumps({"success": True, "code": 0, "result": {
        "uuid": "sym", "title": "Q", "dataStr": {"head": {"x": 0, "y": 0}, "shape": pins},
        "packageDetail": {"uuid": "sot23", "title": "SOT-23-3_L2.9-W1.6-P1.90-LS2.8-BR", "updateTime": 1700000000,
                          "dataStr": {"head": {"x": HX, "y": HY}, "shape": shapes}}}}), encoding="utf-8")
    board, db, cpl = env.tmp / "q.json", env.tmp / "db", env.tmp / "q.csv"
    assert run(capsys, "cpl", board, "-o", cpl, "--force", "--db", db, *offline(env))[0] == 2
    confirm = ["confirm", board, "--ref", "Q1", "--db", db, "--by", "me", *offline(env)]
    assert run(capsys, *confirm, "--source", "preview", "--cpl", cpl)[0] == 0
    code, out, _ = run(capsys, "check", board, cpl, "--db", db, *offline(env))
    assert code == 2 and "an assembled board that works does (`jlcplace confirm --source board`), or editing the " \
        "entry" in out, out
    code, out, _ = run(capsys, *confirm, "--source", "manual", "--note", "datasheet: the symbol is wrong")
    assert code == 0 and "Q1 C2500: preview-verified" in out, out
    assert "jlcplace: Q1: the entry stays preview-verified (a manual verification ranks below it), and its pinout " \
        "stays red: an assembled board that works settles it (--source board), or editing the entry" in out, out
