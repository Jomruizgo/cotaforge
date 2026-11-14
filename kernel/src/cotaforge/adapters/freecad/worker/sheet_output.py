# ruff: noqa: E501
"""What is on a sheet besides what its items made: TechDraw's own objects a native script added (a section view, a
dimension, a note, a hatch, a cosmetic line).

The items are drawn by the compositor and mirrored on the page, so everything they make is known and readable (the review
reads the composed sheet). A native script writes to the page directly, and none of that is in the composed sheet. The
visible CAD paints it all (capture and PDF come from the same scene), the headless one cannot, and the review cannot read
it either way: ``native_rows`` lists it so a capture, a ``list`` and the review can say what is there instead of calling
the sheet fine without it. No object is dropped silently.
"""

from typing import Any

import sheet_native

PROP_KEY = "CotaforgeKey"  # an object a unit of the mirror made
PROP_SHEET = sheet_native.PROP_SHEET
OWN_TYPES = ("TechDraw::DrawProjGroup", "TechDraw::DrawProjGroupItem")
HATCH_TYPES = ("TechDraw::DrawHatch", "TechDraw::DrawGeomHatch")
COSMETIC_LISTS = (
    ("CosmeticEdges", "cosmetic line"),
    ("CenterLines", "centre line"),
    ("GeomFormats", "edge format"),
)
MAX_LISTED = 40


def _own(obj: Any) -> bool:
    """True for what the sheet made: its units and tagged views, the projection group and its items, the isometric view."""
    props = obj.PropertiesList
    if PROP_KEY in props or PROP_SHEET in props or str(obj.TypeId) in OWN_TYPES:
        return True
    return str(obj.TypeId) == "TechDraw::DrawViewPart" and str(obj.Label) == sheet_native.VIEW_LABELS["isometric"]


def _label(obj: Any) -> str:
    return str(getattr(obj, "Label", "") or getattr(obj, "Name", ""))


def _first_label(items: Any) -> str:
    """The label of the object a link property points at (a link, a list of links or ``(object, sub-elements)`` pairs)."""
    try:
        first = items[0] if isinstance(items, (list, tuple)) else items
        first = first[0] if isinstance(first, (list, tuple)) else first
        return _label(first) if first is not None else ""
    except (IndexError, TypeError, AttributeError):
        return ""


def _about(obj: Any, type_id: str) -> str:
    """What a reader needs to know about one object: the value of a dimension, the text of a note, the section's letter."""
    try:
        if type_id.endswith("DrawViewDimension"):
            value = f", {float(obj.getRawValue()):g}"
            on = _first_label(obj.References2D)
            return f"{obj.Type}{value}" + (f" on {on}" if on else "")
        if type_id.endswith("DrawViewAnnotation"):
            return "text: " + " / ".join(str(t) for t in obj.Text)
        if type_id.endswith("DrawRichAnno"):
            return "text: " + " ".join(str(obj.AnnoText).split())[:80]
        if type_id.endswith("DrawViewBalloon"):
            return f"balloon {obj.Text}"
        if type_id.endswith("DrawViewSection"):
            return f"section {obj.SectionSymbol}, cut surface {obj.CutSurfaceDisplay}"
        if type_id in HATCH_TYPES:
            return "on " + _first_label(obj.Source)
        if type_id.endswith("DrawLeaderLine"):
            return "on " + _first_label(obj.ParentView)
    except Exception:  # noqa: BLE001 - FreeCAD raises plain exceptions of every kind; the row still names the object
        return ""
    return ""


def _flat(page: Any) -> list[Any]:
    out: list[Any] = []
    for obj in page.Views:
        out.append(obj)
        if str(obj.TypeId) == "TechDraw::DrawProjGroup":
            out += list(obj.Views)
    return out


def native_rows(page: Any) -> list[dict[str, Any]]:
    """The objects on ``page`` that no item made: ``{"text": ..., "view": bool}`` rows (``view`` marks a whole view, which
    the compositor draws as its visible edges only). Cosmetic lines, centre lines and edge formats count per view; the
    hatch objects of the document that hang on a view of this page are listed too."""
    rows: list[dict[str, Any]] = []
    views = _flat(page)
    for obj in views:
        type_id = str(obj.TypeId)
        if not _own(obj):
            about = _about(obj, type_id)
            rows.append(
                {
                    "text": f"{_label(obj)} [{type_id.split('::')[-1]}]" + (f": {about}" if about else ""),
                    "view": hasattr(obj, "getVisibleEdges"),
                }
            )
        for attr, name in COSMETIC_LISTS:
            try:
                count = len(list(getattr(obj, attr, None) or []))
                if attr == "CosmeticEdges":
                    count -= sheet_native.own_cosmetic_count(obj)  # the sheet made those: they are its own lines
            except Exception:  # noqa: BLE001 - not every object has the list
                count = 0
            if count:
                rows.append({"text": f"{count} {name}(s) on {_label(obj)}", "view": False})
    names = {v.Name for v in views}
    for obj in page.Document.Objects:
        if str(obj.TypeId) in HATCH_TYPES:
            source = getattr(obj, "Source", None)
            target = source[0] if isinstance(source, (list, tuple)) and source else source
            if target is not None and getattr(target, "Name", None) in names:
                rows.append(
                    {
                        "text": f"{_label(obj)} [{str(obj.TypeId).split('::')[-1]}]: {_about(obj, str(obj.TypeId))}",
                        "view": False,
                    }
                )
    return rows


def listed(rows: list[dict[str, Any]]) -> list[str]:
    """The rows as text, at most ``MAX_LISTED`` (the count says how many were left out)."""
    texts = [str(r["text"]) for r in rows]
    if len(texts) > MAX_LISTED:
        texts = [*texts[:MAX_LISTED], f"(+{len(texts) - MAX_LISTED} more)"]
    return texts


def not_drawn(rows: list[dict[str, Any]]) -> list[str]:
    """What the compositor does NOT draw of ``rows`` (the headless CAD, or a visible one whose own export failed): a native
    view is drawn as its visible edges only, everything else not at all."""
    out = []
    for row in rows:
        if row["view"]:
            out.append(
                f"{row['text']} (drawn as its visible edges only: no hidden lines, hatching, section line or dimensions)"
            )
        else:
            out.append(f"{row['text']} (not drawn)")
    if len(out) > MAX_LISTED:
        out = [*out[:MAX_LISTED], f"(+{len(out) - MAX_LISTED} more)"]
    return out
