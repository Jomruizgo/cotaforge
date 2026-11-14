"""Mould tools: ``inspect(action=draft)`` (draft and undercuts by face) and ``op shrink_body`` (the linked cavity)."""

from typing import Any

import pytest
from cotaforge.adapters.fake import FakeAdapter
from cotaforge.contract import BodyHandle, DraftAnalysisResult, FaceDraft, FaceHandle

from cotaforge_mcp.schemas import TOOLS_BY_NAME
from tests.conftest import Caller, MakeSession, fake_without


class Canned(FakeAdapter):
    """The fake whose draft analysis answers a fixed list of faces (a real CAD shadows and samples; the fake cannot)."""

    def __init__(self, faces: tuple[FaceDraft, ...], painted: bool = False, shadow_tested: bool = True) -> None:
        super().__init__()
        self.faces = faces
        self.asked: list[Any] = []
        self.painted = painted
        self.shadow_tested = shadow_tested

    def draft_analysis(self, request: Any) -> DraftAnalysisResult:
        self.asked.append(request)
        return DraftAnalysisResult(
            faces=self.faces,
            method="canned",
            shadow_tested=self.shadow_tested,
            painted=self.painted and request.paint,
        )


def _face(n: int, status: str, draft: float, area: float = 100.0, surface: str = "plane", **more: Any) -> FaceDraft:
    return FaceDraft(
        face=FaceHandle(id=f"face:{n}"),
        body=BodyHandle(id="body:1"),
        surface=surface,
        area_mm2=area,
        draft_deg=draft,
        status=status,
        **more,
    )


FACES = (
    _face(1, "ok", 90.0, 6000.0),
    _face(2, "ok", 2.0, 1200.0),
    _face(3, "low", 0.4, 300.0),
    _face(4, "zero", 0.0, 900.0),
    _face(5, "undercut", 0.0, 80.0, "cylinder", shadowed=0.62, samples=40, exact=False),
)


def _canned(make: MakeSession, call_factory: Any, **kw: Any) -> tuple[Canned, Caller]:
    adapter = Canned(FACES, **kw)
    session = make(adapter)
    caller = call_factory(session)
    caller.ok("doc", action="new", name="p")
    caller.ok("op", name="box", id="b", params={"size": [40, 30, 10]})
    return adapter, caller


def test_the_draft_action_exists_only_with_a_cad_that_measures_it(make: MakeSession) -> None:
    assert "draft" in TOOLS_BY_NAME["inspect"].actions(make(FakeAdapter()))
    assert "draft" not in TOOLS_BY_NAME["inspect"].actions(make(fake_without("draft_analysis")))


def test_it_needs_the_pull_direction(make: MakeSession) -> None:
    _, call = _canned(make, Caller)
    reply = call("inspect", action="draft")
    assert reply.is_error and "pull" in reply.text


def test_the_counts_come_first_then_the_faces_that_are_not_ok_worst_first(make: MakeSession) -> None:
    adapter, call = _canned(make, Caller)
    text = call.ok("inspect", action="draft", pull=[0, 0, 1], min_draft_deg=1.5)
    lines = text.splitlines()
    assert lines[0] == "draft against the pull (0, 0, 1), minimum 1.5 deg: 5 faces = 1 undercut, 1 zero, 1 low, 2 ok"
    rows = [line for line in lines if line.startswith("face:")]
    assert [r.split()[0] for r in rows] == ["face:5", "face:4", "face:3"]  # undercut, zero, low: not the ok ones
    assert "62 % of the points cannot be reached along the pull" in rows[0] and "sampled at 40 points" in rows[0]
    assert rows[1].endswith(": zero") and "exact" in rows[1]
    assert (
        adapter.asked[0].min_draft_deg == 1.5 and adapter.asked[0].pull == (0, 0, 1) and adapter.asked[0].paint is False
    )


def test_without_a_minimum_the_default_is_used_and_its_source_is_said(make: MakeSession) -> None:
    adapter, call = _canned(make, Caller)
    text = call.ok("inspect", action="draft", pull=[0, 0, 1])
    assert "minimum 1 deg" in text
    assert "rule of thumb" in text and "not a standard" in text
    assert adapter.asked[0].min_draft_deg == 1.0


def test_a_minimum_given_by_the_engineer_needs_no_note(make: MakeSession) -> None:
    _, call = _canned(make, Caller)
    assert "rule of thumb" not in call.ok("inspect", action="draft", pull=[0, 0, 1], min_draft_deg=2)


def test_painting_is_asked_for_and_the_colours_are_explained(make: MakeSession) -> None:
    adapter, call = _canned(make, Caller, painted=True)
    text = call.ok("inspect", action="draft", pull=[0, 0, 1], paint=True)
    assert adapter.asked[0].paint is True
    assert "painted: green ok, yellow low, red zero or undercut" in text


def test_a_cad_that_cannot_cast_the_shadow_says_that_no_undercut_means_nothing(make: MakeSession) -> None:
    _, call = _canned(make, Caller, shadow_tested=False)
    assert "did not test undercuts" in call.ok("inspect", action="draft", pull=[0, 0, 1])


def test_a_long_list_shows_the_limit_and_writes_the_rest_to_a_file(make: MakeSession) -> None:
    many = tuple(_face(n, "zero", 0.0, float(n)) for n in range(1, 8))
    adapter = Canned(many)
    call = Caller(make(adapter))
    call.ok("doc", action="new", name="p")
    call.ok("op", name="box", id="b", params={"size": [40, 30, 10]})
    text = call.ok("inspect", action="draft", pull=[0, 0, 1], limit=3)
    assert len([ln for ln in text.splitlines() if ln.startswith("face:")]) == 3
    assert "4 more faces omitted; full report in" in text


def test_the_real_fake_reports_the_walls_of_a_box_as_having_no_draft(call: Caller) -> None:
    call.ok("doc", action="new", name="p")
    call.ok("op", name="box", id="b", params={"size": [40, 30, 10]})
    text = call.ok("inspect", action="draft", pull=[0, 0, 1])
    assert (
        text.splitlines()[0]
        == "draft against the pull (0, 0, 1), minimum 1 deg: 6 faces = 0 undercut, 4 zero, 0 low, 2 ok"
    )


def test_a_zero_pull_is_refused(call: Caller) -> None:
    call.ok("doc", action="new", name="p")
    call.ok("op", name="box", id="b", params={"size": [40, 30, 10]})
    assert call("inspect", action="draft", pull=[0, 0, 0]).is_error


# --- shrink_body -----------------------------------------------------------------------------------------------------


def test_the_cavity_op_is_listed_in_the_guide_with_its_two_mandatory_fields(call: Caller) -> None:
    text = call.ok("cad_guide", topic="op:shrink_body")
    assert "shrinkage_percent" in text and "factor_source" in text


def test_the_cavity_is_made_linked_and_keeps_the_factor_and_its_source_in_the_intent(call: Caller) -> None:
    call.ok("doc", action="new", name="p")
    call.ok("op", name="box", id="part", params={"size": [100, 60, 20]})
    text = call.ok(
        "op",
        name="shrink_body",
        id="cavity",
        params={
            "source": {"kind": "body", "from_op": "part"},
            "shrinkage_percent": 0.6,
            "factor_source": "datasheet X",
        },
        intent="cavity",
    )
    assert "cavity" in text
    intent = call.session.executor.plan.operations[-1].intent  # what the plan file keeps
    assert intent == "cavity [shrinkage 0.6 % isotropic (source: datasheet X)]"
    assert "scale=(1.006, 1.006, 1.006)" in call.ok("inspect", action="tree")


def test_a_cavity_without_its_source_is_refused(call: Caller) -> None:
    call.ok("doc", action="new", name="p")
    call.ok("op", name="box", id="part", params={"size": [100, 60, 20]})
    reply = call(
        "op",
        name="shrink_body",
        id="cavity",
        params={"source": {"kind": "body", "from_op": "part"}, "shrinkage_percent": 0.6},
        intent="cavity",
    )
    assert reply.is_error and "factor_source" in reply.text


def test_editing_the_part_moves_the_cavity_with_it(call: Caller) -> None:
    call.ok("doc", action="new", name="p")
    call.ok("op", name="box", id="part", params={"size": [100, 60, 20]})
    call.ok(
        "op",
        name="shrink_body",
        id="cavity",
        params={
            "source": {"kind": "body", "from_op": "part"},
            "shrinkage_percent": 1.0,
            "factor_source": "datasheet X",
        },
        intent="cavity",
    )
    call.ok("op", edit="part", params={"size": [120, 60, 20]})
    measured = call.ok("inspect", action="measure")
    assert "121.2" in measured or "(121.2" in measured  # 120 x 1.01 along X: the cavity followed the part


@pytest.mark.parametrize("name", ["shrink_body"])
def test_the_op_name_is_in_the_catalog(call: Caller, name: str) -> None:
    assert name in call.ok("cad_guide", topic="ops")
