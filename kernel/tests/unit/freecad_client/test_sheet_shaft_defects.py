# ruff: noqa: E501
"""What a machinist rejects on the sheet of a stepped shaft with a threaded end and a keyway, and on the polar dimensions of a
bolt circle (no FreeCAD): a diameter given twice, a diameter by leader instead of a dimension line on its step, a thread
callout without its class, a keyway with no length and no position (its ends are half circles) and an angle arc over the
bore."""

import sys
from pathlib import Path
from typing import Any

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import sheet_collide  # noqa: E402
import sheet_context as sc  # noqa: E402
import sheet_items as items  # noqa: E402
import sheet_keyway as kw  # noqa: E402
import sheet_pattern as sp  # noqa: E402
import sheet_model as model  # noqa: E402
import sheet_plan_bridge as bridge  # noqa: E402
import sheet_plan_layout as pl  # noqa: E402
import sheet_thread  # noqa: E402

from .test_sheet_external_thread import DIAMETER, draw, make_context, record  # noqa: E402

X = (1.0, 0.0, 0.0)


# --- a shaft along x seen from the front: Ø20 from x 0 to 28, Ø32 from 28 to 103 (1:1, the axis at page y 150) -----------------------


class ShaftX(sc.Geometry):
    STEPS = {"face:a": (10.0, 0.0, 28.0), "face:b": (16.0, 28.0, 103.0), "face:c": (11.0, 103.0, 168.0)}

    def __init__(self) -> None:
        self.faces = {
            name: sc.FaceInfo(name, "cylinder", (lo + (hi - lo) / 2, 0.0, 0.0), radius=r, axis_point=(0.0, 0.0, 0.0), axis_dir=X, surface_point=(lo + 1.0, 0.0, r))
            for name, (r, lo, hi) in self.STEPS.items()
        }

    def face(self, face_id: str) -> sc.FaceInfo:
        return self.faces[face_id]

    def round_extent(self) -> tuple[sc.Vec3, float] | None:
        return X, 32.0

    def cylinder_span(self, face_id: str, across: sc.Vec3) -> tuple[float, float] | None:
        return self.STEPS[face_id][1:]


def shaft_context(*found: dict[str, Any]) -> sc.Context:
    front = sc.ViewInfo("front", 60.0, 150.0, 1.0, (0, -1, 0), (1, 0, 0), (84.0, 0.0, 0.0), (60.0 - 84.0 + 84.0, 134.0, 228.0, 166.0))
    # the view's origin is at the page x of the model x 84 (the middle of the shaft): the part runs from page x 0 to 168 + 0
    front = sc.ViewInfo("front", 0.0, 150.0, 1.0, (0, -1, 0), (1, 0, 0), (0.0, 0.0, 0.0), (0.0, 134.0, 168.0, 166.0))
    return sc.Context(
        sheet=model.Sheet(420.0, 297.0),
        views={"front": front},
        holes=[],
        geometry=ShaftX(),
        area=(-30.0, 40.0, 400.0, 282.0),
        language="es",
        threads=sheet_thread.external_threads(list(found)),
    )


def dia(face: str, **over: Any) -> dict[str, Any]:
    return {"type": "dim", "kind": "diameter", "face": [face], "id": f"d_{face[-1]}", **over}


def dimension_lines(ctx: sc.Context) -> list[model.Line]:
    return [e for e in ctx.sheet.entities if isinstance(e, model.Line) and e.layer == model.DIMENSIONS]


def test_a_diameter_of_a_turned_part_is_a_dimension_line_between_its_silhouette_lines_not_a_leader() -> None:
    ctx = shaft_context()
    (r,) = draw(ctx, dia("face:b"))
    assert r.status == "ok", r.detail
    vertical = [ln for ln in dimension_lines(ctx) if abs(ln.x1 - ln.x2) < 1e-6 and abs(abs(ln.y2 - ln.y1) - 32.0) < 0.6]
    assert vertical, "no dimension line of 32 mm between the two silhouette lines"
    assert not [ln for ln in dimension_lines(ctx) if abs(ln.x1 - ln.x2) > 0.5 and abs(ln.y1 - ln.y2) > 0.5], "a leader was drawn"
    assert "Ø32" in ctx.sheet.texts()
    x = vertical[0].x1
    assert 28.0 < x < 103.0  # it stands on its own step


def test_a_diameter_that_the_step_dimensions_is_not_given_again_by_the_overall_height() -> None:
    ctx = shaft_context()
    specs = [{"type": "overall", "id": "o1"}, dia("face:b")]
    ctx.extras["items"] = specs  # the sheet as the CAD stores it: what `overall` reads to know a step already says the diameter
    results = draw(ctx, *specs)
    assert all(r.status == "ok" for r in results), [r.detail for r in results]
    assert [t for t in ctx.sheet.texts() if "32" in t] == ["Ø32"], ctx.sheet.texts()


def test_the_overall_height_stays_when_no_step_gives_the_largest_diameter() -> None:
    ctx = shaft_context()
    specs = [{"type": "overall", "id": "o1"}, dia("face:a")]
    ctx.extras["items"] = specs
    draw(ctx, *specs)
    assert "Ø32" in ctx.sheet.texts()


def test_the_texts_of_the_steps_spread_along_the_shaft_and_never_meet() -> None:
    ctx = shaft_context()
    draw(ctx, dia("face:a", fit="k6"), dia("face:b"), dia("face:c", fit="k6"))
    boxes = [sheet_collide.text_box(e) for e in ctx.sheet.entities if isinstance(e, model.Text)]
    assert len(boxes) == 3
    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            assert a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1], (a, b)


def test_a_diameter_asked_with_a_side_keeps_its_leader() -> None:
    ctx = shaft_context()
    (r,) = draw(ctx, dia("face:b", side="above"))
    assert r.status == "ok", r.detail
    assert [ln for ln in dimension_lines(ctx) if abs(ln.x1 - ln.x2) > 0.5 and abs(ln.y1 - ln.y2) > 0.5]


def test_a_diameter_dimension_on_its_step_is_an_obstacle_for_the_planner_not_a_leader_it_moves() -> None:
    ctx = shaft_context()
    (r,) = draw(ctx, dia("face:b"))
    units = [sc_unit for sc_unit in _units(ctx, "d_b")]
    page = (420.0, 297.0)
    prepared = bridge.prepare(page, (-30.0, 40.0, 400.0, 282.0), None, {"front": (0.0, 134.0, 168.0, 166.0)}, units, [{"id": "d_b", "type": "dim", "kind": "diameter", "face": ["face:b"]}])
    assert r.status == "ok" and not prepared.unread and not prepared.movable


def _units(ctx: sc.Context, key: str) -> list[Any]:
    import sheet_units as su  # noqa: PLC0415

    start, end = ctx.extras["item_slices"][key]
    return [su.Unit(key, su.KIND_ITEM, "dim", tuple(ctx.sheet.entities[start:end]))]


# --- the thread: designation with its class --------------------------------------------------------------------------------------


def test_the_callout_of_a_thread_carries_its_class_and_its_length() -> None:
    ctx = make_context(record(tolerance_class="6g"))
    draw(ctx, DIAMETER)
    assert "M24 - 6g long. útil 15" in ctx.sheet.texts()


def test_a_thread_with_no_class_keeps_its_callout_as_it_was() -> None:
    ctx = make_context(record())
    draw(ctx, DIAMETER)
    assert "M24 long. útil 15" in ctx.sheet.texts()


# --- the keyway: half circles at its ends ----------------------------------------------------------------------------------------


def keyway_slot() -> kw.Slot:
    cylinder = kw.Cylinder(X, (0.0, 0.0, 0.0), 11.0, False, 118.0, 167.0)
    flats = [
        kw.Flat((0.0, 1.0, 0.0), (135.0, 3.0, 9.25), "face:15"),
        kw.Flat((0.0, -1.0, 0.0), (135.0, -3.0, 9.25), "face:17"),
        kw.Flat((0.0, 0.0, 1.0), (135.0, 0.0, 7.5), "face:18"),
        kw.Flat((-1.0, 0.0, 0.0), (118.0, 0.0, 0.0), "face:10"),  # the shoulder the Ø22 end starts at
    ]
    (slot,) = kw.find_slots([cylinder], flats)
    ends = [kw.Round("face:14", (125.0, 0.0, 9.0), (0.0, 0.0, 1.0), 3.0), kw.Round("face:16", (144.0, 0.0, 9.0), (0.0, 0.0, 1.0), 3.0)]
    return kw.with_rounds(slot, ends)


def test_a_keyway_closed_by_half_circles_is_dimensioned_from_the_shoulder_to_its_first_point_and_over_its_whole_length() -> None:
    slot = keyway_slot()
    assert [r.face_id for r in slot.rounds] == ["face:14", "face:16"]
    specs = {s["id"]: s for s in kw.dimension_specs(slot, "kw", ("front",), "A-A", "P9")}
    assert specs["kw_w"]["fit"] == "P9" and specs["kw_w"]["view"] == "A-A"
    assert specs["kw_l"]["from"] == {"at": [122.0, 0.0, 9.25]} and specs["kw_l"]["to"] == {"at": [147.0, 0.0, 9.25]}  # 25 long, end to end
    assert specs["kw_p"]["from"] == {"face": "face:10"} and specs["kw_p"]["to"] == {"at": [122.0, 0.0, 9.25]}  # 4 from the shoulder
    assert specs["kw_l"]["along"] == specs["kw_p"]["along"] == "x"


def test_a_keyway_a_plane_ends_keeps_its_planes() -> None:
    cylinder = kw.Cylinder(X, (0.0, 0.0, 0.0), 11.0, False, 118.0, 167.0)
    flats = [
        kw.Flat((0.0, 1.0, 0.0), (135.0, 3.0, 9.25), "face:15"),
        kw.Flat((0.0, -1.0, 0.0), (135.0, -3.0, 9.25), "face:17"),
        kw.Flat((0.0, 0.0, 1.0), (135.0, 0.0, 7.5), "face:18"),
        kw.Flat((1.0, 0.0, 0.0), (122.0, 0.0, 9.0), "face:20"),
        kw.Flat((-1.0, 0.0, 0.0), (147.0, 0.0, 9.0), "face:21"),
        kw.Flat((-1.0, 0.0, 0.0), (118.0, 0.0, 0.0), "face:10"),
    ]
    (slot,) = kw.find_slots([cylinder], flats)
    assert kw.with_rounds(slot, [kw.Round("face:14", (125.0, 0.0, 9.0), (0.0, 0.0, 1.0), 3.0)]).rounds == ()
    ids = {s["id"] for s in kw.dimension_specs(slot, "kw", ("front",))}
    assert {"kw_l", "kw_p"} <= ids


# --- the angle of a bolt circle: the arc stands outside the holes -------------------------------------------------------------------


def test_the_arc_of_a_first_hole_angle_is_never_drawn_nearer_than_the_radius_it_came_with() -> None:
    view = pl.View("top", 140.0, 140.0, at=(100.0, 40.0))
    sheet = pl.Sheet(paper=(420.0, 297.0), frame=(20.0, 10.0, 410.0, 287.0), title_block=(230.0, 229.0, 410.0, 287.0))
    centre = (70.0, 70.0)  # the apex, local to the view
    near = pl.plan_layout(sheet, [view], [pl.Item("a", "angle", "top", 9.0, 3.5, (centre,), extra=(0.0, 22.5))])
    far = pl.plan_layout(sheet, [view], [pl.Item("a", "angle", "top", 9.0, 3.5, (centre,), extra=(0.0, 22.5, 51.0))])
    assert not near.unplaced and not far.unplaced
    assert near.items[0].data[0] < 51.0  # the planner's own radii start at 12 mm: over the bore of a flange
    assert far.items[0].data[0] >= 51.0


# --- the same note twice, the class of a thread ------------------------------------------------------------------------------------


def test_a_note_that_says_what_another_note_says_is_refused_naming_it() -> None:
    first = {"id": "n1", "type": "note", "text": "Sin tratamiento térmico."}
    again = {"id": "n2", "type": "note", "text": "sin  tratamiento   térmico."}
    try:
        items.refuse_repeated_note(again, [first])
    except sc.ItemError as exc:
        assert "n1" in str(exc) and "same words" in str(exc)
    else:
        raise AssertionError("the repeated note was accepted")
    items.refuse_repeated_note({"id": "n3", "type": "note", "text": "Cantidad 10."}, [first])  # another note: fine
    items.refuse_repeated_note({"id": "d1", "type": "dim", "text": first["text"]}, [first])  # not a note: not this rule's


def test_the_class_of_an_external_thread_is_an_iso_965_class_or_nothing() -> None:
    from cotaforge.adapters.freecad.external_thread import ExternalThread  # noqa: PLC0415

    face = {"kind": "face", "from_op": "shaft", "select": {"surface": "cylinder"}}
    base: dict[str, Any] = {"face": face, "end": face, "size": "M20x1.5", "length_mm": 26}
    assert ExternalThread(**base).tolerance_class is None
    assert ExternalThread(**base, tolerance_class=" 6G ").tolerance_class == "6g"
    for wrong in ("6H7", "xx", "g6", "12g"):
        try:
            ExternalThread(**base, tolerance_class=wrong)
        except ValueError:
            continue
        raise AssertionError(f"{wrong!r} was accepted as a thread class")


# --- a lone hole of a round part is located by its radius and its angle, a hole of a rectangle is not ----------------------------------------------


def lone_pin() -> list[Any]:
    from .test_sheet_pattern import bore, hole, ring  # noqa: PLC0415

    flange = [bore(0), *ring(8, 40.0, first=22.5, start=1), hole(9, 21.65, 12.5, 4.0, None)]  # Ø4 at R25, 30 degrees
    return flange


def test_a_lone_hole_of_a_round_part_gets_its_polar_dimensions_by_default_counted_about_the_axis_of_the_part() -> None:
    found = sp.auto_items(lone_pin(), ["top", "iso"], (sc.unit((0.0, 0.0, 1.0)), (0.0, 0.0, 6.0)))
    by_id = {i["id"]: i for i in found}
    assert {"pd1", "pd1a", "pd2", "pd2a"} == set(by_id), by_id  # the bolt circle's two and the pin's two
    assert by_id["pd2"]["at"] == [21.65, 12.5, 12.0] and by_id["pd2"]["about"] == [0.0, 0.0, 6.0]
    assert by_id["pd2a"]["kind"] == "angle" and by_id["pd2a"]["about"] == [0.0, 0.0, 6.0]
    assert "about" not in by_id["pd1"]  # the pattern is counted from its own centre


def test_the_bore_on_the_axis_and_the_holes_of_a_part_that_is_not_round_are_not_on_a_radius() -> None:
    found = sp.auto_items(lone_pin(), ["top"], (sc.unit((0.0, 0.0, 1.0)), (0.0, 0.0, 6.0)))
    assert all(i["at"] != [0.0, 0.0, 12.0] for i in found)  # the bore
    assert [i["id"] for i in sp.auto_items(lone_pin(), ["top"])] == ["pd1", "pd1a"]  # no round axis: the pin keeps the default location


def test_a_hole_counted_about_the_axis_draws_its_pitch_circle_and_is_located_by_it() -> None:
    from .test_sheet_pattern import context, run  # noqa: PLC0415

    ctx = context(lone_pin())
    stored, results = run(ctx, *sp.auto_items(lone_pin(), ["top"], (sc.unit((0.0, 0.0, 1.0)), (0.0, 0.0, 6.0))))
    assert all(r.status == "ok" for r in results), [r.detail for r in results]
    assert 9 in ctx.extras["polar_located"] and "Ø50" in ctx.sheet.texts() and stored
