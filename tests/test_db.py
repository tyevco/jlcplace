"""The correction database, and the JLC-preview key-press convention."""
import csv
import json
import math
import os
import random
import shutil
from pathlib import Path

import pytest

from jlcplace import convention as cv
from jlcplace import db as dbmod
from jlcplace.board import load_board
from jlcplace.cli import main
from jlcplace.cpl import assembled, read_cpl, write_cpl
from jlcplace.db import PRESS_MM, PRESS_REASON, STALE_REASON, CorrectionDB, presses_to_correction, presses_to_row
from jlcplace.model import BOTTOM, GREEN, RED, TOP, VERIFIED, YELLOW, Correction, CplRow, Part

K = PRESS_MM
SCRATCH = Path(os.environ.get("JLCPLACE_TEST_EASYEDA", "/tmp/claude-0/-home-user-dial-me-spinner/"
                              "3708159a-bc3b-5167-8507-f5b08e1ce6c9/scratchpad/jlc"))
Z_FIX = SCRATCH / "src/z2amiller_jlccheck/tests/fixtures/jlcfootprint"
CORNER = Z_FIX / "boards/corner_case/corner_case.kicad_pcb"


def corr(rot=90.0, dx=0.1, dy=-0.2, ee_key="uuid1:100", kicad_hash="sha1:aaaa", status="auto", **kw):
    return Correction(rot=rot, dx=dx, dy=dy, method="number", rms=0.0123456, verdict=kw.pop("verdict", GREEN),
                      score=90, reasons=kw.pop("reasons", ["fit by number"]), matched=6, ee_key=ee_key,
                      kicad_hash=kicad_hash, status=status, **kw)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "_today", lambda: "2026-10-22")
    return CorrectionDB(tmp_path / "db")


# ------------------------------------------------------------------------------------------------------- files

def test_file_layout(db):
    db.upsert("c2681320", corr(), footprint="Package_TO_SOT_SMD:SOT-23-6", ee_title="SOT-23-6_L2.9-W1.6")
    f = db.path / "C2681320.json"
    text = f.read_text(encoding="utf-8")
    doc = json.loads(text)
    assert text == json.dumps(doc, sort_keys=True, indent=1, ensure_ascii=False) + "\n"
    assert doc == {"lcsc": "C2681320", "entries": [{
        "ee": {"key": "uuid1:100", "title": "SOT-23-6_L2.9-W1.6"},
        "kicad": {"fpid": "Package_TO_SOT_SMD:SOT-23-6", "pad_hash": "sha1:aaaa"},
        "correction": {"rot": 90.0, "dx": 0.1, "dy": -0.2, "mirror": False},
        "fit": {"method": "number", "rms": 0.0123, "matched": 6, "score": 90, "alternatives": []},
        "verdict": GREEN, "reasons": ["fit by number"], "status": "auto", "verifications": [],
        "tool": "jlcplace 0.1.0", "fitted": "2026-10-22"}]}
    assert os.listdir(db.path) == ["C2681320.json"]
    got = db.lookup("C2681320", ee_key="uuid1:100", kicad_hash="sha1:aaaa")
    assert (got.rot, got.dx, got.dy, got.mirror, got.verdict, got.status) == (90.0, 0.1, -0.2, False, GREEN, "auto")
    assert (got.method, got.matched, got.score, got.rms, got.ee_key, got.kicad_hash) == \
        ("number", 6, 90, 0.0123, "uuid1:100", "sha1:aaaa")


def test_a_looked_up_correction_carries_its_entrys_easyeda_title(db):
    """The check judges a part the fit judged non-polar by its EasyEDA title (BI, a crystal's) when the record is not
    at hand: the stored entry's title comes back with its polarity."""
    db.upsert("C140894", corr(polar=False), ee_title="SMA_L4.4-W2.6-LS5.0-BI")
    got = db.lookup("C140894", ee_key="uuid1:100", kicad_hash="sha1:aaaa")
    assert (got.polar, got.ee_title) == (False, "SMA_L4.4-W2.6-LS5.0-BI")
    db.upsert("C1", corr())
    assert (db.lookup("C1").polar, db.lookup("C1").ee_title) == (None, "")


def test_no_pad_geometry_is_stored(db):
    alternatives = [{"rot": 270.0, "dx": 0.0, "dy": 1.5, "mirror": False, "nets": "same",
                     "pads": [[1.0, 2.0], [3.0, 4.0]], "landings": [{"ee": "1", "x": 0.1, "y": 0.2}],
                     "ee": [["1", -0.43, 0.0], ["2", 0.43, 0.0]], "bad": float("nan")}]
    db.upsert("C1", corr(alternatives=alternatives), ee_title="R0402")
    (alt,) = db.entries("C1")[0]["fit"]["alternatives"]
    assert alt == {"rot": 270.0, "dx": 0.0, "dy": 1.5, "mirror": False, "nets": "same"}
    part = Part("R1", BOTTOM, 0, 0, 30)
    db.confirm("C1", ee_key="uuid1:100", kicad_hash="sha1:aaaa", by="me", source="preview",
               presses={"space": 1, "right": 2, "up": -1}, part=part,
               correction=presses_to_correction(part, db.lookup("C1"), space=1, right=2, up=-1))
    db.upsert("C1", corr(kicad_hash="sha1:bbbb", alternatives=alternatives))
    doc = json.loads((db.path / "C1.json").read_text(encoding="utf-8"))

    def walk(v, path):
        if isinstance(v, dict):
            for k, w in v.items():
                walk(w, f"{path}.{k}")
        elif isinstance(v, list):
            for w in v:
                assert isinstance(w, (dict, str)), f"{path} holds {w!r}: no coordinate lists in the database"
                walk(w, path + "[]")
        else:
            assert not (isinstance(v, float) and not math.isfinite(v)), path

    walk(doc, "")
    assert doc["entries"][0]["fit"]["alternatives"] == []             # pressed: the old fit's members are gone
    assert all(set(e["ee"]) == {"key", "title"} for e in doc["entries"])


def test_writes_are_atomic(db, monkeypatch):
    db.upsert("C1", corr())
    before = (db.path / "C1.json").read_bytes()

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(dbmod.DBError, match="C1.json: cannot write: disk full"):      # cli-3: bad input, exit 3
        db.upsert("C1", corr(kicad_hash="sha1:bbbb"))
    assert (db.path / "C1.json").read_bytes() == before
    assert os.listdir(db.path) == ["C1.json"]                         # the temporary file was removed


@pytest.mark.parametrize("code", ["../etc/passwd", "C12/../C1", "X123", "", "C0402"])
def test_codes_are_validated(db, code):
    with pytest.raises(ValueError):
        db.upsert(code, corr())
    with pytest.raises(ValueError):
        db.lookup(code)


def test_not_a_correction_file_and_hand_edited_entries(db):
    db.path.mkdir(parents=True)
    (db.path / "C5.json").write_text('["not", "an", "entry file"]\n', encoding="utf-8")
    with pytest.raises(ValueError):
        db.lookup("C5")
    (db.path / "C6.json").write_text('{"lcsc": "C6", "entries": [{"correction": {"rot": 90, "dx": 0, "dy": 1}, '
                                     '"status": "manual"}]}\n', encoding="utf-8")
    got = db.lookup("C6", ee_key="u:1", kicad_hash="sha1:x")         # no keys: it claims no footprint
    assert (got.rot, got.dy, got.status, got.method, got.ee_key) == (90.0, 1.0, "manual", "db", "")
    # sec-2: git conflict markers and a hand-edited value were a JSONDecodeError / a bare ValueError, from lookup
    # only when the entry was reached
    good = (db.path / "C6.json").read_text(encoding="utf-8")
    for broken in ("<<<<<<< HEAD\n" + good + "=======\n" + good + ">>>>>>> other\n",
                   good.replace('"rot": 90', '"rot": "x"'), good.replace('"correction": {', '"correction": [{')):
        (db.path / "C6.json").write_text(broken, encoding="utf-8")
        for call in (lambda: db.lookup("C6"), lambda: db.entries("C6"), lambda: db.upsert("C6", corr())):
            with pytest.raises(dbmod.DBError, match="C6.json: not a valid jlcplace correction file"):
                call()


# ------------------------------------------------------------------------------------------------------- keys

def test_keys_same_code_on_two_footprints(db):
    db.upsert("C7519", corr(rot=270.0, kicad_hash="sha1:sot236"), footprint="SOT-23-6")
    db.upsert("C7519", corr(rot=90.0, kicad_hash="sha1:other"), footprint="SOT-23-6_Handsoldering")
    assert len(db.entries("C7519")) == 2
    assert db.lookup("C7519", ee_key="uuid1:100", kicad_hash="sha1:sot236").rot == 270.0
    assert db.lookup("C7519", ee_key="uuid1:100", kicad_hash="sha1:other").rot == 90.0
    assert db.lookup("C7519", kicad_hash="sha1:nope") is None              # a third footprint needs its own fit
    assert db.lookup("C9999") is None and db.entries("C9999") == []
    assert db.lookup("C7519").rot == 90.0                                  # match any: equal status, the later one


def test_precedence_board_preview_manual_auto(db):
    for h, status in (("sha1:a", "auto"), ("sha1:m", "manual"), ("sha1:p", "auto"), ("sha1:b", "auto")):
        db.upsert("C1", corr(kicad_hash=h, status=status, dx=len(h)))
    db.confirm("C1", ee_key="uuid1:100", kicad_hash="sha1:p", by="me", source="preview")
    assert db.lookup("C1").kicad_hash == "sha1:p"
    db.confirm("C1", ee_key="uuid1:100", kicad_hash="sha1:b", by="me", source="board")
    assert db.lookup("C1").kicad_hash == "sha1:b"
    db.confirm("C1", ee_key="uuid1:100", kicad_hash="sha1:b", by="me", source="board", ok=False, note="turned")
    assert db.lookup("C1").kicad_hash == "sha1:p"
    db.confirm("C1", ee_key="uuid1:100", kicad_hash="sha1:p", by="me", source="preview", ok=False)
    assert db.lookup("C1").kicad_hash == "sha1:m"


def test_stale_entries(db):
    db.upsert("C1", corr(ee_key="uuid1:100"))
    db.confirm("C1", ee_key="uuid1:100", kicad_hash="sha1:aaaa", by="me", source="board")
    got = db.lookup("C1", ee_key="uuid1:200", kicad_hash="sha1:aaaa")
    assert (got.status, got.verdict, got.rot, got.ee_key) == ("stale", RED, 90.0, "uuid1:100")
    assert got.reasons[-1] == STALE_REASON == "footprint changed since verified"
    db.upsert("C1", corr(rot=270.0, ee_key="uuid1:200"))                   # the caller refits
    got = db.lookup("C1", ee_key="uuid1:200", kicad_hash="sha1:aaaa")
    assert (got.status, got.rot) == ("auto", 270.0)
    assert db.lookup("C1", ee_key="uuid1:100", kicad_hash="sha1:aaaa").status == "board-verified"
    # an entry made without an EasyEDA footprint (no key) is never stale
    db.upsert("C2", corr(ee_key="", status="manual"))
    assert db.lookup("C2", ee_key="uuid9:1", kicad_hash="sha1:aaaa").status == "manual"


def test_upsert_keeps_verifications_and_never_downgrades(db):
    db.upsert("C1", corr())
    db.confirm("C1", ee_key="uuid1:100", kicad_hash="sha1:aaaa", by="ann", source="board", board="dms", ref="U2",
               layer=TOP, note="assembled fine")
    db.upsert("C1", corr(dx=0.10004, reasons=["refit"], verdict=YELLOW))    # the same numbers, refitted
    (e,) = db.entries("C1")
    assert (e["status"], e["verdict"], e["reasons"]) == ("board-verified", VERIFIED, ["fit by number"])
    assert e["verifications"] == [{"by": "ann", "date": "2026-10-22", "source": "board", "ok": True, "board": "dms",
                                   "ref": "U2", "layer": TOP, "presses": None, "note": "assembled fine",
                                   "correction": {"rot": 90.0, "dx": 0.1, "dy": -0.2, "mirror": False}}]
    with pytest.raises(ValueError, match="board-verified"):
        db.upsert("C1", corr(rot=270.0))
    with pytest.raises(ValueError):
        db.upsert("C1", corr(status="stale"))
    db.upsert("C1", corr(rot=270.0, status="board-verified", verdict=VERIFIED))   # an equal status may replace
    assert db.lookup("C1").rot == 270.0 and len(db.entries("C1")[0]["verifications"]) == 1


def test_confirm(db):
    db.upsert("C1", corr(verdict=YELLOW))
    with pytest.raises(LookupError):
        db.confirm("C1", ee_key="other:1", kicad_hash="sha1:aaaa", by="me", source="board")
    with pytest.raises(ValueError):
        db.confirm("C1", ee_key="uuid1:100", kicad_hash="sha1:aaaa", by="me", source="hearsay")
    got = db.confirm("C1", ee_key="uuid1:100", kicad_hash="sha1:aaaa", by="me", source="manual")
    assert (got.status, got.verdict) == ("manual", YELLOW)
    got = db.confirm("C1", ee_key="uuid1:100", kicad_hash="sha1:aaaa", by="me", source="preview")
    assert (got.status, got.verdict) == ("preview-verified", VERIFIED)
    got = db.confirm("C1", ee_key="uuid1:100", kicad_hash="sha1:aaaa", by="me", source="manual")
    assert got.status == "preview-verified"                                 # a weaker check does not demote
    got = db.confirm("C1", ee_key="uuid1:100", kicad_hash="sha1:aaaa", by="bob", source="board", ok=False,
                     note="U2 pin 1 on pad 4")
    assert (got.status, got.verdict) == ("auto", RED)
    assert got.reasons[-1] == "verification failed (board, bob): U2 pin 1 on pad 4"
    assert [v["ok"] for v in db.entries("C1")[0]["verifications"]] == [True, True, True, False]


def test_a_failed_verification_holds_until_the_numbers_are_fixed_or_found_right(db):
    """A failed verification changed nothing that lasted: `fit --db` stored the same numbers green again, and the
    check could not tell the entry's numbers had been verified wrong. A verification records the numbers it was of;
    the latest one of the entry's numbers failing is `failure` (red), through refits of the same numbers, until other
    numbers replace them or a later verification of them is ok."""
    keys = {"ee_key": "uuid1:100", "kicad_hash": "sha1:aaaa"}
    db.upsert("C1", corr())
    assert db.lookup("C1").failure is None
    db.confirm("C1", **keys, by="ann", source="preview", ok=False, ref="U2", layer=TOP,
               note="pin 1 at the wrong corner")
    v = db.entries("C1")[0]["verifications"][-1]
    assert v["correction"] == {"rot": 90.0, "dx": 0.1, "dy": -0.2, "mirror": False}
    got = db.lookup("C1", **keys)
    assert got.verdict == RED and got.failure == v
    db.upsert("C1", corr())                                # a refit gives the same numbers: still wrong
    got = db.lookup("C1", **keys)
    assert (got.verdict, got.failure, got.reasons) == (RED, v, ["fit by number", "verification failed (preview, ann): "
                                                                                 "pin 1 at the wrong corner"])
    got = db.confirm("C1", **keys, by="bob", source="board")              # the assembled board was right after all
    assert (got.verdict, got.failure, db.lookup("C1").failure) == (VERIFIED, None, None)
    db.confirm("C1", **keys, by="bob", source="board", ok=False)
    db.upsert("C1", corr(rot=270.0, status="manual", verdict=YELLOW))   # fixed: the failure was of other numbers
    got = db.lookup("C1", **keys)
    assert (got.rot, got.verdict, got.failure, len(got.verifications)) == (270.0, YELLOW, None, 0)
    # a failure recorded before verifications held their numbers counts while the entry still gives its reason
    old = {"by": "ann", "date": "2026-01-02", "source": "preview", "ok": False, "board": "b.kicad_pcb", "ref": "U2",
           "layer": TOP, "presses": None, "note": ""}
    entry = db.entries("C1")[0]
    for reasons, failed in ((["fit by number", "verification failed (preview, ann)"], True),
                            (["fit by number"], False)):
        db.file("C2").write_text(json.dumps({"lcsc": "C2", "entries": [
            {**entry, "verdict": RED if failed else GREEN, "reasons": reasons, "verifications": [old]}]}),
            encoding="utf-8")
        got = db.lookup("C2", **keys)
        assert (got.failure is not None, got.verdict) == (failed, RED if failed else GREEN)


def test_a_refit_close_to_numbers_verified_wrong_keeps_them_red(db):
    """confirm stored an uploaded row's numbers, 0.08 mm off the fit's; the assembled board showed them wrong, and `fit
    --db` stored the fit's: other numbers, so the failure no longer counted and the row the board showed wrong was
    green again. A refit within the check's offset limits of numbers verified wrong is the same placement."""
    keys = {"ee_key": "uuid1:100", "kicad_hash": "sha1:aaaa"}
    db.upsert("C1", corr(dy=-0.12, status="manual", verdict=YELLOW))     # an uploaded row, 0.08 mm from the fit's
    db.confirm("C1", **keys, by="ann", source="board", ok=False, note="reversed")
    for refit in (corr(), corr(rot=91.5, dx=0.0)):                       # the fit's dy -0.2, or 1.5 deg and 0.13 mm
        db.upsert("C1", refit)
        got = db.lookup("C1", **keys)
        assert (got.rot, got.dx, got.dy, got.verdict) == (90.0, 0.1, -0.12, RED), got
        assert got.failure is not None and got.reasons[-1] == "verification failed (board, ann): reversed"
    db.upsert("C1", corr(rot=270.0))                                     # another placement replaces them
    got = db.lookup("C1", **keys)
    assert (got.rot, got.verdict, got.failure) == (270.0, GREEN, None)
    db.confirm("C1", **keys, by="ann", source="board", ok=False)
    db.upsert("C1", corr(rot=270.0, dy=-0.1, status="manual", verdict=YELLOW))   # typed in: it replaces them
    got = db.lookup("C1", **keys)
    assert (got.dy, got.verdict, got.failure) == (-0.1, YELLOW, None)


def test_manual_confirmation_of_a_red_fit(db):
    """A fit that cannot decide (a switch's internal pairs) is red; a person's manual confirmation makes it yellow,
    not verified: only JLC's preview or the assembled board does that."""
    db.upsert("C9", corr(verdict=RED, reasons=["numbering conflict: EE 2 -> KiCad 1"]))
    got = db.confirm("C9", ee_key="uuid1:100", kicad_hash="sha1:aaaa", by="ann", source="manual", note="datasheet")
    assert (got.status, got.verdict) == ("manual", YELLOW)
    assert got.reasons == ["numbering conflict: EE 2 -> KiCad 1", "accepted manually (ann): datasheet"]
    assert db.lookup("C9").verdict == YELLOW
    got = db.confirm("C9", ee_key="uuid1:100", kicad_hash="sha1:aaaa", by="bob", source="preview")
    assert (got.status, got.verdict) == ("preview-verified", VERIFIED)
    db.upsert("C8", corr(verdict=GREEN))
    assert db.confirm("C8", ee_key="uuid1:100", kicad_hash="sha1:aaaa", by="ann", source="manual").verdict == GREEN


def test_confirm_with_presses(db):
    """geo-1: presses are relative to the row that was uploaded, which need not be the stored correction's row:
    confirm records that row's correction with the presses applied, and never adds them to the stored numbers."""
    db.upsert("C1", corr(rot=90.0, dx=0.1, dy=-0.2))
    part = Part("U12", BOTTOM, 120.0, 80.0, 0.0)
    keys = {"ee_key": "uuid1:100", "kicad_hash": "sha1:aaaa", "by": "me"}
    with pytest.raises(ValueError, match="relative to the row that was uploaded"):
        db.confirm("C1", **keys, source="preview", presses=(2, 0, 0), part=part)
    with pytest.raises(ValueError):
        db.confirm("C1", **keys, source="preview", presses=(2, 0, 0), part=part, ok=False,
                   correction=corr(rot=270.0))
    with pytest.raises(ValueError):
        db.confirm("C1", **keys, source="preview", presses={"left": 1}, part=part, correction=corr())
    assert db.lookup("C1").rot == 90.0 and db.entries("C1")[0]["verifications"] == []
    db.confirm("C1", **keys, source="board")
    # the uploaded row was the stored correction's; JLC's preview needed two space and three right presses
    origin = (100.0, 100.0)
    row = CplRow("U12", *cv.cpl_row_for(part, db.lookup("C1"), origin)[:2], BOTTOM,
                 cv.cpl_row_for(part, db.lookup("C1"), origin)[2])
    seen = cv.row_correction(part, presses_to_row(row, space=2, right=3), origin)
    pressed = Correction(seen.rot, seen.dx, seen.dy, method="manual", verdict=YELLOW, reasons=["from the row"])
    with pytest.raises(ValueError, match="board-verified with other numbers"):
        db.confirm("C1", **keys, source="preview", presses={"space": 2, "right": 3}, part=part, correction=pressed)
    db.confirm("C1", **keys, source="board", ok=False, note="turned")
    got = db.confirm("C1", **keys, source="preview", presses={"space": 2, "right": 3}, part=part, correction=pressed)
    # a bottom part at 0 deg: right in the preview (seen from below) is -x on the board and in the library
    assert (got.rot, got.dx, got.dy) == (270.0, pytest.approx(0.1 - 3 * K, abs=1e-4), -0.2)
    assert got.status == "preview-verified" and got.reasons == ["from the row"]
    got = db.confirm("C1", **keys, source="preview", presses={"space": 2, "right": 3}, part=part,
                     correction=presses_to_correction(part, corr(rot=90.0, dx=0.1, dy=-0.2), space=2, right=3))
    assert got.method == "manual" and db.entries("C1")[0]["fit"]["method"] == "manual"
    assert got.reasons == ["from the row"]           # the same numbers: the entry is kept, the verification added
    assert len(db.entries("C1")[0]["verifications"]) == 4 and PRESS_REASON == "derived from presses (FT convention)"
    v = db.entries("C1")[0]["verifications"][-1]
    assert v["presses"] == {"space": 2, "right": 3, "up": 0} and (v["ref"], v["layer"]) == ("U12", BOTTOM)


# ------------------------------------------------------------------------------------------------------- presses

def _lib_delta(part, **presses):
    c = presses_to_correction(part, Correction(rot=0.0), **presses)
    return c.dx, c.dy


# Fabrication-Toolkit's README: which JLC preview arrow corresponds to +x and +y of its 'FT Position Offset'
FT_TABLE = [(0, TOP, "right", "up"), (0, BOTTOM, "left", "down"), (180, TOP, "left", "down"),
            (180, BOTTOM, "right", "up"), (90, TOP, "up", "left"), (90, BOTTOM, "up", "left"),
            (-90, TOP, "down", "right"), (-90, BOTTOM, "down", "right")]
ARROW = {"right": {"right": 1}, "left": {"right": -1}, "up": {"up": 1}, "down": {"up": -1}}


@pytest.mark.parametrize("theta, layer, x_arrow, y_arrow", FT_TABLE)
def test_presses_follow_fabrication_toolkit_table(theta, layer, x_arrow, y_arrow):
    """FT's offset (px, py) is the library-frame vector (px, -py) (y up in the footprint's own frame, see the next
    test), so one press of the arrow its table names for x must move the EasyEDA origin by (+K, 0) in the library
    frame, and the one named for y by (0, -K)."""
    part = Part("U1", layer, 0.0, 0.0, cv.norm_angle(theta))
    assert _lib_delta(part, **ARROW[x_arrow]) == (pytest.approx(K, abs=1e-12), pytest.approx(0.0, abs=1e-12))
    assert _lib_delta(part, **ARROW[y_arrow]) == (pytest.approx(0.0, abs=1e-12), pytest.approx(-K, abs=1e-12))


def test_fabrication_toolkit_offset_is_a_library_vector_with_y_up():
    """FT moves the CPL point (cpl frame) by (px c - py s, px s + py c) on top and (px c + py s, px s - py c) on the
    bottom (c, s of theta); that is library vector (px, -py) carried to the board and into the cpl frame."""
    rnd = random.Random(8)
    for _ in range(300):
        theta, px, py = rnd.uniform(-180, 180), rnd.uniform(-2, 2), rnd.uniform(-2, 2)
        c, s = math.cos(math.radians(theta)), math.sin(math.radians(theta))
        for layer, ft in ((TOP, (px * c - py * s, px * s + py * c)), (BOTTOM, (px * c + py * s, px * s - py * c))):
            part = Part("U1", layer, 31.0, 47.0, cv.norm_angle(theta))
            a, b = cv.library_to_board(part, (0.0, 0.0)), cv.library_to_board(part, (px, -py))
            delta = cv.board_to_cpl(b, (0, 0))[0] - cv.board_to_cpl(a, (0, 0))[0], \
                cv.board_to_cpl(b, (0, 0))[1] - cv.board_to_cpl(a, (0, 0))[1]
            assert math.dist(delta, ft) < 1e-7          # norm_angle keeps 1e-6 deg


@pytest.mark.parametrize("layer", [TOP, BOTTOM])
def test_presses_move_the_part_on_the_preview_screen(layer):
    """In the preview (top: the cpl frame; bottom: seen from below, cpl x mirrored) an arrow press moves the placed
    part by K that way, and a space press turns it 90 deg counter-clockwise about the row point."""
    rnd = random.Random(9)
    sigma = -1 if layer == BOTTOM else 1
    origin = (100.0, 100.0)
    for _ in range(100):
        part = Part("U1", layer, rnd.uniform(60, 140), rnd.uniform(60, 140), rnd.uniform(0, 360))
        base = Correction(rot=rnd.choice([0, 90, 180, 270, rnd.uniform(0, 360)]), dx=rnd.uniform(-1, 1),
                          dy=rnd.uniform(-1, 1))
        right, up, space = rnd.randint(-5, 5), rnd.randint(-5, 5), rnd.randint(-3, 3)
        new = presses_to_correction(part, base, space=space, right=right, up=up)
        assert new.method == "manual" and new.reasons[-1] == f"{PRESS_REASON}: space {space}, right {right}, up {up}"
        x0, y0, a0 = cv.cpl_row_for(part, base, origin)
        x1, y1, a1 = cv.cpl_row_for(part, new, origin)
        screen = (sigma * (x1 - x0), y1 - y0)
        assert math.dist(screen, (right * K, up * K)) < 2e-4               # cpl_row_for rounds to 1e-4 mm
        assert abs((a1 - a0 - 90 * space + 180) % 360 - 180) < 1e-5
        # the footprint as the preview shows it, relative to the row point: turned by 90 * space CCW
        e = (rnd.uniform(-2, 2), rnd.uniform(-2, 2))

        def shown(x, y, a, e=e):
            C = cv.cpl_to_board((x, y), origin)
            q = cv.board_to_cpl(cv.ee_to_board(e, C, a, layer), origin)
            return sigma * (q[0] - x), q[1] - y                            # screen axes, y up

        u, v = shown(x0, y0, a0), shown(x1, y1, a1)
        t = math.radians(90 * space)                                       # CCW on a y-up screen
        assert math.dist(v, (u[0] * math.cos(t) - u[1] * math.sin(t), u[0] * math.sin(t) + u[1] * math.cos(t))) < 1e-6


def test_presses_keep_mirror_and_status_and_replace_old_press_reason():
    part = Part("U1", TOP, 0, 0, 0)
    base = Correction(rot=10.0, mirror=True, status="manual", verdict=YELLOW, reasons=["x"])
    once = presses_to_correction(part, base, right=1)
    twice = presses_to_correction(part, once, up=1)
    assert (twice.mirror, twice.status, twice.verdict) == (True, "manual", YELLOW)
    assert twice.reasons == ["x", f"{PRESS_REASON}: space 0, right 0, up 1"]
    assert (twice.dx, twice.dy) == (pytest.approx(K), pytest.approx(-K))
    assert presses_to_correction(part, base, space=4).rot == 10.0


@pytest.mark.parametrize("layer", [TOP, BOTTOM])
def test_presses_on_the_uploaded_row_agree_with_presses_on_its_correction(layer):
    """geo-1: confirm moves the uploaded row (presses_to_row) and takes its correction (convention.row_correction);
    under jlc-2022 that is presses_to_correction of the row's own correction, and row_correction inverts cpl_row_for
    under every reading."""
    rnd = random.Random(10)
    origin = (100.0, 100.0)
    for _ in range(200):
        part = Part("U1", layer, rnd.uniform(60, 140), rnd.uniform(60, 140), rnd.choice([0, 90, 180, 270,
                                                                                        rnd.uniform(0, 360)]))
        base = Correction(rot=rnd.choice([0, 90, 180, 270, rnd.uniform(0, 360)]), dx=round(rnd.uniform(-2, 2), 4),
                          dy=round(rnd.uniform(-2, 2), 4))
        for reading in cv.BOTTOM_READINGS:
            x, y, a = cv.cpl_row_for(part, base, origin, reading)
            back = cv.row_correction(part, CplRow("U1", x, y, layer, a), origin, reading)
            assert abs((back.rot - base.rot + 180) % 360 - 180) < 1e-5
            assert math.dist((back.dx, back.dy), (base.dx, base.dy)) < 2e-4         # rows carry 1e-4 mm
        space, right, up = rnd.randint(-3, 3), rnd.randint(-5, 5), rnd.randint(-5, 5)
        x, y, a = cv.cpl_row_for(part, base, origin)
        seen = cv.row_correction(part, presses_to_row(CplRow("U1", x, y, layer, a), space=space, right=right, up=up),
                                 origin)
        want = presses_to_correction(part, base, space=space, right=right, up=up)
        assert abs((seen.rot - want.rot + 180) % 360 - 180) < 1e-5
        assert math.dist((seen.dx, seen.dy), (want.dx, want.dy)) < 2e-4


@pytest.mark.cache
@pytest.mark.skipif(not (CORNER.is_file() and (Z_FIX / "truth_presses.csv").is_file()),
                    reason=f"z2amiller ground truth not in {Z_FIX}")
def test_jlc_preview_presses_replayed_on_the_uploaded_rows(tmp_path, capsys):
    """geo-1: z2amiller recorded JLC's preview by uploading a CPL of raw KiCad angles and noting the space presses
    that put each part right. Replayed with `jlcplace confirm`, the presses went onto jlcplace's own fits instead of
    the uploaded rows: C2, D4, D8, D9, D12, LED2 and U4 ended preview-verified the wrong way round, and the truth's
    rows red against them."""
    cache = tmp_path / "cache"
    cache.mkdir()
    for f in (Z_FIX / "easyeda").glob("C*.json"):
        shutil.copy(f, cache)
    board = load_board(CORNER, reader="sexpr")
    rows = []
    for part in board.parts:
        if assembled(part) and part.lcsc:
            x, y, a = cv.cpl_row_for(part, Correction(0.0), board.origin)      # KiCad's anchor and angle
            rows.append(CplRow(part.ref, x, y, part.layer, a))
    raw = tmp_path / "raw.csv"
    write_cpl(raw, rows)
    presses = {r[0]: int(r[1]) // 90 for r in csv.reader(open(Z_FIX / "truth_presses.csv", encoding="utf-8"))
               if r and not r[0].startswith("#") and r[1:2] != [""]}
    truth = {r["reference"]: float(r["observed_rotation"])
             for r in csv.DictReader(open(Z_FIX / "truth.csv", encoding="utf-8"))
             if r["observed_rotation"].strip()}
    refs = ("C2", "D4", "D8", "D9", "D12", "LED2", "U4")
    db = tmp_path / "db"
    common = ["--reader", "sexpr", "--cache", cache, "--offline"]
    for ref in refs:
        code = main([str(a) for a in ["confirm", CORNER, "--ref", ref, "--db", db, "--by", "z2amiller", "--source",
                                      "preview", "--cpl", raw, "--space", presses[ref], *common]])
        out, err = capsys.readouterr()
        part = board.part(ref)
        assert code == 0 and f"rot {cv.norm_angle(truth[ref] - part.angle):g}, " in out, (ref, out, err)
    # the rows jlcplace writes are the truth's now, and the truth's rows are verified
    code = main([str(a) for a in ["cpl", CORNER, "-o", tmp_path / "jlc.csv", "--db", db, "--force", *common]])
    capsys.readouterr()
    written = {r.ref: r.rotation for r in read_cpl(tmp_path / "jlc.csv")}
    assert {ref: written[ref] for ref in refs} == {ref: truth[ref] for ref in refs}
    for r in rows:
        r.rotation = truth.get(r.ref, r.rotation) if r.ref in refs else r.rotation
    write_cpl(tmp_path / "truth.csv", rows)
    main([str(a) for a in ["check", CORNER, tmp_path / "truth.csv", "--db", db, "--json", tmp_path / "t.json",
                           "-q", *common]])
    got = {r["ref"]: r["verdict"] for r in json.loads((tmp_path / "t.json").read_text(encoding="utf-8"))["results"]}
    assert {ref: got[ref] for ref in refs} == {ref: VERIFIED for ref in refs}
