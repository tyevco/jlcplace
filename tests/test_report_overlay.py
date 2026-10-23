"""The overlay images (report.overlay_svg, write_overlays, svg_to_png) and the board outline they draw (board_outline,
gerber_outline): one standalone SVG per side of the whole board, as JLC's placement preview shows it, with the parts
the check flags circled. Synthetic boards and hand-made EasyEDA pads need no data; the dial-me-spinner checks read
the boards, their Gerbers and the cached EasyEDA records when they are on this machine."""
import dataclasses
import os
import re
import shutil
import struct
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from jlcplace import convention as cv
from jlcplace import report
from jlcplace.model import BOTTOM, GREEN, RED, TOP, YELLOW, Board, Correction, CplRow, EEFootprint, EEPad, NotFound, \
    Pad, Part
from jlcplace.pipeline import Resolved, check

SVG = "{http://www.w3.org/2000/svg}"
ORIGIN = (100.0, 100.0)
DIAL = Path(os.environ.get("JLCPLACE_DIAL_ME_SPINNER", "/home/user/dial-me-spinner")) / "hardware"
SCRATCH = Path(os.environ.get("JLCPLACE_TEST_EASYEDA", "/tmp/claude-0/-home-user-dial-me-spinner/"
                              "3708159a-bc3b-5167-8507-f5b08e1ce6c9/scratchpad/jlc"))
DEMOS = Path("/usr/share/kicad/demos")


def two_pad(ref, x, y, angle=0.0, layer=TOP, code="C25744"):
    pads = [Pad("1", -0.5, 0.0, 0.6, 0.6, net=f"{ref}a"), Pad("2", 0.5, 0.0, 0.6, 0.6, net=f"{ref}b")]
    return Part(ref, layer, x, y, angle, footprint="Resistor_SMD:R_0402_1005Metric", lcsc=code, pads=pads)


def sot23_6(ref, x, y, angle=0.0, layer=TOP):
    xy = [("1", -1.1375, -0.95), ("2", -1.1375, 0.0), ("3", -1.1375, 0.95),
          ("4", 1.1375, 0.95), ("5", 1.1375, 0.0), ("6", 1.1375, -0.95)]
    pads = [Pad(n, px, py, 1.325, 0.6, net=f"{ref}{n}") for n, px, py in xy]
    return Part(ref, layer, x, y, angle, footprint="Package_TO_SOT_SMD:SOT-23-6", lcsc="C7519", pads=pads)


def ee_for(part, corr):
    """The EasyEDA footprint whose correction is corr: the KiCad pads taken back through it."""
    pads = []
    for p in part.copper_pads():
        v = cv.rot((p.x - corr.dx, p.y - corr.dy), -corr.rot)
        pads.append(EEPad(p.number, v[0], v[1], p.w, p.h))
    return EEFootprint(part.lcsc, "test", pads=pads)


def scene(*extra):
    """U1 (SOT-23-6, its EasyEDA footprint drawn half a turn round) and R1 on the top, U2, R2 and R3 on the bottom,
    R9 on the top with no EasyEDA record; their rows as the corrections put them, then U1 turned back to KiCad's
    angle (red, wrong pins), R1 0.5 mm off (red, off copper) and R3 10 mm off (red, its pads far from it)."""
    parts = [sot23_6("U1", 120.0, 80.0), two_pad("R1", 110.0, 95.0, 90.0), sot23_6("U2", 140.0, 90.0, 0.0, BOTTOM),
             two_pad("R2", 105.0, 85.0, 0.0, BOTTOM), two_pad("R3", 150.0, 85.0, 0.0, BOTTOM),
             two_pad("R9", 125.0, 92.0, 0.0, TOP, code="C99999"), *extra]
    resolved = {}
    for p in parts:
        corr = Correction(rot=180.0 if p.ref.startswith("U") else 0.0, verdict=GREEN)
        ee = NotFound(p.lcsc, "Component not found") if p.ref == "R9" else ee_for(p, corr)
        resolved[p.ref] = Resolved(p, ee, None if p.ref == "R9" else corr, "" if p.ref == "R9" else "fit")
    rows = []
    for p in parts:
        corr = resolved[p.ref].corr or Correction(0.0)
        x, y, a = cv.cpl_row_for(p, corr, ORIGIN)
        rows.append(CplRow(p.ref, x, y, p.layer, a))
    board = Board("/somewhere/demo-board.kicad_pcb", ORIGIN, parts, source="test")
    return board, rows, resolved


def mutated():
    board, rows, resolved = scene()
    fix = {"U1": {"rotation": 0.0}, "R1": {"x": rows[1].x + 0.5}, "R3": {"x": rows[4].x + 10.0}}
    rows = [dataclasses.replace(r, **fix.get(r.ref, {})) for r in rows]
    run = check(board, rows, resolved, cpl_path="/somewhere/demo-CPL.csv")
    got = {r.ref: r.verdict for r in run.results}
    assert got == {"U1": RED, "R1": RED, "U2": GREEN, "R2": GREEN, "R3": RED, "R9": YELLOW}, got
    return run


def root(svg: str) -> ET.Element:
    return ET.fromstring(svg)


def numbers(d: str) -> list[tuple[float, float]]:
    v = [float(t) for t in re.findall(r"-?\d+(?:\.\d+)?", d)]
    return list(zip(v[::2], v[1::2]))


def centre(el) -> tuple[float, float]:
    pts = numbers(el.get("d"))
    return sum(x for x, _ in pts) / len(pts), sum(y for _, y in pts) / len(pts)


def by_ref(r: ET.Element, tag: str, cls: str) -> dict[str, list[ET.Element]]:
    out: dict[str, list[ET.Element]] = {}
    for el in r.iter(SVG + tag):
        if cls in (el.get("class") or "").split():
            out.setdefault(el.get("data-ref"), []).append(el)
    return out


def texts(el) -> list[str]:
    return ["".join(t.itertext()) for t in el.iter(SVG + "text")]


def group(r: ET.Element, cls: str) -> ET.Element:
    return next(g for g in r.iter(SVG + "g") if g.get("class") == cls)


# ------------------------------------------------------------------------------------------------------- drawing

def test_one_standalone_svg_per_side_and_the_bottom_seen_from_below():
    """A standalone image in px (width, height, xmlns), the board frame drawn y down; the bottom mirrored in x as
    JLC's preview shows it, so a bottom part further right on the board is further left in the image."""
    run = mutated()
    top, bottom = root(report.overlay_svg(run, TOP)), root(report.overlay_svg(run, BOTTOM))
    for r, side in ((top, TOP), (bottom, BOTTOM)):
        assert r.tag == SVG + "svg" and r.get("data-side") == side
        w, h = int(r.get("width")), int(r.get("height"))
        assert w == report.OVERLAY_WIDTH and r.get("viewBox") == f"0 0 {w} {h}" and 400 < h < 3 * w
    kp = by_ref(top, "path", "kp")
    assert set(kp) == {"U1", "R1", "R9"}                               # copper of the top's parts only
    (u1,), (r1,) = kp["U1"], kp["R1"]
    assert centre(r1)[0] < centre(u1)[0] and centre(u1)[1] < centre(r1)[1]   # x 110 < 120, y 80 < 95 (y down)
    kb = by_ref(bottom, "path", "kp")
    assert set(kb) == {"U2", "R2", "R3"}
    assert centre(kb["R2"][0])[0] > centre(kb["U2"][0])[0] > centre(kb["R3"][0])[0]   # x 105 < 140 < 150, mirrored
    assert centre(kb["R2"][0])[1] < centre(kb["U2"][0])[1]                           # y is not mirrored
    # every row's EasyEDA pads are drawn where JLC puts them, in the row's verdict colour; R9 has none
    ee = by_ref(top, "path", "ee")
    assert set(ee) == {"U1", "R1"} and "v-red" in ee["U1"][0].get("class")
    assert set(by_ref(bottom, "path", "ee")) == {"U2", "R2", "R3"}
    assert "v-green" in by_ref(bottom, "path", "ee")["U2"][0].get("class")


def test_red_and_yellow_parts_are_circled_and_labelled_in_their_colour():
    run = mutated()
    top, bottom = root(report.overlay_svg(run, TOP)), root(report.overlay_svg(run, BOTTOM))
    circled = {ref: [c.get("class") for c in cs] for ref, cs in by_ref(top, "circle", "problem").items()}
    assert circled == {"U1": ["problem red"], "R1": ["problem red"], "R9": ["problem yellow"]}
    assert set(by_ref(bottom, "circle", "problem")) == {"R3"}                         # U2, R2 green: no circle
    red, yellow = (by_ref(top, "circle", "problem")[k][0] for k in ("U1", "R9"))
    assert red.get("stroke") != yellow.get("stroke") and yellow.get("stroke-dasharray") and \
        not red.get("stroke-dasharray")
    # the circle holds the part's copper
    for ref in ("U1", "R1", "R9"):
        c = by_ref(top, "circle", "problem")[ref][0]
        cx, cy, r = (float(c.get(k)) for k in ("cx", "cy", "r"))
        x, y = centre(by_ref(top, "path", "kp")[ref][0])
        assert (x - cx) ** 2 + (y - cy) ** 2 < r * r, ref
    labels = {ref: texts(t[0])[0] for ref, t in by_ref(top, "text", "label").items()}
    assert labels == {"U1": "U1 wrong-pad", "R1": "R1 off-copper", "R9": "R9 not-simulated"}
    assert by_ref(top, "text", "label")["U1"][0].get("fill") == red.get("stroke")
    # the list under the drawing says why, worst first
    listed = texts(group(top, "problems"))
    assert listed[0] == "Circled on the top (3): look at them before ordering"
    assert [t.split()[0] for t in listed if t.split()[0] in ("U1", "R1", "R9")] == ["R1", "U1", "R9"]
    assert any(t.startswith("R1 red ") and "land off copper" in t for t in listed)


def test_pads_put_far_from_their_part_get_a_second_circle_joined_to_it():
    """R3's row is 10 mm off: one circle round its copper, one round the pads JLC would put down, and a line from one
    to the other; the label is on the part."""
    run = mutated()
    bottom = root(report.overlay_svg(run, BOTTOM))
    (part, far) = by_ref(bottom, "circle", "problem")["R3"]
    assert part.get("class") == "problem red" and far.get("class") == "problem red jlc"
    ee = centre(by_ref(bottom, "path", "ee")["R3"][0])
    kp = centre(by_ref(bottom, "path", "kp")["R3"][0])
    for c, p in ((part, kp), (far, ee)):
        cx, cy, r = (float(c.get(k)) for k in ("cx", "cy", "r"))
        assert (p[0] - cx) ** 2 + (p[1] - cy) ** 2 < r * r
    assert (kp[0] - float(far.get("cx"))) ** 2 + (kp[1] - float(far.get("cy"))) ** 2 > float(far.get("r")) ** 2
    assert len(by_ref(bottom, "line", "link")["R3"]) == 1 and len(by_ref(bottom, "text", "label")["R3"]) == 1


def test_the_source_of_the_easyeda_outlines_is_declared_in_the_image():
    """The EasyEDA pad outlines come from the JLCEDA/EasyEDA Official Library: the image says so in visible text
    (not only in metadata), with both links, and that it is for review, not for publishing."""
    run = mutated()
    for side in (TOP, BOTTOM):
        r = root(report.overlay_svg(run, side))
        notice = " ".join(texts(group(r, "source")))
        assert report.SOURCE_LIBRARY in notice and all(u in notice for u in report.SOURCE_LINKS)
        assert "do not publish" in notice
        ys = [float(t.get("y")) for t in group(r, "source").iter(SVG + "text")]
        assert ys and max(ys) < int(r.get("height"))                 # inside the image, not cut off
        assert report.SOURCE_LIBRARY in "".join(r.find(SVG + "desc").itertext())


def test_legend_outline_origins_and_header():
    run = mutated()
    square = [[(100.0, 70.0), (160.0, 70.0), (160.0, 100.0), (100.0, 100.0), (100.0, 70.0)]]
    r = root(report.overlay_svg(run, TOP, outline=square, gerber_origin=(0.0, 0.0),
                                notes=["origin mismatch: shift by (100, -100)\n::error::x"]))
    legend = " ".join(texts(group(r, "legend")))
    for words in ("KiCad copper pad", "EasyEDA pads as JLC places them", "red row", "yellow row", "circled",
                  "board outline", "CPL origin", "Gerbers' (0, 0)"):
        assert words in legend, words
    edge = by_ref(r, "path", "edge")[None]
    assert len(edge) == 1 and numbers(edge[0].get("d"))
    assert by_ref(r, "path", "mask")                                  # the closed outline is filled as the board
    assert by_ref(r, "path", "cpl-origin")
    header = texts(group(r, "header"))
    assert header[0] == "demo-board.kicad_pcb: top, seen from above"
    assert "CPL demo-CPL.csv" in header[1] and "2 red, 1 yellow, 0 green" in header[1]
    assert "/somewhere" not in " ".join(header)                       # file names, never paths
    assert any("origin mismatch: shift by (100, -100)\\x0a::error::x" in t for t in header)
    # the Gerbers' (0, 0) is far off this view: said, not drawn
    assert not by_ref(r, "path", "gerber-origin") and any("Gerbers' (0, 0)" in t for t in header)
    r = root(report.overlay_svg(run, TOP, gerber_origin=(115.0, 85.0)))
    assert by_ref(r, "path", "gerber-origin")
    r = root(report.overlay_svg(run, BOTTOM))
    assert not by_ref(r, "path", "edge") and "board outline" not in " ".join(texts(group(r, "legend")))
    assert texts(group(r, "header"))[0] == ("demo-board.kicad_pcb: bottom, seen from below as JLC's preview shows "
                                           "it (x mirrored)")


def test_too_many_problems_are_not_circled():
    """A CPL from another origin makes every row red: circles round all of them would hide the board and the shift
    the red outlines show, so past report.CIRCLES the image circles none and says why; the list still names them."""
    parts = [two_pad(f"R{i}", 105.0 + 3.0 * (i % 10), 60.0 + 3.0 * (i // 10)) for i in range(report.CIRCLES + 1)]
    corr = Correction(0.0, verdict=GREEN)
    resolved = {p.ref: Resolved(p, ee_for(p, corr), corr, "fit") for p in parts}
    board = Board("b.kicad_pcb", ORIGIN, parts)
    rows = []
    for p in parts:
        x, y, a = cv.cpl_row_for(p, corr, ORIGIN)
        rows.append(CplRow(p.ref, x + 1.0, y, p.layer, a))
    run = check(board, rows, resolved)
    assert {r.verdict for r in run.results} == {RED}
    r = root(report.overlay_svg(run, TOP, notes=["origin mismatch: shift by (-1, 0)"]))
    assert not by_ref(r, "circle", "problem") and not by_ref(r, "text", "label")
    header = texts(group(r, "header"))
    assert header[2] == "origin mismatch: shift by (-1, 0)"
    assert any(f"{report.CIRCLES + 1} red and yellow parts on this side: too many to circle" in t for t in header)
    assert len(by_ref(r, "path", "ee")) == report.CIRCLES + 1
    listed = texts(group(r, "problems"))
    assert listed[0].startswith(f"Red and yellow on the top ({report.CIRCLES + 1}):")
    assert sum(1 for t in listed if t.startswith("R") and " red " in t) == report._LIST
    run.results = run.results[:report.CIRCLES]
    assert len(by_ref(root(report.overlay_svg(run, TOP)), "circle", "problem")) == report.CIRCLES


@pytest.mark.parametrize("n", [5, report.CIRCLES + 1])
def test_easyeda_pads_far_off_the_view_are_pointed_at(n):
    """A CPL from another origin than the Gerbers (141 mm here): every EasyEDA pad lands far off the board, outside
    the view. The header says where they went and an arrow points there; nothing claims they are outlined."""
    parts = [two_pad(f"R{i}", 105.0 + 3.0 * (i % 10), 60.0 + 3.0 * (i // 10)) for i in range(n)]
    corr = Correction(0.0, verdict=GREEN)
    resolved = {p.ref: Resolved(p, ee_for(p, corr), corr, "fit") for p in parts}
    rows = []
    for p in parts:
        x, y, a = cv.cpl_row_for(p, corr, ORIGIN)
        rows.append(CplRow(p.ref, x + 100.0, y - 100.0, p.layer, a))
    run = check(Board("b.kicad_pcb", ORIGIN, parts), rows, resolved)
    r = root(report.overlay_svg(run, TOP))
    header = " ".join(texts(group(r, "header")))
    assert f"the EasyEDA pads of {n} rows land 141.4 mm from their parts, off this view" in header, header
    assert "outlined in their colour" not in header
    arrow = by_ref(r, "path", "offview")[None][0]
    (x0, y0), (x1, y1) = numbers(arrow.get("d"))[:2]
    assert x1 > x0 and y1 > y0 and abs((x1 - x0) - (y1 - y0)) < 1.0       # toward +x and down: board (+100, +100)


def test_circles_stay_in_the_drawing_on_a_tiny_board():
    """One part, its row off it: the circle round the part and its pads does not run over the header and the
    legend (circles are clipped to the drawing, and a tiny board gets a view of at least 20 mm)."""
    part = sot23_6("U1", 120.0, 80.0)
    corr = Correction(180.0, verdict=GREEN)
    x, y, a = cv.cpl_row_for(part, corr, ORIGIN)
    run = check(Board("tiny.kicad_pcb", ORIGIN, [part]), [CplRow("U1", x + 2.0, y, TOP, a)],
                {"U1": Resolved(part, ee_for(part, corr), corr, "fit")})
    assert run.results[0].verdict == RED
    svg = report.overlay_svg(run, TOP)
    r = root(svg)
    clipped = [el for g in r.iter(SVG + "g") if g.get("clip-path") == "url(#area)" for el in g.iter(SVG + "circle")]
    assert by_ref(r, "circle", "problem")["U1"] and all(el in clipped for el in by_ref(r, "circle", "problem")["U1"])
    area = next(r.iter(SVG + "clipPath")).find(SVG + "rect")
    assert float(area.get("width")) / report.OVERLAY_WIDTH < 1 and "20 mm" not in svg
    kp = by_ref(r, "path", "kp")["U1"][0]
    xs = [q[0] for q in numbers(kp.get("d"))]
    assert max(xs) - min(xs) < float(area.get("width")) / 5                # the part is a small part of the view


def test_text_from_the_files_is_escaped():
    """A reference with XML's special characters is drawn as itself and the image stays well-formed."""
    board, rows, resolved = scene(two_pad("Q&<9>", 135.0, 75.0))
    resolved["Q&<9>"] = Resolved(board.part("Q&<9>"), NotFound("C25744", "x"), None, "")
    run = check(board, rows, resolved)
    r = root(report.overlay_svg(run, TOP))
    assert texts(by_ref(r, "text", "label")["Q&<9>"][0])[0].startswith("Q&<9>")


def test_a_side_with_nothing_on_it():
    board, rows, resolved = scene()
    keep = {"U1", "R1"}
    board = Board(board.path, ORIGIN, [p for p in board.parts if p.ref in keep])
    run = check(board, [r for r in rows if r.ref in keep], {k: v for k, v in resolved.items() if k in keep})
    r = root(report.overlay_svg(run, BOTTOM))
    assert any("Nothing on the bottom" in t for t in texts(r)) and not by_ref(r, "circle", "problem")


# ------------------------------------------------------------------------------------------------------- files

def test_write_overlays_writes_top_and_bottom(tmp_path):
    run = mutated()
    out = report.write_overlays(run, tmp_path / "ov")
    assert out == {"top.svg": tmp_path / "ov/top.svg", "bottom.svg": tmp_path / "ov/bottom.svg"}
    for p in out.values():
        root(p.read_text(encoding="utf-8"))
    assert sorted(f.name for f in (tmp_path / "ov").iterdir()) == ["bottom.svg", "top.svg"]


def no_renderer(monkeypatch, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setitem(sys.modules, "cairosvg", None)              # import cairosvg raises ImportError
    monkeypatch.delenv("JLCPLACE_CHROMIUM", raising=False)
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(empty))
    for k in ("HOME", "USERPROFILE", "LOCALAPPDATA"):
        monkeypatch.setenv(k, str(empty))
    monkeypatch.setattr(report, "_installed", lambda: "")            # /Applications, Program Files: not on PATH


def test_no_png_without_a_renderer(monkeypatch, tmp_path):
    """PNG is optional: with neither cairosvg nor a headless Chromium, svg_to_png writes nothing and says so ("")."""
    run = mutated()
    out = report.write_overlays(run, tmp_path / "ov")
    no_renderer(monkeypatch, tmp_path)
    assert report.png_renderer() == ""
    assert report.svg_to_png(out["top.svg"], tmp_path / "ov/top.png") == ""
    assert report.write_overlays(run, tmp_path / "ov", png=True) == out
    assert not list((tmp_path / "ov").glob("*.png"))


def test_a_chromium_is_found_in_playwrights_browsers(monkeypatch, tmp_path):
    no_renderer(monkeypatch, tmp_path)
    for d in ("chromium_headless_shell-1194/chrome-linux/headless_shell",
              "chromium_headless_shell-1248/chrome-headless-shell-linux64/chrome-headless-shell"):
        (tmp_path / "pw" / d).parent.mkdir(parents=True)
        (tmp_path / "pw" / d).write_text("", encoding="utf-8")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "pw"))
    assert report.png_renderer() == str(tmp_path / "pw/chromium_headless_shell-1248/chrome-headless-shell-linux64/"
                                                     "chrome-headless-shell")          # the newest
    monkeypatch.setenv("JLCPLACE_CHROMIUM", "/opt/my/chrome")
    assert report.png_renderer() == "/opt/my/chrome"


def png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR"
    return struct.unpack(">II", data[16:24])


def test_a_browser_installed_where_macos_and_windows_put_it_is_found(tmp_path, monkeypatch):
    """Chrome and Edge are rarely on PATH on macOS and Windows: png_renderer looks where they install."""
    for v in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        monkeypatch.delenv(v, raising=False)
    apps = tmp_path / "Applications"
    assert report._installed(mac_roots=[apps]) == ""
    chrome = apps / "Google Chrome.app/Contents/MacOS/Google Chrome"
    chrome.parent.mkdir(parents=True)
    chrome.write_bytes(b"")
    assert report._installed(mac_roots=[apps]) == str(chrome)
    chrome.unlink()
    edge = tmp_path / "x86/Microsoft/Edge/Application/msedge.exe"
    edge.parent.mkdir(parents=True)
    edge.write_bytes(b"")
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "x86"))
    assert report._installed(mac_roots=[apps]) == str(edge)

    monkeypatch.setitem(sys.modules, "cairosvg", None)     # importing it fails
    monkeypatch.delenv("JLCPLACE_CHROMIUM", raising=False)
    monkeypatch.setattr(report, "_playwright", lambda patterns: "")
    monkeypatch.setattr(report.shutil, "which", lambda name: None)
    monkeypatch.setattr(report, "_installed", lambda: str(edge))
    assert report.png_renderer() == str(edge)


def test_png_when_a_renderer_is_at_hand(tmp_path):
    if not report.png_renderer():
        pytest.skip("no PNG renderer (cairosvg or a headless Chromium) is on this machine")
    run = mutated()
    out = report.write_overlays(run, tmp_path / "ov", png=True)
    assert set(out) == {"top.svg", "bottom.svg", "top.png", "bottom.png"}
    for side in (TOP, BOTTOM):
        r = root(out[f"{side}.svg"].read_text(encoding="utf-8"))
        assert png_size(out[f"{side}.png"]) == (int(r.get("width")), int(r.get("height")))


# ------------------------------------------------------------------------------------------------------- outline

BOARD_TEXT = """(kicad_pcb (version 20241229) (generator "pcbnew")
  (setup (aux_axis_origin 100 100))
  (gr_rect (start 100 100) (end 140 130) (stroke (width 0.05) (type default)) (fill no) (layer "Edge.Cuts"))
  (gr_circle (center 120 115) (end 122 115) (stroke (width 0.05) (type default)) (fill no) (layer "Edge.Cuts"))
  (gr_arc (start 100 140) (mid 105 145) (end 110 140) (stroke (width 0.05) (type default)) (layer "Edge.Cuts"))
  (gr_line (start 0 0) (end 10 10) (stroke (width 0.1) (type default)) (layer "F.SilkS"))
  (footprint "X:Y" (layer "F.Cu") (at 130 120 90)
    (property "Value" "a(b \\" (c")
    (fp_line (start 0 0) (end 2 0) (stroke (width 0.05) (type default)) (layer "Edge.Cuts"))
    (fp_line (start 0 0) (end 0 3) (stroke (width 0.05) (type default)) (layer "F.SilkS")))
  (footprint "X:Z" (layer "F.Cu") (at 10 10) (fp_line (start 0 0) (end 1 0) (layer "F.SilkS")))
)
"""


def test_board_outline_reads_the_edge_cuts_drawings(tmp_path):
    p = tmp_path / "b.kicad_pcb"
    p.write_text(BOARD_TEXT, encoding="utf-8")
    lines = report.board_outline(p)
    assert len(lines) == 4
    rect, circle, arc, fp = lines
    assert rect == [(100.0, 100.0), (140.0, 100.0), (140.0, 130.0), (100.0, 130.0), (100.0, 100.0)]
    assert circle[0] == circle[-1] and all(abs(((x - 120) ** 2 + (y - 115) ** 2) ** 0.5 - 2) < 1e-9 for x, y in circle)
    assert arc[0] == pytest.approx((100, 140)) and arc[-1] == pytest.approx((110, 140))
    assert max(y for _, y in arc) == pytest.approx(145.0) and all(y >= 140 - 1e-9 for _, y in arc)
    # a footprint's drawing is turned with it: at 90 deg its +x points up the board (y down)
    assert fp == [pytest.approx((130.0, 120.0)), pytest.approx((130.0, 118.0))]
    assert len(report._rings(lines)) == 2                    # the rectangle and the circle close; the rest do not
    p.write_text(BOARD_TEXT.replace('"Edge.Cuts"', '"Dwgs.User"'), encoding="utf-8")
    assert report.board_outline(p) is None
    q = tmp_path / "b.json"
    q.write_text("{}", encoding="utf-8")
    assert report.board_outline(q) is None


def test_rings_chain_segments_either_way_round():
    segs = [[(0, 0), (1, 0)], [(1, 1), (1, 0)], [(1, 1), (0, 1)], [(0, 0), (0, 1)]]
    (ring,) = report._rings(segs)
    assert ring[0] == ring[-1] and len(ring) == 5 and set(ring) == {(0, 0), (1, 0), (1, 1), (0, 1)}


def need(path: Path) -> None:
    if not path.exists():
        pytest.skip(f"{path} is not on this machine (JLCPLACE_DIAL_ME_SPINNER / JLCPLACE_TEST_EASYEDA say where)")


def bbox(lines):
    xs = [x for ln in lines for x, _ in ln]
    ys = [y for ln in lines for _, y in ln]
    return min(xs), min(ys), max(xs), max(ys)


@pytest.mark.pcbnew
@pytest.mark.parametrize("src", [DIAL / "kicad/main/dial-me-spinner-main.kicad_pcb",
                                 DIAL / "kicad/base/dial-me-spinner-base.kicad_pcb",
                                 DEMOS / "pic_programmer/pic_programmer.kicad_pcb",
                                 DEMOS / "stickhub/StickHub.kicad_pcb",
                                 DEMOS / "kit-dev-coldfire-xilinx_5213/kit-dev-coldfire-xilinx_5213.kicad_pcb",
                                 DEMOS / "custom_pads_test/custom_pads_test.kicad_pcb"],
                         ids=lambda p: p.stem)
def test_board_outline_matches_kicads_own_edge_cuts_plot(tmp_path, src):
    """kicad-cli plots Edge.Cuts from the page origin at g = (bx, -by): the outline's extent is the plot's."""
    from jlcplace.gerber import read_gerbers
    need(src)
    cli = shutil.which("kicad-cli")
    if not cli:
        pytest.skip("kicad-cli is not on this machine")
    subprocess.run([cli, "pcb", "export", "gerbers", "-l", "Edge.Cuts", "-o", str(tmp_path), str(src)], check=True,
                   capture_output=True)
    gs = read_gerbers(tmp_path)
    plot = [[(x, -y) for x, y in d.points()] for d in gs.outline.draws]
    assert bbox(report.board_outline(src)) == pytest.approx(bbox(plot), abs=0.01)


def test_gerber_outline_is_the_board_outline_on_the_released_gerbers():
    """The released main board's Gerbers (plotted from the drill/place origin), through the origin gerber_origin
    finds, outline the board where its Edge.Cuts do; without an origin there is no outline."""
    from jlcplace.board import load_board
    from jlcplace.gerber import OriginCheck, gerber_origin, read_gerbers
    src, gerbers = DIAL / "kicad/main/dial-me-spinner-main.kicad_pcb", DIAL / "fab/main/gerbers"
    need(src)
    need(gerbers)
    gs = read_gerbers(gerbers)
    chk = gerber_origin(load_board(src, reader="sexpr"), gs)
    assert chk.origin == pytest.approx((100.0, 100.0), abs=1e-3)
    assert bbox(report.gerber_outline(gs, chk)) == pytest.approx(bbox(report.board_outline(src)), abs=0.01)
    assert bbox(report.board_outline(src)) == pytest.approx((68.0, 68.0, 132.0, 132.0), abs=0.01)   # 64 mm round
    assert report.gerber_outline(gs, OriginCheck()) is None


# ------------------------------------------------------------------------------------------------- dial-me-spinner

@pytest.mark.cache
def test_old_main_cpl_overlays_circle_the_three_wrong_rows(tmp_path):
    """The old main CPL (J1, J20, J40 misplaced): red circles round exactly those, J20 on the top and J1, J40 on the
    bottom; the yellow ones (BZ1, whose polarity only pad numbers decide; R26, EasyEDA has no record) in yellow."""
    from jlcplace.board import load_board
    from jlcplace.cpl import read_cpl
    from jlcplace.easyeda import EEClient
    from jlcplace.pipeline import resolve
    src = DIAL / "kicad/main/dial-me-spinner-main.kicad_pcb"
    old = SCRATCH / "fab-before/main/dial-me-spinner-main-CPL-JLCPCB.csv"
    for p in (src, old, SCRATCH / "ee_raw"):
        need(p)
    cache = tmp_path / "cache"
    shutil.copytree(SCRATCH / "ee_raw", cache)
    board = load_board(src, reader="sexpr")
    run = check(board, read_cpl(old), resolve(board, EEClient(cache, offline=True)), cpl_path=str(old))
    outline = report.board_outline(src)
    circled = {}
    for side in (TOP, BOTTOM):
        r = root(report.overlay_svg(run, side, outline=outline))
        for ref, cs in by_ref(r, "circle", "problem").items():
            circled[ref] = (side, cs[0].get("class").split()[1])
        assert by_ref(r, "path", "mask") and by_ref(r, "path", "edge")
    assert circled == {"J20": (TOP, RED), "J1": (BOTTOM, RED), "J40": (BOTTOM, RED), "R26": (TOP, YELLOW),
                       "BZ1": (BOTTOM, YELLOW)}
