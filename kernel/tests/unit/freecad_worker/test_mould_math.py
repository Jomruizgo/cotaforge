"""``worker/mould_math.py`` in isolation: the sides of the faces, the walls that need a decision, the parting line, the
loops, the run-off offsets and the volume balance (no FreeCAD, no numpy)."""

import importlib.util
import math
import sys
from pathlib import Path
from typing import Any

import pytest

from cotaforge.contract import DRAFT_ZERO_DEG, proposed_block_margin

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("worker_mould_math", WORKER_DIR / "mould_math.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mm = _load()


def test_the_constants_the_worker_mirrors_equal_the_contract_s() -> None:
    assert mm.ZERO_LEAN == pytest.approx(math.sin(math.radians(DRAFT_ZERO_DEG)))
    for extent in (10.0, 100.0, 400.0, 1000.0):
        assert mm.proposed_margin(extent) == proposed_block_margin(extent)


@pytest.mark.parametrize(
    ("low", "high", "side"),
    [
        (0.5, 0.5, "core"),  # looks along the pull
        (-0.5, -0.5, "cavity"),  # looks against it
        (0.0, 0.0, "zero"),  # a wall
        (-1e-5, 1e-5, "zero"),  # within 0.01 degrees
        (0.0, 0.03, "core"),  # a drafted wall that touches zero at one edge
        (-1.0, 1.0, "straddle"),  # a cylinder across the pull: the silhouette runs across it
    ],
)
def test_the_side_of_a_face_comes_from_the_signed_lean_of_its_normal(low: float, high: float, side: str) -> None:
    assert mm.side_of_leans(low, high) == side


def _shell() -> tuple[dict[str, str], dict[str, set[str]]]:
    """An open-top box: the rim and the floor-inside look along the pull, the bottom against it, four walls have no
    draft. face names: rim, floor, bottom, w1..w4 (outer walls: top = rim, bottom = bottom)."""
    raw = {"rim": "core", "bottom": "cavity", **{f"w{n}": "zero" for n in range(1, 5)}}
    neighbours = {"rim": {"w1", "w2", "w3", "w4"}, "bottom": {"w1", "w2", "w3", "w4"}}
    for n in range(1, 5):
        neighbours[f"w{n}"] = {"rim", "bottom", f"w{n % 4 + 1}", f"w{(n - 2) % 4 + 1}"}
    return raw, neighbours


def test_walls_touching_both_halves_need_the_engineer_and_are_not_guessed() -> None:
    raw, neighbours = _shell()
    sides, ambiguous = mm.resolve_sides(raw, neighbours, None)
    assert [a["faces"] for a in ambiguous] == [["w1", "w2", "w3", "w4"]]  # one group: the walls touch each other
    assert ambiguous[0]["touches"] == ["cavity", "core"]
    assert all(sides[f"w{n}"] == "zero" for n in range(1, 5))  # left as they are


@pytest.mark.parametrize("decision", ["core", "cavity"])
def test_the_decision_moves_the_parting_line_to_the_rim_or_to_the_bottom(decision: str) -> None:
    raw, neighbours = _shell()
    sides, ambiguous = mm.resolve_sides(raw, neighbours, decision)
    assert not ambiguous and all(sides[f"w{n}"] == decision for n in range(1, 5))
    edge_faces = {f"rim-w{n}": ("rim", f"w{n}") for n in range(1, 5)} | {
        f"bottom-w{n}": ("bottom", f"w{n}") for n in range(1, 5)
    }
    edge_faces |= {f"w{n}-w{n % 4 + 1}": (f"w{n}", f"w{n % 4 + 1}") for n in range(1, 5)}
    expected = sorted(f"{'rim' if decision == 'cavity' else 'bottom'}-w{n}" for n in range(1, 5))
    assert mm.parting_edges(edge_faces, sides) == expected  # the walls' own seams are never parting edges


def test_a_wall_between_two_faces_of_one_side_takes_that_side_without_asking() -> None:
    raw = {"a": "core", "b": "core", "wall": "zero", "other": "cavity"}
    neighbours = {"wall": {"a", "b"}, "a": {"wall"}, "b": {"wall"}, "other": set()}
    sides, ambiguous = mm.resolve_sides(raw, neighbours, None)
    assert not ambiguous and sides["wall"] == "core"


def test_a_decision_does_not_override_a_wall_that_has_only_one_side_around_it() -> None:
    raw = {"a": "core", "wall": "zero"}
    sides, _ = mm.resolve_sides(raw, {"wall": {"a"}, "a": {"wall"}}, "cavity")
    assert sides["wall"] == "core"


def test_a_slide_face_is_neither_halfs_and_makes_no_parting_edge() -> None:
    raw = {"a": "core", "b": "cavity", "slide": "slide"}
    assert mm.parting_edges({"ab": ("a", "b"), "as": ("a", "slide"), "bs": ("b", "slide")}, raw) == ["ab"]


def test_edges_chain_into_a_closed_loop_whatever_their_direction() -> None:
    ends = {"e1": ("a", "b"), "e2": ("c", "b"), "e3": ("c", "d"), "e4": ("a", "d")}  # a square, edges any way round
    (loop,) = mm.chain_loops(ends)
    assert loop["closed"] and not loop["branched"]
    assert [e for e, _ in loop["edges"]] == ["e1", "e2", "e3", "e4"]
    assert [forward for _, forward in loop["edges"]] == [True, False, True, False]


def test_two_separate_loops_and_an_open_chain_are_told_apart() -> None:
    ends = {
        "o1": ("a", "b"),
        "o2": ("b", "a"),  # a loop of two edges
        "i1": ("x", "y"),
        "i2": ("y", "z"),
        "i3": ("z", "x"),  # another
        "open1": ("p", "q"),  # a chain that goes nowhere
    }
    loops = mm.chain_loops(ends)
    assert sorted((len(lp["edges"]), lp["closed"]) for lp in loops) == [(1, False), (2, True), (3, True)]


def test_a_circle_is_a_loop_of_one_edge() -> None:
    (loop,) = mm.chain_loops({"c": ("v", "v")})
    assert loop["closed"] and loop["edges"] == [("c", True)]


def test_the_signed_area_tells_the_turning_direction() -> None:
    square = [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)]
    assert mm.signed_area(square) == pytest.approx(4.0)
    assert mm.signed_area(square[::-1]) == pytest.approx(-4.0)


def test_the_outward_normal_points_away_from_the_inside_for_either_turning_direction() -> None:
    assert mm.outward_normal((1.0, 0.0), True) == pytest.approx((0.0, -1.0))  # bottom edge of a ccw square: down
    assert mm.outward_normal((-1.0, 0.0), False) == pytest.approx((0.0, -1.0))  # the same edge walked the other way
    with pytest.raises(ValueError, match="no extent"):
        mm.outward_normal((0.0, 0.0), True)


def test_a_square_corner_moves_diagonally_and_a_straight_run_straight() -> None:
    assert mm.miter_offset((0.0, -1.0), (1.0, 0.0), 10.0) == pytest.approx((10.0, -10.0))
    assert mm.miter_offset((0.0, -1.0), (0.0, -1.0), 10.0) == pytest.approx((0.0, -10.0))


def test_an_edge_that_folds_back_has_no_finite_corner() -> None:
    with pytest.raises(ValueError, match="fold back"):
        mm.miter_offset((1.0, 0.0), (-1.0, 0.0), 10.0)


def test_the_balance_is_the_relative_gap_between_the_block_and_its_pieces() -> None:
    assert mm.volume_balance(1000.0, [600.0, 400.0]) == 0.0
    assert mm.volume_balance(1000.0, [600.0, 399.0]) == pytest.approx(0.001)
    assert mm.volume_balance(0.0, [1.0]) == math.inf


def test_the_components_are_sorted_and_deterministic() -> None:
    nodes = {"b", "a", "d", "c"}
    assert mm.components(nodes, {"a": {"b"}, "b": {"a"}, "c": {"d"}, "d": {"c"}}) == [["a", "b"], ["c", "d"]]
