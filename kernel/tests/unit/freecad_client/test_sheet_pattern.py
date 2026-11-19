# ruff: noqa: E501
"""Circular hole patterns (``sheet_pattern``, ``items_pattern``): found on the part, drawn with their pitch circle and radial
centre lines, dimensioned by the pitch circle diameter and the angle of the first hole (ISO 129-1) and never by X / Y. Hand-made
contexts, no FreeCAD: a round flange seen from above, a plate with a pattern around a bore, the four holes of a rectangle."""

import math
import sys
from pathlib import Path
from typing import Any

import pytest

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import sheet_context as sc  # noqa: E402
import sheet_items as items  # noqa: E402
import sheet_model as model  # noqa: E402
import sheet_pattern as sp  # noqa: E402
import sheet_plan_bridge as bridge  # noqa: E402
import sheet_quality as quality  # noqa: E402
import sheet_review as review  # noqa: E402

DOWN = (0.0, 0.0, -1.0)
EXTRA = {"thread": ("M8", 12.0)}


def hole(index: int, x: float, y: float, d: float = 6.8, thread: tuple[str, float | None] | None = ("M8", 12.0)) -> sc.HoleInfo:
    return sc.HoleInfo(index, (x, y, 12.0), DOWN, d, 12.0, True, (f"face:h{index}",), thread, True)


def ring(n: int, radius: float, first: float = 0.0, centre: tuple[float, float] = (0.0, 0.0), start: int = 1, **kw: Any) -> list[sc.HoleInfo]:
    out = []
    for i in range(n):
        a = math.radians(first + 360.0 * i / n)
        out.append(hole(start + i, centre[0] + radius * math.cos(a), centre[1] + radius * math.sin(a), **kw))
    return out


def bore(index: int = 0, x: float = 0.0, y: float = 0.0, d: float = 42.0) -> sc.HoleInfo:
    return hole(index, x, y, d, None)


def context(holes: list[sc.HoleInfo], centre: tuple[float, float] = (0.0, 0.0)) -> sc.Context:
    """A top view at 1:1 whose page origin (200, 150) is the model point ``centre`` (so a hole at the centre is at (200, 150))."""
    view = sc.ViewInfo("top", 200.0, 150.0, 1.0, (0, 0, 1), (1, 0, 0), (centre[0], centre[1], 12.0), (120.0, 70.0, 280.0, 230.0))
    ctx = sc.Context(sheet=model.Sheet(420.0, 297.0), views={"top": view}, holes=holes, geometry=sc.Geometry(), area=(25.0, 20.0, 405.0, 282.0))
    ctx.extras["extent_mm"] = (160.0, 160.0, 12.0)
    return ctx


def run(ctx: sc.Context, *specs: dict[str, Any]) -> tuple[list[dict[str, Any]], list[sc.ItemResult]]:
    taken: set[str] = set()
    normalised = []
    for number, spec in enumerate(specs, start=1):
        done = items.normalise(spec, taken, number)
        taken.add(done["id"])
        normalised.append(done)
    ctx.extras["items"] = normalised
    return normalised, items.draw_all(ctx, normalised)


def at(h: sc.HoleInfo) -> list[float]:
    return list(h.centre)


def lines(ctx: sc.Context, layer: str = model.AXES) -> list[model.Line]:
    return [e for e in ctx.sheet.entities if isinstance(e, model.Line) and e.layer == layer]


def angle_of(line: model.Line) -> float:
    return math.degrees(math.atan2(line.y2 - line.y1, line.x2 - line.x1)) % 180.0


# --- finding the patterns -----------------------------------------------------------------------------------------------


def test_six_holes_on_a_pitch_circle_around_a_bore_are_one_pattern() -> None:
    holes = [bore(), *ring(6, 35.0)]
    (pattern,) = sp.find(holes, sp.axes_of_holes(holes))
    assert pattern.count == 6 and pattern.even and abs(pattern.diameter - 70.0) < 1e-6
    assert all(abs(c) < 1e-6 for c in pattern.centre[:2]) and pattern.indexes == {1, 2, 3, 4, 5, 6}


def test_a_pattern_off_the_centre_of_the_part_is_found_about_its_bore() -> None:
    holes = [bore(0, 30.0, -12.0, 20.0), *ring(6, 22.5, 15.0, centre=(30.0, -12.0))]
    (pattern,) = sp.find(holes, sp.axes_of_holes(holes))
    assert abs(pattern.centre[0] - 30.0) < 1e-6 and abs(pattern.centre[1] + 12.0) < 1e-6 and abs(pattern.radius - 22.5) < 1e-6


def test_the_four_holes_of_a_rectangle_around_a_bore_are_not_a_bolt_circle() -> None:
    corners = [hole(i + 1, x, y, 11.0, None) for i, (x, y) in enumerate([(-60, -35), (60, -35), (60, 35), (-60, 35)])]
    holes = [bore(), *corners]
    assert sp.find(holes, sp.axes_of_holes(holes)) == []


def test_four_holes_of_a_square_need_an_axis_and_five_or_more_do_not() -> None:
    square = ring(4, 30.0, 45.0)
    assert sp.find(square, sp.axes_of_holes(square)) == []
    with_bore = [bore(), *square]
    assert len(sp.find(with_bore, sp.axes_of_holes(with_bore))) == 1
    five = ring(5, 30.0)
    assert len(sp.find(five, sp.axes_of_holes(five))) == 1


def test_three_holes_that_happen_to_be_on_a_circle_are_not_a_pattern_without_an_axis() -> None:
    odd = [hole(1, 10.0, 0.0), hole(2, -3.0, 9.0), hole(3, 4.0, -8.0)]
    assert sp.find(odd, sp.axes_of_holes(odd)) == []


def test_an_uneven_pattern_about_a_bore_is_found_and_says_it_is_uneven() -> None:
    angles = (0.0, 100.0, 230.0)
    holes = [bore(), *[hole(i + 1, 30 * math.cos(math.radians(a)), 30 * math.sin(math.radians(a))) for i, a in enumerate(angles)]]
    (pattern,) = sp.find(holes, sp.axes_of_holes(holes))
    assert pattern.count == 3 and not pattern.even


def test_the_flange_with_eight_tapped_holes_and_a_lone_small_one_has_one_pattern_and_a_lone_hole_about_its_axis() -> None:
    tapped = ring(8, 40.0, 22.5)
    lone = hole(20, 25.0 * math.cos(math.radians(30)), 25.0 * math.sin(math.radians(30)), 4.0, None)
    holes = [bore(0, d=30.0), *tapped, lone]
    ctx = context(holes)
    (pattern,) = sp.patterns_of(ctx)
    assert pattern.count == 8 and 20 not in pattern.indexes
    polar = sp.lone_hole(ctx, lone)
    assert polar.lone and abs(polar.radius - 25.0) < 1e-6 and all(abs(c) < 1e-6 for c in polar.centre[:2])


def test_a_lone_hole_with_no_axis_to_count_from_says_so() -> None:
    ctx = context([hole(1, 20.0, 10.0)])
    with pytest.raises(sc.ItemError, match="about"):
        sp.lone_hole(ctx, ctx.holes[0])


# --- drawing ------------------------------------------------------------------------------------------------------------------


def plate_ctx(first: float = 0.0) -> tuple[sc.Context, list[sc.HoleInfo]]:
    holes = [bore(), *ring(6, 35.0, first)]
    return context(holes), holes


def test_the_item_draws_the_pitch_circle_the_radial_lines_and_the_diameter() -> None:
    ctx, holes = plate_ctx()
    _, results = run(ctx, {"type": "pattern_dim", "at": at(holes[1])})
    assert results[0].status == "ok", results[0].detail
    circles = [e for e in ctx.sheet.entities if isinstance(e, model.Circle) and e.layer == model.AXES]
    assert [(round(c.cx, 3), round(c.cy, 3), round(c.r, 3)) for c in circles] == [(200.0, 150.0, 35.0)]
    assert "Ø70" in ctx.sheet.texts()
    centre_lines = [ln for ln in lines(ctx) if math.hypot(ln.x1 - 200.0, ln.y1 - 150.0) < 1e-6]
    assert len(centre_lines) == 6  # one from the axis towards each hole


def test_the_centre_mark_of_a_hole_is_turned_along_its_radius() -> None:
    ctx, holes = plate_ctx(first=30.0)
    run(ctx, {"type": "pattern_dim", "at": at(holes[1])})
    through_the_30_degree_hole = [
        ln for ln in lines(ctx) if abs(ln.x1 + ln.x2 - 2 * (200.0 + 35.0 * math.cos(math.radians(30)))) < 1e-6 and abs(ln.y1 + ln.y2 - 2 * (150.0 + 35.0 * math.sin(math.radians(30)))) < 1e-6
    ]
    assert sorted(round(angle_of(ln), 1) for ln in through_the_30_degree_hole) == [30.0, 120.0]  # the radial bar and the tangential one


def test_a_hole_on_a_centre_line_keeps_the_upright_cross() -> None:
    ctx, holes = plate_ctx(first=0.0)
    run(ctx, {"type": "pattern_dim", "at": at(holes[1])})
    marks = [ln for ln in lines(ctx) if abs(ln.x1 + ln.x2 - 2 * 235.0) < 1e-6 and abs(ln.y1 + ln.y2 - 2 * 150.0) < 1e-6]
    assert sorted(round(angle_of(ln), 1) for ln in marks) == [0.0, 90.0]


def test_a_hole_outside_every_pattern_keeps_the_plain_cross() -> None:
    ctx = context([hole(1, 20.0, 10.0)])
    run(ctx, {"type": "hole_dim", "all": True})
    assert sorted(round(angle_of(ln), 1) for ln in lines(ctx)) == [0.0, 90.0]


def test_the_angle_of_the_first_hole_is_given_when_it_is_not_on_a_centre_line() -> None:
    holes = [bore(0, d=34.0), *ring(4, 40.0, 45.0, d=9.0, thread=None)]
    ctx = context(holes)
    _, results = run(ctx, {"type": "pattern_dim", "at": at(holes[1]), "kind": "angle"})
    assert results[0].status == "ok", results[0].detail
    assert "45°" in ctx.sheet.texts()
    assert [e for e in ctx.sheet.entities if isinstance(e, model.Arc) and e.layer == model.DIMENSIONS]
    assert ctx.extras["polar"][1]["given"] == 1


def test_the_first_hole_is_the_lowest_one_when_several_are_the_same_angle_from_a_centre_line() -> None:
    ctx, holes = plate_ctx(first=15.0)  # 15, 75, 135, 195, 255, 315: every one is 15 degrees from a centre line
    run(ctx, {"type": "pattern_dim", "at": at(holes[1]), "kind": "angle"})
    (arc,) = [e for e in ctx.sheet.entities if isinstance(e, model.Arc) and e.layer == model.DIMENSIONS]
    assert (round(arc.start, 3), round(arc.end, 3)) == (0.0, 15.0)


def test_the_diameter_leader_leaves_between_two_holes_and_never_along_a_centre_line() -> None:
    holes = [bore(0, d=30.0), *ring(8, 40.0, 22.5)]  # the gaps are at 0, 45, 90...: the ones on a centre line are not used
    ctx = context(holes)
    run(ctx, {"type": "pattern_dim", "at": at(holes[1])})
    tips = [(ln.x1, ln.y1) for ln in lines(ctx, model.DIMENSIONS) if abs(ln.x1 - 200.0) > 1 or abs(ln.y1 - 150.0) > 1]
    leader = [ln for ln in lines(ctx, model.DIMENSIONS) if math.hypot(ln.x2 - ln.x1, ln.y2 - ln.y1) > 6.0 and abs(ln.y1 - ln.y2) > 1e-6]
    assert tips and leader
    ln = leader[0]
    direction = math.degrees(math.atan2(ln.y1 - 150.0, ln.x1 - 200.0)) % 90.0
    assert min(direction, 90.0 - direction) > 1.0


def test_the_leader_is_not_put_along_a_centre_line_because_the_outline_has_a_vertex_there() -> None:
    holes = [bore(0, d=30.0), *ring(8, 40.0, 22.5)]
    ctx = context(holes)
    ctx.extras["view_ink"] = [model.Circle(200.0, 150.0, 70.0, model.CONTOUR)]  # a round flange: its polygon has a vertex at 0 degrees
    ctx.views["top"].box = (130.0, 80.0, 270.0, 220.0)
    run(ctx, {"type": "pattern_dim", "at": at(holes[1])})
    ln = next(e for e in lines(ctx, model.DIMENSIONS) if math.hypot(e.x2 - e.x1, e.y2 - e.y1) > 6.0 and abs(e.y1 - e.y2) > 1e-6)
    direction = math.degrees(math.atan2(ln.y1 - 150.0, ln.x1 - 200.0)) % 90.0
    assert min(direction, 90.0 - direction) > 1.0


def test_no_angle_is_drawn_when_the_first_hole_is_on_a_centre_line() -> None:
    ctx, holes = plate_ctx(first=0.0)
    before = len(ctx.sheet.entities)
    _, results = run(ctx, {"type": "pattern_dim", "at": at(holes[1]), "kind": "angle"})
    assert results[0].status == "ok" and "centre line" in results[0].detail
    assert len(ctx.sheet.entities) == before


def test_a_lone_hole_on_a_radius_gets_its_radius_as_a_pitch_circle_and_its_angle() -> None:
    lone = hole(20, 25.0 * math.cos(math.radians(30)), 25.0 * math.sin(math.radians(30)), 4.0, None)
    holes = [bore(0, d=30.0), *ring(8, 40.0, 22.5), lone]
    ctx = context(holes)
    _, results = run(ctx, {"type": "pattern_dim", "at": at(lone)}, {"type": "pattern_dim", "at": at(lone), "kind": "angle"})
    assert [r.status for r in results] == ["ok", "ok"], [r.detail for r in results]
    assert "Ø50" in ctx.sheet.texts() and "30°" in ctx.sheet.texts()


def test_the_default_location_does_not_count_the_holes_of_a_pattern_in_x_and_y() -> None:
    holes = [bore(), *ring(6, 35.0, 30.0)]
    ctx = context(holes)
    ctx.extras["symmetry"] = {"x": 0.0, "y": 0.0}
    run(ctx, {"type": "datum_dims", "auto": True})
    assert any(i in ctx.extras.get("positions", {}) for i in range(1, 7))  # without a pattern item: coordinates
    ctx = context(holes)
    ctx.extras["symmetry"] = {"x": 0.0, "y": 0.0}
    run(ctx, {"type": "datum_dims", "auto": True}, {"type": "pattern_dim", "at": at(holes[1]), "auto": True})
    assert not any(i in ctx.extras.get("positions", {}) for i in range(1, 7))


def test_the_callout_of_a_pattern_counts_only_its_holes() -> None:
    holes = [bore(), *ring(6, 35.0), hole(7, 60.0, 50.0)]
    ctx = context(holes)
    run(ctx, {"type": "hole_dim", "all": True})
    callouts = [t for t in ctx.sheet.texts() if "M8" in t]
    assert any(t.startswith("6× M8") for t in callouts) and any(t.startswith("M8") for t in callouts)


def test_a_pattern_item_whose_hole_is_on_no_pattern_is_refused_with_the_reason() -> None:
    ctx = context([hole(1, 20.0, 10.0)])
    _, results = run(ctx, {"type": "pattern_dim", "at": [20.0, 10.0, 12.0]})
    assert results[0].status == "error" and "axis" in results[0].detail


# --- the review ---------------------------------------------------------------------------------------------------------------


def findings(ctx: sc.Context, normalised: list[dict[str, Any]], results: list[sc.ItemResult]) -> list[review.Finding]:
    ctx.extras.setdefault("units", [])
    data = review.ReviewInput(normalised, {r.id: r for r in results}, ctx, ctx.sheet, [])
    return [f for f in quality.check(data) if f.code == "pattern_not_polar"]


def test_a_pattern_located_by_coordinates_is_a_finding() -> None:
    ctx, holes = plate_ctx(30.0)
    done, results = run(ctx, {"type": "datum_dims", "x_from": "min", "y_from": "min"})
    (found,) = findings(ctx, done, results)
    assert "X / Y" in found.message and "Ø70" in found.message and found.items == ("g1",)


def test_a_pitch_circle_that_is_only_in_a_note_is_a_finding() -> None:
    ctx, holes = plate_ctx(30.0)
    done, results = run(ctx, {"type": "note", "text": "6 agujeros M8 en círculo de Ø70"})
    (found,) = findings(ctx, done, results)
    assert "solo en una nota" in found.message


def test_a_pattern_with_its_pitch_circle_dimensioned_is_no_finding() -> None:
    ctx, holes = plate_ctx(30.0)
    done, results = run(ctx, {"type": "pattern_dim", "at": at(holes[1])}, {"type": "pattern_dim", "at": at(holes[1]), "kind": "angle"})
    assert findings(ctx, done, results) == []


def test_a_pattern_whose_first_hole_is_off_a_centre_line_needs_its_angle() -> None:
    ctx, holes = plate_ctx(15.0)
    done, results = run(ctx, {"type": "pattern_dim", "at": at(holes[1])})
    (found,) = findings(ctx, done, results)
    assert "ángulo" in found.message


def test_the_four_holes_of_a_rectangle_are_never_a_finding() -> None:
    corners = [hole(i + 1, x, y, 11.0, None) for i, (x, y) in enumerate([(-60, -35), (60, -35), (60, 35), (-60, 35)])]
    ctx = context([bore(), *corners])
    done, results = run(ctx, {"type": "datum_dims", "x_from": "min", "y_from": "min"})
    assert findings(ctx, done, results) == []


# --- the planner ---------------------------------------------------------------------------------------------------------------


def test_the_planner_places_the_diameter_as_a_leader_and_the_angle_as_an_arc() -> None:
    assert "pattern_dim" in bridge.MOVABLE_TYPES
    assert bridge._wants_kind("pattern_dim", {"type": "pattern_dim"}) == "leader"  # noqa: SLF001
    assert bridge._wants_kind("pattern_dim", {"type": "pattern_dim", "kind": "angle"}) == "angle"  # noqa: SLF001


def test_the_diameter_leader_is_a_leader_the_planner_can_read() -> None:
    ctx, holes = plate_ctx(30.0)
    run(ctx, {"type": "pattern_dim", "at": at(holes[1])})
    ink = bridge.sheet_collide.ink_of("pd1", ctx.sheet.entities)
    assert bridge.read_callout(ink) is not None and bridge._leader_lines(ink) == 1  # noqa: SLF001
