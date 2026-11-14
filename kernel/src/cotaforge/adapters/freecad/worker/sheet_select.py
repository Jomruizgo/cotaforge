# ruff: noqa: E501
"""Selection and pointing on drawing sheets: what the engineer selected in the sheet window, and what the agent points at.

READING (``read_selection``): ``Gui.Selection`` gives ``(object, sub-element)`` pairs of the TechDraw page -- an item the
agent put on the sheet (every object of an item carries ``CotaforgeItem`` = its id and ``CotaforgeSheet`` = the sheet),
a dimension, a view, or an ``Edge<i>`` / ``Vertex<i>`` of a view. Each becomes a ``SheetPick`` with the item (id, type, the
text it prints, what it measures), the view, and the model elements it corresponds to by the ids the other tools use.

* an item names its model elements itself (``sheet_refs``: the faces and edges of its fields, the holes whose axis a
  point lies on); the stored item is the truth, the symbol only tells what it prints;
* a line of a view is matched by geometry (``view_match``): the sub-element name ``Edge<i>`` is the index into the
  geometry TechDraw keeps (``DrawViewPart.getEdgeByIndex(i)``: the same line the window drew, scale 1, y up), the model
  edges are projected with the view's direction, x direction and centre, and the ones that lie under the line are the
  edge -- the one nearest the viewer for a visible line, the farthest for a hidden one. Their faces and the holes whose
  wall is among them follow from the registry (``EdgeRec.faces``, ``list_holes``).

POINTING (``plan`` + ``gui.paint_sheet``): the items, the views, or the model faces / edges (what they are on the sheet:
the items that refer to them and the lines of the views that draw them), painted on the window without touching the
document; and the other way, the faces an item refers to, in the 3D view.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import drawing
import geometry as g
import gui
import Part
import provenance as prov
import registry as reg
import sheet_mirror
import sheet_native
import sheet_refs
import view_match as vm
from protocol import WorkerError, invalid_request
from sheet_context import HoleInfo

PROP_ITEM = sheet_mirror.PROP_ITEM
PROP_SHEET = sheet_mirror.PROP_SHEET
PROP_KEY = sheet_mirror.PROP_KEY
PROP_NATIVE = sheet_mirror.PROP_NATIVE
POINT_ON_FACE_MM = 0.05
SAMPLE_DEFLECTION_MM = 0.01
MAX_SAMPLES = 40
MAX_PATH_POINTS = 240
BOX_MARGIN_MM = 1.0
VIEW_MARGIN_MM = 2.0
DERIVED_VIEWS = ("TechDraw::DrawViewSection", "TechDraw::DrawViewDetail")  # their projection is not the plain one


def _n(value: float) -> str:
    return f"{round(float(value), 3) + 0.0:g}"  # + 0.0: no "-0"


def _pt(p: Any) -> str:
    return "[" + ", ".join(_n(c) for c in p) + "]"


# --- the sheets of a document -----------------------------------------------------------------------------------------


@dataclass
class PageInfo:
    page: Any
    name: str
    spec: dict[str, Any]
    items: dict[str, dict[str, Any]]
    body: str | None
    overall: str | None  # the id of its ``overall`` item: TechDraw's own overall dimensions belong to it


def tag_of(obj: Any) -> tuple[str, str] | None:
    """What a page object stands for: ``("item", id)`` (an item of the sheet), ``("view", name)`` (a view of an ``hlr``
    sheet), ``("extra", key)`` (thread arcs, the ISO 286 note) or ``("overall", name)`` (one of TechDraw's own overall
    dimensions); ``None`` for what the engineer or TechDraw made."""
    props = obj.PropertiesList
    if PROP_NATIVE in props:  # one of TechDraw's own overall dimensions (it carries the item tag too)
        return "overall", str(getattr(obj, PROP_NATIVE)).partition(":")[2]
    if PROP_ITEM in props and str(getattr(obj, PROP_ITEM)):
        return "item", str(getattr(obj, PROP_ITEM))
    if PROP_KEY in props:
        return sheet_refs.parse_key(str(getattr(obj, PROP_KEY)))
    return None


def page_views(page: Any) -> list[Any]:
    """Every object of ``page``, the items of a projection group included."""
    out: list[Any] = []
    for obj in page.Views:
        out.append(obj)
        if str(obj.TypeId) == "TechDraw::DrawProjGroup":
            out.extend(obj.Views)
    return out


def view_name(obj: Any) -> str | None:
    """The name a view of the page goes by (``front``, ``top``, ``isometric``...), ``None`` for what is not a view."""
    if not hasattr(obj, "getVisibleEdges"):
        return None
    # the view a projection item stands for is the one the sheet tagged it with (the anchor of a sheet without a front view
    # is the top or the right one), else the group's own name for it
    projected = str(getattr(obj, "CotaforgeViewKey", "") or getattr(obj, "Type", ""))
    return sheet_refs.view_role(str(obj.TypeId), str(obj.Name), str(obj.Label), projected)


def object_text(obj: Any) -> str:
    """What a page object prints: the texts of a symbol, the value of a dimension, the lines of a note."""
    props = obj.PropertiesList
    if "Symbol" in props:
        return " ".join(sheet_refs.texts_of(str(obj.Symbol)))
    getter = getattr(obj, "getText", None)
    if getter is not None:
        try:
            text = str(getter())
        except Exception:  # noqa: BLE001 - a dimension that is not computed yet has no text
            return ""
        if "Arbitrary" in props and not bool(getattr(obj, "Arbitrary")):  # noqa: B009 - TechDraw measured it: '70,00 mm'
            text = re.sub(r"\s*mm$", "", text.strip())  # the sheet prints the number alone
            if "," in text or "." in text:
                text = text.rstrip("0").rstrip(",.")
        return text
    if "Text" in props:
        value = getattr(obj, "Text")  # noqa: B009
        return " ".join(str(v) for v in value) if isinstance(value, list | tuple) else str(value)
    return ""


class Sheets:
    """The drawing sheets of a document, with the holes of their part and the model they draw (made once per request)."""

    def __init__(self, doc: reg.DocumentRec) -> None:
        self.doc = doc
        self.fc = doc.fc_doc
        self.pages: list[PageInfo] = []
        for page in drawing.pages(self.fc):
            spec = drawing.spec_of(page)
            items = {str(i["id"]): dict(i) for i in spec.get("items", []) if isinstance(i, dict) and "id" in i}
            overall = next((i for i, s in items.items() if s.get("type") == "overall"), None)
            body = spec.get("body")
            self.pages.append(PageInfo(page, str(spec.get("sheet", page.Name)), spec, items, body, overall))
        self._holes: dict[str | None, sheet_refs.LazyHoles] = {}
        self._models: dict[str | None, ModelIndex] = {}

    def __bool__(self) -> bool:
        return bool(self.pages)

    def by_name(self, name: str | None) -> list[PageInfo]:
        if name is None:
            return list(self.pages)
        return [p for p in self.pages if name in (p.name, str(p.page.Name), str(p.page.Label))]

    def page_of(self, obj: Any) -> PageInfo | None:
        for info in self.pages:
            if obj.Name == info.page.Name or any(o.Name == obj.Name for o in page_views(info.page)):
                return info
        return None

    def holes(self, info: PageInfo) -> sheet_refs.LazyHoles:
        """The holes of the part ``info`` draws (read from the model the first time an item needs them)."""
        if info.body not in self._holes:

            def load() -> list[HoleInfo]:
                try:
                    return drawing.hole_infos(self.doc, info.body, None)
                except WorkerError:
                    return []

            self._holes[info.body] = sheet_refs.LazyHoles(load)
        return self._holes[info.body]

    def model(self, info: PageInfo) -> "ModelIndex":
        if info.body not in self._models:
            self._models[info.body] = ModelIndex(self.doc, drawing._bodies(self.doc, info.body, None))
        return self._models[info.body]

    def texts(self, info: PageInfo) -> dict[str, str]:
        """The text each item of the page prints, by item id."""
        found: dict[str, list[str]] = {}
        for obj in page_views(info.page):
            tag = tag_of(obj)
            if tag is None:
                continue
            kind, name = tag
            item = name if kind == "item" else (info.overall if kind == "overall" else None)
            if item is not None:
                text = object_text(obj)
                if text:
                    found.setdefault(item, []).append(text)
        return {item: " | ".join(texts) for item, texts in found.items()}


class ModelIndex:
    """The edges, vertices and cylinders of the bodies a sheet draws, as ``view_match`` takes them."""

    def __init__(self, doc: reg.DocumentRec, bodies: list[Any]) -> None:
        self.doc = doc
        self.bodies = bodies
        self.shapes = {b.id: reg.world_shape(doc, b) for b in bodies}
        self._edges: list[vm.EdgeCand] | None = None
        self._cylinders: list[vm.CylinderCand] | None = None

    def edge_rec(self, edge_id: str) -> Any | None:
        for body in self.bodies:
            rec = body.find("edges", edge_id)
            if rec is not None and not rec.stale:
                return rec
        return None

    def face_edges(self, face_id: str) -> list[str]:
        return [rec.id for body in self.bodies for rec in body.edges.values() if face_id in rec.faces and not rec.stale]

    def _live_edges(self) -> Iterator[tuple[Any, Any]]:
        """``(record, edge)`` of every live edge, one at a time (``Edges[i]`` would wrap every edge of the shape)."""
        for body in self.bodies:
            shape = self.shapes[body.id]
            if shape.isNull():
                continue
            for rec in body.edges.values():
                if not rec.stale:
                    yield rec, shape.getElement(rec.sub_name)

    @staticmethod
    def _samples3d(edge: Any) -> tuple[g.Vec3, ...]:
        try:
            points = [g.vec(p) for p in edge.discretize(Deflection=SAMPLE_DEFLECTION_MM)]
        except Exception:  # noqa: BLE001 - a degenerate edge has nothing to discretize
            points = []
        if len(points) < 2:
            points = [g.vec(v.Point) for v in edge.Vertexes[:2]]
        return tuple(points)

    def all_edges(self) -> list[vm.EdgeCand]:
        if self._edges is None:
            self._edges = [vm.EdgeCand(rec.id, self._samples3d(edge)) for rec, edge in self._live_edges()]
        return self._edges

    def edges_near(self, frame: vm.Frame, box: tuple[float, float, float, float], tol: float) -> list[vm.EdgeCand]:
        """The edges whose box, seen in ``frame``, reaches ``box`` (view coordinates): only those are sampled."""
        if self._edges is not None:
            return self._edges
        out = []
        for rec, edge in self._live_edges():
            b = edge.BoundBox
            corners = [
                frame.flat((x, y, z)) for x in (b.XMin, b.XMax) for y in (b.YMin, b.YMax) for z in (b.ZMin, b.ZMax)
            ]
            xs, ys = [c[0] for c in corners], [c[1] for c in corners]
            if max(xs) + tol < box[0] or min(xs) - tol > box[2] or max(ys) + tol < box[1] or min(ys) - tol > box[3]:
                continue
            out.append(vm.EdgeCand(rec.id, self._samples3d(edge)))
        return out

    def vertices_near(self, frame: vm.Frame, point: vm.Pt, tol: float) -> list[vm.VertexCand]:
        out = []
        for body in self.bodies:
            shape = self.shapes[body.id]
            for rec in body.vertices.values():
                if rec.stale:
                    continue
                p = g.vec(shape.getElement(rec.sub_name).Point)
                x, y = frame.flat(p)
                if abs(x - point[0]) <= tol and abs(y - point[1]) <= tol:
                    out.append(vm.VertexCand(rec.id, p))
        return out

    def cylinders(self) -> list[vm.CylinderCand]:
        if self._cylinders is None:
            found: list[vm.CylinderCand] = []
            for body in self.bodies:
                shape = self.shapes[body.id]
                for rec in body.faces.values():
                    if rec.stale or rec.sig.surface != "cylinder":
                        continue
                    face = shape.getElement(rec.sub_name)
                    surf = face.Surface
                    axis = g.unit(g.vec(surf.Axis))
                    centre = g.vec(surf.Center)
                    _, _, v0, v1 = face.ParameterRange
                    u, v = prov.mid_params(face)
                    radial = g.sub(g.vec(face.valueAt(u, v)), centre)
                    convex = g.dot(g.vec(face.normalAt(u, v)), radial) > 0
                    found.append(
                        vm.CylinderCand(rec.id, centre, axis, float(surf.Radius), float(v0), float(v1), convex)
                    )
            self._cylinders = found
        return self._cylinders

    def face_shape(self, face_id: str) -> Any | None:
        for body in self.bodies:
            rec = body.find("faces", face_id)
            if rec is not None and not rec.stale:
                return self.shapes[body.id].getElement(rec.sub_name)
        return None


# --- a view of the page -------------------------------------------------------------------------------------------------


def frame_of(view: Any) -> vm.Frame:
    """The projection of ``view`` (its direction and x direction are unit vectors for the ones the sheet makes; the
    isometric view's are not, so they are normalised here)."""
    return vm.Frame(g.vec(view.getGeometricCenter()), g.unit(g.vec(view.XDirection)), g.unit(g.vec(view.Direction)))


def _thin(points: list[vm.Pt], limit: int) -> list[vm.Pt]:
    if len(points) <= limit:
        return points
    step = (len(points) - 1) / (limit - 1)
    return [points[round(i * step)] for i in range(limit)]


def edge_points(edge: Any, deflection: float = SAMPLE_DEFLECTION_MM, limit: int = MAX_SAMPLES) -> list[vm.Pt]:
    """A line of a view (``getEdgeByIndex``: scale 1, y up) as points, the ends included; a straight line also its middle."""
    try:
        points = [(float(p.x), float(p.y)) for p in edge.discretize(Deflection=deflection)]
    except Exception:  # noqa: BLE001
        points = []
    if len(points) < 2:
        ends = [(float(v.Point.x), float(v.Point.y)) for v in edge.Vertexes[:2]]
        points = ends if len(ends) == 2 else points
    if len(points) == 2:
        a, b = points
        points = [a, ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), b]
    return _thin(points, limit)


def is_derived(view: Any) -> bool:
    """A section or detail view does not draw the part the way its direction says (a cut, a scaled part): no mapping."""
    return str(view.TypeId) in DERIVED_VIEWS or bool(getattr(view, "Rotation", 0))


def is_hidden_edge(view: Any, index: int) -> bool:
    """Whether ``Edge<index>`` of ``view`` is a hidden line (TechDraw lists the visible lines first, then the hidden ones)."""
    visible = len(view.getVisibleEdges())
    return visible <= index < visible + len(view.getHiddenEdges())


def match_view_edge(
    index: ModelIndex, view: Any, edge_index: int, projected: list[vm.Projected] | None = None
) -> tuple[vm.Match, bool, list[vm.Pt]]:
    """``(match, hidden, samples)`` for ``Edge<edge_index>`` of ``view``: the model edges it is the drawing of."""
    frame = frame_of(view)
    samples = edge_points(view.getEdgeByIndex(edge_index))
    hidden = is_hidden_edge(view, edge_index)
    if projected is None:
        xs, ys = [p[0] for p in samples], [p[1] for p in samples]
        box = (min(xs), min(ys), max(xs), max(ys))
        projected = vm.project_edges(frame, index.edges_near(frame, box, vm.TOL_MM))
    return vm.match_edge(frame, samples, projected, index.cylinders(), hidden), hidden, samples


def map_view(doc: reg.DocumentRec, sheet: str | None, name: str) -> list[dict[str, Any]]:
    """Every line and point of the view ``name`` of the sheet and the model elements it draws: the table the selection
    reads one row of (``edge`` rows then ``vertex`` rows). Test and diagnosis."""
    sheets = Sheets(doc)
    info = _need_page(sheets, sheet)
    view = _find_view(info, name)
    if view is None or is_derived(view):
        return []
    index = sheets.model(info)
    frame = frame_of(view)
    projected = vm.project_edges(frame, index.all_edges())
    rows: list[dict[str, Any]] = []
    for i in range(len(view.getVisibleEdges()) + len(view.getHiddenEdges())):
        match, hidden, _ = match_view_edge(index, view, i, projected)
        edge = view.getEdgeByIndex(i)
        rows.append(
            {
                "kind": "edge",
                "index": i,
                "curve": type(edge.Curve).__name__,
                "hidden": hidden,
                "edges": match.edges,
                "faces": match.faces,
                "behind": match.behind,
            }
        )
    return rows


# --- reading the selection -------------------------------------------------------------------------------------------


def _need_page(sheets: Sheets, sheet: str | None) -> PageInfo:
    found = sheets.by_name(sheet)
    if not found:
        known = ", ".join(p.name for p in sheets.pages) or "none"
        raise invalid_request(f"no drawing sheet {sheet!r}; the document's sheets: {known}")
    return found[0]


def _find_view(info: PageInfo, name: str) -> Any | None:
    wanted = name.lower().removeprefix("view:")
    if wanted in ("iso", "isometric"):
        wanted = "isometric"
    for obj in page_views(info.page):
        found = view_name(obj)
        if found is not None and found.lower() == wanted:
            return obj
    return None


def _hole_out(hole: HoleInfo) -> dict[str, Any]:
    return {
        "diameter_mm": round(float(hole.diameter), 4),
        "through": hole.through,
        "origin": [round(float(c), 4) for c in hole.centre],
        "direction": [round(float(c), 4) for c in hole.direction],
        "faces": list(hole.faces),
    }


def _ref_label(holes: list[HoleInfo], ref: Any) -> str:
    """How a dimension's reference is named to the agent: a hole by its size and face, a face or edge by its id."""
    if not isinstance(ref, dict):
        return "?"
    if "face" in ref:
        return str(ref["face"])
    if "edge" in ref:
        return str(ref["edge"])
    point = sheet_refs.point_of(ref.get("at"))
    if point is None:
        return "?"
    hits = [h for h in holes if h.on_axis(point)]
    if hits:
        hole = hits[0]
        faces = f" ({', '.join(hole.faces)})" if hole.faces else ""
        return f"hole D{_n(hole.diameter)}{faces}"
    return f"point {_pt(point)}"


def _item_pick(sheets: Sheets, info: PageInfo, item_id: str, obj: Any, text: str) -> dict[str, Any]:
    spec = info.items.get(item_id, {})
    holes = sheets.holes(info)
    found = sheet_refs.refs_of(spec, holes) if spec else sheet_refs.ItemRefs()
    wanted = set(found.holes)
    detail = sheet_refs.describe_measure(spec, holes, lambda ref: _ref_label(holes, ref))
    kind = str(spec.get("type", "?"))
    if not detail and kind == "hole_dim" and found.holes:
        detail = f"callout of {len(found.holes)} hole{'s' if len(found.holes) != 1 else ''}"
    if not detail and found.whole:
        detail = "the part as a whole"
    tag = tag_of(obj)
    if tag is not None and tag[0] == "overall":
        detail = f"overall dimension: {tag[1]}"
    if found.points:
        detail += ("; " if detail else "") + "points " + ", ".join(_pt(p) for p in found.points)
    return {
        "sheet": info.name,
        "kind": "item",
        "label": f"{item_id} {kind}",
        "item": item_id,
        "item_type": kind,
        "text": text,
        "detail": detail,
        "faces": sheet_refs.faces_of_refs(found, holes),
        "edges": list(found.edges),
        "holes": [_hole_out(h) for h in (holes if wanted else ()) if h.index in wanted],
    }


def _extra_pick(info: PageInfo, key: str, text: str) -> dict[str, Any]:
    return {
        "sheet": info.name,
        "kind": "other",
        "label": sheet_refs.EXTRA_KEYS.get(key, key),
        "text": text,
        "detail": "drawn by the sheet itself, not an item",
    }


def _view_pick(info: PageInfo, view: Any, name: str) -> dict[str, Any]:
    scale = float(view.getScale()) if hasattr(view, "getScale") else 1.0
    direction = g.vec(view.Direction) if hasattr(view, "Direction") else None
    looks = f"looks along {_pt(direction)}; " if direction is not None else ""
    return {
        "sheet": info.name,
        "kind": "view",
        "label": f"{name} view",
        "view": name,
        "detail": f"{looks}scale {_n(scale)}",
    }


def _edge_pick(sheets: Sheets, info: PageInfo, view: Any, name: str, index: int) -> dict[str, Any]:
    pick = _view_pick(info, view, name)
    pick["kind"] = "edge"
    pick["label"] = f"{name} view Edge{index}"
    try:
        edge = view.getEdgeByIndex(index)
    except Exception:  # noqa: BLE001 - an index past the geometry the view has now (it was recomputed)
        pick["detail"] = "this line is no longer in the view"
        return pick
    curve = type(edge.Curve).__name__
    if curve == "Circle":
        shape = f"{'circle' if edge.isClosed() else 'arc'} diameter {_n(2 * edge.Curve.Radius)} mm"
    elif curve == "Line":
        shape = f"line {_n(edge.Length)} mm"
    else:
        shape = f"{curve.lower()} {_n(edge.Length)} mm"
    if is_derived(view):
        pick["detail"] = f"{shape}; a section or detail view: its lines are not mapped to the model"
        return pick
    index_model = sheets.model(info)
    match, hidden, _ = match_view_edge(index_model, view, index)
    pick["detail"] = f"{shape}, {'hidden' if hidden else 'visible'}"
    faces: list[str] = []
    for edge_id in match.edges:
        rec = index_model.edge_rec(edge_id)
        for face in rec.faces if rec is not None else ():
            if face not in faces:
                faces.append(face)
    for face in match.faces:
        if face not in faces:
            faces.append(face)
    holes = [h for h in sheets.holes(info) if set(h.faces) & set(faces)]
    pick["edges"] = list(match.edges)
    pick["faces"] = faces
    pick["holes"] = [_hole_out(h) for h in holes]
    if not match.edges and not match.faces:
        pick["detail"] += "; no model edge lies under it (a cosmetic line, a centre line, or the part changed)"
    elif match.behind:
        pick["detail"] += f"; on the same line but behind it: {', '.join(match.behind)}"
    return pick


def _vertex_pick(sheets: Sheets, info: PageInfo, view: Any, name: str, index: int) -> dict[str, Any]:
    pick = _view_pick(info, view, name)
    pick["kind"] = "vertex"
    pick["label"] = f"{name} view Vertex{index}"
    try:
        vertex = view.getVertexByIndex(index)
    except Exception:  # noqa: BLE001
        pick["detail"] = "this point is no longer in the view"
        return pick
    point = (float(vertex.Point.x), float(vertex.Point.y))
    pick["detail"] = f"point [{_n(point[0])}, {_n(point[1])}] of the view"
    if is_derived(view):
        return pick
    index_model = sheets.model(info)
    frame = frame_of(view)
    match = vm.match_vertex(frame, point, index_model.vertices_near(frame, point, vm.TOL_MM), False)
    if match.edges:
        pick["detail"] += f"; model {', '.join(match.edges)}"
        edges: list[str] = []
        for body in index_model.bodies:
            for vertex_id in match.edges:
                rec = body.find("vertices", vertex_id)
                for edge_id in rec.edges if rec is not None else ():
                    if edge_id not in edges:
                        edges.append(edge_id)
        pick["edges"] = edges
    return pick


def _dimension_pick(sheets: Sheets, info: PageInfo | None, obj: Any) -> dict[str, Any]:
    """A dimension nobody of ours placed (the engineer's own): its text and the model edges its 3D references name."""
    import queries  # noqa: PLC0415 - queries imports this module lazily too

    edges: list[str] = []
    faces: list[str] = []
    refs3d = getattr(obj, "References3D", None) or []
    for ref_obj, subs in refs3d:
        for sub in subs:
            found = queries._entity_for_sub_element(sheets.doc, ref_obj.Name, sub)  # noqa: SLF001
            if found is None:
                continue
            (faces if found[1] == "face" else edges).append(found[0])
    return {
        "sheet": info.name if info else "",
        "kind": "other",
        "label": f"dimension {obj.Label}",
        "text": object_text(obj),
        "detail": f"a dimension of the sheet (measure type {getattr(obj, 'MeasureType', '?')})",
        "faces": faces,
        "edges": edges,
    }


def read_pick(sheets: Sheets, obj: Any, sub: str) -> dict[str, Any] | None:
    """The ``SheetPick`` (as a dict) of one selected object / sub-element of a page, ``None`` for what is not on a sheet."""
    type_id = str(obj.TypeId)
    if type_id == "TechDraw::DrawPage":
        info = sheets.page_of(obj)
        return {"sheet": info.name if info else str(obj.Label), "kind": "other", "label": f"sheet {obj.Label}"}
    info = sheets.page_of(obj)
    if info is None:
        return None
    tag = tag_of(obj)
    if tag is not None:
        kind, name = tag
        if kind == "item":
            return _item_pick(sheets, info, name, obj, object_text(obj))
        if kind == "overall":
            return _item_pick(sheets, info, info.overall or "", obj, object_text(obj))
        if kind == "view":
            view = {"sheet": info.name, "kind": "view", "label": f"{name} view", "view": name}
            view["detail"] = "a view of the sheet (an hlr sheet: drawn by us, its lines are not TechDraw's)"
            return view
        return _extra_pick(info, name, object_text(obj))
    if "Dim" in type_id:
        return _dimension_pick(sheets, info, obj)
    name = view_name(obj)
    if name is None:
        return {
            "sheet": info.name,
            "kind": "other",
            "label": f"{obj.Label}",
            "text": object_text(obj),
            "detail": f"a {type_id.split('::')[-1]} of the sheet",
        }
    leaf = sub.rpartition(".")[2]
    if leaf.startswith("Edge") and leaf[4:].isdigit():
        return _edge_pick(sheets, info, obj, name, int(leaf[4:]))
    if leaf.startswith("Vertex") and leaf[6:].isdigit():
        return _vertex_pick(sheets, info, obj, name, int(leaf[6:]))
    return _view_pick(info, obj, name)


def read_selection(
    doc: reg.DocumentRec, selected: list[tuple[str, str, str, str]]
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """``(picks, unresolved)`` of the sheet objects among ``selected`` (``gui.selected_entities``'s rows): what each is, and
    the ones that belong to no sheet of ours (a page the engineer made by hand), by label and reason."""
    sheets = Sheets(doc)
    picks: list[dict[str, Any]] = []
    unresolved: list[dict[str, str]] = []
    for obj_name, label, sub, type_id in selected:
        if not type_id.startswith("TechDraw::"):
            continue
        obj = doc.fc_doc.getObject(obj_name)
        pick = read_pick(sheets, obj, sub) if obj is not None else None
        if pick is not None:
            picks.append(pick)
        else:
            unresolved.append(
                {"label": f"{label} · {sub}" if sub else label, "reason": "drawing object of no sheet of ours"}
            )
    return picks, unresolved


def is_sheet_row(row: tuple[str, str, str, str]) -> bool:
    return row[3].startswith("TechDraw::")


# --- from the model to the sheet -------------------------------------------------------------------------------------------


def items_referring_to(doc: reg.DocumentRec, entities: list[tuple[str, str]]) -> dict[str, list[dict[str, str]]]:
    """For each ``(kind, id)`` of ``entities`` (``face`` / ``edge``) the items of the sheets that refer to it: through a
    face id, an edge id, a hole whose wall it is (or whose rim it is), or a point that lies on the face."""
    sheets = Sheets(doc)
    result: dict[str, list[dict[str, str]]] = {eid: [] for _, eid in entities}
    if not sheets:
        return result
    for info in sheets.pages:
        holes = sheets.holes(info)
        texts = sheets.texts(info)
        model = sheets.model(info)
        for item_id, spec in info.items.items():
            found = sheet_refs.refs_of(spec, holes)
            if not (found.faces or found.edges or found.holes or found.points):
                continue
            for kind, entity_id in entities:
                if _refers(model, found, holes, kind, entity_id):
                    result[entity_id].append(
                        {
                            "sheet": info.name,
                            "item": item_id,
                            "item_type": str(spec.get("type", "?")),
                            "text": texts.get(item_id, ""),
                        }
                    )
    return result


def _refers(model: ModelIndex, found: sheet_refs.ItemRefs, holes: list[HoleInfo], kind: str, entity_id: str) -> bool:
    if kind == "face":
        if sheet_refs.refers_to(found, holes, faces=[entity_id]):
            return True
        shape = model.face_shape(entity_id)
        return shape is not None and any(
            shape.distToShape(Part.Vertex(g.fcvec(p)))[0] <= POINT_ON_FACE_MM for p in found.points
        )
    rec = model.edge_rec(entity_id)
    rim = tuple(rec.faces) if rec is not None else ()
    return sheet_refs.refers_to(found, holes, edges=[entity_id], rim_faces=rim)


# --- pointing -------------------------------------------------------------------------------------------------------------


@dataclass
class PagePaint:
    page: Any
    rects: list[tuple[float, float, float, float]] = field(default_factory=list)
    paths: list[list[vm.Pt]] = field(default_factory=list)
    recolor: list[Any] = field(default_factory=list)
    shown: list[str] = field(default_factory=list)


@dataclass
class Plan:
    """What a ``show_entities`` request paints: per sheet the boxes, the lines and the recoloured objects, and the model
    faces / edges to colour in the 3D view; ``notes`` is what could not be shown (and why)."""

    paints: dict[str, PagePaint] = field(default_factory=dict)
    faces: list[str] = field(default_factory=list)
    edges: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def paint(self, info: PageInfo) -> PagePaint:
        return self.paints.setdefault(info.name, PagePaint(info.page))


def _add_unique(target: list[str], values: list[str]) -> None:
    for value in values:
        if value not in target:
            target.append(value)


def _symbol_rect(obj: Any, offset: tuple[float, float]) -> tuple[float, float, float, float] | None:
    """The box of what a symbol draws, on the page where the window shows it (``offset``: how far the window moved it)."""
    svg = str(obj.Symbol)
    box, content = sheet_refs.view_box(svg), sheet_refs.content_bounds(svg)
    if box is None or content is None:
        return None
    vx, vy, vw, vh = box
    cx, cy = vx + vw / 2, vy + vh / 2
    x, y = float(obj.X) + offset[0], float(obj.Y) + offset[1]
    ys = (y - (content[1] - cy), y - (content[3] - cy))
    m = BOX_MARGIN_MM
    return x + content[0] - cx - m, min(ys) - m, x + content[2] - cx + m, max(ys) + m


def _offsets(info: PageInfo) -> dict[str, tuple[float, float]]:
    claimants = [
        (o.Name, float(o.X), float(o.Y))
        for o in info.page.Views
        if hasattr(o, "X") and "Dim" not in str(o.TypeId) and str(o.TypeId) != "TechDraw::DrawViewBalloon"
    ]
    try:
        return gui.displayed_offsets(info.page, claimants)
    except Exception:  # noqa: BLE001 - painting is cosmetic: a window that cannot be read paints where the objects say
        return {}


def _view_rect(info: PageInfo, view: Any) -> tuple[float, float, float, float]:
    x, y = drawing._page_position(info.page, view)  # noqa: SLF001
    box = drawing._view_box(view)  # noqa: SLF001
    m = VIEW_MARGIN_MM
    return x + box.XMin - m, y - box.YMax - m, x + box.XMax + m, y - box.YMin + m


def _point_at(sheets: Sheets, infos: list[PageInfo], what: str, plan: Plan) -> None:
    """One thing the agent named: an item id, or ``view:<name>``."""
    name = what.removeprefix("item:")
    is_view = name.startswith("view:")
    hit = False
    for info in infos:
        paint = plan.paint(info)
        if is_view:
            hit |= _point_at_view(info, name[5:], paint)
            continue
        objects = [o for o in page_views(info.page) if _is_item(info, o, name)]
        if name not in info.items and not objects:
            continue
        hit = True
        offsets = None
        for obj in objects:
            if "Symbol" in obj.PropertiesList:
                offsets = offsets if offsets is not None else _offsets(info)
                rect = _symbol_rect(obj, offsets.get(obj.Name, (0.0, 0.0)))
                if rect is not None:
                    paint.rects.append(rect)
            elif gui.can_recolor(obj):
                paint.recolor.append(obj)
            else:
                plan.notes.append(f"{name}: its {str(obj.TypeId).split('::')[-1]} cannot be coloured")
        edges = sheet_native.cosmetic_paths(info.page, name)  # its centre lines and thread arcs are the views' own edges
        paint.paths.extend(edges)
        if not objects and not edges:
            plan.notes.append(
                f"{name}: the item has nothing on the sheet now (it failed to draw, or it is a title block field)"
            )
        paint.shown.append(name)
    if not hit:
        known = sorted({i for info in infos for i in info.items})
        views = sorted({v for info in infos for o in page_views(info.page) if (v := view_name(o))})
        raise invalid_request(
            f"{what!r} is neither an item of the sheet nor one of its views; items: {', '.join(known) or 'none'}; "
            f"views: {', '.join(f'view:{v}' for v in views) or 'none'}"
        )


def _is_item(info: PageInfo, obj: Any, item_id: str) -> bool:
    tag = tag_of(obj)
    if tag is None:
        return False
    kind, name = tag
    return (kind == "item" and name == item_id) or (kind == "overall" and info.overall == item_id)


def _point_at_view(info: PageInfo, name: str, paint: PagePaint) -> bool:
    view = _find_view(info, name)
    if view is not None:
        paint.rects.append(_view_rect(info, view))
        paint.shown.append(f"view:{name}")
        return True
    for obj in page_views(info.page):  # an hlr sheet's view is a symbol of ours
        if tag_of(obj) == ("view", name) and "Symbol" in obj.PropertiesList:
            rect = _symbol_rect(obj, (0.0, 0.0))
            if rect is not None:
                paint.rects.append(rect)
                paint.shown.append(f"view:{name}")
                return True
    return False


def _add_model_of_item(sheets: Sheets, info: PageInfo, item_id: str, plan: Plan) -> None:
    holes = sheets.holes(info)
    found = sheet_refs.refs_of(info.items[item_id], holes)
    _add_unique(plan.faces, sheet_refs.faces_of_refs(found, holes))
    _add_unique(plan.edges, list(found.edges))


def _paint_model_on_views(sheets: Sheets, info: PageInfo, faces: list[str], edges: list[str], plan: Plan) -> None:
    """The lines of the views of ``info`` that draw the given model faces and edges (a face by its boundary and, for a
    cylinder seen across its axis, its silhouette)."""
    model = sheets.model(info)
    target_edges = set(edges)
    for face in faces:
        target_edges.update(model.face_edges(face))
    target_faces = set(faces)
    paint = plan.paint(info)
    candidates = model.all_edges()
    cylinders = model.cylinders()
    for view in page_views(info.page):
        name = view_name(view)
        if name is None or is_derived(view):
            continue
        frame = frame_of(view)
        projected = vm.project_edges(frame, candidates)
        scale = float(view.getScale())
        vx, vy = drawing._page_position(info.page, view)  # noqa: SLF001
        lines = 0
        for i in range(len(view.getVisibleEdges())):
            geometry = view.getEdgeByIndex(i)
            samples = edge_points(geometry)
            match = vm.match_edge(frame, samples, projected, cylinders, False)
            if (set(match.edges) & target_edges) or (set(match.faces) & target_faces):
                points = edge_points(geometry, deflection=0.02, limit=MAX_PATH_POINTS)
                paint.paths.append([(vx + x * scale, vy + y * scale) for x, y in points])
                lines += 1
        if lines:
            paint.shown.append(f"{name} view: {lines} line{'s' if lines != 1 else ''}")


def plan(
    doc: reg.DocumentRec,
    items: list[str],
    faces: list[str],
    edges: list[str],
    where: str,
    sheet: str | None,
) -> Plan:
    """What to paint for ``items`` (item ids, ``view:<name>``) and the model ``faces`` / ``edges``, by ``where`` (see
    ``ShowEntitiesRequest``): items on the sheet; entities in 3D (the caller does it) and, with ``sheet`` or ``both``, the
    items that refer to them and the lines of the views that draw them; with ``3d`` or ``both`` the faces and edges the
    items refer to in 3D."""
    sheets = Sheets(doc)
    result = Plan()
    if where in ("auto", "3d", "both"):
        _add_unique(result.faces, faces)
        _add_unique(result.edges, edges)
    if not sheets:
        if items or where in ("sheet", "both"):
            raise invalid_request("the document has no drawing sheet: create it first with drawing(action=create)")
        return result
    infos = sheets.by_name(sheet)
    if sheet is not None and not infos:
        raise invalid_request(
            f"no drawing sheet {sheet!r}; the document's sheets: {', '.join(p.name for p in sheets.pages)}"
        )
    for what in items:
        _point_at(sheets, infos, what, result)
        if where in ("3d", "both") and not what.startswith("view:"):
            for info in infos:
                if what.removeprefix("item:") in info.items:
                    _add_model_of_item(sheets, info, what.removeprefix("item:"), result)
    if where in ("sheet", "both") and (faces or edges):
        found = items_referring_to(doc, [("face", f) for f in faces] + [("edge", e) for e in edges])
        referred = [(r["sheet"], r["item"]) for refs in found.values() for r in refs]
        for info in infos:
            for item_id in sorted({item for name, item in referred if name == info.name}):
                _point_at(sheets, [info], item_id, result)
            _paint_model_on_views(sheets, info, faces, edges, result)
    return result


def apply(doc: reg.DocumentRec, result: Plan) -> list[str]:
    """Paint ``result`` on the sheets; the lines that say what was painted where."""
    said: list[str] = []
    for paint in result.paints.values():
        if not (paint.rects or paint.paths or paint.recolor):
            continue
        gui.paint_sheet(doc.fc_doc, paint.page, paint.rects, paint.paths, paint.recolor)
        said.append(f"sheet {paint.page.Label}: {', '.join(paint.shown) or 'painted'}")
    return said
