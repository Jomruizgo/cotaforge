# ruff: noqa: E501
"""What a shop reader finds wrong on a sheet that looks finished, as measurements of the sheet itself (no FreeCAD import).

* ``dimension_off_part``: a length dimension between two faces whose end does not stand on a line of the part (a visible or a
  hidden edge across the measured direction, or a silhouette that turns back there): the extension line then touches the part
  over nothing and the reader measures a place that is not an edge. An overall length that stops short of the part is this;
* ``side_view_repeats_section``: a plain side view of a turned part whose axial section already gives the profile (the minimum views:
  the section carries every diameter and length, the side view adds only the outline again);
* ``assembly_solid_cut``: a section of an assembly that cut a solid part lengthwise (a shaft, bolt, screw, nut, washer, pin, key, rivet,
  ball, rib): those are drawn whole, as if the plane passed behind them (ISO 128-34);
* ``centre_line_gap``: two centre lines of one axis with a gap between them where a cylinder of the part stands (one continuous
  centre line per axis: the reader takes a gap for a part that is not there).

``sheet_review.review`` calls ``check`` after its own checks.
"""

from collections.abc import Sequence
from typing import Any

import sheet_solid
from sheet_collide import segments_of
from sheet_context import Context, ViewInfo
from sheet_model import CONTOUR, HIDDEN
from sheet_review import Finding, ReviewInput

Segment = tuple[float, float, float, float]

END_TOL_MM = 0.3  # an end of a dimension this near a line of the part stands on it
MIN_EDGE_MM = 0.8  # a line across the measured direction shorter than this is a point
GAP_MIN_MM = 0.5  # two centre lines further apart than this have a gap between them
SILHOUETTE_MM = 1.0  # a silhouette pair must overlap the gap by this much to be a cylinder in it
SYMMETRY_MM = 0.3  # the two silhouette lines of a cylinder lie this near equally far from its axis
AXIS_TOL_MM = 0.2  # two centre lines this near one another are on one axis


def stands_on_edge(coordinate: float, horizontal: bool, segments: Sequence[Segment]) -> bool:
    """Whether an end of a dimension at ``coordinate`` (x when the dimension line is ``horizontal``, else y) stands on a line of
    the part: a segment across the measured direction (an edge seen edge-on: a vertical one for a horizontal dimension) lying at
    that coordinate, or a place where the outline turns back there (the extreme of an arc, a silhouette that comes to that
    coordinate and goes no further) -- not a place where the outline merely runs through it."""
    m = (0, 2) if horizontal else (1, 3)
    t = (1, 3) if horizontal else (0, 2)
    left = right = touched = False
    for s in segments:
        m0, m1, t0, t1 = s[m[0]], s[m[1]], s[t[0]], s[t[1]]
        if abs(m0 - coordinate) <= END_TOL_MM and abs(m1 - coordinate) <= END_TOL_MM and abs(t1 - t0) >= MIN_EDGE_MM:
            return True
        for here, far in ((m0, m1), (m1, m0)):
            if abs(here - coordinate) > END_TOL_MM:
                continue
            touched = True
            left = left or far < coordinate - END_TOL_MM
            right = right or far > coordinate + END_TOL_MM
    return touched and not (left and right)


def _outline(ctx: Context, view: ViewInfo) -> list[Segment]:
    """The lines of the part (visible and hidden) in ``view``, in page mm: every line of the sheet on those layers (a section's
    outline is drawn there too, and the views' ink alone leaves it out)."""
    ink = [e for e in ctx.sheet.entities if getattr(e, "layer", None) in (CONTOUR, HIDDEN)]
    x0, y0, x1, y1 = view.box
    return [s for s in segments_of(ink) if x0 - 1 <= (s[0] + s[2]) / 2 <= x1 + 1 and y0 - 1 <= (s[1] + s[3]) / 2 <= y1 + 1]


def _views(ctx: Context) -> dict[str, ViewInfo]:
    return {**ctx.views, **ctx.extras.get("sections", {})}


def _off_part(data: ReviewInput) -> list[Finding]:
    ctx = data.ctx
    ends = ctx.extras.get("face_ends", [])
    if not ends:
        return []
    views = _views(ctx)
    out: list[Finding] = []
    for item, name, orient, coordinates in ends:
        view = views.get(name)
        if view is None:
            continue
        outline = _outline(ctx, view)
        if not outline:
            continue
        missing = [c for c in coordinates if not stands_on_edge(c, orient == "h", outline)]
        if not missing:
            continue
        where = ", ".join(ctx.num(round(c, 1)) for c in missing)
        text = ctx.say(
            f"La cota {item} de la vista {name} termina en {where} mm de la hoja, donde no hay ninguna arista de la pieza "
            "(ni visible ni oculta) a lo largo de lo que mide: sus líneas de extensión tocan la pieza sobre nada y el lector "
            "mide un sitio que no es un borde. Una cota entre dos caras llega a cada una de ellas: comprueba que las caras "
            "sean las que quieres (inspect action=faces) y, si es la cota total, que llegue al extremo de la vista.",
            f"Dimension {item} in the {name} view ends at {where} mm of the sheet, where the part has no edge (visible or hidden) "
            "across the measured direction: its extension lines touch the part over nothing and the reader measures a place that "
            "is not an edge. A dimension between two faces reaches each of them: check the faces are the ones you mean "
            "(inspect action=faces) and, for the overall length, that it reaches the end of the view.",
        )
        out.append(Finding("dimension_off_part", "warning", text, (item,), key=item))
    return out


def centre_gaps(lines: Sequence[tuple[str, float, float, float, float]], outlines: dict[str, list[Segment]]) -> list[tuple[str, float, float]]:
    """``(view, from, to)`` for each gap between two centre lines of one axis (``lines`` are ``(view, x1, y1, x2, y2)``, page mm)
    that has a cylinder of the part in it: a pair of silhouette lines equally far from the axis on both sides over the gap."""
    found: list[tuple[str, float, float]] = []
    for view in {v for v, *_ in lines}:
        mine = [(x1, y1, x2, y2) for v, x1, y1, x2, y2 in lines if v == view]
        for horizontal in (True, False):
            axis_lines = [
                (min(x1, x2), max(x1, x2), y1) if horizontal else (min(y1, y2), max(y1, y2), x1)
                for x1, y1, x2, y2 in mine
                if (abs(y1 - y2) < 1e-6 if horizontal else abs(x1 - x2) < 1e-6)
            ]
            axis_lines.sort(key=lambda a: (round(a[2] / AXIS_TOL_MM), a[0]))
            for first, second in zip(axis_lines, axis_lines[1:], strict=False):
                if abs(first[2] - second[2]) > AXIS_TOL_MM or second[0] - first[1] <= GAP_MIN_MM:
                    continue
                lo, hi, axis = first[1], second[0], first[2]
                if _cylinder_between(outlines.get(view, ()), horizontal, lo, hi, axis):
                    found.append((view, lo, hi))
    return found


def _cylinder_between(outline: Sequence[Segment], horizontal: bool, lo: float, hi: float, axis: float) -> bool:
    runs: list[tuple[float, float, float]] = []  # (distance of the line from the axis, side, overlap with the gap)
    for s in outline:
        a0, a1, p0, p1 = (s[0], s[2], s[1], s[3]) if horizontal else (s[1], s[3], s[0], s[2])
        if abs(p0 - p1) > 0.05:
            continue
        overlap = min(max(a0, a1), hi) - max(min(a0, a1), lo)
        if overlap < SILHOUETTE_MM or abs(p0 - axis) < 0.5:
            continue
        runs.append((abs(p0 - axis), 1.0 if p0 > axis else -1.0, overlap))
    return any(
        r[1] != q[1] and abs(r[0] - q[0]) <= SYMMETRY_MM for i, r in enumerate(runs) for q in runs[i + 1 :]
    )


def _centre(data: ReviewInput) -> list[Finding]:
    ctx = data.ctx
    records = ctx.extras.get("centre_lines", [])
    if len(records) < 2:
        return []
    lines = [(r.view, r.line.x1, r.line.y1, r.line.x2, r.line.y2) for r in records]
    views = _views(ctx)
    outlines = {name: _outline(ctx, view) for name, view in views.items()}
    out: list[Finding] = []
    for view, lo, hi in centre_gaps(lines, outlines):
        text = ctx.say(
            f"La línea de ejes de la vista {view} se corta entre {ctx.num(round(lo, 1))} y {ctx.num(round(hi, 1))} mm de la hoja "
            "donde hay un cilindro de la pieza: un eje lleva UNA línea continua de punta a punta (ISO 128-24), el hueco se lee "
            "como si ahí no hubiera pieza. Dibuja la línea del eje entera (view_lines centres=true) o añade la que falta.",
            f"The centre line of the {view} view breaks between {ctx.num(round(lo, 1))} and {ctx.num(round(hi, 1))} mm of the sheet "
            "where a cylinder of the part stands: an axis carries ONE continuous line from end to end (ISO 128-24), and the gap "
            "reads as if there were no part there. Draw the whole axis (view_lines centres=true) or add the line that is missing.",
        )
        out.append(Finding("centre_line_gap", "warning", text, (), key=f"{view}@{round(lo)}"))
    return out


def _side_view_repeats_section(data: ReviewInput) -> list[Finding]:
    ctx = data.ctx
    planes = ctx.extras.get("section_planes", {})
    if not planes or ctx.assembly is not None or ctx.weldment is not None or ctx.geometry is None:
        return []
    found = ctx.geometry.round_extent()
    if found is None:
        return []
    axis = found[0]
    facts = ctx.extras.get("facts")
    if facts is not None and (getattr(facts, "keyways", 0) or getattr(facts, "flats", None)):
        return []  # a keyway or a flat is seen from the side: the side view shows what the section does not
    if any(abs(sum(a * b for a, b in zip(h.direction, axis, strict=True))) < 0.999 for h in ctx.holes):
        return []  # a cross hole is seen from the side
    out: list[Finding] = []
    for label, (_, normal) in planes.items():
        if abs(sum(a * b for a, b in zip(normal, axis, strict=True))) > 1e-3:
            continue  # the plane does not contain the axis: not an axial section
        for name, view in ctx.views.items():
            if view.derived or not view.shows_axis_along(axis):
                continue
            if abs(abs(sum(a * b for a, b in zip(view.direction, normal, strict=True))) - 1.0) > 1e-3:
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
