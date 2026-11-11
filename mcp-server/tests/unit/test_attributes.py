"""``doc set_material`` / ``doc set_route``: declared-set validation at declaration time, persistence with the file and
the refusal of undeclared changes made by a native step."""

from cotaforge_mcp.config import Settings
from cotaforge_mcp.schemas import TOOLS_BY_NAME, input_schema
from cotaforge_mcp.session import Session
from tests.conftest import (
    BareFake,
    Caller,
    LossyFake,
    MakeSession,
    NativeFake,
    declare,
    material_entry,
    plate,
    process_entry,
)


def test_nothing_declared_nothing_restricted(call: Caller) -> None:
    call.ok("doc", action="new", name="p")
    assert "material: none" in call.ok("doc", action="status") and "route: none" in call.ok("doc", action="status")
    assert call.ok("doc", action="set_material", material="Latón") == "material: Latón\nroute: none"
    assert call.ok("doc", action="set_route", route=["whatever", "else"]).endswith("route: whatever, else")
    assert "material: Latón" in call.ok("doc", action="status") and "UNSAVED" in call.ok("doc", action="status")
    cleared = call.ok("doc", action="set_material", material="")
    assert cleared.startswith("material: none")
    assert call.ok("doc", action="set_route", route=[]).endswith("route: none")


def test_a_material_outside_the_declared_set_is_refused_at_once(call: Caller, settings: Settings) -> None:
    declare(settings, material_entry(), material_entry("aluminio-6061", "Aluminio 6061", "6061-T6"))
    call.ok("doc", action="new", name="p")
    refused = call.err("doc", action="set_material", material="Titanio")
    assert refused.startswith("error: outside_declared") and "field: material" in refused
    assert "declared: ['Acero 1045', 'Aluminio 6061']" in refused and "memory(action=save" in refused
    assert "material: none" in call.ok("doc", action="status")  # nothing was assigned

    named = call.ok("doc", action="set_material", material="acero 1045")
    assert named.startswith("material: acero 1045 (declared: acero-1045)")
    assert "(declared: aluminio-6061)" in call.ok("doc", action="set_material", material="6061-t6")
    assert "declared: Acero 1045, Aluminio 6061" in call.ok("doc", action="set_material", material="")


def test_a_route_must_be_a_subset_of_the_declared_processes(call: Caller, settings: Settings) -> None:
    declare(settings, process_entry("torno-a"), process_entry("fresa-b"))
    call.ok("doc", action="new", name="p")
    refused = call.err("doc", action="set_route", route=["torno-a", "fundicion"])
    assert "outside_declared" in refused and "fundicion" in refused and "torno-a" in refused
    assert call.ok("doc", action="set_route", route=["torno-a", "fresa-b"]).endswith("route: torno-a, fresa-b")
    assert "field: route" in call.err("doc", action="set_route")
    assert "field: material" in call.err("doc", action="set_material")


def test_user_scope_declarations_count_and_project_wins(call: Caller, settings: Settings) -> None:
    declare(settings, material_entry("acero-1045"), scope="user")
    call.ok("doc", action="new", name="p")
    assert call.ok("doc", action="set_material", material="C45").startswith("material: C45")
    inactive = {**material_entry("acero-1045"), "status": "inactive"}
    declare(settings, inactive)  # the project turns the user's entry off: nothing of this kind is declared now
    assert call.ok("doc", action="set_material", material="Titanio").startswith("material: Titanio")


def test_they_come_back_with_the_file(call: Caller, settings: Settings) -> None:
    declare(settings, material_entry(), process_entry())
    plate(call)
    call.ok("doc", action="set_material", material="C45")
    call.ok("doc", action="set_route", route=["torno-a"])
    call.ok("doc", action="save", path="p.fake")
    call.ok("doc", action="close")
    opened = call.ok("doc", action="open", path="p.fake")
    assert "material: C45 (declared: acero-1045)" in opened and "route: torno-a" in opened


def test_the_session_record_puts_them_back_when_the_cad_file_lost_them(make: MakeSession, settings: Settings) -> None:
    call = Caller(make(LossyFake()))
    plate(call)
    call.ok("doc", action="set_material", material="Latón")
    call.ok("doc", action="set_route", route=["a", "b"])
    call.ok("doc", action="save", path="p.fake")
    call.ok("doc", action="close")
    opened = call.ok("doc", action="open", path="p.fake")
    assert "material: Latón" in opened and "route: a, b" in opened


def test_the_actions_exist_only_when_the_cad_stores_them(session: Session, make: MakeSession) -> None:
    assert "set_material" in input_schema(TOOLS_BY_NAME["doc"], session)["properties"]["action"]["enum"]
    bare = make(BareFake())
    assert "set_material" not in input_schema(TOOLS_BY_NAME["doc"], bare)["properties"]["action"]["enum"]
    call = Caller(bare)
    call.ok("doc", action="new", name="p")
    assert "not available with this CAD" in call.err("doc", action="set_material", material="x")


# --- native steps ----------------------------------------------------------------------------------------------------


def test_a_native_step_cannot_introduce_an_undeclared_material(make: MakeSession, settings: Settings) -> None:
    declare(settings, material_entry())
    call = Caller(make(NativeFake()))
    plate(call)
    call.ok("doc", action="set_material", material="Acero 1045")
    steps = [s.id for s in call.session.steps()]

    refused = call.err("run_native", code="box-and-material 5 Titanio", intent="relabel")
    assert refused.startswith("error: outside_declared") and "s3 native was undone" in refused
    assert "Titanio" in refused and "state: unchanged" in refused
    assert [s.id for s in call.session.steps()] == steps  # no step, and the adapter undid its box
    assert call.session.design_attributes().material == "Acero 1045"
    assert call.ok("inspect", action="measure").count("body:") == 1

    metadata_only = call.err("run_native", code="set-material Titanio")
    assert "outside_declared" in metadata_only and call.session.design_attributes().material == "Acero 1045"

    allowed = call.ok("run_native", code="box-and-material 5 C45", intent="re-labelled with a designation")
    assert allowed.startswith("ok:") and call.session.design_attributes().material == "C45"


def test_without_declarations_a_native_change_of_material_is_fine(make: MakeSession) -> None:
    call = Caller(make(NativeFake()))
    plate(call)
    assert call.ok("run_native", code="set-material Titanio").startswith("ok:")
    assert call.session.design_attributes().material == "Titanio"
