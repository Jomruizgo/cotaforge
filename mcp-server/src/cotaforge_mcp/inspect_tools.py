"""``inspect``: read-only measured facts about the live document."""

import math
from dataclasses import dataclass
from typing import Any

from cotaforge.constraints import DensityLookup, describe_density, lookup_density, mass_kg
from cotaforge.contract import (
    MAX_SWEEP_POSITIONS,
    BodyInfo,
    BodyInterferenceRequest,
    DocumentKind,
    EdgeHandle,
    EdgeInfo,
    EdgeRequest,
    EdgesBetweenRequest,
    Empty,
    FaceHandle,
    FaceInfo,
    FaceRequest,
    FeatureHandle,
    FeatureRequest,
    HoleInfo,
    InterferenceRequest,
    JointHandle,
    JointSweepResult,
    ListHolesRequest,
    MassRequest,
    ProvenanceRole,
    SketchStatusRequest,
    SweepJointRequest,
    sweep_values,
)
from cotaforge.executor.edge_sets import report as edge_set_report
from cotaforge.executor.errors import ResolutionError
from cotaforge.ir import EdgeSet
from pydantic import ValidationError

from cotaforge_mcp import feature_tools, foreign, mould_tools, rules
from cotaforge_mcp.overflow import Overflow
from cotaforge_mcp.reply import ToolError, area_text, num, vec
from cotaforge_mcp.schemas import InspectArgs
from cotaforge_mcp.session import Session, measured_mass_line

LIST_LIMIT = 4000


def _features(session: Session, only: str | None = None) -> list[tuple[str, FeatureHandle]]:
    """(step id or alias, feature handle) of everything in effect, oldest first."""
    session.require_executor()
    found: list[tuple[str, FeatureHandle]] = []
    found.extend(session.aliases.items())
    for step in session.steps():
        if step.feature is not None:
            found.append((step.id, step.feature))
    if only is not None:
        found = [item for item in found if item[0] == only]
        step = None if found or only in session.known_ids() else session.step_naming(only)
        if step is not None:
            raise ToolError(
                f"{only!r} is a body id; op takes the id of the step whose faces you want: {only} is named by step "
                f'{step!r}: inspect(action="faces", op="{step}")',
                code="invalid_input",
                field_name="op",
                usage='inspect(action="faces", op="<step id>")',
                example=f'inspect(action="faces", op="{step}")',
            )
        if not found:
            raise ToolError(
                f"no step or adopted feature {only!r} produced geometry",
                code="unknown_step",
                field_name="op",
                usage='inspect(action="tree") lists the steps',
            )
    return found


def _id_number(handle_id: str) -> tuple[str, int]:
    kind, _, rest = handle_id.partition(":")
    return kind, int(rest) if rest.isdigit() else 0


def _dot(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def _unit(v: list[float]) -> tuple[float, ...]:
    length = math.sqrt(sum(x * x for x in v)) or 1.0
    return tuple(x / length for x in v)


def _face_row(session: Session, face: FaceInfo) -> str:
    origin = session.step_of_feature(face.provenance.created_by.id) or face.provenance.created_by.id
    normal = f" n{vec(face.normal, 3)}" if face.normal is not None else ""
    radius = f" r={num(face.radius)}" if face.radius is not None else ""
    return (
        f"{face.handle.id} {face.surface.value}{normal}{radius} c{vec(face.center)} "
        f"A={num(face.area_mm2)} mm2 by={origin}"
    )


def _edge_row(edge: EdgeInfo) -> str:
    radius = f" r={num(edge.radius)}" if edge.radius is not None else ""
    convex = "" if edge.convex is None else (" convex" if edge.convex else " concave")
    angle = f" {num(edge.dihedral_deg, 1)}deg" if edge.dihedral_deg is not None else ""
    center = f" c{vec(edge.center)}" if edge.center is not None else ""
    return (
        f"{edge.handle.id} {edge.curve.value}{radius} L={num(edge.length_mm)} mm{center} "
        f"faces {edge.faces[0].id},{edge.faces[1].id}{convex}{angle}"
    )


def tree(session: Session, feature: str | None = None) -> list[str]:
    """The steps and the features of the document; with the CAD's own listing (``list_features``) every feature is
    shown by its CAD name, whoever made it, and ``feature`` asks for one in detail."""
    session.require_executor()
    listed = session.supports("list_features")
    if feature is not None:
        if not listed:
            raise ToolError("tree with id is not available with this CAD", code="not_supported", field_name="id")
        return feature_tools.tree_lines(session, feature)
    nodes = session.adapter.feature_tree(Empty(operation_id="tree")).nodes
    if listed:  # the CAD's listing below covers what was made outside the steps
        nodes = tuple(n for n in nodes if not n.parameters.get("manual"))
    own = feature_tools.tree_lines(session) if listed else []
    if not nodes and not own:
        return ["tree: empty"]
    lines = [f"tree: {len(nodes)} features"] if nodes or not listed else []
    for node in nodes:
        label = session.step_of_feature(node.handle.id)
        if node.parameters.get("manual"):
            lines.append(f"{node.handle.id} {node.kind} '{node.name}' (made outside the steps)")
            continue
        extra = ", ".join(f"{k}={v}" for k, v in list(node.parameters.items())[:4] if k != "object")
        lines.append(
            f"{node.handle.id} {node.kind} '{node.name}'"
            + (f" [{label}]" if label else "")
            + (f" ({extra})" if extra else "")
        )
    return [*lines, *own]


def faces(session: Session, args: InspectArgs) -> list[str]:
    wanted: dict[str, FaceInfo] = {}
    for _label, feature in _features(session, args.op):
        result = session.adapter.faces_of(
            FeatureRequest(operation_id="faces", feature=feature, role=ProvenanceRole.any)
        )
        for face in result.faces:
            wanted.setdefault(face.handle.id, face)
    items = sorted(wanted.values(), key=lambda f: _id_number(f.handle.id))
    if args.normal is not None:
        target = _unit(args.normal)
        items = [f for f in items if f.normal is not None and _dot(f.normal, target) > 0.999]
    if args.surface is not None:
        items = [f for f in items if f.surface.value == args.surface]
    lines = [f"faces: {len(items)}"]
    lines.extend(_face_row(session, f) for f in items[: args.limit])
    if len(items) > args.limit:
        lines.append(f"... {len(items) - args.limit} more (filter with normal/surface/op or raise limit)")
    return lines


def _edge_set_preview(session: Session, args: InspectArgs) -> list[str]:
    """``inspect(action="edges", where={...}, op="<step>")``: the edges an edge set would resolve to, with no op run."""
    executor = session.require_executor()
    data: dict[str, Any] = {"kind": "edges", "where": args.where}
    if args.op is not None:
        data["from_op"] = args.op
    elif not (args.where or {}).get("on_face"):
        stepped = [s.id for s in session.steps() if s.feature is not None]
        if stepped:
            data["from_op"] = stepped[-1]
    try:
        ref = EdgeSet.model_validate(data)
    except ValidationError as exc:
        detail = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:4])
        raise ToolError(
            f"where is not a valid edge set: {detail}",
            code="invalid_input",
            field_name="where",
            usage='inspect(action="edges", op="<step>", where={"parallel_to":"z","convex":true})',
            example='inspect(action="edges", op="plate", where={"parallel_to":"z","convex":true})',
        ) from exc
    try:
        found = executor.preview_edge_set(ref)
    except ResolutionError as exc:
        raise ToolError(exc.message, code="unresolved_reference", field_name="where") from exc
    return ["edge set (nothing was changed):", *edge_set_report("edges", ref, found, limit=args.limit)]


def edges(session: Session, args: InspectArgs) -> list[str]:
    session.require_executor()
    if args.where is not None:
        return _edge_set_preview(session, args)
    if args.between is not None:
        a, b = args.between
        result = session.adapter.edges_between(
            EdgesBetweenRequest(operation_id="edges", a=FaceHandle(id=a), b=FaceHandle(id=b))
        )
    elif args.face is not None:
        result = session.adapter.edges_of(FaceRequest(operation_id="edges", face=FaceHandle(id=args.face)))
    else:
        raise ToolError(
            "edges needs the face (or two faces) they bound",
            code="invalid_input",
            field_name="face",
            usage='inspect(action="edges", face="face:3") | inspect(action="edges", between=["face:1","face:3"])',
            example='inspect(action="edges", face="face:1")',
        )
    items = sorted(result.edges, key=lambda e: _id_number(e.handle.id))
    lines = [f"edges: {len(items)}"]
    lines.extend(_edge_row(e) for e in items[: args.limit])
    if len(items) > args.limit:
        lines.append(f"... {len(items) - args.limit} more (raise limit)")
    return lines


def _need_id(args: InspectArgs, kind: str) -> str:
    if not args.id:
        raise ToolError(
            f"{kind} needs the handle id",
            code="invalid_input",
            field_name="id",
            usage=f'inspect(action="{kind}", id="{kind}:3")',
            example=f'inspect(action="{kind}", id="{kind}:1")',
        )
    return args.id


def face_detail(session: Session, args: InspectArgs) -> list[str]:
    session.require_executor()
    face = session.adapter.face_info(FaceRequest(operation_id="face", face=FaceHandle(id=_need_id(args, "face"))))
    lines = [
        _face_row(session, face),
        f"body: {session.body_label(face.body.id)}",
        f"bbox: min {vec(face.bbox.min)} max {vec(face.bbox.max)}",
    ]
    if face.axis is not None:
        lines.append(f"axis: origin {vec(face.axis.origin)} direction {vec(face.axis.direction)}")
    if face.provenance.modified_by:
        mods = [session.step_of_feature(m.id) or m.id for m in face.provenance.modified_by]
        lines.append("modified by: " + ", ".join(mods))
    return lines


def edge_detail(session: Session, args: InspectArgs) -> list[str]:
    session.require_executor()
    edge = session.adapter.edge_info(EdgeRequest(operation_id="edge", edge=EdgeHandle(id=_need_id(args, "edge"))))
    lines = [_edge_row(edge), f"bbox: min {vec(edge.bbox.min)} max {vec(edge.bbox.max)}"]
    if edge.start is not None and edge.end is not None:
        lines.append(f"from {vec(edge.start)} to {vec(edge.end)}")
    return lines


def _density_of(session: Session) -> DensityLookup:
    """The density of the material the document carries (declared density first, then the shipped table)."""
    return lookup_density(session.design_attributes().material, rules.declared_set(session.settings))


def _bom_key(body: BodyInfo) -> tuple[int, float, tuple[float, ...]]:
    """What makes two bodies 'the same part': face count, volume and sorted extent (rounded to ~1e-4 relative)."""
    size = sorted(body.bbox.size())
    volume = body.volume_mm3 or 0.0  # only solids are grouped (bom skips surfaces)
    return body.face_count, float(f"{volume:.4g}"), tuple(round(s, 2) for s in size)


def bom(session: Session) -> list[str]:
    """Bill of materials: one row per distinct body (identical ones grouped), measured, with the declared
    material and route, which the document carries as a whole."""
    session.require_executor()
    groups: dict[tuple[int, float, tuple[float, ...]], list[tuple[str, BodyInfo]]] = {}
    surfaces: list[str] = []
    seen: set[str] = set()
    for label, feature in _features(session):
        for body in session.adapter.bodies_of(FeatureRequest(operation_id="bom", feature=feature)).bodies:
            if body.handle.id in seen:
                continue
            seen.add(body.handle.id)
            if body.kind == "surface":
                surfaces.append(f"{session.body_label(body.handle.id)} ({area_text(body.area_mm2)})")
                continue
            origin = session.step_of_feature(body.provenance.created_by.id) or label
            groups.setdefault(_bom_key(body), []).append((origin, body))
    skipped = [f"left out (surface: no volume, no mass): {_surface_names(surfaces)}"] if surfaces else []
    if not groups:
        return ["bom: empty (no solid body yet)", *skipped] if surfaces else ["bom: empty (no body yet)"]
    attributes = session.design_attributes()
    lookup = lookup_density(attributes.material, rules.declared_set(session.settings))
    material = attributes.material or "none"
    route = ", ".join(attributes.route) or "none"
    total = sum(len(items) for items in groups.values())
    lines = [f"bom: {len(groups)} line(s), {total} part(s); material: {material}; route: {route}"]
    total_kg = 0.0
    sources: list[str] = []  # who measured the masses the CAD gave (a body of the CAD's own mass is never recomputed)
    computed = missing = 0
    for items in groups.values():
        body = items[0][1]
        ids = ",".join(origin for origin, _ in items[:4]) + (",..." if len(items) > 4 else "")
        volume = body.volume_mm3 or 0.0
        row = f"{ids} x{len(items)}: volume {num(volume)} mm3 each"
        each: float | None = None
        if body.mass_kg is not None:
            each = body.mass_kg
            if body.mass_source not in sources:
                sources.append(body.mass_source)
            row += f", mass {num(each, 4)} kg each ({body.mass_source} measured it)"
        elif lookup.density is not None:
            each = mass_kg(volume, lookup.density)
            computed += len(items)
            row += f", mass {num(each, 4)} kg each"
        else:
            missing += len(items)
        if each is not None:
            total_kg += each * len(items)
        lines.append(row)
    if sources and not computed and not missing:
        lines.append(f"total mass: {num(total_kg, 4)} kg ({' and '.join(sources)} measured it)")
    elif sources:
        rest = f"the rest from {describe_density(lookup.density)}" if lookup.density is not None else "the rest unknown"
        lines.append(
            f"total mass: {num(total_kg, 4)} kg of the parts with a mass ({' and '.join(sources)} measured it; {rest})"
        )
    elif lookup.density is not None:
        lines.append(f"total mass: {num(total_kg, 4)} kg ({describe_density(lookup.density)})")
    else:
        lines.append(f"mass: not available ({lookup.reason})")
    return [*lines, *skipped]


def _surface_names(names: list[str], shown: int = 4) -> str:
    more = f" and {len(names) - shown} more" if len(names) > shown else ""
    return ", ".join(names[:shown]) + more


def properties(session: Session, args: InspectArgs) -> list[str]:
    session.require_executor()
    body = session.body_handle(args.body, "body") if args.body else None
    if body is not None:
        surface = next((b for b in _live_bodies(session) if b.handle.id == body.id and b.kind == "surface"), None)
        if surface is not None:
            return _surface_properties(session, surface)
    mass = session.adapter.mass_properties(MassRequest(operation_id="properties", body=body))
    volume = f"volume: {num(mass.volume_mm3)} mm3"
    if mass.surface_only:
        volume += f" ({foreign.surface_only_wording(len(mass.surface_only), mass.centroid_meaningful)})"
    lines = [volume]
    lookup = _density_of(session)
    if mass.mass_kg is not None:  # the CAD measured it with its own material: that value, not one from our density
        lines.append(measured_mass_line(mass))
    elif lookup.density is not None:
        kg = mass_kg(mass.volume_mm3, lookup.density)
        lines.append(f"mass: {num(kg, 4)} kg ({describe_density(lookup.density)})")
    else:
        lines.append(f"mass: not available ({lookup.reason})")
    if mass.surface_mm2 is not None:
        surface = f"surface: {num(mass.surface_mm2)} mm2"
        if mass.area_left_out:
            names = ", ".join(session.body_label(b) for b in mass.area_left_out[:4])
            more = f" and {len(mass.area_left_out) - 4} more" if len(mass.area_left_out) > 4 else ""
            surface += (
                f" (without {names}{more}: the CAD returned an area that cannot be real, the shape is not valid; "
                "inspect validity says where)"
            )
        lines.append(surface)
    if mass.centroid_meaningful:
        lines.append(f"centroid: {vec(mass.centroid)} mm" + (" (of the solids only)" if mass.surface_only else ""))
    else:
        lines.append("centroid: not meaningful (no solid in the document: there is no volume to take it of)")
    if mass.inertia is not None:
        lines.append("inertia (about the centroid, mm5 per unit density):")
        lines.extend("  " + vec(row, 2) for row in mass.inertia)
    return lines


def _surface_properties(session: Session, body: BodyInfo) -> list[str]:
    """A surface body has an area and nothing else to weigh: no volume, no mass (never a measured 0)."""
    return [
        f"{session.body_label(body.handle.id)}: surface body, {area_text(body.area_mm2)}",
        "volume: none (a surface has no volume)",
        "mass: none (a surface has no volume to weigh)",
        f"size {vec(body.bbox.size())} mm, {body.face_count} faces",
    ]


def measure(session: Session) -> list[str]:
    session.require_executor()
    mass, bbox = session.measure()
    lines = [
        f"bbox: size {vec(bbox.size())} mm, min {vec(bbox.min)}, max {vec(bbox.max)}",
        f"volume: {num(mass.volume_mm3)} mm3",
    ]
    if bbox.left_out:
        lines.append(f"note: the box leaves out faces that have no real size: {'; '.join(bbox.left_out[:6])}")
    for body in _live_bodies(session):
        left_out = body.bbox_faces_left_out
        what = (
            f"surface, {area_text(body.area_mm2)}" if body.kind == "surface" else f"volume {num(body.volume_mm3)} mm3"
        )
        lines.append(
            f"{session.body_label(body.handle.id)}: {what}, "
            f"size {vec(body.bbox.size())}, {body.face_count} faces"
            + (
                f" (the size leaves out {left_out} face{'' if left_out == 1 else 's'} of no real size)"
                if left_out
                else ""
            )
        )
    return lines


def _live_bodies(session: Session) -> list[BodyInfo]:
    """The bodies of everything in effect, each once, oldest first."""
    seen: dict[str, BodyInfo] = {}
    for _label, feature in _features(session):
        for body in session.adapter.bodies_of(FeatureRequest(operation_id="bodies", feature=feature)).bodies:
            seen.setdefault(body.handle.id, body)
    return list(seen.values())


OVERLAP_TOL_MM3 = 1e-6
CONTACT_TOL_MM = 1e-6
MAX_PAIRS = 20


def interference(session: Session) -> list[str]:
    executor = session.require_executor()
    if executor.plan.kind is DocumentKind.assembly:
        return _component_interference(session)
    return _body_interference(session)


@dataclass(frozen=True)
class _Reading:
    """Two things (bodies of a part, components of an assembly) measured against each other: the common volume and the
    minimum distance, ``None`` for what the CAD could not measure."""

    a: str
    b: str
    common_mm3: float | None
    distance_mm: float | None
    note: str = ""


def _state(reading: _Reading) -> str:
    if reading.common_mm3 is not None and reading.common_mm3 > OVERLAP_TOL_MM3:
        return "overlap"
    if reading.distance_mm is not None and reading.distance_mm > CONTACT_TOL_MM:
        return "clear"
    if reading.common_mm3 is not None and reading.distance_mm is not None:
        return "touching"
    return "unmeasured"


def _row(reading: _Reading, state: str) -> str:
    names = f"{reading.a} x {reading.b}"
    if state == "overlap":
        return f"{names}: OVERLAP {num(reading.common_mm3 or 0.0)} mm3"
    if state == "clear":
        return f"{names}: clear, min distance {num(reading.distance_mm or 0.0)} mm" + (
            f" ({reading.note})" if reading.note else ""
        )
    if state == "touching":
        return f"{names}: touching (no common volume)" + (f" ({reading.note})" if reading.note else "")
    common = "common volume NOT measured" if reading.common_mm3 is None else "no common volume"
    distance = "distance NOT measured" if reading.distance_mm is None else "distance 0"
    return f"{names}: {common}, {distance}" + (f" ({reading.note})" if reading.note else "")


def _report(things: str, count: int, readings: list[_Reading], warnings: tuple[str, ...]) -> list[str]:
    """One summary line (how many pairs overlap, touch, are clear or could not be measured) and a row per pair, the
    worst first. A pair the CAD could not measure is said to be so, never counted as clear."""
    rank = {"overlap": 0, "unmeasured": 1, "touching": 2, "clear": 3}
    rows = sorted(
        ((reading, _state(reading)) for reading in readings),
        key=lambda item: (rank[item[1]], -(item[0].common_mm3 or 0.0), item[0].distance_mm or 0.0),
    )
    counts = {state: sum(1 for _, s in rows if s == state) for state in rank}
    total = sum(reading.common_mm3 or 0.0 for reading, state in rows if state == "overlap")
    parts = [f"{counts['overlap']} overlap" + (f" ({num(total)} mm3)" if counts["overlap"] else "")]
    parts.extend(f"{counts[state]} {state}" for state in ("touching", "clear") if counts[state])
    if counts["unmeasured"]:
        parts.append(f"{counts['unmeasured']} not fully measured")
    pairs = len(rows)
    lines = [f"interference: {count} {things}, {pairs} pair{'s' if pairs != 1 else ''}: " + ", ".join(parts)]
    lines.extend(_row(reading, state) for reading, state in rows[:MAX_PAIRS])
    if pairs > MAX_PAIRS:
        lines.append(f"... {pairs - MAX_PAIRS} more pairs (counted above)")
    lines.extend(f"warning: {w}" for w in warnings)
    return lines


def _component_interference(session: Session) -> list[str]:
    """Every pair of components of the assembly, in the format of the bodies of a part."""
    session.need("check_interference", "inspect(action=interference)")
    result = session.adapter.check_interference(InterferenceRequest(operation_id="interference", components=None))
    if not result.pairs:
        return ["interference: not checked: the assembly has fewer than two components"]
    components = {p.a.id for p in result.pairs} | {p.b.id for p in result.pairs}
    readings = [
        _Reading(session.component_label(p.a.id), session.component_label(p.b.id), p.volume_mm3, p.distance_mm, p.note)
        for p in result.pairs
    ]
    return _report("components", len(components), readings, ())


def _body_interference(session: Session) -> list[str]:
    """Every pair of bodies of the part: overlap (common volume), contact or clearance (minimum distance). A pair the
    CAD could not measure is said to be so, never counted as clear."""
    session.need("body_interference", "inspect(action=interference) between the bodies of a part")
    result = session.adapter.body_interference(BodyInterferenceRequest(operation_id="interference"))
    bodies = {p.a.id for p in result.pairs} | {p.b.id for p in result.pairs}
    if not result.pairs:
        count = len(_live_bodies(session))
        return [
            f"interference: not checked: the part has {count} bod{'y' if count == 1 else 'ies'} (interference is "
            "measured between two or more bodies)"
        ]
    readings = [
        _Reading(session.body_label(p.a.id), session.body_label(p.b.id), p.common_mm3, p.distance_mm, p.note)
        for p in result.pairs
    ]
    return _report("bodies", len(bodies), readings, result.warnings)


def sketch_status(session: Session, args: InspectArgs) -> list[str]:
    session.need("sketch_status", "inspect(action=sketch_status)")
    executor = session.require_executor()
    if not args.sketch:
        raise ToolError(
            "sketch_status needs the sketch step id (or an adopted sketch handle)",
            code="invalid_input",
            field_name="sketch",
            usage='inspect(action="sketch_status", sketch="s1")',
            example='inspect(action="sketch_status", sketch="s1")',
        )
    handle = executor.sketch_for(args.sketch)
    target = FeatureHandle(id=handle.id if handle is not None else args.sketch)
    status = session.adapter.sketch_status(SketchStatusRequest(operation_id="sketch_status", sketch=target))
    state = "fully constrained" if status.fully_constrained else f"{status.dof} degrees of freedom left"
    lines = [
        f"sketch {args.sketch}: {state}",
        f"geometry: {status.geometry_count}, constraints: {status.constraint_count}, "
        f"over-constrained: {'YES' if status.over_constrained else 'no'}",
    ]
    lines.extend(
        f"conflict {c.kind}: constraint {c.constraint_index} {c.constraint_name}".rstrip() for c in status.conflicts[:6]
    )
    if status.solver_message:
        lines.append(status.solver_message)
    return lines


SWEEP_USAGE = 'inspect(action="sweep", id="<add_joint step>", range=[from, to, step])'
SWEEP_EXAMPLE = 'inspect(action="sweep", id="hinge", range=[0, 90, 1])'


def _joint_handle(session: Session, args: InspectArgs) -> JointHandle:
    executor = session.require_executor()
    if not args.id:
        raise ToolError(
            "sweep needs the joint (the id of its add_joint step)",
            code="invalid_input",
            field_name="id",
            usage=SWEEP_USAGE,
            example=SWEEP_EXAMPLE,
        )
    handle = executor.joint_for(args.id)
    if handle is None and args.id.startswith("joint:"):
        handle = JointHandle(id=args.id)
    if handle is None:
        context = executor.context
        known = ", ".join(context.joint_by_op) if context is not None and context.joint_by_op else "none yet"
        raise ToolError(
            f"{args.id!r} is not an add_joint step; joints: {known}",
            code="unknown_step",
            field_name="id",
            usage=SWEEP_USAGE,
            example=SWEEP_EXAMPLE,
        )
    return handle


def _at(value: float, unit: str) -> str:
    return f"{num(value)} {unit}".rstrip()


def sweep(session: Session, args: InspectArgs) -> list[str]:
    """Drive one joint through a range and measure the component it moves against every other at each position. The
    wording says what was checked (the positions, with the step) and never that the motion in between is free."""
    session.need("sweep_joint", "inspect(action=sweep)")
    handle = _joint_handle(session, args)
    if args.range is None:
        raise ToolError(
            "sweep needs range=[from, to, step] (degrees of a revolute, mm of a slider)",
            code="invalid_input",
            field_name="range",
            usage=SWEEP_USAGE,
            example=SWEEP_EXAMPLE,
        )
    start, end, step = args.range
    if not (step > 0 and all(math.isfinite(x) for x in args.range)):
        raise ToolError(
            "the step of a sweep must be above 0 and the range finite",
            code="invalid_input",
            field_name="range",
            usage=SWEEP_USAGE,
            example=SWEEP_EXAMPLE,
        )
    count = len(sweep_values(start, end, step)) if abs(end - start) / step < 1e6 else 10**6
    if count > MAX_SWEEP_POSITIONS:
        raise ToolError(
            f"this sweep has {count if count < 10**6 else 'too many'} positions and the most is "
            f"{MAX_SWEEP_POSITIONS}: raise the step or shorten the range (a second sweep over the tight part with a "
            "finer step is cheap)",
            code="sweep_too_long",
            field_name="range",
            usage=SWEEP_USAGE,
            example=SWEEP_EXAMPLE,
        )
    result = session.adapter.sweep_joint(
        SweepJointRequest(operation_id="sweep", joint=handle, start=start, end=end, step=step)
    )
    return _sweep_lines(session, args.id or handle.id, result)


def _sweep_lines(session: Session, name: str, result: JointSweepResult) -> list[str]:
    unit = result.joint.unit
    moving = session.component_label(result.joint.b.id)
    lines = [
        f"sweep {name} ({result.joint.joint_type.value}, moves {moving}): {_at(result.start, unit)} to "
        f"{_at(result.end, unit)} every {_at(result.step, unit)}: {result.positions} positions checked"
        " (only those, not what lies between them)"
    ]
    clash = result.first_clash
    if clash is None:
        lines.append("no overlap at any position checked")
    else:
        lines.append(
            f"first overlap: at {_at(clash.value, unit)}: {session.component_label(clash.a.id)} x "
            f"{session.component_label(clash.b.id)} share {num(clash.volume_mm3)} mm3"
        )
    for pair in result.pairs:
        names = f"{session.component_label(pair.a.id)} x {session.component_label(pair.b.id)}"
        parts = []
        before = " before it" if pair.first_overlap_value is not None else ""
        if pair.first_overlap_value is not None:
            parts.append(f"OVERLAPS from {_at(pair.first_overlap_value, unit)}")
        if pair.min_distance_mm is not None and pair.at_value is not None:
            touching = " (touching)" if pair.min_distance_mm <= 1e-6 else ""
            parts.append(
                f"least clearance{before} {num(pair.min_distance_mm)} mm at {_at(pair.at_value, unit)}{touching}"
            )
        elif pair.first_overlap_value is None and pair.unmeasured < result.positions:
            parts.append("clearance NOT measured")
        if pair.unmeasured:
            parts.append(f"{pair.unmeasured} of {result.positions} positions NOT measured")
        lines.append(f"{names}: " + "; ".join(parts))
    if not result.pairs:
        lines.append("there is no other component to measure against")
    lines.extend(f"note: {note}" for note in result.notes)
    lines.append(f"the joint is back at {_at(result.joint.value, unit)}")
    return lines


def _hole_row(session: Session, hole: HoleInfo) -> str:
    kind = {True: "through", False: "blind", None: "through/blind unknown"}[hole.through]
    arc = "" if hole.arc_deg >= 359.0 else f" arc {num(hole.arc_deg, 0)}deg"
    faces = ",".join(f.id for f in hole.faces)
    fitted = f" fitted (max deviation {num(round(hole.fit_deviation_mm or 0.0, 4))} mm)" if hole.fitted else ""
    sink = ""
    if hole.countersink_diameter_mm is not None and hole.countersink_angle_deg is not None:
        cone = f"D{num(round(hole.countersink_diameter_mm, 2))} {num(round(hole.countersink_angle_deg, 1))}deg"
        sink = f" countersink {cone}"
    return (
        f"D{num(round(hole.diameter_mm, 2))} {kind} depth {num(hole.depth_mm)}{sink}{arc} "
        f"{session.body_label(hole.body.id)} axis from {vec(hole.axis.origin)} along {vec(hole.axis.direction)} "
        f"faces {faces}{fitted}"
    )


def holes(session: Session, args: InspectArgs) -> list[str]:
    """The cylindrical holes (concave cylinders) grouped by diameter to 0.01 mm: the counts first, then one row per
    hole sorted by diameter. A long list shows ``limit`` rows and writes the whole to a file."""
    session.need("list_holes", "inspect(action=holes)")
    session.require_executor()
    body = session.body_handle(args.body, "body") if args.body else None
    result = session.adapter.list_holes(ListHolesRequest(operation_id="holes", body=body))
    items = sorted(
        result.holes,
        key=lambda h: (round(h.diameter_mm, 2), h.body.id, h.axis.origin, h.faces[0].id if h.faces else ""),
    )
    if not items:
        return ["holes: 0 (no concave cylindrical face with half a circle or more)", *result.warnings]
    counts: dict[float, list[HoleInfo]] = {}
    for hole in items:
        counts.setdefault(round(hole.diameter_mm, 2), []).append(hole)
    bodies = len({h.body.id for h in items})
    summary = ", ".join(
        f"{len(group)} x D{num(diameter)}"
        + (f" ({sum(1 for h in group if h.through is True)} through)" if any(h.through for h in group) else "")
        for diameter, group in counts.items()
    )
    report = Overflow(session.settings.workspace, "holes", f"holes of {session.doc_name or 'the document'}")
    shown, more = report.items("holes", [_hole_row(session, h) for h in items], args.limit)
    # what the agent must read comes before the rows: the counts, where the rest is, the warnings and the notes
    lines = [f"holes: {len(items)} in {bodies} bod{'y' if bodies == 1 else 'ies'}; by diameter (mm): {summary}"]
    if more is not None:
        lines.append(more)
        warning = report.write()
        if warning:
            lines.append(warning)
    lines.extend(f"warning: {w}" for w in result.warnings)
    fitted = sum(1 for h in items if h.fitted)
    if fitted:
        lines.append(
            f"note: {fitted} of {len(items)} holes are on non-analytic (B-spline) faces and were recognised by fitting "
            "a cylinder to the surface (diameter and axis good to the deviation shown)"
        )
    lines.extend(shown)
    return lines


def validity(session: Session) -> list[str]:
    """The deep geometry check: the CAD's self-intersection (BOP) check on EVERY body, the ones of a customer's file
    included. Slow on a large file (the cheap check every result already reports skips those bodies)."""
    session.require_executor()
    valid, problems = session.validity(deep=True)
    verdict = "valid" if valid else "NOT valid - " + session.problems_text(problems)
    return [f"validity (deep check of every body): {verdict}"]


def run(session: Session, args: InspectArgs) -> tuple[list[str], int]:
    """Lines and the character limit that applies (lists may be longer than a fact sheet)."""
    handlers: dict[str, Any] = {
        "tree": lambda: tree(session, args.id),
        "faces": lambda: faces(session, args),
        "edges": lambda: edges(session, args),
        "face": lambda: face_detail(session, args),
        "edge": lambda: edge_detail(session, args),
        "properties": lambda: properties(session, args),
        "measure": lambda: measure(session),
        "bom": lambda: bom(session),
        "interference": lambda: interference(session),
        "sketch_status": lambda: sketch_status(session, args),
        "sweep": lambda: sweep(session, args),
        "holes": lambda: holes(session, args),
        "validity": lambda: validity(session),
        "draft": lambda: mould_tools.draft(session, args),
        "parting": lambda: mould_tools.parting(session, args),
    }
    limit = LIST_LIMIT if args.action in ("tree", "faces", "edges", "bom", "sweep", "holes", "draft", "parting") else 1500
    with session.lock:
        return handlers[args.action](), limit
