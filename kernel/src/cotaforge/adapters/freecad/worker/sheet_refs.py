# ruff: noqa: E501
"""What a sheet item refers to in the model, and what a symbol of the sheet says (no FreeCAD import, unit-testable).

An item of the sheet (``{"id": "d3", "type": "dim", "kind": "distance", "from": {...}, "to": {...}}``) names the model
elements it dimensions: faces and edges by id, or points that lie on the axis of a hole or on a face. ``refs_of`` reads
those references out of the stored item (the model itself is not touched: that is the worker's side,
``sheet_select``); ``text_of`` and ``content_bounds`` read what a ``DrawViewSymbol`` of the page shows (its SVG).

The same module owns the names of the sheet's views: ``view_role`` turns a TechDraw view object into ``front`` / ``top``
/ ``right`` / ``isometric`` / ``view:...`` however the sheet was made (TechDraw's own projection group, the single
isometric view, or the ``View_<name>`` objects of the native sheet).
"""

import re
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from sheet_context import HoleInfo, Vec3
from sheet_model import text_width

SVG_NS = "{http://www.w3.org/2000/svg}"
NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
TOKEN = re.compile(r"[MLAZmlaz]|-?\d+(?:\.\d+)?")
NATIVE_PREFIX = "View_"
GROUP_ITEM_ROLES = {
    "Front": "front",
    "Top": "top",
    "Right": "right",
    "Left": "left",
    "Rear": "rear",
    "Bottom": "bottom",
}
EXTRA_KEYS = {"threads": "thread arcs"}


@dataclass
class ItemRefs:
    """The model elements an item names: ``faces`` and ``edges`` by id, ``holes`` as indexes into the part's hole list,
    ``points`` (model mm) that lie on a face or an edge (they are not holes). ``whole`` says the item is about the part
    as a whole (an overall dimension, a general note)."""

    faces: list[str] = field(default_factory=list)
    edges: list[str] = field(default_factory=list)
    holes: list[int] = field(default_factory=list)
    points: list[Vec3] = field(default_factory=list)
    whole: bool = False


class LazyHoles(Sequence[HoleInfo]):
    """The holes of the part, found the first time something looks at them: an item that names no hole (a note, a face
    dimension) never asks, and the CAD finds holes by reading the whole B-rep."""

    def __init__(self, load: Callable[[], list[HoleInfo]]) -> None:
        self._load = load
        self._found: list[HoleInfo] | None = None

    def _holes(self) -> list[HoleInfo]:
        if self._found is None:
            self._found = self._load()
        return self._found

    def __len__(self) -> int:
        return len(self._holes())

    def __getitem__(self, index: Any) -> Any:
        return self._holes()[index]


def _add(target: list[Any], value: Any) -> None:
    if value not in target:
        target.append(value)


def _ids(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value] if isinstance(value, list | tuple) else []


def point_of(value: Any) -> Vec3 | None:
    """``[x, y, z]`` as stored in an item, or ``None`` when it is not a point."""
    if isinstance(value, list | tuple) and len(value) == 3:
        try:
            return (float(value[0]), float(value[1]), float(value[2]))
        except (TypeError, ValueError):
            return None
    return None


def _points(value: Any) -> list[Vec3]:
    """One point or a list of points, as the items store them."""
    if isinstance(value, list | tuple) and value and all(isinstance(v, list | tuple) for v in value):
        return [p for p in (point_of(v) for v in value) if p is not None]
    point = point_of(value)
    return [point] if point is not None else []


def _hole_at(holes: Sequence[HoleInfo], point: Vec3) -> list[int]:
    return [h.index for h in holes if h.on_axis(point)]


def _point_ref(refs: ItemRefs, holes: Sequence[HoleInfo], point: Vec3) -> None:
    found = _hole_at(holes, point)
    if found:
        for index in found:
            _add(refs.holes, index)
    else:
        _add(refs.points, point)


def _ref(refs: ItemRefs, holes: Sequence[HoleInfo], ref: Any) -> None:
    """One ``{"face": ..}`` / ``{"edge": ..}`` / ``{"at": [x, y, z]}`` reference of a dimension."""
    if not isinstance(ref, dict):
        return
    if "face" in ref:
        for face in _ids(ref["face"]):
            _add(refs.faces, face)
    if "edge" in ref:
        for edge in _ids(ref["edge"]):
            _add(refs.edges, edge)
    if "at" in ref:
        point = point_of(ref["at"])
        if point is not None:
            _point_ref(refs, holes, point)


def _holes_field(refs: ItemRefs, holes: Sequence[HoleInfo], value: Any) -> None:
    """``"all"`` or the points on the axes of the holes (``hole_dim.at``, ``section.holes``, ``datum_dims.holes``)."""
    if value == "all":
        for hole in holes:
            _add(refs.holes, hole.index)
        return
    for point in _points(value):
        _point_ref(refs, holes, point)


def _hole_faces(refs: ItemRefs, holes: Sequence[HoleInfo], faces: Iterable[str]) -> None:
    """The holes whose wall is one of ``faces`` (a ``face`` ids of a hole_dim stand for the holes they belong to)."""
    wanted = set(faces)
    for hole in holes:
        if wanted & set(hole.faces):
            _add(refs.holes, hole.index)


def _dim(spec: dict[str, Any], holes: Sequence[HoleInfo], refs: ItemRefs) -> None:
    for key in ("from", "to"):
        _ref(refs, holes, spec.get(key))
    for key in ("face", "faces"):
        for face in _ids(spec.get(key)):
            _add(refs.faces, face)


def _hole_dim(spec: dict[str, Any], holes: Sequence[HoleInfo], refs: ItemRefs) -> None:
    if spec.get("all"):
        _holes_field(refs, holes, "all")
    if "at" in spec:
        _holes_field(refs, holes, spec["at"])
    if "face" in spec:
        faces = _ids(spec["face"])
        _hole_faces(refs, holes, faces)
        for face in faces:
            _add(refs.faces, face)


def _on_face(spec: dict[str, Any], holes: Sequence[HoleInfo], refs: ItemRefs) -> None:
    for face in _ids(spec.get("face")):
        _add(refs.faces, face)


def _datum_dims(spec: dict[str, Any], holes: Sequence[HoleInfo], refs: ItemRefs) -> None:
    if "holes" in spec:
        _holes_field(refs, holes, spec["holes"])
    else:
        _holes_field(refs, holes, "all")
    for key in ("x_from", "y_from"):
        value = spec.get(key)
        if isinstance(value, str) and value.startswith("face:"):
            _add(refs.faces, value)


def _section(spec: dict[str, Any], holes: Sequence[HoleInfo], refs: ItemRefs) -> None:
    if "holes" in spec and spec["holes"] != "axis":  # the axis of the part names no hole
        _holes_field(refs, holes, spec["holes"])


def _detail(spec: dict[str, Any], holes: Sequence[HoleInfo], refs: ItemRefs) -> None:
    point = point_of(spec.get("at"))
    if point is not None:
        _point_ref(refs, holes, point)


def _whole(spec: dict[str, Any], holes: Sequence[HoleInfo], refs: ItemRefs) -> None:
    refs.whole = True


def _line(spec: dict[str, Any], holes: Sequence[HoleInfo], refs: ItemRefs) -> None:
    for face in _ids(spec.get("axis")):  # the axis of a cylinder, a bore too
        _add(refs.faces, face)
        _hole_faces(refs, holes, [face])
    for key in ("from", "to"):
        _ref(refs, holes, spec.get(key))


READERS: dict[str, Callable[[dict[str, Any], Sequence[HoleInfo], ItemRefs], None]] = {
    "overall": _whole,
    "dim": _dim,
    "hole_dim": _hole_dim,
    "datum": _on_face,
    "gdt": _on_face,
    "finish": _on_face,
    "datum_dims": _datum_dims,
    "section": _section,
    "detail": _detail,
    "aux_view": _on_face,
    "line": _line,
}


def refs_of(spec: dict[str, Any], holes: Sequence[HoleInfo]) -> ItemRefs:
    """The model elements the stored item ``spec`` names (``holes``: the part's holes, to say which one a point is on)."""
    refs = ItemRefs()
    reader = READERS.get(str(spec.get("type")))
    if reader is not None:
        reader(spec, holes, refs)
    return refs


def faces_of_refs(refs: ItemRefs, holes: Sequence[HoleInfo]) -> list[str]:
    """Every face id the item names: its own and the walls of its holes."""
    out = list(refs.faces)
    wanted = set(refs.holes)
    for hole in holes if wanted else ():
        if hole.index in wanted:
            for face in hole.faces:
                _add(out, face)
    return out


def refers_to(
    refs: ItemRefs,
    holes: Sequence[HoleInfo],
    faces: Iterable[str] = (),
    edges: Iterable[str] = (),
    rim_faces: Iterable[str] = (),
) -> bool:
    """Whether the item names one of ``faces`` (also through a hole's wall), one of ``edges``, or a hole whose wall is
    in ``rim_faces`` (the faces next to a selected circular edge: the hole the edge is the rim of)."""
    wall = set(faces_of_refs(refs, holes))
    if wall & set(faces):
        return True
    if set(refs.edges) & set(edges):
        return True
    rim = set(rim_faces)
    return bool(rim and refs.holes) and any(hole.index in refs.holes and rim & set(hole.faces) for hole in holes)


# --- the views of the sheet ---------------------------------------------------------------------------------------------


def view_role(type_id: str, name: str, label: str, projected: str = "") -> str | None:
    """The name a view of the page goes by: ``front`` / ``top`` / ``right`` / ... for the items of the projection group
    (``projected`` is their ``Type``), ``isometric`` for the single isometric view, ``<name>`` of a native ``View_<name>``
    object, ``section`` / ``detail`` for a derived view named by its label; ``None`` when ``name`` is not a view."""
    if type_id == "TechDraw::DrawProjGroupItem":
        return GROUP_ITEM_ROLES.get(projected, projected.lower() or None)
    if name.startswith(NATIVE_PREFIX):
        return name[len(NATIVE_PREFIX) :].lower() or None
    if type_id in ("TechDraw::DrawViewPart", "TechDraw::DrawViewSection", "TechDraw::DrawViewDetail"):
        if label.lower().startswith(("iso", "view_iso")):
            return "isometric"
        return label or name
    return None


def parse_key(key: str) -> tuple[str, str]:
    """``(kind, name)`` of the unit key a page object carries (``CotaforgeKey``): ``view`` for ``view:front`` (a view of an
    ``hlr`` sheet), ``extra`` for what no item owns, ``item`` otherwise."""
    if key.startswith("view:"):
        return "view", key[5:]
    if key in EXTRA_KEYS:
        return "extra", key
    return "item", key


# --- what a symbol shows --------------------------------------------------------------------------------------------------


def _root(svg: str) -> ET.Element:
    return ET.fromstring(svg.encode("utf-8"))  # noqa: S314 - our own text


def texts_of(svg: str) -> list[str]:
    """The texts of a symbol, in the order the sheet drew them."""
    try:
        root = _root(svg)
    except ET.ParseError:
        return []
    return ["".join(t.itertext()).strip() for t in root.iter(f"{SVG_NS}text") if "".join(t.itertext()).strip()]


def number_in(text: str) -> float | None:
    """The first number of a dimension's text (``Ø6 H7`` -> 6, ``12,5`` -> 12.5)."""
    found = re.search(r"-?\d+(?:[.,]\d+)?", text)
    return float(found.group(0).replace(",", ".")) if found else None


def view_box(svg: str) -> tuple[float, float, float, float] | None:
    """``(x, y, width, height)`` of the symbol's viewBox (page mm, y DOWN, as the SVG writes it)."""
    try:
        root = _root(svg)
        x, y, w, h = (float(v) for v in root.attrib["viewBox"].split())
    except (ET.ParseError, KeyError, ValueError):
        return None
    return x, y, w, h


def _path_points(d: str) -> list[tuple[float, float]]:
    """The points a path of ours passes (``M x y L x y``, ``A r r 0 large sweep x y``): arcs by their end points and the
    four extremes of their circle when they are long, which is enough for a box."""
    tokens = TOKEN.findall(d)
    points: list[tuple[float, float]] = []
    i = 0
    command = ""
    while i < len(tokens):
        token = tokens[i]
        if token.isalpha():
            command = token.upper()
            i += 1
            continue
        if command in ("M", "L") and i + 1 < len(tokens):
            points.append((float(tokens[i]), float(tokens[i + 1])))
            i += 2
        elif command == "A" and i + 6 < len(tokens):
            points.append((float(tokens[i + 5]), float(tokens[i + 6])))
            i += 7
        else:
            i += 1
    return points


def content_bounds(svg: str) -> tuple[float, float, float, float] | None:
    """The box of what a symbol draws, ``(x0, y0, x1, y1)`` in the SVG's own coordinates (page mm, y DOWN): the box of
    its paths, circles and texts (a text by the width ``sheet_model`` estimates). ``None`` when it draws nothing."""
    try:
        root = _root(svg)
    except ET.ParseError:
        return None
    xs: list[float] = []
    ys: list[float] = []
    for element in root.iter():
        tag = element.tag.removeprefix(SVG_NS)
        if tag == "path":
            for x, y in _path_points(element.attrib.get("d", "")):
                xs.append(x)
                ys.append(y)
        elif tag == "circle":
            cx, cy, r = (float(element.attrib.get(k, "0")) for k in ("cx", "cy", "r"))
            xs += [cx - r, cx + r]
            ys += [cy - r, cy + r]
        elif tag == "text":
            text = "".join(element.itertext())
            x, y = float(element.attrib.get("x", "0")), float(element.attrib.get("y", "0"))
            size = float(element.attrib.get("font-size", "2.5"))
            width = text_width(text, size)
            anchor = element.attrib.get("text-anchor", "start")
            left = x - (width / 2 if anchor == "middle" else width if anchor == "end" else 0.0)
            xs += [left, left + width]
            ys += [y - size, y + size * 0.3]
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def describe_measure(spec: dict[str, Any], holes: Sequence[HoleInfo], label_of: Callable[[Any], str]) -> str:
    """A line that says what a dimension item measures, by the names the agent knows (``label_of`` names a reference:
    ``hole D6 (face:7)``); empty for an item that is not a dimension."""
    kind = spec.get("kind")
    if spec.get("type") == "dim" and kind == "distance":
        along = f" along {spec['along']}" if spec.get("along") else ""
        return f"distance{along} from {label_of(spec.get('from'))} to {label_of(spec.get('to'))}"
    if spec.get("type") == "dim" and kind in ("radius", "diameter", "chamfer"):
        return f"{kind} of {', '.join(_ids(spec.get('face')))}"
    if spec.get("type") == "dim" and kind == "angle":
        return f"angle between {', '.join(_ids(spec.get('faces')))}"
    return ""
