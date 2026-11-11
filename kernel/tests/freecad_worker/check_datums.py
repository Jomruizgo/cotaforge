"""Datum planes and axes (ir ops ref_plane / ref_axis): fixed-pose datums, sketches on them, undo."""

import math

from harness import (
    call,
    circle,
    expect_error,
    extrude,
    mass,
    new_document,
    op,
    rect,
    sketch,
    vclose,
    volume,
)

S30 = math.sin(math.radians(30.0))
C30 = math.cos(math.radians(30.0))

# XY rotated 30 degrees about +X: u = X, v = (0, cos, sin), n = (0, -sin, cos)
TILTED = {
    "origin": [0.0, 0.0, 0.0],
    "u": [1.0, 0.0, 0.0],
    "v": [0.0, C30, S30],
    "n": [0.0, -S30, C30],
}
OFFSET_XY = {"origin": [0.0, 0.0, 20.0], "u": [1.0, 0.0, 0.0], "v": [0.0, 1.0, 0.0], "n": [0.0, 0.0, 1.0]}


def _datum_plane(frame: dict, operation_id: str = "dp") -> dict:
    return call("datum_plane", op(operation_id, how="offset", frame=frame))


def check_datum_plane_echoes_the_pose_and_assigns_an_id() -> None:
    new_document("datum_plane")
    result = _datum_plane(TILTED)
    assert result["datum"] == {"id": "datum_plane:1"}
    frame = result["frame"]
    for key in ("origin", "u", "v", "n"):
        assert vclose(frame[key], TILTED[key], 1e-9), (key, frame)
    assert result["rebuild_ok"] is True
    assert _datum_plane(OFFSET_XY, "dp2")["datum"] == {"id": "datum_plane:2"}


def check_datum_axis_echoes_the_axis_and_normalises_the_direction() -> None:
    new_document("datum_axis")
    result = call("datum_axis", op("da", how="two_points", axis={"origin": [1, 2, 3], "direction": [0, 0, 5]}))
    assert result["datum"] == {"id": "datum_axis:1"}
    assert vclose(result["axis"]["origin"], (1, 2, 3)) and vclose(result["axis"]["direction"], (0, 0, 1))


def check_datums_do_not_touch_the_model() -> None:
    new_document("datum_neutral")
    sk = sketch("s0", "XY", [rect("r", (50, 30), 100, 60)])
    extrude("base", sk, 8)
    before = mass()
    _datum_plane(OFFSET_XY)
    call("datum_axis", op("da", how="two_points", axis={"origin": [0, 0, 0], "direction": [0, 0, 1]}))
    assert mass() == before
    assert call("feature_tree", op("q"))["nodes"][-1]["kind"] == "extrude"
    assert len(call("feature_tree", op("q"))["nodes"]) == 1  # datums are not feature-tree nodes


def check_left_handed_or_non_orthonormal_frames_are_rejected() -> None:
    new_document("datum_bad")
    left_handed = {**OFFSET_XY, "v": [0.0, -1.0, 0.0]}
    expect_error("invalid_request", "datum_plane", op("bad", how="offset", frame=left_handed))
    skewed = {**OFFSET_XY, "v": [0.5, 0.5, 0.0]}
    expect_error("invalid_request", "datum_plane", op("bad", how="offset", frame=skewed))
    expect_error(
        "invalid_request", "datum_axis", op("bad", how="two_points", axis={"origin": [0, 0, 0], "direction": [0, 0, 0]})
    )


def check_sketch_on_a_datum_reports_the_datum_frame_exactly() -> None:
    new_document("datum_sketch")
    dp = _datum_plane(TILTED)
    target = {"handle": dp["datum"], "frame": dp["frame"]}
    result = sketch("s1", target, [circle("c", (0, 0), 10)])
    for key in ("origin", "u", "v", "n"):
        assert vclose(result["frame"][key], TILTED[key], 1e-12), (key, result["frame"])
    assert abs(result["profile_areas_mm2"][0] - math.pi * 25.0) < 1e-6


def check_extrude_from_a_tilted_datum_follows_the_datum_normal() -> None:
    new_document("datum_rod")
    dp = _datum_plane(TILTED)
    sk = sketch("s1", {"handle": dp["datum"], "frame": dp["frame"]}, [circle("c", (0, 0), 10)])
    extrude("rod", sk, 40)
    assert abs(volume() - math.pi * 25.0 * 40.0) < 1e-3
    box = call("bounding_box", op("q"))
    # the rod axis runs along n from the origin; the end cap centre is at 40 * n
    assert box["max"][2] > 40 * C30 - 1e-6 and box["min"][1] < -40 * S30 + 1e-6
    assert abs(box["min"][0] + 5.0) < 1e-3 and abs(box["max"][0] - 5.0) < 1e-3


def check_sketch_on_an_unknown_datum_is_an_invalid_reference() -> None:
    new_document("datum_stale")
    expect_error(
        "invalid_reference",
        "sketch",
        op("s1", on={"handle": {"id": "datum_plane:7"}, "frame": OFFSET_XY}, entities=[circle("c", (0, 0), 10)]),
    )


def check_datums_survive_undo_of_a_later_feature() -> None:
    new_document("datum_undo")
    sk0 = sketch("s0", "XY", [rect("r", (50, 30), 100, 60)])
    extrude("base", sk0, 8)
    dp = _datum_plane({**OFFSET_XY, "origin": [0.0, 0.0, 8.0]})
    target = {"handle": dp["datum"], "frame": dp["frame"]}
    sk1 = sketch("s1", target, [rect("r", (50, 30), 20, 20)])
    extrude("boss", sk1, 5)
    assert abs(volume() - (48000.0 + 2000.0)) < 1e-3
    assert call("undo_last", op("undo"))["reverted"] == {"id": "feature:2"}
    assert abs(volume() - 48000.0) < 1e-3
    sk2 = sketch("s2", target, [rect("r", (50, 30), 10, 10)])  # the datum is still there after the undo
    extrude("boss2", sk2, 5)
    assert abs(volume() - (48000.0 + 500.0)) < 1e-3


def check_sketch_on_a_datum_whose_frame_differs_from_the_registered_one_is_rejected() -> None:
    new_document("datum_frame_drift")
    dp = _datum_plane(OFFSET_XY)
    drifted = {**dp["frame"], "origin": [0.0, 0.0, 21.0]}
    expect_error(
        "invalid_request",
        "sketch",
        op("s1", on={"handle": dp["datum"], "frame": drifted}, entities=[circle("c", (0, 0), 10)]),
    )


def check_non_finite_and_malformed_datum_input_is_an_invalid_request() -> None:
    new_document("datum_bad_numbers")
    nan = float("nan")
    inf = float("inf")
    expect_error("invalid_request", "datum_plane", op("bad", how="offset", frame={**OFFSET_XY, "origin": [nan, 0, 0]}))
    expect_error("invalid_request", "datum_plane", op("bad", how="offset", frame={**OFFSET_XY, "u": [inf, 0, 0]}))
    expect_error("invalid_request", "datum_plane", op("bad", how="offset", frame={**OFFSET_XY, "u": [1.0, 0.0]}))
    expect_error(
        "invalid_request",
        "datum_axis",
        op("bad", how="two_points", axis={"origin": [nan, 0, 0], "direction": [0, 0, 1]}),
    )
    expect_error(
        "invalid_request", "datum_axis", op("bad", how="two_points", axis={"origin": [0, 0, 0], "direction": [0, 1]})
    )
    dp = _datum_plane(OFFSET_XY)
    expect_error(
        "invalid_request",
        "sketch",
        op("s1", on={"handle": dp["datum"], "frame": {"origin": [0, 0, 20]}}, entities=[circle("c", (0, 0), 10)]),
    )


def check_datum_handles_created_after_the_reverted_feature_are_stale_after_undo_last() -> None:
    new_document("datum_stale_after_undo")
    sk0 = sketch("s0", "XY", [rect("r", (50, 30), 100, 60)])
    extrude("base", sk0, 8)
    late = _datum_plane({**OFFSET_XY, "origin": [0.0, 0.0, 30.0]})  # made after the last feature
    call("undo_last", op("undo"))
    expect_error(
        "invalid_reference",
        "sketch",
        op("s1", on={"handle": late["datum"], "frame": late["frame"]}, entities=[circle("c", (0, 0), 10)]),
    )


def check_sketch_on_a_datum_accepts_float_noise_below_the_frame_tolerance() -> None:
    new_document("datum_frame_noise")
    dp = _datum_plane(OFFSET_XY)
    noisy = {**dp["frame"], "origin": [0.0, 0.0, 20.0 + 1e-7]}
    result = sketch("s1", {"handle": dp["datum"], "frame": noisy}, [circle("c", (0, 0), 10)])
    assert vclose(result["frame"]["origin"], noisy["origin"], 1e-12)
