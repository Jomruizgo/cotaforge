"""``check`` and the export block on a real shaft: measured diameter against a declared lathe."""

from pathlib import Path

import pytest
from cotaforge.memory import MemoryEntry, MemoryStore, Scope

from tests.conftest import material_entry, process_entry
from tests.integration.conftest import running_server


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COTAFORGE_HOME", str(tmp_path / "home"))


def declare(workspace: Path, *entries: dict) -> None:
    folder = workspace / "memory"
    folder.mkdir(parents=True, exist_ok=True)
    store = MemoryStore(project_dir=folder)
    for data in entries:
        store.save(MemoryEntry.model_validate(data), Scope.project)


async def test_a_shaft_wider_than_the_declared_lathe_cannot_be_exported(workspace: Path) -> None:
    declare(workspace, material_entry(), process_entry("torno-a", max_diameter=200.0))
    async with running_server(workspace) as (cad, _):
        await cad.ok("doc", action="new", name="shaft")
        await cad.ok("doc", action="set_material", material="Acero 1045")
        await cad.ok("doc", action="set_route", route=["torno-a"])
        await cad.ok("op", name="cylinder", id="wide", params={"diameter_mm": 250, "height_mm": 400})

        report = await cad.ok("check")
        assert "1 violation" in report and "VIOLATION: envelope.max_diameter_mm | measured 250 | limit 200" in report
        assert "PASS: material" in report and "export: BLOCKED" in report
        for action in ("step", "mesh", "macro"):
            refused = await cad.err("export", action=action, path=f"shaft.{action}")
            assert refused.startswith("error: export_blocked") and "max_diameter_mm" in refused
        assert not list(workspace.glob("shaft.*"))

        assert (await cad.ok("doc", action="save", path="shaft.FCStd")).startswith("saved:")  # saving is allowed
        await cad.ok("undo")
        fixed = await cad.ok("op", name="cylinder", id="narrow", params={"diameter_mm": 150, "height_mm": 400})
        assert "volume:" in fixed
        again = await cad.ok("check")
        assert "0 violation" in again and "PASS: envelope.max_diameter_mm | measured 150 | limit 200" in again
        step = await cad.ok("export", action="step", path="shaft.step")
        assert (workspace / "shaft.step").stat().st_size > 200 and "shaft.step" in step


async def test_with_nothing_declared_export_is_never_blocked(workspace: Path) -> None:
    async with running_server(workspace) as (cad, _):
        await cad.ok("doc", action="new", name="free")
        await cad.ok("op", name="cylinder", params={"diameter_mm": 900, "height_mm": 50})
        report = await cad.ok("check")
        assert "nothing is declared" in report and "export: allowed" in report
        assert (workspace / "free.step").exists() is False
        await cad.ok("export", action="step", path="free.step")
        assert (workspace / "free.step").is_file()
