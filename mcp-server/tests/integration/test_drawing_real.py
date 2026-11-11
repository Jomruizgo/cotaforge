"""The drawing sheet end to end on the real headless FreeCAD, through the stdio server: made, looked at, written as
PDF and DXF without any window, kept by save + reopen; the window-only display actions are refused with a reason."""

import base64
from pathlib import Path

from tests.integration.conftest import finish_drawing, running_server

PNG = b"\x89PNG\r\n\x1a\n"


async def test_a_drawing_is_made_looked_at_and_written_without_a_window(workspace: Path) -> None:
    async with running_server(workspace) as (cad, init):
        assert init.instructions is not None and "drawing" in init.instructions.split("tools:")[1].splitlines()[0]
        await cad.ok("doc", action="new", name="plate")
        await cad.ok("op", name="box", params={"size": [60, 40, 20]}, intent="the plate")

        text = await cad.ok("drawing", action="create", template="A4", title="Placa", author="Ana", scale="1:1")
        assert "A4 sheet at 1:1" in text and "width 60 mm, height 20 mm, depth 40 mm" in text
        assert "finished computing" in text

        shot = await cad("drawing", action="capture")
        assert not shot.is_error, shot.text
        assert len(shot.images) == 1 and shot.images[0].mimeType == "image/png"
        assert base64.b64decode(shot.images[0].data)[:8] == PNG

        assert "drawing_unfinished" in await cad.err("export", action="pdf", path="plate.pdf")  # asked, not answered
        assert not (workspace / "plate.pdf").exists()
        await finish_drawing(cad)
        pdf = await cad.ok("export", action="pdf", path="plate.pdf")
        assert "exported pdf" in pdf
        data = (workspace / "plate.pdf").read_bytes()
        assert data.startswith(b"%PDF") and len(data) > 20000  # a drawn sheet, not the blank page of an unopened one
        await cad.ok("export", action="dxf", path="plate.dxf")
        assert (workspace / "plate.dxf").stat().st_size > 2000
        assert "already exists" in await cad.err("export", action="pdf", path="plate.pdf")


async def test_the_sheet_is_part_of_the_document_and_survives_save_and_reopen(workspace: Path) -> None:
    async with running_server(workspace) as (cad, _):
        await cad.ok("doc", action="new", name="plate")
        await cad.ok("op", name="box", params={"size": [60, 40, 20]})
        await cad.ok("drawing", action="create", title="Placa")
        await cad.ok("doc", action="save", path="plate.FCStd")
        await cad.ok("doc", action="close")
        await cad.ok("doc", action="open", path="plate.FCStd")
        shot = await cad("drawing", action="capture")
        assert not shot.is_error and base64.b64decode(shot.images[0].data)[:8] == PNG
        await finish_drawing(cad)
        await cad.ok("export", action="pdf", path="reopened.pdf")
        assert (workspace / "reopened.pdf").read_bytes().startswith(b"%PDF")
        await cad.ok("drawing", action="create", title="Otra vez", template="A3")  # and it can be rebuilt


async def test_rebuilding_the_sheet_after_changing_the_part_follows_the_part(workspace: Path) -> None:
    async with running_server(workspace) as (cad, _):
        await cad.ok("doc", action="new", name="plate")
        await cad.ok("op", name="box", params={"size": [60, 40, 20]})
        assert "height 20 mm" in await cad.ok("drawing", action="create")
        await cad.ok("undo")
        await cad.ok("op", name="box", params={"size": [60, 40, 50]})
        assert "height 50 mm" in await cad.ok("drawing", action="create")  # replaced, not stacked


async def test_a_drawing_needs_a_part_and_a_sheet_to_export(workspace: Path) -> None:
    async with running_server(workspace) as (cad, _):
        await cad.ok("doc", action="new", name="empty")
        assert "no body" in await cad.err("drawing", action="create")
        await cad.ok("op", name="box", params={"size": [10, 10, 10]})
        text = await cad.err("export", action="pdf", path="none.pdf")
        assert "create it first" in text
        assert not (workspace / "none.pdf").exists()


async def test_the_window_only_display_actions_are_refused_with_the_reason(workspace: Path) -> None:
    async with running_server(workspace) as (cad, init):
        assert init.instructions is not None and "not available here" in init.instructions
        await cad.ok("doc", action="new", name="plate")
        await cad.ok("op", name="box", params={"size": [10, 10, 10]})
        for arguments in (
            {"action": "style", "ids": ["s1"], "transparency": 50},
            {"action": "section", "axis": "z", "offset_mm": 3},
            {"action": "set_view", "name": "rear"},
        ):
            text = await cad.err("view", **arguments)
            assert "not_supported" in text or "not available" in text
            assert "not available with this CAD" in text
