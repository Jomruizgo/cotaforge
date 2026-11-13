"""Executor: cursor over a plan, bounded retry, undo, verification and a structured trace.

Implements the step algorithm and the flip-retry extension, plus ``extend`` (append operations,
re-validating the plan), ``undo_to`` (revert executed steps newest-first, keeping their records as
evidence) and the public ``check_health`` (canary on any trigger).
"""

import math
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from pydantic import BaseModel, TypeAdapter, ValidationError

from cotaforge.contract import (
    REQUEST_BY_KIND,
    Adapter,
    AdapterError,
    ArcEntity,
    BodyHandle,
    BoundingBox,
    CanaryResult,
    CircleEntity,
    ComponentHandle,
    ComponentRequest,
    DatumAxisRequest,
    DatumAxisResult,
    DatumPlaneRequest,
    DatumPlaneResult,
    DatumPlaneTarget,
    DocumentHandle,
    EdgeInfo,
    EditFeatureRequest,
    EditResult,
    Empty,
    ErrorCode,
    ErrorInfo,
    ExtensionRequest,
    FaceHandle,
    FaceInfo,
    FaceRequest,
    FeatureEdit,
    FeatureHandle,
    Handle,
    InterferenceResult,
    JointHandle,
    MassProperties,
    MassRequest,
    NamedDimension,
    NewDocumentRequest,
    OperationResult,
    OperationSpec,
    RectEntity,
    Request,
    SketchConstraint,
    SketchFrame,
    SketchHandle,
    SketchRequest,
    SketchResult,
    SquareEntity,
    UndoResult,
    Vec3,
    extension_op_name,
    plain_op_name,
)
from cotaforge.contract.dimensions import DIMENSION_FIELDS, non_dimension_changes, sketch_edit_refusal
from cotaforge.executor.build import DISPATCH, NON_FEATURE_OPS, build_request
from cotaforge.executor.config import DEFAULT_CONFIG, ExecutorConfig
from cotaforge.executor.context import ExecutionContext, StateSnapshot
from cotaforge.executor.diagnosis import Diagnosis, RepairAction, diagnose
from cotaforge.executor.edge_sets import describe_edge
from cotaforge.executor.errors import ExecutorStateError, ResolutionError, UndoFloorError, UndoInterruptedError
from cotaforge.executor.resolver import Resolver
from cotaforge.executor.trace import (
    AttemptRecord,
    EditRecord,
    ExecutionTrace,
    ExecutorState,
    ExecutorStatus,
    HealthEvent,
    HealthTrigger,
    RevertRecord,
    StepRecord,
    StepStatus,
    active_steps_of,
)
from cotaforge.ir.errors import PlanError
from cotaforge.ir.extension import ExtensionOperation, is_extension_op
from cotaforge.ir.ops_features import Operation
from cotaforge.ir.plan import Plan, parse_plan
from cotaforge.ir.refs import EdgeRef, EdgeSet, FaceRef, PlaneRef, VertexRef
from cotaforge.verification.expected import forget_geometry_in
from cotaforge.verification.observe import ASSEMBLY_OPS, Observation, box_request, gather_observation, mass_request
from cotaforge.verification.results import CheckKind, CheckResult, Expectation
from cotaforge.verification.verifier import Verifier

MATE_FLIP_OPS = frozenset({"mate_coincident", "mate_distance"})
PATTERN_OPS = frozenset({"linear_pattern", "circular_pattern", "mirror"})
MATE_OPS = frozenset({"mate_concentric", "mate_coincident", "mate_distance"})
PLACE_TOL_MM = 1e-6  # a feature rebuilt in the place it had: farther than this and it moved


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _in_place_of(request: Request, recorded: dict[str, Any]) -> Request:
    """``request`` measured from the origin the feature stands on in the document (``recorded``): an edit changes the
    values asked, not the point they are measured from, so the origin is not a difference between the two."""
    if "origin" not in type(request).model_fields:
        return request
    origin = recorded.get("origin")
    return request.model_copy(update={"origin": None if origin is None else tuple(origin)})


_PROBED_REFS = (FaceRef, EdgeRef, VertexRef)
# adapter error codes that say the worker may be dead, hung or the document inconsistent: an edit that raised one is
# neither rolled back nor safe to rebuild from, so the session degrades
WORKER_GONE_CODES = frozenset({ErrorCode.degraded_session, ErrorCode.timeout, ErrorCode.session_lost})
CLEAN_REFUSALS = frozenset({ErrorCode.invalid_request, ErrorCode.invalid_reference, ErrorCode.not_supported})
"""Errors of an adapter that checked the request before touching the part: it answered, and the part is as it was."""
# adapter error codes that say the VALUE does not build (an in-place edit that raised one was rolled back by the worker)
_SEMANTIC_CODES = frozenset(
    {ErrorCode.invalid_request, ErrorCode.invalid_reference, ErrorCode.geometry_failed, ErrorCode.rebuild_failed}
)
_OPERATION_ADAPTER: TypeAdapter = TypeAdapter(Operation)


def _named_refs(op: Any) -> list[tuple[str, Any]]:
    """``(field path, ref)`` pairs of an operation (``face``, ``edges[0]``, ``on``, ...) plus the refs one level
    down in a datum definition (``define.base``, ``define.entity``, ``define.about``, ``define.a`` ...); refs
    nested inside a ref (an ``EdgeRef.between`` face) are resolved through their parent."""
    out: list[tuple[str, Any]] = []
    for name in type(op).model_fields:
        value = getattr(op, name)
        if isinstance(value, _PROBED_REFS):
            out.append((name, value))
        elif isinstance(value, tuple | list):
            out.extend((f"{name}[{i}]", item) for i, item in enumerate(value) if isinstance(item, _PROBED_REFS))
        elif name == "define" and isinstance(value, BaseModel):
            for inner in type(value).model_fields:
                item = getattr(value, inner)
                if isinstance(item, _PROBED_REFS):
                    out.append((f"define.{inner}", item))
    return out


def _refs_to_probe_after_an_edit(op: Any) -> list[tuple[str, Any]]:
    """The refs of ``op`` that can be resolved in the document as it stands after an edit: a fillet or a chamfer
    consumed the edges it names, so they are not in the document any more (and nothing is lost: the adapter gave the op
    the edges it had)."""
    refs = _named_refs(op)
    if getattr(op, "op", None) in ("fillet", "chamfer"):
        return [(path, ref) for path, ref in refs if not isinstance(ref, EdgeRef)]
    return refs


def _route_of(path: str) -> tuple[str | int, ...]:
    """The keys that reach the ref at ``path`` (``edges[0]``, ``define.base``) from the dump of its op."""
    out: list[str | int] = []
    for part in path.split("."):
        name, index = _parse_field_path(part)
        out.append(name)
        if index is not None:
            out.append(index)
    return tuple(out)


def _extension_face_refs(
    op: ExtensionOperation, specs: Mapping[str, Any]
) -> list[tuple[str, tuple[str | int, ...], Any]]:
    """The faces named in the arguments of an adapter operation, as ``(path, route in params, ref)``: the paths are the
    ones ``build_extension`` records for them, so the faces it resolved when it ran can be told apart now."""
    spec = specs.get(op.op)
    if spec is None:
        return []
    try:
        model = spec.params.model_validate(op.params)
    except ValidationError:
        return []
    out: list[tuple[str, tuple[str | int, ...], Any]] = []

    def walk(value: Any, path: str, route: tuple[str | int, ...]) -> None:
        if isinstance(value, FaceRef):
            out.append((path, route, value))
        elif isinstance(value, BaseModel):
            for name, field in type(value).model_fields.items():
                walk(getattr(value, name), f"{path}.{name}", (*route, field.alias or name))
        elif isinstance(value, tuple | list):
            for i, item in enumerate(value):
                walk(item, f"{path}[{i}]", (*route, i))
        elif isinstance(value, dict):
            for key, item in value.items():
                walk(item, f"{path}.{key}", (*route, key))

    for name, field in type(model).model_fields.items():
        walk(getattr(model, name), name, (field.alias or name,))
    return out


def _vec(point: Any) -> str:
    return "(" + ", ".join(f"{float(x):g}" for x in point) + ")"


def _face_probeable(face: FaceRef, ctx: ExecutionContext) -> bool:
    """Whether ``resolver.face`` can attempt this ref right now: a ``from_selection`` ref names a
    live CAD selection directly and is always ready to probe (its own staleness is exactly what probing
    exists to catch, at extend time rather than at next_operation); a ``from_op`` ref is only probeable
    once the operation it names has actually produced a feature."""
    return face.from_selection is not None or face.from_op in ctx.feature_by_op


def _edge_probeable(ref: EdgeRef, ctx: ExecutionContext) -> bool:
    if ref.from_selection is not None:
        return True
    faces = ref.between if ref.between is not None else (ref.of,)
    return all(face is not None and _face_probeable(face, ctx) for face in faces)


def _vertex_probeable(ref: VertexRef, ctx: ExecutionContext) -> bool:
    if ref.faces is not None:
        return all(_face_probeable(face, ctx) for face in ref.faces)
    return ref.edge is not None and _edge_probeable(ref.edge, ctx)


def _feature_of(step: StepRecord) -> FeatureHandle | None:
    if not step.attempts:
        return None
    result = step.attempts[-1].result
    return result.feature if result is not None else None


def _revert_left_cad_intact(info: ErrorInfo) -> bool:
    """Only ``rebuild_failed`` from ``undo_last`` says "rolled back: the document and the registry are as before the
    attempt" (the FreeCAD worker's atomic revert). A timeout (the RPC layer kills the worker), ``internal``
    flagged ``degraded`` -- any other code, or any error carrying ``degraded`` --
    leaves the CAD in an unknown state, whatever its ``recoverable`` flag says (that flag is about retrying a
    REQUEST, not about the state a failed revert leaves behind)."""
    if info.details.get("reason") == "undo_floor" and not info.details.get("degraded"):
        return True  # the adapter refused BEFORE touching anything: the feature has no undo journal (after a resume)
    return info.code is ErrorCode.rebuild_failed and info.recoverable and not info.details.get("degraded")


# --- repair helpers -------------------------------

_ENTITY_DIMENSION_ATTRIBUTES: dict[type, tuple[str, ...]] = {
    RectEntity: ("width", "height"),
    SquareEntity: ("side",),
    CircleEntity: ("diameter",),
    ArcEntity: ("radius",),
}


def _entity_dimension_attributes(entity: Any) -> tuple[str, ...]:
    """The ``NamedDimension.attribute`` values the IR declares for a sketch entity's own kind (the exact
    vocabulary of dimensions: width/height, side, diameter,
    radius). A line has no single driving dimension in this vocabulary and is skipped."""
    attributes = _ENTITY_DIMENSION_ATTRIBUTES.get(type(entity), ())
    if isinstance(entity, RectEntity) and entity.chamfer_mm is not None:
        return (*attributes, "chamfer")
    return attributes


def _entity_center(entity: Any) -> tuple[float, float] | None:
    center = getattr(entity, "center", None)
    return tuple(center) if center is not None else None  # type: ignore[return-value]


def _parse_field_path(field_path: str) -> tuple[str, int | None]:
    """``"edges[0]" -> ("edges", 0)``; ``"a" -> ("a", None)``."""
    if field_path.endswith("]") and "[" in field_path:
        name, _, rest = field_path.partition("[")
        return name, int(rest[:-1])
    return field_path, None


def _with_new_handle_id(current: Any, index: int | None, new_id: str) -> Any:
    """Rebuild ``current`` (a resolved request field) with ``new_id`` in place of the stale handle's id;
    ``None`` when ``current`` does not have the shape a re-resolved reference could patch (defensive: an
    unrecognised field is left alone rather than guessed at)."""
    if index is None:
        if not isinstance(current, Handle):
            return None
        return type(current)(id=new_id)
    if not isinstance(current, tuple) or index < 0 or index >= len(current):
        return None
    item = current[index]
    if not isinstance(item, Handle):
        return None
    items = list(current)
    items[index] = type(item)(id=new_id)
    return tuple(items)


class Executor:
    def __init__(
        self,
        plan: Plan,
        adapter: Adapter,
        verifier: Verifier,
        config: ExecutorConfig = DEFAULT_CONFIG,
        documents: Mapping[str, DocumentHandle] | None = None,
    ) -> None:
        self.plan = plan
        self.adapter = adapter
        self.verifier = verifier
        self.config = config
        self.state = ExecutorState.idle
        self.cursor = 0
        self._documents: dict[str, DocumentHandle] = dict(documents or {})
        self._extension_specs: dict[str, OperationSpec] | None = None
        self._ctx: ExecutionContext | None = None
        self._resolver: Resolver | None = None
        self._steps: list[StepRecord] = []
        self._health: list[HealthEvent] = []
        self._reverts: list[RevertRecord] = []
        self._edits: list[EditRecord] = []
        self._edited_requests: dict[str, tuple[StepRecord, dict[str, Any]]] = {}
        """Op id -> (the step it belongs to, the request now in the document) for ops edited in place: the
        ``StepRecord`` is frozen and keeps the request the op RAN with. An entry is only valid while that very step is
        the active one (an op undone and re-run has a new step, which ran with the plan's current values)."""
        self._pause_requested = False
        self._last_mass: MassProperties | None = None
        self._last_bbox: BoundingBox | None = None
        self._fresh: tuple[MassProperties, BoundingBox, bool] | None = None
        """The measure the part had when the last step ended (mass, box, whether an assembly op took it): the next step
        reads it instead of measuring again. Dropped by anything that may change the model other than a step that ended
        in a verdict: an undo, an in-place edit, a step recorded from outside, a hand edit (``invalidate_measure``)."""
        self._model_generation = 0
        self._step_generation = 0
        self._pending_frames: dict[str, SketchFrame] = {}
        self._moved_places: dict[str, float] = {}
        self._pending_datums: dict[str, DatumPlaneResult | DatumAxisResult] = {}
        self._external_features: dict[str, FeatureHandle] = {}
        self._step_saved: dict[str, Any] | None = None
        """The verifier state saved before the op in flight computed its expectations (see ``next_operation``)."""
        self._verifier_states: dict[str, dict[str, Any]] = {}
        """Op id -> the verifier state as it was just before that op succeeded: what ``undo_to`` restores."""
        self._reverted_cursor: int | None = None
        """The cursor ``undo_to`` left when it set ``_tail_reverted``: the flag only describes the op AT that cursor."""
        self._partial_revert: tuple[str, ...] = ()
        self._last_undo_warnings: tuple[str, ...] = ()
        self._cleanup_error: ErrorInfo | None = None
        self._tail_reverted = False
        """Set by ``undo_to`` when it actually reverted something; consulted and cleared by ``extend``:
        a plain redo (``resume``/``next_operation`` with no intervening ``extend``) still re-runs
        the reverted tail unchanged, but ``extend`` replaces it instead of appending after it."""
        self._drifted_ops: frozenset[str] = frozenset()
        self._undo_floor = 0
        """Index into ``_steps``: the steps before it ran before the last ``reattach`` and cannot be undone."""

    @classmethod
    def reattach(
        cls,
        plan: Plan,
        adapter: Adapter,
        verifier: Verifier,
        config: ExecutorConfig = DEFAULT_CONFIG,
        *,
        trace: ExecutionTrace,
        context: ExecutionContext,
        cursor: int,
        state: ExecutorState,
        reverted_cursor: int | None = None,
        edited_requests: Mapping[str, dict[str, Any]] | None = None,
        external_features: Mapping[str, FeatureHandle] | None = None,
        drifted_ops: Sequence[str] = (),
    ) -> "Executor":
        """An executor over a document the adapter REOPENED (``open_document``) instead of building it: nothing runs.
        (The design calls this ``Executor.resume``; ``resume`` already un-pauses a paused executor.)

        Restored, one private attribute at a time: the plan; the context (handles by op id, sketch frames, datums --
        what the trace alone cannot rebuild); the steps, health events, reverts and in-place edits of ``trace``; the
        last measured mass and box; the cursor and the state (``running`` is stored as ``paused``: nothing is in
        flight after a reopen); a pending revert at ``reverted_cursor``; the requests now in the document of the ops
        edited in place (bound to their ACTIVE step, as ``edit_in_place`` binds them); the adopted manual features.
        Dropped on purpose: the verifier's per-op states (``_verifier_states``) and the analytic model it built --
        ``verifier`` is fresh, so checks it can no longer derive are reported instead of asserted, the same downgrade
        as after an in-place edit; the pending frames/datums of an op in flight (there is none) and the last undo's
        bookkeeping. ``undo_floor`` becomes the number of steps in ``trace``: those ran in an earlier session and have
        no undo journal in the reopened document."""
        if state not in (ExecutorState.paused, ExecutorState.completed, ExecutorState.failed, ExecutorState.running):
            msg = f"cannot reattach an executor in state {state.value}"
            raise ExecutorStateError(msg)
        if not 0 <= cursor <= len(plan.operations):
            msg = f"cursor {cursor} is outside the plan ({len(plan.operations)} operations)"
            raise ExecutorStateError(msg)
        executor = cls(plan, adapter, verifier, config, documents=context.documents)
        executor._ctx = context
        executor._resolver = Resolver(adapter, context, config.thresholds)
        executor._steps = list(trace.steps)
        executor._health = list(trace.health)
        executor._reverts = list(trace.reverts)
        executor._edits = list(trace.edits)
        executor._last_mass = trace.final_mass
        executor._last_bbox = trace.final_bbox
        executor.cursor = cursor
        executor.state = ExecutorState.paused if state is ExecutorState.running else state
        if executor.state is ExecutorState.paused and cursor == len(plan.operations):
            executor.state = ExecutorState.completed
        executor._tail_reverted = reverted_cursor is not None
        executor._reverted_cursor = reverted_cursor
        active = {step.op_id: step for step in trace.active_steps()}
        for op_id, request in (edited_requests or {}).items():
            step = active.get(op_id)
            if step is not None:
                executor._edited_requests[op_id] = (step, dict(request))
        executor._external_features = dict(external_features or {})
        executor._undo_floor = len(executor._steps)
        executor._drifted_ops = frozenset(drifted_ops)
        return executor

    @property
    def drifted_ops(self) -> frozenset[str]:
        """Ops whose geometry was found changed outside the harness when the document was reopened: ``edit_in_place``
        refuses them (a rebuild is the way to change them)."""
        return self._drifted_ops

    @property
    def undo_floor(self) -> int:
        """How many recorded steps ran before the last ``reattach`` (0: never reattached): ``undo_to`` refuses to
        revert any of them (``UndoFloorError``)."""
        return self._undo_floor

    @property
    def documents(self) -> Mapping[str, DocumentHandle]:
        """The part documents ``insert_component`` can take, by the name a plan gives them."""
        return dict(self._documents)

    def bind_documents(self, documents: Mapping[str, DocumentHandle], document: DocumentHandle | None = None) -> None:
        """Point the part names (and, when the adapter gave the document a new id, the document itself) at the handles
        the adapter holds NOW: a reopened document and the parts it uses come back under new ids."""
        self.invalidate_measure()
        self._documents.clear()
        self._documents.update(documents)
        if self._ctx is not None:
            self._ctx.documents = self._documents
            if document is not None:
                self._ctx.document = document

    # --- public API (5.1) ---------------------------------------------------------------------------

    def start(self) -> None:
        if self.state != ExecutorState.idle:
            msg = f"start() called in state {self.state.value}"
            raise ExecutorStateError(msg)
        if self.config.canary_before_plan and not self.check_health("before_plan").healthy:
            self.state = ExecutorState.degraded
            return
        info = self.adapter.new_document(
            NewDocumentRequest(operation_id=f"{self.plan.id}.new_document", kind=self.plan.kind, name=self.plan.id)
        )
        self._ctx = ExecutionContext(document=info.handle, documents=self._documents)
        self._resolver = Resolver(self.adapter, self._ctx, self.config.thresholds)
        self.state = ExecutorState.running if self.plan.operations else ExecutorState.completed

    def next_operation(self) -> StepRecord:
        """Run the next op. The verifier records what it expects of an op BEFORE the adapter runs it, so the state it
        keeps (which bodies exist, which were consumed, what was edited) is saved first: an op that does not end
        successfully hands it back at once, and ``undo_to`` restores the state saved before the first op it undoes."""
        if self.state not in (ExecutorState.running, ExecutorState.paused):
            msg = f"next_operation() called in state {self.state.value}"
            raise ExecutorStateError(msg)
        self._step_saved = None
        try:
            record = self._run_step()
        except BaseException:
            # an op that never reached a verdict (an adapter crash, a cancellation) leaves no trace in the verifier
            saved, self._step_saved = self._step_saved, None
            if saved is not None:
                self.verifier.restore(saved)
            raise
        saved, self._step_saved = self._step_saved, None
        if saved is not None:
            if record.status is StepStatus.success:
                self._verifier_states[record.op_id] = saved
            else:
                self.verifier.restore(saved)
        return record

    def _run_step(self) -> StepRecord:
        ctx, resolver = self._require_started()
        op = self.plan.operations[self.cursor]
        started = _now()
        attempts: list[AttemptRecord] = []
        self._step_generation = self._model_generation
        resolver.begin_step()
        resolver.begin_reads()  # what the references and the expectations read is read once; closed before the call
        try:
            return self._run_step_reads_open(ctx, resolver, op, started, attempts)
        finally:
            resolver.end_reads()

    def _run_step_reads_open(
        self, ctx: ExecutionContext, resolver: Resolver, op: Any, started: datetime, attempts: list[AttemptRecord]
    ) -> StepRecord:

        # The part is measured only once the step is known to be able to run: a reference that does not resolve
        # refuses it with no measure at all (and the measure the last step left is reused when nothing changed it).
        try:
            request = build_request(op, ctx, resolver, self.extensions())
        except ResolutionError as exc:
            self.state = ExecutorState.failed
            return self._finish(op, StepStatus.plan_error, attempts, started, self._known_mass(op), None, (), str(exc))
        except Exception as exc:  # request building failures must not crash run_to_end
            self.state = ExecutorState.failed
            message = f"expectation computation failed: {type(exc).__name__}: {exc}"
            return self._finish(op, StepStatus.plan_error, attempts, started, self._known_mass(op), None, (), message)
        mass_before, bbox_before = self._measure_before(op)

        try:
            snapshot = self._snapshot(op, request, mass_before, bbox_before)
            self._step_saved = self.verifier.snapshot()
            expectations = self.verifier.expected(op, request, snapshot, resolver.adapter)
        except ResolutionError as exc:
            self.state = ExecutorState.failed
            return self._finish(op, StepStatus.plan_error, attempts, started, mass_before, None, (), str(exc))
        except Exception as exc:  # request building, snapshot or expectation failures must not crash run_to_end
            self.state = ExecutorState.failed
            message = f"expectation computation failed: {type(exc).__name__}: {exc}"
            return self._finish(op, StepStatus.plan_error, attempts, started, mass_before, None, (), message)

        requires_feature = self._requires_feature(op)
        flipped = False
        repairs = 0
        resolver.end_reads()  # the call changes the part: nothing read so far holds for what comes after it
        for number in range(1, self.config.max_retries + 2):
            clock = perf_counter()
            result, error, raw = self._call(op, request)
            duration_ms = (perf_counter() - clock) * 1000.0
            refused = result is None and error is not None and error.code in CLEAN_REFUSALS
            if not refused:  # the adapter may have changed the part: what the last step measured is no longer known
                self._fresh = None
            level1 = self.verifier.level1(result, error, requires_feature=requires_feature)
            request_dump = request.model_dump(mode="json")

            if not level1.ok:
                undone = self._undo_if_feature(op, result)
                if self.state is ExecutorState.degraded:  # the cleanup revert failed: no retry on top of it
                    attempts.append(
                        AttemptRecord(
                            number=number,
                            request=request_dump,
                            result=result,
                            error=error,
                            level1=level1,
                            flipped=flipped,
                            duration_ms=duration_ms,
                        )
                    )
                    return self._cleanup_failed_step(op, attempts, started, mass_before, expectations)
                diagnosis: Diagnosis | None = None
                repair_applied: RepairAction | None = None
                # Diagnose and, within the bounded
                # per-operation repair budget, attempt a repair that only ever mutates the NEXT attempt's
                # request -- never the document, never an operation this plan did not build (see
                # diagnosis.py's own guardrail and Executor._apply_repair below).
                if self.config.repair_on_failure and repairs < self.config.max_repairs_per_operation:
                    diagnosis = diagnose(
                        op,
                        error=error,
                        result=result,
                        adapter=self.adapter,
                        resolver=resolver,
                        external_features=frozenset(self._external_features),
                    )
                    if diagnosis.repairable:
                        request, repair_applied = self._apply_repair(op, request, diagnosis)
                        if repair_applied is not None:
                            repairs += 1
                attempts.append(
                    AttemptRecord(
                        number=number,
                        request=request_dump,
                        result=result,
                        error=error,
                        level1=level1,
                        undone=undone,
                        flipped=flipped,
                        duration_ms=duration_ms,
                        diagnosis=diagnosis,
                        repair_applied=repair_applied,
                    )
                )
                # an adapter that refused the request answered, so it is alive and the part is as it was: no canary
                if self.config.canary_after_failure and not refused and not self.check_health("after_failure").healthy:
                    self.state = ExecutorState.degraded
                    return self._finish(op, StepStatus.failed, attempts, started, mass_before, None, expectations)
                # A repair that was actually applied earns this attempt one more try even when the
                # triggering error is not normally recoverable (``invalid_reference`` is not, by default
                # -- contract/errors.py -- yet a re-resolved dangling reference must still get retried).
                # It never grows the retry budget: `number <= max_retries` still gates it exactly as
                # before, so a repair consumes a retry, it does not grant an extra one.
                if number <= self.config.max_retries and (
                    repair_applied is not None or error is None or error.recoverable
                ):
                    continue
                self.state = ExecutorState.failed
                return self._finish(op, StepStatus.failed, attempts, started, mass_before, None, expectations)

            assert result is not None
            observation = gather_observation(
                self.adapter,
                op,
                result,
                snapshot,
                request,
                raw_result=raw,
                check_names=frozenset(e.name for e in expectations),
            )
            level2 = self.verifier.level2(expectations, observation)
            attempts.append(
                AttemptRecord(
                    number=number,
                    request=request_dump,
                    result=result,
                    error=None,
                    level1=level1,
                    level2=level2,
                    flipped=flipped,
                    duration_ms=duration_ms,
                )
            )
            if all(check.ok for check in level2 if check.kind == CheckKind.asserted):
                self._register(op, request, result, observation)
                return self._succeed(op, attempts, started, mass_before, observation, expectations)

            if self._flip_applies(op, request, level2, attempts):
                if self._undo_if_feature(op, result):
                    attempts[-1] = attempts[-1].model_copy(update={"undone": True})
                elif self.state is ExecutorState.degraded:
                    return self._cleanup_failed_step(op, attempts, started, mass_before, expectations)
                request = request.model_copy(update={"flip": not getattr(request, "flip", False)})
                flipped = True
                continue
            if self.config.stop_on_verification_failure:
                if self._undo_if_feature(op, result):
                    attempts[-1] = attempts[-1].model_copy(update={"undone": True})
                elif self.state is ExecutorState.degraded:
                    return self._cleanup_failed_step(op, attempts, started, mass_before, expectations)
                self.state = ExecutorState.failed
                return self._finish(op, StepStatus.failed, attempts, started, mass_before, None, expectations)
            self._register(op, request, result, observation)  # warn-only mode
            return self._succeed(op, attempts, started, mass_before, observation, expectations)

        self.state = ExecutorState.failed
        return self._finish(op, StepStatus.failed, attempts, started, mass_before, None, expectations)

    def run_to_end(self) -> ExecutionTrace:
        while self.state == ExecutorState.running:
            self.next_operation()
        return self.trace()

    def pause(self) -> None:
        """Pause after the current op completes (never mid-op); a no-op if already paused."""
        self._pause_requested = True

    def pause_now(self) -> None:
        """Pause between ops: ``running`` -> ``paused`` immediately.

        Only a caller that is not inside ``next_operation`` can know that no op is in flight (the executor
        is synchronous), which is why this is separate from ``pause()``; ``ExecutorStateError`` otherwise.
        """
        if self.state is not ExecutorState.running:
            msg = f"pause_now() called in state {self.state.value}"
            raise ExecutorStateError(msg)
        self._pause_requested = True
        self.state = ExecutorState.paused

    def resume(self) -> None:
        self._pause_requested = False
        if self.state == ExecutorState.paused:
            self.state = ExecutorState.running

    def extend(
        self, operations: Sequence[Any], *, external_features: Mapping[str, FeatureHandle] | None = None
    ) -> None:
        """Append ``operations`` (dicts or operation models) to the plan and re-validate it (V1-V14).

        Allowed from ``paused`` or ``completed``. The new plan is
        rebuilt through ``model_validate`` — never ``model_copy`` — so a duplicate id or a forward
        reference raises ``PlanError`` and leaves the executor untouched. The trace keeps ``plan_id``
        and simply gains steps; the state becomes ``running`` when there is work left.

        A plain ``undo_to`` does not remove the reverted ops from
        ``self.plan.operations`` — they stay queued after the cursor so a caller who does nothing but
        ``resume``/``next_operation`` again redoes them unchanged (this is intentional and tested:
        ``test_undo_to_then_resume_re_runs_the_ops_and_active_steps_follows``). But that same stale tail
        must not silently run again ahead of a correction: when ``undo_to`` has left one (``_tail_reverted``
        is set, cleared here), ``extend`` DISCARDS it — ``operations[:cursor]`` — and appends the new ops
        in its place, instead of appending after it. In the common case (no undo since the last extend)
        this is a no-op: ``cursor`` already equals ``len(operations)`` (paused with nothing left) or sits at
        the original plan's own next-to-run op, whose tail was never reverted and is kept in full.

        ``external_features`` adopts features the engineer made by hand in the CAD under alias op ids
        (``ir.plan.EXTERNAL_IDS_CONTEXT``): each alias is registered in the execution context as if an
        op with that id had produced the feature, so the appended ops may reference it by provenance
        (``from_op``). An alias that collides with a plan op id — an existing one or one appended in this
        same call (two operations of the same batch may share an id even though neither is in the
        plan yet) — is a ``PlanError`` (V1). Aliases already adopted by an earlier ``extend`` stay valid.
        """
        if self.state not in (ExecutorState.paused, ExecutorState.completed):
            msg = f"extend() called in state {self.state.value}"
            raise ExecutorStateError(msg)
        ctx, _ = self._require_started()
        adopted = dict(external_features or {})
        if adopted:
            self.invalidate_measure()
        appended = [op.model_dump(mode="python") if hasattr(op, "model_dump") else dict(op) for op in operations]
        data = self.plan.model_dump(mode="python")
        existing = data["operations"][: self.cursor] if self._tail_reverted else data["operations"]
        plan_ids = {str(op["id"]) for op in existing} | {
            str(op["id"]) for op in appended if isinstance(op, dict) and op.get("id") is not None
        }
        for alias in adopted:
            if alias in plan_ids:
                msg = f"manual feature alias collides with operation id: {alias!r}"
                raise PlanError(self.plan.id, alias, "V1", msg)
        data["operations"] = [*existing, *appended]
        external = set(self._external_features) | set(adopted)
        self.plan = parse_plan(data, external_ids=external)
        self._external_features.update(adopted)
        ctx.feature_by_op.update(adopted)
        self._tail_reverted = False
        self._reverted_cursor = None
        self._pause_requested = False
        self.state = ExecutorState.running if self.cursor < len(self.plan.operations) else ExecutorState.completed

    def probe_references(
        self, operations: Sequence[Any], *, external_features: Mapping[str, FeatureHandle] | None = None
    ) -> dict[str, str]:
        """Resolve, without executing anything, every face/edge/vertex reference of ``operations`` whose
        provenance (``from_op``) is already executed (or an adopted manual feature), so an ambiguous or empty
        selector surfaces as ``ResolutionError`` — with its candidate list — BEFORE the operations are
        appended (``extend``) instead of failing the session at ``next_operation``. References to operations
        not executed yet (including ones in the same batch) are skipped; operations that do not parse are
        skipped too (``extend`` reports them as ``PlanError``). Returns ``"<op id>.<path>" -> handle id`` for
        every reference it resolved. Read-only: the executor's own resolver and context are untouched."""
        ctx, _ = self._require_started()
        probe_ctx = ExecutionContext(document=ctx.document, documents=ctx.documents)
        probe_ctx.feature_by_op = {**ctx.feature_by_op, **dict(external_features or {})}
        probe_ctx.body_by_op = dict(ctx.body_by_op)
        resolver = Resolver(self.adapter, probe_ctx, self.config.thresholds)
        resolved: dict[str, str] = {}
        for raw in operations:
            try:
                op = raw if hasattr(raw, "refs") else _OPERATION_ADAPTER.validate_python(raw)
            except ValueError:
                continue
            for path, ref in _named_refs(op):
                if isinstance(ref, FaceRef) and _face_probeable(ref, probe_ctx):
                    resolved[f"{op.id}.{path}"] = resolver.face(ref, op.id, path).handle.id
                elif isinstance(ref, EdgeRef) and _edge_probeable(ref, probe_ctx):
                    resolved[f"{op.id}.{path}"] = resolver.edge(ref, op.id, path).handle.id
                elif isinstance(ref, VertexRef) and _vertex_probeable(ref, probe_ctx):
                    resolved[f"{op.id}.{path}"] = resolver.vertex(ref, op.id, path).handle.id
        return resolved

    def resolve_display_refs(
        self, refs: Sequence[FaceRef | EdgeRef], *, op_id: str = "show_entities"
    ) -> list[FaceInfo | EdgeInfo | ResolutionError]:
        """Resolve each of ``refs`` against the session as it stands now, read-only: the same probe-context/``Resolver``
        pattern as ``probe_references`` above, except each ref is resolved independently -- a ``ResolutionError`` (0 or
        >1 candidates) is caught and returned in place rather than aborting the batch, since the caller reports it as a
        candidates list for that one ref instead of failing the whole call. Order matches ``refs``; never mutates the
        executor's own resolver/context."""
        ctx, _ = self._require_started()
        probe_ctx = ExecutionContext(document=ctx.document, documents=ctx.documents)
        probe_ctx.feature_by_op = dict(ctx.feature_by_op)
        probe_ctx.body_by_op = dict(ctx.body_by_op)
        resolver = Resolver(self.adapter, probe_ctx, self.config.thresholds)
        results: list[FaceInfo | EdgeInfo | ResolutionError] = []
        for index, ref in enumerate(refs):
            path = f"refs[{index}]"
            try:
                if isinstance(ref, FaceRef):
                    results.append(resolver.face(ref, op_id, path))
                else:
                    results.append(resolver.edge(ref, op_id, path))
            except ResolutionError as exc:
                results.append(exc)
        return results

    def preview_edge_set(self, ref: EdgeSet, *, op_id: str = "edges") -> list[EdgeInfo]:
        """The edges ``ref`` resolves to against the session as it stands now, read-only: the same probe-context pattern
        as ``resolve_display_refs``. A set that matches nothing raises ``ResolutionError`` (its message says what each
        criterion left)."""
        ctx, _ = self._require_started()
        probe_ctx = ExecutionContext(document=ctx.document, documents=ctx.documents)
        probe_ctx.feature_by_op = dict(ctx.feature_by_op)
        probe_ctx.body_by_op = dict(ctx.body_by_op)
        resolver = Resolver(self.adapter, probe_ctx, self.config.thresholds)
        return resolver.edge_set(ref, op_id, "edges")

    def sketch_frame_for(self, on: PlaneRef | FaceRef, *, op_id: str = "sketch") -> SketchFrame:
        """The frame a sketch on ``on`` (a base plane, a datum plane or a planar face) would get, resolved against the
        session as it stands now, read-only. ``ResolutionError`` when the reference does not resolve to a plane."""
        ctx, _ = self._require_started()
        probe_ctx = ExecutionContext(document=ctx.document, documents=ctx.documents)
        probe_ctx.feature_by_op = dict(ctx.feature_by_op)
        probe_ctx.body_by_op = dict(ctx.body_by_op)
        probe_ctx.datum_plane_by_op = dict(ctx.datum_plane_by_op)
        return Resolver(self.adapter, probe_ctx, self.config.thresholds).plane_frame(on, op_id, "on")

    def feature_for(self, op_id: str) -> FeatureHandle | None:
        """The feature an executed op (or an adopted manual alias) produced, by provenance; ``None`` otherwise."""
        return self._ctx.feature_by_op.get(op_id) if self._ctx is not None else None

    def sketch_for(self, op_id: str) -> SketchHandle | None:
        """The sketch an executed ``sketch`` op produced, by provenance; ``None`` otherwise."""
        return self._ctx.sketch_by_op.get(op_id) if self._ctx is not None else None

    def component_for(self, op_id: str) -> ComponentHandle | None:
        """The component an executed ``insert_component`` op produced, by provenance; ``None`` otherwise.

        The sibling of ``feature_for``/``sketch_for`` for the third handle kind ``ExecutionContext``
        already keeps (``component_by_op``). A caller needs it to ask the adapter's ``ASSEMBLIES`` queries for each
        component's bounding box and transform.
        """
        return self._ctx.component_by_op.get(op_id) if self._ctx is not None else None

    def joint_for(self, op_id: str) -> JointHandle | None:
        """The joint an executed ``add_joint`` op made, by provenance; ``None`` otherwise."""
        return self._ctx.joint_by_op.get(op_id) if self._ctx is not None else None

    @property
    def tail_reverted(self) -> bool:
        """True while the op AT the cursor is a reverted one awaiting a redo or a replacement: an ``undo_to`` left it
        there and nothing has moved the cursor since. A plain redo of that op moves the cursor, and the ops further
        down the stale tail are then no longer "pending revert" (they never ran in this position); ``extend`` still
        replaces them."""
        return self._tail_reverted and self._reverted_cursor == self.cursor

    @property
    def reverted_cursor(self) -> int | None:
        """The cursor the last ``undo_to`` that reverted something left (``None``: none, or ``extend`` consumed it)."""
        return self._reverted_cursor if self._tail_reverted else None

    @property
    def context(self) -> ExecutionContext | None:
        """The live execution context (handles by op id), ``None`` before ``start``. Read-only use: a checkpoint dumps
        it; nothing outside the executor may mutate it."""
        return self._ctx

    @property
    def edited_requests(self) -> dict[str, dict[str, Any]]:
        """Op id -> the request now in the document, for ops edited in place whose step is still the active one."""
        active = {s.op_id: s for s in self.trace().active_steps()}
        return {op_id: request for op_id, (step, request) in self._edited_requests.items() if active.get(op_id) is step}

    def recorded_selection_origins(self, op_id: str) -> dict[str, str | None]:
        """For an op that ran (a successful, non-reverted step), ``token -> id of the op whose result created the entity
        that token named when the op ran`` (``None``: no recorded result lists it) for every ``from_selection`` ref it
        carries. Read from what the step recorded -- ``StepRecord.resolved_refs`` (the handle each ref resolved to)
        and the ``created_faces``/``created_edges`` of the active steps -- never from the document as it stands now:
        an op that CONSUMED its selected entity (a fillet on a picked edge) made the token stale on the live document,
        yet the entity (and its maker) are still known here."""
        active = active_steps_of(self._steps, self._reverts)
        step = next((s for s in reversed(active) if s.op_id == op_id and s.status is StepStatus.success), None)
        op = next((o for o in self.plan.operations if o.id == op_id), None)
        if step is None or op is None:
            return {}
        made_by: dict[str, str] = {}
        inserted_by: dict[str, str] = {}  # component id -> the insert that made it (its faces are not listed)
        for other in active:
            result = other.attempts[-1].result if other.attempts else None
            if other.status is not StepStatus.success or result is None:
                continue
            for handle in (*result.created_faces, *result.created_edges):
                made_by.setdefault(handle.id, other.op_id)
            if other.op == "insert_component" and result.component is not None:
                inserted_by.setdefault(result.component.id, other.op_id)
        origins: dict[str, str | None] = {}
        for path, ref in _named_refs(op):
            token = getattr(ref, "from_selection", None)
            if token is not None and path in step.resolved_refs:
                handle_id = step.resolved_refs[path]
                origins[token] = made_by.get(handle_id) or inserted_by.get(handle_id.partition("/")[0])
        return origins

    @property
    def external_features(self) -> Mapping[str, FeatureHandle]:
        """Alias op id -> manual feature adopted through ``extend(external_features=...)``."""
        return dict(self._external_features)

    def abandon_failed(self) -> StepRecord | None:
        """Leave the ``failed`` state by dropping the operation that failed (and anything queued after it).

        The failed step's record stays in the trace as evidence and its feature, if any, was already undone by
        ``next_operation``; only the queued operations go, so a caller can ``extend`` with a corrected one instead of
        being stuck behind a step that will never succeed. Returns that record (``None`` when the failure happened
        before any step was recorded). ``ExecutorStateError`` unless ``failed``."""
        if self.state is not ExecutorState.failed:
            msg = f"abandon_failed() called in state {self.state.value}"
            raise ExecutorStateError(msg)
        failed = self._steps[-1] if self._steps and self._steps[-1].status is not StepStatus.success else None
        dropped = self.plan.operations[self.cursor :]
        data = self.plan.model_dump(mode="python")
        data["operations"] = data["operations"][: self.cursor]
        self.plan = parse_plan(data, external_ids=set(self._external_features))
        for op in dropped:
            self._pending_frames.pop(op.id, None)
            self._pending_datums.pop(op.id, None)
        self._tail_reverted = False
        self._reverted_cursor = None
        self._pause_requested = False
        self.state = ExecutorState.completed if self.cursor >= len(self.plan.operations) else ExecutorState.paused
        return failed

    def undo_to(self, op_id: str | None, *, by: str = "executor", reason: str = "") -> tuple[str, ...]:
        """Revert every step recorded after the successful step ``op_id``, newest first. ``op_id=None`` reverts every
        step that can be reverted: all of them, or all that ran after the last ``reattach`` (the undo floor).

        Allowed from ``paused``, ``completed`` or ``failed``. For each reverted successful step whose
        last attempt produced a feature, one ``adapter.undo_last`` call must report exactly that
        handle; a mismatch means the adapter and the executor disagree on history, the executor goes
        ``degraded`` and ``ExecutorStateError`` is raised. Context registrations of the reverted ops are
        dropped, the cursor moves back to the op after ``op_id``, a ``RevertRecord`` is appended and the
        state becomes ``paused``. Reverted ``StepRecord``s stay in the trace (see ``RevertRecord``), AND
        the reverted ops themselves stay in ``self.plan.operations`` (never truncated here) — a caller who
        just ``resume``s redoes them exactly as before, unchanged. If instead the caller corrects the plan
        with ``extend`` before redoing anything, ``extend`` discards this stale tail and replaces it with
        the new operations (see ``extend``'s docstring) — the two tools together give "replace what
        I undid" without the caller ever slicing ``plan.operations`` themselves.

        Only steps whose last attempt produced a feature call ``adapter.undo_last``: a reverted ``sketch`` (or datum)
        has no feature to undo. On the FreeCAD adapter that leaves nothing behind -- a sketch is a registry entry
        that becomes a CAD object only when the feature consuming it is built, and that object goes with the
        feature's own undo -- so re-running the sketch op creates a fresh one and the old record is unreachable
        (``tests/integration/freecad/test_undo_depth.py``). An adapter whose sketches are real tree objects
        would need ``undo_last`` to take them along.
        """
        self._partial_revert = ()
        self._last_undo_warnings = ()
        self.invalidate_measure()
        if self.state not in (ExecutorState.paused, ExecutorState.completed, ExecutorState.failed):
            msg = f"undo_to() called in state {self.state.value}"
            raise ExecutorStateError(msg)
        ctx, _ = self._require_started()
        active = self.trace().active_steps()
        before_floor = {id(step) for step in self._steps[: self._undo_floor]}
        target: StepRecord | None
        if op_id is None:
            below_floor = [s for s in active if id(s) in before_floor]
            target = below_floor[-1] if below_floor else None
        else:
            target = next((s for s in reversed(active) if s.op_id == op_id and s.status is StepStatus.success), None)
            if target is None:
                msg = f"undo_to({op_id!r}): no successful, non-reverted step with that id"
                raise ExecutorStateError(msg)
        after = active[active.index(target) + 1 :] if target is not None else active

        # only a reverted FEATURE needs the CAD's undo journal; a failed step or a sketch/datum record is bookkeeping
        # (review finding: a resumed failed session must be able to undo its failed step)
        def journaled(step: StepRecord) -> bool:
            return id(step) in before_floor and step.status is StepStatus.success and self._adapter_undoes(step)

        below = [step for step in after if journaled(step)]
        if below:
            # These steps ran before the document was reopened; the CAD keeps no undo journal for them. Refused
            # BEFORE any adapter call: nothing is reverted, the executor is untouched.
            floor_op = next(s.op_id for s in reversed(active) if journaled(s))
            raise UndoFloorError(
                f"undo_to({op_id!r}) would revert {', '.join(repr(s.op_id) for s in below)}, which ran before this "
                f"session was resumed: the undo floor is {floor_op!r} (the last op before the resume)",
                floor_op=floor_op,
                below=tuple(s.op_id for s in below),
            )
        reverted: list[str] = []
        warnings: list[str] = []
        for step in reversed(after):
            feature = _feature_of(step)
            if step.status is StepStatus.success and self._adapter_undoes(step):
                try:
                    undo = self._undo_call(step.op, feature)(Empty(operation_id=step.op_id))
                except AdapterError as exc:
                    # The adapter could not take this op back. What was reverted before it stays reverted and is
                    # recorded as such, whatever the error. ONLY ``rebuild_failed`` (the FreeCAD worker rolls a failed
                    # revert back, so its document and registry are as before the attempt) leaves the executor paused
                    # at the last reverted op, a retry of undo_to finishes the job, and the error propagates as it is.
                    # Anything else (a timeout kills the worker, ``internal``, a worker that flags itself degraded)
                    # leaves the CAD in an unknown state: degraded.
                    intact = _revert_left_cad_intact(exc.info)
                    self._settle_partial_revert(
                        after, reverted, warnings, anchor=step, by=by, reason=reason, degrade=not intact
                    )
                    if intact:
                        raise
                    msg = (
                        f"adapter could not revert op {step.op_id!r} ({exc.info.code.value}: {exc.info.message}); "
                        "the ops reverted before it stay reverted, session degraded"
                    )
                    raise UndoInterruptedError(
                        msg, failed_op=step.op_id, info=exc.info, reverted=tuple(reverted)
                    ) from exc
                warnings.extend(undo.warnings)
                if undo.reverted != feature:
                    # the op the adapter reverted is not the one it was asked about: history disagrees. Book what
                    # went before, then degrade through the same error as any failed revert.
                    self._settle_partial_revert(
                        after, reverted, warnings, anchor=step, by=by, reason=reason, degrade=True
                    )
                    msg = (
                        f"undo_to({op_id!r}): adapter reverted {undo.reverted!r} while the executor expected "
                        f"{feature!r} for op {step.op_id!r}; history disagrees, session degraded"
                    )
                    raise UndoInterruptedError(msg, failed_op=step.op_id, info=None, reverted=tuple(reverted))
            for registry in (
                ctx.feature_by_op,
                ctx.sketch_by_op,
                ctx.sketch_frame_by_op,
                ctx.sketch_entities_by_op,
                ctx.body_by_op,
                ctx.body_sides,
                ctx.component_by_op,
                ctx.joint_by_op,
                ctx.datum_plane_by_op,
                ctx.datum_axis_by_op,
                ctx.place_by_op,
            ):
                registry.pop(step.op_id, None)
            self._pending_frames.pop(step.op_id, None)
            self._pending_datums.pop(step.op_id, None)
            reverted.append(step.op_id)
        # the verifier forgets what the undone ops taught it: back to the state saved before the first of them
        first_undone = next((s.op_id for s in after if s.op_id in self._verifier_states), None)
        if first_undone is not None:
            self.verifier.restore(self._verifier_states[first_undone])
        for step in after:
            self._verifier_states.pop(step.op_id, None)
        self.cursor = self._cursor_after(target) if target is not None else 0
        # the reverts HAPPENED: record them before the measurements below, which can fail (and then say so)
        self._partial_revert = tuple(reverted)
        self._reverts.append(
            RevertRecord(op_ids=tuple(reverted), by=by, reason=reason, at=_now(), warnings=tuple(warnings))
        )
        self._last_undo_warnings = tuple(warnings)
        if reverted:
            # operations[cursor:] are now stale (reverted) steps kept only so a plain redo can
            # re-run them unchanged; the next extend() must replace them instead of appending after them.
            self._tail_reverted = True
            self._reverted_cursor = self.cursor
        self._pause_requested = False
        self.state = ExecutorState.paused
        try:
            measure_id = op_id or "undo_to"
            mass = target.mass_after if target is not None else None
            self._last_mass = mass or self.adapter.mass_properties(MassRequest(operation_id=measure_id))
            self._last_bbox = self.adapter.bounding_box(MassRequest(operation_id=measure_id))
        except AdapterError as exc:
            self.state = ExecutorState.degraded
            done = ", ".join(repr(r) for r in reverted) or "nothing"
            msg = (
                f"undo_to({op_id!r}) reverted {done} but could not measure the part afterwards "
                f"({exc.info.code.value}: {exc.info.message}); session degraded"
            )
            raise UndoInterruptedError(
                msg, failed_op=op_id or "undo_to", info=exc.info, reverted=tuple(reverted), stage="measure"
            ) from exc
        self._partial_revert = ()  # it did not raise
        return tuple(reverted)

    def _cursor_after(self, step: StepRecord) -> int:
        """The plan cursor that has ``step`` done and nothing after it: one past its operation, or -- for a step run
        outside the plan (``record_external_step``) -- the number of operations that were done when it ran."""
        if step.anchor is not None:
            return step.anchor
        positions = {op.id: index for index, op in enumerate(self.plan.operations)}
        return positions[step.op_id] + 1

    def record_external_step(
        self,
        op_id: str,
        kind: str,
        *,
        feature: FeatureHandle | None,
        mass_before: MassProperties,
        mass_after: MassProperties,
        note: str = "",
    ) -> StepRecord:
        """Record a step the CAD already ran OUTSIDE the plan (a native fragment): it has no operation, no references to
        resolve and nothing to verify beyond what the caller measured, but it is a step of the history -- ``undo_to``
        reverts it (one ``undo_last`` when it left a ``feature``) in its place between the plan operations, and the
        steps recorded after a ``reattach`` count it below the undo floor like any other.

        ``op_id`` must be new (neither a step nor a plan operation); allowed once started and not ``failed`` or
        ``degraded`` (between two operations of a plan, or after its last)."""
        if self.state not in (ExecutorState.running, ExecutorState.paused, ExecutorState.completed):
            msg = f"record_external_step() called in state {self.state.value}"
            raise ExecutorStateError(msg)
        self.invalidate_measure()
        self._require_started()
        if any(step.op_id == op_id for step in self._steps) or any(op.id == op_id for op in self.plan.operations):
            msg = f"record_external_step({op_id!r}): that id is already a step or an operation of the plan"
            raise ExecutorStateError(msg)
        now = _now()
        attempt = AttemptRecord(
            number=1,
            request={"external": kind, "note": note},
            result=OperationResult(operation_id=op_id, feature=feature, rebuild_ok=True),
            error=None,
            level1=CheckResult(name="external_step", kind=CheckKind.reported, ok=True, expected=None, observed=None),
            duration_ms=0.0,
        )
        record = StepRecord(
            op_id=op_id,
            op=kind,
            status=StepStatus.success,
            attempts=(attempt,),
            mass_before=mass_before,
            mass_after=mass_after,
            started_at=now,
            finished_at=now,
            anchor=self.cursor,
        )
        self._steps.append(record)
        self._last_mass = mass_after
        return record

    @property
    def partial_revert(self) -> tuple[str, ...]:
        """The ops the LAST ``undo_to`` reverted before it raised (empty when it did not raise, or reverted nothing)."""
        return self._partial_revert

    @property
    def last_undo_warnings(self) -> tuple[str, ...]:
        """What the adapter reported not honouring of hand work in the last ``undo_to`` (``UndoResult.warnings``)."""
        return self._last_undo_warnings

    def _settle_partial_revert(
        self,
        after: list[StepRecord],
        reverted: list[str],
        warnings: list[str],
        *,
        anchor: StepRecord,
        by: str,
        reason: str,
        degrade: bool,
    ) -> None:
        """Book-keeping for an ``undo_to`` that stopped part-way: the ops it reverted are reverted (verifier state,
        cursor at the oldest of them, one ``RevertRecord``) and the executor is paused at that point -- or
        ``degraded``. Nothing is recorded when nothing was reverted."""
        self._partial_revert = tuple(reverted)
        self._last_undo_warnings = tuple(warnings)
        if reverted:
            done = [s for s in after if s.op_id in set(reverted)]
            first_undone = next((s.op_id for s in done if s.op_id in self._verifier_states), None)
            if first_undone is not None:
                self.verifier.restore(self._verifier_states[first_undone])
            for step in done:
                self._verifier_states.pop(step.op_id, None)
            self.cursor = self._cursor_after(done[0]) - (1 if done[0].anchor is None else 0)
            try:  # the part as it stands now: the anchor step's mass, the adapter's box (best effort when it is lost)
                self._last_mass = anchor.mass_after or self.adapter.mass_properties(
                    MassRequest(operation_id=anchor.op_id)
                )
                self._last_bbox = self.adapter.bounding_box(MassRequest(operation_id=anchor.op_id))
            except AdapterError:
                pass  # degrading anyway, or the next measurement will say so
            self._reverts.append(
                RevertRecord(op_ids=tuple(reverted), by=by, reason=reason, at=_now(), warnings=tuple(warnings))
            )
            self._tail_reverted = True
            self._reverted_cursor = self.cursor
            self._pause_requested = False
            self.state = ExecutorState.paused
        if degrade:
            self.state = ExecutorState.degraded

    # --- keeping the place of the ops a rebuild runs again -----------------------------------------

    def hold_places(self, op_ids: Sequence[str]) -> None:
        """Keep where ``op_ids`` stand (a hole or a sketch on a planar face) for the rebuild that is about to undo and
        run them again: a feature is placed from the centre of its face, and that centre moves with every hole in the
        face, so running the same request again would put it somewhere else. Call ``release_places`` when the rebuild
        is over. An op that is not on a planar face has nothing to keep."""
        ctx, _ = self._require_started()
        ctx.held_places = {i: ctx.place_by_op[i] for i in op_ids if i in ctx.place_by_op}
        self._moved_places = {}

    def release_places(self) -> dict[str, float]:
        """Stop keeping places; returns how far each op that could not stay where it was moved (op id -> mm)."""
        ctx, _ = self._require_started()
        ctx.held_places = {}
        moved, self._moved_places = self._moved_places, {}
        return moved

    def _note_move(self, op_id: str, now: SketchFrame) -> None:
        held = self._require_started()[0].held_places.get(op_id)
        if held is None:
            return
        offset = tuple(a - b for a, b in zip(now.origin, held.origin, strict=True))
        along = sum(o * n for o, n in zip(offset, held.n, strict=True))
        aside = math.sqrt(max(sum(o * o for o in offset) - along * along, 0.0))
        if aside > PLACE_TOL_MM:
            self._moved_places[op_id] = aside

    # --- in-place editing ---------------------------------------------------------

    def edit_in_place(
        self,
        new_plan: Plan,
        op_ids: Sequence[str],
        *,
        by: str = "executor",
        reason: str = "",
        plan_revision: int | None = None,
    ) -> EditRecord:
        """Change the ops ``op_ids`` of the plan to what ``new_plan`` says IN PLACE in the CAD
        (``Adapter.edit_feature``) instead of undoing and re-running everything after them.

        ``new_plan`` is the candidate the caller already validated (IR, tokens): it must carry the
        same operations in the same order, differing only in the named ops. The new requests are built with the same
        builders a run uses, sent as ONE batch, and every outcome is an ``EditRecord``:

        * ``refused``: the executor or the adapter says this cannot be done in place (nothing was touched; not
          recorded);
        * ``rolled_back``: the adapter's checks failed, or a reference of a later op resolves differently now (the
          edit is taken back; ``reference_drift`` names them): the document is as before; recorded;
        * ``unchanged``: the requests did not change (only fields the CAD never sees): the plan is adopted; recorded;
        * ``applied``: the plan is adopted, the part re-measured (``final_mass``/``final_bbox``), the analytic shapes
          the verifier tracks forgotten, and the dependents named in ``recomputed_ops`` are marked as recomputed, not
          re-verified per operation (their ``StepRecord`` stays as it was). Recorded.

        Allowed from ``paused`` or ``completed``; ``ExecutorStateError`` otherwise. The cursor does not move.
        ``plan_revision`` is the revision a standing edit creates (the caller numbers revisions); it is kept on the
        ``applied``/``unchanged`` records only.
        """
        if self.state not in (ExecutorState.paused, ExecutorState.completed):
            msg = f"edit_in_place() called in state {self.state.value}"
            raise ExecutorStateError(msg)
        self.invalidate_measure()
        ids = tuple(dict.fromkeys(op_ids))
        prepared = self._prepare_edit(new_plan, ids)
        changes: dict[str, tuple[str, ...]] = {}
        old_requests: dict[str, dict[str, Any]] = {}
        new_requests: dict[str, dict[str, Any]] = {}
        if not isinstance(prepared, str):
            changes = prepared[2]
            by_feature = {handle.id: op_id for op_id, handle in self._require_started()[0].feature_by_op.items()}
            old_requests = {by_feature[e.feature.id]: e.request.model_dump(mode="json") for e in prepared[1]}
            new_requests = {by_feature[e.feature.id]: e.request.model_dump(mode="json") for e in prepared[0]}

        def record(status: str, **fields: Any) -> EditRecord:
            return EditRecord(
                op_ids=ids,
                status=status,
                by=by,
                reason=reason,
                at=_now(),
                changes=changes,
                old_requests=old_requests,
                new_requests=new_requests,
                plan_revision=plan_revision if status in ("applied", "unchanged") else None,
                **fields,  # type: ignore[arg-type]
            )

        if isinstance(prepared, str):
            return record("refused", failure=prepared, failure_kind="technical")
        edits, old_edits, changes = prepared
        if not edits:
            self._adopt_edited_plan(new_plan, remeasure=False)
            done = record("unchanged")
            self._edits.append(done)
            return done
        refusal = self._edit_capability_refusal()
        if refusal is not None:
            return record("refused", failure=refusal, failure_kind="technical")
        operation_id = f"{self.plan.id}.edit"
        try:
            result = self.adapter.edit_feature(EditFeatureRequest(operation_id=operation_id, edits=tuple(edits)))
        except AdapterError as exc:
            if exc.info.code is ErrorCode.not_supported:
                return record("refused", failure=exc.info.message, failure_kind="technical")
            if exc.info.code in WORKER_GONE_CODES:
                self.state = ExecutorState.degraded  # nobody knows what state the document is in: no rebuild either
                raise
            # anything else (rebuild_failed, internal ...): the worker rolled the edit back before raising. An error
            # about the request or the geometry is about the VALUE (semantic); a lost session or a timeout is not
            kind = "semantic" if exc.info.code in _SEMANTIC_CODES else "technical"
            done = record("rolled_back", failure=f"{exc.info.code.value}: {exc.info.message}", failure_kind=kind)
            self._edits.append(done)
            return done
        if result.refused:
            return record("refused", result=result, failure=self._refusal_text(result), failure_kind="technical")
        if result.rebuild_ok and not result.applied and not result.dry_run and result.failure is None:
            # The adapter found nothing to change: the document already carries these values (set by hand, say). That
            # is a no-op to adopt, not a failure: rolling it back would make the caller rebuild the part for nothing.
            self._adopt_edited_plan(new_plan, remeasure=False, edits=edits)
            done = record("unchanged", result=result)
            self._edits.append(done)
            return done
        recomputed = self._ops_of(result.recomputed)
        if not result.rebuild_ok or not result.applied:
            done = record(
                "rolled_back",
                result=result,
                recomputed_ops=recomputed,
                failure=result.failure or "the edit failed",
                failure_kind=result.failure_kind
                or "semantic",  # an adapter that does not say is not trusted to fall back
            )
            self._edits.append(done)
            return done
        drift, follows, followed = self._reference_drift(new_plan, ids)
        if drift:
            self._take_edit_back(old_edits, operation_id)
            done = record(
                "rolled_back",
                result=result,
                recomputed_ops=recomputed,
                reference_drift=drift,
                failure="a reference of a later operation resolves differently after the edit: " + "; ".join(drift),
                failure_kind="technical",  # the topological naming problem, not the value
            )
            self._edits.append(done)
            return done
        if follows:
            new_plan = self._following_plan(new_plan, follows)
        self._adopt_edited_plan(new_plan, remeasure=True, edits=edits)
        done = record("applied", result=result, recomputed_ops=recomputed, followed=followed)
        self._edits.append(done)
        return done

    def _edit_capability_refusal(self) -> str | None:
        if "edit_feature" not in self.adapter.capabilities().tools:
            return f"adapter {self.adapter.name!r} cannot edit features in place"
        return None

    @staticmethod
    def _refusal_text(result: EditResult) -> str:
        return "; ".join(f"{feature}: {why}" for feature, why in result.refused.items())

    def _ops_of(self, features: Sequence[FeatureHandle]) -> tuple[str, ...]:
        ctx, _ = self._require_started()
        by_feature = {handle.id: op_id for op_id, handle in ctx.feature_by_op.items()}
        return tuple(by_feature[f.id] for f in features if f.id in by_feature)

    def _prepare_edit(
        self, new_plan: Plan, ids: tuple[str, ...]
    ) -> str | tuple[list[FeatureEdit], list[FeatureEdit], dict[str, tuple[str, ...]]]:
        """The refusal text, or ``(edits, the edits that put the old requests back, fields changed per op)``; ``edits``
        holds only the ops whose adapter request really changes. A ``sketch`` step has no feature of its own: its new
        dimensions travel with the features that consume it (``FeatureEdit.sketch``)."""
        ctx, resolver = self._require_started()
        if not ids:
            return "no operation to edit"
        old_by_id = {op.id: op for op in self.plan.operations}
        if [op.id for op in new_plan.operations] != [op.id for op in self.plan.operations]:
            msg = "the edited plan must carry the same operations in the same order"
            raise ExecutorStateError(msg)
        for op in new_plan.operations:
            if op.id not in ids and op != old_by_id[op.id]:
                msg = f"operation {op.id!r} differs in the edited plan but is not among the ops to edit"
                raise ExecutorStateError(msg)
        if self._tail_reverted:
            return "a revert is pending in this session: the part is not at the end of the executed ops"
        drifted = [op_id for op_id in ids if op_id in self._drifted_ops]
        if drifted:
            return (
                f"the geometry of {', '.join(repr(i) for i in drifted)} was changed outside the harness (found when "
                "the session was resumed): an in-place edit would mix that hand change into its checks; rebuild the "
                "plan to change it"
            )
        active = {s.op_id: s for s in self.trace().active_steps() if s.status is StepStatus.success}
        new_by_id = {op.id: op for op in new_plan.operations}
        changes: dict[str, tuple[str, ...]] = {}
        sketches: dict[str, tuple[SketchRequest, SketchRequest]] = {}  # consumer op -> (new sketch, old sketch)
        feature_ids: list[str] = []
        for op_id in ids:
            old, new = old_by_id.get(op_id), new_by_id.get(op_id)
            step = active.get(op_id)
            if old is None or new is None or step is None or not step.attempts:
                return f"operation {op_id!r} has not been executed in this session"
            if new.op != "sketch":
                feature_ids.append(op_id)
                continue
            changes[op_id] = tuple(name for name in type(new).model_fields if getattr(new, name) != getattr(old, name))
            refusal = self._prepare_sketch_edit(new_plan, new, step, active, resolver, ctx, sketches)
            if refusal is not None:
                return refusal
        for consumer in sketches:
            if consumer not in feature_ids:
                feature_ids.append(consumer)
        edits: list[FeatureEdit] = []
        old_edits: list[FeatureEdit] = []
        for op_id in feature_ids:
            old, new = old_by_id[op_id], new_by_id[op_id]
            step = active[op_id]
            feature = ctx.feature_by_op.get(op_id)
            if feature is None:
                return f"operation {op_id!r} produced no feature, so there is nothing to edit in place"
            extension = is_extension_op(new.op)
            if extension:
                if new.op not in self.extensions():
                    return f"operation {op_id!r} ({plain_op_name(new.op)}) is no longer declared by the adapter"
                kind = plain_op_name(new.op)  # edited with the ExtensionRequest it was made with, under its own name
            else:
                kind = DISPATCH[new.op][0]
                if kind not in REQUEST_BY_KIND:
                    return f"operation {op_id!r} ({new.op}) has no in-place edit"
            if any(isinstance(ref, EdgeSet) for ref in getattr(new, "edges", ())):
                # the edges a set names were consumed by the op itself: only a rebuild re-resolves the set on the part
                return f"operation {op_id!r} ({new.op}) names an edge set: it is re-resolved when the steps are rebuilt"
            if op_id in ids:
                changes[op_id] = tuple(
                    name for name in type(new).model_fields if getattr(new, name) != getattr(old, name)
                )
                if extension and "params" in changes[op_id]:  # name the arguments that changed, not just "params"
                    keys = sorted(k for k in {**old.params, **new.params} if old.params.get(k) != new.params.get(k))  # type: ignore[attr-defined]
                    changes[op_id] = tuple(f"params.{k}" for k in keys) + tuple(
                        n for n in changes[op_id] if n != "params"
                    )
            resolver.begin_step()
            try:
                request = build_request(new, ctx, resolver, self.extensions())
            except (ResolutionError, AdapterError) as exc:  # e.g. a selection token that is stale on the document
                return f"the new {op_id!r} cannot be built against the document as it stands: {exc}"
            recorded = self._current_request(op_id, step)
            request = _in_place_of(request, recorded)
            dumped = request.model_dump(mode="json")
            sketch_pair = sketches.get(op_id)
            if dumped == recorded and sketch_pair is None:
                continue
            if dumped != recorded and kind in DIMENSION_FIELDS:
                other = non_dimension_changes(kind, recorded, dumped)
                if other:
                    return (
                        f"{op_id} ({new.op}): {', '.join(other)} is not a dimension, so it cannot be set on the "
                        "feature that exists"
                    )
            edits.append(
                FeatureEdit(feature=feature, kind=kind, request=request, sketch=sketch_pair[0] if sketch_pair else None)
            )
            old_model = ExtensionRequest if extension else REQUEST_BY_KIND[kind]
            old_edits.append(
                FeatureEdit(
                    feature=feature,
                    kind=kind,
                    request=old_model.model_validate(recorded),
                    sketch=sketch_pair[1] if sketch_pair else None,
                )
            )
        return edits, old_edits, changes

    def _prepare_sketch_edit(
        self,
        new_plan: Plan,
        new: Any,
        step: StepRecord,
        active: dict[str, StepRecord],
        resolver: Resolver,
        ctx: ExecutionContext,
        sketches: dict[str, tuple[SketchRequest, SketchRequest]],
    ) -> str | None:
        """Fill ``sketches`` with the new and the old request of the sketch ``new`` for each executed op that consumes
        it, or return why its dimensions cannot be set in place."""
        consumers = [
            op.id
            for op in new_plan.operations
            if getattr(op, "sketch", None) == new.id and op.id in active and op.id in ctx.feature_by_op
        ]
        if not consumers:
            return f"sketch {new.id!r} is not used by an executed feature: there is no sketch in the CAD to change"
        resolver.begin_step()
        try:
            request = build_request(new, ctx, resolver, self.extensions())
        except (ResolutionError, AdapterError) as exc:
            return f"the new {new.id!r} cannot be built against the document as it stands: {exc}"
        recorded = self._current_request(new.id, step)
        request = _in_place_of(request, recorded)
        dumped = request.model_dump(mode="json")
        why = sketch_edit_refusal(recorded, dumped)
        if why is not None:
            return f"sketch {new.id!r}: {why}"
        if dumped == recorded:
            return None
        old_request = SketchRequest.model_validate(recorded)
        assert isinstance(request, SketchRequest)
        for consumer in consumers:
            sketches[consumer] = (request, old_request)
        return None

    def _current_request(self, op_id: str, step: StepRecord) -> dict[str, Any]:
        """The request of ``op_id`` as it stands in the document: the one it ran with unless an edit changed it."""
        edited = self._edited_requests.get(op_id)
        return edited[1] if edited is not None and edited[0] is step else step.attempts[-1].request

    def _adopt_edited_plan(self, new_plan: Plan, *, remeasure: bool, edits: Sequence[FeatureEdit] = ()) -> None:
        self.plan = new_plan
        if edits:
            active = {s.op_id: s for s in self.trace().active_steps() if s.status is StepStatus.success}
            by_feature = {handle.id: op_id for op_id, handle in self._require_started()[0].feature_by_op.items()}
            for edit in edits:
                op_id = by_feature[edit.feature.id]
                self._edited_requests[op_id] = (active[op_id], edit.request.model_dump(mode="json"))
                if edit.sketch is not None and edit.sketch.operation_id in active:
                    self._edited_requests[edit.sketch.operation_id] = (
                        active[edit.sketch.operation_id],
                        edit.sketch.model_dump(mode="json"),
                    )
        if not remeasure:
            return
        self.verifier.forget_geometry()
        for saved in self._verifier_states.values():
            forget_geometry_in(saved)
        op_id = f"{self.plan.id}.edit"
        try:
            self._last_mass = self.adapter.mass_properties(MassRequest(operation_id=op_id))
            self._last_bbox = self.adapter.bounding_box(MassRequest(operation_id=op_id))
        except AdapterError as exc:
            self.state = ExecutorState.degraded
            msg = (
                f"the edit was applied but the part could not be measured afterwards "
                f"({exc.info.code.value}: {exc.info.message}); session degraded"
            )
            raise ExecutorStateError(msg) from exc

    def _take_edit_back(self, old_edits: list[FeatureEdit], operation_id: str) -> None:
        """Put the old requests back (the round trip is exact): the executor found something the adapter could not."""
        try:
            back = self.adapter.edit_feature(
                EditFeatureRequest(operation_id=f"{operation_id}.revert", edits=tuple(old_edits))
            )
        except AdapterError as exc:
            self.state = ExecutorState.degraded
            msg = f"could not take the edit back ({exc.info.code.value}: {exc.info.message}); session degraded"
            raise ExecutorStateError(msg) from exc
        if not back.applied:
            self.state = ExecutorState.degraded
            msg = f"could not take the edit back ({back.failure or self._refusal_text(back)}); session degraded"
            raise ExecutorStateError(msg)

    def _reference_drift(
        self, new_plan: Plan, ids: tuple[str, ...]
    ) -> tuple[tuple[str, ...], dict[str, list[tuple[tuple[str | int, ...], Vec3]]], tuple[str, ...]]:
        """Re-resolve the symbolic references of every executed op AFTER the first edited one and compare with the
        handles they resolved to when they ran (``StepRecord.resolved_refs``): a face selector can silently land on
        another face once the CAD recomputed (the topological naming problem). Read-only.

        Returns ``(drift, follows, notes)``. A face selected by an absolute ``center`` that the edit moved does not
        resolve any more by that number; when the face it found is still in the document (the registry keeps its
        handle) and still has every other thing the selector asks for, it is the same face that moved, not a drift:
        ``follows`` gives, per op, the route to that selector and the centre it has now, and ``notes`` says it."""
        ctx, _ = self._require_started()
        positions = {op.id: index for index, op in enumerate(new_plan.operations)}
        first = min(positions[i] for i in ids)
        probe_ctx = ExecutionContext(document=ctx.document, documents=ctx.documents)
        probe_ctx.feature_by_op = dict(ctx.feature_by_op)
        probe_ctx.body_by_op = dict(ctx.body_by_op)
        probe = Resolver(self.adapter, probe_ctx, self.config.thresholds)
        active = {s.op_id: s for s in self.trace().active_steps() if s.status is StepStatus.success}
        drift: list[str] = []
        follows: dict[str, list[tuple[tuple[str | int, ...], Vec3]]] = {}
        notes: list[str] = []
        for op in new_plan.operations[first + 1 :]:
            step = active.get(op.id)
            if step is None or not step.resolved_refs:
                continue
            probe.begin_step()
            named = [(path, _route_of(path), ref) for path, ref in _refs_to_probe_after_an_edit(op)]
            if isinstance(op, ExtensionOperation):
                named += _extension_face_refs(op, self.extensions())
            for path, route, ref in named:
                try:
                    if isinstance(ref, FaceRef):
                        probe.face(ref, op.id, path)
                    elif isinstance(ref, EdgeRef):
                        probe.edge(ref, op.id, path)
                    else:
                        probe.vertex(ref, op.id, path)
                except (ResolutionError, AdapterError) as exc:
                    moved = self._followed_face(ref, step.resolved_refs.get(path), op.id, probe)
                    if moved is None:
                        drift.append(f"{op.id}.{path} no longer resolves ({str(exc).splitlines()[0]})")
                        continue
                    probe.resolved_refs[path] = step.resolved_refs[path]
                    follows.setdefault(op.id, []).append((route, moved.center))
                    was = ref.select.center
                    notes.append(f"{op.id}.{path} followed its face: centre {_vec(was)} -> {_vec(moved.center)}")
            paths = [path for path, _, _ in named]
            for key, was_id in step.resolved_refs.items():
                if not any(key == path or key.startswith(path) for path in paths):
                    continue
                now = probe.resolved_refs.get(key)
                if now is not None and now != was_id:
                    drift.append(f"{op.id}.{key} was {was_id}, now {now}")
        return tuple(drift), follows, tuple(notes)

    def _followed_face(self, ref: Any, handle_id: str | None, op_id: str, probe: Resolver) -> FaceInfo | None:
        """The face a selector with an absolute ``center`` found when its step ran, if it is still in the document and
        still fits the rest of the selector (surface, normal, size): the same face, moved by an edit."""
        if not isinstance(ref, FaceRef) or ref.from_selection is not None or handle_id is None:
            return None
        if ref.select.center is None:
            return None
        try:
            face = self.adapter.face_info(FaceRequest(operation_id=op_id, face=FaceHandle(id=handle_id)))
        except AdapterError:
            return None
        bare = ref.model_copy(update={"select": ref.select.model_copy(update={"center": None})})
        return face if probe._face_matches(face, bare) else None  # noqa: SLF001

    def _following_plan(self, new_plan: Plan, follows: dict[str, list[tuple[tuple[str | int, ...], Vec3]]]) -> Plan:
        """``new_plan`` with the ``center`` of each selector in ``follows`` set to where its face is now, so that a
        later rebuild looks for the face where it stands, not where it stood."""
        data = new_plan.model_dump(mode="python")
        for op in data["operations"]:
            for route, center in follows.get(op["id"], []):
                node = op["params"] if is_extension_op(op["op"]) else op
                for key in route:
                    node = node[key]
                node["select"]["center"] = tuple(center)
        return parse_plan(data, external_ids=set(self._external_features))

    def check_health(self, trigger: HealthTrigger) -> CanaryResult:
        """Run the adapter canary and record a ``HealthEvent`` for ``trigger``."""
        result = self.adapter.canary(Empty(operation_id=f"{self.plan.id}.canary"))
        self._health.append(HealthEvent(when=_now(), trigger=trigger, result=result))
        return result

    @property
    def health(self) -> tuple[HealthEvent, ...]:
        """Health events recorded so far (cheaper than ``trace()`` for a session that emits them)."""
        return tuple(self._health)

    def status(self) -> ExecutorStatus:
        total = len(self.plan.operations)
        current = self.plan.operations[self.cursor].id if self.cursor < total else None
        return ExecutorStatus(
            state=self.state,
            cursor=self.cursor,
            total=total,
            current_op_id=current,
            last_step=self._steps[-1] if self._steps else None,
        )

    def trace(self) -> ExecutionTrace:
        return ExecutionTrace(
            plan_id=self.plan.id,
            adapter=self.adapter.name,
            config=self.config,
            steps=tuple(self._steps),
            health=tuple(self._health),
            final_state=self.state,
            final_mass=self._last_mass,
            final_bbox=self._last_bbox,
            reverts=tuple(self._reverts),
            edits=tuple(self._edits),
        )

    def document(self) -> DocumentHandle | None:
        if self.state == ExecutorState.idle:
            msg = "executor has not been started (call start() first)"
            raise ExecutorStateError(msg)
        if self._ctx is None:
            return None
        return self._ctx.document

    def read_extension(self, operation: ExtensionOperation) -> OperationResult:
        """Run an adapter operation that only reads (``OperationSpec.read_only``) NOW and return what it answered. It is
        not a step: it is not added to the plan, leaves no record or undo, and the verifier and the cursor never see it.
        The references in its arguments are resolved against the steps in effect, like those of a step.

        Raises ``ResolutionError`` (a reference that names nothing now), ``AdapterError`` (the CAD refused) and
        ``ExecutorStateError`` (not started, degraded, or not a read the adapter declares)."""
        spec = self.extensions().get(operation.op)
        if spec is None or not spec.read_only:
            msg = f"{plain_op_name(operation.op)!r} is not a read-only operation of this adapter"
            raise ExecutorStateError(msg)
        if self.state is ExecutorState.degraded:
            msg = f"read_extension() called in state {self.state.value}"
            raise ExecutorStateError(msg)
        ctx, resolver = self._require_started()
        resolver.begin_step()
        request = build_request(operation, ctx, resolver, self.extensions())
        raw = getattr(self.adapter, spec.method)(request)
        if not isinstance(raw, OperationResult):
            msg = f"adapter method {spec.method!r} of the read {spec.name!r} returned {type(raw).__name__}"
            raise TypeError(msg)
        return raw

    # --- internals -----------------------------------------------------------------------------------

    def handles_by_op(self) -> tuple[dict[str, BodyHandle], dict[str, FeatureHandle]]:
        """Copies of the body and the feature each executed operation made (``op id -> handle``): how a caller matches
        a body before a rebuild with the same body after it, since the handles themselves can change."""
        ctx, _ = self._require_started()
        return dict(ctx.body_by_op), dict(ctx.feature_by_op)

    def extensions(self) -> dict[str, OperationSpec]:
        """The operations the adapter declared, by their name inside a plan (``ext:<name>``)."""
        if self._extension_specs is None:
            self._extension_specs = {extension_op_name(spec.name): spec for spec in self.adapter.extra_operations()}
        return self._extension_specs

    def _method_of(self, op: Any) -> str:
        spec = self.extensions().get(op.op)
        return spec.method if spec is not None else DISPATCH[op.op][0]

    def _undo_method_of(self, op_name: str) -> str | None:
        """The adapter method that takes back a step of ``op_name`` that left no feature, if its operation names one."""
        spec = self.extensions().get(op_name)
        return spec.undo_method if spec is not None else None

    def _adapter_undoes(self, step: StepRecord) -> bool:
        """Does reverting this step take a call to the adapter: it left a feature (``undo_last``), or it changed the
        model without one and its operation gave an ``undo_method``."""
        return _feature_of(step) is not None or self._undo_method_of(step.op) is not None

    def _undo_call(self, op_name: str, feature: FeatureHandle | None) -> Callable[[Empty], UndoResult]:
        method = None if feature is not None else self._undo_method_of(op_name)
        return self.adapter.undo_last if method is None else getattr(self.adapter, method)

    def _requires_feature(self, op: Any) -> bool:
        spec = self.extensions().get(op.op)
        return spec.makes_feature if spec is not None else op.op not in NON_FEATURE_OPS

    def _require_started(self) -> tuple[ExecutionContext, Resolver]:
        if self._ctx is None or self._resolver is None:
            msg = "executor has not been started (call start() first)"
            raise ExecutorStateError(msg)
        return self._ctx, self._resolver

    def _call(self, op: Any, request: Request) -> tuple[OperationResult | None, ErrorInfo | None, object | None]:
        method_name = self._method_of(op)
        try:
            raw = getattr(self.adapter, method_name)(request)
        except AdapterError as exc:
            return None, exc.info, None
        return self._normalise(op, raw), None, raw

    def _normalise(self, op: Any, raw: object) -> OperationResult:
        if isinstance(raw, OperationResult):
            return raw
        if isinstance(raw, SketchResult):
            self._pending_frames[op.id] = raw.frame
            return OperationResult(
                operation_id=op.id, feature=None, rebuild_ok=raw.rebuild_ok, sketch=raw.sketch, warnings=raw.warnings
            )
        if isinstance(raw, DatumPlaneResult | DatumAxisResult):
            self._pending_datums[op.id] = raw
            return OperationResult(operation_id=op.id, feature=None, rebuild_ok=raw.rebuild_ok)
        if isinstance(raw, InterferenceResult):
            return OperationResult(operation_id=op.id, feature=None, rebuild_ok=True)
        msg = f"adapter method for {op.op!r} returned {type(raw).__name__}"
        raise TypeError(msg)

    def _undo_if_feature(self, op: Any, result: OperationResult | None) -> bool:
        """Revert the feature of a failed attempt. When THAT revert fails the failed attempt's feature may still be in
        the document: the executor degrades (``self._cleanup_error`` says why, ``_cleanup_failed_step`` ends the step)
        instead of running the op again on top of it."""
        if result is None:
            return False
        if result.feature is None and self._undo_method_of(op.op) is None:
            return False
        try:
            self._undo_call(op.op, result.feature)(Empty(operation_id=op.id))
        except AdapterError as exc:
            self.state = ExecutorState.degraded
            self._cleanup_error = ErrorInfo(
                code=exc.info.code,
                message=(
                    f"could not revert the failed attempt of {op.id!r} ({exc.info.code.value}: {exc.info.message}); "
                    "the failed attempt's feature may remain in the document"
                ),
                operation_id=exc.info.operation_id or op.id,
                details=dict(exc.info.details),
                recoverable=False,
            )
            return False
        return True

    def _cleanup_failed_step(
        self,
        op: Any,
        attempts: list[AttemptRecord],
        started: datetime,
        mass_before: MassProperties | None,
        expectations: tuple[Expectation, ...],
    ) -> StepRecord:
        """End a step whose cleanup revert failed: the last attempt carries the (non-recoverable) cleanup error."""
        attempts[-1] = attempts[-1].model_copy(update={"error": self._cleanup_error})
        return self._finish(op, StepStatus.failed, attempts, started, mass_before, None, expectations)

    # --- bounded repair ------------------------

    def _apply_repair(self, op: Any, request: Request, diagnosis: Diagnosis) -> tuple[Request, RepairAction | None]:
        """Return the request the next attempt should send, plus the repair actually applied (``None``
        when nothing could be concretely applied, even though ``diagnosis.repairable`` was ``True``).

        Never touches the document or any operation other than ``op``'s own next attempt: it either
        returns a new ``request`` built from data this plan already declared, or returns ``request``
        unchanged. Guardrail: a reference into ``self._external_features`` -- a feature this
        plan adopted by alias but did not build -- is refused here too, in addition to ``diagnosis.py``
        already marking it unrepairable; belt and suspenders on the one rule repair cannot break.
        """
        repair = diagnosis.repair
        if repair is None:
            return request, None
        if repair.kind == "add_sketch_constraint":
            return self._repair_add_sketch_constraint(op, request, repair)
        if repair.kind == "reresolve_reference":
            return self._repair_reresolve_reference(op, request, diagnosis, repair)
        return request, None  # pragma: no cover - exhaustive over RepairAction.kind today

    def _repair_add_sketch_constraint(
        self, op: Any, request: Request, repair: RepairAction
    ) -> tuple[Request, RepairAction | None]:
        if op.op != "sketch" or not hasattr(request, "entities"):
            # Only the sketch operation's own request carries entities/constraints/named_dimensions; a
            # downstream op that merely depends on a sketch has no field to hold this fix (diagnosis.py
            # already refuses repairable=True for that case, this is defence in depth).
            return request, None
        raw_indices = str(repair.detail.get("free_geometry", ""))
        indices = [int(token) for token in raw_indices.split(",") if token != ""]
        if not indices:
            return request, None
        entities = request.entities  # type: ignore[attr-defined]
        named_dimensions = list(request.named_dimensions)  # type: ignore[attr-defined]
        constraints = list(request.constraints)  # type: ignore[attr-defined]
        existing_dims = {(d.entity, d.attribute) for d in named_dimensions}
        existing_constraints = {(c.kind, c.entities) for c in constraints}
        changed = False
        for index in indices:
            if index < 0 or index >= len(entities):
                continue
            entity = entities[index]
            for attribute in _entity_dimension_attributes(entity):
                key = (entity.name, attribute)
                if key in existing_dims:
                    continue
                named_dimensions.append(
                    NamedDimension(name=f"{entity.name}_{attribute}", entity=entity.name, attribute=attribute)
                )
                existing_dims.add(key)
                changed = True
            center = _entity_center(entity)
            if center is not None and center == (0.0, 0.0):
                key = ("coincident", (entity.name, "origin"))
                if key not in existing_constraints:
                    constraints.append(SketchConstraint(kind="coincident", entities=(entity.name, "origin")))
                    existing_constraints.add(key)
                    changed = True
        if not changed:
            return request, None
        new_request = request.model_copy(
            update={"named_dimensions": tuple(named_dimensions), "constraints": tuple(constraints)}
        )
        return new_request, repair

    def _repair_reresolve_reference(
        self, op: Any, request: Request, diagnosis: Diagnosis, repair: RepairAction
    ) -> tuple[Request, RepairAction | None]:
        reference = diagnosis.reference
        if reference is None or reference in self._external_features:
            return request, None
        resolved = self.probe_references((op,), external_features=self._external_features)
        prefix = f"{op.id}."
        updates: dict[str, Any] = {}
        for path, new_id in resolved.items():
            if not path.startswith(prefix):
                continue
            field_path = path[len(prefix) :]
            if "." in field_path:
                continue  # nested intermediate resolution (e.g. an edge's between[i] face); not a request field
            name, index = _parse_field_path(field_path)
            current = getattr(request, name, None)
            new_value = _with_new_handle_id(current, index, new_id)
            if new_value is not None:
                updates[name] = new_value
        if not updates or all(updates[name] == getattr(request, name) for name in updates):
            return request, None
        return request.model_copy(update=updates), repair

    def _snapshot(self, op: Any, request: Request, mass: MassProperties, bbox: BoundingBox) -> StateSnapshot:
        seed_delta: float | None = None
        component_bbox: BoundingBox | None = None
        if op.op in PATTERN_OPS:
            seed_delta = self._seed_volume_delta(op.features)
        if op.op in MATE_OPS:
            face_b = self.adapter.face_info(FaceRequest(operation_id=op.id, face=request.b))  # type: ignore[attr-defined]
            if face_b.component is not None:
                component_bbox = self.adapter.component_bounding_box(
                    ComponentRequest(operation_id=op.id, component=face_b.component)
                )
        ctx, _ = self._require_started()
        return StateSnapshot(
            mass=mass,
            bbox=bbox,
            seed_volume_delta=seed_delta,
            component_bbox_before=component_bbox,
            untracked_bodies=bool(self._external_features or ctx.component_by_op),
        )

    def _seed_volume_delta(self, feature_ops: tuple[str, ...]) -> float | None:
        total = 0.0
        active = active_steps_of(self._steps, self._reverts)
        for op_id in feature_ops:
            observed = self._observed_volume_delta(op_id, active)
            if observed is None:
                return None
            total += observed
        return total

    def _observed_volume_delta(self, op_id: str, steps: Sequence[StepRecord] | None = None) -> float | None:
        """The volume change the CURRENT (not undone) successful step of ``op_id`` observed: a seed that was undone and
        run again must not be read from its first record."""
        for step in reversed(self.trace().active_steps() if steps is None else steps):
            if step.op_id != op_id or step.status != StepStatus.success:
                continue
            for check in step.last_level2():
                if check.name == "volume_delta" and isinstance(check.observed, int | float):
                    return float(check.observed)
        return None

    def _register(self, op: Any, request: Request, result: OperationResult, observation: Observation) -> None:
        ctx, _ = self._require_started()
        if result.feature is not None:
            ctx.feature_by_op[op.id] = result.feature
        if result.sketch is not None:
            ctx.sketch_by_op[op.id] = result.sketch
        placed: SketchFrame | None = result.place
        if op.op == "sketch":
            frame = self._pending_frames.pop(op.id, None)
            if frame is not None:
                ctx.sketch_frame_by_op[op.id] = frame
                if isinstance(getattr(request, "on", None), FaceHandle):
                    placed = frame
            ctx.sketch_entities_by_op[op.id] = tuple(op.entities)
        if placed is not None:
            self._note_move(op.id, placed)
            ctx.place_by_op[op.id] = placed
        datum = self._pending_datums.pop(op.id, None)
        if isinstance(datum, DatumPlaneResult):
            # for ``ref_plane`` the executor's own computed pose is the truth downstream (Level 2 already compared
            # the adapter's realised frame with it); an operation the adapter declared asked for no pose, so the
            # frame it reports is the plane. A sketch on this datum carries exactly this frame.
            frame = request.frame if isinstance(request, DatumPlaneRequest) else datum.frame
            ctx.datum_plane_by_op[op.id] = DatumPlaneTarget(handle=datum.datum, frame=frame)
        elif isinstance(datum, DatumAxisResult):
            ctx.datum_axis_by_op[op.id] = request.axis if isinstance(request, DatumAxisRequest) else datum.axis
        if result.created_bodies:
            ctx.body_by_op[op.id] = result.created_bodies[0]
            if op.op == "split":
                # created_bodies is (positive, negative) for keep=both, the one kept piece otherwise
                sides = ("positive", "negative") if request.keep == "both" else (request.keep,)  # type: ignore[attr-defined]
                ctx.body_sides[op.id] = dict(zip(sides, result.created_bodies, strict=False))
        elif op.op.startswith("boolean_"):
            ctx.body_by_op[op.id] = request.target  # type: ignore[attr-defined]
        elif op.op in ("translate", "rotate"):
            ctx.body_by_op[op.id] = request.body  # type: ignore[attr-defined]
        if result.component is not None:
            ctx.component_by_op[op.id] = result.component
        if op.op == "add_joint" and result.joint is not None:
            ctx.joint_by_op[op.id] = result.joint.handle
        self._last_mass = observation.mass_after
        self._last_bbox = observation.bbox_after
        if self._model_generation == self._step_generation:  # nothing touched the part while the step ran
            self._fresh = (observation.mass_after, observation.bbox_after, op.op in ASSEMBLY_OPS)

    def invalidate_measure(self) -> None:
        """The part may have changed by something other than a step (a hand edit in the CAD): the next step measures it
        again instead of reusing what the last one left. Safe to call from another thread."""
        self._model_generation += 1
        self._fresh = None

    def last_measure(self, *, quick: bool = False) -> tuple[MassProperties, BoundingBox] | None:
        """Mass and box the last step measured when it ended, if nothing has changed the part since (``None``: the
        caller asks the adapter). The same answer the executor itself reuses for the next step, so a caller that prints
        the state after a step does not measure what the step just measured. ``quick`` accepts a quick box; otherwise
        only the exact one is returned."""
        fresh = self._fresh
        if fresh is None or (not quick and not fresh[1].exact):
            return None
        return fresh[0], fresh[1]

    def _known_mass(self, op: Any) -> MassProperties | None:
        """The part's mass before ``op`` when it is known without asking the adapter (``None``: not known)."""
        fresh = self._fresh
        return fresh[0] if fresh is not None and fresh[2] == (op.op in ASSEMBLY_OPS) else None

    def _measure_before(self, op: Any) -> tuple[MassProperties, BoundingBox]:
        """Mass and box of the part before ``op``. The last step measured the part when it ended (the same measure this
        op asks for) and nothing has changed it since, so that is reused; otherwise the adapter is asked."""
        fresh = self._fresh
        if fresh is not None and fresh[2] == (op.op in ASSEMBLY_OPS):
            return fresh[0], fresh[1]
        return (
            self.adapter.mass_properties(mass_request(op.id, op.op)),
            self.adapter.bounding_box(box_request(op.id, op.op)),
        )

    def _flip_applies(
        self, op: Any, request: Request, level2: tuple[CheckResult, ...], attempts: list[AttemptRecord]
    ) -> bool:
        if not self.config.flip_retry_for_mates or op.op not in MATE_FLIP_OPS:
            return False
        if any(attempt.flipped for attempt in attempts):
            return False
        failing = [c.name for c in level2 if c.kind == CheckKind.asserted and c.ok is False]
        return failing == ["component_bbox"]

    def _succeed(
        self,
        op: Any,
        attempts: list[AttemptRecord],
        started: datetime,
        mass_before: MassProperties,
        observation: Observation,
        expectations: tuple[Expectation, ...],
    ) -> StepRecord:
        self.cursor += 1
        if self.cursor == len(self.plan.operations):
            self.state = ExecutorState.completed
        else:
            self.state = ExecutorState.paused if self._pause_requested else ExecutorState.running
        return self._finish(
            op, StepStatus.success, attempts, started, mass_before, observation.mass_after, expectations
        )

    @staticmethod
    def _step_notes(resolver: Resolver, attempts: list[AttemptRecord]) -> tuple[str, ...]:
        """The edge-set reports of the step plus, for a ``skip_failing`` op, the edges the CAD left out."""
        notes = [line for report in resolver.edge_set_reports.values() for line in report]
        result = attempts[-1].result if attempts else None
        if result is not None and result.skipped_edges:
            notes.append(f"skipped {len(result.skipped_edges)} edge(s) the CAD could not take (the rest were applied):")
            for handle in result.skipped_edges:
                edge = resolver.edge_set_edges.get(handle.id)
                notes.append(f"  {describe_edge(edge) if edge is not None else handle.id}")
        return tuple(notes)

    def _finish(
        self,
        op: Any,
        status: StepStatus,
        attempts: list[AttemptRecord],
        started: datetime,
        mass_before: MassProperties | None,
        mass_after: MassProperties | None,
        expectations: tuple[Expectation, ...],
        plan_error: str | None = None,
    ) -> StepRecord:
        _, resolver = self._require_started()
        record = StepRecord(
            op_id=op.id,
            op=op.op,
            status=status,
            attempts=tuple(attempts),
            resolved_refs=dict(resolver.resolved_refs),
            notes=self._step_notes(resolver, attempts) if status is StepStatus.success else (),
            expectations=expectations,
            plan_error=plan_error,
            mass_before=mass_before,
            mass_after=mass_after,
            started_at=started,
            finished_at=_now(),
        )
        self._steps.append(record)
        return record
