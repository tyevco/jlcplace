"""Geometry: rigid fits, assignment, pad outlines and point-in-pad tests. Pure Python + numpy, no KiCad needed.

Pads (model.Pad and model.EEPad) share the fields used here: x, y, w, h, angle, shape, rr, poly. A pad's outline
point u (in the pad's own axes, w along x) sits at  (x, y) + R(angle) . u  in the pad's frame.
"""

from __future__ import annotations

import hashlib
import math

import numpy as np

from .convention import rot


# ------------------------------------------------------------------------------------------------------- rigid fit

def kabsch(src, dst, mirror: bool = False, weights=None):
    """Best rigid map  dst ~ R(angle) . M . src + t  (M = diag(-1, 1) when mirror). Returns (angle, tx, ty, rms)."""
    S = np.asarray(src, float).reshape(-1, 2).copy()
    T = np.asarray(dst, float).reshape(-1, 2)
    if mirror:
        S[:, 0] = -S[:, 0]
    w = np.ones(len(S)) if weights is None else np.asarray(weights, float)
    w = w / w.sum()
    cs, ct = (S * w[:, None]).sum(0), (T * w[:, None]).sum(0)
    a, b = S - cs, T - ct
    # standard (y-up) angle phi maps a onto b; R(angle) == Q(-angle), so angle = -phi
    num = (w * (a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0])).sum()
    den = (w * (a[:, 0] * b[:, 0] + a[:, 1] * b[:, 1])).sum()
    angle = -math.degrees(math.atan2(num, den)) if (num or den) else 0.0
    r = np.array([rot(p, angle) for p in a]) if len(a) else a
    t = ct - np.array(rot(cs, angle))
    res = b - r
    rms = float(np.sqrt((w * (res ** 2).sum(1)).sum())) if len(a) else 0.0
    return angle % 360.0, float(t[0]), float(t[1]), rms


def apply(p, angle, tx, ty, mirror=False):
    q = (-p[0], p[1]) if mirror else (p[0], p[1])
    r = rot(q, angle)
    return (r[0] + tx, r[1] + ty)


# ------------------------------------------------------------------------------------------------------- assignment

def assign(cost):
    """Minimum-cost assignment (Hungarian, O(n^2 m)). cost: n x m, any n, m. Returns [(i, j), ...] for min(n, m)
    pairs."""
    C = np.asarray(cost, float)
    if C.size == 0:
        return []
    transposed = C.shape[0] > C.shape[1]
    if transposed:
        C = C.T
    n, m = C.shape
    INF = float("inf")
    u, v = np.zeros(n + 1), np.zeros(m + 1)
    p = np.zeros(m + 1, dtype=int)       # p[j]: row assigned to column j (1-based), 0 = none
    way = np.zeros(m + 1, dtype=int)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = np.full(m + 1, INF)
        used = np.zeros(m + 1, dtype=bool)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta, j1 = INF, 0
            for j in range(1, m + 1):
                if not used[j]:
                    cur = C[i0 - 1, j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j], way[j] = cur, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    pairs = [(int(p[j]) - 1, j - 1) for j in range(1, m + 1) if p[j]]
    if transposed:
        pairs = [(j, i) for i, j in pairs]
    return sorted(pairs)


# ------------------------------------------------------------------------------------------------------- pads

def _to_pad_axes(pad, q):
    return rot((q[0] - pad.x, q[1] - pad.y), -(pad.angle or 0.0))


def point_in_polygon(q, poly) -> bool:
    """Even-odd rule; points on an edge count as inside."""
    x, y = q
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        # on the edge?
        if min(x1, x2) - 1e-9 <= x <= max(x1, x2) + 1e-9 and min(y1, y2) - 1e-9 <= y <= max(y1, y2) + 1e-9:
            if abs((x2 - x1) * (y - y1) - (y2 - y1) * (x - x1)) <= 1e-9 * max(1.0, math.hypot(x2 - x1, y2 - y1)):
                return True
        if (y1 > y) != (y2 > y):
            xi = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < xi:
                inside = not inside
    return inside


def _dist_to_polygon(q, poly) -> float:
    best = float("inf")
    for i in range(len(poly)):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % len(poly)]
        dx, dy = x2 - x1, y2 - y1
        L = dx * dx + dy * dy
        t = 0.0 if L == 0 else max(0.0, min(1.0, ((q[0] - x1) * dx + (q[1] - y1) * dy) / L))
        best = min(best, math.hypot(q[0] - (x1 + t * dx), q[1] - (y1 + t * dy)))
    return best


def pad_contains(pad, q, tol: float = 0.0) -> bool:
    """Is point q (in the pad's frame) on the pad, grown by tol?"""
    if pad.poly:
        return point_in_polygon(q, pad.poly) or (tol > 0 and _dist_to_polygon(q, pad.poly) <= tol)
    ux, uy = _to_pad_axes(pad, q)
    hw, hh = pad.w / 2.0, pad.h / 2.0
    shape = (pad.shape or "rect").lower()
    if shape == "circle":
        return math.hypot(ux, uy) <= hw + tol
    if shape == "ellipse":
        a, b = hw + tol, hh + tol
        return a > 0 and b > 0 and (ux / a) ** 2 + (uy / b) ** 2 <= 1.0 + 1e-12
    if shape == "oval":
        if hw >= hh:
            r, ex, ey = hh, hw - hh, 0.0
        else:
            r, ex, ey = hw, 0.0, hh - hw
        cx, cy = max(-ex, min(ex, ux)), max(-ey, min(ey, uy))
        return math.hypot(ux - cx, uy - cy) <= r + tol
    if abs(ux) > hw + tol or abs(uy) > hh + tol:
        return False
    if shape == "roundrect" and pad.rr > 0:
        r = pad.rr * min(pad.w, pad.h)
        cx, cy = max(-(hw - r), min(hw - r, ux)), max(-(hh - r), min(hh - r, uy))
        return math.hypot(ux - cx, uy - cy) <= r + tol
    return True                                   # rect, chamfered/trapezoid/custom without a polygon: bounding rect


def pad_outline(pad, arc_steps: int = 8):
    """Outline polygon of a pad in its frame (for drawing; also used when a polygon is needed)."""
    if pad.poly:
        return list(pad.poly)
    hw, hh = pad.w / 2.0, pad.h / 2.0
    shape = (pad.shape or "rect").lower()
    pts = []

    def arc(cx, cy, r, a0, a1):
        for k in range(arc_steps + 1):
            a = math.radians(a0 + (a1 - a0) * k / arc_steps)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))

    if shape in ("circle", "ellipse"):
        n = 4 * arc_steps
        pts = [(hw * math.cos(2 * math.pi * k / n), hh * math.sin(2 * math.pi * k / n)) for k in range(n)]
        if shape == "circle":
            pts = [(hw * math.cos(2 * math.pi * k / n), hw * math.sin(2 * math.pi * k / n)) for k in range(n)]
    elif shape == "oval":
        if hw >= hh:
            r, e = hh, hw - hh
            arc(e, 0, r, -90, 90)
            arc(-e, 0, r, 90, 270)
        else:
            r, e = hw, hh - hw
            arc(0, e, r, 0, 180)
            arc(0, -e, r, 180, 360)
    elif shape == "roundrect" and pad.rr > 0:
        r = pad.rr * min(pad.w, pad.h)
        arc(hw - r, hh - r, r, 0, 90)
        arc(-(hw - r), hh - r, r, 90, 180)
        arc(-(hw - r), -(hh - r), r, 180, 270)
        arc(hw - r, -(hh - r), r, 270, 360)
    else:
        pts = [(hw, hh), (-hw, hh), (-hw, -hh), (hw, -hh)]
    out = []
    for u in pts:
        r = rot(u, pad.angle or 0.0)
        out.append((pad.x + r[0], pad.y + r[1]))
    return out


def pad_hash(pads) -> str:
    """Stable hash of a pad set's geometry and numbering (soldered copper pads only)."""
    rows = sorted((p.number, round(p.x, 3) + 0.0, round(p.y, 3) + 0.0, round(p.w, 3), round(p.h, 3),
                   round((p.angle or 0.0) % 180.0, 1)) for p in pads if getattr(p, "copper", True))
    return "sha1:" + hashlib.sha1(repr(rows).encode()).hexdigest()[:16]


def centroid(pts):
    pts = list(pts)
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
