"""Holes at several points in ONE ``hole`` call, and the origin their ``(u, v)`` is measured from.

Plain asserts run inside ``freecadcmd`` (see ``run.py``). Every position is read from the CAD: the axis of each
cylinder the feature leaves. The origin is the centre of the box of the face, not its centre of mass: the centre of
mass moves when a hole is drilled in the face (a D14 hole off the middle of a 100 x 60 plate moved it by 0.37 mm),
the box does not.
"""

import math
from typing import Any

import main
from harness import call, extrude, face_info, faces_of, new_document, op, rect, select_face, sketch

TOL = 1e-6  # mm
PLATE = (100.0, 60.0, 8.0)  # centred at (50, 30), top at z = 8


def _plate() -> dict[str, Any]:
    new_document("plate")
    sk = sketch("base_sketch", "XY", [rect("outline", (50, 30), PLATE[0], PLATE[1])])
    base = extrude("base", sk, PLATE[2])
    return {"base": base, "top": select_face(faces_of(base["feature"]), normal=(0, 0, 1))}


def _hole(top: dict[str, Any], name: str, diameter: float, **fields: Any) -> dict[str, Any]:
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
    )
    params.update(fields)
    return call("hole", params)


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
