"""The command line, the pipeline and the reports on a synthetic board with hand-made EasyEDA records: no KiCad, no
EasyEDA data and no network needed."""
import csv
from html.parser import HTMLParser
import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest

from jlcplace import convention as cv
from jlcplace import geom
from jlcplace.board import save_snapshot
from jlcplace.check import check_row
from jlcplace.cli import main
from jlcplace.cpl import read_cpl, write_cpl
from jlcplace.db import CorrectionDB
from jlcplace.model import BOTTOM, GREEN, RED, TOP, VERIFIED, YELLOW, Board, Correction, CplRow, EEFootprint, \
    NotFound, Pad, Part
from jlcplace.parts import ee_key
from jlcplace.pipeline import Run, apply_bom_codes, check, correction, key_reasons, proxy_footprint, resolve
from jlcplace.report import _gh, github_annotations, html, junit

ORIGIN = (100.0, 100.0)
SOT = [("1", -1.1375, -0.95), ("2", -1.1375, 0.0), ("3", -1.1375, 0.95),
       ("4", 1.1375, 0.95), ("5", 1.1375, 0.0), ("6", 1.1375, -0.95)]
NETS = ("A", "GND", "B", "C", "VCC", "D")
HX, HY = 4000.0, 3000.0                    # EasyEDA footprint origin, canvas units (10 mil)


def sot(ref, layer, x, y, angle):
    pads = [Pad(n, px, py, 1.325, 0.6, shape="roundrect", rr=0.25, net=net) for (n, px, py), net in zip(SOT, NETS)]
    return Part(ref, layer, x, y, angle, footprint="Package_TO_SOT_SMD:SOT-23-6", lcsc="C1001", pads=pads)


def two(ref, layer, x, y, angle, code, footprint="Resistor_SMD:R_0402_1005Metric", half=0.48, nets=None):
    nets = nets or (f"{ref}_1", f"{ref}_2")
    return Part(ref, layer, x, y, angle, footprint=footprint, lcsc=code,
                pads=[Pad("1", -half, 0.0, 0.56, 0.62, net=nets[0]), Pad("2", half, 0.0, 0.56, 0.62, net=nets[1])])


def record(pads, uuid="fp", utime=1700000000):
    """An API response whose footprint has RECT pads [(number, x, y, w, h, rotation)] in the ee frame (mm)."""
    shapes = []
    for i, (n, x, y, w, h, r) in enumerate(pads):
        px, py = HX + x / 0.254, HY + y / 0.254
        shapes.append("~".join(["PAD", "RECT", f"{px:.6f}", f"{py:.6f}", f"{w / 0.254:.6f}", f"{h / 0.254:.6f}", "1",
                                "", n, "0", "", f"{r:g}", f"gge{i}", "0", "", "Y", "0", "0", "0.2", f"{px},{py}"]))
    return {"success": True, "code": 0, "result": {
        "uuid": "sym", "title": "T", "dataStr": {"head": {"x": 0, "y": 0}, "shape": []},
        "packageDetail": {"uuid": uuid, "title": f"TEST-{uuid}", "updateTime": utime,
                          "dataStr": {"head": {"x": HX, "y": HY}, "shape": shapes}}}}


RECORDS = {
    # the SOT-23-6 drawn turned half a turn: the correction is rot 180
    "C1001": record([(n, -x, -y, 1.3, 0.6, 0) for n, x, y in SOT], uuid="sot"),
    "C1002": record([("1", -0.48, 0.0, 0.6, 0.6, 0), ("2", 0.48, 0.0, 0.6, 0.6, 0)], uuid="r0402"),
    "C1003": {"success": False, "code": 404, "message": "Component not found"},
    "C1005": {"success": False, "code": 404, "message": "Component not found"},
}


@pytest.fixture
def env(tmp_path):
    """A board snapshot (U1 top, U2 bottom at 30 deg, R1 top at 90, R2 bottom), a record cache, and helpers."""
    def make(*extra):
        parts = [sot("U1", TOP, 120.0, 80.0, 0.0), sot("U2", BOTTOM, 130.0, 90.0, 30.0),
                 two("R1", TOP, 110.0, 95.0, 90.0, "C1002"), two("R2", BOTTOM, 105.0, 85.0, 0.0, "C1002"),
                 Part("H1", TOP, 140.0, 60.0, 0.0, footprint="MountingHole:MountingHole_3.2mm", exclude_bom=True)]
        board = Board(str(tmp_path / "board.kicad_pcb"), ORIGIN, parts + list(extra), source="test")
        save_snapshot(board, tmp_path / "board.json")
        return board

    cache = tmp_path / "cache"
    cache.mkdir()
    for code, rec in RECORDS.items():
        (cache / f"{code}.json").write_text(json.dumps(rec))
    (tmp_path / "empty").mkdir()
    return SimpleNamespace(tmp=tmp_path, make=make, board=str(tmp_path / "board.json"), cache=str(cache),
                           empty=str(tmp_path / "empty"), out=str(tmp_path / "out.csv"))


def run(capsys, *args):
    code = main([str(a) for a in args])
    out = capsys.readouterr()
    return code, out.out, out.err


def offline(env, cache=None):
    return ["--cache", cache or env.cache, "--offline"]


# ------------------------------------------------------------------------------------------------------- cpl, check

def test_cpl_writes_rows_that_check_clean(env, capsys):
    board = env.make()
    code, out, _ = run(capsys, "cpl", env.board, "-o", env.out, *offline(env))
    assert code == 0 and f"wrote {env.out} (4 rows)" in out
    rows = {r.ref: r for r in read_cpl(env.out)}
    assert list(rows) == ["R1", "R2", "U1", "U2"]                       # H1 has no code and no BOM line
    for ref, alpha in (("U1", 180.0), ("U2", 180.0), ("R1", 0.0), ("R2", 0.0)):
        x, y, a = cv.cpl_row_for(board.part(ref), Correction(alpha), ORIGIN)
        assert (rows[ref].x, rows[ref].y, rows[ref].rotation) == pytest.approx((x, y, a), abs=2e-4), ref
    assert rows["U1"].rotation == 180.0 and (rows["U1"].x, rows["U1"].y) == (20.0, 20.0)
    assert oct(os.stat(env.out).st_mode & 0o777) != "0o600"           # not left as mkstemp made it

    t = env.tmp
    code, out, _ = run(capsys, "check", env.board, env.out, *offline(env), "--json", t / "r.json", "--report",
                       t / "r.html", "--junit", t / "r.xml", "--checklist", t / "c.md")
    assert code == 0
    assert "4 results: 0 red, 0 yellow, 4 green, 0 verified" in out
    d = json.loads((t / "r.json").read_text())
    assert d["summary"]["red"] == 0 and d["summary"]["worst"] == GREEN and len(d["results"]) == 4
    u1 = next(r for r in d["results"] if r["ref"] == "U1")
    assert u1["correction"]["source"] == "fit" and u1["correction"]["rot"] == 180.0 and u1["line"] == 4
    assert all(lnd["ee"] == lnd["kicad"] for lnd in u1["landings"])
    # U2: bottom at a row angle of 330, so the bottom reading matters and the angle is odd
    cats = {(i["category"], i["ref"]) for i in d["checklist"]}
    assert {("bottom reading", "U2"), ("odd angle", "U2")} <= cats and ("bottom reading", "R2") not in cats
    md = (t / "c.md").read_text()
    assert "## Bottom reading" in md and "- [ ] **U2**" in md
    suite = ET.fromstring((t / "r.xml").read_text())
    assert (suite.get("tests"), suite.get("failures")) == ("4", "0")
    page = (t / "r.html").read_text()
    assert page.count('<details class="card green"') == 4 and page.count("<svg") == 6


def test_mutated_rows_are_red_and_annotated(env, capsys):
    env.make()
    run(capsys, "cpl", env.board, "-o", env.out, *offline(env))
    rows = read_cpl(env.out)
    for r in rows:
        if r.ref == "U1":
            r.rotation = 0.0                                 # KiCad's own angle: the SOT-23-6 half a turn off
        if r.ref == "R1":
            r.x += 0.5                                       # off its pads
    write_cpl(env.out, rows)
    code, out, _ = run(capsys, "check", env.board, env.out, *offline(env), "--github-annotations")
    assert code == 2
    lines = out.splitlines()
    assert any(ln.startswith("RED     U1") and "wrong-pad: EasyEDA pads land on other-numbered KiCad pads of other "
               "nets: 1->4 (C, not A)" in ln for ln in lines)
    assert any(ln.startswith("RED     R1") and "off-copper" in ln for ln in lines)
    ann = [ln for ln in lines if ln.startswith("::")]
    assert len(ann) == 2
    assert ann[1].startswith(f"::error file={_gh(env.out, True)},line=4,title=jlcplace U1 wrong-pad::")
    assert ann[0].startswith(f"::error file={_gh(env.out, True)},line=2,title=jlcplace R1 off-copper::")


def test_fail_on_yellow_and_reports(env, capsys):
    env.make(two("R3", TOP, 112.0, 70.0, 0.0, "C1005"))      # EasyEDA has no such component: a resistor, yellow
    code, out, _ = run(capsys, "cpl", env.board, "-o", env.out, *offline(env))
    assert code == 0 and "YELLOW  R3" in out and "not-simulated" in out
    assert any(r.ref == "R3" and (r.x, r.y, r.rotation) == (12.0, 30.0, 0.0) for r in read_cpl(env.out))
    assert run(capsys, "check", env.board, env.out, *offline(env))[0] == 0
    code, _, _ = run(capsys, "check", env.board, env.out, *offline(env), "--fail-on", "yellow", "--junit",
                     env.tmp / "j.xml", "--checklist", env.tmp / "c.csv", "-q")
    assert code == 1
    assert ET.fromstring((env.tmp / "j.xml").read_text()).get("failures") == "1"
    rows = list(csv.DictReader((env.tmp / "c.csv").open()))
    assert {(r["category"], r["ref"]) for r in rows} >= {("not simulated", "R3"), ("bottom reading", "U2")}


def test_cpl_refuses_red_rows(env, capsys):
    env.make(two("D1", TOP, 115.0, 70.0, 0.0, "C1003", footprint="LED_SMD:LED_0603_1608Metric", half=0.7875))
    Path(env.out).write_text("old\n")
    code, out, _ = run(capsys, "cpl", env.board, "-o", env.out, *offline(env))
    assert code == 2 and "not written" in out and "RED     D1" in out
    assert Path(env.out).read_text() == "old\n"                         # left as it was
    assert not [p for p in env.tmp.iterdir() if p.name.startswith(".out.csv")]
    code, out, _ = run(capsys, "cpl", env.board, "-o", env.out, *offline(env), "--force")
    assert code == 2 and "red rows included (--force)" in out
    assert [r.ref for r in read_cpl(env.out)] == ["D1", "R1", "R2", "U1", "U2"]


def test_offline_check_from_a_committed_database(env, capsys):
    env.make()
    db = env.tmp / "db"
    code, out, _ = run(capsys, "fit", env.board, "--db", db, *offline(env))
    assert code == 0 and "4 parts: 0 red, 0 yellow, 4 green" in out
    stored = json.loads((db / "C1001.json").read_text())
    assert set(stored["entries"][0]) == {"ee", "kicad", "correction", "fit", "verdict", "reasons", "status",
                                         "verifications", "tool", "fitted"}
    assert "pads" not in json.dumps(stored)                               # numbers and hashes, no geometry
    run(capsys, "cpl", env.board, "-o", env.out, *offline(env))
    # no EasyEDA record at all: the KiCad pads through the stored correction stand in for them
    code, out, _ = run(capsys, "check", env.board, env.out, "--db", db, *offline(env, env.empty), "--json", "-")
    assert code == 0
    d = json.loads(out)
    assert {r["verdict"] for r in d["results"]} == {GREEN}
    assert all(r["messages"][0].startswith("simulated with the KiCad pads through the stored auto correction")
               and r["easyeda"]["proxy"] for r in d["results"])
    rows = read_cpl(env.out)
    next(r for r in rows if r.ref == "U1").rotation = 0.0
    write_cpl(env.out, rows)
    code, out, _ = run(capsys, "check", env.board, env.out, "--db", db, *offline(env, env.empty))
    assert code == 2 and "RED     U1" in out and "wrong-pad" in out
    # without the database nothing can be simulated: the SOT-23-6 parts are red, the resistors yellow
    code, out, _ = run(capsys, "check", env.board, env.out, *offline(env, env.empty), "--json", "-")
    d = json.loads(out)
    verdicts = {r["ref"]: r["verdict"] for r in d["results"]}
    assert code == 2 and verdicts == {"R1": YELLOW, "R2": YELLOW, "U1": RED, "U2": RED}


def test_confirm_presses_and_show(env, capsys):
    board = env.make()
    db = env.tmp / "db"
    run(capsys, "cpl", env.board, "-o", env.out, *offline(env))
    code, out, _ = run(capsys, "confirm", env.board, "--ref", "U1", "--db", db, "--by", "me", "--source", "preview",
                       *offline(env))
    assert code == 0 and out.startswith("U1 C1001: preview-verified, verified: rot 180")
    code, out, _ = run(capsys, "check", env.board, env.out, "--db", db, *offline(env), "--json", "-")
    assert {r["ref"]: r["verdict"] for r in json.loads(out)["results"]}["U1"] == VERIFIED
    # one space press in the preview turns the part 90 degrees counter-clockwise: rot 180 -> 270
    code, out, _ = run(capsys, "confirm", env.board, "--ref", "U1", "--db", db, "--by", "me", "--source", "preview",
                       "--space", "1", *offline(env))
    assert code == 0 and "rot 270" in out
    code, out, _ = run(capsys, "show", env.board, "--ref", "U1", "--db", db, *offline(env), "--json", "-")
    d = json.loads(out)
    assert d["correction"]["source"] == "db" and d["correction"]["rot"] == 270.0
    assert d["rows"]["jlc-2022"]["rotation"] == 270.0 and d["pad_hash"] == geom.pad_hash(board.part("U1").pads)
    code, out, _ = run(capsys, "show", "C1001", "--db", db, *offline(env))
    assert code == 0 and "title: TEST-sot" in out and "status=\"preview-verified\"" in out
    # a part EasyEDA does not have: the correction is typed in
    env.make(two("R3", TOP, 112.0, 70.0, 0.0, "C1005"))
    code, out, err = run(capsys, "confirm", env.board, "--ref", "R3", "--db", db, "--by", "me", "--source", "manual",
                         *offline(env))
    assert code == 3 and "give one with --rot" in err
    code, out, _ = run(capsys, "confirm", env.board, "--ref", "R3", "--db", db, "--by", "me", "--source", "manual",
                       "--rot", "0", *offline(env))
    assert code == 0 and out.startswith("R3 C1005: manual, yellow: rot 0")


def test_bad_input_is_exit_3(env, capsys):
    env.make()
    bad = env.tmp / "bad.csv"
    bad.write_text("a,b,c\n1,2,3\n")
    assert run(capsys, "check", env.tmp / "nope.kicad_pcb", bad, *offline(env))[0] == 3
    assert run(capsys, "check", env.board, env.tmp / "nope.csv", *offline(env))[0] == 3
    code, _, err = run(capsys, "check", env.board, bad, *offline(env))
    assert code == 3 and "no header with Designator" in err
    assert run(capsys, "check", env.board, bad, "--reading", "sideways")[0] == 3
    assert run(capsys, "frobnicate")[0] == 3
    assert run(capsys, "show", "not-a-code", *offline(env))[0] == 3
    assert run(capsys, "check", env.board, bad, "--origin", "nowhere", *offline(env))[0] == 3
    assert run(capsys, "check", env.board, bad, "--json", "-", "--github-annotations", *offline(env))[0] == 3
    assert run(capsys, "--version")[0] == 0


def test_fetch_uses_the_cache(env, capsys):
    env.make()
    code, out, _ = run(capsys, "fetch", "C1001", "c1003", env.board, "--cache", env.cache)
    assert code == 0
    assert out.splitlines()[:3] == ["C1001: TEST-sot, 6 pads (cached)",
                                    "C1003: not found (Component not found; cached)",
                                    "C1002: TEST-r0402, 2 pads (cached)"]


# ------------------------------------------------------------------------------------------------------- pipeline

def test_proxy_footprint_reproduces_the_correction():
    for layer, angle, corr in ((TOP, 0.0, Correction(180.0, 0.1, -0.2)), (BOTTOM, 37.0, Correction(90.0, 0.0, 0.3)),
                               (TOP, 210.0, Correction(33.0, -0.05, 0.0))):
        part = sot("U9", layer, 150.0, 50.0, angle)
        ee = proxy_footprint(part, corr)
        x, y, a = cv.cpl_row_for(part, corr, ORIGIN)
        res = check_row(part, CplRow("U9", x, y, layer, a), ee, ORIGIN, corr=corr, alt_readings=False)
        assert (res.status, res.verdict) == ("ok", GREEN)
        assert [(lnd.ee_number, lnd.kicad_number) for lnd in res.landings] == [(n, n) for n, _, _ in SOT]
        assert max(lnd.dist for lnd in res.landings) < 1e-6
        for p, e in zip(part.pads, ee.pads):            # outlines go through the correction too
            ring = [cv.ee_to_library(corr, q) for q in geom.pad_outline(e)]
            assert max(min(abs(q[0] - r[0]) + abs(q[1] - r[1]) for r in ring) for q in geom.pad_outline(p)) < 1e-6


def test_stale_database_entries_are_refitted(tmp_path):
    part = sot("U1", TOP, 120.0, 80.0, 0.0)
    ee = EEFootprint("C1001", "SOT", uuid="new", utime=2, pads=proxy_footprint(part, Correction(180.0)).pads)
    db = CorrectionDB(tmp_path)
    h = geom.pad_hash(part.copper_pads())
    db.upsert("C1001", Correction(180.0, verdict=GREEN, ee_key="old:1", kicad_hash=h))
    db.confirm("C1001", ee_key="old:1", kicad_hash=h, by="me", source="board")
    corr, source = correction(part, ee, db)
    assert source == "fit" and corr.verdict == GREEN and corr.rot == 180.0
    assert corr.reasons[-1].endswith("the refit gives the same placement")
    db2 = CorrectionDB(tmp_path / "2")
    db2.upsert("C1001", Correction(0.0, verdict=GREEN, ee_key="old:1", kicad_hash=h))
    corr, _ = correction(part, ee, db2)
    assert corr.verdict == YELLOW and "the refit differs" in corr.reasons[-1]
    assert key_reasons(corr, 1)[0] == corr.reasons[-1]
    # an entry for the current EasyEDA footprint is used as it is; the key is the footprint's pads, not its update
    # time, so a record EasyEDA touched without moving a pad keeps its entries
    db2.upsert("C1001", Correction(180.0, 0.01, verdict=YELLOW, ee_key=ee_key(ee), kicad_hash=h))
    corr, source = correction(part, ee, db2)
    assert (source, corr.dx, corr.verdict) == ("db", 0.01, YELLOW)
    touched = EEFootprint("C1001", "SOT", uuid="new", utime=3, pads=ee.pads)
    assert correction(part, touched, db2)[1] == "db" and touched.key() != ee.key()


def test_bom_codes_and_rows_the_bom_leaves_out():
    parts = [two("R1", TOP, 110.0, 95.0, 0.0, ""), sot("U1", TOP, 120.0, 80.0, 0.0), sot("U5", TOP, 125.0, 80.0, 0.0)]
    parts[1].lcsc = parts[2].lcsc = ""
    board = Board("b", ORIGIN, parts)
    assert apply_bom_codes(board, [{"designators": ["R1"], "lcsc": "C1002"}, {"designators": ["U1"], "lcsc": "x"}]) \
        == ["R1"]
    assert (board.part("R1").lcsc, board.part("R1").lcsc_field) == ("C1002", "BOM")

    class Client:
        def get(self, code):
            return NotFound(code, "not here")

    rows = [CplRow("U1", 20.0, 20.0, TOP, 0.0), CplRow("U5", 25.0, 20.0, TOP, 0.0)]
    run_ = check(board, rows, resolve(board, Client()), bom_rows=[{"designators": ["U5"], "lcsc": ""}])
    got = {r.ref: (r.status, r.verdict) for r in run_.results}
    # U1: no code and no BOM line, so JLC does not place it; U5 is in the BOM (code typed in at JLC?) and polar
    assert got == {"U1": ("not-simulated", YELLOW), "U5": ("not-simulated", RED), "R1": ("no-row", RED)}
    assert run_.results[0].messages[-1] == "not in the BOM: JLC does not place it"
    assert "no correction to make a row from (not here)" in run_.results[2].messages


# ------------------------------------------------------------------------------------------------------- reports

class _Tags(HTMLParser):
    VOID = {"meta", "br", "img", "input", "link", "hr", "path", "circle"}

    def __init__(self):
        super().__init__()
        self.stack, self.bad, self.external = [], [], []

    def handle_starttag(self, tag, attrs):
        for k, v in attrs:
            if k in ("src", "href") and v and not v.startswith("#"):
                self.external.append(v)
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.stack.pop()

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.bad.append(tag)
        else:
            self.stack.pop()


def test_html_report_is_self_contained_and_well_formed(env):
    board = env.make(two("D1", TOP, 115.0, 70.0, 0.0, "C1003", footprint="LED_SMD:LED_0603_1608Metric"))

    class Client:
        def get(self, code):
            from jlcplace.easyeda import from_response
            return from_response(code, RECORDS[code])

    resolved = resolve(board, Client(), use_fallback=True)
    rows = [CplRow(r, *cv.cpl_row_for(board.part(r), resolved[r].corr, ORIGIN)[:2], board.part(r).layer,
                   cv.cpl_row_for(board.part(r), resolved[r].corr, ORIGIN)[2]) for r in ("U1", "U2", "R1", "D1")]
    rows[0].rotation = 0.0
    rows.append(CplRow("Q9", 0.0, 0.0, TOP, 0.0))
    run_ = check(board, rows, resolved)
    page = html(run_)
    p = _Tags()
    p.feed(page)
    assert p.external == [] and p.bad == [] and p.stack == []
    assert "<script" not in page and "@import" not in page and "url(" not in page
    order = [page.index(f"<b>{r}</b>") for r in ("D1", "Q9", "R2", "U1", "R1", "U2")]
    assert order == sorted(order)                       # red (D1, Q9 not on the board, R2 no row, U1), then green
    assert "seen from below" in page and 'class="ee other"' in page    # U1's pads land on other numbers
    assert isinstance(run_, Run) and run_.exit_code() == 2


def test_annotations_escape_and_junit_counts():
    assert _gh("50%: a,b\nc", prop=True) == "50%25%3A a%2Cb%0Ac"
    assert _gh("50%: a,b\nc") == "50%25: a,b%0Ac"
    part = two("R1", TOP, 110.0, 95.0, 0.0, "C1002")
    res = [check_row(part, CplRow("R1", 10.5, 5.0, TOP, 0.0), None, ORIGIN)]
    run_ = Run(Board("b.kicad_pcb", ORIGIN, [part]), [res[0].row], res, {}, cpl_path="x.csv", row_lines=[2])
    assert res[0].verdict == YELLOW
    assert github_annotations(run_) == ["::warning file=x.csv,line=2,title=jlcplace R1 not-simulated::placement not "
                                        "simulated: no EasyEDA footprint"]
    assert github_annotations(run_, fail_on=YELLOW)[0].startswith("::error ")
    assert ET.fromstring(junit(run_)).get("failures") == "0"
    assert ET.fromstring(junit(run_, fail_on=YELLOW)).get("failures") == "1"
