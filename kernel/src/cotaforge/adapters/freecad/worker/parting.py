"""The parting line of a moulded part, the slides its undercuts need, and the parting surface.

Reads the B-rep; nothing here changes a document (``mould_split.py`` builds the halves from these results).

* every face is graded as ``draft_analysis`` does it (``mould.py``) and given a SIDE from the signed lean of its normal
  against the pull: ``core`` (looks along the pull), ``cavity`` (looks against it), ``zero`` (no lean at all), or
  ``straddle`` (looks both ways: the silhouette runs across it);
* a face the pull cannot release (an undercut) belongs to a SLIDE region, not to either half;
* a group of zero-draft walls whose neighbours are all on one side takes that side; one that touches both sides (the
  outer wall of a shell) needs the engineer's decision (``zero_draft_to``);
* the PARTING LINE is the set of edges between a core face and a cavity face, chained into loops.
"""

import math
from typing import Any

import draft_math as dm
import FreeCAD as App
import geometry as g
import mould as md
import mould_math as mm
import Part

PLANAR_REL_TOL = 1e-6
STEP_TOL_MM = 1e-6
STEP_MAX_DEG = 10.0
SLIVER_AREA_MM2 = (
    2.0  # an undercut group this small, that no direction frees and that the shadow test blocks at few points,
)
SLIVER_SHARE = 0.34  # is a sampling artefact of a tiny face (a ray grazing a neighbour), not an undercut
STEP_SLOPE = math.tan(math.radians(STEP_MAX_DEG))
DIRECTION_TOL = 0.999  # two directions closer than this (cosine) are the same candidate
SLIDE_MAX_ALONG_PULL = 0.7  # a slide that moves mostly along the pull is not a slide


class PartingError(Exception):
    """Something the parting tools cannot build; the message says what and why (a FreeCAD limit, or a gap)."""


# --- topology ---------------------------------------------------------------------------------------------------


def _table(subs: list[Any]) -> dict[int, list[tuple[int, Any]]]:
    out: dict[int, list[tuple[int, Any]]] = {}
    for index, sub in enumerate(subs):
        out.setdefault(sub.hashCode(), []).append((index, sub))
    return out


def _index_of(table: dict[int, list[tuple[int, Any]]], sub: Any) -> int:
    for index, other in table.get(sub.hashCode(), ()):
        if other.isSame(sub):
            return index
    msg = "a sub-shape is not in the shape"
    raise PartingError(msg)


def _nearest_vertex(table: dict[int, list[tuple[int, Any]]], edge: Any, point: Any) -> int:
    best = min(edge.Vertexes, key=lambda v: v.Point.distanceToPoint(point))
    return _index_of(table, best)


class Topology:
    """The faces, edges and vertices of one shape, and who touches whom. An edge's two ends are the points of its first
    and last parameter (``ends``): walking an edge ``forward`` goes from the first to the last."""

    def __init__(self, shape: Any) -> None:
        self.shape = shape
        self.faces = list(shape.Faces)
        self.edges = list(shape.Edges)
        faces, vertices = _table(self.faces), _table(list(shape.Vertexes))
        self.edge_faces: dict[int, tuple[int, ...]] = {}
        self.edge_ends: dict[int, tuple[int, int]] = {}
        self.neighbours: dict[int, set[int]] = {i: set() for i in range(len(self.faces))}
        for ei, edge in enumerate(self.edges):
            owners = sorted({_index_of(faces, f) for f in shape.ancestorsOfType(edge, Part.Face)})
            self.edge_faces[ei] = tuple(owners)
            if len(owners) == 2:
                self.neighbours[owners[0]].add(owners[1])
                self.neighbours[owners[1]].add(owners[0])
            if edge.Vertexes:
                first = edge.valueAt(edge.FirstParameter)
                last = edge.valueAt(edge.LastParameter)
                self.edge_ends[ei] = (_nearest_vertex(vertices, edge, first), _nearest_vertex(vertices, edge, last))
            else:
                self.edge_ends[ei] = (-1 - ei, -1 - ei)


# --- grading ----------------------------------------------------------------------------------------------------


def grade(shape: Any, pull: g.Vec3, minimum: float) -> list[dict[str, Any]]:
    """One row per face of ``shape``: what ``draft_analysis`` says (``draft_deg``, ``shadowed``, ``status``) plus the
    signed lean range, the surface kind, the area and the shadow samples (kept for the slides)."""
    vertices, triangles = shape.tessellate(md.DEFLECTION)
    caster = dm.ShadowCaster([g.vec(v) for v in vertices], [tuple(t) for t in triangles], pull)
    rows: list[dict[str, Any]] = []
    for index, face in enumerate(shape.Faces):
        kind = g.surface_kind(face)
        if kind == "plane":
            normal, samples = md._plane_samples(face)  # noqa: SLF001
            draft, exact, count = dm.lean_deg(normal, pull), True, 1
        else:
            least, samples, count = md._curved_samples(face, pull)  # noqa: SLF001
            draft, exact = math.degrees(math.asin(min(1.0, least))), False
        share = dm.shadowed_share(caster, samples, md.SHADOW_OFFSET)
        # the signed lean over the face, from the points the shadow test already reads (exact for a plane)
        leans = [dm.dot(n, pull) for _, n in samples]
        low, high = min(leans), max(leans)
        rows.append(
            {
                "index": index,
                "surface": kind,
                "area_mm2": float(face.Area),
                "draft_deg": draft,
                "shadowed": share,
                "samples": count,
                "exact": exact,
                "status": dm.classify(draft, share > 0.0, minimum),
                "lean_low": low,
                "lean_high": high,
                "samples_pts": samples,
            }
        )
    return rows


# --- loops ------------------------------------------------------------------------------------------------------


def edge_points(edge: Any, forward: bool) -> list[g.Vec3]:
    """The points of an edge in the direction it is walked: its two ends for a line, a polyline for any curve."""
    if g.curve_kind(edge) == "line":
        pts = [g.vec(edge.valueAt(edge.FirstParameter)), g.vec(edge.valueAt(edge.LastParameter))]
    else:
        pts = [g.vec(p) for p in edge.discretize(Deflection=0.02)]
    return pts if forward else pts[::-1]


def _plane_of(points: list[g.Vec3]) -> tuple[g.Vec3, g.Vec3, float] | None:
    """A plane through the points, ``(point, unit normal, largest distance of a point to it)``, or None when they lie
    on a line."""
    origin = points[0]
    far = max(points, key=lambda p: g.dist(p, origin))
    axis = g.sub(far, origin)
    if g.length(axis) < 1e-9:
        return None
    best, normal = 0.0, None
    for p in points:
        cand = g.cross(axis, g.sub(p, origin))
        if g.length(cand) > best:
            best, normal = g.length(cand), cand
    if normal is None or best < 1e-9 * g.length(axis):
        return None
    normal = g.unit(normal)
    spread = max(abs(g.dot(g.sub(p, origin), normal)) for p in points)
    return origin, normal, spread


def describe_loop(topo: Topology, walked: list[tuple[int, bool]], pull: g.Vec3, closed: bool) -> dict[str, Any]:
    """The facts of one parting loop: its length, whether it lies in one plane, the heights (along the pull) it runs at,
    and its area across the pull (the largest is the outer loop)."""
    points: list[g.Vec3] = []
    for index, forward in walked:
        pts = edge_points(topo.edges[index], forward)
        points.extend(pts if not points else pts[1:])
    length = sum(float(topo.edges[i].Length) for i, _ in walked)
    e1, e2 = dm.basis_across(pull)
    outline = [(dm.dot(p, e1), dm.dot(p, e2)) for p in points]
    heights = [dm.dot(p, pull) for p in points]
    size = max(
        max(p[0] for p in outline) - min(p[0] for p in outline),
        max(p[1] for p in outline) - min(p[1] for p in outline),
        1.0,
    )
    plane = _plane_of(points)
    planar = plane is not None and plane[2] <= PLANAR_REL_TOL * size + 1e-6
    levels: list[float] = []
    for h in sorted(heights):
        if not levels or h - levels[-1] > PLANAR_REL_TOL * size + 1e-6:
            levels.append(h)
    return {
        "edges": [i for i, _ in walked],
        "walked": walked,
        "closed": closed,
        "length_mm": length,
        "points": len(points),
        "planar": planar,
        "plane_normal": plane[1] if planar and plane is not None else None,
        "plane_point": plane[0] if planar and plane is not None else None,
        "levels": levels,
        "height_mm": (min(heights), max(heights)),
        "area_across_mm2": abs(mm.signed_area(outline)),
    }


# --- the analysis -----------------------------------------------------------------------------------------------


def _clusters(rows: list[dict[str, Any]], topo: Topology) -> list[list[int]]:
    undercut = {str(r["index"]) for r in rows if r["status"] == "undercut"}
    neighbours = {str(i): {str(j) for j in n} for i, n in topo.neighbours.items()}
    return [[int(x) for x in group] for group in mm.components(undercut, neighbours)]


def _directions(topo: Topology, region: list[int], pull: g.Vec3) -> list[tuple[g.Vec3, str]]:
    """Candidate slide directions for a region: the axis of its cylinders and cones, the normal of its planes, then the
    two axes across the pull; each once, none mostly along the pull."""
    found: list[tuple[g.Vec3, str]] = []
    for index in region:
        face = topo.faces[index]
        kind = g.surface_kind(face)
        if kind in ("cylinder", "cone"):
            found.append((g.unit(g.vec(face.Surface.Axis)), f"axis of its {kind} face:{index}"))
        elif kind == "plane":
            u, v = face.Surface.parameter(face.CenterOfMass)
            found.append((g.unit(g.vec(face.normalAt(u, v))), f"normal of its plane face:{index}"))
    e1, e2 = dm.basis_across(pull)
    found += [(e1, "an axis across the pull"), (e2, "the other axis across the pull")]
    out: list[tuple[g.Vec3, str]] = []
    for d, why in found:
        if abs(dm.dot(d, pull)) > SLIDE_MAX_ALONG_PULL:
            continue
        for sign in (1.0, -1.0):
            cand = g.scale(d, sign)
            if all(dm.dot(cand, other) < DIRECTION_TOL for other, _ in out):
                out.append((cand, why))
    return out


def propose_slides(
    shape: Any, topo: Topology, rows: list[dict[str, Any]], pull: g.Vec3, vertices: list[g.Vec3], triangles: list[Any]
) -> list[dict[str, Any]]:
    """One proposal per connected group of undercut faces: the direction it can be pulled out along (a ray from each of
    its sample points, along the direction, meets nothing of the part), preferring the sense that points away from the
    part, then the direction closest to square with the pull."""
    by_index = {r["index"]: r for r in rows}
    centre = g.vec(shape.BoundBox.Center)
    proposals: list[dict[str, Any]] = []
    for number, region in enumerate(_clusters(rows, topo), start=1):
        samples = [s for i in region for s in by_index[i]["samples_pts"]]
        total = sum(by_index[i]["area_mm2"] for i in region) or 1.0
        place = (0.0, 0.0, 0.0)
        for i in region:
            place = g.add(place, g.scale(g.vec(topo.faces[i].CenterOfMass), by_index[i]["area_mm2"] / total))
        outward = g.sub(place, centre)
        best: tuple[tuple[float, float, int], g.Vec3, str] | None = None
        for rank, (direction, why) in enumerate(_directions(topo, region, pull)):
            caster = dm.ShadowCaster(vertices, triangles, direction)
            for sense in (1, -1):
                free = not any(
                    caster.blocked(
                        (
                            p[0] + n[0] * md.SHADOW_OFFSET,
                            p[1] + n[1] * md.SHADOW_OFFSET,
                            p[2] + n[2] * md.SHADOW_OFFSET,
                        ),
                        sense,
                    )
                    for p, n in samples
                )
                if not free:
                    continue
                pointing = g.scale(direction, float(sense))
                key = (-dm.dot(pointing, outward), abs(dm.dot(pointing, pull)), rank)
                if best is None or key < best[0]:
                    best = (key, pointing, why)
        proposals.append(
            {
                "region": number,
                "faces": region,
                "area_mm2": sum(by_index[i]["area_mm2"] for i in region),
                "direction": best[1] if best is not None else None,
                "why": best[2] if best is not None else "",
                "max_share": max(by_index[i]["shadowed"] for i in region),
            }
        )
    return proposals


def analyse(shape: Any, pull: g.Vec3, minimum: float, decision: str | None) -> dict[str, Any]:
    """Everything the engineer needs before a split: the graded faces, the side each takes, the walls that need a
    decision, the faces the silhouette runs across, the parting loops and the slides the undercuts need."""
    topo = Topology(shape)
    rows = grade(shape, pull, minimum)
    vertices_raw, triangles_raw = shape.tessellate(md.DEFLECTION)
    vertices = [g.vec(v) for v in vertices_raw]
    triangles = [tuple(t) for t in triangles_raw]
    proposals = propose_slides(shape, topo, rows, pull, vertices, triangles)
    slivers = [
        s
        for s in proposals
        if s["direction"] is None and s["area_mm2"] <= SLIVER_AREA_MM2 and s["max_share"] <= SLIVER_SHARE
    ]
    slides = [dict(s, region=n) for n, s in enumerate((s for s in proposals if s not in slivers), start=1)]
    in_slide = {i for s in slides for i in s["faces"]}
    raw: dict[str, str] = {}
    for row in rows:
        index = row["index"]
        raw[str(index)] = mm.SLIDE if index in in_slide else mm.side_of_leans(row["lean_low"], row["lean_high"])
    neighbours = {str(i): {str(j) for j in n} for i, n in topo.neighbours.items()}
    edge_faces = {str(e): (str(f[0]), str(f[1])) for e, f in topo.edge_faces.items() if len(f) == 2}

    def under(choice: str | None) -> dict[str, Any]:
        sides, ambiguous = mm.resolve_sides(raw, neighbours, choice)
        parting = [int(e) for e in mm.parting_edges(edge_faces, sides)]
        ends = {str(e): (str(topo.edge_ends[e][0]), str(topo.edge_ends[e][1])) for e in parting}
        loops: list[dict[str, Any]] = []
        for loop in mm.chain_loops(ends):
            walked = [(int(e), bool(fwd)) for e, fwd in loop["edges"]]  # type: ignore[attr-defined]
            loops.append(describe_loop(topo, walked, pull, bool(loop["closed"])) | {"branched": bool(loop["branched"])})
        loops.sort(key=lambda lp: -lp["area_across_mm2"])
        for number, loop in enumerate(loops, start=1):
            loop["number"] = number
            loop["role"] = "outer" if number == 1 else "inner"
        return {
            "sides": {int(f): s for f, s in sides.items()},
            "ambiguous": [{"faces": [int(x) for x in a["faces"]], "touches": a["touches"]} for a in ambiguous],  # type: ignore[attr-defined]
            "straddle": sorted(int(f) for f, s in sides.items() if s == mm.STRADDLE),
            "loops": loops,
            "length": sum(lp["length_mm"] for lp in loops),
        }

    chosen = under(decision)
    proposal: str | None = None
    if chosen["ambiguous"]:
        # walls with no draft that touch both halves, and no decision: the loops are computed under the decision that
        # gives the shorter parting line (a tie goes to the cavity: the outside of the part is the fixed half's)
        tried = {side: under(side) for side in (mm.CAVITY, mm.CORE)}
        proposal = min((mm.CAVITY, mm.CORE), key=lambda side: (round(tried[side]["length"], 6), side != mm.CAVITY))
        chosen = chosen | {k: tried[proposal][k] for k in ("sides", "straddle", "loops")}
    return {
        "topology": topo,
        "rows": rows,
        "proposal": proposal,
        "slides": slides,
        "slivers": [s["faces"] for s in slivers],
        "vertices": vertices,
        "triangles": triangles,
        **chosen,
    }


# --- the parting surface ----------------------------------------------------------------------------------------


def _v(p: g.Vec3) -> Any:
    return App.Vector(*p)


def _across(vec2: tuple[float, float], e1: g.Vec3, e2: g.Vec3) -> g.Vec3:
    return g.add(g.scale(e1, vec2[0]), g.scale(e2, vec2[1]))


def _planar_sheet(loop: dict[str, Any], reach: float, centre: g.Vec3) -> Any:
    """A square face in the plane of the loop, ``2 * reach`` a side, centred on the point of the plane nearest
    ``centre``."""
    origin, normal = loop["plane_point"], loop["plane_normal"]
    foot = g.sub(centre, g.scale(normal, g.dot(g.sub(centre, origin), normal)))
    helper = (1.0, 0.0, 0.0) if abs(normal[0]) < 0.9 else (0.0, 1.0, 0.0)
    u = g.unit(g.cross(normal, helper))
    w = g.cross(normal, u)
    corners = [
        g.add(foot, g.add(g.scale(u, sx * reach), g.scale(w, sy * reach)))
        for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))
    ]
    return Part.Face(Part.makePolygon([_v(c) for c in [*corners, corners[0]]]))


def _end_tangents(seg: dict[str, Any]) -> tuple[g.Vec3, g.Vec3]:
    """The tangent at the start and at the end of a segment, in the direction it is walked."""
    edge = seg["edge"]
    if seg["kind"] == "line":
        d = g.unit(g.sub(seg["b"], seg["a"])) if g.dist(seg["a"], seg["b"]) > 0 else (0.0, 0.0, 0.0)
        return d, d
    first = g.unit(g.vec(edge.tangentAt(edge.FirstParameter)))
    last = g.unit(g.vec(edge.tangentAt(edge.LastParameter)))
    return (first, last) if seg["forward"] else (g.scale(last, -1.0), g.scale(first, -1.0))


def _runoff_kind(edge: Any, pts: list[g.Vec3]) -> str | None:
    """``line`` for a line or any edge whose points all lie on its chord (a straight B-spline), ``arc`` for an open
    circular arc, None for anything else (a closed circle or a free curve)."""
    kind = g.curve_kind(edge)
    if kind == "line":
        return "line"
    if kind == "arc":
        return "arc"
    chord = g.sub(pts[-1], pts[0])
    if kind == "other" and g.length(chord) > 1e-9:
        axis = g.unit(chord)
        deviation = max(g.length(g.cross(g.sub(p, pts[0]), axis)) for p in pts)
        if deviation <= 1e-6 * g.length(chord) + 1e-6:
            return "line"
    return None


def runoff_sheet(topo: Topology, loop: dict[str, Any], pull: g.Vec3, reach: float) -> Any:
    """The parting surface of a loop that is not in one plane: every edge is pushed outward, across the pull, by
    ``reach``, and the corners follow the mitre of their two edges, so neighbouring strips share their edge and the
    surface has no gap. A level edge gives a level strip, an edge parallel to the pull (a step of the line) a wall."""
    e1, e2 = dm.basis_across(pull)

    def flat(p: g.Vec3) -> tuple[float, float]:
        return (dm.dot(p, e1), dm.dot(p, e2))

    segments: list[dict[str, Any]] = []
    for index, forward in loop["walked"]:
        edge = topo.edges[index]
        pts = edge_points(edge, forward)
        kind = _runoff_kind(edge, pts)
        if kind is None:
            msg = (
                f"the parting line has an edge that is neither a line nor a circular arc (edge:{index}, "
                f"{type(edge.Curve).__name__}): the run-off surface is built from lines and arcs only"
            )
            raise PartingError(msg)
        a, b = pts[0], pts[-1]
        across = math.hypot(flat(b)[0] - flat(a)[0], flat(b)[1] - flat(a)[1])
        # an edge within STEP_MAX_DEG of the pull is a step of the line (a drafted wall's edge leans a little)
        step = kind == "line" and across <= STEP_SLOPE * abs(dm.dot(b, pull) - dm.dot(a, pull)) + STEP_TOL_MM
        segments.append(
            {"edge": edge, "index": index, "kind": kind, "a": a, "b": b, "pts": pts, "step": step, "forward": forward}
        )
    count = len(segments)
    if all(s["step"] for s in segments):
        msg = "the parting line runs only along the pull: there is nothing to push outward"
        raise PartingError(msg)
    outline = [flat(p) for s in segments for p in s["pts"]]
    ccw = mm.signed_area(outline) > 0.0

    def normal_of(direction: g.Vec3) -> tuple[float, float]:
        return mm.outward_normal(flat(direction), ccw)

    for s in segments:
        s["t0"], s["t1"] = _end_tangents(s)

    offsets: list[tuple[float, float]] = []
    for k in range(count):  # the junction between segment k-1 and segment k
        j = (k - 1) % count
        while segments[j]["step"]:
            j = (j - 1) % count
        m = k
        while segments[m]["step"]:
            m = (m + 1) % count
        try:
            offsets.append(mm.miter_offset(normal_of(segments[j]["t1"]), normal_of(segments[m]["t0"]), reach))
        except ValueError as exc:
            raise PartingError(
                f"the parting line folds back on itself near edge:{segments[k]['index']}: {exc}"
            ) from exc
    faces: list[Any] = []
    for i, seg in enumerate(segments):
        a, b = seg["a"], seg["b"]
        a2 = g.add(a, _across(offsets[i], e1, e2))
        b2 = g.add(b, _across(offsets[(i + 1) % count], e1, e2))
        if seg["kind"] == "arc":
            k = len(seg["pts"]) // 2
            mid = seg["pts"][k]
            before, after = flat(seg["pts"][k - 1]), flat(seg["pts"][k + 1])
            n_mid = mm.outward_normal((after[0] - before[0], after[1] - before[1]), ccw)
            mid2 = g.add(mid, _across((n_mid[0] * reach, n_mid[1] * reach), e1, e2))
            inner = Part.Arc(_v(a), _v(mid), _v(b)).toShape()
            outer = Part.Arc(_v(a2), _v(mid2), _v(b2)).toShape()
        else:
            inner, outer = Part.makeLine(_v(a), _v(b)), Part.makeLine(_v(a2), _v(b2))
        faces.append(Part.makeRuledSurface(inner, outer))
    return Part.Compound(faces)


def shut_off(topo: Topology, loop: dict[str, Any]) -> Any:
    """A flat face closing an inner loop (an opening the part has through it)."""
    wires = Part.sortEdges([topo.edges[i] for i in loop["edges"]])
    if len(wires) != 1:
        msg = "an inner parting loop does not chain into one closed wire"
        raise PartingError(msg)
    return Part.Face(Part.Wire(wires[0]))


def build_sheet(
    topo: Topology, loops: list[dict[str, Any]], pull: g.Vec3, reach: float, centre: g.Vec3
) -> tuple[Any, str, list[str]]:
    """The parting surface of all loops as one compound of faces, the method in words (planar, run-off, shut-off) and
    what could not be built."""
    faces: list[Any] = []
    parts: list[str] = []
    limits: list[str] = []
    shutoffs = 0
    for loop in loops:
        number = loop["number"]
        if not loop["closed"]:
            limits.append(f"loop {number} is not closed: no surface can be built from an open line")
            continue
        if loop["role"] == "outer":
            if loop["planar"]:
                try:
                    inside = shut_off(topo, loop)
                    faces.extend(_planar_sheet(loop, reach, centre).cut(inside).Faces)
                except (PartingError, Part.OCCError) as exc:
                    limits.append(f"loop {number}: the plane of the line cannot be cut open along it ({exc})")
                    continue
                parts.append("a plane through the line, out to the block")
                continue
            try:
                faces.extend(runoff_sheet(topo, loop, pull, reach).Faces)
                parts.append(
                    f"a run-off surface over {len(loop['levels'])} heights (edges pushed outward across the pull, "
                    "corners mitred, steps as walls)"
                )
            except (PartingError, Part.OCCError) as exc:
                limits.append(f"loop {number}: {exc}")
            continue
        try:
            faces.append(shut_off(topo, loop))
            shutoffs += 1
        except (PartingError, Part.OCCError) as exc:
            limits.append(
                f"loop {number} (inner) cannot be closed with a flat face ({exc}): FreeCAD fills only planar loops "
                "with a flat face; a filled or lofted shut-off has to be made by hand"
            )
    if shutoffs:
        parts.append(f"{shutoffs} flat shut-off(s) close the inner loop(s)")
    return (Part.Compound(faces) if faces else None), "; ".join(parts), limits
