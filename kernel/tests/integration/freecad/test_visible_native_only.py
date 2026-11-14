# ruff: noqa: E501
"""The harness only makes FreeCAD's own TechDraw objects, and the engineer's hand edits behave natively, in the visible CAD.

What the sheet draws on a view (centre lines, thread arcs, hidden lines) is the view's own: cosmetic edges and the view's
hidden-line property. The engineer moves the view and they follow, changes the model and the dimensions update, deletes the
view and they go; no symbol of the harness's drawing is left on the page for those items.

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
    CLEARANCE,
    DOWELS,
    TAPPED,
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
"""


def _native(adapter: FreeCADAdapter, code: str) -> Any:
    result = adapter.run_native(RunNativeRequest(operation_id="probe", code=PREAMBLE + code))
    assert result.ok, result.error
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_what_the_sheet_draws_on_a_view_is_the_views_and_follows_the_engineers_hand_edits(
    visible: FreeCADAdapter,
) -> None:  # noqa: F811
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or ".")
    shots.mkdir(parents=True, exist_ok=True)
    doc, _ = _build_plate(visible)
    visible.create_drawing(DrawingRequest(operation_id="dr", template="A3", title="Placa 19"))
    visible.annotate_drawing(
        AnnotateDrawingRequest(
            operation_id="a",
            action="add",
            items=(
                {"type": "view_lines", "id": "vl"},
                {"type": "hole_dim", "id": "dowels", "at": [[x, y, 0] for x, y in DOWELS], "fit": "H7"},
                {"type": "hole_dim", "id": "clear", "at": [[x, y, 0] for x, y in CLEARANCE]},
                {"type": "hole_dim", "id": "tap", "at": [[TAPPED[0], TAPPED[1], 0]]},
            ),
        )
    )
    # 1. the centre lines, the thread arc and the hidden lines are the views' own: no symbol of the harness stands for them
    rows = _state(visible, doc, "sheet_objects")
    assert not [r for r in rows if r[1] == "DrawViewSymbol" and r[3] in ("vl", "loc", "threads")], rows
    state = _native(
        visible,
        """
top = view("Top")
print(json.dumps({
    "own": json.loads(top.CotaforgeCosmetic),
    "edges": len(top.CosmeticEdges),
    "hidden": bool(top.HardHidden),
    "symbols": [o.Name for o in page.Views if o.TypeId == "TechDraw::DrawViewSymbol"],
}))
""",
    )
    assert set(state["own"]) >= {"vl", "threads"} and state["edges"] >= 4 and state["hidden"], state
    # 2. move the view: the cosmetic edges are in the view's own coordinates, so they stay on the same features
    moved = _native(
        visible,
        """
top = view("Top")
def ends(e):
    try:
        float(e.Radius)
        return [float(e.Center.x), float(e.Center.y), float(e.Radius)]
    except TypeError:
        return [float(e.Start.x), float(e.Start.y), float(e.End.x), float(e.End.y)]
before = [ends(e) for e in top.CosmeticEdges]
x0 = float(top.X)
top.X = x0 + 25.0
doc.recompute()
print(json.dumps({"same": before == [ends(e) for e in top.CosmeticEdges], "moved": float(top.X) - x0, "count": len(before)}))
""",
    )
    assert moved["same"] and abs(moved["moved"] - 25.0) < 1e-6 and moved["count"] >= 4, moved
    _state(visible, doc, "sheet_png", path=str(shots / "native_moved_view.png"))
    # 3. change the model: TechDraw recomputes and the dimensions say the new number
    changed = _native(
        visible,
        """
pads = [o for o in doc.Objects if o.TypeId == "PartDesign::Pad"]
pads[0].Length = 25.0
doc.recompute()
for _ in range(40):
    App.processEvents() if hasattr(App, "processEvents") else None
page.recompute(True)
doc.recompute()
dims = [o for o in page.Views if "DimExtent" in o.TypeId or "Dimension" in o.TypeId]
print(json.dumps({"values": sorted(round(abs(float(d.getRawValue())), 3) for d in dims)}))
""",
    )
    assert 25.0 in changed["values"] and 19.0 not in changed["values"], changed
    # 4. delete the view: what is on it goes with it, and nothing of the harness's drawing is left behind
    gone = _native(
        visible,
        """
top = view("Top")
before = len(top.CosmeticEdges)
names = [o.Name for o in page.Views]
group.removeProjection("Top")
doc.recompute()
left = [o.Name for o in page.Views if o.Name not in names]
symbols = [o.Name for o in page.Views if o.TypeId == "TechDraw::DrawViewSymbol"]
print(json.dumps({"had": before, "symbols": symbols, "still_there": [o.Name for o in group.Views if str(o.Type) == "Top"]}))
""",
    )
    assert gone["had"] >= 4 and gone["symbols"] == [] and gone["still_there"] == [], gone
    _state(visible, doc, "sheet_png", path=str(shots / "native_deleted_view.png"))
    review = visible.describe_drawing(DescribeDrawingRequest(operation_id="r", review=True)).sheets[0]
    assert not [f for f in review.findings if f.code == "foreign_symbol"], review.findings
