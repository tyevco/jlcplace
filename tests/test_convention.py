"""The placement model: CPL angle formulas, the frames, and their round trips."""
import math
import random

import pytest

from jlcplace import convention as cv
from jlcplace.model import BOTTOM, TOP, Correction, Part


def close(a, b, tol=1e-9):
    return math.dist(a, b) <= tol


def test_rot_is_counter_clockwise_on_screen():
    # +x rotated by 90 deg CCW on a y-down screen points to -y (up)
    assert close(cv.rot((1, 0), 90), (0, -1))
    assert close(cv.rot((0, 1), 90), (1, 0))


@pytest.mark.parametrize("layer", [TOP, BOTTOM])
def test_library_board_round_trip(layer):
    rnd = random.Random(1)
    for _ in range(200):
        part = Part("U1", layer, rnd.uniform(-50, 50), rnd.uniform(-50, 50), rnd.uniform(0, 360))
        l = (rnd.uniform(-5, 5), rnd.uniform(-5, 5))
        assert close(cv.board_to_library(part, cv.library_to_board(part, l)), l, 1e-9)


@pytest.mark.parametrize("layer", [TOP, BOTTOM])
@pytest.mark.parametrize("reading", list(cv.BOTTOM_READINGS))
def test_written_angle_places_ee_footprint_on_kicad_footprint(layer, reading):
    """For any part and correction, the row written under a reading puts every ee point where KiCad has it, when JLC
    uses that same reading."""
    rnd = random.Random(2)
    for _ in range(300):
        theta = rnd.choice([0, 90, 180, 270, rnd.uniform(0, 360)])
        corr = Correction(rot=rnd.choice([0, 90, 180, 270, rnd.uniform(0, 360)]), dx=rnd.uniform(-2, 2),
                          dy=rnd.uniform(-2, 2))
        part = Part("U1", layer, rnd.uniform(-40, 40), rnd.uniform(-40, 40), theta)
        origin = (rnd.uniform(-10, 100), rnd.uniform(-10, 100))
        x, y, A = cv.cpl_row_for(part, corr, origin, reading)
        C = cv.cpl_to_board((x, y), origin)
        for _ in range(5):
            e = (rnd.uniform(-4, 4), rnd.uniform(-4, 4))
            want = cv.library_to_board(part, cv.ee_to_library(corr, e))
            got = cv.ee_to_board(e, C, A, layer, reading)
            assert close(got, want, 2e-4), (layer, reading, theta, corr, got, want)


def test_bottom_formula_is_180_minus_theta_plus_alpha():
    for theta in (0, 30, 90, 158, 235, 270):
        for alpha in (0, 90, 180, 270):
            assert cv.jlc_angle(theta, alpha, BOTTOM) == cv.norm_angle(180 - theta + alpha)
            assert cv.jlc_angle(theta, alpha, TOP) == cv.norm_angle(theta + alpha)


def test_cpl_frame_is_y_up_relative_to_origin():
    assert cv.board_to_cpl((110.0, 90.0), (100.0, 100.0)) == (10.0, 10.0)
    assert cv.cpl_to_board((10.0, 10.0), (100.0, 100.0)) == (110.0, 90.0)


def test_no_negative_zero_in_rows():
    part = Part("R1", TOP, 100.0, 100.0, 0.0)
    x, y, _ = cv.cpl_row_for(part, Correction(rot=0), (100.0, 100.0))
    assert f"{x:.4f}" == "0.0000" and f"{y:.4f}" == "0.0000"


def test_mirrored_correction_is_refused():
    with pytest.raises(ValueError):
        cv.cpl_row_for(Part("U1", TOP, 0, 0, 0), Correction(rot=0, mirror=True), (0, 0))
