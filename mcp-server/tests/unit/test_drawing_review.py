# ruff: noqa: E501
"""Who reviews a drawing before it is exported (``drawing_review`` of the brief): the setting, the reports of the
manufacturer and the engineer, the engineer's choice with its wait (the clock is injected), and the gate in every mode."""

from pathlib import Path

import pytest
import yaml
from cotaforge.contract import DrawingFinding

from cotaforge_mcp import design_memory, drawing_gate
from cotaforge_mcp.config import Settings
from cotaforge_mcp.server import make_session
from tests.conftest import Caller, DrawingFake, plate, set_review

NOT_LOCATED = DrawingFinding(
    code="hole_not_located",
    severity="warning",
    message="Agujero Ø6 [20, 20, 8] sin posición",
    id="hole_not_located:@20,20,8",
    features=("feature:3",),
)
REMARK = {
    "kind": "missing",
    "where": "vista superior, agujero Ø8",
    "text": "No dice la profundidad del avellanado",
    "fix": "Añadir ⌵ y profundidad a la llamada",
}
REMARK_2 = {"kind": "ambiguous", "where": "nota 2", "text": "«Rebabas» sin dimensión de rebaba máxima"}
WHY = "no aplica: la rebaba máxima la fija la tolerancia general de la hoja"


class Clock:
    """The server's clock, set by the test."""

    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now

    def minutes(self, minutes: float) -> None:
        self.now += minutes * 60


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    fake = Clock()
    monkeypatch.setattr(drawing_gate, "clock", fake)
    return fake


def session_in(settings: Settings, mode: str, wait: int | None = None) -> tuple[Caller, DrawingFake]:
    set_review(settings, mode, wait)
    fake = DrawingFake()
    call = Caller(
        make_session(
            Settings(adapter="fake", workspace=settings.workspace, home=settings.home, base_memory=False), fake
        )
    )
    plate(call)
    call.ok("drawing", action="create")
    return call, fake


def report(call: Caller, by: str, *items: dict[str, str]) -> str:
    return call.ok("drawing", action="review_report", sheet="Drawing", by=by, items=list(items))


# --- the setting ----------------------------------------------------------------------------------------------------------


def test_the_default_is_the_engineer_or_the_manufacturer_with_fifteen_minutes(settings: Settings) -> None:
    brief = design_memory.load_brief(settings)
    assert (brief.drawing_review, brief.drawing_review_wait_min) == ("engineer_or_fabricator", 15)
    assert not (settings.workspace / "brief.yaml").exists()  # the default is not written


def test_the_setting_is_kept_in_the_brief_next_to_the_sector_and_needs_the_engineers_words(settings: Settings) -> None:
    call = Caller(make_session(settings, DrawingFake()))
    call.ok("memory", action="brief", sector="chapa y soldadura")
    refused = call.err("memory", action="brief", drawing_review="own")
    assert "engineer's decision" in refused and "text" in refused
    assert "drawing review own" in call.ok(
        "memory", action="brief", drawing_review="own", text="Ana: revisa tú el plano"
    )
    data = yaml.safe_load((settings.workspace / "brief.yaml").read_text(encoding="utf-8"))
    assert data["drawing_review"] == "own" and data["drawing_review_said"] == "Ana: revisa tú el plano"
    assert data["sector"] == "chapa y soldadura"  # a new setting does not lose the old ones
    call.ok("memory", action="brief", sector="mecanizado")
    assert design_memory.load_brief(settings).drawing_review == "own"  # nor does a later sector


def test_the_wait_is_set_in_minutes_with_the_mode_or_alone(settings: Settings) -> None:
    call = Caller(make_session(settings, DrawingFake()))
    text = call.ok("memory", action="brief", drawing_review="engineer_or_fabricator:30")
    assert "within 30 min" in text and design_memory.load_brief(settings).drawing_review_wait_min == 30
    call.ok("memory", action="brief", drawing_review="0")
    brief = design_memory.load_brief(settings)
    assert (brief.drawing_review, brief.drawing_review_wait_min) == ("engineer_or_fabricator", 0)
    assert "not one of" in call.err("memory", action="brief", drawing_review="amigo", text="x")
    assert "minutes" in call.err("memory", action="brief", drawing_review="fabricator:pronto", text="x")
    call.ok("memory", action="brief", drawing_review="engineer_or_fabricator")  # back to the default: no words needed
    assert design_memory.load_brief(settings).drawing_review_said == ""


def test_an_unknown_value_in_the_file_reads_as_the_default(settings: Settings) -> None:
    (settings.workspace / "brief.yaml").write_text(
        "drawing_review: mañana\ndrawing_review_wait_min: -3\n", encoding="utf-8"
    )
    brief = design_memory.load_brief(settings)
    assert (brief.drawing_review, brief.drawing_review_wait_min) == ("engineer_or_fabricator", 15)


# --- ninguno and the findings that block in every other mode ----------------------------------------------------------------


def test_ninguno_asks_nothing_and_exports(settings: Settings) -> None:
    call, fake = session_in(settings, "none")
    fake.findings = (NOT_LOCATED,)
    assert "exported pdf" in call.ok("export", action="pdf")
    assert "export of this sheet is open" in call.ok("drawing", action="review")


@pytest.mark.parametrize("mode", ["engineer_or_fabricator", "fabricator", "engineer", "own"])
def test_a_blocking_finding_holds_the_export_in_every_mode_but_none(settings: Settings, mode: str) -> None:
    call, fake = session_in(settings, mode)
    fake.findings = (NOT_LOCATED,)
    text = call.err("export", action="pdf")
    assert "hole_not_located:@20,20,8" in text and f"drawing review mode: {mode}" in text


# --- fabricator -----------------------------------------------------------------------------------------------------------------


def test_fabricante_the_export_asks_to_launch_the_agent_and_a_report_with_answers_opens_it(settings: Settings) -> None:
    call, fake = session_in(settings, "fabricator")
    refused = call.err("export", action="pdf")
    assert "error: drawing_unfinished" in refused and "no fabricator report yet" in refused
    assert 'subagent_type="fabricator"' in refused and 'by="fabricator"' in refused and '"Drawing"' in refused
    text = report(call, "fabricator", REMARK, REMARK_2)
    assert "fab1.1, fab1.2" in text and "2 remark(s)" in text and "no aplica" in text
    after = call.err("export", action="pdf")
    assert "fab1.1 (fabricator, missing) [vista superior, agujero Ø8]: No dice la profundidad" in after
    assert "-> smallest fix: Añadir ⌵ y profundidad" in after and "fab1.2" in after
    call.ok(
        "drawing", action="answer", id="fab1.1", text="applies: añadí el avellanado con su profundidad en la llamada"
    )
    assert "fab1.1" not in call.err("export", action="pdf").split("remarks of the reviewer")[1]
    call.ok("drawing", action="answer", id="fab1.2", text=WHY)
    assert "exported pdf" in call.ok("export", action="pdf")
    assert fake.exported[-1].sheet == "Drawing"


def test_a_report_with_no_remarks_is_a_report_and_opens_the_export(settings: Settings) -> None:
    call, _ = session_in(settings, "fabricator")
    assert "no remarks: nothing to answer" in report(call, "fabricator")
    assert "exported pdf" in call.ok("export", action="pdf")


def test_a_report_stays_valid_through_the_fixes_it_asked_for_and_the_edits_are_shown(settings: Settings) -> None:
    call, _ = session_in(settings, "fabricator")
    report(call, "fabricator", REMARK)
    assert "reviewed at edit 0, sheet now at edit 0" in call.err("export", action="pdf")
    call.ok("drawing", action="add", items=[{"type": "note", "text": "Avellanado ⌵ 90° prof. 3"}])  # the fix
    pending = call.err("export", action="pdf")
    assert "reviewed at edit 0, sheet now at edit 1" in pending and "fab1.1" in pending
    assert "the sheet changed 1 time(s) after the review" in pending  # shown, it gates nothing
    call.ok(
        "drawing", action="answer", id="fab1.1", text="corregido: añadí el avellanado con su profundidad en una nota"
    )
    assert "exported pdf" in call.ok("export", action="pdf")  # no new review is asked
    assert "(reviewed at edit 0, sheet now at edit 1)" in call.ok("drawing", action="review")


def test_corregido_is_an_answer_that_says_what_was_changed(settings: Settings) -> None:
    call, fake = session_in(settings, "fabricator")
    report(call, "fabricator", REMARK)
    assert "bad_answer" in call.err("drawing", action="answer", id="fab1.1", text="corregido: ok")
    call.ok("drawing", action="answer", id="fab1.1", text="corregido: añadí el avellanado en la llamada")
    assert fake.annotated[-1].verdict == "applies"
    assert "APPLIES: añadí el avellanado" in call.ok("drawing", action="review")


def test_a_new_report_of_the_same_reviewer_supersedes_the_old_one(settings: Settings) -> None:
    call, _ = session_in(settings, "fabricator")
    report(call, "fabricator", REMARK)
    call.ok("drawing", action="add", items=[{"type": "note", "text": "Cambio mayor de la hoja"}])
    report(call, "fabricator", REMARK_2)  # the agent decided the sheet changed beyond the remarks: it asks again
    text = call.err("export", action="pdf")
    assert "fab2.1" in text and "fab1.1" not in text.split("remarks of the reviewer")[1]
    call.ok("drawing", action="answer", id="fab2.1", text=WHY)
    assert "exported pdf" in call.ok("export", action="pdf")


def test_answering_does_not_change_the_sheet_so_the_report_stays_current(settings: Settings) -> None:
    call, _ = session_in(settings, "fabricator")
    report(call, "fabricator", REMARK)
    call.ok("drawing", action="answer", id="fab1.1", text=WHY)
    assert "exported pdf" in call.ok("export", action="pdf")


def test_only_the_manufacturers_report_opens_fabricante_and_only_the_engineers_opens_ingeniero(
    settings: Settings,
) -> None:
    call, _ = session_in(settings, "fabricator")
    note = report(call, "engineer")
    assert "only the fabricator report opens the export" in note
    assert "no fabricator report" in call.err("export", action="pdf")
    call, _ = session_in(settings, "engineer")
    assert "only the engineer report opens the export" in report(call, "fabricator")
    assert "no engineer report" in call.err("export", action="pdf")


def test_an_answer_is_needed_to_close_a_remark_and_it_must_say_why(settings: Settings) -> None:
    call, _ = session_in(settings, "fabricator")
    report(call, "fabricator", REMARK)
    assert "bad_answer" in call.err("drawing", action="answer", id="fab1.1", text="ok")
    assert "no finding or question 'fab9.9'" in call.err("drawing", action="answer", id="fab9.9", text=WHY)


def test_a_report_is_checked_before_the_cad_hears_of_it(settings: Settings) -> None:
    call, fake = session_in(settings, "fabricator")
    count = len(fake.annotated)
    for items, needle in (
        ([{"kind": "huge", "text": "x"}], "kind must be one of"),
        ([{"kind": "missing"}], "text is required"),
        ([{"text": "x", "prio": 1}], "unknown field(s) prio"),
        (["libre"], "valid dictionary"),
        ([{"text": "x" * 500}], "at most 400"),
    ):
        text = call.err("drawing", action="review_report", sheet="Drawing", by="fabricator", items=items)
        assert needle in text and "usage:" in text, (items, text)
    assert "by" in call.err("drawing", action="review_report", sheet="Drawing", items=[])
    assert "by" in call.err("drawing", action="review_report", sheet="Drawing", by="vecino", items=[])
    assert len(fake.annotated) == count


# --- engineer ------------------------------------------------------------------------------------------------------------------


def test_ingeniero_waits_for_his_report_without_a_time_limit(settings: Settings, clock: Clock) -> None:
    call, _ = session_in(settings, "engineer")
    text = call.ok("drawing", action="request_review", sheet="Drawing")
    assert "ask the engineer to review sheet Drawing" in text and "No time limit" in text
    clock.minutes(24 * 60)
    refused = call.err("export", action="pdf")
    assert "no engineer report yet" in refused and 'by="engineer"' in refused
    assert "view(action=selection)" in refused
    assert "no remarks" in report(call, "engineer")
    assert "exported pdf" in call.ok("export", action="pdf")


# --- engineer_or_fabricator: the engineer chooses, or the manufacturer is assumed ------------------------------------------------------


def test_the_first_export_asks_for_the_request_and_the_request_says_what_to_ask_and_starts_the_timer(
    settings: Settings, clock: Clock
) -> None:
    call, fake = session_in(settings, "engineer_or_fabricator")
    refused = call.err("export", action="pdf")
    assert "nobody has been asked who reviews sheet Drawing" in refused
    assert 'drawing(action="request_review", sheet="Drawing")' in refused
    text = call.ok("drawing", action="request_review")
    assert "¿Revisas tú la hoja Drawing o la revisa el agente fabricante?" in text
    assert "within 15 min the manufacturer review counts" in text and "NEVER end the turn" in text
    assert "`sleep 900`" in text and "run_in_background=true" in text and "Start-Sleep -Seconds 900" in text
    assert "if the engineer has already answered the choice, ignore the timer" in text
    assert [a.action for a in fake.annotated][-1] == "request_review" and fake.annotated[-1].at == clock.now


def test_while_the_engineer_has_not_answered_the_refusal_says_how_many_minutes_are_left(
    settings: Settings, clock: Clock
) -> None:
    call, _ = session_in(settings, "engineer_or_fabricator", wait=20)
    call.ok("drawing", action="request_review")
    clock.minutes(5)
    refused = call.err("export", action="pdf")
    assert "waiting for the engineer's choice" in refused and "15 min left of 20" in refused
    assert "¿Revisas tú la hoja Drawing o la revisa el agente fabricante?" in refused
    assert 'drawing(action="review_choice"' in refused and "never end the turn to wait" in refused
    clock.minutes(14.5)
    assert "1 min left of 20" in call.err("export", action="pdf")  # rounded up: it is never 0 min while waiting
    review = call.ok("drawing", action="review")
    assert "state: waiting for the engineer's choice" in review and "REFUSED" in review


def test_asking_again_does_not_extend_the_wait(settings: Settings, clock: Clock) -> None:
    call, fake = session_in(settings, "engineer_or_fabricator")
    call.ok("drawing", action="request_review")
    clock.minutes(10)
    again = call.ok("drawing", action="request_review")
    assert "already requested: 5 min left of 15" in again
    assert [a.action for a in fake.annotated].count("request_review") == 1


def test_no_answer_in_time_assumes_the_manufacturer_and_the_refusal_says_so(settings: Settings, clock: Clock) -> None:
    call, _ = session_in(settings, "engineer_or_fabricator")
    call.ok("drawing", action="request_review")
    clock.minutes(15)
    refused = call.err("export", action="pdf")
    assert "the engineer did not choose within 15 min: the manufacturer review counts" in refused
    assert 'subagent_type="fabricator"' in refused
    report(call, "fabricator", REMARK)
    after = call.err("export", action="pdf")
    assert "fab1.1" in after and "manufacturer review counts" in after
    call.ok("drawing", action="answer", id="fab1.1", text=WHY)
    assert "exported pdf" in call.ok("export", action="pdf")


def test_with_a_wait_of_zero_the_manufacturer_counts_at_once(settings: Settings, clock: Clock) -> None:
    call, _ = session_in(settings, "engineer_or_fabricator", wait=0)
    call.ok("drawing", action="request_review")
    assert "manufacturer review counts" in call.err("export", action="pdf")


def test_the_engineer_chose_himself_so_the_gate_waits_for_his_report_with_no_time_limit(
    settings: Settings, clock: Clock
) -> None:
    call, _ = session_in(settings, "engineer_or_fabricator", wait=5)
    call.ok("drawing", action="request_review")
    text = call.ok("drawing", action="review_choice", sheet="Drawing", by="engineer")
    assert "reviews sheet Drawing himself" in text and "no time limit" in text
    clock.minutes(600)  # the timer would have gone off long ago: it means nothing now
    refused = call.err("export", action="pdf")
    assert "the engineer chose to review the sheet himself (no time limit): no engineer report" in refused
    assert 'by="engineer"' in refused
    report(call, "engineer", {"text": "Falta la nota de material", "kind": "missing", "where": "rótulo"})
    assert "eng1.1" in call.err("export", action="pdf")
    call.ok("drawing", action="answer", id="eng1.1", text="applies: añadí la nota de material en el bloque de notas")
    assert "exported pdf" in call.ok("export", action="pdf")


def test_the_engineer_chose_the_manufacturer_and_the_reply_says_to_launch_it(settings: Settings, clock: Clock) -> None:
    call, _ = session_in(settings, "engineer_or_fabricator")
    call.ok("drawing", action="request_review")
    text = call.ok("drawing", action="review_choice", sheet="Drawing", by="fabricator")
    assert 'subagent_type="fabricator"' in text
    assert "the engineer chose the manufacturer: no fabricator report" in call.err("export", action="pdf")
    report(call, "fabricator")
    assert "exported pdf" in call.ok("export", action="pdf")


def test_the_engineers_report_counts_over_the_manufacturers(settings: Settings, clock: Clock) -> None:
    call, _ = session_in(settings, "engineer_or_fabricator", wait=0)
    call.ok("drawing", action="request_review")
    report(call, "fabricator", REMARK)  # the assumed manufacturer found something
    assert "fab1.1" in call.err("export", action="pdf")
    report(call, "engineer")  # the engineer looks later and finds nothing: his report is the one that counts
    review = call.ok("drawing", action="review")
    assert "the engineer's report counts: engineer report 2" in review
    assert "exported pdf" in call.ok("export", action="pdf")  # the manufacturer's unanswered remark does not block
    report(call, "engineer", {"text": "El espesor no está en ninguna nota"})
    assert "eng3.1" in call.err("export", action="pdf")  # a later report with remarks is a new review: answers needed


def test_the_choice_of_reviewer_belongs_to_the_sheet_and_a_change_neither_asks_again_nor_restarts_the_wait(
    settings: Settings, clock: Clock
) -> None:
    call, fake = session_in(settings, "engineer_or_fabricator", wait=5)
    call.ok("drawing", action="request_review")
    call.ok("drawing", action="review_choice", sheet="Drawing", by="fabricator")
    report(call, "fabricator")
    call.ok("drawing", action="add", items=[{"type": "note", "text": "Sin rebabas"}])
    assert "exported pdf" in call.ok("export", action="pdf")  # still reviewed, still chosen
    asked = call.ok("drawing", action="request_review")
    assert "already reviewed by the manufacturer agent" in asked and "nothing is asked again" in asked
    assert [a.action for a in fake.annotated].count("request_review") == 1


def test_the_assumed_manufacturer_stays_assumed_through_the_changes(settings: Settings, clock: Clock) -> None:
    call, _ = session_in(settings, "engineer_or_fabricator", wait=5)
    call.ok("drawing", action="request_review")
    clock.minutes(6)
    assert "manufacturer review counts" in call.err("export", action="pdf")
    call.ok("drawing", action="add", items=[{"type": "note", "text": "Sin rebabas"}])
    again = call.err("export", action="pdf")
    assert "manufacturer review counts" in again and "waiting" not in again  # the wait did not restart
    assert "the engineer did not choose within 5 min, so the manufacturer review counts" in call.ok(
        "drawing", action="request_review"
    )


def test_asking_again_before_the_wait_is_over_does_not_ask_him_twice(settings: Settings, clock: Clock) -> None:
    call, _ = session_in(settings, "engineer_or_fabricator", wait=5)
    call.ok("drawing", action="request_review")
    call.ok("drawing", action="add", items=[{"type": "note", "text": "Sin rebabas"}])  # a change: same wait, same sheet
    clock.minutes(1)
    assert "4 min left of 5" in call.err("export", action="pdf")


def test_the_engineer_can_still_review_later_and_his_newer_report_counts(settings: Settings, clock: Clock) -> None:
    call, _ = session_in(settings, "engineer_or_fabricator", wait=0)
    call.ok("drawing", action="request_review")
    report(call, "fabricator")  # the assumed manufacturer found nothing
    assert "exported pdf" in call.ok("export", action="pdf")
    report(call, "engineer", {"text": "El espesor no está en ninguna nota"})  # he looks later
    refused = call.err("export", action="pdf", overwrite=True)
    assert "eng2.1" in refused and "the engineer's report counts" in refused
    call.ok("drawing", action="answer", id="eng2.1", text="corregido: añadí la nota de espesor")
    assert "exported pdf" in call.ok("export", action="pdf", overwrite=True)


def test_when_the_engineer_says_yo_after_the_manufacturer_was_assumed_his_report_is_needed(
    settings: Settings, clock: Clock
) -> None:
    call, _ = session_in(settings, "engineer_or_fabricator", wait=0)
    call.ok("drawing", action="request_review")
    report(call, "fabricator")
    call.ok("drawing", action="review_choice", sheet="Drawing", by="engineer")
    assert "no engineer report yet" in call.err("export", action="pdf")


def test_the_choice_exists_only_in_the_engineer_or_manufacturer_mode(settings: Settings) -> None:
    call, _ = session_in(settings, "fabricator")
    assert "exists only in drawing_review=engineer_or_fabricator" in call.err(
        "drawing", action="review_choice", sheet="Drawing", by="engineer"
    )
    call, _ = session_in(settings, "engineer_or_fabricator")
    assert "ENGINEER answered" in call.err("drawing", action="review_choice", sheet="Drawing", by="vecino")


# --- propio: the older behaviour, minus the questions the memory already asks --------------------------------------------------------------


def test_propio_does_not_wait_for_any_report(settings: Settings) -> None:
    call, _ = session_in(settings, "own")
    text = call.ok("drawing", action="request_review")
    assert "nobody outside is asked" in text
    assert "exported pdf" in call.ok("export", action="pdf")


def test_in_the_outside_modes_the_self_questions_and_memory_entries_are_not_asked_of_the_agent(
    settings: Settings,
) -> None:
    from tests.conftest import declare  # noqa: PLC0415

    declare(
        settings,
        {
            "id": "plano-del-taller",
            "type": "principle",
            "title": "El taller lee el plano sin preguntar",
            "applies_when": "Antes de exportar una hoja",
            "not_when": "Una hoja que no se fabrica",
            "recognize": "Una hoja que remite a otro documento",
            "typical_error": "Remitir a otro documento",
            "when": {"ops": ["drawing_export"], "stages": ["detail"]},
            "examples": [
                {"sector": s, "text": "Un soporte.", "validated": False}
                for s in ("automotriz", "fixtures", "agricola", "mobiliario")
            ],
        },
    )
    call, fake = session_in(settings, "fabricator")
    from cotaforge.contract import DrawingQuestion  # noqa: PLC0415

    fake.questions = (DrawingQuestion(id="q.callouts", text="¿Las llamadas?"),)
    report(call, "fabricator")
    review = call.ok("drawing", action="review")
    assert "q.callouts" not in review and "plano-del-taller" not in review
    assert "exported pdf" in call.ok("export", action="pdf")


# --- one sheet, one file; what the manufacturer reads --------------------------------------------------------------------------------------


def test_the_gate_looks_at_the_one_sheet_that_is_exported(settings: Settings) -> None:
    call, fake = session_in(settings, "fabricator")
    call.ok("drawing", action="create", sheet="Detalle")
    two = call.err("export", action="pdf")
    assert "2 sheets (Drawing, Detalle)" in two
    assert "sheet Detalle" in call.err("export", action="pdf", sheet="Detalle")
    call.ok("drawing", action="review_report", sheet="Detalle", by="fabricator", items=[])
    assert "exported pdf" in call.ok("export", action="pdf", sheet="Detalle", path="detalle.pdf")
    assert "sheet Drawing" in call.err("export", action="pdf", sheet="Drawing")  # the other sheet has no report
    assert fake.exported[-1].sheet == "Detalle"


def test_list_gives_the_manufacturer_the_texts_the_sheet_prints(settings: Settings) -> None:
    call, _ = session_in(settings, "fabricator")
    text = call.ok("drawing", action="list")
    assert "printed on the sheet" in text and "19 de espesor" in text


def test_read_gives_the_manufacturer_only_what_the_sheet_prints(settings: Settings) -> None:
    call, _ = session_in(settings, "fabricator")
    dim = {
        "type": "dim",
        "kind": "distance",
        "from": {"face": "face:3"},
        "to": {"face": "face:5"},
    }
    call.ok("drawing", action="add", items=[dim])
    listing = call.ok("drawing", action="list")
    assert "face:3" in listing  # the working agent keeps its detail
    text = call.ok("drawing", action="read")
    assert "19 de espesor" in text and "Fabrication notes" in text  # what the sheet prints
    for model_data in ("face:", "hole_dim", "body", "findings", "warning"):
        assert model_data not in text


def test_the_manufacturer_agent_is_told_to_read_not_list() -> None:
    agent = (Path(__file__).parents[3] / "plugin" / "agents" / "fabricator.md").read_text(encoding="utf-8")
    assert 'action="read"' in agent and "Never use `list` or `review`" in agent


def test_the_workspace_has_no_report_files(settings: Settings) -> None:
    call, _ = session_in(settings, "fabricator")
    report(call, "fabricator", REMARK)
    assert [p.name for p in Path(settings.workspace).iterdir() if "report" in p.name] == []


def test_after_the_engineer_chose_the_manufacturer_a_later_report_of_his_supersedes_it(
    settings: Settings, clock: Clock
) -> None:
    call, _ = session_in(settings, "engineer_or_fabricator")
    call.ok("drawing", action="review_choice", sheet="Drawing", by="fabricator")
    report(call, "fabricator", REMARK)
    report(call, "engineer")  # he looked at it afterwards and has nothing to add: his report is the newer one
    assert "the engineer reviewed later" in call.ok("drawing", action="review")
    assert "exported pdf" in call.ok("export", action="pdf")
    report(call, "fabricator", REMARK_2)  # the agent asked the manufacturer again: the newest one counts
    assert "fab3.1" in call.err("export", action="pdf", overwrite=True)
