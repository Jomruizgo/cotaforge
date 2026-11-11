# ruff: noqa: E501
"""What a sheet item needs to resolve itself against the part, as plain data (no FreeCAD import): the views and where
the model sits in them, the holes, a way to look up faces and edges, and the places already taken on the sheet.

The worker (``drawing.py``) fills a ``Context`` from the live document; the unit tests fill one by hand. Items
(``items_*.py``) read it and add entities to ``Context.sheet``; none of them imports FreeCAD.

The 3D -> view mapping, verified against TechDraw 1.1.3: for a view with ``Direction`` d and ``XDirection`` x, a model
point p lands at ``x_local = (p - C) . x`` and ``y_local = (p - C) . (d x x)`` (both times the view scale), C being the
model point at the view's centre (``getGeometricCenter``); the view is placed on the page at (X, Y).
"""

import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sheet_collide import ink_of, text_box
from sheet_model import Entity, Line, Sheet, Text, fmt
from sheet_viewshape import dimension_shape

Vec3 = tuple[float, float, float]
Point = tuple[float, float]
Rect = tuple[float, float, float, float]  # x0, y0, x1, y1 (page mm, y up)

POSITION_TOL_MM = 0.05  # two positions are the same place
ANGLE_TOL = 1e-3  # two unit vectors are parallel when 1 - |a . b| is below this squared-ish
DEFAULT_OFFSET_MM = 10.0  # first dimension line from the edge of a view
STACK_STEP_MM = 7.0  # next ones, farther out
AREA_SLACK_MM = 2.0  # a label may go this far past the drawing area on the sides and the top (the frame is farther)

VIEW_NAMES = ("front", "top", "right")
SECTION_LABEL = re.compile(r"^([A-Z])-\1$")  # the label of a section view: the same capital letter twice


class ItemError(ValueError):
    """An item the sheet cannot take; the message tells the agent what to change."""


# --- vectors -------------------------------------------------------------------------------------------------------


def sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def scale(a: Vec3, s: float) -> Vec3:
    return (a[0] * s, a[1] * s, a[2] * s)


def dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def norm(a: Vec3) -> float:
    return math.sqrt(dot(a, a))


def unit(a: Vec3) -> Vec3:
    n = norm(a)
    if n < 1e-12:
        msg = "a zero vector has no direction"
        raise ItemError(msg)
    return (a[0] / n, a[1] / n, a[2] / n)


def parallel(a: Vec3, b: Vec3, tol: float = 1e-6) -> bool:
    """Whether two vectors lie on one line (either sense)."""
    return norm(cross(unit(a), unit(b))) < tol


def perpendicular(a: Vec3, b: Vec3, tol: float = 1e-6) -> bool:
    return abs(dot(unit(a), unit(b))) < tol


MODEL_AXES: dict[str, Vec3] = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}


def model_axis(v: Vec3) -> str | None:
    """The model axis (``x``, ``y`` or ``z``) a vector lies along, ``None`` for any other direction."""
    return next((name for name, axis in MODEL_AXES.items() if parallel(axis, v)), None)


# --- views and the model in them --------------------------------------------------------------------------------------


@dataclass
class ViewInfo:
    """One projected view: where its origin is on the page (``x``, ``y``, mm, y up), its scale and the model point
    ``centre`` that sits at that origin; ``box`` is the page rectangle of the geometry TechDraw drew in it."""

    name: str
    x: float
    y: float
    scale: float
    direction: Vec3
    xdir: Vec3
    centre: Vec3
    box: Rect
    ydir: Vec3 = field(init=False)
    vertices: tuple[
        Point, ...
    ] = ()  # the vertices TechDraw's own view has (page mm): real points a dimension may start at
    derived: bool = False  # a view an item drew (a section), made of sheet entities: no TechDraw view object behind it

    def __post_init__(self) -> None:
        self.direction = unit(self.direction)
        self.xdir = unit(self.xdir)
        self.ydir = unit(cross(self.direction, self.xdir))

    def project(self, p: Vec3) -> Point:
        """The page position of the model point ``p`` in this view."""
        d = sub(p, self.centre)
        return (self.x + dot(d, self.xdir) * self.scale, self.y + dot(d, self.ydir) * self.scale)

    def axis(self, v: Vec3) -> Point:
        """The (unscaled) direction a model vector takes on the page; (0, 0) when it points at the viewer."""
        return (dot(v, self.xdir), dot(v, self.ydir))

    def shows_axis_along(self, v: Vec3) -> bool:
        """Whether a model direction lies in the view plane (so a length along it can be dimensioned here)."""
        return perpendicular(self.direction, v, 1e-6)

    def looks_along(self, v: Vec3) -> bool:
        """Whether the view is looking along ``v`` (a hole with that axis shows as a circle)."""
        return parallel(self.direction, v, 1e-6)


@dataclass
class HoleInfo:
    """A cylindrical hole of the part. ``centre`` is the axis point where the hole opens (any point of the axis
    identifies it); ``thread`` is ``(size, depth_mm or None)`` when a ``hole`` feature declared one and
    ``declared`` says a ``hole`` feature made this hole (so its thread is known: ``None`` means plain, not unknown)."""

    index: int
    centre: Vec3
    direction: Vec3
    diameter: float
    depth: float
    through: bool | None
    faces: tuple[str, ...] = ()
    thread: tuple[str, float | None] | None = None
    declared: bool = False
    feature: str | None = None  # the feature that made the hole's wall (a review names it)
    fit: str | None = None  # the tolerance class the ``hole`` operation declared (``H7``): its ``hole_dim`` default
    hole_class: str | None = None  # precision | plain | threaded, as the convention classed it when the hole was made
    precision_grade: int | None = None  # the IT grade that convention called precision

    def on_axis(self, p: Vec3, tol: float = POSITION_TOL_MM) -> bool:
        """Whether ``p`` lies on the axis of the hole (within ``tol`` mm) -- how an ``at`` finds its hole."""
        d = sub(p, self.centre)
        along = dot(d, unit(self.direction))
        radial = norm(sub(d, scale(unit(self.direction), along)))
        return radial <= tol


def hole_stacks(holes: list[HoleInfo]) -> list[list[HoleInfo]]:
    """Holes on one axis (a counterbore is two holes there), each stack ordered by diameter."""
    stacks: list[list[HoleInfo]] = []
    for hole in sorted(holes, key=lambda h: h.index):
        for stack in stacks:
            if parallel(stack[0].direction, hole.direction) and stack[0].on_axis(hole.centre):
                stack.append(hole)
                break
        else:
            stacks.append([hole])
    return [sorted(s, key=lambda h: h.diameter) for s in stacks]


@dataclass
class FaceInfo:
    """A face by what a dimension needs: ``kind`` (plane / cylinder / other), a point on it, its outward normal
    (planes), the radius, axis and a point on the surface (cylinders)."""

    id: str
    kind: str
    point: Vec3
    normal: Vec3 | None = None
    radius: float | None = None
    axis_point: Vec3 | None = None
    axis_dir: Vec3 | None = None
    surface_point: Vec3 | None = None


@dataclass
class EdgeInfo:
    """An edge by what a dimension needs: ``line`` (a straight edge: its start, end), ``circle`` (a circular edge: its
    centre, radius, axis direction)."""

    id: str
    kind: str
    start: Vec3 | None = None
    end: Vec3 | None = None
    centre: Vec3 | None = None
    radius: float | None = None
    axis_dir: Vec3 | None = None


@dataclass
class ChamferInfo:
    """The legs of a flat chamfer, measured on the two faces it joins, and its direction (the edge it removed)."""

    face: str
    leg_a: float
    leg_b: float
    angle_deg: float  # between the chamfer face and the face of ``leg_a``
    edge_dir: Vec3
    middle: Vec3  # a model point in the middle of the chamfer face


@dataclass
class Projection:
    """A part seen from one direction, in the view's own coordinates (scale 1, y up): its ``visible`` and ``hidden``
    edges, and for a section the ``cut_loops`` (the outlines of the faces the plane cut, holes included)."""

    visible: list[Entity]
    hidden: list[Entity] = field(default_factory=list)
    cut_loops: list[list[Point]] = field(default_factory=list)
    cut_owner: list[int] = field(default_factory=list)  # for each loop, the index of the body (shape) whose face it bounds


class Geometry:
    """The lookups an item makes into the model. The worker implements them on the live document; tests subclass."""

    def face(self, face_id: str) -> FaceInfo:
        msg = f"unknown face {face_id!r}"
        raise ItemError(msg)

    def edge(self, edge_id: str) -> EdgeInfo:
        msg = f"unknown edge {edge_id!r}"
        raise ItemError(msg)

    def on_part(self, p: Vec3) -> Vec3 | None:
        """``p`` snapped onto the part's boundary (a vertex, edge or face within tolerance), or ``None`` when it is
        not on the part."""
        return None

    def chamfer(self, face_id: str) -> ChamferInfo:
        msg = "chamfer legs are not available"
        raise ItemError(msg)

    def round_extent(self) -> tuple[Vec3, float] | None:
        """``(axis, diameter)`` when the part is a body of revolution about a coordinate axis whose overall size across
        it is the diameter of one of its outer cylinders (a flange, a shaft); ``None`` otherwise."""
        return None

    def symmetry(self) -> dict[str, float]:
        """The model axes (``x``, ``y``, ``z``) the part is mirror-symmetric across, each with the position of its plane
        of symmetry along that axis (the middle of the part's box): measured on the shape, never assumed. A part whose
        symmetry cannot be told (too complex to test, an assembly) says none."""
        return {}

    def cylinder_span(self, face_id: str, across: Vec3) -> tuple[float, float] | None:
        """Where, along the axis of the cylindrical face ``face_id`` (measured from its ``axis_point``), the surface
        really exists on the line of its side that points along ``across`` (a flat or a groove may have taken the rest):
        ``(low, high)`` of the longest stretch, ``None`` when it cannot be told."""
        return None

    # --- derived views (P4: section, detail, auxiliary, hidden lines) ---
    def projection(
        self, centre: Vec3, xdir: Vec3, direction: Vec3, cut: tuple[Vec3, Vec3] | None = None
    ) -> "Projection":
        """The part looked at from ``direction`` (towards the viewer), x axis ``xdir``, origin at ``centre``, scale 1;
        with ``cut`` = (point, normal) cut by that plane first (the viewer's side removed)."""
        msg = "projections are not available"
        raise ItemError(msg)

    def view_projection(self, centre: Vec3, xdir: Vec3, direction: Vec3) -> "Projection":
        """The visible edges of the whole part for a standard view of a sheet made with ``projection="hlr"`` (kept
        between calls where that is costly); by default the same as ``projection`` without a cut."""
        return self.projection(centre, xdir, direction)

    def face_outline(self, face_id: str, centre: Vec3, xdir: Vec3, direction: Vec3) -> list[list[Point]]:
        """The loops of a face in a view's own coordinates (same frame as ``projection``)."""
        msg = "face outlines are not available"
        raise ItemError(msg)

    def faces_at(self, p: Vec3, face_ids: list[str]) -> list[str]:
        """Those of ``face_ids`` that pass through the point ``p`` (within the position tolerance)."""
        return []

    # --- assemblies (P3: parts list, balloons, exploded view) ---
    def instance_box(self, comp_id: str) -> tuple[Vec3, Vec3]:
        """The (quick, never smaller than the part) box of one component instance in the assembly's axes."""
        msg = "component boxes are not available"
        raise ItemError(msg)

    def instance_anchors(
        self,
        instances: list[str],
        frame: tuple[Vec3, Vec3, Vec3],
        offsets: dict[str, Vec3],
        margin_mm: float,
        outward: bool = False,
    ) -> dict[str, list[Vec3]]:
        """For each instance, points of a face it shows to a viewer looking against ``frame`` = (centre, xdir,
        direction): the best first (the one with ``margin_mm`` of the same instance all round it), none when the
        instance is hidden behind others. ``offsets`` displaces instances (an exploded view); the points returned are
        where the instance IS drawn, offset included."""
        msg = "instance visibility is not available"
        raise ItemError(msg)

    def assembly_projection(self, centre: Vec3, xdir: Vec3, direction: Vec3, offsets: dict[str, Vec3]) -> "Projection":
        """The whole assembly (components displaced by ``offsets``) looked at like ``projection``, visible edges."""
        msg = "assembly projections are not available"
        raise ItemError(msg)


@dataclass
class PartGroup:
    """One line of the parts list: the instances of one part (the same part file), its position and the texts it shows
    (``name``, ``reference``, ``material``, ``remarks``; the engineer's overrides are applied by the item)."""

    position: int
    key: str
    name: str
    reference: str
    material: str
    remarks: str
    instances: list[str]
    bought: bool = False

    @property
    def quantity(self) -> int:
        return len(self.instances)


@dataclass
class Assembly:
    """The components of an assembly document grouped by part, in the order of their first instance."""

    groups: list[PartGroup] = field(default_factory=list)

    def position_of(self, comp_id: str) -> int | None:
        for group in self.groups:
            if comp_id in group.instances:
                return group.position
        return None

    def instances(self) -> list[str]:
        return [i for group in self.groups for i in group.instances]


@dataclass
class ItemResult:
    """What one item did: ``status`` is ``ok``, ``warning`` (drawn, with something to know) or ``error`` (not drawn)."""

    id: str
    type: str
    status: str = "ok"
    detail: str = ""


@dataclass
class Context:
    sheet: Sheet
    views: dict[str, ViewInfo]
    holes: list[HoleInfo]
    geometry: Geometry
    area: Rect  # the free drawing area (page mm, y up) the views and their dimensions sit in
    language: str = "es"
    revisions: list[dict[str, str]] = field(default_factory=list)
    occupied: list[Rect] = field(default_factory=list)
    stacks: dict[tuple[str, str], int] = field(default_factory=dict)
    notes: int = 0
    table_bottom: float = 0.0
    warnings: list[str] = field(default_factory=list)
    extras: dict[str, Any] = field(default_factory=dict)
    assembly: Assembly | None = None

    @property
    def comma(self) -> bool:
        return self.language == "es"

    def any_view(self, name: str) -> ViewInfo:
        """A view of the sheet by name: front / top / right, the TechDraw ``isometric`` one, or the ``exploded`` view an
        item drew (``extras["views_extra"]``)."""
        found = self.views.get(name) or self.extras.get("views_extra", {}).get(name)
        if found is None:
            known = sorted({*self.views, *self.extras.get("views_extra", {})})
            msg = f"the sheet has no {name!r} view (it has {', '.join(known) or 'none'})"
            raise ItemError(msg)
        return found

    def num(self, value: float) -> str:
        return fmt(value, self.comma)

    def say(self, es: str, en: str) -> str:
        return es if self.language == "es" else en

    def find_view(self, name: str) -> ViewInfo | None:
        """A standard view (front / top / right) or a section view an item drew (its label, ``A-A``), else ``None``."""
        return self.views.get(name) or self.extras.get("sections", {}).get(name)

    def view(self, name: str) -> ViewInfo:
        found = self.find_view(name)
        if found is None:
            known = sorted({*self.views, *self.extras.get("sections", {})})
            msg = f"the sheet has no {name!r} view (it has {', '.join(known)})"
            if SECTION_LABEL.match(name):
                msg += ": a section view exists once its section item is on the sheet"
            raise ItemError(msg)
        return found

    def nudge(self) -> int:
        """How many times the placement pass has moved the item being drawn away from a collision (``drawing.compose``
        draws the sheet again with the item nudged farther each time): 0 on the first pass."""
        return int(self.extras.get("nudges", {}).get(self.extras.get("current"), 0))

    def stack_distance(self, view: str, side: str, offset_mm: float | None) -> float:
        """Distance of the next dimension line from the edge of ``view`` on ``side``: ``offset_mm`` when the item asked
        for one, else the first free slot (lines stack outward in the order the items are drawn; a nudged item skips
        slots)."""
        self.extras["stack_view"] = view  # the dimension the item draws next stands on this view
        slot = self.stacks.get((view, side), 0)
        self.stacks[(view, side)] = slot + 1
        if offset_mm is not None:
            return offset_mm
        return DEFAULT_OFFSET_MM + STACK_STEP_MM * (slot + self.nudge())

    def reserve_dimension(self, entities: list[Entity], strip: Rect) -> None:
        """Reserve what a dimension takes: ``strip`` (its line, arrows and text, where no label may go) and, apart, the
        boxes of its texts (a leader may cross the line of a dimension when it must, never its text)."""
        self.occupied.append(strip)
        self.extras.setdefault("strips", []).append(strip)
        self.extras.setdefault("texts", []).extend(text_box(e) for e in entities if isinstance(e, Text))
        # the lines of the dimension (its line and extension lines): a leader that must cross one is allowed, but a
        # leader that can go around them is better (``items_common.place_callout`` scores the crossings)
        self.extras.setdefault("dimension_lines", []).extend(
            (e.x1, e.y1, e.x2, e.y2) for e in entities if isinstance(e, Line)
        )
        self.extras.setdefault("dimension_main_lines", []).extend(
            ink_of("", entities).dimension_lines
        )  # the ones with arrows
        shape = dimension_shape(
            str(self.extras.get("current") or ""),
            str(self.extras.get("stack_view") or ""),
            entities,
            self._arrows_asked(),
        )
        if shape is not None:
            self.extras.setdefault("dim_shapes", []).append(shape)

    def _arrows_asked(self) -> str:
        """What the item being drawn asked for the arrows (``auto`` when it did not say)."""
        item = self.extras.get("current")
        for spec in self.extras.get("items", []):
            if spec.get("id") == item:
                return str(spec.get("arrows", "auto"))
        return "auto"

    @property
    def third_angle(self) -> bool:
        """Whether the sheet is in third-angle projection (the top view above the front one, the right view at its right)."""
        return self.extras.get("angle") == "third"

    def inside(self, rect: Rect) -> bool:
        """Whether ``rect`` lies in the drawing area: inside the frame (a little in from it) and above the title block."""
        left, low, right, top = self.area
        return (
            rect[0] >= left - AREA_SLACK_MM
            and rect[2] <= right + AREA_SLACK_MM
            and rect[1] >= low
            and rect[3] <= top + AREA_SLACK_MM
        )

    def note(self, message: str) -> None:
        """Tell the agent something about the item being drawn (it becomes a warning in the item's result)."""
        item = self.extras.get("current")
        if item is not None:
            self.extras.setdefault("placement_notes", {}).setdefault(item, []).append(message)

    def native(self, *objects: Any) -> None:
        """The TechDraw objects (``sheet_units.NativeDim`` / ``NativeNote``) the item being drawn asks for: the entities
        it adds are what the headless sheet writes, these are what the CAD's page holds (and TechDraw exports)."""
        item = self.extras.get("current")
        if item is not None:
            self.extras.setdefault("natives", {}).setdefault(item, []).extend(objects)

    def record_dimension(self, key: tuple[Any, ...], view: str) -> None:
        """Keep that the item being drawn put a dimension on ``view`` that says ``key`` (what it measures, in the model:
        an axis and the two places it spans, or a diameter and its face): the same thing said in two views is a defect
        (ISO 129) the review flags."""
        item = self.extras.get("current")
        self.extras.setdefault("dim_records", []).append((str(item or ""), view, key))

    def used_view(self, name: str) -> None:
        """The item being drawn carries something on view ``name`` (the review drops a view nothing is carried on)."""
        item = self.extras.get("current")
        self.extras.setdefault("view_use", {}).setdefault(name, set()).add(item or "")

    def reserve(self, rect: Rect) -> None:
        self.occupied.append(rect)

    def free(self, rect: Rect) -> bool:
        return not any(_overlap(rect, other) for other in self.occupied)

    def add(self, entities: list[Entity]) -> None:
        self.sheet.extend(entities)


def _overlap(a: Rect, b: Rect) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


@dataclass(frozen=True)
class ItemType:
    """A kind of sheet item: ``check`` reduces an agent's dict to its validated fields (``ItemError`` otherwise),
    ``draw`` resolves it against the geometry and adds entities to ``ctx.sheet``, returning ``(status, detail)``.
    ``prepare`` (optional) sees all the items of the type before any is drawn (counting notes, collecting revisions).
    ``fields`` are the names the item takes besides ``type`` and ``id``; ``prefix`` starts its automatic ids. ``phase``
    orders the drawing, not the list: 0 lines and dimensions (they take their room first), 1 leaders with text (they
    look for room around those), 2 free text."""

    name: str
    prefix: str
    check: Callable[[dict[str, Any]], dict[str, Any]]
    draw: Callable[["Context", dict[str, Any]], tuple[str, str]]
    fields: frozenset[str]
    prepare: Callable[["Context", list[dict[str, Any]]], None] | None = None
    phase: int = 1


# --- field validation -------------------------------------------------------------------------------------------------

Check = Callable[[Any], Any]


def v_str(max_len: int = 120) -> Check:
    def check(value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            msg = "must be a non-empty text"
            raise ItemError(msg)
        if len(value) > max_len:
            msg = f"must be at most {max_len} characters"
            raise ItemError(msg)
        return value.strip()

    return check


def v_num(low: float | None = None, high: float | None = None) -> Check:
    def check(value: Any) -> float:
        if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
            msg = "must be a number"
            raise ItemError(msg)
        if (low is not None and value < low) or (high is not None and value > high):
            msg = f"must be between {low} and {high}"
            raise ItemError(msg)
        return float(value)

    return check


def v_bool(value: Any) -> bool:
    if not isinstance(value, bool):
        msg = "must be true or false"
        raise ItemError(msg)
    return value


def v_enum(*choices: str) -> Check:
    def check(value: Any) -> str:
        if value not in choices:
            msg = f"must be one of {', '.join(choices)}"
            raise ItemError(msg)
        return str(value)

    return check


def v_point3(value: Any) -> Vec3:
    if (
        not isinstance(value, list | tuple)
        or len(value) != 3
        or any(isinstance(c, bool) or not isinstance(c, int | float) or not math.isfinite(c) for c in value)
    ):
        msg = "must be a point [x, y, z] in mm"
        raise ItemError(msg)
    return (float(value[0]), float(value[1]), float(value[2]))


def v_point2(value: Any) -> Point:
    if (
        not isinstance(value, list | tuple)
        or len(value) != 2
        or any(isinstance(c, bool) or not isinstance(c, int | float) or not math.isfinite(c) for c in value)
    ):
        msg = "must be a page position [x, y] in mm"
        raise ItemError(msg)
    return (float(value[0]), float(value[1]))


def v_points3(value: Any) -> list[Vec3]:
    """One point or a list of points."""
    if isinstance(value, list | tuple) and value and all(isinstance(v, list | tuple) for v in value):
        return [v_point3(v) for v in value]
    return [v_point3(value)]


def v_ids(prefix: str) -> Check:
    def check(value: Any) -> list[str]:
        items = value if isinstance(value, list | tuple) else [value]
        if not items or any(not isinstance(i, str) or not i.startswith(f"{prefix}:") for i in items):
            msg = f"must be a {prefix} id like '{prefix}:3' or a list of them"
            raise ItemError(msg)
        return [str(i) for i in items]

    return check


def v_ref(value: Any) -> dict[str, Any]:
    """A reference to a place on the part: ``{"at": [x,y,z]}``, ``{"face": "face:3"}`` or ``{"edge": "edge:5"}``."""
    if not isinstance(value, dict) or len(value) != 1:
        msg = 'must be {"at": [x,y,z]}, {"face": "face:N"} or {"edge": "edge:N"}'
        raise ItemError(msg)
    ((key, inner),) = value.items()
    if key == "at":
        return {"at": list(v_point3(inner))}
    if key in ("face", "edge"):
        return {key: v_ids(key)(inner)[0]}
    msg = f"unknown reference {key!r}: use at, face or edge"
    raise ItemError(msg)


def check_fields(spec: dict[str, Any], checks: dict[str, Check], required: tuple[str, ...] = ()) -> dict[str, Any]:
    """The spec reduced to its known, validated fields. An unknown field, a missing required one or a bad value is an
    ``ItemError`` that names the field."""
    unknown = sorted(set(spec) - set(checks) - {"type", "id"})
    if unknown:
        msg = f"unknown field(s) {', '.join(unknown)}; this item takes {', '.join(sorted(checks))}"
        raise ItemError(msg)
    for name in required:
        if name not in spec:
            msg = f"{name} is required"
            raise ItemError(msg)
    out: dict[str, Any] = {}
    for name, value in spec.items():
        if name in ("type", "id"):
            out[name] = value
            continue
        try:
            out[name] = checks[name](value)
        except ItemError as exc:
            msg = f"{name} {exc}"
            raise ItemError(msg) from exc
    return out


def v_view_or_section(value: Any) -> str:
    """A standard view (front / top / right) or the label of a section view the sheet makes (``A-A``)."""
    if isinstance(value, str) and (value in VIEW_NAMES or SECTION_LABEL.match(value)):
        return value
    msg = f"must be one of {', '.join(VIEW_NAMES)} or the label of a section view (A-A)"
    raise ItemError(msg)


# the fields every drawn item may carry
COMMON_FIELDS: dict[str, Check] = {
    "view": v_enum(*VIEW_NAMES),
    "offset_mm": v_num(0.0, 500.0),
    "side": v_enum("above", "below", "left", "right"),
}
