"""``parting_analysis`` on the fake: a box has four walls with no draft that touch both halves, so the engineer must decide;
under either decision the parting line is one closed loop of four edges (the rim, or the bottom)."""

import pytest

from cotaforge.contract import PartingAnalysisRequest, PartingAnalysisResult
from tests.unit.executor.test_with_fake import _fake, _run

BLOCK = {"id": "part", "op": "box", "intent": "plastic part", "size": [100, 60, 20]}
PLAN = {"id": "p", "kind": "part", "operations": [BLOCK]}


def _analysis(pull: tuple[float, float, float], decision: str | None = None) -> PartingAnalysisResult:
    adapter = _fake()
    _run([PLAN], adapter)
    return adapter.parting_analysis(PartingAnalysisRequest(operation_id="p", pull=pull, zero_draft_to=decision))


def test_the_four_walls_of_a_box_have_no_draft_and_touch_both_halves() -> None:
    result = _analysis((0, 0, 1))
    assert len(result.ambiguous) == 1
    (group,) = result.ambiguous
    assert len(group.faces) == 4 and group.touches == ("cavity", "core")
    assert result.provisional is True and result.zero_draft_proposal == "cavity"  # a tie goes to the cavity


@pytest.mark.parametrize("decision", ["core", "cavity"])
def test_a_decision_gives_one_closed_loop_of_four_edges_and_nothing_left_to_decide(decision: str) -> None:
    result = _analysis((0, 0, 1), decision)
    assert not result.ambiguous and not result.provisional and result.complete
    (loop,) = result.loops
    assert loop.closed and loop.role == "outer" and len(loop.edges) == 4
    assert loop.length_mm == pytest.approx(2 * (100 + 60))  # the outline of the box across the pull


def test_the_loop_follows_the_pull() -> None:
    along_x = _analysis((1, 0, 0), "cavity")
    (loop,) = along_x.loops
    assert loop.length_mm == pytest.approx(2 * (60 + 20))  # the outline across X is the 60 x 20 face


def test_the_fake_says_it_cannot_see_undercuts() -> None:
    result = _analysis((0, 0, 1), "cavity")
    assert not result.slides
    assert any("no undercut is reported" in w for w in result.warnings)


def test_a_zero_pull_never_reaches_the_adapter() -> None:
    with pytest.raises(ValueError, match="non-zero"):
        PartingAnalysisRequest(operation_id="p", pull=(0, 0, 0))
