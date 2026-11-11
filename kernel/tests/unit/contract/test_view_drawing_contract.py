"""Contract surface of the view display tools (style, section, fit) and of the drawing sheet tools."""

import pytest
from pydantic import ValidationError

from cotaforge.contract import (
    Ack,
    AdapterError,
    AnnotateDrawingRequest,
    BodyHandle,
    CaptureDrawingRequest,
    CaptureDrawingResult,
    DescribeDrawingRequest,
    DescribeDrawingResult,
    DrawingRequest,
    DrawingResult,
    ErrorCode,
    ExportDrawingRequest,
    ExportDrawingResult,
    SectionRequest,
    StyleRequest,
    ViewRequest,
    WindowRequest,
    WindowResult,
    parse_color,
)
from cotaforge.contract.toolschema import TOOL_REGISTRY, Group
from tests.unit.contract.test_adapter import _MinimalAdapter

BODY = BodyHandle(id="body:1")


# --- the tools in the registry ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "group", "request_model", "response_model"),
    [
        ("set_view", Group.VIEW, ViewRequest, Ack),
        ("style_bodies", Group.VIEW, StyleRequest, Ack),
        ("section_plane", Group.VIEW, SectionRequest, Ack),
        ("window", Group.VIEW, WindowRequest, WindowResult),
        ("create_drawing", Group.DRAWING, DrawingRequest, DrawingResult),
        ("capture_drawing", Group.DRAWING, CaptureDrawingRequest, CaptureDrawingResult),
        ("export_drawing", Group.DRAWING, ExportDrawingRequest, ExportDrawingResult),
        ("annotate_drawing", Group.DRAWING, AnnotateDrawingRequest, DrawingResult),
        ("describe_drawing", Group.DRAWING, DescribeDrawingRequest, DescribeDrawingResult),
    ],
)
def test_the_tools_are_registered_and_optional(
    name: str, group: Group, request_model: type, response_model: type
) -> None:
    spec = TOOL_REGISTRY[name]
    assert (spec.group, spec.optional, spec.request_model, spec.response_model) == (
        group,
        True,
        request_model,
        response_model,
    )


@pytest.mark.parametrize(
    "name",
    [
        "style_bodies",
        "section_plane",
        "window",
        "create_drawing",
        "capture_drawing",
        "export_drawing",
        "annotate_drawing",
        "describe_drawing",
    ],
)
def test_an_adapter_that_does_not_override_them_says_not_supported_and_is_not_capable(name: str) -> None:
    adapter = _MinimalAdapter()
    requests = {
        "style_bodies": StyleRequest(operation_id="s", bodies=(BODY,), visible=False),
        "section_plane": SectionRequest(operation_id="s", axis="z", offset_mm=3),
        "window": WindowRequest(operation_id="s", command="info"),
        "create_drawing": DrawingRequest(operation_id="s"),
        "capture_drawing": CaptureDrawingRequest(operation_id="s", path="x.png"),
        "export_drawing": ExportDrawingRequest(operation_id="s", path="x.pdf", format="pdf"),
        "annotate_drawing": AnnotateDrawingRequest(operation_id="s", action="update"),
        "describe_drawing": DescribeDrawingRequest(operation_id="s"),
    }
    with pytest.raises(AdapterError) as info:
        getattr(adapter, name)(requests[name])
    assert info.value.info.code is ErrorCode.not_supported and info.value.info.recoverable is False
    assert name not in adapter.capabilities().tools


# --- the requests -----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["iso", "front", "top", "right", "rear", "left", "bottom", "dimetric", "trimetric"])
def test_set_view_takes_every_standard_orientation(name: str) -> None:
    assert ViewRequest(operation_id="v", name=name).name == name


def test_set_view_needs_neither_a_name_nor_bodies_to_just_fit_everything() -> None:
    request = ViewRequest(operation_id="v")
    assert request.name is None and request.bodies == ()
    assert ViewRequest(operation_id="v", name="front", bodies=(BODY,)).bodies == (BODY,)
    with pytest.raises(ValidationError):
        ViewRequest(operation_id="v", name="oblique")


def test_style_needs_a_body_and_at_least_one_change() -> None:
    with pytest.raises(ValidationError):
        StyleRequest(operation_id="s", bodies=(), transparency=10)
    with pytest.raises(ValidationError):
        StyleRequest(operation_id="s", bodies=(BODY,))
    assert StyleRequest(operation_id="s", bodies=(BODY,), visible=False).visible is False


@pytest.mark.parametrize("value", [-1, 101])
def test_transparency_is_a_percentage(value: int) -> None:
    with pytest.raises(ValidationError):
        StyleRequest(operation_id="s", bodies=(BODY,), transparency=value)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("#FF8800", "#ff8800"), ("ff8800", "#ff8800"), ("Blue", "#1976d2"), (" grey ", "#9e9e9e")],
)
def test_colours_are_normalised_to_lowercase_hex(text: str, expected: str) -> None:
    assert parse_color(text) == expected
    assert StyleRequest(operation_id="s", bodies=(BODY,), color=text).color == expected


@pytest.mark.parametrize("text", ["", "#12345", "#gg0000", "chartreuse"])
def test_an_unknown_colour_is_refused_with_what_is_accepted(text: str) -> None:
    with pytest.raises(ValidationError, match="#rrggbb"):
        StyleRequest(operation_id="s", bodies=(BODY,), color=text)


def test_a_section_without_an_axis_removes_the_plane() -> None:
    assert SectionRequest(operation_id="s").axis is None
    assert SectionRequest(operation_id="s", axis="x", offset_mm=-4.5).offset_mm == -4.5
    with pytest.raises(ValidationError):
        SectionRequest(operation_id="s", axis="w")


def test_a_drawing_request_defaults_and_limits() -> None:
    request = DrawingRequest(operation_id="d")
    assert request.template == "A4" and request.scale is None
    assert (request.language, request.general_tolerances, request.sheet, request.body) == (
        "es",
        "ISO 2768-mK",
        None,
        None,
    )
    assert DrawingRequest(operation_id="d", language="en", sheet="Planta", body=BODY).body == BODY
    for bad in ({"language": "fr"}, {"sheet": "2 bad"}, {"sheet": "x" * 30}):
        with pytest.raises(ValidationError):
            DrawingRequest(operation_id="d", **bad)
    assert DrawingRequest(operation_id="d", template="A3", scale=0.5, title="T").template == "A3"
    for bad in ({"template": "A5"}, {"scale": 0}, {"scale": -1}):
        with pytest.raises(ValidationError):
            DrawingRequest(operation_id="d", **bad)


def test_the_drawing_exports_are_pdf_or_dxf_and_the_capture_has_sane_bounds() -> None:
    with pytest.raises(ValidationError):
        ExportDrawingRequest(operation_id="e", path="a.svg", format="svg")
    with pytest.raises(ValidationError):
        CaptureDrawingRequest(operation_id="c", path="a.png", width=10)


def test_a_capture_may_ask_for_a_region_and_a_result_says_what_the_file_holds() -> None:
    assert CaptureDrawingRequest(operation_id="c", path="a.png").region is None
    zoom = CaptureDrawingRequest(operation_id="c", path="a.png", region=(10, 20, 60, 70))
    assert zoom.region == (10.0, 20.0, 60.0, 70.0)
    with pytest.raises(ValidationError):
        CaptureDrawingRequest(operation_id="c", path="a.png", region=(1, 2, 3))
    shown = CaptureDrawingResult.model_validate(
        {
            "renderer": "techdraw",
            "page_mm": [420, 297],
            "views": [
                {"name": "front", "rect": [10, 10, 50, 40]},
                {"name": "S", "rect": [1, 2, 3, 4], "kind": "native"},
            ],
            "native": ["SecNative [DrawViewSection]"],
            "size_px": [1400, 990],
        }
    )
    assert [v.kind for v in shown.views] == ["standard", "native"] and shown.unrendered == () and shown.region is None
    both = ExportDrawingResult()
    assert both.renderer == "compositor" and both.views == () and both.native == () and both.unrendered == ()
    with pytest.raises(ValidationError):
        CaptureDrawingResult.model_validate({"renderer": "word"})
