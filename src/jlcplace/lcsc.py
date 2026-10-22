"""Find a footprint's LCSC part code, and the fields other tools use to carry placement corrections.

Field names are matched case-insensitively and ignoring spaces, underscores and hyphens ("LCSC_PN" == "lcsc pn").
A field whose value is not a code (e.g. "LCSC_R1", or an MPN such as "CL10A106KP8NNNC") is skipped and the search
moves on to the next name, so MPN only counts when it holds a code.
"""

from __future__ import annotations

import re
import unicodedata

#: searched in this order, after the caller's own names
LCSC_FIELDS: tuple[str, ...] = (
    "LCSC", "LCSC Part", "LCSC Part #", "LCSC#", "LCSC_PN", "LCSC PN",
    "JLCPCB Part", "JLCPCB Part #", "JLC", "JLC_PN", "JLCPCB",
    # supplier-specific spellings other tools read (Fabrication-Toolkit, kicad-jlcpcb-tools), before the generic ones
    "LCSC P/N", "LCSC Part No.", "LCSC Part Number", "JLCPCB PN", "JLCPCB P/N", "JLCPCB Part No.",
    "JLCPCB Part Number", "JLC Part", "JLC Part #", "JLC Part Number",
    "Part #", "PN", "P/N", "Part No.", "Part Number", "MPN",
)

#: fields in which other tools keep their own rotation/position offsets (Fabrication-Toolkit also reads the plain
#: "Rotation Offset"/"RotOffset"/"Position Offset"/"PosOffset"): jlcplace's correction on top would apply twice
CORRECTION_FIELDS: tuple[str, ...] = (
    "FT Rotation Offset", "FT Position Offset", "JLCPCB Rotation Offset", "JLCPCB Position Offset",
    "JLCRotOffset", "JLCPosOffset", "JLCPCB_CORRECTION", "JLCPCB Correction",
    "Rotation Offset", "RotOffset", "Position Offset", "PosOffset",
)

# LCSC numbers its parts from C1 up: a leading zero ("C0402", "C0603") is a package size typed in the wrong field
_CODE = re.compile(r"C[1-9][0-9]*")
# a label typed in front of the code: "LCSC: C2040", "LCSC#C2040", "JLCPCB Part # = C2040"
_PREFIX = re.compile(r"(?:LCSC|JLCPCB|JLC)(?:\s*(?:PART|P/N|PN))?\s*(?:#|NO\.?|NUMBER)?\s*[:=#]?\s*")


def _key(name) -> str:
    return re.sub(r"[\s_\-]+", "", unicodedata.normalize("NFKC", str(name))).casefold()


def normalize(value) -> str:
    """The code a field value holds, "C<digits>", or "" unless the value is exactly one code.

    Cleanup: Unicode compatibility forms folded (full-width characters, no-break spaces), invisible format
    characters, surrounding whitespace and quotes removed, upper-cased, one leading "LCSC:"-style label dropped."""
    if value is None:
        return ""
    s = unicodedata.normalize("NFKC", str(value))
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Cf").strip().strip("'\"").strip().upper()
    m = _PREFIX.match(s)
    if m and _CODE.fullmatch(s[m.end():]):
        s = s[m.end():]
    return s if _CODE.fullmatch(s) else ""


def find_lcsc(fields: dict, extra=()) -> tuple[str, str]:
    """(code, field name) from the first of `extra` + LCSC_FIELDS whose field holds a valid code, else ("", "").
    Among fields matching one name loosely, the exact spelling is tried first."""
    by_key: dict[str, list[str]] = {}
    for name in fields:
        by_key.setdefault(_key(name), []).append(name)
    for want in (*extra, *LCSC_FIELDS):
        for name in sorted(by_key.get(_key(want), ()), key=lambda n: n != want):
            code = normalize(fields[name])
            if code:
                return code, name
    return "", ""


def correction_fields(fields: dict) -> dict[str, str]:
    """{field name: value} for the non-empty fields that carry another tool's placement offsets."""
    keys = {_key(n) for n in CORRECTION_FIELDS}
    return {name: str(v).strip() for name, v in fields.items()
            if _key(name) in keys and v is not None and str(v).strip()}
