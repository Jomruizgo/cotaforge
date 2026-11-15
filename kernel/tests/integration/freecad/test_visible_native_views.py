# ruff: noqa: E501
"""A section is FreeCAD's own ``DrawViewSection``, and the engineer's hand edits behave natively, in the visible CAD.

The cut, its hatch and the cutting line with its letters are TechDraw's; the dimensions on it are TechDraw's own
``DrawViewDimension`` objects that reference the section's geometry. The engineer moves the parent view and the section goes
with it, changes the model and the section, its hatch and its dimensions update; a parent that is taken away leaves nothing of
the section behind once the sheet is refreshed (the CAD's own window refuses to delete a view that has a section).

Needs a display (the visible GUI binary); carries the ``visible`` marker. ``COTAFORGE_TEST_SHOTS`` keeps the window images.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.contract import AnnotateDrawingRequest, DescribeDrawingRequest, DrawingRequest, RunNativeRequest
from tests.integration.freecad.test_visible_live_sheet import (  # noqa: F401 - the fixture and the plate are shared
    DOWELS,
    _build_plate,
    _state,
    visible,
)

pytestmark = [
    pytest.mark.freecad,
    pytest.mark.visible,
    pytest.mark.skipif(
        sys.platform != "win32" and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")),
        reason="the visible worker needs a display (DISPLAY / WAYLAND_DISPLAY)",
    ),
]

PREAMBLE = """
import json
import FreeCAD as App
doc = App.ActiveDocument
page = [o for o in doc.Objects if o.TypeId == "TechDraw::DrawPage"][0]
group = [o for o in page.Views if o.TypeId == "TechDraw::DrawProjGroup"][0]
def view(name):
    return [v for v in group.Views if str(v.Type) == name][0]
def sections():
    return [o for o in page.Views if o.TypeId == "TechDraw::DrawViewSection"]
def settle(seconds=30.0):
    import time
    from PySide import QtCore
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        doc.recompute()
        QtCore.QCoreApplication.processEvents()
        views = [v for v in page.Views if hasattr(v, "getVisibleEdges")] + list(group.Views)
        if not [v for v in views if "Touched" in [str(x) for x in v.State]]:
            break
        time.sleep(0.1)
    for _ in range(10):
        QtCore.QCoreApplication.processEvents()
        time.sleep(0.05)
def dims_on(obj):
    out = []
    for d in page.Views:
        if d.TypeId == "TechDraw::DrawViewDimension" and any(r[0].Name == obj.Name for r in d.References2D):
            out.append(d)
    return out
"""

LINE_OF_HOLES = [[x, y, 0] for x, y in DOWELS[:2]]  # the two dowels on y = 12: one plane runs along both axes


def _native(adapter: FreeCADAdapter, code: str) -> Any:
    result = adapter.run_native(RunNativeRequest(operation_id="probe", code=PREAMBLE + code))
    assert result.ok, result.error
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_a_section_is_the_cads_own_view_and_follows_the_engineers_hand_edits(visible: FreeCADAdapter) -> None:  # noqa: F811
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or ".")
    shots.mkdir(parents=True, exist_ok=True)
    doc, _ = _build_plate(visible)
    visible.create_drawing(DrawingRequest(operation_id="dr", template="A3", title="Placa 19"))
    added = visible.annotate_drawing(
        AnnotateDrawingRequest(
            operation_id="a",
            action="add",
            items=(
                {"type": "section", "id": "sec", "holes": LINE_OF_HOLES, "view": "top"},
                {"type": "hole_dim", "id": "bore", "at": [LINE_OF_HOLES[0]], "view": "A-A"},
            ),
        )
    )
    assert [i.status for i in added.items if i.id in ("sec", "bore")] == ["ok", "ok"], added.items
    # 1. the section is a DrawViewSection hung from the top view, and no symbol stands for it or for its dimension
    state = _native(
        visible,
        """
top = view("Top")
sec = sections()
dims = dims_on(sec[0]) if sec else []
print(json.dumps({
    "sections": len(sec),
    "base": sec[0].BaseView.Name if sec and sec[0].BaseView is not None else None,
    "top": top.Name,
    "symbols": [o.Name for o in page.Views if o.TypeId == "TechDraw::DrawViewSymbol" and o.CotaforgeItem in ("sec", "bore")],
    "dims": [d.Label for d in dims],
    "hatch": str(sec[0].CutSurfaceDisplay) if sec else None,
    "edges": len(sec[0].getVisibleEdges()) if sec else 0,
}))
""",
    )
    assert state["sections"] == 1 and state["base"] == state["top"], state
    assert state["symbols"] == [] and len(state["dims"]) == 1 and state["hatch"] == "PatHatch" and state["edges"] > 0, state
    review = visible.describe_drawing(DescribeDrawingRequest(operation_id="r", review=True)).sheets[0]
    assert not [f for f in review.findings if f.code in ("native_fallback", "window_differs")], review.findings
    _state(visible, doc, "sheet_png", path=str(shots / "native_section_made.png"))
    # 2. move the parent view: the section goes with it (its position is the parent's plus an offset), and so does its dimension
    moved = _native(
        visible,
        """
top, sec = view("Top"), sections()[0]
dim = dims_on(sec)[0]
before = (float(sec.X), float(sec.Y), float(dim.X), float(dim.Y))
top.X = float(top.X) + 25.0
doc.recompute()
print(json.dumps({
    "section": [float(sec.X) - before[0], float(sec.Y) - before[1]],
    "dimension": [float(dim.X) - before[2], float(dim.Y) - before[3]],
    "expression": [e[1] for e in sec.ExpressionEngine],
}))
""",
    )
    assert abs(moved["section"][0] - 25.0) < 1e-6 and abs(moved["section"][1]) < 1e-6, moved
    assert moved["dimension"] == [0.0, 0.0], moved  # the dimension is in the section's own coordinates: it goes with it
    _state(visible, doc, "sheet_png", path=str(shots / "native_section_moved_parent.png"))
    # 3. change the model (the two dowel holes go from 6 to 7 mm): the section, its hatch and its dimension update
    changed = _native(
        visible,
        """
sec = sections()[0]
dim = dims_on(sec)[0]
before = (round(abs(float(dim.getRawValue())), 3), len(sec.getVisibleEdges()))
holes = [o for o in doc.Objects if o.TypeId == "PartDesign::Hole" and abs(float(o.Diameter) - 6.0) < 1e-6]
for h in holes:
    h.Diameter = 7.0
settle()
bad = [o.Name for o in doc.Objects if "Invalid" in o.State or "RecomputeError" in str(o.State)]
print(json.dumps({"holes": len(holes), "bad": bad, "before": before, "after": [round(abs(float(dim.getRawValue())), 3), len(sec.getVisibleEdges())]}))
""",
    )
    assert changed["holes"] == 4 and changed["bad"] == [], changed
    assert changed["before"][0] == 6.0 and changed["after"][0] == 7.0, changed  # TechDraw measured the new bore
    _state(visible, doc, "sheet_png", path=str(shots / "native_section_changed_model.png"))
    # 4. the parent is taken away (through the API: the window itself refuses to delete a view that has a section): nothing of the
    #    section is left once the sheet is refreshed
    visible.annotate_drawing(AnnotateDrawingRequest(operation_id="rv", action="remove_view", view="top"))
    left = _native(
        visible,
        """
print(json.dumps({"sections": [o.Name for o in sections()], "dims": [o.Name for o in page.Views if o.TypeId == "TechDraw::DrawViewDimension" and any(getattr(r[0], "TypeId", "") == "TechDraw::DrawViewSection" for r in o.References2D)]}))
""",
    )
    assert left["sections"] == [] and left["dims"] == [], left
    _state(visible, doc, "sheet_png", path=str(shots / "native_section_parent_gone.png"))
