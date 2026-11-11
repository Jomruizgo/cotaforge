"""ExecutorConfig."""

from pydantic import BaseModel

from cotaforge.verification.thresholds import DEFAULT_THRESHOLDS, Thresholds


class ExecutorConfig(BaseModel, frozen=True):
    max_retries: int = 2
    stop_on_verification_failure: bool = True
    canary_before_plan: bool = True
    canary_after_failure: bool = True
    flip_retry_for_mates: bool = True
    thresholds: Thresholds = DEFAULT_THRESHOLDS
    repair_on_failure: bool = True
    """Diagnose and, when repairable, attempt a
    bounded in-retry repair on a level-1 failure. ``False`` disables diagnosis and repair entirely --
    every ``AttemptRecord.diagnosis``/``repair_applied`` stays ``None`` and the retry loop behaves exactly
    as it did before this option existed (see the ``repair_on_failure=False`` regression tests)."""
    max_repairs_per_operation: int = 1
    """Upper bound on how many times a single operation may be diagnosed-and-repaired across its own
    retries -- "acotada" (bounded) with a number, per the plan. A repair never adds an attempt of its
    own: it consumes one of the existing ``max_retries`` budget."""


DEFAULT_CONFIG = ExecutorConfig()
