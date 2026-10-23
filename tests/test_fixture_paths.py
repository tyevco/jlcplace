"""The tests find their optional real data where CONTRACT.md says: JLCPLACE_DIAL_ME_SPINNER (a dial-me-spinner
checkout) and JLCPLACE_TEST_EASYEDA (ee_raw/, dl/, fab-before/, src/). Hard-coded paths made 44 tests skip, the golden
ones among them, on any machine but the one they were written on (ci-2)."""
import json
import os
import subprocess
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent

PROBE = """
import json, sys
sys.path.insert(0, sys.argv[1])
import test_board, test_check, test_cpl, test_db, test_e2e, test_easyeda, test_fit
import test_action, test_e2e_gerbers, test_gerber, test_offset, test_pinout, test_report_overlay
print(json.dumps({
    "board": [str(test_board.DIAL), str(test_board.REF)] + [str(p) for p in test_board.OLDER + test_board.NEWER],
    "check": [test_check.DMS, test_check.BEFORE, test_check.OFFSETS, test_check.pcb_path("main"),
              test_check.cpl_path("base", before=True)],
    "cpl": [test_cpl.DMS, test_cpl.MAIN_PCB],
    "db": [str(test_db.Z_FIX), str(test_db.CORNER)],
    "e2e": [str(test_e2e.DIAL), str(test_e2e.SCRATCH)],
    "easyeda": [str(test_easyeda.DIAL), str(test_easyeda.EE_RAW), str(test_easyeda.Z_FIX)],
    "fit": [str(test_fit.DIAL), str(test_fit.EE_RAW), str(test_fit.Z_FIX)],
    "action": [str(test_action.DIAL), str(test_action.SCRATCH)],
    "e2e_gerbers": [str(test_e2e_gerbers.DIAL), str(test_e2e_gerbers.EE_RAW), str(test_e2e_gerbers.BEFORE)],
    "gerber": [str(test_gerber.DIAL), str(test_gerber.JLC), str(test_gerber.GOLDEN)],
    "offset": [str(test_offset.DIAL), str(test_offset.SCRATCH)],
    "pinout": [str(p) for p in test_pinout.RECORDS],
    "report_overlay": [str(test_report_overlay.DIAL), str(test_report_overlay.SCRATCH)],
}))
"""


def test_fixture_locations_follow_the_environment(tmp_path):
    env = dict(os.environ, JLCPLACE_DIAL_ME_SPINNER=str(tmp_path / "dms"), JLCPLACE_TEST_EASYEDA=str(tmp_path / "jlc"),
               PYTHONIOENCODING="utf-8")
    out = subprocess.run([sys.executable, "-c", PROBE, str(TESTS)], env=env, capture_output=True, encoding="utf-8",
                         cwd=tmp_path, check=True).stdout
    paths = json.loads(out.splitlines()[-1])
    for module, ps in paths.items():
        for p in ps:
            assert p.startswith(str(tmp_path)), (module, p)
