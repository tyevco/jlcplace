#!/usr/bin/env python3
"""Do the Gerbers, the drill files and the CPL use the same origin?

    python3 origin_check.py GERBERS CPL

GERBERS is the Gerber directory, the zip that goes to JLC, or the Edge.Cuts (board outline) Gerber itself. CPL is the
pick-and-place file (Mid X / Mid Y in mm, Y up, as JLC reads it). JLC lines the CPL up with the Gerbers' coordinates,
so a CPL made from another origin than the Gerbers puts every part off by the same vector. This script reads the
board outline (and, in a directory or zip, the Excellon drill files) and reports whether the CPL rows and the drill
hits lie on the board the Gerbers draw. It catches a whole-file origin mismatch, not a part a few mm off: `jlcplace
check` checks each row. Standard library only; reads nothing but its two arguments.

Exit codes: 0 same origin, 1 rows or holes off the board (an origin, unit or sign mismatch), 2 bad input.
"""
import csv
import io
import math
import re
import sys
import zipfile
from pathlib import Path

MARGIN = 3.0        # mm: a connector's EasyEDA origin may sit a little past the board edge
OUTLINE = re.compile(r"edge[._]cuts|\.(gm1|gko|gml)$|profile", re.I)
DRILL = re.compile(r"\.(drl|xln|exc)$", re.I)


def read_inputs(path: Path) -> tuple[tuple[str, str], list[tuple[str, str]]]:
    """((outline name, text), [(drill name, text), ...]) from a Gerber file, a directory or a zip."""
    if path.is_dir():
        files = [(p.name, p.read_text(encoding="utf-8", errors="replace")) for p in sorted(path.iterdir())
                 if p.is_file()]
    elif zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            files = [(Path(n).name, z.read(n).decode("utf-8", errors="replace")) for n in sorted(z.namelist())
                     if not n.endswith("/")]
    else:
        return (path.name, path.read_text(encoding="utf-8", errors="replace")), []
    outlines = [f for f in files if OUTLINE.search(f[0]) and not DRILL.search(f[0]) and "map" not in f[0].lower()]
    outlines += [f for f in files if "%TF.FileFunction,Profile" in f[1] and f not in outlines]
    if not outlines:
        raise ValueError(f"{path}: no board outline (Edge.Cuts) Gerber in it")
    return outlines[0], [f for f in files if DRILL.search(f[0])]


def gerber_box(text: str) -> tuple[float, float, float, float]:
    """The bounding box (mm) of everything an RS-274X file draws, arcs included (as their whole circle)."""
    fs = re.search(r"%FS([LT])([AI])X(\d)(\d)Y(\d)(\d)\*%", text)
    if not fs:
        raise ValueError("no %FS format statement: not an RS-274X Gerber")
    trailing, digits = fs.group(1) == "T", (int(fs.group(3)) + int(fs.group(4)), int(fs.group(5)) + int(fs.group(6)))
    decimals = (int(fs.group(4)), int(fs.group(6)))
    scale = 25.4 if "%MOIN*%" in text else 1.0

    def num(s: str, axis: int) -> float:
        if trailing:
            sign = "-" if s.startswith("-") else ""
            s = sign + s.lstrip("+-").ljust(digits[axis], "0")
        return int(s) / 10 ** decimals[axis] * scale

    xs, ys = [], []
    x = y = 0.0
    arc = False
    body = re.sub(r"%[^%]*%", "", text)                      # extended commands carry no coordinates
    for st in body.split("*"):
        st = st.strip()
        if not st or st.startswith("G04"):
            continue
        g = re.match(r"G0?([123])(?!\d)", st)
        if g:
            arc = g.group(1) in "23"
        c = dict(re.findall(r"([XYIJ])([+-]?\d+)", st))
        if not c or ("D" in st and not re.search(r"D0?[123]$", st)):
            continue
        nx = num(c["X"], 0) if "X" in c else x
        ny = num(c["Y"], 1) if "Y" in c else y
        if arc and re.search(r"D0?1$", st) and ("I" in c or "J" in c):
            cx, cy = x + num(c.get("I", "0"), 0), y + num(c.get("J", "0"), 1)
            r = math.hypot(x - cx, y - cy)
            xs += [cx - r, cx + r]
            ys += [cy - r, cy + r]
        x, y = nx, ny
        xs.append(x)
        ys.append(y)
    if not xs:
        raise ValueError("the outline Gerber draws nothing")
    return min(xs), max(xs), min(ys), max(ys)


def drill_hits(text: str) -> list[tuple[float, float]]:
    """Hole positions (mm) from an Excellon file written with decimal points (KiCad's default)."""
    scale = 25.4 if re.search(r"^INCH", text, re.M) else 1.0
    out, x, y = [], None, None
    body = text.split("\n%", 1)[-1]                            # after the header, when there is one
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith(("X", "Y")):
            continue
        first = re.split(r"G85", line)[0]                   # a slot: its start point
        for axis, v in re.findall(r"([XY])([+-]?[\d.]+)", first):
            if "." not in v:
                raise ValueError("the drill file has no decimal points (export it with decimal zeros format)")
            if axis == "X":
                x = float(v) * scale
            else:
                y = float(v) * scale
        if x is not None and y is not None:
            out.append((x, y))
    return out


def length(cell: str) -> float:
    m = re.fullmatch(r"\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*(mm|mil|in|inch)?\s*", cell, re.I)
    if not m:
        raise ValueError(f"{cell!r} is not a length")
    unit = (m.group(2) or "mm").lower()
    return float(m.group(1)) * {"mm": 1.0, "mil": 0.0254, "in": 25.4, "inch": 25.4}[unit]


def cpl_points(path: Path) -> list[tuple[str, float, float]]:
    """(ref, x, y) from a CSV/TSV CPL: JLC's Mid X / Mid Y, or KiCad's PosX / PosY."""
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeError:
            continue
    names = {"x": ("midx", "posx", "centerx", "refx", "x"), "y": ("midy", "posy", "centery", "refy", "y"),
             "ref": ("designator", "ref", "reference", "refdes")}
    for delim in (",", "\t", ";"):
        rows = list(csv.reader(io.StringIO(text), delimiter=delim))
        for h, head in enumerate(rows):
            norm = [re.sub(r"[\s_.]|\(.*?\)|\[.*?\]", "", c).lower() for c in head]
            col = {k: next((norm.index(n) for n in ns if n in norm), None) for k, ns in names.items()}
            if None in col.values():
                continue
            pts = []
            for r in rows[h + 1:]:
                if len(r) > max(col.values()) and r[col["ref"]].strip():
                    pts.append((r[col["ref"]].strip(), length(r[col["x"]]), length(r[col["y"]])))
            return pts
    raise ValueError(f"{path}: no header with a designator and Mid X / Mid Y (or PosX / PosY) columns")


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0 if argv[:1] in (["-h"], ["--help"]) else 2
    gerbers, cpl = Path(argv[0]), Path(argv[1])
    try:
        (oname, otext), drills = read_inputs(gerbers)
        x0, x1, y0, y1 = gerber_box(otext)
        rows = cpl_points(cpl)
        holes = [(name, p) for name, text in drills for p in drill_hits(text)]
    except (OSError, ValueError, KeyError) as e:
        print(f"origin_check: {e}", file=sys.stderr)
        return 2
    if not rows:
        print(f"origin_check: {cpl}: no rows", file=sys.stderr)
        return 2

    def off(x: float, y: float) -> bool:
        return not (x0 - MARGIN <= x <= x1 + MARGIN and y0 - MARGIN <= y <= y1 + MARGIN)

    print(f"board outline ({oname}): x {x0:.3f} to {x1:.3f}, y {y0:.3f} to {y1:.3f} mm")
    bad = [r for r, x, y in rows if off(x, y)]
    rx, ry = [x for _, x, _ in rows], [y for _, _, y in rows]
    print(f"CPL rows ({cpl.name}, {len(rows)}): x {min(rx):.3f} to {max(rx):.3f}, y {min(ry):.3f} to {max(ry):.3f} mm; "
          f"{len(rows) - len(bad)} on the board")
    bad_holes = [p for _, p in holes if off(*p)]
    if holes:
        print(f"drill hits ({', '.join(sorted({n for n, _ in holes}))}, {len(holes)}): "
              f"{len(holes) - len(bad_holes)} on the board")
    rows_wrong = len(bad) > max(2, len(rows) // 10)
    span = max(max(rx) - min(rx), max(ry) - min(ry))
    tiny = len(rows) >= 5 and not rows_wrong and span < max(x1 - x0, y1 - y0) / 10
    if tiny:
        print(f"UNITS?: the {len(rows)} rows span only {span:.3f} mm on a board {max(x1 - x0, y1 - y0):.1f} mm across: "
              f"the CPL's numbers are probably inches, and JLC's CPL is in mm. Export it in mm.")
        return 1
    if not rows_wrong and not bad_holes:
        print("same origin: the CPL rows and the drill hits lie on the board the Gerbers draw"
              + (f" (off the board, check these rows: {', '.join(bad)})" if bad else ""))
        return 0
    if rows_wrong:
        dx, dy = (min(rx) + max(rx) - x0 - x1) / 2, (min(ry) + max(ry) - y0 - y1) / 2
        print(f"ORIGIN MISMATCH: {len(bad)} of {len(rows)} CPL rows lie off the board the Gerbers draw "
              f"({', '.join(bad[:8])}{' ...' if len(bad) > 8 else ''}); the rows' centre is ({dx:.3f}, {dy:.3f}) mm "
              f"from the outline's. The CPL and the Gerbers use different origins, or the CPL's units or Y sign are "
              f"wrong: export the Gerbers, the drill files and the CPL from the same drill/place origin.")
    if bad_holes:
        print(f"DRILL MISMATCH: {len(bad_holes)} of {len(holes)} drill hits lie off the board the Gerbers draw: "
              f"export the drill files from the same origin as the Gerbers (drill origin: drill/place file origin).")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
