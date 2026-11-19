"""Pure, FreeCAD-free colour bookkeeping for ``gui.highlight_entities``/``gui.clear_highlight`` (the
agent-to-engineer counterpart of the CAD-selection feature): computing a per-face/
per-edge colour list with some indices swapped for a highlight colour, and remembering enough of the
ORIGINAL list per object to put it back exactly. Restore must never guess a background colour -- a body
may already carry per-face colours (a multi-coloured part) or the FreeCAD default (this module never
needs to know which, it only ever replays back whatever it first read).

No ``FreeCAD``/``FreeCADGui``/``PySide`` import on purpose, unlike the rest of ``worker/gui.py`` (which
cannot even be imported without the real FreeCAD ``App``/``Gui`` modules): this file alone is directly
unit-testable with plain pytest -- the same reasoning ``debounce.py`` documents for itself.
"""

from collections.abc import Sequence
from typing import Any

RGBA = tuple[float, float, float, float]
"""``(r, g, b, a)``, all 0..1; ``a`` is the OPACITY (1 opaque), as FreeCAD 1.1 reads it (see ``appearance``)."""

__all__ = ["HighlightState", "base_index", "expand_to_length", "recolor", "restore_value"]


class HighlightState:
    """Every worker's temporary recolouring, keyed by ``(document_name, object_name)``: a single ``object_name``-only
    key let two documents (a worker may hold several open at once, ``registry.DocumentRec``/``main.py``'s
    ``self.documents``) clobber or leak into each other's entries whenever they happened to share an object name (e.g.
    both Tips named ``"Pad"``). Every read/forget below is scoped to ONE document by name, so acting on document B can
    never disturb, restore, or drop document A's still-pending highlight."""

    def __init__(self) -> None:
        self._originals: dict[tuple[str, str, str], tuple[Any, ...]] = {}

    def remember(self, document_name: str, object_name: str, attr: str, original: Sequence[Any]) -> None:
        """Record ``object_name``'s (in ``document_name``) ``attr`` original value, once per object per attribute per
        round -- a second ``remember`` for the same key this round is a no-op (the FIRST read is the true
        original; a later one would already be showing the highlight colour). The faces and the edges of one object
        are two attributes, each with its own original: keyed by the object alone, painting both in one call forgot
        the edges' original. ``original`` is whatever the caller restores with (colours, or materials)."""
        key = (document_name, object_name, attr)
        if key not in self._originals:
            self._originals[key] = tuple(original)

    def entries_for(self, document_name: str) -> list[tuple[str, str, tuple[Any, ...]]]:
        """``(object_name, attr, original)`` for every entry remembered for ``document_name`` -- read-only,
        does NOT remove anything. Fresh review fix: the caller (``gui.clear_highlight``) restores one
        entry at a time and calls ``discard`` only once THAT entry's own restore actually succeeded, so a
        failure on one object never loses the others (the old ``pop_all`` cleared every entry up front,
        before any restore was attempted, so a failure partway through lost the rest forever)."""
        return [
            (obj_name, attr, colors)
            for (doc_name, obj_name, attr), colors in self._originals.items()
            if doc_name == document_name
        ]

    def discard(self, document_name: str, object_name: str, attr: str | None = None) -> None:
        """Forget one entry (``attr`` given) or every entry of the object -- call only once it no longer needs
        restoring: its colours were put back successfully, or there is nothing left in FreeCAD to restore a colour
        on (object/document gone)."""
        for key in [k for k in self._originals if k[:2] == (document_name, object_name) and attr in (None, k[2])]:
            del self._originals[key]

    def discard_document(self, document_name: str) -> None:
        """Forget every entry of ``document_name`` WITHOUT attempting to restore anything -- the document
        is being deleted/closed (``gui``'s ``slotDeletedDocument`` hook): there is nothing left in FreeCAD
        to restore a colour on, so this is the one case where discarding without restoring is correct."""
        for key in [key for key in self._originals if key[0] == document_name]:
            del self._originals[key]

    def __bool__(self) -> bool:
        return bool(self._originals)


def recolor(current: Sequence[RGBA], indices: Sequence[int], color: RGBA) -> list[RGBA]:
    """A copy of ``current`` with every position named in ``indices`` replaced by ``color``. An
    out-of-range index is skipped, never raised: a stale index racing a recompute should paint one
    entity fewer, not blow up the whole highlight. ``current`` must already have one entry per
    face/edge (``expand_to_length`` below) -- painting an index into a shorter, "uniform colour" list
    would silently recolour the WHOLE shape instead of just that one element."""
    result = list(current)
    for index in indices:
        if 0 <= index < len(result):
            result[index] = color
    return result


def expand_to_length(current: Sequence[RGBA], length: int) -> list[RGBA]:
    """A colour list of exactly ``length`` entries, built from ``current`` (confirmed against
    real FreeCAD): a freshly read ``DiffuseColor``/``LineColorArray``
    is a length-1 list (ONE "uniform" colour for the whole shape) until it is explicitly given one entry
    per face/edge, and stays exactly that length after being set -- FreeCAD never auto-resizes it, even
    across a topology change (see ``restore_value`` below). Painting index ``i`` directly into that
    length-1 list would set the shape's ONE colour, recolouring everything, not just element ``i``
    (verified: this was the actual bug the first version of ``highlight_entities`` shipped with). When
    ``current`` already has ``length`` entries it is returned as-is; otherwise every position is filled
    with ``current``'s last entry (its only entry, in the common length-1 case) -- the uniform colour
    FreeCAD itself means by a short list, made explicit so ``recolor`` can safely target one index."""
    if len(current) == length:
        return list(current)
    fill = current[-1] if current else (1.0, 1.0, 1.0, 1.0)
    return [fill] * length


def base_index(colours: Sequence[RGBA]) -> int:
    """The index of the colour most faces show (the first of them on a tie): the colour of the object itself in a
    per-face list, what a list that no longer fits the solid falls back to. ``0`` for an empty list."""
    seen: dict[tuple[int, int, int], list[int]] = {}
    for index, colour in enumerate(colours):
        seen.setdefault((round(colour[0] * 255), round(colour[1] * 255), round(colour[2] * 255)), []).append(index)
    if not seen:
        return 0
    return max(seen.values(), key=len)[0]


def restore_value(original: Sequence[RGBA], current_length: int) -> list[RGBA]:
    """The value to assign back to a ``ViewObject`` colour property (``DiffuseColor``/``LineColorArray``):
    ``original`` verbatim when it is a length-1 "uniform colour" (always valid, whatever the object's
    current face/edge count -- confirmed against real FreeCAD: that is exactly what a freshly created
    shape starts with, regardless of how many faces it has) or the object still has exactly as many
    faces/edges as when ``original`` was recorded (``current_length`` -- the shape's REAL current
    topological count, e.g. ``len(obj.Shape.Faces)``, never the property's own live length: FreeCAD leaves
    a stale per-element list exactly as it was across a topology change instead of resizing it, confirmed
    against real FreeCAD, so comparing against the property itself would never detect the mismatch).
    Otherwise an EMPTY list -- FreeCAD's own convention for "use the object's single base colour" --
    since assigning a colour list of the WRONG length either raises inside FreeCAD or silently corrupts
    the per-element colouring; the empty list is the one value that is always valid regardless of the
    object's current element count, so this never raises and never leaves the object highlighted."""
    if len(original) == 1 or len(original) == current_length:
        return list(original)
    return []
