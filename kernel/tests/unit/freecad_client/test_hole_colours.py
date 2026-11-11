# ruff: noqa: E501
"""The colour of a hole in the worker, without FreeCAD: which faces a coloured hole owns (and that they follow later
operations), what the per-face colour list becomes (and that the engineer's own colour is respected), the class loaded
from the one place that decides it, the fit a ``hole_dim`` takes from the hole, and the review finding when they differ."""

import dataclasses
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import hole_class  # noqa: E402
import hole_colours  # noqa: E402
import hole_paint  # noqa: E402
import sheet_quality as quality  # noqa: E402
import sheet_review as review  # noqa: E402

from .test_sheet_quality import DOWELS, draw, plate  # noqa: E402

PRECISION = (255, 128, 128)
PLAIN = (0, 192, 0)
THREADED = (128, 128, 255)
BASE = (0.8, 0.8, 0.8, 0.0)
kernel_classify = importlib.import_module("cotaforge.conventions.classify")


# --- which faces ------------------------------------------------------------------------------------------------------------


def _face(index: int, stale: bool = False) -> Any:
    return SimpleNamespace(index=index, stale=stale)


def _feature(fid: str, kind: str, faces: list[str], **params: Any) -> Any:
    return SimpleNamespace(id=fid, kind=kind, params=params, created_faces=faces)


def test_a_coloured_hole_owns_the_faces_it_made_wherever_they_are_now() -> None:
    plate_ = _feature("feature:1", "extrude", ["face:1"])
    bore = _feature("feature:2", "hole", ["face:10", "face:11"], colour=list(PRECISION), hole_class="precision")
    doc = SimpleNamespace(features=[plate_, bore])
    # a later operation renumbered the faces: the registry says where they are now
    body = SimpleNamespace(faces={"face:10": _face(7), "face:11": _face(8), "face:1": _face(0)})
    assert hole_colours.face_colours(doc, body) == {7: PRECISION, 8: PRECISION}
    body = SimpleNamespace(faces={"face:10": _face(12), "face:11": _face(13)})
    assert hole_colours.face_colours(doc, body) == {12: PRECISION, 13: PRECISION}


def test_a_face_a_later_feature_destroyed_or_made_stale_is_not_painted() -> None:
    bore = _feature("feature:2", "hole", ["face:10", "face:11"], colour=list(PLAIN))
    doc = SimpleNamespace(features=[bore])
    body = SimpleNamespace(faces={"face:10": _face(3, stale=True)})  # face:11 is gone
    assert hole_colours.face_colours(doc, body) == {}


def test_a_hole_made_with_the_convention_off_is_not_painted() -> None:
    bore = _feature("feature:2", "hole", ["face:10"], colour=None)
    old = _feature("feature:3", "hole", ["face:11"])  # saved before there was a convention: no colour at all
    doc = SimpleNamespace(features=[bore, old])
    assert hole_colours.face_colours(doc, SimpleNamespace(faces={"face:10": _face(1), "face:11": _face(2)})) == {}


def test_a_pattern_or_mirror_of_a_coloured_hole_takes_its_colour() -> None:
    seed = _feature("feature:2", "hole", ["face:10"], colour=list(THREADED), hole_class="threaded")
    pattern = _feature("feature:3", "linear_pattern", ["face:20", "face:21"], features=[{"id": "feature:2"}])
    mirror = _feature("feature:4", "mirror", ["face:30"], features=[{"id": "feature:3"}])
    other = _feature("feature:5", "linear_pattern", ["face:40"], features=[{"id": "feature:1"}])
    doc = SimpleNamespace(features=[seed, pattern, mirror, other])
    faces = {f"face:{n}": _face(n) for n in (10, 20, 21, 30, 40)}
    assert hole_colours.face_colours(doc, SimpleNamespace(faces=faces)) == {
        10: THREADED,
        20: THREADED,
        21: THREADED,
        30: THREADED,
    }


def test_a_document_without_holes_costs_one_pass_and_paints_nothing() -> None:
    doc = SimpleNamespace(features=[_feature("feature:1", "extrude", ["face:1"])])
    assert hole_colours.hole_features(doc) == {}
    assert hole_colours.face_colours(doc, SimpleNamespace(faces={})) == {}


def test_the_fit_is_the_one_the_hole_declared() -> None:
    assert hole_colours.hole_fit(_feature("f", "hole", [], fit="H7")) == "H7"
    assert hole_colours.hole_fit(_feature("f", "hole", [])) is None


def test_a_pattern_or_mirror_of_a_hole_has_the_declarations_of_its_seed() -> None:
    """The copies are made by the pattern feature, not by the hole: their fit, class and thread are the seed's (the callout
    reads the hole's faces through ``created_by``)."""
    seed = _feature(
        "feature:2", "hole", ["face:10"], fit="H7", hole_class="precision", precision_grade=8, thread={"size": "M6"}
    )
    pattern = _feature("feature:3", "linear_pattern", ["face:20"], features=[{"id": "feature:2"}])
    mirrored = _feature("feature:4", "mirror", ["face:30"], features=[{"id": "feature:3"}])  # a mirror of the pattern
    other = _feature("feature:5", "extrude", ["face:1"])
    loose = _feature("feature:6", "linear_pattern", ["face:40"], features=[{"id": "feature:5"}])  # copies of a non-hole
    doc = SimpleNamespace(features=[seed, pattern, mirrored, other, loose])
    expected = ("H7", "precision", 8, {"size": "M6"})
    assert hole_colours.declarations(doc, seed) == expected
    assert hole_colours.declarations(doc, pattern) == expected
    assert hole_colours.declarations(doc, mirrored) == expected
    assert hole_colours.declarations(doc, other) is None
    assert hole_colours.declarations(doc, loose) is None


# --- the per-face colour list ------------------------------------------------------------------------------------------------


def rgba(rgb: tuple[int, int, int]) -> hole_paint.RGBA:
    return hole_paint.to_rgba(rgb)


def test_a_uniform_object_is_expanded_and_only_the_hole_faces_change() -> None:
    new = hole_paint.plan([BASE], 5, {1: PRECISION, 3: PLAIN}, [PRECISION, PLAIN], BASE)
    assert new == [BASE, rgba(PRECISION), BASE, rgba(PLAIN), BASE]


def test_nothing_is_written_when_the_list_is_already_right() -> None:
    right = [BASE, rgba(PRECISION), BASE]
    assert hole_paint.plan(right, 3, {1: PRECISION}, [PRECISION], BASE) is None


def test_a_stale_list_after_a_later_feature_is_made_again_from_the_registry() -> None:
    stale = [BASE, rgba(PRECISION), BASE]  # FreeCAD left a 3-face list on a solid that now has 6
    new = hole_paint.plan(stale, 6, {4: PRECISION}, [PRECISION], BASE)
    assert new == [BASE, BASE, BASE, BASE, rgba(PRECISION), BASE]


def test_the_engineers_own_colour_is_respected() -> None:
    red = (1.0, 0.0, 0.0, 0.0)
    shown = [BASE, red, rgba(PRECISION)]  # he painted face 1 red by hand
    new = hole_paint.plan(shown, 3, {1: PLAIN, 2: PRECISION}, [PRECISION, PLAIN], BASE)
    assert new is None  # face 1 stays red, face 2 is already right
    new = hole_paint.plan(shown, 3, {1: PLAIN, 2: PLAIN}, [PRECISION, PLAIN], BASE)
    assert new == [BASE, red, rgba(PLAIN)]  # a colour of ours does change when the class changed


def test_a_colour_of_ours_on_a_face_that_is_no_longer_a_hole_goes_back_to_the_base() -> None:
    new = hole_paint.plan([rgba(PRECISION), BASE], 2, {}, [PRECISION], BASE)
    assert new == []  # nothing of ours left: FreeCAD's one colour for the object


def test_a_uniform_object_the_engineer_painted_is_left_alone() -> None:
    blue = (0.0, 0.0, 1.0, 0.0)
    new = hole_paint.plan([blue], 3, {1: PRECISION}, [PRECISION], BASE)
    assert new is None  # his whole-object colour is not the base: no face of it is ours to repaint


# --- the one place that classifies ------------------------------------------------------------------------------------------


def test_the_worker_classifies_with_the_very_file_of_the_kernel() -> None:
    for threaded, fit in [(False, "H7"), (False, "H9"), (False, None), (True, "H7"), (False, "g6")]:
        assert hole_class.classify(threaded=threaded, fit=fit) == kernel_classify.classify(threaded=threaded, fit=fit)
    assert hole_class.classify(threaded=False, fit="H9", max_precision_grade=9) == "precision"
    assert hole_class.fit_grade("H7/g6") == 7


# --- hole_dim takes the fit of the hole -------------------------------------------------------------------------------------


def _with_fit(ctx: Any, fit: str | None, cls: str | None = None, grade: int | None = 8) -> None:
    ctx.holes[:] = [
        dataclasses.replace(h, fit=fit, hole_class=cls, precision_grade=grade) if h.diameter == 6.0 else h
        for h in ctx.holes
    ]


def test_hole_dim_takes_the_fit_the_hole_declares_when_it_gives_none() -> None:
    ctx, _ = plate()
    _with_fit(ctx, "H7", "precision")
    draw(ctx, {"type": "hole_dim", "id": "d", "at": [[x, y, 0] for x, y in DOWELS]})
    assert "4× Ø6 H7 (+0,012/0) pasante" in ctx.sheet.texts()


def test_hole_dim_with_its_own_fit_or_text_keeps_it() -> None:
    ctx, _ = plate()
    _with_fit(ctx, "H7", "precision")
    draw(ctx, {"type": "hole_dim", "id": "d", "at": [[x, y, 0] for x, y in DOWELS], "fit": "H8"})
    assert "4× Ø6 H8 (+0,018/0) pasante" in ctx.sheet.texts()
    ctx, _ = plate()
    _with_fit(ctx, "H7", "precision")
    draw(ctx, {"type": "hole_dim", "id": "d", "at": [[x, y, 0] for x, y in DOWELS], "text": "4x pin hole"})
    assert not [t for t in ctx.sheet.texts() if "H7" in t]


def test_a_declared_fit_the_tables_do_not_cover_is_dropped_not_an_error() -> None:
    ctx, _ = plate()
    _with_fit(ctx, "H12")  # IT12 is not in the tables
    draw(ctx, {"type": "hole_dim", "id": "d", "at": [[x, y, 0] for x, y in DOWELS]})
    assert "4× Ø6 pasante" in ctx.sheet.texts()


def test_holes_declared_with_different_fits_do_not_share_a_callout() -> None:
    ctx, _ = plate()
    ctx.holes[:] = [
        dataclasses.replace(h, fit="H7" if h.index < 2 else None) if h.diameter == 6.0 else h for h in ctx.holes
    ]
    draw(ctx, {"type": "hole_dim", "id": "d", "at": [[x, y, 0] for x, y in DOWELS]})
    texts = ctx.sheet.texts()
    assert "2× Ø6 H7 (+0,012/0) pasante" in texts and "2× Ø6 pasante" in texts


# --- the review finding -----------------------------------------------------------------------------------------------------


def _findings(ctx: Any, results: list[Any]) -> list[review.Finding]:
    ctx.extras.setdefault("units", [])
    data = review.ReviewInput(ctx.extras["items"], {r.id: r for r in results}, ctx, ctx.sheet, [])
    return [f for f in quality.check(data) if f.code == "hole_fit_differs"]


def _fit_item(fit: str) -> dict[str, Any]:
    return {"type": "hole_dim", "id": "d", "at": [[x, y, 0] for x, y in DOWELS], "fit": fit}


def test_a_callout_fit_on_a_hole_that_declares_none_is_an_info_finding() -> None:
    ctx, _ = plate()
    results = draw(ctx, _fit_item("H7"))
    found = _findings(ctx, results)
    assert found and found[0].severity == "info"
    assert "H7" in found[0].message and "ninguno" in found[0].message


def test_a_callout_fit_of_another_class_than_the_hole_is_an_info_finding() -> None:
    ctx, _ = plate()
    _with_fit(ctx, "H11", "plain")
    results = draw(ctx, _fit_item("H7"))
    found = _findings(ctx, results)
    assert found and "H11" in found[0].message and "plain" in found[0].message


def test_a_callout_tolerance_on_a_plain_hole_is_the_same_info_finding() -> None:
    ctx, _ = plate()
    _with_fit(ctx, "H11", "plain")
    spec = {"type": "hole_dim", "id": "d", "at": [[x, y, 0] for x, y in DOWELS], "tol": 0.005}
    found = _findings(ctx, draw(ctx, spec))
    assert found and found[0].severity == "info"
    assert "tolerancia" in found[0].message and "H11" in found[0].message and "plain" in found[0].message
    ctx, _ = plate()
    _with_fit(ctx, "H11", "plain")
    assert _findings(ctx, draw(ctx, {**spec, "tol": 0.5})) == []  # a wide zone is what a plain hole has


def test_the_same_class_is_no_finding() -> None:
    ctx, _ = plate()
    _with_fit(ctx, "H7", "precision")
    assert _findings(ctx, draw(ctx, _fit_item("H6"))) == []  # another fit of the same class
    ctx, _ = plate()
    _with_fit(ctx, "H7", "precision")
    assert _findings(ctx, draw(ctx, {"type": "hole_dim", "id": "d", "at": [[x, y, 0] for x, y in DOWELS]})) == []


def test_a_hole_no_operation_made_gives_no_finding() -> None:
    ctx, _ = plate()
    ctx.holes[:] = [dataclasses.replace(h, declared=False) for h in ctx.holes]
    assert _findings(ctx, draw(ctx, _fit_item("H7"))) == []


def test_the_grade_limit_of_the_convention_that_made_the_hole_is_the_one_used() -> None:
    ctx, _ = plate()
    _with_fit(ctx, "H9", "precision", grade=9)  # the engineer's convention: IT9 is still precision
    assert _findings(ctx, draw(ctx, _fit_item("H9"))) == []


# --- the counterbore a cut opened on a drilled hole -------------------------------------------------------------------------


class Cylinder:
    def __init__(self, x: float, y: float) -> None:
        self.Center, self.Axis = _p(x, y, 0.0), _p(0.0, 0.0, 1.0)


class Cone:
    def __init__(self, x: float, y: float) -> None:
        self.Apex, self.Axis = _p(x, y, 9.0), _p(0.0, 0.0, -1.0)


class Plane:
    def __init__(self, normal: tuple[float, float, float] = (0.0, 0.0, 1.0)) -> None:
        self.Axis = _p(*normal)


class Circle:
    def __init__(self, x: float, y: float) -> None:
        self.Center = _p(x, y, 0.0)


class Line:
    pass


def _p(x: float, y: float, z: float) -> Any:
    return SimpleNamespace(x=x, y=y, z=z)


def _solid_face(index: int, surface: Any, edges: list[Any] | None = None) -> Any:
    shape = SimpleNamespace(Surface=surface, Edges=[SimpleNamespace(Curve=c) for c in edges or []])
    return SimpleNamespace(index=index, stale=False, shape=shape)


def _drilled_plate() -> tuple[Any, dict[str, Any]]:
    bore = _feature("feature:2", "hole", ["face:10"], colour=list(PLAIN), hole_class="plain")
    pocket = _feature("feature:3", "cut", ["face:20", "face:21"])
    beside = _feature("feature:4", "cut", ["face:30", "face:31"])
    doc = SimpleNamespace(features=[_feature("feature:1", "extrude", ["face:1"]), bore, pocket, beside])
    faces = {
        "face:10": _solid_face(10, Cylinder(20, 15)),
        "face:20": _solid_face(20, Cylinder(20, 15)),  # the wall of the counterbore, about the same axis
        "face:21": _solid_face(21, Plane(), [Circle(20, 15), Circle(20, 15)]),  # its floor
        "face:30": _solid_face(30, Cylinder(60, 15)),  # a recess with no hole in it
        "face:31": _solid_face(31, Plane(), [Circle(60, 15)]),
    }
    return doc, faces


def test_the_wall_and_floor_of_a_counterbore_cut_about_the_axis_of_a_coloured_hole_are_painted_with_it() -> None:
    doc, faces = _drilled_plate()
    assert hole_colours.face_colours(doc, SimpleNamespace(faces=faces)) == {10: PLAIN, 20: PLAIN, 21: PLAIN}


def test_a_cone_about_the_axis_is_the_hole_and_a_plane_that_is_not_bounded_by_its_circles_is_not() -> None:
    doc, faces = _drilled_plate()
    doc.features.append(_feature("feature:5", "cut", ["face:40", "face:41", "face:42"]))
    faces["face:40"] = _solid_face(40, Cone(20, 15))
    faces["face:41"] = _solid_face(41, Plane(), [Circle(20, 15), Line()])  # a flat with an edge that is no circle
    faces["face:42"] = _solid_face(42, Plane((1.0, 0.0, 0.0)), [Circle(20, 15)])  # not square to the axis
    assert hole_colours.face_colours(doc, SimpleNamespace(faces=faces)) == {10: PLAIN, 20: PLAIN, 21: PLAIN, 40: PLAIN}


def test_a_pocket_is_not_a_hole_when_the_hole_has_no_colour() -> None:
    doc, faces = _drilled_plate()
    doc.features[1].params["colour"] = None  # made while the convention was off
    assert hole_colours.face_colours(doc, SimpleNamespace(faces=faces)) == {}


def test_a_stale_face_of_the_pocket_is_not_painted() -> None:
    doc, faces = _drilled_plate()
    faces["face:21"].stale = True
    assert hole_colours.face_colours(doc, SimpleNamespace(faces=faces)) == {10: PLAIN, 20: PLAIN}
