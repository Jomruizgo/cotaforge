"""Tolerances on a sheet (``sheet_tol.py``, ``items_gdt.py``, ``items_finish.py``, ``sheet_thread.py`` and the ``fit`` /
``tol`` fields of ``hole_dim`` and ``dim``) against a hand-made flanged bushing along z: flange R20 from z 0 to 6, boss
R12 up to z 20, a through bore R6 and an M6 tapped hole in the flange, at 1:1 in a front, a top and a right view. No
FreeCAD: the text must come out of the geometry the context describes."""

import io
import sys
from pathlib import Path
from typing import Any

import pytest

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import sheet_context as sc  # noqa: E402
import sheet_dxf  # noqa: E402
import sheet_items as items  # noqa: E402
import sheet_model as model  # noqa: E402
import sheet_review as review  # noqa: E402
import sheet_thread  # noqa: E402

CENTRE = (50.0, 30.0)


class BushingGeometry(sc.Geometry):
    def __init__(self) -> None:
        z = (0.0, 0.0, 1.0)
        self.faces = {
            "face:bottom": sc.FaceInfo("face:bottom", "plane", (50.0, 30.0, 0.0), normal=(0.0, 0.0, -1.0)),
            "face:end": sc.FaceInfo("face:end", "plane", (50.0, 30.0, 20.0), normal=z),
            "face:side": sc.FaceInfo("face:side", "plane", (70.0, 30.0, 3.0), normal=(1.0, 0.0, 0.0)),
            "face:bore": sc.FaceInfo(
                "face:bore",
                "cylinder",
                (50.0, 30.0, 10.0),
                radius=6.0,
                axis_point=(50.0, 30.0, 0.0),
                axis_dir=z,
                surface_point=(56.0, 30.0, 10.0),
            ),
            "face:journal": sc.FaceInfo(
                "face:journal",
                "cylinder",
                (50.0, 30.0, 13.0),
                radius=12.0,
                axis_point=(50.0, 30.0, 0.0),
                axis_dir=z,
                surface_point=(62.0, 30.0, 13.0),
            ),
            "face:tapped": sc.FaceInfo(
                "face:tapped", "cylinder", (64.0, 30.0, 2.0), radius=2.5, axis_point=(64.0, 30.0, 0.0), axis_dir=z
            ),
            "face:cone": sc.FaceInfo("face:cone", "other", (50.0, 30.0, 3.0)),
        }

    def round_extent(self) -> tuple[sc.Vec3, float] | None:
        return (0.0, 0.0, 1.0), 40.0  # a body of revolution about z whose outer cylinder is the flange, D40

    def face(self, face_id: str) -> sc.FaceInfo:
        if face_id not in self.faces:
            raise sc.ItemError(f"{face_id} is not a face of the part")
        return self.faces[face_id]


def make_context(language: str = "es", general: str | None = None) -> sc.Context:
    views = {
        "front": sc.ViewInfo(
            "front", 150.0, 160.0, 1.0, (0, -1, 0), (1, 0, 0), (50, 30, 10), (130.0, 150.0, 170.0, 170.0)
        ),
        "top": sc.ViewInfo("top", 150.0, 100.0, 1.0, (0, 0, 1), (1, 0, 0), (50, 30, 10), (130.0, 80.0, 170.0, 120.0)),
        "right": sc.ViewInfo("right", 50.0, 160.0, 1.0, (1, 0, 0), (0, 1, 0), (50, 30, 10), (30.0, 150.0, 70.0, 170.0)),
    }
    down = (0.0, 0.0, -1.0)
    holes = [
        sc.HoleInfo(0, (50.0, 30.0, 20.0), down, 12.0, 20.0, True, ("face:bore",), None, True),
        sc.HoleInfo(1, (64.0, 30.0, 6.0), down, 5.0, 8.0, False, ("face:tapped",), ("M6", 6.0), True),
    ]
    ctx = sc.Context(
        sheet=model.Sheet(420.0, 297.0),
        views=views,
        holes=holes,
        geometry=BushingGeometry(),
        area=(25.0, 80.0, 405.0, 282.0),
        language=language,
    )
    if general is not None:
        ctx.extras["general_tolerances"] = general
    return ctx


def draw(ctx: sc.Context, *specs: dict[str, Any]) -> list[sc.ItemResult]:
    taken: set[str] = set()
    normalised = []
    for number, spec in enumerate(specs, start=1):
        done = items.normalise(spec, taken, number)
        taken.add(done["id"])
        normalised.append(done)
    results = items.draw_all(ctx, normalised)
    return results


def texts(ctx: sc.Context) -> list[str]:
    return ctx.sheet.texts()


BORE = {"type": "hole_dim", "face": "face:bore", "id": "bore"}
JOURNAL = {"type": "dim", "kind": "diameter", "face": "face:journal"}
DATUM_A = {"type": "datum", "letter": "A", "on": "bore"}  # the axis of the bore: on its callout


# --- fits on a hole and on a diameter ---------------------------------------------------------------------------------


def test_a_hole_with_a_fit_shows_the_class_and_its_deviations() -> None:
    ctx = make_context()
    (r,) = draw(ctx, {**BORE, "fit": "H7"})
    assert r.status == "ok", r.detail
    assert "Ø12 H7 (+0,018/0) pasante" in texts(ctx)


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        ({"show": "symbol"}, "Ø12 H7 pasante"),
        ({"show": "deviations"}, "Ø12 +0,018/0 pasante"),
        ({"show": "limits"}, "Ø12,018 / 12,000 pasante"),
        ({"show": "both"}, "Ø12 H7 (+0,018/0) pasante"),
    ],
)
def test_the_fit_can_be_shown_as_symbol_deviations_limits_or_both(extra: dict[str, str], expected: str) -> None:
    ctx = make_context()
    (r,) = draw(ctx, {**BORE, "fit": "H7", **extra})
    assert r.status == "ok", r.detail
    assert expected in texts(ctx)


def test_a_fit_of_two_classes_is_shown_as_its_symbol() -> None:
    ctx = make_context()
    (r,) = draw(ctx, {**BORE, "fit": "H7/g6"})
    assert r.status == "ok" and "Ø12 H7/g6 pasante" in texts(ctx)
    with pytest.raises(sc.ItemError, match="symbol only"):
        items.normalise({**BORE, "fit": "H7/g6", "show": "deviations"}, set(), 1)


def test_the_english_sheet_writes_a_decimal_point() -> None:
    ctx = make_context("en")
    draw(ctx, {**BORE, "fit": "H7"})
    assert "Ø12 H7 (+0.018/0) thru" in texts(ctx)


def test_a_shaft_diameter_is_measured_and_gets_its_fit() -> None:
    ctx = make_context()
    (r,) = draw(ctx, {**JOURNAL, "fit": "g6"})
    assert r.status == "ok", r.detail
    assert "Ø24 g6 (-0,007/-0,02)" in texts(ctx)
    assert "in the front view" in r.detail  # the view that shows the shaft's axis across the page


def test_a_diameter_dimension_refuses_the_wall_of_a_hole_and_a_flat_face() -> None:
    ctx = make_context()
    bore, flat = draw(ctx, {"type": "dim", "kind": "diameter", "face": "face:bore"}, {**JOURNAL, "face": "face:end"})
    assert bore.status == "error" and "wall of a hole" in bore.detail
    assert flat.status == "error" and "cylindrical face" in flat.detail


def test_a_threaded_hole_takes_no_fit() -> None:
    ctx = make_context()
    (r,) = draw(ctx, {"type": "hole_dim", "face": "face:tapped", "fit": "H7"})
    assert r.status == "error" and "thread class" in r.detail
    assert ctx.sheet.entities == []


def test_the_fit_checks_its_arguments() -> None:
    for spec, message in (
        ({"fit": "X7"}, "not in the tables"),
        ({"fit": "H7", "tol": 0.1}, "give one"),
        ({"show": "limits"}, "needs a fit or a tol"),
        ({"tol": 0.1, "show": "symbol"}, "give fit"),
        ({"tol": [-0.1, 0.1]}, "must not be below"),
        ({"tol": 0}, "above 0"),
        ({"tol": "0.1"}, "number"),
    ):
        with pytest.raises(sc.ItemError, match=message):
            items.normalise({**BORE, **spec}, set(), 1)


def test_a_fit_outside_the_tables_is_an_item_error_not_a_wrong_number() -> None:
    ctx = make_context()
    (r,) = draw(ctx, {**BORE, "fit": "J9"})
    assert r.status == "error" and "J9" in r.detail


# --- a fit prints its class and its limits, never a note that asks the shop to verify ------------------------------


def test_a_sheet_that_uses_a_fit_prints_class_and_limits_and_no_verification_note() -> None:
    ctx = make_context()
    draw(ctx, {**BORE, "fit": "H7"})
    found = texts(ctx)
    assert any("H7 (+0,018/0)" in t for t in found), found
    assert not any(w in t.lower() for t in found for w in ("transcrit", "verificar", "verify"))


def test_a_fit_of_two_classes_prints_the_symbol_only_and_no_note() -> None:
    ctx = make_context()
    draw(ctx, {**BORE, "fit": "H7/g6"})
    found = texts(ctx)
    assert any("H7/g6" in t for t in found), found
    assert not any("transcrit" in t.lower() for t in found)


def test_a_refused_fit_leaves_no_note_behind() -> None:
    ctx = make_context()
    draw(ctx, {"type": "hole_dim", "face": "face:tapped", "fit": "H7"})
    assert not any("transcritas" in t for t in texts(ctx))


# --- explicit tolerances ----------------------------------------------------------------------------------------------


def _distance(**extra: Any) -> dict[str, Any]:
    return {
        "type": "dim",
        "kind": "distance",
        "from": {"face": "face:bottom"},
        "to": {"face": "face:end"},
        "view": "front",
        **extra,
    }


def test_a_linear_dimension_stacks_its_deviations() -> None:
    ctx = make_context()
    (r,) = draw(ctx, _distance(tol=[0.1, -0.05]))
    assert r.status == "ok", r.detail
    assert {"20", "+0,1", "-0,05"} <= set(texts(ctx))
    small = [e for e in ctx.sheet.entities if isinstance(e, model.Text) and e.text in ("+0,1", "-0,05")]
    upper, lower = sorted(small, key=lambda t: t.x)  # the text reads up the page: its "up" is toward the left
    assert upper.text == "+0,1" and upper.x < lower.x and upper.y == pytest.approx(lower.y)
    assert upper.height < 3.5  # smaller type than the value
    value = next(e for e in ctx.sheet.entities if isinstance(e, model.Text) and e.text == "20")
    assert upper.angle == value.angle == 90.0  # the front view's height dimension reads from the right


def test_a_symmetric_tolerance_is_one_inline_pair_and_limits_replace_the_value() -> None:
    ctx = make_context()
    draw(ctx, _distance(tol=0.1))
    assert "20 ±0,1" in texts(ctx)
    ctx = make_context()
    draw(ctx, _distance(tol=[0.015, 0], show="limits"))
    assert "20,015 / 20,000" in texts(ctx)


def test_a_fit_on_a_linear_dimension_is_symbol_and_stacked_deviations() -> None:
    ctx = make_context()
    (r,) = draw(ctx, _distance(fit="h9"))
    assert r.status == "ok", r.detail
    assert "20 h9" in texts(ctx) and "0" in texts(ctx) and "-0,052" in texts(ctx)


def test_a_radius_and_an_angle_take_a_tolerance_inline() -> None:
    ctx = make_context()
    draw(ctx, {"type": "dim", "kind": "radius", "face": "face:journal", "tol": 0.2})
    assert "R12 ±0,2" in texts(ctx)
    with pytest.raises(sc.ItemError, match="not for a radius"):
        items.normalise({"type": "dim", "kind": "radius", "face": "face:journal", "fit": "H7"}, set(), 1)
    with pytest.raises(sc.ItemError, match="chamfer takes no fit"):
        items.normalise({"type": "dim", "kind": "chamfer", "face": "face:end", "tol": 0.1}, set(), 1)


# --- ISO 2768 general tolerances --------------------------------------------------------------------------------------


def test_an_untoleranced_dimension_reports_the_general_tolerance_that_applies() -> None:
    ctx = make_context(general="ISO 2768-mK")
    (r,) = draw(ctx, _distance())
    assert "general tolerance (ISO 2768-m): ±0,2" in r.detail  # 20 mm
    assert "tablas ISO 2768 transcritas; verificar contra la norma" in r.detail
    (second,) = draw(make_context(general="ISO 2768-fH"), _distance())
    assert "ISO 2768-f): ±0,1" in second.detail
    assert "transcritas" not in second.detail or "ISO 2768" in second.detail


def test_the_transcription_remark_for_iso_2768_is_said_once_per_sheet() -> None:
    ctx = make_context(general="ISO 2768-mK")
    first, second = draw(ctx, _distance(), {**JOURNAL})
    assert "transcritas" in first.detail and "transcritas" not in second.detail
    assert "general tolerance (ISO 2768-m): ±0,2" in second.detail  # Ø24 is in the 6 to 30 mm range too


def test_the_hole_and_overall_dimensions_report_theirs_and_a_toleranced_one_does_not() -> None:
    ctx = make_context(general="ISO 2768-mK")
    (hole,) = draw(ctx, BORE)
    assert "general tolerance (ISO 2768-m): Ø12 ±0,2" in hole.detail
    (overall,) = draw(make_context(general="ISO 2768-mK"), {"type": "overall"})
    assert "width ±0,3, height ±0,2" in overall.detail and "depth" not in overall.detail
    (toleranced,) = draw(make_context(general="ISO 2768-mK"), _distance(tol=0.1))
    assert "general tolerance" not in toleranced.detail and "toleranced" in toleranced.detail


def test_a_title_block_that_does_not_name_iso_2768_reports_nothing() -> None:
    (r,) = draw(make_context(general="según plano 7"), _distance())
    assert "general tolerance" not in r.detail
    (none,) = draw(make_context(), _distance())
    assert "general tolerance" not in none.detail


# --- datums -----------------------------------------------------------------------------------------------------------


def test_a_datum_on_an_axis_stands_on_the_shelf_of_the_dimension_callout_with_its_letter_below() -> None:
    ctx = make_context()
    bore, datum = draw(ctx, BORE, DATUM_A)
    assert bore.status == "ok" and datum.status == "ok", datum.detail
    assert "on the dimension of bore (the axis)" in datum.detail
    assert texts(ctx).count("A") == 1
    anchor = ctx.extras["anchors"]["bore"][0]
    triangles = [e for e in ctx.sheet.entities if isinstance(e, model.Solid) and len(e.points) == 3]
    triangle = triangles[-1]
    assert triangle.points[0][1] == pytest.approx(anchor["elbow"][1])  # its tip touches the shelf line
    assert triangle.points[1][1] < anchor["elbow"][1]  # and its base is below
    letter = next(e for e in ctx.sheet.entities if isinstance(e, model.Text) and e.text == "A")
    assert letter.y < anchor["elbow"][1]


def test_the_axis_of_a_cylinder_cannot_be_a_datum_by_face_and_a_datum_needs_one_place() -> None:
    ctx = make_context()
    (r,) = draw(ctx, {"type": "datum", "letter": "A", "face": "face:journal"})
    assert r.status == "error" and "AXIS" in r.detail and "on=" in r.detail
    for spec in ({"type": "datum", "letter": "A"}, {**DATUM_A, "face": "face:end"}):
        with pytest.raises(sc.ItemError, match="exactly one of face"):
            items.normalise(spec, set(), 1)
    ctx = make_context()
    (r,) = draw(ctx, {"type": "datum", "letter": "A", "on": "nothing"})
    assert r.status == "error" and "no callout with a leader" in r.detail


def test_a_datum_on_a_plane_seen_edge_on_touches_its_outline() -> None:
    ctx = make_context()
    (r,) = draw(ctx, {"type": "datum", "letter": "B", "face": "face:bottom"})
    assert r.status == "ok" and "surface of face:bottom in the front view" in r.detail
    triangle = next(e for e in ctx.sheet.entities if isinstance(e, model.Solid))
    assert triangle.points[0] == (150.0, 150.0)  # the middle of the bottom edge of the front view


def test_a_face_seen_face_on_cannot_carry_a_datum_in_that_view() -> None:
    ctx = make_context()
    (r,) = draw(ctx, {"type": "datum", "letter": "B", "face": "face:end", "view": "top"})
    assert r.status == "error" and "face-on" in r.detail
    assert ctx.sheet.entities == []


def test_a_letter_names_one_datum_and_some_letters_are_not_allowed() -> None:
    ctx = make_context()
    _, first, second = draw(ctx, BORE, DATUM_A, {**DATUM_A, "id": "dup"})
    assert first.status == "ok" and second.status == "error" and "already on this sheet" in second.detail
    for letter in ("I", "O", "Q", "a", "AB", 1):
        with pytest.raises(sc.ItemError, match="letter"):
            items.normalise({**DATUM_A, "letter": letter}, set(), 1)


# --- feature control frames -------------------------------------------------------------------------------------------


def test_a_frame_citing_a_datum_that_does_not_exist_is_an_item_error() -> None:
    ctx = make_context()
    (r,) = draw(ctx, {"type": "gdt", "char": "perpendicularity", "value": 0.05, "datums": ["B"], "face": "face:end"})
    assert r.status == "error" and "datum B is not on this sheet" in r.detail and "add a datum item" in r.detail
    assert ctx.sheet.entities == []
    ctx = make_context()
    _, a, f = draw(
        ctx,
        BORE,
        DATUM_A,
        {"type": "gdt", "char": "perpendicularity", "value": 0.05, "datums": ["A", "B"], "face": "face:end"},
    )
    assert a.status == "ok" and f.status == "error" and "datum B" in f.detail and "datums drawn: A" in f.detail


def test_a_frame_is_drawn_after_its_datum_whatever_the_order_of_the_items() -> None:
    frame = {"type": "gdt", "char": "runout", "value": 0.02, "datums": ["A"], "face": "face:end"}
    first, second = make_context(), make_context()
    (_, r1, _) = draw(first, BORE, frame, DATUM_A)
    (_, _, r2) = draw(second, BORE, DATUM_A, frame)
    assert r1.status == "ok" and r2.status == "ok"
    assert sorted(map(repr, first.sheet.entities)) == sorted(map(repr, second.sheet.entities))


def test_a_flatness_frame_has_a_symbol_and_a_value_and_no_datum() -> None:
    ctx = make_context()
    (r,) = draw(ctx, {"type": "gdt", "char": "flatness", "value": 0.02, "face": "face:bottom"})
    assert r.status == "ok", r.detail
    assert texts(ctx) == ["0,02"]
    assert any(isinstance(e, model.Glyph) and e.name == "flatness" for e in ctx.sheet.entities)
    assert any(isinstance(e, model.Solid) for e in ctx.sheet.entities)  # the arrowhead of the leader
    with pytest.raises(sc.ItemError, match="form tolerance"):
        items.normalise(
            {"type": "gdt", "char": "flatness", "value": 0.02, "datums": ["A"], "face": "face:bottom"}, set(), 1
        )


def test_the_frame_cells_are_symbol_value_and_datums() -> None:
    ctx = make_context()
    draw(ctx, BORE, DATUM_A, {"type": "gdt", "char": "runout", "value": 0.02, "datums": ["A"], "face": "face:journal"})
    outline = next(e for e in ctx.sheet.entities if isinstance(e, model.Polyline) and e.closed and len(e.points) == 4)
    height = max(p[1] for p in outline.points) - min(p[1] for p in outline.points)
    assert height == pytest.approx(7.0)
    dividers = [e for e in ctx.sheet.entities if isinstance(e, model.Line) and e.x1 == e.x2 and e.y2 - e.y1 == 7.0]
    assert len(dividers) == 2  # symbol | value | A
    assert "0,02" in texts(ctx) and texts(ctx).count("A") == 2  # the datum feature's letter and the one in the frame


def test_position_with_a_diameter_zone_and_a_modifier_hangs_from_the_callout() -> None:
    ctx = make_context()
    results = draw(
        ctx,
        BORE,
        DATUM_A,
        {"type": "hole_dim", "face": "face:tapped", "id": "t"},
        {
            "type": "gdt",
            "char": "position",
            "value": 0.1,
            "diameter": True,
            "modifier": "M",
            "datums": ["A"],
            "on": "t",
        },
    )
    assert [r.status for r in results] == ["ok"] * 4, [r.detail for r in results]
    assert "Ø0,1" in texts(ctx) and "M6 ↧ 6" in texts(ctx)
    assert any(isinstance(e, model.Glyph) and e.name == "mmc" for e in ctx.sheet.entities)
    anchor = ctx.extras["anchors"]["t"][0]
    frame_top = max(
        e.y2 for e in ctx.sheet.entities if isinstance(e, model.Line) and e.x1 == e.x2 and e.y2 - e.y1 == 7.0
    )
    assert frame_top == pytest.approx(anchor["elbow"][1], abs=0.1)  # under the size: its top edge is the shelf line


POSITION = {"type": "gdt", "char": "position", "value": 0.05, "diameter": True, "on": "bore", "id": "pos"}
DATUM_C = {"type": "datum", "id": "dC", "letter": "C", "face": "face:bottom"}  # the face the bore is perpendicular to
DATUM_B = {"type": "datum", "id": "dB", "letter": "B", "face": "face:side"}


def test_a_frame_hangs_on_the_shelf_of_its_size_callout_and_the_room_is_kept_for_it() -> None:
    ctx = make_context()
    results = draw(ctx, BORE, DATUM_C, DATUM_B, {**POSITION, "datums": ["C", "B"]})
    assert [r.status for r in results] == ["ok"] * 4, [r.detail for r in results]
    anchor = ctx.extras["anchors"]["bore"][0]
    slot = ctx.extras["frame_slots"]["bore"]
    assert slot[3] == pytest.approx(anchor["elbow"][1], abs=0.1) and slot[3] - slot[1] == pytest.approx(7.0, abs=0.1)
    boxes = [
        (
            min(q[0] for q in e.points),
            min(q[1] for q in e.points),
            max(q[0] for q in e.points),
            max(q[1] for q in e.points),
        )
        for e in ctx.sheet.entities
        if isinstance(e, model.Polyline) and e.closed and len(e.points) == 4
    ]
    frame_box = max(boxes, key=lambda b: b[2] - b[0])
    assert frame_box[3] == pytest.approx(anchor["elbow"][1], abs=0.1)  # its top edge is the shelf line
    assert slot[0] - 1e-6 <= frame_box[0] and frame_box[2] <= slot[2] + 1e-6  # inside the room that was kept


def exact_flags(ctx: sc.Context, item: str) -> list[bool]:
    return [n.exact for n in ctx.extras["natives"].get(item, []) if hasattr(n, "exact")]


def test_the_dimensions_that_locate_a_hole_with_a_position_frame_are_boxed() -> None:
    ctx = make_context()
    draw(ctx, BORE, DATUM_C, {"type": "datum_dims", "id": "dd"}, {**POSITION, "datums": ["C"]})
    assert ctx.extras["ted_holes"] == {0}
    flags = exact_flags(ctx, "dd")
    names = {n.name: n.exact for n in ctx.extras["natives"]["dd"]}
    assert any(flags), names
    boxed = [
        e for e in ctx.sheet.entities if isinstance(e, model.Polyline) and e.closed and e.layer == model.DIMENSIONS
    ]
    assert len(boxed) >= 1


def test_without_a_position_frame_the_location_dimensions_are_plain() -> None:
    ctx = make_context()
    draw(ctx, BORE, DATUM_C, {"type": "datum_dims", "id": "dd"})
    assert not any(exact_flags(ctx, "dd")) and exact_flags(ctx, "dd")


def test_a_distance_dimension_can_be_theoretically_exact_and_only_a_distance() -> None:
    ctx = make_context()
    spec = {"type": "dim", "kind": "distance", "from": {"at": [50, 30, 20]}, "to": {"face": "face:side"}, "id": "d1"}
    (plain,) = draw(ctx, spec)
    assert plain.status == "ok", plain.detail
    before = len([e for e in ctx.sheet.entities if isinstance(e, model.Polyline)])
    ctx2 = make_context()
    (boxed,) = draw(ctx2, {**spec, "exact": True})
    assert boxed.status == "ok", boxed.detail
    assert exact_flags(ctx2, "d1") == [True]
    assert len([e for e in ctx2.sheet.entities if isinstance(e, model.Polyline)]) == before + 1
    with pytest.raises(sc.ItemError, match="exact"):
        items.normalise({"type": "dim", "kind": "radius", "face": ["face:bore"], "exact": True}, set(), 1)


def location(**extra: Any) -> dict[str, Any]:
    return {
        "type": "dim",
        "kind": "distance",
        "from": {"at": [50, 30, 20]},
        "to": {"face": "face:side"},
        "id": "d1",
        **extra,
    }


def codes(found: list[review.Finding], *wanted: str) -> list[str]:
    return [f.code for f in found if f.code in wanted]


def test_review_flags_a_position_frame_located_by_plain_dimensions() -> None:
    found = reviewed(make_context(), BORE, DATUM_C, location(), {**POSITION, "datums": ["C"]})
    flagged = [f for f in found if f.code == "gdt_location_not_exact"]
    assert len(flagged) == 1 and flagged[0].items == ("pos", "d1") and "exact=true" in flagged[0].message
    ok = reviewed(make_context(), BORE, DATUM_C, location(exact=True), {**POSITION, "datums": ["C"]})
    assert codes(ok, "gdt_location_not_exact") == []
    no_position = reviewed(
        make_context(),
        BORE,
        DATUM_C,
        location(),
        {"type": "gdt", "char": "runout", "value": 0.02, "datums": ["C"], "on": "bore"},
    )
    assert codes(no_position, "gdt_location_not_exact") == []


def test_review_flags_a_position_frame_with_no_datum_that_fixes_the_axis() -> None:
    found = reviewed(make_context(), BORE, DATUM_B, {**POSITION, "datums": ["B"]})
    flagged = [f for f in found if f.code == "gdt_axis_not_oriented"]
    assert len(flagged) == 1 and flagged[0].items == ("pos",) and "C | A | B" in flagged[0].message
    for datums, specs in (
        (["C", "B"], (DATUM_C, DATUM_B)),
        (["A", "B"], (DATUM_B, {"type": "datum", "letter": "A", "on": "bore"})),
    ):
        found = reviewed(make_context(), BORE, *specs, {**POSITION, "datums": datums})
        assert codes(found, "gdt_axis_not_oriented") == [], (
            datums
        )  # the face the hole is perpendicular to, or its own axis
    none = reviewed(make_context(), BORE, {**POSITION})
    assert codes(none, "gdt_axis_not_oriented") == ["gdt_axis_not_oriented"]


def test_a_frame_can_only_hang_from_a_callout_that_exists() -> None:
    ctx = make_context()
    (r,) = draw(ctx, {"type": "gdt", "char": "position", "value": 0.1, "on": "nothing"})
    assert r.status == "error" and "no callout with a leader" in r.detail


def test_the_rules_of_the_characteristics_are_checked() -> None:
    base = {"type": "gdt", "value": 0.05}
    for spec, message in (
        ({"char": "position"}, "exactly one of face"),
        ({"char": "position", "face": "face:end"}, "hang the frame from the dimension"),
        ({"char": "flatness", "face": "face:end", "diameter": True}, "no diameter zone"),
        ({"char": "runout", "face": "face:end", "modifier": "M"}, "no M / L"),
        ({"char": "runout", "face": "face:end"}, "needs datums"),
        ({"char": "parallelism", "face": "face:end", "datums": ["A", "A"]}, "different datum letters"),
        ({"char": "parallelism", "face": "face:end", "datums": ["A", "B", "C", "D"]}, "one to three"),
        ({"char": "parallelism", "face": "face:end", "diameter": True, "datums": ["A"]}, "Ø zone"),
        ({"char": "blob", "face": "face:end"}, "must be one of"),
        ({"char": "flatness", "face": "face:end", "on": "h1"}, "exactly one of face"),
    ):
        with pytest.raises(sc.ItemError, match=message):
            items.normalise({**base, **spec}, set(), 1)
    for char in (
        "flatness straightness circularity cylindricity parallelism perpendicularity angularity position concentricity "
        "coaxiality symmetry runout total_runout profile_line profile_surface"
    ).split():
        glyph = {"coaxiality": "concentricity"}.get(char, char)
        assert glyph in model.GLYPHS


def test_every_glyph_stays_in_its_unit_square_and_draws() -> None:
    for name, strokes in model.GLYPHS.items():
        for stroke in strokes:
            assert all(-0.01 <= x <= 1.01 and -0.01 <= y <= 1.01 for x, y in stroke), name
        assert model.Glyph(name, 0, 0, 4.6).strokes()


# --- surface finish ---------------------------------------------------------------------------------------------------


def test_the_bore_gets_an_ra_symbol_with_a_leader() -> None:
    ctx = make_context()
    (r,) = draw(ctx, {"type": "finish", "face": "face:bore", "ra": 1.6})
    assert r.status == "ok", r.detail
    assert "Ra 1,6" in texts(ctx)
    assert any(isinstance(e, model.Solid) for e in ctx.sheet.entities)  # the arrow on the circle of the bore
    assert "Ra 1,6 (required) on face:bore in the top view" in r.detail


def test_the_three_machining_symbols_differ() -> None:
    shapes = {}
    for how in ("required", "forbidden", "any"):
        ctx = make_context()
        (r,) = draw(ctx, {"type": "finish", "face": "face:end", "ra": 3.2, "machining": how, "view": "front"})
        assert r.status == "ok", r.detail
        shapes[how] = (
            sum(isinstance(e, model.Circle) for e in ctx.sheet.entities),
            [len(e.points) for e in ctx.sheet.entities if isinstance(e, model.Polyline)][:1],
            [e.closed for e in ctx.sheet.entities if isinstance(e, model.Polyline)][:1],
        )
    assert shapes["required"][2] == [True] and shapes["forbidden"][2] == [False] and shapes["any"][2] == [False]
    assert shapes["forbidden"][0] == 1 and shapes["required"][0] == 0 and shapes["any"][0] == 0


def test_a_surface_that_faces_up_gets_the_symbol_on_its_outline() -> None:
    ctx = make_context()
    (r,) = draw(ctx, {"type": "finish", "face": "face:end", "ra": 0.8, "view": "front"})
    assert "touching its outline" in r.detail
    lines = [e for e in ctx.sheet.entities if isinstance(e, model.Polyline)]
    assert (150.0, 170.0) in lines[0].points  # the vertex is on the top edge of the front view
    assert "Ra 0,8" in texts(ctx)


def test_a_decimal_ra_keeps_its_digits() -> None:
    ctx = make_context()
    draw(ctx, {"type": "finish", "face": "face:end", "ra": 0.025})
    assert "Ra 0,025" in texts(ctx)


def test_the_general_finish_is_the_symbol_and_the_basic_symbol_in_parentheses() -> None:
    ctx = make_context()
    (r,) = draw(ctx, {"type": "finish", "general": True, "ra": 6.3})
    assert r.status == "ok", r.detail
    assert "Ra 6,3" in texts(ctx) and texts(ctx).count("(") == 1 and texts(ctx).count(")") == 1
    xs = [t.x for t in ctx.sheet.entities if isinstance(t, model.Text)]
    assert max(xs) > 300  # at the right of the area, near the title block


def test_finish_checks_its_arguments() -> None:
    for spec, message in (
        ({}, "exactly one of face"),
        ({"face": "face:end", "general": True, "ra": 1}, "exactly one of face"),
        ({"face": "face:end"}, "ra is required"),
        ({"face": "face:end", "ra": 1, "machining": "maybe"}, "must be one of"),
        ({"face": "face:end", "ra": -1}, "between"),
    ):
        with pytest.raises(sc.ItemError, match=message):
            items.normalise({"type": "finish", **spec}, set(), 1)
    items.normalise({"type": "finish", "face": "face:end", "machining": "forbidden"}, set(), 1)  # no value needed


# --- the thread arc ---------------------------------------------------------------------------------------------------


def test_a_tapped_hole_gets_the_thin_arc_at_the_major_diameter_in_the_view_that_shows_a_circle() -> None:
    ctx = make_context()
    arcs = sheet_thread.thread_entities(ctx)
    assert len(arcs) == 1  # the top view only; the front and right views see it from the side
    arc = arcs[0]
    assert isinstance(arc, model.Arc) and arc.layer == model.THREAD
    assert (arc.cx, arc.cy) == pytest.approx(ctx.views["top"].project((64.0, 30.0, 6.0)))
    assert arc.r == pytest.approx(3.0)  # M6: the major diameter, outside the 5 mm tap drill
    assert (arc.end - arc.start) % 360 == pytest.approx(270.0)  # three quarters of a circle


def test_the_thread_arc_follows_the_scale_and_skips_plain_and_foreign_threads() -> None:
    ctx = make_context()
    ctx.views["top"].scale = 2.0
    assert sheet_thread.thread_entities(ctx)[0].r == pytest.approx(6.0)
    ctx.holes[1].thread = ("G1/8", None)
    assert sheet_thread.thread_entities(ctx) == []
    assert sheet_thread.major_diameter("M8x1") == 8.0 and sheet_thread.major_diameter("M2.5") == 2.5


def test_the_thread_layer_is_declared_in_the_dxf() -> None:
    ctx = make_context()
    ctx.sheet.extend(sheet_thread.thread_entities(ctx))
    data = sheet_dxf.to_dxf(ctx.sheet).decode("cp1252")
    assert "ROSCAS" in data


# --- a whole sheet ----------------------------------------------------------------------------------------------------


def acceptance_items() -> list[dict[str, Any]]:
    return [
        {"type": "overall"},
        {"type": "hole_dim", "face": "face:bore", "fit": "H7", "id": "bore"},
        {"type": "hole_dim", "face": "face:tapped", "id": "tap"},
        {"type": "dim", "kind": "diameter", "face": "face:journal", "fit": "g6", "id": "jrn"},
        {"type": "datum", "letter": "A", "on": "bore"},
        {"type": "dim", "kind": "distance", "from": {"face": "face:bore"}, "to": {"face": "face:tapped"}, "along": "x"},
        {"type": "gdt", "char": "total_runout", "value": 0.02, "datums": ["A"], "on": "jrn"},
        {"type": "gdt", "char": "position", "value": 0.1, "diameter": True, "datums": ["A"], "on": "tap"},
        {"type": "gdt", "char": "flatness", "value": 0.02, "face": "face:bottom"},
        {"type": "finish", "face": "face:bore", "ra": 1.6},
        {"type": "finish", "general": True, "ra": 3.2},
    ]


def test_the_whole_acceptance_set_draws_without_errors() -> None:
    ctx = make_context(general="ISO 2768-mK")
    results = draw(ctx, *acceptance_items())
    assert [r.status for r in results] == ["ok"] * len(results) or all(
        r.status in ("ok", "warning") for r in results
    ), [(r.id, r.status, r.detail) for r in results]
    assert not [r for r in results if r.status == "error"]
    shown = texts(ctx)
    for wanted in ("Ø12 H7 (+0,018/0) pasante", "Ø24 g6 (-0,007/-0,02)", "Ra 1,6", "Ra 3,2", "0,02", "Ø0,1"):
        assert wanted in shown, wanted


def test_the_same_items_draw_the_same_sheet_and_the_dxf_is_readable() -> None:
    a, b = make_context(general="ISO 2768-mK"), make_context(general="ISO 2768-mK")
    draw(a, *acceptance_items())
    draw(b, *acceptance_items())
    assert a.sheet.entities == b.sheet.entities
    data = sheet_dxf.to_dxf(a.sheet)
    pytest.importorskip("ezdxf")
    from ezdxf import recover  # noqa: PLC0415

    document, auditor = recover.read(io.BytesIO(data))
    assert not auditor.errors and not auditor.has_fixes
    assert len(list(document.modelspace())) > 60


# --- the review sees the new items ------------------------------------------------------------------------------


def reviewed(ctx: sc.Context, *specs: dict[str, Any]) -> list[review.Finding]:
    taken: set[str] = set()
    normalised = []
    for number, spec in enumerate(specs, start=1):
        done = items.normalise(spec, taken, number)
        taken.add(done["id"])
        normalised.append(done)
    results = items.draw_all(ctx, normalised)
    return review.review(review.ReviewInput(normalised, {r.id: r for r in results}, ctx, ctx.sheet))


def test_a_frame_citing_an_undefined_datum_is_one_datum_undefined_finding() -> None:
    found = reviewed(
        make_context(),
        {"type": "gdt", "char": "runout", "value": 0.02, "datums": ["A", "B"], "face": "face:journal", "id": "f1"},
        BORE,
        DATUM_A,
    )
    undefined = [f for f in found if f.code == "datum_undefined"]
    assert len(undefined) == 1 and undefined[0].items == ("f1",) and "B" in undefined[0].message
    assert not [f for f in found if f.code == "item_error" and f.items == ("f1",)]  # not reported twice


GDT_NOTES_THAT_SHOULD_BE_FRAMES = [
    "Perpendicularidad del Ø42 H7 respecto a A: 0,02",
    "Coaxialidad Ø0,05 respecto a A-B",
    "Salto radial 0,03 en el Ø32",
    "Planitud 0,02 en la cara de apoyo",
    "Tolerancia de posición Ø0,1 respecto a A y B",
    "Posición (tolerancia) 0,1 a los datums A y B",
    "⊥ Ø0,1 A",
    "Perpendicularity 0.05 to datum A",
    "Flatness of the seat: 0.02",
]
NOTES_THAT_ARE_NOT_TOLERANCE_FRAMES = [
    "Fabricar de lámina rectificada",
    "Rebabas eliminadas",
    "Posición de los agujeros: ver cotas",  # names a characteristic, no tolerance value
    "Planitud según marco",  # no value
    "Posición del taladro a 12,5 mm del borde",  # a distance, not a tolerance of position
    "Ra 1,6 en todas las caras mecanizadas",
]


def note_findings(text: str) -> list[review.Finding]:
    found = reviewed(make_context(), {"type": "note", "id": "n1", "text": text})
    return [f for f in found if f.code == "gdt_as_note"]


@pytest.mark.parametrize("text", GDT_NOTES_THAT_SHOULD_BE_FRAMES)
def test_a_geometric_tolerance_written_as_a_note_is_flagged_as_a_frame_missing(text: str) -> None:
    (finding,) = note_findings(text)
    assert finding.items == ("n1",) and finding.severity == "warning" and "ISO 1101" in finding.message
    assert "gdt" in finding.message
    from sheet_checks import NON_BLOCKING  # noqa: PLC0415

    assert "gdt_as_note" not in NON_BLOCKING  # the export waits for it (an answer "not applicable" clears it)


@pytest.mark.parametrize("text", NOTES_THAT_ARE_NOT_TOLERANCE_FRAMES)
def test_an_ordinary_note_is_not_taken_for_a_geometric_tolerance(text: str) -> None:
    assert note_findings(text) == []


def test_a_real_frame_with_its_datums_leaves_the_review_quiet_about_notes_and_datums() -> None:
    found = reviewed(
        make_context(),
        BORE,
        DATUM_A,
        {"type": "gdt", "char": "runout", "value": 0.02, "datums": ["A"], "face": "face:journal", "id": "f1"},
        {"type": "note", "id": "n1", "text": "Fabricar de lámina rectificada"},
    )
    assert not {f.code for f in found} & {"gdt_as_note", "datum_undefined", "datum_unused"}


def test_a_datum_no_frame_cites_is_still_flagged_by_the_review() -> None:
    found = reviewed(make_context(), BORE, DATUM_A)
    unused = [f for f in found if f.code == "datum_unused"]
    assert len(unused) == 1 and "datum A" in unused[0].message


def test_the_review_does_not_flag_the_tolerance_items_as_dimensions_or_errors() -> None:
    found = reviewed(make_context(general="ISO 2768-mK"), *acceptance_items())
    flagged = {f.code for f in found}
    assert not flagged & {
        "duplicate_dimension",
        "over_dimensioned",
        "datum_undefined",
        "item_error",
        "stale_reference",
        "dim_no_reference",
        "feature_no_dimension",
    }, [(f.code, f.message) for f in found]


def test_the_overall_size_of_a_round_part_is_its_diameter_given_once() -> None:
    ctx = make_context()
    (r,) = draw(ctx, {"type": "overall"})
    assert r.status == "ok" and r.detail == "width Ø40, height 20 mm", r.detail
    assert [t for t in texts(ctx) if t in ("Ø40", "40", "20")] == ["Ø40", "20"]  # no second 40 in the top view


def test_a_dimension_that_repeats_an_overall_extent_is_a_duplicate() -> None:
    height = {
        "type": "dim",
        "kind": "distance",
        "from": {"face": "face:bottom"},
        "to": {"face": "face:end"},
        "view": "front",
        "tol": 0.1,
        "id": "h",
    }
    found = reviewed(make_context(), {"type": "overall", "id": "o"}, height)
    (dup,) = [f for f in found if f.code == "duplicate_dimension"]
    assert dup.items == ("o", "h") and "20" in dup.message
    other = {**height, "to": {"at": [70.0, 30.0, 3.0]}, "id": "s"}  # a shorter distance is not one
    assert not [
        f for f in reviewed(make_context(), {"type": "overall", "id": "o"}, other) if f.code == "duplicate_dimension"
    ]


def test_a_position_frame_on_a_feature_no_dimension_locates_is_incomplete() -> None:
    bore = {**BORE, "id": "b"}
    tap = {"type": "hole_dim", "face": "face:tapped", "id": "t"}
    position = {"type": "gdt", "char": "position", "value": 0.1, "diameter": True, "datums": ["A"], "on": "t"}
    datum = {**DATUM_A, "on": "b"}
    found = reviewed(make_context(), bore, tap, datum, position)
    (missing,) = [f for f in found if f.code == "gdt_not_located"]
    assert missing.items == (position.get("id") or "gt4",) or "gt4" in missing.items[0]
    assert "position" in missing.message and "Ø5" in missing.message
    where = {
        "type": "dim",
        "kind": "distance",
        "from": {"face": "face:bore"},
        "to": {"face": "face:tapped"},
        "along": "x",
    }
    assert not [f for f in reviewed(make_context(), bore, tap, datum, where, position) if f.code == "gdt_not_located"]
    flat = {"type": "gdt", "char": "flatness", "value": 0.02, "face": "face:bottom"}
    assert not [f for f in reviewed(make_context(), flat) if f.code == "gdt_not_located"]  # form: nothing to locate
