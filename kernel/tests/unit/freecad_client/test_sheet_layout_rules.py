# ruff: noqa: E501
"""Layout faults of a sheet the review must name, on synthetic data (no FreeCAD): a dimension on the far side of a part when
the near side is free (tie of crossings, decided by the run of the extension lines), an extension line across the dimension
line of another dimension, a datum symbol lying on a line of another item, the leader of a chamfer that does not end on the
chamfer's own line, and a text over a datum symbol."""

import sys
from pathlib import Path

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import sheet_collide as collide  # noqa: E402
import sheet_context as sc  # noqa: E402
import sheet_model as model  # noqa: E402
import sheet_review as review  # noqa: E402
import sheet_units as units  # noqa: E402
import sheet_viewchecks as checks  # noqa: E402
import sheet_viewrules as vr  # noqa: E402


def polygon(*points: tuple[float, float]) -> list[vr.Segment]:
    ring = [*points, points[0]]
    return [(a[0], a[1], b[0], b[1]) for a, b in zip(ring, ring[1:], strict=False)]


PLATE = polygon((0, 0), (100, 0), (100, 60), (0, 60))
PLATE_BOX = (0.0, 0.0, 100.0, 60.0)


def vertical_dim(
    owner: str, ys: tuple[float, float], feature_x: float, line_x: float, view: str = "top"
) -> vr.DimShape:
    """A vertical dimension (horizontal extension lines from ``feature_x`` to the line at ``line_x``)."""
    right = 1.0 if line_x >= feature_x else -1.0
    start, end = feature_x + right, line_x + right * 2.0
    return vr.DimShape(owner, view, tuple((start, y, end, y) for y in ys), line_x, abs(ys[1] - ys[0]), 6.0, True)


def sides(*dims: vr.DimShape) -> list[vr.SideFinding]:
    return vr.dimension_side(list(dims), {"top": PLATE}, {"top": PLATE_BOX})


# --- 1. the side of a dimension when both sides cross the part once -------------------------------------------------------


def test_holes_15_mm_from_the_left_edge_dimensioned_on_the_right_are_asked_to_go_left() -> None:
    d30 = vertical_dim("d30", (15.0, 45.0), 15.0, 117.0)  # from the left holes across the whole plate
    found = sides(d30)
    assert [(f.owner, f.now, f.better, f.reason) for f in found] == [("d30", "right", "left", "length")]
    assert found[0].crossings_now == found[0].crossings_better == 2  # a tie of crossings: the run decides
    assert found[0].length_better < found[0].length_now / 2


def test_the_same_dimension_on_its_nearest_side_is_quiet() -> None:
    assert sides(vertical_dim("d30", (15.0, 45.0), 15.0, -10.0)) == []


def test_holes_in_the_middle_of_the_plate_can_go_either_side() -> None:
    assert sides(vertical_dim("d", (15.0, 45.0), 50.0, 117.0)) == []
    assert sides(vertical_dim("d", (15.0, 45.0), 50.0, -17.0)) == []


def test_a_stacked_pair_on_the_near_side_and_the_overall_alone_are_quiet() -> None:
    near = vertical_dim("d15", (0.0, 15.0), 15.0, -10.0)
    farther = vertical_dim("d30", (15.0, 45.0), 15.0, -17.0)
    assert sides(near, farther) == []
    assert sides(vertical_dim("o60", (0.0, 60.0), 100.0, 110.0)) == []


def test_a_hole_a_few_millimetres_nearer_one_edge_is_not_worth_a_move() -> None:
    assert sides(vertical_dim("d", (15.0, 45.0), 45.0, 117.0)) == []  # 55 mm over the part against 45


def test_the_review_message_for_a_run_names_the_lengths_and_the_side() -> None:
    d30 = vertical_dim("d30", (15.0, 45.0), 15.0, 117.0)
    view = sc.ViewInfo("top", 100.0, 60.0, 1.0, (0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (50.0, 30.0, 4.0), PLATE_BOX)
    extras = {"view_ink": [model.Line(*s, model.CONTOUR) for s in PLATE], "dim_shapes": [d30]}
    found = [
        f
        for f in checks.check(review_data({"top": view}, extras, [{"id": "d30", "type": "dim"}]))
        if f.code == "dimension_side"
    ]
    assert len(found) == 1 and "side=left" in found[0].message and "recorren" in found[0].message


def review_data(
    views: dict[str, sc.ViewInfo], extras: dict[str, object], items: list[dict[str, object]]
) -> review.ReviewInput:
    ctx = sc.Context(
        sheet=model.Sheet(420.0, 297.0), views=views, holes=[], geometry=sc.Geometry(), area=(20.0, 40.0, 410.0, 287.0)
    )
    ctx.extras.update(extras)
    return review.ReviewInput(items, {}, ctx, ctx.sheet)


# --- 2. an extension line across the dimension line of another dimension ------------------------------------------------------


def crossing(*dims: vr.DimShape) -> list[tuple[str, str]]:
    return [(f.owner, f.other) for f in vr.dimension_crossings(list(dims))]


def test_extension_lines_across_the_overall_dimension_line_are_a_crossing() -> None:
    o60 = vertical_dim("o60", (0.0, 60.0), 100.0, 110.0)
    d30 = vertical_dim("d30", (15.0, 45.0), 15.0, 117.0)
    d15 = vertical_dim("d15", (0.0, 15.0), 15.0, 124.0)
    assert crossing(o60, d30, d15) == [("d30", "o60"), ("d15", "o60")]


def test_interleaved_stacked_dimensions_cross() -> None:
    inner = vertical_dim("a", (0.0, 15.0), 100.0, 110.0)
    outer = vertical_dim("b", (10.0, 25.0), 100.0, 117.0)  # its extension line at y=10 runs through the line of a
    assert crossing(inner, outer) == [("b", "a")]


def test_a_normal_stack_the_overall_alone_and_dimensions_on_the_near_side_do_not_cross() -> None:
    inner = vertical_dim("a", (0.0, 15.0), 100.0, 110.0)
    outer = vertical_dim("b", (0.0, 60.0), 100.0, 117.0)  # from one datum: it only touches the end of a
    assert crossing(inner, outer) == []
    assert crossing(vertical_dim("o60", (0.0, 60.0), 100.0, 110.0)) == []
    left = vertical_dim("d30", (15.0, 45.0), 15.0, -10.0)
    right = vertical_dim("o60", (0.0, 60.0), 100.0, 110.0)
    assert crossing(left, right) == []


def test_a_dimension_is_not_judged_against_its_own_lines_nor_across_views() -> None:
    d30 = vertical_dim("d30", (15.0, 45.0), 15.0, 117.0)
    assert crossing(d30) == []
    other_view = vertical_dim("o60", (0.0, 60.0), 100.0, 110.0, view="front")
    assert crossing(d30, other_view) == []


def test_the_review_reports_the_crossing_with_the_free_side_and_never_holds_an_export() -> None:
    from sheet_checks import NON_BLOCKING

    view = sc.ViewInfo(
        "top", 100.0, 60.0, 1.0, (0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (50.0, 30.0, 4.0), (60.0, 100.0, 160.0, 160.0)
    )
    o60 = vertical_dim("o60", (100.0, 160.0), 160.0, 170.0)
    d30 = vertical_dim("d30", (115.0, 145.0), 75.0, 177.0)
    data = review_data(
        {"top": view}, {"dim_shapes": [o60, d30]}, [{"id": "o60", "type": "overall"}, {"id": "d30", "type": "dim"}]
    )
    data.ctx.occupied.append(view.box)
    found = [f for f in checks.check(data) if f.code == "dimension_crossing"]
    assert len(found) == 1 and found[0].items == ("d30", "o60")
    assert "o60" in found[0].message and "side=left" in found[0].message
    assert "dimension_crossing" in NON_BLOCKING


# --- 3. a datum symbol on a line of another item ---------------------------------------------------------------------------------

TRIANGLE = ((15.0, 0.0), (13.5, -3.5), (16.5, -3.5))
FRAME = (12.0, -10.0, 18.0, -6.0)
STEM = (15.0, -3.5, 15.0, -6.0)


def datum() -> vr.DatumInk:
    return vr.DatumInk("B", TRIANGLE, (FRAME,), (STEM,))


def test_a_triangle_on_an_extension_line_is_found() -> None:
    found = vr.datum_on_line([datum()], {"d15": [(15.0, 8.0, 15.0, -2.0)]})
    assert [(f.owner, f.over, f.part) for f in found] == [("B", "d15", "triangle")]


def test_a_line_through_the_frame_or_along_the_stem_is_found() -> None:
    assert vr.datum_on_line([datum()], {"x": [(10.0, -8.0, 30.0, -8.0)]})[0].part == "frame"
    assert vr.datum_on_line([datum()], {"x": [(15.0, -3.0, 15.0, -5.0)]})  # along the stem


def test_a_datum_clear_of_other_lines_resting_on_the_outline_is_quiet() -> None:
    others = {
        "d15": [(30.0, 8.0, 30.0, -20.0)],
        "d30": [(0.0, 5.0, 40.0, 5.0)],
    }  # the outline of the part is not an item
    assert vr.datum_on_line([datum()], others) == []
    assert vr.datum_on_line([datum()], {"B": [(15.0, 8.0, 15.0, -2.0)]}) == []  # its own ink is never "another item"


def test_the_review_names_the_datum_and_the_item_it_lies_on() -> None:
    triangle = model.Solid(TRIANGLE, model.DIMENSIONS)
    box = model.Polyline(((12.0, -10.0), (18.0, -10.0), (18.0, -6.0), (12.0, -6.0)), model.DIMENSIONS, closed=True)
    datum_unit = units.Unit(
        "B",
        "B datum",
        units.KIND_ITEM,
        (triangle, box, model.Line(*STEM, model.DIMENSIONS), model.Text(15.0, -8.5, "B")),
        "datum",
    )
    ext = units.Unit("d15", "d15 dim", units.KIND_ITEM, (model.Line(15.0, 8.0, 15.0, -2.0, model.DIMENSIONS),), "dim")
    items = [{"id": "B", "type": "datum", "face": ["face:1"]}, {"id": "d15", "type": "dim"}]
    found = [f for f in checks.check(review_data({}, {"units": [datum_unit, ext]}, items)) if f.code == "datum_on_line"]
    assert len(found) == 1 and found[0].items == ("B", "d15") and "triángulo" in found[0].message
    clear = units.Unit("d15", "d15 dim", units.KIND_ITEM, (model.Line(40.0, 8.0, 40.0, -2.0, model.DIMENSIONS),), "dim")
    assert [
        f for f in checks.check(review_data({}, {"units": [datum_unit, clear]}, items)) if f.code == "datum_on_line"
    ] == []
    on_callout = [{"id": "B", "type": "datum", "on": "h1"}, {"id": "d15", "type": "dim"}]
    assert [
        f for f in checks.check(review_data({}, {"units": [datum_unit, ext]}, on_callout)) if f.code == "datum_on_line"
    ] == []


# --- 3b. a datum no tolerance frame uses ----------------------------------------------------------------------------------------


def unused(items: list[dict[str, object]]) -> list[object]:
    return [f for f in checks.check(review_data({}, {}, items)) if f.code == "datum_unused"]


def test_a_datum_no_frame_cites_is_flagged_and_a_sheet_with_no_gdt_flags_all_its_datums() -> None:
    found = unused([{"id": "dA", "type": "datum", "letter": "A", "face": ["face:1"]}])
    assert len(found) == 1 and found[0].items == ("dA",) and found[0].severity == "warning"
    assert "datum A (dA)" in found[0].message and "gdt" in found[0].message
    from sheet_checks import NON_BLOCKING

    assert "datum_unused" in NON_BLOCKING
    two = unused([{"id": "dA", "type": "datum", "letter": "A"}, {"id": "dB", "type": "datum", "letter": "B"}])
    assert [f.items for f in two] == [("dA",), ("dB",)]


def test_a_datum_a_frame_cites_is_quiet() -> None:
    items = [
        {"id": "dA", "type": "datum", "letter": "A"},
        {"id": "g1", "type": "gdt", "char": "flatness", "value": 0.05, "datums": ["A"]},
    ]
    assert unused(items) == []
    assert unused([*items, {"id": "g2", "type": "gdt", "datums": ["A", "B"]}]) == []  # B has no item: nothing to flag


def test_a_datum_cited_only_as_the_second_or_third_of_a_frame_is_quiet() -> None:
    items = [
        {"id": "dB", "type": "datum", "letter": "B"},
        {"id": "dC", "type": "datum", "letter": "C"},
        {"id": "g1", "type": "gdt", "datums": ["A", "B", "C"]},
    ]
    assert unused(items) == []


def test_of_two_datums_only_the_one_no_frame_cites_is_flagged_and_letters_compare_in_capitals() -> None:
    items = [
        {"id": "dA", "type": "datum", "letter": "A"},
        {"id": "dB", "type": "datum", "letter": "b"},
        {"id": "g1", "type": "gdt", "datums": ["A"]},
        {"id": "g2", "type": "gdt", "datums": ["A"]},  # several frames may cite one datum
    ]
    assert [f.items for f in unused(items)] == [("dB",)]
    assert unused([*items, {"id": "g3", "type": "gdt", "datums": ["B"]}]) == []


# --- 4. the leader of a chamfer callout -----------------------------------------------------------------------------------------

CHAMFERED = polygon((4, 0), (100, 0), (100, 60), (0, 60), (0, 4))  # a 4 x 45 degree chamfer at the corner (0, 0)
MID = (2.0, 2.0)
OUT = (-(2**-0.5), -(2**-0.5))  # the outward normal of the chamfer line


def chamfer(tip: tuple[float, float], elbow: tuple[float, float]) -> list[str]:
    return vr.chamfer_leader_problems(tip, elbow, MID, OUT, CHAMFERED)


def test_a_leader_to_the_middle_of_the_chamfer_from_outside_along_its_normal_is_right() -> None:
    assert chamfer((2.0, 2.0), (-8.0, -8.0)) == []
    assert chamfer((2.0, 2.0), (-12.0, -6.0)) == []  # within 30 degrees of the normal


def test_a_leader_to_the_side_face_edge_near_the_chamfer_is_not_on_the_chamfer() -> None:
    assert "off_line" in chamfer((0.0, 10.0), (-10.0, 10.0))
    assert "off_line" in chamfer((0.0, 2.5), (-10.0, 2.5))  # on the part's outline but not on the chamfer's line
    assert chamfer((0.0, 2.5), (-8.0, -5.5)) == ["off_line"]  # even coming out right, it points at the wrong line


def test_a_leader_from_the_part_side_or_far_from_perpendicular_is_reported() -> None:
    assert chamfer((2.0, 2.0), (12.0, 12.0)) == ["from_inside"]
    assert chamfer((2.0, 2.0), (-20.0, -2.0)) == ["oblique"]


def test_the_review_judges_the_leader_the_item_placed() -> None:
    view = sc.ViewInfo("front", 100.0, 60.0, 1.0, (0.0, -1.0, 0.0), (1.0, 0.0, 0.0), (50.0, 30.0, 4.0), PLATE_BOX)

    def run(tip: tuple[float, float], elbow: tuple[float, float]) -> list[review.Finding]:
        extras = {
            "view_ink": [model.Line(*s, model.CONTOUR) for s in CHAMFERED],
            "chamfer_marks": {"c1": ("front", MID, OUT)},
            "anchors": {"c1": [{"tip": tip, "elbow": elbow, "dir": -1.0, "rect": (0.0, 0.0, 10.0, 4.0)}]},
        }
        found = checks.check(review_data({"front": view}, extras, [{"id": "c1", "type": "dim", "kind": "chamfer"}]))
        return [f for f in found if f.code == "leader_not_on_chamfer"]

    assert run((2.0, 2.0), (-8.0, -8.0)) == []
    bad = run((0.0, 10.0), (-10.0, 10.0))
    assert len(bad) == 1 and bad[0].items == ("c1",) and "side=" in bad[0].message
    from sheet_checks import NON_BLOCKING

    assert "leader_not_on_chamfer" in NON_BLOCKING


# --- 5. a text over a datum symbol --------------------------------------------------------------------------------------------------


def datum_ink_unit() -> collide.Ink:
    box = model.Polyline(((12.0, -10.0), (18.0, -10.0), (18.0, -6.0), (12.0, -6.0)), model.DIMENSIONS, closed=True)
    entities = [
        model.Solid(TRIANGLE, model.DIMENSIONS),
        box,
        model.Line(*STEM, model.DIMENSIONS),
        model.Text(15.0, -9.0, "A"),
    ]
    return collide.ink_of("A", entities)


def test_a_rotated_text_box_over_a_frame_polyline_collides_in_both_orders() -> None:
    rotated = model.Text(14.0, -9.0, "30", 3.5, model.DIMENSIONS, "center", 90.0)  # the text of a dimension on the left
    text_box = collide.text_box(rotated)
    assert text_box[2] - text_box[0] < text_box[3] - text_box[1]  # turned: taller than wide
    dimension = collide.ink_of("d30", [rotated])
    frame_first = collide.collisions([datum_ink_unit(), dimension])
    text_first = collide.collisions([dimension, datum_ink_unit()])
    assert {c.kind for c in frame_first} & {"text_line", "line_text"}
    assert {c.kind for c in text_first} & {"text_line", "line_text"}


def test_a_rotated_text_wholly_inside_a_datum_frame_collides_in_both_orders() -> None:
    # the text of a dimension on the left of a view, turned 90 degrees, sits inside the lettered box: no edge of the frame
    # crosses its box, which is why a text-against-lines test alone never saw it
    frame = model.Polyline(((84.0, 96.5), (91.0, 96.5), (91.0, 103.5), (84.0, 103.5)), model.DIMENSIONS, closed=True)
    datum = collide.ink_of("A", [model.Solid(((100.0, 100.0), (96.0, 97.7), (96.0, 102.3)), model.DIMENSIONS), frame])
    inside = collide.ink_of("d30", [model.Text(88.8, 100.0, "40", 3.5, model.DIMENSIONS, "center", 90.0)])
    box = collide.text_box(model.Text(88.8, 100.0, "40", 3.5, model.DIMENSIONS, "center", 90.0))
    assert 84.0 < box[0] and box[2] < 91.0 and 96.5 < box[1] and box[3] < 103.5  # really wholly inside
    assert "text_frame" in {c.kind for c in collide.collisions([datum, inside])}
    assert "text_frame" in {c.kind for c in collide.collisions([inside, datum])}
    assert datum.frames == ((84.0, 96.5, 91.0, 103.5),)


def test_a_text_beside_a_frame_or_inside_its_own_is_quiet() -> None:
    frame = model.Polyline(((84.0, 96.5), (91.0, 96.5), (91.0, 103.5), (84.0, 103.5)), model.DIMENSIONS, closed=True)
    own = collide.ink_of("A", [frame, model.Text(87.5, 98.7, "A", 3.5, model.DIMENSIONS, "center")])
    beside = collide.ink_of("d30", [model.Text(70.0, 100.0, "40", 3.5, model.DIMENSIONS, "center", 90.0)])
    assert collide.collisions([own, beside]) == []


def test_a_rotated_text_clear_of_the_datum_is_quiet() -> None:
    far = model.Text(40.0, -9.0, "30", 3.5, model.DIMENSIONS, "center", 90.0)
    assert collide.collisions([datum_ink_unit(), collide.ink_of("d30", [far])]) == []
