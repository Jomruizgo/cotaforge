"""shell: PartDesign::Thickness (Mode Skin) driven straight on the worker. Thickness builds the WALLS alone: inward
(Reversed=True) the cavity is the offset of the body and the outer size stays; outward (Reversed=False) the old body
becomes the cavity and the walls grow outside it. Every expected number is derived by hand (they were measured on
FreeCAD 1.0.2 and 1.1.3 and agree to 1e-9), and the silent no-ops of Thickness (an unchanged shape reported as
"Up-to-date") must come back as errors with nothing left behind."""

import math
from typing import Any

import main
from harness import (
    IDENTITY,
    bbox,
    call,
    close,
    expect_error,
    extrude,
    faces_of,
    new_document,
    op,
    rect,
    select_face,
    sketch,
    vclose,
    volume,
)

BOX = (40.0, 30.0, 20.0)
BOX_VOLUME = 24000.0


def _box(size: tuple[float, float, float] = BOX, name: str = "sh_box") -> dict[str, Any]:
    new_document(name)
    return call("box", op("b", size=list(size), transform=IDENTITY))


def _cylinder(name: str = "sh_cyl", direction: tuple[float, float, float] = (0.0, 0.0, 1.0)) -> dict[str, Any]:
    new_document(name)
    axis = {"origin": [0, 0, 0], "direction": list(direction)}
    return call("cylinder", op("c", diameter_mm=40, height_mm=30, axis=axis))  # R20 H30


def _face(made: dict[str, Any], normal: tuple[float, float, float]) -> dict[str, Any]:
    return select_face(faces_of(made["feature"]), normal=normal)


def _shell(
    faces: list[dict[str, Any]], t: float = 2.0, *, outward: bool = False, join: str = "intersection"
) -> dict[str, Any]:
    params = op("sh", faces=[f["handle"] for f in faces], thickness_mm=t, outward=outward, join=join)
    return call("shell", params)


def _shell_error(code: str, faces: list[Any], t: float = 2.0, join: str = "intersection") -> Any:
    return expect_error(code, "shell", op("sh", faces=faces, thickness_mm=t, outward=False, join=join))


def _body() -> Any:
    return main.WORKER.active.live_bodies()[0]


def _face_count() -> int:
    return sum(1 for f in _body().faces.values() if not f.stale)  # the old faces of an outward shell go stale


# --- inward ------------------------------------------------------------------------------------------------------


def check_one_removed_face_inward_leaves_exact_walls_and_the_same_box() -> None:
    made = _box()
    top = _face(made, (0, 0, 1))
    result = _shell([top])
    # cavity 36 x 26 x 18 = 16848 (two walls on X and Y, one on Z): 24000 - 16848
    assert close(volume(), 7152.0, 1e-6), volume()
    box = bbox()
    assert vclose(box["min"], (0, 0, 0), 1e-6) and vclose(box["max"], BOX, 1e-6), box
    assert result["rebuild_ok"] is True
    assert _face_count() == 11  # 6 faces + 4 inner walls + the floor
    assert top["handle"] in result["modified_faces"], result["modified_faces"]
    assert call("is_valid", op("q"))["valid"] is True


def check_two_opposite_faces_make_a_through_cavity() -> None:
    made = _box()
    _shell([_face(made, (0, 0, 1)), _face(made, (0, 0, -1))])
    assert close(volume(), 24000.0 - 36 * 26 * 20, 1e-6), volume()  # 5280
    assert _face_count() == 10


def check_two_adjacent_faces_open_two_sides() -> None:
    made = _box()
    _shell([_face(made, (0, 0, 1)), _face(made, (1, 0, 0))])
    assert close(volume(), 24000.0 - 38 * 26 * 18, 1e-6), volume()  # 6216: x has one wall left, y two, z one


def check_a_thick_wall_that_still_fits_is_exact() -> None:
    made = _box()
    _shell([_face(made, (0, 0, 1))], 14.0)
    assert close(volume(), 24000.0 - 12 * 2 * 6, 1e-6), volume()  # cavity 12 x 2 x 6


def check_cylinder_with_one_cap_removed() -> None:
    made = _cylinder()
    _shell([_face(made, (0, 0, 1))])
    assert close(volume(), math.pi * (400 * 30 - 18**2 * 28), 1e-6), volume()  # 9198.5833
    assert _face_count() == 5


def check_cylinder_with_both_caps_removed_is_a_tube() -> None:
    made = _cylinder()
    _shell([_face(made, (0, 0, 1)), _face(made, (0, 0, -1))])
    assert close(volume(), math.pi * (400 - 18**2) * 30, 1e-6), volume()  # 7162.8313
    assert _face_count() == 4


def check_cylinder_lying_along_x_with_its_far_cap_removed() -> None:
    made = _cylinder("sh_cylx", (1.0, 0.0, 0.0))
    _shell([_face(made, (1, 0, 0))])
    assert close(volume(), math.pi * (400 * 30 - 18**2 * 28), 1e-6), volume()
    box = bbox()
    assert vclose(box["min"], (0, -20, -20), 1e-6) and vclose(box["max"], (30, 20, 20), 1e-6), box


def check_a_partdesign_plate_is_hollowed_like_a_primitive() -> None:
    """100 x 60 x 8 built by extrude (already a PartDesign body): top removed, 2 mm -> cavity 96 x 56 x 6."""
    new_document("sh_plate")
    base = extrude("plate", sketch("plate_sketch", "XY", [rect("outline", (50, 30), 100, 60)]), 8)
    _shell([select_face(faces_of(base["feature"]), normal=(0, 0, 1))])
    assert close(volume(), 100 * 60 * 8 - 96 * 56 * 6, 1e-6), volume()  # 15744


def check_five_of_six_faces_removed_leaves_a_slab_not_a_no_op() -> None:
    """Only the bottom stays: the cavity is 40 x 30 x 18, the slab 40 x 30 x 2 = 2400, and the face count stays at 6."""
    made = _box()
    faces = [(0, 0, 1), (1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0)]
    _shell([_face(made, n) for n in faces])
    assert close(volume(), 40 * 30 * 2, 1e-6), volume()
    assert call("is_valid", op("q"))["valid"] is True


def check_a_translated_body_is_hollowed_in_place() -> None:
    made = _box()
    call(
        "move_body",
        op(
            "mv",
            body=made["created_bodies"][0],
            transform={"origin": [100.0, 0.0, 0.0], "rotation": IDENTITY["rotation"]},
        ),
    )
    _shell([_face(made, (0, 0, 1))])
    assert close(volume(), 7152.0, 1e-6), volume()
    box = bbox()
    assert vclose(box["min"], (100, 0, 0), 1e-6) and vclose(box["max"], (140, 30, 20), 1e-6), box


# --- outward: the old body becomes the cavity --------------------------------------------------------------------


def check_outward_box_keeps_only_the_skin() -> None:
    """Grown box 44 x 34 x 22 (top removed: no growth up there) minus the 24000 it contains: V = 8912, and the volume
    FALLS because the old solid is now the cavity."""
    made = _box()
    _shell([_face(made, (0, 0, 1))], outward=True)
    assert close(volume(), 44 * 34 * 22 - BOX_VOLUME, 1e-6), volume()  # 8912
    box = bbox()
    assert vclose(box["min"], (-2, -2, -2), 1e-6) and vclose(box["max"], (42, 32, 20), 1e-6), box
    assert _face_count() == 11


def check_outward_on_a_thin_part_raises_the_volume() -> None:
    """10 x 10 x 2 with the top removed, 5 mm outward: grown 20 x 20 x 7 = 2800 minus the 200 inside = 2600 > 200."""
    made = _box((10.0, 10.0, 2.0), "sh_thin")
    _shell([_face(made, (0, 0, 1))], 5.0, outward=True)
    assert close(volume(), 20 * 20 * 7 - 200.0, 1e-6), volume()  # 2600


def check_outward_cylinder_with_one_cap_removed() -> None:
    made = _cylinder("sh_cyl_out")
    _shell([_face(made, (0, 0, 1))], outward=True)
    assert close(volume(), math.pi * (22**2 * 32 - 20**2 * 30), 1e-6), volume()  # 10957.8752
    box = bbox()
    assert vclose(box["min"], (-22, -22, -2), 1e-6) and vclose(box["max"], (22, 22, 30), 1e-6), box


def check_outward_cylinder_with_both_caps_removed_is_a_sleeve() -> None:
    made = _cylinder("sh_sleeve")
    _shell([_face(made, (0, 0, 1)), _face(made, (0, 0, -1))], outward=True)
    assert close(volume(), math.pi * (22**2 - 20**2) * 30, 1e-6), volume()


def check_the_arc_join_rounds_the_outer_edges() -> None:
    """Skin of the box with the top removed and rounded outside: 8000 (slabs: 2400 + 3200 + 2400) + 80 pi (four vertical
    quarter cylinders of r 2 and length 20) + 140 pi (the bottom edges, 40 + 40 + 30 + 30 long) + 16 pi / 3 (four
    eighth spheres) = 8707.9055."""
    made = _box()
    _shell([_face(made, (0, 0, 1))], outward=True, join="arc")
    assert close(volume(), 8000.0 + 220.0 * math.pi + 16.0 * math.pi / 3.0, 1e-6), volume()


# --- the silent no-ops of Thickness ------------------------------------------------------------------------------


def _assert_nothing_changed(faces: int, features: int) -> None:
    assert close(volume(), BOX_VOLUME, 1e-9), volume()
    assert _face_count() == faces
    assert len(main.WORKER.active.features) == features
    assert call("is_valid", op("q"))["valid"] is True


def check_an_empty_face_list_is_refused() -> None:
    _box()
    err = _shell_error("invalid_request", [])
    assert "at least one face" in err.message, err.message
    _assert_nothing_changed(6, 1)


def check_a_wall_too_thick_for_the_thinnest_section_leaves_nothing_behind() -> None:
    """t >= half of Y (30): Thickness answers "Up-to-date" with the volume unchanged (16, 19) or an invalid shape
    (exactly half, 15). All three must be geometry_failed, with the document as it was."""
    for t in (16.0, 19.0, 15.0):
        made = _box(name=f"sh_thick_{int(t)}")
        err = _shell_error("geometry_failed", [_face(made, (0, 0, 1))["handle"]], t)
        assert "kernel could not build this shell" in err.message, err.message
        assert err.recoverable is False, err.recoverable  # the kernel is deterministic: no identical retry
        _assert_nothing_changed(6, 1)


def check_a_failed_shell_does_not_block_the_next_one() -> None:
    made = _box()
    top = _face(made, (0, 0, 1))
    _shell_error("geometry_failed", [top["handle"]], 16.0)
    _shell([top])
    assert close(volume(), 7152.0, 1e-6), volume()


# --- references, validation and undo -----------------------------------------------------------------------------


def check_faces_of_two_bodies_and_a_repeated_face_are_invalid() -> None:
    first = _box()
    second = call(
        "box", op("b2", size=[10, 10, 10], transform={"origin": [100.0, 0.0, 0.0], "rotation": IDENTITY["rotation"]})
    )
    a, b = _face(first, (0, 0, 1)), _face(second, (0, 0, 1))
    err = _shell_error("invalid_request", [a["handle"], b["handle"]])
    assert "different bodies" in err.message, err.message
    err = _shell_error("invalid_request", [a["handle"], a["handle"]])
    assert "twice" in err.message, err.message
    _shell_error("invalid_reference", [{"id": "face:nope"}])


def check_a_non_positive_thickness_and_an_unknown_join_are_invalid() -> None:
    made = _box()
    top = _face(made, (0, 0, 1))["handle"]
    _shell_error("invalid_request", [top], 0.0)
    _shell_error("invalid_request", [top], 2.0, "round")


def check_undo_restores_the_solid() -> None:
    made = _box()
    top = _face(made, (0, 0, 1))
    result = _shell([top])
    assert close(volume(), 7152.0, 1e-6)
    undone = call("undo_last", op("u"))
    assert undone["reverted"] == result["feature"], undone
    assert close(volume(), BOX_VOLUME, 1e-9), volume()
    assert _face_count() == 6
    again = _shell([_face(made, (0, 0, 1))])  # the same face works again after the undo
    assert close(volume(), 7152.0, 1e-6) and again["rebuild_ok"] is True
