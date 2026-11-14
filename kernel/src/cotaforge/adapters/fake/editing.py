"""``FakeAdapter.edit_feature``: in-place editing of the analytic fake.

The fake has no kernel that recomputes dependents, so it edits in place by DETERMINISTIC REPLAY: every successful
mutating call is recorded (``FakeDocument.history``: method, request, id counters just before it); an edit swaps the
requests of the edited features and replays the whole history into a fresh document with the same id counters, so every
face, edge and body keeps its id (the stable ids a parametric CAD keeps). The replay runs without fault injection. If it
raises, nothing was touched (the replay never mutates the real document); if it succeeds the new content
replaces the old one IN PLACE (the document object stays: components of an assembly hold it).

The eligibility policy mirrors the FreeCAD worker's (``worker/edit.py``) so the tool layer sees the same
refusals on both:

* the edited op must be a ``box``, a ``cylinder`` or a ``move_body`` (retargeting a move to another body is
  not an edit), or a feature whose change is only a dimension (``contract.dimensions``: an extrude depth, a hole
  diameter, a fillet radius, a pattern count...), or a feature that consumes a sketch whose dimensions changed
  (``FeatureEdit.sketch``: same entities, constraints and plane);
* every op that depends on the edited feature (found through the body ids in the recorded requests) must be a live
  ``box``/``cylinder``/``move_body``/``boolean`` without ``keep_tools`` (a feature edit also follows the features of
  its own bodies); a ``copy_body``/``mirror_body``/``split`` among them is a static copy, and any op of another kind
  after an edited primitive may depend on it implicitly: both refuse;
* a document with hand-made changes (the test-only hand edit helpers) cannot be replayed and refuses.
"""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING, Any

from cotaforge.adapters.fake.booleans import as_box, signed_intersection_sum
from cotaforge.adapters.fake.faults import FaultEngine
from cotaforge.adapters.fake.model import FakeDocument, FakeFace, FakeFeature, HistoryEntry, Primitive
from cotaforge.contract import (
    AdapterError,
    BodyDelta,
    BodyHandle,
    BodyInterferenceRequest,
    BodyInterferenceResult,
    BodyMeasure,
    BodyPairReading,
    BodyReading,
    BoundingBox,
    CutReading,
    EditFeatureRequest,
    EditFlag,
    EditResult,
    Empty,
    ErrorCode,
    FaceHandle,
    FeatureHandle,
    HealthIssue,
    MassRequest,
    MeasureBodiesResult,
    RefsDelta,
)
from cotaforge.contract.dimensions import DIMENSION_FIELDS, non_dimension_changes, sketch_edit_refusal

if TYPE_CHECKING:
    from cotaforge.adapters.fake.adapter import FakeAdapter

FEATURE_KINDS = frozenset(DIMENSION_FIELDS)
"""Feature ops whose dimensions (and the dimensions of the sketch they consume) can be edited in place."""
EDITABLE_KINDS = frozenset({"box", "cylinder", "move_body"}) | FEATURE_KINDS
"""Ops whose request can be edited in place."""
LIVE_KINDS = frozenset({"box", "cylinder", "move_body", "boolean", "shrink_body"})
"""Ops that stay linked to their inputs (a recompute updates them): the only ones allowed among the dependents of an
edited primitive."""
FEATURE_LIVE_KINDS = LIVE_KINDS | FEATURE_KINDS | frozenset({"mirror", "shell", "draft"})
"""The features of a body follow an edit of an earlier feature of the same body (the PartDesign chain)."""
EXPLICIT_INPUT_KINDS = frozenset(
    {"box", "cylinder", "boolean", "move_body", "copy_body", "mirror_body", "shrink_body", "split"}
)
"""Ops whose inputs are exactly the bodies named in their request (everything else may depend implicitly)."""
_VOLUME_EPS = 1e-9
_GEOMETRY_EPS = 1e-6


def _input_bodies(entry: HistoryEntry) -> set[str]:
    """Body ids an op reads, from its recorded request (only meaningful for ``EXPLICIT_INPUT_KINDS``)."""
    request = entry.request
    out: set[str] = set()
    for name in ("target", "body", "source"):
        handle = getattr(request, name, None)
        if isinstance(handle, BodyHandle):
            out.add(handle.id)
    out.update(t.id for t in getattr(request, "tools", ()) or ())
    return out


def _output_bodies(entry: HistoryEntry, feature: FakeFeature | None) -> set[str]:
    out = _input_bodies(entry) if entry.method in ("boolean", "move_body") else set()
    if feature is not None:
        out.update(feature.created_bodies)
        out.update(feature.modified_bodies)
    return out


def _entry_of(doc: FakeDocument, feature_id: str) -> tuple[int, HistoryEntry] | None:
    for index, entry in enumerate(doc.history):
        if entry.feature_id == feature_id:
            return index, entry
    return None


def _feature(doc: FakeDocument, feature_id: str | None) -> FakeFeature | None:
    return next((f for f in doc.features if f.id == feature_id), None)


def refusals(doc: FakeDocument, request: EditFeatureRequest) -> dict[str, str]:
    """Why each edit of ``request`` cannot be done in place (empty: every edit can)."""
    refused: dict[str, str] = {}
    if doc.hand_edited:
        reason = "the document has hand-made changes that a replay cannot reproduce"
        return {edit.feature.id: reason for edit in request.edits}
    edited_ids = {edit.feature.id for edit in request.edits}
    for edit in request.edits:
        found = _entry_of(doc, edit.feature.id)
        if found is None:
            refused[edit.feature.id] = "the feature is not one the harness made (hand-made or unknown)"
            continue
        start, entry = found
        if edit.kind != entry.method:
            refused[edit.feature.id] = f"the feature is a {entry.method}, not a {edit.kind}"
            continue
        if edit.kind not in EDITABLE_KINDS:
            refused[edit.feature.id] = f"an in-place edit of a {edit.kind} is not supported yet"
            continue
        if edit.kind == "move_body" and getattr(edit.request, "body", None) != getattr(entry.request, "body", None):
            refused[edit.feature.id] = "retargeting a move to another body is not an in-place edit"
            continue
        if edit.kind in FEATURE_KINDS:
            other = non_dimension_changes(edit.kind, _dump(entry.request), _dump(edit.request))
            if other:
                refused[edit.feature.id] = f"{', '.join(other)} is not a dimension of the {edit.kind}"
                continue
        if edit.sketch is not None:
            why = _sketch_refusal(doc, edit)
            if why is not None:
                refused[edit.feature.id] = why
                continue
        blocker = _blocker(doc, start, entry, edited_ids, edit.kind in FEATURE_KINDS)
        if blocker is not None:
            refused[edit.feature.id] = blocker
    return refused


def _dump(request: Any) -> dict[str, Any]:
    return request.model_dump(mode="json")


def _sketch_entry(doc: FakeDocument, operation_id: str) -> tuple[int, HistoryEntry] | None:
    for index, entry in enumerate(doc.history):
        if entry.method == "sketch" and entry.request.operation_id == operation_id:
            return index, entry
    return None


def _sketch_refusal(doc: FakeDocument, edit: Any) -> str | None:
    found = _sketch_entry(doc, edit.sketch.operation_id)
    if found is None:
        return f"the sketch {edit.sketch.operation_id} is not one the harness made"
    return sketch_edit_refusal(_dump(found[1].request), _dump(edit.sketch))


def _blocker(
    doc: FakeDocument, start: int, entry: HistoryEntry, edited_ids: set[str], feature_edit: bool = False
) -> str | None:
    """The first dependent of the entry at ``start`` that an edit cannot update, as a reason (None: all can). The
    dependents of an edited feature are the features that work on its bodies; those of a primitive are every op after
    it that is not an explicit reader of other bodies."""
    dependent_bodies = _output_bodies(entry, _feature(doc, entry.feature_id))
    live = FEATURE_LIVE_KINDS if feature_edit else LIVE_KINDS
    for later in doc.history[start + 1 :]:
        if later.feature_id is None:
            continue  # a sketch or a datum: no feature of its own; the op that consumes it is judged below
        feature = _feature(doc, later.feature_id)
        label = feature.name if feature is not None else later.feature_id
        if later.method in EXPLICIT_INPUT_KINDS:
            if not (_input_bodies(later) & dependent_bodies):
                continue
        elif feature_edit and not (_output_bodies(later, feature) & dependent_bodies):
            continue  # a feature of another body does not read this one
        # a dependent (or an op of a kind that may depend implicitly on anything before it)
        if later.method not in live:
            what = "a static copy" if later.method in EXPLICIT_INPUT_KINDS else "an op that may depend on it"
            return f"{label} ({later.method}) comes after and is {what}: it would not follow the edit"
        if later.method == "boolean" and getattr(later.request, "keep_tools", False):
            return f"{label} (boolean with keep_tools) keeps a static copy of its tool: it would not follow the edit"
        dependent_bodies |= _output_bodies(later, feature)
    return None


def closure(doc: FakeDocument, edited_ids: set[str]) -> set[str]:
    """Feature ids a recompute would update: the edited ones and everything that reads their bodies."""
    reached: set[str] = set()
    bodies: set[str] = set()
    for entry in doc.history:
        if entry.feature_id is None:
            continue
        feature = _feature(doc, entry.feature_id)
        if entry.feature_id in edited_ids:
            reached.add(entry.feature_id)
            bodies |= _output_bodies(entry, feature)
        elif entry.method in EXPLICIT_INPUT_KINDS and _input_bodies(entry) & bodies:
            reached.add(entry.feature_id)
            bodies |= _output_bodies(entry, feature)
        elif entry.method in FEATURE_LIVE_KINDS - EXPLICIT_INPUT_KINDS and _output_bodies(entry, feature) & bodies:
            reached.add(entry.feature_id)
            bodies |= _output_bodies(entry, feature)
    return reached


def _measure(adapter: FakeAdapter, doc: FakeDocument) -> dict[str, BodyMeasure]:
    """Per live body: the neutral measures every adapter reports."""
    saved = adapter._active
    adapter._active = doc
    try:
        out: dict[str, BodyMeasure] = {}
        for body_id in list(doc.bodies):
            ask = MassRequest(operation_id="edit.measure", body=BodyHandle(id=body_id))
            mass = adapter.mass_properties(ask)
            box = adapter.bounding_box(ask)
            faces = [f for f in doc.faces.values() if f.body == body_id]
            face_ids = {f.id for f in faces}
            edges = [e for e in doc.edges.values() if e.faces[0] in face_ids]
            out[body_id] = BodyMeasure(
                volume_mm3=mass.volume_mm3,
                bbox=box,
                centroid=mass.centroid if mass.volume_mm3 > _VOLUME_EPS else None,
                solids=1 if mass.volume_mm3 > _VOLUME_EPS else 0,
                faces=len(faces),
                edges=len(edges),
            )
        return out
    finally:
        adapter._active = saved


def _cut_volumes(doc: FakeDocument, feature_ids: set[str]) -> dict[str, float]:
    """The volume each subtract of ``feature_ids`` removes (the negative primitives it made)."""
    out: dict[str, float] = {}
    for feature in doc.features:
        if feature.id in feature_ids and feature.kind == "boolean_subtract":
            out[feature.id] = sum(
                doc.primitives[p].volume_mm3 for p in feature.primitives if doc.primitives[p].sign < 0
            )
    return out


def measure_bodies(adapter: FakeAdapter, request: Empty) -> MeasureBodiesResult:
    """The read-only reading of ``FakeAdapter.measure_bodies``: every body, what every subtraction removes. The fake's
    bodies are analytic and never overlap in a way it reports, so ``overlaps`` is always empty."""
    doc = adapter._doc(request)
    bodies = tuple(
        BodyReading(body=BodyHandle(id=body_id), label=_label(doc, body_id), measure=measure)
        for body_id, measure in _measure(adapter, doc).items()
    )
    names = {f.id: f.name for f in doc.features}
    cuts = tuple(
        CutReading(feature=FeatureHandle(id=feature_id), label=names.get(feature_id, ""), removed_mm3=removed)
        for feature_id, removed in _cut_volumes(doc, set(names)).items()
    )
    return MeasureBodiesResult(bodies=bodies, cuts=cuts)


def body_interference(adapter: FakeAdapter, request: BodyInterferenceRequest) -> BodyInterferenceResult:
    """The read-only pairwise reading of ``FakeAdapter.body_interference``: the common volume of two bodies is the
    signed sum over their primitives (the same rule as the components of an assembly); the distance is exact only
    between bodies made of positive axis-aligned boxes. Anything else is left unmeasured (``None``) with a note."""
    doc = adapter._doc(request)
    if request.bodies is None:
        ids = list(doc.bodies)
    else:
        ids = []
        for handle in request.bodies:
            if handle.id not in doc.bodies:
                raise adapter._error(ErrorCode.invalid_reference, f"unknown body {handle.id}", request)
            ids.append(handle.id)
    prims = {body_id: [doc.primitives[p] for p in doc.bodies[body_id]] for body_id in ids}
    pairs = []
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            common = signed_intersection_sum(prims[a], prims[b])
            notes = [] if common is not None else ["the fake cannot intersect these shapes"]
            if common is not None and common > _VOLUME_EPS:
                distance: float | None = 0.0
            else:
                distance = _box_distance(prims[a], prims[b])
                if distance is None:
                    notes.append("the fake measures distances between axis-aligned boxes only")
            pairs.append(
                BodyPairReading(
                    a=BodyHandle(id=a),
                    b=BodyHandle(id=b),
                    common_mm3=None if common is None else max(common, 0.0),
                    distance_mm=distance,
                    note="; ".join(notes),
                )
            )
    return BodyInterferenceResult(pairs=tuple(pairs))


def _box_distance(a: list[Primitive], b: list[Primitive]) -> float | None:
    """Minimum distance between two unions of positive axis-aligned boxes (``None``: another shape is involved)."""
    boxes_a = [as_box(p) if p.sign > 0 else None for p in a]
    boxes_b = [as_box(p) if p.sign > 0 else None for p in b]
    if not boxes_a or not boxes_b or None in boxes_a or None in boxes_b:
        return None
    best = math.inf
    for p in boxes_a:
        for q in boxes_b:
            assert p is not None and q is not None
            gaps = [max(0.0, q.min[k] - p.max[k], p.min[k] - q.max[k]) for k in range(3)]
            best = min(best, math.sqrt(sum(gap * gap for gap in gaps)))
    return best


def _label(doc: FakeDocument, body_id: str) -> str:
    for feature in reversed(doc.features):
        if body_id in feature.created_bodies or body_id in feature.modified_bodies:
            return feature.name
    return ""


def _replay(adapter: FakeAdapter, doc: FakeDocument, history: list[HistoryEntry]) -> FakeDocument:
    """A fresh document built by running ``history`` again, same ids; raises ``AdapterError`` when an op fails."""
    fresh = FakeDocument(id=doc.id, kind=doc.kind, name=doc.name, deterministic_ids=doc.deterministic_ids)
    saved = (adapter._active, adapter._faults, adapter._last_fired, adapter._replaying)
    adapter._active = fresh
    adapter._faults = FaultEngine(None)
    adapter._replaying = True
    try:
        for entry in history:
            fresh.counters = dict(entry.counters)
            getattr(adapter, entry.method)(entry.request)
            fresh.history.append(HistoryEntry(entry.method, entry.request, dict(entry.counters), entry.feature_id))
        # the replay allocated the ids it was given, so the counters end where the original document's did
        fresh.counters = dict(doc.counters)
    finally:
        adapter._active, adapter._faults, adapter._last_fired, adapter._replaying = saved
    return fresh


def _face_moved(old: FakeFace, new: FakeFace) -> bool:
    return (
        abs(old.area_mm2 - new.area_mm2) > _GEOMETRY_EPS
        or max(abs(a - b) for a, b in zip(old.center, new.center, strict=True)) > _GEOMETRY_EPS
        or old.radius != new.radius
    )


def _box_moved(a: BoundingBox, b: BoundingBox) -> bool:
    return any(abs(x - y) > _GEOMETRY_EPS for x, y in zip((*a.min, *a.max), (*b.min, *b.max), strict=True))


def edit_feature(adapter: FakeAdapter, request: EditFeatureRequest) -> EditResult:
    started = time.perf_counter()
    doc = adapter._doc(request)
    refused = refusals(doc, request)
    edited_ids = {edit.feature.id for edit in request.edits}
    reached = closure(doc, edited_ids)
    recomputed = tuple(FeatureHandle(id=f.id) for f in doc.features if f.id in reached)

    def done(**fields: Any) -> EditResult:
        fields.setdefault("recomputed", recomputed)
        return EditResult(dry_run=request.dry_run, duration_ms=(time.perf_counter() - started) * 1000.0, **fields)

    if refused:
        return done(rebuild_ok=True, applied=False, refused=refused, recomputed=())
    if request.dry_run:
        return done(rebuild_ok=True, applied=False)

    new_requests = {edit.feature.id: edit.request for edit in request.edits}
    new_sketches = {edit.sketch.operation_id: edit.sketch for edit in request.edits if edit.sketch is not None}
    history = [
        HistoryEntry(
            e.method,
            new_sketches.get(e.request.operation_id or "", e.request)
            if e.method == "sketch"
            else new_requests.get(e.feature_id or "", e.request),
            dict(e.counters),
            e.feature_id,
        )
        for e in doc.history
    ]
    before = _measure(adapter, doc)
    try:
        fresh = _replay(adapter, doc, history)
    except AdapterError as exc:
        owner = next((e.feature_id for e in history if e.request.operation_id == exc.info.operation_id), None)
        issue = HealthIssue(
            feature=FeatureHandle(id=owner) if owner else None,
            object=exc.info.operation_id or "",
            state=("Invalid",),
            message=f"{exc.info.code.value}: {exc.info.message}",
        )
        return done(
            rebuild_ok=False,
            applied=False,
            health=(issue,),
            failure=f"the recompute failed at {exc.info.operation_id or 'an op'}: {exc.info.message}",
            failure_kind="semantic",
        )
    after = _measure(adapter, fresh)

    deltas: list[BodyDelta] = []
    flags: list[EditFlag] = []
    cuts_before, cuts_after = _cut_volumes(doc, reached), _cut_volumes(fresh, reached)
    for feature_id, removed in cuts_before.items():
        if removed > _GEOMETRY_EPS and cuts_after.get(feature_id, 0.0) <= _GEOMETRY_EPS:
            name = next((f.name for f in fresh.features if f.id == feature_id), feature_id)
            flags.append(
                EditFlag(
                    kind="cut_without_effect",
                    severity="warning",
                    message=f"{name} no longer removes any material (its tool misses the body)",
                    features=(FeatureHandle(id=feature_id),),
                )
            )
    for body_id in dict.fromkeys([*before, *after]):
        old, new = before.get(body_id), after.get(body_id)
        label = _label(fresh if new is not None else doc, body_id)
        if old is None and new is None:
            continue
        if old is not None and new is not None and not _box_moved(old.bbox, new.bbox):
            if abs(old.volume_mm3 - new.volume_mm3) <= _GEOMETRY_EPS and old.faces == new.faces:
                continue  # untouched by the edit: not worth reporting
        deltas.append(BodyDelta(body=BodyHandle(id=body_id), label=label, before=old, after=new))
        if old is not None and (new is None or new.solids == 0):
            flags.append(
                EditFlag(
                    kind="body_disappeared",
                    severity="failure",
                    message=f"body {label or body_id} has no material after the edit",
                    bodies=(BodyHandle(id=body_id),),
                )
            )
        elif old is None:
            flags.append(
                EditFlag(
                    kind="body_appeared",
                    severity="warning",
                    message=f"body {label or body_id} appeared",
                    bodies=(BodyHandle(id=body_id),),
                )
            )
    failures = [flag for flag in flags if flag.severity == "failure"]
    if failures:
        return done(
            rebuild_ok=False,
            applied=False,
            delta=tuple(deltas),
            flags=tuple(flags),
            failure=failures[0].message,
            failure_kind="semantic",
        )

    created = [FaceHandle(id=i) for i in fresh.faces if i not in doc.faces]
    removed = [FaceHandle(id=i) for i in doc.faces if i not in fresh.faces]
    modified = [FaceHandle(id=i) for i in fresh.faces if i in doc.faces and _face_moved(doc.faces[i], fresh.faces[i])]
    # the new content replaces the old IN PLACE: the document object stays (components of an assembly hold it)
    for name in (
        "features",
        "sketches",
        "primitives",
        "faces",
        "edges",
        "vertices",
        "bodies",
        "components",
        "datum_planes",
        "datum_axes",
        "datum_marks",
        "counters",
        "history",
    ):
        setattr(doc, name, getattr(fresh, name))
    return done(
        rebuild_ok=True,
        applied=True,
        delta=tuple(deltas),
        flags=tuple(flags),
        refs=RefsDelta(created=tuple(created), removed=tuple(removed), modified=tuple(modified)),
    )
