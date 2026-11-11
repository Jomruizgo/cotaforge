"""Feature ops on bodies that were moved (``translate`` / ``rotate``) or copied, against the real FreeCAD worker.

A moved PartDesign body carries its pose in ``Body.Placement`` while its features live in body-local coordinates, so
every world-frame value the worker writes into a body-local object (sketch placement, datum lines, axis senses) has to
be expressed in the body frame. A moved *Part* body is baked into world geometry when it is upgraded to PartDesign.
Every expected volume below is derived by hand from the plate (100 x 60 x 8 = 48000 mm3).
"""

import math
from typing import Any

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.contract import FeatureRequest, MassRequest
from tests.integration.freecad.test_copy_body import (
    _assert_green,
    _asserted_ok,
    _bbox,
    _block,
    _body,
    _close,
    _copy,
    _mass,
    _plate,
    _result,
    _run,
)

pytestmark = pytest.mark.freecad

PLATE = 100.0 * 60.0 * 8.0
BOSS = 20.0 * 10.0 * 5.0  # the 20 x 10 boss, 5 mm tall
POCKET = 20.0 * 10.0 * 3.0
HOLE = math.pi * 5.0**2 * 8.0  # D10 through the 8 mm plate


def _move(kind: str, **fields: Any) -> dict[str, Any]:
    return {"id": "mv", "op": kind, "intent": "move", "target": {"kind": "body", "from_op": "base"}, **fields}


def _sketch_on(face: dict[str, Any]) -> dict[str, Any]:
    rect = {"kind": "rect", "name": "r", "center": [0, 0], "width": 20, "height": 10}
    return {"id": "fs", "op": "sketch", "intent": "i", "on": face, "entities": [rect]}


def _face(normal: list[float]) -> dict[str, Any]:
    return {"kind": "face", "from_op": "base", "select": {"normal": normal}}


def _boss() -> dict[str, Any]:
    return {"id": "boss", "op": "extrude", "intent": "i", "sketch": "fs", "depth_mm": 5}


def _pocket() -> dict[str, Any]:
    return {"id": "pocket", "op": "cut", "intent": "i", "sketch": "fs", "end_condition": "blind", "depth_mm": 3}


def _hole(normal: list[float]) -> dict[str, Any]:
    return {
        "id": "bore",
        "op": "hole",
        "intent": "i",
        "face": _face(normal),
        "position": [0, 0],
        "standard": "simple",
        "diameter_mm": 10,
        "end_condition": "through_all",
    }


# (label, move op, normal of the plate's top face after the move)
MOVES = [
    ("translate", _move("translate", offset=[0, 100, 0]), [0, 0, 1]),
    ("rotate_x90", _move("rotate", axis="X", angle_deg=90), [0, -1, 0]),
    ("rotate_z30", _move("rotate", axis="Z", angle_deg=30), [0, 0, 1]),
    ("rotate_x180", _move("rotate", axis="X", angle_deg=180), [0, 0, -1]),
]


@pytest.mark.parametrize(("label", "move", "top"), MOVES, ids=[m[0] for m in MOVES])
def test_a_boss_on_the_face_of_a_moved_partdesign_body(
    adapter: FreeCADAdapter, label: str, move: Any, top: Any
) -> None:
    _, trace = _run(adapter, *_plate(), move, _sketch_on(_face(top)), _boss())
    _assert_green(trace)
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(PLATE + BOSS, abs=1e-3), label
    assert "volume_delta" in _asserted_ok(trace, "boss")


@pytest.mark.parametrize(("label", "move", "top"), MOVES, ids=[m[0] for m in MOVES])
def test_a_pocket_in_the_face_of_a_moved_partdesign_body(
    adapter: FreeCADAdapter, label: str, move: Any, top: Any
) -> None:
    _, trace = _run(adapter, *_plate(), move, _sketch_on(_face(top)), _pocket())
    _assert_green(trace)
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(PLATE - POCKET, abs=1e-3), label


@pytest.mark.parametrize(("label", "move", "top"), MOVES, ids=[m[0] for m in MOVES])
def test_a_hole_in_the_face_of_a_moved_partdesign_body(
    adapter: FreeCADAdapter, label: str, move: Any, top: Any
) -> None:
    _, trace = _run(adapter, *_plate(), move, _hole(top))
    _assert_green(trace)
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(PLATE - HOLE, abs=1e-3), label
    assert {"hole_axis_direction", "hole_axis_position", "hole_radius"} <= _asserted_ok(trace, "bore")
    faces = adapter.faces_of(FeatureRequest(operation_id="q", feature=_result(trace, "bore").feature)).faces
    (wall,) = [f for f in faces if f.axis is not None]
    # a hole's axis sense points INTO the material: the opposite of the face's outward normal, in world terms
    assert _close(wall.axis.direction, tuple(-c for c in top), 1e-6), (label, wall.axis.direction)


def test_the_boss_of_a_translated_partdesign_body_sits_where_the_body_is(adapter: FreeCADAdapter) -> None:
    _, trace = _run(adapter, *_plate(), _move("translate", offset=[0, 100, 0]), _sketch_on(_face([0, 0, 1])), _boss())
    _assert_green(trace)
    assert trace.final_bbox is not None
    assert _close(trace.final_bbox.min, (0, 100, 0), 1e-6) and _close(trace.final_bbox.max, (100, 160, 13), 1e-6)


# --- Part bodies: the placement is baked when the body becomes a PartDesign body ---------------------------------


def test_extrude_on_a_rotated_part_body(adapter: FreeCADAdapter) -> None:
    move = {
        "id": "mv",
        "op": "rotate",
        "intent": "i",
        "target": {"kind": "body", "from_op": "block"},
        "axis": "Z",
        "angle_deg": 90,
    }
    top = {"kind": "face", "from_op": "block", "select": {"normal": [0, 0, 1]}}
    rect = {"kind": "rect", "name": "r", "center": [0, 0], "width": 4, "height": 4}
    sketch = {"id": "fs", "op": "sketch", "intent": "i", "on": top, "entities": [rect]}
    _, trace = _run(adapter, _block(), move, sketch, _boss())
    _assert_green(trace)
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(6000.0 + 4 * 4 * 5, abs=1e-3)
    assert trace.final_bbox is not None and _close(trace.final_bbox.min, (-20, 0, 0), 1e-6)


def test_a_hole_through_the_original_face_ref_of_a_translated_part_body(adapter: FreeCADAdapter) -> None:
    """The face id taken before the bake still resolves after it (same geometry, same identity)."""
    move = {
        "id": "mv",
        "op": "translate",
        "intent": "i",
        "target": {"kind": "body", "from_op": "block"},
        "offset": [0, 100, 0],
    }
    top = {"kind": "face", "from_op": "block", "select": {"normal": [0, 0, 1]}}
    bore = {
        "id": "bore",
        "op": "hole",
        "intent": "i",
        "face": top,
        "position": [0, 0],
        "standard": "simple",
        "diameter_mm": 6,
        "end_condition": "through_all",
    }
    _, trace = _run(adapter, _block(), move, bore)
    _assert_green(trace)
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(6000.0 - math.pi * 9 * 30, abs=1e-3)
    faces = adapter.faces_of(FeatureRequest(operation_id="q", feature=_result(trace, "block").feature)).faces
    top_face = next(f for f in faces if f.normal is not None and _close(f.normal, (0, 0, 1)))
    assert top_face.center == pytest.approx(
        (5.0, 110.0, 30.0), abs=1e-6
    )  # the block's own face record followed the move


def test_an_unmoved_body_takes_the_old_path_and_keeps_its_place(adapter: FreeCADAdapter) -> None:
    top = {"kind": "face", "from_op": "block", "select": {"normal": [0, 0, 1]}}
    rect = {"kind": "rect", "name": "r", "center": [0, 0], "width": 4, "height": 4}
    sketch = {"id": "fs", "op": "sketch", "intent": "i", "on": top, "entities": [rect]}
    _, trace = _run(adapter, _block(), sketch, _boss())
    _assert_green(trace)
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(6000.0 + 80.0, abs=1e-3)
    assert trace.final_bbox is not None
    assert _close(trace.final_bbox.min, (0, 0, 0), 1e-9) and _close(trace.final_bbox.max, (10, 20, 35), 1e-9)


# --- copies of copies, copies of moved bodies, snapshot semantics -----------------------------------------------


def test_a_copy_of_a_rotated_copy_lands_where_the_hand_math_says(adapter: FreeCADAdapter) -> None:
    """Block (0..10, 0..20, 0..30), centroid (5, 10, 15). First copy: +100 in Y -> (5, 110, 15). Second copy of that
    copy: rotate 90 deg about Z through the origin, (x, y) -> (-y, x) -> (-110, 5, 15)."""
    second = _copy("twin2", source="twin", rotate={"axis": "Z", "angle_deg": 90})
    _, trace = _run(adapter, _block(), _copy(offset=[0, 100, 0]), second)
    _assert_green(trace)
    twin2 = _body(trace, "twin2")
    assert _close(_mass(adapter, twin2).centroid, (-110, 5, 15), 1e-6)
    box = _bbox(adapter, twin2)
    assert _close(box.min, (-120, 0, 0), 1e-6) and _close(box.max, (-100, 10, 30), 1e-6)
    assert _mass(adapter, twin2).volume_mm3 == pytest.approx(6000.0, abs=1e-6)
    assert _close(_mass(adapter, _body(trace, "twin")).centroid, (5, 110, 15), 1e-6)


def test_a_copy_of_a_moved_partdesign_body_has_the_moved_pose_and_the_source_mass(adapter: FreeCADAdapter) -> None:
    move = _move("translate", offset=[0, 100, 0])
    twin = _copy("twin", source="base", offset=[0, 0, 50])
    _, trace = _run(adapter, *_plate(), move, twin)
    _assert_green(trace)
    copy = _body(trace, "twin")
    assert _mass(adapter, copy).volume_mm3 == pytest.approx(PLATE, abs=1e-6)
    assert _close(_mass(adapter, copy).centroid, (50, 130, 54), 1e-6)  # plate centroid (50, 30, 4) + (0, 100, 50)
    assert _close(_mass(adapter, _body(trace, "base")).centroid, (50, 130, 4), 1e-6)


def test_the_copy_of_a_rotated_partdesign_cylinder_hole_keeps_the_axis_sense(adapter: FreeCADAdapter) -> None:
    """Axis senses of a placed PartDesign source are body-local; the copy must carry them in world terms."""
    move = _move("rotate", axis="X", angle_deg=180)
    twin = _copy("twin", source="base", offset=[0, 0, 200])
    _, trace = _run(adapter, *_plate(), _hole([0, 0, 1]) | {"id": "bore"}, move, twin)
    _assert_green(trace)
    faces = adapter.faces_of(FeatureRequest(operation_id="q", feature=_result(trace, "twin").feature)).faces
    (side,) = [f for f in faces if f.axis is not None]
    # the bore sense was -Z (into the plate from its top face); turning the plate 180 about X makes it +Z, and the
    # copy (offset only) is the same turned body
    assert _close(side.axis.direction, (0, 0, 1), 1e-6), side.axis
    source_faces = adapter.faces_of(FeatureRequest(operation_id="q", feature=_result(trace, "bore").feature)).faces
    (source_side,) = [f for f in source_faces if f.axis is not None]
    assert _close(side.axis.direction, source_side.axis.direction, 1e-6)


def test_a_copy_is_a_snapshot_of_its_source(adapter: FreeCADAdapter) -> None:
    """The source grows a boss after the copy was taken; the copy keeps its volume."""
    top = {"kind": "face", "from_op": "block", "select": {"normal": [0, 0, 1]}}
    rect = {"kind": "rect", "name": "r", "center": [0, 0], "width": 4, "height": 4}
    sketch = {"id": "fs", "op": "sketch", "intent": "i", "on": top, "entities": [rect]}
    _, trace = _run(adapter, _block(), _copy(offset=[0, 100, 0]), sketch, _boss())
    _assert_green(trace)
    assert _mass(adapter, _body(trace, "twin")).volume_mm3 == pytest.approx(6000.0, abs=1e-6)
    assert _mass(adapter, _body(trace, "block")).volume_mm3 == pytest.approx(6000.0 + 80.0, abs=1e-3)
    assert adapter.mass_properties(MassRequest(operation_id="q")).volume_mm3 == pytest.approx(12080.0, abs=1e-3)


# --- patterns and dress-up on a moved PartDesign body --------------------------------------------------------------

SEED = {
    "id": "seed",
    "op": "hole",
    "intent": "i",
    "face": _face([0, 0, 1]),
    "position": [-20, 0],
    "standard": "simple",
    "diameter_mm": 10,
    "end_condition": "through_all",
}
C30, S30 = math.cos(math.radians(30)), math.sin(math.radians(30))


def _moved(label: str, p: tuple[float, float, float]) -> tuple[float, float, float]:
    """A point of the unmoved plate after the named move (hand-written rotation matrices, see MOVES)."""
    x, y, z = p
    return {
        "translate": (x, y + 100.0, z),
        "rotate_x90": (x, -z, y),
        "rotate_z30": (x * C30 - y * S30, x * S30 + y * C30, z),
        "rotate_x180": (x, -y, -z),
    }[label]


def _bore_axes(adapter: FreeCADAdapter, trace: Any, op_id: str) -> list[Any]:
    faces = adapter.faces_of(FeatureRequest(operation_id="q", feature=_result(trace, op_id).feature)).faces
    return [f.axis for f in faces if f.axis is not None]


def _on_axis(point: tuple[float, float, float], axis: Any, tol: float = 1e-6) -> bool:
    """Whether ``point`` lies on the axis line (distance from the line, not from the origin point)."""
    rel = [p - o for p, o in zip(point, axis.origin, strict=True)]
    along = sum(r * d for r, d in zip(rel, axis.direction, strict=True))
    return math.dist(rel, [along * d for d in axis.direction]) <= tol


SIMPLE_MOVES = MOVES[:3]  # translate, rotate 90 about X, rotate 30 about Z
# (top face normal, +X face normal) of the plate AFTER each move
FACE_NORMALS = {
    "translate": ([0, 0, 1], [1, 0, 0]),
    "rotate_x90": ([0, -1, 0], [1, 0, 0]),
    "rotate_z30": ([0, 0, 1], [C30, S30, 0]),
}


@pytest.mark.parametrize(("label", "move"), [(m[0], m[1]) for m in SIMPLE_MOVES], ids=[m[0] for m in SIMPLE_MOVES])
def test_a_linear_pattern_on_a_moved_partdesign_body(adapter: FreeCADAdapter, label: str, move: Any) -> None:
    pattern = {
        "id": "row",
        "op": "linear_pattern",
        "intent": "i",
        "features": ["seed"],
        "direction": "X",
        "spacing_mm": 30,
        "count": 2,
    }
    _, trace = _run(adapter, *_plate(), SEED, move, pattern)
    _assert_green(trace)
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(PLATE - 2 * HOLE, abs=1e-3), label
    # the seed bore sits at (30, 30) of the unmoved plate; the pattern copies it 30 mm along WORLD +X
    seed = _moved(label, (30.0, 30.0, 4.0))
    second = (seed[0] + 30.0, seed[1], seed[2])
    bores = _bore_axes(adapter, trace, "row")
    assert any(_on_axis(second, axis) for axis in bores), (label, second, bores)
    assert any(_on_axis(seed, axis) for axis in _bore_axes(adapter, trace, "seed")), (label, seed)


def test_a_circular_pattern_about_a_world_axis_on_a_translated_partdesign_body(adapter: FreeCADAdapter) -> None:
    """The plate centre (50, 30) moved to (50, 130): the axis is given in world terms, the four holes of radius 20
    around it stay inside the plate."""
    axis = {"origin": [50, 130, 0], "direction": [0, 0, 1]}
    ring = {"id": "ring", "op": "circular_pattern", "intent": "i", "features": ["seed"], "axis": axis, "count": 4}
    _, trace = _run(adapter, *_plate(), SEED, _move("translate", offset=[0, 100, 0]), ring)
    _assert_green(trace)
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(PLATE - 4 * HOLE, abs=1e-3)
    assert trace.final_bbox is not None
    assert _close(trace.final_bbox.min, (0, 100, 0), 1e-6) and _close(trace.final_bbox.max, (100, 160, 8), 1e-6)


def _edge(top: list[float], side: list[float]) -> dict[str, Any]:
    return {"kind": "edge", "between": [_face(top), _face(side)]}


@pytest.mark.parametrize(("label", "move"), [(m[0], m[1]) for m in SIMPLE_MOVES], ids=[m[0] for m in SIMPLE_MOVES])
def test_a_fillet_on_the_edge_of_a_moved_partdesign_body(adapter: FreeCADAdapter, label: str, move: Any) -> None:
    top, side = FACE_NORMALS[label]
    fillet = {"id": "round", "op": "fillet", "intent": "i", "edges": [_edge(top, side)], "radius_mm": 2}
    _, trace = _run(adapter, *_plate(), move, fillet)
    _assert_green(trace)
    assert trace.final_mass is not None
    removed = (1 - math.pi / 4) * 2**2 * 60.0  # (1 - pi/4) r^2 L along the 60 mm edge
    assert trace.final_mass.volume_mm3 == pytest.approx(PLATE - removed, abs=1e-3), label


@pytest.mark.parametrize(("label", "move"), [(m[0], m[1]) for m in SIMPLE_MOVES], ids=[m[0] for m in SIMPLE_MOVES])
def test_a_two_distance_chamfer_on_the_edge_of_a_moved_partdesign_body(
    adapter: FreeCADAdapter, label: str, move: Any
) -> None:
    top, side = FACE_NORMALS[label]
    chamfer = {
        "id": "bevel",
        "op": "chamfer",
        "intent": "i",
        "edges": [_edge(top, side)],
        "distance1_mm": 3,
        "distance2_mm": 2,
        "distance1_on": _face(top),
    }
    _, trace = _run(adapter, *_plate(), move, chamfer)
    _assert_green(trace)
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(PLATE - 0.5 * 3 * 2 * 60.0, abs=1e-3), label
    # distance1 (3 mm) runs on the top face, so it loses 3 x 60 mm2 and the +X face loses 2 x 60; a swapped pair of
    # distances gives the same volume but 5880 / 6060 instead
    faces = adapter.faces_of(FeatureRequest(operation_id="q", feature=_result(trace, "base").feature)).faces
    (top_face,) = [f for f in faces if f.normal is not None and _close(f.normal, top, 1e-6) and f.area_mm2 > 5000]
    (side_face,) = [f for f in faces if f.normal is not None and _close(f.normal, side, 1e-6) and f.area_mm2 > 300]
    assert top_face.area_mm2 == pytest.approx(6000.0 - 3 * 60.0, abs=1e-6), label
    assert side_face.area_mm2 == pytest.approx(60.0 * 8 - 2 * 60.0, abs=1e-6), label


# --- a circular pattern on rotated bodies, a revolve and a datum-plane cut on moved bodies -----------------------


@pytest.mark.parametrize("label", ["rotate_x90", "rotate_z30"])
def test_a_circular_pattern_on_a_rotated_partdesign_body(adapter: FreeCADAdapter, label: str) -> None:
    """The plate centre (50, 30, 4) goes where the rotation puts it; the seed bore is 20 mm from it, so four holes
    about the world axis through that centre (along the plate normal) fall on the same circle."""
    move = next(m[1] for m in MOVES if m[0] == label)
    centre = _moved(label, (50.0, 30.0, 4.0))
    direction = {"rotate_x90": [0, 1, 0], "rotate_z30": [0, 0, 1]}[label]
    axis = {"origin": list(centre), "direction": direction}
    ring = {"id": "ring", "op": "circular_pattern", "intent": "i", "features": ["seed"], "axis": axis, "count": 4}
    _, trace = _run(adapter, *_plate(), SEED, move, ring)
    _assert_green(trace)
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(PLATE - 4 * HOLE, abs=1e-3), label
    axes = _bore_axes(adapter, trace, "ring")
    # the holes the ring adds are the seed turned by 90, 180 and 270 degrees about the world axis: check the
    # opposite one, (seed -> 2 * centre - seed), which is the same for either turn direction
    seed = _moved(label, (30.0, 30.0, 4.0))
    opposite = tuple(2 * c - s for c, s in zip(centre, seed, strict=True))
    assert any(_on_axis(opposite, a) for a in axes), (label, opposite)


def test_a_revolve_merging_into_a_rotated_partdesign_body(adapter: FreeCADAdapter) -> None:
    """A shaft revolved about the sketch u axis (world X, length 20, radius 10) is turned 90 degrees about Z so it
    stands along +Y; a second revolve about the sketch v axis (world Y) adds a 10 mm collar of radius 14 at its
    end: 2000 pi + pi * 14^2 * 10 = 3960 pi (a wider collar, so its side face does not merge with the shaft)."""
    shaft_profile = {"kind": "rect", "name": "p", "center": [10, 5], "width": 20, "height": 10}
    collar_profile = {"kind": "rect", "name": "c", "center": [7, 25], "width": 14, "height": 10}
    shaft_sketch = {"id": "s1", "op": "sketch", "intent": "i", "on": {"kind": "plane", "name": "XY"}}
    collar_sketch = {"id": "s2", "op": "sketch", "intent": "i", "on": {"kind": "plane", "name": "XY"}}
    shaft = {"id": "shaft", "op": "revolve", "intent": "i", "sketch": "s1", "axis": "u", "angle_deg": 360}
    collar = {"id": "collar", "op": "revolve", "intent": "i", "sketch": "s2", "axis": "v", "angle_deg": 360}
    turn = {"id": "mv", "op": "rotate", "intent": "i", "target": {"kind": "body", "from_op": "shaft"}}
    turn |= {"axis": "Z", "angle_deg": 90}
    _, trace = _run(
        adapter,
        shaft_sketch | {"entities": [shaft_profile]},
        shaft,
        turn,
        collar_sketch | {"entities": [collar_profile]},
        collar,
    )
    _assert_green(trace)
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(3960.0 * math.pi, abs=1e-3)
    assert trace.final_bbox is not None
    assert _close(trace.final_bbox.min, (-14, 0, -14), 1e-6) and _close(trace.final_bbox.max, (14, 30, 14), 1e-6)


DATUM_CUTS = [
    # label, move, datum offset from XY (the plate face it lies on), cut direction into the plate, rect centre (x, y)
    ("translate", _move("translate", offset=[0, 100, 0]), 8, "negative", (50, 130)),
    ("rotate_z30", _move("rotate", axis="Z", angle_deg=30), 8, "negative", (50 * C30 - 30 * S30, 50 * S30 + 30 * C30)),
    ("rotate_x180", _move("rotate", axis="X", angle_deg=180), 0, "negative", (50, -30)),
]


@pytest.mark.parametrize(("label", "move", "offset", "direction", "centre"), DATUM_CUTS, ids=[d[0] for d in DATUM_CUTS])
def test_a_cut_sketched_on_a_datum_plane_through_a_moved_partdesign_body(
    adapter: FreeCADAdapter, label: str, move: Any, offset: float, direction: str, centre: Any
) -> None:
    """The datum is a world plane (z = offset): the 20 x 10 pocket is centred on the moved plate and 3 mm deep."""
    datum = {
        "id": "dp",
        "op": "ref_plane",
        "intent": "i",
        "define": {"how": "offset", "base": {"kind": "plane", "name": "XY"}, "distance_mm": offset},
    }
    rect = {"kind": "rect", "name": "r", "center": list(centre), "width": 20, "height": 10}
    sketch = {"id": "ds", "op": "sketch", "intent": "i", "on": {"kind": "plane", "from_op": "dp"}, "entities": [rect]}
    pocket = {
        "id": "pocket",
        "op": "cut",
        "intent": "i",
        "sketch": "ds",
        "end_condition": "blind",
        "depth_mm": 3,
        "direction": direction,
    }
    _, trace = _run(adapter, *_plate(), move, datum, sketch, pocket)
    _assert_green(trace)
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(PLATE - POCKET, abs=1e-3), label
    floor = [
        f
        for f in adapter.faces_of(FeatureRequest(operation_id="q", feature=_result(trace, "pocket").feature)).faces
        if f.normal is not None and f.area_mm2 == pytest.approx(200.0, abs=1e-6)
    ]
    assert len(floor) == 1, label  # the pocket floor, 20 x 10
    assert floor[0].center[2] == pytest.approx(offset - 3.0, abs=1e-6), label  # 3 mm below the datum, along -Z
