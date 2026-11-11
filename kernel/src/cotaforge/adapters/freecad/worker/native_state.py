"""Document state reading for ``run_native``: object fingerprints (what a step changed, and proof that a rolled-back
document equals its pre-step self), the solids summary (volume and box of the whole document) and the broken-state
detector.

A fingerprint is a JSON-safe dict per object: type, label, scalar properties, placement, links, expressions, sketch
geometry/constraints, spreadsheet cells and a shape summary (counts, volume, area, box). Equal fingerprints before and
after mean the step left no trace (up to ``ROUND`` decimals on floats).
"""

from typing import Any

import changes
import FreeCAD as App
import Part

ROUND = 6
VOLUME_TOL = 1e-9
# Presentation flags FreeCAD keeps out of its transactions: a step that only toggles them is not a model change
# (and could not be undone), so they are not part of the fingerprint. ``_ElementMapVersion`` is FreeCAD's own
# bookkeeping of the topological-name map: the first recompute of a body loaded from a file fills it in ("" -> the
# version), which is not a change of the model and must not read as an incomplete rollback.
_VOLATILE_SCALARS = ("Visibility", "_ElementMapVersion")

# Objects whose shape is legitimately null: empty containers, sketches without geometry, origin features.
_NULL_SHAPE_OK_PREFIXES = ("App::", "Sketcher::", "Spreadsheet::", "TechDraw::", "Drawing::", "Measure::")
_NULL_SHAPE_OK_TYPES = frozenset({"PartDesign::Body", "Part::Compound", "PartDesign::CoordinateSystem"})
# Objects that must produce a solid (Extrusion/Revolution/Fillet/Chamfer may legitimately give a shell or a face).
_SOLID_REQUIRED_TYPES = frozenset(
    {"Part::Box", "Part::Cylinder", "Part::Sphere", "Part::Cone", "Part::Torus", "Part::Prism", "Part::Wedge"}
)
_PARTDESIGN_SOLID_FEATURES = (
    "PartDesign::Pad",
    "PartDesign::Pocket",
    "PartDesign::Revolution",
    "PartDesign::Groove",
    "PartDesign::Hole",
    "PartDesign::Fillet",
    "PartDesign::Chamfer",
    "PartDesign::Draft",
    "PartDesign::Thickness",
    "PartDesign::LinearPattern",
    "PartDesign::PolarPattern",
    "PartDesign::Mirrored",
    "PartDesign::MultiTransform",
    "PartDesign::Boolean",
    "PartDesign::AdditiveBox",
    "PartDesign::SubtractiveBox",
    "PartDesign::AdditiveCylinder",
    "PartDesign::SubtractiveCylinder",
    "PartDesign::AdditiveSphere",
    "PartDesign::SubtractiveSphere",
)
_BOOLEAN_TYPES = frozenset(
    {"Part::Cut", "Part::Fuse", "Part::Common", "Part::MultiFuse", "Part::MultiCommon", "PartDesign::Boolean"}
)
_SKETCH_PROBLEMS = (
    ("ConflictingConstraints", "conflicting constraints"),
    ("RedundantConstraints", "redundant constraints"),
    ("PartiallyRedundantConstraints", "partially redundant constraints"),
    ("MalformedConstraints", "malformed constraints"),
)


def _r(value: Any) -> float:
    return round(float(value), ROUND)


def _shape_of(obj: Any) -> Any:
    """The object's shape in document coordinates, or ``None`` when it has none."""
    if not hasattr(obj, "Shape"):
        return None
    try:
        shape = Part.getShape(obj)
    except Exception:  # noqa: BLE001 - objects with no usable shape
        try:
            shape = obj.Shape
        except Exception:  # noqa: BLE001
            return None
    return shape


def bbox_of(shape: Any) -> list[float] | None:
    try:
        box = shape.BoundBox
        if not box.isValid():
            return None
        return [_r(box.XMin), _r(box.YMin), _r(box.ZMin), _r(box.XMax), _r(box.YMax), _r(box.ZMax)]
    except Exception:  # noqa: BLE001
        return None


def shape_summary(shape: Any) -> dict[str, Any] | None:
    if shape is None or shape.isNull():
        return None
    out: dict[str, Any] = {"type": str(shape.ShapeType)}
    for key, attr in (("solids", "Solids"), ("faces", "Faces"), ("edges", "Edges"), ("vertices", "Vertexes")):
        try:
            out[key] = len(getattr(shape, attr))
        except Exception:  # noqa: BLE001
            out[key] = -1
    for key, attr in (("volume", "Volume"), ("area", "Area")):
        try:
            out[key] = _r(getattr(shape, attr))
        except Exception:  # noqa: BLE001
            out[key] = None
    out["bbox"] = bbox_of(shape)
    return out


def _link_name(value: Any) -> str | None:
    return value.Name if hasattr(value, "Name") and hasattr(value, "Document") else None


def _link_repr(value: Any) -> Any:
    """Name(s) for a link / link list / link-with-subnames value; ``None`` when ``value`` is not link-shaped."""
    name = _link_name(value)
    if name is not None:
        return name
    if isinstance(value, tuple) and len(value) == 2 and _link_name(value[0]) is not None:
        return [_link_name(value[0]), _subs_repr(value[1])]
    if isinstance(value, list | tuple):
        parts: list[Any] = []
        for item in value:
            if isinstance(item, tuple | list) and len(item) == 2 and _link_name(item[0]) is not None:
                parts.append([_link_name(item[0]), _subs_repr(item[1])])
            elif _link_name(item) is not None:
                parts.append(_link_name(item))
            else:
                return None
        return parts or None
    return None


def _subs_repr(subs: Any) -> Any:
    return list(subs) if isinstance(subs, list | tuple) else str(subs)


def _sketch_extras(obj: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        out["geometry"] = [str(g) for g in obj.Geometry]
        out["constraints"] = [
            [
                str(c.Type),
                int(c.First),
                int(c.FirstPos),
                int(c.Second),
                int(c.SecondPos),
                int(c.Third),
                int(c.ThirdPos),
                _r(c.Value),
                str(c.Name),
                bool(c.Driving),
                bool(c.InVirtualSpace),
            ]
            for c in obj.Constraints
        ]
    except Exception:  # noqa: BLE001
        pass
    return out


def _sheet_extras(obj: Any) -> dict[str, Any]:
    try:
        cells = {}
        for address in obj.getUsedCells():
            cells[address] = obj.getContents(address)
        aliases = {}
        for address in cells:
            alias = obj.getAlias(address)
            if alias:
                aliases[address] = alias
        return {"cells": cells, "aliases": aliases}
    except Exception:  # noqa: BLE001
        return {}


def object_fingerprint(obj: Any) -> dict[str, Any]:
    fp: dict[str, Any] = {"type": str(obj.TypeId), "label": str(obj.Label)}
    scalars = changes.snapshot([obj]).get(obj.Name, {})
    for volatile in _VOLATILE_SCALARS:
        scalars.pop(volatile, None)
    fp["scalars"] = scalars
    links: dict[str, Any] = {}
    frames: dict[str, Any] = {}
    for name in obj.PropertiesList:
        if name == "Shape":
            continue
        try:
            value = getattr(obj, name)
        except Exception:  # noqa: BLE001 - a property getter may raise on a half-built object
            continue
        if isinstance(value, App.Placement):
            frames[name] = [_r(x) for x in value.Matrix.A]
        elif isinstance(value, App.Vector):
            frames[name] = [_r(value.x), _r(value.y), _r(value.z)]
        elif isinstance(value, App.Rotation):
            frames[name] = [_r(q) for q in value.Q]
        elif name == "ExpressionEngine":
            frames[name] = [[str(p), str(e)] for p, e in value]
        else:
            link = _link_repr(value)
            if link is not None and link != []:
                links[name] = link
    fp["links"] = links
    fp["frames"] = frames
    fp["shape"] = shape_summary(_shape_of(obj))
    type_id = fp["type"]
    if type_id == "Sketcher::SketchObject":
        fp["sketch"] = _sketch_extras(obj)
    elif type_id == "Spreadsheet::Sheet":
        fp["sheet"] = _sheet_extras(obj)
    return fp


def document_fingerprint(fc_doc: Any) -> dict[str, dict[str, Any]]:
    """``{object_name: fingerprint}`` for every object of the document."""
    return {obj.Name: object_fingerprint(obj) for obj in fc_doc.Objects}


def fingerprint_diff(before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]) -> list[str]:
    """Human-readable differences between two document fingerprints (empty when they are equal)."""
    lines: list[str] = []
    for name in sorted(set(before) | set(after)):
        if name not in after:
            lines.append(f"{name}: missing after")
        elif name not in before:
            lines.append(f"{name}: unexpected object after")
        elif before[name] != after[name]:
            keys = sorted(k for k in set(before[name]) | set(after[name]) if before[name].get(k) != after[name].get(k))
            lines.append(f"{name}: changed {', '.join(keys)}")
    return lines


# --- solids of the whole document ---------------------------------------------------------------------------------


def _has_solids(shape: Any) -> bool:
    try:
        return shape is not None and not shape.isNull() and len(shape.Solids) > 0
    except Exception:  # noqa: BLE001
        return False


def _is_empty(shape: Any) -> bool:
    """True for a shape with no topology at all (the empty compound a boolean returns when nothing is left)."""
    try:
        return shape.isNull() or len(shape.Vertexes) == 0
    except Exception:  # noqa: BLE001
        return False


def solid_objects(fc_doc: Any) -> list[Any]:
    """Objects carrying solids that no other solid-carrying object consumes (the document's visible results).

    A box used by a ``Part::Cut`` is an input, not a result; a ``PartDesign::Body`` is a result and its inner
    features (consumed by the body and by later features) are not.
    """
    carriers = [o for o in fc_doc.Objects if _has_solids(_shape_of(o))]
    carrier_names = {o.Name for o in carriers}
    results = []
    for obj in carriers:
        if any(user.Name in carrier_names for user in obj.InList):
            continue
        results.append(obj)
    return results


def solids_summary(fc_doc: Any) -> tuple[float, list[float] | None]:
    """``(total_volume_mm3, bbox_or_None)`` of the document's result solids."""
    volume = 0.0
    boxes: list[list[float]] = []
    for obj in solid_objects(fc_doc):
        shape = _shape_of(obj)
        volume += sum(float(s.Volume) for s in shape.Solids)
        box = bbox_of(shape)
        if box is not None:
            boxes.append(box)
    if not boxes:
        return volume, None
    merged = [
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        min(b[2] for b in boxes),
        max(b[3] for b in boxes),
        max(b[4] for b in boxes),
        max(b[5] for b in boxes),
    ]
    return volume, merged


# --- broken state -------------------------------------------------------------------------------------------------


def _status(obj: Any) -> str:
    try:
        text = str(obj.getStatusString())
    except Exception:  # noqa: BLE001
        text = "invalid"
    return " ".join(text.split())[:300]


def broken_reasons(obj: Any) -> list[str]:
    """Why ``obj`` is unusable right now (empty when it is fine)."""
    reasons: list[str] = []
    type_id = str(obj.TypeId)
    try:
        state = list(obj.State)
    except Exception:  # noqa: BLE001
        state = []
    if "Invalid" in state or "Error" in state:
        reasons.append(f"recompute error: {_status(obj)}")
    if type_id == "Sketcher::SketchObject":
        # Degrees of freedom > 0 is a legal state; only solver problems count.
        for attr, label in _SKETCH_PROBLEMS:
            try:
                indexes = list(getattr(obj, attr))
            except Exception:  # noqa: BLE001
                continue
            if indexes:
                reasons.append(f"sketch with {label} (constraints {', '.join(str(int(i)) for i in indexes)})")
        return _dedupe_sketch(reasons)
    shape = _shape_of(obj)
    if shape is None:
        return reasons
    must_have_shape = (
        type_id in _SOLID_REQUIRED_TYPES
        or type_id in _PARTDESIGN_SOLID_FEATURES
        or (
            type_id not in _NULL_SHAPE_OK_TYPES
            and not type_id.startswith(_NULL_SHAPE_OK_PREFIXES)
            and not type_id.startswith("PartDesign::")
        )
    )
    if shape.isNull():
        if must_have_shape and "Invalid" not in state:
            reasons.append("null shape")
        return reasons
    try:
        valid = bool(shape.isValid())
    except Exception:  # noqa: BLE001
        valid = False
    if not valid:
        reasons.append("invalid shape (the geometry check failed)")
    if type_id in _BOOLEAN_TYPES and _is_empty(shape):
        reasons.append("boolean produced an empty result")
    elif (type_id in _SOLID_REQUIRED_TYPES or type_id in _PARTDESIGN_SOLID_FEATURES) and not _has_solids(shape):
        if "Invalid" not in state:
            reasons.append("no solid produced")
    for solid in getattr(shape, "Solids", ()):
        try:
            if float(solid.Volume) <= VOLUME_TOL:
                reasons.append(f"solid with zero or negative volume ({_r(solid.Volume)} mm3)")
                break
        except Exception:  # noqa: BLE001
            continue
    return reasons


def _dedupe_sketch(reasons: list[str]) -> list[str]:
    # an invalid sketch reports both the recompute error text and the constraint indexes: keep the precise one
    precise = [r for r in reasons if r.startswith("sketch with")]
    return precise or reasons


def broken_objects(fc_doc: Any) -> dict[str, list[str]]:
    """``{object_name: reasons}`` for every object that is unusable right now."""
    out: dict[str, list[str]] = {}
    for obj in fc_doc.Objects:
        reasons = broken_reasons(obj)
        if reasons:
            out[obj.Name] = reasons
    return out
