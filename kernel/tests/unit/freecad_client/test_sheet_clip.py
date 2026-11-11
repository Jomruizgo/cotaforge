"""Plane geometry of the derived views (``worker/sheet_clip.py``): placing, clipping and hatching, without FreeCAD."""

import math
import sys
from pathlib import Path

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import sheet_clip as clip  # noqa: E402
import sheet_model as model  # noqa: E402


def test_place_scales_and_moves_every_kind_of_entity() -> None:
    placed = clip.place(
        [model.Line(0, 0, 1, 1), model.Circle(1, 2, 3), model.Arc(0, 0, 2, 0, 90), model.Polyline(((0, 0), (1, 0)))],
        10.0,
        20.0,
        2.0,
    )
    assert placed[0] == model.Line(10, 20, 12, 22)
    assert placed[1] == model.Circle(12, 24, 6)
    assert placed[2] == model.Arc(10, 20, 4, 0, 90)
    assert placed[3].points == ((10, 20), (12, 20))


def test_relayer_keeps_the_geometry() -> None:
    out = clip.relayer([model.Line(0, 0, 1, 0, model.CONTOUR, 0.5)], model.HIDDEN)
    assert out == [model.Line(0, 0, 1, 0, model.HIDDEN, 0.5)]


def test_a_line_through_a_circle_is_cut_at_the_circle() -> None:
    (piece,) = clip.clip_to_circle([model.Line(-10, 0, 10, 0)], 0, 0, 5)
    assert isinstance(piece, model.Line)
    assert math.isclose(piece.x1, -5) and math.isclose(piece.x2, 5)


def test_what_lies_wholly_inside_is_kept_and_what_lies_outside_is_dropped() -> None:
    inside = model.Circle(1, 1, 1)
    out = clip.clip_to_circle([inside, model.Line(20, 20, 30, 30), model.Line(-1, 0, 1, 0)], 0, 0, 5)
    assert out[0] is inside
    assert len(out) == 2


def test_a_circle_crossing_the_boundary_becomes_an_arc_polyline_inside() -> None:
    out = clip.clip_to_circle([model.Circle(5, 0, 3)], 0, 0, 4)
    assert out and all(isinstance(e, model.Polyline) for e in out)
    for e in out:
        assert all(math.hypot(x, y) <= 4 + 1e-6 for x, y in e.points)


def test_clip_to_rect_cuts_lines_and_keeps_the_inside() -> None:
    out = clip.clip_to_rect([model.Line(-5, 5, 15, 5), model.Circle(5, 5, 1)], (0, 0, 10, 10))
    assert isinstance(out[0], model.Line) and (out[0].x1, out[0].x2) == (0.0, 10.0)
    assert out[1] == model.Circle(5, 5, 1)


def _area(lines: list[model.Line]) -> float:
    return sum(math.hypot(line.x2 - line.x1, line.y2 - line.y1) for line in lines)


def test_hatch_fills_a_square_at_45_degrees() -> None:
    square = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    lines = clip.hatch([square], 1.0)
    assert lines and all(line.layer == model.HATCH for line in lines)
    for line in lines:
        assert math.isclose(abs(line.y2 - line.y1), abs(line.x2 - line.x1), abs_tol=1e-6)
        for x, y in ((line.x1, line.y1), (line.x2, line.y2)):
            assert -1e-6 <= x <= 10 + 1e-6 and -1e-6 <= y <= 10 + 1e-6
    # lines 1 mm apart across a 10 x 10 square: total length is area / spacing
    assert math.isclose(_area(lines), 100.0, rel_tol=0.02)


def test_hatch_leaves_a_hole_empty() -> None:
    outer = [(0.0, 0.0), (20.0, 0.0), (20.0, 20.0), (0.0, 20.0)]
    hole = [(8.0, 8.0), (12.0, 8.0), (12.0, 12.0), (8.0, 12.0)]
    full = _area(clip.hatch([outer], 1.0))
    holed = _area(clip.hatch([outer, hole], 1.0))
    assert math.isclose(full - holed, 16.0, rel_tol=0.05)
    for line in clip.hatch([outer, hole], 1.0):
        mx, my = (line.x1 + line.x2) / 2, (line.y1 + line.y2) / 2
        assert not (8.05 < mx < 11.95 and 8.05 < my < 11.95)


def test_hatch_lines_sit_on_a_page_grid() -> None:
    squares = ([(0.0, 0.0), (5.0, 0.0), (5.0, 5.0), (0.0, 5.0)], [(20.3, 1.1), (25.3, 1.1), (25.3, 6.1), (20.3, 6.1)])
    for square in squares:
        for line in clip.hatch([square], 2.0):
            across = (line.y1 - line.x1) / math.sqrt(2)  # distance of the line from the origin, across the hatch
            assert math.isclose(across / 2.0, round(across / 2.0), abs_tol=1e-4)


def test_hatch_spacing_grows_with_the_area_within_the_iso_range() -> None:
    assert clip.hatch_spacing(10, 10) == clip.MIN_HATCH_MM
    assert clip.hatch_spacing(500, 500) == clip.MAX_HATCH_MM
    assert clip.MIN_HATCH_MM < clip.hatch_spacing(60, 40) < clip.MAX_HATCH_MM
