"""An alert, measured on the geometry, for a bearing that sits on a toleranced length much longer than itself.

A machine designer steps the shaft: only the seat of each bearing is a close fit, the rest of the shaft is smaller, and
the shoulder that is left locates the bearing. A shaft of one diameter over a long run with bearings on it forces each
bearing to be pushed along a long fit to reach its seat. This module looks for that and, when it finds it, activates the
base memory entry ``ENTRY_ID`` through the same line the roles and ops use (``memory: <id> - recognize: ...``)
with what it measured. It is an alert: nothing is ever refused and the reading never fails the op it comes with.

What it measures (only from neutral face data, so every adapter gets it):

* a *bearing* is a step whose role is ``comercial`` (or whose id / part name reads like a bearing) and whose body
  is a ring: a cylindrical bore with a coaxial, larger cylinder around it. Its width is the length of the bore
  (the inner ring);
* the *run* is the continuous length of cylindrical faces of another body that are coaxial with the bore and of the
  bore's diameter (within a small tolerance), seen from the bearing outwards. Faces of the same body are merged:
  a CAD that splits a cylinder in two halves reads the same;
* it fires when the run is more than ``LONG_RUN_FACTOR`` times the bearing width.

What it cannot see: a bearing drawn as a solid disc (no bore), a seat that is not cylindrical, a shaft that is not
modelled, whether a ring is a withdrawal sleeve (the entry's ``not_when``), and whether the shaft is meant to be ground
over its whole length. The declared fit of a shaft face does not exist in the model (a shaft fit is declared on the
drawing), so it is not read here.
"""

import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from cotaforge.contract import FaceInfo, FeatureHandle, FeatureRequest, ProvenanceRole, SurfaceType

from cotaforge_mcp import design_memory

if TYPE_CHECKING:
    from cotaforge_mcp.session import Session

ENTRY_ID = "transmission-bearing-seat-only-over-its-width"
LONG_RUN_FACTOR = 2.0
"""A run longer than this many bearing widths fires: the seat (its width plus chamfers and an undercut) fits in it."""
MAX_FEATURES = 80
"""More features than this and the alert is not looked for (a big assembly: the reads cost more than the alert)."""
MAX_LINES = 3

_PARALLEL = 1.0 - 1e-4  # |cos| of two axes that are the same direction (about 0.8 degrees)
_APART_MM = 0.2  # two axes closer than this are one axis
_GAP_MM = 0.5  # two cylindrical faces of one diameter closer than this are one run
_RING_RATIO = 1.15  # the outside of a ring is at least this many times its bore
_MIN_SHARE = 0.5  # the share of a bearing's width a run must cover to be its seat
_ALIGNED = 1.0 - 1e-6

# A bearing by what the engineer or the agent called it: the word, or a catalogue designation of a radial series.
_BEARING_NAME = re.compile(
    r"rodamient|bearing|cojinete|balero|ruleman|roulement"
    r"|(?<![0-9a-z])(?:6[0-4]\d\d|16\d{3}|3[02]\d{3}|n[ujn]p?\d{3,4})(?![0-9])",
    re.IGNORECASE,
)

# Operations that cannot change what is measured here: not worth reading the model after them.
_QUIET_OPS = frozenset(
    {
        "sketch",
        "ref_plane",
        "ref_axis",
        "hole",
        "chamfer",
        "fillet",
        "draft",
        "add_joint",
        "set_joint",
        "check_interference",
    }
)


# ---- the geometry (pure) --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Cylinder:
    """A cylindrical face as a stretch of an axis: ``centre`` is its middle along the axis, ``half`` half its length."""

    owner: str
    axis: tuple[float, float, float]
    centre: tuple[float, float, float]
    radius: float
    half: float


def _dot(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def _sub(a: tuple[float, ...], b: tuple[float, ...]) -> tuple[float, float, float]:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _num(value: float) -> str:
    text = f"{round(value, 2):.2f}".rstrip("0").rstrip(".")
    return text or "0"


def cylinder_of(face: FaceInfo, owner: str) -> Cylinder | None:
    """The face as a ``Cylinder`` (``None`` for anything that is not a cylinder with an axis and a radius)."""
    if face.surface is not SurfaceType.cylinder or face.axis is None or not face.radius or face.radius <= 0:
        return None
    direction = tuple(face.axis.direction)
    norm = math.sqrt(_dot(direction, direction))
    if norm < 1e-9:
        return None
    axis = tuple(c / norm for c in direction)
    first = next(c for c in axis if abs(c) > 1e-9)
    if first < 0:  # an axis has no sense: all faces of one line read the same way
        axis = tuple(-c for c in axis)
    size = face.bbox.size()
    aligned = [i for i in range(3) if abs(axis[i]) >= _ALIGNED]
    # along a coordinate axis the box of the face is its length (also for half a cylinder); else a whole one is assumed
    length = size[aligned[0]] if aligned else face.area_mm2 / (2.0 * math.pi * face.radius)
    if length <= 1e-6:
        return None
    return Cylinder(owner, axis, tuple(face.center), float(face.radius), length / 2.0)  # type: ignore[arg-type]


def coaxial(a: Cylinder, b: Cylinder) -> bool:
    """The same axis line: the same direction and the centre of one on the line of the other."""
    if abs(_dot(a.axis, b.axis)) < _PARALLEL:
        return False
    offset = _sub(b.centre, a.centre)
    along = _dot(offset, a.axis)
    off_line = math.sqrt(max(0.0, _dot(offset, offset) - along * along))
    return off_line <= _APART_MM


def _span(c: Cylinder, reference: Cylinder) -> tuple[float, float]:
    """The stretch of ``c`` along the axis of ``reference``, from the centre of ``reference``."""
    middle = _dot(_sub(c.centre, reference.centre), reference.axis)
    return middle - c.half, middle + c.half


def _overlap(a: tuple[float, float], b: tuple[float, float]) -> float:
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


def bore_of(cylinders: list[Cylinder]) -> Cylinder | None:
    """The bore of a ring body: its smallest cylinder with a coaxial, larger one around it over most of its length."""
    for bore in sorted(cylinders, key=lambda c: c.radius):
        reach = _span(bore, bore)
        for outside in cylinders:
            if (
                outside is not bore
                and outside.radius >= bore.radius * _RING_RATIO
                and coaxial(bore, outside)
                and _overlap(reach, _span(outside, bore)) >= _MIN_SHARE * 2.0 * bore.half
            ):
                return bore
    return None


def _radius_tolerance(radius: float) -> float:
    return 0.1 + 0.005 * radius


def _runs(candidates: list[Cylinder], bore: Cylinder) -> list[tuple[float, float, float]]:
    """``(start, end, radius)`` along the bore's axis of every continuous run of coaxial faces of its diameter."""
    spans = sorted(
        (*_span(c, bore), c.radius)
        for c in candidates
        if coaxial(bore, c) and abs(c.radius - bore.radius) <= _radius_tolerance(bore.radius)
    )
    runs: list[list[float]] = []
    for start, end, radius in spans:
        if runs and start <= runs[-1][1] + _GAP_MM:
            runs[-1][1] = max(runs[-1][1], end)
            runs[-1][2] = max(runs[-1][2], radius)
        else:
            runs.append([start, end, radius])
    return [(a, b, r) for a, b, r in runs]


@dataclass(frozen=True)
class Seat:
    """A bearing found on a run longer than it should be."""

    bearing: str
    shaft: str
    diameter: float
    run: float
    width: float


def long_seats(cylinders: dict[str, list[Cylinder]], bearings: dict[str, str]) -> list[Seat]:
    """The bearings whose seat runs on over more than ``LONG_RUN_FACTOR`` times their width.

    ``cylinders`` are the cylindrical faces by body (owner id); ``bearings`` maps the owners that are bearings to their
    label. A bearing is looked for on the faces of every other owner that is not itself a bearing."""
    found: list[Seat] = []
    for owner, label in bearings.items():
        bore = bore_of(cylinders.get(owner, []))
        if bore is None:
            continue
        width = 2.0 * bore.half
        mine = _span(bore, bore)
        for other, candidates in cylinders.items():
            if other == owner or other in bearings:
                continue
            for start, end, radius in _runs(candidates, bore):
                if _overlap(mine, (start, end)) < _MIN_SHARE * width:
                    continue
                if end - start > LONG_RUN_FACTOR * width:
                    found.append(Seat(label, other, 2.0 * radius, end - start, width))
    return found


# ---- reading the model --------------------------------------------------------------------------------------------


def _owner(face: FaceInfo) -> str:
    return face.component.id if face.component is not None else face.body.id


def _bearing_steps(session: "Session", roles: dict[str, str]) -> set[str]:
    """Steps that are a bearing by role or by name (the id, or the part an inserted component is an instance of)."""
    named = {}
    executor = session.executor
    if executor is not None:
        for operation in executor.plan.operations:
            named[operation.id] = f"{operation.id} {getattr(operation, 'part', '')}"
    return {step for step, role in roles.items() if role == "comercial"} | {
        step for step, text in named.items() if _BEARING_NAME.search(text)
    }


def _readings(
    session: "Session", features: dict[str, FeatureHandle]
) -> tuple[dict[str, list[FaceInfo]], dict[str, str]]:
    """The faces of every feature by owner (a body, or a whole component), and the first step that made each owner."""
    faces: dict[str, dict[str, FaceInfo]] = {}
    label: dict[str, str] = {}
    for step, feature in features.items():
        request = FeatureRequest(operation_id="bearing_seats", feature=feature, role=ProvenanceRole.any)
        for face in session.adapter.faces_of(request).faces:
            owner = _owner(face)
            label.setdefault(owner, step)
            faces.setdefault(owner, {})[face.handle.id] = face
    return {owner: list(by_id.values()) for owner, by_id in faces.items()}, label


def line_for(seats: list[Seat]) -> str:
    """The ``memory:`` line for the bearings that sit on one run."""
    first = seats[0]
    names = " and ".join(f"{s.bearing} (width {_num(s.width)} mm)" for s in seats)
    return (
        f"memory: {ENTRY_ID} — recognize: {names} sit{'' if len(seats) > 1 else 's'} on {first.shaft}, which measures "
        f"{_num(first.run)} mm in a row of Ø{_num(first.diameter)} (more than {_num(LONG_RUN_FACTOR)} times the width): "
        "the fit goes only on the seat and the rest of the shaft is smaller"
    )


def alerts(session: "Session", op: str, step: str | None = None, role: str = "") -> list[str]:
    """The lines to add to the result of ``op`` (the step ``step`` it made, with the role ``role``): empty when nothing
    long is found, when there is no bearing, when the document is a concept model, or when anything goes wrong."""
    if session.purpose == "sketch" or session.executor is None:
        return []
    try:
        return _alerts(session, op, step, role)
    except Exception:  # noqa: BLE001 - an alert must never fail the op it comes with
        return []


def _alerts(session: "Session", op: str, step: str | None, role: str) -> list[str]:
    roles = design_memory.effective_roles(session)
    if step is not None and role:
        roles = {**roles, step: role}
    bearing_steps = _bearing_steps(session, roles)
    if not bearing_steps or (op in _QUIET_OPS and step not in bearing_steps):
        return []
    entry = next((e for e in design_memory.active_entries(session.settings) if e.id == ENTRY_ID), None)
    if entry is None:  # the engineer replaced or switched it off
        return []
    features: dict[str, FeatureHandle] = {s.id: s.feature for s in session.steps() if s.feature is not None}
    for alias, feature in session.aliases.items():
        features.setdefault(alias, feature)
    if not features or len(features) > MAX_FEATURES:
        return []
    faces, label = _readings(session, features)
    bearings = {owner: first for owner, first in label.items() if first in bearing_steps}
    cylinders: dict[str, list[Cylinder]] = {}
    for owner, owner_faces in faces.items():
        found = [c for c in (cylinder_of(f, owner) for f in owner_faces) if c is not None]
        if found:
            cylinders[owner] = found
    seats = [
        Seat(label.get(s.bearing, s.bearing), label.get(s.shaft, s.shaft), s.diameter, s.run, s.width)
        for s in long_seats(cylinders, {owner: owner for owner in bearings})
    ]
    by_run: dict[tuple[str, float, float], list[Seat]] = {}
    for seat in seats:
        by_run.setdefault((seat.shaft, round(seat.diameter, 2), round(seat.run, 1)), []).append(seat)
    lines = []
    for group in by_run.values():
        line = line_for(group)
        if line not in session.mem.pushed:
            session.mem.pushed.add(line)
            lines.append(line)
    return lines[:MAX_LINES]


def after_op(session: "Session", step: str, op: str, role: str) -> list[str]:
    """After a step that worked."""
    return alerts(session, op, step, role)


def after_action(session: "Session", op: str) -> list[str]:
    """After an edit or a delete of a feature."""
    return alerts(session, op)
