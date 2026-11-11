"""``check`` and the export block: findings of the measured part against the declared set."""

from pathlib import Path

from cotaforge_mcp.config import Settings
from tests.conftest import Caller, ExportFake, MakeSession, declare, material_entry, plate, process_entry


def exporting(make: MakeSession) -> Caller:
    return Caller(make(ExportFake()))


def test_with_nothing_declared_nothing_restricts(make: MakeSession, tmp_path: Path) -> None:
    call = exporting(make)
    plate(call)
    report = call.ok("check")
    assert report.startswith("check: 0 violation, 0 unverified, 0 pass")
    assert "nothing is declared" in report and "geometry: valid" in report and "export: allowed" in report
    assert "exported step:" in call.ok("export", action="step", path="p.step")
    assert (tmp_path / "p.step").is_file()


def test_unassigned_material_and_route_are_unverified_and_never_block(make: MakeSession, settings: Settings) -> None:
    declare(settings, material_entry(), process_entry(max_diameter=50.0))
    call = exporting(make)
    plate(call)
    report = call.ok("check")
    assert "0 violation, 2 unverified, 0 pass" in report
    assert "UNVERIFIED: material - no material is assigned to the part; declared: Acero 1045" in report
    assert "UNVERIFIED: route - no process route is assigned to the part; declared: torno-a" in report
    assert "export: allowed" in report
    assert "exported step:" in call.ok("export", action="step")


def test_a_violation_blocks_export_and_only_export(make: MakeSession, settings: Settings, tmp_path: Path) -> None:
    declare(settings, material_entry(), process_entry(max_diameter=50.0))
    call = exporting(make)
    plate(call)  # 100 x 60 x 8: the diameter (the larger of the two shorter extents) is 60
    call.ok("doc", action="set_material", material="C45")
    call.ok("doc", action="set_route", route=["torno-a"])

    report = call.ok("check")
    assert "1 violation, 0 unverified, 2 pass" in report
    assert (
        "VIOLATION: envelope.max_diameter_mm | measured 60 | limit 50" in report
        and "[torno-a, Tabla 2026-03]" in report
    )
    assert "export: BLOCKED by 1 violation(s); editing and doc save are still allowed" in report

    for action in ("step",):
        refused = call.err("export", action=action)
        assert refused.startswith("error: export_blocked") and "envelope.max_diameter_mm" in refused
        assert "doc(action=save) are still allowed" in refused
    assert not (tmp_path / "plate.step").exists()

    assert call.ok("doc", action="save", path="p.fake").startswith("saved:")  # saving is allowed
    assert call.ok("op", name="box", params={"size": [1, 1, 1]}).startswith("ok:")  # so is editing
    assert call.ok("undo").startswith("undone:")

    declare(settings, process_entry(max_diameter=80.0))  # the engineer re-declares the lathe with its real capacity
    assert "export: allowed" in call.ok("check")
    assert "exported step:" in call.ok("export", action="step", path="ok.step")


def test_a_placeholder_limit_is_pending_not_a_violation(make: MakeSession, settings: Settings) -> None:
    entry = process_entry(max_diameter=10.0)
    entry["process"]["capabilities"][0]["placeholder"] = True
    declare(settings, material_entry(), entry)
    call = exporting(make)
    plate(call)
    call.ok("doc", action="set_route", route=["torno-a"])
    report = call.ok("check")
    assert "0 violation" in report and "placeholder" in report and "export: allowed" in report


def test_native_steps_downgrade_a_pass_to_unverified(make: MakeSession, settings: Settings) -> None:
    declare(settings, process_entry(max_diameter=200.0))
    call = exporting(make)
    plate(call)
    call.ok("doc", action="set_route", route=["torno-a"])
    assert "2 pass" in call.ok("check") and "PASS: envelope.max_diameter_mm" in call.ok("check")
    call.ok("run_native", code="box 5", intent="a block")
    after = call.ok("check")
    assert (
        "1 unverified, 1 pass" in after
        and "UNVERIFIED: envelope.max_diameter_mm" in after
        and "native steps are in effect" in after
    )
    call.ok("undo")
    assert "PASS: envelope.max_diameter_mm" in call.ok("check")


def test_check_needs_a_document(make: MakeSession) -> None:
    assert "no document is open" in exporting(make).err("check")


class NotingFake(ExportFake):
    def check_notes(self) -> tuple[str, ...]:
        return ("sketches: 1 of 2 not fully defined (Sketch2)",)


def test_the_cad_adds_its_own_lines_to_the_report_and_they_do_not_block(make: MakeSession) -> None:
    call = Caller(make(NotingFake()))
    plate(call)
    report = call.ok("check")
    assert "sketches: 1 of 2 not fully defined (Sketch2)" in report and "export: allowed" in report


def test_the_file_guidance_names_the_extension_of_the_cad_in_use(make: MakeSession) -> None:
    call = exporting(make)
    suffix = call.session.adapter.document_suffix
    assert f"part{suffix}" in call.err("doc", action="open") and ".FCStd" not in call.err("doc", action="open")
