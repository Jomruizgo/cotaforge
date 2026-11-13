# ruff: noqa: E501
"""The isometric view at a scale of its own (ISO 5455: a pictorial view may have one, written under it): the automatic layout
gives the main views the largest standard scale the sheet takes, and the review counts it as a remedy. No FreeCAD."""

import sys
from pathlib import Path

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import sheet_context as sc  # noqa: E402
import sheet_layout as layout  # noqa: E402
import sheet_model as model  # noqa: E402
import sheet_review as review  # noqa: E402
import sheet_viewchecks as checks  # noqa: E402
import sheet_viewrules as vr  # noqa: E402

A4 = layout.PAGES_MM["A4"]
SHAFT = (60.0, 12.0, 12.0)  # a turned part laid out as length x diameter x diameter


def _plan(size: tuple[float, float, float], page: tuple[float, float] = A4, **kwargs: object) -> layout.Layout:
    return layout.plan(*size, *page, iso_dims=size, has_top=False, **kwargs)  # type: ignore[arg-type]


def test_a_small_part_takes_a_larger_main_scale_with_the_isometric_view_at_its_own() -> None:
    same = layout.plan(*SHAFT, *A4, 1.0, None, True, False, iso_dims=SHAFT)  # what it was: 1:1 with everything at 1:1
    lay = _plan(SHAFT)
    assert same.iso_scale == 0.0
    assert lay.scale == 2.0 and lay.iso_scale == 1.0 and lay.fits
    assert (lay.scale, lay.iso_scale) in {(s, t) for s in layout.STANDARD_SCALES for t in layout.STANDARD_SCALES}


def test_the_isometric_view_keeps_the_sheets_scale_when_that_gives_the_same_main_scale() -> None:
    lay = _plan((60.0, 20.0, 20.0))
    assert lay.iso_scale == 0.0 and lay.scale == 1.0


def test_the_isometric_view_is_at_most_one_standard_step_below_the_sheets() -> None:
    for size in ((30.0, 10.0, 10.0), (60.0, 12.0, 12.0), (80.0, 10.0, 10.0)):
        for page in layout.PAGES_MM.values():
            lay = _plan(size, page)
            if lay.iso_scale:
                assert lay.iso_scale < lay.scale and lay.scale / lay.iso_scale <= layout.ISO_OWN_RATIO + 1e-9


def test_the_isometric_view_and_its_label_stay_inside_the_drawing_area() -> None:
    lay = _plan(SHAFT)
    left, top, right, bottom = layout.drawing_area(*A4)
    iso_w, iso_h = layout.iso_extent(*SHAFT)
    x, y = lay.iso
    assert left <= x - iso_w * lay.iso_scale / 2 and x + iso_w * lay.iso_scale / 2 <= right
    assert top <= y - iso_h * lay.iso_scale / 2 and y + iso_h * lay.iso_scale / 2 + layout.ISO_LABEL_MM <= bottom + 1e-6


def test_a_scale_that_was_asked_for_makes_the_isometric_view_give_way_before_the_sheet_overflows() -> None:
    plain = layout.plan(*SHAFT, *A4, 2.0, None, False, False, iso_dims=SHAFT)  # no isometric view: fits at 2:1
    asked = _plan(SHAFT, scale=2.0)
    assert plain.fits and asked.fits and asked.scale == 2.0 and asked.iso_scale == 1.0


def test_an_assembly_sheet_keeps_every_view_at_the_sheets_scale() -> None:
    lay = _plan(SHAFT, reserve=(100.0, 30.0))
    assert lay.iso_scale == 0.0


def test_a_sheet_without_isometric_view_has_no_scale_for_it() -> None:
    assert _plan(SHAFT, isometric=False).iso_scale == 0.0


def test_auto_paper_chooses_the_smallest_paper_with_the_enlargement() -> None:
    assert layout.auto_paper(*SHAFT, has_top=False, iso_dims=SHAFT) == ("A4", 2.0)


def test_the_rule_counts_the_isometric_scale_as_a_remedy() -> None:
    area = (25.0, 58.0 + 10.0, 282.0, 190.0)
    rects = [(60.0, 100.0, 190.0, 120.0)]  # 130 mm wide: at 2:1 it does not fit, so no larger scale is a remedy
    assert vr.sheet_underused(rects, area, 1.0, scales=layout.STANDARD_SCALES) is None  # no remedy known: not underused
    found = vr.sheet_underused(rects, area, 1.0, scales=layout.STANDARD_SCALES, iso_own=(2.0, 1.0))
    assert found is not None and found.reason == "small" and found.iso_main == 2.0 and found.iso_scale == 1.0
    assert (
        vr.sheet_underused(rects, area, 2.0, scales=layout.STANDARD_SCALES, iso_own=(2.0, 1.0)) is None
    )  # already there


def test_the_review_blocks_a_sheet_that_the_isometric_scale_would_fill() -> None:
    width, height = A4
    left, top, right, bottom = layout.drawing_area(width, height)
    area = (left, height - bottom, right, height - top)
    ctx = sc.Context(
        sheet=model.Sheet(width, height), views={}, holes=[], geometry=sc.Geometry(), area=area, language="en"
    )
    cx, cy = (area[0] + area[2]) / 2, (area[1] + area[3]) / 2
    boxes = [
        (cx - 65, cy - 6, cx - 5, cy + 6),
        (cx + 30, cy - 8, cx + 65, cy + 8),
    ]  # the shaft and its isometric view at 1:1
    ctx.extras.update({"view_boxes": boxes, "main_scale": 1.0, "layout_mm": SHAFT, "angle": "first", "views_extra": {}})
    ctx.views = {"front": sc.ViewInfo("front", 0, 0, 1.0, (0, -1, 0), (1, 0, 0), (0, 0, 0), boxes[0])}
    found = [f for f in checks.check(review.ReviewInput([], {}, ctx, ctx.sheet)) if f.code == "sheet_underused"]
    assert found and "its own scale" in found[0].message and "scale=2" in found[0].message, found
