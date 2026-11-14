"""``draft_analysis`` on the fake (planes exact, cylinders zero) and the contract of the tool."""

import math

import pytest
from pydantic import ValidationError

from cotaforge.contract import (
    DEFAULT_MIN_DRAFT_DEG,
    DRAFT_ZERO_DEG,
    TOOL_REGISTRY,
    AdapterError,
    BodyHandle,
    DraftAnalysisRequest,
    DraftAnalysisResult,
    ErrorCode,
    classify_draft,
)
from tests.unit.executor.test_with_fake import _fake, _run

BLOCK = {"id": "part", "op": "box", "intent": "plastic part", "size": [100, 60, 20]}
PLAN = {"id": "p", "kind": "part", "operations": [BLOCK]}


def _analysis(pull: tuple[float, float, float], minimum: float = 1.0) -> DraftAnalysisResult:
    adapter = _fake()
    _run([PLAN], adapter)
    return adapter.draft_analysis(DraftAnalysisRequest(operation_id="d", pull=pull, min_draft_deg=minimum))


def test_the_tool_is_registered_as_an_optional_read_and_the_base_adapter_refuses() -> None:
    spec = TOOL_REGISTRY["draft_analysis"]
    assert spec.optional is True
    with pytest.raises(AdapterError) as failure:
        spec.request_model  # noqa: B018 - registered
        from cotaforge.contract import Adapter

        Adapter.draft_analysis(None, DraftAnalysisRequest(operation_id="d", pull=(0, 0, 1)))  # type: ignore[arg-type]
    assert failure.value.info.code is ErrorCode.not_supported


def test_a_box_pulled_along_z_has_ninety_degrees_on_top_and_bottom_and_none_on_the_walls() -> None:
    result = _analysis((0, 0, 1))
    by_draft = sorted(round(f.draft_deg, 6) for f in result.faces)
    assert by_draft == [0.0, 0.0, 0.0, 0.0, 90.0, 90.0]
    walls = [f for f in result.faces if f.draft_deg == 0]
    assert all(f.status == "zero" and f.exact for f in walls)
    assert {f.status for f in result.faces if f.draft_deg == 90} == {"ok"}


def test_an_oblique_pull_gives_the_angle_between_the_face_and_the_pull() -> None:
    result = _analysis((1, 0, 1))  # 45 degrees from both x and z
    assert {round(f.draft_deg, 6) for f in result.faces if f.draft_deg not in (0.0,)} == {45.0}


def test_areas_come_from_the_faces() -> None:
    result = _analysis((0, 0, 1))
    areas = sorted(round(f.area_mm2, 3) for f in result.faces)
    assert areas == [1200.0, 1200.0, 2000.0, 2000.0, 6000.0, 6000.0]


def test_the_fake_says_it_did_not_test_undercuts_so_none_is_reported() -> None:
    result = _analysis((0, 0, 1))
    assert result.shadow_tested is False and "no shadow test" in result.method
    assert all(f.shadowed == 0.0 for f in result.faces)


def test_one_body_can_be_asked_for_and_an_unknown_one_finds_nothing() -> None:
    adapter = _fake()
    _run([PLAN], adapter)
    none = adapter.draft_analysis(DraftAnalysisRequest(operation_id="d", body=BodyHandle(id="body:99"), pull=(0, 0, 1)))
    assert none.faces == ()


def test_a_zero_pull_is_refused_by_the_request() -> None:
    with pytest.raises(ValidationError):
        DraftAnalysisRequest(operation_id="d", pull=(0, 0, 0))


@pytest.mark.parametrize(
    ("draft", "shadowed", "expected"),
    [
        (0.0, False, "zero"),
        (DRAFT_ZERO_DEG, False, "zero"),
        (0.5, False, "low"),
        (DEFAULT_MIN_DRAFT_DEG, False, "ok"),
        (DEFAULT_MIN_DRAFT_DEG - 0.0005, False, "ok"),  # a face drafted to exactly the minimum measures a hair under
        (DEFAULT_MIN_DRAFT_DEG - 0.01, False, "low"),
        (3.0, False, "ok"),
        (90.0, False, "ok"),
        (45.0, True, "undercut"),
        (0.0, True, "undercut"),
    ],
)
def test_the_classification(draft: float, shadowed: bool, expected: str) -> None:
    assert classify_draft(draft, shadowed, DEFAULT_MIN_DRAFT_DEG) == expected


def test_the_default_minimum_is_one_degree() -> None:
    assert math.isclose(DEFAULT_MIN_DRAFT_DEG, 1.0)
