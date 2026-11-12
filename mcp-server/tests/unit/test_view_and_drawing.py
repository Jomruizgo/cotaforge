"""The 3D display tools (orient / fit, style, section) and the drawing sheet, over fakes with a window and a sheet."""

from pathlib import Path
from typing import Any

import pytest

from cotaforge_mcp.config import Settings
from cotaforge_mcp.guide import guide
from cotaforge_mcp.schemas import TOOLS_BY_NAME, input_schema, visible_tools
from tests.conftest import PNG, BareFake, Caller, DrawingFake, MakeSession, VisibleFake, plate, set_review


@pytest.fixture
def window(make: MakeSession) -> tuple[Caller, VisibleFake]:
    adapter = VisibleFake()
    call = Caller(make(adapter))
    plate(call)
    return call, adapter


@pytest.fixture
def sheet(make: MakeSession, settings: Settings) -> tuple[Caller, DrawingFake]:
    set_review(settings, "ninguno")  # these tests are about the tools, not about who reviews the drawing
    adapter = DrawingFake()
    call = Caller(make(adapter))
    plate(call)
    return call, adapter


# --- set_view: orientation and fit ------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["iso", "front", "top", "right", "rear", "left", "bottom", "dimetric", "trimetric"])
def test_every_standard_orientation_is_accepted(window: tuple[Caller, VisibleFake], name: str) -> None:
    call, adapter = window
    assert f"view: {name}" in call.ok("view", action="set_view", name=name)
    assert adapter.views[-1].name == name and adapter.views[-1].bodies == ()


def test_an_unknown_orientation_is_refused_with_the_valid_ones(window: tuple[Caller, VisibleFake]) -> None:
    call, adapter = window
    text = call.err("view", action="set_view", name="oblique")
    assert "name" in text and "trimetric" in text and adapter.views == []


def test_set_view_without_a_name_only_fits_and_with_ids_fits_those_bodies(window: tuple[Caller, VisibleFake]) -> None:
    call, adapter = window
    assert "orientation kept; fit: whole model" in call.ok("view", action="set_view")
    assert adapter.views[-1].name is None and adapter.views[-1].bodies == ()
    text = call.ok("view", action="set_view", ids=["s2"], name="front")  # a step that made a body names the body
    assert "fit: s2" in text
    assert [b.id for b in adapter.views[-1].bodies] == ["body:1"] and adapter.views[-1].name == "front"


# --- style ------------------------------------------------------------------------------------------------------------


def test_style_sets_transparency_colour_and_visibility(window: tuple[Caller, VisibleFake]) -> None:
    call, adapter = window
    text = call.ok("view", action="style", ids=["s2"], transparency=60, color="Blue", visible=False)
    assert "transparency 60%" in text and "colour #1976d2" in text and "hidden" in text and "display only" in text
    request = adapter.styles[-1]
    assert (request.transparency, request.color, request.visible) == (60, "#1976d2", False)
    assert [b.id for b in request.bodies] == ["body:1"]
    assert "shown" in call.ok("view", action="style", ids=["body:1"], visible=True)  # a body id works as well


def test_style_needs_bodies_and_something_to_change(window: tuple[Caller, VisibleFake]) -> None:
    call, adapter = window
    assert "needs ids" in call.err("view", action="style", transparency=50)
    assert "transparency, color and/or visible" in call.err("view", action="style", ids=["s2"])
    assert adapter.styles == []


@pytest.mark.parametrize(("field", "value"), [("transparency", 101), ("transparency", -5), ("color", "chartreuse")])
def test_style_refuses_a_bad_value_before_the_cad_hears_of_it(
    window: tuple[Caller, VisibleFake], field: str, value: object
) -> None:
    call, adapter = window
    text = call.err("view", action="style", ids=["s2"], **{field: value})
    assert field in text
    assert adapter.styles == []


def test_style_of_something_that_is_not_a_body_is_refused_clearly(window: tuple[Caller, VisibleFake]) -> None:
    call, _ = window
    assert "does not name a body" in call.err("view", action="style", ids=["s1"], transparency=10)  # a sketch


# --- section ----------------------------------------------------------------------------------------------------------


def test_a_section_plane_is_set_and_removed(window: tuple[Caller, VisibleFake]) -> None:
    call, adapter = window
    text = call.ok("view", action="section", axis="z", offset_mm=4.5)
    assert "plane z = 4.5 mm" in text and "+z side" in text and "display only" in text
    assert (adapter.sections[-1].axis, adapter.sections[-1].offset_mm) == ("z", 4.5)
    assert "removed" in call.ok("view", action="section")
    assert adapter.sections[-1].axis is None
    assert "axis" in call.err("view", action="section", axis="w")


# --- headless: the window-only actions say so -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "arguments",
    [
        {"action": "style", "ids": ["s2"], "transparency": 10},
        {"action": "section", "axis": "x"},
        {"action": "set_view", "name": "rear"},
        {"action": "capture"},
    ],
)
def test_without_a_window_the_display_actions_are_not_available(make: MakeSession, arguments: dict) -> None:
    call = Caller(make(DrawingFake()))  # a CAD with sheets but no window
    plate(call)
    text = call.err("view", **arguments)
    assert "not available with this CAD" in text and "show" in text  # what is left: show
    assert call.last is not None and "not_supported" in call.last.text


def test_the_view_tool_lists_only_the_actions_the_cad_can_do(make: MakeSession) -> None:
    visible = make(VisibleFake())
    assert input_schema(TOOLS_BY_NAME["view"], visible)["properties"]["action"]["enum"][-2:] == ["style", "section"]
    bare = make(BareFake())
    assert "view" not in visible_tools(bare)


# --- drawing ----------------------------------------------------------------------------------------------------------


def test_the_drawing_tool_only_exists_when_the_cad_can_make_sheets(make: MakeSession, session: object) -> None:
    assert "drawing" not in visible_tools(session)  # type: ignore[arg-type]
    with_sheets = make(DrawingFake())
    assert "drawing" in visible_tools(with_sheets)
    assert input_schema(TOOLS_BY_NAME["drawing"], with_sheets)["properties"]["action"]["enum"] == [
        "create",
        "add",
        "remove",
        "remove_sheet",
        "update",
        "add_view",
        "remove_view",
        "answer",
        "approve",
        "request_review",
        "review_choice",
        "review_report",
        "list",
        "read",
        "review",
        "requirements",
        "capture",
    ]
    export = input_schema(TOOLS_BY_NAME["export"], with_sheets)["properties"]["action"]["enum"]
    assert export[-2:] == ["pdf", "dxf"]
    assert "pdf" not in input_schema(TOOLS_BY_NAME["export"], make(BareFake()))["properties"]["action"]["enum"]


def test_create_passes_the_title_block_fields_and_reports_the_sheet(sheet: tuple[Caller, DrawingFake]) -> None:
    call, adapter = sheet
    text = call.ok(
        "drawing", action="create", template="A3", title="Soporte", author="Ana", date="2026-10-24", material="Acero"
    )
    request = adapter.created[-1]
    assert (request.template, request.title, request.author, request.date, request.material) == (
        "A3",
        "Soporte",
        "Ana",
        "2026-10-24",
        "Acero",
    )
    assert request.scale is None
    assert "A3 sheet at 1:2" in text and "front, top, right, isometric" in text
    assert "width 60 mm, height 20 mm, depth 40 mm" in text and "finished computing (0.25 s)" in text
    assert "watches the sheet window" in text and "pdf" in text


def test_the_default_projection_is_freecads_own_and_says_nothing_about_the_window(
    sheet: tuple[Caller, DrawingFake],
) -> None:
    call, adapter = sheet
    text = call.ok("drawing", action="create")
    assert adapter.created[-1].projection == "techdraw"
    assert "projection" not in text and "NO views" not in text
    assert "projection" not in call.ok("drawing", action="list")


def test_the_hlr_projection_is_passed_on_and_the_replies_say_its_views_are_symbols(
    sheet: tuple[Caller, DrawingFake],
) -> None:
    call, adapter = sheet
    created = call.ok("drawing", action="create", projection="hlr")
    assert adapter.created[-1].projection == "hlr"
    assert "projection: hlr" in created and "symbols" in created and "NO views" not in created
    added = call.ok("drawing", action="add", items=[{"type": "hole_dim", "all": True}])
    assert "window: 1 new" in added and "views:" not in added  # an add answers briefly
    listed = call.ok("drawing", action="list")
    assert "projection hlr (views drawn by our own projection)" in listed
    assert "projection hlr" in call.ok("drawing", action="review")
    again = call.ok("drawing", action="create", projection="techdraw")  # switching back is a new create
    assert "projection: hlr" not in again


def test_an_unknown_projection_is_refused_by_the_schema(sheet: tuple[Caller, DrawingFake]) -> None:
    call, adapter = sheet
    call.err("drawing", action="create", projection="fast")
    assert adapter.created == []


def test_the_title_defaults_to_the_document_and_the_sheet_marks_the_document_changed(
    sheet: tuple[Caller, DrawingFake],
) -> None:
    call, adapter = sheet
    call.session.dirty = False
    call.ok("drawing", action="create")
    assert adapter.created[-1].title == "plate" and adapter.created[-1].template == "A4"
    assert call.session.dirty is True  # the sheet is in the document: saving keeps it


def test_the_document_material_is_the_default_one_in_the_title_block(sheet: tuple[Caller, DrawingFake]) -> None:
    call, adapter = sheet
    call.ok("doc", action="set_material", material="Acero 1045")
    call.ok("drawing", action="create")
    assert adapter.created[-1].material == "Acero 1045"


@pytest.mark.parametrize(("text", "ratio"), [("1:2", 0.5), ("2:1", 2.0), ("0.5", 0.5), (" 1 : 5 ", 0.2), ("1", 1.0)])
def test_the_scale_is_a_ratio_written_the_way_a_drawing_writes_it(
    sheet: tuple[Caller, DrawingFake], text: str, ratio: float
) -> None:
    call, adapter = sheet
    call.ok("drawing", action="create", scale=text)
    assert adapter.created[-1].scale == pytest.approx(ratio)


@pytest.mark.parametrize("text", ["half", "0", "-1:2", "1:0", "1:2:3", "inf"])
def test_a_bad_scale_is_refused_with_the_forms_that_work(sheet: tuple[Caller, DrawingFake], text: str) -> None:
    call, adapter = sheet
    out = call.err("drawing", action="create", scale=text)
    assert "not a scale" in out and "1:2" in out and adapter.created == []


def test_warnings_of_the_cad_reach_the_agent(sheet: tuple[Caller, DrawingFake]) -> None:
    call, _ = sheet
    assert "warning: a note" in call.ok("drawing", action="create", scale="7")


def test_a_sheet_that_never_finished_computing_is_an_error_naming_what_to_do(sheet: tuple[Caller, DrawingFake]) -> None:
    call, adapter = sheet
    adapter.fail_with = "the CAD did not finish computing the view(s) Isometric within 90 s: nothing was done on them"
    text = call.err("drawing", action="create")
    assert "Isometric" in text and "nothing was done" in text and call.last is not None


def test_the_drawing_needs_a_document(make: MakeSession) -> None:
    call = Caller(make(DrawingFake()))
    assert "no document is open" in call.err("drawing", action="create")


def test_capture_returns_the_sheet_as_an_image(sheet: tuple[Caller, DrawingFake]) -> None:
    call, _ = sheet
    reply = call("drawing", action="capture")
    assert not reply.is_error and len(reply.images) == 1 and reply.images[0].data == PNG
    assert "sheet image" in reply.text


def test_pdf_and_dxf_are_exports_of_the_sheet(sheet: tuple[Caller, DrawingFake], settings: Settings) -> None:
    call, adapter = sheet
    call.ok("drawing", action="create")
    pdf = call.ok("export", action="pdf")
    assert "exported pdf" in pdf and (settings.workspace / "plate.pdf").read_bytes().startswith(b"%PDF")
    call.ok("export", action="dxf", path="sheets/plate.dxf")
    assert (settings.workspace / "sheets" / "plate.dxf").is_file()
    assert [(r.format, Path(r.path).name) for r in adapter.exported] == [("pdf", "plate.pdf"), ("dxf", "plate.dxf")]


def test_exporting_a_sheet_never_replaces_a_file_unasked(sheet: tuple[Caller, DrawingFake], settings: Settings) -> None:
    call, adapter = sheet
    call.ok("drawing", action="create")
    (settings.workspace / "plate.pdf").write_bytes(b"mine")
    assert "already exists" in call.err("export", action="pdf")
    assert (settings.workspace / "plate.pdf").read_bytes() == b"mine" and adapter.exported == []
    call.ok("export", action="pdf", overwrite=True)
    assert (settings.workspace / "plate.pdf").read_bytes().startswith(b"%PDF")


# --- the guide --------------------------------------------------------------------------------------------------------


def test_the_drawing_topic_explains_the_flow_and_its_limits(make: MakeSession) -> None:
    text = guide(make(DrawingFake()), "drawing")
    for needle in ("create", "capture", "export(action=pdf|dxf)", "finished computing", "without", "scale"):
        assert needle in text
    assert "not available" in guide(make(BareFake()), "drawing")


def test_the_old_guidance_about_waiting_for_techdraw_names_the_tool() -> None:
    from cotaforge.adapters.freecad import FreeCADAdapter  # noqa: PLC0415

    traps = " ".join(FreeCADAdapter().known_traps())
    assert "drawing(action=create)" in traps and "blank" in traps


# --- the CAD's own window ---------------------------------------------------------------------------------------------


class WindowFake(VisibleFake):
    """A visible CAD that also answers window commands (what was asked is kept in ``windows``)."""

    def __init__(self) -> None:
        super().__init__()
        self.windows: list[Any] = []

    def window(self, request: Any) -> Any:
        from cotaforge.contract import WindowResult

        self.windows.append(request)
        if request.command == "capture":
            Path(request.path).write_bytes(PNG)
            return WindowResult(text="window capture", image_path=request.path)
        return WindowResult(text=f"window {request.command}")


def test_window_commands_reach_the_adapter_and_never_the_plan(make: MakeSession) -> None:
    adapter = WindowFake()
    call = Caller(make(adapter))
    plate(call)
    steps = len(call.session.executor.plan.operations)
    assert call.ok("view", action="window") == "window info"  # info is the default
    assert call.ok("view", action="window", command="place", monitor=2, area=[0, 0, 0.5, 1]) == "window place"
    request = adapter.windows[-1]
    assert (request.command, request.monitor, request.area) == ("place", 2, (0.0, 0.0, 0.5, 1.0))
    reply = call("view", action="window", command="capture")
    assert not reply.is_error and len(reply.images) == 1
    assert len(call.session.executor.plan.operations) == steps  # not a step: no plan entry, nothing to undo


def test_window_area_is_checked_before_the_cad_hears_of_it(make: MakeSession) -> None:
    adapter = WindowFake()
    call = Caller(make(adapter))
    plate(call)
    assert "fractions" in call.err("view", action="window", command="place", area=[0.5, 0, 0.2, 1])
    assert "fractions" in call.err("view", action="window", command="place", area=[0, 0, 2, 1])
    assert adapter.windows == []


def test_the_window_action_exists_only_when_the_cad_has_a_window_command(make: MakeSession) -> None:
    with_window = input_schema(TOOLS_BY_NAME["view"], make(WindowFake()))["properties"]["action"]["enum"]
    without = input_schema(TOOLS_BY_NAME["view"], make(VisibleFake()))["properties"]["action"]["enum"]
    assert "window" in with_window and "window" not in without
