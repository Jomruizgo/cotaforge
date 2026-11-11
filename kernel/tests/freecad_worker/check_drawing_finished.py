# ruff: noqa: E501
"""A finished drawing in the headless worker: the review of the bracket as real geometry gives its findings with ids
(holes named, dowel-size holes, notes, a section for the blind and counterbored holes) and the questions of judgement;
an answer is kept in the sheet and survives a save and a reopen; a fixed finding is gone; ``approve`` fills ``Aprobó``
and any change of the sheet empties it again."""

from typing import Any

import drawing
import main
import sheet_model as model
from check_drawing_views import add, build_bracket, composed
from check_save_resume import reopen, save_and_reset, workdir
from harness import call, circle, expect_error, extrude, faces_of, op, select_face, sketch

NOT_APPLIES = "no aplica: la pieza no lleva este requisito"


def review() -> dict[str, Any]:
    return call("describe_drawing", op("r", review=True))["sheets"][0]


def answer(item_id: str, text: str = NOT_APPLIES, verdict: str = "not_applies", **extra: Any) -> dict[str, Any]:
    return call("annotate_drawing", op("a", action="answer", id=item_id, verdict=verdict, text=text, **extra))


def codes(sheet: dict[str, Any]) -> set[str]:
    return {f["code"] for f in sheet["findings"]}


def bracket_sheet() -> None:
    build_bracket()
    call("create_drawing", op("d", template="A3", scale=1.0))
    add({"type": "hole_dim", "all": True})


def check_the_review_of_real_geometry_gives_findings_with_ids_and_the_questions() -> None:
    bracket_sheet()
    sheet = review()
    found = sheet["findings"]
    ids = [f["id"] for f in found]
    assert len(ids) == len(set(ids)) and all(f["blocking"] for f in found), ids
    assert {
        "hole_not_located",
        "fit_hole_no_tolerance",
        "notes_material",
        "notes_edges",
        "notes_heat_treatment",
        "notes_coating",
        "notes_roughness",
        "notes_marking",
        "hidden_feature_no_section",
    } <= codes(sheet), codes(sheet)
    assert "centre_mark_missing" not in codes(sheet)  # the callouts draw the marks of the holes they name
    located = [f for f in found if f["code"] == "hole_not_located"]
    assert located and all("Ø" in f["message"] and f["id"].startswith("hole_not_located:@") for f in located)
    (fit,) = [f for f in found if f["code"] == "fit_hole_no_tolerance"]
    assert "Ø6" in fit["message"] and fit["id"] == "fit_hole_no_tolerance:@67.5,20,21", fit
    assert [q["id"] for q in sheet["questions"]] == ["q.callouts", "q.line_weights"]  # the rest are memory principles
    assert (
        all(q["answer"] is None for q in sheet["questions"]) and sheet["answers"] == {} and sheet["approved_by"] == ""
    )


def check_a_hole_seen_as_a_circle_with_no_mark_is_found_and_view_lines_closes_it() -> None:
    build_bracket()
    call("create_drawing", op("d", template="A3", scale=1.0))
    call("annotate_drawing", op("noloc", action="remove", ids=["loc"], items=[]))  # the default location marks the axes
    marks = [f for f in review()["findings"] if f["code"] == "centre_mark_missing"]
    assert len(marks) == 2 and all("top" in f["message"] for f in marks), marks  # the counterbore and the blind hole
    add({"type": "section", "holes": "all", "view": "top"})
    add({"type": "view_lines"})
    assert "centre_mark_missing" not in codes(review())
    assert "hidden_feature_no_section" not in codes(review())  # the section goes through the holes


def check_a_slot_end_and_a_boss_are_read_from_the_real_part_and_the_items_close_them() -> None:
    base = build_bracket(with_holes=False)
    top = select_face(faces_of(base["feature"]), normal=(0, 0, 1), area_min=1000)
    slot_entity = {"kind": "slot", "name": "sl", "start": [-15, -10], "end": [-15, 10], "width": 6}
    slot = sketch("slot_sk", top["handle"], [slot_entity])
    cut = op(
        "slot",
        sketch=slot["sketch"],
        end_condition="blind",
        depth_mm=5,
        direction="negative",
        both_directions=False,
        depth2_mm=None,
    )
    call("cut", cut)
    pin = sketch("pin_sk", top["handle"], [circle("pin", (10, 0), 10)])
    extrude("pin", pin, 12)
    call("create_drawing", op("d", template="A3", scale=1.0))
    found = review()["findings"]
    (rounded,) = [f for f in found if f["code"] == "round_no_dimension"]
    assert rounded["key"] == "R3" and "R3" in rounded["message"], rounded  # the two ends of the slot, one finding
    (axis,) = [f for f in found if f["code"] == "cylinder_no_axis"]
    assert "Ø10" in axis["message"], axis
    add({"type": "view_lines"})  # the boss gets its mark in the view that sees it along its axis and its axis across
    assert [e for e in composed()[0].entities if getattr(e, "layer", None) == model.AXES]
    assert "cylinder_no_axis" not in codes(review())
    add({"type": "note", "text": "Radios R3 salvo indicación"})
    assert "round_no_dimension" not in codes(review())


def check_an_answer_is_kept_in_the_sheet_and_survives_save_and_reopen() -> None:
    bracket_sheet()
    answer("notes_heat_treatment:heat_treatment", "no aplica: acero de bajo carbono sin tratamiento")
    answer("q.callouts", "applies: cada llamada lleva cantidad, diámetro y profundidad", verdict="applies")
    answer(
        "drawing-manufacturing-data-in-title-block",
        "applies: las notas generales cubren aristas",
        verdict="applies",
        memory=True,
    )
    sheet = review()
    done = {f["id"]: f["answer"] for f in sheet["findings"] if f["answer"]}
    assert done["notes_heat_treatment:heat_treatment"]["verdict"] == "not_applies"
    assert {q["id"] for q in sheet["questions"] if q["answer"]} == {"q.callouts"}
    assert set(sheet["answers"]) == {
        "notes_heat_treatment:heat_treatment",
        "q.callouts",
        "drawing-manufacturing-data-in-title-block",
    }
    before = dict(sheet["answers"])
    with workdir() as directory:
        path, state, _ = save_and_reset(directory, "soporte")
        reopen(path, state)
        after = review()
        assert after["answers"] == before
        assert {f["id"] for f in after["findings"] if f["answer"]} == {"notes_heat_treatment:heat_treatment"}
        assert [q["answer"]["verdict"] for q in after["questions"] if q["answer"]] == ["applies"]


def check_an_answer_must_name_something_the_review_has() -> None:
    bracket_sheet()
    refused = expect_error(
        "invalid_request",
        "annotate_drawing",
        op("a", action="answer", id="notes_nope", verdict="applies", text="applies: algo que no existe"),
    )
    assert "notes_nope" in refused.message and "pending:" in refused.message
    expect_error(
        "invalid_request", "annotate_drawing", op("a", action="answer", id="q.callouts")
    )  # no verdict, no text


def check_a_fixed_finding_is_gone_and_its_stale_answer_does_not_count() -> None:
    bracket_sheet()
    assert "notes_edges" in codes(review())
    answer("notes_edges:edges")
    add({"type": "note", "text": "Romper aristas vivas 0,3 a 0,5 mm; sin rebabas"})
    sheet = review()
    assert "notes_edges" not in codes(sheet) and "notes_edges:edges" in sheet["answers"]  # kept, never asked again


def check_a_material_in_a_note_and_a_title_block_that_disagree_are_found() -> None:
    bracket_sheet()
    add({"type": "note", "text": "Material según rótulo"})
    assert {"material_title_block_empty"} <= codes(review())
    call("create_drawing", op("d", template="A3", scale=1.0, material="AISI 1045"))
    add({"type": "note", "text": "Material: AISI 304"})
    sheet = review()
    assert "material_inconsistent" in codes(sheet) and "notes_material" not in codes(sheet), codes(sheet)


def check_approve_fills_the_title_block_and_any_change_empties_it() -> None:
    bracket_sheet()
    expect_error("invalid_request", "annotate_drawing", op("a", action="approve", by="Ana"))
    assert call("annotate_drawing", op("a", action="approve", by="Ana", text="Ana: apruébalo"))["sheet"] == "Drawing"
    sheet = review()
    assert sheet["approved_by"] == "Ana" and sheet["fields"]["approval_person"] == "Ana"
    page = drawing.find_page(main.WORKER.active.fc_doc)
    assert dict(page.Template.EditableTexts)["approval_person"] == "Ana"
    assert "Ana" in composed()[0].texts()  # the PDF and the DXF are written from this sheet
    assert drawing.spec_of(page)["approval"]["reason"] == "Ana: apruébalo"
    with workdir() as directory:
        path, state, _ = save_and_reset(directory, "soporte")
        reopen(path, state)
        assert review()["approved_by"] == "Ana"
    add({"type": "note", "text": "Sin rebabas"})  # the drawing changed: nobody approved this one
    changed = review()
    assert changed["approved_by"] == "" and "approval_person" not in changed["fields"]
    assert "approval" not in drawing.spec_of(drawing.find_page(main.WORKER.active.fc_doc))
    call("annotate_drawing", op("a", action="approve", by="Ana", text="Ana: apruébalo otra vez"))
    call("annotate_drawing", op("u", action="update"))
    assert review()["approved_by"] == ""
    call("annotate_drawing", op("a", action="approve", by="Ana", text="Ana: apruébalo de nuevo"))
    call("annotate_drawing", op("v", action="remove_view", view="right"))
    assert review()["approved_by"] == ""


def check_nothing_but_approve_writes_the_approver() -> None:
    build_bracket()
    call("create_drawing", op("d", template="A3", scale=1.0, title="Soporte", author="Pedro", company="Taller"))
    sheet = call("describe_drawing", op("l"))["sheets"][0]
    assert "approval_person" not in sheet["fields"]
    again = call("create_drawing", op("d", template="A3", scale=1.0, author="Eva"))  # a rebuilt sheet starts empty
    assert again["sheet"] == "Drawing"
    assert "approval_person" not in call("describe_drawing", op("l"))["sheets"][0]["fields"]


# --- the reviews: request, choice and report, kept in the sheet and old after any change of it -----------------------------


def report(by: str, *remarks: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return call("annotate_drawing", op("r", action="review_report", by=by, remarks=list(remarks), at=100.0, **extra))


def check_a_report_is_kept_in_the_sheet_with_ids_and_answers_and_survives_save_and_reopen() -> None:
    bracket_sheet()
    sheet = review()
    assert sheet["reports"] == {} and sheet["review_request"] is None and sheet["review_choice"] == ""
    assert sheet["printed"] and any("Ø" in t for t in sheet["printed"]), sheet["printed"]
    done = report(
        "fabricator",
        {"kind": "missing", "where": "vista superior", "text": "Falta la profundidad", "fix": "Añadir ↧"},
        {"text": "Nota 2 ambigua"},
    )
    assert done["changed"] == ["fab1.1", "fab1.2"]
    sheet = review()
    fab = sheet["reports"]["fabricator"]
    assert (fab["n"], fab["edit"], [r["id"] for r in fab["remarks"]]) == (1, sheet["edits"], ["fab1.1", "fab1.2"])
    assert fab["remarks"][1]["kind"] == "other" and fab["remarks"][0]["fix"] == "Añadir ↧"
    answer(
        "fab1.1", "applies: añadí la profundidad en la llamada", verdict="applies"
    )  # a remark id is a valid answer id
    expect_error(
        "invalid_request",
        "annotate_drawing",
        op("a", action="answer", id="fab9.9", verdict="applies", text="applies: no existe"),
    )
    assert "fab1.1" in review()["answers"]
    assert report("engineer")["changed"] == []  # a report with no remarks is a report
    assert review()["reports"]["engineer"]["n"] == 2  # the sheet numbers its reports
    with workdir() as directory:
        path, state, _ = save_and_reset(directory, "soporte")
        reopen(path, state)
        after = review()
        assert after["reports"]["fabricator"]["remarks"][0]["id"] == "fab1.1" and "fab1.1" in after["answers"]
        assert after["reports"]["fabricator"]["edit"] == after["edits"]


def check_a_change_of_the_sheet_counts_as_an_edit_but_keeps_the_reports_the_request_and_the_choice() -> None:
    bracket_sheet()
    call("annotate_drawing", op("q", action="request_review", at=50.0))
    call("annotate_drawing", op("c", action="review_choice", by="fabricator"))
    report("fabricator", {"text": "Falta algo"})
    answer("notes_edges:edges")  # an answer is not a change of the sheet
    sheet = review()
    assert sheet["review_request"] == {"at": 50.0} and sheet["review_choice"] == "fabricator"
    before = sheet["edits"]
    assert sheet["reports"]["fabricator"]["edit"] == before
    add({"type": "note", "text": "Romper aristas vivas 0,3 a 0,5 mm; sin rebabas"})
    later = review()
    assert later["edits"] == before + 1  # the counter shows what changed after the review ...
    assert later["review_request"] == {"at": 50.0} and later["review_choice"] == "fabricator"  # ... and gates nothing
    assert later["reports"]["fabricator"]["edit"] == before and later["reports"]["fabricator"]["remarks"]
    call("annotate_drawing", op("u", action="update"))
    call("annotate_drawing", op("c", action="review_choice", by="engineer"))  # the engineer may change his mind
    assert review()["review_choice"] == "engineer"
    call("annotate_drawing", op("v", action="remove_view", view="right"))
    assert review()["review_choice"] == "engineer" and review()["edits"] == before + 3
    expect_error("invalid_request", "annotate_drawing", op("c", action="review_choice", by="vecino"))
    expect_error("invalid_request", "annotate_drawing", op("q", action="request_review"))  # no time given


def check_an_export_writes_one_sheet_to_one_file() -> None:
    bracket_sheet()
    call("create_drawing", op("d2", template="A3", scale=1.0, sheet="Detalle"))
    with workdir() as directory:
        pdf = f"{directory}/one.pdf"
        expect_error("invalid_request", "export_drawing", op("e", path=pdf, format="pdf"))  # two sheets, none named
        written = call("export_drawing", op("e", path=pdf, format="pdf", sheet="Detalle"))
        assert written["paths"] == [pdf]
        dxf = f"{directory}/other.dxf"
        assert call("export_drawing", op("e", path=dxf, format="dxf", sheet="Drawing"))["paths"] == [dxf]
        import os  # noqa: PLC0415

        assert sorted(os.listdir(directory)) == ["one.pdf", "other.dxf"]
