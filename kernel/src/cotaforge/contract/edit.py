"""In-place feature editing: the optional ``edit_feature`` tool.

Changing a value of an EARLIER operation is, in a parametric CAD, an edit of the property of the feature that already
exists, followed by the CAD's own recompute of whatever depends on it -- not a rebuild of everything after it. The tool
carries the FULL new contract request of every edited op (the one the executor's builders produce, e.g. a
``BoxRequest``), never property names: each adapter maps that request onto its own properties, so the contract stays
CAD-neutral and a later adapter (or a later op kind) needs no new vocabulary.

The result is the "observation loop": the health of the recompute (as the CAD reports it), the geometric
delta per body measured by tools, flags for the silent failures the CAD does not report (a body that vanished, a union
that split, a cut that no longer cuts, a new interference...), and the references that were created, removed or
modified. An edit that breaks the model is ROLLED BACK by the adapter and reported (``applied`` false, ``failure``
set): the caller never finds a half-edited document. A feature the adapter cannot edit in place is REFUSED (reason per
feature, nothing touched): the caller falls back to rebuilding.
"""

from typing import Literal

from pydantic import BaseModel, Field, SerializeAsAny, model_validator

from cotaforge.contract.assembly import MateDistanceRequest
from cotaforge.contract.dimensions import SKETCH_CONSUMERS
from cotaforge.contract.extension import ExtensionRequest
from cotaforge.contract.geometry import BoundingBox, Vec3
from cotaforge.contract.handles import BodyHandle, FaceHandle, FeatureHandle
from cotaforge.contract.requests import (
    BooleanRequest,
    BoxRequest,
    ChamferRequest,
    CircularPatternRequest,
    CopyBodyRequest,
    CutRequest,
    CylinderRequest,
    DraftRequest,
    ExtrudeRequest,
    FilletRequest,
    HoleRequest,
    LinearPatternRequest,
    MirrorBodyRequest,
    MirrorRequest,
    MoveBodyRequest,
    Request,
    RevolveRequest,
    ShellRequest,
    SketchRequest,
    SplitRequest,
    SweepRequest,
)

__all__ = [
    "REQUEST_BY_KIND",
    "BodyDelta",
    "BodyInterferenceRequest",
    "BodyInterferenceResult",
    "BodyMeasure",
    "BodyPairReading",
    "BodyReading",
    "CutReading",
    "EditFeatureRequest",
    "EditFlag",
    "EditFlagKind",
    "EditResult",
    "FailureKind",
    "FeatureEdit",
    "HealthIssue",
    "MeasureBodiesResult",
    "OverlapReading",
    "RefsDelta",
    "kind_of_request",
]

REQUEST_BY_KIND: dict[str, type[Request]] = {
    "extrude": ExtrudeRequest,
    "cut": CutRequest,
    "revolve": RevolveRequest,
    "sweep": SweepRequest,
    "hole": HoleRequest,
    "linear_pattern": LinearPatternRequest,
    "circular_pattern": CircularPatternRequest,
    "chamfer": ChamferRequest,
    "fillet": FilletRequest,
    "shell": ShellRequest,
    "draft": DraftRequest,
    "mirror": MirrorRequest,
    "box": BoxRequest,
    "cylinder": CylinderRequest,
    "boolean": BooleanRequest,
    "move_body": MoveBodyRequest,
    "copy_body": CopyBodyRequest,
    "mirror_body": MirrorBodyRequest,
    "split": SplitRequest,
    "mate_distance": MateDistanceRequest,
}
"""The adapter method (``DISPATCH``'s first element in the executor) of every op that creates a feature, with the
request model it takes: how an ``edit_feature`` request is rebuilt from JSON."""


def kind_of_request(request: Request) -> str | None:
    """The kind of ``request`` (``"box"`` for a ``BoxRequest``; the operation's own name for the ``ExtensionRequest`` of
    an operation the adapter declared), or ``None`` when it is not a feature request."""
    if type(request) is ExtensionRequest:
        return request.name
    for kind, model in REQUEST_BY_KIND.items():
        if type(request) is model:
            return kind
    return None


def _is_extension_json(kind: object, request: dict[str, object]) -> bool:
    """A request given as JSON is an ``ExtensionRequest`` when it names the operation (``name`` equal to the kind) and
    the kind is not a common one, or is one whose common model has no ``params`` (an adapter may replace a common
    operation by one of its own with the same name)."""
    if request.get("name") != kind:
        return False
    model = REQUEST_BY_KIND.get(kind)  # type: ignore[arg-type]
    return model is None or ("params" in request and "params" not in model.model_fields)


class FeatureEdit(BaseModel, frozen=True):
    """One feature to edit: its handle and the full NEW request of the op that made it (``kind`` names which). The
    feature of an operation the adapter declared is edited with the ``ExtensionRequest`` it was made with and the
    operation's own name as ``kind``."""

    feature: FeatureHandle
    kind: str = Field(
        min_length=1,
        description="The op's adapter method: 'box', 'cylinder', 'move_body', ... or the name of an adapter's own op",
    )
    request: SerializeAsAny[Request]
    sketch: SketchRequest | None = Field(
        default=None,
        description=(
            "The NEW request of the sketch step this feature consumes, when a dimension of that sketch changed (same "
            "entities, constraints and plane; only numbers differ). The adapter sets those dimensions on the sketch "
            "that exists and recomputes; 'request' is the feature's own request as it stands (changed or not)"
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _rebuild_the_typed_request(cls, data: object) -> object:
        """From JSON the request is a dict: give it the model its ``kind`` names (the base ``Request`` would drop every
        field it does not declare)."""
        if isinstance(data, dict) and isinstance(data.get("request"), dict):
            kind = data.get("kind")
            if isinstance(kind, str) and _is_extension_json(kind, data["request"]):
                return {**data, "request": ExtensionRequest.model_validate(data["request"])}
            model = REQUEST_BY_KIND.get(kind) if isinstance(kind, str) else None
            if model is None:
                msg = f"unknown edit kind {kind!r}: expected one of {', '.join(sorted(REQUEST_BY_KIND))}"
                raise ValueError(msg)
            return {**data, "request": model.model_validate(data["request"])}
        return data

    @model_validator(mode="after")
    def _kind_matches_the_request(self) -> "FeatureEdit":
        if self.sketch is not None and self.kind not in SKETCH_CONSUMERS:
            msg = f"edit kind {self.kind!r} does not consume a sketch: only {', '.join(sorted(SKETCH_CONSUMERS))} do"
            raise ValueError(msg)
        if type(self.request) is ExtensionRequest:
            if self.kind != self.request.name:
                msg = f"edit kind {self.kind!r} does not match the operation {self.request.name!r}"
                raise ValueError(msg)
            return self
        model = REQUEST_BY_KIND.get(self.kind)
        if model is None:
            msg = f"unknown edit kind {self.kind!r}: expected one of {', '.join(sorted(REQUEST_BY_KIND))}"
            raise ValueError(msg)
        if type(self.request) is not model:
            msg = f"edit kind {self.kind!r} does not match a {type(self.request).__name__}"
            raise ValueError(msg)
        return self


class EditFeatureRequest(Request):
    """Edit ``edits`` in place, all together (ONE recompute for the batch). ``dry_run`` only asks whether the CAD can
    do it: nothing is touched, ``refused`` carries the reasons and ``recomputed`` what a real edit would recompute."""

    edits: tuple[FeatureEdit, ...] = Field(min_length=1)
    dry_run: bool = False

    @model_validator(mode="after")
    def _distinct_features(self) -> "EditFeatureRequest":
        ids = [edit.feature.id for edit in self.edits]
        if len(set(ids)) != len(ids):
            msg = "a feature appears more than once in the edits"
            raise ValueError(msg)
        return self


EditFlagKind = Literal[
    "body_disappeared",
    "body_appeared",
    "solids_increased",
    "solids_decreased",
    "cut_without_effect",
    "new_interference",
    "placement_drift",
    "shape_invalid",
    "volume_changed",
]


FailureKind = Literal["semantic", "technical"]


class BodyMeasure(BaseModel, frozen=True):
    """What the tools measure of one body, in the same terms for every CAD."""

    volume_mm3: float
    bbox: BoundingBox
    centroid: Vec3 | None = None
    solids: int = Field(ge=0)
    faces: int = Field(ge=0)
    edges: int = Field(ge=0)


class BodyDelta(BaseModel, frozen=True):
    """One body before and after the edit. ``before`` None: it appeared; ``after`` None: it disappeared."""

    body: BodyHandle
    label: str = Field(default="", description="The op that built the body as it stands (its tree name)")
    before: BodyMeasure | None
    after: BodyMeasure | None


class EditFlag(BaseModel, frozen=True):
    """A silent failure the CAD does not report. ``severity`` ``failure``: the check failed and the edit was rolled
    back; ``warning``: the edit stands and the caller decides (a cut that no longer cuts may be what was wanted)."""

    kind: EditFlagKind
    severity: Literal["failure", "warning"]
    message: str
    features: tuple[FeatureHandle, ...] = ()
    bodies: tuple[BodyHandle, ...] = ()
    value: float | None = Field(default=None, description="The measure behind the flag (an interference volume, mm3)")


class HealthIssue(BaseModel, frozen=True):
    """An object the CAD itself reports as invalid, with an error or still touched after the recompute."""

    feature: FeatureHandle | None = Field(
        description="The feature that owns the object, None when it is not registered"
    )
    object: str = Field(description="The CAD's own name for the object")
    state: tuple[str, ...] = ()
    message: str = ""


class RefsDelta(BaseModel, frozen=True):
    """What the recompute did to the faces the harness tracks: ``removed`` handles are STALE from now on."""

    created: tuple[FaceHandle, ...] = ()
    removed: tuple[FaceHandle, ...] = ()
    modified: tuple[FaceHandle, ...] = ()


class EditResult(BaseModel, frozen=True):
    """The outcome of ``edit_feature``.

    * refused (``refused`` not empty): nothing was touched; ``applied`` false, ``rebuild_ok`` true;
    * attempted and failed (``rebuild_ok`` false): rolled back, the document is exactly as before; ``failure`` says why
      and ``health``/``flags`` where;
    * applied: ``applied`` true, ``rebuild_ok`` true; ``flags`` may carry warnings;
    * ``dry_run``: ``applied`` false, nothing touched, ``recomputed`` is what a real edit would recompute.
    """

    rebuild_ok: bool
    applied: bool
    dry_run: bool = False
    recomputed: tuple[FeatureHandle, ...] = ()
    refused: dict[str, str] = Field(default_factory=dict, description="Feature id -> why it cannot be edited in place")
    health: tuple[HealthIssue, ...] = ()
    delta: tuple[BodyDelta, ...] = ()
    flags: tuple[EditFlag, ...] = ()
    refs: RefsDelta = RefsDelta()
    failure: str | None = Field(default=None, description="Why the edit was rolled back (the first failed check)")
    failure_kind: FailureKind | None = Field(
        default=None,
        description=(
            "Set with ``failure``. 'semantic': the checks found the part would break (invalid object, a body that "
            "vanished or split, a shape that is not valid): a rebuild would apply the same change without the checks. "
            "'technical': the edit mechanism's own invariant failed (a placement or an untouched body moved): the "
            "value is not at fault and a rebuild is a sound way to apply it"
        ),
    )
    duration_ms: float = 0.0
    warnings: tuple[str, ...] = ()


class BodyReading(BaseModel, frozen=True):
    """One live body as ``measure_bodies`` reads it: the neutral measures plus whether the CAD calls its shape valid."""

    body: BodyHandle
    label: str = Field(default="", description="The op that built the body as it stands (its tree name)")
    measure: BodyMeasure
    valid: bool = True


class CutReading(BaseModel, frozen=True):
    """How much material one subtraction removes right now (0: its tool misses the body)."""

    feature: FeatureHandle
    label: str = ""
    removed_mm3: float


class OverlapReading(BaseModel, frozen=True):
    """Two live bodies that share volume (a part made of bodies that should only touch has none)."""

    a: BodyHandle
    b: BodyHandle
    volume_mm3: float


class MeasureBodiesResult(BaseModel, frozen=True):
    """The outcome of the optional, READ-ONLY ``measure_bodies``: the checks a rebuild's per-operation steps cannot do
    (they look at their own operation's result, not at the rest of the part) done as one reading of the whole document,
    so the caller can compare a reading before and after a change. ``warnings`` say what could not be measured."""

    bodies: tuple[BodyReading, ...] = ()
    cuts: tuple[CutReading, ...] = ()
    overlaps: tuple[OverlapReading, ...] = ()
    duration_ms: float = 0.0
    warnings: tuple[str, ...] = ()


class BodyInterferenceRequest(Request):
    """The live bodies of the active part to measure against each other (``body_interference``): ``None`` = all."""

    bodies: tuple[BodyHandle, ...] | None = None


class BodyPairReading(BaseModel, frozen=True):
    """Two live bodies measured against each other: ``common_mm3`` the volume they share (0: no overlap) and
    ``distance_mm`` their minimum distance (0: they touch or overlap). Touching is common 0 with distance 0. A value
    that could not be measured is ``None`` and ``note`` says why: it is never reported as 0."""

    a: BodyHandle
    b: BodyHandle
    common_mm3: float | None
    distance_mm: float | None
    note: str = ""


class BodyInterferenceResult(BaseModel, frozen=True):
    """The outcome of the optional, READ-ONLY ``body_interference``: one reading per pair of bodies."""

    pairs: tuple[BodyPairReading, ...] = ()
    warnings: tuple[str, ...] = ()
