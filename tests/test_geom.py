"""Rigid fits, assignment and pad hit tests."""
import itertools
import math
import random

import numpy as np
import pytest

from jlcplace import geom
from jlcplace.convention import rot
from jlcplace.model import Pad


@pytest.mark.parametrize("mirror", [False, True])
def test_kabsch_recovers_a_known_transform(mirror):
    rnd = random.Random(3)
    for _ in range(100):
        src = [(rnd.uniform(-5, 5), rnd.uniform(-5, 5)) for _ in range(rnd.randint(2, 12))]
        a, tx, ty = rnd.uniform(0, 360), rnd.uniform(-3, 3), rnd.uniform(-3, 3)
        dst = [geom.apply(p, a, tx, ty, mirror) for p in src]
        if len(set(src)) < 2:
            continue
        ga, gx, gy, rms = geom.kabsch(src, dst, mirror)
        assert rms < 1e-9
        assert min(abs(ga - a) % 360, 360 - abs(ga - a) % 360) < 1e-6
        assert abs(gx - tx) < 1e-9 and abs(gy - ty) < 1e-9


def test_assign_matches_brute_force():
    rnd = random.Random(4)
    for _ in range(60):
        n, m = rnd.randint(1, 6), rnd.randint(1, 6)
        C = np.array([[rnd.uniform(0, 10) for _ in range(m)] for _ in range(n)])
        pairs = geom.assign(C)
        assert len(pairs) == min(n, m)
        assert len({i for i, _ in pairs}) == len(pairs) == len({j for _, j in pairs})
        got = sum(C[i, j] for i, j in pairs)
        if n <= m:
            best = min(sum(C[i, j] for i, j in zip(range(n), perm)) for perm in itertools.permutations(range(m), n))
        else:
            best = min(sum(C[i, j] for j, i in zip(range(m), perm)) for perm in itertools.permutations(range(n), m))
        assert abs(got - best) < 1e-9


def test_rect_and_rotated_rect():
    p = Pad("1", 1.0, 2.0, 2.0, 1.0)
    assert geom.pad_contains(p, (1.99, 2.49)) and not geom.pad_contains(p, (2.01, 2.0))
    q = Pad("1", 0.0, 0.0, 2.0, 1.0, angle=90)
    assert geom.pad_contains(q, (0.0, 0.99)) and not geom.pad_contains(q, (0.99, 0.0))


def test_roundrect_corner_and_oval_and_circle():
    rr = Pad("1", 0, 0, 2.0, 2.0, shape="roundrect", rr=0.25)  # r = 0.5
    assert geom.pad_contains(rr, (0.95, 0.0))
    assert not geom.pad_contains(rr, (0.95, 0.95))            # outside the rounded corner
    assert geom.pad_contains(rr, (0.8, 0.8))
    ov = Pad("1", 0, 0, 3.0, 1.0, shape="oval")
    assert geom.pad_contains(ov, (1.45, 0.0)) and not geom.pad_contains(ov, (1.45, 0.45))
    c = Pad("1", 0, 0, 1.0, 1.0, shape="circle")
    assert geom.pad_contains(c, (0.35, 0.35)) and not geom.pad_contains(c, (0.4, 0.4))


def test_polygon_pad_and_tolerance():
    tri = Pad("1", 0, 0, 0, 0, shape="custom", poly=[(0, 0), (2, 0), (0, 2)])
    assert geom.pad_contains(tri, (0.5, 0.5)) and not geom.pad_contains(tri, (1.5, 1.5))
    assert geom.pad_contains(tri, (1.05, 1.05), tol=0.1)


def test_outline_points_are_on_the_pad():
    for p in (Pad("1", 1, 1, 2, 1, angle=30), Pad("1", 0, 0, 3, 1, shape="oval", angle=45),
              Pad("1", 0, 0, 2, 2, shape="roundrect", rr=0.25, angle=10), Pad("1", 0, 0, 1, 1, shape="circle")):
        for q in geom.pad_outline(p):
            assert geom.pad_contains(p, q, tol=1e-6)


def test_pad_hash_ignores_order_and_paste_only_pads():
    a = [Pad("1", 0, 0, 1, 1), Pad("2", 1, 0, 1, 1)]
    b = [Pad("2", 1, 0, 1, 1), Pad("1", 0, 0, 1, 1), Pad("", 0, 0, 0.5, 0.5, copper=False)]
    assert geom.pad_hash(a) == geom.pad_hash(b)
    assert geom.pad_hash(a) != geom.pad_hash([Pad("1", 0, 0, 1, 1), Pad("2", 1.1, 0, 1, 1)])


def test_a_non_finite_point_is_on_no_pad():
    """cli-1: abs(nan) > hw is False, so a nan point was inside every rect pad."""
    nan, inf = float("nan"), float("inf")
    for shape in ("rect", "roundrect", "oval", "circle", "ellipse"):
        pad = Pad("1", 0.0, 0.0, 1.0, 1.0, shape=shape, rr=0.25)
        for q in ((nan, nan), (nan, 0.0), (0.0, inf), (-inf, inf)):
            assert not geom.pad_contains(pad, q, 1e-3), (shape, q)
    poly = Pad("1", 0.0, 0.0, 1.0, 1.0, poly=[(-1, -1), (1, -1), (1, 1), (-1, 1)])
    assert not geom.pad_contains(poly, (nan, nan), 1e-3)
