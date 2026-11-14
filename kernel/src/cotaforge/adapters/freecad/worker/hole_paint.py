# ruff: noqa: E501
"""What a body's per-face colour list becomes when its holes are coloured (no FreeCAD import: unit-testable).

A face colour is a list with one ``(r, g, b, a)`` per face, indexed by face number (``a`` is the OPACITY, 1 opaque: see ``appearance``), and FreeCAD leaves it exactly as it was
when a later feature changes the solid (confirmed on 1.1.3, see ``highlight.expand_to_length``): after a fillet the list is
stale. So the colours of the holes are re-derived from the registry (which follows the faces through every operation, see
``hole_colours.face_colours``) and written again whenever the list is not what they need.

The engineer's own colour is respected: a face is only repainted while it still shows the base colour of the object or a
colour this harness put there. A face the engineer painted another colour is left alone."""

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

RGBA = tuple[float, float, float, float]
Rgb = tuple[int, int, int]


def to_rgba(colour: Rgb, alpha: float = 1.0) -> RGBA:
    """A convention colour (0..255) as a face colour (0..1; ``alpha`` is the OPACITY, 1 opaque: see ``appearance``)."""
    return (colour[0] / 255.0, colour[1] / 255.0, colour[2] / 255.0, alpha)


def key(colour: Sequence[float]) -> Rgb:
    """The 0..255 bytes of a colour, to compare colours without float noise."""
    return (round(colour[0] * 255), round(colour[1] * 255), round(colour[2] * 255))


def base_of(current: Sequence[RGBA], ours: Iterable[Rgb], fallback: RGBA) -> RGBA:
    """The colour of the object itself: the one most faces of ``current`` show that is not one of ours, ``fallback`` (the
    object's own ``ShapeColor``) when every face is. FreeCAD's ``ShapeColor`` follows the FIRST face of a per-face list, so
    once face 0 is a hole it is the hole's colour and not the part's."""
    mine = {tuple(c) for c in ours}
    seen: dict[Rgb, list[Any]] = {}
    for colour in current:
        shown = key(colour)
        if shown not in mine:
            seen.setdefault(shown, []).append(colour)
    if not seen:
        return fallback
    most = max(seen.values(), key=len)  # the earliest of the most frequent: max keeps the first on a tie
    return tuple(most[0])  # type: ignore[return-value]


def plan(
    current: Sequence[RGBA],
    face_count: int,
    wanted: Mapping[int, Rgb],
    ours: Iterable[Rgb],
    base: RGBA,
) -> list[RGBA] | None:
    """The colour list to write, or ``None`` when the one the object has is already right.

    ``current`` is the list as FreeCAD gives it (one entry = one uniform colour; ``face_count`` entries = per face; any other
    length is stale and means nothing), ``wanted`` maps face index -> colour of the hole class, ``ours`` every colour this
    harness may have painted before (a face still showing one is not the engineer's), ``base`` the colour of the object."""
    mine = {tuple(c) for c in ours}
    if len(current) == face_count:
        faces = [tuple(c) for c in current]  # type: ignore[misc]
    elif len(current) == 1:
        faces = [tuple(current[0])] * face_count  # type: ignore[list-item]
    else:  # stale after a change of the solid: it says nothing about any face
        faces = [base] * face_count
    base_key = key(base)
    result: list[RGBA] = []
    for index, shown in enumerate(faces):
        shown_key = key(shown)
        free = shown_key == base_key or shown_key in mine  # not painted by the engineer
        if index in wanted and free:
            result.append(to_rgba(wanted[index], base[3]))
        elif index not in wanted and free and shown_key != base_key:
            result.append(base)  # a colour of ours on a face that is no longer a hole's (class changed, hole removed)
        else:
            result.append(shown)  # type: ignore[arg-type]
    unchanged = all(key(a) == key(b) for a, b in zip(faces, result, strict=True))
    if unchanged and len(current) in (1, face_count):
        return None
    if not wanted and all(key(c) == base_key for c in result):
        return []  # nothing of ours left: FreeCAD's "one colour for the object"
    return result
