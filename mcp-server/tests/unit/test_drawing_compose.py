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


def composed(call: Caller, decisions: dict[str, Any], **more: Any) -> str:
    """A compose by the engineer's order: the rows it answers "no aplica" are measured ones."""
    return call.ok("drawing", action="compose", decisions=decisions, by="Ana", reason="Ana: no aplica a esta pieza", **more)


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
    composed(call, {**everything, "notes": ["dos", "tres"]}, recompose=True)  # the engineer's order
    items = items_of(fake)
    assert items["dc_notes1"]["text"] == "dos" and items["dc_notes2"]["text"] == "tres"
    assert items["mine"]["text"] == "a mano" and sum(i.startswith("dc_notes") for i in items) == 2
    assert sum(i == "rq_callouts" for i in items) == 1


# --- the hybrid: one order for the first proposal, then local fixes --------------------------------------------------------------


def keeping_items_on_rebuild(fake: DrawingFake) -> None:
    """The CAD keeps the items of a sheet it builds again (the fake starts a sheet from its overall dimension)."""
    make = fake.create_drawing

    def create(request: Any) -> Any:
        kept = list(fake.drawings.get("Drawing", {}).get("items", ()))
        result = make(request)
        if kept:
            fake.drawings["Drawing"]["items"] = kept
        return result

    fake.create_drawing = create  # type: ignore[method-assign]


def composed_once(settings: Settings) -> tuple[Caller, DrawingFake, dict[str, Any]]:
    call, fake = sheet_shaft(settings)
    keeping_items_on_rebuild(fake)
    everything = {"answers": na_all(call)}
    composed(call, {**everything, "notes": ["uno"]})
    return call, fake, everything


def test_the_compose_records_what_it_decided_of_the_whole_sheet(settings: Settings) -> None:
    call, fake = sheet_shaft(settings)
    composed(call, {"answers": na_all(call), "sheet": {"template": "A3", "title": "Eje"}, "datums": [{"letter": "A", "face": "face:3"}]})
    recorded = fake.drawings["Drawing"]["composed"]["signature"]
    assert recorded == {"template": "A3", "datums": [{"letter": "A", "face": "face:3"}]}  # the title is local, not recorded


def test_a_second_compose_that_changes_nothing_of_the_whole_sheet_is_refused_and_points_to_the_local_actions(settings: Settings) -> None:
    call, fake, everything = composed_once(settings)
    before = len(fake.annotated)
    refused = call.err("drawing", action="compose", decisions={**everything, "notes": ["dos"]}, by="Ana", reason="Ana: no aplica")
    assert "already composed" in refused and "nothing was changed" in refused and 'action="update"' in refused
    assert len(fake.annotated) == before and len(fake.created) == 1  # not a call to the CAD
    assert items_of(fake)["dc_notes1"]["text"] == "uno"


def test_a_second_compose_for_a_change_that_affects_the_whole_sheet_is_allowed(settings: Settings) -> None:
    call, fake, everything = composed_once(settings)
    text = composed(call, {**everything, "notes": ["dos"], "sheet": {"template": "A3"}})
    assert "composed sheet" in text and items_of(fake)["dc_notes1"]["text"] == "dos"
    assert fake.drawings["Drawing"]["composed"]["signature"]["template"] == "A3"
    call.err("drawing", action="compose", decisions={**everything, "notes": ["tres"], "sheet": {"template": "A3"}}, by="Ana", reason="Ana: no aplica")


def test_a_second_compose_with_another_dimension_scheme_is_allowed(settings: Settings) -> None:
    call, fake, everything = composed_once(settings)
    composed(call, {**everything, "relations": [{"type": "datum_dims", "x_from": "face:3", "y_from": "face:5", "holes": "all"}]})
    assert "dc_relations1" in items_of(fake)


def test_the_engineer_can_order_a_second_compose_and_it_needs_his_words(settings: Settings) -> None:
    call, fake, everything = composed_once(settings)
    lonely = call.err("drawing", action="compose", decisions={**everything, "notes": ["dos"]}, recompose=True)
    assert "engineer's order" in lonely and items_of(fake)["dc_notes1"]["text"] == "uno"
    composed(call, {**everything, "notes": ["dos"]}, recompose=True)
    assert items_of(fake)["dc_notes1"]["text"] == "dos"


def test_a_refused_first_compose_leaves_the_sheet_open_to_compose(settings: Settings) -> None:
    call, fake = sheet_shaft(settings)
    call.err("drawing", action="compose", decisions={"notes": ["x"]})  # decisions missing: nothing recorded
    assert "composed" not in fake.drawings["Drawing"]
    composed(call, {"answers": na_all(call), "notes": ["x"]})
    assert "composed" in fake.drawings["Drawing"]


def test_a_sheet_that_was_not_composed_in_one_order_has_no_guard(settings: Settings) -> None:
    call, fake = sheet_shaft(settings)
    call.ok("drawing", action="create")
    call.ok("drawing", action="add", items=[{"type": "note", "id": "dc_notes1", "text": "a mano"}])
    composed(call, {"answers": na_all(call), "notes": ["x"]})  # nothing recorded: it is the first compose
    assert items_of(fake)["dc_notes1"]["text"] == "x"


# --- update: one item in place ------------------------------------------------------------------------------------------------------


def test_update_with_items_edits_one_item_in_place_and_pins_it(settings: Settings) -> None:
    call, fake, everything = composed_once(settings)
    call.ok("drawing", action="add", items=[{"type": "note", "id": "mine", "text": "a mano"}])
    before = {k: dict(v) for k, v in items_of(fake).items()}
    sent = len(fake.annotated)
    text = call.ok("drawing", action="update", items=[{"id": "dc_notes1", "text": "uno, corregido"}])
    asked = fake.annotated[sent:]
    assert [r.action for r in asked] == ["edit"] and len(fake.created) == 1  # no layout, no create, no add/remove
    items = items_of(fake)
    assert items["dc_notes1"]["text"] == "uno, corregido"
    assert {k: v for k, v in items.items() if k != "dc_notes1"} == {k: v for k, v in before.items() if k != "dc_notes1"}
    assert "edited in place: dc_notes1" in text and "nothing else moved" in text
    assert fake.drawings["Drawing"]["composed"]["pinned"] == ["dc_notes1"]


def test_update_can_move_an_item_and_take_a_field_off(settings: Settings) -> None:
    call, fake, _ = composed_once(settings)
    call.ok("drawing", action="add", items=[{"type": "dim", "id": "d1", "kind": "distance", "side": "above", "offset_mm": 8}])
    call.ok("drawing", action="update", items=[{"id": "d1", "side": "below", "offset_mm": 15}])
    assert items_of(fake)["d1"]["side"] == "below" and items_of(fake)["d1"]["offset_mm"] == 15
    call.ok("drawing", action="update", items=[{"id": "d1", "offset_mm": None}])
    assert "offset_mm" not in items_of(fake)["d1"]


def test_an_edit_the_cad_refuses_for_a_collision_reports_it_and_changes_nothing(settings: Settings) -> None:
    call, fake, _ = composed_once(settings)
    call.ok("drawing", action="add", items=[{"type": "dim", "id": "d1", "kind": "distance", "offset_mm": 8}])
    fake.collides = {"d1"}
    refused = call.err("drawing", action="update", items=[{"id": "d1", "offset_mm": 1}])
    assert "would put d1 on d9" in refused and "nothing was changed" in refused
    assert items_of(fake)["d1"]["offset_mm"] == 8 and "pinned" not in fake.drawings["Drawing"].get("composed", {})


def test_update_names_the_items_it_does_not_find_and_asks_for_something_to_change(settings: Settings) -> None:
    call, fake, _ = composed_once(settings)
    assert "no item nope" in call.err("drawing", action="update", items=[{"id": "nope", "text": "x"}])
    assert "nothing to change" in call.err("drawing", action="update", items=[{"id": "dc_notes1"}])
    assert "valid dictionary" in call.err("drawing", action="update", items=["dc_notes1"])


def test_an_update_of_items_does_not_mix_with_the_title_block_or_the_layout_of_the_sheet(settings: Settings) -> None:
    call, fake, _ = composed_once(settings)
    refused = call.err("drawing", action="update", items=[{"id": "dc_notes1", "text": "x"}], title_block={"author": "Ana"})
    assert "two calls" in refused


def test_update_without_items_still_lays_the_views_out_again(settings: Settings) -> None:
    call, fake, _ = composed_once(settings)
    sent = len(fake.annotated)
    call.ok("drawing", action="update", title_block={"author": "Ana"})
    assert [r.action for r in fake.annotated[sent:]] == ["update"]


def test_a_recompose_keeps_what_was_edited_in_place_and_says_so(settings: Settings) -> None:
    call, fake, everything = composed_once(settings)
    call.ok("drawing", action="update", items=[{"id": "dc_notes1", "text": "uno, a mano"}])
    text = composed(call, {**everything, "notes": ["dos", "tres"], "sheet": {"template": "A3"}})
    items = items_of(fake)
    assert items["dc_notes1"]["text"] == "uno, a mano"  # pinned: not taken off, not put again
    assert items["dc_notes2"]["text"] == "tres" and sum(i.startswith("dc_notes") for i in items) == 2
    assert "kept as edited by hand" in text and "dc_notes1" in text
    assert fake.drawings["Drawing"]["composed"]["pinned"] == ["dc_notes1"]  # the pin survives the record


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


def bigger_paper_fits(fake: DrawingFake) -> None:
    """A CAD on which the views overlap on A4 and not on a bigger paper."""
    overlap = finding("views_overlap", "two views overlap")
    fake.findings = (*fake.findings, overlap)
    make = fake.create_drawing

    def create(request: Any) -> Any:
        result = make(request)
        fake.findings = tuple(f for f in fake.findings if f.code != "views_overlap")
        if request.template == "A4":
            fake.findings = (*fake.findings, overlap)
        return result

    fake.create_drawing = create  # type: ignore[method-assign]


def test_the_composer_raises_a_paper_nobody_fixed_when_the_annotations_do_not_fit_and_says_so(settings: Settings) -> None:
    call, fake = sheet_shaft(settings)
    bigger_paper_fits(fake)
    text = composed(call, {"answers": na_all(call)})
    assert "paper raised from A4 to A3" in text and "1 layout finding(s) before, 0 after" in text
    assert [c.template for c in fake.created][-1] == "A3"


def test_a_bigger_paper_that_does_not_help_is_put_back(settings: Settings) -> None:
    call, fake = sheet_shaft(settings)
    fake.findings = (*fake.findings, finding("views_overlap", "two views overlap"))  # no paper makes it go
    text = composed(call, {"answers": na_all(call)})
    assert "tried and put back" in text and "paper raised" not in text
    assert [c.template for c in fake.created][-2:] == ["A3", "A4"]


def test_a_paper_the_engineer_fixed_is_never_raised(settings: Settings) -> None:
    call, fake = sheet_shaft(settings)
    fake.findings = (*fake.findings, finding("views_overlap", "two views overlap"))
    text = composed(call, {"sheet": {"template": "A4"}, "answers": na_all(call)})
    assert "paper raised" not in text and {c.template for c in fake.created} == {"A4"}


def test_compose_needs_the_cad_to_draw(settings: Settings) -> None:
    assert drawing_compose.KEYS and set(drawing_compose.ROW_KEYS) <= set(dr.ROW_IDS)


# --- the global layout ------------------------------------------------------------------------------------------------------------


def test_compose_asks_for_the_global_layout_once_after_everything_is_put(settings: Settings) -> None:
    call, fake = sheet_shaft(settings)
    fake.layout_report = {"layout_placed": ("dc_rel1",), "layout_unplaced": (), "layout_fits": True}
    text = composed(call, {"answers": na_all(call)})
    actions = [r.action for r in fake.annotated]
    assert actions.count("layout") == 1 and actions.index("layout") == actions.index("add") + 1  # right after the last add
    assert "layout: 1 dimension(s) placed by the global planner" in text and "NOT PLACED" not in text


def test_what_the_layout_cannot_place_is_in_the_reply_never_dropped(settings: Settings) -> None:
    call, fake = sheet_shaft(settings)
    fake.layout_report = {"layout_unplaced": ("d7: no free place: text over text (6 candidates)",), "layout_fits": True}
    text = composed(call, {"answers": na_all(call)})
    assert "NOT PLACED d7: no free place" in text and "side or offset_mm" in text


def test_a_sheet_the_cad_cannot_plan_keeps_the_placement_of_every_add_and_says_so(settings: Settings) -> None:
    call, fake = sheet_shaft(settings)
    fake.layout_fails = "the worker has no layout"
    text = composed(call, {"answers": na_all(call)})
    assert "composed sheet" in text and "the CAD could not plan the sheet (the worker has no layout)" in text


def test_views_that_do_not_fit_make_the_composer_raise_the_paper_and_plan_again(settings: Settings) -> None:
    call, fake = sheet_shaft(settings)
    answers = {"layout_fits": False, "layout_needed": (400.0, 250.0)}
    fake.layout_report = answers
    make = fake.create_drawing

    def create(request: Any) -> Any:
        if request.template == "A3":  # on the bigger paper the planner finds room
            fake.layout_report = {"layout_fits": True}
        return make(request)

    fake.create_drawing = create  # type: ignore[method-assign]
    text = composed(call, {"answers": na_all(call)})
    assert "paper raised from A4 to A3" in text and "1 layout finding(s) before, 0 after" in text
    assert [r.action for r in fake.annotated].count("layout") == 2  # once on A4, once more on A3


def test_a_paper_the_engineer_fixed_is_not_raised_when_the_views_do_not_fit(settings: Settings) -> None:
    call, fake = sheet_shaft(settings)
    fake.layout_report = {"layout_fits": False, "layout_needed": (400.0, 250.0)}
    text = composed(call, {"sheet": {"template": "A4"}, "answers": na_all(call)})
    assert "paper raised" not in text and "need 400 x 250 mm and do not fit the paper" in text


def test_an_external_thread_is_derived_as_its_own_callout_not_asked_as_a_fit(settings: Settings) -> None:
    call, fake = sheet_shaft(settings)
    fake.facts = {**fake.facts, "thread_features": 1}
    plan = call.ok("drawing", action="plan")
    assert "rq_threads: a callout (size, pitch, usable length) of every external thread" in plan
    composed(call, {"answers": na_all(call)})
    assert items_of(fake)["rq_threads"]["type"] == "thread_dims"
