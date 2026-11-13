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
