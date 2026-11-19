"""Handle registry: worker-assigned ids mapped to live TopoShape sub-elements.

Topology matching always happens on the *topo* shape of a body (``Tip.Shape`` for PartDesign bodies, which keeps
``isPartner`` identity across recomputes and undo; ``body.Shape`` is a copy and does not). Geometry reported to
the client comes from the *world* shape (topo shape placed by the body placement), read by element index.
"""

import math
from dataclasses import dataclass
from typing import Any

import geometry as g
import Part
import provenance as prov
from FreeCAD import Placement
from protocol import WorkerError, invalid_reference

COMPONENT_SEPARATOR = "/"
BIG_SURFACE_FACES = 5000
"""A body of loose faces (no solid) with more faces than this registers faces only (``adopt_result``)."""


class _SubShape:
    """The sub-shape a face or edge record stands for, as a lazy attribute: a ``TopoShape`` wrapper of a sub-shape costs
    about 6 KB of memory, so a record made by adopting a body (thousands of them for an imported assembly) keeps only
    the body's shape and its own index and takes the sub-shape out of it (``getElement``, the same TShape as the
    ``Faces``/``Edges`` entry, so ``isSame``/``isPartner`` agree) the first time something asks."""

    _shape: Any = None
    _source: Any = None
    index: int
    sub_name: str

    @property
    def shape(self) -> Any:
        if self._shape is None and self._source is not None:
            self._shape = self._source.getElement(self.sub_name)
        return self._shape

    @shape.setter
    def shape(self, value: Any) -> None:
        self._shape = value
        self._source = None

    def bind(self, source: Any) -> None:
        """Stand for the sub-shape ``index`` of ``source`` without taking it out yet."""
        self._shape = None
        self._source = source


class FaceRec(_SubShape):
    def __init__(
        self, id_: str, body: str, index: int, shape: Any, sig: prov.FaceSig, created_by: str, source: Any = None
    ) -> None:
        self.id = id_
        self.body = body
        self.index = index
        self._shape = shape
        self._source = source  # the body shape ``shape`` is taken from when it is not given
        self.sig = sig
        self.created_by = created_by
        self.modified_by: list[str] = []
        self.stale = False
        self.axis_dir: g.Vec3 | None = None  # intended axis sense for cylinders (hole: -normal, boss: +n)

    @property
    def sub_name(self) -> str:
        return f"Face{self.index + 1}"

    def clone(self) -> "FaceRec":
        c = FaceRec(self.id, self.body, self.index, self._shape, self.sig, self.created_by, self._source)
        c.modified_by = list(self.modified_by)
        c.stale = self.stale
        c.axis_dir = self.axis_dir
        return c


class EdgeRec(_SubShape):
    def __init__(
        self, id_: str, body: str, index: int, shape: Any, sig: prov.EdgeSig, created_by: str, source: Any = None
    ) -> None:
        self.id = id_
        self.body = body
        self.index = index
        self._shape = shape
        self._source = source
        self.sig = sig
        self.created_by = created_by
        self.faces: tuple[str, ...] = ()
        self.stale = False

    @property
    def sub_name(self) -> str:
        return f"Edge{self.index + 1}"

    def clone(self) -> "EdgeRec":
        c = EdgeRec(self.id, self.body, self.index, self._shape, self.sig, self.created_by, self._source)
        c.faces = tuple(self.faces)
        c.stale = self.stale
        return c


class VertexRec:
    def __init__(self, id_: str, body: str, index: int, point: g.Vec3) -> None:
        self.id = id_
        self.body = body
        self.index = index
        self.point = point
        self.edges: list[str] = []
        self.stale = False

    @property
    def sub_name(self) -> str:
        return f"Vertex{self.index + 1}"

    def clone(self) -> "VertexRec":
        c = VertexRec(self.id, self.body, self.index, self.point)
        c.edges = list(self.edges)
        c.stale = self.stale
        return c


_RECORD_PREFIX = {"faces": "face", "edges": "edge", "vertices": "vertex"}


@dataclass(frozen=True)
class Pristine:
    """The sub-shape records of a body that a foreign file brought in and nothing has touched since are not kept (nor
    saved): they are what ``build_pristine`` makes from the body's shape, in the order of the shape, with ids taken from
    these ranges (face ``face:{face_start + index}``, vertex by its index, edge by its index in the shape's edge list:
    seams and degenerated edges take an id nobody uses). The same shape gives the same ids, so a reopened document
    answers to the ids it had, while a big assembly costs a few integers per body instead of a record per face, edge
    and vertex. ``faces_only``: a big body of loose faces registers its faces and nothing else."""

    face_start: int
    n_faces: int
    edge_start: int = 0
    n_edges: int = 0
    vertex_start: int = 0
    n_vertices: int = 0
    faces_only: bool = False

    def owns(self, kind: str, record_id: str) -> bool:
        """Is ``record_id`` (``face:12``) one of the ids of this range."""
        prefix, _, number = record_id.partition(":")
        if prefix != _RECORD_PREFIX[kind] or not number.isdigit():
            return False
        start, count = {
            "faces": (self.face_start, self.n_faces),
            "edges": (self.edge_start, self.n_edges),
            "vertices": (self.vertex_start, self.n_vertices),
        }[kind]
        return start <= int(number) < start + count

    def to_json(self) -> dict[str, Any]:
        return {
            "faces": [self.face_start, self.n_faces],
            "edges": [self.edge_start, self.n_edges],
            "vertices": [self.vertex_start, self.n_vertices],
            "faces_only": self.faces_only,
        }

    @staticmethod
    def from_json(data: dict[str, Any]) -> "Pristine":
        return Pristine(
            int(data["faces"][0]),
            int(data["faces"][1]),
            int(data["edges"][0]),
            int(data["edges"][1]),
            int(data["vertices"][0]),
            int(data["vertices"][1]),
            bool(data["faces_only"]),
        )


class BodyRec:
    def __init__(self, id_: str, kind: str, fc_body: str | None, result_obj: str, created_by: str) -> None:
        self.id = id_
        self.kind = kind  # "partdesign" | "part"
        self.fc_body = fc_body
        self.result_obj = result_obj
        self.created_by = created_by
        self.modified_by: list[str] = []
        self._faces: dict[str, FaceRec] = {}
        self._edges: dict[str, EdgeRec] = {}
        self._vertices: dict[str, VertexRec] = {}
        self.deleted = False
        self.sheet_metal = False  # made by base_flange (and bent by edge_flange): the body the sheet-metal ops work on
        self.reference: Any = None  # the shape this body had when a foreign file brought it in; ``None`` if made here
        self.pristine: Pristine | None = None  # records are (or can be) the ones ``build_pristine`` makes: not saved
        self.doc: DocumentRec | None = None  # the document that builds the records of a lazy body
        self._lazy = False  # ``pristine`` and the records are not built yet: the first access to them builds them
        # caches (not part of snapshots; dropped by ``invalidate`` whenever the shape may have changed)
        self._topo: Any = None
        self._world: Any = None
        self._vols: list[float] | None = None  # volume of each solid
        self._centres: list[g.Vec3] | None = None  # centre of mass of each solid (as slow as the volume: on demand)
        self._area: float | None = None  # surface area; NaN when the CAD gave a number that cannot be one
        self._bbox: tuple[dict[str, Any], int] | None = None  # exact box and faces left out of it (``body_box``)
        self._fbox: tuple[dict[str, Any], int] | None = None  # the quick box and faces left out of it
        self._valid: bool | None = None  # ``Shape.isValid()``
        self._deep: list[str] | None = None  # problems the BOP check found (``queries.is_valid``)

    # the records are built the first time something asks for them (see ``Pristine``)

    @property
    def faces(self) -> dict[str, FaceRec]:
        self._ensure()
        return self._faces

    @faces.setter
    def faces(self, value: dict[str, FaceRec]) -> None:
        self._lazy = False
        self._faces = value

    @property
    def edges(self) -> dict[str, EdgeRec]:
        self._ensure()
        return self._edges

    @edges.setter
    def edges(self, value: dict[str, EdgeRec]) -> None:
        self._lazy = False
        self._edges = value

    @property
    def vertices(self) -> dict[str, VertexRec]:
        self._ensure()
        return self._vertices

    @vertices.setter
    def vertices(self, value: dict[str, VertexRec]) -> None:
        self._lazy = False
        self._vertices = value

    @property
    def is_lazy(self) -> bool:
        """The records are not built (nothing has asked for them): the body is still just its ``pristine`` ranges."""
        return self._lazy

    def _ensure(self) -> None:
        if self._lazy:
            build_pristine(self)

    def live_faces(self) -> list[tuple[str, int]]:
        """``(id, index)`` of every live face. A lazy body answers from its id ranges without building a record (a
        record needs the signature of its face: a pass over the whole shape)."""
        if self._lazy and self.pristine is not None:
            return [(f"face:{self.pristine.face_start + i}", i) for i in range(self.pristine.n_faces)]
        return [(r.id, r.index) for r in self.faces.values() if not r.stale]

    def find(self, kind: str, record_id: str) -> Any:
        """The record ``record_id`` of this body (``kind``: "faces", "edges" or "vertices"), or ``None``; a lazy body
        answers from its id ranges and builds its records only when the id is one of its own."""
        if self._lazy and self.pristine is not None and not self.pristine.owns(kind, record_id):
            return None
        return getattr(self, kind).get(record_id)

    def unchanged_since_adoption(self) -> Pristine | None:
        """``pristine`` when the records are still exactly what it builds (so they need not be saved), else ``None``."""
        spec = self.pristine
        if spec is None or self.modified_by or self.deleted:
            return None
        if self._lazy:
            return spec
        counts = (len(self._faces), len(self._edges), len(self._vertices))
        if counts[0] != spec.n_faces or counts[1] > spec.n_edges or counts[2] != spec.n_vertices:
            return None
        if any(r.stale or r.modified_by or r.axis_dir is not None for r in self._faces.values()):
            return None
        if any(r.stale for r in (*self._edges.values(), *self._vertices.values())):
            return None
        return spec

    def clone(self) -> "BodyRec":
        c = BodyRec(self.id, self.kind, self.fc_body, self.result_obj, self.created_by)
        c.reference = self.reference
        c.sheet_metal = self.sheet_metal
        c.modified_by = list(self.modified_by)
        c.pristine = self.pristine
        c.doc = self.doc
        if self._lazy:  # nothing built yet: the copy builds its own when asked
            c._lazy = True
        else:
            c._faces = {k: v.clone() for k, v in self._faces.items()}
            c._edges = {k: v.clone() for k, v in self._edges.items()}
            c._vertices = {k: v.clone() for k, v in self._vertices.items()}
        c.deleted = self.deleted
        return c

    def invalidate(self) -> None:
        self._topo = None
        self._world = None
        self._vols = None
        self._centres = None
        self._area = None
        self._bbox = None
        self._fbox = None
        self._valid = None
        self._deep = None

    def measures(self) -> tuple[Any, ...]:
        """The shape the caches were taken from and what was measured of it (``restore_measures`` gives them back)."""
        return (self._world, self._vols, self._centres, self._area, self._bbox, self._fbox, self._valid, self._deep)


class SketchRec:
    def __init__(self, id_: str, spec: dict[str, Any], frame: dict[str, Any], profiles: list[dict[str, Any]]) -> None:
        self.id = id_
        self.spec = spec
        self.frame = frame
        self.profiles = profiles
        self.on_face: str | None = None
        self.body: str | None = None
        self.fc_name: str | None = None
        self.named_dimensions: dict[str, float] = {}

    def clone(self) -> "SketchRec":
        c = SketchRec(self.id, self.spec, self.frame, self.profiles)
        c.on_face = self.on_face
        c.body = self.body
        c.fc_name = self.fc_name
        c.named_dimensions = dict(self.named_dimensions)
        return c


class DatumRec:
    """A fixed-pose datum plane (``pose`` = frame) or datum axis (``pose`` = origin + direction).

    Datums are not features: they own no FreeCAD object, no feature-tree node and no undo unit. A sketch on a
    datum plane carries the plane's frame in its own ``Placement`` (``MapMode=Deactivated``), exactly like a
    sketch on a base plane, so the record only has to survive undo (``Snapshot``) and prove a handle is live.
    """

    def __init__(self, id_: str, kind: str, pose: dict[str, Any]) -> None:
        self.id = id_
        self.kind = kind  # "plane" | "axis"
        self.pose = pose

    def clone(self) -> "DatumRec":
        return DatumRec(self.id, self.kind, self.pose)


class FeatureRec:
    def __init__(self, id_: str, kind: str, label: str, params: dict[str, Any]) -> None:
        self.id = id_
        self.kind = kind
        self.label = label
        self.params = params
        self.fc_names: list[str] = []
        self.fc_main: str | None = None
        self.named_dimensions: dict[str, float] = {}
        self.sketch: str | None = None
        self._created_faces: list[str] = []
        self.modified_faces: list[str] = []
        self._created_edges: list[str] = []
        self.created_bodies: list[str] = []
        self.modified_bodies: list[str] = []
        self.instance_count: int | None = None
        self.face_count: int | None = None  # a component: the faces and edges of its part, counted instead of listed
        self.edge_count: int | None = None
        self.component: str | None = None
        self.mate: str | None = None
        self.warnings: list[str] = []
        self.journal: Any = None  # ``journal.Journal``: what the op did to the FreeCAD document, for undo_last
        # bodies adopted lazily (``Pristine``) whose faces and edges this feature created: listed on first use
        self.lazy_bodies: list[str] = []
        self.doc: DocumentRec | None = None

    @property
    def created_faces(self) -> list[str]:
        self.resolve()
        return self._created_faces

    @created_faces.setter
    def created_faces(self, value: list[str]) -> None:
        self.resolve()
        self._created_faces = value

    @property
    def created_edges(self) -> list[str]:
        self.resolve()
        return self._created_edges

    @created_edges.setter
    def created_edges(self, value: list[str]) -> None:
        self.resolve()
        self._created_edges = value

    def resolve(self) -> None:
        """List the faces and edges of the lazily adopted bodies (their records are built for it)."""
        if not self.lazy_bodies or self.doc is None:
            return
        names, self.lazy_bodies = self.lazy_bodies, []
        for name in names:
            body = self.doc.bodies.get(name)
            if body is None:
                continue
            self._created_faces.extend(r.id for r in body.faces.values())
            self._created_edges.extend(r.id for r in body.edges.values())


@dataclass(frozen=True)
class Slice:
    """A body of an inserted part that is still what a foreign file brought in (``Pristine``): the component keeps where
    the body sits in its compound (the index each kind of sub-shape starts at) and nothing else. Its records are made
    from the body's shape, with the ids the part's body has, the first time something asks for one of them."""

    body: str
    pristine: Pristine
    created_by: str
    face_at: int
    edge_at: int
    vertex_at: int


class ComponentRec:
    """An instance of a part document. Its face / edge / vertex records are those of the part's bodies (same local ids,
    indexes shifted to the compound ``base_shape``). The bodies a foreign file brought in and nothing has touched
    (``Slice``) cost a few integers each until a record of theirs is asked for, and then only that body's records are
    made: a vendor assembly of hundreds of solids and tens of thousands of faces is inserted without describing any of
    them. The records of every other body are copied from the part's when the component is filled."""

    def __init__(self, id_: str, part_doc: str, fc_part: str, fc_shape: str, base_shape: Any) -> None:
        self.id = id_
        self.part_doc = part_doc
        self.fc_part = fc_part
        self.fc_shape = fc_shape  # the first App::Link of the component
        self.fc_links: list[str] = [fc_shape] if fc_shape else []  # one App::Link per solid body of the part document
        self.base_shape = base_shape  # compound of the part's bodies in part-document coordinates
        self.fixed = False
        self.created_by = ""
        self.body_ids: list[str] = []  # local body ids
        self.body_solids: dict[str, Any] = {}  # local body id -> its unplaced shape
        self.mates: list[str] = []
        self.foreign = False  # every body is the untouched shape a foreign file brought in
        self._faces: dict[str, FaceRec] = {}  # keyed by local id; index = face index in base_shape
        self._edges: dict[str, EdgeRec] = {}
        self._vertices: dict[str, VertexRec] = {}
        self.face_counts: dict[str, int] = {}  # local body id -> faces of its shape
        self.face_total = 0  # faces and edges of the whole compound (counted once, when the component is filled)
        self.edge_total = 0
        self._slices: dict[str, Slice] = {}  # local body id -> where it sits, for the bodies with lazy records
        self._pending: dict[str, Slice] = {}  # the slices whose records are not made yet
        self._placed: Any = None
        self._placed_key: Any = None
        # measures of the base shape (they do not depend on the pose): kept until the component is filled again
        self._volumes: list[float] | None = None
        self._body_volumes: dict[str, float] | None = None
        self._centres: list[g.Vec3] | None = None
        self._area: float | None = None
        self._valid: bool | None = None
        self._unchecked: list[str] = []  # local bodies whose validity check was left for later (VALIDITY_FACE_LIMIT)
        self._boxes: dict[bool, tuple[Any, tuple[dict[str, Any], int]]] = {}  # exact? -> (pose key, box)
        # box of each body in part axes, exact (True) or quick (False), measured once and kept for any pose
        self._body_boxes: dict[bool, dict[str, tuple[dict[str, Any], int]]] = {True: {}, False: {}}
        self._invalid: list[str] = []  # local bodies that failed the validity check

    # --- records -------------------------------------------------------------------------------------------------

    @property
    def faces(self) -> dict[str, FaceRec]:
        self.build_all()
        return self._faces

    @property
    def edges(self) -> dict[str, EdgeRec]:
        self.build_all()
        return self._edges

    @property
    def vertices(self) -> dict[str, VertexRec]:
        self.build_all()
        return self._vertices

    def reset_records(self) -> None:
        self._faces, self._edges, self._vertices = {}, {}, {}
        self._slices, self._pending = {}, {}
        self.invalidate_measures()

    def invalidate_measures(self) -> None:
        self._volumes = self._centres = self._area = self._valid = self._body_volumes = None
        self._unchecked = []
        self._boxes = {}
        self._body_boxes = {True: {}, False: {}}
        self._invalid = []

    def defer(self, slice_: Slice) -> None:
        """The records of this body are made when something asks for one of them."""
        self._slices[slice_.body] = slice_
        self._pending[slice_.body] = slice_

    def build_slice(self, body_id: str) -> None:
        """Make the records of one lazy body (``registry._build_records`` on its shape, shifted into the compound)."""
        slice_ = self._pending.pop(body_id, None)
        if slice_ is None:
            return
        shape = self.body_solids[body_id]
        scratch = BodyRec(body_id, "part", None, "", slice_.created_by)
        _build_records(scratch, shape, slice_.pristine)
        for rec in scratch._faces.values():
            rec.index += slice_.face_at
            rec.bind(self.base_shape)
        for rec in scratch._edges.values():
            rec.index += slice_.edge_at
            rec.bind(self.base_shape)
        for vertex in scratch._vertices.values():
            vertex.index += slice_.vertex_at
        self._faces.update(scratch._faces)
        self._edges.update(scratch._edges)
        self._vertices.update(scratch._vertices)

    def build_all(self) -> None:
        for body_id in list(self._pending):
            self.build_slice(body_id)

    def find(self, kind: str, record_id: str) -> Any:
        """The record ``record_id`` (``kind``: "faces", "edges" or "vertices") or ``None``; only the lazy body the id
        belongs to is made."""
        store = {"faces": self._faces, "edges": self._edges, "vertices": self._vertices}[kind]
        rec = store.get(record_id)
        if rec is not None or not self._pending:
            return rec
        for body_id, slice_ in self._pending.items():
            if slice_.pristine.owns(kind, record_id):
                self.build_slice(body_id)
                return store.get(record_id)
        return None

    def records(self, kind: str, bodies: set[str] | None = None) -> list[Any]:
        """The live records of ``kind`` of the given local bodies (all of them: ``None``); the other lazy bodies stay
        unmade."""
        for body_id in list(self._pending):
            if bodies is None or body_id in bodies:
                self.build_slice(body_id)
        store = {"faces": self._faces, "edges": self._edges, "vertices": self._vertices}[kind]
        return [r for r in store.values() if not r.stale and (bodies is None or r.body in bodies)]

    # --- measures ------------------------------------------------------------------------------------------------

    def _solids(self) -> list[Any]:
        return list(self.base_shape.Solids)  # also for one solid: a Part.Shape wrapper of it lacks the Solid methods

    def volumes(self) -> list[float]:
        """The volume of each solid of the base shape, measured once (the pose does not change it)."""
        if self._volumes is None:
            self._volumes = [float(s.Volume) for s in self._solids()]
        return self._volumes

    def body_volume(self, body_id: str) -> float:
        """The volume of the solids of one body of the component, from the volumes already measured."""
        if self._body_volumes is None:
            volumes, first, out = self.volumes(), 0, {}
            for local in self.body_ids:
                count = self.body_solids[local].countElement("Solid")
                out[local] = sum(volumes[first : first + count])
                first += count
            self._body_volumes = out
        return self._body_volumes[body_id]

    def centres(self) -> list[g.Vec3]:
        """The centre of mass of each solid in part axes, measured once (the pose moves it: apply the placement)."""
        if self._centres is None:
            self._centres = [g.vec(s.CenterOfMass) for s in self._solids()]
        return self._centres

    def body_local_box(self, body_id: str, exact: bool) -> tuple[dict[str, Any], int]:
        """``(box, faces left out)`` of one body in part axes, measured once. A quick box asked for is the exact one
        when that was measured already (the exact box is a pass over every curved surface of the body)."""
        for kind in (True, exact):
            found = self._body_boxes[kind].get(body_id)
            if found is not None:
                return found
        measured = box_without_garbage(self.body_solids[body_id], exact)
        self._body_boxes[exact][body_id] = measured
        return measured

    def local_box(self, exact: bool) -> tuple[dict[str, Any], int]:
        """``(box, faces left out)`` of the whole component in part axes: the union of its bodies' boxes."""
        union: dict[str, Any] | None = None
        left_out = 0
        for body_id in self.body_ids:
            box, missing = self.body_local_box(body_id, exact)
            union = g.bbox_union(union, box)
            left_out += missing
        return (union if union is not None else dict(g.ZERO_BBOX)), left_out

    def surface_area(self) -> float:
        """The surface area of the base shape, measured once; NaN when the CAD gave a number that cannot be one."""
        if self._area is None:
            area = float(self.base_shape.Area)
            self._area = area if plausible_area(area, self.base_shape) else math.nan
        return self._area

    def clone(self) -> "ComponentRec":
        c = ComponentRec(self.id, self.part_doc, self.fc_part, self.fc_shape, self.base_shape)
        c.fc_links = list(self.fc_links)
        c.fixed = self.fixed
        c.created_by = self.created_by
        c.foreign = self.foreign
        c._slices = dict(self._slices)
        c._pending = dict(self._slices)  # what a lazy body made is made again when asked: a copy keeps no records of it
        c._faces = {k: v.clone() for k, v in self._faces.items() if v.body not in self._slices}
        c._edges = {k: v.clone() for k, v in self._edges.items() if v.body not in self._slices}
        c._vertices = {k: v.clone() for k, v in self._vertices.items() if v.body not in self._slices}
        c.body_ids = list(self.body_ids)
        c.body_solids = dict(self.body_solids)
        c.face_counts, c.face_total, c.edge_total = self.face_counts, self.face_total, self.edge_total
        c.mates = list(self.mates)
        c._volumes, c._centres, c._area, c._valid = self._volumes, self._centres, self._area, self._valid
        c._unchecked = list(self._unchecked)
        c._body_boxes = {kind: dict(boxes) for kind, boxes in self._body_boxes.items()}
        c._invalid = list(self._invalid)
        return c


class JointRec:
    """A joint of an assembly: ``b`` is carried by it, ``a`` (``None``: the ground) stays. The axis is in assembly axes
    and ``base`` is the pose of ``b`` at value 0 (the pose it had when the joint was made); the pose at a value is
    ``delta(value) * base`` (``ops_joints.pose``). ``fc_objects`` are the FreeCAD joint objects the engineer sees."""

    def __init__(
        self,
        id_: str,
        joint_type: str,
        a: str | None,
        b: str,
        axis: tuple[g.Vec3, g.Vec3] | None,
        base: Placement,
        low: float | None,
        high: float | None,
    ) -> None:
        self.id = id_
        self.joint_type = joint_type
        self.a = a
        self.b = b
        self.axis = axis  # (origin, unit direction)
        self.base = base
        self.low = low
        self.high = high
        self.value = 0.0
        self.fc_objects: list[str] = []
        self.created_by = ""

    def clone(self) -> "JointRec":
        c = JointRec(self.id, self.joint_type, self.a, self.b, self.axis, Placement(self.base), self.low, self.high)
        c.value = self.value
        c.fc_objects = list(self.fc_objects)
        c.created_by = self.created_by
        return c


class Snapshot:
    def __init__(self, doc: "DocumentRec") -> None:
        self.bodies = {k: v.clone() for k, v in doc.bodies.items()}
        self.sketches = {k: v.clone() for k, v in doc.sketches.items()}
        self.datums = {k: v.clone() for k, v in doc.datums.items()}
        self.components = {k: v.clone() for k, v in doc.components.items()}
        self.joints = {k: v.clone() for k, v in doc.joints.items()}
        self.counters = dict(doc.counters)


class DocumentRec:
    def __init__(self, id_: str, kind: str, name: str, fc_doc: Any) -> None:
        self.id = id_
        self.kind = kind
        self.name = name
        self.fc_doc = fc_doc
        self.counters: dict[str, int] = {}
        self.bodies: dict[str, BodyRec] = {}
        self.sketches: dict[str, SketchRec] = {}
        self.datums: dict[str, DatumRec] = {}
        self.features: list[FeatureRec] = []
        self.components: dict[str, ComponentRec] = {}
        self.joints: dict[str, JointRec] = {}
        self.undo_stack: list[Snapshot] = []
        self.borrowed = False
        """The engineer had this file open already (by hand): the document is his. Closing it here only forgets it, and
        its window is never closed."""
        self.windowless = False
        """The harness loaded the document without a window, or closed its window once an assembly used it
        (``windows.py``): ``set_active_document`` gives it a window again."""
        self.object_volumes: dict[str, tuple[Any, float, int]] = {}
        """Cache of ``edit.capture`` (not part of snapshots): per object, the shape it had when its volume was measured,
        the volume and its solids. The same shape is the same volume."""

    def next_id(self, kind: str) -> str:
        self.counters[kind] = self.counters.get(kind, 0) + 1
        return f"{kind}:{self.counters[kind]}"

    def snapshot(self) -> Snapshot:
        return Snapshot(self)

    def restore(self, snap: Snapshot) -> None:
        self.bodies = snap.bodies
        self.sketches = snap.sketches
        self.datums = snap.datums
        self.components = snap.components
        self.joints = snap.joints
        self.counters = snap.counters
        for body in self.bodies.values():
            body.invalidate()
        for comp in self.components.values():
            comp._placed = None

    def feature(self, feature_id: str, operation_id: str | None) -> FeatureRec:
        for f in self.features:
            if f.id == feature_id:
                return f
        raise invalid_reference(feature_id, operation_id)

    def live_bodies(self) -> list[BodyRec]:
        return [b for b in self.bodies.values() if not b.deleted]


# --- shapes -------------------------------------------------------------------------------------------------------


def topo_shape(doc: DocumentRec, body: BodyRec) -> Any:
    """Shape used for topology matching (``Tip.Shape`` for PartDesign; placed result for Part). May be null."""
    if body._topo is not None:
        return body._topo
    if body.kind == "partdesign":
        fc_body = doc.fc_doc.getObject(body.fc_body)
        tip = fc_body.Tip if fc_body is not None else None
        shape = tip.Shape if tip is not None else Part.Shape()
    else:
        obj = doc.fc_doc.getObject(body.result_obj)
        shape = Part.getShape(obj) if obj is not None else Part.Shape()
    body._topo = shape
    return shape


def _solids_of(doc: DocumentRec, body: BodyRec) -> list[Any]:
    shape = world_shape(doc, body)
    return [shape] if shape.ShapeType == "Solid" else list(shape.Solids)


def body_volumes(doc: DocumentRec, body: BodyRec) -> list[float]:
    """The volume of each solid of a body's world shape, measured once per shape (a pass over the whole shape, and
    ``mass_properties`` asks for them on every operation)."""
    if body._vols is None:
        body._vols = [float(s.Volume) for s in _solids_of(doc, body)]
    return body._vols


def body_centres(doc: DocumentRec, body: BodyRec) -> list[g.Vec3]:
    """The centre of mass of each solid: another pass over the shape, taken only by who wants a centroid."""
    if body._centres is None:
        body._centres = [g.vec(s.CenterOfMass) for s in _solids_of(doc, body)]
    return body._centres


def body_parts(doc: DocumentRec, body: BodyRec) -> list[tuple[float, g.Vec3]]:
    """``[(volume, centre of mass)]`` per solid."""
    return list(zip(body_volumes(doc, body), body_centres(doc, body), strict=True))


def body_area(doc: DocumentRec, body: BodyRec) -> float:
    """The surface area of a body's world shape, measured once and only when asked (it is a pass over every face, and
    only ``inspect properties`` wants it); NaN when the CAD returned a number that cannot be an area of this shape."""
    if body._area is None:
        shape = world_shape(doc, body)
        area = float(shape.Area)
        if not plausible_area(area, shape):
            area = math.nan  # a garbage number (an invalid shape): never summed, the caller says it was left out
        body._area = area
    return body._area


def body_mass(doc: DocumentRec, body: BodyRec) -> tuple[float, list[tuple[float, g.Vec3]]]:
    """``(area, [(volume, centre of mass) per solid])``."""
    return body_area(doc, body), body_parts(doc, body)


PLAUSIBLE_AREA_FACTOR = 1000.0
"""A surface area above this many times what the bounding box allows (its surface, or the square of its diagonal for a
flat one) is not a measure of the shape: the CAD integrates garbage over an invalid face."""
MAX_PLAUSIBLE_AREA_MM2 = 1e15
MAX_BOX_EXTENT_MM = 1e6
"""A bounding box wider than this (a kilometre) in any direction, or reaching further than ``MAX_BOX_COORD_MM`` from the
origin, or not finite, is not the box of a part: an unbounded or garbage face of an invalid shape makes one. Such a box
is never reported nor joined to another."""
MAX_BOX_COORD_MM = 1e7


def sane_box(box: Any) -> bool:
    """Is ``box`` (a FreeCAD ``BoundBox``) the box of something real: valid, finite and not absurdly big."""
    if not box.isValid():
        return False
    values = (box.XMin, box.XMax, box.YMin, box.YMax, box.ZMin, box.ZMax)
    if not all(math.isfinite(v) and abs(v) <= MAX_BOX_COORD_MM for v in values):
        return False
    return max(box.XLength, box.YLength, box.ZLength) <= MAX_BOX_EXTENT_MM


def plausible_area(area: float, shape: Any) -> bool:
    """Is ``area`` (mm2) a believable surface area of ``shape``: finite, not absurdly large, and not vastly above what
    its bounding box allows (a shape whose box is not real has no area to believe)."""
    if not math.isfinite(area) or area < 0 or area > MAX_PLAUSIBLE_AREA_MM2:
        return False
    box = shape.BoundBox
    if not box.isValid():
        return area == 0.0
    if not sane_box(box):
        return False
    dx, dy, dz = box.XLength, box.YLength, box.ZLength
    allowed = max(2.0 * (dx * dy + dy * dz + dz * dx), dx * dx + dy * dy + dz * dz)
    return area <= PLAUSIBLE_AREA_FACTOR * max(allowed, 1.0)


VALIDITY_FACE_LIMIT = 500
"""A body of loose faces (no solid) that a foreign file brought in and with more faces than this does not get the cheap
validity check unless asked (deep): ``isValid()`` takes about as long as reading the file for thousands of curved
faces."""


def validity_unchecked(body: BodyRec, faces: int, has_solids: bool) -> bool:
    return body.reference is not None and not has_solids and faces > VALIDITY_FACE_LIMIT


def _box_of(shape: Any, exact: bool) -> Any:
    if exact:
        return g.exact_box(shape)
    return shape.BoundBox


def box_without_garbage(shape: Any, exact: bool) -> tuple[dict[str, Any], int]:
    """``(box, faces left out)``: the box of ``shape`` (the exact one, or the quick ``BoundBox``). When that box is not
    real (``sane_box``) the box is the union of the boxes of the faces that have a real one, and the number of faces
    that were left out says how many did not; a void shape is the zero box."""
    box = _box_of(shape, exact)
    if sane_box(box):
        return g.bbox_dict(box), 0
    if not box.isValid() or shape.isNull():
        return dict(g.ZERO_BBOX), 0
    union: dict[str, Any] | None = None
    left_out = 0
    for face in shape.Faces:
        try:
            face_box = _box_of(face, exact)
        except Exception:  # noqa: BLE001 - a face the CAD cannot box is one without a real box
            left_out += 1
            continue
        if not sane_box(face_box):
            left_out += 1
            continue
        union = g.bbox_union(union, g.bbox_dict(face_box))
    return (union if union is not None else dict(g.ZERO_BBOX)), left_out


def body_box(doc: DocumentRec, body: BodyRec, exact: bool = True) -> tuple[dict[str, Any], int]:
    """``(box, faces left out)`` of the body's world shape, measured once per shape. The exact box is the slow one for a
    body of thousands of curved faces; the quick one (``exact`` False) is the exact one when that was already
    measured."""
    if body._bbox is not None:
        return body._bbox
    if not exact and body.reference is not None:  # only a body a file brought in, untouched, gets the quick one
        if body._fbox is None:
            body._fbox = box_without_garbage(world_shape(doc, body), False)
        return body._fbox
    body._bbox = box_without_garbage(world_shape(doc, body), True)
    return body._bbox


def body_bbox(doc: DocumentRec, body: BodyRec) -> dict[str, Any]:
    """The exact box of the body (see ``body_box``)."""
    return body_box(doc, body)[0]


def body_placement(doc: DocumentRec, body: BodyRec) -> Placement:
    if body.kind == "partdesign":
        fc_body = doc.fc_doc.getObject(body.fc_body)
        return fc_body.Placement if fc_body is not None else Placement()
    return Placement()


def world_shape(doc: DocumentRec, body: BodyRec) -> Any:
    """Topo shape placed in document coordinates; same element order as the topo shape (``copy`` keeps it)."""
    if body._world is not None:
        return body._world
    shape = topo_shape(doc, body)
    placement = body_placement(doc, body)
    if shape.isNull() or g.is_identity_placement(placement):
        body._world = shape
    else:
        placed = shape.copy()
        placed.Placement = placement.multiply(placed.Placement)
        body._world = placed
    return body._world


def reread_shape(doc: DocumentRec, body: BodyRec) -> Any:
    """The body's world shape read again from the document. What was measured of it is dropped only when the shape is
    another one: the same shape (same ``TShape`` and location, so the same geometry) keeps its volumes, centres, box
    and validity, which cost a pass over every face each. A body whose placement is not the identity gets a copy of its
    shape every time: never the same one, so it is measured again."""
    old = body._world
    if old is None or old.isNull():
        body.invalidate()
        return world_shape(doc, body)
    kept = body.measures()
    body.invalidate()
    fresh = world_shape(doc, body)
    restore_measures(doc, body, kept)
    return fresh


def restore_measures(doc: DocumentRec, body: BodyRec, kept: tuple[Any, ...]) -> None:
    """Give back to ``body`` what ``body.measures()`` held when it still has that very shape (``refresh`` re-resolves
    the records and drops the measures of a shape that is the same one)."""
    world = kept[0]
    if world is None or world.isNull():
        return
    now = world_shape(doc, body)
    if not now.isNull() and now.isSame(world):
        body._vols, body._centres, body._area, body._bbox, body._fbox, body._valid, body._deep = kept[1:]


def adopt_result(doc: DocumentRec, obj: Any, feature: FeatureRec) -> str | None:
    """Register the solid result ``obj`` (a PartDesign body, or a Part object no other solid consumes) as a body owned
    by ``feature``: fresh face / edge / vertex handles, minted by signature. Used by ``persist.adopt`` (a file the
    harness never wrote) and by ``run_native`` (geometry a fragment created outside any registered body). Returns a
    note when the object has no shape (adopted empty) or its handles could not be minted (not adopted), ``None``
    otherwise."""
    is_body = str(obj.TypeId) == "PartDesign::Body"
    body = BodyRec(
        doc.next_id("body"),
        "partdesign" if is_body else "part",
        str(obj.Name) if is_body else None,
        str(obj.Name),
        feature.id,
    )
    doc.bodies[body.id] = body
    feature.created_bodies.append(body.id)
    shape = topo_shape(doc, body)
    if shape.isNull():
        return f"{obj.Name} has no shape and was adopted empty"
    faces_only = shape.countElement("Solid") == 0 and shape.countElement("Face") > BIG_SURFACE_FACES
    try:
        # no record is built here: the body owns ranges of ids, and its faces, edges and vertices are made from its
        # shape when something asks for them (and are not saved while nothing has changed the body)
        body.pristine = reserve(doc, shape, faces_only)
    except Exception as exc:  # noqa: BLE001 - a shape the handle machinery cannot describe must not fail the caller
        del doc.bodies[body.id]
        feature.created_bodies.remove(body.id)
        return f"{obj.Name} could not be adopted ({type(exc).__name__}: {exc}); it is not measured"
    body.doc = doc
    body._lazy = True
    feature.doc = doc
    feature.lazy_bodies.append(body.id)
    if faces_only:
        return (
            f"{obj.Name} is a surface body of {body.pristine.n_faces} faces: its faces have handles, its edges and "
            "vertices do not (they would take gigabytes of memory); sewing it into solids gives them"
        )
    return None


def component_placement(doc: DocumentRec, comp: ComponentRec) -> Placement:
    return doc.fc_doc.getObject(comp.fc_part).Placement


def placed_component_shape(doc: DocumentRec, comp: ComponentRec) -> Any:
    placement = component_placement(doc, comp)
    key = tuple(placement.toMatrix().A)
    if comp._placed is not None and comp._placed_key == key:
        return comp._placed
    placed = Part.Shape(comp.base_shape)  # the same geometry under its own Location: no copy of the surfaces
    placed.Placement = placement.multiply(placed.Placement)
    comp._placed = placed
    comp._placed_key = key
    return placed


def placed_component_body(doc: DocumentRec, comp: ComponentRec, local_body: str) -> Any:
    placement = component_placement(doc, comp)
    placed = Part.Shape(comp.body_solids[local_body])
    placed.Placement = placement.multiply(placed.Placement)
    return placed


def moved_box(box: dict[str, Any], placement: Placement) -> dict[str, Any] | None:
    """The box of a bound after the pose, when that is exact: the pose only moves it or turns it by a multiple of 90
    degrees about the axes (the corners map to corners). ``None`` when the pose turns it any other way: the shape has to
    be measured again."""
    matrix = placement.toMatrix().A
    rows = [matrix[0:3], matrix[4:7], matrix[8:11]]
    if any(min(abs(v), abs(abs(v) - 1.0)) > 1e-9 for row in rows for v in row):
        return None
    corners = [g.apply_placement(placement, corner) for corner in g.bbox_corners(box)]
    low = tuple(round(min(c[i] for c in corners), 9) for i in range(3))
    high = tuple(round(max(c[i] for c in corners), 9) for i in range(3))
    return {"min": g.clean(low), "max": g.clean(high)}


def component_box(doc: DocumentRec, comp: ComponentRec, exact: bool = True) -> tuple[dict[str, Any], int]:
    """``(box, faces left out)`` of the component in the assembly, measured once per pose (the exact box is a pass over
    every curved surface; asked on every step it would cost as much as the step). The box of each body is measured once
    in the part's axes and the pose moves it (any pose that keeps the axes: a move, a turn of 90 degrees). A component
    made of what a foreign file brought in may answer its quick box when ``exact`` is False, as such a body does
    (``body_box``)."""
    placed = placed_component_shape(doc, comp)
    key = comp._placed_key
    for kind in (True, exact):  # the exact box already measured is the quick one too
        cached = comp._boxes.get(kind)
        if cached is not None and cached[0] == key:
            return cached[1]
    if not exact and not comp.foreign:
        exact = True
    local, left_out = comp.local_box(exact)
    box = moved_box(local, component_placement(doc, comp))
    result = (box, left_out) if box is not None else box_without_garbage(placed, exact)
    comp._boxes[exact] = (key, result)
    return result


# --- refresh --------------------------------------------------------------------------------------------------------


class Diff:
    def __init__(self) -> None:
        self.created_faces: list[str] = []
        self.modified_faces: list[str] = []
        self.removed_faces: list[str] = []
        self.created_edges: list[str] = []
        self.removed_edges: list[str] = []


def _is_partner(a: Any, b: Any) -> bool:
    try:
        return bool(a.isPartner(b))
    except Exception:  # noqa: BLE001 - OCC raises on null shapes
        return False


def hash_index(shapes: list[Any]) -> dict[int, list[int]]:
    """Positions of ``shapes`` by ``hashCode`` (equal shapes hash alike): finding a sub-shape among thousands is then a
    lookup, not a walk over all of them."""
    out: dict[int, list[int]] = {}
    for position, shape in enumerate(shapes):
        out.setdefault(shape.hashCode(), []).append(position)
    return out


def find_shape(index: dict[int, list[int]], shapes: list[Any], wanted: Any) -> int | None:
    """Position of the shape among ``shapes`` that IS ``wanted`` (same TShape and Location), using ``hash_index``."""
    for position in index.get(wanted.hashCode(), ()):
        if shapes[position].isSame(wanted):
            return position
    return None


def edge_face_table(all_edges: list[Any], faces: list[Any]) -> list[list[int]]:
    """For each edge of ``all_edges`` the positions of the ``faces`` it bounds, in one pass over the faces. (The
    CAD's own ``ancestorsOfType`` looks the ancestors up from scratch on every call: quadratic in the faces of a
    big shape.)"""
    index = hash_index(all_edges)
    table: list[list[int]] = [[] for _ in all_edges]
    for j, face in enumerate(faces):
        for edge in face.Edges:
            position = find_shape(index, all_edges, edge)
            if position is not None:
                table[position].append(j)
    return table


def is_model_edge(edge: Any, around: list[Any]) -> bool:
    """Is ``edge`` an edge of the model (one a record stands for)? Not a seam of one of the faces ``around`` it (a
    cylinder, a cone, a sphere closes on itself along one), not a degenerated edge (the apex of a cone, the pole of a
    sphere: it has no length, and a file written by another CAD may give it a curve all the same). Free edges (one face
    round them) and non-manifold ones (three or more) ARE edges: they have a record, and what needs exactly two faces
    refuses them when asked."""
    if any(edge.isSeam(face) for face in around):
        return False
    if getattr(edge, "Degenerated", False):
        return False
    try:
        edge.Curve  # noqa: B018 - FreeCAD raises "undefined curve type" for a degenerated edge without a curve
    except Exception:  # noqa: BLE001
        return False
    return True


def _visible_edge_table(shape: Any, faces: list[Any]) -> list[tuple[int, Any, list[int]]]:
    """(index, edge, positions of the faces it bounds) for edges that are not seams of one of their faces."""
    all_edges = list(shape.Edges)
    table = edge_face_table(all_edges, faces)
    out = []
    for i, e in enumerate(all_edges):
        if is_model_edge(e, [faces[j] for j in table[i]]):
            out.append((i, e, table[i]))
    return out


def _visible_edges(shape: Any) -> list[tuple[int, Any, list[Any]]]:
    """(index, edge, ancestor faces) for edges that are not seams of one of their faces."""
    faces = list(shape.Faces)
    return [(i, e, [faces[j] for j in around]) for i, e, around in _visible_edge_table(shape, faces)]


def _locate(index: dict[int, list[int]], shape: Any, kind: str, wanted: Any) -> int | None:
    """Position of ``wanted`` (a sub-shape of ``shape``) given ``hash_index`` of the sub-shapes of that ``kind``
    ("Face", "Edge", "Vertex") of which the list is no longer held: the one entry of its bucket, or, when two
    sub-shapes share a hash, the one that is the same shape (taken out of ``shape`` by name, which is slow on a big
    shape: rare)."""
    bucket = index.get(wanted.hashCode())
    if not bucket:
        return None
    if len(bucket) == 1:  # ``wanted`` is a sub-shape of ``shape``, so its own position is in the bucket
        return bucket[0]
    for i in bucket:
        if shape.getElement(f"{kind}{i + 1}").isSame(wanted):
            return i
    return None


class _PointGrid:
    """Points by position, to find the one at (nearly) a given place without walking them all: a grid of ``CELL`` mm
    cells, looking in the neighbour cells only when the point lies within the tolerance of a cell border."""

    CELL = 1e-3

    def __init__(self) -> None:
        self._cells: dict[tuple[int, int, int], list[tuple[int, g.Vec3]]] = {}

    def _key(self, point: g.Vec3) -> tuple[int, int, int]:
        return (math.floor(point[0] / self.CELL), math.floor(point[1] / self.CELL), math.floor(point[2] / self.CELL))

    def add(self, index: int, point: g.Vec3) -> None:
        self._cells.setdefault(self._key(point), []).append((index, point))

    def find(self, point: g.Vec3, tolerance: float = 1e-6) -> int | None:
        axes = []
        for value in point:
            cell = math.floor(value / self.CELL)
            offset = value / self.CELL - cell
            near = tolerance / self.CELL
            axes.append([cell] + ([cell - 1] if offset < near else []) + ([cell + 1] if offset > 1 - near else []))
        for x in axes[0]:
            for y in axes[1]:
                for z in axes[2]:
                    for index, other in self._cells.get((x, y, z), ()):
                        if g.dist(other, point) <= tolerance:
                            return index
        return None


def reserve(doc: DocumentRec, shape: Any, faces_only: bool = False) -> Pristine:
    """Take the ids of the faces, edges and vertices of ``shape`` (one range of each kind) without making a record."""
    n_faces = shape.countElement("Face")
    n_edges, n_vertices = (0, 0) if faces_only else (shape.countElement("Edge"), shape.countElement("Vertex"))
    starts = {}
    for kind, count in (("vertex", n_vertices), ("face", n_faces), ("edge", n_edges)):
        starts[kind] = doc.counters.get(kind, 0) + 1
        doc.counters[kind] = starts[kind] - 1 + count
    return Pristine(starts["face"], n_faces, starts["edge"], n_edges, starts["vertex"], n_vertices, faces_only)


def build_pristine(body: BodyRec) -> None:
    """Make the records a lazy body stands for, from its shape and its id ranges (``Pristine``). A shape that no longer
    has the counts of the ranges (the file changed under a saved session) gets new ranges: its old ids mean nothing."""
    doc, spec = body.doc, body.pristine
    body._lazy = False
    if doc is None or spec is None:
        return
    shape = topo_shape(doc, body)
    if shape.isNull():
        return
    counts = (shape.countElement("Face"), 0 if spec.faces_only else shape.countElement("Edge"))
    if counts != (spec.n_faces, spec.n_edges) or (
        not spec.faces_only and shape.countElement("Vertex") != spec.n_vertices
    ):
        spec = body.pristine = reserve(doc, shape, spec.faces_only)
    _build_records(body, shape, spec)


def _leave_pristine(doc: DocumentRec, body: BodyRec) -> None:
    """The body is about to change: the feature that adopted it lists its faces and edges first (they are the ones it
    created, whatever happens to them next), and from now on the body's records are saved as they are."""
    if body.pristine is None:
        return
    for feature in doc.features:
        if body.id in feature.lazy_bodies:
            feature.resolve()
    body._ensure()
    body.pristine = None


def _build_records(body: BodyRec, shape: Any, spec: Pristine) -> None:
    """The records of an untouched body: faces (``face:{face_start + index}``), edges that are not seams or
    degenerated (``edge:{edge_start + index}``), vertices; the faces each edge bounds; the edges each vertex ends. Every
    record takes its sub-shape lazily and the lists of sub-shapes are held one kind at a time and as short as
    possible: a wrapper of a sub-shape costs about 6 KB and a big shape (thousands of loose faces) has hundreds of
    thousands of edges and vertices. Sub-shapes are found by hash, never by walking a list."""
    created_by = body.created_by
    faces_map, edges_map, vertices_map = body._faces, body._edges, body._vertices
    if spec.faces_only:
        for i, face in enumerate(shape.Faces):
            rec = FaceRec(f"face:{spec.face_start + i}", body.id, i, None, prov.face_sig(face), created_by, shape)
            faces_map[rec.id] = rec
        return
    vertex_recs: list[VertexRec] = []
    grid = _PointGrid()
    for i, vertex in enumerate(shape.Vertexes):
        rec_v = VertexRec(f"vertex:{spec.vertex_start + i}", body.id, i, g.vec(vertex.Point))
        vertex_recs.append(rec_v)
        grid.add(i, rec_v.point)
    faces = list(shape.Faces)
    face_ids: list[str] = []
    for i, face in enumerate(faces):
        rec = FaceRec(f"face:{spec.face_start + i}", body.id, i, None, prov.face_sig(face), created_by, shape)
        faces_map[rec.id] = rec
        face_ids.append(rec.id)
    edges = list(shape.Edges)
    edge_hashes = hash_index(edges)
    around: list[list[int]] = [[] for _ in edges]
    for j, face in enumerate(faces):
        for edge in face.Edges:
            position = _locate(edge_hashes, shape, "Edge", edge)
            if position is not None:
                around[position].append(j)
    del edge_hashes
    for i, edge in enumerate(edges):
        if not is_model_edge(edge, [faces[j] for j in around[i]]):
            continue
        rec = EdgeRec(f"edge:{spec.edge_start + i}", body.id, i, None, prov.edge_sig(edge), created_by, shape)
        rec.faces = tuple(face_ids[j] for j in around[i])
        edges_map[rec.id] = rec
        for end in prov.edge_ends(edge):
            position = grid.find(end)
            if position is not None and rec.id not in vertex_recs[position].edges:
                vertex_recs[position].edges.append(rec.id)
    for rec_v in vertex_recs:
        vertices_map[rec_v.id] = rec_v


def _moved_plane(rec: FaceRec, faces: list[Any], sigs: list[Any], claimed: set[int]) -> int | None:
    """The unclaimed planar face with the record's normal that stands where the record's face stood once slid along
    that normal: the nearest centre in the plane of the face, within half the size of the old face."""
    old = rec.sig
    if old.surface != "plane" or old.direction is None:
        return None
    reach = 0.5 * math.sqrt(max(float(getattr(rec.shape, "Area", 0.0) or 0.0), 1e-9))
    best: tuple[float, int] | None = None
    for i, sig in enumerate(sigs):
        if i in claimed or sig.surface != "plane" or sig.direction is None:
            continue
        if g.dot(old.direction, sig.direction) <= prov.PLANE_DOT:
            continue
        offset = g.sub(sig.center, old.center)
        along = g.scale(old.direction, g.dot(offset, old.direction))
        aside = g.length(g.sub(offset, along))
        if aside <= max(reach, 1e-6) and (best is None or aside < best[0]):
            best = (aside, i)
    return best[1] if best is not None else None


def refresh(
    doc: DocumentRec,
    body: BodyRec,
    new_shape: Any,
    created_by: str,
    mark: bool = True,
    lazy: bool = False,
    follow: frozenset[str] = frozenset(),
) -> Diff:
    """Re-resolve every record of ``body`` against ``new_shape``.

    ``mark=False`` (after undo) re-resolves without attributing modifications to ``created_by``. ``lazy`` makes the
    NEW records take their sub-shape out of ``new_shape`` only when asked (``_SubShape``). ``follow`` names planar
    faces that keep their record when an edit moves them along their normal (the face a sketch sits on)."""
    _leave_pristine(doc, body)
    body.invalidate()
    body._topo = new_shape
    diff = Diff()
    faces = list(new_shape.Faces) if not new_shape.isNull() else []
    edges = _visible_edge_table(new_shape, faces) if not new_shape.isNull() else []
    vertices = list(new_shape.Vertexes) if not new_shape.isNull() else []

    # faces
    face_sigs = [prov.face_sig(f) for f in faces]
    claimed: set[int] = set()
    index_of: dict[str, int] = {}
    moved: list[FaceRec] = []
    for rec in sorted((r for r in body.faces.values() if not r.stale), key=_id_order):
        found = None
        for i, f in enumerate(faces):
            # ``isPartner`` alone is not identity: the two caps of a prism (and the instances of a pattern)
            # share one TShape under different Locations, so a rebuilt bottom face would otherwise claim
            # the untouched top face (verified on a blind hole in a -Z face). The partner
            # must also sit on the record's own surface.
            if i not in claimed and _is_partner(f, rec.shape) and prov.sig_match(rec.sig, face_sigs[i]):
                found = i
                break
        if found is not None:
            claimed.add(found)
            rec.index = found
            rec.shape = faces[found]
            rec.sig = face_sigs[found]
            index_of[rec.id] = found
            continue
        candidates = [i for i in range(len(faces)) if i not in claimed and prov.sig_match(rec.sig, face_sigs[i])]
        if candidates:
            centre = rec.sig.center
            best = min(candidates, key=lambda i: g.dist(face_sigs[i].center, centre))
            claimed.add(best)
            rec.index = best
            rec.shape = faces[best]
            rec.sig = face_sigs[best]
            if mark and created_by not in rec.modified_by:
                rec.modified_by.append(created_by)
            diff.modified_faces.append(rec.id)
            index_of[rec.id] = best
            continue
        if rec.id in follow:
            moved.append(rec)
            continue
        rec.stale = True
        diff.removed_faces.append(rec.id)
    for rec in moved:  # a planar face an edit moved along its normal keeps its record (a sketch sits on it)
        best = _moved_plane(rec, faces, face_sigs, claimed)
        if best is None:
            rec.stale = True
            diff.removed_faces.append(rec.id)
            continue
        claimed.add(best)
        rec.index = best
        rec.shape = faces[best]
        rec.sig = face_sigs[best]
        if mark and created_by not in rec.modified_by:
            rec.modified_by.append(created_by)
        diff.modified_faces.append(rec.id)
        index_of[rec.id] = best
    for i, f in enumerate(faces):
        if i in claimed:
            continue
        rec = FaceRec(doc.next_id("face"), body.id, i, None if lazy else f, face_sigs[i], created_by, new_shape)
        body.faces[rec.id] = rec
        diff.created_faces.append(rec.id)
        index_of[rec.id] = i
    face_id_by_index = {i: fid for fid, i in index_of.items()}

    # edges
    edge_sigs = {i: prov.edge_sig(e) for i, e, _ in edges}
    claimed_e: set[int] = set()
    edge_at = {i: e for i, e, _ in edges}
    for rec in sorted((r for r in body.edges.values() if not r.stale), key=_id_order):
        found = None
        for i, e, _ in edges:
            if i not in claimed_e and _is_partner(e, rec.shape) and prov.edge_sig_match(rec.sig, edge_sigs[i]):
                found = i
                break
        if found is None:
            candidates = [i for i, _, _ in edges if i not in claimed_e and prov.edge_sig_match(rec.sig, edge_sigs[i])]
            if candidates:
                found = candidates[0]
        if found is None:
            rec.stale = True
            diff.removed_edges.append(rec.id)
            continue
        claimed_e.add(found)
        rec.index = found
        rec.shape = edge_at[found]
        rec.sig = edge_sigs[found]
    for i, e, _ in edges:
        if i in claimed_e:
            continue
        rec = EdgeRec(doc.next_id("edge"), body.id, i, None if lazy else e, edge_sigs[i], created_by, new_shape)
        body.edges[rec.id] = rec
        diff.created_edges.append(rec.id)
    edge_by_index = {r.index: r for r in body.edges.values() if not r.stale}
    for i, _, around in edges:
        edge_by_index[i].faces = tuple(face_id_by_index[j] for j in around if j in face_id_by_index)

    # vertices
    claimed_v: set[int] = set()
    points = [g.vec(v.Point) for v in vertices]
    for rec in sorted((r for r in body.vertices.values() if not r.stale), key=_id_order):
        found = None
        for i, p in enumerate(points):
            if i not in claimed_v and g.dist(p, rec.point) <= 1e-6:
                found = i
                break
        if found is None:
            rec.stale = True
            continue
        claimed_v.add(found)
        rec.index = found
        rec.point = points[found]
    for i, p in enumerate(points):
        if i in claimed_v:
            continue
        rec = VertexRec(doc.next_id("vertex"), body.id, i, p)
        body.vertices[rec.id] = rec
    vertex_by_index = {r.index: r for r in body.vertices.values() if not r.stale}
    for r in vertex_by_index.values():
        r.edges = []
    vertex_hashes = hash_index(vertices)
    for i, e, _ in edges:
        erec = edge_by_index[i]
        for v in e.Vertexes:
            position = find_shape(vertex_hashes, vertices, v)
            vrec = vertex_by_index.get(position) if position is not None else None
            if vrec is not None and erec.id not in vrec.edges:
                vrec.edges.append(erec.id)
    return diff


def refresh_by_index(doc: DocumentRec, body: BodyRec, new_shape: Any, created_by: str) -> Diff:
    """After a rigid move: same element order, every face modified."""
    _leave_pristine(doc, body)
    body.invalidate()
    body._topo = new_shape
    diff = Diff()
    faces = list(new_shape.Faces)
    edge_shapes = list(new_shape.Edges) if body.edges else []
    for rec in body.faces.values():
        if rec.stale or rec.index >= len(faces):
            continue
        rec.shape = faces[rec.index]
        rec.sig = prov.face_sig(rec.shape)
        if created_by not in rec.modified_by:
            rec.modified_by.append(created_by)
        diff.modified_faces.append(rec.id)
    for rec in body.edges.values():
        if rec.stale:
            continue
        rec.shape = edge_shapes[rec.index]
        rec.sig = prov.edge_sig(rec.shape)
    points = [g.vec(v.Point) for v in new_shape.Vertexes]
    for rec in body.vertices.values():
        if not rec.stale and rec.index < len(points):
            rec.point = points[rec.index]
    return diff


def _id_order(rec: Any) -> int:
    return int(rec.id.rsplit(":", 1)[1])


# --- resolution -------------------------------------------------------------------------------------------------


def split_component(entity_id: str) -> tuple[str, str] | None:
    if COMPONENT_SEPARATOR not in entity_id:
        return None
    comp_id, local = entity_id.split(COMPONENT_SEPARATOR, 1)
    return comp_id, local


def prefixed(component_id: str, local_id: str) -> str:
    return f"{component_id}{COMPONENT_SEPARATOR}{local_id}"


def resolve_face(doc: DocumentRec, face_id: str, operation_id: str | None) -> tuple[FaceRec, ComponentRec | None]:
    split = split_component(face_id)
    if split is None:
        for body in doc.live_bodies():
            rec = body.find("faces", face_id)
            if rec is not None and not rec.stale:
                return rec, None
        raise invalid_reference(face_id, operation_id)
    comp = resolve_component(doc, split[0], operation_id)
    rec = comp.find("faces", split[1])
    if rec is None or rec.stale:
        raise invalid_reference(face_id, operation_id)
    return rec, comp


def resolve_edge(doc: DocumentRec, edge_id: str, operation_id: str | None) -> tuple[EdgeRec, ComponentRec | None]:
    split = split_component(edge_id)
    if split is None:
        for body in doc.live_bodies():
            rec = body.find("edges", edge_id)
            if rec is not None and not rec.stale:
                return rec, None
        raise invalid_reference(edge_id, operation_id)
    comp = resolve_component(doc, split[0], operation_id)
    rec = comp.find("edges", split[1])
    if rec is None or rec.stale:
        raise invalid_reference(edge_id, operation_id)
    return rec, comp


def resolve_vertex(doc: DocumentRec, vertex_id: str, operation_id: str | None) -> tuple[VertexRec, ComponentRec | None]:
    split = split_component(vertex_id)
    if split is None:
        for body in doc.live_bodies():
            rec = body.find("vertices", vertex_id)
            if rec is not None and not rec.stale:
                return rec, None
        raise invalid_reference(vertex_id, operation_id)
    comp = resolve_component(doc, split[0], operation_id)
    rec = comp.find("vertices", split[1])
    if rec is None or rec.stale:
        raise invalid_reference(vertex_id, operation_id)
    return rec, comp


def resolve_body(doc: DocumentRec, body_id: str, operation_id: str | None) -> BodyRec:
    body = doc.bodies.get(body_id)
    if body is None or body.deleted:
        raise invalid_reference(body_id, operation_id)
    return body


def resolve_component(doc: DocumentRec, comp_id: str, operation_id: str | None) -> ComponentRec:
    comp = doc.components.get(comp_id)
    if comp is None:
        raise invalid_reference(comp_id, operation_id)
    return comp


def resolve_joint(doc: DocumentRec, joint_id: str, operation_id: str | None) -> JointRec:
    joint = doc.joints.get(joint_id)
    if joint is None:
        raise invalid_reference(joint_id, operation_id)
    return joint


def joint_driving(doc: DocumentRec, comp: ComponentRec) -> JointRec | None:
    """The joint that carries ``comp`` (its ``b``) or that it is the reference of (its ``a``): such a component is
    placed by the joint, not by hand."""
    return next((j for j in doc.joints.values() if comp.id in (j.b, j.a)), None)


def resolve_sketch(doc: DocumentRec, sketch_id: str, operation_id: str | None) -> SketchRec:
    sketch = doc.sketches.get(sketch_id)
    if sketch is None:
        raise invalid_reference(sketch_id, operation_id)
    return sketch


def resolve_datum_plane(doc: DocumentRec, datum_id: str, operation_id: str | None) -> DatumRec:
    datum = doc.datums.get(datum_id)
    if datum is None or datum.kind != "plane":
        raise invalid_reference(datum_id, operation_id)
    return datum


def handle_id(value: Any, operation_id: str | None, field: str) -> str:
    """``{"id": "face:3"}`` -> ``"face:3"``; anything else is an ``invalid_request``."""
    if isinstance(value, dict) and isinstance(value.get("id"), str):
        return value["id"]
    if isinstance(value, str):
        return value
    raise WorkerError("invalid_request", f"field {field} is not a handle", operation_id)
