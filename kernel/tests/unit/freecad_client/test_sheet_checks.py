# ruff: noqa: E501
"""The checks of a finished drawing (``worker/sheet_checks.py``) against the hand-made plate of ``test_sheet_items``: no
FreeCAD. What is checked is what each check does with the geometry and the sheet it is given: it fires on the real
fault, stays quiet when the fault is fixed, and never guesses when a fact is not known."""

import sys
from pathlib import Path
from typing import Any

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import sheet_checks as checks  # noqa: E402
import sheet_context as sc  # noqa: E402
import sheet_model as model  # noqa: E402
import sheet_review as review  # noqa: E402
import sheet_thread  # noqa: E402
import sheet_units as su  # noqa: E402

from .test_sheet_items import hole  # noqa: E402
from .test_sheet_views import run_review, views_context  # noqa: E402


def found(code: str, findings: list[review.Finding]) -> list[review.Finding]:
    return [f for f in findings if f.code == code]


def dowels() -> list[sc.HoleInfo]:
    return [hole(0, 20.0, 20.0, 6.0), hole(1, 80.0, 20.0, 6.0)]


# --- the position of a hole is measured from a reference -------------------------------------------------------------------


def test_holes_located_only_from_each_other_have_no_reference() -> None:
    ctx = views_context([hole(0, 10.0, 10.0), hole(1, 90.0, 10.0)])
    chain = {"type": "dim", "kind": "distance", "from": {"at": [10, 10, 8]}, "to": {"at": [90, 10, 8]}}
    first, second = found("hole_no_reference", run_review(ctx, {"type": "overall"}, chain))  # both hang from each other
    assert first.severity == "warning" and "otros agujeros" in first.message
    assert (first.key, second.key) == ("@10,10,8", "@90,10,8")
    anchored = run_review(
        views_context([hole(0, 10.0, 10.0), hole(1, 90.0, 10.0)]),
        {"type": "overall"},
        chain,
        {"type": "dim", "kind": "distance", "from": {"face": "face:left"}, "to": {"at": [10, 10, 8]}},
    )
    assert found("hole_no_reference", anchored) == []


def test_datum_ordinates_are_a_reference() -> None:
    found_ = run_review(views_context([hole(0, 10.0, 10.0)]), {"type": "overall"}, {"type": "datum_dims"})
    assert found("hole_no_reference", found_) == [] and found("hole_not_located", found_) == []


def test_the_hole_that_has_no_position_is_named_with_its_diameter_and_centre() -> None:
    (finding,) = found("hole_not_located", run_review(views_context([hole(0, 10.0, 10.0)]), {"type": "overall"}))
    assert "Ø6,6 [10, 10, 8]" in finding.message and finding.key == "@10,10,8"


# --- fit holes ---------------------------------------------------------------------------------------------------------------


def test_a_plain_hole_of_a_dowel_size_asks_for_its_fit() -> None:
    findings = run_review(views_context(dowels()), {"type": "overall"})
    named = found("fit_hole_no_tolerance", findings)
    assert [f.key for f in named] == ["@20,20,8", "@80,20,8"] and all(f.severity == "warning" for f in named)
    assert 'fit="H7"' in named[0].message


def test_a_toleranced_callout_closes_the_fit_finding() -> None:
    findings = run_review(views_context(dowels()), {"type": "overall"}, {"type": "hole_dim", "all": True, "fit": "H7"})
    assert found("fit_hole_no_tolerance", findings) == []


def test_clearance_tap_drill_and_threaded_holes_are_not_taken_for_fit_holes() -> None:
    ctx = views_context(
        [hole(0, 20.0, 20.0, 6.6), hole(1, 40.0, 20.0, 5.0), hole(2, 60.0, 20.0, 5.0, thread=("M6", 8.0))]
    )
    assert found("fit_hole_no_tolerance", run_review(ctx, {"type": "overall"})) == []


def test_a_fit_finer_than_it6_needs_a_reason() -> None:
    findings = run_review(views_context(dowels()), {"type": "overall"}, {"type": "hole_dim", "all": True, "fit": "H5"})
    (finding,) = found("tolerance_finer_than_it6", findings)
    assert finding.items == ("h2",) and "IT5" in finding.message
    quiet = run_review(views_context(dowels()), {"type": "overall"}, {"type": "hole_dim", "all": True, "fit": "H7"})
    assert found("tolerance_finer_than_it6", quiet) == []


def test_a_tolerance_band_narrower_than_it6_of_its_size_needs_a_reason() -> None:
    tight = run_review(
        views_context(dowels()), {"type": "overall"}, {"type": "hole_dim", "all": True, "tol": [0.003, 0.0]}
    )
    assert found("tolerance_finer_than_it6", tight)  # 3 µm on Ø6 (IT6 is 8 µm)
    loose = run_review(
        views_context(dowels()), {"type": "overall"}, {"type": "hole_dim", "all": True, "tol": [0.02, 0.0]}
    )
    assert found("tolerance_finer_than_it6", loose) == []


# --- forms: radii ------------------------------------------------------------------------------------------------------------


def facts_with(*rounds: checks.RoundFace, bosses: tuple[checks.Boss, ...] = ()) -> checks.PartFacts:
    return checks.PartFacts(rounds=list(rounds), bosses=list(bosses))


def test_a_radius_with_no_dimension_and_no_note_is_asked_for() -> None:
    ctx = views_context([])
    ctx.extras["facts"] = facts_with(checks.RoundFace("face:c1", 5.0, False), checks.RoundFace("face:c2", 5.0, False))
    (finding,) = found("round_no_dimension", run_review(ctx, {"type": "overall"}))
    assert finding.key == "R5" and "face:c1, face:c2" in finding.message


def test_a_radius_dimension_or_a_note_closes_it() -> None:
    for spec in (
        {"type": "dim", "kind": "radius", "face": "face:fillet"},
        {"type": "note", "text": "Radios R5 salvo indicación"},
        {"type": "note", "text": "Todos los radios no indicados R2,5"},
    ):
        ctx = views_context([])
        ctx.extras["facts"] = facts_with(checks.RoundFace("face:c1", 5.0, False))
        assert found("round_no_dimension", run_review(ctx, {"type": "overall"}, spec)) == [], spec


def test_the_rounds_of_a_fillet_feature_are_left_to_the_feature_check() -> None:
    ctx = views_context([])
    ctx.extras["facts"] = facts_with(checks.RoundFace("face:c1", 5.0, False))
    feature = review.FeatureInfo("feature:3", "fillet", "fillet", ["face:c1"])
    findings = run_review(ctx, {"type": "overall"}, features=[feature])
    assert found("round_no_dimension", findings) == [] and found("feature_no_dimension", findings)


def test_without_facts_the_checks_that_need_them_stay_silent() -> None:
    assert found("round_no_dimension", run_review(views_context([]), {"type": "overall"})) == []


# --- centre marks and axes -------------------------------------------------------------------------------------------------


def test_a_hole_seen_as_a_circle_needs_its_centre_mark() -> None:
    bare = run_review(views_context([hole(0, 10.0, 10.0)]), {"type": "overall"})
    (finding,) = found("centre_mark_missing", bare)
    assert "top" in finding.message and finding.key == "@10,10,8"
    marked = run_review(views_context([hole(0, 10.0, 10.0)]), {"type": "overall"}, {"type": "view_lines"})
    assert found("centre_mark_missing", marked) == []


def test_a_whole_cylinder_needs_its_axis_in_the_view_that_sees_it_across() -> None:
    ctx = views_context([])
    boss = checks.Boss("face:pin", 4.0, (50.0, 30.0, 4.0), (0.0, 0.0, 1.0))
    ctx.extras["facts"] = facts_with(bosses=(boss,))
    (finding,) = found("cylinder_no_axis", run_review(ctx, {"type": "overall"}))
    assert finding.key == "face:pin" and "top" in finding.message
    ctx = views_context([])
    ctx.extras["facts"] = facts_with(bosses=(boss,))
    ctx.add(_cross(ctx.views["top"].project(boss.axis_point)))  # seen along its axis: the mark
    ctx.add(_axis_line(ctx, boss))  # seen across it: the axis
    assert found("cylinder_no_axis", run_review(ctx, {"type": "overall"})) == []


def _cross(centre: tuple[float, float]) -> list[model.Entity]:
    x, y = centre
    return [model.Line(x - 6, y, x + 6, y, model.AXES), model.Line(x, y - 6, x, y + 6, model.AXES)]


def _axis_line(ctx: sc.Context, boss: checks.Boss) -> list[model.Entity]:
    out: list[model.Entity] = []
    for view in ctx.views.values():
        if view.shows_axis_along(boss.axis_dir):
            x, y = view.project(boss.axis_point)
            out.append(model.Line(x, y - 20, x, y + 20, model.AXES))
    return out


# --- text on the part ---------------------------------------------------------------------------------------------------------


def _outline(view: sc.ViewInfo) -> list[model.Entity]:
    x0, y0, x1, y1 = view.box
    return [
        model.Line(x0, y0, x1, y0),
        model.Line(x1, y0, x1, y1),
        model.Line(x1, y1, x0, y1),
        model.Line(x0, y1, x0, y0),
    ]


def _with_text(text_at: tuple[float, float]) -> list[review.Finding]:
    ctx = views_context([])
    ctx.extras["view_ink"] = _outline(ctx.views["top"])
    unit = su.Unit("d1", "d1", su.KIND_ITEM, (model.Text(*text_at, "42", 3.5, model.TEXT, "center"),), "dim")
    ctx.extras["units"] = [unit]
    return run_review(ctx, {"type": "overall"})


def test_a_dimension_text_inside_the_outline_of_a_view_is_flagged_and_outside_is_not() -> None:
    (finding,) = found("text_over_part", _with_text((150.0, 100.0)))  # the middle of the top view
    assert finding.items == ("d1",) and "dentro del contorno" in finding.message
    assert found("text_over_part", _with_text((150.0, 140.0))) == []  # above the top view
    assert found("text_over_part", _with_text((150.0, 100.0))) != []


# --- the general notes ----------------------------------------------------------------------------------------------------------


NOTE_CODES = ["notes_edges", "notes_heat_treatment", "notes_coating", "notes_roughness", "notes_marking"]


def test_a_sheet_with_no_notes_asks_for_all_of_them() -> None:
    findings = run_review(views_context([]), {"type": "overall"})
    assert [f.code for f in findings if f.code.startswith("notes_")] == ["notes_material", *NOTE_CODES]
    assert found("notes_material", findings)[0].severity == "warning"


def test_each_note_closes_its_own_finding() -> None:
    ctx = views_context([])
    ctx.extras["fields"] = {"part_material": "AISI 1045"}
    findings = run_review(
        ctx,
        {"type": "overall"},
        {"type": "note", "text": "Romper aristas vivas 0,3 mm, sin rebabas"},
        {"type": "note", "text": "Sin tratamiento térmico"},
        {"type": "note", "text": "Sin recubrimiento"},
        {"type": "finish", "general": True, "ra": 3.2},
        {"type": "note", "text": "Grabar el número de plano"},
    )
    assert [f.code for f in findings if f.code.startswith("notes_")] == []


def test_the_material_in_the_title_block_or_in_a_note_counts() -> None:
    ctx = views_context([])
    ctx.extras["fields"] = {"part_material": "Aluminio 6061"}
    assert found("notes_material", run_review(ctx, {"type": "overall"})) == []
    assert (
        found(
            "notes_material",
            run_review(views_context([]), {"type": "overall"}, {"type": "note", "text": "Material: acero A36"}),
        )
        == []
    )


def test_a_note_that_sends_the_material_to_the_title_block_needs_a_material_there() -> None:
    note = {"type": "note", "text": "Material según rótulo"}
    (finding,) = found("material_title_block_empty", run_review(views_context([]), {"type": "overall"}, note))
    assert finding.items == ("n2",) and finding.severity == "warning"
    ctx = views_context([])
    ctx.extras["fields"] = {"part_material": "AISI 1045"}
    assert found("material_title_block_empty", run_review(ctx, {"type": "overall"}, note)) == []


def test_a_note_and_the_title_block_must_name_one_material() -> None:
    ctx = views_context([])
    ctx.extras["fields"] = {"part_material": "AISI 1045"}
    (finding,) = found(
        "material_inconsistent", run_review(ctx, {"type": "overall"}, {"type": "note", "text": "Material: AISI 304."})
    )
    assert "AISI 304" in finding.message and "AISI 1045" in finding.message
    same = run_review(ctx, {"type": "overall"}, {"type": "note", "text": "Material: AISI 1045 rectificado."})
    assert found("material_inconsistent", same) == []


# --- hidden lines, sections, threads ----------------------------------------------------------------------------------------------


def test_a_blind_hole_or_a_counterbore_asks_for_a_section_through_it() -> None:
    blind = [hole(0, 10.0, 10.0, 6.6, 5.0, through=False)]
    (finding,) = found("hidden_feature_no_section", run_review(views_context(blind), {"type": "overall"}))
    assert "Ø6,6" in finding.message
    cut = run_review(views_context(blind), {"type": "overall"}, {"type": "section", "holes": "all", "view": "top"})
    assert found("hidden_feature_no_section", cut) == []
    assert (
        found("hidden_feature_no_section", run_review(views_context([hole(0, 10.0, 10.0)]), {"type": "overall"})) == []
    )


def test_a_view_full_of_hidden_lines_asks_for_a_section() -> None:
    ctx = views_context([])
    ctx.extras["view_ink"] = _outline(ctx.views["top"])
    x0, y0, x1, _ = ctx.views["top"].box
    ctx.add([model.Line(x0 + i * 4, y0, x0 + i * 4, y0 + 40, model.HIDDEN) for i in range(14)])
    (finding,) = found("hidden_dense", run_review(ctx, {"type": "overall"}))
    assert finding.key == "top" and "14 líneas ocultas" in finding.message
    quiet = views_context([])
    quiet.extras["view_ink"] = _outline(quiet.views["top"])
    quiet.add([model.Line(x0, y0, x0, y0 + 10, model.HIDDEN)])
    assert found("hidden_dense", run_review(quiet, {"type": "overall"})) == []


def test_a_threaded_hole_no_view_sees_square_on_has_no_thread_representation() -> None:
    slanted = sc.HoleInfo(0, (50.0, 30.0, 8.0), (0.7071, 0.0, 0.7071), 5.0, 8.0, False, ("face:h0",), ("M6", 8.0), True)
    (finding,) = found("thread_no_representation", run_review(views_context([slanted]), {"type": "overall"}))
    assert finding.severity == "warning" and "M6" not in finding.message and finding.key.startswith("@")
    ctx = views_context([hole(0, 10.0, 10.0, 5.0, thread=("M6", 8.0))])
    ctx.add(sheet_thread.thread_entities(ctx))  # what the sheet draws for it in the top view
    assert found("thread_no_representation", run_review(ctx, {"type": "overall"})) == []


# --- ids and the questions ----------------------------------------------------------------------------------------------------------


def test_ids_are_stable_and_unique_and_notices_about_the_window_never_block() -> None:
    rows: list[dict[str, Any]] = [
        {"code": "hole_not_located", "key": "@1,2,3", "items": [], "features": []},
        {"code": "hole_not_located", "key": "@1,2,3", "items": [], "features": []},
        {"code": "feature_no_dimension", "key": "", "items": [], "features": ["feature:4"]},
        {"code": "notes_edges", "key": "edges", "items": [], "features": []},
        {"code": "no_view_lines", "key": "", "items": [], "features": []},
        {"code": "window_differs", "key": "", "items": [], "features": []},
        {"code": "native_fallback", "key": "", "items": ["d3"], "features": []},
    ]
    checks.assign_ids(rows)
    assert [r["id"] for r in rows] == [
        "hole_not_located:@1,2,3",
        "hole_not_located:@1,2,3#2",
        "feature_no_dimension:feature:4",
        "notes_edges:edges",
        "no_view_lines",
        "window_differs",
        "native_fallback:d3",
    ]
    assert [r["blocking"] for r in rows] == [True, True, True, True, True, False, False]


def test_the_questions_of_judgement_keep_only_what_no_memory_principle_asks_and_grow_with_the_part() -> None:
    """Views, sections, dimensions, tolerances and fabrication notes are the base set's ``dibujo-*`` principles: asking
    them here too made the answers rote. What has no counterpart stays."""
    ctx = views_context([])
    ctx.extras["facts"] = checks.PartFacts()
    base = ["q.callouts", "q.line_weights"]
    data = review.ReviewInput([], {}, ctx, ctx.sheet)
    assert [q.id for q in checks.judgement(data)] == base
    ctx.extras["facts"] = checks.PartFacts(sheet_metal=True)
    assert [q.id for q in checks.judgement(data)] == [*base, "q.sheet_metal"]
    english = views_context([])
    english.language = "en"
    assert (
        "Do the callouts give quantity" in checks.judgement(review.ReviewInput([], {}, english, english.sheet))[0].text
    )


def test_the_review_still_finds_what_it_found_before() -> None:
    findings = run_review(views_context(), {"type": "overall"})
    assert len(found("hole_no_callout", findings)) == 4 and len(found("hole_not_located", findings)) == 4
