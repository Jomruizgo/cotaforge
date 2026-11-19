# ruff: noqa: E501
"""The colours the harness puts on the 3D view, read from the PICTURE the view renders (``capture``), not from a property.

On FreeCAD 1.1 the 4th value of a face colour is its opacity, and a PartDesign body shows what its Tip shows. A colour the
harness wrote with the old meaning (0 = opaque) or on the Body read back perfectly from the property and was not on the
screen: the face disappeared, or nothing changed. Every check here counts pixels of the colour and fails on that code.

Needs a display (Windows opens its own window); carries the ``visible`` marker."""

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.contract import (
    BoxRequest,
    CaptureRequest,
    CircleEntity,
    CutRequest,
    DocumentKind,
    EndCondition,
    ExtrudeRequest,
    FaceInfo,
    FaceRequest,
    FeatureRequest,
    HoleRequest,
    HoleStandard,
    NewDocumentRequest,
    PlaneName,
    RectEntity,
    ShowEntitiesRequest,
    SketchRequest,
    StyleRequest,
)
from tests.integration.freecad import png_pixels as px

pytestmark = [
    pytest.mark.freecad,
    pytest.mark.visible,
    pytest.mark.skipif(
        sys.platform != "win32" and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")),
        reason="the visible worker needs a display (DISPLAY / WAYLAND_DISPLAY)",
    ),
]

SHOWN = 2000  # a picture of 900 x 600: a whole face shows ~ 40000 pixels, the 12 mm hole ~ 3000, an edge ~ 600
EDGE = 150


@pytest.fixture
def window(freecad_root: Path, tmp_path: Path) -> Iterator[FreeCADAdapter]:
    with FreeCADAdapter(
        freecad_root=freecad_root, timeout_s=120.0, log_dir=tmp_path / "logs", visible=True, enable_test_hooks=True
    ) as adapter:
        yield adapter


def _shot(adapter: FreeCADAdapter, tmp_path: Path, name: str) -> Path:
    path = tmp_path / f"{name}.png"
    adapter.capture(CaptureRequest(operation_id=f"cap_{name}", path=str(path), width=900, height=600))
    return path


def _plate(adapter: FreeCADAdapter, name: str) -> tuple[object, object, FaceInfo]:
    """A PartDesign body: a 60 x 40 x 15 plate. Returns its first feature, its body and its top face."""
    adapter.new_document(NewDocumentRequest(operation_id="new", kind=DocumentKind.part, name=name))
    sketch = adapter.sketch(
        SketchRequest(
            operation_id="s0", on=PlaneName.XY, entities=(RectEntity(name="o", center=(0, 0), width=60, height=40),)
        )
    )
    made = adapter.extrude(ExtrudeRequest(operation_id="e0", sketch=sketch.sketch, depth_mm=15.0))
    assert made.feature is not None
    faces = adapter.faces_of(FeatureRequest(operation_id="f", feature=made.feature)).faces
    top = [f for f in faces if f.normal is not None and abs(f.normal[2] - 1.0) < 1e-6 and f.center[2] > 5.0]
    assert len(top) == 1
    return made.feature, made.created_bodies[0], top[0]


def test_a_highlighted_face_is_orange_on_the_screen_and_the_part_comes_back_when_it_is_cleared(
    window: FreeCADAdapter, tmp_path: Path
) -> None:
    _feature, _body, top = _plate(window, "face")
    plain = _shot(window, tmp_path, "plain")
    grey = px.count(plain, px.is_grey)
    assert grey > 5 * SHOWN and px.count(plain, px.is_orange) == 0

    window.show_entities(ShowEntitiesRequest(operation_id="show", entities=(top.handle,)))
    lit = _shot(window, tmp_path, "face_highlight")
    assert px.count(lit, px.is_orange) > 5 * SHOWN  # the top face, in the picture
    assert px.count(lit, px.is_grey) < grey  # and it is not grey any more: the faces next to it still are
    assert px.count(lit, px.is_grey) > 0.2 * grey

    window.show_entities(ShowEntitiesRequest(operation_id="clear", clear=True))
    back = _shot(window, tmp_path, "face_cleared")
    assert px.count(back, px.is_orange) == 0
    assert px.count(back, px.is_grey) == grey  # exactly the part it was


def test_a_highlighted_edge_is_orange_on_the_screen_and_black_again_when_it_is_cleared(
    window: FreeCADAdapter, tmp_path: Path
) -> None:
    _feature, _body, top = _plate(window, "edge")
    edges = window.edges_of(FaceRequest(operation_id="ed", face=top.handle)).edges
    assert px.count(_shot(window, tmp_path, "plain"), px.is_orange) == 0

    window.show_entities(ShowEntitiesRequest(operation_id="show", entities=(edges[0].handle,)))
    assert px.count(_shot(window, tmp_path, "edge_highlight"), px.is_orange) > EDGE

    window.show_entities(ShowEntitiesRequest(operation_id="clear", clear=True))
    assert px.count(_shot(window, tmp_path, "edge_cleared"), px.is_orange) == 0


def test_a_face_and_an_edge_of_the_same_body_are_both_put_back(window: FreeCADAdapter, tmp_path: Path) -> None:
    _feature, _body, top = _plate(window, "both")
    edges = window.edges_of(FaceRequest(operation_id="ed", face=top.handle)).edges
    grey = px.count(_shot(window, tmp_path, "plain"), px.is_grey)

    window.show_entities(ShowEntitiesRequest(operation_id="show", entities=(top.handle, edges[0].handle)))
    assert px.count(_shot(window, tmp_path, "both"), px.is_orange) > 5 * SHOWN

    window.show_entities(ShowEntitiesRequest(operation_id="clear", clear=True))
    back = _shot(window, tmp_path, "both_cleared")
    assert px.count(back, px.is_orange) == 0  # neither the face nor the edge stays orange
    assert px.count(back, px.is_grey) == grey


def test_a_highlighted_face_of_a_part_object_is_orange_on_the_screen(window: FreeCADAdapter, tmp_path: Path) -> None:
    window.new_document(NewDocumentRequest(operation_id="new", kind=DocumentKind.part, name="box"))
    box = window.box(BoxRequest(operation_id="box", size=(60.0, 40.0, 15.0)))
    assert box.feature is not None
    faces = window.faces_of(FeatureRequest(operation_id="f", feature=box.feature)).faces
    top = [f for f in faces if f.normal is not None and abs(f.normal[2] - 1.0) < 1e-6][0]
    window.show_entities(ShowEntitiesRequest(operation_id="show", entities=(top.handle,)))
    assert px.count(_shot(window, tmp_path, "box_highlight"), px.is_orange) > SHOWN
    window.show_entities(ShowEntitiesRequest(operation_id="clear", clear=True))
    assert px.count(_shot(window, tmp_path, "box_cleared"), px.is_orange) == 0


def test_a_precision_hole_is_painted_on_the_screen_and_stays_painted_after_a_later_feature(
    window: FreeCADAdapter, tmp_path: Path
) -> None:
    feature, _body, top = _plate(window, "hole")
    window.hole(
        HoleRequest(
            operation_id="h0",
            face=top.handle,
            position=(0.0, 0.0),
            standard=HoleStandard.simple,
            diameter_mm=12.0,
            end_condition=EndCondition.through_all,
            fit="H7",
        )
    )
    first = px.count(_shot(window, tmp_path, "hole"), px.is_pink)
    assert first > SHOWN // 2  # the bore of the hole, in the colour of its class

    # a later feature (a pocket in a corner) makes a new Tip: the hole is painted again on it
    faces = window.faces_of(FeatureRequest(operation_id="f2", feature=feature)).faces
    top_now = [f for f in faces if f.normal is not None and abs(f.normal[2] - 1.0) < 1e-6 and f.center[2] > 5.0][0]
    sketch = window.sketch(
        SketchRequest(
            operation_id="s1",
            on=top_now.handle,
            entities=(
                CircleEntity(name="p", center=(20.0 - top_now.center[0], 10.0 - top_now.center[1]), diameter=8.0),
            ),
        )
    )
    window.cut(CutRequest(operation_id="cut", sketch=sketch.sketch, end_condition=EndCondition.blind, depth_mm=3.0))
    assert px.count(_shot(window, tmp_path, "hole_after_cut"), px.is_pink) > SHOWN // 2


def test_a_highlight_over_a_painted_hole_puts_the_hole_colour_back(window: FreeCADAdapter, tmp_path: Path) -> None:
    _feature, _body, top = _plate(window, "over")
    window.hole(
        HoleRequest(
            operation_id="h0",
            face=top.handle,
            position=(0.0, 0.0),
            standard=HoleStandard.simple,
            diameter_mm=12.0,
            end_condition=EndCondition.through_all,
            fit="H7",
        )
    )
    painted = px.count(_shot(window, tmp_path, "hole"), px.is_pink)
    assert painted > SHOWN // 2
    window.show_entities(ShowEntitiesRequest(operation_id="show", entities=(top.handle,)))
    assert px.count(_shot(window, tmp_path, "lit"), px.is_orange) > 5 * SHOWN
    window.show_entities(ShowEntitiesRequest(operation_id="clear", clear=True))
    back = _shot(window, tmp_path, "cleared")
    assert px.count(back, px.is_orange) == 0
    assert px.count(back, px.is_pink) == painted  # the hole is the colour it was before the highlight


def test_a_body_given_a_colour_shows_it_even_with_a_painted_hole(window: FreeCADAdapter, tmp_path: Path) -> None:
    """``style_bodies`` is the engineer's whole-body colour; the picture must follow it (blue here)."""
    _feature, body, top = _plate(window, "style")
    window.hole(
        HoleRequest(
            operation_id="h0",
            face=top.handle,
            position=(0.0, 0.0),
            standard=HoleStandard.simple,
            diameter_mm=12.0,
            end_condition=EndCondition.through_all,
            fit="H7",
        )
    )
    window.style_bodies(StyleRequest(operation_id="st", bodies=(body,), color="#0000ff"))  # type: ignore[arg-type]
    shown = _shot(window, tmp_path, "blue")
    blue = px.count(shown, lambda r, g, b: b > 100 and r < b * 0.4 and g < b * 0.6)
    assert blue > 5 * SHOWN
