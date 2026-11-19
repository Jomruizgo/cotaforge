"""mould_component: catalogue parts as native PartDesign bodies (sketch + pad / pocket / revolution), carrying their
catalogue properties and checked against the volume their numbers give."""

import math

import FreeCAD as App
from harness import call, close, expect_error, new_document, op

PLANE_XY = {"origin": [0.0, 0.0, 0.0], "normal": [0.0, 0.0, 1.0], "x": [1.0, 0.0, 0.0]}


def _frame(origin: tuple = (0.0, 0.0, 0.0), z: tuple = (0.0, 0.0, 1.0), x: tuple = (1.0, 0.0, 0.0)) -> dict:
    return {"origin": list(origin), "z": list(z), "x": list(x)}


def _catalog(ref: str) -> dict:
    return {"family": "DME", "reference": ref, "size": "made-up test part", "source": "test", "note": ""}


def _plate(role: str = "plate", volume: float | None = None) -> dict:
    # 40 x 30 x 10 with a 6 mm hole through and an 8 mm counterbore 3 mm deep from the top
    hole = {"shape": "circle", "cx": 5.0, "cy": 0.0, "d": 6.0}
    seat = {"shape": "circle", "cx": 5.0, "cy": 0.0, "d": 8.0}
    cut = math.pi * 3.0**2 * 7.0 + math.pi * 4.0**2 * 3.0
    return {
        "role": role,
        "frame": _frame(),
        "features": [
            {
                "kind": "pad",
                "name": "Plate",
                "plane": PLANE_XY,
                "outline": {"shape": "rect", "cx": 0.0, "cy": 0.0, "w": 40.0, "h": 30.0},
                "holes": [],
                "depth": 10.0,
            },
            {
                "kind": "pocket",
                "name": "Bore",
                "plane": {**PLANE_XY, "origin": [0.0, 0.0, 10.0]},
                "circles": [hole],
                "rects": [],
                "depth": 10.0,
            },
            {
                "kind": "pocket",
                "name": "Seat",
                "plane": {**PLANE_XY, "origin": [0.0, 0.0, 10.0]},
                "circles": [seat],
                "rects": [],
                "depth": 3.0,
            },
        ],
        "catalog": _catalog("TEST-PLATE"),
        "volume_mm3": 40.0 * 30.0 * 10.0 - cut if volume is None else volume,
    }


def _pin() -> dict:
    d, head, k, length = 6.0, 10.0, 3.0, 50.0
    profile = [[0.0, -k], [head / 2, -k], [head / 2, 0.0], [d / 2, 0.0], [d / 2, length], [0.0, length]]
    return {
        "role": "pin",
        "frame": _frame(origin=(100.0, 0.0, 0.0), z=(0.0, 1.0, 0.0), x=(1.0, 0.0, 0.0)),
        "features": [{"kind": "revolve", "name": "Pin", "profile": profile}],
        "catalog": _catalog("TEST-PIN"),
        "volume_mm3": math.pi * ((head / 2) ** 2 * k + (d / 2) ** 2 * length),
    }


def check_a_part_is_a_partdesign_body_of_named_sketches_pads_and_pockets() -> None:
    new_document("mp_plate")
    result = call("mould_component", op("pl", parts=[_plate()]))
    assert len(result["created_bodies"]) == 1 and result["rebuild_ok"] is True, result
    (body,) = [o for o in App.ActiveDocument.Objects if o.TypeId == "PartDesign::Body"]
    types = [o.TypeId for o in body.Group]
    assert (
        types.count("Sketcher::SketchObject") == 3
        and "PartDesign::Pad" in types
        and types.count("PartDesign::Pocket") == 2
    ), types
    assert body.Tip.TypeId == "PartDesign::Pocket" and body.Tip is body.Group[-1]
    sketch = next(o for o in body.Group if o.TypeId == "Sketcher::SketchObject")
    names = {c.Name for c in sketch.Constraints if c.Name}
    assert {"Outline_Width", "Outline_Height"} <= names, names  # the dimensions the engineer can change
    assert sketch.solve() == 0  # fully defined: nothing left to move


def check_the_body_carries_its_catalogue_reference_as_properties() -> None:
    new_document("mp_props")
    result = call("mould_component", op("pl", parts=[_plate()]))
    (body,) = [o for o in App.ActiveDocument.Objects if o.TypeId == "PartDesign::Body"]
    assert body.CotaforgeCatalogFamily == "DME" and body.CotaforgeCatalogRef == "TEST-PLATE"
    assert body.CotaforgeCatalogSize == "made-up test part" and body.CotaforgeCatalogSource == "test"
    info = call("bodies_of", op("q", feature=result["feature"]))["bodies"][0]
    assert info["catalog"] == "DME TEST-PLATE (made-up test part)", info


def check_a_pin_is_a_revolution_of_a_named_profile_about_its_own_axis() -> None:
    new_document("mp_pin")
    call("mould_component", op("pn", parts=[_pin()]))
    (body,) = [o for o in App.ActiveDocument.Objects if o.TypeId == "PartDesign::Body"]
    assert [o.TypeId for o in body.Group].count("PartDesign::Revolution") == 1
    shape = body.Shape.copy()
    shape.Placement = body.Placement
    box = shape.optimalBoundingBox(False, False)
    assert close(box.YLength, 53.0, 1e-3) and close(
        box.YMin, -3.0, 1e-3
    )  # head 3 below the origin, shaft 50 along +y (the frame's z)
    assert close(box.XLength, 10.0, 1e-3)


def check_two_parts_in_one_step_are_two_bodies_labelled_by_role() -> None:
    new_document("mp_two")
    result = call("mould_component", op("kit", parts=[_plate("first"), _pin()]))
    assert len(result["created_bodies"]) == 2
    labels = sorted(o.Label for o in App.ActiveDocument.Objects if o.TypeId == "PartDesign::Body")
    assert labels == ["kit", "kit_pin"], labels  # the first part is the step's own


def check_numbers_that_do_not_match_the_geometry_are_refused_and_nothing_is_left() -> None:
    new_document("mp_bad")
    expect_error("geometry_failed", "mould_component", op("bad", parts=[_plate(volume=1000.0)]))
    assert not [o for o in App.ActiveDocument.Objects if o.TypeId == "PartDesign::Body"]
    expect_error("invalid_request", "mould_component", op("none", parts=[]))
