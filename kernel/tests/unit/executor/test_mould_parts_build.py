"""The requests the two mould-parts steps build: catalogue numbers in millimetres, the split step as a handle."""

from typing import Any

import pytest

from cotaforge.contract import BodyHandle, FeatureHandle, MouldComponentRequest, SlideMechanismRequest
from cotaforge.executor.build import DISPATCH
from cotaforge.executor.errors import ResolutionError
from cotaforge.ir import parse_plan

PART = {"id": "part", "op": "box", "intent": "plastic part", "size": [100, 60, 20]}
SHRUNK = {
    "id": "shrunk",
    "op": "shrink_body",
    "intent": "shrunk part",
    "source": {"kind": "body", "from_op": "part"},
    "shrinkage_percent": 0.6,
    "factor_source": "resin datasheet",
}
SPLIT = {
    "id": "inserts",
    "op": "mould_split",
    "intent": "cavity and core",
    "source": {"kind": "body", "from_op": "shrunk"},
    "pull": "Z",
    "slides": "all",
}


class Resolver:
    def body(self, ref: Any, op_id: str, field: str) -> BodyHandle:
        return BodyHandle(id="body:2")


class Context:
    feature_by_op = {"inserts": FeatureHandle(id="feature:3")}


def _op(*ops: dict[str, Any]) -> Any:
    return parse_plan({"id": "m", "kind": "part", "operations": list(ops)}).operations[-1]


def test_the_component_request_carries_the_parts_in_document_coordinates() -> None:
    method, builder = DISPATCH["mould_component"]
    assert method == "mould_component"
    op = _op(
        {
            "id": "base", "op": "mould_component", "intent": "base", "kind": "mould_base", "size": "1012",
            "at": [{"origin": [10, 20, 30], "axis": [0, 1, 0]}],
        }
    )  # fmt: skip
    request = builder(op, None, Resolver())
    assert isinstance(request, MouldComponentRequest) and len(request.parts) == 23
    ring = next(p for p in request.parts if p.role == "locating_ring")
    assert (
        ring.frame.origin[0] == pytest.approx(10.0) and ring.frame.origin[1] < 20.0
    )  # the platen side is -pull: here -y
    assert request.parts[0].frame.z == pytest.approx((0.0, 1.0, 0.0))


def test_the_mechanism_request_names_the_split_step_and_carries_the_catalogue_rows_it_may_pick_from() -> None:
    method, builder = DISPATCH["slide_mechanism"]
    assert method == "slide_mechanism"
    op = _op(
        PART, SHRUNK, SPLIT,
        {
            "id": "mech", "op": "slide_mechanism", "intent": "mechanism", "split": "inserts", "slide": 2,
            "part": {"kind": "body", "from_op": "shrunk"}, "pull": [0, 0, 2], "angle_deg": 12, "pin_diameter_mm": 14,
        },
    )  # fmt: skip
    request = builder(op, Context(), Resolver())
    assert isinstance(request, SlideMechanismRequest)
    assert request.split.id == "feature:3" and request.slide == 2 and request.pull == pytest.approx((0, 0, 1))
    assert {o.d for o in request.pin_options} == {14.0} and request.pin_options[0].item == "APD1440"
    assert request.wear_options and request.sources["angle_pin"].startswith("DME")
    assert request.wedge_extra_deg == 2.0 and request.angle_deg == 12.0


def test_a_pin_diameter_that_is_not_a_catalogue_one_is_refused() -> None:
    _, builder = DISPATCH["slide_mechanism"]
    op = _op(
        PART, SHRUNK, SPLIT,
        {
            "id": "mech", "op": "slide_mechanism", "intent": "mechanism", "split": "inserts", "slide": 1,
            "part": {"kind": "body", "from_op": "shrunk"}, "angle_deg": 15, "pin_diameter_mm": 13,
        },
    )  # fmt: skip
    with pytest.raises(ResolutionError, match="angle pin"):
        builder(op, Context(), Resolver())
