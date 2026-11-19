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
    """The edges of the part in ``view`` (the visible ones first, then the hidden ones) and how many are visible. TechDraw counts the
    cosmetic edges and the centre lines among the visible ones and lists them AFTER every edge of the part: they are not the part, so
    they are left out (a centre mark through the end arc of a keyway is not a hidden line)."""
    own = len(list(getattr(view, "CosmeticEdges", None) or [])) + len(list(getattr(view, "CenterLines", None) or []))
    visible, hidden = max(len(view.getVisibleEdges()) - own, 0), len(view.getHiddenEdges())
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


def _kinds(ctx: Any) -> dict[str, str]:
    """The type of each item of the sheet, by id (empty when the context does not carry the items)."""
    items = ctx.extras.get("items") or ctx.extras.get("sheet_items") or []
    return {str(i.get("id")): str(i.get("type")) for i in items if isinstance(i, dict)}


def _how_to(ctx: Any, item: str, kind: str | None) -> str:
    """What to do about the dimension ``item``, in the sheet's language: only calls the gates let the drafter make and that exist."""
    seen = ctx.say(
        "Acota el rasgo donde se ve de frente: en la vista estándar que lo mira de frente (top o right; si la hoja no la tiene, "
        'drawing(action="add_view", views=["top"])). Una vista auxiliar (aux_view) no admite cotas y no hay corte parcial '
        "(section es solo completo); si ninguna vista estándar lo ve de frente, no hay herramienta: díselo al ingeniero. ",
        "Dimension the feature where it is seen face-on: in the standard view that looks at it square (top or right; if the "
        'sheet lacks it, drawing(action="add_view", views=["top"])). An auxiliary view (aux_view) takes no dimensions and there '
        "is no partial section (section is full only); if no standard view sees it face-on there is no tool: tell the engineer. ",
    )
    move = f'drawing(action="update", items=[{{"id":"{item}","view":"top"}}])'
    if kind in (None, "dim"):
        return seen + ctx.say(
            f"Mueve la cota con {move} (si cae sobre otro item, el error pide otro side u offset_mm). No quites el item: uno que "
            "puso el sistema (rq_) pide reason= y, si el modelo lo midió, la orden del ingeniero (by=).",
            f"Move the dimension with {move} (if it lands on another item the error asks for another side or offset_mm). Do not "
            "remove the item: one the harness added (rq_) needs reason= and, if the model measured it, the engineer's order (by=).",
        )
    return seen + ctx.say(
        f"{item} ({kind}) agrupa varias cotas: update lo mueve entero y quitarlo las pierde todas. Si la oculta es una sola "
        "referencia, cambia con update el campo que la fija para que apunte a una línea visible; no hay herramienta que saque una "
        "sola cota de un item de varias.",
        f"{item} ({kind}) groups several dimensions: update moves it whole and removing it drops them all. If the hidden one is "
        "a single reference, change with update the field that fixes it so that it points at a visible line; no tool takes one "
        "dimension out of an item of several.",
    )


def findings(rows: list[tuple[str, str, str]], ctx: Any) -> list[dict[str, Any]]:
    """The review findings of ``rows`` as ``{"code", "severity", "message", "items", "features", "key"}``, one per dimension.

    The way out the message gives is one the gates let through and one that exists: the dimension is MOVED (``update`` of the
    item) to the standard view that sees the feature face-on, which ``add_view`` puts on the sheet when it lacks it. It is never
    taken off (removing an ``rq_`` item needs a reason, and an order when the model measured it; removing an item of several
    dimensions drops all of them), and an ``aux_view`` or a partial section is not offered: the first takes no dimensions (items
    name only the standard views and the labels of sections) and the second does not exist (``section`` is full only)."""
    out: list[dict[str, Any]] = []
    by_item: dict[str, list[tuple[str, str]]] = {}
    for item, view, sub in rows:
        by_item.setdefault(item, []).append((view, sub))
    kinds = _kinds(ctx)
    for item, refs in list(by_item.items())[:MAX_ROWS]:
        view = refs[0][0]
        text = ctx.say(
            f"La cota {item} mide sobre una línea oculta de la vista {view}: una línea oculta no se acota nunca. ",
            f"The dimension {item} measures on a hidden line of the {view} view: a hidden line is never dimensioned. ",
        ) + _how_to(ctx, item, kinds.get(item))
        out.append(_finding("dimension_on_hidden", text, item))
    return out


def _finding(code: str, message: str, item: str) -> dict[str, Any]:
    return {"code": code, "severity": "warning", "message": message, "items": [item], "features": [], "key": item}
