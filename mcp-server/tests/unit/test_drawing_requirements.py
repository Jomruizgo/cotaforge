# ruff: noqa: E501
"""What the standards ask a sheet to carry (``drawing_requirements``): the table is data that the CAD's facts activate, the
export waits for every active row to be met or answered, and a measured row (or finding) is not dismissed on the agent's
word. The fake CAD reports the facts a real one counts (``DrawingFake.facts``); the table is also checked against the
FreeCAD worker's vocabulary of facts and finding codes."""

import re
import sys
from pathlib import Path
from typing import Any

import pytest
from cotaforge.contract import DrawingAnswer, DrawingFinding

from cotaforge_mcp import drawing_requirements as dr
from cotaforge_mcp.config import Settings
from tests.conftest import Caller, DrawingFake
from tests.unit.test_drawing_finished import NOT_LOCATED, drawing_session

WORKER = Path(__file__).resolve().parents[3] / "kernel" / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER) not in sys.path:
    sys.path.append(str(WORKER))

import sheet_checks  # noqa: E402
import sheet_reqfacts  # noqa: E402

ALL_FACTS = {name: 0 for name in sheet_reqfacts.FACT_NAMES}
"""A sheet that counts nothing yet (a plate with its title block): the CAD reports every fact, most of them 0."""
PLATE = {**ALL_FACTS, "sheet": 1, "part": 1, "holes": 4, "title_material": 1, "title_general_tolerance": 1, "title_scale": 1, "title_projection": 1}
SHAFT = {
    **PLATE,
    "holes": 1,
    "threaded_holes": 1,
    "fit_holes": 2,
    "fit_holes_uncalled": 1,
    "functional_faces": 2,
    "bosses": 3,
    "turned": 1,
    "boss_radii_undimensioned": 2,
    "keyways": 1,
}


def finding(code: str, message: str = "x", blocking: bool = True, answer: DrawingAnswer | None = None) -> DrawingFinding:
    return DrawingFinding(code=code, severity="warning", message=message, id=code, blocking=blocking, answer=answer)


def rows(facts: dict[str, int], findings: tuple[Any, ...] = (), answers: dict[str, Any] | None = None, intent: dr.Intent | None = None) -> dict[str, dr.Requirement]:
    return {r.id: r for r in dr.evaluate(facts, findings, answers or {}, intent or dr.Intent())}


# --- the table is data and agrees with the CAD that fills it ------------------------------------------------------------------


def test_the_table_has_unique_ids_both_languages_and_a_standard_by_number_only() -> None:
    assert len(dr.ROWS) == len(dr.ROW_IDS) == len(set(dr.ROW_IDS)) >= 15
    for row in dr.ROWS:
        assert row.id.startswith(dr.PREFIX)
        assert all(row.title) and all(row.must) and all(row.question) and all(row.proposal), row.id
        assert not row.clause  # a clause is written only after the standard's text was checked: none has been
        assert re.fullmatch(r"(?:(?:ISO|DIN) [0-9]{2,5}(?:-[0-9])?(?: / ISO [0-9]{2,5})?(?: \([^)]*\))?(?:, )?)*", row.standard), row.standard
    the_standards = {row.standard for row in dr.ROWS}
    assert {"ISO 6410", "ISO 286-1, ISO 286-2", "ISO 13715", "ISO 2553, ISO 13920"} <= the_standards


def test_every_fact_the_table_reads_is_one_the_worker_counts() -> None:
    assert dr.FACTS_USED <= set(sheet_reqfacts.FACT_NAMES), dr.FACTS_USED - set(sheet_reqfacts.FACT_NAMES)


def test_every_finding_a_row_takes_in_is_a_code_the_worker_can_make() -> None:
    made: set[str] = set()
    for path in WORKER.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        made |= set(re.findall(r'(?:Finding|_finding)\(\s*"([a-z0-9_]+)"', text))
        made |= set(re.findall(r'"code":\s*"([a-z0-9_]+)"', text))
        made |= set(re.findall(r'\bcode = "([a-z0-9_]+)"', text))  # a code chosen by a branch, then used by Finding(code, ...)
    made |= {f"notes_{code}" for code, *_ in sheet_checks.NOTE_RULES} | {"notes_material"}
    claimed = {c for row in dr.ROWS for c in row.codes}
    assert claimed <= made, claimed - made
    assert dr.MEASURED_FINDINGS <= made, dr.MEASURED_FINDINGS - made
    assert not dr.MEASURED_FINDINGS & {"collision", "fit_hole_no_tolerance", "text_over_part", "view_adds_nothing", "notes_edges"}


# --- what activates a row, and its state --------------------------------------------------------------------------------------


def test_a_plain_plate_activates_the_rows_of_every_part_and_not_the_thread_the_fit_or_the_welds() -> None:
    active = rows(PLATE)
    assert {"req.general_tolerance", "req.title_block", "req.surface_texture", "req.edge_state", "req.heat_treatment", "req.dimensions", "req.centre_lines", "req.quantity_process"} <= set(active)
    assert not {"req.thread", "req.fit_class", "req.welds", "req.parts_list", "req.keyway", "req.turned_part", "req.bent_sheet", "req.geometric_tolerance"} & set(active)
    assert active["req.general_tolerance"].state == dr.MET and active["req.title_block"].state == dr.MET


def test_a_threaded_fitted_shaft_activates_its_rows_and_they_are_measured() -> None:
    active = rows(SHAFT)
    for row_id in ("req.thread", "req.fit_class", "req.turned_part", "req.keyway"):
        assert row_id in active, row_id
    assert active["req.thread"].measured and active["req.fit_class"].measured and active["req.turned_part"].measured
    assert active["req.thread"].state == dr.MISSING
    assert any("a printed thread designation" in text for text in active["req.thread"].lacking)
    assert active["req.fit_class"].state == dr.MISSING
    assert any("fit holes with no callout" in text for text in active["req.fit_class"].lacking)
    assert any("2 steps with no diameter dimension" in text for text in active["req.turned_part"].lacking)
    assert active["req.keyway"].state == dr.MISSING and not active["req.keyway"].measured  # named, not measured
    assert any("fit class of the width" in text for text in active["req.keyway"].lacking)
    assert rows({**SHAFT, "keyways_measured": 1})["req.keyway"].measured  # by the shape of the part: measured
    assert "req.thread" in rows({**PLATE, "thread_features": 1})  # an external thread, named so, activates the row


def test_a_row_is_met_when_the_sheet_carries_what_it_needs() -> None:
    carried = {**SHAFT, "thread_designation_on_sheet": 1, "tolerance_classes_on_sheet": 2, "fit_holes_uncalled": 0, "boss_radii_undimensioned": 0}
    active = rows(carried)
    assert active["req.thread"].state == dr.MET and active["req.fit_class"].state == dr.MET and active["req.turned_part"].state == dr.MET


def test_a_finding_a_row_takes_in_makes_it_missing_and_an_answered_one_does_not() -> None:
    open_one = rows(PLATE, (finding("notes_edges"),))["req.edge_state"]
    assert open_one.state == dr.MISSING and open_one.codes == ("notes_edges",)
    answered = finding("notes_edges", answer=DrawingAnswer(verdict="not_applies", text="no aplica: todas las aristas llevan chaflán modelado"))
    assert rows(PLATE, (answered,))["req.edge_state"].state == dr.MET  # edges are a judgement: the agent's answer counts
    measured = finding("hole_not_located", answer=DrawingAnswer(verdict="not_applies", text="no aplica: es de paso"))
    assert rows(PLATE, (measured,))["req.dimensions"].state == dr.MISSING  # a measurement: the answer does not close it
    ordered = finding("hole_not_located", answer=DrawingAnswer(verdict="not_applies", text="no aplica: es de paso [ordered by Ana: «sí»]", ordered_by="Ana"))
    assert rows(PLATE, (ordered,))["req.dimensions"].state == dr.MET


def test_no_facts_no_rows() -> None:
    assert dr.evaluate({}, (), {}, dr.Intent()) == []


def test_an_answer_closes_a_row_unless_it_dismisses_a_measured_one_without_the_engineers_order() -> None:
    met = DrawingAnswer(verdict="applies", text="met: thread in the callout h1")
    dismissed = DrawingAnswer(verdict="not_applies", text="no aplica: no lleva rosca")
    ordered = DrawingAnswer(verdict="not_applies", text="no aplica: x [ordered by Ana: «quítala»]", ordered_by="Ana")
    assert rows(SHAFT, answers={"req.thread": met})["req.thread"].closed
    refused = rows(SHAFT, answers={"req.thread": dismissed})["req.thread"]
    assert not refused.closed and refused.refused_answer
    assert rows(SHAFT, answers={"req.thread": ordered})["req.thread"].closed
    not_measured = rows(SHAFT, answers={"req.keyway": dismissed})["req.keyway"]
    assert not_measured.closed  # a keyway by name is not a measurement: the agent may say why it does not apply


# --- the intent file ---------------------------------------------------------------------------------------------------------


INTENT_ES = """# Intención: eje

## Función
Transmite el par del motor al reductor.

## Ajustes
- Asiento del rodamiento Ø25 k6; agujero del piñón H7.

## Tratamientos y acabado (calor, recubrimiento, rugosidad, aristas, marcado)
- Temple y revenido a 40 HRC.
- Romper aristas 0,5 mm.

## Soldaduras
undefined

## Cotas
<!-- tolerancia general, críticas, libres -->
"""


def test_the_intent_file_is_read_by_section_in_either_language_and_undefined_is_empty() -> None:
    intent = dr.parse_intent(INTENT_ES)
    assert set(intent.sections) == {"function", "fits", "treatments"}  # undefined and comment-only sections are empty
    assert "k6" in intent.sections["fits"] and "HRC" in intent.sections["treatments"]
    english = dr.parse_intent("## Fits\n- Bearing seat 25 k6\n\n## Treatments and finish\n- No heat treatment\n## Welds\n- Fillet, ISO 13920 class B\n")
    assert set(english.sections) == {"fits", "treatments", "welds"}
    assert intent.silent_on(next(r for r in dr.ROWS if r.id == "req.welds"))


def test_what_the_intent_declares_activates_a_row_and_makes_it_measured() -> None:
    intent = dr.parse_intent(INTENT_ES)
    active = rows(PLATE, intent=intent)
    assert "req.fit_class" in active and active["req.fit_class"].measured and "k6" in active["req.fit_class"].intent_line
    assert active["req.heat_treatment"].measured and "40 HRC" in active["req.heat_treatment"].intent_line
    assert "req.welds" not in active  # the heading says undefined: nothing declared, the drafter asks
    plain = rows(PLATE)
    assert not plain["req.heat_treatment"].measured and "req.fit_class" not in plain


def test_the_intent_file_is_found_by_the_name_of_the_open_part(tmp_path: Path) -> None:
    drawings = tmp_path / "drawings"
    drawings.mkdir()
    (drawings / "eje-intent.md").write_text(INTENT_ES, encoding="utf-8")
    (drawings / "placa-intent.md").write_text("## Fits\n- none needed\n", encoding="utf-8")
    assert "fits" in dr.find_intent(tmp_path, "eje").sections and dr.find_intent(tmp_path, "eje").path.endswith("eje-intent.md")
    assert dr.find_intent(tmp_path, "placa").sections == {"fits": "- none needed"}
    assert dr.find_intent(tmp_path, "otra") == dr.Intent()  # two files, none named: not guessed
    (drawings / "placa-intent.md").unlink()
    assert dr.find_intent(tmp_path, "otra").path.endswith("eje-intent.md")  # the only one there is
    assert dr.find_intent(tmp_path / "nada", "x") == dr.Intent()


# --- through the tools: the listing, the questions, the gate -------------------------------------------------------------------


def session(settings: Settings, facts: dict[str, int], mode: str = "own") -> tuple[Caller, DrawingFake]:
    fake = DrawingFake()
    fake.facts = facts
    return drawing_session(settings, adapter=fake, mode=mode)


def test_create_lists_what_the_sheet_must_carry_and_the_questions_before_drawing(settings: Settings) -> None:
    fake = DrawingFake()
    fake.facts = SHAFT
    call, _ = drawing_session(settings, adapter=fake, mode="own")
    text = call.ok("drawing", action="create")
    assert "this sheet must carry" in text and "req.thread" in text and "ISO 6410" in text and "[MISSING]" in text
    assert "ask, one at a time" in text and "to close a row: ask what it lacks BEFORE you draw it" in text
    assert "¿Qué rosca lleva (designación, paso, clase de tolerancia)" in text and "Propuesta: M<tamaño>" in text  # the engineer's language
    assert "intent file" in text and "met: where on the sheet" in text


def test_requirements_lists_them_again_and_says_where_the_intent_file_was_read(settings: Settings) -> None:
    (settings.workspace / "drawings").mkdir()
    (settings.workspace / "drawings" / "plate-intent.md").write_text(INTENT_ES, encoding="utf-8")
    call, fake = session(settings, PLATE)
    fake.findings = (finding("notes_heat_treatment", "Falta la nota de tratamiento térmico"),)
    text = call.ok("drawing", action="requirements")
    assert "intent file" in text and "plate-intent.md" in text
    assert "el archivo de intención dice «Temple y revenido a 40 HRC.»" in text  # known: confirm it, do not ask it again
    assert "req.dimensions" in text and "ISO 129-1" in text


def test_a_cad_that_reports_no_facts_gets_no_rows_and_the_gate_is_what_it_was(settings: Settings) -> None:
    call, fake = session(settings, {})
    assert "this sheet must carry" not in call.ok("drawing", action="review")
    assert "exported pdf" in call.ok("export", action="pdf")
    assert "this CAD reports no facts" in call.ok("drawing", action="requirements")


def test_the_export_is_refused_while_an_active_row_is_open_and_goes_through_when_it_is_closed(settings: Settings) -> None:
    call, fake = session(settings, PLATE)  # nothing on the sheet about surface texture, quantity and process
    refused = call.err("export", action="pdf")
    assert "error: drawing_unfinished" in refused and "requirements the sheet must carry" in refused
    assert "req.surface_texture" in refused and "req.quantity_process" in refused
    assert "req.edge_state" not in refused  # no finding about it: it is met
    assert fake.exported == []
    call.ok("drawing", action="answer", id="req.surface_texture", text="met: Ra 3,2 general en el rótulo")
    still = call.err("export", action="pdf")
    assert "req.quantity_process" in still and "req.surface_texture" not in still
    call.ok("drawing", action="answer", id="req.quantity_process", text="met: nota 3, cantidad 10 piezas, corte láser")
    assert "exported pdf" in call.ok("export", action="pdf")
    assert fake.exported


def test_a_row_the_sheet_does_not_carry_blocks_the_export_with_its_question(settings: Settings) -> None:
    call, fake = session(settings, SHAFT)
    refused = call.err("export", action="pdf")
    for row_id in ("req.thread", "req.fit_class", "req.turned_part", "req.keyway"):
        assert row_id in refused, row_id
    assert "lacking: a printed thread designation" in refused and "[MISSING] req.keyway" in refused
    assert fake.exported == []


def test_a_measured_row_is_not_dismissed_on_the_agents_word_but_an_engineers_order_closes_it_and_he_reads_it(settings: Settings) -> None:
    call, fake = session(settings, SHAFT)
    text = call.err("drawing", action="answer", id="req.thread", text="no aplica: el eje no lleva rosca")
    assert "measured_not_dismissable" in text and "1 threaded holes" in text and "by=<his name>" in text
    assert call.err("drawing", action="answer", id="req.thread", text="no aplica: x y z", by="Ana").count("by (his name) and reason") == 1
    done = call.ok(
        "drawing", action="answer", id="req.thread", text="no aplica: la rosca la hace el cliente", by="Ana", reason="Ana: la rosca la mecaniza el cliente, quítala del plano"
    )
    assert "requirement" in done
    answer = fake.drawings["Drawing"]["answers"]["req.thread"]
    assert answer.ordered_by == "Ana" and "«Ana: la rosca la mecaniza el cliente, quítala del plano»" in answer.text
    review = call.ok("drawing", action="review")
    assert "req.thread: DOES NOT APPLY: la rosca la hace el cliente" in review and "(ordered by Ana)" in review
    assert "[answered] req.thread" in review and "answered: DOES NOT APPLY" in review


def test_a_row_that_is_not_measured_can_be_dismissed_with_a_reason(settings: Settings) -> None:
    call, _ = session(settings, SHAFT)
    call.ok("drawing", action="answer", id="req.keyway", text="no aplica: el chavetero lo hace el montador en obra")
    review = call.ok("drawing", action="review")
    assert "[answered] req.keyway" in review and "[NEEDS AN ANSWER] req.keyway" not in review


def test_an_answer_for_a_row_that_does_not_apply_here_is_refused_and_lists_the_ones_that_do(settings: Settings) -> None:
    call, _ = session(settings, PLATE)
    text = call.err("drawing", action="answer", id="req.welds", text="met: no weld here at all")
    assert "unknown_entry" in text and "req.dimensions" in text and "req.welds" in text


def test_a_measured_finding_is_not_dismissed_on_the_agents_word_either(settings: Settings) -> None:
    fake = DrawingFake()
    fake.facts, fake.findings = {}, (NOT_LOCATED,)  # no facts: the finding stands on its own, as before
    call, _ = drawing_session(settings, adapter=fake, mode="own")
    text = call.err("drawing", action="answer", id="hole_not_located:@20,20,8", text="no aplica: es de paso")
    assert "measured_not_dismissable" in text and "Agujero Ø6" in text
    call.ok("drawing", action="answer", id="hole_not_located:@20,20,8", text="no aplica: es de paso", by="Ana", reason="Ana: no se ubica, la ubica el montaje")
    assert "(ordered by Ana)" in call.ok("drawing", action="review")
    assert "exported pdf" in call.ok("export", action="pdf")  # his order closed the measurement


def test_a_judgement_finding_stays_answerable(settings: Settings) -> None:
    fake = DrawingFake()
    fake.findings = (finding("collision", "a note over a dimension"),)
    call, _ = drawing_session(settings, adapter=fake, mode="own")
    call.ok("drawing", action="answer", id="collision", text="no aplica: no hay sitio libre en la vista")


def test_the_findings_a_row_takes_in_are_listed_under_it_and_closed_with_it(settings: Settings) -> None:
    call, fake = session(settings, {**PLATE, "surface_texture_on_sheet": 1, "quantity_on_sheet": 1, "process_on_sheet": 1})
    fake.findings = (finding("notes_edges", "Falta la nota de aristas"), finding("notes_coating", "Falta la nota de recubrimiento"))
    review = call.ok("drawing", action="review")
    assert "notes_edges (row req.edge_state)" in review and "[MISSING] req.edge_state" in review and "[MISSING] req.coating" in review
    refused = call.err("export", action="pdf")
    assert refused.count("req.edge_state") >= 1 and "- notes_edges" not in refused  # not listed twice: under its row
    call.ok("drawing", action="answer", id="req.edge_state", text="met: nota 2 de la hoja")
    call.ok("drawing", action="answer", id="req.coating", text="no aplica: se entrega sin recubrimiento, lo dice el cliente")
    call.ok("drawing", action="answer", id="req.dimensions", text="met: todas las posiciones en datum_dims")
    for row_id in ("req.heat_treatment", "req.marking"):
        call.ok("drawing", action="answer", id=row_id, text="no aplica: el cliente no lo pide en este plano")
    review = call.ok("drawing", action="review")
    assert "export of this sheet is open" in review, review


def test_mode_none_asks_nothing(settings: Settings) -> None:
    call, _ = session(settings, SHAFT, mode="none")
    assert "this sheet must carry" not in call.ok("drawing", action="review")
    assert "exported pdf" in call.ok("export", action="pdf")


def test_symmetry_is_stated_not_inferred_and_reaches_the_cad(settings: Settings) -> None:
    call, fake = session(settings, PLATE)
    call.ok("drawing", action="create", symmetry=True)
    assert fake.created[-1].symmetry is True
    call.ok("drawing", action="update", symmetry=False)
    assert fake.annotated[-1].symmetry is False
    call.ok("drawing", action="create")
    assert fake.created[-1].symmetry is None  # a rebuild that says nothing leaves the sheet as it was


def test_the_guide_points_at_the_list_instead_of_repeating_it() -> None:
    from cotaforge_mcp import guide_drawing

    for lines in (guide_drawing.QUALITY_LINES, guide_drawing.DONE_LINES):
        text = "\n".join(lines)
        assert "drawing(action=requirements)" in text
    assert "REQUIREMENTS:" in "\n".join(guide_drawing.DONE_LINES) and "reason=" in "\n".join(guide_drawing.DONE_LINES)


@pytest.mark.parametrize("row", dr.ROWS, ids=lambda r: r.id)
def test_every_row_prints_its_standard_what_it_carries_and_a_question(row: dr.Row) -> None:
    req = dr.Requirement(row, dr.MISSING, "why", ("something",), True, (), "", None, False, False)
    text = "\n".join(dr.line(req, "es"))
    assert row.id in text and row.must[1] in text and row.question[0] in text
    if row.standard:
        assert row.standard in text
