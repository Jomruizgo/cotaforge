# ruff: noqa: E501
"""ISO 6410 thread representation (no FreeCAD import): a tapped hole seen along its axis is drawn with its tap drill as
a thick circle (TechDraw draws that one) and the major diameter of the thread as a thin arc of about three quarters of a
circle. The arc belongs to the geometry, not to a callout, so it is drawn for every hole a ``hole`` operation declared
threaded, whether or not a ``hole_dim`` item names it.
"""

import importlib.util
import math
import re
import sys
from pathlib import Path
from typing import Any

from sheet_context import Context, ThreadInfo, dot, parallel, sub, unit
from sheet_model import CONTOUR, THREAD, Arc, Entity, Line

_SIZE = re.compile(r"^M(\d+(?:\.\d+)?)")


def _load_table() -> object:
    """The thread table of the contract (``cotaforge/contract/threads.py``), loaded by path: the worker runs in FreeCAD's
    own Python, where the contract package (pydantic) cannot be imported, and the file is standard library only. One
    table for the plan, the hole and the sheet."""
    path = Path(__file__).resolve().parents[3] / "contract" / "threads.py"
    name = "cotaforge_thread_table"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # a dataclass looks its own module up while it is created
    spec.loader.exec_module(module)
    return module


def thread_table(size: str) -> tuple[str, float] | None:
    """``(designation, tap drill)`` of an ISO metric size written the way a drawing or an engineer writes it (``M45x1,5``,
    ``M12``), or ``None`` when the table of the contract does not have it."""
    row = _load_table().thread(size)  # type: ignore[attr-defined]
    return None if row is None else (row.designation, row.tap_drill_mm)


# the quarter left open is the upper right one (0 to 90 degrees), so the arc runs counter-clockwise from 90 to 360
OPEN_FROM_DEG = 90.0
OPEN_TO_DEG = 360.0


# ISO 262 coarse metric threads: size -> tap drill diameter (mm), the hole a tap goes into (ISO 2306)
TAP_DRILLS: dict[str, float] = {
    "M2": 1.6,
    "M2.5": 2.05,
    "M3": 2.5,
    "M4": 3.3,
    "M5": 4.2,
    "M6": 5.0,
    "M8": 6.8,
    "M10": 8.5,
    "M12": 10.2,
    "M14": 12.0,
    "M16": 14.0,
    "M20": 17.5,
    "M24": 21.0,
}
TAP_DRILL_TOL_MM = 0.05


def tap_drill_thread(diameter: float) -> str | None:
    """The metric thread whose tap drill is ``diameter`` (Ø5 -> ``M6``, Ø6.8 -> ``M8``), or ``None``: a hole of any other
    diameter (a Ø9 clearance hole, a Ø6 dowel) is not one a thread would be cut in."""
    for size, drill in TAP_DRILLS.items():
        if abs(diameter - drill) <= TAP_DRILL_TOL_MM:
            return size
    return None


def major_diameter(size: str) -> float | None:
    """``M6`` -> 6.0, ``M8x1`` -> 8.0; ``None`` for a size that is not metric."""
    match = _SIZE.match(size.strip())
    return float(match.group(1)) if match else None


def thread_entities(ctx: Context) -> list[Entity]:
    """The thin arcs of every threaded hole that shows as a circle in a view of the sheet."""
    out: list[Entity] = []
    for hole in ctx.holes:
        if hole.thread is None:
            continue
        major = major_diameter(hole.thread[0])
        if major is None or major <= hole.diameter:
            continue
        for view in ctx.views.values():
            if not view.looks_along(hole.direction):
                continue
            cx, cy = view.project(hole.centre)
            out.append(Arc(cx, cy, major / 2 * view.scale, OPEN_FROM_DEG, OPEN_TO_DEG, THREAD))
    out += external_entities(ctx)
    return out


# --- external threads (the ``external_thread`` operation): ISO 6410 on a shaft end --------------------------------------

EXTERNAL_RADIUS_TOL_MM = 0.05
EXTERNAL_AXIS_TOL_MM = 0.05


def external_threads(found: list[dict[str, Any]]) -> list[ThreadInfo]:
    """The ``ThreadInfo`` of the records ``thread_records.read_threads`` read from the document."""
    return [
        ThreadInfo(
            size=str(r["size"]),
            major=float(r["major_mm"]),
            minor=float(r["minor_mm"]),
            length=float(r["length_mm"]),
            start=(float(r["start"][0]), float(r["start"][1]), float(r["start"][2])),
            direction=unit((float(r["direction"][0]), float(r["direction"][1]), float(r["direction"][2]))),
            cylinder=str(r.get("cylinder", "")),
            pitch=float(r.get("pitch_mm", 0.0)),
            name=str(r.get("name", "")),
            tolerance_class=str(r.get("tolerance_class", "")),
        )
        for r in found
    ]


def threads_on_face(ctx: Context, face_id: str, face: Any) -> list[ThreadInfo]:
    """The external threads of the cylinder ``face_id``: the ones made on that face, else (a face renamed by a later edit)
    the ones on the same axis and diameter. ``face`` is its ``FaceInfo``."""
    named = [t for t in ctx.threads if t.cylinder == face_id]
    if named:
        return named
    if face.radius is None or face.axis_dir is None or face.axis_point is None:
        return []
    found = []
    for t in ctx.threads:
        if abs(2 * face.radius - t.major) > EXTERNAL_RADIUS_TOL_MM or not parallel(face.axis_dir, t.direction):
            continue
        axis = unit(face.axis_dir)
        offset = sub(t.start, face.axis_point)
        along = dot(offset, axis)
        radial = math.dist(offset, (axis[0] * along, axis[1] * along, axis[2] * along))
        if radial <= EXTERNAL_AXIS_TOL_MM:
            found.append(t)
    return found


def external_callout(ctx: Context, threads: list[ThreadInfo]) -> list[str]:
    """The callout lines of the threads on one cylinder: ``M12 long. útil 20`` (equal ones share a count: ``2× M12 ...``)."""
    groups: dict[tuple[str, float, str], int] = {}
    for t in threads:
        key = (t.size, round(t.length, 3), t.tolerance_class)
        groups[key] = groups.get(key, 0) + 1
    lines = []
    for (size, length, tol_class), count in groups.items():
        shown = size.replace(".", ",") if ctx.comma else size
        shown += f" - {tol_class}" if tol_class else ""  # ISO 6410: the designation with its class, ``M20x1,5 - 6g``
        line = f"{shown} {ctx.say('long. útil', 'useful length')} {ctx.num(length)}"
        lines.append(f"{count}× {line}" if count > 1 else line)
    return lines


def external_entities(ctx: Context) -> list[Entity]:
    """ISO 6410 for a thread on a shaft: in a view across the axis the thin line at the minor diameter along the thread
    (the thick lines at the major diameter are the cylinder, TechDraw draws them); in a view along the axis the thin arc of
    about three quarters of a circle at the minor diameter."""
    out: list[Entity] = []
    for t in ctx.threads:
        end = (
            t.start[0] + t.direction[0] * t.length,
            t.start[1] + t.direction[1] * t.length,
            t.start[2] + t.direction[2] * t.length,
        )
        for view in ctx.views.values():
            if view.looks_along(t.direction):
                cx, cy = view.project(t.start)
                out.append(Arc(cx, cy, t.minor / 2 * view.scale, OPEN_FROM_DEG, OPEN_TO_DEG, THREAD))
            elif view.shows_axis_along(t.direction):
                a, b = view.project(t.start), view.project(end)
                size = math.dist(a, b)
                if size < 0.5:
                    continue
                u = ((b[0] - a[0]) / size, (b[1] - a[1]) / size)
                r = t.minor / 2 * view.scale
                for sign in (1.0, -1.0):
                    n = (-u[1] * sign * r, u[0] * sign * r)
                    out.append(Line(a[0] + n[0], a[1] + n[1], b[0] + n[0], b[1] + n[1], THREAD))
                # where the full thread ends: a transverse line across the whole shaft, wide like the outline (ISO 6410: the
                # limit of the useful thread length is a continuous wide line; the text of the standard was not opened here)
                nx, ny = -u[1] * t.major / 2 * view.scale, u[0] * t.major / 2 * view.scale
                out.append(Line(b[0] + nx, b[1] + ny, b[0] - nx, b[1] - ny, CONTOUR))
    return out
