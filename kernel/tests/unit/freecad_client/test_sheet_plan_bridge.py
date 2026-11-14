# ruff: noqa: E501
"""The bridge between a composed sheet and the layout planner (no FreeCAD): the linear dimensions the planner moves, what it
leaves where it is and says so, and what stands as an obstacle."""

import math
import sys
from pathlib import Path

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import sheet_collide as collide  # noqa: E402
import sheet_draw as draw  # noqa: E402
import sheet_model as model  # noqa: E402
import sheet_plan_bridge as bridge  # noqa: E402
import sheet_plan_layout as pl  # noqa: E402
import sheet_units as units  # noqa: E402

PAGE = (297.0, 210.0)
AREA = (10.0, 50.0, 287.0, 200.0)  # page mm, y up: above the title block, inside the frame
TITLE = (100.0, 10.0, 287.0, 50.0)
FRONT = (60.0, 110.0, 160.0, 160.0)  # 100 x 50 view, y up


def dimension(key: str, a: tuple[float, float], b: tuple[float, float], orient: str, line: float, text: str) -> units.Unit:
    entities, _ = draw.linear_dimension(a, b, orient, line, text)
    return units.Unit(key, key, units.KIND_ITEM, tuple(entities), "dim")


def width(key: str = "w", line: float = 100.0) -> units.Unit:
    return dimension(key, (60.0, 110.0), (160.0, 110.0), "h", line, "100")


def spec(key: str, **more: object) -> dict[str, object]:
    return {"id": key, "type": "dim", "kind": "distance", **more}


def test_a_dimension_line_is_read_from_its_ink() -> None:
    ink = collide.ink_of("w", width().entities)
    axis, feet, box = bridge.read_dimension(ink)  # type: ignore[misc]
    assert axis == "h" and sorted(round(x) for x, _ in feet) == [60, 160]
    assert box[2] - box[0] > 3 and box[3] - box[1] > 2


def test_a_dimension_that_asked_for_no_place_is_given_a_side_and_an_offset() -> None:
    prepared = bridge.prepare(PAGE, AREA, TITLE, {"front": FRONT}, [width()], [spec("w")])
    out = bridge.run(prepared)
    assert set(out.placements) == {"w"} and not out.unplaced
    placed = out.placements["w"]
    assert placed["side"] in ("above", "below") and placed["offset_mm"] >= 10.0  # the first line stands 10 mm off the view (ISO 129)


def test_what_the_item_asked_for_is_never_moved_and_is_an_obstacle() -> None:
    chosen = spec("w", side="below", offset_mm=12.0)
    prepared = bridge.prepare(PAGE, AREA, TITLE, {"front": FRONT}, [width()], [chosen])
    assert not prepared.movable
    assert [i.kind for i in prepared.items] == ["callout"]  # its ink, pinned
    assert bridge.run(prepared).placements == {}


def test_two_dimensions_of_the_same_side_are_stacked_not_drawn_over_each_other() -> None:
    long_one = width("long", 100.0)
    short = dimension("short", (60.0, 110.0), (100.0, 110.0), "h", 100.0, "40")
    prepared = bridge.prepare(PAGE, AREA, TITLE, {"front": FRONT}, [long_one, short], [spec("long"), spec("short")])
    out = bridge.run(prepared)
    assert set(out.placements) == {"long", "short"}
    a, b = out.placements["long"], out.placements["short"]
    assert (a["side"], a["offset_mm"]) != (b["side"], b["offset_mm"])  # the planner cannot put two lines on one another


def test_a_dimension_the_planner_cannot_read_stays_and_is_reported() -> None:
    mark = units.Unit("n", "n", units.KIND_ITEM, (model.Text(70.0, 120.0, "note"),), "dim")
    prepared = bridge.prepare(PAGE, AREA, TITLE, {"front": FRONT}, [mark], [spec("n")])
    out = bridge.run(prepared)
    assert out.placements == {} and out.unplaced and out.unplaced[0][0] == "n"
    assert "not a linear dimension" in out.unplaced[0][1]


def test_a_dimension_with_no_free_room_is_reported_as_unplaced_never_dropped() -> None:
    wall = units.Unit(
        "wall", "wall", units.KIND_ITEM, tuple(model.Line(10.0, y, 287.0, y) for y in range(52, 200, 1)), "line"
    )  # ink over the whole drawing area
    prepared = bridge.prepare(PAGE, AREA, TITLE, {"front": FRONT}, [width(), wall], [spec("w")])
    out = bridge.run(prepared)
    assert "w" not in out.placements and [k for k, _ in out.unplaced] == ["w"]


def test_a_vertical_dimension_is_read_and_placed_left_or_right() -> None:
    height = dimension("h", (160.0, 110.0), (160.0, 160.0), "v", 175.0, "50")
    prepared = bridge.prepare(PAGE, AREA, TITLE, {"front": FRONT}, [height], [spec("h")])
    assert [i.axis for i in prepared.items] == ["v"]
    out = bridge.run(prepared)
    assert out.placements["h"]["side"] in ("left", "right")


def test_views_that_do_not_fit_the_area_say_how_much_room_they_need() -> None:
    big = {"front": (20.0, 60.0, 280.0, 190.0), "top": (20.0, 60.0, 280.0, 190.0)}
    flat = bridge.prepare(PAGE, AREA, TITLE, big, [], [])
    out = bridge.run(flat)
    assert out.fits is False and out.needed[0] > 0  # two views of the area's size cannot both have room
    small = bridge.prepare(PAGE, AREA, TITLE, {"front": FRONT}, [], [])
    assert bridge.run(small).fits is True


def test_the_views_are_related_as_the_sheet_arranges_them() -> None:
    boxes = {"front": (60.0, 110.0, 160.0, 160.0), "top": (60.0, 170.0, 160.0, 195.0), "right": (170.0, 110.0, 195.0, 160.0)}
    prepared = bridge.prepare(PAGE, AREA, TITLE, boxes, [], [])
    relation = {v.id: v.relation for v in prepared.related}
    assert relation["front"] is None and relation["top"] == ("front", "above") and relation["right"] == ("front", "right")


def test_a_planned_dimension_stands_where_its_offset_says() -> None:
    prepared = bridge.prepare(PAGE, AREA, TITLE, {"front": FRONT}, [width()], [spec("w")])
    out = bridge.run(prepared)
    plan = pl.plan_layout(prepared.sheet, prepared.views, prepared.items)
    line = plan.items[0].segments[0]
    top = prepared.boxes["front"]
    distance = top[1] - line[1] if out.placements["w"]["side"] == "above" else line[1] - top[3]
    assert abs(distance - out.placements["w"]["offset_mm"]) <= 0.25


def callout(key: str, tip: tuple[float, float], elbow: tuple[float, float], text: str = "M12 long. util 20") -> units.Unit:
    entities, _ = draw.leader(tip, elbow, [text], "right")
    return units.Unit(key, key, units.KIND_ITEM, tuple(entities), "dim")


def test_a_leader_is_read_from_its_ink() -> None:
    unit = callout("t", (150.0, 160.0), (170.0, 175.0))
    tip, elbow, box = bridge.read_callout(collide.ink_of("t", unit.entities))  # type: ignore[misc]
    assert tip == (150.0, 160.0) and elbow == (170.0, 175.0) and box[0] == 170.0 and box[2] - box[0] > 4


def test_a_diameter_callout_is_given_the_elbow_where_the_planner_puts_its_text() -> None:
    unit = callout("t", (150.0, 160.0), (150.0, 175.0))
    chosen = {"id": "t", "type": "dim", "kind": "diameter"}
    prepared = bridge.prepare(PAGE, AREA, TITLE, {"front": FRONT}, [unit], [chosen])
    assert list(prepared.movable) == ["t"] and prepared.tips["t"] == (150.0, 160.0)
    out = bridge.run(prepared)
    dx, dy = out.placements["t"]["elbow_mm"]
    assert abs(dx) + abs(dy) > 5  # the vector from the tip to the elbow: a leader of some length
    assert "side" not in out.placements["t"]  # the tip stays where the CAD finds it: only the elbow is told


def test_a_leader_that_already_has_its_elbow_is_not_moved() -> None:
    unit = callout("t", (150.0, 160.0), (150.0, 175.0))
    prepared = bridge.prepare(PAGE, AREA, TITLE, {"front": FRONT}, [unit], [{"id": "t", "type": "dim", "kind": "diameter", "elbow_mm": [0.0, 15.0]}])
    assert not prepared.movable


def test_two_leaders_do_not_end_on_one_another() -> None:
    a, b = callout("a", (150.0, 160.0), (150.0, 175.0), "M12 long. util 20"), callout("b", (155.0, 160.0), (160.0, 172.0), "1 x 45")
    specs = [{"id": "a", "type": "dim", "kind": "diameter"}, {"id": "b", "type": "dim", "kind": "chamfer"}]
    out = bridge.run(bridge.prepare(PAGE, AREA, TITLE, {"front": FRONT}, [a, b], specs))
    assert set(out.placements) == {"a", "b"} and out.placements["a"] != out.placements["b"]


# --- the other items: angular dimensions, datums, weld symbols, hole callouts ---------------------------------------------


def angle_unit(key: str = "an") -> units.Unit:
    entities, _ = draw.angle_dimension((160.0, 110.0), 0.0, 40.0, 12.0, "40°")
    return units.Unit(key, key, units.KIND_ITEM, tuple(entities), "dim")


def test_an_angular_dimension_is_read_and_given_an_arc_radius() -> None:
    unit = angle_unit()
    apex, (a0, a1), text = bridge.read_angle(unit.entities)  # type: ignore[misc]
    assert apex == (160.0, 110.0) and (a0, a1) == (0.0, 40.0) and text[2] > text[0]
    prepared = bridge.prepare(PAGE, AREA, TITLE, {"front": FRONT}, [unit], [{"id": "an", "type": "dim", "kind": "angle"}])
    assert list(prepared.movable) == ["an"] and prepared.items[0].kind == "angle"
    out = bridge.run(prepared)
    assert not out.unplaced and out.placements["an"]["offset_mm"] >= pl.ANGLE_RADII_MM[0]
    assert "side" not in out.placements["an"]


def test_an_angular_dimension_that_has_its_radius_is_not_moved() -> None:
    prepared = bridge.prepare(PAGE, AREA, TITLE, {"front": FRONT}, [angle_unit()], [{"id": "an", "type": "dim", "kind": "angle", "offset_mm": 20.0}])
    assert not prepared.movable


def datum_unit(key: str = "dC") -> units.Unit:
    import items_gdt as gdt  # noqa: PLC0415

    triangle, base = gdt._triangle((110.0, 110.0), (0.0, -1.0))  # on the bottom edge of the view, pointing down
    boxed, _ = gdt._frame_at((110.0, 96.0), (0.0, -1.0), [("letter", "C")])
    return units.Unit(key, key, units.KIND_ITEM, (triangle, model.Line(base[0], base[1], 110.0, 96.0, model.DIMENSIONS), *boxed), "datum")


def test_a_datum_is_read_and_given_a_side_and_a_stem() -> None:
    unit = datum_unit()
    tip, normal, frame = bridge.read_datum(collide.ink_of("dC", unit.entities), unit.entities)  # type: ignore[misc]
    assert tip == (110.0, 110.0) and normal == (0.0, -1.0) and frame[2] - frame[0] > 4
    prepared = bridge.prepare(PAGE, AREA, TITLE, {"front": FRONT}, [unit], [{"id": "dC", "type": "datum", "letter": "C", "face": ["face:1"]}])
    assert list(prepared.movable) == ["dC"]
    out = bridge.run(prepared)
    assert not out.unplaced and out.placements["dC"]["side"] == "below" and out.placements["dC"]["offset_mm"] >= 5.0
    assert out.placements["dC"]["tip_at"] == [110.0, 110.0]  # the triangle stays where it touches the face: only the stem and the side are asked


def test_the_datum_of_an_axis_stays_on_the_shelf_of_its_callout() -> None:
    prepared = bridge.prepare(PAGE, AREA, TITLE, {"front": FRONT}, [datum_unit()], [{"id": "dC", "type": "datum", "letter": "C", "on": "h1"}])
    assert not prepared.movable and not prepared.unread


def weld_unit(key: str = "w1", tips: tuple[tuple[float, float], ...] = ((150.0, 150.0), (150.0, 130.0))) -> units.Unit:
    import items_weld as weld  # noqa: PLC0415

    anchor = (190.0, 150.0)
    entities, _ = weld.symbol(anchor, 1.0, {"kind": "fillet", "size_mm": 4.0, "sides": "arrow"}, False, 1)
    for t in tips:
        entities = [*entities, model.Line(t[0], t[1], anchor[0], anchor[1], model.DIMENSIONS), draw.arrow(t, (-1.0, 0.0))]
    return units.Unit(key, key, units.KIND_ITEM, tuple(entities), "weld")


def test_a_weld_symbol_is_read_with_its_arrows_and_its_size() -> None:
    tips, (reach, up, down) = bridge.read_weld(weld_unit().entities)  # type: ignore[misc]
    assert tips == [(150.0, 150.0), (150.0, 130.0)]
    assert reach >= 14.0 and up > 0 and down > 0


def test_a_weld_symbol_is_given_the_place_of_its_reference_line() -> None:
    prepared = bridge.prepare(PAGE, AREA, TITLE, {"front": FRONT}, [weld_unit()], [{"id": "w1", "type": "weld"}])
    assert list(prepared.movable) == ["w1"]
    out = bridge.run(prepared)
    assert not out.unplaced, out.unplaced
    x, y = out.placements["w1"]["anchor_at"]
    assert AREA[0] < x < AREA[2] and AREA[1] < y < AREA[3]  # page mm, y up
    assert set(out.placements["w1"]) == {"anchor_at"}


def test_a_weld_symbol_with_two_reference_lines_is_an_obstacle() -> None:
    both = units.Unit("w1", "w1", units.KIND_ITEM, weld_unit().entities + tuple(model.Line(100.0, 60.0 + i, 130.0, 60.0 + i, model.DIMENSIONS) for i in (0,)), "weld")
    prepared = bridge.prepare(PAGE, AREA, TITLE, {"front": FRONT}, [both], [{"id": "w1", "type": "weld"}])
    assert not prepared.movable and prepared.unread and "weld symbol" in prepared.unread[0][1]


def test_a_pinned_weld_arrow_is_an_arrow_for_the_planner() -> None:
    prepared = bridge.prepare(PAGE, AREA, TITLE, {"front": FRONT}, [weld_unit()], [{"id": "w1", "type": "weld", "side": "right"}])
    assert not prepared.movable
    kinds = {k for i in prepared.items for k in i.fixed_kinds}
    assert "arrow" in kinds


def hole_unit(key: str = "h1") -> units.Unit:
    entities, _ = draw.leader((140.0, 140.0), (170.0, 175.0), ["4× Ø6 H7", "pasante"], "right")
    return units.Unit(key, key, units.KIND_ITEM, (*entities, *draw.centre_mark(140.0, 148.0, 8.0)), "hole_dim")  # the hole: centre (140, 148), radius 8


def test_a_hole_callout_is_given_its_tip_and_its_elbow() -> None:
    prepared = bridge.prepare(PAGE, AREA, TITLE, {"front": FRONT}, [hole_unit()], [{"id": "h1", "type": "hole_dim", "at": [[0, 0, 0]]}])
    assert list(prepared.movable) == ["h1"]
    out = bridge.run(prepared)
    tip_x, tip_y, ex, ey = out.placements["h1"]["leader_mm"]
    assert abs(math.dist((tip_x, tip_y), (140.0, 148.0)) - 8.0) < 0.05  # the tip stays on the circle of the hole
    assert abs(ex - tip_x) + abs(ey - tip_y) > 5
    nx, ny = tip_x - 140.0, tip_y - 148.0
    ux, uy = ex - tip_x, ey - tip_y
    assert abs(nx * uy - ny * ux) < 0.2 * math.hypot(nx, ny) * math.hypot(ux, uy)  # the leader runs along the radius: it points at the centre


def test_a_hole_callout_that_asked_for_its_place_is_not_moved() -> None:
    for asked in ({"side": "right"}, {"offset_mm": 12.0}, {"leader_mm": [140.0, 140.0, 170.0, 175.0]}):
        prepared = bridge.prepare(PAGE, AREA, TITLE, {"front": FRONT}, [hole_unit()], [{"id": "h1", "type": "hole_dim", **asked}])
        assert not prepared.movable, asked


def test_what_the_planner_does_not_move_has_a_stated_reason() -> None:
    for kind in ("overall", "datum_dims", "note", "gdt", "finish", "balloons", "section"):
        assert bridge.FIXED_REASONS[kind].strip(), kind
    assert not set(bridge.FIXED_REASONS) & bridge.MOVABLE_TYPES


def two_hole_unit(key: str = "h2") -> units.Unit:
    """One hole callout item that drew a leader for each of two groups of holes (the way ``rq_callouts`` does)."""
    first, _ = draw.leader((140.0, 140.0), (170.0, 175.0), ["4× Ø6 H7", "pasante"], "right")
    second, _ = draw.leader((90.0, 128.0), (60.0, 100.0), ["2× Ø9", "pasante"], "left")
    marks = (*draw.centre_mark(140.0, 148.0, 8.0), *draw.centre_mark(97.0, 128.0, 7.0))
    return units.Unit(key, key, units.KIND_ITEM, (*first, *second, *marks), "hole_dim")


def test_a_hole_callout_with_a_leader_for_each_group_is_planned_leader_by_leader() -> None:
    prepared = bridge.prepare(PAGE, AREA, TITLE, {"front": FRONT}, [two_hole_unit()], [{"id": "h2", "type": "hole_dim", "all": True}])
    assert sorted(prepared.movable) == ["h2@0", "h2@1"]
    out = bridge.run(prepared)
    assert set(out.placements) == {"h2"} and not out.unplaced  # one placement for the item: the leaders of all its groups
    vectors = out.placements["h2"]["leader_mm"]
    assert len(vectors) == 2
    circles = [((140.0, 148.0), 8.0), ((97.0, 128.0), 7.0)]
    used = []
    for tip_x, tip_y, ex, ey in vectors:
        centre, radius = next((c, r) for c, r in circles if abs(math.dist((tip_x, tip_y), c) - r) < 0.05)  # each tip stays on a circle
        used.append(centre)
        nx, ny = tip_x - centre[0], tip_y - centre[1]
        assert abs(nx * (ey - tip_y) - ny * (ex - tip_x)) < 0.2 * math.hypot(nx, ny) * math.hypot(ex - tip_x, ey - tip_y)  # pointing at its centre
    assert sorted(used) == sorted(c for c, _ in circles)  # one leader for each hole


def test_the_centre_mark_of_a_hole_drawn_by_another_item_gives_the_circle_of_the_callout() -> None:
    callout = hole_unit()
    bare = units.Unit("h1", "h1", units.KIND_ITEM, tuple(e for e in callout.entities if getattr(e, "layer", None) != model.AXES), "hole_dim")
    marks = units.Unit("vl", "vl", units.KIND_ITEM, tuple(draw.centre_mark(140.0, 148.0, 8.0)), "view_lines")
    prepared = bridge.prepare(PAGE, AREA, TITLE, {"front": FRONT}, [bare, marks], [{"id": "h1", "type": "hole_dim", "at": [[0, 0, 0]]}, {"id": "vl", "type": "view_lines"}])
    assert list(prepared.movable) == ["h1"]
    assert prepared.items[0].extra[2] == 8.0  # the radius: the leader is told to point at the centre


def test_a_place_the_planner_gave_is_taken_off_when_the_sheet_is_rebuilt_but_the_engineers_stays() -> None:
    items = [
        {"id": "w", "type": "dim", "side": "below", "offset_mm": 12.0},
        {"id": "x", "type": "dim", "side": "above", "offset_mm": 30.0},
        {"id": "h", "type": "hole_dim", "leader_mm": [[1.0, 2.0, 3.0, 4.0], [5.0, 6.0, 7.0, 8.0]]},
    ]
    layout = {"placed": {"w": {"side": "below", "offset_mm": 12.0}, "x": {"side": "above", "offset_mm": 12.0}, "h": {"leader_mm": [[1.0, 2.0, 3.0, 4.0], [5.0, 6.0, 7.0, 8.0]]}}}
    bridge.forget_planned(items, layout)
    assert items[0] == {"id": "w", "type": "dim"}  # the planner's: planned again for the new views
    assert items[1] == {"id": "x", "type": "dim", "side": "above", "offset_mm": 30.0}  # the engineer changed the offset: his
    assert items[2] == {"id": "h", "type": "hole_dim"}
    bridge.forget_planned(items, None)  # a sheet that never had a layout


def test_a_text_of_the_sheet_keeps_a_margin_for_the_cad_that_sets_it_a_little_off() -> None:
    entities, _ = draw.linear_dimension((60.0, 110.0), (60.0, 160.0), "v", 40.0, "50")
    unit = units.Unit("v", "v", units.KIND_ITEM, tuple(entities), "overall")
    prepared = bridge.prepare(PAGE, AREA, TITLE, {"front": FRONT}, [unit], [{"id": "v", "type": "overall"}])
    ink = collide.ink_of("v", unit.entities)
    text = ink.texts[0][0]
    box = prepared.items[0].fixed_box
    assert box is not None
    flat = bridge.flip(text, PAGE[1])
    assert box[0] <= flat[0] - bridge.TEXT_SLACK_MM + 1e-9 and box[2] >= flat[2] + bridge.TEXT_SLACK_MM - 1e-9


def test_the_outline_the_planner_keeps_leaders_off_has_the_circles_of_the_holes_too() -> None:
    ink = [
        model.Line(60.0, 110.0, 160.0, 110.0, model.CONTOUR),
        model.Circle(100.0, 130.0, 6.0, model.CONTOUR),
        model.Arc(120.0, 140.0, 4.0, 0.0, 90.0, model.CONTOUR),
        model.Line(0.0, 0.0, 5.0, 5.0, model.AXES),  # a centre line is not the part
    ]
    pieces = bridge.view_outline(ink)
    assert len(pieces) > 12  # the circle is many short pieces, not one box
    assert all((s[0], s[1]) != (0.0, 0.0) for s in pieces)
    ring = [s for s in pieces if abs(math.dist((s[0], s[1]), (100.0, 130.0)) - 6.0) < 0.01]
    assert len(ring) >= 12
