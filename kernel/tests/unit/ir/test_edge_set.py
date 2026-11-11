"""``EdgeSet`` (several edges named by what they are) in the IR: shape, aliases, plan rules."""

import pytest
from pydantic import ValidationError

from cotaforge.ir import ChamferOperation, EdgeRef, EdgeSet, EdgeWhere, FaceRef, FilletOperation, parse_plan
from cotaforge.ir.errors import PlanError
from cotaforge.ir.refs import Range

_PLATE = [
    {
        "id": "sk",
        "op": "sketch",
        "intent": "outline",
        "on": {"kind": "plane", "name": "XY"},
        "entities": [{"kind": "rect", "name": "outline", "center": [0, 0], "width": 40, "height": 30}],
    },
    {"id": "plate", "op": "extrude", "intent": "thickness", "sketch": "sk", "depth_mm": 10},
]


def _plan(*ops: dict) -> dict:
    return {"id": "p", "kind": "part", "operations": [*_PLATE, *ops]}


def test_edge_set_needs_a_source() -> None:
    with pytest.raises(ValidationError, match="from_op"):
        EdgeSet(kind="edges")
    assert EdgeSet(kind="edges", from_op="plate").where == EdgeWhere()
    on_face = FaceRef(from_op="plate", select={"normal": (0, 0, 1)})
    assert EdgeSet(kind="edges", where=EdgeWhere(on_face=on_face)).from_op is None


def test_except_is_written_except_and_read_back_either_way() -> None:
    ref = EdgeSet.model_validate({"kind": "edges", "from_op": "plate", "where": {"except": [[1.0, 2.0, 3.0]]}})
    assert ref.where.except_ == ((1.0, 2.0, 3.0),)
    dumped = ref.model_dump(mode="json")
    assert EdgeSet.model_validate(dumped) == ref  # the dump says except_: it must parse back
    edge = {"kind": "edge", "of": {"kind": "face", "from_op": "plate"}, "curve": "line"}
    mixed = EdgeWhere.model_validate({"except": [[0, 0, 0], edge]})
    assert isinstance(mixed.except_[1], EdgeRef)


def test_where_rejects_unknown_criteria_and_takes_ranges_and_axes() -> None:
    with pytest.raises(ValidationError):
        EdgeWhere.model_validate({"parallell_to": "z"})
    where = EdgeWhere.model_validate({"parallel_to": "z", "length_mm": {"min": 2, "max": 9}, "convex": True})
    assert where.length_mm == Range(min=2, max=9)
    assert EdgeWhere.model_validate({"parallel_to": [1, 1, 0]}).parallel_to == (1.0, 1.0, 0.0)


def test_chamfer_and_fillet_take_sets_and_single_edges_mixed() -> None:
    single = {"kind": "edge", "of": {"kind": "face", "from_op": "plate"}, "curve": "line"}
    a_set = {"kind": "edges", "from_op": "plate", "where": {"parallel_to": "z"}}
    chamfer = ChamferOperation.model_validate(
        {"id": "c", "op": "chamfer", "intent": "i", "edges": [single, a_set], "distance1_mm": 1, "skip_failing": True}
    )
    assert isinstance(chamfer.edges[0], EdgeRef) and isinstance(chamfer.edges[1], EdgeSet)
    assert chamfer.skip_failing is True
    fillet = FilletOperation.model_validate(
        {"id": "f", "op": "fillet", "intent": "i", "edges": [a_set], "radius_mm": 2}
    )
    assert fillet.skip_failing is False


def test_a_set_wrongly_kinded_as_an_edge_is_refused() -> None:
    with pytest.raises(ValidationError):
        ChamferOperation.model_validate(
            {
                "id": "c",
                "op": "chamfer",
                "intent": "i",
                "edges": [{"kind": "edge", "from_op": "plate", "where": {}}],
                "distance1_mm": 1,
            }
        )


def test_refs_walk_into_the_set_and_the_plan_checks_its_step() -> None:
    face = {"kind": "face", "from_op": "plate", "select": {"normal": [0, 0, 1]}}
    chamfer = {
        "id": "c",
        "op": "chamfer",
        "intent": "i",
        "edges": [{"kind": "edges", "from_op": "plate", "where": {"on_face": face}}],
        "distance1_mm": 1,
    }
    plan = parse_plan(_plan(chamfer))
    refs = plan.operations[-1].refs()
    assert any(isinstance(r, EdgeSet) for r in refs) and any(isinstance(r, FaceRef) for r in refs)
    forward = {**chamfer, "edges": [{"kind": "edges", "from_op": "later"}]}
    with pytest.raises(PlanError, match="unknown or later"):
        parse_plan(_plan(forward))
    on_sketch = {**chamfer, "edges": [{"kind": "edges", "from_op": "sk"}]}
    with pytest.raises(PlanError, match="wrong operation type"):
        parse_plan(_plan(on_sketch))
