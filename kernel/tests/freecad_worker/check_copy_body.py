"""copy_body: a history-free Part::Feature duplicate placed by a transform, the source untouched, undo, axis senses."""

import math

from harness import (
    IDENTITY,
    bbox,
    call,
    close,
    expect_error,
    extrude,
    face_info,
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
ROT90X = [[1, 0, 0], [0, 0, -1], [0, 1, 0]]  # +Z -> -Y (right-hand rule about +X)


def _body_mass(handle: dict[str, str]) -> dict:
    return call("mass_properties", op("q", body=handle))


def _body_bbox(handle: dict[str, str]) -> dict:
    return call("bounding_box", op("q", body=handle))


def _copy(source: dict[str, str], transform: dict, operation_id: str = "cp") -> dict:
    return call("copy_body", op(operation_id, source=source, transform=transform))


def check_copy_body_translates_a_box_and_leaves_the_source_alone() -> None:
    new_document("cp_box")
    box = call("box", op("b", size=[10, 20, 30], transform=IDENTITY))
    source = box["created_bodies"][0]
    result = _copy(source, {"origin": [0, 100, 0], "rotation": IDENTITY["rotation"]})
    twin = result["created_bodies"][0]
    assert twin != source and result["rebuild_ok"] is True, result
    assert len(result["created_faces"]) == 6 and result["modified_faces"] == [], result
    assert close(volume(), 12000.0, 1e-6)
    assert close(_body_mass(twin)["volume_mm3"], 6000.0, 1e-6)
    assert close(_body_mass(twin)["surface_mm2"], 2 * (200 + 300 + 600), 1e-6)
    assert vclose(_body_mass(twin)["centroid"], (5, 110, 15), 1e-6)
    assert vclose(_body_mass(source)["centroid"], (5, 10, 15), 1e-6)
    assert vclose(_body_bbox(twin)["min"], (0, 100, 0), 1e-9) and vclose(_body_bbox(twin)["max"], (10, 120, 30), 1e-9)
    assert vclose(_body_bbox(source)["max"], (10, 20, 30), 1e-9)


def check_copy_body_rotation_is_about_the_origin_then_offset() -> None:
    new_document("cp_rot")
    source = call("box", op("b", size=[10, 20, 30], transform=IDENTITY))["created_bodies"][0]
    twin = _copy(source, {"origin": [0, 100, 0], "rotation": ROT90Z})["created_bodies"][0]
    # x' = -y, y' = x, then +100 in Y: x in [-20, 0], y in [100, 110]
    box = _body_bbox(twin)
    assert vclose(box["min"], (-20, 100, 0), 1e-9) and vclose(box["max"], (0, 110, 30), 1e-9), box


def check_copy_body_carries_the_cylinder_axis_sense() -> None:
    new_document("cp_cyl")
    cyl = call("cylinder", op("c", diameter_mm=20, height_mm=40, axis={"origin": [0, 0, 0], "direction": [0, 0, 1]}))
    twin = _copy(cyl["created_bodies"][0], {"origin": [0, 0, 0], "rotation": ROT90X})
    side = select_face(faces_of(twin["feature"]), surface="cylinder")
    assert parallel(side["axis"]["direction"], (0, -1, 0)), side["axis"]
    assert close(side["radius"], 10.0, 1e-9)
    original = select_face(faces_of(cyl["feature"]), surface="cylinder")
    assert parallel(original["axis"]["direction"], (0, 0, 1)), original["axis"]


def check_copy_body_of_a_partdesign_body_is_a_part_body_with_equal_mass() -> None:
    new_document("cp_pd")
    sk = sketch("s0", "XY", [rect("r", (50, 30), 100, 60)])
    base = extrude("base", sk, 8)
    source = base["created_bodies"][0]
    twin = _copy(source, {"origin": [0, 100, 0], "rotation": IDENTITY["rotation"]})["created_bodies"][0]
    assert close(_body_mass(twin)["volume_mm3"], 48000.0, 1e-6)
    assert close(_body_mass(twin)["surface_mm2"], _body_mass(source)["surface_mm2"], 1e-6)
    assert vclose(_body_mass(twin)["centroid"], (50, 130, 4), 1e-6)


def check_copy_body_undo_removes_the_copy_and_its_handles() -> None:
    new_document("cp_undo")
    source = call("box", op("b", size=[10, 20, 30], transform=IDENTITY))["created_bodies"][0]
    result = _copy(source, {"origin": [0, 100, 0], "rotation": IDENTITY["rotation"]})
    face = result["created_faces"][0]
    face_info(face)
    undone = call("undo_last", op("undo"))
    assert undone["reverted"] == result["feature"], undone
    assert close(volume(), 6000.0, 1e-6)
    expect_error("invalid_reference", "face_info", op("q", face=face))
    box = bbox()
    assert vclose(box["max"], (10, 20, 30), 1e-9), box


def check_copy_body_rejects_an_unknown_source() -> None:
    new_document("cp_bad")
    expect_error(
        "invalid_reference",
        "copy_body",
        op("cp", source={"id": "body:9"}, transform={"origin": [0, 0, 0], "rotation": IDENTITY["rotation"]}),
    )


def check_extrude_on_a_moved_part_body_keeps_its_place() -> None:
    """ensure_partdesign bakes the Part body's placement into the geometry (PartDesign drops a base Placement)."""
    new_document("cp_moved")
    box = call("box", op("b", size=[10, 20, 30], transform=IDENTITY))
    body = box["created_bodies"][0]
    call("move_body", op("mv", body=body, transform={"origin": [0, 100, 0], "rotation": IDENTITY["rotation"]}))
    top = select_face(faces_of(box["feature"]), normal=(0, 0, 1))
    sk = sketch("bs", top["handle"], [rect("boss", (0, 0), 4, 4)])
    extrude("boss", sk, 5)
    assert close(volume(), 6000.0 + 80.0, 1e-6), volume()
    assert math.isclose(bbox()["min"][1], 100.0, abs_tol=1e-9), bbox()
