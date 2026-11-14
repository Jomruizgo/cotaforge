"""Mould tools: ``inspect(action=draft)`` (draft and undercuts by face) and ``op shrink_body`` (the linked cavity)."""

from typing import Any

import pytest
from cotaforge.adapters.fake import FakeAdapter
from cotaforge.contract import (
    BodyHandle,
    DraftAnalysisResult,
    EdgeHandle,
    FaceDraft,
    FaceHandle,
    PartingAnalysisResult,
    PartingLoop,
    SlideProposal,
    WallsToDecide,
)

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


# --- parting ---------------------------------------------------------------------------------------------------------


class CannedParting(FakeAdapter):
    """The fake whose parting analysis answers a fixed result (a real CAD finds the loops, walls and undercuts)."""

    def __init__(self, result: PartingAnalysisResult) -> None:
        super().__init__()
        self.result = result
        self.asked: list[Any] = []

    def parting_analysis(self, request: Any) -> PartingAnalysisResult:
        self.asked.append(request)
        return self.result


def _loop(number: int, role: str, closed: bool = True, planar: bool = True, levels: tuple[float, ...] = (45.0,)) -> Any:
    return PartingLoop(
        number=number,
        role=role,
        closed=closed,
        length_mm=386.3,
        planar=planar,
        levels_mm=levels,
        edges=tuple(EdgeHandle(id=f"edge:{i}") for i in range(8)),
    )


def _parting(make: MakeSession, result: PartingAnalysisResult) -> tuple[CannedParting, Caller]:
    adapter = CannedParting(result)
    call = Caller(make(adapter))
    call.ok("doc", action="new", name="p")
    call.ok("op", name="box", id="b", params={"size": [40, 30, 10]})
    return adapter, call


def test_the_parting_action_exists_only_with_a_cad_that_finds_it(make: MakeSession) -> None:
    assert "parting" in TOOLS_BY_NAME["inspect"].actions(make(FakeAdapter()))
    assert "parting" not in TOOLS_BY_NAME["inspect"].actions(make(fake_without("parting_analysis")))


def test_parting_needs_the_pull_and_says_which_half_it_opens(make: MakeSession) -> None:
    _, call = _parting(make, PartingAnalysisResult())
    reply = call("inspect", action="parting")
    assert reply.is_error and "pull" in reply.text and "core" in reply.text


def test_a_clean_part_lists_its_loops_and_the_block_rule(make: MakeSession) -> None:
    adapter, call = _parting(
        make, PartingAnalysisResult(loops=(_loop(1, "outer"), _loop(2, "inner", levels=(0.0,))), method="m")
    )
    text = call.ok("inspect", action="parting", pull=[0, 0, 1], id="cavity")
    lines = text.splitlines()
    assert (
        lines[0] == "parting against the pull (0, 0, 1): 2 loop(s), 0 group(s) of walls to decide, 0 undercut region(s)"
    )
    assert "loop 1 (outer): 8 edges, 386.3 mm, planar, at 45 mm along the pull" in text
    assert "loop 2 (inner): 8 edges, 386.3 mm, planar, at 0 mm along the pull" in text
    assert "not a standard" in text and "25 mm" in text
    assert adapter.asked[0].zero_draft_to == "cavity" and adapter.asked[0].pull == (0, 0, 1)


def test_a_line_that_steps_says_across_how_many_heights(make: MakeSession) -> None:
    _, call = _parting(make, PartingAnalysisResult(loops=(_loop(1, "outer", planar=False, levels=(24.0, 30.0)),)))
    assert "not planar, steps across heights 24, 30 mm" in call.ok("inspect", action="parting", pull=[0, 0, 1])


def test_walls_to_decide_are_asked_and_the_loops_are_marked_provisional(make: MakeSession) -> None:
    walls = WallsToDecide(faces=tuple(FaceHandle(id=f"face:{n}") for n in range(1, 5)), touches=("cavity", "core"))
    result = PartingAnalysisResult(
        loops=(_loop(1, "outer"),), ambiguous=(walls,), zero_draft_proposal="cavity", provisional=True
    )
    _, call = _parting(make, result)
    text = call.ok("inspect", action="parting", pull=[0, 0, 1])
    assert "DECIDE: 4 wall(s) with no draft touch both halves (cavity, core): face:1, face:2, face:3, face:4" in text
    assert "zero_draft_to=core or cavity" in text
    assert "computed under the proposal zero_draft_to=cavity" in text and "the engineer decides" in text


def test_an_undercut_region_names_the_slide_it_needs_and_how_to_accept_it(make: MakeSession) -> None:
    slide = SlideProposal(
        region=1,
        faces=(FaceHandle(id="face:7"),),
        area_mm2=531.4,
        direction=(1.0, 0.0, 0.0),
        why="axis of its cylinder face:7",
    )
    _, call = _parting(make, PartingAnalysisResult(loops=(_loop(1, "outer"),), slides=(slide,)))
    text = call.ok("inspect", action="parting", pull=[0, 0, 1])
    assert "undercut region 1: 1 face(s)" in text and "needs a SLIDE along (1, 0, 0)" in text
    assert "slides=[1] or all" in text


def test_an_undercut_nothing_frees_gets_no_slide_and_the_reason(make: MakeSession) -> None:
    slide = SlideProposal(region=1, faces=(FaceHandle(id="face:9"),), area_mm2=12.0, direction=None)
    _, call = _parting(make, PartingAnalysisResult(loops=(_loop(1, "outer"),), slides=(slide,)))
    assert "no direction frees it, so no slide can be proposed" in call.ok("inspect", action="parting", pull=[0, 0, 1])


def test_faces_the_silhouette_crosses_are_a_declared_limit_not_a_silent_gap(make: MakeSession) -> None:
    result = PartingAnalysisResult(loops=(_loop(1, "outer"),), straddle=(FaceHandle(id="face:3"),), complete=False)
    _, call = _parting(make, result)
    text = call.ok("inspect", action="parting", pull=[0, 0, 1])
    assert "INCOMPLETE" in text.splitlines()[0]
    assert "LIMIT: the silhouette runs across face:3" in text and "cannot split a face along its silhouette" in text


def test_a_loop_that_does_not_close_is_said(make: MakeSession) -> None:
    _, call = _parting(make, PartingAnalysisResult(loops=(_loop(1, "outer", closed=False),)))
    assert "(NOT closed)" in call.ok("inspect", action="parting", pull=[0, 0, 1])


def test_a_zero_pull_is_refused_for_the_parting_too(call: Caller) -> None:
    call.ok("doc", action="new", name="p")
    call.ok("op", name="box", id="b", params={"size": [40, 30, 10]})
    assert call("inspect", action="parting", pull=[0, 0, 0]).is_error


def test_the_real_fake_asks_about_the_walls_of_a_box(call: Caller) -> None:
    call.ok("doc", action="new", name="p")
    call.ok("op", name="box", id="b", params={"size": [40, 30, 10]})
    text = call.ok("inspect", action="parting", pull=[0, 0, 1])
    assert (
        text.splitlines()[0]
        == "parting against the pull (0, 0, 1): 1 loop(s), 1 group(s) of walls to decide, 0 undercut region(s)"
    )
    assert "DECIDE: 4 wall(s) with no draft touch both halves" in text


def test_the_split_op_has_its_reference_in_the_guide(call: Caller) -> None:
    text = call.ok("cad_guide", topic="op:mould_split")
    assert "zero_draft_to" in text and "slides" in text
