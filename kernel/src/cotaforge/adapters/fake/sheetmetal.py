"""Sheet metal in the fake adapter: ``base_flange``, ``edge_flange`` and ``unfold`` (adapters/fake/sheetmetal.py).

A stand-in with analytic answers, not a bender. The sheet is the extrusion of its (hole-free) outline; an edge flange
is a straight wall of the volume the bend + leg - replaced strip closed form says (no bend geometry is built), standing
on the sheet's big face at one whole side of a rectangular outline and bent at any angle only in its volume; the flat
pattern's size is the sheet's extent plus the flat gain of each flange. The forms are written out here on purpose, apart
from ``verification.formulas``: two independent copies that must agree are a check on both.

What the fake refuses (``not_supported``, like the arcs it cannot sketch): holes in the sheet, outlines that are not
rectangles, flanges that are not on a whole side of the sheet, and an unfold of anything else.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from cotaforge.adapters.fake.builders import Build, build_prism, invalid_request, not_supported
from cotaforge.adapters.fake.model import FakeDocument, FakeFeature, vadd, vcross, vdot, vlength, vscale, vsub, vunit
from cotaforge.contract import (
    BaseFlangeRequest,
    EdgeFlangeRequest,
    ExtrudeRequest,
    FlatPattern,
    OperationResult,
    Request,
    SketchFrame,
    UnfoldRequest,
    Vec3,
)

if TYPE_CHECKING:
    from cotaforge.adapters.fake.adapter import FakeAdapter

SHEET_METAL_TOOLS = frozenset({"base_flange", "edge_flange", "unfold"})
SIDE_TOLERANCE = 1e-6


def gain(angle: float, t: float, r: float, k: float, length: float, inside: bool, outer: bool) -> float:
    """What a flange adds to the flat length of the sheet across its edge (bend allowance + leg, less the strip a
    bend inside the footprint replaces)."""
    setback = (r + t) * math.tan(math.radians(angle) / 2.0)
    leg = length - setback if outer else length
    total = math.radians(angle) * (r + k * t) + leg
    return total - (r + t) if inside else total


def volume(width: float, t: float, angle: float, r: float, length: float, inside: bool, outer: bool) -> float:
    setback = (r + t) * math.tan(math.radians(angle) / 2.0)
    leg = length - setback if outer else length
    bend = math.radians(angle) * (r + t / 2.0)
    return width * t * (bend + leg - ((r + t) if inside else 0.0))


def _sheet_feature(doc: FakeDocument, body: str) -> FakeFeature | None:
    return next((f for f in doc.features if f.kind == "base_flange" and body in f.created_bodies), None)


def _plate(doc: FakeDocument, base: FakeFeature) -> tuple[SketchFrame, tuple[float, float, float, float], float]:
    """The sheet's sketch frame, its rectangle ``(u0, v0, u1, v1)`` and its thickness."""
    sketch = doc.sketches[str(base.sketch)]
    (u0, v0), (u1, v1) = sketch.profiles[0].bbox_uv
    return sketch.frame, (u0, v0, u1, v1), float(base.parameters["thickness_mm"])


def _require(adapter: FakeAdapter, tool: str, request: Request) -> None:
    """A call that arrives although the capability is off says why, never silently builds."""
    if not adapter._sheet_metal:
        raise not_supported(f"{tool}: {adapter.unavailable_tools()[tool]}", request.operation_id)


def base_flange(adapter: FakeAdapter, request: BaseFlangeRequest) -> OperationResult:
    _require(adapter, "base_flange", request)
    doc = adapter._doc(request)
    sketch = adapter._defined_sketch(doc, request.sketch.id, request)
    if len(sketch.profiles) != 1:
        raise not_supported(
            "the fake models a sheet without holes: its sketch must hold one closed profile; run it on FreeCAD",
            request.operation_id,
        )
    profile = sketch.profiles[0]
    (u0, v0), (u1, v1) = profile.bbox_uv
    if abs(profile.area_mm2 - (u1 - u0) * (v1 - v0)) > 1e-9 * max(1.0, profile.area_mm2):
        raise not_supported("the fake models a rectangular sheet only; run it on FreeCAD", request.operation_id)
    result = adapter.extrude(
        ExtrudeRequest(
            operation_id=request.operation_id,
            sketch=request.sketch,
            depth_mm=request.thickness_mm,
            direction=request.direction,
            merge=False,
        )
    )
    feature = doc.features[-1]
    feature.kind = "base_flange"
    feature.name = feature.name.replace("extrude", "base_flange", 1)
    feature.parameters = {
        "sketch": sketch.id,
        "thickness_mm": float(request.thickness_mm),
        "direction": request.direction.value,
    }
    return result


def edge_flange(adapter: FakeAdapter, request: EdgeFlangeRequest) -> OperationResult:
    _require(adapter, "edge_flange", request)
    doc = adapter._doc(request)
    edge = adapter._edge(doc, request.edge.id, request)
    if edge.start is None or edge.end is None:
        raise invalid_request(f"edge {edge.id} is not a straight edge", request.operation_id)
    faces = [doc.faces[face_id] for face_id in edge.faces]
    big = max(faces, key=lambda face: face.area_mm2)
    base = _sheet_feature(doc, big.body)
    if base is None or big.normal is None:
        raise invalid_request(
            f"edge_flange works on a sheet-metal body (made by base_flange); {big.body} is not one",
            request.operation_id,
        )
    frame, (u0, v0, u1, v1), t = _plate(doc, base)
    a = (vdot(vsub(edge.start, frame.origin), frame.u), vdot(vsub(edge.start, frame.origin), frame.v))
    b = (vdot(vsub(edge.end, frame.origin), frame.u), vdot(vsub(edge.end, frame.origin), frame.v))
    if abs(a[1] - b[1]) <= SIDE_TOLERANCE and abs(abs(a[0] - b[0]) - (u1 - u0)) <= SIDE_TOLERANCE:
        axis, position, sides = "v", a[1], (v0, v1)
    elif abs(a[0] - b[0]) <= SIDE_TOLERANCE and abs(abs(a[1] - b[1]) - (v1 - v0)) <= SIDE_TOLERANCE:
        axis, position, sides = "u", a[0], (u0, u1)
    else:
        raise not_supported(
            "the fake bends a whole side of the sheet's rectangle only; run it on FreeCAD", request.operation_id
        )
    side = next((i for i, value in enumerate(sides) if abs(position - value) <= SIDE_TOLERANCE), None)
    if side is None:
        raise not_supported("the fake bends a whole side of the sheet's rectangle only", request.operation_id)
    if any(
        f.kind == "edge_flange" and f.parameters.get("axis") == axis and f.parameters.get("side") == side
        for f in doc.features
        if big.body in f.modified_bodies
    ):
        raise invalid_request("that side of the sheet already has a flange", request.operation_id)
    inside, outer = request.position == "material_inside", request.length_from == "outer_sharp"
    width = vlength(vsub(edge.end, edge.start))
    added = volume(width, t, request.angle_deg, request.bend_radius_mm, request.length_mm, inside, outer)
    height = added / (width * t)
    if height <= 1e-9:
        raise invalid_request(
            "the flange adds no material (its leg is shorter than the strip the bend replaces); the fake cannot "
            "model it",
            request.operation_id,
        )

    def build(feature: FakeFeature) -> None:
        feature.parameters["axis"] = axis
        feature.parameters["side"] = side
        direction = vunit(vsub(edge.end, edge.start))
        up = big.normal
        into = vunit(vcross(up, direction))
        if vdot(into, vsub(big.center, edge.start)) < 0:
            into = vscale(into, -1.0)
        corner_b = vadd(edge.start, vscale(into, t))
        loop: list[Vec3] = [
            edge.start,
            corner_b,
            vadd(corner_b, vscale(up, height)),
            vadd(edge.start, vscale(up, height)),
        ]
        if vdot(vcross(vsub(loop[1], loop[0]), vsub(loop[2], loop[1])), direction) < 0:
            loop.reverse()
        build_prism(Build(doc, feature, big.body), loop, direction, width, 1)

    return adapter._construct(doc, "edge_flange", request, build)


def unfold(adapter: FakeAdapter, request: UnfoldRequest) -> OperationResult:
    _require(adapter, "unfold", request)
    doc = adapter._doc(request)
    face, _ = adapter._locate_face(doc, request.face.id, request)
    base = _sheet_feature(doc, face.body)
    if base is None:
        raise invalid_request(
            f"unfold works on a sheet-metal body (made by base_flange); {face.body} is not one", request.operation_id
        )
    _, (u0, v0, u1, v1), t = _plate(doc, base)
    extent = {"u": u1 - u0, "v": v1 - v0}
    blank = extent["u"] * extent["v"]
    for flange in (f for f in doc.features if f.kind == "edge_flange" and face.body in f.modified_bodies):
        p = flange.parameters
        axis = str(p["axis"])
        grown = gain(
            float(p["angle_deg"]),
            t,
            float(p["bend_radius_mm"]),
            request.k_factor,
            float(p["length_mm"]),
            p["position"] == "material_inside",
            p["length_from"] == "outer_sharp",
        )
        across = (u1 - u0) if axis == "v" else (v1 - v0)  # the flange is as wide as the side it stands on
        blank += grown * across
        extent[axis] += grown
    first, second = sorted(extent.values(), reverse=True)
    flat = FlatPattern(size_mm=(first, second), thickness_mm=t, area_mm2=blank, volume_mm3=blank * t, valid=True)

    def build(feature: FakeFeature) -> None:  # the flat pattern builds nothing in the solid
        feature.parameters["fixed_face"] = face.id

    result = adapter._construct(doc, "unfold", request, build)
    return result.model_copy(update={"flat_pattern": flat})


__all__ = ["SHEET_METAL_TOOLS", "base_flange", "edge_flange", "unfold"]
