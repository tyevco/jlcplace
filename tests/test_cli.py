"""The command line, the pipeline and the reports on a synthetic board with hand-made EasyEDA records: no KiCad, no
EasyEDA data and no network needed."""
import csv
from html.parser import HTMLParser
import json
import os
import random
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
    assert any(ln.startswith("RED     R1") and "land off copper" in ln for ln in lines)
    ann = [ln for ln in lines if ln.startswith("::")]
    assert len(ann) == 2
    assert ann[1].startswith(f"::error file={_gh(env.out, True)},line=4,title=jlcplace U1 wrong-pad::")
    assert ann[0].startswith(f"::error file={_gh(env.out, True)},line=2,title=jlcplace R1 off-copper::")


def test_fail_on_yellow_and_reports(env, capsys):
    env.make(two("R3", TOP, 112.0, 70.0, 0.0, "C1005"))      # EasyEDA has no such component: a resistor, yellow
    code, out, _ = run(capsys, "cpl", env.board, "-o", env.out, *offline(env))
    assert code == 0 and "YELLOW  R3" in out and "placement not simulated" in out
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
    # the check's own findings come first; the note on how it was simulated last (cli-7)
    assert all(r["messages"][-1].startswith("simulated with the KiCad pads through the stored auto correction: ")
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
                       "--cpl", env.out, *offline(env))
    assert code == 0 and out.startswith("U1 C1001: preview-verified, verified: rot 180")
    code, out, _ = run(capsys, "check", env.board, env.out, "--db", db, *offline(env), "--json", "-")
    assert {r["ref"]: r["verdict"] for r in json.loads(out)["results"]}["U1"] == VERIFIED
    # one space press in the preview turns the part 90 degrees counter-clockwise: rot 180 -> 270; a verified entry is
    # given other numbers only once its own verification is withdrawn
    confirm = ["confirm", env.board, "--ref", "U1", "--db", db, "--by", "me", "--source", "preview", "--cpl", env.out]
    code, out, err = run(capsys, *confirm, "--space", "1", *offline(env))
    assert code == 3 and "preview-verified with other numbers" in err, out + err
    assert run(capsys, *confirm, "--failed", *offline(env))[0] == 0
    code, out, _ = run(capsys, *confirm, "--space", "1", *offline(env))
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
    assert code == 3 and "or the correction (--rot/--dx/--dy)" in err
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
        == [("R1", "", "C1002")]
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
                                        "simulated: no EasyEDA footprint; a two-pad non-polar part, and the row "
                                        "point is on it"]
    assert github_annotations(run_, fail_on=YELLOW)[0].startswith("::error ")
    assert ET.fromstring(junit(run_)).get("failures") == "0"
    assert ET.fromstring(junit(run_, fail_on=YELLOW)).get("failures") == "1"


# ------------------------------------------------------------------------------------------------------- review

def record2(pads, *, names=None, tags=None, plus=(), title="", uuid="fp2"):
    """record() with symbol pin names {number: name}, part categories and "+" marks [(x, y, size)] (ee frame, mm)
    drawn as two silkscreen tracks."""
    rec = record(pads, uuid=uuid)
    res = rec["result"]
    res["dataStr"]["shape"] = [
        f"P~show~0~{n}~0~0~0~gge{n}~0^^0~0^^M 0 0 h10~#000000^^1~0~0~0~{name}~start~~~#000000^^"
        f"1~0~0~0~{n}~end~~~#000000^^0~0~0^^0~M 0 0" for n, name in (names or {}).items()]
    if tags is not None:
        res["tags"] = list(tags)
    if title:
        res["packageDetail"]["title"] = title
    for k, (x, y, s) in enumerate(plus):
        px, py, h = HX + x / 0.254, HY + y / 0.254, s / 2 / 0.254
        res["packageDetail"]["dataStr"]["shape"] += [
            f"TRACK~0.6~3~~{px - h:.4f} {py:.4f} {px + h:.4f} {py:.4f}~ggp{k}h~0",
            f"TRACK~0.6~3~~{px:.4f} {py - h:.4f} {px:.4f} {py + h:.4f}~ggp{k}v~0"]
    return rec


def diode(ref, x, y, code, names=("K", "A"), footprint="Diode_SMD:D_SOD-123", numbers=("1", "2"), layer=TOP,
          angle=0.0, nets=None, half=1.65):
    nets = nets or (f"{ref}_1", f"{ref}_2")
    return Part(ref, layer, x, y, angle, footprint=footprint, lcsc=code,
                pads=[Pad(numbers[0], -half, 0.0, 0.9, 1.2, net=nets[0], pin_name=names[0]),
                      Pad(numbers[1], half, 0.0, 0.9, 1.2, net=nets[1], pin_name=names[1])])


def write_records(env, **records):
    for code, rec in records.items():
        (Path(env.cache) / f"{code}.json").write_text(json.dumps(rec))


SOD = [("1", -1.6, 0.0, 0.9, 1.2, 0), ("2", 1.6, 0.0, 0.9, 1.2, 0)]


def test_database_entries_follow_the_pin_functions(env, capsys):
    """truth-1: D4 and D5, one code on one footprint, pad 1 the cathode on D4 and the anode on D5 (EasyEDA: pad 1 =
    K, + drawn beside its anode). One database entry for both let D5's correction replace D4's: `cpl --db` wrote D4
    reversed, and confirming D5 vouched for it."""
    write_records(env, C2001=record2(SOD, names={"1": "K", "2": "A"}, plus=[(2.4, 0.0, 0.5)], uuid="sod"))
    env.make(diode("D4", 115.0, 70.0, "C2001", names=("K", "A")), diode("D5", 115.0, 60.0, "C2001", names=("A", "K")))
    db = env.tmp / "db"
    code, out, _ = run(capsys, "fit", env.board, "--db", db, *offline(env), "--ref", "D4", "--ref", "D5")
    assert code == 0 and out.count("[stored]") == 2, out
    assert len(json.loads((db / "C2001.json").read_text())["entries"]) == 2
    code, out, _ = run(capsys, "cpl", env.board, "-o", env.out, "--db", db, *offline(env))
    rows = {r.ref: r.rotation for r in read_cpl(env.out)}
    assert code == 0 and (rows["D4"], rows["D5"]) == (0.0, 180.0), out
    # D5 checked in JLC's preview: D4's entry is not verified by it, and D4 turned to D5's angle is red
    run(capsys, "confirm", env.board, "--ref", "D5", "--db", db, "--by", "me", "--source", "preview", "--cpl",
        env.out, *offline(env))
    statuses = sorted(e["status"] for e in json.loads((db / "C2001.json").read_text())["entries"])
    assert statuses == ["auto", "preview-verified"]
    rows = read_cpl(env.out)
    next(r for r in rows if r.ref == "D4").rotation = 180.0
    write_cpl(env.out, rows)
    code, out, _ = run(capsys, "check", env.board, env.out, "--db", db, *offline(env), "--json", "-")
    got = {r["ref"]: r["verdict"] for r in json.loads(out)["results"]}
    assert code == 2 and got["D4"] == RED and got["D5"] == VERIFIED
    # one database for two boards: a board with only D5 gets D5's own correction, not D4's
    db2 = env.tmp / "db2"
    env.make(diode("D4", 115.0, 70.0, "C2001", names=("K", "A")))
    run(capsys, "fit", env.board, "--db", db2, *offline(env), "--ref", "D4")
    env.make(diode("D5", 115.0, 60.0, "C2001", names=("A", "K")))
    code, out, _ = run(capsys, "cpl", env.board, "-o", env.out, "--db", db2, *offline(env))
    assert code == 0 and {r.ref: r.rotation for r in read_cpl(env.out)}["D5"] == 180.0, out


def test_fit_does_not_let_a_second_part_replace_the_first_ones_entry(env, capsys, monkeypatch):
    """truth-1: should two parts with one database key (code, EasyEDA footprint, KiCad pads and pin functions) ever
    fit differently, the second must not replace the first one's entry behind its back."""
    import dataclasses

    from jlcplace import cli
    real = cli.fit_part

    def fit_part(part, ee, **kw):                     # R2 fits otherwise than R1 (same code, same pads)
        c = real(part, ee, **kw)
        return dataclasses.replace(c, rot=180.0) if part.ref == "R2" else c

    monkeypatch.setattr(cli, "fit_part", fit_part)
    env.make()
    db = env.tmp / "db"
    code, out, _ = run(capsys, "fit", env.board, "--db", db, *offline(env))
    r2 = next(ln for ln in out.splitlines() if " R2 " in ln)
    assert "not stored: R1 has the same code and footprint and fits rot 0" in r2, out
    entries = json.loads((db / "C1002.json").read_text())["entries"]
    assert [e["correction"]["rot"] for e in entries] == [0.0]


def test_the_bom_code_is_the_part_jlc_places(env, capsys):
    """cli-2: JLC assembles from the BOM's LCSC column. The BOM puts C1006 (the same SOT-23-6 drawn without the half
    turn) where the board says C1001: rows for the board's code put C1006 half a turn off."""
    write_records(env, C1006=record([(n, x, y, 1.3, 0.6, 0) for n, x, y in SOT], uuid="sot0"))
    env.make()
    run(capsys, "cpl", env.board, "-o", env.out, *offline(env))
    bom = env.tmp / "bom.csv"
    bom.write_text("Comment,Designator,Footprint,LCSC\nSOT,\"U1,U2\",SOT-23-6,C1006\nR,\"R1,R2\",0402,C1002\n")
    code, out, _ = run(capsys, "check", env.board, env.out, "--bom", bom, *offline(env), "--json", env.tmp / "r.json")
    res = {r["ref"]: r for r in json.loads((env.tmp / "r.json").read_text())["results"]}
    assert code == 2 and res["U1"]["lcsc"] == "C1006" and res["U1"]["verdict"] == RED
    assert "U1: JLC places C1006 from the BOM; the board says C1001 (checked with C1006)" in out
    # cpl with the BOM writes rows for the BOM's code
    code, out, _ = run(capsys, "cpl", env.board, "-o", env.out, "--bom", bom, *offline(env))
    assert code == 0 and {r.ref: r.rotation for r in read_cpl(env.out)}["U1"] == 0.0, out
    code, _, _ = run(capsys, "check", env.board, env.out, "--bom", bom, *offline(env))
    assert code == 0


def test_cpl_refuses_rows_nothing_vouches_for(env, capsys):
    """logic-1: KiCad numbers the diode K / A, EasyEDA 1 / 2 with no telling pin names: the fit is red
    ("ambiguous"), the row written from it was green and `cpl` kept the file."""
    write_records(env, C2002=record2(SOD, names={"1": "1", "2": "2"}, uuid="ka"))
    env.make(diode("D21", 115.0, 70.0, "C2002", names=("", ""), numbers=("K", "A")))
    code, out, _ = run(capsys, "cpl", env.board, "-o", env.out, *offline(env))
    assert code == 2 and "not written" in out and "RED     D21" in out and "nothing vouches" in out, out
    assert not Path(env.out).exists()


def test_rows_of_doubtful_corrections_are_yellow(env, capsys):
    """ee-2: an LED whose polarity only the pad numbers decide (no KiCad pin functions, no + drawn): the row
    jlcplace writes was green and `--fail-on yellow` passed it."""
    write_records(env, C2286=record2([("2", -0.75, 0.0, 0.8, 0.8, 0), ("1", 0.75, 0.0, 0.8, 0.8, 0)],
                                     names={"1": "1", "2": "2"}, uuid="led"))
    env.make(diode("LED1", 110.0, 75.0, "C2286", names=("", ""), footprint="LED_SMD:LED_0603_1608Metric",
                   half=0.7875))
    code, out, _ = run(capsys, "cpl", env.board, "-o", env.out, *offline(env), "--fail-on", "yellow")
    assert code == 1 and "YELLOW  LED1" in out and "fit was unsure" in out, out
    rows = {r.ref: r.rotation for r in read_cpl(env.out)}
    assert rows["LED1"] == 180.0                               # by the numbers: EasyEDA's pad 1 on KiCad's 1


def test_names_against_numbers_wait_for_a_decision(env, capsys):
    """truth-3 / geo-4: D12, EasyEDA's symbol names its pad 1 A, its footprint draws the + beside pad 2, KiCad's
    pad 1 is K. JLC's preview went with the + (and the numbers); jlcplace went with the names and wrote it reversed.
    Without a + nothing settles it: red until confirmed."""
    pads = [("1", -2.0, 0.0, 1.5, 1.5, 0), ("2", 2.0, 0.0, 1.5, 1.5, 0)]
    write_records(env, C2836=record2(pads, names={"1": "A", "2": "K"}, uuid="sma"),
                  C2837=record2(pads, names={"1": "A", "2": "K"}, plus=[(3.0, 0.0, 0.6)], uuid="sma+"))
    env.make(diode("D12", 115.0, 70.0, "C2837", footprint="Diode_SMD:D_SMA", half=2.0))
    code, out, _ = run(capsys, "cpl", env.board, "-o", env.out, *offline(env))
    assert code == 0 and {r.ref: r.rotation for r in read_cpl(env.out)}["D12"] == 0.0, out
    env.make(diode("D12", 115.0, 70.0, "C2836", footprint="Diode_SMD:D_SMA", half=2.0))
    code, out, _ = run(capsys, "cpl", env.board, "-o", env.out, *offline(env))
    assert code == 2 and "polarity undecided" in out and "jlcplace confirm" in out, out
    db = env.tmp / "db"
    code, out, _ = run(capsys, "confirm", env.board, "--ref", "D12", "--db", db, "--by", "me", "--source", "preview",
                       "--rot", "0", *offline(env))
    assert code == 0, out
    code, out, _ = run(capsys, "cpl", env.board, "-o", env.out, "--db", db, *offline(env))
    assert code == 0 and {r.ref: r.rotation for r in read_cpl(env.out)}["D12"] == 0.0, out


def test_offline_summary_keeps_the_finding_first(env, capsys):
    """cli-7: simulated from the database without records, the summary and JUnit showed only "simulated with the
    KiCad pads through ..." and a capped verdict gave no reason."""
    # R3's EasyEDA pads sit 0.17 mm further out than KiCad's: a yellow fit (rms), a green row with the record
    write_records(env, C2003=record([("1", -0.65, 0.0, 0.6, 0.6, 0), ("2", 0.65, 0.0, 0.6, 0.6, 0)], uuid="r3"))
    env.make(two("R3", TOP, 112.0, 70.0, 0.0, "C2003"))
    db = env.tmp / "db"
    code, out, _ = run(capsys, "fit", env.board, "--db", db, *offline(env))
    assert "YELLOW  R3" in out and "rms 0.170" in out, out
    run(capsys, "cpl", env.board, "-o", env.out, *offline(env))
    rows = read_cpl(env.out)
    next(r for r in rows if r.ref == "R1").x += 0.5                    # off its pads
    write_cpl(env.out, rows)
    code, out, _ = run(capsys, "check", env.board, env.out, "--db", db, *offline(env, env.empty), "--junit",
                       env.tmp / "j.xml")
    lines = {ln.split()[1]: ln for ln in out.splitlines() if ln.startswith(("RED", "YELLOW"))}
    assert code == 2 and "land off copper" in lines["R1"], out
    assert "because the stored auto correction is yellow: rms 0.170" in lines["R3"], out
    failure = ET.fromstring((env.tmp / "j.xml").read_text()).find(".//failure")
    assert "land off copper" in failure.get("message")


def test_hint_when_the_codes_live_in_the_bom(env, capsys):
    """cli-8: every row 'the board part has no LCSC code' and nothing said --bom."""
    board = env.make()
    for p in board.parts:
        p.lcsc = ""
    save_snapshot(board, env.board)
    rows = [CplRow(p.ref, 0.0, 0.0, p.layer, 0.0) for p in board.parts if p.pads]
    write_cpl(env.out, rows)
    code, out, _ = run(capsys, "check", env.board, env.out, *offline(env))
    assert out.count("pass --bom BOM.csv") == 1 and "--lcsc-field" in out, out


def test_messages_read_as_english(env, capsys):
    """cli-11: '1 codes: 1 footprints', '1 BOM issues', 'rot 0 also land', 'KiCad pads no EasyEDA pad lands on',
    'off-copper: ... land off copper'."""
    env.make()
    code, out, _ = run(capsys, "fetch", "C1001", "--cache", env.cache)
    assert "1 code: 1 footprint, 0 not found" in out
    run(capsys, "cpl", env.board, "-o", env.out, *offline(env))
    rows = read_cpl(env.out)
    next(r for r in rows if r.ref == "R1").x += 0.5
    write_cpl(env.out, rows)
    bom = env.tmp / "bom.csv"
    bom.write_text("Comment,Designator,Footprint,LCSC\nSOT,\"U1,U2\",SOT-23-6,C1001\nR,\"R1,R2,R9\",0402,C1002\n")
    code, out, _ = run(capsys, "check", env.board, env.out, "--bom", bom, *offline(env))
    assert "; 1 BOM issue" in out and "BOM issues" not in out, out
    r1 = next(ln for ln in out.splitlines() if ln.startswith("RED     R1"))
    assert "off-copper:" not in r1 and "land off copper" in r1, r1


def test_readme_says_what_the_database_keeps():
    """lic-2: database entries keep the EasyEDA footprint's title (package dimensions) for people reading them."""
    text = (Path(__file__).resolve().parents[1] / "README.md").read_text()
    assert "hold only\nderived numbers, pad numbers and hashes" not in text and "footprint's title" in text
    db = CorrectionDB(Path(os.environ.get("TMPDIR", "/tmp")) / "jlcplace-lic2-unused")
    assert db.file("C1").name == "C1.json"


def test_offset_rows_show_in_the_summary(env, capsys):
    """truth-4 / logic-4: a row 0.5 mm along its pads from where the correction puts the part (every pad centre still
    on its own pad) was green, its 'differs from the row jlcplace writes' only in the JSON."""
    env.make()
    run(capsys, "cpl", env.board, "-o", env.out, *offline(env))
    rows = read_cpl(env.out)
    next(r for r in rows if r.ref == "U1").x += 0.5
    write_cpl(env.out, rows)
    code, out, _ = run(capsys, "check", env.board, env.out, *offline(env), "--fail-on", "yellow",
                       "--github-annotations")
    line = next(ln for ln in out.splitlines() if ln.startswith("YELLOW  U1"))
    assert code == 1 and "offset: differs from the row jlcplace writes" in line and "0.500 mm" in line, out
    assert any(ln.startswith("::error ") and "jlcplace U1 offset" in ln for ln in out.splitlines())


def test_a_part_the_bom_lists_is_checked_though_the_board_leaves_it_out_of_its_bom(env, capsys):
    """logic-10: D1 is excluded from the board's BOM, but the JLC BOM lists it, so JLC places it; its row is
    reversed."""
    write_records(env, C2001=record2(SOD, names={"1": "K", "2": "A"}, uuid="sod"))
    d1 = diode("D1", 115.0, 70.0, "C2001")
    d1.exclude_bom = True
    board = env.make(d1)
    x, y, a = cv.cpl_row_for(board.part("D1"), Correction(180.0), ORIGIN)      # D1 turned half a turn
    write_cpl(env.out, [CplRow("D1", x, y, TOP, a)])
    bom = env.tmp / "bom.csv"
    bom.write_text("Comment,Designator,Footprint,LCSC\nD,D1,SOD-123,C2001\n")
    code, out, _ = run(capsys, "check", env.board, env.out, *offline(env), "--json", env.tmp / "a.json")
    assert {r["ref"]: r["status"] for r in json.loads((env.tmp / "a.json").read_text())["results"]}["D1"] \
        == "not-placed"
    code, out, _ = run(capsys, "check", env.board, env.out, "--bom", bom, *offline(env), "--json", env.tmp / "b.json")
    d = {r["ref"]: r for r in json.loads((env.tmp / "b.json").read_text())["results"]}["D1"]
    assert code == 2 and (d["status"], d["verdict"]) == ("wrong-pad", RED), d
    assert "the BOM lists it: JLC will place it" in d["messages"][-1]


def test_checklist_puts_the_placements_in_doubt_first(env, capsys):
    """truth-5: 'uncertain correction' items in plain ref order hid the one wrong fit among the right ones; the ones
    whose choice of placement is in doubt (a polarity, an orientation) now come first."""
    write_records(env, C2286=record2([("2", -0.75, 0.0, 0.8, 0.8, 0), ("1", 0.75, 0.0, 0.8, 0.8, 0)],
                                     names={"1": "1", "2": "2"}, uuid="led"),
                  C2003=record([("1", -0.65, 0.0, 0.6, 0.6, 0), ("2", 0.65, 0.0, 0.6, 0.6, 0)], uuid="r3"))
    env.make(two("C0", TOP, 112.0, 70.0, 0.0, "C2003", footprint="Capacitor_SMD:C_0402_1005Metric"),
             diode("LED9", 110.0, 75.0, "C2286", names=("", ""), footprint="LED_SMD:LED_0603_1608Metric", half=0.7875))
    run(capsys, "cpl", env.board, "-o", env.out, *offline(env))
    code, out, _ = run(capsys, "check", env.board, env.out, *offline(env), "--json", "-")
    unsure = [i["ref"] for i in json.loads(out)["checklist"] if i["category"] == "uncertain correction"]
    assert unsure == ["LED9", "C0"], unsure


# ------------------------------------------------------------------------------------------------------- confirm

def rows_with(board, **rots):
    """CPL rows made from the correction rot given per ref, at KiCad's anchor (as another tool writes them)."""
    out = []
    for ref, r in rots.items():
        part = board.part(ref)
        x, y, a = cv.cpl_row_for(part, Correction(r), ORIGIN)
        out.append(CplRow(ref, x, y, part.layer, a))
    return out


def entry_rots(db, code="C1001"):
    entries = json.loads((Path(db) / f"{code}.json").read_text())["entries"]
    return [(e["correction"]["rot"], e["status"]) for e in entries]


def test_presses_are_relative_to_the_row_that_was_uploaded(env, capsys):
    """geo-1: another tool's CPL put U1 at KiCad's own angle (rot 0); JLC's preview needed two space presses. confirm
    added them to jlcplace's own fit (rot 180) and stored rot 0 as preview-verified: from then on the right row was
    red and the wrong one verified."""
    board = env.make()
    db, raw = env.tmp / "db", env.tmp / "raw.csv"
    write_cpl(raw, rows_with(board, U1=0.0))
    code, out, err = run(capsys, "confirm", env.board, "--ref", "U1", "--db", db, "--by", "me", "--source", "preview",
                         "--cpl", raw, "--space", "2", *offline(env))
    assert code == 0 and out.startswith("U1 C1001: preview-verified, verified: rot 180"), out + err
    assert entry_rots(db) == [(180.0, "preview-verified")]
    v = json.loads((db / "C1001.json").read_text())["entries"][0]["verifications"][-1]
    assert v["presses"] == {"space": 2, "right": 0, "up": 0}
    # the right row (jlcplace's) is verified, the uploaded one is red
    run(capsys, "cpl", env.board, "-o", env.out, "--db", db, *offline(env))
    code, out, _ = run(capsys, "check", env.board, env.out, "--db", db, *offline(env), "--json", "-")
    assert {r["ref"]: r["verdict"] for r in json.loads(out)["results"]}["U1"] == VERIFIED
    write_cpl(raw, rows_with(board, U1=0.0))
    code, out, _ = run(capsys, "check", env.board, raw, "--db", db, *offline(env), "--json", "-")
    assert {r["ref"]: r["verdict"] for r in json.loads(out)["results"]}["U1"] == RED
    # an arrow press moves the row point (the preview's screen: the cpl frame on the top)
    db2 = env.tmp / "db2"
    code, out, _ = run(capsys, "confirm", env.board, "--ref", "U1", "--db", db2, "--by", "me", "--source",
                       "preview", "--cpl", env.out, "--right", "2", *offline(env))
    x0, y0, _ = cv.cpl_row_for(board.part("U1"), Correction(180.0), ORIGIN)
    assert code == 0 and f"row X {x0 + 2 * 0.0635:.4f} Y {y0:.4f} rot 180" in out, out


def test_presses_on_parts_sharing_an_entry_are_not_added_twice(env, capsys):
    """geo-1: `confirm --ref U1 --ref U2 --space 2` (one entry: one code, one footprint) added the 180 degrees once
    per ref, so they cancelled and the entry ended where it was, marked preview-verified."""
    env.make()
    db = env.tmp / "db"
    run(capsys, "cpl", env.board, "-o", env.out, *offline(env))
    code, out, err = run(capsys, "confirm", env.board, "--ref", "U1", "--ref", "U2", "--db", db, "--by", "me",
                         "--source", "preview", "--cpl", env.out, "--space", "2", *offline(env))
    lines = out.splitlines()
    assert code == 0 and lines[0].startswith("U1 C1001: preview-verified, verified: rot 0"), out + err
    assert lines[1].startswith("U2 C1001: preview-verified, verified: rot 0"), out
    assert entry_rots(db) == [(0.0, "preview-verified")]
    # rows that give one entry two corrections: refused, nothing stored
    board = env.make()
    write_cpl(env.out, rows_with(board, U1=180.0, U2=0.0))
    db2 = env.tmp / "db2"
    code, out, err = run(capsys, "confirm", env.board, "--ref", "U1", "--ref", "U2", "--db", db2, "--by", "me",
                         "--source", "preview", "--cpl", env.out, *offline(env))
    assert code == 3 and "U1 and U2 share" in err and not (db2 / "C1001.json").exists(), out + err


def test_a_preview_confirmation_records_the_uploaded_row(env, capsys):
    """geo-1: with no presses, confirm verified jlcplace's own correction (rot 180) although the uploaded row, the one
    JLC's preview showed right, was another (rot 0)."""
    board = env.make()
    db, raw = env.tmp / "db", env.tmp / "raw.csv"
    write_cpl(raw, rows_with(board, U1=0.0))
    code, out, err = run(capsys, "confirm", env.board, "--ref", "U1", "--db", db, "--by", "me", "--source", "preview",
                         "--cpl", raw, *offline(env))
    assert code == 0 and out.startswith("U1 C1001: preview-verified, verified: rot 0"), out + err
    assert "replaces the auto correction rot 180" in out
    code, out, _ = run(capsys, "check", env.board, raw, "--db", db, *offline(env), "--json", "-")
    assert {r["ref"]: r["verdict"] for r in json.loads(out)["results"]}["U1"] == VERIFIED
    # a verified entry is not replaced by a verification of other numbers ...
    write_cpl(raw, rows_with(board, U1=90.0))
    code, out, err = run(capsys, "confirm", env.board, "--ref", "U1", "--db", db, "--by", "me", "--source", "preview",
                         "--cpl", raw, *offline(env))
    assert code == 3 and "preview-verified with other numbers" in err and entry_rots(db) == [(0.0, "preview-verified")]
    # ... and a failure of a row that is not the stored correction's says nothing about it
    code, out, err = run(capsys, "confirm", env.board, "--ref", "U1", "--db", db, "--by", "me", "--source", "preview",
                         "--cpl", raw, "--failed", *offline(env))
    assert code == 3 and "nothing recorded" in err and entry_rots(db) == [(0.0, "preview-verified")]
    # the failure of the stored correction's own row drops it; then the other numbers can be recorded
    write_cpl(raw, rows_with(board, U1=0.0))
    code, out, _ = run(capsys, "confirm", env.board, "--ref", "U1", "--db", db, "--by", "me", "--source", "preview",
                       "--cpl", raw, "--failed", *offline(env))
    assert code == 0 and entry_rots(db) == [(0.0, "auto")]
    write_cpl(raw, rows_with(board, U1=180.0))
    code, out, _ = run(capsys, "confirm", env.board, "--ref", "U1", "--db", db, "--by", "me", "--source", "preview",
                       "--cpl", raw, *offline(env))
    assert code == 0 and entry_rots(db) == [(180.0, "preview-verified")]


def test_a_preview_or_board_confirmation_needs_the_uploaded_cpl(env, capsys):
    """geo-1: what JLC showed is the uploaded row, which need not be jlcplace's correction: preview and board
    confirmations, and presses, name the CPL."""
    env.make()
    db = env.tmp / "db"
    base = ["confirm", env.board, "--ref", "U1", "--db", db, "--by", "me", *offline(env)]
    for extra in (["--source", "preview"], ["--source", "board"], ["--source", "manual", "--space", "1"],
                  ["--source", "manual", "--cpl", env.out, "--rot", "0"],
                  ["--source", "preview", "--cpl", env.out, "--space", "1", "--failed"]):
        code, out, err = run(capsys, *base, *extra)
        assert code == 3 and ("--cpl" in err or "--failed" in err), (extra, out, err)
    assert not db.exists()
    code, out, err = run(capsys, *base, "--source", "preview", "--cpl", env.tmp / "nope.csv")
    assert code == 3 and "no such file" in err
    run(capsys, "cpl", env.board, "-o", env.out, *offline(env))
    rows = [r for r in read_cpl(env.out) if r.ref != "U1"]
    write_cpl(env.out, rows)
    code, out, err = run(capsys, *base, "--source", "preview", "--cpl", env.out)
    assert code == 3 and "no row for U1" in err, err


# ------------------------------------------------------------------------------------------------------- bad values

@pytest.mark.parametrize("field, value", [("Rotation", "nan"), ("Rotation", "inf"), ("Rotation", "Infinity"),
                                          ("Rotation", "1e400"), ("Mid X", "1e400mm"), ("Mid Y", "-1e999")])
def test_non_finite_cpl_values_are_bad_input(env, capsys, field, value):
    """cli-1: Rotation 'nan' (or inf, 1e400) and Mid X '1e400mm' were accepted: every EasyEDA pad was 'on its own
    number' at distance nan, the rows green and the exit code 0."""
    env.make()
    run(capsys, "cpl", env.board, "-o", env.out, *offline(env))
    with open(env.out, newline="") as f:
        rows = list(csv.DictReader(f))
    rows[1][field] = value
    bad = env.tmp / "bad.csv"
    with open(bad, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    code, out, err = run(capsys, "check", env.board, bad, *offline(env))
    assert code == 3 and "line 3" in err and "not a finite number" in err, (out, err)


def test_non_finite_origin_is_bad_input(env, capsys):
    """cli-1: `--origin nan,nan` put every row point at nan, and rect pads 'contained' it."""
    env.make()
    run(capsys, "cpl", env.board, "-o", env.out, *offline(env))
    for origin in ("nan,nan", "inf,0", "0,1e400"):
        code, out, err = run(capsys, "check", env.board, env.out, "--origin", origin, *offline(env))
        assert code == 3 and "not a finite number" in err, (origin, out, err)


def test_json_report_is_strict_json(env, capsys):
    """cli-1: a part with no copper pads lands its EasyEDA pads at distance inf, written as a bare Infinity token
    (not JSON); it is null now."""
    env.make(Part("U9", TOP, 125.0, 70.0, 0.0, footprint="X:none", lcsc="C1001"))
    run(capsys, "cpl", env.board, "-o", env.out, "--force", *offline(env))
    code, out, _ = run(capsys, "check", env.board, env.out, *offline(env), "--json", env.tmp / "r.json")

    def strict(token):
        raise ValueError(f"not JSON: {token}")

    d = json.loads((env.tmp / "r.json").read_text(), parse_constant=strict)
    u9 = next(r for r in d["results"] if r["ref"] == "U9")
    assert code == 2 and u9["verdict"] == RED and {lnd["dist"] for lnd in u9["landings"]} == {None}


# ------------------------------------------------------------------------------------------------------- odd files

def test_cr_only_and_utf16_cpls_read_like_the_original(env, capsys):
    """cli-4: Excel for Mac's 'CSV (Macintosh)' (CR line ends) and Excel's 'Unicode Text' (UTF-16 with a BOM) crashed
    with _csv.Error and exit 1."""
    env.make()
    run(capsys, "cpl", env.board, "-o", env.out, *offline(env))
    text = Path(env.out).read_bytes().decode("utf-8")              # CRLF, as written
    want = run(capsys, "check", env.board, env.out, *offline(env), "--json", "-")
    for name, data in (("cr.csv", text.replace("\r\n", "\r").encode()), ("utf16.csv", text.encode("utf-16")),
                       ("utf16be.csv", b"\xfe\xff" + text.encode("utf-16-be")),
                       ("tabs16.txt", text.replace(",", "\t").encode("utf-16"))):
        (env.tmp / name).write_bytes(data)
        assert [(r.ref, r.x, r.y, r.layer, r.rotation) for r in read_cpl(env.tmp / name)] == \
            [(r.ref, r.x, r.y, r.layer, r.rotation) for r in read_cpl(env.out)], name
        code, out, err = run(capsys, "check", env.board, env.tmp / name, *offline(env), "--json", "-")
        assert code == want[0] == 0 and json.loads(out)["results"] == json.loads(want[1])["results"], (name, err)


def test_garbage_cpl_bom_and_snapshot_are_bad_input(env, capsys):
    """cli-4: random bytes as the CPL or --bom crashed with _csv.Error, and a snapshot of {} or [] with
    KeyError/TypeError, all with a traceback and exit 1."""
    env.make()
    run(capsys, "cpl", env.board, "-o", env.out, *offline(env))
    garbage = env.tmp / "garbage.csv"
    garbage.write_bytes(bytes(random.Random(4).randrange(256) for _ in range(4096)))
    code, out, err = run(capsys, "check", env.board, garbage, *offline(env))
    assert code == 3 and "garbage.csv" in err, (out, err)
    code, out, err = run(capsys, "check", env.board, env.out, "--bom", garbage, *offline(env))
    assert code == 3 and "garbage.csv" in err, (out, err)
    for i, text in enumerate(("{}", "[]", '{"parts": 3}', '{"path": "b", "origin": [0, 0], "parts": [{"x": 1}]}',
                              '"board"')):
        snap = env.tmp / f"snap{i}.json"
        snap.write_text(text)
        code, out, err = run(capsys, "check", snap, env.out, *offline(env))
        assert code == 3 and "not a jlcplace snapshot" in err, (text, out, err)


def test_a_broken_database_file_is_bad_input(env, capsys):
    """sec-2: git conflict markers in a committed database file, or a hand-edited "rot": "x", crashed every command
    that read it with a traceback and exit 1 (the 'yellow' code)."""
    board = env.make()
    db = env.tmp / "db"
    run(capsys, "fit", env.board, "--db", db, *offline(env))
    run(capsys, "cpl", env.board, "-o", env.out, "--db", db, *offline(env))
    f = db / "C1001.json"
    good = f.read_text()
    entry = json.loads(good)["entries"][0]
    commands = [["check", env.board, env.out], ["cpl", env.board, "-o", env.tmp / "again.csv"], ["fit", env.board],
                ["confirm", env.board, "--ref", "U1", "--by", "me", "--source", "manual"],
                ["show", env.board, "--ref", "U1"], ["show", "C1001"]]
    for broken in ("<<<<<<< HEAD\n" + good + "=======\n" + good + ">>>>>>> other\n",
                   json.dumps({"lcsc": "C1001", "entries": [{**entry, "correction": {**entry["correction"],
                                                                                     "rot": "x"}}]}),
                   json.dumps({"lcsc": "C1001", "entries": [{**entry, "correction": None}]}),
                   json.dumps({"lcsc": "C1001", "entries": ["entry"]}), b"\xff\xfe{\x00}\x00"):
        f.write_bytes(broken if isinstance(broken, bytes) else broken.encode())
        for cmd in commands:
            code, out, err = run(capsys, *cmd, "--db", db, *offline(env))
            assert code == 3 and "C1001.json: not a valid jlcplace correction file" in err, (broken, cmd, out, err)
            assert "Traceback" not in err
    f.write_text(good)
    # a snapshot whose code has a leading zero (a package typed into the field) is no code, as the board readers
    # read it; it crashed the database lookup
    board.part("R1").lcsc = "C0402"
    save_snapshot(board, env.board)
    code, out, err = run(capsys, "check", env.board, env.out, "--db", db, *offline(env), "--json", "-")
    r1 = next(r for r in json.loads(out)["results"] if r["ref"] == "R1")
    assert code == 0 and r1["lcsc"] == "" and "the board part has no LCSC code" in r1["messages"], err


def test_an_internal_error_is_not_the_yellow_exit_code(env, capsys, monkeypatch):
    """sec-2: any uncaught exception left the console script with exit 1, which means 'yellow' to a wrapper that
    tolerates yellow."""
    import jlcplace.cli as cli

    def boom(args):
        raise RuntimeError("a bug")

    monkeypatch.setattr(cli, "cmd_check", boom)
    env.make()
    code, out, err = run(capsys, "check", env.board, env.out, *offline(env))
    assert code == 4 and "jlcplace: internal error: RuntimeError: a bug" in err and "Traceback" in err


def test_an_output_that_cannot_be_written_is_bad_input(env, capsys):
    """cli-3: an output under a file (or a read-only directory) crashed with a traceback and exit 1 (the 'yellow'
    code) even on a red run, and the outputs after it were never written."""
    env.make()
    run(capsys, "cpl", env.board, "-o", env.out, *offline(env))
    rows = read_cpl(env.out)
    next(r for r in rows if r.ref == "U1").rotation = 0.0                  # red
    write_cpl(env.out, rows)
    afile = env.tmp / "afile"
    afile.write_text("not a directory")
    for opt in ("--report", "--json", "--junit", "--checklist"):
        later = env.tmp / f"later{opt}.xml"
        code, out, err = run(capsys, "check", env.board, env.out, *offline(env), "-q", opt, afile / "r.out",
                             *(["--junit", later] if opt != "--junit" else ["--json", later]))
        assert code == 3 and f"{afile / 'r.out'}: cannot write" in err and "Traceback" not in err, (opt, err)
        assert later.is_file(), opt                                         # the other outputs are still written
    code, out, err = run(capsys, "cpl", env.board, "-o", afile / "CPL.csv", *offline(env))
    assert code == 3 and "cannot write" in err and "Traceback" not in err, err
    code, out, err = run(capsys, "fit", env.board, "--json", afile / "f.json", *offline(env))
    assert code == 3 and "cannot write" in err and "Traceback" not in err, err
    for cmd in (["fit", env.board], ["confirm", env.board, "--ref", "U1", "--by", "me", "--source", "manual"]):
        code, out, err = run(capsys, *cmd, "--db", afile / "db", *offline(env))
        assert code == 3 and "cannot write" in err and "Traceback" not in err, (cmd, err)


def test_a_reference_on_the_board_twice_is_red(env, capsys):
    """cli-9 / logic-7: R2 renamed R1 on the board and its row left out (a CPL keyed by reference): the one R1 row was
    checked against the first R1 (or against the other one's footprint, off-copper), exit 0 or a misleading red, and
    resolve() gave the first R1 the second one's correction."""
    board = env.make()
    run(capsys, "cpl", env.board, "-o", env.out, *offline(env))
    board.part("R2").ref = "R1"
    save_snapshot(board, env.board)
    write_cpl(env.out, [r for r in read_cpl(env.out) if r.ref != "R2"])
    code, out, err = run(capsys, "check", env.board, env.out, *offline(env))
    line = next(ln for ln in out.splitlines() if ln.startswith("RED     R1"))
    assert code == 2 and "R1 is on the board 2 times: JLC cannot tell them apart" in line, out
    assert sum(1 for ln in out.splitlines() if " R1 " in ln) == 1, out
    resolved = resolve(Board("b", ORIGIN, board.parts), _Client())
    assert resolved["R1"].part is board.parts[2]                           # the first R1, not overwritten
    code, out, err = run(capsys, "confirm", env.board, "--ref", "R1", "--db", env.tmp / "db", "--by", "me",
                         "--source", "manual", *offline(env))
    assert code == 3 and "R1 is on the board 2 times" in err


class _Client:
    def get(self, code):
        return NotFound(code, "not here")
