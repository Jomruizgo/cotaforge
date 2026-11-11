"""What is exactly known about a sheet-metal part, from the plan alone (verification/sheetmetal.py).

``ExpectedComputer`` keeps one plain-dict record per sheet (the state is copied for ``undo_to``, so it holds only
containers and numbers) and asks this module for the closed forms. The record is EXACT while the part is the simple
case the forms cover: a base flange whose outline is a rectangle, and edge flanges that each take a whole straight
side of that rectangle, at most one per side. Anything else (a flange on a flange, on part of a side, a second
flange on the same side, an outline that is not a rectangle) clears ``exact``: the volume and the flat size are then
only reported, never asserted.

The forms agree with the SheetMetal add-on to 1e-4 mm over a sweep of angles, positions, length references, radii and
thicknesses (``kernel/tests/integration/freecad/test_sheet_metal.py``).
"""

import math
from collections.abc import Sequence
from typing import Any

from cotaforge.contract import Profile, SketchFrame, Vec3
from cotaforge.verification import formulas as f

Sheet = dict[str, Any]

SIDE_TOLERANCE_MM = 1e-3


class SheetMetalError(ValueError):
    """A sheet-metal request that cannot be built, found while the expectations are computed, before the adapter is
    called: the executor reports it as a plan error with this message."""


def outline_of(profiles: Sequence[Profile]) -> Profile:
    """The sheet's outline: its largest closed profile (every other one is a hole)."""
    return max(profiles, key=lambda p: p.area_mm2)


def blank_area(profiles: Sequence[Profile]) -> float:
    """Area of the sheet's face: the outline minus its holes."""
    outline = outline_of(profiles)
    return outline.area_mm2 - sum(p.area_mm2 for p in profiles if p is not outline)


def new_sheet(frame: SketchFrame, profiles: Sequence[Profile], thickness: float, sign: float) -> Sheet:
    """The record of a base flange: ``sign`` is +1 when the sheet lies on the side of the sketch normal."""
    outline = outline_of(profiles)
    (u0, v0), (u1, v1) = outline.bbox_uv
    is_rect = abs(outline.area_mm2 - (u1 - u0) * (v1 - v0)) <= 1e-9 * max(1.0, outline.area_mm2)
    return {
        "t": thickness,
        "sign": sign,
        "frame": frame,
        "rect": (u0, v0, u1, v1) if is_rect else None,
        "holes": [
            (p.bbox_uv[0][0], p.bbox_uv[0][1], p.bbox_uv[1][0], p.bbox_uv[1][1]) for p in profiles if p is not outline
        ],
        "flanges": [],
        "exact": is_rect,
    }


def _local(frame: SketchFrame, point: Vec3) -> tuple[float, float, float]:
    rel = f.sub(point, frame.origin)
    return f.dot(rel, frame.u), f.dot(rel, frame.v), f.dot(rel, frame.n)


def add_flange(
    sheet: Sheet,
    start: Vec3 | None,
    end: Vec3 | None,
    *,
    angle_deg: float,
    radius: float,
    length: float,
    material_inside: bool,
    from_outer_sharp: bool,
) -> float | None:
    """Record an edge flange on the sheet and return the volume it adds when that is known exactly (else ``None``).
    Raises ``SheetMetalError`` for a flange that cannot exist: no straight leg left, or a bend longer than the sheet."""
    setback = f.outside_setback(angle_deg, sheet["t"], radius)
    if from_outer_sharp and length <= setback + SIDE_TOLERANCE_MM:
        msg = (
            f"edge_flange length {length:g} mm measured to the outer sharp is not longer than the bend itself "
            f"(outside setback {setback:g} mm at radius {radius:g}, thickness {sheet['t']:g}, {angle_deg:g} deg): "
            "no straight leg is left; lengthen it, or set length_from=leg"
        )
        raise SheetMetalError(msg)
    flange = {
        "angle": angle_deg,
        "radius": radius,
        "length": length,
        "inside": material_inside,
        "outer": from_outer_sharp,
        "axis": None,
        "side": None,
    }
    placed = _place(sheet, start, end, flange)
    sheet["flanges"].append(flange)
    if not placed:
        sheet["exact"] = False
        return None
    if material_inside:
        _check_bend_fits(sheet, flange)
    if material_inside and _hole_in_the_bend(sheet, flange):
        sheet["exact"] = False  # the bend cuts through a hole: nothing is predicted for this sheet any more
        return None
    neighbours = [other for other in sheet["flanges"][:-1] if other["axis"] != flange["axis"]]
    width = _width(sheet, flange["axis"])
    if neighbours or width is None:
        return None  # a corner relief or miter changes the volume in a way the closed form does not cover
    return flange_volume(sheet["t"], width, flange)


def _check_bend_fits(sheet: Sheet, flange: dict[str, Any]) -> None:
    """The bends that go inside the sheet on one axis take ``r + t`` each from its depth there: they must leave some."""
    rect = sheet["rect"]
    if rect is None or flange["axis"] is None:
        return
    u0, v0, u1, v1 = rect
    depth = (v1 - v0) if flange["axis"] == "v" else (u1 - u0)
    taken = sum(
        other["radius"] + sheet["t"]
        for other in sheet["flanges"]
        if other["axis"] == flange["axis"] and other["inside"]
    )
    if taken >= depth - SIDE_TOLERANCE_MM:
        msg = (
            f"the bends that go inside the sheet take {taken:g} mm (radius + thickness each) of its {depth:g} mm: "
            "use a smaller bend radius or position=material_outside"
        )
        raise SheetMetalError(msg)


def _hole_in_the_bend(sheet: Sheet, flange: dict[str, Any]) -> bool:
    """Whether a hole reaches into the strip (``r + t`` deep from its side) a bend inside the sheet replaces."""
    u0, v0, u1, v1 = sheet["rect"]
    strip = flange["radius"] + sheet["t"]
    for hu0, hv0, hu1, hv1 in sheet["holes"]:
        if flange["axis"] == "v":  # an edge along u at v0 or v1: the strip runs across v
            low, high, start, end = hv0, hv1, v0, v1
        else:
            low, high, start, end = hu0, hu1, u0, u1
        if (flange["side"] == 0 and low < start + strip) or (flange["side"] == 1 and high > end - strip):
            return True
    return False


def _place(sheet: Sheet, start: Vec3 | None, end: Vec3 | None, flange: dict[str, Any]) -> bool:
    """Which whole side of the sheet's rectangle the edge is (``axis`` is the direction the flat length grows in)."""
    rect = sheet["rect"]
    if not sheet["exact"] or rect is None or start is None or end is None:
        return False
    frame: SketchFrame = sheet["frame"]
    a, b = _local(frame, start), _local(frame, end)
    t, sign = sheet["t"], sheet["sign"]
    levels = (0.0, sign * t)
    if abs(a[2] - b[2]) > SIDE_TOLERANCE_MM or all(abs(a[2] - level) > SIDE_TOLERANCE_MM for level in levels):
        return False  # not on a face of the sheet's own plate
    u0, v0, u1, v1 = rect
    if abs(a[1] - b[1]) <= SIDE_TOLERANCE_MM and abs(abs(a[0] - b[0]) - (u1 - u0)) <= SIDE_TOLERANCE_MM:
        axis, position, sides = "v", a[1], (v0, v1)  # an edge along u: the flat length grows along v
    elif abs(a[0] - b[0]) <= SIDE_TOLERANCE_MM and abs(abs(a[1] - b[1]) - (v1 - v0)) <= SIDE_TOLERANCE_MM:
        axis, position, sides = "u", a[0], (u0, u1)
    else:
        return False
    which = [i for i, side in enumerate(sides) if abs(position - side) <= SIDE_TOLERANCE_MM]
    if not which or any(other["axis"] == axis and other["side"] == which[0] for other in sheet["flanges"]):
        return False
    flange["axis"], flange["side"] = axis, which[0]
    return True


def _width(sheet: Sheet, axis: str | None) -> float | None:
    rect = sheet["rect"]
    if rect is None or axis is None:
        return None
    u0, v0, u1, v1 = rect
    return (u1 - u0) if axis == "v" else (v1 - v0)  # the flange takes the side across its flat direction


def flange_volume(thickness: float, width: float, flange: dict[str, Any]) -> float:
    """Volume one flange adds: the bend (a ring sector), its straight leg, minus the strip of sheet the bend replaces
    when the bend goes inside the sheet's footprint."""
    angle, radius = flange["angle"], flange["radius"]
    leg = flange["length"] - (f.outside_setback(angle, thickness, radius) if flange["outer"] else 0.0)
    bend = math.radians(angle) * (radius + thickness / 2.0)
    stripped = (radius + thickness) if flange["inside"] else 0.0
    return width * thickness * (bend + leg - stripped)


def flat_size(sheet: Sheet, k_factor: float) -> tuple[float, float] | None:
    """The flat blank's bounding rectangle, longer side first, or ``None`` when the sheet is not the exact case."""
    rect = sheet["rect"]
    if not sheet["exact"] or rect is None:
        return None
    u0, v0, u1, v1 = rect
    extent = {"u": u1 - u0, "v": v1 - v0}
    for flange in sheet["flanges"]:
        if flange["axis"] is None:
            return None
        extent[flange["axis"]] += f.edge_flange_flat_gain(
            flange["angle"],
            sheet["t"],
            flange["radius"],
            k_factor,
            flange["length"],
            material_inside=flange["inside"],
            from_outer_sharp=flange["outer"],
        )
    first, second = sorted(extent.values(), reverse=True)
    return first, second
