"""``view(action="references")``: the agent shows or hides planes, axes, points and sketches by name."""

from typing import Any

import pytest
from cotaforge.contract import ReferenceVisibilityResult

from cotaforge_mcp.schemas import TOOLS_BY_NAME, input_schema
from tests.conftest import Caller, MakeSession, VisibleFake, plate


class ReferenceFake(VisibleFake):
    """A CAD with a few references; each is shown or hidden and the call says what changed, like the worker."""

    def __init__(self) -> None:
        super().__init__()
        self.shown: dict[str, bool] = {"Plane1": False, "Plane2": True, "Sketch1": False}
        self.requests: list[Any] = []

    def set_reference_visibility(self, request: Any) -> ReferenceVisibilityResult:
        self.requests.append(request)
        changed, already, unknown = [], [], []
        for name in request.names:
            if name not in self.shown:
                unknown.append(name)
            elif self.shown[name] == request.visible:
                already.append(name)
            else:
                self.shown[name] = request.visible
                changed.append(name)
        return ReferenceVisibilityResult(changed=tuple(changed), already=tuple(already), unknown=tuple(unknown))


@pytest.fixture
def cad(make: MakeSession) -> Caller:
    call = Caller(make(ReferenceFake()))
    plate(call)
    return call


def fake(call: Caller) -> ReferenceFake:
    adapter = call.session.adapter
    assert isinstance(adapter, ReferenceFake)
    return adapter


def test_the_action_exists_when_the_cad_can_do_it(cad: Caller) -> None:
    assert "references" in input_schema(TOOLS_BY_NAME["view"], cad.session)["properties"]["action"]["enum"]


def test_a_hidden_plane_is_shown_to_look_at_it_and_hidden_again(cad: Caller) -> None:
    shown = cad.ok("view", action="references", ids=["Plane1"], visible=True)
    assert shown.startswith("references shown: Plane1") and "display only" in shown
    assert fake(cad).shown["Plane1"] is True
    hidden = cad.ok("view", action="references", ids=["Plane1", "Sketch1"], visible=False)
    assert hidden.startswith("references hidden: Plane1; already hidden: Sketch1")
    assert fake(cad).shown == {"Plane1": False, "Plane2": True, "Sketch1": False}


def test_a_name_that_is_no_reference_is_named(cad: Caller) -> None:
    said = cad.ok("view", action="references", ids=["Plane2", "Nope"], visible=True)
    assert "already shown: Plane2" in said and "not a reference of this document: Nope" in said


def test_without_names_or_visible_it_says_how_to_call(cad: Caller) -> None:
    assert "references needs ids" in cad.err("view", action="references", visible=True)
    assert "references needs ids" in cad.err("view", action="references", ids=["Plane1"])
    assert fake(cad).requests == []


def test_a_cad_that_cannot_says_so(make: MakeSession) -> None:
    call = Caller(make(VisibleFake()))
    plate(call)
    assert "references" not in input_schema(TOOLS_BY_NAME["view"], call.session)["properties"]["action"]["enum"]
