# ruff: noqa: E501
"""One truth for the sheet in the VISIBLE CAD: the capture is painted from the same window scene the PDF is written from, so a
section view, a dimension and a note a native script put on the page are in both; the reply lists them; a zoom into a
region shows a part of the sheet; and a native object with a colour Qt's PDF writer cannot print (outside 0..1) is
normalised before the PDF is written, so no colour operator comes out without operands (the file that made the engineer's
viewer stop after the first view).

Needs a display (on Windows the GUI binary opens its own window); skipped without one, carries the ``visible`` marker (run
serially). Set ``COTAFORGE_TEST_SHOTS`` to a folder to keep the images and the PDF.
"""

from __future__ import annotations

import os
import re
import sys
import zlib
from pathlib import Path

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.contract import (
    AnnotateDrawingRequest,
    CaptureDrawingRequest,
    DescribeDrawingRequest,
    DrawingRequest,
    ExportDrawingRequest,
    RunNativeRequest,
)
from tests.integration.freecad.test_visible_live_sheet import _build_plate, visible  # noqa: F401 - the fixture and the plate

pytestmark = [
    pytest.mark.freecad,
    pytest.mark.visible,
    pytest.mark.skipif(
        sys.platform != "win32" and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")),
        reason="the visible worker needs a display (DISPLAY / WAYLAND_DISPLAY)",
    ),
]

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
sec.SectionOrigin = App.Vector(50, 30, 9.5)
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


def _operators_without_operands(pdf: bytes) -> list[bytes]:
    """The ``scn`` / ``SCN`` operators that have nothing before them on their line in the page content (Qt writes the
    components and the operator on one line): what poppler reports as an incorrect number of arguments."""
    found: list[bytes] = []
    for stream in re.findall(rb"stream\r?\n(.*?)\r?\nendstream", pdf, re.S):
        try:
            data = zlib.decompress(stream)
        except zlib.error:
            continue
        found += re.findall(rb"(?:^|\n|cs |CS )(scn|SCN)(?=\s)", data)
    return found


def test_capture_and_pdf_come_from_one_scene_with_the_native_objects_in_both(
    visible: FreeCADAdapter,  # noqa: F811
    tmp_path: Path,
) -> None:
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path)
    shots.mkdir(parents=True, exist_ok=True)
    _build_plate(visible)
    visible.create_drawing(DrawingRequest(operation_id="dr", template="A3", title="Placa"))
    visible.annotate_drawing(
        AnnotateDrawingRequest(operation_id="a1", action="add", items=({"type": "hole_dim", "id": "all", "all": True},))
    )

    # a sheet of items only: nothing native, four views, the same answer from the capture and from the PDF
    plain = visible.capture_drawing(CaptureDrawingRequest(operation_id="c0", path=str(shots / "plain.png"), width=1600))
    assert plain.renderer == "techdraw" and plain.native == () and plain.unrendered == ()
    assert [v.name for v in plain.views] == ["front", "top", "right", "isometric"]
    assert plain.size_px == (1600, round(1600 * 297 / 420))

    native = visible.run_native(RunNativeRequest(operation_id="n1", code=SECTION, intent="a section view"))
    assert native.ok, native.error
    visible.capture_drawing(  # a view made by a script has no geometry until the sheet is computed (a capture waits for it)
        CaptureDrawingRequest(operation_id="c05", path=str(shots / "section.png"), width=800)
    )
    bad = visible.run_native(RunNativeRequest(operation_id="n2", code=BAD_COLOURS, intent="lines with bad colours"))
    assert bad.ok, bad.error

    shot = visible.capture_drawing(
        CaptureDrawingRequest(operation_id="c1", path=str(shots / "natives.png"), width=1600)
    )
    pdf_path = shots / "natives.pdf"
    exported = visible.export_drawing(ExportDrawingRequest(operation_id="e1", path=str(pdf_path), format="pdf"))
    for reply in (shot, exported):
        assert reply.renderer == "techdraw" and reply.unrendered == ()  # the scene draws everything on the page
        assert [v.name for v in reply.views] == ["front", "top", "right", "isometric", "SecNative"]
        assert reply.views[-1].kind == "native"
        text = " | ".join(reply.native)
        assert "SecNative [DrawViewSection]" in text and "NoteNative [DrawViewAnnotation]: text: nota nativa" in text
        assert "2 cosmetic line(s) on SecNative" in text
    assert shot.views == exported.views and shot.native == exported.native and shot.page_mm == exported.page_mm
    assert any("colour outside 0..1; normalised" in w for w in shot.warnings + exported.warnings)

    pdf = pdf_path.read_bytes()
    assert pdf.startswith(b"%PDF") and _operators_without_operands(pdf) == []
    assert (shots / "natives.png").stat().st_size > 20000

    # a zoom: the image shows the region only, at the width asked for
    zoom = visible.capture_drawing(
        CaptureDrawingRequest(
            operation_id="c2", path=str(shots / "zoom.png"), width=1200, region=(190.0, 60.0, 310.0, 140.0)
        )
    )
    assert zoom.size_px == (1200, 800) and zoom.region == (190.0, 60.0, 310.0, 140.0)
    assert (shots / "zoom.png").stat().st_size > 3000

    # the review lists what it cannot read
    sheet = visible.describe_drawing(DescribeDrawingRequest(operation_id="r", review=True)).sheets[0]
    assert "NoteNative [DrawViewAnnotation]: text: nota nativa" in " | ".join(sheet.native)
    (finding,) = [f for f in sheet.findings if f.code == "native_unread"]
    assert finding.severity == "warning" and finding.blocking is False and "NoteNative" in finding.message
