"""Sketch materialisation: fully constrained, named dimensions readable with getDatum, profile validation."""

import math
from typing import Any

import fc
import FreeCAD as App
import main
import Part
import registry as reg
import Sketcher
from FreeCAD import Vector
from harness import call, circle, expect_error, extrude, line, new_document, op, rect, sketch, square


def _fc_sketch(sketch_id: str) -> Any:
    doc = main.WORKER.active
    rec = doc.sketches[sketch_id]
    assert rec.fc_name is not None, "sketch was not materialised"
    return doc.fc_doc.getObject(rec.fc_name)


def check_rect_fully_constrained_with_named_dimensions() -> None:
    new_document("sk")
    sk = sketch(
        "s",
        "XY",
        [rect("outline", (50, 30), 100, 60)],
        named_dimensions=[
            {"name": "plate_width", "entity": "outline", "attribute": "width"},
            {"name": "plate_height", "entity": "outline", "attribute": "height"},
            {"name": "cx", "entity": "outline", "attribute": "u"},
        ],
    )
    extrude("e", sk, 8)
    obj = _fc_sketch(sk["sketch"]["id"])
    assert obj.FullyConstrained, "rect sketch is not fully constrained"
    assert obj.solve() == 0
    assert abs(obj.getDatum("plate_width").Value - 100.0) < 1e-9
    assert abs(obj.getDatum("plate_height").Value - 60.0) < 1e-9
    assert abs(obj.getDatum("cx").Value - 50.0) < 1e-9  # u of the rect centre (fake parity)
    tree = call("feature_tree", op("t"))["nodes"]
    assert tree[0]["named_dimensions"] == {"plate_width": 100.0, "plate_height": 60.0, "cx": 50.0}, tree[0]
    assert abs(call("mass_properties", op("m"))["volume_mm3"] - 48000.0) < 1e-6  # the construction point adds nothing
    script = call("export_macro", op("x", path="unused"))["script"]
    namespace: dict[str, Any] = {}
    exec(script, namespace)  # noqa: S102 - replay of the recorded constraints, construction point included
    replayed = namespace["docs"]["doc:1"]
    assert abs(replayed.getObject("Body").Shape.Volume - 48000.0) < 1e-6
    App.closeDocument(replayed.Name)


def check_square_fully_constrained_by_one_side_and_an_equal() -> None:
    """A square carries ONE driving distance (``S_<name>``, renamed by a named ``side``) plus an ``Equal``
    between two adjacent sides; a rect carries two driving distances."""
    new_document("sk")
    sk = sketch(
        "s",
        "XY",
        [square("boss", (15, 15), 20)],
        named_dimensions=[
            {"name": "boss_side", "entity": "boss", "attribute": "side"},
            {"name": "cx", "entity": "boss", "attribute": "u"},
        ],
    )
    assert abs(sk["profile_areas_mm2"][0] - 400.0) < 1e-9
    extrude("e", sk, 10)
    obj = _fc_sketch(sk["sketch"]["id"])
    assert obj.FullyConstrained, "square sketch is not fully constrained"
    assert obj.solve() == 0
    assert obj.getDatum("boss_side").Value == 20.0
    assert abs(obj.getDatum("cx").Value - 15.0) < 1e-9
    driving = [c for c in obj.Constraints if c.Driving and c.Type in ("Distance", "DistanceX", "DistanceY")]
    # side + the two anchors of the first corner + the reference-free centre point is not driving
    assert sorted(c.Name for c in driving) == ["", "", "boss_side"], [(c.Type, c.Name) for c in driving]
    assert sum(1 for c in obj.Constraints if c.Type == "Equal") == 1
    assert call("feature_tree", op("t"))["nodes"][0]["named_dimensions"] == {"boss_side": 20.0, "cx": 15.0}
    assert abs(call("mass_properties", op("m"))["volume_mm3"] - 4000.0) < 1e-6
    # editing the single side keeps it a square: 20 -> 30 gives 30 x 30 x 10
    obj.setDatum("boss_side", App.Units.Quantity("30 mm"))
    obj.Document.recompute()
    assert obj.FullyConstrained and obj.solve() == 0
    bbox = obj.Shape.BoundBox
    assert abs(bbox.XLength - 30.0) < 1e-6 and abs(bbox.YLength - 30.0) < 1e-6, (bbox.XLength, bbox.YLength)
    # default name when no named dimension asks for it
    new_document("sk2")
    sk2 = sketch("s2", "XY", [square("q", (0, 0), 10)])
    extrude("e2", sk2, 1)
    obj2 = _fc_sketch(sk2["sketch"]["id"])
    assert obj2.FullyConstrained and obj2.solve() == 0
    assert obj2.getDatum("S_q").Value == 10.0
    assert call("feature_tree", op("t2"))["nodes"][0]["named_dimensions"] == {}


def check_rect_with_chamfered_corners_is_fully_constrained_by_one_size() -> None:
    """``chamfer_mm`` cuts the four corners at 45 degrees in the sketch: eight lines, ONE driving chamfer size
    (``C_<name>``, renamed by a named ``chamfer``), no freedom left, width/height/position still driving."""
    new_document("sk")
    entity = {**rect("plate", (50, 30), 100, 60), "chamfer_mm": 4.0}
    sk = sketch(
        "s",
        "XY",
        [entity],
        named_dimensions=[
            {"name": "plate_w", "entity": "plate", "attribute": "width"},
            {"name": "plate_h", "entity": "plate", "attribute": "height"},
            {"name": "cx", "entity": "plate", "attribute": "u"},
            {"name": "cy", "entity": "plate", "attribute": "v"},
        ],
    )
    area = 100 * 60 - 2 * 4.0 * 4.0
    assert abs(sk["profile_areas_mm2"][0] - area) < 1e-9, sk["profile_areas_mm2"]
    assert sk["warnings"] == ["rect plate: four corners chamfered 4 mm at 45 degrees"], sk["warnings"]
    extrude("e", sk, 8)
    obj = _fc_sketch(sk["sketch"]["id"])
    assert obj.FullyConstrained and obj.DoF == 0, (obj.FullyConstrained, obj.DoF)
    assert not obj.RedundantConstraints and not obj.ConflictingConstraints
    assert obj.solve() == 0
    assert obj.GeometryCount == 9  # eight lines and the construction point of the centre
    assert obj.getDatum("C_plate").Value == 4.0
    assert abs(obj.getDatum("plate_w").Value - 100.0) < 1e-9 and abs(obj.getDatum("plate_h").Value - 60.0) < 1e-9
    assert abs(obj.getDatum("cx").Value - 50.0) < 1e-9 and abs(obj.getDatum("cy").Value - 30.0) < 1e-9
    assert sum(1 for c in obj.Constraints if c.Driving and c.Name == "C_plate") == 1
    nodes = call("feature_tree", op("t"))["nodes"]
    assert nodes[0]["named_dimensions"] == {
        "C_plate": 4.0,
        "plate_w": 100.0,
        "plate_h": 60.0,
        "cx": 50.0,
        "cy": 30.0,
    }, nodes[0]
    body = main.WORKER.active.fc_doc.getObject("Body")
    assert len(body.Shape.Faces) == 10, len(body.Shape.Faces)  # eight sides, top and bottom
    assert abs(body.Shape.Volume - area * 8) < 1e-6, body.Shape.Volume
    assert abs(call("mass_properties", op("m"))["volume_mm3"] - area * 8) < 1e-6
    bbox = body.Shape.BoundBox
    assert abs(bbox.XMin) < 1e-6 and abs(bbox.XMax - 100.0) < 1e-6 and abs(bbox.YMax - 60.0) < 1e-6, bbox
    # editing the single chamfer size moves all four corners: 4 -> 6
    obj.setDatum("C_plate", App.Units.Quantity("6 mm"))
    obj.Document.recompute()
    assert obj.FullyConstrained and obj.solve() == 0
    area6 = 100 * 60 - 2 * 6.0 * 6.0
    assert abs(body.Shape.Volume - area6 * 8) < 1e-6, body.Shape.Volume
    bbox = body.Shape.BoundBox
    assert abs(bbox.XLength - 100.0) < 1e-6 and abs(bbox.YLength - 60.0) < 1e-6
    # a chamfer that eats the side is refused
    expect_error(
        "invalid_request", "sketch", op("bad", on="XY", entities=[{**rect("p", (0, 0), 20, 10), "chamfer_mm": 5.0}])
    )


def check_circle_fully_constrained_diameter_and_radius() -> None:
    new_document("sk")
    sk = sketch(
        "s",
        "XY",
        [circle("c", (20, 10), 6.6)],
        named_dimensions=[
            {"name": "hole_d", "entity": "c", "attribute": "diameter"},
            {"name": "hole_r", "entity": "c", "attribute": "radius"},
            {"name": "u", "entity": "c", "attribute": "u"},
        ],
    )
    assert abs(sk["profile_areas_mm2"][0] - math.pi * 3.3**2) < 1e-9
    extrude("e", sk, 5)
    obj = _fc_sketch(sk["sketch"]["id"])
    assert obj.FullyConstrained and obj.solve() == 0
    assert abs(obj.getDatum("hole_d").Value - 6.6) < 1e-9
    assert abs(obj.getDatum("hole_r").Value - 3.3) < 1e-9  # reference (non-driving) constraint
    assert abs(obj.getDatum("u").Value - 20.0) < 1e-9
    reference = [c for c in obj.Constraints if c.Name == "hole_r"][0]
    assert not reference.Driving
    assert abs(call("mass_properties", op("m"))["volume_mm3"] - math.pi * 3.3**2 * 5) < 1e-6


def check_polygon_fully_constrained_and_length_dimension() -> None:
    new_document("sk")
    sk = sketch(
        "s",
        "XY",
        [
            line("a", (0, 0), (60, 0)),
            line("b", (60, 0), (60, 10)),
            line("c", (60, 10), (10, 10)),
            line("d", (10, 10), (10, 40)),
            line("e", (10, 40), (0, 40)),
            line("f", (0, 40), (0, 0)),
        ],
        named_dimensions=[
            {"name": "base_len", "entity": "a", "attribute": "length"},
            {"name": "c_u", "entity": "c", "attribute": "u"},
            {"name": "c_v", "entity": "c", "attribute": "v"},
        ],
    )
    assert abs(sk["profile_areas_mm2"][0] - 900.0) < 1e-9
    extrude("e", sk, 20)
    obj = _fc_sketch(sk["sketch"]["id"])
    assert obj.FullyConstrained and obj.solve() == 0
    assert abs(obj.getDatum("base_len").Value - 60.0) < 1e-9
    assert abs(obj.getDatum("c_u").Value - 60.0) < 1e-9 and abs(obj.getDatum("c_v").Value - 10.0) < 1e-9
    assert abs(call("mass_properties", op("m"))["volume_mm3"] - 18000.0) < 1e-6


def check_unordered_line_chain_is_closed() -> None:
    new_document("sk")
    entities = [
        line("a", (0, 0), (10, 0)),
        line("c", (10, 10), (0, 10)),
        line("b", (10, 10), (10, 0)),
        line("d", (0, 0), (0, 10)),
    ]
    sk = sketch("s", "XY", entities)
    assert abs(sk["profile_areas_mm2"][0] - 100.0) < 1e-9
    extrude("e", sk, 1)
    assert abs(call("mass_properties", op("m"))["volume_mm3"] - 100.0) < 1e-6
    assert _fc_sketch(sk["sketch"]["id"]).FullyConstrained


def arc3(name: str, start: tuple[float, float], via: tuple[float, float], end: tuple[float, float]) -> dict[str, Any]:
    return {"kind": "arc3", "name": name, "start": list(start), "through": list(via), "end": list(end)}


def arc_ends(
    name: str, center: tuple[float, float], start: tuple[float, float], end: tuple[float, float], ccw: bool = True
) -> dict[str, Any]:
    points = {"center": list(center), "start": list(start), "end": list(end)}
    return {"kind": "arc_ends", "name": name, **points, "ccw": ccw}


def slot(name: str, start: tuple[float, float], end: tuple[float, float], width: float) -> dict[str, Any]:
    return {"kind": "slot", "name": name, "start": list(start), "end": list(end), "width": width}


def check_a_chain_of_lines_and_arcs_is_a_fully_constrained_profile() -> None:
    """A rounded-end bar spelled out entity by entity (lines, a three-point arc and a centre + ends arc, one of them
    clockwise): exact area and centroid from the entities, one pad, FullyConstrained, the volume of the pad."""
    new_document("sk")
    entities = [
        line("top", (10, 5), (50, 5)),
        arc3("right", (50, 5), (55, 0), (50, -5)),
        line("bottom", (50, -5), (10, -5)),
        arc_ends("left", (10, 0), (10, -5), (10, 5), ccw=False),
    ]
    sk = sketch("s", "XY", entities, named_dimensions=[{"name": "end_r", "entity": "right", "attribute": "radius"}])
    area = 40 * 10 + math.pi * 25
    assert abs(sk["profile_areas_mm2"][0] - area) < 1e-9, sk["profile_areas_mm2"]
    extrude("e", sk, 6)
    obj = _fc_sketch(sk["sketch"]["id"])
    assert obj.FullyConstrained and obj.solve() == 0
    assert obj.DoF == 0 and not obj.RedundantConstraints and not obj.ConflictingConstraints
    assert abs(obj.getDatum("end_r").Value - 5.0) < 1e-9
    assert abs(call("mass_properties", op("m"))["volume_mm3"] - area * 6) < 1e-3
    status = call("sketch_status", op("q", sketch=sk["sketch"]))
    assert status["dof"] == 0 and status["fully_constrained"] and not status["over_constrained"], status
    script = call("export_macro", op("x", path="unused"))["script"]
    namespace: dict[str, Any] = {}
    exec(script, namespace)  # noqa: S102 - the recorded arcs replay
    replayed = namespace["docs"]["doc:1"]
    assert abs(replayed.getObject("Body").Shape.Volume - area * 6) < 1e-3
    App.closeDocument(replayed.Name)


def check_arc_forms_with_angles_and_a_half_disc() -> None:
    """The angle form (``arc``) and a D shape: the diameter plus a half circle."""
    new_document("sk")
    dome = {"kind": "arc", "name": "dome", "center": [0, 0], "radius": 5, "start_deg": 0, "end_deg": 180}
    sk = sketch("s", "XY", [line("base", (-5, 0), (5, 0)), dome])
    assert abs(sk["profile_areas_mm2"][0] - math.pi * 25 / 2) < 1e-9
    extrude("e", sk, 2)
    assert _fc_sketch(sk["sketch"]["id"]).FullyConstrained
    assert abs(call("mass_properties", op("m"))["volume_mm3"] - math.pi * 25) < 1e-3


def check_a_slot_is_one_profile_pinned_by_its_arcs() -> None:
    new_document("sk")
    sk = sketch(
        "s",
        "XY",
        [slot("s", (0, 0), (20, 10), 6)],
        named_dimensions=[
            {"name": "slot_w", "entity": "s", "attribute": "width"},
            {"name": "slot_l", "entity": "s", "attribute": "length"},
            {"name": "slot_u", "entity": "s", "attribute": "u"},
        ],
    )
    length = math.hypot(20, 10)
    area = length * 6 + math.pi * 9
    assert len(sk["profile_areas_mm2"]) == 1 and abs(sk["profile_areas_mm2"][0] - area) < 1e-9
    extrude("e", sk, 4)
    obj = _fc_sketch(sk["sketch"]["id"])
    assert obj.FullyConstrained and obj.solve() == 0
    assert abs(obj.getDatum("slot_w").Value - 6.0) < 1e-9
    assert abs(obj.getDatum("slot_l").Value - length) < 1e-9
    assert abs(obj.getDatum("slot_u").Value - 0.0) < 1e-9
    assert abs(call("mass_properties", op("m"))["volume_mm3"] - area * 4) < 1e-3
    status = call("sketch_status", op("q", sketch=sk["sketch"]))
    assert status["dof"] == 0 and not status["over_constrained"], status


def check_a_slot_cuts_with_the_analytic_volume() -> None:
    new_document("sk")
    base = extrude("base", sketch("plate", "XY", [rect("r", (50, 30), 100, 60)]), 8)
    assert base["rebuild_ok"]
    cutter = sketch("slot_sk", "XY", [slot("s", (30, 30), (70, 30), 10)])
    area = 40 * 10 + math.pi * 25
    params = op("slot", sketch=cutter["sketch"], end_condition="through_all", direction="positive")
    params.update(depth_mm=None, both_directions=False, depth2_mm=None, scope=None)
    call("cut", params)
    assert abs(call("mass_properties", op("m"))["volume_mm3"] - (48000 - area * 8)) < 1e-3


def check_arc_errors_and_open_chains_are_invalid() -> None:
    new_document("sk")
    collinear = [arc3("a", (0, 0), (5, 0), (10, 0))]
    expect_error("invalid_request", "sketch", op("s", on="XY", entities=collinear))
    off_circle = [arc_ends("a", (0, 0), (5, 0), (0, 6))]
    expect_error("invalid_request", "sketch", op("s", on="XY", entities=off_circle))
    lone_arc = [{"kind": "arc", "name": "a", "center": [0, 0], "radius": 5, "start_deg": 0, "end_deg": 90}]
    expect_error("invalid_request", "sketch", op("s", on="XY", entities=lone_arc))
    open_chain = [line("a", (0, 0), (10, 0)), line("b", (10, 0), (10, 10))]
    expect_error("invalid_request", "sketch", op("s", on="XY", entities=open_chain))
    expect_error("invalid_request", "sketch", op("s", on="XY", entities=[]))
    bad_dim = [{"name": "x", "entity": "nope", "attribute": "width"}]
    expect_error(
        "invalid_request", "sketch", op("s", on="XY", entities=[rect("r", (0, 0), 1, 1)], named_dimensions=bad_dim)
    )


def check_sketch_on_non_planar_face_is_invalid_request() -> None:
    new_document("sk")
    sk = sketch("s", "XY", [circle("c", (0, 0), 10)])
    e = extrude("e", sk, 5)
    faces = call("faces_of", op("q", feature=e["feature"], role="created"))["faces"]
    cyl = [f for f in faces if f["surface"] == "cylinder"]
    expect_error("invalid_request", "sketch", op("s2", on=cyl[0]["handle"], entities=[rect("r", (0, 0), 1, 1)]))


def _register_raw_sketch(doc: Any, sk: Any) -> str:
    """Register a hand-built ``Sketcher::SketchObject`` under the registry, bypassing ``materialise_sketch``
    (which fixes every point unconditionally): every sketch built through the public ``sketch``/``extrude``
    RPCs is always fully constrained by construction, so an underdefined sketch can only be produced by
    talking to the raw FreeCAD object directly, which is exactly what ``sketch_status`` must diagnose."""
    rec = reg.SketchRec(doc.next_id("sketch"), {}, {}, [])
    rec.fc_name = sk.Name
    doc.sketches[rec.id] = rec
    return rec.id


def check_sketch_status_reports_dof_for_underdefined_and_then_fully_constrained() -> None:
    new_document("sk")
    doc = main.WORKER.active
    sk = fc.add_object(doc.fc_doc, "Sketcher::SketchObject", "RawLine")
    geo_id = fc.call(sk, "addGeometry", Part.LineSegment(Vector(0, 0, 0), Vector(10, 0, 0)), False)
    fc.call(sk, "solve")
    sketch_id = _register_raw_sketch(doc, sk)

    underdefined = call("sketch_status", op("q1", sketch={"id": sketch_id}))
    assert underdefined["dof"] > 0, underdefined
    assert underdefined["fully_constrained"] is False
    assert underdefined["geometry_count"] == 1
    assert underdefined["constraint_count"] == 0
    assert underdefined["over_constrained"] is False
    assert underdefined["conflicts"] == []

    # the same sketch, now pinned at both endpoints of its single line
    fc.add_constraint(sk, "DistanceX", -1, 1, geo_id, 1, 0.0)
    fc.add_constraint(sk, "DistanceY", -1, 1, geo_id, 1, 0.0)
    fc.add_constraint(sk, "DistanceX", -1, 1, geo_id, 2, 10.0)
    fc.add_constraint(sk, "DistanceY", -1, 1, geo_id, 2, 0.0)
    fc.call(sk, "solve")

    fully_defined = call("sketch_status", op("q2", sketch={"id": sketch_id}))
    assert fully_defined["dof"] == 0, fully_defined
    assert fully_defined["fully_constrained"] is True
    assert fully_defined["constraint_count"] == 4
    assert fully_defined["over_constrained"] is False


def check_sketch_status_reports_redundant_constraints_as_over_constrained() -> None:
    new_document("sk")
    doc = main.WORKER.active
    sk = fc.add_object(doc.fc_doc, "Sketcher::SketchObject", "RawRedundant")
    geo_id = fc.call(sk, "addGeometry", Part.LineSegment(Vector(0, 0, 0), Vector(10, 0, 0)), False)
    fc.add_constraint(sk, "DistanceX", -1, 1, geo_id, 1, 0.0)
    fc.add_constraint(sk, "DistanceY", -1, 1, geo_id, 1, 0.0)
    fc.add_constraint(sk, "DistanceX", -1, 1, geo_id, 2, 10.0)
    fc.add_constraint(sk, "DistanceY", -1, 1, geo_id, 2, 0.0)
    fc.add_constraint(sk, "DistanceX", -1, 1, geo_id, 2, 10.0)  # exact duplicate: redundant, not conflicting
    fc.call(sk, "solve")
    sketch_id = _register_raw_sketch(doc, sk)

    status = call("sketch_status", op("q", sketch={"id": sketch_id}))
    assert status["over_constrained"] is True, status
    assert any(c["kind"] == "redundant" for c in status["conflicts"]), status


# --- the rule of every feature: its sketch ends fully defined by dimensions ----------------------------------

UNDER_DEFINED_PAD = """
body = doc.addObject("PartDesign::Body", "Body")
sk = body.newObject("Sketcher::SketchObject", "Sketch")
sk.AttachmentSupport = (body.Origin.OriginFeatures[3], [""])
sk.MapMode = "FlatFace"
pts = [Vector(0, 0, 0), Vector(20, 0, 0), Vector(20, 10, 0), Vector(0, 10, 0)]
for i in range(4):
    sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]), False)
for i in range(4):
    sk.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1))
{more}
pad = body.newObject("PartDesign::Pad", "Pad")
pad.Profile = sk
pad.Length = 5
doc.recompute()
"""
DIMENSIONS = """
for i in (0, 2):
    sk.addConstraint(Sketcher.Constraint("Horizontal", i))
for i in (1, 3):
    sk.addConstraint(Sketcher.Constraint("Vertical", i))
sk.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, 20))
sk.addConstraint(Sketcher.Constraint("DistanceY", 1, 1, 1, 2, 10))
sk.addConstraint(Sketcher.Constraint("DistanceX", -1, 1, 0, 1, 0))
sk.addConstraint(Sketcher.Constraint("DistanceY", -1, 1, 0, 1, 0))
"""
BLOCKS = """
for i in range(4):
    sk.addConstraint(Sketcher.Constraint("Block", i))
"""


def _native(code: str, name: str) -> dict[str, Any]:
    return call("run_native", op(name, code=code, intent="the sketch rule", timeout_s=60))


def check_every_entity_the_tool_sketches_ends_fully_defined_without_a_block() -> None:
    """``materialise_sketch``'s gate never refuses a sketch the tool made: each kind of entity, alone."""
    import sketch_defined

    entities = {
        "rect": [rect("r", (50, 30), 100, 60)],
        "square": [square("q", (0, 0), 10)],
        "chamfered rect": [{**rect("p", (50, 30), 100, 60), "chamfer_mm": 4.0}],
        "circle": [circle("c", (0, 0), 10)],
        "line chain": [line("a", (0, 0), (10, 0)), line("b", (10, 0), (10, 10)), line("c", (10, 10), (0, 0))],
        "arcs": [
            line("top", (10, 5), (50, 5)),
            arc3("right", (50, 5), (55, 0), (50, -5)),
            line("bottom", (50, -5), (10, -5)),
            arc_ends("left", (10, 0), (10, -5), (10, 5), ccw=False),
        ],
        "slot": [slot("s", (0, 0), (20, 10), 6)],
        "rect and circles": [rect("plate", (50, 30), 100, 60), circle("h1", (20, 15), 6), circle("h2", (80, 45), 6)],
    }
    for kind, items in entities.items():
        new_document("sk")
        sk = sketch("s", "XY", items)
        extrude("e", sk, 3)
        obj = _fc_sketch(sk["sketch"]["id"])
        assert obj.DoF == 0 and sketch_defined.block_count(obj) == 0, (kind, obj.DoF)
        assert sketch_defined.judge(obj) is None, kind


def check_a_native_feature_on_an_under_defined_sketch_is_rolled_back_with_the_reason() -> None:
    new_document("sk")
    result = _native(UNDER_DEFINED_PAD.format(more=""), "loose")
    assert result["ok"] is False and result["rolled_back"] is True, result
    message = result["error"]["message"]
    assert "sketch_not_fully_defined" in message and "degree(s) of freedom" in message, message
    assert "dimensions" in result["error"]["hint"]
    assert main.WORKER.active.fc_doc.getObject("Body") is None  # nothing of the step is left


def check_the_same_step_with_the_sketch_dimensioned_stands() -> None:
    new_document("sk")
    result = _native(UNDER_DEFINED_PAD.format(more=DIMENSIONS), "tight")
    assert result["ok"] is True, result
    assert main.WORKER.active.fc_doc.getObject("Pad").Shape.Volume > 0


def check_a_block_does_not_define_a_sketch() -> None:
    new_document("sk")
    result = _native(UNDER_DEFINED_PAD.format(more=BLOCKS), "blocked")
    assert result["ok"] is False and result["rolled_back"] is True, result
    assert "sketch_has_block" in result["error"]["message"], result["error"]


def check_a_sketch_no_feature_uses_may_stay_under_defined() -> None:
    new_document("sk")
    code = UNDER_DEFINED_PAD.split("pad = body")[0].format(more="")
    result = _native(code, "construction")
    assert result["ok"] is True, result
    assert call("sketch_notes", op("n")) == {"notes": []}  # unused: not listed either


def check_a_file_keeps_its_under_defined_sketch_and_check_lists_it() -> None:
    """A document that arrives with a pad on an under defined sketch is neither changed nor refused. ``check`` lists the
    sketch; changing a property of the pad stands (it builds nothing on the sketch); a NEW feature on it is refused."""
    new_document("sk")
    doc = main.WORKER.active.fc_doc
    body = doc.addObject("PartDesign::Body", "Body")
    sk = body.newObject("Sketcher::SketchObject", "Sketch")
    sk.AttachmentSupport = (body.Origin.OriginFeatures[3], [""])
    sk.MapMode = "FlatFace"
    pts = [Vector(0, 0, 0), Vector(20, 0, 0), Vector(20, 10, 0), Vector(0, 10, 0)]
    for i in range(4):
        sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]), False)
    for i in range(4):
        sk.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1))
    pad = body.newObject("PartDesign::Pad", "Pad")
    pad.Profile = sk
    pad.Length = 5
    doc.recompute()
    notes = call("sketch_notes", op("n"))["notes"]
    assert notes and notes[0].startswith("sketches: 0 of 1 behind a feature fully defined"), notes
    assert notes[1].startswith("sketch Sketch: it still has ") and "degree(s) of freedom" in notes[1], notes
    edited = call("set_feature_properties", op("e", feature="Pad", properties={"Length": 9}))
    assert edited["ok"] is True, edited
    again = _native(
        'pocket = doc.getObject("Body").newObject("PartDesign::Pocket", "Pocket")\n'
        'pocket.Profile = doc.getObject("Sketch")\n',
        "again",
    )
    assert again["ok"] is False and "sketch_not_fully_defined" in again["error"]["message"], again


def _old_file_with_a_free_sketch_on_the_top_face() -> tuple[Any, Any, Any]:
    """An opened file: a dimensioned pad and, on its top face, a pocket whose sketch is NOT fully defined."""
    new_document("sk")
    doc = main.WORKER.active.fc_doc
    body = doc.addObject("PartDesign::Body", "Body")
    base = body.newObject("Sketcher::SketchObject", "Base")
    base.AttachmentSupport = (body.Origin.OriginFeatures[3], [""])
    base.MapMode = "FlatFace"
    pts = [Vector(0, 0, 0), Vector(20, 0, 0), Vector(20, 10, 0), Vector(0, 10, 0)]
    for i in range(4):
        base.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]), False)
    for i in range(4):
        base.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1))
    for i in (0, 2):
        base.addConstraint(Sketcher.Constraint("Horizontal", i))
    for i in (1, 3):
        base.addConstraint(Sketcher.Constraint("Vertical", i))
    base.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, 20))
    base.addConstraint(Sketcher.Constraint("DistanceY", 1, 1, 1, 2, 10))
    base.addConstraint(Sketcher.Constraint("DistanceX", -1, 1, 0, 1, 0))
    base.addConstraint(Sketcher.Constraint("DistanceY", -1, 1, 0, 1, 0))
    pad = body.newObject("PartDesign::Pad", "Pad")
    pad.Profile = base
    pad.Length = 10
    doc.recompute()
    top = next(
        f"Face{i + 1}"
        for i, face in enumerate(pad.Shape.Faces)
        if abs(face.CenterOfMass.z - 10) < 1e-6 and abs(face.normalAt(0, 0).z - 1) < 1e-6
    )
    free = body.newObject("Sketcher::SketchObject", "Free")
    free.AttachmentSupport = (pad, [top])
    free.MapMode = "FlatFace"
    pts = [Vector(2, 2, 0), Vector(8, 2, 0), Vector(8, 6, 0), Vector(2, 6, 0)]
    for i in range(4):
        free.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]), False)
    for i in range(4):
        free.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1))
    pocket = body.newObject("PartDesign::Pocket", "Pocket")
    pocket.Profile = free
    pocket.Length = 2
    doc.recompute()
    return doc, body, free


def check_a_free_sketch_that_followed_its_face_is_not_blamed_on_the_step_that_moved_it() -> None:
    """The sketch sits on the pad's top face: a longer pad moves its Placement, nothing else of it. The step edits
    the pad only, so it stands even though the sketch under the pocket is not fully defined."""
    doc, _, free = _old_file_with_a_free_sketch_on_the_top_face()
    assert free.DoF > 0 and abs(free.Placement.Base.z - 10) < 1e-9
    pairs = free.getGeometryWithDependentParameters()  # (geometry id, point position), repeated per parameter
    assert pairs and all(isinstance(g, int) and isinstance(p, int) for g, p in pairs), pairs
    assert any(label.startswith("line 0") for label in __import__("sketch_defined").free_parts(free))
    edited = call("set_feature_properties", op("e", feature="Pad", properties={"Length": 15}))
    assert edited["ok"] is True, edited
    assert abs(free.Placement.Base.z - 15) < 1e-9, free.Placement.Base  # it did follow the face
    assert doc.getObject("Pad").Length.Value == 15


def check_a_shape_binder_reading_a_sketch_is_not_a_use_of_it() -> None:
    """A binder points at the sketch of a construction: no feature is built on it, so it may stay under defined. A
    pad built on the binder makes it a use."""
    new_document("sk")
    doc = main.WORKER.active.fc_doc
    code = (
        UNDER_DEFINED_PAD.split("pad = body")[0].format(more="")
        + 'binder = body.newObject("PartDesign::ShapeBinder", "Binder")\n'
        + 'binder.Support = [(sk, "")]\n'
        + "doc.recompute()\n"
    )
    result = _native(code, "binder")
    assert result["ok"] is True, result
    import sketch_defined

    sketch_obj = doc.getObject("Sketch")
    assert [o.Name for o in sketch_obj.InList if o.Name == "Binder"] == ["Binder"]  # FreeCAD lists it as a referrer
    assert sketch_defined.users(sketch_obj) == [] and call("sketch_notes", op("n")) == {"notes": []}
    pad = _native(
        'pad = doc.getObject("Body").newObject("PartDesign::Pad", "Pad")\n'
        'pad.Profile = doc.getObject("Binder")\npad.Length = 3\ndoc.recompute()\n',
        "pad",
    )
    assert pad["ok"] is False and "sketch_not_fully_defined" in pad["error"]["message"], pad
