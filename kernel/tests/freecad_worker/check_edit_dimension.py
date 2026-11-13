"""``edit_feature`` with a DIMENSION: a datum of a sketch (``setDatum``) or a value of a PartDesign feature, set on the
objects that exist (``worker/edit.py`` + ``worker/dim_edit.py``).

Plain asserts run inside ``freecadcmd`` (see ``run.py``). The oracle is a FRESH document built with the new values: the
edited document must have its volume, its box and its face and edge counts, and the handles of the steps after the
edit must be the ones they were. NOT YET RUN on FreeCAD: written together with the code and run by the owner's Windows
suite.
"""

from typing import Any

import main
from harness import (
    call,
    close,
    expect_error,
    extrude,
    face_info,
    faces_of,
    new_document,
    op,
    rect,
    select_face,
    sketch,
    volume,
)


def _entities(width: float, height: float = 60.0) -> list[dict[str, Any]]:
    return [rect("outline", (50, 30), width, height)]


def _named() -> list[dict[str, Any]]:
    return [{"name": "plate_width", "entity": "outline", "attribute": "width"}]


def _sketch_request(width: float, height: float = 60.0) -> dict[str, Any]:
    return op("base_sketch", on="XY", entities=_entities(width, height), constraints=[], named_dimensions=_named())


def _pad_request(sk: dict[str, Any], depth: float) -> dict[str, Any]:
    return op(
        "base",
        sketch=sk["sketch"],
        depth_mm=depth,
        direction="positive",
        both_directions=False,
        depth2_mm=None,
        merge=True,
    )


def _hole_request(top: dict[str, Any], diameter: float, position: tuple[float, float] = (-30, -20)) -> dict[str, Any]:
    return op(
        "hole_1",
        face=top["handle"],
        position=list(position),
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


def _plate(width: float = 100.0, depth: float = 8.0, diameter: float = 6.0) -> dict[str, Any]:
    new_document("plate")
    sk = sketch("base_sketch", "XY", _entities(width), named_dimensions=_named())
    base = extrude("base", sk, depth)
    top = select_face(faces_of(base["feature"]), normal=(0, 0, 1))
    hole = call("hole", _hole_request(top, diameter))
    return {"sketch": sk, "base": base, "top": top, "hole": hole}


def _fresh_volume(width: float = 100.0, depth: float = 8.0, diameter: float = 6.0) -> float:
    _plate(width, depth, diameter)
    return volume()


def _edit(edits: list[dict[str, Any]], dry_run: bool = False) -> dict[str, Any]:
    return call("edit_feature", op("edit", edits=edits, dry_run=dry_run))


def _pad_edit(ids: dict[str, Any], depth: float, sketch_request: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "feature": ids["base"]["feature"],
        "kind": "extrude",
        "request": _pad_request(ids["sketch"], depth),
        "sketch": sketch_request,
    }


def check_a_sketch_dimension_is_set_in_place_and_the_steps_after_it_keep_their_handles() -> None:
    ids = _plate()
    features = [f.id for f in main.WORKER.active.features]
    result = _edit([_pad_edit(ids, 8.0, _sketch_request(120.0))])
    assert result["applied"] and not result["refused"], result
    assert close(volume(), _fresh_volume(120.0)), "the edited plate is not the plate built with the new width"
    ids = _plate()  # the oracle replaced the document: build it again to look at the handles
    assert _edit([_pad_edit(ids, 8.0, _sketch_request(120.0))])["applied"]
    assert [f.id for f in main.WORKER.active.features] == features, "no step runs again, no id changes"
    assert face_info(ids["top"]["handle"])["area_mm2"] > 0, "the face the hole sits on is still the same handle"


def check_a_pad_length_is_set_in_place_and_the_hole_sketch_follows_the_face() -> None:
    ids = _plate()
    result = _edit([_pad_edit(ids, 12.0)])
    assert result["applied"], result
    assert close(volume(), _fresh_volume(depth=12.0)), "the hole did not follow the top face to its new height"


def check_a_hole_diameter_is_set_in_place() -> None:
    ids = _plate()
    edit = {"feature": ids["hole"]["feature"], "kind": "hole", "request": _hole_request(ids["top"], 10.0)}
    result = _edit([edit])
    assert result["applied"], result
    assert close(volume(), _fresh_volume(diameter=10.0))


def check_what_a_dimension_cannot_say_is_refused_and_nothing_moves() -> None:
    ids = _plate()
    before = volume()
    moved = {"feature": ids["hole"]["feature"], "kind": "hole", "request": _hole_request(ids["top"], 6.0, (0, 0))}
    result = _edit([moved])
    assert result["refused"] and "position" in next(iter(result["refused"].values())), result
    two = op("base_sketch", on="XY", entities=[*_entities(100.0), rect("extra", (200, 30), 20, 20)], constraints=[])
    result = _edit([_pad_edit(ids, 8.0, {**two, "named_dimensions": _named()})])
    assert result["refused"] and "other entities" in next(iter(result["refused"].values())), result
    assert close(volume(), before)


def check_a_value_that_breaks_the_part_is_rolled_back_and_the_sketch_is_as_it_was() -> None:
    ids = _plate()
    before = volume()
    # a plate 20 wide: the hole at (-30, -20) from the face centre falls off it
    result = _edit([_pad_edit(ids, 8.0, _sketch_request(20.0))])
    assert not result["applied"] and result["failure"], result
    assert close(volume(), before)
    sk = next(o for o in main.WORKER.active.fc_doc.Objects if o.TypeId == "Sketcher::SketchObject")
    assert close(sk.getDatum("plate_width").Value, 100.0), "the sketch was not given back"
    assert _edit([_pad_edit(ids, 8.0, _sketch_request(110.0))])["applied"], "the session goes on after a rollback"


def check_a_length_the_engineer_changed_by_hand_is_not_put_back() -> None:
    ids = _plate()
    doc = main.WORKER.active
    pad = doc.fc_doc.getObject(doc.features[0].fc_main)
    pad.Length = 9.0  # by hand
    doc.fc_doc.recompute()
    result = _edit([_pad_edit(ids, 8.0, _sketch_request(120.0))])  # the request still says 8: only the sketch changes
    assert result["applied"], result
    assert close(float(pad.Length.Value), 9.0), "the edit put the old length back"


def check_a_named_dimension_of_a_sketch_of_an_opened_file_is_set_by_its_name() -> None:
    """A sketch the harness did not make (as in a file that was opened): ``set_feature_properties`` takes the names of
    its driving dimensions, lists them, and refuses an unknown one."""
    import Part
    import Sketcher
    from FreeCAD import Vector

    new_document("opened")
    doc = main.WORKER.active.fc_doc
    body = doc.addObject("PartDesign::Body", "Body")
    sk = body.newObject("Sketcher::SketchObject", "Sketch")
    sk.AttachmentSupport = (body.Origin.OriginFeatures[3], [""])
    sk.MapMode = "FlatFace"
    points = [Vector(0, 0, 0), Vector(20, 0, 0), Vector(20, 10, 0), Vector(0, 10, 0)]
    for i in range(4):
        sk.addGeometry(Part.LineSegment(points[i], points[(i + 1) % 4]), False)
    for i in range(4):
        sk.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1))
    sk.addConstraint(Sketcher.Constraint("Horizontal", 0))
    sk.addConstraint(Sketcher.Constraint("Horizontal", 2))
    sk.addConstraint(Sketcher.Constraint("Vertical", 1))
    sk.addConstraint(Sketcher.Constraint("Vertical", 3))
    sk.addConstraint(Sketcher.Constraint("DistanceX", -1, 1, 0, 1, 0.0))
    sk.addConstraint(Sketcher.Constraint("DistanceY", -1, 1, 0, 1, 0.0))
    sk.renameConstraint(sk.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, 20.0)), "W")
    sk.addConstraint(Sketcher.Constraint("DistanceY", 1, 1, 1, 2, 10.0))
    doc.recompute()
    listed = call("list_features", op("q", feature="Sketch"))["features"][0]
    assert any(item.startswith("W:length") for item in listed["editable_properties"]), listed
    result = call("set_feature_properties", op("e", feature="Sketch", properties={"W": 30}))
    assert result["ok"] is True, result
    assert close(float(sk.getDatum("W").Value), 30.0)
    expect_error("invalid_request", "set_feature_properties", op("e2", feature="Sketch", properties={"nope": 5}))
