"""``worker/sheet_layout.py`` in isolation: where the views of a drawing sheet go, and how a measured value and a
scale are written. Plain arithmetic, no FreeCAD needed."""

import math
import sys
from pathlib import Path

import pytest

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"


if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))  # the worker modules import their siblings by bare name

import sheet_layout as layout  # noqa: E402
import sheet_model as model  # noqa: E402

A4 = layout.PAGES_MM["A4"]
A3 = layout.PAGES_MM["A3"]


# --- layout -----------------------------------------------------------------------------------------------------------


def test_the_isometric_extent_of_a_unit_cube() -> None:
    width, height = layout.iso_extent(1, 1, 1)
    assert width == pytest.approx(math.sqrt(2))
    assert height == pytest.approx(4 / math.sqrt(6))


def test_a_small_bracket_takes_the_largest_standard_scale_that_fits() -> None:
    plan = layout.plan(60, 40, 30, *A4)
    assert (plan.scale, plan.standard, plan.fits) == (1.0, True, True)
    # a bigger one would not have fitted
    assert not layout._fits(layout._needed(2.0, 60, 40, 30, layout.iso_extent(60, 40, 30)), *_area_size(A4))


def test_a_tiny_part_is_enlarged_and_a_big_one_reduced() -> None:
    assert layout.plan(5, 5, 5, *A4).scale == 5.0  # 10:1 would need 134 mm of height for the front and top views
    big = layout.plan(500, 300, 200, *A4)
    assert big.scale in layout.STANDARD_SCALES and big.scale <= 0.2 and big.fits


def test_a_part_no_standard_scale_fits_is_drawn_at_the_largest_one_that_does() -> None:
    plan = layout.plan(50000, 30000, 20000, *A4)
    assert plan.standard is False and plan.fits is True
    assert plan.scale < min(layout.STANDARD_SCALES)


def test_a_scale_asked_for_is_kept_and_overflow_is_reported() -> None:
    kept = layout.plan(60, 40, 30, *A4, scale=0.5)
    assert (kept.scale, kept.standard, kept.fits) == (0.5, True, True)
    too_big = layout.plan(60, 40, 30, *A4, scale=5.0)
    assert too_big.scale == 5.0 and too_big.fits is False


def test_a3_takes_a_larger_scale_than_a4_for_the_same_part() -> None:
    assert layout.plan(200, 150, 100, *A3).scale > layout.plan(200, 150, 100, *A4).scale


@pytest.mark.parametrize("page", [A4, A3])
@pytest.mark.parametrize("part", [(60, 40, 30), (120, 20, 80), (10, 10, 200), (300, 200, 20)])
def test_the_views_stay_inside_the_drawing_area_and_never_overlap(page: tuple[float, float], part: tuple) -> None:
    w, d, h = part
    plan = layout.plan(w, d, h, *page)
    s, gap = plan.scale, plan.spacing
    fx, fy = plan.front
    iso_w, iso_h = layout.iso_extent(w, d, h)
    boxes = {
        "front": (fx - w * s / 2, fy - h * s / 2, fx + w * s / 2, fy + h * s / 2),
        "right": (fx - w * s / 2 - gap - d * s, fy - h * s / 2, fx - w * s / 2 - gap, fy + h * s / 2),
        "top": (fx - w * s / 2, fy + h * s / 2 + gap, fx + w * s / 2, fy + h * s / 2 + gap + d * s),
        "iso": (
            plan.iso[0] - iso_w * s / 2,
            plan.iso[1] - iso_h * s / 2,
            plan.iso[0] + iso_w * s / 2,
            plan.iso[1] + iso_h * s / 2,
        ),
    }
    left, top, right, bottom = layout.drawing_area(*page)
    for name, (x0, y0, x1, y1) in boxes.items():
        assert left - 1e-6 <= x0 and x1 <= right + 1e-6 and top - 1e-6 <= y0 and y1 <= bottom + 1e-6, name
    names = list(boxes)
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            ax0, ay0, ax1, ay1 = boxes[a]
            bx0, by0, bx1, by1 = boxes[b]
            assert ax1 <= bx0 or bx1 <= ax0 or ay1 <= by0 or by1 <= ay0, (a, b)


def _area_size(page: tuple[float, float]) -> tuple[float, float]:
    left, top, right, bottom = layout.drawing_area(*page)
    return right - left, bottom - top


# --- numbers on the sheet ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "text"), [(60.0, "60"), (12.5, "12.5"), (12.504, "12.5"), (0.0, "0"), (1e-9, "0"), (100.25, "100.25")]
)
def test_values_are_written_like_a_drawing_writes_them(value: float, text: str) -> None:
    assert model.fmt(value) == text


@pytest.mark.parametrize(
    ("scale", "text"), [(1.0, "1 : 1"), (0.5, "1 : 2"), (0.2, "1 : 5"), (2.0, "2 : 1"), (10.0, "10 : 1")]
)
def test_the_title_block_scale(scale: float, text: str) -> None:
    assert model.fmt_scale(scale) == text
