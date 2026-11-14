"""The drawing built LIVE in the visible worker: after each item is added, the TechDraw page of the FreeCAD window holds
its objects (asserted from the worker) and the sheet window shows them (the window is painted to an image and the image
is not the one before). The PDF is written from the same sheet model, so it shows the same items at the same places.

Needs a display (on Windows the GUI binary opens its own window); skipped without one, carries the ``visible`` marker
(run serially). Set ``COTAFORGE_TEST_SHOTS`` to a folder to keep the window images, the PDF and the sheet image.
"""

from __future__ import annotations

import hashlib
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.contract import (
    AnnotateDrawingRequest,
    CaptureDrawingRequest,
    CircleEntity,
    CutRequest,
    DescribeDrawingRequest,
    DocumentInfo,
    DocumentKind,
    DrawingRequest,
    EndCondition,
    ExportDrawingRequest,
    ExtrudeRequest,
    FaceInfo,
    FeatureHandle,
    FeatureRequest,
    HoleRequest,
    HoleStandard,
    HoleThread,
    NewDocumentRequest,
    PlaneName,
    RectEntity,
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

DOWELS = ((15.0, 12.0), (85.0, 12.0), (15.0, 48.0), (85.0, 48.0))
CLEARANCE = ((35.0, 30.0), (65.0, 30.0))
TAPPED = (50.0, 12.0)
THICKNESS = 19.0


@pytest.fixture
def visible(freecad_root: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[FreeCADAdapter]:
    with FreeCADAdapter(
        freecad_root=freecad_root,
        timeout_s=120.0,
        log_dir=tmp_path_factory.mktemp("live-sheet-logs"),
        visible=True,
        enable_test_hooks=True,
    ) as adapter:
        yield adapter


def _state(adapter: FreeCADAdapter, doc: DocumentInfo, what: str, **extra: Any) -> Any:
    rpc = adapter._rpc  # noqa: SLF001 - integration test, same seam test_visible_view_drawing.py uses
    assert rpc is not None
    params = {"operation_id": "state", "what": what, "document": doc.name, **extra}
    return rpc.call("test_view_state", params, timeout_s=60.0)["value"]


def _top(adapter: FreeCADAdapter, base: FeatureHandle) -> FaceInfo:
    faces = adapter.faces_of(FeatureRequest(operation_id="faces", feature=base)).faces
    (top,) = [f for f in faces if f.normal is not None and abs(f.normal[2] - 1.0) < 1e-6]
    return top


def _side(adapter: FreeCADAdapter, base: FeatureHandle, normal: tuple[float, float, float]) -> str:
    faces = adapter.faces_of(FeatureRequest(operation_id="faces", feature=base)).faces
    (face,) = [
        f
        for f in faces
        if f.normal is not None and all(abs(a - b) < 1e-6 for a, b in zip(f.normal, normal, strict=True))
    ]
    return face.handle.id


def _build_plate(adapter: FreeCADAdapter) -> tuple[DocumentInfo, FeatureHandle]:
    doc = adapter.new_document(NewDocumentRequest(operation_id="d", kind=DocumentKind.part, name="placa"))
    outline = RectEntity(name="outline", center=(50, 30), width=100.0, height=60.0)
    sketch = adapter.sketch(SketchRequest(operation_id="s0", on=PlaneName.XY, entities=(outline,)))
    base = adapter.extrude(ExtrudeRequest(operation_id="base", sketch=sketch.sketch, depth_mm=THICKNESS))
    assert base.feature is not None

    def hole(name: str, where: tuple[float, float], **fields: Any) -> None:
        top = _top(adapter, base.feature)  # type: ignore[arg-type]
        position = (where[0] - top.center[0], where[1] - top.center[1])
        params: dict[str, Any] = {"standard": HoleStandard.simple, "end_condition": EndCondition.through_all}
        params.update(fields)
        adapter.hole(HoleRequest(operation_id=name, face=top.handle, position=position, **params))

    for number, where in enumerate(DOWELS, start=1):
        hole(f"dowel{number}", where, diameter_mm=6.0)
    for number, where in enumerate(CLEARANCE, start=1):
        hole(f"clear{number}", where, diameter_mm=9.0)
        top = _top(adapter, base.feature)
        centre = (where[0] - top.center[0], where[1] - top.center[1])
        pocket = adapter.sketch(
            SketchRequest(
                operation_id=f"cbs{number}",
                on=top.handle,
                entities=(CircleEntity(name="cb", center=centre, diameter=14.0),),
            )
        )
        adapter.cut(CutRequest(operation_id=f"cb{number}", sketch=pocket.sketch, depth_mm=9.0))
    hole(
        "tapped",
        TAPPED,
        diameter_mm=5.0,
        standard=HoleStandard.iso_tapped,
        size="M6",
        end_condition=EndCondition.blind,
        depth_mm=12.0,
        thread=HoleThread(size="M6", depth_mm=10.0),
    )
    return doc, base.feature


def _digest(path: Path) -> str:
    return hashlib.sha1(path.read_bytes(), usedforsecurity=False).hexdigest()


def test_each_item_shows_in_the_sheet_window_as_it_is_added(visible: FreeCADAdapter, tmp_path: Path) -> None:
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path)
    shots.mkdir(parents=True, exist_ok=True)
    doc, base = _build_plate(visible)
    created = visible.create_drawing(DrawingRequest(operation_id="dr", template="A3", title="Placa 19", scale=1.0))
    assert created.waited_s < 90.0
    first = shots / "step00_views.png"
    _state(visible, doc, "sheet_png", path=str(first))
    steps: list[tuple[str, dict[str, Any]]] = [
        ("view_lines", {"type": "view_lines", "id": "view_lines"}),
        ("dowels", {"type": "hole_dim", "id": "dowels", "at": [[x, y, 0] for x, y in DOWELS], "fit": "H7"}),
        ("clear", {"type": "hole_dim", "id": "clear", "at": [[x, y, 0] for x, y in CLEARANCE]}),
        ("tap", {"type": "hole_dim", "id": "tap", "at": [[TAPPED[0], TAPPED[1], 0]]}),
        ("dA", {"type": "datum", "id": "dA", "letter": "A", "face": _side(visible, base, (0.0, 0.0, -1.0))}),
        ("dB", {"type": "datum", "id": "dB", "letter": "B", "face": _side(visible, base, (-1.0, 0.0, 0.0))}),
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
        ),
        ("tbl", {"type": "datum_dims", "id": "tbl", "mode": "table"}),
        ("n1", {"type": "note", "id": "n1", "text": "Rebabas eliminadas, aristas vivas rotas 0,3 x 45"}),
        (
            "rv",
            {
                "type": "revision",
                "id": "rv",
                "index": "A",
                "description": "Primera emision",
                "date": "2026-10-26",
                "by": "JR",
            },
        ),
    ]
    previous = _digest(first)
    expected: set[str] = set()  # the default location and view_lines are the views' own cosmetic edges: no object
    for number, (key, item) in enumerate(steps, start=1):
        result = visible.annotate_drawing(
            AnnotateDrawingRequest(operation_id=f"a{number}", action="add", items=(item,))
        )
        assert result.changed == (key,) and result.warnings == (), (key, result.warnings)
        assert {i.id: i.status for i in result.items}[key] in ("ok", "warning")
        shot = shots / f"step{number:02d}_{key}.png"
        _state(visible, doc, "sheet_png", path=str(shot))
        objects = _state(visible, doc, "sheet_objects")
        if key != "rv":  # a revision is a row of the title block: TechDraw's own template text
            if key != "view_lines":
                expected.add(key)
            # loc: its dimensions, once datums locate the holes; the rest of it and threads are the views' edges
            assert {row[3] for row in objects} - {"threads", "loc"} == expected, (key, objects)
            assert result.window != "unchanged", result.window  # (the default location may be redrawn by what is added)
        digest = _digest(shot)
        assert digest != previous, f"the window did not change after adding {key}"
        previous = digest
        assert _state(visible, doc, "sheet_compare") == [], key
        if number == 1:  # ISO 128: the outline of the views is thick, the dimension lines and hidden lines thin
            weights = _state(visible, doc, "sheet_weights")
            assert weights["outline"] >= 1.8 * weights["thin"] > 0, weights
            assert weights["outline"] >= 1.8 * weights["dimension"], weights
    final = shots / "final_window.png"
    _state(visible, doc, "sheet_png", path=str(final))
    pdf, png = shots / "final.pdf", shots / "final_sheet.png"
    visible.export_drawing(ExportDrawingRequest(operation_id="e", path=str(pdf), format="pdf"))
    visible.capture_drawing(CaptureDrawingRequest(operation_id="c", path=str(png), width=1800))
    assert pdf.read_bytes().startswith(b"%PDF")
    review = visible.describe_drawing(DescribeDrawingRequest(operation_id="r", review=True))
    assert not [f for f in review.sheets[0].findings if f.code == "window_differs"]
    assert _state(visible, doc, "sheet_compare") == []
    # the centre marks: at the projected centre of every hole (0.01 mm in the objects), and painted there (0.6 mm: the
    # window is measured in pixels) -- the owner saw them on the circles' circumference
    assert _state(visible, doc, "sheet_cross_check") == []
    painted = _state(visible, doc, "sheet_cross_pixels")
    assert len(painted) >= 7, painted
    assert all(row[2] is not None and abs(row[2]) <= 0.6 for row in painted), painted
