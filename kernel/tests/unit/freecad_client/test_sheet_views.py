# ruff: noqa: E501
"""Special views, hidden / centre lines and the sheet review (``items_views.py``, ``sheet_review.py``) against the
hand-made plate of ``test_sheet_items``: no FreeCAD. The projections are canned (a rectangle with a hole), so what is
checked is what the items do with them: letters, hatching, the cutting-plane line, clipping, placement and the findings."""

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
import sheet_review as review  # noqa: E402

from .test_sheet_items import PlateGeometry, draw, hole, make_context  # noqa: E402


class ViewsGeometry(PlateGeometry):
    """The plate seen from anywhere is a 100 x 8 rectangle (a 6.6 hole through it); a cut of it has two hatched pieces."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[Any, ...]] = []

    def projection(self, centre: sc.Vec3, xdir: sc.Vec3, direction: sc.Vec3, cut: Any = None) -> sc.Projection:
        self.calls.append((centre, xdir, direction, cut))
        visible: list[model.Entity] = [
            model.Line(-50, -4, 50, -4),
            model.Line(50, -4, 50, 4),
            model.Line(50, 4, -50, 4),
            model.Line(-50, 4, -50, -4),
            model.Circle(0, 0, 3.3),
        ]
        hidden: list[model.Entity] = [
            model.Line(-3.3, -4, -3.3, 4, model.HIDDEN),
            model.Line(3.3, -4, 3.3, 4, model.HIDDEN),
        ]
        loops = [
            [(-50.0, -4.0), (-3.3, -4.0), (-3.3, 4.0), (-50.0, 4.0)],
            [(3.3, -4.0), (50.0, -4.0), (50.0, 4.0), (3.3, 4.0)],
        ]
        return sc.Projection(visible, [] if cut else hidden, loops if cut else [])

    def face_outline(self, face_id: str, centre: sc.Vec3, xdir: sc.Vec3, direction: sc.Vec3) -> list[list[sc.Point]]:
        return [[(-20.0, -5.0), (20.0, -5.0), (20.0, 5.0), (-20.0, 5.0)]]


def views_context(holes: list[sc.HoleInfo] | None = None) -> sc.Context:
    ctx = make_context(holes)
    ctx.geometry = ViewsGeometry()
    ctx.area = (25.0, 60.0, 405.0, 282.0)
    ctx.extras["view_boxes"] = [v.box for v in ctx.views.values()]
    return ctx


def results_of(ctx: sc.Context, *specs: dict[str, Any]) -> list[sc.ItemResult]:
    return draw(ctx, *specs)


# --- registry ---------------------------------------------------------------------------------------------------------


def test_the_four_new_types_are_registered_and_phased_after_the_dimensions() -> None:
    for kind in ("view_lines", "section", "detail", "aux_view"):
        assert kind in items.REGISTRY and kind not in items.PLANNED
    assert items.REGISTRY["view_lines"].phase == 0
    assert all(items.REGISTRY[k].phase > 2 for k in ("section", "detail", "aux_view"))


@pytest.mark.parametrize(
    ("spec", "text"),
    [
        ({"type": "section"}, "section needs holes"),
        ({"type": "section", "holes": "all", "at": [0, 0, 0]}, "either holes"),
        ({"type": "section", "at": [0, 0, 0], "normal": [0, 0, 0]}, "zero vector"),
        ({"type": "section", "holes": "all", "letter": "I"}, "I, O and Q"),
        ({"type": "detail", "at": [0, 0, 0]}, "radius_mm is required"),
        ({"type": "detail", "at": [0, 0, 0], "radius_mm": -1}, "radius_mm"),
        ({"type": "aux_view"}, "face is required"),
        ({"type": "aux_view", "face": ["face:1", "face:2"]}, "one face id"),
        ({"type": "view_lines", "hidden": "yes"}, "hidden"),
    ],
)
def test_bad_items_say_what_to_change(spec: dict[str, Any], text: str) -> None:
    with pytest.raises(sc.ItemError, match=text):
        items.normalise(spec, set(), 1)


# --- section ----------------------------------------------------------------------------------------------------------


def test_a_section_through_the_holes_is_hatched_labelled_and_drawn_with_its_cutting_line() -> None:
    ctx = views_context([hole(0, 10, 10), hole(1, 90, 10)])
    (result,) = results_of(ctx, {"type": "section", "holes": "all", "view": "top"})
    assert result.status == "ok", result.detail
    assert "A-A in the top view" in result.detail
    assert "A-A" in ctx.sheet.texts()
    hatch = ctx.sheet.of_layer(model.HATCH)
    assert hatch and all(isinstance(e, model.Line) for e in hatch)
    for line in hatch:  # 45 degrees on the page
        assert math.isclose(abs(line.x2 - line.x1), abs(line.y2 - line.y1), abs_tol=1e-6)
    thick = [
        e
        for e in ctx.sheet.of_layer(model.CONTOUR)
        if isinstance(e, model.Line) and math.isclose(e.y1, 80.0) and e.y1 == e.y2
    ]
    assert len(thick) >= 2  # the two thick ends of the cutting-plane line, on the line through the holes
    assert any(isinstance(e, model.Line) and e.layer == model.AXES for e in ctx.sheet.entities)  # thin chain between
    assert ctx.sheet.texts().count("A") == 2  # one letter at each end of the line
    assert sum(isinstance(e, model.Solid) for e in ctx.sheet.entities) == 2  # the arrowheads


def test_the_cut_view_takes_free_room_clear_of_every_view() -> None:
    ctx = views_context([hole(0, 10, 10), hole(1, 90, 10)])
    results_of(ctx, {"type": "section", "holes": "all", "view": "top"})
    rect = ctx.extras["view_rects"]["s1"]
    for box in ctx.extras["view_boxes"]:
        assert not (rect[0] < box[2] and box[0] < rect[2] and rect[1] < box[3] and box[1] < rect[3]), (rect, box)
    assert ctx.area[0] <= rect[0] and rect[2] <= ctx.area[2] and ctx.area[1] <= rect[1] and rect[3] <= ctx.area[3]


def test_the_view_sees_the_cut_from_the_side_that_does_not_mirror_the_part_unless_flipped() -> None:
    ctx = views_context([hole(0, 10, 10), hole(1, 90, 10)])
    results_of(ctx, {"type": "section", "holes": "all", "view": "top"})
    _, xdir, direction, cut = ctx.geometry.calls[-1]
    assert sc.dot(xdir, ctx.views["top"].xdir) > 0 and cut is not None
    assert sc.dot(direction, (0, -1, 0)) > 0.99  # the viewer stands at -y (the front), as for the front view
    flipped = views_context([hole(0, 10, 10), hole(1, 90, 10)])
    results_of(flipped, {"type": "section", "holes": "all", "view": "top", "flip": True})
    assert sc.dot(flipped.geometry.calls[-1][1], ctx.views["top"].xdir) < 0


def test_letters_run_on_skipping_the_ones_asked_for() -> None:
    ctx = views_context([hole(0, 10, 10), hole(1, 90, 10)])
    results = results_of(
        ctx,
        {"type": "section", "holes": "all", "view": "top"},
        {"type": "section", "holes": "all", "view": "top", "letter": "C", "flip": True},
        {"type": "section", "holes": "all", "view": "top", "flip": True, "at_mm": [300, 100]},
    )
    assert [r.status for r in results] == ["ok", "ok", "ok"], [r.detail for r in results]
    labels = [t for t in ctx.sheet.texts() if "-" in t]
    assert sorted(labels) == ["A-A", "B-B", "C-C"]


def test_holes_that_are_not_on_one_line_name_the_fix() -> None:
    ctx = views_context()
    (result,) = results_of(ctx, {"type": "section", "holes": "all", "view": "top"})
    assert result.status == "error" and "not on one straight line" in result.detail and "holes=[...]" in result.detail
    assert not ctx.sheet.entities  # nothing was left on the sheet


def test_a_plane_not_seen_edge_on_is_refused() -> None:
    ctx = views_context()
    (result,) = results_of(ctx, {"type": "section", "at": [50, 30, 4], "normal": [0, 0, 1], "view": "top"})
    assert result.status == "error" and "seen edge-on there" in result.detail


def test_a_section_with_an_explicit_plane_goes_below_or_where_at_mm_says() -> None:
    ctx = views_context()
    (result,) = results_of(
        ctx, {"type": "section", "at": [50, 30, 4], "normal": [0, -1, 0], "view": "top", "at_mm": [300, 150]}
    )
    assert result.status == "ok", result.detail
    rect = ctx.extras["view_rects"]["s1"]
    assert rect[0] < 300 < rect[2] and rect[1] < 150 < rect[3]


def test_a_crowded_sheet_says_where_to_look() -> None:
    ctx = views_context([hole(0, 10, 10), hole(1, 90, 10)])
    ctx.area = (25.0, 80.0, 60.0, 100.0)  # no room left
    (result,) = results_of(ctx, {"type": "section", "holes": "all", "view": "top"})
    assert result.status == "error" and "at_mm" in result.detail


# --- detail -----------------------------------------------------------------------------------------------------------


def test_a_detail_is_the_inside_of_its_circle_enlarged_and_labelled() -> None:
    ctx = views_context()
    (result,) = results_of(ctx, {"type": "detail", "view": "front", "at": [100, 30, 8], "radius_mm": 5})
    assert result.status == "ok", result.detail
    assert "A (5:1)" in ctx.sheet.texts() and "A" in ctx.sheet.texts()
    rect = ctx.extras["view_rects"]["e1"]
    circles = [e for e in ctx.sheet.of_layer(model.DIMENSIONS) if isinstance(e, model.Circle)]
    big = max(circles, key=lambda c: c.r)
    assert math.isclose(big.r, 25.0)  # 5 mm at 5:1
    inside = [e for e in ctx.sheet.of_layer(model.CONTOUR) if isinstance(e, model.Line) and rect[0] < e.x1 < rect[2]]
    for e in inside:
        assert (
            math.hypot(e.x1 - big.cx, e.y1 - big.cy) <= 25.0 + 1e-6
            and math.hypot(e.x2 - big.cx, e.y2 - big.cy) <= 25.0 + 1e-6
        )
    small = min(circles, key=lambda c: c.r)
    assert math.isclose(small.r, 5.0)  # the same circle at the parent's own scale


def test_a_detail_that_would_not_enlarge_or_that_is_empty_is_refused() -> None:
    ctx = views_context()
    (slow,) = results_of(ctx, {"type": "detail", "at": [100, 30, 8], "radius_mm": 5, "scale": 0.5})
    assert slow.status == "error" and "must be bigger" in slow.detail
    (empty,) = results_of(views_context(), {"type": "detail", "at": [1000, 1000, 8], "radius_mm": 0.5})
    assert empty.status == "error" and "nothing of the part" in empty.detail


# --- auxiliary view ---------------------------------------------------------------------------------------------------


def test_an_auxiliary_view_is_partial_lettered_and_pointed_at_from_the_parent() -> None:
    ctx = views_context()
    (result,) = results_of(ctx, {"type": "aux_view", "face": "face:slope"})
    assert result.status == "ok", result.detail
    assert "view A square-on to face:slope" in result.detail
    assert ctx.sheet.texts().count("A") == 2  # the label of the view and the letter of its arrow
    _, xdir, direction, _ = ctx.geometry.calls[-1]
    assert sc.dot(direction, (0.7071, 0.0, 0.7071)) > 0.999  # looks at the face from outside
    shown = ctx.sheet.of_layer(model.CONTOUR)
    rect = ctx.extras["view_rects"]["x1"]
    assert shown and all(rect[0] - 1 <= e.x1 <= rect[2] + 1 for e in shown if isinstance(e, model.Line) and e.x1 > 200)


def test_an_auxiliary_view_of_a_face_a_standard_view_shows_square_on_is_refused() -> None:
    ctx = views_context()
    (result,) = results_of(ctx, {"type": "aux_view", "face": "face:top"})
    assert result.status == "error" and "already shows it square-on" in result.detail
    (cyl,) = results_of(views_context(), {"type": "aux_view", "face": "face:fillet"})
    assert cyl.status == "error" and "PLANE" in cyl.detail


# --- hidden edges and centre lines ----------------------------------------------------------------------------------------


def test_view_lines_draws_hidden_edges_in_the_elevations_and_axes_on_every_hole() -> None:
    ctx = views_context()
    (result,) = results_of(ctx, {"type": "view_lines"})
    assert result.status == "ok", result.detail
    hidden = ctx.sheet.of_layer(model.HIDDEN)
    assert len(hidden) == 4  # two edges in each of front and right, none in top
    assert ctx.extras["hidden_views"] == {"front", "right"}
    axes = ctx.sheet.of_layer(model.AXES)
    crosses = [e for e in axes if isinstance(e, model.Line)]
    assert len(crosses) >= 8  # a cross (two lines) on each of the four holes in the top view, plus the side-on axes


def test_a_hole_callout_does_not_double_the_centre_marks_view_lines_drew() -> None:
    ctx = views_context()
    results_of(ctx, {"type": "view_lines", "hidden": False}, {"type": "hole_dim", "all": True})
    top = ctx.views["top"]
    marks = [
        e
        for e in ctx.sheet.of_layer(model.AXES)
        if isinstance(e, model.Line) and abs(e.y1 - e.y2) < 1e-9 and top.box[1] < e.y1 < top.box[3]
    ]
    assert len(marks) == 4  # one horizontal bar per hole, not two


def test_view_lines_can_pick_the_views_and_turn_centres_off() -> None:
    ctx = views_context()
    results_of(ctx, {"type": "view_lines", "hidden": ["top"], "centres": False})
    assert len(ctx.sheet.of_layer(model.HIDDEN)) == 2 and not ctx.sheet.of_layer(model.AXES)


# --- the review -------------------------------------------------------------------------------------------------------


def run_review(
    ctx: sc.Context, *specs: dict[str, Any], features: list[review.FeatureInfo] | None = None
) -> list[review.Finding]:
    taken: set[str] = set()
    normalised = []
    for number, spec in enumerate(specs, start=1):
        done = items.normalise(spec, taken, number)
        taken.add(done["id"])
        normalised.append(done)
    results = items.draw_all(ctx, normalised)
    data = review.ReviewInput(normalised, {r.id: r for r in results}, ctx, ctx.sheet, features or [])
    return review.review(data)


def codes(found: list[review.Finding]) -> list[str]:
    return [f.code for f in found]


def test_holes_nobody_calls_out_or_locates_are_findings() -> None:
    found = run_review(views_context(), {"type": "overall"})
    assert codes(found).count("hole_no_callout") == 4 and codes(found).count("hole_not_located") == 4
    assert all(f.severity == "warning" for f in found if f.code.startswith("hole_"))
    assert "no_view_lines" in codes(found)


def test_a_called_out_and_ordinate_dimensioned_plate_is_clean_of_hole_findings() -> None:
    found = run_review(
        views_context(),
        {"type": "overall"},
        {"type": "view_lines"},
        {"type": "hole_dim", "all": True},
        {"type": "datum_dims"},
    )
    # what is left is the finished-drawing list (notes), asked about elsewhere: no finding is about a hole or the sheet
    # (and a view the plate has that none of these dimensions: the right one)
    assert [(f.code, f.items) for f in found if f.severity == "warning" and f.code != "notes_material"] == [
        ("view_adds_nothing", ("right",))
    ], found


def test_a_threaded_hole_without_its_callout_is_named_and_an_undeclared_one_is_flagged() -> None:
    ctx = views_context([hole(0, 10, 10, thread=("M6", 8.0)), hole(1, 90, 10, 5.0, declared=False)])
    found = run_review(ctx, {"type": "hole_dim", "at": [[90, 10, 8]]}, {"type": "datum_dims"})
    assert "thread_no_callout" in codes(found)
    undeclared = [f for f in found if f.code == "thread_undeclared"]
    assert undeclared and undeclared[0].severity == "info" and undeclared[0].items == ("h1",)


def test_an_ordinate_set_plus_a_chain_on_the_same_holes_is_over_dimensioned() -> None:
    ctx = views_context([hole(0, 10, 10), hole(1, 90, 10)])
    found = run_review(
        ctx,
        {"type": "datum_dims", "id": "g1"},
        {
            "type": "dim",
            "id": "d1",
            "kind": "distance",
            "from": {"at": [10, 10, 8]},
            "to": {"at": [90, 10, 8]},
            "along": "x",
        },
        {
            "type": "dim",
            "id": "d2",
            "kind": "distance",
            "from": {"at": [10, 10, 8]},
            "to": {"at": [90, 10, 8]},
            "along": "x",
        },
    )
    over = [f for f in found if f.code == "over_dimensioned"]
    assert {f.items for f in over} == {("g1", "d1"), ("g1", "d2")}
    assert [f.items for f in found if f.code == "duplicate_dimension"] == [("d1", "d2")]


def test_a_chain_that_closes_on_the_overall_size_is_over_dimensioned() -> None:
    ctx = views_context([hole(0, 10, 10), hole(1, 90, 10)])
    found = run_review(
        ctx,
        {"type": "overall"},
        {"type": "dim", "id": "a", "kind": "distance", "from": {"face": "face:left"}, "to": {"at": [10, 10, 8]}},
        {
            "type": "dim",
            "id": "b",
            "kind": "distance",
            "from": {"at": [10, 10, 8]},
            "to": {"at": [90, 10, 8]},
            "along": "x",
        },
        {"type": "dim", "id": "c", "kind": "distance", "from": {"at": [90, 10, 8]}, "to": {"face": "face:right"}},
    )
    chain = [f for f in found if f.code == "over_dimensioned"]
    assert chain and chain[0].items == ("a", "b", "c")


def test_a_slot_with_a_size_but_no_position_is_not_located() -> None:
    ctx = views_context([])
    feature = review.FeatureInfo("feature:7", "cut", "slot", ["face:fillet", "face:back"])
    found = run_review(ctx, {"type": "dim", "id": "r1", "kind": "radius", "face": "face:fillet"}, features=[feature])
    located = [f for f in found if f.code == "feature_not_located"]
    assert located and located[0].features == ("feature:7",) and located[0].items == ("r1",)
    bare = run_review(views_context([]), {"type": "overall"}, features=[feature])
    assert "feature_no_dimension" in codes(bare)


def test_a_dimension_tying_the_slot_to_an_edge_locates_it() -> None:
    feature = review.FeatureInfo("feature:7", "cut", "slot", ["face:fillet", "face:back"])
    found = run_review(
        views_context([]),
        {"type": "dim", "kind": "radius", "face": "face:fillet"},
        {"type": "dim", "kind": "distance", "from": {"face": "face:front"}, "to": {"face": "face:back"}},
        features=[feature],
    )
    assert not [f for f in found if f.code.startswith("feature_")], found


def test_items_that_cannot_be_drawn_are_findings_with_their_ids() -> None:
    found = run_review(
        views_context(),
        {"type": "dim", "id": "d9", "kind": "radius", "face": "face:gone"},
        {"type": "dim", "id": "d8", "kind": "distance", "from": {"at": [50, 30, 80]}, "to": {"at": [10, 10, 8]}},
        {"type": "hole_dim", "id": "h9", "at": [[1, 2, 3]]},
    )
    by_code = {f.code: f.items for f in found if f.items}
    assert by_code["stale_reference"] == ("d9",)
    assert "d8" in {i for f in found if f.code == "dim_no_reference" for i in f.items}
    assert "h9" in {i for f in found if f.code == "dim_no_reference" for i in f.items}


def test_views_over_the_title_block_or_each_other_or_the_frame_are_findings() -> None:
    ctx = views_context()
    ctx.extras["view_rects"] = {
        "s1": (100.0, 150.0, 200.0, 170.0),  # on the top view
        "s2": (30.0, 20.0, 60.0, 60.0),  # in the title block area
        "s3": (10.0, 150.0, 40.0, 190.0),  # left of the frame
    }
    found = run_review(
        ctx, {"type": "overall"}, {"type": "view_lines"}, {"type": "hole_dim", "all": True}, {"type": "datum_dims"}
    )
    got = codes(found)
    assert "views_overlap" in got and "view_over_title_block" in got and "view_outside_frame" in got


def test_text_outside_the_frame_is_a_finding() -> None:
    ctx = views_context()
    ctx.sheet.add(model.Text(417.0, 100.0, "fuera", 3.5, model.TEXT, "left"))
    found = run_review(
        ctx, {"type": "overall"}, {"type": "view_lines"}, {"type": "hole_dim", "all": True}, {"type": "datum_dims"}
    )
    assert "text_outside_frame" in codes(found)


def test_datums_a_frame_cites_must_be_defined() -> None:
    ctx = views_context()
    specs = [{"id": "f1", "type": "gdt", "datums": ["A", "B"]}, {"id": "d1", "type": "datum", "letter": "A"}]
    data = review.ReviewInput(
        specs, {"f1": sc.ItemResult("f1", "gdt"), "d1": sc.ItemResult("d1", "datum")}, ctx, ctx.sheet
    )
    (finding,) = review._datums(data)
    assert finding.code == "datum_undefined" and finding.items == ("f1",) and "B" in finding.message
    specs.append({"id": "d2", "type": "datum", "letter": "B"})
    data.results["d2"] = sc.ItemResult("d2", "datum")
    assert review._datums(data) == []
