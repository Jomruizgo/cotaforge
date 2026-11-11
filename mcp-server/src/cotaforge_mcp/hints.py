"""Deterministic hints appended to the result of an op, from measured geometry only (never a block, never a guess).

Today one: a ``boolean_subtract`` whose tool is a cylinder that pierces a planar face of the target square-on is a hole,
and ``hole`` makes it as an editable feature that keeps its faces. A document marked as a sketch (a concept model) does
not get it: there, primitives and booleans are the point.
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
