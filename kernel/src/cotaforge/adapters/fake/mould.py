"""``FakeAdapter.draft_analysis``: the draft of the analytic faces of the fake, exact.

The fake knows planes (with a normal) and cylinders (with an axis). A plane's lean from the pull direction is exact; a
cylinder is a full wall round its axis, so some line of it always runs parallel to the pull (zero draft), or the whole
face does when the axis is the pull. The fake has no ray casting: ``shadow_tested`` is false and no undercut is
reported (a real CAD does that test).
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from cotaforge.contract import (
    BodyHandle,
    DraftAnalysisRequest,
    DraftAnalysisResult,
    EdgeHandle,
    FaceDraft,
    FaceHandle,
    PartingAnalysisRequest,
    PartingAnalysisResult,
    PartingLoop,
    WallsToDecide,
    classify_draft,
)
from cotaforge.contract.enums import ErrorCode, SurfaceType

if TYPE_CHECKING:
    from cotaforge.adapters.fake.adapter import FakeAdapter

METHOD = (
    "planes: exact from the face normal; cylinders: a full wall always has a line parallel to the pull, so 0 degrees; "
    "no shadow test in this adapter"
)


def draft_analysis(adapter: FakeAdapter, request: DraftAnalysisRequest) -> DraftAnalysisResult:
    doc = adapter._doc(request)  # noqa: SLF001 - same package family
    wanted = request.body.id if request.body is not None else None
    length = math.sqrt(sum(c * c for c in request.pull))
    pull = tuple(c / length for c in request.pull)
    faces: list[FaceDraft] = []
    for face in doc.faces.values():
        if face.body not in doc.bodies or (wanted is not None and face.body != wanted):
            continue
        if face.surface is SurfaceType.plane and face.normal is not None:
            along = abs(sum(n * p for n, p in zip(face.normal, pull, strict=True)))
            draft, exact = math.degrees(math.asin(min(1.0, along))), True
        elif face.surface is SurfaceType.cylinder:
            draft, exact = 0.0, False
        else:
            continue
        faces.append(
            FaceDraft(
                face=FaceHandle(id=face.id),
                body=BodyHandle(id=face.body),
                surface=face.surface.value,
                area_mm2=face.area_mm2,
                draft_deg=draft,
                samples=1,
                exact=exact,
                status=classify_draft(draft, False, request.min_draft_deg),
            )
        )
    return DraftAnalysisResult(faces=tuple(faces), method=METHOD, shadow_tested=False)


# --- parting_analysis -------------------------------------------------------------------------------------------------

PARTING_METHOD = (
    "planes: the side from the face normal against the pull (exact); a cylinder is the core's side by axis only when "
    "its axis is the pull (no lean), else its silhouette runs across it; no ray casting: no undercut or slide is "
    "reported; the loops chain the edges between a core face and a cavity face"
)
_ZERO_LEAN = math.sin(math.radians(0.01))  # mirror of the worker's mould_math.ZERO_LEAN (a unit test keeps them equal)


def _side(face: Any, pull: tuple[float, float, float]) -> str:
    if face.surface is SurfaceType.plane and face.normal is not None:
        lean = sum(n * p for n, p in zip(face.normal, pull, strict=True))
        if abs(lean) <= _ZERO_LEAN:
            return "zero"
        return "core" if lean > 0 else "cavity"
    if face.surface is SurfaceType.cylinder and face.axis is not None:
        along = abs(sum(a * p for a, p in zip(face.axis.direction, pull, strict=True)))
        return "zero" if along > 1.0 - 1e-9 else "straddle"
    return "zero"


def _groups(faces: set[str], neighbours: dict[str, set[str]]) -> list[list[str]]:
    seen: set[str] = set()
    out: list[list[str]] = []
    for start in sorted(faces):
        if start in seen:
            continue
        group, stack = [], [start]
        seen.add(start)
        while stack:
            node = stack.pop()
            group.append(node)
            for other in sorted(neighbours.get(node, ())):
                if other in faces and other not in seen:
                    seen.add(other)
                    stack.append(other)
        out.append(sorted(group))
    return out


def _resolve(
    raw: dict[str, str], neighbours: dict[str, set[str]], decision: str | None
) -> tuple[dict[str, str], list[tuple[list[str], list[str]]]]:
    sides, ambiguous = dict(raw), []
    for group in _groups({f for f, s in raw.items() if s == "zero"}, neighbours):
        members = set(group)
        touching = {
            raw[n] for f in group for n in neighbours.get(f, ()) if n not in members and raw[n] in ("core", "cavity")
        }
        if len(touching) == 1:
            chosen = next(iter(touching))
        elif decision in ("core", "cavity"):
            chosen = decision
        else:
            ambiguous.append((group, sorted(touching)))
            continue
        for face in group:
            sides[face] = chosen
    return sides, ambiguous


def _loops(adapter: FakeAdapter, doc: Any, body: str, sides: dict[str, str]) -> list[PartingLoop]:
    ends: dict[str, tuple[Any, Any]] = {}
    for edge in doc.edges.values():
        a, b = edge.faces
        if a not in sides or b not in sides or {sides[a], sides[b]} != {"core", "cavity"}:
            continue
        key = (
            (tuple(round(c, 6) for c in edge.start), tuple(round(c, 6) for c in edge.end))
            if edge.start is not None and edge.end is not None
            else (("circle", edge.id),) * 2
        )
        ends[edge.id] = key
    at: dict[Any, list[str]] = {}
    for edge_id, (p, q) in sorted(ends.items()):
        at.setdefault(p, []).append(edge_id)
        at.setdefault(q, []).append(edge_id)
    used: set[str] = set()
    loops: list[PartingLoop] = []
    for first in sorted(ends):
        if first in used:
            continue
        used.add(first)
        chain, start, point, closed = [first], ends[first][0], ends[first][1], False
        while True:
            if point == start:
                closed = True
                break
            following = [e for e in at[point] if e not in used]
            if not following:
                break
            nxt = following[0]
            used.add(nxt)
            chain.append(nxt)
            p, q = ends[nxt]
            point = q if p == point else p
        length = sum(doc.edges[e].length_mm for e in chain)
        loops.append(
            PartingLoop(
                number=len(loops) + 1,
                role="outer",
                closed=closed,
                length_mm=length,
                planar=True,
                levels_mm=(),
                edges=tuple(EdgeHandle(id=e) for e in chain),
            )
        )
    loops.sort(key=lambda lp: -lp.length_mm)
    return [
        lp.model_copy(update={"number": n, "role": "outer" if n == 1 else "inner"}) for n, lp in enumerate(loops, 1)
    ]


def parting_analysis(adapter: FakeAdapter, request: PartingAnalysisRequest) -> PartingAnalysisResult:
    doc = adapter._doc(request)  # noqa: SLF001 - same package family
    bodies = [request.body.id] if request.body is not None else sorted(doc.bodies)
    if len(bodies) != 1 or bodies[0] not in doc.bodies:
        raise adapter._error(  # noqa: SLF001
            ErrorCode.invalid_request, "parting_analysis reads one body: name it with body", request
        )
    body = bodies[0]
    length = math.sqrt(sum(c * c for c in request.pull))
    pull = tuple(c / length for c in request.pull)
    faces = {f.id: f for f in doc.faces.values() if f.body == body}
    raw = {fid: _side(face, pull) for fid, face in faces.items()}
    neighbours: dict[str, set[str]] = {fid: set() for fid in faces}
    for edge in doc.edges.values():
        a, b = edge.faces
        if a in faces and b in faces and a != b:
            neighbours[a].add(b)
            neighbours[b].add(a)
    sides, ambiguous = _resolve(raw, neighbours, request.zero_draft_to)
    proposal = None
    if ambiguous:
        tried = {s: _resolve(raw, neighbours, s)[0] for s in ("cavity", "core")}
        lengths = {s: sum(lp.length_mm for lp in _loops(adapter, doc, body, tried[s])) for s in tried}
        proposal = min(("cavity", "core"), key=lambda s: (round(lengths[s], 6), s != "cavity"))
        sides = tried[proposal]
    straddle = tuple(FaceHandle(id=f) for f, s in sorted(sides.items()) if s == "straddle")
    loops = tuple(_loops(adapter, doc, body, sides))
    return PartingAnalysisResult(
        loops=loops,
        ambiguous=tuple(
            WallsToDecide(faces=tuple(FaceHandle(id=f) for f in group), touches=tuple(touches))
            for group, touches in ambiguous
        ),
        zero_draft_proposal=proposal,
        provisional=proposal is not None,
        straddle=straddle,
        complete=not straddle and all(lp.closed for lp in loops),
        method=PARTING_METHOD,
        warnings=("this adapter has no ray casting: no undercut is reported, which does not mean there are none",),
    )
