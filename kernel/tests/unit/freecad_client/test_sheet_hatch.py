# ruff: noqa: E501
"""The hatching of a section with several bodies, on synthetic cut areas: the plan, which bodies touch, the review, and the
lines the section draws for each body."""

import math
import sys
from pathlib import Path

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import items_views as views  # noqa: E402
import pytest  # noqa: E402
import sheet_context as sc  # noqa: E402
import sheet_hatch as sh  # noqa: E402
import sheet_model as model  # noqa: E402
import sheet_review as review  # noqa: E402
import sheet_viewchecks as checks  # noqa: E402


def square(x: float, y: float, size: float = 20.0) -> list[tuple[float, float]]:
    return [(x, y), (x + size, y), (x + size, y + size), (x, y + size)]


# a plate (0..60 x 0..10) with a boss on it (20..40 x 10..40) and a rib to the right (40..55 x 10..30)
PLATE = [[(0.0, 0.0), (60.0, 0.0), (60.0, 10.0), (0.0, 10.0)]]
BOSS = [[(20.0, 10.0), (40.0, 10.0), (40.0, 40.0), (20.0, 40.0)]]
RIB = [[(40.0, 10.0), (55.0, 10.0), (55.0, 30.0), (40.0, 30.0)]]
FAR = [[(100.0, 100.0), (110.0, 100.0), (110.0, 110.0), (100.0, 110.0)]]


# --- which bodies touch --------------------------------------------------------------------------------------------------------


def test_bodies_whose_cut_areas_meet_are_neighbours() -> None:
    found = sh.touching_bodies({"plate": PLATE, "boss": BOSS, "rib": RIB, "far": FAR})
    assert sorted(found) == [("boss", "rib"), ("plate", "boss"), ("plate", "rib")]


def test_a_body_apart_from_the_others_touches_none() -> None:
    assert sh.touching_bodies({"a": PLATE, "far": FAR}) == []


# --- the plan --------------------------------------------------------------------------------------------------------------------


def test_touching_bodies_get_other_angles_and_never_share_a_style() -> None:
    touching = [("plate", "boss"), ("plate", "rib"), ("boss", "rib")]
    plan = sh.plan(["plate", "boss", "rib"], touching)
    assert plan["plate"] == (0.0, 1.0) and plan["boss"] == (90.0, 1.0)  # 45 degrees and 135 degrees
    assert len({plan["plate"], plan["boss"], plan["rib"]}) == 3
    assert plan["rib"] not in (plan["plate"], plan["boss"])


def test_bodies_that_do_not_touch_may_share_a_style() -> None:
    plan = sh.plan(["a", "far"], [])
    assert plan["a"] == plan["far"] == (0.0, 1.0)


def test_a_style_that_was_asked_for_is_kept_and_the_others_work_round_it() -> None:
    plan = sh.plan(["plate", "boss"], [("plate", "boss")], {"plate": (90.0, 1.0)})
    assert plan["plate"] == (90.0, 1.0) and plan["boss"] == (0.0, 1.0)
    assert sh.plan(["a"], [], {"ghost": (45.0, 1.0)}) == {
        "a": (0.0, 1.0)
    }  # a body the section does not have is ignored


# --- the review -------------------------------------------------------------------------------------------------------------------


def fact(
    body: str, pattern: str = "ANSI31", turn: float = 0.0, spacing: float = 1.0, material: str = "S355"
) -> sh.HatchFact:
    return sh.HatchFact(body, material, pattern, turn, spacing)


def test_two_adjacent_bodies_with_the_same_hatch_are_told_to_turn_one() -> None:
    found = sh.hatch_findings([fact("plate"), fact("boss")], [("plate", "boss")])
    assert found == [sh.HatchFinding("hatch_same_as_neighbour", ("plate", "boss"), 90.0)]


def test_a_hatch_turned_half_a_turn_is_the_same_hatch() -> None:
    assert sh.hatch_findings([fact("a", turn=0.0), fact("b", turn=180.0)], [("a", "b")])


def test_neighbours_that_differ_by_angle_or_by_spacing_are_quiet() -> None:
    assert sh.hatch_findings([fact("a"), fact("b", turn=90.0)], [("a", "b")]) == []
    assert sh.hatch_findings([fact("a"), fact("b", spacing=2.0)], [("a", "b")]) == []


def test_bodies_that_do_not_touch_may_have_the_same_hatch() -> None:
    assert sh.hatch_findings([fact("a"), fact("b")], []) == []


def test_two_bodies_of_one_material_with_different_patterns_are_reported() -> None:
    found = sh.hatch_findings([fact("a"), fact("b", pattern="ANSI32", turn=90.0)], [("a", "b")])
    assert [f.code for f in found] == ["hatch_pattern_differs"]


def test_different_materials_may_have_different_patterns_and_unknown_materials_are_not_judged() -> None:
    assert sh.hatch_findings([fact("a"), fact("b", pattern="ANSI32", material="AISI 304")], []) == []
    assert sh.hatch_findings([fact("a", material=""), fact("b", pattern="ANSI32", material="")], []) == []


# --- the lines of the section -----------------------------------------------------------------------------------------------------


def angles(lines: list[object]) -> set[int]:
    return {round(math.degrees(math.atan2(ln.y2 - ln.y1, ln.x2 - ln.x1))) % 180 for ln in lines}  # type: ignore[attr-defined]


def test_each_body_is_hatched_at_its_own_angle_and_spacing() -> None:
    styles = sh.plan(["body 1", "body 2"], [("body 1", "body 2")])
    first = views.hatch_bodies({"body 1": PLATE}, styles, 2.0)
    second = views.hatch_bodies({"body 2": BOSS}, styles, 2.0)
    assert angles(first) == {45} and angles(second) == {135}
    both = views.hatch_bodies({"body 1": PLATE, "body 2": BOSS}, styles, 2.0)
    assert len(both) == len(first) + len(second)


def test_a_wider_spacing_draws_fewer_lines() -> None:
    dense = views.hatch_bodies({"a": PLATE}, {"a": (0.0, 1.0)}, 2.0)
    sparse = views.hatch_bodies({"a": PLATE}, {"a": (0.0, 2.0)}, 2.0)
    assert 1.6 < len(dense) / len(sparse) < 2.4


def test_the_loops_of_a_cut_are_grouped_by_the_body_that_owns_them() -> None:
    groups = views._bodies_of_cut([*PLATE, *BOSS, *RIB], [0, 1, 1])  # noqa: SLF001
    assert list(groups) == ["body 1", "body 2"] and len(groups["body 2"]) == 2
    assert list(views._bodies_of_cut(PLATE, [])) == ["body 1"]  # noqa: SLF001 - a projection that names no owner: one body


def test_the_hatch_option_takes_a_turn_and_a_spacing_and_nothing_else() -> None:
    assert views.check_section({"holes": "all", "hatch": {"body 2": {"turn": 90, "spacing": 1.5}}})["hatch"] == {
        "body 2": (90.0, 1.5)
    }
    for bad in ({"body 2": {"pattern": "ANSI32"}}, {"body 2": {}}, {}, {"body 2": {"spacing": 0}}, [1]):
        with pytest.raises(sc.ItemError):
            views.check_section({"holes": "all", "hatch": bad})


# --- the review of the sheet ---------------------------------------------------------------------------------------------------------


def review_of(facts: list[sh.HatchFact], touching: list[tuple[str, str]]) -> list[object]:
    ctx = sc.Context(
        sheet=model.Sheet(420.0, 297.0), views={}, holes=[], geometry=sc.Geometry(), area=(0.0, 0.0, 420.0, 297.0)
    )
    ctx.extras["hatches"] = {"A-A": (facts, touching)}
    return checks._hatches(review.ReviewInput([], {}, ctx, ctx.sheet))  # noqa: SLF001


def test_the_review_names_the_two_bodies_hatched_alike_and_the_other_angle() -> None:
    (found,) = review_of([fact("body 1"), fact("body 2")], [("body 1", "body 2")])
    assert found.code == "hatch_same_as_neighbour" and "body 1" in found.message and "body 2" in found.message  # type: ignore[attr-defined]
    assert "90" in found.message and "nunca por el patrón" in found.message  # type: ignore[attr-defined]
    assert found.severity == "warning"  # type: ignore[attr-defined]


def test_the_review_is_quiet_when_the_bodies_were_given_other_angles() -> None:
    assert review_of([fact("body 1"), fact("body 2", turn=90.0)], [("body 1", "body 2")]) == []
