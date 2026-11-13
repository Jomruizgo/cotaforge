# ruff: noqa: E501
"""The quality floor of a drawing (F7) against a hand-made 100 x 60 x 19 plate with four dowel holes, two counterbored holes and an
M6, laid out the way ``drawing.py`` lays a sheet out: the default baseline location of the holes, the short leaders that do not
cross, the dowel marker, the numbered notes with the thickness and the ISO 286 note, the paper sizes and the third angle. No
FreeCAD: everything here is arithmetic on what the sheet composes."""

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import sheet_checks as checks  # noqa: E402
import sheet_collide as collide  # noqa: E402
import sheet_context as sc  # noqa: E402
import sheet_items as items  # noqa: E402
import sheet_layout as lay  # noqa: E402
import sheet_model as model  # noqa: E402
import sheet_quality as quality  # noqa: E402
import sheet_review as review  # noqa: E402
import sheet_template as tpl  # noqa: E402
import sheet_template_gen as gen  # noqa: E402

PAGE = (420.0, 297.0)
WIDTH, DEPTH, HEIGHT = 100.0, 60.0, 19.0
DOWELS = ((15.0, 12.0), (85.0, 12.0), (15.0, 48.0), (85.0, 48.0))
CLEAR = ((35.0, 30.0), (65.0, 30.0))
TAP = (50.0, 12.0)
ROOM = {"left_mm": 29.0, "right_mm": 24.0, "spacing_mm": 34.0, "below_mm": 43.0}


class PlateGeometry(sc.Geometry):
    def __init__(self) -> None:
        self.faces = {
            "face:left": sc.FaceInfo("face:left", "plane", (0.0, 30.0, 9.5), normal=(-1.0, 0.0, 0.0)),
            "face:bottom": sc.FaceInfo("face:bottom", "plane", (50.0, 30.0, 0.0), normal=(0.0, 0.0, -1.0)),
            "face:front": sc.FaceInfo("face:front", "plane", (50.0, 0.0, 9.5), normal=(0.0, -1.0, 0.0)),
        }

    def face(self, face_id: str) -> sc.FaceInfo:
        return self.faces[face_id]

    def round_extent(self) -> None:
        return None


def plate(third: bool = False, one_view: bool = False) -> tuple[sc.Context, lay.Layout]:
    if one_view:
        layout = lay.plan(
            WIDTH, 0.0, DEPTH, *PAGE, None, None, True, False, False, iso_dims=(WIDTH, DEPTH, HEIGHT), **ROOM
        )
    else:
        room = dict(ROOM, below_mm=15.0, above_mm=43.0) if third else ROOM
        layout = lay.plan(
            WIDTH,
            DEPTH,
            HEIGHT,
            *PAGE,
            None,
            None,
            True,
            True,
            False,
            third=third,
            iso_dims=(WIDTH, DEPTH, HEIGHT),
            **room,
        )
    s = layout.scale
    fx, fy = layout.front[0], PAGE[1] - layout.front[1]
    centre = (50.0, 30.0, 9.5)
    if one_view:
        views = {
            "top": sc.ViewInfo(
                "top",
                fx,
                fy,
                s,
                (0, 0, 1),
                (1, 0, 0),
                centre,
                (fx - WIDTH * s / 2, fy - DEPTH * s / 2, fx + WIDTH * s / 2, fy + DEPTH * s / 2),
            )
        }
    else:
        front = sc.ViewInfo(
            "front",
            fx,
            fy,
            s,
            (0, -1, 0),
            (1, 0, 0),
            centre,
            (fx - WIDTH * s / 2, fy - HEIGHT * s / 2, fx + WIDTH * s / 2, fy + HEIGHT * s / 2),
        )
        shift = HEIGHT * s / 2 + layout.spacing + DEPTH * s / 2
        ty = fy + shift if third else fy - shift
        top = sc.ViewInfo(
            "top",
            fx,
            ty,
            s,
            (0, 0, 1),
            (1, 0, 0),
            centre,
            (fx - WIDTH * s / 2, ty - DEPTH * s / 2, fx + WIDTH * s / 2, ty + DEPTH * s / 2),
        )
        views = {"front": front, "top": top}
    holes: list[sc.HoleInfo] = []
    for x, y in DOWELS:
        holes.append(
            sc.HoleInfo(len(holes), (x, y, 19.0), (0, 0, -1), 6.0, 19.0, True, (f"face:d{len(holes)}",), None, True)
        )
    for x, y in CLEAR:
        holes.append(
            sc.HoleInfo(len(holes), (x, y, 19.0), (0, 0, -1), 9.0, 19.0, True, (f"face:c{len(holes)}",), None, True)
        )
        holes.append(
            sc.HoleInfo(len(holes), (x, y, 19.0), (0, 0, -1), 14.0, 9.0, False, (f"face:b{len(holes)}",), None, True)
        )
    holes.append(
        sc.HoleInfo(len(holes), (TAP[0], TAP[1], 19.0), (0, 0, -1), 5.0, 12.0, False, ("face:t",), ("M6", 10.0), True)
    )
    left, top_, right, bottom = lay.drawing_area(*PAGE)
    ctx = sc.Context(
        sheet=model.Sheet(*PAGE),
        views=views,
        holes=holes,
        geometry=PlateGeometry(),
        area=(left, PAGE[1] - bottom, right, PAGE[1] - top_),
    )
    ctx.extras["angle"] = "third" if third else "first"
    ctx.extras["extent_mm"] = (WIDTH, DEPTH, HEIGHT)
    ink: list[Any] = []
    for v in views.values():
        x0, y0, x1, y1 = v.box
        ink += [
            model.Line(x0, y0, x1, y0),
            model.Line(x1, y0, x1, y1),
            model.Line(x1, y1, x0, y1),
            model.Line(x0, y1, x0, y0),
        ]
    top_view = views["top"]
    ink += [model.Circle(*top_view.project(h.centre), h.diameter / 2 * s) for h in holes]
    ctx.sheet.extend(ink)
    ctx.extras["view_ink"] = ink
    ctx.extras["view_boxes"] = [v.box for v in views.values()]
    return ctx, layout


def draw(ctx: sc.Context, *specs: dict[str, Any]) -> list[sc.ItemResult]:
    taken: set[str] = set()
    normalised = []
    for number, spec in enumerate(specs, start=1):
        done = items.normalise(spec, taken, number)
        taken.add(done["id"])
        normalised.append(done)
    ctx.extras["items"] = normalised
    items.prepare_all(ctx, normalised)
    return items.draw_items(ctx, normalised)


def holes_items() -> list[dict[str, Any]]:
    return [
        {"type": "hole_dim", "id": "dowels", "at": [[x, y, 0] for x, y in DOWELS], "fit": "H7", "dowel": True},
        {"type": "hole_dim", "id": "clear", "at": [[x, y, 0] for x, y in CLEAR]},
        {"type": "hole_dim", "id": "tap", "at": [[TAP[0], TAP[1], 0]]},
    ]


def sheet_of(
    *extra: dict[str, Any],
    third: bool = False,
    one_view: bool = False,
    symmetry: dict[str, float] | None = None,
    only: Callable[[sc.HoleInfo], bool] | None = None,
) -> tuple[sc.Context, list[sc.ItemResult]]:
    """The plate with its default location. ``symmetry`` says the planes the part is mirror-symmetric across (the worker
    measures them on the shape); ``only`` keeps the holes it accepts (and drops the callouts, which name all of them)."""
    ctx, _ = plate(third, one_view)
    if symmetry is not None:
        ctx.extras["symmetry"] = symmetry
    callouts = holes_items()
    if only is not None:
        ctx.holes[:] = [h for h in ctx.holes if only(h)]
        callouts = []
    specs = [{"type": "overall", "id": "o1"}, {"type": "datum_dims", "id": "loc", "auto": True}, *callouts, *extra]
    return ctx, draw(ctx, *specs)


def review_of(ctx: sc.Context, results: list[sc.ItemResult]) -> list[review.Finding]:
    """The findings about the positions of the holes: the review's own and the quality floor's."""
    ctx.extras.setdefault("units", [])
    data = review.ReviewInput(ctx.extras["items"], {r.id: r for r in results}, ctx, ctx.sheet, [])
    return review._hole_callouts(data) + quality.check(data)  # noqa: SLF001


def of_code(found: list[review.Finding], code: str) -> list[review.Finding]:
    return [f for f in found if f.code == code]


def is_dowel(hole: sc.HoleInfo) -> bool:
    return hole.diameter == 6.0


CORNER_VALUES = ("15", "35", "50", "65", "85", "12", "48")  # what a count from the corner of the box would print


# --- the default location -----------------------------------------------------------------------------------------------------


def test_a_plate_with_one_plane_of_symmetry_is_located_across_it_by_pairs_and_asks_for_the_other_direction() -> None:
    ctx, results = sheet_of(symmetry={"x": 50.0})
    loc = {r.id: r for r in results}["loc"]
    assert loc.status == "warning" and "Ask the engineer" in loc.detail and "along y" in loc.detail
    texts = ctx.sheet.texts()
    assert texts.count("70") == 1 and texts.count("30") == 1  # the dowel pair and the clearance pair, once each
    assert not [v for v in CORNER_VALUES if v in texts]  # nothing counted from the corner of the box
    assert all(ctx.extras["loc_axes"][h.index] == {"x"} for h in ctx.holes)  # located across x, nothing along y
    assert all(ctx.extras["loc_missing"][h.index] == {"y"} for h in ctx.holes)
    top = ctx.views["top"]
    plane = top.project((50.0, 0.0, 0.0))[0]  # the plane of symmetry is drawn as a centre line
    lines = [e for e in ctx.sheet.of_layer(model.AXES) if isinstance(e, model.Line)]
    assert any(
        abs(e.x1 - plane) < 1e-6 and abs(e.x2 - plane) < 1e-6 and abs(e.y1 - e.y2) > top.box[3] - top.box[1]
        for e in lines
    )


@pytest.mark.parametrize(("third", "one_view"), [(True, False), (False, True)])
def test_the_pairs_of_a_symmetric_part_go_outside_the_part_in_third_angle_and_in_a_sheet_of_one_view(
    third: bool, one_view: bool
) -> None:
    ctx, results = sheet_of(symmetry={"x": 50.0, "y": 30.0}, only=is_dowel, third=third, one_view=one_view)
    assert {r.id: r.status for r in results}["loc"] == "ok"
    box = ctx.views["top"].box
    texts = [e for e in ctx.sheet.entities if isinstance(e, model.Text) and e.text in ("70", "36")]
    assert len(texts) == 2 and all(t.y < box[1] or t.y > box[3] or t.x < box[0] for t in texts)  # not inside the part


def test_a_part_symmetric_both_ways_is_located_by_symmetry_alone() -> None:
    ctx, results = sheet_of(symmetry={"x": 50.0, "y": 30.0}, only=is_dowel)
    assert {r.id: r.status for r in results}["loc"] == "ok"
    texts = ctx.sheet.texts()
    assert texts.count("70") == 1 and texts.count("36") == 1  # the spacing across x and across y, one dimension each
    assert not [v for v in CORNER_VALUES if v in texts]
    assert all(ctx.extras["loc_axes"][h.index] == {"x", "y"} for h in ctx.holes)
    assert not ctx.extras.get("loc_missing")
    top = ctx.views["top"]
    ink = [e for e in ctx.sheet.of_layer(model.AXES) if isinstance(e, model.Line)]
    assert any(abs(e.x1 - e.x2) < 1e-6 and abs(e.x1 - top.project((50.0, 0, 0))[0]) < 1e-6 for e in ink)  # x = 50
    assert any(abs(e.y1 - e.y2) < 1e-6 and abs(e.y1 - top.project((0, 30.0, 0))[1]) < 1e-6 for e in ink)  # y = 30


def test_a_hole_on_the_axis_of_a_symmetric_part_has_no_dimension_at_all() -> None:
    ctx, _ = plate()
    ctx.holes[:] = [sc.HoleInfo(0, (50.0, 30.0, 19.0), (0, 0, -1), 20.0, 19.0, True, ("face:bore",), None, True)]
    ctx.extras["symmetry"] = {"x": 50.0, "y": 30.0}
    results = draw(ctx, {"type": "datum_dims", "id": "loc", "auto": True})
    assert results[0].status == "ok" and ctx.extras["loc_axes"][0] == {"x", "y"}
    assert not ctx.sheet.texts() or all(not t.strip() for t in ctx.sheet.texts())  # no number: the centre lines say it
    assert not [f for f in review_of(ctx, results) if f.code in ("hole_not_located", "hole_partly_located")]


def test_with_no_reference_nothing_is_counted_from_the_corner_and_the_agent_is_told_to_ask() -> None:
    ctx, results = sheet_of()
    loc = {r.id: r for r in results}["loc"]
    assert loc.status == "warning" and "Ask the engineer which face or edge" in loc.detail and "x and y" in loc.detail
    assert not [v for v in CORNER_VALUES if v in ctx.sheet.texts()]
    found = of_code(review_of(ctx, results), "hole_not_located")
    assert len(found) == 7 and all("Pregunta al ingeniero" in f.message and "x, y" in f.message for f in found)


def test_a_declared_datum_is_the_reference_of_its_direction_and_beats_the_symmetry() -> None:
    ctx, _ = plate()
    ctx.extras["symmetry"] = {"x": 50.0}
    results = draw(
        ctx,
        {"type": "datum", "id": "dB", "letter": "B", "face": "face:left"},
        {"type": "datum_dims", "id": "loc", "auto": True},
    )
    from items_datum import declared_datums  # noqa: PLC0415

    # B is across x; the bottom (A) is not in the plane
    assert declared_datums(ctx, ctx.views["top"]) == ("face:left", None)
    texts = ctx.sheet.texts()
    assert all(texts.count(v) == 1 for v in ("35", "50", "65", "85")) and "70" not in texts  # counted from the datum
    assert {r.id: r for r in results}["loc"].status == "warning"  # y still has no reference
    assert all(ctx.extras["loc_axes"][h.index] == {"x"} for h in ctx.holes)


def datum_sheet() -> tuple[sc.Context, list[sc.ItemResult]]:
    """The plate with a datum across x (its left face) and one across y (its front face): the engineer's references."""
    ctx, _ = plate()
    results = draw(
        ctx,
        {"type": "overall", "id": "o1"},
        {"type": "datum", "id": "dB", "letter": "B", "face": "face:left"},
        {"type": "datum", "id": "dC", "letter": "C", "face": "face:front"},
        {"type": "datum_dims", "id": "loc", "auto": True},
        *holes_items(),
    )
    return ctx, results


def test_with_two_datums_every_hole_is_located_from_them_with_one_dimension_per_value() -> None:
    ctx, results = datum_sheet()
    assert {r.id: r.status for r in results}["loc"] == "ok"
    for hole in ctx.holes:
        assert ctx.extras["loc_axes"][hole.index] == {"x", "y"}, hole
    unit_texts = ctx.sheet.texts()
    for value in ("15", "35", "50", "65", "85", "12", "30", "48"):  # five x values and three y values, once each
        assert unit_texts.count(value) == 1, (value, unit_texts)


def test_the_rows_stack_outward_below_and_left_of_the_view_with_an_equal_step() -> None:
    ctx, _ = datum_sheet()
    box = ctx.views["top"].box
    lines = ctx.extras["dimension_main_lines"]
    horizontal = sorted({round(ln[1], 2) for ln in lines if abs(ln[1] - ln[3]) < 1e-6 and ln[1] < box[1]}, reverse=True)
    assert len(horizontal) == 5  # the x values: 15, 35, 50, 65, 85
    steps = {round(a - b, 2) for a, b in zip(horizontal, horizontal[1:], strict=False)}
    assert steps == {sc.STACK_STEP_MM}
    vertical = sorted({round(ln[0], 2) for ln in lines if abs(ln[0] - ln[2]) < 1e-6 and ln[0] < box[0]}, reverse=True)
    assert len(vertical) == 3 and {round(a - b, 2) for a, b in zip(vertical, vertical[1:], strict=False)} == {
        sc.STACK_STEP_MM
    }


def test_holes_that_share_a_coordinate_are_joined_by_a_centre_line() -> None:
    ctx, _ = datum_sheet()
    top = ctx.views["top"]
    axes = [e for e in ctx.sheet.of_layer(model.AXES) if isinstance(e, model.Line)]
    # the two dowels at x = 15 are joined by a vertical line
    left = [top.project((15.0, 12.0, 0.0)), top.project((15.0, 48.0, 0.0))]
    assert any(abs(ln.x1 - left[0][0]) < 1e-6 and abs(ln.x2 - left[0][0]) < 1e-6 for ln in axes), left


def test_the_default_location_leaves_alone_the_holes_another_item_locates() -> None:
    ctx, _ = plate()
    ctx.extras["symmetry"] = {"x": 50.0}
    results = draw(
        ctx,
        {"type": "datum_dims", "id": "loc", "auto": True},
        {"type": "datum_dims", "id": "mine", "holes": [[x, y, 0] for x, y in CLEAR]},
    )
    assert all(r.status in ("ok", "warning") for r in results)
    covered = ctx.extras["auto_covered"]
    assert all(h.index not in covered for h in ctx.holes if h.diameter in (9.0, 14.0))


def test_auto_takes_no_table_and_no_holes() -> None:
    with pytest.raises(sc.ItemError, match="auto"):
        items.normalise({"type": "datum_dims", "auto": True, "mode": "table"}, set(), 1)
    with pytest.raises(sc.ItemError, match="auto"):
        items.normalise({"type": "datum_dims", "auto": True, "holes": "all"}, set(), 1)


# --- the floor asks for function: a location by symmetry is complete, a real gap still fails ------------------------------


def test_the_review_accepts_a_location_by_symmetry_as_complete() -> None:
    ctx, results = sheet_of(symmetry={"x": 50.0, "y": 30.0}, only=is_dowel)
    codes = {f.code for f in review_of(ctx, results)}
    assert not codes & {"hole_not_located", "hole_partly_located", "hole_no_reference"}, codes


def test_the_review_still_fails_a_hole_the_symmetry_leaves_half_located() -> None:
    ctx, results = sheet_of(symmetry={"x": 50.0})
    found = review_of(ctx, results)
    partly = of_code(found, "hole_partly_located")
    assert partly and all(
        "falta su posición en y" in f.message and "Pregunta al ingeniero" in f.message for f in partly
    )
    assert not of_code(found, "hole_not_located")  # they are located across x: half of the way


def test_the_same_size_in_two_views_is_flagged_without_blocking() -> None:
    ctx, _ = plate()
    ctx.extras["model_box"] = ((0.0, 0.0, 0.0), (WIDTH, DEPTH, HEIGHT))
    ctx.geometry.faces["face:right"] = sc.FaceInfo("face:right", "plane", (100.0, 30.0, 9.5), normal=(1.0, 0.0, 0.0))
    results = draw(
        ctx,
        {"type": "overall", "id": "o1"},
        {
            "type": "dim",
            "id": "w",
            "kind": "distance",
            "from": {"face": "face:left"},
            "to": {"face": "face:right"},
            "view": "top",
        },
    )
    assert all(r.status == "ok" for r in results), results
    ctx.extras.setdefault("units", [])
    data = review.ReviewInput(ctx.extras["items"], {r.id: r for r in results}, ctx, ctx.sheet, [])
    (finding,) = of_code(quality.check(data), "dimension_repeated")
    assert finding.severity == "info" and finding.items == ("o1", "w") and "ISO 129" in finding.message
    assert "dimension_repeated" in checks.NON_BLOCKING


def test_a_size_given_once_or_a_different_size_in_the_other_view_is_not_repeated() -> None:
    ctx, _ = plate()
    ctx.extras["model_box"] = ((0.0, 0.0, 0.0), (WIDTH, DEPTH, HEIGHT))
    ctx.geometry.faces["face:mid"] = sc.FaceInfo("face:mid", "plane", (50.0, 30.0, 9.5), normal=(1.0, 0.0, 0.0))
    results = draw(
        ctx,
        {"type": "overall", "id": "o1"},
        {
            "type": "dim",
            "id": "half",
            "kind": "distance",
            "from": {"face": "face:left"},
            "to": {"face": "face:mid"},
            "view": "top",
        },
    )
    ctx.extras.setdefault("units", [])
    data = review.ReviewInput(ctx.extras["items"], {r.id: r for r in results}, ctx, ctx.sheet, [])
    assert not of_code(quality.check(data), "dimension_repeated")


def test_no_dimension_line_or_text_lies_inside_the_outline_of_a_view() -> None:
    ctx, _ = sheet_of()
    box = ctx.views["top"].box
    inner = (box[0] + 0.5, box[1] + 0.5, box[2] - 0.5, box[3] - 0.5)
    for seg in ctx.extras["dimension_main_lines"]:
        assert not (inner[0] < seg[0] < inner[2] and inner[1] < seg[1] < inner[3] and inner[0] < seg[2] < inner[2]), seg


# --- callouts -----------------------------------------------------------------------------------------------------------------


def test_the_leaders_of_the_callouts_never_cross_and_cut_the_outline_once() -> None:
    ctx, results = sheet_of()
    assert all(r.status in ("ok", "warning") for r in results)
    leaders = ctx.extras["leaders"]
    assert len(leaders) == 3
    parts = [
        [
            (ln["tip"][0], ln["tip"][1], ln["elbow"][0], ln["elbow"][1]),
            (ln["elbow"][0], ln["elbow"][1], ln["end"][0], ln["end"][1]),
        ]
        for ln in leaders
    ]
    for i, a in enumerate(parts):
        for b in parts[i + 1 :]:
            assert not any(collide.segments_cross(x, y) for x in a for y in b)
    outline = collide.segments_of(ctx.extras["view_ink"])
    for ln in leaders:
        cuts = sum(
            1
            for s in outline
            if collide.segments_cross((ln["tip"][0], ln["tip"][1], ln["elbow"][0], ln["elbow"][1]), s)
        )
        assert cuts == 1, (ln["item"], cuts)


def test_the_review_of_a_clean_plate_has_nothing_against_the_leaders_or_the_positions() -> None:
    ctx, results = sheet_of()
    ctx.extras["units"] = []
    data = review.ReviewInput(ctx.extras["items"], {r.id: r for r in results}, ctx, ctx.sheet, [])
    found = {f.code for f in quality.check(data)}
    assert not found & {"leader_crossing", "leader_crosses_part", "hole_partly_located", "dimension_inside_outline"}, (
        found
    )


def test_two_leaders_that_cross_are_a_finding() -> None:
    ctx, results = sheet_of()
    a, b = ctx.extras["leaders"][0], ctx.extras["leaders"][1]
    # make the second leader cross the first: it goes from the first one's elbow side to its tip side
    ctx.extras["leaders"][1] = dict(b, tip=a["elbow"], elbow=a["tip"], end=(a["tip"][0] + 10.0, a["tip"][1]))
    ctx.extras["leaders"].append(
        dict(
            b,
            item="x1",
            tip=(a["tip"][0] - 5.0, (a["tip"][1] + a["elbow"][1]) / 2),
            elbow=(a["tip"][0] + 20.0, (a["tip"][1] + a["elbow"][1]) / 2),
            end=(a["tip"][0] + 30.0, (a["tip"][1] + a["elbow"][1]) / 2),
        )
    )
    data = review.ReviewInput(ctx.extras["items"], {r.id: r for r in results}, ctx, ctx.sheet, [])
    assert "leader_crossing" in {f.code for f in quality.check(data)}


def test_the_dowel_callout_has_its_marker_its_fit_with_deviations_and_a_ring_on_each_hole() -> None:
    ctx, _ = sheet_of()
    assert "◎ 4× Ø6 H7 (+0,012/0) pasante" in ctx.sheet.texts()
    rings = [e for e in ctx.sheet.of_layer(model.AXES) if isinstance(e, model.Circle)]
    assert len(rings) == 4 and {round(r.r, 2) for r in rings} == {4.6}
    # not asked for, not a dowel: the same fit on a hole without dowel=true gets no marker and the review asks
    ctx2, _ = plate()
    results = draw(ctx2, {"type": "hole_dim", "id": "d", "at": [[x, y, 0] for x, y in DOWELS], "fit": "H7"})
    assert "4× Ø6 H7 (+0,012/0) pasante" in ctx2.sheet.texts() and not [
        e for e in ctx2.sheet.of_layer(model.AXES) if isinstance(e, model.Circle)
    ]
    data = review.ReviewInput(ctx2.extras["items"], {r.id: r for r in results}, ctx2, ctx2.sheet, [])
    assert "dowel_unmarked" in {f.code for f in quality.check(data)}


# --- notes --------------------------------------------------------------------------------------------------------------------


def test_the_notes_are_numbered_with_the_thickness_first() -> None:
    ctx, _ = plate(one_view=True)
    draw(
        ctx,
        {"type": "note", "id": "n1", "text": "Fabricar de lámina rectificada"},
        {"type": "note", "id": "tn", "auto": "thickness"},
        {"type": "note", "id": "n2", "text": "Rebabas eliminadas"},
    )
    notes = [t for t in ctx.sheet.texts() if t[:2] in ("1.", "2.", "3.", "4.")]
    assert sorted(notes) == [
        "1. 19 de espesor",
        "2. Fabricar de lámina rectificada",
        "3. Rebabas eliminadas",
    ]


def test_the_iso_286_note_is_no_longer_a_note_a_sheet_writes() -> None:
    with pytest.raises(sc.ItemError, match="auto"):
        items.normalise({"type": "note", "auto": "iso286"}, set(), 1)


def test_an_auto_note_takes_no_text_and_a_note_without_either_is_refused() -> None:
    with pytest.raises(sc.ItemError, match="takes no text"):
        items.normalise({"type": "note", "auto": "thickness", "text": "x"}, set(), 1)
    with pytest.raises(sc.ItemError, match="text is required"):
        items.normalise({"type": "note"}, set(), 1)


def test_a_flat_part_whose_holes_go_through_the_thickness_is_suggested_one_view() -> None:
    ctx, results = sheet_of()
    ctx.extras["units"] = []
    data = review.ReviewInput(ctx.extras["items"], {r.id: r for r in results}, ctx, ctx.sheet, [])
    found = [f for f in quality.check(data) if f.code == "single_view_enough"]
    assert found and found[0].severity == "info" and "UNA vista" in found[0].message
    one, one_results = sheet_of(one_view=True)
    one.extras["units"] = []
    assert not [
        f
        for f in quality.check(
            review.ReviewInput(one.extras["items"], {r.id: r for r in one_results}, one, one.sheet, [])
        )
        if f.code == "single_view_enough"
    ]


# --- layout, papers, angle -------------------------------------------------------------------------------------------------------


def test_the_rows_of_the_holes_take_room_and_the_scale_still_grows_to_the_largest_that_fits() -> None:
    plain = lay.plan(WIDTH, DEPTH, HEIGHT, 297.0, 210.0)
    rows = lay.plan(WIDTH, DEPTH, HEIGHT, 297.0, 210.0, below_mm=43.0, left_mm=29.0)
    assert rows.scale <= plain.scale and rows.fits
    # the room for callouts is a comfort: a smaller scale than the sheet has without it is never paid for it
    roomy = lay.plan(WIDTH, DEPTH, HEIGHT, 297.0, 210.0, below_mm=43.0, left_mm=29.0, right_mm=24.0, spacing_mm=34.0)
    assert roomy.scale == rows.scale


def test_third_angle_puts_the_top_view_above_and_a_single_view_takes_the_front_place() -> None:
    first, third = lay.plan(WIDTH, DEPTH, HEIGHT, *PAGE), lay.plan(WIDTH, DEPTH, HEIGHT, *PAGE, third=True)
    assert third.front[1] > first.front[1]  # the front view hangs lower: the top view is above it (y down)
    one = lay.plan(WIDTH, 0.0, DEPTH, *PAGE, None, None, True, False, False, iso_dims=(WIDTH, DEPTH, HEIGHT))
    assert one.scale >= first.scale


@pytest.mark.parametrize("size", list(gen.PAGES_MM))
@pytest.mark.parametrize("language", ["es", "en"])
@pytest.mark.parametrize("angle", ["first", "third"])
def test_every_paper_has_its_template_with_the_same_fields(size: str, language: str, angle: str) -> None:
    svg = gen.build(size, language, angle)
    width, height, entities = tpl.parse(svg, {"title": "Placa", "part_material": "Acero"})
    assert (width, height) == gen.PAGES_MM[size]
    assert tpl.editable_fields(svg) == tpl.editable_fields(gen.build("A3", "es", "first"))
    assert any(isinstance(e, model.Text) and e.text == "Acero" for e in entities)
    block = tpl.title_block_rect(entities, width)
    assert block is not None and block[2] <= width - 5.0 and block[1] >= 9.0


def test_the_generated_a3_is_the_shipped_one_and_the_third_angle_symbol_is_the_mirror() -> None:
    shipped = (WORKER_DIR / "templates" / "A3_es.svg").read_text(encoding="utf-8")
    mine = gen.build("A3", "es", "first")
    assert tpl.editable_fields(shipped) == tpl.editable_fields(mine)
    _, _, a = tpl.parse(shipped, {})
    _, _, b = tpl.parse(mine, {})
    assert len(a) == len(b) and tpl.title_block_rect(a, 420.0) == tpl.title_block_rect(b, 420.0)

    def circles(svg: str) -> list[float]:
        return [e.cx for e in tpl.parse(svg, {})[2] if isinstance(e, model.Circle) and e.r in (2.5, 5.0)]

    assert min(circles(gen.build("A3", "es", "first"))) > max(circles(gen.build("A3", "es", "third"))) - 1.0
    assert circles(gen.build("A3", "es", "first")) != circles(gen.build("A3", "es", "third"))


def test_the_fixed_notes_are_three_editable_fields_of_every_template() -> None:
    for size in ("A4", "A3", "A0", "Letter"):
        names = tpl.editable_fields(gen.build(size, "es", "first"))
        assert {"fixed_note_1", "fixed_note_2", "fixed_note_3"} <= set(names)


def test_segments_cross_only_when_they_cross_and_not_when_they_meet_at_an_end() -> None:
    assert collide.segments_cross((0, 0, 10, 10), (0, 10, 10, 0))
    assert not collide.segments_cross((0, 0, 10, 10), (10, 10, 20, 0))  # meet at an end
    assert not collide.segments_cross((0, 0, 10, 0), (0, 5, 10, 5))  # parallel
    assert not collide.segments_cross((0, 0, 10, 0), (0, 0, 10, 0))  # the same line: an overlap, not a crossing
