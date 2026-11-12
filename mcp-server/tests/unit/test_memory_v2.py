"""Memory v2 through the tools (fake CAD): roles, the push at the decision moment, answers at the milestone, governed
saving, the brief and the base set."""

from pathlib import Path
from typing import Any

from cotaforge.memory import MemoryStore, Scope, base_memory_dir

from cotaforge_mcp import design_memory, memory_tools
from cotaforge_mcp.config import Settings
from cotaforge_mcp.guide import guide
from tests.conftest import PLATE_SKETCH, Caller, MakeSession, VisibleFake, declare

PRINCIPLE: dict[str, Any] = {
    "id": "precision-del-ensamble",
    "type": "principle",
    "title": "No pedir una precisión que la pieza sola no garantiza",
    "applies_when": "Una pieza soldada lleva un patrón con tolerancia fina",
    "not_when": "La pieza se mecaniza completa después de soldar",
    "recognize": "Una pieza soldada con un patrón de agujeros con tolerancia fina en voladizo",
    "typical_error": "Soldar una placa en voladizo que lleva el patrón tolerado",
    "when": {"roles": ["soldado"], "stages": ["detail"]},
    "examples": [
        {"sector": "automotriz", "text": "Un soporte de chasis.", "validated": False},
        {"sector": "fixtures", "text": "La placa del dispositivo.", "validated": True},
        {"sector": "agricola", "text": "Un implemento.", "validated": False},
        {"sector": "mobiliario", "text": "Un herraje.", "validated": False},
    ],
}
STATEMENT = {"source": "user_statement", "reference": "su palabra", "by": "Ana", "date": "2026-10-27"}


def approved(settings: Settings, entry: dict[str, Any] = PRINCIPLE) -> None:
    declare(settings, entry)


def welded_plate(call: Caller) -> str:
    call.ok("doc", action="new", name="plate")
    call.ok("op", name="sketch", params=PLATE_SKETCH)
    return call.ok("op", name="extrude", params={"sketch": "s1", "depth_mm": 8, "role": "soldado"})


# ---- roles ----------------------------------------------------------------------------------------------------------


def test_a_role_goes_on_the_step_that_makes_the_body_and_is_listed(call: Caller) -> None:
    text = welded_plate(call)
    assert "role: s2 = soldado" in text
    assert "roles: s2=soldado" in call.ok("doc", action="status")


def test_an_unknown_role_is_refused_with_the_vocabulary(call: Caller) -> None:
    call.ok("doc", action="new", name="plate")
    call.ok("op", name="sketch", params=PLATE_SKETCH)
    refused = call.err("op", name="extrude", params={"sketch": "s1", "depth_mm": 8, "role": "rey"})
    assert "unknown_role" in refused and "soldado" in refused and "roles.yaml" in refused
    assert "s2" not in call.ok("doc", action="status")  # nothing was run


def test_a_role_can_be_set_and_cleared_later_and_a_step_without_body_says_so(call: Caller) -> None:
    welded_plate(call)
    assert "role: s2 = soporte" in call.ok("op", edit="s2", params={"role": "soporte"})
    assert "roles: s2=soporte" in call.ok("doc", action="status")
    assert "note: role 'soporte' not set: s1 made no body" in call.ok("op", edit="s1", params={"role": "soporte"})
    assert "cleared" in call.ok("op", edit="s2", params={"role": ""})
    assert "roles:" not in call.ok("doc", action="status")


def test_roles_survive_save_close_and_open(call: Caller, settings: Settings) -> None:
    welded_plate(call)
    call.ok("doc", action="save", path="plate.fake")
    call.ok("doc", action="close")
    call.ok("doc", action="open", path="plate.fake")
    assert "roles: s2=soldado" in call.ok("doc", action="status")


def test_the_engineer_extends_the_roles_and_changes_a_colour(settings: Settings) -> None:
    assert design_memory.role_palette(settings)["soldado"] == design_memory.DEFAULT_ROLE_COLORS["soldado"]
    (settings.workspace / "roles.yaml").write_text(
        "roles:\n  soldado: '#112233'\n  bastidor: '#445566'\n", encoding="utf-8"
    )
    palette = design_memory.role_palette(settings)
    assert palette["soldado"] == "#112233" and palette["bastidor"] == "#445566"
    assert design_memory.checked_role(settings, "Bastidor") == "bastidor"
    assert design_memory.checked_role(settings, "Fijación") == "fijacion"


def test_the_window_colours_a_body_by_its_role(make: MakeSession, settings: Settings) -> None:
    fake = VisibleFake()
    call = Caller(make(fake))
    welded_plate(call)
    assert fake.styles and fake.styles[-1].color == design_memory.DEFAULT_ROLE_COLORS["soldado"].lower()
    fake.styles.clear()
    assert "1 body(ies) painted" in call.ok("view", action="style", color="roles")
    assert len(fake.styles) == 1


# ---- the push -------------------------------------------------------------------------------------------------------


def test_an_op_that_matches_an_entry_gets_one_line_and_the_same_line_is_not_repeated(
    call: Caller, settings: Settings
) -> None:
    approved(settings)
    text = welded_plate(call)
    lines = [line for line in text.splitlines() if line.startswith("memory:")]
    assert lines[0].startswith("memory: precision-del-ensamble — recognize: Una pieza soldada con un patrón")
    assert "memory(action=answer, answers={id: text}" in lines[1]
    again = call.ok("op", name="extrude", params={"sketch": "s1", "depth_mm": 3, "role": "soldado"})
    assert "memoria:" not in again  # same entry, same op: said once


def test_a_proposed_entry_is_not_pushed_and_approving_it_starts_the_push(call: Caller, settings: Settings) -> None:
    entry = {**PRINCIPLE, "when": {"ops": ["extrude"]}, "status": "proposed"}
    declare(settings, entry)
    call.ok("doc", action="new", name="plate")
    call.ok("op", name="sketch", params=PLATE_SKETCH)
    assert "memoria:" not in call.ok("op", name="extrude", params={"sketch": "s1", "depth_mm": 8})
    call.ok("memory", action="approve", id="precision-del-ensamble", by="Ana", text="Ana: apruébala")
    assert "memory: precision-del-ensamble" in call.ok("op", name="extrude", params={"sketch": "s1", "depth_mm": 4})


def test_edit_and_delete_of_a_feature_are_ops_a_trigger_can_name(call: Caller, settings: Settings) -> None:
    approved(settings, {**PRINCIPLE, "when": {"ops": ["edit_feature"]}})
    welded_plate(call)
    assert "memory: precision-del-ensamble" in call.ok("op", edit="s2", params={"depth_mm": 9})


# ---- the milestone ---------------------------------------------------------------------------------------------------


def test_review_refuses_until_the_entry_is_read_and_answered(call: Caller, settings: Settings) -> None:
    approved(settings)
    call.ok("memory", action="brief", sector="fixtures", processes=["soldadura"])
    welded_plate(call)
    refused = call.err("memory", action="review", stage="detail")
    assert "pending_answers" in refused and "- precision-del-ensamble — recognize:" in refused
    assert "NOT read" in refused

    early = call.err(
        "memory", action="answer", id="precision-del-ensamble", text="applies: la placa s2 lleva el patrón"
    )
    assert "not_read" in early

    got = call.ok("memory", action="get", id="precision-del-ensamble")
    assert got.index("[fixtures] (validated)") < got.index("analogy of automotriz: does it apply here?")
    assert got.count("analogy of") == 2 and "mobiliario" not in got and "NOT validated" in got

    assert "answer is not enough" in call.err(
        "memory", action="answer", id="precision-del-ensamble", text="applies: sí"
    )
    assert "start with" in call.err("memory", action="answer", id="precision-del-ensamble", text="sí aplica a todo")
    done = call.ok(
        "memory", action="answer", id="precision-del-ensamble", text="applies: la placa s2 se suelda con el patrón"
    )
    assert done.startswith("answered: precision-del-ensamble (applies)")
    ready = call.ok("memory", action="review", stage="detail")
    assert "ready - 1 entry answered" in ready and "APPLIES: applies: la placa s2" in ready
    assert "memory answers: 1 given" in call.ok("doc", action="status")


def test_several_entries_are_read_and_answered_in_one_call_each(call: Caller, settings: Settings) -> None:
    second = {
        **PRINCIPLE,
        "id": "soldadura-sin-distorsion",
        "title": "Prever la distorsión del cordón antes de fijar la cota",
        "applies_when": "Una pieza soldada con cotas de posición críticas",
        "not_when": "La pieza se mecaniza completa después de soldar",
        "recognize": "Una placa soldada cuya cota de posición se mide después del cordón",
        "typical_error": "Fijar la cota antes de soldar sin sobremedida de mecanizado",
    }
    approved(settings)
    approved(settings, second)
    call.ok("memory", action="brief", sector="fixtures", processes=["soldadura"])
    welded_plate(call)
    ids = ["precision-del-ensamble", "soldadura-sin-distorsion"]
    batch = {i: "applies: la placa s2 se suelda con el patrón" for i in ids}

    assert "not_read" in call.err("memory", action="answer", answers=batch)  # nothing read yet: the first stops it
    got = call.ok("memory", action="get", ids=ids)
    assert got.count("memory entry (") == 2 and "precision-del-ensamble" in got and "soldadura-sin-distorsion" in got
    assert "at most" in call.err("memory", action="get", ids=[f"x{n}" for n in range(13)])

    bad = {ids[0]: batch[ids[0]], ids[1]: "applies: sí"}  # the second is not an answer
    refused = call.err("memory", action="answer", answers=bad)
    assert "answer 2 (soldadura-sin-distorsion)" in refused and "1 of 2 recorded before it" in refused
    done = call.ok("memory", action="answer", answers=batch)
    assert done.count("answered:") == 2
    ready = call.ok("memory", action="review", stage="detail")
    assert "ready - 2 entries answered" in ready


def test_a_stage_without_triggered_entries_is_ready(call: Caller, settings: Settings) -> None:
    approved(settings)
    welded_plate(call)
    assert "no entry is called for here" in call.ok("memory", action="review", stage="layout")


def test_answers_survive_save_close_and_open_but_reading_does_not(
    call: Caller, make: MakeSession, settings: Settings
) -> None:
    approved(settings)
    welded_plate(call)
    call.ok("memory", action="get", id="precision-del-ensamble")
    call.ok(
        "memory", action="answer", id="precision-del-ensamble", text="does not apply: se mecaniza después de soldar"
    )
    call.ok("doc", action="save", path="plate.fake")
    call.ok("doc", action="close")
    call.ok("doc", action="open", path="plate.fake")
    assert "DOES NOT APPLY" in call.ok("memory", action="review", stage="detail")
    fresh = Caller(make(call.session.adapter.__class__()))  # another server session: nothing was read
    fresh.ok("doc", action="open", path="plate.fake")
    assert "DOES NOT APPLY" in fresh.ok("memory", action="review", stage="detail")
    assert "not_read" in fresh.err(
        "memory", action="answer", id="precision-del-ensamble", text="applies: la placa s2 ok"
    )


def test_a_proposed_entry_cannot_be_answered(call: Caller, settings: Settings) -> None:
    declare(settings, {**PRINCIPLE, "status": "proposed"})
    welded_plate(call)
    call.ok("memory", action="get", id="precision-del-ensamble")
    assert "unknown_entry" in call.err(
        "memory", action="answer", id="precision-del-ensamble", text="applies: la placa s2 ok"
    )
    assert "no entry is called for here" in call.ok("memory", action="review", stage="detail")


# ---- governed saving -------------------------------------------------------------------------------------------------


def test_a_principle_with_a_brand_and_a_measure_is_refused_with_a_split_suggestion(call: Caller) -> None:
    call.ok("memory", action="brief", sector="fixtures", processes=["soldadura"])
    bad = {
        **PRINCIPLE,
        "id": "mala",
        "title": "Los soportes de Festolux llevan agujeros de 19 mm en fixtures",
        "when": None,
    }
    del bad["when"]
    refused = call.err("memory", action="save", entry=bad)
    assert "split_entry" in refused and "'19 mm'" in refused and "Festolux" in refused and "fixtures" in refused
    assert "practice" in refused and "nothing now" in refused
    assert "mala" not in call.ok("memory", action="index")  # nothing was written


def test_a_principle_needs_recognize_and_an_example(call: Caller) -> None:
    bare = {k: v for k, v in PRINCIPLE.items() if k not in ("recognize", "examples")}
    refused = call.err("memory", action="save", entry=bare)
    assert "recognize" in refused and "example" in refused


def test_a_proper_save_lands_proposed_with_the_classification(call: Caller, settings: Settings) -> None:
    saved = call.ok("memory", action="save", entry=PRINCIPLE)
    assert "status: proposed" in saved and "PENDING" in saved
    assert "classification: type=principle, scope=project" in saved and "roles=['soldado']" in saved
    assert "status: proposed" in (settings.workspace / "memory" / "precision-del-ensamble.md").read_text(
        encoding="utf-8"
    )
    index = call.ok("memory", action="index")
    assert "precision-del-ensamble [proposed]" in index
    assert "precision-del-ensamble [proposed]" in memory_tools.instructions_section(settings)


def test_the_agent_cannot_save_an_active_entry_even_if_it_asks(call: Caller, settings: Settings) -> None:
    call.ok("memory", action="save", entry={**PRINCIPLE, "status": "active"})
    stored = MemoryStore(project_dir=settings.workspace / "memory").get("precision-del-ensamble")
    assert stored is not None and stored.entry.is_proposed


def test_save_shows_similar_entries_and_asks_to_update_or_confirm(call: Caller, settings: Settings) -> None:
    approved(settings)
    twin = {**PRINCIPLE, "id": "gemela", "title": "Precisión que la pieza soldada no garantiza"}
    refused = call.err("memory", action="save", entry=twin)
    assert "similar_entries" in refused and "precision-del-ensamble" in refused and "new=true" in refused
    assert "saved:" in call.ok("memory", action="save", entry=twin, new=True)
    update = {**PRINCIPLE, "title": "Otro título para la misma regla"}
    saved = call.ok("memory", action="save", entry=update)  # same id: an update, not a new entry
    assert "the approved version of precision-del-ensamble is replaced" in saved


def test_user_scope_is_proposed_whatever_the_provenance(call: Caller) -> None:
    for entry in (PRINCIPLE, {**PRINCIPLE, "provenance": STATEMENT}):
        ok = call.ok("memory", action="save", entry=entry, scope="user")
        assert "in user memory" in ok and "status: proposed" in ok and "ASK THE ENGINEER" in ok


def test_approve_is_the_engineers_gate(call: Caller, settings: Settings) -> None:
    call.ok("memory", action="save", entry=PRINCIPLE)
    assert "approve is the engineer's gate" in call.err("memory", action="approve", id="precision-del-ensamble")
    done = call.ok("memory", action="approve", id="precision-del-ensamble", by="Ana", text="Ana: sí, es regla")
    assert done.startswith("approved: precision-del-ensamble in project memory by Ana (Ana: sí, es regla)")
    assert "not_proposed" in call.err(
        "memory", action="approve", id="precision-del-ensamble", by="Ana", text="otra vez"
    )


def test_process_and_material_declarations_stay_active_as_before(call: Caller) -> None:
    from tests.conftest import material_entry

    assert "declared: it now counts" in call.ok("memory", action="save", entry=material_entry())


# ---- brief, base set, guide -----------------------------------------------------------------------------------------


def test_the_brief_is_kept_in_the_design_space(call: Caller, settings: Settings) -> None:
    assert "sector not declared" in call.ok("memory", action="brief") and "ask the engineer" in call.ok(
        "memory", action="brief"
    )
    done = call.ok("memory", action="brief", sector="fixtures", processes=["soldadura", "mecanizado"])
    assert "sector fixtures; processes soldadura, mecanizado" in done
    assert "sector fixtures" in call.ok("memory", action="brief")
    assert design_memory.load_brief(settings).sector == "fixtures"


def test_the_base_set_is_listed_pushed_and_deactivated_by_id(make: MakeSession, settings: Settings) -> None:
    on = Settings(adapter="fake", workspace=settings.workspace, home=settings.home, base_memory=True)
    from cotaforge.adapters.fake import FakeAdapter

    from cotaforge_mcp.server import make_session

    call = Caller(make_session(on, FakeAdapter()))
    index = call.ok("memory", action="index")
    assert "base — principles:" in index and "drawing-sufficient-views" in index
    section = memory_tools.instructions_section(on)
    base_total = len(list(base_memory_dir().glob("*.md")))
    assert f"base: {base_total} generic entries" in section and "drawing-sufficient-views" not in section
    assert "memory(action=index|search)" in section  # the entries with no trigger are found on demand
    got = call.ok("memory", action="get", id="drawing-sufficient-views")
    assert "base set" in got and "analogy of" in got
    assert "deactivated: drawing-sufficient-views for this project" in call.ok(
        "memory", action="deactivate", id="drawing-sufficient-views"
    )
    assert "drawing-sufficient-views" not in call.ok("memory", action="index")  # off: not listed at all
    assert (settings.workspace / "memory" / "drawing-sufficient-views.md").is_file()
    assert Scope.base in {i.scope for i in MemoryStore(base_dir=base_memory_dir()).entries()}


def test_a_base_principle_is_pushed_at_its_op_and_required_at_the_milestone(settings: Settings) -> None:
    from cotaforge.adapters.fake import FakeAdapter

    from cotaforge_mcp.server import make_session

    on = Settings(adapter="fake", workspace=settings.workspace, home=settings.home, base_memory=True)
    call = Caller(make_session(on, FakeAdapter()))
    welded_plate(call)
    refused = call.err("memory", action="review", stage="detail")
    assert "welded-hollow-section-vented-closure" in refused  # roles soldado, stage detail
    layout = call.err("memory", action="review", stage="layout")
    assert "design-target-mass" in layout  # a stage-only trigger


def test_the_guide_topic_explains_memory_and_roles(session: Any) -> None:
    text = guide(session, "memory")
    for needle in (
        "memory(action=review",
        "approve",
        "ONE idea per entry",
        "soldado",
        "roles.yaml",
        "insert_component",
    ):
        assert needle in text, needle
    for needle in ("related", "replaces", "sources", "reference_book", "sectors.yaml", "analogies"):  # memory M1.1
        assert needle in text, needle
    assert len(text) < 6000  # cad_guide cuts its reply at 6000 characters


def test_the_tests_leave_the_real_memory_alone(settings: Settings) -> None:
    assert settings.home is not None and settings.home != Path.home() / ".cotaforge"
