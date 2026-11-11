# ruff: noqa: E501
"""One truth for the sheet, end to end: the real server over stdio against the real FreeCAD (Windows).

A plate drawing gets a section view, cosmetic lines with colours Qt's PDF writer cannot print (outside 0..1) and a note, all
put on the page by ``run_native``. The capture and the PDF come from the same rendering and say the same things about the
sheet; ``capture`` zooms into a region; ``review`` does not call the sheet fine without the objects it cannot read; the PDF is
checked after it is written (``pdf_check``) and is sound. The same sheet in the headless CAD lists every native object it could
not draw instead of leaving it out.

Set ``COTAFORGE_TEST_SHOTS`` to a folder to keep the images and the PDFs.
"""

import base64
import os
import sys
from pathlib import Path

import pytest

from cotaforge_mcp import pdf_check
from tests.integration.conftest import Cad, finish_drawing, running_server
from tests.integration.test_drawing_finished_real import THICKNESS, make_plate

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the visible FreeCAD window is exercised on Windows")

SECTION = """
import FreeCAD as App
doc = App.ActiveDocument
page = [o for o in doc.Objects if o.TypeId == "TechDraw::DrawPage"][0]
front = [o for o in doc.Objects if o.TypeId == "TechDraw::DrawProjGroupItem" and o.Type == "Front"][0]
sec = doc.addObject("TechDraw::DrawViewSection", "SecNative")
page.addView(sec)
sec.BaseView = front
sec.Source = front.Source
sec.ScaleType = "Custom"
sec.Scale = 1.0
sec.SectionOrigin = App.Vector(0, 0, 9.5)
sec.SectionNormal = App.Vector(0, 1, 0)
sec.Direction = App.Vector(0, 1, 0)
sec.SectionDirection = "Down"
sec.SectionSymbol = "A"
sec.X = 250
sec.Y = 100
doc.recompute()
"""

BAD_COLOURS = """
import FreeCAD as App
doc = App.ActiveDocument
page = [o for o in doc.Objects if o.TypeId == "TechDraw::DrawPage"][0]
sec = doc.getObject("SecNative")
for i, colour in enumerate(((255.0, 0.0, 0.0, 0.0), (-1.0, 0.0, 1.0, 0.0))):
    tag = sec.makeCosmeticLine(App.Vector(-20, 5 - i * 5, 0), App.Vector(20, 5 - i * 5, 0))
    sec.getCosmeticEdge(tag).Format = {"style": 1, "weight": 0.7, "color": colour, "visible": True}
note = doc.addObject("TechDraw::DrawViewAnnotation", "NoteNative")
page.addView(note)
note.Text = ["nota nativa"]
note.X, note.Y = 100, 200
doc.recompute()
"""


async def sheet_with_natives(cad: Cad) -> None:
    await make_plate(cad)
    await cad.ok("drawing", action="create", template="A3", scale="1:1", title="Placa 19")
    await cad.ok("drawing", action="add", items=[{"type": "view_lines"}])
    await cad.ok("run_native", code=SECTION, intent="a section view of the plate, made natively")
    await cad("drawing", action="capture")  # the sheet is computed (a view made by a script has no geometry before)
    await cad.ok("run_native", code=BAD_COLOURS, intent="cosmetic lines with colours outside 0..1, and a note")


def keep(folder: Path, name: str, reply_images: list) -> None:  # type: ignore[type-arg]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(base64.b64decode(reply_images[0].data))


async def test_the_capture_and_the_pdf_are_one_rendering_with_the_native_objects_in_both(
    workspace: Path, tmp_path: Path
) -> None:
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    async with running_server(workspace, visible=True) as (cad, _):
        await sheet_with_natives(cad)
        assert THICKNESS == 19

        shot = await cad("drawing", action="capture")
        assert not shot.is_error, shot.text
        keep(shots, "natives.png", shot.images)
        assert "TechDraw's own scene of the sheet window (the PDF is written from the same scene)" in shot.text
        assert "views on the sheet: front, top, right, isometric, SecNative (native)" in shot.text
        assert "SecNative [DrawViewSection]: section A" in shot.text and "NoteNative [DrawViewAnnotation]" in shot.text
        assert "2 cosmetic line(s) on SecNative" in shot.text
        assert "colour outside 0..1; normalised" in shot.text and "NOT DRAWN" not in shot.text

        zoom = await cad("drawing", action="capture", region=[190, 60, 310, 140])
        assert not zoom.is_error and "zoom into x 190..310, y 60..140 mm" in zoom.text
        keep(shots, "zoom.png", zoom.images)
        assert (await cad.err("drawing", action="capture", region=[0, 0, 900, 100])).count("region") >= 1

        listed = await cad.ok("drawing", action="list")
        assert "native TechDraw objects (" in listed and "NoteNative [DrawViewAnnotation]: text: nota nativa" in listed

        review = await cad.ok("drawing", action="review")
        assert "native_unread" in review and "no puede leer" in review and "review: nothing found" not in review

        await finish_drawing(cad)  # the review is closed the way an agent that has decided none applies would
        pdf = workspace / "placa.pdf"
        exported = await cad.ok("export", action="pdf", path=str(pdf))
        assert "TechDraw's own export of the sheet window" in exported
        assert "checked: one page, every operator sound, ink in all 5 view(s) of the sheet" in exported
        assert "native TechDraw objects on the sheet" in exported and "NOT DRAWN" not in exported
        report = pdf_check.check_pdf(pdf, pages=1)
        assert report.ok, report.summary()
        shots.mkdir(parents=True, exist_ok=True)
        (shots / "natives.pdf").write_bytes(pdf.read_bytes())


async def test_the_headless_cad_names_every_native_object_it_cannot_draw(workspace: Path) -> None:
    async with running_server(workspace, visible=False) as (cad, _):
        await sheet_with_natives(cad)
        shot = await cad("drawing", action="capture")
        assert not shot.is_error, shot.text
        assert "this CAD is headless" in shot.text and "NOT DRAWN here (3)" in shot.text
        for name in ("SecNative", "NoteNative", "2 cosmetic line(s)"):
            assert name in shot.text
        await finish_drawing(cad)
        pdf = workspace / "placa.pdf"
        exported = await cad.ok("export", action="pdf", path=str(pdf))
        assert "cotaforge's own writer of the sheet" in exported and "NOT DRAWN here (3)" in exported
        assert pdf_check.check_pdf(pdf, pages=1).ok
