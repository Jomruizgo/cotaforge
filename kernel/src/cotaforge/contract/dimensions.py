"""What counts as "only a dimension" when a feature or a sketch is edited in place (contract/dimensions.py).

Changing a dimension is setting a number on an object that already exists. Anything else (another number of entities,
another sketch shape, another plane, another end condition, another set of edges) is a different design and is rebuilt.
The executor asks here before it sends an edit and the analytic fake answers with the same rule, so both CADs agree on
what is in place; the FreeCAD worker holds the same table for its own (JSON) side.
"""

from typing import Any

__all__ = [
    "DERIVED_FIELDS",
    "DIMENSION_FIELDS",
    "SKETCH_CONSUMERS",
    "non_dimension_changes",
    "sketch_edit_refusal",
]

SKETCH_CONSUMERS = frozenset({"extrude", "cut", "revolve", "sweep"})
"""The ops that read a ``sketch`` step: the features a sketch edit is carried by."""

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
"""Per feature op, the request fields (dotted for a nested one) whose change is a dimension. ``operation_id`` is the
name of the step, not a dimension, and never counts."""


DERIVED_FIELDS: dict[str, frozenset[str]] = {
    "hole": frozenset({"colour", "hole_class", "precision_grade"}),
}
"""Per feature op, the request fields the adapter fills in from the convention (the class and colour of a hole, from its
fit and thread): never the engineer's design, so their change alone is not a change. A changed ``fit`` or ``thread``
is, and it is not listed here."""


def _leaves(value: Any, path: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            out.update(_leaves(item, f"{path}.{key}" if path else str(key)))
        return out or {path: {}}
    return {path: value}


def non_dimension_changes(kind: str, old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """The fields that differ between two JSON dumps of the request of a ``kind`` op and are not dimensions of it
    (empty: the change is only dimensions). A field going from absent (``None``) to a value, or back, is not a
    dimension either: a counterbore that appears is another hole."""
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
    """``value`` with every number replaced by ``None``: what stays is the shape (kinds, names, counts)."""
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int | float):
        return None
    if isinstance(value, dict):
        return {key: _skeleton(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_skeleton(item) for item in value]
    return value


def sketch_edit_refusal(old: dict[str, Any], new: dict[str, Any]) -> str | None:
    """Why a sketch cannot be changed by setting its dimensions (``None``: it can). ``old`` and ``new`` are JSON dumps
    of the two ``SketchRequest``s."""
    if old.get("on") != new.get("on"):
        return "the sketch is on another plane or face"
    for key in ("entities", "constraints", "named_dimensions"):
        if _skeleton(old.get(key)) != _skeleton(new.get(key)):
            return f"the sketch has other {key.replace('_', ' ')}, not only other dimensions"
    return None
