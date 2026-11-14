# ruff: noqa: E501
"""A turned part goes on the sheet with its axis horizontal (as it is machined), alone in ONE main view unless an end view
shows something; the end view that only repeats circles is flagged; an overall dimension that drew nothing on a view does not
count as that view's job; the parts list prints a designation, never a file name."""

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"))

import assembly_names as names  # noqa: E402
import sheet_context as sc  # noqa: E402
import sheet_items as items  # noqa: E402
import sheet_model as model  # noqa: E402
import sheet_review as review  # noqa: E402
import sheet_turned as turned  # noqa: E402
from sheet_context import Context, ViewInfo  # noqa: E402

from .test_sheet_assembly import context, reviewed  # noqa: E402
from .test_sheet_items import draw, hole  # noqa: E402
from .test_sheet_views import ViewsGeometry, views_context  # noqa: E402


def up_of(direction: tuple[float, float, float], xdir: tuple[float, float, float]) -> tuple[float, float, float]:
    """The page's up in the model for a view looking along ``direction`` with page-x ``xdir`` (what ``ViewInfo.ydir`` is)."""
    d, r = direction, xdir
    return (d[1] * r[2] - d[2] * r[1], d[2] * r[0] - d[0] * r[2], d[0] * r[1] - d[1] * r[0])


# --- the orientation ----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("axis", [(1.0, 0.0, 0.0), (0.0, -1.0, 0.0), (0.0, 0.0, 1.0)])
def test_the_axis_runs_along_the_page_whatever_axis_the_part_was_modelled_on(axis: tuple[float, float, float]) -> None:
    direction, xdirection = turned.frame(axis)
    assert abs(sum(a * b for a, b in zip(xdirection, axis, strict=True))) == 1.0  # the page's x is the axis
    assert abs(sum(a * b for a, b in zip(direction, axis, strict=True))) == 0.0  # the line of sight is across it
    up = up_of(direction, xdirection)
    assert abs(sum(a * b for a, b in zip(up, axis, strict=True))) == 0.0  # and the page's up is across it too


def test_a_shaft_modelled_standing_on_z_is_the_front_view_turned_a_quarter_not_another_face() -> None:
    direction, xdirection = turned.frame((0.0, 0.0, 1.0))
    assert direction == (0.0, -1.0, 0.0) and xdirection == (0.0, 0.0, 1.0)  # still seen from the front: z to the right
    assert turned.frame((1.0, 0.0, 0.0)) == ((0.0, -1.0, 0.0), (1.0, 0.0, 0.0))  # already horizontal: the plain front view


def test_flipping_puts_the_other_end_on_the_right_without_mirroring_the_part() -> None:
    direction, xdirection = turned.frame((0.0, 0.0, 1.0), flip=True)
    assert xdirection == (0.0, 0.0, -1.0)
    plain_up = up_of(*turned.frame((0.0, 0.0, 1.0)))
    assert up_of(direction, xdirection) == plain_up  # up stays up: the view is seen from behind, not mirrored


def test_the_thread_end_goes_to_the_right() -> None:
    assert turned.thread_end_flip((0.0, 0.0, 1.0), (0.0, 60.0), [50.0]) is False  # thread near the high end: already right
    assert turned.thread_end_flip((0.0, 0.0, 1.0), (0.0, 60.0), [10.0]) is True  # thread near the low end: flip
    assert turned.thread_end_flip((0.0, 0.0, 1.0), (0.0, 60.0), []) is False  # no thread: the positive end stays right
    assert turned.thread_end_flip((0.0, 0.0, 1.0), (0.0, 60.0), [5.0, 55.0]) is False  # both ends: leave it


def test_the_layout_arranges_length_across_and_diameter_up() -> None:
    assert turned.layout_sizes(60.0, 12.0) == (60.0, 12.0, 12.0)


def test_a_plain_turned_part_is_one_view_and_only_a_keyway_adds_the_end_view() -> None:
    assert turned.views_for(False) == ["front"]
    assert turned.views_for(True) == ["front", "right"]


# --- the end view that adds nothing ------------------------------------------------------------------------------------------


class AxisGeometry(sc.Geometry):
    def round_extent(self) -> tuple[sc.Vec3, float] | None:
        return (0.0, 0.0, 1.0), 12.0


def end_view_review(carried: dict[str, set[str]], items: list[dict[str, str]]) -> list[review.Finding]:
    front = ViewInfo("front", 0, 0, 2.0, (0, -1, 0), (0, 0, 1), (0.0, 0.0, 0.0), (0, 0, 120, 24))
    end = ViewInfo("right", 0, 0, 2.0, (0, 0, 1), (1, 0, 0), (0.0, 0.0, 0.0), (0, 0, 24, 24))
    ctx = Context(model.Sheet(297, 210), {"front": front, "right": end}, [], AxisGeometry(), (0, 0, 1, 1))
    ctx.extras["view_use"] = carried
    return review._unused_views(review.ReviewInput(items, {}, ctx, model.Sheet(297, 210)))  # noqa: SLF001


def test_the_end_view_of_a_shaft_that_only_repeats_circles_is_flagged_and_the_message_says_why() -> None:
    items = [{"id": "o1", "type": "overall"}, {"id": "thr", "type": "dim"}]
    (found,) = end_view_review({"front": {"o1", "thr"}}, items)
    assert (found.code, found.items) == ("view_adds_nothing", ("right",))
    assert "vista de extremo de una pieza torneada" in found.message and "repite círculos" in found.message
    assert "chavetero" in found.message and "remove_view" in found.message


def test_the_same_view_with_a_dimension_of_its_own_stays_quiet() -> None:
    items = [{"id": "o1", "type": "overall"}, {"id": "k1", "type": "dim"}]
    assert end_view_review({"front": {"o1"}, "right": {"k1"}}, items) == []


def test_an_overall_dimension_that_drew_nothing_on_a_view_does_not_give_it_a_job() -> None:
    """Why the end view slipped through: ``overall`` marked the top view as used while skipping its depth (a diameter the
    height already gives). Only a dimension drawn on a view is its job."""
    import items_dims  # noqa: PLC0415

    source = Path(items_dims.__file__).read_text()
    skipped = source.index("if not (d_round and given):")
    assert source[skipped : skipped + 200].count('ctx.used_view("top")') == 1  # inside the branch that draws the depth
    assert 'if top is not None:\n        ctx.used_view("top")' not in source


# --- the section of an assembly -----------------------------------------------------------------------------------------------


def test_a_section_of_an_assembly_with_no_dimension_asks_what_it_shows_that_the_other_views_do_not() -> None:
    ctx = context()
    ctx.extras["sections"] = {"B-B": ViewInfo("B-B", 0, 0, 1.0, (0, 1, 0), (1, 0, 0), (0.0, 0.0, 0.0), (0, 0, 10, 10))}
    ctx.extras["section_items"] = {"B-B": "secB"}
    ctx.extras["view_use"] = {n: {"d"} for n in ctx.views}
    items = [{"id": "d", "type": "dim"}, {"id": "secB", "type": "section"}]
    found = [f for f in review._unused_views(review.ReviewInput(items, {}, ctx, ctx.sheet)) if f.items == ("B-B",)]  # noqa: SLF001
    assert len(found) == 1
    assert "plano de conjunto" in found[0].message and "el montaje" in found[0].message and "los ajustes" in found[0].message
    assert "by=, reason=" in found[0].message  # only the engineer's order, with what it shows


# --- the parts list: a designation, never a file name -------------------------------------------------------------------------


def test_the_designation_is_the_title_the_designer_gave_or_the_title_of_the_parts_sheet_and_never_a_file_name() -> None:
    assert names.designation("Placa superior", "Soporte") == "Placa superior"
    assert names.designation("", "Soporte 2") == "Soporte 2"
    assert names.designation(None, None) == ""  # nothing: the review asks the designer
    assert not hasattr(names, "display_name")  # the name of the document and of its file cannot be printed any more


def test_a_part_with_no_designation_is_a_question_to_the_designer_that_never_shows_the_file_name() -> None:
    ctx = context()
    ctx.assembly.groups[0].name = ""
    found = reviewed(ctx, {"type": "parts_list"}, {"type": "balloons", "view": "front"})
    (unnamed,) = [f for f in found if f.code == "part_unnamed"]
    assert unnamed.severity == "warning"
    assert "1 (comp:1)" in unnamed.message and "set_title" in unnamed.message and "no el nombre del archivo" in unnamed.message
    assert "placa_base" not in unnamed.message


def test_a_designation_written_in_the_list_closes_the_question() -> None:
    ctx = context()
    ctx.assembly.groups[0].name = ""
    rows = [{"component": "comp:1", "name": "Placa base"}]
    found = reviewed(ctx, {"type": "parts_list", "rows": rows}, {"type": "balloons", "view": "front"})
    assert "part_unnamed" not in [f.code for f in found]


def test_a_position_without_designation_is_named_by_its_component_in_the_messages() -> None:
    group = sc.PartGroup(2, "k", "", "", "", "", ["comp:9", "comp:10"])
    assert group.label == "(comp:9)"
    assert sc.PartGroup(2, "k", "Escuadra", "", "", "", ["comp:9"]).label == "Escuadra"


# --- the isometric view ------------------------------------------------------------------------------------------------------


def iso_findings(shown: list[str] | None) -> list[review.Finding]:
    import sheet_viewchecks as viewchecks  # noqa: PLC0415

    ctx = Context(model.Sheet(297, 210), {}, [], None, (0, 0, 1, 1))  # type: ignore[arg-type]
    if shown is not None:
        ctx.extras["sheet_views"] = shown
    return [f for f in viewchecks.check(review.ReviewInput([], {}, ctx, ctx.sheet)) if f.code == "no_isometric"]


def test_a_sheet_made_without_the_isometric_view_is_flagged_and_one_with_it_is_not() -> None:
    (found,) = iso_findings(["front"])
    assert found.severity == "warning" and "no lleva vista isométrica" in found.message and "by=, reason=" in found.message
    assert iso_findings(["front", "iso"]) == []
    assert iso_findings(None) == []  # a sheet whose views are not known (a test double) is not judged


def test_the_finding_holds_the_export_until_it_is_fixed() -> None:
    import sheet_checks as checks  # noqa: PLC0415

    rows = [{"code": "no_isometric"}]
    checks.assign_ids(rows)
    assert rows[0]["blocking"] is True


# --- callouts crowded at one spot ---------------------------------------------------------------------------------------------


def crowded(tips: list[tuple[str, tuple[float, float]]], view_width: float = 120.0) -> list[review.Finding]:
    import sheet_viewchecks as viewchecks  # noqa: PLC0415

    front = ViewInfo("front", 0, 0, 2.0, (0, -1, 0), (0, 0, 1), (0.0, 0.0, 0.0), (0, 0, view_width, min(view_width, 24)))
    ctx = Context(model.Sheet(297, 210), {"front": front}, [], None, (0, 0, 1, 1))  # type: ignore[arg-type]
    ctx.extras["leaders"] = [{"item": i, "tip": p, "elbow": p, "end": p} for i, p in tips]
    return [f for f in viewchecks.check(review.ReviewInput([], {}, ctx, ctx.sheet)) if f.code == "callouts_crowded"]


def test_two_callouts_jammed_at_the_end_of_a_long_view_are_flagged() -> None:
    (found,) = crowded([("thr", (118.0, 24.0)), ("rq_chamfers", (119.5, 24.0))])
    assert found.items == ("thr", "rq_chamfers") and "se amontonan" in found.message and "offset_mm" in found.message


def test_callouts_apart_or_with_no_room_beside_them_are_quiet() -> None:
    assert crowded([("thr", (90.0, 24.0)), ("rq_chamfers", (119.5, 24.0))]) == []  # the thread callout stands on the thread
    assert crowded([("a", (10.0, 10.0)), ("b", (12.0, 10.0))], view_width=10.0) == []  # a small view: no room to move to


# --- a part of revolution with only coaxial features: ONE axial section, in the main view -----------------------------------------

Z = (0.0, 0.0, 1.0)
MIDDLE = (50.0, 30.0, 4.0)


def ring_faces() -> list[turned.FaceFact]:
    """A stepped hollow ring about z, by hand: the outside, its steps and its bore (what ``LiveGeometry`` reads off the shape)."""
    return [
        turned.FaceFact("cylinder", axis=Z, point=(50.0, 30.0, 6.0)),  # the flange
        turned.FaceFact("plane", normal=(0.0, 0.0, -1.0)),  # its seating face
        turned.FaceFact("plane", normal=Z),
        turned.FaceFact("cylinder", axis=Z, point=(50.0, 30.0, 21.0)),  # the boss
        turned.FaceFact("plane", normal=Z),
        turned.FaceFact("cylinder", axis=Z, point=(50.0, 30.0, 10.0), concave=True),  # the bore
        turned.FaceFact("cone", axis=Z, point=(50.0, 30.0, 0.0), concave=True),  # a lead-in chamfer of the bore
    ]


def test_every_face_coaxial_and_something_inside_is_a_part_for_one_section() -> None:
    assert turned.coaxial_facts(ring_faces(), Z, MIDDLE) == (True, True)
    assert turned.section_only(True, True)


def test_a_solid_pin_with_nothing_inside_keeps_its_outside_view() -> None:
    solid = [f for f in ring_faces() if not f.concave]
    assert turned.coaxial_facts(solid, Z, MIDDLE) == (True, False)
    assert not turned.section_only(True, False)


@pytest.mark.parametrize(
    "off_axis",
    [
        turned.FaceFact("cylinder", axis=Z, point=(84.0, 30.0, 6.0), concave=True),  # a hole on a pitch circle
        turned.FaceFact("plane", normal=(1.0, 0.0, 0.0)),  # a flat, the wall of a keyway
        turned.FaceFact("cylinder", axis=(1.0, 0.0, 0.0), point=(50.0, 30.0, 4.0), concave=True),  # a cross hole
        turned.FaceFact("other"),  # a face the test cannot read
    ],
)
def test_one_feature_off_the_axis_keeps_the_end_view_that_shows_it(off_axis: turned.FaceFact) -> None:
    coaxial, _ = turned.coaxial_facts([*ring_faces(), off_axis], Z, MIDDLE)
    assert not coaxial and not turned.section_only(coaxial, True)


def test_a_sphere_on_the_axis_is_coaxial_and_one_off_it_is_not() -> None:
    assert turned.coaxial_facts([*ring_faces(), turned.FaceFact("sphere", point=(50.0, 30.0, 2.0))], Z, MIDDLE)[0]
    assert not turned.coaxial_facts([*ring_faces(), turned.FaceFact("sphere", point=(60.0, 30.0, 2.0))], Z, MIDDLE)[0]


def test_the_view_a_section_stands_in_place_of_is_not_drawn() -> None:
    assert turned.hidden_views([{"type": "section", "in_place": True}]) == {"front"}
    assert turned.hidden_views([{"type": "section", "in_place": True, "view": "right"}]) == {"right"}
    assert turned.hidden_views([{"type": "section", "holes": "axis"}, {"type": "dim", "in_place": True}]) == set()


class RingGeometry(ViewsGeometry):
    """The plate's views, but the part is a ring about z: a bore on its axis, nothing off it."""

    coaxial = True

    def round_extent(self) -> tuple[sc.Vec3, float] | None:
        return Z, 100.0

    def axial_features(self) -> tuple[bool, bool] | None:
        return self.coaxial, True


def ring_context(coaxial: bool = True) -> sc.Context:
    ctx = views_context([hole(0, 50, 30, 30.0, 8.0)])
    ctx.geometry = RingGeometry()
    ctx.geometry.coaxial = coaxial
    ctx.extras["model_box"] = ((0.0, 0.0, 0.0), (100.0, 60.0, 8.0))
    return ctx


IN_PLACE = {"type": "section", "id": "sec", "holes": "axis", "in_place": True, "view": "front"}


def test_the_section_in_place_is_drawn_over_the_main_view_in_its_frame_with_no_cutting_line_and_no_letters() -> None:
    ctx = ring_context()
    front = ctx.views["front"]
    (result,) = draw(ctx, IN_PLACE)
    assert result.status == "ok" and "in place of the front view" in result.detail and "no cutting line or letters" in result.detail
    section = ctx.view("A-A")
    assert section.derived and (section.x, section.y, section.scale) == (front.x, front.y, front.scale)
    assert (section.direction, section.xdir, section.centre) == (front.direction, front.xdir, front.centre)  # the very same frame
    assert ctx.extras["in_place_views"] == {"A-A": "front"}
    assert not [e for e in ctx.sheet.entities if isinstance(e, model.Solid)]  # no arrowheads
    assert "A" not in ctx.sheet.texts() and "A-A" not in ctx.sheet.texts()  # neither the letters nor the label
    assert ctx.sheet.of_layer(model.HATCH)  # the cut faces are hatched
    _centre, _xdir, direction, cut = ctx.geometry.calls[-1]
    assert direction == front.direction and cut is not None and tuple(cut[1]) == (0.0, 1.0, 0.0)  # the viewer's side is cut away
    assert any(isinstance(e, model.Line) and e.layer == model.AXES for e in ctx.sheet.entities)  # the centre line of the bore


def test_a_section_in_place_wants_the_axis_in_the_view() -> None:
    ctx = ring_context()
    (result,) = draw(ctx, {**IN_PLACE, "view": "top"})  # the top view looks along the axis: it cannot stand for the main view
    assert result.status == "error" and "axis must lie in the view" in result.detail
    with pytest.raises(sc.ItemError, match="in_place takes holes"):
        items.normalise({"type": "section", "holes": "all", "in_place": True}, set(), 1)


def test_a_bore_no_view_sees_as_a_circle_is_called_out_on_the_section_with_one_diameter_per_step() -> None:
    ctx = ring_context()
    ctx.holes = [hole(0, 50, 30, 30.0, 12.0, True), hole(1, 50, 30, 44.0, 10.0, False)]
    ctx.views.pop("top")  # no end view: nothing sees the bore as a circle
    results = {r.id: r for r in draw(ctx, {"type": "hole_dim", "id": "cb", "all": True}, IN_PLACE)}
    assert results["sec"].status == "ok" and results["cb"].status == "ok", results
    assert results["cb"].detail.startswith("A-A:")
    assert ctx.sheet.texts().count("Ø30 pasante") == 1 and any("Ø44" in t for t in ctx.sheet.texts())  # a Ø for each step


# --- the review ------------------------------------------------------------------------------------------------------------------


def coaxial_review(carried: dict[str, set[str]], items_: list[dict[str, Any]], coaxial: bool = True) -> list[review.Finding]:
    ctx = ring_context(coaxial)
    ctx.extras["view_use"] = carried
    data = review.ReviewInput(items_, {}, ctx, ctx.sheet)
    return [*review._unused_views(data), *review._end_view_of_coaxial_part(data)]  # noqa: SLF001


def test_an_end_view_on_a_part_of_only_coaxial_features_is_flagged_even_when_it_is_dimensioned() -> None:
    items_ = [{"id": "o1", "type": "overall"}, {"id": "d1", "type": "dim"}]
    found = coaxial_review({"front": {"o1"}, "top": {"d1"}}, items_)
    (finding,) = [f for f in found if f.items == ("top",)]
    assert finding.code == "view_adds_nothing" and finding.severity == "warning"
    assert "todos sus rasgos coaxiales" in finding.message and "un solo corte axial" in finding.message
    assert "remove_view" in finding.message and "in_place" in finding.message


def test_the_end_view_with_nothing_on_it_gets_the_same_wording_once() -> None:
    found = coaxial_review({"front": {"o1"}}, [{"id": "o1", "type": "overall"}])
    mine = [f for f in found if f.items == ("top",)]
    assert len(mine) == 1 and "todos sus rasgos coaxiales" in mine[0].message


def test_a_part_with_a_feature_off_the_axis_keeps_its_end_view_without_a_finding() -> None:
    items_ = [{"id": "o1", "type": "overall"}, {"id": "d1", "type": "dim"}]
    assert coaxial_review({"front": {"o1"}, "top": {"d1"}, "right": {"d1"}}, items_, coaxial=False) == []


def test_the_section_in_place_is_not_a_view_without_a_job_when_the_dimensions_are_on_the_main_view() -> None:
    ctx = ring_context()
    draw(ctx, IN_PLACE)
    ctx.extras["view_use"] = {**ctx.extras.get("view_use", {}), "front": {"sec", "o1"}}
    items_ = [{"id": "sec", "type": "section"}, {"id": "o1", "type": "overall"}]
    assert [f for f in review._unused_views(review.ReviewInput(items_, {}, ctx, ctx.sheet)) if f.items == ("A-A",)] == []  # noqa: SLF001


def test_the_bore_on_the_axis_is_located_by_the_axis_and_a_bush_needs_no_view_lines_item() -> None:
    ctx = ring_context()
    draw(ctx, IN_PLACE)
    data = review.ReviewInput([IN_PLACE], {"sec": sc.ItemResult("sec", "section", "ok")}, ctx, ctx.sheet)
    assert review._bores_on_axis(ctx) == {0}  # noqa: SLF001
    assert not [f for f in review._hole_callouts(data) if f.code == "hole_not_located"]  # noqa: SLF001
    assert review._view_lines(data) == []  # noqa: SLF001
    ctx.holes = [hole(0, 80, 30, 9.0, 8.0)]  # a hole off the axis still needs its position
    assert review._bores_on_axis(ctx) == set()  # noqa: SLF001
