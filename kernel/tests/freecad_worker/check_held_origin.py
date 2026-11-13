"""A request on a planar face may name the origin its coordinates are measured from (``origin``): a rebuild puts a hole
or a sketch where it stood instead of measuring from the centre of the face as it is now. Plain asserts run inside
``freecadcmd`` (see ``run.py``)."""

import math
from typing import Any

import main
from check_edit_dimension import _hole_request, _plate
from harness import call, close, op

TOL = 1e-6  # mm


def _body_shape() -> Any:
    doc = main.WORKER.active
    return next(o for o in doc.fc_doc.Objects if o.TypeId == "PartDesign::Body").Shape


def _axes(diameter: float) -> list[tuple[float, float]]:
    found = []
    for face in _body_shape().Faces:
        surface = face.Surface
        if type(surface).__name__ == "Cylinder" and abs(surface.Radius * 2 - diameter) < 1e-6:
            found.append((surface.Center.x, surface.Center.y))
    return sorted(set(found))


def check_a_hole_with_an_origin_is_measured_from_it_and_slid_onto_the_face() -> None:
    ids = _plate()
    centre = call("face_info", op("q", face=ids["top"]["handle"]))["center"]
    request = _hole_request(ids["top"], 4.0, (25.0, 0.0))
    request["operation_id"] = "hole_2"
    request["origin"] = [centre[0] + 1.5, centre[1] - 0.5, centre[2] - 2.0]  # a point off the plane of the face
    result = call("hole", request)
    (axis,) = _axes(4.0)
    assert math.dist(axis, (centre[0] + 1.5 + 25.0, centre[1] - 0.5)) < TOL, axis
    place = result["place"]
    assert close(place["origin"][2], centre[2], TOL), "the origin was not slid onto the plane of the face"
    assert math.dist(place["origin"][:2], (centre[0] + 1.5, centre[1] - 0.5)) < TOL, place


def check_a_hole_without_an_origin_reports_the_centre_of_the_face_it_was_measured_from() -> None:
    ids = _plate()
    box = call("face_info", op("q", face=ids["top"]["handle"]))["bbox"]
    centre = [(a + b) / 2 for a, b in zip(box["min"], box["max"], strict=True)]  # the box, not the centre of mass
    request = _hole_request(ids["top"], 4.0, (25.0, 0.0))
    request["operation_id"] = "hole_2"
    result = call("hole", request)
    assert all(close(a, b, TOL) for a, b in zip(result["place"]["origin"], centre, strict=True)), result["place"]
    (axis,) = _axes(4.0)
    assert math.dist(axis, (centre[0] + 25.0, centre[1])) < TOL, axis


def check_a_sketch_with_an_origin_has_its_frame_there() -> None:
    ids = _plate()
    centre = call("face_info", op("q", face=ids["top"]["handle"]))["center"]
    wanted = [centre[0] + 2.0, centre[1] + 3.0, centre[2]]
    sk = call(
        "sketch",
        op(
            "on_top",
            on=ids["top"]["handle"],
            entities=[{"kind": "circle", "name": "c", "center": [0, 0], "diameter": 4}],
            constraints=[],
            named_dimensions=[],
            origin=wanted,
        ),
    )
    assert all(close(a, b, TOL) for a, b in zip(sk["frame"]["origin"], wanted, strict=True)), sk["frame"]
