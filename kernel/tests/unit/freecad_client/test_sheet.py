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
    k = plan.iso_scale or s  # the isometric view may have a scale of its own
    boxes = {
        "front": (fx - w * s / 2, fy - h * s / 2, fx + w * s / 2, fy + h * s / 2),
        "right": (fx - w * s / 2 - gap - d * s, fy - h * s / 2, fx - w * s / 2 - gap, fy + h * s / 2),
        "top": (fx - w * s / 2, fy + h * s / 2 + gap, fx + w * s / 2, fy + h * s / 2 + gap + d * s),
        "iso": (
            plan.iso[0] - iso_w * k / 2,
            plan.iso[1] - iso_h * k / 2,
            plan.iso[0] + iso_w * k / 2,
            plan.iso[1] + iso_h * k / 2,
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


def _block(plan: layout.Layout, part: tuple[float, float, float], **kw: object) -> tuple[float, float, float, float]:
    """The page rectangle (y down) of the group of standard views and its room, as the plan placed it."""
    w, d, h = part
    spec = layout.Spec(w, d, h, layout.iso_extent(w, d, h), spacing=plan.spacing, **kw)  # type: ignore[arg-type]
    block_w, block_h, front_x, front_y = layout._extents(plan.scale, spec)  # noqa: SLF001
    x, y = plan.front[0] - front_x, plan.front[1] - front_y
    return x, y, x + block_w, y + block_h


@pytest.mark.parametrize("scale", [None, 1.0, 0.5])
def test_the_block_is_centred_in_the_free_area_not_hung_from_its_top_left(scale: float | None) -> None:
    """A 100 x 60 x 8 plate on A3: the whole block (views, room for dimensions, isometric) sits halfway between the top
    margin and the title block, and across the paper."""
    part = (100.0, 60.0, 8.0)
    plan = layout.plan(*part, *A3, scale=scale)
    spec = layout.Spec(*part, layout.iso_extent(*part), spacing=plan.spacing)
    need_w, need_h = layout._needed_spec(plan.scale, spec)  # noqa: SLF001
    left, top, right, bottom = layout.drawing_area(*A3)
    x0, y0, _, _ = _block(plan, part)
    assert y0 - top > 0 and bottom - (y0 + need_h) == pytest.approx(y0 - top)  # as much above as below
    assert x0 - left == pytest.approx(right - (x0 + need_w))  # and as much to the left as to the right


def test_a_block_that_fills_the_height_hangs_from_the_top_as_before() -> None:
    """No spare height (or no height given): nothing to centre, the block starts at the top margin."""
    assert layout._drop(200.0, 100.0) == 0.0  # noqa: SLF001
    assert layout._drop(100.0, None) == 0.0  # noqa: SLF001
    assert layout._drop(100.0, 200.0) == 50.0  # noqa: SLF001
    plain = layout._blocks(1.0, 100.0, 60.0, 8.0, layout.iso_extent(100.0, 60.0, 8.0), 25.0, 380.0, 15.0)  # noqa: SLF001
    centred = layout._blocks(
        1.0, 100.0, 60.0, 8.0, layout.iso_extent(100.0, 60.0, 8.0), 25.0, 380.0, 15.0, area_h=209.0
    )  # noqa: SLF001
    assert plain[0][1] == 15.0 and centred[0][1] > 15.0


def test_views_move_apart_when_there_is_room_up_to_twice_the_spacing() -> None:
    roomy = layout.plan(100.0, 60.0, 8.0, *A3, scale=1.0)
    assert roomy.spacing == layout.SPACING_MAX_MM == 2 * layout.SPACING_MM
    callouts = layout.plan(100.0, 60.0, 8.0, *A3, scale=1.0, spacing_mm=34.0, right_mm=24.0)
    assert 34.0 < callouts.spacing <= layout.SPACING_MAX_MM  # the room the callouts asked for is a floor, never less
    tight = layout.plan(60.0, 40.0, 30.0, *A4, scale=5.0)
    assert (
        tight.fits is False and tight.spacing == layout.SPACING_MM
    )  # no room (it overflows): the spacing is what it was
    never = layout.plan(100.0, 60.0, 8.0, *A3, scale=1.0, spacing_mm=60.0)
    assert never.spacing == 60.0  # a wider spacing than the cap is not narrowed


def test_a_sheet_of_one_standard_view_has_no_gap_to_widen() -> None:
    one = layout.plan(100.0, 0.0, 60.0, *A3, scale=1.0, has_top=False, has_right=False)
    assert one.spacing == layout.SPACING_MM


def test_spreading_and_centring_keep_the_room_the_dimensions_asked_for() -> None:
    """Rows of baseline dimensions below, above and left of the views, callout room to the right, third angle: the block
    (with its room) still lies inside the drawing area and the views do not overlap."""
    left, top, right, bottom = layout.drawing_area(*A3)
    part = (100.0, 60.0, 20.0)
    for third in (False, True):
        extra = {"below_mm": 43.0, "above_mm": 22.0, "left_mm": 29.0, "right_mm": 24.0, "third": third}
        plan = layout.plan(*part, *A3, scale=1.0, **extra)
        x0, y0, x1, y1 = _block(plan, part, **extra)
        assert left - 1e-6 <= x0 and x1 <= right + 1e-6 and top - 1e-6 <= y0 and y1 <= bottom + 1e-6
        assert plan.fits and plan.spacing >= 20.0


def test_an_assembly_block_stays_clear_of_the_parts_list_when_centred() -> None:
    reserve = (layout.TITLE_BLOCK_WIDTH_MM, 6.0 * 10)
    plan = layout.plan(160, 100, 87, *A3, reserve=reserve)
    held = (
        A3[0] - layout.FRAME_MARGIN_MM - reserve[0] - 2.0,
        A3[1] - layout.TITLE_BLOCK_TOP_MM - reserve[1] - 2.0,
        A3[0] - layout.FRAME_MARGIN_MM,
        A3[1] - layout.TITLE_BLOCK_TOP_MM,
    )
    w, d, h = 160.0, 100.0, 87.0
    spec = layout.Spec(w, d, h, layout.iso_extent(w, d, h), spacing=plan.spacing)
    left, top, right, bottom = layout.drawing_area(*A3)
    block_w, block_h, _, _ = layout._extents(plan.scale, spec)  # noqa: SLF001
    x0, y0, _, _ = _block(plan, (w, d, h))
    iso_x = x0 + block_w + layout.ISO_GAP_MM
    iso_h = spec.iso[1] * plan.scale + layout.DIM_MARGIN_MM
    boxes = [(x0, y0, x0 + block_w, y0 + block_h), (iso_x, y0, iso_x + spec.iso[0] * plan.scale, y0 + iso_h)]
    assert layout._clear_of(boxes, held)  # noqa: SLF001
    assert plan.fits and y0 >= top


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
