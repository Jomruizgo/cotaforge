# ruff: noqa: E501
"""``drawing(action=plan)`` serves the work sequence of THIS sheet with the catalog rules of each step, and every drawing
reply ends with the step the sheet is at. The status is read from the page (the CAD's review), never kept aside."""

from cotaforge_mcp.config import Settings
from tests.conftest import DrawingFake
from tests.unit.test_drawing_auto import shaft_session
from tests.unit.test_drawing_compose import choose_steps
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
    assert "views.turned_axial_section" in text and "keyway.dimensioned" in text
    assert "circular_pattern.polar" not in text and "welded.weld_symbols" not in text  # not this part's
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
    call.ok("drawing", action="answer", id="plan.process", text="applies: torneado y rectificado, dicho por el ingeniero")
    call.ok("drawing", action="answer", id="plan.quantity", text="applies: 10 unidades, dicho por el ingeniero")
    again = call.ok("drawing", action="plan")
    assert "plan.process" not in again and "plan.quantity" not in again and "? plan.datums" in again
    assert "processes: turning, grinding, machining" in again and "quantity: 10 unidades" in again


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
    assert added.splitlines()[-1].startswith("step ")
    assert reviews(fake, before) == 1  # the line costs ONE review of the sheet, nothing else
    before = len(fake.described)
    reviewed = call.ok("drawing", action="review")
    assert reviewed.splitlines()[-1].startswith("step ")
    listed = call.ok("drawing", action="requirements")
    assert listed.splitlines()[-1].startswith("step ")
    answered = call.ok("drawing", action="answer", id="req.coating", text="no aplica: la pieza no lleva recubrimiento")
    assert answered.splitlines()[-1].startswith("step ")
    assert "step " not in call.ok("drawing", action="list").splitlines()[-1]  # a plain look at the sheet reviews nothing


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
