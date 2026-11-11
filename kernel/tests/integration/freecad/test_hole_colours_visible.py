# ruff: noqa: E501
"""The faces of plain holes in the 3D window of the REAL visible worker: every face about the axis of a drilled hole is
painted green in the body's own ``ViewObject.DiffuseColor`` (the bore, the wall and floor of a counterbore, the cone of a
countersink, the floor of a blind hole), whether the hole operation made the counterbore or a pocket concentric with the hole
did, and nothing else of the part is painted.

Needs a display (Windows opens its own window); carries the ``visible`` marker."""

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.contract import (
    CircleEntity,
    CutRequest,
    DocumentKind,
    EndCondition,
    ExtrudeRequest,
    FaceInfo,
    FeatureRequest,
    HoleCounterbore,
    HoleCountersink,
    HoleRequest,
    HoleStandard,
    NewDocumentRequest,
    PlaneName,
    RectEntity,
    SketchRequest,
)
from tests.integration.freecad.worker_tools import call_hook

pytestmark = [
    pytest.mark.freecad,
    pytest.mark.visible,
    pytest.mark.skipif(
        sys.platform != "win32" and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")),
        reason="the visible worker needs a display (DISPLAY / WAYLAND_DISPLAY)",
    ),
]

GREEN = (0, 192, 0)
HOLES = {  # name -> where on the 140 x 60 plate
    "through": (20.0, 15.0),
    "counterbore": (50.0, 15.0),
    "countersink": (80.0, 15.0),
    "blind": (110.0, 15.0),
    "pocketed": (35.0, 45.0),
    "pocketed_blind": (85.0, 45.0),
}
FACES_ABOUT_THE_AXIS = {  # the bore, plus what opens it
    "through": 1,
    "counterbore": 3,
    "countersink": 2,
    "blind": 2,
    "pocketed": 3,
    "pocketed_blind": 4,
}

READ_THE_WINDOW = f"""
import math
HOLES = {HOLES!r}
body = [o for o in doc.Objects if o.TypeId == "PartDesign::Body"][0]
colours = [list(c) for c in body.ViewObject.DiffuseColor]
faces = body.Shape.Faces
if len(colours) == 1:
    colours = colours * len(faces)
def near(a, b):
    return abs(a - b) < 1e-3
def about(face, where):
    s = face.Surface
    kind = type(s).__name__
    if kind in ("Cylinder", "Cone", "Plane") and not near(abs(s.Axis.z), 1.0):
        return False
    if kind == "Cylinder":
        return near(s.Center.x, where[0]) and near(s.Center.y, where[1])
    if kind == "Cone":
        return near(s.Apex.x, where[0]) and near(s.Apex.y, where[1])
    if kind == "Plane":
        return bool(face.Edges) and all(
            type(e.Curve).__name__ == "Circle" and near(e.Curve.Center.x, where[0]) and near(e.Curve.Center.y, where[1])
            for e in face.Edges
        )
    return False
out = {{"count": len(faces), "shown": len(colours), "holes": {{}}, "painted": []}}
for name, where in HOLES.items():
    out["holes"][name] = [[i, [round(c * 255) for c in colours[i][:3]]] for i, f in enumerate(faces) if about(f, where)]
out["painted"] = [i for i, c in enumerate(colours) if tuple(round(v * 255) for v in c[:3]) == (0, 192, 0)]
RESULT = out
"""


@pytest.fixture
def window(freecad_root: Path, tmp_path: Path) -> Iterator[FreeCADAdapter]:
    with FreeCADAdapter(
        freecad_root=freecad_root, timeout_s=120.0, log_dir=tmp_path / "logs", visible=True, enable_test_hooks=True
    ) as adapter:
        yield adapter


def _top(adapter: FreeCADAdapter, feature: object) -> FaceInfo:
    faces = adapter.faces_of(FeatureRequest(operation_id="faces", feature=feature)).faces  # type: ignore[arg-type]
    top = [f for f in faces if f.normal is not None and abs(f.normal[2] - 1.0) < 1e-6 and f.center[2] > 5.0]
    assert len(top) == 1, [f.handle.id for f in top]
    return top[0]


def _drill(adapter: FreeCADAdapter, base: object, name: str, **fields: object) -> None:
    top = _top(adapter, base)
    where = HOLES[name]
    adapter.hole(
        HoleRequest(
            operation_id=f"hole_{name}",
            face=top.handle,
            position=(where[0] - top.center[0], where[1] - top.center[1]),
            standard=HoleStandard.simple,
            diameter_mm=6.0,
            **fields,  # type: ignore[arg-type]
        )
    )


def _pocket(adapter: FreeCADAdapter, base: object, name: str) -> None:
    top = _top(adapter, base)
    where = HOLES[name]
    sketch = adapter.sketch(
        SketchRequest(
            operation_id=f"cb_{name}",
            on=top.handle,
            entities=(
                CircleEntity(name="cb", center=(where[0] - top.center[0], where[1] - top.center[1]), diameter=11.0),
            ),
        )
    )
    adapter.cut(
        CutRequest(operation_id=f"pocket_{name}", sketch=sketch.sketch, end_condition=EndCondition.blind, depth_mm=3.0)
    )


def test_every_face_of_a_plain_hole_is_green_in_the_window(window: FreeCADAdapter, tmp_path: Path) -> None:
    window.new_document(NewDocumentRequest(operation_id="new", kind=DocumentKind.part, name="holes"))
    sketch = window.sketch(
        SketchRequest(
            operation_id="s0", on=PlaneName.XY, entities=(RectEntity(name="o", center=(70, 30), width=140, height=60),)
        )
    )
    base = window.extrude(ExtrudeRequest(operation_id="base", sketch=sketch.sketch, depth_mm=10.0)).feature
    assert base is not None
    _drill(window, base, "through", end_condition=EndCondition.through_all)
    _drill(
        window,
        base,
        "counterbore",
        end_condition=EndCondition.through_all,
        counterbore=HoleCounterbore(diameter_mm=11.0, depth_mm=4.0),
    )
    _drill(
        window,
        base,
        "countersink",
        end_condition=EndCondition.through_all,
        countersink=HoleCountersink(diameter_mm=12.0, angle_deg=90.0),
    )
    _drill(window, base, "blind", end_condition=EndCondition.blind, depth_mm=6.0)
    _drill(window, base, "pocketed", end_condition=EndCondition.through_all)
    _pocket(window, base, "pocketed")
    _drill(window, base, "pocketed_blind", end_condition=EndCondition.blind, depth_mm=6.0)
    _pocket(window, base, "pocketed_blind")

    script = tmp_path / "read_window.py"
    script.write_text(READ_THE_WINDOW, encoding="utf-8")
    seen = call_hook(window, "test_view_state", {"what": "script", "path": str(script)})["value"]
    assert seen["shown"] == seen["count"], (
        f"the window holds a colour list of {seen['shown']} for {seen['count']} faces"
    )
    for name, expected in FACES_ABOUT_THE_AXIS.items():
        faces = seen["holes"][name]
        assert len(faces) == expected, (name, faces)
        assert all(tuple(rgb) == GREEN for _, rgb in faces), (name, faces)
    about = {index for faces in seen["holes"].values() for index, _ in faces}
    assert set(seen["painted"]) == about, (sorted(seen["painted"]), sorted(about))  # nothing else of the part is green
