"""Holes at several points in ONE ``hole`` call, and the origin their ``(u, v)`` is measured from.

Plain asserts run inside ``freecadcmd`` (see ``run.py``). Every position is read from the CAD: the axis of each
cylinder the feature leaves. The origin is the centre of the box of the face, not its centre of mass: the centre of
mass moves when a hole is drilled in the face (a D14 hole off the middle of a 100 x 60 plate moved it by 0.37 mm),
the box does not.
"""

import math
from typing import Any

import main
from harness import call, extrude, face_info, faces_of, line, new_document, op, rect, select_face, sketch

TOL = 1e-6  # mm
PLATE = (100.0, 60.0, 8.0)  # centred at (50, 30), top at z = 8


def _plate() -> dict[str, Any]:
    new_document("plate")
    sk = sketch("base_sketch", "XY", [rect("outline", (50, 30), PLATE[0], PLATE[1])])
    base = extrude("base", sk, PLATE[2])
    return {"base": base, "top": select_face(faces_of(base["feature"]), normal=(0, 0, 1))}


def _box_origin(top: dict[str, Any]) -> list[float]:
    """The origin the executor sends for a hole: the centre of the box of the face (``stable_face_origin``)."""
    box = face_info(top["handle"])["bbox"]
    return [(a + b) / 2 for a, b in zip(box["min"], box["max"], strict=True)]


def _params(top: dict[str, Any], name: str, diameter: float, *, origin: Any = "box", **fields: Any) -> dict[str, Any]:
    params = op(
        name,
        face=top["handle"],
        standard="simple",
        size=None,
        diameter_mm=diameter,
        end_condition="through_all",
        depth_mm=None,
        thread=None,
        counterbore=None,
        countersink=None,
        drill_point="flat",
        drill_point_angle_deg=118.0,
        origin=_box_origin(top) if origin == "box" else origin,
    )
    params.update(fields)
    return params


def _hole(top: dict[str, Any], name: str, diameter: float, **kw: Any) -> dict[str, Any]:
    return call("hole", _params(top, name, diameter, **kw))


def _edit(feature: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    result = call(
        "edit_feature", op("edit", edits=[{"feature": feature, "kind": "hole", "request": request}], dry_run=False)
    )
    assert result["applied"], result
    return result


def _axes(diameter: float) -> list[tuple[float, float]]:
    """World (x, y) of the axis of every cylindrical face of ``diameter``, sorted."""
    doc = main.WORKER.active
    body = next(o for o in doc.fc_doc.Objects if o.TypeId == "PartDesign::Body")
    found = set()
    for face in body.Shape.Faces:
        surface = face.Surface
        if type(surface).__name__ == "Cylinder" and abs(surface.Radius * 2 - diameter) < 1e-6:
            found.add((round(surface.Center.x, 9), round(surface.Center.y, 9)))
    return sorted(found)


def _same(found: list[tuple[float, float]], wanted: list[tuple[float, float]]) -> None:
    wanted = sorted(wanted)
    assert len(found) == len(wanted), (found, wanted)
    for got, want in zip(found, wanted, strict=True):
        assert math.dist(got, want) <= TOL, f"axis at {got}, asked for {want} ({math.dist(got, want):.6f} mm off)"


def check_the_second_hole_of_a_face_is_measured_from_the_same_point_as_the_first() -> None:
    ids = _plate()
    _hole(ids["top"], "hole_1", 14.0, position=[-30.0, -20.0])
    centre_of_mass = face_info(ids["top"]["handle"])["center"]
    assert math.dist(centre_of_mass[:2], (50.0, 30.0)) > 0.3, "the hole did not move the centre of mass of the face"
    _hole(ids["top"], "hole_2", 4.0, position=[0.0, 0.0])  # (0, 0): the middle of the plate, however many holes
    _same(_axes(4.0), [(50.0, 30.0)])
    _same(_axes(14.0), [(20.0, 10.0)])


def check_four_holes_of_a_rectangular_pattern_in_one_call() -> None:
    ids = _plate()
    corners = [(-30.0, -15.0), (30.0, -15.0), (30.0, 15.0), (-30.0, 15.0)]
    result = _hole(ids["top"], "pattern", 6.0, position=list(corners[0]), positions=[list(c) for c in corners])
    assert result["feature"], result
    _same(_axes(6.0), [(50.0 + u, 30.0 + v) for u, v in corners])
    body = next(o for o in main.WORKER.active.fc_doc.Objects if o.TypeId == "PartDesign::Body")
    holes = [o for o in body.Group if o.TypeId == "PartDesign::Hole"]
    assert len(holes) == 1, [o.Name for o in holes]  # ONE feature
    expected = PLATE[0] * PLATE[1] * PLATE[2] - 4 * math.pi * 3.0**2 * PLATE[2]
    assert abs(body.Shape.Volume - expected) < 1e-3, (body.Shape.Volume, expected)


def check_six_holes_of_a_bolt_circle_in_one_call() -> None:
    ids = _plate()
    radius, centre = 17.5, (10.0, 0.0)
    points = [
        (centre[0] + radius * math.cos(math.radians(60 * i)), centre[1] + radius * math.sin(math.radians(60 * i)))
        for i in range(6)
    ]
    _hole(ids["top"], "circle", 5.0, position=list(points[0]), positions=[list(p) for p in points])
    _same(_axes(5.0), [(50.0 + u, 30.0 + v) for u, v in points])
    for x, y in _axes(5.0):
        assert abs(math.dist((x, y), (50.0 + centre[0], 30.0 + centre[1])) - radius) <= TOL
    # what the drawing reads (``list_holes``, from the solid) sees six holes, as for six separate features
    listed = call("list_holes", op("q"))["holes"]
    assert len(listed) == 6, listed


def check_a_counterbore_applies_to_every_hole_of_the_call() -> None:
    ids = _plate()
    points = [(-20.0, 0.0), (20.0, 0.0)]
    _hole(
        ids["top"],
        "cbore",
        6.0,
        position=list(points[0]),
        positions=[list(p) for p in points],
        counterbore={"diameter_mm": 11.0, "depth_mm": 3.0},
    )
    _same(_axes(6.0), [(50.0 + u, 30.0 + v) for u, v in points])
    _same(_axes(11.0), [(50.0 + u, 30.0 + v) for u, v in points])


def check_a_request_without_an_origin_is_measured_from_the_centre_of_mass_as_it_always_was() -> None:
    """What a model saved before the change recorded: no origin. Rebuilt, it puts the hole where it was."""
    ids = _plate()
    _hole(ids["top"], "hole_1", 14.0, origin=None, position=[-30.0, -20.0])
    centre_of_mass = face_info(ids["top"]["handle"])["center"]
    second = _hole(ids["top"], "hole_2", 4.0, origin=None, position=[0.0, 0.0])
    _same(_axes(4.0), [tuple(centre_of_mass[:2])])  # the old frame: it drifted, and it still does for these
    before = _axes(4.0)
    # an edit in place of its diameter, with the request as it was recorded (no origin), keeps it where it was
    _edit(second["feature"], _params(ids["top"], "hole_2", 8.0, origin=None, position=[0.0, 0.0]))
    _same(_axes(8.0), before)


def _l_plate() -> dict[str, Any]:
    new_document("lplate")
    points = [(0, 0), (100, 0), (100, 20), (20, 20), (20, 60), (0, 60)]
    lines = [line(f"l{i}", points[i], points[(i + 1) % len(points)]) for i in range(len(points))]
    base = extrude("base", sketch("base_sketch", "XY", lines), PLATE[2])
    return {"base": base, "top": select_face(faces_of(base["feature"]), normal=(0, 0, 1))}


def check_the_origin_on_an_l_shaped_face_is_its_box_centre_even_outside_the_material() -> None:
    ids = _l_plate()
    origin = _box_origin(ids["top"])
    assert math.dist(origin[:2], (50.0, 30.0)) <= TOL  # in the notch: no material there
    mass = face_info(ids["top"]["handle"])["center"]
    assert math.dist(mass[:2], (50.0, 30.0)) > 5.0  # the centre of mass is elsewhere
    points = [(-40.0, 20.0), (-40.0, 10.0)]  # world (10, 50) and (10, 40): on the upright of the L
    _hole(ids["top"], "pair", 5.0, position=list(points[0]), positions=[list(p) for p in points])
    _same(_axes(5.0), [(50.0 + u, 30.0 + v) for u, v in points])
    _hole(ids["top"], "foot", 6.0, position=[30.0, -20.0])  # world (80, 10), on the foot, a second hole in the face
    _same(_axes(6.0), [(80.0, 10.0)])


def check_a_multi_hole_feature_is_edited_in_place_by_its_diameter_and_its_counterbore() -> None:
    ids = _plate()
    corners = [(-30.0, -15.0), (30.0, -15.0), (30.0, 15.0), (-30.0, 15.0)]
    made = _hole(
        ids["top"],
        "pattern",
        6.0,
        position=list(corners[0]),
        positions=[list(c) for c in corners],
        counterbore={"diameter_mm": 11.0, "depth_mm": 3.0},
    )
    before = _axes(6.0)
    _edit(
        made["feature"],
        _params(
            ids["top"],
            "pattern",
            8.0,
            position=list(corners[0]),
            positions=[list(c) for c in corners],
            counterbore={"diameter_mm": 14.0, "depth_mm": 3.0},
        ),
    )
    _same(_axes(8.0), before)  # every circle took the new diameter, none moved
    _same(_axes(14.0), before)
    assert not _axes(6.0) and not _axes(11.0)
    body = next(o for o in main.WORKER.active.fc_doc.Objects if o.TypeId == "PartDesign::Body")
    assert len([o for o in body.Group if o.TypeId == "PartDesign::Hole"]) == 1


def check_a_hole_moved_in_place_leaves_the_features_after_it_where_they_are() -> None:
    ids = _plate()
    first = _hole(ids["top"], "first", 8.0, position=[-30.0, -20.0])
    _hole(ids["top"], "second", 6.0, position=[30.0, -20.0])
    _hole(ids["top"], "third", 5.0, position=[30.0, 20.0])
    result = _edit(first["feature"], _params(ids["top"], "first", 8.0, position=[-25.0, -12.5]))
    assert not result.get("failure"), result
    _same(_axes(8.0), [(25.0, 17.5)])
    _same(_axes(6.0), [(80.0, 10.0)])  # the ones made after it did not move
    _same(_axes(5.0), [(80.0, 50.0)])
    expected = PLATE[0] * PLATE[1] * PLATE[2] - math.pi * PLATE[2] * (4.0**2 + 3.0**2 + 2.5**2)
    body = next(o for o in main.WORKER.active.fc_doc.Objects if o.TypeId == "PartDesign::Body")
    assert abs(body.Shape.Volume - expected) < 1e-3, (body.Shape.Volume, expected)
    # and back to where it was: the same plate as the first time
    _edit(first["feature"], _params(ids["top"], "first", 8.0, position=[-30.0, -20.0]))
    _same(_axes(8.0), [(20.0, 10.0)])


def check_the_points_of_a_multi_hole_feature_move_in_place_one_by_one() -> None:
    ids = _plate()
    corners = [(-30.0, -15.0), (30.0, -15.0), (30.0, 15.0), (-30.0, 15.0)]
    made = _hole(ids["top"], "pattern", 6.0, position=list(corners[0]), positions=[list(c) for c in corners])
    moved = [(-30.0, -15.0), (35.0, -15.0), (35.0, 10.0), (-30.0, 10.0)]
    _edit(
        made["feature"],
        _params(ids["top"], "pattern", 6.0, position=list(moved[0]), positions=[list(c) for c in moved]),
    )
    _same(_axes(6.0), [(50.0 + u, 30.0 + v) for u, v in moved])


def check_a_hole_moved_onto_an_axis_of_its_sketch_is_not_edited_in_place() -> None:
    """There is no distance for a centre on the axis: the edit says so and the caller rebuilds."""
    ids = _plate()
    first = _hole(ids["top"], "first", 8.0, position=[-30.0, -20.0])
    dry = call(
        "edit_feature",
        op(
            "edit",
            edits=[
                {
                    "feature": first["feature"],
                    "kind": "hole",
                    "request": _params(ids["top"], "first", 8.0, position=[0.0, -20.0]),
                }
            ],
            dry_run=True,
        ),
    )
    assert dry["refused"], dry


def _try_edit(feature: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    return call(
        "edit_feature", op("edit", edits=[{"feature": feature, "kind": "hole", "request": request}], dry_run=False)
    )


def _body_volume() -> float:
    body = next(o for o in main.WORKER.active.fc_doc.Objects if o.TypeId == "PartDesign::Body")
    return float(body.Shape.Volume)


def check_a_hole_moved_off_the_face_is_rolled_back_with_the_message_of_a_rebuild() -> None:
    ids = _plate()
    first = _hole(ids["top"], "first", 8.0, position=[-30.0, -20.0])
    _hole(ids["top"], "second", 6.0, position=[30.0, -20.0])
    volume = _body_volume()
    result = _try_edit(first["feature"], _params(ids["top"], "first", 8.0, position=[-60.0, -20.0]))  # plate is +-50
    assert not result["applied"], result
    assert "is not on face" in str(result.get("failure") or result), result
    _same(_axes(8.0), [(20.0, 10.0)])  # nothing moved, nothing stuck
    _same(_axes(6.0), [(80.0, 10.0)])
    assert abs(_body_volume() - volume) < 1e-6


def check_a_hole_moved_over_a_hole_made_after_it_is_rolled_back() -> None:
    ids = _plate()
    first = _hole(ids["top"], "first", 8.0, position=[-30.0, -20.0])
    _hole(ids["top"], "second", 6.0, position=[30.0, -20.0])
    volume = _body_volume()
    result = _try_edit(first["feature"], _params(ids["top"], "first", 8.0, position=[30.0, -20.0]))
    assert not result["applied"], result
    assert "is not on face" in str(result.get("failure") or result), result
    _same(_axes(8.0), [(20.0, 10.0)])
    _same(_axes(6.0), [(80.0, 10.0)])
    assert abs(_body_volume() - volume) < 1e-6
