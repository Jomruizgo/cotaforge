# ruff: noqa: E501
"""Two owner rules read off a bench shaft sheet (no FreeCAD): no redundant dimension (``dimension_redundant``) and a hidden line is
never dimensioned (``dimension_on_hidden``)."""

import re
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from cotaforge.contract import drawing_items
from cotaforge.contract.requests import AnnotateDrawingRequest

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import sheet_checks as checks  # noqa: E402
import sheet_hiddendims as hiddendims  # noqa: E402
import sheet_review as review  # noqa: E402
import sheet_viewchecks as viewchecks  # noqa: E402

from .test_sheet_items import hole  # noqa: E402
from .test_sheet_turned import ring_context  # noqa: E402
from .test_sheet_views import run_review, views_context  # noqa: E402

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


CALL = re.compile(r'drawing\(action="(\w+)", ([^)]*)\)')
HOLDS_BACK = ("remove", "remove_view", "remove_sheet")  # ``drawing_gate.guard_removal``: an ``rq_`` item comes off only with reason= (and by=)


def _messages(item: str, kind: str | None) -> list[str]:
    out = []
    for language in ("es", "en"):
        ctx = ring_context()
        ctx.language = language
        ctx.extras["items"] = [{"id": item, "type": kind}] if kind else []
        out.append(hiddendims.findings([(item, "front", "Edge2")], ctx)[0]["message"])
    return out


def test_the_message_names_only_calls_the_gates_let_the_drafter_make() -> None:
    """The way out is to MOVE the dimension (``update``) to the standard view that sees the feature face-on, which ``add_view`` puts on
    the sheet: never a bare ``remove`` (an ``rq_`` item needs reason=, a measured one the engineer's order, and a multi-dimension item
    loses every dimension), and every call it names takes fields the request really has."""
    for item, kind in (("rq_keyway_l", "dim"), ("kw_len", None), ("bl", "baseline_dims")):
        for message in _messages(item, kind):
            calls = CALL.findall(message)
            assert calls, message
            for action, body in calls:
                assert action in {"add_view", "update"}, message
                assert action not in HOLDS_BACK
                if action == "add_view":
                    assert body == 'views=["top"]'  # what ``view_export.drawing`` turns into ``AnnotateDrawingRequest(action="add_view", view="top")``
                    AnnotateDrawingRequest(operation_id="drawing", action="add_view", view="top")
                else:
                    assert f'"id":"{item}","view":"top"' in body and "view" in drawing_items.ITEM_FIELDS["dim"]
            assert 'action="remove"' not in message and "ids=" not in message


def test_the_message_says_so_when_there_is_no_tool_for_what_it_asks() -> None:
    """There is no partial (broken-out) section (``section`` is full only) and an ``aux_view`` takes no dimension (an item's ``view`` is a
    standard view or a section label). The message says so and never offers either as the way."""
    assert "partial" not in drawing_items.ITEM_FIELDS and "broken" not in " ".join(drawing_items.ITEM_FIELDS)
    for kind in ("dim", "baseline_dims"):
        es, en = _messages("d1", kind)
        assert "no hay corte parcial" in es and "no admite cotas" in es and "no hay herramienta" in es
        assert "no partial section" in en and "takes no dimensions" in en and "there is no tool" in en
        assert 'action="add"' not in es and 'action="add"' not in en  # the auxiliary view is mentioned only to say it takes no dimension


def test_an_item_of_several_dimensions_is_not_told_to_be_moved_or_removed_whole() -> None:
    es, en = _messages("bl", "baseline_dims")
    assert "baseline_dims" in es and "agrupa varias cotas" in es and "groups several dimensions" in en
    assert '"id":"bl","view"' not in es  # update would carry every dimension of the item to the other view


# --- one defect, one finding, one weight ---------------------------------------------------------------------------------------


def _finding(code: str, *items: str) -> review.Finding:
    return review.Finding(code, "warning", "", tuple(items))


def test_a_chain_that_closes_on_the_overall_is_listed_once_as_over_dimensioned() -> None:
    """125 + 43 closing on the overall 168: ``over_dimensioned`` holds the export, so ``dimension_redundant`` (which only warns) stays quiet."""
    both = [_finding("over_dimensioned", "len125", "len43"), _finding("dimension_redundant", "o1", "len125", "len43")]
    (left,) = review.one_weight_per_defect(both)
    assert left.code == "over_dimensioned" and "over_dimensioned" not in checks.NON_BLOCKING


def test_a_redundant_loop_over_dimensioned_does_not_report_is_still_listed() -> None:
    other = _finding("dimension_redundant", "a", "b", "c")  # a difference of two others: no overall involved
    unrelated = _finding("over_dimensioned", "len125", "len43")
    assert review.one_weight_per_defect([unrelated, other]) == [unrelated, other]
    assert review.one_weight_per_defect([other]) == [other]
    assert review.one_weight_per_defect([_finding("over_dimensioned")]) == [_finding("over_dimensioned")]  # no ids: matches nothing


def test_the_catalog_rows_of_the_two_findings_agree_with_it() -> None:
    from cotaforge.drawing import catalog  # noqa: PLC0415

    rows = {r.id: r for r in catalog.load().rules}
    assert "over_dimensioned" in rows["dimensions.once"].checks and rows["dimensions.once"].weight == "blocks"
    assert rows["dimensions.no_redundant"].weight == "warns" and "dimensions.once" in rows["dimensions.no_redundant"].text


# --- the plate of the owner: 8 + 44 + 8 closes on 60 -------------------------------------------------------------------------------


def _plate_row(item: str, a: float, b: float) -> dict[str, Any]:
    return {"type": "dim", "id": item, "kind": "distance", "from": {"at": [10, a, 8]}, "to": {"at": [10, b, 8]}, "along": "y", "view": "top"}


def _plate_review(*specs: dict[str, Any], language: str = "es") -> list[review.Finding]:
    """A 100 x 60 plate with four holes on two rows (y = 8 and y = 52), the top view, and the findings of its sheet."""
    ctx = views_context([hole(0, 10, 8), hole(1, 90, 8), hole(2, 10, 52), hole(3, 90, 52)])
    ctx.language = language
    return run_review(ctx, {"type": "overall", "id": "o1"}, *specs)


def _closing(found: list[review.Finding]) -> list[review.Finding]:
    return [f for f in found if f.code in ("over_dimensioned", "dimension_redundant")]


def test_8_44_8_on_the_overall_60_is_one_blocking_finding_when_all_are_dim_items() -> None:
    (one,) = _closing(_plate_review(_plate_row("top8", 0, 8), _plate_row("row44", 8, 52), _plate_row("low8", 52, 60)))
    assert one.code == "over_dimensioned" and set(one.items) == {"top8", "row44", "low8"}
    assert "over_dimensioned" not in checks.NON_BLOCKING  # it holds the export


def test_8_44_8_on_the_overall_60_is_caught_when_the_spacing_and_the_locator_come_from_the_default_hole_location() -> None:
    """The default location of the holes (``datum_dims`` auto: the spacing of the group and ONE locator from the nearest edge, 44 and 8)
    plus a dimension of the other 8 closes on the overall. Develop did not see it: the spacing and the locator are not ``dim`` items,
    and the loop check reads only the lengths of ``dim`` items and the overall."""
    (one,) = _closing(_plate_review({"type": "datum_dims", "id": "g", "auto": True}, _plate_row("low8", 52, 60)))
    assert one.code == "over_dimensioned" and one.items == ("g", "low8")
    assert "localizador" in one.message and "otro extremo" in one.message


def test_the_locator_with_the_spacing_alone_and_a_chain_that_does_not_close_are_not_flagged() -> None:
    assert _closing(_plate_review({"type": "datum_dims", "id": "g", "auto": True})) == []  # 8 + 44: the far 8 is not given
    assert _closing(_plate_review(_plate_row("top8", 0, 8), _plate_row("row44", 8, 52))) == []


def test_the_fix_the_message_names_is_one_the_gate_lets_through() -> None:
    """Keep the locator from the reference edge with the spacing and drop the dimension at the other end. The ``remove`` the message
    names takes ``items`` (not ``ids``) and says that an ``rq_`` id also needs reason= (``drawing_gate.guard_removal``)."""
    for language, start in (("es", "Deja el localizador"), ("en", "Keep the locator")):
        (one,) = _closing(_plate_review({"type": "datum_dims", "id": "g", "auto": True}, _plate_row("low8", 52, 60), language=language))
        assert start in one.message
        assert one.message.count('drawing(action="remove", items=[') == 1 and "rq_" in one.message and "reason=" in one.message and "ids=" not in one.message


def test_a_chain_made_by_the_default_hole_location_alone_is_not_a_finding_the_drafter_could_clear() -> None:
    """Two groups of holes (Ø6.6 on the rows y = 8 and 52, Ø5 on y = 20 and 52): the default location gives 8 + 44 from the bottom
    and 8 from the top, which closes on 60, all from the ONE item. The drafter has no tool to take one dimension out of it, so the
    review leaves it alone (a chain with another item in it is flagged: the test above)."""
    ctx = views_context([hole(0, 10, 8), hole(1, 90, 8), hole(2, 10, 52), hole(3, 90, 52), hole(4, 50, 20, 5.0), hole(5, 70, 20, 5.0), hole(6, 50, 52, 5.0), hole(7, 70, 52, 5.0)])
    found = run_review(ctx, {"type": "overall", "id": "o1"}, {"type": "datum_dims", "id": "g", "auto": True})
    spans = [(a, b) for item, axis, a, b in ctx.extras["position_spans"] if axis == "y"]
    assert (0.0, 8.0) in spans and (8.0, 52.0) in spans and (52.0, 60.0) in spans, spans  # the chain does close, inside one item
    assert _closing(found) == []
