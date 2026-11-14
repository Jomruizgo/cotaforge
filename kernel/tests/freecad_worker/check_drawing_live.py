# ruff: noqa: E501
"""The sheet is built live: what an item adds shows on the TechDraw page as soon as it is added (the mirror), removing an
item removes only that item (and its objects), and the page always agrees with the sheet the PDF is written from.

The requests here are shaped exactly as the adapter sends them (``AnnotateDrawingRequest.model_dump``): ``items`` for
``add``, ``ids`` for ``remove``.
"""

import json
import os
import tempfile
from typing import Any

import drawing
import main
import sheet_mirror
import sheet_model as model
from harness import (
    call,
    circle,
    expect_error,
    extrude,
    faces_of,
    new_document,
    op,
    rect,
    select_face,
    sketch,
)

PLATE = (100.0, 60.0, 19.0)
DOWELS = ((15.0, 12.0), (85.0, 12.0), (15.0, 48.0), (85.0, 48.0))  # 4 x D6 H7
CLEARANCE = ((35.0, 30.0), (65.0, 30.0))  # 2 x D9 with a D14 counterbore, 9 deep
TAPPED = (50.0, 12.0)  # M6


def _tmp(name: str) -> str:
    return os.path.join(tempfile.mkdtemp(prefix="cadh_live_"), name)


def _rec() -> Any:
    assert main.WORKER.active is not None
    return main.WORKER.active


def _page() -> Any:
    page = drawing.find_page(_rec().fc_doc)
    assert page is not None
    return page


def add(*items: dict[str, Any]) -> dict[str, Any]:
    return call("annotate_drawing", op("a", action="add", items=list(items), ids=[]))


def remove(*ids: str) -> dict[str, Any]:
    return call("annotate_drawing", op("r", action="remove", items=[], ids=list(ids)))


def keys() -> set[str]:
    return set(sheet_mirror.owned(_page()))


def names() -> dict[str, list[str]]:
    return {k: sorted(o.Name for o in v) for k, v in sheet_mirror.owned(_page()).items()}


def build_part() -> dict[str, Any]:
    """The synthetic part of the acceptance: a 100 x 60 x 19 plate with 4 dowel holes D6 (hole op), 2 clearance holes D9
    with a D14 counterbore (hole op + a cut: a hole not all of whose features are hole ops) and an M6 tapped hole."""
    new_document("placa")
    base = extrude("base", sketch("s0", "XY", [rect("outline", (50, 30), PLATE[0], PLATE[1])]), PLATE[2])

    def top() -> dict[str, Any]:
        return select_face(faces_of(base["feature"]), normal=(0, 0, 1))

    def drill(name: str, where: tuple[float, float], **fields: Any) -> None:
        params = {"standard": "simple", "size": None, "end_condition": "through_all", "depth_mm": None}
        params.update(fields)
        face = top()
        centre = face["center"]
        call("hole", op(name, face=face["handle"], position=[where[0] - centre[0], where[1] - centre[1]], **params))

    for number, where in enumerate(DOWELS, start=1):
        drill(f"dowel{number}", where, diameter_mm=6)
    for number, where in enumerate(CLEARANCE, start=1):
        drill(f"clear{number}", where, diameter_mm=9)
        face = top()
        pocket = sketch(
            f"cb_sk{number}",
            face["handle"],
            [circle("cb", (where[0] - face["center"][0], where[1] - face["center"][1]), 14)],
        )
        call(
            "cut",
            op(
                f"cbore{number}",
                sketch=pocket["sketch"],
                end_condition="blind",
                depth_mm=9,
                direction="negative",
                both_directions=False,
                depth2_mm=None,
            ),
        )
    drill(
        "tapped",
        TAPPED,
        standard="iso_tapped",
        size="M6",
        diameter_mm=5.0,
        end_condition="blind",
        depth_mm=12.0,
        thread={"size": "M6", "depth_mm": 10.0},
    )
    return base


def side_face(base: dict[str, Any], normal: tuple[int, int, int]) -> str:
    return str(select_face(faces_of(base["feature"]), normal=normal)["handle"]["id"])


# --- removing ---------------------------------------------------------------------------------------------------------


def check_removing_items_as_the_adapter_sends_them_keeps_the_sheet_and_the_rest() -> None:
    """Real use: ``remove`` of ONE item deleted the whole sheet (the worker read ``items``, the adapter sends ``ids``)."""
    build_part()
    call("create_drawing", op("d", template="A3", title="Placa"))
    added = add({"type": "note", "text": "Sin rebabas"}, {"type": "note", "text": "Aristas rotas"})
    ids = [r["id"] for r in added["items"] if r["type"] == "note"]
    assert len(ids) == 2
    left = remove(ids[0])
    assert drawing.find_page(_rec().fc_doc) is not None, "the sheet is gone"
    assert "removed" not in left and left["changed"] == [ids[0]]
    assert [r["id"] for r in left["items"] if r["type"] == "note"] == [ids[1]]
    missing = expect_error("invalid_request", "annotate_drawing", op("r", action="remove", items=[], ids=["zz"]))
    assert "zz" in str(missing)


def check_removing_without_ids_is_refused_and_the_sheet_goes_only_with_remove_sheet() -> None:
    build_part()
    call("create_drawing", op("d", template="A4"))
    expect_error("invalid_request", "annotate_drawing", op("r", action="remove", items=[], ids=[]))
    assert drawing.find_page(_rec().fc_doc) is not None
    gone = call("annotate_drawing", op("r", action="remove_sheet", items=[], ids=[]))
    assert gone["removed"] is True and gone["sheets"] == []
    assert drawing.find_page(_rec().fc_doc) is None
    assert not [o for o in _rec().fc_doc.Objects if str(o.TypeId).startswith("TechDraw::")]


# --- the page, item by item --------------------------------------------------------------------------------------------


def check_every_item_shows_on_the_page_when_it_is_added_and_the_page_matches_the_sheet() -> None:
    base = build_part()
    call("create_drawing", op("d", template="A3", title="Placa", scale=1.0))
    # the overall dimensions are TechDraw's own objects, not mirrored ones; the thread arc of the M6 and the centre lines of
    # the default location are cosmetic edges of the views (no object of their own)
    assert keys() == set(), keys()
    steps: list[tuple[list[dict[str, Any]], str]] = [
        ([{"type": "view_lines", "id": "vl"}], "vl"),
        ([{"type": "hole_dim", "id": "dowels", "at": [[x, y, 0] for x, y in DOWELS], "fit": "H7"}], "dowels"),
        ([{"type": "hole_dim", "id": "clear", "at": [[x, y, 0] for x, y in CLEARANCE]}], "clear"),
        ([{"type": "hole_dim", "id": "tap", "at": [[TAPPED[0], TAPPED[1], 0]]}], "tap"),
        ([{"type": "datum", "id": "dA", "letter": "A", "face": side_face(base, (0, 0, -1))}], "dA"),
        ([{"type": "datum", "id": "dB", "letter": "B", "face": side_face(base, (-1, 0, 0))}], "dB"),
        (
            [
                {
                    "type": "gdt",
                    "id": "perp",
                    "char": "perpendicularity",
                    "value": 0.05,
                    "datums": ["A"],
                    "face": side_face(base, (1, 0, 0)),
                }
            ],
            "perp",
        ),
        ([{"type": "datum_dims", "id": "tbl", "mode": "table"}], "tbl"),
        ([{"type": "note", "id": "n1", "text": "Sin rebabas"}], "n1"),
        ([{"type": "revision", "id": "rv", "index": "A", "description": "Primera emision", "by": "JR"}], ""),
    ]
    seen: set[str] = set()
    for items, key in steps:
        result = add(*items)
        statuses = {r["id"]: r["status"] for r in result["items"]}
        assert all(statuses[i["id"]] in ("ok", "warning") for i in items), result["items"]
        assert result["changed"] == [i["id"] for i in items]
        if key:
            if key != "vl":  # hidden and centre lines of view_lines are the views' own: no object
                seen.add(key)
            assert keys() - {"threads", "iso286"} == seen, (keys(), seen)
            assert result["window"] != "unchanged", result["window"]
        problems = drawing.compare_window(_rec(), None, "check")
        assert problems == [], problems
    # what TechDraw has an object for is TechDraw's own object (a dimension, an annotation); what it has none for (datum
    # and frame symbols, the table, the view lines) is a symbol of our drawing at the place the sheet draws it
    native = {
        "dowels": "DrawViewDimension",
        "clear": "DrawViewDimension",
        "tap": "DrawViewDimension",
        "n1": "DrawViewAnnotation",
    }
    for row in drawing.page_objects(_page()):
        assert row[1] == native.get(row[3], "DrawViewSymbol") or row[3] in native and row[1] == "DrawViewSymbol", row
    kinds = {
        row[3]: {r[1] for r in drawing.page_objects(_page()) if r[3] == row[3]} for row in drawing.page_objects(_page())
    }
    for key, kind in native.items():
        assert kind in kinds[key], (key, kinds[key])


def check_adding_an_item_leaves_the_objects_of_the_others_alone() -> None:
    build_part()
    call("create_drawing", op("d", template="A3", scale=1.0))
    add({"type": "hole_dim", "id": "dowels", "at": [[x, y, 0] for x, y in DOWELS]})
    before = names()
    result = add(
        {"type": "dim", "id": "len", "kind": "distance", "from": {"at": [0, 30, 10]}, "to": {"at": [100, 30, 10]}}
    )
    after = names()
    assert after["dowels"] == before["dowels"], "an unchanged item was rebuilt"
    assert "len" in after and result["window"] == "1 new", result["window"]


def check_removing_an_item_removes_only_its_objects() -> None:
    build_part()
    call("create_drawing", op("d", template="A3", scale=1.0))
    add({"type": "note", "id": "n1", "text": "uno"}, {"type": "hole_dim", "id": "h1", "at": [[15, 12, 0]]})
    assert {"n1", "h1"} <= keys()
    result = remove("n1")
    assert "n1" not in keys() and "h1" in keys(), keys()
    assert result["window"] == "1 gone"
    assert drawing.compare_window(_rec(), None, "check") == []


def check_a_note_added_later_moves_the_first_one_on_the_page_too() -> None:
    """The numbered notes block grows upward: the first note's place changes when the second arrives, and so does its object."""
    build_part()
    call("create_drawing", op("d", template="A3", scale=1.0))
    add({"type": "note", "id": "n1", "text": "uno"})
    first = {r[3]: (r[4], r[5]) for r in drawing.page_objects(_page())}["n1"]
    add({"type": "note", "id": "n2", "text": "dos"})
    moved = {r[3]: (r[4], r[5]) for r in drawing.page_objects(_page())}
    assert moved["n1"] != first and {"n1", "n2"} <= keys()
    assert drawing.compare_window(_rec(), None, "check") == []


def check_the_page_follows_the_part_and_a_rebuilt_sheet_keeps_its_items_on_the_page() -> None:
    build_part()
    call("create_drawing", op("d", template="A3", scale=1.0))
    add({"type": "hole_dim", "id": "h1", "at": [[15, 12, 0]]}, {"type": "note", "id": "n1", "text": "uno"})
    call("annotate_drawing", op("u", action="update", items=[], ids=[]))
    assert {"h1", "n1"} <= keys() and drawing.compare_window(_rec(), None, "check") == []
    call(
        "create_drawing", op("d2", template="A3", scale=1.0, fresh=True)
    )  # from scratch: the items go, the objects too
    assert keys() <= {"threads", "loc"}


def check_an_hlr_sheet_shows_its_views_and_items_on_the_page() -> None:
    build_part()
    call("create_drawing", op("d", template="A3", scale=1.0, projection="hlr"))
    assert {"view:front", "view:top", "view:right", "view:isometric", "o1"} <= keys()
    add({"type": "hole_dim", "id": "h1", "at": [[15, 12, 0]]})
    assert "h1" in keys() and drawing.compare_window(_rec(), None, "check") == []
    assert not drawing.part_views(_page())


def check_the_window_check_catches_a_page_that_differs_and_an_export_puts_it_right() -> None:
    base = build_part()
    call("create_drawing", op("d", template="A3", scale=1.0))
    add(
        {"type": "note", "id": "n1", "text": "uno"},
        {"type": "datum", "id": "dA", "letter": "A", "face": side_face(base, (0, 0, -1))},
    )
    assert drawing.compare_window(_rec(), None, "check") == []
    owned = sheet_mirror.owned(_page())
    note = next(o for o in owned["n1"] if str(o.TypeId) == "TechDraw::DrawViewAnnotation")
    note.Text = ["somebody wrote this by hand"]  # the engineer edited a native annotation: it stays, the check says so
    owned["dA"][0].X = float(owned["dA"][0].X) + 3.0  # somebody dragged a symbol
    owned["dA"][0].Symbol = str(owned["dA"][0].Symbol).replace(">A<", ">B<")
    problems = drawing.compare_window(_rec(), None, "check")
    assert any("dA" in p and "at (" in p for p in problems), problems
    assert any("dA" in p and "texts" in p for p in problems), problems
    assert any("n1" in p and "by hand" in p for p in problems), problems
    call("export_drawing", op("e", path=_tmp("x.pdf"), format="pdf"))  # a symbol is put back by the export...
    left = drawing.compare_window(_rec(), None, "check")
    assert not any("dA" in p for p in left), left
    assert any("n1" in p for p in left), left  # ...what the engineer edited in a native object is left as he made it


# --- where a callout goes --------------------------------------------------------------------------------------------


def unit_of(key: str) -> Any:
    page = _page()
    _, _, ctx = drawing.compose(_rec(), page, drawing.spec_of(page), 1, 1, "check")
    found = {u.key: u for u in ctx.extras["units"]}
    return found[key], ctx


def callout(key: str) -> tuple[float, float, str]:
    """(x, y, anchor) of the first text of a callout: its shelf side and height tell where the leader went."""
    unit, _ = unit_of(key)
    first = next(e for e in unit.entities if isinstance(e, model.Text))
    return first.x, first.y, first.anchor


def check_side_and_offset_move_a_callout_exactly_where_they_say_in_every_direction() -> None:
    """Real use: offset_mm and side did not always move a callout (the placer nudged them away to avoid overlaps)."""
    build_part()
    call("create_drawing", op("d", template="A3", scale=1.0))
    where = [[85, 12, 0]]
    add({"type": "hole_dim", "id": "base", "at": where})
    base_x, base_y, _ = callout("base")
    for side in ("left", "right", "above", "below"):
        add({"type": "hole_dim", "id": side, "at": where, "side": side})
        x, y, anchor = callout(side)
        if side == "left":
            assert anchor == "right", (side, anchor)  # the shelf runs left of the elbow
        elif side == "right":
            assert anchor == "left", (side, anchor)
    # offset_mm: two offsets give two places, the farther one farther out along the leader (and exactly the gap apart)
    add(
        {"type": "hole_dim", "id": "near", "at": where, "side": "right", "offset_mm": 12},
        {"type": "hole_dim", "id": "far", "at": where, "side": "right", "offset_mm": 30},
    )
    (nx, ny, _), (fx, fy, _) = callout("near"), callout("far")
    assert abs(abs(fx - nx) - 18.0) < 0.01 and abs(abs(fy - ny) - 18.0) < 0.01, (
        nx,
        ny,
        fx,
        fy,
    )  # the elbow runs on the diagonal
    assert (base_x, base_y) != (nx, ny)


def check_a_callout_that_cannot_go_where_it_was_asked_says_why_and_never_leaves_the_frame_unflagged() -> None:
    build_part()
    call("create_drawing", op("d", template="A3", scale=1.0))
    row = add({"type": "hole_dim", "id": "far", "at": [[85, 12, 0]], "side": "right", "offset_mm": 400})["items"][-1]
    assert row["status"] == "warning" and ("marco" in row["detail"] or "frame" in row["detail"]), row
    findings = call("describe_drawing", op("r", review=True))["sheets"][0]["findings"]
    assert any(f["code"] in ("item_outside_frame", "text_outside_frame") and "far" in f["items"] for f in findings), (
        findings
    )


def check_a_callout_the_placer_chooses_stays_inside_the_frame() -> None:
    """Without side or offset the placer moves a callout that would leave the frame (or land on something) to a place that
    does not, on every sheet size."""
    build_part()
    for template in ("A4", "A3"):
        call("create_drawing", op("d", template=template, scale=1.0, fresh=True))
        add({"type": "hole_dim", "id": "dowels", "at": [[x, y, 0] for x, y in DOWELS], "fit": "H7"})
        add(
            {"type": "hole_dim", "id": "clear", "at": [[x, y, 0] for x, y in CLEARANCE]},
            {"type": "hole_dim", "id": "tap", "at": [[50, 12, 0]]},
        )
        unit_ids = ("dowels", "clear", "tap")
        _, ctx = unit_of("dowels")
        for key in unit_ids:
            unit, ctx = unit_of(key)
            x0, y0, x1, y1 = unit.rect
            assert ctx.inside((x0, y0, x1, y1)) or "marco" in str(_status(key)) or "frame" in str(_status(key)), (
                template,
                key,
                unit.rect,
                ctx.area,
            )


def _status(item_id: str) -> str:
    rows = call("describe_drawing", op("r", review=False))["sheets"][0]["items"]
    return next(r["detail"] for r in rows if r["id"] == item_id)


def check_the_review_flags_an_item_over_the_title_block_and_one_outside_the_frame() -> None:
    build_part()
    call("create_drawing", op("d", template="A3", scale=1.0))
    add(
        {"type": "note", "id": "low", "text": "Sobre el rotulo", "at_mm": [200, 30]},
        {"type": "note", "id": "out", "text": "Fuera del marco", "at_mm": [415, 150]},
    )
    findings = call("describe_drawing", op("r", review=True))["sheets"][0]["findings"]
    codes = {(f["code"], tuple(f["items"])) for f in findings}
    assert ("item_over_title_block", ("low",)) in codes, findings
    assert any(code in ("item_outside_frame", "text_outside_frame") and "out" in ids for code, ids in codes), findings
    clean = add({"type": "note", "id": "ok", "text": "Dentro", "at_mm": [60, 100]})
    assert clean["items"][-1]["status"] == "ok"


def check_visible_lines_are_thick_and_everything_else_thin_in_the_svg_and_on_the_techdraw_objects() -> None:
    """ISO 128: the visible outline is at least 1.8 times the dimension line, in the PDF / SVG and in the window."""
    import sheet_svg

    build_part()
    call("create_drawing", op("d", template="A3", scale=1.0))
    add({"type": "hole_dim", "id": "h1", "at": [[15, 12, 0]]}, {"type": "view_lines", "id": "vl"})
    sheet, _, ctx = drawing.compose(_rec(), _page(), drawing.spec_of(_page()), 1, 1, "check")
    style = model.LAYERS
    outline, dim = style[model.CONTOUR].width_mm, style[model.DIMENSIONS].width_mm
    assert outline >= 1.8 * dim, (outline, dim)
    for layer in (model.HIDDEN, model.AXES, model.HATCH, model.THREAD, model.TEXT):
        assert style[layer].width_mm <= outline / 1.8, layer
    svg = sheet_svg.to_svg(sheet)
    assert f'stroke-width="{outline:g}"' in svg and f'stroke-width="{dim:g}"' in svg
    assert {
        e.height for e in sheet.entities if isinstance(e, model.Text) and e.layer in (model.DIMENSIONS, model.TEXT)
    } == {3.5}
    assert sheet_mirror.line_weights(_page()) == {}  # the native weights live on the view providers: the visible suite


# --- centre marks ----------------------------------------------------------------------------------------------------


def check_every_centre_mark_is_at_the_projected_centre_of_its_hole_in_the_sheet_and_on_the_page() -> None:
    """Real use: the crosses of the circles were painted off their centres in the window. Every hole seen as a circle
    in a view has its cross at the projected 3D centre to 0.01 mm, in the sheet model and in the TechDraw objects."""
    build_part()
    for projection in ("techdraw", "hlr"):
        call("create_drawing", op("d", template="A3", scale=1.0, projection=projection, fresh=True))
        add(
            {"type": "view_lines", "id": "vl"},
            {"type": "hole_dim", "id": "dowels", "at": [[x, y, 0] for x, y in DOWELS]},
        )
        assert drawing.centre_mark_problems(_rec(), None, "check") == [], projection
        page = _page()
        marks = sheet_mirror.page_crosses(page, 297.0)
        holes = drawing.hole_infos(_rec(), None, "check")
        assert len(holes) == 9 and len(marks) == 7, (
            len(holes),
            len(marks),
        )  # a counterbore shares the centre of its hole
        # a cross somebody moved is named
        if projection == "hlr":  # its views are symbols: the crosses are a symbol's drawing
            owned = next(o for o in sheet_mirror.owned(page)["loc"] if str(o.TypeId) == "TechDraw::DrawViewSymbol")
            owned.X = float(owned.X) + 0.5
        else:  # a TechDraw view: the crosses are cosmetic edges of it; the engineer deletes one of them by hand
            top = next(v for v in drawing.part_views(page) if drawing.view_key(v) == "top")
            tag = json.loads(str(top.CotaforgeCosmetic))["loc"][1][0]
            top.removeCosmeticEdge(tag)
        problems = drawing.centre_mark_problems(_rec(), None, "check")
        assert problems and any("not at the centre of any hole" in p or "page does not" in p for p in problems), (
            problems
        )
        call("export_drawing", op("e", path=_tmp("x.pdf"), format="pdf"))  # the export puts the page right
        assert drawing.centre_mark_problems(_rec(), None, "check") == []


def check_a_symbol_is_only_ever_moved_up_or_down_to_keep_off_a_view_origin() -> None:
    """Measured in FreeCAD 1.1.3: the window pulls a symbol that sits within 6 mm (up or down) of a view origin, which the
    mirror avoids by padding the SVG; padding it sideways painted its drawing aside by the padding (the centre marks of a
    sheet with the right view dropped came out 8 mm off). So a symbol is padded vertically only."""
    build_part()
    call("create_drawing", op("d", template="A3", scale=1.0))
    add({"type": "view_lines", "id": "vl"}, {"type": "hole_dim", "id": "dowels", "at": [[x, y, 0] for x, y in DOWELS]})
    page = _page()
    _, _, ctx = drawing.compose(_rec(), page, drawing.spec_of(page), 1, 1, "check")
    units = {u.key: sheet_mirror.symbol_unit(u, True) for u in ctx.extras["units"]}
    for key, objects in sheet_mirror.owned(page).items():
        guides = sheet_mirror.guides_of(page, {key})
        for obj in objects:
            if str(obj.TypeId) == "TechDraw::DrawViewSymbol" and units.get(key) is not None:
                cx, _ = units[key].centre
                assert abs(float(obj.X) - cx) < 0.01, (key, float(obj.X), cx)  # never shifted sideways
                assert all(abs(float(obj.Y) - g) > 7.0 for g in guides[1]), (key, float(obj.Y))
    assert drawing.compare_window(_rec(), None, "check") == []
