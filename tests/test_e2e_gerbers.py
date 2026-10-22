"""End to end with the Gerbers, on the dial-me-spinner boards (aux origin (100, 100)) with real EasyEDA records (a
temporary copy of the cached responses, --offline): JLC lays the CPL over the Gerbers from the Gerbers' own (0, 0).

  * the released CPL over the released Gerbers (plotted from the drill/place origin): no origin problem;
  * the released CPL over Gerbers plotted from the page origin (kicad-cli without --use-drill-file-origin): red for
    the whole run, first, with what JLC will do (every part 141.42 mm off) and the fix (shift every row by
    (+100, -100) in the cpl frame, or one origin for both);
  * KiCad's own position file and Gerbers, both from the page origin: one origin, nothing said about it; the rows
    are judged on their own (KiCad's angles and anchors);
  * `cpl --gerbers` writes the rows from the Gerbers' origin;
  * the old CPLs over the old order's Gerbers: still red on exactly their wrong rows;
  * a MOSFET whose LCSC part names its pins G, S, D on a symbol whose pads are G, D, S: red pinout."""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from jlcplace.cli import main
from jlcplace.cpl import read_cpl

DIAL = Path(os.environ.get("JLCPLACE_DIAL_ME_SPINNER", "/home/user/dial-me-spinner")) / "hardware"
SCRATCH = Path(os.environ.get("JLCPLACE_TEST_EASYEDA", "/tmp/claude-0/-home-user-dial-me-spinner/"
                              "3708159a-bc3b-5167-8507-f5b08e1ce6c9/scratchpad/jlc"))
EE_RAW = SCRATCH / "ee_raw"
BEFORE = SCRATCH / "fab-before"
KICAD_CLI = shutil.which("kicad-cli")


def pcb(b):
    return DIAL / f"kicad/{b}/dial-me-spinner-{b}.kicad_pcb"


def fab(b, what, before=False):
    return (BEFORE / b if before else DIAL / "fab" / b) / f"dial-me-spinner-{b}-{what}-JLCPCB.csv"


def gerbers(b, before=False, zipped=False):
    return (BEFORE / b if before else DIAL / "fab" / b) / ("gerbers.zip" if zipped else "gerbers")


def need(path: Path) -> None:
    if not path.exists():
        pytest.skip(f"{path} is not on this machine (JLCPLACE_DIAL_ME_SPINNER / JLCPLACE_TEST_EASYEDA say where)")


pytestmark = pytest.mark.cache
needs_cli = pytest.mark.skipif(KICAD_CLI is None, reason="kicad-cli is not on this machine")


@pytest.fixture(scope="module")
def cache(tmp_path_factory):
    need(EE_RAW)
    d = tmp_path_factory.mktemp("cache")
    for f in EE_RAW.glob("C*.json"):
        shutil.copy(f, d)
    return d


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    """The main board's Gerbers plotted from the page origin, and KiCad's position file from the page origin."""
    need(pcb("main"))
    d = tmp_path_factory.mktemp("page")
    subprocess.run([KICAD_CLI, "pcb", "export", "gerbers", "-o", str(d / "gerbers"), str(pcb("main"))], check=True,
                   capture_output=True)
    subprocess.run([KICAD_CLI, "pcb", "export", "pos", "--format", "csv", "--units", "mm", "--side", "both",
                    "--exclude-dnp", "-o", str(d / "pos.csv"), str(pcb("main"))], check=True, capture_output=True)
    return d


def jl(capsys, *args):
    code = main([str(a) for a in args])
    out = capsys.readouterr()
    return code, out.out, out.err


def check(capsys, tmp_path, board, cpl, *args, cache):
    """(exit code, stdout, the JSON report) of `jlcplace check`."""
    out_json = tmp_path / "r.json"
    code, out, err = jl(capsys, "check", board, cpl, "--cache", cache, "--offline", "--json", out_json, *args)
    assert code == 3 or out_json.is_file(), err
    return code, out, json.loads(out_json.read_text(encoding="utf-8")) if code != 3 else {}


def verdicts(d, verdict):
    return {r["ref"]: r["status"] for r in d["results"] if r["verdict"] == verdict}


# ------------------------------------------------------------------------------------------------------- released

@pytest.mark.parametrize("zipped", [False, True], ids=["dir", "zip"])
def test_released_cpl_over_the_released_gerbers_has_no_origin_problem(cache, tmp_path, capsys, zipped):
    need(gerbers("main", zipped=zipped))
    code, out, d = check(capsys, tmp_path, pcb("main"), fab("main", "CPL"), "--gerbers", gerbers("main", zipped=zipped),
                         "--bom", fab("main", "BOM"), cache=cache)
    assert code == 0 and d["findings"] == [], out
    o = d["origin"]
    assert (o["x"], o["y"], o["from_gerbers"], o["verdict"], o["offset"]) == (100.0, 100.0, True, "green", None)
    g = o["gerbers"]
    assert (g["named"], g["method"], g["coverage"], g["stale"], g["belongs"]) == ("aux", "x2-pins", 1.0, False, True)
    assert verdicts(d, "red") == {} and verdicts(d, "yellow") == {"R26": "not-simulated", "BZ1": "ok"}
    assert "origin 100, 100 from the Gerbers (aux)" in out.splitlines()[0]


@pytest.mark.parametrize("before", [False, True], ids=["released", "old-order"])
def test_the_base_board_over_its_own_gerbers_has_no_origin_problem(cache, tmp_path, capsys, before):
    need(gerbers("base", before))
    code, out, d = check(capsys, tmp_path, pcb("base"), fab("base", "CPL", before), "--gerbers",
                         gerbers("base", before), cache=cache)
    assert d["findings"] == [] and d["origin"]["gerbers"]["named"] == "aux", out


# ------------------------------------------------------------------------------------------------------- page origin

@pytest.mark.pcbnew
@needs_cli
def test_page_origin_gerbers_and_the_released_cpl_are_red_with_the_shift_and_the_fix(cache, page, tmp_path, capsys):
    """The most common failure, measured: Gerbers plotted from the page origin, the CPL from the drill/place origin.
    JLC would put every part (-100, +100) mm off (cpl frame: 100 left, 100 up); shifting every row by (+100, -100)
    mm, or plotting the Gerbers from the drill/place origin, or writing the CPL from the page origin fixes it."""
    code, out, d = check(capsys, tmp_path, pcb("main"), fab("main", "CPL"), "--gerbers", page / "gerbers",
                         "--github-annotations", cache=cache)
    assert code == 2
    first = out.splitlines()[1]
    assert first.startswith("RED     origin mismatch: JLC lays the CPL over the Gerbers from their (0, 0), board "
                            "(0, 0) (the page origin), but the CPL was written from the aux (drill/place file) origin "
                            "(100, 100) (184 of 184 rows): JLC would put every part 141.42 mm off, displaced by "
                            "(-100.000, +100.000) mm; fix: plot the Gerbers and drill files from the aux (drill/place "
                            "file) origin (KiCad: 'Use drill/place file origin' ticked), or write the CPL from the "
                            "Gerbers' origin"), first
    assert first.endswith("or shift every row by (+100.000, -100.000) mm (`check --fixed-cpl FILE` writes it)"), first
    assert [(f["status"], f["verdict"]) for f in d["findings"]] == [("origin", "red")]
    off = d["origin"]["offset"]
    assert (off["dx"], off["dy"], off["origin"], off["origin_kind"], off["rows"], off["total"]) == \
        (-100.0, 100.0, [100.0, 100.0], "aux", 184, 184)
    g = d["origin"]["gerbers"]
    assert (g["origin"], g["named"]) == ([0.0, 0.0], "absolute")
    assert len(verdicts(d, "red")) == 184                                # as JLC reads them: every part off
    assert re.search(r"^::error file=[^,]*,title=jlcplace origin::origin mismatch", out, re.M)
    # the fix the finding names: every row shifted by (+100, -100)
    fixed = tmp_path / "fixed.csv"
    fixed.write_text("Designator,Mid X,Mid Y,Layer,Rotation\n" + "".join(
        f"{r.ref},{r.x + 100:.4f}mm,{r.y - 100:.4f}mm,{r.layer.capitalize()},{r.rotation:g}\n"
        for r in read_cpl(fab("main", "CPL"))), encoding="utf-8")
    code, out, d = check(capsys, tmp_path, pcb("main"), fixed, "--gerbers", page / "gerbers", cache=cache)
    assert code == 0 and d["findings"] == [] and verdicts(d, "red") == {}, out
    # --origin aux with those Gerbers: the option names another origin than JLC's
    code, out, d = check(capsys, tmp_path, pcb("main"), fab("main", "CPL"), "--gerbers", page / "gerbers",
                         "--origin", "aux", cache=cache)
    assert code == 2 and d["findings"][0]["message"].startswith("origin mismatch: JLC lays the CPL over the Gerbers")


@pytest.mark.pcbnew
@needs_cli
def test_kicads_position_file_over_page_origin_gerbers_is_one_origin(cache, page, tmp_path, capsys):
    """KiCad's position file and Gerbers both from the page origin: no origin finding, the rows judged on their own
    (KiCad's angles and anchors: the parts EasyEDA draws turned or shifted are red, the rest sit on their pads).
    Without the Gerbers the same file gets the common-offset hint, which names the origin to pass."""
    code, out, d = check(capsys, tmp_path, pcb("main"), page / "pos.csv", "--gerbers", page / "gerbers", cache=cache)
    assert d["findings"] == [] and d["origin"]["offset"] is None and d["origin"]["verdict"] == "green", out
    assert "origin mismatch" not in out and "origin 0, 0 from the Gerbers (absolute)" in out.splitlines()[0]
    red = verdicts(d, "red")
    assert code == 2 and 5 <= len(red) <= 30 and {"J1", "J20", "U2", "U21"} <= set(red), red
    assert len(verdicts(d, "green")) > 150
    code, out, d = check(capsys, tmp_path, pcb("main"), page / "pos.csv", cache=cache)
    assert code == 2 and "jlcplace: origin mismatch: 182 of 184 rows would sit on their parts" in out
    assert "--origin absolute" in out and d["findings"] == []


@pytest.mark.pcbnew
@needs_cli
def test_cpl_writes_the_rows_from_the_gerbers_origin(cache, page, tmp_path, capsys):
    out = tmp_path / "page-cpl.csv"
    code, text, _ = jl(capsys, "cpl", pcb("main"), "-o", out, "--gerbers", page / "gerbers", "--cache", cache,
                       "--offline")
    assert code == 0 and f"wrote {out} (184 rows)" in text, text
    got, want = {r.ref: r for r in read_cpl(out)}, {r.ref: r for r in read_cpl(fab("main", "CPL"))}
    assert sorted(got) == sorted(want)
    for ref, g in got.items():
        w = want[ref]
        assert (g.layer, g.rotation) == (w.layer, w.rotation), ref
        assert abs(g.x - (w.x + 100.0)) <= 6e-4 and abs(g.y - (w.y - 100.0)) <= 6e-4, (ref, g, w)


# ------------------------------------------------------------------------------------------------------- old order

def test_old_cpl_over_the_old_gerbers_is_red_on_exactly_the_wrong_rows(cache, tmp_path, capsys):
    need(fab("main", "CPL", before=True))
    need(gerbers("main", before=True))
    code, out, d = check(capsys, tmp_path, pcb("main"), fab("main", "CPL", before=True), "--gerbers",
                         gerbers("main", before=True), cache=cache)
    assert code == 2 and d["findings"] == []
    assert verdicts(d, "red") == {"J1": "off-copper", "J20": "off-copper", "J40": "off-copper"}, out


# ------------------------------------------------------------------------------------------------------- pinout

def _named_pins(raw: dict, names: dict) -> dict:
    """An EasyEDA API response whose symbol pins carry other names: {pad number: name}."""
    shapes = raw["result"]["dataStr"]["shape"]
    for i, s in enumerate(shapes):
        if isinstance(s, str) and s.startswith("P~"):
            segs = s.split("^^")
            number = segs[4].split("~")[4] if len(segs) > 4 else s.split("~")[3]
            fields = segs[3].split("~")
            fields[4] = names.get(number, fields[4])
            segs[3] = "~".join(fields)
            shapes[i] = "^^".join(segs)
    return raw


def _with_pin_functions(text: str, ref: str, functions: dict) -> str:
    """The .kicad_pcb text with (pinfunction ...) on the footprint `ref`'s pads, as KiCad writes it after "Update PCB
    from schematic"."""
    at = text.index(f'"Reference" "{ref}"')
    start = text.rfind("(footprint", 0, at)
    end = text.index("(embedded_fonts", at)
    body = text[start:end]
    for number, name in functions.items():
        m = re.search(rf'\(pad "{number}" .*?(\(net \d+ "[^"]*"\))', body, re.S)
        body = body[:m.end()] + f'\n\t\t\t(pinfunction "{name}")\n\t\t\t(pintype "passive")' + body[m.end():]
    return text[:start] + body + text[end:]


def test_a_mosfet_whose_pins_are_permuted_against_the_symbol_is_red_pinout(cache, tmp_path, capsys):
    """The SOT-23 at D20 made a MOSFET: its symbol's pads are G, D, S (KiCad's Q_NMOS_GDS), the LCSC part (the
    cached C2500 record, its pins renamed) names them G, S, D. Every pad lands on copper, so a check by pads alone
    passes it; drain and source are swapped, so the board does not work. Red, status pinout, with or without the
    Gerbers, and the checklist says to check the part's datasheet."""
    need(pcb("main"))
    board = tmp_path / "mosfet.kicad_pcb"
    gds = {"1": "G", "2": "D", "3": "S"}
    board.write_text(_with_pin_functions(pcb("main").read_text(encoding="utf-8"), "D20", gds), encoding="utf-8")
    recs = tmp_path / "cache"
    shutil.copytree(cache, recs)
    raw = json.loads((recs / "C2500.json").read_text(encoding="utf-8"))
    (recs / "C2500.json").write_text(json.dumps(_named_pins(raw, {"1": "G", "2": "S", "3": "D"})), encoding="utf-8")
    extra = ("--gerbers", gerbers("main")) if gerbers("main").exists() else ()
    code, out, d = check(capsys, tmp_path, board, fab("main", "CPL"), "--reader", "sexpr", "--checklist",
                         tmp_path / "c.md", *extra, cache=recs)
    assert code == 2 and verdicts(d, "red") == {"D20": "pinout"}, out
    d20 = next(r for r in d["results"] if r["ref"] == "D20")
    assert d20["messages"][0].startswith("pinout differs from the symbol: pad 2 is S on the LCSC part, D on the "
                                         "board; pad 3 is D on the LCSC part, S on the board"), d20["messages"]
    assert all(lnd["kicad"] is not None for lnd in d20["landings"])               # every pad on copper
    assert d20["correction"]["verdict"] == "red"
    item = d["checklist"][0]
    assert (item["category"], item["ref"], item["verdict"]) == ("pinout", "D20", "red")
    assert "## Pinout" in (tmp_path / "c.md").read_text(encoding="utf-8")
    # the board without the pin functions: names cannot be compared, the row is as before
    code, out, d = check(capsys, tmp_path, pcb("main"), fab("main", "CPL"), "--reader", "sexpr", cache=recs)
    assert code == 0 and verdicts(d, "red") == {}, out
