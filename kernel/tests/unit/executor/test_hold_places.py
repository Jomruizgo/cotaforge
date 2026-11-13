"""A rebuild runs the same requests again; ``Executor.hold_places`` makes a hole or a sketch on a planar face come back
to the place it had instead of to ``position`` measured from the centre of the face as it is now (on the fake)."""

import math
from typing import Any

import pytest

from cotaforge.adapters.fake.adapter import FakeAdapter
from cotaforge.contract import ConnectRequest, SketchFrame
from cotaforge.executor.config import ExecutorConfig
from cotaforge.executor.executor import Executor
from cotaforge.executor.trace import ExecutorState
from cotaforge.ir import parse_plan
from cotaforge.verification.verifier import Verifier

# 100 x 60 x 8 plate, corner at the origin: the top face is centred at (50, 30, 8).
TOP = {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, 1]}}
PLAN = [
    {
        "id": "s1",
        "op": "sketch",
        "intent": "outline",
        "on": {"kind": "plane", "name": "XY"},
        "entities": [{"kind": "rect", "name": "r", "center": [50, 30], "width": 100, "height": 60}],
    },
    {"id": "base", "op": "extrude", "intent": "plate", "sketch": "s1", "depth_mm": 8},
    {
        "id": "h1",
        "op": "hole",
        "intent": "hole",
        "face": TOP,
        "position": [-30, -20],
        "standard": "simple",
        "diameter_mm": 6,
        "end_condition": "through_all",
    },
    {
        "id": "pk",
        "op": "sketch",
        "intent": "pocket outline",
        "on": TOP,
        "entities": [{"kind": "circle", "name": "c", "center": [20, 15], "diameter": 8}],
    },
]


def _started() -> Executor:
    adapter = FakeAdapter()
    adapter.connect(ConnectRequest(operation_id="connect"))
    executor = Executor(
        parse_plan({"id": "p", "kind": "part", "operations": PLAN}), adapter, Verifier(), ExecutorConfig()
    )
    executor.start()
    executor.run_to_end()
    assert executor.state == ExecutorState.completed
    return executor


def _moved(executor: Executor, op_id: str, by: tuple[float, float, float]) -> None:
    """Pretend the centre of the face was elsewhere when ``op_id`` was built: what a hole in the face does to it."""
    ctx, _ = executor._require_started()  # noqa: SLF001
    held = ctx.place_by_op[op_id]
    ctx.place_by_op[op_id] = SketchFrame(
        origin=tuple(a + b for a, b in zip(held.origin, by, strict=True)),  # type: ignore[arg-type]
        u=held.u,
        v=held.v,
        n=held.n,
    )


def _request(executor: Executor, op_id: str) -> dict[str, Any]:
    step = next(s for s in executor.trace().active_steps() if s.op_id == op_id)
    return step.attempts[-1].request


def _rebuild(executor: Executor, hold: list[str]) -> None:
    executor.hold_places(hold)
    executor.undo_to("base", by="agent", reason="edit")
    again = [op for op in PLAN if op["id"] in ("h1", "pk")]
    executor.extend(again)  # what the session does: the steps are appended again, as the plan now has them
    for _ in again:
        executor.next_operation()


def test_the_first_build_measures_from_the_centre_of_the_face() -> None:
    executor = _started()
    ctx, _ = executor._require_started()  # noqa: SLF001
    assert _request(executor, "h1")["origin"] is None and _request(executor, "pk")["origin"] is None
    assert ctx.place_by_op["h1"].origin == pytest.approx((50.0, 30.0, 8.0))
    assert ctx.place_by_op["pk"].origin == pytest.approx((50.0, 30.0, 8.0))


def test_a_held_hole_and_sketch_come_back_to_the_place_they_had() -> None:
    executor = _started()
    _moved(executor, "h1", (1.5, -0.5, 0.0))
    _moved(executor, "pk", (0.25, 0.75, 0.0))
    _rebuild(executor, ["h1", "pk"])
    assert executor.state == ExecutorState.completed, executor.trace().final_state
    assert _request(executor, "h1")["origin"] == pytest.approx([51.5, 29.5, 8.0])
    assert _request(executor, "pk")["origin"] == pytest.approx([50.25, 30.75, 8.0])
    assert executor.release_places() == {}  # they stand where they stood
    ctx, _ = executor._require_started()  # noqa: SLF001
    assert ctx.place_by_op["h1"].origin == pytest.approx((51.5, 29.5, 8.0))
    assert ctx.held_places == {}


def test_what_is_not_held_is_built_from_the_centre_of_the_face_again() -> None:
    executor = _started()
    _moved(executor, "h1", (1.5, -0.5, 0.0))
    _rebuild(executor, ["pk"])
    assert _request(executor, "h1")["origin"] is None
    assert executor.release_places() == {}


def test_a_held_place_follows_the_face_across_its_plane() -> None:
    executor = _started()
    _moved(executor, "h1", (1.5, -0.5, -3.0))  # the plate was thicker, or thinner, when it was built
    _rebuild(executor, ["h1"])
    assert _request(executor, "h1")["origin"] == pytest.approx([51.5, 29.5, 8.0])  # slid along the normal onto the face
    assert executor.release_places() == {}


def test_a_place_on_another_face_is_not_kept_and_the_distance_is_reported() -> None:
    executor = _started()
    ctx, _ = executor._require_started()  # noqa: SLF001
    held = ctx.place_by_op["h1"]
    ctx.place_by_op["h1"] = SketchFrame(origin=(52.0, 30.0, 8.0), u=held.u, v=held.v, n=(0.0, 1.0, 0.0))
    _rebuild(executor, ["h1"])
    assert _request(executor, "h1")["origin"] is None  # the face now has another orientation: only it can say
    moved = executor.release_places()
    assert set(moved) == {"h1"} and math.isclose(moved["h1"], 2.0)


def test_a_dimension_edit_in_place_after_a_held_rebuild_does_not_see_the_origin_as_a_change() -> None:
    executor = _started()
    _moved(executor, "h1", (1.5, -0.5, 0.0))
    _rebuild(executor, ["h1", "pk"])
    executor.release_places()
    operations = [{**op, "diameter_mm": 8} if op["id"] == "h1" else op for op in PLAN]
    record = executor.edit_in_place(parse_plan({"id": "p", "kind": "part", "operations": operations}), ["h1"])
    assert record.status == "applied", (record.status, record.failure)
