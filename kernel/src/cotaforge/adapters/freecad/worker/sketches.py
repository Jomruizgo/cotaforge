"""A sketch or a datum a feature has consumed is not left showing in the 3D view.

FreeCAD's own commands hide a sketch once the pad or pocket built on it exists; features made through the API do not,
so every consumed sketch stayed drawn over the part, and so did the datum planes, lines and points the ops make for a
revolution, a pattern or a mirror. A reference no feature uses yet (the sketch being worked on, a plane waiting for its
sketch) stays shown. A designer hides what the model no longer needs on screen; so does the harness.

Consumed means: a feature is built on it, or on a sketch that hangs on it (a sketch attached to a datum plane), or on a
datum that hangs on it (a plane offset from another plane). The origin planes and axes of a body are never tidied: they
follow FreeCAD's own default.

What the engineer did by hand wins: ``hide_consumed`` hides only what became consumed in the step (``already`` is what
was consumed before it), so a reference the engineer showed is not hidden again by the next step.

``Visibility`` of the object, which the visible worker mirrors in its window and a headless one keeps in the file, is a
display property: never a change of the design (``changes.DISPLAY_ONLY``). In the window it does flip the document's
modified flag, so ``gui.unmodified`` puts it back: opening a document and finding old sketches showing must not turn the
document into one with unsaved changes. Datums are tidied after an op or a fragment, not when a file is opened: a plane
the engineer left showing in his own file stays as he left it.
"""

from __future__ import annotations

from typing import Any

import gui
import sketch_defined

SKETCH_TYPE = sketch_defined.SKETCH_TYPE
DATUM_TYPE = "Part::Datum"
ORIGIN_TYPES = ("App::Plane", "App::Line", "App::Point")
"""The planes, axes and point of a body's origin: shown or hidden on request, never tidied by themselves."""
_consumes = sketch_defined.consumes  # one definition of "a feature built on a sketch" for this and the defined-rule


def is_reference(obj: Any) -> bool:
    """A sketch, a datum (plane, line, point, coordinate system) or an origin plane, axis or point of a body: what the
    engineer shows or hides by name."""
    return (
        obj.isDerivedFrom(SKETCH_TYPE)
        or obj.isDerivedFrom(DATUM_TYPE)
        or any(obj.isDerivedFrom(kind) for kind in ORIGIN_TYPES)
    )


def _is_used(obj: Any, used_sketches: dict[str, bool], seen: frozenset[str]) -> bool:
    """Whether a datum was used by a feature, directly or through a consumed sketch or a datum that hangs on it."""
    name = str(obj.Name)
    if name in seen:
        return False
    seen = seen | {name}
    for user in obj.InList:
        if _consumes(user):
            return True
        if user.isDerivedFrom(SKETCH_TYPE):
            if used_sketches.get(str(user.Name)):
                return True
        elif user.isDerivedFrom(DATUM_TYPE) and _is_used(user, used_sketches, seen):
            return True
    return False


def consumed(fc_doc: Any, datums: bool = True) -> list[Any]:
    """The sketches of the document that some feature is built on and, with ``datums``, the datum planes, lines and
    points a feature, a consumed sketch or a consumed datum hangs on."""
    objects = list(fc_doc.Objects)
    used = {
        str(obj.Name): any(_consumes(user) for user in obj.InList) for obj in objects if obj.isDerivedFrom(SKETCH_TYPE)
    }
    found = [obj for obj in objects if used.get(str(obj.Name))]
    if datums:
        found.extend(obj for obj in objects if obj.isDerivedFrom(DATUM_TYPE) and _is_used(obj, used, frozenset()))
    return found


def consumed_names(fc_doc: Any, datums: bool = True) -> set[str]:
    return {str(obj.Name) for obj in consumed(fc_doc, datums)}


def hide_consumed(fc_doc: Any, already: set[str] | None = None, datums: bool = True) -> list[str]:
    """Hide every consumed sketch (and datum) that is shown; the names of the ones hidden. ``already``: what was
    consumed before the step: if it is shown now, the engineer or a fragment showed it on purpose and it stays. A
    document that was not modified is still not modified afterwards."""
    shown = [
        obj
        for obj in consumed(fc_doc, datums)
        if bool(getattr(obj, "Visibility", False)) and str(obj.Name) not in (already or ())
    ]
    if not shown:
        return []
    with gui.unmodified(fc_doc):
        for obj in shown:
            obj.Visibility = False
    return [str(obj.Name) for obj in shown]


def find_reference(fc_doc: Any, name: str) -> Any | None:
    """The sketch, datum or origin feature called ``name`` (its internal name, else its label)."""
    obj = fc_doc.getObject(name)
    if obj is None:
        matches = [o for o in fc_doc.getObjectsByLabel(name) if is_reference(o)]
        obj = matches[0] if len(matches) == 1 else None
    return obj if obj is not None and is_reference(obj) else None


def set_visibility(fc_doc: Any, names: list[str], visible: bool) -> dict[str, list[str]]:
    """Show or hide references by name (display only): which changed, which were already so, which are no reference
    of the document. Nothing is removed or touched but ``Visibility``."""
    changed: list[str] = []
    already: list[str] = []
    unknown: list[str] = []
    targets: list[tuple[str, Any]] = []
    for name in names:
        obj = find_reference(fc_doc, name)
        if obj is None:
            unknown.append(name)
        elif bool(getattr(obj, "Visibility", False)) == visible:
            already.append(name)
        else:
            targets.append((name, obj))
    if targets:
        with gui.unmodified(fc_doc):
            for name, obj in targets:
                obj.Visibility = visible
                changed.append(name)
    return {"changed": changed, "already": already, "unknown": unknown}
