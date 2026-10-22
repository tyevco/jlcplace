"""The worked example the documentation shows: a small synthetic board, a known-good CPL (pick-and-place file) and a
CPL with two wrong rows, both checked with `jlcplace check --report`, and screenshots of the HTML reports.

    python docs/examples/make_examples.py               # run both checks, rewrite the four images in docs/public/img/
    python docs/examples/make_examples.py --keep DIR    # also keep the board, CPLs, records and reports in DIR
    python docs/examples/make_examples.py --no-images   # only run the checks (Playwright not needed)

Everything here is made up: the KiCad footprints, their pads and the EasyEDA-format records, which are written by hand
into a temporary record cache, as tests/test_cli.py does. No EasyEDA data is read or written, and nothing goes on the
network. The board:

    U1  SOT-23-6 (top). Its EasyEDA footprint is drawn turned half a turn, so its correct row has rotation 180.
    J1  6-pin FPC connector (top). Its EasyEDA origin sits 1.3 mm from KiCad's anchor, so its row is not at the
        anchor.
    Q1  SOT-23 (bottom). Its EasyEDA footprint is drawn turned a quarter turn.
    R1, R2, C1, C2  two-pad passives (top).
    H1-H4 (plated mounting holes) and TP1-TP3 (bottom test pads) carry no LCSC code: JLC does not place them.

example-CPL-good.csv has the rows the corrections give (convention.cpl_row_for). example-CPL-bad.csv is the same file
with the rows of U1 and J1 as KiCad's own position file gives them: U1 at KiCad's angle (0), J1 at KiCad's anchor.

The HTML report carries the time it was made; the script sets it to STAMP so that the images do not change from run
to run. The images are cut from the reports in a 720 px wide window (about the width of a docs page's text column)
drawn at 5/3 scale, so the PNGs are 1173 px wide; light theme.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from jlcplace.board import save_snapshot
from jlcplace.convention import cpl_row_for
from jlcplace.cpl import write_cpl
from jlcplace.model import BOTTOM, TOP, Board, Correction, CplRow, Pad, Part

HERE = Path(__file__).resolve().parent
IMG = HERE.parent / "public" / "img"
TRANSCRIPT = HERE / "transcript.txt"
STAMP = "2026-01-01 12:00"             # the report's time stamp, fixed
ORIGIN = (100.0, 118.0)                # the aux origin: lower-left corner of a 26 x 18 mm board
BOARD, GOOD, BAD = "example.json", "example-CPL-good.csv", "example-CPL-bad.csv"
CACHE = "cache"
WIDTH, SCALE, MARGIN = 720, 5 / 3, 8   # CSS px window width (about a docs page's column), pixel ratio, margin

# ------------------------------------------------------------------------------------------------------- KiCad side
# Pads are in the footprint's library frame (mm, y down), as model.Pad has them.

SOT23_6 = [("1", -1.1375, -0.95, "BST"), ("2", -1.1375, 0.0, "GND"), ("3", -1.1375, 0.95, "FB"),
           ("4", 1.1375, 0.95, "EN"), ("5", 1.1375, 0.0, "VIN"), ("6", 1.1375, -0.95, "SW")]
SOT23 = [("1", -1.1375, -0.95, "LED_EN"), ("2", -1.1375, 0.95, "GND"), ("3", 1.1375, 0.0, "LED_K")]   # G, S, D
FPC_PINS = [(str(i + 1), -2.5 + i, 0.0) for i in range(6)]          # 1.0 mm pitch
FPC_MP = [(-4.1, 2.4), (4.1, 2.4)]                                  # mounting tabs, numbered MP in KiCad
FPC_NETS = ("VIN", "GND", "EN", "VOUT", "SDA", "SCL")


def two_pad(ref, x, y, angle, code, footprint, value, half, w, h, nets):
    return Part(ref, TOP, x, y, angle, footprint=footprint, value=value, lcsc=code, lcsc_field="LCSC",
                pads=[Pad("1", -half, 0.0, w, h, shape="roundrect", rr=0.25, net=nets[0]),
                      Pad("2", half, 0.0, w, h, shape="roundrect", rr=0.25, net=nets[1])])


def make_board() -> Board:
    parts = [
        Part("U1", TOP, 108.0, 110.5, 0.0, footprint="Example:SOT-23-6", value="BUCK", lcsc="C1001",
             lcsc_field="LCSC",
             pads=[Pad(n, x, y, 1.325, 0.6, shape="roundrect", rr=0.25, net=net, pin_name=net)
                   for n, x, y, net in SOT23_6]),
        Part("J1", TOP, 113.0, 103.2, 0.0, footprint="Example:FPC-6_P1.0mm", value="FPC-6", lcsc="C1004",
             lcsc_field="LCSC",
             pads=[Pad(n, x, y, 0.6, 1.6, net=net) for (n, x, y), net in zip(FPC_PINS, FPC_NETS)]
             + [Pad("MP", x, y, 1.8, 2.0) for x, y in FPC_MP]),
        two_pad("C1", 108.0, 114.2, 0.0, "C1003", "Example:C_0402", "10u", 0.48, 0.56, 0.62, ("VIN", "GND")),
        two_pad("C2", 104.8, 110.5, 90.0, "C1003", "Example:C_0402", "100n", 0.48, 0.56, 0.62, ("BST", "SW")),
        two_pad("R1", 114.0, 110.5, 0.0, "C1002", "Example:R_0603", "100k", 0.825, 0.8, 0.95, ("VOUT", "FB")),
        two_pad("R2", 118.0, 112.0, 90.0, "C1002", "Example:R_0603", "22k", 0.825, 0.8, 0.95, ("FB", "GND")),
        Part("Q1", BOTTOM, 119.0, 113.0, 0.0, footprint="Example:SOT-23", value="NMOS", lcsc="C1005",
             lcsc_field="LCSC",
             pads=[Pad(n, x, y, 1.325, 0.6, shape="roundrect", rr=0.25, net=net, pin_name=pin)
                   for (n, x, y, net), pin in zip(SOT23, "GSD")]),
    ]
    for i, (x, y) in enumerate(((102.5, 102.5), (123.5, 102.5), (102.5, 115.5), (123.5, 115.5))):
        parts.append(Part(f"H{i + 1}", TOP, x, y, 0.0, footprint="Example:MountingHole_M2_Pad", exclude_bom=True,
                          exclude_pos=True, tht=True,
                          pads=[Pad("1", 0.0, 0.0, 3.2, 3.2, shape="circle", kind="tht", net="GND")]))
    for i, ((x, y), net) in enumerate(zip(((106.0, 104.5), (121.0, 105.5), (106.5, 115.5)), ("VIN", "VOUT", "GND"))):
        parts.append(Part(f"TP{i + 1}", BOTTOM, x, y, 0.0, footprint="Example:TestPoint_Pad_D1.5mm",
                          exclude_bom=True, exclude_pos=True,
                          pads=[Pad("1", 0.0, 0.0, 1.5, 1.5, shape="circle", net=net)]))
    return Board(BOARD, ORIGIN, parts, source="json")


#: what each code's EasyEDA footprint is to KiCad's: library = R(rot) . ee + (dx, dy)
CORRECTIONS = {
    "C1001": Correction(180.0),                 # SOT-23-6 drawn half a turn round
    "C1002": Correction(0.0),
    "C1003": Correction(0.0),
    "C1004": Correction(0.0, 0.0, 1.3),         # FPC: the EasyEDA origin is 1.3 mm from KiCad's anchor
    "C1005": Correction(270.0),                 # SOT-23 drawn a quarter turn round
}

# ------------------------------------------------------------------------------------------------------- EasyEDA side
# Hand-made records in the API's format (easyeda.py): RECT pads in canvas units of 10 mil, y down, around the
# footprint origin head.x, head.y. Pad lists are [(number, x, y, w, h, rotation)] in the ee frame (mm).

HX, HY = 4000.0, 3000.0


def record(uuid: str, title: str, pads) -> dict:
    shapes = []
    for i, (n, x, y, w, h, r) in enumerate(pads):
        px, py = HX + x / 0.254, HY + y / 0.254
        shapes.append("~".join(["PAD", "RECT", f"{px:.6f}", f"{py:.6f}", f"{w / 0.254:.6f}", f"{h / 0.254:.6f}", "1",
                                "", n, "0", "", f"{r:g}", f"gge{i}", "0", "", "Y", "0", "0", "0.2", f"{px},{py}"]))
    return {"success": True, "code": 0, "result": {
        "uuid": f"sym-{uuid}", "title": title, "dataStr": {"head": {"x": 0, "y": 0}, "shape": []},
        "packageDetail": {"uuid": uuid, "title": title, "updateTime": 1767268800,
                          "dataStr": {"head": {"x": HX, "y": HY}, "shape": shapes}}}}


RECORDS = {
    # pad n of KiCad's SOT-23-6 sits at (-x, -y): the same footprint turned half a turn
    "C1001": record("example-sot23-6", "EXAMPLE_SOT-23-6", [(n, -x, -y, 1.2, 0.6, 0) for n, x, y, _ in SOT23_6]),
    "C1002": record("example-r0603", "EXAMPLE_R0603", [("1", -0.8, 0.0, 0.85, 0.9, 0), ("2", 0.8, 0.0, 0.85, 0.9, 0)]),
    "C1003": record("example-c0402", "EXAMPLE_C0402", [("1", -0.5, 0.0, 0.6, 0.6, 0), ("2", 0.5, 0.0, 0.6, 0.6, 0)]),
    # the origin at the middle of the pads, 1.3 mm from KiCad's anchor (the middle of the pin row); tabs 7 and 8
    "C1004": record("example-fpc6", "EXAMPLE_FPC-6P-1.0", [(n, x, y - 1.3, 0.6, 1.5, 0) for n, x, y in FPC_PINS]
                    + [(str(7 + i), x, y - 1.3, 1.8, 2.0, 0) for i, (x, y) in enumerate(FPC_MP)]),
    # KiCad's SOT-23 with pins 1, 2 on the left; this one has them along the bottom
    "C1005": record("example-sot23", "EXAMPLE_SOT-23", [("1", -0.95, 1.1, 0.6, 1.2, 0), ("2", 0.95, 1.1, 0.6, 1.2, 0),
                                                        ("3", 0.0, -1.1, 0.6, 1.2, 0)]),
}

# ------------------------------------------------------------------------------------------------------- the run


def write_inputs(work: Path) -> None:
    board = make_board()
    save_snapshot(board, work / BOARD)
    cache = work / CACHE
    cache.mkdir(exist_ok=True)
    for code, rec in RECORDS.items():
        (cache / f"{code}.json").write_text(json.dumps(rec, indent=1) + "\n", encoding="utf-8")
    good = []
    for part in sorted((p for p in board.parts if p.lcsc), key=lambda p: p.ref):
        x, y, a = cpl_row_for(part, CORRECTIONS[part.lcsc], board.origin)
        good.append(CplRow(part.ref, x, y, part.layer, a))
    write_cpl(work / GOOD, good)
    bad = []
    for r in good:
        part = board.part(r.ref)
        if r.ref == "U1":                                   # KiCad's angle: the SOT-23-6 half a turn off
            r = CplRow(r.ref, r.x, r.y, r.layer, part.angle)
        elif r.ref == "J1":                                 # KiCad's anchor: the connector 1.3 mm off
            r = CplRow(r.ref, part.x - ORIGIN[0], ORIGIN[1] - part.y, r.layer, r.rotation)
        bad.append(r)
    write_cpl(work / BAD, bad)


def jlcplace() -> list[str]:
    """The jlcplace command next to this Python (a virtual environment's bin/ or Scripts/), else `python -m`."""
    exe = shutil.which("jlcplace", path=str(Path(sys.executable).parent))
    return [exe] if exe else [sys.executable, "-m", "jlcplace"]


def run_check(work: Path, cpl: str, html: str) -> tuple[str, int, str]:
    args = ["check", BOARD, cpl, "--cache", CACHE, "--report", html]
    env = {**os.environ, "NO_COLOR": "1", "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run(jlcplace() + args, cwd=work, capture_output=True, text=True, encoding="utf-8", env=env,
                       check=False)                        # exit 2 is the expected answer for the bad CPL
    if r.stderr.strip() or r.returncode not in (0, 1, 2):
        sys.exit(f"make_examples: jlcplace {' '.join(args)} exited {r.returncode}:\n{r.stdout}{r.stderr}")
    page = work / html
    text, n = re.subn(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}, jlcplace ", f"{STAMP}, jlcplace ", page.read_text("utf-8"))
    if n != 1:
        sys.exit(f"make_examples: no time stamp found in {page}")
    page.write_text(text, encoding="utf-8")
    return "jlcplace " + shlex.join(args), r.returncode, r.stdout


def expect(output: str, code: int, want_code: int, red: set[str]) -> None:
    """Stop when jlcplace no longer says what the docs say of the example: these red rows, no yellow ones."""
    got = {v: {ln.split()[1] for ln in output.splitlines() if ln.startswith(v + " ")} for v in ("RED", "YELLOW")}
    if code != want_code or got != {"RED": red, "YELLOW": set()}:
        sys.exit(f"make_examples: expected exit {want_code}, red {sorted(red)} and no yellow; got exit {code}, red "
                 f"{sorted(got['RED'])} and yellow {sorted(got['YELLOW'])}:\n{output}")


# ------------------------------------------------------------------------------------------------------- images

_RECT = """sel => { const r = document.querySelector(sel).getBoundingClientRect();
                    return [r.left, r.top + window.scrollY, r.right, r.bottom + window.scrollY]; }"""


def _card(page, ref: str) -> str:
    """The CSS selector of a part's card (cards are details.card#rN, N the result's index)."""
    anchor = page.evaluate("""ref => { for (const d of document.querySelectorAll('details.card'))
                                         if (d.querySelector('summary > b').textContent === ref) return d.id;
                                       return null; }""", ref)
    if not anchor:
        sys.exit(f"make_examples: no card for {ref}")
    return f"#{anchor}"


def _shoot(page, path: Path, x0, y0, x1, y1) -> None:
    page.screenshot(path=str(path), full_page=True, animations="disabled", caret="hide",
                    clip={"x": x0 - MARGIN, "y": y0 - MARGIN, "width": x1 - x0 + 2 * MARGIN,
                          "height": y1 - y0 + 2 * MARGIN})


def images(work: Path) -> list[Path]:
    from playwright.sync_api import sync_playwright

    IMG.mkdir(parents=True, exist_ok=True)
    out = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": WIDTH, "height": 1000}, device_scale_factor=SCALE,
                                    color_scheme="light", reduced_motion="reduce")
            page.goto((work / "report-bad.html").as_uri())
            # the top of the report: title, counts, the source notice, both board views and the note under them
            x0, y0, x1, _ = page.evaluate(_RECT, "main > h1")
            y1 = page.evaluate(_RECT, ".boards + p.note")[3]
            out.append(IMG / "report-overview.png")
            _shoot(page, out[-1], x0, y0, x1, y1)
            for ref, name in (("J1", "card-offset.png"), ("U1", "card-rotated.png")):
                out.append(IMG / name)
                _shoot(page, out[-1], *page.evaluate(_RECT, _card(page, ref)))
            page.goto((work / "report-good.html").as_uri())
            sel = _card(page, "U1")
            page.click(f"{sel} > summary")                   # green cards start closed
            out.append(IMG / "card-ok.png")
            _shoot(page, out[-1], *page.evaluate(_RECT, sel))
        finally:
            browser.close()
    return out


# ------------------------------------------------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--keep", metavar="DIR", help="write the board, CPLs, records and reports into DIR and keep them")
    ap.add_argument("--no-images", action="store_true", help="only run the checks; leave docs/public/img/ alone")
    args = ap.parse_args(argv)
    tmp = None
    if args.keep:
        work = Path(args.keep).resolve()
        work.mkdir(parents=True, exist_ok=True)
    else:
        tmp = tempfile.TemporaryDirectory(prefix="jlcplace-example-")
        work = Path(tmp.name)
    try:
        write_inputs(work)
        transcript = []
        for cpl, html, want_code, red in ((GOOD, "report-good.html", 0, set()),
                                          (BAD, "report-bad.html", 2, {"U1", "J1"})):
            cmd, code, output = run_check(work, cpl, html)
            expect(output, code, want_code, red)
            transcript.append(f"$ {cmd}\n{output}[exit code {code}]\n")
        text = "\n".join(transcript)
        sys.stdout.write(text)
        TRANSCRIPT.write_text(text, encoding="utf-8", newline="\n")
        if not args.no_images:
            for p in images(work):
                print(f"wrote {p.relative_to(HERE.parent.parent)}")
        if args.keep:
            print(f"kept the board, CPLs, records and reports in {work}")
    finally:
        if tmp is not None:
            tmp.cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(main())
