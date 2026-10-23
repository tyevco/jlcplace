"""Data model shared by every jlcplace module.

All lengths are millimetres and all angles degrees.

Frames
------
board    KiCad board coordinates: absolute, y down.
library  A footprint's own frame as drawn in its KiCad library (top view, y down). KiCad places a part with
             board = P + R(theta) . D . library,   D = diag(1, -1) on the bottom, the identity on the top
         (flipping to the bottom mirrors the footprint about its local x axis, then it is rotated).
ee       The EasyEDA footprint's frame: its origin, y down, EasyEDA's zero orientation. JLC places this footprint.
cpl      JLC's coordinates: relative to the CPL origin (the board's aux origin unless overridden), y UP,
         X not mirrored on the bottom.

R(a) is KiCad's rotation, counter-clockwise as seen on screen (y down):
    R(a) . (x, y) = (x cos a + y sin a,  -x sin a + y cos a)

A correction relates the EasyEDA footprint to the KiCad one, independent of the side the part is on:
    library = R(rot) . M . ee + (dx, dy),    M = diag(-1, 1) if mirror else the identity
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

TOP, BOTTOM = "top", "bottom"
GREEN, YELLOW, RED, VERIFIED = "green", "yellow", "red", "verified"
VERDICT_ORDER = {RED: 0, YELLOW: 1, GREEN: 2, VERIFIED: 3}
FORMULA_START = ("=", "+", "-", "@")   # a spreadsheet runs a cell that starts with one of these as a formula

_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\ud800-\udfff]")    # C0, DEL, C1 and lone surrogates


def printable(s) -> str:
    """str(s) with every control character shown as \\xNN. Text from boards, CPLs, BOMs, the database and EasyEDA
    goes to terminals and CI logs, where a newline starts a GitHub workflow command ('::stop-commands::...') and ESC
    a terminal escape."""
    s = str(s)
    if not _CONTROL.search(s):
        return s
    return _CONTROL.sub(lambda m: f"\\x{ord(m.group()):02x}" if ord(m.group()) < 0x100
                        else f"\\u{ord(m.group()):04x}", s)


def ref_problem(ref: str) -> str:
    """Why a reference cannot be a designator, or "". No KiCad designator holds a control character (it would put
    workflow commands in a CI log and make the JUnit XML unreadable) or starts with = + - @ (the CPL is a CSV: a
    spreadsheet would run it as a formula)."""
    if _CONTROL.search(ref):
        return "it holds a control character"
    if ref.startswith(FORMULA_START):
        return f"it starts with {ref[0]!r}, which makes a spreadsheet cell a formula"
    return ""


@dataclass
class Pad:
    """A KiCad pad, in its footprint's library frame."""
    number: str                    # "" for unnumbered pads (paste apertures, mechanical)
    x: float                       # centre, library frame
    y: float
    w: float                       # size along the pad's own x axis
    h: float                       # size along the pad's own y axis
    angle: float = 0.0             # pad rotation in the library frame (same sense as R)
    shape: str = "rect"            # rect | roundrect | oval | circle | trapezoid | chamfered | custom
    kind: str = "smd"              # smd | tht | npth | connect (copper with no paste/solder, e.g. edge contacts)
    copper: bool = True            # has copper the part is soldered to (outer copper on its side, or plated through)
    net: str = ""
    pin_name: str = ""             # KiCad pin function (the schematic pin name)
    pin_type: str = ""
    rr: float = 0.0                # roundrect corner radius ratio (0..0.5)
    poly: list[tuple[float, float]] | None = None  # exact outline in the library frame, if known (wins over shape)
    cu: str = ""                   # the board's outer copper the pad has, as placed: top | bottom | both; "" none (a
                                   # paste aperture, NPTH) or not known (an older snapshot): the Gerbers plot it there


@dataclass
class Part:
    """A placed footprint."""
    ref: str
    layer: str                     # TOP | BOTTOM
    x: float                       # footprint anchor, board frame
    y: float
    angle: float                   # footprint orientation, as KiCad's GetOrientationDegrees() reports it
    footprint: str = ""            # "Lib:Name"
    value: str = ""
    lcsc: str = ""                 # normalised LCSC code ("C12345") or ""
    lcsc_field: str = ""           # the footprint field the code came from
    fields: dict[str, str] = field(default_factory=dict)
    dnp: bool = False
    exclude_pos: bool = False
    exclude_bom: bool = False
    board_only: bool = False
    tht: bool = False              # footprint type is through-hole
    pads: list[Pad] = field(default_factory=list)

    @property
    def bottom(self) -> bool:
        return self.layer == BOTTOM

    @property
    def placed(self) -> bool:
        """JLC places it: not DNP, not excluded from the position file, not board-only."""
        return not (self.dnp or self.exclude_pos or self.board_only)

    def copper_pads(self) -> list[Pad]:
        return [p for p in self.pads if p.copper]


@dataclass
class Board:
    path: str
    origin: tuple[float, float]    # CPL origin, board frame
    parts: list[Part]
    source: str = ""               # "pcbnew" | "sexpr" | "json"
    kicad_version: str = ""
    origin_kind: str = "aux"       # aux | absolute | grid | user | gerber (the Gerbers' (0, 0): JLC reads rows from it)

    def part(self, ref: str) -> Part | None:
        return next((p for p in self.parts if p.ref == ref), None)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Board":
        parts = []
        for p in d["parts"]:
            p = dict(p)
            p["pads"] = [Pad(**{**q, "poly": [tuple(v) for v in q["poly"]] if q.get("poly") else None})
                         for q in p.get("pads", [])]
            parts.append(Part(**p))
        return cls(path=d["path"], origin=tuple(d["origin"]), parts=parts, source=d.get("source", "json"),
                   kicad_version=d.get("kicad_version", ""), origin_kind=d.get("origin_kind", "aux"))


@dataclass
class EEPad:
    """An EasyEDA footprint pad, in the ee frame."""
    number: str
    x: float
    y: float
    w: float
    h: float
    angle: float = 0.0             # same sense as R (the parser converts EasyEDA's own)
    shape: str = "rect"            # rect | oval | ellipse | polygon
    layer: str = TOP               # top | bottom | multi (through-hole)
    hole: float = 0.0              # drill diameter, 0 for SMD
    poly: list[tuple[float, float]] | None = None  # outline in the ee frame (polygon pads)


@dataclass
class EEFootprint:
    lcsc: str
    title: str                     # e.g. "SOT-23-6_L2.9-W1.6-P0.95-LS2.8-BR"
    uuid: str = ""                 # packageDetail uuid
    utime: int = 0                 # packageDetail update time (EasyEDA's own; not the record's updated_at)
    pads: list[EEPad] = field(default_factory=list)
    marks: list[tuple[float, float]] = field(default_factory=list)   # pin-1 marks (layer 101), ee frame: they sit
                                                     # by pad 1 whatever its function, so they say nothing of polarity
    pin_names: dict[str, str] = field(default_factory=dict)          # pad number -> symbol pin name
    package: str = ""
    tokens: list[str] = field(default_factory=list)  # orientation tokens in the title: TL, TR, BL, BR, RD, FD, CW, BI
    source: str = "easyeda-classic"
    tags: list[str] = field(default_factory=list)    # the record's part categories (result.tags), e.g. "TVS"
    plus: list[tuple[float, float, float]] = field(default_factory=list)  # "+" marks drawn on silkscreen/document:
                                                     # (x, y, bar length), ee frame

    def key(self) -> str:
        return f"{self.uuid}:{self.utime}"


@dataclass
class NotFound:
    lcsc: str
    message: str


@dataclass
class Correction:
    rot: float                     # alpha, degrees
    dx: float = 0.0                # the ee origin in the library frame
    dy: float = 0.0
    mirror: bool = False           # the EasyEDA footprint is a mirror image of the KiCad one: never applied silently
    method: str = ""               # number | geometry | manual | db
    rms: float = 0.0               # fit residual over matched pads, mm
    verdict: str = ""              # GREEN | YELLOW | RED | VERIFIED
    score: int = 0                 # 0..100, orders parts within a verdict
    reasons: list[str] = field(default_factory=list)
    alternatives: list[dict] = field(default_factory=list)  # other transforms under which every EE pad lands
    matched: int = 0               # pads matched by number
    ee_key: str = ""               # parts.ee_key of the EasyEDA footprint it was fitted on
    kicad_hash: str = ""           # parts.kicad_hash of the KiCad part it was fitted on (pads and pin functions)
    status: str = "auto"           # auto | manual | preview-verified | board-verified
    doubt: str = ""                # why the fit may have chosen the wrong one of several placements that land every
                                   # pad: "polarity" (pad numbers only, or names / + mark decided), "orientation" (the
                                   # pads do not fix the body's orientation), "" none
    polar: bool | None = None      # two-terminal part judged polar by the fit (parts.nonpolar); None: not judged


@dataclass
class CplRow:
    ref: str
    x: float                       # cpl frame
    y: float
    layer: str                     # TOP | BOTTOM
    rotation: float
    extra: dict[str, str] = field(default_factory=dict)  # other columns, kept verbatim


@dataclass
class PadLanding:
    ee_number: str
    kicad_number: str | None       # the copper pad its centre lands in; None = off copper
    dist: float                    # to the nearest KiCad copper pad centre, mm
    net: str = ""                  # net of the pad it lands in


@dataclass
class RowResult:
    ref: str
    lcsc: str = ""
    status: str = "ok"             # ok | offset | off-copper | wrong-pad | reversed | renumbered | pinout
                                   # | not-simulated | no-row | not-on-board | layer-mismatch | not-placed | duplicate
                                   # | duplicate-ref
    verdict: str = GREEN
    landings: list[PadLanding] = field(default_factory=list)
    uncovered: list[str] = field(default_factory=list)   # KiCad soldered pads no EE pad lands in
    messages: list[str] = field(default_factory=list)
    convention_sensitive: bool = False                   # an alternative bottom-side reading changes pin -> net
    row: CplRow | None = None
    expected: CplRow | None = None                       # the row jlcplace would write, when a correction is known
    # convention_sensitive: per other reading, the EasyEDA pads that move, (ee number, KiCad pad or None = off copper)
    reading_moves: dict[str, list[tuple[str, str | None]]] = field(default_factory=dict)
