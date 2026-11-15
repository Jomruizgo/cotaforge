# ruff: noqa: E501
"""The review reads the dimensions TechDraw holds on the page, a view drawn twice holds the export, a native object the review
cannot read holds the export, and the rules a step answers for (no FreeCAD)."""

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import sheet_checks as checks  # noqa: E402
import sheet_context as sc  # noqa: E402
import sheet_model as model  # noqa: E402
import sheet_pageread as pageread  # noqa: E402
import sheet_review as review  # noqa: E402
import sheet_viewchecks as viewchecks  # noqa: E402
import sheet_viewrules as rules  # noqa: E402
from sheet_context import ViewInfo  # noqa: E402

from .test_sheet_turned import ring_context  # noqa: E402

# --- the dimensions of the page ----------------------------------------------------------------------------------------------


def _vec(x: float, y: float) -> Any:
    return SimpleNamespace(x=x, y=y, z=0.0)


def _view(name: str = "View_front", x: float = 186.0, y: float = 215.0, scale: float = 0.5) -> Any:
    return SimpleNamespace(Name=name, Label=name, px=x, py=y, getScale=lambda: scale)


def _dim(view: Any, points: Any, arrows: Any, label: str = "hub", item: str = "hub", kind: str = "Distance") -> Any:
    """A TechDraw linear dimension as FreeCAD 1.1 answers: points in the view's page mm, arrow tips in its unscaled mm."""
    return SimpleNamespace(
        Name=f"dim_{label}",
        Label=label,
        Type=kind,
        CotaforgeItem=item,
        References2D=[(view, "Vertex1")],
        getLinearPoints=lambda: [_vec(*p) for p in points],
        getArrowPositions=lambda: [_vec(*a) for a in arrows],
        getText=lambda: "Ø70",
    )


HUB = {"points": [(3.0, 17.48), (3.0, -17.52)], "arrows": [(52.49, 35.04), (52.49, -34.96)]}  # measured on FreeCAD 1.1 for a Ø70 step at 1:2


def _read(page_views: list[Any]) -> tuple[list[rules.DimShape], list[tuple[str, str]]]:
    page = SimpleNamespace(Views=page_views)
    return pageread.dimension_shapes(page, {"View_front": "front"}, lambda _p, v: (v.px, v.py), "CotaforgeItem")


def test_a_native_dimension_is_read_where_techdraw_puts_its_lines() -> None:
    (shape,), unread = _read([_dim(_view(), **HUB)])
    assert unread == [] and (shape.owner, shape.view) == ("hub", "front")
    assert abs(shape.line_at - (186.0 + 52.49 * 0.5)) < 1e-6  # the dimension line: the arrow tips at the scale of the view
    # one extension line from each measured point; ``getLinearPoints`` has y down (the point at 17,48 belongs to the arrow tip at -34,96 / 2)
    assert [round(s[1], 2) for s in shape.extension] == [round(215 - 17.48, 2), round(215 + 17.52, 2)]
    assert all(abs(s[0] - 189.0) < 1e-6 and abs(s[2] - shape.line_at) < 1e-6 for s in shape.extension)  # horizontal, up to the dimension line
    assert abs(shape.span - 35.0) < 1e-6


def test_the_extension_lines_of_a_native_diameter_across_another_dimensions_line_are_a_crossing() -> None:
    """The cover of the review: the diameters Ø70 / Ø50 are aligned dimensions on the page (extension lines out to x = 212) and the
    overall Ø100 stands at x = 203.5: the extension lines cross its line. The composed sheet drew the two as leaders and saw nothing."""
    overall = rules.DimShape(
        "o1", "front", ((194.5, 190.5, 205.5, 190.5), (194.5, 240.5, 205.5, 240.5)), 203.5, 50.0, 8.0, True
    )
    native, _ = _read([_dim(_view(), **HUB)])
    judged = pageread.merged([overall], native)
    crossings = rules.dimension_crossings(judged)
    assert [(c.owner, c.other) for c in crossings] == [("hub", "o1")]


def test_a_native_shape_replaces_the_composed_one_of_the_same_item_and_the_rest_stay() -> None:
    drawn = [rules.DimShape("hub", "front", (), 1.0), rules.DimShape("o1", "front", (), 2.0)]
    page = [rules.DimShape("hub", "front", (), 3.0)]
    assert [(s.owner, s.line_at) for s in pageread.merged(drawn, page)] == [("o1", 2.0), ("hub", 3.0)]


def test_a_dimension_it_cannot_read_is_listed_never_skipped() -> None:
    oblique = _dim(_view(), [(0.0, 0.0), (10.0, 10.0)], [(0.0, 0.0), (20.0, 20.0)], label="slanted", item="")

    def broken() -> Any:
        msg = "the dimension is not computed"
        raise RuntimeError(msg)

    failing = _dim(_view(), **HUB, label="late")
    failing.getLinearPoints = broken
    orphan = _dim(_view(), **HUB, label="loose")
    orphan.References2D = []
    shapes, unread = _read([oblique, failing, orphan, _dim(_view(), **HUB, kind="Diameter", label="dia")])
    assert shapes == []  # a diameter has no extension lines: it is not a linear dimension, nothing to read
    assert len(unread) == 3 and all(":" in why for _owner, why in unread)
    assert "oblique" in unread[0][1] and "RuntimeError" in unread[1][1] and "no view" in unread[2][1]
    assert [owner for owner, _ in unread] == ["slanted", "hub", "hub"]  # the owner is what a stand-in shape of the composed sheet is looked up by


def test_a_dimension_whose_lines_are_not_computed_is_unread_not_wrong() -> None:
    """A headless FreeCAD gives every arrow tip at the origin: nothing to read (the composed sheet's own shape stands in)."""
    zero = _dim(_view(), [(0.0, 0.0), (0.0, 0.0)], [(0.0, 0.0), (0.0, 0.0)], label="Dim width", item="o1")
    shapes, unread = _read([zero])
    assert shapes == [] and unread == [("o1", "Dim width: TechDraw has not computed its lines")]


def test_a_dimension_nobody_tagged_is_its_own_owner() -> None:
    (shape,), _ = _read([_dim(_view(), **HUB, label="by hand", item="")])
    assert shape.owner == "by hand"


# --- a native object the review cannot read holds the export ----------------------------------------------------------------------


def test_native_unread_holds_the_export() -> None:
    assert "native_unread" not in checks.NON_BLOCKING
    rows: list[dict[str, Any]] = [{"code": "native_unread", "items": [], "features": []}, {"code": "window_differs", "items": [], "features": []}]
    checks.assign_ids(rows)
    assert [r["blocking"] for r in rows] == [True, False]


# --- the profile twice ------------------------------------------------------------------------------------------------------------


def _duplicates(carried: dict[str, set[str]], coaxial: bool = True) -> list[review.Finding]:
    ctx = ring_context(coaxial)
    ctx.extras["view_use"] = carried
    items = [{"id": "o1", "type": "overall"}, {"id": "d1", "type": "dim"}, {"id": "d2", "type": "dim"}]
    return [f for f in viewchecks.check(review.ReviewInput(items, {}, ctx, ctx.sheet)) if f.code == "view_duplicate"]


def test_two_views_across_the_axis_of_a_part_of_revolution_are_the_same_drawing_and_the_second_is_flagged() -> None:
    """The ring's front and right views both see the axis edge-on: the profile twice. The one that carries the fewer items is the repeat."""
    (found,) = _duplicates({"front": {"o1"}, "right": {"d1", "d2"}, "top": {"d1"}})
    assert (found.code, found.severity, found.items) == ("view_duplicate", "warning", ("front",))
    assert "repite la vista right" in found.message and "remove_view" in found.message
    (tie,) = _duplicates({"front": {"o1"}, "right": {"d1"}})
    assert tie.items == ("right",)  # a tie keeps the front view


def test_it_holds_the_export_and_only_a_part_of_revolution_has_the_same_profile_from_two_sides() -> None:
    assert "view_duplicate" not in checks.NON_BLOCKING
    assert _duplicates({"front": {"o1"}, "right": {"d1"}}, coaxial=False) == []  # a part with an off-axis feature: its sides differ


def test_two_views_that_look_the_same_way_are_one_view_drawn_twice() -> None:
    ctx = ring_context(False)
    ctx.views["right"] = ViewInfo("right", 0, 0, 1.0, ctx.views["front"].direction, ctx.views["front"].xdir, (0.0, 0.0, 0.0), (0, 0, 10, 10))
    ctx.extras["view_use"] = {"front": {"o1"}, "right": set()}
    found = [f for f in viewchecks.check(review.ReviewInput([{"id": "o1", "type": "overall"}], {}, ctx, ctx.sheet)) if f.code == "view_duplicate"]
    assert [f.items for f in found] == [("right",)]


# --- the rules a step answers for -------------------------------------------------------------------------------------------------


def test_a_step_runs_only_the_view_crossing_and_paper_rules() -> None:
    ctx = ring_context()
    ctx.extras["view_use"] = {"front": {"o1"}, "right": {"d1"}}  # the top view carries nothing, the front and right repeat each other
    items = [{"id": "o1", "type": "overall"}, {"id": "d1", "type": "dim"}]
    data = review.ReviewInput(items, {}, ctx, ctx.sheet)
    codes = sorted({f.code for f in viewchecks.step_check(data)})
    assert codes and set(codes) <= set(viewchecks.STEP_CODES)
    assert {"view_adds_nothing", "view_duplicate"} <= set(codes)
    assert not any(f.code in {"dimension_stack_order", "datum_unused", "view_redundant", "no_isometric"} for f in viewchecks.step_check(data))


def test_every_rule_of_a_step_is_one_that_holds_the_export() -> None:
    assert not set(viewchecks.STEP_CODES) & checks.NON_BLOCKING


def test_a_sheet_of_one_view_has_nothing_to_report_at_a_step() -> None:
    ctx = sc.Context(model.Sheet(297, 210), {}, [], sc.Geometry(), (0, 0, 1, 1), language="es")
    assert viewchecks.step_check(review.ReviewInput([], {}, ctx, ctx.sheet)) == []


# --- the same measure twice ---------------------------------------------------------------------------


def _twice(records: list[tuple[str, str, tuple[Any, ...]]]) -> list[review.Finding]:
    ctx = ring_context()
    ctx.extras["dim_records"] = records
    return [f for f in viewchecks.check(review.ReviewInput([], {}, ctx, ctx.sheet)) if f.code == "dimension_twice"]


def test_the_same_length_given_by_two_dimensions_of_one_view_holds_the_export() -> None:
    """The assembly's hole spacing: "80" twice, one over the other."""
    (found,) = _twice([("d80", "top", ("len", "x", 20.0, 100.0)), ("loc", "top", ("len", "x", 20.0, 100.0))])
    assert found.items == ("d80", "loc") and "dada dos veces en la vista top" in found.message and "remove" in found.message
    assert "dimension_twice" not in checks.NON_BLOCKING


def test_the_same_size_in_two_views_or_one_item_saying_two_things_is_not_that() -> None:
    assert _twice([("a", "front", ("len", "x", 0.0, 10.0)), ("b", "top", ("len", "x", 0.0, 10.0))]) == []  # two views: ``dimension_repeated``
    assert _twice([("dext", "A-A", ("dia", 100.0, "face:2")), ("dext", "A-A", ("dia", 100.0, "overall"))]) == []


# --- the lines of a note printed on one another -----------------------------------------------------------------------------------


def _note_line(label: str, x: float, y: float, text: str, size: float = 3.5) -> Any:
    return SimpleNamespace(
        TypeId="TechDraw::DrawViewAnnotation", Label=label, Name=label, Text=[text], TextSize=size, X=x, Y=y
    )


def _over(lines: list[Any]) -> list[review.Finding]:
    boxes, unread = pageread.text_boxes(SimpleNamespace(Views=lines))
    assert unread == []
    ctx = ring_context()
    ctx.extras["page_texts"] = boxes
    items = [{"id": "n4", "type": "note"}, {"id": "n5", "type": "note"}]
    return [f for f in viewchecks.check(review.ReviewInput(items, {}, ctx, ctx.sheet)) if f.code == "text_over_text"]


def test_two_lines_of_a_note_printed_on_one_another_are_flagged_and_lines_one_step_apart_are_not() -> None:
    """The shaft: the second line of note 5 landed on its first one. Lines 5 mm apart (the step of a note) read."""
    apart = [_note_line("n4 n1", 235.5, 247.4, "4. Salto radial del extremo"), _note_line("n5 n1", 235.5, 241.4, "5. Chavetero DIN 6885"), _note_line("n5 n2", 235.5, 236.4, "    R3, flancos Ra 3,2")]
    assert _over(apart) == []
    (found,) = _over([apart[0], apart[1], _note_line("n5 n2", 235.5, 241.4, "    R3, flancos Ra 3,2. Simetria")])
    assert found.items == ("n5",) and "se imprimen una sobre otra" in found.message
    assert "text_over_text" not in checks.NON_BLOCKING


def test_texts_side_by_side_on_one_line_are_not_on_one_another() -> None:
    """Note 7 and note 8 of the shaft stand at one height, a block apart."""
    assert _over([_note_line("n7 n1", 265.5, 88.4, "7. Chaflanes: 1,5 x 45"), _note_line("n8 n1", 425.5, 88.4, "8. Cantidad: 10 unidades.")]) == []
