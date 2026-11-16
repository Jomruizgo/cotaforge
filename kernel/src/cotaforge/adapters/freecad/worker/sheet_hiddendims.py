# ruff: noqa: E501
"""A dimension on a hidden line, read from the TechDraw page (no FreeCAD import: the objects are only read).

A hidden line says "something is behind here"; it is never a place to measure from or to (the owner's rule, ISO 129-1: a
dimension goes on the visible outline, or on a section that cuts the feature). ``dimension_on_hidden`` fires for a dimension of
the page one of whose references is, in its view,

* an edge TechDraw lists among the view's hidden edges, or
* a vertex that lies on a hidden edge and on no visible one (the end of a hidden line; a point that a visible line also
  passes through is on the outline and is fine).

Only a view that shows hidden lines has any (``HardHidden``): a view without them can never raise this. A reference this cannot
read is not a finding (the dimension is read by ``sheet_pageread`` and listed there when it cannot be).
"""

from collections.abc import Callable
from typing import Any

HIT_MM = 0.05  # a vertex this near an edge (view mm) lies on it
MAX_ROWS = 3  # findings listed before the rest are counted

Distance = Callable[[Any, float, float], float]


def _part_distance(edge: Any, x: float, y: float) -> float:
    import Part  # noqa: PLC0415 - FreeCAD's own module: only the worker that holds a page has it

    return float(edge.distToShape(Part.Vertex(x, y, 0.0))[0])


def _subs(entry: Any) -> tuple[Any, list[str]]:
    """``(view, [sub names])`` of one entry of ``References2D`` (a pair of the view and a name or a list of names)."""
    view, subs = entry[0], entry[1]
    return view, [str(s) for s in (subs if isinstance(subs, list | tuple) else [subs])]


def _index(name: str, kind: str) -> int | None:
    return int(name[len(kind) :]) if name.startswith(kind) and name[len(kind) :].isdigit() else None


def hidden_references(page: Any, names: dict[str, str], item_prop: str, distance: Distance = _part_distance) -> list[tuple[str, str, str]]:
    """``(item, view, reference)`` of every dimension of ``page`` that measures from or to a hidden line.

    ``names``: the name of a view object -> the name the review uses for it; ``item_prop``: the property that names the item a
    dimension belongs to (a dimension nobody tagged is the engineer's own and is left alone)."""
    out: list[tuple[str, str, str]] = []
    cache: dict[str, tuple[list[Any], int]] = {}  # view name -> its edges and how many of them are visible
    for obj in page.Views:
        if not str(getattr(obj, "TypeId", "")).endswith("DrawViewDimension"):
            continue
        owner = str(getattr(obj, item_prop, "") or "")
        if not owner:  # a dimension the engineer put on the page by hand is his decision, not the sheet's
            continue
        try:
            entries = list(obj.References2D)
        except Exception:  # noqa: BLE001 - FreeCAD raises plain exceptions of every kind: not a finding
            continue
        for entry in entries:
            try:
                view, subs = _subs(entry)
                if str(view.Name) not in cache:
                    cache[str(view.Name)] = _edges_of(view)
                edges, visible = cache[str(view.Name)]
                if visible >= len(edges):  # no hidden line in this view
                    continue
                for sub in subs:
                    if _on_hidden(view, sub, edges, visible, distance):
                        out.append((owner, names.get(str(view.Name), str(getattr(view, "Label", view.Name))), sub))
            except Exception:  # noqa: BLE001, S112 - a reference it cannot read is the business of ``sheet_pageread``
                continue
    return list(dict.fromkeys(out))


def _edges_of(view: Any) -> tuple[list[Any], int]:
    visible, hidden = len(view.getVisibleEdges()), len(view.getHiddenEdges())
    return [view.getEdgeByIndex(i) for i in range(visible + hidden)], visible


def _on_hidden(view: Any, sub: str, edges: list[Any], visible: int, distance: Distance) -> bool:
    edge = _index(sub, "Edge")
    if edge is not None:
        return visible <= edge < len(edges)
    vertex = _index(sub, "Vertex")
    if vertex is None:
        return False
    v = view.getVertexByIndex(vertex)
    x, y = float(v.X), float(v.Y)
    near = [distance(e, x, y) <= HIT_MM for e in edges]
    return any(near[visible:]) and not any(near[:visible])


def findings(rows: list[tuple[str, str, str]], ctx: Any) -> list[dict[str, Any]]:
    """The review findings of ``rows`` as ``{"code", "severity", "message", "items", "features", "key"}``, one per dimension."""
    out: list[dict[str, Any]] = []
    by_item: dict[str, list[tuple[str, str]]] = {}
    for item, view, sub in rows:
        by_item.setdefault(item, []).append((view, sub))
    for item, refs in list(by_item.items())[:MAX_ROWS]:
        view = refs[0][0]
        text = ctx.say(
            f"La cota {item} mide sobre una línea oculta de la vista {view}: una línea oculta no se acota nunca. Acota el rasgo "
            "en la vista que lo ve de frente o en un corte parcial (drawing action=remove, ids=[...]; luego la cota en esa vista).",
            f"The dimension {item} measures on a hidden line of the {view} view: a hidden line is never dimensioned. Dimension "
            "the feature in the view that sees it face-on, or in a partial (broken-out) section (drawing action=remove, "
            "ids=[...]; then the dimension on that view).",
        )
        out.append(_finding("dimension_on_hidden", text, item))
    return out


def _finding(code: str, message: str, item: str) -> dict[str, Any]:
    return {"code": code, "severity": "warning", "message": message, "items": [item], "features": [], "key": item}
