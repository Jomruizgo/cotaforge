"""mirror / mirror_body: PartDesign::Mirrored about a PartDesign::Plane datum, and a history-free Part::Feature
reflection (``Shape.mirror``) of a whole body, both driven straight on the worker.

Every expected number is derived by hand: a reflected boss adds its own volume again (pi r^2 h), a reflected hole or
pocket removes its own again, and a reflected body keeps its volume and surface and has its centroid and box reflected
across the plane (``p' = p - 2 n (n . (p - o))``). Run on FreeCAD 1.0.2 and 1.1.3."""

import math
from typing import Any

import geometry as g
import main
from harness import (
    IDENTITY,
    bbox,
    call,
    circle,
    close,
    expect_error,
    extrude,
    face_info,
    faces_of,
    new_document,
    op,
    rect,
    select_face,
    sketch,
    vclose,
    volume,
)

S2 = math.sqrt(0.5)
PLATE_VOLUME = 48000.0
BOSS_VOLUME = math.pi * 25.0 * 5.0  # d 10, 5 mm tall
HOLE_VOLUME = math.pi * 9.0 * 8.0  # d 6 through the 8 mm plate
ROT90Z = [[0, -1, 0], [1, 0, 0], [0, 0, 1]]


def _plane(origin: tuple[float, float, float], normal: tuple[float, float, float]) -> dict[str, Any]:
    """A right-handed frame whose normal is ``normal`` (u and v only complete it; a reflection ignores them)."""
    n = g.unit(normal)
    seed = g.Z if abs(g.dot(n, g.Z)) < 0.9 else g.X
    u = g.unit(g.perpendicular_part(seed, n))
    v = g.cross(n, u)
    return {"origin": list(origin), "u": list(u), "v": list(v), "n": list(n)}


YZ_AT_20 = _plane((20.0, 0.0, 0.0), (1.0, 0.0, 0.0))
YZ_AT_50 = _plane((50.0, 0.0, 0.0), (1.0, 0.0, 0.0))


def _plate() -> tuple[dict[str, Any], dict[str, Any]]:
    """100 x 60 x 8 plate with its corner at the origin; returns the extrude result and its top face."""
    sk = sketch("s0", "XY", [rect("r", (50, 30), 100, 60)])
    base = extrude("base", sk, 8)
    return base, select_face(faces_of(base["feature"]), normal=(0, 0, 1))


def _boss(top: dict[str, Any], at: tuple[float, float] = (10.0, 10.0)) -> dict[str, Any]:
    """A d10 x 5 boss on the plate top at world ``at`` (the face frame is centred on (50, 30))."""
    sk = sketch("bs", top["handle"], [circle("c", (at[0] - 50.0, at[1] - 30.0), 10)])
    return extrude("boss", sk, 5)


def _hole(top: dict[str, Any], at: tuple[float, float] = (10.0, 10.0)) -> dict[str, Any]:
    return call(
        "hole",
        op(
            "h",
            face=top["handle"],
            position=[at[0] - 50.0, at[1] - 30.0],
            standard="simple",
            size=None,
            diameter_mm=6,
            end_condition="through_all",
            depth_mm=None,
        ),
    )


def _mirror(features: list[dict[str, Any]], plane: dict[str, Any], operation_id: str = "mi") -> dict[str, Any]:
    return call("mirror", op(operation_id, features=[f["feature"] for f in features], plane=plane))


def _body_mass(handle: dict[str, str]) -> dict[str, Any]:
    return call("mass_properties", op("q", body=handle))


def _body_bbox(handle: dict[str, str]) -> dict[str, Any]:
    return call("bounding_box", op("q", body=handle))


# --- mirror ----------------------------------------------------------------------------------------------------------


def check_a_boss_mirrored_about_a_plane_adds_its_own_volume_again() -> None:
    new_document("mi_boss")
    _, top = _plate()
    boss = _boss(top)
    assert close(volume(), PLATE_VOLUME + BOSS_VOLUME, 1e-6), volume()
    result = _mirror([boss], YZ_AT_20)
    assert close(volume(), PLATE_VOLUME + 2 * BOSS_VOLUME, 1e-6), volume()  # 48785.398
    assert result["instance_count"] == 2 and result["rebuild_ok"] is True, result
    created = {f["id"] for f in result["created_faces"]}
    assert len(created) == 2, result  # the reflected wall and cap, like the seed's
    walls = [f for f in faces_of(result["feature"], "created") if f["surface"] == "cylinder"]
    assert len(walls) == 1 and abs(walls[0]["center"][0] - 30.0) < 1e-6 and abs(walls[0]["center"][1] - 10.0) < 1e-6
    assert vclose(bbox()["max"], (100, 60, 13), 1e-6), bbox()
    assert call("is_valid", op("q"))["valid"] is True


def check_a_through_hole_mirrored_removes_its_volume_again() -> None:
    new_document("mi_hole")
    _, top = _plate()
    hole = _hole(top)
    assert close(volume(), PLATE_VOLUME - HOLE_VOLUME, 1e-6)
    result = _mirror([hole], YZ_AT_50)
    assert close(volume(), PLATE_VOLUME - 2 * HOLE_VOLUME, 1e-6), volume()  # 47547.6
    bores = [f for f in faces_of(result["feature"], "created") if f["surface"] == "cylinder"]
    assert len(bores) == 1 and abs(bores[0]["center"][0] - 90.0) < 1e-6, bores
    # a hole's sense is the drill direction: -Z, and a reflection across x = 50 leaves it alone
    assert abs(bores[0]["axis"]["direction"][2] + 1.0) < 1e-6, bores[0]["axis"]


def check_a_pocket_mirrored_removes_its_volume_again() -> None:
    new_document("mi_pocket")
    _, top = _plate()
    sk = sketch("ps", top["handle"], [rect("slot", (20, -10), 12, 8)])  # world centre (70, 20)
    pocket = call(
        "cut",
        op(
            "pocket",
            sketch=sk["sketch"],
            end_condition="blind",
            depth_mm=3,
            direction="negative",
            both_directions=False,
            depth2_mm=None,
        ),
    )
    assert close(volume(), PLATE_VOLUME - 288.0, 1e-6), volume()
    _mirror([pocket], YZ_AT_50)
    assert close(volume(), PLATE_VOLUME - 576.0, 1e-6), volume()  # 12 x 8 x 3 twice


def check_two_seeds_are_mirrored_together() -> None:
    new_document("mi_two")
    _, top = _plate()
    boss = _boss(top, (10.0, 10.0))
    hole = _hole(top, (10.0, 40.0))
    before = volume()
    result = _mirror([boss, hole], YZ_AT_50)
    assert close(volume(), before + BOSS_VOLUME - HOLE_VOLUME, 1e-6), volume()
    assert len(result["created_faces"]) == 3, result  # wall + cap of the boss, bore of the hole


def check_a_mirror_plane_that_is_not_axis_aligned_is_exact_in_the_kernel() -> None:
    """A boss at (10, 20) across the plane through (30, 30, 0) with normal (1, -1, 0): it lands at (20, 10)."""
    new_document("mi_oblique")
    _, top = _plate()
    boss = _boss(top, (10.0, 20.0))
    result = _mirror([boss], _plane((30.0, 30.0, 0.0), (1.0, -1.0, 0.0)))
    assert close(volume(), PLATE_VOLUME + 2 * BOSS_VOLUME, 1e-6), volume()
    wall = next(f for f in faces_of(result["feature"], "created") if f["surface"] == "cylinder")
    assert abs(wall["center"][0] - 20.0) < 1e-6 and abs(wall["center"][1] - 10.0) < 1e-6, wall["center"]


def check_the_mirror_plane_is_taken_in_the_frame_of_a_moved_body() -> None:
    """A PartDesign body moved by 100 along Y: the plane x = 50 is a WORLD plane, the datum lives in body terms."""
    new_document("mi_moved")
    base, top = _plate()
    body = base["created_bodies"][0]
    call("move_body", op("mv", body=body, transform={"origin": [0, 100, 0], "rotation": IDENTITY["rotation"]}))
    top = select_face(faces_of(base["feature"]), normal=(0, 0, 1))
    sk = sketch("bs", top["handle"], [circle("c", (-40, -20), 10)])  # world centre (10, 110)
    boss = extrude("boss", sk, 5)
    result = _mirror([boss], YZ_AT_50)
    wall = next(f for f in faces_of(result["feature"], "created") if f["surface"] == "cylinder")
    assert abs(wall["center"][0] - 90.0) < 1e-6 and abs(wall["center"][1] - 110.0) < 1e-6, wall["center"]
    assert close(volume(), PLATE_VOLUME + 2 * BOSS_VOLUME, 1e-6), volume()


def check_a_boss_wall_keeps_or_flips_its_axis_sense_with_the_reflection() -> None:
    """The boss wall's intended axis is +Z (the extrude direction). Across x = 20 it stays +Z; across z = 4 (the
    plate's mid-plane) the reflected boss hangs under the plate (z in [-5, 0]) and its axis turns to -Z."""
    new_document("mi_sense")
    _, top = _plate()
    boss = _boss(top)
    seed_wall = next(f for f in faces_of(boss["feature"], "created") if f["surface"] == "cylinder")
    assert abs(seed_wall["axis"]["direction"][2] - 1.0) < 1e-6, seed_wall["axis"]
    sideways = _mirror([boss], YZ_AT_20, "mi_x")
    wall = next(f for f in faces_of(sideways["feature"], "created") if f["surface"] == "cylinder")
    assert wall["axis"]["direction"][2] > 1 - 1e-6, wall["axis"]
    down = _mirror([boss], _plane((0.0, 0.0, 4.0), (0.0, 0.0, 1.0)), "mi_z")
    wall = next(f for f in faces_of(down["feature"], "created") if f["surface"] == "cylinder")
    assert wall["axis"]["direction"][2] < -1 + 1e-6, wall["axis"]
    assert close(bbox()["min"][2], -5.0, 1e-6), bbox()


def check_a_mirror_that_changes_nothing_is_refused() -> None:
    new_document("mi_same")
    _, top = _plate()
    boss = _boss(top, (30.0, 10.0))
    before = volume()
    error = expect_error(
        "geometry_failed", "mirror", op("mi", features=[boss["feature"]], plane=_plane((30.0, 0, 0), (1.0, 0, 0)))
    )
    assert "no material" in error.message, error.message
    assert close(volume(), before, 1e-9)  # nothing left behind
    assert len(main.WORKER.active.features) == 2, "the failed mirror left a feature record"


def check_a_reflection_that_misses_the_body_is_refused() -> None:
    new_document("mi_miss")
    _, top = _plate()
    boss = _boss(top)
    before = volume()
    plane = _plane((-100.0, 0.0, 0.0), (1.0, 0.0, 0.0))  # the reflected boss lands at x = -210: off the plate
    error = expect_error("geometry_failed", "mirror", op("mi", features=[boss["feature"]], plane=plane))
    assert "material" in error.message or "body" in error.message, error.message
    assert close(volume(), before, 1e-9)


def check_a_primitive_body_cannot_be_mirrored_as_a_feature() -> None:
    new_document("mi_prim")
    box = call("box", op("b", size=[10, 20, 30], transform=IDENTITY))
    error = expect_error("not_supported", "mirror", op("mi", features=[box["feature"]], plane=YZ_AT_20))
    assert "mirror_body" in error.message, error.message


def check_mirror_rejects_a_bad_plane_and_an_unknown_feature() -> None:
    new_document("mi_bad")
    _, top = _plate()
    boss = _boss(top)
    skewed = {**YZ_AT_20, "u": [1.0, 0.0, 0.0]}  # not orthogonal to the normal
    expect_error("invalid_request", "mirror", op("mi", features=[boss["feature"]], plane=skewed))
    expect_error("invalid_reference", "mirror", op("mi", features=[{"id": "feature:99"}], plane=YZ_AT_20))
    expect_error("invalid_request", "mirror", op("mi", features=[], plane=YZ_AT_20))


def check_undo_removes_the_mirror() -> None:
    new_document("mi_undo")
    _, top = _plate()
    boss = _boss(top)
    result = _mirror([boss], YZ_AT_20)
    undone = call("undo_last", op("undo"))
    assert undone["reverted"] == result["feature"], undone
    assert close(volume(), PLATE_VOLUME + BOSS_VOLUME, 1e-6), volume()
    assert call("is_valid", op("q"))["valid"] is True


# --- mirror_body -----------------------------------------------------------------------------------------------------


def _mirror_body(source: dict[str, str], plane: dict[str, Any], operation_id: str = "mb") -> dict[str, Any]:
    return call("mirror_body", op(operation_id, source=source, plane=plane))


def check_a_box_is_mirrored_into_a_new_body_and_the_source_stays() -> None:
    new_document("mb_box")
    source = call("box", op("b", size=[10, 20, 30], transform=IDENTITY))["created_bodies"][0]
    result = _mirror_body(source, YZ_AT_20)
    twin = result["created_bodies"][0]
    assert twin != source and result["rebuild_ok"] is True, result
    assert len(result["created_faces"]) == 6 and result["modified_faces"] == [], result
    assert close(volume(), 12000.0, 1e-6)
    assert close(_body_mass(twin)["volume_mm3"], 6000.0, 1e-6)
    assert close(_body_mass(twin)["surface_mm2"], 2 * (200 + 300 + 600), 1e-6)
    assert vclose(_body_mass(twin)["centroid"], (35, 10, 15), 1e-6), _body_mass(twin)["centroid"]  # 40 - 5
    assert vclose(_body_bbox(twin)["min"], (30, 0, 0), 1e-9) and vclose(_body_bbox(twin)["max"], (40, 20, 30), 1e-9)
    assert vclose(_body_mass(source)["centroid"], (5, 10, 15), 1e-6)
    assert vclose(_body_bbox(source)["max"], (10, 20, 30), 1e-9)
    top = select_face(faces_of(result["feature"]), normal=(0, 0, 1))
    assert vclose(top["center"], (35, 10, 30), 1e-6), top
    assert call("is_valid", op("q"))["valid"] is True


def check_a_cylinder_axis_sense_is_reflected() -> None:
    new_document("mb_cyl")
    cyl = call("cylinder", op("c", diameter_mm=20, height_mm=40, axis={"origin": [0, 0, 0], "direction": [0, 0, 1]}))
    twin = _mirror_body(cyl["created_bodies"][0], _plane((0, 0, 0), (0, 0, 1)))
    side = select_face(faces_of(twin["feature"]), surface="cylinder")
    assert side["axis"]["direction"][2] < -1 + 1e-6, side["axis"]  # +Z across z = 0 becomes -Z
    assert close(side["radius"], 10.0, 1e-9)
    assert vclose(_body_bbox(twin["created_bodies"][0])["min"], (-10, -10, -40), 1e-6)
    across_x = _mirror_body(cyl["created_bodies"][0], YZ_AT_20, "mb2")
    kept = select_face(faces_of(across_x["feature"]), surface="cylinder")
    assert kept["axis"]["direction"][2] > 1 - 1e-6, kept["axis"]  # a plane along the axis leaves its sense alone


def check_a_partdesign_body_with_a_hole_keeps_the_hole_and_reflects_its_sense() -> None:
    new_document("mb_hole")
    base, top = _plate()
    _hole(top)
    source = base["created_bodies"][0]
    twin = _mirror_body(source, _plane((0.0, 0.0, 8.0), (0.0, 0.0, 1.0)))  # across the plate's top face
    mass = _body_mass(twin["created_bodies"][0])
    assert close(mass["volume_mm3"], PLATE_VOLUME - HOLE_VOLUME, 1e-6)
    assert close(mass["surface_mm2"], _body_mass(source)["surface_mm2"], 1e-6)
    bore = select_face(faces_of(twin["feature"]), surface="cylinder")
    assert bore["axis"]["direction"][2] > 1 - 1e-6, bore["axis"]  # drilled down (-Z); the twin's is drilled up
    assert abs(bore["center"][0] - 10.0) < 1e-6 and abs(bore["center"][2] - 12.0) < 1e-6, bore["center"]


def check_a_moved_and_rotated_partdesign_body_is_mirrored_in_world_terms() -> None:
    """The plate is rotated a quarter turn about Z and moved 100 along Y: x in [-60, 0], y in [100, 200]. Across x = 10
    (x' = 20 - x) it lands at x in [20, 80] with its centroid (-30, 150, 4) -> (50, 150, 4)."""
    new_document("mb_moved")
    base, _ = _plate()
    body = base["created_bodies"][0]
    call("move_body", op("mv", body=body, transform={"origin": [0, 100, 0], "rotation": ROT90Z}))
    twin = _mirror_body(body, _plane((10.0, 0.0, 0.0), (1.0, 0.0, 0.0)))["created_bodies"][0]
    assert vclose(_body_bbox(twin)["min"], (20, 100, 0), 1e-6) and vclose(_body_bbox(twin)["max"], (80, 200, 8), 1e-6)
    assert vclose(_body_mass(twin)["centroid"], (50, 150, 4), 1e-6), _body_mass(twin)["centroid"]
    assert close(_body_mass(twin)["volume_mm3"], PLATE_VOLUME, 1e-6)
    assert vclose(_body_bbox(body)["min"], (-60, 100, 0), 1e-6), "the source moved"


def check_an_oblique_plane_keeps_the_volume_and_reflects_the_centroid() -> None:
    new_document("mb_oblique")
    source = call("box", op("b", size=[10, 20, 30], transform=IDENTITY))["created_bodies"][0]
    angle = math.radians(20.0)
    normal = (math.cos(angle), math.sin(angle), 0.0)
    origin = (3.0, -2.0, 1.0)
    twin = _mirror_body(source, _plane(origin, normal))["created_bodies"][0]
    mass = _body_mass(twin)
    assert close(mass["volume_mm3"], 6000.0, 1e-6)
    assert close(mass["surface_mm2"], 2 * (200 + 300 + 600), 1e-6)
    assert vclose(mass["centroid"], g.reflect_point((5.0, 10.0, 15.0), origin, normal), 1e-6), mass["centroid"]


def check_mirror_body_undo_removes_the_twin_and_its_handles() -> None:
    new_document("mb_undo")
    source = call("box", op("b", size=[10, 20, 30], transform=IDENTITY))["created_bodies"][0]
    result = _mirror_body(source, YZ_AT_20)
    face = result["created_faces"][0]
    face_info(face)
    undone = call("undo_last", op("undo"))
    assert undone["reverted"] == result["feature"], undone
    assert close(volume(), 6000.0, 1e-6)
    expect_error("invalid_reference", "face_info", op("q", face=face))


def check_mirror_body_rejects_an_unknown_source_and_a_bad_plane() -> None:
    new_document("mb_bad")
    expect_error("invalid_reference", "mirror_body", op("mb", source={"id": "body:9"}, plane=YZ_AT_20))
    source = call("box", op("b", size=[10, 20, 30], transform=IDENTITY))["created_bodies"][0]
    expect_error("invalid_request", "mirror_body", op("mb", source=source, plane={**YZ_AT_20, "n": [0, 0, 0]}))


def check_a_mirrored_body_takes_a_later_boolean_like_any_part_body() -> None:
    new_document("mb_bool")
    source = call("box", op("b", size=[10, 10, 10], transform=IDENTITY))["created_bodies"][0]
    twin = _mirror_body(source, _plane((5.0, 0.0, 0.0), (1.0, 0.0, 0.0)))["created_bodies"][0]  # x in [0, 10] again
    call("boolean", op("u", kind="union", target=source, tools=[twin], keep_tools=False))
    assert close(volume(), 1000.0, 1e-6), volume()  # the twin is the source


# --- mixed seeds: every class of seed is judged on its own, and nothing may be left floating -------------------------

X_PLUS_Y_100 = _plane((50.0, 50.0, 0.0), (1.0, 1.0, 0.0))


def check_a_mixed_mirror_that_leaves_a_boss_floating_is_refused() -> None:
    """Boss at (10, 10) and bore at (60, 30) across x + y = 100: the bore lands on the plate at (70, 40) but the boss
    lands at (90, 90), off the 100 x 60 plate. The bore's volume used to hide it."""
    new_document("mi_float")
    _, top = _plate()
    boss = _boss(top, (10.0, 10.0))
    hole = _hole(top, (60.0, 30.0))
    before = volume()
    error = expect_error(
        "geometry_failed", "mirror", op("mi", features=[boss["feature"], hole["feature"]], plane=X_PLUS_Y_100)
    )
    assert "boss" in error.message and "touch" in error.message, error.message
    assert close(volume(), before, 1e-9)  # nothing left behind


def check_a_mixed_mirror_whose_cut_removes_nothing_is_refused() -> None:
    """Across x = 60 the boss at (70, 30) lands on the plate at (50, 30), but the bore at (10, 30) lands at (110, 30),
    off the 100 mm plate. The reflected boss adds material, which used to make the net change look right."""
    new_document("mi_mixed_cut")
    _, top = _plate()
    boss = _boss(top, (70.0, 30.0))
    hole = _hole(top, (10.0, 30.0))
    before = volume()
    plane = _plane((60.0, 0.0, 0.0), (1.0, 0.0, 0.0))
    error = expect_error(
        "geometry_failed", "mirror", op("mi", features=[boss["feature"], hole["feature"]], plane=plane)
    )
    assert "removed no material" in error.message, error.message
    assert close(volume(), before, 1e-9)


def check_a_mixed_mirror_that_works_is_one_feature_for_the_agent() -> None:
    new_document("mi_mixed_ok")
    _, top = _plate()
    boss = _boss(top, (10.0, 10.0))
    hole = _hole(top, (30.0, 30.0))
    result = _mirror([boss, hole], YZ_AT_50)
    assert close(volume(), PLATE_VOLUME + 2 * BOSS_VOLUME - 2 * HOLE_VOLUME, 1e-6), volume()
    assert result["instance_count"] == 2 and len(result["created_faces"]) == 3, result
    assert call("is_valid", op("q"))["valid"] is True
    undone = call("undo_last", op("undo"))
    assert undone["reverted"] == result["feature"], undone
    assert close(volume(), PLATE_VOLUME + BOSS_VOLUME - HOLE_VOLUME, 1e-6), volume()


# --- seeds that are not mirrorable: say what to do instead -----------------------------------------------------------


def check_a_pattern_cannot_be_a_seed_and_the_message_says_what_to_mirror() -> None:
    new_document("mi_pattern_seed")
    _, top = _plate()
    hole = _hole(top, (10.0, 10.0))
    row = call("linear_pattern", op("row", features=[hole["feature"]], direction=[1, 0, 0], spacing_mm=15, count=2))
    error = expect_error("not_supported", "mirror", op("mi", features=[row["feature"]], plane=YZ_AT_50))
    assert "pattern" in error.message and "re-pattern" in error.message, error.message
    assert "mirror_body" not in error.message, error.message


def check_a_sweep_cannot_be_a_seed_yet() -> None:
    new_document("mi_sweep_seed")
    _, top = _plate()
    tube = sketch("sec", top["handle"], [circle("c", (0, 0), 6)])
    swept = call(
        "sweep", op("sw", sketch=tube["sketch"], path=[[50, 30, 8], [50, 30, 28]], corner_radius_mm=None, merge=True)
    )
    error = expect_error("not_supported", "mirror", op("mi", features=[swept["feature"]], plane=YZ_AT_50))
    assert "sweep" in error.message and "not supported as a mirror seed yet" in error.message, error.message


def check_a_cut_that_splits_the_body_is_refused_with_the_same_wording() -> None:
    """A through slot 4 mm wide across the whole 60 mm depth near the left edge, mirrored across x = 50 to x = 80: it
    cuts the plate in three. PartDesign must not keep that, and the message names both causes of a second solid."""
    new_document("mi_split")
    _, top = _plate()
    sk = sketch("ss", top["handle"], [rect("slot", (-30, 0), 4, 60)])  # world x 18..22 over the full depth
    slot = call(
        "cut",
        op(
            "slot",
            sketch=sk["sketch"],
            end_condition="through_all",
            depth_mm=None,
            direction="negative",
            both_directions=False,
            depth2_mm=None,
        ),
    )
    before = volume()
    error = expect_error("geometry_failed", "mirror", op("mi", features=[slot["feature"]], plane=YZ_AT_50))
    # 1.1.3 keeps the pieces (a second solid, named with both causes); 1.0.2 drops the cut altogether
    assert ("separate solid" in error.message and "splitting the body" in error.message) or (
        "removed no material" in error.message
    ), error.message
    assert close(volume(), before, 1e-9)
