# ruff: noqa: E501
"""What the default composition must not emit (the owner's rules read off real bench sheets): the overall beside a chain that already
reaches both ends, and the messages of the findings that name the tools the drafter has (no FreeCAD)."""

import sys
from pathlib import Path
from typing import Any

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import items_dims  # noqa: E402
import sheet_review as review  # noqa: E402
import sheet_viewchecks as viewchecks  # noqa: E402

from .test_sheet_turned import ring_context  # noqa: E402

# --- the overall is not given when the others chain both ends ----------------------------------------------------------------


def _chain_context(spans: list[tuple[float, float, str | None]], monkeypatch: Any) -> Any:
    """The context of a 168 long part along x whose ``dim`` items measure ``spans`` (lo, hi, view)."""
    ctx = ring_context()
    ctx.extras["model_box"] = ((0.0, 0.0, 0.0), (168.0, 32.0, 32.0))
    ctx.extras["items"] = [
        {"id": f"d{n}", "type": "dim", "kind": "distance", "from": {"at": [lo, 0, 0]}, "to": {"at": [hi, 0, 0]}, "along": "x", **({"view": view} if view else {})}
        for n, (lo, hi, view) in enumerate(spans)
    ]

    def resolve(_ctx: Any, ref: dict[str, Any]) -> items_dims.Ref:
        return items_dims.Ref("point", tuple(ref["at"]), "p")  # type: ignore[arg-type]

    monkeypatch.setattr(items_dims, "resolve_ref", resolve)
    return ctx


def test_the_overall_is_left_out_when_a_baseline_reaches_both_ends(monkeypatch: Any) -> None:
    """A datum baseline from the shoulder at 43 gives 43 (to the left end) and 125 (to the right one): 168 is their sum, a dimension the
    shop gets twice (the owner's shaft: ``o1`` beside the baseline)."""
    ctx = _chain_context([(0.0, 43.0, None), (43.0, 168.0, None)], monkeypatch)
    assert items_dims.given_by_dimensions(ctx, "x", "front")


def test_the_overall_stays_when_the_dimensions_do_not_reach_both_ends(monkeypatch: Any) -> None:
    assert not items_dims.given_by_dimensions(_chain_context([(0.0, 43.0, None), (43.0, 103.0, None)], monkeypatch), "x", "front")
    assert not items_dims.given_by_dimensions(_chain_context([], monkeypatch), "x", "front")  # nothing else on the sheet: the overall IS the length
    other_view = _chain_context([(0.0, 43.0, "top"), (43.0, 168.0, "top")], monkeypatch)
    assert not items_dims.given_by_dimensions(other_view, "x", "front")  # a dimension of another view does not count
    assert not items_dims.given_by_dimensions(_chain_context([(0.0, 43.0, None), (43.0, 168.0, None)], monkeypatch), "y", "front")  # another axis


# --- the messages name calls that exist -----------------------------------------------------------------------------------------


def test_dimension_twice_names_the_remove_call_with_items_and_what_an_rq_item_needs() -> None:
    for language in ("es", "en"):
        ctx = ring_context()
        ctx.language = language
        ctx.extras["dim_records"] = [("a", "front", ("len", "x", 0.0, 80.0)), ("b", "front", ("len", "x", 0.0, 80.0))]
        (found,) = [f for f in viewchecks._dimension_twice(review.ReviewInput([], {}, ctx, ctx.sheet))]  # noqa: SLF001
        assert 'drawing(action="remove", items=[' in found.message and "ids=" not in found.message
        assert "rq_" in found.message and "reason=" in found.message and "update" in found.message  # the way out for an item of several dimensions


# --- an overall the sheet did not draw is not a size a chain closes on -------------------------------------------------------------------


def _plate_100(*dims: dict[str, Any]) -> tuple[Any, list[review.Finding]]:
    import sheet_items as items  # noqa: PLC0415

    from .test_sheet_views import views_context  # noqa: PLC0415

    ctx = views_context([])
    ctx.extras["model_box"] = ((0.0, 0.0, 0.0), (100.0, 60.0, 19.0))
    taken: set[str] = set()
    normalised = []
    for number, spec in enumerate(({"type": "overall", "id": "o1"}, *dims), start=1):
        done = items.normalise(spec, taken, number)
        taken.add(done["id"])
        normalised.append(done)
    ctx.extras["items"] = normalised  # what ``drawing`` hands every item: the items of the sheet
    results = items.draw_all(ctx, normalised)
    return ctx, review.review(review.ReviewInput(normalised, {r.id: r for r in results}, ctx, ctx.sheet, []))


def _x(item: str, a: float, b: float) -> dict[str, Any]:
    return {"type": "dim", "id": item, "kind": "distance", "from": {"at": [a, 30, 8]}, "to": {"at": [b, 30, 8]}, "along": "x", "view": "front"}


def test_d1_0_to_40_and_d2_40_to_100_leave_the_overall_100_out_and_no_chain_closes_on_it() -> None:
    ctx, found = _plate_100(_x("d1", 0, 40), _x("d2", 40, 100))
    assert ctx.extras.get("overall_skipped") == {"x"}  # the overall item did not draw the width: d1 and d2 chain both ends
    assert not [f for f in found if f.code in ("over_dimensioned", "duplicate_dimension", "dimension_redundant")], [(f.code, f.items) for f in found]


def test_an_overall_that_was_drawn_still_has_its_chain_flagged() -> None:
    ctx, found = _plate_100(_x("d1", 0, 40), _x("d2", 40, 90))  # 90: the two do not reach the far end, the overall 100 is drawn
    assert not ctx.extras.get("overall_skipped")
    assert not [f for f in found if f.code == "over_dimensioned"]  # 40 + 50 is not 100: nothing closes


def test_the_overall_is_never_left_out_of_an_assembly_or_a_weldment(monkeypatch: Any) -> None:
    ctx, _ = _plate_100(_x("d1", 0, 40), _x("d2", 40, 100))

    def resolve(_c: Any, ref: dict[str, Any]) -> items_dims.Ref:
        return items_dims.Ref("point", tuple(ref["at"]), "p")  # type: ignore[arg-type]

    monkeypatch.setattr(items_dims, "resolve_ref", resolve)
    assert items_dims.given_by_dimensions(ctx, "x", "front")
    ctx.assembly = object()
    assert not items_dims.given_by_dimensions(ctx, "x", "front")
    ctx.assembly, ctx.weldment = None, object()
    assert not items_dims.given_by_dimensions(ctx, "x", "front")


# --- the pitch of a row is not given beside the spacing that already ties its two ends ----------------------------------------------------------------


def _forest(spans: list[tuple[float, float]]) -> bool:
    parent: dict[float, float] = {}

    def find(k: float) -> float:
        parent.setdefault(k, k)
        while parent[k] != k:
            k = parent[k]
        return k

    for a, b in spans:
        ra, rb = find(a), find(b)
        if ra == rb:
            return False
        parent[ra] = rb
    return True


def test_a_row_at_one_pitch_beside_a_group_that_ties_its_ends_is_no_closed_loop() -> None:
    """Group B (Ø5) on x = 10 and 70 gives 60; group A (Ø6.6) at pitch 20 over the same 60 would give 3 x 20: a loop nobody reported."""
    from .test_sheet_items import hole  # noqa: PLC0415
    from .test_sheet_views import run_review, views_context  # noqa: PLC0415

    holes = [hole(0, 10, 40, 5.0), hole(1, 70, 40, 5.0), hole(2, 10, 20), hole(3, 30, 20), hole(4, 50, 20), hole(5, 70, 20)]
    ctx = views_context(holes)
    run_review(ctx, {"type": "overall", "id": "o1"}, {"type": "datum_dims", "id": "g", "auto": True})
    spans = [(a, b) for _item, axis, a, b in ctx.extras["position_spans"] if axis == "x"]
    assert spans and _forest(spans), spans
    assert not (any(b - a == 60.0 for a, b in spans) and any(b - a == 20.0 for a, b in spans) and len(spans) < 3), spans
