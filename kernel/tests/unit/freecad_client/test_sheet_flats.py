# ruff: noqa: E501
"""Flat faces of a turned part get their two diagonals (``worker/sheet_flats.py``, ``view_lines``, the review): no FreeCAD.

The part is a washer: a 40 x 40 square flange 6 mm thick whose corners are cut by a Ø50 cylinder, a Ø30 x 5 boss on it and a Ø10
hole through. Its axis runs along z. Seen from the side (front: looking along y), the flat of the flange (a rectangle 30 x 6, the
corners being cut by the cylinder) reads as a cylinder next to the boss unless it carries its diagonals."""

import math
import sys
from pathlib import Path
from typing import Any

import pytest

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import sheet_checks as checks  # noqa: E402
import sheet_context as sc  # noqa: E402
import sheet_flats as flats  # noqa: E402
import sheet_model as model  # noqa: E402

from .test_sheet_items import draw  # noqa: E402
from .test_sheet_views import ViewsGeometry, run_review, views_context  # noqa: E402

Z_AXIS = (0.0, 0.0, 1.0)
FLANGE = (0.0, 6.0)  # along the axis: the flange; the boss stands on it up to 11
HALF = 20.0
CHORD = 15.0  # half the width of the flat left by the Ø50 cylinder: sqrt(25^2 - 20^2)


def washer_axes() -> list[flats.Turned]:
    # the boss (Ø30, a whole turn) and the four corner arcs of the Ø50 cylinder (16 degrees each) share the axis
    return [flats.Turned(Z_AXIS, (0.0, 0.0, 0.0), 25.0, 0.0, 11.0, True)]


def washer_flats() -> list[flats.Flat]:
    mid = (FLANGE[0] + FLANGE[1]) / 2
    return [
        flats.Flat("face:minus_y", (0, -1, 0), (0.0, -HALF, mid)),
        flats.Flat("face:plus_y", (0, 1, 0), (0.0, HALF, mid)),
        flats.Flat("face:minus_x", (-1, 0, 0), (-HALF, 0.0, mid)),
        flats.Flat("face:plus_x", (1, 0, 0), (HALF, 0.0, mid)),
        flats.Flat("face:flange_top", (0, 0, 1), (0.0, 0.0, FLANGE[1])),
        flats.Flat("face:flange_bottom", (0, 0, -1), (0.0, 0.0, FLANGE[0])),
    ]


# --- which planar faces are flats of a turned part --------------------------------------------------------------------------


def test_the_sides_of_a_square_flange_cut_by_a_cylinder_are_flats_of_the_round() -> None:
    found = flats.find_flats(washer_axes(), washer_flats())
    assert sorted(f.face_id for f in found) == ["face:minus_x", "face:minus_y", "face:plus_x", "face:plus_y"]
    by_id = {f.face_id: f for f in found}
    assert by_id["face:minus_y"].normal == pytest.approx((0, -1, 0)), "the normal points away from the axis"
    assert by_id["face:plus_x"].distance == pytest.approx(HALF)


def test_the_normal_of_a_flat_points_away_from_the_axis_whatever_sense_the_face_gave() -> None:
    inverted = [flats.Flat("face:a", (0, 1, 0), (0.0, -HALF, 3.0))]  # a normal of the wrong sense (a reversed face)
    (found,) = flats.find_flats(washer_axes(), inverted)
    assert found.normal == pytest.approx((0, -1, 0))


def test_a_square_flange_with_sharp_corners_is_a_plate_not_a_flat_of_the_round() -> None:
    boss_only = [
        flats.Turned(Z_AXIS, (0.0, 0.0, 0.0), 15.0, 0.0, 11.0, True)
    ]  # no corner cylinder: 20 is beyond the round of 15
    assert flats.find_flats(boss_only, washer_flats()) == []


def test_the_end_faces_and_the_fillets_tangent_to_a_round_are_not_flats() -> None:
    assert not [f for f in flats.find_flats(washer_axes(), washer_flats()) if f.face_id.startswith("face:flange")]
    tangent = [flats.Flat("face:t", (0, -1, 0), (0.0, -25.0, 3.0))]  # at the radius itself
    assert flats.find_flats(washer_axes(), tangent) == []


def test_a_round_that_covers_too_little_of_its_turn_is_not_a_turned_axis() -> None:
    fillet = [flats.Turned(Z_AXIS, (0.0, 0.0, 0.0), 25.0, 0.0, 11.0, False)]
    assert flats.find_flats(fillet, washer_flats()) == []


def test_a_flat_far_along_the_axis_from_the_round_is_not_its_flat() -> None:
    far = [flats.Flat("face:far", (0, -1, 0), (0.0, -HALF, 60.0))]
    assert flats.find_flats(washer_axes(), far) == []


def test_the_walls_and_the_bottom_of_a_keyway_are_left_out() -> None:
    shaft = [flats.Turned(Z_AXIS, (0.0, 0.0, 0.0), 12.0, 0.0, 50.0, True)]
    key = [
        flats.Flat("face:wall_a", (1, 0, 0), (2.0, 7.0, 25.0)),
        flats.Flat("face:wall_b", (-1, 0, 0), (-2.0, 7.0, 25.0)),
        flats.Flat("face:bottom", (0, 1, 0), (0.0, 7.0, 25.0)),
        flats.Flat("face:wrench", (0, -1, 0), (0.0, -8.0, 45.0)),
    ]
    found = flats.find_flats(shaft, key, frozenset({"face:wall_a", "face:wall_b", "face:bottom"}))
    assert [f.face_id for f in found] == ["face:wrench"]


# --- the diagonals in a view ----------------------------------------------------------------------------------------------


class WasherGeometry(ViewsGeometry):
    """The flat faces as 3D rectangles (with a collinear point on each side, as a discretised wire has), seen in any view."""

    RECTS = {
        "face:minus_y": [(-CHORD, -HALF, 0.0), (CHORD, -HALF, 0.0), (CHORD, -HALF, 6.0), (-CHORD, -HALF, 6.0)],
        "face:plus_x": [(HALF, -CHORD, 0.0), (HALF, CHORD, 0.0), (HALF, CHORD, 6.0), (HALF, -CHORD, 6.0)],
    }

    def face_outline(self, face_id: str, centre: sc.Vec3, xdir: sc.Vec3, direction: sc.Vec3) -> list[list[sc.Point]]:
        ydir = sc.cross(direction, xdir)
        corners = self.RECTS[face_id]
        ring: list[sc.Vec3] = []
        for i, a in enumerate(corners):
            b = corners[(i + 1) % 4]
            ring += [a, tuple((p + q) / 2 for p, q in zip(a, b, strict=True))]  # type: ignore[list-item]
        return [[(sc.dot(sc.sub(p, centre), xdir), sc.dot(sc.sub(p, centre), ydir)) for p in ring]]


def washer_context(*, geometry: Any = None) -> sc.Context:
    ctx = views_context([])
    ctx.geometry = geometry or WasherGeometry()
    found = [f for f in flats.find_flats(washer_axes(), washer_flats()) if f.face_id in WasherGeometry.RECTS]
    ctx.extras["facts"] = checks.PartFacts(flats=found)
    return ctx


def thin_lines(ctx: sc.Context) -> list[model.Line]:
    return [e for e in ctx.sheet.entities if isinstance(e, model.Line) and e.layer == model.FLAT_FACES]


def test_a_flat_is_seen_face_on_only_in_the_view_that_looks_along_its_normal() -> None:
    ctx = washer_context()
    by_id = {f.face_id: f for f in ctx.extras["facts"].flats}
    assert [v.name for v in flats.seen_in(ctx, by_id["face:minus_y"])] == ["front"]  # looking along -y, from the front
    assert [v.name for v in flats.seen_in(ctx, by_id["face:plus_x"])] == ["right"]


def test_the_diagonals_join_opposite_corners_of_the_flat_on_the_page() -> None:
    ctx = washer_context()
    flat = next(f for f in ctx.extras["facts"].flats if f.face_id == "face:minus_y")
    front = ctx.views["front"]
    (first, second), how = flats.diagonals(ctx, front, flat)  # type: ignore[misc]
    assert "four corners" in how
    assert first.layer == second.layer == model.FLAT_FACES
    # the flat is 30 wide and 6 tall at 1:1; the front view looks along -y with x to the right, z up
    left, right = front.project((-CHORD, -HALF, 0.0)), front.project((CHORD, -HALF, 6.0))
    ends = {(round(first.x1, 3), round(first.y1, 3), round(first.x2, 3), round(first.y2, 3))}
    assert ends == {(round(left[0], 3), round(left[1], 3), round(right[0], 3), round(right[1], 3))}
    assert math.dist((second.x1, second.y1), (second.x2, second.y2)) == pytest.approx(math.hypot(30, 6))


def test_view_lines_draws_the_diagonals_of_the_flats_in_the_views_that_see_them_face_on() -> None:
    ctx = washer_context()
    (res,) = draw(ctx, {"type": "view_lines"})
    assert res.status == "ok" and "diagonals" in res.detail and "face:minus_y in front" in res.detail
    assert len(thin_lines(ctx)) == 4, "two flats, each seen face-on in one view, two diagonals each"


def test_with_centres_off_no_diagonals_are_drawn() -> None:
    ctx = washer_context()
    draw(ctx, {"type": "view_lines", "centres": False})
    assert thin_lines(ctx) == []


def test_a_flat_with_curved_ends_gets_the_diagonals_of_the_box_of_its_outline() -> None:
    class Rounded(WasherGeometry):
        def face_outline(
            self, face_id: str, centre: sc.Vec3, xdir: sc.Vec3, direction: sc.Vec3
        ) -> list[list[sc.Point]]:
            # a flat 30 long with a rounded end (a cutter's run-out): many corners, not four
            pts = [(-15.0, -3.0), (10.0, -3.0)]
            pts += [(10.0 + 3.0 * math.sin(a / 10 * math.pi), -3.0 * math.cos(a / 10 * math.pi)) for a in range(1, 10)]
            pts += [(10.0, 3.0), (-15.0, 3.0)]
            return [pts]

    ctx = washer_context(geometry=Rounded())
    flat = next(f for f in ctx.extras["facts"].flats if f.face_id == "face:minus_y")
    got = flats.diagonals(ctx, ctx.views["front"], flat)
    assert got is not None
    lines, how = got
    assert "box of its outline" in how
    assert max(math.dist((ln.x1, ln.y1), (ln.x2, ln.y2)) for ln in lines) == pytest.approx(math.hypot(28, 6))


def test_a_flat_too_thin_to_read_gets_none() -> None:
    class Thin(WasherGeometry):
        def face_outline(
            self, face_id: str, centre: sc.Vec3, xdir: sc.Vec3, direction: sc.Vec3
        ) -> list[list[sc.Point]]:
            return [[(0.0, 0.0), (30.0, 0.0), (30.2, 0.2), (0.0, 0.2)]]

    ctx = washer_context(geometry=Thin())
    flat = ctx.extras["facts"].flats[0]
    assert flats.diagonals(ctx, ctx.views["front"], flat) is None


# --- the review -----------------------------------------------------------------------------------------------------------


def found(code: str, findings: list[Any]) -> list[Any]:
    return [f for f in findings if f.code == code]


def test_a_sheet_without_the_diagonals_gets_a_finding_that_names_the_view_and_the_face() -> None:
    ctx = washer_context()
    unmarked = found("flat_face_unmarked", run_review(ctx, {"type": "overall"}))
    assert sorted(f.key for f in unmarked) == ["face:minus_y", "face:plus_x"]
    by_key = {f.key: f for f in unmarked}
    assert "front" in by_key["face:minus_y"].message and "face:minus_y" in by_key["face:minus_y"].message
    assert "right" in by_key["face:plus_x"].message
    assert "view_lines" in by_key["face:plus_x"].message


def test_the_finding_goes_away_when_view_lines_draws_the_diagonals() -> None:
    marked = run_review(washer_context(), {"type": "overall"}, {"type": "view_lines"})
    assert found("flat_face_unmarked", marked) == []


def test_a_part_without_flats_or_an_assembly_never_gets_the_finding() -> None:
    plain = views_context([])
    plain.extras["facts"] = checks.PartFacts()
    assert found("flat_face_unmarked", run_review(plain, {"type": "overall"})) == []
    assembly = washer_context()
    assembly.extras["facts"].assembly = True
    assert found("flat_face_unmarked", run_review(assembly, {"type": "overall"})) == []


def test_a_diagonal_that_is_not_where_the_flat_is_does_not_close_the_finding() -> None:
    ctx = washer_context()
    ctx.add([model.Line(10.0, 10.0, 20.0, 20.0, model.FLAT_FACES)])  # a thin line elsewhere on the sheet
    assert found("flat_face_unmarked", run_review(ctx, {"type": "overall"}))
