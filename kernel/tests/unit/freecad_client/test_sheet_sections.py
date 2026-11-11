# ruff: noqa: E501
"""A section is a view the other items can name (``view="A-A"``): its dimensions, thread callout, notes and axes, the lines
of ISO 128 types, and the text of a callout checked against the measured hole -- on the hand-made plate of ``test_sheet_items``
with the canned projections of ``test_sheet_views``. No FreeCAD."""

import sys
from pathlib import Path
from typing import Any

import pytest

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import sheet_callout  # noqa: E402
import sheet_context as sc  # noqa: E402
import sheet_model as model  # noqa: E402
import sheet_review as review  # noqa: E402
import sheet_thread  # noqa: E402

from .test_sheet_items import draw, hole  # noqa: E402
from .test_sheet_views import ViewsGeometry, views_context  # noqa: E402

SECTION = {"type": "section", "id": "sec", "holes": "all", "view": "top"}


def two_bores(**fields: Any) -> sc.Context:
    return views_context([hole(0, 10, 10, **fields), hole(1, 90, 10, **fields)])


def by_id(results: list[sc.ItemResult]) -> dict[str, sc.ItemResult]:
    return {r.id: r for r in results}


# --- the section as a view -----------------------------------------------------------------------------------------------


def test_a_section_registers_itself_as_a_derived_view_with_its_centre_lines() -> None:
    ctx = two_bores()
    (result,) = draw(ctx, SECTION)
    assert result.status == "ok" and "2 centre line(s)" in result.detail, result.detail
    view = ctx.view("A-A")
    assert view.derived and view.name == "A-A" and view.scale == 1.0
    assert "A-A" not in ctx.views and ctx.find_view("A-A") is view and ctx.find_view("B-B") is None
    rect = ctx.extras["view_rects"]["sec"]
    assert rect[0] < view.box[0] and view.box[2] < rect[2]
    axes = [e for e in ctx.sheet.of_layer(model.AXES) if isinstance(e, model.Line) and abs(e.x1 - e.x2) < 1e-6]
    assert len(axes) == 2 and all(
        view.box[1] - 2.1 <= min(e.y1, e.y2) for e in axes
    )  # a chain line along each bore, 2 mm past it


def test_a_view_that_does_not_exist_is_said_with_the_way_to_make_it() -> None:
    with pytest.raises(sc.ItemError, match="a section view exists once its section item is on the sheet"):
        views_context().view("A-A")
    ctx = views_context()
    (result,) = draw(ctx, {"type": "hole_dim", "id": "h", "all": True, "view": "B-B"})
    assert result.status == "error" and "B-B" in result.detail


def test_items_on_a_section_are_drawn_after_it_whatever_the_order_of_the_list() -> None:
    ctx = two_bores()
    results = by_id(draw(ctx, {"type": "hole_dim", "id": "dia", "all": True, "view": "A-A"}, SECTION))
    assert results["sec"].status == "ok" and results["dia"].status == "ok", results
    assert "Ø6,6 pasante" in ctx.sheet.texts()


def test_the_view_field_of_the_other_items_still_takes_only_the_standard_views_where_it_is_not_meant_for_sections() -> (
    None
):
    for spec in (
        {"type": "datum_dims", "view": "A-A"},
        {"type": "balloons", "view": "A-A"},
    ):
        with pytest.raises(sc.ItemError, match="view"):
            import sheet_items as items

            items.normalise(spec, set(), 1)


# --- dimensions on the section ---------------------------------------------------------------------------------------------


def test_a_bore_is_dimensioned_across_its_walls_with_the_measured_diameter() -> None:
    ctx = two_bores()
    results = by_id(draw(ctx, SECTION, {"type": "hole_dim", "id": "dia", "all": True, "view": "A-A"}))
    assert results["dia"].status == "ok" and "A-A:" in results["dia"].detail
    view = ctx.view("A-A")
    texts = [e for e in ctx.sheet.entities if isinstance(e, model.Text) and e.text == "Ø6,6 pasante"]
    assert len(texts) == 2  # one dimension per bore: a bore is shown once, never counted as "2x"
    arrows = [e for e in ctx.sheet.entities if isinstance(e, model.Solid)]
    assert len(arrows) >= 4
    # the dimension line is above the section (the label hangs below it) and 6.6 mm across the bore
    spans = sorted(
        {
            round(abs(a.points[0][0] - b.points[0][0]), 1)
            for a in arrows
            for b in arrows
            if a is not b and abs(a.points[0][1] - b.points[0][1]) < 1e-6
        }
    )
    assert 6.6 in spans
    assert all(t.y > view.box[3] for t in texts)
    assert "dia" not in ctx.extras.get("natives", {})  # a section has no TechDraw view to measure in


def test_a_threaded_bore_is_dimensioned_at_its_major_diameter_with_the_thread_lines_in_the_wall() -> None:
    ctx = views_context([hole(0, 10, 10, diameter=6.8, thread=("M8", None))])
    results = by_id(draw(ctx, SECTION, {"type": "hole_dim", "id": "dia", "all": True, "view": "A-A"}))
    assert "thread lines of 1 threaded bore" in results["sec"].detail
    lines = [e for e in ctx.sheet.of_layer(model.THREAD) if isinstance(e, model.Line)]
    assert len(lines) == 2  # the thin lines at the major diameter, one on each side of the axis
    assert all(abs(abs(e.x1 - e.x2)) < 1e-6 for e in lines) and abs(abs(lines[0].x1 - lines[1].x1) - 8.0) < 1e-6
    assert any("M8" in t for t in ctx.sheet.texts())
    arrows = [e for e in ctx.sheet.entities if isinstance(e, model.Solid)]
    xs = sorted({round(a.points[0][0], 2) for a in arrows if abs(a.points[0][1] - arrows[-1].points[0][1]) < 1e-6})
    assert abs(xs[-1] - xs[0] - 8.0) < 1e-6, xs  # the dimension spans the thread's 8 mm, not the 6.8 tap drill


def test_holes_that_are_not_on_one_line_cannot_share_a_cutting_plane_and_the_refusal_says_which_to_name() -> None:
    """The refusal P1 met on a plate: four holes at the corners of a rectangle have no plane through all of them, so
    ``holes="all"`` is refused -- rightly -- and the message names the way out; two of them on one line are cut."""
    ctx = views_context()  # the plate's four holes
    (refused,) = draw(ctx, {"type": "section", "id": "sec", "holes": "all", "view": "top"})
    assert (
        refused.status == "error" and "not on one straight line" in refused.detail and "holes=[...]" in refused.detail
    )
    assert "A-A" not in ctx.extras.get("sections", {})  # nothing of the refused section is left on the sheet
    again = views_context()
    (cut,) = draw(again, {"type": "section", "id": "sec", "holes": [[10, 10, 8], [90, 10, 8]], "view": "top"})
    assert cut.status == "ok" and "2 centre line(s)" in cut.detail, cut.detail


def test_a_hole_the_plane_does_not_run_along_is_said_and_skipped() -> None:
    ctx = views_context([hole(0, 10, 10), hole(1, 10, 50)])
    ctx.extras["_"] = None
    results = by_id(
        draw(
            ctx,
            {"type": "section", "id": "sec", "holes": [[10, 10, 8]], "view": "top"},
            {"type": "hole_dim", "id": "dia", "all": True, "view": "A-A"},
        )
    )
    assert results["dia"].status == "warning" and "does not run along its axis" in results["dia"].detail


def test_a_length_between_two_faces_in_a_section_starts_at_the_end_of_each_face_and_makes_no_native_dimension() -> None:
    ctx = two_bores()
    results = by_id(
        draw(
            ctx,
            SECTION,
            {
                "type": "dim",
                "id": "len",
                "kind": "distance",
                "from": {"face": "face:bottom"},
                "to": {"face": "face:top"},
                "view": "A-A",
                "along": "z",
            },
        )
    )
    assert results["len"].status == "ok" and "8 mm along z in the A-A view" in results["len"].detail, results[
        "len"
    ].detail
    assert "len" not in ctx.extras.get("natives", {})
    assert "8" in ctx.sheet.texts()


# --- the note on a view ------------------------------------------------------------------------------------------------------


def test_a_note_on_a_section_is_written_under_it_and_is_not_numbered() -> None:
    ctx = two_bores()
    results = by_id(
        draw(
            ctx,
            SECTION,
            {"type": "note", "id": "n1", "text": "Rosca M8 en toda la longitud", "view": "A-A"},
            {"type": "note", "id": "n2", "text": "Otra nota del bloque"},
        )
    )
    assert results["n1"].status == "ok" and results["n1"].detail == "note under A-A"
    rect = ctx.extras["view_rects"]["sec"]
    where = [e for e in ctx.sheet.entities if isinstance(e, model.Text) and e.text == "Rosca M8 en toda la longitud"]
    assert len(where) == 1 and where[0].y < rect[1]
    assert (
        "1. Otra nota del bloque" in ctx.sheet.texts()
    )  # the note of the block is number 1: the view note takes no number


def test_a_note_cannot_name_a_standard_view_nor_give_view_and_a_position() -> None:
    import sheet_items as items

    with pytest.raises(sc.ItemError, match="label of a section view"):
        items.normalise({"type": "note", "text": "x", "view": "top"}, set(), 1)
    with pytest.raises(sc.ItemError, match="view or at_mm"):
        items.normalise({"type": "note", "text": "x", "view": "A-A", "at_mm": [10, 10]}, set(), 1)


# --- the text of a callout and the measured hole ----------------------------------------------------------------------------


def stack_of(diameter: float, depth: float = 30.0, thread: tuple[str, float | None] | None = None) -> list[sc.HoleInfo]:
    return [hole(0, 0, 0, diameter=diameter, depth=depth, thread=thread, declared=thread is not None)]


def test_a_thread_text_that_agrees_with_a_plain_bore_of_the_tap_drill_says_nothing() -> None:
    ctx = views_context()
    assert sheet_callout.problems(ctx, "M45x1,5 - 6H", stack_of(43.5)) == []
    assert sheet_callout.problems(ctx, "M45x1.5 - 6H pasante", stack_of(43.5)) == []
    assert sheet_callout.problems(ctx, "M12", stack_of(10.2)) == []
    assert (
        sheet_callout.problems(ctx, "Ø43,5 H7 (+0,025/0)", stack_of(43.5)) == []
    )  # the class and the deviations are not geometry


def test_a_thread_text_whose_tap_drill_is_not_the_bore_says_what_the_bore_measures() -> None:
    ctx = views_context()
    (found,) = sheet_callout.problems(ctx, "M45x1,5 - 6H", stack_of(42.0))
    assert "M45x1,5" in found and "Ø43,5" in found and "Ø42" in found
    english = views_context()
    english.language = "en"
    assert "tap drill is Ø43.5" in sheet_callout.problems(english, "M45x1.5", stack_of(42.0))[0]


def test_a_thread_text_against_a_hole_made_with_another_thread_contradicts_the_model() -> None:
    ctx = views_context()
    (found,) = sheet_callout.problems(ctx, "M42x1,5", stack_of(43.5, thread=("M45x1.5", None)))
    assert "M42x1,5" in found and "M45x1,5" in found and "se hizo como" in found


def test_numbers_the_hole_does_not_have_and_a_wrong_count_are_each_said() -> None:
    ctx = views_context()
    found = sheet_callout.problems(ctx, "4x Ø44 ↧ 30", stack_of(43.5, depth=30.0), count=1)
    assert len(found) == 2 and "4x" in found[0] and "44" in found[1]
    assert sheet_callout.problems(ctx, "4x Ø43,5 ↧ 30", stack_of(43.5, depth=30.0), count=4) == []
    unknown = sheet_callout.problems(ctx, "M44x1,5", stack_of(42.5))
    assert len(unknown) == 1 and "no está en la tabla" in unknown[0]


def test_a_hole_dim_text_is_the_callout_and_the_item_warns_when_it_contradicts() -> None:
    ctx = views_context([hole(0, 10, 10, diameter=43.5, depth=8.0, through=True)])
    (ok,) = draw(ctx, {"type": "hole_dim", "id": "t", "all": True, "text": "M45x1,5 - 6H"})
    assert ok.status == "ok" and "M45x1,5 - 6H" in ctx.sheet.texts()
    bad_ctx = views_context([hole(0, 10, 10, diameter=43.5, depth=8.0, through=True)])
    (bad,) = draw(bad_ctx, {"type": "hole_dim", "id": "t", "all": True, "text": "M45x2 - 6H"})
    assert bad.status == "warning" and "el texto contradice el modelo" in bad.detail and "M45x2" in bad.detail
    assert "M45x2 - 6H" in bad_ctx.sheet.texts()  # still drawn as written: the engineer's wording, with the warning


def test_the_review_holds_a_sheet_whose_callout_text_contradicts_the_model() -> None:
    ctx = views_context([hole(0, 10, 10, diameter=43.5, depth=8.0, through=True)])
    specs = [{"id": "t", "type": "hole_dim", "all": True, "text": "M45x2 - 6H"}]
    results = {"t": sc.ItemResult("t", "hole_dim", "ok")}
    found = review.review(review.ReviewInput(specs, results, ctx, ctx.sheet))
    contradictions = [f for f in found if f.code == "callout_contradicts"]
    assert len(contradictions) == 1 and contradictions[0].severity == "warning" and contradictions[0].items == ("t",)
    specs[0]["text"] = "M45x1,5 - 6H"
    found = review.review(review.ReviewInput(specs, results, ctx, ctx.sheet))
    assert not [f for f in found if f.code == "callout_contradicts"]


@pytest.mark.parametrize(
    "spec",
    [
        {"type": "hole_dim", "all": True, "text": "M45", "fit": "H7"},
        {"type": "hole_dim", "all": True, "text": ""},
    ],
)
def test_a_hole_dim_text_takes_no_fit_and_is_not_empty(spec: dict[str, Any]) -> None:
    import sheet_items as items

    with pytest.raises(sc.ItemError):
        items.normalise(spec, set(), 1)


def test_a_text_for_several_groups_of_holes_is_refused() -> None:
    ctx = views_context([hole(0, 10, 10, diameter=6.6), hole(1, 90, 10, diameter=9.0)])
    (result,) = draw(ctx, {"type": "hole_dim", "id": "t", "all": True, "text": "x"})
    assert result.status == "error" and "ONE hole" in result.detail


def test_a_declared_thread_on_a_plain_bore_must_be_the_one_its_diameter_says() -> None:
    ctx = views_context([hole(0, 10, 10, diameter=43.5, depth=8.0, through=True, declared=False)])
    (ok,) = draw(ctx, {"type": "hole_dim", "id": "t", "all": True, "thread": "M45x1,5"})
    assert ok.status == "ok" and "M45x1,5 pasante" in ctx.sheet.texts()
    assert [
        e for e in ctx.sheet.of_layer(model.THREAD) if isinstance(e, model.Arc)
    ]  # the thin arc of the thread in the top view
    wrong = views_context([hole(0, 10, 10, diameter=42.0, depth=8.0, through=True, declared=False)])
    (bad,) = draw(wrong, {"type": "hole_dim", "id": "t", "all": True, "thread": "M45x1.5"})
    assert bad.status == "error" and "drilled at Ø43,5" in bad.detail and "measures Ø42" in bad.detail
    unknown = views_context([hole(0, 10, 10, diameter=43.5, depth=8.0, through=True, declared=False)])
    (nope,) = draw(unknown, {"type": "hole_dim", "id": "t", "all": True, "thread": "M45x1.7"})
    assert nope.status == "error" and "not in the ISO 261 thread table" in nope.detail
    made = views_context([hole(0, 10, 10, diameter=43.5, depth=8.0, through=True, thread=("M45x1.5", None))])
    (clash,) = draw(made, {"type": "hole_dim", "id": "t", "all": True, "thread": "M45x2"})
    assert clash.status == "error" and "contradicts the model" in clash.detail


def test_the_sheet_reads_the_one_thread_table_of_the_contract_by_path() -> None:
    assert sheet_thread.thread_table("M45x1,5") == ("M45x1.5", 43.5)
    assert sheet_thread.thread_table("M8") == ("M8", 6.8)
    assert sheet_thread.thread_table("M7") is None
    table = (WORKER_DIR.parents[2] / "contract" / "threads.py").read_text(encoding="utf-8")
    assert "import pydantic" not in table and "from pydantic" not in table  # the worker's Python has none


# --- ISO 128 lines -----------------------------------------------------------------------------------------------------------


def test_an_axis_is_a_chain_line_along_the_cylinder_and_a_cross_where_it_is_seen_end_on() -> None:
    ctx = views_context()
    (result,) = draw(ctx, {"type": "line", "id": "ax", "axis": "face:fillet"})
    assert result.status == "ok" and "chain line of the axis of face:fillet in top, front, right" in result.detail
    axes = [e for e in ctx.sheet.of_layer(model.AXES) if isinstance(e, model.Line)]
    assert (
        len(axes) == 4
    )  # the cross of the top view (it looks along the axis: two lines) and one line in front and right


def test_the_three_line_types_are_the_iso_128_layers() -> None:
    ctx = views_context()
    draw(
        ctx,
        {"type": "line", "id": "a", "axis": "face:fillet", "view": "front", "style": "chain"},
        {"type": "line", "id": "b", "axis": "face:fillet", "view": "front", "style": "dashed"},
        {"type": "line", "id": "c", "axis": "face:fillet", "view": "front", "style": "continuous"},
    )
    assert model.LINETYPES["CENTER"] == (6.0, -0.75, 0.125, -0.75) and model.LAYERS[model.AXES].linetype == "CENTER"
    assert model.LAYERS[model.HIDDEN].linetype == "HIDDEN" and model.LAYERS[model.DIMENSIONS].linetype == "CONTINUOUS"
    layers = {e.layer for e in ctx.sheet.entities if isinstance(e, model.Line)}
    assert {model.AXES, model.HIDDEN, model.DIMENSIONS} <= layers


def test_a_line_between_two_places_of_the_part_is_thin_continuous_unless_a_style_is_asked() -> None:
    ctx = views_context()
    (result,) = draw(
        ctx, {"type": "line", "id": "l", "from": {"at": [10, 10, 8]}, "to": {"at": [90, 10, 8]}, "view": "top"}
    )
    assert result.status == "ok" and "continuous line" in result.detail
    line = next(e for e in ctx.sheet.entities if isinstance(e, model.Line) and e.layer == model.DIMENSIONS)
    assert abs(abs(line.x2 - line.x1) - 80.0) < 1e-6 and abs(line.y1 - line.y2) < 1e-6
    chained = views_context()
    draw(
        chained,
        {
            "type": "line",
            "id": "l",
            "from": {"at": [10, 10, 8]},
            "to": {"at": [90, 10, 8]},
            "view": "top",
            "style": "chain",
            "extend_mm": 3,
        },
    )
    long = next(e for e in chained.sheet.entities if isinstance(e, model.Line) and e.layer == model.AXES)
    assert abs(abs(long.x2 - long.x1) - 86.0) < 1e-6


@pytest.mark.parametrize(
    ("spec", "text"),
    [
        ({"type": "line"}, "axis"),
        ({"type": "line", "axis": "face:fillet", "from": {"at": [1, 1, 1]}, "to": {"at": [2, 2, 2]}}, "not both"),
        ({"type": "line", "from": {"at": [1, 1, 1]}}, "both from and to"),
        ({"type": "line", "axis": ["face:1", "face:2"]}, "one face id"),
        ({"type": "line", "axis": "face:1", "style": "dotted"}, "chain, dashed, continuous"),
    ],
)
def test_a_bad_line_says_what_to_change(spec: dict[str, Any], text: str) -> None:
    import sheet_items as items

    with pytest.raises(sc.ItemError, match=text):
        items.normalise(spec, set(), 1)


def test_an_axis_needs_a_cylinder_and_a_line_needs_two_different_places() -> None:
    ctx = views_context()
    results = by_id(
        draw(
            ctx,
            {"type": "line", "id": "p", "axis": "face:top"},
            {"type": "line", "id": "q", "from": {"at": [10, 10, 8]}, "to": {"at": [10, 10, 8]}},
        )
    )
    assert results["p"].status == "error" and "needs a cylindrical face" in results["p"].detail
    assert results["q"].status == "error" and "same point" in results["q"].detail


# --- a diameter ends where the surface is ------------------------------------------------------------------------------------


class BossGeometry(ViewsGeometry):
    """A boss Ø40 on the plate whose face has been cut away in the middle (a flat took it): the centre of mass of the face
    is in the air, the surface exists only from z = 0 to z = 3 on the side that faces the viewer."""

    def __init__(self) -> None:
        super().__init__()
        self.faces["face:boss"] = sc.FaceInfo(
            "face:boss",
            "cylinder",
            (50.0, 10.0, 6.0),
            radius=20.0,
            axis_point=(50.0, 10.0, 0.0),
            axis_dir=(0.0, 0.0, 1.0),
            surface_point=(70.0, 10.0, 6.0),
        )

    def cylinder_span(self, face_id: str, across: sc.Vec3) -> tuple[float, float] | None:
        return (0.0, 3.0)


def boss_context() -> sc.Context:
    ctx = two_bores()
    ctx.geometry = BossGeometry()
    return ctx


def test_a_leader_to_a_cylinder_touches_the_silhouette_where_the_surface_is_not_where_the_face_has_its_centre() -> None:
    ctx = boss_context()
    results = by_id(draw(ctx, {"type": "dim", "id": "od", "kind": "diameter", "face": "face:boss", "view": "front"}))
    assert results["od"].status == "ok", results["od"].detail
    front = ctx.views["front"]
    (leader,) = ctx.extras["leaders"]
    # the middle of the stretch (0, 3), not z = 6 where the centre of the face is
    expected = front.project((50.0, 10.0, 1.5))[1]
    assert abs(leader["tip"][1] - expected) < 1e-6, (leader["tip"], expected)


def test_the_diameter_in_a_section_is_a_linear_dimension_between_its_silhouette_lines_with_the_o_prefix() -> None:
    ctx = boss_context()
    results = by_id(
        draw(ctx, SECTION, {"type": "dim", "id": "od", "kind": "diameter", "face": "face:boss", "view": "A-A"})
    )
    assert results["od"].status == "ok" and "between its two silhouette lines" in results["od"].detail, results[
        "od"
    ].detail
    assert "Ø40" in ctx.sheet.texts() and not ctx.extras.get("leaders")  # no leader that ends in the air
    view = ctx.view("A-A")
    arrows = [e for e in ctx.sheet.entities if isinstance(e, model.Solid)]
    lines = [e for e in ctx.sheet.of_layer(model.DIMENSIONS) if isinstance(e, model.Line) and abs(e.y1 - e.y2) < 1e-6]
    spans = [round(abs(e.x2 - e.x1), 2) for e in lines]
    assert 40.0 in spans, spans  # the dimension line runs between the two silhouette lines, 40 mm apart on the page
    assert arrows and all(a.points[0][1] > view.box[3] or a.points[0][1] < view.box[1] for a in arrows[-2:])
    assert "od" not in ctx.extras.get("natives", {})  # a section has no TechDraw view to measure in


# --- a section through the axis of a part of revolution ------------------------------------------------------------------------


class RoundGeometry(ViewsGeometry):
    def round_extent(self) -> tuple[sc.Vec3, float]:
        return (0.0, 0.0, 1.0), 60.0


def test_a_section_through_the_axis_of_a_round_part_needs_no_hole_and_goes_through_the_middle_of_the_part() -> None:
    ctx = views_context([])
    ctx.geometry = RoundGeometry()
    ctx.extras["model_box"] = ((20.0, 0.0, 0.0), (80.0, 60.0, 8.0))  # a round part: the axis is the middle of its box
    (result,) = draw(ctx, {"type": "section", "id": "sec", "holes": "axis"})
    assert result.status == "ok" and "through the axis of the part" in result.detail, result.detail
    at, normal = ctx.extras["section_planes"]["A-A"]
    assert (at[0], at[1]) == (50.0, 30.0) and abs(
        normal[2]
    ) < 1e-9  # the plane contains the axis: its normal is across it
    assert ctx.find_view("A-A") is not None and ctx.extras["view_use"]["top"] == {"sec"}


def test_a_section_through_the_axis_of_a_part_that_is_not_round_says_what_to_use_instead() -> None:
    ctx = views_context([])
    (result,) = draw(ctx, {"type": "section", "id": "sec", "holes": "axis"})
    assert result.status == "error" and "no axis of revolution" in result.detail and "holes=[...]" in result.detail
