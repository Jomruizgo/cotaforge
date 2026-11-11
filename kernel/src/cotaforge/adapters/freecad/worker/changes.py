"""Pure, FreeCAD-duck-typed property snapshotting for the live manual-edit notice (the
CAD-selection/manual-edit seam): no FreeCAD import, so it is unit-testable against plain
stand-ins (``tests/unit/freecad_client``).

What counts as the DESIGN, and so is tracked: JSON-safe scalar properties (bool/int/float/str), plus
FreeCAD's dimensioned properties (``App::PropertyLength``/``PropertyAngle``/... -- Python-side
``Base.Quantity`` objects, duck-typed via their ``.Value`` float, reported by that plain number); for a
sketch, the dimensional constraints and a signature of its geometry; for a free-standing solid (not inside
a body or a part), its placement. Shapes, links and lists change on almost every recompute regardless of who
caused it and would drown a hand edit in noise.

What never counts: display-only state (``Visibility``), the properties FreeCAD marks as OUTPUT (computed by a
recompute, never typed by the engineer: ``Label2``, ...), internal ones (``_ElementMapVersion``, ...) and
TechDraw objects (their views recompute on their own when a sheet is opened). Camera, selection and colours
are not document properties at all, so they never reach this module.

A snapshot also remembers the type of every object (``Baseline.kinds``): that is how an object added or
removed by hand is told from one that was only edited (``watch.py``).
"""

import math
from typing import Any, NamedTuple

Snapshot = dict[str, dict[str, Any]]

_SCALAR_TYPES = (bool, int, float, str)

DISPLAY_ONLY = frozenset({"Visibility"})
"""Document-object properties that only say how the object is shown."""

_SKETCH_TYPE = "Sketcher::SketchObject"
_DIMENSIONAL = frozenset({"Distance", "DistanceX", "DistanceY", "Radius", "Diameter", "Angle"})
_CONTAINER_TYPES = frozenset({"PartDesign::Body", "App::Part"})
_OPAQUE = "~"
"""A key ending with it holds a signature that only says "changed"; its value is never shown."""

_output_cache: dict[tuple[str, str], bool] = {}


class Baseline(NamedTuple):
    props: Snapshot
    kinds: dict[str, str]


__all__ = ["DISPLAY_ONLY", "Baseline", "Snapshot", "capture", "diff", "snapshot"]


def _scalar(value: Any) -> Any:
    """``value`` as a JSON-safe scalar, or ``None`` when it is not one (shapes, placements, links,
    lists, ...). A ``Base.Quantity`` (what ``App::PropertyLength``/``PropertyAngle``/... give back from
    Python -- e.g. a ``Part::Box``'s ``Length`` is 10.0 mm, not the plain float ``10.0``) is unwrapped to
    its ``.Value``, duck-typed rather than importing FreeCAD: this module stays importable, and
    unit-testable, without it."""
    if isinstance(value, _SCALAR_TYPES):
        return value
    number = getattr(value, "Value", None)
    return number if isinstance(number, (int, float)) else None


def _is_output(obj: Any, type_id: str, name: str) -> bool:
    """FreeCAD flags the properties a recompute computes as ``Output``: they are never the engineer's input.
    Cached by type and name (the flag belongs to the declaration, not to the instance)."""
    key = (type_id, name)
    hit = _output_cache.get(key)
    if hit is None:
        try:
            hit = "Output" in obj.getPropertyStatus(name)
        except Exception:  # noqa: BLE001 - a stand-in (or a stale object) without statuses: nothing is an output
            hit = False
        _output_cache[key] = hit
    return hit


def _rounded(value: Any, digits: int = 3) -> float:
    return round(float(value), digits) + 0.0  # + 0.0: no "-0.0"


def _sketch_facts(obj: Any) -> dict[str, Any]:
    """What a hand edit of a sketch changes that no scalar property shows: how many constraints and geometries it
    has, the value of every driving dimension, and an opaque signature of where the geometry lies."""
    facts: dict[str, Any] = {}
    try:
        constraints = list(obj.Constraints)
        geometry = list(obj.Geometry)
    except Exception:  # noqa: BLE001 - a sketch being rebuilt may not answer
        return facts
    facts["constraints"] = len(constraints)
    facts["geometry"] = len(geometry)
    for index, constraint in enumerate(constraints):
        if constraint.Type not in _DIMENSIONAL or not getattr(constraint, "Driving", True):
            continue
        value = math.degrees(constraint.Value) if constraint.Type == "Angle" else constraint.Value
        facts[f"constraint {constraint.Name or f'#{index}'}"] = _rounded(value, 4)
    signature = []
    for geo in geometry:
        row = []
        for attr in ("StartPoint", "EndPoint", "Center"):
            point = getattr(geo, attr, None)
            if point is not None:
                row.extend(_rounded(c) for c in (point.x, point.y, point.z))
        for attr in ("Radius", "MajorRadius", "MinorRadius", "FirstParameter", "LastParameter"):
            number = getattr(geo, attr, None)
            if isinstance(number, (int, float)):
                row.append(_rounded(number))
        signature.append(tuple(row))
    facts[f"geometry{_OPAQUE}"] = hash(tuple(signature))
    return facts


def _placement_text(obj: Any) -> str | None:
    """The placement of a free-standing solid as a short text, ``None`` for what is not one: anything inside a
    body or a part follows its container, and the container's own move is the one worth reporting."""
    try:
        if obj.getParentGeoFeatureGroup() is not None:
            return None
        type_id = str(obj.TypeId)
        if type_id not in _CONTAINER_TYPES and not obj.isDerivedFrom("Part::Feature"):
            return None
        place = obj.Placement
        base = place.Base
        angles = place.Rotation.getYawPitchRoll()
    except Exception:  # noqa: BLE001 - not a placed object, or a stand-in without placements
        return None
    position = ", ".join(f"{_rounded(c):g}" for c in (base.x, base.y, base.z))
    turn = ", ".join(f"{_rounded(a, 2):g}" for a in angles)
    return f"at ({position}) turned ({turn})"


def capture(objects: list[Any]) -> Baseline:
    """``Baseline`` of the objects given: ``props`` is ``{object_name: {prop_name: value}}`` for everything the
    module docstring calls the design, ``kinds`` is ``{object_name: type}``. Drawing objects (TechDraw) are left
    out entirely."""
    props_by_object: Snapshot = {}
    kinds: dict[str, str] = {}
    for obj in objects:
        type_id = str(getattr(obj, "TypeId", ""))
        if type_id.startswith("TechDraw::"):
            continue
        props: dict[str, Any] = {}
        for name in getattr(obj, "PropertiesList", ()):
            if name in DISPLAY_ONLY or name.startswith("_"):
                continue
            try:
                raw = getattr(obj, name)
            except Exception:  # noqa: BLE001 - a property getter may legitimately raise on a stale object
                continue
            value = _scalar(raw)
            if value is not None and not _is_output(obj, type_id, name):
                props[name] = value
        if type_id == _SKETCH_TYPE:
            props.update(_sketch_facts(obj))
        placement = _placement_text(obj)
        if placement is not None:
            props["Placement"] = placement
        props_by_object[obj.Name] = props
        kinds[obj.Name] = type_id
    return Baseline(props_by_object, kinds)


def snapshot(objects: list[Any]) -> Snapshot:
    """``{object_name: {prop_name: value}}`` for EVERY scalar (or ``Quantity``) property of every object given,
    display and computed ones included, drawing objects (TechDraw) left out: a fingerprint for telling that something
    changed at all (``native_state``), not the design view of ``capture``."""
    out: Snapshot = {}
    for obj in objects:
        if str(getattr(obj, "TypeId", "")).startswith("TechDraw::"):
            continue
        props: dict[str, Any] = {}
        for name in getattr(obj, "PropertiesList", ()):
            try:
                raw = getattr(obj, name)
            except Exception:  # noqa: BLE001 - a property getter may legitimately raise on a stale object
                continue
            value = _scalar(raw)
            if value is not None:
                props[name] = value
        out[obj.Name] = props
    return out


def _line(prop: str, old: Any, new: Any) -> str:
    if prop.endswith(_OPAQUE):
        return f"{prop[: -len(_OPAQUE)]}: changed"
    return f"{prop}: {old!r} -> {new!r}"


def diff(before: Snapshot, after: Snapshot) -> dict[str, list[str]]:
    """``{object_name: ["Prop: old -> new", ...]}`` for every tracked object whose design properties changed
    between the two snapshots, in property-iteration order. An object present in only one of them is added or
    removed, not edited: the caller reports that (``watch.py``)."""
    changes: dict[str, list[str]] = {}
    for name, props in after.items():
        before_props = before.get(name)
        if before_props is None:
            continue
        lines = [
            _line(prop, before_props[prop], value)
            for prop, value in props.items()
            if prop in before_props and before_props[prop] != value
        ]
        if lines:
            changes[name] = lines
    return changes
