"""Read a KiCad board into model.Board: with pcbnew, with a pure-Python S-expression reader, or from a JSON snapshot.

Pads come out in each footprint's library frame (jlcplace.model). KiCad keeps a pad *as placed*: the file's
`(at x y a)` and pcbnew's GetFPRelativePosition() give s, the pad position in the footprint's unrotated local frame
after any flip, and the angle a is board-absolute. With l the library position and psi the library pad angle:

    s = D . l,     a = theta + psi (top),     a = theta - psi (bottom)         D = diag(1, -1) on the bottom

because flipping mirrors the footprint about its local x axis and negates every pad's relative angle (KiCad's
PAD::Flip), and D . R(psi) = R(-psi) . D. A flipped pad's own geometry (custom primitives, trapezoid delta, chamfered
corners, shape offset) is mirrored the same way, so a point u in the stored pad axes is at  l + R(psi) . D . u  in
the library frame. tests/test_board.py proves this against pcbnew's pad positions and HitTest on real boards.

Pad.poly is set for custom, chamfered and trapezoid pads and for pads whose shape is offset from the hole (x, y stay
the hole, GetPosition()). It is one closed ring read with the even-odd rule: copper with holes or several islands has
its rings joined by zero-width bridges walked both ways (as KiCad fractures polygons), which leaves
geom.point_in_polygon exact except on the bridge lines themselves; a bridge to a hole runs through copper, one to an
island crosses the gap. The S-expression reader builds custom pads as the union of the anchor and the primitives
(strokes included) with arcs to ARC_ERR; pcbnew's outline is its effective polygon (board max error, 5 um default).

A footprint's filled graphics on its paste layer (fp_poly, fp_rect, fp_circle on F.Paste, B.Paste when flipped) are
stencil openings that KiCad plots as it plots a paste-only pad: both readers add them after the pads, as paste
apertures (number "", copper False, custom, Pad.poly their outline, (x, y) its box centre).
"""

from __future__ import annotations

import json
import math
import os
import re
import warnings
from pathlib import Path

import numpy as np

from .convention import board_to_library, norm_angle, rot
from .cpl import parse_length
from .lcsc import find_lcsc
from .lcsc import normalize as normalize_lcsc
from .model import BOTTOM, TOP, Board, Pad, Part, ref_problem

#: chord error (mm) of the polygons the S-expression reader makes from arcs; vertices lie on the arc
ARC_ERR = 1e-3

_MANDATORY = ("Reference", "Value", "Datasheet", "Description")
_KINDS = {"smd": "smd", "thru_hole": "tht", "np_thru_hole": "npth", "connect": "connect"}
_SHAPES = ("rect", "roundrect", "oval", "circle", "trapezoid", "custom")


# ------------------------------------------------------------------------------------------------------- entry points

def load_board(path, *, origin="aux", lcsc_fields=(), reader: str = "auto") -> Board:
    """reader: "auto" (a .json path is a snapshot; else pcbnew if it imports, else sexpr), "pcbnew", "sexpr",
    "json". In auto mode a board pcbnew cannot load (e.g. a newer file format) is read with sexpr, with a warning."""
    p = os.fspath(path)
    if reader == "json" or (reader == "auto" and p.lower().endswith(".json")):
        return _snapshot_with(load_snapshot(p), origin, lcsc_fields)
    if reader == "sexpr":
        return load_board_sexpr(p, origin=origin, lcsc_fields=lcsc_fields)
    if reader == "pcbnew":
        return load_board_pcbnew(p, origin=origin, lcsc_fields=lcsc_fields)
    if reader != "auto":
        raise ValueError(f"unknown reader {reader!r} (auto, pcbnew, sexpr, json)")
    try:
        import pcbnew  # noqa: F401
    except ImportError:
        return load_board_sexpr(p, origin=origin, lcsc_fields=lcsc_fields)
    try:
        return load_board_pcbnew(p, origin=origin, lcsc_fields=lcsc_fields)
    except (OSError, RuntimeError) as e:
        if not Path(p).is_file():
            raise
        why = (f"{p}: written by a newer KiCad (version {e.version}) than pcbnew {e.pcbnew} reads"
               if isinstance(e, NewerFormat) else str(e) if p in str(e) else f"{p}: {e}")
        warnings.warn(f"{why}; reading it with the S-expression reader")      # the file's name once
        return load_board_sexpr(p, origin=origin, lcsc_fields=lcsc_fields)


class NewerFormat(OSError):
    """A board file pcbnew cannot load because a newer KiCad wrote it: its message names neither path (the caller
    says it) nor anything but the way out."""

    def __init__(self, version: int, pcbnew: str, reads: int):
        self.version, self.pcbnew, self.reads = version, pcbnew, reads
        super().__init__(f"written by a newer KiCad (version {version}); pcbnew {pcbnew} cannot read it (it reads up "
                         f"to version {reads}); use --reader sexpr or that KiCad's Python")


def file_version(path) -> int | None:
    """The `(version N)` a .kicad_pcb file starts with (its format's date, e.g. 20241229 for KiCad 9), or None."""
    try:
        with open(path, "rb") as f:
            head = f.read(4096).decode("utf-8-sig", "replace")
    except OSError:
        return None
    m = re.match(r"\s*\(\s*kicad_pcb\s*\(\s*version\s+(\d+)\s*\)", head)
    return int(m.group(1)) if m else None


def save_snapshot(board: Board, path) -> None:
    """JSON (Board.to_dict), sorted keys, 1-space indent, trailing newline."""
    Path(path).write_text(json.dumps(board.to_dict(), indent=1, sort_keys=True) + "\n", encoding="utf-8")


def load_snapshot(path) -> Board:
    """The board a snapshot holds. ValueError for a file that is not JSON or not a jlcplace snapshot."""
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError(f"not a jlcplace snapshot (not JSON: {e})") from None
    try:
        board = Board.from_dict(d)
    except (KeyError, TypeError, AttributeError, ValueError) as e:
        raise ValueError(f"not a jlcplace snapshot ({type(e).__name__}: {e})") from None
    for part in board.parts:            # a code as the board readers read one: a package typed in ("C0402") is none
        code = normalize_lcsc(part.lcsc) if part.lcsc else ""
        if code != part.lcsc:
            part.lcsc, part.lcsc_field = code, part.lcsc_field if code else ""
    return _checked(board)


def _checked(board: Board) -> Board:
    """The board, unless a part's reference cannot be a designator (model.ref_problem): ValueError naming it."""
    for part in board.parts:
        why = ref_problem(part.ref)
        if why:
            raise ValueError(f"the part at ({part.x:g}, {part.y:g}) has the reference {part.ref!r}, which is not a "
                             f"designator: {why}")
    return board


def _snapshot_with(board: Board, origin, lcsc_fields) -> Board:
    # a snapshot stores only the origin it was made with: keep it for "aux"/its own kind, override with explicit ones
    if not (isinstance(origin, str) and origin.strip().lower() in ("aux", board.origin_kind)):
        board.origin, board.origin_kind = resolve_origin(origin, None, None)
    if lcsc_fields:
        for part in board.parts:
            part.lcsc, part.lcsc_field = find_lcsc(part.fields, lcsc_fields)
    return board


def resolve_origin(origin, aux, grid) -> tuple[tuple[float, float], str]:
    """(origin, kind) for "aux" | "grid" | "absolute" | "X,Y" | (x, y); aux/grid are the board's (None = unknown).
    ValueError for anything else, and for a coordinate that is not a finite number ("nan,nan")."""
    (x, y), kind = _origin(origin, aux, grid)
    for v in (x, y):
        if not math.isfinite(v):
            raise ValueError(f"origin {origin!r}: not a finite number")
    return (x, y), kind


def _length(text: str) -> float:
    """mm from one origin coordinate: a number (nan and inf too: resolve_origin refuses them by name), or a length
    with a unit as the CPL reader takes it ('10mm', '3.937in', '100 mil')."""
    try:
        return float(text) + 0.0
    except ValueError:
        return parse_length(text) + 0.0


def _origin(origin, aux, grid) -> tuple[tuple[float, float], str]:
    if isinstance(origin, str):
        key = origin.strip().lower()
        named = {"aux": aux, "grid": grid, "absolute": (0.0, 0.0)}
        if key in named:
            if named[key] is None:
                raise ValueError(f"origin {origin!r} is not known for this board")
            return (float(named[key][0]) + 0.0, float(named[key][1]) + 0.0), key
        nums = [s for s in re.split(r"[,;()]+", key) if s.strip()]
        if len(nums) == 1:
            nums = key.split()                      # "10 20"
        if len(nums) == 2:
            try:
                return (_length(nums[0]), _length(nums[1])), "user"
            except ValueError:
                pass
        raise ValueError(f"origin must be aux, grid, absolute, 'X,Y' (mm, or with a unit: 4in, 100mil) or (x, y); "
                         f"got {origin!r}")
    x, y = origin
    return (float(x) + 0.0, float(y) + 0.0), "user"


# ------------------------------------------------------------------------------------------------------- pcbnew

def load_board_pcbnew(path, *, origin="aux", lcsc_fields=()) -> Board:
    import pcbnew

    p = os.fspath(path)
    if not Path(p).is_file():
        raise FileNotFoundError(p)
    kb = pcbnew.LoadBoard(p)
    if kb is None:                         # e.g. a file from a newer KiCad: LoadBoard returns None, saying nothing
        v, reads = file_version(p), getattr(pcbnew, "SEXPR_BOARD_FILE_VERSION", None)
        if v is not None and isinstance(reads, int) and v > reads:
            raise NewerFormat(v, pcbnew.Version(), reads)
        raise OSError(f"pcbnew {pcbnew.Version()} could not load it" + (f" (file format version {v})" if v else ""))
    return board_from_pcbnew(kb, p, origin=origin, lcsc_fields=lcsc_fields)


def board_from_pcbnew(kb, path: str = "", *, origin="aux", lcsc_fields=()) -> Board:
    """model.Board from a pcbnew.BOARD already in memory (e.g. inside a KiCad action plugin)."""
    import pcbnew

    p = path or str(kb.GetFileName())
    ds = kb.GetDesignSettings()
    org, kind = resolve_origin(origin, _mm2(ds.GetAuxOrigin()), _mm2(ds.GetGridOrigin()))
    ver = str(kb.GetFileFormatVersionAtLoad()) if hasattr(kb, "GetFileFormatVersionAtLoad") else ""
    parts = [_pcbnew_part(pcbnew, fp, lcsc_fields) for fp in kb.GetFootprints()]
    return _checked(Board(path=p, origin=org, parts=parts, source="pcbnew", kicad_version=ver, origin_kind=kind))


def _mm2(v) -> tuple[float, float]:
    return (v.x / 1e6 + 0.0, v.y / 1e6 + 0.0)


def pad_layer(pcbnew, pad, bottom: bool = False) -> int:
    """A layer the pad is on, for pcbnew's per-layer geometry calls: the part's outer copper if the pad has it, else
    its first copper layer, else its first layer (GetPrincipalLayer() answers F.Cu for a paste-only aperture)."""
    side = pcbnew.B_Cu if bottom else pcbnew.F_Cu
    if pad.IsOnLayer(side):
        return side
    seq = list(pad.GetLayerSet().Seq())
    return next((ly for ly in seq if pcbnew.IsCopperLayer(ly)), seq[0] if seq else side)


def _pcbnew_part(pcbnew, fp, lcsc_fields) -> Part:
    bottom = fp.GetLayer() == pcbnew.B_Cu
    fields = {f.GetName(): f.GetText() for f in fp.GetFields()}
    code, fname = find_lcsc(fields, lcsc_fields)
    x, y = _mm2(fp.GetPosition())
    part = Part(ref=fp.GetReference(), layer=BOTTOM if bottom else TOP, x=x, y=y,
                angle=norm_angle(fp.GetOrientationDegrees()), footprint=str(fp.GetFPIDAsString()),
                value=fp.GetValue(), lcsc=code, lcsc_field=fname, fields=fields, dnp=bool(fp.IsDNP()),
                exclude_pos=bool(fp.IsExcludedFromPosFiles()), exclude_bom=bool(fp.IsExcludedFromBOM()),
                board_only=bool(fp.IsBoardOnly()), tht=bool(fp.GetAttributes() & pcbnew.FP_THROUGH_HOLE))
    part.pads = [_pcbnew_pad(pcbnew, pad, part) for pad in fp.Pads()]
    part.pads += _pcbnew_paste(pcbnew, fp, part)
    return part


def _pcbnew_pad(pcbnew, pad, part: Part) -> Pad:
    shapes = {pcbnew.PAD_SHAPE_RECTANGLE: "rect", pcbnew.PAD_SHAPE_ROUNDRECT: "roundrect",
              pcbnew.PAD_SHAPE_OVAL: "oval", pcbnew.PAD_SHAPE_CIRCLE: "circle",
              pcbnew.PAD_SHAPE_TRAPEZOID: "trapezoid", pcbnew.PAD_SHAPE_CHAMFERED_RECT: "chamfered",
              pcbnew.PAD_SHAPE_CUSTOM: "custom"}
    kinds = {pcbnew.PAD_ATTRIB_SMD: "smd", pcbnew.PAD_ATTRIB_PTH: "tht", pcbnew.PAD_ATTRIB_NPTH: "npth",
             pcbnew.PAD_ATTRIB_CONN: "connect"}
    on_side = pad.IsOnLayer(pcbnew.B_Cu if part.bottom else pcbnew.F_Cu)
    # the padstack entry for the copper the part is soldered to (KiCad swaps front/back entries when flipping)
    layer = pad_layer(pcbnew, pad, part.bottom)
    s = -1.0 if part.bottom else 1.0
    rel = pad.GetFPRelativePosition()
    a = pad.GetFPRelativeOrientation().AsDegrees()        # = board angle - theta = psi on the top, -psi on the bottom
    shape = shapes[pad.GetShape(layer)]
    kind = kinds[pad.GetAttribute()]
    size = pad.GetSize(layer)
    off = pad.GetOffset(layer)
    poly = None
    if shape in ("custom", "chamfered", "trapezoid") or off.x or off.y:
        ps = pad.GetEffectivePolygon(layer, pcbnew.ERROR_INSIDE)
        rings = []
        for i in range(ps.OutlineCount()):
            chains = [ps.Outline(i)] + [ps.Hole(i, j) for j in range(ps.HoleCount(i))]
            for ch in chains:
                rings.append([board_to_library(part, _mm2(ch.CPoint(k))) for k in range(ch.PointCount())])
        poly = _bridge(_clean_rings(rings))
    cu = _outer(pad.IsOnLayer(pcbnew.F_Cu), pad.IsOnLayer(pcbnew.B_Cu)) if kind != "npth" else ""
    return Pad(number=pad.GetNumber(), x=rel.x / 1e6 + 0.0, y=s * rel.y / 1e6 + 0.0, w=size.x / 1e6, h=size.y / 1e6,
               angle=norm_angle(s * a), shape=shape, kind=kind,
               copper=kind == "tht" or (kind in ("smd", "connect") and on_side), net=pad.GetNetname(),
               pin_name=pad.GetPinFunction(), pin_type=pad.GetPinType(),
               rr=pad.GetRoundRectRadiusRatio(layer) if shape in ("roundrect", "chamfered") else 0.0, poly=poly, cu=cu)


def _pcbnew_paste(pcbnew, fp, part: Part) -> list[Pad]:
    """The footprint's filled graphics on its paste layer as paste apertures (module docstring)."""
    layer = pcbnew.B_Paste if part.bottom else pcbnew.F_Paste
    out = []
    for g in fp.GraphicalItems():
        if not (isinstance(g, pcbnew.PCB_SHAPE) and g.GetLayer() == layer and g.IsFilled()):
            continue
        ps = pcbnew.SHAPE_POLY_SET()
        g.TransformShapeToPolygon(ps, layer, 0, round(ARC_ERR * 1e6), pcbnew.ERROR_INSIDE)
        rings = []
        for i in range(ps.OutlineCount()):
            for ch in [ps.Outline(i)] + [ps.Hole(i, j) for j in range(ps.HoleCount(i))]:
                rings.append([board_to_library(part, _mm2(ch.CPoint(k))) for k in range(ch.PointCount())])
        out += _aperture(_bridge(_clean_rings(rings)))
    return out


def _aperture(poly) -> list[Pad]:
    """[the paste aperture of outline poly (library frame)], or [] for none."""
    if not poly:
        return []
    a = np.asarray(poly, float)
    (x0, y0), (x1, y1) = a.min(0), a.max(0)
    return [Pad(number="", x=float(x0 + x1) / 2 + 0.0, y=float(y0 + y1) / 2 + 0.0, w=float(x1 - x0), h=float(y1 - y0),
                shape="custom", kind="smd", copper=False, poly=poly)]


def _outer(front: bool, back: bool) -> str:
    """model.Pad.cu: the outer copper a pad has on the board as placed (a flipped footprint's layers are flipped)."""
    return "both" if front and back else TOP if front else BOTTOM if back else ""


# ------------------------------------------------------------------------------------------------------- S-expressions

_SCAN = re.compile(r'"(?:[^"\\]|\\.)*"|[()]', re.S)
_TOKEN = re.compile(r'[()]|"((?:[^"\\]|\\.)*)"|[^\s()"]+', re.S)
_HEAD = re.compile(r'\(\s*([^\s()"]+)')
_ESC = re.compile(r'\\(x[0-9a-fA-F]{1,2}|[0-7]{1,3}|.)', re.S)
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "f": "\f", "v": "\v"}
_WANTED = {"version", "generator", "generator_version", "host", "setup", "net", "footprint", "module"}


def _unescape(s: str) -> str:
    if "\\" not in s:
        return s

    def sub(m):
        e = m.group(1)
        if e[0] == "x" and len(e) > 1:
            return chr(int(e[1:], 16))
        if e[0] in "01234567":
            return chr(int(e, 8))
        return _ESCAPES.get(e, e)

    return _ESC.sub(sub, s)


def parse_sexpr(text: str, start: int = 0, end: int | None = None) -> list:
    """The S-expressions in text[start:end] as nested lists of str (quoted strings unescaped, atoms verbatim)."""
    stack: list[list] = [[]]
    for m in _TOKEN.finditer(text, start, len(text) if end is None else end):
        t = m.group()
        if t == "(":
            node: list = []
            stack[-1].append(node)
            stack.append(node)
        elif t == ")":
            if len(stack) == 1:
                raise ValueError(f"unbalanced ')' at offset {m.start()}")
            stack.pop()
        elif t[0] == '"':
            stack[-1].append(_unescape(m.group(1)))
        else:
            stack[-1].append(t)
    if len(stack) != 1:
        raise ValueError("unbalanced '(': the file is truncated")
    return stack[0]


# KiCad 8 wrote teardrop settings as "(curved_edges no)filter_ratio 0.9)"; pcbnew reads that because the opening
# paren of a teardrop keyword is optional in its parser (the RoyalBlue54L demo has 349 of them). The writer never
# otherwise puts a keyword straight after ')', so restoring the paren is safe.
_TEARDROP_TYPO = re.compile(r"\)(best_length_ratio|max_length|best_width_ratio|max_width|curved_edges|filter_ratio"
                            r"|enabled|allow_two_segments|prefer_zone_connections)(?=\s)")


def _top_level(text: str) -> list:
    """The root's children that the reader needs, parsed; the rest (tracks, zones, drawings) is skipped unparsed."""
    m = re.match(r"\ufeff?\s*\(\s*kicad_pcb\b", text)
    if not m:
        raise ValueError("not a KiCad board file (no kicad_pcb root)")
    out, depth, start = [], 0, 0
    for t in _SCAN.finditer(text, m.start()):
        c = t.group()
        if c == "(":
            depth += 1
            if depth == 2:
                start = t.start()
        elif c == ")":
            if depth == 2:
                h = _HEAD.match(text, start)
                if h and h.group(1) in _WANTED:
                    out.append(parse_sexpr(text, start, t.end())[0])
            depth -= 1
            if depth == 0:
                if text[t.end():].strip():
                    raise ValueError(f"unbalanced ')' closes the board at offset {t.start()}: the file is malformed")
                return out
    raise ValueError("unbalanced '(': the file is truncated")


def _kid(node, key):
    return next((c for c in node if isinstance(c, list) and c and c[0] == key), None)


def _kids(node, key):
    return [c for c in node if isinstance(c, list) and c and c[0] == key]


def _atoms(node) -> list[str]:
    return [c for c in node[1:] if isinstance(c, str)] if node else []


def _nums(node, n: int, default: float = 0.0) -> list[float]:
    """The first n numeric atoms (stopping at a keyword such as `unlocked`), padded with default."""
    vals: list[float] = []
    for c in _atoms(node):
        if len(vals) == n:
            break
        try:
            vals.append(float(c))
        except ValueError:
            break
    return vals + [default] * (n - len(vals))


def _yes(node) -> bool:
    """`(flag)` or `(flag yes)`."""
    if node is None:
        return False
    a = _atoms(node)
    return not a or a[0].lower() in ("yes", "true")


def load_board_sexpr(path, *, origin="aux", lcsc_fields=()) -> Board:
    """Read a .kicad_pcb (KiCad 5 `module` and KiCad 6-9 `footprint` formats) without KiCad."""
    p = os.fspath(path)
    text = Path(p).read_text(encoding="utf-8", errors="replace")
    nodes = _top_level(_TEARDROP_TYPO.sub(r")(\1", text))
    version = next((int(float(_atoms(n)[0])) for n in nodes if n[0] == "version" and _atoms(n)), 0)
    setup = next((n for n in nodes if n[0] == "setup"), [])
    aux = tuple(_nums(_kid(setup, "aux_axis_origin"), 2))
    grid = tuple(_nums(_kid(setup, "grid_origin"), 2))
    org, kind = resolve_origin(origin, aux, grid)
    max_err = float(_atoms(_kid(setup, "max_error"))[0]) if _kid(setup, "max_error") else 0.005
    nets = {a[0]: (a[1] if len(a) > 1 else "") for a in (_atoms(n) for n in nodes if n[0] == "net") if a}
    parts = [_sexpr_part(n, version, nets, lcsc_fields, min(ARC_ERR, max_err))
             for n in nodes if n[0] in ("footprint", "module")]
    return _checked(Board(path=p, origin=org, parts=parts, source="sexpr",
                          kicad_version=str(version) if version else "", origin_kind=kind))


def _sexpr_part(fp, version: int, nets: dict, lcsc_fields, err: float) -> Part:
    lib = fp[1] if len(fp) > 1 and isinstance(fp[1], str) else ""
    layer = (_atoms(_kid(fp, "layer")) or ["F.Cu"])[0]
    bottom = layer == "B.Cu"
    x, y, theta = _nums(_kid(fp, "at"), 3)
    fields = dict.fromkeys(_MANDATORY, "")
    for c in fp:
        if not isinstance(c, list) or not c:
            continue
        if c[0] == "property" and len(c) > 1 and isinstance(c[1], str):
            name, value = c[1], c[2] if len(c) > 2 and isinstance(c[2], str) else ""
            # what pcbnew does with properties that are not fields (pcb_io_kicad_sexpr_parser, KiCad 9)
            if name in ("ki_fp_filters", "Footprint"):
                continue
            if version < 20230620:
                if name in ("ki_keywords", "ki_locked", "Sheetfile", "Sheet file", "Sheetname", "Sheet name"):
                    continue
                if name == "ki_description":
                    name = "Description"
            fields[name] = value
        elif c[0] == "fp_text" and len(c) > 2 and c[1] in ("reference", "value"):
            fields[c[1].title()] = c[2]
    attrs = {a for c in _kids(fp, "attr") for a in _atoms(c)}

    def flag(name: str) -> bool:
        return name in attrs or _yes(_kid(fp, name))

    virtual = "virtual" in attrs                   # KiCad 5: neither placed nor in the BOM
    tht = "through_hole" in attrs
    if version < 20200826 and not ({"smd", "virtual"} & attrs):
        tht = True                                 # legacy files only marked smd/virtual; pcbnew reads the rest as THT
    code, fname = find_lcsc(fields, lcsc_fields)
    part = Part(ref=fields["Reference"], layer=BOTTOM if bottom else TOP, x=x + 0.0, y=y + 0.0,
                angle=norm_angle(theta), footprint=lib, value=fields["Value"], lcsc=code, lcsc_field=fname,
                fields=fields, dnp=flag("dnp"), exclude_pos=virtual or flag("exclude_from_pos_files"),
                exclude_bom=virtual or flag("exclude_from_bom"), board_only=flag("board_only"), tht=tht)
    part.pads = [_sexpr_pad(c, part, theta, nets, err) for c in _kids(fp, "pad")]
    part.pads += _sexpr_paste(fp, part, err)
    return part


_PASTE_GRAPHICS = {"fp_poly": "gr_poly", "fp_rect": "gr_rect", "fp_circle": "gr_circle"}


def _sexpr_paste(fp, part: Part, err: float) -> list[Pad]:
    """The footprint's filled graphics on its paste layer as paste apertures (module docstring). Their points are
    stored as placed (after any flip), in the footprint's unrotated frame, as a pad's position is."""
    layer = "B.Paste" if part.bottom else "F.Paste"
    out = []
    for c in fp:
        kind = _PASTE_GRAPHICS.get(c[0]) if isinstance(c, list) and c else None
        if kind is None or (_atoms(_kid(c, "layer")) or [""])[0] != layer or not _filled(c, kind):
            continue
        cu = _Copper(err)
        _add_primitive(cu, [kind] + c[1:], err)
        rings = [[(x, -y if part.bottom else y) for x, y in r] for r in _union(cu.regions())]
        out += _aperture(_bridge(_clean_rings(rings)))
    return out


def _sexpr_pad(c, part: Part, theta: float, nets: dict, err: float) -> Pad:
    a = _atoms(c)
    number, ptype, pshape = (a + ["", "", ""])[:3] if len(a) >= 3 else ("", "smd", "rect")
    kind = _KINDS.get(ptype, "smd")
    layers = set(_atoms(_kid(c, "layers")))
    if not any(ly.endswith(".Cu") for ly in layers):
        number = ""                                # pcbnew drops the number of an aperture pad (no copper at all)
    side = "B.Cu" if part.bottom else "F.Cu"
    on_side = bool(layers & {side, "*.Cu", "F&B.Cu"})
    geo = c
    stack = _kid(c, "padstack")
    if stack is not None and on_side:
        # front/inner/back or custom padstacks: the soldered side's entry overrides the pad's own geometry
        entry = next((e for e in _kids(stack, "layer") if _atoms(e)[:1] == [side]), None)
        if entry is not None and side != "F.Cu":
            geo = entry
            pshape = (_atoms(_kid(entry, "shape")) or [pshape])[0]
    sx, sy, ang = _nums(_kid(c, "at"), 3)
    w, h = _nums(_kid(geo, "size"), 2)
    chamfers = set(_atoms(_kid(geo, "chamfer")))
    shape = pshape if pshape in _SHAPES else "rect"
    if chamfers:
        shape = "chamfered"                        # KiCad writes a chamfered rect as roundrect + (chamfer ...)
    rr = 0.0
    if shape in ("roundrect", "chamfered"):
        rr = float(_atoms(_kid(geo, "roundrect_rratio"))[0]) if _kid(geo, "roundrect_rratio") else 0.25
    drill = _kid(c, "drill")
    off = _kid(geo, "offset") if geo is not c else (_kid(drill, "offset") if drill else None)
    ox, oy = _nums(off, 2)
    net = _kid(c, "net")
    na = _atoms(net)
    netname = na[1] if len(na) > 1 else (nets.get(na[0], na[0]) if na else "")
    bottom = part.bottom
    psi = norm_angle(theta - ang if bottom else ang - theta)
    lx, ly = sx + 0.0, (-sy if bottom else sy) + 0.0
    poly = None
    if shape in ("custom", "chamfered", "trapezoid") or ox or oy:
        cr = float(_atoms(_kid(geo, "chamfer_ratio"))[0]) if _kid(geo, "chamfer_ratio") else 0.2
        rings = _pad_rings(shape, w, h, rr, cr, chamfers, _nums(_kid(geo, "rect_delta"), 2), (ox, oy),
                           (_atoms(_kid(_kid(geo, "options") or [], "anchor")) or ["rect"])[0],
                           _kid(geo, "primitives") or [], err)
        poly = _bridge(_clean_rings([[_stored_to_library(u, lx, ly, psi, bottom) for u in r] for r in rings]))
    both = bool(layers & {"*.Cu", "F&B.Cu"})
    cu = _outer(both or "F.Cu" in layers, both or "B.Cu" in layers) if kind != "npth" else ""
    return Pad(number=number, x=lx, y=ly, w=w, h=h, angle=psi, shape=shape, kind=kind,
               copper=kind == "tht" or (kind in ("smd", "connect") and on_side), net=netname,
               pin_name=(_atoms(_kid(c, "pinfunction")) or [""])[0], pin_type=(_atoms(_kid(c, "pintype")) or [""])[0],
               rr=rr, poly=poly, cu=cu)


def _stored_to_library(u, lx, ly, psi, bottom):
    """l + R(psi) . D . u for a point u in the stored (as-placed) pad axes."""
    r = rot((u[0], -u[1] if bottom else u[1]), psi)
    return (lx + r[0], ly + r[1])


# ------------------------------------------------------------------------------------------------------- pad outlines
# Everything here is in the stored pad axes (x along the pad's width), mm. A region is a list of closed rings read
# with the even-odd rule. Circles keep their vertices on the circle at the canonical angles 2 pi k / n, so arcs and
# discs of one circle share vertices, and a stroke is a rectangle per segment plus one disc per joint: overlapping
# primitives then meet edge to edge or cross at clear angles, never in hairline slivers the union could misread.

def _nseg(r: float, err: float) -> int:
    """Vertices on a full circle of radius r (a multiple of 8). Sized for 0.6 err so that an arc's end pieces, which
    may span up to 1.25 steps (see _arc), still keep the chord error below err."""
    if r <= err:
        return 8
    n = math.ceil(math.pi / math.acos(1.0 - 0.6 * err / r))
    return max(8, -(-n // 8) * 8)


def _arc(c, r: float, a0: float, a1: float, err: float) -> list:
    """Points on circle (c, r) from angle a0 to a1 (radians, atan2 sense in these y-down axes, either direction):
    both ends exact, canonical vertices between them except those within a quarter step of an end."""
    step = 2 * math.pi / _nseg(r, err)
    q = 0.25
    if a1 >= a0:
        ks = range(math.floor(a0 / step + q) + 1, math.ceil(a1 / step - q))
    else:
        ks = range(math.ceil(a0 / step - q) - 1, math.floor(a1 / step + q), -1)
    pts = [(c[0] + r * math.cos(a0), c[1] + r * math.sin(a0))]
    pts += [(c[0] + r * math.cos(k * step), c[1] + r * math.sin(k * step)) for k in ks]
    pts.append((c[0] + r * math.cos(a1), c[1] + r * math.sin(a1)))
    return pts


def _circle(c, r: float, err: float) -> list:
    n = _nseg(r, err)
    return [(c[0] + r * math.cos(2 * math.pi * k / n), c[1] + r * math.sin(2 * math.pi * k / n)) for k in range(n)]


def _capsule(a, b, r: float, err: float) -> list:
    """Ring of the stadium of radius r around segment ab."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    if math.hypot(dx, dy) < 1e-9:
        return _circle(a, r, err)
    phi = math.atan2(dy, dx)
    h = math.pi / 2
    return _arc(b, r, phi - h, phi + h, err) + _arc(a, r, phi + h, phi + 3 * h, err)


def _rounded_rect(hw: float, hh: float, r: float, chamfer: float, corners: set, err: float) -> list:
    """KiCad's round/chamfered rect: corners cut by `chamfer` along both edges, the others rounded with radius r."""
    out = []
    for name, (sx, sy), a0 in (("bottom_right", (1, 1), 0.0), ("bottom_left", (-1, 1), 90.0),
                               ("top_left", (-1, -1), 180.0), ("top_right", (1, -1), 270.0)):
        cx, cy = sx * hw, sy * hh
        if name in corners and chamfer > 0:
            # entering the corner along the previous edge, leaving along the next (counter-clockwise in atan2)
            first = (cx, cy - sy * chamfer) if sx * sy > 0 else (cx - sx * chamfer, cy)
            second = (cx - sx * chamfer, cy) if sx * sy > 0 else (cx, cy - sy * chamfer)
            out += [first, second]
        elif r > 0:
            out += _arc((cx - sx * r, cy - sy * r), r, math.radians(a0), math.radians(a0 + 90.0), err)
        else:
            out.append((cx, cy))
    return out


def _shape_ring(shape: str, w: float, h: float, rr: float, cr: float, chamfers: set, delta, err: float) -> list:
    hw, hh = w / 2.0, h / 2.0
    if shape == "circle":
        return _circle((0.0, 0.0), hw, err)
    if shape == "oval":
        if abs(hw - hh) < 1e-12:
            return _circle((0.0, 0.0), hw, err)
        if hw > hh:
            return _capsule((-(hw - hh), 0.0), (hw - hh, 0.0), hh, err)
        return _capsule((0.0, -(hh - hw)), (0.0, hh - hw), hw, err)
    if shape == "trapezoid":
        # KiCad's trapezoid: delta.x tilts the left/right edges, delta.y the top/bottom ones
        dx, dy = delta[0] / 2.0, delta[1] / 2.0
        return [(-hw - dy, hh + dx), (hw + dy, hh - dx), (hw - dy, -hh + dx), (-hw + dy, -hh - dx)]
    if shape in ("roundrect", "chamfered"):
        m = min(w, h)
        return _rounded_rect(hw, hh, min(rr, 0.5) * m, min(cr, 0.5) * m if shape == "chamfered" else 0.0,
                             chamfers, err)
    return [(hw, hh), (-hw, hh), (-hw, -hh), (hw, -hh)]


def _pad_rings(shape, w, h, rr, cr, chamfers, delta, offset, anchor, prims, err) -> list:
    if shape == "custom":
        cu = _Copper(err)
        if anchor == "circle":
            cu.circle((0.0, 0.0), w / 2.0)
        else:
            cu.region([[(w / 2, h / 2), (-w / 2, h / 2), (-w / 2, -h / 2), (w / 2, -h / 2)]])
        for p in prims[1:]:
            if isinstance(p, list):
                _add_primitive(cu, p, err)
        rings = _union(cu.regions())
    else:
        rings = [_shape_ring(shape, w, h, rr, cr, chamfers, delta, err)]
    ox, oy = offset
    return [[(x + ox, y + oy) for x, y in r] for r in rings]


def _xy(node) -> tuple[float, float]:
    v = _nums(node, 2)
    return (v[0], v[1])


def _three_point_arc(s, m, e):
    """(centre, radius, a0, a1) of the arc from s through m to e; None if the points are collinear."""
    ax, ay, bx, by, cx, cy = s[0], s[1], m[0], m[1], e[0], e[1]
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return None
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
    r = math.hypot(ax - ux, ay - uy)
    a0, am, ae = (math.atan2(p[1] - uy, p[0] - ux) for p in (s, m, e))
    sweep = (ae - a0) % (2 * math.pi)
    if (am - a0) % (2 * math.pi) > sweep:
        sweep -= 2 * math.pi
    return (ux, uy), r, a0, a0 + sweep


def _arc_params(p):
    """(centre, radius, a0, a1) of a gr_arc: KiCad 7+ (start mid end), or KiCad 5/6 (start=centre, end=first point,
    angle in degrees, clockwise on screen = increasing atan2 angle in y-down axes)."""
    if _kid(p, "mid") is not None:
        return _three_point_arc(_xy(_kid(p, "start")), _xy(_kid(p, "mid")), _xy(_kid(p, "end")))
    c, s = _xy(_kid(p, "start")), _xy(_kid(p, "end"))
    a0 = math.atan2(s[1] - c[1], s[0] - c[0])
    return c, math.hypot(s[0] - c[0], s[1] - c[1]), a0, a0 + math.radians(_nums(_kid(p, "angle"), 1)[0])


def _poly_points(pts_node, err) -> list:
    """(pts (xy x y) ... (arc (start) (mid) (end)) ...) as a point list."""
    out = []
    for q in pts_node[1:] if pts_node else []:
        if not isinstance(q, list):
            continue
        if q[0] == "xy":
            out.append(_xy(q))
        elif q[0] == "arc":
            arc = _three_point_arc(_xy(_kid(q, "start")), _xy(_kid(q, "mid")), _xy(_kid(q, "end")))
            if arc:
                out += _arc(arc[0], arc[1], arc[2], arc[3], err)
            else:
                out += [_xy(_kid(q, "start")), _xy(_kid(q, "end"))]
    return out


def _merge_close(pts, closed: bool, tol: float) -> list:
    """Drop points within tol of the previous one (an open polyline keeps both ends)."""
    out = [pts[0]] if pts else []
    for p in pts[1:]:
        if math.dist(p, out[-1]) >= tol:
            out.append(p)
    if not closed and len(pts) > 1 and out[-1] is not pts[-1]:
        if len(out) > 1:
            out[-1] = pts[-1]
        elif math.dist(out[0], pts[-1]) > 1e-9:
            out.append(pts[-1])
    while closed and len(out) > 2 and math.dist(out[-1], out[0]) < tol:
        out.pop()
    return out


def _bezier(q, err: float) -> list:
    """A cubic Bezier as a polyline: with n even steps the chords deviate by at most
    3/4 max(|q0 - 2 q1 + q2|, |q1 - 2 q2 + q3|) / n^2."""
    m = max(math.hypot(q[0][0] - 2 * q[1][0] + q[2][0], q[0][1] - 2 * q[1][1] + q[2][1]),
            math.hypot(q[1][0] - 2 * q[2][0] + q[3][0], q[1][1] - 2 * q[2][1] + q[3][1]))
    n = max(4, math.ceil(math.sqrt(0.75 * m / err)))
    return [tuple((1 - t) ** 3 * q[0][i] + 3 * (1 - t) ** 2 * t * q[1][i] + 3 * (1 - t) * t * t * q[2][i]
                  + t ** 3 * q[3][i] for i in (0, 1)) for t in (k / n for k in range(n + 1))]


def _add_primitive(cu: "_Copper", p, err: float) -> None:
    """One custom-pad primitive (gr_poly/line/arc/circle/rect/curve), its stroke included."""
    kind = p[0]
    width = _nums(_kid(_kid(p, "stroke") or p, "width"), 1)[0]
    r = width / 2.0
    filled = _filled(p, kind)
    if kind in ("gr_poly", "gr_rect"):
        if kind == "gr_poly":
            pts = _merge_close(_poly_points(_kid(p, "pts"), err), True, err / 4)
        else:
            (x0, y0), (x1, y1) = _xy(_kid(p, "start")), _xy(_kid(p, "end"))
            pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        if filled and len(pts) >= 3:
            cu.region([pts])
        cu.stroke(pts, True, r)
    elif kind == "gr_line":
        cu.stroke([_xy(_kid(p, "start")), _xy(_kid(p, "end"))], False, r)
    elif kind == "gr_circle":
        c, e = _xy(_kid(p, "center")), _xy(_kid(p, "end"))
        rad = math.dist(c, e)
        if filled or rad - r <= err:
            cu.circle(c, rad + r)
        else:
            cu.region([_circle(c, rad + r, err), _circle(c, rad - r, err)])
    elif kind == "gr_arc":
        arc = _arc_params(p)
        if arc is None:
            cu.stroke([_xy(_kid(p, "start")), _xy(_kid(p, "end"))], False, r)
        elif arc[1] - r > err:
            cu.band(*arc, r)
        else:
            cu.stroke(_arc(arc[0], arc[1], arc[2], arc[3], err), False, r)
    elif kind == "gr_curve":
        q = [_xy(v) for v in _kids(_kid(p, "pts") or [], "xy")][:4]
        if len(q) == 4:
            cu.stroke(_bezier(q, err), False, r)
    # gr_bbox / gr_vector are annotations, not copper


def _filled(p, kind: str) -> bool:
    """Is graphic p (gr_* or fp_* of `kind` gr_poly, gr_rect, gr_circle, ...) filled?"""
    fill = _kid(p, "fill")
    if fill is not None:
        return (_atoms(fill) or ["no"])[0].lower() in ("yes", "solid", "true")
    # legacy files had no fill flag: polygons were filled, rects and circles when their stroke width was 0
    width = _nums(_kid(_kid(p, "stroke") or p, "width"), 1)[0]
    return kind == "gr_poly" or (kind in ("gr_rect", "gr_circle") and width == 0)


class _Copper:
    """Collects a custom pad's copper as regions for _union, built so that pieces which touch share exact vertices.

    Stroked segments meeting at a point form a joint. Two segments of one width that turn by less than
    min(0.3, sqrt(8 err / r)) rad, and are long enough that the inner corner stays inside both, are mitered: their quads
    share the exact miter edge and the miter overshoots the round join by < err. Every other joint and open end gets one
    disc, merged with any circle of the same centre and radius (anchor, filled circle), whose vertices include the exact
    corners of everything meeting there."""

    def __init__(self, err: float):
        self.err = err
        self.rings: list = []                   # finished regions
        self.segs: list = []                    # (a, b, r)
        self.bands: list = []                   # (outer arc + inner arc ring, ends [(point, outer, inner)], r)
        self.discs: dict = {}                   # key -> [centre, r, required points]

    @staticmethod
    def _key(p, r):
        return (round(p[0], 9), round(p[1], 9), round(r, 9))

    def region(self, rings) -> None:
        self.rings.append(rings)

    def circle(self, c, r: float) -> None:
        if r > 0:
            self.discs.setdefault(self._key(c, r), [c, r, []])

    def stroke(self, pts, closed: bool, r: float) -> None:
        if r <= 0 or not pts:
            return
        pts = _merge_close(list(pts), closed, self.err / 4)
        if len(pts) == 1:
            self.circle(pts[0], r)
            return
        pairs = list(zip(pts, pts[1:] + pts[:1])) if closed and len(pts) > 2 else list(zip(pts, pts[1:]))
        self.segs += [(a, b, r) for a, b in pairs if math.dist(a, b) > 1e-9]

    def band(self, c, rad: float, a0: float, a1: float, r: float) -> None:
        outer, inner = _arc(c, rad + r, a0, a1, self.err), _arc(c, rad - r, a1, a0, self.err)
        ends = [((c[0] + rad * math.cos(a0), c[1] + rad * math.sin(a0)), outer[0], inner[-1]),
                ((c[0] + rad * math.cos(a1), c[1] + rad * math.sin(a1)), outer[-1], inner[0])]
        self.bands.append((outer + inner, ends, r))

    def regions(self) -> list:
        joints: dict = {}
        for i, (a, b, r) in enumerate(self.segs):
            joints.setdefault(self._key(a, r), []).append((i, 0))
            joints.setdefault(self._key(b, r), []).append((i, 1))
        for ring, ends, r in self.bands:
            self.rings.append([ring])
            for p, po, pi in ends:
                self.circle(p, r)
                self.discs[self._key(p, r)][2] += [po, pi]
        corners: dict = {}
        for key, ends in joints.items():
            mit = self._miter(ends) if len(ends) == 2 else None
            if mit:
                corners.update(mit)
                continue
            seg = self.segs[ends[0][0]]
            self.circle(seg[0] if ends[0][1] == 0 else seg[1], seg[2])
            for i, end in ends:
                lr = self._nominal(i, end)
                corners[(i, end)] = lr
                self.discs[key][2] += list(lr)
        out = list(self.rings)
        for i in range(len(self.segs)):
            (la, ra), (lb, rb) = corners[(i, 0)], corners[(i, 1)]
            out.append([[la, lb, rb, ra]])
        for c, r, req in self.discs.values():
            out.append([_disc(c, r, req, self.err)])
        return out

    def _nominal(self, i: int, end: int):
        """(left, right) corners of segment i at its start (end 0) or end (1), left of its direction a -> b."""
        a, b, r = self.segs[i]
        L = math.dist(a, b)
        nx, ny = -(b[1] - a[1]) / L * r, (b[0] - a[0]) / L * r
        p = a if end == 0 else b
        return (p[0] + nx, p[1] + ny), (p[0] - nx, p[1] - ny)

    def _miter(self, ends):
        (i, ei), (j, ej) = ends
        (a1, b1, r), (a2, b2, r2) = self.segs[i], self.segs[j]
        if r != r2 or i == j:
            return None
        p = a1 if ei == 0 else b1
        o1 = (b1[0] - a1[0], b1[1] - a1[1]) if ei == 0 else (a1[0] - b1[0], a1[1] - b1[1])
        o2 = (b2[0] - a2[0], b2[1] - a2[1]) if ej == 0 else (a2[0] - b2[0], a2[1] - b2[1])
        L1, L2 = math.hypot(*o1), math.hypot(*o2)
        cosang = max(-1.0, min(1.0, (o1[0] * o2[0] + o1[1] * o2[1]) / (L1 * L2)))
        turn = math.pi - math.acos(cosang)
        if turn >= min(0.3, math.sqrt(8 * self.err / r)) or r * math.tan(turn / 2) >= 0.45 * min(L1, L2):
            return None
        # arriving along -o1, leaving along o2; n_in, n_out their left normals
        nin, nout = (o1[1] / L1, -o1[0] / L1), (-o2[1] / L2, o2[0] / L2)
        k = r / (1.0 + nin[0] * nout[0] + nin[1] * nout[1])
        mp = (p[0] + k * (nin[0] + nout[0]), p[1] + k * (nin[1] + nout[1]))
        mm = (p[0] - k * (nin[0] + nout[0]), p[1] - k * (nin[1] + nout[1]))
        out = {}
        for s, e in ends:
            left, _ = self._nominal(s, e)
            out[(s, e)] = (mp, mm) if math.dist(left, mp) < math.dist(left, mm) else (mm, mp)
        return out


def _disc(c, r: float, req, err: float) -> list:
    """Circle polygon through the canonical vertices and the required points (exact, on the circle); canonical
    vertices within a quarter step of a required point are left out."""
    n = _nseg(r, err)
    step = 2 * math.pi / n
    pts = {}
    for q in req:
        pts[math.atan2(q[1] - c[1], q[0] - c[0]) % (2 * math.pi)] = q
    taken = sorted(pts)
    for k in range(n):
        a = k * step
        near = min((abs((a - t + math.pi) % (2 * math.pi) - math.pi) for t in taken), default=math.inf)
        if near > 0.25 * step:
            pts[a] = (c[0] + r * math.cos(a), c[1] + r * math.sin(a))
    return [pts[a] for a in sorted(pts)]


# ------------------------------------------------------------------------------------------------------- polygon union

def _even_odd(rings, P) -> np.ndarray:
    """Even-odd containment of points P (N x 2) in a region (list of rings)."""
    inside = np.zeros(len(P), dtype=bool)
    px, py = P[:, 0][:, None], P[:, 1][:, None]
    for ring in rings:
        R = np.asarray(ring, float)
        x1, y1 = R[:, 0][None, :], R[:, 1][None, :]
        x2, y2 = np.roll(R[:, 0], -1)[None, :], np.roll(R[:, 1], -1)[None, :]
        straddle = (y1 > py) != (y2 > py)
        with np.errstate(divide="ignore", invalid="ignore"):
            xi = x1 + (py - y1) * (x2 - x1) / (y2 - y1)
        inside ^= (np.count_nonzero(straddle & (px < xi), axis=1) % 2).astype(bool)
    return inside


def _union(regions, tol: float = 1e-10) -> list:
    """Boundary rings of the union of regions (each a list of even-odd rings).

    Every edge is split where it meets an edge of another region (tol, mm, snaps meeting points onto vertices). A
    piece is kept when exactly one of the two points delta either side of its midpoint is in the union, oriented with
    the union on its left, shared edges once; the pieces are chained back into rings. If a ring does not close (a
    piece misread in a sliver thinner than delta), the classification is retried with other offsets."""
    regions = [[r for r in reg if len(r) >= 3] for reg in regions]
    regions = [reg for reg in regions if reg]
    if len(regions) <= 1:
        return [list(r) for reg in regions for r in reg]
    pieces = _split_edges(regions, tol)
    if not pieces:
        return []
    P0, P1 = np.asarray([p for p, _ in pieces]), np.asarray([q for _, q in pieces])
    mid, dv = (P0 + P1) / 2, P1 - P0
    n = np.stack([-dv[:, 1], dv[:, 0]], axis=1) / np.hypot(dv[:, 0], dv[:, 1])[:, None]
    rings: list = []
    for delta in (1e-9, 1e-7, 1e-11, 1e-6):
        left, right = np.zeros(len(pieces), bool), np.zeros(len(pieces), bool)
        for reg in regions:
            left |= _even_odd(reg, mid + delta * n)
            right |= _even_odd(reg, mid - delta * n)
        kept, seen = [], set()
        for (p, q), lft, rgt in zip(pieces, left, right):
            if lft != rgt:
                seg = (p, q) if lft else (q, p)
                key = (_vkey(seg[0]), _vkey(seg[1]))
                if key not in seen:
                    seen.add(key)
                    kept.append(seg)
        rings, closed = _chain(kept)
        if closed:
            return rings
    warnings.warn("a custom pad's primitives did not merge into closed outlines; its copper outline is approximate")
    return rings


def _split_edges(regions, tol: float) -> list:
    """All region edges as pieces (p, q), split wherever an edge of another region crosses, touches or overlaps."""
    A, B = [], []
    for reg in regions:
        for ring in reg:
            for k in range(len(ring)):
                p, q = ring[k], ring[(k + 1) % len(ring)]
                if math.dist(p, q) > tol:
                    A.append(p), B.append(q)
    A, B = np.asarray(A, float), np.asarray(B, float)
    D = B - A
    length = np.hypot(D[:, 0], D[:, 1])
    lo, hi = np.minimum(A, B) - tol, np.maximum(A, B) + tol
    splits: list[list] = [[(0.0, tuple(A[e])), (1.0, tuple(B[e]))] for e in range(len(A))]
    idx = np.arange(len(A))
    for e in range(len(A)):
        # edges of the same region too: a primitive polygon may cross itself
        f = np.nonzero((idx > e) & np.all(lo <= hi[e], axis=1) & np.all(hi >= lo[e], axis=1))[0]
        if not len(f):
            continue
        d, g, w = D[e], D[f], A[f] - A[e]
        den = d[0] * g[:, 1] - d[1] * g[:, 0]
        cw_g = w[:, 0] * g[:, 1] - w[:, 1] * g[:, 0]
        cw_d = w[:, 0] * d[1] - w[:, 1] * d[0]
        crossing = np.abs(den) > 1e-12 * length[e] * length[f]
        with np.errstate(divide="ignore", invalid="ignore"):
            t, u = cw_g / den, cw_d / den
        te, uf = tol / length[e], tol / length[f]
        for k in np.nonzero(crossing & (t >= -te) & (t <= 1 + te) & (u >= -uf) & (u <= 1 + uf))[0]:
            fk = f[k]
            tk, uk = min(max(t[k], 0.0), 1.0), min(max(u[k], 0.0), 1.0)
            # a meeting point at an existing vertex reuses that vertex, so the pieces chain exactly
            if min(uk, 1 - uk) * length[fk] <= tol:
                pt = tuple(A[fk] if uk < 0.5 else B[fk])
            elif min(tk, 1 - tk) * length[e] <= tol:
                pt = tuple(A[e] if tk < 0.5 else B[e])
            else:
                pt = (A[e][0] + tk * d[0], A[e][1] + tk * d[1])
            splits[e].append((tk, pt))
            splits[fk].append((uk, pt))
        # collinear overlaps: split each edge at the other's end points
        for k in np.nonzero(~crossing & (np.abs(cw_d) <= tol * length[e]))[0]:
            fk = f[k]
            for pt in (A[fk], B[fk]):
                tt = float((pt - A[e]) @ d) / length[e] ** 2
                if tol < tt * length[e] < length[e] - tol:
                    splits[e].append((tt, tuple(pt)))
            for pt in (A[e], B[e]):
                uu = float((pt - A[fk]) @ g[k]) / length[fk] ** 2
                if tol < uu * length[fk] < length[fk] - tol:
                    splits[fk].append((uu, tuple(pt)))
    pieces = []
    for sp in splits:
        sp.sort()
        pts = [sp[0][1]]
        for _, pt in sp[1:]:
            if math.dist(pt, pts[-1]) > tol:
                pts.append(pt)
        pieces += list(zip(pts, pts[1:]))
    return pieces


def _vkey(p) -> tuple[int, int]:
    return (round(p[0] * 1e9), round(p[1] * 1e9))


def _chain(segs) -> tuple[list, bool]:
    """(rings, all closed) from directed segments; ends are matched to starts within 2e-9 mm."""
    starts: dict = {}
    for i, (p, _) in enumerate(segs):
        starts.setdefault(_vkey(p), []).append(i)
    used = [False] * len(segs)

    def following(q):
        kx, ky = _vkey(q)
        for key in [(kx, ky)] + [(kx + i, ky + j) for i in (-2, -1, 0, 1, 2) for j in (-2, -1, 0, 1, 2) if i or j]:
            for c in starts.get(key, ()):
                if not used[c] and math.dist(segs[c][0], q) <= 2e-9:
                    return c
        return None

    rings, closed = [], True
    for i in range(len(segs)):
        if used[i]:
            continue
        ring, j = [], i
        while j is not None:
            used[j] = True
            ring.append(segs[j][0])
            if math.dist(segs[j][1], segs[i][0]) <= 2e-9:
                break
            j = following(segs[j][1])
        else:
            closed = False
        rings.append(ring)
    return rings, closed


def _area(ring) -> float:
    return 0.5 * sum(ring[k][0] * ring[k - 1][1] - ring[k - 1][0] * ring[k][1] for k in range(len(ring)))


def _between(a, b, c, tol: float) -> bool:
    """b lies on segment ac within tol, so dropping it leaves the outline unchanged (a spike tip, a == c, stays)."""
    dx, dy = c[0] - a[0], c[1] - a[1]
    L2 = dx * dx + dy * dy
    if L2 <= 1e-18:
        return False
    t = ((b[0] - a[0]) * dx + (b[1] - a[1]) * dy) / L2
    return 0.0 < t < 1.0 and abs((b[0] - a[0]) * dy - (b[1] - a[1]) * dx) <= tol * math.sqrt(L2)


def _clean_rings(rings, tol: float = 2e-6) -> list:
    """Coordinates rounded to 1e-6 mm (KiCad's nm); repeated points, points on a straight run and degenerate rings
    dropped."""
    out = []
    for ring in rings:
        pts: list = []
        for p in ring:
            p = (round(p[0], 6) + 0.0, round(p[1], 6) + 0.0)
            if pts and math.dist(p, pts[-1]) <= 1e-9:
                continue
            pts.append(p)
            while len(pts) >= 3 and _between(pts[-3], pts[-2], pts[-1], tol):
                del pts[-2]
        while len(pts) > 1 and math.dist(pts[0], pts[-1]) <= 1e-9:
            pts.pop()
        while len(pts) >= 3:
            if _between(pts[-2], pts[-1], pts[0], tol):
                pts.pop()
            elif _between(pts[-1], pts[0], pts[1], tol):
                pts.pop(0)
            else:
                break
        if len(pts) >= 3 and abs(_area(pts)) > 1e-12:
            out.append(pts)
    return out


def _bridge(rings) -> list | None:
    """One even-odd ring from several: the largest first, each other ring spliced in at its vertex nearest the
    outline built so far, by a zero-width bridge walked both ways (its crossings cancel)."""
    if not rings:
        return None
    rings = sorted(rings, key=lambda r: -abs(_area(r)))
    poly = list(rings[0])
    for ring in rings[1:]:
        P, Q = np.asarray(poly), np.asarray(ring)
        d = ((P[:, None, :] - Q[None, :, :]) ** 2).sum(2)
        i, j = np.unravel_index(int(np.argmin(d)), d.shape)
        poly = poly[:i + 1] + ring[j:] + ring[:j] + [ring[j], poly[i]] + poly[i + 1:]
    return poly
