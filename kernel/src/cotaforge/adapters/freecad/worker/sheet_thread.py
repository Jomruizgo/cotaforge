# ruff: noqa: E501
"""ISO 6410 thread representation (no FreeCAD import): a tapped hole seen along its axis is drawn with its tap drill as
a thick circle (TechDraw draws that one) and the major diameter of the thread as a thin arc of about three quarters of a
circle. The arc belongs to the geometry, not to a callout, so it is drawn for every hole a ``hole`` operation declared
threaded, whether or not a ``hole_dim`` item names it.
"""

import importlib.util
import re
import sys
from pathlib import Path

from sheet_context import Context
from sheet_model import THREAD, Arc, Entity

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
    return out
