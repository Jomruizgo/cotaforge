# ruff: noqa: E501
"""``external_thread``: a cosmetic ISO metric thread on the outside cylinder of a shaft end (the adapter's own operation,
``adapters/freecad/external_thread.py`` is its model).

It adds no geometry. The thread is a record of the part: a feature of the registry (kind ``thread``, with the numbers the
sheet needs) and an object of the document tree, a ``App::FeaturePython`` with no shape and properties in the
``Cotaforge`` group (``thread_records.py``), so that it survives a save and the engineer sees it in the model tree. The
sheet reads it with ``thread_records.read_threads`` (the ISO 6410 representation, the callout and the thread requirement
of the drawing).
"""

from typing import Any

import fc
import geometry as g
import holes
import ops_common as oc
import registry as reg
import thread_records as records
from protocol import invalid_request
from queries import _face_shape, face_descriptor

KIND = "thread"
MAJOR_TOLERANCE_MM = 0.05  # the cylinder is the thread blank at its nominal diameter
PARALLEL = 1.0 - 1e-6
LENGTH_TOLERANCE_MM = 1e-6


def _projected(point: g.Vec3, origin: g.Vec3, axis: g.Vec3) -> float:
    return g.dot(g.sub(point, origin), axis)


def external_thread(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    op = str(params.get("operation_id", ""))
    inner = params.get("params") or {}
    cyl_id = reg.handle_id(inner["face"], op, "face")
    end_id = reg.handle_id(inner["end"], op, "end")
    size, major, pitch, minor = (
        str(inner["size"]),
        float(inner["major_mm"]),
        float(inner["pitch_mm"]),
        float(inner["minor_mm"]),
    )
    length = float(inner["length_mm"])
    cyl_rec, cyl_comp = reg.resolve_face(doc, cyl_id, op)
    end_rec, end_comp = reg.resolve_face(doc, end_id, op)
    if cyl_comp is not None or end_comp is not None:
        raise invalid_request("external_thread works on the faces of a part: open the part that has the shaft", op)
    cyl, end = face_descriptor(doc, cyl_rec, None), face_descriptor(doc, end_rec, None)
    if cyl["surface"] != "cylinder" or cyl["axis"] is None or cyl["radius"] is None:
        raise invalid_request(
            f"face {cyl_id} is not a cylinder ({cyl['surface']}): name the round face of the shaft end", op
        )
    if end["surface"] != "plane" or end["normal"] is None:
        raise invalid_request(
            f"end {end_id} is not a flat face ({end['surface']}): name the flat end face of the shaft", op
        )
    axis = g.unit(g.vec(cyl["axis"]["direction"]))
    origin = g.vec(cyl["axis"]["origin"])
    shape = _face_shape(doc, cyl_rec, None)
    if holes._is_concave(shape):  # noqa: SLF001 - the one definition of "the material lies outside the face"
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
        raise invalid_request(
            f"end {end_id} is not square to the axis of {cyl_id}: it is not the end face of that shaft", op
        )
    stretch = [_projected(g.vec(v.Point), origin, axis) for v in shape.Vertexes]
    low, high = min(stretch), max(stretch)
    at = _projected(g.vec(end["center"]), origin, axis)
    placed = records.end_placement(at, low, high, major)
    if placed is None:
        raise invalid_request(
            f"end {end_id} is not at an end of {cyl_id} (the end face is {at - low:.4g} mm from one end and "
            f"{high - at:.4g} mm from the other): the thread starts from the end face of the shaft",
            op,
        )
    sense, start, room = placed
    if length > room + LENGTH_TOLERANCE_MM:
        raise invalid_request(
            f"length_mm {length:.4g} is longer than the shaft from {end_id} to the end of {cyl_id} ({room:.4g} mm): the "
            "thread runs from the end face along the shaft and stops where the cylinder stops",
            op,
        )
    start_point = g.add(origin, g.scale(axis, start))
    direction = g.scale(axis, sense)

    def build(ctx: oc.OpContext) -> None:
        obj = fc.add_object(
            doc.fc_doc, records.OBJECT_TYPE, "ExternalThread", label=f"{records.LABEL_PREFIX}_{ctx.feature.label}"
        )
        for name, (kind, text) in records.PROPERTIES.items():
            obj.addProperty(kind, name, records.GROUP, text)
        fc.set_prop(obj, "ThreadSize", size)
        fc.set_prop(obj, "ThreadMajor", major)
        fc.set_prop(obj, "ThreadPitch", pitch)
        fc.set_prop(obj, "ThreadMinor", minor)
        fc.set_prop(obj, "ThreadLength", length)
        fc.set_prop(obj, "ThreadStart", g.fcvec(start_point))
        fc.set_prop(obj, "ThreadDirection", g.fcvec(direction))
        fc.set_prop(obj, "ThreadCylinder", cyl_id)
        fc.set_prop(obj, "ThreadEndFace", end_id)
        fc.set_prop(obj, "ThreadClass", str(inner.get("tolerance_class") or ""))
        ctx.feature.fc_names.append(obj.Name)
        ctx.feature.fc_main = obj.Name
        ctx.feature.label = str(obj.Label)  # the feature tree says what the model tree says: ``thread_<id>``

    flat = {"operation_id": op, **inner}
    result = oc.run_op(doc, KIND, flat, build)
    result["warnings"] = [
        *result.get("warnings", []),
        f"cosmetic thread {size} x {length:g} mm: no geometry was added; the shaft keeps its volume",
    ]
    return result
