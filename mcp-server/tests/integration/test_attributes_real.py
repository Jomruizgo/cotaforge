"""Material and route against the real FreeCAD through the real server: declared-set validation, persistence in the
native file (stamped round trip and adoption), and a native fragment that tries to change them."""

from pathlib import Path

import pytest
from cotaforge.memory import MemoryEntry, MemoryStore, Scope

from tests.conftest import material_entry, process_entry
from tests.integration.conftest import Cad, running_server, volume

SKETCH = {
    "on": {"kind": "plane", "name": "XY"},
    "entities": [{"kind": "rect", "name": "outline", "center": [30, 20], "width": 60, "height": 40}],
}
FIND_BODY = "o = [o for o in doc.Objects if 'CotaforgeMaterial' in o.PropertiesList][0]\n"
META_MATERIAL = "doc.Meta = dict(doc.Meta, **{'cotaforge_design.material': '%s'})"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COTAFORGE_HOME", str(tmp_path / "home"))  # the server child inherits it


def declare(workspace: Path, *entries: dict) -> None:
    folder = workspace / "memory"
    folder.mkdir(parents=True, exist_ok=True)
    store = MemoryStore(project_dir=folder)
    for data in entries:
        store.save(MemoryEntry.model_validate(data), Scope.project)


async def plate(cad: Cad) -> None:
    await cad.ok("doc", action="new", name="plate")
    await cad.ok("op", name="sketch", params=SKETCH)
    await cad.ok("op", name="extrude", params={"sketch": "s1", "depth_mm": 10})


async def test_declared_material_and_route_are_enforced_when_assigned(workspace: Path) -> None:
    declare(workspace, material_entry(), process_entry())
    async with running_server(workspace) as (cad, _):
        await plate(cad)
        refused = await cad.err("doc", action="set_material", material="Titanio")
        assert "outside_declared" in refused and "Acero 1045" in refused
        assert "material: C45 (declared: acero-1045)" in await cad.ok("doc", action="set_material", material="C45")
        assert "outside_declared" in await cad.err("doc", action="set_route", route=["fundicion"])
        assert "route: torno-a" in await cad.ok("doc", action="set_route", route=["torno-a"])
        status = await cad.ok("doc", action="status")
        assert "material: C45" in status and "route: torno-a" in status and "UNSAVED" in status


async def test_they_live_in_the_fcstd_and_come_back_stamped_and_adopted(workspace: Path) -> None:
    declare(workspace, material_entry(), process_entry())
    async with running_server(workspace) as (cad, _):
        await plate(cad)
        await cad.ok("doc", action="set_material", material="Acero 1045")
        await cad.ok("doc", action="set_route", route=["torno-a"])
        await cad.ok("doc", action="save", path="plate.FCStd")
        await cad.ok("doc", action="close")

        restored = await cad.ok("doc", action="open", path="plate.FCStd")
        assert "step ids and handles kept" in restored
        assert "material: Acero 1045 (declared: acero-1045)" in restored and "route: torno-a" in restored
        await cad.ok("doc", action="close")

        (workspace / "plate.cotaforge.json").unlink()  # no session record: the file is adopted, the attributes stay
        adopted = await cad.ok("doc", action="open", path="plate.FCStd")
        assert "ADOPTED" in adopted
        assert "material: Acero 1045 (declared: acero-1045)" in adopted and "route: torno-a" in adopted


async def test_a_native_fragment_cannot_introduce_an_undeclared_material(workspace: Path) -> None:
    declare(workspace, material_entry())
    async with running_server(workspace) as (cad, _):
        await plate(cad)
        await cad.ok("doc", action="set_material", material="Acero 1045")

        relabel = FIND_BODY + "o.CotaforgeMaterial = 'Titanio'"
        refused = await cad.err("run_native", code=relabel, intent="relabel the body")
        assert "outside_declared" in refused and "s3 native was undone" in refused and "Titanio" in refused
        status = await cad.ok("doc", action="status")
        assert "material: Acero 1045 (declared: acero-1045)" in status and "steps: 2" in status

        meta_only = META_MATERIAL % "Latón"
        assert "outside_declared" in await cad.err("run_native", code=meta_only)
        assert "material: Acero 1045" in await cad.ok("doc", action="status")

        half = await cad.err("run_native", code=FIND_BODY + "o.CotaforgeMaterial = 'C45'")
        assert "Acero 1045 / C45" in half  # the body and the document metadata must agree

        both = FIND_BODY + "o.CotaforgeMaterial = 'C45'\n" + META_MATERIAL % "C45"
        allowed = await cad.ok("run_native", code=both, intent="a declared designation")
        assert allowed.startswith("ok:") and "material: C45" in await cad.ok("doc", action="status")
        assert volume(await cad.ok("inspect", action="properties")) == pytest.approx(60 * 40 * 10, rel=1e-6)
