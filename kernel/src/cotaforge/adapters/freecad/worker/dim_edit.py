"""The pure half of editing a DIMENSION in place (no FreeCAD import, so the unit tests drive it with stand-ins).

Changing a dimension is setting a number on an object that exists: a PartDesign property (a pad's ``Length``, a hole's
``Diameter``, a fillet's ``Radius``, a pattern's ``Occurrences``) or the datum of a sketch constraint (``setDatum``).
This module decides WHAT to set; ``edit.py`` sets it inside the transaction with its guards.

* ``non_dimension_changes`` / ``sketch_refusal``: what is only a dimension and what is another design (the same table
  as ``cotaforge.contract.dimensions``, here over the JSON the worker receives).
* ``property_sets``: the properties a feature edit sets, only those whose value differs between the OLD recorded
  request and the new one. A value the engineer changed by hand and the edit does not touch is therefore never put back.
* ``datum_changes``: the datums of a sketch to set, from the constraint lists of two sketches built from scratch (the
  old and the new dimensions) and the live one: the same constraint at the same index, only the numbers differ.
"""

from typing import Any

FEATURE_TYPES: dict[str, tuple[str, ...]] = {
    "extrude": ("PartDesign::Pad",),
    "cut": ("PartDesign::Pocket",),
    "revolve": ("PartDesign::Revolution",),
    "sweep": ("PartDesign::AdditivePipe",),
    "hole": ("PartDesign::Hole",),
    "fillet": ("PartDesign::Fillet",),
    "chamfer": ("PartDesign::Chamfer",),
    "linear_pattern": ("PartDesign::LinearPattern", "PartDesign::MultiTransform"),
    "circular_pattern": ("PartDesign::PolarPattern",),
}
"""The FreeCAD type of the main object of each feature op that can be edited in place."""

FEATURE_KINDS = frozenset(FEATURE_TYPES)
SKETCH_CONSUMERS = frozenset({"extrude", "cut", "revolve", "sweep"})
FEATURE_LIVE_KINDS = FEATURE_KINDS | frozenset({"mirror", "shell", "draft"})
"""The PartDesign ops that follow an edit of an earlier feature of the same body (the BaseFeature chain)."""

DIMENSION_FIELDS: dict[str, frozenset[str]] = {
    "extrude": frozenset({"depth_mm", "depth2_mm"}),
    "cut": frozenset({"depth_mm", "depth2_mm"}),
    "revolve": frozenset({"angle_deg"}),
    "sweep": frozenset(),
    "hole": frozenset(
        {
            "position",
            "positions",
            "diameter_mm",
            "depth_mm",
            "counterbore.diameter_mm",
            "counterbore.depth_mm",
            "countersink.diameter_mm",
            "countersink.angle_deg",
            "drill_point_angle_deg",
        }
    ),
    "linear_pattern": frozenset({"spacing_mm", "count", "spacing2_mm", "count2"}),
    "circular_pattern": frozenset({"count", "angle_deg"}),
    "chamfer": frozenset({"distance1_mm", "distance2_mm"}),
    "fillet": frozenset({"radius_mm"}),
}

DERIVED_FIELDS: dict[str, frozenset[str]] = {
    "hole": frozenset({"colour", "hole_class", "precision_grade"}),
}
"""Filled in by the adapter from the convention, not the engineer's design: their change alone is not a change."""

DISTANCE_CONSTRAINTS = frozenset({"Distance", "DistanceX", "DistanceY", "Radius", "Diameter"})
"""The datums ``setDatum`` sets with a length in mm. (An angle is another unit: its change is a rebuild.)"""
VALUE_EPS = 1e-12


class NotInPlaceError(Exception):
    """The change cannot be set on the objects that exist; the caller rolls back and the steps are rebuilt."""


def _leaves(value: Any, path: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            out.update(_leaves(item, f"{path}.{key}" if path else str(key)))
        return out or {path: {}}
    return {path: value}


def non_dimension_changes(kind: str, old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    allowed = DIMENSION_FIELDS.get(kind, frozenset())
    derived = DERIVED_FIELDS.get(kind, frozenset())
    before, after = _leaves(old), _leaves(new)
    changed: list[str] = []
    for path in dict.fromkeys([*before, *after]):
        if path == "operation_id" or path in derived or before.get(path) == after.get(path):
            continue
        if path in allowed and before.get(path) is not None and after.get(path) is not None:
            continue
        changed.append(path)
    return changed


def _skeleton(value: Any) -> Any:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int | float):
        return None
    if isinstance(value, dict):
        return {key: _skeleton(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_skeleton(item) for item in value]
    return value


def sketch_refusal(old: dict[str, Any], new: dict[str, Any]) -> str | None:
    """Why the sketch cannot change by its dimensions alone (``None``: it can)."""
    if old.get("on") != new.get("on"):
        return "the sketch is on another plane or face"
    for key in ("entities", "constraints", "named_dimensions"):
        if _skeleton(old.get(key)) != _skeleton(new.get(key)):
            return f"the sketch has other {key.replace('_', ' ')}, not only other dimensions"
    return None


# --- who owns the faces an edit made again ---------------------------------------------------------------------------


def _curved(sig: Any) -> bool:
    return sig.surface in ("cylinder", "cone", "torus", "sphere")


def _gap(old: Any, new: Any) -> float | None:
    """How far apart a face that went stale and a new one are, ``None`` when the new one cannot be the old one again:
    another kind of surface or another direction. A curved surface is compared by its axis (a bore resized keeps it),
    any other by its centre."""
    if old.surface != new.surface:
        return None
    if old.direction is not None and new.direction is not None:
        if abs(sum(a * b for a, b in zip(old.direction, new.direction, strict=True))) < 0.999:
            return None
    a, b = (old.anchor, new.anchor) if _curved(old) and old.anchor and new.anchor else (old.center, new.center)
    return float(sum((x - y) ** 2 for x, y in zip(a, b, strict=True)) ** 0.5)


def replaced_faces(stale: dict[str, Any], new: dict[str, Any]) -> dict[str, str]:
    """``{new face id: the stale face id it replaces}``. An edit that resizes a bore or moves a feature a hair gives the
    face another signature, so the registry makes a new face and drops the old one; the new one must belong to the
    feature of the old one (its colour, its dimensions), not to whichever feature was edited. One to one, the closest
    pair first; a new face with no old one left to take is the edited feature's. ``stale`` and ``new`` map ids to the
    face signatures."""
    pairs = sorted(
        (gap, new_id, old_id)
        for new_id, new_sig in new.items()
        for old_id, old_sig in stale.items()
        if (gap := _gap(old_sig, new_sig)) is not None
    )
    taken_old: set[str] = set()
    out: dict[str, str] = {}
    for _, new_id, old_id in pairs:
        if new_id in out or old_id in taken_old:
            continue
        out[new_id] = old_id
        taken_old.add(old_id)
    return out


# --- the properties of a feature -------------------------------------------------------------------------------------


def _differs(old: Any, new: Any) -> bool:
    if isinstance(old, int | float) and isinstance(new, int | float):
        return abs(float(old) - float(new)) > VALUE_EPS
    return old != new


def property_sets(
    kind: str, old: dict[str, Any], new: dict[str, Any], main: Any, named: list[Any]
) -> list[tuple[Any, str, Any]]:
    """``[(object, property, value)]`` a feature edit sets: only the properties whose value differs between the old
    recorded request and the new one. ``main`` is the feature's object; ``named`` the other objects it made (a chamfer
    of several orientations, the two patterns of a multi-direction pattern). ``NotInPlaceError`` if FreeCAD can not
    set them."""
    out: list[tuple[Any, str, Any]] = []

    def put(obj: Any, prop: str, before: Any, after: Any) -> None:
        if _differs(before, after):
            out.append((obj, prop, after))

    if kind == "extrude":
        both = bool(new.get("both_directions"))
        put(main, "Length", old["depth_mm"], new["depth_mm"])
        if both:
            put(main, "Length2", old.get("depth2_mm") or old["depth_mm"], new.get("depth2_mm") or new["depth_mm"])
    elif kind == "cut":
        if new.get("end_condition") == "blind":
            put(main, "Length", old.get("depth_mm"), new.get("depth_mm"))
            if new.get("both_directions"):
                put(
                    main,
                    "Length2",
                    old.get("depth2_mm") or old.get("depth_mm"),
                    new.get("depth2_mm") or new.get("depth_mm"),
                )
    elif kind == "revolve":
        put(main, "Angle", old.get("angle_deg", 360.0), new.get("angle_deg", 360.0))
    elif kind == "hole":
        if _differs(old.get("diameter_mm"), new.get("diameter_mm")) and new.get("thread"):
            raise NotInPlaceError("the diameter of a threaded hole is FreeCAD's tap drill of its size")
        put(main, "Diameter", old["diameter_mm"], new["diameter_mm"])
        put(main, "Depth", old.get("depth_mm"), new.get("depth_mm"))
        for cut in ("counterbore", "countersink"):
            before, after = old.get(cut) or {}, new.get(cut) or {}
            put(main, "HoleCutDiameter", before.get("diameter_mm"), after.get("diameter_mm"))
            put(main, "HoleCutDepth", before.get("depth_mm"), after.get("depth_mm"))
            put(main, "HoleCutCountersinkAngle", before.get("angle_deg"), after.get("angle_deg"))
        put(main, "DrillPointAngle", old.get("drill_point_angle_deg"), new.get("drill_point_angle_deg"))
    elif kind == "fillet":
        put(main, "Radius", old["radius_mm"], new["radius_mm"])
    elif kind == "chamfer":
        for obj in named or [main]:
            put(obj, "Size", old["distance1_mm"], new["distance1_mm"])
            if new.get("distance2_mm") is not None:
                put(obj, "Size2", old.get("distance2_mm"), new["distance2_mm"])
    elif kind == "linear_pattern":
        patterns = named or [main]
        directions = [(old["spacing_mm"], old["count"], new["spacing_mm"], new["count"])]
        if new.get("direction2") is not None:
            directions.append((old["spacing2_mm"], old["count2"], new["spacing2_mm"], new["count2"]))
        if len(directions) != len(patterns):
            raise NotInPlaceError("the patterns of the feature are not the ones its request names")
        for obj, (old_spacing, old_count, spacing, count) in zip(patterns, directions, strict=True):
            put(obj, "Length", float(old_spacing) * (int(old_count) - 1), float(spacing) * (int(count) - 1))
            put(obj, "Occurrences", int(old_count), int(count))
    elif kind == "circular_pattern":
        prop = "Angle" if new.get("equal_spacing", True) else "Offset"
        put(main, prop, old.get("angle_deg", 360.0), new.get("angle_deg", 360.0))
        put(main, "Occurrences", int(old["count"]), int(new["count"]))
    return out


# --- the datums of a sketch ------------------------------------------------------------------------------------------


def datum_changes(
    old: tuple[list[Any], list[float]], new: tuple[list[Any], list[float]], live: list[Any]
) -> list[tuple[int, str, float]]:
    """``[(index, name, value)]`` of the driving datums that differ between the sketch built from the old dimensions
    (``old``) and from the new ones (``new``); each side is ``(structure, values)`` with one structure row per
    constraint (type, geometry and point references, driving, name). ``live`` is the structure of the sketch in the
    document. ``NotInPlaceError`` when the new dimensions give another structure (a vertex that now coincides with
    another one, say), when the live sketch is not the one the harness made (changed by hand), or when an angle
    changes."""
    if old[0] != new[0]:
        raise NotInPlaceError("the new dimensions give the sketch another set of constraints")
    if old[0] != live:
        raise NotInPlaceError(
            "the sketch in the CAD is not the one the harness made (its constraints were changed by hand)"
        )
    changes: list[tuple[int, str, float]] = []
    for index, (before, after) in enumerate(zip(old[1], new[1], strict=True)):
        kind, driving, name = old[0][index][0], old[0][index][7], old[0][index][8]
        if not driving or abs(after - before) <= VALUE_EPS:
            continue
        if kind not in DISTANCE_CONSTRAINTS:
            raise NotInPlaceError(
                f"{name or f'constraint {index}'} is a {kind} dimension: only lengths are set in place"
            )
        changes.append((index, name, after))
    return changes


POSITION_EPS = 1e-9  # mm: the same point
HAND_EPS = 1e-6  # mm: a datum of the sketch that is not where the recorded request left it was changed by hand


def hole_points(request: dict[str, Any]) -> list[tuple[float, float]]:
    """The (u, v) of every circle of a hole request, in the order of its sketch."""
    points = request.get("positions")
    if points is None:
        points = [request["position"]]
    return [(float(p[0]), float(p[1])) for p in points]


def hole_position_changes(
    old: dict[str, Any], new: dict[str, Any], rows: list[tuple[Any, ...]], values: list[float]
) -> list[tuple[int, str, float]]:
    """``[(index, name, value)]`` of the datums that move the circles of a hole sketch from the points of the old
    request to those of the new one: the DistanceX / DistanceY from the sketch origin to the centre of each circle
    (``rows`` and ``values``: the structure and the values of the live constraints, as ``datum_changes`` reads them).
    Another number of holes, a centre that has no such datum (on an axis of the sketch, or a hole across a tube wall,
    whose sketch is placed by its entry point) or a datum that is not where the recorded request left it raise
    ``NotInPlaceError``."""
    before, after = hole_points(old), hole_points(new)
    if len(before) != len(after):
        raise NotInPlaceError("the number of holes of the feature changes")
    changes: list[tuple[int, str, float]] = []
    for circle, (was, now) in enumerate(zip(before, after, strict=True)):
        for kind, axis in (("DistanceX", 0), ("DistanceY", 1)):
            if abs(was[axis] - now[axis]) <= POSITION_EPS:
                continue
            found = [
                i
                for i, row in enumerate(rows)
                if row[0] == kind and row[1:5] == (-1, 1, circle, 3) and row[7]  # origin to centre, driving
            ]
            if len(found) != 1:
                raise NotInPlaceError(
                    f"the sketch of the hole has no {kind} from its origin to the centre of hole {circle + 1}"
                )
            index = found[0]
            if abs(values[index] - was[axis]) > HAND_EPS:
                raise NotInPlaceError("the sketch of the hole is not where its request says (changed by hand)")
            if abs(now[axis]) <= POSITION_EPS:
                raise NotInPlaceError("a centre on an axis of the sketch is not a distance")
            changes.append((index, str(rows[index][8]), now[axis]))
    return changes


def bound_by_expression(paths: list[str], changes: list[tuple[int, str, float]]) -> list[str]:
    """The datums of ``changes`` that an expression of the sketch drives (a value set directly would be overwritten by
    the next recompute)."""
    bound: list[str] = []
    for index, name, _ in changes:
        wanted = {f"Constraints[{index}]"}
        if name:
            wanted.add(f"Constraints.{name}")
        if any(path in wanted for path in paths):
            bound.append(name or f"constraint {index}")
    return bound
