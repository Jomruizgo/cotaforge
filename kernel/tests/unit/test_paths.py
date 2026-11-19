"""English names inside, Spanish names still read: the one path rule and the old ids of the base memory."""

from pathlib import Path

import pytest

from cotaforge import paths
from cotaforge.memory import MemoryStore, Scope, base_memory_dir, render_entry
from cotaforge.memory.legacy_ids import LEGACY_BY_NEW, LEGACY_IDS
from tests.unit.memory.builders import make_entry


def test_a_new_folder_gets_the_english_name(tmp_path: Path) -> None:
    assert paths.resolve(tmp_path, paths.MEMORY) == tmp_path / "memory"
    assert paths.resolve(tmp_path, paths.ASSUMPTIONS) == tmp_path / "assumptions-and-decisions.md"


def test_an_old_folder_is_used_while_the_english_one_is_missing(tmp_path: Path) -> None:
    (tmp_path / "memoria").mkdir()
    (tmp_path / "supuestos-y-decisiones.md").write_text("x", encoding="utf-8")
    assert paths.resolve(tmp_path, paths.MEMORY) == tmp_path / "memoria"
    assert paths.resolve(tmp_path, paths.ASSUMPTIONS) == tmp_path / "supuestos-y-decisiones.md"
    (tmp_path / "memory").mkdir()  # once the engineer has the English one, that one wins
    assert paths.resolve(tmp_path, paths.MEMORY) == tmp_path / "memory"
    assert (tmp_path / "memoria").is_dir(), "nothing is renamed or removed"


def test_the_suffixes_of_a_drawing_keep_the_spanish_name_when_the_file_has_it(tmp_path: Path) -> None:
    folder = tmp_path / "planos"
    folder.mkdir()
    (folder / "bracket-intencion.md").write_text("x", encoding="utf-8")
    (folder / "bracket-preguntas.jsonl").write_text("", encoding="utf-8")
    assert paths.resolve(folder, "bracket-intent.md") == folder / "bracket-intencion.md"
    assert paths.resolve(folder, "bracket-questions.jsonl") == folder / "bracket-preguntas.jsonl"
    assert paths.resolve(folder, "plate-intent.md") == folder / "plate-intent.md"
    assert paths.legacy_name("plate-window.json") == "plate-ventana.json"


def test_every_english_name_has_its_spanish_one() -> None:
    assert paths.legacy_name("memory") == "memoria" and paths.legacy_name("calculations") == "calculos"
    assert paths.legacy_name("drawings") == "planos" and paths.legacy_name("friction.md") == "friccion.md"
    assert paths.legacy_name("something-else") is None


@pytest.mark.parametrize(
    ("old", "new"),
    [("dibujante", "drafter"), ("diseñador", "designer"), ("ingeniero", "engineer"), ("fabricante", "fabricator")],
)
def test_the_spanish_role_tokens_are_aliases(old: str, new: str) -> None:
    assert paths.alias_token(old) == new == paths.alias_token(new)
    assert paths.alias_token(" Ingeniero ") == "engineer"
    assert paths.alias_token("Ana") == "Ana", "anything else comes back as it is"


# ---- the base memory ids -------------------------------------------------------------------------------------------


ADDED_AFTER_THE_RENAME = {"machining-turned-end-chamfer"}


def test_every_old_base_id_points_to_a_base_entry_that_exists() -> None:
    store = MemoryStore(base_dir=base_memory_dir())
    ids = {item.entry.id for item in store.scope_entries(Scope.base)}
    # LEGACY_IDS is the frozen table of the 0.10 rename: an entry added later has an English id from the start
    assert (
        set(LEGACY_IDS.values()) | ADDED_AFTER_THE_RENAME == ids
        and not set(LEGACY_IDS.values()) & ADDED_AFTER_THE_RENAME
    )
    assert len(LEGACY_IDS) == len(set(LEGACY_IDS.values()))
    assert LEGACY_BY_NEW == {new: old for old, new in LEGACY_IDS.items()}
    assert not set(LEGACY_IDS) & ids, "an old id never collides with a new one"


def test_a_link_to_an_old_base_id_reaches_the_new_entry() -> None:
    entry = make_entry("mine", related=["chapa-alivio-de-doblez"], replaces="dibujo-vistas-suficientes")
    assert entry.related == ("sheet-metal-bend-relief",) and entry.replaces == "drawing-sufficient-views"


def test_the_engineers_override_by_the_old_id_still_overrides_and_keeps_its_file(tmp_path: Path) -> None:
    mine = tmp_path / "memoria"
    mine.mkdir()
    off = make_entry("dibujo-vistas-suficientes", status="inactive")
    (mine / "dibujo-vistas-suficientes.md").write_text(render_entry(off), encoding="utf-8")
    store = MemoryStore(project_dir=mine, base_dir=base_memory_dir())
    found = store.get("drawing-sufficient-views")
    assert found is not None and found.scope is Scope.project and not found.entry.is_active
    store.save(found.entry.model_copy(update={"title": "Mine"}), Scope.project)
    assert sorted(p.name for p in mine.glob("*.md") if p.name != "INDEX.md") == ["dibujo-vistas-suficientes.md"]
