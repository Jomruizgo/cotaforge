# ruff: noqa: E501
"""How an object looks in FreeCAD's 3D view, face by face: the one place that reads and writes it (FreeCAD 1.1).

Two facts, both confirmed by looking at the rendered picture of 1.1.3 (never by reading a property back, which says nothing
about what the engineer sees):

- The alpha of a colour is its OPACITY (``1.0`` opaque, ``0.0`` invisible), not the transparency of FreeCAD 0.x. The harness
  wrote ``0.0`` meaning "opaque", so every face it painted vanished from the view. A face has a ``Material``: its colour in
  ``DiffuseColor`` and its transparency in ``Transparency`` (0 opaque). ``rgba`` and ``recolored`` convert between that and the
  ``(r, g, b, opacity)`` the pure modules (``highlight``, ``hole_paint``) plan with.
- A PartDesign body shows what its Tip shows. The per-face ``ShapeAppearance`` of the Tip is what paints, and the per-edge
  ``LineColorArray`` is only read from the Tip: written on the Body it changes the property and not the picture. So every
  colour of this harness goes to the Tip's view (``paint_object``); any other object is its own view.

FreeCAD is imported inside the functions that need it, so the choice of the object and the arithmetic stay unit-testable.
"""

from typing import Any

from highlight import RGBA

_MATERIAL_FIELDS = ("AmbientColor", "DiffuseColor", "EmissiveColor", "Shininess", "SpecularColor", "Transparency")


def paint_object(obj: Any) -> Any:
    """The object whose ``ViewObject`` the 3D view paints for ``obj``: a PartDesign body's Tip, else ``obj`` itself."""
    if getattr(obj, "TypeId", "") == "PartDesign::Body":
        tip = getattr(obj, "Tip", None)
        if tip is not None and getattr(tip, "ViewObject", None) is not None:
            return tip
    return obj


def clone(material: Any) -> Any:
    """An independent copy of a ``Material``: what is remembered must not change when the view's own is written."""
    import FreeCAD as App  # noqa: PLC0415 - only a worker that holds a view gets here

    copy = App.Material()
    for field in _MATERIAL_FIELDS:
        setattr(copy, field, getattr(material, field))
    return copy


def read(view: Any) -> list[Any]:
    """The per-face materials of ``view`` (one entry for the whole object until a face is painted), as copies."""
    return [clone(m) for m in view.ShapeAppearance]


def rgba(material: Any) -> RGBA:
    """``(r, g, b, opacity)`` of a face material."""
    r, g, b = (float(c) for c in tuple(material.DiffuseColor)[:3])
    return (r, g, b, 1.0 - float(material.Transparency))


def recolored(template: Any, colour: RGBA) -> Any:
    """A copy of ``template`` (its shininess and the rest are kept) showing ``colour`` = ``(r, g, b, opacity)``."""
    material = clone(template)
    material.DiffuseColor = (colour[0], colour[1], colour[2], 1.0)
    material.Transparency = 1.0 - colour[3]
    return material


def from_rgba(colours: list[RGBA], current: list[Any]) -> list[Any]:
    """The materials for ``colours``: the very material a face already has when its colour is the same, a recoloured copy of it
    (of the first one when the list is shorter) otherwise. ``[]`` is not a value FreeCAD takes: the caller never passes it."""
    out: list[Any] = []
    for index, colour in enumerate(colours):
        have = current[index] if index < len(current) else current[0]
        same = index < len(current) and all(abs(a - b) < 1e-6 for a, b in zip(rgba(have), colour, strict=True))
        out.append(clone(have) if same else recolored(have, colour))
    return out


def restore(original: list[Any], face_count: int, base_index: int) -> list[Any]:
    """The materials to put back: ``original`` as it was when it is a uniform one or still has a material per face, else (a
    feature changed the solid meanwhile and the list says nothing about its faces) the one of the base, for the whole object."""
    if len(original) == 1 or len(original) == face_count:
        return [clone(m) for m in original]
    return [clone(original[base_index])]
