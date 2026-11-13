# ruff: noqa: E501
"""Every face of a plain hole is painted in its class colour, whatever opens it: the counterbore, the countersink and the
floor of a blind hole, made by the ``hole`` operation or as a pocket (a circle on the face, cut) concentric with the hole.

The expected faces are found from the geometry of the solid (a cylinder, a cone or a circle-bounded plane about the axis of
each hole), not from the registry the paint is read from, so the two can disagree and the check says so. Headless: the per-face
``DiffuseColor`` of the window is the same answer (``hole_colours.sync``), checked in a visible FreeCAD by
``tests/integration/freecad/test_hole_colours_visible.py``."""

from typing import Any

import hole_colours
import main
from harness import call, circle, extrude, faces_of, new_document, op, rect, select_face, sketch

GREEN = (0, 192, 0)
PLATE = (140.0, 60.0, 10.0)
AXIS_Z = (0.0, 0.0, 1.0)


def _rec() -> Any:
    return main.WORKER.active


def _drill(base: dict[str, Any], where: tuple[float, float], diameter: float, **extra: Any) -> dict[str, Any]:
    face = select_face(faces_of(base["feature"]), normal=(0, 0, 1))
    centre = face["center"]
    fields: dict[str, Any] = {
        "standard": "simple",
        "size": None,
        "end_condition": "through_all",
        "depth_mm": None,
        "hole_class": "plain",
        "precision_grade": 8,
        "colour": list(GREEN),
    }
    fields.update(extra)
    return call(
        "hole",
        op(
            f"h{where[0]:g}_{where[1]:g}",
            face=face["handle"],
            position=[where[0] - centre[0], where[1] - centre[1]],
            diameter_mm=diameter,
            **fields,
        ),
    )


def _pocket(base: dict[str, Any], where: tuple[float, float], diameter: float, depth: float) -> dict[str, Any]:
    face = select_face(faces_of(base["feature"]), normal=(0, 0, 1))
    centre = face["center"]
    shape = sketch(
        f"cb{where[0]:g}_{where[1]:g}",
        face["handle"],
        [circle("cb", (where[0] - centre[0], where[1] - centre[1]), diameter)],
    )
    return call(
        "cut",
        op(
            f"pocket{where[0]:g}_{where[1]:g}",
            sketch=shape["sketch"],
            end_condition="blind",
            depth_mm=depth,
            direction="negative",
            both_directions=False,
            depth2_mm=None,
        ),
    )


def _near(a: float, b: float) -> bool:
    return abs(a - b) < 1e-3


def _belongs(face: Any, where: tuple[float, float]) -> bool:
    """The face turns about the vertical axis through ``where``: found from the geometry only."""
    surface = face.Surface
    kind = type(surface).__name__
    if kind == "Cylinder":
        return (
            _near(abs(surface.Axis.z), 1.0) and _near(surface.Center.x, where[0]) and _near(surface.Center.y, where[1])
        )
    if kind == "Cone":
        return _near(abs(surface.Axis.z), 1.0) and _near(surface.Apex.x, where[0]) and _near(surface.Apex.y, where[1])
    if kind == "Plane":
        return (
            _near(abs(surface.Axis.z), 1.0)
            and bool(face.Edges)
            and all(
                type(e.Curve).__name__ == "Circle"
                and _near(e.Curve.Center.x, where[0])
                and _near(e.Curve.Center.y, where[1])
                for e in face.Edges
            )
        )
    return False


def _faces_by_hole(
    holes: dict[str, tuple[float, float]],
) -> tuple[dict[str, set[int]], dict[int, tuple[int, int, int]]]:
    """(hole -> indexes of the faces about its axis, painted index -> colour), read from the solid and from the paint."""
    rec = _rec()
    body = next(iter(rec.live_bodies()))
    shape = rec.fc_doc.getObject(body.result_obj).Shape
    painted = hole_colours.face_colours(rec, body)
    found = {name: {i for i, f in enumerate(shape.Faces) if _belongs(f, where)} for name, where in holes.items()}
    return found, painted


def _assert_all_green(holes: dict[str, tuple[float, float]], counts: dict[str, int]) -> None:
    found, painted = _faces_by_hole(holes)
    for name, indexes in found.items():
        assert len(indexes) == counts[name], (name, "faces about the axis", sorted(indexes))
        unpainted = sorted(i for i in indexes if painted.get(i) != GREEN)
        assert not unpainted, f"{name}: faces {unpainted} of {sorted(indexes)} are not green; painted: {painted}"
    wanted = set().union(*found.values())
    assert set(painted) == wanted, (sorted(painted), sorted(wanted))  # nothing else of the part is painted
    assert set(painted.values()) == {GREEN}


def check_every_face_of_a_through_counterbored_countersunk_and_blind_hole_is_green() -> None:
    new_document("placa")
    base = extrude("base", sketch("s0", "XY", [rect("outline", (70, 30), PLATE[0], PLATE[1])]), PLATE[2])
    holes = {"through": (20.0, 15.0), "cbore": (50.0, 15.0), "csink": (80.0, 15.0), "blind": (110.0, 15.0)}
    _drill(base, holes["through"], 6.0)
    _drill(base, holes["cbore"], 6.0, counterbore={"diameter_mm": 11.0, "depth_mm": 4.0})
    _drill(base, holes["csink"], 6.0, countersink={"diameter_mm": 12.0, "angle_deg": 90.0})
    _drill(base, holes["blind"], 6.0, end_condition="blind", depth_mm=6.0)
    _assert_all_green(holes, {"through": 1, "cbore": 3, "csink": 2, "blind": 2})


def check_a_counterbore_made_as_a_pocket_on_a_drilled_hole_is_green_too() -> None:
    new_document("placa")
    base = extrude("base", sketch("s0", "XY", [rect("outline", (70, 30), PLATE[0], PLATE[1])]), PLATE[2])
    holes = {"drilled": (20.0, 15.0), "pocketed": (60.0, 15.0), "plain_pocket": (100.0, 15.0)}
    _drill(base, holes["drilled"], 9.0)
    _drill(base, holes["pocketed"], 9.0)
    _pocket(base, holes["pocketed"], 14.0, 4.0)
    _drill(
        base, holes["plain_pocket"], 9.0, colour=None, hole_class=None, precision_grade=None
    )  # the convention was off
    _pocket(base, holes["plain_pocket"], 14.0, 4.0)
    found, painted = _faces_by_hole(holes)
    assert len(found["pocketed"]) == 3 and len(found["drilled"]) == 1, found
    assert all(painted.get(i) == GREEN for i in found["drilled"] | found["pocketed"]), (found, painted)
    assert not [i for i in found["plain_pocket"] if i in painted], (
        found,
        painted,
    )  # no colour on the hole, none on its pocket


def check_a_pocket_that_is_not_about_the_axis_of_a_hole_is_not_painted() -> None:
    new_document("placa")
    base = extrude("base", sketch("s0", "XY", [rect("outline", (70, 30), PLATE[0], PLATE[1])]), PLATE[2])
    holes = {"drilled": (20.0, 15.0), "pocket_beside": (60.0, 15.0)}
    _drill(base, holes["drilled"], 9.0)
    _pocket(base, holes["pocket_beside"], 14.0, 4.0)  # a recess with no hole in it
    _, painted = _faces_by_hole(holes)
    found = _faces_by_hole(holes)[0]
    assert set(painted) == found["drilled"], (painted, found)
    assert found["pocket_beside"] and not (found["pocket_beside"] & set(painted)), (painted, found)


def check_the_counterbore_of_a_hole_a_pattern_copied_is_green_in_every_copy() -> None:
    new_document("placa")
    base = extrude("base", sketch("s0", "XY", [rect("outline", (70, 30), PLATE[0], PLATE[1])]), PLATE[2])
    seed = _drill(base, (20.0, 15.0), 6.0, counterbore={"diameter_mm": 11.0, "depth_mm": 4.0})
    call("linear_pattern", op("copies", features=[seed["feature"]], direction=[1, 0, 0], spacing_mm=40.0, count=3))
    holes = {"a": (20.0, 15.0), "b": (60.0, 15.0), "c": (100.0, 15.0)}
    _assert_all_green(holes, {"a": 3, "b": 3, "c": 3})
