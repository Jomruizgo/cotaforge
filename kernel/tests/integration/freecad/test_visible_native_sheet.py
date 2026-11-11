"""The drawing as a TechDraw drawing, in the visible CAD: a 19 mm plate with its holes, datums, a perpendicularity
frame, position dimensions, notes and a revision is built item by item; after each item the page holds
TechDraw's own objects (their types are asserted), a view is dropped and the scale grows, and the PDF is TechDraw's own
export of the window.

Needs a display (on Windows the GUI binary opens its own window); skipped without one, carries the ``visible`` marker
(run serially). Set ``COTAFORGE_TEST_SHOTS`` to a folder to keep the window images and the PDF.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.contract import (
    AnnotateDrawingRequest,
    DescribeDrawingRequest,
    DrawingRequest,
    ExportDrawingRequest,
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

DIMENSION = "DrawViewDimension"
ANNOTATION = "DrawViewAnnotation"
SYMBOL = "DrawViewSymbol"


def _kinds(adapter: FreeCADAdapter, doc: Any, item: str) -> set[str]:
    return {row[1] for row in _state(adapter, doc, "sheet_objects") if row[3] == item}


def test_the_plate_is_built_item_by_item_with_techdraws_own_objects(visible: FreeCADAdapter, tmp_path: Path) -> None:  # noqa: F811
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path)
    shots.mkdir(parents=True, exist_ok=True)
    doc, base = _build_plate(visible)
    created = visible.create_drawing(DrawingRequest(operation_id="dr", template="A3", title="Placa 19"))
    assert created.scale == 1.0 and created.views == ("front", "top", "right", "isometric"), created
    left = _side(visible, base, (-1.0, 0.0, 0.0))
    bottom = _side(visible, base, (0.0, 0.0, -1.0))
    front_face = _side(visible, base, (0.0, -1.0, 0.0))
    steps: list[tuple[str, dict[str, Any], set[str]]] = [
        ("vl", {"type": "view_lines", "id": "vl"}, {SYMBOL}),
        (
            "dowels",
            {"type": "hole_dim", "id": "dowels", "at": [[x, y, 0] for x, y in DOWELS], "fit": "H7"},
            {DIMENSION},
        ),
        ("clear", {"type": "hole_dim", "id": "clear", "at": [[x, y, 0] for x, y in CLEARANCE]}, {DIMENSION}),
        ("tap", {"type": "hole_dim", "id": "tap", "at": [[TAPPED[0], TAPPED[1], 0]]}, {DIMENSION}),
        ("dA", {"type": "datum", "id": "dA", "letter": "A", "face": bottom}, {SYMBOL}),
        ("dB", {"type": "datum", "id": "dB", "letter": "B", "face": left}, {SYMBOL}),
        (
            "perp",
            {
                "type": "gdt",
                "id": "perp",
                "char": "perpendicularity",
                "value": 0.05,
                "datums": ["A"],
                "face": _side(visible, base, (1.0, 0.0, 0.0)),
            },
            {SYMBOL},
        ),
        (
            "pos",
            {
                "type": "datum_dims",
                "id": "pos",
                "x_from": left,
                "y_from": front_face,
                "holes": [[x, y, 0] for x, y in CLEARANCE],
            },
            {DIMENSION},
        ),
        ("n1", {"type": "note", "id": "n1", "text": "Rebabas eliminadas, aristas vivas rotas 0,3 x 45"}, {ANNOTATION}),
        ("n2", {"type": "note", "id": "n2", "text": "Material segun rotulo"}, {ANNOTATION}),
        (
            "rv",
            {
                "type": "revision",
                "id": "rv",
                "index": "A",
                "description": "Primera emision",
                "date": "2026-10-27",
                "by": "JR",
            },
            set(),
        ),
    ]
    shot_at = {"dowels", "perp", "n2"}  # the intermediate shots; the final one is taken after the views are chosen

    def run(chosen: list[tuple[str, dict[str, Any], set[str]]], first: int) -> None:
        for number, (key, item, kinds) in enumerate(chosen, start=first):
            result = visible.annotate_drawing(
                AnnotateDrawingRequest(operation_id=f"a{number}", action="add", items=(item,))
            )
            assert result.changed == (key,), (key, result.changed)
            status = {i.id: i.status for i in result.items}[key]
            assert status in ("ok", "warning"), (key, [i for i in result.items if i.id == key])
            assert not [w for w in result.warnings if "could not" in w], result.warnings
            if kinds:  # what TechDraw has an object for is that object; the rest is a symbol of our drawing
                assert kinds <= _kinds(visible, doc, key), (key, _kinds(visible, doc, key))
            if key in ("dowels", "clear", "tap", "pos"):  # TechDraw measured it: the page holds the model's number
                assert _state(visible, doc, "sheet_compare") == [], key
            if key in shot_at:
                _state(visible, doc, "sheet_png", path=str(shots / f"step{number:02d}_{key}.png"))

    run(steps[:4], 1)
    # the right view carries no dimension: drop it and the others take the room (2:1 would not
    # fit with the isometric view, so the scale stays the largest standard one that does)
    review = visible.describe_drawing(DescribeDrawingRequest(operation_id="r1", review=True)).sheets[0]
    flagged = [f for f in review.findings if f.code == "view_adds_nothing"]
    assert ("right",) in [f.items for f in flagged], flagged  # hidden lines alone do not give the view a job
    dropped = visible.annotate_drawing(AnnotateDrawingRequest(operation_id="v1", action="remove_view", view="right"))
    assert dropped.scale == 1.0 and dropped.views == ("front", "top", "isometric"), dropped
    assert [w for w in dropped.warnings if "items that cannot be drawn" in w] == [], dropped.warnings
    assert _state(visible, doc, "sheet_compare") == []
    _state(visible, doc, "sheet_png", path=str(shots / "step05_right_dropped.png"))
    run(steps[4:], 5)
    objects = _state(visible, doc, "sheet_objects")
    assert {o[1] for o in objects if o[3] in ("dowels", "clear", "tap", "pos")} >= {DIMENSION}
    assert all(row[3] for row in objects)  # every object of the page belongs to an item or a unit of the sheet
    assert _state(visible, doc, "sheet_compare") == []

    # the owner's case: the 60 mm dimension and the perpendicularity frame, and everything else on the sheet
    final = visible.describe_drawing(DescribeDrawingRequest(operation_id="r2", review=True)).sheets[0]
    collisions = [f for f in final.findings if f.code == "collision"]
    lines = [f"{f.items}: {f.message}" for f in collisions]
    (shots / "collisions.txt").write_text("\n".join(lines), encoding="utf-8")
    assert collisions == [], [(f.items, f.message[:140]) for f in collisions]
    assert not [f for f in final.findings if f.code in ("window_differs", "native_fallback")], final.findings
    _state(visible, doc, "sheet_png", path=str(shots / "final_window.png"))
    assert _state(visible, doc, "sheet_cross_check") == []  # the centre marks are at the centres of their holes

    pdf, dxf = shots / "final.pdf", shots / "final.dxf"
    written = visible.export_drawing(ExportDrawingRequest(operation_id="e", path=str(pdf), format="pdf"))
    assert written.renderer == "techdraw" and written.paths == (str(pdf),), written
    assert pdf.read_bytes().startswith(b"%PDF") and pdf.stat().st_size > 20_000
    out = visible.export_drawing(ExportDrawingRequest(operation_id="x", path=str(dxf), format="dxf"))
    assert (
        out.renderer == "compositor" and dxf.read_bytes().count(b"AC1009") == 1
    )  # our R12 writer, for the CAD that cuts


def test_each_of_several_sheets_is_its_own_pdf_written_by_techdraw(visible: FreeCADAdapter, tmp_path: Path) -> None:  # noqa: F811
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path)
    doc, _ = _build_plate(visible)
    visible.create_drawing(DrawingRequest(operation_id="d1", template="A3", title="Placa 19", scale=1.0))
    visible.create_drawing(DrawingRequest(operation_id="d2", template="A4", title="Placa 19 B", sheet="Segunda"))
    visible.annotate_drawing(
        AnnotateDrawingRequest(
            operation_id="a", action="add", items=({"type": "note", "id": "n1", "text": "Una nota"},), sheet="Segunda"
        )
    )
    for name in ("Drawing", "Segunda"):  # one drawing, one sheet, one file
        pdf = shots / f"{name}.pdf"
        written = visible.export_drawing(
            ExportDrawingRequest(operation_id="e", path=str(pdf), format="pdf", sheet=name)
        )
        assert written.renderer == "techdraw" and written.paths == (str(pdf),)
        counts = [int(c) for c in re.findall(rb"/Count (\d+)", pdf.read_bytes())]
        assert counts and max(counts) == 1, counts  # one PDF page
    assert _state(visible, doc, "sheet_compare") == []


def test_dropping_a_view_grows_the_scale_to_the_largest_standard_one_that_fits(
    visible: FreeCADAdapter,  # noqa: F811
    tmp_path: Path,
) -> None:
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path)
    doc, _ = _build_plate(visible)
    made = visible.create_drawing(DrawingRequest(operation_id="d", template="A4", title="Placa 19"))
    assert made.scale == 0.5
    visible.annotate_drawing(
        AnnotateDrawingRequest(operation_id="noloc", action="remove", ids=("loc",))
    )  # no rows to make room for
    visible.annotate_drawing(
        AnnotateDrawingRequest(
            operation_id="a",
            action="add",
            items=({"type": "hole_dim", "id": "dowels", "at": [[x, y, 0] for x, y in DOWELS], "fit": "H7"},),
        )
    )
    _state(visible, doc, "sheet_png", path=str(shots / "grow_before.png"))
    dropped = visible.annotate_drawing(AnnotateDrawingRequest(operation_id="v", action="remove_view", view="right"))
    assert dropped.scale == 1.0 and dropped.views == ("front", "top", "isometric"), dropped
    assert _kinds(visible, doc, "dowels") >= {DIMENSION}
    assert _state(visible, doc, "sheet_compare") == []
    _state(visible, doc, "sheet_png", path=str(shots / "grow_after.png"))
