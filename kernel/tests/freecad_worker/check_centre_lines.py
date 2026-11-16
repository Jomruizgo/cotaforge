# ruff: noqa: E501
"""A centre line is a chain line, made with TechDraw's own centre-line object (real TechDraw, no window).

The axis of a hole seen from the side is a ``CenterLine`` between the two sides of the hole; a centre mark, which has no two
edges to hang from, is a cosmetic line. Both carry TechDraw's line number of the chain line (4 with ISO 128-20), which is what
the window paints and the PDF exports; the review reads the page and names a centre line that is not one.
"""

from typing import Any

import drawing
import sheet_linetype
import sheet_native
from check_drawing_live import _page, _rec, add, build_part
from harness import call, op

CHAIN = 4


def views() -> dict[str, Any]:
    page = _page()
    found = {name: sheet_native.view_object(page, name) for name in sheet_native.view_names(page)}
    return {name: view for name, view in found.items() if view is not None}


def numbers(view: Any, prop: str) -> list[int]:
    return sheet_linetype.numbers_of(view, prop) if list(getattr(view, prop)) else []


def codes() -> list[str]:
    return [f["code"] for f in call("describe_drawing", op("r", review=True))["sheets"][0]["findings"]]


def sheet_with_lines() -> None:
    build_part()
    call("create_drawing", op("d", template="A3", title="Placa", scale=1.0))
    result = next(r for r in add({"type": "view_lines", "id": "vl", "hidden": ["front"]})["items"] if r["id"] == "vl")
    assert result["status"] in ("ok", "warning"), result


def check_the_axes_are_centre_line_objects_and_every_centre_line_is_the_chain_line() -> None:
    sheet_with_lines()
    page = _page()
    objects = 0
    for name, view in views().items():
        centre, cosmetic = numbers(view, "CenterLines"), numbers(view, "CosmeticEdges")
        marks = [n for e, n in zip(view.CosmeticEdges, cosmetic, strict=True) if dict(e.Format)["style"] == sheet_native.ISO_STYLE["centre"]]
        assert all(n == CHAIN for n in centre + marks), (name, centre, cosmetic)
        assert sheet_native.own_centre_count(view) == len(centre), (name, len(centre))  # none left behind, none of another's
        assert all(abs(float(line.Format["weight"]) - 0.18) < 1e-9 for line in view.CenterLines), name
        objects += len(centre)
    assert objects >= 5, objects  # the holes stand on five axes in the front view: each hangs from the two sides of its hole (hidden edges)
    assert len(list(views()["front"].CenterLines)) == 5 and not [e for e in views()["front"].CosmeticEdges]  # no cosmetic line stands for an axis
    assert sheet_native.line_type_problems(page) == []
    assert sheet_native.verify_centre_lines(page) == []  # TechDraw drew each where the sheet asked
    assert "centre_line_not_chain" not in codes()  # a correct sheet has nothing to flag
    problems = drawing.compare_window(_rec(), None, "check")
    assert problems == [], problems


def check_adding_to_the_sheet_keeps_one_centre_line_per_axis() -> None:
    sheet_with_lines()
    before = {name: len(list(view.CenterLines)) for name, view in views().items()}
    add({"type": "note", "id": "n1", "text": "Sin rebabas"})
    add({"type": "note", "id": "n2", "text": "Material con certificado"})
    assert {name: len(list(view.CenterLines)) for name, view in views().items()} == before
    assert all(sheet_native.own_centre_count(view) == len(list(view.CenterLines)) for view in views().values())


def centre_line_ends(view: Any) -> list[tuple[float, ...]]:
    """The centre lines of ``view`` as TechDraw draws them now (the view's own units), each from its lower end."""
    frame = sheet_native.Frame(_page(), view)
    count = len(list(view.CenterLines))
    ends = [sheet_native._line_ends(e) for e in frame.edges[len(frame.edges) - count :]]  # noqa: SLF001
    return sorted(tuple(round(v, 3) for v in (row if row[1] <= row[3] else (row[2], row[3], row[0], row[1]))) for row in ends)


def check_a_centre_line_follows_the_model_and_its_view_with_no_harness() -> None:
    """The engineer opens the file in plain FreeCAD, makes the plate thicker and moves the views. A centre line is TechDraw's
    own object between two edges: where the view keeps the numbers of its edges (the right view, the centre line between the
    two faces of the plate) TechDraw draws it again from them, with no harness. TechDraw names the edges by number, though,
    and numbers them again when the hidden edges of a view change (the front view, measured in 1.1.3): those centre lines
    land on other edges, as a dimension of TechDraw does, until the sheet is updated: it makes again every centre line whose
    two edges are no longer the sides of its feature (``test_sheet_linetype``). Only the first is checked here."""
    sheet_with_lines()
    doc = _rec().fc_doc
    right = views()["right"]
    assert centre_line_ends(right) == [(0.0, -12.0, 0.0, 12.0)]  # the plate is 19 thick: 2.5 past each face
    pad = next(o for o in doc.Objects if str(o.TypeId) == "PartDesign::Pad")
    pad.Length = 29.0
    right.X = float(right.X) + 12.0
    doc.recompute()
    for _ in range(50):
        if "Touched" not in [str(x) for x in right.State]:
            break
        doc.recompute()
    assert centre_line_ends(right) == [(0.0, -17.0, 0.0, 17.0)]  # followed the faces of the thicker plate, in the moved view
    assert numbers(right, "CenterLines") == [CHAIN]


def check_the_review_names_a_centre_line_of_the_sheet_that_is_not_a_chain_line() -> None:
    sheet_with_lines()
    assert "centre_line_not_chain" not in codes()
    front = views()["front"]
    sheet_linetype.apply(front, "CenterLines", {0: 2})  # one of the sheet's own, left the dashed line
    _rec().fc_doc.recompute()
    assert ("front", "centre line", 2) in sheet_native.line_type_problems(_page())
    assert "centre_line_not_chain" in codes()
    add({"type": "note", "id": "n3", "text": "Sin rebabas"})  # any update puts it right
    assert "centre_line_not_chain" not in codes()


def check_the_review_never_judges_a_centre_line_the_engineer_made() -> None:
    sheet_with_lines()
    front = views()["front"]
    lines = [row[0] for row in sheet_native._model_lines(sheet_native.Frame(_page(), front))]  # noqa: SLF001
    own = len(list(front.CenterLines))
    front.makeCenterLine([f"Edge{lines[0]}", f"Edge{lines[1]}"], 2)  # by hand: TechDraw gives it the engineer's own line style
    _rec().fc_doc.recompute()
    assert len(list(front.CenterLines)) == own + 1
    for number in (2, 5):  # whatever his style is
        sheet_linetype.apply(front, "CenterLines", {own: number})
        assert sheet_native.line_type_problems(_page()) == [], number
        assert "centre_line_not_chain" not in codes(), number


def check_a_centre_line_the_engineer_deletes_stays_deleted() -> None:
    sheet_with_lines()
    front = views()["front"]
    before = len(list(front.CenterLines))
    front.removeCenterLine(str(front.CenterLines[0].Tag))  # what deleting it in the window does
    _rec().fc_doc.recompute()
    for _ in range(2):  # an item added, a review, the next update
        add({"type": "note", "id": f"n{_}", "text": f"Sin rebabas {_}"})
        assert len(list(front.CenterLines)) == before - 1
        assert codes().count("line_deleted_by_hand") == 1
        rows = call("describe_drawing", op("r", review=True))["sheets"][0]["findings"]
        assert not [r for r in rows if r["code"] == "line_deleted_by_hand" and r["blocking"]]
        assert "window_differs" not in [r["code"] for r in rows]
    assert drawing.compare_window(_rec(), None, "check") == []
