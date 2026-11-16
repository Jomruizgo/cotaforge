# ruff: noqa: E501
"""Two owner rules read off a bench shaft sheet (no FreeCAD): no redundant dimension (``dimension_redundant``) and a hidden line is
never dimensioned (``dimension_on_hidden``)."""

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import sheet_checks as checks  # noqa: E402
import sheet_hiddendims as hiddendims  # noqa: E402
import sheet_review as review  # noqa: E402
import sheet_viewchecks as viewchecks  # noqa: E402

from .test_sheet_turned import ring_context  # noqa: E402

# --- the sum or the difference of other dimensions ---------------------------------------------------------------------------


def _redundant(records: list[tuple[str, str, tuple[Any, ...]]]) -> list[review.Finding]:
    ctx = ring_context()
    ctx.extras["dim_records"] = records
    return [f for f in viewchecks.check(review.ReviewInput([], {}, ctx, ctx.sheet)) if f.code == "dimension_redundant"]


def test_an_overall_beside_the_segments_that_add_up_to_it_is_redundant() -> None:
    (found,) = _redundant(
        [("len125", "front", ("len", "x", 0.0, 125.0)), ("thread", "front", ("len", "x", 125.0, 168.0)), ("overall", "front", ("len", "x", 0.0, 168.0))]
    )
    assert set(found.items) == {"len125", "thread", "overall"} and "front" in found.message
    assert "dimension_redundant" in checks.NON_BLOCKING  # listed until the sheet defaults stop breaking it


def test_a_dimension_that_is_the_difference_of_two_others_is_redundant() -> None:
    """The bench shaft: 125 from one end, 168 overall and the 43 of the thread, from the other end."""
    (found,) = _redundant(
        [("a", "front", ("len", "x", 0.0, 168.0)), ("b", "front", ("len", "x", 125.0, 168.0)), ("c", "front", ("len", "x", 0.0, 125.0))]
    )
    assert set(found.items) == {"a", "b", "c"}


def test_a_baseline_stack_a_chain_without_its_closing_dimension_and_other_views_are_not_redundant() -> None:
    stack = [(f"d{i}", "front", ("len", "x", 0.0, float(end))) for i, end in enumerate((28, 43, 103, 168))]
    assert _redundant(stack) == []
    chain = [("a", "front", ("len", "x", 0.0, 28.0)), ("b", "front", ("len", "x", 28.0, 43.0)), ("c", "front", ("len", "x", 43.0, 168.0))]
    assert _redundant(chain) == []
    split = [("a", "front", ("len", "x", 0.0, 28.0)), ("b", "top", ("len", "x", 28.0, 168.0)), ("c", "front", ("len", "x", 0.0, 168.0))]
    assert _redundant(split) == []  # the loop is not in one view
    assert _redundant([("a", "front", ("len", "x", 0.0, 10.0)), ("b", "front", ("len", "y", 10.0, 20.0)), ("c", "front", ("len", "y", 0.0, 20.0))]) == []  # other axes


def test_the_same_length_twice_a_stage_a_diameter_and_one_items_own_links_are_not_this() -> None:
    assert _redundant([("a", "front", ("len", "x", 0.0, 10.0)), ("b", "front", ("len", "x", 0.0, 10.0))]) == []  # ``dimension_twice``
    staged = ("len", "x", 0.0, 168.0, "stage", "ground")
    assert _redundant([("a", "front", ("len", "x", 0.0, 100.0)), ("b", "front", ("len", "x", 100.0, 168.0)), ("c", "front", staged)]) == []
    assert _redundant([("a", "front", ("dia", 22.0, "face:3")), ("b", "front", ("dia", 25.0, "face:5"))]) == []
    one = [("stack", "front", ("len", "x", 0.0, 10.0)), ("stack", "front", ("len", "x", 10.0, 30.0)), ("stack", "front", ("len", "x", 0.0, 30.0))]
    assert _redundant(one) == []


# --- a dimension on a hidden line --------------------------------------------------------------------------------------------


def _edge(x0: float, y0: float, x1: float, y1: float) -> Any:
    return SimpleNamespace(seg=(x0, y0, x1, y1))


def _near(edge: Any, x: float, y: float) -> float:
    x0, y0, x1, y1 = edge.seg
    dx, dy = x1 - x0, y1 - y0
    t = max(0.0, min(1.0, ((x - x0) * dx + (y - y0) * dy) / ((dx * dx + dy * dy) or 1.0)))
    return float(((x - (x0 + t * dx)) ** 2 + (y - (y0 + t * dy)) ** 2) ** 0.5)


def _page(refs: list[tuple[str, ...]], hidden: bool = True) -> Any:
    """A view with two visible edges (the outline) and one hidden edge (a keyway end seen through the shaft); a dimension per row of ``refs``."""
    edges = [_edge(0, 0, 100, 0), _edge(0, 11, 100, 11), _edge(30, 4, 30, 7)]
    vertices = [SimpleNamespace(X=0.0, Y=0.0), SimpleNamespace(X=100.0, Y=0.0), SimpleNamespace(X=30.0, Y=4.0), SimpleNamespace(X=50.0, Y=5.0)]
    view = SimpleNamespace(
        Name="View_front",
        Label="Front",
        getVisibleEdges=lambda: edges[:2],
        getHiddenEdges=lambda: edges[2:] if hidden else [],
        getEdgeByIndex=lambda i: edges[i],
        getVertexByIndex=lambda i: vertices[i],
    )
    dims = [
        SimpleNamespace(TypeId="TechDraw::DrawViewDimension", Label=f"dim{i}", Name=f"dim{i}", Item=item, References2D=[(view, list(subs))])
        for i, (item, *subs) in enumerate(refs)
    ]
    return SimpleNamespace(Views=[view, *dims])


def _hidden(refs: list[tuple[str, ...]], hidden: bool = True) -> list[tuple[str, str, str]]:
    return hiddendims.hidden_references(_page(refs, hidden), {"View_front": "front"}, "Item", _near)


def test_a_dimension_on_a_hidden_edge_or_on_the_end_of_one_is_found() -> None:
    assert _hidden([("kw_len", "Edge2")]) == [("kw_len", "front", "Edge2")]
    assert _hidden([("kw_pos", "Vertex2")]) == [("kw_pos", "front", "Vertex2")]  # the end of the hidden line, inside the outline


def test_a_dimension_on_the_outline_a_point_off_every_line_or_a_view_without_hidden_lines_is_not() -> None:
    assert _hidden([("d", "Edge0"), ("e", "Vertex0"), ("f", "Vertex1")]) == []  # the visible outline and its ends
    assert _hidden([("g", "Vertex3")]) == []  # a point on no line
    assert _hidden([("kw_len", "Edge2"), ("kw_pos", "Vertex2")], hidden=False) == []  # the view shows no hidden line
    assert _hidden([("", "Edge2")]) == []  # the engineer's own dimension (no item): his decision


def test_the_finding_names_the_dimension_and_holds_the_export() -> None:
    ctx = ring_context()
    (row,) = hiddendims.findings([("kw_len", "front", "Edge2"), ("kw_len", "front", "Vertex2")], ctx)
    assert row["code"] == "dimension_on_hidden" and row["items"] == ["kw_len"] and "oculta" in row["message"]
    assert "dimension_on_hidden" in checks.NON_BLOCKING  # listed until the automatic keyway dimensions leave the hidden lines
