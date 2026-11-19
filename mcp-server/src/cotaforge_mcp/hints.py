"""Deterministic hints appended to the result of an op, from measured geometry only (never a block, never a guess).

Two:
* a ``boolean_subtract`` whose tool is a cylinder that pierces a planar face of the target square-on is a hole, and
  ``hole`` makes it as an editable feature that keeps its faces;
* a ``cut`` (or a ``chamfer``) that leaves a wider cylinder, or a wide cone, coaxial with a hole made earlier is the
  counterbore (or countersink) of that hole, which ``hole`` makes in ONE feature (``counterbore=`` / ``countersink=``).
A document marked as a sketch (a concept model) gets neither: there, primitives and booleans are the point.
"""

import json
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from cotaforge.contract import FaceInfo, FeatureRequest, ProvenanceRole, SurfaceType
from cotaforge.ir.ops_primitives import BooleanSubtractOperation

if TYPE_CHECKING:
    from cotaforge_mcp.session import Session

_TOL = 1e-3
_SQUARE_ON = 1.0 - 1e-6


def _dot(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def _sub(a: tuple[float, ...], b: tuple[float, ...]) -> tuple[float, ...]:
    return tuple(x - y for x, y in zip(a, b, strict=True))


def _num(value: float) -> float | int:
    value = round(value, 3) + 0.0
    return int(value) if value == int(value) else value


def _vec(values: tuple[float, ...]) -> list[float | int]:
    return [_num(v) for v in values]


@dataclass(frozen=True)
class _Tool:
    """A cylinder body: ``axis`` unit direction, ``origin`` a point on it, ``t_lo..t_hi`` its extent along the axis."""

    axis: tuple[float, ...]
    origin: tuple[float, ...]
    radius: float
    t_lo: float
    t_hi: float


def _cylinder(faces: tuple[FaceInfo, ...]) -> _Tool | None:
    """The tool as a plain cylinder (one cylindrical wall and two caps square to its axis), else ``None``."""
    walls = [f for f in faces if f.surface is SurfaceType.cylinder]
    caps = [f for f in faces if f.surface is SurfaceType.plane]
    if len(walls) != 1 or len(caps) != 2 or len(faces) != 3:
        return None
    wall = walls[0]
    if wall.axis is None or wall.radius is None:
        return None
    length = math.sqrt(_dot(wall.axis.direction, wall.axis.direction))
    if length < 1e-9:
        return None
    axis = tuple(c / length for c in wall.axis.direction)
    origin = tuple(wall.axis.origin)
    if any(c.normal is None or abs(_dot(c.normal, axis)) < _SQUARE_ON for c in caps):
        return None
    ts = sorted(_dot(_sub(tuple(c.center), origin), axis) for c in caps)
    return _Tool(axis, origin, wall.radius, ts[0], ts[1])


def _inside_face(face: FaceInfo, point: tuple[float, ...], tool: _Tool) -> bool:
    """The tool's circle on the plane of ``face`` lies inside the face's box (the exact box of that circle)."""
    for i in range(3):
        reach = tool.radius * math.sqrt(max(0.0, 1.0 - tool.axis[i] ** 2))
        if point[i] - reach < face.bbox.min[i] - _TOL or point[i] + reach > face.bbox.max[i] + _TOL:
            return False
    return True


def _drilled_face(tool: _Tool, faces: tuple[FaceInfo, ...]) -> tuple[FaceInfo, tuple[float, ...], bool, float] | None:
    """(the face the tool enters, the point of its axis on that face, whether it goes all the way through, how deep it
    goes into the material) when the tool goes square into the material from a planar face of the target."""
    # the faces whose normal is +Z come first, then +Y, then +X: a through tool is reported from the top
    planar = sorted(
        (f for f in faces if f.normal is not None),
        key=lambda f: (-f.normal[2], -f.normal[1], -f.normal[0], f.handle.id),  # type: ignore[index]
    )
    for face in planar:
        assert face.normal is not None
        alignment = _dot(face.normal, tool.axis)
        if abs(alignment) < _SQUARE_ON:
            continue
        sign = 1.0 if alignment > 0 else -1.0  # along the outward normal the material is at smaller tau
        t_face = _dot(_sub(tuple(face.center), tool.origin), tool.axis)
        plane = sign * t_face
        lo, hi = sorted((sign * tool.t_lo, sign * tool.t_hi))
        if not (hi >= plane - _TOL and lo < plane - _TOL):
            continue
        if not _inside_face(face, _axis_point(tool, t_face), tool):
            continue
        floors = []
        for other in planar:
            if other is face or other.normal is None or _dot(other.normal, face.normal) > -_SQUARE_ON:
                continue
            t_other = _dot(_sub(tuple(other.center), tool.origin), tool.axis)
            if sign * t_other < plane - _TOL and _inside_face(other, _axis_point(tool, t_other), tool):
                floors.append(sign * t_other)
        through = bool(floors) and lo <= max(floors) + _TOL
        return face, _axis_point(tool, t_face), through, plane - lo
    return None


def _axis_point(tool: _Tool, t: float) -> tuple[float, ...]:
    return tuple(o + a * t for o, a in zip(tool.origin, tool.axis, strict=True))


def drilled_holes(session: "Session", operation: Any) -> list[str]:
    """Hint lines for a ``boolean_subtract`` that is a hole; empty for anything else (and for a sketch document)."""
    if not isinstance(operation, BooleanSubtractOperation) or session.purpose == "sketch":
        return []
    try:
        return _drilled_holes(session, operation)
    except Exception:  # noqa: BLE001 - a hint must never fail the op it comes with
        return []


def _drilled_holes(session: "Session", operation: BooleanSubtractOperation) -> list[str]:
    executor = session.require_executor()
    target = executor.feature_for(operation.target.from_op)
    if target is None or operation.target.side is not None:
        return []
    adapter = session.adapter
    target_faces = adapter.faces_of(FeatureRequest(operation_id="hint", feature=target, role=ProvenanceRole.any)).faces
    found: list[tuple[str, _Tool, FaceInfo, tuple[float, ...], bool, float]] = []
    for ref in operation.tools:
        feature = executor.feature_for(ref.from_op)
        if feature is None or ref.side is not None:
            continue
        faces = adapter.faces_of(FeatureRequest(operation_id="hint", feature=feature, role=ProvenanceRole.any)).faces
        tool = _cylinder(faces)
        drilled = _drilled_face(tool, target_faces) if tool is not None else None
        if tool is not None and drilled is not None:
            found.append((ref.from_op, tool, *drilled))
    if not found:
        return []
    tool_id, tool, face, point, through, depth = found[0]
    assert face.normal is not None
    params: dict[str, Any] = {
        "face": {
            "kind": "face",
            "from_op": operation.target.from_op,
            "select": {"normal": [_num(c) for c in face.normal], "center": _vec(tuple(face.center))},
        },
        "at": _vec(point),
        "standard": "simple",
        "diameter_mm": _num(2 * tool.radius),
        "end_condition": "through_all" if through else "blind",
    }
    if not through:
        params["depth_mm"] = _num(depth)
    more = f" (+{len(found) - 1} more tools like it)" if len(found) > 1 else ""
    diameter = _num(2 * tool.radius)
    example = json.dumps(params, separators=(",", ":"))
    return [
        f"hint: this is a hole; use hole, it stays editable and keeps its faces: {tool_id} is a D{diameter} cylinder "
        f"normal to face {face.handle.id} of {operation.target.from_op}, going into it{more}. "
        f'Undo this step and run op(name="hole", params={example})',
        "concept model? doc(action=set_purpose, purpose=sketch) turns this hint off",
    ]


# ---- a counterbore or a countersink made of two steps ----------------------------------------------------------------

_COAXIAL_MM = 0.05
_COUNTERSINK_RATIO = 1.4
"""A cone must be this many times wider than the hole to read as a countersink (a 1 mm edge chamfer is not one)."""
_MAX_STEPS = 40
"""More hole and cut steps than this and the hint is not looked for (the reads cost more than the hint)."""
_WIDENING_OPS = ("cut", "chamfer", "revolve", "boolean_subtract")


@dataclass(frozen=True)
class _Round:
    """A cylindrical or conical face as seen from its axis: where it is, how wide and how long along it."""

    kind: str  # "cylinder" | "cone"
    origin: tuple[float, ...]
    axis: tuple[float, ...]
    radius: float
    length: float


def _round_of(face: FaceInfo) -> _Round | None:
    """The face as a ``_Round`` (``None`` for anything else, or for a cone with a slanted axis, whose width is not read
    from its box)."""
    if face.surface not in (SurfaceType.cylinder, SurfaceType.cone) or face.axis is None:
        return None
    norm = math.sqrt(_dot(face.axis.direction, face.axis.direction))
    if norm < 1e-9:
        return None
    axis = tuple(c / norm for c in face.axis.direction)
    size = tuple(hi - lo for lo, hi in zip(face.bbox.min, face.bbox.max, strict=True))
    along = sum(abs(a) * s for a, s in zip(axis, size, strict=True))
    if face.surface is SurfaceType.cylinder:
        if face.radius is None:
            return None
        return _Round("cylinder", tuple(face.axis.origin), axis, face.radius, along)
    if max(abs(a) for a in axis) < _SQUARE_ON:
        return None
    across = [s for a, s in zip(axis, size, strict=True) if abs(a) < 0.5]
    return _Round("cone", tuple(face.axis.origin), axis, max(across) / 2.0, along)


def _coaxial(a: _Round, b: _Round) -> bool:
    if abs(_dot(a.axis, b.axis)) < _SQUARE_ON:
        return False
    offset = _sub(b.origin, a.origin)
    along = _dot(offset, a.axis)
    off_axis = tuple(o - along * x for o, x in zip(offset, a.axis, strict=True))
    return math.sqrt(_dot(off_axis, off_axis)) <= _COAXIAL_MM


def _widens(wide: _Round, hole: _Round) -> bool:
    """``wide`` is a counterbore (a wider cylinder) or a countersink (a much wider cone) of the drilled ``hole``."""
    if not _coaxial(wide, hole):
        return False
    if wide.kind == "cylinder":
        return wide.radius > hole.radius + 0.01
    return wide.radius >= _COUNTERSINK_RATIO * hole.radius


def _created_rounds(session: "Session", feature: Any) -> list[_Round]:
    request = FeatureRequest(operation_id="hint", feature=feature, role=ProvenanceRole.created)
    return [r for r in (_round_of(f) for f in session.adapter.faces_of(request).faces) if r is not None]


def widened_holes(session: "Session", step_id: str, op_name: str) -> list[str]:
    """Hint lines for a step that, with a hole made before (or after) it, makes a counterbore or a countersink out of
    two steps; empty for anything else (and for a sketch document, and for an adapter that reads no faces)."""
    if op_name not in (*_WIDENING_OPS, "hole") or session.purpose == "sketch":
        return []
    try:
        return _widened_holes(session, step_id, op_name)
    except Exception:  # noqa: BLE001 - a hint must never fail the op it comes with
        return []


def _widened_holes(session: "Session", step_id: str, op_name: str) -> list[str]:
    steps = session.steps()
    mine = next((s for s in steps if s.id == step_id and s.feature is not None), None)
    others = [s for s in steps if s.id != step_id and s.feature is not None]
    if mine is None:
        return []
    drills = [s for s in others if s.op == "hole"]
    cuts = [s for s in others if s.op in _WIDENING_OPS]
    if len(drills) + len(cuts) > _MAX_STEPS or not (cuts if op_name == "hole" else drills):
        return []  # nothing to pair this step with (or too much to read)
    found: list[tuple[str, str, _Round, _Round]] = []  # (hole step, widening step, hole face, widening face)
    if op_name == "hole":  # a pocket made first, the hole drilled into it after
        holes_now = [r for r in _created_rounds(session, mine.feature) if r.kind == "cylinder"]
        for step in cuts:
            for wide in _created_rounds(session, step.feature):
                found.extend((step_id, step.id, hole, wide) for hole in holes_now if _widens(wide, hole))
    else:
        wides = _created_rounds(session, mine.feature)
        for step in drills:
            for hole in (r for r in _created_rounds(session, step.feature) if r.kind == "cylinder"):
                found.extend((step.id, step_id, hole, wide) for wide in wides if _widens(wide, hole))
    if not found:
        return []
    hole_step, wide_step, hole, wide = found[0]
    if wide.kind == "cylinder":
        word, key = "counterbore", "counterbore"
        value: dict[str, Any] = {"diameter_mm": _num(2 * wide.radius), "depth_mm": _num(wide.length)}
    else:
        word, key = "countersink", "countersink"
        value = {"diameter_mm": _num(2 * wide.radius), "angle_deg": 90}
    more = f" (+{len(found) - 1} more like it)" if len(found) > 1 else ""
    example = json.dumps({key: value}, separators=(",", ":"))
    return [
        f"hint: {wide_step} is a {word} of hole {hole_step} (D{_num(2 * hole.radius)}): a D{_num(2 * wide.radius)} "
        f"coaxial round{more}. hole makes it in ONE feature that stays editable and keeps its faces: undo {wide_step}, "
        f'then op(edit="{hole_step}", params={example}); on a new hole, put {key} in its params. '
        "Not the head of a screw? ignore this.",
        "concept model? doc(action=set_purpose, purpose=sketch) turns this hint off",
    ]
