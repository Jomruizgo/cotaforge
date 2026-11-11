"""Production drawings in the headless worker: sheets with a spec (hole callouts, dimensions, datum dimensions, notes,
revisions), several sheets, the Spanish title block, the PDF and the DXF.

The point of these checks is that every number on a sheet comes out of the model: the callouts are compared with the
holes the part really has, the circles drawn with where TechDraw projects the hole, the dimension values with the
geometry before and after the part is edited, and the sheet with itself after a save and a reopen. The sheets are
inspected as ``sheet_model`` data (what the PDF and the DXF are written from), not as PDF text.
"""

import os
import re
import tempfile
from typing import Any

import drawing
import main
import sheet_dxf
import sheet_model as model
import sheet_svg
from check_edit_in_place import box, cyl, edit, sub, with_changes
from check_edit_in_place import build as build_plan
from check_save_resume import reopen, save_and_reset, workdir
from harness import (
    IDENTITY,
    build_part03,
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

PLATE = (100.0, 60.0, 20.0)
# world positions of the holes of build_plate (an asymmetric set: a mirrored or swapped axis would show)
HOLES = {"a": (15.0, 12.0), "b": (85.0, 10.0), "c": (30.0, 50.0), "d": (60.0, 35.0)}


def _rec() -> Any:
    assert main.WORKER.active is not None
    return main.WORKER.active


def _tmp(name: str) -> str:
    return os.path.join(tempfile.mkdtemp(prefix="cadh_sheets_"), name)


def build_plate() -> dict[str, Any]:
    """100 x 60 x 20 plate with two plain D8 through holes, a tapped M6 blind hole (thread 10 of 12) and a plain D6
    blind one, all made by hole operations (so the thread is declared). Returns the base extrusion."""
    new_document("placa")
    sk = sketch("s0", "XY", [rect("outline", (50, 30), 100, 60)])
    base = extrude("base", sk, 20)

    def top() -> dict[str, Any]:
        return select_face(faces_of(base["feature"]), normal=(0, 0, 1))

    def drill(name: str, where: tuple[float, float], **fields: Any) -> None:
        params = {"standard": "simple", "size": None, "end_condition": "through_all", "depth_mm": None}
        params.update(fields)
        face = top()  # the centre of the face moves with every hole: the position is counted from where it is now
        centre = face["center"]
        call("hole", op(name, face=face["handle"], position=[where[0] - centre[0], where[1] - centre[1]], **params))

    drill("hole_a", HOLES["a"], diameter_mm=8)
    drill("hole_b", HOLES["b"], diameter_mm=8)
    drill(
        "hole_c",
        HOLES["c"],
        standard="iso_tapped",
        size="M6",
        diameter_mm=5.0,
        end_condition="blind",
        depth_mm=12.0,
        thread={"size": "M6", "depth_mm": 10.0},
    )
    drill("hole_d", HOLES["d"], diameter_mm=6, end_condition="blind", depth_mm=5.0)
    return base


def composed(sheet: str | None = None) -> tuple[model.Sheet, list[Any]]:
    """The sheet as the PDF and the DXF are written from it, and what each item did."""
    rec = _rec()
    page = drawing.find_page(rec.fc_doc, sheet)
    spec = drawing.spec_of(page)
    number = drawing.pages(rec.fc_doc).index(page) + 1
    return drawing.compose_sheet(rec, page, spec, number, len(drawing.pages(rec.fc_doc)), "check")


def texts(sheet: model.Sheet, layer: str | None = None) -> list[str]:
    return [e.text for e in sheet.entities if isinstance(e, model.Text) and (layer is None or e.layer == layer)]


def add(*items: dict[str, Any], sheet: str | None = None) -> dict[str, Any]:
    params = op("a", action="add", items=list(items))
    if sheet:
        params["sheet"] = sheet
    return call("annotate_drawing", params)


# --- tolerances, datums, frames, finish, threads ---------------------------------------------------------------------


def _hole_face(diameter: float) -> str:
    for hole in drawing.hole_infos(_rec(), None, "check"):
        if abs(hole.diameter - diameter) < 1e-6 and hole.faces:
            return hole.faces[0]
    raise AssertionError(f"no hole of diameter {diameter}")


def check_fits_datums_frames_finish_and_the_thread_arc_come_out_of_the_real_part() -> None:
    build_plate()
    call("create_drawing", op("d", template="A3", title="Placa", scale=1.0))
    bore = _hole_face(8.0)
    rows = add(
        {"type": "hole_dim", "at": [HOLES["a"][0], HOLES["a"][1], 10.0], "fit": "H8", "id": "bore"},
        {"type": "datum", "letter": "A", "on": "bore"},
        {"type": "gdt", "char": "position", "value": 0.1, "diameter": True, "datums": ["A"], "on": "bore"},
        {"type": "gdt", "char": "perpendicularity", "value": 0.05, "datums": ["B"], "face": bore},
        {"type": "finish", "face": bore, "ra": 1.6},
        {"type": "finish", "general": True, "ra": 3.2},
    )["items"]
    by_id = {r["id"]: r for r in rows}
    assert by_id["bore"]["status"] == "ok", by_id["bore"]
    assert any(r["type"] == "gdt" and r["status"] == "refused" and "datum B" in r["detail"] for r in rows), rows
    sheet, _ = composed()
    found = texts(sheet)
    assert "Ø8 H8 (+0,022/0) pasante" in found, found
    assert any(t.endswith("Tablas ISO 286 transcritas; verificar contra la norma") for t in found), (
        found
    )  # a numbered note
    assert "Ra 1,6" in found and "Ra 3,2" in found and "Ø0,1" in found, found
    assert texts(sheet, model.DIMENSIONS).count("A") == 2  # the datum feature and the one the frame cites
    arcs = [e for e in sheet.entities if isinstance(e, model.Arc) and e.layer == model.THREAD]
    assert len(arcs) == 1 and abs(arcs[0].r - 3.0) < 1e-6, arcs  # the M6 hole, seen in the top view only
    info = next(h for h in drawing.hole_infos(_rec(), None, "check") if h.thread is not None)
    top = drawing.view_infos(drawing.find_page(_rec().fc_doc))["top"]
    cx, cy = top.project(info.centre)
    assert abs(arcs[0].cx - cx) < 1e-6 and abs(arcs[0].cy - cy) < 1e-6
    data = sheet_dxf.to_dxf(sheet).decode("cp1252")
    assert "ROSCAS" in data and "%%c8 H8" in data.replace("Ø", "%%c")


def build_bushing() -> dict[str, str]:
    """A flanged bushing along z: flange D40 x 6, boss D24 x 14 on top, a D12 through bore and an M6 tapped hole in the
    flange. Returns the ids of the faces the tolerance items attach to."""
    new_document("casquillo")
    flange = extrude("flange", sketch("s0", "XY", [circle("c0", (0, 0), 40)]), 6)
    top = select_face(faces_of(flange["feature"]), normal=(0, 0, 1))
    boss = extrude("boss", sketch("s1", top["handle"], [circle("c1", (0, 0), 24)]), 14)
    end = next(f for f in faces_of(boss["feature"]) if f["surface"] == "plane" and abs(f["center"][2] - 20) < 1e-6)
    journal = next(f for f in faces_of(boss["feature"]) if f["surface"] == "cylinder")
    bottom = select_face(faces_of(flange["feature"]), normal=(0, 0, -1))
    simple = {"standard": "simple", "size": None, "end_condition": "through_all", "depth_mm": None}
    call("hole", op("bore", face=end["handle"], position=[0, 0], diameter_mm=12, **simple))
    shoulder = next(
        f for f in faces_of(flange["feature"]) if f["surface"] == "plane" and abs(f["center"][2] - 6) < 1e-6
    )
    tapped = {
        "standard": "iso_tapped",
        "size": "M6",
        "end_condition": "blind",
        "depth_mm": 5.0,
        "thread": {"size": "M6", "depth_mm": 4.0},
        "diameter_mm": 5.0,
    }
    call("hole", op("tap", face=shoulder["handle"], position=[16, 0], **tapped))
    return {
        "journal": journal["handle"]["id"],
        "bottom": bottom["handle"]["id"],
        "end": end["handle"]["id"],
        "shoulder": shoulder["handle"]["id"],
    }


def check_a_flanged_bushing_gets_fits_datum_frames_finish_and_the_thread_arc() -> None:
    """The acceptance sheet of the tolerance items on a real part; with ``COTAFORGE_P2_OUT`` set it also writes the
    PDF, the PNG and the DXF there to be looked at."""
    faces = build_bushing()
    call(
        "create_drawing",
        op(
            "d",
            template="A3",
            title="Casquillo",
            scale=2.0,
            material="Acero 1045",
            views=["front", "top", "right", "iso"],
        ),
    )
    bore = _hole_face(12.0)
    rows = add(
        {"type": "view_lines"},
        {"type": "hole_dim", "face": bore, "fit": "H7", "id": "bore"},
        {"type": "hole_dim", "face": _hole_face(5.0), "id": "tap"},
        {"type": "dim", "kind": "diameter", "face": faces["journal"], "fit": "g6", "id": "jrn"},
        {
            "type": "dim",
            "kind": "distance",
            "from": {"face": faces["bottom"]},
            "to": {"face": faces["shoulder"]},
            "tol": [0.1, -0.05],
        },
        {"type": "dim", "kind": "distance", "from": {"face": bore}, "to": {"face": _hole_face(5.0)}, "along": "x"},
        {"type": "datum", "letter": "A", "on": "bore"},
        {"type": "gdt", "char": "total_runout", "value": 0.02, "datums": ["A"], "on": "jrn"},
        {
            "type": "gdt",
            "char": "position",
            "value": 0.1,
            "diameter": True,
            "modifier": "M",
            "datums": ["A"],
            "on": "tap",
        },
        {"type": "gdt", "char": "flatness", "value": 0.02, "face": faces["bottom"]},
        {"type": "finish", "face": bore, "ra": 1.6},
        {"type": "finish", "general": True, "ra": 3.2},
    )["items"]
    assert all(r["status"] in ("ok", "warning") for r in rows), rows
    sheet, results = composed()
    assert not [r for r in results if r.status == "error"], [(r.id, r.detail) for r in results]
    found = texts(sheet, model.DIMENSIONS) + texts(sheet, model.TEXT)
    for wanted in ("Ø12 H7 (+0,018/0) pasante", "Ø24 g6 (-0,007/-0,02)", "Ra 1,6", "Ra 3,2", "Ø0,1"):
        assert wanted in found, (wanted, found)
    assert len([e for e in sheet.entities if isinstance(e, model.Arc) and e.layer == model.THREAD]) == 1
    findings = call("describe_drawing", op("r", review=True))["sheets"][0]["findings"]
    wrong = {"duplicate_dimension", "over_dimensioned", "datum_undefined", "item_error", "stale_reference"}
    wrong.add("gdt_not_located")
    assert not [f for f in findings if f["code"] in wrong], findings
    out = os.environ.get("COTAFORGE_P2_OUT")
    if out:
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(out, "review.txt"), "w", encoding="utf-8") as handle:
            lines = [f"{f['severity']} {f['code']}: {f['message']}" for f in findings]
            handle.write(chr(10).join(lines) or "no findings")
        call("export_drawing", op("e", path=os.path.join(out, "casquillo.pdf"), format="pdf"))
        call("export_drawing", op("e", path=os.path.join(out, "casquillo.dxf"), format="dxf"))
        call("capture_drawing", op("c", path=os.path.join(out, "casquillo.png"), width=2400))


# --- hole callouts ----------------------------------------------------------------------------------------------------


def check_equal_holes_share_a_callout_and_the_thread_comes_from_the_hole_operation() -> None:
    build_plate()
    call("create_drawing", op("d", template="A3", title="Placa"))
    result = add({"type": "hole_dim", "all": True})
    row = result["items"][-1]
    assert row["type"] == "hole_dim" and row["status"] == "ok", row  # every hole was made by a hole operation
    sheet, _ = composed()
    found = texts(sheet, model.TEXT)
    assert "2× Ø8 pasante" in found, found
    assert "M6 ↧ 10" in found and "taladro Ø5 ↧ 12" in found, found
    assert "Ø6 ↧ 5" in found, found
    assert sum(1 for t in found if "Ø8" in t) == 1  # one callout for the two equal holes


def _review() -> list[dict[str, Any]]:
    return call("describe_drawing", op("r", review=True))["sheets"][0]["findings"]


def check_a_hole_the_operation_did_not_make_is_described_by_its_geometry_only() -> None:
    """A hole no hole operation made (a native cut) is described by its geometry. Only a hole of a TAP-DRILL diameter
    may be a threaded one: a clearance or dowel diameter says nothing about threads (real use: every cut hole made the
    review and the callout warn "rosca no declarada", on a part with no thread at all)."""
    plan = [box("plate", PLATE), cyl("h1", 8, PLATE[2], (15, 12, 0)), sub("drilled", "plate", "h1")]
    build_plan(plan, "geometry")
    call("create_drawing", op("d", template="A3"))
    row = add({"type": "hole_dim", "all": True})["items"][-1]
    assert row["status"] == "ok" and "rosca" not in row["detail"], row
    assert "Ø8 pasante" in texts(composed()[0], model.TEXT)
    assert "thread_undeclared" not in {f["code"] for f in _review()}
    plan = [box("plate", PLATE), cyl("h1", 5, PLATE[2], (15, 12, 0)), cyl("h2", 6.8, PLATE[2], (50, 30, 0))]
    build_plan([*plan, sub("drilled", "plate", "h1", "h2")], "geometry")
    call("create_drawing", op("d", template="A3"))
    row = add({"type": "hole_dim", "all": True})["items"][-1]
    assert row["status"] == "ok" and "M6" in row["detail"] and "M8" in row["detail"], row  # Ø5 = M6, Ø6.8 = M8
    infos = [f for f in _review() if f["code"] == "thread_undeclared"]
    assert infos and all(f["severity"] == "info" for f in infos), infos


def check_every_hole_circle_is_drawn_where_the_hole_projects_at_any_scale() -> None:
    build_plate()
    for scale in (1.0, 0.5, 2.0):
        call("create_drawing", op("d", template="A3", scale=scale))
        sheet, _ = composed()
        page = drawing.find_page(_rec().fc_doc)
        top = drawing.view_infos(page)["top"]
        circles = [e for e in sheet.entities if isinstance(e, model.Circle) and e.layer == model.CONTOUR]
        for hole in drawing.hole_infos(_rec(), None, "check"):
            px, py = top.project(hole.centre)
            found = [c for c in circles if abs(c.cx - px) < 0.01 and abs(c.cy - py) < 0.01]
            assert found, (scale, hole.centre, (px, py))
            assert abs(found[0].r - hole.diameter / 2 * scale) < 0.01, (scale, found[0].r, hole.diameter)
        # the four world positions the plate was built with, through the mapping of the view (y is down in TechDraw)
        expected = {
            (round(top.x + (x - 50.0) * scale, 2), round(top.y + (y - 30.0) * scale, 2)) for x, y in HOLES.values()
        }
        drawn = {(round(c.cx, 2), round(c.cy, 2)) for c in circles if c.r < 5 * scale}
        assert expected <= drawn, (scale, expected - drawn)


# --- dimensions are measured, not typed ------------------------------------------------------------------------------


def check_a_distance_dimension_is_the_distance_between_the_model_references() -> None:
    build_plate()
    call("create_drawing", op("d", template="A3"))
    result = add(
        {"type": "dim", "kind": "distance", "from": {"at": [15, 12, 20]}, "to": {"at": [85, 10, 20]}, "along": "x"},
        {"type": "dim", "kind": "distance", "from": {"at": [30, 50, 20]}, "to": {"at": [30, 35, 20]}, "along": "y"},
    )
    rows = {r["id"]: r for r in result["items"]}
    assert all(r["status"] == "ok" for r in rows.values() if r["id"] != "loc"), rows  # loc asks for a reference
    sheet, _ = composed()
    dimension_texts = texts(sheet, model.DIMENSIONS)
    assert "70" in dimension_texts and "15" in dimension_texts, dimension_texts
    # not on the part: refused, and nothing was stored for it
    refused = add({"type": "dim", "kind": "distance", "from": {"at": [50, 30, 80]}, "to": {"at": [85, 10, 20]}})
    assert refused["items"][-1]["status"] == "refused" and "not on the part" in refused["items"][-1]["detail"]
    listed = call("describe_drawing", op("l"))["sheets"][0]
    assert [i["type"] for i in listed["items"] if not i["spec"].get("auto")] == ["overall", "dim", "dim"]


def check_datum_dimensions_give_every_hole_from_the_datum_edges() -> None:
    build_plate()
    call("create_drawing", op("d", template="A3"))
    row = add({"type": "datum_dims"})["items"][-1]
    assert row["status"] == "ok" and "x from 15, 30, 60, 85; y from 10, 12, 35, 50" in row["detail"], row
    shown = texts(composed()[0], model.DIMENSIONS)
    for value in ("15", "30", "60", "85", "10", "12", "35", "50"):
        assert value in shown, (value, shown)
    call("annotate_drawing", op("r", action="remove", ids=[row["id"]]))
    row = add({"type": "datum_dims", "mode": "table"})["items"][-1]
    sheet, _ = composed()
    assert "TABLA DE AGUJEROS" in texts(sheet), texts(sheet)
    assert {"1", "2", "3", "4"} <= set(texts(sheet, model.TEXT))


def check_the_dimensions_follow_the_part_when_it_is_edited_and_update_lays_the_views_out_again() -> None:
    plan = [
        box("plate", PLATE),
        cyl("h1", 8, PLATE[2], (15, 12, 0)),
        cyl("h2", 8, PLATE[2], (85, 12, 0)),
        sub("drilled", "plate", "h1", "h2"),
    ]
    ids = build_plan(plan, "editable")
    call("create_drawing", op("d", template="A3"))
    left = select_face(faces_of(ids["plate"]["feature"]), normal=(-1, 0, 0))["handle"]["id"]

    def far_hole_face() -> str:
        holes = call("list_holes", op("h"))["holes"]
        return max(holes, key=lambda h: h["axis"]["origin"][0])["faces"][0]["id"]

    add({"type": "dim", "kind": "distance", "from": {"face": left}, "to": {"face": far_hole_face()}})
    before = texts(composed()[0], model.DIMENSIONS)
    assert "85" in before and "100" in before, before
    edit(ids, with_changes(plan, plate={"size": [140, 60, 20]}, h2={"origin": [125, 12, 0]}), "plate", "h2")
    result = call("annotate_drawing", op("u", action="update"))
    assert result["dimensions"][0] == "width 140 mm", result["dimensions"]
    rows = {r["id"]: r for r in result["items"]}
    assert rows["o1"]["status"] == "ok" and "width 140" in rows["o1"]["detail"], rows
    # the edit minted a new face for the hole it moved: the old id is reported, and no old number is drawn for it
    assert rows["d2"]["status"] == "error" and "is not a face of the part" in rows["d2"]["detail"], rows
    stale = texts(composed()[0], model.DIMENSIONS)
    assert "85" not in stale and "100" not in stale and "140" in stale, stale
    call("annotate_drawing", op("r", action="remove", ids=["d2"]))
    add({"type": "dim", "kind": "distance", "from": {"face": left}, "to": {"face": far_hole_face()}})
    after = texts(composed()[0], model.DIMENSIONS)
    assert "125" in after and "140" in after, after


def check_a_radius_an_angle_and_a_chamfer_are_measured_on_their_faces() -> None:
    part = build_part03()
    call("create_drawing", op("d", template="A3"))
    fillet = select_face(faces_of(part["fillet"]["feature"], "created"), surface="cylinder")
    chamfer_face = faces_of(part["chamfer"]["feature"], "created")
    assert len(chamfer_face) == 1, chamfer_face
    result = add(
        {"type": "dim", "kind": "radius", "face": fillet["handle"]["id"]},
        {"type": "dim", "kind": "chamfer", "face": chamfer_face[0]["handle"]["id"]},
    )
    rows = {r["id"]: r for r in result["items"]}
    assert rows["d2"]["status"] == "ok" and "R5" in rows["d2"]["detail"], rows
    assert rows["d3"]["status"] == "ok" and "legs 3 and 2 mm" in rows["d3"]["detail"], rows


# --- sheets, files ----------------------------------------------------------------------------------------------------


def check_the_title_block_is_in_spanish_with_the_fields_asked_for() -> None:
    build_plate()
    call(
        "create_drawing",
        op(
            "d",
            template="A3",
            title="Placa de anclaje",
            author="Ana",
            material="Acero 1045",
            drawing_number="PL-001",
            revision="B",
            general_tolerances="ISO 2768-fH",
            company="Taller Sur",
            scale=1.0,
        ),
    )
    sheet, _ = composed()
    svg = sheet_svg.to_svg(sheet)
    for text in (
        "Placa de anclaje",
        "Ana",
        "Acero 1045",
        "PL-001",
        "ISO 2768-fH",
        "Taller Sur",
        "Plano de fabricación",
        "Título:",
        "Número de plano:",
        "Tolerancias generales:",
        "Revisión:",
        "1 : 1",
        "1 / 1",
    ):
        assert text in svg, text
    assert "Created by:" not in svg and "Drawing number:" not in svg  # nothing of the English template
    fields = dict(drawing.find_page(_rec().fc_doc).Template.EditableTexts)  # the FreeCAD sheet window shows it too
    assert fields["title"] == "Placa de anclaje" and fields["revision_index"] == "B"
    english = call("create_drawing", op("d", template="A3", language="en", sheet="Ingles"))
    assert english["language"] == "en"
    assert "Drawing number:" in sheet_svg.to_svg(composed("Ingles")[0])


def check_revisions_fill_the_revision_table_and_the_revision_field() -> None:
    build_plate()
    call("create_drawing", op("d", template="A3", revision="A"))
    add(
        {"type": "revision", "index": "A", "description": "Emisión inicial", "date": "2026-10-25", "by": "JR"},
        {"type": "revision", "index": "B", "description": "Agujeros a Ø8", "date": "2026-10-26", "by": "JR"},
    )
    sheet, _ = composed()
    found = texts(sheet, model.TITLE_BLOCK)
    assert "Emisión inicial" in found and "Agujeros a Ø8" in found and "B" in found, found
    fields = dict(drawing.find_page(_rec().fc_doc).Template.EditableTexts)
    assert fields["revision_index"] == "B" and fields["rev_2_description"] == "Agujeros a Ø8"


def check_several_sheets_are_independent_drawings_each_exported_to_its_own_file() -> None:
    build_plate()
    first = call("create_drawing", op("d", template="A3", title="Conjunto"))
    second = call("create_drawing", op("d", template="A4", sheet="Detalle", scale=2.0, title="Detalle"))
    assert (first["sheet"], second["sheet"]) == ("Drawing", "Detalle") and second["sheets"] == ["Drawing", "Detalle"]
    add({"type": "hole_dim", "all": True}, sheet="Detalle")
    listed = call("describe_drawing", op("l"))["sheets"]
    assert [s["sheet"] for s in listed] == ["Drawing", "Detalle"]
    mine = [[i["type"] for i in s["items"] if not i["spec"].get("auto")] for s in listed]
    assert mine == [["overall"], ["overall", "hole_dim"]], mine
    assert "1 / 2" in sheet_svg.to_svg(composed("Drawing")[0]) and "2 / 2" in sheet_svg.to_svg(composed("Detalle")[0])
    expect_error(
        "invalid_request", "export_drawing", op("e", path=_tmp("both.pdf"), format="pdf")
    )  # one sheet, one file
    sizes = {}
    for name, size in (("Drawing", 420), ("Detalle", 297)):  # A3 and A4 landscape, each in its own file
        pdf = _tmp(f"{name}.pdf")
        assert call("export_drawing", op("e", path=pdf, format="pdf", sheet=name))["paths"] == [pdf]
        with open(pdf, "rb") as handle:
            data = handle.read()
        boxes = [tuple(float(v) for v in m) for m in re.findall(rb"/MediaBox \[\s*0 0 ([\d.]+) ([\d.]+)\s*\]", data)]
        assert len(boxes) == 1, boxes  # one page
        sizes[name] = round(boxes[0][0] / 72 * 25.4)
        assert sizes[name] == size, sizes
    dxf = _tmp("sheets.dxf")
    assert call("export_drawing", op("e", path=dxf, format="dxf", sheet="Drawing"))["paths"] == [dxf]
    assert os.path.getsize(dxf) > 5000 and not os.path.exists(dxf.replace(".dxf", "-2.dxf"))
    removed = call("annotate_drawing", op("r", action="remove_sheet", sheet="Detalle"))
    assert removed["removed"] is True and removed["sheets"] == ["Drawing"]
    assert [p.Name for p in drawing.pages(_rec().fc_doc)] == ["Drawing"]


def check_each_sheet_can_draw_one_body() -> None:
    new_document("dos")
    first = call("box", op("b1", size=[100, 60, 8], transform=IDENTITY))
    second = call("box", op("b2", size=[30, 20, 10], transform={**IDENTITY, "origin": [200, 0, 0]}))
    one = call("create_drawing", op("d", template="A4", sheet="Grande", body=first["created_bodies"][0]))
    two = call("create_drawing", op("d", template="A4", sheet="Chica", body=second["created_bodies"][0]))
    assert one["dimensions"] == ["width 100 mm", "height 8 mm", "depth 60 mm"], one["dimensions"]
    assert two["dimensions"] == ["width 30 mm", "height 10 mm", "depth 20 mm"], two["dimensions"]


def check_the_dxf_has_the_title_block_in_cp1252_with_the_layers_declared() -> None:
    build_plate()
    call("create_drawing", op("d", template="A3", title="Placa de anclaje — ñandú"))
    add({"type": "hole_dim", "all": True}, {"type": "note", "text": "Aristas sin rebabas — 0,3 mm"})
    path = _tmp("placa.dxf")
    call("export_drawing", op("e", path=path, format="dxf"))
    with open(path, "rb") as handle:
        data = handle.read()
    text = data.decode("cp1252")  # every byte is a cp1252 character
    assert b"\xc3" not in data, "no UTF-8 sequence: the header says ANSI_1252"
    for expected in (
        "AC1009",
        "ANSI_1252",
        "%%c8 pasante",
        "Placa de anclaje",
        "T\xedtulo:",
        "ISO 2768-mK",
        "\xf1and\xfa",
    ):
        assert expected in text or expected.encode("cp1252") in data, expected
    for layer in ("CONTORNO", "OCULTAS", "COTAS", "TEXTO", "ROTULO", "EJES", "SOMBREADO"):
        assert layer in text, layer
    assert "HIDDEN" in text and "CENTER" in text
    assert data.count(b"\r\nCIRCLE\r\n") >= 5  # the holes of the views (and the symbol of the title block) are circles


def check_the_png_shows_the_annotations() -> None:
    build_plate()
    call("create_drawing", op("d", template="A3"))
    call("annotate_drawing", op("r", action="remove", ids=["loc"]))  # the default location would make the two alike
    path = _tmp("plain.png")
    call("capture_drawing", op("c", path=path, width=1200))
    add({"type": "hole_dim", "all": True}, {"type": "datum_dims"})
    annotated = _tmp("annotated.png")
    call("capture_drawing", op("c", path=annotated, width=1200))
    from PySide import QtGui  # noqa: PLC0415

    def ink(name: str) -> int:
        image = QtGui.QImage(name)
        return sum(
            1
            for x in range(0, image.width(), 2)
            for y in range(0, image.height(), 2)
            if QtGui.qGray(image.pixel(x, y)) < 110
        )

    assert ink(annotated) > ink(path) + 150, (ink(annotated), ink(path))


# --- persistence, legacy, errors --------------------------------------------------------------------------------------


def check_the_sheet_and_its_items_survive_a_save_and_a_reopen() -> None:
    build_plate()
    call("create_drawing", op("d", template="A3", title="Placa", language="es"))
    hole_faces = [h["faces"][0]["id"] for h in call("list_holes", op("h"))["holes"]]
    add(
        {"type": "hole_dim", "face": hole_faces[0]},
        {"type": "hole_dim", "at": [60, 35, 10]},
        {"type": "datum_dims"},
        {"type": "dim", "kind": "distance", "from": {"at": [15, 12, 10]}, "to": {"at": [85, 10, 10]}, "along": "x"},
        {"type": "note", "text": "Sin rebabas"},
        {"type": "revision", "index": "A", "description": "Emisión inicial"},
    )
    sheet_before, results_before = composed()
    spec_before = drawing.spec_of(drawing.find_page(_rec().fc_doc))
    with workdir() as directory:
        path, state, _ = save_and_reset(directory, "placa")
        reopen(path, state)
        page = drawing.find_page(_rec().fc_doc)
        assert drawing.read_spec(page) == spec_before  # the spec is a property of the page, saved with the document
        assert drawing.template_path(spec_before["template"], "t").endswith("A3_es.svg")  # looked up again, by key
        described = call("describe_drawing", op("l"))["sheets"][0]
        assert [(i["id"], i["status"]) for i in described["items"]] == [(r.id, r.status) for r in results_before]
        sheet_after, _ = composed()
        assert texts(sheet_after) == texts(sheet_before)
        assert len(sheet_after.entities) == len(sheet_before.entities)
        # and the sheet can be written straight after the reopen
        out = _tmp("reopened.pdf")
        call("export_drawing", op("e", path=out, format="pdf"))
        assert os.path.getsize(out) > 10000


def check_the_template_is_found_by_key_even_if_the_path_in_the_document_is_stale() -> None:
    build_plate()
    call("create_drawing", op("d", template="A4"))
    page = drawing.find_page(_rec().fc_doc)
    moved = _tmp("A4_es.svg")  # what a document moved to another machine carries: a path that is not there any more
    with open(moved, "w", encoding="utf-8") as handle:
        handle.write(open(drawing.template_path("A4_es", "t"), encoding="utf-8").read())
    page.Template.Template = moved
    os.remove(moved)
    sheet, _ = composed()
    assert "Título:" in texts(sheet, model.TITLE_BLOCK)
    call("export_drawing", op("e", path=_tmp("stale.pdf"), format="pdf"))


def check_a_sheet_made_before_specs_existed_still_works_and_gets_a_spec_when_changed() -> None:
    build_plate()
    call("create_drawing", op("d", template="A4", language="en"))
    page = drawing.find_page(_rec().fc_doc)
    page.removeProperty(drawing.SPEC_PROP)  # a v0.3 sheet
    assert drawing.read_spec(page) is None
    described = call("describe_drawing", op("l"))["sheets"][0]
    assert described["language"] == "en" and [i["type"] for i in described["items"]] == ["overall"]
    add({"type": "hole_dim", "all": True})
    assert drawing.read_spec(drawing.find_page(_rec().fc_doc)) is not None


def check_bad_items_are_refused_with_a_reason() -> None:
    build_plate()
    call("create_drawing", op("d", template="A3"))
    error = expect_error("invalid_request", "annotate_drawing", op("a", action="add", items=[{"type": "weld"}]))
    assert "draws no weld symbols" in error.message and "item 1 of 1" in error.message
    expect_error(
        "invalid_request", "annotate_drawing", op("a", action="add", items=[{"type": "hole_dim", "all": True, "x": 1}])
    )
    expect_error("invalid_request", "annotate_drawing", op("a", action="remove", ids=["nope"]))
    expect_error(
        "invalid_request",
        "annotate_drawing",
        op("a", action="add", items=[{"type": "note", "text": "x"}], sheet="Otra"),
    )
    row = add({"type": "hole_dim", "at": [1, 1, 1]})["items"][-1]
    assert row["status"] == "refused" and "no hole has its axis through" in row["detail"], row
    kept = [i for i in call("describe_drawing", op("l"))["sheets"][0]["items"] if not i["spec"].get("auto")]
    assert len(kept) == 1  # only the overall dimensions (the default location of the holes is not the engineer's)


def check_an_item_is_removed_by_id_and_the_rest_stay() -> None:
    build_plate()
    call("create_drawing", op("d", template="A3"))
    ids = [r["id"] for r in add({"type": "hole_dim", "all": True}, {"type": "note", "text": "uno"})["items"]]
    removed = call("annotate_drawing", op("r", action="remove", ids=[ids[-1]]))
    assert [r["id"] for r in removed["items"]] == ids[:-1]
    assert not [t for t in texts(composed()[0]) if t.endswith("uno")]


def check_the_dxf_text_codes_hold_for_the_special_signs_the_sheet_prints() -> None:
    base = build_plate()
    call("create_drawing", op("d", template="A3"))
    top = select_face(faces_of(base["feature"]), normal=(0, 0, 1))
    side = select_face(faces_of(base["feature"]), normal=(1, 0, 0))
    result = add(
        {"type": "hole_dim", "all": True},
        {"type": "dim", "kind": "angle", "faces": [top["handle"]["id"], side["handle"]["id"]]},
    )
    assert result["items"][-1]["status"] in ("ok", "warning"), result["items"]  # (an angle text may lie on a view)
    data = sheet_dxf.to_dxf(composed()[0])
    assert b"%%c" in data and b"%%d" in data  # diameter and degree signs as R12 codes
    assert "90" in texts(composed()[0], model.DIMENSIONS)[-1]
