# ruff: noqa: E501
"""What a weldment asks of its designer (``sheet_weldplan.py``) on canned facts, and the paper a sheet gets when nobody chose one
(``sheet_layout.auto_paper``: the smallest paper on which the drawing is legible, never a bigger one only for a bigger scale).
No FreeCAD."""

import sys
from pathlib import Path

import pytest

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import sheet_context as sc  # noqa: E402
import sheet_joints as sj  # noqa: E402
import sheet_layout as layout  # noqa: E402
import sheet_weldplan as wp  # noqa: E402
from sheet_weldment import BodyFact, group_bodies  # noqa: E402

TUBE = BodyFact("body:1", 177600.0, 119288.0, 16, (400.0, 40.0, 40.0))  # 40 x 40 x 3, 400 long
BAR = BodyFact("body:2", 150000.0, 56600.0, 6, (500.0, 50.0, 6.0))  # flat bar 50 x 6
PLATE = BodyFact("body:5", 6 * 340 * 340.0, 2 * 340 * 340.0 + 4 * 6 * 340, 6, (340.0, 340.0, 6.0))


def test_a_hollow_body_is_measured_as_a_tube_with_its_wall() -> None:
    assert wp.section_of(TUBE) == {"kind": "tube", "section_mm": [40.0, 40.0], "wall_mm": 3.0}


def test_a_long_solid_body_is_a_bar_and_a_short_one_a_plate() -> None:
    assert wp.section_of(BAR) == {"kind": "bar", "section_mm": [50.0, 6.0], "wall_mm": 0.0}
    assert wp.section_of(PLATE) == {"kind": "plate", "section_mm": [6.0], "wall_mm": 0.0}


def tee(tube: str) -> sj.Joint:
    ring = ((0.0, 0.0, 0.0), (0.0, 40.0, 0.0), (0.0, 40.0, 40.0), (0.0, 0.0, 40.0), (0.0, 0.0, 0.0))
    return sj.Joint((tube, "body:5"), "tee", (ring,), (3.0, 6.0), 1600.0)


def test_the_plan_lists_the_bodies_with_their_quantity_and_the_groups_of_joints_with_the_proposal() -> None:
    four = [BodyFact(f"body:{n}", 177600.0, 119288.0, 16, (400.0, 40.0, 40.0)) for n in (1, 2, 3, 4)]
    assembly = group_bodies([*four, PLATE], "es")
    assert assembly is not None
    joints = [tee(f"body:{n}") for n in (1, 2, 3, 4)]
    done = wp.plan_of(assembly.groups, joints, frozenset({"1"}), {"body:1+body:5"})
    first, second = done["bodies"]
    assert first["instances"] == ["body:1", "body:2", "body:3", "body:4"] and first["kind"] == "tube" and first["named"] is True
    assert second["kind"] == "plate" and second["named"] is False
    (group,) = done["joints"]
    assert group["pairs"] == [["body:1", "body:5"], ["body:2", "body:5"], ["body:3", "body:5"], ["body:4", "body:5"]]
    assert (group["weld_kind"], group["size_mm"], group["thin_mm"], group["all_around"]) == ("fillet", 3.0, 3.0, True)
    assert group["welded"] is False  # only one of the four has a symbol


def test_a_group_is_welded_when_every_joint_has_a_symbol() -> None:
    assembly = group_bodies([TUBE, PLATE], "es")
    assert assembly is not None
    done = wp.plan_of(assembly.groups, [tee("body:1")], frozenset(), {"body:1+body:5"})
    assert done["joints"][0]["welded"] is True


def test_the_context_keeps_the_measured_body_of_each_position() -> None:
    assembly = group_bodies([TUBE, PLATE], "es")
    assert assembly is not None and all(isinstance(g, sc.PartGroup) and g.measured is not None for g in assembly.groups)


# --- the paper -------------------------------------------------------------------------------------------------------------------


def test_a_frame_takes_the_smallest_paper_where_it_is_legible_not_a_bigger_one_for_its_scale() -> None:
    dims = (406.0, 340.0, 340.0)
    assert layout.auto_paper(*dims, True, True, True, iso_dims=dims) == ("A3", 0.2)  # front, top, right and isometric views
    # front, right and isometric: A2 would draw it at 1:2 (what the sheet got before), two papers up only to reach a bigger scale
    paper, scale = layout.auto_paper(*dims, True, False, True, iso_dims=dims)
    assert (paper, scale) == ("A4", 0.2)
    assert layout.plan(*dims, *layout.PAGES_MM["A2"], None, None, True, False, True, iso_dims=dims).scale == 0.5


def test_a_small_part_is_not_drawn_at_the_floor_scale_when_a_bigger_paper_reaches_a_legible_size() -> None:
    dims = (300.0, 200.0, 100.0)  # at 1:5 its largest side would be 60 mm on A4
    assert layout.auto_paper(*dims, True, True, True, iso_dims=dims) == ("A3", 0.5)
    assert max(dims) * layout.LEGIBLE_SCALE < layout.LEGIBLE_EXTENT_MM


@pytest.mark.parametrize(
    ("dims", "expected"),
    [((100.0, 60.0, 8.0), ("A3", 1.0)), ((20.0, 20.0, 20.0), ("A4", 2.0))],
)
def test_what_reaches_full_size_keeps_its_paper(dims: tuple[float, float, float], expected: tuple[str, float]) -> None:
    assert layout.auto_paper(*dims, True, True, True, iso_dims=dims) == expected


# --- where the members stand on the plate ----------------------------------------------------------------------------------------


def frame_facts() -> tuple[list[sc.PartGroup], list[sj.Joint], dict[str, tuple[tuple[float, float, float], tuple[float, float, float]]]]:
    four = [BodyFact(f"body:{n}", 177600.0, 119288.0, 16, (400.0, 40.0, 40.0)) for n in (1, 2, 3, 4)]
    assembly = group_bodies([*four, PLATE], "es")
    assert assembly is not None
    boxes = {f"body:{n}": ((0.0, y, z), (400.0, y + 40.0, z + 40.0)) for n, (y, z) in zip((1, 2, 3, 4), ((0, 0), (300, 0), (0, 300), (300, 300)), strict=True)}
    boxes["body:5"] = ((-6.0, 0.0, 0.0), (0.0, 340.0, 340.0))
    return assembly.groups, [tee(f"body:{n}") for n in (1, 2, 3, 4)], boxes


def test_the_plan_says_where_the_members_stand_on_the_one_plate() -> None:
    groups, joints, boxes = frame_facts()
    placement = wp.plan_of(groups, joints, frozenset(), set(), boxes)["placement"]
    assert placement["base"] == "body:5" and placement["base_box"] == boxes["body:5"]
    assert [m["id"] for m in placement["members"]] == ["body:1", "body:2", "body:3", "body:4"]
    assert placement["members"][1]["box"] == ((0.0, 300.0, 0.0), (400.0, 340.0, 40.0))


def test_there_is_no_placement_without_one_plate_or_without_the_boxes() -> None:
    groups, joints, boxes = frame_facts()
    assert wp.plan_of(groups, joints, frozenset(), set())["placement"] is None  # a geometry that cannot say
    two = [BodyFact("body:5", 6 * 340 * 340.0, 2 * 340 * 340.0 + 4 * 6 * 340, 6, (340.0, 340.0, 6.0)), BodyFact("body:6", 6 * 300 * 300.0, 2 * 300 * 300.0 + 4 * 6 * 300, 6, (300.0, 300.0, 6.0))]
    assembly = group_bodies(two, "es")
    assert assembly is not None
    assert wp.placement_of(assembly.groups, [], {**boxes, "body:6": boxes["body:5"]}) is None  # two plates: which one is the base?
