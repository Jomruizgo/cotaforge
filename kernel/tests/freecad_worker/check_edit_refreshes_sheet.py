"""An in-place edit refreshes the drawing pages of the document at once (``edit.edit_feature``): the sheet in the window
must not keep the old value of a dimension next to a model that changed. Plain asserts run inside ``freecadcmd`` (see
``run.py``); the native objects of the page are what a headless FreeCAD can measure."""

from typing import Any

import drawing
import main
import sheet_mirror
from check_drawing_live import DOWELS, build_part
from check_edit_dimension import _edit
from harness import call, op


def _rec() -> Any:
    assert main.WORKER.active is not None
    return main.WORKER.active


def _page() -> Any:
    page = drawing.find_page(_rec().fc_doc)
    assert page is not None
    return page


def _dowel_dimension() -> Any:
    natives = [o for o in sheet_mirror.owned(_page()).get("dowels", []) if str(o.TypeId) != "TechDraw::DrawViewSymbol"]
    assert natives
    return natives[0]


def _resize_first_dowel(diameter: float) -> dict[str, Any]:
    rec = next(r for r in _rec().features if r.params.get("operation_id") == "dowel1")
    request = {**rec.params, "diameter_mm": diameter}
    return _edit([{"feature": {"id": rec.id}, "kind": "hole", "request": request}])


def _findings() -> list[dict[str, Any]]:
    return call("describe_drawing", op("r", review=True))["sheets"][0]["findings"]


def check_a_hole_resized_in_place_is_drawn_with_the_new_diameter_at_once() -> None:
    build_part()
    call("create_drawing", op("d", template="A3", scale=1.0))
    items = [{"type": "hole_dim", "id": "dowels", "at": [[x, y, 0] for x, y in DOWELS[:1]]}]
    call("annotate_drawing", op("a", action="add", items=items, ids=[]))
    assert abs(abs(float(_dowel_dimension().getRawValue())) - 6.0) < 0.011
    result = _resize_first_dowel(7.0)
    assert result["applied"], result
    assert abs(abs(float(_dowel_dimension().getRawValue())) - 7.0) < 0.011, "the page keeps the old diameter"
    assert "7" in str(_dowel_dimension().FormatSpec), _dowel_dimension().FormatSpec
    stale = [f for f in _findings() if f["code"] == "window_differs"]
    assert not stale, stale
