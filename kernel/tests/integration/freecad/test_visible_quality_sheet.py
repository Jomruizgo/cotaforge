# ruff: noqa: E501
"""The quality floor of a drawing, in the visible CAD: the 19 mm plate (4 dowel holes H7, 2 counterbored holes, one M6) drawn
three ways -- all its views, ONE view with a thickness note, and in third angle -- and measured: every hole located in x and
y by baseline dimensions stacked outside the part, callouts whose leaders never cross and cut the outline once, the dowels
marked, the material in the title block, the notes numbered, none of them a request to verify the ISO 286 tables.

Needs a display (on Windows the GUI binary opens its own window); skipped without one, carries the ``visible`` marker (run
serially). Set ``COTAFORGE_TEST_SHOTS`` to a folder to keep the window images, the PDF and the transcript.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.contract import (
    AdapterError,
    AnnotateDrawingRequest,
    DescribeDrawingRequest,
    DrawingRequest,
    ExportDrawingRequest,
    SetDesignAttributesRequest,
)
from tests.integration.freecad.test_visible_live_sheet import (  # noqa: F401 - the fixture and the plate are shared
    CLEARANCE,
    DOWELS,
    TAPPED,
    _build_plate,
    _side,
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

MATERIAL = "Acero 1045"


def _holes(extra: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return [
        {"type": "hole_dim", "id": "dowels", "at": [[x, y, 0] for x, y in DOWELS], "fit": "H7", "dowel": True},
        {"type": "hole_dim", "id": "clear", "at": [[x, y, 0] for x, y in CLEARANCE]},
        {"type": "hole_dim", "id": "tap", "at": [[TAPPED[0], TAPPED[1], 0]]},
        *([extra] if extra else []),
    ]


def _datums(left: str, front: str) -> list[dict[str, Any]]:
    """The references the engineer gives: the left face (across x) and the front face (across y) of the plate."""
    return [
        {"type": "datum", "id": "dB", "letter": "B", "face": left},
        {"type": "datum", "id": "dC", "letter": "C", "face": front},
    ]


def _notes() -> list[dict[str, Any]]:
    return [
        {"type": "note", "id": "n1", "text": "Fabricar de lámina rectificada por ambas caras"},
        {"type": "note", "id": "n2", "text": "Rebabas eliminadas, aristas vivas rotas 0,3 x 45"},
        {"type": "note", "id": "n3", "text": "Material según rótulo"},
    ]


def _add(adapter: FreeCADAdapter, label: str, items: list[dict[str, Any]], sheet: str | None = None) -> Any:
    result = adapter.annotate_drawing(
        AnnotateDrawingRequest(operation_id=label, action="add", items=tuple(items), sheet=sheet)
    )
    bad = [i for i in result.items if i.status == "error"]
    assert not bad, bad
    return result


def _measure(adapter: FreeCADAdapter, doc: Any, sheet: str | None, shots: Path, name: str) -> dict[str, Any]:
    """The metrics of the sheet as composed, a window image and the review findings: the acceptance numbers."""
    _state(adapter, doc, "sheet_png", path=str(shots / f"{name}.png"), sheet=sheet)
    metrics = _state(adapter, doc, "sheet_metrics", sheet=sheet)
    review = adapter.describe_drawing(DescribeDrawingRequest(operation_id=f"r-{name}", review=True, sheet=sheet))
    sheet_info = review.sheets[0] if sheet is None else next(s for s in review.sheets if s.sheet == sheet)
    metrics["review"] = [[f.code, f.severity, list(f.items), f.message[:400]] for f in sheet_info.findings]
    metrics["items"] = [[i.id, i.type, i.status, i.detail[:160]] for i in sheet_info.items]
    (shots / f"{name}.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=1), encoding="utf-8")
    return metrics


def _floor(name: str, metrics: dict[str, Any]) -> list[str]:
    """The measured floor: every hole axis has an x and a y, no leaders cross, none cuts the outline twice, no dimension
    inside an outline, no collision, and the review has nothing against the placement. Returns what fails."""
    problems: list[str] = []
    for index, hole in metrics["holes"].items():
        if hole["diameter"] in (6.0, 5.0, 9.0) and ("x" not in hole or "y" not in hole):
            problems.append(f"{name}: hole {index} (diameter {hole['diameter']}) lacks its x or its y: {hole}")
    problems += [f"{name}: {row[0]} {row[1]}" for row in metrics["findings"] if row[2] == "warning"]
    against = {
        "collision",
        "hole_not_located",
        "hole_partly_located",
        "leader_crossing",
        "leader_crosses_part",
        "leader_crosses_dimension",
        "dimension_inside_outline",
        "window_differs",
        "native_fallback",
        "material_title_block_empty",
        "item_outside_frame",
        "item_over_title_block",
    }
    problems += [f"{name}: review {row[0]} {row[2]} {row[3][:200]}" for row in metrics["review"] if row[0] in against]
    return problems


def test_the_plate_meets_the_floor_in_three_variants(visible: FreeCADAdapter, tmp_path: Path) -> None:  # noqa: F811
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path)
    shots.mkdir(parents=True, exist_ok=True)
    doc, base = _build_plate(visible)
    visible.set_design_attributes(SetDesignAttributesRequest(operation_id="mat", material=MATERIAL))
    left = _side(visible, base, (-1.0, 0.0, 0.0))
    bottom = _side(visible, base, (0.0, 0.0, -1.0))
    front = _side(visible, base, (0.0, -1.0, 0.0))
    transcript: list[str] = []
    problems: list[str] = []

    # 1. all the views: the default location counts from the datums the engineer declares (the plate is symmetric across x
    # only: x could do without, y needs the front face) -- never from the corner of the box
    created = visible.create_drawing(
        DrawingRequest(operation_id="v1", template="A3", title="Placa 19", views=("front", "top", "iso"))
    )
    transcript.append(f"create A3: scale {created.scale}, views {created.views}, items {[i.id for i in created.items]}")
    assert "loc" in {i.id for i in created.items}, created.items
    _add(visible, "a1", [{"type": "view_lines", "id": "vl"}, *_holes()])
    _add(visible, "a2", [{"type": "datum", "id": "dA", "letter": "A", "face": bottom}])
    _add(visible, "a3", [{"type": "datum", "id": "dB", "letter": "B", "face": left}])
    _add(visible, "a3b", [{"type": "datum", "id": "dC", "letter": "C", "face": front}])
    result = _add(visible, "a4", _notes())
    transcript += [f"{i.id} {i.type} {i.status}: {i.detail[:140]}" for i in result.items]
    metrics = _measure(visible, doc, None, shots, "v1_all_views")
    transcript.append(f"v1 review: {metrics['review']}")
    assert metrics["material"] == MATERIAL, metrics["material"]
    assert not any("ISO 286" in t for t in metrics["texts"]), metrics["texts"]  # a fit prints its limits, never a note to verify them
    problems += _floor("v1", metrics)
    pdf = shots / "v1_plate.pdf"
    written = visible.export_drawing(
        ExportDrawingRequest(operation_id="e1", path=str(pdf), format="pdf", sheet="Drawing")
    )
    assert written.renderer == "techdraw" and pdf.read_bytes().startswith(b"%PDF")

    # 2. a plate in ONE view, with its thickness as a note
    one = visible.create_drawing(
        DrawingRequest(operation_id="v2", template="A3", title="Placa 19 una vista", sheet="Una", views=("top",))
    )
    transcript.append(f"create one view: scale {one.scale}, views {one.views}")
    assert one.views == ("top",), one.views
    _add(visible, "b0", _datums(left, front), sheet="Una")
    _add(visible, "b1", _holes(), sheet="Una")
    _add(visible, "b2", _notes()[:2], sheet="Una")
    metrics = _measure(visible, doc, "Una", shots, "v2_one_view")
    transcript.append(f"v2 texts: {metrics['texts']}")
    assert any("Espesor" in t and "19" in t for t in metrics["texts"]), metrics["texts"]
    problems += _floor("v2", metrics)

    # 3. third angle: the symbol and the views placed the other way round
    third = visible.create_drawing(
        DrawingRequest(
            operation_id="v3",
            template="A3",
            title="Placa 19 tercer diedro",
            sheet="Tercer",
            angle="third",
            views=("front", "top", "iso"),
        )
    )
    transcript.append(f"create third angle: scale {third.scale}, views {third.views}")
    assert third.angle == "third"
    _add(visible, "c0", _datums(left, front), sheet="Tercer")
    _add(visible, "c1", [{"type": "view_lines", "id": "vl"}, *_holes()], sheet="Tercer")
    _add(visible, "c2", _notes()[:2], sheet="Tercer")
    metrics = _measure(visible, doc, "Tercer", shots, "v3_third_angle")
    problems += _floor("v3", metrics)

    for sheet, name in (("Una", "v2_one_view.pdf"), ("Tercer", "v3_third_angle.pdf")):  # one PDF per sheet
        out = shots / name
        written = visible.export_drawing(
            ExportDrawingRequest(operation_id=f"e-{sheet}", path=str(out), format="pdf", sheet=sheet)
        )
        assert written.renderer == "techdraw" and out.read_bytes().startswith(b"%PDF")
    with pytest.raises(AdapterError, match="ONE sheet"):  # one drawing, one sheet, one file
        visible.export_drawing(
            ExportDrawingRequest(operation_id="e2", path=str(shots / "all_sheets.pdf"), format="pdf")
        )
    visible.export_drawing(
        ExportDrawingRequest(operation_id="e3", path=str(shots / "v1_plate.dxf"), format="dxf", sheet="Drawing")
    )
    (shots / "transcript.txt").write_text("\n".join([*transcript, "", "PROBLEMS", *problems]), encoding="utf-8")
    assert not problems, problems
