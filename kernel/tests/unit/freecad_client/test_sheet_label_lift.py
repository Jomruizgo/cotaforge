"""The label TechDraw's own linear dimensions are given (``sheet_draw.lifted_label``): no FreeCAD."""

import math
import sys
from pathlib import Path

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import sheet_draw as draw  # noqa: E402


def test_the_native_label_puts_the_line_of_a_dimension_where_the_sheet_draws_it() -> None:
    """TechDraw sets the dimension line ``LABEL_LIFT_MM`` off the label it is given (measured: 6.5 mm from the outline
    for the 10 mm the sheet drew): the label goes to the side of the text, so the line lands where it is drawn."""
    x, y = draw.native_label("h", 50.0, 170.0)
    assert x == 50.0 and math.isclose(y - draw.LABEL_LIFT_MM, 170.0)  # the text is above a horizontal line
    x, y = draw.native_label("v", 90.0, 120.0)  # the middle of the dimension and its line
    assert y == 90.0 and math.isclose(x + draw.LABEL_LIFT_MM, 120.0)  # ... and left of a vertical one


def test_the_label_of_a_native_dimension_is_lifted_on_the_side_of_its_text() -> None:
    assert draw.lifted_label("h", (25.0, 10.0)) == (25.0, 10.0 + draw.LABEL_LIFT_MM)
    assert draw.lifted_label("v", (10.0, 25.0)) == (10.0 - draw.LABEL_LIFT_MM, 25.0)
    assert draw.native_label("h", 25.0, 10.0) == draw.lifted_label("h", (25.0, 10.0))
    assert draw.native_label("v", 25.0, 10.0) == draw.lifted_label("v", (10.0, 25.0))
    assert math.isclose(draw.LABEL_LIFT_MM, 3.5)  # measured on the sheet TechDraw printed
