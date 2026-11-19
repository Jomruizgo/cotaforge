"""The sheet items (``worker/sheet_items.py`` and ``items_*.py``) against a hand-made context: a 100 x 60 x 8 plate with
four D6.6 through holes, seen in a front, a top and a right view at 1:1. No FreeCAD: the values must come out of the
geometry the context describes, and the placement must be deterministic."""

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

HOLE_CENTRES = [(10.0, 10.0), (90.0, 10.0), (10.0, 50.0), (90.0, 50.0)]


class PlateGeometry(sc.Geometry):
    """Faces, edges and chamfers of the test plate, by hand."""

    def __init__(self) -> None:
        self.faces = {
            "face:left": sc.FaceInfo("face:left", "plane", (0.0, 30.0, 4.0), normal=(-1.0, 0.0, 0.0)),
            "face:right": sc.FaceInfo("face:right", "plane", (100.0, 30.0, 4.0), normal=(1.0, 0.0, 0.0)),
            "face:front": sc.FaceInfo("face:front", "plane", (50.0, 0.0, 4.0), normal=(0.0, -1.0, 0.0)),
            "face:back": sc.FaceInfo("face:back", "plane", (50.0, 60.0, 4.0), normal=(0.0, 1.0, 0.0)),
            "face:top": sc.FaceInfo("face:top", "plane", (50.0, 30.0, 8.0), normal=(0.0, 0.0, 1.0)),
            "face:bottom": sc.FaceInfo("face:bottom", "plane", (50.0, 30.0, 0.0), normal=(0.0, 0.0, -1.0)),
            "face:slope": sc.FaceInfo(
                "face:slope", "plane", (50.0, 30.0, 4.0), normal=(0.7071067811865476, 0.0, 0.7071067811865476)
            ),
            "face:fillet": sc.FaceInfo(
                "face:fillet",
                "cylinder",
                (0.0, 0.0, 4.0),
                radius=5.0,
                axis_point=(5.0, 5.0, 4.0),
                axis_dir=(0.0, 0.0, 1.0),
                surface_point=(1.4644660940672627, 1.4644660940672627, 4.0),
            ),
            "face:dome": sc.FaceInfo("face:dome", "other", (0.0, 0.0, 0.0)),
        }
        self.edges = {
            "edge:hole1": sc.EdgeInfo(
                "edge:hole1", "circle", centre=(10.0, 10.0, 8.0), radius=3.3, axis_dir=(0.0, 0.0, 1.0)
            ),
            "edge:hole2": sc.EdgeInfo(
                "edge:hole2", "circle", centre=(90.0, 10.0, 8.0), radius=3.3, axis_dir=(0.0, 0.0, 1.0)
            ),
            "edge:top": sc.EdgeInfo("edge:top", "line", start=(0.0, 0.0, 8.0), end=(100.0, 0.0, 8.0)),
            "edge:wavy": sc.EdgeInfo("edge:wavy", "spline"),
        }

    def face(self, face_id: str) -> sc.FaceInfo:
        if face_id not in self.faces:
            raise sc.ItemError(f"{face_id} is not a face of the part")
        return self.faces[face_id]

    def edge(self, edge_id: str) -> sc.EdgeInfo:
        if edge_id not in self.edges:
            raise sc.ItemError(f"{edge_id} is not an edge of the part")
        return self.edges[edge_id]

    def on_part(self, p: sc.Vec3) -> sc.Vec3 | None:
        inside = all(lo - 0.05 <= c <= hi + 0.05 for c, lo, hi in zip(p, (0, 0, 0), (100, 60, 8), strict=True))
        on_boundary = any(abs(c - b) <= 0.05 for c, bs in zip(p, ((0, 100), (0, 60), (0, 8)), strict=True) for b in bs)
        return p if inside and on_boundary else None

    def chamfer(self, face_id: str) -> sc.ChamferInfo:
        if face_id != "face:slope":
            raise sc.ItemError(f"{face_id} is not a flat chamfer")
        return sc.ChamferInfo(face_id, 3.0, 2.0, 33.690067525979785, (0.0, 1.0, 0.0), (98.5, 30.0, 7.0))


def make_context(holes: list[sc.HoleInfo] | None = None, language: str = "es") -> sc.Context:
    views = {
        "front": sc.ViewInfo(
            "front", 150.0, 160.0, 1.0, (0, -1, 0), (1, 0, 0), (50, 30, 4), (100.0, 156.0, 200.0, 164.0)
        ),
        "top": sc.ViewInfo("top", 150.0, 100.0, 1.0, (0, 0, 1), (1, 0, 0), (50, 30, 4), (100.0, 70.0, 200.0, 130.0)),
        "right": sc.ViewInfo("right", 50.0, 160.0, 1.0, (1, 0, 0), (0, 1, 0), (50, 30, 4), (20.0, 156.0, 80.0, 164.0)),
    }
    if holes is None:
        holes = [
            sc.HoleInfo(i, (x, y, 8.0), (0.0, 0.0, -1.0), 6.6, 8.0, True, (f"face:h{i}",), None, True)
            for i, (x, y) in enumerate(HOLE_CENTRES)
        ]
    return sc.Context(
        sheet=model.Sheet(420.0, 297.0),
        views=views,
        holes=holes,
        geometry=PlateGeometry(),
        area=(25.0, 80.0, 405.0, 282.0),
        language=language,
    )


def hole(
    index: int,
    x: float,
    y: float,
    diameter: float = 6.6,
    depth: float = 8.0,
    through: bool | None = True,
    thread: tuple[str, float | None] | None = None,
    declared: bool = True,
) -> sc.HoleInfo:
    return sc.HoleInfo(
        index, (x, y, 8.0), (0.0, 0.0, -1.0), diameter, depth, through, (f"face:h{index}",), thread, declared
    )


def draw(ctx: sc.Context, *specs: dict[str, Any]) -> list[sc.ItemResult]:
    taken: set[str] = set()
    normalised = []
    for number, spec in enumerate(specs, start=1):
        done = items.normalise(spec, taken, number)
        taken.add(done["id"])
        normalised.append(done)
    return items.draw_all(ctx, normalised)


def texts(ctx: sc.Context) -> list[str]:
    return ctx.sheet.texts()


# --- the registry -----------------------------------------------------------------------------------------------------


def test_the_registry_has_the_built_types_and_names_the_planned_ones() -> None:
    assert set(items.REGISTRY) == {
        "hole_dim",
        "hole_positions",
        "pattern_dim",
        "line",
        "dim",
        "datum_dims",
        "note",
        "revision",
        "overall",
        "view_lines",
        "section",
        "detail",
        "aux_view",
        "datum",
        "gdt",
        "parts_list",
        "balloons",
        "exploded",
        "finish",
        "weld",
        "parting_line",
        "pull_direction",
        "mould_zone",
        "no_draft",
    }
    assert not set(items.PLANNED) & set(items.REGISTRY)
    assert not set(items.UNSUPPORTED) & set(items.REGISTRY)


def test_a_weld_needs_the_two_bodies_and_the_size() -> None:
    with pytest.raises(sc.ItemError, match=r"two bodies that touch"):
        items.normalise({"type": "weld", "between": [], "size_mm": 3}, set(), 1)
    with pytest.raises(sc.ItemError, match=r"size_mm is required"):
        items.normalise({"type": "weld", "between": ["body:1", "body:2"]}, set(), 1)
    assert "weld" in items.all_types() and "weld" in items.REGISTRY


def test_an_unknown_type_an_unknown_field_and_a_bad_id_are_refused() -> None:
    with pytest.raises(sc.ItemError, match="unknown item type 'blob'"):
        items.normalise({"type": "blob"}, set(), 1)
    with pytest.raises(sc.ItemError, match="unknown field"):
        items.normalise({"type": "hole_dim", "all": True, "colour": "red"}, set(), 1)
    with pytest.raises(sc.ItemError, match="id"):
        items.normalise({"type": "hole_dim", "all": True, "id": "9 bad"}, set(), 1)
    with pytest.raises(sc.ItemError, match="already on this sheet"):
        items.normalise({"type": "hole_dim", "all": True, "id": "h1"}, {"h1"}, 1)


def test_ids_are_generated_and_skip_the_ones_taken() -> None:
    assert items.normalise({"type": "hole_dim", "all": True}, set(), 1)["id"] == "h1"
    assert items.normalise({"type": "hole_dim", "all": True}, {"h1", "h2"}, 1)["id"] == "h3"


def test_every_type_lists_its_fields_for_the_contract_to_compare() -> None:
    for name, kind in items.REGISTRY.items():
        assert items.fields_of(name) == kind.fields | {"type", "id"}
    assert "at" in items.fields_of("hole_dim") and "kind" in items.fields_of("dim")


# --- hole_dim ---------------------------------------------------------------------------------------------------------


def test_equal_holes_share_one_callout_with_their_count() -> None:
    ctx = make_context()
    (result,) = draw(ctx, {"type": "hole_dim", "all": True})
    assert result.status == "ok", result.detail
    assert texts(ctx) == ["4× Ø6,6 pasante"]
    assert "top: 4× Ø6,6 pasante" in result.detail
    circles_axes = [e for e in ctx.sheet.of_layer(model.AXES) if isinstance(e, model.Line)]
    assert len(circles_axes) == 8  # a cross on each of the four holes


def test_the_callout_in_english_and_with_a_point_decimal() -> None:
    ctx = make_context(language="en")
    draw(ctx, {"type": "hole_dim", "all": True})
    assert texts(ctx) == ["4× Ø6.6 thru"]


def test_different_holes_get_one_callout_each_and_a_blind_hole_says_its_depth() -> None:
    ctx = make_context(
        [hole(0, 10, 10, 8.0, 8.0, True), hole(1, 90, 10, 8.0, 8.0, True), hole(2, 50, 30, 6.0, 10.0, False)]
    )
    draw(ctx, {"type": "hole_dim", "all": True})
    assert sorted(texts(ctx)) == ["2× Ø8 pasante", "Ø6 ↧ 10"]


def test_a_tapped_hole_says_the_thread_and_the_drill_when_the_thread_is_shorter() -> None:
    ctx = make_context([hole(0, 50, 30, 5.0, 15.0, False, ("M6", 12.0))])
    draw(ctx, {"type": "hole_dim", "all": True})
    assert texts(ctx) == ["M6 ↧ 12", "taladro Ø5 ↧ 15"]
    through = make_context([hole(0, 50, 30, 5.0, 8.0, True, ("M6", None))])
    draw(through, {"type": "hole_dim", "all": True})
    assert texts(through) == ["M6 pasante"]


def test_a_counterbore_is_one_callout_of_two_lines() -> None:
    ctx = make_context([hole(0, 50, 30, 11.0, 6.4, False), hole(1, 50, 30, 6.6, 8.0, True)])
    (result,) = draw(ctx, {"type": "hole_dim", "all": True})
    assert texts(ctx) == ["Ø6,6 pasante", "⌴ Ø11 ↧ 6,4"]
    assert result.status == "ok"


def test_only_tap_drill_holes_with_no_hole_feature_say_the_thread_is_not_declared() -> None:
    ctx = make_context([hole(0, 10, 10, 5.0, declared=False), hole(1, 90, 10, 5.0, declared=False)])
    (result,) = draw(ctx, {"type": "hole_dim", "all": True})
    assert result.status == "ok" and "rosca no declarada" in result.detail and "M6" in result.detail
    assert texts(ctx) == ["2× Ø5 pasante"]  # the drawing itself does not claim anything about a thread
    ctx = make_context([hole(0, 10, 10, declared=False), hole(1, 90, 10, declared=False)])  # Ø6,6: a clearance hole
    (clear,) = draw(ctx, {"type": "hole_dim", "all": True})
    assert clear.status == "ok" and "rosca" not in clear.detail


def test_holes_are_found_by_a_point_on_their_axis_within_005_mm() -> None:
    ctx = make_context()
    (ok,) = draw(ctx, {"type": "hole_dim", "at": [10.04, 10.0, 3.0]})
    assert ok.status == "ok" and texts(ctx) == ["Ø6,6 pasante"]
    ctx = make_context()
    (bad,) = draw(ctx, {"type": "hole_dim", "at": [10.1, 10.0, 0.0]})
    assert bad.status == "error" and "no hole has its axis through" in bad.detail
    assert ctx.sheet.entities == []  # nothing is left behind by an item that failed


def test_holes_are_found_by_their_face_id_and_a_wrong_face_says_so() -> None:
    ctx = make_context()
    (ok,) = draw(ctx, {"type": "hole_dim", "face": ["face:h0", "face:h1"]})
    assert ok.status == "ok" and texts(ctx) == ["2× Ø6,6 pasante"]
    (bad,) = draw(make_context(), {"type": "hole_dim", "face": "face:99"})
    assert bad.status == "error" and "not the wall of a hole" in bad.detail


def test_hole_dim_needs_exactly_one_way_to_name_the_holes() -> None:
    with pytest.raises(sc.ItemError, match="exactly one of"):
        items.normalise({"type": "hole_dim"}, set(), 1)
    with pytest.raises(sc.ItemError, match="exactly one of"):
        items.normalise({"type": "hole_dim", "all": True, "at": [0, 0, 0]}, set(), 1)


def test_a_hole_whose_axis_is_not_along_the_asked_view_is_reported_not_drawn() -> None:
    ctx = make_context()
    (result,) = draw(ctx, {"type": "hole_dim", "all": True, "view": "front"})
    assert result.status == "error" and "not along the direction of the front view" in result.detail


def test_the_callout_leaves_the_view_and_does_not_land_on_the_next_one() -> None:
    ctx = make_context([hole(0, 10, 10, 8.0, 8.0, True), hole(1, 12, 12, 6.0, 10.0, False)])
    draw(ctx, {"type": "hole_dim", "all": True})
    boxes = [e for e in ctx.sheet.entities if isinstance(e, model.Text)]
    top = ctx.views["top"].box
    assert all(t.x < top[0] or t.x > top[2] or t.y < top[1] or t.y > top[3] for t in boxes)  # outside the view
    a, b = boxes[0], boxes[1]
    assert abs(a.y - b.y) > 2.0 or abs(a.x - b.x) > 5.0  # the second was moved off the first


# --- dim --------------------------------------------------------------------------------------------------------------


def _dim_values(ctx: sc.Context) -> list[str]:
    return [t.text for t in ctx.sheet.entities if isinstance(t, model.Text) and t.layer == model.DIMENSIONS]


def test_a_distance_between_two_holes_is_measured_on_the_model() -> None:
    ctx = make_context()
    (result,) = draw(ctx, {"type": "dim", "kind": "distance", "from": {"at": [10, 10, 4]}, "to": {"at": [90, 10, 4]}})
    assert result.status == "ok", result.detail
    assert _dim_values(ctx) == ["80"]
    assert "80 mm along x in the front view" in result.detail or "80 mm along x in the top view" in result.detail


def test_a_distance_between_holes_goes_on_the_view_that_shows_them_as_circles() -> None:
    spec = {"type": "dim", "kind": "distance", "from": {"at": [10, 10, 4]}, "to": {"at": [90, 10, 4]}}
    (r,) = draw(make_context(), spec)
    assert "80 mm along x in the top view" in r.detail  # the front view also shows x, but the holes are circles in top
    (front,) = draw(make_context(), {**spec, "view": "front"})
    assert "in the front view" in front.detail


def test_callouts_are_drawn_after_the_dimensions_so_they_find_room_around_them() -> None:
    both = [
        {"type": "hole_dim", "all": True},
        {"type": "datum_dims"},
    ]
    first, second = make_context(), make_context()
    draw(first, *both)
    draw(second, *reversed(both))  # the order the agent added them in does not change where things go
    assert sorted(map(repr, first.sheet.entities)) == sorted(map(repr, second.sheet.entities))
    callout = next(t for t in first.sheet.entities if isinstance(t, model.Text) and "pasante" in t.text)
    for value in (t for t in first.sheet.entities if isinstance(t, model.Text) and t.layer == model.DIMENSIONS):
        assert abs(callout.x - value.x) > 1.0 or abs(callout.y - value.y) > 3.0  # not on top of an ordinate value


def test_the_distance_view_is_chosen_to_show_the_axis_or_asked_for() -> None:
    spec = {"type": "dim", "kind": "distance", "from": {"at": [10, 10, 4]}, "to": {"at": [10, 50, 4]}}
    (r,) = draw(make_context(), spec)
    assert "40 mm along y in the top view" in r.detail
    (bad,) = draw(make_context(), {**spec, "view": "front"})
    assert bad.status == "error" and "does not show the y axis" in bad.detail


def test_a_distance_between_two_faces_is_their_separation_along_the_normal() -> None:
    ctx = make_context()
    (r,) = draw(ctx, {"type": "dim", "kind": "distance", "from": {"face": "face:left"}, "to": {"face": "face:right"}})
    assert r.status == "ok" and _dim_values(ctx) == ["100"]
    ctx = make_context()
    (r,) = draw(ctx, {"type": "dim", "kind": "distance", "from": {"face": "face:left"}, "to": {"at": [90, 10, 4]}})
    assert _dim_values(ctx) == ["90"]  # a face and a hole centre


def test_a_distance_from_an_edge_to_a_hole_circle() -> None:
    ctx = make_context()
    (r,) = draw(ctx, {"type": "dim", "kind": "distance", "from": {"edge": "edge:hole1"}, "to": {"edge": "edge:hole2"}})
    assert r.status == "ok" and _dim_values(ctx) == ["80"]


def test_the_dimension_values_never_come_from_the_item() -> None:
    with pytest.raises(sc.ItemError, match="unknown field"):
        items.normalise(
            {
                "type": "dim",
                "kind": "distance",
                "value": 99,
                "from": {"face": "face:left"},
                "to": {"face": "face:right"},
            },
            set(),
            1,
        )


def test_a_point_that_is_not_on_the_part_is_refused() -> None:
    ctx = make_context()
    (r,) = draw(ctx, {"type": "dim", "kind": "distance", "from": {"at": [50, 30, 20]}, "to": {"at": [90, 10, 4]}})
    assert r.status == "error" and "is not on the part" in r.detail


def test_an_ambiguous_direction_asks_for_along() -> None:
    ctx = make_context()
    (r,) = draw(ctx, {"type": "dim", "kind": "distance", "from": {"at": [0, 0, 8]}, "to": {"at": [100, 60, 8]}})
    assert r.status == "error" and "say along=x|y|z" in r.detail
    (ok,) = draw(
        make_context(),
        {"type": "dim", "kind": "distance", "from": {"at": [0, 0, 8]}, "to": {"at": [100, 60, 8]}, "along": "y"},
    )
    assert ok.status == "ok"


def test_dimension_lines_stack_outward_in_the_order_they_are_added() -> None:
    ctx = make_context()
    draw(
        ctx,
        {"type": "dim", "kind": "distance", "from": {"face": "face:left"}, "to": {"face": "face:right"}},
        {"type": "dim", "kind": "distance", "from": {"at": [10, 10, 4]}, "to": {"at": [90, 10, 4]}, "view": "front"},
    )
    lines = [e for e in ctx.sheet.entities if isinstance(e, model.Line) and e.y1 == e.y2 and e.x1 != e.x2]
    front_top = ctx.views["front"].box[3]
    assert sorted(round(e.y1, 3) for e in lines) == [
        front_top + sc.DEFAULT_OFFSET_MM,
        front_top + sc.DEFAULT_OFFSET_MM + sc.STACK_STEP_MM,
    ]


def test_an_explicit_offset_and_side_place_the_dimension_line() -> None:
    ctx = make_context()
    draw(
        ctx,
        {
            "type": "dim",
            "kind": "distance",
            "from": {"face": "face:left"},
            "to": {"face": "face:right"},
            "view": "top",
            "side": "above",
            "offset_mm": 25,
        },
    )
    line = next(e for e in ctx.sheet.entities if isinstance(e, model.Line) and e.y1 == e.y2 and e.x1 != e.x2)
    assert line.y1 == pytest.approx(ctx.views["top"].box[3] + 25)
    (bad,) = draw(
        make_context(),
        {
            "type": "dim",
            "kind": "distance",
            "from": {"face": "face:left"},
            "to": {"face": "face:right"},
            "side": "left",
        },
    )
    assert bad.status == "error" and "above or below" in bad.detail


def test_a_radius_dimension_reads_the_cylinder() -> None:
    ctx = make_context()
    (r,) = draw(ctx, {"type": "dim", "kind": "radius", "face": "face:fillet"})
    assert r.status == "ok" and texts(ctx) == ["R5"]
    (bad,) = draw(make_context(), {"type": "dim", "kind": "radius", "face": "face:top"})
    assert bad.status == "error" and "cylindrical face" in bad.detail


def test_an_angle_between_two_faces_is_the_angle_the_sheet_shows() -> None:
    ctx = make_context()
    (r,) = draw(ctx, {"type": "dim", "kind": "angle", "faces": ["face:top", "face:right"]})
    assert r.status == "ok" and _dim_values(ctx) == ["90°"]
    ctx = make_context()
    (r,) = draw(ctx, {"type": "dim", "kind": "angle", "faces": ["face:top", "face:slope"]})
    assert r.status == "ok" and _dim_values(ctx) == ["45°"]
    assert any(isinstance(e, model.Arc) for e in ctx.sheet.entities)
    (bad,) = draw(make_context(), {"type": "dim", "kind": "angle", "faces": ["face:top", "face:bottom"]})
    assert bad.status == "error" and "parallel" in bad.detail


def test_a_chamfer_is_leg_by_angle() -> None:
    ctx = make_context()
    (r,) = draw(ctx, {"type": "dim", "kind": "chamfer", "face": "face:slope"})
    assert r.status == "ok" and texts(ctx) == ["3 × 33,7°"]
    assert "legs 3 and 2 mm" in r.detail


def test_a_chamfer_leader_ends_on_the_chamfer_line_and_goes_out_along_its_normal() -> None:
    class WithNormal(PlateGeometry):
        def chamfer(self, face_id: str) -> sc.ChamferInfo:
            info = super().chamfer(face_id)
            return sc.ChamferInfo(
                info.face,
                info.leg_a,
                info.leg_b,
                info.angle_deg,
                info.edge_dir,
                info.middle,
                (0.7071067811865476, 0.0, 0.7071067811865476),
            )

    ctx = make_context()
    ctx.geometry = WithNormal()
    (r,) = draw(ctx, {"type": "dim", "id": "c1", "kind": "chamfer", "face": "face:slope"})
    assert r.status == "ok"
    anchor = ctx.extras["anchors"]["c1"][-1]
    tip = anchor["tip"]
    assert tip == pytest.approx((198.5, 163.0))  # the middle of the chamfer line in the front view
    dx, dy = anchor["elbow"][0] - tip[0], anchor["elbow"][1] - tip[1]
    assert dx > 0 and dy > 0 and dx == pytest.approx(dy)  # along the outward normal (45 degrees), from outside
    assert ctx.extras["chamfer_marks"]["c1"][0] == "front"
    assert ctx.extras["chamfer_marks"]["c1"][2] == pytest.approx((0.7071067811865476, 0.7071067811865476))


def test_a_chamfer_with_a_side_asked_keeps_the_agents_diagonal() -> None:
    class WithNormal(PlateGeometry):
        def chamfer(self, face_id: str) -> sc.ChamferInfo:
            info = super().chamfer(face_id)
            return sc.ChamferInfo(
                info.face,
                info.leg_a,
                info.leg_b,
                info.angle_deg,
                info.edge_dir,
                info.middle,
                (0.7071067811865476, 0.0, 0.7071067811865476),
            )

    ctx = make_context()
    ctx.geometry = WithNormal()
    (r,) = draw(ctx, {"type": "dim", "id": "c1", "kind": "chamfer", "face": "face:slope", "side": "below"})
    assert r.status in ("ok", "warning")
    anchor = ctx.extras["anchors"]["c1"][-1]
    assert anchor["elbow"][1] < anchor["tip"][1]  # the engineer's side wins over the default


def test_dim_checks_its_arguments_by_kind() -> None:
    for spec, message in (
        ({"type": "dim", "kind": "distance", "from": {"at": [0, 0, 0]}}, "needs from and to"),
        ({"type": "dim", "kind": "radius"}, "needs face"),
        ({"type": "dim", "kind": "angle", "faces": ["face:1"]}, "two planar face ids"),
        ({"type": "dim"}, "kind is required"),
        (
            {"type": "dim", "kind": "distance", "from": {"at": [0, 0, 0], "face": "face:1"}, "to": {"at": [1, 1, 1]}},
            "must be",
        ),
    ):
        with pytest.raises(sc.ItemError, match=message):
            items.normalise(spec, set(), 1)


# --- overall ----------------------------------------------------------------------------------------------------------


def test_the_overall_dimensions_come_from_the_views() -> None:
    ctx = make_context()
    (r,) = draw(ctx, {"type": "overall"})
    assert r.status == "ok" and _dim_values(ctx) == ["100", "8", "60"]
    assert r.detail == "width 100, height 8, depth 60 mm"


# --- datum_dims -------------------------------------------------------------------------------------------------------


def test_ordinate_dimensions_give_each_hole_from_the_datum_edges() -> None:
    ctx = make_context()
    (r,) = draw(ctx, {"type": "datum_dims"})
    assert r.status == "ok", r.detail
    values = [t.text for t in ctx.sheet.entities if isinstance(t, model.Text) and t.layer == model.DIMENSIONS]
    assert sorted(values) == sorted(["10", "90", "10", "50"])  # one baseline dimension per distinct position
    assert "x from 10, 90; y from 10, 50" in r.detail
    natives = ctx.extras["natives"]["g1"]
    assert [(n.kind, n.orient, n.expected) for n in natives] == [
        ("distance", "h", 10.0),
        ("distance", "h", 90.0),
        ("distance", "v", 10.0),
        ("distance", "v", 50.0),
    ]


def test_ordinates_follow_a_datum_face_and_flip_the_sign_across_it() -> None:
    ctx = make_context()
    draw(ctx, {"type": "datum_dims", "x_from": "face:left", "y_from": "face:front"})
    assert "10" in _dim_values(ctx) and "90" in _dim_values(ctx)
    ctx = make_context()
    (bad,) = draw(ctx, {"type": "datum_dims", "x_from": "face:top"})
    assert bad.status == "error" and "cannot be that datum" in bad.detail


def test_the_hole_table_lists_x_y_and_the_callout_of_every_hole_and_numbers_them() -> None:
    ctx = make_context()
    (r,) = draw(ctx, {"type": "datum_dims", "mode": "table"})
    assert r.status == "ok", r.detail
    all_texts = texts(ctx)
    assert "TABLA DE AGUJEROS" in all_texts and "Descripción" in all_texts
    assert all_texts.count("Ø6,6 pasante") == 4
    assert {"1", "2", "3", "4"} <= set(all_texts)


def test_a_view_with_no_hole_looking_along_it_says_where_the_holes_are() -> None:
    (r,) = draw(make_context(), {"type": "datum_dims", "view": "front"})
    assert r.status == "error" and "looks along the front view" in r.detail


# --- note and revision ------------------------------------------------------------------------------------------------


def test_notes_are_numbered_and_the_block_has_a_header() -> None:
    ctx = make_context()
    draw(ctx, {"type": "note", "text": "Romper aristas vivas 0,3 mm"}, {"type": "note", "text": "Sin rebabas"})
    ys = {t.text: t.y for t in ctx.sheet.entities if isinstance(t, model.Text)}
    assert set(ys) == {"1. Romper aristas vivas 0,3 mm", "2. Sin rebabas", "NOTAS:"}
    assert ys["NOTAS:"] > ys["1. Romper aristas vivas 0,3 mm"] > ys["2. Sin rebabas"]


def test_the_notes_block_keeps_a_text_height_off_a_dimension_and_moves_as_a_whole_when_it_does_not() -> None:
    ctx = make_context()
    draw(ctx, {"type": "note", "text": "Sin rebabas"})
    block_x = next(t.x for t in ctx.sheet.entities if isinstance(t, model.Text) and t.text == "NOTAS:")
    block_width = model.text_width("1. Sin rebabas")
    ctx = make_context()
    start = block_x + block_width + 1.5  # a dimension strip 1.5 mm from where the block ends: near, not overlapping
    strip = (start, ctx.area[1], start + 25.0, ctx.area[1] + 30.0)
    ctx.reserve(strip)
    draw(ctx, {"type": "note", "text": "Sin rebabas"})
    moved = next(t.x for t in ctx.sheet.entities if isinstance(t, model.Text) and t.text == "NOTAS:")
    assert moved + block_width <= strip[0] - 3.5 or moved >= strip[2] + 3.5, (block_x, moved, strip)


def test_a_long_note_wraps_and_a_note_can_sit_at_a_page_position() -> None:
    ctx = make_context()
    draw(ctx, {"type": "note", "text": "palabra " * 30})
    assert len([t for t in texts(ctx) if t.startswith("1.") or t.startswith("    ")]) >= 3
    ctx = make_context()
    draw(ctx, {"type": "note", "text": "ver plano 2", "at_mm": [200, 150]})
    note = next(t for t in ctx.sheet.entities if isinstance(t, model.Text))
    assert (note.x, note.y, note.text) == (200.0, 150.0, "1. ver plano 2")


def test_revisions_are_collected_for_the_title_block() -> None:
    ctx = make_context()
    results = draw(
        ctx,
        {"type": "revision", "index": "A", "description": "Emisión inicial", "date": "2026-10-25", "by": "JR"},
        {"type": "revision", "index": "B", "description": "Agujeros a Ø8"},
    )
    assert [r.status for r in results] == ["ok", "ok"]
    assert [r["index"] for r in ctx.revisions] == ["A", "B"] and ctx.revisions[1]["date"] == ""
    assert ctx.sheet.entities == []  # the rows live in the title block, which is composed from ctx.revisions


def test_a_sixth_revision_pushes_the_first_one_out_of_the_table() -> None:
    ctx = make_context()
    specs = [{"type": "revision", "index": chr(65 + i), "description": f"cambio {i}"} for i in range(6)]
    results = draw(ctx, *specs)
    assert results[0].status == "warning" and "last 5" in results[0].detail and results[5].status == "ok"


# --- determinism ------------------------------------------------------------------------------------------------------


def test_the_same_items_draw_the_same_sheet() -> None:
    specs = (
        {"type": "overall"},
        {"type": "hole_dim", "all": True},
        {"type": "datum_dims"},
        {"type": "dim", "kind": "distance", "from": {"face": "face:left"}, "to": {"face": "face:right"}},
    )
    a, b = make_context(), make_context()
    draw(a, *specs)
    draw(b, *specs)
    assert a.sheet.entities == b.sheet.entities and len(a.sheet.entities) > 40


def test_a_hole_table_with_no_free_room_says_so_and_at_mm_places_it_anyway() -> None:
    ctx = make_context()
    ctx.reserve(ctx.area)  # nothing is free
    (crowded,) = draw(ctx, {"type": "datum_dims", "mode": "table"})
    assert crowded.status == "warning" and "at_mm" in crowded.detail
    placed = make_context()
    placed.reserve(placed.area)
    (fine,) = draw(placed, {"type": "datum_dims", "mode": "table", "at_mm": [30, 150]})
    assert fine.status == "ok"
    assert "TABLA DE AGUJEROS" in texts(placed)


def test_the_notes_block_only_reserves_the_width_of_its_lines() -> None:
    ctx = make_context()
    draw(ctx, {"type": "note", "text": "corto"})
    x0, _, x1, _ = ctx.occupied[0]
    assert x1 - x0 < 30.0  # not the 150 mm a full-width line would take


def test_a_note_that_states_the_thickness_is_recognised_so_the_sheet_does_not_say_it_twice() -> None:
    import items_notes  # noqa: PLC0415

    assert items_notes.states_thickness("Espesor 19 mm. Romper aristas vivas 0,3 mm")
    assert items_notes.states_thickness("Thickness: 6")
    assert not items_notes.states_thickness("Fabricar de lámina rectificada de 19 mm")  # a stock form is not the thickness note
    assert not items_notes.states_thickness("Espesor según plano")  # no number: it states nothing
