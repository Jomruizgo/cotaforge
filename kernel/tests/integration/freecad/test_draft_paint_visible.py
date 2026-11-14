"""The coloured draft analysis in the window of the REAL visible worker: FreeCAD 1.1 draws a per-face colour from the
``ShapeAppearance`` of the view of the object the window shows (the Tip of a PartDesign body); a ``DiffuseColor`` set on
the body's view paints nothing (seen on 1.1.3). Green is a face that meets the minimum, yellow a low one, red a face
with no draft or an undercut; ``show_entities(clear)`` puts the appearance back.

Needs a display (Windows opens its own window); carries the ``visible`` marker."""

import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.contract import (
    DocumentKind,
    DraftAnalysisRequest,
    ExtrudeRequest,
    NewDocumentRequest,
    PlaneName,
    RectEntity,
    RunNativeRequest,
    ShowEntitiesRequest,
    SketchRequest,
)

pytestmark = [
    pytest.mark.freecad,
    pytest.mark.visible,
    pytest.mark.skipif(
        sys.platform != "win32" and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")),
        reason="the visible worker needs a display (DISPLAY / WAYLAND_DISPLAY)",
    ),
]

READ_THE_TIP = """
import json
body = [o for o in doc.Objects if o.TypeId == "PartDesign::Body"][0]
view = body.Tip.ViewObject
print("TIP", json.dumps([[round(c, 2) for c in m.DiffuseColor[:3]] for m in view.ShapeAppearance]))
"""


@pytest.fixture
def window(freecad_root: Path, tmp_path: Path) -> Iterator[FreeCADAdapter]:
    with FreeCADAdapter(
        freecad_root=freecad_root, timeout_s=120.0, log_dir=tmp_path / "logs", visible=True, enable_test_hooks=True
    ) as adapter:
        yield adapter


def _tip(adapter: FreeCADAdapter) -> list[list[float]]:
    result = adapter.run_native(RunNativeRequest(operation_id="read", code=READ_THE_TIP, intent="read the colours"))
    line = next(line for line in result.stdout.splitlines() if line.startswith("TIP "))
    return json.loads(line[4:])


def test_the_faces_are_painted_by_their_draft_and_the_appearance_comes_back(window: FreeCADAdapter) -> None:
    window.new_document(NewDocumentRequest(operation_id="new", kind=DocumentKind.part, name="painted"))
    sketch = window.sketch(
        SketchRequest(
            operation_id="s0", on=PlaneName.XY, entities=(RectEntity(name="o", center=(20, 15), width=40, height=30),)
        )
    )
    window.extrude(ExtrudeRequest(operation_id="base", sketch=sketch.sketch, depth_mm=20.0))
    before = _tip(window)

    result = window.draft_analysis(DraftAnalysisRequest(operation_id="d", pull=(0, 0, 1), paint=True))
    assert result.painted is True
    shown = _tip(window)
    assert len(shown) == 6
    greens = [c for c in shown if c == [0.2, 0.7, 0.25]]
    reds = [c for c in shown if c == [0.85, 0.15, 0.15]]
    assert len(greens) == 2 and len(reds) == 4  # top and bottom meet the minimum; the four walls have no draft

    window.show_entities(ShowEntitiesRequest(operation_id="c", clear=True))
    assert _tip(window) == before
