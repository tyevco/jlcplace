"""Part classes, pad-number spelling and the EasyEDA footprint key, shared by fit, check and the pipeline so they
cannot drift apart."""

from __future__ import annotations

import re

from . import geom

#: two-terminal parts whose terminals may be swapped, by reference prefix, unless something says they are polar
NONPOLAR_REFS = frozenset({"R", "RN", "RT", "RV", "C", "L", "FB", "F", "NTC", "Y", "X", "XTAL", "SW", "JP"})
#: KiCad footprint names that say a two-terminal part is polar
POLAR_FOOTPRINT = re.compile(r"diode|led|tantal|polar|elec|cp_|:d_|zener|schottky|tvs|batt", re.I)


def norm_number(s) -> str:
    """Pad numbers compare case-insensitively, and '01' is '1'."""
    s = str(s or "").strip().upper()
    return (s.lstrip("0") or "0") if s.isdigit() else s


def ref_prefix(ref: str) -> str:
    """'R12' -> 'R', 'LED3' -> 'LED', '' when the reference does not start with letters."""
    m = re.match(r"[A-Za-z]+", ref or "")
    return m.group(0).upper() if m else ""


def ee_key(ee) -> str:
    """The key a correction is stored under for an EasyEDA footprint: a hash of its pads in the ee frame (numbers,
    positions, sizes, angles). EEFootprint.key() (uuid:updateTime) also moves when EasyEDA edits only a record's
    metadata on its server (C2500's updateTime is 16 months after its last editor save; the licence notice now in
    every record is the likely edit), which would make verified corrections stale for nothing. The pads are what a
    correction depends on: any change that moves a pad changes this key."""
    return "ee-" + geom.pad_hash(ee.pads)
