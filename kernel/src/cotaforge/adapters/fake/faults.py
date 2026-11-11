"""Fault injection models for the fake adapter."""

from enum import Enum

from pydantic import BaseModel

from cotaforge.contract import ErrorCode


class FaultKind(str, Enum):  # noqa: UP042
    raise_error = "raise_error"
    rebuild_failed = "rebuild_failed"
    wrong_volume = "wrong_volume"
    wrong_side = "wrong_side"
    canary_fail = "canary_fail"
    wrong_face_count = "wrong_face_count"
    underdefined_sketch = "underdefined_sketch"
    """The fake's ``sketch()`` reports
    ``rebuild_ok=False`` on the attempt this fires, so level 1 fails on the sketch op itself -- the ONE
    case ``executor.diagnosis.diagnose`` can mechanically repair (``add_sketch_constraint``, since only
    the sketch operation's own request carries entities/constraints/named_dimensions). The sketch is
    otherwise materialised exactly as the IR declares it: whether it is actually underdefined (``dof >
    0``) is a property of the part's own sketch (no ``named_dimensions`` declared), never of this
    fault, which only supplies the failing attempt's signal."""
    wrong_datum_pose = "wrong_datum_pose"
    """``datum_plane`` / ``datum_axis`` report a pose displaced from the requested one (origin moved 1 mm along
    the normal / axis direction and, for a plane, the normal tilted 5 degrees): the Level 2 ``datum_*`` pose
    checks must catch it."""
    dangling_reference = "dangling_reference"
    """Simulates a manual edit of the referenced
    geometry landing between the request's original resolution and the retry's re-resolution. The fake
    renames the request's own referenced face to a fresh id in the document (same geometry, new handle)
    and raises ``invalid_reference`` -- so ``Executor._apply_repair``'s ``reresolve_reference`` genuinely
    finds a different, valid handle instead of a no-op (``_repair_reresolve_reference`` only applies a
    repair when the re-resolved value differs from the stale one)."""


class Fault(BaseModel, frozen=True):
    operation_id: str
    attempt: int | None = None
    kind: FaultKind
    error_code: ErrorCode = ErrorCode.geometry_failed
    recoverable: bool = True
    volume_factor: float = 0.5
    times: int | None = None


class FaultPlan(BaseModel, frozen=True):
    faults: tuple[Fault, ...] = ()


class FaultEngine:
    """Matches faults against (operation_id, attempt) and keeps the fired log."""

    def __init__(self, plan: FaultPlan | None) -> None:
        self._plan = plan or FaultPlan()
        self._attempts: dict[str, int] = {}
        self._fired_count: dict[int, int] = {}
        self._log: list[tuple[str, int, FaultKind]] = []
        self.canary_healthy = True

    def begin(self, operation_id: str) -> tuple[Fault, ...]:
        """Count one attempt for the operation and return the faults that fire on it."""
        self._attempts[operation_id] = self._attempts.get(operation_id, 0) + 1
        attempt = self._attempts[operation_id]
        fired: list[Fault] = []
        for index, fault in enumerate(self._plan.faults):
            if fault.operation_id != operation_id:
                continue
            if fault.attempt is not None and fault.attempt != attempt:
                continue
            if fault.times is not None and self._fired_count.get(index, 0) >= fault.times:
                continue
            self._fired_count[index] = self._fired_count.get(index, 0) + 1
            self._log.append((operation_id, attempt, fault.kind))
            if fault.kind is FaultKind.canary_fail:
                self.canary_healthy = False
            fired.append(fault)
        return tuple(fired)

    def attempts(self, operation_id: str) -> int:
        return self._attempts.get(operation_id, 0)

    def fired(self) -> tuple[tuple[str, int, FaultKind], ...]:
        return tuple(self._log)
