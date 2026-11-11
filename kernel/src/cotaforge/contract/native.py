"""``run_native`` request and result (contract/native.py).

``run_native`` is the escape hatch for what the optimised tools do not cover: the caller sends a fragment of
the CAD's own scripting language and gets back the same short, measured result every time. It never rejects
a fragment for style, size or a missing intent line (those are only ``warnings``); a fragment that raises or
leaves something broken is rolled back by the adapter and explained.
"""

from pydantic import BaseModel, ConfigDict, Field

from cotaforge.contract.geometry import BoundingBox
from cotaforge.contract.handles import FeatureHandle
from cotaforge.contract.requests import Request

# Upper bound of the captured output reported back; what was printed beyond it is counted in
# ``RunNativeResult.stdout_dropped_chars``, never cut silently.
MAX_STDOUT_CHARS = 64 * 1024


class RunNativeRequest(Request):
    """One native fragment. ``intent`` is a free-text line about what the fragment is for (recorded, never
    required). ``timeout_s`` bounds the execution of the fragment itself: when it expires the adapter stops
    the fragment, rolls the step back and reports a timeout error."""

    model_config = ConfigDict(extra="forbid")

    code: str
    intent: str = ""
    timeout_s: float = Field(default=60.0, gt=0, le=3600)


class NativeObject(BaseModel, frozen=True):
    """Short summary of a CAD object a step created, modified or removed."""

    model_config = ConfigDict(extra="forbid")

    name: str
    label: str
    type_id: str


class BrokenObject(BaseModel, frozen=True):
    """An object a step left unusable (invalid shape, solver conflict, recompute error, ...)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    reason: str


class NativeError(BaseModel, frozen=True):
    """What went wrong. ``line`` is the 1-based line of the caller's code (``None`` when the failure does
    not come from a line of it, e.g. a rolled-back step whose objects were left broken)."""

    model_config = ConfigDict(extra="forbid")

    type: str
    message: str
    line: int | None = None
    hint: str = ""


class RunNativeResult(BaseModel, frozen=True):
    """Outcome of one native step.

    ``ok`` is true only when the fragment ran, recomputed and left nothing broken. When ``ok`` is false the
    adapter has already restored the document to its pre-step state (``rolled_back``; also true when the
    fragment never ran, e.g. a syntax error); if it could not fully do so, ``rolled_back`` is false and a
    warning says what differs. ``volume_delta_mm3`` and the boxes cover the whole document's solids (boxes
    are ``None`` when the document has no solid); after a failed step they describe the unchanged
    document."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    feature: FeatureHandle | None = None
    """The history entry the adapter made for a step that changed the document (what ``undo_last`` reverts as ONE
    step); ``None`` for a step that changed nothing or failed."""
    created: tuple[NativeObject, ...] = ()
    modified: tuple[NativeObject, ...] = ()
    removed: tuple[NativeObject, ...] = ()
    volume_delta_mm3: float = 0.0
    bbox_before: BoundingBox | None = None
    bbox_after: BoundingBox | None = None
    broken: tuple[BrokenObject, ...] = ()
    stdout: str = ""
    stdout_dropped_chars: int = Field(default=0, ge=0)
    """Characters the fragment printed beyond ``MAX_STDOUT_CHARS``: counted, not kept (``stdout`` holds the start)."""
    error: NativeError | None = None
    rolled_back: bool = False
    warnings: tuple[str, ...] = ()
    duration_ms: float = 0.0
