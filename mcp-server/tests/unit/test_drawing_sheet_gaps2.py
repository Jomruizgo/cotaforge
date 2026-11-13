# ruff: noqa: E501
"""What the second review of the two real sheets found still wrong, at the gate: a sheet that wastes its paper (with a remedy), a
view or a section that adds nothing, and a part in the parts list with no designation all hold the export, and the agent
cannot dismiss them on its own word; the designation of a part is set with ``doc set_title``."""

import pytest
from cotaforge.contract import DrawingFinding

from cotaforge_mcp import drawing_requirements as dr
from cotaforge_mcp.config import Settings
from tests.conftest import Caller
from tests.unit.test_drawing_review import session_in

CODES = {
    "sheet_underused": "Las vistas y sus cotas ocupan solo el 14 % del área de dibujo: cabe una escala mayor (scale=5, 5 : 1).",
    "view_adds_nothing": "La vista right es la vista de extremo de una pieza torneada y no lleva ninguna cota.",
    "part_unnamed": "Piezas sin designación en la lista de piezas: 1 (comp:1).",
    "no_isometric": "La hoja no lleva vista isométrica: todo plano de fabricación la lleva.",
}


def held(code: str) -> DrawingFinding:
    return DrawingFinding(code=code, severity="warning", message=CODES[code], id=code, blocking=True, items=("right",))


def test_the_findings_the_second_review_added_are_measured_so_only_the_engineer_can_dismiss_them() -> None:
    assert {"sheet_underused", "view_adds_nothing", "part_unnamed", "no_isometric"} <= dr.MEASURED_FINDINGS


@pytest.mark.parametrize("code", sorted(CODES))
def test_each_one_holds_the_export_and_the_agents_own_word_does_not_close_it(settings: Settings, code: str) -> None:
    call, fake = session_in(settings, "engineer")
    fake.findings = (held(code),)
    refused = call.err("export", action="pdf")
    assert f"- {code}:" in refused and "export pdf refused" in refused
    assert "measured" in call.err("drawing", action="answer", id=code, text="no aplica: me parece bien así")
    assert f"- {code}:" in call.err("export", action="pdf")  # the agent's own reason does not count for a measurement
    call.ok(
        "drawing",
        action="answer",
        id=code,
        text="no aplica: el corte enseña el montaje",
        by="Ana",
        reason="el corte B-B enseña cómo encajan las placas con los separadores",
    )
    assert f"- {code}:" not in call.err("export", action="pdf")  # closed by the engineer's order, the rest still holds


def test_a_gap_with_no_remedy_is_only_a_notice_and_never_arrives_as_blocking() -> None:
    notice = DrawingFinding(
        code="sheet_unbalanced", severity="warning", message="x", id="sheet_unbalanced", blocking=False
    )
    assert "sheet_unbalanced" not in dr.MEASURED_FINDINGS and notice.blocking is False


def test_the_designation_of_a_part_is_set_with_the_title_action_and_shown_in_the_status(call: Caller) -> None:
    call.ok("doc", action="new", name="top-plate")
    text = call.ok("doc", action="set_title", name="Placa superior")
    assert text.endswith("title: Placa superior")
    assert "title: Placa superior" in call.ok("doc", action="status")
    assert "title:" not in call.ok("doc", action="set_title", name="")  # an empty string clears it
    assert "field: name" in call.err("doc", action="set_title")
