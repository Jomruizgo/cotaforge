"""Sketch-based features, patterns and finishing operations: sketch, extrude, cut, revolve, hole, linear_pattern,
circular_pattern, chamfer, fillet, shell. Every construction op runs through ``ops_common.run_op``.
"""

import math
from typing import Any

import fc
import geometry as g
import ops_common as oc
import Part
import registry as reg
from FreeCAD import Placement, Rotation, Vector
from protocol import WorkerError, geometry_failed, invalid_request, not_supported, unrecoverable
from queries import edge_descriptor, face_descriptor

TRANSFORMABLE = {"extrude", "cut", "revolve", "hole"}
FEATURE_TYPES = {
    "extrude": "PartDesign::Pad",
    "cut": "PartDesign::Pocket",
    "revolve": "PartDesign::Revolution",
    "hole": "PartDesign::Hole",
}


def _op(params: dict[str, Any]) -> str:
    return str(params.get("operation_id", ""))


# --- sketch ------------------------------------------------------------------------------------------------------


def _origin_on(params: dict[str, Any], desc: dict[str, Any]) -> g.Vec3:
    """The origin of the frame on the planar face ``desc``: the ``origin`` the request names (a rebuild puts a feature
    where it stood), slid along the normal onto the plane of the face, or the centre of the face."""
    centre = tuple(desc["center"])
    wanted = params.get("origin")
    if wanted is None:
        return centre
    n = g.unit(tuple(desc["normal"]))
    point = g.vec(wanted)
    return g.add(point, g.scale(n, g.dot(g.sub(centre, point), n)))


def sketch(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    op = _op(params)
    on = params.get("on")
    on_face: str | None = None
    if isinstance(on, str):
        if on not in g.PLANE_FRAMES:
            raise invalid_request(f"unknown plane {on}", op)
        frame = g.frame_for_plane(on)
    elif isinstance(on, dict) and "frame" in on:
        # a datum plane (contract ``DatumPlaneTarget``): the handle must be live, the frame travels with it and is
        # reported back unchanged so the sketch's own Placement is exactly the datum's pose
        registered = reg.resolve_datum_plane(doc, reg.handle_id(on.get("handle"), op, "on.handle"), op)
        try:
            g.realise_frame(on["frame"])
            frame = {key: [float(c) for c in on["frame"][key]] for key in ("origin", "u", "v", "n")}
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise invalid_request(f"sketch on a datum plane: invalid frame ({exc})", op) from exc
        if any(
            g.dist(g.vec(frame[key]), g.vec(registered.pose[key])) > g.FRAME_TOL for key in ("origin", "u", "v", "n")
        ):
            raise invalid_request(
                f"sketch on {registered.id}: the frame differs from the pose the datum was created with", op
            )
    else:
        face_id = reg.handle_id(on, op, "on")
        rec, comp = reg.resolve_face(doc, face_id, op)
        desc = face_descriptor(doc, rec, comp)
        if desc["normal"] is None:
            raise invalid_request(f"face {face_id} is not planar", op)
        frame = g.frame_for_face(_origin_on(params, desc), tuple(desc["normal"]))
        on_face = face_id
    entities = [dict(e) for e in params.get("entities", ())]
    if not entities:
        raise invalid_request("sketch needs at least one entity", op)
    profiles = oc.sketch_profiles(entities, op)
    names = {str(e["name"]) for e in entities}
    for c in params.get("constraints", ()):
        for ref in c.get("entities", ()):
            if str(ref).split(".", 1)[0] not in names:
                raise invalid_request(f"constraint refers to unknown entity {ref}", op)
    for nd in params.get("named_dimensions", ()):
        if str(nd.get("entity")) not in names:
            raise invalid_request(f"named dimension {nd.get('name')} refers to unknown entity {nd.get('entity')}", op)
    rec = reg.SketchRec(doc.next_id("sketch"), params, frame, profiles)
    rec.on_face = on_face
    doc.sketches[rec.id] = rec
    return {
        "sketch": {"id": rec.id},
        "frame": frame,
        "profile_areas_mm2": [p["area"] for p in profiles],
        "warnings": [
            f"rect {e['name']}: four corners chamfered {float(e['chamfer_mm']):g} mm at 45 degrees"
            for e in entities
            if e["kind"] == "rect" and e.get("chamfer_mm") is not None
        ],
    }


# --- datum plane / datum axis (ir ops ref_plane / ref_axis) ---------------------------------------------------------
#
# Fixed-pose datums (decision 2 of the ref_plane/ref_axis plan): the executor computed the pose, the worker only
# realises it. No FreeCAD object is created (no PartDesign::Plane, nothing in the tree, no undo unit, no feature
# record): a datum is a registry entry plus the guarantee that a sketch on it gets exactly that Placement.


def datum_plane(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    op = _op(params)
    requested = params.get("frame")
    if not isinstance(requested, dict):
        raise invalid_request("datum_plane needs a frame", op)
    try:
        frame = g.realise_frame(requested)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise invalid_request(f"datum_plane: {exc}", op) from exc
    rec = reg.DatumRec(doc.next_id("datum_plane"), "plane", frame)
    doc.datums[rec.id] = rec
    return {"datum": {"id": rec.id}, "frame": frame, "rebuild_ok": True}


def datum_axis(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    op = _op(params)
    requested = params.get("axis")
    if not isinstance(requested, dict):
        raise invalid_request("datum_axis needs an axis", op)
    try:
        origin = g.vec(requested["origin"])
        direction = g.unit(g.vec(requested["direction"]))
        if not all(math.isfinite(c) for c in (*origin, *direction)):
            raise ValueError("non-finite numbers")
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise invalid_request(f"datum_axis: invalid axis ({exc})", op) from exc
    axis = g.axis_dict(origin, direction)
    rec = reg.DatumRec(doc.next_id("datum_axis"), "axis", axis)
    doc.datums[rec.id] = rec
    return {"datum": {"id": rec.id}, "axis": axis, "rebuild_ok": True}


def _sketch_of(doc: reg.DocumentRec, params: dict[str, Any]) -> reg.SketchRec:
    op = _op(params)
    return reg.resolve_sketch(doc, reg.handle_id(params["sketch"], op, "sketch"), op)


def _consume_sketch(ctx: oc.OpContext, sk: reg.SketchRec, body: reg.BodyRec, name: str) -> Any:
    fc_body = oc.ensure_partdesign(ctx.doc, body, ctx.feature)
    # the sketch object carries the id of the sketch STEP (what the agent calls it), not "<consumer>_sketch"
    label = str(sk.spec.get("operation_id") or "") or ctx.label("sketch")
    sketch_obj = oc.materialise_sketch(ctx.doc, fc_body, sk, name, label=label)
    ctx.feature.fc_names.append(sketch_obj.Name)
    ctx.feature.sketch = sk.id
    ctx.feature.named_dimensions = dict(sk.named_dimensions)
    ctx.check_objects.append(sketch_obj)
    return fc_body, sketch_obj


def _finish_feature(ctx: oc.OpContext, fc_body: Any, feature_obj: Any, body: reg.BodyRec) -> None:
    fc.set_prop(fc_body, "Tip", feature_obj)
    ctx.feature.fc_names.append(feature_obj.Name)
    ctx.feature.fc_main = feature_obj.Name
    ctx.adopt(feature_obj)
    ctx.check_objects.append(feature_obj)
    ctx.bodies.append(body)


# --- extrude / cut / revolve --------------------------------------------------------------------------------


def extrude(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    op = _op(params)
    sk = _sketch_of(doc, params)
    depth = float(params["depth_mm"])
    if depth <= 0:
        raise invalid_request("extrude depth_mm must be positive", op)
    merge = bool(params.get("merge", True))
    direction = params.get("direction", "positive")
    both = bool(params.get("both_directions", False))
    depth2 = params.get("depth2_mm")

    def build(ctx: oc.OpContext) -> None:
        body = oc.boss_body(doc, sk, merge, ctx.feature)
        fc_body, sketch_obj = _consume_sketch(ctx, sk, body, "Sketch")
        pad = fc.new_in_body(fc_body, "PartDesign::Pad", "Pad", label=ctx.label())
        fc.set_prop(pad, "Profile", sketch_obj)
        if both:
            fc.set_two_sides(pad)
            fc.set_prop(pad, "Length", depth)
            fc.set_prop(pad, "Length2", float(depth2) if depth2 is not None else depth)
        else:
            fc.set_prop(pad, "Type", "Length")
            fc.set_prop(pad, "Length", depth)
        fc.set_prop(pad, "Reversed", direction == "negative")
        n = oc.dir_to_body_frame(fc_body, tuple(sk.frame["n"]))
        ctx.axis_hint = n if direction != "negative" else g.scale(n, -1.0)
        ctx.expect_volume = "increase"
        _finish_feature(ctx, fc_body, pad, body)

    return oc.run_op(doc, "extrude", params, build)


def cut(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    op = _op(params)
    sk = _sketch_of(doc, params)
    end = params.get("end_condition", "blind")
    depth = params.get("depth_mm")
    if end == "up_to_face":
        raise invalid_request("a cut ends blind or through_all; up_to_face is for holes", op)
    if end == "blind" and depth is None:
        raise invalid_request("blind cut requires depth_mm", op)
    direction = params.get("direction", "negative")
    both = bool(params.get("both_directions", False))
    depth2 = params.get("depth2_mm")
    body = oc.cut_body(doc, sk, op)
    scope = params.get("scope")
    if scope is not None:
        scope_ids = [reg.handle_id(item, op, "scope") for item in scope]
        if scope_ids != [body.id]:
            raise invalid_request(
                f"cut.scope {scope_ids!r} does not match {body.id!r}, the body its sketch belongs to: "
                "FreeCAD's PartDesign cut always affects exactly that one body -- scope cannot select a "
                "different one (this worker has no feature scope); omit it or name that body",
                op,
            )

    def build(ctx: oc.OpContext) -> None:
        fc_body, sketch_obj = _consume_sketch(ctx, sk, body, "Sketch")
        pocket = fc.new_in_body(fc_body, "PartDesign::Pocket", "Pocket", label=ctx.label())
        fc.set_prop(pocket, "Profile", sketch_obj)
        if end == "through_all":
            fc.set_prop(pocket, "Type", "ThroughAll")
            if both:
                fc.set_prop(pocket, "Midplane", True)
        elif both:
            fc.set_two_sides(pocket)
            fc.set_prop(pocket, "Length", float(depth))
            fc.set_prop(pocket, "Length2", float(depth2) if depth2 is not None else float(depth))
        else:
            fc.set_prop(pocket, "Type", "Length")
            fc.set_prop(pocket, "Length", float(depth))
        fc.set_prop(pocket, "Reversed", direction == "positive")
        n = oc.dir_to_body_frame(fc_body, tuple(sk.frame["n"]))
        ctx.axis_hint = g.scale(n, -1.0) if direction != "positive" else n
        ctx.expect_volume = "decrease"
        _finish_feature(ctx, fc_body, pocket, body)

    return oc.run_op(doc, "cut", params, build)


def revolve(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    op = _op(params)
    sk = _sketch_of(doc, params)
    axis = params["axis"]
    origin = g.vec(axis["origin"])
    direction = g.unit(g.vec(axis["direction"]))
    angle = float(params.get("angle_deg", 360.0))
    if any(p["kind"] == "circle" for p in sk.profiles):
        raise not_supported("revolved circles (tori) are not supported", op)

    def build(ctx: oc.OpContext) -> None:
        body = oc.boss_body(doc, sk, True, ctx.feature)
        fc_body, sketch_obj = _consume_sketch(ctx, sk, body, "Sketch")
        datum = fc.new_in_body(fc_body, "PartDesign::Line", "RevolveAxis", label=ctx.label("axis"))
        fc.set_prop(datum, "MapMode", "Deactivated")
        world_axis = Placement(g.fcvec(origin), Rotation(Vector(0, 0, 1), g.fcvec(direction)))
        fc.set_prop(datum, "Placement", oc.to_body_frame(fc_body, world_axis))
        ctx.feature.fc_names.append(datum.Name)
        rev = fc.new_in_body(fc_body, "PartDesign::Revolution", "Revolution", label=ctx.label())
        fc.set_prop(rev, "Profile", sketch_obj)
        fc.set_prop(rev, "ReferenceAxis", (datum, [""]))
        fc.set_prop(rev, "Angle", angle)
        ctx.axis_hint = oc.dir_to_body_frame(fc_body, direction)
        ctx.expect_volume = "increase"
        _finish_feature(ctx, fc_body, rev, body)

    return oc.run_op(doc, "revolve", params, build)


# --- sweep ---------------------------------------------------------------------------------------------------

SPINE_BINDER = True
"""How the spine reaches the pipe: through a ``PartDesign::SubShapeBinder`` inside the body (clean: no "out of the
allowed scope" warning, measured on FreeCAD 1.1.3) or a direct link to the document-level wire object. The switch
is off only in ``check_sweep.py``, which uses it to prove the direct link on its own; the direct link is also the
automatic fallback when the binder build leaves the pipe invalid."""

OVERLAP_HINT = (
    "a sweep adds area x length; its path must not run through existing material "
    "(start the path on the face and leave it)"
)

LAST_SPINE_LINK = ""
"""``"binder"`` or ``"direct"``: how the last sweep actually linked its spine (read by ``check_sweep.py``)."""


def _hide(obj: Any) -> None:
    view = getattr(obj, "ViewObject", None)
    if view is None:
        return
    try:
        view.Visibility = False
    except Exception:  # noqa: BLE001 - cosmetic only (headless worker has no ViewObject)
        pass


def _binder_sub_elements(edges: list[str]) -> list[str]:
    """Sub-elements of the binder that the pipe spine names. Explicit ``EdgeN`` works on FreeCAD 1.0 and 1.1; the
    whole-object form ``[""]`` only builds on 1.1 (1.0.2: "A fatal error occurred when making the pipe")."""
    return list(edges)


def _make_binder(ctx: oc.OpContext, fc_body: Any, spine: Any, edges: list[str]) -> Any | None:
    binder = fc.new_in_body(fc_body, "PartDesign::SubShapeBinder", "SweepSpineBinder", label=ctx.label("spine_binder"))
    fc.set_prop(binder, "Support", [(spine, tuple(edges))])
    fc.recompute(ctx.doc.fc_doc)
    if binder.Shape.isNull() or len(binder.Shape.Edges) != len(edges):
        fc.remove_object(ctx.doc.fc_doc, binder.Name)
        return None
    return binder


def sweep(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """``PartDesign::AdditivePipe`` of the single closed profile along a polyline spine (mitred corners, or
    ``Part.Arc`` fillets). Mode Standard + Transition "Right corner": the section stays normal to the path, so the
    volume is exactly area x path length (measured on FreeCAD 1.1.3; Fixed / Round corner / Transformed are not)."""
    op = _op(params)
    sk = _sketch_of(doc, params)
    if len(sk.profiles) != 1:
        raise invalid_request(
            f"sweep needs exactly ONE closed profile; sketch {sk.id} has {len(sk.profiles)} "
            "(a ring section is two profiles: not supported in v1)",
            op,
        )
    radius = params.get("corner_radius_mm")
    merge = bool(params.get("merge", True))
    try:
        points = [g.vec(p) for p in params["path"]]
        wire, wire_source = g.sweep_wire(points, None if radius is None else float(radius))
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise invalid_request(f"sweep path: {exc}", op) from exc
    start = g.frame_to_world(sk.frame, *sk.profiles[0]["centroid"])
    if g.dist(start, points[0]) > 1e-2:
        raise invalid_request(
            f"sweep profile centroid {g.clean(start)} is not at the path start {g.clean(points[0])}", op
        )

    def build(ctx: oc.OpContext) -> None:
        body = oc.boss_body(doc, sk, merge, ctx.feature)
        fc_body, sketch_obj = _consume_sketch(ctx, sk, body, "Sketch")
        spine = fc.add_object(doc.fc_doc, "Part::Feature", "SweepSpine", label=ctx.label("spine"))
        fc.set_prop(spine, "Shape", wire, source=wire_source)
        ctx.feature.fc_names.append(spine.Name)
        ctx.check_objects.append(spine)
        _hide(spine)
        edges = [f"Edge{i}" for i in range(1, len(wire.Edges) + 1)]
        binder = _make_binder(ctx, fc_body, spine, edges) if SPINE_BINDER else None
        pipe = fc.new_in_body(fc_body, "PartDesign::AdditivePipe", "Pipe", label=ctx.label())
        fc.set_prop(pipe, "Profile", sketch_obj)
        fc.set_prop(pipe, "Spine", (binder, _binder_sub_elements(edges)) if binder is not None else (spine, edges))
        fc.set_prop(pipe, "Mode", "Standard")
        fc.set_prop(pipe, "Transition", "Right corner")
        if binder is not None:
            fc.recompute(doc.fc_doc)
            if pipe.Shape.isNull() or not oc.feature_state_ok(pipe):
                # the binder route did not build the pipe (version quirk): link the wire object directly
                fc.set_prop(pipe, "Spine", (spine, edges))
                fc.remove_object(doc.fc_doc, binder.Name)
                binder = None
        if binder is not None:
            ctx.feature.fc_names.append(binder.Name)
            ctx.check_objects.append(binder)
            _hide(binder)
        global LAST_SPINE_LINK  # noqa: PLW0603
        LAST_SPINE_LINK = "binder" if binder is not None else "direct"
        ctx.expect_volume = "increase"
        ctx.no_material_hint = OVERLAP_HINT

        def valid_shape() -> None:
            # a fillet radius below the section reach collapses the inner side of the bend: the kernel then keeps the
            # right volume and State "Up-to-date" while the solid is invalid (measured), so check the shape itself
            body.invalidate()
            shape = reg.topo_shape(doc, body)
            if shape.isNull() or not shape.isValid():
                raise geometry_failed("the swept solid is not valid (the section does not fit the bend)", op)
            try:
                shape.check(True)  # BOP check: self-intersection, which isValid() alone does not report
            except Exception as exc:  # noqa: BLE001 - OCC raises a plain exception naming the defect
                raise geometry_failed(f"the swept solid is not valid ({exc}); {OVERLAP_HINT}", op) from exc

        ctx.post_check = valid_shape
        _finish_feature(ctx, fc_body, pipe, body)

    return oc.run_op(doc, "sweep", params, build)


# --- hole ------------------------------------------------------------------------------------------------------


TAP_DRILL_TOLERANCE_MM = 1e-3
FACE_TOLERANCE_MM = 1e-4
HOLE_EDGE_CLEARANCE_MM = 0.05
"""A radial hole must keep this far from the boundary of its face: a circle tangent to an edge makes a degenerate
edge in the kernel that later fails in observation, and the agent cannot act on that."""


def _radial_entry(
    doc: reg.DocumentRec,
    rec: reg.FaceRec,
    desc: dict[str, Any],
    face_id: str,
    position: Any,
    diameter: float,
    body: reg.BodyRec,
    op: str,
) -> tuple[dict[str, Any], dict[str, Any], g.Vec3]:
    """The entry of a hole on a cylindrical face: (entry, tangent-plane sketch frame, world drill direction).

    The hole sketch is the circle in the plane tangent to the cylinder at the entry point (origin on the surface, ``n``
    the outward radial, so PartDesign drills along ``-n`` like on a planar face) and its depth is measured from that
    plane, i.e. from the surface point on the hole's own axis (measured on both versions). Only the CONVEX wall of a
    tube or shaft is supported: the material of a bore lies outside it, and the entry plane would cut air. The whole
    footprint of the hole must lie on the face: PartDesign would silently cut a notch from an edge it crosses."""
    axis = desc["axis"]
    axis_dir = g.vec(axis["direction"])
    radius = float(desc["radius"])
    entry = g.radial_entry(g.vec(desc["center"]), g.vec(axis["origin"]), axis_dir, radius, (position[0], position[1]))
    point, outward = g.vec(entry["surface_point"]), g.vec(entry["outward"])
    face = reg.world_shape(doc, body).Faces[rec.index]
    u, v = face.Surface.parameter(g.fcvec(point))
    if g.dot(g.vec(face.normalAt(u, v)), outward) <= 0.0:
        raise invalid_request(
            f"face {face_id} is a concave cylinder (a bore): its material lies outside it, so a hole entering from "
            "its surface would cut air. Drill across a bore from the outer wall of the tube instead, or put the hole "
            "on a datum plane (ref_plane through the bore) with a cut",
            op,
        )
    _, _, v0, v1 = face.ParameterRange
    half = abs(v1 - v0) / 2.0
    where = [float(position[0]), float(position[1])]
    axial = float(position[0])

    def on_face(p: g.Vec3) -> bool:
        return bool(face.distToShape(Part.Vertex(g.fcvec(p)))[0] <= FACE_TOLERANCE_MM)

    if not on_face(point):
        if abs(axial) > half + FACE_TOLERANCE_MM:
            raise invalid_request(
                f"position {where} is not on face {face_id}: axial_mm {axial:g} is beyond the face; it is measured "
                f"along the cylinder axis (pointing up: its Z, else Y, else X component positive) from the face "
                f"centre and must stay within +-{half:.6g} mm",
                op,
            )
        raise invalid_request(
            f"position {where} is not on face {face_id}: angle_deg {where[1]:g} is outside the angular range the "
            "face covers (a trimmed face covers only part of the angle; angle 0 is world +X projected perpendicular "
            "to the axis, positive by the right-hand rule about the axis pointing up)",
            op,
        )
    outside = [
        z
        for p, z in g.radial_footprint(entry, axis_dir, radius, diameter / 2.0 + HOLE_EDGE_CLEARANCE_MM)
        if not on_face(p)
    ]
    if outside:
        if any(abs(axial + z) > half + FACE_TOLERANCE_MM for z in outside):
            raise invalid_request(
                f"the D{diameter:g} hole at {where} is not fully on face {face_id}: it crosses the axial end of the "
                f"face, or leaves less than {HOLE_EDGE_CLEARANCE_MM:g} mm of clearance to it (the face spans "
                f"+-{half:.6g} mm from its centre): keep |axial_mm| <= "
                f"{max(half - diameter / 2.0 - HOLE_EDGE_CLEARANCE_MM, 0.0):.6g}",
                op,
            )
        raise invalid_request(
            f"the D{diameter:g} hole at {where} is not fully on face {face_id}: it crosses an angular edge of the "
            "face, or leaves less than 0.05 mm of clearance to it (the face covers only part of the angle): move "
            "angle_deg away from the edge or use a smaller diameter",
            op,
        )
    return entry, g.radial_sketch_frame(entry, axis_dir), g.scale(outward, -1.0)


def _apply_thread(h: Any, thread: dict[str, Any], diameter: float, op: str) -> None:
    """Cosmetic tapped hole (``Threaded`` with ``ModelThread`` off): FreeCAD drills the tap drill of the size and draws
    no thread geometry, so the shape is the plain cylinder. ``ThreadDepth`` is metadata with no geometric effect."""
    fc.set_prop(h, "Threaded", True)
    fc.set_prop(h, "ModelThread", False)
    if not fc.set_thread_size(h, str(thread["size"])):  # also sets ThreadType: coarse or fine profile
        raise not_supported(f"this FreeCAD has no {thread['size']} in its ISO metric thread table", op)
    fc.set_prop(h, "Diameter", diameter)
    depth = thread.get("depth_mm")
    if depth is None:
        fc.set_prop(h, "ThreadDepthType", "Hole Depth")
    else:
        fc.set_prop(h, "ThreadDepthType", "Dimension")
        fc.set_prop(h, "ThreadDepth", float(depth))


def _apply_entry_and_point(
    h: Any, counterbore: dict[str, Any] | None, countersink: dict[str, Any] | None, angled: bool, params: dict[str, Any]
) -> None:
    """The entry shape and the drill point, with FreeCAD's own property names. ``HoleCutCustomValues`` makes FreeCAD use
    the numbers given here, not the ones it derives from a thread size. A blind ``Depth`` runs from the entry plane
    and includes the counterbore; an angled point adds its cone beyond ``Depth`` (``DrillForDepth`` stays off)."""
    if counterbore is not None:
        fc.set_prop(h, "HoleCutType", "Counterbore")
        fc.set_prop(h, "HoleCutCustomValues", True)
        fc.set_prop(h, "HoleCutDiameter", float(counterbore["diameter_mm"]))
        fc.set_prop(h, "HoleCutDepth", float(counterbore["depth_mm"]))
    elif countersink is not None:
        fc.set_prop(h, "HoleCutType", "Countersink")
        fc.set_prop(h, "HoleCutCustomValues", True)
        fc.set_prop(h, "HoleCutDiameter", float(countersink["diameter_mm"]))
        fc.set_prop(h, "HoleCutCountersinkAngle", float(countersink.get("angle_deg", 90.0)))
    else:
        fc.set_prop(h, "HoleCutType", "None")
    if angled:
        fc.set_prop(h, "DrillPoint", "Angled")
        fc.set_prop(h, "DrillPointAngle", float(params.get("drill_point_angle_deg", 118.0)))
    else:
        fc.set_prop(h, "DrillPoint", "Flat")


def _depth_to_face(
    doc: reg.DocumentRec,
    params: dict[str, Any],
    frame: dict[str, Any],
    centre: list[float],
    drill: g.Vec3,
    entry_face_id: str,
    op: str,
) -> float:
    """``up_to_face``: PartDesign::Hole has no such depth type (its DepthType is Dimension or ThroughAll), so the depth
    is the distance from the entry plane to the plane of the target face along the hole axis. Only a planar face
    normal to the axis has one distance for the whole hole."""
    up_id = reg.handle_id(params["up_to_face"], op, "up_to_face")
    if up_id == entry_face_id:
        raise invalid_request("up_to_face is the entry face itself", op)
    target, component = reg.resolve_face(doc, up_id, op)
    if component is not None:
        raise not_supported("up_to_face on a component face is not supported", op)
    info = face_descriptor(doc, target, component)
    if info["normal"] is None:
        raise invalid_request(
            f"up_to_face {up_id} is not planar: PartDesign::Hole cannot end on a curved face "
            "(use a blind depth_mm, or end the hole on a flat face)",
            op,
        )
    normal = g.unit(g.vec(info["normal"]))
    if abs(g.dot(normal, drill)) < 1.0 - 1e-6:
        raise invalid_request(
            f"up_to_face {up_id} is not normal to the hole axis: PartDesign::Hole has a flat or conical bottom and "
            "cannot end on an oblique face (use a blind depth_mm)",
            op,
        )
    entry = g.add(
        g.vec(frame["origin"]), g.add(g.scale(g.vec(frame["u"]), centre[0]), g.scale(g.vec(frame["v"]), centre[1]))
    )
    depth = g.dot(g.sub(g.vec(info["center"]), entry), drill)
    if depth <= FACE_TOLERANCE_MM:
        raise invalid_request(
            f"up_to_face {up_id} is not ahead of the hole: it lies {depth:.4g} mm along the drill direction from "
            f"the entry face {entry_face_id}",
            op,
        )
    return float(depth)


def _check_planar_entry(
    doc: reg.DocumentRec, rec: Any, frame: dict[str, Any], body: Any, face_id: str, centre: list[float], op: str
) -> None:
    """The axis of a hole on a planar face must start ON the face, not merely inside its bounding box: a face with an
    inner wire (the top of a plate around a boss that stands on it) spans the box of the plate, yet the material under
    its centre is covered by the boss, so a hole 'entering' there starts inside the part and never touches the face."""
    origin, u, v = g.vec(frame["origin"]), g.vec(frame["u"]), g.vec(frame["v"])
    point = g.add(origin, g.add(g.scale(u, centre[0]), g.scale(v, centre[1])))
    face = reg.world_shape(doc, body).Faces[rec.index]
    if face.distToShape(Part.Vertex(g.fcvec(point)))[0] > FACE_TOLERANCE_MM:
        raise invalid_request(
            f"position {centre} is not on face {face_id}: the point ({point[0]:.3f}, {point[1]:.3f}, {point[2]:.3f}) "
            "lies in the bounding box of the face but outside the face itself (a hole in it, or a feature standing "
            "on it covers that spot). To drill the top of a boss select the boss's own top face, not the face it "
            "stands on",
            op,
        )


def hole(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    op = _op(params)
    face_id = reg.handle_id(params["face"], op, "face")
    rec, comp = reg.resolve_face(doc, face_id, op)
    if comp is not None:
        raise not_supported("hole on a component face is not supported", op)
    desc = face_descriptor(doc, rec, comp)
    cylindrical = desc["surface"] == "cylinder" and desc["axis"] is not None and desc["radius"] is not None
    if desc["normal"] is None and not cylindrical:
        raise invalid_request(f"face {face_id} is not planar", op)
    end = params.get("end_condition", "through_all")
    depth = params.get("depth_mm")
    if end == "blind" and depth is None:
        raise invalid_request("blind hole requires depth_mm", op)
    diameter = float(params["diameter_mm"])
    if diameter <= 0:
        raise invalid_request("hole diameter_mm must be positive", op)
    counterbore, countersink = params.get("counterbore"), params.get("countersink")
    # absent on the raw protocol = flat (the angled default of the IR and the contract always arrives explicit)
    angled = params.get("drill_point", "flat") == "angled" and end != "through_all"  # a through hole has no bottom
    if counterbore is not None and countersink is not None:
        raise invalid_request("a hole takes a counterbore or a countersink, not both", op)
    if cylindrical and (counterbore or countersink or angled or end == "up_to_face"):
        raise not_supported(
            "counterbore, countersink, an angled drill point and up_to_face are supported on planar faces only: a "
            "hole across the wall of a tube or shaft is a plain cylinder",
            op,
        )
    position = params["position"]
    body = reg.resolve_body(doc, rec.body, op)
    if cylindrical:
        _, frame, drill = _radial_entry(doc, rec, desc, face_id, position, diameter, body, op)
        centre = [0.0, 0.0]  # the sketch origin IS the entry point
    else:
        frame = g.frame_for_face(_origin_on(params, desc), tuple(desc["normal"]))
        drill = g.scale(tuple(desc["normal"]), -1.0)
        centre = [float(position[0]), float(position[1])]
        _check_planar_entry(doc, rec, frame, body, face_id, centre, op)
    emulation: str | None = None
    if end == "up_to_face":
        depth = _depth_to_face(doc, params, frame, centre, drill, face_id, op)
        end = "blind"
        emulation = (
            f"up_to_face is emulated (PartDesign::Hole has no such end): depth {depth:.6g} mm measured to "
            f"{params['up_to_face']['id']} now; edit or re-run the step to measure it again"
        )
    thread = params.get("thread")
    spec = {
        "operation_id": op,
        "entities": [{"kind": "circle", "name": "hole", "center": centre, "diameter": diameter}],
    }
    circle = reg.SketchRec(doc.next_id("sketch"), spec, frame, oc.sketch_profiles(spec["entities"], op))
    circle.on_face = face_id

    def build(ctx: oc.OpContext) -> None:
        fc_body = oc.ensure_partdesign(doc, body, ctx.feature)
        if emulation is not None:
            ctx.feature.warnings.append(emulation)
        sketch_obj = oc.materialise_sketch(doc, fc_body, circle, "HoleSketch", label=ctx.label("sketch"))
        ctx.feature.fc_names.append(sketch_obj.Name)
        ctx.check_objects.append(sketch_obj)
        h = fc.new_in_body(fc_body, "PartDesign::Hole", "Hole", label=ctx.label())
        fc.set_prop(h, "Profile", sketch_obj)
        fc.set_prop(h, "Diameter", diameter)
        if thread is not None:
            _apply_thread(h, thread, diameter, op)
        else:
            fc.set_prop(h, "Threaded", False)
            fc.set_prop(h, "ThreadType", "None")
        _apply_entry_and_point(h, counterbore, countersink, angled, params)
        fc.set_prop(h, "Reversed", False)
        if end == "through_all":
            fc.set_prop(h, "DepthType", "ThroughAll")
        else:
            fc.set_prop(h, "DepthType", "Dimension")
            fc.set_prop(h, "Depth", float(depth))
        ctx.axis_hint = oc.dir_to_body_frame(fc_body, drill)
        ctx.expect_volume = "decrease"
        if thread is not None:
            # FreeCAD silently overrides ``Diameter`` with ITS tap drill for a threaded hole: the IR derives the same
            # number from contract.threads, so a difference means the tables drifted apart and the hole would not be
            # the one the plan (and the verification) describe
            def check_tap_drill() -> None:
                drilled = float(getattr(h.Diameter, "Value", h.Diameter))
                if abs(drilled - diameter) > TAP_DRILL_TOLERANCE_MM:
                    raise geometry_failed(
                        f"FreeCAD tap drill {drilled:g} mm for {thread['size']} differs from the table's "
                        f"{diameter:g} mm (contract.threads): the hole would not be the one verified",
                        op,
                    )

            ctx.post_check = check_tap_drill
        _finish_feature(ctx, fc_body, h, body)

    result = oc.run_op(doc, "hole", params, build)
    if not cylindrical:
        result["place"] = frame
    return result


# --- patterns --------------------------------------------------------------------------------------------------


def _pattern_seeds(doc: reg.DocumentRec, params: dict[str, Any]) -> tuple[list[reg.FeatureRec], reg.BodyRec]:
    op = _op(params)
    seeds = [doc.feature(reg.handle_id(f, op, "features"), op) for f in params.get("features", ())]
    if not seeds:
        raise invalid_request("pattern requires at least one feature", op)
    bodies: set[str] = set()
    for seed in seeds:
        if seed.kind not in TRANSFORMABLE or seed.fc_main is None:
            raise invalid_request(f"feature {seed.id} ({seed.kind}) cannot be patterned", op)
        bodies.update(seed.modified_bodies)
        bodies.update(seed.created_bodies)
    if len(bodies) != 1:
        raise invalid_request("pattern features must belong to exactly one body", op)
    body = reg.resolve_body(doc, next(iter(bodies)), op)
    if body.kind != "partdesign":
        raise invalid_request("pattern requires a PartDesign body", op)
    return seeds, body


def _datum_line(ctx: oc.OpContext, fc_body: Any, name: str, role: str, origin: g.Vec3, direction: g.Vec3) -> Any:
    datum = fc.new_in_body(fc_body, "PartDesign::Line", name, label=ctx.label(role))
    fc.set_prop(datum, "MapMode", "Deactivated")
    world = Placement(g.fcvec(origin), Rotation(Vector(0, 0, 1), g.fcvec(g.unit(direction))))
    fc.set_prop(datum, "Placement", oc.to_body_frame(fc_body, world))
    ctx.feature.fc_names.append(datum.Name)
    return datum


def _seed_axis_hint(seeds: list[reg.FeatureRec], body: reg.BodyRec) -> g.Vec3 | None:
    for seed in seeds:
        for fid in seed.created_faces:
            rec = body.faces.get(fid)
            if rec is not None and rec.axis_dir is not None:
                return rec.axis_dir
    return None


def linear_pattern(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    op = _op(params)
    seeds, body = _pattern_seeds(doc, params)
    direction = g.unit(g.vec(params["direction"]))
    spacing = float(params["spacing_mm"])
    count = int(params["count"])
    if count < 2 or spacing <= 0:
        raise invalid_request("linear pattern needs count >= 2 and positive spacing", op)
    direction2 = params.get("direction2")
    spacing2 = params.get("spacing2_mm")
    count2 = params.get("count2")
    second = direction2 is not None
    if second and (spacing2 is None or count2 is None or int(count2) < 2):
        raise invalid_request("second pattern direction needs spacing2_mm and count2 >= 2", op)
    instances = count * (int(count2) if second else 1)

    def build(ctx: oc.OpContext) -> None:
        fc_body = doc.fc_doc.getObject(body.fc_body)
        originals = [doc.fc_doc.getObject(s.fc_main) for s in seeds]
        d1 = _datum_line(ctx, fc_body, "PatternDir1", "dir1", g.ORIGIN, direction)
        if not second:
            lp = fc.new_in_body(fc_body, "PartDesign::LinearPattern", "LinearPattern", label=ctx.label())
            fc.set_prop(lp, "Originals", originals)
            fc.set_prop(lp, "Direction", (d1, [""]))
            fc.set_pattern_mode(lp, "length")
            fc.set_prop(lp, "Length", spacing * (count - 1))
            fc.set_prop(lp, "Occurrences", count)
            main = lp
        else:
            d2 = _datum_line(ctx, fc_body, "PatternDir2", "dir2", g.ORIGIN, g.unit(g.vec(direction2)))
            mt = fc.new_in_body(fc_body, "PartDesign::MultiTransform", "MultiTransform", label=ctx.label())
            fc.set_prop(mt, "Originals", originals)
            lp1 = fc.new_in_body(fc_body, "PartDesign::LinearPattern", "LinearPattern", label=ctx.label("pattern1"))
            fc.set_prop(lp1, "Direction", (d1, [""]))
            fc.set_pattern_mode(lp1, "length")
            fc.set_prop(lp1, "Length", spacing * (count - 1))
            fc.set_prop(lp1, "Occurrences", count)
            lp2 = fc.new_in_body(fc_body, "PartDesign::LinearPattern", "LinearPattern", label=ctx.label("pattern2"))
            fc.set_prop(lp2, "Direction", (d2, [""]))
            fc.set_pattern_mode(lp2, "length")
            fc.set_prop(lp2, "Length", float(spacing2) * (int(count2) - 1))
            fc.set_prop(lp2, "Occurrences", int(count2))
            fc.set_prop(mt, "Transformations", [lp1, lp2])
            ctx.feature.fc_names.extend([lp1.Name, lp2.Name])
            main = mt
        ctx.feature.instance_count = instances
        ctx.axis_hint = _seed_axis_hint(seeds, body)
        _finish_feature(ctx, fc_body, main, body)

    return oc.run_op(doc, "linear_pattern", params, build)


def circular_pattern(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    op = _op(params)
    seeds, body = _pattern_seeds(doc, params)
    axis = params["axis"]
    origin = g.vec(axis["origin"])
    direction = g.unit(g.vec(axis["direction"]))
    count = int(params["count"])
    angle = float(params.get("angle_deg", 360.0))
    equal = bool(params.get("equal_spacing", True))
    if count < 2:
        raise invalid_request("circular pattern needs count >= 2", op)

    def build(ctx: oc.OpContext) -> None:
        fc_body = doc.fc_doc.getObject(body.fc_body)
        originals = [doc.fc_doc.getObject(s.fc_main) for s in seeds]
        datum = _datum_line(ctx, fc_body, "PatternAxis", "axis", origin, direction)
        pp = fc.new_in_body(fc_body, "PartDesign::PolarPattern", "PolarPattern", label=ctx.label())
        fc.set_prop(pp, "Originals", originals)
        fc.set_prop(pp, "Axis", (datum, [""]))
        if equal:
            fc.set_pattern_mode(pp, "angle")
            fc.set_prop(pp, "Angle", angle)
        else:
            fc.set_pattern_mode(pp, "offset")
            fc.set_prop(pp, "Offset", angle)
        fc.set_prop(pp, "Occurrences", count)
        ctx.feature.instance_count = count
        hint = _seed_axis_hint(seeds, body)
        local_direction = oc.dir_to_body_frame(fc_body, direction)
        ctx.axis_hint = hint if hint is not None and abs(g.dot(hint, local_direction)) > 1 - 1e-6 else None
        _finish_feature(ctx, fc_body, pp, body)

    return oc.run_op(doc, "circular_pattern", params, build)


# --- mirror -----------------------------------------------------------------------------------------------------------

SEPARATE_SOLID = "a reflected boss floating, because it does not touch the body, or a cut splitting the body in two"
"""PartDesign keeps one solid: a second one after a mirror comes from one of these two."""

ADDITIVE_KINDS = {"extrude", "revolve"}
"""Seed kinds that add material; the others (a cut, a hole) remove it."""


def _mirror_seeds(doc: reg.DocumentRec, params: dict[str, Any]) -> tuple[list[reg.FeatureRec], reg.BodyRec]:
    """The features to reflect and the one PartDesign body they belong to. A primitive body (box, cylinder, a boolean
    result, a copy) has no features to reflect: that is ``mirror_body``, so say so instead of failing obscurely."""
    op = _op(params)
    seeds = [doc.feature(reg.handle_id(f, op, "features"), op) for f in params.get("features", ())]
    if not seeds:
        raise invalid_request("mirror requires at least one feature", op)
    bodies: set[str] = set()
    for seed in seeds:
        if seed.kind not in TRANSFORMABLE or seed.fc_main is None:
            raise not_supported(_seed_refusal(seed), op)
        bodies.update(seed.modified_bodies)
        bodies.update(seed.created_bodies)
    if len(bodies) != 1:
        raise invalid_request("mirror features must belong to exactly one body", op)
    body = reg.resolve_body(doc, next(iter(bodies)), op)
    if body.kind != "partdesign":
        raise not_supported("mirror requires a PartDesign body (a primitive body has no features): use mirror_body", op)
    return seeds, body


def _seed_refusal(seed: reg.FeatureRec) -> str:
    """Why this feature cannot be a mirror seed, and what to do instead (the right advice depends on what it is)."""
    head = "mirror reflects extrude, cut, revolve and hole features of a PartDesign body"
    if seed.kind in ("linear_pattern", "circular_pattern", "mirror"):
        return (
            f"{head}; feature {seed.id} is a {seed.kind}, which cannot be a seed: mirror the pattern's seed features "
            "and re-pattern them (or mirror the seeds together with their pattern's source)"
        )
    if seed.kind == "sweep":
        return (
            f"{head}; feature {seed.id} is a sweep, which is not supported as a mirror seed yet: mirror_body can "
            "reflect the whole body instead"
        )
    if seed.kind in ("chamfer", "fillet", "shell", "draft"):
        return (
            f"{head}; feature {seed.id} is a {seed.kind}, a dress-up of other faces: mirror the features it dresses "
            "before dressing them, or dress the reflection too"
        )
    return f"{head}; feature {seed.id} is a {seed.kind}: to reflect a whole body into a new one use mirror_body"


def _seed_axis_senses(seeds: list[reg.FeatureRec], body: reg.BodyRec) -> list[g.Vec3]:
    """The distinct intended axis senses (body-local) of the cylindrical/conical faces the seeds created."""
    found: list[g.Vec3] = []
    for seed in seeds:
        for fid in seed.created_faces:
            rec = body.faces.get(fid)
            if rec is None or rec.axis_dir is None:
                continue
            if not any(g.dist(rec.axis_dir, other) < 1e-6 for other in found):
                found.append(rec.axis_dir)
    return found


def mirror(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """Reflect PartDesign features across a plane through ``PartDesign::Mirrored`` with a ``PartDesign::Plane`` datum
    (Deactivated, at the plane's pose in body-local terms) as ``MirrorPlane``.

    ``Mirrored`` fails or does nothing quietly in three ways, all turned into a non-recoverable ``geometry_failed``
    (the kernel is deterministic): a reflection that coincides with the seed (nothing new), a reflected cut that misses
    the material, and a reflected boss that does not touch the body (PartDesign keeps what it can and a floating solid
    can survive). Seeds that add material and seeds that remove it are mirrored by one ``Mirrored`` each, so that each
    class is judged on ITS OWN contribution (a boss that adds material must not hide a bore that removed none); the
    result is still one operation, one feature record. The faces the seeds created carry an intended axis sense; the
    reflected ones get its reflection, but only when the seeds agree on one (a single sense cannot be right for
    cylinders along different axes)."""
    op = _op(params)
    seeds, body = _mirror_seeds(doc, params)
    plane, origin, normal = oc.mirror_plane(params, op)
    classes = [
        (label, group)
        for label, group in (
            ("boss", [seed for seed in seeds if seed.kind in ADDITIVE_KINDS]),
            ("cut", [seed for seed in seeds if seed.kind not in ADDITIVE_KINDS]),
        )
        if group
    ]
    before = reg.topo_shape(doc, body)
    volume_before = float(before.Volume)
    solids_before = len(before.Solids)

    def build(ctx: oc.OpContext) -> None:
        fc_body = doc.fc_doc.getObject(body.fc_body)
        datum = fc.new_in_body(fc_body, "PartDesign::Plane", "MirrorPlane", label=ctx.label("plane"))
        fc.set_prop(datum, "MapMode", "Deactivated")
        fc.set_prop(datum, "Placement", oc.to_body_frame(fc_body, g.frame_placement(plane)))
        _hide(datum)
        ctx.feature.fc_names.append(datum.Name)
        made: list[tuple[str, Any]] = []
        for label, group in classes:
            # one Mirrored is the op's own feature; with both a boss and a cut there are two, named by what they reflect
            mirrored_label = ctx.label() if len(classes) == 1 else ctx.label(label)
            mirrored = fc.new_in_body(fc_body, "PartDesign::Mirrored", "Mirrored", label=mirrored_label)
            fc.set_prop(mirrored, "Originals", [doc.fc_doc.getObject(s.fc_main) for s in group])
            fc.set_prop(mirrored, "MirrorPlane", (datum, [""]))
            made.append((label, mirrored))
            if len(made) < len(classes):  # the next Mirrored is built on this one: make it the tip, with a shape
                fc.set_prop(fc_body, "Tip", mirrored)
                fc.recompute(doc.fc_doc)
        for _, earlier in made[:-1]:  # ``_finish_feature`` records the last one
            ctx.feature.fc_names.append(earlier.Name)
            ctx.check_objects.append(earlier)
        ctx.feature.instance_count = 2
        senses = _seed_axis_senses(seeds, body)
        if len(senses) == 1:
            ctx.axis_hint = g.reflect_direction(senses[0], oc.dir_to_body_frame(fc_body, normal))
        _finish_feature(ctx, fc_body, made[-1][1], body)

        def post_check() -> None:
            body.invalidate()
            shape = reg.topo_shape(doc, body)
            if shape.isNull() or not shape.isValid():
                raise unrecoverable(geometry_failed("mirror produced an invalid shape", op))
            hint = (
                "the reflection coincides with the seed, or lands off the material: move the plane or pick other seeds"
            )
            running = volume_before
            for label, mirrored in made:
                step = mirrored.Shape
                change = float(step.Volume) - running
                running = float(step.Volume)
                if len(step.Solids) > max(solids_before, 1):
                    raise unrecoverable(
                        geometry_failed(
                            f"mirror left a separate solid ({SEPARATE_SOLID}; the reflected {label} is at fault): "
                            f"{hint}",
                            op,
                        )
                    )
                if label == "boss" and change <= 1e-9:
                    raise unrecoverable(
                        geometry_failed(f"mirror added no material (a reflected boss must touch the body): {hint}", op)
                    )
                if label == "cut" and change >= -1e-9:
                    raise unrecoverable(geometry_failed(f"mirror removed no material: {hint}", op))
            if len(shape.Solids) > max(solids_before, 1):
                raise unrecoverable(geometry_failed(f"mirror left a separate solid ({SEPARATE_SOLID}): {hint}", op))

        ctx.post_check = post_check

    return oc.run_op(doc, "mirror", params, build)


# --- chamfer / fillet -----------------------------------------------------------------------------------------


def _edges_of_one_body(doc: reg.DocumentRec, params: dict[str, Any]) -> tuple[list[reg.EdgeRec], reg.BodyRec]:
    op = _op(params)
    recs = []
    for e in params.get("edges", ()):
        rec, comp = reg.resolve_edge(doc, reg.handle_id(e, op, "edges"), op)
        if comp is not None:
            raise not_supported("dress-up on a component edge is not supported", op)
        recs.append(rec)
    if not recs:
        raise invalid_request("at least one edge is required", op)
    bodies = {r.body for r in recs}
    if len(bodies) != 1:
        raise invalid_request("edges must belong to one body", op)
    body = reg.resolve_body(doc, next(iter(bodies)), op)
    return recs, body


def _edge_text(doc: reg.DocumentRec, rec: reg.EdgeRec) -> str:
    """``edge:12 (line, length 19 mm, midpoint (10, 0, 9.5))`` for an error message."""
    try:
        d = edge_descriptor(doc, rec, None)
    except WorkerError:
        return rec.id
    if d.get("start") and d.get("end"):
        point = [(s + e) / 2.0 for s, e in zip(d["start"], d["end"], strict=True)]
    else:
        point = d.get("center")
    where = f", at ({', '.join(f'{c:.4g}' for c in point)})" if point else ""
    return f"{rec.id} ({d['curve']}, length {d['length_mm']:.4g} mm{where})"


def _settle_edges(
    doc: reg.DocumentRec,
    obj: Any,
    base: Any,
    edges: list[reg.EdgeRec],
    names: list[str],
    *,
    skip_failing: bool,
    what: str,
    op: str | None,
) -> list[reg.EdgeRec]:
    """Make a chamfer or fillet ``obj`` (its Base already set to ``base`` + every ``names``) recompute, or say which
    edges it cannot take. FreeCAD fails the whole feature and names only the feature, so the edges are tried in
    order on the SAME object (its Base changes, nothing is added to the body): each one is kept when the feature
    still recomputes with it and set aside when it does not. Cheap in the usual case: one recompute, and none at all
    for a single edge. With ``skip_failing`` the kept edges are applied and the ones set aside are returned; without
    it the failure names them. Raises when no edge is left."""
    if len(edges) < 2:
        return []
    fc.recompute(doc.fc_doc)
    if oc.feature_state_ok(obj):
        return []
    status = oc.status_of(obj)
    kept: list[int] = []
    set_aside: list[int] = []
    for index in range(len(edges)):
        fc.set_prop(obj, "Base", (base, [names[i] for i in [*kept, index]]))
        fc.recompute(doc.fc_doc)
        (kept if oc.feature_state_ok(obj) else set_aside).append(index)
    failing = [edges[i] for i in set_aside]
    if not kept or not set_aside or not skip_failing:
        listing = "; ".join(_edge_text(doc, e) for e in failing) or "none isolated"
        if not kept:
            head = f"{what} fails on every one of the {len(edges)} edges ({status}): {listing}"
        elif set_aside:
            head = (
                f"{what} cannot take {len(failing)} of the {len(edges)} edges ({status}): {listing}. The other "
                f"{len(kept)} work; pass skip_failing=true to apply them and report these, or exclude or change them"
            )
        else:  # every edge alone is fine and the whole set was not: the failure is in the combination
            head = f"{what} fails on the set of {len(edges)} edges taken together ({status}) although each works alone"
        raise geometry_failed(head, op, failing_edges=",".join(e.id for e in failing))
    fc.set_prop(obj, "Base", (base, [names[i] for i in kept]))
    fc.recompute(doc.fc_doc)
    if not oc.feature_state_ok(obj):
        raise geometry_failed(f"{what}: {oc.status_of(obj)}", op)
    return failing


def _extent_along(face: Any, direction: g.Vec3) -> float:
    box = g.exact_box(face)
    corners = [(x, y, z) for x in (box.XMin, box.XMax) for y in (box.YMin, box.YMax) for z in (box.ZMin, box.ZMax)]
    values = [g.dot(c, direction) for c in corners]
    return max(values) - min(values)


def chamfer(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    op = _op(params)
    d1 = float(params["distance1_mm"])
    d2 = params.get("distance2_mm")
    face1 = params.get("distance1_face")
    if d2 is not None and face1 is None:
        raise invalid_request("distance1_face is required when distance2_mm is set", op)
    if d1 <= 0 or (d2 is not None and float(d2) <= 0):
        raise invalid_request("chamfer distances must be positive", op)
    edges, body = _edges_of_one_body(doc, params)
    face_rec: reg.FaceRec | None = None
    if face1 is not None:
        face_rec, comp = reg.resolve_face(doc, reg.handle_id(face1, op, "distance1_face"), op)
        for e in edges:
            if face_rec.id not in e.faces:
                raise invalid_request(f"distance1_face {face_rec.id} is not adjacent to edge {e.id}", op)
    before = reg.topo_shape(doc, body)
    face1_normal: g.Vec3 | None = None
    if face_rec is not None:
        face1_normal = tuple(face_descriptor(doc, face_rec, None)["normal"] or (0.0, 0.0, 0.0))

    def flip_for(edge: reg.EdgeRec) -> bool:
        if face_rec is None or d2 is None:
            return False
        first = before.ancestorsOfType(edge.shape, Part.Face)[0]
        return not first.isSame(face_rec.shape)

    groups: dict[bool, list[reg.EdgeRec]] = {}
    for e in edges:
        groups.setdefault(flip_for(e), []).append(e)

    skipped: list[reg.EdgeRec] = []
    skip_failing = bool(params.get("skip_failing"))

    def build(ctx: oc.OpContext) -> None:
        fc_body = oc.ensure_partdesign(doc, body, ctx.feature)
        base = fc_body.Tip
        chamfers: list[Any] = []
        for index, (flip, group) in enumerate(groups.items(), start=1):
            names = [e.sub_name for e in group]
            if chamfers:  # second feature: re-find the edges in the first chamfer's shape
                fc.recompute(doc.fc_doc)
                if not oc.feature_state_ok(chamfers[-1]):
                    raise geometry_failed(oc.status_of(chamfers[-1]), op)
                shape = chamfers[-1].Shape
                names = [_find_edge_name(shape, e) for e in group]
            # two distances on edges whose first face differs need one Chamfer per orientation: numbered then
            ch_label = ctx.label() if len(groups) == 1 else ctx.label(str(index))
            ch = fc.new_in_body(fc_body, "PartDesign::Chamfer", "Chamfer", label=ch_label)
            fc.set_prop(ch, "Base", (base, names))
            if d2 is not None:
                fc.set_prop(ch, "ChamferType", "Two distances")
                fc.set_prop(ch, "Size", d1)
                fc.set_prop(ch, "Size2", float(d2))
            else:
                fc.set_prop(ch, "ChamferType", "Equal distance")
                fc.set_prop(ch, "Size", d1)
            fc.set_prop(ch, "FlipDirection", flip)
            ctx.feature.fc_names.append(ch.Name)
            ctx.check_objects.append(ch)
            skipped.extend(_settle_edges(doc, ch, base, group, names, skip_failing=skip_failing, what="chamfer", op=op))
            chamfers.append(ch)
            base = ch
        fc.set_prop(fc_body, "Tip", chamfers[-1])
        ctx.feature.fc_main = chamfers[-1].Name
        ctx.adopt(chamfers[-1])  # a no-op when the op made several (``<label>_1``, ``<label>_2``)
        ctx.bodies.append(body)

        def post_check() -> None:
            if d2 is None or face1_normal is None:
                return
            local_normal = oc.dir_to_body_frame(fc_body, face1_normal)  # the chamfer faces are body-local
            for ch in chamfers:
                if not _chamfer_extent_ok(ch, local_normal, float(d2), d1, op):
                    fc.set_prop(ch, "FlipDirection", not ch.FlipDirection)
                    fc.recompute(doc.fc_doc)
                    if not oc.feature_state_ok(ch):
                        raise geometry_failed(oc.status_of(ch), op)
                    if not _chamfer_extent_ok(ch, local_normal, float(d2), d1, op):
                        raise geometry_failed("chamfer distances could not be oriented on distance1_face", op)

        ctx.post_check = post_check

    return _with_skipped(oc.run_op(doc, "chamfer", params, build), doc, skipped, "chamfer")


def _with_skipped(
    result: dict[str, Any], doc: reg.DocumentRec, skipped: list[reg.EdgeRec], what: str
) -> dict[str, Any]:
    """Report the edges a ``skip_failing`` chamfer or fillet left out (their ids as they were in the request)."""
    if skipped:
        ids = [e.id for e in skipped]
        warning = f"{what} skipped {len(ids)} edge(s) it could not take: {', '.join(ids)}"
        doc.features[-1].warnings.append(warning)
        result["skipped_edges"] = oc.handles(ids)
        result["warnings"] = [*result["warnings"], warning]
    return result


def _extent_matches(extent: float, target: float) -> bool:
    """Absolute-or-relative 1e-6 comparison."""
    return abs(extent - target) <= 1e-6 * max(1.0, abs(target))


def _chamfer_extent_ok(ch: Any, n1: g.Vec3, d2: float, d1: float, op: str | None) -> bool:
    """Every new planar face created by the chamfer must extend ``d2`` along the normal of
    ``distance1_face``. Returns ``False`` when a face's extent matches ``d1`` instead (the two
    distances got swapped: one flip + recompute corrects it). Raises ``geometry_failed`` when a
    face's extent matches neither distance -- silently returning success would report a chamfer
    with the wrong geometry.
    """
    base_faces = ch.Base[0].Shape.Faces
    needs_flip = False
    for f in ch.Shape.Faces:
        if g.surface_kind(f) != "plane" or any(f.isPartner(b) for b in base_faces):
            continue
        n = g.unit(g.vec(f.normalAt(0, 0)))
        if abs(g.dot(n, n1)) > 1 - 1e-6 or abs(g.dot(n, n1)) < 1e-6:
            continue  # not a chamfer face adjacent to distance1_face
        extent = _extent_along(f, n1)
        if _extent_matches(extent, d2):
            continue
        if _extent_matches(extent, d1):
            needs_flip = True
            continue
        raise geometry_failed(
            "chamfer face extent matches neither distance1_mm nor distance2_mm",
            op,
            extent=extent,
            d1=d1,
            d2=d2,
        )
    return not needs_flip


def _find_edge_name(shape: Any, rec: reg.EdgeRec) -> str:
    import provenance as prov  # noqa: PLC0415

    for i, e in enumerate(shape.Edges):
        if e.isPartner(rec.shape):
            return f"Edge{i + 1}"
    for i, e in enumerate(shape.Edges):
        if prov.edge_sig_match(rec.sig, prov.edge_sig(e)):
            return f"Edge{i + 1}"
    raise geometry_failed(f"edge {rec.id} disappeared after the first chamfer", None)


def fillet(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    op = _op(params)
    radius = float(params["radius_mm"])
    if radius <= 0:
        raise invalid_request("fillet radius_mm must be positive", op)
    edges, body = _edges_of_one_body(doc, params)
    names = [e.sub_name for e in edges]
    edge_dir: g.Vec3 | None = None
    if len(edges) == 1 and edges[0].sig.curve == "line":
        edge_dir = g.unit(g.sub(g.vec(edges[0].shape.lastVertex().Point), g.vec(edges[0].shape.firstVertex().Point)))
    skipped: list[reg.EdgeRec] = []
    skip_failing = bool(params.get("skip_failing"))

    def build(ctx: oc.OpContext) -> None:
        fc_body = oc.ensure_partdesign(doc, body, ctx.feature)
        base = fc_body.Tip  # newObject moves Tip to the new solid feature: capture the base first
        fi = fc.new_in_body(fc_body, "PartDesign::Fillet", "Fillet", label=ctx.label())
        fc.set_prop(fi, "Base", (base, names))
        fc.set_prop(fi, "Radius", radius)
        skipped.extend(_settle_edges(doc, fi, base, edges, names, skip_failing=skip_failing, what="fillet", op=op))
        ctx.axis_hint = edge_dir
        _finish_feature(ctx, fc_body, fi, body)

    # FreeCAD merges the faces of tangent or meeting fillets, so the number of faces made says nothing about the edges
    return _with_skipped(oc.run_op(doc, "fillet", params, build), doc, skipped, "fillet")


# --- shell --------------------------------------------------------------------------------------------------------


def _faces_of_one_body(
    doc: reg.DocumentRec, params: dict[str, Any], verb: str = "shell", purpose: str = "remove"
) -> tuple[list[reg.FaceRec], reg.BodyRec]:
    op = _op(params)
    recs: list[reg.FaceRec] = []
    for f in params.get("faces", ()):
        rec, comp = reg.resolve_face(doc, reg.handle_id(f, op, "faces"), op)
        if comp is not None:
            raise not_supported(f"{verb} on a component face is not supported", op)
        if any(rec.id == other.id for other in recs):
            raise invalid_request(f"{verb} lists face {rec.id} twice", op)
        recs.append(rec)
    if not recs:
        raise invalid_request(f"{verb} needs at least one face to {purpose}", op)
    bodies = {r.body for r in recs}
    if len(bodies) != 1:
        raise invalid_request(f"{verb} faces belong to different bodies", op)
    return recs, reg.resolve_body(doc, next(iter(bodies)), op)


def shell(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """Hollow one body through ``PartDesign::Thickness`` (Mode Skin): the named faces are removed and the walls are the
    only material left. ``Reversed=True`` is INWARD (the walls grow into the body, the outer size is kept), the default
    ``Reversed=False`` is OUTWARD (the old body becomes the cavity and the walls grow outside it).

    Thickness fails silently: an empty face list, or a wall that does not fit, leaves the shape unchanged (or invalid)
    with the feature "Up-to-date". The post-check turns both into a non-recoverable ``geometry_failed`` (the kernel is
    deterministic: asking again fails the same way). A shell is a no-op when the volume did not change AND no face was
    added; a legitimate shell can keep its face count (five of six box faces removed leaves a slab of six faces), so
    the count alone proves nothing."""
    op = _op(params)
    thickness = float(params["thickness_mm"])
    if thickness <= 0:
        raise invalid_request("shell thickness_mm must be positive", op)
    outward = bool(params.get("outward", False))
    join = str(params.get("join", "intersection"))
    if join not in ("intersection", "arc"):
        raise invalid_request(f"shell join must be 'intersection' or 'arc', not {join!r}", op)
    faces, body = _faces_of_one_body(doc, params)
    names = [f.sub_name for f in faces]
    before = reg.topo_shape(doc, body)
    volume_before = float(before.Volume)
    faces_before = len(before.Faces)

    def build(ctx: oc.OpContext) -> None:
        fc_body = oc.ensure_partdesign(doc, body, ctx.feature)
        base = fc_body.Tip  # newObject moves Tip to the new solid feature: capture the base first
        th = fc.new_in_body(fc_body, "PartDesign::Thickness", "Thickness", label=ctx.label())
        fc.set_prop(th, "Base", (base, names))
        fc.set_prop(th, "Value", thickness)
        fc.set_prop(th, "Reversed", not outward)
        fc.set_prop(th, "Mode", "Skin")
        fc.set_prop(th, "Join", "Arc" if join == "arc" else "Intersection")
        _finish_feature(ctx, fc_body, th, body)

        def post_check() -> None:
            body.invalidate()
            shape = reg.topo_shape(doc, body)
            hint = (
                "the kernel could not build this shell (a thinner wall, fewer or other removed faces, or a "
                "different join may help)"
            )
            if shape.isNull() or not shape.isValid():
                raise unrecoverable(geometry_failed(f"shell of {thickness:g} mm produced an invalid shape: {hint}", op))
            change = float(shape.Volume) - volume_before
            if abs(change) <= 1e-6 and len(shape.Faces) <= faces_before:
                raise unrecoverable(geometry_failed(f"shell of {thickness:g} mm changed nothing: {hint}", op))
            if not outward and change >= -1e-6:
                raise unrecoverable(geometry_failed(f"shell of {thickness:g} mm removed no material: {hint}", op))

        ctx.post_check = post_check

    return oc.run_op(doc, "shell", params, build)


# --- draft --------------------------------------------------------------------------------------------------------

_DRAFT_ALONG_PULL = math.sin(math.radians(1.0))
"""A planar face whose normal is this close to perpendicular to the pull lies ALONG it (an ordinary mould wall)."""
_FACING_PULL = math.cos(math.radians(1.0))
"""A planar face whose normal is this close to parallel to the pull FACES it (it lies in, or parallel to, the neutral
plane): the kernel cannot draft it and, depending on the version, either fails or silently leaves it alone."""
_DRAFT_LEAN_TOL_DEG = 0.1
_DRAFT_POSITION_TOL = 1e-3


class _StderrTap:
    """Capture what the kernel writes to file descriptor 2 while it is open (PartDesign::Draft logs ``Adding face failed
    on Face6. Omitted`` there on 1.0.2 and still reports success). Headless only: the GUI binary sends its log to the
    Report view, where nothing is captured and the per-face check below is the guard. The text is replayed to the real
    stderr on exit, so no log is lost."""

    def __init__(self) -> None:
        self._saved: int | None = None
        self._path = ""

    def __enter__(self) -> "_StderrTap":
        import os
        import sys
        import tempfile

        try:
            sys.stderr.flush()
            handle, self._path = tempfile.mkstemp(prefix="cotaforge-stderr-")
            self._saved = os.dup(2)
            os.dup2(handle, 2)
            os.close(handle)
        except OSError:
            self._saved = None
        return self

    def text(self) -> str:
        if self._saved is None:
            return ""
        try:
            with open(self._path, encoding="utf-8", errors="replace") as log:
                return log.read()
        except OSError:
            return ""

    def __exit__(self, *exc: object) -> None:
        import os

        if self._saved is None:
            return
        captured = self.text()
        os.dup2(self._saved, 2)
        os.close(self._saved)
        try:
            os.write(2, captured.encode("utf-8", errors="replace"))
            os.remove(self._path)
        except OSError:
            pass


def _outward_normal(face: Any) -> g.Vec3:
    """The face's outward normal (``normalAt`` follows the solid's orientation) at its centre of mass."""
    u, v = face.Surface.parameter(face.CenterOfMass)
    return g.unit(g.vec(face.normalAt(u, v)))


def _probe_point(face: Any) -> g.Vec3:
    """A point ON the face near its centre of mass (the centre itself when the face contains it, the nearest point of
    the face otherwise: an L-shaped face's centre of mass lies in the void)."""
    _, pairs, _ = face.distToShape(Part.Vertex(face.CenterOfMass))
    return g.vec(pairs[0][0])


def _face_signature(shape: Any) -> frozenset[tuple[Any, ...]]:
    """Kind, centre of mass and area of every face, rounded: two shapes with the same signature have the same faces."""
    return frozenset(
        (g.surface_kind(f), *(round(c, 5) for c in g.vec(f.CenterOfMass)), round(float(f.Area), 4)) for f in shape.Faces
    )


def _lean_deg(normal: g.Vec3, pull: g.Vec3) -> float:
    return math.degrees(math.asin(max(-1.0, min(1.0, g.dot(normal, pull)))))


def _plane_pivot(n0: g.Vec3, point: g.Vec3, normal: g.Vec3, origin: g.Vec3) -> tuple[g.Vec3, g.Vec3] | None:
    """A point and the direction of the line where the face's own plane (``n0``, through ``point``) meets the neutral
    plane (``normal``, through ``origin``): the line a drafted face turns about. ``None`` for parallel planes."""
    d = g.cross(n0, normal)
    dd = g.dot(d, d)
    if dd < 1e-12:
        return None
    a, b = g.dot(n0, point), g.dot(normal, origin)
    x = g.add(g.scale(g.cross(normal, d), a), g.scale(g.cross(d, n0), b))
    return g.scale(x, 1.0 / dd), g.unit(d)


def _draft_subjects(
    recs: list[reg.FaceRec], pull: g.Vec3, normal: g.Vec3, origin: g.Vec3
) -> tuple[list[dict[str, Any]], int]:
    """What each requested face looks like BEFORE the draft (body frame), for the faces whose result is judged: a plane
    lying along the pull (remembering where it meets the neutral plane) and a cylinder coaxial with the pull (it becomes
    a cone through the circle it has on the neutral plane). Also the number of requested faces that are NOT judged
    (tilted planes, other surfaces): built, but their lean is not checked."""
    subjects: list[dict[str, Any]] = []
    unjudged = 0
    for rec in recs:
        face = rec.shape
        kind = g.surface_kind(face)
        if kind == "plane":
            n0 = _outward_normal(face)
            pivot = _plane_pivot(n0, g.vec(face.CenterOfMass), normal, origin)
            if abs(g.dot(n0, pull)) > _DRAFT_ALONG_PULL or pivot is None:
                unjudged += 1
                continue
            subjects.append({"kind": "plane", "probe": _probe_point(face), "pivot": pivot[0], "pivot_dir": pivot[1]})
        elif kind == "cylinder" and abs(g.dot(g.unit(g.vec(face.Surface.Axis)), pull)) >= _FACING_PULL:
            axis = g.unit(g.vec(face.Surface.Axis))
            centre = g.vec(face.Surface.Center)
            along = g.dot(g.sub(origin, centre), normal) / g.dot(axis, normal)
            on_neutral = g.add(centre, g.scale(axis, along))
            side = g.unit(g.cross(axis, (1.0, 0.0, 0.0) if abs(axis[0]) < 0.9 else (0.0, 1.0, 0.0)))
            subjects.append(
                {
                    "kind": "cone",
                    "probe": _probe_point(face),
                    "axis": axis,
                    "centre": centre,
                    "pivot": g.add(on_neutral, g.scale(side, float(face.Surface.Radius))),
                }
            )
        else:
            unjudged += 1
    return subjects, unjudged


def _is_successor(face: Any, subject: dict[str, Any], pull: g.Vec3, angle: float) -> float | None:
    """The lean (degrees) of ``face`` when it is the successor of the subject, else ``None``. A successor is of the
    subject's kind, has the requested angle in ABSOLUTE terms (FreeCAD drafts to ``angle`` from the pull, not by
    ``angle`` more), still passes through where the old surface met the neutral plane, and CONTAINS the old probe
    point carried onto it: so a coplanar twin or another coaxial cone cannot stand in for it."""
    kind = g.surface_kind(face)
    surface = face.Surface
    if subject["kind"] == "plane":
        if kind != "plane":
            return None
        normal = _outward_normal(face)
        lean = _lean_deg(normal, pull)
        if abs(abs(lean) - angle) > _DRAFT_LEAN_TOL_DEG:
            return None
        if abs(g.dot(normal, subject["pivot_dir"])) > _DRAFT_POSITION_TOL:
            return None
        if abs(g.dot(g.sub(subject["pivot"], g.vec(face.CenterOfMass)), normal)) > _DRAFT_POSITION_TOL:
            return None
    else:
        if kind != "cone" or abs(math.degrees(abs(float(surface.SemiAngle))) - angle) > _DRAFT_LEAN_TOL_DEG:
            return None
        axis = g.unit(g.vec(surface.Axis))
        if abs(g.dot(axis, subject["axis"])) < 1.0 - 1e-6:
            return None
        if g.length(g.perpendicular_part(g.sub(g.vec(surface.Center), subject["centre"]), subject["axis"])) > 1e-3:
            return None
        pivot = Vector(*subject["pivot"])
        if surface.value(*surface.parameter(pivot)).distanceToPoint(pivot) > _DRAFT_POSITION_TOL:
            return None
        lean = _lean_deg(_outward_normal(face), pull)
    probe = Vector(*subject["probe"])
    carried = surface.value(*surface.parameter(probe))
    if face.distToShape(Part.Vertex(carried))[0] > _DRAFT_POSITION_TOL:
        return None
    return lean


def _judge_draft(shape: Any, subjects: list[dict[str, Any]], pull: g.Vec3, angle: float) -> str:
    """``"ok"`` when every judged face has its OWN successor (no face serves two) leaning +``angle`` towards the pull
    (its outward normal has component sin(angle) along it), ``"wrong"`` when a successor leans the other way (flip
    ``Reversed``), ``"missing"`` when some face has no successor at all (it was not drafted)."""
    taken: set[int] = set()
    verdict = "ok"
    for subject in subjects:
        found: tuple[int, float] | None = None
        for index, face in enumerate(shape.Faces):
            if index in taken:
                continue
            lean = _is_successor(face, subject, pull, angle)
            if lean is not None:
                found = (index, lean)
                break
        if found is None:
            return "missing"
        taken.add(found[0])
        if abs(found[1] - angle) > _DRAFT_LEAN_TOL_DEG:
            verdict = "wrong"
    return verdict


def _refuse_faces_facing_the_pull(doc: reg.DocumentRec, recs: list[reg.FaceRec], pull: g.Vec3, op: str) -> None:
    """A planar face parallel to the neutral plane (it faces the pull, or lies in the plane itself) cannot be drafted:
    1.1.3 fails the whole feature and 1.0.2 silently leaves it alone while drafting the others: refused here."""
    for rec in recs:
        normal = face_descriptor(doc, rec, None)["normal"]
        if normal is not None and abs(g.dot(g.unit(g.vec(normal)), pull)) >= _FACING_PULL:
            raise invalid_request(
                f"draft face {rec.id} faces the pull (its normal is within 1 deg of it): a face facing the pull, or "
                "lying in the neutral plane, cannot be drafted; name the walls that run along the pull instead",
                op,
            )


def draft(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """Taper faces through ``PartDesign::Draft`` about a ``PartDesign::Plane`` datum at the neutral frame.

    ``PullDirection`` is NEVER set (an Origin axis is rejected on 1.0.2 and fails on 1.1.3): the pull is the neutral
    plane's normal, so the plane's pose carries it. ``Reversed=False`` makes the drafted faces' outward normals lean
    towards that normal (measured on 1.0.2 and 1.1.3), so ``Reversed`` is set when the requested pull points the other
    way. The draft is ABSOLUTE (a face already leaning 0.5 deg and drafted 3 deg leans 3), and 45 degrees and more give
    an invalid shape on a box (the IR stops at 30).

    The sense is then CHECKED on the real result, face by face: every requested wall that lies along the pull (and every
    cylinder coaxial with it) must have its own successor leaning ``angle_deg`` towards the pull. Wrong leans flip
    ``Reversed`` once; a face without a successor, a shape that still leans wrongly, an invalid shape, a face the
    kernel logged as "Omitted" (1.0.2 skips what it cannot draft and still reports success) and, when some face is not
    judged, a shape whose faces did not change are non-recoverable ``geometry_failed`` with nothing left behind."""
    op = _op(params)
    angle = float(params["angle_deg"])
    if not 0.0 < angle <= 30.0:
        raise invalid_request(f"draft angle_deg must be above 0 and at most 30, not {angle:g}", op)
    try:
        neutral = g.realise_frame(params["neutral"])
        pull = g.unit(g.vec(params["pull"]))
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise invalid_request(f"draft: invalid neutral frame or pull ({exc})", op) from exc
    normal = g.vec(neutral["n"])
    if abs(g.dot(pull, normal)) < math.cos(math.radians(1.5)):
        raise invalid_request("draft pull must be perpendicular to the neutral plane", op)
    recs, body = _faces_of_one_body(doc, params, "draft", "draft")
    _refuse_faces_facing_the_pull(doc, recs, pull, op)
    tap = _StderrTap()

    def build(ctx: oc.OpContext) -> None:
        fc_body = oc.ensure_partdesign(doc, body, ctx.feature)
        base = fc_body.Tip  # newObject moves Tip to the new solid feature: capture the base first
        names = [rec.sub_name for rec in recs]  # after ensure_partdesign: a Part body is re-keyed by index
        pose = oc.to_body_frame(fc_body, g.frame_placement(neutral))
        pull_local = oc.dir_to_body_frame(fc_body, pull)
        plane_normal = g.vec(pose.Rotation.multVec(Vector(0, 0, 1)))
        subjects, unjudged = _draft_subjects(recs, pull_local, plane_normal, g.vec(pose.Base))
        signature_before = _face_signature(reg.topo_shape(doc, body))
        plane = fc.new_in_body(fc_body, "PartDesign::Plane", "NeutralPlane", label=ctx.label("neutral_plane"))
        fc.set_prop(plane, "MapMode", "Deactivated")
        # PartDesign::Draft reads the plane in the frame of the feature it drafts (measured on 1.0.2 and 1.1.3: a body
        # whose Pad sits on a rotated sketch plane, e.g. XZ, leans about the rotated pull), so the pose given to the
        # datum is expressed in that frame. The datum is a hidden helper: where it shows is not the neutral plane.
        fc.set_prop(plane, "Placement", base.Placement.inverse().multiply(pose))
        _hide(plane)
        ctx.feature.fc_names.append(plane.Name)
        dr = fc.new_in_body(fc_body, "PartDesign::Draft", "Draft", label=ctx.label())
        fc.set_prop(dr, "Base", (base, names))
        fc.set_prop(dr, "Angle", angle)
        fc.set_prop(dr, "NeutralPlane", (plane, [""]))
        fc.set_prop(dr, "Reversed", g.dot(pull_local, plane_normal) < 0)
        _finish_feature(ctx, fc_body, dr, body)
        hint = (
            "the faces must lie along the pull and the angle must not collapse a wall: use a smaller angle or move "
            "the neutral plane"
        )

        def current() -> Any:
            body.invalidate()
            return reg.topo_shape(doc, body)

        def refuse(message: str) -> WorkerError:
            return unrecoverable(geometry_failed(f"draft of {angle:g} deg {message}: {hint}", op))

        def post_check() -> None:
            shape = current()
            if shape.isNull() or not shape.isValid():
                raise refuse("produced an invalid shape")
            verdict = _judge_draft(shape, subjects, pull_local, angle) if subjects else "ok"
            if verdict == "wrong":
                fc.set_prop(dr, "Reversed", not bool(dr.Reversed))
                fc.recompute(doc.fc_doc)
                shape = current()
                if shape.isNull() or not shape.isValid():
                    raise refuse("produced an invalid shape")
                verdict = _judge_draft(shape, subjects, pull_local, angle)
            if verdict != "ok":
                what = "leans the faces the other way" if verdict == "wrong" else "left a requested face undrafted"
                raise refuse(what)
            if "Omitted" in tap.text():
                raise refuse("had a requested face omitted by the kernel (it cannot draft it)")
            if (unjudged or not subjects) and _face_signature(shape) == signature_before:
                raise refuse("changed nothing")
            if unjudged:
                ctx.feature.warnings.append(
                    f"{unjudged} requested face(s) tilted against the pull or not planar were drafted but not "
                    "checked: only faces lying along the pull (and cylinders coaxial with it) are verified"
                )

        ctx.post_check = post_check

    try:
        with tap:
            return oc.run_op(doc, "draft", params, build)
    except WorkerError as exc:
        if exc.code != "geometry_failed" or exc.message.rstrip().rpartition(":")[2].strip():
            raise
        # the kernel failed the feature and said nothing ("Draft: "): name the usual causes
        raise unrecoverable(
            geometry_failed(
                f"draft of {angle:g} deg failed in the kernel: only faces that lie along the pull can be drafted "
                "(not the face the pull runs into), and the angle must not collapse a wall; use a smaller angle or "
                "move the neutral plane",
                op,
            )
        ) from exc
