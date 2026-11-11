"""copy_body with a scale: ``Shape.scale`` of the placed shape about a world point, then the rigid transform. Exact in
the kernel: volume s^3, surface s^2, centroid ``about + s (c - about)`` and then moved. Run on FreeCAD 1.0.2 and
1.1.3."""

import math
from typing import Any

import geometry as g
from harness import (
    IDENTITY,
    call,
    close,
    expect_error,
    extrude,
    faces_of,
    new_document,
    op,
    parallel,
    rect,
    select_face,
    sketch,
    vclose,
    volume,
)

ROT90Z = [[0, -1, 0], [1, 0, 0], [0, 0, 1]]
PLATE_VOLUME = 48000.0
HOLE_VOLUME = math.pi * 9.0 * 8.0


def _mass(handle: dict[str, str]) -> dict[str, Any]:
    return call("mass_properties", op("q", body=handle))


def _bbox(handle: dict[str, str]) -> dict[str, Any]:
    return call("bounding_box", op("q", body=handle))


def _copy(
    source: dict[str, str], scale: float, about: Any = (0, 0, 0), transform: Any = None, op_id: str = "cp"
) -> dict:
    return call(
        "copy_body",
        op(op_id, source=source, transform=transform or IDENTITY, scale=scale, scale_about=list(about)),
    )


def _plate_with_hole() -> dict[str, str]:
    sk = sketch("s0", "XY", [rect("r", (50, 30), 100, 60)])
    base = extrude("base", sk, 8)
    top = select_face(faces_of(base["feature"]), normal=(0, 0, 1))
    call(
        "hole",
        op(
            "h",
            face=top["handle"],
            position=[-40.0, -20.0],
            standard="simple",
            size=None,
            diameter_mm=6,
            end_condition="through_all",
            depth_mm=None,
        ),
    )
    return base["created_bodies"][0]


def check_copy_scale_a_box_has_the_cubed_volume_and_squared_surface() -> None:
    new_document("sc_box")
    source = call("box", op("b", size=[10, 20, 30], transform=IDENTITY))["created_bodies"][0]
    twin = _copy(source, 2.0)["created_bodies"][0]
    assert close(_mass(twin)["volume_mm3"], 8 * 6000.0, 1e-6)
    assert close(_mass(twin)["surface_mm2"], 4 * 2 * (200 + 300 + 600), 1e-6)
    assert vclose(_mass(twin)["centroid"], (10, 20, 30), 1e-9)  # (5, 10, 15) scaled about the origin
    assert vclose(_bbox(twin)["min"], (0, 0, 0), 1e-9) and vclose(_bbox(twin)["max"], (20, 40, 60), 1e-9)
    assert close(_mass(source)["volume_mm3"], 6000.0, 1e-9)  # the source is untouched
    assert close(volume(), 6000.0 + 48000.0, 1e-6)


def check_copy_scale_about_a_point_keeps_that_point_fixed() -> None:
    new_document("sc_about")
    source = call("box", op("b", size=[10, 20, 30], transform=IDENTITY))["created_bodies"][0]
    twin = _copy(source, 0.5, about=(5, 10, 15))["created_bodies"][0]
    assert vclose(_mass(twin)["centroid"], (5, 10, 15), 1e-9)
    assert vclose(_bbox(twin)["min"], (2.5, 5, 7.5), 1e-9) and vclose(_bbox(twin)["max"], (7.5, 15, 22.5), 1e-9)
    assert close(_mass(twin)["volume_mm3"], 6000.0 / 8, 1e-9)


def check_copy_scale_comes_before_the_rotation_and_the_offset() -> None:
    """p' = R (about + s (p - about)) + t, with R a quarter turn about Z through the origin."""
    new_document("sc_order")
    source = call("box", op("b", size=[10, 20, 30], transform=IDENTITY))["created_bodies"][0]
    about = (2.0, 3.0, 4.0)
    s = 1.5
    t = (100.0, 0.0, 0.0)
    twin = _copy(source, s, about=about, transform={"origin": list(t), "rotation": ROT90Z})["created_bodies"][0]
    scaled_centroid = g.add(about, g.scale(g.sub((5.0, 10.0, 15.0), about), s))
    expected = g.add((-scaled_centroid[1], scaled_centroid[0], scaled_centroid[2]), t)
    assert vclose(_mass(twin)["centroid"], expected, 1e-9), (_mass(twin)["centroid"], expected)
    assert close(_mass(twin)["volume_mm3"], s**3 * 6000.0, 1e-6)


def check_copy_scale_a_shrinkage_allowance_is_exact_for_a_cylinder() -> None:
    new_document("sc_cyl")
    cyl = call("cylinder", op("c", diameter_mm=20, height_mm=40, axis={"origin": [0, 0, 0], "direction": [0, 0, 1]}))
    source = cyl["created_bodies"][0]
    s = 1.006
    result = _copy(source, s, about=(0, 0, 0), transform={"origin": [60, 0, 0], "rotation": IDENTITY["rotation"]})
    twin = result["created_bodies"][0]
    base = math.pi * 100 * 40
    assert close(_mass(twin)["volume_mm3"], s**3 * base, 1e-9 * base)
    assert close(_mass(twin)["surface_mm2"], s**2 * (2 * math.pi * 10 * 40 + 2 * math.pi * 100), 1e-6)
    assert vclose(_mass(twin)["centroid"], (60, 0, 20 * s), 1e-9)
    sides = [f for f in faces_of(result["feature"], "created") if f["surface"] == "cylinder"]
    assert len(sides) == 1 and close(sides[0]["radius"], 10 * s, 1e-9), sides
    assert parallel(sides[0]["axis"]["direction"], (0, 0, 1))


def check_copy_scale_a_partdesign_body_keeps_its_hole_and_the_hole_keeps_its_sense() -> None:
    new_document("sc_pd")
    source = _plate_with_hole()
    s = 2.0
    result = _copy(source, s, about=(10, 10, 0))
    twin = result["created_bodies"][0]
    assert close(_mass(twin)["volume_mm3"], s**3 * (PLATE_VOLUME - HOLE_VOLUME), 1e-6)
    bores = [f for f in faces_of(result["feature"], "created") if f["surface"] == "cylinder"]
    assert len(bores) == 1 and close(bores[0]["radius"], 3.0 * s, 1e-9), bores
    assert parallel(bores[0]["axis"]["direction"], (0, 0, -1)), bores[0]["axis"]  # the hole's intended sense
    assert close(_mass(source)["volume_mm3"], PLATE_VOLUME - HOLE_VOLUME, 1e-6)


def check_copy_scale_a_moved_partdesign_body_is_scaled_where_it_stands() -> None:
    new_document("sc_pd_moved")
    source = _plate_with_hole()
    call("move_body", op("mv", body=source, transform={"origin": [200, 50, 7], "rotation": ROT90Z}))
    centroid = _mass(source)["centroid"]
    box = _bbox(source)
    s = 1.25
    about = (180.0, 40.0, 0.0)
    twin = _copy(source, s, about=about)["created_bodies"][0]
    expected = g.add(about, g.scale(g.sub(centroid, about), s))
    assert vclose(_mass(twin)["centroid"], expected, 1e-8), (_mass(twin)["centroid"], expected)
    assert close(_mass(twin)["volume_mm3"], s**3 * (PLATE_VOLUME - HOLE_VOLUME), 1e-6)
    tb = _bbox(twin)
    assert vclose(tb["min"], g.add(about, g.scale(g.sub(box["min"], about), s)), 1e-8)
    assert vclose(tb["max"], g.add(about, g.scale(g.sub(box["max"], about), s)), 1e-8)


def check_copy_scale_of_one_is_a_rigid_copy_and_a_bad_scale_is_refused() -> None:
    new_document("sc_one")
    source = call("box", op("b", size=[10, 20, 30], transform=IDENTITY))["created_bodies"][0]
    twin = _copy(source, 1.0, about=(3, 3, 3))["created_bodies"][0]
    assert close(_mass(twin)["volume_mm3"], 6000.0, 1e-9) and vclose(_mass(twin)["centroid"], (5, 10, 15), 1e-9)
    for bad in (0.0, -1.0):
        expect_error(
            "invalid_request",
            "copy_body",
            op("cp", source=source, transform=IDENTITY, scale=bad, scale_about=[0, 0, 0]),
        )
    assert close(volume(), 12000.0, 1e-6)


def check_copy_scale_undo_removes_the_scaled_copy() -> None:
    new_document("sc_undo")
    source = call("box", op("b", size=[10, 20, 30], transform=IDENTITY))["created_bodies"][0]
    result = _copy(source, 2.0)
    undone = call("undo_last", op("undo"))
    assert undone["reverted"] == result["feature"], undone
    assert close(volume(), 6000.0, 1e-6)
    expect_error("invalid_reference", "mass_properties", op("q", body=result["created_bodies"][0]))
