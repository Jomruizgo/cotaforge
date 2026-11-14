# ruff: noqa: E501
"""A distance between the two ends of one circle is a diameter and carries ``Ø`` (the cable entry bore of the junction box,
seen from the side, was dimensioned ``20,5 +0,3/0`` between two points of its wall: a width to the reader). No FreeCAD."""

import math
import sys
from pathlib import Path

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import sheet_context as sc  # noqa: E402

from .test_sheet_items import PlateGeometry, draw, make_context  # noqa: E402


class BoreGeometry(PlateGeometry):
    """The test plate with a conical bore along x at (y, z) = (30, 4): radius 5 at x = 100 and 4.7 at x = 95 (draft)."""

    def circle_span(self, a: sc.Vec3, b: sc.Vec3) -> float | None:
        if abs(a[0] - b[0]) > 1e-6 or abs(a[1] + b[1] - 60.0) > 1e-6 or abs(a[2] - 4.0) > 1e-6 or abs(b[2] - 4.0) > 1e-6:
            return None
        radius = abs(a[1] - 30.0)
        return 2 * radius if abs(radius - (5.0 - 0.06 * (100.0 - a[0]))) < 1e-6 else None

    def on_part(self, p: sc.Vec3) -> sc.Vec3 | None:
        return p if abs(p[0] - 100.0) < 0.05 or abs(p[0] - 95.0) < 0.05 else None


def context() -> sc.Context:
    ctx = make_context(holes=[])
    ctx.geometry = BoreGeometry()
    return ctx


def test_a_distance_between_the_two_ends_of_a_bore_is_a_diameter_with_its_o() -> None:
    ctx = context()
    (result,) = draw(
        ctx, {"type": "dim", "kind": "distance", "from": {"at": [95.0, 25.3, 4.0]}, "to": {"at": [95.0, 34.7, 4.0]}, "along": "y", "view": "right", "side": "below"}
    )
    assert result.status == "ok", result.detail
    assert "Ø9,4" in ctx.sheet.texts(), ctx.sheet.texts()  # 2 x 4.7: the diameter, never the bare 9,4 of a width


def test_the_tolerance_of_a_bore_keeps_the_o() -> None:
    ctx = context()
    (result,) = draw(
        ctx,
        {"type": "dim", "kind": "distance", "from": {"at": [95.0, 25.3, 4.0]}, "to": {"at": [95.0, 34.7, 4.0]}, "along": "y", "view": "right", "side": "below", "tol": [0.3, 0.0]},
    )
    assert result.status == "ok", result.detail
    assert any(t.startswith("Ø9,4") for t in ctx.sheet.texts()), ctx.sheet.texts()


def test_the_native_dimension_of_a_bore_prints_the_o_too() -> None:
    """What TechDraw writes is the native object's text: a plain distance would print the number alone, so the diameter hands
    it its text."""
    ctx = context()
    draw(ctx, {"id": "d1", "type": "dim", "kind": "distance", "from": {"at": [95.0, 25.3, 4.0]}, "to": {"at": [95.0, 34.7, 4.0]}, "along": "y", "view": "right", "side": "below"})
    (native,) = ctx.extras["natives"]["d1"]
    assert native.text == "Ø9,4"


def test_a_width_between_two_points_that_are_not_a_circle_has_no_o() -> None:
    ctx = context()
    draw(ctx, {"id": "w1", "type": "dim", "kind": "distance", "from": {"at": [100.0, 10.0, 4.0]}, "to": {"at": [100.0, 50.0, 4.0]}, "along": "y", "view": "right", "side": "below"})
    assert not any("Ø" in t for t in ctx.sheet.texts()), ctx.sheet.texts()
    (native,) = ctx.extras["natives"]["w1"]
    assert native.text is None  # TechDraw prints the measured width itself


def test_the_chord_must_be_the_dimension_itself() -> None:
    """Two points of a circle that are not opposite (a chord off the axis) are not its diameter."""
    ctx = context()
    assert ctx.geometry.circle_span((95.0, 25.3, 4.0), (95.0, 30.0, 4.0)) is None
    assert math.isclose(ctx.geometry.circle_span((95.0, 25.3, 4.0), (95.0, 34.7, 4.0)) or 0.0, 9.4)
