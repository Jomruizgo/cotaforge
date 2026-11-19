"""The operation catalog as the agent sees it: names, one-line purposes and per-operation references.

Everything structural (names, fields, types, defaults, constraints) is read from the kernel's pydantic models, so
adding an operation there makes it appear here without code. Only prose lives in this file (purposes, field notes,
minimal examples), and the tests fail when an operation has no reference, no valid example or no term mapping.

Kernel concepts that mean nothing to the agent are not shown: the intent links of an operation (``links``), the
plan-level validation stamp and plan templates never reach the ``op`` surface (``HIDDEN_FIELDS``); ``intent`` is a plain
sentence the server records.
"""

import difflib
import json
import types
import typing
from collections.abc import Callable, Mapping
from enum import Enum
from typing import Any, Literal, get_args, get_origin

from cotaforge.contract import Capabilities, OperationSpec
from cotaforge.executor import DISPATCH
from cotaforge.ir import OP_CATEGORY, Operation, OpId
from pydantic import BaseModel, RootModel, TypeAdapter
from pydantic.fields import FieldInfo

HIDDEN_FIELDS = frozenset({"id", "op", "intent", "links"})

CAD_NOTE = "<cad-note>"
"""Marks, inside a note, where the sentence true of the active CAD only goes: ``op_reference`` fills it from the
adapter's ``guide_topic("note:<op>")`` (and a field's from ``note:<op>.<field>``), or leaves it out."""

Extensions = Mapping[str, OperationSpec]
"""The operations the adapter declared (``Adapter.extra_operations``), by the name the agent writes. In every function
that takes them an adapter's operation takes the place of the common one of its name; without them (``None``) the
answers are the ones of the common vocabulary alone."""

Notes = Callable[[str], str | None]
"""What an adapter answers for a ``note:...`` key: the text of that CAD's sentence, or ``None`` for none."""

PURPOSE: dict[str, str] = {
    "sketch": "2D profiles (rect, square, circle, slot, lines, arcs) on a base plane, a datum plane or a planar face",
    "ref_plane": "reference plane (offset, angle, midplane, normal to a curve, three points) to sketch on",
    "ref_axis": "reference axis (two points, point + direction, edge/entity, plane intersection)",
    "extrude": "pad the closed profiles of a sketch along its normal (adds material)",
    "cut": "pocket the closed profiles of a sketch (removes material, blind or through)",
    "revolve": "revolve the profiles of a sketch about an axis (adds material)",
    "sweep": "sweep the profile of a sketch along a path (adds material)",
    "hole": "drill a hole into a planar face at a world point (at) or a (u, v) position; ONE call also makes the "
    "counterbore, the countersink, the tapped hole and the blind or angled-point end",
    "linear_pattern": "repeat features along one or two directions",
    "circular_pattern": "repeat features about an axis",
    "chamfer": "bevel the given edges",
    "fillet": "round the given edges",
    "shell": "hollow a body to a wall thickness, removing the chosen faces",
    "draft": "taper faces by a draft angle about a neutral plane",
    "mirror": "mirror features about a plane inside their body",
    "mirror_body": "mirror a whole body about a plane into a new body",
    "shrink_body": "derive a LINKED, scaled copy of a body for a plastic's shrinkage (a mould cavity); the shrinkage and its source are mandatory",
    "mould_split": "split the shrunk part's negative into the mould's cavity and core along the parting surface (and the slides its undercuts need); see inspect(action=parting) first",
    "mould_component": "build a standard mould component from the DME catalogue (a 2-plate base kit, guide pin and bush, locating ring, sprue bush, ejector and return pins, angle pin, wear plate) as native parametric bodies carrying their catalogue reference; refs and sizes are listed in the guide",
    "slide_mechanism": "add the carrier, T-gibs, angle pin, locking heel, ball detent and wear plate of a slide made by mould_split; refused unless the stroke clears the undercut, the heel is steeper than the pin and nothing interferes closed or open",
    "split": "split a body with a plane into two",
    "copy_body": "copy a body, optionally moved",
    "base_flange": "start a sheet-metal part: a sketch outline (holes inside it) becomes a flat sheet of a thickness",
    "edge_flange": "bend a wall (flange) out of a straight edge of the sheet: angle, bend radius, length",
    "unfold": "flat pattern of the sheet (K-factor required): measured flat size; export dxf flat=<this step>",
    "box": "auxiliary rectangular body: reference, boolean tool or concept-model block (features: sketch + extrude)",
    "cylinder": "auxiliary cylindrical body: reference, boolean tool or concept-model block (holes: hole)",
    "boolean_union": "fuse bodies",
    "boolean_subtract": "subtract tool bodies from a target body",
    "boolean_intersect": "keep the common volume of bodies",
    "translate": "move a body by an offset",
    "rotate": "rotate a body about an axis",
    "insert_component": "insert an instance of a saved part document into the assembly at a placement",
    "fix_component": "fix a component in place",
    "place_component": "move an instance to a placement (origin + rotation) in the assembly",
    "add_joint": "join a component to another (or to the ground): fixed, revolute about an axis or slider along one",
    "set_joint": "move a joint to a value (degrees of a revolute, mm of a slider): the joined component follows",
    "mate_concentric": "mate two cylindrical faces concentrically",
    "mate_coincident": "mate two planar faces coincident",
    "mate_distance": "mate two planar faces at a distance",
    "check_interference": "measure interference between components",
}

_FACE_TOP = {"kind": "face", "from_op": "s2", "select": {"normal": [0, 0, 1]}}
_FACE_SIDE = {"kind": "face", "from_op": "s2", "select": {"normal": [1, 0, 0]}}
_BODY = {"kind": "body", "from_op": "s1"}

_PLANE_XY = {"kind": "plane", "name": "XY"}
_PLANE_YZ = {"kind": "plane", "name": "YZ"}

EXAMPLES: dict[str, dict[str, Any]] = {
    "sketch": {
        "on": _PLANE_XY,
        "entities": [{"kind": "rect", "name": "outline", "center": [0, 0], "width": 40, "height": 30}],
    },
    "ref_plane": {"define": {"how": "offset", "base": _PLANE_XY, "distance_mm": 20}},
    "ref_axis": {"define": {"how": "two_points", "points": [[0, 0, 0], [0, 0, 10]]}},
    "extrude": {"sketch": "s1", "depth_mm": 10},
    "cut": {"sketch": "s3", "end_condition": "through_all"},
    "revolve": {"sketch": "s1", "axis": "v"},
    "sweep": {"sketch": "s1", "path": {"kind": "polyline", "points": [[0, 0, 0], [0, 0, 50], [30, 0, 50]]}},
    "hole": {
        "face": _FACE_TOP,
        "at": [0, 0, 10],
        "standard": "simple",
        "diameter_mm": 6,
        "end_condition": "through_all",
    },
    "linear_pattern": {"features": ["s4"], "direction": "X", "spacing_mm": 15, "count": 3},
    "circular_pattern": {"features": ["s4"], "axis": {"origin": [0, 0, 0], "direction": [0, 0, 1]}, "count": 4},
    "chamfer": {"edges": [{"kind": "edge", "between": [_FACE_TOP, _FACE_SIDE]}], "distance1_mm": 1},
    "fillet": {"edges": [{"kind": "edge", "between": [_FACE_TOP, _FACE_SIDE]}], "radius_mm": 2},
    "shell": {"faces": [_FACE_TOP], "thickness_mm": 2},
    "draft": {"faces": [_FACE_SIDE], "neutral": _PLANE_XY, "pull": "Z", "angle_deg": 3},
    "mirror": {"features": ["s3"], "plane": _PLANE_YZ},
    "mirror_body": {"source": _BODY, "plane": _PLANE_YZ},
    "shrink_body": {"source": _BODY, "shrinkage_percent": 0.6, "factor_source": "resin datasheet"},
    "mould_split": {"source": _BODY, "pull": "Z", "zero_draft_to": "cavity", "slides": "all"},
    "mould_component": {"kind": "mould_base", "size": "1012", "at": [{"origin": [0, 0, 30], "axis": "Z"}]},
    "slide_mechanism": {"split": "inserts", "slide": 1, "part": _BODY, "pull": "Z", "angle_deg": 15},
    "split": {"source": _BODY, "plane": _PLANE_XY, "keep": "both"},
    "copy_body": {"source": _BODY, "offset": [50, 0, 0]},
    "base_flange": {"sketch": "s1", "thickness_mm": 2},
    "edge_flange": {
        "edge": {"kind": "edge", "between": [_FACE_TOP, _FACE_SIDE]},
        "length_mm": 20,
        "angle_deg": 90,
        "bend_radius_mm": 3,
    },
    "unfold": {"face": {"kind": "face", "from_op": "s2", "select": {"normal": [0, 0, -1]}}, "k_factor": 0.4},
    "box": {"size": [40, 30, 10]},
    "cylinder": {"diameter_mm": 10, "height_mm": 20},
    "boolean_union": {"target": _BODY, "tools": [{"kind": "body", "from_op": "s2"}]},
    "boolean_subtract": {"target": _BODY, "tools": [{"kind": "body", "from_op": "s2"}]},
    "boolean_intersect": {"target": _BODY, "tools": [{"kind": "body", "from_op": "s2"}]},
    "translate": {"target": _BODY, "offset": [10, 0, 0]},
    "rotate": {"target": _BODY, "axis": "Z", "angle_deg": 90},
    "insert_component": {"part": "base_part", "placement": {"origin": [0, 0, 0]}},
    "fix_component": {"component": "s1"},
    "place_component": {"component": "s1", "placement": {"origin": [30, 0, 0]}},
    "add_joint": {
        "type": "revolute",
        "a": "s1",
        "b": "s2",
        "axis": {"origin": [0, 0, 10], "direction": [0, -1, 0]},
        "min": 0,
        "max": 120,
    },
    "set_joint": {"joint": "s3", "value": 30},
    "mate_concentric": {"a": _FACE_TOP, "b": _FACE_SIDE},
    "mate_coincident": {"a": _FACE_TOP, "b": _FACE_SIDE},
    "mate_distance": {"a": _FACE_TOP, "b": _FACE_SIDE, "distance_mm": 5},
    "check_interference": {},
}
"""Minimal parameters per operation, written against the conventional ids s1 (sketch) / s2 (solid): the tests fail when
one stops validating against the IR model or when an operation has none."""

FIELD_NOTES: dict[tuple[str, str], str] = {
    ("sketch", "on"): "plane XY|YZ|XZ, a datum plane (ref_plane step) or a planar face ref; "
    "planes: XY u=X v=Y, YZ u=Y v=Z, XZ u=X v=-Z",
    ("sketch", "entities"): "closed profiles only (mm): rect/square/circle/slot, or lines and arcs chained end to end. "
    "rect: optional chamfer_mm cuts its four corners at 45 degrees (< half the shorter side). "
    "arc3: start, a point ON the arc, end; arc_ends: centre, start, end (ccw=false goes clockwise); "
    "arc: centre, radius, ccw from start_deg to end_deg; slot: start/end = centres of the round ends, width = diameter",
    ("sketch", "named_dimensions"): "names a dimension of an entity (attribute: width|height|side|diameter|radius|"
    "length|chamfer|u|v) so op(edit=<sketch id>, params={dimensions:{<name>: value}}) can change it later",
    ("extrude", "sketch"): "id of an earlier sketch step",
    ("extrude", "merge"): "false makes a separate body instead of fusing with the one it touches",
    ("cut", "depth_mm"): "required when end_condition is blind",
    ("cut", "direction"): "negative = into the material for a sketch on a face",
    ("revolve", "axis"): "u|v (sketch axes) or the name of a line entity of the sketch; profile on ONE side of it",
    ("hole", "position"): "(u, v) mm in the face frame, not world coordinates, measured from the centre of the face's "
    "BOUNDING BOX (it does not move when holes are drilled in the face; the centre of mass does). On an L-shaped or "
    "notched face that point can lie outside the material: it is only the reference. Or give at",
    ("hole", "positions"): "several holes in ONE call and one feature: a list of (u, v), each as position (e.g. the 4 "
    "corners of a rectangular pattern); no two closer than one diameter; not with up_to_face",
    ("hole", "bolt_circle"): "holes equally spaced on a circle in ONE feature: {diameter_mm (pitch circle), count, "
    "center [u, v] (default [0, 0]), start_angle_deg (from +u towards +v, default 0)}",
    ("hole", "at"): "world point [x, y, z] mm instead of position: projected onto the face, refused (by how many mm) "
    "when outside it",
    ("hole", "size"): "required for iso_clearance / iso_tapped, e.g. M6",
    ("hole", "depth_mm"): "required when end_condition is blind (from the entry face, counterbore included); none "
    "for up_to_face",
    ("hole", "end_condition"): "up_to_face ends at the plane of the face named in up_to_face",
    ("hole", "up_to_face"): "a flat face NORMAL to the hole axis and ahead of the entry (e.g. the floor of a "
    "counterbore you drill into from the other side)",
    ("hole", "counterbore"): "wider cylinder at the entry. M8 socket head in ONE call: standard=iso_clearance, "
    "size=M8, counterbore={diameter_mm:14, depth_mm:9}",
    ("hole", "countersink"): "cone at the entry: diameter_mm at the entry face, angle_deg included (default 90); "
    "not with counterbore",
    ("hole", "drill_point"): "angled (default) adds a cone of drill_point_angle_deg beyond depth_mm; "
    "flat for a flat bottom; through_all has none",
    ("linear_pattern", "features"): "ids of earlier extrude/cut/revolve/hole steps",
    ("linear_pattern", "direction"): "X|Y|Z or a vector",
    ("circular_pattern", "axis"): "{origin, direction} or a cylindrical face/edge ref",
    ("fillet", "edges"): "each item: ONE edge (between two face refs, or of a face + curve) or an edge SET "
    '{kind:"edges", from_op:<step of the body>, where:{...}} that resolves to every edge matching all its criteria',
    ("chamfer", "edges"): 'same as fillet; inspect(action="edges", op=<step>, where={...}) previews a set',
    ("fillet", "skip_failing"): "true: when the CAD cannot round some edges, apply the others and report the skipped",
    (
        "chamfer",
        "skip_failing",
    ): "true: when the CAD cannot chamfer some edges, apply the others and report the skipped",
    ("base_flange", "sketch"): "id of an earlier sketch step: its largest closed profile is the outline, the others "
    "(inside it) are holes",
    ("base_flange", "direction"): "positive = the sheet lies on the side the sketch normal points to",
    ("edge_flange", "edge"): "an edge between a big face of the sheet and its thickness face (between=[big, side]); "
    "the wall rises on the side of the big face: the top edge bends up, the bottom edge down",
    ("edge_flange", "length_mm"): "to the outer virtual sharp (length_from=outer_sharp) or the straight leg (leg)",
    ("edge_flange", "angle_deg"): "bend angle: 90 = wall perpendicular to the sheet",
    ("edge_flange", "bend_radius_mm"): "INNER radius of the bend (the shop's tooling value)",
    ("edge_flange", "position"): "material_inside keeps the wall's outer face in the plane of the sheet edge "
    "(the bend eats into the sheet); material_outside leaves the sheet whole and bends beyond its edge",
    ("unfold", "face"): "a big flat face of the sheet; it stays fixed in the flat pattern",
    ("unfold", "k_factor"): "REQUIRED, ANSI: the neutral line sits k x thickness from the inside of the bend (0..1, "
    "usually 0.3-0.5). It depends on material, thickness, radius and tooling: ask the engineer or the shop, state "
    "the value you assume in intent",
}

EDIT_NOTES: dict[str, str] = {
    "sketch": "; or params={dimensions:{<name or entity.attribute>: mm}} (a rect/square resizes about its centre)",
    "hole": "; a new size re-derives the diameter of an iso hole",
    "chamfer": "; an edge set is resolved again on the part as it stands (the steps are rebuilt)",
    "fillet": "; an edge set is resolved again on the part as it stands (the steps are rebuilt)",
}

EDGE_SET_NOTE = (
    'Edge set = {kind:"edges", from_op:<step>, where:{...}}: every edge of that step\'s body that meets ALL of where '
    "(parallel_to X|Y|Z|vector; length_mm n|{min,max}; convex true|false; planar true = both faces flat; "
    "curve line|circle|arc; on_face <face ref> = its contour; exclude_holes true = not the walls of holes; "
    "except [point|edge ref] = drop the edge through that point). Resolved when the step runs and reported (count + "
    'one line each); inspect(action="edges", op=<step>, where={...}) shows it first. '
    'Machine chamfer on the corners of a plate profile: where={parallel_to:"z",convex:true} (the edges along the '
    'thickness). 1x45 on the straight edges between flat faces, not the holes: where={curve:"line",planar:true}. '
    "If the CAD cannot do some edges the step fails NAMING them; skip_failing=true applies the others."
)

NOTES: dict[str, str] = {
    "insert_component": "part = the NAME of another open part document, already saved; the assembly is saved with "
    "its first insert if it has no file yet. Each call makes one instance (insert twice for two); the step id names "
    'it for place_component / fix_component / check_interference. cad_guide(topic="assembly") has the workflow.',
    "place_component": "component = the insert_component step; placement is ABSOLUTE (it replaces the pose, it does "
    "not add to it). Moves the instance only: the part document is untouched.",
    "add_joint": "b = the insert_component step the joint moves; a = the one it hangs from (omit: the ground). Made "
    "at the pose b has now = value 0, so place both first; the limits must include 0. axis (revolute: turns about "
    "it, positive = right-handed about its direction; slider: moves along its direction) is {origin, direction}, "
    "a cylindrical face or circular edge ref, a linear edge ref (start to end) or a ref_axis step. Joined "
    "components are no longer placed by hand (place_component / mates refuse them): use set_joint."
    f'{CAD_NOTE} cad_guide(topic="assembly") has the workflow.',
    "set_joint": "joint = the add_joint step; value is ABSOLUTE from the pose the joint was made at, inside its "
    'limits. To check clearances along the way use inspect(action="sweep"), not a hand-written loop.',
    "sketch": "A sketch is its own step: extrude/cut/revolve/sweep it by its id; faces come from the step that "
    "made them. Points are (u, v) in the plane's frame (base planes: u/v are world axes); on a face or datum plane "
    'add frame="world" (a param of sketch, not stored) to give [x, y, z] mm points instead: they are mapped into the '
    "plane, refused (by how many mm) when off it; rect width runs along u, height along v.",
    "chamfer": EDGE_SET_NOTE,
    "fillet": EDGE_SET_NOTE,
    "hole": "face = {kind:face, from_op:<step id of the solid>, select:{normal|center|area_mm2|largest...}}. "
    "Counterbore, countersink, angled point and up_to_face are done by the op: never a raw script. A hole, cut or "
    "boss is verified against the real boolean of its tool with the part as it is (so a hole that breaks into an "
    "existing cavity is accepted); the reply says what was compared.",
    "ref_plane": "A reference plane is a step of its own (no solid): sketch on it with on={kind:plane, from_op:<id>}.",
    "sweep": "path = polyline of world points; the profile sketch plane must be normal to the first segment, through "
    "the first point, with the profile centroid on it.",
    "base_flange": "Holes and cut-outs go in this sketch as extra closed profiles (circle, rect, line loops); a sheet "
    "takes no cut/hole/fillet step afterwards. Faces of the sheet come from this step (from_op).",
    "edge_flange": "Take the edge from the sheet step (from_op) and bend each side once; the part keeps ONE body. "
    "Measured: volume and box of the bent part.",
    "unfold": "Makes the flat pattern OUTSIDE the solid: mass, box and validity keep describing the bent part. "
    "The reply states the flat size (longer x shorter side), thickness and blank area. "
    'export(action="dxf", flat="<this step id>") writes the outline and holes for the cutting shop (no bend lines).',
}


# --- structure ------------------------------------------------------------------------------------------------------


def op_models(extensions: Extensions | None = None) -> dict[str, type[BaseModel]]:
    """Operation name -> pydantic model, read from the IR's discriminated union; an operation the adapter declared
    replaces the common one of its name."""
    union = get_args(Operation)[0]
    models: dict[str, type[BaseModel]] = {}
    for model in get_args(union):
        models[str(model.model_fields["op"].default)] = model
    for name, spec in (extensions or {}).items():
        models[name] = spec.params
    return models


OPERATION_ADAPTER: TypeAdapter = TypeAdapter(Operation)


def op_names(extensions: Extensions | None = None) -> list[str]:
    return list(op_models(extensions))


def adapter_method(op_name: str) -> str | None:
    entry = DISPATCH.get(op_name)
    return entry[0] if entry else None


def available_ops(capabilities: Capabilities, extensions: Extensions | None = None) -> list[str]:
    """Operations whose adapter method this CAD supports (everything else is invisible to the agent), plus the ones the
    adapter declared (one that shares a name with a common operation takes its place and its group)."""
    rank = {"everyday": 0, "multibody": 1, "sheet_metal": 2, "assembly": 3}
    own = extensions or {}
    names = [n for n in op_models() if n not in own and adapter_method(n) in capabilities.tools]
    names.extend(own)
    return sorted(names, key=lambda n: rank.get(OP_CATEGORY.get(n, ""), 4))


def unavailable_ops(reasons: dict[str, str]) -> dict[str, str]:
    """Operations whose adapter tool the CAD could offer but cannot right now (``Adapter.unavailable_tools``): op name
    -> why and how to get it."""
    return {name: reasons[tool] for name in op_models() if (tool := adapter_method(name)) in reasons}


def close_names(name: str, candidates: list[str], extra: list[str] | None = None) -> list[str]:
    matches = difflib.get_close_matches(name.strip().lower(), candidates, n=4, cutoff=0.5)
    for alias in extra or []:
        if alias not in matches:
            matches.append(alias)
    return matches


# --- type rendering -------------------------------------------------------------------------------------------------


def _unwrap(annotation: Any) -> Any:
    while get_origin(annotation) is typing.Annotated:
        annotation = get_args(annotation)[0]
    return annotation


def _is_step_id(annotation: Any) -> bool:
    if get_origin(annotation) is typing.Annotated:
        return any(getattr(m, "pattern", None) for m in _all_metadata(annotation))
    return False


def _all_metadata(annotation: Any) -> list[Any]:
    out: list[Any] = []
    for meta in get_args(annotation)[1:]:
        if isinstance(meta, FieldInfo):
            out.extend(meta.metadata)
        else:
            out.append(meta)
    return out


_SCALARS = {float: "number", int: "int", str: "string", bool: "bool"}


def describe_type(annotation: Any, nested: dict[str, type[BaseModel]]) -> str:
    """Compact type text; pydantic models met on the way are collected in ``nested`` and shown by name."""
    if annotation is OpId:
        return "step-id"
    step = _is_step_id(annotation)
    inner = _unwrap(annotation)
    origin = get_origin(inner)
    if step and inner is str:
        return "step-id"
    if inner in _SCALARS:
        return _SCALARS[inner]
    if inner is type(None):
        return "null"
    if origin is Literal:
        return "|".join(json.dumps(v) for v in get_args(inner))
    if isinstance(inner, type) and issubclass(inner, Enum):
        return "|".join(json.dumps(m.value) for m in inner)
    if isinstance(inner, type) and issubclass(inner, RootModel):  # an id written as a bare string (a step of the plan)
        kind = {"SketchStep": "sketch step-id", "FeatureStep": "feature step-id"}.get(inner.__name__)
        return kind or describe_type(inner.model_fields["root"].rebuild_annotation(), nested)
    if isinstance(inner, type) and issubclass(inner, BaseModel):
        nested.setdefault(inner.__name__, inner)
        return inner.__name__
    if origin in (typing.Union, types.UnionType):
        parts = [describe_type(a, nested) for a in get_args(inner) if a is not type(None)]
        return " | ".join(dict.fromkeys(parts))
    if origin is tuple:
        args = get_args(inner)
        if len(args) == 2 and args[1] is Ellipsis:
            return f"[{describe_type(args[0], nested)}, ...]"
        texts = [describe_type(a, nested) for a in args]
        if len(set(texts)) == 1:
            return f"[{texts[0]} x{len(texts)}]"
        return "[" + ", ".join(texts) + "]"
    if origin in (list, set, frozenset):
        return f"[{describe_type(get_args(inner)[0], nested)}, ...]"
    if origin is dict or inner is dict:
        return "object"
    return getattr(inner, "__name__", str(inner))


def _constraints(field: FieldInfo) -> str:
    bits = []
    for meta in field.metadata:
        for attr, label in (("gt", ">"), ("ge", ">="), ("lt", "<"), ("le", "<="), ("min_length", "min_len ")):
            value = getattr(meta, attr, None)
            if value is not None:
                bits.append(f"{label}{value}")
    return " ".join(bits)


def _default_text(field: FieldInfo) -> str | None:
    if field.is_required():
        return None
    default = field.get_default(call_default_factory=True)  # a default_factory (dict, list) has no .default
    if isinstance(default, Enum):
        default = default.value
    if isinstance(default, BaseModel):
        return "default: no filter/identity"
    if default is None:
        return "optional"
    if isinstance(default, tuple | list) and not default:
        return "default []"
    if isinstance(default, dict) and not default:
        return "default {}"
    return f"default {json.dumps(default)}"


def _field_line(
    model_name: str, name: str, field: FieldInfo, nested: dict[str, type[BaseModel]], notes: Notes | None = None
) -> str:
    kind = describe_type(field.rebuild_annotation(), nested)
    note = FIELD_NOTES.get((model_name, name), "")
    if note and notes is not None:
        note += notes(f"note:{model_name}.{name}") or ""
    extras = " ".join(x for x in (_constraints(field), _default_text(field) or "REQUIRED") if x)
    line = f"  {field.alias or name}: {kind}  [{extras}]"
    return f"{line}  {note}" if note else line


def _model_fields(model: type[BaseModel], hidden: frozenset[str] = frozenset()) -> list[tuple[str, FieldInfo]]:
    return [(n, f) for n, f in model.model_fields.items() if n not in hidden]


def model_summary(model: type[BaseModel], nested: dict[str, type[BaseModel]]) -> str:
    parts = []
    for name, field in _model_fields(model, HIDDEN_FIELDS):
        kind = describe_type(field.rebuild_annotation(), nested)
        name = field.alias or name  # noqa: PLW2901 - the name the author writes (``except``, not ``except_``)
        if field.is_required():
            parts.append(f"{name}: {kind}")
        else:
            default = _default_text(field)
            parts.append(f"{name}?: {kind}" + (f" ({default})" if default and default != "optional" else ""))
    return f"{model.__name__}{{" + "; ".join(parts) + "}"


# --- references -------------------------------------------------------------------------------------------------------


def purpose(name: str, extensions: Extensions | None = None) -> str:
    """One line: the adapter's own, else the curated purpose, else the first line of the model's docstring."""
    if extensions and name in extensions:
        spec = extensions[name]
        return f"{spec.purpose} (read-only)" if spec.read_only else spec.purpose
    if name in PURPOSE:
        return PURPOSE[name]
    doc = (op_models()[name].__doc__ or "").strip().splitlines()
    return doc[0] if doc else ""


def example_params(name: str, extensions: Extensions | None = None) -> dict[str, Any] | None:
    if extensions and name in extensions:
        return extensions[name].example
    return EXAMPLES.get(name)


def example_call(name: str, extensions: Extensions | None = None) -> str:
    params = example_params(name, extensions)
    if params is None:
        return f'op(name="{name}", params={{...}})'
    return f'op(name="{name}", params={json.dumps(params, separators=(",", ":"))})'


def about(name: str, limit: int = 700, extensions: Extensions | None = None) -> str:
    """The model's own docstring (what the operation really does), collapsed to one paragraph."""
    doc = " ".join((op_models(extensions)[name].__doc__ or "").split())
    if len(doc) > limit:
        doc = doc[: limit - 1].rsplit(" ", 1)[0] + "..."
    return doc


def op_reference(name: str, notes: Notes | None = None, extensions: Extensions | None = None) -> str:
    """The reference of one op. ``notes`` is the adapter's ``guide_topic``: it adds what is true of that CAD only.
    An operation the adapter declared has no curated notes of the common one it may replace: its own guide text says
    all of it, and it cannot be changed with ``edit``."""
    if extensions and name in extensions:
        return _extension_reference(extensions[name])
    model = op_models()[name]
    nested: dict[str, type[BaseModel]] = {}
    lines = [f"{name}: {purpose(name)}"]
    detail = about(name)
    if detail:
        lines.append(f"about: {detail}")
    lines.append("params:")
    for field_name, field in _model_fields(model, HIDDEN_FIELDS):
        lines.append(_field_line(name, field_name, field, nested, notes))
    lines.extend(_type_lines(nested))
    if name in NOTES:
        own = (notes(f"note:{name}") if notes is not None else None) or ""
        lines.append(f"note: {NOTES[name].replace(CAD_NOTE, own)}")
    lines.append(
        "also: id (step id, default s<N>), intent (why, one line); change it later with "
        f'op(edit="<id>", params={{only the fields that change}}){EDIT_NOTES.get(name, "")}'
    )
    lines.append(f"example: {example_call(name)}")
    return "\n".join(lines)


def _type_lines(nested: dict[str, type[BaseModel]]) -> list[str]:
    """The ``types:`` block: every model met in the fields, and the ones those use, one summary line each."""
    seen: set[str] = set()
    type_lines: list[str] = []
    while nested:
        type_name, type_model = next(iter(nested.items()))
        del nested[type_name]
        if type_name in seen:
            continue
        seen.add(type_name)
        type_lines.append("  " + model_summary(type_model, nested))
    return ["types:", *type_lines] if type_lines else []


def _extension_reference(spec: OperationSpec) -> str:
    """The reference of an operation the adapter declared: its model's fields as they are, its own guide text, and the
    way to change it (``edit`` on its id; the plan keeps it as the adapter's operation)."""
    nested: dict[str, type[BaseModel]] = {}
    lines = [f"{spec.name}: {purpose(spec.name, {spec.name: spec})}"]
    detail = " ".join((spec.params.__doc__ or "").split())
    if detail:
        lines.append(f"about: {detail if len(detail) <= 700 else detail[:699].rsplit(' ', 1)[0] + '...'}")
    lines.append("params:")
    for field_name, field in _model_fields(spec.params, HIDDEN_FIELDS):
        lines.append(_field_line(f"ext:{spec.name}", field_name, field, nested))
    lines.extend(_type_lines(nested))
    if spec.guide.strip():
        lines.append(f"note: {' '.join(spec.guide.split())}")
    if spec.read_only:
        lines.append("read-only: answers now and changes nothing; it is not a step (no id, not in the plan, no undo)")
    else:
        lines.append(
            "also: id (step id, default s<N>), intent (why, one line); to change it, op(edit=<id>, params={...}) with "
            "only the fields to change (a dimension is set in place; else the steps from it on are rebuilt)"
        )
    lines.append(f"example: {example_call(spec.name, {spec.name: spec})}")
    return "\n".join(lines)


def op_usage(name: str, extensions: Extensions | None = None) -> str:
    """The right shape in one line (for validation errors)."""
    model = op_models(extensions)[name]
    nested: dict[str, type[BaseModel]] = {}
    parts = []
    for field_name, field in _model_fields(model, HIDDEN_FIELDS):
        kind = describe_type(field.rebuild_annotation(), nested)
        parts.append(f"{field_name}{'' if field.is_required() else '?'}: {kind}")
    return f'op(name="{name}", params={{{"; ".join(parts)}}})'


def ops_table(names: list[str], extensions: Extensions | None = None) -> str:
    models = op_models(extensions)
    rows = []
    for name in names:
        required = [n for n, f in _model_fields(models[name], HIDDEN_FIELDS) if f.is_required()]
        rows.append(f"{name}({', '.join(required)}): {purpose(name, extensions)}")
    return "\n".join(rows)
