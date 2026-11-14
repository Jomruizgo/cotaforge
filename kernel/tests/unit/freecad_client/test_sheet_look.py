# ruff: noqa: E501
"""What the agent is shown of a sheet (no FreeCAD): the measured list of what lies wrong (one function, ``layout_findings``,
whose source a better lint can replace) and the choice of zoomed crops (``select_crops``): about 10 px per mm on the
annotated area of each view and on every finding, capped in number and size."""

import sys
from pathlib import Path

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import sheet_collide as collide  # noqa: E402
import sheet_context as sc  # noqa: E402
import sheet_look as look  # noqa: E402
import sheet_model as model  # noqa: E402
import sheet_viewrules as vr  # noqa: E402

BOX = (0.0, 0.0, 340.0, 40.0)  # the long view of a shaft (page mm, y up)


def above(owner: str, x0: float, x1: float, distance: float) -> vr.DimShape:
    line = BOX[3] + distance
    ext = tuple((x, BOX[3] + 1.0, x, line + 2.0) for x in (x0, x1))
    return vr.DimShape(owner, "front", ext, line, x1 - x0, 9.0, True)


def context(**extras: object) -> sc.Context:
    view = sc.ViewInfo("front", 1.0, 1.0, 1.0, (0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (170.0, 20.0, 0.0), BOX)
    ctx = sc.Context(
        sheet=model.Sheet(420.0, 297.0), views={"front": view}, holes=[], geometry=sc.Geometry(), area=(20.0, 40.0, 410.0, 287.0), language="en"
    )
    ctx.extras.update(extras)
    return ctx


# --- the measured list ------------------------------------------------------------------------------------------------------


def test_a_clean_sheet_measures_nothing() -> None:
    assert look.layout_findings(context()) == []
    ok = [above("l260", 0.0, 260.0, 10.0), above("l340", 0.0, 340.0, 17.0)]
    assert look.layout_findings(context(dim_shapes=ok)) == []


def test_the_owners_260_inside_340_is_a_stack_order_finding_with_a_box_over_both_lines() -> None:
    found = look.layout_findings(context(dim_shapes=[above("l340", 0.0, 340.0, 10.0), above("l260", 0.0, 260.0, 17.0)]))
    stack = [f for f in found if f.kind == "stack_order"]
    assert len(stack) == 1 and stack[0].items == ("l340", "l260") and stack[0].view == "front"
    x0, y0, x1, y1 = stack[0].box
    assert x0 <= 0.0 and x1 >= 340.0 and y0 <= 50.0 and y1 >= 57.0  # both dimension lines (y 50 and 57) are inside the box
    assert "260" in stack[0].text and "340" in stack[0].text


def test_two_lines_too_close_are_a_stack_gap_finding() -> None:
    found = look.layout_findings(context(dim_shapes=[above("a", 0.0, 100.0, 10.0), above("b", 0.0, 200.0, 14.0)]))
    assert [f.kind for f in found if f.kind.startswith("stack")] == ["stack_gap"]


def test_an_extension_line_across_another_dimension_line_is_a_crossing_at_the_point() -> None:
    found = look.layout_findings(context(dim_shapes=[above("lA", 50.0, 200.0, 17.0), above("lB", 0.0, 100.0, 10.0)]))
    crossing = [f for f in found if f.kind == "crossing"]
    assert crossing and all(f.box[0] < f.box[2] and f.box[1] < f.box[3] for f in crossing)


def test_the_collisions_the_items_measured_become_findings_around_where_they_are() -> None:
    texts = collide.Collision("dB", "d3", "text_text", (100.0, 60.0), "'12' over '34'")
    leader = collide.Collision("h1", "views", "text_line", (50.0, 30.0), "'M8' over the outline of views")
    off = collide.Collision("n1", "sheet", "outside_frame", (400.0, 20.0), "outside the drawing area")
    found = look.layout_findings(context(collisions=[texts, leader, off]))
    assert [f.kind for f in found] == ["overlap", "outside", "crossing"]  # the worst first
    assert found[0].items == ("dB", "d3") and found[2].items == ("h1",)  # 'views' and 'sheet' are not items
    for f, at in zip(found, [(100.0, 60.0), (400.0, 20.0), (50.0, 30.0)], strict=True):
        assert f.box[0] < at[0] < f.box[2] and f.box[1] < at[1] < f.box[3]


def test_the_list_goes_worst_first_so_a_cut_list_keeps_the_overlaps_and_the_stack() -> None:
    texts = collide.Collision("a", "b", "text_text", (100.0, 60.0), "1 over 2")
    lines = collide.Collision("c", "views", "text_line", (50.0, 30.0), "M8 over the outline of views")
    found = look.layout_findings(
        context(collisions=[lines, texts], dim_shapes=[above("l340", 0.0, 340.0, 10.0), above("l260", 0.0, 260.0, 17.0)])
    )
    kinds = [f.kind for f in found]
    assert kinds.index("overlap") < kinds.index("stack_order") < kinds.index("crossing")


def test_two_leaders_that_cross_are_a_crossing_naming_both_callouts() -> None:
    a = {"item": "h1", "tip": (10.0, 10.0), "elbow": (30.0, 30.0), "end": (40.0, 30.0)}
    b = {"item": "h2", "tip": (10.0, 30.0), "elbow": (30.0, 10.0), "end": (40.0, 10.0)}
    found = look.layout_findings(context(leaders=[a, b]))
    assert [(f.kind, f.items) for f in found] == [("crossing", ("h1", "h2"))]


# --- the crops --------------------------------------------------------------------------------------------------------------

PAGE = (420.0, 297.0)
TOP = ("top", (40.0, 150.0, 190.0, 250.0))  # 150 x 100 mm
FRONT = ("front", (40.0, 40.0, 190.0, 100.0))


def around(view: tuple[str, look.Rect], n: int, outside: float = 12.0) -> list[look.Rect]:
    """``n`` annotation boxes standing just above the view."""
    x0, _, x1, y1 = view[1]
    return [(x0 + 10.0 * k, y1 + outside, x0 + 10.0 * k + 8.0, y1 + outside + 4.0) for k in range(n)]


def test_a_sheet_with_few_annotations_is_read_whole_no_crops() -> None:
    chosen, left = look.select_crops([TOP, FRONT], around(TOP, 3), [], PAGE)
    assert chosen == [] and left == []


def test_a_dense_sheet_gets_a_crop_of_the_annotated_area_of_each_busy_view_at_about_ten_px_per_mm() -> None:
    notes = around(TOP, 5) + around(FRONT, 3)
    chosen, left = look.select_crops([TOP, FRONT], notes, [], PAGE)
    assert [c.view for c in chosen] == ["top", "front"] and left == []
    for crop in chosen:
        x0, y0, x1, y1 = crop.region
        assert max(x1 - x0, y1 - y0) * crop.px_per_mm <= look.MAX_CROP_PX + 1  # capped in size
        assert crop.px_per_mm >= look.MIN_PX_PER_MM  # and readable: a 1 mm overlap is at least 8 px
        assert crop.label.startswith(crop.view) and f"{crop.px_per_mm:.0f} px/mm" in crop.label  # labelled with view and region
    top = chosen[0].region
    assert top[0] <= TOP[1][0] and top[2] >= TOP[1][2] and top[3] >= TOP[1][3] + 12.0 + 4.0  # the view and what stands over it
    assert all(len(look._tiles(c.region)) == 1 for c in chosen)  # one crop per view up to 166 mm wide


def test_a_view_with_one_annotation_has_no_crop_of_its_own_unless_a_finding_is_in_it() -> None:
    notes = around(TOP, 6) + around(FRONT, 1)
    chosen, _ = look.select_crops([TOP, FRONT], notes, [], PAGE)
    assert [c.view for c in chosen] == ["top"]
    finding = look.LayoutFinding("overlap", "x on y", (100.0, 60.0, 112.0, 70.0), "front")
    chosen, _ = look.select_crops([TOP, FRONT], notes, [finding], PAGE)
    assert {c.view for c in chosen} == {"top", "front"}
    assert next(c for c in chosen if c.view == "front").findings == (0,)


def test_the_planted_overlap_is_inside_a_crop_at_a_scale_that_shows_it() -> None:
    planted = look.LayoutFinding("overlap", "'12' over '34'", (88.0, 104.0, 98.0, 110.0), "front")  # about 1 mm of overlap
    chosen, _ = look.select_crops([TOP, FRONT], around(TOP, 5) + around(FRONT, 3), [planted], PAGE)
    held = [c for c in chosen if 0 in c.findings]
    assert held, "the finding is in no crop"
    x0, y0, x1, y1 = held[0].region
    assert x0 <= 88.0 and x1 >= 98.0 and y0 <= 104.0 and y1 >= 110.0
    assert held[0].px_per_mm >= look.MIN_PX_PER_MM  # 1 mm = 8..10 px, against 3.3 px in the whole sheet


def test_a_finding_no_view_crop_holds_gets_a_crop_of_its_own_around_it() -> None:
    in_notes = look.LayoutFinding("overlap", "a on b", (300.0, 200.0, 318.0, 212.0))  # right of every view, nothing near
    chosen, _ = look.select_crops([TOP, FRONT], [], [in_notes], PAGE)
    assert len(chosen) == 1 and chosen[0].findings == (0,) and chosen[0].view == "sheet"
    x0, y0, x1, y1 = chosen[0].region
    assert x0 < 300.0 and x1 > 318.0 and y0 < 200.0 and y1 > 212.0
    assert chosen[0].px_per_mm == look.TARGET_PX_PER_MM  # a small box is drawn at the target


def test_an_area_too_wide_for_the_target_scale_is_cut_into_tiles_not_shrunk() -> None:
    shaft = ("front", (20.0, 100.0, 360.0, 140.0))  # 340 mm: one crop would be 3.2 px/mm
    spread = [(20.0 + 45.0 * k, 152.0, 28.0 + 45.0 * k, 156.0) for k in range(8)]  # dimensions all along it
    chosen, _ = look.select_crops([shaft], spread, [], PAGE)
    assert len(chosen) >= 3
    assert all(c.px_per_mm >= look.MIN_PX_PER_MM for c in chosen)
    assert min(c.region[0] for c in chosen) <= 20.0 and max(c.region[2] for c in chosen) >= 20.0 + 45.0 * 7  # the annotated run is covered
    assert all(max(c.region[2] - c.region[0], c.region[3] - c.region[1]) * c.px_per_mm <= look.MAX_CROP_PX + 1 for c in chosen)


def test_the_number_of_crops_is_capped_and_what_is_left_out_is_said_with_how_to_ask() -> None:
    views = [(f"v{n}", (10.0, 10.0 + 40.0 * n, 100.0, 40.0 + 40.0 * n)) for n in range(6)]
    notes = [box for v in views for box in around(v, 3, outside=2.0)]
    found = [look.LayoutFinding("overlap", "a on b", (50.0, 20.0 + 40.0 * 5, 60.0, 27.0 + 40.0 * 5), "v5")]
    chosen, left = look.select_crops(views, notes, found, (200.0, 400.0))
    assert len(chosen) == look.MAX_CROPS and len(left) == 1
    assert chosen[0].view == "v5"  # the view with a finding goes first
    assert "region=[" in left[0]


def test_crops_cost_a_bounded_number_of_pixels() -> None:
    views = [(f"v{n}", (10.0, 10.0 + 40.0 * n, 400.0, 40.0 + 40.0 * n)) for n in range(5)]
    notes = [box for v in views for box in around(v, 6, outside=2.0)]
    chosen, _ = look.select_crops(views, notes, [], (420.0, 297.0 * 2))
    pixels = sum(c.width_px * round((c.region[3] - c.region[1]) * c.px_per_mm) for c in chosen)
    assert len(chosen) <= look.MAX_CROPS and pixels <= look.MAX_CROPS * look.MAX_CROP_PX * look.MAX_CROP_PX
