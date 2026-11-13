# ruff: noqa: E501
"""A geometric tolerance is a feature control frame, not a note: a block with a precision bore (Ø20 H7) carries its
class and limits on the callout, a position tolerance to the datums A and B hung from that callout, and the two datum
symbols the frame cites. The sheet carries no note asking the shop to verify a table, and the review has nothing to say
about notes, undefined datums or unused datums.

Needs a display (on Windows the GUI binary opens its own window); skipped without one, carries the ``visible`` marker (run
serially). Set ``COTAFORGE_TEST_SHOTS`` to a folder to keep the window image and the metrics.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.contract import (
    DocumentKind,
    DrawingRequest,
    EndCondition,
    ExtrudeRequest,
    HoleRequest,
    HoleStandard,
    NewDocumentRequest,
    PlaneName,
    RectEntity,
    SketchRequest,
)
from tests.integration.freecad.test_visible_live_sheet import _side, _top, visible  # noqa: F401 - the fixture is shared
from tests.integration.freecad.test_visible_quality_sheet import _add, _measure

pytestmark = [
    pytest.mark.freecad,
    pytest.mark.visible,
    pytest.mark.skipif(
        sys.platform != "win32" and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")),
        reason="the visible worker needs a display (DISPLAY / WAYLAND_DISPLAY)",
    ),
]

BORE_AT = (40.0, 30.0)  # model mm: 40 from the left face, 30 from the front face (the block is 100 x 60)


def test_a_precision_bore_carries_its_class_and_a_position_frame_to_two_datums(
    visible: FreeCADAdapter,  # noqa: F811
    tmp_path: Path,
) -> None:
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path)
    shots.mkdir(parents=True, exist_ok=True)
    doc = visible.new_document(NewDocumentRequest(operation_id="d", kind=DocumentKind.part, name="bloque"))
    outline = RectEntity(name="outline", center=(50, 30), width=100.0, height=60.0)
    sketch = visible.sketch(SketchRequest(operation_id="s0", on=PlaneName.XY, entities=(outline,)))
    base = visible.extrude(ExtrudeRequest(operation_id="base", sketch=sketch.sketch, depth_mm=30.0))
    assert base.feature is not None
    top = _top(visible, base.feature)
    visible.hole(
        HoleRequest(
            operation_id="bore",
            face=top.handle,
            position=(BORE_AT[0] - top.center[0], BORE_AT[1] - top.center[1]),
            diameter_mm=20.0,
            standard=HoleStandard.simple,
            end_condition=EndCondition.through_all,
        )
    )
    left = _side(visible, base.feature, (-1.0, 0.0, 0.0))
    front = _side(visible, base.feature, (0.0, -1.0, 0.0))

    visible.create_drawing(
        DrawingRequest(operation_id="v1", template="A3", title="Bloque", views=("front", "top", "iso"))
    )
    _add(visible, "a1", [{"type": "view_lines", "id": "vl"}])
    _add(visible, "a2", [{"type": "hole_dim", "id": "bore", "at": [[BORE_AT[0], BORE_AT[1], 0]], "fit": "H7"}])
    _add(
        visible,
        "a3",
        [
            {"type": "datum", "id": "dA", "letter": "A", "face": front},
            {"type": "datum", "id": "dB", "letter": "B", "face": left},
        ],
    )
    result = _add(
        visible,
        "a4",
        [
            {
                "type": "gdt",
                "id": "pos",
                "char": "position",
                "value": 0.05,
                "diameter": True,
                "datums": ["A", "B"],
                "on": "bore",
            },
            {"type": "note", "id": "n1", "text": "Rebabas eliminadas, aristas vivas rotas 0,3 x 45"},
        ],
    )
    frame = next(i for i in result.items if i.id == "pos")
    print("FRAME", frame.status, frame.detail)

    metrics = _measure(visible, doc, None, shots, "gdt_block")
    assert frame.status == "ok", frame.detail
    texts = metrics["texts"]
    assert any("H7 (+0,021/0)" in t for t in texts), texts  # the class and the limits of the table
    assert "Ø0,05" in texts and texts.count("A") >= 2 and texts.count("B") >= 2, texts  # the frame and the two datums
    assert not any(w in t.lower() for t in texts for w in ("transcrit", "verificar", "verify")), texts
    flagged = {row[0] for row in metrics["review"]}
    assert not flagged & {"gdt_as_note", "datum_undefined", "datum_unused", "gdt_not_located", "item_error"}, metrics[
        "review"
    ]
