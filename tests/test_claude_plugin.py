"""The Claude Code marketplace (.claude-plugin/marketplace.json) and the jlcplace plugin (plugins/jlcplace/) stay
installable and true to the CLI: the manifests have what Claude Code needs and agree with each other and with
pyproject, every skill's frontmatter is valid, every `jlcplace ...` command in the plugin parses with the real
parser, every relative link resolves inside the plugin (only that directory reaches users), nothing copyrighted
ships, and, where the `claude` CLI is installed, `claude plugin validate --strict` passes on both and the plugin
installs from this checkout."""
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

try:
    import tomllib
except ImportError:                       # Python 3.10: pytest itself depends on tomli there
    import tomli as tomllib

import jlcplace

ROOT = Path(__file__).resolve().parents[1]
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"
PLUGIN = ROOT / "plugins" / "jlcplace"
SKILLS = sorted(p for p in (PLUGIN / "skills").iterdir() if p.is_dir())
PAGES = sorted(PLUGIN.rglob("*.md"))
REPO = "https://github.com/tyevco/jlcplace"
NAME = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
FENCE = re.compile(r"^ *```(\w*)[^\n]*\n(.*?)^ *```", re.M | re.S)
INLINE = re.compile(r"(?<!`)`(jlcplace [^`]+)`(?!`)")
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
# fields `claude plugin validate --strict` accepts without an "Unknown field" warning (checked with claude 2.1.308)
MARKET_KEYS = {"$schema", "name", "owner", "description", "metadata", "plugins"}
ENTRY_KEYS = {"name", "source", "description", "category", "tags", "homepage", "repository", "author", "license",
              "keywords"}
PLUGIN_KEYS = {"$schema", "name", "version", "description", "author", "homepage", "repository", "license", "keywords"}
SKILL_KEYS = {"name", "description", "when_to_use", "argument-hint", "allowed-tools", "disable-model-invocation",
              "user-invocable", "model", "license", "compatibility", "metadata"}


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def frontmatter(path: Path) -> tuple[dict, str]:
    yaml = pytest.importorskip("yaml")    # in the test extra; CI always has it
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path}: the first line must be ---"
    head, sep, body = text[4:].partition("\n---\n")
    assert sep, f"{path}: the frontmatter is not closed by ---"
    meta = yaml.safe_load(head)
    assert isinstance(meta, dict), f"{path}: the frontmatter is not a mapping"
    return meta, body


def rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


# ------------------------------------------------------------------------------------------------------- manifests

def test_marketplace_manifest():
    m = load(MARKETPLACE)
    assert set(m) <= MARKET_KEYS, set(m) - MARKET_KEYS
    assert m["name"] == "jlcplace" and m["owner"]["name"] == "tyevco" and m["description"]
    (entry,) = m["plugins"]
    assert set(entry) <= ENTRY_KEYS, set(entry) - ENTRY_KEYS
    assert entry["name"] == load(PLUGIN / ".claude-plugin" / "plugin.json")["name"]   # validate does not catch it
    src = entry["source"]
    assert src.startswith("./") and ".." not in src.split("/"), src
    assert (ROOT / src).resolve() == PLUGIN.resolve()
    assert "version" not in entry, "the version lives in plugin.json only (validate warns when the two differ)"
    assert entry["description"] and entry["category"] and {"kicad", "jlcpcb", "pcb"} <= set(entry["tags"])
    assert entry["repository"] == REPO and entry["homepage"].startswith("https://")


def test_plugin_manifest_agrees_with_pyproject():
    p = load(PLUGIN / ".claude-plugin" / "plugin.json")
    project = pyproject()["project"]
    assert set(p) <= PLUGIN_KEYS, set(p) - PLUGIN_KEYS
    assert NAME.fullmatch(p["name"]) and not re.match(r"(claude|anthropic|cc-plugin)", p["name"])
    # one version for the CLI and its skills: bump it with every plugin change, or installed copies never update
    assert re.fullmatch(r"\d+\.\d+\.\d+", p["version"]) and p["version"] == project["version"] == jlcplace.__version__
    assert p["license"] == project["license"] == "MIT"
    assert p["author"]["name"] == "tyevco" and p["repository"] == REPO and p["description"]
    assert {"kicad", "jlcpcb", "pick-and-place"} <= set(p["keywords"])
    assert not (PLUGIN / ".claude-plugin" / "marketplace.json").exists()
    meta = sorted(f.name for f in (PLUGIN / ".claude-plugin").iterdir())
    assert meta in (["plugin.json"], ["icon.png", "plugin.json"]), meta


@pytest.mark.parametrize("readme", [ROOT / "README.md", PLUGIN / "README.md"], ids=rel)
def test_readmes_say_how_to_install(readme):
    text = readme.read_text(encoding="utf-8")
    for line in ("/plugin marketplace add tyevco/jlcplace", "/plugin install jlcplace@jlcplace",
                 "/plugin install jlcplace --marketplace tyevco/jlcplace"):
        assert line in text, f"{rel(readme)}: no `{line}`"


# ------------------------------------------------------------------------------------------------------- skills

def test_the_skills_are_there():
    assert {s.name for s in SKILLS} == {"setup-jlcplace", "check-jlc-placement", "write-jlc-cpl", "prepare-jlc-order",
                                        "diagnose-jlc-misplacement", "review-jlc-preview"}


@pytest.mark.parametrize("skill", SKILLS, ids=lambda p: p.name)
def test_skill_frontmatter(skill):
    """Claude Code reads name and description into every session; --strict warns without them, and it does not
    check names, field types or lengths at all (so this test does)."""
    meta, body = frontmatter(skill / "SKILL.md")
    assert set(meta) <= SKILL_KEYS, set(meta) - SKILL_KEYS
    assert meta["name"] == skill.name and NAME.fullmatch(skill.name) and len(skill.name) <= 64
    desc = meta["description"]
    assert isinstance(desc, str) and 50 <= len(desc) <= 1024 and "Use when" in desc, desc
    assert len(desc) + len(meta.get("when_to_use", "")) <= 1536          # the skill listing truncates there
    assert "<" not in desc and ">" not in desc
    for key in ("argument-hint", "allowed-tools"):
        assert isinstance(meta.get(key, ""), str), f"{key} must be a string (quote it): {meta[key]!r}"
    for tool in re.findall(r"\S+\([^)]*\)|\S+", meta.get("allowed-tools", "")):
        assert re.fullmatch(r"[A-Z]\w*(\([^)]+\))?", tool), tool
        # `jlcplace fetch` stays unapproved, so it prompts. `Bash(jlcplace check *)` matches `--online` too, so that
        # option is kept out by the skills' text (test_the_skills_never_add_online), not by this list
        assert "fetch" not in tool and "--online" not in tool, "network commands are never pre-approved"
    assert body.count("\n") < 500, "keep SKILL.md short; move detail into a linked file"
    if skill.name != "setup-jlcplace":
        assert "../setup-jlcplace/SKILL.md" in body, "every skill says where to get jlcplace when it is missing"


@pytest.mark.parametrize("skill", SKILLS, ids=lambda p: p.name)
def test_the_skills_never_add_online(skill):
    """allowed-tools pre-approves `jlcplace check *` and the like with any options, `--online` among them: only the
    text keeps it out. Every mention of it says never to add it (setup-jlcplace also names it as a network option)."""
    _, body = frontmatter(skill / "SKILL.md")
    for _, block in FENCE.findall(body):
        assert "--online" not in block, f"{skill.name}: --online in a code block"
    for para in re.split(r"\n\s*\n", FENCE.sub("", body)):
        if "--online" in para:
            assert re.search(r"[Nn]ever add `--online`", para), f"{skill.name}: {para[:120]}"


def test_plugin_root_paths_exist():
    """`${CLAUDE_PLUGIN_ROOT}` is filled in by Claude Code in a plugin skill's text and allowed-tools; every path built
    on it names a file of this plugin."""
    paths = [m for page in PAGES for m in re.findall(r"\$\{CLAUDE_PLUGIN_ROOT\}(/[^\s)`\"']+)",
                                                     page.read_text(encoding="utf-8"))]
    for p in paths:
        assert (PLUGIN / p.lstrip("/")).is_file(), p
    assert "${CLAUDE_SKILL_DIR}" not in "".join(p.read_text(encoding="utf-8") for p in PAGES)


# ------------------------------------------------------------------------------------------------------- the text

def _fenced_commands(text):
    """`jlcplace ...` lines in shell code blocks, joined across trailing backslashes (as tests/test_docs.py)."""
    for lang, body in FENCE.findall(text):
        if lang not in ("sh", "bash", "shell", "console", ""):
            continue
        body = re.sub(r"\\\n\s*", " ", body)
        for line in body.splitlines():
            line = re.sub(r"^\s*\$\s+", "", line).strip()
            if line.startswith("jlcplace "):
                yield line.split(" #", 1)[0].split(" | ", 1)[0].split(" && ", 1)[0].strip()


def _subparsers(parser):
    return next(a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction").choices


def test_there_are_commands_to_check():
    assert sum(len(list(_fenced_commands(p.read_text(encoding="utf-8")))) for p in PAGES) >= 12


@pytest.mark.parametrize("page", PAGES, ids=rel)
def test_commands_in_the_plugin_parse(page):
    """Every command in a code block parses with the real CLI; every `jlcplace <command> ...` span in the text names a
    real command and only its own options (a span may leave out required arguments)."""
    from jlcplace.cli import _glued, build_parser
    parser = build_parser()
    text = page.read_text(encoding="utf-8")
    for cmd in _fenced_commands(text):
        try:
            parser.parse_args(_glued(shlex.split(cmd)[1:]))
        except SystemExit as e:
            if e.code not in (0, None):
                pytest.fail(f"{rel(page)}: `{cmd}` does not parse")
    subs = _subparsers(parser)
    prose = FENCE.sub("", text)
    for span in INLINE.findall(prose):
        words = span.split()
        if words[1].startswith("-"):
            assert words[1] in parser._option_string_actions, f"{rel(page)}: `{span}`"
            continue
        if words[1] == "..." or re.fullmatch(r"\d+(\.\d+)+", words[1]):       # `jlcplace ... | head`, a version
            continue
        assert words[1] in subs, f"{rel(page)}: `{span}`: no command {words[1]}"
        for w in words[2:]:
            opt = w.split("=", 1)[0]
            if opt.startswith("--"):
                assert opt in subs[words[1]]._option_string_actions, f"{rel(page)}: `{span}`: no option {opt}"


@pytest.mark.parametrize("page", PAGES, ids=rel)
def test_relative_links_resolve_inside_the_plugin(page):
    """Only plugins/jlcplace/ is copied into the user's plugin cache: a link out of it is dead for every user."""
    for target in LINK.findall(page.read_text(encoding="utf-8")):
        if re.match(r"[a-z][a-z0-9+.-]*:|#", target):
            continue
        path = (page.parent / target.split("#", 1)[0]).resolve()
        assert path.exists(), f"{rel(page)}: {target} does not exist"
        assert PLUGIN.resolve() in path.parents, f"{rel(page)}: {target} leaves the plugin directory"


def test_the_readme_links_to_the_plugin():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "(plugins/jlcplace/README.md)" in text and (PLUGIN / "README.md").is_file()


def test_the_plugin_ships_no_easyeda_data():
    """EasyEDA's records and the HTML report (it draws EasyEDA pads) never go into the repository, and everything under
    plugins/jlcplace/ is copied to every user."""
    allowed = {".md", ".json", ".png"}
    for f in PLUGIN.rglob("*"):
        if f.is_dir() or "__pycache__" in f.parts:
            continue
        assert f.suffix in allowed, rel(f)
        if f.suffix == ".json":
            assert f.name == "plugin.json", rel(f)
        if f.suffix == ".png":
            assert rel(f) == "plugins/jlcplace/.claude-plugin/icon.png", rel(f)


def test_the_facts_the_skills_state():
    """The exit codes, the cache location and the network rule the skills give are the CLI's."""
    from jlcplace import cli, easyeda
    setup = (PLUGIN / "skills" / "setup-jlcplace" / "SKILL.md").read_text(encoding="utf-8")
    codes = (cli.EXIT_OK, cli.EXIT_YELLOW, cli.EXIT_RED, cli.EXIT_INPUT, cli.EXIT_INTERNAL, cli.EXIT_INTERRUPTED,
             cli.EXIT_BROKEN_PIPE)
    assert codes == (0, 1, 2, 3, 4, 130, 141)
    check = (PLUGIN / "skills" / "check-jlc-placement" / "SKILL.md").read_text(encoding="utf-8")
    for code in codes:
        assert re.search(rf"\b{code}\b", setup) and re.search(rf"\b{code}\b", check), code
    assert "$JLCPLACE_CACHE" in setup and "$XDG_CACHE_HOME/jlcplace" in setup and "~/.cache/jlcplace" in setup
    assert "$JLCPLACE_CACHE, else $XDG_CACHE_HOME/jlcplace, else ~/.cache/jlcplace" in easyeda.default_cache_dir.__doc__
    assert "Only `jlcplace fetch` and the `--online` option download" in setup
    for verdict in ("red", "yellow", "green", "verified"):
        assert f"| {verdict} |" in check


# ------------------------------------------------------------------------------------------------------- version gate

def test_the_version_gate():
    """A pull request that changes the plugin (its README aside) must raise plugin.json's version."""
    spec = importlib.util.spec_from_file_location("plugin_version_gate", ROOT / ".github" / "plugin_version_gate.py")
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    assert gate.PLUGIN_JSON == rel(PLUGIN / ".claude-plugin" / "plugin.json")
    v = gate.verdict
    skill = "plugins/jlcplace/skills/setup-jlcplace/SKILL.md"
    assert v([skill], "0.1.0", "0.1.0") and "0.1.0" in v([skill], "0.1.0", "0.1.0")
    assert v([skill], "0.2.0", "0.1.9")
    assert v([skill], "0.1.0", "0.1.1") is None and v([skill], "0.9.0", "0.10.0") is None
    assert v([skill], None, "0.1.0") is None                              # the base has no plugin yet
    assert v(["src/jlcplace/cli.py", "README.md", ".claude-plugin/marketplace.json"], "0.1.0", "0.1.0") is None
    assert v(["plugins/jlcplace/README.md"], "0.1.0", "0.1.0") is None


# ------------------------------------------------------------------------------------------------------- claude CLI

def test_ci_validates_the_plugin():
    """The plugin job runs `claude plugin validate --strict` on both manifests with a pinned CLI, and the tests marked
    `claude`; every other job either leaves them out or allows their skip (no claude there)."""
    yaml = pytest.importorskip("yaml")
    jobs = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))["jobs"]
    plugin = jobs["plugin"]
    run = "\n".join(s.get("run", "") for s in plugin["steps"])
    assert re.search(r"@anthropic-ai/claude-code@\d+\.\d+\.\d+\b", run), "pin the Claude Code CLI"
    assert "claude plugin validate . --strict" in run and "claude plugin validate plugins/jlcplace --strict" in run
    assert "tests/test_claude_plugin.py" in run and "skip_gate.py junit-plugin.xml" in run
    assert not re.search(r"not claude", run)
    gate = next(s for s in plugin["steps"] if "plugin_version_gate.py" in s.get("run", ""))
    assert gate["if"] == "github.event_name == 'pull_request'" and "origin/$BASE_REF" in gate["run"]
    assert gate["env"]["BASE_REF"] == "${{ github.base_ref }}"
    assert plugin["steps"][0]["with"]["fetch-depth"] == 0
    for name, job in jobs.items():
        if name == "plugin":
            continue
        for s in job["steps"]:
            if "pytest" in s.get("run", "") and "not claude" not in s["run"]:
                allowed = re.compile(job.get("env", {}).get("ALLOW_SKIPS") or r"(?!)")
                assert allowed.search("the Claude Code CLI (claude) is not on this machine"), name


@pytest.mark.claude
@pytest.mark.parametrize("args", [["."], ["plugins/jlcplace"], ["plugins/jlcplace", "--profile", "directory"]],
                         ids=["marketplace", "plugin", "plugin-directory-profile"])
def test_claude_plugin_validate(args, tmp_path):
    """`claude plugin validate` needs no login; a throwaway HOME keeps the user's Claude config out of it. --strict
    turns every warning into a failure, but not with --profile directory, which warns until the plugin has an icon."""
    claude = shutil.which("claude")
    if claude is None:
        pytest.skip("the Claude Code CLI (claude) is not on this machine")
    env = _claude_env(tmp_path)
    strict = [] if "--profile" in args else ["--strict"]
    r = subprocess.run([claude, "plugin", "validate", *args, *strict], cwd=ROOT, env=env, capture_output=True,
                       encoding="utf-8", errors="replace", timeout=300)
    assert r.returncode == 0 and "Validation passed" in r.stdout, r.stdout + r.stderr


def _claude_env(home: Path) -> dict:
    return dict(os.environ, HOME=str(home), USERPROFILE=str(home), CLAUDE_CONFIG_DIR=str(home / ".claude"),
                CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1")


@pytest.mark.claude
def test_claude_plugin_installs_from_this_checkout(tmp_path):
    """`marketplace add` this checkout, `install`, then `details`: the six skills load, and what they add to every
    session (their names and descriptions) stays small. A throwaway HOME keeps the user's Claude config out of it."""
    claude = shutil.which("claude")
    if claude is None:
        pytest.skip("the Claude Code CLI (claude) is not on this machine")
    env = _claude_env(tmp_path)

    def run(*args):
        r = subprocess.run([claude, "plugin", *args], cwd=ROOT, env=env, capture_output=True, encoding="utf-8",
                           errors="replace", timeout=300)
        assert r.returncode == 0, f"claude plugin {' '.join(args)}: {r.stdout}{r.stderr}"
        return r.stdout

    run("marketplace", "add", str(ROOT))
    run("install", "jlcplace@jlcplace", "--scope", "user")
    details = run("details", "jlcplace")
    names = re.search(r"Skills \((\d+)\)\s+(.*)", details)
    assert names and names.group(1) == "6", details
    assert set(re.split(r",\s*", names.group(2).strip())) == {s.name for s in SKILLS}, details
    always = re.search(r"Always-on:\s+~([\d.]+)(k?) tok", details)
    assert always, details
    tokens = float(always.group(1)) * (1000 if always.group(2) else 1)
    assert tokens < 1000, f"the skill descriptions cost ~{tokens:.0f} tokens in every session: shorten them"
