"""Fail a CI job when pytest skipped a test for a reason the job does not allow.

    python .github/skip_gate.py junit.xml        # ALLOW_SKIPS: a regular expression the allowed reasons match

Tests that need optional data (a dial-me-spinner checkout, EasyEDA records, KiCad's pcbnew) skip when it is absent.
That is right on a laptop and wrong in a CI job that is meant to have the data: a moved fixture path would otherwise
turn the golden tests into silent skips. Every skip is listed in the job summary."""
import os
import re
import sys
import xml.etree.ElementTree as ET


def gh(s: str) -> str:                         # GitHub workflow-command data escaping
    return s.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main(path: str) -> int:
    allow = re.compile(os.environ.get("ALLOW_SKIPS") or r"(?!)")
    skipped, bad = [], []
    for tc in ET.parse(path).iter("testcase"):
        s = tc.find("skipped")
        if s is None:
            continue
        why = s.get("message", "") or (s.text or "")
        name = f"{tc.get('classname')}::{tc.get('name')}"
        skipped.append((name, why))
        if not allow.search(why):
            bad.append((name, why))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"### {len(skipped)} skipped, {len(bad)} not allowed in this job\n\n")
            f.writelines(f"- {'**not allowed** ' if (n, w) in bad else ''}`{n}`: {w}\n" for n, w in skipped)
    for n, w in bad:
        print(f"::error title=unexpected skip::{gh(n + ': ' + w)}")
    print(f"{len(skipped)} skipped, {len(bad)} not allowed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
