"""Mould-making reads: the optional, READ-ONLY ``draft_analysis`` query.

A moulded part leaves the mould along one pull direction (per mould half). Every face of it must lean from that
direction by some draft angle or it scrapes while the part is ejected; a face that cannot be reached from the pull
direction at all (something of the part stands between it and the mould half that would form it) is an undercut and
needs a side action. ``draft_analysis`` measures both on the model, per face, so a number never comes from a guess.
"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from cotaforge.contract.geometry import Vec3
from cotaforge.contract.handles import BodyHandle, FaceHandle
from cotaforge.contract.requests import Request

__all__ = [
    "DEFAULT_MIN_DRAFT_DEG",
    "DRAFT_MIN_TOL_DEG",
    "DRAFT_ZERO_DEG",
    "DraftAnalysisRequest",
    "DraftAnalysisResult",
    "DraftStatus",
    "FaceDraft",
    "classify_draft",
]

DEFAULT_MIN_DRAFT_DEG = 1.0
"""The minimum draft used when the engineer gives none. It is the usual first-pass rule of thumb of injection moulding
design guides (about 1 degree on a smooth wall, more on a textured one, less only when the moulder agrees), NOT a
standard and not a property of any material: the resin's datasheet or the moulder sets the real value."""

DRAFT_ZERO_DEG = 0.01
"""A face leaning less than this from the pull direction has no draft at all (a wall parallel to the pull)."""

DRAFT_MIN_TOL_DEG = 0.001
"""A face within this of the minimum meets it: a face drafted to exactly the minimum measures a hair under it."""

DraftStatus = Literal["ok", "low", "zero", "undercut"]


def classify_draft(draft_deg: float, shadowed: bool, min_draft_deg: float) -> DraftStatus:
    """``undercut`` when the face cannot be reached from the pull direction, else ``zero`` (no lean at all), ``low``
    (leans, but under the minimum) or ``ok``."""
    if shadowed:
        return "undercut"
    if draft_deg <= DRAFT_ZERO_DEG:
        return "zero"
    return "low" if draft_deg < min_draft_deg - DRAFT_MIN_TOL_DEG else "ok"


class DraftAnalysisRequest(Request):
    """Measure the draft of every face of ``body`` (``None`` = every live body of the part) against the pull direction
    ``pull`` (the direction a mould half moves away from the part; the opposite half goes the other way, so both senses
    are checked). ``min_draft_deg`` classifies the faces. ``paint`` also colours the faces on the CAD's screen until
    the next call that shows something: green ok, yellow low, red zero or undercut."""

    body: BodyHandle | None = None
    pull: Vec3
    min_draft_deg: float = Field(default=DEFAULT_MIN_DRAFT_DEG, ge=0.0, le=45.0)
    paint: bool = False

    @field_validator("pull")
    @classmethod
    def _pull_not_zero(cls, value: Vec3) -> Vec3:
        if sum(c * c for c in value) < 1e-12:
            msg = "pull must be a non-zero vector"
            raise ValueError(msg)
        return value


class FaceDraft(BaseModel, frozen=True):
    """One face. ``draft_deg`` is the SMALLEST lean of the face from the pull direction over the face (0 = a wall
    parallel to the pull, 90 = a face looking straight along it). A plane is exact; a curved face is sampled
    (``exact`` false, ``samples`` points). ``shadowed`` is the share (0..1) of the sampled points that cannot be reached
    from the pull direction: the ray from the point, along the sense that releases it, meets the part again.
    ``status`` follows ``classify_draft``."""

    face: FaceHandle
    body: BodyHandle
    surface: str
    area_mm2: float
    draft_deg: float = Field(ge=0.0, le=90.0 + 1e-6)
    shadowed: float = Field(default=0.0, ge=0.0, le=1.0)
    samples: int = Field(default=1, ge=1)
    exact: bool = True
    status: DraftStatus


class DraftAnalysisResult(BaseModel, frozen=True):
    """The faces, ``method`` (how the numbers were obtained: what is exact, what is sampled and how), whether the
    shadow test ran (``shadow_tested``: false when the adapter cannot cast it, so an undercut is then NOT reported) and
    whether the faces were painted."""

    faces: tuple[FaceDraft, ...] = ()
    method: str = ""
    shadow_tested: bool = True
    painted: bool = False
    warnings: tuple[str, ...] = ()
