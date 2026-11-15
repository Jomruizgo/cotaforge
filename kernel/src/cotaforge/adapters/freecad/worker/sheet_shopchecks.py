# ruff: noqa: E501
"""What a shop reader finds wrong on a sheet that looks finished (no FreeCAD import).

* ``dimension_off_part`` (measured, holds the export): a length dimension between two faces whose end does not stand on a line of
  the part. The ends are the ones TechDraw holds on the page (``sheet_pageread.dimension_ends``: the measured points of the page
  dimension) and the lines are the ones TechDraw drew for that view (its visible and hidden edges); a sheet read without its page
  (a headless FreeCAD computes no dimension lines) is judged on the composed sheet instead. An end stands on the part when a line
  across the measured direction (an edge seen edge-on, however short) or a place where the outline turns back lies at its
  coordinate, within the trace of the face it measures (``face_ends``), not anywhere in the view;
* ``centre_line_gap`` (holds the export): a cylinder of the part (``PartFacts.bosses``, read from the B-rep) seen from the side
  whose axis carries a centre line over only part of its length. The centre lines are the ones of the composed sheet;
* ``side_view_repeats_section`` (a notice, never holds the export): a plain side view of a turned part whose axial section already
  gives the profile, when nothing of the part is seen only from the side;
* ``assembly_solid_cut`` (a notice, never holds the export): a section of an assembly whose plane cut a solid part (a shaft, bolt,
  pin, key...). Which way the plane cut it (lengthwise or across) is NOT measured here, so it cannot hold an export.

``sheet_review.review`` calls ``check`` after its own checks.
"""

import math
from collections.abc import Sequence
from typing import Any

import sheet_section
import sheet_seen
import sheet_solid
from sheet_collide import segments_of
from sheet_context import Context, ViewInfo
from sheet_model import AXES, CONTOUR, HIDDEN, Line
from sheet_review import Finding, ReviewInput

Point = tuple[float, float]
Segment = tuple[float, float, float, float]

END_TOL_MM = 0.3  # an end of a dimension this near a line of the part stands on it (page mm; never more than 0.3 mm of the part: see ``end_tolerance``)
POINT_MM = 1e-3  # a line shorter than this is a point
NEAR_MM = 2.0  # with no trace of the measured face known, the line must pass this near the end of the dimension, across it
TRACE_TOL_MM = 0.3  # the trace of the measured face is taken this much wider than the face
GAP_MIN_MM = 0.5  # a stretch of an axis without centre line longer than this is a gap
AXIS_TOL_MM = 0.2  # a centre line this near the axis of a cylinder lies on it
VIEW_MARGIN_MM = 5.0  # a centre line belongs to a view when its middle is this near the view's box


def end_tolerance(scale: float) -> float:
    """How near (page mm) an end of a dimension must be to a line of the part in a view at ``scale``: ``END_TOL_MM`` on the paper,
    and at a reduction scale no more than that much of the PART (at 1:5 a dimension 1 mm short of a shoulder is 0.2 mm short on
    the paper: it is off the part all the same)."""
    return END_TOL_MM * min(1.0, scale if scale > 0 else 1.0)


def stands_on_edge(
    coordinate: float,
    horizontal: bool,
    segments: Sequence[Segment],
    window: tuple[float, float] | None = None,
    tol: float = END_TOL_MM,
) -> bool:
    """Whether an end of a dimension at ``coordinate`` (x when the dimension line is ``horizontal``, else y) stands on a line of
    the part: a segment across the measured direction (an edge seen edge-on: a vertical one for a horizontal dimension, of any
    length: a shoulder of 0.2 mm is an edge) lying at that coordinate, or a place where the outline turns back there (the extreme
    of an arc, a silhouette that comes to that coordinate and goes no further) -- not a place where the outline merely runs through
    it. ``window``: the stretch ACROSS the measured direction where the line must be (the trace of the measured face, or the
    neighbourhood of the dimension's end): a line at the same coordinate elsewhere in the view is another feature."""
    m = (0, 2) if horizontal else (1, 3)
    t = (1, 3) if horizontal else (0, 2)
    left = right = touched = False
    for s in segments:
        m0, m1, t0, t1 = s[m[0]], s[m[1]], s[t[0]], s[t[1]]
        lo, hi = min(t0, t1), max(t0, t1)
        if window is not None and (hi < window[0] or lo > window[1]):
            continue
        if abs(m0 - coordinate) <= tol and abs(m1 - coordinate) <= tol:
            if hi - lo > POINT_MM and hi - lo >= abs(m1 - m0):
                return True
            continue
        for here, far, there in ((m0, m1, t0), (m1, m0, t1)):
            if abs(here - coordinate) > tol or (window is not None and not window[0] <= there <= window[1]):
                continue
            touched = True
            left = left or far < coordinate - tol
            right = right or far > coordinate + tol
    return touched and not (left and right)


def _outline(ctx: Context, view: ViewInfo) -> list[Segment]:
    """The lines of the part (visible and hidden) in ``view`` on the COMPOSED sheet, in page mm: every line of the sheet on those
    layers (a section's outline is drawn there too, and the views' ink alone leaves it out)."""
    ink = [e for e in ctx.sheet.entities if getattr(e, "layer", None) in (CONTOUR, HIDDEN)]
    x0, y0, x1, y1 = view.box
    return [s for s in segments_of(ink) if x0 - 1 <= (s[0] + s[2]) / 2 <= x1 + 1 and y0 - 1 <= (s[1] + s[3]) / 2 <= y1 + 1]


def _views(ctx: Context) -> dict[str, ViewInfo]:
    return {**ctx.views, **ctx.extras.get("sections", {})}


def _window(
    p: Point, horizontal: bool, points: Sequence[Point], traces: Sequence[tuple[float, float] | None], tol: float
) -> tuple[float, float]:
    """Where, across the measured direction, the line of the part must be for the end ``p``: the trace of the face that end
    measures (the recorded end at the same coordinate), else the neighbourhood of the end itself."""
    along, across = (0, 1) if horizontal else (1, 0)
    for recorded, trace in zip(points, traces, strict=False):
        if trace is not None and abs(recorded[along] - p[along]) <= max(tol, TRACE_TOL_MM):
            return min(trace[0], p[across]) - TRACE_TOL_MM, max(trace[1], p[across]) + TRACE_TOL_MM
    return p[across] - NEAR_MM, p[across] + NEAR_MM


def _off_part(data: ReviewInput) -> list[Finding]:
    """``face_ends`` (``items_dims._distance``): ``(item, view, "h" | "v", the two ends on the composed sheet, the trace of each
    measured face across the dimension or None)``. ``page_dim_ends`` / ``page_outlines`` (``drawing.read_page_dimensions``): the
    same dimension and the lines of its view as TechDraw holds them."""
    ctx = data.ctx
    records = ctx.extras.get("face_ends", [])
    if not records:
        return []
    views = _views(ctx)
    on_page: dict[str, list[Any]] = {}
    for ends in ctx.extras.get("page_dim_ends", []):
        on_page.setdefault(ends.owner, []).append(ends)
    page_outlines: dict[str, list[Segment]] = ctx.extras.get("page_outlines", {})
    measured_on: dict[str, str] = ctx.extras.setdefault("off_part_measured_on", {})  # item -> "page" | "sheet" (a diagnosis reads it)
    out: list[Finding] = []
    for item, name, orient, points, traces in records:
        judged: list[tuple[bool, float, Sequence[Segment], Sequence[Point]]] = [
            (ends.horizontal, ends.scale, page_outlines[ends.view], ends.points)
            for ends in on_page.get(item, [])
            if page_outlines.get(ends.view)
        ]
        measured_on[item] = "page"
        if not judged:  # the page holds no dimension of this item (headless, or a symbol stands for it): the composed sheet
            view = views.get(name)
            outline = _outline(ctx, view) if view is not None else []
            if view is None or not outline:
                measured_on[item] = "nothing"
                continue
            judged = [(orient == "h", view.scale, outline, points)]
            measured_on[item] = "sheet"
        missing: list[Point] = []
        for horizontal, scale, outline, ends_at in judged:
            tol = end_tolerance(scale)
            for p in ends_at:
                window = _window(p, horizontal, points, traces, tol)
                if not stands_on_edge(p[0] if horizontal else p[1], horizontal, outline, window, tol):
                    missing.append(p)
        if not missing:
            continue
        where = ", ".join(f"({ctx.num(round(p[0], 1))}; {ctx.num(round(p[1], 1))})" for p in missing)
        text = ctx.say(
            f"La cota {item} de la vista {name} termina en {where} mm de la hoja, donde la vista no tiene ninguna arista de la pieza "
            "(ni visible ni oculta) atravesada a lo que mide: su línea de extensión sale de un sitio que no es un borde y el lector "
            "no sabe qué mide. Una cota entre dos caras llega a cada una de ellas: comprueba que las caras sean las que quieres "
            "(inspect action=faces), que se vean en esa vista (una cara interior se acota en el corte) y, si es la cota total, que "
            "llegue al extremo de la vista.",
            f"Dimension {item} in the {name} view ends at {where} mm of the sheet, where the view has no edge of the part (visible or "
            "hidden) across the measured direction: its extension line starts from a place that is not an edge and the reader cannot "
            "tell what it measures. A dimension between two faces reaches each of them: check the faces are the ones you mean "
            "(inspect action=faces), that they show in that view (an inside face is dimensioned in the section) and, for the overall "
            "length, that it reaches the end of the view.",
        )
        out.append(Finding("dimension_off_part", "warning", text, (item,), key=item))
    return out


def axis_gaps(a: Point, b: Point, lines: Sequence[Segment]) -> tuple[list[tuple[float, float]], bool]:
    """The stretches of the axis from ``a`` to ``b`` (page mm) that no centre line of ``lines`` covers, as distances from ``a``,
    each longer than ``GAP_MIN_MM``; and whether any of the lines lies on that axis at all."""
    length = math.dist(a, b)
    if length < POINT_MM:
        return [], False
    ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
    covered: list[tuple[float, float]] = []
    on_axis = False
    for x1, y1, x2, y2 in lines:
        if any(abs(-(px - a[0]) * uy + (py - a[1]) * ux) > AXIS_TOL_MM for px, py in ((x1, y1), (x2, y2))):
            continue
        on_axis = True
        t0, t1 = sorted(((x1 - a[0]) * ux + (y1 - a[1]) * uy, (x2 - a[0]) * ux + (y2 - a[1]) * uy))
        if t1 > 0.0 and t0 < length:
            covered.append((max(t0, 0.0), min(t1, length)))
    gaps: list[tuple[float, float]] = []
    reached = 0.0
    for t0, t1 in sorted(covered):
        if t0 - reached > GAP_MIN_MM:
            gaps.append((reached, t0))
        reached = max(reached, t1)
    if length - reached > GAP_MIN_MM:
        gaps.append((reached, length))
    return gaps, on_axis


def _centre(data: ReviewInput) -> list[Finding]:
    """A cylinder of the part seen from the side carries its centre line from end to end. The cylinders are the part's
    (``PartFacts.bosses``: whole convex cylinders of the B-rep, one per cylinder), never pairs of lines of the drawing: a
    rectangular bar with a hole in each end has two centre lines and no cylinder between them. A cylinder whose axis carries no
    line at all in the view is ``cylinder_no_axis``'s (``sheet_checks``); this one is the line that is there and stops short."""
    ctx = data.ctx
    facts = ctx.extras.get("facts")
    bosses = list(getattr(facts, "bosses", None) or [])
    drawn = [(e.x1, e.y1, e.x2, e.y2) for e in data.sheet.entities if isinstance(e, Line) and e.layer == AXES]
    if not bosses or not drawn:
        return []
    planes = ctx.extras.get("section_planes", {})
    shown: list[tuple[str, ViewInfo, bool]] = [(name, view, False) for name, view in ctx.views.items()]
    shown += [(label, view, True) for label, view in ctx.extras.get("sections", {}).items()]
    stands_for = ctx.extras.get("in_place_views", {})  # a section in place of a view: the view itself is not drawn
    out: list[Finding] = []
    for boss in bosses:
        if boss.bore or boss.length <= 0:
            continue
        half = boss.length / 2
        ends = [tuple(p + u * half * sign for p, u in zip(boss.axis_point, boss.axis_dir, strict=True)) for sign in (-1.0, 1.0)]
        seen: set[tuple[float, float, float, float]] = set()
        for name, view, section in shown:
            if section:
                plane = planes.get(name)
                if plane is None or not sheet_section.in_plane(plane[0], plane[1], boss.axis_point, boss.axis_dir):
                    continue
            elif name in stands_for.values() or not view.shows_axis_along(boss.axis_dir):
                continue
            elif getattr(facts, "assembly", False) and not sheet_seen.side_on_seen(ctx, view, boss):
                continue  # a cylinder behind another part shows no sides
            a, b = view.project(ends[0]), view.project(ends[1])  # type: ignore[arg-type]
            place = (round(a[0], 1), round(a[1], 1), round(b[0], 1), round(b[1], 1))
            if place in seen:
                continue
            seen.add(place)
            x0, y0, x1, y1 = view.box
            mine = [
                s
                for s in drawn
                if x0 - VIEW_MARGIN_MM <= (s[0] + s[2]) / 2 <= x1 + VIEW_MARGIN_MM
                and y0 - VIEW_MARGIN_MM <= (s[1] + s[3]) / 2 <= y1 + VIEW_MARGIN_MM
            ]
            gaps, on_axis = axis_gaps(a, b, mine)
            if not gaps or not on_axis:
                continue
            length = math.dist(a, b)
            lo, hi = gaps[0]
            p = (a[0] + (b[0] - a[0]) * lo / length, a[1] + (b[1] - a[1]) * lo / length)
            q = (a[0] + (b[0] - a[0]) * hi / length, a[1] + (b[1] - a[1]) * hi / length)
            span = f"({ctx.num(round(p[0], 1))}; {ctx.num(round(p[1], 1))}) - ({ctx.num(round(q[0], 1))}; {ctx.num(round(q[1], 1))})"
            text = ctx.say(
                f"La línea de ejes de la vista {name} no recorre el cilindro Ø{ctx.num(2 * boss.radius)} ({boss.face_id}): falta entre "
                f"{span} mm de la hoja. Un eje lleva UNA línea continua de punta a punta (ISO 128-24); el hueco se lee como si ahí "
                "no hubiera pieza. Dibuja la línea del eje entera (view_lines centres=true) o añade la que falta.",
                f"The centre line of the {name} view does not run the length of the Ø{ctx.num(2 * boss.radius)} cylinder ({boss.face_id}): "
                f"it is missing between {span} mm of the sheet. An axis carries ONE continuous line from end to end (ISO 128-24); the "
                "gap reads as if there were no part there. Draw the whole axis (view_lines centres=true) or add the line that is missing.",
            )
            out.append(Finding("centre_line_gap", "warning", text, (), key=f"{boss.face_id}@{name}"))
    return out


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def side_features(ctx: Context, axis: Sequence[float]) -> str | None:
    """What of a turned part about ``axis`` is seen only from the side (so its plain side view shows what the axial section does
    not): a keyway, a flat, a hole or a cylinder across the axis, a milled face (a plane that is not square to the axis: the wall
    of a slot off the axis, a pocket). ``None`` when the part has none."""
    facts = ctx.extras.get("facts")
    if getattr(facts, "keyways", 0):
        return "keyway"
    if getattr(facts, "flats", None):
        return "flat"
    if any(abs(_dot(h.direction, axis)) < 0.999 for h in ctx.holes):
        return "cross hole"
    if any(abs(_dot(c.axis, axis)) < 0.999 for c in getattr(facts, "cylinders", None) or []):
        return "cylinder across the axis"
    if any(abs(_dot(p.normal, axis)) < 0.999 for p in getattr(facts, "planes", None) or []):
        return "milled face"
    return None


def _side_view_repeats_section(data: ReviewInput) -> list[Finding]:
    ctx = data.ctx
    planes = ctx.extras.get("section_planes", {})
    if not planes or ctx.assembly is not None or ctx.weldment is not None or ctx.geometry is None:
        return []
    found = ctx.geometry.round_extent()
    if found is None:
        return []
    axis = found[0]
    if side_features(ctx, axis) is not None:
        return []  # the side view shows what the section does not
    stands_for = ctx.extras.get("in_place_views", {})  # a section drawn in place of a view: that view is not on the sheet
    out: list[Finding] = []
    for label, (_, normal) in planes.items():
        if label in stands_for or abs(_dot(normal, axis)) > 1e-3:
            continue  # the section IS the side view (drawn in its place), or its plane does not contain the axis
        for name, view in ctx.views.items():
            if view.derived or name in stands_for.values() or not view.shows_axis_along(axis):
                continue
            if abs(abs(_dot(view.direction, normal)) - 1.0) > 1e-3:
                continue  # it does not look the way the section looks
            text = ctx.say(
                f"La vista {name} es la vista lateral lisa de una pieza torneada y el corte axial {label} ya da su perfil, todos sus Ø y "
                f"todas sus longitudes: la vista {name} solo repite el contorno (mínimo de vistas). Quítala (drawing action=remove_view, "
                f"view={name}) y pasa sus cotas y sus símbolos de rugosidad al corte {label} (view={label}).",
                f"The {name} view is the plain side view of a turned part and the axial section {label} already gives its profile, every "
                f"Ø and every length: the {name} view only repeats the outline (the minimum views). Drop it (drawing action=remove_view, "
                f"view={name}) and move its dimensions and finish symbols to the section {label} (view={label}).",
            )
            out.append(Finding("side_view_repeats_section", "warning", text, (), key=f"{name}:{label}"))
    return out


def _solid_cuts(data: ReviewInput) -> list[Finding]:
    ctx = data.ctx
    out: list[Finding] = []
    for label, cut in ctx.extras.get("section_cuts", {}).items():
        found = sheet_solid.cut_solids(list(cut))
        if not found:
            continue
        names = ", ".join(f"{name} ({kind})" for name, kind in found)
        # which way the plane cut each part is not recorded (``items_views._record_cuts``): a notice, never a hold
        text = ctx.say(
            f"La sección {label} corta a lo largo de su eje piezas macizas: {names}. En un corte de conjunto los ejes, tornillos, "
            "tuercas, arandelas, pasadores, chavetas, remaches, bolas y nervios cortados a lo largo NO se cortan: se dibujan enteros, "
            "en su contorno, como si el plano pasara por detrás (ISO 128-34). Un eje solo se corta si el corte existe para enseñar "
            "algo de su interior, y entonces con un corte local (parcial), no con el plano de toda la sección.",
            f"Section {label} cuts solid parts lengthwise: {names}. In a section of an assembly shafts, bolts, screws, nuts, washers, "
            "pins, keys, rivets, balls and ribs cut lengthwise are NOT cut: they are drawn whole, in their outline, as if the plane "
            "passed behind them (ISO 128-34). A shaft is cut only when the section exists to show something inside it, and then with "
            "a local (broken-out) section, never with the plane of the whole one.",
        )
        out.append(Finding("assembly_solid_cut", "warning", text, (), key=label))
    return out


def check(data: ReviewInput) -> list[Finding]:
    found: list[Any] = []
    for one in (_off_part, _centre, _side_view_repeats_section, _solid_cuts):
        found += one(data)
    return found
