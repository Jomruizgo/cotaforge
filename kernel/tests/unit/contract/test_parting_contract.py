"""The contract of ``parting_analysis`` (a read) and ``mould_split`` (a construction): both optional, both refused by a CAD
that cannot, with requests that refuse a zero pull and a block that is not a block."""

import pytest
from pydantic import ValidationError

from cotaforge.contract import (
    BLOCK_MARGIN_RULE,
    TOOL_REGISTRY,
    Adapter,
    AdapterError,
    BodyHandle,
    ErrorCode,
    Group,
    MouldSplitRequest,
    PartingAnalysisRequest,
    PartingAnalysisResult,
    proposed_block_margin,
)


@pytest.mark.parametrize(("name", "group"), [("parting_analysis", Group.QUERY), ("mould_split", Group.CONSTRUCTION)])
def test_both_tools_are_registered_as_optional(name: str, group: Group) -> None:
    spec = TOOL_REGISTRY[name]
    assert spec.optional is True and spec.group is group


def test_a_cad_that_cannot_says_so_and_does_not_pretend() -> None:
    for call, request in (
        (Adapter.parting_analysis, PartingAnalysisRequest(operation_id="p", pull=(0, 0, 1))),
        (Adapter.mould_split, MouldSplitRequest(operation_id="m", source=BodyHandle(id="body:1"), pull=(0, 0, 1))),
    ):
        with pytest.raises(AdapterError) as failure:
            call(None, request)  # type: ignore[arg-type]
        assert failure.value.info.code is ErrorCode.not_supported


@pytest.mark.parametrize("model", [PartingAnalysisRequest, MouldSplitRequest])
def test_a_zero_pull_is_refused(model: type) -> None:
    extra = {"source": BodyHandle(id="body:1")} if model is MouldSplitRequest else {}
    with pytest.raises(ValidationError, match="non-zero"):
        model(operation_id="x", pull=(0, 0, 0), **extra)


def test_a_block_with_a_side_of_nothing_is_refused() -> None:
    with pytest.raises(ValidationError, match="above 0"):
        MouldSplitRequest(operation_id="m", source=BodyHandle(id="body:1"), pull=(0, 0, 1), block_size=(100, 0, 50))


def test_the_split_defaults_ask_for_nothing_the_engineer_did_not_say() -> None:
    request = MouldSplitRequest(operation_id="m", source=BodyHandle(id="body:1"), pull=(0, 0, 1))
    assert request.slides == "none" and request.zero_draft_to is None and request.block_size is None
    assert request.margin_mm is None  # the block is the proposal, stated as one


def test_slides_are_all_none_or_a_list_of_regions() -> None:
    base = {"operation_id": "m", "source": BodyHandle(id="body:1"), "pull": (0, 0, 1)}
    assert MouldSplitRequest(**base, slides=(1, 3)).slides == (1, 3)
    assert MouldSplitRequest(**base, slides="all").slides == "all"
    with pytest.raises(ValidationError):
        MouldSplitRequest(**base, slides="some")


def test_the_proposed_margin_is_the_larger_of_25_mm_and_a_fifth_of_the_longest_side() -> None:
    assert proposed_block_margin(50.0) == 25.0
    assert proposed_block_margin(100.0) == 25.0
    assert proposed_block_margin(300.0) == 60.0
    assert "not a standard" in BLOCK_MARGIN_RULE and "25 mm" in BLOCK_MARGIN_RULE


def test_a_result_is_complete_by_default_and_says_nothing_it_did_not_measure() -> None:
    result = PartingAnalysisResult()
    assert result.complete and not result.loops and not result.slides and not result.provisional
