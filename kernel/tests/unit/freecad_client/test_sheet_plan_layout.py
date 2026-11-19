# ruff: noqa: E501
"""The global layout planner on the failures seen on real sheets (no FreeCAD): a callout cramped at a thread tip, crowded
callouts, a dimension line through the notes, a datum on top of location dimensions, leaders across the part."""

import math
import sys
import time
from pathlib import Path

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import sheet_plan_layout as pl  # noqa: E402

SHEET = pl.Sheet(paper=(420.0, 297.0), frame=(20.0, 10.0, 410.0, 287.0), title_block=(230.0, 229.0, 410.0, 287.0))


def seg_hits(s: pl.Segment, r: pl.Rect) -> bool:
    return pl._seg_rect(s, pl._inflate(r, -0.25))


def assert_clean(plan: pl.Plan, sheet: pl.Sheet = SHEET) -> None:
    """No text over text, no line through a text, no leader crossing a leader, nothing on the title block or a view."""
    assert not plan.unplaced, plan.unplaced
    boxes = {p.id: p.text_box for p in plan.items}
    ids = sorted(boxes)
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            assert not pl._overlap(boxes[a], boxes[b]), f"text of {a} over text of {b}"
    by = {p.id: p for p in plan.items}
    for p in plan.items:
        assert pl._inside(p.text_box, sheet.frame), p.id
        if sheet.title_block:
            assert not pl._overlap(p.text_box, sheet.title_block), f"{p.id} on the title block"
        for v in plan.views:
            assert not pl._overlap(p.text_box, (v.x, v.y, v.x + v.w, v.y + v.h)), f"{p.id} over view {v.id}"
        for o in plan.items:
            if o.id != p.id:
                for s in o.segments:
                    assert not seg_hits(s, p.text_box), f"a line of {o.id} through the text of {p.id}"
    leaders = [(p.id, s) for p in plan.items if p.kind in ("callout", "balloon") for s in p.segments]
    for i, (a, sa) in enumerate(leaders):
        for b, sb in leaders[i + 1 :]:
            assert not pl._cross(sa, sb), f"leaders of {a} and {b} cross"
    assert by or not plan.items


def view_box(plan: pl.Plan, vid: str) -> pl.Rect:
    v = next(v for v in plan.views if v.id == vid)
    return (v.x, v.y, v.x + v.w, v.y + v.h)


def plate_views() -> list[pl.View]:
    return [pl.View("front", 100.0, 60.0), pl.View("top", 100.0, 40.0, relation=("front", "below"))]


def plate_items() -> list[pl.Item]:
    holes = [(15.0, 15.0), (85.0, 15.0), (15.0, 45.0), (85.0, 45.0)]
    items = [
        pl.Item("w", "dim", "front", 9.0, 3.5, ((0.0, 60.0), (100.0, 60.0)), axis="h"),
        pl.Item("h", "dim", "front", 9.0, 3.5, ((100.0, 0.0), (100.0, 60.0)), axis="v"),
        pl.Item("x1", "dim", "front", 6.0, 3.5, ((0.0, 15.0), (15.0, 15.0)), axis="h", sides=("top",)),
        pl.Item("x2", "dim", "front", 6.0, 3.5, ((0.0, 15.0), (85.0, 15.0)), axis="h", sides=("top",)),
        pl.Item("y1", "dim", "front", 6.0, 3.5, ((15.0, 0.0), (15.0, 15.0)), axis="v", sides=("left",)),
        pl.Item("y2", "dim", "front", 6.0, 3.5, ((15.0, 0.0), (15.0, 45.0)), axis="v", sides=("left",)),
        pl.Item("datA", "datum", "front", 7.0, 7.0, ((0.0, 30.0),), normal=(-1.0, 0.0), slide=14.0),
        pl.Item("hole", "callout", "front", 22.0, 3.5, (holes[1],), sides=("right",)),
        pl.Item("n1", "note", None, 60.0, 3.5, order=1),
        pl.Item("n2", "note", None, 48.0, 3.5, order=2),
        pl.Item("n3", "note", None, 70.0, 3.5, order=3),
        pl.Item("ra", "surface", None, 14.0, 10.0),
    ]
    return items


def test_clean_plate_is_laid_out_without_unplaced_items_or_overlaps() -> None:
    plan = pl.plan_layout(SHEET, plate_views(), plate_items())
    assert plan.fits
    assert_clean(plan)
    assert {p.id for p in plan.items} == {i.id for i in plate_items()}


def test_datum_on_the_clean_plate_is_not_on_a_location_dimension() -> None:
    """The datum on the left face used to land on the extension lines of the location dimensions (datum_on_line)."""
    plan = pl.plan_layout(SHEET, plate_views(), plate_items())
    datum = next(p for p in plan.items if p.id == "datA")
    for other in plan.items:
        if other.id == "datA":
            continue
        for s in other.segments:
            assert not seg_hits(s, datum.text_box)
            for ds in datum.segments:
                assert pl._collinear_overlap(ds, s) <= 0.5, f"stem of the datum on a line of {other.id}"


def test_datum_with_no_room_to_slide_goes_past_the_stack_and_never_over_a_dimension() -> None:
    items = [
        pl.Item("y1", "dim", "front", 6.0, 3.5, ((0.0, 5.0), (0.0, 30.0)), axis="v", sides=("left",)),
        pl.Item("y2", "dim", "front", 6.0, 3.5, ((0.0, 5.0), (0.0, 55.0)), axis="v", sides=("left",)),
        pl.Item("datA", "datum", "front", 7.0, 7.0, ((0.0, 42.0),), normal=(-1.0, 0.0)),
    ]
    plan = pl.plan_layout(SHEET, plate_views(), items)
    assert_clean(plan)
    datum = next(p for p in plan.items if p.id == "datA")
    for other in plan.items:
        if other.id != "datA":
            for s in other.segments:
                for ds in datum.segments:
                    assert pl._collinear_overlap(ds, s) <= 0.5


def test_dimensions_are_stacked_shorter_inside_at_iso_distances() -> None:
    plan = pl.plan_layout(SHEET, plate_views(), plate_items())
    top = [p for p in plan.items if p.kind == "dim" and p.side == "top" and p.view == "front"]
    by_id = {p.id: p for p in top}
    assert by_id["x1"].level < by_id["x2"].level
    left = {p.id: p for p in plan.items if p.kind == "dim" and p.side == "left"}
    assert left["y1"].level < left["y2"].level
    box = view_box(plan, "front")
    line = next(s for s in by_id["x1"].segments if s[1] == s[3] and s[0] != s[2] and abs(s[2] - s[0]) < 20)
    assert abs((box[1] - line[1]) - pl.DIM_FIRST_MM) < 0.01
    line2 = next(s for s in by_id["x2"].segments if s[1] == s[3] and abs(s[2] - s[0]) > 60)
    assert (box[1] - line2[1]) - (box[1] - line[1]) >= pl.DIM_STEP_MM - 0.01 or by_id["x2"].level > by_id["x1"].level


def test_notes_form_one_block_in_number_order_and_no_dimension_runs_through_it() -> None:
    items = plate_items() + [pl.Item(f"m{i}", "note", None, 55.0, 3.5, order=10 + i) for i in range(6)]
    plan = pl.plan_layout(SHEET, plate_views(), items)
    assert_clean(plan)
    notes = sorted((p for p in plan.items if p.kind == "note"), key=lambda p: p.text_box[1])
    assert [n.id for n in notes] == ["n1", "n2", "n3", "m0", "m1", "m2", "m3", "m4", "m5"]
    assert len({round(n.text_box[0], 3) for n in notes}) == 1
    block = (notes[0].text_box[0], notes[0].text_box[1], notes[0].text_box[0] + 70.0, notes[-1].text_box[3])
    for p in plan.items:
        if p.kind != "note":
            assert not any(seg_hits(s, block) for s in p.segments), f"{p.id} through the notes"


def test_crowded_callouts_on_one_edge_do_not_overlap_or_cross() -> None:
    items = [pl.Item(f"c{i}", "callout", "front", 24.0, 3.5, ((100.0, 5.0 + 9.0 * i),)) for i in range(6)]
    plan = pl.plan_layout(SHEET, plate_views(), items)
    assert_clean(plan)
    boxes = sorted((p.text_box for p in plan.items), key=lambda b: b[1])
    assert len(boxes) == 6


def test_thread_callout_at_the_tip_is_not_cramped() -> None:
    """A thread callout used to sit hard against the tip of the shaft."""
    views = [pl.View("front", 120.0, 30.0)]
    items = [
        pl.Item("thr", "callout", "front", 20.0, 3.5, ((120.0, 15.0),), sides=("right", "top")),
        pl.Item("d", "dim", "front", 8.0, 3.5, ((0.0, 0.0), (0.0, 30.0)), axis="v", sides=("right",)),
        pl.Item("l", "dim", "front", 8.0, 3.5, ((0.0, 0.0), (120.0, 0.0)), axis="h", sides=("top",)),
    ]
    plan = pl.plan_layout(SHEET, views, items)
    assert_clean(plan)
    box, thr = view_box(plan, "front"), next(p for p in plan.items if p.id == "thr")
    gap = (
        min(thr.text_box[0] - box[2], box[0] - thr.text_box[2])
        if thr.side in ("right", "left")
        else thr.text_box[1] - box[3]
    )
    if thr.side == "top":
        gap = box[1] - thr.text_box[3]
    assert gap >= pl.LEADER_MIN_MM - 0.01


def test_leaders_do_not_cross_the_part() -> None:
    outline = ((0.0, 0.0, 100.0, 0.0), (100.0, 0.0, 100.0, 60.0), (100.0, 60.0, 0.0, 60.0), (0.0, 60.0, 0.0, 0.0))
    views = [pl.View("front", 100.0, 60.0, outline=outline)]
    items = [
        pl.Item("l1", "callout", "front", 20.0, 3.5, ((0.0, 10.0),)),
        pl.Item("r1", "callout", "front", 20.0, 3.5, ((100.0, 50.0),)),
        pl.Item("t1", "callout", "front", 20.0, 3.5, ((50.0, 0.0),)),
        pl.Item("b1", "callout", "front", 20.0, 3.5, ((50.0, 60.0),)),
    ]
    plan = pl.plan_layout(SHEET, views, items)
    assert_clean(plan)
    box = view_box(plan, "front")
    for p in plan.items:
        (ax, ay, bx, by) = p.segments[0]
        inside = pl._inside_len(p.segments[0], pl._inflate(box, -0.5))
        assert inside < 0.5, f"leader of {p.id} runs {inside:.1f} mm through the part"


def test_leader_never_crosses_a_dimension_text() -> None:
    items = plate_items()
    plan = pl.plan_layout(SHEET, plate_views(), items)
    assert_clean(plan)


def test_a_sheet_of_60_items_is_laid_out_in_well_under_a_second() -> None:
    views = [
        pl.View("front", 140.0, 80.0),
        pl.View("top", 140.0, 50.0, relation=("front", "below")),
        pl.View("right", 50.0, 80.0, relation=("front", "left")),
        pl.View("iso", 70.0, 60.0),
    ]
    items = []
    for i in range(12):  # a chain of dimensions along the top, one down the left, and some overall ones
        items.append(
            pl.Item(
                f"d{i}",
                "dim",
                "front",
                6.0,
                3.5,
                ((10.0 * i + 5.0, 10.0), (10.0 * i + 15.0, 10.0)),
                axis="h",
                sides=("top",),
            )
        )
        items.append(
            pl.Item(
                f"v{i}",
                "dim",
                "front",
                6.0,
                3.5,
                ((10.0, 6.0 * i + 4.0), (10.0, 6.0 * i + 10.0)),
                axis="v",
                sides=("left",),
            )
        )
    for i in range(6):
        items.append(
            pl.Item(
                f"o{i}",
                "dim",
                "front",
                9.0,
                3.5,
                ((5.0, 10.0), (5.0 + 20.0 * (i + 1), 10.0)),
                axis="h",
                sides=("bottom",),
            )
        )
    for i in range(14):
        items.append(pl.Item(f"c{i}", "callout", "front", 22.0, 3.5, ((5.0 + 9 * i, 6.0 + 5.0 * i),)))
    for i in range(6):
        items.append(pl.Item(f"b{i}", "balloon", "top", 8.0, 8.0, ((10.0 + 20 * i, 25.0),)))
    for i in range(4):
        items.append(
            pl.Item(f"dt{i}", "datum", "right", 7.0, 7.0, ((0.0, 10.0 + 15 * i),), normal=(-1.0, 0.0), slide=10.0)
        )
    for i in range(4):
        items.append(pl.Item(f"n{i}", "note", None, 60.0, 3.5, order=i))
    items.append(pl.Item("sec", "section_label", "right", 10.0, 4.0))
    items.append(pl.Item("ra", "surface", None, 14.0, 10.0))
    assert len(items) >= 60
    t0 = time.perf_counter()
    plan = pl.plan_layout(SHEET, views, items)
    elapsed = time.perf_counter() - t0
    assert elapsed < 1.0, f"{elapsed:.2f} s"
    placed = {p.id for p in plan.items} | {u.id for u in plan.unplaced}
    assert placed == {i.id for i in items}, "every item is either placed or reported"
    assert len(plan.unplaced) <= 4, plan.unplaced
    leaders = [(p.id, s) for p in plan.items if p.kind in ("callout", "balloon") for s in p.segments]
    texts = {p.id: p.text_box for p in plan.items}
    ids = sorted(texts)
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            assert not pl._overlap(texts[a], texts[b]), (a, b)
    crossing = [(a, b) for i, (a, sa) in enumerate(leaders) for b, sb in leaders[i + 1 :] if pl._cross(sa, sb)]
    assert not crossing, crossing


def test_what_cannot_be_placed_is_reported_with_the_reason() -> None:
    items = [pl.Item(f"n{i}", "note", None, 380.0, 3.5, order=i) for i in range(80)]
    plan = pl.plan_layout(SHEET, plate_views(), items)
    assert plan.unplaced
    assert all(u.reason for u in plan.unplaced)
    assert any("notes block" in u.reason for u in plan.unplaced)


def test_a_dimension_with_no_free_side_is_reported() -> None:
    boxed = pl.Sheet(
        paper=(420.0, 297.0),
        frame=(20.0, 10.0, 410.0, 287.0),
        title_block=(230.0, 229.0, 410.0, 287.0),
        area=(25.0, 15.0, 400.0, 220.0),
    )
    items = [pl.Item("d", "dim", "front", 9.0, 3.5, ((0.0, 0.0), (100.0, 0.0)), axis="h", fixed_box=None)]
    other = [pl.Item("blk", "note", None, 9.0, 3.5, order=1, fixed_box=(0.0, 0.0, 420.0, 297.0))]
    plan = pl.plan_layout(boxed, plate_views(), items + other)
    assert any(u.id == "d" for u in plan.unplaced)


def test_views_that_do_not_fit_are_reported() -> None:
    plan = pl.plan_layout(SHEET, [pl.View("front", 500.0, 60.0)], [])
    assert not plan.fits
    assert plan.needed[0] >= 500.0


def test_hand_placed_items_are_kept_as_obstacles() -> None:
    pinned = pl.Item("hand", "note", None, 40.0, 3.5, fixed_box=(150.0, 90.0, 190.0, 93.5))
    plan = pl.plan_layout(
        SHEET,
        [pl.View("front", 100.0, 60.0)],
        [pinned, pl.Item("x", "dim", "front", 9.0, 3.5, ((0.0, 0.0), (100.0, 0.0)), axis="h")],
    )
    assert not plan.unplaced
    assert all(p.id != "hand" for p in plan.items)
    d = next(p for p in plan.items if p.id == "x")
    assert not pl._overlap(d.text_box, pinned.fixed_box or (0, 0, 0, 0))


def test_the_plan_is_deterministic() -> None:
    a = pl.plan_layout(SHEET, plate_views(), plate_items())
    b = pl.plan_layout(SHEET, plate_views(), plate_items())
    assert a == b


def test_free_view_takes_the_room_that_is_left_and_not_the_projected_views() -> None:
    views = [*plate_views(), pl.View("iso", 70.0, 60.0)]
    plan = pl.plan_layout(SHEET, views, [])
    boxes = [view_box(plan, v) for v in ("front", "top", "iso")]
    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            assert not pl._overlap(a, b)
    assert math.isfinite(boxes[2][0])


def test_a_datum_on_the_very_line_of_an_extension_line_with_no_slide_is_reported() -> None:
    items = [
        pl.Item("y1", "dim", "front", 6.0, 3.5, ((0.0, 5.0), (0.0, 30.0)), axis="v", sides=("left",)),
        pl.Item("datA", "datum", "front", 7.0, 7.0, ((0.0, 5.0),), normal=(-1.0, 0.0)),
    ]
    plan = pl.plan_layout(SHEET, plate_views(), items)
    assert [u.id for u in plan.unplaced] == ["datA"]
    assert "line" in plan.unplaced[0].reason


# --- every annotation is placed: leaders with a shelf, angular dimensions, weld symbols, datums -------------------------


def one_view(box: tuple[float, float] = (100.0, 60.0)) -> list[pl.View]:
    return [pl.View("front", *box, at=(150.0, 100.0))]


def test_a_leader_has_an_oblique_line_and_a_horizontal_shelf_at_an_iso_angle() -> None:
    item = pl.Item("t", "leader", "front", 28.0, 5.0, ((20.0, 20.0),))
    plan = pl.plan_layout(SHEET, one_view(), [item])
    assert not plan.unplaced, plan.unplaced
    placed = plan.items[0]
    leader, shelf = placed.segments
    assert shelf[1] == shelf[3] and abs(shelf[2] - shelf[0]) == 28.0  # the shelf the text stands on
    assert (shelf[0], shelf[1]) == (leader[2], leader[3]) == placed.data[:2]  # it starts where the leader ends (the elbow, then the tip)
    angle = abs(math.degrees(math.atan2(leader[3] - leader[1], leader[2] - leader[0]))) % 90
    assert 10.0 < angle < 80.0  # never along a centre line or a dimension line (ISO 128-22)
    assert placed.text_box[3] == shelf[1] and placed.text_box[1] == shelf[1] - 5.0


def test_two_leaders_to_nearby_holes_do_not_cross_and_their_text_does_not_overlap() -> None:
    items = [pl.Item(f"t{i}", "leader", "front", 26.0, 8.0, ((20.0 + 6 * i, 15.0),)) for i in range(4)]
    plan = pl.plan_layout(SHEET, one_view(), items)
    assert not plan.unplaced, plan.unplaced
    leaders = [p.segments[0] for p in plan.items]
    for i, a in enumerate(leaders):
        for b in leaders[i + 1 :]:
            assert not pl._cross(a, b)
    boxes = [p.text_box for p in plan.items]
    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            assert not pl._overlap(a, b)


def test_an_angular_dimension_takes_the_smallest_arc_that_crosses_nothing() -> None:
    angle = pl.Item("a", "angle", "front", 9.0, 3.5, ((100.0, 60.0),), extra=(0.0, 60.0))  # the apex at the bottom right corner
    plan = pl.plan_layout(SHEET, one_view(), [angle])
    assert not plan.unplaced, plan.unplaced
    assert plan.items[0].data == (pl.ANGLE_RADII_MM[0],)
    blocker = pl.Item("n", "callout", None, fixed_box=(258.0, 148.0, 268.0, 152.0))
    again = pl.plan_layout(SHEET, one_view(), [blocker, angle])
    assert not again.unplaced, again.unplaced
    assert next(p for p in again.items if p.id == "a").data[0] > pl.ANGLE_RADII_MM[0]  # a note on the first arc pushes it out


def test_a_weld_symbol_stands_near_its_arrows_and_crosses_no_leader() -> None:
    views = [pl.View("front", 100.0, 60.0, at=(150.0, 100.0))]
    tips = ((10.0, 30.0), (10.0, 50.0))
    weld = pl.Item("w", "weld", "front", 0.0, 0.0, tips, extra=(20.0, 6.0, 6.0))
    plan = pl.plan_layout(SHEET, views, [weld])
    assert not plan.unplaced, plan.unplaced
    ax, ay = plan.items[0].data
    for tip in tips:
        assert math.hypot(150.0 + tip[0] - ax, 100.0 + tip[1] - ay) < 40.0  # short arrows
    # a leader already standing across the straight way out: the arrows go round it, they never cross it
    wall = pl.Item("l", "callout", None, fixed_segments=((140.0, 110.0, 140.0, 140.0),), fixed_kinds=("leader",))
    around = pl.plan_layout(SHEET, views, [wall, weld])
    assert not [u for u in around.unplaced if u.id == "w"], around.unplaced
    placed = next(p for p in around.items if p.id == "w")
    for seg in placed.segments:
        if seg[2:] == placed.data or seg[:2] == placed.data:
            assert not pl._cross(seg, (140.0, 110.0, 140.0, 140.0)), seg


def test_a_weld_with_no_place_without_an_arrow_across_a_leader_is_reported() -> None:
    views = [pl.View("front", 100.0, 60.0, at=(150.0, 100.0))]
    ring = tuple(
        (math.cos((a * 22.5 + 3.0) * math.pi / 180) * 7 + 160.0, math.sin((a * 22.5 + 3.0) * math.pi / 180) * 7 + 130.0) for a in range(16)
    )
    leaders = tuple((ring[i][0], ring[i][1], ring[(i + 1) % 16][0], ring[(i + 1) % 16][1]) for i in range(16))
    fence = pl.Item("f", "callout", None, fixed_segments=leaders, fixed_kinds=("leader",) * 16)
    weld = pl.Item("w", "weld", "front", 0.0, 0.0, ((10.0, 30.0),), extra=(20.0, 6.0, 6.0))  # its tip stands inside the closed ring
    plan = pl.plan_layout(SHEET, [pl.View("front", 100.0, 60.0, at=(150.0, 100.0))], [fence, weld])
    assert [u.id for u in plan.unplaced] == ["w"] and "across a leader" in plan.unplaced[0].reason


def test_a_datum_symbol_is_placed_by_the_planner_with_its_stem() -> None:
    datum = pl.Item("d", "datum", "front", 7.0, 7.0, ((50.0, 60.0),), normal=(0.0, 1.0))
    plan = pl.plan_layout(SHEET, one_view(), [datum])
    assert not plan.unplaced, plan.unplaced
    placed = plan.items[0]
    assert placed.side == "bottom" and placed.data[0] >= pl.STEM_MIN_MM


def test_the_leader_of_a_hole_points_at_its_centre_whatever_the_angle() -> None:
    item = pl.Item("h", "leader", "front", 26.0, 8.0, ((50.0, 15.0),), extra=(50.0, 20.0, 5.0))  # a hole of radius 5 at (50, 20)
    plan = pl.plan_layout(SHEET, one_view(), [item])
    assert not plan.unplaced, plan.unplaced
    placed = plan.items[0]
    ex, ey, tx, ty = placed.data
    cx, cy = 150.0 + 50.0, 100.0 + 20.0
    assert abs(math.hypot(tx - cx, ty - cy) - 5.0) < 1e-6
    assert abs((tx - cx) * (ey - ty) - (ty - cy) * (ex - tx)) < 1e-6 * 100  # tip, centre and elbow on one line


def test_a_planned_sheet_with_every_kind_of_item_is_clean_and_fast() -> None:
    items = [
        pl.Item("w1", "dim", "front", 12.0, 3.5, ((0.0, 60.0), (100.0, 60.0)), "h"),
        pl.Item("a1", "angle", "front", 9.0, 3.5, ((0.0, 0.0),), extra=(300.0, 360.0)),
        pl.Item("l1", "leader", "front", 28.0, 8.0, ((30.0, 30.0),)),
        pl.Item("l2", "leader", "front", 28.0, 8.0, ((70.0, 30.0),)),
        pl.Item("d1", "datum", "front", 7.0, 7.0, ((50.0, 0.0),), normal=(0.0, -1.0)),
        pl.Item("s1", "weld", "front", 0.0, 0.0, ((100.0, 20.0), (100.0, 40.0)), extra=(20.0, 6.0, 6.0)),
    ]
    start = time.perf_counter()
    plan = pl.plan_layout(SHEET, one_view(), items)
    assert time.perf_counter() - start < 2.0
    assert not plan.unplaced, plan.unplaced
    assert pl.plan_layout(SHEET, one_view(), items) == plan  # deterministic


def test_a_weld_symbol_stands_next_to_the_view_of_its_joint_and_its_arrows_never_enter_another_view() -> None:
    """The frame: the end view at the left, the side view at the right of it, the joint at the left edge of the side view. The
    reference line goes in the gap beside the side view; the arrows cross nothing but the gap."""
    end_view, side_view = (54.6, 68.4, 122.6, 136.4), (162.6, 68.3, 243.8, 136.3)
    views = [pl.View("end", 68.0, 68.0, at=end_view[:2]), pl.View("side", 81.2, 68.0, at=side_view[:2])]
    tips = ((1.2, 5.0), (1.2, 62.0))  # on the plate seam at the left edge of the side view, local mm
    weld = pl.Item("w", "weld", "side", 0.0, 0.0, tips, extra=(24.8, 5.5, 5.5))
    plan = pl.plan_layout(SHEET, views, [weld])
    assert not plan.unplaced, plan.unplaced
    placed = plan.items[0]
    ax, ay = placed.data
    assert end_view[2] < ax < side_view[0]  # the junction is in the gap between the two views, next to the one that has the joint
    for view in (end_view, side_view):
        assert not pl._overlap(placed.text_box, view), "the reference line is over a view"
    for seg in placed.segments:
        assert not seg_hits(seg, end_view), f"an arrow runs into the other view: {seg}"
    assert max(math.dist((side_view[0] + t[0], side_view[1] + t[1]), (ax, ay)) for t in tips) < 70.0


def test_a_leader_to_a_hole_in_a_row_of_holes_goes_round_the_others() -> None:
    holes = [(20.0 + 15.0 * i, 30.0) for i in range(5)]  # five holes of radius 5 in a row
    outline = [(0.0, 0.0, 100.0, 0.0), (100.0, 0.0, 100.0, 60.0), (100.0, 60.0, 0.0, 60.0), (0.0, 60.0, 0.0, 0.0)]
    rings = []
    for cx, cy in holes:
        pts = [(cx + 5.0 * math.cos(2 * math.pi * k / 24), cy + 5.0 * math.sin(2 * math.pi * k / 24)) for k in range(25)]
        rings += [(a[0], a[1], b[0], b[1]) for a, b in zip(pts, pts[1:], strict=False)]
    view = pl.View("front", 100.0, 60.0, at=(150.0, 100.0), outline=tuple(outline + rings))
    target = holes[2]
    item = pl.Item("h", "leader", "front", 26.0, 8.0, ((target[0], target[1] - 5.0),), extra=(target[0], target[1], 5.0))
    plan = pl.plan_layout(SHEET, [view], [item])
    assert not plan.unplaced, plan.unplaced
    leader = plan.items[0].segments[0]
    for cx, cy in holes:
        if (cx, cy) == target:
            continue
        # the leader (page mm) keeps out of the circle of every other hole of the row
        steps = [(leader[0] + (leader[2] - leader[0]) * t / 40, leader[1] + (leader[3] - leader[1]) * t / 40) for t in range(41)]
        assert min(math.dist(p, (150.0 + cx, 100.0 + cy)) for p in steps) >= 5.0, (cx, leader)
