"""How a CPL row maps to where JLC puts the part, and back.

The frames are defined in jlcplace.model. JLC documents the CPL columns, but not how it reads the rotation of a
bottom-side part. Every open tool (kicad-jlcpcb-tools, Fabrication-Toolkit, KiKit, KiBot, JLCKicadTools) and the
JLC preview truth data recorded by the z2amiller fork agree on the reading named "jlc-2022" here:

    top:     A = (theta + alpha) mod 360,        JLC puts ee point e at   C + R(A) . e
    bottom:  A = (180 - theta + alpha) mod 360,  JLC puts ee point e at   C + R(-A) . Mx . e

where C is the row's point in the board frame and Mx = diag(-1, 1): the bottom is viewed from below, so the angle is
counter-clockwise seen from below and x is mirrored. Both readings place the EasyEDA footprint exactly where the
KiCad one is when library = R(alpha) . ee + d, because R(theta) . D . R(alpha) = R(-A) . Mx on the bottom.

Any rigid placement of a mirrored footprint is R(phi) . Mx for some phi, so the possible bottom-side readings are
phi = sigma * A + kappa. jlc-2022 is sigma = -1, kappa = 0. The other three are kept as hypotheses: `check` re-places
every bottom row under each of them and flags the parts whose pin-to-net mapping would change (the parts to look at
in JLC's preview, since nothing offline can prove which reading the factory uses).
"""

from __future__ import annotations

import math

from .model import BOTTOM, Correction, Part

#: name -> (sigma, kappa) for the bottom side; the top is always C + R(A) . e
BOTTOM_READINGS = {
    "jlc-2022": (-1, 0.0),
    "ccw-from-top": (1, 0.0),
    "ccw-from-top+180": (1, 180.0),
    "jlc-2022+180": (-1, 180.0),
}
DEFAULT = "jlc-2022"


def rot(p, a):
    """R(a) . p (KiCad's sense: counter-clockwise on screen, y down)."""
    r = math.radians(a)
    c, s = math.cos(r), math.sin(r)
    return (p[0] * c + p[1] * s, -p[0] * s + p[1] * c)


def norm_angle(a: float) -> float:
    """[0, 360), rounded to 1e-6 so 359.9999999 does not survive as a separate angle."""
    a = round(a % 360.0, 6)
    return 0.0 if a >= 360.0 else a + 0.0


def jlc_angle(theta: float, alpha: float, layer: str, reading: str = DEFAULT) -> float:
    """The CPL rotation that places the EasyEDA footprint like the KiCad one, under the given bottom reading."""
    if layer != BOTTOM:
        return norm_angle(theta + alpha)
    sigma, kappa = BOTTOM_READINGS[reading]
    # placement R(sigma A + kappa) Mx must equal R(theta) D R(alpha) = R(theta - alpha - 180) Mx
    return norm_angle((theta - alpha - 180.0 - kappa) / sigma)


def ee_to_board(e, C, A: float, layer: str, reading: str = DEFAULT):
    """Where JLC puts EasyEDA point e for a row at board point C with rotation A."""
    if layer != BOTTOM:
        r = rot(e, A)
    else:
        sigma, kappa = BOTTOM_READINGS[reading]
        r = rot((-e[0], e[1]), sigma * A + kappa)
    return (C[0] + r[0], C[1] + r[1])


def library_to_board(part: Part, l):
    """board = P + R(theta) . D . l"""
    s = -1.0 if part.layer == BOTTOM else 1.0
    r = rot((l[0], s * l[1]), part.angle)
    return (part.x + r[0], part.y + r[1])


def board_to_library(part: Part, q):
    """l = D . R(-theta) . (q - P)"""
    s = -1.0 if part.layer == BOTTOM else 1.0
    r = rot((q[0] - part.x, q[1] - part.y), -part.angle)
    return (r[0], s * r[1])


def ee_to_library(corr: Correction, e):
    """library = R(rot) . M . e + d"""
    m = (-e[0], e[1]) if corr.mirror else (e[0], e[1])
    r = rot(m, corr.rot)
    return (r[0] + corr.dx, r[1] + corr.dy)


def board_to_cpl(q, origin):
    return (q[0] - origin[0], origin[1] - q[1])


def cpl_to_board(c, origin):
    return (origin[0] + c[0], origin[1] - c[1])


def cpl_row_for(part: Part, corr: Correction, origin, reading: str = DEFAULT):
    """(Mid X, Mid Y, rotation) in the cpl frame for a part and its correction. A mirrored correction has no rigid
    placement and is refused."""
    if corr.mirror:
        raise ValueError(f"{part.ref}: the EasyEDA footprint is a mirror image of the KiCad one; no CPL row places it")
    q = library_to_board(part, (corr.dx, corr.dy))
    x, y = board_to_cpl(q, origin)
    return round(x, 4) + 0.0, round(y, 4) + 0.0, jlc_angle(part.angle, corr.rot, part.layer, reading)
