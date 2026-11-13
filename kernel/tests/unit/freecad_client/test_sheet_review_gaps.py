# ruff: noqa: E501
"""What the drawing review let through on two real sheets (no FreeCAD): an assembly of two plates held apart by spacer
sleeves and a threaded shaft end.

* the overall width of the assembly stands above the front view while TechDraw draws its extension lines up from the lower
  corners of the view, across the gap between the plates: the harness puts it below, and the review flags it when it is above;
* the sleeves seen from the side get their axis line (and so does a section), only where the view really shows them; the review
  flags the ones that lack it;
* a shaft with an M12 callout does not carry the plain Ø12 of its overall size as well;
* the end of the useful thread is drawn across the shaft; a chamfer on a round edge gets its callout.
"""

import math
import sys
from pathlib import Path
from typing import Any

import pytest

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import items_assembly as asm  # noqa: E402
import sheet_checks as checks  # noqa: E402
import sheet_context as sc  # noqa: E402
import sheet_items as items  # noqa: E402
import sheet_model as model  # noqa: E402
import sheet_review as review  # noqa: E402
import sheet_seen  # noqa: E402
import sheet_thread  # noqa: E402
import sheet_viewchecks as viewchecks  # noqa: E402
import sheet_viewrules as vr  # noqa: E402

from .test_sheet_items import PlateGeometry  # noqa: E402

CONTOUR = model.CONTOUR


def line(x1: float, y1: float, x2: float, y2: float) -> model.Line:
    return model.Line(x1, y1, x2, y2, CONTOUR)


# --- the front view of two plates held apart by two spacers (page mm, 1:1, y up) ---------------------------------------------

BOX = (100.0, 140.0, 220.0, 176.0)
# plates 10 and 8 thick at the bottom and the top, a gap of 18 between them, two sleeves of Ø16 at x = 128 and 192
ASSEMBLY_INK = [
    line(100, 140, 220, 140), line(100, 150, 220, 150), line(100, 140, 100, 150), line(220, 140, 220, 150),
    line(100, 168, 220, 168), line(100, 176, 220, 176), line(100, 168, 100, 176), line(220, 168, 220, 176),
    line(120, 150, 120, 168), line(136, 150, 136, 168), line(184, 150, 184, 168), line(200, 150, 200, 168),
]  # fmt: skip
BLOCK_INK = [line(100, 140, 220, 140), line(100, 176, 220, 176), line(100, 140, 100, 176), line(220, 140, 220, 176)]
SEGMENTS = [(e.x1, e.y1, e.x2, e.y2) for e in ASSEMBLY_INK]


def front_context(ink: list[Any], extras: dict[str, Any] | None = None) -> sc.Context:
    view = sc.ViewInfo("front", 160.0, 158.0, 1.0, (0, -1, 0), (1, 0, 0), (60, 0, 18), BOX)
    ctx = sc.Context(
        sheet=model.Sheet(420.0, 297.0),
        views={"front": view},
        holes=[],
        geometry=PlateGeometry(),
        area=(25.0, 80.0, 405.0, 282.0),
    )
    ctx.extras["view_ink"] = ink
    ctx.extras["main_scale"] = 1.0
    ctx.extras.update(extras or {})
    return ctx


def draw(ctx: sc.Context, *specs: dict[str, Any]) -> list[sc.ItemResult]:
    taken: set[str] = set()
    normalised = []
    for number, spec in enumerate(specs, start=1):
        done = items.normalise(spec, taken, number)
        taken.add(done["id"])
        normalised.append(done)
    ctx.extras["items"] = normalised
    return items.draw_all(ctx, normalised)


# --- the extension lines of an overall width (finding 1) ---------------------------------------------------------------------


def test_extension_lines_over_nothing_are_measured_against_the_lines_of_the_part() -> None:
    left = (100.0, 140.0, 100.0, 176.0)
    assert vr.uncovered_run((left[0], left[1]), (left[2], left[3]), SEGMENTS) == pytest.approx(18.0)  # the gap between the plates
    assert vr.uncovered_run((100.0, 140.0), (100.0, 176.0), [(e.x1, e.y1, e.x2, e.y2) for e in BLOCK_INK]) == 0.0


def test_an_overall_whose_extension_lines_cross_a_gap_is_found_and_a_block_is_not() -> None:
    runs = [(100.0, 140.0, 100.0, 176.0), (220.0, 140.0, 220.0, 176.0)]
    found = vr.extent_gaps([("o1", "front", "above", runs)], {"front": SEGMENTS})
    assert [(g.owner, g.gap_mm) for g in found] == [("o1", 36.0)]  # 18 mm at each end
    assert vr.extent_gaps([("o1", "front", "above", runs)], {"front": [(e.x1, e.y1, e.x2, e.y2) for e in BLOCK_INK]}) == []
    assert vr.extent_gaps([("o1", "front", "below", [])], {"front": SEGMENTS}) == []  # below, the lines go away from the part


def test_the_width_of_an_assembly_with_a_gap_goes_below_the_view() -> None:
    ctx = front_context(ASSEMBLY_INK)
    (res,) = draw(ctx, {"type": "overall"})
    assert res.status == "ok", res.detail
    width = ctx.extras["overall_dims"]["width"]
    assert width["line"] < BOX[1], "the dimension line is under the view"
    assert ctx.extras["extent_dims"][0][2] == "below"
    assert "below the view" in res.detail
    found = viewchecks.check(review.ReviewInput([{"id": "o1", "type": "overall"}], {}, ctx, ctx.sheet))
    assert [f for f in found if f.code == "dimension_side"] == [], "placed below, its lines do not cross the gap"


def test_the_width_of_a_block_stays_above_the_view() -> None:
    ctx = front_context(BLOCK_INK)
    draw(ctx, {"type": "overall"})
    assert ctx.extras["overall_dims"]["width"]["line"] > BOX[3]
    assert ctx.extras["extent_dims"][0][2] == "above"


def test_an_overall_placed_above_by_hand_over_a_gap_is_flagged_and_says_how_to_move_it() -> None:
    ctx = front_context(ASSEMBLY_INK)
    draw(ctx, {"type": "overall", "side": "above"})
    assert ctx.extras["overall_dims"]["width"]["line"] > BOX[3]
    data = review.ReviewInput([{"id": "o1", "type": "overall"}], {}, ctx, ctx.sheet)
    (found,) = [f for f in viewchecks.check(data) if f.code == "dimension_side"]
    assert found.items == ("o1",) and "side=below" in found.message
    assert "cruzan un hueco" in found.message


# --- the diameter of a threaded shaft, said once (finding 5) ------------------------------------------------------------------

SHAFT_BOX = (100.0, 100.0, 124.0, 220.0)  # Ø12 x 60 at 2:1, axis along z


class ShaftGeometry(PlateGeometry):
    def round_extent(self) -> tuple[sc.Vec3, float]:
        return (0.0, 0.0, 1.0), 12.0

    def face(self, face_id: str) -> sc.FaceInfo:
        if face_id == "face:shaft":
            return sc.FaceInfo(
                "face:shaft", "cylinder", (0, 0, 30), radius=6.0, axis_point=(0, 0, 0), axis_dir=(0, 0, 1), surface_point=(6, 0, 30)
            )
        return super().face(face_id)


def shaft_context() -> sc.Context:
    ctx = front_context([])
    ctx.views["front"] = sc.ViewInfo("front", 112.0, 160.0, 2.0, (0, -1, 0), (1, 0, 0), (0, 0, 30), SHAFT_BOX)
    ctx.geometry = ShaftGeometry()
    ctx.threads = [sc.ThreadInfo("M12", 12.0, 9.853, 20.0, (0, 0, 60), (0, 0, -1), "face:shaft", 1.75)]
    return ctx


THREAD_DIM = {"type": "dim", "id": "thr", "kind": "diameter", "face": "face:shaft", "view": "front"}


def test_the_overall_diameter_of_a_shaft_is_a_diameter_and_its_thread_callout_does_not_repeat_it() -> None:
    plain = shaft_context()
    (res,) = draw(plain, {"type": "overall"})
    assert "Ø12" in plain.sheet.texts() and "60" in plain.sheet.texts(), res.detail  # with no callout the diameter is stated

    called = shaft_context()
    called.threads = [sc.ThreadInfo("M12", 12.0, 9.853, 60.0, (0, 0, 60), (0, 0, -1), "face:shaft", 1.75)]  # the thread runs the whole length
    res, callout = draw(called, {"type": "overall"}, THREAD_DIM)
    assert res.status == "ok" and callout.status == "ok", callout.detail
    assert any(t.startswith("M12") for t in called.sheet.texts())
    assert "Ø12" not in called.sheet.texts() and "12" not in called.sheet.texts(), "M12 already says the major diameter"
    assert "60" in called.sheet.texts()
    assert "not drawn: width Ø12" in res.detail


def test_a_thread_on_one_end_leaves_the_plain_part_its_own_diameter() -> None:
    """M12 for 20 of 60 mm: the callout says the thread's diameter and nothing of the plain 40 mm, which keeps its Ø12."""
    ctx = shaft_context()  # the thread is 20 mm long on a 60 mm shaft
    res, callout = draw(ctx, {"type": "overall"}, THREAD_DIM)
    assert res.status == "ok" and callout.status == "ok", callout.detail
    assert any(t.startswith("M12") for t in ctx.sheet.texts())
    assert "Ø12" in ctx.sheet.texts() and "60" in ctx.sheet.texts()
    assert "not drawn" not in res.detail


def test_a_diameter_dim_on_a_plain_cylinder_does_not_remove_the_overall_diameter() -> None:
    ctx = shaft_context()
    ctx.threads = []
    draw(ctx, {"type": "overall"}, {"type": "dim", "id": "d", "kind": "diameter", "face": "face:shaft", "view": "front"})
    assert any(t.startswith("Ø12") for t in ctx.sheet.texts())


# --- the end of the useful thread (finding 6) --------------------------------------------------------------------------------


def test_the_side_view_of_an_external_thread_has_a_wide_line_where_the_full_thread_ends() -> None:
    ctx = shaft_context()
    entities = sheet_thread.external_entities(ctx)
    wide = [e for e in entities if isinstance(e, model.Line) and e.layer == CONTOUR]
    thin = [e for e in entities if isinstance(e, model.Line) and e.layer == model.THREAD]
    assert len(thin) == 2 and len(wide) == 1
    (end,) = wide
    # 20 mm from the end face at 2:1: 40 page mm below it, across the whole major diameter (12 x 2 = 24 mm)
    assert end.y1 == pytest.approx(end.y2) and abs(end.x2 - end.x1) == pytest.approx(24.0)
    top = ctx.views["front"].project((0, 0, 60))[1]
    assert top - end.y1 == pytest.approx(40.0)
    assert (end.x1 + end.x2) / 2 == pytest.approx(ctx.views["front"].project((0, 0, 0))[0])


# --- the callout of a chamfer on a round edge (finding 7) --------------------------------------------------------------------


class ChamferedShaft(ShaftGeometry):
    def cone_chamfer(self, face_id: str) -> sc.ConeChamfer | None:
        if face_id != "face:cone":
            return None
        return sc.ConeChamfer("face:cone", 1.0, 1.0, 45.0, (0, 0, 1), ((0, 0, 60), 5.0), ((0, 0, 59), 6.0))

    def face(self, face_id: str) -> sc.FaceInfo:
        if face_id == "face:cone":
            return sc.FaceInfo("face:cone", "other", (5.5, 0, 59.5))
        return super().face(face_id)


def test_a_chamfer_on_a_shaft_end_gets_its_size_and_angle_on_its_profile_line() -> None:
    ctx = shaft_context()
    ctx.geometry = ChamferedShaft()
    (res,) = draw(ctx, {"type": "dim", "id": "ch", "kind": "chamfer", "face": "face:cone", "view": "front"})
    assert res.status == "ok", res.detail
    assert "1 × 45°" in ctx.sheet.texts()
    view, tip, normal = ctx.extras["chamfer_marks"]["ch"]
    assert view == "front" and normal[0] > 0.5  # the leader comes from outside the shaft
    # the middle of the profile line: half way between (5, 60) and (6, 59) seen at 2:1
    assert tip == pytest.approx(
        ((ctx.views["front"].project((5.5, 0, 59.5)))[0], ctx.views["front"].project((5.5, 0, 59.5))[1]), abs=1e-6
    )


def test_a_flat_face_is_still_a_flat_chamfer_and_a_cone_geometry_is_not_needed() -> None:
    ctx = shaft_context()  # PlateGeometry has no cone chamfer: the old path runs and refuses a face that is not a chamfer
    (res,) = draw(ctx, {"type": "dim", "id": "ch", "kind": "chamfer", "face": "face:top", "view": "front"})
    assert res.status == "error" and "chamfer" in res.detail  # PlateGeometry has no legs for a flat face either


# --- the sleeves of an assembly seen from the side (finding 3) ----------------------------------------------------------------

SLEEVE = checks.Boss("comp:2/cylinder1", 8.0, (60.0, 0.0, 18.0), (0.0, 0.0, 1.0), 18.0)


def sleeve_context(ink: list[Any], bosses: list[checks.Boss], section: bool = False) -> sc.Context:
    ctx = front_context(ink)
    ctx.extras["facts"] = checks.PartFacts(bosses=bosses, assembly=True)
    if section:
        ctx.extras["sections"] = {"B-B": sc.ViewInfo("B-B", 160.0, 100.0, 1.0, (0, -1, 0), (1, 0, 0), (60, 0, 18), (100.0, 82.0, 220.0, 118.0), derived=True)}
        ctx.extras["section_planes"] = {"B-B": ((0.0, 0.0, 18.0), (0.0, 1.0, 0.0))}
    return ctx


def test_a_cylinder_is_seen_from_the_side_when_both_lines_of_its_silhouette_are_drawn() -> None:
    ctx = sleeve_context(ASSEMBLY_INK, [SLEEVE])
    view = ctx.views["front"]
    # the sleeve at x = 60 stands at page x 160: its silhouette is x = 152 and 168
    ink = [line(152, 150, 152, 168), line(168, 150, 168, 168)]
    assert sheet_seen.side_on_seen(sleeve_context(ink, [SLEEVE]), view, SLEEVE)
    assert not sheet_seen.side_on_seen(ctx, view, SLEEVE)  # no lines of a Ø16 cylinder at x = 160 in this ink
    hidden = [line(152, 150, 152, 168)]
    assert not sheet_seen.side_on_seen(sleeve_context(hidden, [SLEEVE]), view, SLEEVE), "one side only: it is behind another part"


def test_the_review_flags_a_sleeve_seen_from_the_side_with_no_axis_and_stays_quiet_when_it_is_hidden_or_drawn() -> None:
    ink = [line(152, 150, 152, 168), line(168, 150, 168, 168)]
    ctx = sleeve_context(ink, [SLEEVE])
    found = checks._marks(review.ReviewInput([], {}, ctx, ctx.sheet))
    assert [f.code for f in found] == ["cylinder_no_axis"] and "front" in found[0].message
    ctx = sleeve_context([], [SLEEVE])  # nothing of it is drawn in this view: no axis to ask for
    assert checks._marks(review.ReviewInput([], {}, ctx, ctx.sheet)) == []
    ctx = sleeve_context(ink, [SLEEVE])
    mid = ctx.views["front"].project(SLEEVE.axis_point)
    ctx.sheet.extend([model.Line(mid[0], 146.0, mid[0], 172.0, model.AXES)])
    assert checks._marks(review.ReviewInput([], {}, ctx, ctx.sheet)) == []


def test_the_review_asks_for_the_axis_of_a_cylinder_in_a_section_that_cuts_it() -> None:
    ctx = sleeve_context([], [SLEEVE], section=True)
    found = checks._marks(review.ReviewInput([], {}, ctx, ctx.sheet))
    assert [f.code for f in found] == ["cylinder_no_axis"] and "B-B" in found[0].message
    bore = checks.Boss("comp:1/cylinder1", 4.5, (60.0, 0.0, 18.0), (0.0, 0.0, 1.0), 10.0, bore=True)
    ctx = sleeve_context(ASSEMBLY_INK, [bore])  # a bore is inside the material in a plain view: no axis asked there
    assert checks._marks(review.ReviewInput([], {}, ctx, ctx.sheet)) == []


def test_view_lines_draws_the_axis_of_a_sleeve_once_where_the_view_shows_it() -> None:
    ink = [line(152, 150, 152, 168), line(168, 150, 168, 168)]
    ctx = sleeve_context(ink, [SLEEVE, checks.Boss("comp:2/cylinder2", 4.5, (60.0, 0.0, 18.0), (0.0, 0.0, 1.0), 18.0, bore=True)])
    (res,) = draw(ctx, {"type": "view_lines", "hidden": []})
    assert res.status == "ok", res.detail
    axes = [e for e in ctx.sheet.entities if isinstance(e, model.Line) and e.layer == model.AXES]
    assert len(axes) == 1, "the bore of the sleeve is inside it: one axis for both"
    assert axes[0].x1 == pytest.approx(160.0) and axes[0].y2 - axes[0].y1 > 18.0
    bare = sleeve_context([], [SLEEVE])
    draw(bare, {"type": "view_lines", "hidden": []})
    assert [e for e in bare.sheet.entities if isinstance(e, model.Line) and e.layer == model.AXES] == []


# --- the balloons of an assembly (finding 4) ----------------------------------------------------------------------------------


def test_balloons_default_to_the_isometric_view_and_the_review_flags_the_ones_that_are_not() -> None:
    ctx = front_context([])
    assert asm._view_name(ctx, {}) == "front"
    ctx.extras["views_extra"] = {"isometric": object()}
    assert asm._view_name(ctx, {}) == "isometric"
    assert asm._view_name(ctx, {"view": "top"}) == "top"
    ctx.extras["views_extra"] = {"isometric": object(), "exploded": object()}
    assert asm._view_name(ctx, {}) == "exploded"

    ctx.extras["views_extra"] = {"isometric": object()}
    ctx.assembly = sc.Assembly([sc.PartGroup(1, "plate", "plate", "", "S355", "", ["comp:1"])])
    ctx.extras["balloons"] = {"drawn": {1}, "hidden": {}, "view": {1: "front"}}
    ctx.extras["parts_list"] = {"rect": (0, 0, 1, 1), "positions": [1]}
    data = review.ReviewInput([{"id": "pl", "type": "parts_list"}, {"id": "bal", "type": "balloons"}], {"pl": sc.ItemResult("pl", "parts_list", "ok", ""), "bal": sc.ItemResult("bal", "balloons", "ok", "")}, ctx, ctx.sheet)
    codes = [f.code for f in review._assembly_lists(data)]
    assert "balloons_off_isometric" in codes
    ctx.extras["balloons"]["view"] = {1: "isometric"}
    assert "balloons_off_isometric" not in [f.code for f in review._assembly_lists(data)]


# --- a view with nothing in it (finding 2): the check does fire on a section, the gate used to accept the agent's answer ---------


def test_a_section_with_no_dimension_is_flagged_as_a_view_that_adds_nothing() -> None:
    ctx = front_context([])
    section = sc.ViewInfo("B-B", 160.0, 100.0, 1.0, (0, -1, 0), (1, 0, 0), (60, 0, 18), (100.0, 82.0, 220.0, 118.0), derived=True)
    ctx.extras["sections"] = {"B-B": section}
    ctx.extras["section_items"] = {"B-B": "secB"}
    ctx.extras["view_use"] = {"front": {"d1", "secB"}, "B-B": {"th1"}}  # the thread lines are lines, not dimensions
    specs = [{"id": "d1", "type": "dim"}, {"id": "secB", "type": "section"}, {"id": "th1", "type": "line"}]
    ctx.extras["views_extra"] = {}
    ctx.views["top"] = sc.ViewInfo("top", 160.0, 220.0, 1.0, (0, 0, 1), (1, 0, 0), (60, 0, 18), (100.0, 200.0, 220.0, 240.0))
    ctx.extras["view_use"]["top"] = {"d1"}
    found = review._unused_views(review.ReviewInput(specs, {}, ctx, ctx.sheet))
    assert [(f.code, f.items) for f in found] == [("view_adds_nothing", ("B-B",))]
    assert "secB" in found[0].message  # it says which item to remove


def test_the_vocabulary_of_the_new_findings_exists() -> None:
    assert math.isfinite(vr.EXTENT_GAP_MM)
    assert "chamfer_dims" in items.UNSUPPORTED


class HorizontalShaft(ShaftGeometry):
    def round_extent(self) -> tuple[sc.Vec3, float]:
        return (1.0, 0.0, 0.0), 12.0

    def face(self, face_id: str) -> sc.FaceInfo:
        if face_id == "face:shaft":
            return sc.FaceInfo(
                "face:shaft", "cylinder", (30, 0, 0), radius=6.0, axis_point=(0, 0, 0), axis_dir=(1, 0, 0), surface_point=(30, 6, 0)
            )
        return PlateGeometry.face(self, face_id)


def test_the_diameter_of_a_shaft_with_a_thread_on_one_end_sits_on_the_plain_part_not_on_the_thread() -> None:
    """Axis along the page x, M12 on the right end (x 40..60 of 60): the Ø12 is at the plain LEFT end, and its extension lines
    start at the left end of the silhouette, which is plain cylinder."""
    ctx = shaft_context()
    ctx.views["front"] = sc.ViewInfo("front", 160.0, 160.0, 2.0, (0, -1, 0), (1, 0, 0), (30, 0, 0), (100.0, 148.0, 220.0, 172.0))
    ctx.geometry = HorizontalShaft()
    ctx.threads = [sc.ThreadInfo("M12", 12.0, 9.853, 20.0, (60, 0, 0), (-1, 0, 0), "face:shaft", 1.75)]
    draw(ctx, {"type": "overall"}, THREAD_DIM)
    label = next(e for e in ctx.sheet.entities if isinstance(e, model.Text) and e.text == "Ø12")
    assert label.x < 100.0, "the Ø is left of the view, over the plain part"
    starts = [e for e in ctx.sheet.entities if isinstance(e, model.Line) and e.layer == model.DIMENSIONS and e.y1 == e.y2 and e.y1 in (148.0, 172.0)]
    assert starts and all(min(e.x1, e.x2) < 105.0 for e in starts), "its extension lines start at the plain left end"
    thread_x = [e.x for e in ctx.sheet.entities if isinstance(e, model.Text) and e.text.startswith("M12")]
    assert thread_x and label.x < min(thread_x)


def test_a_shaft_standing_on_the_page_gets_its_diameter_at_the_plain_end() -> None:
    ctx = shaft_context()  # axis along the page y, the thread at the top (z 40..60)
    draw(ctx, {"type": "overall"}, THREAD_DIM)
    label = next(e for e in ctx.sheet.entities if isinstance(e, model.Text) and e.text == "Ø12")
    assert label.y < 100.0, "the Ø is below the view, at the plain bottom end"
