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

from test_cli import ORIGIN, env, offline, run  # noqa: F401  (env is a fixture)

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
    assert code == 2 and first.startswith("RED     origin mismatch: JLC lays the CPL over the Gerbers from their (0, 0), "
                                          "board (100, 100) (the aux (drill/place file) origin), but the CPL was "
                                          "written from board (99.800, 100.000), none of the board's origins"), out
    assert "JLC would put every part 0.20 mm off" in first and "shift every row by (-0.200, +0.000) mm" in first
    code, out, _ = run(capsys, "check", e.board, small, *offline(e))
    assert "origin mismatch" not in out
    tiny = shifted(e.out, e.tmp / "tiny.csv", 0.03, 0.0)
    code, out, _ = run(capsys, "check", e.board, tiny, "--gerbers", e.aux, *offline(e))
    assert code == 0 and "origin mismatch" not in out, out


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
