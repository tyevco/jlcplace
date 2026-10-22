"""board.py: both readers, the library frame of pads (flipped parts included), custom-pad outlines, snapshots and
origins.

The pcbnew tests prove the frame rules against KiCad itself, on the dial-me-spinner boards, every KiCad demo board,
older-format boards from the reference tools' test data, and boards that KiCad builds and flips inside the test:
  (a) every pad centre mapped with convention.library_to_board is pcbnew's pad.GetPosition();
  (b) points sampled in and around every pad agree with pcbnew's pad.HitTest (geom.pad_contains on the library
      frame pad, mapped to the board), except within a band of the outline that covers KiCad's arc approximation;
  (c) the S-expression reader and the pcbnew reader return the same Board.
"""
import csv
import json
import math
import os
import random
import warnings
from pathlib import Path

import numpy as np
import pytest

from jlcplace import board as jb
from jlcplace import convention as cv
from jlcplace import geom
from jlcplace.model import BOTTOM, TOP, Board

# optional real data; CONTRACT.md: JLCPLACE_DIAL_ME_SPINNER (a dial-me-spinner checkout), JLCPLACE_TEST_EASYEDA (the
# directory holding src/, the reference tools' test data); the defaults are the development machine's
DIAL = Path(os.environ.get("JLCPLACE_DIAL_ME_SPINNER", "/home/user/dial-me-spinner")) / "hardware"
DIAL_BOARDS = [DIAL / "kicad/main/dial-me-spinner-main.kicad_pcb", DIAL / "kicad/base/dial-me-spinner-base.kicad_pcb"]
DEMOS = sorted(Path("/usr/share/kicad/demos").glob("**/*.kicad_pcb"))
REF = Path(os.environ.get("JLCPLACE_TEST_EASYEDA", "/tmp/claude-0/-home-user-dial-me-spinner/"
                         "3708159a-bc3b-5167-8507-f5b08e1ce6c9/scratchpad/jlc")) / "src"
# KiCad 5 (module), 6, 7 and 8 files from the reference tools' test data: formats pcbnew 9 still reads
OLDER = [REF / "yaqwsx_KiKit" / p for p in (
    "docs/resources/conn.kicad_pcb", "test/resources/multiboard.kicad_pcb",
    "test/resources/assembly_project_1_KiCAD6/assembly_project_1_KiCAD6.kicad_pcb",
    "test/resources/assembly_project_1_KiCAD7/assembly_project_1_KiCAD7.kicad_pcb",
    "test/resources/conn-fail-ignored.kicad_pcb", "test/resources/conn-fail-ignored-v8.kicad_pcb")]
# KiCad 10 files (version 20260206): pcbnew 9 cannot load them, the S-expression reader can
NEWER = [REF / "z2amiller_jlccheck/tests/fixtures/jlcfootprint/boards" / p for p in (
    "variant_board/variant_board.kicad_pcb", "corner_case/corner_case.kicad_pcb",
    "pin_functions_and_marks/pin_functions_and_marks.kicad_pcb")]
REAL = DIAL_BOARDS + DEMOS + OLDER


def need(path: Path) -> None:
    if not path.exists():
        pytest.skip(f"{path} is not on this machine (JLCPLACE_DIAL_ME_SPINNER / JLCPLACE_TEST_EASYEDA say where)")


def adiff(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def seg_dist(P, ring) -> np.ndarray:
    """Distance from each point of P (N x 2) to the closed polyline ring."""
    A = np.asarray(ring, float)
    D = np.roll(A, -1, axis=0) - A
    L = (D * D).sum(1)
    L[L == 0] = 1e-30
    t = np.clip(((P[:, None, :] - A[None]) * D[None]).sum(2) / L[None], 0.0, 1.0)
    Q = A[None] + t[..., None] * D[None]
    return np.sqrt(((P[:, None, :] - Q) ** 2).sum(2)).min(1)


def boundary_distance(pad, P) -> np.ndarray:
    """Exact distance of library-frame points P to the pad's outline (its polygon, or its analytic shape)."""
    if pad.poly:
        return seg_dist(P, pad.poly)
    c, s = math.cos(math.radians(pad.angle)), math.sin(math.radians(pad.angle))
    dx, dy = P[:, 0] - pad.x, P[:, 1] - pad.y
    ux, uy = dx * c - dy * s, dx * s + dy * c            # R(-angle) . (q - centre), the pad's own axes
    hw, hh = pad.w / 2, pad.h / 2
    if pad.shape == "circle":
        return np.abs(np.hypot(ux, uy) - hw)
    if pad.shape == "oval":
        r = min(hw, hh)
        ex, ey = hw - r, hh - r
        return np.abs(np.hypot(ux - np.clip(ux, -ex, ex), uy - np.clip(uy, -ey, ey)) - r)
    r = pad.rr * min(pad.w, pad.h) if pad.shape == "roundrect" else 0.0
    qx, qy = np.abs(ux) - (hw - r), np.abs(uy) - (hh - r)
    sd = np.hypot(np.maximum(qx, 0), np.maximum(qy, 0)) + np.minimum(np.maximum(qx, qy), 0) - r
    return np.abs(sd)


def sample_points(pad, rnd) -> np.ndarray:
    """Library-frame points in and around a pad: a grid and random points over 1.5x its extent."""
    if pad.poly:
        X, Y = np.asarray(pad.poly).T
        x0, x1, y0, y1 = X.min(), X.max(), Y.min(), Y.max()
        mx, my = 0.15 * (x1 - x0) + 0.02, 0.15 * (y1 - y0) + 0.02
        g = [(x0 - mx + (x1 - x0 + 2 * mx) * i / 8, y0 - my + (y1 - y0 + 2 * my) * j / 8)
             for i in range(9) for j in range(9)]
        g += [(rnd.uniform(x0 - mx, x1 + mx), rnd.uniform(y0 - my, y1 + my)) for _ in range(40)]
        return np.asarray(g)
    us = [(i * 0.375, j * 0.375) for i in (-2, -1, 0, 1, 2) for j in (-2, -1, 0, 1, 2)]
    us += [(rnd.uniform(-0.75, 0.75), rnd.uniform(-0.75, 0.75)) for _ in range(6)]
    pts = []
    for fx, fy in us:
        r = cv.rot((fx * pad.w, fy * pad.h), pad.angle)
        pts.append((pad.x + r[0], pad.y + r[1]))
    return np.asarray(pts)


def check_positions(pcbnew, kb, board: Board) -> list:
    """(a): library_to_board(pad centre) == pcbnew's pad position, for every pad."""
    bad = []
    fps = list(kb.GetFootprints())
    assert len(fps) == len(board.parts)
    for part, fp in zip(board.parts, fps):
        assert part.ref == fp.GetReference()
        for pad, kp in zip(part.pads, fp.Pads()):
            g = kp.GetPosition()
            d = math.dist(cv.library_to_board(part, (pad.x, pad.y)), (g.x / 1e6, g.y / 1e6))
            if d > 1e-4:
                bad.append(f"{part.ref}.{pad.number} ({part.layer} {part.angle}) off by {d:.6f} mm")
    return bad


def check_hittest(pcbnew, kb, board: Board, seed: int = 1) -> tuple[list, dict]:
    """(b): pad_contains on the library-frame pad == pcbnew HitTest at the mapped point, away from the outline."""
    rnd = random.Random(seed)
    max_err = kb.GetDesignSettings().m_MaxError / 1e6
    bad, tested = [], {}
    for part, fp in zip(board.parts, kb.GetFootprints()):
        for pad, kp in zip(part.pads, fp.Pads()):
            layer = jb.pad_layer(pcbnew, kp, part.bottom)
            curved = pad.poly is not None or pad.shape in ("circle", "oval") or (pad.rr > 0 and pad.shape != "rect")
            # HitTest uses KiCad's own polygon, inside the true outline by up to the board's max error
            band = 1e-3 + (max_err + jb.ARC_ERR if curved else 0.0)
            P = sample_points(pad, rnd)
            far = boundary_distance(pad, P) > band
            for q in P[far]:
                q = (float(q[0]), float(q[1]))
                g = cv.library_to_board(part, q)
                theirs = kp.HitTest(pcbnew.VECTOR2I(round(g[0] * 1e6), round(g[1] * 1e6)), 0, layer)
                mine = geom.pad_contains(pad, q)
                tested[pad.shape] = tested.get(pad.shape, 0) + 1
                if mine != theirs:
                    bad.append(f"{part.ref}.{pad.number} {pad.shape} ({part.layer} {part.angle}) at {q}: "
                               f"pad_contains {mine}, HitTest {theirs}")
    return bad, tested


def poly_tol(pad, max_err: float) -> float:
    exact = pad.shape in ("trapezoid", "rect") or (pad.shape == "chamfered" and pad.rr == 0)
    return 1e-4 if exact else max_err + 2 * jb.ARC_ERR + 1e-3


def compare_boards(a: Board, b: Board, max_err: float = 0.005) -> list:
    """(c): every field of two Boards, positions within 1e-4 mm, angles within 1e-3 deg, outlines within poly_tol."""
    bad = []
    for f in ("origin", "origin_kind", "kicad_version"):
        if getattr(a, f) != getattr(b, f):
            bad.append(f"board {f}: {getattr(a, f)} != {getattr(b, f)}")
    if len(a.parts) != len(b.parts):
        return bad + [f"{len(a.parts)} parts != {len(b.parts)}"]
    for pa, pb in zip(a.parts, b.parts):
        for f in ("ref", "layer", "footprint", "value", "fields", "lcsc", "lcsc_field", "dnp", "exclude_pos",
                  "exclude_bom", "board_only", "tht"):
            if getattr(pa, f) != getattr(pb, f):
                bad.append(f"{pa.ref} {f}: {getattr(pa, f)!r} != {getattr(pb, f)!r}")
        if math.dist((pa.x, pa.y), (pb.x, pb.y)) > 1e-4 or adiff(pa.angle, pb.angle) > 1e-3:
            bad.append(f"{pa.ref} placement: {(pa.x, pa.y, pa.angle)} != {(pb.x, pb.y, pb.angle)}")
        if len(pa.pads) != len(pb.pads):
            bad.append(f"{pa.ref}: {len(pa.pads)} pads != {len(pb.pads)}")
            continue
        for qa, qb in zip(pa.pads, pb.pads):
            tag = f"{pa.ref}.{qa.number}"
            for f in ("number", "shape", "kind", "copper", "net", "pin_name", "pin_type"):
                if getattr(qa, f) != getattr(qb, f):
                    bad.append(f"{tag} {f}: {getattr(qa, f)!r} != {getattr(qb, f)!r}")
            for f in ("x", "y", "w", "h", "rr"):
                if abs(getattr(qa, f) - getattr(qb, f)) > 1e-4:
                    bad.append(f"{tag} {f}: {getattr(qa, f)} != {getattr(qb, f)}")
            if adiff(qa.angle, qb.angle) > 1e-3:
                bad.append(f"{tag} angle: {qa.angle} != {qb.angle}")
            if (qa.poly is None) != (qb.poly is None):
                bad.append(f"{tag}: poly {qa.poly is None} vs {qb.poly is None}")
            elif qa.poly:
                A, B = np.asarray(qa.poly), np.asarray(qb.poly)
                h = max(seg_dist(A, qb.poly).max(), seg_dist(B, qa.poly).max())
                if h > poly_tol(qa, max_err):
                    bad.append(f"{tag} {qa.shape} outlines differ by {h:.6f} mm")
    return bad


def report(bad: list, n: int = 12) -> str:
    return f"{len(bad)} problems:\n" + "\n".join(bad[:n])


# --------------------------------------------------------------------------------------------------------- real boards

@pytest.fixture(scope="module", params=REAL, ids=lambda p: p.name)
def real(request):
    pcbnew = pytest.importorskip("pcbnew")
    path = request.param
    need(path)
    kb = pcbnew.LoadBoard(str(path))
    assert kb is not None, f"pcbnew could not load {path}"
    yield pcbnew, kb, jb.load_board_sexpr(path), jb.board_from_pcbnew(kb, str(path))


@pytest.mark.pcbnew
def test_pad_centres_land_on_pcbnew_positions(real):
    pcbnew, kb, sexpr, pcb = real
    for board in (sexpr, pcb):
        bad = check_positions(pcbnew, kb, board)
        assert not bad, f"[{board.source}] " + report(bad)


@pytest.mark.pcbnew
def test_pad_geometry_matches_hittest(real):
    pcbnew, kb, sexpr, pcb = real
    for board in (sexpr, pcb):
        bad, tested = check_hittest(pcbnew, kb, board)
        assert not bad, f"[{board.source}] " + report(bad)
        assert sum(tested.values()) > 0 or not any(p.pads for p in board.parts)


@pytest.mark.pcbnew
def test_readers_agree(real):
    pcbnew, kb, sexpr, pcb = real
    bad = compare_boards(sexpr, pcb, kb.GetDesignSettings().m_MaxError / 1e6)
    assert not bad, report(bad)
    assert sexpr.source == "sexpr" and pcb.source == "pcbnew"


# -------------------------------------------------------------------------------------------------- KiCad-built boards

def V(pcbnew, x, y):
    return pcbnew.VECTOR2I(round(x * 1e6), round(y * 1e6))


def _shape(pcbnew, kind, w=0.0, fill=False, **k):
    s = pcbnew.PCB_SHAPE(None, kind)
    if kind == pcbnew.SHAPE_T_POLY:
        pts = pcbnew.VECTOR_VECTOR2I()
        for p in k["pts"]:
            pts.append(V(pcbnew, *p))
        s.SetPolyPoints(pts)
    elif kind == pcbnew.SHAPE_T_ARC:
        s.SetArcGeometry(V(pcbnew, *k["s"]), V(pcbnew, *k["m"]), V(pcbnew, *k["e"]))
    elif kind == pcbnew.SHAPE_T_CIRCLE:
        s.SetCenter(V(pcbnew, *k["c"]))
        s.SetEnd(V(pcbnew, k["c"][0] + k["r"], k["c"][1]))
    elif kind == pcbnew.SHAPE_T_BEZIER:
        p = k["p"]
        s.SetStart(V(pcbnew, *p[0]))
        s.SetBezierC1(V(pcbnew, *p[1]))
        s.SetBezierC2(V(pcbnew, *p[2]))
        s.SetEnd(V(pcbnew, *p[3]))
    else:
        s.SetStart(V(pcbnew, *k["s"]))
        s.SetEnd(V(pcbnew, *k["e"]))
    s.SetWidth(round(w * 1e6))
    s.SetFilled(fill)
    return s


def build_library_footprint(pcbnew, board, rnd):
    """A footprint in its library state with every pad shape and kind; returns it and the expected library pads as
    (number, (x, y), angle, (w, h), shape)."""
    fp = pcbnew.FOOTPRINT(board)
    fp.SetFPIDAsString("Test:Everything")
    want = []
    SMD, PTH = pcbnew.PAD.SMDMask(), pcbnew.PAD.PTHMask()
    r = lambda a, b: round(rnd.uniform(a, b), 3)                       # noqa: E731

    def add(attr, shape, size, layers, rr=None, chamfer=None, cr=0.2, delta=None, offset=None, anchor=None,
            prims=(), name=None, expect=None):
        p = pcbnew.PAD(fp)
        num = str(len(want) + 1) if name is None else name
        p.SetNumber(num)
        p.SetAttribute(attr)
        p.SetLayerSet(layers)
        p.SetShape(shape)
        p.SetSize(V(pcbnew, *size))
        if attr in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH):
            d = round(min(size) * 0.5, 3)
            p.SetDrillSize(V(pcbnew, d, d))
        if rr is not None:
            p.SetRoundRectRadiusRatio(rr)
        if chamfer is not None:
            p.SetChamferRectRatio(cr)
            p.SetChamferPositions(chamfer)
        if delta is not None:
            p.SetDelta(V(pcbnew, *delta))
        if offset is not None:
            p.SetOffset(V(pcbnew, *offset))
        if anchor is not None:
            p.SetAnchorPadShape(pcbnew.F_Cu, anchor)
        for s in prims:
            p.AddPrimitiveShape(pcbnew.F_Cu, s)
            s.thisown = False                                         # the pad owns it now
        fp.Add(p)
        l, a = (r(-15, 15), r(-15, 15)), r(0, 360)
        p.SetFPRelativePosition(V(pcbnew, *l))
        p.SetFPRelativeOrientation(pcbnew.EDA_ANGLE(a, pcbnew.DEGREES_T))
        want.append((num if expect is None else expect, l, a, size))
        return p

    S = pcbnew
    for shape in (S.PAD_SHAPE_RECTANGLE, S.PAD_SHAPE_OVAL, S.PAD_SHAPE_CIRCLE, S.PAD_SHAPE_ROUNDRECT):
        for attr, layers in ((S.PAD_ATTRIB_SMD, SMD), (S.PAD_ATTRIB_PTH, PTH)):
            w, h = r(0.5, 2.0), r(0.3, 1.5)
            if shape == S.PAD_SHAPE_CIRCLE:
                h = w
            add(attr, shape, (w, h), layers, rr=0.3)
            add(attr, shape, (w, h), layers, rr=0.15, offset=(r(-0.3, 0.3), r(-0.3, 0.3)))
    for mask in range(1, 16):                                         # every combination of chamfered corners
        add(S.PAD_ATTRIB_SMD, S.PAD_SHAPE_CHAMFERED_RECT, (r(0.8, 2), r(0.5, 1.5)), SMD, rr=rnd.choice([0.0, 0.2]),
            chamfer=mask, cr=r(0.1, 0.4))
    for delta in ((0.3, 0), (0, 0.3), (-0.25, 0), (0, -0.2), (0.2, 0.1)):
        add(S.PAD_ATTRIB_SMD, S.PAD_SHAPE_TRAPEZOID, (r(1, 2), r(0.8, 1.5)), SMD, delta=delta)
    add(S.PAD_ATTRIB_NPTH, S.PAD_SHAPE_CIRCLE, (1.0, 1.0), S.PAD.UnplatedHoleMask(), expect="")  # saved unnumbered
    add(S.PAD_ATTRIB_CONN, S.PAD_SHAPE_RECTANGLE, (1.0, 2.0), S.PAD.ConnSMDMask())
    paste = S.LSET()
    paste.AddLayer(S.F_Paste)
    # a paste-only aperture: KiCad drops its number on loading
    add(S.PAD_ATTRIB_SMD, S.PAD_SHAPE_ROUNDRECT, (0.7, 0.6), paste, rr=0.25, name="AP", expect="")
    far = S.LSET()
    far.AddLayer(S.B_Cu)
    add(S.PAD_ATTRIB_SMD, S.PAD_SHAPE_RECTANGLE, (1.0, 0.5), far, name="FAR")         # copper on the other side
    T = pcbnew
    customs = [
        (S.PAD_SHAPE_RECTANGLE, [_shape(T, T.SHAPE_T_POLY, pts=[(0, 0), (2, 0.3), (1.5, 1.6), (0.2, 1.0)], fill=True)]),
        (S.PAD_SHAPE_CIRCLE, [_shape(T, T.SHAPE_T_POLY, 0.2, True, pts=[(0, 0), (2, 0.3), (1.5, 1.6)])]),
        (S.PAD_SHAPE_RECTANGLE, [_shape(T, T.SHAPE_T_POLY, 0.15, pts=[(-1, -1), (1, -1), (1, 1), (-1, 1)])]),
        (S.PAD_SHAPE_CIRCLE, [_shape(T, T.SHAPE_T_SEGMENT, 0.3, s=(0, 0), e=(2.5, 1.0)),
                              _shape(T, T.SHAPE_T_SEGMENT, 0.3, s=(2.5, 1.0), e=(2.5, -1.5))]),
        (S.PAD_SHAPE_RECTANGLE, [_shape(T, T.SHAPE_T_ARC, 0.25, s=(1, 0), m=(0, 1), e=(-1, 0))]),
        (S.PAD_SHAPE_CIRCLE, [_shape(T, T.SHAPE_T_ARC, 0.4, s=(2, 0), m=(0, -2), e=(-2, 0.5)),
                              _shape(T, T.SHAPE_T_ARC, 0.5, s=(0.3, 0), m=(0, 0.3), e=(-0.3, 0))]),
        (S.PAD_SHAPE_RECTANGLE, [_shape(T, T.SHAPE_T_CIRCLE, fill=True, c=(1.5, 0), r=0.6),
                                 _shape(T, T.SHAPE_T_CIRCLE, 0.2, c=(-1.5, 0.5), r=0.8)]),     # a disc and a ring
        (S.PAD_SHAPE_CIRCLE, [_shape(T, T.SHAPE_T_CIRCLE, 0.3, True, c=(0, 0), r=1.5)]),
        (S.PAD_SHAPE_RECTANGLE, [_shape(T, T.SHAPE_T_RECTANGLE, 0.2, True, s=(-2, -0.5), e=(-0.5, 1.5)),
                                 _shape(T, T.SHAPE_T_RECTANGLE, 0.2, s=(0.5, -1), e=(2.5, 1))]),
        (S.PAD_SHAPE_CIRCLE, [_shape(T, T.SHAPE_T_BEZIER, 0.3, p=[(0, 0), (1, -2), (2, 2), (3, 0)])]),
        (S.PAD_SHAPE_RECTANGLE, [_shape(T, T.SHAPE_T_POLY, fill=True, pts=[(0.5, 0.5), (3, 0.5), (3, 3)]),
                                 _shape(T, T.SHAPE_T_POLY, fill=True, pts=[(-0.5, -0.5), (-3, -0.5), (-3, -3)])]),
        (S.PAD_SHAPE_CIRCLE, [_shape(T, T.SHAPE_T_SEGMENT, 0.8, s=(0, 0), e=(0, -2.5))]),          # stroke = anchor
    ]
    for anchor, prims in customs:
        add(S.PAD_ATTRIB_SMD, S.PAD_SHAPE_CUSTOM, (0.8, 0.8 if anchor == S.PAD_SHAPE_CIRCLE else 0.6), SMD,
            anchor=anchor, prims=prims)
    # a front/inner/back padstack whose back copper is a bigger, offset rounded rectangle
    p = add(S.PAD_ATTRIB_PTH, S.PAD_SHAPE_CIRCLE, (1.2, 1.2), PTH, name="FIB")
    p.Padstack().SetMode(S.PADSTACK.MODE_FRONT_INNER_BACK)
    p.SetShape(S.F_Cu, S.PAD_SHAPE_CIRCLE)
    p.SetSize(S.F_Cu, V(S, 1.2, 1.2))
    p.SetShape(S.B_Cu, S.PAD_SHAPE_ROUNDRECT)
    p.SetSize(S.B_Cu, V(S, 2.4, 1.4))
    p.SetRoundRectRadiusRatio(S.B_Cu, 0.2)
    p.SetOffset(S.B_Cu, V(S, 0.3, -0.2))
    return fp, want


PLACEMENTS = [("top", 0.0), ("top", 90.0), ("top", 37.5), ("tb", 0.0), ("tb", 90.0), ("tb", 213.25), ("lr", 0.0),
              ("lr", 301.7)]


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """A board KiCad builds: one library footprint placed on the top, flipped top-to-bottom and flipped left-to-right,
    at right and arbitrary angles; saved and reloaded."""
    pcbnew = pytest.importorskip("pcbnew")
    rnd = random.Random(7)
    kb = pcbnew.BOARD()
    lib, want = build_library_footprint(pcbnew, kb, rnd)
    for i, (how, theta) in enumerate(PLACEMENTS):
        fp = pcbnew.FOOTPRINT(lib)
        fp.SetReference(f"U{i + 1}")
        kb.Add(fp)
        fp.SetPosition(V(pcbnew, 50 + 60 * (i % 4), 50 + 60 * (i // 4)))
        if how == "tb":
            fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_TOP_BOTTOM)
        elif how == "lr":
            fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
        fp.SetOrientationDegrees(theta)
    path = tmp_path_factory.mktemp("built") / "built.kicad_pcb"
    assert pcbnew.SaveBoard(str(path), kb)
    kb = pcbnew.LoadBoard(str(path))
    yield pcbnew, kb, want, jb.load_board_sexpr(path), jb.board_from_pcbnew(kb, str(path))


@pytest.mark.pcbnew
def test_flipped_footprints_read_back_in_the_library_frame(built):
    """Whatever KiCad's flip did, both readers recover the pads exactly as they were drawn in the library."""
    pcbnew, kb, want, sexpr, pcb = built
    for board in (sexpr, pcb):
        assert [(p.layer, p.angle) for p in board.parts] == [(TOP if h == "top" else BOTTOM, a) for h, a in PLACEMENTS]
        for part in board.parts:
            assert len(part.pads) == len(want)
            # KiCad saves pads sorted by number (ties in memory order), so match them by their library position
            for pad in part.pads:
                num, l, a, size = min(want, key=lambda w: math.dist(w[1], (pad.x, pad.y)))
                where = f"[{board.source}] {part.ref} ({part.layer} {part.angle}) pad {num!r}"
                assert pad.number == num, where
                assert math.dist((pad.x, pad.y), l) <= 2e-6, where
                assert adiff(pad.angle, a) <= 1e-6, where
                # the front/inner/back pad too: a part's own side always carries the library's front entry
                assert (pad.w, pad.h) == pytest.approx(size, abs=1e-9), where


@pytest.mark.pcbnew
def test_built_board_positions_hittest_and_readers(built):
    pcbnew, kb, want, sexpr, pcb = built
    shapes = set()
    for board in (sexpr, pcb):
        assert not check_positions(pcbnew, kb, board)
        bad, tested = check_hittest(pcbnew, kb, board, seed=3)
        assert not bad, f"[{board.source}] " + report(bad)
        shapes |= set(tested)
    assert shapes == {"rect", "roundrect", "oval", "circle", "trapezoid", "chamfered", "custom"}
    bad = compare_boards(sexpr, pcb, kb.GetDesignSettings().m_MaxError / 1e6)
    assert not bad, report(bad)


@pytest.mark.pcbnew
def test_built_board_pad_kinds_and_copper(built):
    pcbnew, kb, _, sexpr, _ = built
    for part, fp in zip(sexpr.parts, kb.GetFootprints()):
        fib = next(p for p in fp.Pads() if p.GetNumber() == "FIB")
        # flipping swapped the padstack's front and back entries: the far side has the library's back copper
        far = pcbnew.F_Cu if part.bottom else pcbnew.B_Cu
        assert fib.GetShape(far) == pcbnew.PAD_SHAPE_ROUNDRECT and fib.GetSize(far).x == 2400000
        pads = {p.number: p for p in part.pads}
        assert (pads["1"].kind, pads["1"].copper) == ("smd", True)
        assert (pads["2"].kind, pads["2"].copper) == ("smd", True)
        assert (pads["3"].kind, pads["3"].copper) == ("tht", True)
        npth = next(p for p in part.pads if p.kind == "npth")
        conn = next(p for p in part.pads if p.kind == "connect")
        assert not npth.copper and conn.copper
        aperture = next(p for p in part.pads if p.kind == "smd" and p.number == "")
        assert not aperture.copper and aperture.shape == "roundrect"     # paste only
        assert not pads["FAR"].copper                      # copper only on the far side of the part
        assert (pads["FIB"].shape, pads["FIB"].w, pads["FIB"].copper, pads["FIB"].poly) == ("circle", 1.2, True, None)


# ------------------------------------------------------------------------------------------------------ KiCad 10, auto

@pytest.mark.parametrize("path", NEWER, ids=lambda p: p.name)
def test_newer_format_reads_with_sexpr(path):
    need(path)
    b = jb.load_board_sexpr(path)
    assert b.kicad_version == "20260206" and b.parts
    for part in b.parts:
        assert part.ref and part.pads
        for pad in part.pads:
            assert pad.w > 0 and pad.h > 0
    if path.name == "variant_board.kicad_pcb":
        # (variant (name "B") (dnp yes)) applies to variant B only: the default variant still places Q1
        assert [(p.ref, p.lcsc, p.dnp) for p in b.parts] == [
            ("D4", "C81598", False), ("R2", "C25804", False), ("C1", "C134220", False), ("Q1", "C2132", False),
            ("LED1", "C2286", False)]


@pytest.mark.pcbnew
def test_auto_reader_falls_back_for_newer_files():
    pytest.importorskip("pcbnew")
    path = NEWER[0]
    need(path)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        b = jb.load_board(path)
    assert b.source == "sexpr"
    assert any("S-expression reader" in str(x.message) for x in w)
    with pytest.raises(OSError):
        jb.load_board(path, reader="pcbnew")


@pytest.mark.pcbnew
def test_auto_reader_prefers_pcbnew():
    pytest.importorskip("pcbnew")
    need(DIAL_BOARDS[1])
    assert jb.load_board(DIAL_BOARDS[1]).source == "pcbnew"
    assert jb.load_board(DIAL_BOARDS[1], reader="sexpr").source == "sexpr"
    with pytest.raises(ValueError):
        jb.load_board(DIAL_BOARDS[1], reader="kicad")


# ----------------------------------------------------------------------------------------------------- dial-me-spinner

def read_bom(path: Path) -> dict:
    out = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            for ref in row["Designator"].split(","):
                out[ref.strip()] = row["LCSC Part #"]
    return out


@pytest.mark.parametrize("which", ["main", "base"])
def test_dial_board_lcsc_codes_match_the_fab_bom(which):
    path = DIAL / f"kicad/{which}/dial-me-spinner-{which}.kicad_pcb"
    bom_path = DIAL / f"fab/{which}/dial-me-spinner-{which}-BOM-JLCPCB.csv"
    need(path)
    need(bom_path)
    b = jb.load_board_sexpr(path)
    assert b.origin == (100.0, 100.0) and b.origin_kind == "aux"
    parts = {p.ref: p for p in b.parts}
    bom = read_bom(bom_path)
    assert bom
    for ref, code in bom.items():
        assert parts[ref].lcsc == code, ref
        assert parts[ref].lcsc_field == "LCSC", ref
        assert parts[ref].placed, ref
    # the fab CPL lists exactly the placed parts with codes, each on the side the board has it
    with open(DIAL / f"fab/{which}/dial-me-spinner-{which}-CPL-JLCPCB.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert {r["Designator"] for r in rows} == {p.ref for p in b.parts if p.lcsc and p.placed}
    for r in rows:
        assert parts[r["Designator"]].layer == r["Layer"].lower()


def test_dial_board_flags_and_bottom_pads():
    need(DIAL_BOARDS[0])
    b = jb.load_board_sexpr(DIAL_BOARDS[0])
    c10 = b.part("C10")
    assert (c10.layer, c10.angle, c10.footprint, c10.value) == (BOTTOM, 90.0, "Capacitor_SMD:C_0603_1608Metric", "10u")
    assert c10.fields["MPN"] == "CL10A106KP8NNNC" and c10.lcsc == "C19702"
    p1, p2 = c10.pads
    # file: (at -0.775 0 90) on a bottom footprint at 90 deg -> library (-0.775, 0), angle 90 - 90 = 0
    assert (p1.number, p1.x, p1.y, p1.angle, p1.shape, p1.rr, p1.net) == ("1", -0.775, 0.0, 0.0, "roundrect", 0.25,
                                                                           "+3V3")
    assert cv.library_to_board(c10, (p1.x, p1.y)) == pytest.approx((97.3689, 110.8783), abs=1e-9)
    assert p2.net == "GND" and all(p.copper and p.kind == "smd" for p in c10.pads)


# ------------------------------------------------------------------------------------------------------------- origins

def test_origin_options():
    need(DIAL_BOARDS[1])
    p = DIAL_BOARDS[1]
    assert jb.load_board_sexpr(p).origin == (100.0, 100.0)
    b = jb.load_board_sexpr(p, origin="absolute")
    assert (b.origin, b.origin_kind) == ((0.0, 0.0), "absolute")
    b = jb.load_board_sexpr(p, origin="12.5, -3")
    assert (b.origin, b.origin_kind) == ((12.5, -3.0), "user")
    b = jb.load_board_sexpr(p, origin=(1, 2))
    assert (b.origin, b.origin_kind) == ((1.0, 2.0), "user")
    assert jb.load_board_sexpr(p, origin="grid").origin_kind == "grid"
    with pytest.raises(ValueError):
        jb.load_board_sexpr(p, origin="middle")


@pytest.mark.parametrize("text, want", [("aux", ((5.0, 6.0), "aux")), ("GRID", ((7.0, 8.0), "grid")),
                                        (" absolute ", ((0.0, 0.0), "absolute")), ("1,2", ((1.0, 2.0), "user")),
                                        ("(1.5; -2)", ((1.5, -2.0), "user")), ("10mm,20mm", ((10.0, 20.0), "user")),
                                        ((3, 4), ((3.0, 4.0), "user"))])
def test_resolve_origin(text, want):
    assert jb.resolve_origin(text, (5, 6), (7, 8)) == want


def test_resolve_origin_rejects_garbage():
    for bad in ("1", "1,2,3", "x,y", ""):
        with pytest.raises(ValueError):
            jb.resolve_origin(bad, (0, 0), (0, 0))


@pytest.mark.pcbnew
def test_origins_agree_with_pcbnew():
    pytest.importorskip("pcbnew")
    for p in DIAL_BOARDS:
        need(p)
        for o in ("aux", "grid", "absolute", "3,4"):
            a, b = jb.load_board_sexpr(p, origin=o), jb.load_board_pcbnew(p, origin=o)
            assert (a.origin, a.origin_kind) == (b.origin, b.origin_kind)


# ----------------------------------------------------------------------------------------------------------- snapshots

def test_snapshot_round_trip(tmp_path):
    need(DIAL_BOARDS[0])
    b = jb.load_board_sexpr(DIAL_BOARDS[0])
    out = tmp_path / "board.json"
    jb.save_snapshot(b, out)
    text = out.read_text(encoding="utf-8")
    assert text.endswith("}\n") and json.loads(text)["origin"] == [100.0, 100.0]
    assert text == json.dumps(json.loads(text), indent=1, sort_keys=True) + "\n"
    back = jb.load_snapshot(out)
    assert back == b
    assert jb.load_board(out) == b                                   # auto: a .json path is a snapshot
    moved = jb.load_board(out, origin=(1.0, 2.0))
    assert (moved.origin, moved.origin_kind) == ((1.0, 2.0), "user")
    with pytest.raises(ValueError):
        jb.load_board(out, origin="grid")                            # the snapshot only knows its aux origin


def test_snapshot_round_trip_keeps_polygons(tmp_path):
    path = Path("/usr/share/kicad/demos/custom_pads_test/custom_pads_test.kicad_pcb")
    need(path)
    b = jb.load_board_sexpr(path)
    assert sum(1 for part in b.parts for p in part.pads if p.poly) >= 6
    jb.save_snapshot(b, tmp_path / "b.json")
    back = jb.load_snapshot(tmp_path / "b.json")
    assert back == b
    assert all(isinstance(v, tuple) for part in back.parts for p in part.pads if p.poly for v in p.poly)


def test_snapshot_lcsc_fields_rerun(tmp_path):
    b = jb.load_board_sexpr(write_board(tmp_path, MINIMAL))
    jb.save_snapshot(b, tmp_path / "s.json")
    again = jb.load_board(tmp_path / "s.json", lcsc_fields=["Supplier"])
    assert again.part("R1").lcsc == "C99" and again.part("R1").lcsc_field == "Supplier"


# ------------------------------------------------------------------------------------------------------- S-expressions

def test_parse_sexpr():
    assert jb.parse_sexpr('(a "b c" (d 1.5) "e\\"f" "g\\\\h" "i\\nj")') == [
        ["a", "b c", ["d", "1.5"], 'e"f', "g\\h", "i\nj"]]
    assert jb.parse_sexpr('(a ")(" b)') == [["a", ")(", "b"]]
    assert jb.parse_sexpr("") == []
    with pytest.raises(ValueError):
        jb.parse_sexpr("(a (b)")
    with pytest.raises(ValueError):
        jb.parse_sexpr("(a))")


def write_board(tmp_path: Path, text: str, name: str = "t.kicad_pcb") -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


# KiCad 6 style (fp_text fields, properties, attr tokens) plus newer forms in one file
MINIMAL = """(kicad_pcb (version 20221018) (generator pcbnew)
  (setup (aux_axis_origin 50 60) (grid_origin 1 2))
  (net 0 "") (net 1 "GND") (net 2 "/a \\"quoted\\" net")
  (footprint "Resistor_SMD:R_0603" (layer "F.Cu") (at 10 20 30)
    (property "LCSC" "LCSC_R1") (property "Supplier" "C99") (property "JLCPCB" " c25804 ")
    (property "Sheetfile" "x.kicad_sch") (property "ki_description" "a resistor") (property "Footprint" "dup")
    (attr smd exclude_from_bom)
    (fp_text reference "R1" (at 0 -1.43 30) (layer "F.SilkS"))
    (fp_text value "10k" (at 0 1.43 30) (layer "F.Fab"))
    (pad "1" smd roundrect (at -0.825 0 30) (size 0.8 0.95) (layers "F.Cu" "F.Paste" "F.Mask")
      (roundrect_rratio 0.25) (net 1 "GND") (pinfunction "A") (pintype "passive"))
    (pad "2" smd rect (at 0.825 0 75) (size 0.8 0.95) (layers "F.Cu" "F.Paste" "F.Mask") (net 2 "/a \\"quoted\\" net"))
  )
  (footprint "Lib:Bottom" (layer "B.Cu") (at 30 40 90)
    (property "Reference" "U1") (property "Value" "chip") (attr through_hole board_only)
    (dnp yes)
    (pad "1" thru_hole oval (at 1 0.5 120) (size 2 1) (drill 0.8 (offset 0.1 0.2)) (layers "*.Cu" "*.Mask") (net 1))
    (pad "" np_thru_hole circle (at 0 -2) (size 1 1) (drill 1) (layers "*.Cu"))
    (pad "3" smd rect (at 0 3) (size 1 1) (layers "F.Cu"))
    (pad "4" connect rect (at 0 4) (size 1 1) (layers "B.Cu" "B.Mask") (net "LATE"))
  )
  (gr_line (start 0 0) (end 1 1) (layer "Edge.Cuts"))
  (segment (start 0 0) (end 1 1) (width 0.2) (layer "F.Cu") (net 1))
)
"""


def test_minimal_board(tmp_path):
    b = jb.load_board_sexpr(write_board(tmp_path, MINIMAL))
    assert (b.origin, b.kicad_version, b.source) == ((50.0, 60.0), "20221018", "sexpr")
    assert jb.load_board_sexpr(write_board(tmp_path, MINIMAL), origin="grid").origin == (1.0, 2.0)
    r1, u1 = b.parts
    assert (r1.ref, r1.value, r1.layer, r1.angle, r1.footprint) == ("R1", "10k", TOP, 30.0, "Resistor_SMD:R_0603")
    assert r1.fields == {"Reference": "R1", "Value": "10k", "Datasheet": "", "Description": "a resistor",
                         "LCSC": "LCSC_R1", "Supplier": "C99", "JLCPCB": " c25804 "}
    assert (r1.lcsc, r1.lcsc_field) == ("C25804", "JLCPCB")
    assert (r1.exclude_bom, r1.exclude_pos, r1.dnp, r1.board_only, r1.tht) == (True, False, False, False, False)
    p1, p2 = r1.pads
    assert (p1.x, p1.y, p1.angle, p1.rr, p1.net, p1.pin_name, p1.pin_type) == (-0.825, 0.0, 0.0, 0.25, "GND", "A",
                                                                               "passive")
    assert (p2.angle, p2.rr, p2.net) == (45.0, 0.0, '/a "quoted" net')     # board 75 - footprint 30
    assert (u1.ref, u1.layer, u1.dnp, u1.board_only, u1.tht) == ("U1", BOTTOM, True, True, True)
    q1, q2, q3, q4 = u1.pads
    # bottom: library = (x, -y), angle = theta - board angle = 90 - 120
    assert (q1.x, q1.y, q1.angle, q1.kind, q1.copper, q1.net) == (1.0, -0.5, 330.0, "tht", True, "GND")
    assert q1.poly and geom.pad_contains(q1, (1.0, -0.5))                   # offset shape: outline in poly
    assert (q2.number, q2.kind, q2.copper) == ("", "npth", False)
    assert (q3.kind, q3.copper) == ("smd", False)                           # F.Cu only, on a bottom part
    assert (q4.kind, q4.copper, q4.net) == ("connect", True, "LATE")


def test_legacy_module_format(tmp_path):
    text = """(kicad_pcb (version 20171130) (host pcbnew 5.1.9)
  (module conn:J (layer B.Cu) (tedit 5D6FB713) (at 10 10 180)
    (fp_text reference J4 (at 0 3.3) (layer B.SilkS) hide)
    (fp_text value Molex (at 0 -3.2) (layer B.Fab))
    (pad 1 smd rect (at 0.8 1.7 0) (size 0.55 1.5) (layers B.Cu B.Paste B.Mask) (net 1 GND)))
  (module kikit:Tab (layer F.Cu) (at 0 0) (attr virtual)
    (fp_text reference REF** (at 0 0) (layer F.SilkS)) (fp_text value Tab (at 0 0) (layer F.Fab))))
"""
    j4, tab = jb.load_board_sexpr(write_board(tmp_path, text)).parts
    assert (j4.ref, j4.value, j4.layer, j4.angle, j4.tht) == ("J4", "Molex", BOTTOM, 180.0, True)
    assert (j4.pads[0].x, j4.pads[0].y, j4.pads[0].angle, j4.pads[0].net) == (0.8, -1.7, 180.0, "GND")
    assert (tab.exclude_pos, tab.exclude_bom, tab.tht, tab.placed) == (True, True, False, False)


def test_teardrop_typo_is_repaired_and_malformed_files_fail(tmp_path):
    good = "(kicad_pcb (version 20240108) (footprint \"a\" (layer \"F.Cu\") (at 0 0) (property \"Reference\" \"R1\")" \
           " (pad \"1\" smd rect (at 0 0) (size 1 1) (layers \"F.Cu\") (teardrops (curved_edges no)filter_ratio 0.9)" \
           " (enabled yes)))) (footprint \"b\" (layer \"F.Cu\") (at 0 0) (property \"Reference\" \"R2\")))"
    assert [p.ref for p in jb.load_board_sexpr(write_board(tmp_path, good)).parts] == ["R1", "R2"]
    with pytest.raises(ValueError, match="malformed"):
        jb.load_board_sexpr(write_board(tmp_path, "(kicad_pcb (version 1) (footprint \"a\")) (net 1))"))
    with pytest.raises(ValueError, match="truncated"):
        jb.load_board_sexpr(write_board(tmp_path, "(kicad_pcb (version 1) (footprint \"a\""))
    with pytest.raises(ValueError, match="not a KiCad board"):
        jb.load_board_sexpr(write_board(tmp_path, "(kicad_sch (version 1))"))


# ------------------------------------------------------------------------------------------------------------ outlines

def area(ring) -> float:
    return abs(jb._area(ring))


def inside(poly, pts) -> np.ndarray:
    return np.array([geom.point_in_polygon(tuple(p), poly) for p in pts])


def test_union_of_overlapping_squares_and_shared_edges():
    a = [(0, 0), (2, 0), (2, 2), (0, 2)]
    b = [(1, 1), (3, 1), (3, 3), (1, 3)]
    rings = jb._union([[a], [b]])
    assert len(rings) == 1 and area(rings[0]) == pytest.approx(7.0)
    c = [(2, 0), (4, 0), (4, 2), (2, 2)]                     # shares an edge with a: the edge disappears
    rings = jb._clean_rings(jb._union([[a], [c]]))
    assert len(rings) == 1 and area(rings[0]) == pytest.approx(8.0) and len(rings[0]) == 4


def test_union_keeps_holes_and_islands_under_even_odd():
    err = jb.ARC_ERR
    cu = jb._Copper(err)
    cu.region([jb._circle((0, 0), 2.0, err), jb._circle((0, 0), 1.0, err)])       # a ring
    cu.region([[(5, 5), (6, 5), (6, 6), (5, 6)]])                                  # an island
    poly = jb._bridge(jb._clean_rings(jb._union(cu.regions())))
    assert inside(poly, [(1.5, 0), (5.5, 5.5), (0, -1.5)]).all()
    assert not inside(poly, [(0, 0), (0.5, 0.2), (3, 2), (4, 6.5), (3.01, 3)]).any()
    # the island hangs on a zero-width bridge along the shortest gap (here the diagonal): only points exactly on it
    # count as inside, as every point on an outline does
    assert inside(poly, [(3, 3)]).all()


def test_stroke_miters_near_straight_joints():
    err = jb.ARC_ERR
    cu = jb._Copper(err)
    pts = [(0.0, 0.0), (1.0, 1e-5), (2.0, 0.0), (3.0, 2e-4), (4.0, 0.0)]          # turns of 1e-5 .. 4e-4 rad
    cu.stroke(pts, False, 0.1)
    regs = cu.regions()
    assert len(regs) == 4 + 2                                                      # 4 quads, discs only at the ends
    poly = jb._bridge(jb._clean_rings(jb._union(regs)))
    assert area(poly) == pytest.approx(4 * 0.2 + math.pi * 0.01, rel=2e-3)


def _fuzz_case(rnd, err):
    cu = jb._Copper(err)
    P = lambda: (round(rnd.uniform(-2, 2), 3), round(rnd.uniform(-2, 2), 3))       # noqa: E731
    for kind in rnd.sample(["poly", "disc", "ring", "stroke", "bezier", "shared", "rect", "arc", "lines", "anchor"],
                           rnd.randint(1, 5)):
        if kind == "poly":
            c, n = P(), rnd.randint(3, 9)
            angs = sorted(rnd.uniform(0, 2 * math.pi) for _ in range(n))           # may cross itself
            cu.region([[(c[0] + rnd.uniform(0.3, 2) * math.cos(a), c[1] + rnd.uniform(0.3, 2) * math.sin(a))
                        for a in angs]])
        elif kind == "disc":
            cu.circle(P(), rnd.uniform(0.05, 1.5))
        elif kind == "ring":
            c, r = P(), rnd.uniform(0.5, 1.5)
            cu.region([jb._circle(c, r, err), jb._circle(c, r * rnd.uniform(0.3, 0.9), err)])
        elif kind == "stroke":
            if rnd.random() < 0.5:
                a, b = P(), P()
                pts = [(a[0] + (b[0] - a[0]) * t + rnd.uniform(-1e-4, 1e-4), a[1] + (b[1] - a[1]) * t)
                       for t in np.linspace(0, 1, 7)]
                cu.stroke(pts, False, rnd.choice([0.05, 0.1, 0.3]))
            else:
                cu.stroke([P() for _ in range(rnd.randint(2, 6))], rnd.random() < 0.3, rnd.choice([0.05, 0.1, 0.3]))
        elif kind == "bezier":
            cu.stroke(jb._bezier([P(), P(), P(), P()], err), False, rnd.choice([0.05, 0.15]))
        elif kind == "shared":
            a, b, c, d = P(), P(), P(), P()
            cu.region([[a, b, c]])
            cu.region([[a, c, d]])
        elif kind == "rect":
            x0, y0 = P()
            w, h = rnd.uniform(0.2, 2), rnd.uniform(0.2, 2)
            cu.region([[(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h)]])
        elif kind == "arc":
            node = ["gr_arc", ["start", *map(str, P())], ["mid", *map(str, P())], ["end", *map(str, P())],
                    ["width", str(rnd.choice([0.1, 0.4]))]]
            jb._add_primitive(cu, node, err)
        elif kind == "lines":                       # separate lines sharing end points, some nearly collinear
            pts = [P(), P()]
            for _ in range(rnd.randint(1, 4)):
                if rnd.random() < 0.5:
                    d, a = (pts[-1][0] - pts[-2][0], pts[-1][1] - pts[-2][1]), rnd.uniform(-1e-3, 1e-3)
                    pts.append((pts[-1][0] + d[0] * math.cos(a) - d[1] * math.sin(a),
                                pts[-1][1] + d[0] * math.sin(a) + d[1] * math.cos(a)))
                else:
                    pts.append(P())
            w = rnd.choice([0.1, 0.25])
            for a, b in zip(pts, pts[1:]):
                cu.stroke([a, b], False, w)
        else:                                       # a line starting at the anchor with the anchor's radius
            cu.circle((0.0, 0.0), 0.5)
            cu.stroke([(0.0, 0.0), P()], False, 0.5)
    return cu.regions()


@pytest.mark.parametrize("seed", range(4))
def test_union_matches_brute_force(seed):
    """The outline's even-odd interior is the union of the primitives (points within 1e-5 mm of an edge excepted),
    and the rings always close."""
    err = jb.ARC_ERR
    for case in range(25):
        rnd = random.Random(1000 * seed + case)
        regs = _fuzz_case(rnd, err)
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            poly = jb._bridge(jb._clean_rings(jb._union(regs)))
        P = np.array([(rnd.uniform(-4, 4), rnd.uniform(-4, 4)) for _ in range(600)])
        truth = np.zeros(len(P), bool)
        for reg in regs:
            truth |= jb._even_odd(reg, P)
        mine = jb._even_odd([poly], P) if poly else np.zeros(len(P), bool)
        near = np.zeros(len(P), bool)
        for reg in regs:
            for ring in reg:
                near |= seg_dist(P, ring) <= 1e-5
        assert not ((truth != mine) & ~near).any(), (seed, case)


def test_custom_pad_from_primitives(tmp_path):
    """An anchor and primitives sharing edges, a stroked ring and a filled poly with a stroke, on a bottom part."""
    text = """(kicad_pcb (version 20241229)
  (footprint "L:C" (layer "B.Cu") (at 0 0 90) (property "Reference" "P1")
    (pad "1" smd custom (at 1 2 120) (size 1 1) (layers "B.Cu")
      (options (clearance outline) (anchor rect))
      (primitives
        (gr_poly (pts (xy 0.5 -0.5) (xy 2 -0.5) (xy 2 0.5) (xy 0.5 0.5)) (width 0) (fill yes))
        (gr_circle (center -3 0) (end -2 0) (width 0.2) (fill no))
        (gr_line (start 0 0) (end -2 0) (width 0.2)))))
)"""
    pad = jb.load_board_sexpr(write_board(tmp_path, text)).parts[0].pads[0]
    assert (pad.shape, pad.x, pad.y, pad.angle) == ("custom", 1.0, -2.0, 330.0)
    # stored pad axes -> library: l + R(psi) . D . u
    to_lib = lambda u: jb._stored_to_library(u, pad.x, pad.y, pad.angle, True)   # noqa: E731
    yes = [(0, 0), (1.9, 0.4), (1.2, -0.45), (-1, 0.05), (-3, 0.95), (-3.95, 0), (-2, 0.08)]
    no = [(-3, 0), (-3, 0.5), (0, 0.7), (2.1, 0), (-1, 0.15)]
    assert inside(pad.poly, [to_lib(u) for u in yes]).all()
    assert not inside(pad.poly, [to_lib(u) for u in no]).any()
    # area: anchor 1 + rectangle 1.5 (its edge on the anchor's) + ring + line body
    ring = math.pi * (1.1 ** 2 - 0.9 ** 2)
    # the line's body from x = -2 to the anchor at -0.5, less the part already in the ring (x < -3 + sqrt(1.21 - y^2))
    overlap = 0.2 * 0.1 - 2 * 0.1 ** 3 / 3 / (2 * 1.1)
    assert area(pad.poly) == pytest.approx(1 + 1.5 + ring + 1.5 * 0.2 - overlap, rel=2e-3)


def test_missing_files_raise(tmp_path):
    with pytest.raises(FileNotFoundError):
        jb.load_board_sexpr(tmp_path / "nope.kicad_pcb")
    with pytest.raises(FileNotFoundError):
        jb.load_board(tmp_path / "nope.json")
    with pytest.raises(FileNotFoundError):
        jb.load_board(tmp_path / "nope.kicad_pcb", reader="sexpr")


@pytest.mark.pcbnew
def test_missing_file_with_pcbnew(tmp_path):
    pytest.importorskip("pcbnew")
    with pytest.raises(FileNotFoundError):
        jb.load_board(tmp_path / "nope.kicad_pcb")
