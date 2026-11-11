"""Edge sets through the tools: preview with inspect, chamfer/fillet with a set, the report, edit and the errors."""

import re

import pytest

from cotaforge_mcp import recipes
from cotaforge_mcp.guide import guide
from tests.conftest import Caller, plate

_VERTICAL = {"kind": "edges", "from_op": "s2", "where": {"parallel_to": "z", "convex": True}}


def _volume(text: str) -> float:
    match = re.search(r"volume: (-?[\d.]+) mm3", text)
    assert match, text
    return float(match.group(1))


def test_inspect_previews_an_edge_set_without_changing_the_part(call: Caller) -> None:
    plate(call)
    shown = call.ok("inspect", action="edges", op="s2", where={"parallel_to": "z", "convex": True})
    assert shown.startswith("edge set (nothing was changed):")
    assert "edges: 4 edge(s) (parallel_to Z, convex)" in shown
    assert shown.count("direction (0, 0, 1)") == 4 and "length 8 mm" in shown
    assert "steps: 2" in call.ok("doc", action="status")  # still the plate: nothing ran


def test_the_preview_takes_the_last_step_when_no_op_is_named(call: Caller) -> None:
    plate(call)
    assert "12 edge(s)" in call.ok("inspect", action="edges", where={})


def test_a_bad_where_and_an_empty_set_are_actionable(call: Caller) -> None:
    plate(call)
    bad = call.err("inspect", action="edges", op="s2", where={"parallell_to": "z"})
    assert "field: where" in bad and "usage:" in bad
    empty = call.err("inspect", action="edges", op="s2", where={"parallel_to": "z", "convex": False})
    assert "matched 0 edges" in empty and "after concave: 0" in empty


def test_chamfer_on_a_set_is_one_step_with_the_edges_listed(call: Caller) -> None:
    plate(call)
    done = call.ok("op", name="chamfer", params={"edges": [_VERTICAL], "distance1_mm": 3}, id="corners")
    assert done.startswith("ok: corners chamfer")
    assert "edges[0]: 4 edge(s) (parallel_to Z, convex)" in done
    assert _volume(done) == pytest.approx(48000 - 4 * 3 * 3 / 2 * 8)


def test_fillet_on_a_set_and_the_two_shapes_of_edges_mixed(call: Caller) -> None:
    plate(call)
    done = call.ok("op", name="fillet", params={"edges": [_VERTICAL], "radius_mm": 2})
    assert _volume(done) == pytest.approx(48000 - 4 * (1 - 3.141592653589793 / 4) * 4 * 8)


def test_editing_a_chamfer_on_a_set_rebuilds_and_resolves_it_again(call: Caller) -> None:
    plate(call)
    call.ok("op", name="chamfer", params={"edges": [_VERTICAL], "distance1_mm": 3}, id="corners")
    edited = call.ok("op", edit="corners", params={"distance1_mm": 1})
    assert "rebuilt from corners" in edited and "edge set" in edited
    assert _volume(edited) == pytest.approx(48000 - 4 * 1 * 1 / 2 * 8)


def test_the_chamfer_recipe_runs_and_the_guide_prints_it(call: Caller) -> None:
    call.ok("doc", action="new", name="l_plate")
    steps = recipes.CHAMFERS
    for _comment, arguments in steps[:3]:
        call.ok("op", **arguments)
    assert "corners" in [op.id for op in call.session.executor.plan.operations]  # type: ignore[union-attr]
    call.ok("undo", to="plate")  # the alternative replaces the corner chamfer
    done = call.ok("op", **steps[3][1])
    assert "edge(s) (planar faces, curve line)" in done
    text = guide(call.session, "recipe")  # type: ignore[arg-type]
    for _comment, arguments in steps:
        assert recipes.call_text(arguments) in text
    assert "skip_failing" in text and "where=" in text
    reference = call.ok("cad_guide", topic="op:chamfer")
    assert "Edge set" in reference and "except" in reference and "except_" not in reference
    assert "skip_failing" in reference


def test_a_set_with_no_match_names_what_each_criterion_left(call: Caller) -> None:
    plate(call)
    failed = call.err(
        "op",
        name="chamfer",
        params={"edges": [{"kind": "edges", "from_op": "s2", "where": {"length_mm": 7}}], "distance1_mm": 1},
    )
    assert "matched 0 edges" in failed and "after length_mm: 0" in failed
    assert "state: unchanged" in failed
