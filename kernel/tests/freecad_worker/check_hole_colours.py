# ruff: noqa: E501
"""The colour of a hole, on the real FreeCAD worker (written for the owner to run; see ``run.py``).

What it proves, headless (``freecadcmd``, no GUI, no ``ViewObject``):

* a coloured hole owns the faces its operation made, and they are the right ones (cylinders of the hole's radius), also
  after a LATER feature changed the face numbering, after a pattern copied the hole, and after save + reset + reopen of
  the file;
* ``hole_colours.sync`` does nothing and does not fail without a GUI;
* the isometric view of a sheet made with ``iso_colour`` is a shaded picture under the lines: filled polygons in the three
  colours of the convention, inside the box of the isometric view, none in the other views, absent when the flag is off, still
  there after save and reopen; the PDF and PNG are written and the PNG has pixels of each colour.

The last check only runs in a visible FreeCAD (``gui.ACTIVE``; headless it returns at once): the per-face ``DiffuseColor``
that ``hole_colours.sync`` writes, that it is made again after a later feature, and that a colour the engineer set by hand is
left alone. What survives of it when the GUI SAVES the file is not checked: FreeCAD writes it in GuiDocument.xml, and the
colours are re-derived from the registry when the file is opened (``sync`` runs after ``open_document``).

Prints the paths of the PNG / PDF it wrote: LOOK at them (the picture must be the plate seen from above the front-right
corner, the dowel hole in soft red, the plain one in green, the M6 in blue, the lines of the view over it).
"""

import os
import re
import tempfile
from typing import Any

import drawing
import hole_colours
import main
import sheet_svg
from harness import call, circle, extrude, faces_of, new_document, op, rect, select_face, sketch

RED = [255, 128, 128]
GREEN = [0, 192, 0]
BLUE = [128, 128, 255]
PLATE = (100.0, 60.0, 10.0)
HOLES = (  # name, where, diameter, extra fields (as the adapter sends them), colour, radius of the bore
    ("dowel", (20.0, 15.0), 6.0, {"fit": "H7", "hole_class": "precision", "precision_grade": 8}, RED),
    ("clear", (50.0, 30.0), 9.0, {"hole_class": "plain", "precision_grade": 8}, GREEN),
    (
        "tap",
        (80.0, 15.0),
        5.0,
        {"hole_class": "threaded", "precision_grade": 8, "thread": {"size": "M6", "depth_mm": 10.0}},
        BLUE,
    ),
)


def _tmp(name: str) -> str:
    return os.path.join(tempfile.mkdtemp(prefix="cadh_holecol_"), name)


def _rec() -> Any:
    return main.WORKER.active


def _build() -> dict[str, Any]:
    new_document("placa")
    base = extrude("base", sketch("s0", "XY", [rect("outline", (50, 30), PLATE[0], PLATE[1])]), PLATE[2])
    for name, where, diameter, extra, colour in HOLES:
        face = select_face(faces_of(base["feature"]), normal=(0, 0, 1))
        centre = face["center"]
        fields = {"standard": "simple", "size": None, "end_condition": "through_all", "depth_mm": None}
        if "thread" in extra:
            fields.update(standard="iso_tapped", size="M6", end_condition="blind", depth_mm=12.0)
        call(
            "hole",
            op(
                name,
                face=face["handle"],
                position=[where[0] - centre[0], where[1] - centre[1]],
                diameter_mm=diameter,
                colour=colour,
                **fields,
                **extra,
            ),
        )
    return base


def _painted() -> dict[tuple[str, float], int]:
    """(colour, bore radius) -> number of faces painted so, read from the live document: the registry says which face
    index is painted, the shape says what that face is."""
    rec = _rec()
    out: dict[tuple[str, float], int] = {}
    for body in rec.live_bodies():
        faces = hole_colours.face_colours(rec, body)
        shape = rec.fc_doc.getObject(body.result_obj).Shape
        for index, colour in faces.items():
            surface = shape.Faces[index].Surface
            kind = type(surface).__name__
            radius = round(float(getattr(surface, "Radius", 0.0)), 3)
            key = (",".join(str(c) for c in colour), radius if kind == "Cylinder" else -1.0)
            out[key] = out.get(key, 0) + 1
    return out


def _bores(painted: dict[tuple[str, float], int]) -> set[tuple[str, float]]:
    return {k for k in painted if k[1] > 0}


EXPECTED_BORES = {("255,128,128", 3.0), ("0,192,0", 4.5)}  # the tapped hole is drilled at the tap drill of M6 (5 mm)


def check_a_hole_owns_the_faces_it_made_and_they_are_the_right_ones() -> None:
    _build()
    painted = _painted()
    bores = _bores(painted)
    assert EXPECTED_BORES <= bores, painted
    assert any(colour == "128,128,255" for colour, _ in bores), painted
    assert all(colour in {"255,128,128", "0,192,0", "128,128,255"} for colour, _ in painted), painted


def check_the_colour_follows_the_faces_when_a_later_feature_changes_the_solid() -> None:
    base = _build()
    before = _painted()
    # a boss on top in the far corner: new faces, the numbering of the faces after it shifts
    top = select_face(faces_of(base["feature"]), normal=(0, 0, 1))
    boss = sketch("boss_sk", top["handle"], [circle("boss", (35.0, 20.0), 8.0)])
    extrude("boss", boss, 5.0)
    after = _painted()
    assert after == before, (before, after)
    # and a fillet-free chamfer of a plate edge would also renumber: the registry is what is read, not an index
    rec = _rec()
    indexes = {i for body in rec.live_bodies() for i in hole_colours.face_colours(rec, body)}
    assert indexes, "no painted face left"


def check_a_pattern_of_a_coloured_hole_takes_its_colour() -> None:
    base = _build()
    seed = call(
        "hole",
        op(
            "seed",
            face=select_face(faces_of(base["feature"]), normal=(0, 0, 1))["handle"],
            position=[-40.0, 20.0],
            standard="simple",
            size=None,
            diameter_mm=4.0,
            end_condition="through_all",
            depth_mm=None,
            colour=RED,
            hole_class="precision",
            precision_grade=8,
            fit="H7",
        ),
    )
    call("linear_pattern", op("copies", features=[seed["feature"]], direction=[1, 0, 0], spacing_mm=8.0, count=3))
    assert _painted().get(("255,128,128", 2.0)) == 3, _painted()  # the seed and its two copies


def check_a_hole_made_with_the_convention_off_is_not_painted() -> None:
    new_document("sin")
    base = extrude("base", sketch("s0", "XY", [rect("outline", (50, 30), 100, 60)]), 10)
    top = select_face(faces_of(base["feature"]), normal=(0, 0, 1))
    call(
        "hole",
        op(
            "h",
            face=top["handle"],
            position=[0, 0],
            standard="simple",
            size=None,
            diameter_mm=6,
            end_condition="through_all",
            depth_mm=None,
        ),
    )
    assert _painted() == {}


def check_the_colours_survive_save_and_reopen_on_a_headless_document() -> None:
    import persist

    _build()
    before = _painted()
    path = _tmp("placa.FCStd")
    saved = call(
        "save_document",
        op(
            "save",
            path=path,
            stamp={
                "design_id": "d",
                "plan_id": "p",
                "plan_revision": 1,
                "save_seq": 1,
                "last_step_index": 3,
                "harness_version": "0.0.0",
                "plan_sha256": "a" * 64,
                "sidecar_sha256": None,
            },
        ),
    )
    assert persist.dump(_rec())
    main.reset()
    assert main.WORKER.active is None
    call("open_document", op("open", path=path, adapter_state=saved["adapter_state"]))
    assert _painted() == before, (before, _painted())
    hole_colours.sync(_rec())  # headless: nothing to paint, nothing to fail


def _dowel(top: dict[str, Any], diameter: float, name: str = "dowel", **extra: Any) -> dict[str, Any]:
    """The request of a coloured, fitted hole as the adapter sends it, for the make and for every edit of it."""
    fields: dict[str, Any] = {"fit": "H7", "hole_class": "precision", "precision_grade": 8, "colour": RED}
    fields.update(extra)
    return op(
        name,
        face=top["handle"],
        position=[-30.0, -20.0],
        standard="simple",
        size=None,
        diameter_mm=diameter,
        end_condition="through_all",
        depth_mm=None,
        **fields,
    )


def _plate_with_dowel(diameter: float = 6.0) -> dict[str, Any]:
    new_document("placa")
    base = extrude("base", sketch("s0", "XY", [rect("outline", (50, 30), 100.0, 60.0)]), 10.0)
    top = select_face(faces_of(base["feature"]), normal=(0, 0, 1))
    return {"base": base, "top": top, "hole": call("hole", _dowel(top, diameter))}


def _edit_hole(ids: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    return call(
        "edit_feature", op("edit", edits=[{"feature": ids["hole"]["feature"], "kind": "hole", "request": request}])
    )


def _bore_colours() -> dict[float, set[str]]:
    """Radius of every painted bore -> the colours it is painted in."""
    out: dict[float, set[str]] = {}
    for colour, radius in _painted():
        if radius > 0:
            out.setdefault(radius, set()).add(colour)
    return out


def check_a_diameter_edit_in_place_keeps_the_class_and_the_paint_follows_the_new_faces() -> None:
    ids = _plate_with_dowel(6.0)
    assert _bore_colours() == {3.0: {"255,128,128"}}, _bore_colours()
    result = _edit_hole(ids, _dowel(ids["top"], 12.0))  # what the adapter sends: same fit, the class and colour derived
    assert result["applied"] and not result["refused"], result
    assert _bore_colours() == {6.0: {"255,128,128"}}, _bore_colours()  # the old bore is gone, the new one is painted
    hole = [f for f in _rec().features if f.kind == "hole"][0]
    assert (hole.params["hole_class"], hole.params["colour"], hole.params["precision_grade"]) == ("precision", RED, 8)
    assert hole.created_faces


def check_the_paint_follows_a_diameter_edit_of_a_hole_that_a_pattern_copied_and_a_later_hole_followed() -> None:
    ids = _plate_with_dowel(4.0)
    call(
        "linear_pattern", op("copies", features=[ids["hole"]["feature"]], direction=[1, 0, 0], spacing_mm=12.0, count=3)
    )
    later = dict(
        face=ids["top"]["handle"], position=[30.0, 10.0], standard="simple", size=None, diameter_mm=3.0,
        end_condition="through_all", depth_mm=None,
    )  # fmt: skip
    call("hole", op("plain", **later))  # made while the convention was off: no colour, never painted
    assert _bore_colours() == {2.0: {"255,128,128"}}, _bore_colours()
    assert _painted()[("255,128,128", 2.0)] == 3, _painted()
    result = _edit_hole(ids, _dowel(ids["top"], 8.0))
    assert result["applied"] and not result["refused"], result
    assert _bore_colours() == {4.0: {"255,128,128"}}, _bore_colours()  # the plain hole (r 1.5) stays unpainted
    assert _painted()[("255,128,128", 4.0)] == 3, _painted()  # the seed and its two copies


def check_a_class_the_convention_gives_again_repaints_the_hole_in_place() -> None:
    ids = _plate_with_dowel(6.0)
    # the convention changed since the hole was made: the same fit now gives another colour (and a coarser precision grade)
    other = _dowel(ids["top"], 10.0, colour=[1, 2, 3], precision_grade=9)
    result = _edit_hole(ids, other)
    assert result["applied"] and not result["refused"], result
    assert _bore_colours() == {5.0: {"1,2,3"}}, _bore_colours()
    # the convention switched off: the hole keeps its fit and loses the paint
    off = _dowel(ids["top"], 11.0, colour=None, hole_class=None, precision_grade=None)
    assert _edit_hole(ids, off)["applied"]
    assert _painted() == {}, _painted()


def check_what_the_convention_derived_is_never_the_reason_an_edit_is_refused() -> None:
    ids = _plate_with_dowel(6.0)
    bare = _dowel(ids["top"], 9.0, colour=None, hole_class=None, precision_grade=None)  # the derived fields are absent
    result = _edit_hole(ids, bare)
    assert result["applied"] and not result["refused"], (
        result
    )  # was: "colour, hole_class, precision_grade is not a dimension"


def check_the_callout_of_an_edited_hole_shows_the_new_diameter_and_keeps_its_fit() -> None:
    import sheet_model as model

    ids = _plate_with_dowel(6.0)  # the hole is at (20, 10) on the plate
    call("create_drawing", op("d", template="A3", scale=1.0))
    call(
        "annotate_drawing", op("a", action="add", items=[{"type": "hole_dim", "id": "d1", "at": [[20, 10, 0]]}], ids=[])
    )

    def texts() -> str:
        page = drawing.find_page(_rec().fc_doc)
        _, _, ctx = drawing.compose(_rec(), page, drawing.spec_of(page), 1, 1, "check")
        unit = next(u for u in ctx.extras["units"] if u.key == "d1")
        return " | ".join(e.text for e in unit.entities if isinstance(e, model.Text))

    before = texts()
    assert "6" in before and "H7" in before, before
    assert _edit_hole(ids, _dowel(ids["top"], 12.0))["applied"]
    after = texts()
    assert "12" in after and "H7" in after and "+0,018/0" in after and after != before, (before, after)  # H7 of 12


def check_a_changed_fit_or_thread_is_still_another_hole_and_nothing_moves() -> None:
    ids = _plate_with_dowel(6.0)
    before = _bore_colours()
    result = _edit_hole(ids, _dowel(ids["top"], 6.0, fit="H9", hole_class="plain"))
    assert result["refused"] and "fit" in next(iter(result["refused"].values())), result
    assert _bore_colours() == before, _bore_colours()


def _compose() -> Any:
    rec = _rec()
    page = drawing.find_page(rec.fc_doc)
    spec = drawing.spec_of(page)
    sheet, _, _ = drawing.compose(rec, page, spec, 1, 1, None)
    return page, spec, sheet


def check_the_isometric_view_is_a_shaded_picture_under_its_lines() -> None:
    _build()
    call("create_drawing", op("d", template="A3", title="Placa", iso_colour=True))
    page, spec, sheet = _compose()
    assert spec.get("iso_colour") is True
    assert sheet.underlay, "no picture under the isometric view"
    fills = {s.fill for s in sheet.underlay}
    reddish = [f for f in fills if f[0] > f[1] + 40 and f[0] > f[2] + 40]
    greenish = [f for f in fills if f[1] > f[0] + 40 and f[1] > f[2] + 40]
    bluish = [f for f in fills if f[2] > f[0] + 40 and f[2] > f[1] + 40 and abs(f[0] - f[1]) < 30]
    assert reddish and greenish and bluish, sorted(fills)[:12]
    iso = drawing.iso_info(page)
    x0, y0, x1, y1 = iso.box
    xs = [p[0] for s in sheet.underlay for p in s.points]
    ys = [p[1] for s in sheet.underlay for p in s.points]
    # the picture sits on the lines of the TechDraw isometric view (same place, same scale): a few mm of slack for the
    # silhouette of the fill against the box of the geometry
    assert min(xs) > x0 - 2 and max(xs) < x1 + 2 and min(ys) > y0 - 2 and max(ys) < y1 + 2, (
        (min(xs), min(ys), max(xs), max(ys)),
        iso.box,
    )
    covered = (max(xs) - min(xs)) * (max(ys) - min(ys)) / ((x1 - x0) * (y1 - y0))
    assert covered > 0.6, covered
    for name, view in drawing.view_infos(page).items():
        vx0, vy0, vx1, vy1 = view.box
        assert not any(vx0 < p[0] < vx1 and vy0 < p[1] < vy1 for s in sheet.underlay for p in s.points), (
            f"colour inside {name}"
        )
    svg = sheet_svg.to_svg(sheet)
    first_line_layer = re.search(r'<g id="layer-(?!underlay)', svg)
    assert first_line_layer and svg.index('<g id="layer-underlay"') < first_line_layer.start()  # painted first


def check_the_flag_off_keeps_the_line_drawing_and_the_flag_stays_in_the_file() -> None:
    _build()
    call("create_drawing", op("d", template="A3", title="Placa"))
    assert not _compose()[2].underlay
    call("annotate_drawing", op("u", action="update", iso_colour=True))
    assert _compose()[2].underlay
    path = _tmp("placa.FCStd")
    saved = call(
        "save_document",
        op(
            "save",
            path=path,
            stamp={
                "design_id": "d",
                "plan_id": "p",
                "plan_revision": 1,
                "save_seq": 1,
                "last_step_index": 3,
                "harness_version": "0.0.0",
                "plan_sha256": "a" * 64,
                "sidecar_sha256": None,
            },
        ),
    )
    main.reset()
    call("open_document", op("open", path=path, adapter_state=saved["adapter_state"]))
    assert _compose()[2].underlay, "the picture is gone after a reopen"
    call("annotate_drawing", op("u2", action="update", iso_colour=False))
    assert not _compose()[2].underlay


def check_the_png_and_the_pdf_carry_the_colours() -> None:
    _build()
    call("create_drawing", op("d", template="A3", title="Placa", iso_colour=True))
    png = _tmp("iso.png")
    pdf = _tmp("iso.pdf")
    call("capture_drawing", op("c", path=png, width=1800))
    call("export_drawing", op("e", path=pdf, format="pdf"))
    from PySide import QtGui  # noqa: PLC0415

    image = QtGui.QImage(png)
    assert not image.isNull()
    counts = {"red": 0, "green": 0, "blue": 0}
    for y in range(0, image.height(), 2):
        for x in range(0, image.width(), 2):
            c = image.pixelColor(x, y)
            r, g, b = c.red(), c.green(), c.blue()
            if r > g + 40 and r > b + 40:
                counts["red"] += 1
            elif g > r + 40 and g > b + 40:
                counts["green"] += 1
            elif b > r + 40 and b > g + 40 and abs(r - g) < 30:
                counts["blue"] += 1
    assert all(n > 5 for n in counts.values()), counts
    assert os.path.getsize(pdf) > 4000
    print(f"LOOK AT: {png}\nLOOK AT: {pdf}")  # noqa: T201


def check_the_visible_worker_paints_the_faces_and_respects_a_hand_colour() -> None:
    import gui

    if not gui.ACTIVE:
        return
    base = _build()
    rec = _rec()
    hole_colours.sync(rec)
    body = rec.live_bodies()[0]
    obj = rec.fc_doc.getObject(hole_colours._owner(body))  # noqa: SLF001

    def assert_painted() -> dict[int, tuple[int, int, int]]:
        colours = hole_colours.face_colours(rec, body)
        shown = list(obj.ViewObject.DiffuseColor)
        assert len(shown) == len(obj.Shape.Faces)
        for index, rgb in colours.items():
            assert tuple(round(c * 255) for c in shown[index][:3]) == tuple(rgb), (index, shown[index], rgb)
        return colours

    assert_painted()
    # a later feature renumbers the faces: the next sync paints the holes again, on their new numbers
    top = select_face(faces_of(base["feature"]), normal=(0, 0, 1))
    extrude("boss", sketch("boss_sk", top["handle"], [circle("boss", (35.0, 20.0), 8.0)]), 5.0)
    hole_colours.sync(rec)
    colours = assert_painted()
    # the engineer paints one of those faces yellow by hand: it is his
    index = next(iter(colours))
    mine = list(obj.ViewObject.DiffuseColor)
    mine[index] = (1.0, 1.0, 0.0, 0.0)
    obj.ViewObject.DiffuseColor = mine
    hole_colours.sync(rec)
    assert tuple(obj.ViewObject.DiffuseColor[index][:3]) == (1.0, 1.0, 0.0)
