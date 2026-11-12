"""``external_thread``: a cosmetic ISO metric thread on the outside cylinder of a shaft end (the adapter's own operation,
``adapters/freecad/external_thread.py`` is its model).

It adds no geometry. The thread is a record of the part: a feature of the registry (kind ``thread``, with the numbers the
sheet needs) and an object of the document tree, a ``App::FeaturePython`` with no shape and properties in the
``Cotaforge`` group, so that it survives a save and the engineer sees it in the model tree. The sheet reads it with
``read_threads`` (the ISO 6410 representation, the callout and the thread requirement of the drawing).
"""

import math
from typing import Any

import fc
import geometry as g
import ops_common as oc
import registry as reg
from protocol import invalid_request
from queries import _face_shape, face_descriptor

KIND = "thread"
OBJECT_TYPE = "App::FeaturePython"
GROUP = "Cotaforge"
LABEL_PREFIX = "thread"
"""The label of the object starts with it (``thread_<id>``): a record of the part that the drawing can find by name too."""
MAJOR_TOLERANCE_MM = 0.05  # the cylinder is the thread blank at its nominal diameter
END_TOLERANCE_MM = 0.01  # the end face is at the end of the cylinder
PARALLEL = 1.0 - 1e-6
LENGTH_TOLERANCE_MM = 1e-6

# property name -> (FreeCAD type, what it says)
_PROPERTIES = {
    "ThreadSize": ("App::PropertyString", "ISO metric designation (M12, M45x1.5)"),
    "ThreadMajor": ("App::PropertyFloat", "Major (nominal) diameter, mm"),
    "ThreadPitch": ("App::PropertyFloat", "Pitch, mm"),
    "ThreadMinor": ("App::PropertyFloat", "Basic minor diameter d1 = d - 1.0825 P, mm"),
    "ThreadLength": ("App::PropertyFloat", "Thread length from the end face, mm"),
    "ThreadStart": ("App::PropertyVector", "Centre of the end face the thread starts from (on the axis), mm"),
    "ThreadDirection": ("App::PropertyVector", "Unit direction along the axis, from the end face into the shaft"),
    "ThreadCylinder": ("App::PropertyString", "The cylindrical face the thread is on (handle at the time it was made)"),
    "ThreadEndFace": ("App::PropertyString", "The end face the thread starts from (handle at the time it was made)"),
}


def _projected(point: g.Vec3, origin: g.Vec3, axis: g.Vec3) -> float:
    return g.dot(g.sub(point, origin), axis)


def _outside(face: Any, axis_origin: g.Vec3, axis: g.Vec3) -> bool:
    """Whether the material is inside the cylinder: the normal of the face points away from the axis at its middle."""
    u0, u1, v0, v1 = face.ParameterRange
    u, v = (u0 + u1) / 2.0, (v0 + v1) / 2.0
    point = g.vec(face.valueAt(u, v))
    normal = g.vec(face.normalAt(u, v))
    along = _projected(point, axis_origin, axis)
    radial = g.sub(point, g.add(axis_origin, g.scale(axis, along)))
    return g.dot(normal, radial) > 0.0


def external_thread(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    op = str(params.get("operation_id", ""))
    inner = params.get("params") or {}
    cyl_id = reg.handle_id(inner["face"], op, "face")
    end_id = reg.handle_id(inner["end"], op, "end")
    size, major, pitch, minor = (str(inner["size"]), float(inner["major_mm"]), float(inner["pitch_mm"]), float(inner["minor_mm"]))
    length = float(inner["length_mm"])
    cyl_rec, cyl_comp = reg.resolve_face(doc, cyl_id, op)
    end_rec, end_comp = reg.resolve_face(doc, end_id, op)
    if cyl_comp is not None or end_comp is not None:
        raise invalid_request("external_thread works on the faces of a part: open the part that has the shaft", op)
    cyl, end = face_descriptor(doc, cyl_rec, None), face_descriptor(doc, end_rec, None)
    if cyl["surface"] != "cylinder" or cyl["axis"] is None or cyl["radius"] is None:
        raise invalid_request(f"face {cyl_id} is not a cylinder ({cyl['surface']}): name the round face of the shaft end", op)
    if end["surface"] != "plane" or end["normal"] is None:
        raise invalid_request(f"end {end_id} is not a flat face ({end['surface']}): name the flat end face of the shaft", op)
    axis = g.unit(g.vec(cyl["axis"]["direction"]))
    origin = g.vec(cyl["axis"]["origin"])
    shape = _face_shape(doc, cyl_rec, None)
    if not _outside(shape, origin, axis):
        raise invalid_request(
            f"face {cyl_id} is a bore (the material is outside it): an external thread goes on a shaft; a thread in a "
            "bore is a hole (op hole, standard iso_tapped)",
            op,
        )
    diameter = 2.0 * float(cyl["radius"])
    if abs(diameter - major) > MAJOR_TOLERANCE_MM:
        raise invalid_request(
            f"the cylinder {cyl_id} is Ø{diameter:.4g} and {size} has a major diameter of {major:.4g}: the shaft end is "
            f"modelled at the nominal diameter of its thread (Ø{major:.4g}); change the cylinder or the size",
            op,
        )
    if abs(g.dot(g.unit(g.vec(end["normal"])), axis)) < PARALLEL:
        raise invalid_request(f"end {end_id} is not square to the axis of {cyl_id}: it is not the end face of that shaft", op)
    stretch = [_projected(g.vec(v.Point), origin, axis) for v in shape.Vertexes]
    low, high = min(stretch), max(stretch)
    at = _projected(g.vec(end["center"]), origin, axis)
    if abs(at - high) <= END_TOLERANCE_MM:
        direction, start = g.scale(axis, -1.0), high
    elif abs(at - low) <= END_TOLERANCE_MM:
        direction, start = axis, low
    else:
        raise invalid_request(
            f"end {end_id} is not at an end of {cyl_id} (the end face is {at - low:.4g} mm from one end and "
            f"{high - at:.4g} mm from the other): the thread starts from the end face of the cylinder",
            op,
        )
    if length > (high - low) + LENGTH_TOLERANCE_MM:
        raise invalid_request(
            f"length_mm {length:.4g} is longer than the cylinder {cyl_id} ({high - low:.4g} mm): the thread runs from "
            "the end face along the shaft and stops where the cylinder stops",
            op,
        )
    start_point = g.add(origin, g.scale(axis, start))

    def build(ctx: oc.OpContext) -> None:
        obj = fc.add_object(doc.fc_doc, OBJECT_TYPE, "ExternalThread", label=f"{LABEL_PREFIX}_{ctx.feature.label}")
        for name, (kind, text) in _PROPERTIES.items():
            obj.addProperty(kind, name, GROUP, text)
        fc.set_prop(obj, "ThreadSize", size)
        fc.set_prop(obj, "ThreadMajor", major)
        fc.set_prop(obj, "ThreadPitch", pitch)
        fc.set_prop(obj, "ThreadMinor", minor)
        fc.set_prop(obj, "ThreadLength", length)
        fc.set_prop(obj, "ThreadStart", g.fcvec(start_point))
        fc.set_prop(obj, "ThreadDirection", g.fcvec(direction))
        fc.set_prop(obj, "ThreadCylinder", cyl_id)
        fc.set_prop(obj, "ThreadEndFace", end_id)
        ctx.feature.fc_names.append(obj.Name)
        ctx.feature.fc_main = obj.Name
        ctx.adopt(obj)

    flat = {"operation_id": op, **inner}
    result = oc.run_op(doc, KIND, flat, build)
    result["warnings"] = [
        *result.get("warnings", []),
        f"cosmetic thread {size} x {length:g} mm: no geometry was added; the shaft keeps its volume",
    ]
    return result


def read_threads(doc: reg.DocumentRec) -> list[dict[str, Any]]:
    """The cosmetic external threads of the part, for the sheet: designation, major and minor diameter, pitch, length, the
    centre of the end face and the direction from it into the shaft (world, mm). Read from the objects of the document, so
    a file the engineer reopens (or edited by hand) says what it holds."""
    found: list[dict[str, Any]] = []
    for obj in doc.fc_doc.Objects:
        if obj.TypeId != OBJECT_TYPE or "ThreadSize" not in obj.PropertiesList:
            continue
        start, direction = obj.ThreadStart, obj.ThreadDirection
        found.append(
            {
                "name": obj.Label,
                "size": str(obj.ThreadSize),
                "major_mm": float(obj.ThreadMajor),
                "pitch_mm": float(obj.ThreadPitch),
                "minor_mm": float(obj.ThreadMinor),
                "length_mm": float(obj.ThreadLength),
                "start": [float(start.x), float(start.y), float(start.z)],
                "direction": [float(direction.x), float(direction.y), float(direction.z)],
            }
        )
    return found


def feature_numbers(rec: reg.FeatureRec) -> dict[str, Any] | None:
    """The same, from the registry feature of the op (for a caller that has the record and no document walk)."""
    if rec.kind != KIND:
        return None
    p = rec.params
    return {"size": p.get("size"), "length_mm": p.get("length_mm"), "major_mm": p.get("major_mm")}


__all__ = ["external_thread", "feature_numbers", "read_threads", "math"]
