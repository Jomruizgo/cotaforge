# ruff: noqa: E501
"""A released sheet that changes gets a revision (ISO 7200): the first issue carries no row; a change after a PDF went out puts
a row in the title block's own revision table (letter, what changed with the PREVIOUS value, who asked, the date), a marker
beside the changed dimension and the revision field; an earlier row is never rewritten."""

from cotaforge.contract import DrawingMeasure

from cotaforge_mcp import drawing_revisions as rv
from cotaforge_mcp.config import Settings
from tests.conftest import Caller
from tests.unit.test_drawing_finished import drawing_session
from tests.unit.test_drawing_requirements import PLATE


def hole(value: float, id: str = "h1") -> DrawingMeasure:
    return DrawingMeasure(id=id, kind="hole", value_mm=value, toleranced=True)


def revisions(fake: object) -> list[dict]:
    return [i for i in fake.drawings["Drawing"]["items"] if i["type"] == "revision"]  # type: ignore[attr-defined]


def again(call: Caller, **arguments: object) -> str:
    """Export a sheet that changed after its release: the row the harness puts on it is a change, so it is refused until the
    agent has looked at the sheet again, and then the same call goes through (the row is not written twice)."""
    refused = call.err("export", action="pdf", overwrite=True, **arguments)
    assert "sheet_not_looked_at" in refused and "revision" in refused
    call.ok("drawing", action="capture")
    return call.ok("export", action="pdf", overwrite=True, **arguments)


def released(settings: Settings) -> tuple[Caller, object]:
    call, fake = drawing_session(settings, mode="none")
    fake.facts = {**PLATE}  # type: ignore[attr-defined]
    fake.measures = (hole(8.0), DrawingMeasure(id="d1", kind="distance", value_mm=40.0))  # type: ignore[attr-defined]
    return call, fake


def test_the_first_issue_carries_no_revision_row(settings: Settings) -> None:
    call, fake = released(settings)
    assert "exported pdf" in call.ok("export", action="pdf")
    assert revisions(fake) == []
    assert "req.released" in fake.drawings["Drawing"]["answers"]  # what the sheet stated is remembered IN the sheet, not in a file
    assert not list((settings.workspace).rglob("*released*"))  # no file outside the document


def test_a_sheet_exported_again_unchanged_gets_no_row(settings: Settings) -> None:
    call, fake = released(settings)
    call.ok("export", action="pdf")
    assert "exported pdf" in call.ok("export", action="pdf", overwrite=True)
    assert revisions(fake) == []


def test_a_changed_hole_after_the_first_export_puts_a_row_with_the_previous_value_the_requester_and_the_marker(settings: Settings) -> None:
    call, fake = released(settings)
    call.ok("export", action="pdf")
    fake.measures = (hole(10.0), DrawingMeasure(id="d1", kind="distance", value_mm=40.0))
    text = again(call, revision_by="Cliente Norte", revision_reason="NC")
    (row,) = revisions(fake)
    assert row["index"] == "A" and row["by"] == "Cliente"  # the narrow column holds 8 characters
    assert "agujero" in row["description"] and "Ø8" in row["description"] and "Ø10" in row["description"]  # the previous and the new value
    assert row["description"].endswith("; NC") and row["at"] == ["h1:10"]  # the marker goes beside the changed dimension, the one that states 10
    assert len(row["date"]) == 10 and "revision A" in text


def test_the_requester_is_the_engineer_when_nobody_said(settings: Settings) -> None:
    call, fake = released(settings)
    call.ok("export", action="pdf")
    fake.measures = (hole(8.0), DrawingMeasure(id="d1", kind="distance", value_mm=42.0))
    again(call)
    (row,) = revisions(fake)
    assert row["by"] == "ing." and "d1" in row["description"] and "40" in row["description"] and "42" in row["description"]


def test_the_next_change_is_the_next_letter_and_the_earlier_row_is_left_as_it_was(settings: Settings) -> None:
    call, fake = released(settings)
    call.ok("export", action="pdf")
    fake.measures = (hole(10.0), DrawingMeasure(id="d1", kind="distance", value_mm=40.0))
    again(call)
    (first,) = [dict(r) for r in revisions(fake)]
    fake.measures = (hole(12.0), DrawingMeasure(id="d1", kind="distance", value_mm=40.0))
    again(call)
    rows = revisions(fake)
    assert [r["index"] for r in rows] == ["A", "B"] and rows[0] == first  # never rewritten
    assert "Ø10" in rows[1]["description"] and "Ø12" in rows[1]["description"] and rows[0]["at"] == ["h1:10"]


def test_a_revision_the_engineer_wrote_by_hand_after_the_release_stands_and_the_harness_adds_none(settings: Settings) -> None:
    call, fake = released(settings)
    call.ok("export", action="pdf")
    fake.measures = (hole(10.0), DrawingMeasure(id="d1", kind="distance", value_mm=40.0))
    call.ok("drawing", action="add", items=[{"type": "revision", "id": "mine", "index": "A", "description": "mi cambio", "by": "yo"}])
    call.ok("drawing", action="capture")  # the row he added is a change somebody looked at
    call.ok("export", action="pdf", overwrite=True)
    assert [r["id"] for r in revisions(fake)] == ["mine"]


def test_a_row_not_released_yet_is_not_added_twice_when_the_export_is_tried_again(settings: Settings) -> None:
    call, fake = released(settings)
    call.ok("export", action="pdf")
    fake.measures = (hole(10.0), DrawingMeasure(id="d1", kind="distance", value_mm=40.0))
    again(call)
    call.ok("export", action="pdf", overwrite=True)  # the same change again: the row of the first try stands, nothing is rewritten
    assert [r["index"] for r in revisions(fake)] == ["A"]


def test_the_text_of_a_row_fits_the_field_and_says_how_many_more_changed() -> None:
    phrases = [f"d{n}: Ø{n}0 a Ø{n}1" for n in range(1, 12)]
    text = rv.description(phrases, "es")
    assert len(text) <= rv.MAX_DESCRIPTION and text.startswith("d1: Ø10 a Ø11") and "(+10 más)" in text
    assert len(rv.description(phrases, "es", rv.COLUMN_CHARS["narrow"][0])) <= rv.COLUMN_CHARS["narrow"][0]
    assert rv.next_letter([]) == "A" and rv.next_letter(["A", "B"]) == "C" and rv.next_letter(["-"]) == "A"


def test_one_callout_of_four_holes_where_one_changed_is_one_change_whatever_the_order_the_holes_come_in(settings: Settings) -> None:
    call, fake = released(settings)
    fake.measures = (hole(6.6), hole(6.6), hole(6.6), hole(6.6))
    call.ok("export", action="pdf")
    fake.measures = (hole(9.0), hole(6.6), hole(6.6), hole(6.6))  # the changed hole now comes first
    again(call)
    (row,) = revisions(fake)
    assert row["description"] == "agujero: Ø6,6 a Ø9" and row["at"] == ["h1:9"]  # one phrase with the previous value, not four


def test_a_reason_that_does_not_fit_the_narrow_column_of_the_title_block_is_not_written_into_the_row(settings: Settings) -> None:
    call, fake = released(settings)
    call.ok("export", action="pdf")
    fake.measures = (hole(10.0), DrawingMeasure(id="d1", kind="distance", value_mm=40.0))
    again(call, revision_reason="el cliente pidio un agujero mayor")  # the A4 table shows about 26 characters of the description
    (row,) = revisions(fake)
    assert row["description"] == "agujero: Ø8 a Ø10" and len(row["description"]) <= rv.COLUMN_CHARS["narrow"][0]
