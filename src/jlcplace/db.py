"""The correction database: one JSON file per LCSC code, made to be kept in git and shared.

    <dir>/<code>.json = {"lcsc": code, "entries": [entry, ...]}
    entry = {"ee": {"key": parts.ee_key, "title"}, "kicad": {"fpid", "pad_hash": parts.kicad_hash},
             "correction": {"rot", "dx", "dy", "mirror"}, "fit": {"method", "rms", "matched", "score",
             "alternatives", "doubt"?, "polar"?}, "verdict", "reasons", "status", "verifications": [{"by", "date",
             "source", "ok", "board", "ref", "layer", "presses", "note"}], "tool", "fitted"}

Only derived numbers, hashes and the EasyEDA footprint's title (for people reading the file) are stored, never pad
geometry: EasyEDA claims copyright over its library, and the numbers are all a CPL needs. An entry is keyed by (ee
key, KiCad key): the KiCad key hashes the pads and the pin functions, so the same code on two KiCad footprints, or on
one footprint with other pin names, is two entries; a changed EasyEDA footprint (new key) makes the old entry stale.

Status, strongest first: board-verified (the assembled board was right), preview-verified (JLC's placement preview
was right), manual (typed in), auto (fitted). A verification can carry the keys pressed in JLC's preview to put the
part right; presses_to_correction folds them into the correction (Fabrication-Toolkit's convention, derived there).
"""

from __future__ import annotations

import copy
import datetime
import json
import math
import re
from dataclasses import replace
from pathlib import Path

from .convention import norm_angle, rot
from .cpl import atomic_write
from .model import RED, VERIFIED, YELLOW, Correction, Part

TOOL = "jlcplace 0.1.0"
PRESS_MM = 0.0635                     # one arrow press in JLC's preview: 1/400 in
PRESS_REASON = "derived from presses (FT convention)"
STALE_REASON = "footprint changed since verified"
STATUS_RANK = {"auto": 0, "manual": 1, "preview-verified": 2, "board-verified": 3}
SOURCES = {"board": "board-verified", "preview": "preview-verified", "manual": "manual",
           "board-verified": "board-verified", "preview-verified": "preview-verified"}
_SAME_ROT, _SAME_MM = 1e-4, 1e-4      # corrections closer than this are the same correction


def _today() -> str:
    return datetime.date.today().isoformat()


def _code(code: str) -> str:
    c = str(code).strip().upper()
    if not re.fullmatch(r"C[1-9][0-9]*", c):
        raise ValueError(f"not an LCSC code: {code!r}")
    return c


def _num(v, nd: int = 4):
    return round(float(v), nd) + 0.0


def _scalars(d: dict) -> dict:
    """The scalar items of a dict (numbers rounded): what may be stored from a fit's alternative. Lists and nested
    objects, where pad positions would live, are dropped."""
    out = {}
    for k, v in d.items():
        if isinstance(v, bool) or v is None or isinstance(v, str):
            out[str(k)] = v
        elif isinstance(v, (int, float)) and math.isfinite(v):
            out[str(k)] = _num(v) if isinstance(v, float) else v
    return out


def _same(a: dict, b: dict) -> bool:
    da = abs((a["rot"] - b["rot"] + 180.0) % 360.0 - 180.0)
    return (da <= _SAME_ROT and abs(a["dx"] - b["dx"]) <= _SAME_MM and abs(a["dy"] - b["dy"]) <= _SAME_MM
            and bool(a.get("mirror")) == bool(b.get("mirror")))


def _corr_block(corr: Correction) -> dict:
    return {"rot": norm_angle(corr.rot), "dx": _num(corr.dx), "dy": _num(corr.dy), "mirror": bool(corr.mirror)}


def _fit_block(corr: Correction) -> dict:
    out = {"method": corr.method, "rms": _num(corr.rms), "matched": int(corr.matched), "score": int(corr.score),
           "alternatives": [_scalars(a) for a in corr.alternatives if isinstance(a, dict)]}
    if corr.doubt:
        out["doubt"] = corr.doubt
    if corr.polar is not None:
        out["polar"] = bool(corr.polar)
    return out


def _to_correction(e: dict, status: str | None = None) -> Correction:
    c, f = e["correction"], e.get("fit", {})
    return Correction(rot=float(c["rot"]), dx=float(c["dx"]), dy=float(c["dy"]), mirror=bool(c.get("mirror")),
                      method=f.get("method") or "db", rms=float(f.get("rms", 0.0)), verdict=e.get("verdict", ""),
                      score=int(f.get("score", 0)), reasons=list(e.get("reasons", [])),
                      alternatives=copy.deepcopy(f.get("alternatives", [])), matched=int(f.get("matched", 0)),
                      ee_key=_keys(e)[0], kicad_hash=_keys(e)[1], status=status or e.get("status", "auto"),
                      doubt=str(f.get("doubt") or ""), polar=None if f.get("polar") is None else bool(f["polar"]))


def _keys(e: dict) -> tuple[str, str]:
    """(ee key, KiCad pad hash) of an entry; a hand-edited entry may lack either block."""
    return (e.get("ee") or {}).get("key", ""), (e.get("kicad") or {}).get("pad_hash", "")


def _best(cands: list[tuple[int, dict]]) -> dict:
    """Strongest status; among equals the later entry."""
    return max(cands, key=lambda ie: (STATUS_RANK.get(ie[1].get("status", "auto"), 0), ie[0]))[1]


class CorrectionDB:
    """A directory of <code>.json files. Writes are atomic (temporary file + rename), with sorted keys, a 1-space
    indent and a trailing newline, so files diff well in git."""

    def __init__(self, path):
        self.path = Path(path)

    def file(self, code: str) -> Path:
        return self.path / f"{_code(code)}.json"

    def _load(self, code: str) -> dict:
        f = self.file(code)
        if not f.exists():
            return {"lcsc": _code(code), "entries": []}
        with open(f, encoding="utf-8") as fh:
            doc = json.load(fh)
        if not isinstance(doc, dict) or not isinstance(doc.get("entries"), list):
            raise ValueError(f"{f}: not a jlcplace correction file")
        return doc

    def _save(self, code: str, doc: dict) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        atomic_write(self.file(code), json.dumps(doc, sort_keys=True, indent=1, ensure_ascii=False) + "\n")

    def entries(self, code: str) -> list[dict]:
        """The stored entries for a code (copies), [] when there is no file."""
        return copy.deepcopy(self._load(code)["entries"])

    def lookup(self, code: str, *, ee_key: str = "", kicad_hash: str = "") -> Correction | None:
        """The correction for a code on a KiCad footprint (pad hash) and EasyEDA footprint (key); "" matches any.
        Among matching entries the strongest status wins (board-verified > preview-verified > manual > auto), then
        the later entry. When entries exist for the pad hash but none for this ee key, the strongest is returned
        stale: status "stale", verdict red, reason STALE_REASON (the caller refits and compares). None when no
        entry is for this KiCad footprint."""
        cands = [(i, e) for i, e in enumerate(self._load(code)["entries"])
                 if not kicad_hash or _keys(e)[1] in ("", kicad_hash)]
        if not cands:
            return None
        exact = [(i, e) for i, e in cands if not ee_key or _keys(e)[0] in ("", ee_key)]
        if exact:
            return _to_correction(_best(exact))
        corr = _to_correction(_best(cands), status="stale")
        corr.verdict = RED
        corr.reasons.append(STALE_REASON)
        return corr

    def upsert(self, code: str, corr: Correction, *, footprint: str = "", ee_title: str = "", tool: str = TOOL
               ) -> None:
        """Store a fitted or typed-in correction under (corr.ee_key, corr.kicad_hash). Re-storing the same numbers
        refreshes the fit and keeps the status and verifications; different numbers replace the entry's
        correction, but never one with a stronger status (ValueError: confirm or edit the file instead)."""
        if corr.status not in STATUS_RANK:
            raise ValueError(f"{code}: cannot store a correction with status {corr.status!r}")
        doc = self._load(code)
        new = {"ee": {"key": corr.ee_key, "title": ee_title}, "kicad": {"fpid": footprint, "pad_hash": corr.kicad_hash},
               "correction": _corr_block(corr), "fit": _fit_block(corr), "verdict": corr.verdict,
               "reasons": list(corr.reasons), "status": corr.status, "verifications": [], "tool": tool,
               "fitted": _today()}
        old = next((e for e in doc["entries"] if _keys(e) == (corr.ee_key, corr.kicad_hash)), None)
        if old is None:
            doc["entries"].append(new)
        else:
            rank_old, rank_new = STATUS_RANK.get(old.get("status", "auto"), 0), STATUS_RANK[corr.status]
            if _same(old["correction"], new["correction"]):
                new["status"] = old["status"] if rank_old > rank_new else corr.status
                if STATUS_RANK.get(new["status"], 0) >= STATUS_RANK["preview-verified"]:
                    new["verdict"], new["reasons"] = old.get("verdict", VERIFIED), old.get("reasons", [])
            elif rank_old > rank_new:
                raise ValueError(f"{code}: refusing to replace a {old['status']} correction with a {corr.status} one")
            new["verifications"] = old.get("verifications", [])
            new["ee"]["title"] = ee_title or (old.get("ee") or {}).get("title", "")
            new["kicad"]["fpid"] = footprint or (old.get("kicad") or {}).get("fpid", "")
            doc["entries"][doc["entries"].index(old)] = new
        doc["lcsc"] = _code(code)
        self._save(code, doc)

    def confirm(self, code: str, *, ee_key: str, kicad_hash: str, by: str, source: str, board: str = "",
                ref: str = "", layer: str = "", ok: bool = True, presses=None, note: str = "",
                part: Part | None = None) -> Correction:
        """Record a verification of the entry (ee_key, kicad_hash) and return its correction as now stored.

        source: "board" (the assembled board), "preview" (JLC's placement preview) or "manual". ok=True raises the
        status to the source's (verdict VERIFIED for board/preview; a red entry confirmed manually becomes yellow).
        ok=False marks the entry red and drops it to
        "auto". presses = {"space": n, "right": n, "up": n} (or a 3-tuple), the keys pressed in JLC's preview to
        make the part right: the correction is adjusted with presses_to_correction, which needs the `part` (its
        angle and side), and the adjusted correction carries only this verification's status."""
        if source not in SOURCES:
            raise ValueError(f"source must be one of {sorted(SOURCES)}")
        doc = self._load(code)
        entry = next((e for e in doc["entries"] if _keys(e) == (ee_key, kicad_hash)), None)
        if entry is None:
            raise LookupError(f"{code}: no entry for ee key {ee_key!r} and pad hash {kicad_hash!r}")
        p = _presses(presses)
        status = SOURCES[source]
        if any(p.values()):
            if not ok:
                raise ValueError("presses describe how the part was put right; they cannot come with ok=False")
            if part is None:
                raise ValueError("presses need the part: the arrow directions depend on its angle and side")
            adjusted = presses_to_correction(part, _to_correction(entry), **p)
            entry["correction"], entry["fit"] = _corr_block(adjusted), _fit_block(adjusted)
            entry["reasons"] = adjusted.reasons
            entry["status"] = "auto"           # earlier verifications were of other numbers
        if part is not None:
            ref, layer = ref or part.ref, layer or part.layer
        if ok:
            if STATUS_RANK[status] >= STATUS_RANK.get(entry.get("status", "auto"), 0):
                entry["status"] = status
            if STATUS_RANK[entry["status"]] >= STATUS_RANK["preview-verified"]:
                entry["verdict"] = VERIFIED
            elif entry.get("verdict") in (RED, "", None):
                # a person vouched for what the fit could not decide (a switch's internal pairs): no longer red, but
                # not verified until JLC's preview or the assembled board shows it
                entry["verdict"] = YELLOW
                entry.setdefault("reasons", []).append(f"accepted manually ({by})" + (f": {note}" if note else ""))
        else:
            entry["status"], entry["verdict"] = "auto", RED
            entry.setdefault("reasons", []).append(f"verification failed ({source}, {by})" + (f": {note}" if note
                                                                                              else ""))
        entry.setdefault("verifications", []).append(
            {"by": by, "date": _today(), "source": source, "ok": bool(ok), "board": board, "ref": ref,
             "layer": layer, "presses": p if any(p.values()) else None, "note": note})
        self._save(code, doc)
        return _to_correction(entry)


def _presses(presses) -> dict:
    if presses is None:
        return {"space": 0, "right": 0, "up": 0}
    if isinstance(presses, dict):
        unknown = set(presses) - {"space", "right", "up"}
        if unknown:
            raise ValueError(f"unknown presses {sorted(unknown)}")
        return {k: int(presses.get(k, 0)) for k in ("space", "right", "up")}
    space, right, up = presses
    return {"space": int(space), "right": int(right), "up": int(up)}


def presses_to_correction(part: Part, base: Correction, *, space: int = 0, right: int = 0, up: int = 0
                          ) -> Correction:
    """The correction after pressing keys in JLC's placement preview: `space` presses (each turns the part 90 deg
    counter-clockwise as the preview shows it), `right` and `up` arrow presses (negative = left / down; each moves it
    PRESS_MM on screen).

    Derivation (Fabrication-Toolkit's offsets and its README table of which arrow fixes which offset):
    * FT adds its rotation offset to the CPL angle on both sides, positive counter-clockwise, so a space press is
      A + 90, i.e. rot + 90 (A = theta + rot on top, 180 - theta + rot on the bottom). On the bottom JLC puts the
      footprint at C + R(-A) . Mx . e; seen from below (x mirrored) that is R(A) . e, counter-clockwise in A too.
    * FT's position offset (px, py) moves the CPL point by R(theta) . (px, -py) on top and R(theta) . D . (px, -py)
      on the bottom, in the board frame: (px, py) is a library-frame vector with y up. Its table (0 deg Front: x =
      right arrow, y = up arrow; 0 deg Back: left, down; 180 Front: left, down; 180 Back: right, up; 90 either side:
      up, left; -90: down, right) holds for exactly one screen frame: the top preview is the cpl frame (x right, y
      up), the bottom preview is seen from below (x mirrored, y up).
    So a press moving the part (sigma * right, up) * PRESS_MM in the cpl frame (sigma = -1 on the bottom) moves the
    EasyEDA origin, in the library frame, by D . R(-theta) . (sigma * right, -up) * PRESS_MM; rotation about the
    row point leaves the origin where it is."""
    sigma = -1.0 if part.bottom else 1.0
    b = (sigma * right * PRESS_MM, -up * PRESS_MM)          # board frame (y down)
    lx, ly = rot(b, -part.angle)
    if part.bottom:
        ly = -ly
    note = f"{PRESS_REASON}: space {space}, right {right}, up {up}"
    return replace(base, rot=norm_angle(base.rot + 90.0 * space), dx=base.dx + lx + 0.0, dy=base.dy + ly + 0.0,
                   method="manual", reasons=[r for r in base.reasons if not r.startswith(PRESS_REASON)] + [note],
                   alternatives=[])
