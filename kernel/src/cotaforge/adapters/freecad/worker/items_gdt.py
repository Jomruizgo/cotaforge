"""``datum`` and ``gdt``: ISO 5459 datum features and ISO 1101 feature control frames (no FreeCAD import).

* ``datum`` -- a datum triangle with its letter in a square frame, attached to a face of the part: on the outline of a
  plane seen edge-on, or on the AXIS of a cylinder (the triangle sits at the end of the centre line in a side view, at
  the centre of the circle in an end view).
* ``gdt`` -- a frame of cells (characteristic symbol | tolerance, with the diameter zone and the M / L modifier | up to
  three datum letters) joined by a leader to a face, or hung from the callout of a dimension (``on`` = the id of a
  ``hole_dim`` or of a ``dim`` that has a leader), which is how the tolerance of an AXIS is attached.

A frame cites datums by letter and every letter must be a ``datum`` item of the sheet that drew: a frame citing a datum
that does not exist is an item error, never a silent drawing. The tolerance value is the engineer's requirement, not a
measurement; it is the only number on the sheet that does not come from the model (besides the explicit tolerances).
"""

from dataclasses import dataclass
from typing import Any

from items_attach import attach_face, place_label
from items_common import asked
from sheet_context import (
    COMMON_FIELDS,
    Context,
    ItemError,
    ItemType,
    Point,
    check_fields,
    v_bool,
    v_enum,
    v_ids,
    v_num,
    v_str,
)
from sheet_draw import arrow
from sheet_model import DIMENSIONS, Entity, Glyph, Line, Polyline, Solid, Text, text_width

CELL_MM = 7.0  # height of a frame; a symbol or datum cell is this wide
TEXT_MM = 3.5
GLYPH_MM = 4.6
MODIFIER_MM = 4.0
TRIANGLE_HEIGHT_MM = 4.0
TRIANGLE_HALF_BASE_MM = 2.3
STEM_MM = 4.0
FRAME_PAD_MM = 1.75
BAD_LETTERS = "IOQ"


@dataclass(frozen=True)
class Characteristic:
    glyph: str
    datums: str  # "none" (a form tolerance: no datum), "optional" or "required"
    axis: bool = False  # always tolerates an axis or a median plane: hang it from the dimension of the feature
    diameter: bool = False  # may have the cylindrical zone sign
    modifier: bool = False  # may carry M / L


CHARACTERISTICS: dict[str, Characteristic] = {
    "flatness": Characteristic("flatness", "none"),
    "straightness": Characteristic("straightness", "none", diameter=True, modifier=True),
    "circularity": Characteristic("circularity", "none"),
    "cylindricity": Characteristic("cylindricity", "none"),
    "parallelism": Characteristic("parallelism", "required", diameter=True, modifier=True),
    "perpendicularity": Characteristic("perpendicularity", "required", diameter=True, modifier=True),
    "angularity": Characteristic("angularity", "required", diameter=True, modifier=True),
    "position": Characteristic("position", "optional", axis=True, diameter=True, modifier=True),
    "concentricity": Characteristic("concentricity", "required", axis=True, diameter=True, modifier=True),
    "coaxiality": Characteristic("concentricity", "required", axis=True, diameter=True, modifier=True),
    "symmetry": Characteristic("symmetry", "required", axis=True, modifier=True),
    "runout": Characteristic("runout", "required"),
    "total_runout": Characteristic("total_runout", "required"),
    "profile_line": Characteristic("profile_line", "optional"),
    "profile_surface": Characteristic("profile_surface", "optional"),
}


def v_letter(value: Any) -> str:
    if not isinstance(value, str) or len(value) != 1 or not "A" <= value <= "Z" or value in BAD_LETTERS:
        msg = f"must be one capital letter A-Z other than {', '.join(BAD_LETTERS)} (ISO 5459)"
        raise ItemError(msg)
    return value


def v_datums(value: Any) -> list[str]:
    items = value if isinstance(value, list | tuple) else [value]
    letters = [v_letter(i) for i in items]
    if not 1 <= len(letters) <= 3 or len(set(letters)) != len(letters):
        msg = 'must be one to three different datum letters, like ["A", "B"]'
        raise ItemError(msg)
    return letters


def v_one_face(value: Any) -> list[str]:
    ids = v_ids("face")(value)
    if len(ids) != 1:
        msg = "must be ONE face id"
        raise ItemError(msg)
    return ids


# --- the frame --------------------------------------------------------------------------------------------------------


def _cell_texts(entities: list[Entity], x: float, y: float, width: float, text: str) -> None:
    entities.append(Text(x + width / 2, y + 2.2, text, TEXT_MM, DIMENSIONS, "center"))


def frame(x: float, y: float, cells: list[tuple[str, ...]]) -> tuple[list[Entity], float]:
    """A frame with its bottom-left corner at ``(x, y)``; ``cells`` are ``("glyph", name)``, ``("value", text, modifier
    glyph or "")`` and ``("letter", L)``. Returns the entities and the width."""
    out: list[Entity] = []
    widths: list[float] = []
    for cell in cells:
        if cell[0] == "value":
            extra = MODIFIER_MM + 1.0 if cell[2] else 0.0
            widths.append(text_width(cell[1], TEXT_MM) + extra + 2 * FRAME_PAD_MM)
        else:
            widths.append(CELL_MM)
    total = sum(widths)
    out.append(Polyline(((x, y), (x + total, y), (x + total, y + CELL_MM), (x, y + CELL_MM)), DIMENSIONS, closed=True))
    left = x
    for index, (cell, width) in enumerate(zip(cells, widths, strict=True)):
        if index:
            out.append(Line(left, y, left, y + CELL_MM, DIMENSIONS))
        if cell[0] == "glyph":
            pad = (CELL_MM - GLYPH_MM) / 2
            out.append(Glyph(cell[1], left + pad, y + pad, GLYPH_MM, DIMENSIONS))
        elif cell[0] == "letter":
            _cell_texts(out, left, y, width, cell[1])
        else:
            text = cell[1]
            shown = text_width(text, TEXT_MM)
            out.append(Text(left + FRAME_PAD_MM, y + 2.2, text, TEXT_MM, DIMENSIONS, "left"))
            if cell[2]:
                pad = (CELL_MM - MODIFIER_MM) / 2
                out.append(Glyph(cell[2], left + FRAME_PAD_MM + shown + 1.0, y + pad, MODIFIER_MM, DIMENSIONS))
        left += width
    return out, total


def _frame_at(
    anchor: Point, direction: tuple[float, float], cells: list[tuple[str, ...]]
) -> tuple[list[Entity], tuple[float, float, float, float]]:
    """A frame joined to ``anchor`` by the middle of the side facing the feature (``direction`` points away from it)."""
    probe, width = frame(0.0, 0.0, cells)
    del probe
    if abs(direction[0]) >= abs(direction[1]):
        x0 = anchor[0] if direction[0] > 0 else anchor[0] - width
        y0 = anchor[1] - CELL_MM / 2
    else:
        x0 = anchor[0] - width / 2
        y0 = anchor[1] if direction[1] > 0 else anchor[1] - CELL_MM
    entities, _ = frame(x0, y0, cells)
    return entities, (x0, y0, x0 + width, y0 + CELL_MM)


# --- datum ------------------------------------------------------------------------------------------------------------

DATUM_CHECKS = {
    "letter": v_letter,
    "face": v_one_face,
    "on": v_str(24),
    "view": COMMON_FIELDS["view"],
    "side": COMMON_FIELDS["side"],
    "offset_mm": COMMON_FIELDS["offset_mm"],
}


def check_datum(spec: dict[str, Any]) -> dict[str, Any]:
    out = check_fields(spec, DATUM_CHECKS, required=("letter",))
    if ("face" in out) == ("on" in out):
        msg = (
            "datum needs exactly one of face (a PLANE seen edge-on: the triangle stands on its outline) or on (the "
            "id of the hole_dim or dim whose callout carries the datum: the way to name the AXIS of a cylinder)"
        )
        raise ItemError(msg)
    return out


def prepare_datum(ctx: Context, specs: list[dict[str, Any]]) -> None:
    """The first datum item of each letter owns it; a second one with the same letter is an error when drawn."""
    first: dict[str, str] = {}
    for spec in specs:
        first.setdefault(spec["letter"], spec["id"])
    ctx.extras["datum_owner"] = first


def _triangle(tip: Point, direction: tuple[float, float]) -> tuple[Entity, Point]:
    """A filled datum triangle with its tip at ``tip``, its base ``direction`` away; returns it and its base centre."""
    px, py = -direction[1], direction[0]
    base = (tip[0] + direction[0] * TRIANGLE_HEIGHT_MM, tip[1] + direction[1] * TRIANGLE_HEIGHT_MM)
    corners = (
        tip,
        (base[0] + px * TRIANGLE_HALF_BASE_MM, base[1] + py * TRIANGLE_HALF_BASE_MM),
        (base[0] - px * TRIANGLE_HALF_BASE_MM, base[1] - py * TRIANGLE_HALF_BASE_MM),
    )
    return Solid(corners, DIMENSIONS), base


def _datum_on_callout(ctx: Context, spec: dict[str, Any]) -> tuple[str, str]:
    """The datum of an AXIS: its triangle stands on the end of the shelf of the callout that dimensions the feature (ISO
    5459: in line with the dimension), the framed letter hangs below it."""
    target = spec["on"]
    anchors = ctx.extras.get("anchors", {}).get(target)
    if not anchors:
        msg = (
            f"item {target!r} has no callout with a leader on this sheet to carry the datum (a hole_dim, or a dim of "
            "kind diameter or radius, drawn without error)"
        )
        raise ItemError(msg)
    elbow, direction, rect = anchors[0]["elbow"], anchors[0]["dir"], anchors[0]["rect"]
    tip_x = elbow[0] + direction * (rect[2] - rect[0] - 3.0)
    tip = (tip_x, elbow[1])
    triangle, base = _triangle(tip, (0.0, -1.0))
    _, width = frame(0.0, 0.0, [("letter", spec["letter"])])
    free = False
    first: tuple[list[Entity], tuple[float, float, float, float]] | None = None
    for step in range(4):
        top = base[1] - STEM_MM - step * (CELL_MM + 1.5)
        boxed, box = _frame_at((tip_x, top), (0.0, -1.0), [("letter", spec["letter"])])
        entities: list[Entity] = [triangle, Line(base[0], base[1], tip_x, top, DIMENSIONS), *boxed]
        if first is None:
            first = (entities, box)
        if ctx.free(box):
            free = True
            break
    assert first is not None
    chosen, box = (entities, box) if free else first
    ctx.reserve(box)
    ctx.add(chosen)
    return ("ok" if free else "warning"), f"datum {spec['letter']} on the dimension of {target} (the axis)"


def draw_datum(ctx: Context, spec: dict[str, Any]) -> tuple[str, str]:
    letter = spec["letter"]
    owner = ctx.extras.get("datum_owner", {}).get(letter, spec["id"])
    if owner != spec["id"]:
        msg = f"datum {letter} is already on this sheet (item {owner}): a letter names one datum feature"
        raise ItemError(msg)
    if "on" in spec:
        status, text = _datum_on_callout(ctx, spec)
        ctx.extras.setdefault("datums_drawn", set()).add(letter)
        return status, text if status == "ok" else f"{text}; it overlaps other things"
    face_id = spec["face"][0]
    if ctx.geometry.face(face_id).kind == "cylinder":
        msg = (
            f"{face_id} is a cylinder: its datum is its AXIS, which ISO 5459 puts in line with the dimension of the "
            "feature: dimension it (hole_dim, or dim kind=diameter) and use on=<that item's id>"
        )
        raise ItemError(msg)
    att = attach_face(ctx, face_id, spec.get("view"), spec.get("side"))

    def build(anchor: Point, direction: tuple[float, float]) -> tuple[list[Entity], tuple[float, float, float, float]]:
        triangle, base = _triangle(att.tip, direction)
        boxed, rect = _frame_at(anchor, direction, [("letter", letter)])
        return [triangle, Line(base[0], base[1], anchor[0], anchor[1], DIMENSIONS), *boxed], rect

    entities, free = place_label(ctx, att, build, spec.get("offset_mm"), asked(spec))
    ctx.add(entities)
    ctx.extras.setdefault("datums_drawn", set()).add(letter)
    text = f"datum {letter} on the surface of {face_id} in the {att.view.name} view"
    return ("ok", text) if free else ("warning", f"{text}; it overlaps other things: move it with offset_mm or side")


# --- gdt --------------------------------------------------------------------------------------------------------------

GDT_CHECKS = {
    "char": v_enum(*CHARACTERISTICS),
    "value": v_num(0.0001, 50.0),
    "diameter": v_bool,
    "modifier": v_enum("M", "L"),
    "datums": v_datums,
    "face": v_one_face,
    "on": v_str(24),
    "view": COMMON_FIELDS["view"],
    "side": COMMON_FIELDS["side"],
    "offset_mm": COMMON_FIELDS["offset_mm"],
}


def check_gdt(spec: dict[str, Any]) -> dict[str, Any]:
    out = check_fields(spec, GDT_CHECKS, required=("char", "value"))
    char = CHARACTERISTICS[out["char"]]
    name = out["char"]
    if ("face" in out) == ("on" in out):
        msg = (
            "gdt needs exactly one of face (a face id: the leader goes to it) or on (the id of the dimension item it "
            "hangs from)"
        )
        raise ItemError(msg)
    if out.get("diameter") and not char.diameter:
        msg = f"{name} has no diameter zone (Ø): remove diameter"
        raise ItemError(msg)
    if "modifier" in out and not char.modifier:
        msg = f"{name} takes no M / L modifier"
        raise ItemError(msg)
    given = out.get("datums", [])
    if char.datums == "none" and given:
        msg = f"{name} is a form tolerance: it takes no datums"
        raise ItemError(msg)
    if char.datums == "required" and not given:
        msg = f'{name} needs datums: datums=["A"] (up to three, each a datum item of the sheet)'
        raise ItemError(msg)
    if (char.axis or out.get("diameter")) and "on" not in out:
        msg = (
            f"{name}{' with a Ø zone' if out.get('diameter') and not char.axis else ''} tolerates an axis or a median "
            "plane: hang the frame from the dimension of the feature with on=<id of the hole_dim or dim>"
        )
        raise ItemError(msg)
    return out


def _cells(spec: dict[str, Any]) -> list[tuple[str, ...]]:
    char = CHARACTERISTICS[spec["char"]]
    value = ("Ø" if spec.get("diameter") else "") + "{value}"
    modifier = {"M": "mmc", "L": "lmc"}.get(spec.get("modifier", ""), "")
    cells: list[tuple[str, ...]] = [("glyph", char.glyph), ("value", value, modifier)]
    cells += [("letter", letter) for letter in spec.get("datums", [])]
    return cells


def draw_gdt(ctx: Context, spec: dict[str, Any]) -> tuple[str, str]:
    letters = spec.get("datums", [])
    drawn = ctx.extras.get("datums_drawn", set())
    for letter in letters:
        if letter not in drawn:
            have = ", ".join(sorted(drawn)) or "none"
            msg = f"datum {letter} is not on this sheet (datums drawn: {have}): add a datum item with letter {letter}"
            raise ItemError(msg)
    cells = [(c[0], c[1].replace("{value}", _value(ctx, spec["value"])), *c[2:]) for c in _cells(spec)]
    summary = f"{spec['char']} {_value(ctx, spec['value'])}" + (f" to {'-'.join(letters)}" if letters else "")
    if "on" in spec:
        return _hang(ctx, spec, cells, summary)
    face_id = spec["face"][0]
    att = attach_face(ctx, face_id, spec.get("view"), spec.get("side"))

    def build(anchor: Point, direction: tuple[float, float]) -> tuple[list[Entity], tuple[float, float, float, float]]:
        boxed, rect = _frame_at(anchor, direction, cells)
        tip = att.tip_towards(direction)
        entities: list[Entity] = [
            Line(tip[0], tip[1], anchor[0], anchor[1], DIMENSIONS),
            arrow(tip, (-direction[0], -direction[1])),
            *boxed,
        ]
        return entities, rect

    entities, free = place_label(ctx, att, build, spec.get("offset_mm"), asked(spec))
    ctx.add(entities)
    text = f"{summary} on {face_id} in the {att.view.name} view"
    return ("ok", text) if free else ("warning", f"{text}; it overlaps other things: move it with offset_mm or side")


def _value(ctx: Context, value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text.replace(".", ",") if ctx.comma else text


def _clear(ctx: Context, rect: tuple[float, float, float, float]) -> bool:
    """Free of other labels and of the views (a frame lying on the outline of a view is a collision)."""
    boxes = list(ctx.extras.get("view_boxes", [])) or [v.box for v in ctx.views.values()]
    return ctx.free(rect) and not any(
        rect[0] < b[2] and b[0] < rect[2] and rect[1] < b[3] and b[1] < rect[3] for b in boxes
    )


def _hang(ctx: Context, spec: dict[str, Any], cells: list[tuple[str, ...]], summary: str) -> tuple[str, str]:
    """Hang the frame under the shelf of the callout of the dimension item ``on``; when there is no room under it, it
    continues the shelf at its far end (ISO 1101 allows a frame joined to the end of the leader line) or stands on the
    callout's text."""
    target = spec["on"]
    anchors = ctx.extras.get("anchors", {}).get(target)
    if not anchors:
        msg = (
            f"item {target!r} has no callout with a leader on this sheet to hang a frame from (a hole_dim, or a dim of "
            "kind radius, diameter or chamfer, drawn without error); for a linear dimension use face= instead"
        )
        raise ItemError(msg)
    anchor = anchors[0]
    elbow, direction = anchor["elbow"], anchor["dir"]
    _, width = frame(0.0, 0.0, cells)
    x0 = elbow[0] if direction > 0 else elbow[0] - width
    stub_x = elbow[0] + direction * 3.0
    first: tuple[list[Entity], tuple[float, float, float, float]] | None = None
    free = False
    for step in range(4):
        y0 = elbow[1] - 2.5 - CELL_MM - step * (CELL_MM + 1.5)
        boxed, _ = frame(x0, y0, cells)
        entities: list[Entity] = [Line(stub_x, elbow[1], stub_x, y0 + CELL_MM, DIMENSIONS), *boxed]
        rect = (x0, y0, x0 + width, elbow[1])
        if first is None:
            first = (entities, rect)
        if _clear(ctx, (x0, y0, x0 + width, y0 + CELL_MM)):
            free = True
            break
    assert first is not None
    chosen = (entities, rect) if free else first
    if not free:  # nothing free under the shelf (the callout stands on a view): beside it or over its text
        text_rect = anchor["rect"]
        end = elbow[0] + direction * (text_rect[2] - text_rect[0])
        far_x = end if direction > 0 else end - width
        over = max(text_rect[1], text_rect[3]) + 1.0
        for x0, y0 in ((far_x, elbow[1] - CELL_MM / 2), (min(text_rect[0], text_rect[2]), over)):
            box = (x0, y0, x0 + width, y0 + CELL_MM)
            if ctx.inside(box) and _clear(ctx, box):
                chosen, free = (frame(x0, y0, cells)[0], box), True
                break
    ctx.reserve((chosen[1][0], chosen[1][1], chosen[1][2], chosen[1][1] + CELL_MM))
    ctx.add(chosen[0])
    text = f"{summary} hung from the callout of {target}"
    return (
        ("ok", text)
        if free
        else ("warning", f"{text}; it overlaps other things: move the callout with offset_mm or side")
    )


ITEMS = (
    ItemType("datum", "dt", check_datum, draw_datum, frozenset(DATUM_CHECKS), prepare_datum, phase=1),
    ItemType("gdt", "gt", check_gdt, draw_gdt, frozenset(GDT_CHECKS), phase=2),
)
