"""The correction database: one JSON file per LCSC code, made to be kept in git and shared.

    <dir>/<code>.json = {"lcsc": code, "entries": [entry, ...]}
    entry = {"ee": {"key": parts.ee_key, "title"}, "kicad": {"fpid", "pad_hash": parts.kicad_hash},
             "correction": {"rot", "dx", "dy", "mirror"}, "fit": {"method", "rms", "matched", "score",
             "alternatives", "doubt"?, "polar"?}, "verdict", "reasons", "status", "verifications": [{"by", "date",
             "source", "ok", "board", "ref", "layer", "presses", "note", "correction"}], "tool", "fitted"}

Only derived numbers, hashes and the EasyEDA footprint's title (for people reading the file) are stored, never pad
geometry: EasyEDA claims copyright over its library, and the numbers are all a CPL needs. An entry is keyed by (ee
key, KiCad key): the KiCad key hashes the pads and the pin functions, so the same code on two KiCad footprints, or on
one footprint with other pin names, is two entries; a changed EasyEDA footprint (new key) makes the old entry stale.

Status, strongest first: board-verified (the assembled board was right), preview-verified (JLC's placement preview
was right), manual (typed in), auto (fitted). A verification is of the row that was uploaded: its correction
(convention.row_correction) replaces the entry's numbers when they differ. A verification can carry the keys pressed
in JLC's preview to put the part right; they move the uploaded row (presses_to_row, Fabrication-Toolkit's convention,
derived at presses_to_correction), and the correction is the moved row's. A verification records the numbers it
was of: when the latest one of the entry's numbers failed (`confirm --failed`), they were verified wrong, and the
entry stays red, through refits that give the same numbers or numbers within the check's offset limits of them (the
same placement), until other numbers replace them or a later verification finds them right (Correction.failure).
"""

from __future__ import annotations

import copy
import datetime
import json
import math
import re
from dataclasses import replace
from pathlib import Path

from .check import OFFSET_ANGLE, OFFSET_FLOOR
from .convention import norm_angle, rot
from .cpl import atomic_write
from .model import BOTTOM, RED, VERIFIED, YELLOW, Correction, CplRow, Part

TOOL = "jlcplace 0.1.0"
PRESS_MM = 0.0635                     # one arrow press in JLC's preview: 1/400 in
PRESS_REASON = "derived from presses (FT convention)"
ROW_REASON = "taken from the row"   # a correction recorded from the row that was uploaded (and checked)
STALE_REASON = "footprint changed since verified"
STATUS_RANK = {"auto": 0, "manual": 1, "preview-verified": 2, "board-verified": 3}
SOURCES = {"board": "board-verified", "preview": "preview-verified", "manual": "manual",
           "board-verified": "board-verified", "preview-verified": "preview-verified"}
_SAME_ROT, _SAME_MM = 1e-4, 1e-4      # corrections closer than this are the same correction


class DBError(ValueError):
    """A database file that cannot be read (not JSON: a merge left conflict markers in it; an entry that holds no
    correction) or written. The command line reports it as bad input."""


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


def _near(a: dict, b: dict) -> bool:
    """Within the check's offset limits of each other (check.OFFSET_FLOOR, OFFSET_ANGLE): one placement, whose row the
    check holds red when either was verified wrong."""
    da = abs((a["rot"] - b["rot"] + 180.0) % 360.0 - 180.0)
    return (da <= OFFSET_ANGLE and math.hypot(a["dx"] - b["dx"], a["dy"] - b["dy"]) <= OFFSET_FLOOR
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
    corr = Correction(rot=float(c["rot"]), dx=float(c["dx"]), dy=float(c["dy"]), mirror=bool(c.get("mirror")),
                      method=f.get("method") or "db", rms=float(f.get("rms", 0.0)), verdict=e.get("verdict", ""),
                      score=int(f.get("score", 0)), reasons=list(e.get("reasons", [])),
                      alternatives=copy.deepcopy(f.get("alternatives", [])), matched=int(f.get("matched", 0)),
                      ee_key=_keys(e)[0], kicad_hash=_keys(e)[1], status=status or e.get("status", "auto"),
                      doubt=str(f.get("doubt") or ""), polar=None if f.get("polar") is None else bool(f["polar"]),
                      verifications=_of_numbers(e))
    if corr.failure is not None:
        corr.verdict = RED                 # verified wrong: nothing about these numbers makes them right again
    return corr


def _failed_reason(v: dict) -> str:
    """The reason confirm adds to an entry when a verification fails."""
    note = v.get("note") or ""
    return f"verification failed ({v.get('source', '')}, {v.get('by', '')})" + (f": {note}" if note else "")


def _of_numbers(e: dict) -> list[dict]:
    """The entry's verifications of the numbers it holds now, in order (copies). A verification records the numbers
    it was of ("correction"); one recorded before verifications held them counts as of these, a failed one only while
    the entry still gives its reason (a refit or a verification with other numbers replaced the reasons)."""
    out = []
    for v in e.get("verifications") or []:
        if not isinstance(v, dict):
            continue
        c = v.get("correction")
        if c is None:
            mine = v.get("ok", True) is not False or _failed_reason(v) in (e.get("reasons") or [])
        else:
            try:
                mine = _same(c, e["correction"])
            except (KeyError, TypeError, ValueError, AttributeError):
                mine = False               # a hand-edited record without numbers it could be of
        if mine:
            out.append(copy.deepcopy(v))
    return out


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
        """The file's document; DBError when it is not JSON or an entry holds no correction (every entry is read
        here, so no caller meets a broken one later)."""
        f = self.file(code)
        if not f.exists():
            return {"lcsc": _code(code), "entries": []}
        try:
            with open(f, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise DBError(f"{f}: not a valid jlcplace correction file ({e})") from None
        if not isinstance(doc, dict) or not isinstance(doc.get("entries"), list):
            raise DBError(f"{f}: not a valid jlcplace correction file (no list of entries)")
        for i, e in enumerate(doc["entries"]):
            try:
                _to_correction(e)
            except (KeyError, TypeError, ValueError, AttributeError) as err:
                raise DBError(f"{f}: not a valid jlcplace correction file (entry {i + 1}: {type(err).__name__}: "
                              f"{err})") from None
        return doc

    def _save(self, code: str, doc: dict) -> None:
        try:
            self.path.mkdir(parents=True, exist_ok=True)
            atomic_write(self.file(code), json.dumps(doc, sort_keys=True, indent=1, ensure_ascii=False) + "\n")
        except OSError as e:
            raise DBError(f"{self.file(code)}: cannot write: {e.strerror or e}") from None

    def entries(self, code: str) -> list[dict]:
        """The stored entries for a code (copies), [] when there is no file."""
        return copy.deepcopy(self._load(code)["entries"])

    def lookup(self, code: str, *, ee_key: str = "", kicad_hash: str = "") -> Correction | None:
        """The correction for a code on a KiCad footprint (pad hash) and EasyEDA footprint (key); "" matches any.
        Among matching entries the strongest status wins (board-verified > preview-verified > manual > auto), then
        the later entry, with its verifications of its numbers (red when the latest one failed). When entries exist
        for the pad hash but none for this ee key, the strongest is returned stale: status "stale", verdict red,
        reason STALE_REASON (the caller refits and compares). None when no entry is for this KiCad footprint."""
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
        refreshes the fit and keeps the status and verifications (and a failed verification of those numbers, with
        its red verdict and reason); so does a fit within the check's offset limits of numbers verified wrong (_near:
        the same placement). Different numbers replace the entry's correction, but never one with a stronger status
        (ValueError: confirm or edit the file instead)."""
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
            if corr.status == "auto" and _to_correction(old).failure is not None \
                    and _near(old["correction"], new["correction"]):
                # a refit of practically the numbers verified wrong (an uploaded row's, a micron off): the check holds
                # that placement red, and so does the entry; typed-in numbers or a confirm replace them
                new["correction"] = dict(old["correction"])
            if _same(old["correction"], new["correction"]):
                new["status"] = old["status"] if rank_old > rank_new else corr.status
                if STATUS_RANK.get(new["status"], 0) >= STATUS_RANK["preview-verified"]:
                    new["verdict"], new["reasons"] = old.get("verdict", VERIFIED), old.get("reasons", [])
                elif _to_correction(old).failure is not None:
                    # these numbers were verified wrong: a refit that gives them again does not make them right
                    new["verdict"] = RED
                    new["reasons"] += [r for r in old.get("reasons", []) if r.startswith("verification failed")
                                       and r not in new["reasons"]]
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
                part: Part | None = None, correction: Correction | None = None) -> Correction:
        """Record a verification of the entry (ee_key, kicad_hash) and return its correction as now stored.

        source: "board" (the assembled board), "preview" (JLC's placement preview) or "manual". ok=True raises the
        status to the source's (verdict VERIFIED for board/preview; a red entry confirmed manually becomes yellow).
        ok=False marks the entry red and drops it to "auto": its numbers were verified wrong (Correction.failure)
        until other numbers replace them or a later verification of them is ok. Each verification records the
        numbers it was of ("correction").
        `correction`: the correction of the row that was checked (convention.row_correction of the uploaded row,
        moved by the presses): when its numbers differ from the entry's, it replaces them (its reasons and verdict
        with them) and carries only this verification's status. A verified entry is not replaced by a verification
        of other numbers that is no stronger (ValueError: record the failed verification of its own row first).
        presses = {"space": n, "right": n, "up": n} (or a 3-tuple), the keys pressed in JLC's preview to make the
        part right, are recorded with the verification; they are relative to the uploaded row, so they come with
        that row's `correction` (never folded into the stored numbers, which need not be what was uploaded)."""
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
            if correction is None:
                raise ValueError("presses are relative to the row that was uploaded: pass that row's correction with "
                                 "the presses applied (convention.row_correction of presses_to_row)")
        if correction is not None:
            if not ok:
                raise ValueError("a failed verification records no numbers")
            new = _corr_block(correction)
            if not _same(entry["correction"], new):
                rank = STATUS_RANK.get(entry.get("status", "auto"), 0)
                if rank >= STATUS_RANK["preview-verified"] and rank >= STATUS_RANK[status]:
                    raise ValueError(f"{code}: the entry is {entry['status']} with other numbers "
                                     f"({_describe(entry['correction'])}): record the failed verification of its own "
                                     f"row first")
                entry["correction"], entry["fit"] = new, _fit_block(correction)
                entry["reasons"], entry["verdict"] = list(correction.reasons), correction.verdict or YELLOW
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
             "layer": layer, "presses": p if any(p.values()) else None, "note": note,
             "correction": dict(entry["correction"])})      # the numbers it was of: other numbers are not
        self._save(code, doc)
        return _to_correction(entry)


def _describe(c: dict) -> str:
    return f"rot {c['rot']:g}, dx {c['dx']:.4f}, dy {c['dy']:.4f}" + (", mirrored" if c.get("mirror") else "")


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


def presses_to_row(row: CplRow, *, space: int = 0, right: int = 0, up: int = 0) -> CplRow:
    """The row as JLC's preview shows it after the presses: each space press adds 90 deg to the CPL rotation (FT adds
    its rotation offset to the CPL angle on both sides), each arrow press moves the part PRESS_MM on the preview's
    screen, the cpl frame on the top and the cpl frame seen from below (x mirrored) on the bottom. The derivation is
    at presses_to_correction; row_correction of the moved row is the correction the presses made right."""
    sigma = -1.0 if row.layer == BOTTOM else 1.0
    return replace(row, x=round(row.x + sigma * right * PRESS_MM, 6) + 0.0, y=round(row.y + up * PRESS_MM, 6) + 0.0,
                   rotation=norm_angle(row.rotation + 90.0 * space))


def presses_to_correction(part: Part, base: Correction, *, space: int = 0, right: int = 0, up: int = 0
                          ) -> Correction:
    """The correction after pressing keys in JLC's placement preview: `space` presses (each turns the part 90 deg
    counter-clockwise as the preview shows it), `right` and `up` arrow presses (negative = left / down; each moves it
    PRESS_MM on screen), under the jlc-2022 reading. `base` must be the correction of the row that was uploaded
    (the presses are relative to what the preview showed): the command line takes it from that row instead
    (presses_to_row, convention.row_correction), which also holds under the other readings.

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
