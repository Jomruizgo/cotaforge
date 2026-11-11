"""The standard views of a sheet made with ``projection="hlr"`` (``worker/sheet_hlr.py``) against a canned projection of a
box: where each view lands on the page, the frames, the boxes the items read, and what an assembly sheet gets that a part
sheet does not. No FreeCAD."""

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import sheet_context as sc  # noqa: E402
import sheet_hlr as hlr  # noqa: E402
import sheet_layout as layout  # noqa: E402
import sheet_model as model  # noqa: E402

from cotaforge.contract import (  # noqa: E402
    DescribeDrawingResult,
    DrawingRequest,
    DrawingResult,
    DrawingSheetInfo,
)

SIZE = (60.0, 40.0, 20.0)  # width (x), depth (y), height (z)
CENTRE = (30.0, 20.0, 10.0)


def box_edges(w: float, h: float) -> list[model.Entity]:
    """The outline of a w x h rectangle centred on the view's origin (a part whose box centre is the origin)."""
    a, b = w / 2, h / 2
    return [
        model.Line(-a, -b, a, -b),
        model.Line(a, -b, a, b),
        model.Line(a, b, -a, b),
        model.Line(-a, b, -a, -b),
    ]


class BoxGeometry(sc.Geometry):
    """A 60 x 40 x 20 box seen from the six directions; counts the projections it makes."""

    def __init__(self) -> None:
        self.calls: list[tuple[sc.Vec3, sc.Vec3, sc.Vec3]] = []

    def view_projection(self, centre: sc.Vec3, xdir: sc.Vec3, direction: sc.Vec3) -> sc.Projection:
        self.calls.append((centre, xdir, direction))
        w, d, h = SIZE
        extent = {
            (0.0, -1.0, 0.0): (w, h),  # front: x by z
            (0.0, 0.0, 1.0): (w, d),  # top: x by y
            (1.0, 0.0, 0.0): (d, h),  # right: y by z
        }.get(tuple(round(c, 6) + 0.0 for c in direction))
        if extent is None:  # the isometric view: any off-centre outline will do
            return sc.Projection([model.Line(-10.0, -5.0, 30.0, 25.0)])
        return sc.Projection(box_edges(*extent))


def record(iso: bool = True) -> dict[str, object]:
    return hlr.record(0.5, layout.SPACING_MM, (150.0, 140.0), (300.0, 150.0) if iso else None, SIZE, CENTRE)


def test_the_record_keeps_what_places_the_views_again() -> None:
    rec = record()
    assert rec == {
        "scale": 0.5,
        "spacing": 20.0,
        "front": [150.0, 140.0],
        "iso": [300.0, 150.0],
        "size": [60.0, 40.0, 20.0],
        "centre": [30.0, 20.0, 10.0],
    }
    assert record(iso=False)["iso"] is None


def test_the_group_is_first_angle_top_below_the_front_and_right_to_its_left() -> None:
    where = hlr.origins(record())
    assert where["front"] == (150.0, 140.0)
    assert where["top"] == pytest.approx((150.0, 140.0 - ((20.0 + 40.0) / 2 * 0.5 + 20.0)))  # below: y up
    assert where["right"] == pytest.approx((150.0 - ((60.0 + 40.0) / 2 * 0.5 + 20.0), 140.0))


def test_each_standard_view_is_projected_once_in_the_frame_techdraw_gives_it() -> None:
    geometry = BoxGeometry()
    views = hlr.compose_views(geometry, record())
    frames = {(c, x, d) for c, x, d in geometry.calls}
    assert len(geometry.calls) == 4 and all(c == CENTRE for c, _, _ in geometry.calls)
    assert ((CENTRE), (1.0, 0.0, 0.0), (0.0, -1.0, 0.0)) in frames  # front
    assert (CENTRE, (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)) in frames  # top
    assert (CENTRE, (0.0, 1.0, 0.0), (1.0, 0.0, 0.0)) in frames  # right
    iso_x, iso_d = hlr.FRAMES["isometric"][1], hlr.FRAMES["isometric"][0]
    assert sc.norm(iso_x) == pytest.approx(1.0) and sc.norm(iso_d) == pytest.approx(1.0)  # unit vectors
    assert set(views.infos) == {"front", "top", "right"} and views.iso is not None


def test_the_views_boxes_are_the_part_seen_at_the_scale_on_the_page() -> None:
    views = hlr.compose_views(BoxGeometry(), record())
    front, top, right = views.infos["front"], views.infos["top"], views.infos["right"]
    assert front.box == pytest.approx((150.0 - 15.0, 140.0 - 5.0, 150.0 + 15.0, 140.0 + 5.0))  # 60 x 20 at 1:2
    assert top.box[3] - top.box[1] == pytest.approx(20.0) and top.box[2] - top.box[0] == pytest.approx(30.0)
    assert right.box[2] - right.box[0] == pytest.approx(20.0) and right.box[3] - right.box[1] == pytest.approx(10.0)
    assert top.box[3] < front.box[1] and right.box[2] < front.box[0]  # below and to the left, a spacing apart
    assert front.box[1] - top.box[3] == pytest.approx(20.0) and front.box[0] - right.box[2] == pytest.approx(20.0)
    assert (front.scale, front.centre, front.x, front.y) == (0.5, CENTRE, 150.0, 140.0)
    assert front.project(CENTRE) == pytest.approx((150.0, 140.0))  # the model point at the origin is the view's origin
    assert front.project((60.0, 0.0, 20.0)) == pytest.approx((150.0 + 15.0, 140.0 + 5.0))  # x right, z up


def test_the_isometric_view_is_centred_where_the_layout_put_it() -> None:
    iso = hlr.compose_views(BoxGeometry(), record()).iso
    assert iso is not None
    assert ((iso.box[0] + iso.box[2]) / 2, (iso.box[1] + iso.box[3]) / 2) == pytest.approx((300.0, 150.0))
    assert iso.name == "isometric"


def test_without_the_isometric_view_there_are_three() -> None:
    geometry = BoxGeometry()
    views = hlr.compose_views(geometry, record(iso=False))
    assert views.iso is None and len(geometry.calls) == 3 and len(views.rects) == 3


def test_the_entities_are_scaled_and_moved_onto_the_page() -> None:
    views = hlr.compose_views(BoxGeometry(), record(iso=False))
    xs = [x for e in views.entities if isinstance(e, model.Line) for x in (e.x1, e.x2)]
    assert min(xs) == pytest.approx(150.0 - ((60.0 + 40.0) / 2 * 0.5 + 20.0) - 10.0)  # the right view's left edge
    assert all(e.layer == model.CONTOUR for e in views.entities)


def test_a_view_that_shows_nothing_is_an_item_error_naming_it() -> None:
    class Empty(sc.Geometry):
        def view_projection(self, centre: sc.Vec3, xdir: sc.Vec3, direction: sc.Vec3) -> sc.Projection:
            return sc.Projection([])

    with pytest.raises(sc.ItemError, match="front view"):
        hlr.compose_views(Empty(), record())


def make_context() -> sc.Context:
    return sc.Context(
        sheet=model.Sheet(420.0, 297.0, "t"), views={}, holes=[], geometry=BoxGeometry(), area=(0, 0, 1, 1)
    )


def test_apply_gives_the_context_the_views_and_an_assembly_sheet_the_isometric_one_too() -> None:
    views = hlr.compose_views(BoxGeometry(), record())
    part = make_context()
    hlr.apply(part, views, assembly=False)
    assert set(part.views) == {"front", "top", "right"} and len(part.extras["view_boxes"]) == 4
    assert "views_extra" not in part.extras and "iso_box" not in part.extras  # a part sheet does not balloon the iso
    asm = make_context()
    hlr.apply(asm, views, assembly=True)
    assert asm.extras["views_extra"]["isometric"] is views.iso and asm.extras["iso_box"] == views.iso.box
    assert asm.any_view("isometric").name == "isometric"


# --- the option in the contract ---------------------------------------------------------------------------------------


def test_the_default_projection_is_freecads_own() -> None:
    assert DrawingRequest(operation_id="d").projection == "techdraw"
    assert DrawingRequest(operation_id="d", projection="hlr").projection == "hlr"
    assert DrawingResult(sheet="Drawing").projection == "techdraw"
    sheet = DrawingSheetInfo(sheet="D", template="A4", language="es", page_mm=(297.0, 210.0), scale=1.0)
    assert sheet.projection == "techdraw" and DescribeDrawingResult(sheets=(sheet,)).sheets[0].projection == "techdraw"


def test_any_other_projection_is_refused() -> None:
    with pytest.raises(ValidationError):
        DrawingRequest(operation_id="d", projection="fast")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        DrawingResult(sheet="D", projection="svg")  # type: ignore[arg-type]
