"""The standard views of a sheet made with ``projection="hlr"`` (no FreeCAD import): the front / top / right group and
the isometric view are projected with the same function the derived views use (``Geometry.view_projection``) and
placed on the page where TechDraw would have put them, instead of being TechDraw view objects.

The layout is worked out once, when the sheet is built, and kept in the sheet spec (``record``): the scale, the front
view's place, the size and centre of the part. A view's origin is the model point ``centre`` (the centre of the part's
box, like TechDraw's group); the top view lies below the front one and the right view to its left (first angle), a
``spacing`` away from it. The isometric view is centred on the place the layout gave it.
"""

from dataclasses import dataclass, field

from sheet_clip import bbox, place
from sheet_context import Context, Geometry, ItemError, Rect, Vec3, ViewInfo, unit
from sheet_layout import ISO_DIRECTION, ISO_XDIRECTION
from sheet_model import Entity

# (direction towards the viewer, x axis of the view): the ones TechDraw's projection group uses in first angle
FRAMES: dict[str, tuple[Vec3, Vec3]] = {
    "front": ((0.0, -1.0, 0.0), (1.0, 0.0, 0.0)),
    "top": ((0.0, 0.0, 1.0), (1.0, 0.0, 0.0)),
    "right": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
    "isometric": (unit(ISO_DIRECTION), unit(ISO_XDIRECTION)),  # unit vectors: the projection frame is built from them
}
GROUP = ("front", "top", "right")


def record(
    scale: float,
    spacing: float,
    front: tuple[float, float],
    iso: tuple[float, float] | None,
    size: tuple[float, float, float],
    centre: Vec3,
    iso_scale: float = 0.0,
) -> dict[str, object]:
    """What the sheet spec keeps of the layout (page y UP): enough to place the views again after any reopen."""
    rec: dict[str, object] = {
        "scale": scale,
        "spacing": spacing,
        "front": list(front),
        "iso": list(iso) if iso is not None else None,
        "size": list(size),
        "centre": list(centre),
    }
    if iso_scale:
        rec["iso_scale"] = iso_scale  # the isometric view's own scale; missing: the sheet's
    return rec


@dataclass
class Views:
    """The views of a sheet as the compositor needs them."""

    infos: dict[str, ViewInfo] = field(default_factory=dict)  # front / top / right (items dimension these)
    iso: ViewInfo | None = None
    rects: list[Rect] = field(default_factory=list)  # every view's geometry box on the page
    entities: list[Entity] = field(default_factory=list)
    groups: dict[str, list[Entity]] = field(default_factory=dict)  # the same entities, by view name


def origins(rec: dict[str, object]) -> dict[str, tuple[float, float]]:
    """Where the origin (the model point ``centre``) of the front, top and right views sits on the page (y up)."""
    scale = float(rec["scale"])  # type: ignore[arg-type]
    spacing = float(rec["spacing"])  # type: ignore[arg-type]
    width, depth, height = (float(c) for c in rec["size"])  # type: ignore[attr-defined]
    fx, fy = (float(c) for c in rec["front"])  # type: ignore[attr-defined]
    return {
        "front": (fx, fy),
        "top": (fx, fy - ((height + depth) / 2 * scale + spacing)),
        "right": (fx - ((width + depth) / 2 * scale + spacing), fy),
    }


def compose_views(geometry: Geometry, rec: dict[str, object]) -> Views:
    """Project the part for each standard view of ``rec`` and place the results on the page."""
    scale = float(rec["scale"])  # type: ignore[arg-type]
    centre = tuple(float(c) for c in rec["centre"])  # type: ignore[attr-defined]
    where = origins(rec)
    iso_at = rec.get("iso")
    names = [*GROUP, *(["isometric"] if iso_at else [])]
    out = Views()
    for name in names:
        direction, xdir = FRAMES[name]
        projection = geometry.view_projection(centre, xdir, direction)  # type: ignore[arg-type]
        local = bbox(list(projection.visible))
        if local is None:
            msg = f"nothing of the part is seen in the {name} view"
            raise ItemError(msg)
        k = scale
        if name == "isometric":
            ix, iy = (float(c) for c in iso_at)  # type: ignore[attr-defined,union-attr]
            k = float(rec.get("iso_scale") or scale)  # type: ignore[arg-type]  # a scale of its own, when it has one
            ox, oy = ix - (local[0] + local[2]) / 2 * k, iy - (local[1] + local[3]) / 2 * k
        else:
            ox, oy = where[name]
        box = (ox + local[0] * k, oy + local[1] * k, ox + local[2] * k, oy + local[3] * k)
        info = ViewInfo(name, ox, oy, k, direction, xdir, centre, box)  # type: ignore[arg-type]
        if name == "isometric":
            out.iso = info
        else:
            out.infos[name] = info
        out.rects.append(box)
        placed = place(list(projection.visible), ox, oy, k)
        out.groups[name] = placed
        out.entities += placed
    return out


def apply(ctx: Context, views: Views, assembly: bool) -> None:
    """Put the views in the context like the TechDraw ones are: the isometric one is known to the items (balloons) of an
    assembly sheet only."""
    ctx.views = views.infos
    ctx.extras["view_boxes"] = views.rects
    if assembly and views.iso is not None:
        ctx.extras["views_extra"] = {"isometric": views.iso}
        ctx.extras["iso_box"] = views.iso.box
