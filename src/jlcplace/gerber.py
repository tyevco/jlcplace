"""Read Gerber fabrication sets (RS-274X, X2 attributes or none) and find where their origin lies on the board.

Frames
------
gerber   A Gerber file's own coordinates: mm here (inch files are converted), y UP, (0, 0) wherever the plotting
         tool put it.

KiCad plots board point b, with plot origin o (the drill/place file origin when "Use drill/place file origin" is
ticked, else the page origin (0, 0)), at

    g = (bx - ox, oy - by)          = convention.board_to_cpl(b, o)

so a Gerber set plotted from o is in the cpl frame of origin o. JLC lays a CPL's Mid X / Mid Y over the Gerbers'
own (0, 0): the CPL is right only when its origin is the Gerbers'. tests/test_gerber.py proves the map on sets that
kicad-cli plots from the dial-me-spinner boards and KiCad's demo boards, from both origins, with and without X2.

The origin check fits  g = s . F . b + t  (F = diag(fx, fy)) between the board's copper pads and the Gerbers' pads.
A sound set has F = diag(1, -1), s = 1, and its origin is the board point b0 with s . F . b0 + t = 0. Other F are
mirrored sets (y not inverted, x mirrored), s = 25.4 or 1/25.4 a unit mix-up. Pads are compared by the centre of
their outline's bounding box on both sides (the flash point is not the copper's centre for a custom pad, and a
region has none), which commutes with every F.

Reader coverage: FS (leading or trailing zeros omitted, absolute or incremental, any digit counts, explicit decimal
points), MO / G70 / G71, AD (C, R, O, P with holes; macros), AM (primitives 1, 2/20, 21, 22, 4, 5, 6, 7, variables,
arithmetic: KiCad's RoundRect, RotRect, Outline*P, FreePoly*, HorizOval), D01 / D02 / D03 (modal in old files),
G01 / G02 / G03 with G74 / G75, G36 / G37 regions (one object per contour), LP, LM / LR / LS, SR, AB blocks, OF /
SF, X2 attributes TF / TA / TO / TD, also as KiCad's X1 comments (G04 #@! ...). Layer functions come from
TF.FileFunction, else a .gbrjob job file, else the file name (Protel extensions .gtl .gbl .gtp .gbp .gts .gko .gm1
..., Eagle's .cmp .sol .crc ..., KiCad's layer names). Excellon drill files are listed as skipped.
"""

from __future__ import annotations

import json
import math
import os
import re
import zipfile
import zlib
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np

from .geom import pad_outline, point_in_polygon
from .model import BOTTOM, RED, TOP, YELLOW, Board, CplRow

#: board pads and Gerber pads closer than this (board mm) are the same pad
MATCH_TOL = 0.02
#: Gerber and CPL origins closer than this (mm) are the same origin
ORIGIN_TOL = 0.01
#: an origin is believed when at least this fraction of the board's pads, and MIN_MATCHED of them (all of them on
#: a board with fewer), land on Gerber pads
MIN_FRACTION = 0.3
MIN_MATCHED = 3
#: the profile layer's box this close to the board's Edge.Cuts box (mm) is the board outline, from the same origin
OUTLINE_TOL = 0.1
#: Gerbers belong to the board when at least this fraction of its SMD pads have a Gerber pad at their place
BELONGS = 0.5
#: total size read from a .zip (uncompressed: a zip bomb is bad input) or a directory
MAX_ZIP = 512 * 1024 * 1024
#: what one untrusted file may expand to (step and repeat, blocks, macros turn a few bytes into millions of objects):
#: objects and outline vertices per file, vertices of one macro primitive (also moire rings) and of one aperture
MAX_OBJECTS = 1_000_000
MAX_VERTICES = 10_000_000
MAX_PRIMITIVE = 4096
MAX_APERTURE = 65536

INCH = 25.4
FLIPS = {"none": (1.0, -1.0), "y": (1.0, 1.0), "x": (-1.0, -1.0), "xy": (-1.0, 1.0)}
SCALES = (1.0, INCH, 1.0 / INCH)
_CIRCLE_N = 64                   # vertices of a circle polygon: a multiple of 4, so its bounding box is exact
_ARC_ERR = 1e-4                  # chord error (mm) of sampled arcs; the extreme points are always vertices

Poly = list[tuple[float, float]]


class GerberError(ValueError):
    """A file that is not readable Gerber, naming the file and the block."""


# ------------------------------------------------------------------------------------------------------- model

@dataclass
class GPad:
    """A pad-like object of a Gerber layer, gerber frame (mm, y up): a flash, a region contour, or a stroke that an
    attribute calls a pad."""
    x: float                       # centre of the outline's bounding box
    y: float
    w: float                       # bounding box size
    h: float
    poly: Poly                     # outline: the aperture's (the convex hull of its dark primitives when it has
                                   # several), the contour, or the stroke's
    kind: str = "flash"            # flash | region | draw
    aperture: str = ""             # "C", "R", "O", "P", a macro's name, "block"; "" for a region
    params: tuple = ()             # the aperture's parameters as written (file units, degrees)
    at: tuple[float, float] | None = None   # the flash point (the aperture's origin)
    dark: bool = True              # LP polarity
    function: str = ""             # TA.AperFunction: "SMDPad,CuDef", "ComponentPad", "ViaPad", "Conductor", ...
    ref: str = ""                  # TO.P reference (else TO.C)
    pin: str = ""                  # TO.P pad number
    pin_name: str = ""             # TO.P pin function
    net: str = ""                  # TO.N
    shapes: list = field(default_factory=list, repr=False, compare=False)  # [(dark, Nx2 array)], aperture order,
                                   # relative to `at` (to (0, 0) for a region)

    def contains(self, q) -> bool:
        """Is gerber point q on this object's dark area? The last primitive containing q decides (a clear primitive
        erases what the aperture's earlier ones drew)."""
        o = self.at or (0.0, 0.0)
        u = (q[0] - o[0], q[1] - o[1])
        dark = False
        for d, poly in self.shapes or [(True, np.asarray(self.poly))]:
            if point_in_polygon(u, [tuple(p) for p in poly]):
                dark = d
        return dark


@dataclass
class GDraw:
    """A stroke (D01 outside a region), gerber frame: a straight segment or an arc of the aperture's width."""
    x1: float
    y1: float
    x2: float
    y2: float
    width: float
    arc: tuple[float, float, bool] | None = None   # (cx, cy, counter-clockwise) for G02 / G03
    dark: bool = True
    function: str = ""
    ref: str = ""
    pin: str = ""
    net: str = ""

    def points(self) -> list[tuple[float, float]]:
        """The centre line: its two ends, arcs sampled (their extreme points included)."""
        if not self.arc:
            return [(self.x1, self.y1), (self.x2, self.y2)]
        return [(self.x1, self.y1)] + _arc_points((self.x1, self.y1), (self.x2, self.y2), self.arc[:2], self.arc[2])


@dataclass
class GerberLayer:
    """One Gerber file."""
    name: str                      # the file's name in the set
    function: str = ""             # X2 file function, e.g. "Copper,L1,Top", "Paste,Top", "Profile,NP"
    kind: str = "other"            # copper | paste | mask | legend | profile | drill | drillmap | component | other
    side: str = ""                 # top | bottom | inner | ""
    source: str = ""               # where the function came from: x2 | comment (G04 #@!) | job | name | ""
    polarity: str = "positive"
    units: str = "mm"              # the file's unit; every coordinate here is mm
    software: str = ""             # TF.GenerationSoftware
    attributes: dict[str, str] = field(default_factory=dict)   # file attributes (TF), names without the dot
    flashes: list[GPad] = field(default_factory=list)
    regions: list[GPad] = field(default_factory=list)
    draws: list[GDraw] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def pads(self) -> list[GPad]:
        """Dark pad-like objects: what TO.P or a pad AperFunction (not ViaPad) calls a pad; without either, every
        flash, and a region on a paste layer, of a component (TO.C) or no larger than 10 mm (a zone is larger)."""
        out = [p for p in self.flashes if p.dark and _padlike(p, True)]
        out += [p for p in self.regions if p.dark and _padlike(p, self.kind == "paste" or bool(p.ref)
                                                                 or max(p.w, p.h) <= 10.0)]
        out += [_stroke_pad(d) for d in self.draws if d.dark and not d.arc and _padlike(d, False)]
        return out

    def bbox(self) -> tuple[float, float, float, float] | None:
        """(xmin, ymin, xmax, ymax) of every object: pad outlines and stroke centre lines."""
        pts = [q for p in self.flashes + self.regions for q in ((p.x - p.w / 2, p.y - p.h / 2),
                                                                 (p.x + p.w / 2, p.y + p.h / 2))]
        pts += [q for d in self.draws for q in d.points()]
        return _box(pts)


@dataclass
class GerberSet:
    """The Gerber files of one board."""
    path: str
    layers: list[GerberLayer] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)   # (file name, why it was not read as Gerber)

    def find(self, kind: str, side: str | None = None) -> list[GerberLayer]:
        return [la for la in self.layers if la.kind == kind and (side is None or la.side == side)]

    def pads(self, side: str, kinds=("copper", "paste")) -> list[GPad]:
        """The pad-like objects of the outer layers of one side (top | bottom), copper and paste by default."""
        return [p for k in kinds for la in self.find(k, side) for p in la.pads()]

    @property
    def outline(self) -> GerberLayer | None:
        prof = self.find("profile")
        return prof[0] if prof else None

    @property
    def extents(self) -> tuple[float, float, float, float] | None:
        """(xmin, ymin, xmax, ymax) of the board outline (the profile layer's centre lines), gerber frame."""
        return self.outline.bbox() if self.outline else None

    @property
    def x2_pins(self) -> bool:
        """Do the copper layers name the reference and pin of their pads (TO.P, X2 or KiCad's X1 comments)?"""
        return any(p.ref and p.pin for la in self.find("copper") for p in la.flashes + la.regions)


def _padlike(p, otherwise: bool) -> bool:
    """A pad by its attributes; `otherwise` when it has none."""
    if p.ref and p.pin:
        return True
    f = p.function
    if not f:
        return otherwise
    return "Pad" in f.split(",")[0] and not f.startswith("ViaPad")


def _stroke_pad(d: GDraw) -> GPad:
    poly = _capsule((d.x1, d.y1), (d.x2, d.y2), d.width / 2.0)
    pad = _pad_from(poly, kind="draw", aperture="C", params=(d.width,))
    pad.dark, pad.function, pad.ref, pad.pin, pad.net = d.dark, d.function, d.ref, d.pin, d.net
    return pad


def _box(pts):
    if not len(pts):
        return None
    a = np.asarray(pts, float).reshape(-1, 2)
    return float(a[:, 0].min()), float(a[:, 1].min()), float(a[:, 0].max()), float(a[:, 1].max())


def _pad_from(poly, **kw) -> GPad:
    a = np.asarray(poly, float).reshape(-1, 2)
    x0, y0, x1, y1 = a[:, 0].min(), a[:, 1].min(), a[:, 0].max(), a[:, 1].max()
    return GPad(x=float(x0 + x1) / 2.0, y=float(y0 + y1) / 2.0, w=float(x1 - x0), h=float(y1 - y0),
                poly=list(map(tuple, a.tolist())), **kw)


# ------------------------------------------------------------------------------------------------------- geometry

def _circle(cx: float, cy: float, r: float, n: int = _CIRCLE_N) -> np.ndarray:
    t = np.arange(n) * (2.0 * math.pi / n)
    return np.column_stack([cx + r * np.cos(t), cy + r * np.sin(t)])


def _rect(cx: float, cy: float, w: float, h: float) -> np.ndarray:
    return np.array([[cx - w / 2, cy - h / 2], [cx + w / 2, cy - h / 2], [cx + w / 2, cy + h / 2],
                     [cx - w / 2, cy + h / 2]], float)


def _capsule(a, b, r: float, n: int = _CIRCLE_N) -> np.ndarray:
    """Stadium around segment ab of radius r (a circle when a == b): the convex hull of two circles."""
    return _hull(np.vstack([_circle(a[0], a[1], r, n), _circle(b[0], b[1], r, n)]))


def _obround(w: float, h: float) -> np.ndarray:
    r = min(w, h) / 2.0
    e = (w - h) / 2.0 if w >= h else (h - w) / 2.0
    a, b = ((-e, 0.0), (e, 0.0)) if w >= h else ((0.0, -e), (0.0, e))
    return _capsule(a, b, r)


def _rotate(p: np.ndarray, deg: float) -> np.ndarray:
    """Counter-clockwise about (0, 0) in the gerber frame (y up)."""
    if not deg:
        return p
    t = math.radians(deg)
    c, s = math.cos(t), math.sin(t)
    return np.column_stack([p[:, 0] * c - p[:, 1] * s, p[:, 0] * s + p[:, 1] * c])


def _hull(p: np.ndarray) -> np.ndarray:
    """Convex hull, counter-clockwise (Andrew's monotone chain)."""
    pts = sorted(set(map(tuple, np.round(np.asarray(p, float), 12).tolist())))     # floats, not numpy scalars
    if len(pts) <= 2:
        return np.asarray(pts, float).reshape(-1, 2)

    def half(seq):
        out = []
        for q in seq:
            while len(out) >= 2 and ((out[-1][0] - out[-2][0]) * (q[1] - out[-2][1])
                                     - (out[-1][1] - out[-2][1]) * (q[0] - out[-2][0])) <= 0:
                out.pop()
            out.append(q)
        return out[:-1]
    return np.asarray(half(pts) + half(pts[::-1]), float)


def _arc_points(s, e, c, ccw: bool, full: bool = False) -> list[tuple[float, float]]:
    """Points after s along the arc about c from s to e (counter-clockwise when ccw, gerber frame), e included,
    every axis extreme the arc passes included, chords within _ARC_ERR."""
    r = math.hypot(s[0] - c[0], s[1] - c[1])
    a0 = math.atan2(s[1] - c[1], s[0] - c[0])
    a1 = math.atan2(e[1] - c[1], e[0] - c[0])
    sweep = (a1 - a0) % (2 * math.pi) if ccw else -((a0 - a1) % (2 * math.pi))
    if full or abs(sweep) < 1e-12 and math.hypot(e[0] - s[0], e[1] - s[1]) < 1e-9:
        sweep = 2 * math.pi if ccw else -2 * math.pi
    step = 2 * math.acos(max(-1.0, 1 - _ARC_ERR / r)) if r > _ARC_ERR else math.pi / 2
    n = min(MAX_PRIMITIVE, max(1, math.ceil(abs(sweep) / min(step, math.pi / 8))))   # a huge radius: coarser
    ts = [a0 + sweep * k / n for k in range(1, n)]
    k0, k1 = sorted((a0, a0 + sweep))
    ts += [q * math.pi / 2 for q in range(math.floor(k0 / (math.pi / 2)) + 1, math.ceil(k1 / (math.pi / 2)))]
    ts.sort(reverse=sweep < 0)
    rr = math.hypot(e[0] - c[0], e[1] - c[1])
    out = []
    for t in ts:                   # the radius moves from the start's to the end's (a file's arcs are not exact)
        f = (t - a0) / sweep if sweep else 1.0
        rad = r + (rr - r) * f
        out.append((c[0] + rad * math.cos(t), c[1] + rad * math.sin(t)))
    out.append((float(e[0]), float(e[1])))
    return out


# ------------------------------------------------------------------------------------------------------- apertures

class _Expr:
    """Aperture macro arithmetic: numbers, $n, unary + -, binary + - x X /, parentheses."""
    _TOK = re.compile(r"\s*(?:(\d+\.?\d*|\.\d+)|\$(\d+)|([-+xX/()]))")

    def __init__(self, text: str, env: dict[int, float]):
        self.toks, pos = [], 0
        text = text.strip()
        while pos < len(text):
            m = self._TOK.match(text, pos)
            if not m or m.end() == pos:
                raise ValueError(f"bad macro expression {text!r}")
            self.toks.append(m.groups())
            pos = m.end()
        self.i, self.env = 0, env

    def value(self) -> float:
        v = self._sum()
        if self.i != len(self.toks):
            raise ValueError("bad macro expression")
        return v

    def _peek(self):
        return self.toks[self.i][2] if self.i < len(self.toks) else None

    def _sum(self) -> float:
        v = self._prod()
        while self._peek() in ("+", "-"):
            op = self._peek()
            self.i += 1
            w = self._prod()
            v = v + w if op == "+" else v - w
        return v

    def _prod(self) -> float:
        v = self._unary()
        while self._peek() in ("x", "X", "/"):
            op = self._peek()
            self.i += 1
            w = self._unary()
            v = v * w if op != "/" else (v / w if w else 0.0)
        return v

    def _unary(self) -> float:
        if self._peek() in ("+", "-"):
            op = self._peek()
            self.i += 1
            v = self._unary()
            return -v if op == "-" else v
        if self.i >= len(self.toks):
            raise ValueError("bad macro expression")
        num, var, op = self.toks[self.i]
        self.i += 1
        if num is not None:
            return float(num)
        if var is not None:
            return self.env.get(int(var), 0.0)
        if op == "(":
            v = self._sum()
            if self._peek() != ")":
                raise ValueError("unbalanced parentheses")
            self.i += 1
            return v
        raise ValueError("bad macro expression")


def _macro_shapes(body: list[str], params: list[float]) -> list[tuple[bool, np.ndarray]]:
    """The primitives of an aperture macro as (dark, polygon), in macro units, in order."""
    env = {i + 1: v for i, v in enumerate(params)}
    out = []
    for blk in body:
        blk = blk.strip()
        if not blk or re.match(r"0(?![\d.])", blk):     # primitive 0: a comment
            continue
        m = re.match(r"\$(\d+)\s*=(.*)$", blk, re.S)
        if m:
            env[int(m.group(1))] = _Expr(m.group(2), env).value()
            continue
        f = [_Expr(s, env).value() if s.strip() else 0.0 for s in blk.split(",")]
        code, a = int(round(f[0])), f[1:]

        def arg(i, d=0.0, a=a):                            # a missing argument is 0
            return a[i] if i < len(a) else d
        if code == 1:                                      # circle: exposure, diameter, x, y[, rotation]
            out.append((arg(0) > 0.5, _rotate(_circle(arg(2), arg(3), arg(1) / 2), arg(4))))
        elif code in (2, 20):                              # vector line: exposure, width, x1, y1, x2, y2, rot
            w, x1, y1, x2, y2 = arg(1), arg(2), arg(3), arg(4), arg(5)
            L = math.hypot(x2 - x1, y2 - y1)
            if L > 0 and w > 0:
                nx, ny = -(y2 - y1) / L * w / 2, (x2 - x1) / L * w / 2
                p = np.array([[x1 + nx, y1 + ny], [x1 - nx, y1 - ny], [x2 - nx, y2 - ny], [x2 + nx, y2 + ny]])
                out.append((arg(0) > 0.5, _rotate(p, arg(6))))
        elif code == 21:                                   # centre line: exposure, w, h, x, y, rot
            out.append((arg(0) > 0.5, _rotate(_rect(arg(3), arg(4), arg(1), arg(2)), arg(5))))
        elif code == 22:                                   # lower-left line: exposure, w, h, x, y, rot
            out.append((arg(0) > 0.5, _rotate(_rect(arg(3) + arg(1) / 2, arg(4) + arg(2) / 2, arg(1), arg(2)),
                                              arg(5))))
        elif code == 4:                                    # outline: exposure, n, x0, y0, ... xn, yn, rot
            n = int(round(arg(1)))
            if not 0 < n <= min(MAX_PRIMITIVE, (len(a) - 2) // 2 - 1):
                raise ValueError(f"an outline of {n} vertices with {max(0, len(a) - 3) // 2} points given")
            pts = np.array([[arg(2 + 2 * k), arg(3 + 2 * k)] for k in range(n + 1)], float)
            if len(pts) > 1 and np.allclose(pts[0], pts[-1]):
                pts = pts[:-1]
            out.append((arg(0) > 0.5, _rotate(pts, arg(4 + 2 * n))))
        elif code == 5:                                    # polygon: exposure, n, x, y, diameter, rot
            n = max(3, int(round(arg(1))))
            if n > MAX_PRIMITIVE:
                raise ValueError(f"a polygon of {n} vertices")
            out.append((arg(0) > 0.5, _rotate(_circle(arg(2), arg(3), arg(4) / 2, n), arg(5))))
        elif code == 6:                                    # moire: x, y, D, ring, gap, rings, cross w, cross L, rot
            cx, cy, D, th, gap, nr, cw, cl, rot = (arg(i) for i in range(9))
            if nr > MAX_PRIMITIVE:
                raise ValueError(f"a moire of {nr:g} rings")
            r = D / 2
            for _ in range(max(0, int(round(nr)))):
                if r <= 0:
                    break
                out.append((True, _rotate(_circle(cx, cy, r), rot)))
                if r - th > 0:
                    out.append((False, _rotate(_circle(cx, cy, r - th), rot)))
                r -= th + gap
            if cw > 0 and cl > 0:
                out.append((True, _rotate(_rect(cx, cy, cl, cw), rot)))
                out.append((True, _rotate(_rect(cx, cy, cw, cl), rot)))
        elif code == 7:                                    # thermal: x, y, outer D, inner d, gap, rot
            cx, cy, D, d, gap, rot = (arg(i) for i in range(6))
            out.append((True, _rotate(_circle(cx, cy, D / 2), rot)))
            out.append((False, _rotate(_circle(cx, cy, d / 2), rot)))
            out.append((False, _rotate(_rect(cx, cy, D, gap), rot)))
            out.append((False, _rotate(_rect(cx, cy, gap, D), rot)))
        elif code == 3:                                    # end of file marker in some old macros
            continue
        else:
            raise ValueError(f"unknown macro primitive {code}")
    return out


@dataclass
class _Aperture:
    template: str
    params: tuple
    shapes: list                   # [(dark, Nx2 array)] mm, aperture frame
    function: str = ""
    width: float = 0.0             # the stroke width a draw with it has (circle diameter, rectangle's smaller side)
    block: list | None = None      # an AB block: objects relative to its origin
    _cache: dict = field(default_factory=dict)

    def outline(self, mirror: str, rot: float, scale: float):
        """(outline, shapes) after LM, LS and LR (mirror first, then scale, then rotate)."""
        key = (mirror, rot, scale)
        if key not in self._cache:
            shapes = []
            for d, p in self.shapes:
                q = p.copy()
                if "X" in mirror:
                    q[:, 0] = -q[:, 0]
                if "Y" in mirror:
                    q[:, 1] = -q[:, 1]
                shapes.append((d, _rotate(q * scale, rot)))
            darks = [p for d, p in shapes if d]
            if len(darks) == 1:
                poly = darks[0]
            else:
                poly = _hull(np.vstack(darks)) if darks else np.zeros((0, 2))
            self._cache[key] = (poly, shapes)
        return self._cache[key]


def _standard_shapes(t: str, a: list[float]) -> list[tuple[bool, np.ndarray]]:
    def hole(i):
        return [(False, _circle(0.0, 0.0, a[i] / 2))] if len(a) > i and a[i] > 0 else []
    if t == "C":
        return [(True, _circle(0.0, 0.0, a[0] / 2))] + hole(1)
    if t == "R":
        return [(True, _rect(0.0, 0.0, a[0], a[1]))] + hole(2)
    if t == "O":
        return [(True, _obround(a[0], a[1]))] + hole(2)
    if t == "P":
        if len(a) > 1 and a[1] > MAX_PRIMITIVE:
            raise ValueError(f"a polygon of {a[1]:g} vertices")
        return [(True, _rotate(_circle(0.0, 0.0, a[0] / 2, max(3, int(round(a[1])))), a[2] if len(a) > 2 else 0.0))
                ] + hole(3)
    raise KeyError(t)


# ------------------------------------------------------------------------------------------------------- parser

_TOKENS = re.compile(r"%([^%]*)%|([^%*]*)\*")
_WORD = re.compile(r"([A-Z])([+-]?[0-9.]*)")
_ESC = re.compile(r"\\u([0-9a-fA-F]{4})|\\U([0-9a-fA-F]{8})")


def _unescape(s: str) -> str:
    return _ESC.sub(lambda m: chr(int(m.group(1) or m.group(2), 16)), s)


class _Parser:
    def __init__(self, name: str):
        self.layer = GerberLayer(name=name)
        self.name = name
        self.fmt = None                     # {"X": (int, dec), "Y": ..., "zeros": L|T, "inc": bool}
        self.unit = None                    # mm per file unit
        self.aps: dict[int, _Aperture] = {}
        self.macros: dict[str, list[str]] = {}
        self.ap: _Aperture | None = None
        self.x = self.y = 0.0               # current point, mm
        self.interp, self.quadrant, self.op = 1, None, None
        self.region: list | None = None     # contours of the open G36
        self.contour: list | None = None
        self.dark, self.mirror, self.rot, self.scale = True, "N", 0.0, 1.0
        self.ta: dict[str, str] = {}
        self.to: dict[str, str] = {}
        self.sr = None                      # (nx, ny, i, j, marks)
        self.blocks: list = []              # open AB blocks: (code, saved target)
        self.target = (self.layer.flashes, self.layer.regions, self.layer.draws)
        self.offset = (0.0, 0.0)            # %OF
        self.sf = (1.0, 1.0)                # %SF
        self.block_no = 0
        self.objects = self.vertices = 0    # spent of MAX_OBJECTS, MAX_VERTICES

    def fail(self, msg: str):
        raise GerberError(f"{self.name}: block {self.block_no}: {msg}")

    def spend(self, objects: int, vertices: int):
        """Count objects about to be stored against the file's budget: an untrusted file is refused before it
        expands past MAX_OBJECTS objects or MAX_VERTICES outline vertices (a GerberError, not a MemoryError)."""
        self.objects += objects
        self.vertices += vertices
        if self.objects > MAX_OBJECTS or self.vertices > MAX_VERTICES:
            self.fail(f"expands to more than {MAX_OBJECTS} objects or {MAX_VERTICES} vertices (step and repeat, "
                      "blocks or macros): not a plot of a board")

    # -------------------------------------------------------------------- top level
    def parse(self, text: str) -> GerberLayer:
        text = text.replace("\r", "").replace("\n", "")
        for m in _TOKENS.finditer(text):
            self.block_no += 1
            ext, word = m.group(1), m.group(2)
            try:
                if ext is not None:
                    self.extended(ext)
                else:
                    if self.word(word.strip()):
                        break
            except GerberError:
                raise
            except (ValueError, KeyError, IndexError, ZeroDivisionError, OverflowError, TypeError, RecursionError) as e:
                self.fail(f"{(ext if ext is not None else word)[:60]!r}: {e}")
        if self.sr:
            self.close_sr()
        la = self.layer
        la.units = "in" if self.unit == INCH else "mm"
        if self.unit is None:
            la.notes.append("no unit (MO): read as mm")
        return la

    def extended(self, ext: str):
        blocks = ext.split("*")
        head = blocks[0].strip()
        if head.startswith("AM"):
            self.macros[head[2:].strip()] = [b for b in blocks[1:] if b.strip()]
            return
        for b in blocks:
            b = b.strip()
            if b:
                self.command(b)

    def command(self, b: str):
        c = b[:2]
        if c == "FS":
            m = re.match(r"FS([LTD]?)([AI]?).*?X(\d)(\d)Y(\d)(\d)", b)
            if not m:
                self.fail(f"bad format {b!r}")
            self.fmt = {"zeros": m.group(1) or "L", "inc": m.group(2) == "I",
                        "X": (int(m.group(3)), int(m.group(4))), "Y": (int(m.group(5)), int(m.group(6)))}
        elif c == "MO":
            self.unit = INCH if b[2:4] == "IN" else 1.0
        elif c == "AD":
            self.define(b)
        elif c == "AB":
            self.block(b[2:])
        elif c == "LP":
            self.dark = b[2:3] != "C"
        elif c == "LM":
            self.mirror = b[2:] or "N"
        elif c == "LR":
            self.rot = float(b[2:])
        elif c == "LS":
            self.scale = float(b[2:])
        elif c == "SR":
            self.step_repeat(b[2:])
        elif c in ("TF", "TA", "TO", "TD"):
            self.attribute(b, "x2")
        elif c == "OF":
            a = re.search(r"A([+-]?[\d.]+)", b)
            bb = re.search(r"B([+-]?[\d.]+)", b)
            self.offset = (float(a.group(1)) if a else 0.0, float(bb.group(1)) if bb else 0.0)
        elif c == "SF":
            a = re.search(r"A([+-]?[\d.]+)", b)
            bb = re.search(r"B([+-]?[\d.]+)", b)
            self.sf = (float(a.group(1)) if a else 1.0, float(bb.group(1)) if bb else 1.0)
        elif c == "IP":
            if b[2:5] == "NEG":
                self.layer.polarity = "negative"
                self.layer.notes.append("negative image (IPNEG)")
        elif c in ("MI", "IR", "AS"):
            if b not in ("MIA0B0", "IR0", "ASAXBY", "MIA0", "MIB0"):
                self.layer.notes.append(f"deprecated image transformation {b} ignored")
        # IN, LN and anything else: names and options that change no geometry

    def attribute(self, b: str, source: str):
        kind, rest = b[:2], b[2:]
        if kind == "TD":
            name = rest.lstrip(".").strip()
            if not name:
                self.ta.clear()
                self.to.clear()
            else:
                self.ta.pop(name, None)
                self.to.pop(name, None)
            return
        name, _, value = rest.partition(",")
        name, value = name.lstrip(".").strip(), _unescape(value)
        if kind == "TF":
            self.layer.attributes[name] = value
            if name == "FileFunction":
                self.layer.function, self.layer.source = value, source
            elif name == "FilePolarity":
                self.layer.polarity = value.strip().lower() or "positive"
            elif name == "GenerationSoftware":
                self.layer.software = value
        elif kind == "TA":
            self.ta[name] = value
        else:
            self.to[name] = value

    # -------------------------------------------------------------------- apertures
    def define(self, b: str):
        m = re.match(r"ADD(\d+)([^,]*)(?:,(.*))?$", b, re.S)
        if not m:
            self.fail(f"bad aperture definition {b!r}")
        code, t = int(m.group(1)), m.group(2).strip()
        raw = [s for s in (m.group(3) or "").split("X") if s.strip()]
        params = [float(s) for s in raw]
        u = self.unit or 1.0
        fn = self.ta.get("AperFunction", "")
        if t in ("C", "R", "O", "P"):
            lengths = [v * u for v in params]
            if t == "P" and len(lengths) > 1:
                lengths[1] = params[1]                     # vertex count and rotation are not lengths
                if len(lengths) > 2:
                    lengths[2] = params[2]
            shapes = _standard_shapes(t, lengths)
            width = lengths[0] if t in ("C", "P") else min(lengths[0], lengths[1])
        elif t in self.macros:
            shapes = [(d, p * u) for d, p in _macro_shapes(self.macros[t], params)]
            if sum(len(p) for _, p in shapes) > MAX_APERTURE:
                self.fail(f"aperture D{code} ({t}) has more than {MAX_APERTURE} vertices")
            width = 0.0
        else:
            self.fail(f"aperture D{code} uses the undefined macro {t!r}")
        self.aps[code] = _Aperture(template=t, params=tuple(params), shapes=shapes, function=fn, width=width)

    def block(self, rest: str):
        rest = rest.strip()
        if rest.startswith("D"):
            code = int(rest[1:])
            store = ([], [], [])
            self.blocks.append((code, self.target, (self.x, self.y)))
            self.target = store
        else:
            if not self.blocks:
                self.fail("AB closes no block")
            code, saved, _ = self.blocks.pop()
            objs = self.target
            self.target = saved
            self.aps[code] = _Aperture(template="block", params=(), shapes=[], block=[list(o) for o in objs])

    # -------------------------------------------------------------------- step and repeat
    def step_repeat(self, rest: str):
        if self.sr:
            self.close_sr()
        m = re.match(r"X(\d+)Y(\d+)I([+-]?[\d.]+)J([+-]?[\d.]+)", rest.strip())
        if not m:
            return
        nx, ny = int(m.group(1)), int(m.group(2))
        if nx > 1 or ny > 1:
            u = self.unit or 1.0
            self.sr = (nx, ny, float(m.group(3)) * u, float(m.group(4)) * u, [len(t) for t in self.target])

    def close_sr(self):
        nx, ny, di, dj, marks = self.sr
        self.sr = None
        bases = [lst[mark:] for lst, mark in zip(self.target, marks)]
        self.spend((nx * ny - 1) * sum(map(len, bases)),
                   (nx * ny - 1) * sum(len(o.poly) if isinstance(o, GPad) else 2 for b in bases for o in b))
        for lst, base in zip(self.target, bases):
            for ix in range(nx):
                for iy in range(ny):
                    if ix or iy:
                        lst.extend(_moved(o, ix * di, iy * dj) for o in base)

    # -------------------------------------------------------------------- word commands
    def coord(self, v: str, axis: str) -> float:
        if "." in v:
            return float(v) * (self.unit or 1.0)
        if self.fmt is None:
            self.fail("a coordinate before the format statement (FS)")
        ni, nd = self.fmt["X" if axis in "XI" else "Y"]
        sign = -1.0 if v.startswith("-") else 1.0
        digits = v.lstrip("+-")
        if self.fmt["zeros"] == "T":
            digits = digits.ljust(ni + nd, "0")
        return sign * int(digits or "0") / 10.0 ** nd * (self.unit or 1.0)

    def word(self, w: str) -> bool:
        """Process one word command; True at the end of the file."""
        if not w:
            return False
        if re.match(r"G0*4(?!\d)", w):
            m = re.match(r"G0*4\s*#@!\s*(T[FAOD].*)$", w)
            if m:
                self.attribute(m.group(1).strip(), "comment")
            return False
        if w.startswith("M"):
            return w[1:].lstrip("0") in ("", "2")          # M02 ends the file, M00 stops it, M01 is optional
        vals, d = {}, None
        for k, v in _WORD.findall(w):
            if k == "G":
                self.gcode(int(float(v)))
            elif k == "D":
                d = int(v)
            elif k in "XYIJ":
                vals[k] = v
        if d is not None and d >= 10:
            if d not in self.aps:
                self.fail(f"D{d} is not defined")
            self.ap = self.aps[d]
            return False
        if d is None and not vals:
            return False
        if d is None:
            d = self.op                    # deprecated modal operation
            if d is None:
                return False
        self.op = d
        self.operate(d, vals)
        return False

    def gcode(self, g: int):
        if g in (1, 2, 3):
            self.interp = g
        elif g == 36:
            self.region, self.contour = [], None
        elif g == 37:
            self.end_region()
        elif g == 74:
            self.quadrant = "single"
        elif g == 75:
            self.quadrant = "multi"
        elif g == 70:
            self.unit = INCH
        elif g == 71:
            self.unit = 1.0
        elif g == 91:
            if self.fmt is not None:
                self.fmt["inc"] = True
        elif g == 90:
            if self.fmt is not None:
                self.fmt["inc"] = False

    def point(self, vals) -> tuple[float, float]:
        inc = self.fmt is not None and self.fmt["inc"]
        x = self.coord(vals["X"], "X") if "X" in vals else (0.0 if inc else None)
        y = self.coord(vals["Y"], "Y") if "Y" in vals else (0.0 if inc else None)
        if inc:
            return self.x + x * self.sf[0], self.y + y * self.sf[1]
        return (self.x if x is None else x * self.sf[0] + self.offset[0] * (self.unit or 1.0),
                self.y if y is None else y * self.sf[1] + self.offset[1] * (self.unit or 1.0))

    def operate(self, d: int, vals):
        start = (self.x, self.y)
        end = self.point(vals)
        if d == 2:
            if self.region is not None:
                self.close_contour()
                self.contour = [end]
        elif d == 1:
            arc = None
            if self.interp in (2, 3):
                ij = (self.coord(vals.get("I", "0"), "I"), self.coord(vals.get("J", "0"), "J"))
                signed = any(vals.get(k, "").startswith("-") for k in "IJ")
                arc = self.arc_centre(start, end, ij, ccw=self.interp == 3,
                                      single=self.quadrant == "single" or (self.quadrant is None and not signed))
            if self.region is not None:
                if self.contour is None:
                    self.contour = [start]
                if arc:
                    self.contour += _arc_points(start, end, arc[:2], arc[2], full=arc[3])
                else:
                    self.contour.append(end)
            else:
                self.draw(start, end, arc)
        elif d == 3:
            if self.region is None:
                self.flash(end)
        else:
            self.fail(f"D0{d} is not an operation")
        self.x, self.y = end

    def arc_centre(self, s, e, ij, ccw: bool, single: bool):
        """(cx, cy, ccw, full circle) of an arc from s to e. Multi quadrant (G75): the centre is s + (I, J).
        Single quadrant (G74): I and J are unsigned, and the centre is the one of the four that gives an arc of at
        most 90 degrees with its ends equally far from it."""
        if not single:
            c = (s[0] + ij[0], s[1] + ij[1])
            return c[0], c[1], ccw, math.hypot(e[0] - s[0], e[1] - s[1]) < 1e-9
        best = None
        for sx in (1, -1):
            for sy in (1, -1):
                c = (s[0] + sx * abs(ij[0]), s[1] + sy * abs(ij[1]))
                a0 = math.atan2(s[1] - c[1], s[0] - c[0])
                a1 = math.atan2(e[1] - c[1], e[0] - c[0])
                sweep = (a1 - a0) % (2 * math.pi) if ccw else (a0 - a1) % (2 * math.pi)
                if sweep > math.pi / 2 + 1e-6:
                    continue
                err = abs(math.hypot(s[0] - c[0], s[1] - c[1]) - math.hypot(e[0] - c[0], e[1] - c[1]))
                if best is None or err < best[0]:
                    best = (err, c)
        c = best[1] if best else (s[0] + ij[0], s[1] + ij[1])
        return c[0], c[1], ccw, False

    def attrs(self):
        p = self.to.get("P", "").split(",")
        ref = p[0] if p[0] else self.to.get("C", "")
        return {"function": self.ta.get("AperFunction", ""), "ref": ref, "pin": p[1] if len(p) > 1 else "",
                "net": self.to.get("N", "")}

    def flash(self, at):
        if self.ap is None:
            self.fail("a flash with no aperture selected")
        ap = self.ap
        if ap.block is not None:
            self.flash_block(ap, at)
            return
        poly, shapes = ap.outline(self.mirror, self.rot, self.scale)
        if not len(poly):
            return
        a = self.attrs()
        a["function"] = ap.function
        pin_name = self.to.get("P", "").split(",")
        self.spend(1, len(poly))
        pad = _pad_from(poly + np.asarray(at), kind="flash", aperture=ap.template, params=ap.params,
                        at=(float(at[0]), float(at[1])), dark=self.dark, shapes=shapes, **a)
        pad.pin_name = pin_name[2] if len(pin_name) > 2 else ""
        self.target[0].append(pad)

    def flash_block(self, ap: _Aperture, at):
        mirror, rot, scale = self.mirror, self.rot, self.scale

        def fn(p):
            q = np.asarray(p, float).reshape(-1, 2).copy()
            if "X" in mirror:
                q[:, 0] = -q[:, 0]
            if "Y" in mirror:
                q[:, 1] = -q[:, 1]
            return _rotate(q * scale, rot) + np.asarray(at)
        flip = mirror in ("X", "Y")
        self.spend(sum(map(len, ap.block)), sum(len(o.poly) if isinstance(o, GPad) else 2 for b in ap.block for o in b))
        for lst, objs in zip(self.target, ap.block):
            for o in objs:
                o = _mapped(o, fn, flip)
                if not self.dark:
                    o.dark = not o.dark
                lst.append(o)

    def draw(self, s, e, arc):
        if self.ap is None:
            self.fail("a draw with no aperture selected")
        a = self.attrs()
        a["function"] = self.ap.function
        width = self.ap.width * self.scale
        self.spend(1, 2)
        if arc and arc[3]:                 # a full circle: two half arcs
            c = (arc[0], arc[1])
            m = (2 * c[0] - s[0], 2 * c[1] - s[1])
            self.target[2].append(GDraw(s[0], s[1], m[0], m[1], width, (c[0], c[1], arc[2]), self.dark, **a))
            self.target[2].append(GDraw(m[0], m[1], e[0], e[1], width, (c[0], c[1], arc[2]), self.dark, **a))
            return
        self.target[2].append(GDraw(s[0], s[1], e[0], e[1], width, arc[:3] if arc else None, self.dark, **a))

    def close_contour(self):
        if self.contour and len(self.contour) >= 3:
            pts = self.contour
            if math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) < 1e-9:
                pts = pts[:-1]
            if len(pts) >= 3:
                self.region.append(pts)
        self.contour = None

    def end_region(self):
        if self.region is None:
            return
        self.close_contour()
        a = self.attrs()
        self.spend(len(self.region), sum(map(len, self.region)))
        for pts in self.region:
            arr = np.asarray(pts, float)
            pad = _pad_from(arr, kind="region", dark=self.dark, shapes=[(True, arr)], **a)
            self.target[1].append(pad)
        self.region, self.contour = None, None


def _moved(o, dx: float, dy: float):
    """A copy of a GPad or GDraw moved by (dx, dy) (step and repeat: often hundreds of thousands of copies)."""
    if isinstance(o, GDraw):
        return replace(o, x1=o.x1 + dx, y1=o.y1 + dy, x2=o.x2 + dx, y2=o.y2 + dy,
                       arc=(o.arc[0] + dx, o.arc[1] + dy, o.arc[2]) if o.arc else None)
    return replace(o, x=o.x + dx, y=o.y + dy, poly=[(u + dx, v + dy) for u, v in o.poly],
                   at=(o.at[0] + dx, o.at[1] + dy) if o.at else None,
                   shapes=o.shapes if o.at else [(d, p + (dx, dy)) for d, p in o.shapes])


def _mapped(o, fn, flip: bool):
    """A copy of a GPad or GDraw with every point mapped by fn (an isometry or a scaling); flip: fn mirrors."""
    if isinstance(o, GDraw):
        (x1, y1), (x2, y2) = fn([(o.x1, o.y1), (o.x2, o.y2)])
        arc = None
        if o.arc:
            (cx, cy), = fn([o.arc[:2]])
            arc = (float(cx), float(cy), o.arc[2] != flip)
        scale = float(np.hypot(*(fn([(1.0, 0.0)])[0] - fn([(0.0, 0.0)])[0])))
        return GDraw(float(x1), float(y1), float(x2), float(y2), o.width * scale, arc, o.dark, o.function, o.ref,
                     o.pin, o.net)
    at = o.at or (0.0, 0.0)
    if o.shapes:
        base = np.asarray(at)
        shapes = [(d, fn(p + base)) for d, p in o.shapes]
        new_at = tuple(float(v) for v in fn([at])[0]) if o.at else None
        rel = [(d, p - (np.asarray(new_at) if new_at else 0.0)) for d, p in shapes]
    else:
        rel, new_at = [], tuple(float(v) for v in fn([at])[0]) if o.at else None
    pad = _pad_from(fn(o.poly), kind=o.kind, aperture=o.aperture, params=o.params, at=new_at, dark=o.dark,
                    function=o.function, ref=o.ref, pin=o.pin, pin_name=o.pin_name, net=o.net, shapes=rel)
    return pad


def parse_gerber(text: str, name: str = "") -> GerberLayer:
    """One Gerber file's text as a GerberLayer (its function from its X2 attributes or, failing that, its name)."""
    la = _Parser(name).parse(text)
    if not la.function:
        fn = classify(name)
        if fn:
            la.function, la.source = fn, "name"
    la.kind, la.side = function_kind(la.function)
    return la


# ------------------------------------------------------------------------------------------------------- layer names

_KIND = {"copper": "copper", "plated": "drill", "nonplated": "drill", "profile": "profile", "soldermask": "mask",
         "paste": "paste", "solderpaste": "paste", "legend": "legend", "component": "component",
         "drillmap": "drillmap", "keep-out": "other", "assemblydrawing": "other", "fabricationdrawing": "other"}
_SIDE = {"top": TOP, "bot": BOTTOM, "inr": "inner"}

# Protel / Altium, KiCad and Eagle extensions
_EXT = {".gtl": "Copper,L1,Top", ".gbl": "Copper,Bot", ".gtp": "Paste,Top", ".gbp": "Paste,Bot",
        ".gts": "Soldermask,Top", ".gbs": "Soldermask,Bot", ".gto": "Legend,Top", ".gbo": "Legend,Bot",
        ".gko": "Profile,NP", ".gm1": "Profile,NP", ".gml": "Profile,NP", ".gm": "Profile,NP",
        ".cmp": "Copper,L1,Top", ".sol": "Copper,Bot", ".crc": "Paste,Top", ".crs": "Paste,Bot",
        ".stc": "Soldermask,Top", ".sts": "Soldermask,Bot", ".plc": "Legend,Top", ".pls": "Legend,Bot",
        ".dim": "Profile,NP", ".top": "Copper,L1,Top", ".bot": "Copper,Bot", ".smt": "Soldermask,Top",
        ".smb": "Soldermask,Bot", ".spt": "Paste,Top", ".spb": "Paste,Bot", ".sst": "Legend,Top",
        ".ssb": "Legend,Bot", ".oln": "Profile,NP", ".out": "Profile,NP"}
_NAME_RULES = [            # (regular expression on the lower-case stem, function), first match wins
    (r"(^|[-_. ])f[._]cu$", "Copper,L1,Top"), (r"(^|[-_. ])b[._]cu$", "Copper,Bot"),
    (r"(^|[-_. ])in\d+[._]cu$", "Copper,Inr"),
    (r"(^|[-_. ])f[._]paste$", "Paste,Top"), (r"(^|[-_. ])b[._]paste$", "Paste,Bot"),
    (r"(^|[-_. ])f[._]mask$", "Soldermask,Top"), (r"(^|[-_. ])b[._]mask$", "Soldermask,Bot"),
    (r"(^|[-_. ])f[._]silk", "Legend,Top"), (r"(^|[-_. ])b[._]silk", "Legend,Bot"),
    (r"(^|[-_. ])edge[._]cuts$", "Profile,NP"),
    (r"(paste|cream|stencil).*(top|front)|(top|front).*(paste|cream|stencil)", "Paste,Top"),
    (r"(paste|cream|stencil).*(bot|back)|(bot|back).*(paste|cream|stencil)", "Paste,Bot"),
    (r"(mask|stop).*(top|front)|(top|front).*(mask|stop)", "Soldermask,Top"),
    (r"(mask|stop).*(bot|back)|(bot|back).*(mask|stop)", "Soldermask,Bot"),
    (r"(silk|legend|overlay).*(top|front)|(top|front).*(silk|legend|overlay)", "Legend,Top"),
    (r"(silk|legend|overlay).*(bot|back)|(bot|back).*(silk|legend|overlay)", "Legend,Bot"),
    (r"outline|profile|edge|dimension|milling|boardshape", "Profile,NP"),
    (r"(copper|signal|layer|cu).*(top|front)|(top|front).*(copper|signal|layer|cu)|^top$", "Copper,L1,Top"),
    (r"(copper|signal|layer|cu).*(bot|back)|(bot|back).*(copper|signal|layer|cu)|^bottom$", "Copper,Bot"),
]


def classify(name: str) -> str:
    """An X2-like file function guessed from a file name, or "" (X1 sets say what a file is only by its name)."""
    p = Path(name)
    ext, stem = p.suffix.lower(), p.stem.lower()
    if ext in _EXT:
        return _EXT[ext]
    if re.fullmatch(r"\.g\d+", ext) or re.fullmatch(r"\.gp\d+", ext):
        return "Copper,Inr"
    if re.fullmatch(r"\.gm\d+", ext):
        return "Profile,NP" if ext == ".gm1" else "Other,Mechanical"
    if ext in (".drl", ".xln", ".exc", ".drd"):
        return "Plated,Drill"
    for rx, fn in _NAME_RULES:
        if re.search(rx, stem):
            return fn
    return ""


def function_kind(function: str) -> tuple[str, str]:
    """(kind, side) of an X2 file function: ("copper", "top") for "Copper,L1,Top", ("paste", "bottom") for
    "Paste,Bot", ("profile", "") for "Profile,NP"."""
    f = [s.strip() for s in function.split(",")]
    kind = _KIND.get(f[0].lower(), "other") if f[0] else "other"
    side = ""
    for s in f[1:]:
        if s.lower() in _SIDE:
            side = _SIDE[s.lower()]
            break
    return kind, side


# ------------------------------------------------------------------------------------------------------- reading sets

def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _what(name: str, data: bytes) -> str:
    """gerber | excellon | job | archive | other."""
    low = name.lower()
    head = data[:65536]
    if low.endswith(".gbrjob"):
        return "job"
    if low.endswith(".zip"):
        return "archive"
    if re.search(rb"%FS|%MO|%TF|G04 #@!", head):
        return "gerber"
    if re.search(rb"(^|[\r\n])\s*M48", head):
        return "excellon"
    ext = Path(low).suffix
    if (ext in _EXT or re.fullmatch(r"\.(gbr|ger|pho|art|g\d+|gm\d+|gp\d+)", ext)) and re.search(rb"D0?[123]\*", head):
        return "gerber"
    return "other"


_FUNCTION = re.compile(rb"(?:%TF\.FileFunction,|G04 #@! TF\.FileFunction,)([^*%]*)\*")
#: the layers the check reads (others are listed as not read: parsing them costs time and is untrusted work)
_KIND_WORDS = {"copper": "copper", "mask": "mask", "legend": "legend", "drillmap": "drill map",
               "component": "component", "other": "drawing or other", "drill": "drill"}


def _used(kind: str, side: str) -> bool:
    return kind in ("copper", "paste") and side != "inner" or kind == "profile"


def _head(f: Path) -> bytes:
    with f.open("rb") as fh:
        return fh.read(65536)


def _read_zip(p: Path) -> list[tuple[str, bytes]]:
    files = []
    try:
        with zipfile.ZipFile(p) as z:
            infos = [i for i in z.infolist() if not i.is_dir()]
            if sum(i.file_size for i in infos) > MAX_ZIP:
                raise GerberError(f"{p.name}: more than {MAX_ZIP >> 20} MB uncompressed")
            for i in sorted(infos, key=lambda i: i.filename):
                try:
                    files.append((i.filename, z.read(i)))
                except (RuntimeError, NotImplementedError, zlib.error, EOFError, zipfile.BadZipFile) as e:
                    # encrypted, Deflate64 (Windows Explorer, 7-Zip), corrupt
                    raise GerberError(f"{p.name}: {i.filename}: cannot be read ({e})") from None
    except zipfile.BadZipFile as e:
        raise GerberError(f"{p.name}: not a zip file ({e})") from None
    return files


def read_gerbers(path) -> GerberSet:
    """The Gerber files of a directory (its files, not its subdirectories), of a .zip (every member), or one file.
    Files that are not Gerber (Excellon drill files, the job file, documents) are listed in `skipped`; a .gbrjob
    job file names the functions of files that carry no X2 attributes. Only the layers the check uses are parsed:
    outer copper, paste, the profile, and files whose function nothing names; the others (inner copper, mask,
    legend, drawings: named by their own TF.FileFunction or G04 #@! comment, the job file, or their name) are
    listed in `skipped` unread. A directory that holds no Gerber files names its subdirectories that do.
    GerberError for a file that cannot be read as Gerber, or a zip that cannot be read."""
    p = Path(path)
    files: list[tuple[str, bytes]] = []
    subdirs: list[Path] = []
    if p.is_dir():
        entries = sorted(p.iterdir())
        subdirs = [q for q in entries if q.is_dir()]
        if sum(q.stat().st_size for q in entries if q.is_file()) > MAX_ZIP:
            raise GerberError(f"{p.name}: more than {MAX_ZIP >> 20} MB of files")
        files = [(q.name, q.read_bytes()) for q in entries if q.is_file()]
    elif p.suffix.lower() == ".zip":
        files = _read_zip(p)
    elif p.is_file():
        files.append((p.name, p.read_bytes()))
    else:
        raise FileNotFoundError(f"no such file or directory: {os.fspath(path)}")
    gs = GerberSet(path=os.fspath(path))
    jobs: dict[str, str] = {}
    kinds = [_what(name, data) for name, data in files]
    for (name, data), what in zip(files, kinds):
        if what == "job":
            try:
                job = json.loads(_decode(data))
                for f in job.get("FilesAttributes", []):
                    jobs[Path(f.get("Path", "")).name] = f.get("FileFunction", "")
            except (ValueError, AttributeError, TypeError):
                pass
    for (name, data), what in zip(files, kinds):
        if what == "job":
            gs.skipped.append((name, "Gerber job file"))
        elif what == "gerber":
            m = _FUNCTION.search(data[:65536])
            fn = _unescape(m.group(1).decode("latin-1")).strip() if m else jobs.get(Path(name).name) or classify(name)
            kind, side = function_kind(fn)
            if fn and not _used(kind, side):
                label = "an inner copper" if kind == "copper" else f"a {_KIND_WORDS.get(kind, kind)}"
                gs.skipped.append((name, f"not read: {label} layer ({fn}), which the check does not use"))
                continue
            la = parse_gerber(_decode(data), name)
            if la.source in ("", "name") and jobs.get(Path(name).name):
                la.function, la.source = jobs[Path(name).name], "job"
                la.kind, la.side = function_kind(la.function)
            gs.layers.append(la)
        else:
            gs.skipped.append((name, {"excellon": "Excellon drill file", "archive": "archive: pass it as the path",
                                      "other": "not a Gerber file"}[what]))
    if not gs.layers:
        for q in subdirs:
            if any(_what(f.name, _head(f)) == "gerber" for f in sorted(q.iterdir()) if f.is_file()):
                gs.skipped.append((f"{q.name}/", "a directory of Gerber files: pass it as the path"))
    return gs


def read_gerber_text(texts: dict[str, str]) -> GerberSet:
    """A GerberSet from {file name: Gerber text} (tests, and callers that hold the files in memory)."""
    return GerberSet(path="", layers=[parse_gerber(t, n) for n, t in texts.items()])


# ------------------------------------------------------------------------------------------------------- board side

@dataclass
class BoardPad:
    """A board pad as the Gerbers show it: board frame (mm, y down)."""
    ref: str
    number: str
    side: str                      # top | bottom | both (plated through)
    x: float                       # centre of the copper outline's bounding box
    y: float
    w: float                       # bounding box size along the board axes
    h: float
    kind: str = "smd"              # model.Pad.kind
    copper: bool = True            # has outer copper (model.Pad.copper or .cu): False for paste-only apertures
    net: str = ""


def board_pads(board: Board, *, apertures: bool = False) -> list[BoardPad]:
    """Every footprint's copper pads (DNP parts too: KiCad plots their pads) on the outer copper they have
    (model.Pad.cu: an edge connector's pads on the far side of its part are there), with paste-only apertures (on
    the part's side) when asked. The outline is model.Pad.poly, else geom.pad_outline, mapped as
    convention.library_to_board does."""
    out = []
    for part in board.parts:
        bottom = part.layer == BOTTOM
        t = math.radians(part.angle)
        c, s = math.cos(t), math.sin(t)
        for pad in part.pads:
            copper = pad.kind != "npth" and (pad.copper or bool(pad.cu))
            if not copper and not (apertures and pad.kind == "smd"):
                continue
            pts = np.asarray(pad.poly if pad.poly else pad_outline(pad), float)
            lx, ly = pts[:, 0], -pts[:, 1] if bottom else pts[:, 1]
            bx, by = part.x + lx * c + ly * s, part.y - lx * s + ly * c      # P + R(theta) . D . l
            x0, x1, y0, y1 = bx.min(), bx.max(), by.min(), by.max()
            side = (pad.cu if copper and pad.cu else "both" if pad.kind == "tht" else BOTTOM if bottom else TOP)
            out.append(BoardPad(part.ref, pad.number, side, float(x0 + x1) / 2, float(y0 + y1) / 2,
                                float(x1 - x0), float(y1 - y0), pad.kind, copper, pad.net))
    return out


_ORIGIN_RX = {"aux": re.compile(r"\(aux_axis_origin\s+([-+\d.eE]+)\s+([-+\d.eE]+)\s*\)"),
              "grid": re.compile(r"\(grid_origin\s+([-+\d.eE]+)\s+([-+\d.eE]+)\s*\)")}


def board_origins(board: Board) -> dict[str, tuple[float, float]]:
    """The board's named origins: its CPL origin under its kind, "absolute" (0, 0), and "aux" and "grid" as its
    .kicad_pcb file sets them ((0, 0) when the file has none, as KiCad omits them there). board.py resolves only the
    origin it is asked for."""
    out = {board.origin_kind or "aux": tuple(board.origin), "absolute": (0.0, 0.0)}
    p = Path(board.path or "")
    if p.suffix == ".kicad_pcb" and p.is_file():
        text = p.read_text(encoding="utf-8", errors="replace")
        for k, rx in _ORIGIN_RX.items():
            m = rx.search(text)
            out.setdefault(k, (float(m.group(1)), float(m.group(2))) if m else (0.0, 0.0))
    return out


# ------------------------------------------------------------------------------------------------------- matching

class _Grid:
    """Nearest-neighbour lookup within one cell (the tolerance) of a fixed point set."""

    def __init__(self, pts: np.ndarray, cell: float):
        self.pts, self.cell = np.asarray(pts, float).reshape(-1, 2), cell
        k = self._keys(self.pts)
        self.order = np.argsort(k, kind="stable")
        self.sorted = k[self.order]

    def _keys(self, p):
        i = np.floor(p / self.cell).astype(np.int64) + (1 << 30)
        return i[:, 0] * (1 << 31) + i[:, 1]

    def nearest(self, q: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(index or -1, distance) of the nearest point no further than one cell, for each query point."""
        q = np.asarray(q, float).reshape(-1, 2)
        best = np.full(len(q), np.inf)
        idx = np.full(len(q), -1, dtype=np.int64)
        if not len(self.pts) or not len(q):
            return idx, best
        i = np.floor(q / self.cell).astype(np.int64) + (1 << 30)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                k = (i[:, 0] + dx) * (1 << 31) + (i[:, 1] + dy)
                lo, hi = np.searchsorted(self.sorted, k, "left"), np.searchsorted(self.sorted, k, "right")
                for step in range(int((hi - lo).max())):  # every point of the cell (copper and paste coincide)
                    hit = lo + step < hi
                    j = self.order[np.minimum(lo + step, len(self.order) - 1)]
                    d = np.where(hit, np.hypot(*(self.pts[j] - q).T), np.inf)
                    better = d < best
                    best[better], idx[better] = d[better], j[better]
        far = best > self.cell
        idx[far], best[far] = -1, np.inf
        return idx, best


@dataclass
class _Side:
    B: np.ndarray                  # board pad centres (board frame)
    G: np.ndarray                  # Gerber pad centres
    idx: np.ndarray                # index of each board pad in the board_pads list (a plated pad is on both sides)
    grids: dict = field(default_factory=dict)

    def grid(self, tol: float) -> _Grid:
        if tol not in self.grids:
            self.grids[tol] = _Grid(self.G, tol)
        return self.grids[tol]


def _map(B: np.ndarray, flip: str, s: float) -> np.ndarray:
    fx, fy = FLIPS[flip]
    return B * (s * fx, s * fy)


def _match(sides: list[_Side], flip: str, s: float, t, tol: float):
    """(s F b, g, board pad index) of the pads within tol of a Gerber pad of their side under g = s F b + t."""
    us, gs, ids = [], [], []
    for sd in sides:
        if not len(sd.B) or not len(sd.G):
            continue
        u = _map(sd.B, flip, s) + t
        j, d = sd.grid(tol).nearest(u)
        ok = j >= 0
        us.append(u[ok] - t)
        gs.append(sd.G[j[ok]])
        ids.append(sd.idx[ok])
    if not us:
        return np.zeros((0, 2)), np.zeros((0, 2)), np.zeros(0, dtype=np.int64)
    return np.vstack(us), np.vstack(gs), np.concatenate(ids)


def _refine(sides, flip, s, t, tol):
    """(t, board pads matched, rms): the median translation over the pads within 10 tol of t, then over those
    within tol of that. A plated pad matched on both sides counts once."""
    U, G, _ = _match(sides, flip, s, t, tol * 10)
    if len(U):
        t = np.median(G - U, axis=0)
    U, G, ids = _match(sides, flip, s, t, tol)
    if len(U):
        t = np.median(G - U, axis=0)
        U, G, ids = _match(sides, flip, s, t, tol)
    rms = float(np.sqrt(((G - U - t) ** 2).sum(1).mean())) if len(U) else 0.0
    return np.asarray(t, float), len(np.unique(ids)), rms


def _vote(sides: list[_Side], flip: str, s: float, tol: float, anchors: int = 48, cell: float = 0.1, extra=()):
    """Candidate translations t for g = s F b + t, best first (the most board pads matched, then the least rms):
    every pair of an anchor board pad and a Gerber pad of its side votes for t = g - s F b, once per anchor and cell
    of size `cell` (gerber units, as tol). A cell scores the votes of its 3 x 3 neighbourhood: a round origin puts t
    on a cell boundary, where rounding noise splits its votes over up to four cells, and on a periodic layout a t
    one pitch off, which misses only the edge pads, would otherwise collect more in one cell. The 8 best cells two
    or more cells apart, and the `extra` translations (the board's named origins), are refined by matching every
    pad."""
    keys, diffs = [], []
    for si, sd in enumerate(sides):
        if not len(sd.B) or not len(sd.G):
            continue
        B = np.unique(np.round(sd.B, 6), axis=0)
        order = np.lexsort((B[:, 1], B[:, 0]))
        pick = B[order][np.unique(np.linspace(0, len(B) - 1, min(anchors, len(B))).round().astype(int))]
        U = _map(pick, flip, s)
        D = sd.G[None, :, :] - U[:, None, :]                       # anchors x gerber pads x 2
        k = np.floor(D / cell).astype(np.int64) + (1 << 20)
        key = k[..., 0] * (1 << 21) + k[..., 1]
        aid = np.broadcast_to(np.arange(len(U))[:, None] + si * 4096, key.shape)
        keys.append(np.unique(key.ravel() * 8192 + aid.ravel()) // 8192)
        diffs.append(D.reshape(-1, 2))
    if not keys:
        return []
    uk, cnt = np.unique(np.concatenate(keys), return_counts=True)
    near = np.zeros(len(uk), dtype=np.int64)              # every cell's 3 x 3 neighbourhood sum
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            q = uk + dx * (1 << 21) + dy
            i = np.minimum(np.searchsorted(uk, q), len(uk) - 1)
            near += np.where(uk[i] == q, cnt[i], 0)
    picked: list[tuple[int, int]] = []
    for k in uk[np.argsort(-near, kind="stable")].tolist():
        kx, ky = k // (1 << 21) - (1 << 20), k % (1 << 21) - (1 << 20)
        if all(max(abs(kx - px), abs(ky - py)) > 2 for px, py in picked):   # one candidate per peak
            picked.append((kx, ky))
            if len(picked) == 8:
                break
    out, seen = [], []
    for t0 in [((kx + 0.5) * cell, (ky + 0.5) * cell) for kx, ky in picked] + [tuple(t) for t in extra]:
        t, m, rms = _refine(sides, flip, s, np.asarray(t0, float), tol)
        if any(np.hypot(*(t - u)) < tol for u in seen):
            continue
        seen.append(t)
        out.append((m, rms, t))
    out.sort(key=lambda v: (-v[0], v[1]))
    return out


def _pin_pairs(bpads: list[BoardPad], gs: GerberSet):
    """(board points, gerber points) of the (ref, pin, side) groups both have equally often, their centroids (pads
    that share a number, such as a QFN's GND pads, are paired as a group)."""
    bg: dict = {}
    for p in bpads:
        for side in ((TOP, BOTTOM) if p.side == "both" else (p.side,)):
            bg.setdefault((p.ref, p.number, side), []).append((p.x, p.y))
    gg: dict = {}
    for la in gs.find("copper"):
        if la.side not in (TOP, BOTTOM):
            continue
        for p in la.flashes + la.regions:
            if p.ref and p.pin and p.dark:
                gg.setdefault((p.ref, p.pin, la.side), []).append((p.x, p.y))
    B, G = [], []
    for k, b in bg.items():
        g = gg.get(k)
        if g and len(g) == len(b):
            B.append(np.mean(b, axis=0))
            G.append(np.mean(g, axis=0))
    return np.asarray(B, float).reshape(-1, 2), np.asarray(G, float).reshape(-1, 2)


# ------------------------------------------------------------------------------------------------------- origin check

@dataclass
class OriginCheck:
    """Where a Gerber set's (0, 0) lies on the board, how it was found, and whether the set is this board's."""
    origin: tuple[float, float] | None = None   # board point that is Gerber (0, 0); None when nothing matched
    method: str = ""               # x2-pins (TO.P reference and pin pairs) | pad-voting (pad positions) | ""
    flip: str = "none"             # g = s . F . b + t: none F = diag(1, -1) (KiCad's), y (y not inverted),
                                   # x (x mirrored), xy (both: the board turned half a turn)
    scale: float = 1.0             # s: 25.4 or 1/25.4 when the set's unit is wrong
    residual: float = 0.0          # rms distance of the matched pads from the fitted map, mm
    matched: int = 0               # board pads with a Gerber copper or paste pad at their place
    board_pads: int = 0            # board copper pads compared
    gerber_pads: int = 0           # Gerber copper and paste pads compared
    pin_pairs: int = 0             # (ref, pin) groups the x2-pins fit used
    cpl_origin: tuple[float, float] | None = None
    cpl_origin_kind: str = ""
    named: str = ""                # the board origin the Gerbers were plotted from: aux | grid | absolute | user | ""
    verdict: str = "unknown"       # matches | does-not-match | unknown
    shift: tuple[float, float] | None = None   # cpl frame: add to every row's Mid X, Mid Y to put it on the
                                               # Gerbers' origin
    compared: int = 0              # board pads the coverage counts (SMD, plated, connect: on a side with layers)
    coverage: float = 0.0          # fraction of them with a Gerber pad at their place on every side compared
    missing: list[str] = field(default_factory=list)          # "R5:1", board pads with none
    extra: list[tuple[float, float]] = field(default_factory=list)   # Gerber paste pads (board frame) on no board pad
    mismatched: list[str] = field(default_factory=list)       # Gerber pads whose TO.P / TO.N name another pad or net
    extra_origin: tuple[float, float] | None = None   # board point whole paste layers of extra pads were plotted from
    named_extra: str = ""          # the board origin that is
    extra_layers: list[str] = field(default_factory=list)     # those paste layers
    belongs: bool = False          # coverage >= BELONGS: the Gerbers are this board's
    stale: bool = False            # this board's, but pads moved, came or went (or were renamed) since plotted
    problems: list[tuple[str, str, str]] = field(default_factory=list)   # (verdict, side or "", message): the set
                                   # lacks a side's layers, holds two plots, or its outline is from another origin
    messages: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.verdict == "matches" and self.belongs and not self.stale


_NAMES = {"aux": "the drill/place file origin", "absolute": "the page origin", "grid": "the grid origin",
          "user": "the origin given"}


def gerber_to_board(g, origin, flip: str = "none", scale: float = 1.0) -> tuple[float, float]:
    """Board point of gerber point g under the check's map (with origin b0: g = s F (b - b0))."""
    fx, fy = FLIPS[flip]
    return origin[0] + g[0] / (scale * fx), origin[1] + g[1] / (scale * fy)


def board_to_gerber(b, origin, flip: str = "none", scale: float = 1.0) -> tuple[float, float]:
    """g = s F (b - b0); with flip "none" and scale 1 this is convention.board_to_cpl(b, b0), KiCad's plot map."""
    fx, fy = FLIPS[flip]
    return scale * fx * (b[0] - origin[0]), scale * fy * (b[1] - origin[1])


def gerber_origin(board: Board, gerbers, cpl_origin=None, *, cpl_origin_kind: str | None = None,
                  tol: float = MATCH_TOL, edge=None) -> OriginCheck:
    """Find the board point that is the Gerbers' (0, 0), compare it with the CPL origin (default board.origin), and
    check that the Gerbers are this board's as it is (_coverage: the fraction of its pads with a Gerber pad at their
    place; pads that are missing, paste pads on no board pad, or pin attributes that name another pad, make the set
    stale) and whole (`problems`: a side without its layers, two plots in one set, the outline plotted from another
    origin than the copper: compared with `edge`, the board's Edge.Cuts as board-frame polylines, when given).

    The map g = s F b + t is fitted from (reference, pin) pairs when the copper layers carry TO.P attributes and at
    least half the pairs agree on it (method x2-pins), else by translation voting between the board's pads and the
    Gerbers' pads (pad-voting), which survives missing pads, fiducials, vias and other copper. Every F and s are
    tried; the sound one (F = diag(1, -1), s = 1) wins a tie. `gerbers` is a GerberSet or a path read_gerbers takes;
    cpl_origin_kind names the CPL origin in messages (default board.origin_kind, "user" for an explicit one)."""
    gs = gerbers if isinstance(gerbers, GerberSet) else read_gerbers(gerbers)
    res = OriginCheck()
    c = tuple(board.origin) if cpl_origin is None else (float(cpl_origin[0]), float(cpl_origin[1]))
    res.cpl_origin = c
    res.cpl_origin_kind = cpl_origin_kind or (board.origin_kind if cpl_origin is None else "user")
    bp = board_pads(board)
    res.board_pads = len(bp)
    sides = []
    for side in (TOP, BOTTOM):
        idx = np.array([i for i, p in enumerate(bp) if p.side in (side, "both")], dtype=np.int64)
        B = np.array([(bp[i].x, bp[i].y) for i in idx], float).reshape(-1, 2)
        G = np.array([(p.x, p.y) for p in gs.pads(side)], float).reshape(-1, 2)
        sides.append(_Side(B, G, idx))
    res.gerber_pads = sum(len(sd.G) for sd in sides)
    if not gs.layers:
        dirs = [n for n, why in gs.skipped if why.startswith("a directory of Gerber files")]
        res.messages.append("no Gerber files were read" + (f": the Gerbers are in {', '.join(dirs)} (pass it as the "
                                                           "path)" if dirs else ""))
        return res
    if not res.gerber_pads:
        res.messages.append("the Gerbers have no copper or paste pads to compare (" + _layers_said(gs) + ")")
        res.problems = _duplicates(gs)             # the message says what is missing
        return res
    res.problems = _duplicates(gs) + _sides(board, gs)
    if not res.board_pads:
        res.messages.append("the board has no copper pads to compare with the Gerbers")
        return res
    least = min(MIN_MATCHED, res.board_pads)
    need = max(least, math.ceil(MIN_FRACTION * res.board_pads))

    best = None                    # (board pads matched, rms, flip, s, t, method, pin pairs that agree)
    scaled = ""                    # the pin attributes fit only at another scale: says which
    if gs.x2_pins:
        PB, PG = _pin_pairs(bp, gs)
        if len(PB) >= least:
            fits = []
            for flip in FLIPS:
                for s in SCALES:
                    d = PG - _map(PB, flip, s)
                    t = np.median(d, axis=0)
                    n = int((np.hypot(*(d - t).T) <= tol * s).sum())
                    fits.append((n, flip == "none" and s == 1.0, flip, s, t))
            fits.sort(key=lambda f: (-f[0], not f[1]))
            n, _, flip, s, t = fits[0]
            if n >= max(least, len(PB) / 2):
                t, m, rms = _refine(sides, flip, s, t, tol * s)
                best = (m, rms, flip, s, t, "x2-pins", n)
            else:
                res.messages.append(f"the Gerbers' pin attributes do not fit the board ({n} of {len(PB)} pins agree):"
                                    " matched by pad positions instead")
                scaled = _similar(PB, PG, tol)
    named = board_origins(board)
    named.setdefault(res.cpl_origin_kind, c)
    if best is None:
        found = []
        for s in SCALES:
            for flip in FLIPS:
                # the board's own origins are candidates too: an origin is usually one of them
                cands = _vote(sides, flip, s, tol * s, cell=0.1 * s,
                              extra=[-_map(np.asarray(o, float), flip, s) for o in named.values()])
                if cands:
                    m, rms, t = cands[0]
                    found.append((m, rms, flip, s, t))
                if flip == "none" and s == 1.0 and cands and cands[0][0] >= 0.9 * res.board_pads:
                    break                                       # a sound set: the other maps cannot beat it
            if found and max(f[0] for f in found) >= 0.9 * res.board_pads:
                break
        if found:
            found.sort(key=lambda f: (-f[0], not (f[2] == "none" and f[3] == 1.0), f[1]))
            m, rms, flip, s, t = found[0]
            best = (m, rms, flip, s, t, "pad-voting", 0)
    if best is None or best[0] < need:
        n = best[0] if best else 0
        res.matched = n
        res.messages.append(f"the Gerbers' pads do not match the board's ({n} of {res.board_pads} pads at most): "
                            + (scaled or "are these the board's Gerbers?") + _unnamed_note(gs))
        res.messages += _coverage(res, board, gs, None, "none", 1.0, tol)
        return res
    m, rms, flip, s, t, method, pairs = best
    fx, fy = FLIPS[flip]
    o = (round(float(-t[0] / (s * fx)), 9) + 0.0, round(float(-t[1] / (s * fy)), 9) + 0.0)   # no -0.0
    res.origin, res.method, res.flip, res.scale = o, method, flip, s
    res.residual, res.matched, res.pin_pairs = rms / s, m, pairs
    for k in [res.cpl_origin_kind, "aux", "absolute", "grid"]:
        if k in named and math.hypot(o[0] - named[k][0], o[1] - named[k][1]) <= ORIGIN_TOL:
            res.named = k
            break
    notes = _coverage(res, board, gs, o, flip, s, tol)
    if res.extra_origin is not None:
        res.named_extra = next((k for k in ["aux", "absolute", "grid", res.cpl_origin_kind] if k in named and
                                math.dist(res.extra_origin, named[k]) <= ORIGIN_TOL), "")
    res.problems += _outline(gs, o, flip, s, edge, board)
    _verdict(res)
    res.messages += notes
    return res


def _coverage(res: OriginCheck, board: Board, gs: GerberSet, o, flip: str, s: float, tol: float) -> list[str]:
    """The board's pads on each side the set has layers for, against that side's Gerber pads: SMD pads against its
    copper and paste, plated and connect pads (no paste) against its copper; a plated pad is compared on both sides,
    and counted once. Paste pads on no board pad, a whole paste layer of them plotted from another origin
    (`extra_origin`). With pin attributes (TO.P, TO.N), copper pads that name another board pad or net than the one
    at their place (`mismatched`: parts swapped or nets changed since the plot). A side with no layer is a note (and
    one of `problems`), not stale."""
    allp = board_pads(board, apertures=True)
    seen: dict[int, bool] = {}                        # board pad index -> a Gerber pad on every side compared
    notes, todo = [], []
    for side in (TOP, BOTTOM):
        cu, paste = bool(gs.find("copper", side)), bool(gs.find("paste", side))
        mine = [i for i, p in enumerate(allp) if p.copper and p.side in (side, "both")
                and (cu or paste and p.kind == "smd")]
        if not (cu or paste):
            n = sum(1 for p in allp if p.copper and p.side == side and p.kind == "smd")
            if n:
                notes.append(f"the Gerbers have no {side} copper or paste layer: the {n} SMD pads of the {side} side "
                             "were not checked")
        todo.append((side, mine))
        seen.update((i, True) for i in mine)
    res.compared = len(seen)
    if o is None:
        res.missing = [f"{allp[i].ref}:{allp[i].number}" for i in sorted(seen)]
        return notes
    tol_s = tol * s                                   # gerber units
    for side, mine in todo:
        G = np.array([(p.x, p.y) for p in gs.pads(side)], float).reshape(-1, 2)
        if mine:
            U = np.array([board_to_gerber((allp[i].x, allp[i].y), o, flip, s) for i in mine])
            j, _ = _Grid(G, tol_s).nearest(U)
            for i, k in zip(mine, j):
                seen[i] = seen[i] and k >= 0
        here = [p for p in allp if p.side in (side, "both")]
        B = np.array([board_to_gerber((p.x, p.y), o, flip, s) for p in here], float).reshape(-1, 2)
        grid = _Grid(B, tol_s)
        for la in gs.find("paste", side):
            paste = la.pads()
            if not paste:
                continue
            j, _ = grid.nearest(np.array([(p.x, p.y) for p in paste]))
            off = [gerber_to_board((p.x, p.y), o, flip, s) for p, k in zip(paste, j) if k < 0]
            res.extra += off
            if len(off) >= max(MIN_MATCHED, 0.8 * len(paste)):
                d = _shift_of(np.asarray(off), np.array([(p.x, p.y) for p in here if p.kind == "smd"], float), tol)
                if d is not None:                     # the layer is a shifted copy of the board's pads
                    if res.extra_origin is None:
                        res.extra_origin = (round(o[0] + d[0], 6) + 0.0, round(o[1] + d[1], 6) + 0.0)
                    res.extra_layers.append(Path(la.name).name)
        if gs.x2_pins:
            cu = [i for i, p in enumerate(here) if p.copper]
            res.mismatched += _renamed([here[i] for i in cu], B[cu], gs.find("copper", side), tol_s)
    res.missing = [f"{allp[i].ref}:{allp[i].number}" for i in sorted(seen) if not seen[i]]
    res.coverage = 1.0 - len(res.missing) / len(seen) if seen else 0.0
    res.belongs = bool(seen) and res.coverage >= BELONGS
    res.stale = res.belongs and bool(res.missing or res.extra or res.mismatched)
    return notes


def _shift_of(E: np.ndarray, B: np.ndarray, tol: float):
    """The one vector d (board frame) that puts at least 80 % of the points E on points B (within tol), or None:
    pairs of up to 24 of E and every B vote for d by cells of 0.1 mm."""
    if not len(E) or not len(B):
        return None
    pick = E[np.unique(np.linspace(0, len(E) - 1, min(24, len(E))).round().astype(int))]
    D = (B[None, :, :] - pick[:, None, :]).reshape(-1, 2)
    _, inv, counts = np.unique(np.floor(D / 0.1).astype(np.int64), axis=0, return_inverse=True, return_counts=True)
    grid, best = _Grid(B, tol), None
    for c in np.argsort(-counts, kind="stable")[:8]:
        d = np.median(D[inv.ravel() == c], axis=0)
        n = int((grid.nearest(E + d)[0] >= 0).sum())
        if best is None or n > best[0]:
            best = (n, d)
    return best[1] if best is not None and best[0] >= 0.8 * len(E) else None


def _renamed(here: list[BoardPad], B: np.ndarray, layers: list[GerberLayer], tol: float) -> list[str]:
    """Copper pads whose TO.P names another board pad than any at their place, or whose TO.N another net: the parts
    were swapped, or the nets changed, after the plot (positions alone cannot tell). `B`: the gerber points of
    `here`."""
    out = []
    grid = _Grid(B, tol)
    at: dict[tuple[str, str], list[int]] = {}
    for i, p in enumerate(here):
        at.setdefault((p.ref, p.number), []).append(i)
    for la in layers:
        for g in la.flashes + la.regions:
            if not (g.dark and g.ref and g.pin):
                continue
            same = [here[i] for i in at.get((g.ref, g.pin), []) if math.hypot(*(B[i] - (g.x, g.y))) <= tol]
            if not same:
                j = int(grid.nearest(np.array([(g.x, g.y)]))[0][0])
                if j >= 0:
                    out.append(f"{here[j].ref}:{here[j].number} is where the Gerbers have {g.ref}:{g.pin}")
            elif g.net and g.net != "N/C" and all(p.net and p.net != g.net for p in same):
                out.append(f"{g.ref}:{g.pin} is on net {g.net} in the Gerbers, {same[0].net} on the board")
    return list(dict.fromkeys(out))


def _duplicates(gs: GerberSet) -> list[tuple[str, str, str]]:
    """More than one outer copper or paste layer of a side (or profile, or job file, as their attributes say): two
    plots in one set (KiCad never deletes an old plot), and JLC may make the board from either."""
    groups: dict[str, list[str]] = {f"{side} {kind}": [] for side in (TOP, BOTTOM) for kind in ("copper", "paste")}
    for la in gs.layers:
        if la.kind in ("copper", "paste") and la.side in (TOP, BOTTOM):
            groups.setdefault(f"{la.side} {la.kind}", []).append(la.name)
        elif la.kind == "profile" and la.source != "name":     # Altium's .gko and .gm1 are both named "profile"
            groups.setdefault("profile", []).append(la.name)
    groups["job"] = [n for n, why in gs.skipped if why == "Gerber job file"]
    dup = [(k, sorted(v)) for k, v in groups.items() if len(v) > 1]
    if not dup:
        return []
    projects = sorted({la.attributes["ProjectId"].split(",")[0] for la in gs.layers if la.attributes.get("ProjectId")})
    said = "; ".join(f"{len(v)} {k} {'files' if k == 'job' else 'layers'}: {', '.join(v)}" for k, v in dup)
    return [(RED, "", f"the Gerbers hold more than one plot ({said}"
             + (f"; projects {', '.join(projects)}" if len(projects) > 1 else "")
             + "): JLC may make the board from either; send one plot")]


def _read(gs: GerberSet, n: int = 12) -> str:
    names = [la.name for la in gs.layers]
    return ", ".join(names[:n]) + (f" and {len(names) - n} more" if len(names) > n else "")


def _sides(board: Board, gs: GerberSet) -> list[tuple[str, str, str]]:
    """A side the board has SMD pads on and the set has no copper layer for (red: JLC could not make it; also when it
    has no paste layer either), or copper but no paste (yellow: its SMD pads would get no paste)."""
    out = []
    pads = board_pads(board)
    for side in (TOP, BOTTOM):
        smd = sum(1 for p in pads if p.copper and p.side == side and p.kind == "smd")
        if not smd:
            continue
        cu, paste = gs.find("copper", side), gs.find("paste", side)
        if not cu:
            out.append((RED, side, f"the Gerbers have no {side} copper{'' if paste else ' or paste'} layer: the board's "
                                   f"{_count(smd, f'{side} SMD pad')} {'is' if smd == 1 else 'are'} on no layer of "
                                   f"the set (read: {_read(gs)})"))
        elif not paste:
            out.append((YELLOW, side, f"the Gerbers have no {side} paste layer: the board's "
                                      f"{_count(smd, f'{side} SMD pad')} would get no solder paste"))
    return out


def _count(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _outline(gs: GerberSet, o, flip: str, s: float, edge, board: Board) -> list[tuple[str, str, str]]:
    """The profile layer through the copper's map against the board's Edge.Cuts (`edge`, board-frame polylines):
    moved by one vector (plotted from another origin) or another outline. Without `edge` (a snapshot), the board's
    pads must lie inside it."""
    prof = gs.outline
    box = prof.bbox() if prof is not None else None
    if box is None:
        return []
    corners = np.array([gerber_to_board(q, o, flip, s) for q in ((box[0], box[1]), (box[2], box[3]))])
    lo, hi = corners.min(0), corners.max(0)
    name = Path(prof.name).name
    if edge:
        pts = np.array([q for ln in edge for q in ln], float).reshape(-1, 2)
        elo, ehi = pts.min(0), pts.max(0)
        if np.abs(lo - elo).max() <= OUTLINE_TOL and np.abs(hi - ehi).max() <= OUTLINE_TOL:
            return []
        if np.abs((hi - lo) - (ehi - elo)).max() > OUTLINE_TOL:
            return [(RED, "", f"the board outline ({name}) is not the board's Edge.Cuts: {hi[0] - lo[0]:g} x "
                              f"{hi[1] - lo[1]:g} mm against {ehi[0] - elo[0]:g} x {ehi[1] - elo[1]:g} mm")]
        d = lo - elo                                   # board frame: where the profile lies, less where it belongs
        at = (round(o[0] - d[0], 4) + 0.0, round(o[1] - d[1], 4) + 0.0)
        named = board_origins(board)
        kind = next((k for k in ("aux", "absolute", "grid") if k in named and math.dist(at, named[k]) <= ORIGIN_TOL), "")
        dc = board_to_gerber((o[0] + d[0], o[1] + d[1]), o)
        return [(RED, "", f"the board outline ({name}) was plotted from another origin than the copper, board "
                          f"({at[0]:g}, {at[1]:g})" + (f" ({_NAMES[kind]})" if kind else "")
                          + f": it lies ({dc[0]:+.3f}, {dc[1]:+.3f}) mm off the board's Edge.Cuts; plot every layer "
                          "from one origin")]
    bp = board_pads(board)
    if not bp:
        return []
    inside = sum(1 for p in bp if lo[0] - 1 <= p.x <= hi[0] + 1 and lo[1] - 1 <= p.y <= hi[1] + 1) / len(bp)
    if inside >= 0.5:
        return []
    return [(RED, "", f"the board outline ({name}) lies away from the copper: {inside:.0%} of the board's pads are "
                      "inside it; plot every layer from one origin")]


def _similar(PB: np.ndarray, PG: np.ndarray, tol: float) -> str:
    """The pin pairs fitted with any scale (g = k F b + t): when most agree at a k that is not 1 or 25.4, say it (a
    coordinate format, FS, that does not match the data); else ""."""
    best = None
    for flip in FLIPS:
        U = _map(PB, flip, 1.0)
        du, dg = U - U.mean(0), PG - PG.mean(0)
        den = float((du ** 2).sum())
        if den <= 0:
            continue
        k = float((du * dg).sum()) / den
        if k <= 0:
            continue
        t = np.median(PG - k * U, axis=0)
        n = int((np.hypot(*(PG - k * U - t).T) <= tol * max(1.0, k)).sum())
        if best is None or n > best[0]:
            best = (n, k)
    if best is None or best[0] < max(MIN_MATCHED, len(PB) / 2) or any(abs(best[1] / v - 1) < 1e-3 for v in SCALES):
        return ""
    k = best[1]
    return (f"the pin attributes fit the board scaled by {k:.4g} ({best[0]} of {len(PB)} pins): a coordinate format "
            "(FS) that does not match the data?")


def _layers_said(gs: GerberSet) -> str:
    """Which files were taken for which layer, and the Gerber files nothing names that hold flashes (a copper layer
    renamed, as KiCad's French demos do: 'Dessus'), for a set whose pads cannot be compared."""
    said = [f"{side} {kind}: " + (", ".join(f"{Path(la.name).name} ({len(la.pads())} pads)"
                                           for la in gs.find(kind, side)) or "none")
            for kind in ("copper", "paste") for side in (TOP, BOTTOM)]
    return "; ".join(said) + _unnamed_note(gs, "; ")


def _unnamed_note(gs: GerberSet, sep: str = " (") -> str:
    files = [f"{Path(la.name).name} ({n} flashes)" for la in sorted(gs.layers, key=lambda la: la.name)
             if la.kind == "other" and not la.function for n in [sum(1 for p in la.flashes if p.dark)] if n]
    if not files:
        return ""
    return (f"{sep}not named as a layer, with pads: " + ", ".join(files) + ": name copper and paste files as KiCad "
            "does (-F_Cu, -B_Paste) or plot them with X2 attributes" + (")" if sep == " (" else ""))


def _fmt(p) -> str:
    return f"({round(p[0], 4) + 0.0:.4f}, {round(p[1], 4) + 0.0:.4f})"


def _verdict(res: OriginCheck) -> None:
    o, c = res.origin, res.cpl_origin
    how = (f"{res.method}, {res.matched} of {res.board_pads} board pads on Gerber pads, residual "
           f"{res.residual:.4f} mm")
    if res.flip != "none":
        res.verdict = "does-not-match"
        res.messages.append({"y": "the Gerbers are mirrored: their y axis is not inverted (KiCad plots board y down "
                                  "as Gerber y up), so JLC sees the board mirrored top to bottom",
                             "x": "the Gerbers are mirrored left to right (a set plotted as seen from below?)",
                             "xy": "the Gerbers are turned half a turn against the board"}[res.flip] + f" ({how})")
    if abs(res.scale - 1.0) > 1e-9:
        res.verdict = "does-not-match"
        res.messages.append("the Gerbers are 25.4 times too large: millimetre coordinates in a file that says inches"
                            if res.scale > 1 else
                            "the Gerbers are 25.4 times too small: inch coordinates in a file that says millimetres")
    named = f" ({_NAMES[res.named]})" if res.named in _NAMES else ""
    cname = f" ({_NAMES[res.cpl_origin_kind]})" if res.cpl_origin_kind in _NAMES else ""
    d = math.hypot(o[0] - c[0], o[1] - c[1])
    res.shift = (round(c[0] - o[0], 6) + 0.0, round(o[1] - c[1], 6) + 0.0)
    if res.verdict == "does-not-match":
        pass
    elif d <= ORIGIN_TOL:
        res.verdict = "matches"
        res.messages.append(f"the Gerbers' (0, 0) is the CPL origin {_fmt(c)}{cname} ({how})")
    else:
        res.verdict = "does-not-match"
        fix = []
        if res.named == "absolute" and res.cpl_origin_kind == "aux":
            fix.append("plot the Gerbers and drill files with 'Use drill/place file origin' (kicad-cli pcb export "
                       "gerbers --use-drill-file-origin)")
        elif res.named == "aux" and res.cpl_origin_kind == "absolute":
            fix.append("write the CPL from the drill/place origin, as the Gerbers are (KiCad's position file: 'Use "
                       "drill/place file origin')")
        flag = res.named if res.named in ("aux", "absolute", "grid") else f"{o[0]:g},{o[1]:g}"
        fix.append(("or " if fix else "") + f"shift every CPL row by ({res.shift[0]:+.4f}, {res.shift[1]:+.4f}) mm "
                   f"(the CPL written with --origin {flag})")
        res.messages.append(f"origin mismatch: the Gerbers' (0, 0) is board {_fmt(o)}{named}, the CPL's origin is "
                            f"{_fmt(c)}{cname}: JLC would put every part {d:.2f} mm off; " + ", ".join(fix)
                            + f" ({how})")
    if not res.belongs:
        res.messages.append(f"the Gerbers do not seem to be this board's: only {res.coverage:.0%} of its "
                            f"{res.compared} pads have a Gerber pad at their place" if res.compared else
                            "the Gerbers have no layer to compare the board's pads with")
    elif res.stale:
        parts = []
        if res.missing:
            parts.append(f"{len(res.missing)} of {res.compared} board pads have no Gerber pad at their place ("
                         + ", ".join(res.missing[:8]) + (" ..." if len(res.missing) > 8 else "") + ")")
        if res.extra:
            parts.append(f"{len(res.extra)} Gerber paste pads lie on no board pad (first at board "
                         f"{_fmt(res.extra[0])})")
        if res.mismatched:
            parts.append(f"the Gerbers' pin attributes name other pads than the board's at their place "
                         f"({len(res.mismatched)} pads: " + "; ".join(res.mismatched[:4])
                         + (" ..." if len(res.mismatched) > 4 else "") + ")")
        if res.extra_origin is not None:
            x, y = res.extra_origin
            res.messages.append(f"the paste layers ({', '.join(sorted(res.extra_layers))}) were plotted from another "
                                f"origin than the copper, board ({x:g}, {y:g})"
                                + (f" ({_NAMES[res.named_extra]})" if res.named_extra in _NAMES else "")
                                + ": plot every layer from one origin: " + "; ".join(parts))
        elif any(m.startswith("the Gerbers hold more than one plot") for _, _, m in res.problems):
            res.messages.append("the Gerbers hold pads of another plot than the board's: " + "; ".join(parts))
        else:
            res.messages.append("the Gerbers are older than the board, re-plot them: " + "; ".join(parts))


def shifted_rows(rows: list[CplRow], check: OriginCheck) -> list[CplRow]:
    """The CPL rows moved onto the Gerbers' origin (check.shift added to Mid X, Mid Y): what JLC needs when the
    Gerbers stay as they are. ValueError when the check found no plain translation (a mirrored or scaled set)."""
    if check.shift is None or check.flip != "none" or abs(check.scale - 1.0) > 1e-9:
        raise ValueError("the Gerbers' origin is not a plain shift of the CPL's: no corrected CPL")
    dx, dy = check.shift
    return [CplRow(r.ref, round(r.x + dx, 4) + 0.0, round(r.y + dy, 4) + 0.0, r.layer, r.rotation, dict(r.extra))
            for r in rows]


__all__ = ["GerberError", "GPad", "GDraw", "GerberLayer", "GerberSet", "BoardPad", "OriginCheck", "parse_gerber",
           "read_gerbers", "read_gerber_text", "classify", "function_kind", "board_pads", "board_origins",
           "gerber_origin", "gerber_to_board", "board_to_gerber", "shifted_rows", "MATCH_TOL", "ORIGIN_TOL"]
