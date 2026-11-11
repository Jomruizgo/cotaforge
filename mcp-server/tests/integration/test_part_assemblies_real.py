"""The workflow of an assembly of part documents on the real FreeCAD through the real server: each part is modelled
with features in its own document and axes, saved, and placed in an assembly (links to the part files); the assembly
is saved, the server restarted, and the assembly reopens with its parts; a part saved again shows in it."""

from pathlib import Path

import pytest

from tests.integration.conftest import Cad, running_server, volume


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COTAFORGE_HOME", str(tmp_path / "home"))


BLOCK_SKETCH = {
    "on": {"kind": "plane", "name": "XY"},
    "entities": [{"kind": "rect", "name": "outline", "center": [0, 0], "width": 40, "height": 40}],
}
PIN_SKETCH = {
    "on": {"kind": "plane", "name": "XY"},
    "entities": [{"kind": "circle", "name": "section", "center": [0, 0], "diameter": 6}],
}
BORE = {
    "face": {"kind": "face", "from_op": "block_pad", "select": {"normal": [0, 0, 1]}},
    "position": [0, 0],
    "standard": "simple",
    "diameter_mm": 6.5,
    "end_condition": "through_all",
}
BLOCK_VOLUME = 40 * 40 * 20 - 3.14159265 * 3.25**2 * 20
PIN_VOLUME = 3.14159265 * 3**2 * 30


async def model_parts(cad: Cad) -> None:
    await cad.ok("doc", action="new", name="block")
    await cad.ok("op", name="sketch", id="block_sketch", params=BLOCK_SKETCH)
    await cad.ok("op", name="extrude", id="block_pad", params={"sketch": "block_sketch", "depth_mm": 20})
    await cad.ok("op", name="hole", id="bore", params=BORE)
    await cad.ok("doc", action="save", path="parts/block.FCStd")
    await cad.ok("doc", action="new", name="pin")
    await cad.ok("op", name="sketch", id="pin_sketch", params=PIN_SKETCH)
    await cad.ok("op", name="extrude", id="pin_pad", params={"sketch": "pin_sketch", "depth_mm": 30})
    await cad.ok("doc", action="save", path="parts/pin.FCStd")


async def test_parts_in_their_own_documents_are_placed_checked_saved_and_reopened(workspace: Path) -> None:
    async with running_server(workspace) as (cad, _):
        await model_parts(cad)
        await cad.ok("doc", action="new", name="pinned_block", kind="assembly")

        first = await cad.ok(
            "op", name="insert_component", id="block_i", params={"part": "block", "placement": {"origin": [0, 0, 0]}}
        )
        assert first.startswith("saved the assembly first") and volume(first) == pytest.approx(BLOCK_VOLUME, rel=1e-4)
        await cad.ok(
            "op",
            name="insert_component",
            id="pin_i",
            params={"part": "pin", "placement": {"origin": [100, 0, 0]}},
        )
        apart = await cad.ok("inspect", action="interference")
        assert apart.startswith("interference: 2 components, 1 pair: 0 overlap, 1 clear"), apart
        assert "clear, min distance" in apart

        moved = await cad.ok(
            "op", name="place_component", id="seat", params={"component": "pin_i", "placement": {"origin": [0, 0, 10]}}
        )
        assert moved.startswith("ok: seat place_component")
        together = await cad.ok("inspect", action="interference")
        assert "OVERLAP" not in together, together  # the pin is in the bore of the block (6 in 6.5): no common volume
        assert "clear, min distance 0.25 mm" in together, together

        saved = await cad.ok("doc", action="save")
        assert "warning" not in saved, saved
        assert (workspace / "pinned_block.FCStd").is_file() and (workspace / "pinned_block.cotaforge.json").is_file()

    # a new server, nothing open: the assembly reopens with its parts and the steps keep working
    async with running_server(workspace) as (cad, _):
        opened = await cad.ok("doc", action="open", path="pinned_block.FCStd")
        assert opened.startswith("document: pinned_block (assembly)"), opened
        assert "session restored: step ids and handles kept" in opened
        assert "other open documents:" in opened and "block (part)" in opened and "pin (part)" in opened
        assert volume(opened) == pytest.approx(BLOCK_VOLUME + PIN_VOLUME, rel=1e-4)
        again = await cad.ok("inspect", action="interference")
        assert "clear, min distance 0.25 mm" in again, again

        # the parts are open and editable: the pin is still a feature-based part with its own steps
        await cad.ok("doc", action="switch", name="pin")
        assert "pin_pad" in await cad.ok("inspect", action="tree")


async def test_a_part_changed_after_it_was_inserted_updates_the_assembly(workspace: Path) -> None:
    async with running_server(workspace) as (cad, _):
        await model_parts(cad)
        await cad.ok("doc", action="new", name="asm", kind="assembly")
        await cad.ok("op", name="insert_component", id="pin_i", params={"part": "pin", "placement": {}})
        before = volume(await cad.ok("inspect", action="measure"))
        assert before == pytest.approx(PIN_VOLUME, rel=1e-4)

        await cad.ok("doc", action="switch", name="pin")
        await cad.ok("op", name="box", id="foot", params={"size": [10, 10, 10], "placement": {"origin": [20, 0, 0]}})
        await cad.ok("doc", action="save")
        back = await cad.ok("doc", action="switch", name="asm")

        assert "components updated from their part documents" in back, back
        assert volume(back) == pytest.approx(PIN_VOLUME + 1000, rel=1e-4)


async def test_the_part_is_refused_until_it_is_saved_and_cannot_be_closed_while_used(workspace: Path) -> None:
    async with running_server(workspace) as (cad, _):
        await cad.ok("doc", action="new", name="loose")
        await cad.ok("op", name="box", params={"size": [5, 5, 5]})
        await cad.ok("doc", action="new", name="asm", kind="assembly")
        refused = await cad.err("op", name="insert_component", params={"part": "loose", "placement": {}})
        assert "never been saved" in refused

        await cad.ok("doc", action="switch", name="loose")
        await cad.ok("doc", action="save", path="loose.FCStd")
        await cad.ok("doc", action="switch", name="asm")
        await cad.ok("op", name="insert_component", id="i", params={"part": "loose", "placement": {}})
        await cad.ok("doc", action="switch", name="loose")
        assert "part of the open assembly asm" in await cad.err("doc", action="close")
