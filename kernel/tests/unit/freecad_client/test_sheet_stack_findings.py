# ruff: noqa: E501
"""The review holds a sheet whose parallel dimensions are stacked wrong (ISO 129-1), on synthetic data (no FreeCAD): a 260 drawn
outside a 340 (the long one inside the short one), two lines closer than the minimum, an extension line across another
dimension's line. Chains (dimensions that share no run) are never compared."""

import sys
from pathlib import Path

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import sheet_checks as checks_mod  # noqa: E402
import sheet_context as sc  # noqa: E402
import sheet_model as model  # noqa: E402
import sheet_plan_layout as pl  # noqa: E402
import sheet_review as review  # noqa: E402
import sheet_viewchecks as checks  # noqa: E402
import sheet_viewrules as vr  # noqa: E402

BOX = (0.0, 0.0, 400.0, 40.0)  # the long view of a shaft (page mm, y up)


def above(owner: str, x0: float, x1: float, distance: float, view: str = "front") -> vr.DimShape:
    """A dimension standing ``distance`` mm above the view, from ``x0`` to ``x1`` (vertical extension lines down to the part)."""
    line = BOX[3] + distance
    ext = tuple((x, BOX[3] + 1.0, x, line + 2.0) for x in (x0, x1))
    return vr.DimShape(owner, view, ext, line, x1 - x0, 9.0, True)


def stacks(*dims: vr.DimShape) -> list[tuple[str, str, str]]:
    return [(f.owner, f.other, f.kind) for f in vr.dimension_stacks(list(dims), {"front": BOX})]


def test_the_planner_and_the_review_use_one_minimum_gap() -> None:
    assert pl.MIN_PARALLEL_GAP_MM == vr.MIN_PARALLEL_GAP_MM
    assert pl.DIM_STEP_MM >= vr.MIN_PARALLEL_GAP_MM


def test_a_long_dimension_nearer_the_part_than_the_short_one_it_runs_along_is_an_inverted_stack() -> None:
    """The owner's sheet: 260 drawn outside 340 (here inside)."""
    assert stacks(above("l340", 0.0, 340.0, 10.0), above("l260", 0.0, 260.0, 17.0)) == [("l340", "l260", "order")]


def test_the_shorter_nearest_the_part_and_the_longer_outside_is_quiet() -> None:
    assert stacks(above("l260", 0.0, 260.0, 10.0), above("l340", 0.0, 340.0, 17.0)) == []
    assert stacks(above("a", 0.0, 100.0, 10.0), above("b", 0.0, 200.0, 17.0), above("c", 0.0, 340.0, 24.0)) == []


def test_two_lines_closer_than_the_minimum_are_a_gap() -> None:
    found = vr.dimension_stacks([above("a", 0.0, 100.0, 10.0), above("b", 0.0, 200.0, 14.0)], {"front": BOX})
    assert [(f.owner, f.other, f.kind) for f in found] == [("b", "a", "gap")]
    assert abs(found[0].gap_mm - 4.0) < 1e-6
    assert stacks(above("a", 0.0, 100.0, 10.0), above("b", 0.0, 200.0, 16.6)) == []  # 6.6 mm: enough


def test_a_chain_shares_no_run_and_may_stand_on_one_line_or_close() -> None:
    assert stacks(above("a", 0.0, 100.0, 10.0), above("b", 100.0, 190.0, 12.0)) == []


def test_dimensions_of_another_side_or_view_are_not_compared() -> None:
    below = vr.DimShape("b", "front", ((0.0, -1.0, 0.0, -12.0), (200.0, -1.0, 200.0, -12.0)), -10.0, 200.0, 9.0, True)
    assert stacks(above("a", 0.0, 100.0, 10.0), below) == []
    assert stacks(above("a", 0.0, 100.0, 10.0), above("b", 0.0, 200.0, 11.0, view="top")) == []


def review_data(dims: list[vr.DimShape]) -> review.ReviewInput:
    view = sc.ViewInfo("front", 1.0, 1.0, 1.0, (0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (200.0, 20.0, 0.0), BOX)
    ctx = sc.Context(sheet=model.Sheet(420.0, 297.0), views={"front": view}, holes=[], geometry=sc.Geometry(), area=(20.0, 40.0, 410.0, 287.0))
    ctx.extras["dim_shapes"] = dims
    items = [{"id": d.owner, "type": "dim"} for d in dims]
    return review.ReviewInput(items, {}, ctx, ctx.sheet)


def test_the_review_names_both_dimensions_and_holds_the_export() -> None:
    data = review_data([above("l340", 0.0, 340.0, 10.0), above("l260", 0.0, 260.0, 17.0)])
    found = [f for f in checks.check(data) if f.code == "dimension_stack_order"]
    assert len(found) == 1 and found[0].items == ("l340", "l260")
    assert "340" in found[0].message and "260" in found[0].message and "ISO 129-1" in found[0].message
    assert "dimension_stack_order" not in checks_mod.NON_BLOCKING and "dimension_gap" not in checks_mod.NON_BLOCKING


def test_the_review_states_the_minimum_it_applies() -> None:
    found = [f for f in checks.check(review_data([above("a", 0.0, 100.0, 10.0), above("b", 0.0, 200.0, 13.0)])) if f.code == "dimension_gap"]
    assert len(found) == 1 and "3 mm: el mínimo entre cotas paralelas es 6,5 mm" in found[0].message  # the measured gap and the minimum, stated


def test_an_extension_line_across_another_dimension_line_holds_the_export() -> None:
    assert "dimension_crossing" not in checks_mod.NON_BLOCKING
