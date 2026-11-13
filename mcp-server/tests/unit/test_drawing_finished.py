# ruff: noqa: E501
"""A finished drawing through the tools (fake CAD that keeps the sheets): what the review asks in ``propio`` mode, the gate on
export, the answers (with memory's read-first rule), and the engineer's approval of the title block. The other modes of
``drawing_review`` (who reviews) are in ``test_drawing_review.py``."""

from cotaforge.contract import DrawingFinding, DrawingQuestion
from cotaforge.memory import MemoryStore, Scope, base_memory_dir

from cotaforge_mcp import design_memory
from cotaforge_mcp.config import Settings
from cotaforge_mcp.server import make_session
from tests.conftest import Caller, DrawingFake, ExportFake, declare, plate, set_review

NOT_LOCATED = DrawingFinding(
    code="hole_not_located",
    severity="warning",
    message="Agujero Ø6 [20, 20, 8] sin posición",
    id="hole_not_located:@20,20,8",
    features=("feature:3",),
)
NOTE = DrawingFinding(code="notes_edges", severity="info", message="Falta la nota de aristas", id="notes_edges:edges")
WINDOW = DrawingFinding(
    code="window_differs",
    severity="warning",
    message="La ventana no muestra lo mismo",
    id="window_differs",
    blocking=False,
)
CALLOUTS = DrawingQuestion(id="q.callouts", text="¿Las llamadas dicen cantidad, Ø y profundidad?")
LINES = DrawingQuestion(id="q.line_weights", text="¿Los grosores siguen ISO 128?")

PROJECT_PRINCIPLE = {
    "id": "plano-del-taller",
    "type": "principle",
    "title": "El taller lee el plano sin preguntar",
    "applies_when": "Antes de exportar una hoja",
    "not_when": "Una hoja que no se fabrica",
    "recognize": "Una hoja que remite a otro documento para lo esencial",
    "typical_error": "Remitir a otro documento",
    "when": {"ops": ["drawing_export"], "stages": ["detail"]},
    "examples": [
        {"sector": "automotriz", "text": "Un soporte.", "validated": False},
        {"sector": "fixtures", "text": "Una placa.", "validated": True},
        {"sector": "agricola", "text": "Un implemento.", "validated": False},
        {"sector": "mobiliario", "text": "Un herraje.", "validated": False},
    ],
}


def drawing_session(
    settings: Settings, base: bool = False, adapter: DrawingFake | None = None, mode: str = "own"
) -> tuple[Caller, DrawingFake]:
    set_review(settings, mode)
    fake = adapter or DrawingFake()
    on = Settings(adapter="fake", workspace=settings.workspace, home=settings.home, base_memory=base)
    call = Caller(make_session(on, fake))
    plate(call)
    call.ok("drawing", action="create")
    return call, fake


def drawing_entries(settings: Settings) -> list[str]:
    on = Settings(adapter="fake", workspace=settings.workspace, home=settings.home, base_memory=True)
    call = Caller(make_session(on, DrawingFake()))
    return [e.id for e in design_memory.drawing_entries(call.session)]


# --- the review ------------------------------------------------------------------------------------------------------------


def test_the_review_lists_findings_with_ids_the_questions_and_what_the_export_waits_for(settings: Settings) -> None:
    call, fake = drawing_session(settings)
    fake.findings, fake.questions = (NOT_LOCATED, NOTE), (CALLOUTS, LINES)
    text = call.ok("drawing", action="review")
    assert "review: 1 warning(s), 1 to confirm" in text
    assert "BLOCKS EXPORT hole_not_located:@20,20,8 [feature:3]: Agujero Ø6 [20, 20, 8] sin posición" in text
    assert "q.callouts: ¿Las llamadas dicen cantidad, Ø y profundidad?" in text and "q.line_weights" in text
    assert (
        "export of this sheet is REFUSED: 4 pending (2 findings, 2 questions, 0 memory entries, 0 reviewer remarks"
        in text
    )
    assert "drawing review mode: own" in text
    assert 'not approved ("Aprobó" is empty)' in text


def test_the_review_of_a_clean_sheet_with_no_questions_opens_the_export(settings: Settings) -> None:
    call, _ = drawing_session(settings)
    text = call.ok("drawing", action="review")
    assert "nothing found" in text and "export of this sheet is open" in text


def test_the_memory_entries_an_export_calls_for_are_in_the_review(settings: Settings) -> None:
    declare(settings, PROJECT_PRINCIPLE)
    call, _ = drawing_session(settings, base=True)
    text = call.ok("drawing", action="review")
    assert "memory entries an export calls for" in text
    assert "plano-del-taller — recognize: Una hoja que remite" in text and "[NOT read: memory get first]" in text
    assert "drawing-manufacturing-data-in-title-block" in text  # the base set is called for too


def test_only_the_entries_that_name_an_op_of_the_drawing_are_called_for(settings: Settings) -> None:
    called = drawing_entries(settings)
    assert len(called) == 9 and all(i.startswith("drawing-") for i in called)  # the base drawing principles
    assert "drawing-manufacturing-data-in-title-block" in called and "drawing-sufficient-views" in called
    assert "design-target-mass" not in called  # a stage-only trigger of the design, not of the drawing


# --- the gate ------------------------------------------------------------------------------------------------------------------


def test_the_export_is_refused_while_anything_is_unanswered_and_lists_exactly_what(settings: Settings) -> None:
    declare(settings, PROJECT_PRINCIPLE)
    call, fake = drawing_session(settings, base=True)
    fake.findings, fake.questions = (NOT_LOCATED, NOTE, WINDOW), (CALLOUTS,)
    for action in ("pdf", "dxf"):
        text = call.err("export", action=action)
        assert "error: drawing_unfinished" in text and f"export {action} refused" in text
        assert "hole_not_located:@20,20,8: Agujero Ø6 [20, 20, 8] sin posición" in text
        assert "notes_edges:edges" in text and "q.callouts" in text
        assert "plano-del-taller" in text and "drawing-manufacturing-data-in-title-block" in text
        assert "window_differs" not in text  # a notice about the CAD window never holds an export
        assert "there is no draft PDF" in text
    assert fake.exported == [] and not (settings.workspace / "plate.pdf").exists()


def test_an_answer_closes_a_point_and_the_export_goes_through_when_all_are_closed(settings: Settings) -> None:
    declare(settings, PROJECT_PRINCIPLE)
    call, fake = drawing_session(settings, base=True)
    fake.findings, fake.questions = (NOT_LOCATED,), (CALLOUTS,)
    call.err("export", action="pdf")
    call.ok(
        "drawing", action="answer", id="hole_not_located:@20,20,8", text="corregido: ubicado con datum_dims en la vista top"
    )  # a hole with no position is a measurement: only a fix closes it (see test_drawing_requirements)
    call.ok("drawing", action="answer", id="q.callouts", text="applies: una placa con un solo tipo de agujero")
    pending = call.err("export", action="pdf")
    assert "- hole_not_located" not in pending and "- q.callouts" not in pending and "- plano-del-taller" in pending
    for entry in drawing_entries(settings):
        call.ok("memory", action="get", id=entry)
        call.ok("drawing", action="answer", id=entry, text="no aplica: la pieza se fabrica con este plano solo")
    assert "exported pdf" in call.ok("export", action="pdf")
    assert fake.exported[-1].format == "pdf" and (settings.workspace / "plate.pdf").is_file()
    assert "exported dxf" in call.ok("export", action="dxf", path="plate.dxf")


def test_a_fixed_finding_is_gone_and_needs_no_answer(settings: Settings) -> None:
    call, fake = drawing_session(settings)
    fake.findings = (NOT_LOCATED,)
    call.err("export", action="pdf")
    fake.findings = ()  # the dimension was added: the next review does not find it
    assert "exported pdf" in call.ok("export", action="pdf")


def test_the_dxf_of_a_face_cut_is_not_a_drawing_and_is_not_gated(settings: Settings) -> None:
    class Both(ExportFake, DrawingFake):
        pass

    call, fake = drawing_session(settings, adapter=Both())
    fake.findings = (NOT_LOCATED,)
    call.err("export", action="pdf")
    assert "exported dxf" in call.ok("export", action="dxf", face="face:1", path="cut.dxf")


# --- answering ---------------------------------------------------------------------------------------------------------------------


def test_the_answer_to_a_memory_entry_also_closes_it_at_the_milestone(settings: Settings) -> None:
    declare(settings, PROJECT_PRINCIPLE)
    call, _ = drawing_session(settings, base=True)
    call.session.mem.ops_seen.add("drawing_export")  # what an export does: the entry is triggered at the milestone
    _, before = design_memory.review(call.session, design_memory.Stage.detail)
    assert any("plano-del-taller" in line for line in before)
    call.ok("memory", action="get", id="plano-del-taller")
    call.ok("drawing", action="answer", id="plano-del-taller", text="applies: el plano dice todo lo esencial")
    _, after = design_memory.review(call.session, design_memory.Stage.detail)
    assert not any("plano-del-taller" in line for line in after)  # asked once, answered once


def test_a_memory_entry_cannot_be_answered_before_it_is_read(settings: Settings) -> None:
    declare(settings, PROJECT_PRINCIPLE)
    call, fake = drawing_session(settings, base=True)
    text = call.err("drawing", action="answer", id="plano-del-taller", text="applies: el plano dice todo lo esencial")
    assert "error: not_read" in text and "memory(action=get" in text
    assert fake.annotated == []  # nothing reached the CAD
    call.ok("memory", action="get", id="plano-del-taller")
    assert "answered: plano-del-taller" in call.ok(
        "drawing", action="answer", id="plano-del-taller", text="applies: el plano dice todo lo esencial"
    )
    request = fake.annotated[-1]
    assert (request.action, request.id, request.verdict, request.memory) == (
        "answer",
        "plano-del-taller",
        "applies",
        True,
    )
    assert "plano-del-taller: APPLIES: el plano dice todo lo esencial" in call.ok("drawing", action="review")


def test_an_answer_says_how_it_applies_or_why_it_does_not(settings: Settings) -> None:
    call, fake = drawing_session(settings)
    fake.questions = (CALLOUTS,)
    assert "bad_answer" in call.err("drawing", action="answer", id="q.callouts", text="si")
    assert "bad_answer" in call.err("drawing", action="answer", id="q.callouts", text="no aplica: no")
    assert "bad_answer" in call.err(
        "drawing", action="answer", id="q.callouts", text="las llamadas de la placa estan completas"
    )
    assert "answered: q.callouts (applies)" in call.ok(
        "drawing", action="answer", id="q.callouts", text="aplica: bastan la frontal y la superior"
    )
    assert "(does not apply)" in call.ok(
        "drawing", action="answer", id="q.callouts", text="does not apply: a plate has one hole type"
    )


def test_an_id_the_review_does_not_have_is_refused_by_the_cad(settings: Settings) -> None:
    call, fake = drawing_session(settings)
    fake.questions = (CALLOUTS,)
    refused = call.err("drawing", action="answer", id="q.nope", text="no aplica: esta pregunta no existe aquí")
    assert "no finding or question 'q.nope'" in refused
    assert "id" in call.err("drawing", action="answer", text="no aplica: esta pregunta no existe aquí")


def test_an_active_entry_the_export_does_not_call_for_is_not_a_question_here(settings: Settings) -> None:
    call, _ = drawing_session(settings, base=True)
    text = call.err("drawing", action="answer", id="design-target-mass", text="applies: la masa objetivo es esta")
    assert "does not call for here" in text


def test_the_answers_are_kept_in_the_sheet_and_make_the_document_dirty(settings: Settings) -> None:
    call, fake = drawing_session(settings)
    fake.questions = (CALLOUTS,)
    call.session.dirty = False
    call.ok("drawing", action="answer", id="q.callouts", text="applies: una sola llamada para los cuatro agujeros")
    assert call.session.dirty is True
    stored = fake.drawings["Drawing"]["answers"]["q.callouts"]
    assert (stored.verdict, stored.text) == ("applies", "applies: una sola llamada para los cuatro agujeros")


# --- "Aprobó" ----------------------------------------------------------------------------------------------------------------------


def test_approve_is_the_engineers_gate_and_fills_the_title_block(settings: Settings) -> None:
    call, fake = drawing_session(settings)
    for arguments in ({}, {"by": "Ana"}, {"reason": "Ana: apruébalo"}, {"by": " ", "reason": "x"}):
        assert "engineer's gate" in call.err("drawing", action="approve", sheet="Drawing", **arguments)
    assert "names the sheet" in call.err("drawing", action="approve", by="Ana", reason="Ana: apruébalo")
    assert [a.action for a in fake.annotated if a.action == "approve"] == []
    text = call.ok("drawing", action="approve", sheet="Drawing", by="Ana", reason="Ana: apruébalo")
    assert "approved: sheet Drawing by Ana (Ana: apruébalo)" in text and "empties it again" in text
    request = fake.annotated[-1]
    assert (request.action, request.by, request.text) == ("approve", "Ana", "Ana: apruébalo")
    assert "approved by: Ana" in call.ok("drawing", action="review")


def test_any_change_of_the_sheet_empties_the_approval(settings: Settings) -> None:
    call, _ = drawing_session(settings)
    call.ok("drawing", action="approve", sheet="Drawing", by="Ana", reason="Ana: apruébalo")
    call.ok("drawing", action="add", items=[{"type": "note", "text": "Sin rebabas"}])
    assert 'not approved ("Aprobó" is empty)' in call.ok("drawing", action="review")


def test_the_agent_has_no_other_way_to_write_the_approver(settings: Settings) -> None:
    from cotaforge_mcp.schemas import TOOLS_BY_NAME

    fields = set(TOOLS_BY_NAME["drawing"].model.model_fields)
    assert not {"approval_person", "approved_by", "approver"} & fields
    call, _ = drawing_session(settings)
    assert "approval_person" in call.err("drawing", action="create", approval_person="Eve")  # an unknown argument


# --- the guide and the base set -------------------------------------------------------------------------------------------------------


def test_the_guide_topic_explains_the_gate_and_stays_whole(settings: Settings) -> None:
    from cotaforge_mcp.guide import guide

    call, _ = drawing_session(settings)
    text = guide(call.session, "drawing:done")
    for needle in (
        "REFUSED",
        "no draft PDF",
        "drawing(action=answer",
        "no aplica",
        "approve",
        "hole_not_located",
        "q.callouts",
        "drawing_review",
        "engineer_or_fabricator",
        "review_choice",
        "review_report",
        "run_in_background=true",
        "One drawing = one sheet = one file",
        "cannot prove WHO",
    ):
        assert needle in text, needle
    assert 200 < len(text) < 6000
    assert "drawing:done" in guide(call.session, "drawing")


def test_the_base_set_has_nine_drawing_principles() -> None:
    entries = [i.entry for i in MemoryStore(base_dir=base_memory_dir()).entries() if i.scope is Scope.base]
    named = [e.id for e in entries if e.when is not None and any(op.startswith("drawing_") for op in e.when.ops)]
    assert len(named) == 9 and all(i.startswith("drawing-") for i in named)
