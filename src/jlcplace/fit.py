"""Work out the correction for one part, and how far to trust it.

The correction is the rigid map from the EasyEDA footprint (ee frame) to the KiCad one (library frame), as in
jlcplace.model:

    library = R(rot) . M . ee + (dx, dy),    M = diag(-1, 1) if mirror else the identity

Everything here happens in the library frame, on the part's soldered copper only (`Part.copper_pads()`). KiCad pads
drawn on top of each other (USB-C A1/B12, the same land twice) are merged into one *land*; an EasyEDA pad *lands*
when its centre is inside a land's copper (`geom.pad_contains`).

Evidence
--------
numbers   The number fit: Kabsch, normal and mirrored, over EasyEDA pad <-> KiCad land pairs of the same number.
          Numbers that occur once on each side give the pairs; a number repeated on one side only (SOT-223's pin 2
          and tab 2, MP x2 against one MP) has no defined partner and is left out; numbers repeated equally often on
          both sides are paired by position once the others have fixed the map. With fewer than two such pairs,
          centroids per common number; when that is degenerate too (a holder numbered 1, 2, 2), the one placement
          that puts every numbered pad on its own number. An EasyEDA number that concatenates a merged land's
          numbers ("A1B12" on A1 + B12) names that land. Also rot 0/90/180/270 with the mean translation.
geometry  Numbers ignored: rotations 0/90/180/270 and those aligning the farthest-apart pad pairs, normal and
          mirrored; translations voted by how many pads they land (anchored on distinctive EasyEDA pads, so extra or
          missing pads do not pull them), refined 3x by assignment (geom.assign; cost = centre distance + size
          mismatch) and Kabsch. Every transform under which all EasyEDA pads land is kept, merged with the others
          that land each pad on the same land: the members of S (`Correction.alternatives` lists the others).
names     EasyEDA symbol pin names vs KiCad pin functions of the lands each pad lands on (normalised, synonyms).
          Names count only where they separate members of S: libraries that name pins differently under every
          placement say nothing about the placement.
nets      Two members are equivalent when every EasyEDA pad lands on the same nets (a land without a net is its
          own net). Swapping the two terminals of a non-polar two-terminal part (R, C, L, a switch numbered 1,1,2,2)
          is harmless too. Anything else that changes a pad's net is a different placement.

Decision (Correction.verdict)
-----------------------------
red      nothing lands every EasyEDA pad (the best partial transform is returned); only a mirror image lands, or the
         mirrored fit is better supported by numbers and names than every normal one and no normal one lands on the
         same nets, or as well supported and far more exact (mirror=True: never placed); numbering conflicts (a pad
         on another-numbered land of another net, its own number existing in KiCad) that no pin names vouch for; or
         several non-equivalent members that neither numbers nor names separate ("ambiguous").
yellow   names pick a member the numbers do not (the names win: in JLC's preview data they were right 5 times of 6);
         numbering differs or does not overlap but the members of S are equivalent, or the names pick one; the
         number fit missing or leaving pads off copper while a fit consistent with the numbering lands them; a polar
         two-terminal part whose polarity only the pad numbers decide; pin names that differ; rms > 0.1 mm; < 80 %
         of the numbered EasyEDA pads matched by number; an angle that is not a multiple of 90 (kept unsnapped);
         KiCad lands that no EasyEDA pad lands on; another placement that fits as well on the same numbers and nets
         (the pads cannot orient the body).
green    the number fit lands every pad without numbering conflicts, names agree or are uninformative, and every
         other member of S is equivalent or ruled out by the numbers (for a polar two-terminal part, by the names
         as well).

Angles within `snap` degrees of a multiple of 90 are snapped and the translation refitted for the snapped angle.
"""

from __future__ import annotations

import itertools
import math
import re
from dataclasses import dataclass, field, replace

import numpy as np

from . import geom
from .convention import ee_to_library, norm_angle, rot
from .model import BOTTOM, GREEN, RED, YELLOW, Correction, EEFootprint, Pad, PadLanding, Part
from .parts import NONPOLAR_REFS, POLAR_FOOTPRINT, ee_key, norm_number, ref_prefix

MERGE_TOL = 0.01          # mm: KiCad pads with centres this close are one land
RMS_YELLOW = 0.1          # mm
MATCHED_YELLOW = 0.8      # fraction of the EasyEDA pads matched by number
BOX_MARGIN = 0.05         # mm: bounding-box prefilter before the exact pad test
MAX_REFINE = 24           # geometric hypotheses refined per part ...
PARTIAL_REFINE = 4        # ... of them missing pads, when others land them all
ASSIGN_MAX = 2500         # cost-matrix cells up to which refinement pairs by Hungarian assignment
SAMPLE = 64               # pads that vote on translation hypotheses
MIN_SPREAD = 0.05         # mm: a pair set narrower than this fixes no angle
TWIN_RMS = 0.05           # mm: placements fitting this much alike are the same pattern turned

_POLAR_EE_TITLE = re.compile(r"led|sod|diode|tant|elec", re.I)
_POLARITY_NAMES = frozenset({"ANODE", "CATHODE"})

_SYNONYMS = {
    "EP": "GND", "PAD": "GND", "EPAD": "GND", "TAB": "GND", "THERMAL": "GND", "THERMALPAD": "GND", "VSS": "GND",
    "DP": "D+", "DM": "D-", "DN": "D-",
    "SHIELD": "SHELL", "SH": "SHELL", "EH": "SHELL", "MH": "SHELL",
    "A": "ANODE", "+": "ANODE", "K": "CATHODE", "C": "CATHODE", "-": "CATHODE",
    "VCC": "VDD",
}
_UNINFORMATIVE = re.compile(r"(PIN)?\d*|NC|DNC|N\.C\.?")


# ------------------------------------------------------------------------------------------------------- names

def name_tokens(name: str, number: str = "") -> frozenset[str]:
    """Canonical tokens of a pin name: upper case without ~{} # * ! _ and spaces, split at '/', ',' and parentheses
    (alternate functions; '/RESET' is active low), 'nRESET' -> RESET, synonyms applied. A name that only repeats its
    pad number, digits, PINn or NC is uninformative: the empty set."""
    s = (name or "").replace("~{", "").replace("{", "").replace("}", "")
    out = set()
    for t in re.split(r"[/,()\s]+", s):
        t = t.strip("~#*!")
        if len(t) > 1 and t[0] == "n" and t[1].isupper():
            t = t[1:]
        t = t.upper().replace("_", "")
        if _UNINFORMATIVE.fullmatch(t) or t == norm_number(number):
            continue
        out.add(_SYNONYMS.get(t, t))
    return frozenset(out)


def compare_names(a: frozenset, b: frozenset) -> int:
    """+1 agree, -1 disagree, 0 unknown: a side uninformative, or equal only up to a trailing index (CC1 vs CC2,
    DP1 vs D+), which is how libraries differ on symmetric connectors."""
    if not a or not b:
        return 0
    if a & b:
        return 1

    def stem(ts):
        return {_SYNONYMS.get(u, u) for u in (re.sub(r"\d+$", "", t) for t in ts) if u}

    return 0 if stem(a) & stem(b) else -1


# ------------------------------------------------------------------------------------------------------- lands

@dataclass
class _Land:
    """KiCad copper pads merged where they coincide (library frame)."""
    pads: list[Pad]
    x: float
    y: float
    numbers: frozenset[str]
    net: object                      # the set of its nets, or ("land", index) when it has none
    names: frozenset[str]
    box: tuple[float, float, float, float]


def _lands(pads: list[Pad]) -> list[_Land]:
    groups: list[list[Pad]] = []
    for p in pads:
        g = next((g for g in groups if math.hypot(g[0].x - p.x, g[0].y - p.y) <= MERGE_TOL), None)
        if g is None:
            groups.append([p])
        else:
            g.append(p)
    lands = []
    for k, g in enumerate(groups):
        pts = [q for p in g for q in geom.pad_outline(p)]
        nets = frozenset(p.net for p in g if p.net)
        lands.append(_Land(pads=g, x=sum(p.x for p in g) / len(g), y=sum(p.y for p in g) / len(g),
                           numbers=frozenset(norm_number(p.number) for p in g if p.number),
                           net=nets if nets else ("land", k),
                           names=frozenset().union(*(name_tokens(p.pin_name, p.number) for p in g)),
                           box=(min(q[0] for q in pts), min(q[1] for q in pts),
                                max(q[0] for q in pts), max(q[1] for q in pts))))
    return lands


def _extent(w: float, h: float, a: float):
    """Axis-aligned extent of a w x h pad turned by a."""
    c, s = abs(math.cos(math.radians(a))), abs(math.sin(math.radians(a)))
    return w * c + h * s, w * s + h * c


def _spread(pts) -> float:
    P = np.asarray(pts, float).reshape(-1, 2)
    return float(np.ptp(P, 0).max()) if len(P) else 0.0


# ------------------------------------------------------------------------------------------------------- transforms

@dataclass(frozen=True)
class _Tf:
    """library = R(a) . M . ee + (tx, ty)"""
    a: float
    tx: float
    ty: float
    mirror: bool = False
    rms: float = 0.0


@dataclass
class _Member:
    """A class of transforms that land every EasyEDA pad on the same lands."""
    tf: _Tf
    land: tuple[int, ...]
    agree: int = 0                   # EasyEDA pads on a land with their own number
    conflicts: list[str] = field(default_factory=list)   # "EE 5 -> KiCad 6": own number elsewhere, other net
    names_ok: int = 0
    names_bad: int = 0
    is_nf: bool = False              # its transform is the number fit

    @property
    def mirror(self) -> bool:
        return self.tf.mirror

    @property
    def name_score(self) -> int:
        return self.names_ok - self.names_bad

    @property
    def evidence(self):
        return self.agree - len(self.conflicts), self.name_score


def _snap_angle(a: float, snap: float) -> float:
    k = round(a / 90.0)
    return norm_angle(k * 90.0) if abs(a - k * 90.0) <= snap else norm_angle(a)


def _fixed_angle_fit(src, dst, a: float, mirror: bool) -> _Tf:
    """Least-squares translation for a fixed angle: the mean of dst - R(a) . M . src."""
    S = np.asarray(src, float).reshape(-1, 2).copy()
    if mirror:
        S[:, 0] = -S[:, 0]
    d = np.asarray(dst, float).reshape(-1, 2) - np.array([rot(p, a) for p in S])
    t = d.mean(0)
    return _Tf(norm_angle(a), float(t[0]), float(t[1]), mirror, float(np.sqrt(((d - t) ** 2).sum(1).mean())))


def _fit_pairs(src, dst, mirror: bool, snap: float) -> _Tf:
    """Kabsch; an angle within `snap` of a multiple of 90 is snapped and the translation refitted for it."""
    a, tx, ty, rms = geom.kabsch(src, dst, mirror)
    s = _snap_angle(a, snap)
    if s != norm_angle(a):
        return _fixed_angle_fit(src, dst, s, mirror)
    return _Tf(norm_angle(a), float(tx), float(ty), mirror, rms)


class _Fit:
    """One part against one EasyEDA footprint."""

    def __init__(self, part: Part, ee: EEFootprint, snap: float):
        self.part, self.ee, self.snap = part, ee, snap
        self.lands = _lands(part.copper_pads())
        self.E = np.array([(p.x, p.y) for p in ee.pads], float).reshape(-1, 2)
        self.K = np.array([(ld.x, ld.y) for ld in self.lands], float).reshape(-1, 2)
        self.box = np.array([ld.box for ld in self.lands], float).reshape(-1, 4)
        self.ksize = np.column_stack((self.box[:, 2] - self.box[:, 0], self.box[:, 3] - self.box[:, 1]))
        self.enum = [norm_number(p.number) for p in ee.pads]
        names = {norm_number(k): v for k, v in (ee.pin_names or {}).items()}
        self.etok = [name_tokens(names.get(n, ""), n) for n in self.enum]
        self.by_num: dict[str, list[int]] = {}
        for j, ld in enumerate(self.lands):
            for n in ld.numbers:
                self.by_num.setdefault(n, []).append(j)
        for n in set(self.enum) - set(self.by_num):
            hit = [j for j, ld in enumerate(self.lands) if 1 < len(ld.numbers) <= 4
                   and any("".join(p) == n for p in itertools.permutations(sorted(ld.numbers)))]
            if len(hit) == 1:
                self.by_num[n] = hit
        self.terminals = sorted({n for ld in self.lands for n in ld.numbers})
        self._cache: dict[tuple, tuple[int, ...]] = {}
        self._anchor_list: list[int] | None = None

    # -------------------------------------------------------------------------------------------- mapping, landing

    def map(self, tf: _Tf) -> np.ndarray:
        X = self.E.copy()
        if tf.mirror:
            X[:, 0] = -X[:, 0]
        r = math.radians(tf.a)
        c, s = math.cos(r), math.sin(r)
        return np.column_stack((X[:, 0] * c + X[:, 1] * s + tf.tx, -X[:, 0] * s + X[:, 1] * c + tf.ty))

    def _inbox(self, P: np.ndarray) -> np.ndarray:
        b = self.box
        return ((P[..., None, 0] >= b[:, 0] - BOX_MARGIN) & (P[..., None, 0] <= b[:, 2] + BOX_MARGIN)
                & (P[..., None, 1] >= b[:, 1] - BOX_MARGIN) & (P[..., None, 1] <= b[:, 3] + BOX_MARGIN))

    def landing(self, tf: _Tf) -> tuple[int, ...]:
        """The land each EasyEDA pad's centre is on (-1 = off copper). Where lands overlap, the one with the pad's own
        number, else the nearest centre."""
        key = (round(tf.a, 6), round(tf.tx, 6), round(tf.ty, 6), tf.mirror)
        if key not in self._cache:
            P = self.map(tf)
            inb = self._inbox(P)
            out = []
            for i, q in enumerate(P):
                hits = [int(j) for j in np.nonzero(inb[i])[0]
                        if any(geom.pad_contains(p, (q[0], q[1])) for p in self.lands[j].pads)]
                own = [j for j in hits if j in self.by_num.get(self.enum[i], ())]
                hits = own or hits
                out.append(min(hits, key=lambda j: math.dist(q, self.K[j])) if hits else -1)
            self._cache[key] = tuple(out)
        return self._cache[key]

    def member(self, tf: _Tf, land) -> _Member:
        m = _Member(tf=tf, land=tuple(land))
        for i, j in enumerate(land):
            if j < 0:
                continue
            own = self.by_num.get(self.enum[i], ())
            if j in own:
                m.agree += 1
            elif own and self.lands[j].net not in {self.lands[k].net for k in own}:
                m.conflicts.append(f"EE {self.ee.pads[i].number} -> KiCad {self.label(j)}")
            c = compare_names(self.etok[i], self.lands[j].names)
            m.names_ok += c > 0
            m.names_bad += c < 0
        return m

    def label(self, j: int) -> str:
        return "/".join(sorted(self.lands[j].numbers)) or f"(unnumbered at {self.K[j][0]:.3f}, {self.K[j][1]:.3f})"

    def same_nets(self, a: _Member, b: _Member) -> bool:
        return all(self.lands[i].net == self.lands[j].net for i, j in zip(a.land, b.land))

    # -------------------------------------------------------------------------------------------- number fit

    def number_pairs(self, tf: _Tf | None = None):
        """(EasyEDA points, KiCad points) for the number fit; see the module docstring. With tf, numbers repeated
        equally often on both sides are added, paired by position under tf."""
        ee_by: dict[str, list[int]] = {}
        for i, n in enumerate(self.enum):
            if n:
                ee_by.setdefault(n, []).append(i)
        common = sorted(n for n in ee_by if n in self.by_num)
        once = [n for n in common if len(ee_by[n]) == 1 and len(self.by_num[n]) == 1]
        src = [self.E[ee_by[n][0]] for n in once]
        dst = [self.K[self.by_num[n][0]] for n in once]
        if tf is not None:
            P = self.map(tf)
            for n in common:
                ii, jj = ee_by[n], self.by_num[n]
                if len(ii) == len(jj) > 1:
                    for a, b in geom.assign([[math.dist(P[i], self.K[j]) for j in jj] for i in ii]):
                        src.append(self.E[ii[a]])
                        dst.append(self.K[jj[b]])
            return src, dst
        if len(src) >= 2 and _spread(src) > MIN_SPREAD and _spread(dst) > MIN_SPREAD:
            return src, dst
        src = [self.E[ee_by[n]].mean(0) for n in common]
        dst = [self.K[self.by_num[n]].mean(0) for n in common]
        ok = len(src) >= 2 and _spread(src) > MIN_SPREAD and _spread(dst) > MIN_SPREAD
        return (src, dst) if ok else ([], [])

    def number_fit(self, mirror: bool) -> _Tf | None:
        src, dst = self.number_pairs()
        if not src:
            return None
        tf = _fit_pairs(src, dst, mirror, self.snap)
        src2, dst2 = self.number_pairs(tf)
        if len(src2) > len(src) and _spread(src2) > MIN_SPREAD:
            tf = _fit_pairs(src2, dst2, mirror, self.snap)
        return tf

    def fixed_rotations(self) -> list[_Tf]:
        src, dst = self.number_pairs()
        return [_fixed_angle_fit(src, dst, a, False) for a in (0.0, 90.0, 180.0, 270.0)] if src else []

    # -------------------------------------------------------------------------------------------- geometric fit

    def _angles(self, mirror: bool) -> list[float]:
        angles = [0.0, 90.0, 180.0, 270.0]

        def far_pairs(P):
            if len(P) < 2:
                return []
            D = np.linalg.norm(P[:, None] - P[None], axis=2)
            iu = np.triu_indices(len(P), 1)
            d = D[iu]
            order = np.argsort(-d, kind="stable")[:12]
            return [(int(iu[0][k]), int(iu[1][k])) for k in order if d[k] >= 0.98 * d[order[0]]][:3]

        for (p, q), (r, s) in itertools.product(far_pairs(self.E), far_pairs(self.K)):
            for dst in ((self.K[r], self.K[s]), (self.K[s], self.K[r])):
                a = geom.kabsch([self.E[p], self.E[q]], list(dst), mirror)[0]
                if all(min(abs(a - b) % 360, 360 - abs(a - b) % 360) > 0.01 for b in angles):
                    angles.append(a)
        return angles

    def _anchors(self) -> list[int]:
        """EasyEDA pads to anchor translations on: the most distinctive size, the farthest out, the largest."""
        if self._anchor_list is None:
            size = np.array([sorted((p.w, p.h)) for p in self.ee.pads], float).reshape(-1, 2)
            similar = (np.abs(size[:, None] - size[None]) <= 0.1 * np.maximum(size[:, None], 1e-3)).all(2).sum(1)
            area = size[:, 0] * size[:, 1]
            far = np.linalg.norm(self.E - self.E.mean(0), axis=1)
            out: list[int] = []
            for k in (int(np.lexsort((-area, similar))[0]), int(np.argmax(far)), int(np.argmax(area))):
                if k not in out:
                    out.append(k)
            self._anchor_list = out
        return self._anchor_list

    def _size_cost(self, tf: _Tf) -> np.ndarray:
        ext = np.array([_extent(p.w, p.h, (-(p.angle or 0.0) if tf.mirror else (p.angle or 0.0)) + tf.a)
                        for p in self.ee.pads], float).reshape(-1, 2)
        return 0.5 * np.abs(ext[:, None] - self.ksize[None]).sum(2)

    def _refine(self, tf: _Tf) -> _Tf:
        gate = np.maximum(0.5 * np.hypot(self.ksize[:, 0], self.ksize[:, 1]), 0.2)
        pairs: list[tuple[int, int]] = []
        for _ in range(3):
            P = self.map(tf)
            D = np.linalg.norm(P[:, None] - self.K[None], axis=2)
            C = D + self._size_cost(tf)
            near = C.argmin(1).tolist()
            if len(set(near)) == len(near):      # row minima in distinct columns already are the optimal assignment
                new = list(enumerate(near))
            elif C.size <= ASSIGN_MAX:
                new = geom.assign(C)
            else:                                # large and still off: greedy is enough, the next Kabsch corrects it
                new, ui, uj = [], set(), set()
                for k in np.argsort(C, axis=None, kind="stable"):
                    i, j = divmod(int(k), C.shape[1])
                    if i not in ui and j not in uj:
                        new.append((i, j))
                        ui.add(i)
                        uj.add(j)
                        if len(new) == min(C.shape):
                            break
                new.sort()
            new = [(i, j) for i, j in new if D[i, j] <= gate[j]]
            if not new or new == pairs:
                break
            pairs = new
            src, dst = self.E[[i for i, _ in pairs]], self.K[[j for _, j in pairs]]
            if len(pairs) >= 2 and _spread(src) > MIN_SPREAD:
                a, tx, ty, rms = geom.kabsch(src, dst, tf.mirror)
            else:
                a, (tx, ty), rms = tf.a, dst[0] - np.array(rot(((-1 if tf.mirror else 1) * src[0][0], src[0][1]),
                                                               tf.a)), 0.0
            tf = _Tf(norm_angle(a), float(tx), float(ty), tf.mirror, float(rms))
        if len(pairs) >= 2 and _spread(self.E[[i for i, _ in pairs]]) > MIN_SPREAD:
            return _fit_pairs(self.E[[i for i, _ in pairs]], self.K[[j for _, j in pairs]], tf.mirror, self.snap)
        return _Tf(_snap_angle(tf.a, self.snap), tf.tx, tf.ty, tf.mirror, tf.rms)

    def geometric(self) -> list[_Tf]:
        """Refined transforms: every distinct hypothesis that puts all pads in a land's box, and the best few that
        miss some (land patterns that differ can need the refinement to land them all)."""
        n, m = len(self.E), len(self.K)
        # votes are counted on a fixed sample of the pads (all of them up to SAMPLE): the exact test comes later
        sample = sorted(set(np.linspace(0, n - 1, min(n, SAMPLE)).astype(int).tolist()) | set(self._anchors()))
        ns = len(sample)
        hyps = []                        # (sampled pads landed, transform)
        step = max(1, int(2e6 // (ns * m)))
        for mirror in (False, True):
            for a in self._angles(mirror):
                base = self.map(_Tf(a, 0.0, 0.0, mirror))
                T = [self.K.mean(0) - base.mean(0),
                     (self.K.min(0) + self.K.max(0)) / 2 - (base.min(0) + base.max(0)) / 2]
                T = np.array(T + [self.K[j] - base[i] for i in self._anchors() for j in range(m)])
                for k in range(0, len(T), step):
                    landed = self._inbox(base[sample][None] + T[k:k + step, None]).any(2).sum(1)
                    hyps += [(int(landed[u]), _Tf(a, float(T[k + u][0]), float(T[k + u][1]), mirror))
                             for u in range(len(landed))]
        best = max(h[0] for h in hyps)
        keep = [h for h in hyps if h[0] >= best - max(1, ns // 10)]

        def near(tf):                    # tie-break: mean distance to the nearest land centre
            return float(np.linalg.norm(self.map(tf)[:, None] - self.K[None], axis=2).min(1).mean())

        picked: list[_Tf] = []
        partial = 0
        for landed, tf in sorted(keep, key=lambda h: (-h[0], near(h[1]))):
            if any(p.mirror == tf.mirror and abs(p.a - tf.a) < 0.5 and math.hypot(p.tx - tf.tx, p.ty - tf.ty) < 0.05
                   for p in picked):
                continue
            if landed < ns:
                if partial >= (PARTIAL_REFINE if best == ns else MAX_REFINE):
                    continue
                partial += 1
            picked.append(tf)
            if len(picked) >= MAX_REFINE + PARTIAL_REFINE:
                break
        return [self._refine(tf) for tf in picked]

    def canonical(self, tf: _Tf, land) -> _Tf:
        """The transform that best fits a landing map: Kabsch over every landed pad (EasyEDA centre -> land centre),
        kept only if it lands the pads the same way."""
        ii = [i for i, j in enumerate(land) if j >= 0]
        if len(ii) < 2 or _spread(self.E[ii]) <= MIN_SPREAD:
            return tf
        c = _fit_pairs(self.E[ii], self.K[[land[i] for i in ii]], tf.mirror, self.snap)
        return c if self.landing(c) == tuple(land) else tf

    # -------------------------------------------------------------------------------------------- part class

    def nonpolar(self) -> bool:
        """A two-terminal part whose terminals may be swapped. Unknown parts count as polar."""
        if len(self.terminals) != 2 or ref_prefix(self.part.ref) not in NONPOLAR_REFS:
            return False
        if POLAR_FOOTPRINT.search(self.part.footprint or "") or _POLAR_EE_TITLE.search(self.ee.title or ""):
            return False
        if any(ld.names & _POLARITY_NAMES for ld in self.lands):
            return False
        return not (len(set(self.enum)) == 2 and any(t & _POLARITY_NAMES for t in self.etok))

    def fixes_rotation(self, land) -> bool:
        """Do the lands the pads are on pin the rotation down (two distinct lands, apart)?"""
        js = sorted({j for j in land if j >= 0})
        return len(js) >= 2 and _spread(self.K[js]) > MIN_SPREAD

    def uncovered(self, land) -> list[str]:
        """KiCad lands no EasyEDA pad lands on, unless they sit on a covered land (thermal vias in an exposed pad)."""
        hit = {j for j in land if j >= 0}
        return [self.label(j) for j, ld in enumerate(self.lands) if j not in hit
                and not any(geom.pad_contains(p, (ld.x, ld.y)) for k in hit for p in self.lands[k].pads)]


# ------------------------------------------------------------------------------------------------------- decision

def _rank(m: _Member):
    """Best supported first; among equals the smallest rotation, so ties resolve deterministically."""
    return -m.evidence[0], -m.evidence[1], round(m.tf.rms, 3), min(m.tf.a, 360.0 - m.tf.a), m.tf.a


def _deg(m: _Member) -> str:
    return f"{norm_angle(m.tf.a):g}" + (" mirrored" if m.mirror else "")


def fit_part(part: Part, ee: EEFootprint, *, snap: float = 0.5) -> Correction:
    """The correction library = R(rot) . M . ee + (dx, dy) for a part and its EasyEDA footprint, with the verdict,
    reasons and the other transforms that land every pad. The rules are in the module docstring."""
    corr = Correction(rot=0.0, ee_key=ee_key(ee), kicad_hash=geom.pad_hash(part.copper_pads()))
    if any(p.layer == BOTTOM for p in ee.pads):
        # pads on EasyEDA's bottom layer are on the far side of the part and never meet its copper (as in check)
        ee = replace(ee, pads=[p for p in ee.pads if p.layer != BOTTOM])
    if not ee.pads or not part.copper_pads():
        corr.verdict, corr.method = RED, "none"
        corr.reasons.append("the EasyEDA footprint has no pads" if not ee.pads else
                            "the KiCad footprint has no soldered copper pads")
        return corr
    f = _Fit(part, ee, snap)
    nf, nfm = f.number_fit(False), f.number_fit(True)
    members: dict[tuple, _Member] = {}
    partial: list[tuple[int, float, _Tf]] = []
    for tf in [t for t in (nf, nfm) if t] + f.fixed_rotations() + f.geometric():
        land = f.landing(tf)
        if -1 in land:
            partial.append((sum(j >= 0 for j in land), tf.rms, tf))
            continue
        is_nf = tf is nf or tf is nfm
        key = (tf.mirror, land)
        if key not in members:
            members[key] = f.member(tf if is_nf else f.canonical(tf, land), land)
        if is_nf and not members[key].is_nf:
            members[key].tf, members[key].is_nf = tf, True
    normal = [m for m in members.values() if not m.mirror]
    mirrored = [m for m in members.values() if m.mirror]
    numbered = sum(1 for n in f.enum if n) or len(ee.pads)     # an unnumbered (mechanical) pad cannot match
    if not any(m.is_nf for m in normal):
        # numbers repeated symmetrically (1, 2, 2) leave the pair fit degenerate; the one placement that puts every
        # numbered pad on its own number is the number fit all the same
        full = [m for m in normal if m.agree == numbered and not m.conflicts and f.fixes_rotation(m.land)]
        if len(full) == 1:
            full[0].is_nf = True
    nonpolar = f.nonpolar()
    reasons = corr.reasons

    def harmless(m: _Member, ref: _Member) -> bool:
        """Same nets on every pad, or a terminal swap of a non-polar two-terminal part."""
        if f.same_nets(m, ref):
            return True
        if not nonpolar:
            return False
        a, b = (frozenset({t}) for t in f.terminals)
        swap = {a: b, b: a}
        return all(f.lands[i].numbers == f.lands[j].numbers or swap.get(f.lands[i].numbers) == f.lands[j].numbers
                   for i, j in zip(m.land, ref.land))

    def names_pick(pool: list[_Member]) -> _Member | None:
        """The member the pin names single out (ties among equivalent members allowed), or None."""
        if not any(m.names_ok or m.names_bad for m in pool):
            return None
        top = max(m.name_score for m in pool)
        best = [m for m in pool if m.name_score == top]
        if top <= 0 or any(not harmless(b, best[0]) for b in best[1:]):
            return None
        return min(best, key=_rank)

    def take(tf: _Tf, method: str):
        corr.rot, corr.mirror, corr.method = norm_angle(tf.a), tf.mirror, method
        corr.dx, corr.dy, corr.rms = round(tf.tx, 4) + 0.0, round(tf.ty, 4) + 0.0, round(tf.rms, 4)

    def describe(m: _Member, ref: _Member) -> dict:
        changed = [ee.pads[i].number for i, (a, b) in enumerate(zip(m.land, ref.land))
                   if f.lands[a].net != f.lands[b].net]
        return {"rot": norm_angle(m.tf.a), "dx": round(m.tf.tx, 4) + 0.0, "dy": round(m.tf.ty, 4) + 0.0,
                "mirror": m.mirror, "matched": m.agree, "conflicts": len(m.conflicts), "names": m.name_score,
                "equivalent": harmless(m, ref), "nets_changed": changed[:16]}

    # ---- nothing lands every pad
    if not members:
        # the transform that lands the most pads; the number fit among equals
        best = max(partial, key=lambda p: (p[0], p[2] is nf, -p[1]))[2] if partial else _Tf(0.0, 0.0, 0.0)
        take(best, "number" if best is nf else "geometry")
        land = f.landing(best)
        off = [ee.pads[i].number or f"#{i}" for i, j in enumerate(land) if j < 0]
        reasons.append(f"no placement puts every EasyEDA pad on KiCad copper; under the best one {len(off)} of "
                       f"{len(land)} are off copper: {', '.join(off[:16])}")
        corr.verdict, corr.matched = RED, f.member(best, land).agree
        corr.score = int(40 * (len(land) - len(off)) / len(land))
        return corr

    # ---- mirror image: better supported (unless a normal placement gives every pad the same net), or as well
    # supported and far more exact (a nearly collinear pattern lands both ways, but only one fits)
    best_m = min(mirrored, key=_rank) if mirrored else None
    best_n = min(normal, key=_rank) if normal else None
    if best_m is not None and (
            best_n is None
            or (best_m.evidence > best_n.evidence and not any(f.same_nets(best_m, m) for m in normal))
            or (best_m.evidence >= best_n.evidence and best_m.tf.rms + 0.05 < best_n.tf.rms
                and best_m.tf.rms < 0.5 * best_n.tf.rms)):
        take(best_m.tf, "number" if best_m.is_nf else "geometry")
        reasons.append("the mirrored fit beats every normal one: mirrored footprint or wrong footprint" if normal else
                       "only a mirror image of the EasyEDA footprint lands on the KiCad pads: mirrored footprint or "
                       "wrong footprint")
        corr.verdict, corr.matched, corr.score = RED, best_m.agree, 10
        corr.alternatives = [describe(m, best_m) for m in sorted(normal, key=_rank)][:12]
        return corr

    # ---- choose among the normal members: N is the placement the pad numbers support, if they do so without conflict
    pick = names_pick(normal)
    lead = min(normal, key=lambda m: _rank(m)[:2] + (not m.is_nf,) + _rank(m)[2:])
    N = lead if not lead.conflicts and lead.agree >= 1 else None
    alts: list[_Member] = []
    if N is not None and pick is not None and not harmless(pick, N):
        chosen, verdict = pick, YELLOW
        reasons.append(f"pin names pick rot {_deg(pick)} ({pick.names_ok} agree, {pick.names_bad} differ), pad "
                       f"numbers pick rot {_deg(N)}: following the names")
    elif N is not None:
        chosen, verdict = N, GREEN if N.is_nf else YELLOW
        if not N.is_nf:
            if nf is None and f.fixes_rotation(N.land):
                reasons.append("fewer than two pad numbers fix the map; placed by geometry")
            elif nf is None:
                reasons.append("the pads are on one land, which does not fix the rotation")
            elif any(m.is_nf for m in normal):
                reasons.append("the number fit is not the placement that matches the most pad numbers")
            else:
                reasons.append("the number fit leaves pads off copper; a placement consistent with the numbering "
                               "lands them")
        alts = [m for m in normal if m is not N and not harmless(m, N)]
        loose = [m for m in alts if m.agree >= N.agree and not m.conflicts and m.name_score >= N.name_score]
        by_names = all(N.name_score > m.name_score for m in alts)
        if loose:
            verdict = RED
            reasons.append("ambiguous: " + "; ".join(f"rot {_deg(m)} lands every pad, on other nets, and neither pad "
                                                     f"numbers nor pin names rule it out" for m in loose))
        elif alts and len(f.terminals) == 2 and not by_names:
            verdict = YELLOW
            reasons.append(f"polarity decided by pad numbers only (rot {', '.join(_deg(m) for m in alts)} also lands "
                           f"both pads, and no pin names confirm it): check the polarity mark in JLC's preview")
        elif alts:
            reasons.append(f"rot {', '.join(_deg(m) for m in alts)} also land every pad, on other nets; ruled out by "
                           + ("pad numbers and pin names" if by_names else "pad numbers"))
        twins = [m for m in normal if m is not N and m.land != N.land and f.same_nets(m, N) and m.agree >= N.agree
                 and m.tf.rms <= N.tf.rms + TWIN_RMS]
        if twins:
            verdict = YELLOW if verdict == GREEN else verdict
            reasons.append(f"rot {', '.join(_deg(m) for m in twins)} puts every pad on the same numbers and nets: the "
                           f"pads do not fix the body's orientation")
    else:
        why = "numbering differs" if any(m.agree for m in normal) else "the pad numbers do not overlap"
        if lead.conflicts:
            why += f" ({', '.join(lead.conflicts[:6])})"
        chosen = pick or lead
        alts = [m for m in normal if m is not chosen and not harmless(m, chosen)]
        if pick is not None:
            verdict = YELLOW
            reasons.append(f"{why}; pin names pick rot {_deg(pick)} ({pick.names_ok} agree)")
        elif lead.conflicts:
            # the pad would sit on another terminal's net, and only domain knowledge (a switch's internal pairs, a
            # regulator's pinout) could say that is intended
            verdict = RED
            reasons.append(f"numbering conflict: {', '.join(lead.conflicts[:6])} (pads on another terminal's net) "
                           f"and no pin names confirm it; check the pinout, or confirm the placement in JLC's preview")
        elif alts:
            verdict = RED
            reasons.append(f"ambiguous: {why}, and rot {', '.join(_deg(m) for m in [lead] + alts)} each land every "
                           f"pad on different nets with nothing to separate them")
        else:
            verdict = YELLOW
            reasons.append(f"{why}; " + ("every placement that lands all pads is electrically equivalent"
                                         + (" or swaps the terminals of a non-polar part" if nonpolar else "")
                                         + ", but the pads do not fix the body's orientation"
                                         if sum(m.tf.rms <= chosen.tf.rms + TWIN_RMS for m in normal) > 1
                                         else "one placement fits"))
    take(chosen.tf, "number" if chosen.is_nf else "geometry")

    # ---- names and quality
    def downgrade(why: str):
        nonlocal verdict
        verdict = YELLOW if verdict == GREEN else verdict
        reasons.append(why)

    if chosen.names_bad:
        downgrade(f"{chosen.names_bad} pin name(s) differ ({chosen.names_ok} agree)")
    elif chosen.names_ok:
        reasons.append(f"{chosen.names_ok} pin names agree")
    if chosen.agree < MATCHED_YELLOW * numbered:
        downgrade(f"only {chosen.agree} of {numbered} numbered EasyEDA pads matched by number")
    if chosen.tf.rms > RMS_YELLOW:
        downgrade(f"rms {chosen.tf.rms:.3f} mm: the land patterns differ (every pad lands)")
    if corr.rot % 90:
        downgrade(f"angle {corr.rot:g} deg is not a multiple of 90 (kept unsnapped)")
    uncovered = f.uncovered(chosen.land)
    if uncovered:
        downgrade(f"KiCad pads no EasyEDA pad lands on: {', '.join(uncovered[:16])}")
    if verdict == GREEN:
        reasons.insert(0, f"fits by pad numbers: {chosen.agree}/{numbered} matched, rms {chosen.tf.rms:.3f} mm")

    corr.verdict, corr.matched = verdict, chosen.agree
    corr.alternatives = [describe(m, chosen) for m in sorted(members.values(), key=_rank) if m is not chosen][:12]
    s = 100.0 * (0.5 + 0.5 * chosen.agree / numbered) * max(0.3, 1.0 - chosen.tf.rms / 0.5) * 0.9 ** len(alts)
    s *= 1.0 if chosen.names_ok and not chosen.names_bad else 0.95 if not chosen.names_bad else 0.8
    corr.score = int(round(min({GREEN: 100, YELLOW: 70}.get(verdict, 40), s)))
    return corr


# ------------------------------------------------------------------------------------------------------- landings

def _landed_pad(pads: list[Pad], q, number: str) -> Pad | None:
    """The copper pad q is on: its own number first where pads overlap (USB-C A1/B12), then a pad whose number is part
    of it (EasyEDA's "A1B12"), then the nearest centre."""
    hits = [p for p in pads if geom.pad_contains(p, q)]
    if not hits:
        return None
    n = norm_number(number)
    return (next((p for p in hits if norm_number(p.number) == n), None)
            or next((p for p in hits if p.number and norm_number(p.number) in n), None)
            or min(hits, key=lambda p: math.dist(q, (p.x, p.y))))


def ee_landings(part: Part, ee: EEFootprint, corr: Correction) -> list[PadLanding]:
    """Where each EasyEDA pad's centre lands under corr, in the library frame: the copper pad it is on (None = off
    copper) and its distance to the nearest copper pad centre."""
    pads = part.copper_pads()
    out = []
    for e in ee.pads:
        q = ee_to_library(corr, (e.x, e.y))
        hit = _landed_pad(pads, q, e.number)
        dist = min((math.dist(q, (p.x, p.y)) for p in pads), default=math.inf)
        out.append(PadLanding(ee_number=e.number, kicad_number=hit.number if hit else None, dist=round(dist, 4),
                              net=hit.net if hit else ""))
    return out


def net_map(part: Part, ee: EEFootprint, corr: Correction) -> dict[int, str | None]:
    """EasyEDA pad index -> the net of the KiCad pad it lands on ("" for a pad without a net); None = off copper."""
    pads = part.copper_pads()
    out: dict[int, str | None] = {}
    for i, e in enumerate(ee.pads):
        hit = _landed_pad(pads, ee_to_library(corr, (e.x, e.y)), e.number)
        out[i] = hit.net if hit else None
    return out
