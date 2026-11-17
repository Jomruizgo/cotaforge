# ruff: noqa: E501
"""The line a step reply ends with is help, never part of the step: whatever breaks while the sheet as the step left it is read, the
step that was already written stands and its reply says so. A retry of a ``remove`` that was written would fail with "no item".
Plain asserts run inside ``freecadcmd`` (see ``run.py``)."""

from typing import Any

import drawing
from check_drawing_live import add, build_part, remove
from harness import call, op


def _item_ids() -> list[str]:
    return [i["id"] for i in call("describe_drawing", op("r"))["sheets"][0]["items"]]


def _broken(*_args: Any, **_kwargs: Any) -> Any:
    raise RuntimeError("an exception nobody listed")


def check_the_state_a_step_returns_names_the_rules_it_ran() -> None:
    build_part()
    call("create_drawing", op("d", template="A3", title="Placa"))
    added = add({"type": "note", "text": "Sin rebabas"})
    state = added["page_state"]
    assert state is not None and state["facts"], state
    import sheet_viewchecks  # noqa: PLC0415

    assert tuple(state["checked_codes"]) == sheet_viewchecks.STEP_CODES  # a full review leaves it empty: its findings are all of them
    assert not call("describe_drawing", op("r", review=True))["sheets"][0].get("checked_codes")


def check_a_remove_already_written_answers_without_the_line_when_reading_the_state_fails() -> None:
    build_part()
    call("create_drawing", op("d", template="A3", title="Placa"))
    added = add({"type": "note", "text": "Sin rebabas"}, {"type": "note", "text": "Aristas rotas"})
    gone = [r["id"] for r in added["items"] if r["type"] == "note"][0]
    real = {name: getattr(drawing, name) for name in ("step_rows", "_review_data", "_page_state")}
    try:
        for name in real:  # each of the three reads the step line stands on
            setattr(drawing, name, _broken)
            try:
                result = remove(gone)
            finally:
                setattr(drawing, name, real[name])
            assert result["page_state"] is None and result["step_findings"] == [], name
            assert any("step line could not be read" in w for w in result["warnings"]), (name, result["warnings"])
            assert gone not in _item_ids(), f"{name}: the remove was written and must stand"
            added = add({"type": "note", "text": "Otra vez"})  # the next round of the loop has something to remove
            gone = [r["id"] for r in added["items"] if r["type"] == "note" and r["id"] in _item_ids()][-1]
    finally:
        for name, func in real.items():
            setattr(drawing, name, func)
    again = remove(gone)  # with the reads working the same call answers with its line again
    assert again["page_state"] is not None and not again["warnings"], again["warnings"]
