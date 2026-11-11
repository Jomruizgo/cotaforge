"""sweep: a constant section along a polyline spine (PartDesign::AdditivePipe): exact A x L volumes for mitred and
filleted paths, spine link (binder and the direct-link fallback), validity guard, merge, undo, copy, path-length parity.
Every expected number is derived by hand."""

import math

import geometry as g
import ops_features
from harness import (
    IDENTITY,
    bbox,
    call,
    circle,
    close,
    expect_error,
    extrude,
    faces_of,
    mass,
    new_document,
    op,
    rect,
    select_face,
    sketch,
    vclose,
    volume,
)

AREA = 25.0 * math.pi
L_PATH = [[0, 0, 0], [0, 0, 100], [100, 0, 100]]
FILLET_LENGTH = 200.0 - 40.0 + 10.0 * math.pi  # R20 on a 90 degree corner: 191.41592653...


def main_doc_objects() -> list:
    import main  # noqa: PLC0415 - the worker entry point, on sys.path inside freecadcmd

    return list(main.WORKER.active.fc_doc.Objects)


def _sweep(sk: dict, path: list, radius: float | None = None, merge: bool = True, operation_id: str = "sw") -> dict:
    return call(
        "sweep",
        op(operation_id, sketch=sk["sketch"], path=path, corner_radius_mm=radius, merge=merge),
    )


def _tube_sketch(diameter: float = 10.0) -> dict:
    return sketch("sec", "XY", [circle("c", (0, 0), diameter)])


def _surfaces(result: dict) -> list[str]:
    return sorted(f["surface"] for f in faces_of(result["feature"]))


def check_straight_tube_has_exact_volume_box_and_three_faces() -> None:
    new_document("sw_straight")
    result = _sweep(_tube_sketch(), [[0, 0, 0], [0, 0, 100]])
    assert result["rebuild_ok"] is True and len(result["created_bodies"]) == 1, result
    assert ops_features.LAST_SPINE_LINK == "binder", (
        ops_features.LAST_SPINE_LINK
    )  # the preferred route builds on both lines
    assert close(volume(), AREA * 100.0, 1e-6)
    box = bbox()
    assert vclose(box["min"], (-5, -5, 0), 1e-6) and vclose(box["max"], (5, 5, 100), 1e-6), box
    assert _surfaces(result) == ["cylinder", "plane", "plane"], _surfaces(result)


def check_mitred_l_bend_volume_and_faces() -> None:
    new_document("sw_l")
    result = _sweep(_tube_sketch(), L_PATH)
    assert close(volume(), AREA * 200.0, 1e-6), volume()
    assert _surfaces(result) == ["cylinder", "cylinder", "plane", "plane"], _surfaces(result)
    box = bbox()  # exact here: the mitre box of a circle is planar-ish; the kernel box must contain the true one
    assert box["min"][0] <= -5 + 1e-6 and box["max"][0] >= 100 - 1e-6 and box["max"][2] >= 105 - 1e-6, box
    assert call("is_valid", op("q"))["valid"] is True


def check_filleted_bend_volume_torus_and_faces() -> None:
    new_document("sw_fillet")
    result = _sweep(_tube_sketch(), L_PATH, radius=20.0)
    assert close(volume(), AREA * FILLET_LENGTH, 1e-6), (volume(), AREA * FILLET_LENGTH)
    assert close(volume(), 15033.772, 1e-3)
    assert _surfaces(result) == ["cylinder", "cylinder", "plane", "plane", "torus"], _surfaces(result)
    torus = next(f for f in faces_of(result["feature"]) if f["surface"] == "torus")
    assert close(torus["radius"], 20.0, 1e-6), torus  # major (bend) radius
    assert close(torus["area_mm2"], 2 * math.pi * 5.0 * (20.0 * math.pi / 2.0), 1e-3), torus
    assert call("is_valid", op("q"))["valid"] is True


def check_oblique_rectangular_mitre_is_area_times_length() -> None:
    new_document("sw_oblique")
    frame = g.frame_for_face((0.0, 0.0, 0.0), (0.6, 0.0, 0.8))
    dp = call("datum_plane", op("dp", how="normal_to", frame=frame))
    sk = sketch("sec", {"handle": dp["datum"], "frame": dp["frame"]}, [rect("r", (0, 0), 10, 4)])
    path = [[0, 0, 0], [60, 0, 80], [60, 50, 80]]
    result = _sweep(sk, path)
    assert result["rebuild_ok"] is True
    assert close(volume(), 40.0 * (100.0 + 50.0), 1e-5), volume()
    assert call("is_valid", op("q"))["valid"] is True


def check_path_lengths_match_the_hand_values() -> None:
    wire, source = g.sweep_wire([(0.0, 0.0, 0.0), (0.0, 0.0, 100.0), (100.0, 0.0, 100.0)], None)
    assert close(wire.Length, 200.0, 1e-9) and len(wire.Edges) == 2 and source.startswith("Part.Wire([")
    wire, _ = g.sweep_wire([(0.0, 0.0, 0.0), (0.0, 0.0, 100.0), (100.0, 0.0, 100.0)], 20.0)
    assert close(wire.Length, FILLET_LENGTH, 1e-9) and len(wire.Edges) == 3, (wire.Length, len(wire.Edges))
    wire, _ = g.sweep_wire([(0.0, 0.0, 0.0), (0.0, 0.0, 100.0), (80.0, 0.0, 100.0)], 30.0)
    assert close(wire.Length, 120.0 + 15.0 * math.pi, 1e-9), wire.Length  # 70 + 15 pi + 50
    z_bend = [(0.0, 0.0, 0.0), (0.0, 0.0, 50.0), (60.0, 0.0, 50.0), (60.0, 0.0, 100.0)]
    wire, _ = g.sweep_wire(z_bend, 10.0)
    assert close(wire.Length, 50 + 60 + 50 - 4 * 10.0 + 2 * 10.0 * math.pi / 2.0, 1e-9), wire.Length


def check_wire_rejects_paths_it_cannot_build() -> None:
    for points, radius in (
        ([(0, 0, 0)], None),
        ([(0, 0, 0), (0, 0, 0), (0, 0, 5)], None),
        ([(0, 0, 0), (0, 0, 50), (0, 0, 100)], None),
        ([(0, 0, 0), (0, 0, 50), (0, 0, 10)], None),
        ([(0, 0, 0), (0, 0, 100), (100, 0, 100)], 150.0),
    ):
        try:
            g.sweep_wire([tuple(float(c) for c in p) for p in points], radius)
        except ValueError:
            continue
        raise AssertionError(f"sweep_wire accepted {points} radius {radius}")


def check_fillet_below_the_section_reach_is_rejected_and_rolled_back() -> None:
    new_document("sw_reach")
    sk = sketch("sec", "XY", [rect("r", (0, 0), 10, 5)])  # reach 5.59
    expect_error(
        "geometry_failed", "sweep", op("sw", sketch=sk["sketch"], path=L_PATH, corner_radius_mm=4.0, merge=True)
    )
    assert volume() == 0.0 and call("feature_tree", op("q"))["nodes"] == []
    ok = _sweep(sk, L_PATH, radius=20.0, operation_id="sw2")  # the same sketch still works after the failure
    assert ok["rebuild_ok"] is True


def check_a_self_overlapping_sweep_is_rejected_by_the_solid_check() -> None:
    """The executor refuses a path that comes back within a section width of itself; the worker still guards
    a direct caller with ``shape.check(True)`` (self-intersection), which ``isValid()`` alone does not do."""
    new_document("sw_overlap")
    sk = _tube_sketch()
    hairpin = [[0, 0, 0], [0, 0, 100], [8, 0, 100], [8, 0, 0]]  # legs 8 mm apart, section 10 mm wide
    expect_error(
        "geometry_failed", "sweep", op("sw", sketch=sk["sketch"], path=hairpin, corner_radius_mm=3.0, merge=True)
    )
    assert volume() == 0.0 and call("feature_tree", op("q"))["nodes"] == []


def check_a_sweep_inside_existing_material_says_what_to_change() -> None:
    new_document("sw_inside")
    base = sketch("s0", "XY", [rect("r", (50, 30), 100, 60)])
    extrude("plate", base, 8)
    inside = sketch("s1", "XY", [circle("c", (50, 30), 10)])
    error = expect_error(
        "geometry_failed",
        "sweep",
        op("sw", sketch=inside["sketch"], path=[[50, 30, 0], [50, 30, 6]], corner_radius_mm=None, merge=True),
    )
    assert "must not run through existing material" in error.message, error.message


def check_direct_spine_link_fallback_builds_the_same_solid() -> None:
    new_document("sw_direct")
    ops_features.SPINE_BINDER = False
    try:
        _sweep(_tube_sketch(), L_PATH, radius=20.0)
    finally:
        ops_features.SPINE_BINDER = True
    assert close(volume(), AREA * FILLET_LENGTH, 1e-6), volume()
    assert call("is_valid", op("q"))["valid"] is True


def check_a_binder_that_leaves_the_pipe_invalid_falls_back_to_the_direct_link() -> None:
    new_document("sw_binder_fallback")
    original = ops_features._binder_sub_elements
    ops_features._binder_sub_elements = lambda edges: ["Edge99"]  # a spine reference the pipe cannot resolve
    try:
        _sweep(_tube_sketch(), L_PATH, radius=20.0)
    finally:
        ops_features._binder_sub_elements = original
    assert ops_features.LAST_SPINE_LINK == "direct", ops_features.LAST_SPINE_LINK
    assert close(volume(), AREA * FILLET_LENGTH, 1e-6), volume()
    assert call("is_valid", op("q"))["valid"] is True
    names = [o.Name for o in main_doc_objects()]
    assert not any(name.startswith("SweepSpineBinder") for name in names), names  # the failed binder is gone


def check_sweep_merges_into_an_existing_pad() -> None:
    new_document("sw_merge")
    base = sketch("s0", "XY", [rect("r", (50, 30), 100, 60)])
    plate = extrude("plate", base, 8)
    top = select_face(faces_of(plate["feature"]), normal=(0, 0, 1))
    on_top = sketch("s1", top["handle"], [circle("c", (0, 0), 10)])
    before = volume()
    result = _sweep(on_top, [[50, 30, 8], [50, 30, 48]])
    assert result["created_bodies"] == [], result  # joined the plate
    assert close(volume() - before, AREA * 40.0, 1e-6), (volume(), before)
    assert len(call("bodies_of", op("q", feature=result["feature"]))["bodies"]) == 1


def check_undo_removes_the_sweep_and_its_spine() -> None:
    new_document("sw_undo")
    sk = _tube_sketch()
    before = mass()
    result = _sweep(sk, L_PATH, radius=20.0)
    assert call("undo_last", op("u"))["reverted"] == result["feature"]
    assert mass() == before and call("feature_tree", op("q"))["nodes"] == []
    again = _sweep(sk, L_PATH)  # the sketch survives the undo
    assert close(volume(), AREA * 200.0, 1e-6) and again["rebuild_ok"] is True


def check_a_swept_body_can_be_copied_and_moved() -> None:
    new_document("sw_copy")
    result = _sweep(_tube_sketch(), L_PATH)
    source = result["created_bodies"][0]
    copy = call(
        "copy_body", op("cp", source=source, transform={"origin": [0, 100, 0], "rotation": IDENTITY["rotation"]})
    )
    twin = copy["created_bodies"][0]
    assert close(call("mass_properties", op("q", body=twin))["volume_mm3"], AREA * 200.0, 1e-6)
    assert close(volume(), 2 * AREA * 200.0, 1e-6)
    twin_box = call("bounding_box", op("q", body=twin))
    assert twin_box["min"][1] <= 95 + 1e-6 and twin_box["max"][1] >= 105 - 1e-6, twin_box


def _sweep_out_of_a_moved_plate(origin: list, rotation: list, binder: bool) -> None:
    """Sweep out of the top face of a MOVED PartDesign plate: the profile sketch, the spine and the pipe all have to
    agree on where the body is (the spine is a document-level wire in world terms; inside the body it is reached
    through the binder or, in the fallback, a direct link)."""
    new_document("sw_moved")
    base = sketch("s0", "XY", [rect("r", (50, 30), 100, 60)])
    plate = extrude("plate", base, 8)
    call("move_body", op("mv", body=plate["created_bodies"][0], transform={"origin": origin, "rotation": rotation}))
    top = select_face(
        faces_of(plate["feature"]),
        normal=tuple(
            g.rotate_direction(g.placement_from_transform({"origin": origin, "rotation": rotation}), (0, 0, 1))
        ),
    )
    start = top["center"]
    normal = top["normal"]
    on_top = sketch("s1", top["handle"], [circle("c", (0, 0), 10)])
    end = [start[i] + 40.0 * normal[i] for i in range(3)]
    before = volume()
    ops_features.SPINE_BINDER = binder
    try:
        result = _sweep(on_top, [list(start), end])
    finally:
        ops_features.SPINE_BINDER = True
    assert result["rebuild_ok"] is True and result["created_bodies"] == [], result
    assert ops_features.LAST_SPINE_LINK == ("binder" if binder else "direct"), ops_features.LAST_SPINE_LINK
    assert close(volume() - before, AREA * 40.0, 1e-3), (volume(), before)
    assert call("is_valid", op("q"))["valid"] is True


ROT30Z = [
    [math.cos(math.radians(30)), -math.sin(math.radians(30)), 0],
    [math.sin(math.radians(30)), math.cos(math.radians(30)), 0],
    [0, 0, 1],
]


def check_sweep_out_of_a_translated_plate_through_the_binder() -> None:
    _sweep_out_of_a_moved_plate([30, 10, 0], IDENTITY["rotation"], True)


def check_sweep_out_of_a_rotated_plate_through_the_binder() -> None:
    _sweep_out_of_a_moved_plate([30, 10, 5], ROT30Z, True)


def check_sweep_out_of_a_rotated_plate_through_the_direct_link() -> None:
    _sweep_out_of_a_moved_plate([30, 10, 5], ROT30Z, False)


def check_a_second_profile_or_a_bad_path_is_an_invalid_request() -> None:
    new_document("sw_bad")
    ring = sketch("ring", "XY", [circle("o", (0, 0), 10), circle("i", (0, 0), 6)])
    expect_error(
        "invalid_request", "sweep", op("sw", sketch=ring["sketch"], path=L_PATH, corner_radius_mm=None, merge=True)
    )
    sk = _tube_sketch()
    expect_error(
        "invalid_request", "sweep", op("sw", sketch=sk["sketch"], path=[[0, 0, 0]], corner_radius_mm=None, merge=True)
    )
    off = sketch("off", "XY", [circle("c", (10, 0), 10)])
    expect_error(
        "invalid_request", "sweep", op("sw", sketch=off["sketch"], path=L_PATH, corner_radius_mm=None, merge=True)
    )
