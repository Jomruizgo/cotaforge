"""Bounding boxes must come from the exact B-rep, never from a display mesh.

OCC's ``Shape.BoundBox`` silently uses an existing triangulation when the shape has one. Only the GUI
tessellates (to draw the object), so on the VISIBLE worker the box of a cylinder comes from the facetted
mesh and shrinks by up to a chord sagitta (a vertical D40 rod measured (-19.941, -19.985)..(20, 19.985))
while the headless worker, with no mesh, answers the exact box. An asserted ``bbox_after`` then fails on a
correct model. These tests run the same scenario on both workers and demand the exact box.

The visible leg needs a display and is skipped without one.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.contract import (
    CircleEntity,
    DocumentKind,
    ExtrudeRequest,
    FeatureRequest,
    MassRequest,
    NewDocumentRequest,
    PlaneName,
    SketchRequest,
)

pytestmark = pytest.mark.freecad

HAS_DISPLAY = sys.platform == "win32" or bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
TOL = 1e-6
ROD_MIN = (-20.0, -20.0, 0.0)
ROD_MAX = (20.0, 20.0, 100.0)


@pytest.fixture(
    scope="module",
    params=[
        pytest.param(False, id="headless"),
        pytest.param(
            True,
            id="visible",
            marks=pytest.mark.skipif(not HAS_DISPLAY, reason="the visible worker needs a display"),
        ),
    ],
)
def worker(
    request: pytest.FixtureRequest, freecad_root: Path, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[FreeCADAdapter]:
    with FreeCADAdapter(
        freecad_root=freecad_root,
        timeout_s=60.0,
        log_dir=tmp_path_factory.mktemp("exact-bbox-logs"),
        visible=request.param,
    ) as adapter:
        yield adapter


def _close(a: tuple[float, ...], b: tuple[float, ...]) -> bool:
    return all(abs(x - y) <= TOL for x, y in zip(a, b, strict=True))


def _standing_rod(worker: FreeCADAdapter, name: str):  # noqa: ANN202 - OperationResult, kept local
    worker.new_document(NewDocumentRequest(operation_id=f"new_{name}", kind=DocumentKind.part, name=name))
    sketch = worker.sketch(
        SketchRequest(
            operation_id=f"{name}.sketch",
            on=PlaneName.XY,
            entities=(CircleEntity(name="rod", center=(0, 0), diameter=40),),
        )
    )
    rod = worker.extrude(ExtrudeRequest(operation_id=f"{name}.extrude", sketch=sketch.sketch, depth_mm=100.0))
    assert rod.rebuild_ok
    return rod


def test_body_bounding_box_of_a_vertical_rod_is_exact(worker: FreeCADAdapter) -> None:
    _standing_rod(worker, "rod_body")
    box = worker.bounding_box(MassRequest(operation_id="rod_body.bbox"))
    assert _close(box.min, ROD_MIN), box
    assert _close(box.max, ROD_MAX), box


def test_face_descriptor_bbox_of_the_cylindrical_face_is_exact(worker: FreeCADAdapter) -> None:
    rod = _standing_rod(worker, "rod_face")
    assert rod.feature is not None
    faces = worker.faces_of(FeatureRequest(operation_id="rod_face.faces", feature=rod.feature)).faces
    (side,) = [f for f in faces if f.surface.value == "cylinder"]
    assert _close(side.bbox.min, ROD_MIN), side.bbox
    assert _close(side.bbox.max, ROD_MAX), side.bbox
