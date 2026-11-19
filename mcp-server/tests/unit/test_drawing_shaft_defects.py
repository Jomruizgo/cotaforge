# ruff: noqa: E501
"""What the machinist of a shaft sheet rejects and the harness decides before the CAD draws anything: the same note written
twice, a keyway width with no class although the intent file gives it, and a thread the intent file declares that the model
does not hold (a note prints no thread lines)."""

from types import SimpleNamespace
from typing import Any

from cotaforge_mcp import drawing_compose as compose
from cotaforge_mcp import drawing_requirements as dr
from tests.unit.test_drawing_requirements import PLATE, SHAFT, finding

TREATMENTS = "Sin tratamiento térmico. Sin recubrimiento (aceite antioxidante)."
INTENT = dr.parse_intent(
    f"""## Ajustes
- Rosca M20x1,5 - 6g, longitud roscada 26.
- Chavetero para chaveta 6 x 6 DIN 6885 forma A: ancho 6 P9, profundidad en el eje 3,5.

## Tratamientos y acabado
- {TREATMENTS}
"""
)


def stored_note(text: str, item_id: str = "n1") -> SimpleNamespace:
    return SimpleNamespace(id=item_id, type="note", spec={"id": item_id, "type": "note", "text": text})


def treatment_rows() -> list[dr.Requirement]:
    codes = (finding("notes_heat_treatment", "Falta"), finding("notes_coating", "Falta"))
    return dr.evaluate({**PLATE}, codes, {}, INTENT)


def notes_of(additions: list[dr.Addition]) -> list[str]:
    return [a.item["text"] for a in additions if a.item["type"] == "note"]


def test_one_line_of_the_intent_that_declares_two_rows_is_one_note_not_two() -> None:
    notes = [a for a in dr.plan_additions(treatment_rows(), []) if a.item["type"] == "note" and TREATMENTS in a.item["text"]]
    assert len(notes) == 1, [a.item for a in notes]
    assert {"req.heat_treatment", "req.coating"} <= set(notes[0].rows)  # the one note meets both


def test_a_note_the_sheet_already_carries_is_not_added_again() -> None:
    added = dr.plan_additions(treatment_rows(), [stored_note(TREATMENTS.upper().replace("TÉRMICO", "térmico"))])
    assert TREATMENTS not in notes_of(added)


def test_the_same_note_in_two_decisions_reaches_the_sheet_once() -> None:
    items, _, _ = compose.normalise({"notes": ["Cantidad 10.", "cantidad  10.", "Romper aristas"]})
    assert [i["text"] for i in items if i["type"] == "note"] == ["Cantidad 10.", "Romper aristas"]


def test_the_class_of_the_keyway_width_is_read_from_the_line_of_the_intent_that_names_the_keyway() -> None:
    assert INTENT.keyway_fit() == "P9"
    assert dr.parse_intent("## Ajustes\n- Chavetero 6 x 6 forma A, longitud 25.\n").keyway_fit() == ""
    assert dr.parse_intent("## Ajustes\n- Asiento Ø25 P9 de rodamiento.\n").keyway_fit() == ""  # a P9 that is not the keyway's


def test_the_keyway_the_harness_dimensions_carries_that_class() -> None:
    facts: dict[str, Any] = {**SHAFT, "keyways_measured": 1, "keyways": 1}
    added = dr.plan_additions(dr.evaluate(facts, (), {}, INTENT), [], keyway_fit=INTENT.keyway_fit())
    (keyway,) = [a.item for a in added if a.item["type"] == "keyway_dims"]
    assert keyway["fit"] == "P9"
    bare = dr.plan_additions(dr.evaluate(facts, (), {}, INTENT), [])
    assert "fit" not in [a.item for a in bare if a.item["type"] == "keyway_dims"][0]


def test_a_thread_the_intent_declares_on_a_shaft_that_the_model_does_not_hold_is_missing_and_says_how_to_model_it() -> None:
    facts: dict[str, Any] = {**PLATE, "turned": 1, "thread_designation_on_sheet": 1}  # a note prints the designation
    (row,) = [r for r in dr.evaluate(facts, (), {}, INTENT) if r.id == "req.thread"]
    assert row.state == dr.MISSING and any("external_thread" in text for text in row.lacking)
    held = dr.evaluate({**facts, "thread_features": 1}, (), {}, INTENT)
    assert [r.state for r in held if r.id == "req.thread"] == [dr.MET]


def test_the_thread_line_of_a_part_that_is_not_turned_or_a_fastener_it_takes_asks_nothing_more() -> None:
    plate = {**PLATE, "thread_designation_on_sheet": 1}
    assert [r.state for r in dr.evaluate(plate, (), {}, INTENT) if r.id == "req.thread"] == [dr.MET]
    clamp = dr.parse_intent("## Función\n- Soporte que recibe tornillos M6 de la tapa.\n")
    shaft = {**PLATE, "turned": 1, "thread_designation_on_sheet": 1}
    assert [r.state for r in dr.evaluate(shaft, (), {}, clamp) if r.id == "req.thread"] == [dr.MET]


def test_the_question_about_that_thread_says_where_it_goes_in_both_languages() -> None:
    facts: dict[str, Any] = {**PLATE, "turned": 1, "thread_designation_on_sheet": 1}
    (row,) = [r for r in dr.evaluate(facts, (), {}, INTENT) if r.id == "req.thread"]
    assert "external_thread" in dr.question_of(row, "en") and "una nota no dibuja" in dr.question_of(row, "es")
    held = [r for r in dr.evaluate({**facts, "thread_features": 1}, (), {}, INTENT) if r.id == "req.thread"][0]
    assert "external_thread" not in dr.question_of(held, "en")
