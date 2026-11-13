# ruff: noqa: E501
"""The quality floor of a drawing, in the headless worker: the default location of the holes, the views a sheet may have
(a plate in ONE view with its thickness as a note), third angle, the papers, the material of the title block and the notes
that refer to it.

What TechDraw PAINTS needs the GUI binary (``tests/integration/freecad/test_visible_quality_sheet.py`` measures the leaders
and the stacked rows there); here the sheet is composed and inspected as data.
"""

from typing import Any

import drawing
import main
from check_drawing_live import CLEARANCE, DOWELS, TAPPED, add, build_part, remove
from harness import call, op


def _rec() -> Any:
    assert main.WORKER.active is not None
    return main.WORKER.active


def _page(sheet: str | None = None) -> Any:
    page = drawing.find_page(_rec().fc_doc, sheet)
    assert page is not None
    return page


def _spec(sheet: str | None = None) -> dict[str, Any]:
    return drawing.spec_of(_page(sheet))


def _findings(sheet: str | None = None) -> list[dict[str, Any]]:
    return call("describe_drawing", op("r", review=True, sheet=sheet))["sheets"][0]["findings"]


def _metrics(sheet: str | None = None) -> dict[str, Any]:
    return drawing.quality_metrics(_rec(), sheet, "q")


def _holes() -> list[dict[str, Any]]:
    return [
        {"type": "hole_dim", "id": "dowels", "at": [[x, y, 0] for x, y in DOWELS], "fit": "H7", "dowel": True},
        {"type": "hole_dim", "id": "clear", "at": [[x, y, 0] for x, y in CLEARANCE]},
        {"type": "hole_dim", "id": "tap", "at": [[TAPPED[0], TAPPED[1], 0]]},
    ]


def check_the_default_location_counts_from_the_symmetry_and_from_the_declared_datums() -> None:
    from check_drawing_live import side_face  # noqa: PLC0415

    base = build_part()  # symmetric across x = 50 (one plane), not across y
    made = call("create_drawing", op("d", template="A3", views=["front", "top", "iso"]))
    loc = next(i for i in made["items"] if i["id"] == "loc")
    assert loc["status"] == "warning" and "Ask the engineer which face or edge" in loc["detail"], loc
    add(*_holes())
    m = _metrics()
    holes = [h for h in m["holes"].values() if h["diameter"] in (5.0, 6.0, 9.0)]
    assert holes and all("x" in h and "y" not in h for h in holes), holes  # x by the symmetry, y asks for a reference
    codes = {f["code"] for f in _findings()}
    assert "hole_partly_located" in codes and "hole_not_located" not in codes, codes
    assert not any(v in _page_texts() for v in ("15", "12", "48")), "nothing is counted from the corner of the box"
    add(
        {"type": "datum", "id": "dC", "letter": "C", "face": side_face(base, (0, -1, 0))}
    )  # the engineer's reference in y
    m = _metrics()
    holes = [h for h in m["holes"].values() if h["diameter"] in (5.0, 6.0, 9.0)]
    assert all("x" in h and "y" in h for h in holes), holes
    assert not [f for f in _findings() if f["code"] in ("hole_not_located", "hole_partly_located", "collision")], (
        _findings()
    )


def _page_texts() -> list[str]:
    page = _page()
    found = drawing.compose(_rec(), page, drawing.spec_of(page), 1, 1, "q")[0]
    return list(found.texts())


def check_the_default_location_leaves_the_holes_an_explicit_dimension_locates() -> None:
    build_part()
    call("create_drawing", op("d", template="A3", views=["front", "top", "iso"]))
    add({"type": "datum_dims", "id": "mine", "holes": [[x, y, 0] for x, y in CLEARANCE]})
    positions = _metrics()["holes"]
    covered = [h for h in positions.values() if h["diameter"] == 9.0]
    assert covered and all("x" in h and "y" in h for h in covered)
    removed = remove("loc")  # the engineer does not want the default: it does not come back
    assert "loc" not in {i["id"] for i in removed["items"]}
    add(*_holes())
    assert "loc" not in {i["id"] for i in call("describe_drawing", op("l"))["sheets"][0]["items"]}
    assert "hole_not_located" in {f["code"] for f in _findings()}  # the review says what is missing


def check_a_plate_is_drawn_in_one_view_with_its_thickness_as_a_note() -> None:
    build_part()
    made = call("create_drawing", op("d", template="A3", views=["top"]))
    assert made["views"] == ["top"], made["views"]
    assert _metrics()["views"] == ["top"]
    add(*_holes(), {"type": "note", "id": "n1", "text": "Fabricar de lámina rectificada"})
    texts = _metrics()["texts"]
    assert "1. 19 de espesor" in texts, [t for t in texts if t[:1].isdigit() and "." in t[:3]]
    assert any(t.startswith("2. Fabricar") for t in texts)
    assert any("ISO 286" in t and t.startswith("3. ") for t in texts), "the footnote goes in the numbered block"
    assert not [f for f in _findings() if f["code"] in ("hole_not_located", "collision", "view_adds_nothing")], (
        _findings()
    )
    # the front view may be taken off a sheet that has ONE other standard view
    call("create_drawing", op("d2", template="A3", sheet="Dos"))
    dropped = call("annotate_drawing", op("v", action="remove_view", view="right", sheet="Dos", items=[], ids=[]))
    assert "right" not in dropped["views"]
    again = call("annotate_drawing", op("v", action="remove_view", view="front", sheet="Dos", items=[], ids=[]))
    assert again["views"] == ["top", "isometric"], again["views"]
    assert "tn" in {i["id"] for i in again["items"]}


def check_a_second_sheet_of_one_view_names_its_view_by_its_tag_not_its_label() -> None:
    """FreeCAD makes labels unique in a document: the anchor of the second sheet is ``View_top001`` there, so the view is
    known by the key the sheet tagged it with."""
    build_part()
    call("create_drawing", op("d1", template="A3"))
    made = call("create_drawing", op("d2", template="A3", sheet="Una", views=["top"]))
    assert made["views"] == ["top"]
    assert _metrics("Una")["views"] == ["top"], _metrics("Una")["views"]
    assert _metrics("Drawing")["views"] == ["front", "right", "top"]


def check_the_review_suggests_one_view_for_a_flat_part_and_the_title_block_takes_fields() -> None:
    build_part()
    call("create_drawing", op("d", template="A3", views=["front", "top", "iso"]))
    assert "single_view_enough" in {f["code"] for f in _findings()}, "a 19 mm plate with only through holes"
    call(
        "annotate_drawing",
        op(
            "u",
            action="update",
            items=[],
            ids=[],
            title_block={"legal_owner_2": "Taller Sur", "fixed_note_1": "Tratamiento: ninguno"},
        ),
    )
    fields = _spec()["fields"]
    assert fields["legal_owner_2"] == "Taller Sur" and fields["fixed_note_1"] == "Tratamiento: ninguno"
    texts = _metrics()["texts"]
    assert "Taller Sur" in texts and "Tratamiento: ninguno" in texts
    try:
        call("annotate_drawing", op("u2", action="update", items=[], ids=[], title_block={"scale": "9:9"}))
    except Exception as exc:  # noqa: BLE001 - the worker's own error type carries the message
        assert "no settable field scale" in str(exc), exc
    else:
        raise AssertionError("a field the sheet writes itself cannot be set")
    try:  # Aprobó is the engineer's: only approve fills it
        call("annotate_drawing", op("u3", action="update", items=[], ids=[], title_block={"approval_person": "Eve"}))
    except Exception as exc:  # noqa: BLE001
        assert "no settable field approval_person" in str(exc), exc
    else:
        raise AssertionError("the approver is not a title block field the agent sets")


def check_third_angle_puts_the_top_view_above_the_front_one() -> None:
    build_part()
    made = call("create_drawing", op("d", template="A3", angle="third", views=["front", "top", "iso"]))
    assert made["angle"] == "third"
    page = _page()
    infos = drawing.view_infos(page)
    assert infos["top"].y > infos["front"].y, (infos["top"].y, infos["front"].y)  # y is up the page
    group = next(o for o in page.Views if str(o.TypeId) == "TechDraw::DrawProjGroup")
    assert str(group.ProjectionType) == "Third angle"


def check_every_paper_has_its_template_with_the_projection_symbol() -> None:
    build_part()
    for number, size in enumerate(("A4", "A3", "A2", "A1", "A0", "Letter")):
        made = call("create_drawing", op(f"d{number}", template=size, sheet=f"S{number}"))
        assert made["template"] == size and made["page_mm"] == list(drawing.sheet_layout.PAGES_MM[size]), made
        assert not made["warnings"] or all("fit" not in w for w in made["warnings"]), made["warnings"]
    third = call("create_drawing", op("t", template="A2", angle="third", language="en", sheet="T"))
    assert third["angle"] == "third" and third["language"] == "en"


def check_the_material_of_the_title_block_follows_the_design() -> None:
    build_part()
    call("set_design_attributes", op("m", material="Acero 1045"))
    call("create_drawing", op("d", template="A3"))
    add({"type": "note", "id": "n1", "text": "Material según rótulo"})
    assert _metrics()["material"] == "Acero 1045"
    assert not [f for f in _findings() if f["code"] == "material_title_block_empty"]
    call("set_design_attributes", op("m2", material=""))
    assert "material_title_block_empty" in {f["code"] for f in _findings()}, "the note points at an empty field"
