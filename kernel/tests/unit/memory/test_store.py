"""MemoryStore and INDEX.md: scopes, merging, search, atomic writes, broken files, COTAFORGE_HOME."""

from pathlib import Path

import pytest

from cotaforge.memory import (
    HOME_ENV_VAR,
    INDEX_FILENAME,
    MemoryStore,
    MemoryStoreError,
    Scope,
    index_line,
    parse_entry,
    render_entry,
    render_index,
    user_memory_dir,
)
from tests.unit.memory.builders import make_entry, make_material, make_process


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(tmp_path / "design" / "memory", tmp_path / "home" / "memory")


# ---- locations -------------------------------------------------------------------------------


def test_user_memory_dir_uses_cotaforge_home(tmp_path: Path) -> None:
    assert user_memory_dir({HOME_ENV_VAR: str(tmp_path / "cf")}) == tmp_path / "cf" / "memory"


def test_user_memory_dir_defaults_to_the_home_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv(HOME_ENV_VAR, raising=False)
    assert user_memory_dir() == tmp_path / ".cotaforge" / "memory"
    assert user_memory_dir({HOME_ENV_VAR: "  "}) == tmp_path / ".cotaforge" / "memory"


def test_user_memory_dir_reads_the_process_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(HOME_ENV_VAR, str(tmp_path / "elsewhere"))
    assert user_memory_dir() == tmp_path / "elsewhere" / "memory"


def test_for_design_combines_the_project_and_user_folders(tmp_path: Path) -> None:
    built = MemoryStore.for_design(tmp_path / "design", {HOME_ENV_VAR: str(tmp_path / "cf")})
    assert built.directory(Scope.project) == tmp_path / "design" / "memory"
    assert built.directory(Scope.user) == tmp_path / "cf" / "memory"
    assert MemoryStore.for_design(None, {HOME_ENV_VAR: str(tmp_path)}).directory(Scope.project) is None


# ---- save / get / index ----------------------------------------------------------------------


def test_save_writes_the_file_returns_the_text_and_regenerates_the_index(store: MemoryStore) -> None:
    entry = make_entry("first-rule")
    text = store.save(entry, Scope.project)
    project_dir = store.directory(Scope.project)
    assert project_dir is not None
    assert (project_dir / "first-rule.md").read_text(encoding="utf-8") == text
    assert parse_entry(text, expected_id="first-rule") == entry
    index = (project_dir / INDEX_FILENAME).read_text(encoding="utf-8")
    assert "generated" in index.splitlines()[0]
    assert "- [Keep it simple](first-rule.md) · practice · Any first sketch" in index
    assert not [path for path in project_dir.iterdir() if path.name.endswith(".tmp")]


def test_move_takes_the_entry_to_the_other_scope_and_never_overwrites(store: MemoryStore) -> None:
    from cotaforge.memory import EntryStatus

    store.save(make_entry("moving", status=EntryStatus.proposed), Scope.user)
    moved = store.move("moving", Scope.user, Scope.project, EntryStatus.active)
    assert moved.is_active
    assert store.scope_entries(Scope.user) == []
    assert [i.entry.id for i in store.scope_entries(Scope.project)] == ["moving"]
    user_dir = store.directory(Scope.user)
    assert user_dir is not None and not (user_dir / "moving.md").exists()
    assert "moving" not in (user_dir / INDEX_FILENAME).read_text(encoding="utf-8")

    store.save(make_entry("busy"), Scope.user)
    store.save(make_entry("busy", title="Mine"), Scope.project)
    with pytest.raises(MemoryStoreError, match="already has an entry"):
        store.move("busy", Scope.user, Scope.project)
    assert [i.entry.id for i in store.scope_entries(Scope.user)] == ["busy"]  # nothing was moved
    with pytest.raises(MemoryStoreError, match="no entry"):
        store.move("missing", Scope.user, Scope.project)
    with pytest.raises(MemoryStoreError, match="already in"):
        store.move("busy", Scope.user, Scope.user)
    with pytest.raises(MemoryStoreError, match="read-only"):
        store.move("busy", Scope.user, Scope.base)


def test_save_does_not_touch_the_other_scope(store: MemoryStore) -> None:
    store.save(make_entry("only-project"), Scope.project)
    user_dir = store.directory(Scope.user)
    assert user_dir is not None and not user_dir.exists()


def test_save_replaces_an_existing_entry_of_the_same_scope(store: MemoryStore) -> None:
    store.save(make_entry("rule", title="Old"), Scope.project)
    store.save(make_entry("rule", title="New"), Scope.project)
    found = store.get("rule")
    assert found is not None and found.entry.title == "New"
    assert len(store.scope_entries(Scope.project)) == 1


def test_save_without_a_folder_for_the_scope_fails(tmp_path: Path) -> None:
    only_user = MemoryStore(None, tmp_path / "memory")
    with pytest.raises(MemoryStoreError, match="project"):
        only_user.save(make_entry(), Scope.project)


def test_a_failed_write_leaves_no_partial_file(store: MemoryStore, monkeypatch: pytest.MonkeyPatch) -> None:
    store.save(make_entry("rule", title="Original"), Scope.project)
    project_dir = store.directory(Scope.project)
    assert project_dir is not None

    def boom(src: object, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("cotaforge.memory.store.os.replace", boom)
    with pytest.raises(OSError, match="disk full"):
        store.save(make_entry("rule", title="Changed"), Scope.project)
    monkeypatch.undo()
    assert parse_entry((project_dir / "rule.md").read_text(encoding="utf-8")).title == "Original"
    assert sorted(path.name for path in project_dir.iterdir()) == [INDEX_FILENAME, "rule.md"]


def test_index_has_one_line_per_active_entry_within_150_characters(store: MemoryStore) -> None:
    long_text = "Applies when " + "very long condition " * 20
    store.save(make_entry("zeta", applies_when=long_text), Scope.project)
    store.save(make_entry("alpha", type="principle"), Scope.project)
    store.save(make_process("lathe"), Scope.project)
    store.save(make_entry("gone", status="inactive"), Scope.project)
    project_dir = store.directory(Scope.project)
    assert project_dir is not None
    lines = (project_dir / INDEX_FILENAME).read_text(encoding="utf-8").splitlines()
    entry_lines = [line for line in lines if line.startswith("- [")]
    assert len(entry_lines) == 3  # the inactive one is omitted
    assert all(len(line) <= 150 for line in entry_lines)
    assert entry_lines[0].endswith("…")  # the long one is truncated
    # sorted by type then id: practice (zeta), principle (alpha), process (lathe)
    assert [line.split("](")[1].split(")")[0] for line in entry_lines] == ["zeta.md", "alpha.md", "lathe.md"]
    assert [line.split(" · ")[1] for line in entry_lines] == ["practice", "principle", "process"]


def test_index_line_is_single_line_and_bracket_safe() -> None:
    entry = make_entry("odd", title="A [tricky]\ntitle", applies_when="two\nlines")
    line = index_line(entry)
    assert "\n" not in line
    assert line == "- [A (tricky) title](odd.md) · practice · two lines"


def test_render_index_of_nothing_is_just_the_header() -> None:
    assert render_index([]).strip().startswith("# Memory index")
    assert "- [" not in render_index([])


def test_reindex_rebuilds_the_index_after_a_manual_edit(store: MemoryStore) -> None:
    store.save(make_entry("rule"), Scope.project)
    project_dir = store.directory(Scope.project)
    assert project_dir is not None
    (project_dir / INDEX_FILENAME).write_text("garbage", encoding="utf-8")
    text = store.reindex(Scope.project)
    assert "rule.md" in text and (project_dir / INDEX_FILENAME).read_text(encoding="utf-8") == text


# ---- scopes ----------------------------------------------------------------------------------


def test_project_wins_over_user_for_the_same_id(store: MemoryStore) -> None:
    store.save(make_entry("shared", title="From user"), Scope.user)
    store.save(make_entry("shared", title="From project"), Scope.project)
    merged = store.entries()
    assert [(item.entry.id, item.entry.title, item.scope) for item in merged] == [
        ("shared", "From project", Scope.project)
    ]
    found = store.get("shared")
    assert found is not None and found.scope is Scope.project


def test_union_of_scopes_carries_each_entry_scope(store: MemoryStore) -> None:
    store.save(make_entry("project-rule"), Scope.project)
    store.save(make_entry("user-rule"), Scope.user)
    assert {(item.entry.id, item.scope) for item in store.entries()} == {
        ("project-rule", Scope.project),
        ("user-rule", Scope.user),
    }


def test_inactive_project_entry_turns_off_the_user_one(store: MemoryStore) -> None:
    store.save(make_process("lathe"), Scope.user)
    store.save(make_process("lathe", status="inactive"), Scope.project)
    assert [item.entry.id for item in store.entries(include_inactive=False)] == []
    assert [item.entry.id for item in store.entries()] == ["lathe"]


def test_deactivate_keeps_the_file_and_the_other_fields(store: MemoryStore) -> None:
    original = make_process("lathe")
    store.save(original, Scope.project)
    text = store.deactivate("lathe", Scope.project)
    project_dir = store.directory(Scope.project)
    assert project_dir is not None
    assert (project_dir / "lathe.md").is_file()
    assert parse_entry(text) == original.model_copy(update={"status": "inactive"})
    assert "lathe.md" not in (project_dir / INDEX_FILENAME).read_text(encoding="utf-8")
    assert store.get("lathe") is not None  # still retrievable, e.g. to reactivate


def test_deactivate_unknown_id_fails_and_never_deletes(store: MemoryStore) -> None:
    store.save(make_entry("rule"), Scope.user)
    with pytest.raises(MemoryStoreError, match="no entry 'rule' in the project"):
        store.deactivate("rule", Scope.project)


def test_combined_index_lists_project_first_and_merges_by_id(store: MemoryStore) -> None:
    store.save(make_entry("shared", title="User version"), Scope.user)
    store.save(make_entry("user-only"), Scope.user)
    store.save(make_entry("shared", title="Project version"), Scope.project)
    store.save(make_entry("off", status="inactive"), Scope.project)
    text = store.combined_index()
    assert text.index("project — practices: shared") < text.index("user memory — practices: user-only")
    assert text.count("shared") == 1 and "off" not in text.replace("of the", "")  # merged by id; inactive left out
    assert "Project version" not in text  # ids only: the title is one search away


def test_combined_index_groups_by_scope_type_and_sector_and_wraps(store: MemoryStore) -> None:
    store.save(
        make_entry("alfa", type="principle", recognize="x", examples=[{"sector": "injection-molds", "text": "t"}]),
        Scope.project,
    )
    store.save(make_entry("beta", sector="injection-molds"), Scope.project)
    store.save(make_entry("gamma", sector="fixtures"), Scope.project)
    for number in range(12):
        store.save(make_entry(f"una-regla-con-un-nombre-largo-{number:02d}", sector="injection-molds"), Scope.user)
    store.save(make_entry("pendiente", status="proposed"), Scope.user)
    text = store.combined_index()
    assert "project — principles: alfa" in text
    assert "project — practices fixtures: gamma" in text and "project — practices injection-molds: beta" in text
    assert "pendiente [proposed]" in text
    lines = [line for line in text.splitlines() if line.startswith(("  ", "tu memoria"))]
    assert len(lines) >= 3 and all(len(line) <= 125 for line in text.splitlines()[2:])  # wrapped, continuation indented
    assert any(line.startswith("  una-regla-con-un-nombre-largo-") for line in lines)


def test_combined_index_without_memory_is_just_the_header(store: MemoryStore) -> None:
    text = store.combined_index()
    assert "—" not in text and "- [" not in text and "##" not in text


# ---- search ----------------------------------------------------------------------------------


def test_search_is_case_and_accent_insensitive_and_ranked(store: MemoryStore) -> None:
    store.save(
        make_entry("shaft-note", title="Cálculo de ejes", applies_when="Ejes largos", tags=["revolución"]), Scope.user
    )
    store.save(make_entry("misc", title="Other", applies_when="Cuando el EJE es corto", tags=[]), Scope.user)
    store.save(make_entry("unrelated", title="Nothing", applies_when="Nada"), Scope.project)
    found = store.search("CALCULO eje")
    assert [item.entry.id for item in found] == ["shaft-note", "misc"]  # title+applies beats applies only
    assert [item.entry.id for item in store.search("revolucion")] == ["shaft-note"]
    assert store.search("zzz") == []
    assert store.search("   ") == []


def test_search_matches_id_and_skips_inactive(store: MemoryStore) -> None:
    store.save(make_entry("weld-prep"), Scope.project)
    store.save(make_entry("weld-old", status="inactive"), Scope.project)
    assert [item.entry.id for item in store.search("weld")] == ["weld-prep"]


# ---- broken files ----------------------------------------------------------------------------


def test_a_broken_file_is_reported_not_fatal(store: MemoryStore) -> None:
    store.save(make_entry("good"), Scope.project)
    project_dir = store.directory(Scope.project)
    user_dir = store.directory(Scope.user)
    assert project_dir is not None and user_dir is not None
    user_dir.mkdir(parents=True)
    (project_dir / "bad-yaml.md").write_text("---\nid: [oops\n---\n", encoding="utf-8")
    (project_dir / "wrong-id.md").write_text(render_entry(make_entry("other-id")), encoding="utf-8")
    (user_dir / "no-applies.md").write_text(
        "---\nid: no-applies\ntype: practice\ntitle: T\nnot_when: x\n---\n", encoding="utf-8"
    )
    (project_dir / "binary.md").write_bytes(b"\xff\xfe\x00bad")
    assert [item.entry.id for item in store.entries()] == ["good"]
    problems = {(problem.scope, problem.file.name): problem.message for problem in store.problems()}
    assert set(problems) == {
        (Scope.project, "bad-yaml.md"),
        (Scope.project, "wrong-id.md"),
        (Scope.project, "binary.md"),
        (Scope.user, "no-applies.md"),
    }
    assert "applies_when" in problems[(Scope.user, "no-applies.md")]
    assert "must equal the file name" in problems[(Scope.project, "wrong-id.md")]
    # the index is still generated from the good entries
    assert "good.md" in store.reindex(Scope.project)


def test_a_store_with_no_folders_is_empty(tmp_path: Path) -> None:
    empty = MemoryStore(tmp_path / "nope", None)
    assert empty.entries() == [] and empty.problems() == [] and empty.get("x") is None and empty.search("x") == []


def test_changes_made_outside_the_store_are_seen(store: MemoryStore) -> None:
    assert store.entries() == []
    project_dir = store.directory(Scope.project)
    assert project_dir is not None
    project_dir.mkdir(parents=True)
    (project_dir / "by-hand.md").write_text(render_entry(make_entry("by-hand")), encoding="utf-8")
    assert [item.entry.id for item in store.entries()] == ["by-hand"]


def test_an_unchanged_file_is_not_parsed_again_but_an_edited_one_is(
    store: MemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cotaforge.memory import store as store_module

    project_dir = store.directory(Scope.project)
    assert project_dir is not None
    project_dir.mkdir(parents=True)
    path = project_dir / "kept.md"
    path.write_text(render_entry(make_entry("kept", title="First title")), encoding="utf-8")
    parses: list[str] = []
    real = store_module.parse_entry
    monkeypatch.setattr(store_module, "parse_entry", lambda text, **kw: parses.append(text) or real(text, **kw))

    assert [item.entry.title for item in store.entries()] == ["First title"]
    assert [item.entry.title for item in store.entries()] == ["First title"]
    assert len(parses) == 1  # the second read reused the parse

    path.write_text(render_entry(make_entry("kept", title="Second title, longer")), encoding="utf-8")
    assert [item.entry.title for item in store.entries()] == ["Second title, longer"]
    assert len(parses) == 2  # an edit (new size and time) is seen at once


def test_material_entries_are_stored_like_any_other(store: MemoryStore) -> None:
    store.save(make_material(), Scope.user)
    found = store.get("steel-1045")
    assert found is not None and found.entry.material is not None and found.entry.material.name == "Steel 1045"
