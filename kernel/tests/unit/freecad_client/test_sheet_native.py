"""What an item asks of TechDraw (native dimensions, annotations, view flags), the placement of the sheet (collisions of
ink, a label that slides along its face, a layout without a view) and the review of the views, without FreeCAD."""

import sys
from pathlib import Path
from typing import Any

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import items_attach as attach  # noqa: E402
import sheet_collide as collide  # noqa: E402
import sheet_draw as draw  # noqa: E402
import sheet_layout as layout  # noqa: E402
import sheet_model as model  # noqa: E402
import sheet_review as review  # noqa: E402
import sheet_units as units  # noqa: E402
from sheet_context import Context, ViewInfo  # noqa: E402

# --- what a unit asks of TechDraw -------------------------------------------------------------------------------


def _dim(**over: Any) -> units.NativeDim:
    base: dict[str, Any] = {
        "item": "d1",
        "name": "d1",
        "kind": "distance",
        "view": "top",
        "refs": (units.Ref2("point", (0.0, 0.0)), units.Ref2("point", (50.0, 0.0))),
        "label": (25.0, 10.0),
        "expected": 50.0,
        "orient": "h",
    }
    return units.NativeDim(**{**base, **over})


class _Obj:
    def __init__(self, value: float | Exception) -> None:
        self.value = value

    def getRawValue(self) -> float:  # noqa: N802 - TechDraw's name
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


def test_a_native_dimension_is_true_only_when_techdraw_measures_the_models_value() -> None:
    dim = _dim()
    assert units.measured_problem(_Obj(50.0), dim) is None
    assert units.measured_problem(_Obj(-50.004), dim) is None  # TechDraw may measure the other way round
    assert "TechDraw measures 49.000 and the model says 50.000" in str(units.measured_problem(_Obj(49.0), dim))
    assert "could not measure" in str(units.measured_problem(_Obj(RuntimeError("no")), dim))
    assert units.measured_problem(_Obj(1.0), _dim(expected=None)) is None
    angle = _dim(kind="angle", expected=45.0)
    assert units.measured_problem(_Obj(45.04), angle) is None and units.measured_problem(_Obj(45.2), angle)


def test_the_signature_changes_with_what_the_sheet_asks_and_not_with_noise() -> None:
    def unit(*natives: units.Native) -> units.Unit:
        return units.Unit(
            "d1", "d1 dim", units.KIND_ITEM, (model.Line(0, 0, 1, 1, model.DIMENSIONS),), "dim", None, natives
        )

    first = units.signature(unit(_dim()))
    assert first == units.signature(unit(_dim(label=(25.001, 10.0))))  # a thousandth of a millimetre is not a move
    assert first != units.signature(unit(_dim(label=(26.0, 10.0))))
    assert first != units.signature(unit(_dim(text="Ø6")))
    assert first != units.signature(unit(_dim(tol=(0.1, -0.1))))


def test_a_note_is_one_annotation_per_line_and_a_view_flag_no_object() -> None:
    note = units.NativeNote("n1", "n", ("NOTAS:", "1. uno"), (27.0, 60.0))
    flag = units.NativeFlag("vl", "front")
    u = units.Unit("n1", "n1 note", units.KIND_ITEM, (), "note", None, (note, flag))
    assert units.object_count(u) == 2
    assert units.object_count(units.Unit("vl", "vl", units.KIND_ITEM, (), "view_lines", None, (flag,))) == 0


def test_what_techdraw_draws_leaves_only_the_rest_for_the_symbol() -> None:
    mark = model.Line(0, 0, 4, 0, model.AXES)
    texts = (model.Text(10, 10, "Ø6", 3.5, model.TEXT), model.Line(0, 0, 9, 9, model.DIMENSIONS), mark)
    held = units.Unit("h1", "h1 hole_dim", units.KIND_ITEM, texts, "hole_dim", None, (_dim(),))
    assert held.residue == (mark,)  # the centre mark stays a symbol
    assert units.Unit("h1", "h1", units.KIND_ITEM, texts, "hole_dim").residue == texts  # no natives: all of it
    hidden = model.Line(0, 0, 4, 0, model.HIDDEN)
    lines = units.Unit(
        "vl", "vl", units.KIND_ITEM, (hidden, mark), "view_lines", None, (units.NativeFlag("vl", "front"),)
    )
    assert lines.residue == (mark,)  # TechDraw draws the hidden edges itself


# --- collisions of ink -----------------------------------------------------------------------------------------------


def _ink(owner: str, *entities: model.Entity) -> collide.Ink:
    return collide.ink_of(owner, entities)


def test_a_frame_over_a_dimension_text_is_a_collision_and_beside_it_is_not() -> None:
    dimension = _ink("o1", model.Text(250.0, 199.0, "60", 3.5, model.DIMENSIONS, "center", 90.0))
    frame_over = _ink(
        "perp",
        model.Line(246.0, 190.0, 262.0, 190.0),
        model.Line(246.0, 205.0, 262.0, 205.0),
        model.Line(247.5, 190.0, 247.5, 205.0),
    )
    found = collide.collisions([dimension, frame_over])
    assert [(c.a, c.b, c.kind) for c in found] == [("perp", "o1", "line_text")]
    frame_beside = _ink("perp", model.Line(256.0, 190.0, 272.0, 190.0), model.Line(256.0, 205.0, 272.0, 205.0))
    assert collide.collisions([dimension, frame_beside]) == []


def test_a_leader_through_the_text_of_a_dimension_collides_and_through_its_line_does_not() -> None:
    dimension = _ink(
        "o1",
        model.Line(250.0, 169.0, 250.0, 229.0, model.DIMENSIONS),
        model.Solid(((250.0, 169.0), (250.9, 172.0), (249.1, 172.0))),
        model.Text(248.8, 199.0, "60", 3.5, model.DIMENSIONS, "center", 90.0),
    )
    through_text = _ink("perp", model.Line(235.0, 199.0, 262.0, 199.0))
    through_line = _ink("perp", model.Line(235.0, 184.0, 262.0, 184.0))
    assert [c.kind for c in collide.collisions([dimension, through_text])] == ["line_text"]
    assert collide.collisions([dimension, through_line]) == []  # a leader may cross a dimension line, never its text


def test_two_texts_on_one_place_collide_and_each_alone_does_not() -> None:
    a = _ink("a", model.Text(60.0, 120.0, "Uno encima", 3.5))
    b = _ink("b", model.Text(62.0, 121.0, "Otro encima", 3.5))
    assert [(c.a, c.b, c.kind) for c in collide.collisions([a, b])] == [("b", "a", "text_text")]
    assert (
        collide.collisions([a]) == []
        and collide.collisions([a, _ink("c", model.Text(60.0, 140.0, "Lejos", 3.5))]) == []
    )


def test_two_dimension_lines_on_one_line_collide_but_shared_extension_lines_do_not() -> None:
    def dim(owner: str, line_y: float, x1: float) -> collide.Ink:
        return _ink(
            owner,
            model.Line(0.0, line_y, x1, line_y, model.DIMENSIONS),
            model.Solid(((0.0, line_y), (3.0, line_y + 0.9), (3.0, line_y - 0.9))),
            model.Line(0.0, 0.0, 0.0, line_y + 2, model.DIMENSIONS),  # the extension line at the datum, shared
        )

    assert collide.collisions([dim("a", -10.0, 50.0), dim("b", -17.0, 80.0)]) == []
    assert [c.kind for c in collide.collisions([dim("a", -10.0, 50.0), dim("b", -10.2, 80.0)])] == ["dimension_lines"]


def test_a_text_over_the_outline_of_a_view_collides_and_the_outline_under_a_leader_does_not() -> None:
    view = _ink(
        "views", model.Line(0.0, 0.0, 100.0, 0.0, model.CONTOUR), model.Line(100.0, 0.0, 100.0, 60.0, model.CONTOUR)
    )
    assert [c.kind for c in collide.collisions([_ink("n", model.Text(80.0, -1.0, "texto", 3.5))], [view])] == [
        "text_line"
    ]
    assert (
        collide.collisions(
            [_ink("n", model.Text(80.0, 20.0, "texto", 3.5)), _ink("l", model.Line(50.0, -5.0, 50.0, 5.0))], [view]
        )
        == []
    )


def test_centre_lines_and_the_title_block_are_under_everything() -> None:
    centre = _ink("vl", model.Line(0.0, 10.0, 100.0, 10.0, model.AXES))
    text = _ink("n", model.Text(40.0, 9.0, "texto", 3.5))
    assert collide.collisions([centre, text]) == []


def test_segments_are_tested_against_the_inside_of_a_box() -> None:
    box = (0.0, 0.0, 10.0, 10.0)
    assert collide.segment_hits_rect((-5.0, 5.0, 15.0, 5.0), box)
    assert not collide.segment_hits_rect((-5.0, 12.0, 15.0, 12.0), box)
    assert not collide.segment_hits_rect((10.0, -5.0, 10.0, 15.0), box)  # along an edge: not through the inside


# --- a label slides along the face it points at ----------------------------------------------------------------------


def test_a_label_may_touch_a_plane_at_its_own_tip_first_and_then_slide_along_its_trace() -> None:
    view = ViewInfo("top", 0.0, 0.0, 1.0, (0, 0, 1), (1, 0, 0), (0.0, 0.0, 0.0), (0.0, 0.0, 60.0, 60.0))
    plane = attach.Attach(view, (60.0, 30.0), (1.0, 0.0), "plane", trace=((60.0, 2.0), (60.0, 58.0)))
    tips = plane.tips()
    assert tips[0] == (60.0, 30.0) and len(tips) == len(attach.SLIDES)
    assert {round(t[0], 6) for t in tips} == {60.0} and {round(t[1], 1) for t in tips[1:]} == {16.0, 44.0, 8.7, 51.3}
    assert attach.Attach(view, (60.0, 30.0), (1.0, 0.0), "plane").tips() == [(60.0, 30.0)]  # no trace: one place


# --- a sheet with fewer views ----------------------------------------------------------------------------------------


def test_dropping_the_right_view_frees_the_room_and_the_scale_grows() -> None:
    a4 = layout.PAGES_MM["A4"]
    four = layout.plan(100.0, 60.0, 19.0, *a4)
    three = layout.plan(100.0, 60.0, 19.0, *a4, has_right=False)
    assert (four.scale, three.scale) == (0.5, 1.0)
    assert three.front[0] < four.front[0]  # the front view moved left: nothing is to its left any more
    plain = layout.plan(100.0, 60.0, 19.0, *a4, isometric=False, has_top=False, has_right=False)
    assert plain.scale == 2.0 and plain.fits


def test_the_default_layout_is_what_it_was() -> None:
    a3 = layout.PAGES_MM["A3"]
    assert layout.plan(100.0, 60.0, 19.0, *a3) == layout.plan(100.0, 60.0, 19.0, *a3, has_top=True, has_right=True)


# --- the review of the views -----------------------------------------------------------------------------------------


def _view(name: str) -> ViewInfo:
    return ViewInfo(name, 0, 0, 1.0, (0, 0, 1), (1, 0, 0), (0.0, 0.0, 0.0), (0, 0, 10, 10))


def _views_review(
    types: dict[str, str], use: dict[str, set[str]], names: tuple[str, ...] = ("front", "top", "right")
) -> list[review.Finding]:
    """The ``view_adds_nothing`` findings of a sheet with views ``names`` where the items ``types`` (id -> item type)
    carry something on the views of ``use``."""
    ctx = Context(model.Sheet(297, 210), {n: _view(n) for n in names}, [], None, (0, 0, 1, 1))  # type: ignore[arg-type]
    ctx.extras["view_use"] = use
    items = [{"id": i, "type": t} for i, t in types.items()]
    return review._unused_views(review.ReviewInput(items, {}, ctx, model.Sheet(297, 210)))


def test_a_view_with_no_dimension_is_flagged_whatever_it_shows() -> None:
    found = _views_review({"d1": "dim"}, {"front": {"d1"}, "top": {"d1"}})
    assert [(f.code, f.severity, f.items) for f in found] == [("view_adds_nothing", "warning", ("right",))]
    assert "no lleva ninguna cota" in found[0].message and "remove_view" in found[0].message


def test_a_dimension_a_hole_callout_or_a_balloon_gives_a_view_its_job() -> None:
    use = {"front": {"d"}, "top": {"h"}, "right": {"b"}}
    assert _views_review({"d": "dim", "h": "hole_dim", "b": "balloons"}, use) == []
    assert _views_review({"d": "overall", "h": "datum_dims", "b": "gdt"}, use) == []


def test_centre_lines_notes_and_hidden_line_passes_do_not_give_a_view_a_job() -> None:
    types = {"d": "dim", "l": "line", "v": "view_lines", "n": "note"}
    found = _views_review(types, {"front": {"d"}, "top": {"l", "v"}, "right": {"n"}})
    assert [f.items for f in found] == [("top",), ("right",)]


def test_a_view_holding_the_cut_line_or_detail_circle_of_a_section_stays_quiet() -> None:
    types = {"s": "section", "e": "detail", "d": "dim"}
    assert _views_review(types, {"front": {"s"}, "top": {"e"}, "right": {"d"}}) == []


def test_the_isometric_view_is_never_flagged_but_a_section_with_no_dimension_is() -> None:
    ctx = Context(model.Sheet(297, 210), {"front": _view("front")}, [], None, (0, 0, 1, 1))  # type: ignore[arg-type]
    ctx.extras["views_extra"] = {"isometric": _view("isometric")}
    ctx.extras["sections"] = {"A-A": _view("A-A")}
    ctx.extras["section_items"] = {"A-A": "s1"}
    ctx.extras["view_use"] = {"front": {"s1"}}
    items = [{"id": "s1", "type": "section"}]
    found = review._unused_views(review.ReviewInput(items, {}, ctx, model.Sheet(297, 210)))
    assert [f.items for f in found] == [("A-A",)]
    assert "s1" in found[0].message and "remove" in found[0].message  # a section goes with its item, not remove_view
    ctx.extras["view_use"]["A-A"] = {"d9"}
    items.append({"id": "d9", "type": "dim"})
    assert review._unused_views(review.ReviewInput(items, {}, ctx, model.Sheet(297, 210))) == []


def test_a_sheet_with_one_view_is_not_flagged_and_the_message_follows_the_language() -> None:
    assert _views_review({}, {}, ("top",)) == []
    ctx = Context(model.Sheet(297, 210), {"front": _view("front"), "top": _view("top")}, [], None, (0, 0, 1, 1))  # type: ignore[arg-type]
    ctx.language = "en"
    found = review._unused_views(review.ReviewInput([], {}, ctx, model.Sheet(297, 210)))
    assert len(found) == 2 and "carries no dimension" in found[0].message


# --- dimensions on top of each other: the collision the review reports ------------------------------------------------


def _h_dimension(owner: str, x0: float, x1: float, line: float, text: str) -> collide.Ink:
    entities, _ = draw.linear_dimension((x0, 0.0), (x1, 0.0), "h", line, text)
    return collide.ink_of(owner, entities)


def _collision_findings(found: list[collide.Collision], *ids: str) -> list[review.Finding]:
    ctx = Context(model.Sheet(297, 210), {}, [], None, (0, 0, 1, 1))  # type: ignore[arg-type]
    ctx.extras["collisions"] = found
    items = [{"id": i, "type": "dim"} for i in ids]
    return review._collisions(review.ReviewInput(items, {}, ctx, model.Sheet(297, 210)))


def test_the_text_of_one_dimension_over_the_text_of_another_is_a_collision_finding() -> None:
    a = _h_dimension("a", 0.0, 40.0, -10.0, "40")
    b = _h_dimension("b", 5.0, 35.0, -10.8, "30")  # texts one on the other, lines 0.8 mm apart
    found = collide.collisions([a, b])
    assert "text_text" in {c.kind for c in found}
    findings = _collision_findings(found, "a", "b")
    assert [(f.code, f.items) for f in findings][0] == ("collision", ("b", "a"))


def test_two_dimension_lines_on_one_line_and_overlapping_are_a_collision_finding() -> None:
    a = _h_dimension("a", 0.0, 40.0, -10.0, "40")
    b = _h_dimension("b", 30.0, 80.0, -10.0, "50")
    found = collide.collisions([a, b])
    assert "dimension_lines" in {c.kind for c in found}
    assert {f.code for f in _collision_findings(found, "a", "b")} == {"collision"}
    c = _h_dimension("c", 45.0, 80.0, -10.0, "35")  # the same line, but a gap between them: no overlap
    assert "dimension_lines" not in {x.kind for x in collide.collisions([a, c])}


def test_the_text_of_a_dimension_over_the_line_of_another_is_a_collision_finding() -> None:
    a = _h_dimension("a", 0.0, 60.0, -10.0, "60")
    b = _h_dimension("b", 10.0, 50.0, -13.2, "40")  # its text (just above its line) is crossed by a's line
    found = collide.collisions([a, b])
    assert {c.kind for c in found} & {"line_text", "text_line"}
    assert _collision_findings(found, "a", "b")
    far = _h_dimension("b", 10.0, 50.0, -20.0, "40")
    assert collide.collisions([a, far]) == []


def test_a_dimension_the_engineer_put_on_the_page_by_hand_gives_the_view_its_job() -> None:
    ctx = Context(model.Sheet(297, 210), {"front": _view("front"), "top": _view("top")}, [], None, (0, 0, 1, 1))  # type: ignore[arg-type]
    data = review.ReviewInput([], {}, ctx, model.Sheet(297, 210))
    assert [f.items for f in review._unused_views(data)] == [("front",), ("top",)]
    ctx.extras["hand_marks"] = {"top": 1}  # plain data the worker read from the TechDraw page
    assert [f.items for f in review._unused_views(data)] == [("front",)]
