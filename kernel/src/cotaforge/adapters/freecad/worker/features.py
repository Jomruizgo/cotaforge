"""The FreeCAD document's own features: list them, delete one, set a property of one, and what the 3D view shows.

``list_features`` reads every feature object whoever made it (the plan, ``run_native``, the engineer, or the file that
was opened: the harness adopts such a file's bodies, not the history inside them). ``remove_feature`` and
``set_feature_properties`` change them the way FreeCAD does (``Body.removeObject``, a property set + recompute) and are
run as ONE ``run_native`` step (``ops_native``): the transaction, the rollback when something is left broken, the undo
journal and the re-sync of the registry are that step's. What this module adds is what a free-form fragment cannot know:
which objects need the one being deleted, the property's type and choices, and a check at the end of the fragment that
every touched body is still one valid solid that equals its Tip.

The fragments are plain FreeCAD Python (``doc``, the names of the objects as literals), so the macro the session exports
replays them.
"""

import math
import re
from typing import Any

import FreeCAD as App
import ops_native
import registry as reg
import sketch_defined
import sketches
from protocol import invalid_request, operation_id_of

BODY_TYPE = "PartDesign::Body"
LISTED_PREFIXES = ("PartDesign::", "Sketcher::", "Part::")
SKIPPED_TYPES = frozenset({BODY_TYPE, "Part::Compound"})
MAX_LISTED = 400
MAX_REFS = 200
MAX_REF_GEOMETRY = 60
MAX_NAMED = 8
SUMMARY_PROPERTIES = (
    "Radius",
    "Radius2",
    "Size",
    "Size2",
    "Length",
    "Length2",
    "Width",
    "Height",
    "Angle",
    "Angle2",
    "Type",
    "ChamferType",
    "Reversed",
    "Midplane",
    "Offset",
    "Occurrences",
    "Thickness",
    "Diameter",
    "Depth",
    "UseAllEdges",
)
EDITABLE_TYPES = {
    "App::PropertyLength": "length",
    "App::PropertyDistance": "length",
    "App::PropertyAngle": "angle",
    "App::PropertyFloat": "number",
    "App::PropertyFloatConstraint": "number",
    "App::PropertyQuantity": "number",
    "App::PropertyQuantityConstraint": "number",
    "App::PropertyPercent": "integer",
    "App::PropertyInteger": "integer",
    "App::PropertyIntegerConstraint": "integer",
    "App::PropertyBool": "bool",
    "App::PropertyEnumeration": "enum",
}
SKIPPED_STATUS = frozenset({"Output"})
"""``ReadOnly`` is not here: FreeCAD flips it by itself as other properties change (a chamfer's ``Size2`` is
read-only until its ``ChamferType`` asks for two distances), and the types above carry no computed result."""
POSITIVE = frozenset({"Radius", "Radius2", "Size", "Size2", "Thickness", "Diameter"})
"""Dimensions that make no sense at zero or below: refused with a plain message, not FreeCAD's recompute error."""
ALIASES = {
    "radius_mm": "Radius",
    "size_mm": "Size",
    "size2_mm": "Size2",
    "length_mm": "Length",
    "length2_mm": "Length2",
    "depth_mm": "Length",
    "thickness_mm": "Thickness",
    "diameter_mm": "Diameter",
    "angle_deg": "Angle",
    "offset_mm": "Offset",
}
REF_PROPERTIES = frozenset({"edges", "faces", "refs", "Base"})
REF_NAME = re.compile(r"^(Edge|Face)\d+$")
MATCH_TOL = 1e-6

# --- fragments (plain FreeCAD Python; ``doc`` and the literals are all they need) -----------------------------------

VERIFY_SNIPPET = """\
for _bn in _bodies:
    _b = doc.getObject(_bn)
    if _b is None or _b.Tip is None:
        continue
    _s = _b.Shape
    if _s.isNull() or not _s.Solids or not _s.isValid():
        raise RuntimeError("body " + _b.Label + " has no valid solid after this change")
    _t = _b.Tip.Shape.Volume
    if abs(_s.Volume - _t) > 1e-6 * max(1.0, abs(_t)):
        raise RuntimeError("body " + _b.Label + " does not match its Tip after this change")
"""
FOCUS_SNIPPET = """\
for _bn in _bodies:
    _b = doc.getObject(_bn)
    if _b is None or _b.Tip is None:
        continue
    for _o in _b.Group:
        if _o.Name == _b.Tip.Name:
            _o.Visibility = True
        elif _o.isDerivedFrom("Sketcher::SketchObject") or _o.isDerivedFrom("PartDesign::Feature"):
            _o.Visibility = False
    _b.Visibility = True
"""


# --- reading ----------------------------------------------------------------------------------------------------


def _is_feature(obj: Any) -> bool:
    type_id = str(obj.TypeId)
    return type_id not in SKIPPED_TYPES and type_id.startswith(LISTED_PREFIXES)


def _parent_body(obj: Any) -> Any | None:
    try:
        parent = obj.getParentGeoFeatureGroup()
    except Exception:  # noqa: BLE001 - an object kind without the notion of a parent group
        return None
    return parent if parent is not None and str(parent.TypeId) == BODY_TYPE else None


def _plain(value: Any) -> float | str | bool | None:
    if isinstance(value, bool):
        return value
    if hasattr(value, "Value"):
        return float(value.Value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return value
    return None


def _status(obj: Any, prop: str) -> set[str]:
    try:
        return set(obj.getPropertyStatus(prop))
    except Exception:  # noqa: BLE001
        return set()


SKETCH_TYPE = "Sketcher::SketchObject"
DIMENSION_CONSTRAINTS = {"Distance": "length", "DistanceX": "length", "DistanceY": "length", "Radius": "length"}
DIMENSION_CONSTRAINTS.update({"Diameter": "length", "Angle": "angle"})


def sketch_dimensions(obj: Any) -> dict[str, tuple[int, str, float]]:
    """``{name: (index, kind, value)}`` of the NAMED driving dimensions of a sketch: the constraints a sketch of an
    opened file can be changed by (an angle in degrees). A dimension without a name has none to be called by."""
    found: dict[str, tuple[int, str, float]] = {}
    if str(obj.TypeId) != SKETCH_TYPE:
        return found
    for index, c in enumerate(obj.Constraints):
        kind = DIMENSION_CONSTRAINTS.get(str(c.Type))
        if kind is None or not c.Name or not getattr(c, "Driving", True):
            continue
        value = math.degrees(float(c.Value)) if kind == "angle" else float(c.Value)
        found.setdefault(str(c.Name), (index, kind, value))
    return found


def _editable(obj: Any) -> dict[str, str]:
    """``{property: kind}`` of the scalar properties ``set_feature_properties`` accepts on ``obj`` (plus ``Label``); a
    sketch also takes the names of its driving dimensions."""
    found: dict[str, str] = {}
    for prop in obj.PropertiesList:
        kind = EDITABLE_TYPES.get(obj.getTypeIdOfProperty(prop))
        if kind is not None and not (_status(obj, prop) & SKIPPED_STATUS) and prop not in ("Visibility",):
            found[prop] = _refined(obj, prop, kind)
    for name, (_, kind, _) in sketch_dimensions(obj).items():
        found.setdefault(name, kind)
    found["Label"] = "text"
    return found


def _refined(obj: Any, prop: str, kind: str) -> str:
    """A quantity property says what it measures in its unit: a length, an angle."""
    if kind != "number":
        return kind
    try:
        unit = str(getattr(getattr(obj, prop), "Unit").Type)  # noqa: B009 - a plain float has no unit
    except AttributeError:
        return kind
    return {"Length": "length", "Angle": "angle"}.get(unit, kind)


def _read(obj: Any, prop: str) -> float | str | bool | None:
    try:
        return _plain(getattr(obj, prop))
    except Exception:  # noqa: BLE001 - a property that cannot be read right now is just not shown
        return None


def _base_refs(obj: Any) -> tuple[Any, list[str]] | None:
    """``(the feature it builds on, its edge/face names)`` for a dress-up, else None."""
    base = getattr(obj, "Base", None)
    if isinstance(base, (tuple, list)) and len(base) == 2 and base[0] is not None:
        return base[0], [str(s) for s in base[1]]
    return None


def _owner_map(doc: reg.DocumentRec) -> dict[str, reg.FeatureRec]:
    owners: dict[str, reg.FeatureRec] = {}
    for feature in doc.features:
        for name in (*feature.fc_names, feature.fc_main):
            if name:
                owners.setdefault(name, feature)
    return owners


def _body_ids(doc: reg.DocumentRec) -> dict[str, str]:
    """FreeCAD object name (container or result) -> the registry's body id."""
    found: dict[str, str] = {}
    for rec in doc.live_bodies():
        for name in (rec.fc_body, rec.result_obj):
            if name:
                found.setdefault(name, rec.id)
    return found


def _ref_geometry(shape: Any, name: str) -> dict[str, Any] | None:
    try:
        element = shape.getElement(name)
        if name.startswith("Edge"):
            mid = element.valueAt((element.FirstParameter + element.LastParameter) / 2.0)
            return {"name": name, "kind": "edge", "size": float(element.Length), "center": [mid.x, mid.y, mid.z]}
        centre = element.CenterOfMass
        return {"name": name, "kind": "face", "size": float(element.Area), "center": [centre.x, centre.y, centre.z]}
    except Exception:  # noqa: BLE001 - a name that is not in the shape any more
        return None


def _entry(
    doc: reg.DocumentRec, obj: Any, owners: dict[str, reg.FeatureRec], ids: dict[str, str], detail: bool
) -> dict:
    body = _parent_body(obj)
    tip = getattr(body, "Tip", None) if body is not None else None
    editable = _editable(obj) if detail else {}
    names = [p for p in (editable if detail else SUMMARY_PROPERTIES) if p != "Label" and p in obj.PropertiesList]
    properties: dict[str, Any] = {}
    for prop in names:
        value = _read(obj, prop)
        if value is not None:
            properties[prop] = value
    for name, (_, _, value) in list(sketch_dimensions(obj).items())[: MAX_NAMED if not detail else None]:
        properties[name] = value
    refs = _base_refs(obj)
    owner = owners.get(str(obj.Name))
    base_feature = getattr(obj, "BaseFeature", None)
    used_by = sorted({str(o.Name) for o in obj.InList if str(o.TypeId) != BODY_TYPE and o.Name != obj.Name})
    entry: dict[str, Any] = {
        "name": str(obj.Name),
        "label": str(obj.Label),
        "type_id": str(obj.TypeId),
        "body_name": str(body.Name) if body is not None else "",
        "body": {"id": ids[body.Name]} if body is not None and body.Name in ids else None,
        "is_tip": tip is not None and tip.Name == obj.Name,
        "suppressed": bool(getattr(obj, "Suppressed", False)),
        "visible": bool(getattr(obj, "Visibility", True)),
        "base": str(base_feature.Name) if base_feature is not None else None,
        "properties": properties,
        "ref_count": len(refs[1]) if refs is not None else None,
        "ref_names": refs[1][:MAX_REFS] if refs is not None else [],
        "used_by": used_by,
        "feature": {"id": owner.id} if owner is not None else None,
        "feature_kind": owner.kind if owner is not None else None,
    }
    if body is None:
        for name, rec_id in ids.items():
            if name == obj.Name:
                entry["body"] = {"id": rec_id}
    if detail:
        entry["editable_properties"] = [
            f"{p}:{kind}" + (f"[{'|'.join(obj.getEnumerationsOfProperty(p))}]" if kind == "enum" else "")
            for p, kind in editable.items()
        ]
        if refs is not None:
            shape = refs[0].Shape
            geometry = [_ref_geometry(shape, n) for n in refs[1][:MAX_REF_GEOMETRY]]
            entry["refs"] = [item for item in geometry if item is not None]
    return entry


def list_features(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    op = operation_id_of(params)
    fc_doc = doc.fc_doc
    ids = _body_ids(doc)
    owners = _owner_map(doc)
    only_body: set[str] | None = None
    if params.get("body") is not None:
        rec = reg.resolve_body(doc, reg.handle_id(params["body"], op, "body"), op)
        only_body = {n for n in (rec.fc_body, rec.result_obj) if n}
    wanted = params.get("feature")
    if wanted is not None:
        obj = find_object(fc_doc, str(wanted), op)
        return {
            "bodies": _bodies(doc, ids, {_name_of(_parent_body(obj))}),
            "features": [_entry(doc, obj, owners, ids, True)],
        }
    entries = []
    seen: set[str] = set()
    for container in fc_doc.Objects:  # a body's features in the order of its chain, then what lies outside any body
        if str(container.TypeId) != BODY_TYPE or (only_body is not None and container.Name not in only_body):
            continue
        for obj in container.Group:
            if _is_feature(obj) and obj.Name not in seen:
                seen.add(obj.Name)
                entries.append(obj)
    for obj in fc_doc.Objects:
        if _is_feature(obj) and obj.Name not in seen and _parent_body(obj) is None:
            if only_body is None or obj.Name in only_body:
                entries.append(obj)
    shown = entries[:MAX_LISTED]
    return {
        "bodies": _bodies(doc, ids, only_body),
        "features": [_entry(doc, obj, owners, ids, False) for obj in shown],
        "truncated": len(entries) - len(shown),
    }


def _name_of(obj: Any) -> str:
    return str(obj.Name) if obj is not None else ""


def _bodies(doc: reg.DocumentRec, ids: dict[str, str], only: set[str] | None) -> list[dict[str, Any]]:
    out = []
    for obj in doc.fc_doc.Objects:
        if str(obj.TypeId) != BODY_TYPE or (only is not None and obj.Name not in only):
            continue
        tip = getattr(obj, "Tip", None)
        out.append(
            {
                "name": str(obj.Name),
                "label": str(obj.Label),
                "body": {"id": ids[obj.Name]} if obj.Name in ids else None,
                "tip": str(tip.Name) if tip is not None else None,
                "visible": bool(getattr(obj, "Visibility", True)),
            }
        )
    return out


def find_object(fc_doc: Any, text: str, op: str | None) -> Any:
    """The feature object called ``text`` (its name, else its label, else its name in any case)."""
    obj = fc_doc.getObject(text)
    if obj is not None and _is_feature(obj):
        return obj
    hits = [o for o in fc_doc.getObjectsByLabel(text) if _is_feature(o)]
    if not hits:
        hits = [o for o in fc_doc.Objects if _is_feature(o) and str(o.Name).lower() == text.lower()]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        raise invalid_request(
            f"{text!r} names {len(hits)} features ({', '.join(str(o.Name) for o in hits[:MAX_NAMED])}): use the name",
            op,
        )
    known = [str(o.Name) for o in fc_doc.Objects if _is_feature(o)]
    shown = ", ".join(known[: MAX_NAMED * 3]) + (" ..." if len(known) > MAX_NAMED * 3 else "")
    raise invalid_request(f"no feature called {text!r} in the document; features: {shown or 'none'}", op)


# --- running a fragment as one step -----------------------------------------------------------------------------


def _step(doc: reg.DocumentRec, params: dict[str, Any], label: str, code: str) -> dict[str, Any]:
    intent = str(params.get("intent") or "") or label
    result = ops_native.run_native(
        doc, {"operation_id": operation_id_of(params), "code": code, "intent": intent, "timeout_s": 60.0}
    )
    result["warnings"] = [w for w in result["warnings"] if not w.startswith("Large fragment")]
    return result


def _bodies_literal(bodies: list[str]) -> str:
    return f"_bodies = {sorted(set(bodies))!r}\n"


# --- delete -----------------------------------------------------------------------------------------------------


def _targets_of_step(doc: reg.DocumentRec, handle: Any, op: str | None) -> list[Any]:
    rec = doc.feature(reg.handle_id(handle, op, "feature"), op)
    fc_doc = doc.fc_doc
    main = fc_doc.getObject(rec.fc_main) if rec.fc_main else None
    if main is not None and _is_feature(main):
        return [main]
    found = [o for o in (fc_doc.getObject(n) for n in rec.fc_names) if o is not None and _is_feature(o)]
    if not found:
        raise invalid_request(f"step feature {rec.id} ({rec.kind}) has no feature object in the document", op)
    return found


def _ordered(fc_doc: Any, objects: list[Any]) -> list[Any]:
    """Dependants before what they depend on (the order to delete in)."""
    names = {str(o.Name) for o in objects}
    depth = {}
    for obj in objects:
        depth[str(obj.Name)] = len({str(o.Name) for o in obj.OutListRecursive if str(o.Name) in names})
    return sorted(objects, key=lambda o: depth[str(o.Name)], reverse=True)


def _blockers(obj: Any, targets: set[str]) -> list[str]:
    """Objects (other than the Body that holds it and the feature built right on it, which FreeCAD re-links) that need
    ``obj``: a sketch placed on its face, a profile, an expression, a link."""
    found = []
    for user in obj.InList:
        name = str(user.Name)
        if str(user.TypeId) == BODY_TYPE or name in targets or name in found:
            continue
        base = getattr(user, "BaseFeature", None)
        if base is not None and base.Name == obj.Name:
            continue  # the next solid of the chain: FreeCAD re-links it to the previous one
        found.append(name)
    return found


def remove_feature(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    op = operation_id_of(params)
    fc_doc = doc.fc_doc
    if params.get("step_feature") is not None:
        objects = _targets_of_step(doc, params["step_feature"], op)
    else:
        objects = [find_object(fc_doc, str(params.get("feature")), op)]
    cascade = bool(params.get("cascade", False))
    targets = {str(o.Name) for o in objects}
    if cascade:
        for obj in list(objects):
            for user in obj.InListRecursive:
                if str(user.TypeId) != BODY_TYPE and _is_feature(user) and str(user.Name) not in targets:
                    targets.add(str(user.Name))
                    objects.append(user)
    else:
        for obj in objects:
            blockers = _blockers(obj, targets)
            if blockers:
                raise invalid_request(
                    f"{obj.Name} ({obj.Label}) is needed by {', '.join(blockers[:MAX_NAMED])}"
                    + (f" and {len(blockers) - MAX_NAMED} more" if len(blockers) > MAX_NAMED else "")
                    + ": delete those first, or pass cascade=true to delete them with it",
                    op,
                )
    objects = _ordered(fc_doc, objects)
    bodies: list[str] = []
    for obj in objects:
        body = _parent_body(obj)
        if body is None:
            continue
        bodies.append(str(body.Name))
        remaining = [
            o
            for o in body.Group
            if o.isDerivedFrom("PartDesign::Feature") and str(o.Name) not in targets and hasattr(o, "BaseFeature")
        ]
        if not remaining and any(o.isDerivedFrom("PartDesign::Feature") and str(o.Name) in targets for o in body.Group):
            raise invalid_request(
                f"deleting {obj.Name} would leave {body.Label} without any solid feature: undo the steps back to "
                "its start instead",
                op,
            )
    names = [str(o.Name) for o in objects]
    code = (
        _bodies_literal(bodies)
        + f"for _n in {names!r}:\n"
        + "    _o = doc.getObject(_n)\n"
        + "    _p = _o.getParentGeoFeatureGroup()\n"
        + '    if _p is not None and _p.TypeId == "PartDesign::Body":\n'
        + "        _p.removeObject(_o)\n"
        + "    doc.removeObject(_n)\n"
        + "doc.recompute()\n"
        + VERIFY_SNIPPET
        + FOCUS_SNIPPET
    )
    result = _step(doc, params, f"delete {', '.join(names)}", code)
    _explain_failure(result, "delete", names)
    return result


def _explain_failure(result: dict[str, Any], what: str, names: list[str]) -> None:
    error = result.get("error")
    if result.get("ok") or error is None:
        return
    refused_sketch = any(
        code in str(error.get("message", "")) for code in (sketch_defined.NOT_DEFINED, sketch_defined.HAS_BLOCK)
    )
    if error.get("type") == "BrokenResult" and not refused_sketch and not error.get("hint", "").startswith(what):
        error["hint"] = (
            f"{what} {', '.join(names)} leaves a feature that was built on it unable to rebuild (a dress-up whose "
            "edges are gone, a sketch placed on its face): delete that feature first, or use cascade=true"
            if what == "delete"
            else "pick another value, or other edges; the feature cannot rebuild with these"
        )


# --- set properties ---------------------------------------------------------------------------------------------


def _expression_bound(obj: Any, prop: str) -> bool:
    try:
        return any(
            str(path).lstrip(".") == prop or str(path).lstrip(".").startswith(prop + ".")
            for path, _ in obj.ExpressionEngine
        )
    except Exception:  # noqa: BLE001
        return False


def _number(prop: str, value: Any, op: str | None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise invalid_request(f"{prop} needs a number, got {value!r}", op)
    try:
        number = float(value)
    except ValueError as exc:
        raise invalid_request(f"{prop} needs a number, got {value!r}", op) from exc
    if not math.isfinite(number):
        raise invalid_request(f"{prop} must be a finite number", op)
    return number


def _coerce(obj: Any, prop: str, kind: str, value: Any, op: str | None) -> Any:
    """The value as the Python literal the fragment assigns, after the checks FreeCAD would only make at recompute."""
    if kind == "bool":
        if isinstance(value, bool) or value in (0, 1):
            return bool(value)
        raise invalid_request(f"{prop} is true or false, got {value!r}", op)
    if kind == "enum":
        choices = [str(c) for c in obj.getEnumerationsOfProperty(prop)]
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < len(choices):
            return choices[value]
        match = [c for c in choices if isinstance(value, str) and c.lower() == value.lower()]
        if not match:
            raise invalid_request(f"{prop} is one of {', '.join(choices)}; got {value!r}", op)
        return match[0]
    if kind == "text":
        if not isinstance(value, str) or not value.strip():
            raise invalid_request(f"{prop} needs a non-empty text", op)
        return value
    number = _number(prop, value, op)
    if kind == "integer":
        if number != int(number):
            raise invalid_request(f"{prop} needs a whole number, got {value!r}", op)
        return int(number)
    if prop in POSITIVE and number <= 0:
        raise invalid_request(f"{prop} must be greater than 0, got {number:g}", op)
    if kind == "length" and prop.startswith("Length") and number < 0:
        raise invalid_request(f"{prop} cannot be negative, got {number:g}", op)
    return number


def _edge_signature(edge: Any) -> tuple[float, tuple[float, ...]]:
    mid = edge.valueAt((edge.FirstParameter + edge.LastParameter) / 2.0)
    return float(edge.Length), (mid.x, mid.y, mid.z)


def _match_edge(doc: reg.DocumentRec, ref: str, base_shape: Any, op: str | None) -> str:
    """The name, in ``base_shape``, of the edge the harness id ``ref`` stands for (same length and midpoint)."""
    rec, comp = reg.resolve_edge(doc, ref, op)
    if comp is not None:
        raise invalid_request(f"{ref} belongs to a component: a dress-up works on the edges of its own body", op)
    body = reg.resolve_body(doc, rec.body, op)
    length, mid = _edge_signature(reg.topo_shape(doc, body).getElement(rec.sub_name))
    hits = []
    for index, candidate in enumerate(base_shape.Edges):
        c_length, c_mid = _edge_signature(candidate)
        if abs(c_length - length) <= MATCH_TOL * max(1.0, length) and all(
            abs(a - b) <= MATCH_TOL * max(1.0, abs(a)) for a, b in zip(c_mid, mid, strict=True)
        ):
            hits.append(f"Edge{index + 1}")
    if len(hits) != 1:
        raise invalid_request(
            f"{ref} has {'no' if not hits else 'several'} matching edge in the shape this feature builds on (the "
            "feature may be the one that creates it): give the FreeCAD names of that shape, e.g. "
            '"Edge12" (inspect(action=tree, id=<feature>) lists the ones it uses now)',
            op,
        )
    return hits[0]


def _refs_value(doc: reg.DocumentRec, obj: Any, value: Any, op: str | None) -> list[str]:
    refs = _base_refs(obj)
    if refs is None:
        raise invalid_request(f"{obj.Name} ({obj.TypeId}) does not work on a list of edges or faces", op)
    if not isinstance(value, (list, tuple)) or not value:
        raise invalid_request("edges needs a non-empty list of edge names ('Edge12') or ids ('edge:7')", op)
    base_obj, current = refs
    kind = "Face" if current and current[0].startswith("Face") else "Edge"
    shape = base_obj.Shape
    names: list[str] = []
    for item in value:
        text = item.get("id") if isinstance(item, dict) else item
        if not isinstance(text, str):
            raise invalid_request(f"an edge is a name ('Edge12') or an id ('edge:7'), got {item!r}", op)
        if REF_NAME.match(text):
            if not text.startswith(kind):
                raise invalid_request(f"{obj.Name} works on {kind.lower()}s: {text!r} is not one", op)
            if _ref_geometry(shape, text) is None:
                raise invalid_request(f"{text} is not in the shape {base_obj.Name} produces", op)
            name = text
        elif text.startswith("edge:") and kind == "Edge":
            name = _match_edge(doc, text, shape, op)
        else:
            raise invalid_request(f"{text!r} is not an edge name ('Edge12') or id ('edge:7')", op)
        if name not in names:
            names.append(name)
    return names


def set_feature_properties(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    op = operation_id_of(params)
    obj = find_object(doc.fc_doc, str(params.get("feature")), op)
    requested = params.get("properties")
    if not isinstance(requested, dict) or not requested:
        raise invalid_request("set_feature_properties needs at least one property", op)
    editable = _editable(obj)
    lines: list[str] = []
    for key, value in requested.items():
        wanted = ALIASES.get(str(key).lower(), str(key))
        if wanted.lower() in {r.lower() for r in REF_PROPERTIES}:
            names = _refs_value(doc, obj, value, op)
            lines.append(f"_o.Base = (_o.Base[0], {names!r})")
            continue
        dimensions = sketch_dimensions(obj)
        prop = next((p for p in editable if p.lower() == wanted.lower()), None)
        if prop is not None and prop in dimensions:
            index, kind, _ = dimensions[prop]
            if _expression_bound(obj, f"Constraints.{prop}"):
                raise invalid_request(
                    f"{obj.Name}.{prop} is bound to an expression: the next recompute would overwrite the new value; "
                    "change the expression's source",
                    op,
                )
            number = _number(prop, value, op)
            if number <= 0 and kind == "length":
                raise invalid_request(f"{prop} must be greater than 0, got {number:g}", op)
            lines.append(f"_o.setDatum({index}, {(math.radians(number) if kind == 'angle' else number)!r})")
            continue
        if prop is None:
            raise invalid_request(
                f"{obj.Name} ({obj.TypeId}) has no editable property {key!r}; it has: {', '.join(editable)}"
                + ("; edges: the list of edges a dress-up works on" if _base_refs(obj) is not None else ""),
                op,
            )
        if _expression_bound(obj, prop):
            raise invalid_request(
                f"{obj.Name}.{prop} is bound to an expression: the next recompute would overwrite the new value; "
                "change the expression's source",
                op,
            )
        lines.append(f"_o.{prop} = {_coerce(obj, prop, editable[prop], value, op)!r}")
    body = _parent_body(obj)
    bodies = [str(body.Name)] if body is not None else []
    code = (
        _bodies_literal(bodies)
        + f"_o = doc.getObject({str(obj.Name)!r})\n"
        + "\n".join(lines)
        + "\ndoc.recompute()\n"
        + VERIFY_SNIPPET
        + FOCUS_SNIPPET
    )
    result = _step(doc, params, f"set {obj.Name}: {', '.join(str(k) for k in requested)}", code)
    _explain_failure(result, "set", [str(obj.Name)])
    return result


# --- what the 3D view shows ---------------------------------------------------------------------------------------


def focus_model(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """Show each body's Tip and hide its sketches and intermediate features: ``Visibility`` of the objects, which the
    visible worker mirrors in the 3D view and a headless one keeps in the file. Not a step: nothing to undo."""
    op = operation_id_of(params)
    fc_doc = doc.fc_doc
    if params.get("bodies"):
        names = []
        for handle in params["bodies"]:
            rec = reg.resolve_body(doc, reg.handle_id(handle, op, "body"), op)
            names.append(str(rec.fc_body or rec.result_obj))
    else:
        names = [str(o.Name) for o in fc_doc.Objects if str(o.TypeId) == BODY_TYPE]
    before = {str(o.Name): bool(o.Visibility) for o in fc_doc.Objects if hasattr(o, "Visibility")}
    scope: dict[str, Any] = {"doc": fc_doc, "_bodies": names, "App": App}
    exec(FOCUS_SNIPPET, scope)  # noqa: S102 - our own constant, the same text the delete/edit fragments embed
    after = {str(o.Name): bool(o.Visibility) for o in fc_doc.Objects if hasattr(o, "Visibility")}
    return {
        "shown": [n for n, v in after.items() if v and not before.get(n, False)],
        "hidden": [n for n, v in after.items() if not v and before.get(n, False)],
    }


def set_reference_visibility(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """Show or hide sketches, datums and origin features by name (``Visibility``). Not a step: nothing to undo."""
    return sketches.set_visibility(doc.fc_doc, [str(n) for n in params.get("names", [])], bool(params.get("visible")))
