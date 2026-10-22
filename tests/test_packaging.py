"""Packaging and CI as data: pyproject's metadata (cli-14), the README's CI recipe (cli-5, ci-4) and this repository's
own workflow (.github/workflows/ci.yml). A workflow GitHub would reject, or a classifier the CI matrix does not test,
fails here and not on the first push."""
import re
from pathlib import Path

import pytest

try:
    import tomllib
except ImportError:                       # Python 3.10: pytest itself depends on tomli there
    import tomli as tomllib

import jlcplace

ROOT = Path(__file__).resolve().parents[1]
REPO = "https://github.com/tyevco/jlcplace"
PYTHONS = ["3.10", "3.11", "3.12", "3.13"]


def pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def load_yaml(text: str) -> dict:
    yaml = pytest.importorskip("yaml")    # in the test extra; CI always has it
    return yaml.safe_load(text)


def triggers(workflow: dict):
    return workflow.get("on", workflow.get(True))      # YAML 1.1 reads a bare `on` as true


def runs(steps) -> list[str]:
    return [s.get("run", "") for s in steps]


def step(steps, text: str) -> tuple[int, dict]:
    """The first step whose `run` or `uses` holds text, and its index."""
    return next((i, s) for i, s in enumerate(steps) if text in s.get("run", "") or text in s.get("uses", ""))


# ------------------------------------------------------------------------------------------------------- cli-14

def test_pyproject_metadata():
    """cli-14: `license = {text = "MIT"}` is deprecated (setuptools warned 3-4 times per build and stops taking it in
    2027), and there were no project URLs, no authors and no Python version or OS classifiers."""
    meta = pyproject()
    p = meta["project"]
    assert p["license"] == "MIT" and p["license-files"] == ["LICENSE"]
    assert (ROOT / "LICENSE").read_text(encoding="utf-8").startswith("MIT License")
    # setuptools reads a license expression from 77 on (an older one refuses it under --no-build-isolation)
    setuptools = [r for r in meta["build-system"]["requires"] if r.startswith("setuptools")]
    assert setuptools and int(re.fullmatch(r"setuptools>=(\d+)(\.\d+)*", setuptools[0]).group(1)) >= 77, setuptools
    assert {"name": "tyevco"} in p["authors"]
    assert p["urls"] == {"Homepage": REPO, "Repository": REPO, "Issues": REPO + "/issues"}
    assert p["version"] == jlcplace.__version__
    classifiers = p["classifiers"]
    assert not [c for c in classifiers if c.startswith("License ::")]     # PEP 639: the expression says it
    assert "Operating System :: OS Independent" in classifiers
    versions = [c.rsplit(" :: ", 1)[1] for c in classifiers
                if re.fullmatch(r"Programming Language :: Python :: 3\.\d+", c)]
    assert versions == PYTHONS and p["requires-python"] == ">=" + PYTHONS[0]


# ------------------------------------------------------------------------------------------------------- CI

def ci() -> dict:
    return load_yaml((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))


def test_ci_tests_what_the_package_claims():
    """The unit tests run on every OS and Python the classifiers name, without the locale deciding text I/O; the
    kicad job runs the pcbnew tests in KiCad's image with its own Python; the e2e job fetches the records it caches."""
    wf = ci()
    assert {"push", "pull_request"} <= set(triggers(wf))
    jobs = wf["jobs"]
    unit = jobs["unit"]
    m = unit["strategy"]["matrix"]
    assert sorted(m["os"]) == ["macos-latest", "ubuntu-latest", "windows-latest"] and m["python"] == PYTHONS
    assert "exclude" not in m and unit["defaults"]["run"]["shell"] == "bash"
    _, test = step(unit["steps"], "pytest")
    assert "-X warn_default_encoding" in test["run"] and '"not pcbnew and not cache and not network"' in test["run"]
    assert "error::EncodingWarning" in " ".join(pyproject()["tool"]["pytest"]["ini_options"]["filterwarnings"])

    kicad = jobs["kicad"]
    assert kicad["container"]["image"] == "kicad/kicad:9.0" and "--user root" in kicad["container"]["options"]
    install = "\n".join(runs(kicad["steps"]))
    assert "python3 -m venv --system-site-packages" in install and "python3-venv" in install   # the image's Python
    _, test = step(kicad["steps"], "pytest")
    assert '-m "not cache and not network and not claude"' in test["run"] and kicad["env"]["ALLOW_SKIPS"] == "(?!)"

    e2e = jobs["e2e"]
    steps = e2e["steps"]
    checkouts = {s["with"].get("repository") for s in steps if s.get("uses", "").startswith("actions/checkout")}
    assert "tyevco/dial-me-spinner" in checkouts
    assert "JLCPLACE_DIAL_ME_SPINNER" in "\n".join(runs(steps))
    i_restore, restore = step(steps, "actions/cache/restore")
    i_fetch, fetch = step(steps, "jlcplace fetch")
    i_save, save = step(steps, "actions/cache/save")
    i_test, test = step(steps, "pytest")
    assert i_restore < i_fetch < i_save < i_test
    cache = re.search(r"--cache (\S+)", fetch["run"]).group(1)
    assert restore["with"]["path"] == save["with"]["path"] == cache
    assert fetch["continue-on-error"] is True and fetch["id"] == "fetch"
    assert "steps.fetch.outcome == 'success'" in save["if"]
    assert test["continue-on-error"] == "${{ steps.fetch.outcome == 'failure' }}"
    for job in jobs.values():
        for s in job["steps"]:
            assert "@" in s.get("uses", "@"), s                               # every action at a version


# ------------------------------------------------------------------------------------------------------- cli-5, ci-4

def readme_recipe() -> dict:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    start = text.index("# .github/workflows/cpl.yml")
    return load_yaml(text[start:text.index("```", start)])


def test_readme_ci_recipe_is_a_workflow_that_fills_its_cache():
    """cli-5 / ci-4: the recipe was not YAML (a '{' inside a flow mapping: GitHub rejects the file), no step wrote the
    record cache it restored (so the 'full simulation' never happened), and it installed whatever HEAD was."""
    wf = readme_recipe()
    assert isinstance(wf, dict) and {"push", "pull_request"} <= set(triggers(wf))
    (job,) = wf["jobs"].values()
    steps = job["steps"]
    _, install = step(steps, "pip install")
    assert re.search(r'"jlcplace @ git\+https://github\.com/tyevco/jlcplace@[\w.-]+"', install["run"]), install
    i_cache, cache = step(steps, "actions/cache@")
    i_fetch, fetch = step(steps, "jlcplace fetch")
    i_check, check = step(steps, "jlcplace check")
    assert i_cache < i_fetch < i_check
    # the records go where the cache step saves them: JLCPLACE_CACHE (jlcplace expands the ~, so does actions/cache)
    assert "--cache" not in fetch["run"] and job["env"]["JLCPLACE_CACHE"] == cache["with"]["path"]
    assert "hashFiles(" in cache["with"]["key"] and cache["with"]["restore-keys"]
    board = re.search(r"jlcplace fetch (\S+)", fetch["run"]).group(1)
    assert board in check["run"] and board in cache["with"]["key"]
    assert fetch.get("continue-on-error") is True                  # EasyEDA down: the check still runs, and says so
    assert "--online" not in check["run"] and "--report" not in check["run"]
