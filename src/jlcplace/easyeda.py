"""Fetch, cache and parse EasyEDA (classic) component records: the footprints JLC places.

Record
------
`https://easyeda.com/api/products/{code}/components` returns `{"success": true, "result": {...}}` or, for an unknown
code, `{"success": false, "code": 404, "message": "Component not found"}` (HTTP 200 either way). The footprint is
`result.packageDetail.dataStr`; the symbol is `result.dataStr` (multi-unit symbols also carry `subparts`).

Units and frames
----------------
Footprint geometry is in absolute canvas units of 10 mil (1 unit = 0.254 mm), y down. The footprint origin, the
point JLC puts at a CPL row's Mid X / Mid Y, is `head.x, head.y`. The ee frame (jlcplace.model) is

    e = 0.254 * (p - head)                                   (mm, y down, EasyEDA's zero orientation)

Pads
----
`PAD~shape~x~y~w~h~layer~net~number~hole_r~points~rotation~id~hole_len~hole_points~plated~...`, layers 1 top,
2 bottom, 11 multi (through-hole). w and h are along the pad's own axes; `points` is EasyEDA's own outline of the
pad in canvas units with the rotation already applied (RECT: the four corners, POLYGON: the outline, OVAL: the end
points of its centre line). A pad whose drill is unplated (`plated` N) has no copper to solder: it is a hole, as is
`HOLE~x~y~radius~id~locked` (a connector's peg, a mounting hole), and both go to `EEFootprint.holes`.

The rotation field r turns the pad's axes in the same sense as model's R(a), counter-clockwise on screen (y down), so
`EEPad.angle = r`. Outlines cannot show the sense at multiples of 90 (a centred rectangle is symmetric), and every
cached pad is at a multiple of 90. The corner order can: a pad drawn at r = 0 lists the corners of its w x h rectangle
as TL, TR, BR, BL, and every RECT pad at r = 90 or 270 in the cached records lists exactly R(r) applied to that
sequence, starting at BL (r = 90) or TR (r = 270). The other sense, R(-r), would start at the opposite corner and
matches none of them (tests/test_easyeda.py). At r = 0 the order varies (editing rotates and mirrors the stored list
along with the pad), which is why only rotated pads count as evidence. easyeda2kicad and JLC2KiCad_lib copy r unchanged
into KiCad's pad orientation, which has R's sense. As a guard for the angles the records do not cover, a RECT or OVAL
pad at a non-right angle is checked against its own `points` and follows them if they show the other sense.
"""

from __future__ import annotations

import datetime
import email.utils
import http.client
import json
import logging
import math
import os
import random
import re
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import __version__
from .convention import norm_angle, rot
from .model import BOTTOM, TOP, EEFootprint, EEPad, NotFound

log = logging.getLogger(__name__)

API_URL = "https://easyeda.com/api/products/{code}/components?version=6.4.19.5"
USER_AGENT = f"jlcplace/{__version__} (+https://github.com/tyevco/jlcplace)"
EE_UNIT = 0.254                        # mm per EasyEDA canvas unit (10 mil)
BACKOFF = (3.0, 6.0, 12.0, 24.0, 48.0)  # s, after the 1st, 2nd, ... consecutive failure; the last repeats
ORIENTATION_TOKENS = ("TL", "TR", "BL", "BR", "RD", "FD", "CW", "BI")   # BI: bidirectional (a TVS)
MARK_LAYER = "101"                     # ComponentPolarityLayer: the pin-1 dot (by pad 1 whatever its function)
PLUS_LAYERS = ("3", "12")              # top silkscreen and document: where a footprint draws its "+"
PAD_LAYERS = {"1": TOP, "2": BOTTOM, "11": "multi"}
PAD_SHAPES = {"RECT": "rect", "OVAL": "oval", "ELLIPSE": "ellipse", "POLYGON": "polygon"}

_CODE = re.compile(r"C[1-9][0-9]*")       # as lcsc.normalize, pipeline.is_code and the database: no leading zero


class EEError(Exception):
    """The record could not be fetched or parsed (network failure, offline cache miss, malformed data)."""


def default_cache_dir() -> Path:
    """$JLCPLACE_CACHE, else $XDG_CACHE_HOME/jlcplace, else ~/.cache/jlcplace."""
    if os.environ.get("JLCPLACE_CACHE"):
        return Path(os.environ["JLCPLACE_CACHE"]).expanduser()
    if os.environ.get("XDG_CACHE_HOME"):
        return Path(os.environ["XDG_CACHE_HOME"]).expanduser() / "jlcplace"
    return Path.home() / ".cache" / "jlcplace"


def check_code(code: str) -> str:
    """'c2040 ' -> 'C2040'. Anything else is refused: the code becomes a file name and part of a URL."""
    c = str(code).strip().upper()
    if not _CODE.fullmatch(c):
        raise EEError(f"not an LCSC code: {code!r}")
    return c


# ------------------------------------------------------------------------------------------------------- parsing

def _mm(v: float) -> float:
    return round(v * EE_UNIT, 6) + 0.0


def _num(s, what: str) -> float:
    try:
        v = float(s)
    except (TypeError, ValueError):
        raise EEError(f"{what}: not a number: {s!r}") from None
    if not math.isfinite(v):
        raise EEError(f"{what}: not finite: {s!r}")
    return v


def _datastr(d, what: str) -> dict:
    """dataStr is a dict in current responses; older ones carried it as a JSON string."""
    if isinstance(d, str):
        try:
            d = json.loads(d)
        except ValueError:
            raise EEError(f"{what}: dataStr is not JSON") from None
    if not isinstance(d, dict):
        raise EEError(f"{what}: dataStr missing")
    return d


def _points(s: str, ox: float, oy: float, what: str) -> list[tuple[float, float]]:
    v = [_num(t, what) for t in s.replace(",", " ").split()]
    if len(v) % 2:
        raise EEError(f"{what}: odd number of coordinates")
    return [(_mm(v[i] - ox), _mm(v[i + 1] - oy)) for i in range(0, len(v), 2)]


def _outline_points(shape: str, x, y, w, h, a) -> list[tuple[float, float]]:
    """What EasyEDA lists in `points` for a pad at angle a in R's sense: a RECT's corners, an OVAL's centre-line
    ends (any order)."""
    if shape == "rect":
        us = [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]
    else:
        e = abs(w - h) / 2
        us = [(e, 0.0), (-e, 0.0)] if w >= h else [(0.0, e), (0.0, -e)]
    return [(x + r[0], y + r[1]) for r in (rot(u, a) for u in us)]


def _outline_sense(shape: str, angle: float, x, y, w, h, pts, what: str) -> float:
    """At an angle that is not a multiple of 90 the two rotation senses draw different pads, and EasyEDA's own
    outline (`points`) shows which one it means. Keep `angle` unless the outline matches only the other sense."""
    if len(pts) != (4 if shape == "rect" else 2):
        return angle

    def off(a):
        ref = _outline_points(shape, x, y, w, h, a)
        return max(min(math.dist(p, q) for q in ref) for p in pts)

    tol = 2e-3 + 1e-3 * max(w, h)
    if off(angle) > tol and off(-angle) <= tol:
        log.warning("%s: outline shows rotation %g in the other sense; using the outline", what, angle)
        return norm_angle(-angle)
    return angle


def _pad(fields: list[str], ox: float, oy: float, code: str) -> EEPad | None:
    what = f"{code}: PAD {'~'.join(fields[:9])[:60]}"
    if len(fields) < 9:
        raise EEError(f"{what}: too few fields")
    layer = PAD_LAYERS.get(fields[6].strip())
    if layer is None:                  # not a copper layer: nothing to solder
        return None
    f = fields + [""] * (16 - len(fields))
    shape = PAD_SHAPES.get(f[1].strip().upper(), f[1].strip().lower() or "rect")
    x, y = _mm(_num(f[2], what) - ox), _mm(_num(f[3], what) - oy)
    w, h = _mm(_num(f[4], what)), _mm(_num(f[5], what))
    angle = norm_angle(_num(f[11], what)) if f[11].strip() else 0.0
    hole = round(2.0 * _mm(_num(f[9], what)), 6) if f[9].strip() else 0.0
    if hole > 0 and f[15].strip().upper() == "N":
        return None                    # an unplated hole: no copper to solder
    poly = None
    if shape in ("rect", "oval") and angle % 90 and f[10].strip():
        angle = _outline_sense(shape, angle, x, y, w, h, _points(f[10], ox, oy, what), what)
    if shape == "polygon":
        poly = _points(f[10], ox, oy, what)
        if len(poly) < 3:
            raise EEError(f"{what}: polygon pad with fewer than 3 points")
        # the points are the final outline; w, h = its extent along the pad's own axes
        u = [rot((px - x, py - y), -angle) for px, py in poly]
        w = round(max(p[0] for p in u) - min(p[0] for p in u), 6)
        h = round(max(p[1] for p in u) - min(p[1] for p in u), 6)
    return EEPad(number=f[8].strip(), x=x, y=y, w=w, h=h, angle=angle, shape=shape, layer=layer, hole=hole,
                 poly=poly)


def _hole(fields: list[str], ox: float, oy: float, code: str) -> tuple[float, float, float] | None:
    """An unplated hole as (x, y, diameter), ee frame: `HOLE~x~y~radius~id~locked`, or a PAD whose drill is unplated
    (`plated` N). None for anything else, and for a malformed one: holes are only evidence (fit.py), a record without
    them is still placed."""
    f = fields + [""] * (16 - len(fields))
    if f[0] == "HOLE" and len(fields) >= 4:
        x, y, r = f[1], f[2], f[3]
    elif f[0] == "PAD" and f[9].strip() and f[15].strip().upper() == "N":
        x, y, r = f[2], f[3], f[9]
    else:
        return None
    try:
        d = round(2.0 * _mm(_num(r, code)), 6)
        return (_mm(_num(x, code) - ox), _mm(_num(y, code) - oy), d) if d > 0 else None
    except EEError:
        return None


def _units(symbol, what: str) -> list[dict]:
    """The symbol's dataStr plus every unit of a multi-part symbol. Units are found under `result.subparts` (as
    easyeda2kicad reads them) or `dataStr.subparts`, each either a dataStr or a record holding one."""
    out: list[dict] = []
    if symbol is None:
        return out

    def walk(d):
        if isinstance(d, str):
            d = _datastr(d, what)
        if not isinstance(d, dict):
            return
        if "dataStr" in d and "shape" not in d:
            walk(d["dataStr"])
            for sp in d.get("subparts") or []:
                walk(sp)
            return
        out.append(d)
        for sp in d.get("subparts") or []:
            walk(sp)

    walk(symbol)
    return out


def _pin(s: str) -> tuple[str, str] | None:
    """`P~show~etype~spice~x~y~rot~id~locked^^dot^^path^^name^^number^^...` -> (pad number, pin name). The number is
    the number label's text (segment 4, field 4), falling back to the spice pin number."""
    segs = s.split("^^")
    head = segs[0].split("~")
    name = segs[3].split("~") if len(segs) > 3 else []
    num = segs[4].split("~") if len(segs) > 4 else []
    number = (num[4] if len(num) > 4 else "").strip() or (head[3] if len(head) > 3 else "").strip()
    if not number:
        return None
    return number, (name[4] if len(name) > 4 else "").strip()


def pin_names(result: dict) -> dict[str, str]:
    """Pad number -> symbol pin name, over every unit of the symbol. The first non-empty name wins, the units' before
    the main symbol's: a multi-unit record's main symbol can repeat a unit with the '/' between a pin's functions
    lost ('PA0MCI0_CKTCLK3' for the unit's 'PA0/MCI0_CK/TCLK3', C111548)."""
    names: dict[str, str] = {}
    units = _units({"dataStr": result.get("dataStr"), "subparts": result.get("subparts")}, "symbol")
    for ds in units[1:] + units[:1]:
        for s in ds.get("shape") or []:
            if isinstance(s, str) and s.startswith("P~"):
                p = _pin(s)
                if p and (p[0] not in names or (p[1] and not names[p[0]])):
                    names[p[0]] = p[1]
    return names


def title_tokens(title: str) -> list[str]:
    """Orientation tokens EasyEDA puts in footprint titles ('SOT-23-3_..-LS2.8-BR' -> ['BR']), in order, once."""
    out: list[str] = []
    for t in re.split(r"[-_\s]+", title or ""):
        t = t.upper()
        if t in ORIENTATION_TOKENS and t not in out:
            out.append(t)
    return out


def parse_record(code: str, result: dict) -> EEFootprint:
    """`result` of an API response -> EEFootprint in the ee frame (mm, y down, relative to the footprint origin).
    Raises EEError when the record has no footprint or the footprint data is malformed."""
    code = check_code(code)
    if not isinstance(result, dict):
        raise EEError(f"{code}: no component record")
    pk = result.get("packageDetail")
    if not isinstance(pk, dict) or not pk:
        raise EEError(f"{code}: the EasyEDA record has no footprint (packageDetail missing)")
    ds = _datastr(pk.get("dataStr"), f"{code}: packageDetail")
    head = ds.get("head") or {}
    if "x" not in head or "y" not in head:
        raise EEError(f"{code}: footprint has no origin (head.x / head.y)")
    ox, oy = _num(head["x"], f"{code}: head.x"), _num(head["y"], f"{code}: head.y")
    pads, marks, holes = [], [], []
    shapes = [s for s in ds.get("shape") or [] if isinstance(s, str)]
    for s in shapes:
        f = s.split("~")
        if f[0] in ("PAD", "HOLE"):
            p = _pad(f, ox, oy, code) if f[0] == "PAD" else None
            h = _hole(f, ox, oy, code) if p is None else None
            if p is not None:
                pads.append(p)
            elif h is not None:
                holes.append(h)              # an unplated pad or hole: what a peg goes into
        elif f[0] == "CIRCLE" and len(f) > 5 and f[5].strip() == MARK_LAYER:
            what = f"{code}: CIRCLE"
            marks.append((_mm(_num(f[1], what) - ox), _mm(_num(f[2], what) - oy)))
    c_para = head.get("c_para") or {}
    package = str(c_para.get("package") or "")
    title = str(pk.get("title") or package)
    # packageDetail.updateTime moves on every change to the footprint record, head.utime only on editor saves (C2500:
    # 2026-08-24 vs 2025-04-23). The key must not miss a change; an extra one only costs a refit.
    try:
        utime = int(float(pk.get("updateTime") or head.get("utime") or 0))
    except (TypeError, ValueError):
        utime = 0
    tags = result.get("tags")
    tags = [str(t) for t in tags if isinstance(t, (str, int, float))] if isinstance(tags, list) else []
    return EEFootprint(lcsc=code, title=title, uuid=str(pk.get("uuid") or head.get("uuid") or ""), utime=utime,
                       pads=pads, marks=marks, pin_names=pin_names(result), package=package or title,
                       tokens=title_tokens(title), tags=tags, plus=plus_marks(shapes, ox, oy), holes=holes)


# ------------------------------------------------------------------------------------------------------- "+" marks

_PATH_TOKEN = re.compile(r"[A-Za-z]|[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?")
PLUS_ASPECT = 2.5                      # a filled bar is at least this many times longer than wide
PLUS_MID = 0.35                        # the bars cross within this fraction of each one's length from its middle
PLUS_RATIO = 2.5                       # neither bar is longer than this many times the other
PLUS_CLEAR = 0.25                      # no other stroke comes this close (fraction of the shorter bar)


def _path_points(path: str) -> list[tuple[float, float]] | None:
    """The points of an `M x y L x y ... Z` path, None when it has curves."""
    nums = []
    for t in _PATH_TOKEN.findall(path or ""):
        if t.upper() in ("M", "L", "Z"):
            continue
        if t.isalpha():
            return None
        nums.append(float(t))
    return list(zip(nums[0::2], nums[1::2]))


def _seg_dist(p, a, b) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = dx * dx + dy * dy
    t = 0.0 if n <= 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / n))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def plus_marks(shapes: list[str], ox: float = 0.0, oy: float = 0.0) -> list[tuple[float, float, float]]:
    """The "+" signs a footprint draws on its silkscreen or document layer (3, 12), as (x, y, length) in the ee frame:
    a horizontal and a vertical bar of one layer (two-point TRACKs, or thin filled rectangles), alike in length,
    crossing near both middles, with no other stroke of that layer near the crossing or the bars' ends (a diode's
    drawn symbol meets its bar at an end, an outline at a corner). EasyEDA draws one beside the positive pad of a
    polarised two-terminal part (a diode's anode, a capacitor's +); `parts.ee_positive_pad` reads which. Malformed
    shapes are skipped."""
    bars, edges = [], []
    for s in shapes:
        f = s.split("~")
        try:
            if f[0] == "TRACK" and len(f) > 4 and f[2].strip() in PLUS_LAYERS:
                v = [float(t) for t in f[4].split()]
                pts = [(_mm(v[i] - ox), _mm(v[i + 1] - oy)) for i in range(0, len(v) - 1, 2)]
                if len(pts) == 2:
                    bars.append((f[2].strip(), pts[0], pts[1]))
                else:
                    edges += [(f[2].strip(), a, b) for a, b in zip(pts, pts[1:])]
            elif f[0] == "SOLIDREGION" and len(f) > 3 and f[1].strip() in PLUS_LAYERS:
                pts = _path_points(f[3])
                if not pts:
                    continue
                pts = [(_mm(x - ox), _mm(y - oy)) for x, y in pts]
                xs, ys = [p[0] for p in pts], [p[1] for p in pts]
                w, h = max(xs) - min(xs), max(ys) - min(ys)
                if len(pts) <= 5 and max(w, h) >= PLUS_ASPECT * max(min(w, h), 1e-9):
                    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
                    bars.append((f[1].strip(), (min(xs), cy), (max(xs), cy)) if w >= h else
                                (f[1].strip(), (cx, min(ys)), (cx, max(ys))))
                else:
                    edges += [(f[1].strip(), a, b) for a, b in zip(pts, pts[1:])]
        except (ValueError, IndexError):
            continue
    horiz = [b for b in bars if abs(b[1][1] - b[2][1]) < 1e-6 < abs(b[1][0] - b[2][0])]
    vert = [b for b in bars if abs(b[1][0] - b[2][0]) < 1e-6 < abs(b[1][1] - b[2][1])]
    out = []
    for hb in horiz:
        hx0, hx1 = sorted((hb[1][0], hb[2][0]))
        hy, hl = hb[1][1], hx1 - hx0
        for vb in vert:
            vy0, vy1 = sorted((vb[1][1], vb[2][1]))
            vx, vl = vb[1][0], vy1 - vy0
            if vb[0] != hb[0] or not (hx0 <= vx <= hx1 and vy0 <= hy <= vy1):
                continue
            if max(hl, vl) > PLUS_RATIO * min(hl, vl) or abs(vx - (hx0 + hx1) / 2) > PLUS_MID * hl \
                    or abs(hy - (vy0 + vy1) / 2) > PLUS_MID * vl:
                continue
            c, clear = (vx, hy), PLUS_CLEAR * min(hl, vl)
            ends = (c, hb[1], hb[2], vb[1], vb[2])
            if any(o is not hb and o is not vb and o[0] == hb[0] and any(_seg_dist(p, o[1], o[2]) < clear
                                                                          for p in ends) for o in bars):
                continue
            if any(o[0] == hb[0] and _seg_dist(c, o[1], o[2]) < clear for o in edges):
                continue
            out.append((round(vx, 4) + 0.0, round(hy, 4) + 0.0, round(max(hl, vl), 4)))
    return out


def from_response(code: str, response) -> EEFootprint | NotFound:
    """A whole API response (as cached) -> EEFootprint, or NotFound for an unknown code or a record without a
    footprint. Raises EEError for anything else."""
    code = check_code(code)
    if not isinstance(response, dict):
        raise EEError(f"{code}: unexpected EasyEDA response")
    if response.get("success") is not True:
        if str(response.get("code")) == "404":
            return NotFound(code, str(response.get("message") or "Component not found"))
        raise EEError(f"{code}: EasyEDA error {response.get('code')}: {response.get('message', '')}".rstrip(": "))
    result = response.get("result")
    if not isinstance(result, dict) or not result:
        return NotFound(code, "EasyEDA returned an empty record (no component)")
    pk = result.get("packageDetail")
    if not isinstance(pk, dict) or not pk or not pk.get("dataStr"):
        return NotFound(code, "the EasyEDA record has no footprint (packageDetail missing)")
    return parse_record(code, result)


# ------------------------------------------------------------------------------------------------------- client

def _retry_after(value, now: float) -> float | None:
    """Seconds from a Retry-After header (delta-seconds or an HTTP date)."""
    if not value:
        return None
    value = str(value).strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        t = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if t is None:
        return None
    if t.tzinfo is None:               # "-0000" parses naive; HTTP dates are UTC
        t = t.replace(tzinfo=datetime.timezone.utc)
    return max(0.0, t.timestamp() - now)


def _is_retryable(status: int | None) -> bool:
    return status is None or status in (403, 408, 425, 429) or 500 <= status <= 599


class EEClient:
    """EasyEDA records through a cache directory: `cache_dir/<code>.json` holds the raw response verbatim, and records
    are always parsed from that file.

    Network policy: at least `min_interval` s between requests; HTTP 403/429/5xx, network errors and non-JSON bodies
    (a CDN's HTML error page) are retried after BACKOFF[n] s plus up to 25 % jitter, or after Retry-After when that is
    longer; after `max_failures` consecutive failures, a Retry-After above `max_wait`, or retry waits that would add
    up to more than `max_total_wait` s over the client's life (a success resets the failure count, not this), the
    client stops calling the network and raises EEError. A not-found answer is cached and trusted for
    `notfound_ttl_days` (by the file's mtime), then fetched again. With `offline=True` the network is never used and a
    cache miss raises EEError (the code is added to `misses`).

    `opener(request, timeout=...)` defaults to urllib.request.urlopen; `sleep`, `clock` (monotonic, for the interval)
    and `now` (wall clock, for the TTL and HTTP dates) and `rng` (jitter) are injectable for tests.
    """

    def __init__(self, cache_dir=None, *, offline: bool = False, min_interval: float = 0.7, timeout: float = 30.0,
                 max_failures: int = 5, notfound_ttl_days: float = 7, opener=None, sleep=None, clock=None, now=None,
                 rng=None, max_wait: float = 300.0, max_total_wait: float = 600.0, user_agent: str = USER_AGENT):
        self.cache_dir = Path(cache_dir).expanduser() if cache_dir is not None else default_cache_dir()
        self.offline = offline
        self.min_interval = float(min_interval)
        self.timeout = float(timeout)
        self.max_failures = max(1, int(max_failures))
        self.notfound_ttl = float(notfound_ttl_days) * 86400.0
        self.max_wait = float(max_wait)
        self.max_total_wait = float(max_total_wait)
        self.user_agent = user_agent
        self._open = opener or urllib.request.urlopen
        self._sleep = sleep or time.sleep
        self._clock = clock or time.monotonic
        self._now = now or time.time
        self._rng = rng or random.Random()
        self._last: float | None = None
        self.failures = 0              # consecutive
        self.requests = 0              # made by this client
        self.waited = 0.0              # s slept before retries, over the client's life
        self.misses: list[str] = []    # codes an offline client did not find in the cache
        self._stopped = ""             # why the network was disabled, when not by consecutive failures

    @property
    def disabled(self) -> bool:
        """True once `max_failures` consecutive failures have stopped this client from using the network."""
        return self.failures >= self.max_failures

    def cache_path(self, code: str) -> Path:
        return self.cache_dir / f"{check_code(code)}.json"

    def raw(self, code: str) -> dict | None:
        """The cached raw response, or None when there is none (or it is not valid JSON)."""
        try:
            with open(self.cache_path(code), "rb") as f:
                d = json.loads(f.read())
        except (OSError, ValueError):
            return None
        return d if isinstance(d, dict) else None

    def get(self, code: str, *, refresh: bool = False) -> EEFootprint | NotFound:
        """The parsed footprint, or NotFound. `refresh=True` ignores the cached response (needs the network)."""
        code = check_code(code)
        path = self.cache_path(code)
        stale = None
        if not refresh:
            cached = self.raw(code)
            if cached is not None:
                res = from_response(code, cached)
                if not isinstance(res, NotFound) or self.offline or not self._expired(path):
                    return res
                stale = res
            elif self.offline:
                why = "is not valid JSON" if path.exists() else "is not in the cache"
                if code not in self.misses:
                    self.misses.append(code)
                raise EEError(f"{code}: offline and {path} {why}")
        elif self.offline:
            raise EEError(f"{code}: refresh needs the network, but the client is offline")
        try:
            body = self._fetch(code)
        except EEError as e:
            if stale is None:
                raise
            log.warning("%s; keeping the cached not-found answer", e)
            return stale
        self._store(path, body)
        cached = self.raw(code)
        if cached is None:
            raise EEError(f"{code}: could not read back {path}")
        return from_response(code, cached)

    def _expired(self, path: Path) -> bool:
        try:
            return self._now() - path.stat().st_mtime > self.notfound_ttl
        except OSError:
            return True

    def _store(self, path: Path, body: bytes) -> None:
        """Atomically: a reader sees the old response or the new one, never a partial file."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}.", suffix=".tmp")
        except OSError as e:
            raise EEError(f"cannot write the EasyEDA cache {path.parent}: {e}") from None
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(body)
            os.replace(tmp, path)
        except BaseException as e:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            if isinstance(e, OSError):
                raise EEError(f"cannot write {path}: {e}") from None
            raise

    def _wait_interval(self) -> None:
        if self._last is not None:
            dt = self._clock() - self._last
            if dt < self.min_interval:
                self._sleep(self.min_interval - dt)
        self._last = self._clock()

    def _request(self, url: str):
        """One HTTP request -> (status | None, body, Retry-After header, error text)."""
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent, "Accept": "application/json"})
        self.requests += 1
        try:
            resp = self._open(req, timeout=self.timeout)
            try:
                status = getattr(resp, "status", None) or resp.getcode()
                return int(status), resp.read(), None, ""
            finally:
                close = getattr(resp, "close", None)
                if close:
                    close()
        except urllib.error.HTTPError as e:
            try:
                body = e.read() or b""
            except Exception:  # noqa: BLE001 - an unreadable error body is just empty
                body = b""
            hdrs = getattr(e, "headers", None) or getattr(e, "hdrs", None)
            return e.code, body, hdrs.get("Retry-After") if hdrs is not None else None, f"HTTP {e.code}"
        except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as e:
            return None, b"", None, f"network error: {getattr(e, 'reason', e)}"

    def _fetch(self, code: str) -> bytes:
        """The raw body of a usable response (a record or a not-found answer)."""
        if self.disabled:
            raise EEError(f"{code}: EasyEDA API disabled for this run "
                          + (f"({self._stopped})" if self._stopped else f"after {self.failures} consecutive failures"))
        while True:
            self._wait_interval()
            status, body, retry_after, err = self._request(API_URL.format(code=code))
            retryable = _is_retryable(status)
            try:
                data = json.loads(body) if body else None
            except ValueError:
                data = None
            ok = status is not None and 200 <= status < 300
            if isinstance(data, dict):
                jcode = str(data.get("code"))
                if ok and data.get("success") is True:
                    self.failures = 0
                    return body
                if (ok or status == 404) and data.get("success") is False and jcode == "404":
                    self.failures = 0          # an answer: the code is unknown
                    return body
                if data.get("success") is False:
                    err = f"HTTP {status}: EasyEDA error {jcode}: {data.get('message', '')}".rstrip(": ")
                else:
                    err = f"HTTP {status}: unexpected JSON (no success flag)"
                if ok:                         # an error inside a 2xx: retry only what looks like rate limiting
                    retryable = jcode in ("403", "429") or jcode.startswith("5")
            elif ok:
                err = f"HTTP {status} but the body is not JSON (a proxy or CDN page?)"
                retryable = True
            elif status is not None:
                err = f"HTTP {status}" + (" (non-JSON error page)" if body else "")
            self.failures += 1
            if not retryable:
                raise EEError(f"{code}: {err}")
            if self.disabled:
                raise EEError(f"{code}: {err}; {self.failures} consecutive failures, EasyEDA API disabled for this "
                              f"run")
            base = BACKOFF[min(self.failures - 1, len(BACKOFF) - 1)]
            wait = base + self._rng.uniform(0.0, base / 4.0)
            ra = _retry_after(retry_after, self._now())
            if ra is not None:
                wait = max(wait, ra)
            if wait > self.max_wait:
                self.failures = self.max_failures
                self._stopped = f"asked to wait {wait:.0f} s"
                raise EEError(f"{code}: {err}; asked to wait {wait:.0f} s, EasyEDA API disabled for this run")
            if self.waited + wait > self.max_total_wait:
                self.failures = self.max_failures
                self._stopped = f"{self.waited:.0f} s of retry waits, at most {self.max_total_wait:g} s per run"
                raise EEError(f"{code}: {err}; {self.waited:.0f} s waited in this run, another {wait:.0f} s would pass "
                              f"the {self.max_total_wait:g} s it may wait: EasyEDA API disabled for this run")
            log.warning("EasyEDA %s: %s; retrying in %.1f s (failure %d of %d)", code, err, wait, self.failures,
                        self.max_failures)
            self.waited += wait
            self._sleep(wait)
