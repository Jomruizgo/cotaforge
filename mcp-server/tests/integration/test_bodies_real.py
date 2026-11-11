"""Several bodies in one real FreeCAD part: interference between them, solids that only touch are valid, and a body
named by its step or by its id."""

from pathlib import Path

import pytest

from tests.integration.conftest import running_server, volume


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COTAFORGE_HOME", str(tmp_path / "home"))


CUBE = {"size": [10, 10, 10]}
TOUCHING = (  # one body made of two cubes that share a face and a third that shares only an edge
    'obj = doc.addObject("Part::Feature", "Touching")\n'
    "obj.Shape = Part.makeCompound([Part.makeBox(10, 10, 10, Vector(100, 0, 0)), "
    "Part.makeBox(10, 10, 10, Vector(110, 0, 0)), Part.makeBox(10, 10, 10, Vector(110, 10, 0))])\n"
)


async def test_the_bodies_of_a_part_are_measured_against_each_other(workspace: Path) -> None:
    async with running_server(workspace) as (cad, _):
        await cad.ok("doc", action="new", name="cubes")
        await cad.ok("op", name="box", id="a", params=CUBE)
        await cad.ok("op", name="box", id="b", params={**CUBE, "placement": {"origin": [5, 0, 0]}})
        await cad.ok("op", name="box", id="c", params={**CUBE, "placement": {"origin": [15, 0, 0]}})

        text = await cad.ok("inspect", action="interference")
        assert text.startswith("interference: 3 bodies, 3 pairs: 1 overlap (500 mm3), 1 touching, 1 clear"), text
        assert "[a] x " in text and "OVERLAP 500 mm3" in text and "clear, min distance 5 mm" in text

        assert volume(await cad.ok("inspect", action="properties", body="b")) == pytest.approx(1000)
        moved = await cad.ok("op", name="translate", id="away", params={"target": "a", "offset": [0, 0, 50]})
        assert moved.startswith("ok: away translate")
        again = await cad.ok("inspect", action="interference")
        assert "0 overlap" in again and "[away] x" in again and "clear, min distance 40 mm" in again


async def test_solids_that_only_touch_leave_the_part_valid(workspace: Path) -> None:
    async with running_server(workspace) as (cad, _):
        await cad.ok("doc", action="new", name="contact")
        await cad.ok("run_native", code=TOUCHING, intent="three cubes in contact, one body")
        status = await cad.ok("doc", action="status")
        assert "valid: yes" in status, status
