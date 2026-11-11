"""Several bodies in one part and the ways the agent names things: interference between the bodies of a part, a body by
its step or by its id, and the id a selection gives (usable in inspect, view show and an op)."""

import re
from typing import Any

import pytest
from cotaforge.adapters.fake import FakeAdapter
from cotaforge.contract import EntityResult, FaceHandle, PersistentRef, ResolveRequest

from tests.conftest import Caller, MakeSession, fake_without, plate
from tests.unit.test_tools import volume_of


def boxes(call: Caller) -> None:
    """Three 10 mm cubes: a at x 0..10, b at 5..15 (500 mm3 inside a), c at 15..25 (touches b, 5 mm from a)."""
    call.ok("doc", action="new", name="cubes")
    call.ok("op", name="box", id="a", params={"size": [10, 10, 10]})
    call.ok("op", name="box", id="b", params={"size": [10, 10, 10], "placement": {"origin": [5, 0, 0]}})
    call.ok("op", name="box", id="c", params={"size": [10, 10, 10], "placement": {"origin": [15, 0, 0]}})


# --- interference in a part ------------------------------------------------------------------------------------------


def test_interference_in_a_part_measures_every_pair_of_bodies(call: Caller) -> None:
    boxes(call)
    text = call.ok("inspect", action="interference")
    assert "none" not in text
    assert text.startswith("interference: 3 bodies, 3 pairs: 1 overlap (500 mm3), 1 touching, 1 clear")
    assert "body:1 [a] x body:2 [b]: OVERLAP 500 mm3" in text
    assert "body:2 [b] x body:3 [c]: touching (no common volume)" in text
    assert "body:1 [a] x body:3 [c]: clear, min distance 5 mm" in text


def test_interference_never_answers_none_for_what_it_did_not_check(call: Caller, make: MakeSession) -> None:
    call.ok("doc", action="new", name="one")
    call.ok("op", name="box", params={"size": [10, 10, 10]})
    single = call.ok("inspect", action="interference")
    assert "not checked" in single and "1 body" in single and "none" not in single

    call.ok("op", name="cylinder", params={"diameter_mm": 4, "height_mm": 10, "origin": [50, 5, 0]})
    partial = call.ok("inspect", action="interference")  # the fake measures no distance to a cylinder
    assert "1 not fully measured" in partial and "clear" not in partial
    assert "distance NOT measured" in partial and "axis-aligned boxes" in partial

    other = Caller(make(fake_without("body_interference")))
    boxes(other)
    assert "not available" in other.err("inspect", action="interference")


# --- a body by its step or by its id ---------------------------------------------------------------------------------


def test_a_body_is_named_by_its_step_or_by_its_id_wherever_both_mean_the_same(call: Caller) -> None:
    boxes(call)
    by_step = call.ok("inspect", action="properties", body="b")
    assert volume_of(by_step) == pytest.approx(1000)
    assert by_step == call.ok("inspect", action="properties", body="body:2")
    measure = call.ok("inspect", action="measure")
    assert "body:1 [a]:" in measure and "body:3 [c]:" in measure

    moved = call.ok("op", name="translate", id="m1", params={"target": "body:3", "offset": [10, 0, 0]})
    assert moved.startswith("ok: m1 translate")
    by_id_in_from_op = {"target": {"kind": "body", "from_op": "body:3"}, "offset": [10, 0, 0]}
    assert call.ok("op", name="translate", id="m2", params=by_id_in_from_op).startswith("ok: m2 translate")
    assert call.ok("op", name="translate", id="m3", params={"target": "a", "offset": [0, 50, 0]}).startswith("ok: m3")
    plan = {op.id: op for op in call.session.executor.plan.operations}  # type: ignore[union-attr]
    assert plan["m1"].target.from_op == "c" and plan["m2"].target.from_op == "m1"  # type: ignore[attr-defined]
    assert plan["m3"].target.from_op == "a"  # type: ignore[attr-defined]
    after = call.ok("inspect", action="interference")  # a body is labelled by the last step that names it
    assert "body:2 [b] x body:3 [m2]: clear, min distance 20 mm" in after and "body:1 [m3]" in after


def test_a_reference_in_the_wrong_form_is_refused_with_the_form_to_use(call: Caller) -> None:
    boxes(call)
    unknown = call.err("op", name="translate", params={"target": "body:9", "offset": [1, 0, 0]})
    assert "params.target" in unknown and '{"kind":"body","from_op":"a"}' in unknown
    assert "body:1 = a" in unknown and "body:3 = c" in unknown

    hole = {"position": [0, 0], "standard": "simple", "diameter_mm": 3, "end_condition": "through_all"}
    face = call.err("op", name="hole", params={"face": "face:6", **hole})
    assert "params.face" in face and '{"kind":"face","from_op":"a","select":' in face and "from_selection" in face

    faces = call.err("inspect", action="faces", op="body:2")
    assert 'op="b"' in faces and "step" in faces

    for wrong in ("body:1", "a"):
        shown = call.err("view", action="show", ids=[wrong])
        assert "face or edge id" in shown and 'inspect(action="faces", op="a")' in shown


# --- the id a selection gives ----------------------------------------------------------------------------------------


class TokenFake(FakeAdapter):
    """Persistent tokens shaped like FreeCAD's (``<doc>|<body>|<entity>``) instead of the bare entity id."""

    def persistent_reference(self, request: Any) -> PersistentRef:
        ref = super().persistent_reference(request)
        return ref.model_copy(update={"token": f"doc:1|body:1|{ref.token}"})

    def resolve_reference(self, request: ResolveRequest) -> EntityResult:
        bare = request.ref.model_copy(update={"token": request.ref.token.rsplit("|", 1)[-1]})
        return super().resolve_reference(request.model_copy(update={"ref": bare}))


def test_the_id_a_selection_gives_works_in_inspect_view_show_and_an_op(make: MakeSession) -> None:
    adapter = TokenFake()
    call = Caller(make(adapter))
    plate(call)
    top = re.search(r"(face:\d+)", call.ok("inspect", action="faces", normal=[0, 0, 1])).group(1)  # type: ignore[union-attr]
    adapter.set_selection([FaceHandle(id=top)])

    selection = call.ok("view", action="selection")
    picked = re.search(r"id=(\S+)", selection)
    assert picked is not None, selection
    assert picked.group(1) == top and "doc:1|" not in selection  # the adapter's token is not what the agent gets

    assert top in call.ok("inspect", action="face", id=picked.group(1))
    assert call.ok("view", action="show", ids=[picked.group(1)]).startswith("shown: highlight")
    face = {"kind": "face", "from_selection": picked.group(1)}
    hole = {"face": face, "position": [0, 0], "standard": "simple", "diameter_mm": 6, "end_condition": "through_all"}
    assert call.ok("op", name="hole", params=hole).startswith("ok: s3 hole")
