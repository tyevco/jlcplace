"""action.yml, the composite GitHub Action other repositories run after their fab job, as data and as a run.

As data: the YAML parses, every input is used and reaches a script only through `env` (an input spliced into a
script would run as shell), the step outputs it names exist, nothing is uploaded and no HTML report is written. As a
run: its steps run here as the Actions runner runs them (bash --noprofile --norc -eo pipefail, the GITHUB_OUTPUT,
GITHUB_STEP_SUMMARY, GITHUB_ACTION_PATH and RUNNER_TEMP it sets, `${{ }}` expressions, `if:`), with
actions/setup-python left to the Python running the tests and actions/cache/restore and /save simulated with a
directory. The unmarked tests run each step on its own with a stand-in python or jlcplace; the `cache` test installs
the action for real (pip, from PyPI) and runs it on the dial-me-spinner boards with their cached EasyEDA records."""
import json
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / "action.yml"
BASH = shutil.which("bash")
DIAL = Path(os.environ.get("JLCPLACE_DIAL_ME_SPINNER", "/home/user/dial-me-spinner")) / "hardware"
SCRATCH = Path(os.environ.get("JLCPLACE_TEST_EASYEDA", "/tmp/claude-0/-home-user-dial-me-spinner/"
                              "3708159a-bc3b-5167-8507-f5b08e1ce6c9/scratchpad/jlc"))
INPUTS = {"board", "cpl", "bom", "gerbers", "db", "fail-on", "args", "python-version", "version"}
USES = {"actions/setup-python@v5", "actions/cache/restore@v4", "actions/cache/save@v4"}
# no network for the steps after the install: a request for an EasyEDA record the cache lacks fails at once
NOWHERE = {k: "http://127.0.0.1:9" for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy")} | \
    {"NO_PROXY": "", "no_proxy": ""}


def load() -> dict:
    yaml = pytest.importorskip("yaml")    # in the test extra; CI always has it
    return yaml.safe_load(ACTION.read_text(encoding="utf-8"))


def steps() -> list[dict]:
    return load()["runs"]["steps"]


def step(sid: str) -> dict:
    return next(s for s in steps() if s.get("id") == sid)


def need_bash():
    if BASH is None:
        pytest.skip("bash is not on this machine")


def posix(p) -> str:
    return Path(p).as_posix()


# ------------------------------------------------------------------------------------------------------- expressions

_TOKEN = re.compile(r"\s*(?:('(?:[^']|'')*')|(==|!=|&&|\|\||!|\(|\)|,)|([A-Za-z_][\w.-]*))")


def evaluate(expr: str, ctx: dict):
    """A GitHub Actions expression: literals, context paths (a missing one is null), ! == != && || (strings compare
    ignoring case), and always() / success() / failure() / cancelled()."""
    toks, pos = [], 0
    while pos < len(expr.rstrip()):
        m = _TOKEN.match(expr, pos)
        assert m, f"cannot read {expr[pos:]!r}"
        toks.append(m.group(1) and ("str", m.group(1)[1:-1].replace("''", "'")) or m.group(2) and ("op", m.group(2))
                    or ("name", m.group(3)))
        pos = m.end()
    i = 0

    def peek():
        return toks[i] if i < len(toks) else ("end", "")

    def take():
        nonlocal i
        i += 1
        return toks[i - 1]

    def text(v) -> str:
        return "" if v is None else ("true" if v is True else "false" if v is False else str(v))

    def primary():
        kind, v = take()
        if kind == "str":
            return v
        if (kind, v) == ("op", "("):
            r = either()
            take()
            return r
        if (kind, v) == ("op", "!"):
            return not primary()
        if peek() == ("op", "("):
            take()
            take()
            return {"always": True, "cancelled": False, "success": not ctx["failed"], "failure": ctx["failed"]}[v]
        if v in ("true", "false", "null"):
            return {"true": True, "false": False, "null": None}[v]
        cur = ctx
        for part in v.split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
        return cur

    def compare():
        a = primary()
        while peek() in (("op", "=="), ("op", "!=")):
            op = take()[1]
            b = primary()
            a = (text(a).lower() == text(b).lower()) == (op == "==")
        return a

    def both():
        a = compare()
        while peek() == ("op", "&&"):
            take()
            b = compare()
            a = b if a else a
        return a

    def either():
        a = both()
        while peek() == ("op", "||"):
            take()
            b = both()
            a = a if a else b
        return a

    return either()


def interpolate(s, ctx: dict) -> str:
    def text(v):
        return "" if v is None else ("true" if v is True else "false" if v is False else str(v))
    return re.sub(r"\$\{\{(.*?)\}\}", lambda m: text(evaluate(m.group(1), ctx)), str(s))


def test_the_expression_reader():
    ctx = {"inputs": {"a": "", "b": "x"}, "steps": {"s": {"outputs": {"hit": "true"}}}, "failed": False}
    assert evaluate("inputs.a != ''", ctx) is False and evaluate("inputs.b != ''", ctx) is True
    assert evaluate("steps.s.outputs.hit != 'true' && steps.f.outputs.ok == 'true'", ctx) is False
    assert evaluate("inputs.a || 'dflt'", ctx) == "dflt" and evaluate("!(inputs.b == 'X')", ctx) is False
    assert interpolate("k-${{ inputs.b }}-${{ steps.none.outputs.x }}", ctx) == "k-x-"


# ------------------------------------------------------------------------------------------------------- runner

class Runner:
    """Runs action.yml's steps as the Actions runner would, in tmp: RUNNER_TEMP, a workspace (the working
    directory), one GITHUB_STEP_SUMMARY, a fresh GITHUB_OUTPUT per step, an Actions cache in a directory.
    `bash_env`: shell functions every step's bash reads first (BASH_ENV), e.g. a stand-in `python`."""

    def __init__(self, tmp: Path, *, action_path: Path = ROOT, github=None, bash_env: str = "", step_env=None):
        self.tmp, self.action_path = tmp, action_path
        self.temp, self.workspace, self.store = tmp / "runner-temp", tmp / "workspace", tmp / "actions-cache"
        for d in (self.temp, self.workspace, self.store):
            d.mkdir(parents=True, exist_ok=True)
        self.summary = self.temp / "step_summary.md"
        self.github = {"action_ref": "", "action_repository": "", "action_path": str(action_path),
                       "workspace": str(self.workspace), **(github or {})}
        self.bash_env = tmp / "bash_env.sh"
        self.bash_env.write_text(bash_env, encoding="utf-8", newline="\n")        # bash takes LF line ends only
        self.step_env = step_env or {}
        self.saved: list[str] = []

    def context(self, inputs: dict, done=None) -> dict:
        spec = load()["inputs"]
        unknown = set(inputs) - set(spec)
        assert not unknown, f"not inputs of the action: {unknown}"
        given = {k: str(inputs.get(k, v.get("default", ""))) for k, v in spec.items()}
        missing = [k for k, v in spec.items() if v.get("required") and not given[k]]
        assert not missing, f"required inputs missing: {missing}"
        return {"inputs": given, "github": self.github, "runner": {"temp": str(self.temp), "os": "Linux"},
                "env": {}, "steps": dict(done or {}), "failed": False}

    def env(self, s: dict, ctx: dict, output: Path) -> dict:
        env = {k: v for k, v in os.environ.items() if not k.startswith(("GITHUB_", "RUNNER_", "INPUT_"))
               and k not in ("BASH_ENV", "VIRTUAL_ENV")}
        env.update(GITHUB_ACTION_PATH=str(self.action_path), GITHUB_OUTPUT=str(output),
                   GITHUB_STEP_SUMMARY=str(self.summary), GITHUB_ENV=str(self.temp / "github_env"),
                   GITHUB_PATH=str(self.temp / "github_path"), RUNNER_TEMP=str(self.temp),
                   GITHUB_WORKSPACE=str(self.workspace), GITHUB_ACTIONS="true", CI="true", BASH_ENV=str(self.bash_env))
        env.update(self.step_env.get(s.get("id"), self.step_env.get("*", {})))
        env.update({k: interpolate(v, ctx) for k, v in (s.get("env") or {}).items()})
        return env

    def shell(self, s: dict, ctx: dict, n: int) -> SimpleNamespace:
        assert s.get("shell") == "bash", s.get("name")
        script, output = self.temp / f"step{n}.sh", self.temp / f"output{n}"
        script.write_text(s["run"], encoding="utf-8", newline="\n")
        output.write_text("", encoding="utf-8")
        p = subprocess.run([BASH, "--noprofile", "--norc", "-eo", "pipefail", str(script)], cwd=self.workspace,
                           env=self.env(s, ctx, output), capture_output=True, encoding="utf-8", errors="replace",
                           timeout=900, check=False)
        outputs, lines = {}, output.read_text(encoding="utf-8").splitlines()
        while lines:
            ln = lines.pop(0)
            if "<<" in ln and "=" not in ln.split("<<")[0]:
                k, delim = ln.split("<<", 1)
                body = []
                while lines[0] != delim:
                    body.append(lines.pop(0))
                lines.pop(0)
                outputs[k] = "\n".join(body)
            elif ln:
                k, v = ln.split("=", 1)
                outputs[k] = v
        return SimpleNamespace(code=p.returncode, stdout=p.stdout, stderr=p.stderr, outputs=outputs)

    def uses(self, s: dict, ctx: dict) -> SimpleNamespace:
        w = {k: interpolate(v, ctx) for k, v in (s.get("with") or {}).items()}
        out = {}
        if s["uses"].startswith("actions/cache/restore@"):
            keys = [w["key"]] + [k for k in w.get("restore-keys", "").splitlines() if k.strip()]
            have = sorted(self.store.iterdir(), key=lambda d: d.stat().st_mtime_ns, reverse=True)
            hit = next((d for k in keys for d in have if d.name == k or k != w["key"] and d.name.startswith(k)),
                       None)
            out["cache-hit"] = "true" if hit is not None and hit.name == w["key"] else "false"
            if hit is not None:
                shutil.copytree(hit, w["path"], dirs_exist_ok=True)
                out["cache-matched-key"] = hit.name
        elif s["uses"].startswith("actions/cache/save@"):
            if not (self.store / w["key"]).exists():             # GitHub keeps the first cache of a key
                shutil.copytree(w["path"], self.store / w["key"])
                self.saved.append(w["key"])
        else:
            assert s["uses"].startswith("actions/setup-python@"), s["uses"]
        return SimpleNamespace(code=0, stdout="", stderr="", outputs=out)

    def run(self, inputs: dict, *, only: str | None = None, done=None) -> SimpleNamespace:
        """Every step (or the one with id `only`); the action's exit code, outputs, stdout and job summary."""
        ctx = self.context(inputs, done)
        code, stdout, stderr, ran = 0, [], [], []
        for n, s in enumerate(steps()):
            if only and s.get("id") != only:
                continue
            cond = s.get("if", "success()")
            if not re.search(r"\b(always|success|failure|cancelled)\(\)", str(cond)):
                cond = f"success() && ({cond})"
            if not evaluate(str(cond).replace("${{", "").replace("}}", ""), ctx):
                continue
            r = self.uses(s, ctx) if "uses" in s else self.shell(s, ctx, n)
            ran.append(s.get("id") or s.get("name"))
            stdout.append(r.stdout)
            stderr.append(r.stderr)
            if s.get("id"):
                ctx["steps"][s["id"]] = {"outputs": r.outputs, "outcome": "success" if r.code == 0 else "failure"}
            if r.code:
                code, ctx["failed"] = r.code, True
        outputs = {k: interpolate(v["value"], ctx) for k, v in load()["outputs"].items()}
        summary = self.summary.read_text(encoding="utf-8") if self.summary.exists() else ""
        return SimpleNamespace(code=code, outputs=outputs, stdout="".join(stdout), stderr="".join(stderr),
                               summary=summary, ran=ran, steps=ctx["steps"])


# ------------------------------------------------------------------------------------------------------- as data

def test_the_action_is_a_composite_action_with_the_inputs_and_outputs_promised():
    a = load()
    assert a["runs"]["using"] == "composite" and a["name"] and a["description"] and a["branding"]
    assert set(a["inputs"]) == INPUTS
    assert {k for k, v in a["inputs"].items() if v.get("required")} == {"board", "cpl"}
    assert a["inputs"]["fail-on"]["default"] == "red" and a["inputs"]["version"]["default"] == ""
    assert re.fullmatch(r"3\.1[0-9]", a["inputs"]["python-version"]["default"])
    assert "github.action_ref" in a["inputs"]["version"]["description"]
    assert set(a["outputs"]) >= {"exit-code", "red", "yellow"}
    assert all(s.get("shell") == "bash" for s in a["runs"]["steps"] if "run" in s)


def test_every_input_is_used_and_reaches_the_scripts_only_through_env():
    """An input spliced into a script (`${{ inputs.cpl }}` inside `run:`) would run as shell code: every input
    reaches a script as an environment variable, and every variable a step defines is used by its script."""
    used = set()
    for s in steps():
        refs = " ".join(str(v) for k in ("env", "with") for v in (s.get(k) or {}).values()) + " " + str(s.get("if", ""))
        used |= set(re.findall(r"\binputs\.([\w-]+)", refs))
        if "run" in s:
            assert "${{" not in s["run"], s["name"]
            for var in s.get("env") or {}:
                assert re.search(rf"\${{?{var}\b", s["run"]), f"{s['name']}: {var} is not used"
    assert used == INPUTS


def test_every_step_output_used_is_written_by_its_step():
    text = ACTION.read_text(encoding="utf-8")
    runs = {s["id"]: s.get("run", "") for s in steps() if s.get("id")}
    for sid, key in set(re.findall(r"steps\.([\w-]+)\.outputs\.([\w-]+)", text)):
        if sid == "restore":
            assert key == "cache-hit"                          # actions/cache/restore's own output
            continue
        assert re.search(rf'echo "{re.escape(key)}=|print\(f?"{re.escape(key)}=', runs[sid]), (sid, key)


def test_nothing_is_uploaded_and_no_html_report_is_written():
    """Licensing: EasyEDA records stay in the Actions cache and the HTML report (it draws EasyEDA pad outlines) is
    never written; the only actions used are setup-python and the cache's restore and save."""
    order = [s.get("id") or s.get("uses") for s in steps()]
    assert {s["uses"] for s in steps() if "uses" in s} <= USES
    text = ACTION.read_text(encoding="utf-8")
    assert "upload-artifact" not in text and "--report" not in text
    check, fetch = step("check")["run"], step("fetch")["run"]
    for flag in ("--github-annotations", "--junit", "--checklist", "GITHUB_STEP_SUMMARY", "--fail-on"):
        assert flag in check, flag
    assert '"$JLCPLACE" fetch --cache "$CACHE" "$BOARD"' in fetch and '"$JLCPLACE" check "$BOARD" "$CPL"' in check
    assert order.index("restore") < order.index("fetch") < order.index("check")
    save = next(s for s in steps() if s.get("uses", "").startswith("actions/cache/save@"))
    assert order.index("fetch") < steps().index(save) < order.index("check")   # saved before a red check fails
    assert save["with"]["key"] == step("restore")["with"]["key"] and "steps.key.outputs.hash" in save["with"]["key"]


# ------------------------------------------------------------------------------------------------------- steps

def test_the_cache_key_follows_the_board_and_the_bom(tmp_path):
    need_bash()
    r = Runner(tmp_path)
    board, bom = r.workspace / "b.kicad_pcb", r.workspace / "b-BOM.csv"
    board.write_text("(kicad_pcb)", encoding="utf-8")
    bom.write_text("Comment,Designator,Footprint,LCSC\n", encoding="utf-8")
    done = {"install": {"outputs": {"python": posix(sys.executable)}}}

    def key(**inputs):
        res = r.run({"board": "b.kicad_pcb", "cpl": "c.csv", **inputs}, only="key", done=done)
        assert res.code == 0, res.stderr
        return res.steps["key"]["outputs"]["hash"]

    k = key()
    assert re.fullmatch(r"[0-9a-f]{24}", k) and key() == k
    with_bom = key(bom="b-BOM.csv")
    assert with_bom != k
    board.write_text("(kicad_pcb (version 2))", encoding="utf-8")
    assert key() != k and key(bom="b-BOM.csv") != with_bom
    k2 = key(bom="b-BOM.csv")
    bom.write_text("Comment,Designator,Footprint,LCSC\nx,R1,0402,C25744\n", encoding="utf-8")
    assert key(bom="b-BOM.csv") != k2
    assert re.fullmatch(r"[0-9a-f]{24}", key(board="absent.kicad_pcb"))      # the check reports the missing file


FAKE_PYTHON = """
python() {
  echo "python $*" >> "LOG"
  if [ "$1" = "-m" ] && [ "$2" = "venv" ]; then
    mkdir -p "$3/bin"
    printf '#!/usr/bin/env bash\\necho "venv-python $*" >> "LOG"\\n' > "$3/bin/python"
    printf '#!/usr/bin/env bash\\necho "jlcplace 9.9.9"\\n' > "$3/bin/jlcplace"
    chmod +x "$3/bin/python" "$3/bin/jlcplace"
  fi
}
"""


@pytest.mark.parametrize("version, github, spec", [
    ("", {"action_ref": "v0.1.0"}, None),
    ("v0.1.0", {"action_ref": "v0.1.0"}, None),
    ("", {}, None),                                                       # uses: ./ (no ref): the files at hand
    ("v0.2.0", {"action_ref": "v0.1.0", "action_repository": "tyevco/jlcplace"},
     "jlcplace @ git+https://github.com/tyevco/jlcplace@v0.2.0"),
    ("0123abc", {"action_ref": "main", "action_repository": "someone/fork"},
     "jlcplace @ git+https://github.com/someone/fork@0123abc"),
], ids=["own-ref", "own-ref-named", "local", "other-tag", "fork-sha"])
def test_install_puts_the_actions_own_ref_or_the_version_asked_for_in_a_venv(tmp_path, version, github, spec):
    """By default the action installs itself, from its own files (github.action_ref's code, already on the runner),
    into a venv of its own under RUNNER_TEMP: the caller's Python and PATH are left alone. A second use in the job
    reuses the venv; another version replaces it."""
    need_bash()
    log = tmp_path / "log.txt"
    r = Runner(tmp_path, github=github, bash_env=FAKE_PYTHON.replace("LOG", posix(log)))
    res = r.run({"board": "b", "cpl": "c", "version": version}, only="install")
    assert res.code == 0, res.stderr
    venv = f"{r.temp}/jlcplace-venv"
    lines = log.read_text(encoding="utf-8").splitlines()
    assert lines == [f"python -m venv {venv}",
                     f"venv-python -m pip install --disable-pip-version-check --quiet {spec or str(ROOT)}"]
    assert res.steps["install"]["outputs"] == {"python": f"{venv}/bin/python", "jlcplace": f"{venv}/bin/jlcplace"}
    assert "jlcplace 9.9.9" in res.stdout
    for f in ("github_path", "github_env"):                  # nothing added to the caller's PATH or environment
        assert not (r.temp / f).exists() or not (r.temp / f).read_text(encoding="utf-8")
    r.run({"board": "b", "cpl": "c", "version": version}, only="install")
    assert len(log.read_text(encoding="utf-8").splitlines()) == 2              # reused
    r.run({"board": "b", "cpl": "c", "version": "v9.9.9"}, only="install")
    repo = github.get("action_repository") or "tyevco/jlcplace"
    assert log.read_text(encoding="utf-8").splitlines()[-1].endswith(f"git+https://github.com/{repo}@v9.9.9")


FAKE_JLCPLACE = """#!/usr/bin/env bash
if [ "$1" = "--version" ]; then echo "jlcplace 9.9.9"; exit 0; fi
if [ "$1" = "fetch" ]; then
  printf '%s\\n' "$@" > "LOG.fetch"
  mkdir -p "$3" && echo '{}' > "$3/C25744.json"
  exit "$(cat "LOG.fetch-code" 2>/dev/null || echo 0)"
fi
printf '%s\\n' "$@" > "LOG"
while [ $# -gt 0 ]; do
  case "$1" in
    --junit) echo '<testsuites name="jlcplace"/>' > "$2"; shift ;;
    --checklist) printf '# JLC preview checklist\\n\\n- [ ] **U10**\\n' > "$2"; shift ;;
    --json) printf '{"summary": {"red": 3, "yellow": 2}}' > "$2"; shift ;;
  esac
  shift
done
echo "jlcplace 0.1.0: board.kicad_pcb vs board-CPL.csv (184 rows)"
echo "RED     J1   C160411    bottom  14 of 14 EasyEDA pads land off copper"
echo "::error file=board-CPL.csv,line=4,title=jlcplace J1 off-copper::14 of 14 EasyEDA pads land off copper"
echo "184 results: 3 red, 2 yellow, 179 green, 0 verified"
echo "jlcplace: a note on stderr" >&2
exit CODE
"""


def fake_jlcplace(tmp_path, code: int = 2) -> tuple[Path, Path]:
    """A stand-in jlcplace (the check exits with `code`; the fetch with the number in <log>.fetch-code, else 0)
    that logs its arguments: (its path, the log)."""
    log, fake = tmp_path / "args.txt", tmp_path / "fake-jlcplace"
    fake.write_text(FAKE_JLCPLACE.replace("LOG", posix(log)).replace("CODE", str(code)), encoding="utf-8",
                    newline="\n")
    fake.chmod(0o755)
    return fake, log


def fake_check(tmp_path, inputs: dict, code: int = 2):
    need_bash()
    fake, log = fake_jlcplace(tmp_path, code)
    r = Runner(tmp_path)
    done = {"install": {"outputs": {"python": posix(sys.executable), "jlcplace": posix(fake)}}}
    res = r.run({"board": "board.kicad_pcb", "cpl": "board-CPL.csv", **inputs}, only="check", done=done)
    return res, log.read_text(encoding="utf-8").splitlines(), r


def test_the_check_step_passes_the_inputs_and_reports(tmp_path):
    res, args, r = fake_check(tmp_path, {"bom": "board-BOM.csv", "gerbers": "fab/gerbers", "db": "jlcplace db",
                                         "fail-on": "yellow", "args": "--origin absolute  --lcsc-field LCSC_PN"})
    out = res.steps["check"]["outputs"]
    assert res.code == 2 and out["exit-code"] == "2" and (out["red"], out["yellow"]) == ("3", "2")
    assert res.outputs["exit-code"] == "2" and res.outputs["red"] == "3" and res.outputs["yellow"] == "2"
    assert args[:3] == ["check", "board.kicad_pcb", "board-CPL.csv"]
    pairs = dict(zip(args[3:], args[4:]))
    assert pairs["--fail-on"] == "yellow" and pairs["--bom"] == "board-BOM.csv" and pairs["--gerbers"] == "fab/gerbers"
    assert pairs["--db"] == "jlcplace db" and pairs["--cache"] == f"{r.temp}/jlcplace-easyeda"   # spaces kept
    assert "--github-annotations" in args and args[-4:] == ["--origin", "absolute", "--lcsc-field", "LCSC_PN"]
    assert ET.parse(out["junit"]).getroot().get("name") == "jlcplace"
    assert Path(out["checklist"]).read_text(encoding="utf-8").startswith("# JLC preview checklist")
    # annotations reach the log; the summary (and stderr) go to the job summary, annotations do not
    assert "::error file=board-CPL.csv,line=4,title=jlcplace J1 off-copper::" in res.stdout
    s = res.summary
    assert s.startswith("### jlcplace check: exit code 2") and "184 results: 3 red, 2 yellow" in s
    assert "a note on stderr" in s and "::error" not in s
    assert "<details><summary>Preview checklist</summary>" in s and "- [ ] **U10**" in s


def test_the_check_step_leaves_out_what_is_not_given(tmp_path):
    res, args, _ = fake_check(tmp_path, {}, code=0)
    assert res.code == 0 and res.outputs["exit-code"] == "0"
    assert not {"--bom", "--gerbers", "--db"} & set(args) and args.count("--fail-on") == 1
    assert dict(zip(args, args[1:]))["--fail-on"] == "red"


def test_a_red_finding_about_the_whole_run_counts_as_red(tmp_path):
    """With gerbers, a CPL written from another origin than the Gerbers' (or Gerbers that are not the board's) is
    red for the whole run: `red` counts it, also when no row is red (an --origin that is not the Gerbers')."""
    need_bash()
    fake, _ = fake_jlcplace(tmp_path, 2)
    fake.write_text(fake.read_text(encoding="utf-8").replace(
        '{"summary": {"red": 3, "yellow": 2}}',
        '{"summary": {"red": 0, "yellow": 1}, "findings": [{"status": "origin", "verdict": "red"}]}'),
        encoding="utf-8", newline="\n")
    r = Runner(tmp_path)
    done = {"install": {"outputs": {"python": posix(sys.executable), "jlcplace": posix(fake)}}}
    res = r.run({"board": "board.kicad_pcb", "cpl": "c.csv", "gerbers": "fab/gerbers"}, only="check", done=done)
    assert res.code == 2 and (res.outputs["red"], res.outputs["yellow"]) == ("1", "1")


def test_a_check_that_cannot_run_has_no_counts(tmp_path):
    """Exit 3 (bad input) writes no JSON: the counts are empty, the exit code is the step's."""
    need_bash()
    fake = tmp_path / "fake-jlcplace"
    fake.write_text("#!/usr/bin/env bash\necho 'jlcplace: board.kicad_pcb: no such file' >&2\nexit 3\n",
                    encoding="utf-8", newline="\n")
    fake.chmod(0o755)
    r = Runner(tmp_path)
    done = {"install": {"outputs": {"python": posix(sys.executable), "jlcplace": posix(fake)}}}
    res = r.run({"board": "board.kicad_pcb", "cpl": "c.csv"}, only="check", done=done)
    assert res.code == 3 and res.outputs["exit-code"] == "3" and res.outputs["red"] == "" == res.outputs["yellow"]
    assert "no such file" in res.summary


def test_the_fetch_step_adds_the_boms_codes_and_survives_a_failed_fetch(tmp_path):
    """The BOM's codes are fetched too (JLC places what the BOM names); EasyEDA being down is a warning, and the
    check runs with the records at hand."""
    need_bash()
    fake, log = fake_jlcplace(tmp_path)
    r = Runner(tmp_path)
    (r.workspace / "b-BOM.csv").write_text("Comment,Designator,Footprint,LCSC\nx,R1,0402,C25744\ny,U1,SOT,c7519\n"
                                           "z,C1,0402,C0402\n", encoding="utf-8")
    done = {"install": {"outputs": {"python": posix(sys.executable), "jlcplace": posix(fake)}}}
    fetched = Path(f"{log}.fetch")

    def fetch(**inputs):
        res = r.run({"board": "b.kicad_pcb", "cpl": "c.csv", **inputs}, only="fetch", done=done)
        assert res.code == 0, res.stderr
        return res, fetched.read_text(encoding="utf-8").splitlines()

    res, args = fetch(bom="b-BOM.csv")
    assert res.steps["fetch"]["outputs"] == {"ok": "true"} and "::warning" not in res.stdout
    assert args == ["fetch", "--cache", f"{r.temp}/jlcplace-easyeda", "b.kicad_pcb", "C25744", "C7519"]
    assert fetch()[1] == args[:4] and fetch(bom="absent.csv")[1] == args[:4]       # no BOM, or none to read
    Path(f"{log}.fetch-code").write_text("2", encoding="utf-8")
    res, _ = fetch(bom="b-BOM.csv")
    assert res.steps["fetch"]["outputs"] == {"ok": "false"}
    assert "::warning title=jlcplace::not every EasyEDA record could be fetched (is EasyEDA down" in res.stdout


FAKE_VENV = """
python() {
  if [ "$1" = "-m" ] && [ "$2" = "venv" ]; then
    mkdir -p "$3/bin"
    printf '#!/usr/bin/env bash\\nif [ "$1 $2" = "-m pip" ]; then echo "$*" >> "LOG"; exit 0; fi\\nexec "PY" "$@"\\n' \\
      > "$3/bin/python"
    cp "FAKE" "$3/bin/jlcplace"
    chmod +x "$3/bin/python" "$3/bin/jlcplace"
  else
    "PY" "$@"
  fi
}
"""
SAVE = "Save the EasyEDA records (this repository's Actions cache, never an artifact)"


def test_the_whole_action_in_order_with_stand_ins(tmp_path):
    """Every step and condition: the records go to the Actions cache only after a fetch that got them all and only
    under a key not saved yet; a red check fails the action with its outputs set; python-version '' leaves
    setup-python out; nothing reaches the caller's PATH or environment."""
    need_bash()
    fake, log = fake_jlcplace(tmp_path)
    pip = tmp_path / "pip.txt"
    r = Runner(tmp_path, bash_env=FAKE_VENV.replace("LOG", posix(pip)).replace("PY", posix(sys.executable))
               .replace("FAKE", posix(fake)))
    (r.workspace / "b.kicad_pcb").write_text("(kicad_pcb)", encoding="utf-8")
    inputs = {"board": "b.kicad_pcb", "cpl": "c.csv"}
    Path(f"{log}.fetch-code").write_text("2", encoding="utf-8")
    res = r.run(inputs)                                                  # EasyEDA down: nothing saved
    assert res.ran == ["Set up Python", "install", "key", "restore", "fetch", "check"] and r.saved == []
    assert res.code == 2 and (res.outputs["exit-code"], res.outputs["red"], res.outputs["yellow"]) == ("2", "3", "2")
    assert pip.read_text(encoding="utf-8").splitlines() == [f"-m pip install --disable-pip-version-check --quiet "
                                                            f"{ROOT}"]
    Path(f"{log}.fetch-code").write_text("0", encoding="utf-8")
    res = r.run(inputs)
    key = f"jlcplace-easyeda-{res.steps['key']['outputs']['hash']}"
    assert SAVE in res.ran and r.saved == [key] and (r.store / key / "C25744.json").is_file()
    res = r.run({**inputs, "python-version": ""})
    assert res.ran == ["install", "key", "restore", "fetch", "check"] and r.saved == [key]       # an exact hit
    assert res.steps["restore"]["outputs"]["cache-hit"] == "true"
    assert len(pip.read_text(encoding="utf-8").splitlines()) == 1                                 # venv reused
    for f in ("github_path", "github_env"):
        assert not (r.temp / f).exists() or not (r.temp / f).read_text(encoding="utf-8")


# ------------------------------------------------------------------------------------------------------- for real

def need(path: Path) -> None:
    if not path.exists():
        pytest.skip(f"{path} is not on this machine (JLCPLACE_DIAL_ME_SPINNER / JLCPLACE_TEST_EASYEDA say where)")


@pytest.fixture(scope="module")
def real(tmp_path_factory):
    """A runner whose action is a copy of this repository's (pip builds in its source tree), its `python` the one
    running the tests, the network closed to every step but the install, and an Actions cache seeded with the
    cached EasyEDA records under an older key."""
    need_bash()
    for p in (DIAL / "kicad/main/dial-me-spinner-main.kicad_pcb", SCRATCH / "ee_raw"):
        need(p)
    tmp = tmp_path_factory.mktemp("action")
    src = tmp / "action"
    src.mkdir()
    for f in ("action.yml", "pyproject.toml", "README.md", "LICENSE"):
        shutil.copy(ROOT / f, src / f)
    shutil.copytree(ROOT / "src", src / "src", ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))
    py = posix(sys.executable)
    r = Runner(tmp, action_path=src, github={"action_ref": "v0.1.0", "action_repository": "tyevco/jlcplace"},
               bash_env=f'python() {{ "{py}" "$@"; }}\npython3() {{ "{py}" "$@"; }}\n',
               step_env={"*": NOWHERE, "install": {}})
    seed = r.store / "jlcplace-easyeda-0000"
    seed.mkdir()
    for f in (SCRATCH / "ee_raw").glob("C*.json"):
        shutil.copy(f, seed)                    # mtime now: cached not-found answers stay fresh
    return r


def board(b: str) -> str:
    return str(DIAL / f"kicad/{b}/dial-me-spinner-{b}.kicad_pcb")


def fab(b: str, what: str, before: bool = False) -> str:
    return str((SCRATCH / "fab-before" / b if before else DIAL / "fab" / b) / f"dial-me-spinner-{b}-{what}-JLCPCB.csv")


@pytest.mark.cache
def test_the_action_runs_on_the_dial_me_spinner_boards(real, capsys):
    need(Path(fab("main", "CPL", before=True)))
    r = real
    # the old main CPL: J1, J20 and J40 misplaced
    res = r.run({"board": board("main"), "cpl": fab("main", "CPL", before=True)})
    assert res.ran == ["Set up Python", "install", "key", "restore", "fetch",
                       "Save the EasyEDA records (this repository's Actions cache, never an artifact)", "check"], \
        res.stderr
    assert res.steps["fetch"]["outputs"]["ok"] == "true" and "(fetched)" not in res.stdout
    assert res.steps["restore"]["outputs"] == {"cache-hit": "false", "cache-matched-key": "jlcplace-easyeda-0000"}
    assert r.saved == [f"jlcplace-easyeda-{res.steps['key']['outputs']['hash']}"]
    assert res.code == 2 and (res.outputs["exit-code"], res.outputs["red"], res.outputs["yellow"]) == ("2", "3", "2")
    errors = re.findall(r"^::error file=[^,]*,line=\d+,title=jlcplace (\S+) off-copper::", res.stdout, re.M)
    assert sorted(errors) == ["J1", "J20", "J40"]
    assert "184 results: 3 red, 2 yellow, 179 green, 0 verified" in res.summary and "::error" not in res.summary
    assert ET.parse(res.outputs["junit"]).getroot().get("failures") == "3"
    assert json.loads(Path(res.outputs["json"]).read_text(encoding="utf-8"))["summary"]["red"] == 3
    assert not list(r.tmp.rglob("*.html"))
    # the released CPL with its BOM: clean but for two yellow parts (BZ1, R26); yellow fails with fail-on yellow
    res = r.run({"board": board("main"), "cpl": fab("main", "CPL"), "bom": fab("main", "BOM")})
    assert res.code == 0 and (res.outputs["red"], res.outputs["yellow"]) == ("0", "2"), res.stdout
    saved, spec = len(r.saved), (r.temp / "jlcplace-venv/jlcplace-spec.txt").stat().st_mtime_ns
    res = r.run({"board": board("main"), "cpl": fab("main", "CPL"), "bom": fab("main", "BOM"), "fail-on": "yellow"})
    assert res.code == 1 and res.outputs["exit-code"] == "1"
    assert res.steps["restore"]["outputs"]["cache-hit"] == "true" and len(r.saved) == saved     # nothing new to save
    assert (r.temp / "jlcplace-venv/jlcplace-spec.txt").stat().st_mtime_ns == spec                # the venv reused
    # the base board, its switch's numbering recorded in a correction database
    from jlcplace.cli import main
    db = r.workspace / "jlcplace-db"
    assert main(["confirm", board("base"), "--ref", "SW1", "--db", str(db), "--by", "test", "--source", "manual",
                 "--note", "TS-1187A: pins 1-2 and 3-4 joined", "--cache", str(r.temp / "jlcplace-easyeda"),
                 "--offline"]) == 0
    capsys.readouterr()
    res = r.run({"board": board("base"), "cpl": fab("base", "CPL"), "bom": fab("base", "BOM"), "db": "jlcplace-db"})
    assert res.code == 0 and res.outputs["red"] == "0", res.stdout
    res = r.run({"board": board("base"), "cpl": fab("base", "CPL"), "bom": fab("base", "BOM")})
    assert res.code == 2 and "SW1" in res.stdout                                                  # without it: red


@pytest.mark.cache
def test_the_action_passes_the_gerbers(real):
    jl = Path(real.temp / "jlcplace-venv")
    exe = next((p for p in (jl / "bin/jlcplace", jl / "Scripts/jlcplace.exe") if p.exists()), None)
    if exe is None:
        real.run({"board": board("main"), "cpl": fab("main", "CPL")}, only="install")
        exe = next(p for p in (jl / "bin/jlcplace", jl / "Scripts/jlcplace.exe") if p.exists())
    usage = subprocess.run([str(exe), "check", "--help"], capture_output=True, encoding="utf-8", check=False).stdout
    if "--gerbers" not in usage:
        pytest.skip("jlcplace check has no --gerbers option yet")
    res = real.run({"board": board("main"), "cpl": fab("main", "CPL"), "bom": fab("main", "BOM"),
                    "gerbers": str(DIAL / "fab/main/gerbers")})
    assert res.code == 0 and res.outputs["red"] == "0", res.stdout + res.stderr
