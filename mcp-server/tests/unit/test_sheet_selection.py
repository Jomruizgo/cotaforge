"""Selection on drawing sheets, both ways, through ``view``: what the engineer selected in the sheet window is read as a
pick (item, view, line of a view) with the model ids it corresponds to; a face selected in 3D says which items refer to
it; and ``view(action=show)`` points at items, views, faces and edges, on the sheet and in 3D."""

import pytest
from cotaforge.adapters.fake import FakeAdapter
from cotaforge.contract import (
    EdgeHandle,
    FaceHandle,
    SheetHole,
    SheetPick,
    SheetRef,
    ShowEntitiesRequest,
)

from cotaforge_mcp.guide import server_instructions
from tests.conftest import Caller, DrawingFake, MakeSession, fake_without, plate

HOLE = SheetHole(diameter_mm=6.0, through=True, origin=(15.0, 12.0, 0.0), direction=(0.0, 0.0, 1.0), faces=("face:7",))
DIMENSION = SheetPick(
    sheet="Drawing",
    kind="item",
    label="d3 dim",
    item="d3",
    item_type="dim",
    text="70",
    detail="distance along x from hole D6 (face:7) to hole D6 (face:8)",
    faces=("face:7", "face:8"),
    holes=(HOLE, HOLE.model_copy(update={"origin": (85.0, 12.0, 0.0), "faces": ("face:8",)})),
)
CIRCLE = SheetPick(
    sheet="Drawing",
    kind="edge",
    label="top view Edge4",
    view="top",
    detail="circle diameter 6 mm, visible",
    faces=("face:6", "face:7"),
    edges=("edge:14",),
    holes=(HOLE,),
)
VIEW = SheetPick(
    sheet="Drawing", kind="view", label="right view", view="right", detail="looks along [1, 0, 0]; scale 1"
)


@pytest.fixture
def sheet(make: MakeSession) -> tuple[Caller, FakeAdapter]:
    adapter = FakeAdapter()
    call = Caller(make(adapter))
    plate(call)
    return call, adapter


def test_a_dimension_selected_on_the_sheet_reads_as_its_item_its_text_and_its_holes(
    sheet: tuple[Caller, FakeAdapter],
) -> None:
    call, adapter = sheet
    adapter.sheet_selection = (DIMENSION,)
    text = call.ok("view", action="selection")
    assert text.startswith("selection: 0 usable") and "on the sheet: 1" in text
    line = next(row for row in text.splitlines() if "item d3 dim" in row)
    assert 'prints "70"' in line and "distance along x" in line and "faces face:7, face:8" in line
    assert line.count("hole D6 through axis from") == 2 and "[15, 12, 0]" in line and "[85, 12, 0]" in line
    assert "(wall face:7)" in line and "(wall face:8)" in line


def test_a_hole_circle_a_view_and_several_picks_at_once(sheet: tuple[Caller, FakeAdapter]) -> None:
    call, adapter = sheet
    adapter.sheet_selection = (CIRCLE, VIEW, DIMENSION)
    text = call.ok("view", action="selection")
    assert "on the sheet: 3" in text
    circle = next(row for row in text.splitlines() if "line of the top view (Edge4)" in row)
    assert "model edge:14" in circle and "faces face:6, face:7" in circle and "hole D6 through" in circle
    assert any("right view" in row and "looks along" in row for row in text.splitlines())
    assert "item d3 dim" in text


def test_a_face_selected_in_3d_lists_the_items_that_refer_to_it(sheet: tuple[Caller, FakeAdapter]) -> None:
    call, adapter = sheet
    adapter.set_selection([FaceHandle(id="face:6")])
    adapter.on_sheet = {
        "face:6": (
            SheetRef(sheet="Drawing", item="h1", item_type="hole_dim", text="2x Ø6 pasante"),
            SheetRef(sheet="Segunda", item="d3", item_type="dim", text="70"),
        )
    }
    text = call.ok("view", action="selection")
    assert "on the sheet: h1 hole_dim 2x Ø6 pasante; d3 dim 70 (Segunda)" in text
    adapter.on_sheet = {}
    assert "on the sheet" not in call.ok("view", action="selection")


def test_show_takes_items_and_views_besides_faces_and_edges(sheet: tuple[Caller, FakeAdapter]) -> None:
    call, adapter = sheet
    text = call.ok("view", action="show", ids=["face:6", "edge:3", "d3", "view:top"], on="both", sheet="Drawing")
    assert text.startswith("shown: highlight") and "sheet: d3, view:top" in text
    (request,) = adapter.show_entities_calls
    assert isinstance(request, ShowEntitiesRequest)
    assert request.entities == (FaceHandle(id="face:6"), EdgeHandle(id="edge:3"))
    assert request.items == ("d3", "view:top") and request.where == "both" and request.sheet == "Drawing"
    assert adapter.highlighted_items == ("d3", "view:top")


def test_show_of_an_item_alone_leaves_where_to_the_cad(sheet: tuple[Caller, FakeAdapter]) -> None:
    call, adapter = sheet
    call.ok("view", action="show", ids=["item:h1"])
    assert adapter.show_entities_calls[-1].items == ("item:h1",) and adapter.show_entities_calls[-1].where == "auto"
    call.ok("view", action="show", clear=True)
    assert adapter.show_entities_calls[-1].clear and adapter.highlighted_items == ()


def test_a_step_shown_on_the_sheet_stands_for_the_faces_it_made(sheet: tuple[Caller, FakeAdapter]) -> None:
    call, adapter = sheet
    text = call.ok("view", action="show", ids=["s2"], on="sheet")
    assert text.startswith("shown: highlight")
    (request,) = adapter.show_entities_calls
    made = [entity.id for entity in request.entities]
    assert made and all(face.startswith("face:") for face in made) and request.where == "sheet" and request.items == ()
    assert "names a step" in call.err("view", action="show", ids=["s2"])  # without on=sheet it is still not a face


def test_a_step_or_a_body_id_is_still_not_something_to_show(sheet: tuple[Caller, FakeAdapter]) -> None:
    call, _ = sheet
    assert "names a step" in call.err("view", action="show", ids=["s2"])
    assert "not a face or edge id" in call.err("view", action="show", ids=["body:1"])
    assert "show needs ids" in call.err("view", action="show")


def test_the_guide_asks_to_point_only_where_there_is_a_window_to_point_in(make: MakeSession) -> None:
    visible = Caller(make(DrawingFake()))
    assert "ask the engineer to select it in the window" in server_instructions(visible.session)
    assert 'cad_guide(topic="pointing")' in server_instructions(visible.session)
    guide = visible.ok("cad_guide", topic="pointing")
    assert "ASK HIM TO POINT" in guide and "view:top" in guide and "on the sheet: d3 dim 70" in guide
    plain = Caller(make(FakeAdapter()))  # a CAD with no drawing sheets: pointing in 3D only
    text = plain.ok("cad_guide", topic="pointing")
    assert "ASK HIM TO POINT" in text and "view:top" not in text
    headless = Caller(make(fake_without("get_selection")))
    assert "ask the engineer to select it" not in server_instructions(headless.session)
    assert "needs the visible CAD window" in headless.ok("cad_guide", topic="pointing")
