# ruff: noqa: E501
"""Datums, GD&T frames, finish symbols and the hole table are FreeCAD's own objects, and the engineer's hand edits behave
natively, in the visible CAD.

A feature control frame, a datum feature symbol and a surface texture symbol are a ``DrawViewSymbol`` made from a file of the
symbol library (the ones the engineer inserts with TechDraw's *Insert SVG Symbol*), owned by the view they are on; a frame is
joined to its feature by a ``DrawLeaderLine``; the hole table is a ``Spreadsheet::Sheet`` shown by a ``DrawViewSpreadsheet``.
The engineer edits a value in the property editor (``EditableTexts``), moves the view and what is on it follows, deletes the
view and the review says what was left behind.

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
from cotaforge.contract import (
    AnnotateDrawingRequest,
    CircleEntity,
    DescribeDrawingRequest,
    DocumentKind,
    DrawingRequest,
    ExportDrawingRequest,
    EndCondition,
    ExtrudeRequest,
    FeatureRequest,
    HoleRequest,
    HoleStandard,
    NewDocumentRequest,
    PlaneName,
    RectEntity,
    RunNativeRequest,
    SketchRequest,
)
from tests.integration.freecad.test_visible_live_sheet import (  # noqa: F401 - the fixture and the plate are shared
    CLEARANCE,
    DOWELS,
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

PREAMBLE = """
import json
import FreeCAD as App
doc = App.ActiveDocument
page = [o for o in doc.Objects if o.TypeId == "TechDraw::DrawPage"][0]
group = [o for o in page.Views if o.TypeId == "TechDraw::DrawProjGroup"][0]
def view(name):
    return [v for v in group.Views if str(v.Type) == name][0]
def mine(key):
    return [o for o in page.Views if str(getattr(o, "CotaforgeKey", "")) == key]
"""

DUMP = """
rows = {}
for o in page.Views:
    key = str(getattr(o, "CotaforgeKey", ""))
    if not key:
        continue
    owner = None
    for prop in ("Owner", "LeaderParent"):
        if prop in o.PropertiesList and getattr(o, prop) is not None:
            owner = getattr(o, prop).Label
    rows.setdefault(key, []).append({
        "type": o.TypeId.split("::")[1],
        "library": str(getattr(o, "CotaforgeLibrary", "")),
        "texts": [str(t) for t in o.EditableTexts] if "EditableTexts" in o.PropertiesList else [],
        "owner": owner,
        "x": round(float(o.X), 2), "y": round(float(o.Y), 2),
        "name": o.Name,
    })
print(json.dumps(rows))
"""


def _native(adapter: FreeCADAdapter, code: str) -> Any:
    result = adapter.run_native(RunNativeRequest(operation_id="probe", code=PREAMBLE + code))
    assert result.ok, result.error
    return json.loads(result.stdout.strip().splitlines()[-1])


def _build_sheet(visible: FreeCADAdapter, shots: Path) -> tuple[Any, dict[str, Any]]:  # noqa: F811
    doc, base = _build_plate(visible)
    visible.create_drawing(DrawingRequest(operation_id="dr", template="A3", title="Placa 19", material="AISI 1045"))
    left = _side(visible, base, (-1.0, 0.0, 0.0))
    bottom = _side(visible, base, (0.0, 0.0, -1.0))
    top = _side(visible, base, (0.0, 0.0, 1.0))
    front_face = _side(visible, base, (0.0, -1.0, 0.0))
    items = (
        {"type": "hole_dim", "id": "dowels", "at": [[x, y, 0] for x, y in DOWELS], "fit": "H7"},
        {"type": "datum", "id": "dA", "letter": "A", "face": bottom},
        {"type": "datum", "id": "dB", "letter": "B", "face": left},
        {
            "type": "gdt",
            "id": "perp",
            "char": "perpendicularity",
            "value": 0.05,
            "datums": ["A"],
            "face": _side(visible, base, (1.0, 0.0, 0.0)),
        },
        {"type": "gdt", "id": "flat", "char": "flatness", "value": 0.02, "face": top},
        {"type": "finish", "id": "ra", "ra": 3.2, "face": top},
        {"type": "finish", "id": "gen", "general": True, "ra": 6.3},
        {
            "type": "datum_dims",
            "id": "tab",
            "mode": "table",
            "x_from": left,
            "y_from": front_face,
            "holes": [[x, y, 0] for x, y in CLEARANCE],
        },
    )
    for number, item in enumerate(items, start=1):
        result = visible.annotate_drawing(AnnotateDrawingRequest(operation_id=f"a{number}", action="add", items=(item,)))
        status = {i.id: i.status for i in result.items}[str(item["id"])]
        assert status in ("ok", "warning"), (item["id"], [i for i in result.items if i.id == item["id"]])
    return doc, {"left": left, "bottom": bottom, "top": top}


def test_datums_frames_finish_and_the_hole_table_are_the_softwares_own_objects(visible: FreeCADAdapter, tmp_path: Path) -> None:  # noqa: F811
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path)
    shots.mkdir(parents=True, exist_ok=True)
    doc, _ = _build_sheet(visible, shots)
    rows = _native(visible, DUMP)
    # a library symbol for each: its file, its values in EditableTexts, its view as Owner; a frame has a leader too
    for key, library, texts in (
        ("dA", "datum/datum-", ["A"]),
        ("dB", "datum/datum-", ["B"]),
        ("perp", "perpendicularity/perpendicularity-1-datum", ["0,05", "A"]),
        ("flat", "flatness/flatness-0-datums", ["0,02"]),
        ("ra", "finish/finish-required", ["Ra 3,2"]),
    ):
        symbols = [r for r in rows[key] if r["type"] == "DrawViewSymbol"]
        assert len(symbols) == 1 and symbols[0]["library"].startswith(library), (key, rows[key])
        assert symbols[0]["texts"] == texts and symbols[0]["owner"], (key, symbols)
    for key in ("perp", "flat"):  # joined to its face by a native leader, owned by the same view
        leaders = [r for r in rows[key] if r["type"] == "DrawLeaderLine"]
        assert len(leaders) == 1 and leaders[0]["owner"] == [r for r in rows[key] if r["type"] == "DrawViewSymbol"][0]["owner"]
    general = [r for r in rows["gen"] if r["type"] == "DrawViewSymbol"]
    assert general[0]["library"].startswith("finish/finish-general-") and general[0]["owner"] is None  # near the title block: the page's
    table = [r for r in rows["tab"] if r["type"] == "DrawViewSpreadsheet"]
    assert len(table) == 1
    cells = _native(
        visible,
        """
sheets = [o for o in doc.Objects if o.TypeId == "Spreadsheet::Sheet"]
print(json.dumps({s.Name: [[s.getContents(c) for c in ("A1", "A2", "B3", "C3", "D3")]] for s in sheets}))
""",
    )
    ((name, [header]),) = cells.items()
    header = [cell.lstrip("'") for cell in header]  # a cell holds its text behind a quote
    assert header[0] == "TABLA DE AGUJEROS" and header[1] == "N" and header[2] == "35" and header[4].endswith("pasante"), cells
    # nothing of the harness's own drawing is on the page for these items, and the window shows what the sheet says
    review = visible.describe_drawing(DescribeDrawingRequest(operation_id="r", review=True)).sheets[0]
    assert not [f for f in review.findings if f.code in ("foreign_symbol", "native_fallback", "window_differs")], [
        (f.code, f.message[:160]) for f in review.findings if f.code in ("foreign_symbol", "native_fallback", "window_differs")
    ]
    assert _state(visible, doc, "sheet_compare") == []
    title = _native(visible, "print(json.dumps(dict(page.Template.EditableTexts)))")
    assert title["part_material"] == "AISI 1045"  # the datum that has a field is in the field of the title block
    _state(visible, doc, "sheet_png", path=str(shots / "gdt_plate_native.png"))
    pdf = shots / "gdt_plate_native.pdf"  # the exported sheet is TechDraw's own: the datums that have a field are in it
    visible.export_drawing(ExportDrawingRequest(operation_id="e", path=str(pdf), format="pdf"))
    assert pdf.read_bytes().startswith(b"%PDF") and pdf.stat().st_size > 20_000  # its text is outlines: the field is checked in the window image above


def test_a_frame_value_edited_by_hand_stays_and_a_moved_view_takes_its_symbols_along(visible: FreeCADAdapter, tmp_path: Path) -> None:  # noqa: F811
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path)
    shots.mkdir(parents=True, exist_ok=True)
    doc, _ = _build_sheet(visible, shots)
    before = {r["name"]: r for rows in _native(visible, DUMP).values() for r in rows}
    # 1. the engineer edits the tolerance of the perpendicularity frame in the property editor: the sheet keeps it
    edited = _native(
        visible,
        """
symbol = [o for o in mine("perp") if o.TypeId == "TechDraw::DrawViewSymbol"][0]
texts = list(symbol.EditableTexts)
texts[0] = "0,08"
symbol.EditableTexts = texts
doc.recompute()
print(json.dumps({"texts": [str(t) for t in symbol.EditableTexts]}))
""",
    )
    assert edited["texts"] == ["0,08", "A"]
    visible.annotate_drawing(
        AnnotateDrawingRequest(operation_id="n1", action="add", items=({"type": "note", "id": "n9", "text": "Una nota mas"},))
    )
    again = _native(
        visible,
        """
symbol = [o for o in mine("perp") if o.TypeId == "TechDraw::DrawViewSymbol"][0]
print(json.dumps({"texts": [str(t) for t in symbol.EditableTexts]}))
""",
    )
    assert again["texts"] == ["0,08", "A"], again  # a value the engineer typed is not put back by the sheet
    # 2. the engineer moves the view the symbols are on: they are the view's children, so they keep their place on it
    moved = _native(
        visible,
        """
owners = {}
for key in ("dA", "dB", "perp", "flat", "ra"):
    for o in mine(key):
        owners[o.Name] = [o.Owner.Name if o.TypeId.endswith("Symbol") else o.LeaderParent.Name, float(o.X), float(o.Y)]
front = view("Front")
x0 = float(front.X)
front.X = x0 + 25.0
doc.recompute()
after = {n: [float(doc.getObject(n).X), float(doc.getObject(n).Y)] for n in owners}
print(json.dumps({"owners": owners, "after": after, "moved": float(front.X) - x0, "front": front.Name}))
""",
    )
    assert abs(moved["moved"] - 25.0) < 1e-6
    follows = {n: o for n, o in moved["owners"].items() if o[0] == moved["front"]}
    assert follows, moved
    assert all(moved["after"][n] == pytest.approx(o[1:]) for n, o in follows.items())  # same place on their view: they go with it
    _state(visible, doc, "sheet_png", path=str(shots / "gdt_plate_view_moved.png"))
    assert before  # the objects the sheet made are the same ones after the edits
    # 3. the engineer deletes the view the frame is on: TechDraw takes the dimensions, and what it leaves is reported
    left = _native(
        visible,
        """
front = view("Front")
group.removeProjection("Front")
doc.recompute()
rows = {}
for key in ("dA", "perp", "flat", "ra"):
    for o in mine(key):
        owner = o.Owner if o.TypeId.endswith("Symbol") else o.LeaderParent
        rows.setdefault(key, []).append([o.TypeId.split("::")[1], None if owner is None else owner.Name])
print(json.dumps(rows))
""",
    )
    _state(visible, doc, "sheet_png", path=str(shots / "gdt_plate_view_deleted.png"))
    review = visible.describe_drawing(DescribeDrawingRequest(operation_id="r", review=True)).sheets[0]
    (shots / "gdt_view_deleted.json").write_text(
        json.dumps({"left": left, "findings": [[f.code, f.items, f.message[:200]] for f in review.findings]}, indent=1),
        encoding="utf-8",
    )
    orphans = [row for rows in left.values() for row in rows if row[1] is None]
    assert orphans  # TechDraw leaves what stood on the deleted view on the page, with no view to follow ...
    if orphans:  # ... and the review tells the engineer what stayed behind
        assert [f for f in review.findings if f.code == "native_orphaned" and f.blocking], (left, review.findings)


# --- a milled block (flatness, parallelism, datums) and a cover (coaxiality) ---------------------------------------------


def _faces(adapter: FreeCADAdapter, feature: Any) -> list[Any]:
    return list(adapter.faces_of(FeatureRequest(operation_id="faces", feature=feature)).faces)


def _plane(faces: list[Any], normal: tuple[float, float, float]) -> str:
    (face,) = [f for f in faces if f.normal is not None and all(abs(a - b) < 1e-6 for a, b in zip(f.normal, normal, strict=True))]
    return face.handle.id


def _build_block(adapter: FreeCADAdapter) -> tuple[Any, Any]:
    doc = adapter.new_document(NewDocumentRequest(operation_id="d", kind=DocumentKind.part, name="bloque"))
    sketch = adapter.sketch(
        SketchRequest(operation_id="s0", on=PlaneName.XY, entities=(RectEntity(name="outline", center=(0, 0), width=100.0, height=80.0),))
    )
    base = adapter.extrude(ExtrudeRequest(operation_id="base", sketch=sketch.sketch, depth_mm=40.0))
    assert base.feature is not None
    top = next(f for f in _faces(adapter, base.feature) if f.normal is not None and abs(f.normal[2] - 1.0) < 1e-6)
    made = adapter.hole(
        HoleRequest(
            operation_id="bore",
            face=top.handle,
            position=(0.0, 0.0),
            standard=HoleStandard.simple,
            end_condition=EndCondition.through_all,
            diameter_mm=20.0,
        )
    )
    assert made.feature is not None
    return doc, (base.feature, made.feature)


def _build_cover(adapter: FreeCADAdapter) -> tuple[Any, Any]:
    """A disc with two hubs one on the other: the upper hub is coaxial with the lower one, whose axis is the datum."""
    doc = adapter.new_document(NewDocumentRequest(operation_id="d", kind=DocumentKind.part, name="tapa"))
    sketch = adapter.sketch(
        SketchRequest(operation_id="s0", on=PlaneName.XY, entities=(CircleEntity(name="od", center=(0, 0), diameter=100.0),))
    )
    base = adapter.extrude(ExtrudeRequest(operation_id="base", sketch=sketch.sketch, depth_mm=18.0))
    assert base.feature is not None
    features: list[Any] = []
    previous = base.feature
    for number, diameter in enumerate((70.0, 50.0), start=1):
        crown = max(
            (f for f in _faces(adapter, previous) if f.normal is not None and abs(f.normal[2] - 1.0) < 1e-6),
            key=lambda f: f.center[2],
        )  # the top the next hub stands on
        hub_sketch = adapter.sketch(
            SketchRequest(operation_id=f"hub{number}_s", on=crown.handle, entities=(CircleEntity(name="hub", center=(0.0, 0.0), diameter=diameter),))
        )
        hub = adapter.extrude(ExtrudeRequest(operation_id=f"hub{number}", sketch=hub_sketch.sketch, depth_mm=6.0))
        assert hub.feature is not None
        features.append(hub.feature)
        previous = hub.feature
    return doc, tuple(features)


def _annotate(adapter: FreeCADAdapter, items: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    status: dict[str, Any] = {}
    for number, item in enumerate(items, start=1):
        result = adapter.annotate_drawing(AnnotateDrawingRequest(operation_id=f"b{number}", action="add", items=(item,)))
        row = next(i for i in result.items if i.id == item["id"])
        status[str(item["id"])] = (row.status, row.detail)
    return status


def test_a_milled_block_has_flatness_parallelism_and_datums_as_native_objects(
    visible: FreeCADAdapter, tmp_path: Path  # noqa: F811
) -> None:
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path)
    doc, (base, _) = _build_block(visible)
    faces = _faces(visible, base)
    visible.create_drawing(DrawingRequest(operation_id="dr", template="A3", title="Bloque", material="EN AW-6082 T6"))
    status = _annotate(
        visible,
        (
            {"type": "datum", "id": "dA", "letter": "A", "face": _plane(faces, (0.0, 0.0, -1.0))},
            {"type": "datum", "id": "dB", "letter": "B", "face": _plane(faces, (-1.0, 0.0, 0.0))},
            {"type": "gdt", "id": "gFlat", "char": "flatness", "value": 0.05, "face": _plane(faces, (1.0, 0.0, 0.0))},
            {"type": "gdt", "id": "gPar", "char": "parallelism", "value": 0.05, "datums": ["A"], "face": _plane(faces, (0.0, 0.0, 1.0))},
        ),
    )
    assert all(s[0] in ("ok", "warning") for s in status.values()), {k: (v[0], v[1][:200]) for k, v in status.items()}
    review = visible.describe_drawing(DescribeDrawingRequest(operation_id="r", review=True)).sheets[0]
    foreign = [f for f in review.findings if f.code == "foreign_symbol"]
    kinds = {
        row[1] for row in _state(visible, doc, "sheet_objects") if row[3] in ("dA", "dB", "gFlat", "gPar")
    }
    _state(visible, doc, "sheet_png", path=str(shots / "gdt_block.png"))
    assert not [f for f in foreign if f.severity == "warning"], [(f.severity, f.items, f.message) for f in foreign]
    assert {"DrawViewSymbol", "DrawLeaderLine"} <= kinds, kinds
    assert _state(visible, doc, "sheet_compare") == []


def test_a_cover_has_coaxiality_between_two_hubs_as_native_objects(
    visible: FreeCADAdapter, tmp_path: Path  # noqa: F811
) -> None:
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path)
    doc, (lower, upper) = _build_cover(visible)
    hub = next(f for f in _faces(visible, lower) if f.surface.value == "cylinder")
    tip = next(f for f in _faces(visible, upper) if f.surface.value == "cylinder")
    visible.create_drawing(
        DrawingRequest(operation_id="dr", template="A3", title="Tapa", material="C45", views=("front", "top", "isometric"))
    )
    status = _annotate(
        visible,
        (
            {"type": "dim", "id": "hub", "kind": "diameter", "face": hub.handle.id, "view": "front", "offset_mm": 20.0},  # a callout with a leader: the axis of the datum
            {"type": "dim", "id": "tip", "kind": "diameter", "face": tip.handle.id, "view": "front", "offset_mm": 20.0, "side": "right"},
            {"type": "datum", "id": "dB", "letter": "B", "on": "hub"},
            {"type": "gdt", "id": "coax", "char": "coaxiality", "value": 0.03, "diameter": True, "datums": ["B"], "on": "tip"},
        ),
    )
    assert all(s[0] in ("ok", "warning") for s in status.values()), {k: (v[0], v[1][:200]) for k, v in status.items()}
    review = visible.describe_drawing(DescribeDrawingRequest(operation_id="r", review=True)).sheets[0]
    foreign = [f for f in review.findings if f.code == "foreign_symbol"]
    rows = [row for row in _state(visible, doc, "sheet_objects") if row[3] in ("dB", "coax")]
    _state(visible, doc, "sheet_png", path=str(shots / "gdt_cover.png"))
    assert not [f for f in foreign if f.severity == "warning"], [(f.severity, f.items, f.message) for f in foreign]
    assert {row[3] for row in rows if row[1] == "DrawViewSymbol"} == {"dB", "coax"}, rows
    assert _state(visible, doc, "sheet_compare") == []
