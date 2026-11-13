"""An alert, measured on the geometry, for a milled pocket or slot whose vertical inside corners no end mill can make.

An end mill is round: the vertical inside corner of a pocket has at least the radius of the tool, and the tool must be
long enough to reach the depth (a usual stiffness limit is a depth of about 4 tool diameters). A pocket drawn with sharp
corners, or with a radius smaller than the smallest tool for its depth, cannot be milled as drawn: the shop rounds it,
or needs EDM, or the corner is relieved on purpose. This module looks for that and, when it finds it, activates the
base memory entry ``ENTRY_ID`` through the same line the roles and ops use (``memory: <id> - recognize: ...``) with what
it measured. It is an alert: nothing is ever refused and the reading never fails the op it comes with.

What it measures (only from neutral face and edge data, so every adapter gets it):

* a *pocket* is what one ``cut`` step made. Its cut axis is the direction that every planar wall of the step is
  perpendicular to; a planar face of the step facing along that axis is the *floor* (a blind pocket). A cut with no
  floor goes through the part;
* its *depth* is the extent of its walls along the axis;
* a corner is *sharp* when two walls of the pocket meet in a concave straight edge parallel to the axis; it is *round*
  when a partial cylinder parallel to the axis (not a whole circle: that is a hole) sits next to a wall or is one;
* the *smallest workable tool* has the radius ``depth / (2 * TOOL_DEPTH_RATIO)``. It fires when a corner has less.

It looks only when the project processes (declared as entries, or in the brief) include milling or machining, or when
none is declared; a cut through the part (the outline of a plate may be cut by laser or waterjet) is looked at only when
milling or machining is declared.

What it cannot see: a corner relieved on purpose (a dog-bone is a round corner of the size of its cutter, so it does not
fire), the round corners of an island left in the pocket (read as pocket corners), and whether the shop will make the
part by EDM when that was not declared.
"""

import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from cotaforge.contract import (
    CurveType,
    EdgeInfo,
    FaceInfo,
    FaceRequest,
    FeatureHandle,
    FeatureRequest,
    ProvenanceRole,
    SurfaceType,
)

from cotaforge_mcp import design_memory, rules

if TYPE_CHECKING:
    from cotaforge_mcp.session import Session

ENTRY_ID = "machining-inner-radius-and-cavity-depth"
TOOL_DEPTH_RATIO = 4.0
"""The deepest a tool is used, in tool diameters (the usual limit is 3 to 4): the smallest tool is depth / this."""
MIN_DEPTH_MM = 1.0
"""A pocket shallower than this is an engraving, not a pocket: nothing is said."""
MAX_STEPS = 60
MAX_FACE_READS = 40
"""More wall faces than this in one pocket and its edges are not read."""
MAX_LINES = 3

_PARALLEL = 1.0 - 1e-4  # |cos| of two directions that are the same line (about 0.8 degrees)
_PERPENDICULAR = 1e-3  # |cos| of two directions at a right angle
_WHOLE_CIRCLE = 0.9  # the share of a full cylinder's area from which a cylinder is a hole, not a corner
_TANGENT_DEG = 5.0  # a dihedral this close to 0 or 180 degrees is a smooth junction, not a corner

_MILLING = re.compile(r"mill|fres|machin|mecaniz|maquin|cnc", re.IGNORECASE)

# Operations that cannot change what is measured here: not worth reading the model after them.
_OPS_THAT_COUNT = frozenset({"cut", "fillet", "edit_feature", "delete_feature"})

Vec = tuple[float, float, float]


# ---- the geometry (pure) --------------------------------------------------------------------------------------------


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def _cross(a: Sequence[float], b: Sequence[float]) -> Vec:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _unit(v: Sequence[float]) -> Vec | None:
    norm = math.sqrt(_dot(v, v))
    return None if norm < 1e-9 else (v[0] / norm, v[1] / norm, v[2] / norm)


def _num(value: float) -> str:
    text = f"{round(value, 2):.2f}".rstrip("0").rstrip(".")
    return text or "0"


def _extent(face: FaceInfo, axis: Vec) -> float:
    """How far a face reaches along ``axis``: exact for an axis along a coordinate axis (the box of the face), a bound
    for a tilted one."""
    size = face.bbox.size()
    return sum(abs(axis[i]) * size[i] for i in range(3))


def _is_whole_circle(face: FaceInfo, axis: Vec) -> bool:
    length = _extent(face, axis)
    if not face.radius or length <= 1e-6:
        return False
    return face.area_mm2 >= _WHOLE_CIRCLE * 2.0 * math.pi * face.radius * length


def _cylinder_axis(face: FaceInfo) -> Vec | None:
    if face.surface is not SurfaceType.cylinder or face.axis is None or not face.radius or face.radius <= 0:
        return None
    return _unit(face.axis.direction)


def _plane_normal(face: FaceInfo) -> Vec | None:
    if face.surface is not SurfaceType.plane or face.normal is None:
        return None
    return _unit(face.normal)


def cut_axis(faces: Sequence[FaceInfo]) -> Vec | None:
    """The direction the cut went in: the one most planar walls are perpendicular to (and cylinders parallel to).
    ``None`` when the faces do not look like a pocket."""
    normals = [n for n in (_plane_normal(f) for f in faces) if n is not None]
    axes = [a for a in (_cylinder_axis(f) for f in faces) if a is not None]
    candidates: list[Vec] = [*normals, *axes]
    for i, first in enumerate(normals):
        for second in normals[i + 1 :]:
            product = _unit(_cross(first, second))
            if product is not None:
                candidates.append(product)
    best: tuple[int, Vec] | None = None
    for candidate in candidates:
        walls = [n for n in normals if abs(_dot(n, candidate)) <= _PERPENDICULAR]
        around = [a for a in axes if abs(_dot(a, candidate)) >= _PARALLEL]
        if not walls:
            continue
        score = len(walls) + len(around)
        if best is None or score > best[0]:
            best = (score, candidate)
    if best is None:
        return None
    axis = best[1]
    first = next(c for c in axis if abs(c) > 1e-9)
    return axis if first > 0 else (-axis[0], -axis[1], -axis[2])


@dataclass(frozen=True)
class Pocket:
    """A pocket with corners no tool can make."""

    step: str
    depth: float
    corners: int
    """How many vertical inside corners are smaller than the tool."""
    smallest: float
    """The smallest corner radius measured (0 for a sharp corner)."""
    tool: float
    """The radius of the smallest end mill that reaches ``depth``."""
    through: bool
    """The cut goes through the part (it has no floor)."""


def tool_radius(depth: float) -> float:
    return depth / (2.0 * TOOL_DEPTH_RATIO)


def read_pocket(
    step: str,
    made: Sequence[FaceInfo],
    universe: dict[str, FaceInfo],
    edges_of: Callable[[FaceInfo], Sequence[EdgeInfo]],
) -> Pocket | None:
    """The pocket made by ``step`` (``made`` are the faces it created) when it has a corner below the smallest tool.

    ``universe`` has every face read, by handle id, so that the round corner a later step made is found next to a wall;
    ``edges_of`` reads the edges of a face."""
    if len(made) > MAX_FACE_READS:  # before the axis: it compares every pair of faces (a faceted end has hundreds)
        return None
    axis = cut_axis(made)
    if axis is None:
        return None
    walls = [f for f in made if (n := _plane_normal(f)) is not None and abs(_dot(n, axis)) <= _PERPENDICULAR]
    through = not any((n := _plane_normal(f)) is not None and abs(_dot(n, axis)) >= _PARALLEL for f in made)
    if not walls or len(walls) > MAX_FACE_READS:
        return None
    depth = max(_extent(f, axis) for f in walls)
    if depth < MIN_DEPTH_MM:
        return None
    wall_ids = {f.handle.id for f in walls}
    radii: dict[str, float] = {}

    def corner(face: FaceInfo | None) -> None:
        if face is None or not face.radius:
            return
        face_axis = _cylinder_axis(face)
        if face_axis is None or abs(_dot(face_axis, axis)) < _PARALLEL or _is_whole_circle(face, axis):
            return
        radii[face.handle.id] = float(face.radius)

    for face in made:
        corner(face)
    sharp: set[str] = set()
    for wall in walls:
        for edge in edges_of(wall):
            others = [h.id for h in edge.faces if h.id != wall.handle.id]
            if edge.curve is CurveType.line and edge.convex is False and _along(edge, axis):
                if all(h.id in wall_ids for h in edge.faces) and not _tangent(edge):
                    sharp.add(edge.handle.id)
            else:
                for other in others:
                    corner(universe.get(other))
    tool = tool_radius(depth)
    small = [r for r in radii.values() if r < tool]
    count = len(sharp) + len(small)
    if count == 0:
        return None
    return Pocket(step, depth, count, 0.0 if sharp else min(small), tool, through)


def _along(edge: EdgeInfo, axis: Vec) -> bool:
    if edge.start is not None and edge.end is not None:
        direction = _unit((edge.end[0] - edge.start[0], edge.end[1] - edge.start[1], edge.end[2] - edge.start[2]))
    elif edge.axis is not None:
        direction = _unit(edge.axis.direction)
    else:
        direction = None
    return direction is not None and abs(_dot(direction, axis)) >= _PARALLEL


def _tangent(edge: EdgeInfo) -> bool:
    angle = edge.dihedral_deg
    return angle is not None and (angle < _TANGENT_DEG or angle > 180.0 - _TANGENT_DEG)


def line_for(pockets: list[Pocket]) -> str:
    """The ``memory:`` line for the pockets of one reading."""
    first = pockets[0]
    names = " and ".join(f"{p.step} ({_num(p.depth)} mm deep)" for p in pockets)
    if first.smallest <= 0:
        corners = f"{first.corners} vertical inside corner{'' if first.corners == 1 else 's'} with radius 0"
    else:
        corners = f"vertical inside corners down to radius {_num(first.smallest)} mm"
    return (
        f"memory: {ENTRY_ID} — recognize: {names}: {corners}; "
        f"the smallest end mill that reaches {_num(first.depth)} mm has radius {_num(first.tool)} mm "
        f"(depth up to {_num(TOOL_DEPTH_RATIO)} tool diameters): give the corners a radius a little larger, "
        "relieve them, or change the process"
    )


# ---- reading the model --------------------------------------------------------------------------------------------


def declared_words(session: "Session") -> list[str]:
    """What the project says about its processes: the declared process entries and the processes of the brief."""
    declared = rules.declared_set(session.settings)
    words = [f"{p.tag} {p.title}" for p in declared.processes]
    return [*words, *design_memory.load_brief(session.settings).processes]


def process_allows(words: list[str], *, through: bool) -> bool:
    """Milling or machining is among the processes, or none is declared (and then only a blind pocket counts)."""
    if not words:
        return not through
    return any(_MILLING.search(word) for word in words)


def alerts(session: "Session", op: str) -> list[str]:
    """The lines to add to the result of ``op``: empty when nothing is found, when the document is a concept model, or
    when anything goes wrong. A line is said once per server session."""
    if session.purpose == "sketch" or session.executor is None or op not in _OPS_THAT_COUNT:
        return []
    try:
        lines = []
        for line in _lines(session):
            if line not in session.mem.pushed:
                session.mem.pushed.add(line)
                lines.append(line)
        return lines[:MAX_LINES]
    except Exception:  # noqa: BLE001 - an alert must never fail the op it comes with
        return []


def current_lines(session: "Session") -> list[str]:
    """Every line that fires on the model now (measured again, none left out, none remembered): what the gate holds. A
    concept model, or a reading that fails, has none."""
    if session.purpose == "sketch" or session.executor is None:
        return []
    try:
        return _lines(session)
    except Exception:  # noqa: BLE001 - a reading must never break the call it comes with
        return []


def _lines(session: "Session") -> list[str]:
    steps = [s for s in session.steps() if s.feature is not None and s.op in ("cut", "fillet")]
    cuts = [s.id for s in steps if s.op == "cut"]
    if not cuts or len(steps) > MAX_STEPS:
        return []
    if not any(e.id == ENTRY_ID for e in design_memory.active_entries(session.settings)):
        return []  # the engineer replaced or switched it off
    words = declared_words(session)
    if words and not any(_MILLING.search(word) for word in words):
        return []
    universe: dict[str, FaceInfo] = {}
    made: dict[str, list[FaceInfo]] = {}
    for step in steps:
        feature: FeatureHandle = step.feature  # type: ignore[assignment]
        request = FeatureRequest(operation_id="pocket_corners", feature=feature, role=ProvenanceRole.any)
        for face in session.adapter.faces_of(request).faces:
            universe[face.handle.id] = face
            if step.op == "cut" and face.provenance.created_by.id == feature.id:
                made.setdefault(step.id, []).append(face)

    def edges_of(face: FaceInfo) -> Sequence[EdgeInfo]:
        return session.adapter.edges_of(FaceRequest(operation_id="pocket_corners", face=face.handle)).edges

    pockets = []
    for step_id in cuts:
        pocket = read_pocket(step_id, made.get(step_id, []), universe, edges_of)
        if pocket is not None and process_allows(words, through=pocket.through):
            pockets.append(pocket)
    return [line_for([pocket]) for pocket in pockets]


def after_op(session: "Session", step: str, op: str) -> list[str]:  # noqa: ARG001 - same shape as its siblings
    """After a step that worked."""
    return alerts(session, op)


def after_action(session: "Session", op: str) -> list[str]:
    """After an edit or a delete of a feature."""
    return alerts(session, op)
