# ruff: noqa: E501
"""The colour of the faces a ``hole`` operation made: which faces they are now, and painting them in the 3D view.

What is stored. A ``hole`` request arrives with the colour and the class the convention gave it (``colour``,
``hole_class``) and the fit the engineer declared (``fit``); the feature keeps them in its ``params``, which are saved in
the file with the rest of the registry. So this works on a document with no GUI: the colours are a fact of the registry,
``face_colours`` reads them, and the isometric view of a drawing (``sheet_iso``) uses the same answer.

Which faces. Face numbers shift whenever a later feature changes the solid, so they are never remembered: the registry
follows every face through every operation (``FeatureRec.created_faces`` -> ``BodyRec.faces[id].index``), and a patterned
or mirrored copy of a coloured hole takes the colour of its seed.

Painting. Only a visible worker has a ``ViewObject``; ``sync`` runs after every operation that changes the model and writes
the per-face ``DiffuseColor`` only when the list the object has is not what the holes need (``hole_paint.plan``), so a
document without holes costs one pass over its feature list and nothing else. FreeCAD saves that list with the document's
GUI state, and ``sync`` also runs when a document is opened, which re-derives it from the registry.
"""

import sys
import traceback
from typing import Any

import hole_paint

PATTERN_KINDS = frozenset({"linear_pattern", "circular_pattern", "mirror"})
Rgb = tuple[int, int, int]

# bodies whose colours this worker painted, by (document name, object name): the colours of ours that may be on them
_painted: dict[tuple[str, str], set[Rgb]] = {}


def _colour_of(params: dict[str, Any]) -> Rgb | None:
    value = params.get("colour")
    if isinstance(value, list | tuple) and len(value) == 3:
        return (int(value[0]), int(value[1]), int(value[2]))
    return None


def hole_features(doc: Any) -> dict[str, tuple[Rgb, str | None]]:
    """Feature id -> ``(colour, class)`` for every feature whose faces are painted: the holes that carry a colour and the
    patterns and mirrors of them. A hole made while the convention was off (or before there was one) is not in it."""
    found: dict[str, tuple[Rgb, str | None]] = {}
    for feature in doc.features:
        if feature.kind == "hole":
            colour = _colour_of(feature.params)
            if colour is not None:
                found[feature.id] = (colour, feature.params.get("hole_class"))
        elif feature.kind in PATTERN_KINDS:
            for seed in feature.params.get("features") or ():
                seed_id = seed.get("id") if isinstance(seed, dict) else None
                if seed_id in found:
                    found[feature.id] = found[seed_id]
                    break
    return found


def face_colours(doc: Any, body: Any) -> dict[int, Rgb]:
    """Face index (in the body's current shape) -> colour, for the faces of every coloured hole that still exist."""
    painted = hole_features(doc)
    if not painted:
        return {}
    faces = body.faces
    out: dict[int, Rgb] = {}
    bores: list[tuple[Vec, Vec, Rgb]] = []
    for feature in doc.features:
        if feature.id not in painted:
            continue
        colour = painted[feature.id][0]
        for face_id in feature.created_faces:
            rec = faces.get(face_id)
            if rec is not None and not rec.stale:
                out[rec.index] = colour
                bore = _bore_axis(rec)
                if bore is not None:
                    bores.append((*bore, colour))
    if bores:
        for feature in doc.features:
            if feature.kind in OPENING_KINDS and feature.id not in painted:
                for face_id in feature.created_faces:
                    rec = faces.get(face_id)
                    if rec is not None and not rec.stale and rec.index not in out:
                        colour = _coaxial_colour(rec, bores)
                        if colour is not None:
                            out[rec.index] = colour
    return out


# --- the counterbore or countersink a cut opened on a drilled hole ----------------------------------------------------------

OPENING_KINDS = frozenset({"cut"})
"""Features that can open a hole wider at its mouth. A hole made with ``counterbore`` / ``countersink`` owns those faces
already; a counterbore made as a pocket (a circle on the face, cut ``depth_mm`` deep, concentric with the hole) is a feature
of its own, and its wall and floor are part of the same hole: the same class, the same colour."""
AXIS_TOL_MM = 1e-3
Vec = tuple[float, float, float]


def _vec(value: Any) -> Vec:
    return (float(value.x), float(value.y), float(value.z))


def _unit(v: Vec) -> Vec:
    length = (v[0] ** 2 + v[1] ** 2 + v[2] ** 2) ** 0.5
    return (v[0] / length, v[1] / length, v[2] / length)


def _bore_axis(rec: Any) -> tuple[Vec, Vec] | None:
    """``(point, unit direction)`` of the axis of a face that is a cylinder, ``None`` for any other face (or a stand-in
    with no geometry)."""
    try:
        surface = rec.shape.Surface
        if type(surface).__name__ != "Cylinder":
            return None
        return _vec(surface.Center), _unit(_vec(surface.Axis))
    except Exception:  # noqa: BLE001 - a face with no geometry is not a bore
        return None


def _on_axis(point: Vec, axis: tuple[Vec, Vec]) -> bool:
    (ox, oy, oz), (dx, dy, dz) = axis
    rx, ry, rz = point[0] - ox, point[1] - oy, point[2] - oz
    cross = (ry * dz - rz * dy, rz * dx - rx * dz, rx * dy - ry * dx)
    return (cross[0] ** 2 + cross[1] ** 2 + cross[2] ** 2) ** 0.5 <= AXIS_TOL_MM


def _parallel(a: Vec, b: Vec) -> bool:
    return abs(abs(a[0] * b[0] + a[1] * b[1] + a[2] * b[2]) - 1.0) <= 1e-6


def _coaxial(rec: Any, axis: tuple[Vec, Vec]) -> bool:
    """Whether the face turns about ``axis``: a cylinder or a cone on it (the wall of a counterbore, a countersink) or a
    plane square to it that is bounded by circles centred on it (the floor of a counterbore)."""
    shape = rec.shape
    surface = shape.Surface
    kind = type(surface).__name__
    if kind in ("Cylinder", "Cone", "Plane"):
        if not _parallel(_unit(_vec(surface.Axis)), axis[1]):
            return False
    if kind == "Cylinder":
        return _on_axis(_vec(surface.Center), axis)
    if kind == "Cone":
        return _on_axis(_vec(surface.Apex), axis)
    if kind == "Plane":
        curves = [edge.Curve for edge in shape.Edges]
        return bool(curves) and all(
            type(curve).__name__ == "Circle" and _on_axis(_vec(curve.Center), axis) for curve in curves
        )
    return False


def _coaxial_colour(rec: Any, bores: list[tuple[Vec, Vec, Rgb]]) -> Rgb | None:
    try:
        for point, direction, colour in bores:
            if _coaxial(rec, (point, direction)):
                return colour
    except Exception:  # noqa: BLE001 - a face with no geometry belongs to no hole
        return None
    return None


def classes(doc: Any) -> dict[str, str | None]:
    """Feature id -> class (``precision`` / ``plain`` / ``threaded``) of the coloured holes (what a review compares)."""
    return {fid: cls for fid, (_, cls) in hole_features(doc).items()}


def hole_fit(feature: Any) -> str | None:
    """The fit a ``hole`` feature was declared with (``H7``), ``None`` if none."""
    fit = feature.params.get("fit")
    return str(fit) if fit else None


def seed_hole(doc: Any, feature: Any, _depth: int = 0) -> Any | None:
    """The ``hole`` feature whose declarations (fit, class, thread) apply to the faces ``feature`` made: itself, or for a
    pattern or mirror the hole it copies (through patterns of patterns). ``None`` for any other feature."""
    if feature.kind == "hole":
        return feature
    if feature.kind not in PATTERN_KINDS or _depth > len(doc.features):
        return None
    by_id = {f.id: f for f in doc.features}
    for seed in feature.params.get("features") or ():
        found = by_id.get(seed.get("id") if isinstance(seed, dict) else None)
        source = seed_hole(doc, found, _depth + 1) if found is not None else None
        if source is not None:
            return source
    return None


def declarations(doc: Any, feature: Any) -> tuple[str | None, str | None, int | None, Any] | None:
    """``(fit, class, precision grade, thread)`` a ``hole`` operation declared for the faces ``feature`` made -- a pattern or
    mirror of it included, which has the declarations of its seed; ``None`` when the faces are not a hole's."""
    source = seed_hole(doc, feature)
    if source is None:
        return None
    params = source.params
    return hole_fit(source), params.get("hole_class"), params.get("precision_grade"), params.get("thread")


# --- the 3D view -------------------------------------------------------------------------------------------------------


def _rgba(value: Any) -> hole_paint.RGBA:
    parts = [float(c) for c in value]
    return (parts[0], parts[1], parts[2], parts[3] if len(parts) > 3 else 0.0)


def _owner(body: Any) -> str | None:
    return body.fc_body if body.kind == "partdesign" else body.result_obj


def sync(doc: Any) -> None:
    """Paint the holes of every body of ``doc`` in the visible worker (no-op headless). Cosmetic: a failure is logged,
    never an error of the operation that ran."""
    import gui  # noqa: PLC0415 - imports FreeCAD's GUI; a headless worker never gets here

    if not gui.ACTIVE:
        return
    try:
        _sync(doc)
    except Exception:  # noqa: BLE001
        sys.stderr.write(traceback.format_exc())


def _sync(doc: Any) -> None:
    fc_doc = doc.fc_doc
    painted = hole_features(doc)
    if not painted and not any(key[0] == fc_doc.Name for key in _painted):
        return  # a document with no coloured hole, and none to take back
    convention = {colour for colour, _ in painted.values()}
    for body in doc.live_bodies():
        name = _owner(body)
        obj = fc_doc.getObject(name) if name else None
        view = None if obj is None else obj.ViewObject
        if view is None:
            continue
        key = (fc_doc.Name, obj.Name)
        wanted = face_colours(doc, body) if painted else {}
        if not wanted and key not in _painted:
            continue
        ours = convention | _painted.get(key, set())
        faces = len(obj.Shape.Faces)
        base = _rgba((*tuple(view.ShapeColor)[:3], view.Transparency / 100.0 if hasattr(view, "Transparency") else 0.0))
        new = hole_paint.plan([_rgba(c) for c in view.DiffuseColor], faces, wanted, ours, base)
        if new is not None:
            view.DiffuseColor = new
        _painted[key] = set(ours) | set(wanted.values())
        if not wanted:
            _painted.pop(key, None)


def forget(fc_doc_name: str) -> None:
    """The document is closing: nothing left to take back."""
    for key in [k for k in _painted if k[0] == fc_doc_name]:
        del _painted[key]
