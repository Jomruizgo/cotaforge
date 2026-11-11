"""Pure math of the sweep operation (verification/sweep.py); every expected number is derived by hand."""

import math

import pytest

from cotaforge.contract import (
    CircleEntity,
    LineEntity,
    PlaneName,
    Profile,
    RectEntity,
    SketchFrame,
    frame_for_normal,
    frame_for_plane,
    sketch_profiles,
)
from cotaforge.verification import formulas as f
from cotaforge.verification import sweep as sw

L_BEND = ((0.0, 0.0, 0.0), (0.0, 0.0, 100.0), (100.0, 0.0, 100.0))
XY = frame_for_plane(PlaneName.XY)


def _circle(diameter: float, centre: tuple[float, float] = (0.0, 0.0)) -> Profile:
    return sketch_profiles([CircleEntity(name="c", center=centre, diameter=diameter)])[0]


def _rect(width: float, height: float) -> Profile:
    return sketch_profiles([RectEntity(name="r", center=(0.0, 0.0), width=width, height=height)])[0]


# --- analyse_path / lengths ----------------------------------------------------------------------------------------


def test_mitre_length_is_the_sum_of_segments() -> None:
    analysis = sw.analyse_path(L_BEND)
    assert analysis.length == pytest.approx(200.0)
    assert analysis.segment_lengths == pytest.approx((100.0, 100.0))
    assert analysis.turn_angles == pytest.approx((math.pi / 2,))
    assert analysis.tangent_lengths == (0.0,)


def test_fillet_length_follows_the_hand_formula() -> None:
    # L = sum|seg| - 2 R tan(phi/2) + R phi = 200 - 2*20*1 + 20*pi/2 = 191.41592653...
    analysis = sw.analyse_path(L_BEND, 20.0)
    assert analysis.length == pytest.approx(200.0 - 40.0 + 10.0 * math.pi, abs=1e-9)
    assert analysis.length == pytest.approx(191.416, abs=5e-4)
    assert analysis.tangent_lengths == pytest.approx((20.0,))
    assert sw.path_length(L_BEND, 20.0) == analysis.length


def test_bench_path_length_matches_the_handle_oracle() -> None:
    # (0,0,0) -> (0,0,100) -> (80,0,100), R30: 70 + 15 pi + 50 = 167.1238898...
    assert sw.path_length(((0, 0, 0), (0, 0, 100), (80, 0, 100)), 30.0) == pytest.approx(120.0 + 15.0 * math.pi)


def test_non_right_angle_fillet_uses_the_turn_angle() -> None:
    # 60 deg turn (phi = pi/3): tangent length R tan(pi/6); length = seg0 + seg1 - 2 t + R phi
    p = ((0.0, 0.0, 0.0), (100.0, 0.0, 0.0), (100.0 + 50.0 * math.cos(math.pi / 3), 50.0 * math.sin(math.pi / 3), 0.0))
    t = 10.0 * math.tan(math.pi / 6)
    assert sw.path_length(p, 10.0) == pytest.approx(150.0 - 2 * t + 10.0 * math.pi / 3)


def test_swept_volume_is_area_times_length() -> None:
    assert sw.swept_volume(math.pi * 25.0, 191.416) == pytest.approx(15033.77, abs=0.05)


@pytest.mark.parametrize(
    ("points", "radius", "needle"),
    [
        (((0, 0, 0),), None, "at least 2 points"),
        (((0, 0, 0), (0, 0, 0), (1, 0, 0)), None, "coincide"),
        (((0, 0, 0), (0, 0, 10), (0, 0, 20)), None, "collinear"),
        (((0, 0, 0), (0, 0, 10), (0, 0, 0)), None, "reverses"),
        (L_BEND, -3.0, "positive"),
        (L_BEND, float("nan"), "positive"),
    ],
)
def test_analyse_path_rejects_unsweepable_paths(points: tuple, radius: float | None, needle: str) -> None:
    with pytest.raises(sw.SweepError, match=needle):
        sw.analyse_path(points, radius)


def test_fillet_that_does_not_fit_the_first_segment_is_rejected() -> None:
    # R 120 on a 90 deg corner needs 120 mm of tangent length, the segments are 100 mm
    with pytest.raises(sw.SweepError, match="does not fit path segment 0"):
        sw.analyse_path(L_BEND, 120.0)


def test_two_fillets_share_a_middle_segment() -> None:
    z_bend = ((0, 0, 0), (0, 0, 50), (60, 0, 50), (60, 0, 100))
    assert sw.analyse_path(z_bend, 25.0).length > 0  # 25 + 25 < 60
    with pytest.raises(sw.SweepError, match="segment 1"):
        sw.analyse_path(z_bend, 35.0)  # 35 + 35 > 60


def test_fillet_fit_tolerance_is_strict() -> None:
    with pytest.raises(sw.SweepError, match="does not fit"):
        sw.analyse_path(L_BEND, 100.0)  # tangent length == segment length: the segment vanishes


# --- face count / reach ---------------------------------------------------------------------------------------------


def test_circle_face_counts() -> None:
    straight = sw.analyse_path(((0, 0, 0), (0, 0, 100)))
    assert sw.circle_face_count(straight) == 3
    assert sw.circle_face_count(sw.analyse_path(L_BEND)) == 4
    assert sw.circle_face_count(sw.analyse_path(L_BEND, 20.0)) == 5


def test_reach_of_the_supported_sections() -> None:
    assert sw.section_reach(_circle(10.0)) == pytest.approx(5.0)
    assert sw.section_reach(_rect(10.0, 5.0)) == pytest.approx(math.hypot(5.0, 2.5))
    assert sw.section_reach(_rect(10.0, 5.0)) == pytest.approx(5.59017, abs=1e-5)
    triangle = sketch_profiles(
        [
            LineEntity(name="a", start=(0, 0), end=(6, 0)),
            LineEntity(name="b", start=(6, 0), end=(0, 6)),
            LineEntity(name="c", start=(0, 6), end=(0, 0)),
        ]
    )[0]
    # centroid (2, 2); the farthest vertices (6, 0) and (0, 6) are hypot(4, 2) away
    assert sw.section_reach(triangle) == pytest.approx(math.hypot(4.0, 2.0))


# --- placement ------------------------------------------------------------------------------------------------------


def _dir_frame(origin: tuple[float, float, float], normal: tuple[float, float, float]) -> SketchFrame:
    return frame_for_normal(origin, normal)


def test_placement_accepts_a_profile_on_the_path_start() -> None:
    sw.check_placement(XY, L_BEND, (0.0, 0.0, 0.0))  # XY normal is +Z = first segment


def test_placement_accepts_the_reverse_normal_and_a_tilted_datum() -> None:
    down = ((0, 0, 0), (0, 0, -100), (50, 0, -100))
    sw.check_placement(XY, down, (0.0, 0.0, 0.0))
    diag = (0.0, 0.6, 0.8)
    points = ((5.0, 0.0, 0.0), (5.0, 60.0, 80.0), (5.0, 60.0, 180.0))
    sw.check_placement(_dir_frame((5.0, 0.0, 0.0), diag), points, (5.0, 0.0, 0.0))


def test_placement_rejects_a_plane_that_is_not_normal_to_the_first_segment() -> None:
    with pytest.raises(sw.SweepError, match="normal to the first path segment"):
        sw.check_placement(XY, ((0, 0, 0), (100, 0, 0), (100, 0, 50)), (0.0, 0.0, 0.0))


def test_placement_rejects_a_plane_off_the_path_start() -> None:
    with pytest.raises(sw.SweepError, match="does not pass through the path start"):
        sw.check_placement(XY, ((0, 0, 5), (0, 0, 100), (100, 0, 100)), (0.0, 0.0, 0.0))


def test_placement_rejects_a_centroid_off_the_path_start_and_says_how_to_fix_it() -> None:
    with pytest.raises(sw.SweepError, match=r"centroid \(10, 0, 0\) is not at the path start \(0, 0, 0\)") as info:
        sw.check_placement(XY, L_BEND, (10.0, 0.0, 0.0))
    assert "start the path at world (10, 0, 0)" in str(info.value)


def test_placement_rejects_a_tilt_whose_section_drift_exceeds_the_position_tolerance() -> None:
    # 0.45 deg is inside the angle tolerance (0.5) but a reach-5 section then drifts 5 sin(0.45 deg) = 0.039 mm
    # along the path, beyond the 0.01 mm position tolerance the checks downstream use
    tilt = math.radians(0.45)
    frame = _dir_frame((0.0, 0.0, 0.0), (math.sin(tilt), 0.0, math.cos(tilt)))
    with pytest.raises(sw.SweepError, match=r"0\.45 deg off the sketch normal") as info:
        sw.check_placement(frame, L_BEND, (0.0, 0.0, 0.0), reach=5.0)
    assert "exactly along the normal" in str(info.value)
    sw.check_placement(frame, L_BEND, (0.0, 0.0, 0.0), reach=0.1)  # a 0.1 mm section drifts only 0.8 um
    sw.check_placement(XY, L_BEND, (0.0, 0.0, 0.0), reach=5.0)  # exact normals are untouched


# --- corner extents (orientation of the section against the bend) -------------------------------------------------


def test_circle_extent_is_its_radius_at_every_corner() -> None:
    analysis = sw.analyse_path(((0, 0, 0), (0, 0, 50), (60, 0, 50), (60, 40, 50)))
    assert sw.corner_extents(analysis, _circle(10.0), XY) == pytest.approx((5.0, 5.0))


def test_rectangle_extent_is_its_half_size_toward_the_bend_centre() -> None:
    # XY section, path up Z then along X: the bend plane is XZ and the centre lies toward +X, so the extent is the
    # half of the rectangle's size along X (its width), not its half-diagonal
    analysis = sw.analyse_path(L_BEND)
    assert sw.corner_extents(analysis, _rect(10.0, 4.0), XY) == pytest.approx((5.0,))
    assert sw.corner_extents(analysis, _rect(4.0, 10.0), XY) == pytest.approx((2.0,))
    assert sw.corner_extents(analysis, _rect(40.0, 5.0), XY) == pytest.approx((20.0,))
    assert sw.corner_extents(analysis, _rect(5.0, 40.0), XY) == pytest.approx((2.5,))


def test_the_extent_follows_the_section_through_an_earlier_bend() -> None:
    # 10 x 4 rectangle (x half 5, y half 2). Corner 1 (up Z -> along X) turns the x axis of the section into -Z
    # (mirror across the bisector plane); corner 2 (along X -> along Y) bends toward +Y, where the section
    # still reaches 2 (its y half-size is untouched by the first bend)
    analysis = sw.analyse_path(((0, 0, 0), (0, 0, 50), (60, 0, 50), (60, 40, 50)))
    assert sw.corner_extents(analysis, _rect(10.0, 4.0), XY) == pytest.approx((5.0, 2.0))


def test_polygon_extent_is_the_farthest_vertex_toward_the_centre() -> None:
    triangle = sketch_profiles(
        [
            LineEntity(name="a", start=(-2, -2), end=(4, -2)),
            LineEntity(name="b", start=(4, -2), end=(-2, 4)),
            LineEntity(name="c", start=(-2, 4), end=(-2, -2)),
        ]
    )[0]  # centroid (0, 0); x reaches 4 toward +X and -2 toward -X
    assert sw.corner_extents(sw.analyse_path(L_BEND), triangle, XY) == pytest.approx((4.0,))


# --- clearance ------------------------------------------------------------------------------------------------------


def test_clearance_accepts_the_reference_shapes() -> None:
    sw.check_clearance(sw.analyse_path(L_BEND), _circle(10.0), XY)
    sw.check_clearance(sw.analyse_path(L_BEND, 20.0), _circle(10.0), XY)


def test_fillet_radius_below_the_section_extent_is_rejected() -> None:
    # R4 against a 10 x 5 rectangle whose width (10) lies in the bend plane: extent 5
    with pytest.raises(sw.SweepError, match="too small for the section"):
        sw.check_clearance(sw.analyse_path(L_BEND, 4.0), _rect(10.0, 5.0), XY)


def test_fillet_radius_needs_a_margin_above_the_extent() -> None:
    with pytest.raises(sw.SweepError, match="too small"):
        sw.check_clearance(sw.analyse_path(L_BEND, 5.05), _circle(10.0), XY)  # 1.02 x 5 = 5.1
    sw.check_clearance(sw.analyse_path(L_BEND, 5.2), _circle(10.0), XY)


def test_a_flat_strap_bends_on_its_thin_side_with_a_small_radius() -> None:
    # 5 x 40 strap, thin side (x) in the bend plane: extent 2.5, R3 is fine although the half-diagonal is 20.2
    sw.check_clearance(sw.analyse_path(L_BEND, 3.0), _rect(5.0, 40.0), XY)
    # 10 x 4 at R5.6: extent 5 (not the 5.39 half-diagonal), accepted
    sw.check_clearance(sw.analyse_path(L_BEND, 5.6), _rect(10.0, 4.0), XY)


def test_a_flat_strap_bent_on_its_wide_side_needs_a_large_radius() -> None:
    wide = _rect(40.0, 5.0)  # extent 20 toward the bend centre
    with pytest.raises(sw.SweepError, match="too small"):
        sw.check_clearance(sw.analyse_path(L_BEND, 15.0), wide, XY)
    sw.check_clearance(sw.analyse_path(L_BEND, 21.0), wide, XY)


def test_the_radius_error_names_the_extent_and_the_way_out() -> None:
    with pytest.raises(sw.SweepError) as info:
        sw.check_clearance(sw.analyse_path(L_BEND, 15.0), _rect(40.0, 5.0), XY)
    message = str(info.value)
    assert "corner 1" in message and "20" in message and "toward the bend centre" in message
    assert "rotate the profile" in message


def test_mitre_overhang_longer_than_the_segment_is_rejected() -> None:
    # a 4 mm middle segment between two 90 deg mitres with reach 5: each end overhangs 5*tan(45) = 5
    short = ((0, 0, 0), (0, 0, 50), (4, 0, 50), (4, 0, 100))
    with pytest.raises(sw.SweepError, match="too short for the mitred corners"):
        sw.check_clearance(sw.analyse_path(short), _circle(10.0), XY)


def test_non_adjacent_segments_must_stay_a_section_apart() -> None:
    # the last segment ends 4 mm from the first one (they meet no corner), the section is 6 mm wide
    loop = ((0, 0, 0), (100, 0, 0), (100, 80, 0), (30, 4, 0))
    with pytest.raises(sw.SweepError, match="segments 0 and 2"):
        sw.check_clearance(sw.analyse_path(loop), _circle(6.0), XY)
    sw.check_clearance(sw.analyse_path(loop), _circle(3.0), XY)  # 3 mm wide: 4 mm apart is fine


def test_segment_distance_of_skew_and_touching_segments() -> None:
    assert sw._segment_distance((0, 0, 0), (10, 0, 0), (5, 3, -5), (5, 3, 5)) == pytest.approx(3.0)
    assert sw._segment_distance((0, 0, 0), (10, 0, 0), (12, 0, 0), (20, 0, 0)) == pytest.approx(2.0)
    assert sw._segment_distance((0, 0, 0), (10, 0, 0), (10, 0, 0), (10, 5, 0)) == pytest.approx(0.0)


# --- bounding boxes -------------------------------------------------------------------------------------------------


def test_single_segment_box_is_the_cylinder_box() -> None:
    box = sw.mitre_bbox(((0, 0, 0), (0, 0, 100)), _circle(10.0), XY)
    assert box.min == pytest.approx((-5.0, -5.0, 0.0))
    assert box.max == pytest.approx((5.0, 5.0, 100.0))


def test_single_segment_box_matches_the_existing_swept_profile_box_when_oblique() -> None:
    frame = _dir_frame((0.0, 0.0, 0.0), (0.0, 0.6, 0.8))
    profile = _circle(10.0)
    ours = sw.mitre_bbox(((0, 0, 0), (0, 60, 80)), profile, frame)
    theirs = f.swept_profile_shape_bbox(frame, profile, 0.0, 100.0)
    assert ours.min == pytest.approx(theirs.min)
    assert ours.max == pytest.approx(theirs.max)


def test_l_mitre_circle_box_is_exact() -> None:
    # by hand: the mitre ellipse at (0,0,100) has semi-axes (5,0,-5) and (0,5,0): half extents (5, 5, 5)
    box = sw.mitre_bbox(L_BEND, _circle(10.0), XY)
    assert box.min == pytest.approx((-5.0, -5.0, 0.0))
    assert box.max == pytest.approx((100.0, 5.0, 105.0))


def test_l_mitre_rectangle_box_is_exact() -> None:
    # 10 x 4 rect in XY (x half 5, y half 2): the vertical leg spans x -5..5, the elbow adds z up to 105
    box = sw.mitre_bbox(L_BEND, _rect(10.0, 4.0), XY)
    assert box.min == pytest.approx((-5.0, -2.0, 0.0))
    assert box.max == pytest.approx((100.0, 2.0, 105.0))


def test_oblique_circle_mitre_box_matches_the_hand_value() -> None:
    # d0 = (0.6, 0, 0.8), d1 = (0, 1, 0), phi = 90 deg. Mitre ellipse at (60, 0, 80): semi axes
    # a = 5 (e1 - d0) = 5 (-0.6, 1, -0.8) and b = 5 (d0 x e1) = 5 (-0.8, 0, 0.6): half extents (5, 5, 5).
    # Start disc: 5 sqrt(1 - d_k^2) = (4, 5, 3) around the origin. End disc at (60, 50, 80), normal +Y: (5, 0, 5).
    points = ((0.0, 0.0, 0.0), (60.0, 0.0, 80.0), (60.0, 50.0, 80.0))
    frame = _dir_frame((0.0, 0.0, 0.0), (0.6, 0.0, 0.8))
    box = sw.mitre_bbox(points, _circle(10.0), frame)
    assert box.min == pytest.approx((-4.0, -5.0, -3.0))
    assert box.max == pytest.approx((65.0, 50.0, 85.0))


def test_lower_box_of_a_fillet_is_inside_the_mitre_box_and_contains_the_end_discs() -> None:
    lower = sw.lower_bbox(L_BEND, 20.0, _circle(10.0), XY)
    exact_mitre = sw.mitre_bbox(L_BEND, _circle(10.0), XY)
    assert f.bbox_contains(exact_mitre, lower)
    # start disc (x, y in +-5), end disc at x = 100 (y +-5, z 95..105), centre line up to z=100 / x=100
    assert lower.min == pytest.approx((-5.0, -5.0, 0.0))
    assert lower.max == pytest.approx((100.0, 5.0, 105.0))


def test_lower_box_for_a_mitre_equals_the_exact_box_on_this_shape() -> None:
    lower = sw.lower_bbox(L_BEND, None, _circle(10.0), XY)
    box = sw.mitre_bbox(L_BEND, _circle(10.0), XY)
    assert f.bbox_contains(box, lower)
    assert lower.max[0] == pytest.approx(100.0)
    assert lower.min[0] == pytest.approx(-5.0)


def test_fillet_arc_points_stay_on_the_circle_of_the_fillet() -> None:
    analysis = sw.analyse_path(L_BEND, 20.0)
    arc = sw._arc_points(analysis, 0, 8)
    centre = (20.0, 0.0, 80.0)
    assert arc[0] == pytest.approx((0.0, 0.0, 80.0))
    assert arc[-1] == pytest.approx((20.0, 0.0, 100.0))
    for p in arc:
        assert math.dist(p, centre) == pytest.approx(20.0)


def test_mitre_bbox_refuses_a_filleted_path() -> None:
    with pytest.raises(ValueError, match="mitred corners only"):
        sw.mitre_bbox(L_BEND, _circle(10.0), XY, corner_radius=5.0)
