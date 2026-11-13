# ruff: noqa: E501
"""The weld symbols of a welded part (``sheet_joints.py``, ``items_weld.py``, ``sheet_weldreview.py``) with canned joints: no
FreeCAD. A frame of 4 tubes (3 mm wall) standing on one plate (6 mm): four identical tee joints. What is checked is what the
rules do with the measured joints: the proposal is offered and never applied, a joint with no symbol is a finding, the symbol
is drawn on the view that shows the seam, clear of the lines of the view, and says what the designer decided."""

import sys
from pathlib import Path
from typing import Any

import pytest

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import items_weld as iw  # noqa: E402
import sheet_context as sc  # noqa: E402
import sheet_items as items  # noqa: E402
import sheet_joints as sj  # noqa: E402
import sheet_model as model  # noqa: E402
import sheet_review as review  # noqa: E402

from .test_sheet_items import PlateGeometry, make_context  # noqa: E402
from .test_sheet_weldment import facts  # noqa: E402

TUBES = {"body:1": (0, 0), "body:2": (300, 0), "body:3": (0, 300), "body:4": (300, 300)}  # (y, z) of each tube, 400 long along x


def tee(tube: str) -> sj.Joint:
    y, z = TUBES[tube]
    ring = ((0.0, y, z), (0.0, y + 40, z), (0.0, y + 40, z + 40), (0.0, y, z + 40), (0.0, y, z))
    return sj.Joint((tube, "body:5"), "tee", (ring,), (3.0, 6.0), 1600.0)


def front_visible() -> list[model.Entity]:
    """The near tubes and the plate as the front view draws them (view coordinates about the centre (200, 170, 170))."""
    def rect(x0: float, z0: float, x1: float, z1: float) -> list[model.Entity]:
        a, b, c, d = (x0 - 200, z0 - 170), (x1 - 200, z0 - 170), (x1 - 200, z1 - 170), (x0 - 200, z1 - 170)
        return [model.Line(*a, *b), model.Line(*b, *c), model.Line(*c, *d), model.Line(*d, *a)]

    return rect(0, 0, 400, 40) + rect(0, 300, 400, 340) + rect(-6, 0, 0, 340)


class FrameJoints(PlateGeometry):
    def weld_joints(self) -> list[Any]:
        return [tee(t) for t in TUBES]

    def view_projection(self, centre: Any, xdir: Any, direction: Any) -> sc.Projection:
        return sc.Projection(front_visible())


def context(language: str = "es") -> sc.Context:
    ctx = make_context(language=language)
    ctx.geometry = FrameJoints()
    ctx.views = {"front": sc.ViewInfo("front", 100.0, 200.0, 0.2, (0, -1, 0), (1, 0, 0), (200, 170, 170), (58.8, 166.0, 140.0, 234.0))}
    ctx.weldment = facts_weldment()
    ctx.area = (25.0, 73.0, 405.0, 282.0)
    ctx.extras["view_boxes"] = [v.box for v in ctx.views.values()]
    ctx.extras["main_scale"] = 0.2
    return ctx


def drawn(ctx: sc.Context, *specs: dict[str, Any]) -> list[sc.ItemResult]:
    taken: set[str] = set()
    normalised = []
    for number, spec in enumerate(specs, start=1):
        done = items.normalise(spec, taken, number)
        taken.add(done["id"])
        normalised.append(done)
    ctx.extras["items"] = normalised
    return items.draw_all(ctx, normalised)


def reviewed(ctx: sc.Context, *specs: dict[str, Any]) -> list[review.Finding]:
    taken: set[str] = set()
    normalised = []
    for number, spec in enumerate(specs, start=1):
        done = items.normalise(spec, taken, number)
        taken.add(done["id"])
        normalised.append(done)
    results = items.draw_all(ctx, normalised)
    return review.review(review.ReviewInput(normalised, {r.id: r for r in results}, ctx, ctx.sheet))


WELD = {"type": "weld", "between": ["body:1", "body:5"], "also": [["body:2", "body:5"], ["body:3", "body:5"], ["body:4", "body:5"]], "kind": "fillet", "size_mm": 2, "sides": "arrow", "all_around": True}


# --- the facts: classification, proposal, groups ------------------------------------------------------------------------------


def test_a_tube_end_standing_on_a_plate_is_a_tee_two_ends_meeting_a_butt_two_surfaces_a_lap() -> None:
    assert sj.classify(sj.Contact(444.0, (444.0, 115600.0), (True, False))) == "tee"
    assert sj.classify(sj.Contact(444.0, (444.0, 444.0), (True, True))) == "butt"
    assert sj.classify(sj.Contact(5000.0, (20000.0, 30000.0), (False, False))) == "lap"
    assert sj.classify(sj.Contact(444.0, (444.0, 115600.0), (True, False), flush=True)) == "corner"
    assert sj.classify(sj.Contact(0.0, (0.0, 0.0), (False, False))) == "edge"


def test_the_proposal_is_a_fillet_of_seven_tenths_of_the_thinner_wall_on_the_standard_series() -> None:
    assert sj.proposal(tee("body:1")) == sj.Weld("fillet", 2.0, "throat")  # 0.7 x 3 = 2.1
    thick = sj.Joint(("body:1", "body:2"), "tee", (), (6.0, 8.0))
    assert sj.proposal(thick).size_mm == 4.0  # 0.7 x 6 = 4.2
    assert sj.standard_throat(3.5) == 4.0  # half way: the larger one
    assert sj.proposal(sj.Joint(("body:1", "body:2"), "butt", (), (3.0, 3.0))) == sj.Weld("square", 3.0, "throat")
    assert sj.proposal(sj.Joint(("body:1", "body:2"), "butt", (), (8.0, 8.0))).kind == "v"


def test_the_wall_is_measured_from_the_volume_and_the_surface_never_from_a_name() -> None:
    assert sj.wall_thickness(177600.0, 119288.0, (400.0, 40.0, 40.0)) == 3.0  # a 40x40x3 tube, 400 long
    assert sj.wall_thickness(693600.0, 239360.0, (340.0, 340.0, 6.0)) == 6.0  # a 6 mm plate
    assert sj.wall_thickness(1_000_000.0, 85000.0, (400.0, 50.0, 50.0)) == 23.5  # a solid bar: capped by what the formula says


def test_identical_joints_are_one_group_and_the_key_ignores_the_order() -> None:
    assert [[j.bodies[0] for j in g] for g in sj.groups([tee(t) for t in TUBES])] == [["body:1", "body:2", "body:3", "body:4"]]
    assert sj.joint_key("body:10", "body:2") == sj.joint_key("body:2", "body:10") == "body:2+body:10"
    odd = sj.Joint(("body:6", "body:5"), "tee", tee("body:1").seams, (3.0, 6.0))
    assert len(sj.groups([tee("body:1"), odd])) == 1  # same kind, wall and seam: the same job


@pytest.mark.parametrize(
    ("text", "wrong"),
    [
        ("Soldadura de filete a=4 mm en todas las uniones, proceso MAG, clase ISO 13920-B", True),
        ("Weld: fillet a=4, quality class ISO 13920 B", True),
        ("Soldadura MAG, nivel de calidad ISO 5817-C; tolerancias generales ISO 13920-B", False),
        ("Tolerancias generales ISO 13920-B", False),
        ("Soldadura de filete a=4 mm, MAG", False),
    ],
)
def test_iso_13920_is_not_a_weld_quality_class(text: str, wrong: bool) -> None:
    assert sj.same_class_note(text) is wrong


# --- the item -----------------------------------------------------------------------------------------------------------------


def test_the_symbol_is_drawn_on_the_seam_with_what_the_designer_decided() -> None:
    ctx = context()
    (result,) = drawn(ctx, WELD)
    assert result.status == "ok", result
    printed = ctx.sheet.texts()
    assert "a2" in printed and "4×" not in printed  # the throat before the symbol; the arrows say which joints, not a count
    kinds = [type(e).__name__ for e in ctx.sheet.entities]
    assert kinds.count("Circle") == 1  # all round
    assert "Solid" in kinds  # the arrowhead
    assert "front view" in result.detail and all(f"body:{n} + body:5" in result.detail for n in (1, 2, 3, 4))
    inside = ctx.area
    for e in ctx.sheet.entities:
        if isinstance(e, model.Line):
            assert 20.0 <= min(e.x1, e.x2) and max(e.x1, e.x2) <= 410.0  # inside the frame of the sheet


def test_the_arrow_ends_on_the_seam_and_does_not_cross_the_lines_of_the_view() -> None:
    ctx = context()
    drawn(ctx, WELD)
    (tip, anchor) = ctx.extras["segments"][0]
    view = ctx.views["front"]
    seams = [view.project(p) for t in TUBES for p in tee(t).seams[0]]
    assert min(abs(tip[0] - s[0]) + abs(tip[1] - s[1]) for s in seams) < 8.5  # on one of the seams of the front view (their ends included)
    assert tip[0] <= 61.0  # the joints are at the plate: x = 0 of the model is page x = 60
    assert iw._crossings(iw.visible_segments(ctx, view), tip, anchor) <= 1  # the seam is the edge of the plate seen edge-on: at most its far edge


class PictorialJoints(FrameJoints):
    """The frame as a pictorial view shows it: the four seams stand apart on the page and nothing hides them."""

    def view_projection(self, centre: Any, xdir: Any, direction: Any) -> sc.Projection:
        msg = "no projection for this view"
        raise sc.ItemError(msg)


def pictorial_context() -> sc.Context:
    ctx = context()
    ctx.geometry = PictorialJoints()
    iso = sc.ViewInfo("isometric", 220.0, 170.0, 0.2, (1, 1, 1), (1, -1, 0), (200, 170, 170), (150.0, 120.0, 290.0, 220.0))
    ctx.views = {}
    ctx.extras["views_extra"] = {"isometric": iso}
    ctx.extras["view_boxes"] = [iso.box]
    return ctx


def test_one_symbol_for_identical_joints_has_an_arrow_on_the_seam_of_each_of_them() -> None:
    ctx = pictorial_context()
    (result,) = drawn(ctx, WELD)
    assert result.status == "ok", result
    iso = ctx.extras["views_extra"]["isometric"]
    segments = ctx.extras["segments"]
    assert len(segments) == 4 and len({anchor for _, anchor in segments}) == 1  # four arrows from ONE reference line (ISO 2553)
    seams = {t: [iso.project(p) for p in tee(t).seams[0]] for t in TUBES}
    owners = set()
    for tip, _ in segments:
        near = {t: min(iw._segment_distance(tip, tip, a, b) for a, b in zip(r, r[1:], strict=False)) for t, r in seams.items()}
        owner = min(near, key=lambda t: near[t])
        assert near[owner] < 0.5  # the arrow ends ON the seam line of that joint...
        assert min(((tip[0] - p[0]) ** 2 + (tip[1] - p[1]) ** 2) ** 0.5 for p in seams[owner]) > 1.2  # ...not on a corner of its outline
        owners.add(owner)
    assert owners == set(TUBES)  # one arrow per joint, none left without
    heads = [e for e in ctx.sheet.entities if isinstance(e, model.Solid) and len(e.points) == 3]
    assert len(heads) == 4


def test_the_lettering_of_the_symbol_is_not_smaller_than_the_dimension_text() -> None:
    ctx = pictorial_context()
    drawn(ctx, {**WELD, "all_around": False, "length_mm": 50, "pitch_mm": 100})
    heights = [e.height for e in ctx.sheet.entities if isinstance(e, model.Text)]
    assert heights and min(heights) >= model.TEXT_HEIGHT_MM


def test_the_joints_a_view_hides_get_their_arrow_in_the_view_that_shows_them() -> None:
    ctx = context("en")
    iso = sc.ViewInfo("isometric", 220.0, 170.0, 0.2, (1, 1, 1), (1, -1, 0), (200, 170, 170), (150.0, 120.0, 290.0, 220.0))
    ctx.extras["views_extra"] = {"isometric": iso}
    ctx.geometry.seam_hidden = lambda point, towards: point[1] > 100 and tuple(towards) == (0, -1, 0)  # type: ignore[method-assign]  # the far tubes are behind the near ones in the front view only
    plain = ctx.geometry.view_projection

    def projection(centre: Any, xdir: Any, direction: Any) -> sc.Projection:
        if tuple(direction) != (0, -1, 0):
            raise sc.ItemError("no projection for this view")
        return plain(centre, xdir, direction)

    ctx.geometry.view_projection = projection  # type: ignore[method-assign]
    groups, unseen = iw.split_by_view(ctx, iw.joints_named(ctx, {**WELD}), None)
    assert not unseen
    assert sum(len(m) for _, m in groups) == 4  # every joint has a view
    assert groups[0][0].name in ("front", "isometric")


def test_a_symbol_on_the_other_side_has_the_dashed_identification_line_and_one_on_both_sides_has_none() -> None:
    def lines(sides: str) -> int:
        ctx = context()
        drawn(ctx, {**WELD, "sides": sides, "also": []})
        return sum(1 for e in ctx.sheet.entities if isinstance(e, model.Line))

    arrow_only = lines("arrow")
    both = lines("both")
    other = lines("other")
    assert arrow_only > both  # the dashes
    assert other == arrow_only
    ctx = context()
    drawn(ctx, {**WELD, "sides": "both", "also": []})
    assert ctx.sheet.texts().count("a2") == 2  # the throat on both sides of the reference line


def test_intermittent_field_and_leg_are_written_the_iso_way() -> None:
    ctx = context()
    (result,) = drawn(ctx, {"type": "weld", "between": ["body:1", "body:5"], "kind": "fillet", "size_mm": 4, "size_kind": "leg", "length_mm": 50, "pitch_mm": 100, "field": True})
    printed = ctx.sheet.texts()
    assert "z4" in printed and "50 (100)" in printed
    assert any(isinstance(e, model.Solid) and len(e.points) == 3 for e in ctx.sheet.entities)  # the field flag and the arrowhead
    assert result.status == "ok"


def test_two_bodies_that_do_not_touch_have_no_joint_to_weld() -> None:
    ctx = context("en")
    (result,) = drawn(ctx, {"type": "weld", "between": ["body:1", "body:2"], "size_mm": 2})
    assert result.status == "error" and "do not touch" in result.detail and "body:1+body:5" in result.detail


def test_a_part_with_no_measured_joint_says_so() -> None:
    ctx = context("en")
    ctx.geometry = PlateGeometry()
    (result,) = drawn(ctx, {"type": "weld", "between": ["body:1", "body:5"], "size_mm": 2})
    assert result.status == "error" and "no measured joints" in result.detail


def test_a_joint_no_view_shows_is_an_error_that_says_what_to_do() -> None:
    ctx = context("en")
    ctx.geometry.view_projection = lambda *a, **k: sc.Projection([model.Line(-10, -10, 10, -10)])  # type: ignore[method-assign]
    (result,) = drawn(ctx, WELD)
    assert result.status == "error" and "hidden or edge-on" in result.detail


@pytest.mark.parametrize(
    ("spec", "text"),
    [
        ({"between": ["body:1", "body:1"], "size_mm": 2}, "two bodies that touch"),
        ({"between": ["body:1", "body:5"], "size_mm": 2, "pitch_mm": 50}, "pitch_mm"),
        ({"between": ["body:1", "body:5"], "size_mm": 2, "length_mm": 50, "pitch_mm": 20}, "shorter"),
        ({"between": ["body:1", "body:5"], "size_mm": 2, "kind": "v", "size_kind": "leg"}, "fillet"),
        ({"between": ["body:1", "body:5"], "size_mm": 2, "all_around": True, "length_mm": 50}, "no length"),
    ],
)
def test_the_fields_are_checked(spec: dict[str, Any], text: str) -> None:
    with pytest.raises(sc.ItemError, match=text):
        items.normalise({"type": "weld", **spec}, set(), 1)


# --- the review -------------------------------------------------------------------------------------------------------------


def test_joints_with_no_symbol_are_one_blocking_question_with_the_proposal_never_applied() -> None:
    found = reviewed(context())
    (ask,) = [f for f in found if f.code == "weld_joint_unspecified"]
    assert "UNA vez" in ask.message and "a=2" in ask.message and "4 iguales" in ask.message
    assert "se ve en la vista front" in ask.message and "body:1" in ask.message and "body:4" in ask.message
    assert "salvo indicación" in ask.message  # a general note only adds to the symbols
    assert '"between":["body:1", "body:5"]' in ask.message and '"also":' in ask.message
    english = [f for f in reviewed(context("en")) if f.code == "weld_joint_unspecified"]
    assert "ONCE" in english[0].message and "4 identical" in english[0].message
    import sheet_checks  # noqa: PLC0415

    row: dict[str, Any] = {"code": "weld_joint_unspecified", "items": (), "features": (), "key": ask.key}
    sheet_checks.assign_ids([row])
    assert row["blocking"] is True  # holds the export unless the engineer orders otherwise


def test_a_general_note_does_not_close_a_joint_but_the_symbol_does() -> None:
    note = {"type": "note", "text": "Soldadura de filete a=4 mm en todas las uniones"}
    assert "weld_joint_unspecified" in [f.code for f in reviewed(context(), note)]
    assert "weld_joint_unspecified" not in [f.code for f in reviewed(context(), WELD)]


def test_only_the_joints_a_symbol_does_not_name_stay_open() -> None:
    half = {**WELD, "also": [["body:2", "body:5"]]}
    found = reviewed(context(), half)
    (ask,) = [f for f in found if f.code == "weld_joint_unspecified"]
    assert "body:3" in ask.message and "body:4" in ask.message and "body:2" not in ask.message
    assert "2 iguales" in ask.message


def test_a_weld_note_that_names_iso_13920_as_the_quality_class_is_flagged() -> None:
    note = {"type": "note", "id": "n1", "text": "Soldadura de filete a=2 mm, MAG, clase ISO 13920-B"}
    (bad,) = [f for f in reviewed(context(), WELD, note) if f.code == "weld_class_13920"]
    assert "ISO 5817" in bad.message and bad.items == ("n1",)
    fine = {"type": "note", "text": "Soldadura MAG, nivel ISO 5817-C. Tolerancias generales ISO 13920-B"}
    assert "weld_class_13920" not in [f.code for f in reviewed(context(), WELD, fine)]


def test_a_second_symbol_on_a_joint_with_a_double_sided_one_is_a_duplicate() -> None:
    one = {**WELD, "id": "w1", "sides": "both", "also": []}
    two = {"type": "weld", "id": "w2", "between": ["body:5", "body:1"], "size_mm": 2, "sides": "other"}
    found = reviewed(context(), one, two)
    assert "weld_duplicate" in [f.code for f in found]


def test_with_no_joint_measured_the_old_question_stays_and_a_weld_note_closes_it() -> None:
    from .test_sheet_weldment import context as frame_context  # noqa: PLC0415

    plain = frame_context()
    assert "weld_not_specified" in [f.code for f in reviewed(plain, {"type": "parts_list"})]
    assert "weld_not_specified" not in [f.code for f in reviewed(frame_context(), {"type": "parts_list"}, {"type": "note", "text": "Soldadura MAG a=4"})]


def test_the_facts_for_the_requirement_count_symbols_when_joints_are_measured() -> None:
    import sheet_reqfacts  # noqa: PLC0415

    ctx = context()
    ctx.weldment = facts_weldment()
    normalised = [items.normalise({"type": "note", "text": "Soldadura de filete a=4"}, set(), 1)]
    data = review.ReviewInput(normalised, {}, ctx, ctx.sheet)
    assert sheet_reqfacts.facts(data)["weld_marked_on_sheet"] == 0  # a note does not mark measured joints
    assert sheet_reqfacts.facts(data)["weld_class_on_sheet"] == 0


def facts_weldment() -> Any:
    import sheet_weldment as weld  # noqa: PLC0415

    return weld.group_bodies(facts(), "es")


def test_a_seam_behind_a_body_is_not_shown_even_when_an_outline_coincides_with_it() -> None:
    ctx = context("en")
    ctx.geometry.seam_hidden = lambda point, towards: True  # type: ignore[method-assign]
    (result,) = drawn(ctx, WELD)
    assert result.status == "error" and "hidden or edge-on" in result.detail
    ctx = context("en")
    ctx.geometry.seam_hidden = lambda point, towards: point[1] > 100  # type: ignore[method-assign]  # the far tubes are behind the near ones
    (result,) = drawn(ctx, WELD)
    assert result.status == "warning"
    assert "no arrow for body:2 + body:5, body:4 + body:5" in result.detail  # the far tubes: no view shows their seam, and it says so
    tip = ctx.extras["segments"][0][0]
    assert tip[1] < 175 or 190 < tip[1]  # on the seam of a near tube (page y 166..174 or 226..234), not of a far one
