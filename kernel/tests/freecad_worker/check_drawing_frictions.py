# ruff: noqa: E501
"""What a drawing asks of the engineer must be a function, not a number: the part's own symmetry locates a hole, a
reference the sheet does not have is asked for, a revolved part is drawn in the views its profile needs, a diameter in a
section is dimensioned between its silhouette lines, and a sheet that is made again keeps what is still valid in it.

Synthetic parts only: a plate with one plane of symmetry (``check_drawing_live.build_part``), a sleeve with a bore and two
flats, a plain shaft.
"""

from typing import Any

import drawing
import main
import sheet_model as model
from check_drawing_live import add, build_part, remove, side_face
from harness import call, circle, extrude, faces_of, new_document, op, rect, select_face, sketch


def _rec() -> Any:
    assert main.WORKER.active is not None
    return main.WORKER.active


def _page() -> Any:
    page = drawing.find_page(_rec().fc_doc)
    assert page is not None
    return page


def _composed() -> tuple[Any, list[Any], Any]:
    page = _page()
    return drawing.compose(_rec(), page, drawing.spec_of(page), 1, 1, "frictions")


def _findings() -> list[dict[str, Any]]:
    return call("describe_drawing", op("r", review=True))["sheets"][0]["findings"]


def build_sleeve(bore: bool = True, flats: bool = True) -> dict[str, Any]:
    """A round bar D60 x 50 along z, a through bore D43.5 on its axis, two flats of the upper 30 mm on its sides."""
    new_document("manguito")
    base = extrude("base", sketch("s0", "XY", [circle("od", (0, 0), 60)]), 50)

    def top() -> dict[str, Any]:
        return select_face(faces_of(base["feature"]), normal=(0, 0, 1))

    if bore:
        face = top()
        call(
            "hole",
            op(
                "bore",
                face=face["handle"],
                position=[-face["center"][0], -face["center"][1]],
                standard="simple",
                size=None,
                end_condition="through_all",
                depth_mm=None,
                diameter_mm=43.5,
            ),
        )
    if flats:
        for number, x in enumerate((28.5, -28.5), start=1):
            face = top()
            pocket = sketch(
                f"fs{number}", face["handle"], [rect("f", (x - face["center"][0], -face["center"][1]), 5, 70)]
            )
            call(
                "cut",
                op(
                    f"flat{number}",
                    sketch=pocket["sketch"],
                    end_condition="blind",
                    depth_mm=30,
                    direction="negative",
                    both_directions=False,
                    depth2_mm=None,
                ),
            )
    return base


def _symmetry() -> dict[str, float]:
    geometry = drawing.LiveGeometry(_rec(), drawing._shapes(_rec(), None, "frictions"), "frictions")  # noqa: SLF001
    return geometry.symmetry()


# --- symmetry is measured on the shape -------------------------------------------------------------------------------------


def check_a_plate_with_holes_symmetric_across_x_only_has_one_plane_of_symmetry() -> None:
    build_part()  # 100 x 60 x 19 with holes mirrored across x = 50 but not across y = 30 (the M6 is alone)
    assert _symmetry() == {"x": 50.0}, _symmetry()


def check_a_sleeve_with_two_flats_is_symmetric_across_x_and_y_and_a_plain_cut_is_not() -> None:
    build_sleeve()
    found = _symmetry()
    assert set(found) == {"x", "y"} and all(abs(v) < 1e-6 for v in found.values()), found
    build_sleeve(flats=False)
    assert set(_symmetry()) == {"x", "y", "z"}  # the bore and the bar alone: round and the same either end
    new_document("asimetrica")
    box = extrude("base", sketch("s0", "XY", [rect("r", (20, 10), 40, 20)]), 10)
    assert set(_symmetry()) == {"x", "y", "z"}  # a box is symmetric about the middle of its box
    notch = sketch("ns", select_face(faces_of(box["feature"]), normal=(0, 0, 1))["handle"], [rect("n", (-12, 5), 8, 6)])
    call(
        "cut",
        op(
            "notch",
            sketch=notch["sketch"],
            end_condition="blind",
            depth_mm=5,
            direction="negative",
            both_directions=False,
            depth2_mm=None,
        ),
    )
    assert _symmetry() == {}, "a notch in one corner breaks every plane"


# --- the default location -------------------------------------------------------------------------------------------------------


def check_a_hole_on_the_axis_of_a_symmetric_part_is_located_by_the_part_not_by_a_number() -> None:
    build_sleeve()
    call("create_drawing", op("d", template="A3", views=["front", "top", "iso"], scale=1.0))
    sheet, results, ctx = _composed()
    loc = next(r for r in results if r.id == "loc")
    assert loc.status == "ok" and "symmetry" in loc.detail, loc
    assert ctx.extras["loc_axes"] == {0: {"x", "y"}}, ctx.extras["loc_axes"]
    shown = {e.text for e in sheet.of_layer(model.DIMENSIONS) if isinstance(e, model.Text)}
    assert shown == {"Ø60", "50"}, shown  # the overall size and no number from the edge of the box to the centre
    codes = {f["code"] for f in _findings()}
    assert not codes & {"hole_not_located", "hole_partly_located", "hole_no_reference"}, codes


# --- the views of a revolved part ----------------------------------------------------------------------------------------------


def check_a_revolved_part_with_a_bore_gets_its_end_view_and_the_section_through_the_axis() -> None:
    build_sleeve()
    made = call("create_drawing", op("d", template="A3", scale=1.0))
    assert made["views"] == ["top"], made["views"]
    assert any("views chosen from the part" in w for w in made["warnings"]), made["warnings"]
    section = next(i for i in made["items"] if i["type"] == "section")
    assert section["status"] == "ok" and "through the axis of the part" in section["detail"], section
    assert "tn" not in {i["id"] for i in made["items"]}, "a sleeve has no thickness to write"
    _, _, ctx = _composed()
    assert "A-A" in ctx.extras["sections"]
    stored = next(i for i in drawing.spec_of(_page())["items"] if i["type"] == "section")
    assert stored["holes"] == "axis"


def check_a_solid_round_part_gets_the_one_view_that_shows_its_profile() -> None:
    new_document("eje")
    extrude("base", sketch("s0", "XY", [circle("od", (0, 0), 40)]), 100)
    made = call("create_drawing", op("d", template="A3"))
    assert made["views"] == ["front"] and not [i for i in made["items"] if i["type"] == "section"], made
    assert "tn" not in {i["id"] for i in made["items"]}, "a shaft seen from the side has no thickness to write"


def check_naming_the_views_keeps_the_old_sheet_and_a_part_that_is_not_round_keeps_its_four() -> None:
    build_sleeve()
    made = call("create_drawing", op("d", template="A3", views=["front", "top", "right", "iso"]))
    assert made["views"] == ["front", "top", "right", "isometric"] and not [
        i for i in made["items"] if i["type"] == "section"
    ]
    build_part()
    plain = call("create_drawing", op("d", template="A3"))
    assert plain["views"] == ["front", "top", "right", "isometric"], plain["views"]
    assert not [w for w in plain["warnings"] if "views chosen" in w]


# --- a diameter in a section ---------------------------------------------------------------------------------------------------


def check_a_diameter_in_a_section_is_a_linear_dimension_and_in_a_view_its_leader_touches_the_surface() -> None:
    base = build_sleeve()
    call("create_drawing", op("d", template="A3", scale=1.0))
    outer = select_face(faces_of(base["feature"]), surface="cylinder")["handle"]["id"]
    result = add({"type": "dim", "id": "od", "kind": "diameter", "face": outer, "view": "A-A"})
    row = next(i for i in result["items"] if i["id"] == "od")
    assert row["status"] == "ok" and "between its two silhouette lines" in row["detail"], row
    sheet, _, ctx = _composed()
    assert "Ø60" in sheet.texts() and not [k for k in ctx.extras.get("leaders", []) if k["item"] == "od"]
    repeated = [f for f in _findings() if f["code"] == "dimension_repeated"]
    assert len(repeated) == 1 and set(repeated[0]["items"]) == {"o1", "od"} and not repeated[0]["blocking"], repeated
    # the front view: the flats took the upper 30 mm of the side the leader points at, the surface is in the lower 20
    call("annotate_drawing", op("v", action="add_view", view="front", items=[], ids=[]))
    add({"type": "dim", "id": "od2", "kind": "diameter", "face": outer, "view": "front"})
    _, _, ctx = _composed()
    (leader,) = [k for k in ctx.extras["leaders"] if k["item"] == "od2"]
    front = ctx.views["front"]
    low, high = front.project((30.0, 0.0, 0.0))[1], front.project((30.0, 0.0, 20.0))[1]
    assert min(low, high) - 0.05 <= leader["tip"][1] <= max(low, high) + 0.05, (leader["tip"], low, high)


# --- making the sheet again ------------------------------------------------------------------------------------------------------


def check_a_rebuilt_sheet_keeps_its_items_answers_and_reviewer_and_lists_what_no_longer_resolves() -> None:
    base = build_part()
    call("create_drawing", op("d", template="A3", scale=1.0, title="Placa"))
    top, bottom = side_face(base, (0, 0, 1)), side_face(base, (0, 0, -1))
    add(
        {"type": "hole_dim", "id": "hd", "all": True},
        {"type": "note", "id": "n1", "text": "Sin rebabas"},
        {"type": "dim", "id": "dz", "kind": "distance", "from": {"face": top}, "to": {"face": bottom}, "view": "right"},
    )
    pending = next(f for f in _findings() if f["code"] == "notes_material")
    call(
        "annotate_drawing",
        op(
            "a",
            action="answer",
            id=pending["id"],
            verdict="not_applies",
            text="el material va en el rótulo",
            items=[],
            ids=[],
        ),
    )
    call("annotate_drawing", op("c", action="review_choice", by="fabricator", items=[], ids=[]))
    before = drawing.spec_of(_page())
    # make it again without the right view and without restating the paper or the scale
    made = call(
        "create_drawing", op("d2", views=["front", "top", "iso"], unset=["template", "scale", "angle", "language"])
    )
    spec = drawing.spec_of(_page())
    assert made["template"] == "A3" and spec["scale_requested"] == 1.0 and spec["fields"]["title"] == "Placa", made
    assert {"hd", "n1", "dz"} <= {i["id"] for i in spec["items"]}, spec["items"]
    assert spec["answers"] == before["answers"] and spec["answers"], spec.get("answers")
    assert spec["review_choice"] == {"who": "fabricator"}
    assert any("answer(s)" in line for line in made["kept"]) and any("fabricator" in line for line in made["kept"]), (
        made["kept"]
    )
    assert any("hd, n1, dz" in line for line in made["kept"]), made["kept"]
    (gone,) = made["unresolved"]
    assert gone.startswith("dz (dim)") and "right" in gone, gone  # the right view is gone: it is said, not dropped
    rows = {i["id"]: i for i in made["items"]}
    assert rows["hd"]["status"] in ("ok", "warning") and rows["dz"]["status"] == "error", rows


def check_a_sheet_made_fresh_starts_from_scratch_and_says_what_it_threw_away() -> None:
    build_part()
    call("create_drawing", op("d", template="A3", scale=1.0))
    add({"type": "hole_dim", "id": "hd", "all": True}, {"type": "note", "id": "n1", "text": "Sin rebabas"})
    call("annotate_drawing", op("c", action="review_choice", by="engineer", items=[], ids=[]))
    made = call("create_drawing", op("d2", template="A3", scale=1.0, fresh=True))
    spec = drawing.spec_of(_page())
    assert [i["id"] for i in spec["items"] if not i.get("auto")] == ["o1"], spec["items"]
    assert "review_choice" not in spec and "answers" not in spec
    assert made["kept"] == [] and any("hd, n1" in line for line in made["discarded"]), made["discarded"]
    assert any("engineer" in line for line in made["discarded"]), made["discarded"]


def check_the_items_the_engineer_took_off_stay_off_after_a_rebuild() -> None:
    build_part()
    call("create_drawing", op("d", template="A3", scale=1.0))
    remove("loc")
    call("create_drawing", op("d2", template="A3", scale=1.0))
    assert "loc" not in {i["id"] for i in drawing.spec_of(_page())["items"]}
    assert drawing.spec_of(_page())["declined"] == ["loc"]
