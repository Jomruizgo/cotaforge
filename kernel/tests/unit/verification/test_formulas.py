"""Hand-computed tests for cotaforge.verification.formulas (pure functions)."""

import math

import pytest

from cotaforge.contract import Axis, BoundingBox, Transform, frame_for_plane
from cotaforge.contract.enums import PlaneName
from cotaforge.verification import formulas as f
from cotaforge.verification.formulas import BoxShape, CylinderShape

# --- volumes -----------------------------------------------------------------


def test_extrude_volume_rect() -> None:
    # 100 x 60 plate extruded 8 mm: 6000 * 8 = 48000
    assert f.extrude_volume(6000.0, 8.0) == pytest.approx(48000.0)


def test_extrude_volume_both_directions() -> None:
    # A (depth + depth2) = 6000 * (8 + 4) = 72000; depth2 None -> symmetric: 6000 * 16
    assert f.extrude_volume(6000.0, 8.0, depth2=4.0, both_directions=True) == pytest.approx(72000.0)
    assert f.extrude_volume(6000.0, 8.0, both_directions=True) == pytest.approx(96000.0)


def test_cut_volume_is_negative() -> None:
    assert f.cut_volume(200.0, 5.0) == pytest.approx(-1000.0)


def test_revolve_volume_pappus_rectangle_offset_from_axis() -> None:
    # Rectangle 10 (radial) x 20 (axial), centroid at d = 15 from the axis:
    # V = 2*pi*d*A = 2*pi*15*200 = 6000*pi
    assert f.revolve_volume(200.0, 15.0) == pytest.approx(6000.0 * math.pi)
    # 180 degrees -> half
    assert f.revolve_volume(200.0, 15.0, angle_deg=180.0) == pytest.approx(3000.0 * math.pi)


def test_hole_volume() -> None:
    # -pi * 3.3^2 * 8 = -273.6957...
    assert f.hole_volume(3.3, 8.0) == pytest.approx(-math.pi * 3.3**2 * 8.0)
    assert f.hole_volume(3.3, 8.0) == pytest.approx(-273.6957, abs=1e-3)


def test_chamfer_volume_convex_and_concave() -> None:
    # d1 * d2 / 2 * L = 2 * 3 / 2 * 10 = 30
    assert f.chamfer_volume(2.0, 3.0, 10.0, convex=True) == pytest.approx(-30.0)
    assert f.chamfer_volume(2.0, 3.0, 10.0, convex=False) == pytest.approx(30.0)


def test_fillet_volume() -> None:
    # (1 - pi/4) * r^2 * L = (1 - pi/4) * 4 * 10 = 40 - 10*pi = 8.5840...
    assert f.fillet_volume(2.0, 10.0) == pytest.approx(-(40.0 - 10.0 * math.pi))
    assert f.fillet_volume(2.0, 10.0) == pytest.approx(-8.5840, abs=1e-3)
    assert f.fillet_volume(2.0, 10.0, convex=False) == pytest.approx(8.5840, abs=1e-3)


def test_pattern_volume() -> None:
    # (n - 1) * delta_seed: 4 instances of a -273.69 hole -> 3 * -273.69
    assert f.pattern_volume(4, -273.69) == pytest.approx(-821.07)


def test_box_and_cylinder_volume() -> None:
    assert f.box_volume((100.0, 60.0, 20.0)) == pytest.approx(120000.0)
    # pi * 15^2 * 20 = 4500 pi
    assert f.cylinder_volume(15.0, 20.0) == pytest.approx(4500.0 * math.pi)


def test_centroid_after_removing_a_volume() -> None:
    # Block 100x60x8 (V=48000, c=(50,30,4)) minus a 10x10x8 column at (5,5,4) (V=800):
    # cx = (48000*50 - 800*5) / 47200 = (2400000 - 4000) / 47200 = 50.7627...
    c = f.centroid_after(48000.0, (50.0, 30.0, 4.0), [(-800.0, (5.0, 5.0, 4.0))])
    assert c[0] == pytest.approx(2396000.0 / 47200.0)
    assert c[1] == pytest.approx((48000.0 * 30.0 - 800.0 * 5.0) / 47200.0)
    assert c[2] == pytest.approx(4.0)


# --- bounding boxes -------------------------------------------------------------


def test_swept_profile_bbox_on_xy_plane() -> None:
    frame = frame_for_plane(PlaneName.XY)
    box = f.swept_profile_bbox(frame, ((0.0, 0.0), (100.0, 60.0)), 0.0, 8.0)
    assert box == BoundingBox(min=(0.0, 0.0, 0.0), max=(100.0, 60.0, 8.0))
    # negative sweep (a cut going into the material below the sketch plane)
    box = f.swept_profile_bbox(frame, ((10.0, 10.0), (20.0, 30.0)), -5.0, 0.0)
    assert box == BoundingBox(min=(10.0, 10.0, -5.0), max=(20.0, 30.0, 0.0))


def test_swept_profile_bbox_on_xz_plane_uses_frame_v() -> None:
    # XZ frame: u=+X, v=-Z, n=+Y -> uv (0..10, 0..4) maps to x 0..10, z -4..0
    frame = frame_for_plane(PlaneName.XZ)
    box = f.swept_profile_bbox(frame, ((0.0, 0.0), (10.0, 4.0)), 0.0, 3.0)
    assert box.min == pytest.approx((0.0, 0.0, -4.0))
    assert box.max == pytest.approx((10.0, 3.0, 0.0))


def test_cylinder_bbox_exact_for_tilted_axis() -> None:
    # Axis along Z: disc radius 5 at z 0..10 -> [-5,5]x[-5,5]x[0,10]
    axis = Axis(origin=(0.0, 0.0, 0.0), direction=(0.0, 0.0, 1.0))
    assert f.cylinder_bbox(axis, 5.0, 0.0, 10.0) == BoundingBox(min=(-5.0, -5.0, 0.0), max=(5.0, 5.0, 10.0))
    # Axis along the XY diagonal, radius 1, length 0: extent per world axis = R*sqrt(1 - a_i^2)
    diag = Axis(origin=(0.0, 0.0, 0.0), direction=(1.0, 1.0, 0.0))
    box = f.cylinder_bbox(diag, 1.0, 0.0, 0.0)
    assert box.max == pytest.approx((math.sqrt(0.5), math.sqrt(0.5), 1.0))


def test_bbox_union_transform_and_overlap() -> None:
    a = BoundingBox(min=(0.0, 0.0, 0.0), max=(1.0, 1.0, 1.0))
    b = BoundingBox(min=(2.0, -1.0, 0.5), max=(3.0, 0.5, 2.0))
    assert f.bbox_union(a, b) == BoundingBox(min=(0.0, -1.0, 0.0), max=(3.0, 1.0, 2.0))
    assert not f.bboxes_overlap(a, b)
    assert f.bboxes_overlap(a, BoundingBox(min=(0.5, 0.5, 0.5), max=(2.0, 2.0, 2.0)))
    # touching is not overlapping
    assert not f.bboxes_overlap(a, BoundingBox(min=(1.0, 0.0, 0.0), max=(2.0, 1.0, 1.0)))
    # two coplanar flat boxes (faces) overlap when their footprints do
    flat = BoundingBox(min=(0.0, 0.0, 1.0), max=(1.0, 1.0, 1.0))
    assert f.bboxes_overlap(flat, BoundingBox(min=(0.5, 0.5, 1.0), max=(2.0, 2.0, 1.0)))
    assert not f.bboxes_overlap(flat, BoundingBox(min=(0.5, 0.5, 2.0), max=(2.0, 2.0, 2.0)))
    rotated = f.bbox_transform(
        BoundingBox(min=(0.0, 0.0, 0.0), max=(10.0, 4.0, 2.0)), Transform.from_axis_angle((0.0, 0.0, 1.0), 90.0)
    )
    assert rotated.min == pytest.approx((-4.0, 0.0, 0.0))
    assert rotated.max == pytest.approx((0.0, 10.0, 2.0))
    assert f.bbox_translate(a, (1.0, 2.0, 3.0)) == BoundingBox(min=(1.0, 2.0, 3.0), max=(2.0, 3.0, 4.0))


def test_is_principal_rotation() -> None:
    assert f.is_principal_rotation(Transform.identity())
    assert f.is_principal_rotation(Transform.from_axis_angle((0.0, 1.0, 0.0), 270.0))
    assert not f.is_principal_rotation(Transform.from_axis_angle((0.0, 0.0, 1.0), 45.0))
    # 180 deg about the XY diagonal is a signed permutation (swap x,y; negate z): still principal
    assert f.is_principal_rotation(Transform.from_axis_angle((1.0, 1.0, 0.0), 180.0))
    assert not f.is_principal_rotation(Transform.from_axis_angle((1.0, 1.0, 0.0), 90.0))


def test_rotation_about_point() -> None:
    # 90 deg about Z through (10, 0, 0): the point (10, 0, 0) stays put, (20, 0, 0) -> (10, 10, 0)
    t = f.rotation_about_point((0.0, 0.0, 1.0), 90.0, (10.0, 0.0, 0.0))
    assert t.apply((10.0, 0.0, 0.0)) == pytest.approx((10.0, 0.0, 0.0))
    assert t.apply((20.0, 0.0, 0.0)) == pytest.approx((10.0, 10.0, 0.0))


def test_axis_helpers() -> None:
    axis = Axis(origin=(0.0, 0.0, 0.0), direction=(0.0, 0.0, 1.0))
    assert f.distance_point_to_axis((3.0, 4.0, 7.0), axis) == pytest.approx(5.0)
    assert f.closest_point_on_axis((3.0, 4.0, 7.0), axis) == pytest.approx((0.0, 0.0, 7.0))
    assert f.perpendicular_part((3.0, 4.0, 7.0), (0.0, 0.0, 1.0)) == pytest.approx((3.0, 4.0, 0.0))


def test_revolve_bbox_full_turn_about_world_axis() -> None:
    # Profile on XY revolved about the X axis: points x in [0, 30], y (radius) up to 10 -> bbox x 0..30, y,z -10..10
    axis = Axis(origin=(0.0, 0.0, 0.0), direction=(1.0, 0.0, 0.0))
    points = [(0.0, 0.0, 0.0), (30.0, 0.0, 0.0), (30.0, 10.0, 0.0), (0.0, 10.0, 0.0)]
    assert f.revolve_bbox(axis, points) == BoundingBox(min=(0.0, -10.0, -10.0), max=(30.0, 10.0, 10.0))


# --- intersection volumes (7.4 rules, independent implementation) ----------------


def test_intersection_box_box() -> None:
    a = BoxShape(min=(0.0, 0.0, 0.0), max=(10.0, 10.0, 10.0))
    b = BoxShape(min=(5.0, 5.0, -5.0), max=(20.0, 20.0, 5.0))
    # overlap 5 x 5 x 5 = 125
    assert f.intersection_volume(a, b) == pytest.approx(125.0)
    assert f.intersection_volume(a, BoxShape(min=(10.0, 0.0, 0.0), max=(20.0, 1.0, 1.0))) == pytest.approx(0.0)


def test_intersection_cylinder_cylinder_coaxial() -> None:
    z = (0.0, 0.0, 1.0)
    a = CylinderShape(axis=Axis(origin=(0.0, 0.0, 0.0), direction=z), radius=5.0, height=10.0)
    b = CylinderShape(axis=Axis(origin=(0.0, 0.0, 6.0), direction=z), radius=3.0, height=10.0)
    # pi * 3^2 * (10 - 6) = 36 pi
    assert f.intersection_volume(a, b) == pytest.approx(36.0 * math.pi)
    # opposite sense is still coaxial: b grows downwards from z = 6 -> overlap z 0..6
    c = CylinderShape(axis=Axis(origin=(0.0, 0.0, 6.0), direction=(0.0, 0.0, -1.0)), radius=3.0, height=10.0)
    assert f.intersection_volume(a, c) == pytest.approx(54.0 * math.pi)
    # not coaxial -> None
    d = CylinderShape(axis=Axis(origin=(1.0, 0.0, 0.0), direction=z), radius=3.0, height=10.0)
    assert f.intersection_volume(a, d) is None


def test_intersection_cylinder_box() -> None:
    box = BoxShape(min=(0.0, 0.0, 0.0), max=(100.0, 60.0, 20.0))
    z = (0.0, 0.0, 1.0)
    inside = CylinderShape(axis=Axis(origin=(50.0, 30.0, 0.0), direction=z), radius=15.0, height=20.0)
    # footprint inside -> pi * 15^2 * 20 = 4500 pi; symmetric call order
    assert f.intersection_volume(box, inside) == pytest.approx(4500.0 * math.pi)
    assert f.intersection_volume(inside, box) == pytest.approx(4500.0 * math.pi)
    taller = CylinderShape(axis=Axis(origin=(50.0, 30.0, -5.0), direction=z), radius=15.0, height=40.0)
    assert f.intersection_volume(box, taller) == pytest.approx(4500.0 * math.pi)
    disjoint = CylinderShape(axis=Axis(origin=(200.0, 30.0, 0.0), direction=z), radius=15.0, height=20.0)
    assert f.intersection_volume(box, disjoint) == pytest.approx(0.0)
    above = CylinderShape(axis=Axis(origin=(50.0, 30.0, 20.0), direction=z), radius=15.0, height=20.0)
    assert f.intersection_volume(box, above) == pytest.approx(0.0)
    partial = CylinderShape(axis=Axis(origin=(95.0, 30.0, 0.0), direction=z), radius=15.0, height=20.0)
    assert f.intersection_volume(box, partial) is None
    tilted = CylinderShape(axis=Axis(origin=(50.0, 30.0, 0.0), direction=(1.0, 1.0, 0.0)), radius=5.0, height=20.0)
    assert f.intersection_volume(box, tilted) is None


def test_shape_bbox_and_volume() -> None:
    cyl = CylinderShape(axis=Axis(origin=(50.0, 30.0, 0.0), direction=(0.0, 0.0, 1.0)), radius=15.0, height=20.0)
    assert f.shape_bbox(cyl) == BoundingBox(min=(35.0, 15.0, 0.0), max=(65.0, 45.0, 20.0))
    assert f.shape_volume(cyl) == pytest.approx(4500.0 * math.pi)
    box = BoxShape(min=(0.0, 0.0, 0.0), max=(1.0, 2.0, 3.0))
    assert f.shape_volume(box) == pytest.approx(6.0)


def test_transform_shape() -> None:
    box = BoxShape(min=(0.0, 0.0, 0.0), max=(10.0, 4.0, 2.0))
    moved = f.transform_shape(box, Transform(origin=(1.0, 1.0, 1.0)))
    assert moved == BoxShape(min=(1.0, 1.0, 1.0), max=(11.0, 5.0, 3.0))
    assert f.transform_shape(box, Transform.from_axis_angle((0.0, 0.0, 1.0), 45.0)) is None
    cyl = CylinderShape(axis=Axis(origin=(0.0, 0.0, 0.0), direction=(0.0, 0.0, 1.0)), radius=2.0, height=5.0)
    turned = f.transform_shape(cyl, Transform.from_axis_angle((1.0, 0.0, 0.0), 90.0))
    assert isinstance(turned, CylinderShape)
    assert turned.axis.direction == pytest.approx((0.0, -1.0, 0.0))


# --- exact swept box of a profile on a tilted frame (datum planes) ---------------------------


def test_swept_profile_shape_bbox_of_a_circle_on_an_axis_aligned_frame_matches_the_uv_box() -> None:
    from cotaforge.contract import sketch_profiles
    from cotaforge.contract.sketch import CircleEntity

    frame = frame_for_plane(PlaneName.XY)
    (profile,) = sketch_profiles([CircleEntity(name="c", center=(10.0, 20.0), diameter=8.0)])
    box = f.swept_profile_shape_bbox(frame, profile, 0.0, 5.0)
    assert box.min == pytest.approx((6.0, 16.0, 0.0))
    assert box.max == pytest.approx((14.0, 24.0, 5.0))


def test_swept_profile_shape_bbox_of_a_circle_on_a_tilted_frame_is_the_cylinder_box() -> None:
    from cotaforge.contract import sketch_profiles
    from cotaforge.contract.sketch import CircleEntity
    from cotaforge.verification import datum

    frame = datum.plane_angle(
        frame_for_plane(PlaneName.XY), Axis(origin=(0.0, 0.0, 0.0), direction=(1.0, 0.0, 0.0)), 90.0
    )
    # the XY plane turned 90 deg about X is the XZ-like plane with normal -Y
    (profile,) = sketch_profiles([CircleEntity(name="c", center=(0.0, 0.0), diameter=10.0)])
    box = f.swept_profile_shape_bbox(frame, profile, 0.0, 4.0)
    assert box.min == pytest.approx((-5.0, -4.0, -5.0))
    assert box.max == pytest.approx((5.0, 0.0, 5.0))
    # about a diagonal axis the tilted frame has u and v off the world axes: the square around the circle
    # is rotated with it, so its swept corners overshoot the cylinder
    tilt = datum.plane_angle(
        frame_for_plane(PlaneName.XY), Axis(origin=(0.0, 0.0, 0.0), direction=(1.0, 1.0, 0.0)), 60.0
    )
    box = f.swept_profile_shape_bbox(tilt, profile, 0.0, 10.0)
    assert box == f.cylinder_bbox(Axis(origin=tilt.origin, direction=tilt.n), 5.0, 0.0, 10.0)
    loose = f.swept_profile_bbox(tilt, profile.bbox_uv, 0.0, 10.0)
    assert any(loose.max[i] > box.max[i] + 1e-6 for i in range(3))


def test_swept_profile_shape_bbox_of_a_polygon_uses_its_vertices() -> None:
    from cotaforge.contract import sketch_profiles
    from cotaforge.contract.sketch import LineEntity

    frame = frame_for_plane(PlaneName.XY)
    lines = [
        LineEntity(name="a", start=(0.0, 0.0), end=(10.0, 0.0)),
        LineEntity(name="b", start=(10.0, 0.0), end=(0.0, 10.0)),
        LineEntity(name="c", start=(0.0, 10.0), end=(0.0, 0.0)),
    ]
    (profile,) = sketch_profiles(lines)
    box = f.swept_profile_shape_bbox(frame, profile, 0.0, 2.0)
    assert box.min == pytest.approx((0.0, 0.0, 0.0))
    assert box.max == pytest.approx((10.0, 10.0, 2.0))


# --- radial_hole_volume: a round hole along a radius of a tube -------------------------------------------------


def test_radial_hole_in_a_solid_cylinder_of_the_same_radius_is_the_steinmetz_solid() -> None:
    # two perpendicular cylinders of equal radius R intersect in 16/3 R^3
    assert f.radial_hole_volume(10.0, 0.0, 10.0) == pytest.approx(16.0 / 3.0 * 10.0**3, rel=1e-9)


def test_a_thin_radial_hole_is_a_cylinder_as_long_as_the_diameter() -> None:
    assert f.radial_hole_volume(20.0, 0.0, 0.1) == pytest.approx(math.pi * 0.1**2 * 40.0, rel=1e-3)


def test_a_tube_removes_the_solid_wall_volume_minus_the_bore() -> None:
    solid_outer = f.radial_hole_volume(20.0, 0.0, 3.0)
    solid_bore = f.radial_hole_volume(15.0, 0.0, 3.0)
    assert f.radial_hole_volume(20.0, 15.0, 3.0) == pytest.approx(solid_outer - solid_bore, rel=1e-9)
    assert f.radial_hole_volume(20.0, 15.0, 3.0, depth=None) == f.radial_hole_volume(20.0, 15.0, 3.0)


@pytest.mark.parametrize(
    ("depth", "expected"),
    [(8.0, 141.90804609956), (3.0, 83.22806551336), (None, 283.81609219912)],
)
def test_radial_hole_matches_the_measured_tube_cases(depth: float | None, expected: float) -> None:
    """Tube R20 / r15 with a D6 hole (blind 8, blind 3, through both walls): the values the CAD kernel removes
    within its own 5e-4..9e-4 relative noise on cylinder-cylinder cuts (measured on FreeCAD 1.0.2 and 1.1.3:
    141.777, 83.153, 283.589)."""
    assert f.radial_hole_volume(20.0, 15.0, 3.0, depth) == pytest.approx(expected, rel=1e-9)


def test_radial_hole_wall_of_the_bench_part() -> None:
    assert f.radial_hole_volume(20.0, 15.0, 2.5, 8.0) == pytest.approx(98.4325105814, rel=1e-9)


def test_radial_hole_grows_with_depth_and_stops_at_the_far_wall() -> None:
    previous = 0.0
    for depth in (1.0, 3.0, 5.0, 8.0, 20.0, 34.0, 35.0, 40.0, 100.0):
        removed = f.radial_hole_volume(20.0, 15.0, 3.0, depth)
        assert removed >= previous
        previous = removed
    assert f.radial_hole_volume(20.0, 15.0, 3.0, 40.0) == pytest.approx(f.radial_hole_volume(20.0, 15.0, 3.0))
    # a hole that only reaches the bore removes exactly the near wall: half of the through-all volume
    assert f.radial_hole_volume(20.0, 15.0, 3.0, 20.0) == pytest.approx(
        f.radial_hole_volume(20.0, 15.0, 3.0) / 2.0, rel=1e-3
    )


def test_radial_hole_rejects_a_tube_that_is_not_one() -> None:
    for bad in ((10.0, 10.0, 2.0), (10.0, 12.0, 2.0), (10.0, -1.0, 2.0), (0.0, 0.0, 2.0), (10.0, 0.0, 0.0)):
        with pytest.raises(ValueError, match="radial_hole_volume"):
            f.radial_hole_volume(*bad)
