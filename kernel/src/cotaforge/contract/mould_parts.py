"""Mould parts built from a catalogue: the optional ``mould_component`` and ``slide_mechanism`` requests.

Every part of a mould (a plate, a pin, a bush, a ring, a wedge) reaches the CAD as a ``PartSpec``: a frame in the
document, a short list of NATIVE parametric features (a padded sketch, a pocket, a revolution, a sphere) and the
catalogue reference it stands for. The CAD builds each spec as one body of its own with its own sketches, so the
engineer can open any of them and edit a dimension by hand. Nothing here knows a catalogue: the numbers are already
resolved (``cotaforge.mouldparts`` does that), the adapter only draws them.

Coordinates of a feature are in the PART's frame (its own origin and axes); the frame places the part in the document.
A sketch plane is an origin, a normal and an x direction in that frame; its v axis is ``normal x x``. A pad grows along
the normal; a pocket cuts against it (the way a sketch on the outer face of a part cuts into it).
"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from cotaforge.contract.geometry import Vec3
from cotaforge.contract.handles import BodyHandle, FeatureHandle
from cotaforge.contract.requests import Request

__all__ = [
    "AnglePinOption",
    "CatalogInfo",
    "Circle2D",
    "Feature",
    "HelixFeature",
    "PadFeature",
    "PartFrame",
    "PartSpec",
    "PlaneSpec",
    "Poly2D",
    "PocketFeature",
    "Rect2D",
    "RevolveFeature",
    "SlideMechanismRequest",
    "SphereFeature",
    "MouldComponentRequest",
    "WearPlateOption",
]


class _Frozen(BaseModel, frozen=True, allow_inf_nan=False):
    pass


class PartFrame(_Frozen):
    """Where a part sits in the document: its origin, its z axis and the direction its x axis points to."""

    origin: Vec3 = (0.0, 0.0, 0.0)
    z: Vec3 = (0.0, 0.0, 1.0)
    x: Vec3 = (1.0, 0.0, 0.0)


class PlaneSpec(_Frozen):
    origin: Vec3 = (0.0, 0.0, 0.0)
    normal: Vec3 = (0.0, 0.0, 1.0)
    x: Vec3 = (1.0, 0.0, 0.0)


class Circle2D(_Frozen):
    shape: Literal["circle"] = "circle"
    cx: float
    cy: float
    d: float = Field(gt=0)


class Rect2D(_Frozen):
    shape: Literal["rect"] = "rect"
    cx: float
    cy: float
    w: float = Field(gt=0)
    h: float = Field(gt=0)


class Poly2D(_Frozen):
    shape: Literal["poly"] = "poly"
    points: tuple[tuple[float, float], ...] = Field(min_length=3)


Outline = Annotated[Rect2D | Circle2D | Poly2D, Field(discriminator="shape")]


class PadFeature(_Frozen):
    """A sketch of ``outline`` (and ``holes`` inside it) on ``plane``, padded ``depth`` along the plane's normal."""

    kind: Literal["pad"] = "pad"
    name: str
    plane: PlaneSpec = PlaneSpec()
    outline: Outline
    holes: tuple[Circle2D, ...] = ()
    depth: float = Field(gt=0)


class PocketFeature(_Frozen):
    """The ``circles`` or ``rects`` of a sketch on ``plane``, cut ``depth`` against the plane's normal."""

    kind: Literal["pocket"] = "pocket"
    name: str
    plane: PlaneSpec = PlaneSpec()
    circles: tuple[Circle2D, ...] = ()
    rects: tuple[Rect2D, ...] = ()
    depth: float = Field(gt=0)

    @model_validator(mode="after")
    def _something_to_cut(self) -> "PocketFeature":
        if not self.circles and not self.rects:
            msg = "a pocket cuts at least one circle or rectangle"
            raise ValueError(msg)
        return self


class RevolveFeature(_Frozen):
    """A closed profile of ``(r, z)`` points turned a full turn about the part's z axis."""

    kind: Literal["revolve"] = "revolve"
    name: str
    profile: tuple[tuple[float, float], ...] = Field(min_length=3)


class SphereFeature(_Frozen):
    """A ball of ``radius`` centred on ``center``: added to the body, or cut from it when ``subtract``."""

    kind: Literal["sphere"] = "sphere"
    name: str
    center: Vec3
    radius: float = Field(gt=0)
    subtract: bool = False


class HelixFeature(_Frozen):
    """A coil: a circular wire of ``wire_d`` swept along a helix of mean diameter ``coil_d`` about the line through
    ``origin`` along ``axis``, ``height`` long with a ``pitch``."""

    kind: Literal["helix"] = "helix"
    name: str
    origin: Vec3
    axis: Vec3
    coil_d: float = Field(gt=0)
    wire_d: float = Field(gt=0)
    pitch: float = Field(gt=0)
    height: float = Field(gt=0)


Feature = Annotated[
    PadFeature | PocketFeature | RevolveFeature | SphereFeature | HelixFeature, Field(discriminator="kind")
]


class CatalogInfo(_Frozen):
    """What the part is in a manufacturer's catalogue: it becomes properties of the body and the bill of materials."""

    family: str = Field(min_length=1)
    reference: str = Field(min_length=1)
    size: str = ""
    source: str = ""
    note: str = ""

    def line(self) -> str:
        size = f" ({self.size})" if self.size else ""
        return f"{self.family} {self.reference}{size}"


class PartSpec(_Frozen):
    role: str = Field(min_length=1)
    frame: PartFrame = PartFrame()
    features: tuple[Feature, ...] = Field(min_length=1)
    catalog: CatalogInfo | None = None
    volume_mm3: float | None = Field(default=None, gt=0)
    """The volume the nominal geometry has, worked out by hand: the CAD checks the body against it."""


class MouldComponentRequest(Request):
    """Build every part of ``parts`` as a NEW body (native sketches and features, one body per part, labelled
    ``<step>_<role>``). The first body is the step's own."""

    parts: tuple[PartSpec, ...] = Field(min_length=1)

    @field_validator("parts")
    @classmethod
    def _roles_differ(cls, value: tuple[PartSpec, ...]) -> tuple[PartSpec, ...]:
        roles = [p.role for p in value]
        if len(set(roles)) != len(roles):
            msg = "the parts of one step need different roles"
            raise ValueError(msg)
        return value


class AnglePinOption(_Frozen):
    """A row of the angle pin table: stock pin of diameter ``d`` and ``length`` (mm), its head and its item."""

    d: float
    head_d: float
    head_k: float
    n: float
    length: float
    item: str


class WearPlateOption(_Frozen):
    item: str
    thickness: float
    width: float
    length: float


class SlideMechanismRequest(Request):
    """Add the mechanism of the slide ``slide`` (its region number) that the ``mould_split`` step ``split`` made: a
    carrier with guide grooves, two T-gibs, an angle pin leaning ``angle_deg`` from the pull, a locking heel
    ``wedge_extra_deg`` steeper than the pin, a ball detent and a wear plate. The stroke is the depth the slide must
    travel to clear ``part`` plus ``clearance_mm`` (or ``stroke_mm``, which must clear it). The step is refused unless
    the stroke clears the undercut, the heel is steeper than the pin and nothing interferes in the closed or in the open
    position. ``host`` is the half the slide rides in (it is worked out when omitted)."""

    split: FeatureHandle
    slide: int = Field(ge=1)
    part: BodyHandle
    pull: Vec3
    angle_deg: float = Field(gt=0, lt=45)
    stroke_mm: float | None = Field(default=None, gt=0)
    clearance_mm: float = Field(default=2.0, ge=0)
    wedge_extra_deg: float = Field(default=2.0, gt=0, le=10)
    host: Literal["cavity", "core"] | None = None
    pin_diameter_mm: float = Field(default=12.0, gt=0)
    pin_options: tuple[AnglePinOption, ...] = ()
    wear_options: tuple[WearPlateOption, ...] = ()
    catalog_family: str = "DME"
    sources: dict[str, str] = Field(default_factory=dict)

    @field_validator("pull")
    @classmethod
    def _pull_not_zero(cls, value: Vec3) -> Vec3:
        if sum(c * c for c in value) < 1e-12:
            msg = "pull must be a non-zero vector"
            raise ValueError(msg)
        return value
