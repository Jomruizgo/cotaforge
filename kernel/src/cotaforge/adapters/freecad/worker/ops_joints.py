"""Joints of an assembly: fixed, revolute and slider, with a value that carries one component.

Two things are kept for each joint. The harness' own record (``registry.JointRec``: axis, the pose of the joined
component at value 0, limits, value) is what moves the component -- ``pose(value)`` is exact and does not depend on
the Assembly solver -- and the FreeCAD Assembly workbench's ``JointObject`` (a real ``Fixed`` / ``Revolute`` /
``Slider`` joint, with its limits, between the two components) is what the engineer sees and edits in the model tree.
FreeCAD joins two parts, so a joint to the ground has no FreeCAD joint object, and neither has one in a FreeCAD that
cannot import the Assembly workbench: the joint then works in the harness only, and the result says so.

A component carried by a joint (or that a joint hangs from) is not placed by hand (``ops_assembly.refuse_if_jointed``).
"""

import math
from typing import Any

import fc
import FreeCAD as App
import geometry as g
import ops_assembly as oa
import ops_common as oc
import registry as reg
from FreeCAD import Placement, Rotation, Vector
from protocol import invalid_request

MAX_SWEEP_POSITIONS = 721
UNITS = {"revolute": "deg", "slider": "mm", "fixed": ""}
FC_JOINT_TYPES = {"fixed": 0, "revolute": 1, "slider": 3}  # index in JointObject.JointTypes
TOL = 1e-9


def _op(params: dict[str, Any]) -> str:
    return str(params.get("operation_id", ""))


# --- the record -------------------------------------------------------------------------------------------------


def delta(rec: reg.JointRec, value: float) -> Placement:
    """The motion of value ``value`` in assembly axes, to apply AFTER the pose at value 0."""
    if rec.joint_type == "fixed" or rec.axis is None:
        return Placement()
    origin, direction = rec.axis
    if rec.joint_type == "revolute":
        rotation = Rotation(g.fcvec(direction), value)
        pivot = g.fcvec(origin)
        return Placement(pivot - rotation.multVec(pivot), rotation)
    return Placement(g.fcvec(g.scale(direction, value)), Rotation())


def pose(rec: reg.JointRec, value: float) -> Placement:
    return delta(rec, value).multiply(rec.base)


def info(rec: reg.JointRec) -> dict[str, Any]:
    return {
        "handle": {"id": rec.id},
        "joint_type": rec.joint_type,
        "a": {"id": rec.a} if rec.a else None,
        "b": {"id": rec.b},
        "axis": None if rec.axis is None else g.axis_dict(*rec.axis),
        "value": rec.value,
        "min_value": rec.low,
        "max_value": rec.high,
        "unit": UNITS[rec.joint_type],
    }


def _within(rec: reg.JointRec, value: float) -> bool:
    return (rec.low is None or value >= rec.low - TOL) and (rec.high is None or value <= rec.high + TOL)


def _limits_text(rec: reg.JointRec) -> str:
    low = "-inf" if rec.low is None else f"{rec.low:g}"
    high = "inf" if rec.high is None else f"{rec.high:g}"
    return f"[{low}, {high}] {UNITS[rec.joint_type]}"


def _require_value_joint(rec: reg.JointRec, op: str) -> None:
    if rec.joint_type == "fixed":
        raise invalid_request(f"joint {rec.id} is fixed: it has no value to set", op)


# --- FreeCAD joint objects --------------------------------------------------------------------------------------


def _reference(doc: reg.DocumentRec, comp: reg.ComponentRec, jcs: Placement) -> tuple[Any, Placement]:
    """(joint reference, the connector placement in the frame FreeCAD measures it in) for a connector ``jcs`` given in
    assembly axes: the first link of the component, whose own placement chain FreeCAD composes."""
    import UtilsAssembly  # noqa: PLC0415 - the Assembly workbench's own module

    grp = doc.fc_doc.getObject(comp.fc_part)
    link = doc.fc_doc.getObject(comp.fc_links[0])
    ref = [grp, [f"{link.Name}.Face1", f"{link.Name}.Face1"]]
    frame = UtilsAssembly.getGlobalPlacement(ref)
    return ref, frame.inverse().multiply(jcs)


def _grounded(joint_group: Any, grp: Any) -> bool:
    return any(
        getattr(o, "ObjectToGround", None) is grp for o in joint_group.OutList if "ObjectToGround" in o.PropertiesList
    )


def _make_fc_joints(
    doc: reg.DocumentRec, rec: reg.JointRec, a_comp: reg.ComponentRec | None, b_comp: reg.ComponentRec
) -> tuple[list[str], str]:
    """The FreeCAD joint objects of ``rec`` and a note when there are none (why)."""
    if a_comp is None:
        return [], "a joint to the ground has no FreeCAD joint object (FreeCAD joins two parts)"
    container = oa._assembly_object(doc)  # noqa: SLF001 - shared with the assembly ops
    if container is None:
        return [], "this FreeCAD cannot import the Assembly workbench: the joint exists in the harness only"
    names: list[str] = []
    try:
        import AssemblyApp  # noqa: F401, PLC0415
        import JointObject  # noqa: PLC0415
        import UtilsAssembly  # noqa: PLC0415

        group = UtilsAssembly.getJointGroup(container)
        a_grp = doc.fc_doc.getObject(a_comp.fc_part)
        if not _grounded(group, a_grp) and not any(j.b == a_comp.id for j in doc.joints.values() if j is not rec):
            ground = fc.call(group, "newObject", "App::FeaturePython", "GroundedJoint")
            JointObject.GroundedJoint(ground, a_grp)
            names.append(ground.Name)
        joint = fc.call(group, "newObject", "App::FeaturePython", "Joint")
        names.append(joint.Name)
        JointObject.Joint(joint, FC_JOINT_TYPES[rec.joint_type])
        fc.set_label(joint, rec.created_by or rec.id)
        # both connectors sit at the joint's frame in assembly axes: z along the axis, at the axis origin
        if rec.axis is None:
            jcs = Placement(b_comp_origin(doc, b_comp), Rotation())
        else:
            origin, direction = rec.axis
            jcs = Placement(g.fcvec(origin), g.rotation_between((0.0, 0.0, 1.0), direction))
        ref_a, local_a = _reference(doc, a_comp, jcs)
        ref_b, local_b = _reference(doc, b_comp, jcs)
        fc.set_prop(joint, "Reference1", ref_a)
        fc.set_prop(joint, "Reference2", ref_b)
        fc.set_prop(joint, "Detach1", True)
        fc.set_prop(joint, "Detach2", True)
        fc.set_prop(joint, "Placement1", local_a)
        fc.set_prop(joint, "Placement2", local_b)
        _fc_limits(joint, rec)
        if App.GuiUp:  # the visible worker draws joints with their view provider
            joint.ViewObject  # noqa: B018 - make sure it exists
            JointObject.ViewProviderJoint(joint.ViewObject)
        return names, ""
    except Exception as exc:  # noqa: BLE001 - the joint works without the object; say why there is none
        for name in names:  # nothing half-made is left in the model tree
            if doc.fc_doc.getObject(name) is not None:
                fc.remove_object(doc.fc_doc, name)
        return [], f"FreeCAD joint object not created ({type(exc).__name__}: {exc}); the joint exists in the harness"


def b_comp_origin(doc: reg.DocumentRec, comp: reg.ComponentRec) -> Vector:
    return doc.fc_doc.getObject(comp.fc_part).Placement.Base


def _fc_limits(joint: Any, rec: reg.JointRec) -> None:
    if rec.joint_type == "revolute":
        low, high, enable_low, enable_high, lo_prop, hi_prop = (
            rec.low,
            rec.high,
            "EnableAngleMin",
            "EnableAngleMax",
            "AngleMin",
            "AngleMax",
        )
    elif rec.joint_type == "slider":
        low, high, enable_low, enable_high, lo_prop, hi_prop = (
            rec.low,
            rec.high,
            "EnableLengthMin",
            "EnableLengthMax",
            "LengthMin",
            "LengthMax",
        )
    else:
        return
    if low is not None:
        fc.set_prop(joint, lo_prop, low)
        fc.set_prop(joint, enable_low, True)
    if high is not None:
        fc.set_prop(joint, hi_prop, high)
        fc.set_prop(joint, enable_high, True)


# --- ops --------------------------------------------------------------------------------------------------------


def add_joint(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    op = _op(params)
    joint_type = str(params["joint_type"])
    b_comp = reg.resolve_component(doc, reg.handle_id(params["b"], op, "b"), op)
    a_comp = None if params.get("a") is None else reg.resolve_component(doc, reg.handle_id(params["a"], op, "a"), op)
    if a_comp is b_comp:
        raise invalid_request("a joint joins two different components", op)
    low, high = params.get("min_value"), params.get("max_value")
    if (low is not None and low > 0) or (high is not None and high < 0):
        raise invalid_request("the limits must include 0: the pose the joint is made at is its value 0", op)
    for other in doc.joints.values():
        if other.b == b_comp.id:
            raise invalid_request(f"component {b_comp.id} is already joined by {other.id}", op)
    axis = None
    if joint_type != "fixed":
        raw = params.get("axis")
        if raw is None:
            raise invalid_request(f"a {joint_type} joint needs an axis", op)
        direction = g.unit(tuple(float(x) for x in raw["direction"]))
        if g.length(direction) < 0.5:
            raise invalid_request("the axis has no direction", op)
        axis = (tuple(float(x) for x in raw["origin"]), direction)
    base = Placement(doc.fc_doc.getObject(b_comp.fc_part).Placement)
    a_id = a_comp.id if a_comp else None
    rec = reg.JointRec(doc.next_id("joint"), joint_type, a_id, b_comp.id, axis, base, low, high)
    notes: list[str] = []

    def build(ctx: oc.OpContext) -> None:
        rec.created_by = ctx.feature.label
        names, note = _make_fc_joints(doc, rec, a_comp, b_comp)
        rec.fc_objects = names
        if note:
            notes.append(note)
            ctx.feature.warnings.append(note)
        doc.joints[rec.id] = rec
        ctx.feature.component = b_comp.id
        ctx.feature.fc_names.extend(names)

    result = oc.run_op(doc, "add_joint", params, build)
    result["joint"] = info(rec)
    return result


def set_joint(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    op = _op(params)
    rec = reg.resolve_joint(doc, reg.handle_id(params["joint"], op, "joint"), op)
    _require_value_joint(rec, op)
    value = float(params["value"])
    if not _within(rec, value):
        raise invalid_request(
            f"value {value:g} {UNITS[rec.joint_type]} is outside the limits of joint {rec.id}: {_limits_text(rec)}", op
        )
    comp = reg.resolve_component(doc, rec.b, op)

    def build(ctx: oc.OpContext) -> None:
        grp = doc.fc_doc.getObject(comp.fc_part)
        fc.set_prop(grp, "Placement", pose(rec, value))
        comp._placed = None
        rec.value = value
        ctx.feature.component = comp.id
        ctx.feature.fc_names.append(grp.Name)
        ctx.check_objects.append(grp)

    result = oc.run_op(doc, "set_joint", params, build)
    result["joint"] = info(rec)
    return result


def joint_info(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    op = _op(params)
    return info(reg.resolve_joint(doc, reg.handle_id(params["joint"], op, "joint"), op))


# --- sweep ------------------------------------------------------------------------------------------------------


def sweep_values(start: float, end: float, step: float) -> list[float]:
    """Same positions as ``cotaforge.contract.sweep_values`` (the worker cannot import the kernel)."""
    span = abs(end - start)
    count = int(span / step + 1e-9)
    sign = 1.0 if end >= start else -1.0
    values = [round(start + sign * step * i, 9) for i in range(count + 1)]
    if span - count * step > 1e-9:
        values.append(float(end))
    return values


def sweep_joint(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """Drive the joint through its positions and measure the component it carries against the others at each one.
    Nothing is recorded: the pose is put back exactly as it was, even when a measurement fails."""
    op = _op(params)
    rec = reg.resolve_joint(doc, reg.handle_id(params["joint"], op, "joint"), op)
    _require_value_joint(rec, op)
    start, end, step = float(params["start"]), float(params["end"]), float(params["step"])
    if not (step > 0 and math.isfinite(step) and math.isfinite(start) and math.isfinite(end)):
        raise invalid_request("a sweep needs finite start and end and a step above 0", op)
    if int(abs(end - start) / step + 1e-9) + 2 > MAX_SWEEP_POSITIONS + 1:
        raise invalid_request(
            f"the sweep has more than {MAX_SWEEP_POSITIONS} positions: raise the step or shorten the range", op
        )
    values = sweep_values(start, end, step)
    if len(values) > MAX_SWEEP_POSITIONS:
        raise invalid_request(
            f"the sweep has {len(values)} positions, the most is {MAX_SWEEP_POSITIONS}: raise the step or shorten "
            "the range",
            op,
        )
    outside = [v for v in (values[0], values[-1]) if not _within(rec, v)]
    if outside:
        raise invalid_request(
            f"the sweep goes outside the limits of joint {rec.id}: {_limits_text(rec)}; use start and end inside them",
            op,
        )
    moving = reg.resolve_component(doc, rec.b, op)
    others = [c for c in doc.components.values() if c.id != moving.id]
    grp = doc.fc_doc.getObject(moving.fc_part)
    saved = Placement(grp.Placement)
    notes: list[str] = []
    least: dict[str, dict[str, Any]] = {c.id: {} for c in others}
    first: tuple[float, str, float] | None = None
    try:
        for value in values:
            grp.Placement = pose(rec, value)
            moving._placed = None
            shape = reg.placed_component_shape(doc, moving)
            clash_here: tuple[str, float] | None = None
            for other in others:
                volume, distance, note = oa.measure_pair(shape, reg.placed_component_shape(doc, other))
                row = least[other.id]
                if distance is None:
                    row["unmeasured"] = row.get("unmeasured", 0) + 1
                    if note and note not in notes:
                        notes.append(note)
                elif volume <= 0.0 and "first_overlap" not in row and ("min" not in row or distance < row["min"] - TOL):
                    row["min"], row["at"] = distance, value  # the clearance up to the first overlap
                if volume > 0.0:
                    row.setdefault("first_overlap", value)
                    if clash_here is None or volume > clash_here[1]:
                        clash_here = (other.id, volume)
            if clash_here is not None and first is None:
                first = (value, clash_here[0], clash_here[1])
    finally:
        grp.Placement = saved
        moving._placed = None
    pairs = []
    for other in others:
        row = least[other.id]
        pairs.append(
            {
                "a": {"id": moving.id},
                "b": {"id": other.id},
                "min_distance_mm": row.get("min"),
                "at_value": row.get("at"),
                "first_overlap_value": row.get("first_overlap"),
                "unmeasured": row.get("unmeasured", 0),
            }
        )
    clash = (
        None
        if first is None
        else {"value": first[0], "a": {"id": moving.id}, "b": {"id": first[1]}, "volume_mm3": first[2]}
    )
    return {
        "joint": info(rec),
        "start": start,
        "end": end,
        "step": step,
        "positions": len(values),
        "first_clash": clash,
        "pairs": pairs,
        "notes": notes,
    }


__all__ = ["add_joint", "joint_info", "set_joint", "sweep_joint"]
