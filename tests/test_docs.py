"""The documentation site (docs/) stays true to the code: the command reference is generated from the parser and
up to date, every `jlcplace ...` command shown in a page parses with the real CLI, and every sidebar link has a page.
"""
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
PAGES = sorted(p for p in DOCS.rglob("*.md") if "node_modules" not in p.parts)
FENCE = re.compile(r"^```(\w*)[^\n]*\n(.*?)^```", re.M | re.S)


def test_command_reference_is_generated_from_the_parser():
    r = subprocess.run([sys.executable, str(DOCS / "gen_cli.py"), "--check"], capture_output=True, text=True,
                       encoding="utf-8")
    assert r.returncode == 0, r.stderr


def _commands(text):
    """`jlcplace ...` lines in shell code blocks, joined across trailing backslashes, without prompts or comments."""
    for lang, body in FENCE.findall(text):
        if lang not in ("sh", "bash", "shell", "console", "yaml", "yml", ""):
            continue
        body = re.sub(r"\\\n\s*", " ", body)
        for line in body.splitlines():
            line = re.sub(r"^\s*(\$\s+|-\s+run:\s+|run:\s+)", "", line).strip()
            if line.startswith("jlcplace "):
                yield line.split(" #", 1)[0].split(" | ", 1)[0].split(" && ", 1)[0].split(" || ", 1)[0].strip()


@pytest.mark.parametrize("page", PAGES, ids=lambda p: str(p.relative_to(DOCS)))
def test_commands_in_the_docs_parse(page):
    from jlcplace.cli import _glued, build_parser
    parser = build_parser()
    for cmd in _commands(page.read_text(encoding="utf-8")):
        argv = shlex.split(cmd.replace("${{", "{").replace("}}", "}"))[1:]
        try:
            parser.parse_args(_glued(argv))
        except SystemExit as e:
            if e.code not in (0, None):
                pytest.fail(f"{page.relative_to(DOCS)}: `{cmd}` does not parse")


def test_every_sidebar_link_has_a_page():
    config = (DOCS / ".vitepress" / "config.mts").read_text(encoding="utf-8")
    links = re.findall(r"link: '(/[^']*)'", config)
    assert links
    for link in links:
        page = DOCS / (link.strip("/") or "index")
        assert page.with_suffix(".md").exists(), f"sidebar/nav link {link} has no page"
