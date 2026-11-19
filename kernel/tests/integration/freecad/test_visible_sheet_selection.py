# ruff: noqa: E501
"""Selection in the sheet window, both ways, in the VISIBLE worker (FreeCAD GUI binary).

What runs for real: ``Gui.Selection`` of the objects of a TechDraw page (``addSelection`` with the sub-element names the
sheet window itself uses: ``Edge4`` of a view, a symbol, a dimension), ``get_selection`` turning them into picks of the
sheet, ``show_entities`` painting items, views and the lines of views on the window (counted in the window's own pixels),
and the 3D view coloured from a dimension of the sheet. What does not: a mouse click (the selection is made through the same
``Gui.Selection`` call a click ends in).

Needs a display (on Windows the GUI binary opens its own window); skipped without one, carries the ``visible`` marker (run
serially). ``COTAFORGE_TEST_SHOTS`` keeps the window images.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.contract import (
    AnnotateDrawingRequest,
    CaptureRequest,
    DrawingRequest,
    Empty,
    FaceHandle,
    ListHolesRequest,
    ShowEntitiesRequest,
)
from tests.integration.freecad.test_show_entities_visible import _is_orange, _rpc, _view_color
from tests.integration.freecad.test_visible_live_sheet import _build_plate, _side, _state, visible  # noqa: F401

pytestmark = [
    pytest.mark.freecad,
    pytest.mark.visible,
    pytest.mark.skipif(
        sys.platform != "win32" and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")),
        reason="the visible worker needs a display (DISPLAY / WAYLAND_DISPLAY)",
    ),
]

DIM = {
    "type": "dim",
    "id": "d3",
    "kind": "distance",
    "from": {"at": [15, 12, 0]},
    "to": {"at": [85, 12, 0]},
    "along": "x",
}
CALLOUT = {"type": "hole_dim", "id": "h1", "at": [[15, 12, 0], [85, 12, 0]]}
MIN_PIXELS = (
    150  # a highlighted line or box of the sheet, in pixels of the window more than before (anti-aliasing is not)
)
MIN_LINE_PIXELS = 8  # a recoloured dimension of TechDraw is a few thin lines: far fewer pixels than a boxed symbol
BASELINE_PIXELS = 40  # the sheet itself has a few pixels that are near the highlight colour (a blue dimension's edge)


class Scene:
    """The plate with its holes and a sheet with a distance dimension, a callout and a datum, in a visible worker."""

    def __init__(self, adapter: FreeCADAdapter, shots: Path) -> None:
        self.adapter = adapter
        self.shots = shots
        self.doc, self.base = _build_plate(adapter)
        adapter.create_drawing(DrawingRequest(operation_id="dr", template="A3", title="Placa 19", scale=1.0))
        # these tests are about the items they add: the default location of the holes would be one more of them
        adapter.annotate_drawing(AnnotateDrawingRequest(operation_id="noloc", action="remove", ids=("loc",)))
        datum_face = _side(adapter, self.base, (0.0, 0.0, -1.0))
        for number, item in enumerate((CALLOUT, DIM, {"type": "datum", "id": "dA", "letter": "A", "face": datum_face})):
            result = adapter.annotate_drawing(
                AnnotateDrawingRequest(operation_id=f"a{number}", action="add", items=(item,))
            )
            assert result.changed == (item["id"],) and result.warnings == (), result
        self.views = self.state("sheet_view_names")
        self.holes = adapter.list_holes(ListHolesRequest(operation_id="holes")).holes

    def state(self, what: str, **extra: Any) -> Any:
        return _state(self.adapter, self.doc, what, **extra)

    def select(self, *targets: tuple[str, str]) -> Any:
        self.state("sheet_select", targets=[list(t) for t in targets])
        return self.adapter.get_selection(Empty(operation_id="selection"))

    def native_objects(self) -> dict[str, str]:
        """The page object of each item that a click on the sheet lands on: TechDraw's own dimension or annotation when the
        item has one (a callout also keeps a symbol for its centre marks), else its symbol."""
        found: dict[str, str] = {}
        for name, kind, _, item, _, _ in self.state("sheet_objects"):
            if item not in found or kind != "DrawViewSymbol":
                found[item] = name
        return found

    def pixels(self) -> int:
        return int(self.state("sheet_pixels"))

    def lit(self, baseline: int) -> int:
        """The pixels of the window in the highlight colour that were not there before."""
        return self.pixels() - baseline

    def shot(self, name: str) -> None:
        self.state("sheet_png", path=str(self.shots / f"{name}.png"))

    def hole_at(self, x: float, y: float) -> Any:
        (hole,) = [
            h
            for h in self.holes
            if round(h.axis.origin[0]) == x and round(h.axis.origin[1]) == y and h.diameter_mm == 6.0
        ]
        return hole

    def top_circle_of(self, hole: Any) -> int:
        """The ``Edge<i>`` of the top view that the selection reports as ``hole``'s rim (found by reading, not assumed)."""
        for row in self.state("sheet_map", view="top"):
            if row["curve"] != "Circle" or not row["edges"]:
                continue
            picked = self.select((self.views["top"], f"Edge{row['index']}"))
            if picked.sheet and any(set(h.faces) == {f.id for f in hole.faces} for h in picked.sheet[0].holes):
                return int(row["index"])
        raise AssertionError("no circle of the top view is the rim of that hole")


@pytest.fixture
def scene(visible: FreeCADAdapter, tmp_path: Path) -> Scene:  # noqa: F811
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path)
    shots.mkdir(parents=True, exist_ok=True)
    return Scene(visible, shots)


# --- the engineer selects on the sheet ------------------------------------------------------------------------------------------


def test_a_distance_dimension_selected_on_the_sheet_says_its_item_value_and_holes(scene: Scene) -> None:
    result = scene.select(("Drawing_d3", ""))
    assert result.entries == () and len(result.sheet) == 1
    pick = result.sheet[0]
    assert (pick.kind, pick.item, pick.item_type, pick.sheet) == ("item", "d3", "dim", "Drawing")
    assert pick.text == "70"
    assert [h.diameter_mm for h in pick.holes] == [6.0, 6.0]
    assert [tuple(h.origin[:2]) for h in pick.holes] == [(15.0, 12.0), (85.0, 12.0)]
    assert set(pick.faces) == {f.id for x in (15.0, 85.0) for f in scene.hole_at(x, 12.0).faces}
    assert "distance along x" in pick.detail
    scene.shot("sel_dimension")


def test_several_objects_at_once_a_callout_an_overall_dimension_and_a_datum(scene: Scene) -> None:
    objects = scene.native_objects()
    result = scene.select((objects["h1"], ""), (objects["dA"], ""), (scene.views["front"], ""))
    by_kind = {p.kind: p for p in result.sheet}
    assert {p.item for p in result.sheet if p.kind == "item"} == {"h1", "dA"} and by_kind["view"].view == "front"
    callout = next(p for p in result.sheet if p.item == "h1")
    assert "pasante" in callout.text and len(callout.holes) == 2
    datum = next(p for p in result.sheet if p.item == "dA")
    assert datum.item_type == "datum" and len(datum.faces) == 1


def test_a_hole_circle_of_the_top_view_returns_the_hole_and_its_face(scene: Scene) -> None:
    hole = scene.hole_at(15.0, 12.0)
    index = scene.top_circle_of(hole)
    (pick,) = scene.select((scene.views["top"], f"Edge{index}")).sheet
    assert (pick.kind, pick.view) == ("edge", "top")
    (found,) = pick.holes
    assert found.diameter_mm == 6.0 and found.through is True
    assert tuple(found.origin[:2]) == (15.0, 12.0) and tuple(found.direction) in ((0.0, 0.0, 1.0), (0.0, 0.0, -1.0))
    wall = {f.id for f in hole.faces}
    assert set(found.faces) == wall and wall <= set(pick.faces)
    assert len(pick.edges) == 1 and "circle diameter 6" in pick.detail and "visible" in pick.detail
    scene.shot("sel_hole_circle")


def test_a_whole_view_and_a_point_of_a_view(scene: Scene) -> None:
    (view,) = scene.select((scene.views["right"], "")).sheet
    assert (view.kind, view.view, view.item) == ("view", "right", None) and "looks along" in view.detail
    corner = scene.select((scene.views["front"], "Vertex0")).sheet[0]
    assert corner.kind == "vertex" and corner.view == "front" and "model vertex:" in corner.detail
    assert corner.edges  # the model edges that meet at that corner


def test_a_model_face_and_a_sheet_object_selected_together_are_both_reported(scene: Scene) -> None:
    face = scene.hole_at(15.0, 12.0).faces[0].id
    _rpc(scene.adapter, "test_select_entity", {"entity": {"id": face}})
    only_model = scene.adapter.get_selection(Empty(operation_id="selection"))
    assert [e.kind for e in only_model.entries] == ["face"] and only_model.sheet == ()
    assert {r.item for r in only_model.entries[0].on_sheet} == {"h1", "d3"}


# --- the model selected in 3D finds its dimensions on the sheet ------------------------------------------------------------------


def test_a_face_selected_in_3d_lists_the_dimensions_that_refer_to_it(scene: Scene) -> None:
    face = scene.hole_at(85.0, 12.0).faces[0].id
    _rpc(scene.adapter, "test_select_entity", {"entity": {"id": face}})
    (entry,) = scene.adapter.get_selection(Empty(operation_id="selection")).entries
    refs = {r.item: r for r in entry.on_sheet}
    assert set(refs) == {"h1", "d3"} and refs["d3"].text == "70" and refs["d3"].item_type == "dim"
    assert refs["d3"].sheet == "Drawing" and "pasante" in refs["h1"].text
    other = scene.hole_at(15.0, 48.0).faces[0].id  # a dowel no item mentions
    _rpc(scene.adapter, "test_select_entity", {"entity": {"id": other}})
    assert scene.adapter.get_selection(Empty(operation_id="selection")).entries[0].on_sheet == ()


# --- the agent points at the sheet ------------------------------------------------------------------------------------------------


def test_highlighting_an_item_paints_it_in_the_window_and_clear_takes_it_off(scene: Scene) -> None:
    scene.state("open_sheet")
    baseline = scene.pixels()
    assert baseline <= BASELINE_PIXELS, baseline  # nothing orange before
    scene.shot("hl_before")
    reply = scene.adapter.show_entities(ShowEntitiesRequest(operation_id="show", items=("d3",)))
    assert reply.shown == "highlight" and "d3" in reply.detail and "Drawing" in reply.detail
    assert scene.state("sheet_paint") == [0, 1]  # a native dimension: TechDraw's own object is recoloured, no box
    assert scene.lit(baseline) >= MIN_LINE_PIXELS, scene.pixels()
    scene.shot("hl_item_d3")
    # the highlight takes nothing from the document: the sheet still agrees with its model, the selection is not touched
    assert scene.state("sheet_compare") == []
    assert scene.adapter.get_selection(Empty(operation_id="selection")).sheet == ()
    scene.adapter.show_entities(ShowEntitiesRequest(operation_id="clear", clear=True))
    assert scene.state("sheet_paint") == [0, 0] and scene.lit(baseline) == 0
    scene.shot("hl_cleared")


def test_highlighting_a_view_and_two_things_at_once(scene: Scene) -> None:
    baseline = scene.pixels()
    reply = scene.adapter.show_entities(ShowEntitiesRequest(operation_id="show", items=("view:top", "h1")))
    assert reply.shown == "highlight" and "view:top" in reply.detail and "h1" in reply.detail
    assert scene.state("sheet_paint")[0] >= 2  # the view box and the callout's centre lines (the views' own edges)
    assert scene.lit(baseline) >= MIN_PIXELS
    scene.shot("hl_view_top_and_h1")
    scene.adapter.show_entities(ShowEntitiesRequest(operation_id="clear", clear=True))
    assert scene.lit(baseline) == 0


def test_a_new_highlight_replaces_the_old_one_and_a_change_of_the_sheet_takes_it_off(scene: Scene) -> None:
    baseline = scene.pixels()
    scene.adapter.show_entities(ShowEntitiesRequest(operation_id="a", items=("d3",)))
    first = scene.lit(baseline)
    scene.adapter.show_entities(ShowEntitiesRequest(operation_id="b", items=("h1",)))
    painted = scene.state("sheet_paint")
    assert painted[0] >= 1 and painted[1] == 1  # the callout: its dimension recoloured, its centre marks traced
    assert scene.lit(baseline) >= MIN_LINE_PIXELS and first >= MIN_LINE_PIXELS
    scene.adapter.annotate_drawing(
        AnnotateDrawingRequest(
            operation_id="n", action="add", items=({"type": "note", "id": "n1", "text": "Sin rebabas"},)
        )
    )
    assert (
        scene.state("sheet_paint") == [0, 0] and scene.pixels() <= baseline + BASELINE_PIXELS
    )  # the sheet changed: what was pointed at may have moved


def test_the_overall_dimensions_are_coloured_through_their_own_view_object_and_put_back(scene: Scene) -> None:
    """TechDraw's own dimensions have a colour of their own: they are recoloured, not covered, and clear restores it."""
    names = [row[0] for row in scene.state("sheet_dimensions")]
    assert len(names) == 3
    before = [scene.state("property", object=name, property="Color") for name in names]
    baseline = scene.pixels()
    reply = scene.adapter.show_entities(ShowEntitiesRequest(operation_id="show", items=("o1",)))
    assert reply.shown == "highlight" and "o1" in reply.detail
    assert scene.state("sheet_paint") == [0, 3]
    for name in names:
        red, green, blue = scene.state("property", object=name, property="Color")[:3]
        assert (round(red, 2), round(green, 2), round(blue, 2)) == (1.0, 0.55, 0.0), name
    assert scene.lit(baseline) >= MIN_PIXELS // 3
    scene.shot("hl_overall_dimensions")
    scene.adapter.show_entities(ShowEntitiesRequest(operation_id="clear", clear=True))
    assert scene.state("sheet_paint") == [0, 0]
    assert [scene.state("property", object=name, property="Color") for name in names] == before


def test_an_unknown_item_or_view_is_refused_with_what_there_is(scene: Scene) -> None:
    from cotaforge.contract import AdapterError  # noqa: PLC0415

    for bad in ("zz", "view:nowhere"):
        with pytest.raises(AdapterError) as caught:
            scene.adapter.show_entities(ShowEntitiesRequest(operation_id="show", items=(bad,)))
        assert bad in str(caught.value) and "items:" in str(caught.value)


def test_a_face_highlighted_on_the_sheet_paints_its_items_and_the_lines_that_draw_it(scene: Scene) -> None:
    face = scene.hole_at(15.0, 12.0).faces[0].id
    baseline = scene.pixels()
    reply = scene.adapter.show_entities(
        ShowEntitiesRequest(operation_id="show", entities=(FaceHandle(id=face),), where="sheet")
    )
    assert reply.shown == "highlight" and "h1" in reply.detail and "d3" in reply.detail and "top view" in reply.detail
    boxes, recoloured = scene.state("sheet_paint")
    assert (
        boxes >= 3 and recoloured == 2
    )  # the two native dimensions are recoloured; boxes: marks and the lines of views
    assert scene.lit(baseline) >= MIN_PIXELS
    scene.shot("hl_face_on_sheet")
    assert not any(
        _is_orange(c) for c in _view_color(scene.adapter, scene.doc.name, scene.state("body_object"))
    )  # "sheet": not in 3D
    scene.adapter.show_entities(ShowEntitiesRequest(operation_id="clear", clear=True))


def test_a_dimension_of_the_sheet_highlights_its_faces_in_the_3d_view(scene: Scene) -> None:
    reply = scene.adapter.show_entities(ShowEntitiesRequest(operation_id="show", items=("d3",), where="3d"))
    assert reply.shown == "highlight" and "3D:" in reply.detail
    for x in (15.0, 85.0):
        assert scene.hole_at(x, 12.0).faces[0].id in reply.detail
    assert sum(scene.state("sheet_paint")) == 1  # the dimension on the sheet too
    assert scene.state("active_window") == "Gui::View3DInventor"  # the 3D view is brought to the front
    colours = _view_color(scene.adapter, scene.doc.name, scene.state("body_object"))
    assert sum(1 for c in colours if _is_orange(c)) == 2, colours  # the two walls, in the part's own colour list
    path = scene.shots / "hl_item_in_3d.png"
    scene.adapter.capture(CaptureRequest(operation_id="cap", path=str(path), width=1000, height=700))
    assert path.stat().st_size > 1000
    scene.adapter.show_entities(ShowEntitiesRequest(operation_id="clear", clear=True))
    assert not any(_is_orange(c) for c in _view_color(scene.adapter, scene.doc.name, scene.state("body_object")))


def test_a_native_hole_dim_and_dim_are_read_back_by_item_with_their_holes_and_shown_by_id(scene: Scene) -> None:
    """The objects TechDraw owns -- a diameter dimension that carries the callout, a distance dimension FreeCAD measures --
    are items for the selection: what the engineer clicks says the item id, what it prints and the holes and faces it is
    about, and ``show`` of that id recolours TechDraw's own object (and puts it back)."""
    objects = scene.native_objects()
    kinds = {row[0]: row[1] for row in scene.state("sheet_objects")}
    assert kinds[objects["h1"]] == "DrawViewDimension" and kinds[objects["d3"]] == "DrawViewDimension"
    result = scene.select((objects["h1"], ""), (objects["d3"], ""))
    by_item = {p.item: p for p in result.sheet}
    assert set(by_item) == {"h1", "d3"}
    callout, distance = by_item["h1"], by_item["d3"]
    assert (callout.item_type, distance.item_type) == ("hole_dim", "dim")
    assert "pasante" in callout.text and distance.text == "70"
    assert [h.diameter_mm for h in callout.holes] == [6.0, 6.0]
    wall = {f.id for x in (15.0, 85.0) for f in scene.hole_at(x, 12.0).faces}
    assert set(callout.faces) == wall and set(distance.faces) == wall
    name = objects["d3"]
    before = scene.state("property", object=name, property="Color")
    baseline = scene.pixels()
    scene.adapter.show_entities(ShowEntitiesRequest(operation_id="show", items=("d3",)))
    red, green, blue = scene.state("property", object=name, property="Color")[:3]
    assert (round(red, 2), round(green, 2), round(blue, 2)) == (1.0, 0.55, 0.0)
    assert scene.lit(baseline) >= MIN_LINE_PIXELS
    scene.shot("hl_native_dim")
    scene.adapter.show_entities(ShowEntitiesRequest(operation_id="clear", clear=True))
    assert scene.state("property", object=name, property="Color") == before
