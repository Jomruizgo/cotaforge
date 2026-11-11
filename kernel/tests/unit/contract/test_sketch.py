"""Tests for cotaforge.contract.sketch: entities and sketch_profiles."""

import math

import pytest
from pydantic import TypeAdapter

from cotaforge.contract.sketch import (
    Arc3Entity,
    ArcEndsEntity,
    ArcEntity,
    CircleEntity,
    LineEntity,
    NamedDimension,
    RectEntity,
    SketchEntity,
    SlotEntity,
    SquareEntity,
    arc_geometry,
    sketch_profiles,
)


def test_rect_profile_area_and_centroid() -> None:
    rect = RectEntity(name="outline", center=(50.0, 30.0), width=100.0, height=60.0)
    profiles = sketch_profiles((rect,))
    assert len(profiles) == 1
    profile = profiles[0]
    assert profile.kind == "rect"
    assert profile.area_mm2 == pytest.approx(6000.0)
    assert profile.centroid == pytest.approx((50.0, 30.0))
    assert profile.bbox_uv == ((0.0, 0.0), (100.0, 60.0))


def test_square_profile_is_a_rect_profile_with_side_squared() -> None:
    """The profile is geometry (a square IS a rectangle); the declared intent only changes dimensioning."""
    square = SquareEntity(name="boss", center=(15.0, 15.0), side=20.0)
    (profile,) = sketch_profiles((square,))
    assert profile.kind == "rect"
    assert profile.area_mm2 == pytest.approx(400.0)
    assert profile.centroid == (15.0, 15.0) and profile.center == (15.0, 15.0)
    assert profile.bbox_uv == ((5.0, 5.0), (25.0, 25.0))


def test_square_is_declared_never_inferred_from_a_rect() -> None:
    parsed = TypeAdapter(SketchEntity).validate_python({"kind": "square", "name": "q", "center": [0, 0], "side": 4})
    assert isinstance(parsed, SquareEntity) and parsed.side == 4.0
    equal_sides = TypeAdapter(SketchEntity).validate_python(
        {"kind": "rect", "name": "r", "center": [0, 0], "width": 4, "height": 4}
    )
    assert isinstance(equal_sides, RectEntity)  # width == height stays a rect: two driving dimensions
    with pytest.raises(ValueError, match="width"):
        TypeAdapter(SketchEntity).validate_python({"kind": "square", "name": "q", "center": [0, 0], "width": 4})
    assert NamedDimension(name="s", entity="q", attribute="side").attribute == "side"


def test_circle_profile_area() -> None:
    circle = CircleEntity(name="hole", center=(0.0, 0.0), diameter=10.0)
    profiles = sketch_profiles((circle,))
    assert len(profiles) == 1
    profile = profiles[0]
    assert profile.kind == "circle"
    assert profile.area_mm2 == pytest.approx(25.0 * math.pi)
    assert profile.center == (0.0, 0.0)
    assert profile.radius == pytest.approx(5.0)


def test_l_shaped_polygon_profile() -> None:
    # L-shape: 100x60 rectangle with a 40x30 notch cut from the top-right corner.
    # Hand-computed via shoelace: area = 100*60 - 40*30 = 6000 - 1200 = 4800
    points = [
        (0.0, 0.0),
        (100.0, 0.0),
        (100.0, 30.0),
        (60.0, 30.0),
        (60.0, 60.0),
        (0.0, 60.0),
    ]
    lines = tuple(
        LineEntity(name=f"l{i}", start=points[i], end=points[(i + 1) % len(points)]) for i in range(len(points))
    )
    profiles = sketch_profiles(lines)
    assert len(profiles) == 1
    profile = profiles[0]
    assert profile.kind == "polygon"
    assert profile.area_mm2 == pytest.approx(4800.0)
    # Hand-computed centroid of the L-shape (composed of two rectangles):
    # R1: 100x30 at center (50, 15), area 3000
    # R2: 60x30 at center (30, 45), area 1800
    # cx = (3000*50 + 1800*30) / 4800 = (150000 + 54000) / 4800 = 42.5
    # cy = (3000*15 + 1800*45) / 4800 = (45000 + 81000) / 4800 = 26.25
    assert profile.centroid[0] == pytest.approx(42.5)
    assert profile.centroid[1] == pytest.approx(26.25)


def test_multiple_disjoint_profiles() -> None:
    rect = RectEntity(name="outer", center=(0.0, 0.0), width=10.0, height=10.0)
    circle = CircleEntity(name="hole", center=(20.0, 20.0), diameter=4.0)
    profiles = sketch_profiles((rect, circle))
    assert len(profiles) == 2
    kinds = {p.kind for p in profiles}
    assert kinds == {"rect", "circle"}


# --- arcs ---------------------------------------------------------------------------------------------------------


def test_the_three_arc_forms_describe_the_same_geometry() -> None:
    by_angles = arc_geometry(ArcEntity(name="a", center=(1.0, 2.0), radius=5.0, start_deg=0.0, end_deg=90.0))
    middle = (1 + 5 * math.cos(math.pi / 4), 2 + 5 * math.sin(math.pi / 4))
    three = arc_geometry(Arc3Entity(name="a", start=(6.0, 2.0), through=middle, end=(1.0, 7.0)))
    ends = arc_geometry(ArcEndsEntity(name="a", center=(1.0, 2.0), start=(6.0, 2.0), end=(1.0, 7.0)))
    for geometry in (three, ends):
        assert geometry.center == pytest.approx(by_angles.center)
        assert (geometry.radius, geometry.start_angle, geometry.sweep) == pytest.approx(
            (by_angles.radius, by_angles.start_angle, by_angles.sweep)
        )
    assert by_angles.sweep == pytest.approx(math.pi / 2)


def test_the_direction_of_an_arc_is_the_side_its_middle_point_or_ccw_flag_names() -> None:
    long_way = Arc3Entity(name="a", start=(5.0, 0.0), through=(-5.0, 0.0), end=(0.0, 5.0))
    assert arc_geometry(long_way).sweep == pytest.approx(1.5 * math.pi)  # through the far side: 270 degrees
    cw = ArcEndsEntity(name="a", center=(0.0, 0.0), start=(5.0, 0.0), end=(0.0, 5.0), ccw=False)
    assert arc_geometry(cw).sweep == pytest.approx(1.5 * math.pi)
    assert arc_geometry(ArcEndsEntity(name="a", center=(0.0, 0.0), start=(5.0, 0.0), end=(0.0, 5.0))).sweep == (
        pytest.approx(0.5 * math.pi)
    )


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"kind": "arc3", "start": [0, 0], "through": [5, 0], "end": [10, 0]}, "collinear"),
        ({"kind": "arc_ends", "center": [0, 0], "start": [5, 0], "end": [0, 6]}, "same distance"),
        ({"kind": "arc_ends", "center": [0, 0], "start": [5, 0], "end": [5, 0]}, "coincide"),
        ({"kind": "arc", "center": [0, 0], "radius": 0, "start_deg": 0, "end_deg": 90}, "radius"),
        ({"kind": "arc", "center": [0, 0], "radius": 5, "start_deg": 10, "end_deg": 10}, "coincide"),
        ({"kind": "slot", "start": [0, 0], "end": [0, 0], "width": 4}, "centres"),
        ({"kind": "slot", "start": [0, 0], "end": [10, 0], "width": 0}, "width"),
    ],
)
def test_a_degenerate_arc_or_slot_is_refused_with_the_reason(data: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        TypeAdapter(SketchEntity).validate_python({"name": "x", **data})


def test_lines_and_arcs_chain_into_one_closed_profile() -> None:
    """A D shape: the diameter and a half circle. Area pi r^2 / 2, centroid 4 r / (3 pi) above the diameter."""
    d_shape = (
        LineEntity(name="base", start=(-5.0, 0.0), end=(5.0, 0.0)),
        Arc3Entity(name="dome", start=(5.0, 0.0), through=(0.0, 5.0), end=(-5.0, 0.0)),
    )
    (profile,) = sketch_profiles(d_shape)
    assert profile.kind == "polygon"
    assert profile.area_mm2 == pytest.approx(math.pi * 25 / 2, rel=1e-12)
    assert profile.centroid == pytest.approx((0.0, 4 * 5 / (3 * math.pi)), abs=1e-9)
    assert [*profile.bbox_uv[0], *profile.bbox_uv[1]] == pytest.approx([-5.0, 0.0, 5.0, 5.0], abs=1e-9)
    assert profile.points is not None and len(profile.points) > 100  # the arc is sampled finely


def test_the_entities_of_a_chain_may_come_in_any_order_and_direction() -> None:
    body = (
        Arc3Entity(name="right", start=(10.0, 5.0), through=(15.0, 0.0), end=(10.0, -5.0)),  # cw
        LineEntity(name="top", start=(10.0, 5.0), end=(0.0, 5.0)),  # reversed
        ArcEndsEntity(name="left", center=(0.0, 0.0), start=(0.0, 5.0), end=(0.0, -5.0), ccw=True),
        LineEntity(name="bottom", start=(0.0, -5.0), end=(10.0, -5.0)),
    )
    (profile,) = sketch_profiles(body)
    # 10 x 10 box + a half disc on each side
    assert profile.area_mm2 == pytest.approx(100 + math.pi * 25, rel=1e-9)


def test_a_slot_is_a_closed_profile_of_two_lines_and_two_half_circles() -> None:
    slot = SlotEntity(name="s", start=(0.0, 0.0), end=(20.0, 0.0), width=6.0)
    (profile,) = sketch_profiles((slot,))
    assert profile.area_mm2 == pytest.approx(20 * 6 + math.pi * 3**2, rel=1e-12)
    assert profile.centroid == pytest.approx((10.0, 0.0), abs=1e-9)
    assert [*profile.bbox_uv[0], *profile.bbox_uv[1]] == pytest.approx([-3.0, -3.0, 23.0, 3.0], abs=1e-9)
    slanted = SlotEntity(name="s", start=(0.0, 0.0), end=(10.0, 10.0), width=4.0)
    assert sketch_profiles((slanted,))[0].area_mm2 == pytest.approx(math.hypot(10, 10) * 4 + math.pi * 4, rel=1e-12)


def test_a_slot_next_to_other_profiles_stays_its_own_profile() -> None:
    slot = SlotEntity(name="s", start=(0.0, 0.0), end=(10.0, 0.0), width=4.0)
    profiles = sketch_profiles((slot, CircleEntity(name="c", center=(30.0, 0.0), diameter=6.0)))
    assert sorted(p.kind for p in profiles) == ["circle", "polygon"]


def test_an_open_chain_with_an_arc_names_the_point_where_it_breaks() -> None:
    chain = (
        LineEntity(name="l", start=(0.0, 0.0), end=(10.0, 0.0)),
        Arc3Entity(name="a", start=(10.0, 0.0), through=(12.0, 3.0), end=(10.0, 6.0)),
    )
    with pytest.raises(ValueError, match="open chain"):
        sketch_profiles(chain)


def test_rect_without_chamfer_is_unchanged() -> None:
    """The default leaves every existing plan as it was: no chamfer in the dump's meaning, a plain rect profile."""
    rect = RectEntity(name="outline", center=(0.0, 0.0), width=100.0, height=60.0)
    assert rect.chamfer_mm is None
    assert RectEntity.model_validate({"kind": "rect", "name": "outline", "center": [0, 0], "width": 1, "height": 1})
    (profile,) = sketch_profiles((rect,))
    assert profile.kind == "rect"


def test_chamfered_rect_profile_is_an_octagon_with_the_corners_cut() -> None:
    rect = RectEntity(name="plate", center=(50.0, 30.0), width=100.0, height=60.0, chamfer_mm=4.0)
    (profile,) = sketch_profiles((rect,))
    assert profile.kind == "polygon"
    assert profile.area_mm2 == pytest.approx(6000.0 - 2 * 4.0 * 4.0)
    assert profile.centroid == pytest.approx((50.0, 30.0))
    assert profile.bbox_uv == ((0.0, 0.0), (100.0, 60.0))
    assert profile.points is not None and len(profile.points) == 8
    assert (4.0, 0.0) in profile.points and (0.0, 4.0) in profile.points
    assert (0.0, 0.0) not in profile.points


@pytest.mark.parametrize("chamfer", [0.0, -1.0, 10.0, 12.0])
def test_chamfer_must_be_positive_and_smaller_than_half_the_shorter_side(chamfer: float) -> None:
    with pytest.raises(ValueError, match="smaller than half of the shorter side"):
        RectEntity(name="plate", center=(0.0, 0.0), width=100.0, height=20.0, chamfer_mm=chamfer)


def test_chamfer_is_a_named_dimension_attribute() -> None:
    assert NamedDimension(name="c", entity="plate", attribute="chamfer").attribute == "chamfer"
