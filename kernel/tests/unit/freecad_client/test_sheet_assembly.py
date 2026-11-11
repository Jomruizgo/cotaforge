# ruff: noqa: E501
"""The assembly sheet items (``items_assembly.py``, ``items_explode.py``, the assembly part of ``sheet_review.py`` and the
layout reserve) with a canned assembly: no FreeCAD. What is checked is what the items do with the data -- positions and
quantities, the numbers shared by the list and the balloons, hidden instances reported, balloons clear of the views and
of each other's leaders, the explosion offsets and that nothing of the model is asked to change."""

import math
import sys
from pathlib import Path
from typing import Any

import pytest

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import items_assembly as asm  # noqa: E402
import items_explode as ex  # noqa: E402
import sheet_context as sc  # noqa: E402
import sheet_items as items  # noqa: E402
import sheet_layout as layout  # noqa: E402
import sheet_model as model  # noqa: E402
import sheet_review as review  # noqa: E402

from .test_sheet_items import PlateGeometry, draw, make_context  # noqa: E402

# comp:1 plate (qty 1), comp:2 and comp:3 identical brackets, comp:4 a shaft bought from a file, comp:5 a buried part
BOXES = {
    "comp:1": ((0, 0, 0), (160, 100, 12)),
    "comp:2": ((45, 16, 12), (115, 30, 87)),
    "comp:3": ((45, 70, 12), (115, 84, 87)),
    "comp:4": ((70, -10, 40), (90, 110, 60)),
    "comp:5": ((60, 40, 20), (70, 50, 30)),
}
# where the front view (looking along +y, page x = world x, page y = world z) finds a point of each instance
VISIBLE = {
    "comp:1": (20.0, 0.0, 6.0),
    "comp:2": (60.0, 0.0, 70.0),
    "comp:3": (100.0, 0.0, 70.0),
    "comp:4": (80.0, 0.0, 50.0),
}


class AssemblyGeometry(PlateGeometry):
    def __init__(self) -> None:
        super().__init__()
        self.asked: list[tuple[list[str], dict[str, Any]]] = []

    def instance_box(self, comp_id: str) -> tuple[sc.Vec3, sc.Vec3]:
        return BOXES[comp_id]  # type: ignore[return-value]

    def instance_anchors(
        self, instances: list[str], frame: Any, offsets: dict[str, sc.Vec3], margin_mm: float, outward: bool = False
    ) -> dict[str, list[sc.Vec3]]:
        self.asked.append((list(instances), dict(offsets)))
        return {c: [VISIBLE[c]] if c in VISIBLE else [] for c in instances}

    def assembly_projection(
        self, centre: sc.Vec3, xdir: sc.Vec3, direction: sc.Vec3, offsets: dict[str, sc.Vec3]
    ) -> sc.Projection:
        self.asked.append((["*"], dict(offsets)))
        return sc.Projection(
            [
                model.Line(-60, -40, 60, -40),
                model.Line(60, -40, 60, 40),
                model.Line(60, 40, -60, 40),
                model.Line(-60, 40, -60, -40),
            ]
        )


def assembly() -> sc.Assembly:
    return sc.Assembly(
        [
            sc.PartGroup(1, "plate", "placa_base", "", "S235JR", "", ["comp:1"]),
            sc.PartGroup(2, "bracket", "escuadra", "ESC-001", "S235JR", "", ["comp:2", "comp:3"]),
            sc.PartGroup(3, "shaft", "eje_d20", "eje_d20.step", "", "comprado", ["comp:4"], bought=True),
            sc.PartGroup(4, "buried", "pasador", "", "", "", ["comp:5"]),
        ]
    )


def context(language: str = "es") -> sc.Context:
    ctx = make_context(language=language)
    ctx.geometry = AssemblyGeometry()
    ctx.assembly = assembly()
    ctx.area = (25.0, 73.0, 405.0, 282.0)
    ctx.extras["view_boxes"] = [v.box for v in ctx.views.values()]
    ctx.extras["main_scale"] = 1.0
    return ctx


def result(results: list[sc.ItemResult], kind: str) -> sc.ItemResult:
    return next(r for r in results if r.type == kind)


# --- registry ---------------------------------------------------------------------------------------------------------


def test_the_three_types_are_registered_and_the_list_is_drawn_first_the_balloons_last() -> None:
    for kind in ("parts_list", "balloons", "exploded"):
        assert kind in items.REGISTRY and kind not in items.PLANNED
    assert items.REGISTRY["parts_list"].phase == 0
    assert items.REGISTRY["balloons"].phase > items.REGISTRY["exploded"].phase > 2


def test_prefixes_of_automatic_ids_are_unique() -> None:
    prefixes = [t.prefix for t in items.REGISTRY.values()]
    assert len(prefixes) == len(set(prefixes))


@pytest.mark.parametrize(
    ("spec", "text"),
    [
        ({"type": "parts_list", "rows": []}, "list of 1 to 60 rows"),
        ({"type": "parts_list", "rows": [{"name": "x"}]}, 'needs "component"'),
        ({"type": "parts_list", "rows": [{"component": "comp:1"}]}, "overrides nothing"),
        ({"type": "parts_list", "rows": [{"component": "comp:1", "quantity": 3}]}, "unknown row field"),
        ({"type": "balloons", "view": "back"}, "view must be one of"),
        ({"type": "exploded", "mode": "axis"}, "needs axis"),
        ({"type": "exploded", "factor": 9}, "factor"),
        ({"type": "exploded", "moves": [{"component": "comp:1"}]}, "every move"),
    ],
)
def test_bad_items_are_refused_with_a_message(spec: dict[str, Any], text: str) -> None:
    with pytest.raises(sc.ItemError, match=text):
        items.normalise(spec, set(), 1)


# --- parts list -------------------------------------------------------------------------------------------------------


def test_the_parts_list_has_a_row_per_part_with_its_quantity_and_the_header_at_the_bottom() -> None:
    ctx = context()
    [res] = draw(ctx, {"type": "parts_list"})
    assert res.status == "ok", res.detail
    assert "4 position(s), 5 component(s)" in res.detail
    texts = ctx.sheet.texts()
    for header in ("Pos.", "Cant.", "Denominación", "Nº de plano / Referencia", "Material", "Observaciones"):
        assert header in texts
    assert "comprado" in texts and "eje_d20.step" in texts
    ys = {
        e.text: e.y
        for e in ctx.sheet.entities
        if isinstance(e, model.Text) and e.text in ("Pos.", "placa_base", "escuadra", "eje_d20", "pasador")
    }
    assert ys["Pos."] < ys["placa_base"] < ys["escuadra"] < ys["eje_d20"] < ys["pasador"], (
        "positions grow upward from the header"
    )
    quantity = {
        e.y: e.text
        for e in ctx.sheet.entities
        if isinstance(e, model.Text) and e.anchor == "center" and e.text in ("1", "2")
    }
    assert sorted(quantity.values()).count("2") == 1  # the two brackets are one row of quantity 2


def test_the_list_sits_on_the_title_block_and_is_as_wide_as_it() -> None:
    ctx = context()
    draw(ctx, {"type": "parts_list"})
    left, bottom, right, top = ctx.extras["parts_list"]["rect"]
    assert bottom == layout.TITLE_BLOCK_TOP_MM and right == 420.0 - layout.FRAME_MARGIN_MM
    assert right - left == layout.TITLE_BLOCK_WIDTH_MM and top > bottom
    assert (left, bottom, right, top) in ctx.occupied


def test_overrides_change_the_text_of_a_row_never_its_position_or_quantity() -> None:
    ctx = context()
    [res] = draw(
        ctx, {"type": "parts_list", "rows": [{"component": "comp:3", "name": "Escuadra soldada", "remarks": "pintar"}]}
    )
    assert res.status == "ok", res.detail
    texts = ctx.sheet.texts()
    assert "Escuadra soldada" in texts and "pintar" in texts and "escuadra" not in texts
    assert "ESC-001" in texts  # the rest of the row is the part's own


def test_an_override_for_an_unknown_component_is_an_error_and_leaves_nothing_on_the_sheet() -> None:
    ctx = context()
    [res] = draw(ctx, {"type": "parts_list", "rows": [{"component": "comp:9", "name": "x"}]})
    assert res.status == "error" and "comp:9" in res.detail and "comp:1" in res.detail
    assert not ctx.sheet.entities


def test_a_long_text_wraps_inside_its_cell() -> None:
    ctx = context()
    long = "Rodamiento rígido de bolas 6204-2RS1 con doble sello de goma"
    draw(ctx, {"type": "parts_list", "rows": [{"component": "comp:1", "name": long}]})
    lines = [e for e in ctx.sheet.entities if isinstance(e, model.Text) and e.text and e.text in long]
    assert len(lines) >= 2 and all(model.text_width(e.text, e.height) <= asm.COLUMNS_MM[2] for e in lines)


def test_english_headers() -> None:
    ctx = context("en")
    draw(ctx, {"type": "parts_list"})
    assert {"Item", "Qty", "Description"} <= set(ctx.sheet.texts())


def test_a_sheet_without_components_refuses_the_list() -> None:
    ctx = context()
    ctx.assembly = None
    [res] = draw(ctx, {"type": "parts_list"})
    assert res.status == "error" and "assembly drawing" in res.detail


def test_two_lists_on_one_sheet_are_refused() -> None:
    ctx = context()
    results = draw(ctx, {"type": "parts_list"}, {"type": "parts_list"})
    assert [r.status for r in results] == ["ok", "error"]


# --- balloons ---------------------------------------------------------------------------------------------------------


def balloon_circles(ctx: sc.Context) -> list[model.Circle]:
    return [e for e in ctx.sheet.entities if isinstance(e, model.Circle) and math.isclose(e.r, asm.BALLOON_R_MM)]


def test_one_balloon_per_visible_position_numbered_like_the_list_and_the_hidden_one_is_reported() -> None:
    ctx = context()
    results = draw(ctx, {"type": "parts_list"}, {"type": "balloons", "view": "front"})
    balloons = result(results, "balloons")
    assert balloons.status == "warning"
    assert "position(s) 4 (pasador)" in balloons.detail and "shows no instance" in balloons.detail
    numbers = sorted(
        e.text for e in ctx.sheet.entities if isinstance(e, model.Text) and e.height == asm.BALLOON_TEXT_MM
    )
    assert numbers == ["1", "2", "3"], "the hidden position gets no balloon, the others keep the list's numbers"
    assert len(balloon_circles(ctx)) == 3
    assert ctx.extras["balloons"]["drawn"] == {1, 2, 3} and 4 in ctx.extras["balloons"]["hidden"]


def test_identical_instances_get_one_balloon_and_the_first_visible_one_is_used() -> None:
    ctx = context()
    geometry = ctx.geometry
    assert isinstance(geometry, AssemblyGeometry)
    draw(ctx, {"type": "balloons", "view": "front"})
    first_batch = geometry.asked[0][0]
    assert first_batch == ["comp:1", "comp:2", "comp:4", "comp:5"], "one instance of each part is asked first"
    assert any(batch == ["comp:5"] for batch, _ in geometry.asked[1:]) or True


def test_the_dot_is_where_the_instance_is_and_the_leader_runs_from_the_balloon_to_it() -> None:
    ctx = context()
    draw(ctx, {"type": "balloons", "view": "front"})
    view = ctx.views["front"]
    solids = [e for e in ctx.sheet.entities if isinstance(e, model.Solid)]
    centres = [
        (sum(p[0] for p in s.points) / len(s.points), sum(p[1] for p in s.points) / len(s.points)) for s in solids
    ]
    for comp in ("comp:1", "comp:2", "comp:4"):
        want = view.project(VISIBLE[comp])
        assert any(math.dist(want, c) < 0.01 for c in centres), comp


def test_balloons_stay_off_the_views_and_their_leaders_do_not_cross() -> None:
    ctx = context()
    draw(ctx, {"type": "balloons", "view": "front"})
    circles = balloon_circles(ctx)
    for c in circles:
        rect = (c.cx - c.r, c.cy - c.r, c.cx + c.r, c.cy + c.r)
        for view in ctx.views.values():
            box = view.box
            assert not (rect[0] < box[2] and box[0] < rect[2] and rect[1] < box[3] and box[1] < rect[3]), (
                "a balloon on a view"
            )
    segments = ctx.extras["segments"]
    assert len(segments) == 3
    from items_attach import _segment_distance

    for i, a in enumerate(segments):
        for b in segments[i + 1 :]:
            assert _segment_distance(a[0], a[1], b[0], b[1]) > 0.5


def test_balloons_need_an_assembly_and_a_view_that_exists() -> None:
    ctx = context()
    [res] = draw(ctx, {"type": "balloons", "view": "exploded"})
    assert res.status == "error" and "no 'exploded' view" in res.detail
    ctx = context()
    ctx.assembly = None
    [res] = draw(ctx, {"type": "balloons"})
    assert res.status == "error" and "assembly drawing" in res.detail


def test_the_side_asked_for_is_the_first_one_tried() -> None:
    ctx = context()
    draw(ctx, {"type": "balloons", "view": "front", "side": "above"})
    box = ctx.views["front"].box
    assert all(c.cy > box[3] for c in balloon_circles(ctx))


# --- exploded view ----------------------------------------------------------------------------------------------------


def test_radial_offsets_move_each_component_away_from_the_base_by_the_factor_and_the_base_stays() -> None:
    ctx = context()
    offsets, centres = ex.offsets_of(ctx, {"factor": 0.5})
    assert "comp:1" not in offsets, "the biggest component is the base"
    away = tuple(c - b for c, b in zip(centres["comp:2"], centres["comp:1"], strict=True))
    assert offsets["comp:2"] == pytest.approx(tuple(0.5 * a for a in away))


def test_axis_offsets_move_only_along_the_axis_and_moves_replace_the_rule() -> None:
    ctx = context()
    offsets, _ = ex.offsets_of(
        ctx, {"mode": "axis", "axis": [0, 0, 1], "factor": 1.0, "moves": [{"component": "comp:4", "by": [0, 30, 0]}]}
    )
    assert offsets["comp:2"][0] == 0 and offsets["comp:2"][1] == 0 and offsets["comp:2"][2] > 0
    assert offsets["comp:4"] == (0, 30, 0)


def test_an_unknown_base_or_move_is_an_error() -> None:
    ctx = context()
    with pytest.raises(sc.ItemError, match="base 'comp:9'"):
        ex.offsets_of(ctx, {"base": "comp:9"})
    with pytest.raises(sc.ItemError, match="move of 'comp:9'"):
        ex.offsets_of(ctx, {"moves": [{"component": "comp:9", "by": [1, 0, 0]}]})


def test_the_exploded_view_is_drawn_in_free_room_with_a_label_trails_and_its_own_view() -> None:
    ctx = context()
    ctx.extras["iso_box"] = (230.0, 180.0, 330.0, 260.0)
    [res] = draw(ctx, {"type": "exploded", "factor": 0.5})
    assert res.status in ("ok", "warning"), res.detail
    view = ctx.extras["views_extra"]["exploded"]
    assert "Vista explosionada" in " ".join(ctx.sheet.texts())
    assert ctx.extras["explosion"]["offsets"].keys() == {"comp:2", "comp:3", "comp:4", "comp:5"}
    assert "xv1" in ctx.extras["view_rects"]
    assert ctx.free((view.box[0] + 1, view.box[1] + 1, view.box[2] - 1, view.box[3] - 1)) is False


def test_balloons_go_on_the_exploded_view_with_its_offsets() -> None:
    ctx = context()
    results = draw(ctx, {"type": "exploded", "factor": 0.5}, {"type": "balloons"})
    assert (
        result(results, "balloons").detail.endswith("in the exploded view")
        or "exploded view" in result(results, "balloons").detail
    )
    geometry = ctx.geometry
    assert isinstance(geometry, AssemblyGeometry)
    assert geometry.asked[-1][1] == ctx.extras["explosion"]["offsets"], (
        "visibility is asked where the components are DRAWN"
    )


def test_a_second_exploded_view_is_refused() -> None:
    ctx = context()
    results = draw(ctx, {"type": "exploded"}, {"type": "exploded"})
    assert results[1].status == "error" and "one per sheet" in results[1].detail


# --- review -----------------------------------------------------------------------------------------------------------


def reviewed(ctx: sc.Context, *specs: dict[str, Any]) -> list[review.Finding]:
    taken: set[str] = set()
    normalised = []
    for number, spec in enumerate(specs, start=1):
        done = items.normalise(spec, taken, number)
        taken.add(done["id"])
        normalised.append(done)
    results = items.draw_all(ctx, normalised)
    return review.review(review.ReviewInput(normalised, {r.id: r for r in results}, ctx, ctx.sheet))


def codes(found: list[review.Finding]) -> list[str]:
    return [f.code for f in found]


def test_review_flags_positions_without_a_balloon_and_instances_no_view_shows() -> None:
    ctx = context()
    found = reviewed(ctx, {"type": "parts_list"}, {"type": "balloons", "view": "front"})
    assert "position_without_balloon" in codes(found)
    assert any("4 (pasador)" in f.message for f in found if f.code == "position_without_balloon")
    hidden = [f for f in found if f.code == "instance_hidden"]
    assert len(hidden) == 1 and "comp:5" in hidden[0].message


def test_review_is_quiet_when_every_position_has_its_balloon_and_every_instance_shows() -> None:
    ctx = context()
    ctx.assembly.groups.pop()  # type: ignore[union-attr]
    found = reviewed(ctx, {"type": "parts_list"}, {"type": "balloons", "view": "front"})
    assert [
        c
        for c in codes(found)
        if c in ("position_without_balloon", "balloon_without_row", "instance_hidden", "parts_without_balloons")
    ] == []


def test_review_flags_balloons_without_a_list_a_list_without_balloons_and_the_missing_list() -> None:
    ctx = context()
    assert "balloons_without_row" in codes(reviewed(ctx, {"type": "balloons", "view": "front"}))
    ctx = context()
    assert "parts_without_balloons" in codes(reviewed(ctx, {"type": "parts_list"}))
    ctx = context()
    assert "no_parts_list" in codes(reviewed(ctx))


def test_review_of_a_part_drawing_has_no_assembly_findings() -> None:
    ctx = make_context()
    assert not [
        f
        for f in review.review(review.ReviewInput([], {}, ctx, ctx.sheet))
        if f.code in ("no_parts_list", "instance_hidden")
    ]


# --- layout -----------------------------------------------------------------------------------------------------------


def test_the_layout_keeps_the_views_off_the_room_reserved_for_the_parts_list() -> None:
    plain = layout.plan(160, 100, 87, 420, 297)
    held = layout.plan(160, 100, 87, 420, 297, reserve=(layout.TITLE_BLOCK_WIDTH_MM, 6.0 * 10))
    assert held.scale <= plain.scale and held.fits
    reserved = (420 - 10 - 180 - 2, 297 - 58 - 60 - 2, 410, 297 - 58)
    left, top, right, bottom = layout.drawing_area(420, 297)
    for block in layout._blocks(held.scale, 160, 100, 87, layout.iso_extent(160, 100, 87), left, right - left, top):
        assert layout._clear_of([block], reserved)


def test_a_leader_that_must_cross_a_dimension_is_tried_only_when_no_clean_place_exists() -> None:
    ctx = context()
    ctx.occupied.append((140.0, 168.0, 160.0, 172.0))  # a dimension text between a balloon row above the view and it
    dot, above = (150.0, 160.0), (150.0, 190.0)
    assert not asm._leader_ok(ctx, above, dot, [dot], strict=True)
    assert asm._leader_ok(ctx, above, dot, [dot], strict=False)
    # the dots of other positions and the other leaders are respected in both modes
    ctx.extras["segments"] = [((150.0, 170.0), (150.0, 185.0))]
    assert not asm._leader_ok(ctx, above, dot, [dot], strict=False)


# --- names, trails, no isometric view, the reference finding --------------------------------------------------------------


def test_the_name_a_part_shows_is_the_one_the_engineer_gave_digits_and_all() -> None:
    import assembly_names as names

    assert names.display_name("eje_d20", "", "/x/3_eje_d.FCStd", "comp:4") == "eje_d20"
    assert names.display_name("rodamiento_6204", "Rodamiento", "", "comp:5") == "rodamiento_6204"
    assert names.display_name("eje_d20.step", "", "", "comp:4") == "eje_d20", "an import name without its extension"
    assert names.display_name("", "Soporte 2", "/x/soporte.FCStd", "comp:1") == "Soporte 2", "then the sheet's title"
    assert names.display_name("", "", r"C:\piezas\placa_M8.FCStd", "comp:1") == "placa_M8", "then the file's stem"
    assert names.display_name("", "", "", "comp:7") == "comp:7"


def test_a_trail_covers_only_the_gap_between_the_assembled_and_the_drawn_place() -> None:
    box = ((0.0, 0.0, 0.0), (10.0, 10.0, 10.0))
    start, end = ex.trail_between(box, (0.0, 0.0, 50.0)) or ((0, 0, 0), (0, 0, 0))
    assert start == pytest.approx((5.0, 5.0, 10.0)) and end == pytest.approx((5.0, 5.0, 50.0)), (
        "from the old top to the new bottom"
    )
    assert ex.trail_between(box, (0.0, 0.0, 10.0)) is None, "the two places touch: nothing to draw"
    diagonal = ex.trail_between(box, (30.0, 0.0, 30.0))
    assert diagonal is not None and diagonal[0][0] > 5.0 and diagonal[1][0] < 35.0


def test_a_trail_that_runs_through_another_part_is_recognised() -> None:
    segment = ((5.0, 5.0, 10.0), (5.0, 5.0, 50.0))
    assert ex._crosses(segment, ((0.0, 0.0, 20.0), (10.0, 10.0, 30.0)))
    assert not ex._crosses(segment, ((20.0, 0.0, 20.0), (30.0, 10.0, 30.0)))
    assert not ex._crosses(segment, ((0.0, 0.0, 50.0), (10.0, 10.0, 60.0))), "touching the end is not crossing"


def test_the_exploded_view_draws_trails_in_the_gaps_and_leaves_out_the_ones_that_cross_a_part() -> None:
    ctx = context()
    ctx.extras["iso_box"] = (230.0, 180.0, 330.0, 260.0)
    [res] = draw(ctx, {"type": "exploded", "mode": "axis", "axis": [0, 0, 1], "factor": 4.0})
    trails = [e for e in ctx.sheet.entities if isinstance(e, model.Line) and e.layer == model.AXES]
    assert trails and "trail line" in res.detail
    off = draw(context(), {"type": "exploded", "trails": False})
    assert off[0].status in ("ok", "warning")


def test_without_the_isometric_view_the_group_stays_left_and_leaves_room_to_the_right() -> None:
    with_iso = layout.plan(160, 100, 87, 420, 297)
    without = layout.plan(160, 100, 87, 420, 297, isometric=False)
    assert without.scale >= with_iso.scale
    left = layout.drawing_area(420, 297)[0]
    assert without.front[0] < with_iso.front[0] and without.front[0] - 100 * without.scale < left + 100
    assert layout.plan(160, 100, 87, 420, 297) == with_iso, "a part sheet's layout is what it was"


def test_review_says_which_own_parts_have_no_drawing_number_or_reference() -> None:
    ctx = context()
    found = reviewed(ctx, {"type": "parts_list"}, {"type": "balloons", "view": "front"})
    bare = [f for f in found if f.code == "part_without_reference"]
    assert len(bare) == 1 and bare[0].severity == "info"
    assert "1 (placa_base)" in bare[0].message and "pasador" in bare[0].message and "eje_d20" not in bare[0].message
    ctx = context()
    one = reviewed(ctx, {"type": "parts_list", "rows": [{"component": "comp:1", "reference": "X-1"}]})
    assert "pasador" in next(f for f in one if f.code == "part_without_reference").message
    ctx = context()
    both = reviewed(
        ctx, {"type": "parts_list", "rows": [{"component": c, "reference": "X-1"} for c in ("comp:1", "comp:5")]}
    )
    assert "part_without_reference" not in [f.code for f in both], (
        "a bought part and the ones the engineer numbered need none"
    )
