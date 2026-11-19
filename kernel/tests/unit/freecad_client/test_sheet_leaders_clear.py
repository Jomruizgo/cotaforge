# ruff: noqa: E501
"""Callout leaders that cross nothing (ISO 128-22), measured in sheet coordinates on the planner's output (no FreeCAD): on the
geometry of a real 100 x 60 plate sheet (A4, 1:1) -- seven holes, the overall and the location dimensions all round it --
no leader crosses a dimension line, an extension line or another leader; a text keeps a text height from every other item; a
callout with no clean way out is reported (never drawn across a dimension) and the overall dimensions moved outward leave the
room it needs; one with an area of its view clear of lines may stand on it; the notes block keeps off the dimension lines."""

import math
import sys
from pathlib import Path

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import sheet_plan_layout as pl  # noqa: E402

PAGE = (297.0, 210.0)
TEXT_HEIGHT = 3.5
FRAME = (25.0, 15.0, 282.0, 137.0)
TITLE = (70.0, 152.0, 292.0, 200.0)
HOLES = (  # local to the view (y down): centre, radius
    *[((x, y), 3.0) for x in (12.0, 88.0) for y in (8.0, 52.0)],
    ((50.0, 8.0), 2.5),
    ((36.0, 30.0), 7.0),
    ((36.0, 30.0), 4.5),
    ((64.0, 30.0), 7.0),
    ((64.0, 30.0), 4.5),
)
# what the items of the sheet drew (page mm, y down): (item, text box, lines, kind of each line)
INK = (
    ("o1", (109.6, 14.8, 119.9, 23.0), [(64.7, 30.5, 64.7, 19.5), (164.7, 30.5, 164.7, 19.5), (64.7, 21.5, 164.7, 21.5), (165.7, 91.5, 176.7, 91.5), (165.7, 31.5, 176.7, 31.5), (174.7, 91.5, 174.7, 31.5)], ["ext", "ext", "dim", "ext", "ext", "dim"]),
    ("o1", (168.0, 57.4, 176.2, 65.6), [], []),
    ("loc", (110.6, 94.8, 118.8, 103.0), [(100.7, 62.5, 100.7, 103.5), (128.7, 62.5, 128.7, 103.5), (100.7, 101.5, 128.7, 101.5), (76.7, 84.5, 76.7, 110.5), (152.7, 84.5, 152.7, 110.5), (76.7, 108.5, 152.7, 108.5), (63.7, 91.5, 52.7, 91.5), (75.7, 83.5, 52.7, 83.5), (54.7, 97.5, 54.7, 77.5), (63.7, 91.5, 45.7, 91.5), (99.7, 61.5, 45.7, 61.5), (47.7, 91.5, 47.7, 61.5), (63.7, 91.5, 38.7, 91.5), (75.7, 39.5, 38.7, 39.5), (40.7, 91.5, 40.7, 39.5)], ["ext", "ext", "dim", "ext", "ext", "dim", "ext", "ext", "ext", "ext", "ext", "dim", "ext", "ext", "dim"]),
    ("loc", (110.6, 101.8, 118.8, 110.0), [], []),
    ("loc", (48.0, 84.4, 56.2, 90.5), [], []),
    ("loc", (41.0, 72.4, 49.2, 80.6), [], []),
    ("loc", (34.0, 61.4, 42.2, 69.6), [], []),
)
LEADERS = (  # the three leaders of the hole callout: 4x Ø6 H7 / 2x Ø9 + counterbore / M6 + tapping drill (w, h of the text rectangle)
    ("rq@0", 57.7, 8.7, (89.5, 54.6), (88.0, 52.0, 3.0)),
    ("rq@1", 28.3, 16.6, (29.9, 26.5), (36.0, 30.0, 7.0)),
    ("rq@2", 32.5, 16.6, (52.2, 9.3), (50.0, 8.0, 2.5)),
)


def circle(cx: float, cy: float, r: float, n: int = 36) -> list[pl.Segment]:
    pts = [(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n)) for i in range(n + 1)]
    return [(a[0], a[1], b[0], b[1]) for a, b in zip(pts, pts[1:], strict=False)]


def plate_outline() -> tuple[pl.Segment, ...]:
    box = [(0.0, 0.0, 100.0, 0.0), (100.0, 0.0, 100.0, 60.0), (100.0, 60.0, 0.0, 60.0), (0.0, 60.0, 0.0, 0.0)]
    return tuple(box + [s for (c, r) in HOLES for s in circle(c[0], c[1], r)])


def fixed_items(lift: float = 0.0) -> list[pl.Item]:
    """The ink of the sheet as the planner reads it; ``lift``: the overall dimensions moved this far outward (the width up, the height right)."""

    def up(y: float) -> float:
        return y - lift if y < 25.0 else y

    def right(x: float) -> float:
        return x + lift if x > 170.0 else x

    items: list[pl.Item] = []
    for n, (name, box, segs, kinds) in enumerate(INK):
        if name == "o1":
            segs = [(right(s[0]), up(s[1]), right(s[2]), up(s[3])) for s in segs]
            if box and box[1] < 25.0:
                box = (box[0], box[1] - lift, box[2], box[3] - lift)
            elif box:
                box = (box[0] + lift, box[1], box[2] + lift, box[3])
        items.append(pl.Item(f"{name}#{n}", "callout", None, fixed_box=box, fixed_segments=tuple(segs), fixed_kinds=tuple(kinds)))
    return items


def plan(lift: float = 0.0, inside: bool = True, leaders: tuple[str, ...] = ("rq@0", "rq@1", "rq@2"), glyph_height: bool = False) -> tuple[pl.Plan, list[pl.Item]]:
    sheet = pl.Sheet(PAGE, (FRAME[0], FRAME[1] - 2 * lift, FRAME[2] + 2 * lift, FRAME[3]), TITLE)
    views = [
        pl.View("iso", 56.6, 40.4, at=(214.7, 31.5)),
        pl.View("top", 100.0, 60.0, at=(64.7, 31.5), outline=plate_outline()),
    ]
    movable = [
        pl.Item(name, "leader", "top", w, 4.3 if glyph_height and name == "rq@0" else h, (tip,), extra=extra, inside=inside)
        for name, w, h, tip, extra in LEADERS
        if name in leaders
    ]
    fixed = fixed_items(lift)
    return pl.plan_layout(sheet, views, [*fixed, *movable]), fixed


def pieces(placed: pl.Placed) -> list[pl.Segment]:
    return list(placed.segments)


def lines_of_others(fixed: list[pl.Item]) -> list[tuple[str, str, pl.Segment]]:
    out = []
    for item in fixed:
        kinds = item.fixed_kinds or ("ext",) * len(item.fixed_segments)
        out += [(item.id, k, s) for s, k in zip(item.fixed_segments, kinds, strict=True)]
    return out


def crossings(result: pl.Plan, fixed: list[pl.Item]) -> list[str]:
    """What each leader (its line and its shelf, in sheet coordinates) crosses among the dimension lines, extension lines and the other leaders."""
    found: list[str] = []
    others = lines_of_others(fixed)
    leaders = [p for p in result.items if p.kind == "leader"]
    for p in leaders:
        for s in pieces(p):
            found += [f"{p.id} crosses {owner} ({kind})" for owner, kind, o in others if pl._cross(s, o)]
            for q in leaders:
                if q.id != p.id:
                    found += [f"{p.id} crosses {q.id}" for o in pieces(q) if pl._cross(s, o)]
    return found


def too_close(box: pl.Rect, fixed: list[pl.Item], gap: float = TEXT_HEIGHT) -> list[str]:
    """The items of the sheet closer to ``box`` than ``gap`` mm (a text keeps one text height from every other item)."""
    near = pl._inflate(box, gap - 0.05)
    found = []
    for item in fixed:
        if item.fixed_box and pl._overlap(near, item.fixed_box):
            found.append(f"text of {item.id}")
        found += [f"a line of {item.id}" for s in item.fixed_segments if pl._seg_rect(s, near)]
    return found


# --- the clean plate (A4: every side of the part is closed by dimensions) ------------------------------------------------


def test_no_leader_of_the_clean_plate_crosses_a_dimension_line_an_extension_line_or_another_leader() -> None:
    result, fixed = plan()
    assert result.items, "something is placed"
    assert crossings(result, fixed) == []


def test_a_callout_with_no_way_out_that_crosses_nothing_is_reported_not_drawn_across_a_dimension() -> None:
    result, fixed = plan()
    placed = {p.id for p in result.items}
    stuck = {u.id for u in result.unplaced}
    assert placed | stuck == {"rq@0", "rq@1", "rq@2"}
    assert stuck, "on this sheet the two callouts of the inner holes have no way out that crosses no dimension"
    assert all("no free place" in u.reason for u in result.unplaced)
    assert crossings(result, fixed) == []


def test_the_text_of_the_callout_to_the_corner_hole_keeps_a_text_height_from_the_dimensions() -> None:
    """The single line of the callout, as the glyphs fill it (the text touched the lower end of the 60 before)."""
    result, fixed = plan(glyph_height=True)
    box = next(p.text_box for p in result.items if p.id == "rq@0")
    assert too_close(box, fixed) == []


def test_the_overall_dimensions_two_steps_out_leave_the_room_and_every_callout_is_placed_clean() -> None:
    result, fixed = plan(lift=14.0)
    assert not result.unplaced, result.unplaced
    assert {p.id for p in result.items} == {"rq@0", "rq@1", "rq@2"}
    assert crossings(result, fixed) == []
    for p in result.items:
        assert too_close(p.text_box, fixed, TEXT_HEIGHT / 3.5) == [], p.id  # at least a mm from everything


def test_a_callout_that_found_no_place_chooses_first_the_next_time() -> None:
    """The callout to the top hole takes the room the one to the counterbore needs when it chooses first: the planner runs again with
    the stuck ones first."""
    result, _ = plan(lift=14.0, leaders=("rq@1", "rq@2"))
    assert not result.unplaced, result.unplaced


# --- a closed square of dimension lines round a view ---------------------------------------------------------------------


RING = [(74.0, 74.0, 186.0, 74.0), (186.0, 74.0, 186.0, 136.0), (186.0, 136.0, 74.0, 136.0), (74.0, 136.0, 74.0, 74.0)]


def boxed_in(inside: bool) -> pl.Plan:
    sheet = pl.Sheet((297.0, 210.0), (10.0, 10.0, 287.0, 200.0))
    ring = list(RING)
    walls = pl.Item("walls", "callout", None, fixed_segments=tuple(ring), fixed_kinds=("dim",) * 4)
    view = pl.View("front", 100.0, 50.0, at=(80.0, 80.0), outline=((0.0, 0.0, 100.0, 0.0), (100.0, 0.0, 100.0, 50.0), (100.0, 50.0, 0.0, 50.0), (0.0, 50.0, 0.0, 0.0)))
    hole = pl.Item("h", "leader", "front", 30.0, 8.7, ((30.0, 25.0),), extra=(30.0, 25.0, 4.0), inside=inside)
    return pl.plan_layout(sheet, [view], [walls, hole])


def test_a_leader_inside_a_ring_of_dimension_lines_is_not_drawn_across_it() -> None:
    result = boxed_in(inside=False)
    assert not result.items and [u.id for u in result.unplaced] == ["h"]
    assert "leader across" in result.unplaced[0].reason


def test_a_hole_callout_stands_inside_its_view_on_a_clear_area_when_every_way_out_is_closed() -> None:
    result = boxed_in(inside=True)
    assert not result.unplaced, result.unplaced
    placed = result.items[0]
    ex, ey = placed.data[0], placed.data[1]
    assert 80.0 < ex < 180.0 and 80.0 < ey < 130.0, "the elbow is inside the view"
    assert not any(pl._cross(s, w) for s in placed.segments for w in RING)


# --- the notes block -----------------------------------------------------------------------------------------------------


def test_the_notes_block_keeps_a_text_height_from_the_end_of_a_dimension_line() -> None:
    sheet = pl.Sheet((297.0, 210.0), (10.0, 10.0, 287.0, 200.0))
    # the block would stand at the lower left corner of the area; a dimension line ends 1.5 mm from where it would end
    line = pl.Item("d", "callout", None, fixed_segments=((46.5, 150.0, 46.5, 195.0),), fixed_kinds=("dim",))
    notes = [pl.Item("n1", "note", None, 30.0, 8.2, order=1), pl.Item("n2", "note", None, 30.0, 8.2, order=2)]
    view = pl.View("front", 60.0, 40.0, at=(120.0, 60.0))
    result = pl.plan_layout(sheet, [view], [line, *notes])
    assert not result.unplaced, result.unplaced
    boxes = [p.text_box for p in result.items if p.kind == "note"]
    assert len(boxes) == 2
    for box in boxes:
        assert too_close(box, [line]) == [], box
