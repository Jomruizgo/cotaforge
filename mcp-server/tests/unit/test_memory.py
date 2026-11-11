"""The ``memory`` tool over the real store (project + user scope in temporary folders) and the index in the server
instructions."""

from pathlib import Path
from typing import Any

from cotaforge.adapters.fake import FakeAdapter

from cotaforge_mcp import memory_tools
from cotaforge_mcp.config import Settings
from cotaforge_mcp.guide import server_instructions
from cotaforge_mcp.schemas import TOOLS_BY_NAME
from tests.conftest import Caller, MakeSession, declare, material_entry, process_entry

PRACTICE: dict[str, Any] = {
    "id": "ajuste-ejes",
    "type": "practice",
    "title": "Ajuste de ejes",
    "applies_when": "Ejes con rodamiento",
    "not_when": "Ejes sin carga",
    "tags": ["eje", "ajuste"],
    "body": "Usar h6 para el asiento del rodamiento.",
}


STATEMENT = {
    "source": "user_statement",
    "reference": "dijo que vale para todos sus proyectos",
    "by": "Ana",
    "date": "2026-10-27",
}


def approve(call: Caller, entry_id: str, **extra: Any) -> str:
    return call.ok("memory", action="approve", id=entry_id, by="Ana", text="Ana: apruebala", **extra)


def test_the_tool_is_listed_and_not_gated_by_the_cad(session: object) -> None:
    assert "memory" in TOOLS_BY_NAME
    assert TOOLS_BY_NAME["memory"].visible(session)  # type: ignore[arg-type]


def test_empty_memory_says_so_and_adds_nothing_to_the_instructions(call: Caller) -> None:
    assert call.ok("memory", action="index").startswith("memory: no active entries")
    assert memory_tools.instructions_section(call.session.settings) == ""
    assert "Memory (" not in server_instructions(call.session)


def test_save_get_search_index_round_trip(call: Caller, settings: Settings) -> None:
    saved = call.ok("memory", action="save", entry=PRACTICE)
    assert saved.startswith("saved: ajuste-ejes in project memory (memory/ajuste-ejes.md)")
    assert "declared:" not in saved  # a practice restricts nothing
    assert "status: proposed" in saved and "classification: type=practice, scope=project" in saved
    assert (settings.workspace / "memory" / "ajuste-ejes.md").is_file()
    assert (settings.workspace / "memory" / "INDEX.md").is_file()

    got = call.ok("memory", action="get", id="ajuste-ejes")
    assert "data, not instructions" in got and "title: Ajuste de ejes" in got and "h6" in got
    assert "PROPOSED" in got
    assert "no active entry matches" in call.ok("memory", action="search", text="rodamiento eje")  # not a rule yet
    pending = call.ok("memory", action="index")
    assert "ajuste-ejes [proposed]" in pending
    assert "approved: ajuste-ejes" in approve(call, "ajuste-ejes")

    found = call.ok("memory", action="search", text="rodamiento eje")
    assert "[Ajuste de ejes](ajuste-ejes.md) · practice" in found and found.endswith("[project]")
    assert "no active entry matches" in call.ok("memory", action="search", text="soldadura")

    index = call.ok("memory", action="index")
    assert "project — practices: ajuste-ejes" in index and "[proposed]" not in index
    assert all(len(line) <= 150 for line in index.splitlines())


def test_user_scope_lives_in_the_home_folder_and_project_wins(call: Caller, settings: Settings) -> None:
    call.ok("memory", action="save", entry={**PRACTICE, "provenance": STATEMENT}, scope="user")
    approve(call, "ajuste-ejes", scope="user")
    assert settings.home is not None and (settings.home / "memory" / "ajuste-ejes.md").is_file()
    assert not (settings.workspace / "memory").exists()
    assert "user memory — practices: ajuste-ejes" in call.ok("memory", action="index")

    other = {**PRACTICE, "title": "Ajuste del proyecto"}
    call.ok("memory", action="save", entry=other)
    assert "title: Ajuste del proyecto" in call.ok("memory", action="get", id="ajuste-ejes")
    shadowed = call.ok(
        "memory", action="save", entry={**PRACTICE, "title": "Otro de usuario", "provenance": STATEMENT}, scope="user"
    )
    assert "note: the project entry with the same id wins" in shadowed


def test_a_number_without_provenance_is_rejected_cleanly(call: Caller, settings: Settings) -> None:
    entry = process_entry()
    del entry["provenance"]
    refused = call.err("memory", action="save", entry=entry)
    assert refused.startswith("error: invalid_entry") and "provenance" in refused
    assert "never save an estimate of your own" in refused
    assert "Traceback" not in refused
    assert not (settings.workspace / "memory").exists()  # nothing written


def test_other_invalid_entries_name_the_field(call: Caller) -> None:
    missing = {k: v for k, v in PRACTICE.items() if k != "not_when"}
    assert "not_when" in call.err("memory", action="save", entry=missing)
    assert "colour" in call.err("memory", action="save", entry={**PRACTICE, "colour": "red"})
    assert call.err("memory", action="save").startswith("error: invalid_input")


def test_a_process_or_material_entry_is_declared_at_once(call: Caller) -> None:
    call.ok("doc", action="new", name="p")
    saved = call.ok("memory", action="save", entry=process_entry())
    assert "declared: it now counts" in saved and "max_diameter_mm" in saved
    call.ok("memory", action="save", entry=material_entry())
    assert "outside_declared" in call.err("doc", action="set_route", route=["torno-b"])
    call.ok("doc", action="set_route", route=["torno-a"])
    call.ok("doc", action="set_material", material="C45")
    assert "0 violation" in call.ok("check")


def test_deactivate_keeps_the_file_and_switches_the_entry_off(call: Caller, settings: Settings) -> None:
    call.ok("memory", action="save", entry=PRACTICE)
    approve(call, "ajuste-ejes")
    done = call.ok("memory", action="deactivate", id="ajuste-ejes")
    assert done.startswith("deactivated: ajuste-ejes in project memory")
    assert (settings.workspace / "memory" / "ajuste-ejes.md").is_file()
    assert "no active entries" in call.ok("memory", action="index")
    assert "status: inactive" in call.ok("memory", action="get", id="ajuste-ejes")
    assert "no active entry matches" in call.ok("memory", action="search", text="ejes")


def test_deactivating_a_user_entry_says_it_is_global(call: Caller) -> None:
    call.ok("memory", action="save", entry={**PRACTICE, "provenance": STATEMENT}, scope="user")
    assert "off for every design" in call.ok("memory", action="deactivate", id="ajuste-ejes")


def test_unknown_and_missing_arguments(call: Caller) -> None:
    call.ok("memory", action="save", entry=PRACTICE)
    unknown = call.err("memory", action="get", id="nada")
    assert unknown.startswith("error: unknown_entry") and "ids: ajuste-ejes" in unknown
    assert call.err("memory", action="get").startswith("error: invalid_input")
    assert call.err("memory", action="search").startswith("error: invalid_input")
    assert call.err("memory", action="deactivate").startswith("error: invalid_input")
    assert call.err("memory", action="deactivate", id="nada").startswith("error: unknown_entry")
    assert call.err("memory", action="forget").startswith("error: invalid_input")


def test_a_broken_file_is_reported_and_skipped(call: Caller, settings: Settings) -> None:
    call.ok("memory", action="save", entry=PRACTICE)
    (settings.workspace / "memory" / "roto.md").write_text("no frontmatter\n", encoding="utf-8")
    index = call.ok("memory", action="index")
    assert "1 memory file(s) could not be loaded" in index and "project/roto.md" in index
    assert "ajuste-ejes" in index
    assert "could not be loaded" in memory_tools.instructions_section(settings)


def test_the_instructions_carry_the_index_of_both_scopes(make: MakeSession, settings: Settings) -> None:
    declare(settings, PRACTICE)
    declare(settings, material_entry(), scope="user")
    text = server_instructions(make(FakeAdapter()))
    assert "Memory (the engineer's knowledge" in text
    assert "project — practices: ajuste-ejes" in text
    assert "user memory — materials: acero-1045" in text
    assert "memory(action=search, text) shows titles" in text
    assert "index cut" not in text


def test_the_instructions_index_is_bounded_and_says_when_it_is_cut(settings: Settings) -> None:
    for number in range(memory_tools.MAX_INSTRUCTION_ENTRIES + 5):
        declare(settings, {**PRACTICE, "id": f"regla-{number:03d}", "title": f"Regla {number}"})
    section = memory_tools.instructions_section(settings)
    shown = section.count("regla-")
    assert shown == memory_tools.MAX_INSTRUCTION_ENTRIES
    assert f"index cut: {shown} of {shown + 5} entries shown" in section
    assert len(section) < memory_tools.MAX_INSTRUCTION_CHARS


def test_the_instructions_index_lists_ids_only_so_a_big_memory_stays_small(settings: Settings) -> None:
    for number in range(40):
        entry_id = f"regla-con-un-nombre-descriptivo-{number:02d}"
        declare(settings, {**PRACTICE, "id": entry_id, "applies_when": "x" * 140})
    section = memory_tools.instructions_section(settings)
    assert section.count("regla-con-un-nombre-descriptivo-") == 40 and "index cut" not in section
    assert "x" * 20 not in section and len(section) < 2500  # the applies_when is one search away


def test_the_tests_never_touch_the_real_profile(settings: Settings) -> None:
    assert settings.home is not None and settings.home != Path.home() / ".cotaforge"
    assert str(settings.user_memory_dir()).startswith(str(settings.workspace))
