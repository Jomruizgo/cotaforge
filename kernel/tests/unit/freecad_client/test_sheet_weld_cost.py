# ruff: noqa: E501
"""What it costs to place the weld symbols of a welded part on views that have many lines (no FreeCAD).

A sheet of a welded part is composed several times per request; the placement of a weld symbol tries many candidate places
against every line of the view, so a view of a part with holes (every circle is a few dozen segments) turned one compose into
minutes on the real CAD (bench of 2026-11-13: 4 to 15 minutes per ``compose`` of a bracket with 4 bodies, about 30 s on the
other parts). The tests count the work instead of timing it, so they do not depend on the machine."""

import math
import random
import sys
import time
from pathlib import Path
from typing import Any

import pytest

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import items_attach as ia  # noqa: E402
import items_weld as iw  # noqa: E402
import sheet_context as sc  # noqa: E402
import sheet_model as model  # noqa: E402

from .test_sheet_weld import WELD, FrameJoints, context, drawn, front_visible  # noqa: E402

# --- the two geometry helpers keep their answers ------------------------------------------------------------------------------


def _reference_point_segment(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 < 1e-12 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length2))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def _reference_segment_distance(a: Any, b: Any, c: Any, d: Any) -> float:
    """The function as it was before it was made cheap: the answers must not change."""

    def side(p: Any, q: Any, r: Any) -> float:
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    if side(a, b, c) * side(a, b, d) < 0 and side(c, d, a) * side(c, d, b) < 0:
        return 0.0
    return min(_reference_point_segment(p, q, r) for p, q, r in ((a, c, d), (b, c, d), (c, a, b), (d, a, b)))


def _reference_hits_rect(a: Any, b: Any, rect: Any) -> bool:
    x0, y0, x1, y1 = rect
    corners = ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
    if x0 < a[0] < x1 and y0 < a[1] < y1 or x0 < b[0] < x1 and y0 < b[1] < y1:
        return True
    return any(_reference_segment_distance(a, b, corners[i], corners[(i + 1) % 4]) == 0.0 for i in range(4))


def _points(rng: random.Random, n: int) -> list[tuple[float, float]]:
    grid = [float(v) for v in range(0, 11, 5)]  # a coarse grid: touching, collinear and shared ends come up often
    return [(rng.choice(grid), rng.choice(grid)) if rng.random() < 0.5 else (rng.uniform(-2, 12), rng.uniform(-2, 12)) for _ in range(n)]


def test_the_cheap_segment_distance_gives_the_answers_of_the_one_it_replaces() -> None:
    rng = random.Random(7)
    for _ in range(4000):
        a, b, c, d = _points(rng, 4)
        if rng.random() < 0.15:
            b = a  # a point: the visible-edge test asks it that way
        assert ia._segment_distance(a, b, c, d) == pytest.approx(_reference_segment_distance(a, b, c, d), abs=1e-9)


def test_a_segment_apart_from_a_rectangle_is_rejected_by_its_box_and_the_rest_are_judged_as_before() -> None:
    rng = random.Random(11)
    rects = [(2.0, 2.0, 8.0, 6.0), (0.0, 0.0, 5.0, 5.0), (4.0, 4.0, 4.0, 9.0)]
    for _ in range(4000):
        a, b = _points(rng, 2)
        for rect in rects:
            assert ia._segment_hits_rect(a, b, rect) == _reference_hits_rect(a, b, rect), (a, b, rect)


# --- the placement of a symbol on a busy view ---------------------------------------------------------------------------------


class BusyFrame(FrameJoints):
    """The frame of 4 tubes and a plate seen from the front, with 40 round holes and threads seen end-on (24 segments each):
    the lines of a part that has features, which is what a real bracket looks like."""

    def view_projection(self, centre: Any, xdir: Any, direction: Any) -> sc.Projection:
        rng = random.Random(3)
        circles: list[model.Entity] = [model.Circle(rng.uniform(-190, 190), rng.uniform(-160, 160), rng.uniform(2.0, 6.0)) for _ in range(40)]
        return sc.Projection([*front_visible(), *circles])


def busy_context() -> sc.Context:
    ctx = context("en")
    ctx.geometry = BusyFrame()
    return ctx


def test_placing_the_weld_symbols_of_a_busy_view_does_not_test_every_line_against_every_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = [0]
    attach_distance, weld_distance = ia._segment_distance, iw._segment_distance

    def counted_attach(*args: Any) -> float:
        calls[0] += 1
        return attach_distance(*args)

    def counted_weld(*args: Any) -> float:
        calls[0] += 1
        return weld_distance(*args)

    monkeypatch.setattr(ia, "_segment_distance", counted_attach)
    monkeypatch.setattr(iw, "_segment_distance", counted_weld)
    started = time.perf_counter()
    (result,) = drawn(busy_context(), WELD)
    elapsed = time.perf_counter() - started
    assert result.status in ("ok", "warning"), result
    # the bracket of the bench made 5 million of these calls in one pass
    assert calls[0] < 400_000, calls[0]  # a segment far from the arrow is skipped by its box; the old code made 2.5 million calls here
    assert elapsed < 10.0, elapsed  # half a second here; the old code needed 10 s for this view and minutes for a real part


def test_the_symbol_goes_where_it_went_when_every_line_was_tested(monkeypatch: pytest.MonkeyPatch) -> None:
    """The saving is not a different placement: the reference functions put the symbol on the same place."""
    mine = busy_context()
    drawn(mine, WELD)
    monkeypatch.setattr(ia, "_segment_distance", _reference_segment_distance)
    monkeypatch.setattr(iw, "_segment_distance", _reference_segment_distance)
    monkeypatch.setattr(ia, "_segment_hits_rect", _reference_hits_rect)
    monkeypatch.setattr(iw, "_segment_hits_rect", _reference_hits_rect)
    reference = busy_context()
    drawn(reference, WELD)
    assert [type(e).__name__ for e in mine.sheet.entities] == [type(e).__name__ for e in reference.sheet.entities]
    assert mine.extras["segments"] == reference.extras["segments"]  # the arrows: same tips, same anchor
