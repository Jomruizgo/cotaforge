# ruff: noqa: E501
"""The text block TechDraw painted for a note line is found by what it says (no FreeCAD): the junction box sheet had two lines of
its notes moved out of the frame because another text (a dimension) stood at their height when they were measured."""

import sys
from pathlib import Path

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import sheet_notefit as fit  # noqa: E402

LINE = "    rebaba ≤ 0,1, nunca hacia el labio."
DIMENSION = fit.TextBox("47,5", left=113.6, width=0.8, height=12.0, cx=114.0, cy=191.4)  # the text of a dimension, upright, at the line's height
OWN = fit.TextBox(LINE, left=27.0, width=175.0, height=8.7, cx=114.5, cy=191.4)


def test_a_line_whose_item_is_not_painted_yet_is_not_measured_as_another_text() -> None:
    assert fit.pick_block([DIMENSION], 114.5, 191.4, LINE) is None  # it used to answer (113.6, 0.8, 12.0): the line was moved 87 mm out of the frame
    assert fit.pick_block([DIMENSION], 114.5, 191.4) == (113.6, 0.8, 12.0)  # the lookup by position alone, which is what went wrong


def test_a_line_is_found_by_what_it_says_whatever_the_indentation() -> None:
    assert fit.pick_block([DIMENSION, OWN], 114.5, 191.4, "rebaba ≤ 0,1, nunca hacia el labio.") == (27.0, 175.0, 8.7)
    assert fit.pick_block([DIMENSION, OWN], 114.5, 191.4, LINE) == (27.0, 175.0, 8.7)


def test_the_estimate_of_the_place_may_be_far_off_when_the_line_is_named() -> None:
    far = fit.TextBox(LINE, left=-60.0, width=175.0, height=8.7, cx=27.5, cy=191.4)  # painted where the wrong X had put it
    assert fit.pick_block([far], 27.4, 191.4, LINE) == (-60.0, 175.0, 8.7)
    assert fit.pick_block([far], 80.0, 191.4, LINE) == (-60.0, 175.0, 8.7)  # 52 mm off the estimate: still that line
    assert fit.pick_block([far], 80.0, 191.4) is None  # unnamed: 8 mm either side of the estimate only


def test_another_height_is_another_line() -> None:
    below = fit.TextBox(LINE, left=27.0, width=175.0, height=8.7, cx=114.5, cy=186.4)
    assert fit.pick_block([below], 114.5, 191.4, LINE) is None


def test_a_line_that_is_not_where_the_sheet_wants_it_is_reported_with_how_far() -> None:
    assert fit.misplaced(27.0, (27.2, 175.0, 8.7), 0.5) is None
    assert fit.misplaced(27.0, None, 0.5) is None  # not measured: nothing to judge (headless)
    off = fit.misplaced(27.0, (-60.5, 175.0, 8.7), 0.5)
    assert off is not None and round(off, 1) == -87.5

