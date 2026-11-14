# ruff: noqa: E501
"""Steps mode stacks the dimension lines outward in the order the items are drawn: the distance dimensions are drawn shortest
first, whatever order the agent listed them in (a 340 added before a 260 used to stand nearer the part and have the 260's
extension lines across its line). No FreeCAD."""

import sys
from pathlib import Path

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import items_dims  # noqa: E402
import pytest  # noqa: E402
import sheet_items  # noqa: E402

SPANS = {"l340": 340.0, "l260": 260.0, "l100": 100.0, "kw": 25.0}


@pytest.fixture(autouse=True)
def measured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(items_dims, "distance_span", lambda _ctx, spec: SPANS.get(spec["id"]))


def dist(item_id: str, **extra: object) -> dict[str, object]:
    return {"id": item_id, "type": "dim", "kind": "distance", **extra}


def ids(items: list[dict[str, object]], order: list[int]) -> list[object]:
    return [items[i]["id"] for i in order]


def test_distance_dimensions_are_drawn_shortest_first() -> None:
    items = [dist("l340"), dist("l260"), dist("l100")]
    assert ids(items, sheet_items.shortest_first(None, items, [0, 1, 2], lambda _i: 0)) == ["l100", "l260", "l340"]  # type: ignore[arg-type]


def test_other_items_and_dimensions_with_a_hand_placed_offset_stay_where_they_were() -> None:
    items = [dist("l340"), {"id": "n1", "type": "note", "text": "x"}, dist("l260"), {"id": "d1", "type": "dim", "kind": "diameter"}, dist("kw", offset_mm=20)]
    out = sheet_items.shortest_first(None, items, [0, 1, 2, 3, 4], lambda _i: 0)  # type: ignore[arg-type]
    assert ids(items, out) == ["l260", "n1", "l340", "d1", "kw"]


def test_dimensions_move_only_inside_their_own_phase() -> None:
    items = [dist("l340"), dist("l100"), dist("l260")]
    phases = {0: 0, 1: 5, 2: 0}  # l100 is on a section view: drawn after the sections
    out = sheet_items.shortest_first(None, items, [0, 2, 1], lambda i: phases[i])  # type: ignore[arg-type]
    assert ids(items, out) == ["l260", "l340", "l100"]


def test_a_nudged_line_moves_out_with_every_line_drawn_after_it_never_past_a_longer_one() -> None:
    """The placement pass nudges a line that collides farther out: the lines drawn after it on that side move with it, so the longer
    one (drawn last) never ends up nearer the part than the shorter ones (the 125 inside the 168 of a real shaft sheet)."""
    import sheet_context as sc  # noqa: PLC0415
    import sheet_model as model  # noqa: PLC0415

    ctx = sc.Context(sheet=model.Sheet(420.0, 297.0), views={}, holes=[], geometry=sc.Geometry(), area=(20.0, 40.0, 410.0, 287.0))
    ctx.extras["nudges"] = {"b": 1}
    distances = []
    for item in ("a", "b", "c"):
        ctx.extras["current"] = item
        distances.append(ctx.stack_distance("front", "below", None))
    assert distances == sorted(distances) and len(set(distances)) == 3
    assert distances[2] - distances[1] == sc.STACK_STEP_MM  # c stands one step beyond b, not back on the slot b left
    assert distances[1] - distances[0] == 2 * sc.STACK_STEP_MM  # b was nudged one slot out


def test_a_dimension_that_cannot_be_measured_keeps_its_place() -> None:
    items = [dist("l340"), dist("ghost"), dist("l260")]
    out = sheet_items.shortest_first(None, items, [0, 1, 2], lambda _i: 0)  # type: ignore[arg-type]
    assert ids(items, out) == ["l260", "ghost", "l340"]
