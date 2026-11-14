# ruff: noqa: E501
"""A turned part goes on the sheet the way it is machined: its axis horizontal, so the thread end or the chuck end can be
read the shop's way (no FreeCAD import, unit-tested on its own).

TechDraw's front view looks along -y with z up, so a shaft modelled along z would be drawn standing on its end. The harness
turns the anchor view of such a part instead: the view's x direction is the axis and the part's own side stays towards the
viewer (the front view turned a quarter of a turn, not another face). The views the projection group derives from it (the
end view, which looks along the axis) follow, and in first angle the end view stands beside the main one.

The end of the part with a thread goes to the right (the working end, away from the chuck at the left); a part with no
thread keeps the positive end of its axis on the right.
"""

import math
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Any

import sheet_keyway

Vec3 = tuple[float, float, float]

# axis index -> (direction towards the viewer, x direction) of the turned front view, for the positive end on the right.
# ``up = direction x xdir`` (the page's up in the model): x axis: z up; y axis: -x up; z axis: -x up.
_FRAMES: dict[int, tuple[Vec3, Vec3]] = {
    0: ((0.0, -1.0, 0.0), (1.0, 0.0, 0.0)),
    1: ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),
    2: ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)),
}


def axis_index(axis: Vec3) -> int:
    """The coordinate axis (0, 1, 2) a unit ``axis`` lies along."""
    return max(range(3), key=lambda i: abs(axis[i]))


def frame(axis: Vec3, flip: bool = False) -> tuple[Vec3, Vec3]:
    """``(direction, xdirection)`` of the front view of a part turned about ``axis``: the axis runs along the page's x. With
    ``flip`` its negative end is on the right (the view is seen from the other side, so nothing is mirrored)."""
    direction, xdirection = _FRAMES[axis_index(axis)]
    if flip:
        direction = (-direction[0], -direction[1], -direction[2])
        xdirection = (-xdirection[0], -xdirection[1], -xdirection[2])
    return direction, xdirection


def iso_frame(axis: Vec3, flip: bool = False) -> tuple[Vec3, Vec3]:
    """``(direction, xdirection)`` of the isometric view of a turned part: the standard one (looking from the front, the right
    and above) seen in the frame of the turned main view, so the axis runs along the page the way it does in the main view
    instead of standing up."""
    d, r = frame(axis, flip)
    u = (d[1] * r[2] - d[2] * r[1], d[2] * r[0] - d[0] * r[2], d[0] * r[1] - d[1] * r[0])
    direction = (r[0] + d[0] + u[0], r[1] + d[1] + u[1], r[2] + d[2] + u[2])
    xdirection = (r[0] - d[0], r[1] - d[1], r[2] - d[2])
    return direction, xdirection


def thread_end_flip(axis: Vec3, part_range: tuple[float, float], threads: list[float]) -> bool:
    """Whether the view is flipped so the end with a thread is on the right. ``part_range`` is the part's extent along the
    axis (low, high, model coordinates), ``threads`` the coordinates along the axis of the middle of each external thread.
    Threads at both ends, or none: not flipped."""
    if not threads:
        return False
    middle = (part_range[0] + part_range[1]) / 2
    high = sum(1 for t in threads if t > middle)
    low = sum(1 for t in threads if t < middle)
    return low > 0 and high == 0


def layout_sizes(length: float, diameter: float) -> tuple[float, float, float]:
    """The ``(w, d, h)`` the layout arranges: the length across the sheet, the diameter up and (the end view's width) the
    diameter again."""
    return length, diameter, diameter


def views_for(keyway: bool) -> list[str]:
    """The views a solid turned part is drawn in when nobody says: the main view alone, which carries the diameters, the
    lengths, the thread and the chamfers. A keyway is NOT a reason for an end view: it is shown in a removed section across
    it, on the extension of its cutting line, that carries its width and depth (``keyway_dims``). A part with a bore is the
    section's: see ``drawing._views_for_part``."""
    return ["front"]


# --- the part whose every feature is coaxial: ONE axial section says it all ---------------------------------------------

AXIS_TOL_MM = 0.05  # a surface of revolution whose axis is this far from the part's own is not coaxial with it
ALIGNED = 1.0 - 1e-6  # |component| of a unit vector along the axis to call it parallel to it


@dataclass(frozen=True)
class FaceFact:
    """What the coaxial test needs of one face of the part (read from the shape by the CAD side, plain data here).

    ``kind``: ``plane``, ``cylinder``, ``cone``, ``torus``, ``sphere`` or ``other``. ``axis`` and ``point``: the axis of a
    surface of revolution and one point of it (the centre of a sphere); ``normal``: the normal of a plane. ``concave``: the
    face looks at the axis of the part (a bore, a recess), measured with the outward normal of the solid."""

    kind: str
    axis: Vec3 | None = None
    point: Vec3 | None = None
    normal: Vec3 | None = None
    concave: bool = False


def _off_axis(point: Vec3, axis: Vec3, through: Vec3) -> float:
    """The distance (mm) of ``point`` from the line through ``through`` along the unit vector ``axis``."""
    d = (point[0] - through[0], point[1] - through[1], point[2] - through[2])
    along = d[0] * axis[0] + d[1] * axis[1] + d[2] * axis[2]
    r = (d[0] - along * axis[0], d[1] - along * axis[1], d[2] - along * axis[2])
    return (r[0] ** 2 + r[1] ** 2 + r[2] ** 2) ** 0.5


def coaxial_facts(faces: list[FaceFact], axis: Vec3, through: Vec3) -> tuple[bool, bool]:
    """``(coaxial, hollow)`` of a part of revolution about the unit ``axis`` through the point ``through``: ``coaxial`` when
    EVERY face is a plane square to the axis or a surface of revolution about that same line (a hole on a pitch circle, a
    flat, a keyway, a cross hole, a square or hex flange all make it False; a face the test cannot read does too);
    ``hollow`` when some face looks at the axis (a bore or a recess: there is something inside to cut)."""
    for face in faces:
        if face.kind == "plane":
            if face.normal is None or abs(sum(n * a for n, a in zip(face.normal, axis, strict=True))) < ALIGNED:
                return False, False
        elif face.kind in ("cylinder", "cone", "torus"):
            if face.axis is None or face.point is None:
                return False, False
            if abs(sum(n * a for n, a in zip(face.axis, axis, strict=True))) < ALIGNED:
                return False, False
            if _off_axis(face.point, axis, through) > AXIS_TOL_MM:
                return False, False
        elif face.kind == "sphere":
            if face.point is None or _off_axis(face.point, axis, through) > AXIS_TOL_MM:
                return False, False
        else:
            return False, False
    return True, any(f.concave for f in faces if f.kind != "plane")


def section_only(coaxial: bool, hollow: bool) -> bool:
    """A part of revolution with only coaxial features and something inside (a washer, a bush, a stepped ring, a hollow
    spacer) is fully defined by ONE axial section plus the isometric: the section carries every Ø and every height, the end
    view only repeats circles. A solid one has no section to cut (its plain outside view says everything), and a part with
    an off-axis feature keeps the end view that shows it."""
    return coaxial and hollow


def hidden_views(items: list[dict[str, object]]) -> set[str]:
    """The standard views a sheet does not show because a section stands in their place (``in_place``): the part is drawn
    cut in the main view and the outside view under it would only draw lines across the bore."""
    return {
        str(i.get("view") or "front")
        for i in items
        if i.get("type") == "section" and i.get("in_place")
    }


# --- lengths counted from ONE datum face: parallel (baseline) dimensions ------------------------------------------------------

STEP_TOL_MM = 0.05  # two faces this near along the axis are one position
SQUARE = 1.0 - 1e-3  # |cos| of a plane normal with the axis to call the plane square to it


class BaselineError(ValueError):
    """The face a ``baseline_dims`` counts from cannot be one (said in words for the agent)."""


def _along(v: Vec3, axis: Vec3) -> float:
    return v[0] * axis[0] + v[1] * axis[1] + v[2] * axis[2]


def baseline_dims(
    datum: str,
    planes: Sequence[Any],
    spans: Sequence[tuple[Vec3, float, float]],
    skip: Collection[str],
    prefix: str,
    view: str | None = None,
    side: str | None = None,
) -> list[dict[str, Any]]:
    """The ``dim`` items that give the length of every step of a turned part from ONE datum face (ISO 129-1: parallel
    dimensions from a datum, never a chain of links; the planner stacks them shortest nearest the part).

    ``datum`` is the id of a planar face square to the axis (a shoulder or an end); ``planes`` the planar faces of the part
    (``face_id``, ``normal``, ``centre``); ``spans`` the convex cylinders (their axis, where they start and where they end
    along it, as ``sheet_keyway.Cylinder`` has them): the positions where a cylinder ends are the steps. The planes at those
    positions and the two ends of the part (the lowest and the highest plane square to the axis: a chamfered end has no
    cylinder that ends there) are dimensioned; ``skip`` are faces that are not steps (the ends of a keyway). A datum that is
    not a plane square to an axis along x, y or z raises ``BaselineError``: nothing is guessed."""
    flat = next((p for p in planes if p.face_id == datum), None)
    if flat is None:
        msg = f"{datum} is not a planar face of the part: baseline_dims counts from a shoulder or an end of the part (a plane square to its axis)"
        raise BaselineError(msg)
    size = math.sqrt(_along(flat.normal, flat.normal)) or 1.0
    axis: Vec3 = (flat.normal[0] / size, flat.normal[1] / size, flat.normal[2] / size)
    name = sheet_keyway.axis_name(axis)
    if name is None:
        msg = f"{datum} is not square to the x, y or z axis: baseline_dims counts along the axis of a turned part"
        raise BaselineError(msg)
    origin = _along(flat.centre, axis)
    square = [
        p for p in planes if p.face_id not in skip and abs(_along(p.normal, axis)) >= SQUARE * (math.sqrt(_along(p.normal, p.normal)) or 1.0)
    ]
    ends: set[float] = set()
    for direction, lo, hi in spans:
        facing = _along(direction, axis)
        if abs(abs(facing) - 1.0) < 1e-3:
            ends.update((lo, hi) if facing > 0 else (-lo, -hi))
    if square:
        ends.update((min(_along(p.centre, axis) for p in square), max(_along(p.centre, axis) for p in square)))
    targets: dict[float, Any] = {}
    for position in sorted(ends):
        if abs(position - origin) <= STEP_TOL_MM or any(abs(position - q) <= STEP_TOL_MM for q in targets):
            continue
        here = sorted(
            (p for p in square if abs(_along(p.centre, axis) - position) <= STEP_TOL_MM and p.face_id != datum), key=lambda p: p.face_id
        )
        if here:
            targets[position] = here[0]
    if not targets:
        msg = f"{datum}: no other shoulder or end of the part lies square to the same axis, so there is nothing to dimension from it"
        raise BaselineError(msg)
    out: list[dict[str, Any]] = []
    for number, (_position, face) in enumerate(sorted(targets.items(), key=lambda kv: abs(kv[0] - origin)), start=1):
        item: dict[str, Any] = {
            "type": "dim",
            "kind": "distance",
            "id": f"{prefix}{number}",
            "from": {"face": datum},
            "to": {"face": face.face_id},
            "along": name,
        }
        if view:
            item["view"] = view
        if side:
            item["side"] = side
        out.append(item)
    return out
