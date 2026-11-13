# ruff: noqa: E501
"""Assembly drawings in the headless worker: a base plate, two identical blocks, a shaft imported from a STEP file and a
small pin buried inside the plate. The sheet is inspected as ``sheet_model`` data (what the PDF and the DXF are written
from). What matters: the sheet draws the whole assembly without hidden lines, the parts list groups identical
components and names a bought part by its file, the balloons stand on faces the view shows (one hidden instance is
reported, not drawn), the exploded view moves the DRAWING and never the assembly, and the review agrees.
"""

import os
import tempfile
from typing import Any

import drawing
import FreeCAD as App
import Import
import main
import Part
import sheet_model as model
from harness import IDENTITY, call, circle, extrude, linkable, new_document, op, rect, sketch

V = App.Vector


def _rec() -> Any:
    assert main.WORKER.active is not None
    return main.WORKER.active


def _block(name: str, width: float, depth: float, height: float, material: str | None = None) -> dict[str, Any]:
    new_document(name)
    sk = sketch(f"{name}_sk", "XY", [rect("outline", (width / 2, depth / 2), width, depth)])
    extrude(f"{name}_ex", sk, height)
    # the parts list prints the designation the designer gave (doc set_title), never the document or file name
    call("set_design_attributes", op("attrs", title=name, **({"material": material} if material else {})))
    return linkable({"handle": {"id": _rec().id}})


def _imported_shaft(tmp: str) -> dict[str, Any]:
    maker = App.newDocument("shaftmaker")
    obj = maker.addObject("Part::Feature", "shaft")
    obj.Shape = Part.makeCylinder(5, 100, V(0, 0, 0), V(1, 0, 0))
    maker.recompute()
    path = os.path.join(tmp, "eje_d10.step")
    Import.export([obj], path)
    App.closeDocument(maker.Name)
    call("import_geometry", op("imp", path=path, name="eje_d10.step"))
    call("set_design_attributes", op("attrs", title="eje_d10"))
    return linkable({"handle": {"id": _rec().id}})


def _pose(x: float, y: float, z: float) -> dict[str, Any]:
    return {"origin": [x, y, z], "rotation": IDENTITY["rotation"]}


def assembly(tmp: str) -> dict[str, str]:
    """The five components; returns their ids by role."""
    plate = _block("plate", 100, 60, 8, "S235JR")
    block = _block("block", 20, 14, 30, "S235JR")
    pin = _block("pin_m6", 4, 4, 4)
    shaft = _imported_shaft(tmp)
    linkable(new_document("asm", "assembly"))
    ids = {}
    for role, part, where in (
        ("plate", plate, (0, 0, 0)),
        ("block_a", block, (10, 10, 8)),
        ("block_b", block, (70, 10, 8)),
        ("shaft", shaft, (0, 17, 23)),
        ("pin", pin, (50, 30, 2)),
    ):
        ids[role] = call("insert_component", op(f"i_{role}", part=part["handle"], transform=_pose(*where)))[
            "component"
        ]["id"]
    return ids


def composed() -> tuple[model.Sheet, list[Any], Any]:
    rec = _rec()
    page = drawing.find_page(rec.fc_doc)
    return drawing.compose(rec, page, drawing.spec_of(page), 1, 1, "check")


def add(*items: dict[str, Any]) -> list[dict[str, Any]]:
    return call("annotate_drawing", op("a", action="add", items=list(items)))["items"]


def _texts(sheet: model.Sheet) -> list[str]:
    return sheet.texts()


def check_an_assembly_sheet_draws_every_component_without_hidden_lines_and_says_so_in_the_title_block() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        assembly(tmp)
        result = call("create_drawing", op("d", template="A3", title="Conjunto"))
        assert result["warnings"] == [] and result["scale"] > 0
        sheet, _results, ctx = composed()
        assert "Plano de conjunto" in _texts(sheet) and "Plano de fabricación" not in _texts(sheet)
        assert not sheet.of_layer(model.HIDDEN), "hidden lines are off by default"
        assert ctx.assembly is not None and [g.quantity for g in ctx.assembly.groups] == [1, 2, 1, 1]
        assert any("100" == t for t in _texts(sheet)), "the overall width of the assembly"


def check_the_parts_list_groups_identical_components_and_names_a_bought_part_by_its_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        assembly(tmp)
        call("create_drawing", op("d", template="A3"))
        row = add({"type": "parts_list"})[-1]
        assert row["status"] == "ok", row
        sheet, _, ctx = composed()
        assert ctx.assembly is not None
        by_name = {g.name: g for g in ctx.assembly.groups}
        assert by_name["block"].quantity == 2 and by_name["block"].instances == ["comp:2", "comp:3"]
        assert by_name["plate"].material == "S235JR" and by_name["plate"].remarks == ""
        shaft = ctx.assembly.groups[2]
        assert shaft.bought and shaft.remarks == "comprado" and shaft.reference == "eje_d10.step"
        texts = _texts(sheet)
        assert "Nº de plano / Referencia" in texts and "comprado" in texts and "eje_d10.step" in texts
        left, bottom, right, top = ctx.extras["parts_list"]["rect"]
        assert bottom == 58.0 and right == 410.0 and top > bottom
        # an override changes text, never the quantity
        call("annotate_drawing", op("r", action="remove", ids=[row["id"]]))
        add({"type": "parts_list", "rows": [{"component": "comp:3", "name": "Taco de apoyo"}]})
        sheet, _, ctx = composed()
        assert "Taco de apoyo" in _texts(sheet) and ctx.assembly.groups[1].quantity == 2


def check_balloons_stand_on_visible_faces_and_a_hidden_instance_is_reported_not_drawn() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ids = assembly(tmp)
        call("create_drawing", op("d", template="A3"))
        add({"type": "parts_list"})
        results = add({"type": "balloons", "view": "front"})
        balloons = next(r for r in results if r["type"] == "balloons")
        assert balloons["status"] == "warning" and "5 (" not in balloons["detail"]
        assert "position(s) 4" in balloons["detail"] and "shows no instance" in balloons["detail"], balloons
        sheet, _, ctx = composed()
        numbers = sorted(
            e.text for e in sheet.entities if isinstance(e, model.Text) and e.height == 5.0 and e.text.isdigit()
        )
        assert numbers == ["1", "2", "3"], numbers
        # each dot is a point of its instance: the ray found it on a face of that component
        geometry = ctx.geometry
        view = ctx.views["front"]
        frame = (view.centre, view.xdir, view.direction)
        margin = 2.5 / view.scale  # what the balloons ask for
        found = geometry.instance_anchors([ids["plate"], ids["block_a"], ids["pin"]], frame, {}, margin, outward=True)
        assert found[ids["plate"]] and found[ids["block_a"]] and found[ids["pin"]] == []
        dots = [e for e in sheet.entities if isinstance(e, model.Solid)]
        wanted = view.project(found[ids["block_a"]][0])
        assert any(abs(sum(p[0] for p in s.points) / len(s.points) - wanted[0]) < 0.05 for s in dots)


def check_the_exploded_view_moves_the_drawing_and_never_the_assembly() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ids = assembly(tmp)
        rec = _rec()
        before = {
            cid: tuple(rec.fc_doc.getObject(c.fc_part).Placement.toMatrix().A) for cid, c in rec.components.items()
        }
        call("create_drawing", op("d", template="A3"))
        row = add({"type": "exploded", "factor": 0.5, "moves": [{"component": ids["shaft"], "by": [0, -40, 30]}]})[-1]
        assert row["status"] in ("ok", "warning"), row
        sheet, _, ctx = composed()
        view = ctx.extras["views_extra"]["exploded"]
        offsets = ctx.extras["explosion"]["offsets"]
        assert ids["plate"] not in offsets and offsets[ids["shaft"]] == (0.0, -40.0, 30.0)
        assert "Vista explosionada" in " ".join(_texts(sheet))
        assert view.box[0] >= ctx.area[0] and view.box[2] <= ctx.area[2]
        rec = _rec()
        after = {
            cid: tuple(rec.fc_doc.getObject(c.fc_part).Placement.toMatrix().A) for cid, c in rec.components.items()
        }
        assert after == before, "the poses of the assembly are untouched"
        # balloons on the exploded view ask where the components are drawn
        add({"type": "balloons"})
        _, results, ctx = composed()
        assert next(r for r in results if r.type == "balloons").detail.startswith("3 balloon(s) in the exploded view")


def check_the_review_agrees_with_the_sheet() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ids = assembly(tmp)
        call("create_drawing", op("d", template="A3"))
        add({"type": "parts_list"}, {"type": "balloons", "view": "front"})
        findings = call("describe_drawing", op("r", review=True))["sheets"][0]["findings"]
        codes = {f["code"] for f in findings}
        assert "position_without_balloon" in codes and "instance_hidden" in codes, codes
        hidden = next(f for f in findings if f["code"] == "instance_hidden")
        assert ids["pin"] in hidden["message"]


def check_a_part_sheet_refuses_assembly_items() -> None:
    new_document("lonely")
    sk = sketch("s", "XY", [circle("c", (0, 0), 20)])
    extrude("e", sk, 10)
    call("create_drawing", op("d", template="A4"))
    results = call("annotate_drawing", op("a", action="add", items=[{"type": "parts_list"}]))["items"]
    assert any(r["status"] == "refused" and "assembly drawing" in r["detail"] for r in results), results


def check_the_names_in_the_parts_list_are_the_ones_the_engineer_gave_with_their_digits() -> None:
    """A FreeCAD Label comes back from ``saveAs`` as ``4_rodamiento_``: the list must not print it."""
    with tempfile.TemporaryDirectory() as tmp:
        assembly(tmp)
        call("create_drawing", op("d", template="A3"))
        add({"type": "parts_list"})
        sheet, _, ctx = composed()
        assert ctx.assembly is not None
        assert [g.name for g in ctx.assembly.groups] == ["plate", "block", "eje_d10", "pin_m6"], [
            g.name for g in ctx.assembly.groups
        ]
        texts = _texts(sheet)
        assert "eje_d10" in texts and "pin_m6" in texts and "eje_d10.step" in texts
        assert not any(t[:1].isdigit() and "_" in t for t in texts), "no counter-prefixed label on the sheet"


def check_a_sheet_made_without_the_isometric_view_has_none_and_a_part_sheet_keeps_it() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        assembly(tmp)
        result = call("create_drawing", op("d", template="A3", isometric=False))
        assert result["views"] == ["front", "top", "right"] and result["warnings"] == []
        page = drawing.find_page(_rec().fc_doc)
        assert [v.Label for v in drawing.part_views(page) if v.Label == "Isometric"] == []
        assert drawing.iso_info(page) is None and drawing.spec_of(page)["isometric"] is False
        call("annotate_drawing", op("u", action="update"))  # laid out again: still none
        assert drawing.iso_info(drawing.find_page(_rec().fc_doc)) is None
        add({"type": "parts_list"}, {"type": "exploded"}, {"type": "balloons"})
        _, results, ctx = composed()
        assert all(r.status != "error" for r in results), [(r.type, r.detail) for r in results]
    new_document("lonely")
    sk = sketch("s", "XY", [circle("c", (0, 0), 20)])
    extrude("e", sk, 10)
    assert call("create_drawing", op("d2", template="A4", views=["front", "top", "right", "iso"]))["views"] == [
        "front",
        "top",
        "right",
        "isometric",
    ]
    assert "isometric" not in drawing.spec_of(drawing.find_page(_rec().fc_doc)), "a part sheet's spec is what it was"
