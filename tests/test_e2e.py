"""End to end on the dial-me-spinner boards with real EasyEDA records (a temporary copy of the cached responses,
--offline): the command line in-process and as a subprocess.

Scenarios: the released (correct) CPLs, the old CPLs with 7 misplaced rows, CPLs written by jlcplace from fits alone,
KiCad's own position file checked as if it were a CPL (the naive export), and snapshots checked without KiCad."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from jlcplace.cli import main
from jlcplace.cpl import read_cpl

DIAL = Path(os.environ.get("JLCPLACE_DIAL_ME_SPINNER", "/home/user/dial-me-spinner")) / "hardware"
SCRATCH = Path(os.environ.get("JLCPLACE_TEST_EASYEDA", "/tmp/claude-0/-home-user-dial-me-spinner/"
                              "3708159a-bc3b-5167-8507-f5b08e1ce6c9/scratchpad/jlc"))
EE_RAW = SCRATCH / "ee_raw"
BEFORE = SCRATCH / "fab-before"
OFFSETS = DIAL / "design/jlc_offsets.json"
BOARDS = ("main", "base")


def pcb(b):
    return DIAL / f"kicad/{b}/dial-me-spinner-{b}.kicad_pcb"


def cpl(b, before=False):
    return (BEFORE / b if before else DIAL / "fab" / b) / f"dial-me-spinner-{b}-CPL-JLCPCB.csv"


def bom(b, before=False):
    return (BEFORE / b if before else DIAL / "fab" / b) / f"dial-me-spinner-{b}-BOM-JLCPCB.csv"


pytestmark = [pytest.mark.cache,
              pytest.mark.skipif(not all(pcb(b).is_file() and cpl(b).is_file() for b in BOARDS),
                                 reason=f"needs the dial-me-spinner boards and CPLs under {DIAL}"),
              pytest.mark.skipif(not EE_RAW.is_dir(), reason=f"needs cached EasyEDA records in {EE_RAW}")]
need_before = pytest.mark.skipif(not all(cpl(b, True).is_file() for b in BOARDS),
                                 reason=f"needs the old CPLs in {BEFORE}")


@pytest.fixture(scope="module")
def cache(tmp_path_factory):
    d = tmp_path_factory.mktemp("cache")
    for f in EE_RAW.glob("C*.json"):
        shutil.copy(f, d)
    return d


def jl(capsys, *args):
    code = main([str(a) for a in args])
    out = capsys.readouterr()
    return code, out.out, out.err


def results(path) -> dict:
    return {r["ref"]: r for r in json.loads(Path(path).read_text())["results"]}


def by_verdict(res: dict, verdict: str) -> dict:
    return {ref: r["status"] for ref, r in res.items() if r["verdict"] == verdict}


def confirm_sw1(capsys, cache, db):
    code, out, _ = jl(capsys, "confirm", pcb("base"), "--ref", "SW1", "--db", db, "--by", "test", "--source",
                      "manual", "--note", "TS-1187A: EasyEDA pads 1-2 and 3-4 are each one terminal", "--cache",
                      cache, "--offline")
    assert code == 0 and out.startswith("SW1 C318884: manual, yellow: rot 0, dx 0.0000, dy 0.0000")


# ------------------------------------------------------------------------------------------------------- released

def test_released_cpls_have_no_red_rows(cache, tmp_path, capsys):
    """main: clean but for R26, whose code EasyEDA does not know (a 0402 resistor: yellow). base: SW1 is red until
    someone decides the switch's numbering (EasyEDA 1-4 against KiCad 1,1,2,2: which EasyEDA pins are joined inside
    the switch is not in the records); confirmed manually it is yellow."""
    code, out, _ = jl(capsys, "check", pcb("main"), cpl("main"), "--bom", bom("main"), "--cache", cache, "--offline",
                      "--json", tmp_path / "main.json")
    res = results(tmp_path / "main.json")
    assert code == 0 and len(res) == 184
    assert by_verdict(res, "red") == {} and by_verdict(res, "yellow") == {"R26": "not-simulated"}
    assert json.loads((tmp_path / "main.json").read_text())["bom"] == []
    assert not [r for r in res.values() if any("differs from the row jlcplace writes" in m for m in r["messages"])]

    code, out, _ = jl(capsys, "check", pcb("base"), cpl("base"), "--bom", bom("base"), "--cache", cache, "--offline",
                      "--json", tmp_path / "base.json")
    res = results(tmp_path / "base.json")
    assert code == 2 and by_verdict(res, "red") == {"SW1": "renumbered"} and by_verdict(res, "yellow") == {}
    assert "nothing vouches for this numbering: 2->1" in res["SW1"]["messages"][0]
    assert res["SW1"]["correction"]["reasons"][0].startswith("numbering conflict: EE 2 -> KiCad 1")

    db = tmp_path / "db"
    confirm_sw1(capsys, cache, db)
    code, out, _ = jl(capsys, "check", pcb("base"), cpl("base"), "--db", db, "--cache", cache, "--offline", "--json",
                      tmp_path / "base2.json")
    res = results(tmp_path / "base2.json")
    assert code == 0 and by_verdict(res, "red") == {} and by_verdict(res, "yellow") == {"SW1": "renumbered"}
    assert res["SW1"]["messages"][0] == "numbering accepted by the correction (manual): 2->1"
    assert code == jl(capsys, "check", pcb("base"), cpl("base"), "--db", db, "--cache", cache, "--offline",
                      "--fail-on", "yellow", "-q")[0] - 1


@need_before
def test_old_cpls_are_red_on_exactly_the_seven_wrong_rows(cache, tmp_path, capsys):
    red = {}
    for b in BOARDS:
        out_json = tmp_path / f"{b}.json"
        code, out, _ = jl(capsys, "check", pcb(b), cpl(b, True), "--cache", cache, "--offline", "--json", out_json,
                          "--report", tmp_path / f"{b}.html", "--checklist", tmp_path / f"{b}.md", "--junit",
                          tmp_path / f"{b}.xml", "--github-annotations")
        res = results(out_json)
        assert code == 2
        red[b] = by_verdict(res, "red")
        annotated = {ln.split("title=jlcplace ")[1].split(" ")[0] for ln in out.splitlines()
                     if ln.startswith("::error")}
        assert annotated == set(red[b])
        page = (tmp_path / f"{b}.html").read_text()
        for ref in red[b]:
            assert f'<b>{ref}</b>' in page and page.index(f"<b>{ref}</b>") < page.index('<details class="card green"')
    assert red == {"main": {"J1": "off-copper", "J20": "off-copper", "J40": "off-copper"},
                   "base": {"J1": "off-copper", "J2": "off-copper", "J3": "off-copper", "SW1": "off-copper"}}
    md = (tmp_path / "main.md").read_text()
    assert "## Bottom reading" in md and "**J1** (C160411, bottom, rot 120) **(red: fix the row first)**" in md


def test_cpl_from_fits_reproduces_the_released_cpls(cache, tmp_path, capsys):
    """Rows written from fits alone (no database) against the released CPLs (made from jlc_offsets.json): the same
    refs, sides and angles, positions within 0.6 um. The released rows used corrections rounded to 3 decimals, the
    fits keep 4; base needs SW1's numbering decided first (red, so `cpl` refuses), R26 gets KiCad's own row."""
    offsets = json.loads(OFFSETS.read_text()) if OFFSETS.is_file() else {}
    out = tmp_path / "main.csv"
    code, text, _ = jl(capsys, "cpl", pcb("main"), "-o", out, "--cache", cache, "--offline", "--json",
                       tmp_path / "main.json")
    assert code == 0 and f"wrote {out} (184 rows)" in text
    assert by_verdict(results(tmp_path / "main.json"), "yellow") == {"R26": "not-simulated"}
    _same_rows(out, cpl("main"), offsets)

    out = tmp_path / "base.csv"
    code, text, _ = jl(capsys, "cpl", pcb("base"), "-o", out, "--cache", cache, "--offline")
    assert code == 2 and "not written" in text and not out.exists() and "RED     SW1" in text
    db = tmp_path / "db"
    confirm_sw1(capsys, cache, db)
    code, text, _ = jl(capsys, "cpl", pcb("base"), "-o", out, "--db", db, "--cache", cache, "--offline")
    assert code == 0 and out.exists()
    _same_rows(out, cpl("base"), offsets)


def _same_rows(got_path, want_path, offsets):
    got, want = {r.ref: r for r in read_cpl(got_path)}, {r.ref: r for r in read_cpl(want_path)}
    assert sorted(got) == sorted(want)
    worst = 0.0
    for ref, g in got.items():
        w = want[ref]
        assert (g.layer, g.rotation) == (w.layer, w.rotation), ref
        worst = max(worst, abs(g.x - w.x), abs(g.y - w.y))
    assert worst <= 0.0006
    if offsets:                                     # the released corrections are 3-decimal roundings
        assert all(round(float(e["dx"]), 3) == float(e["dx"]) for e in offsets.values() if "dx" in e)


# ------------------------------------------------------------------------------------------------------- naive export

NAIVE_RED = {
    "main": {"BZ1": "off-copper", "D20": "off-copper", "J1": "off-copper", "J20": "off-copper", "J40": "off-copper",
             "U1": "off-copper", "U10": "off-copper", "U11": "off-copper", "U12": "off-copper", "U2": "wrong-pad",
             "U20": "off-copper", "U21": "wrong-pad", "U31": "wrong-pad", "U32": "off-copper", "U40": "wrong-pad",
             "U41": "wrong-pad", "U42": "off-copper"},
    "base": {"J1": "off-copper", "U1": "off-copper", "U2": "off-copper"},
}


@pytest.mark.skipif(shutil.which("kicad-cli") is None, reason="needs kicad-cli")
def test_kicad_position_file_as_a_cpl(cache, tmp_path, capsys):
    """KiCad's own position file uploaded as the CPL (what a coworker would try first): KiCad's angles and anchors with
    no JLC correction, and KiCad's angle, not 180 - theta, on the bottom. Every row that differs from the released one
    is red unless the difference is harmless: a non-polar two-terminal part turned half a turn (its terminals
    swapped) or a few hundredths of a millimetre with every pad centre on its own pad. SOT-23-6 parts turned 180
    degrees land every pad on copper, on the wrong nets: only the net check catches them."""
    db = tmp_path / "db"
    confirm_sw1(capsys, cache, db)
    for b in BOARDS:
        pos = tmp_path / f"{b}-pos.csv"
        subprocess.run(["kicad-cli", "pcb", "export", "pos", "--format", "csv", "--units", "mm",
                        "--use-drill-file-origin", "--side", "both", "--exclude-dnp", "-o", str(pos), str(pcb(b))],
                       check=True, capture_output=True)
        code, out, _ = jl(capsys, "check", pcb(b), pos, "--bom", bom(b), "--db", db, "--cache", cache, "--offline",
                          "--json", tmp_path / f"{b}.json")
        res = results(tmp_path / f"{b}.json")
        assert code == 2 and by_verdict(res, "red") == NAIVE_RED[b], b
        naive, good = {r.ref: r for r in read_cpl(pos)}, {r.ref: r for r in read_cpl(cpl(b))}
        assert sorted(naive) == sorted(good)
        for ref, n in naive.items():
            g, r = good[ref], res[ref]
            d = max(abs(n.x - g.x), abs(n.y - g.y))
            da = abs((n.rotation - g.rotation + 180.0) % 360.0 - 180.0)
            if d < 0.01 and da < 0.01:
                assert r["verdict"] != "red", ref                         # the released row: never red
            elif r["verdict"] != "red":
                swapped = r["status"] == "renumbered" and da == 180.0 and d < 1e-3 and len(r["landings"]) == 2
                assert swapped or (d < 0.07 and da == 0.0 and r["status"] == "ok"), (b, ref, d, da, r["status"])
        if b == "main":
            for ref in ("U2", "U21"):
                assert all(lnd["kicad"] is not None for lnd in res[ref]["landings"])


# ------------------------------------------------------------------------------------------------------- snapshots

@pytest.mark.pcbnew
@need_before
def test_snapshot_round_trip_and_checks_without_kicad(cache, tmp_path, capsys):
    """A snapshot made with pcbnew, checked in a subprocess where `import pcbnew` fails, gives the same results as the
    board read with pcbnew; so does the S-expression reader on the .kicad_pcb file itself."""
    pytest.importorskip("pcbnew")
    snap = tmp_path / "main.json"
    code, out, _ = jl(capsys, "snapshot", pcb("main"), "-o", snap)
    assert code == 0 and "209 parts (pcbnew), origin aux (100, 100)" in out
    jl(capsys, "check", pcb("main"), cpl("main", True), "--cache", cache, "--offline", "--json", tmp_path / "ref.json")
    ref = results(tmp_path / "ref.json")

    stub = tmp_path / "nopcbnew"
    stub.mkdir()
    (stub / "pcbnew.py").write_text("raise ImportError('pcbnew is blocked for this test')\n")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(stub), str(Path(__file__).parents[1] / "src")])}
    assert subprocess.run([sys.executable, "-c", "import pcbnew"], env=env, capture_output=True).returncode != 0

    def sub(*args):
        p = subprocess.run([sys.executable, "-m", "jlcplace", *map(str, args)], env=env, capture_output=True,
                           text=True, timeout=300)
        return p.returncode, p.stdout, p.stderr

    for board, extra in ((snap, ()), (snap, ("--reader", "json")), (pcb("main"), ()),
                         (pcb("main"), ("--reader", "sexpr"))):
        out_json = tmp_path / "sub.json"
        code, out, err = sub("check", board, cpl("main", True), "--cache", cache, "--offline", "--json", out_json,
                             *extra)
        assert code == 2, err
        got = results(out_json)
        assert {k: (r["status"], r["verdict"]) for k, r in got.items()} == \
            {k: (r["status"], r["verdict"]) for k, r in ref.items()}, (board, extra)
        # a snapshot remembers the reader that made it; the .kicad_pcb file falls back to the S-expression reader
        assert json.loads(out_json.read_text())["board"]["source"] == ("pcbnew" if board == snap else "sexpr")
    code, _, err = sub("check", pcb("main"), cpl("main"), "--reader", "pcbnew", "--cache", cache, "--offline")
    assert code == 3 and "pcbnew" in err
    code, out, _ = sub("check", snap, cpl("main"), "--cache", cache, "--offline")
    assert code == 0 and "184 results: 0 red, 1 yellow, 183 green, 0 verified" in out
