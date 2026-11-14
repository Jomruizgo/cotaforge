# ruff: noqa: E501
"""The centre lines of holes in the edge views (``sheet_centre``, ``items_views``, ``sheet_section``, the review): short, the same
reach at BOTH ends, never through a dimension line or a dimension text. No FreeCAD: the ends are measured in sheet
coordinates, on the lines the items drew."""

import math
import sys
from pathlib import Path

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import sheet_centre as centre  # noqa: E402
import sheet_context as sc  # noqa: E402
import sheet_model as model  # noqa: E402
import sheet_section as section  # noqa: E402

from .test_sheet_items import hole  # noqa: E402
from .test_sheet_views import codes, results_of, run_review, views_context  # noqa: E402

OVER = model.CENTRE_OVERSHOOT_MM
FRONT_TOP, FRONT_BOTTOM = 164.0, 156.0  # the front view of the test plate (8 mm thick) on the page


def _vertical_axes(ctx: sc.Context) -> list[model.Line]:
    """The centre lines that run up and down the front view (the holes seen from the side)."""
    box = ctx.views["front"].box
    return [
        e
        for e in ctx.sheet.of_layer(model.AXES)
        if isinstance(e, model.Line) and abs(e.x1 - e.x2) < 1e-9 and box[0] < e.x1 < box[2] and min(e.y1, e.y2) > 140.0
    ]


def _reaches(line: model.Line, low: float = FRONT_BOTTOM, high: float = FRONT_TOP) -> tuple[float, float]:
    """How far the line goes past ``low`` and past ``high`` (the two ends of the feature)."""
    return low - min(line.y1, line.y2), max(line.y1, line.y2) - high


def test_a_through_hole_centre_line_goes_the_same_short_way_past_both_faces() -> None:
    ctx = views_context([hole(0, 10, 10)])
    results_of(ctx, {"type": "view_lines", "hidden": False})
    (line,) = _vertical_axes(ctx)
    below, above = _reaches(line)
    assert math.isclose(below, OVER, abs_tol=1e-6) and math.isclose(above, OVER, abs_tol=1e-6), (below, above)
    assert 2.0 <= OVER <= 3.0  # ISO 128-24


def test_a_blind_hole_centre_line_goes_the_same_way_past_the_entry_face_and_past_the_drill_point() -> None:
    blind = sc.HoleInfo(0, (10.0, 10.0, 8.0), (0.0, 0.0, -1.0), 5.0, 5.0, False, ("face:h0",), None, True, tip=1.5)
    ctx = views_context([blind])
    results_of(ctx, {"type": "view_lines", "hidden": False})
    (line,) = _vertical_axes(ctx)
    tip_y = FRONT_TOP - blind.length  # the point of the drill on the page
    entry, point = max(line.y1, line.y2) - FRONT_TOP, tip_y - min(line.y1, line.y2)
    assert math.isclose(entry, OVER, abs_tol=1e-6), entry
    assert math.isclose(point, OVER, abs_tol=1e-6), point  # was OVER minus the height of the point: the line looked shifted


def test_a_counterbored_hole_is_centred_on_the_whole_bore() -> None:
    through = sc.HoleInfo(0, (10.0, 10.0, 0.0), (0.0, 0.0, 1.0), 9.0, 8.0, True, ("face:a",), None, True)
    counterbore = sc.HoleInfo(1, (10.0, 10.0, 8.0), (0.0, 0.0, -1.0), 14.0, 3.0, False, ("face:b",), None, True)
    ctx = views_context([through, counterbore])
    results_of(ctx, {"type": "view_lines", "hidden": False})
    (line,) = _vertical_axes(ctx)
    below, above = _reaches(line)
    assert math.isclose(below, above, abs_tol=1e-6) and math.isclose(below, OVER, abs_tol=1e-6), (below, above)


def test_the_section_reach_of_a_stack_follows_the_direction_of_each_hole() -> None:
    """A blind hole made from the other face runs the other way along the axis: its far end is not 'centre + depth' along the
    axis of the first hole."""
    first = sc.HoleInfo(0, (10.0, 10.0, 8.0), (0.0, 0.0, -1.0), 6.6, 8.0, True, ("face:a",), None, True)
    from_below = sc.HoleInfo(1, (10.0, 10.0, 0.0), (0.0, 0.0, 1.0), 9.0, 3.0, False, ("face:b",), None, True)
    start, direction, length = section._stack_reach([first, from_below])  # noqa: SLF001
    assert math.isclose(length, 8.0, abs_tol=1e-9), length  # the plate is 8 mm thick, the bore does not leave it
    assert math.isclose(start[2], 8.0, abs_tol=1e-9) and direction == (0.0, 0.0, -1.0)
    tipped = sc.HoleInfo(2, (10.0, 10.0, 8.0), (0.0, 0.0, -1.0), 5.0, 5.0, False, ("face:c",), None, True, tip=1.5)
    assert math.isclose(section._stack_reach([tipped])[2], 6.5, abs_tol=1e-9)  # noqa: SLF001


def test_a_dimension_close_to_the_part_shortens_the_centre_lines_by_the_same_amount_at_both_ends() -> None:
    ctx = views_context([hole(0, 10, 10)])
    results_of(ctx, {"type": "view_lines", "hidden": False}, {"type": "overall", "id": "ov", "offset_mm": 3.0})
    line = next(e for e in _vertical_axes(ctx))
    below, above = _reaches(line)
    assert math.isclose(below, above, abs_tol=1e-6), (below, above)  # still centred on the hole
    assert above < OVER  # ... but shorter: the dimension line is 3 mm over the outline
    dimension_y = FRONT_TOP + 3.0
    assert max(line.y1, line.y2) <= dimension_y - model.CENTRE_CLEAR_MM + 1e-6  # it never reaches the dimension line


def test_a_dimension_far_enough_leaves_the_centre_lines_whole() -> None:
    ctx = views_context([hole(0, 10, 10)])
    results_of(ctx, {"type": "view_lines", "hidden": False}, {"type": "overall", "id": "ov", "offset_mm": 10.0})
    (line,) = _vertical_axes(ctx)
    assert _reaches(line) == (OVER, OVER) or all(math.isclose(r, OVER, abs_tol=1e-6) for r in _reaches(line))


def test_a_centre_line_never_crosses_the_text_of_a_dimension() -> None:
    """The overall width sits over the middle of the plate, over the hole at x = 50: its text is where the line would go."""
    ctx = views_context([hole(0, 50, 10)])
    results_of(ctx, {"type": "view_lines", "hidden": False}, {"type": "overall", "id": "ov", "offset_mm": 4.0})
    (line,) = _vertical_axes(ctx)
    for rect in ctx.extras.get("texts", []):
        assert not (rect[0] <= line.x1 <= rect[2] and rect[1] <= max(line.y1, line.y2) and min(line.y1, line.y2) <= rect[3]), rect
    assert not centre.crossings(ctx)


def test_the_review_flags_a_long_centre_line_that_crosses_a_dimension_and_one_that_is_long_without_a_reason() -> None:
    ctx = views_context()
    found = run_review(
        ctx,
        {"type": "line", "axis": ["face:fillet"], "view": "front", "extend_mm": 9.0},
        {"type": "overall", "id": "ov", "offset_mm": 3.0},
    )
    assert "centre_line_crosses" in codes(found)  # 9 mm past the part goes through the dimension line 3 mm over it
    ctx = views_context()
    found = run_review(ctx, {"type": "line", "axis": ["face:fillet"], "view": "front", "extend_mm": 6.0})
    assert "centre_line_long" in codes(found) and "centre_line_crosses" not in codes(found)
    ctx = views_context()
    found = run_review(ctx, {"type": "line", "axis": ["face:fillet"], "view": "front", "extend_mm": 6.0, "reason": "the shaft is a datum axis"})
    assert not {"centre_line_long", "centre_line_crosses"} & set(codes(found))


def test_a_clean_sheet_has_no_centre_line_finding() -> None:
    found = run_review(views_context([hole(0, 10, 10)]), {"type": "overall", "offset_mm": 10.0}, {"type": "view_lines"})
    assert not {"centre_line_long", "centre_line_crosses"} & set(codes(found))


def test_the_plane_of_symmetry_goes_the_same_short_way_past_both_edges_of_the_view() -> None:
    import items_datum  # noqa: PLC0415

    ctx = views_context()
    top = ctx.views["top"]
    items_datum._symmetry_line(ctx, top, top.project((50.0, 0.0, 0.0))[0], True)  # noqa: SLF001
    (rec,) = ctx.extras[centre.KEY]
    first, last = centre.overshoots(rec)
    assert math.isclose(first, OVER) and math.isclose(last, OVER)
    assert ctx.sheet.of_layer(model.AXES) == [rec.line]
