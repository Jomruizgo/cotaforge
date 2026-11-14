"""Read-only geometry descriptors returned by provenance and query methods (contract/descriptors.py)."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from cotaforge.contract.enums import CurveType, SurfaceType
from cotaforge.contract.geometry import Axis, BoundingBox, Mat3, Vec3
from cotaforge.contract.handles import BodyHandle, ComponentHandle, EdgeHandle, FaceHandle, FeatureHandle, VertexHandle


class Provenance(BaseModel, frozen=True):
    created_by: FeatureHandle
    modified_by: tuple[FeatureHandle, ...] = ()


class FaceInfo(BaseModel, frozen=True):
    handle: FaceHandle
    body: BodyHandle
    component: ComponentHandle | None = None
    surface: SurfaceType
    normal: Vec3 | None = None
    axis: Axis | None = None
    center: Vec3
    radius: float | None = None
    minor_radius: float | None = None
    """A torus: the radius of its tube (``radius`` is the bend radius); the root fillet of a round edge is one."""
    area_mm2: float
    bbox: BoundingBox
    provenance: Provenance


class EdgeInfo(BaseModel, frozen=True):
    handle: EdgeHandle
    curve: CurveType
    faces: tuple[FaceHandle, FaceHandle]
    start: Vec3 | None = None
    end: Vec3 | None = None
    center: Vec3 | None = None
    axis: Axis | None = None
    radius: float | None = None
    length_mm: float
    bbox: BoundingBox
    convex: bool | None = None
    dihedral_deg: float | None = None


class VertexInfo(BaseModel, frozen=True):
    handle: VertexHandle
    point: Vec3
    edges: tuple[EdgeHandle, ...]


BodyKind = Literal["solid", "surface"]
"""What a body is. An adapter that says nothing about surfaces yields solids."""


class BodyInfo(BaseModel, frozen=True):
    handle: BodyHandle
    kind: BodyKind = "solid"
    volume_mm3: float | None
    """None for a surface body: it has no volume, and 0 would read as a measurement."""
    area_mm2: float | None = None
    """Area of a surface body (None for a solid: its faces are measured by MassProperties.surface_mm2)."""
    bbox: BoundingBox
    face_count: int
    provenance: Provenance
    bbox_faces_left_out: int = 0
    mass_kg: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    """The mass of this body as the CAD measured it with its own material; ``None`` when the CAD does not say (then the
    server computes it from the volume and the density of the declared material)."""
    mass_source: str = ""
    """Who measured ``mass_kg`` (``"ExternalCAD"``): required with it, shown to the agent."""
    catalog: str = ""
    """The manufacturer's reference this body stands for (``"DME 5104GL (guide pin GL, ...)"``), read from the properties of
    the body itself; empty for a body the engineer or the agent drew."""

    @model_validator(mode="after")
    def _mass_has_a_source(self) -> "BodyInfo":
        if self.mass_kg is not None and not self.mass_source.strip():
            msg = "mass_kg needs mass_source: the agent is told who measured it"
            raise ValueError(msg)
        return self


class MassProperties(BaseModel, frozen=True):
    volume_mm3: float
    centroid: Vec3
    centroid_meaningful: bool = True
    """False when nothing has volume (a document of surfaces only): the centroid is the origin by default, not a
    measure."""
    surface_mm2: float | None = None
    inertia: Mat3 | None = None
    area_left_out: tuple[str, ...] = ()
    """Bodies (or components) whose surface area is left out of ``surface_mm2`` because the CAD returned a number that
    cannot be an area (not finite, or vastly above what the shape's bounding box allows: an invalid shape)."""
    surface_only: tuple[str, ...] = ()
    """Bodies that hold no solid (loose faces, open shells): they add no volume to ``volume_mm3``."""
    mass_kg: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    """The mass in kg as the CAD measured it with the material it knows; ``None`` when it does not say. When present the
    agent reads this value (``inspect``, the bill of materials, the reply) instead of one computed from the volume and a
    density of the server's own."""
    mass_source: str = ""
    """Who measured ``mass_kg`` (``"ExternalCAD"``): required with it, shown to the agent."""

    @model_validator(mode="after")
    def _mass_has_a_source(self) -> "MassProperties":
        if self.mass_kg is not None and not self.mass_source.strip():
            msg = "mass_kg needs mass_source: the agent is told who measured it"
            raise ValueError(msg)
        return self


class FeatureNode(BaseModel, frozen=True):
    handle: FeatureHandle
    kind: str
    name: str
    parameters: dict[str, float | str | bool]
    named_dimensions: dict[str, float] = {}
    parent: FeatureHandle | None = None
