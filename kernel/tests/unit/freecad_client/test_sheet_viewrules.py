# ruff: noqa: E501
"""Three drawing rules on synthetic sheet data (no FreeCAD): the side a dimension stands on, a view that only carries a
cutting line, and the arrows of a short dimension."""

import sys
from pathlib import Path

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import sheet_viewrules as vr  # noqa: E402


def polygon(*points: tuple[float, float]) -> list[vr.Segment]:
    ring = [*points, points[0]]
    return [(a[0], a[1], b[0], b[1]) for a, b in zip(ring, ring[1:], strict=False)]


# a turned part seen from the side, axis vertical: Ø40 body from y=20 to 80 and a Ø20 spigot below it
SHAFT = polygon((0, 20), (10, 20), (10, 0), (30, 0), (30, 20), (40, 20), (40, 80), (0, 80))
SHAFT_BOX = (0.0, 0.0, 40.0, 80.0)
# a plate seen from above
PLATE = polygon((0, 0), (100, 0), (100, 60), (0, 60))
PLATE_BOX = (0.0, 0.0, 100.0, 60.0)


def horizontal_dim(owner: str, view: str, xs: tuple[float, float], feature_y: float, line_y: float) -> vr.DimShape:
    """A horizontal dimension: extension lines from ``feature_y`` (a millimetre off the feature) to the dimension line."""
    up = 1.0 if line_y >= feature_y else -1.0
    start, end = feature_y + up, line_y + up * 2.0
    return vr.DimShape(owner, view, tuple((x, start, x, end) for x in xs), line_y, abs(xs[1] - xs[0]), 6.0, True)


def vertical_dim(owner: str, view: str, ys: tuple[float, float], feature_x: float, line_x: float) -> vr.DimShape:
    right = 1.0 if line_x >= feature_x else -1.0
    start, end = feature_x + right, line_x + right * 2.0
    return vr.DimShape(owner, view, tuple((start, y, end, y) for y in ys), line_x, abs(ys[1] - ys[0]), 6.0, True)


# --- the side of a dimension ---------------------------------------------------------------------------------------------


def test_a_spigot_at_the_bottom_dimensioned_above_is_asked_to_go_below() -> None:
    dim = horizontal_dim("d1", "front", (10.0, 30.0), 0.0, 95.0)
    found = vr.dimension_side([dim], {"front": SHAFT}, {"front": SHAFT_BOX})
    assert len(found) == 1
    assert (found[0].owner, found[0].now, found[0].better) == ("d1", "above", "below")
    assert found[0].crossings_better == 0
    assert found[0].crossings_now >= 2


def test_the_same_spigot_dimensioned_below_is_quiet() -> None:
    dim = horizontal_dim("d1", "front", (10.0, 30.0), 0.0, -12.0)
    assert vr.dimension_side([dim], {"front": SHAFT}, {"front": SHAFT_BOX}) == []


def test_a_length_on_the_left_whose_feature_is_on_the_right_is_asked_to_go_right() -> None:
    # the step at y=20..80 of the right side: its lengths are measured from the right edge x=40
    dim = vertical_dim("d2", "front", (20.0, 80.0), 40.0, -10.0)
    found = vr.dimension_side([dim], {"front": SHAFT}, {"front": SHAFT_BOX})
    assert len(found) == 1
    assert (found[0].now, found[0].better) == ("left", "right")


def test_a_dimension_that_can_only_go_across_is_not_reported() -> None:
    # two points in the middle of a plate: above crosses the top edge, below the bottom one, the same
    dim = horizontal_dim("d3", "top", (30.0, 50.0), 30.0, 75.0)
    assert vr.dimension_side([dim], {"top": PLATE}, {"top": PLATE_BOX}) == []
    low = horizontal_dim("d3", "top", (30.0, 50.0), 30.0, -15.0)
    assert vr.dimension_side([low], {"top": PLATE}, {"top": PLATE_BOX}) == []


def test_a_dimension_from_the_edge_it_stands_on_crosses_nothing_and_is_quiet() -> None:
    dim = horizontal_dim("d4", "top", (0.0, 100.0), 60.0, 75.0)
    assert vr.dimension_side([dim], {"top": PLATE}, {"top": PLATE_BOX}) == []


def test_a_view_with_no_outline_or_oblique_extension_lines_is_not_judged() -> None:
    dim = horizontal_dim("d1", "front", (10.0, 30.0), 0.0, 95.0)
    assert vr.dimension_side([dim], {}, {"front": SHAFT_BOX}) == []
    oblique = vr.DimShape("d5", "front", ((0.0, 0.0, 10.0, 10.0),), 95.0, 10.0, 6.0, True)
    assert vr.dimension_side([oblique], {"front": SHAFT}, {"front": SHAFT_BOX}) == []


# --- arrows outside on a short dimension ------------------------------------------------------------------------------------


def test_a_short_dimension_with_the_arrows_inside_is_reported() -> None:
    short = vr.DimShape("s1", "front", span=8.0, text_width=6.0, arrows_inside=True)
    assert vr.short_dimensions([short]) == [short]


def test_a_dimension_where_text_and_arrows_fit_is_quiet() -> None:
    wide = vr.DimShape("s2", "front", span=20.0, text_width=6.0, arrows_inside=True)
    assert vr.short_dimensions([wide]) == []


def test_arrows_already_outside_fixed_by_hand_or_unknown_are_quiet() -> None:
    outside = vr.DimShape("s3", "front", span=8.0, text_width=6.0, arrows_inside=False)
    pinned = vr.DimShape("s4", "front", span=8.0, text_width=6.0, arrows_inside=True, pinned=True)
    unknown = vr.DimShape("s5", "front", span=8.0, text_width=6.0, arrows_inside=None)
    assert vr.short_dimensions([outside, pinned, unknown]) == []


# --- a view that only carries a cutting line --------------------------------------------------------------------------------

DIAMETERS = frozenset({("dia", 20.0), ("dia", 40.0)})
LENGTHS = frozenset({("len", "z", 0.0, 80.0)})


def turned_part(front: vr.ViewUse) -> list[vr.ViewUse]:
    return [
        front,
        vr.ViewUse("side"),
        vr.ViewUse("A-A", dims=DIAMETERS | LENGTHS),
        vr.ViewUse("iso", pictorial=True),
    ]


def test_the_view_that_only_holds_the_cutting_line_goes_when_the_circular_view_can_carry_it() -> None:
    front = vr.ViewUse("front", cuts=(vr.Cut("A-A", ("side",)),))
    found = vr.redundant_views(turned_part(front))
    assert [(r.name, r.reason) for r in found] == [("front", "cut_only")]
    assert found[0].cuts[0].alternatives == ("side",)


def test_a_view_whose_dimensions_the_section_repeats_goes() -> None:
    front = vr.ViewUse("front", dims=DIAMETERS, cuts=(vr.Cut("A-A", ("side",)),))
    found = vr.redundant_views(turned_part(front))
    assert [(r.name, r.reason, r.repeated_in) for r in found] == [("front", "repeated", ("A-A",))]


def test_a_view_that_carries_a_dimension_nobody_else_has_stays() -> None:
    front = vr.ViewUse("front", dims=DIAMETERS | {("len", "z", 5.0, 20.0)}, cuts=(vr.Cut("A-A", ("side",)),))
    assert vr.redundant_views(turned_part(front)) == []


def test_a_view_with_a_callout_stays() -> None:
    front = vr.ViewUse("front", other=True, cuts=(vr.Cut("A-A", ("side",)),))
    assert vr.redundant_views(turned_part(front)) == []


def test_a_cutting_line_no_other_view_can_carry_keeps_its_view() -> None:
    front = vr.ViewUse("front", cuts=(vr.Cut("A-A", ()),))
    assert vr.redundant_views(turned_part(front)) == []


def test_two_views_that_repeat_each_other_do_not_both_go() -> None:
    views = [vr.ViewUse("front", dims=DIAMETERS), vr.ViewUse("top", dims=DIAMETERS)]
    found = vr.redundant_views(views)
    assert len(found) == 1


def test_a_view_that_is_not_read_is_never_judged() -> None:
    front = vr.ViewUse("front", cuts=(vr.Cut("A-A", ("side",)),), unread=True)
    assert vr.redundant_views(turned_part(front)) == []


# --- the FreeCAD sheet: the shape of a drawn dimension and the review findings -------------------------------------------------

import sheet_context as sc  # noqa: E402
import sheet_draw as draw  # noqa: E402
import sheet_model as model  # noqa: E402
import sheet_review as review  # noqa: E402
import sheet_viewchecks as checks  # noqa: E402
import sheet_viewshape as shape  # noqa: E402


def drawn(span: float, arrows: str = "auto", text: str = "12") -> vr.DimShape:
    entities, _ = draw.linear_dimension((0.0, 0.0), (span, 0.0), "h", 20.0, text, arrows=arrows)
    found = shape.dimension_shape("d1", "front", entities, arrows)
    assert found is not None
    return found


def test_auto_puts_the_arrows_outside_when_text_and_arrows_do_not_fit() -> None:
    assert drawn(8.0).arrows_inside is False
    assert drawn(30.0).arrows_inside is True


def test_the_arrow_side_can_be_asked_for() -> None:
    assert drawn(30.0, "outside").arrows_inside is False
    forced = drawn(8.0, "inside")
    assert forced.arrows_inside is True
    assert forced.pinned


def test_a_drawn_dimension_has_its_extension_lines_its_line_and_its_span() -> None:
    found = drawn(30.0)
    assert len(found.extension) == 2
    assert found.line_at == 20.0
    assert abs((found.span or 0.0) - 30.0) < 1e-6


def review_data(
    views: dict[str, sc.ViewInfo], extras: dict[str, object], items: list[dict[str, object]]
) -> review.ReviewInput:
    ctx = sc.Context(
        sheet=model.Sheet(420.0, 297.0), views=views, holes=[], geometry=sc.Geometry(), area=(20.0, 40.0, 410.0, 287.0)
    )
    ctx.extras.update(extras)
    return review.ReviewInput(items, {}, ctx, ctx.sheet)


def standard_view(name: str, direction: tuple[float, float, float], xdir: tuple[float, float, float]) -> sc.ViewInfo:
    return sc.ViewInfo(name, 100.0, 100.0, 1.0, direction, xdir, (0.0, 0.0, 0.0), (80.0, 80.0, 120.0, 120.0))


def test_the_review_says_which_view_to_drop_and_where_the_cutting_line_goes() -> None:
    views = {
        "front": standard_view("front", (0.0, -1.0, 0.0), (1.0, 0.0, 0.0)),
        "right": standard_view("right", (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
        "top": standard_view("top", (0.0, 0.0, -1.0), (1.0, 0.0, 0.0)),
    }
    section = sc.ViewInfo(
        "A-A", 200.0, 100.0, 1.0, (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 0.0), (180.0, 80.0, 220.0, 120.0)
    )
    extras = {
        "sections": {"A-A": section},
        "section_items": {"A-A": "s1"},
        "section_planes": {"A-A": ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0))},
        "view_use": {"front": {"s1"}, "A-A": {"d1"}},
        "dim_records": [("d1", "A-A", ("dia", 20.0, "face:1"))],
    }
    items = [{"id": "s1", "type": "section", "view": "front"}, {"id": "d1", "type": "dim"}]
    found = checks.check(review_data(views, extras, items))
    redundant = [f for f in found if f.code == "view_redundant"]
    assert [f.items for f in redundant] == [("front",)]
    assert (
        "vista top" in redundant[0].message and "vista right" not in redundant[0].message
    )  # only the view that sees the plane edge-on


def test_the_review_keeps_the_view_that_carries_a_hole_callout() -> None:
    views = {
        "front": standard_view("front", (0.0, -1.0, 0.0), (1.0, 0.0, 0.0)),
        "right": standard_view("right", (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
    }
    section = sc.ViewInfo(
        "A-A", 200.0, 100.0, 1.0, (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 0.0), (180.0, 80.0, 220.0, 120.0)
    )
    extras = {
        "sections": {"A-A": section},
        "section_items": {"A-A": "s1"},
        "section_planes": {"A-A": ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0))},
        "view_use": {"front": {"s1", "h1"}, "A-A": {"d1"}},
        "dim_records": [("d1", "A-A", ("dia", 20.0, "face:1"))],
    }
    items = [
        {"id": "s1", "type": "section", "view": "front"},
        {"id": "d1", "type": "dim"},
        {"id": "h1", "type": "hole_dim"},
    ]
    assert [f for f in checks.check(review_data(views, extras, items)) if f.code == "view_redundant"] == []


def test_the_review_reports_a_dimension_on_the_far_side_and_names_the_side() -> None:
    view = sc.ViewInfo("front", 20.0, 40.0, 1.0, (0.0, -1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 0.0), SHAFT_BOX)
    ink = [model.Line(*s, model.CONTOUR) for s in SHAFT]
    dim = horizontal_dim("d1", "front", (10.0, 30.0), 0.0, 95.0)
    extras = {"view_ink": ink, "dim_shapes": [dim]}
    found = checks.check(review_data({"front": view}, extras, [{"id": "d1", "type": "dim"}]))
    assert [f.code for f in found] == ["dimension_side"]
    assert "side=below" in found[0].message
    assert found[0].items == ("d1",)


def test_the_review_reports_a_short_dimension_with_its_arrows_inside() -> None:
    short = vr.DimShape("d9", "front", span=8.0, text_width=6.0, arrows_inside=True)
    found = checks.check(review_data({}, {"dim_shapes": [short]}, [{"id": "d9", "type": "dim"}]))
    assert [f.code for f in found] == ["arrows_inside_short"]
    assert "arrows=outside" in found[0].message


# --- two parallel vertical dimensions whose lines and texts overlap (seen on a real sheet from a commercial CAD) ----------------------------

import sheet_collide as collide  # noqa: E402


def vertical_ink(owner: str, span: tuple[float, float], line_x: float, text: str) -> tuple[collide.Ink, vr.DimShape]:
    entities, _ = draw.linear_dimension((50.0, span[0]), (50.0, span[1]), "v", line_x, text)
    found = shape.dimension_shape(owner, "front", entities)
    assert found is not None
    return collide.ink_of(owner, entities), found


def kinds(found: list[collide.Collision]) -> set[str]:
    return {c.kind for c in found}


def test_two_vertical_dimensions_on_one_line_collide() -> None:
    a, _ = vertical_ink("d30", (0.0, 30.0), 60.0, "30")
    b, _ = vertical_ink("d45", (0.0, 45.0), 60.0, "45")
    assert "dimension_lines" in kinds(collide.collisions([a, b]))


def test_two_vertical_dimensions_whose_texts_overlap_collide() -> None:
    a, _ = vertical_ink("d30", (0.0, 30.0), 60.0, "30")
    b, _ = vertical_ink("d32", (0.0, 32.0), 60.0, "32")
    assert "text_text" in kinds(collide.collisions([a, b]))


def test_a_text_over_another_dimensions_line_or_arrow_collides() -> None:
    a, _ = vertical_ink("d30", (0.0, 30.0), 60.0, "30")
    b, _ = vertical_ink("d45", (0.0, 45.0), 63.0, "45")  # 45 stands 3 mm from 30: its text sits on 30's line
    assert kinds(collide.collisions([a, b])) & {"text_line", "line_text"}


def test_dimensions_three_slots_apart_do_not_collide() -> None:
    a, _ = vertical_ink("d30", (0.0, 30.0), 60.0, "30")
    b, _ = vertical_ink("d45", (0.0, 45.0), 75.0, "45")
    assert collide.collisions([a, b]) == []


def test_a_dimension_line_is_found_where_the_tips_are_not_at_its_ends() -> None:
    line = (60.0, -6.0, 60.0, 36.0)  # arrows outside: the line reaches past the two tips
    extension = (50.0, 0.0, 62.0, 0.0)
    tips = [(60.0, 0.3), (60.0, 29.7)]  # a CAD that reads tips and lines apart is a few tenths off
    assert collide.main_lines([line, extension], tips, through=True) == [line]
    assert (
        collide.main_lines([line, extension], tips) == []
    )  # without ``through`` only a line that ends at a tip counts


def test_a_vertical_dimension_stack_is_caught_even_when_the_tips_do_not_meet_the_line_ends() -> None:
    def read(owner: str, top: float) -> collide.Ink:
        entities, _ = draw.linear_dimension((50.0, 0.0), (50.0, top), "v", 60.0, str(int(top)))
        tips = [e.points[0] for e in entities if isinstance(e, model.Solid)]
        moved = [(x + 0.2, y) for x, y in tips]  # off the line by 0.2 mm
        lines = [(e.x1, e.y1, e.x2, e.y2) for e in entities if isinstance(e, model.Line)]
        base = collide.ink_of(owner, entities)
        return collide.Ink(owner, base.texts, base.lines, tuple(collide.main_lines(lines, moved, through=True)))

    assert "dimension_lines" in kinds(collide.collisions([read("d30", 30.0), read("d45", 45.0)]))


# --- where there is free room ---------------------------------------------------------------------------------------------------

AREA = (0.0, 0.0, 200.0, 150.0)
BOX = (50.0, 40.0, 100.0, 90.0)


def right_dim() -> vr.DimShape:
    return vertical_dim("d45", "front", (40.0, 85.0), 100.0, 110.0)


def test_the_free_side_is_the_other_side_of_the_view() -> None:
    assert vr.free_side(right_dim(), BOX, [BOX], AREA) == "left"


def test_there_is_no_free_side_when_something_stands_there_or_the_page_ends() -> None:
    assert vr.free_side(right_dim(), BOX, [BOX, (30.0, 50.0, 45.0, 70.0)], AREA) is None
    assert vr.free_side(right_dim(), BOX, [BOX], (45.0, 0.0, 200.0, 150.0)) is None


def test_a_dimension_inside_its_view_or_not_axis_aligned_has_no_free_side() -> None:
    inside = vertical_dim("d1", "front", (40.0, 85.0), 100.0, 75.0)
    assert vr.free_side(inside, BOX, [], AREA) is None
    oblique = vr.DimShape("d2", "front", ((0.0, 0.0, 10.0, 10.0),), 120.0)
    assert vr.free_side(oblique, BOX, [], AREA) is None


def test_the_review_names_the_free_side_and_says_a_collision_is_a_decision() -> None:
    view = sc.ViewInfo("front", 75.0, 65.0, 1.0, (0.0, -1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 0.0), BOX)
    data = review_data(
        {"front": view},
        {
            "dim_shapes": [right_dim()],
            "collisions": [
                collide.Collision("d45", "d30", "dimension_lines", (0.0, 0.0), "two dimension lines lie on one another")
            ],
        },
        [{"id": "d45", "type": "dim"}, {"id": "d30", "type": "dim"}],
    )
    data.ctx.area = AREA
    data.ctx.occupied.append(BOX)
    found = review._collisions(data)  # noqa: SLF001
    assert len(found) == 1
    assert "side=left" in found[0].message and "izquierdo" in found[0].message
    assert "no hay sitio libre" in found[0].message
    data.ctx.occupied.append((20.0, 40.0, 49.0, 90.0))
    assert "side=left" not in review._collisions(data)[0].message  # noqa: SLF001
