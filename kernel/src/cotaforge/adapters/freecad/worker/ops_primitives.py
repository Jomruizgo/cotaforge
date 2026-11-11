"""Body-level primitives: box, cylinder, boolean, move_body, copy_body on Part workbench objects."""

import math
from typing import Any

import fc
import geometry as g
import ops_common as oc
import Part
import provenance as prov
import registry as reg
from FreeCAD import Placement, Rotation, Vector
from protocol import geometry_failed, invalid_request, not_supported, unrecoverable
from recorder import literal


def _op(params: dict[str, Any]) -> str:
    return str(params.get("operation_id", ""))


def _axis_hints(doc: reg.DocumentRec, body: reg.BodyRec) -> list[tuple[prov.FaceSig, g.Vec3]]:
    """The intended axis senses of a body's cylinder and cone faces in WORLD terms, each with the signature of that
    face in the world shape: a PartDesign body keeps both its signatures and its senses body-local, so a moved one
    cannot be matched against a world shape without taking them into world terms first."""
    world = reg.world_shape(doc, body)
    pose = reg.body_placement(doc, body)
    hints: list[tuple[prov.FaceSig, g.Vec3]] = []
    for rec in body.faces.values():
        if rec.axis_dir is None or rec.stale or rec.index >= len(world.Faces):
            continue
        hints.append((prov.face_sig(world.Faces[rec.index]), g.rotate_direction(pose, rec.axis_dir)))
    return hints


def _inherit_axis_senses(body: reg.BodyRec, hints: list[tuple[prov.FaceSig, g.Vec3]]) -> None:
    """Give every cylinder/cone face of ``body`` without an intended sense the sense of the hinted face it continues
    (the same surface: a trimmed piece of a cylinder is still that cylinder)."""
    for rec in body.faces.values():
        if rec.stale or rec.axis_dir is not None or rec.sig.surface not in ("cylinder", "cone"):
            continue
        for sig, direction in hints:
            if prov.sig_match(sig, rec.sig):
                rec.axis_dir = direction
                break


def _new_part_body(ctx: oc.OpContext, obj: Any) -> reg.BodyRec:
    body = reg.BodyRec(ctx.doc.next_id("body"), "part", None, obj.Name, ctx.feature.id)
    ctx.doc.bodies[body.id] = body
    ctx.feature.created_bodies.append(body.id)
    ctx.feature.fc_names.append(obj.Name)
    ctx.feature.fc_main = obj.Name
    ctx.adopt(obj)  # a no-op for a split piece (``<label>_positive``)
    ctx.check_objects.append(obj)
    ctx.bodies.append(body)
    return body


def box(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    op = _op(params)
    size = [float(s) for s in params["size"]]
    if min(size) <= 0:
        raise invalid_request("box size must be positive", op)
    transform = params.get("transform") or {}

    def build(ctx: oc.OpContext) -> None:
        obj = fc.add_object(doc.fc_doc, "Part::Box", "Box", label=ctx.label())
        fc.set_prop(obj, "Length", size[0])
        fc.set_prop(obj, "Width", size[1])
        fc.set_prop(obj, "Height", size[2])
        fc.set_prop(obj, "Placement", g.placement_from_transform(transform))
        _new_part_body(ctx, obj)

    return oc.run_op(doc, "box", params, build)


def cylinder(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    op = _op(params)
    diameter = float(params["diameter_mm"])
    height = float(params["height_mm"])
    if diameter <= 0 or height <= 0:
        raise invalid_request("cylinder dimensions must be positive", op)
    axis = params["axis"]
    origin = g.vec(axis["origin"])
    direction = g.unit(g.vec(axis["direction"]))

    def build(ctx: oc.OpContext) -> None:
        obj = fc.add_object(doc.fc_doc, "Part::Cylinder", "Cylinder", label=ctx.label())
        fc.set_prop(obj, "Radius", diameter / 2.0)
        fc.set_prop(obj, "Height", height)
        fc.set_prop(obj, "Placement", Placement(g.fcvec(origin), Rotation(Vector(0, 0, 1), g.fcvec(direction))))
        ctx.axis_hint = direction
        _new_part_body(ctx, obj)

    return oc.run_op(doc, "cylinder", params, build)


def boolean(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    op = _op(params)
    kind = str(params["kind"])
    keep_tools = bool(params.get("keep_tools", False))
    target = reg.resolve_body(doc, reg.handle_id(params["target"], op, "target"), op)
    tools = [reg.resolve_body(doc, reg.handle_id(t, op, "tools"), op) for t in params.get("tools", ())]
    if not tools:
        raise invalid_request("boolean requires at least one tool body", op)
    ids = [target.id] + [t.id for t in tools]
    if len(set(ids)) != len(ids):
        raise invalid_request("boolean bodies must be distinct", op)
    if target.kind != "part":
        raise not_supported("a boolean works on a Part body as its target: copy_body the PartDesign body first", op)
    if kind not in ("union", "subtract", "intersect"):
        raise invalid_request(f"unknown boolean kind {kind}", op)
    if keep_tools and kind != "subtract":
        raise invalid_request("keep_tools is only supported by boolean_subtract", op)
    if any(t.kind != "part" for t in tools) and not keep_tools:
        # a consumed PartDesign tool would have to be deleted with its whole feature tree; a kept one is only read
        raise not_supported(
            "a PartDesign body can be a boolean tool only with keep_tools (it survives, only a copy of its shape is "
            "cut); otherwise copy_body it first",
            op,
        )
    tool_axis_hints: list[tuple[prov.FaceSig, g.Vec3]] = []
    for t in tools:
        if t.kind == "part":
            tool_axis_hints.extend(
                (rec.sig, rec.axis_dir) for rec in t.faces.values() if not rec.stale and rec.axis_dir
            )
        else:
            tool_axis_hints.extend(_axis_hints(doc, t))

    def build(ctx: oc.OpContext) -> None:
        target_obj = doc.fc_doc.getObject(target.result_obj)
        tool_objs = [doc.fc_doc.getObject(t.result_obj) for t in tools]
        if kind == "union":
            result = fc.add_object(doc.fc_doc, "Part::MultiFuse", "Fusion", label=ctx.label())
            fc.set_prop(result, "Shapes", [target_obj, *tool_objs])
        elif kind == "intersect":
            result = fc.add_object(doc.fc_doc, "Part::MultiCommon", "Common", label=ctx.label())
            fc.set_prop(result, "Shapes", [target_obj, *tool_objs])
        else:
            if keep_tools:
                # Fish-mouth coping (keep_tools): Part::Cut always consumes its Tool (hidden, owned by
                # the Cut feature) -- so the ORIGINAL tool object must never become that Tool. A plain
                # Part::Feature copy of each tool's CURRENT shape is cut instead; the originals are never
                # touched below, so they stay live, visible, top-level bodies with their own faces/edges
                # untouched (same identity as before this op -- a later op can still reference them by
                # the op that created them).
                copies: list[Any] = []
                for number, (t, t_obj) in enumerate(zip(tools, tool_objs, strict=True), start=1):
                    copy_label = ctx.label("tool" if len(tools) == 1 else f"tool_{number}")
                    c = fc.add_object(doc.fc_doc, "Part::Feature", "ToolCopy", label=copy_label)
                    if t.kind == "partdesign":
                        # the tool is a featured body: copy its tip shape placed in the document (a Body's own
                        # Shape is in body terms), so a moved or rotated tool is cut where it stands
                        shape = reg.world_shape(doc, t).copy()
                        fc.set_prop(c, "Shape", shape, source=f"<{t_obj.Name}>.Tip.Shape.copy() placed in the document")
                    else:
                        fc.set_prop(c, "Shape", t_obj.Shape.copy(), source=f"<{t_obj.Name}>.Shape.copy()")
                    ctx.feature.fc_names.append(c.Name)
                    ctx.check_objects.append(c)
                    copies.append(c)
                tool_objs = copies
            tool: Any = tool_objs[0]
            if len(tool_objs) > 1:
                tool = fc.add_object(doc.fc_doc, "Part::MultiFuse", "Tools", label=ctx.label("tools"))
                fc.set_prop(tool, "Shapes", tool_objs)
                ctx.feature.fc_names.append(tool.Name)
                ctx.check_objects.append(tool)
            result = fc.add_object(doc.fc_doc, "Part::Cut", "Cut", label=ctx.label())
            fc.set_prop(result, "Base", target_obj)
            fc.set_prop(result, "Tool", tool)
        fc.set_prop(result, "Refine", False)
        ctx.feature.fc_names.append(result.Name)
        ctx.feature.fc_main = result.Name
        ctx.adopt(result)
        ctx.check_objects.append(result)
        target.result_obj = result.Name
        target.invalidate()
        for t in tools:
            if keep_tools:
                continue
            t.deleted = True
            for rec in t.faces.values():
                rec.stale = True
            for rec in t.edges.values():
                rec.stale = True
            ctx.feature.modified_bodies.append(t.id)
        ctx.bodies.append(target)

    result = oc.run_op(doc, "boolean", params, build)
    feature = doc.features[-1]
    # created faces that come from a tool inherit the tool's axis sense (bore of a subtracted cylinder)
    for fid in feature.created_faces:
        rec = target.faces.get(fid)
        if rec is None or rec.axis_dir is not None:
            continue
        for sig, axis_dir in tool_axis_hints:
            if prov.sig_match(sig, rec.sig):
                rec.axis_dir = axis_dir
                break
    return result


def move_body(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    op = _op(params)
    body = reg.resolve_body(doc, reg.handle_id(params["body"], op, "body"), op)
    placement = g.placement_from_transform(params.get("transform") or {})

    def build(ctx: oc.OpContext) -> None:
        name = body.fc_body if body.kind == "partdesign" else body.result_obj
        obj = doc.fc_doc.getObject(name)
        fc.set_prop(obj, "Placement", placement.multiply(obj.Placement))
        # creates no object: fc_main is the moved one and keeps the label of the op that made it (never relabelled)
        ctx.feature.fc_names.append(obj.Name)
        ctx.feature.fc_main = obj.Name
        ctx.check_objects.append(obj)
        ctx.by_index = True
        ctx.bodies.append(body)

    result = oc.run_op(doc, "move_body", params, build)
    if body.kind == "part":  # the placed shape moved: rotate stored axis senses with it
        for rec in body.faces.values():
            if rec.axis_dir is not None and not rec.stale:
                rec.axis_dir = g.rotate_direction(placement, rec.axis_dir)
    return result


def copy_body(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """Duplicate a body as a history-free ``Part::Feature`` carrying a copy of its placed shape, then place it.

    The copy is always a Part body (no sketches or features come with it): booleans and ``move_body`` work on it,
    and a later extrude/cut/hole/fillet upgrades it lazily (``ops_common.ensure_partdesign``).

    ``scale`` (default 1) first scales the copy uniformly about the world point ``scale_about`` with ``Shape.scale`` --
    exact in the kernel, volume s^3 and surface s^2; ``transformGeometry`` would turn a surface into a B-spline and miss
    by 1e-4 -- and then ``transform`` (rotate, offset) places it. The shape keeps its element order, so the intended
    axis senses are copied by face index as for a rigid copy."""
    op = _op(params)
    source = reg.resolve_body(doc, reg.handle_id(params["source"], op, "source"), op)
    placement = g.placement_from_transform(params.get("transform") or {})
    factor = float(params.get("scale", 1.0))
    if not math.isfinite(factor) or factor <= 0.0:
        raise invalid_request(f"copy_body scale must be a finite number > 0, got {factor!r}", op)
    about = g.vec(params.get("scale_about") or (0.0, 0.0, 0.0))
    # intended axis senses of the source's cylinder/cone faces, by face index (a shape copy keeps the element order).
    # A PartDesign body stores them body-local, a Part body in world terms: take them to world terms here.
    pose = reg.body_placement(doc, source)
    axis_dirs = [
        (rec.index, g.rotate_direction(pose, rec.axis_dir))
        for rec in source.faces.values()
        if rec.axis_dir is not None and not rec.stale
    ]

    def build(ctx: oc.OpContext) -> None:
        shape = reg.world_shape(doc, source)
        if shape.isNull():
            raise geometry_failed("cannot copy a body with no shape", op)
        source_obj = doc.fc_doc.getObject(source.fc_body if source.kind == "partdesign" else source.result_obj)
        obj = fc.add_object(doc.fc_doc, "Part::Feature", "Copy", label=ctx.label())
        if factor == 1.0:
            fc.set_prop(obj, "Shape", shape.copy(), source=f"{literal(source_obj)}.Shape.copy()")
        else:
            scaled = shape.copy().scale(factor, g.fcvec(about))
            if scaled.isNull() or not scaled.isValid() or len(scaled.Faces) != len(shape.Faces):
                raise geometry_failed("the scaled shape is invalid", op)
            fc.set_prop(
                obj,
                "Shape",
                scaled,
                source=f"{literal(source_obj)}.Shape.copy().scale({factor!r}, {literal(g.fcvec(about))})",
            )
        fc.set_prop(obj, "Placement", placement.multiply(obj.Placement))
        _new_part_body(ctx, obj)

    result = oc.run_op(doc, "copy_body", params, build)
    copy = doc.bodies[result["created_bodies"][0]["id"]]
    by_index = {rec.index: rec for rec in copy.faces.values() if not rec.stale}
    for index, direction in axis_dirs:
        rec = by_index.get(index)
        if rec is not None and rec.sig.surface in ("cylinder", "cone"):
            rec.axis_dir = g.rotate_direction(placement, direction)
    return result


def mirror_body(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """Reflect a body across a plane into a history-free ``Part::Feature`` (``Shape.mirror`` of its shape): a NEW Part
    body like ``copy_body``'s, the source left where it is. The reflection is exact in the kernel for any plane; the
    volume and surface are the source's. The source's intended axis senses (cylinder and cone faces) are reflected with
    the shape, matched by face index (a mirrored shape keeps the element order).

    ``Shape.mirror`` composes the shape's own placement into the reflection (on FreeCAD 1.0.2 a moved body came out
    placed twice), so the shape is reflected WITHOUT its placement, about the plane taken into the shape's own frame,
    and the placement is put back on the object: the result is the same world shape on every version."""
    op = _op(params)
    source = reg.resolve_body(doc, reg.handle_id(params["source"], op, "source"), op)
    _, origin, normal = oc.mirror_plane(params, op)
    pose = reg.body_placement(doc, source)
    axis_dirs = [
        (rec.index, g.reflect_direction(g.rotate_direction(pose, rec.axis_dir), normal))
        for rec in source.faces.values()
        if rec.axis_dir is not None and not rec.stale
    ]

    def build(ctx: oc.OpContext) -> None:
        world = reg.world_shape(doc, source)
        if world.isNull():
            raise geometry_failed("cannot mirror a body with no shape", op)
        placement = world.Placement
        local = world.copy()
        local.Placement = Placement()
        into_local = placement.inverse()
        base = into_local.multVec(g.fcvec(origin))
        direction = into_local.Rotation.multVec(g.fcvec(normal))
        mirrored = local.mirror(base, direction)
        if mirrored.isNull() or not mirrored.isValid():
            raise geometry_failed("the mirrored shape is invalid", op)
        source_obj = doc.fc_doc.getObject(source.fc_body if source.kind == "partdesign" else source.result_obj)
        obj = fc.add_object(doc.fc_doc, "Part::Feature", "Mirror", label=ctx.label())
        fc.set_prop(
            obj,
            "Shape",
            mirrored,
            source=f"{literal(source_obj)}.Shape.mirror({literal(base)}, {literal(direction)})",
        )
        fc.set_prop(obj, "Placement", placement)
        _new_part_body(ctx, obj)

    result = oc.run_op(doc, "mirror_body", params, build)
    twin = doc.bodies[result["created_bodies"][0]["id"]]
    by_index = {rec.index: rec for rec in twin.faces.values() if not rec.stale}
    for index, direction in axis_dirs:
        rec = by_index.get(index)
        if rec is not None and rec.sig.surface in ("cylinder", "cone"):
            rec.axis_dir = direction
    return result


def _half_space(origin: g.Vec3, normal: g.Vec3, positive: bool, reach: float) -> Any:
    """A box that fills one side of the plane (through ``origin``, with unit ``normal``) out to ``reach`` in every
    direction: the side the normal points to when ``positive``, the other side otherwise."""
    box = Part.makeBox(2.0 * reach, 2.0 * reach, reach, Vector(-reach, -reach, 0.0 if positive else -reach))
    box.Placement = Placement(g.fcvec(origin), Rotation(Vector(0, 0, 1), g.fcvec(normal)))
    return box


def split(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """Cut a body in two along a plane into history-free ``Part::Feature`` bodies (``common`` of the placed shape with a
    half-space box): the positive piece (the side the normal points to) then the negative one, only the kept ones for
    ``keep`` positive or negative. The source is CONSUMED: marked deleted, its faces and edges stale, its object hidden.
    The pieces add up to the source within ~1e-9 mm3 for any plane, oblique ones included.

    A plane with no material on one side does not cut the body: a deterministic ``geometry_failed``, never retried.
    The work happens in document coordinates (the placed shape and a half-space box placed in the document), so a moved
    or rotated source of either kind splits where it stands. The intended axis senses of the source's cylinders go to
    the pieces' faces that continue the same surface."""
    op = _op(params)
    source = reg.resolve_body(doc, reg.handle_id(params["source"], op, "source"), op)
    keep = str(params.get("keep", "both"))
    if keep not in ("both", "positive", "negative"):
        raise invalid_request(f"unknown split keep {keep!r} (both, positive or negative)", op)
    _, origin, normal = oc.mirror_plane(params, op, "split")
    normal = g.unit(normal)
    sides = ("positive", "negative") if keep == "both" else (keep,)
    hints = _axis_hints(doc, source)

    def build(ctx: oc.OpContext) -> None:
        world = reg.world_shape(doc, source)
        if world.isNull():
            raise geometry_failed("cannot split a body with no shape", op)
        volume = float(world.Volume)
        tolerance = max(1e-9, 1e-9 * volume)
        box = g.exact_box(world)
        reach = 2.0 * box.DiagonalLength + (g.fcvec(origin) - box.Center).Length + 1.0
        pieces: dict[str, Any] = {}
        for side in ("positive", "negative"):
            solids = world.common(_half_space(origin, normal, side == "positive", reach)).Solids
            piece_volume = sum(float(s.Volume) for s in solids)
            if not solids or piece_volume <= tolerance or piece_volume >= volume - tolerance:
                raise unrecoverable(
                    geometry_failed(
                        f"plane does not cut the body: there is no material on its {side} side "
                        f"(or all of it is): move the plane through the body",
                        op,
                    )
                )
            piece = solids[0] if len(solids) == 1 else Part.Compound(solids)
            if piece.isNull() or not piece.isValid():
                raise geometry_failed(f"the {side} piece of the split is invalid", op)
            pieces[side] = piece
        source_obj = doc.fc_doc.getObject(source.fc_body if source.kind == "partdesign" else source.result_obj)
        for side in sides:
            # one kept piece is the op's own object; both pieces are named by their side of the plane
            piece_label = ctx.label() if len(sides) == 1 else ctx.label(side)
            obj = fc.add_object(doc.fc_doc, "Part::Feature", f"Split{side.capitalize()}", label=piece_label)
            fc.set_prop(
                obj,
                "Shape",
                pieces[side],
                source=f"{literal(source_obj)}.Shape.common(<half space {side} of {literal(g.fcvec(origin))})",
            )
            _new_part_body(ctx, obj)
        fc.set_prop(source_obj, "Visibility", False)
        source.deleted = True
        for rec in source.faces.values():
            rec.stale = True
        for rec in source.edges.values():
            rec.stale = True
        ctx.feature.modified_bodies.append(source.id)

    result = oc.run_op(doc, "split", params, build)
    for body_id in (b["id"] for b in result["created_bodies"]):
        _inherit_axis_senses(doc.bodies[body_id], hints)
    return result
