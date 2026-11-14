"""``mould_component``: parts of a catalogue as NATIVE PartDesign bodies, one body per part.

A part arrives as a dict (a ``PartSpec`` of the contract): a frame, a short list of features and the catalogue line it
stands for. Each feature is drawn with FreeCAD's own objects inside a ``PartDesign::Body``: a ``Sketcher::SketchObject``
(fully constrained, named dimensions the engineer can change) with a ``PartDesign::Pad``, ``Pocket`` or ``Revolution``, or
a primitive sphere or helix. The body carries the catalogue reference, size and source as properties
(``CotaforgeCatalog*``) and is checked against the volume the numbers give.

``build_part`` is the one builder: the slide mechanism (``slide_mech.py``) draws its parts through it as well.
"""

from typing import Any

import fc
import FreeCAD as App
import ops_common as oc
import Part
import registry as reg
from FreeCAD import Placement, Rotation, Vector
from protocol import geometry_failed, invalid_request, operation_id_of, unrecoverable

CATALOG_PROPERTIES = (
    ("CotaforgeCatalogFamily", "family"),
    ("CotaforgeCatalogRef", "reference"),
    ("CotaforgeCatalogSize", "size"),
    ("CotaforgeCatalogSource", "source"),
    ("CotaforgeCatalogNote", "note"),
)
VOLUME_TOL = 2e-4
"""A part must have the volume its numbers give within this share: a pocket that missed, a seat cut twice or a sketch
that closed the wrong way shows up here (the shapes are cylinders and prisms: exact)."""


def _vec(v: Any) -> Vector:
    return Vector(float(v[0]), float(v[1]), float(v[2]))


def _basis(x: Any, z: Any) -> Rotation:
    """The rotation whose columns are x, z x x and z (x is made perpendicular to z first)."""
    zv = _vec(z)
    zv.normalize()
    xv = _vec(x)
    xv = xv - zv * xv.dot(zv)
    if xv.Length < 1e-9:
        xv = Vector(1, 0, 0) if abs(zv.x) < 0.9 else Vector(0, 1, 0)
        xv = xv - zv * xv.dot(zv)
    xv.normalize()
    yv = zv.cross(xv)
    return Rotation(App.Matrix(xv.x, yv.x, zv.x, 0, xv.y, yv.y, zv.y, 0, xv.z, yv.z, zv.z, 0, 0, 0, 0, 1))


def frame_placement(frame: dict[str, Any]) -> Placement:
    return Placement(_vec(frame["origin"]), _basis(frame["x"], frame["z"]))


def _plane_placement(plane: dict[str, Any]) -> Placement:
    return Placement(_vec(plane["origin"]), _basis(plane["x"], plane["normal"]))


# --- sketches ------------------------------------------------------------------------------------------------------


def _at(sk: Any, geo: int, pos: int, u: float, v: float, name: str) -> None:
    """Pin a point of the sketch to (u, v): on an axis when it lies on one, else by two named distances."""
    if abs(u) < 1e-9:
        fc.add_constraint(sk, "PointOnObject", geo, pos, -2)
    else:
        i = fc.add_constraint(sk, "DistanceX", -1, 1, geo, pos, float(u))
        fc.call(sk, "renameConstraint", i, f"{name}_x")
    if abs(v) < 1e-9:
        fc.add_constraint(sk, "PointOnObject", geo, pos, -1)
    else:
        i = fc.add_constraint(sk, "DistanceY", -1, 1, geo, pos, float(v))
        fc.call(sk, "renameConstraint", i, f"{name}_y")


def _polygon(sk: Any, points: list[tuple[float, float]], name: str) -> None:
    n = len(points)
    ids = [
        fc.call(sk, "addGeometry", Part.LineSegment(Vector(*points[i], 0.0), Vector(*points[(i + 1) % n], 0.0)), False)
        for i in range(n)
    ]
    for i in range(n):
        fc.add_constraint(sk, "Coincident", ids[i], 2, ids[(i + 1) % n], 1)
    for i in range(n):
        _at(sk, ids[i], 1, points[i][0], points[i][1], f"{name}_P{i + 1}")


def _rect(sk: Any, cx: float, cy: float, w: float, h: float, name: str) -> None:
    x0, y0, x1, y1 = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    ids = [
        fc.call(sk, "addGeometry", Part.LineSegment(Vector(*pts[i], 0.0), Vector(*pts[(i + 1) % 4], 0.0)), False)
        for i in range(4)
    ]
    for i in range(4):
        fc.add_constraint(sk, "Coincident", ids[i], 2, ids[(i + 1) % 4], 1)
    fc.add_constraint(sk, "Horizontal", ids[0])
    fc.add_constraint(sk, "Horizontal", ids[2])
    fc.add_constraint(sk, "Vertical", ids[1])
    fc.add_constraint(sk, "Vertical", ids[3])
    fc.call(sk, "renameConstraint", fc.add_constraint(sk, "DistanceX", ids[0], 1, ids[0], 2, float(w)), f"{name}_Width")
    fc.call(
        sk, "renameConstraint", fc.add_constraint(sk, "DistanceY", ids[1], 1, ids[1], 2, float(h)), f"{name}_Height"
    )
    _at(sk, ids[0], 1, x0, y0, f"{name}_Corner")


def _circle(sk: Any, cx: float, cy: float, d: float, name: str) -> None:
    geo = fc.call(sk, "addGeometry", Part.Circle(Vector(cx, cy, 0.0), Vector(0, 0, 1), d / 2.0), False)
    fc.call(sk, "renameConstraint", fc.add_constraint(sk, "Diameter", geo, float(d)), f"{name}_Diameter")
    _at(sk, geo, 3, cx, cy, f"{name}_Centre")


def _outline(sk: Any, outline: dict[str, Any], name: str) -> None:
    shape = outline["shape"]
    if shape == "rect":
        _rect(sk, outline["cx"], outline["cy"], outline["w"], outline["h"], name)
    elif shape == "circle":
        _circle(sk, outline["cx"], outline["cy"], outline["d"], name)
    else:
        _polygon(sk, [tuple(p) for p in outline["points"]], name)


def _sketch(fc_body: Any, label: str, plane: dict[str, Any], draw: Any) -> Any:
    sk = fc.new_in_body(fc_body, "Sketcher::SketchObject", "Sketch", label=label)
    fc.set_prop(sk, "MapMode", "Deactivated")
    fc.set_prop(sk, "Placement", _plane_placement(plane))
    draw(sk)
    return sk


# --- features ------------------------------------------------------------------------------------------------------


def _feature_objects(fc_body: Any, label: str, feat: dict[str, Any]) -> list[Any]:
    """The FreeCAD objects of one feature, in creation order (the last is the one the body's Tip becomes)."""
    kind, name = feat["kind"], feat["name"]
    if kind == "pad":
        sk = _sketch(
            fc_body,
            f"{label}_{name}_sketch",
            feat["plane"],
            lambda s: (
                _outline(s, feat["outline"], "Outline"),
                [_circle(s, h["cx"], h["cy"], h["d"], f"Hole{i + 1}") for i, h in enumerate(feat.get("holes", ()))],
            ),
        )
        pad = fc.new_in_body(fc_body, "PartDesign::Pad", "Pad", label=f"{label}_{name}")
        fc.set_prop(pad, "Profile", sk)
        fc.set_prop(pad, "Type", "Length")
        fc.set_prop(pad, "Length", float(feat["depth"]))
        return [sk, pad]
    if kind == "pocket":

        def draw(s: Any) -> None:
            for i, c in enumerate(feat.get("circles", ())):
                _circle(s, c["cx"], c["cy"], c["d"], f"Hole{i + 1}")
            for i, r in enumerate(feat.get("rects", ())):
                _rect(s, r["cx"], r["cy"], r["w"], r["h"], f"Cut{i + 1}")

        sk = _sketch(fc_body, f"{label}_{name}_sketch", feat["plane"], draw)
        pocket = fc.new_in_body(fc_body, "PartDesign::Pocket", "Pocket", label=f"{label}_{name}")
        fc.set_prop(pocket, "Profile", sk)
        fc.set_prop(pocket, "Type", "Length")
        fc.set_prop(pocket, "Length", float(feat["depth"]))
        return [sk, pocket]
    if kind == "revolve":
        plane = {"origin": (0.0, 0.0, 0.0), "normal": (0.0, -1.0, 0.0), "x": (1.0, 0.0, 0.0)}
        sk = _sketch(
            fc_body,
            f"{label}_{name}_sketch",
            plane,
            lambda s: _polygon(s, [tuple(p) for p in feat["profile"]], "Profile"),
        )
        rev = fc.new_in_body(fc_body, "PartDesign::Revolution", "Revolution", label=f"{label}_{name}")
        fc.set_prop(rev, "Profile", sk)
        fc.set_prop(rev, "ReferenceAxis", (sk, ["V_Axis"]))
        fc.set_prop(rev, "Angle", 360.0)
        return [sk, rev]
    if kind == "sphere":
        type_name = "PartDesign::SubtractiveSphere" if feat.get("subtract") else "PartDesign::AdditiveSphere"
        ball = fc.new_in_body(fc_body, type_name, "Sphere", label=f"{label}_{name}")
        fc.set_prop(ball, "Radius", float(feat["radius"]))
        fc.set_prop(ball, "MapMode", "Deactivated")
        fc.set_prop(ball, "Placement", Placement(_vec(feat["center"]), Rotation()))
        return [ball]
    if kind == "helix":
        axis = _vec(feat["axis"])
        rot = _basis((1, 0, 0) if abs(axis.x) < 0.9 else (0, 1, 0), (axis.x, axis.y, axis.z))
        origin = _vec(feat["origin"])
        # the profile (a circle of the wire) lies in a plane through the axis, at the coil radius from it
        plane = {
            "origin": tuple(origin),
            "normal": tuple(rot.multVec(Vector(0, 1, 0))),
            "x": tuple(rot.multVec(Vector(1, 0, 0))),
        }
        sk = _sketch(
            fc_body,
            f"{label}_{name}_sketch",
            plane,
            lambda s: _circle(s, feat["coil_d"] / 2.0, 0.0, feat["wire_d"], "Wire"),
        )
        line = fc.new_in_body(fc_body, "PartDesign::Line", "HelixAxis", label=f"{label}_{name}_axis")
        fc.set_prop(line, "MapMode", "Deactivated")
        fc.set_prop(line, "Placement", Placement(origin, Rotation(Vector(0, 0, 1), axis)))
        helix = fc.new_in_body(fc_body, "PartDesign::AdditiveHelix", "Helix", label=f"{label}_{name}")
        fc.set_prop(helix, "Profile", sk)
        fc.set_prop(helix, "ReferenceAxis", (line, [""]))
        fc.set_prop(helix, "Mode", "pitch-height-angle")
        fc.set_prop(helix, "Pitch", float(feat["pitch"]))
        fc.set_prop(helix, "Height", float(feat["height"]))
        return [sk, line, helix]
    raise invalid_request(f"unknown feature kind {kind!r}", None)


def set_catalog_properties(obj: Any, catalog: dict[str, Any] | None) -> None:
    """The reference of the part as properties of its body (visible in the property editor, read by the bill of materials)."""
    if not catalog:
        return
    for prop, key in CATALOG_PROPERTIES:
        value = str(catalog.get(key) or "")
        if not value:
            continue
        if prop not in obj.PropertiesList:
            obj.addProperty("App::PropertyString", prop, "Cotaforge", f"Catalogue {key} of this part")
        setattr(obj, prop, value)


def build_part(ctx: oc.OpContext, spec: dict[str, Any], label: str, expected: list[tuple[Any, Any, str]]) -> Any:
    """One body for one part spec. ``expected`` collects ``(fc_body, volume, role)`` for the post check."""
    doc = ctx.doc
    body = oc.new_partdesign_body(doc, ctx.feature)
    fc_body = doc.fc_doc.getObject(body.fc_body)
    fc.set_label(fc_body, label)
    fc.set_prop(fc_body, "Placement", frame_placement(spec["frame"]))
    last: Any = None
    for feat in spec["features"]:
        objects = _feature_objects(fc_body, label, feat)
        for o in objects:
            ctx.feature.fc_names.append(o.Name)
            ctx.check_objects.append(o)
        last = objects[-1]
    fc.set_prop(fc_body, "Tip", last)
    set_catalog_properties(fc_body, spec.get("catalog"))
    ctx.bodies.append(body)
    if ctx.feature.fc_main is None:
        ctx.feature.fc_main = last.Name
    if spec.get("volume_mm3") is not None:
        expected.append((fc_body, float(spec["volume_mm3"]), str(spec["role"])))
    return body


def check_volumes(expected: list[tuple[Any, float, str]], op: str) -> None:
    """Every body one valid solid with the volume its numbers give."""
    for fc_body, volume, role in expected:
        shape = fc_body.Shape
        solids = [] if shape.isNull() else list(shape.Solids)
        if len(solids) != 1 or not shape.isValid():
            raise unrecoverable(geometry_failed(f"{role}: the body is not one valid solid ({len(solids)} solids)", op))
        actual = float(shape.Volume)
        if abs(actual - volume) > VOLUME_TOL * volume:
            raise unrecoverable(
                geometry_failed(
                    f"{role}: volume {actual:.6g} mm3, the catalogue numbers give {volume:.6g} mm3 "
                    f"({100 * (actual - volume) / volume:+.3f} %)",
                    op,
                )
            )


def reply_lines(built: list[tuple[str, str, dict[str, Any] | None]]) -> list[str]:
    """``bodies: role=body:N, ...`` then one line per catalogue reference (with how many bodies carry it): short enough
    for a kit of two dozen bodies to fit in the reply."""
    lines = ["bodies: " + ", ".join(f"{role}={body_id}" for role, body_id, _ in built)]
    seen: dict[str, list[str]] = {}
    for role, _body_id, catalog in built:
        if catalog:
            key = f"{catalog.get('family', '')} {catalog.get('reference', '')}"
            seen.setdefault(key, []).append(str(catalog.get("size", "")) or role)
    for key, sizes in seen.items():
        lines.append(
            f"{key} x{len(sizes)}: "
            + (sizes[0] if len(set(sizes)) == 1 else "; ".join(sizes[:3]) + ("; ..." if len(sizes) > 3 else ""))
        )
    return lines


def mould_component(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    op = operation_id_of(params)
    parts = list(params.get("parts") or ())
    if not parts:
        raise invalid_request("mould_component needs at least one part", op)
    made: list[tuple[str, str, dict[str, Any] | None]] = []
    expected: list[tuple[Any, float, str]] = []

    def build(ctx: oc.OpContext) -> None:
        for number, spec in enumerate(parts):
            label = ctx.label() if number == 0 else ctx.label(str(spec["role"]))
            body = build_part(ctx, spec, label, expected)
            made.append((str(spec["role"]), body.id, spec.get("catalog")))
        ctx.post_check = lambda: check_volumes(expected, op)

    result = oc.run_op(doc, "mould_component", params, build)
    result["text"] = "\n".join(reply_lines(made))
    return result
