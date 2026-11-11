"""draft: PartDesign::Draft about a PartDesign::Plane datum, driven straight on the worker.

The pull is the neutral plane's normal (``PullDirection`` is never set); ``Reversed=False`` makes the drafted faces lean
towards that normal, and the worker CHECKS the lean on the result: a wrong sense is flipped once, a shape whose faces
did not change is refused. Every expected number is derived by hand from the section ``(a - k1 t w)(b - k2 t w)`` at
height ``w`` above the neutral plane (measured on FreeCAD 1.0.2 and 1.1.3, which agree to 1e-9)."""

import math
from typing import Any

import fc
import main
from harness import (
    IDENTITY,
    call,
    close,
    expect_error,
    face_info,
    faces_of,
    new_document,
    op,
    select_face,
    volume,
)

BOX = (40.0, 30.0, 20.0)
BOX_VOLUME = 24000.0
XY = {
    "origin": [0.0, 0.0, 0.0],
    "u": [1.0, 0.0, 0.0],
    "v": [0.0, 1.0, 0.0],
    "n": [0.0, 0.0, 1.0],
}


def _box(name: str = "dr_box") -> dict[str, Any]:
    new_document(name)
    return call("box", op("b", size=list(BOX), transform=IDENTITY))


def _sides(made: dict[str, Any]) -> list[dict[str, Any]]:
    faces = faces_of(made["feature"])
    return [select_face(faces, normal=n) for n in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0))]


def _draft(
    faces: list[dict[str, Any]], angle: float = 3.0, pull: tuple[float, float, float] = (0, 0, 1), neutral: Any = XY
) -> dict[str, Any]:
    params = op("dr", faces=[f["handle"] for f in faces], neutral=neutral, pull=list(pull), angle_deg=angle)
    return call("draft", params)


def _taper(extents: tuple[float, float], k: tuple[int, int], angle: float, lo: float, hi: float) -> float:
    a, b = extents
    t = math.tan(math.radians(angle))
    return (
        a * b * (hi - lo) - (a * k[1] + b * k[0]) * t * (hi**2 - lo**2) / 2 + k[0] * k[1] * t * t * (hi**3 - lo**3) / 3
    )


def _body() -> Any:
    return main.WORKER.active.live_bodies()[0]


def _leans(pull: tuple[float, float, float] = (0, 0, 1)) -> list[float]:
    """Lean (degrees) of every live side face's outward normal towards ``pull``."""
    out = []
    for rec in _body().faces.values():
        if rec.stale:
            continue
        info = face_info({"id": rec.id})
        n = info["normal"]
        if n is not None and abs(n[2]) < 0.99:
            out.append(math.degrees(math.asin(sum(a * b for a, b in zip(n, pull, strict=True)))))
    return out


# --- the box: closed forms -------------------------------------------------------------------------------------


def check_four_sides_from_the_bottom_narrow_upwards() -> None:
    made = _box()
    result = _draft(_sides(made))
    assert close(volume(), _taper((40, 30), (2, 2), 3.0, 0, 20), 1e-6), volume()  # 22561.879
    assert result["rebuild_ok"] is True
    assert all(abs(lean - 3.0) < 1e-6 for lean in _leans()), _leans()
    assert call("is_valid", op("q"))["valid"] is True


def check_the_requested_faces_are_reported_changed() -> None:
    made = _box()
    sides = _sides(made)
    result = _draft(sides)
    touched = {h["id"] for h in result["created_faces"]} | {h["id"] for h in result["modified_faces"]}
    assert len(touched) >= 4, touched


def check_a_mid_height_pivot_adds_only_the_second_order_term() -> None:
    made = _box()
    mid = {**XY, "origin": [0.0, 0.0, 10.0]}
    _draft(_sides(made), neutral=mid)
    assert close(volume(), BOX_VOLUME + math.tan(math.radians(3.0)) ** 2 * 20**3 / 3, 1e-6), volume()  # 24007.3242


def check_a_down_pull_widens_the_walls_upwards() -> None:
    made = _box()
    _draft(_sides(made), pull=(0, 0, -1))
    assert close(volume(), _taper((40, 30), (2, 2), 3.0, -20, 0), 1e-6), volume()  # 25496.7146
    assert all(abs(lean + 3.0) < 1e-6 for lean in _leans()), _leans()  # leans 3 deg towards -Z, i.e. -3 about +Z


def check_a_neutral_frame_whose_normal_points_down_gives_the_same_result_for_the_same_pull() -> None:
    made = _box()
    top_down = {"origin": [0.0, 0.0, 20.0], "u": [1.0, 0.0, 0.0], "v": [0.0, -1.0, 0.0], "n": [0.0, 0.0, -1.0]}
    _draft(_sides(made), pull=(0, 0, -1), neutral=top_down)
    assert close(volume(), _taper((40, 30), (2, 2), 3.0, 0, 20), 1e-6), volume()  # 22561.879: narrows towards -Z


def check_one_wall_alone() -> None:
    made = _box()
    _draft([_sides(made)[0]], 5.0)
    assert close(volume(), _taper((40, 30), (1, 0), 5.0, 0, 20), 1e-6), volume()  # 23475.068


def check_the_largest_angle_still_builds() -> None:
    made = _box()
    _draft(_sides(made), 30.0)
    assert close(volume(), _taper((40, 30), (2, 2), 30.0, 0, 20), 1e-6), volume()


def check_a_cylinder_side_becomes_a_cone() -> None:
    new_document("dr_cyl")
    made = call("cylinder", op("c", diameter_mm=20, height_mm=30, axis={"origin": [0, 0, 0], "direction": [0, 0, 1]}))
    side = select_face(faces_of(made["feature"]), surface="cylinder")
    _draft([side], 2.0)
    t = math.tan(math.radians(2.0))
    assert close(volume(), math.pi * (100 * 30 - 10 * t * 900 + t * t * 27000 / 3), 1e-6), volume()  # 8471.8959
    assert any(f.sig.surface == "cone" for f in _body().faces.values() if not f.stale)


# --- the sense is checked on the result ---------------------------------------------------------------------------


def check_a_wrongly_guessed_sense_is_flipped_once_and_ends_right() -> None:
    """Force the first ``Reversed`` assignment the wrong way: the post-check must see the opposite lean, flip it once
    and finish with the walls narrowing upwards (V = 22561.879)."""
    made = _box()
    sides = _sides(made)
    real = fc.set_prop
    forced = {"done": False}

    def wrong_first(obj: Any, name: str, value: Any, source: str | None = None) -> None:
        if name == "Reversed" and not forced["done"]:
            forced["done"] = True
            value = not value
        real(obj, name, value, source)

    fc.set_prop = wrong_first
    try:
        _draft(sides)
    finally:
        fc.set_prop = real
    assert forced["done"] is True
    assert close(volume(), _taper((40, 30), (2, 2), 3.0, 0, 20), 1e-6), volume()
    assert all(abs(lean - 3.0) < 1e-6 for lean in _leans()), _leans()


def check_a_sense_that_cannot_be_repaired_is_geometry_failed_with_nothing_left() -> None:
    """Every ``Reversed`` assignment forced the wrong way (even the flip): the result never leans as promised."""
    made = _box()
    sides = _sides(made)
    real = fc.set_prop

    def always_wrong(obj: Any, name: str, value: Any, source: str | None = None) -> None:
        real(obj, name, (not value) if name == "Reversed" and obj.TypeId == "PartDesign::Draft" else value, source)

    fc.set_prop = always_wrong
    try:
        err = expect_error(
            "geometry_failed",
            "draft",
            op("dr", faces=[f["handle"] for f in sides], neutral=XY, pull=[0, 0, 1], angle_deg=3.0),
        )
    finally:
        fc.set_prop = real
    assert "the other way" in err.message, err.message
    assert close(volume(), BOX_VOLUME, 1e-9), volume()
    assert len(main.WORKER.active.features) == 1


# --- validation, references and undo -------------------------------------------------------------------------------


def check_invalid_numbers_and_geometry_are_refused() -> None:
    made = _box()
    sides = [f["handle"] for f in _sides(made)]
    for angle in (0.0, -1.0, 45.0, 89.0):
        err = expect_error(
            "invalid_request", "draft", op("dr", faces=sides, neutral=XY, pull=[0, 0, 1], angle_deg=angle)
        )
        assert "at most 30" in err.message, err.message
    err = expect_error("invalid_request", "draft", op("dr", faces=sides, neutral=XY, pull=[1, 0, 0], angle_deg=3.0))
    assert "perpendicular to the neutral plane" in err.message, err.message
    expect_error("invalid_request", "draft", op("dr", faces=[], neutral=XY, pull=[0, 0, 1], angle_deg=3.0))
    expect_error(
        "invalid_request", "draft", op("dr", faces=sides, neutral={"origin": [0, 0, 0]}, pull=[0, 0, 1], angle_deg=3.0)
    )
    expect_error(
        "invalid_reference", "draft", op("dr", faces=[{"id": "face:nope"}], neutral=XY, pull=[0, 0, 1], angle_deg=3.0)
    )
    assert close(volume(), BOX_VOLUME, 1e-9)


def check_faces_of_two_bodies_and_a_repeated_face_are_invalid() -> None:
    first = _box()
    second = call(
        "box", op("b2", size=[10, 10, 10], transform={"origin": [100.0, 0.0, 0.0], "rotation": IDENTITY["rotation"]})
    )
    a = _sides(first)[0]["handle"]
    b = select_face(faces_of(second["feature"]), normal=(1, 0, 0))["handle"]
    err = expect_error("invalid_request", "draft", op("dr", faces=[a, b], neutral=XY, pull=[0, 0, 1], angle_deg=3.0))
    assert "different bodies" in err.message, err.message
    err = expect_error("invalid_request", "draft", op("dr", faces=[a, a], neutral=XY, pull=[0, 0, 1], angle_deg=3.0))
    assert "twice" in err.message, err.message


def check_a_translated_body_is_drafted_about_the_neutral_plane_where_it_stands() -> None:
    made = _box()
    call(
        "move_body",
        op(
            "mv",
            body=made["created_bodies"][0],
            transform={"origin": [100.0, 0.0, 50.0], "rotation": IDENTITY["rotation"]},
        ),
    )
    raised = {**XY, "origin": [0.0, 0.0, 50.0]}
    _draft(_sides(made), neutral=raised)
    assert close(volume(), _taper((40, 30), (2, 2), 3.0, 0, 20), 1e-6), volume()


def check_undo_restores_the_solid_and_the_neutral_plane_goes_with_it() -> None:
    made = _box()
    result = _draft(_sides(made))
    undone = call("undo_last", op("u"))
    assert undone["reverted"] == result["feature"], undone
    assert close(volume(), BOX_VOLUME, 1e-9), volume()
    again = _draft(_sides(made))  # the same faces work again after the undo
    assert close(volume(), _taper((40, 30), (2, 2), 3.0, 0, 20), 1e-6) and again["rebuild_ok"] is True


# --- review fixes: cones are judged by their own semi-angle, drafting is ABSOLUTE, every face has its own successor ---

PULL_X = {"origin": [0.0, 0.0, 0.0], "u": [0.0, 1.0, 0.0], "v": [0.0, 0.0, 1.0], "n": [1.0, 0.0, 0.0]}


def _tan(angle: float) -> float:
    return math.tan(math.radians(angle))


def _shaft(profile: list[tuple[float, float]], name: str) -> dict[str, Any]:
    """Revolve a closed polygon (x along the axis, radius) a full turn about world X."""
    from harness import line, sketch

    new_document(name)
    closed = [*profile, profile[0]]
    sk = sketch("shaft_sk", "XY", [line(f"s{i}", closed[i], closed[i + 1]) for i in range(len(profile))])
    frame = sk["frame"]
    return call(
        "revolve",
        op("shaft", sketch=sk["sketch"], axis={"origin": frame["origin"], "direction": frame["u"]}, angle_deg=360.0),
    )


def _live(kind: str) -> list[Any]:
    return [rec for rec in _body().faces.values() if not rec.stale and rec.sig.surface == kind]


def _draft_x(rec: Any, angle: float, neutral: Any = PULL_X) -> dict[str, Any]:
    params = op("dr", faces=[{"id": rec.id}], neutral=neutral, pull=[1, 0, 0], angle_deg=angle)
    return call("draft", params)


def check_a_cylinder_beside_a_chamfer_cone_is_judged_by_its_own_semi_angle() -> None:
    """R10 shaft with a 45 deg chamfer at the far end: drafting the lateral face 2 deg towards +X must not take the
    45 deg cone for a successor (leans 2 and 45 would read as 'wrong', flip and fail a correct result)."""
    made = _shaft([(0, 0), (0, 10), (25, 10), (30, 5), (30, 0)], "dr_chamfer")
    lateral = select_face(faces_of(made["feature"]), surface="cylinder")
    _draft_x(_body().faces[lateral["handle"]["id"]], 2.0)
    t = _tan(2.0)
    x_r = 25.0 / (1.0 - t)  # where the drafted lateral cone meets the untouched chamfer cone r = 35 - x
    lateral_v = -((10 - x_r * t) ** 3 - 10**3) / (3 * t)
    chamfer_v = ((35 - x_r) ** 3 - 5**3) / 3
    assert close(volume(), math.pi * (lateral_v + chamfer_v), 1e-6), volume()
    cones = sorted(round(math.degrees(abs(rec.shape.Surface.SemiAngle)), 6) for rec in _live("cone"))
    assert cones == [2.0, 45.0], cones


def check_two_coaxial_steps_drafted_at_different_angles() -> None:
    _shaft([(0, 0), (0, 10), (20, 10), (20, 6), (40, 6), (40, 0)], "dr_steps")
    big = next(r for r in _live("cylinder") if abs(r.shape.Surface.Radius - 10.0) < 1e-6)
    _draft_x(big, 2.0)
    small = next(r for r in _live("cylinder") if abs(r.shape.Surface.Radius - 6.0) < 1e-6)
    _draft_x(small, 4.0)
    t1, t2 = _tan(2.0), _tan(4.0)
    v1 = -((10 - 20 * t1) ** 3 - 10**3) / (3 * t1)
    v2 = -((6 - 40 * t2) ** 3 - (6 - 20 * t2) ** 3) / (3 * t2)
    assert close(volume(), math.pi * (v1 + v2), 1e-6), volume()
    assert sorted(round(math.degrees(abs(r.shape.Surface.SemiAngle)), 6) for r in _live("cone")) == [2.0, 4.0]


def _x_wall(rec: Any) -> bool:
    n = face_info({"id": rec.id})["normal"]
    return n is not None and n[0] > 0.99 and abs(n[2]) < 0.02


def _draft_x_wall(angle: float) -> dict[str, Any]:
    wall = next(r for r in _body().faces.values() if not r.stale and r.sig.surface == "plane" and _x_wall(r))
    return call("draft", op("dr2", faces=[{"id": wall.id}], neutral=XY, pull=[0, 0, 1], angle_deg=angle))


def check_a_wall_drafted_again_lands_on_the_absolute_angle() -> None:
    """FreeCAD's draft is ABSOLUTE (a face already leaning 0.5 deg and drafted 3 deg leans 3, not 3.5): 0.5 -> 3.0,
    0.5 -> 0.8 and 0.9 -> 2.0 all succeed, and the volume is that of one wall drafted once by the final angle."""
    for first, second in ((0.5, 3.0), (0.5, 0.8), (0.9, 2.0)):
        made = _box(f"dr_abs_{first}_{second}")
        wall = _sides(made)[0]
        _draft([wall], first)
        _draft_x_wall(second)
        assert close(volume(), _taper((40, 30), (1, 0), second, 0, 20), 1e-6), (first, second, volume())
        assert any(abs(lean - second) < 1e-6 for lean in _leans()), (first, second, _leans())


def check_a_face_facing_the_pull_is_refused_alone_or_among_drafted_ones() -> None:
    made = _box()
    faces = faces_of(made["feature"])
    top, bottom, wall = (select_face(faces, normal=n) for n in ((0, 0, 1), (0, 0, -1), (1, 0, 0)))
    for facing in (top, bottom):
        for chosen in ([facing], [wall, facing], [facing, wall]):
            err = expect_error(
                "invalid_request",
                "draft",
                op("dr", faces=[f["handle"] for f in chosen], neutral=XY, pull=[0, 0, 1], angle_deg=3.0),
            )
            assert "faces the pull" in err.message and "neutral plane" in err.message, err.message
            assert close(volume(), BOX_VOLUME, 1e-9), volume()
    # the face the neutral plane lies in (a datum at the top, the top face named in the list)
    at_top = {**XY, "origin": [0, 0, 20.0]}
    err = expect_error(
        "invalid_request",
        "draft",
        op("dr", faces=[wall["handle"], top["handle"]], neutral=at_top, pull=[0, 0, 1], angle_deg=3.0),
    )
    assert "faces the pull" in err.message, err.message


def check_every_requested_face_needs_its_own_successor() -> None:
    """Two coplanar twins (the x = 40 wall cut in two by a notch): a draft that only reached one of them must not be
    vouched for by the other. Judged straight on the real shapes: the subjects of both twins against a result where only
    one was drafted."""
    import ops_features
    from harness import extrude, line, sketch

    new_document("dr_twins")
    outline = [(0, 0), (40, 0), (40, 10), (30, 10), (30, 20), (40, 20), (40, 30), (0, 30)]
    closed = [*outline, outline[0]]
    sk = sketch("u_sk", "XY", [line(f"l{i}", closed[i], closed[i + 1]) for i in range(len(outline))])
    made = extrude("u", sk, 20)
    twins = [
        f
        for f in faces_of(made["feature"])
        if f["normal"] is not None and f["normal"][0] > 0.99 and abs(f["center"][0] - 40) < 1e-6
    ]
    assert len(twins) == 2, twins
    recs = [_body().faces[t["handle"]["id"]] for t in twins]
    pull = (0.0, 0.0, 1.0)
    subjects, unjudged = ops_features._draft_subjects(recs, pull, pull, (0.0, 0.0, 0.0))
    assert len(subjects) == 2 and unjudged == 0
    call("draft", op("dr", faces=[twins[0]["handle"]], neutral=XY, pull=[0, 0, 1], angle_deg=3.0))
    shape = ops_features.reg.topo_shape(main.WORKER.active, _body())
    verdicts = {ops_features._judge_draft(shape, [s], pull, 3.0) for s in subjects}
    assert verdicts == {"ok", "missing"}, verdicts  # exactly one twin has a successor
    assert ops_features._judge_draft(shape, subjects, pull, 3.0) == "missing"


def check_a_face_the_kernel_omits_fails_even_when_other_faces_are_drafted() -> None:
    """With the up-front refusal switched off the kernel still has to say no: 1.1.3 fails the feature, 1.0.2 logs
    'Adding face failed ... Omitted' and returns a shape with the top face untouched. Either way: geometry_failed with
    nothing left behind."""
    import ops_features

    made = _box()
    faces = faces_of(made["feature"])
    wall, top = select_face(faces, normal=(1, 0, 0)), select_face(faces, normal=(0, 0, 1))
    saved = ops_features._FACING_PULL
    ops_features._FACING_PULL = 2.0  # never triggers
    try:
        expect_error(
            "geometry_failed",
            "draft",
            op("dr", faces=[wall["handle"], top["handle"]], neutral=XY, pull=[0, 0, 1], angle_deg=3.0),
        )
    finally:
        ops_features._FACING_PULL = saved
    assert close(volume(), BOX_VOLUME, 1e-9), volume()
    assert len(main.WORKER.active.features) == 1


def check_tilted_faces_among_drafted_ones_are_built_and_reported_unchecked() -> None:
    from harness import extrude, line, sketch

    new_document("dr_tilted")
    # a trapezoid on XZ (u = x, v = -z): the right side leans 14 deg from the vertical
    outline = [(0, 0), (40, 0), (35, -20), (0, -20)]
    closed = [*outline, outline[0]]
    sk = sketch("t_sk", "XZ", [line(f"l{i}", closed[i], closed[i + 1]) for i in range(len(outline))])
    made = extrude("t", sk, 30)
    faces = faces_of(made["feature"])
    vertical = select_face(faces, normal=(-1, 0, 0))
    slanted = next(f for f in faces if f["normal"] is not None and f["normal"][0] > 0.9)
    chosen = [vertical["handle"], slanted["handle"]]
    result = call("draft", op("dr", faces=chosen, neutral=XY, pull=[0, 0, 1], angle_deg=3.0))
    assert any("not checked" in w for w in result["warnings"]), result["warnings"]


def check_a_body_extruded_from_a_rotated_sketch_plane_is_drafted_about_the_world_pull() -> None:
    """A Pad on XZ carries a rotated Placement and PartDesign::Draft reads the neutral plane in that frame: without
    compensation the walls lean about world Y instead of Z."""
    from harness import extrude, rect, sketch

    new_document("dr_xz")
    sk = sketch("xz_sk", "XZ", [rect("r", (20, -10), 40, 20)])  # x 0..40, z 0..20 (v = -z)
    made = extrude("xz", sk, 30)  # along +Y
    wall = select_face(faces_of(made["feature"]), normal=(1, 0, 0))
    call("draft", op("dr", faces=[wall["handle"]], neutral=XY, pull=[0, 0, 1], angle_deg=3.0))
    assert close(volume(), _taper((40, 30), (1, 0), 3.0, 0, 20), 1e-6), volume()
    assert any(abs(lean - 3.0) < 1e-6 for lean in _leans()), _leans()
