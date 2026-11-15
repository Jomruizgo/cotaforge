# ruff: noqa: E501
"""Sections, details and auxiliary views are TechDraw's own view objects, never sheet geometry (no FreeCAD).

An item that makes a view asks for it as a ``NativeView`` (``DrawViewSection`` / ``DrawViewDetail`` / ``DrawViewPart``); what it
draws for the headless PDF stays in the sheet's entities, but the page of the CAD holds no symbol of it. These tests are the
guard of that: a section, detail or auxiliary view that is drawn only as sheet geometry fails here."""

import math
import sys
from pathlib import Path

import pytest

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import sheet_context as sc  # noqa: E402
import sheet_model as model  # noqa: E402
import sheet_units as su  # noqa: E402

from .test_sheet_items import draw  # noqa: E402
from .test_sheet_sections import two_bores  # noqa: E402
from .test_sheet_views import views_context  # noqa: E402

VIEW_ITEMS = {
    "section": {"type": "section", "id": "v", "holes": "all", "view": "top"},
    "removed section": {"type": "section", "id": "v", "holes": "all", "view": "top", "removed": True},
    "detail": {"type": "detail", "id": "v", "view": "front", "at": [100, 30, 8], "radius_mm": 5},
    "aux_view": {"type": "aux_view", "id": "v", "face": "face:slope"},
}


def unit_of(ctx: sc.Context, key: str = "v") -> su.Unit:
    """The unit the item ``key`` makes: everything the sheet holds, with the natives the item asked for."""
    natives = tuple(ctx.extras.get("natives", {}).get(key, ()))
    return su.Unit(key, key, su.KIND_ITEM, tuple(ctx.sheet.entities), "", None, natives)


@pytest.mark.parametrize("name", sorted(VIEW_ITEMS))
def test_every_kind_of_view_item_asks_for_a_techdraw_view(name: str) -> None:
    ctx = two_bores() if "section" in name else views_context()
    (result,) = draw(ctx, VIEW_ITEMS[name])
    assert result.status == "ok", result.detail
    unit = unit_of(ctx)
    view = su.view_of(unit)
    assert view is not None, f"a {name} drawn only as sheet geometry: it must be a NativeView (TechDraw's own view object)"
    assert view.parent in ("front", "top", "right", "isometric") and view.scale > 0
    # the sheet draws nothing of it on the page: the cut, the hatch, the cutting line and the letters are the view's own
    assert unit.residue == ()
    letters = [n for n in unit.natives if isinstance(n, su.NativeNote)]
    assert su.object_count(unit) == 1 + len(letters)  # the view and the letters beside its arrows: annotations
    assert len(letters) == (1 if name == "aux_view" else 2 if "section" in name else 0)


def test_a_section_asks_for_the_plane_the_frame_and_the_scale_of_the_view_it_draws() -> None:
    ctx = two_bores()
    (result,) = draw(ctx, VIEW_ITEMS["section"])
    assert result.status == "ok", result.detail
    (view,) = [n for n in ctx.extras["natives"]["v"] if isinstance(n, su.NativeView)]
    info = ctx.view(view.name)
    assert view.kind == "section" and view.name == "A-A" and view.letter == "A" and view.parent == "top"
    assert view.at is not None and view.scale == info.scale
    assert view.direction == info.direction and view.xdir == info.xdir  # TechDraw's frame is the sheet's
    assert view.box == info.box  # where the sheet draws it: TechDraw's object is put there
    assert math.isclose(sc.dot(view.direction, view.xdir), 0.0, abs_tol=1e-9)


def test_the_dimensions_of_a_section_name_its_view_so_they_measure_in_techdraws_own_object() -> None:
    ctx = two_bores()
    draw(ctx, VIEW_ITEMS["section"])
    (result,) = draw(ctx, {"type": "hole_dim", "id": "d", "all": True, "view": "A-A"})
    assert result.status == "ok", result.detail
    dims = [n for n in ctx.extras["natives"]["d"] if isinstance(n, su.NativeDim)]
    assert dims and {d.view for d in dims} == {"A-A"}
    assert all(r.kind == "point" for d in dims for r in d.refs)


def test_a_detail_is_a_techdraw_detail_view_of_its_circle() -> None:
    ctx = views_context()
    draw(ctx, VIEW_ITEMS["detail"])
    (view,) = [n for n in ctx.extras["natives"]["v"] if isinstance(n, su.NativeView)]
    assert view.kind == "detail" and view.name == "A (5:1)" and view.parent == "front" and view.scale == 5.0
    assert view.radius == 5.0 and view.anchor is not None
    parent = ctx.view("front")
    assert view.direction == parent.direction and view.xdir == parent.xdir  # a detail looks the way its parent does


def test_an_auxiliary_view_is_the_whole_part_looked_at_square_to_its_face() -> None:
    ctx = views_context()
    draw(ctx, VIEW_ITEMS["aux_view"])
    (view,) = [n for n in ctx.extras["natives"]["v"] if isinstance(n, su.NativeView)]
    assert view.kind == "aux" and view.name == "A"
    assert sc.dot(view.direction, (0.7071, 0.0, 0.7071)) > 0.999  # it looks at the face from outside
    rect = ctx.extras["view_rects"]["x1" if "x1" in ctx.extras["view_rects"] else "v"]
    assert view.box[0] >= rect[0] - 1 and view.box[2] <= rect[2] + 1  # the room the sheet claimed holds it


def test_the_centre_and_thread_lines_inside_a_view_are_cosmetic_edges_of_it_and_the_rest_is_covered() -> None:
    inside = model.Line(10, 10, 10, 40, model.AXES)  # the centre line of a bore in the section
    cut = model.Line(0, 0, 50, 0, model.CONTOUR)
    chain = model.Line(-45, 20, -10, 20, model.AXES)  # the cutting line in the parent view: TechDraw draws its own
    view = su.NativeView("s", "A-A", "section", "top", (0, 0, 50, 50), 1.0, (0, 0, 1), (1, 0, 0), "A", at=(0, 0, 0))
    unit = su.Unit("s", "s", su.KIND_ITEM, (inside, cut, chain), "section", None, (view,))
    (done,) = su.with_cosmetics([unit], {"top": (-40, -10, 90, 60), "A-A": view.box}, [])
    edges = [n for n in done.natives if isinstance(n, su.NativeCosmetic)]
    assert [(n.view, n.kind, n.style) for n in edges] == [("A-A", "line", "centre")]  # not on the parent: the chain line is gone
    assert done.residue == ()


def test_a_line_that_lies_inside_a_view_goes_to_that_view_and_not_to_the_one_beside_it() -> None:
    line = model.Line(10, 10, 10, 40, model.AXES)
    unit = su.Unit("h", "h", su.KIND_ITEM, (line,), "hole_dim")
    # the point is inside the section's box and within the margin of the parent's: the view that holds it wins
    (done,) = su.with_cosmetics([unit], {"top": (-60, 0, 0, 50), "A-A": (0, 0, 50, 50)}, [])
    assert [n.view for n in done.natives if isinstance(n, su.NativeCosmetic)] == ["A-A"]


def test_the_signature_of_a_view_changes_with_what_the_sheet_asks_of_it() -> None:
    a = su.NativeView("s", "A-A", "section", "top", (0.0, 0.0, 50.0, 50.0), 1.0, (0, 0, 1), (1, 0, 0), "A", at=(0, 0, 0))
    moved = su.NativeView("s", "A-A", "section", "top", (10, 0, 60, 50), 1.0, (0, 0, 1), (1, 0, 0), "A", at=(0, 0, 0))
    same = su.NativeView("s", "A-A", "section", "top", (0.0, 0.0, 50.001, 50.0), 1.0, (0, 0, 1), (1, 0, 0), "A", at=(0, 0, 0))
    sig = [su.signature(su.Unit("s", "s", su.KIND_ITEM, (), natives=(v,))) for v in (a, moved, same)]
    assert sig[0] != sig[1] and sig[0] == sig[2]


def test_the_cutting_line_of_a_section_is_cosmetic_edges_of_the_parent_and_not_techdraws_own_section_line() -> None:
    """TechDraw's own section line is switched off (FreeCAD 1.1.3 writes a damaged PDF for it): thick ends, a chain between,
    and an arrow with its letter at each end are made of the parent's cosmetic edges and of annotations."""
    ctx = two_bores()
    draw(ctx, VIEW_ITEMS["section"])
    natives = ctx.extras["natives"]["v"]
    lines = [n for n in natives if isinstance(n, su.NativeCosmetic)]
    assert {n.view for n in lines} == {"top"}  # on the parent, not on the section
    styles = sorted(n.style for n in lines)
    assert styles.count("thick") == 2 and styles.count("centre") == 1 and styles.count("thin") == 6  # 2 x (stem + two sides)
    assert [n.lines for n in natives if isinstance(n, su.NativeNote)] == [("A",), ("A",)]


def test_an_in_place_section_has_no_cutting_line_and_no_letters() -> None:
    ctx = two_bores()
    draw(ctx, {"type": "section", "id": "v", "holes": "axis", "in_place": True, "view": "top"})
    natives = ctx.extras.get("natives", {}).get("v", [])
    if natives:  # the part has an axis of revolution only in some fixtures: when it does, the plane is the part's axis
        assert not [n for n in natives if isinstance(n, su.NativeNote | su.NativeCosmetic)]
