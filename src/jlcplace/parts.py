"""Part classes, pin names, pad-number spelling and the database keys, shared by fit, check and the pipeline so they
cannot drift apart."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import NamedTuple

from . import geom

#: two-terminal parts whose terminals may be swapped, by reference prefix, unless something says they are polar. X is
#: not one: IEC 81346 designates connectors X (a battery connector is polar); an X part counts only when its footprint
#: or its EasyEDA title says crystal
NONPOLAR_REFS = frozenset({"R", "RN", "RT", "RV", "C", "L", "FB", "F", "NTC", "Y", "XTAL", "SW", "JP"})
#: KiCad footprint names that say a two-terminal part is polar ("led" and "elec" as words: not "Angled", "KOHERelec")
POLAR_FOOTPRINT = re.compile(r"diode|(?<![a-z])led|tant|polar|(?<![a-z])elec|cp_|:d_|zener|schottky|tvs|batt", re.I)
#: EasyEDA titles that say so (SOD-123, LED0603, ...)
POLAR_EE_TITLE = re.compile(r"(?<![a-z])led|sod|diode|tant|(?<![a-z])elec", re.I)
#: EasyEDA part categories (result.tags) that say so; ceramics, resistors, inductors, crystals have their own
POLAR_EE_TAGS = re.compile(r"tantal|electrolytic|polymer|diode|(?<![a-z])leds?(?![a-z])|zener|schottky|(?<![a-z])tvs"
                           r"|rectifier", re.I)
#: EasyEDA's titles for tantalum, polymer and electrolytic chip capacitors (its ceramics are C0402, C1206, ...): they
#: count when the record has no category that names a non-polar class (some records have no categories at all)
POLAR_EE_CAP_TITLE = re.compile(r"^(CAP-SMD|CASE-[A-E]_|CP[-_])", re.I)
NONPOLAR_EE_TAGS = re.compile(r"ceramic|mlcc|resistor|inductor|ferrite|crystal|oscillator|thermistor|fuse|varistor",
                              re.I)
#: KiCad footprint names of right-angle parts: turned round, the body faces the other way, which is never harmless.
#: KiCad names its right-angle connectors, terminal blocks and switches, and its crystals lying flat, "_Horizontal"
#: (in their libraries, or under such a name in another library); its axial passives lie flat between their leads
#: and its fuse clips are alike at both ends, so "Horizontal" alone says nothing there
ORIENTED_FOOTPRINT = re.compile(r"(?<![a-z])angled|right.?angle"
                                r"|^(?:(?:Connector|TerminalBlock|Button_Switch|Rotary_Encoder|Potentiometer|Crystal)"
                                r"[^:]*:|(?:[^:]*:)?(?:SW|Switch|Button|PinHeader|PinSocket|Header|Socket|Conn"
                                r"|Connector|Jack|TerminalBlock|Crystal)[_-]).*horizontal", re.I)
#: what makes an X-referenced two-terminal part a crystal
CRYSTAL = re.compile(r"crystal|xtal|resonator|(?<![a-z])osc", re.I)

POLARITY_NAMES = frozenset({"ANODE", "CATHODE"})
SYNONYMS = {
    "VSS": "GND",
    "DP": "D+", "DM": "D-", "DN": "D-",
    "SHIELD": "SHELL", "SH": "SHELL", "EH": "SHELL", "MH": "SHELL",
    "A": "ANODE", "+": "ANODE", "K": "CATHODE", "C": "CATHODE", "-": "CATHODE",
    "VCC": "VDD",
    "VO": "VOUT", "OUT": "VOUT", "VI": "VIN", "IN": "VIN", "INPUT": "VIN", "OUTPUT": "VOUT",
}
#: names that say where a pad is, not what it is (a regulator's tab is often its output): uninformative
LOCATION_NAMES = frozenset({"EP", "PAD", "EPAD", "TAB", "THERMAL", "THERMALPAD"})
#: for comparing only (name_tokens keeps the tokens the polarity rules read): an op-amp's + and - are its inputs, V+
#: and V- its supplies
_ALIKE = {"ANODE": "IN+", "CATHODE": "IN-", "V+": "VDD", "V-": "GND", "VEE": "GND"}
_UNINFORMATIVE = re.compile(r"(PIN)?\d*|NC|DNC|N\.C\.?")
#: the synonyms name_tokens applied before this module learnt LOCATION_NAMES: kicad_hash keeps them, so that the
#: database keys of stored corrections do not move
_LEGACY = {**SYNONYMS, **{n: "GND" for n in LOCATION_NAMES}}
del _LEGACY["INPUT"], _LEGACY["OUTPUT"]
#: KiCad's own libraries follow its library convention (KLC): a diode's or LED's pad 1 is the cathode, a polarised
#: capacitor's pad 1 is +. Footprints from other libraries may not.
_KLC_DIODE = re.compile(r"^(Diode_SMD|Diode_THT|LED_SMD|LED_THT):")
_KLC_CP = re.compile(r"^(Capacitor_Tantalum_SMD:|Capacitor_SMD:CP_|Capacitor_THT:CP_)")


def norm_number(s) -> str:
    """Pad numbers compare case-insensitively, and '01' is '1'."""
    s = str(s or "").strip().upper()
    return (s.lstrip("0") or "0") if s.isdigit() else s


def ref_prefix(ref: str) -> str:
    """'R12' -> 'R', 'LED3' -> 'LED', '' when the reference does not start with letters."""
    m = re.match(r"[A-Za-z]+", ref or "")
    return m.group(0).upper() if m else ""


# ------------------------------------------------------------------------------------------------------- pin names

def name_tokens(name: str, number: str = "") -> frozenset[str]:
    """Canonical tokens of a pin name: upper case without ~{} # * ! _ and spaces, split at '/', ',' and parentheses
    (alternate functions; '/RESET' is active low), 'nRESET' -> RESET, synonyms applied; a name split so also gives
    its parts joined (KiCad's P0.00/XL1 is EasyEDA's P0.00_XL1). A name that only repeats its pad number ('3' on pad
    3, 'A1' on BGA ball A1), digits, PINn, NC, or says only where the pad is (TAB, EP, PAD: LOCATION_NAMES) is
    uninformative: the empty set. A name with a meaning of its own (K on a pad numbered K, + on a pad numbered +) is
    kept."""
    return _tokens(name, number, SYNONYMS, LOCATION_NAMES)


def _tokens(name, number, synonyms, dropped) -> frozenset[str]:
    s = (name or "").replace("~{", "").replace("{", "").replace("}", "")
    out = set()
    words = [t for t in re.split(r"[/,()\s]+", s) if t.strip("~#*!")]
    for t in words + ([s] if len(words) > 1 and not dropped & {w.upper() for w in words} else []):
        t = re.sub(r"[/,()\s]+", "", t).strip("~#*!")
        if len(t) > 1 and t[0] == "n" and t[1].isupper():
            t = t[1:]
        t = t.upper().replace("_", "")
        if _UNINFORMATIVE.fullmatch(t) or t in dropped or (t == norm_number(number) and t not in synonyms):
            continue
        out.add(synonyms.get(t, t))
    return frozenset(out)


def _stem(ts) -> set[str]:
    return {SYNONYMS.get(u, u) for u in (re.sub(r"\d+$", "", t) for t in ts) if u}


def _alike(t: str) -> str:
    """The token as compared: an RGB LED's R- is its red cathode RK, an op-amp's + (ANODE) its IN+, V- its GND."""
    m = re.fullmatch(r"([RGBW])-", t)
    return f"{m.group(1)}K" if m else _ALIKE.get(t, t)


_INDEXED = re.compile(r"(\d*)(\D.*?)(\d*)")


def _split(t: str) -> tuple[str, str]:
    """(stem as compared, index): '1IN+' -> ('IN+', '1'), 'DP1' -> ('D+', '1'), 'P0.00' -> ('P0.', '00')."""
    m = _INDEXED.fullmatch(t)
    if not m:
        return "", t
    stem = SYNONYMS.get(m.group(2), m.group(2))
    return _alike(stem), f"{m.group(1)}.{m.group(3)}" if m.group(1) or m.group(3) else ""


#: an op-amp's input with its sign before or after it, V and a unit (number, or A-D) optional: VIN+, +IN, IN1+, INA-
_OPAMP_INPUT = re.compile(r"([+-])V?IN([A-D]|\d+)?|V?IN([A-D]|\d+)?([+-])")
#: a split supply's rail: VCC+, VS+, +VS, VDD+ (positive), VCC-, VS-, -VS (negative)
_SIGNED_SUPPLY = re.compile(r"([+-])V(?:CC|DD|S)|V(?:CC|DD|S)([+-])")


def _signed(t: str) -> str:
    """An op-amp input or a split supply as compared: VIN+, +IN, IN1+ (unit 1), INA+ (unit A = 1) as [unit]IN+; VCC+,
    VS+, +VS as VDD and VCC-, VS-, -VS as GND, as V+ and V- are. A plain VIN (a regulator's input) stays."""
    m = _OPAMP_INPUT.fullmatch(t)
    if m:
        unit = m.group(2) or m.group(3) or ""
        return f"{'ABCD'.index(unit) + 1 if unit in ('A', 'B', 'C', 'D') else unit}IN{m.group(1) or m.group(4)}"
    m = _SIGNED_SUPPLY.fullmatch(t)
    if m:
        return "VDD" if (m.group(1) or m.group(2)) == "+" else "GND"
    return t


def compare_names(a: frozenset, b: frozenset) -> int:
    """+1 agree, -1 disagree, 0 unknown: a side uninformative; equal up to an index both sides number differently
    (CC1 vs CC2, P1.01 vs P1.03, 1IN+ vs 2IN+), which is how libraries differ on symmetric connectors and units; or
    G against GND (a gate, or KiCad's crystal ground). Equal up to an index only one side has agree (DP1 vs D+: a
    USB-C's D+ of port 1; 1IN+ vs +: an op-amp's input of unit 1). An op-amp's inputs and supplies compare in one
    spelling (_signed: VIN+, IN1+ and INA+ are 1IN+ or IN+, VCC+ is V+)."""
    if not a or not b:
        return 0
    a, b = frozenset(map(_signed, a)), frozenset(map(_signed, b))
    ca, cb = {_alike(t) for t in a}, {_alike(t) for t in b}
    if ca & cb:
        return 1
    out = -1
    for sa, ia in map(_split, a):
        for sb, ib in map(_split, b):
            if sa and sa == sb:
                if not ia or not ib or ia == ib:
                    return 1
                out = 0
    if out < 0 and ("G" in a and "GND" in cb or "G" in b and "GND" in ca):
        out = 0
    return out


class Pin(NamedTuple):
    """One side's pin at a pad: its number, its name as written (for messages), name_tokens of it, and on the KiCad
    side the net key of its pad (a pad without a net is its own net)."""
    number: str
    name: str
    tokens: frozenset
    net: object = None


@dataclass(frozen=True)
class PinDiff:
    """An EasyEDA pad whose pin name disagrees with the pin function of the KiCad pad it lands on.
    kind: "swapped"  the EasyEDA name is the function of another KiCad pad, on another net, and the KiCad function the
                     name of another EasyEDA pad: the names are permuted, and the part's pin sits on the net meant for
                     another of its pins (a MOSFET's S on the board's D); or the KiCad pad says nothing (NC, ~) and no
                     EasyEDA pin of that function reaches the net of the KiCad pads that have it (an LM358's VCC on a
                     single op-amp's NC pad: the part is unpowered);
          "same net" the EasyEDA name is the function of a KiCad pad on the net it lands on: harmless;
          "differs"  neither: the libraries name the pins differently (VIN / IN+, B/C/E against G/S/D)."""
    ee_number: str
    ee_name: str
    kicad_number: str
    kicad_name: str
    kind: str


def pin(number, name, net=None) -> Pin:
    return Pin(str(number or ""), str(name or ""), name_tokens(name, number), net)


def pin_diffs(pairs, ee_pins, kicad_pins) -> list[PinDiff]:
    """The pads whose names disagree (compare_names < 0), or whose named pin lands on a pad whose function says
    nothing while its function's net goes without, through a placement, in the order of `pairs`: (EasyEDA Pin,
    KiCad Pin) for every EasyEDA pad on copper. `ee_pins` and `kicad_pins` are every Pin of each side (the KiCad ones
    with their nets), where a name is looked for on the other pads."""
    ee_pins, kicad_pins = list(ee_pins), list(kicad_pins)
    out = []
    for e, k in pairs:
        c = compare_names(e.tokens, k.tokens)
        if c == 0 and e.tokens and not k.tokens:
            # a named pin on a pad whose function says nothing: harmful when its function's net, on other pads, gets
            # no EasyEDA pin of that function (up to a unit index: unit 2's input beside unit 1's is no loss)
            homes = {q.net for q in kicad_pins if compare_names(e.tokens, q.tokens) > 0}
            if homes and k.net not in homes and not any(q.net in homes for f, q in pairs
                                                        if f.tokens and compare_names(f.tokens, e.tokens) >= 0):
                out.append(PinDiff(e.number, e.name, k.number, k.name, "swapped"))
            continue
        if c >= 0:
            continue
        homes = [q.net for q in kicad_pins if compare_names(e.tokens, q.tokens) > 0]
        if k.net in homes:
            kind = "same net"
        elif homes and any(compare_names(q.tokens, k.tokens) > 0 for q in ee_pins):
            kind = "swapped"
        else:
            kind = "differs"
        out.append(PinDiff(e.number, e.name, k.number, k.name, kind))
    return out


def pinout_text(diffs: list[PinDiff], n: int = 4, board: str = "on the board") -> str:
    """'pad 2 is S on the LCSC part, D on the board; pad 3 (EasyEDA pad 2) is ...' for the first n. `board` says
    where the KiCad side's names come from when the board's pads have no pin functions ("by KiCad's library
    convention (pad 1 = cathode)")."""
    def one(d):
        where = f"pad {d.kicad_number}" + (f" (EasyEDA pad {d.ee_number})"
                                           if norm_number(d.ee_number) != norm_number(d.kicad_number) else "")
        return f"{where} is {d.ee_name} on the LCSC part, {d.kicad_name} {board}"
    return "; ".join(one(d) for d in diffs[:n]) + (f"; {len(diffs) - n} more" if len(diffs) > n else "")


def _terminal_names(part) -> dict[str, frozenset[str]]:
    """KiCad pad number -> the name tokens of its pin functions (all pads of that number)."""
    out: dict[str, set[str]] = {}
    for p in part.copper_pads():
        if p.number:
            out.setdefault(norm_number(p.number), set()).update(name_tokens(p.pin_name, p.number))
    return {n: frozenset(t) for n, t in out.items()}


def _ee_terminal_names(ee) -> dict[str, frozenset[str]]:
    names = {norm_number(k): v for k, v in (ee.pin_names or {}).items()}
    return {n: name_tokens(names.get(n, ""), n) for n in {norm_number(p.number) for p in ee.pads if p.number}}


def _bidirectional(names: dict[str, frozenset[str]]) -> bool:
    """Two terminals named alike up to an index (a bidirectional TVS's A1 / A2): swapping them changes nothing."""
    if len(names) != 2:
        return False
    a, b = names.values()
    return bool(a and b and a != b and not (a | b) & POLARITY_NAMES and _stem(a) and _stem(a) == _stem(b))


# ------------------------------------------------------------------------------------------------------- polarity

def nonpolar(part, ee=None) -> bool:
    """Two numbered terminals that may be swapped. True for a two-terminal part whose two terminals are alike (KiCad
    pin functions equal up to an index, A1 / A2, or an EasyEDA title marked BI: a bidirectional TVS); otherwise it
    takes a non-polar reference prefix (X only for a crystal) and nothing that says polar: the KiCad footprint name
    (polar words, or a right-angle body), anode / cathode / + / - KiCad pin functions, and with the EasyEDA footprint
    `ee` its title (a polarised capacitor's CAP-SMD_ / CASE-x_ unless its categories name a non-polar class), its
    part categories (tantalum, electrolytic, diode, LED, TVS, ...) and its symbol's pin names. Unknown parts count as
    polar. fit and check both use this, so they cannot judge a part differently."""
    kicad = _terminal_names(part)
    if len(kicad) != 2:
        return False
    if _bidirectional(kicad) or (ee is not None and "BI" in (ee.tokens or [])):
        return True
    prefix = ref_prefix(part.ref)
    if prefix not in NONPOLAR_REFS and not (prefix == "X" and (CRYSTAL.search(part.footprint or "") or (
            ee is not None and CRYSTAL.search(ee.title or "")))):
        return False
    if POLAR_FOOTPRINT.search(part.footprint or "") or ORIENTED_FOOTPRINT.search(part.footprint or "") \
            or any(t & POLARITY_NAMES for t in kicad.values()):
        return False
    if ee is not None:
        tags = ee.tags or []
        if POLAR_EE_TITLE.search(ee.title or "") or any(POLAR_EE_TAGS.search(t) for t in tags):
            return False
        if POLAR_EE_CAP_TITLE.search(ee.title or "") and not any(NONPOLAR_EE_TAGS.search(t) for t in tags):
            return False
        names = _ee_terminal_names(ee)
        if len(names) == 2 and any(t & POLARITY_NAMES for t in names.values()):
            return False
    return True


def kicad_positive_pad(part) -> tuple[str, str] | None:
    """(pad number, how it is known) of a two-terminal KiCad part's positive terminal (a diode's anode, a capacitor's
    +): from its pin functions ("pin names") or pad numbers (A, K, +, -: "pad numbers"), else from the KiCad library
    convention for KiCad's own diode, LED and polarised capacitor libraries. None when nothing says."""
    kicad = dict(_terminal_names(part))
    if len(kicad) != 2:
        return None
    how = "pin names" if any(kicad.values()) else "pad numbers"
    for n in kicad:                       # pads numbered A / K / + / - name themselves
        if not kicad[n]:
            kicad[n] = name_tokens(n)
    pos = [n for n, t in kicad.items() if "ANODE" in t and "CATHODE" not in t]
    neg = [n for n, t in kicad.items() if "CATHODE" in t and "ANODE" not in t]
    if len(pos) == 1 and pos != neg:
        return pos[0], how
    if len(neg) == 1:
        return next(n for n in kicad if n != neg[0]), how
    if set(kicad) == {"1", "2"}:
        if _KLC_DIODE.match(part.footprint or ""):
            return "2", "KiCad's library convention (pad 1 = cathode)"
        if _KLC_CP.match(part.footprint or ""):
            return "1", "KiCad's library convention (pad 1 = +)"
    return None


PLUS_LENGTH = (0.05, 0.70)             # a "+" bar's length as a fraction of the pad spacing ...
PLUS_FLOOR = 0.75                      # ... the lower bound never above this many mm
PLUS_OFF_CENTRE = 0.15                 # a "+" this close to the middle (fraction of the spacing) names no pad


def ee_positive_pad(ee) -> str | None:
    """The number of the EasyEDA pad a two-terminal footprint draws its "+" beside (EEFootprint.plus), or None:
    no "+", one near the middle (an origin cross), or marks on both sides."""
    pos: dict[str, list] = {}
    for p in ee.pads:
        if p.number and p.layer != "bottom":
            pos.setdefault(norm_number(p.number), []).append((p.x, p.y))
    if len(pos) != 2 or not ee.plus:
        return None
    (n1, a), (n2, b) = ((n, (sum(q[0] for q in v) / len(v), sum(q[1] for q in v) / len(v))) for n, v in pos.items())
    spacing = math.dist(a, b)
    if spacing <= 0:
        return None
    ux, uy = (b[0] - a[0]) / spacing, (b[1] - a[1]) / spacing
    cx, cy = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    low, high = min(PLUS_LENGTH[0] * spacing, PLUS_FLOOR), PLUS_LENGTH[1] * spacing
    sides = set()
    for x, y, length in ee.plus:
        along = (x - cx) * ux + (y - cy) * uy
        if low <= length <= high and abs(along) >= PLUS_OFF_CENTRE * spacing:
            sides.add(n2 if along > 0 else n1)
    return sides.pop() if len(sides) == 1 else None


# ------------------------------------------------------------------------------------------------------- coverage

def pad_size(p) -> float:
    """The smaller dimension of a KiCad pad's copper along its own axes (its outline's, for a custom pad)."""
    if not p.poly:
        return min(p.w, p.h)
    a = math.radians(p.angle or 0.0)
    c, s = math.cos(a), math.sin(a)
    pts = [((q[0] - p.x) * c - (q[1] - p.y) * s, (q[0] - p.x) * s + (q[1] - p.y) * c) for q in geom.pad_outline(p)]
    return min(max(q[0] for q in pts) - min(q[0] for q in pts), max(q[1] for q in pts) - min(q[1] for q in pts))


VIA_SIZE = 1.0                         # mm: a plated hole no larger than this, numbered like a pad, is a via
SAME_PIN_AREA = 2.0                    # a pad of a covered pin this many times smaller is a stub of that pin


def _area(p) -> float:
    pts = geom.pad_outline(p)
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, pts[1:] + pts[:1]))) / 2


def uncovered_exemption(p, covered) -> str:
    """Why a KiCad copper pad that no EasyEDA pad lands on is no sign of a footprint mismatch, "" when it is:
    "inside" (its centre is on a covered pad: thermal vias in an exposed pad), "via" (a small plated hole numbered
    like a covered SMD pad: the vias of an ESP32 module's GND pads), or "same pin" (a much smaller pad of the same
    number and net as a covered one: the stub of a DPAK's cut middle lead, numbered like its tab). `covered`: the
    copper pads some EasyEDA pad lands on. Equal pads of a number (two MP pads) are never exempt."""
    if any(q is not p and geom.pad_contains(q, (p.x, p.y)) for q in covered):
        return "inside"
    n = norm_number(p.number)
    same = [q for q in covered if q is not p and n and norm_number(q.number) == n]
    if p.kind == "tht" and max(p.w, p.h) <= VIA_SIZE and any(q.kind == "smd" for q in same):
        return "via"
    if any(q.net == p.net and _area(q) >= SAME_PIN_AREA * _area(p) for q in same):
        return "same pin"
    return ""


# ------------------------------------------------------------------------------------------------------- keys

def ee_key(ee) -> str:
    """The key a correction is stored under for an EasyEDA footprint: a hash of its pads in the ee frame (numbers,
    positions, sizes, angles). EEFootprint.key() (uuid:updateTime) also moves when EasyEDA edits only a record's
    metadata on its server (C2500's updateTime is 16 months after its last editor save; the licence notice now in
    every record is the likely edit), which would make verified corrections stale for nothing. The pads are what a
    correction depends on: any change that moves a pad changes this key."""
    return "ee-" + geom.pad_hash(ee.pads)


def _legacy_tokens(name: str, number: str = "") -> frozenset[str]:
    """name_tokens as the database keys were made with: location names read as GND, no joined parts."""
    s = (name or "").replace("~{", "").replace("{", "").replace("}", "")
    out = set()
    for t in re.split(r"[/,()\s]+", s):
        t = t.strip("~#*!")
        if len(t) > 1 and t[0] == "n" and t[1].isupper():
            t = t[1:]
        t = t.upper().replace("_", "")
        if _UNINFORMATIVE.fullmatch(t) or (t == norm_number(number) and t not in _LEGACY):
            continue
        out.add(_LEGACY.get(t, t))
    return frozenset(out)


def kicad_hash(part) -> str:
    """The key a correction is stored under for a KiCad part: geom.pad_hash of its soldered copper pads and, when any
    pin function says something (name_tokens), those names too. The fit decides polarity and orientation by pin
    names as well as by pads, so two parts on one footprint whose pin functions differ (a diode with pad 1 = K and
    one with pad 1 = A) need corrections of their own: one must never take, or vouch for, the other's."""
    pads = part.copper_pads()
    h = geom.pad_hash(pads)
    names = sorted({(norm_number(p.number), tuple(sorted(_legacy_tokens(p.pin_name, p.number)))) for p in pads
                    if p.number and _legacy_tokens(p.pin_name, p.number)})
    if not names:
        return h
    return "sha1:" + hashlib.sha1((h + repr(names)).encode()).hexdigest()[:16]
