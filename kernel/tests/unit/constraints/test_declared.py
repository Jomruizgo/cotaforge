"""DeclaredSet: union of scopes, project over user, closed world, route and material validation."""

from pathlib import Path

import pytest

from cotaforge.constraints import DeclaredSet
from cotaforge.memory import MemoryStore, Scope
from tests.unit.memory.builders import make_entry, make_material, make_process


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(tmp_path / "design" / "memory", tmp_path / "home" / "memory")


def test_nothing_declared_means_nothing_restricts() -> None:
    declared = DeclaredSet([make_entry("practice-only")])
    assert not declared.processes_declared and not declared.materials_declared
    route = declared.validate_route(["anything", "goes"])
    assert route.ok and not route.restricted and route.allowed == ()
    material = declared.validate_material("Unobtainium")
    assert material.ok and not material.restricted


def test_only_active_process_and_material_entries_declare() -> None:
    declared = DeclaredSet(
        [
            make_process("lathe"),
            make_process("mill", tag="milling_3axis", capabilities=[], status="inactive"),
            make_material("steel"),
            make_material("brass", name="Brass", status="inactive"),
            make_entry("note"),
        ]
    )
    assert [item.entry_id for item in declared.processes] == ["lathe"]
    assert [item.entry_id for item in declared.materials] == ["steel"]


def test_declared_process_carries_tag_capabilities_and_provenance_reference() -> None:
    declared = DeclaredSet([make_process("lathe")])
    process = declared.process("lathe")
    assert process is not None
    assert process.known_tag == "turning" and process.reference == "Tool table 2026-03"
    assert process.capabilities[0].name == "max_diameter_mm"
    assert declared.process("missing") is None


def test_route_must_be_a_subset_of_the_declared_processes() -> None:
    declared = DeclaredSet([make_process("lathe"), make_process("mill", tag="milling_3axis", capabilities=[])])
    assert declared.validate_route(["lathe"]).ok
    assert declared.validate_route(["mill", "lathe"]).ok
    assert declared.validate_route([]).ok
    result = declared.validate_route(["lathe", "foundry"])
    assert not result.ok and result.restricted
    assert result.allowed == ("lathe", "mill")
    assert "foundry" in result.reason and "lathe" not in result.reason.split("declared:")[0]


def test_route_matching_ignores_case_and_surrounding_spaces() -> None:
    declared = DeclaredSet([make_process("lathe")])
    assert declared.validate_route([" Lathe "]).ok


def test_material_matches_name_or_designation_ignoring_case_accents_and_spaces() -> None:
    declared = DeclaredSet([make_material("steel", name="Acero 1045", designations=["AISI 1045", "C45"])])
    for spelling in ["Acero 1045", "acero1045", "ACERO  1045", "aisi1045", "c45", "  C45 "]:
        result = declared.validate_material(spelling)
        assert result.ok and result.matched == "steel", spelling
    assert declared.validate_material("Áceró 1045").ok  # accents are ignored


def test_material_outside_the_declared_list_is_rejected_with_the_allowed_names() -> None:
    declared = DeclaredSet([make_material("steel", name="Steel 1045"), make_material("brass", name="Brass")])
    result = declared.validate_material("Aluminium 6061")
    assert not result.ok
    assert result.allowed == ("Brass", "Steel 1045")
    assert "Aluminium 6061" in result.reason
    assert not declared.validate_material("   ").ok


def test_union_of_scopes_in_the_declared_set(store: MemoryStore) -> None:
    store.save(make_process("user-lathe"), Scope.user)
    store.save(make_material("steel"), Scope.user)
    store.save(make_process("project-mill", tag="milling_3axis", capabilities=[]), Scope.project)
    declared = DeclaredSet.from_store(store)
    assert [item.entry_id for item in declared.processes] == ["project-mill", "user-lathe"]
    assert [item.entry_id for item in declared.materials] == ["steel"]


def test_project_entry_overrides_the_user_one_by_id(store: MemoryStore) -> None:
    store.save(make_process("lathe"), Scope.user)
    store.save(make_process("lathe", capabilities=[{"name": "max_diameter_mm", "value": 80}]), Scope.project)
    process = DeclaredSet.from_store(store).process("lathe")
    assert process is not None and process.capabilities[0].value == 80


def test_inactive_project_entry_turns_off_the_user_one(store: MemoryStore) -> None:
    store.save(make_process("lathe"), Scope.user)
    store.save(make_material("steel"), Scope.user)
    assert DeclaredSet.from_store(store).processes_declared
    store.save(make_process("lathe", status="inactive"), Scope.project)
    declared = DeclaredSet.from_store(store)
    assert not declared.processes_declared  # back to "nothing restricts"
    assert declared.materials_declared  # the other kind is untouched
    assert declared.validate_route(["anything"]).ok


def test_from_store_with_empty_memory_restricts_nothing(store: MemoryStore) -> None:
    declared = DeclaredSet.from_store(store)
    assert not declared.processes_declared and not declared.materials_declared
