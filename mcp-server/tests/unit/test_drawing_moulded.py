# ruff: noqa: E501
"""The row of the table of requirements for a moulded part (``req.moulded_part``, ISO 10135): the intent file declares injection, so
the sheet must carry the parting line, the opening direction with its draft, the gate zone and the ejector zones as SYMBOLS (a note
of text does not meet it); the harness adds the parting line itself (measured on the model) and asks for the rest."""

from cotaforge_mcp import drawing_requirements as dr
from tests.unit.test_drawing_requirements import PLATE


def rows(facts: dict[str, int], intent: str = "") -> dict[str, dr.Requirement]:
    parsed = dr.parse_intent(intent) if intent else dr.Intent()
    return {r.id: r for r in dr.evaluate(facts, (), {}, parsed)}


class Item:
    def __init__(self, item_id: str, kind: str) -> None:
        self.id = item_id
        self.type = kind


INJECTION = "## Process\nMoldeo por inyección, PC/ABS V-0, 150 000 piezas/año.\n"
CARRIES = {
    "mould_items": 3,
    "mould_parting_on_sheet": 1,
    "mould_pull_on_sheet": 1,
    "mould_draft_on_sheet": 1,
    "mould_gate_on_sheet": 1,
    "mould_ejector_on_sheet": 1,
}


def test_a_plate_with_no_intent_of_injection_is_not_asked_for_moulding_symbols() -> None:
    assert "req.moulded_part" not in rows(PLATE)


def test_the_intent_that_says_injection_activates_the_row_and_the_standard_is_named_by_number_only() -> None:
    found = rows(PLATE, INJECTION)["req.moulded_part"]
    assert found.row.standard == "ISO 10135" and not found.row.clause  # no clause until somebody checks the text
    assert found.state == dr.MISSING and found.measured  # the intent declares it: not dismissed on the agent's word
    wanted = " ".join(found.lacking)
    for what in ("parting line", "opening direction", "general draft", "gate zone", "ejector zones"):
        assert what in wanted, what


def test_the_english_intent_and_other_words_for_the_process_activate_it_too() -> None:
    for text in ("Injection moulded ABS part", "Process: plastic injection", "Molded in PP", "moulding process: two-plate mould"):
        assert "req.moulded_part" in rows(PLATE, f"## Process\n{text}\n"), text


def test_notes_do_not_meet_it_only_the_symbols_do() -> None:
    """Fifteen notes in the draft said it all in words; the row counts items."""
    assert rows({**PLATE, "process_on_sheet": 1}, INJECTION)["req.moulded_part"].state == dr.MISSING


def test_the_row_is_met_when_the_sheet_carries_all_five() -> None:
    found = rows({**PLATE, **CARRIES}, INJECTION)["req.moulded_part"]
    assert found.state == dr.MET and found.closed


def test_a_missing_symbol_is_named_one_by_one() -> None:
    found = rows({**PLATE, **{**CARRIES, "mould_ejector_on_sheet": 0, "mould_draft_on_sheet": 0}}, INJECTION)["req.moulded_part"]
    assert found.state == dr.MISSING
    assert len(found.lacking) == 2 and any("ejector" in t for t in found.lacking) and any("draft" in t for t in found.lacking)


def test_the_harness_adds_the_parting_line_it_measures_and_never_the_decisions() -> None:
    active = list(rows(PLATE, INJECTION).values())
    (added,) = [a for a in dr.plan_additions(active, []) if "req.moulded_part" in a.rows]
    assert added.item == {"type": "parting_line", "id": dr.PARTING_ID} and "measured on the model" in added.what
    kinds = {a.item["type"] for a in dr.plan_additions(active, [])}
    assert not kinds & {"pull_direction", "mould_zone", "no_draft"}  # the draft, the gate and the ejectors are the designer's
    # the engineer's own parting line comes first, and an item already added is not added twice
    assert not [a for a in dr.plan_additions(active, [Item("pl1", "parting_line")]) if "req.moulded_part" in a.rows]
    assert not [a for a in dr.plan_additions(active, [Item(dr.PARTING_ID, "parting_line")]) if "req.moulded_part" in a.rows]


def test_the_item_the_harness_added_belongs_to_the_row_and_the_question_proposes_the_measured_ones() -> None:
    row = rows(PLATE, INJECTION)["req.moulded_part"].row
    assert dr.owns(row, dr.PARTING_ID) and [r.id for r in dr.rows_of_item(dr.PARTING_ID)] == ["req.moulded_part"]
    assert "parting line" in " ".join(row.proposal) or "línea de partición" in " ".join(row.proposal)


def test_a_sheet_with_a_mould_item_is_asked_even_when_the_intent_is_silent() -> None:
    found = rows({**PLATE, "mould_items": 1, "mould_parting_on_sheet": 1}, "")["req.moulded_part"]
    assert found.state == dr.MISSING and found.measured and not found.intent_line


def test_a_paper_too_small_for_its_notes_is_measured_so_the_agent_cannot_dismiss_it() -> None:
    assert "paper_too_small" in dr.MEASURED_FINDINGS and "sheet_underused" in dr.MEASURED_FINDINGS
