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
