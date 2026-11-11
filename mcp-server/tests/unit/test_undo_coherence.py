"""Undo must keep the executor, the adapter's history and the server's step list in agreement."""

import math
import re

import pytest
from cotaforge.contract import Empty, MassRequest

from tests.conftest import Caller


def volume(call: Caller) -> float:
    return call.session.adapter.mass_properties(MassRequest(operation_id="v")).volume_mm3


def feature_count(call: Caller) -> int:
    return len(call.session.adapter.feature_tree(Empty(operation_id="t")).nodes)


def top_face(from_op: str) -> dict:
    return {"kind": "face", "from_op": from_op, "select": {"normal": [0, 0, 1]}}


def square(call: Caller, op_id: str, on: dict, center: list[float], side: float) -> None:
    call.ok(
        "op",
        name="sketch",
        id=op_id,
        params={"on": on, "entities": [{"kind": "rect", "name": "r", "center": center, "width": side, "height": side}]},
    )


def test_box_cut_fillet_undo_twice_then_extrude(call: Caller) -> None:
    call.ok("doc", action="new", name="mixed")
    call.ok("op", name="box", id="b", params={"size": [40, 30, 10]})
    assert volume(call) == pytest.approx(12000)
    square(call, "pocket_sk", top_face("b"), [0, 0], 10)
    call.ok("op", name="cut", id="pocket", params={"sketch": "pocket_sk", "depth_mm": 4})
    assert volume(call) == pytest.approx(12000 - 10 * 10 * 4)
    side = {"kind": "face", "from_op": "b", "select": {"normal": [1, 0, 0]}}
    call.ok(
        "op",
        name="fillet",
        id="round",
        params={"edges": [{"kind": "edge", "between": [top_face("b"), side]}], "radius_mm": 2},
    )
    rounded = volume(call)
    assert rounded < 11600 - 1

    assert call.ok("undo").startswith("undone: round fillet")
    assert volume(call) == pytest.approx(11600)
    assert call.ok("undo").startswith("undone: pocket cut")
    assert volume(call) == pytest.approx(12000)
    assert [s.id for s in call.session.steps()] == ["b", "pocket_sk"]
    assert feature_count(call) == 1  # only the box is left in the adapter's history

    square(call, "boss_sk", top_face("b"), [10, 0], 8)
    after = call.ok("op", name="extrude", id="boss", params={"sketch": "boss_sk", "depth_mm": 5})
    assert volume(call) == pytest.approx(12000 + 8 * 8 * 5)
    assert "(+320)" in after
    assert [s.id for s in call.session.steps()] == ["b", "pocket_sk", "boss_sk", "boss"]
    assert feature_count(call) == 2


def test_undo_to_start_and_rebuild_with_the_same_ids(call: Caller) -> None:
    call.ok("doc", action="new", name="redo")
    for size in ([10, 10, 10], [20, 20, 20]):
        call.ok("op", name="box", id="a", params={"size": size})
        assert volume(call) == pytest.approx(math.prod(size))
        call.ok("undo", to="start")
        assert volume(call) == pytest.approx(0)
        assert call.session.steps() == []
        assert feature_count(call) == 0


def test_failed_step_in_the_middle_does_not_break_a_later_undo(call: Caller) -> None:
    call.ok("doc", action="new", name="mid")
    call.ok("op", name="box", id="b", params={"size": [10, 10, 10]})
    call.err(
        "op",
        name="fillet",
        params={
            "edges": [
                {
                    "kind": "edge",
                    "of": {"kind": "face", "from_op": "b", "select": {"normal": [0, 1, 1]}},
                    "curve": "line",
                }
            ],
            "radius_mm": 1,
        },
    )
    call.ok("op", name="box", id="c", params={"size": [5, 5, 5]})
    assert re.search(r"undone: c box", call.ok("undo"))
    assert [s.id for s in call.session.steps()] == ["b"]
    assert volume(call) == pytest.approx(1000)
    assert "undone: b box" in call.ok("undo")
    assert volume(call) == pytest.approx(0)


def test_ids_of_undone_steps_are_free_and_live_ids_are_not(call: Caller) -> None:
    call.ok("doc", action="new", name="ids")
    call.ok("op", name="box", id="one", params={"size": [1, 1, 1]})
    call.ok("op", name="box", params={"size": [1, 1, 1]})  # s1
    call.ok("undo")
    assert call.ok("op", name="box", params={"size": [1, 1, 1]}).startswith("ok: s2 box")
    assert "already used" in call.err("op", name="box", id="one", params={"size": [1, 1, 1]})


def test_undo_goes_deeper_than_twenty_steps(call: Caller) -> None:
    call.ok("doc", action="new", name="deep")
    for index in range(40):
        call.ok("op", name="box", id=f"b{index}", params={"size": [1, 1, 1]})
    assert volume(call) == pytest.approx(40)
    back = call.ok("undo", to="b4")
    assert "steps now: 5" in back and volume(call) == pytest.approx(5)
    assert call.ok("undo", to="start").count("b") >= 5
    assert volume(call) == pytest.approx(0) and feature_count(call) == 0
