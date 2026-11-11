"""Faults found in a real session: unexplained refusals and silent no-ops that must say what happened and what to do."""

from pathlib import Path

from cotaforge.memory import Scope

from cotaforge_mcp import rules
from cotaforge_mcp.session import Session
from tests.conftest import Caller, ExportFake, MakeSession, plate

# --- insert_component needs a saved part document of its own (see test_part_documents.py for the whole workflow) ---


def test_insert_component_without_the_part_document_says_how_to_make_it(call: Caller) -> None:
    call.ok("doc", action="new", name="asm", kind="assembly")
    text = call.err("op", name="insert_component", params={"part": "pin", "placement": {"origin": [0, 0, 0]}})
    assert "'pin' is not an open part document" in text and "no part document is open" in text
    assert "model the part in its own document" in text and 'op(name="insert_component"' in text  # usage + example
    assert "was not applied" not in text  # refused before it reached the executor: nothing to undo


def test_insert_component_in_a_part_says_to_make_an_assembly(call: Caller) -> None:
    call.ok("doc", action="new", name="part")
    text = call.err("op", name="insert_component", params={"part": "pin", "placement": {"origin": [0, 0, 0]}})
    assert "works in an assembly" in text and "operation not allowed" not in text


# --- undo after a reopen ---


def test_undo_to_start_after_a_reopen_is_an_honest_error_not_a_silent_success(call: Caller, tmp_path: Path) -> None:
    plate(call)
    call.ok("doc", action="save", path="plate.fake")
    call.ok("doc", action="close")
    call.ok("doc", action="open", path="plate.fake")
    for arguments in ({}, {"to": "start"}):
        text = call.err("undo", **arguments)
        assert "nothing was undone" in text and "undo history does not survive reopening" in text, text
        assert "instead:" in text and "doc(action=" in text
    assert "steps: 2" in call.ok("doc", action="status")  # nothing moved


def test_undo_of_a_step_added_after_the_reopen_still_works(call: Caller, tmp_path: Path) -> None:
    plate(call)
    call.ok("doc", action="save", path="plate.fake")
    call.ok("doc", action="close")
    call.ok("doc", action="open", path="plate.fake")
    call.ok("op", name="box", id="extra", params={"size": [5, 5, 5]})
    assert call.ok("undo").startswith("undone: extra box")
    assert "undo history does not survive reopening" in call.err("undo")


# --- overwriting files: save and export follow one rule ---


def test_save_onto_another_existing_file_needs_overwrite_and_names_the_flag(call: Caller, tmp_path: Path) -> None:
    plate(call)
    (tmp_path / "other.fake").write_text("not a design of ours", encoding="utf-8")
    refused = call.err("doc", action="save", path="other.fake")
    assert "already exists" in refused and "overwrite=true" in refused
    assert (tmp_path / "other.fake").read_text(encoding="utf-8") == "not a design of ours"
    assert call.ok("doc", action="save", path="other.fake", overwrite=True).startswith("saved:")


def test_save_over_the_documents_own_file_needs_no_flag(call: Caller, tmp_path: Path) -> None:
    plate(call)
    call.ok("doc", action="save", path="plate.fake")
    assert call.ok("doc", action="save").startswith("saved:")
    assert call.ok("doc", action="save", path="plate.fake").startswith("saved:")


def test_export_refuses_to_overwrite_unless_asked(make: MakeSession, tmp_path: Path) -> None:
    call = Caller(make(ExportFake()))
    plate(call)
    assert "exported step:" in call.ok("export", action="step", path="p.step")
    (tmp_path / "p.step").write_text("keep me", encoding="utf-8")
    refused = call.err("export", action="step", path="p.step")
    assert "already exists" in refused and "overwrite=true" in refused
    assert (tmp_path / "p.step").read_text(encoding="utf-8") == "keep me"
    assert "exported step:" in call.ok("export", action="step", path="p.step", overwrite=True)
    assert (tmp_path / "p.step").read_text(encoding="utf-8") != "keep me"


# --- the message of a save under a new name ---


def test_a_save_as_says_what_happened_and_what_the_current_file_is(call: Caller, tmp_path: Path) -> None:
    plate(call)
    first = call.ok("doc", action="save", path="plate.fake")
    assert "saved as a new file" not in first  # the first save is not a "save as"
    second = call.ok("doc", action="save", path="plate_v2.fake")
    assert f"the current file is now {tmp_path / 'plate_v2.fake'}" in second
    assert f"{tmp_path / 'plate.fake'} stays as it was last saved" in second
    assert str(tmp_path / "plate_v2.fake") in call.ok("doc", action="status")
    assert "saved as a new file" not in call.ok("doc", action="save")  # a plain save of the current file


# --- memory: required fields and scope ---


def test_memory_save_without_not_when_says_it_is_required(call: Caller) -> None:
    entry = {"id": "regla", "type": "practice", "title": "Una regla", "applies_when": "Siempre"}
    text = call.err("memory", action="save", entry=entry)
    assert "not_when" in text and "BOTH applies_when" in text and "ask instead of inventing" in text


def test_the_memory_descriptions_say_not_when_is_required_and_when_user_scope_fits() -> None:
    from cotaforge_mcp.schemas import TOOLS_BY_NAME, MemoryArgs

    entry_help = MemoryArgs.model_json_schema()["properties"]["entry"]["description"]
    scope_help = MemoryArgs.model_json_schema()["properties"]["scope"]["description"]
    assert "not_when (REQUIRED" in entry_help
    assert "user (every design) stays proposed until his answer" in scope_help
    assert "not_when" in TOOLS_BY_NAME["memory"].description


def test_memory_default_scope_is_still_the_project(call: Caller, session: Session) -> None:
    entry = {"id": "regla", "type": "practice", "title": "Una regla", "applies_when": "Siempre", "not_when": "Nunca"}
    assert "in project memory" in call.ok("memory", action="save", entry=entry)
    store = rules.memory_store(session.settings)
    assert store.get("regla") is not None and store.get("regla").scope is Scope.project  # type: ignore[union-attr]


# --- skill and template text ---


def test_the_fix_skill_and_the_template_state_not_when_and_the_scope_rule() -> None:
    root = Path(__file__).resolve().parents[3]
    skill = (root / "plugin" / "skills" / "fix" / "SKILL.md").read_text(encoding="utf-8")
    assert "mandatory" in skill and "a single question" in skill and 'scope="user"' in skill
    template = (root / "templates" / "design-space" / "CLAUDE.md").read_text(encoding="utf-8")
    assert "`not_when`" in template and "does this hold for his next designs or only for this project?" in template


# --- cad_guide: TechDraw traps ---


def test_the_freecad_traps_cover_techdraw() -> None:
    from cotaforge.adapters.freecad import FreeCADAdapter

    traps = " ".join(FreeCADAdapter().known_traps())
    assert "TechDraw" in traps and "background" in traps and "wait" in traps
    assert "scaled" in traps and "Y inverted" in traps
