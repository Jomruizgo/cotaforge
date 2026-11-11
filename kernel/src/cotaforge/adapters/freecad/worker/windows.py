"""Which documents have a window: an assembly needs its parts LOADED, not shown (``gui.py`` has the measurements).

The engineer works in the window of the document the agent works on; a part that is saved and inside an assembly does
not need one. So:

- a part the harness loads for an assembly (``open_document`` with ``hidden``) never gets a window;
- a part the agent made, once an assembly uses it and it is saved, loses its window (``release``): the document stays
  loaded, its handle still inserts it again and the assembly still links to it;
- a document that has no window gets one when it becomes the active one (``show``): editing a part from the assembly
  is ``switch`` to it, edit, ``save``, ``switch`` back (the part's window closes by itself).

A document the engineer had open already (``borrowed``) and any with unsaved changes are never touched.
"""

from typing import Any

import gui


def used_by_assembly(doc: Any, documents: dict[str, Any]) -> bool:
    """Whether a loaded assembly has an instance of ``doc``."""
    return any(
        other is not doc and other.kind == "assembly" and any(c.part_doc == doc.id for c in other.components.values())
        for other in documents.values()
    )


def can_release(doc: Any, active: Any, documents: dict[str, Any]) -> bool:
    """A part nobody is editing (not the active document), saved, with no unsaved changes, that an assembly uses, and
    that the engineer did not open himself."""
    if doc is active or doc.borrowed or doc.windowless or doc.kind != "part":
        return False
    try:
        saved = bool(str(doc.fc_doc.FileName or ""))
        return saved and not gui.is_modified(doc.fc_doc) and used_by_assembly(doc, documents)
    except Exception:  # noqa: BLE001 - the document was closed by hand: nothing to release
        return False


def release(documents: dict[str, Any], active: Any) -> list[str]:
    """Close the window of every part that can go without one; the names of the parts that lost it. Nothing headless."""
    released: list[str] = []
    if not gui.ACTIVE:
        return released
    for doc in list(documents.values()):
        if can_release(doc, active, documents) and gui.hide_window(doc.fc_doc):
            doc.windowless = True
            released.append(str(doc.name))
    return released


def show(doc: Any) -> bool:
    """Give a document that has no window its window. True when it had none."""
    if not doc.windowless or not gui.ACTIVE:
        return False
    doc.windowless = not gui.show_window(doc.fc_doc)
    return not doc.windowless
