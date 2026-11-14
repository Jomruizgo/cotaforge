# ruff: noqa: E501
"""Keyways read from the shape of the part (no FreeCAD import: plain arithmetic on what ``sheet_facts`` hands over).

A keyway is a slot with two parallel flat walls cut along the axis of a cylindrical face: in a shaft (the walls reach the
cylinder from outside, the slot is cut into the convex face) or in a hub (the walls run out from the bore). What tells it from
any other pair of parallel faces is the cylinder: the walls are parallel to its axis, one on each side of it at the same
distance, the plane half way between them contains the axis, the width between them is a fraction of the diameter and they
lie along the cylinder's own length.
"""

import math
from dataclasses import dataclass

Vec3 = tuple[float, float, float]

PARALLEL = 0.02  # |cos| of a wall's normal with the axis: it is parallel to the axis within this
SAME_PLANE = 0.98  # |cos| of the normals of the two walls: they are parallel
MIN_WIDTH_MM = 1.0
MAX_WIDTH_RATIO = 1.2  # the width between the walls, over the radius of the cylinder
SYMMETRY_MM = 0.5  # the slot is centred on the axis within this, plus 15 % of its width (a sketch is placed by hand)
AXIAL_SLACK_MM = 0.5
FACES_LIMIT = 4000  # a part with more faces is not read: the pairs would cost more than the fact is worth


@dataclass(frozen=True)
class Cylinder:
    """A cylindrical face (or the faces of one cylinder): its axis (unit), a point on it, the radius, whether the material is
    outside (a bore) and where it lies along the axis (``lo`` to ``hi``, measured as the dot product with the axis)."""

    axis: Vec3
    point: Vec3
    radius: float
    concave: bool
    lo: float
    hi: float


@dataclass(frozen=True)
class Flat:
    """A planar face: the unit normal and the centre of mass (``face_id``: its id in the model, when it has one)."""

    normal: Vec3
    centre: Vec3
    face_id: str = ""


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _radial(c: Cylinder, p: Vec3) -> float:
    d = _sub(p, c.point)
    along = _dot(d, c.axis)
    return math.sqrt(max(0.0, _dot(d, d) - along * along))


def _walls(c: Cylinder, a: Flat, b: Flat) -> bool:
    if abs(_dot(a.normal, c.axis)) > PARALLEL or abs(_dot(b.normal, c.axis)) > PARALLEL:
        return False  # not parallel to the axis
    if abs(_dot(a.normal, b.normal)) < SAME_PLANE:
        return False  # not parallel to each other
    side_a, side_b = _dot(_sub(a.centre, c.point), a.normal), _dot(_sub(b.centre, c.point), a.normal)
    if side_a * side_b >= 0:
        return False  # both on one side of the axis: a flat, a step, not a slot around the axis
    width = abs(side_a - side_b)
    if not MIN_WIDTH_MM <= width <= MAX_WIDTH_RATIO * c.radius:
        return False
    if abs(side_a + side_b) > SYMMETRY_MM + 0.15 * width:
        return False  # the plane half way between the walls does not hold the axis
    lateral = _cross(c.axis, a.normal)
    if abs(_dot(_sub(a.centre, b.centre), lateral)) > 1.0 + 0.1 * width:
        return False  # the walls are not face to face: they belong to two slots
    for wall in (a, b):
        reach = _radial(c, wall.centre)
        inside = reach <= c.radius * 1.02 + 0.2 if not c.concave else c.radius * 0.98 - 0.2 <= reach <= c.radius * 2.5 + 1.0
        along = _dot(wall.centre, c.axis)
        if not inside or not c.lo - AXIAL_SLACK_MM <= along <= c.hi + AXIAL_SLACK_MM:
            return False
    return True


@dataclass(frozen=True)
class Round:
    """A rounded end of a slot (a keyway of form A, cut with an end mill, ends in a half circle): the cylindrical face, a point of
    its axis and its radius. Nothing else of the part is at the end of such a keyway: no plane ends it."""

    face_id: str
    centre: Vec3
    axis: Vec3
    radius: float


@dataclass(frozen=True)
class Slot:
    """A keyway as the walls of the part show it: its two walls, the bottom (``None`` when none is found), the planes that end
    it along the axis (none when it runs out through the end of the shaft, one when it does, two when it is closed), the
    planes that end the cylinder itself (``caps``), the cylinder and the radial direction ``up`` the slot opens towards from
    the axis. ``reach`` is how far from the axis the bottom lies along ``up``."""

    cylinder: Cylinder
    wall_a: Flat
    wall_b: Flat
    bottom: Flat | None
    ends: tuple[Flat, ...]
    caps: tuple[Flat, ...]
    up: Vec3
    width: float
    reach: float | None
    rounds: tuple[Round, ...] = ()
    """The half circles that end it along the axis (``with_rounds``), in the order of the axis, when no plane does."""


def with_rounds(slot: Slot, candidates: list[Round]) -> Slot:
    """``slot`` with the rounded ends the part has for it: cylinders across the shaft (their axis along the way the slot opens)
    as wide as the slot, centred on its middle plane and lying along the cylinder it is cut in. A slot a plane ends keeps none."""
    if slot.ends:
        return slot
    c = slot.cylinder
    lateral = _cross(c.axis, slot.up)
    mid = tuple((slot.wall_a.centre[i] + slot.wall_b.centre[i]) / 2 for i in range(3))
    found = []
    for r in candidates:
        if abs(abs(_dot(r.axis, slot.up)) - 1.0) > PARALLEL or abs(2 * r.radius - slot.width) > 0.2 + 0.02 * slot.width:
            continue
        if abs(_dot(_sub(r.centre, mid), lateral)) > SYMMETRY_MM:
            continue
        along = _dot(r.centre, c.axis)
        if c.lo - AXIAL_SLACK_MM <= along <= c.hi + AXIAL_SLACK_MM:
            found.append(r)
    found.sort(key=lambda r: _dot(r.centre, c.axis))
    if len(found) < 2:
        return slot
    return Slot(c, slot.wall_a, slot.wall_b, slot.bottom, slot.ends, slot.caps, slot.up, slot.width, slot.reach, (found[0], found[-1]))


def round_tip(slot: Slot, end: Round, outward: float) -> Vec3:
    """The point of the wall at the farthest reach of a rounded end along the axis of the shaft (``outward``: -1 the low end, +1 the
    high one), half way down the slot: a point OF the part, so a dimension can start from it."""
    c = slot.cylinder
    mid = slot.cylinder.point
    along = _dot(end.centre, c.axis) + outward * end.radius - _dot(mid, c.axis)
    top = c.radius
    depth = (slot.reach + top) / 2 if slot.reach is not None else top
    return tuple(round(mid[i] + c.axis[i] * along + slot.up[i] * depth, 4) for i in range(3))  # type: ignore[return-value]


def _unit(v: Vec3) -> Vec3:
    n = math.sqrt(_dot(v, v)) or 1.0
    return (v[0] / n, v[1] / n, v[2] / n)


def _slot_of(c: Cylinder, a: Flat, b: Flat, flats: list[Flat]) -> Slot:
    mid = tuple((a.centre[i] + b.centre[i]) / 2 for i in range(3))
    radial = _sub(mid, c.point)
    radial = tuple(radial[i] - _dot(radial, c.axis) * c.axis[i] for i in range(3))
    up = _unit(radial)  # type: ignore[arg-type]
    lateral = _cross(c.axis, up)
    width = abs(_dot(_sub(a.centre, b.centre), a.normal))
    bottom: Flat | None = None
    ends: list[Flat] = []
    caps: list[Flat] = []
    for f in flats:
        if f is a or f is b:
            continue
        offset = _sub(f.centre, c.point)
        side = abs(_dot(offset, lateral) - _dot(_sub(mid, c.point), lateral))
        height = _dot(offset, up)
        along = _dot(f.centre, c.axis)
        if abs(_dot(f.normal, c.axis)) > SAME_PLANE:  # across the axis: an end of the slot or an end of the shaft
            if (abs(along - c.lo) <= AXIAL_SLACK_MM or abs(along - c.hi) <= AXIAL_SLACK_MM) and _radial(c, f.centre) <= c.radius * 1.02 + 0.2:
                caps.append(f)
            elif side <= width / 2 + 1.0 and c.lo + AXIAL_SLACK_MM < along < c.hi - AXIAL_SLACK_MM and 0.0 < height <= c.radius * 2.5 + 1.0:
                ends.append(f)
        elif abs(_dot(f.normal, up)) > SAME_PLANE and side <= width / 2 + 1.0 and abs(_dot(f.normal, c.axis)) < PARALLEL:
            if height > 0.0 and (bottom is None or abs(height) < abs(_dot(_sub(bottom.centre, c.point), up))):
                bottom = f
    reach = _dot(_sub(bottom.centre, c.point), up) if bottom is not None else None
    return Slot(c, a, b, bottom, tuple(ends), tuple(caps), up, width, reach)


def find_slots(cylinders: list[Cylinder], flats: list[Flat]) -> list[Slot]:
    """The keyways of the part: the pairs of walls that one cylinder holds as described above (each counted once even when
    a shaft and the bore of a hub on it both hold it), with the bottom and the ends of each."""
    if len(flats) < 2 or len(flats) > FACES_LIMIT:
        return []
    found: dict[frozenset[int], Slot] = {}
    for cylinder in cylinders:
        for i, a in enumerate(flats):
            if abs(_dot(a.normal, cylinder.axis)) > PARALLEL:
                continue
            for j in range(i + 1, len(flats)):
                if _walls(cylinder, a, flats[j]) and frozenset((i, j)) not in found:
                    found[frozenset((i, j))] = _slot_of(cylinder, a, flats[j], flats)
    return list(found.values())


def count_keyways(cylinders: list[Cylinder], flats: list[Flat]) -> int:
    """How many keyways the part has (see ``find_slots``)."""
    return len(find_slots(cylinders, flats))


AXIS_NAMES = (("x", (1.0, 0.0, 0.0)), ("y", (0.0, 1.0, 0.0)), ("z", (0.0, 0.0, 1.0)))
PROFILE_VIEW = {"x": "right", "y": "front", "z": "top"}  # the standard view that looks along each model axis


def axis_name(v: Vec3) -> str | None:
    """``x``, ``y`` or ``z`` when ``v`` lies along that model axis (a dimension is measured along one), else ``None``."""
    return next((name for name, a in AXIS_NAMES if abs(abs(_dot(v, a)) - 1.0) < 1e-3), None)


def middle_of(slot: Slot) -> Vec3:
    """The point of the axis of the cylinder halfway along the keyway (where a section across it is cut)."""
    c = slot.cylinder
    along = _dot(slot.wall_a.centre, c.axis) - _dot(c.point, c.axis)
    return (c.point[0] + c.axis[0] * along, c.point[1] + c.axis[1] * along, c.point[2] + c.axis[2] * along)


def dimension_specs(
    slot: Slot,
    prefix: str,
    views: tuple[str, ...] = (),
    section: str | None = None,
    fit: str | None = None,
    depth_tol: tuple[float, float] | None = None,
) -> list[dict[str, object]]:
    """The ``dim`` items that say a keyway to the shop, measured on the model: the width (between the walls), the depth as
    the standards give it (``d - t1`` on a shaft, ``d + t2`` in a hub: from the bottom to the opposite side of the cylinder),
    the length (between the planes that end it; out through the end of the shaft: to that end) and the position (from the end
    of the shaft to the first plane that ends it). A dimension whose faces are not found, or that does not lie along a model
    axis, is left out: nothing is guessed. ``section`` is the label of the removed section across the keyway (``A-A``): the
    width and the depth go on it, with the width's class (``fit``) and the depth's deviations (``depth_tol``: upper, lower);
    without one they go in the view of the sheet that looks along the axis of the cylinder, when it has it."""
    c, out = slot.cylinder, []
    profile = PROFILE_VIEW.get(axis_name(c.axis) or "")
    view = {"view": section} if section else {"view": profile} if profile in views else {}
    across = axis_name(slot.wall_a.normal)
    if across is not None and slot.wall_a.face_id and slot.wall_b.face_id:
        out.append(
            {"type": "dim", "kind": "distance", "id": f"{prefix}_w", "from": {"face": slot.wall_a.face_id},
             "to": {"face": slot.wall_b.face_id}, "along": across, **view, **({"fit": fit} if fit else {})}
        )  # fmt: skip
    deep = axis_name(slot.up)
    if deep is not None and slot.bottom is not None and slot.bottom.face_id:
        along_axis = _dot(slot.bottom.centre, c.axis)
        opposite = tuple(c.point[i] + c.axis[i] * (along_axis - _dot(c.point, c.axis)) - slot.up[i] * c.radius for i in range(3))
        out.append(
            {"type": "dim", "kind": "distance", "id": f"{prefix}_t", "from": {"face": slot.bottom.face_id},
             "to": {"at": [round(v, 4) for v in opposite]}, "along": deep, **view, **({"tol": list(depth_tol)} if depth_tol else {})}
        )  # fmt: skip
    lengthwise = axis_name(c.axis)
    if lengthwise is None:
        return out
    inside = _dot(slot.wall_a.centre, c.axis)
    ends = sorted(slot.ends, key=lambda f: _dot(f.centre, c.axis))
    caps = sorted(slot.caps, key=lambda f: _dot(f.centre, c.axis))
    first = next((f for f in ends if _dot(f.centre, c.axis) < inside), None)
    last = next((f for f in reversed(ends) if _dot(f.centre, c.axis) > inside), None)
    cap_low = caps[0] if caps and _dot(caps[0].centre, c.axis) < inside else None
    cap_high = caps[-1] if caps and _dot(caps[-1].centre, c.axis) > inside else None
    if slot.rounds:  # closed by two half circles: the length runs between the farthest points of the walls, the position from the shoulder to the near one
        low_end, high_end = slot.rounds
        p_low, p_high = ({"at": list(round_tip(slot, low_end, -1.0))}, {"at": list(round_tip(slot, high_end, 1.0))})
        out.append({"type": "dim", "kind": "distance", "id": f"{prefix}_l", "from": p_low, "to": p_high, "along": lengthwise})
        near = cap_low if cap_low is not None else cap_high
        if near is not None and near.face_id:
            out.append({"type": "dim", "kind": "distance", "id": f"{prefix}_p", "from": {"face": near.face_id}, "to": p_low if cap_low is not None else p_high, "along": lengthwise})
    elif first is not None and last is not None and first.face_id and last.face_id:  # closed at both ends
        out.append({"type": "dim", "kind": "distance", "id": f"{prefix}_l", "from": {"face": first.face_id}, "to": {"face": last.face_id}, "along": lengthwise})
        near = cap_low if cap_low is not None else cap_high
        start = first if cap_low is not None else last
        if near is not None and near.face_id and start.face_id:
            out.append({"type": "dim", "kind": "distance", "id": f"{prefix}_p", "from": {"face": near.face_id}, "to": {"face": start.face_id}, "along": lengthwise})
    else:  # open out through an end of the shaft: its length is measured to that end
        end, cap = (first, cap_high) if first is not None else (last, cap_low)
        if end is not None and cap is not None and end.face_id and cap.face_id:
            out.append({"type": "dim", "kind": "distance", "id": f"{prefix}_l", "from": {"face": end.face_id}, "to": {"face": cap.face_id}, "along": lengthwise})
    return out
