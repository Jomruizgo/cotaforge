# ruff: noqa: E501
"""``sheet_underused``: a sheet whose views and dimensions cover too little of the free drawing area, or sit in a corner,
on synthetic data (no FreeCAD): the pure rule and the review finding it becomes."""

import sys
from pathlib import Path

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import sheet_checks as checks_module  # noqa: E402
import sheet_context as sc  # noqa: E402
import sheet_layout as layout  # noqa: E402
import sheet_model as model  # noqa: E402
import sheet_review as review  # noqa: E402
import sheet_viewchecks as checks  # noqa: E402
import sheet_viewrules as vr  # noqa: E402


def _area(page: str) -> vr.Rect:
    """The free drawing area of a paper as the review has it (page mm, y up)."""
    width, height = layout.PAGES_MM[page]
    left, top, right, bottom = layout.drawing_area(width, height)
    return (left, height - bottom, right, height - top)


def _centred(area: vr.Rect, width: float, height: float) -> vr.Rect:
    cx, cy = (area[0] + area[2]) / 2, (area[1] + area[3]) / 2
    return (cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2)


def _found(page: str, scale: float, rects: list[vr.Rect], *, assembly: bool = False) -> list[review.Finding]:
    width, height = layout.PAGES_MM[page]
    ctx = sc.Context(
        sheet=model.Sheet(width, height), views={}, holes=[], geometry=sc.Geometry(), area=_area(page), language="es"
    )
    ctx.extras.update({"view_boxes": rects, "main_scale": scale})
    if assembly:
        ctx.assembly = object()  # type: ignore[assignment]
    return [f for f in checks.check(review.ReviewInput([], {}, ctx, ctx.sheet)) if f.code == "sheet_underused"]


def _rule(page: str, scale: float, rects: list[vr.Rect], corner: bool = True) -> vr.Underused | None:
    area = _area(page)
    papers = {
        name: (r - left, bottom - top)
        for name, (pw, ph) in layout.PAGES_MM.items()
        if name != "Letter"
        for left, top, r, bottom in [layout.drawing_area(pw, ph)]
    }
    return vr.sheet_underused(
        rects,
        area,
        scale,
        (area[2] - area[0], area[3] - area[1]),
        corner,
        layout.STANDARD_SCALES,
        papers,
    )


def test_the_a3_sheet_of_a_100_by_60_plate_at_1_to_1_is_balanced() -> None:
    """The real example: the block (views and dimensions, 380 x 170 mm) centred in the area, a larger scale does not fit."""
    assert _rule("A3", 1.0, [_centred(_area("A3"), 380.0, 170.0)]) is None
    assert _found("A3", 1.0, [_centred(_area("A3"), 380.0, 170.0)]) == []


def test_a_small_part_at_1_to_5_on_an_a1_sheet_is_flagged_with_a_larger_scale_and_a_smaller_paper() -> None:
    rects = [_centred(_area("A1"), 150.0, 45.0)]
    rule = _rule("A1", 0.2, rects)
    assert rule is not None and rule.reason == "small" and rule.coverage < vr.LOW_COVERAGE
    assert rule.scale == 1.0  # the largest standard scale at which 150 x 45 mm still fits 801 x 506 mm: 5x is 1:1
    assert rule.paper == "A4"
    (found,) = _found("A1", 0.2, rects)
    assert found.severity == "warning" and "scale=1" in found.message and "template=A4" in found.message
    assert "desperdicia" in found.message


def test_the_finding_never_holds_an_export() -> None:
    assert "sheet_underused" in checks_module.NON_BLOCKING


def test_a_block_in_the_corner_is_flagged_even_when_it_covers_enough_paper() -> None:
    area = _area("A3")
    corner = (area[0], area[3] - 160.0, area[0] + 300.0, area[3])  # 300 x 160 mm hung from the top left corner
    rule = _rule("A3", 1.0, [corner])
    assert rule is not None and rule.reason == "corner" and rule.coverage > vr.LOW_COVERAGE
    assert rule.side == "right" and rule.free_mm > vr.CORNER_MIN_MM and rule.opposite_mm == 0.0
    (found,) = _found("A3", 1.0, [corner])
    assert "esquina" in found.message and "la disposición es la automática" in found.message  # no larger scale fits


def test_the_same_block_centred_is_not_in_a_corner() -> None:
    assert _rule("A3", 1.0, [_centred(_area("A3"), 300.0, 160.0)]) is None


def test_a_block_only_a_little_off_centre_is_quiet() -> None:
    area = _area("A3")
    block = _centred(area, 300.0, 160.0)
    nudged = (
        block[0] - 15.0,
        block[1] + 10.0,
        block[2] - 15.0,
        block[3] + 10.0,
    )  # 25 against 55 mm: under 3x, under 60
    assert _rule("A3", 1.0, [nudged]) is None


def test_a_small_sheet_that_is_already_at_the_largest_scale_and_paper_is_not_underused() -> None:
    """A small part on A4 at 5:1: no larger standard scale fits and there is no smaller paper."""
    assert _rule("A4", 5.0, [_centred(_area("A4"), 150.0, 60.0)]) is None


def test_an_assembly_keeps_its_room_for_the_parts_list_so_only_the_small_rule_applies() -> None:
    area = _area("A3")
    corner = (area[0], area[3] - 160.0, area[0] + 300.0, area[3])
    assert _found("A3", 1.0, [corner], assembly=True) == []
    assert len(_found("A1", 0.2, [_centred(_area("A1"), 150.0, 45.0)], assembly=True)) == 1


def test_dimension_strips_count_with_the_views_and_clip_to_the_area() -> None:
    area = _area("A3")
    view = _centred(area, 100.0, 40.0)
    ctx = sc.Context(
        sheet=model.Sheet(420.0, 297.0), views={}, holes=[], geometry=sc.Geometry(), area=area, language="es"
    )
    strip_out = (area[0] - 50.0, area[1] - 50.0, area[0] - 10.0, area[1] - 10.0)  # outside the area: ignored
    strip_in = (view[2], view[1], view[2] + 20.0, view[3])
    strip_over = (view[0], view[3], view[2], area[3] + 30.0)  # sticks out above: clipped to the area
    ctx.extras.update({"view_boxes": [view], "strips": [strip_out, strip_in, strip_over]})
    rects = checks._footprint(ctx)  # noqa: SLF001
    assert len(rects) == 3 and rects[2][3] == area[3]
