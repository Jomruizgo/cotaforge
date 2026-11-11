# ruff: noqa: E501
"""The drawing as TechDraw's own: what each item is on the page (native dimensions and annotations a headless FreeCAD can
make and measure), the contract properties, the truth check, the choice of views and the placement pass.

What TechDraw PAINTS (the PDF it exports, the window) needs the GUI binary: ``tests/integration/freecad`` (visible) checks
that. Here every object is made, measured and compared in the headless worker.
"""

from typing import Any

import drawing
import main
import sheet_collide
import sheet_mirror
from check_drawing_live import CLEARANCE, DOWELS, TAPPED, add, build_part, remove, side_face
from harness import call, expect_error, op


def _rec() -> Any:
    assert main.WORKER.active is not None
    return main.WORKER.active


def _page() -> Any:
    page = drawing.find_page(_rec().fc_doc)
    assert page is not None
    return page


def _objects(item: str) -> list[Any]:
    return sheet_mirror.owned(_page()).get(item, [])


def _natives(item: str) -> list[Any]:
    return [o for o in _objects(item) if str(o.TypeId) != "TechDraw::DrawViewSymbol"]


def _findings() -> list[dict[str, Any]]:
    return call("describe_drawing", op("r", review=True))["sheets"][0]["findings"]


# --- native objects per item ----------------------------------------------------------------------------------------


def check_every_item_that_has_a_techdraw_object_is_that_object_with_the_contract_properties() -> None:
    base = build_part()
    call("create_drawing", op("d", template="A3", scale=1.0))
    add(
        {"type": "hole_dim", "id": "dowels", "at": [[x, y, 0] for x, y in DOWELS], "fit": "H7"},
        {"type": "hole_dim", "id": "clear", "at": [[x, y, 0] for x, y in CLEARANCE]},
        {"type": "hole_dim", "id": "tap", "at": [[TAPPED[0], TAPPED[1], 0]]},
        {
            "type": "dim",
            "id": "len",
            "kind": "distance",
            "from": {"at": [0, 30, 10]},
            "to": {"at": [50, 12, 0]},
            "along": "x",
            "tol": [0.1, -0.05],
        },
        {"type": "note", "id": "n1", "text": "Sin rebabas"},
        {"type": "datum", "id": "dA", "letter": "A", "face": side_face(base, (0, 0, -1))},
    )
    for item, kind, value in (
        ("dowels", "Diameter", 6.0),
        ("clear", "Diameter", 14.0),
        ("tap", "Diameter", 5.0),
        ("len", "DistanceX", 50.0),
    ):
        (dim,) = _natives(item)
        assert str(dim.TypeId) == "TechDraw::DrawViewDimension" and str(dim.Type) == kind, (item, dim.Type)
        assert abs(abs(float(dim.getRawValue())) - value) < 0.011, (item, dim.getRawValue())  # FreeCAD measured it
        assert str(dim.CotaforgeItem) == item and str(dim.CotaforgeSheet) == "Drawing" and str(dim.CotaforgeKey) == item
    (dowels,) = _natives("dowels")
    assert dowels.Arbitrary and "4× Ø6 H7" in str(dowels.FormatSpec) and "pasante" in str(dowels.FormatSpec)
    (clear,) = _natives("clear")
    assert "2× Ø9 pasante" in str(clear.FormatSpec) and "⌴ Ø14 ↧ 9" in str(clear.FormatSpec)
    (length,) = _natives("len")
    assert (
        not length.Arbitrary
        and abs(float(length.OverTolerance) - 0.1) < 1e-9
        and abs(float(length.UnderTolerance) + 0.05) < 1e-9
    )
    notes = _natives("n1")
    assert [str(o.TypeId) for o in notes] == [
        "TechDraw::DrawViewAnnotation"
    ] * 2  # the header and the note: a line each
    assert [str(o.Text[0]) for o in notes] == ["NOTAS:", "1. Sin rebabas"] and all(
        str(o.CotaforgeItem) == "n1" for o in notes
    )
    assert not _natives("dA") and all(
        str(o.CotaforgeItem) == "dA" for o in _objects("dA")
    )  # a datum has no TechDraw object
    labels = {str(v.Label): str(v.CotaforgeSheet) for v in drawing.part_views(_page())}
    assert (
        labels
        == {  # the views are named for whoever reads the page; they are no items, so they carry the sheet only
            "View_front": "Drawing",
            "View_top": "Drawing",
            "View_right": "Drawing",
            "View_iso": "Drawing",
        }
    )
    for o in _page().Views:
        if "Dimension" in str(o.TypeId) or "Annotation" in str(o.TypeId):
            assert str(o.CotaforgeSheet) == "Drawing", o.Name
    problems = drawing.compare_window(_rec(), None, "check")
    assert problems == [], problems


def check_the_baseline_dimensions_of_datum_dims_are_native_and_measure_the_positions() -> None:
    base = build_part()
    call("create_drawing", op("d", template="A3", scale=1.0))
    add({"type": "datum_dims", "id": "pos", "x_from": side_face(base, (-1, 0, 0))})
    dims = _natives("pos")
    assert dims and all(str(o.Type) in ("DistanceX", "DistanceY") for o in dims)
    measured = sorted(round(abs(float(o.getRawValue())), 2) for o in dims if str(o.Type) == "DistanceX")
    assert measured == [15.0, 35.0, 50.0, 65.0, 85.0], measured  # from the left face to each distinct hole position
    problems = drawing.compare_window(_rec(), None, "check")
    assert problems == [], problems


def check_a_native_dimension_is_measured_again_and_the_review_names_one_that_is_wrong() -> None:
    build_part()
    call("create_drawing", op("d", template="A3", scale=1.0))
    add(
        {
            "type": "dim",
            "id": "len",
            "kind": "distance",
            "from": {"at": [0, 30, 10]},
            "to": {"at": [50, 12, 0]},
            "along": "x",
        }
    )
    (dim,) = _natives("len")
    view = dim.References2D[0][0]
    dim.References2D = [(view, "Vertex0"), (view, "Vertex1")]  # somebody pointed it at other corners
    _rec().fc_doc.recompute()
    problems = drawing.compare_window(_rec(), None, "check")
    assert any("len" in p and "TechDraw measures" in p for p in problems), problems
    assert any(f["code"] == "window_differs" for f in _findings())


def check_removing_an_item_removes_its_native_objects_and_the_rest_stay() -> None:
    build_part()
    call("create_drawing", op("d", template="A3", scale=1.0))
    add({"type": "hole_dim", "id": "h1", "at": [[15, 12, 0]]}, {"type": "note", "id": "n1", "text": "uno"})
    kept = [o.Name for o in _natives("n1")]
    remove("h1")
    assert not _natives("h1") and [o.Name for o in _natives("n1")] == kept
    assert not [o for o in _page().Views if "Dimension" in str(o.TypeId) and "h1" in str(o.Label)]


def check_hidden_lines_are_techdraws_own_and_follow_the_item() -> None:
    build_part()
    call("create_drawing", op("d", template="A3", scale=1.0))
    front = next(v for v in drawing.part_views(_page()) if str(v.Type) == "Front")
    assert not front.HardHidden
    add({"type": "view_lines", "id": "vl"})
    assert front.HardHidden  # TechDraw draws the hidden edges itself
    problems = drawing.compare_window(_rec(), None, "check")
    assert problems == [], problems
    remove("vl")
    assert not front.HardHidden


# --- the views of a sheet --------------------------------------------------------------------------------------------


def _view_types() -> list[str]:
    return sorted(str(v.Type) for v in drawing.part_views(_page()) if hasattr(v, "Type"))


def check_a_view_can_be_dropped_and_the_scale_grows_to_the_largest_standard_one_that_fits() -> None:
    build_part()
    first = call("create_drawing", op("d", template="A4"))
    assert first["scale"] == 0.5 and first["views"] == ["front", "top", "right", "isometric"], first
    remove("loc")  # the engineer wants no default location: its rows need no room, so the scale can grow
    add({"type": "hole_dim", "id": "h1", "at": [[15, 12, 0]]})
    dropped = call("annotate_drawing", op("v", action="remove_view", view="right", items=[], ids=[]))
    assert dropped["scale"] == 1.0 and dropped["views"] == ["front", "top", "isometric"], dropped
    assert "1 : 2 -> 1 : 1" in " ".join(dropped["warnings"]), dropped["warnings"]
    assert "Right" not in _view_types() and "h1" in sheet_mirror.owned(_page())  # the items are kept, on the new layout
    assert call("describe_drawing", op("r"))["sheets"][0]["views"] == ["front", "top", "isometric"]
    back = call("annotate_drawing", op("v", action="add_view", view="right", items=[], ids=[]))
    assert back["scale"] == 0.5 and "Right" in _view_types()
    expect_error("invalid_request", "annotate_drawing", op("v", action="remove_view", view="front", items=[], ids=[]))
    expect_error("invalid_request", "annotate_drawing", op("v", action="add_view", view="top", items=[], ids=[]))


def check_a_sheet_is_made_with_the_views_asked_for() -> None:
    build_part()
    made = call("create_drawing", op("d", template="A3", views=["front", "top", "iso"]))
    assert made["views"] == ["front", "top", "isometric"] and "Right" not in _view_types()
    assert {o["id"] for o in made["items"]} == {
        "o1",
        "loc",
    }  # the overall dimensions and the default location of the holes
    assert len([o for o in _page().Views if "DimExtent" in str(o.TypeId)]) == 3
    no_top = call("create_drawing", op("d2", template="A3", views=["front", "right"], sheet="Lateral"))
    assert no_top["views"] == ["front", "right"] and no_top["scale"] == 2.0
    assert len([o for o in drawing.find_page(_rec().fc_doc, "Lateral").Views if "DimExtent" in str(o.TypeId)]) == 3
    expect_error("invalid_request", "create_drawing", op("d3", template="A3", views=["top", "right"], sheet="Mal"))


def check_the_review_flags_a_view_that_carries_no_dimension() -> None:
    build_part()
    call("create_drawing", op("d", template="A3", scale=1.0))
    add({"type": "hole_dim", "id": "h1", "at": [[15, 12, 0]]})
    flagged = [f for f in _findings() if f["code"] == "view_adds_nothing"]
    assert [f["items"] for f in flagged] == [["right"]], flagged  # nothing is dimensioned or called out in the right view
    add({"type": "view_lines", "id": "vl"})  # hidden lines and centre lines are not a job for a view
    assert [f["items"] for f in _findings() if f["code"] == "view_adds_nothing"] == [["right"]]  # still no job
    call("annotate_drawing", op("v", action="remove_view", view="right", items=[], ids=[]))
    assert not [f for f in _findings() if f["code"] == "view_adds_nothing"]


# --- placement: nothing lies on anything -----------------------------------------------------------------------------


def check_a_tolerance_frame_never_lies_on_a_dimension_and_the_review_agrees() -> None:
    """Real use: the 60 mm depth dimension and the perpendicularity frame of the right face collided."""
    base = build_part()
    call("create_drawing", op("d", template="A3", scale=1.0))
    add({"type": "datum", "id": "dA", "letter": "A", "face": side_face(base, (0, 0, -1))})
    result = add(
        {
            "type": "gdt",
            "id": "perp",
            "char": "perpendicularity",
            "value": 0.05,
            "datums": ["A"],
            "face": side_face(base, (1, 0, 0)),
        }
    )
    assert {r["id"]: r["status"] for r in result["items"]}["perp"] == "ok", result["items"]
    found = [f for f in _findings() if f["code"] == "collision"]
    assert found == [], found
    page = _page()
    _, _, ctx = drawing.compose(_rec(), page, drawing.spec_of(page), 1, 1, "check")
    assert ctx.extras["collisions"] == []
    perp = next(u for u in ctx.extras["units"] if u.key == "perp")
    for box, _ in sheet_collide.ink_of("perp", perp.entities).texts:  # no text of the frame is on a text of a dimension
        for taken in ctx.extras["texts"]:
            assert min(box[2], taken[2]) <= max(box[0], taken[0]) or min(box[3], taken[3]) <= max(box[1], taken[1]), (
                box,
                taken,
            )


def check_two_things_on_one_place_are_reported_by_item_id() -> None:
    build_part()
    call("create_drawing", op("d", template="A3", scale=1.0))
    add(
        {"type": "note", "id": "a", "text": "Uno encima", "at_mm": [60, 120]},
        {"type": "note", "id": "b", "text": "Otro encima", "at_mm": [62, 121]},
    )
    found = [f for f in _findings() if f["code"] == "collision"]
    assert found and set(found[0]["items"]) == {"a", "b"}, found


def check_a_dimension_text_does_not_lie_on_the_outline_of_a_view_nor_on_a_note() -> None:
    build_part()
    call("create_drawing", op("d", template="A3", scale=1.0))
    remove("loc")  # a dimension of the engineer's through the symmetry line of the default location is another matter
    add(
        {
            "type": "dim",
            "id": "d1",
            "kind": "distance",
            "from": {"at": [0, 30, 10]},
            "to": {"at": [15, 12, 0]},
            "along": "x",
        },
        {
            "type": "dim",
            "id": "d2",
            "kind": "distance",
            "from": {"at": [0, 30, 10]},
            "to": {"at": [50, 12, 0]},
            "along": "x",
        },
    )
    found = [f for f in _findings() if f["code"] == "collision"]
    assert found == [], found


def check_a_dimension_added_by_hand_in_techdraw_silences_the_empty_view_finding() -> None:
    build_part()
    call("create_drawing", op("d", template="A3", scale=1.0))
    add({"type": "hole_dim", "id": "h1", "at": [[15, 12, 0]]})
    assert [f["items"] for f in _findings() if f["code"] == "view_adds_nothing"] == [["right"]]
    page = _page()
    right = next(v for v in drawing.part_views(page) if drawing.view_key(v) == "right")
    dim = _rec().fc_doc.addObject("TechDraw::DrawViewDimension", "HandDim")  # no CotaforgeKey: not the mirror's
    dim.Type = "Distance"
    dim.References2D = [(right, "Vertex0"), (right, "Vertex1")]
    page.addView(dim)
    _rec().fc_doc.recompute()
    assert not [f for f in _findings() if f["code"] == "view_adds_nothing"]
