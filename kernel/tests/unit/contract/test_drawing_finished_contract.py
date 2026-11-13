"""The contract of a finished drawing: the answer and approve actions, the ids and answers of the review."""

import pytest
from pydantic import ValidationError

from cotaforge.contract import (
    AnnotateDrawingRequest,
    DescribeDrawingResult,
    DrawingAnswer,
    DrawingFinding,
    DrawingQuestion,
    DrawingSheetInfo,
)


def test_an_answer_names_what_it_answers_and_says_how() -> None:
    ok = AnnotateDrawingRequest(
        operation_id="a", action="answer", id="q.views", verdict="not_applies", text="no aplica: una vista basta"
    )
    assert (ok.id, ok.verdict, ok.memory) == ("q.views", "not_applies", False)
    assert AnnotateDrawingRequest(
        operation_id="a", action="answer", id="x", verdict="applies", text="applies: how", memory=True
    ).memory
    for fields in ({}, {"id": "q.views"}, {"id": "q.views", "verdict": "applies"}, {"verdict": "applies", "text": "t"}):
        with pytest.raises(ValidationError, match="answer needs id, verdict"):
            AnnotateDrawingRequest(operation_id="a", action="answer", **fields)
    with pytest.raises(ValidationError):
        AnnotateDrawingRequest(operation_id="a", action="answer", id="q", verdict="maybe", text="t")
    ordered = AnnotateDrawingRequest(
        operation_id="a", action="answer", id="q", verdict="not_applies", text="t", by="Ana"
    )  # the engineer who ordered a does-not-apply the harness would not take on the agent's word
    assert ordered.by == "Ana"


def test_approve_is_the_engineer_naming_himself_and_quoting_his_words() -> None:
    ok = AnnotateDrawingRequest(operation_id="a", action="approve", sheet="Drawing", by="Ana", text="Ana: apruébalo")
    assert (ok.by, ok.text) == ("Ana", "Ana: apruébalo")
    with pytest.raises(ValidationError, match="approve needs by"):
        AnnotateDrawingRequest(operation_id="a", action="approve", by="Ana")
    with pytest.raises(ValidationError, match="approve takes only"):
        AnnotateDrawingRequest(operation_id="a", action="approve", by="Ana", text="t", id="q.views")


def test_the_other_actions_take_none_of_those_fields() -> None:
    for fields in ({"id": "x"}, {"verdict": "applies"}, {"text": "t"}, {"by": "Ana"}, {"memory": True}):
        with pytest.raises(ValidationError, match="takes no id, verdict, text, by or memory"):
            AnnotateDrawingRequest(operation_id="a", action="update", **fields)


def test_a_finding_carries_its_id_whether_it_blocks_and_its_answer() -> None:
    plain = DrawingFinding(code="hole_no_callout", severity="warning", message="m")
    assert (plain.id, plain.blocking, plain.answer) == ("", True, None)  # a CAD without ids is read as before
    answered = DrawingFinding(
        code="notes_edges",
        severity="info",
        message="m",
        id="notes_edges:edges",
        answer=DrawingAnswer(verdict="not_applies", text="no aplica: sin aristas vivas"),
    )
    assert answered.answer is not None and answered.answer.verdict == "not_applies"
    assert DrawingFinding(code="window_differs", severity="warning", message="m", blocking=False).blocking is False


def test_a_sheet_describes_its_questions_answers_and_approval() -> None:
    sheet = DrawingSheetInfo(
        sheet="Drawing",
        template="A3",
        language="es",
        page_mm=(420.0, 297.0),
        scale=1.0,
        questions=(DrawingQuestion(id="q.views", text="¿Bastan las vistas?"),),
        answers={"q.views": DrawingAnswer(verdict="applies", text="applies: bastan")},
        approved_by="Ana",
    )
    again = DescribeDrawingResult.model_validate(DescribeDrawingResult(sheets=(sheet,)).model_dump())
    assert again.sheets[0].questions[0].answer is None and again.sheets[0].answers["q.views"].verdict == "applies"
    assert again.sheets[0].approved_by == "Ana"
    bare = DrawingSheetInfo(sheet="D", template="A4", language="es", page_mm=(297.0, 210.0), scale=1.0)
    assert (bare.questions, bare.answers, bare.approved_by) == ((), {}, "")  # an older worker's answer still reads


# --- the reviews: request, choice and report ---------------------------------------------------------


def test_a_report_names_who_wrote_it_when_and_its_remarks_which_may_be_none() -> None:
    empty = AnnotateDrawingRequest(operation_id="a", action="review_report", sheet="Drawing", by="fabricator", at=10.0)
    assert (empty.by, empty.at, empty.remarks) == ("fabricator", 10.0, ())
    full = AnnotateDrawingRequest(
        operation_id="a",
        action="review_report",
        by="engineer",
        at=1.0,
        remarks=(
            {"kind": "missing", "where": "rótulo", "text": "Falta el material", "fix": "Declararlo"},
            {"text": "x"},
        ),
    )
    assert len(full.remarks) == 2
    for fields, match in (
        ({"at": 1.0}, "review_report needs by"),
        ({"by": "vecino", "at": 1.0}, "review_report needs by"),
        ({"by": "fabricator"}, "review_report needs by"),
        ({"by": "fabricator", "at": 1.0, "text": "t"}, "takes only sheet, by, remarks and at"),
        ({"by": "fabricator", "at": 1.0, "remarks": ({"kind": "huge", "text": "x"},)}, "kind must be one of"),
        ({"by": "fabricator", "at": 1.0, "remarks": ({"kind": "missing"},)}, "text is required"),
        ({"by": "fabricator", "at": 1.0, "remarks": ({"text": "x", "prio": 1},)}, "unknown field"),
        ({"by": "fabricator", "at": 1.0, "remarks": ({"text": "x" * 401},)}, "at most 400"),
        ({"by": "fabricator", "at": 1.0, "remarks": tuple({"text": "x"} for _ in range(31))}, "at most 30 remarks"),
    ):
        with pytest.raises(ValidationError, match=match):
            AnnotateDrawingRequest(operation_id="a", action="review_report", **fields)


def test_a_request_and_a_choice_take_only_what_they_need() -> None:
    assert AnnotateDrawingRequest(operation_id="a", action="request_review", at=5.0).at == 5.0
    with pytest.raises(ValidationError, match="request_review needs at"):
        AnnotateDrawingRequest(operation_id="a", action="request_review")
    with pytest.raises(ValidationError, match="request_review takes only"):
        AnnotateDrawingRequest(operation_id="a", action="request_review", at=5.0, by="Ana")
    assert AnnotateDrawingRequest(operation_id="a", action="review_choice", by="engineer").by == "engineer"
    with pytest.raises(ValidationError, match="review_choice needs by"):
        AnnotateDrawingRequest(operation_id="a", action="review_choice", by="Ana")
    with pytest.raises(ValidationError, match="takes no at"):
        AnnotateDrawingRequest(operation_id="a", action="update", at=5.0)
    with pytest.raises(ValidationError, match="takes no remarks"):
        AnnotateDrawingRequest(operation_id="a", action="update", remarks=({"text": "x"},))


def test_the_sheet_info_carries_the_reports_the_request_and_the_choice_through_json() -> None:
    from cotaforge.contract import DrawingRemark, DrawingReport, DrawingReviewRequest

    info = DrawingSheetInfo(
        sheet="Drawing",
        template="A4",
        language="es",
        page_mm=(297.0, 210.0),
        scale=0.5,
        reports={
            "fabricator": DrawingReport(
                n=1,
                by="fabricator",
                at=3.0,
                remarks=(DrawingRemark(id="fab1.1", kind="missing", text="Falta"),),
                edit=2,
            )
        },
        review_request=DrawingReviewRequest(at=2.0),
        review_choice="fabricator",
        printed=("Espesor 19",),
        edits=5,
    )
    again = DescribeDrawingResult.model_validate_json(DescribeDrawingResult(sheets=(info,)).model_dump_json()).sheets[0]
    assert again.reports["fabricator"].remarks[0].id == "fab1.1" and again.reports["fabricator"].edit == 2
    assert (again.review_request.at if again.review_request else None, again.review_choice) == (2.0, "fabricator")
    plain = DrawingSheetInfo(sheet="D", template="A4", language="es", page_mm=(1.0, 1.0), scale=1.0)
    assert (plain.reports, plain.review_request, plain.review_choice, plain.printed, plain.edits) == (
        {},
        None,
        "",
        (),
        0,
    )
