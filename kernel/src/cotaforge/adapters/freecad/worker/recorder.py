"""Recorder for ``export_macro``: every document mutation routed through ``fc.py`` appends one source line.

The recorded script is standalone FreeCAD Python: it recreates the documents by handle id and addresses
objects with ``O('doc:1', 'Pad')``.
"""

from typing import Any

import Part
from FreeCAD import Matrix, Placement, Rotation, Vector

PREAMBLE = (
    "import FreeCAD as App\n"
    "import Part\n"
    "import Sketcher\n"
    "from FreeCAD import Vector, Rotation, Placement, Matrix\n"
    "\n"
    "docs = {}\n"
    "\n"
    "\n"
    "def O(doc_id, name):\n"
    "    return docs[doc_id].getObject(name)\n"
    "\n"
    "\n"
)


class Recorder:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.enabled = True

    def record(self, line: str) -> None:
        if self.enabled:
            self.lines.append(line + "\n")

    def script(self) -> str:
        return PREAMBLE + "".join(self.lines)


def literal(value: Any) -> str:
    """Python source for a value: FreeCAD types, document objects and containers thereof."""
    if isinstance(value, Vector):
        return f"Vector({value.x!r}, {value.y!r}, {value.z!r})"
    if isinstance(value, Rotation):
        q = value.Q
        return f"Rotation({q[0]!r}, {q[1]!r}, {q[2]!r}, {q[3]!r})"
    if isinstance(value, Placement):
        return f"Placement({literal(value.Base)}, {literal(value.Rotation)})"
    if isinstance(value, Matrix):
        return "Matrix(" + ", ".join(repr(float(x)) for x in value.A) + ")"
    if isinstance(value, Part.LineSegment):
        return f"Part.LineSegment({literal(value.StartPoint)}, {literal(value.EndPoint)})"
    if isinstance(value, Part.Circle):
        return f"Part.Circle({literal(value.Center)}, {literal(value.Axis)}, {value.Radius!r})"
    if isinstance(value, Part.ArcOfCircle):
        circle = f"Part.Circle({literal(value.Center)}, {literal(value.Axis)}, {value.Radius!r})"
        return f"Part.ArcOfCircle({circle}, {value.FirstParameter!r}, {value.LastParameter!r})"
    if isinstance(value, Part.Point):
        return f"Part.Point({literal(Vector(value.X, value.Y, value.Z))})"
    if isinstance(value, Part.Shape):
        return "Part.Shape()  # opaque shape"
    if hasattr(value, "Name") and hasattr(value, "Document"):
        return f"O({doc_key(value.Document)!r}, {value.Name!r})"
    if isinstance(value, tuple):
        return "(" + ", ".join(literal(v) for v in value) + ("," if len(value) == 1 else "") + ")"
    if isinstance(value, list):
        return "[" + ", ".join(literal(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{literal(k)}: {literal(v)}" for k, v in value.items()) + "}"
    if isinstance(value, str | bool | int | float) or value is None:
        return repr(value)
    return repr(value)


_DOC_KEYS: dict[str, str] = {}


def register_document(fc_doc: Any, doc_id: str) -> None:
    _DOC_KEYS[fc_doc.Name] = doc_id


def doc_key(fc_doc: Any) -> str:
    return _DOC_KEYS.get(fc_doc.Name, fc_doc.Name)


def clear_documents() -> None:
    """Drop every ``Name -> doc_id`` mapping. FreeCAD reuses ``Document`` names (``Unnamed``,
    ``Unnamed1``, ...) across worker resets, so without this ``main.reset()`` would leave stale
    entries in ``_DOC_KEYS`` forever and could resolve a new document to an old ``doc_id``."""
    _DOC_KEYS.clear()
