"""The user scope (every future design) needs the engineer's answer to one question: whatever the agent saves there is
``proposed``, and only ``approve`` with ``scope=user`` (or ``scope=project``, which moves it) makes it a rule."""

from typing import Any

from cotaforge.memory import EntryStatus, MemoryStore, Scope

from cotaforge_mcp.config import Settings
from tests.conftest import PLATE_SKETCH, Caller, process_entry

QUESTION = "¿Esto vale para tus próximos diseños o solo para este proyecto?"
FIXTURE_FACT: dict[str, Any] = {
    "id": "izaje-del-dispositivo",
    "type": "practice",
    "title": "Izaje del dispositivo por un cáncamo",
    "applies_when": "Un dispositivo que se levanta con grúa",
    "not_when": "Un dispositivo que no se levanta",
    "tags": ["izaje"],
    "when": {"ops": ["extrude"]},
}
STATEMENT = {
    "source": "user_statement",
    "reference": "dijo que solo vale para proyectos como este",
    "by": "Ana",
    "date": "2026-10-30",
}


def store_of(settings: Settings) -> MemoryStore:
    assert settings.home is not None
    return MemoryStore(project_dir=settings.workspace / "memory", user_dir=settings.home / "memory")


def stored(settings: Settings, scope: Scope, entry_id: str = FIXTURE_FACT["id"]) -> Any:
    return next((i.entry for i in store_of(settings).scope_entries(scope) if i.entry.id == entry_id), None)


def approve(call: Caller, **extra: Any) -> str:
    return call.ok("memory", action="approve", id=FIXTURE_FACT["id"], by="Ana", text="Ana: sí", **extra)


def extrude_plate(call: Caller) -> str:
    call.ok("doc", action="new", name="plate")
    call.ok("op", name="sketch", params=PLATE_SKETCH)
    return call.ok("op", name="extrude", params={"sketch": "s1", "depth_mm": 8})


def test_a_user_save_is_proposed_whatever_the_provenance_and_asks_the_question(
    call: Caller, settings: Settings
) -> None:
    for entry in (FIXTURE_FACT, {**FIXTURE_FACT, "provenance": STATEMENT}):
        saved = call.ok("memory", action="save", entry=entry, scope="user")
        assert "in user memory" in saved and "status: proposed" in saved
        assert f"ASK THE ENGINEER, once and before anything else: '{QUESTION}'" in saved
        assert "scope=user" in saved and "scope=project" in saved and "assumptions-and-decisions.md" in saved
        assert stored(settings, Scope.user).status is EntryStatus.proposed
    assert "izaje-del-dispositivo [proposed]" in call.ok("memory", action="index")
    assert "no active entry matches" in call.ok("memory", action="search", text="izaje")


def test_a_user_declaration_is_proposed_too(call: Caller, settings: Settings) -> None:
    saved = call.ok("memory", action="save", entry=process_entry(), scope="user")
    assert "status: proposed" in saved and "declared:" not in saved
    assert stored(settings, Scope.user, "torno-a").status is EntryStatus.proposed
    assert "declared: it now counts" in call.ok("memory", action="save", entry=process_entry("torno-b"))  # project


def test_approving_a_user_entry_needs_his_answer_to_the_question(call: Caller, settings: Settings) -> None:
    call.ok("memory", action="save", entry=FIXTURE_FACT, scope="user")
    refused = call.err("memory", action="approve", id=FIXTURE_FACT["id"], by="Ana", text="Ana: sí")
    assert "user_scope_needs_answer" in refused and QUESTION in refused and "scope=project" in refused
    assert stored(settings, Scope.user).status is EntryStatus.proposed
    done = approve(call, scope="user")
    assert done.startswith("approved: izaje-del-dispositivo in user memory by Ana (Ana: sí)")
    assert stored(settings, Scope.user).status is EntryStatus.active
    assert "[user]" in call.ok("memory", action="search", text="izaje")


def test_only_this_project_moves_it_to_the_project_memory_and_activates_it(call: Caller, settings: Settings) -> None:
    call.ok("memory", action="save", entry=FIXTURE_FACT, scope="user")
    done = approve(call, scope="project")
    assert "approved for this project only" in done and "moved from user to project memory" in done
    assert "assumptions-and-decisions.md" in done
    assert stored(settings, Scope.user) is None
    assert not (settings.home / "memory" / "izaje-del-dispositivo.md").exists()  # type: ignore[operator]
    assert stored(settings, Scope.project).status is EntryStatus.active
    assert "[project]" in call.ok("memory", action="search", text="izaje")
    assert "izaje-del-dispositivo" not in (settings.home / "memory" / "INDEX.md").read_text(encoding="utf-8")  # type: ignore[operator]


def test_scope_user_only_approves_what_was_saved_in_the_user_memory(call: Caller) -> None:
    call.ok("memory", action="save", entry=FIXTURE_FACT)
    refused = call.err("memory", action="approve", id=FIXTURE_FACT["id"], by="Ana", text="Ana: sí", scope="user")
    assert "invalid_input" in refused and "scope=user approves only" in refused
    assert "approved: izaje-del-dispositivo in project memory" in approve(call, scope="project")


def test_a_proposed_user_entry_is_neither_pushed_nor_required(call: Caller) -> None:
    call.ok("memory", action="save", entry=FIXTURE_FACT, scope="user")
    assert "memoria:" not in extrude_plate(call)
    review = call.ok("memory", action="review", stage="detail")
    assert "no entry is called for here" in review
    approve(call, scope="user")
    again = call.ok("op", name="extrude", params={"sketch": "s1", "depth_mm": 4})
    assert "memory: izaje-del-dispositivo" in again
    assert "izaje-del-dispositivo" in call.err("memory", action="review", stage="detail")


def test_the_project_scope_is_unchanged(call: Caller, settings: Settings) -> None:
    saved = call.ok("memory", action="save", entry=FIXTURE_FACT)
    assert "in project memory" in saved and "status: proposed" in saved
    assert "ASK THE ENGINEER" not in saved and QUESTION not in saved
    assert stored(settings, Scope.project).status is EntryStatus.proposed
    assert approve(call).startswith("approved: izaje-del-dispositivo in project memory by Ana")  # no scope needed
    assert stored(settings, Scope.user) is None


def test_a_user_entry_that_cites_only_cases_of_this_project_gets_a_soft_warning(call: Caller) -> None:
    case = {
        "id": "caso-dispositivo-a",
        "type": "case",
        "title": "Dispositivo A",
        "applies_when": "Un dispositivo parecido",
        "not_when": "Uno de otro tipo",
        "case": {"teaches": "Se iza por un cáncamo sobre el centro de gravedad"},
    }
    call.ok("memory", action="save", entry=case)
    cited = call.ok("memory", action="save", entry={**FIXTURE_FACT, "cases": ["caso-dispositivo-a"]}, scope="user")
    assert "warning: every case it cites (caso-dispositivo-a) is a design of this project" in cited
    alone = call.ok("memory", action="save", entry={**FIXTURE_FACT, "id": "izaje-sin-casos"}, scope="user", new=True)
    assert "every case it cites" not in alone
    entry = {**FIXTURE_FACT, "id": "izaje-del-proyecto", "cases": ["caso-dispositivo-a"]}
    in_project = call.ok("memory", action="save", entry=entry, new=True)
    assert "every case it cites" not in in_project  # only the user scope warns


def test_the_guide_gives_the_question_and_the_one_design_rule(session: Any) -> None:
    from cotaforge_mcp.guide import guide

    text = guide(session, "memory")
    assert QUESTION in text and "scope=user|project" in text and "ONE design" in text
    assert len(text) < 6000  # cad_guide cuts its reply at 6000 characters
