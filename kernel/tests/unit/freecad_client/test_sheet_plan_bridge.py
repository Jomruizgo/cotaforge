# ruff: noqa: E501
"""The bridge between a composed sheet and the layout planner (no FreeCAD): the linear dimensions the planner moves, what it
leaves where it is and says so, and what stands as an obstacle."""

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
