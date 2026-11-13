# ruff: noqa: E501
"""The sheet in one order (``drawing_flow: sheet``): plan, compose, and the option that leaves the steps flow as it was."""

import re
from typing import Any

from cotaforge_mcp import design_memory, drawing_compose
from cotaforge_mcp import drawing_requirements as dr
from cotaforge_mcp.config import Settings
from cotaforge_mcp.guide_drawing import INDEX_LINE, WINDOW_LINE, drawing_text
from cotaforge_mcp.schemas import TOOLS_BY_NAME, input_schema, tool_description
from tests.conftest import Caller, DrawingFake
from tests.unit.test_drawing_auto import INTENT, bare_session, items_of, shaft_session
from tests.unit.test_drawing_requirements import PLATE, finding


def choose_sheet_flow(settings: Settings) -> None:
    design_memory.save_brief(settings, None, None, None, None, None, None, "sheet")


def sheet_shaft(settings: Settings) -> tuple[Caller, DrawingFake]:
    choose_sheet_flow(settings)
    return shaft_session(settings)


def na_all(call: Caller, keep: tuple[str, ...] = ()) -> dict[str, str]:
    """An answer "no aplica" for every row the plan still asks (the test is about something else than the rows)."""
    plan = call.ok("drawing", action="plan").split("decisions it needs")[-1]
    return {r: "no aplica: no es de esta pieza" for r in re.findall(r"\((req\.[a-z_]+)\)", plan) if r not in keep}


def composed(call: Caller, decisions: dict[str, Any]) -> str:
    """A compose by the engineer's order: the rows it answers "no aplica" are measured ones."""
    return call.ok("drawing", action="compose", decisions=decisions, by="Ana", reason="Ana: no aplica a esta pieza")


GDT = {"char": "perpendicularity", "value": 0.02, "datums": ["A"], "face": "face:3"}


# --- the option: with steps nothing changes ----------------------------------------------------------------------------------------


def test_steps_is_the_default_and_the_tools_do_not_know_plan_or_compose(settings: Settings) -> None:
    call, fake = shaft_session(settings)
    session = call.session
    assert design_memory.load_brief(settings).drawing_flow == "steps"
    drawing = TOOLS_BY_NAME["drawing"]
    schema = input_schema(drawing, session)
    assert "plan" not in schema["properties"]["action"]["enum"] and "compose" not in schema["properties"]["action"]["enum"]
    assert "decisions" not in schema["properties"]
    assert "drawing_flow" not in input_schema(TOOLS_BY_NAME["memory"], session)["properties"]
    assert tool_description(drawing, session) == drawing.description  # the text of always
    refused = call.err("drawing", action="plan")
    assert "action" in refused and "plan" in refused
    assert "drawing_flow" not in call.ok("memory", action="brief")


def test_steps_keeps_the_guide_and_the_next_line_of_create_as_they_were(settings: Settings) -> None:
    call, fake = shaft_session(settings)
    text = call.ok("drawing", action="create")
    assert 'drawing(action="add", items=[...]) a few at a time' in text and "plan" not in text.split("next:")[-1]
    guide = call.ok("cad_guide", topic="drawing")
    assert WINDOW_LINE in guide and INDEX_LINE in guide and "compose" not in guide
    assert guide == drawing_text(call.session.capabilities.tools)
    assert "only in a project that builds sheets in one order" in call.ok("cad_guide", topic="drawing:compose")


def test_the_engineer_chooses_the_flow_in_the_brief_and_the_tools_follow(settings: Settings) -> None:
    call, fake = shaft_session(settings)
    assert "drawing_flow" not in call.ok("memory", action="brief")
    call.ok("memory", action="brief", drawing_flow="sheet")  # hidden from the schema of a steps project, accepted all the same
    assert design_memory.load_brief(settings).drawing_flow == "sheet"
    assert "drawing flow sheet" in call.ok("memory", action="brief")
    drawing = TOOLS_BY_NAME["drawing"]
    schema = input_schema(drawing, call.session)
    assert {"plan", "compose"} <= set(schema["properties"]["action"]["enum"]) and "decisions" in schema["properties"]
    assert "compose(decisions)" in tool_description(drawing, call.session)
    assert "drawing_flow" in input_schema(TOOLS_BY_NAME["memory"], call.session)["properties"]
    assert "drawing_flow: sheet" in (settings.workspace / "brief.yaml").read_text(encoding="utf-8")
    text = call.ok("drawing", action="create")
    assert 'drawing(action="plan")' in text.split("next:")[-1]
    guide = call.ok("cad_guide", topic="drawing")
    assert "ONE ORDER" in guide and "drawing:compose" in guide and WINDOW_LINE not in guide
    assert "COMPOSE" in call.ok("cad_guide", topic="drawing:compose")
    call.ok("memory", action="brief", drawing_flow="steps")
    assert "drawing_flow" not in (settings.workspace / "brief.yaml").read_text(encoding="utf-8")


def test_an_unknown_flow_in_the_brief_reads_as_steps(settings: Settings) -> None:
    (settings.workspace / "brief.yaml").write_text("drawing_flow: both\n", encoding="utf-8")
    assert design_memory.load_brief(settings).drawing_flow == "steps"


# --- plan --------------------------------------------------------------------------------------------------------------------------


def test_plan_makes_the_bare_sheet_adds_nothing_and_lists_what_it_derives_and_asks(settings: Settings) -> None:
    call, fake = sheet_shaft(settings)
    text = call.ok("drawing", action="plan")
    assert len(fake.created) == 1 and not fake.annotated  # views and title block; no annotation
    assert items_of(fake).keys() == {"o1"}
    assert "rq_callouts: a callout of every hole" in text.split("decisions it needs")[0]
    assert "req.surface_texture" in text and "[finish]" in text
    assert "wait for his go-ahead" in text
    assert "req.thread" not in text.split("decisions it needs")[1]  # the harness meets it
    call.ok("drawing", action="plan")
    assert len(fake.created) == 1  # an existing sheet is not made again


def test_plan_asks_where_the_holes_are_counted_from_when_the_sheet_has_no_reference(settings: Settings) -> None:
    choose_sheet_flow(settings)
    fake = DrawingFake()
    fake.facts = PLATE
    fake.findings = (finding("hole_not_located", "no reference along x"),)
    call = bare_session(settings, fake)
    text = call.ok("drawing", action="plan")
    assert "[datums|relations|sheet]" in text and "posiciones de los agujeros" in text


def test_plan_uses_the_intent_file_and_asks_only_what_it_does_not_say(settings: Settings) -> None:
    choose_sheet_flow(settings)
    (settings.workspace / "drawings").mkdir()
    (settings.workspace / "drawings" / "plate-intent.md").write_text(INTENT, encoding="utf-8")
    fake = DrawingFake()
    fake.facts = PLATE
    fake.findings = (finding("notes_heat_treatment", "Falta"), finding("notes_coating", "Falta"), finding("notes_edges", "Falta"))
    call = bare_session(settings, fake)
    text = call.ok("drawing", action="plan")
    derived, asked = text.split("decisions it needs")
    assert "rq_heat_treatment" in derived and "rq_coating" in derived and "req.edge_state" in asked
    assert "req.heat_treatment" not in asked


# --- compose -----------------------------------------------------------------------------------------------------------------------


def test_compose_refuses_with_the_missing_decisions_and_changes_nothing(settings: Settings) -> None:
    call, fake = sheet_shaft(settings)
    refused = call.err("drawing", action="compose", decisions={"notes": ["Romper aristas vivas"]})
    assert "decisions missing, nothing was changed" in refused and "req.surface_texture" in refused
    assert not fake.annotated and items_of(fake).keys() == {"o1"}


def test_compose_puts_the_decisions_and_what_it_derives_in_one_order(settings: Settings) -> None:
    call, fake = sheet_shaft(settings)
    decided = ("req.surface_texture", "req.geometric_tolerance", "req.edge_state")
    text = composed(
        call,
        {
            "datums": [{"letter": "A", "face": "face:3"}],
            "gdt": [GDT],
            "finish": [{"general": True, "ra": 3.2}],
            "notes": ["Romper aristas vivas 0,5 x 45"],
            "answers": na_all(call, keep=decided),
        },
    )
    items = items_of(fake)
    assert {"dc_datums1", "dc_gdt1", "dc_finish1", "dc_notes1", "rq_callouts"} <= items.keys()
    assert items["dc_datums1"]["type"] == "datum" and items["dc_notes1"]["text"] == "Romper aristas vivas 0,5 x 45"
    assert "composed sheet" in text and "derived: rq_callouts" in text and "4 from your decisions" in text
    adds = [r for r in fake.annotated if r.action == "add"]
    assert len(adds) == 1 and [i["id"] for i in adds[0].items][-1] == "rq_callouts"  # everything in one call
    assert "req.quantity_process" in fake.drawings["Drawing"]["answers"]


def test_a_second_compose_replaces_what_the_first_put_and_keeps_what_the_hand_added(settings: Settings) -> None:
    call, fake = sheet_shaft(settings)
    everything = {"answers": na_all(call)}
    composed(call, {**everything, "notes": ["uno"]})
    call.ok("drawing", action="add", items=[{"type": "note", "id": "mine", "text": "a mano"}])
    composed(call, {**everything, "notes": ["dos", "tres"]})
    items = items_of(fake)
    assert items["dc_notes1"]["text"] == "dos" and items["dc_notes2"]["text"] == "tres"
    assert items["mine"]["text"] == "a mano" and sum(i.startswith("dc_notes") for i in items) == 2
    assert sum(i == "rq_callouts" for i in items) == 1


def test_a_chosen_relation_takes_the_default_location_off_so_nothing_is_dimensioned_twice(settings: Settings) -> None:
    choose_sheet_flow(settings)
    fake = DrawingFake()
    fake.facts = PLATE
    call = bare_session(settings, fake)
    call.ok("drawing", action="create")
    call.ok("drawing", action="add", items=[{"type": "datum_dims", "id": "loc", "auto": True}])
    composed(
        call,
        {"relations": [{"type": "datum_dims", "x_from": "face:3", "y_from": "face:5", "holes": "all"}], "answers": na_all(call)},
    )
    items = items_of(fake)
    assert "loc" not in items and items["dc_relations1"]["type"] == "datum_dims"



def test_a_decision_that_is_not_valid_refuses_the_whole_order(settings: Settings) -> None:
    call, fake = sheet_shaft(settings)
    for decisions, said in (
        ({"colour": []}, "unknown decision"),
        ({"gdt": [{"type": "note", "text": "x"}]}, "is not one of gdt"),
        ({"notes": [{"text": "x", "id": "mine"}]}, "leave the id out"),
        ({"gdt": [{"char": "flatness"}]}, "value"),
        ({"answers": {"req.nothing": "no aplica: porque si"}}, "not a row"),
        ({"answers": {"req.surface_texture": "ok"}}, ""),
    ):
        refused = call.err("drawing", action="compose", decisions=decisions)
        assert said in refused
    assert not fake.annotated


def test_a_measured_row_is_not_dismissed_on_the_agents_word(settings: Settings) -> None:
    call, fake = sheet_shaft(settings)
    refused = call.err("drawing", action="compose", decisions={"answers": {"req.thread": "no aplica: no lleva rosca"}})
    assert "only on the engineer's order" in refused and not fake.annotated


def test_a_plate_with_through_holes_is_made_in_one_view_and_gets_its_axes_without_asking(settings: Settings) -> None:
    choose_sheet_flow(settings)
    fake = DrawingFake()
    fake.facts = PLATE
    fake.findings = (finding("single_view_enough", "one view says it all", blocking=False), finding("no_view_lines", "no axes"))
    call = bare_session(settings, fake)
    text = call.ok("drawing", action="plan")
    assert [c.views for c in fake.created] == [None, ("top", "iso")] and fake.created[-1].fresh  # made again, nothing on it yet
    assert "rq_view_lines" in text.split("decisions it needs")[0]
    composed(call, {"sheet": {"symmetry": True}, "answers": na_all(call)})
    assert items_of(fake)["rq_view_lines"]["type"] == "view_lines"
    made = len(fake.created)  # the sheet's symmetry rebuilt it once more
    call.ok("drawing", action="plan")
    assert len(fake.created) == made  # the sheet exists now: plan does not make it again


def test_a_view_the_engineer_chose_is_not_replaced_by_the_single_view_rule(settings: Settings) -> None:
    choose_sheet_flow(settings)
    fake = DrawingFake()
    fake.facts = PLATE
    fake.findings = (finding("single_view_enough", "one view says it all", blocking=False),)
    call = bare_session(settings, fake)
    call.ok("drawing", action="plan", views=["front", "top"])
    assert len(fake.created) == 1


# --- the paper ---------------------------------------------------------------------------------------------------------------------


def test_the_composer_raises_a_paper_nobody_fixed_when_the_annotations_do_not_fit_and_says_so(settings: Settings) -> None:
    call, fake = sheet_shaft(settings)
    fake.findings = (*fake.findings, finding("views_overlap", "two views overlap"))
    text = composed(call, {"answers": na_all(call)})
    assert "paper raised from A4 to A3" in text
    assert [c.template for c in fake.created][-1] in ("A3", "A2")


def test_a_paper_the_engineer_fixed_is_never_raised(settings: Settings) -> None:
    call, fake = sheet_shaft(settings)
    fake.findings = (*fake.findings, finding("views_overlap", "two views overlap"))
    text = composed(call, {"sheet": {"template": "A4"}, "answers": na_all(call)})
    assert "paper raised" not in text and {c.template for c in fake.created} == {"A4"}


def test_compose_needs_the_cad_to_draw(settings: Settings) -> None:
    assert drawing_compose.KEYS and set(drawing_compose.ROW_KEYS) <= set(dr.ROW_IDS)
