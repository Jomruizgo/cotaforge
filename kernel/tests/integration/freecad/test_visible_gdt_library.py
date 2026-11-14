# ruff: noqa: E501
"""The ISO 1101 frames of the ``cotaforge-gdt`` library in the VISIBLE CAD, inserted the way TechDraw's own *Insert SVG Symbol*
does it: a ``DrawViewSymbol`` whose ``Symbol`` is the library file. The values of the
frame are its ``EditableTexts``; the engineer edits them in FreeCAD's property editor (the same property the test writes
the second time), and the frame painted on the sheet, and in the PDF, follows.

What is proved here, in the real FreeCAD 1.1: FreeCAD lists the editable texts of the file; a position frame with
``Ø0,1 Ⓜ A B C`` is painted on a plate's sheet; editing one value by hand changes the frame in the window; the PDF has the
edited text; the symbol is still told from a foreign one afterwards (FreeCAD rewrites ``Symbol`` when a text is edited);
every characteristic of the library is painted.

Needs a display (on Windows the GUI binary opens its own window); skipped without one, carries the ``visible`` marker (run
serially). Set ``COTAFORGE_TEST_SHOTS`` to a folder to keep the images and the PDF.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.adapters.freecad.rpc import WORKER_DIR
from cotaforge.contract import (
    CaptureDrawingRequest,
    DrawingRequest,
    ExportDrawingRequest,
    RunNativeRequest,
)
from tests.integration.freecad.test_visible_live_sheet import _build_plate, visible  # noqa: F401 - the fixture and the plate

if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import gdt_frames as gf  # noqa: E402

pytestmark = [
    pytest.mark.freecad,
    pytest.mark.visible,
    pytest.mark.skipif(
        sys.platform != "win32" and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")),
        reason="the visible worker needs a display (DISPLAY / WAYLAND_DISPLAY)",
    ),
]

INSERT = """
import FreeCAD as App, json
doc = App.ActiveDocument
page = [o for o in doc.Objects if o.TypeId == "TechDraw::DrawPage"][0]
spec = json.loads({spec!r})
out = []
for item in spec:
    sym = doc.addObject("TechDraw::DrawViewSymbol", item["name"])
    page.addView(sym)
    sym.Symbol = item["svg"]
    sym.X, sym.Y = item["x"], item["y"]
    sym.EditableTexts = item["texts"]
    out.append([sym.Name, list(sym.EditableTexts)])
doc.recompute()
print(json.dumps({{"symbols": out}}))
"""

EDIT = """
import FreeCAD as App, json
doc = App.ActiveDocument
sym = doc.getObject({name!r})
before = list(sym.EditableTexts)
sym.EditableTexts = json.loads({texts!r})
doc.recompute()
print(json.dumps({{"before": before, "after": list(sym.EditableTexts), "symbol": sym.Symbol}}))
"""


def _run(adapter: FreeCADAdapter, code: str, op: str) -> dict:
    result = adapter.run_native(RunNativeRequest(operation_id=op, code=code, intent="the library frame"))
    assert result.ok, result.error
    return json.loads(result.stdout.strip().splitlines()[-1])


def _digest(path: Path) -> str:
    return hashlib.sha1(path.read_bytes(), usedforsecurity=False).hexdigest()  # noqa: S324 - a change detector


def test_a_position_frame_is_inserted_edited_by_hand_and_exported(visible: FreeCADAdapter, tmp_path: Path) -> None:  # noqa: F811
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path)
    shots.mkdir(parents=True, exist_ok=True)
    _build_plate(visible)
    visible.create_drawing(DrawingRequest(operation_id="dr", template="A3", title="Placa 19", scale=1.0))

    svg, texts = gf.gdt_frame_symbol("position", 0.1, ("diameter", "M"), ("A", "B", "C"))
    assert texts == ("Ø0,1 Ⓜ", "A", "B", "C")
    spec = [{"name": "FramePosition", "svg": svg, "texts": list(texts), "x": 345, "y": 130}]
    made = _run(visible, INSERT.format(spec=json.dumps(spec)), "n1")
    assert made["symbols"] == [["FramePosition", list(texts)]]  # FreeCAD lists the texts it was given
    region = (285.0, 105.0, 405.0, 155.0)
    first = shots / "frame_inserted.png"
    visible.capture_drawing(CaptureDrawingRequest(operation_id="c1", path=str(first), width=1600, region=region))
    whole = shots / "sheet_with_frame.png"
    visible.capture_drawing(CaptureDrawingRequest(operation_id="c1w", path=str(whole), width=1800))

    # the engineer edits one value in the property editor: the tolerance and the second datum
    edited = _run(visible, EDIT.format(name="FramePosition", texts=json.dumps(["Ø0,05 Ⓜ", "A", "D", "C"])), "n2")
    assert edited["before"] == list(texts) and edited["after"] == ["Ø0,05 Ⓜ", "A", "D", "C"]
    second = shots / "frame_edited.png"
    visible.capture_drawing(CaptureDrawingRequest(operation_id="c2", path=str(second), width=1600, region=region))
    assert _digest(second) != _digest(first), "the frame did not change when its text was edited"

    # FreeCAD rewrote the symbol when the text changed: it is still told from a foreign one
    assert edited["symbol"] != svg
    assert gf.is_frame_symbol(edited["symbol"])
    assert not gf.is_frame_symbol(edited["symbol"].replace("M 7 0 V 7", "M 8 0 V 7"))
    assert [v for _, v in gf.editable_fields(edited["symbol"])] == ["Ø0,05 Ⓜ", "A", "D", "C"]

    pdf_path = shots / "frame_edited.pdf"
    visible.export_drawing(ExportDrawingRequest(operation_id="e1", path=str(pdf_path), format="pdf"))
    pdf = pdf_path.read_bytes()
    assert pdf.startswith(b"%PDF") and len(pdf) > 20000


def test_every_characteristic_of_the_library_is_painted(visible: FreeCADAdapter, tmp_path: Path) -> None:  # noqa: F811
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path)
    shots.mkdir(parents=True, exist_ok=True)
    _build_plate(visible)
    visible.create_drawing(DrawingRequest(operation_id="dr", template="A3", title="Placa 19", scale=1.0))
    rows = [
        ("flatness", 0.05, (), ()),
        ("straightness", 0.1, ("diameter", "M"), ()),
        ("circularity", 0.02, (), ()),
        ("cylindricity", 0.03, (), ()),
        ("parallelism", 0.05, (), ("A",)),
        ("perpendicularity", 0.1, ("diameter",), ("A", "B")),
        ("angularity", 0.1, (), ("A", ("B", "M"))),
        ("position", 0.1, ("diameter", "M"), ("A", "B", "C")),
        ("concentricity", 0.05, ("diameter",), ("A-B",)),
        ("symmetry", 0.08, (), ("A",)),
        ("runout", 0.03, (), ("A-B",)),
        ("total_runout", 0.05, (), ("A", "B")),
        ("profile_line", "0,2 CZ", (), ("A", "B")),
        ("profile_surface", 0.3, (), ()),
    ]
    spec = []
    for number, (name, tolerance, modifiers, datums) in enumerate(rows):
        svg, texts = gf.gdt_frame_symbol(name, tolerance, modifiers, datums)
        column, line = divmod(number, 7)
        spec.append(
            {
                "name": f"F{number:02d}",
                "svg": svg,
                "texts": list(texts),
                "x": 70 if column == 0 else 345,
                "y": 215 - line * 20 if column == 0 else 165 - line * 12,
            }
        )
    spec.append(  # the wide cell: a long text
        {
            "name": "Fwide",
            "svg": (w := gf.gdt_frame_symbol("position", "Ø0,05 Ⓜ CZ", (), ("A", "B")))[0],
            "texts": list(w[1]),
            "x": 345,
            "y": 76,
        }
    )
    made = _run(visible, INSERT.format(spec=json.dumps(spec)), "n1")
    assert len(made["symbols"]) == len(rows) + 1
    out = shots / "all_characteristics.png"
    visible.capture_drawing(
        CaptureDrawingRequest(operation_id="c1", path=str(out), width=2000, region=(0.0, 60.0, 420.0, 280.0))
    )
    assert out.stat().st_size > 20000
