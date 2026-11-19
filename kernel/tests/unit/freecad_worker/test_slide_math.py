"""``worker/slide_math.py`` in isolation: the kinematics of the angle pin and the heel, the sizes of the carrier, and the
stock picks (no FreeCAD)."""

import importlib.util
import math
import sys
from pathlib import Path
from typing import Any

import pytest

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("worker_slide_math", WORKER_DIR / "slide_math.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sm = _load()
INSERT = {"d0": -28.0, "e0": -4.0, "e1": 4.0, "qa": 10.0, "qb": 18.0}


def test_the_opening_travel_is_the_stroke_over_the_tangent_and_the_pin_slides_the_stroke_over_the_sine() -> None:
    assert sm.travel_for_stroke(10.0, 15.0) == pytest.approx(10.0 / math.tan(math.radians(15.0)))
    assert sm.pin_axis_travel(10.0, 15.0) == pytest.approx(10.0 / math.sin(math.radians(15.0)))
    # a steeper pin needs less opening for the same stroke
    assert sm.travel_for_stroke(10.0, 25.0) < sm.travel_for_stroke(10.0, 15.0)


def test_the_heel_must_be_steeper_than_the_pin_and_under_45_degrees() -> None:
    assert sm.check_angles(15.0, 2.0) == 17.0
    with pytest.raises(sm.SlideError, match="steeper"):
        sm.check_angles(15.0, 0.0)
    with pytest.raises(sm.SlideError, match="steeper"):
        sm.check_angles(15.0, -1.0)
    with pytest.raises(sm.SlideError, match="45"):
        sm.check_angles(44.0, 2.0)


def test_the_heel_face_recedes_faster_than_the_carrier_moves_so_it_never_drags() -> None:
    n = sm.size(INSERT, 6.0, 15.0, 17.0, 12.0)
    opening = n["opening"]
    carrier = opening * math.tan(math.radians(15.0))  # how far the carrier has gone out
    heel = opening * math.tan(math.radians(17.0))  # how far the heel face has drawn back
    assert carrier == pytest.approx(6.0) and heel > carrier


def test_the_back_face_of_the_carrier_leans_the_heel_angle_and_the_bore_the_pin_angle() -> None:
    n = sm.size(INSERT, 6.0, 15.0, 17.0, 12.0)
    q0, q1 = n["g_p"], n["carrier_height"]
    assert (sm.face_d(n, q1) - sm.face_d(n, q0)) / (q1 - q0) == pytest.approx(math.tan(math.radians(17.0)))
    assert (sm.hole_d(n, q1) - sm.hole_d(n, q0)) / (q1 - q0) == pytest.approx(math.tan(math.radians(15.0)))
    # the wall behind the bore grows with depth: the heel angle is the larger
    assert sm.face_d(n, q1) - sm.hole_d(n, q1) > sm.face_d(n, q0) - sm.hole_d(n, q0)


def test_the_bore_keeps_a_wall_on_both_sides_over_the_whole_carrier() -> None:
    n = sm.size(INSERT, 6.0, 15.0, 17.0, 12.0)
    for q in (n["g_p"], (n["g_p"] + n["carrier_height"]) / 2, n["carrier_height"]):
        reach = n["r_hole"] / math.cos(n["alpha"])
        assert sm.hole_d(n, q) - reach >= n["wall"] - 1e-9  # towards the insert
        assert sm.face_d(n, q) - (sm.hole_d(n, q) + reach) >= n["wall"] - 1e-9  # towards the back face


def test_the_pin_stays_engaged_in_its_bore_when_the_pin_half_has_opened_the_full_travel() -> None:
    for stroke, alpha in ((4.5, 15.0), (12.0, 20.0), (3.0, 10.0)):
        n = sm.size(INSERT, stroke, alpha, alpha + 2.0, 12.0)
        cut = sm.pin_length(n)
        tip_q_closed = n["carrier_height"] - n["tip_short"]
        tip_q_open = tip_q_closed - n["opening"]  # the pin half has gone up by the opening travel
        engaged = (tip_q_open - n["g_p"]) / math.cos(n["alpha"])
        assert engaged >= 1.5 * 12.0 - 1e-6, (stroke, alpha, engaged)
        assert cut > n["seat"]


def test_the_pin_tip_stays_inside_the_carrier_even_with_its_slanted_end() -> None:
    n = sm.size(INSERT, 6.0, 15.0, 17.0, 12.0)
    lowest_edge = (n["carrier_height"] - n["tip_short"]) + 12.0 / 2 * math.sin(n["alpha"])
    assert lowest_edge < n["carrier_height"]


def test_a_slide_that_crosses_the_parting_plane_is_refused() -> None:
    with pytest.raises(sm.SlideError, match="parting plane"):
        sm.size({**INSERT, "qa": -3.0}, 6.0, 15.0, 17.0, 12.0)


def test_the_carrier_covers_the_insert_and_is_tall_enough_for_the_detent() -> None:
    n = sm.size(INSERT, 3.0, 25.0, 27.0, 10.0)
    assert n["carrier_height"] >= INSERT["qb"] + 3.0 and n["carrier_height"] - n["g_p"] >= sm.MIN_CARRIER_HEIGHT_MM
    assert n["width"] >= sm.CARRIER_MIN_WIDTH_MM and n["width"] >= INSERT["e1"] - INSERT["e0"] + sm.CARRIER_MARGIN_MM
    # the ball sits in the free stretch above the groove, with room for its bore and a margin
    free_top = n["q_groove"] - sm.GROOVE_HEIGHT_MM / 2 - n["g_p"]
    assert n["g_p"] + sm.BALL_BORE_D_MM / 2 < n["ball_q"] < n["g_p"] + free_top - sm.BALL_BORE_D_MM / 2


def test_the_pin_is_the_shortest_stock_one_that_is_long_enough_or_nothing() -> None:
    rows = [{"d": 12, "length": ln, "item": f"APD12{ln}"} for ln in (40, 60, 80)]
    assert sm.pick_pin(rows, 60.0)["item"] == "APD1260"
    assert sm.pick_pin(rows, 60.1)["item"] == "APD1280"
    with pytest.raises(sm.SlideError, match="longest stock"):
        sm.pick_pin(rows, 90.0)


def test_the_wear_plate_is_the_thinnest_narrowest_shortest_one_that_covers_the_carrier_and_its_travel() -> None:
    rows = [
        {"item": "A", "thickness": 12.7, "width": 50.8, "length": 127.0},
        {"item": "B", "thickness": 9.525, "width": 76.2, "length": 152.4},
        {"item": "C", "thickness": 9.525, "width": 50.8, "length": 152.4},
    ]
    assert sm.pick_wear(rows, 45.0, 100.0)["item"] == "C"
    assert sm.pick_wear(rows, 60.0, 100.0)["item"] == "B"
    with pytest.raises(sm.SlideError, match="wear plate"):
        sm.pick_wear(rows, 90.0, 100.0)
