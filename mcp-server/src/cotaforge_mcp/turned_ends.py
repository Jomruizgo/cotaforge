"""An alert, measured on the geometry, for a turned part whose free end has a sharp outer edge.

A turned part (a shaft, a pin, a bushing) that ends in a bare 90 degree edge has no lead-in for assembly, cuts a hand
and carries a burr the shop has to break anyway. A chamfer or a radius at the end is a design decision, not a drawing
one. A threaded end needs it too (the first thread must start). This module looks for that and, when it finds it,
activates the base memory entry ``ENTRY_ID`` through the same line the roles and ops use (``memory: <id> - recognize:
...``) with what it measured, one line per end. It is an alert: nothing is ever refused here and the reading never fails
the op it comes with; it closes under the rule of ``measured_alerts``.

What counts as a *free end* (narrow on purpose: no alert is better than a false one):

* the part is *turned*: one body whose faces are, for at least ``TURNED_SHARE`` of their area, cylinders coaxial with
  its main axis, planes perpendicular to it, or cones / tori / spheres of revolution. The main axis runs along a
  coordinate axis and the part is at least as long as its largest diameter (a disc or a washer is not a shaft);
* the free ends are only the TWO end faces at the extremes of the part along that axis. A shoulder is not an end;
* an end is *sharp* when the outer boundary circle of its end face meets a coaxial cylinder of the same radius at a
  convex edge of about 90 degrees: with a chamfer or a radius there, the neighbour is a cone or a torus and nothing
  fires. The size of the chamfer is not judged. The edge of a bore is not looked at;
* ends under ``MIN_DIAMETER_MM`` and bought parts (role ``comercial``) are left alone.

It looks only when the project processes include turning or machining, or none is declared.

What it cannot see: an end that must stay sharp on purpose (a seal seat, a press-in edge), a part turned from a profile
the adapter reads as something other than cylinders, and a chamfer the drawing gives as a note (the model is what is
measured).
"""

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

from cotaforge_mcp import bearing_seats, design_memory, pocket_corners

if TYPE_CHECKING:
    from cotaforge_mcp.session import Session

ENTRY_ID = "machining-turned-end-chamfer"
TURNED_SHARE = 0.8
"""The share of the surface of a body that must be of revolution about one axis to call it a turned part."""
MIN_DIAMETER_MM = 4.0
"""A free end of a smaller diameter is a point or a wire: nothing is said."""
MAX_FEATURES = 80
MAX_LINES = 3

_ALIGNED = 1.0 - 1e-6  # |component| of an axis that runs along a coordinate axis
_AT_END_MM = 0.05  # a face this close to the extreme of the part is at its end
_SAME_RADIUS_MM = 0.05
_RIGHT_ANGLE_DEG = 10.0  # a dihedral within this of 90 degrees is a right angle

_TURNING = re.compile(r"turn|torne|torno|lathe|machin|mecaniz|maquin|cnc", re.IGNORECASE)

# Operations that cannot change what is measured here: not worth reading the model after them.
_QUIET_OPS = frozenset(
    {"sketch", "ref_plane", "ref_axis", "hole", "draft", "add_joint", "set_joint", "check_interference"}
)

_LETTERS = "XYZ"


@dataclass(frozen=True)
class SharpEnd:
    """A free end of a turned part with a sharp outer edge."""

    part: str
    diameter: float
    axis: int
    """The coordinate axis the part is turned about (0, 1, 2)."""
    position: float
    """Where the end face is along that axis."""


# ---- the geometry (pure) --------------------------------------------------------------------------------------------


def _num(value: float) -> str:
    text = f"{round(value, 2):.2f}".rstrip("0").rstrip(".")
    return text or "0"


def _is_revolved(face: FaceInfo, axis: int) -> bool:
    """The face is of revolution about the coordinate axis ``axis`` (a cone, torus or sphere with no axis counts)."""
    if face.surface is SurfaceType.cylinder:
        cylinder = bearing_seats.cylinder_of(face, "")
        return cylinder is not None and abs(cylinder.axis[axis]) >= _ALIGNED
    if face.surface is SurfaceType.plane:
        return face.normal is not None and abs(face.normal[axis]) >= _ALIGNED
    if face.surface in (SurfaceType.cone, SurfaceType.torus, SurfaceType.sphere):
        return face.axis is None or abs(face.axis.direction[axis]) >= _ALIGNED
    return False


def main_axis(faces: Sequence[FaceInfo]) -> int | None:
    """The coordinate axis a turned body is turned about: the one with the most area of coaxial cylinders, when the body
    is a turned part (see the module). ``None`` for anything else."""
    total = sum(f.area_mm2 for f in faces)
    if total <= 0:
        return None
    best: tuple[float, int] | None = None
    for axis in range(3):
        area = sum(
            f.area_mm2
            for f in faces
            if f.surface is SurfaceType.cylinder
            and (c := bearing_seats.cylinder_of(f, ""))
            and c.axis[axis] >= _ALIGNED
        )
        if area > 0 and (best is None or area > best[0]):
            best = (area, axis)
    if best is None:
        return None
    axis = best[1]
    if sum(f.area_mm2 for f in faces if _is_revolved(f, axis)) < TURNED_SHARE * total:
        return None
    return axis


def _is_coaxial_cylinder(face: FaceInfo | None, axis: int, radius: float) -> bool:
    if face is None:
        return False
    cylinder = bearing_seats.cylinder_of(face, "")
    return (
        cylinder is not None
        and abs(cylinder.axis[axis]) >= _ALIGNED
        and abs(cylinder.radius - radius) <= _SAME_RADIUS_MM
    )


def sharp_outer_edge(
    plane: FaceInfo, edges: Sequence[EdgeInfo], universe: dict[str, FaceInfo], axis: int
) -> float | None:
    """The radius of the outer boundary of the end face ``plane`` when it meets the cylinder at a bare right angle (no
    chamfer, no radius); ``None`` when it does not, or when it cannot be read."""
    circles = [e for e in edges if e.curve is CurveType.circle and e.radius]
    if not circles:
        return None
    outer = max(circles, key=lambda e: float(e.radius or 0.0))
    radius = float(outer.radius or 0.0)
    if outer.convex is False:
        return None
    if outer.dihedral_deg is not None and abs(outer.dihedral_deg - 90.0) > _RIGHT_ANGLE_DEG:
        return None
    others = [h.id for h in outer.faces if h.id != plane.handle.id]
    if len(others) != 1 or not _is_coaxial_cylinder(universe.get(others[0]), axis, radius):
        return None
    return radius


def free_ends(
    part: str, faces: Sequence[FaceInfo], edges_of: Callable[[FaceInfo], Sequence[EdgeInfo]]
) -> list[SharpEnd]:
    """The free ends of the turned body made of ``faces`` that have a sharp outer edge."""
    axis = main_axis(faces)
    if axis is None:
        return []
    low = min(f.bbox.min[axis] for f in faces)
    high = max(f.bbox.max[axis] for f in faces)
    diameters = [
        2.0 * c.radius for f in faces if f.surface is SurfaceType.cylinder and (c := bearing_seats.cylinder_of(f, ""))
    ]
    if not diameters or high - low < max(diameters):
        return []  # a disc, a washer or a flange: not a shaft
    universe = {f.handle.id: f for f in faces}
    found = []
    for face in faces:
        if face.surface is not SurfaceType.plane or face.normal is None or abs(face.normal[axis]) < _ALIGNED:
            continue
        position = face.center[axis]
        if min(abs(position - low), abs(position - high)) > _AT_END_MM:
            continue
        radius = sharp_outer_edge(face, edges_of(face), universe, axis)
        if radius is not None and 2.0 * radius >= MIN_DIAMETER_MM:
            found.append(SharpEnd(part, 2.0 * radius, axis, position))
    return sorted(found, key=lambda e: e.position)


def line_for(end: SharpEnd) -> str:
    """The ``memory:`` line for one end. Nothing in it changes unless the end does (so an order of the engineer for it
    stays valid)."""
    where = f"{_LETTERS[end.axis]}={_num(end.position)} mm"
    return (
        f"memory: {ENTRY_ID} — recognize: {end.part}: the free end at {where} (turned about {_LETTERS[end.axis]}) "
        "has a "
        f"sharp outer edge on Ø{_num(end.diameter)} mm, with no chamfer or radius: give the end a chamfer "
        "(also a threaded end) or a radius, or the engineer decides to leave it sharp"
    )


# ---- reading the model --------------------------------------------------------------------------------------------


def alerts(session: "Session", op: str, step: str | None = None, role: str = "") -> list[str]:
    """The lines to add to the result of ``op``: empty when nothing is found, when the document is a concept model, or
    when anything goes wrong. A line is said once per server session."""
    if session.purpose == "sketch" or session.executor is None or op in _QUIET_OPS:
        return []
    try:
        lines = []
        for line in _lines(session, step, role):
            if line not in session.mem.pushed:
                session.mem.pushed.add(line)
                lines.append(line)
        return lines[:MAX_LINES]
    except Exception:  # noqa: BLE001 - an alert must never fail the op it comes with
        return []


def current_lines(session: "Session") -> list[str]:
    """Every line that fires on the model now (measured again, none left out, none remembered): what the gate holds."""
    if session.purpose == "sketch" or session.executor is None:
        return []
    try:
        return _lines(session, None, "")
    except Exception:  # noqa: BLE001 - a reading must never break the call it comes with
        return []


def _owner(face: FaceInfo) -> str:
    return face.component.id if face.component is not None else face.body.id


def _lines(session: "Session", step: str | None, role: str) -> list[str]:
    if not any(e.id == ENTRY_ID for e in design_memory.active_entries(session.settings)):
        return []  # the engineer replaced or switched it off
    words = pocket_corners.declared_words(session)
    if words and not any(_TURNING.search(word) for word in words):
        return []
    features: dict[str, FeatureHandle] = {s.id: s.feature for s in session.steps() if s.feature is not None}
    if not features or len(features) > MAX_FEATURES:
        return []
    roles = design_memory.effective_roles(session)
    if step is not None and role:
        roles = {**roles, step: role}
    faces: dict[str, dict[str, FaceInfo]] = {}
    label: dict[str, str] = {}
    for step_id, feature in features.items():
        request = FeatureRequest(operation_id="turned_ends", feature=feature, role=ProvenanceRole.any)
        for face in session.adapter.faces_of(request).faces:
            owner = _owner(face)
            label.setdefault(owner, step_id)
            faces.setdefault(owner, {})[face.handle.id] = face

    def edges_of(face: FaceInfo) -> Sequence[EdgeInfo]:
        return session.adapter.edges_of(FaceRequest(operation_id="turned_ends", face=face.handle)).edges

    lines: list[str] = []
    for owner, by_id in faces.items():
        if roles.get(label[owner]) == "comercial":
            continue
        lines += [line_for(end) for end in free_ends(label[owner], list(by_id.values()), edges_of)]
    return lines


def after_op(session: "Session", step: str, op: str, role: str) -> list[str]:
    """After a step that worked."""
    return alerts(session, op, step, role)


def after_action(session: "Session", op: str) -> list[str]:
    """After an edit or a delete of a feature."""
    return alerts(session, op)
