# ruff: noqa: E501
"""The stack of parallel dimensions the planner makes (no FreeCAD): lengths counted from one datum stand shortest nearest the
part and longest outside (ISO 129-1), the lines keep an even gap of at least ``MIN_PARALLEL_GAP_MM``, and a dimension placed
after a hand-placed one respects the order of the lines that are already there. Each of these failed on the planner that put
every dimension at the first free level, wherever the others stood."""

import sys
from pathlib import Path

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import sheet_plan_layout as pl  # noqa: E402

SHEET = pl.Sheet(paper=(420.0, 297.0), frame=(20.0, 10.0, 410.0, 287.0), title_block=(230.0, 229.0, 410.0, 287.0))
VIEW_AT = (60.0, 120.0)
VIEW = pl.View("front", 300.0, 40.0, at=VIEW_AT)
EDGE_TOP = VIEW_AT[1]


def dim(item_id: str, a: float, b: float, sides: tuple[str, ...] = ("top",)) -> pl.Item:
    return pl.Item(item_id, "dim", "front", 9.0, 3.5, ((a, 0.0), (b, 0.0)), axis="h", sides=sides)


def hand_dim(item_id: str, a: float, b: float, distance: float) -> pl.Item:
    """A dimension the author placed (it is not moved): its dimension line ``distance`` mm above the view, from ``a`` to ``b``."""
    y = EDGE_TOP - distance
    x0, x1 = VIEW_AT[0] + a, VIEW_AT[0] + b
    return pl.Item(
        item_id,
        "callout",
        None,
        fixed_box=((x0 + x1) / 2 - 4.5, y - 4.3, (x0 + x1) / 2 + 4.5, y - 0.8),
        fixed_segments=((x0, y, x1, y), (x0, EDGE_TOP - 1.0, x0, y - 2.0), (x1, EDGE_TOP - 1.0, x1, y - 2.0)),
        fixed_kinds=("dim", "ext", "ext"),
    )


def distance(plan: pl.Plan, item_id: str) -> float:
    """How far the dimension line of a placed dimension stands above the view."""
    placed = next(p for p in plan.items if p.id == item_id)
    return EDGE_TOP - placed.segments[0][1]


def test_lengths_from_one_datum_stand_shortest_nearest_with_an_even_gap() -> None:
    items = [dim("c", 0.0, 250.0), dim("a", 0.0, 100.0), dim("b", 0.0, 160.0)]  # listed longest first on purpose
    plan = pl.plan_layout(SHEET, [VIEW], items)
    assert not plan.unplaced, plan.unplaced
    a, b, c = (distance(plan, k) for k in "abc")
    assert a < b < c
    assert b - a >= pl.MIN_PARALLEL_GAP_MM and c - b >= pl.MIN_PARALLEL_GAP_MM
    assert abs((b - a) - (c - b)) < 0.01  # the gap is even


def test_a_longer_dimension_goes_outside_a_shorter_one_the_author_placed() -> None:
    """A hand-placed 100 mm dimension stands 24 mm out; the 250 mm one that runs along it must stand outside it, not at the
    first free level (which put its line under the 100 and its extension lines across it)."""
    plan = pl.plan_layout(SHEET, [VIEW], [hand_dim("hand", 0.0, 100.0, 24.0), dim("long", 0.0, 250.0)])
    assert not plan.unplaced, plan.unplaced
    assert distance(plan, "long") >= 24.0 + pl.MIN_PARALLEL_GAP_MM


def test_a_shorter_dimension_does_not_go_outside_a_longer_one_the_author_placed() -> None:
    """The hand-placed 250 mm dimension stands 10 mm out: a 100 mm one that runs along it cannot stand inside it on that side
    (no room), so it takes the other side rather than the wrong order."""
    plan = pl.plan_layout(SHEET, [VIEW], [hand_dim("hand", 0.0, 250.0, 10.0), dim("short", 0.0, 100.0, sides=("top", "bottom"))])
    assert not plan.unplaced, plan.unplaced
    placed = next(p for p in plan.items if p.id == "short")
    assert placed.side == "bottom"


def test_a_shorter_dimension_with_no_other_side_is_reported_never_placed_across_the_longer_one() -> None:
    plan = pl.plan_layout(SHEET, [VIEW], [hand_dim("hand", 0.0, 250.0, 10.0), dim("short", 0.0, 100.0)])
    assert [u.id for u in plan.unplaced] == ["short"]
    assert "stack order" in plan.unplaced[0].reason


def test_dimensions_with_no_common_run_share_a_level_as_a_chain_does() -> None:
    plan = pl.plan_layout(SHEET, [VIEW], [dim("a", 0.0, 100.0), dim("b", 100.0, 190.0)])
    assert not plan.unplaced, plan.unplaced
    assert distance(plan, "a") == distance(plan, "b")
