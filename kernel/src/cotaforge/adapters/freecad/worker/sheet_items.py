"""The registry of sheet items: what a drawing sheet is made of, and the one place a new kind of item is plugged in.

A sheet is a list of items (``{"id": "h1", "type": "hole_dim", ...}``) kept as JSON on the page (``CotaforgeSpec``).
Each item type is an ``ItemType``: a validator that reduces an agent's dict to its known, checked fields, and a drawer
that resolves the item against the current geometry (``sheet_context.Context``) and adds entities to the sheet. The
values on a sheet always come out of the model, never out of the item.

TO ADD A KIND OF ITEM (phases P2-P4): write ``items_<name>.py`` exporting ``ITEMS: tuple[ItemType, ...]`` and add its
module to ``MODULES`` below -- one line. Types already named but not built yet are in ``PLANNED``; they are refused
with "not available yet" until their module lands (remove the type from ``PLANNED`` in the same change).
"""

import re
from typing import Any

import items_assembly
import items_datum
import items_dims
import items_explode
import items_finish
import items_gdt
import items_holes
import items_lines
import items_mould
import items_notes
import items_pattern
import items_views
import items_weld
import sheet_centre
from sheet_context import SECTION_LABEL, Context, ItemError, ItemResult, ItemType

PHASE_ON_SECTION = 5  # after the views (3) and the balloons (4)

# one line per module: the item types of P1. P2 added items_gdt (datum, gdt) and items_finish (finish); tolerances are
# fields of hole_dim / dim (sheet_tol), not an item. P3 items_assembly (parts_list, balloons) and items_explode
# (exploded); P4 items_views (section, detail, aux_view, view_lines) and the review.
MODULES = (
    items_holes,
    items_dims,
    items_datum,
    items_notes,
    items_views,
    items_gdt,
    items_finish,
    items_assembly,
    items_explode,
    items_lines,
    items_weld,
    items_mould,
    items_pattern,
)

REGISTRY: dict[str, ItemType] = {t.name: t for module in MODULES for t in module.ITEMS}

# item types the sheet will have, and the phase that builds them
PLANNED: dict[str, str] = {}

# item types the contract takes that this CAD does not draw: refused with this text (the adapter says it in its own
# words)
UNSUPPORTED: dict[str, str] = {
    "keyway_dims": (
        "keyway_dims is made into plain dim items when it is added (drawing add) and is never stored: if you read "
        "this, the part has no keyway whose walls can be dimensioned"
    ),
    "baseline_dims": (
        "baseline_dims is made into plain dim items when it is added (drawing add) and is never stored: if you read this, "
        "the face it counts from is not a shoulder or an end of the part"
    ),
    "thread_dims": (
        "thread_dims is made into plain dim items (kind diameter) when it is added (drawing add) and is never "
        "stored: if you read this, every external thread of the part has its callout"
    ),
    "chamfer_dims": (
        "chamfer_dims is made into plain dim items (kind chamfer) when it is added (drawing add) and is never "
        "stored: if you read this, the part has no chamfer without a dimension"
    ),
}

ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,23}$")
MAX_ITEMS = 200


def all_types() -> tuple[str, ...]:
    """Every item type name, built or planned."""
    return tuple(sorted({*REGISTRY, *PLANNED, *UNSUPPORTED}))


def fields_of(item_type: str) -> frozenset[str]:
    """The field names an item type takes (the contract's table is checked against this in a test)."""
    return REGISTRY[item_type].fields | {"type", "id"}


def normalise(spec: Any, taken: set[str], next_number: int) -> dict[str, Any]:
    """The validated form of one item. ``taken`` are the ids already on the sheet; an item without an id gets
    ``<prefix><n>``. Raises ``ItemError`` with a message that says what to change."""
    if not isinstance(spec, dict):
        msg = 'an item is an object like {"type": "hole_dim", ...}'
        raise ItemError(msg)
    kind = spec.get("type")
    if not isinstance(kind, str):
        msg = f"an item needs a type, one of {', '.join(sorted(REGISTRY))}"
        raise ItemError(msg)
    if kind in UNSUPPORTED and kind not in REGISTRY:
        raise ItemError(UNSUPPORTED[kind])
    if kind in PLANNED and kind not in REGISTRY:
        msg = (
            f"item type {kind!r} is not available yet (planned for {PLANNED[kind]}); "
            f"available: {', '.join(sorted(REGISTRY))}"
        )
        raise ItemError(msg)
    if kind not in REGISTRY:
        msg = f"unknown item type {kind!r}; available: {', '.join(sorted(REGISTRY))}"
        raise ItemError(msg)
    given = spec.get("id")
    if given is not None and (not isinstance(given, str) or not ID_PATTERN.match(given)):
        msg = f"id {given!r} must be a short name: a letter, then letters, digits, - or _ (24 characters at most)"
        raise ItemError(msg)
    checked = REGISTRY[kind].check({k: v for k, v in spec.items() if k != "id"})
    item_id = given or f"{REGISTRY[kind].prefix}{next_number}"
    while item_id in taken and given is None:
        next_number += 1
        item_id = f"{REGISTRY[kind].prefix}{next_number}"
    if item_id in taken:
        msg = f"id {item_id!r} is already on this sheet"
        raise ItemError(msg)
    return {"id": item_id, **checked}


def edited(item: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """The stored ``item`` with ``patch`` applied (a field set to ``None`` is taken off), checked by its own type. The id
    and the type never change. Raises ``ItemError`` with what to change."""
    kind = str(item["type"])
    if patch.get("type", kind) != kind:
        msg = f"an edit never changes the type ({kind}); remove the item and add the other one"
        raise ItemError(msg)
    merged = {k: v for k, v in item.items() if k != "id"}
    for key, value in patch.items():
        if key in ("id", "type"):
            continue
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    return {"id": item["id"], **REGISTRY[kind].check(merged)}


def prepare_all(ctx: Context, items: list[dict[str, Any]]) -> None:
    """Let each item type see all its items before any is drawn (the title block needs the revisions, the notes block
    needs to know how many lines it has)."""
    ctx.extras["sheet_items"] = items  # what a ``prepare`` needs of the other types
    for kind, group in _by_type(items).items():
        prepare = REGISTRY[kind].prepare if kind in REGISTRY else None
        if prepare is not None:
            prepare(ctx, group)


def shortest_first(
    ctx: Context, items: list[dict[str, Any]], order: list[int], group: Any
) -> list[int]:
    """``order`` with the distance dimensions that stack by the order they are drawn (those with no ``offset_mm``) put shortest
    first inside each group (``group(position)``: the phase), the other items where they were. The lines stack outward in
    the order they are drawn, so a length counted from a datum stands nearer the part than a longer one it runs along (ISO
    129-1), whatever order the agent listed them in; the extension lines of the short one then never cross the line of the
    long one."""
    spans: dict[int, float] = {}
    for position in order:
        spec = items[position]
        if spec.get("type") == "dim" and spec.get("kind") == "distance" and spec.get("offset_mm") is None:
            span = items_dims.distance_span(ctx, spec)
            if span is not None:
                spans[position] = span
    out = list(order)
    seen: dict[Any, list[int]] = {}
    for slot, position in enumerate(order):
        if position in spans:
            seen.setdefault(group(position), []).append(slot)
    for slots in seen.values():
        ranked = sorted((order[s] for s in slots), key=lambda p: (spans[p], p))
        for slot, position in zip(slots, ranked, strict=True):
            out[slot] = position
    return out


def draw_items(ctx: Context, items: list[dict[str, Any]]) -> list[ItemResult]:
    """Draw every item; an item that cannot be drawn becomes an ``error`` result (and leaves nothing on the sheet), the
    others are still drawn. Items are drawn by phase (lines and dimensions, then leaders, then free text) and in list
    order within a phase, so a callout looks for room around the dimensions whatever the order they were added in; the
    results come back in list order."""
    results: list[ItemResult | None] = [None] * len(items)
    in_place = any(i.get("type") == "section" and i.get("in_place") for i in items)

    def phase(position: int) -> int:
        kind = items[position].get("type")
        own = REGISTRY[kind].phase if kind in REGISTRY else 0
        view = items[position].get("view")
        if in_place and kind in ("hole_dim", "overall") and view is None:
            # the bores are called out on the section that stands in the main view, and the overall size stands outside
            # the dimensions of that section (ISO 129-1), so it is drawn after them
            return max(own, PHASE_ON_SECTION)
        # an item on a section view is drawn after the sections: the view exists once its section item has been drawn
        return max(own, PHASE_ON_SECTION) if isinstance(view, str) and SECTION_LABEL.match(view) else own

    def outermost(position: int) -> int:
        """The overall size is drawn after the other dimensions of its phase: the lines stack outward in the order they are
        drawn and the overall one stands outside the smaller ones (ISO 129-1), so the extension lines of the dimensions
        inside it never cross its line."""
        return 1 if items[position].get("type") == "overall" else 0

    order = sorted(range(len(items)), key=lambda i: (phase(i), outermost(i), i))
    for position in shortest_first(ctx, items, order, lambda i: (phase(i), outermost(i))):
        spec = items[position]
        kind = spec.get("type", "?")
        item_id = str(spec.get("id", "?"))
        if kind not in REGISTRY:
            results[position] = ItemResult(item_id, str(kind), "error", f"item type {kind!r} is not available")
            continue
        mark = (len(ctx.sheet.entities), len(ctx.occupied), dict(ctx.stacks), ctx.notes)
        ctx.extras["current"] = item_id
        try:
            status, detail = REGISTRY[kind].draw(ctx, spec)
        except ItemError as exc:
            ctx.extras.get("anchors", {}).pop(item_id, None)
            ctx.extras.get("placement_notes", {}).pop(item_id, None)
            ctx.extras.get("natives", {}).pop(item_id, None)
            if "dim_records" in ctx.extras:
                ctx.extras["dim_records"] = [r for r in ctx.extras["dim_records"] if r[0] != item_id]
            for users in ctx.extras.get("view_use", {}).values():
                users.discard(item_id)
            del ctx.sheet.entities[mark[0] :]
            del ctx.occupied[mark[1] :]
            ctx.stacks.clear()
            ctx.stacks.update(mark[2])
            ctx.notes = mark[3]
            results[position] = ItemResult(item_id, kind, "error", str(exc))
        else:
            notes = ctx.extras.get("placement_notes", {}).pop(item_id, [])
            if notes:
                status, detail = "warning", "; ".join([detail, *notes]) if detail else "; ".join(notes)
            results[position] = ItemResult(item_id, kind, status, detail)
            ctx.extras.setdefault("item_slices", {})[item_id] = (mark[0], len(ctx.sheet.entities))
    sheet_centre.trim(ctx)  # the centre lines stop short of the dimensions and texts the items put in their way
    return [r for r in results if r is not None]


def draw_all(ctx: Context, items: list[dict[str, Any]]) -> list[ItemResult]:
    """``prepare_all`` then ``draw_items``."""
    prepare_all(ctx, items)
    return draw_items(ctx, items)


def _by_type(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for spec in items:
        groups.setdefault(str(spec.get("type")), []).append(spec)
    return groups
