"""Holes across a tube wall (position = [axial_mm, angle_deg]) and cosmetic tapped holes, straight on the worker.

The tube is a rod O40 x 100 bored O30 from its far end, standing on XY or
leaning 30 degrees about X. Expected volumes are the exact integral of what a round hole removes along a radius of the
tube (``verification.formulas.radial_hole_volume``, evaluated here as constants): OCC is 5e-4..2.5e-3 low on
cylinder-cylinder cuts and differencing two 55000 mm3 volumes adds ~0.5 mm3 of noise, so they are compared at 3e-3
relative plus 1 mm3.
"""

import json
import math
import os
from typing import Any

import geometry as g
from harness import (
    call,
    circle,
    expect_error,
    extrude,
    face_info,
    faces_of,
    new_document,
    op,
    rect,
    select_face,
    sketch,
    vclose,
    volume,
)

S30 = math.sin(math.radians(30.0))
C30 = math.cos(math.radians(30.0))
TILTED = {"origin": [0.0, 0.0, 0.0], "u": [1.0, 0.0, 0.0], "v": [0.0, C30, S30], "n": [0.0, -S30, C30]}
FAR_END = {
    "origin": [0.0, -100.0 * S30, 100.0 * C30],
    "u": [1.0, 0.0, 0.0],
    "v": [0.0, C30, S30],
    "n": [0.0, -S30, C30],
}
STANDING = {"origin": [0.0, 0.0, 0.0], "u": [1.0, 0.0, 0.0], "v": [0.0, 1.0, 0.0], "n": [0.0, 0.0, 1.0]}
STANDING_FAR_END = {"origin": [0.0, 0.0, 100.0], "u": [1.0, 0.0, 0.0], "v": [0.0, 1.0, 0.0], "n": [0.0, 0.0, 1.0]}

# radial_hole_volume(20, 15, 3, depth): blind 8, blind 3 (stays in the wall), through both walls
BLIND8, POCKET3, THROUGH = 141.90804609956, 83.22806551336, 283.81609219912
REL, NOISE = 3e-3, 1.0


def _near(value: float, expected: float) -> bool:
    return abs(value - expected) <= max(REL * expected, NOISE)


def _tube(tilted: bool) -> dict:
    """Rod O40 x 100 along the frame normal, bored O30 from its far end; returns the rod and the bore cut."""
    frame, far = (TILTED, FAR_END) if tilted else (STANDING, STANDING_FAR_END)
    new_document("tube")
    plane = call("datum_plane", op("dp", how="offset", frame=frame))
    sk = sketch("rod_sk", {"handle": plane["datum"], "frame": frame}, [circle("outer", (0, 0), 40)])
    rod = extrude("rod", sk, 100)
    end = call("datum_plane", op("dp2", how="offset", frame=far))
    bore_sk = sketch("bore_sk", {"handle": end["datum"], "frame": far}, [circle("inner", (0, 0), 30)])
    bore = call(
        "cut",
        op(
            "bore",
            sketch=bore_sk["sketch"],
            end_condition="through_all",
            direction="negative",
            both_directions=False,
            depth_mm=None,
            depth2_mm=None,
        ),
    )
    return {"rod": rod, "bore": bore}


def _wall(tube: dict) -> dict:
    """The rod's outer wall (the bore's own cylinder is created by the bore cut, not by the rod)."""
    return select_face(faces_of(tube["rod"]["feature"]), surface="cylinder")


def _hole(tube: dict, position: list, end: str = "blind", depth: float | None = 8.0, **extra: Any) -> dict:
    params = op(
        "cross",
        face=_wall(tube)["handle"],
        position=position,
        standard="simple",
        size=None,
        diameter_mm=6.0,
        end_condition=end,
        depth_mm=depth if end == "blind" else None,
    )
    params.update(extra)
    return call("hole", params)


def _hole_walls(result: dict) -> list:
    return [f for f in faces_of(result["feature"], "created") if f["surface"] == "cylinder"]


def _removed(before: float) -> float:
    return before - volume()


# --- the shared pure math ---------------------------------------------------------------------------------------


def check_radial_entry_matches_the_shared_cases() -> None:
    root = os.environ["COTAFORGE_REPO"]
    with open(os.path.join(root, "tests", "data", "radial_entry_cases.json"), encoding="utf-8") as handle:
        cases = json.load(handle)["cases"]
    assert len(cases) >= 6
    for case in cases:
        entry = g.radial_entry(
            tuple(case["center"]), tuple(case["axis_origin"]), tuple(case["axis_dir"]), case["radius"], case["position"]
        )
        for key in ("axis_point", "outward", "surface_point"):
            assert vclose(entry[key], case[key], 1e-9), (case["name"], key, entry[key], case[key])


# --- radial holes ------------------------------------------------------------------------------------------------


def _radial_volumes(tilted: bool) -> None:
    for position, end, depth, expected in (
        ([0, 0], "blind", 8.0, BLIND8),
        ([0, 0], "blind", 3.0, POCKET3),
        ([0, 0], "through_all", None, THROUGH),
    ):
        tube = _tube(tilted)
        assert abs(volume() - math.pi * (20.0**2 - 15.0**2) * 100.0) < 2.0, volume()
        before = volume()
        result = _hole(tube, position, end, depth)
        removed = _removed(before)
        assert _near(removed, expected), (tilted, end, depth, removed, expected)
        assert result["rebuild_ok"] is True
        walls = _hole_walls(result)
        assert len(walls) == (2 if end == "through_all" else 1), [w["handle"] for w in walls]  # one per tube wall
        n = (0.0, -S30, C30) if tilted else (0.0, 0.0, 1.0)
        for wall in walls:
            assert abs(wall["radius"] - 3.0) < 1e-6, wall
            # the hole is radial: its axis is perpendicular to the tube axis, and (angle 0) lies along world X
            assert abs(sum(a * b for a, b in zip(wall["axis"]["direction"], n, strict=True))) < 1e-6, wall["axis"]
            assert abs(abs(wall["axis"]["direction"][0]) - 1.0) < 1e-6, wall["axis"]  # u = world X for both tubes


def check_radial_hole_volumes_on_a_vertical_tube() -> None:
    _radial_volumes(False)


def check_radial_hole_volumes_on_a_tilted_tube() -> None:
    _radial_volumes(True)


def check_the_entry_face_is_modified_and_the_hole_wall_is_created() -> None:
    tube = _tube(True)
    outer = _wall(tube)
    result = _hole(tube, [5, 45])
    assert outer["handle"] in result["modified_faces"], (outer["handle"], result["modified_faces"])
    created = faces_of(result["feature"], "created")
    assert any(f["surface"] == "cylinder" for f in created), created


def check_the_hole_enters_on_the_requested_side() -> None:
    for angle in (0, 45, 90, 180, -90):
        tube = _tube(False)
        outer = face_info(_wall(tube)["handle"])
        entry = g.radial_entry(
            tuple(outer["center"]),
            tuple(outer["axis"]["origin"]),
            tuple(outer["axis"]["direction"]),
            outer["radius"],
            [5, angle],
        )
        result = _hole(tube, [5, angle])
        wall = select_face(faces_of(result["feature"], "created"), surface="cylinder")
        along = sum((c - a) * o for c, a, o in zip(wall["center"], entry["axis_point"], entry["outward"], strict=True))
        assert 12.0 <= along <= 20.0 + 1e-6, (angle, along)


def check_a_hole_in_a_moved_tube_body() -> None:
    tube = _tube(False)
    call(
        "move_body",
        op(
            "mv",
            body=tube["rod"]["created_bodies"][0],
            transform={"origin": [30, 40, 50], "rotation": [[1, 0, 0], [0, 0, -1], [0, 1, 0]]},
        ),
    )
    before = volume()
    result = _hole(tube, [10, 30], "through_all")
    assert _near(_removed(before), THROUGH), (_removed(before), THROUGH)
    assert all(abs(wall["radius"] - 3.0) < 1e-6 for wall in _hole_walls(result))


# --- refusals ----------------------------------------------------------------------------------------------------


def check_a_hole_on_the_bore_is_refused_as_concave() -> None:
    tube = _tube(False)
    before = volume()
    bore = select_face(faces_of(tube["bore"]["feature"]), surface="cylinder")
    error = expect_error(
        "invalid_request",
        "hole",
        op(
            "cross",
            face=bore["handle"],
            position=[0, 0],
            standard="simple",
            size=None,
            diameter_mm=6.0,
            end_condition="blind",
            depth_mm=4.0,
        ),
    )
    assert "concave" in error.message and "bore" in error.message, error.message
    assert volume() == before


def check_a_position_off_the_face_is_refused_with_the_axial_range() -> None:
    tube = _tube(False)
    before = volume()
    error = expect_error(
        "invalid_request",
        "hole",
        op(
            "cross",
            face=_wall(tube)["handle"],
            position=[80, 0],
            standard="simple",
            size=None,
            diameter_mm=6.0,
            end_condition="blind",
            depth_mm=8.0,
        ),
    )
    assert "not on face" in error.message and "+-50" in error.message, error.message
    assert volume() == before


# --- tapped holes ------------------------------------------------------------------------------------------------

TAP_DRILLS = {"M3": 2.5, "M4": 3.3, "M5": 4.2, "M6": 5.0, "M8": 6.8, "M10": 8.5, "M12": 10.2, "M16": 14.0, "M20": 17.5}


def _plate() -> dict:
    new_document("plate")
    sk = sketch("s0", "XY", [rect("r", (0, 0), 100, 100)])
    base = extrude("base", sk, 30)
    return {"base": base, "top": select_face(faces_of(base["feature"]), normal=(0, 0, 1))}


def _tapped(plate: dict, size: str, diameter: float, **extra: Any) -> dict:
    params = op(
        "tap",
        face=plate["top"]["handle"],
        position=[0, 0],
        standard="iso_tapped",
        size=size,
        diameter_mm=diameter,
        end_condition="blind",
        depth_mm=15.0,
        thread={"size": size, "depth_mm": 10.0},
    )
    params.update(extra)
    return call("hole", params)


def check_a_tapped_hole_is_the_tap_drill_cylinder() -> None:
    for size, drill in TAP_DRILLS.items():
        plate = _plate()
        before = volume()
        result = _tapped(plate, size, drill)
        removed = before - volume()
        assert abs(removed - math.pi * (drill / 2) ** 2 * 15.0) < 1e-3, (size, removed)
        wall = select_face(faces_of(result["feature"], "created"), surface="cylinder")
        assert abs(wall["radius"] - drill / 2) < 1e-6, (size, wall["radius"])


def check_a_fine_or_large_tapped_hole_is_the_tap_drill_cylinder_of_the_iso_fine_table() -> None:
    """M45x1.5 was refused (the table stopped at M36 coarse); FreeCAD's ISO fine table has it and drills D - P."""
    for size, drill in {"M8x1": 7.0, "M20x1.5": 18.5, "M45x1.5": 43.5, "M60x4": 56.0, "M45": 40.5}.items():
        plate = _plate()
        before = volume()
        result = _tapped(plate, size, drill)
        removed = before - volume()
        assert abs(removed - math.pi * (drill / 2) ** 2 * 15.0) < 1e-3, (size, removed)
        wall = select_face(faces_of(result["feature"], "created"), surface="cylinder")
        assert abs(wall["radius"] - drill / 2) < 1e-6, (size, wall["radius"])


def check_a_fine_size_the_cad_does_not_have_is_not_supported_and_nothing_is_drilled() -> None:
    plate = _plate()
    before = volume()
    error = expect_error(
        "not_supported",
        "hole",
        op(
            "tap",
            face=plate["top"]["handle"],
            position=[0, 0],
            standard="iso_tapped",
            size="M45x1.7",
            diameter_mm=43.3,
            end_condition="blind",
            depth_mm=15.0,
            thread={"size": "M45x1.7", "depth_mm": 10.0},
        ),
    )
    assert "M45x1.7" in error.message and volume() == before


def check_the_thread_is_kept_as_flattened_metadata() -> None:
    plate = _plate()
    _tapped(plate, "M6", 5.0)
    node = call("feature_tree", op("q"))["nodes"][-1]
    assert node["kind"] == "hole"
    assert node["parameters"]["thread_size"] == "M6" and node["parameters"]["thread_depth_mm"] == 10.0
    assert "thread" not in node["parameters"]


def check_a_diameter_that_is_not_freecads_tap_drill_is_geometry_failed_and_rolled_back() -> None:
    plate = _plate()
    before = volume()
    error = expect_error(
        "geometry_failed",
        "hole",
        op(
            "tap",
            face=plate["top"]["handle"],
            position=[0, 0],
            standard="iso_tapped",
            size="M6",
            diameter_mm=5.2,
            end_condition="blind",
            depth_mm=15.0,
            thread={"size": "M6", "depth_mm": None},
        ),
    )
    assert "tap drill" in error.message and "5" in error.message and "5.2" in error.message, error.message
    assert volume() == before


def check_a_thread_size_freecad_does_not_know_is_not_supported() -> None:
    plate = _plate()
    before = volume()
    error = expect_error(
        "not_supported",
        "hole",
        op(
            "tap",
            face=plate["top"]["handle"],
            position=[0, 0],
            standard="iso_tapped",
            size="M99",
            diameter_mm=5.0,
            end_condition="blind",
            depth_mm=15.0,
            thread={"size": "M99", "depth_mm": None},
        ),
    )
    assert "M99" in error.message, error.message
    assert volume() == before


def check_a_tapped_hole_without_a_thread_depth_uses_the_hole_depth() -> None:
    plate = _plate()
    before = volume()
    _tapped(plate, "M8", 6.8, thread={"size": "M8", "depth_mm": None})
    assert abs((before - volume()) - math.pi * 3.4**2 * 15.0) < 1e-3


def _plain_rod() -> dict:
    new_document("rod")
    sk = sketch("rod_sk", "XY", [circle("c", (0, 0), 20)])
    return {"rod": extrude("rod", sk, 100)}


def check_a_hole_crossing_the_axial_end_is_refused_and_nothing_is_cut() -> None:
    rod = _plain_rod()
    before = volume()
    face = select_face(faces_of(rod["rod"]["feature"]), surface="cylinder")["handle"]
    for position in ([50, 0], [48.6, 0], [-48.6, 0], [48.5, 0]):
        error = expect_error(
            "invalid_request",
            "hole",
            op(
                "cross",
                face=face,
                position=position,
                standard="simple",
                size=None,
                diameter_mm=3.0,
                end_condition="blind",
                depth_mm=5.0,
            ),
        )
        assert "not fully on face" in error.message and "axial end" in error.message, error.message
        assert volume() == before
    _hole_on(face, [47, 0])  # clear of the end: drilled
    assert volume() < before


def _hole_on(face: dict, position: list) -> dict:
    return call(
        "hole",
        op(
            "cross",
            face=face,
            position=position,
            standard="simple",
            size=None,
            diameter_mm=3.0,
            end_condition="blind",
            depth_mm=5.0,
        ),
    )


def check_the_same_side_whatever_the_reported_axis_sense() -> None:
    """A rod extruded up and one extruded down are the same tube: angle 45 enters on the same world side."""
    sides = []
    for direction in ("positive", "negative"):
        new_document("rod")
        sk = sketch("rod_sk", "XY", [circle("c", (0, 0), 20)])
        rod = extrude("rod", sk, 100, direction=direction)
        face = select_face(faces_of(rod["feature"]), surface="cylinder")
        result = _hole_on(face["handle"], [0, 45])
        wall = select_face(faces_of(result["feature"], "created"), surface="cylinder")
        sides.append((wall["center"][0], wall["center"][1]))
    for cx, cy in sides:
        assert abs(cx - cy) < 0.05 and cx > 5.0, sides
