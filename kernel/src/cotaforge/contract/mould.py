"""Mould-making reads: the optional, READ-ONLY ``draft_analysis`` query.

A moulded part leaves the mould along one pull direction (per mould half). Every face of it must lean from that
direction by some draft angle or it scrapes while the part is ejected; a face that cannot be reached from the pull
direction at all (something of the part stands between it and the mould half that would form it) is an undercut and
needs a side action. ``draft_analysis`` measures both on the model, per face, so a number never comes from a guess.
"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from cotaforge.contract.geometry import Vec3
from cotaforge.contract.handles import BodyHandle, EdgeHandle, FaceHandle
from cotaforge.contract.requests import Request

__all__ = [
    "DEFAULT_MIN_DRAFT_DEG",
    "DRAFT_MIN_TOL_DEG",
    "DRAFT_ZERO_DEG",
    "DraftAnalysisRequest",
    "DraftAnalysisResult",
    "MouldSplitRequest",
    "PartingAnalysisRequest",
    "PartingAnalysisResult",
    "PartingLoop",
    "SlideProposal",
    "WallsToDecide",
    "ZeroDraftSide",
    "proposed_block_margin",
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


ZeroDraftSide = Literal["core", "cavity"]
"""The mould half that forms a wall with no draft that touches both halves. The CORE is the moving half: it opens along
``pull``, and forms the faces that look along it; the CAVITY is the fixed half on the other side."""

BLOCK_MARGIN_RULE = (
    "no block given: the part's box grown by the larger of 25 mm and a fifth of its longest side on every side. A "
    "first-pass rule of thumb for a steel insert, not a standard: the mould base and the press size it for real"
)


class PartingAnalysisRequest(Request):
    """READ-ONLY: the parting line of ``body`` (``None`` = the only live body) against the pull ``pull`` (the direction
    the core opens along: the faces that look along it are the core's, the ones that look against it the cavity's).
    ``zero_draft_to`` decides the walls with no draft that touch both halves (the outer wall of a shell: the rim on one
    end, the bottom on the other); without it they are reported and the loops are computed under the proposal."""

    body: BodyHandle | None = None
    pull: Vec3
    min_draft_deg: float = Field(default=DEFAULT_MIN_DRAFT_DEG, ge=0.0, le=45.0)
    zero_draft_to: ZeroDraftSide | None = None

    @field_validator("pull")
    @classmethod
    def _pull_not_zero(cls, value: Vec3) -> Vec3:
        if sum(c * c for c in value) < 1e-12:
            msg = "pull must be a non-zero vector"
            raise ValueError(msg)
        return value


class PartingLoop(BaseModel, frozen=True):
    """One loop of the parting line: the edges between a core face and a cavity face, chained. ``role`` is ``outer`` for
    the loop that encloses the most area across the pull (the parting surface runs from it out to the insert block) and
    ``inner`` for any other (an opening through the part: a shut-off closes it). ``planar`` is whether the loop lies in
    one plane; ``levels_mm`` the distinct heights along the pull it runs at (more than one = the line steps)."""

    number: int = Field(ge=1)
    role: Literal["outer", "inner"]
    closed: bool
    length_mm: float
    planar: bool
    levels_mm: tuple[float, ...] = ()
    edges: tuple[EdgeHandle, ...] = ()


class WallsToDecide(BaseModel, frozen=True):
    """A connected group of walls with no draft that touch BOTH halves: the engineer says which half forms them.
    ``touches`` are the sides of the leaning faces next to the group."""

    faces: tuple[FaceHandle, ...]
    touches: tuple[str, ...] = ()


class SlideProposal(BaseModel, frozen=True):
    """One undercut region (a connected group of faces the pull cannot release) and the slide that would form it:
    ``direction`` is a direction along which a ray from every sample point of the region meets nothing of the part (the
    sense pointing away from the part when both are free), ``None`` when no candidate direction frees it, and ``why``
    where the direction came from. ``accepted`` is whether the split made it."""

    region: int = Field(ge=1)
    faces: tuple[FaceHandle, ...]
    area_mm2: float
    direction: Vec3 | None = None
    why: str = ""
    accepted: bool = False


class PartingAnalysisResult(BaseModel, frozen=True):
    """The parting line and what the split needs decided. ``provisional`` is true when ``loops`` were computed under the
    proposal ``zero_draft_proposal`` because walls were left undecided. ``complete`` is false when the silhouette runs
    across faces (``straddle``): the line is then missing the part that crosses them, and no split is possible."""

    loops: tuple[PartingLoop, ...] = ()
    ambiguous: tuple[WallsToDecide, ...] = ()
    zero_draft_proposal: ZeroDraftSide | None = None
    provisional: bool = False
    straddle: tuple[FaceHandle, ...] = ()
    slides: tuple[SlideProposal, ...] = ()
    complete: bool = True
    method: str = ""
    warnings: tuple[str, ...] = ()


class MouldSplitRequest(Request):
    """Cut the negative of ``source`` (the shrunk part) out of an insert block and split it
    along the parting surface into
    two NEW bodies, the ``cavity`` (fixed half) and the ``core`` (moving half, opens along ``pull``), plus one body per
    accepted slide. ``source`` is untouched. The block is ``block_size`` (x, y, z in the document's axes, centred on the
    part) or, without it, the part's box grown by ``margin_mm`` (default ``BLOCK_MARGIN_RULE``). ``slides`` are the
    undercut regions to form with a slide (``"all"`` or their numbers, as ``PartingAnalysisResult`` lists them); a part
    with an undercut that no slide forms is refused (it would be trapped). ``zero_draft_to`` is required when walls with
    no draft touch both halves."""

    source: BodyHandle
    pull: Vec3
    block_size: Vec3 | None = None
    margin_mm: float | None = Field(default=None, gt=0.0)
    zero_draft_to: ZeroDraftSide | None = None
    slides: Literal["none", "all"] | tuple[int, ...] = "none"
    min_draft_deg: float = Field(default=DEFAULT_MIN_DRAFT_DEG, ge=0.0, le=45.0)

    @field_validator("pull")
    @classmethod
    def _pull_not_zero(cls, value: Vec3) -> Vec3:
        if sum(c * c for c in value) < 1e-12:
            msg = "pull must be a non-zero vector"
            raise ValueError(msg)
        return value

    @field_validator("block_size")
    @classmethod
    def _block_positive(cls, value: Vec3 | None) -> Vec3 | None:
        if value is not None and not all(c > 0.0 for c in value):
            msg = "block_size must be above 0 on every axis"
            raise ValueError(msg)
        return value


def proposed_block_margin(extent_mm: float) -> float:
    """The margin ``BLOCK_MARGIN_RULE`` proposes on every side of a part whose longest side is ``extent_mm``."""
    return max(25.0, 0.2 * extent_mm)
