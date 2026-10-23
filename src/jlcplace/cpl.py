"""Read and write CPL (pick-and-place) and BOM files, and make the rows jlcplace sends to JLC.

Rows live in the cpl frame (model): mm relative to the CPL origin, y up, X not mirrored on the bottom. `read_cpl`
takes JLC's own header (Designator, Mid X, Mid Y, Layer, Rotation) and the spellings other tools write, comma,
semicolon or tab separated, and KiCad's position files (`kicad-cli pcb export pos`, CSV and ASCII). A value with a
unit ("12.5mm", "500mil", "0.5in") is converted; a bare number takes the unit of its column header ("Center-X(mil)"),
of KiCad's "## Unit =" line, or `units`. KiCad's CSV position file does not say its unit: pass units="in" for one
exported with --units in. A KiCad position file carries KiCad's own angle with no JLC correction; checking one is how
a naive CPL is caught.
"""

from __future__ import annotations

import csv
import io
import math
import os
import re
import secrets

from .convention import cpl_row_for, norm_angle
from .lcsc import normalize as normalize_lcsc
from .model import BOTTOM, TOP, Board, Correction, CplRow, Part, printable, ref_problem

UNITS = {"mm": 1.0, "mil": 0.0254, "in": 25.4}
_UNIT_NAMES = {"mm": "mm", "millimeter": "mm", "millimeters": "mm", "mil": "mil", "mils": "mil", "thou": "mil",
               "th": "mil", "in": "in", "inch": "in", "inches": "in", '"': "in"}

# normalised header (lower case without spaces _ - . and a leading #) -> field
_CPL_COLUMNS = {
    "designator": "ref", "designators": "ref", "ref": "ref", "reference": "ref", "refdes": "ref",
    "midx": "x", "posx": "x", "centerx": "x", "centrex": "x", "x": "x", "positionx": "x", "locationx": "x",
    "midy": "y", "posy": "y", "centery": "y", "centrey": "y", "y": "y", "positiony": "y", "locationy": "y",
    "layer": "layer", "side": "layer", "tb": "layer", "mountside": "layer", "mountingside": "layer",
    "boardside": "layer", "placementside": "layer",
    "rotation": "rotation", "rot": "rotation", "angle": "rotation", "orientation": "rotation", "rotate": "rotation",
}
_BOM_COLUMNS = {
    "designator": "designators", "designators": "designators", "reference": "designators",
    "references": "designators", "ref": "designators", "refs": "designators", "refdes": "designators",
    "comment": "comment", "value": "comment", "val": "comment",
    "footprint": "footprint", "package": "footprint", "pattern": "footprint",
    "lcsc": "lcsc", "lcscpart": "lcsc", "lcscpart#": "lcsc", "lcscpartnumber": "lcsc", "lcsc#": "lcsc",
    "lcscpn": "lcsc", "lcscpartno": "lcsc", "jlcpcbpart": "lcsc", "jlcpcbpart#": "lcsc", "jlcpcbpn": "lcsc",
    "jlcpcbpartnumber": "lcsc", "supplierpart": "lcsc", "supplierpart#": "lcsc",
    "qty": "qty", "quantity": "qty", "count": "qty",
}
_TOP = {"top", "t", "f", "front", "fcu", "toplayer", "topside", "component", "componentside"}
_BOTTOM = {"bottom", "b", "bot", "back", "bcu", "bottomlayer", "bottomside", "solder", "solderside"}
_HEADER_UNIT = re.compile(r"[(\[]\s*([a-z\"]+)\s*[)\]]")
_NUMBER = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?")
_KICAD_REF_HEADER = re.compile(r"#\s*ref\s", re.I)


# ------------------------------------------------------------------------------------------------------- helpers

def natural_key(ref: str):
    """Sort key: R2 before R10, text case-insensitive, then the exact string to break ties."""
    return [int(t) if t.isdigit() else t.casefold() for t in re.split(r"(\d+)", ref)], ref


def assembled(part: Part) -> bool:
    """JLC assembles it: placed (not DNP, not excluded from position files, not board-only) and in the BOM."""
    return part.placed and not part.exclude_bom


def jlc_places(part: Part, bom_refs=None, cpl_refs=None) -> bool:
    """JLC places it, as far as the board, the JLC BOM's designators (`bom_refs`, None: no BOM given) and the CPL's
    (`cpl_refs`) tell. JLC never sees KiCad's attributes and places what its BOM and CPL list: never a DNP or
    board-only part; an assembled one always; one the board excludes from its position files or its BOM when the BOM
    lists it, or, with no BOM given, when the board only excludes it from its position files (its own BOM lists it)
    and the CPL lists it."""
    if part.dnp or part.board_only:
        return False
    if assembled(part):
        return True
    if bom_refs is not None:
        return part.ref in bom_refs
    return not part.exclude_bom and cpl_refs is not None and part.ref in cpl_refs


def _finite(v: float, text) -> float:
    if not math.isfinite(v):
        raise ValueError(f"not a finite number: {text!r}")
    return v


def parse_length(text: str, unit: str = "mm") -> float:
    """mm from '12.5', '12.5mm', '500 mil', '0.5in', '12,5' (decimal comma); a bare number is in `unit`. ValueError
    for anything else, and for a number too large to be finite ('1e400')."""
    s = str(text).strip().lower()
    if "," in s and "." not in s:
        s = s.replace(",", ".")
    m = _NUMBER.match(s)
    if not m:
        raise ValueError(f"not a length: {text!r}")
    rest = s[m.end():].strip()
    if rest:
        if rest not in _UNIT_NAMES:
            raise ValueError(f"unknown unit in {text!r}")
        unit = _UNIT_NAMES[rest]
    return _finite(float(m.group(0)) * UNITS[unit], text) + 0.0


def parse_angle(text: str) -> float:
    """Degrees in [0, 360) from '90', '-90.0', '90°', '90 deg'. ValueError for anything else, 'nan', 'inf' and
    numbers too large to be finite among them (norm_angle would make them nan)."""
    s = str(text).strip().lower().replace("°", "")
    s = (s[:-3] if s.endswith("deg") else s).strip()
    if "," in s and "." not in s:
        s = s.replace(",", ".")
    try:
        a = float(s)
    except ValueError:
        raise ValueError(f"not an angle: {text!r}") from None
    return norm_angle(_finite(a, text))


def parse_layer(text: str) -> str:
    """TOP or BOTTOM from Top/Bottom/T/B/F/Front/Back/F.Cu/B.Cu/TopLayer/BottomLayer, any case."""
    s = re.sub(r"[\s_\-.]+", "", str(text).strip().lower())
    if s in _TOP:
        return TOP
    if s in _BOTTOM:
        return BOTTOM
    raise ValueError(f"unknown layer {text!r}")


def _norm_header(name: str) -> tuple[str, str | None]:
    """(key, unit) of a column header: 'Center-X(mm)' -> ('centerx', 'mm'), '# Ref' -> ('ref', None)."""
    s = name.replace("﻿", "").strip().lower()
    unit = None
    m = _HEADER_UNIT.search(s)
    if m and m.group(1) in _UNIT_NAMES:
        unit = _UNIT_NAMES[m.group(1)]
        s = s[:m.start()] + s[m.end():]
    return re.sub(r"[\s_\-.]+", "", s).lstrip("#"), unit


_UTF_BOMS = ((b"\xff\xfe\x00\x00", "utf-32"), (b"\x00\x00\xfe\xff", "utf-32"), (b"\xff\xfe", "utf-16"),
              (b"\xfe\xff", "utf-16"))


def read_text(path) -> str:
    """A CSV's text: UTF-16 / UTF-32 when it starts with their byte order mark (Excel's 'Unicode Text'), else UTF-8
    (with or without a BOM), else cp1252. Line ends are left as they are (CR-only files are read by _table)."""
    with open(path, "rb") as f:
        data = f.read()
    for bom, codec in _UTF_BOMS:
        if data.startswith(bom):
            try:
                return data.decode(codec)
            except UnicodeDecodeError:
                break
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _table(text: str, columns: dict, required: tuple[str, ...], path=""):
    """(rows, header index, {field: column}, {column: unit}) with the first of comma, tab and semicolon under which
    some row names every required field (so a preamble line or decimal commas do not decide it), else None. LF,
    CRLF and CR-only line ends (Excel for Mac's 'CSV (Macintosh)') alike; ValueError for text csv cannot read."""
    for delim in (",", "\t", ";"):
        try:
            rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=delim))
        except csv.Error as e:
            raise ValueError(f"{path}: not a readable CSV ({e})") from None
        found = _find_header(rows, columns, required)
        if found:
            return (rows, *found)
    return None


def _find_header(rows: list[list[str]], columns: dict, required: tuple[str, ...]):
    """(index, {field: column}, {column: unit}) of the first row that names every required field, else None."""
    for i, row in enumerate(rows):
        fields, units = {}, {}
        for j, cell in enumerate(row):
            key, unit = _norm_header(cell)
            f = columns.get(key)
            if f and f not in fields:
                fields[f] = j
                units[j] = unit
        if all(f in fields for f in required):
            return i, fields, units
    return None


def _designator(ref: str) -> str:
    """ref, unless it cannot be a designator (model.ref_problem): ValueError."""
    why = ref_problem(ref)
    if why:
        raise ValueError(f"{ref!r} is not a designator: {why}")
    return ref


def _extra(header: list[str], row: list[str], known) -> dict[str, str]:
    return {header[j].strip(): row[j] for j in range(min(len(row), len(header))) if j not in known}


# ------------------------------------------------------------------------------------------------------- CPL

def read_cpl(path, *, units: str = "mm") -> list[CplRow]:
    """Every row of a CPL or KiCad position file, in file order (duplicates kept: the check reports them). Other
    columns go to `extra`, keyed by their header. ValueError for a file without the five columns or a value that
    does not parse."""
    if units not in UNITS:
        raise ValueError(f"units must be one of {sorted(UNITS)}")
    text = read_text(path)
    if _is_kicad_ascii(text):
        return _read_kicad_ascii(text, units)
    found = _table(text, _CPL_COLUMNS, ("ref", "x", "y", "layer", "rotation"), path)
    if found is None:
        raise ValueError(f"{path}: no header with Designator, Mid X, Mid Y, Layer and Rotation columns")
    rows, h, fields, col_units = found
    known = set(fields.values())
    out = []
    for n, row in enumerate(rows[h + 1:], start=h + 2):
        if not any(c.strip() for c in row) or row[0].lstrip().startswith("#"):
            continue
        cell = {f: (row[j].strip() if j < len(row) else "") for f, j in fields.items()}
        if not cell["ref"]:
            continue
        try:
            _designator(cell["ref"])
            x = parse_length(cell["x"], col_units[fields["x"]] or units)
            y = parse_length(cell["y"], col_units[fields["y"]] or units)
            out.append(CplRow(cell["ref"], x, y, parse_layer(cell["layer"]), parse_angle(cell["rotation"]),
                              _extra(rows[h], row, known)))
        except ValueError as e:
            raise ValueError(f"{path}: line {n} ({printable(cell['ref'])}): {e}") from None
    return out


def _is_kicad_ascii(text: str) -> bool:
    """KiCad's ASCII position file starts with '#' comment lines, one of them the whitespace-separated header."""
    for ln in text.splitlines():
        s = ln.strip()
        if not s:
            continue
        if not s.startswith("#"):
            return False
        if s.startswith("## Unit") or s.startswith("### Footprint positions"):
            return True
        if _KICAD_REF_HEADER.match(s + " "):
            return not any(d in s for d in ",;\t")    # a CSV whose header starts '# Ref' (KiKit's OpenPnP) is not
    return False


def _read_kicad_ascii(text: str, units: str) -> list[CplRow]:
    """'## Unit = mm|inches', a '# Ref Val Package PosX PosY Rot Side' header, whitespace-separated rows. KiCad
    writes spaces inside fields as '_'; whatever sits between Ref and the last four fields is kept in `extra`."""
    header: list[str] = []
    out = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s:
            continue
        if s.startswith("#"):
            m = re.match(r"##\s*unit\s*=\s*([a-z]+)", s, re.I)
            if m:
                units = _UNIT_NAMES.get(m.group(1).lower(), units)
            elif _KICAD_REF_HEADER.match(s + " "):
                header = s.lstrip("#").split()
            elif re.match(r"##\s*end\b", s, re.I):
                break
            continue
        t = s.split()
        if len(t) < 5:
            raise ValueError(f"short position-file row: {s!r}")
        ref, (px, py, rot, side) = t[0], t[-4:]
        extra = dict(zip(header[1:-4], t[1:-4])) if len(t) == len(header) else {"text": " ".join(t[1:-4])}
        try:
            _designator(ref)
            out.append(CplRow(ref, parse_length(px, units), parse_length(py, units), parse_layer(side),
                              parse_angle(rot), extra))
        except ValueError as e:
            raise ValueError(f"{printable(ref)}: {e}") from None
    return out


def format_row(row: CplRow) -> list[str]:
    """JLC's columns: 4 decimals and 'mm' (never '-0.0000'), Top/Bottom, the angle in [0, 360) to 0.001 deg."""
    x, y = round(row.x, 4) + 0.0, round(row.y, 4) + 0.0
    # normalised twice: 359.9996 rounds to 360.0, which is written as 0 (and :g never gets more than 6 digits)
    a = norm_angle(round(norm_angle(row.rotation), 3))
    return [row.ref, f"{x:.4f}mm", f"{y:.4f}mm", "Bottom" if row.layer == BOTTOM else "Top", f"{a:g}"]


def write_cpl(path, rows: list[CplRow]) -> None:
    """JLC's sample layout (Designator, Mid X, Mid Y, Layer, Rotation; CRLF line ends), rows in the order given.
    Written to a temporary file and renamed, so a failed write leaves any old file as it was."""
    buf = io.StringIO(newline="")
    w = csv.writer(buf)
    w.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
    w.writerows(format_row(r) for r in rows)
    atomic_write(path, buf.getvalue())


def _create_temp(path: str) -> tuple[int, str]:
    """A new file next to path under a random name, created 0666 so that the kernel applies the umask (reading the
    umask would mean setting it, and another thread's files made in that instant would get 0666), or given the
    existing file's mode."""
    d = os.path.dirname(os.path.abspath(path))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    for _ in range(100):
        tmp = os.path.join(d, f".jlcplace-{secrets.token_hex(8)}.tmp")
        try:
            fd = os.open(tmp, flags, 0o666)
        except FileExistsError:
            continue
        try:
            os.chmod(tmp, os.stat(path).st_mode & 0o7777)
        except FileNotFoundError:
            pass
        except BaseException:
            os.close(fd)
            os.unlink(tmp)
            raise
        return fd, tmp
    raise FileExistsError(f"no free temporary file name in {d}")


def atomic_write(path, text: str) -> None:
    """Write text (UTF-8, newlines as given) to a temporary file next to path, fsync, then rename over path. The file
    keeps its mode, or gets 0666 less the umask, as any new file does (mkstemp alone would leave it 0600)."""
    path = os.fspath(path)
    fd, tmp = _create_temp(path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# ------------------------------------------------------------------------------------------------------- BOM

def read_bom(path) -> list[dict]:
    """BOM rows as {designators: [...], lcsc, comment, footprint, qty (int or None), extra: {...}}. `lcsc` is the
    normalised code when the cell holds one, else the cell as written (check_bom reports it)."""
    found = _table(read_text(path), _BOM_COLUMNS, ("designators",), path)
    if found is None:
        raise ValueError(f"{path}: no Designator column")
    rows, h, fields, _ = found
    known = set(fields.values())
    out = []
    for n, row in enumerate(rows[h + 1:], start=h + 2):
        if not any(c.strip() for c in row):
            continue
        cell = {f: (row[j].strip() if j < len(row) else "") for f, j in fields.items()}
        qty = None
        if cell.get("qty"):
            try:
                qty = int(float(cell["qty"]))
            except ValueError:
                raise ValueError(f"{path}: line {n}: Qty {cell['qty']!r} is not a number") from None
        raw = cell.get("lcsc", "")
        try:
            refs = [_designator(d) for d in re.split(r"[,;\s]+", cell["designators"]) if d]
        except ValueError as e:
            raise ValueError(f"{path}: line {n}: {e}") from None
        out.append({"designators": refs,
                    "lcsc": normalize_lcsc(raw) or raw, "comment": cell.get("comment", ""),
                    "footprint": cell.get("footprint", ""), "qty": qty, "extra": _extra(rows[h], row, known)})
    return out


# ------------------------------------------------------------------------------------------------------- writing

def correction_for(corrections, part: Part) -> Correction | None:
    """A part's correction from a mapping keyed by reference or by LCSC code (the reference wins)."""
    if not corrections:
        return None
    c = corrections.get(part.ref)
    if c is None and part.lcsc:
        c = corrections.get(part.lcsc)
    return c


def rows_for_board(board: Board, corrections: dict[str, Correction], reading: str = "jlc-2022", *, bom_refs=None
                   ) -> tuple[list[CplRow], list[str]]:
    """(rows, refs) for the parts JLC assembles that carry an LCSC code, made with convention.cpl_row_for; `refs`
    are those left out for want of a correction, or with only a mirrored one (no rigid placement reproduces it).
    With `bom_refs` (the JLC BOM's designators), also the parts the board excludes from its position files or its BOM
    that the BOM lists (jlc_places). `corrections` is keyed by reference or LCSC code. Both lists sorted naturally
    (R2 before R10)."""
    rows, missing = [], []
    for part in board.parts:
        if not jlc_places(part, bom_refs) or not part.lcsc:
            continue
        corr = correction_for(corrections, part)
        if corr is None or corr.mirror:
            missing.append(part.ref)
            continue
        x, y, a = cpl_row_for(part, corr, board.origin, reading)
        rows.append(CplRow(part.ref, x, y, part.layer, a))
    rows.sort(key=lambda r: natural_key(r.ref))
    missing.sort(key=natural_key)
    return rows, missing
