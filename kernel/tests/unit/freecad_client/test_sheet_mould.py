# ruff: noqa: E501
"""The indications of a moulded part (``items_mould``, ISO 10135): the parting line in its own line type, the opening direction with
the draft, the gate and ejector zones with their symbols, the no-draft exception. A hand-made context: a 100 x 60 x 8 plate seen
in a front, a top and a right view at 1:1, mass lower than its middle (an open cavity facing +z). No FreeCAD."""

import sys
from pathlib import Path

import pytest

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import items_mould as mould  # noqa: E402
import sheet_context as sc  # noqa: E402
import sheet_model as model  # noqa: E402
import sheet_reqfacts as reqfacts  # noqa: E402
import sheet_review as review  # noqa: E402

from .test_sheet_items import PlateGeometry, draw, make_context  # noqa: E402


class MouldGeometry(PlateGeometry):
    """The plate as a moulded shell: shallowest along z (8), mass below the middle, the widest section at the shoulder z = 6."""

    def mould_box(self) -> tuple[sc.Vec3, sc.Vec3, sc.Vec3]:
        return (0.0, 0.0, 0.0), (100.0, 60.0, 8.0), (50.0, 30.0, 2.5)

    def outline_areas(self, axis: sc.Vec3, around: tuple[float, float] | None = None) -> list[tuple[float, float]]:
        if abs(axis[2]) != 1.0:
            return []
        return [(1.0, 5900.0), (3.0, 5950.0), (5.0, 5990.0), (5.9, 6000.0), (7.0, 5800.0)]

    def axial_planes(self, axis: sc.Vec3) -> list[float]:
        return [0.0, 6.0, 8.0] if abs(axis[2]) == 1.0 else []

    def section_outline(self, axis: sc.Vec3, level: float, centre: sc.Vec3, xdir: sc.Vec3, direction: sc.Vec3) -> list[sc.Point]:
        # the plan view (top: looks along z): the rectangle of the plate in the view's own coordinates (centre at 50, 30)
        return [(-50.0, -30.0), (50.0, -30.0), (50.0, 30.0), (-50.0, 30.0)]


def context() -> sc.Context:
    ctx = make_context(holes=[])
    ctx.geometry = MouldGeometry()
    return ctx


def layer(ctx: sc.Context, name: str) -> list[model.Entity]:
    return [e for e in ctx.sheet.entities if getattr(e, "layer", None) == name]


# --- what the model proposes ---------------------------------------------------------------------------------------------------------


def test_the_pull_is_the_shallowest_axis_towards_the_open_side() -> None:
    assert mould.shortest_axis((0, 0, 0), (100, 60, 8)) == 2
    assert mould.shortest_axis((0, 0, 0), (100, 100, 100)) == 2  # a tie goes to z
    assert mould.open_sign((0, 0, 0), (100, 60, 8), (50, 30, 2.5), 2) == 1.0  # the mass is below the middle: the cavity opens up
    assert mould.open_sign((0, 0, 0), (100, 60, 8), (50, 30, 5.5), 2) == -1.0


def test_the_parting_plane_is_where_the_outline_is_widest_on_the_planar_face_there() -> None:
    samples = [(1.0, 5900.0), (3.0, 5950.0), (5.0, 5990.0), (5.9, 6000.0), (7.0, 5800.0)]
    assert mould.parting_level(samples, [0.0, 6.0, 8.0], 1.0) == 6.0  # 5.9 is the widest; the shoulder at 6 is within 1.5 mm
    assert mould.parting_level(samples, [], 1.0) == 5.9
    flat = [(1.0, 6000.0), (3.0, 6000.0), (5.0, 6000.0)]  # straight walls: as wide everywhere, the end on the open side
    assert mould.parting_level(flat, [], 1.0) == 5.0 and mould.parting_level(flat, [], -1.0) == 1.0
    assert mould.parting_level([], [], 1.0) is None


# --- parting_line -------------------------------------------------------------------------------------------------------------------


def test_the_parting_line_has_its_own_line_type_and_is_a_line_in_the_side_views_and_the_outline_in_the_plan() -> None:
    ctx = context()
    (result,) = draw(ctx, {"type": "parting_line"})
    assert result.status == "ok", result.detail
    assert "+z" in result.detail and "z=6" in result.detail  # measured: pull +z, the plane on the shoulder
    style = model.LAYERS[model.PARTING]
    assert style.linetype == "PHANTOM" and style.width_mm == model.THICK_MM and len(style.dash) == 6  # thick, long dash and two short ones
    assert model.LINETYPES["PHANTOM"][0] > 0 > model.LINETYPES["PHANTOM"][1]
    lines = [e for e in layer(ctx, model.PARTING) if isinstance(e, model.Line)]
    assert len(lines) == 2  # front and right views, across the pull
    assert all(abs(ln.y1 - ln.y2) < 1e-6 for ln in lines)  # the parting plane is horizontal there
    front = ctx.views["front"]
    assert abs(lines[0].y1 - front.project((50.0, 30.0, 6.0))[1]) < 1e-6  # at the level of the plane, in the page
    assert lines[0].x1 < front.box[0] and lines[0].x2 > front.box[2]  # it reaches past the part
    loops = [e for e in layer(ctx, model.PARTING) if isinstance(e, model.Polyline)]
    assert len(loops) == 1 and loops[0].closed  # the plan view: the outline, a little outside the part
    xs = [p[0] for p in loops[0].points]
    assert min(xs) < ctx.views["top"].box[0] and max(xs) > ctx.views["top"].box[2]
    assert ctx.sheet.texts().count("LP") == 3  # labelled in every view (línea de partición)


def test_the_parting_line_takes_the_pull_and_the_level_the_engineer_gives() -> None:
    ctx = context()
    (result,) = draw(ctx, {"type": "parting_line", "pull": "-z", "level_mm": 3.0})
    assert result.status == "ok" and "-z" in result.detail and "z=3" in result.detail, result.detail


def test_a_part_that_gives_no_measures_asks_for_the_pull() -> None:
    ctx = make_context(holes=[])  # PlateGeometry measures nothing for a mould
    (result,) = draw(ctx, {"type": "parting_line"})
    assert result.status == "error" and "pull" in result.detail


# --- pull_direction -------------------------------------------------------------------------------------------------------------------


def test_the_opening_direction_is_an_arrow_with_the_draft_wedge_and_its_value() -> None:
    ctx = context()
    (result,) = draw(ctx, {"type": "pull_direction", "draft_deg": 1.5})
    assert result.status == "ok", result.detail
    assert "1,5°" in ctx.sheet.texts() and "Apertura" in ctx.sheet.texts()
    assert any(isinstance(e, model.Solid) for e in ctx.sheet.entities)  # the arrow head
    wedge = [e for e in ctx.sheet.entities if isinstance(e, model.Polyline) and e.closed and len(e.points) == 3]
    assert len(wedge) == 1  # the symbol of the draft


def test_without_a_draft_value_the_arrow_has_no_wedge() -> None:
    ctx = context()
    draw(ctx, {"type": "pull_direction"})
    assert not [e for e in ctx.sheet.entities if isinstance(e, model.Polyline) and e.closed and len(e.points) == 3]
    assert not any("°" in t for t in ctx.sheet.texts())


# --- mould_zone ------------------------------------------------------------------------------------------------------------------------


def test_a_gate_zone_inside_the_part_is_a_hatched_disc_with_a_circle_and_g() -> None:
    ctx = context()
    (result,) = draw(ctx, {"type": "mould_zone", "kind": "gate", "at": [50.0, 30.0, 4.0], "radius_mm": 4.0})
    assert result.status == "ok", result.detail
    zone = layer(ctx, model.ZONE)
    assert any(isinstance(e, model.Circle) and abs(e.r - 4.0) < 1e-6 for e in zone)  # the zone, seen face-on in the plan view
    assert any(isinstance(e, model.Circle) and abs(e.r - 3.5) < 1e-6 for e in zone)  # the symbol of a gate
    assert layer(ctx, model.HATCH)  # hatched: a permitted AREA
    assert "G" in ctx.sheet.texts()


def test_an_ejector_zone_on_the_outside_is_a_band_with_a_square_and_e() -> None:
    ctx = context()
    (result,) = draw(ctx, {"type": "mould_zone", "kind": "ejector", "at": [[20.0, 30.0, 0.0], [80.0, 30.0, 0.0]], "radius_mm": 5.0})
    assert result.status == "ok", result.detail
    assert "E" in ctx.sheet.texts() and ctx.sheet.texts().count("E") == 2
    squares = [e for e in layer(ctx, model.ZONE) if isinstance(e, model.Polyline) and e.closed]
    assert len(squares) == 2
    bands = [e for e in layer(ctx, model.ZONE) if isinstance(e, model.Line) and e.width == 1.0]
    assert len(bands) == 2 and all(abs(b.y1 - b.y2) < 1e-6 for b in bands)  # on the bottom face, seen edge-on: a band across the view


def test_a_zone_is_a_gate_or_an_ejector_and_needs_a_point() -> None:
    ctx = context()
    with pytest.raises(sc.ItemError):
        draw(ctx, {"type": "mould_zone", "at": [50.0, 30.0, 4.0]})
    with pytest.raises(sc.ItemError):
        mould.check_zone({"kind": "sprue", "at": [1, 2, 3]})
    with pytest.raises(sc.ItemError):
        mould.check_zone({"kind": "gate"})


# --- no_draft ---------------------------------------------------------------------------------------------------------------------------


def test_a_face_without_draft_and_a_sharp_edge_are_leaders_with_their_text() -> None:
    ctx = context()
    results = draw(
        ctx,
        {"type": "no_draft", "at": [100.0, 30.0, 4.0]},
        {"type": "no_draft", "kind": "sharp_edge", "at": [100.0, 30.0, 8.0]},
    )
    assert [r.status for r in results] == ["ok", "ok"], [r.detail for r in results]
    texts = ctx.sheet.texts()
    assert "0° sin desmoldeo" in texts and "R0 arista viva" in texts


# --- the sheet counts them -----------------------------------------------------------------------------------------------------------------


def test_the_facts_count_the_symbols_not_the_words_of_a_note() -> None:
    ctx = context()
    items = [
        {"id": "pl", "type": "parting_line"},
        {"id": "pd", "type": "pull_direction", "draft_deg": 1.0},
        {"id": "g1", "type": "mould_zone", "kind": "gate", "at": [[50.0, 30.0, 4.0]]},
        {"id": "n1", "type": "note", "text": "Línea de partición en el hombro; inyección en el centro; expulsores en las torretas"},
    ]
    results = {i["id"]: sc.ItemResult(i["id"], i["type"]) for i in items}
    data = review.ReviewInput(items, results, ctx, ctx.sheet)
    out = reqfacts._mould(data)  # noqa: SLF001
    assert out == {
        "mould_items": 3,
        "mould_parting_on_sheet": 1,
        "mould_pull_on_sheet": 1,
        "mould_draft_on_sheet": 1,
        "mould_gate_on_sheet": 1,
        "mould_ejector_on_sheet": 0,
    }


def test_the_registry_and_the_contract_have_the_same_four_types() -> None:
    import sheet_items

    from cotaforge.contract import drawing_items

    for name in ("parting_line", "pull_direction", "mould_zone", "no_draft"):
        assert sheet_items.fields_of(name) == drawing_items.ITEM_FIELDS[name] | {"type", "id"}


# --- the window pulls a symbol whose centre is near a view origin onto it ------------------------------------------------------------------


def test_a_symbol_is_drawn_with_its_centre_clear_of_the_places_the_window_snaps_it_to() -> None:
    """Two zones of the junction box landed 6 to 9 mm from their turrets: the window moves a symbol whose centre is within 6 mm
    (across the page) of a view origin, and the harness can only shift a symbol up the page. So the drawing itself stands clear."""
    import sheet_units

    ctx = context()
    draw(ctx, {"id": "g1", "type": "mould_zone", "kind": "gate", "at": [50.0, 30.0, 4.0], "radius_mm": 4.0})
    box = sheet_units.bounds(ctx.sheet.entities)
    assert box is not None
    centre = (box[0] + box[2]) / 2
    origins = [v.x for v in ctx.views.values()]
    assert all(abs(centre - x) > mould.SNAP_X_MM for x in origins), (centre, origins)
    assert ctx.extras["mould_centres"] == [centre] or abs(ctx.extras["mould_centres"][0] - centre) < 1.0  # the next symbol keeps clear of this one


def test_the_guides_are_the_view_origins_the_notes_and_the_symbols_already_placed() -> None:
    ctx = context()
    ctx.extras.update({"note_lines": 5, "note_x": 27.0, "mould_centres": [210.0]})
    guides = mould._x_guides(ctx)  # noqa: SLF001
    assert {150.0, 50.0, 114.5, 210.0} <= set(guides)


def test_the_label_of_the_parting_line_keeps_off_the_dimension_lines() -> None:
    """On the junction box the label stood where the extension line of the overall height crosses (a collision)."""
    clean = context()
    draw(clean, {"type": "parting_line"})
    first = [e for e in clean.sheet.entities if isinstance(e, model.Text) and e.text == "LP"]
    crossed = context()
    x = first[0].x + 2.0  # a vertical dimension line through the first label of the front view
    crossed.extras["dimension_lines"] = [(x, 100.0, x, 250.0)]
    draw(crossed, {"type": "parting_line"})
    moved = [e for e in crossed.sheet.entities if isinstance(e, model.Text) and e.text == "LP"]
    assert (moved[0].x, moved[0].y) != (first[0].x, first[0].y)
    width = model.text_width("LP", 3.5)
    x0 = moved[0].x - (width if moved[0].anchor == "right" else 0.0)
    near_dimension = x0 - 0.5 < x < x0 + width + 0.5 and moved[0].y - 1.0 < 175.0 < moved[0].y + 4.5
    assert not near_dimension or not (100.0 < moved[0].y < 250.0)  # the label does not sit across the dimension line
