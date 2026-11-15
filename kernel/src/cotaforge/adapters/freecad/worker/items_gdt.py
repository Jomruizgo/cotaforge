# ruff: noqa: E501
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

from gdt_frames import (
    DATUM_CELL_MM,
    DATUM_STRETCH_MM,
    DATUM_STRETCHES,
    SYMBOL_CELL_MM,
    GdtFrameError,
    datum_file,
    frame_width,
    gdt_frame_choice,
    library_files,
)
from items_attach import Placed, _point_segment, attach_face, place_label
from items_common import FRAME_CELL_MM, asked
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
    v_point2,
    v_view_or_section,
    v_str,
)
from sheet_draw import arrow
from sheet_model import DIMENSIONS, Entity, Glyph, Line, Polyline, Solid, Text, text_width
from sheet_units import NativeDim, NativeSymbol

CELL_MM = FRAME_CELL_MM  # height of a frame; a symbol or datum cell is this wide
TEXT_MM = 3.5
GLYPH_MM = 4.6
MODIFIER_MM = 4.0
DATUM_BASE_MM = 6.4  # the datum symbol of the library (ISO 5459): the filled triangle ...
DATUM_HEIGHT_MM = 5.5426
DATUM_STEM_MM = 8.9596  # ... the stem from its apex to the frame ...
DATUM_BOX_MM = 7.0  # ... and the square frame of the letter
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


def frame(
    x: float, y: float, cells: list[tuple[str, ...]], widths: list[float] | None = None
) -> tuple[list[Entity], float]:
    """A frame with its bottom-left corner at ``(x, y)``; ``cells`` are ``("glyph", name)``, ``("value", text, modifier
    glyph or "")`` and ``("letter", L)``. ``widths`` are the widths of the cells when the library symbol the frame is on the
    page decides them (``library_frame``). Returns the entities and the width."""
    out: list[Entity] = []
    sizes: list[float] = []
    for index, cell in enumerate(cells):
        if widths is not None:
            sizes.append(widths[index])
        elif cell[0] == "value":
            extra = MODIFIER_MM + 1.0 if cell[2] else 0.0
            sizes.append(text_width(cell[1], TEXT_MM) + extra + 2 * FRAME_PAD_MM)
        else:
            sizes.append(CELL_MM)
    total = sum(sizes)
    out.append(Polyline(((x, y), (x + total, y), (x + total, y + CELL_MM), (x, y + CELL_MM)), DIMENSIONS, closed=True))
    left = x
    for index, (cell, width) in enumerate(zip(cells, sizes, strict=True)):
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


@dataclass(frozen=True)
class Library:
    """The file of the symbol library a frame is on the page, with the values of its editable texts and the widths of its cells."""

    file: str
    texts: tuple[str, ...]
    widths: list[float]


def library_frame(ctx: Context, spec: dict[str, Any]) -> Library:
    """The file of ``gdt_frames`` for the frame of ``spec`` (the one the engineer would insert from TechDraw's menu) and the
    texts to put in it. The sheet's headless drawing takes its cell widths from the file, so what it reserves is what the
    window shows."""
    modifiers = [name for name in ("diameter", spec.get("modifier", "")) if name and (name != "diameter" or spec.get("diameter"))]
    datums = list(spec.get("datums", []))
    try:
        relative, texts = gdt_frame_choice(
            spec["char"], spec["value"], modifiers, datums, decimal="," if ctx.comma else "."
        )
        total = frame_width(library_files()[relative])
    except GdtFrameError as exc:
        raise ItemError(str(exc)) from exc
    count = len(datums)
    return Library(relative, texts, [SYMBOL_CELL_MM, total - SYMBOL_CELL_MM - DATUM_CELL_MM * count, *[DATUM_CELL_MM] * count])


def _frame_at(
    anchor: Point, direction: tuple[float, float], cells: list[tuple[str, ...]], widths: list[float] | None = None
) -> tuple[list[Entity], tuple[float, float, float, float]]:
    """A frame joined to ``anchor`` by the middle of the side facing the feature (``direction`` points away from it)."""
    probe, width = frame(0.0, 0.0, cells, widths)
    del probe
    if abs(direction[0]) >= abs(direction[1]):
        x0 = anchor[0] if direction[0] > 0 else anchor[0] - width
        y0 = anchor[1] - CELL_MM / 2
    else:
        x0 = anchor[0] - width / 2
        y0 = anchor[1] if direction[1] > 0 else anchor[1] - CELL_MM
    entities, _ = frame(x0, y0, cells, widths)
    return entities, (x0, y0, x0 + width, y0 + CELL_MM)


def _view_of(ctx: Context, target: str) -> str | None:
    """The TechDraw view the callout of item ``target`` is on (its native dimension's): what a symbol hung from it belongs to."""
    for native in ctx.extras.get("natives", {}).get(target, []):
        if isinstance(native, NativeDim):
            return native.view
    return None


# --- datum ------------------------------------------------------------------------------------------------------------

DATUM_CHECKS = {
    "letter": v_letter,
    "face": v_one_face,
    "on": v_str(24),
    "view": v_view_or_section,  # a standard view or a section view (A-A)
    "side": COMMON_FIELDS["side"],
    "offset_mm": COMMON_FIELDS["offset_mm"],
    "tip_at": v_point2,  # where the global layout left the triangle on the face (page mm): written by it, not asked for
}


TIP_ON_TRACE_MM = 1.0  # a planned tip is on the trace of its face (the views moved since: it is searched again)
CARDINAL = {"up": (0.0, 1.0), "down": (0.0, -1.0), "left": (-1.0, 0.0), "right": (1.0, 0.0)}  # page, y up


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


def _cardinal(direction: tuple[float, float]) -> str:
    """The side of the feature the frame of a datum symbol is on: the library has the four (the triangle stands on the feature
    with its base and the stem runs straight to the frame)."""
    if abs(direction[0]) >= abs(direction[1]):
        return "right" if direction[0] > 0 else "left"
    return "up" if direction[1] > 0 else "down"


def datum_entities(
    origin: Point, side: str, letter: str, stretch: int = 0
) -> tuple[Placed, tuple[float, float, float, float]]:
    """The datum symbol of the library drawn on the sheet: the filled triangle stands on the feature with the middle of its base
    at ``origin``, the stem runs from its apex to the framed letter, on ``side`` of the feature. Returns the entities and the
    box of the frame (what the label takes)."""
    dx, dy = CARDINAL[side]
    px, py = -dy, dx

    def at(along: float, across: float) -> Point:
        return origin[0] + along * dx + across * px, origin[1] + along * dy + across * py

    near = DATUM_HEIGHT_MM + DATUM_STEM_MM + stretch * DATUM_STRETCH_MM
    half = DATUM_BOX_MM / 2
    corners = (at(near, -half), at(near + DATUM_BOX_MM, -half), at(near + DATUM_BOX_MM, half), at(near, half))
    base = (at(0.0, -DATUM_BASE_MM / 2), at(0.0, DATUM_BASE_MM / 2))
    apex = at(DATUM_HEIGHT_MM, 0.0)
    stem_end = at(near, 0.0)
    centre = at(near + half, 0.0)
    out = Placed(
        [
            Solid((base[0], base[1], apex), DIMENSIONS),
            Line(apex[0], apex[1], stem_end[0], stem_end[1], DIMENSIONS),
            Polyline(corners, DIMENSIONS, closed=True),
            Text(centre[0], centre[1] - 1.3, letter, TEXT_MM, DIMENSIONS, "center"),
        ]
    )
    xs = [p[0] for p in corners]
    ys = [p[1] for p in corners]
    box = (min(xs), min(ys), max(xs), max(ys))  # the label: the framed letter (the triangle stands on the feature, the stem is a line)
    out.origin, out.side, out.rect, out.reach, out.stretch = origin, side, box, stem_end, stretch
    return out, box


def _native_datum(ctx: Context, spec: dict[str, Any], placed: Placed, view: str | None) -> None:
    """The datum as the library's own symbol of FreeCAD's TechDraw (its letter is the editable text), owned by ``view``."""
    if view is None:
        return
    ctx.used_view(view)
    ctx.native(
        NativeSymbol(
            item=str(spec["id"]),
            name="s",
            kind="datum",
            file=datum_file(placed.side, placed.stretch),
            texts=(spec["letter"],),
            at=placed.origin,
            view=view,
        )
    )


def _datum_on_callout(ctx: Context, spec: dict[str, Any]) -> tuple[str, str]:
    """The datum of an AXIS: its triangle stands on the end of the shelf of the callout that dimensions the feature (ISO
    5459: in line with the dimension), the framed letter below it."""
    target = spec["on"]
    anchors = ctx.extras.get("anchors", {}).get(target)
    if not anchors:
        msg = (
            f"item {target!r} has no callout with a leader on this sheet to carry the datum (a hole_dim, or a dim of "
            "kind diameter or radius, drawn without error)"
        )
        raise ItemError(msg)
    elbow, direction, rect = anchors[0]["elbow"], anchors[0]["dir"], anchors[0]["rect"]
    length = rect[2] - rect[0]
    free = False
    first: tuple[Placed, tuple[float, float, float, float]] | None = None
    for step in range(4):  # along the shelf, from its end back towards the elbow, while the place is taken
        origin = (elbow[0] + direction * max(length - 3.0 - step * (DATUM_BOX_MM + 1.5), DATUM_BOX_MM / 2), elbow[1])
        placed, box = datum_entities(origin, "down", spec["letter"])
        if first is None:
            first = (placed, box)
        if ctx.free(box):
            free = True
            break
    assert first is not None
    chosen, box = (placed, box) if free else first
    ctx.reserve(box)
    ctx.add(chosen)
    _native_datum(ctx, spec, chosen, _view_of(ctx, target))
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
    placed = spec.get("tip_at")
    if placed is not None and att.trace is not None:  # the layout slid the triangle along the face: it stays where it left it
        point = (float(placed[0]), float(placed[1]))
        if _point_segment(point, att.trace[0], att.trace[1]) <= TIP_ON_TRACE_MM:
            att.tip = point

    def build(anchor: Point, direction: tuple[float, float]) -> tuple[list[Entity], tuple[float, float, float, float]]:
        tip = att.tip_towards(direction)
        far = ((anchor[0] - tip[0]) ** 2 + (anchor[1] - tip[1]) ** 2) ** 0.5
        # the frame stands where the label was asked to (its near edge ``far`` from the face), as near as the lengths of stem allow
        stretch = min(DATUM_STRETCHES - 1, max(0, round((far - DATUM_HEIGHT_MM - DATUM_STEM_MM) / DATUM_STRETCH_MM)))
        entities, rect = datum_entities(tip, _cardinal(direction), letter, stretch)
        return entities, rect

    entities, free = place_label(ctx, att, build, spec.get("offset_mm"), asked(spec))
    ctx.add(entities)
    ctx.extras.setdefault("datums_drawn", set()).add(letter)
    _native_datum(ctx, spec, entities, att.view.name)  # type: ignore[arg-type]
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
    "view": v_view_or_section,  # a standard view or a section view (A-A)
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


def _native_frame(
    ctx: Context,
    spec: dict[str, Any],
    library: Library,
    rect: tuple[float, float, float, float],
    view: str | None,
    leader: tuple[Point, ...],
) -> None:
    """The frame as the library's own symbol (its values are the editable texts the engineer changes in the property editor),
    owned by ``view`` and joined to the feature by a native leader line."""
    if view is None:
        return
    ctx.used_view(view)
    ctx.native(
        NativeSymbol(
            item=str(spec["id"]),
            name="f",
            kind="frame",
            file=library.file,
            texts=library.texts,
            at=(rect[0], rect[3]),
            view=view,
            leader=leader,
        )
    )


def draw_gdt(ctx: Context, spec: dict[str, Any]) -> tuple[str, str]:
    letters = spec.get("datums", [])
    drawn = ctx.extras.get("datums_drawn", set())
    for letter in letters:
        if letter not in drawn:
            have = ", ".join(sorted(drawn)) or "none"
            msg = f"datum {letter} is not on this sheet (datums drawn: {have}): add a datum item with letter {letter}"
            raise ItemError(msg)
    library = library_frame(ctx, spec)
    cells = [(c[0], c[1].replace("{value}", _value(ctx, spec["value"])), *c[2:]) for c in _cells(spec)]
    summary = f"{spec['char']} {_value(ctx, spec['value'])}" + (f" to {'-'.join(letters)}" if letters else "")
    if "on" in spec:
        return _hang(ctx, spec, cells, library, summary)
    face_id = spec["face"][0]
    att = attach_face(ctx, face_id, spec.get("view"), spec.get("side"))

    def build(anchor: Point, direction: tuple[float, float]) -> tuple[list[Entity], tuple[float, float, float, float]]:
        boxed, rect = _frame_at(anchor, direction, cells, library.widths)
        tip = att.tip_towards(direction)
        entities = Placed(
            [
                Line(tip[0], tip[1], anchor[0], anchor[1], DIMENSIONS),
                arrow(tip, (-direction[0], -direction[1])),
                *boxed,
            ]
        )
        entities.tip, entities.anchor, entities.rect = tip, anchor, rect
        return entities, rect

    entities, free = place_label(ctx, att, build, spec.get("offset_mm"), asked(spec))
    ctx.add(entities)
    _native_frame(ctx, spec, library, entities.rect, att.view.name, (entities.tip, entities.anchor))  # type: ignore[attr-defined]
    text = f"{summary} on {face_id} in the {att.view.name} view"
    return ("ok", text) if free else ("warning", f"{text}; it overlaps other things: move it with offset_mm or side")


def _value(ctx: Context, value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text.replace(".", ",") if ctx.comma else text


def _hang(
    ctx: Context, spec: dict[str, Any], cells: list[tuple[str, ...]], library: Library, summary: str
) -> tuple[str, str]:
    """Hang the frame from the shelf of the callout of the dimension item ``on``, under the size: its top edge is the
    shelf line, as ISO 1101 draws it (a second frame goes under the first). The room was kept when the callout was
    placed."""
    target = spec["on"]
    anchors = ctx.extras.get("anchors", {}).get(target)
    if not anchors:
        msg = (
            f"item {target!r} has no callout with a leader on this sheet to hang a frame from (a hole_dim, or a dim of "
            "kind radius, diameter or chamfer, drawn without error); for a linear dimension use face= instead"
        )
        raise ItemError(msg)
    elbow, direction = anchors[0]["elbow"], anchors[0]["dir"]
    _, width = frame(0.0, 0.0, cells, library.widths)
    x0 = elbow[0] if direction > 0 else elbow[0] - width
    hung = ctx.extras.setdefault("frames_hung", {})
    y0 = elbow[1] - (hung.get(target, 0) + 1) * CELL_MM
    hung[target] = hung.get(target, 0) + 1
    boxed, _ = frame(x0, y0, cells, library.widths)
    box = (x0, y0, x0 + width, y0 + CELL_MM)
    slot = ctx.extras.get("frame_slots", {}).get(target)
    free = (
        slot is not None and box[0] >= slot[0] - 1e-6 and box[2] <= slot[2] + 1e-6 and box[1] >= slot[1] - 1e-6
    ) or (ctx.free(box))
    ctx.reserve(box)
    ctx.add(boxed)
    _native_frame(ctx, spec, library, box, _view_of(ctx, target), ())  # the frame touches the shelf of the callout: no leader
    text = f"{summary} hung from the callout of {target}"
    return (
        ("ok", text)
        if free
        else ("warning", f"{text}; it overlaps other things: move the callout with offset_mm or side")
    )


def prepare_gdt(ctx: Context, specs: list[dict[str, Any]]) -> None:
    """Before anything is drawn: the room each callout keeps under its shelf for the frames that hang from it, and the
    holes a position frame tolerates (the dimensions that locate them are theoretically exact: boxed)."""
    room: dict[str, tuple[float, int]] = {}
    for spec in specs:
        if "on" not in spec:
            continue
        try:
            width = sum(library_frame(ctx, spec).widths)
        except (KeyError, ItemError):
            continue
        old_width, count = room.get(spec["on"], (0.0, 0))
        room[spec["on"]] = (max(old_width, width), count + 1)
    ctx.extras["frame_room"] = room
    holes = ctx.extras.setdefault("ted_holes", set())
    from items_holes import _select  # noqa: PLC0415 - items_holes imports the sheet types this module is loaded with

    others = {str(i["id"]): i for i in ctx.extras.get("sheet_items", []) if i.get("type") == "hole_dim"}
    for spec in specs:
        target = others.get(str(spec.get("on")))
        if spec.get("char") != "position" or target is None:
            continue
        try:
            holes |= {h.index for h in _select(ctx, target)}
        except ItemError:
            continue


ITEMS = (
    ItemType("datum", "dt", check_datum, draw_datum, frozenset(DATUM_CHECKS), prepare_datum, phase=1),
    ItemType("gdt", "gt", check_gdt, draw_gdt, frozenset(GDT_CHECKS), prepare_gdt, phase=2),
)
