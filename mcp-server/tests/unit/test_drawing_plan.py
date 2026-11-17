# ruff: noqa: E501
"""``drawing(action=plan)`` serves the work sequence of THIS sheet with the catalog rules of each step, and every drawing
reply ends with the step the sheet is at. The status is read from the page (the CAD's review), never kept aside."""

import logging

import pytest

from cotaforge_mcp import drawing_gate
from cotaforge_mcp.config import Settings
from tests.conftest import DrawingFake, declare
from tests.unit.test_drawing_auto import shaft_session
from tests.unit.test_drawing_compose import choose_steps
from tests.unit.test_drawing_finished import PROJECT_PRINCIPLE, drawing_session
from tests.unit.test_drawing_requirements import finding

SHAFT_INTENT = """# Eje

## Función
- Gira sobre dos rodamientos 6205.

## Ajustes
- Asientos de rodamiento Ø25 k6. Rosca M20x1,5 - 6g.

## Datos
- Datum A-B: el eje común de los dos asientos.

## Proceso
- Torneado desde barra, rectificado de los asientos.

## Material
- Acero C45.

## Relaciones funcionales
- Coaxialidad entre los dos asientos Ø0,02.

## Cantidad
- 10 unidades.
"""


def with_intent(settings: Settings, text: str = SHAFT_INTENT) -> None:
    (settings.workspace / "drawings").mkdir(exist_ok=True)
    (settings.workspace / "drawings" / "plate-intent.md").write_text(text, encoding="utf-8")


def reviews(fake: DrawingFake, since: int = 0) -> int:
    return sum(1 for r in fake.described[since:] if r.review)


def test_the_plan_gives_the_sequence_with_the_rules_of_this_part_and_what_the_intent_declares(settings: Settings) -> None:
    choose_steps(settings)
    with_intent(settings)
    call, fake = shaft_session(settings)
    fake.facts = {**fake.facts, "turned": 1, "keyways": 1}
    call.ok("drawing", action="create")
    text = call.ok("drawing", action="plan")
    assert "sheet type: part" in text and "processes: turning, grinding, machining" in text
    assert "material: Acero C45." in text and "quantity: 10 unidades." in text
    assert "revolution_body" in text and "bearing_seat" in text
    for number, title in enumerate(("WHAT IT IS FOR", "VIEWS", "REFERENCES", "DIMENSIONS", "TOLERANCES", "NOTES", "DELIVERABLES", "READ IT AS THE SHOP"), start=1):
        assert f"STEP {number} {title}" in text
    assert "views.turned_axial_section" not in text  # the step in view is in full, the others are one line each
    assert "views.turned_axial_section" in call.ok("drawing", action="plan", step=2) and "keyway.dimensioned" in call.ok("drawing", action="plan", step=4)
    everything = call.ok("drawing", action="plan", full=True)
    assert "views.turned_axial_section" in everything and "keyway.dimensioned" in everything and len(everything) > 2 * len(text)
    assert "circular_pattern.polar" not in everything and "welded.weld_symbols" not in everything  # not this part's
    assert "open questions" not in text  # the intent file says it all
    assert text.splitlines()[-1].startswith("step ")


def test_what_the_intent_file_does_not_say_is_an_open_question_of_the_plan(settings: Settings) -> None:
    choose_steps(settings)
    call, fake = shaft_session(settings)  # no intent file at all
    call.ok("drawing", action="create")
    text = call.ok("drawing", action="plan")
    assert "open questions (asked of the designer or the engineer, never assumed" in text
    assert "? plan.process [purpose] process" in text and "? plan.quantity [purpose] quantity" in text
    assert "STEP 1 WHAT IT IS FOR AND WHO READS IT" in text and "<- YOU ARE HERE" in text.split("STEP 2")[0]
    assert "step 1/8" in text.splitlines()[-1] and "ask plan.process" in text.splitlines()[-1]
    # what the engineer answers is recorded on the sheet: the question closes and the plan reads the answer
    call.ok("drawing", action="answer", id="plan.quantity", text="applies: 10 unidades, dicho por el ingeniero")
    call.ok("drawing", action="answer", id="plan.process", text="applies: torneado y rectificado", by="Ana", reason=call.says("torneado y rectificado"))
    again = call.ok("drawing", action="plan")
    assert "plan.process" not in again and "plan.quantity" not in again and "? plan.datums" in again
    assert "processes: turning, grinding, machining" in again and "quantity: 10 unidades" in again


@pytest.mark.parametrize("key", ["process", "fits", "relations"])
def test_the_design_facts_of_the_plan_are_never_closed_on_the_drafters_own_word(settings: Settings, key: str) -> None:
    choose_steps(settings)
    call, fake = shaft_session(settings)  # no intent file: the question is open
    call.ok("drawing", action="create")
    fake.facts = {**fake.facts, "turned": 1}  # fits are asked of a turned part
    asked = call.ok("drawing", action="plan")
    assert f"? plan.{key} " in asked
    for text in ("applies: lo que a mí me parece", "no aplica: la pieza no lo necesita"):
        refused = call.err("drawing", action="answer", id=f"plan.{key}", text=text)
        assert "design fact" in refused and "intent file" in refused and "by=<his name>" in refused
    assert not fake.drawings["Drawing"].get("answers")  # nothing was recorded
    assert f"? plan.{key} " in call.ok("drawing", action="plan")
    # the engineer's order records it, and then the question is closed
    call.ok("drawing", action="answer", id=f"plan.{key}", text="applies: lo dicho por el ingeniero", by="Ana", reason=call.says("lo dicho por el ingeniero"))
    assert f"? plan.{key} " not in call.ok("drawing", action="plan")


def test_an_answer_to_a_design_fact_that_an_old_sheet_holds_without_his_name_does_not_count(settings: Settings) -> None:
    choose_steps(settings)
    call, fake = shaft_session(settings)
    call.ok("drawing", action="create")
    from cotaforge.contract import DrawingAnswer

    fake.drawings["Drawing"]["answers"] = {"plan.process": DrawingAnswer(verdict="not_applies", text="no aplica: nada")}  # a bare one, as an earlier version stored it
    assert "? plan.process " in call.ok("drawing", action="plan")


def test_a_finding_of_the_page_shows_as_the_open_rule_of_its_step_and_plan_takes_a_step(settings: Settings) -> None:
    choose_steps(settings)
    with_intent(settings)
    call, fake = shaft_session(settings)
    call.ok("drawing", action="create")
    fake.findings = (*fake.findings, finding("cylinder_no_axis", "Cilindro sin eje"))
    text = call.ok("drawing", action="plan")
    assert "[OPEN] views.centre_lines" in text and "cylinder_no_axis" in text
    assert "step 2/8 views" in text.splitlines()[-1]
    assert "Start from the fewest views" in text  # the method of the step the sheet is at
    assert "Work in this order" not in text and "Work in this order" in call.ok("drawing", action="plan", step=4)
    assert "step is 1 to 8" in call.err("drawing", action="plan", step=9)


def test_every_reply_that_changes_or_reviews_the_sheet_ends_with_the_step_line(settings: Settings) -> None:
    choose_steps(settings)
    with_intent(settings)
    call, fake = shaft_session(settings)
    created = call.ok("drawing", action="create")
    assert created.splitlines()[-1].startswith("step ") and 'drawing(action="plan")' in created.splitlines()[-1]
    before = len(fake.described)
    added = call.ok("drawing", action="add", items=[{"type": "note", "text": "Sin tratamiento"}])
    assert added.splitlines()[-1].startswith("step ") and "step checks only" in added.splitlines()[-1]
    updated = call.ok("drawing", action="update", items=[{"id": "o1", "offset_mm": 12}])
    removed = call.ok("drawing", action="remove", items=["o1"])
    assert updated.splitlines()[-1].startswith("step ") and removed.splitlines()[-1].startswith("step ")
    assert len(fake.described) == before  # the line costs NO read of the sheet: no review, no compose, nothing
    before = len(fake.described)
    reviewed = call.ok("drawing", action="review")
    assert reviewed.splitlines()[-1].startswith("step ") and "step checks only" not in reviewed.splitlines()[-1]
    listed = call.ok("drawing", action="requirements")
    assert listed.splitlines()[-1].startswith("step ")
    before = len(fake.described)
    answered = call.ok("drawing", action="answer", id="req.coating", text="no aplica: la pieza no lleva recubrimiento")
    assert answered.splitlines()[-1].startswith("step ")
    assert reviews(fake, before) == 1  # the review that guards the answer is the one the line reads
    assert "step " not in call.ok("drawing", action="list").splitlines()[-1]  # a plain look at the sheet reviews nothing


def test_a_multi_sheet_review_gives_one_line_per_sheet(settings: Settings) -> None:
    choose_steps(settings)
    with_intent(settings)
    call, fake = shaft_session(settings)
    call.ok("drawing", action="create")
    call.ok("drawing", action="create", sheet="Second")
    text = call.ok("drawing", action="review")
    lines = [line for line in text.splitlines() if line.startswith("sheet ") and "step " in line]
    assert [line.split(":")[0] for line in lines] == ["sheet Drawing", "sheet Second"] and all('drawing(action="plan")' in line for line in lines)
    assert call.ok("drawing", action="review", sheet="Second").splitlines()[-1].startswith("step ")  # one sheet: the plain line


def test_the_answer_to_a_memory_entry_and_the_dxf_of_a_sheet_end_with_the_step_line(settings: Settings) -> None:
    declare(settings, PROJECT_PRINCIPLE)
    call, fake = drawing_session(settings, base=True)
    fake.facts = {"sheet": 1, "part": 1}  # the CAD reports facts, so the plan can be read
    assert call.ok("drawing", action="review").splitlines()[-1].startswith("step ")
    call.ok("memory", action="get", id="plano-del-taller")
    before = len(fake.described)
    answered = call.ok("drawing", action="answer", id="plano-del-taller", text="applies: el plano dice todo lo esencial")
    assert answered.splitlines()[-1].startswith("step ") and "step checks only" not in answered.splitlines()[-1]
    assert len(fake.described) == before  # no review of its own: the line comes from the last full one
    # the review-process actions change what the review step waits for, which no review already read says: no line (the docs say so)
    for reply in (
        call.ok("drawing", action="request_review"),
        call.ok("drawing", action="review_report", sheet="Drawing", by="fabricator", items=[]),
    ):
        assert not reply.splitlines()[-1].startswith("step ")


def test_the_dxf_of_a_sheet_ends_with_the_step_line_like_the_pdf(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    call, fake = drawing_session(settings)
    fake.facts = {"sheet": 1, "part": 1}  # the CAD reports facts, so the plan can be read
    monkeypatch.setattr(drawing_gate, "sheet_requirements", lambda *_a, **_k: [])  # the export is not about the rows here
    before = len(fake.described)
    for action, path in (("dxf", "sheet.dxf"), ("pdf", "sheet.pdf")):
        written = call.ok("export", action=action, path=path)
        assert written.splitlines()[-1].startswith("step ") and 'drawing(action="plan")' in written.splitlines()[-1], action
    assert reviews(fake, before) == 2  # the review of each export's own gate, none more for the line


def test_the_step_line_builds_from_the_state_a_step_returns_the_facts_and_its_own_findings(settings: Settings) -> None:
    choose_steps(settings)
    with_intent(settings)
    call, fake = shaft_session(settings)
    fake.facts = {**fake.facts, "turned": 1}
    call.ok("drawing", action="create")
    fake.step_rows = (finding("view_adds_nothing", "La vista derecha no lleva cota"),)  # what the step's own rules found
    before = len(fake.described)
    line = call.ok("drawing", action="add", items=[{"type": "note", "text": "Sin tratamiento"}]).splitlines()[-1]
    assert line.startswith("step 2/8 views: 1 open (views.each_view_shows_its_own (view_adds_nothing))"), line
    assert len(fake.described) == before
    fake.step_rows = (finding("view_adds_nothing", "La vista derecha no lleva cota", blocking=False),)  # a note, not a block
    quiet = call.ok("drawing", action="add", items=[{"type": "note", "text": "Otra"}]).splitlines()[-1]
    assert "view_adds_nothing" not in quiet and "views.each_view_shows_its_own" not in quiet


def test_a_rule_the_last_full_review_left_open_stays_in_the_line_of_an_unrelated_step(settings: Settings) -> None:
    choose_steps(settings)
    with_intent(settings)
    call, fake = shaft_session(settings)
    call.ok("drawing", action="create")
    fake.findings = (*fake.findings, finding("cylinder_no_axis", "Cilindro sin eje"))
    full = call.ok("drawing", action="review").splitlines()[-1]
    assert full.startswith("step 2/8 views") and "views.centre_lines (" in full and "cylinder_no_axis" in full
    before = len(fake.described)
    for line in (
        call.ok("drawing", action="add", items=[{"type": "note", "text": "Sin tratamiento"}]).splitlines()[-1],
        call.ok("drawing", action="update", items=[{"id": "o1", "offset_mm": 12}]).splitlines()[-1],
        call.ok("drawing", action="remove", items=["o1"]).splitlines()[-1],
    ):
        assert line.startswith("step 2/8 views") and "views.centre_lines (" in line and "cylinder_no_axis" in line and "step checks only" in line, line
    assert len(fake.described) == before  # still no read of the sheet for the line
    # the step's own rules say their word fresh each time: one that is gone is gone, one that stands joins what the review left
    fake.step_rows = (finding("view_adds_nothing", "La vista derecha no lleva cota"),)
    both = call.ok("drawing", action="add", items=[{"type": "note", "text": "Otra"}]).splitlines()[-1]
    assert "2 open" in both and "views.centre_lines" in both and "views.each_view_shows_its_own" in both
    fake.step_rows = ()
    one = call.ok("drawing", action="add", items=[{"type": "note", "text": "Y otra"}]).splitlines()[-1]
    assert "views.each_view_shows_its_own" not in one and "cylinder_no_axis" in one
    # the requirement rows read the same findings: the row of the axes is not closed by the step's partial list
    fake.findings = tuple(f for f in fake.findings if f.code != "cylinder_no_axis")
    clean = call.ok("drawing", action="review").splitlines()[-1]
    assert "cylinder_no_axis" not in clean
    assert "cylinder_no_axis" not in call.ok("drawing", action="add", items=[{"type": "note", "text": "Ultima"}]).splitlines()[-1]


def test_with_no_full_review_behind_it_the_quick_line_does_not_claim_a_step(settings: Settings) -> None:
    choose_steps(settings)
    with_intent(settings)
    call, fake = shaft_session(settings)
    call.ok("drawing", action="create")
    call.session.sheet_reviews.clear()  # this session has read no full review of the sheet (it was reopened)
    line = call.ok("drawing", action="add", items=[{"type": "note", "text": "Sin tratamiento"}]).splitlines()[-1]
    assert line.startswith("step unknown") and "no full review" in line and "step 8" not in line and "nothing open" not in line
    reviewed = call.ok("drawing", action="review").splitlines()[-1]
    assert reviewed.startswith("step ") and "unknown" not in reviewed
    after = call.ok("drawing", action="add", items=[{"type": "note", "text": "Otra"}]).splitlines()[-1]
    assert after.startswith("step ") and "unknown" not in after  # the review the session just read stands behind the next ones


def test_a_finding_that_does_not_block_is_not_an_open_rule_of_the_line(settings: Settings) -> None:
    choose_steps(settings)
    with_intent(settings)
    call, fake = shaft_session(settings)
    call.ok("drawing", action="create")
    fake.findings = (finding("cylinder_no_axis", "Cilindro sin eje", blocking=False),)
    text = call.ok("drawing", action="plan", step=2)
    assert "[ok] views.centre_lines" in text and "cylinder_no_axis" not in text
    fake.findings = (finding("cylinder_no_axis", "Cilindro sin eje"),)
    assert "[OPEN] views.centre_lines" in call.ok("drawing", action="plan", step=2)


def test_the_step_line_is_never_cut_by_a_long_reply(settings: Settings) -> None:
    choose_steps(settings)
    with_intent(settings)
    call, _ = shaft_session(settings)
    call.ok("drawing", action="create")
    long = [{"type": "note", "id": f"n{i}", "text": f"nota {i} " + "x" * 60} for i in range(45)]
    text = call.ok("drawing", action="add", items=long)
    assert "chars cut" in text  # the body gave way...
    assert text.splitlines()[-1].startswith("step ") and 'drawing(action="plan")' in text.splitlines()[-1]  # ...the line did not


def test_a_step_line_that_cannot_be_built_leaves_the_reply_and_is_logged(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    choose_steps(settings)
    with_intent(settings)
    call, _ = shaft_session(settings)
    call.ok("drawing", action="create")

    def broken(*_args: object, **_kwargs: object) -> list[object]:
        raise KeyError("an exception nobody listed")

    monkeypatch.setattr(drawing_gate, "sheet_requirements", broken)
    with caplog.at_level(logging.WARNING):
        text = call.ok("drawing", action="add", items=[{"type": "note", "text": "Sin tratamiento"}])
    assert "drawing Drawing" in text and "step " not in text.splitlines()[-1]  # the add answered, without the line
    assert any("step line" in record.getMessage() for record in caplog.records)


def test_review_and_requirements_read_the_sheet_once_for_their_reply_and_their_line(settings: Settings) -> None:
    choose_steps(settings)
    call, fake = shaft_session(settings)
    call.ok("drawing", action="create")
    call.ok("drawing", action="review")  # the automatic items are on: the next ones add nothing
    for action in ("review", "requirements"):
        before = len(fake.described)
        call.ok("drawing", action=action)
        assert reviews(fake, before) == 1, action


def test_in_one_order_the_plan_keeps_its_decisions_and_adds_the_sequence_and_compose_ends_with_the_line(settings: Settings) -> None:
    with_intent(settings)
    call, fake = shaft_session(settings)  # the default flow: the sheet in one order
    text = call.ok("drawing", action="plan")
    assert "decisions it needs" in text and "work sequence of sheet Drawing" in text
    assert text.index("decisions it needs") < text.index("STEP 1 WHAT IT IS FOR")
    assert text.splitlines()[-1].startswith("step ")
