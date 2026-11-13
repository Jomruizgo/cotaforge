# ruff: noqa: E501
"""The cut list of a welded part (``sheet_weldment.py``, the weldment branch of ``items_assembly.py`` and ``sheet_review.py``)
with canned bodies: no FreeCAD. A frame of 4 equal tubes and 1 plate: 2 positions, the tubes with quantity 4; the
designation is never made up; a weldment sheet with no cut list, or a body with no balloon, is a finding."""

import sys
from pathlib import Path
from typing import Any

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import sheet_context as sc  # noqa: E402
import sheet_items as items  # noqa: E402
import sheet_review as review  # noqa: E402
import sheet_weldment as weld  # noqa: E402

from .test_sheet_items import PlateGeometry, draw, make_context  # noqa: E402

TUBE = {"volume": 40 * 40 * 600 - 34 * 34 * 600, "area": 90000.0, "faces": 16, "dims": (600.0, 40.0, 40.0)}
PLATE = {"volume": 200 * 150 * 6, "area": 62000.0, "faces": 6, "dims": (200.0, 150.0, 6.0)}


def facts(plates: int = 1, tubes: int = 4) -> list[weld.BodyFact]:
    out = [weld.BodyFact(f"body:{n}", material="S235JR", **TUBE) for n in range(1, tubes + 1)]  # type: ignore[arg-type]
    out += [weld.BodyFact(f"body:{tubes + n}", material="S235JR", **PLATE) for n in range(1, plates + 1)]  # type: ignore[arg-type]
    return out


# front view looking along +y: where each body shows a point
VISIBLE = {"body:1": (20.0, 0.0, 6.0), "body:2": (60.0, 0.0, 70.0), "body:3": (100.0, 0.0, 70.0), "body:4": (80.0, 0.0, 50.0), "body:5": (40.0, 0.0, 30.0)}


NOTE = {"type": "note", "text": "Soldadura de filete a=4 mm en todas las uniones, MAG, nivel ISO 5817-C"}
BOXES = [((0, 0, 0), (400, 40, 40)), ((0, 300, 0), (400, 340, 40)), ((0, 0, 300), (400, 40, 340)), ((0, 300, 300), (400, 340, 340)), ((-6, 0, 0), (0, 340, 340))]


class FrameGeometry(PlateGeometry):
    def body_boxes(self) -> list[tuple[sc.Vec3, sc.Vec3]]:
        return BOXES  # type: ignore[return-value]

    def instance_anchors(
        self, instances: list[str], frame: Any, offsets: dict[str, sc.Vec3], margin_mm: float, outward: bool = False
    ) -> dict[str, list[sc.Vec3]]:
        return {c: [VISIBLE[c]] if c in VISIBLE else [] for c in instances}


def context(language: str = "es", **kw: Any) -> sc.Context:
    ctx = make_context(language=language)
    ctx.geometry = FrameGeometry()
    ctx.weldment = weld.group_bodies(facts(**kw), language)
    ctx.area = (25.0, 73.0, 405.0, 282.0)
    ctx.extras["view_boxes"] = [v.box for v in ctx.views.values()]
    ctx.extras["main_scale"] = 1.0
    return ctx


def reviewed(ctx: sc.Context, *specs: dict[str, Any]) -> list[review.Finding]:
    taken: set[str] = set()
    normalised = []
    for number, spec in enumerate(specs, start=1):
        done = items.normalise(spec, taken, number)
        taken.add(done["id"])
        normalised.append(done)
    results = items.draw_all(ctx, normalised)
    return review.review(review.ReviewInput(normalised, {r.id: r for r in results}, ctx, ctx.sheet))


def codes(found: list[review.Finding]) -> list[str]:
    return [f.code for f in found]


def test_identical_bodies_share_a_row_with_their_quantity() -> None:
    groups = context().weldment.groups  # type: ignore[union-attr]
    assert [(g.position, g.quantity, g.instances[0]) for g in groups] == [(1, 4, "body:1"), (2, 1, "body:5")]
    assert [g.reference for g in groups] == ["L 600", "200 × 150 × 6"]  # a bar by its length, a plate by its blank
    assert all(g.name == "" for g in groups)  # the harness does not know the profile: it never makes it up


def test_a_different_length_or_end_cut_is_another_row() -> None:
    longer = weld.BodyFact("body:9", material="S235JR", **{**TUBE, "dims": (650.0, 40.0, 40.0)})  # type: ignore[arg-type]
    mitred = weld.BodyFact("body:10", material="S235JR", **{**TUBE, "area": 91000.0, "faces": 18})  # type: ignore[arg-type]
    assert len(weld.group_bodies([*facts(), longer, mitred], "es").groups) == 4  # type: ignore[union-attr]


def test_one_body_is_a_part_and_not_a_weldment() -> None:
    assert weld.group_bodies(facts(plates=0, tubes=1), "es") is None


def test_the_cut_list_prints_quantity_length_material_and_asks_for_the_designation() -> None:
    ctx = context()
    rows = [{"component": "body:1", "name": "Tubo 40×40×3"}]
    results = draw(ctx, {"type": "parts_list", "rows": rows}, {"type": "balloons", "view": "front"})
    assert all(r.status == "ok" for r in results), results
    printed = ctx.sheet.texts()
    for text in ("Designación", "Longitud / medida (mm)", "Tubo 40×40×3", "L 600", "200 × 150 × 6", "S235JR", "(por definir)"):
        assert text in printed, (text, printed)
    assert ctx.extras["parts_list"]["unnamed"] == [2]
    assert ctx.extras["balloons"]["drawn"] == {1, 2}  # one balloon per position, on a body of it


def test_a_complete_cut_list_with_balloons_raises_no_finding() -> None:
    ctx = context()
    rows = [{"component": "body:1", "name": "Tubo 40×40×3"}, {"component": "body:5", "name": "Chapa 6"}]
    found = reviewed(ctx, {"type": "parts_list", "rows": rows}, {"type": "balloons", "view": "front"}, NOTE)
    assert [c for c in codes(found) if c.startswith("weld_") or c in ("item_without_balloon", "balloon_not_in_list")] == []


def test_a_weldment_sheet_without_cut_list_is_a_finding() -> None:
    found = reviewed(context(), {"type": "balloons", "view": "front"})
    assert "weld_list_missing" in codes(found)


def test_a_body_without_balloon_is_a_finding() -> None:
    ctx = context()
    ctx.geometry.__class__ = type("Hidden", (FrameGeometry,), {"instance_anchors": lambda self, i, *a, **k: {c: [] for c in i}})
    found = reviewed(ctx, {"type": "parts_list"}, {"type": "balloons", "view": "front"})
    (missing,) = [f for f in found if f.code == "item_without_balloon"]
    assert "1 (body:1)" in missing.message and "2 (body:5)" in missing.message


def test_a_body_without_designation_is_a_question_that_never_shows_the_object_name() -> None:
    found = reviewed(context(), {"type": "parts_list"}, {"type": "balloons", "view": "front"})
    (unnamed,) = [f for f in found if f.code == "weld_body_unnamed"]
    assert "1 (body:1: L 600)" in unnamed.message and "nunca el nombre del objeto" in unnamed.message
    english = reviewed(context("en"), {"type": "parts_list"}, {"type": "balloons", "view": "front"})
    assert "never the object name" in next(f.message for f in english if f.code == "weld_body_unnamed")


def test_a_designation_written_in_the_list_closes_the_question() -> None:
    rows = [{"component": "body:1", "name": "Tubo 40×40×3"}, {"component": "body:5", "name": "Pletina 150×6"}]
    found = reviewed(context(), {"type": "parts_list", "rows": rows}, {"type": "balloons", "view": "front"})
    assert "weld_body_unnamed" not in codes(found)


def test_a_weldment_with_no_weld_said_is_a_question_and_a_note_closes_it() -> None:
    found = reviewed(context(), {"type": "parts_list"}, {"type": "balloons", "view": "front"})
    (ask,) = [f for f in found if f.code == "weld_not_specified"]
    assert "qué uniones" in ask.message and "ISO 2553" in ask.message
    found = reviewed(context(), {"type": "parts_list"}, {"type": "balloons", "view": "front"}, NOTE)
    assert "weld_not_specified" not in codes(found)


def test_a_square_frame_draws_the_same_in_front_and_top_and_the_default_views_leave_one_out() -> None:
    assert weld.congruent_views(BOXES) == [("front", "top")]
    assert weld.views_without_repeats(BOXES) == ["front", "right"]
    found = reviewed(context(), {"type": "parts_list"}, {"type": "balloons", "view": "front"}, NOTE)
    (twin,) = [f for f in found if f.code == "view_redundant"]
    assert "top" in twin.message and "front" in twin.message


def test_different_bodies_in_front_and_top_are_not_congruent() -> None:
    boxes = [((0, 0, 0), (400, 40, 40)), ((0, 0, 40), (400, 200, 46))]
    assert weld.congruent_views(boxes) == []


def fact(name_dims: tuple[float, float, float], fill: float) -> weld.BodyFact:
    box = name_dims[0] * name_dims[1] * name_dims[2]
    return weld.BodyFact("body:1", box * fill, 1.0, 6, name_dims)


def test_a_hollow_tube_designation_on_a_solid_body_is_a_mismatch() -> None:
    solid = fact((400.0, 40.0, 40.0), 1.0)
    verdict = weld.check_designation("Tubo 40×40×3", solid)
    assert verdict.status == "mismatch" and "solid" in verdict.why
    hollow = fact((400.0, 40.0, 40.0), (1600 - 34 * 34) / 1600)
    assert weld.check_designation("Tubo 40x40x3", hollow).status == "ok"
    assert weld.check_designation("SHS 40x40x3", hollow).status == "ok"


def test_a_section_that_differs_from_the_measured_box_is_a_mismatch() -> None:
    hollow = fact((400.0, 40.0, 40.0), 0.3)
    verdict = weld.check_designation("Tubo 50×50×3", hollow)
    assert verdict.status == "mismatch" and "40" in verdict.why and "50" in verdict.why
    assert weld.check_designation("Pletina 50×6", fact((400.0, 50.0, 6.0), 1.0)).status == "ok"
    assert weld.check_designation("Pletina 50×8", fact((400.0, 50.0, 6.0), 1.0)).status == "mismatch"
    assert weld.check_designation("Chapa 6", fact((340.0, 340.0, 6.0), 1.0)).status == "ok"
    assert weld.check_designation("Chapa 8", fact((340.0, 340.0, 6.0), 1.0)).status == "mismatch"


def test_other_designations_are_skipped_and_said() -> None:
    assert weld.check_designation("Tubo redondo 48,3×3", fact((400.0, 48.0, 48.0), 0.2)).status == "skipped"
    assert weld.check_designation("Perfil IPE 100", fact((400.0, 100.0, 55.0), 0.2)).status == "skipped"


def test_review_flags_a_mismatching_designation_blocking_and_says_what_it_skipped() -> None:
    rows = [{"component": "body:1", "name": "Tubo 40×40×3"}, {"component": "body:5", "name": "Perfil especial"}]
    ctx = context()
    for group in ctx.weldment.groups:  # type: ignore[union-attr]
        if group.position == 1:  # the canned bar is solid in this test
            group.measured = fact((600.0, 40.0, 40.0), 1.0)
    found = reviewed(ctx, {"type": "parts_list", "rows": rows}, {"type": "balloons", "view": "front"}, NOTE)
    (wrong,) = [f for f in found if f.code == "weld_designation_mismatch"]
    assert "body:1" in wrong.message and "solid" in wrong.message
    (skipped,) = [f for f in found if f.code == "weld_designation_unchecked"]
    assert "Perfil especial" in skipped.message
    from sheet_checks import NON_BLOCKING  # noqa: PLC0415

    assert "weld_designation_unchecked" in NON_BLOCKING and "weld_designation_mismatch" not in NON_BLOCKING


def test_an_unknown_body_in_the_rows_is_refused_with_the_known_ones() -> None:
    ctx = context()
    results = draw(ctx, {"type": "parts_list", "rows": [{"component": "body:99", "name": "x"}]})
    assert results[0].status == "error" and "body:1" in results[0].detail


def test_a_part_with_no_bodies_to_list_refuses_the_items() -> None:
    ctx = make_context()
    ctx.geometry = FrameGeometry()
    results = draw(ctx, {"type": "parts_list"})
    assert results[0].status == "error" and "welded" in results[0].detail
