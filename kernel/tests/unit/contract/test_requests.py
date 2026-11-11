"""Tests for cotaforge.contract.requests: base Request/Empty and the full request/response surface."""

import pytest
from pydantic import ValidationError

from cotaforge.contract.enums import (
    DocumentKind,
    EndCondition,
    HoleStandard,
)
from cotaforge.contract.geometry import Axis, Transform
from cotaforge.contract.handles import (
    BodyHandle,
    ComponentHandle,
    DocumentHandle,
    EdgeHandle,
    FaceHandle,
    FeatureHandle,
    SketchHandle,
)
from cotaforge.contract.requests import (
    ChamferRequest,
    ConnectRequest,
    CutRequest,
    CylinderRequest,
    Empty,
    ExtrudeRequest,
    HoleRequest,
    NewDocumentRequest,
    Request,
    SketchRequest,
)
from cotaforge.contract.sketch import RectEntity


def test_request_requires_operation_id() -> None:
    with pytest.raises(ValidationError):
        Request(operation_id="")


def test_request_operation_id_is_echoed() -> None:
    req = Request(operation_id="op-1")
    assert req.operation_id == "op-1"


def test_empty_is_a_request() -> None:
    empty = Empty(operation_id="op-1")
    assert isinstance(empty, Request)


def test_connect_request_default_timeout() -> None:
    req = ConnectRequest(operation_id="connect")
    assert req.timeout_s == 60.0


def test_new_document_request_fields() -> None:
    req = NewDocumentRequest(operation_id="op", kind=DocumentKind.part, name="plate")
    assert req.kind == DocumentKind.part
    assert req.name == "plate"


def test_sketch_request_defaults() -> None:
    from cotaforge.contract.enums import PlaneName

    rect = RectEntity(name="outline", center=(0.0, 0.0), width=10.0, height=10.0)
    req = SketchRequest(operation_id="op", on=PlaneName.XY, entities=(rect,))
    assert req.constraints == ()
    assert req.named_dimensions == ()


def test_extrude_request_depth_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        ExtrudeRequest(operation_id="op", sketch=SketchHandle(id="sketch:1"), depth_mm=0.0)


def test_extrude_request_depth2_must_be_positive_when_set() -> None:
    with pytest.raises(ValidationError):
        ExtrudeRequest(
            operation_id="op", sketch=SketchHandle(id="sketch:1"), depth_mm=5.0, both_directions=True, depth2_mm=-5.0
        )


def test_extrude_request_defaults() -> None:
    req = ExtrudeRequest(operation_id="op", sketch=SketchHandle(id="sketch:1"), depth_mm=5.0)
    from cotaforge.contract.enums import Direction

    assert req.direction == Direction.positive
    assert req.both_directions is False
    assert req.depth2_mm is None
    assert req.merge is True


def test_hole_request_fields() -> None:
    req = HoleRequest(
        operation_id="op",
        face=FaceHandle(id="face:1"),
        position=(1.0, 2.0),
        standard=HoleStandard.iso_clearance,
        size="M6",
        diameter_mm=6.6,
        end_condition=EndCondition.through_all,
        depth_mm=None,
    )
    assert req.diameter_mm == pytest.approx(6.6)


def test_cut_request_depth_must_be_positive_when_set() -> None:
    with pytest.raises(ValidationError):
        CutRequest(
            operation_id="op", sketch=SketchHandle(id="sketch:1"), end_condition=EndCondition.blind, depth_mm=-5.0
        )


def test_cut_request_depth2_must_be_positive_when_set() -> None:
    with pytest.raises(ValidationError):
        CutRequest(
            operation_id="op",
            sketch=SketchHandle(id="sketch:1"),
            end_condition=EndCondition.blind,
            depth_mm=5.0,
            both_directions=True,
            depth2_mm=-1.0,
        )


def test_hole_request_depth_must_be_positive_when_set() -> None:
    with pytest.raises(ValidationError):
        HoleRequest(
            operation_id="op",
            face=FaceHandle(id="face:1"),
            position=(1.0, 2.0),
            standard=HoleStandard.simple,
            diameter_mm=6.6,
            end_condition=EndCondition.blind,
            depth_mm=-5.0,
        )


def test_chamfer_request_optional_fields() -> None:
    req = ChamferRequest(
        operation_id="op",
        edges=(EdgeHandle(id="edge:1"),),
        distance1_mm=1.0,
        distance2_mm=None,
        distance1_face=None,
    )
    assert req.distance2_mm is None


def test_cylinder_request_axis() -> None:
    axis = Axis(origin=(0.0, 0.0, 0.0), direction=(0.0, 0.0, 1.0))
    req = CylinderRequest(operation_id="op", diameter_mm=10.0, height_mm=20.0, axis=axis)
    assert req.height_mm == 20.0


def test_box_request_default_transform() -> None:
    from cotaforge.contract.requests import BoxRequest

    req = BoxRequest(operation_id="op", size=(10.0, 10.0, 10.0))
    assert req.transform == Transform.identity()


def test_operation_result_defaults() -> None:
    from cotaforge.contract.responses import OperationResult

    result = OperationResult(operation_id="op", feature=FeatureHandle(id="feature:1"), rebuild_ok=True)
    assert result.created_faces == ()
    assert result.warnings == ()
    assert result.duration_ms == 0.0


def test_document_handle_roundtrip_json() -> None:
    doc = DocumentHandle(id="doc:1")
    dumped = doc.model_dump_json()
    restored = DocumentHandle.model_validate_json(dumped)
    assert restored == doc


def test_component_handle_distinct_type_from_body_handle() -> None:
    assert ComponentHandle(id="x") != BodyHandle(id="x")


def test_hole_request_thread_is_optional_cosmetic_metadata() -> None:
    from cotaforge.contract import HoleThread

    plain = HoleRequest(
        operation_id="op",
        face=FaceHandle(id="face:1"),
        position=(1.0, 2.0),
        standard=HoleStandard.iso_tapped,
        size="M6",
        diameter_mm=5.0,
        end_condition=EndCondition.blind,
        depth_mm=10.0,
    )
    assert plain.thread is None
    tapped = plain.model_copy(update={"thread": HoleThread(size="M6", depth_mm=8.0)})
    assert tapped.thread is not None and tapped.thread.size == "M6" and tapped.thread.depth_mm == 8.0
    assert HoleThread(size="M6").depth_mm is None
    with pytest.raises(ValidationError):
        HoleThread(size="M6", depth_mm=0.0)
    with pytest.raises(ValidationError):
        HoleThread(size="")
