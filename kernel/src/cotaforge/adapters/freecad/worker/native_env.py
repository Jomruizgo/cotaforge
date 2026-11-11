"""The namespace preloaded for a ``run_native`` fragment (a fresh one per step; state lives in the document).

Names available without an import:

    doc            the active FreeCAD document (``App.ActiveDocument`` is the same object)
    App, FreeCAD   the FreeCAD module (both names)
    Part           the Part module
    Base           ``FreeCAD.Base`` (``Base.Vector`` is ``App.Vector``)
    Vector, Placement, Rotation
                   ``App.Vector``, ``App.Placement``, ``App.Rotation``
    Sketcher       the Sketcher module (absent from the namespace if it cannot be imported)
    PartDesign     the PartDesign module (absent likewise)
    math           the stdlib math module

and three helpers:

    faces_where(obj, predicate)  -> [(name, face), ...]  faces of ``obj.Shape`` for which ``predicate(face)`` is
                                    true; ``name`` is the sub-element name (``"Face3"``) to use in links and
                                    ``Edges``/``Faces`` properties.
    edges_where(obj, predicate)  -> [(name, edge), ...]  same for edges (``"Edge5"``).
    solids()                     -> [obj, ...]  the objects whose solids are the document's current results (inputs
                                    consumed by another object, e.g. the base of a ``Part::Cut``, are left out).

The helpers never raise on a null shape: they return an empty list. Everything else (``import os``...) must be
imported by the fragment itself.
"""

import math
from collections.abc import Callable
from typing import Any

import FreeCAD as App
import native_state
import Part

NAMESPACE_NAMES = (
    "doc",
    "App",
    "FreeCAD",
    "Part",
    "Base",
    "Vector",
    "Placement",
    "Rotation",
    "Sketcher",
    "PartDesign",
    "math",
    "faces_where",
    "edges_where",
    "solids",
)


def _sub_elements(obj: Any, kind: str, predicate: Callable[[Any], bool]) -> list[tuple[str, Any]]:
    try:
        shape = obj if hasattr(obj, "isNull") else obj.Shape
        if shape.isNull():
            return []
        items = shape.Faces if kind == "Face" else shape.Edges
    except Exception:  # noqa: BLE001 - an object without a shape
        return []
    return [(f"{kind}{index}", item) for index, item in enumerate(items, start=1) if predicate(item)]


def faces_where(obj: Any, predicate: Callable[[Any], bool]) -> list[tuple[str, Any]]:
    """Faces of ``obj.Shape`` (or of a shape) for which ``predicate(face)`` is true, as ``("Face3", face)``."""
    return _sub_elements(obj, "Face", predicate)


def edges_where(obj: Any, predicate: Callable[[Any], bool]) -> list[tuple[str, Any]]:
    """Edges of ``obj.Shape`` (or of a shape) for which ``predicate(edge)`` is true, as ``("Edge5", edge)``."""
    return _sub_elements(obj, "Edge", predicate)


def build_namespace(fc_doc: Any) -> dict[str, Any]:
    """A new globals dict for one fragment."""

    def solids() -> list[Any]:
        """The objects whose solids are the document's current results."""
        return native_state.solid_objects(fc_doc)

    if App.ActiveDocument is None or App.ActiveDocument.Name != fc_doc.Name:
        App.setActiveDocument(fc_doc.Name)
    namespace: dict[str, Any] = {
        "__name__": "__run_native__",
        "doc": fc_doc,
        "App": App,
        "FreeCAD": App,
        "Part": Part,
        "Base": App.Base,
        "Vector": App.Vector,
        "Placement": App.Placement,
        "Rotation": App.Rotation,
        "math": math,
        "faces_where": faces_where,
        "edges_where": edges_where,
        "solids": solids,
    }
    for module_name in ("Sketcher", "PartDesign"):
        try:
            namespace[module_name] = __import__(module_name)
        except Exception:  # noqa: BLE001 - absence must not break the step
            pass
    return namespace
