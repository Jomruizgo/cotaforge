# ruff: noqa: E501
"""What the table of requirements of a drawing reads from the sheet and the part (``worker/sheet_reqfacts.py``), the three
fixes that came with it (a bolt pattern dimensioned centre to centre is located; a plate in four views at 1:1 on A3 is
flagged and told what would fill the sheet; the paper a sheet gets when nobody chose one): no FreeCAD, on the hand-made plate
of ``test_sheet_items``."""

import sys
from pathlib import Path
from typing import Any

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import sheet_checks as checks  # noqa: E402
import sheet_context as sc  # noqa: E402
import sheet_items as items  # noqa: E402
import sheet_layout as layout  # noqa: E402
import sheet_model as model  # noqa: E402
import sheet_reqfacts as reqfacts  # noqa: E402
import sheet_review as review  # noqa: E402
import sheet_viewchecks as viewchecks  # noqa: E402
import sheet_viewrules as vr  # noqa: E402

from .test_sheet_items import hole  # noqa: E402
from .test_sheet_views import ViewsGeometry, run_review, views_context  # noqa: E402


class TurnedGeometry(ViewsGeometry):
    """A shaft: a body of revolution about x with an outer cylinder of Ø40 (``round_extent``) and faces of Ø40 and Ø25."""

    def round_extent(self) -> tuple[sc.Vec3, float]:
        return (1.0, 0.0, 0.0), 40.0

    def face(self, face_id: str) -> sc.FaceInfo:
        if face_id == "face:step":
            return sc.FaceInfo("face:step", "cylinder", (0.0, 0.0, 0.0), radius=12.5, axis_point=(0.0, 0.0, 0.0), axis_dir=(1.0, 0.0, 0.0))
        return super().face(face_id)


def facts_of(ctx: sc.Context, *specs: dict[str, Any], features: list[review.FeatureInfo] | None = None) -> dict[str, int]:
    taken: set[str] = set()
    normalised = []
    for number, spec in enumerate(specs, start=1):
        done = items.normalise(spec, taken, number)
        taken.add(done["id"])
        normalised.append(done)
    results = items.draw_all(ctx, normalised)
    data = review.ReviewInput(normalised, {r.id: r for r in results}, ctx, ctx.sheet, features or [])
    return reqfacts.facts(data)


def titled(ctx: sc.Context, material: str = "AISI 1045", tolerance: str = "ISO 2768-mK") -> sc.Context:
    ctx.extras["fields"] = {"part_material": material, "general_tolerances": tolerance}
    ctx.extras["main_scale"] = 1.0
    ctx.extras["angle"] = "first"
    return ctx


# --- the facts --------------------------------------------------------------------------------------------------------------


def test_the_facts_are_exactly_the_names_the_table_reads() -> None:
    found = facts_of(titled(views_context()), {"type": "overall"})
    assert tuple(found) == reqfacts.FACT_NAMES and all(isinstance(v, int) for v in found.values())


def test_a_plate_with_a_title_block_counts_its_holes_and_nothing_measured_beyond() -> None:
    found = facts_of(titled(views_context()), {"type": "overall"})
    assert (found["sheet"], found["part"], found["assembly"], found["holes"]) == (1, 1, 0, 4)
    assert found["threaded_holes"] == found["fit_holes"] == found["bosses"] == found["turned"] == found["sheet_metal"] == 0
    assert (found["title_material"], found["title_general_tolerance"], found["title_scale"], found["title_projection"]) == (1, 1, 1, 1)
    assert found["quantity_on_sheet"] == found["process_on_sheet"] == found["surface_texture_on_sheet"] == 0


def test_a_title_block_with_nothing_in_it_counts_nothing() -> None:
    found = facts_of(titled(views_context(), material="", tolerance=""), {"type": "overall"})
    assert found["title_material"] == 0 and found["title_general_tolerance"] == 0


def test_a_threaded_hole_is_measured_and_its_designation_is_found_once_the_callout_prints_it() -> None:
    ctx = titled(views_context([hole(0, 20.0, 20.0, 5.0, thread=("M6", 8.0))]))
    before = facts_of(ctx, {"type": "overall"})
    assert before["threaded_holes"] == 1 and before["thread_designation_on_sheet"] == 0
    after = facts_of(titled(views_context([hole(0, 20.0, 20.0, 5.0, thread=("M6", 8.0))])), {"type": "overall"}, {"type": "hole_dim", "all": True})
    assert after["thread_designation_on_sheet"] == 1


def test_a_hole_made_with_a_fit_is_a_fit_hole_until_a_callout_covers_it() -> None:
    def fitted() -> list[sc.HoleInfo]:
        return [sc.HoleInfo(0, (20.0, 20.0, 8.0), (0.0, 0.0, -1.0), 6.0, 8.0, True, ("face:h0",), None, True, None, "H7", "precision", 8)]

    before = facts_of(titled(views_context(fitted())), {"type": "overall"})
    assert before["fit_holes"] == 1 and before["fit_holes_uncalled"] == 1 and before["functional_faces"] == 1
    after = facts_of(titled(views_context(fitted())), {"type": "overall"}, {"type": "hole_dim", "all": True, "fit": "H7"})
    assert after["fit_holes_uncalled"] == 0 and after["tolerance_classes_on_sheet"] >= 1
    plain_callout = facts_of(titled(views_context(fitted())), {"type": "overall"}, {"type": "hole_dim", "all": True})
    assert plain_callout["tolerance_classes_on_sheet"] == 1  # a callout with no fit of its own prints the one the hole declared
    plain = facts_of(titled(views_context([hole(0, 20.0, 20.0, 6.6)])), {"type": "overall"})
    assert plain["fit_holes"] == 0  # a clearance hole is not a fit


def test_the_notes_the_sheet_carries_are_found_by_what_they_say() -> None:
    notes = [
        {"type": "note", "text": "Cantidad: 10 pzas"},
        {"type": "note", "text": "Proceso: corte láser y doblado"},
        {"type": "note", "text": "Rugosidad general Ra 3,2"},
        {"type": "note", "text": "Radio interior de doblez 2 mm, desarrollo en la hoja"},
        {"type": "note", "text": "Soldadura en ángulo, clase ISO 13920 B"},
    ]
    found = facts_of(titled(views_context()), {"type": "overall"}, *notes)
    assert found["quantity_on_sheet"] == 1 and found["process_on_sheet"] == 1
    assert found["surface_texture_on_sheet"] == 1 and found["bend_note_on_sheet"] == 1
    assert found["weld_marked_on_sheet"] == 1 and found["weld_class_on_sheet"] == 1


def test_a_shaft_counts_its_bosses_and_the_steps_nobody_dimensioned() -> None:
    ctx = titled(views_context([]))
    ctx.geometry = TurnedGeometry()
    ctx.extras["facts"] = checks.PartFacts(bosses=[checks.Boss("face:b1", 20.0, (0, 0, 0), (1, 0, 0), 80.0), checks.Boss("face:step", 12.5, (0, 0, 0), (1, 0, 0), 30.0)])
    found = facts_of(ctx, {"type": "overall"})
    assert found["turned"] == 1 and found["bosses"] == 2
    assert found["boss_radii_undimensioned"] == 1  # the overall Ø40 says the first; the step of Ø25 has no dimension
    ctx2 = titled(views_context([]))
    ctx2.geometry = TurnedGeometry()
    ctx2.extras["facts"] = ctx.extras["facts"]
    done = facts_of(ctx2, {"type": "overall"}, {"type": "dim", "kind": "diameter", "face": "face:step"})
    assert done["boss_radii_undimensioned"] == 0


def test_a_part_with_no_round_extent_is_not_turned_and_sheet_metal_is_read_from_the_part() -> None:
    ctx = titled(views_context([]))
    ctx.extras["facts"] = checks.PartFacts(sheet_metal=True)
    found = facts_of(ctx, {"type": "overall"})
    assert found["turned"] == 0 and found["sheet_metal"] == 1


def test_a_keyway_is_named_by_the_feature_that_made_it() -> None:
    features = [review.FeatureInfo("f3", "cut", "Chavetero del eje", ["face:k1"]), review.FeatureInfo("f4", "cut", "Slot", ["face:s1"])]
    assert facts_of(titled(views_context([])), {"type": "overall"}, features=features)["keyways"] == 1


def test_a_keyway_the_shape_shows_is_measured_and_the_name_is_the_fallback() -> None:
    ctx = titled(views_context([]))
    ctx.extras["facts"] = checks.PartFacts(keyways=1)
    found = facts_of(ctx, {"type": "overall"})
    assert found["keyways"] == 1 and found["keyways_measured"] == 1
    named = facts_of(titled(views_context([])), {"type": "overall"}, features=[review.FeatureInfo("k", "cut", "keyway", ["f"])])
    assert named["keyways"] == 1 and named["keyways_measured"] == 0  # by name: the agent may say why it does not apply


def test_an_external_thread_is_a_feature_named_so_that_no_hole_made() -> None:
    ctx = titled(views_context([]))
    ctx.extras["facts"] = checks.PartFacts(thread_features=2)
    assert facts_of(ctx, {"type": "overall"})["thread_features"] == 2


# --- fix: a bolt pattern dimensioned centre to centre is located -----------------------------------------------------------------


def centre_to_centre(*extra: dict[str, Any]) -> list[dict[str, Any]]:
    corner_x = {"type": "dim", "kind": "distance", "from": {"face": "face:left"}, "to": {"at": [10, 10, 8]}}
    corner_y = {"type": "dim", "kind": "distance", "from": {"face": "face:front"}, "to": {"at": [10, 10, 8]}}
    row = {"type": "dim", "kind": "distance", "from": {"at": [10, 10, 8]}, "to": {"at": [90, 10, 8]}}  # centre to centre along x
    column = {"type": "dim", "kind": "distance", "from": {"at": [10, 10, 8]}, "to": {"at": [10, 50, 8]}}
    last = {"type": "dim", "kind": "distance", "from": {"at": [90, 10, 8]}, "to": {"at": [90, 50, 8]}}
    return [corner_x, corner_y, row, column, last, *extra]


def partly(found: list[review.Finding]) -> list[review.Finding]:
    return [f for f in found if f.code == "hole_partly_located"]


def test_a_bolt_pattern_dimensioned_centre_to_centre_from_one_corner_is_located() -> None:
    found = run_review(views_context(), {"type": "overall"}, *centre_to_centre())
    assert partly(found) == [] and not [f for f in found if f.code in ("hole_not_located", "hole_no_reference")]


def test_a_hole_off_the_row_is_still_partly_located() -> None:
    ctx = views_context([hole(0, 10.0, 10.0), hole(1, 90.0, 12.0)])  # not on the same row: the dimension along x does not give y
    row = {"type": "dim", "kind": "distance", "from": {"at": [10, 10, 8]}, "to": {"at": [90, 12, 8]}, "along": "x"}
    corner_x = {"type": "dim", "kind": "distance", "from": {"face": "face:left"}, "to": {"at": [10, 10, 8]}}
    corner_y = {"type": "dim", "kind": "distance", "from": {"face": "face:front"}, "to": {"at": [10, 10, 8]}}
    found = partly(run_review(ctx, {"type": "overall"}, corner_x, corner_y, row))
    assert len(found) == 1 and "falta su posición en y" in found[0].message


def test_a_chain_that_never_touches_the_part_is_still_asked_for_a_reference() -> None:
    row = {"type": "dim", "kind": "distance", "from": {"at": [10, 10, 8]}, "to": {"at": [90, 10, 8]}}
    found = run_review(views_context([hole(0, 10.0, 10.0), hole(1, 90.0, 10.0)]), {"type": "overall"}, row)
    assert [f.code for f in found if f.code == "hole_no_reference"]  # centre to centre alone is not a location


# --- fix: the sheet that wastes its paper --------------------------------------------------------------------------------------


def a3_plate(scale: float = 1.0) -> sc.Context:
    """A 100 x 60 x 8 plate in four views at 1:1 on A3, laid out as ``sheet_layout`` does."""
    width, height = layout.PAGES_MM["A3"]
    left, top, right, bottom = layout.drawing_area(width, height)
    ctx = sc.Context(sheet=model.Sheet(width, height), views={}, holes=[], geometry=sc.Geometry(), area=(left, height - bottom, right, height - top), language="es")
    cx, cy = (left + right) / 2, (height - bottom + height - top) / 2
    boxes = {
        "top": (cx - 130, cy - 60, cx - 30, cy),
        "front": (cx - 130, cy + 10, cx - 30, cy + 18),
        "right": (cx - 200, cy + 10, cx - 140, cy + 18),
    }
    ctx.views = {n: sc.ViewInfo(n, (b[0] + b[2]) / 2, (b[1] + b[3]) / 2, scale, (0, 0, 1), (1, 0, 0), (50, 30, 4), b) for n, b in boxes.items()}
    ctx.extras.update(
        {
            "view_boxes": [*boxes.values(), (cx + 10, cy - 60, cx + 148, cy + 26)],  # the isometric view, 138 x 86
            "main_scale": scale,
            "extent_mm": (100.0, 60.0, 8.0),
            "angle": "first",
        }
    )
    return ctx


def underused(ctx: sc.Context) -> list[review.Finding]:
    return [f for f in viewchecks.check(review.ReviewInput([], {}, ctx, ctx.sheet)) if f.code in ("sheet_underused", "sheet_unbalanced")]


def test_a_plate_in_four_views_at_1_to_1_on_a3_is_not_told_to_drop_the_isometric_view() -> None:
    """The isometric view stays on every sheet: the alternatives the review offers keep it, and none of them gives the plate a
    larger standard scale, so the sheet is not flagged for wasting paper (it used to be told to take the isometric off)."""
    assert underused(a3_plate()) == []
    options = viewchecks._fewer_views(a3_plate())  # noqa: SLF001
    assert all(what.endswith("iso]") for what, _ in options)


def test_the_alternatives_are_the_fewest_views_dropped_first() -> None:
    options = viewchecks._fewer_views(a3_plate())  # noqa: SLF001
    assert options[0][0].startswith("views=[front, top, iso]") and options[-1][0] == "views=[front, iso]"
    assert all(scale >= 1.0 for _, scale in options)


def test_a_part_that_fills_its_sheet_is_left_alone() -> None:
    ctx = a3_plate()
    ctx.views = {n: v for n, v in ctx.views.items()}
    ctx.extras["view_boxes"] = [(40.0, 90.0, 260.0, 190.0), (40.0, 200.0, 260.0, 260.0), (270.0, 90.0, 400.0, 260.0)]  # views that take the area
    assert underused(ctx) == []


def test_the_rule_needs_a_remedy_to_flag_the_views_themselves() -> None:
    boxes = [(30.0, 110.0, 130.0, 170.0), (140.0, 110.0, 200.0, 118.0), (210.0, 100.0, 350.0, 190.0)]
    area = (25.0, 60.0, 405.0, 282.0)
    assert vr.sheet_underused(boxes, area, 1.0, views=boxes) is None  # the hull spans the sheet and nothing can be offered
    found = vr.sheet_underused(boxes, area, 1.0, views=boxes, fewer=[("views=[front, top]", 2.0)])
    assert found is not None and found.reason == "small" and found.fewer == "views=[front, top]" and found.ink < vr.LOW_INK
    assert vr.sheet_underused(boxes, area, 1.0, views=boxes, fewer=[("views=[front]", 1.0)]) is None  # no larger scale: no remedy


# --- the paper a sheet gets when nobody chose one --------------------------------------------------------------------------------


def test_the_paper_is_chosen_from_the_part_when_nobody_chose_one() -> None:
    assert layout.auto_paper(20, 20, 20, True, True, True, iso_dims=(20, 20, 20)) == ("A4", 2.0)  # a cube: A4, enlarged
    paper, scale = layout.auto_paper(100, 60, 8, True, True, True, iso_dims=(100, 60, 8))
    assert (paper, scale) == ("A3", 1.0)  # the plate: full size on A3
    assert layout.auto_paper(100, 100, 50, True, True, True, iso_dims=(100, 100, 50)) == ("A4", 0.5)  # not a bigger sheet for a 1:1 it does not need
    assert layout.auto_paper(3000, 1000, 500, True, True, True, iso_dims=(3000, 1000, 500))[1] <= 0.2  # a big part: the largest scale there is


def test_a_sheet_of_fewer_views_takes_a_smaller_paper() -> None:
    four = layout.auto_paper(100, 60, 8, True, True, True, iso_dims=(100, 60, 8))
    two = layout.auto_paper(100, 60, 8, False, True, False, iso_dims=(100, 60, 8))
    assert layout.AUTO_PAPERS.index(two[0]) <= layout.AUTO_PAPERS.index(four[0]) and two[1] >= four[1]
