"""Fail a pull request that changes the Claude Code plugin without raising the plugin's version.

    python .github/plugin_version_gate.py origin/main        # the pull request's base

Claude Code updates an installed plugin only when its plugin.json version changes: a skill fix merged under the old
version never reaches the people who installed the plugin (`claude plugin update` says "already at the latest
version"). The plugin's version is jlcplace's (tests/test_claude_plugin.py keeps plugin.json, pyproject.toml and
jlcplace.__version__ equal), so a plugin change raises all three. The plugin's own README is not loaded by Claude and
may change on its own."""
import json
import subprocess
import sys

PLUGIN = "plugins/jlcplace/"
PLUGIN_JSON = PLUGIN + ".claude-plugin/plugin.json"
EXEMPT = {PLUGIN + "README.md"}


def version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in v.split("."))


def verdict(changed: list[str], base_version: str | None, head_version: str) -> str | None:
    """None when the change may go in, else what is wrong. base_version is None when the base has no plugin."""
    touched = sorted(f for f in changed if f.startswith(PLUGIN) and f not in EXEMPT)
    if not touched or base_version is None:
        return None
    if version_tuple(head_version) <= version_tuple(base_version):
        more = " ..." if len(touched) > 5 else ""
        return (f"the plugin changed ({', '.join(touched[:5])}{more}) but its version is {head_version}, not above the "
                f"base's {base_version}: installed copies update only when it rises. Raise the version in "
                f"{PLUGIN_JSON}, pyproject.toml and src/jlcplace/__init__.py")
    return None


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, encoding="utf-8").stdout


def main(base: str) -> int:
    changed = git("diff", "--name-only", f"{base}...HEAD").splitlines()
    try:
        base_version = json.loads(git("show", f"{base}:{PLUGIN_JSON}"))["version"]
    except subprocess.CalledProcessError:
        base_version = None
    with open(PLUGIN_JSON, encoding="utf-8") as f:
        head_version = json.load(f)["version"]
    why = verdict(changed, base_version, head_version)
    if why:
        print(f"::error title=plugin version::{why}")
        return 1
    print(f"plugin version {head_version} (base: {base_version or 'no plugin'})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "origin/main"))
