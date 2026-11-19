# ruff: noqa: E501
"""Special views of a production drawing in the headless worker: sections with hatched cut faces, details, auxiliary
views, hidden and centre lines, and the review of a sheet.

Like ``check_drawing_sheets`` the sheets are inspected as ``sheet_model`` data (what the PDF and the DXF are written
from). The point here is that every derived view comes out of the model: the section is cut by the plane the item
names, the hatch fills exactly the cut faces, the hidden lines are the ones hidden-line removal finds and a detail is
the part of the view inside its circle.
"""

import math
import os
import tempfile
from typing import Any

import drawing
import main
import sheet_model as model
from harness import call, circle, expect_error, extrude, faces_of, line, new_document, op, rect, select_face, sketch

# bracket: profile in the XZ plane, 80 wide, 30 high at the left, a sloped face at the right, 40 deep
PROFILE = [(0, 0), (80, 0), (80, 12), (55, 30), (0, 30)]
DEPTH = 40.0


def _rec() -> Any:
    assert main.WORKER.active is not None
    return main.WORKER.active


def _tmp(name: str) -> str:
    return os.path.join(tempfile.mkdtemp(prefix="cadh_views_"), name)


def build_bracket(with_holes: bool = True) -> dict[str, Any]:
    """Housing bracket: sloped face, a counterbored hole (D6.6 through, D11 x 6 deep), a blind D5 hole, a blind D6 hole
    square to the sloped face and a 2 x 2 chamfer on the lower right edge. Returns the base extrusion."""
    new_document("soporte")
    corners = [(x, -z) for x, z in PROFILE + [PROFILE[0]]]  # the XZ sketch plane has its y axis along -z
    sk = sketch(
        "profile",
        "XZ",
        [line(f"e{i}", corners[i], corners[i + 1]) for i in range(len(PROFILE))],
    )
    base = extrude("base", sk, DEPTH)
    if not with_holes:
        return base

    def top() -> dict[str, Any]:
        return select_face(faces_of(base["feature"]), normal=(0, 0, 1), area_min=1000)

    def drill(name: str, face: dict[str, Any], where: tuple[float, float], **fields: Any) -> dict[str, Any]:
        params = {"standard": "simple", "size": None, "end_condition": "through_all", "depth_mm": None}
        params.update(fields)
        return call("hole", op(name, face=face["handle"], position=list(where), **params))

    face = top()
    cx, cy, _ = face["center"]
    # holes are placed from the centre of the top face; a counterbore is the wide blind hole first, then the narrow
    # one through its floor
    pocket = drill("cbore", face, (25 - cx, 20 - cy), diameter_mm=11, end_condition="blind", depth_mm=6)
    floor = select_face(faces_of(pocket["feature"]), normal=(0, 0, 1), center=(25, 20, 24))
    drill("through", floor, (0, 0), diameter_mm=6.6)
    face = top()
    cx, cy, _ = face["center"]
    drill("blind", face, (45 - cx, 20 - cy), diameter_mm=5, end_condition="blind", depth_mm=12)
    slope = select_face(faces_of(base["feature"]), center=(67.5, 20, 21))
    drill("square", slope, (0, 0), diameter_mm=6, end_condition="blind", depth_mm=8)
    faces = faces_of(base["feature"])
    side = select_face(faces, normal=(1, 0, 0), area_min=100)
    bottom = select_face(faces, normal=(0, 0, -1), area_min=1000)
    edge = call("edges_between", op("q", a=side["handle"], b=bottom["handle"]))["edges"]
    call(
        "chamfer",
        op("lead_in", edges=[edge[0]["handle"]], distance1_mm=2, distance2_mm=2, distance1_face=bottom["handle"]),
    )
    return base


def composed(sheet: str | None = None) -> tuple[model.Sheet, list[Any], Any]:
    rec = _rec()
    page = drawing.find_page(rec.fc_doc, sheet)
    spec = drawing.spec_of(page)
    number = drawing.pages(rec.fc_doc).index(page) + 1
    return drawing.compose(rec, page, spec, number, len(drawing.pages(rec.fc_doc)), "check")


def add(*items: dict[str, Any]) -> list[dict[str, Any]]:
    return call("annotate_drawing", op("a", action="add", items=list(items)))["items"]


def layer(sheet: model.Sheet, name: str) -> list[Any]:
    return sheet.of_layer(name)


def slope_face_id(base: dict[str, Any]) -> str:
    return str(select_face(faces_of(base["feature"]), center=(67.5, 20, 21))["handle"]["id"])


def loops_area(loops: list[list[tuple[float, float]]]) -> float:
    """The area the loops bound, holes taken away (a loop inside another is a hole)."""

    def area(loop: list[tuple[float, float]]) -> float:
        return 0.5 * abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(loop, loop[1:] + loop[:1], strict=True)))

    def inside(a: list[tuple[float, float]], b: list[tuple[float, float]]) -> bool:
        x, y = a[0]
        hit = False
        for p, q in zip(b, b[1:] + b[:1], strict=True):
            if (p[1] > y) != (q[1] > y) and x < (q[0] - p[0]) * (y - p[1]) / (q[1] - p[1]) + p[0]:
                hit = not hit
        return hit

    total = 0.0
    for loop in loops:
        depth = sum(1 for other in loops if other is not loop and inside(loop, other))
        total += area(loop) * (-1 if depth % 2 else 1)
    return total


def _xs(e: Any) -> list[float]:
    if isinstance(e, model.Line):
        return [e.x1, e.x2]
    if isinstance(e, model.Circle | model.Arc):
        return [e.cx - e.r, e.cx + e.r]
    return [p[0] for p in e.points]


def _ys(e: Any) -> list[float]:
    if isinstance(e, model.Line):
        return [e.y1, e.y2]
    if isinstance(e, model.Circle | model.Arc):
        return [e.cy - e.r, e.cy + e.r]
    return [p[1] for p in e.points]


def _inside(e: Any, box: tuple[float, float, float, float], slack: float = 1.0) -> bool:
    xs, ys = _xs(e), _ys(e)
    return (
        box[0] - slack <= min(xs)
        and max(xs) <= box[2] + slack
        and box[1] - slack <= min(ys)
        and max(ys) <= box[3] + slack
    )


# --- the projection ---------------------------------------------------------------------------------------------------


def check_the_hidden_line_projection_lines_up_with_the_techdraw_views() -> None:
    build_bracket()
    call("create_drawing", op("d", template="A3", title="Soporte", scale=1.0))
    _, _, ctx = composed()
    for name, view in ctx.views.items():
        projection = ctx.geometry.projection(view.centre, view.xdir, view.direction)
        xs = [x for e in projection.visible for x in _xs(e)]
        ys = [y for e in projection.visible for y in _ys(e)]
        k = view.scale
        box = (view.x + min(xs) * k, view.y + min(ys) * k, view.x + max(xs) * k, view.y + max(ys) * k)
        assert all(abs(a - b) < 0.05 for a, b in zip(box, view.box, strict=True)), (name, box, view.box)


# --- hidden lines and centre lines ----------------------------------------------------------------------------------------


def check_view_lines_draw_hidden_edges_in_the_elevations_and_keep_holes_circles() -> None:
    build_bracket()
    call("create_drawing", op("d", template="A3", scale=1.0))
    call("annotate_drawing", op("r", action="remove", ids=["loc"]))  # the default location draws centre lines too
    before, _, plain = composed()
    boxes = list(plain.extras["view_boxes"])
    assert not before.of_layer(model.HIDDEN)  # without the item the views show no hidden edges and no axes
    assert not [e for e in before.of_layer(model.AXES) if any(_inside(e, box, 0.0) for box in boxes)]
    row = add({"type": "view_lines"}, {"type": "hole_dim", "all": True})[-2]
    assert row["status"] == "ok", row
    sheet, _, ctx = composed()
    top, front = ctx.views["top"], ctx.views["front"]
    assert [e for e in sheet.of_layer(model.HIDDEN) if _inside(e, front.box)]
    assert not [e for e in sheet.of_layer(model.HIDDEN) if _inside(e, top.box)]
    # the holes keep being circles (hidden-line removal hands back splines): the through hole, its counterbore, the
    # blind hole
    radii = {
        round(e.r, 2) for e in sheet.of_layer(model.CONTOUR) if isinstance(e, model.Circle) and _inside(e, top.box)
    }
    assert {2.5, 3.3, 5.5} <= radii, radii
    # one centre cross per hole axis in the top view, though the callout also asks for one
    bars = [
        e
        for e in sheet.of_layer(model.AXES)
        if isinstance(e, model.Line) and abs(e.y1 - e.y2) < 1e-9 and top.box[1] < e.y1 < top.box[3]
    ]
    assert len(bars) == 3, (
        bars
    )  # the counterbore + through hole share a cross, the blind hole has one, the slope hole is side-on
    side_on = [e for e in sheet.of_layer(model.AXES) if isinstance(e, model.Line) and abs(e.x1 - e.x2) < 1e-9]
    assert [e for e in side_on if _inside(e, front.box, 6.0)]


# --- section --------------------------------------------------------------------------------------------------------------


def check_a_section_through_the_holes_hatches_exactly_the_cut_faces() -> None:
    build_bracket()
    call("create_drawing", op("d", template="A3", scale=1.0))
    row = add({"type": "section", "holes": "all", "view": "top"})[-1]
    assert row["status"] == "ok" and "A-A in the top view" in row["detail"], row
    sheet, _, ctx = composed()
    assert "A-A" in [e.text for e in sheet.entities if isinstance(e, model.Text)]
    rect = ctx.extras["view_rects"][row["id"]]
    for box in ctx.extras["view_boxes"]:
        assert not (rect[0] < box[2] and box[0] < rect[2] and rect[1] < box[3] and box[1] < rect[3]), (rect, box)
    # the plane y = 20 through the bracket: its profile, minus the 2 x 2 chamfer and what the holes take out of it
    top = ctx.views["top"]
    cut = ((25.0, 20.0, 12.0), (0.0, -1.0, 0.0))
    loops = ctx.geometry.projection(top.centre, (1.0, 0.0, 0.0), (0.0, -1.0, 0.0), cut).cut_loops
    expected = 2175.0 - 2.0 - (6.6 * 24 + 11 * 6 + 5 * 12 + 6 * 8)
    assert abs(loops_area(loops) - expected) < 1.0, (loops_area(loops), expected)
    lines = [e for e in sheet.of_layer(model.HATCH) if isinstance(e, model.Line)]
    assert lines and all(abs(abs(e.x2 - e.x1) - abs(e.y2 - e.y1)) < 1e-6 for e in lines)  # 45 degrees
    length = sum(math.hypot(e.x2 - e.x1, e.y2 - e.y1) for e in lines)
    step = min(4.0, max(1.5, 0.04 * 80))  # the spacing hatch_spacing picks for an area 80 mm across
    assert abs(length * step - expected) / expected < 0.06, (length * step, expected)  # the hatch fills the cut faces
    hidden_in_view = [e for e in sheet.of_layer(model.HIDDEN) if _inside(e, rect, 0.0)]
    assert not hidden_in_view  # a section shows no hidden lines
    assert [e.text for e in sheet.of_layer(model.TEXT) if isinstance(e, model.Text)].count(
        "A"
    ) == 2  # a letter at each end


def check_a_section_is_stored_with_the_sheet_and_describes_itself() -> None:
    build_bracket()
    call("create_drawing", op("d", template="A3", scale=1.0))
    row = add({"type": "section", "at": [40, 20, 15], "normal": [0, -1, 0], "view": "top"})[-1]
    assert row["status"] == "ok", row
    described = call("describe_drawing", op("l"))["sheets"][0]["items"][-1]
    assert described["status"] == "ok" and described["spec"]["normal"] == [0.0, -1.0, 0.0]


def check_a_plane_that_misses_the_part_is_refused_and_not_stored() -> None:
    build_bracket()
    call("create_drawing", op("d", template="A3", scale=1.0))
    row = add({"type": "section", "at": [40, 500, 15], "normal": [0, -1, 0], "view": "top"})[-1]
    assert row["status"] == "refused" and "cutting plane" in row["detail"], row
    listed = call("describe_drawing", op("l"))["sheets"][0]["items"]
    assert [i["type"] for i in listed if not i["spec"].get("auto")] == ["overall"]


def build_sleeve() -> dict[str, Any]:
    """A synthetic sleeve: round bar D60 x 50, a through bore M45x1.5 made by the hole operation (cosmetic thread) and two flats."""
    new_document("manguito")
    base = extrude("base", sketch("od", "XY", [circle("od", (0, 0), 60)]), 50)

    def top() -> dict[str, Any]:
        return select_face(faces_of(base["feature"]), normal=(0, 0, 1), area_min=100)

    call(
        "hole",
        op(
            "bore",
            face=top()["handle"],
            position=[0, 0],
            standard="iso_tapped",
            size="M45x1.5",
            diameter_mm=43.5,
            end_condition="through_all",
            depth_mm=None,
            thread={"size": "M45x1.5", "depth_mm": None},
        ),
    )
    for number, x in enumerate((28.5, -28.5), start=1):
        flat = sketch(f"flat{number}", top()["handle"], [rect("f", (x, 0), 5, 70)])
        call(
            "cut",
            op(
                f"cut{number}",
                sketch=flat["sketch"],
                depth_mm=30,
                end_condition="blind",
                direction="negative",
                both_directions=False,
                depth2_mm=None,
            ),
        )
    return base


def check_a_section_of_a_sleeve_is_a_view_with_its_axis_thread_lines_and_items_on_it() -> None:
    build_sleeve()
    # with a front view the sheet turns the sleeve's axis horizontal (f3dd2e3): "top" no longer sees its bore as a circle,
    # the "right" view (the end view) does
    call("create_drawing", op("d", template="A3", scale=1.0, views=["front", "right"]))
    first = add({"type": "section", "id": "sec", "holes": "all", "view": "right"})[-1]
    assert (
        first["status"] == "ok"
        and "1 centre line(s)" in first["detail"]
        and "thread lines of 1 threaded bore" in first["detail"]
    ), first
    rows = {
        r["id"]: r
        for r in add(
            {"type": "hole_dim", "id": "thr", "all": True, "view": "A-A", "text": "M45x1,5 - 6H"},
            {
                "type": "dim",
                "id": "len",
                "kind": "distance",
                "from": {"at": [25, 0, 0]},
                "to": {"at": [25, 0, 50]},
                "view": "A-A",
                "along": "z",
            },
            {"type": "note", "id": "nt", "text": "Rosca M45x1,5 en toda la longitud", "view": "A-A"},
            {"type": "line", "id": "ax", "axis": "face:1", "view": "A-A"},
        )
    }
    for key in ("thr", "len", "nt", "ax"):
        assert rows[key]["status"] in ("ok", "warning"), rows[key]
    assert "contradice" not in rows["thr"]["detail"], rows["thr"]  # M45x1,5 is what the bore measures: D - P = 43.5
    sheet, _, ctx = composed()
    view = ctx.extras["sections"]["A-A"]
    assert view.derived and abs(view.scale - 1.0) < 1e-9
    thread = [e for e in sheet.of_layer(model.THREAD) if isinstance(e, model.Line)]
    assert len(thread) == 2 and all(abs(e.x1 - e.x2) < 1e-6 for e in thread), thread
    assert abs(abs(thread[0].x1 - thread[1].x1) - 45.0) < 1e-6  # the thin lines are at the major diameter
    chain = [e for e in sheet.of_layer(model.AXES) if isinstance(e, model.Line) and view.box[0] < e.x1 < view.box[2]]
    assert len(chain) >= 1 and all(
        abs(e.x1 - e.x2) < 1e-6 for e in chain
    )  # the centre line of the bore, a chain line along it
    assert "M45x1,5 - 6H" in sheet.texts() and "50" in sheet.texts()
    exported = call("export_drawing", op("e", path=_tmp("s.pdf"), format="pdf"))
    derived = [v for v in exported["views"] if v["kind"] == "derived"]
    assert len(derived) == 1 and derived[0]["rect"][0] < derived[0]["rect"][2], exported["views"]
    bad = add({"type": "hole_dim", "id": "bad", "all": True, "text": "M42x1,5 - 6H"})[-1]
    assert bad["status"] == "warning" and "contradice el modelo" in bad["detail"], bad
    assert "callout_contradicts" in {f["code"] for f in review()}


# --- detail and auxiliary view ----------------------------------------------------------------------------------------------


def check_a_detail_is_the_part_of_the_view_inside_its_circle_enlarged() -> None:
    build_bracket()
    call("create_drawing", op("d", template="A3", scale=1.0))
    row = add({"type": "detail", "view": "front", "at": [80, 20, 0], "radius_mm": 5})[-1]
    assert row["status"] == "ok" and "A (5:1)" in row["detail"], row
    sheet, _, ctx = composed()
    rect = ctx.extras["view_rects"][row["id"]]
    circles = [e for e in sheet.of_layer(model.DIMENSIONS) if isinstance(e, model.Circle)]
    big = max(circles, key=lambda c: c.r)
    assert abs(big.r - 25.0) < 1e-6 and abs(min(circles, key=lambda c: c.r).r - 5.0) < 1e-6
    shown = [e for e in sheet.of_layer(model.CONTOUR) if _inside(e, rect, 0.0)]
    assert shown  # the chamfered corner
    for e in shown:
        for x, y in zip(_xs(e), _ys(e), strict=False):
            assert math.hypot(x - big.cx, y - big.cy) <= 25.0 + 1e-6 or isinstance(e, model.Circle | model.Arc)
    # the 2 x 2 chamfer, enlarged to 10 mm legs: a 45 degree line
    assert any(
        isinstance(e, model.Line) and abs(abs(e.x2 - e.x1) - abs(e.y2 - e.y1)) < 0.01 and abs(e.x2 - e.x1) > 5
        for e in shown
    )


def check_an_auxiliary_view_shows_the_hole_square_to_the_inclined_face_as_a_circle() -> None:
    base = build_bracket()
    call("create_drawing", op("d", template="A3", scale=1.0))
    row = add({"type": "aux_view", "face": slope_face_id(base)})[-1]
    assert row["status"] == "ok" and "square-on" in row["detail"], row
    sheet, _, ctx = composed()
    rect = ctx.extras["view_rects"][row["id"]]
    circles = [e for e in sheet.of_layer(model.CONTOUR) if isinstance(e, model.Circle) and _inside(e, rect, 0.0)]
    assert [round(c.r, 2) for c in circles] == [3.0], (
        circles
    )  # the D6 hole: a true circle here, an ellipse in the front


def check_a_face_a_standard_view_shows_square_on_is_not_an_auxiliary_view() -> None:
    base = build_bracket()
    call("create_drawing", op("d", template="A3", scale=1.0))
    top = select_face(faces_of(base["feature"]), normal=(0, 0, 1), area_min=1000)
    row = add({"type": "aux_view", "face": top["handle"]["id"]})[-1]
    assert row["status"] == "refused" and "square-on" in row["detail"], row


# --- the review -----------------------------------------------------------------------------------------------------------


def review() -> list[dict[str, Any]]:
    return call("describe_drawing", op("r", review=True))["sheets"][0]["findings"]


def check_the_review_of_a_complete_sheet_has_only_the_unlocated_slope_hole() -> None:
    base = build_bracket()
    call("create_drawing", op("d", template="A3", scale=1.0, symmetry=True))  # symmetry is declared intent, not shape
    add(
        {"type": "view_lines"},
        {"type": "hole_dim", "all": True},
        {"type": "datum_dims"},
        {"type": "section", "holes": "all", "view": "top"},
        {"type": "detail", "view": "front", "at": [80, 20, 0], "radius_mm": 5},
        {"type": "aux_view", "face": slope_face_id(base)},
        {"type": "note", "text": "Chaflán 2 x 45° en las aristas no acotadas"},
    )
    found = review()
    # the finished-drawing list (notes, hidden-line density) is asked about too: what is left is about the drawing itself
    own = [f for f in found if not f["code"].startswith("notes_") and f["code"] != "hidden_dense"]
    warnings = [f["code"] for f in own if f["severity"] == "warning"]
    # the hole square to the slope is in no standard view's datum table; it is also a plain hole of a dowel size;
    # the right view and the section carry no dimension on this sheet; the views fill 19 % of the A3 the test chose
    # (sheet_underused, merged in 6c22a27)
    assert warnings == [
        "hole_not_located",
        "view_adds_nothing",
        "view_adds_nothing",
        "fit_hole_no_tolerance",
        "sheet_underused",
    ], found
    assert {f["message"].split()[2] for f in own if f["code"] == "view_adds_nothing"} == {"right", "A-A"}, found
    assert found[0]["features"], found
    # sheet_underused is advice about paper, never blocking; every other finding blocks until fixed or answered
    assert all(f["id"] and (f["blocking"] or f["code"] == "sheet_underused") for f in found), found
    assert len({f["id"] for f in found}) == len(found), found


def check_the_review_of_a_bad_sheet_names_what_is_wrong_and_where() -> None:
    base = build_bracket()
    top = select_face(faces_of(base["feature"]), normal=(0, 0, 1), area_min=1000)
    slot_sk = sketch(
        "slot_sk", top["handle"], [{"kind": "slot", "name": "sl", "start": [-15, -10], "end": [-15, 10], "width": 6}]
    )
    cut = op(
        "slot",
        sketch=slot_sk["sketch"],
        end_condition="blind",
        depth_mm=5,
        direction="negative",
        both_directions=False,
        depth2_mm=None,
    )
    call("cut", cut)
    call("create_drawing", op("d", template="A3", scale=1.0))
    add(
        {"type": "hole_dim", "at": [25, 20, 30]},
        {"type": "datum_dims"},
        {
            "type": "dim",
            "id": "d1",
            "kind": "distance",
            "from": {"at": [25, 20, 30]},
            "to": {"at": [45, 20, 30]},
            "along": "x",
        },
        {
            "type": "dim",
            "id": "d2",
            "kind": "distance",
            "from": {"at": [25, 20, 30]},
            "to": {"at": [45, 20, 30]},
            "along": "x",
        },
    )
    found = review()
    codes = {f["code"] for f in found}
    wanted = {"hole_no_callout", "feature_no_dimension", "duplicate_dimension", "over_dimensioned", "no_view_lines"}
    assert wanted <= codes, codes
    slot = [f for f in found if f["code"] == "feature_no_dimension" and f["severity"] == "warning"]
    assert slot and slot[0]["features"], slot
    over = [f for f in found if f["code"] == "over_dimensioned"]
    assert any("d1" in f["items"] for f in over) and all(f["items"][0].startswith("g") for f in over), over


def check_review_runs_on_every_sheet_and_changes_nothing() -> None:
    build_bracket()
    call("create_drawing", op("d", template="A3", scale=1.0))
    call("create_drawing", op("d2", template="A4", sheet="Otra", scale=1.0))
    result = call("describe_drawing", op("r", review=True))
    assert [len(s["findings"]) > 0 for s in result["sheets"]] == [True, True]
    plain = call("describe_drawing", op("l"))
    assert all("findings" not in s for s in plain["sheets"])
    assert [i["type"] for i in plain["sheets"][0]["items"] if not i["spec"].get("auto")] == ["overall"]
    expect_error("invalid_request", "describe_drawing", op("x", review=True, sheet="Nope"))
