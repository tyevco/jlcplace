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
import test_board, test_check, test_cpl, test_e2e, test_easyeda, test_fit
print(json.dumps({
    "board": [str(test_board.DIAL), str(test_board.REF)] + [str(p) for p in test_board.OLDER + test_board.NEWER],
    "check": [test_check.DMS, test_check.BEFORE, test_check.OFFSETS, test_check.pcb_path("main"),
              test_check.cpl_path("base", before=True)],
    "cpl": [test_cpl.DMS, test_cpl.MAIN_PCB],
    "e2e": [str(test_e2e.DIAL), str(test_e2e.SCRATCH)],
    "easyeda": [str(test_easyeda.DIAL), str(test_easyeda.EE_RAW), str(test_easyeda.Z_FIX)],
    "fit": [str(test_fit.DIAL), str(test_fit.EE_RAW), str(test_fit.Z_FIX)],
}))
"""


def test_fixture_locations_follow_the_environment(tmp_path):
    env = dict(os.environ, JLCPLACE_DIAL_ME_SPINNER=str(tmp_path / "dms"), JLCPLACE_TEST_EASYEDA=str(tmp_path / "jlc"))
    out = subprocess.run([sys.executable, "-c", PROBE, str(TESTS)], env=env, capture_output=True, text=True,
                         cwd=tmp_path, check=True).stdout
    paths = json.loads(out.splitlines()[-1])
    for module, ps in paths.items():
        for p in ps:
            assert p.startswith(str(tmp_path)), (module, p)
