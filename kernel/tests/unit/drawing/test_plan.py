# ruff: noqa: E501
"""The sheet context and the per-step plan: what this drawing is, which catalog rules apply to it at each step and
what the page still lacks. Whatever cannot be decided is a question, never a guess."""

from cotaforge.drawing import catalog, context, plan

SHAFT_FACTS = {"sheet": 1, "part": 1, "turned": 1, "keyways": 1, "thread_features": 1, "chamfers": 2, "bosses": 4}
SHAFT_INTENT = {
    "function": "Eje de un transportador. Gira sobre dos rodamientos 6205.",
    "fits": "Asientos de rodamiento Ø25 k6. Rosca M20x1,5 - 6g. Chavetero ancho 6 P9.",
    "datums": "Datum A-B: el eje común de los dos asientos.",
    "process": "Torneado desde barra Ø35, rectificado de los asientos, fresado del chavetero, roscado.",
    "material": "Acero C45, barra laminada.",
    "relations": "Coaxialidad entre los dos asientos: Ø0,02 respecto del eje A-B.",
    "quantity": "10 unidades.",
}
PLATE_FACTS = {"sheet": 1, "part": 1, "holes": 11, "threaded_holes": 6, "fit_holes": 1, "hole_groups": 1, "circular_patterns": 1}
ASSEMBLY_FACTS = {"sheet": 1, "assembly": 1, "sections": 1, "quantity_on_sheet": 1}


def ids(step: plan.Step) -> set[str]:
    return {s.rule.id for s in step.rules}


def step(made: plan.Plan, key: str) -> plan.Step:
    return next(s for s in made.steps if s.key == key)


# --- the context ------------------------------------------------------------------------------------------------------


def test_a_turned_shaft_is_a_part_with_its_processes_and_features_and_nothing_to_ask() -> None:
    found = context.build(SHAFT_FACTS, SHAFT_INTENT)
    assert found.sheet_type == "part"
    assert found.processes == ("turning", "milling", "grinding", "machining")
    assert {"revolution_body", "keyway", "thread", "fit", "bearing_seat", "chamfer", "datum", "geometric_tolerance", "functional_relation"} <= set(found.features)
    assert "hole" not in found.features and "circular_pattern" not in found.features
    assert found.material.startswith("Acero C45") and found.quantity == "10 unidades."
    assert found.questions == ()


def test_what_nobody_declared_is_a_question_at_its_step_never_a_guess() -> None:
    found = context.build(SHAFT_FACTS, {})
    assert found.processes == () and found.material == "" and found.quantity == ""
    asked = {(q.step, q.about) for q in found.questions}
    assert asked == {
        ("purpose", "process"),
        ("purpose", "material"),
        ("purpose", "quantity"),
        ("references", "datums"),
        ("dimensions", "functional relations"),
        ("tolerances", "fits"),
    }
    assert "bearing_seat" not in found.features and "fit" not in found.features  # not declared: not assumed


def test_the_title_block_and_the_sheet_answer_material_and_quantity_too() -> None:
    found = context.build({**SHAFT_FACTS, "quantity_on_sheet": 1}, {"process": "torneado"}, "C45")
    assert found.material == "C45" and not [q for q in found.questions if q.about in ("material", "quantity", "process")]


def test_a_process_the_harness_does_not_know_and_a_section_that_says_nothing_are_asked() -> None:
    odd = context.build(SHAFT_FACTS, {**SHAFT_INTENT, "process": "sinterizado selectivo"})
    assert [q.about for q in odd.questions] == ["process"]
    none = context.build(SHAFT_FACTS, {**SHAFT_INTENT, "process": "- No aplica."})
    assert [q.about for q in none.questions] == ["process"] and none.processes == ()


def test_the_sheet_type_is_what_the_cad_measured() -> None:
    assert context.build(ASSEMBLY_FACTS, {}).sheet_type == "assembly"
    assert context.build({"sheet": 1, "part": 1, "weldment": 1}, {}).sheet_type == "welded"
    unknown = context.build({}, {})
    assert unknown.sheet_type == "" and unknown.questions[0].about == "sheet type"


def test_a_laser_cut_plate_has_its_process_and_its_hole_features() -> None:
    found = context.build(PLATE_FACTS, {"process": "Corte por láser de placa laminada, taladrado; roscado de los M8."})
    assert found.processes == ("laser_plasma_water", "machining")
    assert {"hole", "hole_group", "circular_pattern", "thread", "fit"} <= set(found.features)


def test_a_word_inside_another_word_names_no_process() -> None:
    assert "casting" not in context.processes_in("Profundidad del taladro 12 mm; la profundidad de la rosca es 18.")
    assert "casting" not in context.processes_in("la profundidad del chavetero")
    assert "casting" in context.processes_in("Fundición gris, pieza fundida") and "casting" in context.processes_in("sand casting")


def test_a_manual_weld_is_welding_and_no_hand_tool_work() -> None:
    assert context.processes_in("soldadura manual") == ("welding",)
    assert context.processes_in("Soldadura manual con electrodo revestido") == ("welding",)
    assert context.processes_in("manual welding") == ("welding",)
    assert "hand_tools" in context.processes_in("acabado manual de las aristas")  # a manual finish stays hand work
    assert "hand_tools" in context.processes_in("soldadura manual y limado de los cordones")  # the file is the hand work here


def test_a_hand_weld_is_never_hand_tool_work_however_it_is_worded_or_spaced() -> None:
    for text in ("soldadura a mano", "soldadura MIG manual", "soldadura eléctrica manual", "Soldadura  manual", "Soldadura\tMAG  a  mano"):
        assert context.processes_in(text) == ("welding",), text
    assert "hand_tools" in context.processes_in("soldadura y limado a mano")  # a conjunction ends the weld phrase
    assert "hand_tools" in context.processes_in("herramienta manual")


def test_double_is_not_bending() -> None:
    assert "sheet_metal_bending" not in context.processes_in("doble chaflán")
    assert "sheet_metal_bending" not in context.processes_in("hilera doble")
    assert "sheet_metal_bending" not in context.processes_in("Doble  hilera de agujeros")
    for text in ("doblado de la chapa", "plegado", "doblez a 90", "dobladora"):
        assert "sheet_metal_bending" in context.processes_in(text), text


def test_a_thread_is_not_a_wire_cut_but_cutting_by_wire_is() -> None:
    assert "wire_edm" not in context.processes_in("hilo M8")
    assert "wire_edm" not in context.processes_in("hilo de la rosca")
    for text in ("corte por hilo", "electroerosión por hilo", "Corte  por  hilo", "wire cut", "wire-EDM"):
        assert "wire_edm" in context.processes_in(text), text


def test_the_design_facts_of_the_plan_are_named() -> None:
    assert set(context.DESIGN_KEYS) == {"process", "fits", "relations"} and set(context.DESIGN_KEYS) <= set(context.QUESTION_KEYS)


# --- the plan ---------------------------------------------------------------------------------------------------------


def test_the_plan_has_the_eight_steps_and_each_carries_only_the_rules_of_this_part() -> None:
    made = plan.build(context.build(SHAFT_FACTS, SHAFT_INTENT), catalog.load())
    assert [s.key for s in made.steps] == ["purpose", "views", "references", "dimensions", "tolerances", "notes", "deliverables", "review"]
    assert {"views.turned_axial_section", "views.centre_lines"} <= ids(step(made, "views"))
    assert {"keyway.dimensioned", "thread.designation", "turned.diameters", "process.stage_sizes"} <= ids(step(made, "dimensions"))
    assert "gdt.from_function" in ids(step(made, "tolerances"))
    every = set().union(*(ids(s) for s in made.steps))
    assert not {"circular_pattern.polar", "hole_group.spacing_from_first", "welded.weld_symbols", "assembly.parts_list_and_balloons", "deliverables.cut_dxf", "bend.dimensioned"} & every
    assert {s.rule.id for s in made.layout} >= {"layout.nothing_on_anything", "layout.inside_frame"}


def test_a_laser_plate_gets_the_pattern_the_group_and_the_cut_dxf_and_not_the_shaft_rules() -> None:
    made = plan.build(context.build(PLATE_FACTS, {"process": "corte por láser"}), catalog.load())
    assert {"circular_pattern.polar", "hole_group.spacing_from_first"} <= ids(step(made, "dimensions"))
    assert "deliverables.cut_dxf" in ids(step(made, "deliverables"))
    assert not {"keyway.dimensioned", "views.turned_axial_section", "turned.diameters"} & set().union(*(ids(s) for s in made.steps))


def test_an_assembly_gets_its_own_rules_and_method_and_none_of_a_part() -> None:
    made = plan.build(context.build(ASSEMBLY_FACTS, {"datums": "cara inferior", "relations": "holgura 2"}), catalog.load())
    every = set().union(*(ids(s) for s in made.steps))
    assert {"section.solid_parts_not_cut", "assembly.parts_list_and_balloons", "assembly.joint_fit", "welded.no_form_of_detailed_parts"} <= every
    assert not {"dimensions.size_and_position", "notes.heat_treatment", "surface.texture"} & every
    assert made.method is not None and "fitter" in made.method.reader
    assert "for each position pick the one view" in step(made, "notes").method
    text = "\n".join(plan.render(made, step=4))
    assert "No hole positions" in text and "this assembly sheet never carries" in text


def test_the_status_of_a_rule_is_what_the_page_says_and_the_current_step_is_the_first_with_something_open() -> None:
    found = context.build(SHAFT_FACTS, SHAFT_INTENT)
    made = plan.build(
        found,
        catalog.load(),
        open_codes={"cylinder_no_axis": 2, "dimension_side": 1, "collision": 1},
        rows={"req.keyway": plan.RowState(False, "the width's class"), "req.thread": plan.RowState(True)},
    )
    views = {s.rule.id: s for s in step(made, "views").rules}
    assert views["views.centre_lines"].state == "open" and "cylinder_no_axis x2" in views["views.centre_lines"].detail
    assert views["views.edge_form_detail" if "views.edge_form_detail" in views else "views.scale_per_view"].state == "unchecked"
    dims = {s.rule.id: s for s in step(made, "dimensions").rules}
    assert dims["keyway.dimensioned"].state == "open" and dims["thread.designation"].state == "clear"
    assert dims["dimensions.side_and_arrows"].state == "open" and not dims["dimensions.side_and_arrows"].holds  # it only warns
    assert made.current.key == "views"
    line = made.line()
    assert line.startswith("step 2/8 views: ") and "views.centre_lines (cylinder_no_axis x2)" in line and "layout: nothing_on_anything open" in line
    later = plan.build(found, catalog.load(), rows={"req.keyway": plan.RowState(False, "the width's class")})
    assert later.current.key == "dimensions" and "req.keyway: the width's class" in later.line()


def test_an_open_question_holds_its_step_and_the_review_is_the_last_one() -> None:
    made = plan.build(context.build(SHAFT_FACTS, {**SHAFT_INTENT, "process": ""}), catalog.load())
    assert made.current.key == "purpose" and "ask plan.process" in made.line()
    done = plan.build(context.build(SHAFT_FACTS, SHAFT_INTENT), catalog.load(), review_pending=("the fabricator's review",))
    assert done.current.key == "review" and "step 8/8" in done.line() and "the fabricator's review" in done.line()
    clean = plan.build(context.build(SHAFT_FACTS, SHAFT_INTENT), catalog.load())
    assert "nothing open in the plan" in clean.line()


def test_the_plan_gives_the_current_step_in_full_and_one_line_for_each_other() -> None:
    made = plan.build(context.build(SHAFT_FACTS, SHAFT_INTENT), catalog.load(), rows={"req.keyway": plan.RowState(False)})
    lines = plan.render(made)
    text = "\n".join(lines)
    assert "sheet type: part" in lines[0] and "turning" in lines[0]
    assert "STEP 4 DIMENSIONS (1 open) <- YOU ARE HERE" in text and "Work in this order" in text
    assert "[OPEN] keyway.dimensioned" in text and "req.keyway needs an answer" in text
    # the other steps are ONE line each: how many rules, which are open; no rule of theirs is listed, not even the owner's
    assert sum(line.startswith("STEP ") for line in lines) == 8
    assert any(line.startswith("STEP 2 VIEWS (nothing open; ") and line.endswith(" rules)") for line in lines)
    assert "views.isometric" not in text and "drawing.purpose" not in text and "Start from the fewest views" not in text
    assert "Start from the fewest views" in "\n".join(plan.render(made, step=2))  # another step is asked for by number
    assert "STEP 4 DIMENSIONS (1 open: keyway.dimensioned" not in text  # the step in view is not summarised
    everything = "\n".join(plan.render(made, full=True))
    assert "  [ok] views.isometric (blocks the export)" in everything and "only thing the shop receives" in everything
    assert len(text) < 0.6 * len(everything) and len(text) < 12000, (len(text), len(everything))  # the full plan is on request only


def test_a_step_that_is_not_in_view_names_its_first_open_rules_in_its_line() -> None:
    made = plan.build(context.build(SHAFT_FACTS, SHAFT_INTENT), catalog.load(), open_codes={"cylinder_no_axis": 1, "no_view_lines": 1})
    lines = plan.render(made, step=4)
    line = next(line for line in lines if line.startswith("STEP 2 VIEWS"))
    assert "open: views.centre_lines" in line and "<- YOU ARE HERE" in line


def test_the_line_of_a_quick_read_says_it_is_the_step_checks_only() -> None:
    made = plan.build(context.build(SHAFT_FACTS, SHAFT_INTENT), catalog.load())
    assert "nothing open in the plan |" in made.line() and "step checks only" not in made.line()
    quick = made.line(quick=True)
    assert "nothing open in the plan so far" in quick and 'step checks only: drawing(action="review")' in quick


def test_a_quick_line_with_no_full_review_behind_it_does_not_claim_a_step() -> None:
    made = plan.build(context.build(SHAFT_FACTS, SHAFT_INTENT), catalog.load(), reviewed=False)
    line = made.line(quick=True)
    assert line.startswith("step unknown (?/8)") and "no full review" in line and 'drawing(action="review")' in line
    assert "step 8/8" not in line and "nothing open" not in line
    assert made.line().startswith("step ") and "unknown" not in made.line()  # a full read is never unknown


def test_a_step_whose_only_open_item_is_the_review_wait_names_it_in_its_summary() -> None:
    made = plan.build(context.build(SHAFT_FACTS, SHAFT_INTENT), catalog.load(), review_pending=("read the sheet as the shop, then ask for the review",))
    summary = next(line for line in plan.render(made, step=1) if line.startswith("STEP 8 "))
    assert "(1 open: wait: read the sheet as the shop, then ask for the review;" in summary and "open: ;" not in summary
    many = plan.build(context.build(SHAFT_FACTS, SHAFT_INTENT), catalog.load(), review_pending=("a", "b", "c", "d"))
    assert "(4 open: wait: a, wait: b, wait: c +1;" in next(line for line in plan.render(many, step=1) if line.startswith("STEP 8 "))


def test_a_rule_a_tool_gate_enforces_is_clear_not_read_and_says_its_gate() -> None:
    made = plan.build(context.build(SHAFT_FACTS, SHAFT_INTENT), catalog.load())
    status = next(s for s in step(made, "purpose").rules if s.rule.id == "drawing.destructive_needs_order")
    assert status.state == "clear" and not status.holds
    assert "gate: the tool refuses both calls without the engineer's order" in plan.render(made, step=1)[-1] or any(
        "gate: the tool refuses" in line for line in plan.render(made, step=1)
    )


def test_every_sheet_type_method_names_only_steps_of_the_sequence() -> None:
    keys = {key for key, _ in plan.SEQUENCE}
    for name, method in plan.sheet_types().items():
        assert name in catalog.load().sheet_types and set(method.steps) == keys, name
