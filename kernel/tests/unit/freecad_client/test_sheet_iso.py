# ruff: noqa: E501
"""The colour of the isometric view, without FreeCAD: the shaded picture is made of filled polygons placed with the view's
own projection, far to near, lit the same way on every sheet; it goes under the lines in the SVG (so in the PDF and the PNG)
and nowhere else (no DXF, nothing an item can collide with)."""

import math
import re
import sys
import time
from pathlib import Path

import pytest

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import sheet_context as sc  # noqa: E402
import sheet_dxf  # noqa: E402
import sheet_iso  # noqa: E402
import sheet_layout  # noqa: E402
import sheet_model as model  # noqa: E402
import sheet_svg  # noqa: E402

RED = (255, 128, 128)


def iso_view(scale: float = 1.0) -> sc.ViewInfo:
    return sc.ViewInfo(
        "isometric",
        200.0,
        150.0,
        scale,
        sheet_layout.ISO_DIRECTION,
        sheet_layout.ISO_XDIRECTION,
        (5.0, 5.0, 5.0),
        (150.0, 100.0, 250.0, 200.0),
    )


def quad(p0, p1, p2, p3):  # noqa: ANN001, ANN201
    return [(p0, p1, p2), (p0, p2, p3)]


def cube(size: float = 10.0, top_colour=None):  # noqa: ANN001, ANN201
    s = size
    faces = {
        "bottom": quad((0, 0, 0), (s, 0, 0), (s, s, 0), (0, s, 0)),
        "top": quad((0, 0, s), (s, 0, s), (s, s, s), (0, s, s)),
        "front": quad((0, 0, 0), (s, 0, 0), (s, 0, s), (0, 0, s)),
        "back": quad((0, s, 0), (s, s, 0), (s, s, s), (0, s, s)),
        "left": quad((0, 0, 0), (0, s, 0), (0, s, s), (0, 0, s)),
        "right": quad((s, 0, 0), (s, s, 0), (s, s, s), (s, 0, s)),
    }
    return [(top_colour if name == "top" else None, tris) for name, tris in faces.items()]


def test_the_triangles_land_where_the_view_puts_the_points() -> None:
    view = iso_view(scale=2.0)
    solids = sheet_iso.shade_meshes(cube(), view)
    points = {p for s in solids for p in s.points}
    for corner in [(0, 0, 0), (10, 10, 10), (10, 0, 0)]:
        x, y = view.project(corner)
        assert any(abs(px - x) < 1e-9 and abs(py - y) < 1e-9 for px, py in points)


def test_the_far_triangles_come_first_and_every_one_is_a_filled_polygon_on_the_shade_layer() -> None:
    view = iso_view()
    solids = sheet_iso.shade_meshes(cube(), view)
    assert solids and all(isinstance(s, model.Solid) and s.layer == model.ISO_SHADE and s.fill for s in solids)
    # two triangles on the same place of the page, the red one nearer the viewer: it is painted last whatever the order given
    toward = sheet_iso._unit(view.direction)  # noqa: SLF001
    far = ((0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (0.0, 10.0, 0.0))
    near = tuple(tuple(c + 5 * t for c, t in zip(p, toward, strict=True)) for p in far)
    ordered = sheet_iso.shade_meshes([(RED, [near]), (None, [far])], view)
    assert len(ordered) == 2 and ordered[-1].fill[0] > ordered[-1].fill[1] + 40  # type: ignore[index]
    assert abs(ordered[0].fill[0] - ordered[0].fill[1]) < 3  # type: ignore[index]


def test_a_small_triangle_behind_a_big_one_is_painted_first_even_when_its_centre_is_nearer() -> None:
    """A face with a hole is a few big triangles; the wall of the hole is many small ones under it. Sorting by the depth
    of the centre of each puts the wall over the face when the wall sits under a corner of a big triangle."""
    view = iso_view()
    toward = sheet_iso._unit(view.direction)  # noqa: SLF001
    big = ((0.0, 0.0, 0.0), (100.0, 0.0, 0.0), (0.0, 100.0, 0.0))
    corner = max(big, key=lambda p: sum(c * t for c, t in zip(p, toward, strict=True)))
    inside = tuple(c + 0.1 * (g - c) for c, g in zip(corner, (30.0, 30.0, 0.0), strict=True))
    behind = tuple(
        tuple(i + 3 * (-t) + d for i, t, d in zip(inside, toward, delta, strict=True))
        for delta in ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0))
    )
    centre_depth = sum(sum(c * t for c, t in zip(p, toward, strict=True)) for p in big) / 3
    assert sum(c * t for c, t in zip(behind[0], toward, strict=True)) > centre_depth  # the old sort got this wrong
    ordered = sheet_iso.shade_meshes([(RED, [behind]), (None, [big])], view)
    assert ordered[0].fill[0] > ordered[0].fill[1] + 40  # type: ignore[index]  # the red one under the grey one
    assert abs(ordered[-1].fill[0] - ordered[-1].fill[1]) < 3  # type: ignore[index]


def test_the_colour_of_a_hole_face_shows_and_the_rest_is_neutral_grey() -> None:
    solids = sheet_iso.shade_meshes(cube(top_colour=RED), iso_view())
    reds = [s for s in solids if s.fill[0] > s.fill[1] + 40]  # type: ignore[index]
    greys = [s for s in solids if abs(s.fill[0] - s.fill[1]) < 3]  # type: ignore[index]
    assert reds and greys
    # the coloured face is the top one: only its two triangles are red
    assert len(reds) == 2


def test_lighting_is_fixed_in_the_view_and_two_sided() -> None:
    view = iso_view()
    light = sheet_iso.light_of(view)
    assert sum(c * c for c in light) == pytest.approx(1.0)
    facing = sheet_iso.lit((200, 200, 200), light, light)
    turned = sheet_iso.lit((200, 200, 200), tuple(-c for c in light), light)  # type: ignore[arg-type]
    away = sheet_iso.lit((200, 200, 200), (light[1], -light[0], 0.0), light)
    assert facing == turned == (200, 200, 200)
    assert away[0] == round(200 * sheet_iso.AMBIENT) or away[0] > round(200 * sheet_iso.AMBIENT) - 1
    assert away[0] < facing[0]


def test_an_edge_on_triangle_is_left_out() -> None:
    view = iso_view()
    flat = [((0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (20.0, 0.0, 0.0))]  # collinear: no area on the page
    assert sheet_iso.shade_meshes([(None, flat)], view) == []


def test_a_part_too_heavy_keeps_its_line_drawing() -> None:
    triangle = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0))
    with pytest.raises(ValueError, match="triangles"):
        sheet_iso.shade_meshes([(None, [triangle] * (sheet_iso.MAX_TRIANGLES + 1))], iso_view())


def test_the_picture_is_under_the_lines_in_the_svg_and_not_in_the_dxf() -> None:
    sheet = model.Sheet(420.0, 297.0, "t")
    sheet.add(model.Line(10.0, 10.0, 50.0, 50.0, model.CONTOUR))
    sheet.underlay = list(sheet_iso.shade_meshes(cube(top_colour=RED), iso_view()))
    svg = sheet_svg.to_svg(sheet)
    under, ink = svg.index('id="layer-underlay"'), svg.index(f'id="layer-{model.CONTOUR}"')
    assert under < ink  # painted first: the lines of the view are over it
    assert re.search(r'<path d="M [^"]+" fill="#[0-9a-f]{6}" stroke="#([0-9a-f]{6})"', svg)
    assert b"ISO_COLOR" not in sheet_dxf.to_dxf(sheet)  # the DXF is the line drawing


def test_a_solid_without_fill_prints_in_ink_as_before() -> None:
    sheet = model.Sheet(100.0, 100.0)
    sheet.add(model.Solid(((0.0, 0.0), (5.0, 0.0), (5.0, 5.0)), model.DIMENSIONS))
    assert 'fill="#000000" stroke="none"' in sheet_svg.to_svg(sheet)
    assert sheet.underlay == []


def uv_sphere(triangles: int, radius: float = 20.0):  # noqa: ANN201
    m = max(4, int((triangles / 4) ** 0.5))
    nl, nm = m, 2 * m

    def pt(i: int, j: int) -> tuple[float, float, float]:
        a, b = math.pi * i / nl, 2 * math.pi * j / nm
        return (radius * math.sin(a) * math.cos(b), radius * math.sin(a) * math.sin(b), radius * math.cos(a))

    out = []
    for i in range(nl):
        for j in range(nm):
            a, b, c, d = pt(i, j), pt(i + 1, j), pt(i + 1, j + 1), pt(i, j + 1)
            out += [(a, b, c), (a, c, d)]
    return [(None, out)]


def holed_plate(holes: int = 6, seg: int = 24, w: float = 120.0, h: float = 60.0, t: float = 10.0, r: float = 4.0):  # noqa: ANN201
    """A plate whose top and bottom faces are wedges from the ring of each hole to its cell corner, and the red walls."""
    cols = 3
    top, bottom, walls = [], [], []
    for k in range(holes):
        cx, cy = w / cols * (k % cols + 0.5), h / 2 * (k // cols + 0.5)
        ring = [
            (cx + r * math.cos(2 * math.pi * s / seg), cy + r * math.sin(2 * math.pi * s / seg)) for s in range(seg)
        ]
        corners = [
            (cx - w / cols / 2, cy - h / 4),
            (cx + w / cols / 2, cy - h / 4),
            (cx + w / cols / 2, cy + h / 4),
            (cx - w / cols / 2, cy + h / 4),
        ]
        for s in range(seg):
            (x0, y0), (x1, y1) = ring[s], ring[(s + 1) % seg]
            walls += [((x0, y0, 0), (x1, y1, 0), (x1, y1, t)), ((x0, y0, 0), (x1, y1, t), (x0, y0, t))]
            corner = corners[
                int(((math.atan2((y0 + y1) / 2 - cy, (x0 + x1) / 2 - cx) + math.pi) / (math.pi / 2) + 2) % 4)
            ]
            top.append(((x0, y0, t), (x1, y1, t), (*corner, t)))
            bottom.append(((x0, y0, 0), (x1, y1, 0), (*corner, 0)))
    return [(None, top), (None, bottom), (RED, walls)]


def test_a_curved_part_does_not_make_the_worker_wait() -> None:
    """On a sphere every triangle has all the others on one side: splitting on triangle planes was quadratic (about 7 s for
    2000 triangles, 155 s for 8000)."""
    start = time.monotonic()
    solids = sheet_iso.shade_meshes(uv_sphere(1900), iso_view())
    assert solids
    assert time.monotonic() - start < 2.5


def test_the_order_is_still_exact_on_a_part_with_curves_and_holes() -> None:
    """Ray by ray, the polygon painted last where a ray goes through is the nearest one (the plate is big enough for the
    balanced splitting to work, with the walls of the holes under the big triangles of the faces)."""
    view = iso_view()
    meshes = [*holed_plate(), *uv_sphere(400, 8.0)]
    toward = sheet_iso._unit(view.direction)  # noqa: SLF001
    polygons = [(tri, (1, 2, 3)) for _, triangles in meshes for tri in triangles]
    ordered = sheet_iso._back_to_front(sheet_iso._build(polygons), toward)  # noqa: SLF001
    assert len(ordered) >= len(polygons)

    def depth_at(poly, x: float, y: float):  # noqa: ANN001, ANN202
        pts = [view.project(p) for p in poly[0]]
        for k in range(1, len(pts) - 1):
            (ax, ay), (bx, by), (cx, cy) = pts[0], pts[k], pts[k + 1]
            d = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
            if abs(d) < 1e-12:
                continue
            u = ((x - ax) * (cy - ay) - (y - ay) * (cx - ax)) / d
            v = ((bx - ax) * (y - ay) - (by - ay) * (x - ax)) / d
            if u > 1e-6 and v > 1e-6 and u + v < 1 - 1e-6:
                p0, p1, p2 = poly[0][0], poly[0][k], poly[0][k + 1]
                return sum(w * sheet_iso._dot(p, toward) for w, p in ((1 - u - v, p0), (u, p1), (v, p2)))  # noqa: SLF001
        return None

    wrong = samples = 0
    shown = [view.project(p) for poly in ordered for p in poly[0]]
    x0, x1 = min(q[0] for q in shown), max(q[0] for q in shown)
    y0, y1 = min(q[1] for q in shown), max(q[1] for q in shown)
    for gx in range(40):
        for gy in range(40):
            x, y = x0 + (x1 - x0) * (gx + 0.37) / 40, y0 + (y1 - y0) * (gy + 0.21) / 40
            depths = [d for poly in ordered if (d := depth_at(poly, x, y)) is not None]
            if len(depths) < 2:
                continue
            samples += 1
            if depths[-1] < max(depths) - 1e-6:  # the last painted is not the nearest
                wrong += 1
    assert samples > 100
    assert wrong == 0


def test_past_its_time_budget_the_order_is_by_depth_and_the_reply_is_told() -> None:
    notes: list[tuple[str, str]] = []
    solids = sheet_iso.shade_meshes(uv_sphere(400), iso_view(), notes, budget=-1.0)
    assert solids
    assert len(notes) == 1 and "approximate" in notes[0][1] and "aproximado" in notes[0][0]
    quiet: list[tuple[str, str]] = []
    sheet_iso.shade_meshes(cube(), iso_view(), quiet)
    assert quiet == []
