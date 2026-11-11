"""Tests for cotaforge.verification.thresholds."""

from cotaforge.contract import BoundingBox
from cotaforge.verification.thresholds import Thresholds


def test_defaults_match_architecture() -> None:
    t = Thresholds()
    assert (t.abs_mm, t.abs_mm2, t.abs_mm3) == (1e-3, 1e-3, 1e-3)
    assert t.rel == 1e-4
    assert t.angle_deg == 0.5
    assert t.position_mm == 1e-2


def test_close_uses_abs_plus_rel_for_mm_units() -> None:
    t = Thresholds()
    # |e - o| <= abs + rel * |e| -> 1000 mm3: tolerance 1e-3 + 0.1 = 0.101
    assert t.close(1000.0, 1000.1, "mm3")
    assert not t.close(1000.0, 1000.2, "mm3")
    assert t.close(0.0, 0.0005, "mm")
    assert not t.close(0.0, 0.002, "mm")
    assert t.close(-500.0, -500.05, "mm3")


def test_close_count_is_exact() -> None:
    t = Thresholds()
    assert t.close(4, 4, "count")
    assert not t.close(4, 5, "count")
    assert not t.close(4, 4.5, "count")


def test_close_deg_uses_angle_threshold() -> None:
    t = Thresholds()
    assert t.close(90.0, 90.4, "deg")
    assert not t.close(90.0, 90.6, "deg")


def test_close_position_and_direction_helpers() -> None:
    t = Thresholds()
    assert t.close_position((0.0, 0.0, 0.0), (0.005, 0.0, 0.0))
    assert not t.close_position((0.0, 0.0, 0.0), (0.02, 0.0, 0.0))
    assert t.parallel((0.0, 0.0, 1.0), (0.0, 0.0, -1.0), either_sense=True)
    assert not t.parallel((0.0, 0.0, 1.0), (0.0, 0.0, -1.0), either_sense=False)
    assert t.parallel((0.0, 0.0, 1.0), (0.0, 0.001, 1.0))
    assert not t.parallel((0.0, 0.0, 1.0), (0.0, 0.02, 1.0))


def test_close_bbox_and_bbox_within() -> None:
    t = Thresholds()
    a = BoundingBox(min=(0.0, 0.0, 0.0), max=(100.0, 60.0, 8.0))
    b = BoundingBox(min=(0.0, 0.001, 0.0), max=(100.0, 60.0, 8.005))
    c = BoundingBox(min=(0.0, 0.0, 0.0), max=(100.0, 60.0, 9.0))
    assert t.close_bbox(a, b)
    assert not t.close_bbox(a, c)
    assert t.bbox_within(b, a)
    assert not t.bbox_within(c, a)
