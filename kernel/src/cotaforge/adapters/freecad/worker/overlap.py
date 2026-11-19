"""``tool_overlap``: one boolean of a tool solid with the part as it is now (READ-ONLY: nothing is added to the doc).

The verification asks "what must this cut remove / this boss add" before the op runs. The answer is the real geometry:
the tool is built exactly (lines and arcs are not sampled), intersected with the body the op would act on, and both
volumes come back. A tool that breaks into an existing cavity, or a sketch with a loop inside a loop, needs no special
case: the cavity is not in the intersection, the inner loop is subtracted by the even-odd rule.
"""

import math
from typing import Any

import geometry as g
import ops_common as oc
import Part
import registry as reg
from FreeCAD import Placement, Rotation, Vector
from protocol import invalid_request, not_supported
from queries import face_descriptor

DEPTH_TOL = 1e-9


def _local_face(loop: list[tuple[oc.Segment, bool]]) -> Any:
    """The planar face (z = 0, sketch coordinates) bounded by one closed loop of lines and arcs."""
    edges = []
    for (start, end, arc, _ccw), _reverse in loop:
        if arc is None:
            edges.append(Part.LineSegment(Vector(start[0], start[1], 0.0), Vector(end[0], end[1], 0.0)).toShape())
        else:
            (cx, cy), radius, first, sweep = arc
            a0 = first % oc.TWO_PI
            circle = Part.Circle(Vector(cx, cy, 0.0), Vector(0, 0, 1), radius)
            edges.append(Part.ArcOfCircle(circle, a0, a0 + sweep).toShape())
    return Part.Face(Part.Wire(Part.sortEdges(edges)[0]))


def _sketch_faces(entities: list[dict[str, Any]], op: str) -> list[Any]:
    """Every closed loop of the sketch as a local face (a loop per rect, square, circle, slot and line/arc chain)."""
    faces: list[Any] = []
    for e in entities:
        kind = e["kind"]
        if kind in ("rect", "square"):
            cx, cy = float(e["center"][0]), float(e["center"][1])
            w, h = (float(e["side"]),) * 2 if kind == "square" else (float(e["width"]), float(e["height"]))
            pts = [
                (cx - w / 2, cy - h / 2),
                (cx + w / 2, cy - h / 2),
                (cx + w / 2, cy + h / 2),
                (cx - w / 2, cy + h / 2),
            ]
            chamfer = oc.rect_chamfer(e, op) if kind == "rect" else None
            if chamfer is not None:
                pts = oc.chamfer_points(pts, chamfer)
            polygon = Part.makePolygon([Vector(x, y, 0.0) for x, y in [*pts, pts[0]]])
            faces.append(Part.Face(polygon))
        elif kind == "circle":
            circle = Part.Circle(
                Vector(float(e["center"][0]), float(e["center"][1]), 0.0), Vector(0, 0, 1), float(e["diameter"]) / 2.0
            )
            faces.append(Part.Face(Part.Wire([circle.toShape()])))
    try:
        for e in (e for e in entities if e["kind"] == "slot"):
            faces.extend(_local_face(loop) for loop in oc.chain_loops(oc.slot_segments(e)))
        chain = [
            oc.line_segment(e) if e["kind"] == "line" else oc.arc_segment(e)
            for e in entities
            if e["kind"] == "line" or e["kind"] in oc.ARC_KINDS
        ]
        faces.extend(_local_face(loop) for loop in oc.chain_loops(chain))
    except ValueError as exc:
        raise invalid_request(str(exc), op) from exc
    return faces


def _signs(faces: list[Any]) -> list[int]:
    """+1 for a loop that adds material, -1 for one that takes it away (even-odd: the number of larger faces that
    enclose it, by a point of its own outline)."""
    areas = [abs(f.Area) for f in faces]
    signs = []
    for i, face in enumerate(faces):
        point = face.Vertexes[0].Point
        depth = sum(
            1 for j, other in enumerate(faces) if j != i and areas[j] > areas[i] and other.isInside(point, 1e-7, True)
        )
        signs.append(1 if depth % 2 == 0 else -1)
    return signs


def _host(doc: reg.DocumentRec, mode: str, sketch: reg.SketchRec | None, face_body: str | None, op: str) -> Any:
    """The body the op would act on, or ``None`` when a boss would start a body of its own."""
    if face_body is not None:
        return reg.resolve_body(doc, face_body, op)
    assert sketch is not None
    if mode == "cut":
        return oc.cut_body(doc, sketch, op)
    if sketch.on_face is not None:
        rec, comp = reg.resolve_face(doc, sketch.on_face, op)
        if comp is not None:
            raise not_supported("construction on a component face is not supported", op)
        return reg.resolve_body(doc, rec.body, op)
    bodies = doc.live_bodies()
    return bodies[0] if len(bodies) == 1 else None


def _sketch_tool(doc: reg.DocumentRec, params: dict[str, Any], op: str) -> tuple[float, float, Any]:
    sketch = reg.resolve_sketch(doc, reg.handle_id(params["sketch"], op, "sketch"), op)
    lo, hi = float(params["lo_mm"]), float(params["hi_mm"])
    faces = _sketch_faces(list(sketch.spec.get("entities", ())), op)
    signs = _signs(faces)
    placement = g.frame_placement(sketch.frame)
    host = _host(doc, str(params["mode"]), sketch, None, op)
    host_shape = reg.world_shape(doc, host) if host is not None else None
    tool_volume = common_volume = 0.0
    for face, sign in zip(faces, signs, strict=True):
        face.translate(Vector(0.0, 0.0, lo))
        prism = face.extrude(Vector(0.0, 0.0, hi - lo))
        prism.Placement = placement
        tool_volume += sign * float(prism.Volume)
        if host_shape is not None and not host_shape.isNull():
            common_volume += sign * float(host_shape.common(prism).Volume)
    return tool_volume, common_volume, host


def _hole_origin(spec: dict[str, Any], desc: dict[str, Any]) -> g.Vec3:
    """Where the hole's ``position`` is measured from: the request's origin slid onto the plane of the face, else the
    centre of the face (see ``ops_features._origin_on``)."""
    centre = tuple(desc["center"])
    wanted = spec.get("origin")
    if wanted is None:
        return centre
    n = g.unit(tuple(desc["normal"]))
    point = g.vec(wanted)
    return g.add(point, g.scale(n, g.dot(g.sub(centre, point), n)))


def _hole_tool(doc: reg.DocumentRec, params: dict[str, Any], op: str) -> tuple[float, float, Any]:
    spec = params["hole"]
    face_id = reg.handle_id(spec["face"], op, "hole.face")
    rec, comp = reg.resolve_face(doc, face_id, op)
    if comp is not None:
        raise not_supported("hole on a component face is not supported", op)
    desc = face_descriptor(doc, rec, comp)
    if desc["normal"] is None:
        raise not_supported("tool_overlap measures holes on planar faces only", op)
    frame = g.frame_for_face(_hole_origin(spec, desc), tuple(desc["normal"]))
    drill = g.scale(tuple(desc["normal"]), -1.0)
    entry = g.frame_to_world(frame, float(spec["position"][0]), float(spec["position"][1]))
    outline = [(float(t), float(r)) for t, r in spec["outline"]]
    polygon = Part.makePolygon([Vector(r, 0.0, t) for t, r in [*outline, outline[0]]])
    tool = Part.Face(polygon).revolve(Vector(0, 0, 0), Vector(0, 0, 1), 360.0)
    tool.Placement = Placement(g.fcvec(entry), Rotation(Vector(0, 0, 1), g.fcvec(drill)))
    host = reg.resolve_body(doc, rec.body, op)
    host_shape = reg.world_shape(doc, host)
    common = 0.0 if host_shape.isNull() else float(host_shape.common(tool).Volume)
    return float(tool.Volume), common, host


def tool_overlap(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    op = str(params.get("operation_id", ""))
    if params.get("mode") not in ("cut", "add"):
        raise invalid_request("tool_overlap mode must be cut or add", op)
    if (params.get("sketch") is None) == (params.get("hole") is None):
        raise invalid_request("tool_overlap needs exactly one of sketch and hole", op)
    if params.get("hole") is not None:
        tool_volume, common_volume, host = _hole_tool(doc, params, op)
    else:
        if float(params["hi_mm"]) - float(params["lo_mm"]) <= DEPTH_TOL or not math.isfinite(float(params["hi_mm"])):
            raise invalid_request("tool_overlap needs hi_mm greater than lo_mm", op)
        tool_volume, common_volume, host = _sketch_tool(doc, params, op)
    return {
        "tool_volume_mm3": tool_volume,
        "common_volume_mm3": common_volume,
        "body": {"id": host.id} if host is not None else None,
    }
