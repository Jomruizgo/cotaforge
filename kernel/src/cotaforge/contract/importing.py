"""Foreign geometry: the optional ``import_geometry`` tool and the optional, read-only ``list_holes`` query.

A customer delivers a part as STEP or IGES. ``import_geometry`` brings it into a NEW document of the CAD as bodies that
are REFERENCES: no feature history, so nothing about them is editable by an op, but they can be measured, looked at,
inserted in an assembly and designed around. The file's coordinates are kept exactly (a vehicle part is positioned in
vehicle coordinates and must stay there) and its length unit is converted to millimetres by the CAD.
"""

from pydantic import BaseModel, Field

from cotaforge.contract.geometry import Axis, BoundingBox
from cotaforge.contract.handles import BodyHandle, FaceHandle, FeatureHandle
from cotaforge.contract.requests import Request
from cotaforge.contract.responses import DocumentInfo

__all__ = [
    "DEFAULT_SEW_TOLERANCE_MM",
    "MAX_SEW_TOLERANCE_MM",
    "HoleInfo",
    "ImportGeometryRequest",
    "ImportGeometryResult",
    "ImportedBody",
    "ListHolesRequest",
    "ListHolesResult",
    "SewingReport",
]

DEFAULT_SEW_TOLERANCE_MM = 0.01
MAX_SEW_TOLERANCE_MM = 10.0


class ImportGeometryRequest(Request):
    """Read the STEP or IGES file at ``path`` (absolute, same operating system as the adapter) into a NEW document
    called ``name``, which becomes the active document. Nothing is saved: the caller does that. Errors (no new
    ``ErrorCode``): ``invalid_request`` for a missing file, an unsupported format (``details.reason=format``) or a path
    the CAD cannot handle; ``internal`` with ``details.reason=unreadable`` when the CAD cannot read the file; and
    ``details.reason=empty`` when the file holds no geometry at all. ``sew`` joins the loose faces of the file into
    shells (edges closer than ``sew_tolerance_mm``) and makes a solid of every closed shell."""

    path: str = Field(min_length=1)
    name: str = Field(min_length=1)
    sew: bool = False
    sew_tolerance_mm: float = Field(default=DEFAULT_SEW_TOLERANCE_MM, gt=0, le=MAX_SEW_TOLERANCE_MM)


class ImportedBody(BaseModel, frozen=True):
    """One body of the imported file as the CAD measured it. A body holds ONE solid, or (``solids`` 0) loose shells and
    faces whose volume is not meaningful. ``valid``: the CAD calls its shape valid (its cheap check; the deep one is
    ``ValidityRequest(deep=True)``), or ``None`` when it was not checked (a body of thousands of loose faces: the check
    takes longer than reading the file; the deep request does it); ``closed``: it is a solid whose shells are all
    closed (the volume can be trusted).
    ``feature`` is the ``adopted`` feature record that owns the body, when the adapter says (one answer instead of a
    ``bodies_of`` query per body of a large file)."""

    body: BodyHandle
    feature: FeatureHandle | None = None
    label: str = ""
    solids: int = Field(ge=0)
    shells: int = Field(ge=0)
    faces: int = Field(ge=0)
    volume_mm3: float
    bbox: BoundingBox
    bbox_exact: bool = True
    bbox_faces_left_out: int = Field(default=0, ge=0)
    valid: bool | None
    closed: bool


class SewingReport(BaseModel, frozen=True):
    """What sewing the loose faces of a file into shells and solids (``ImportGeometryRequest.sew``) came to: the faces
    that went in, the shells that came out (``closed_shells`` of them closed, each made a solid body; the rest stay
    together in one surface body) and the free edges -- gaps wider than ``tolerance_mm`` -- left in what is not a closed
    solid."""

    tolerance_mm: float
    faces: int = Field(ge=0)
    shells: int = Field(ge=0)
    closed_shells: int = Field(ge=0)
    solids: int = Field(ge=0)
    free_edges: int = Field(ge=0)
    seconds: float = Field(default=0.0, ge=0)


class ImportGeometryResult(BaseModel, frozen=True):
    """The new document and its bodies, one per solid (a STEP assembly gives several) and ONE for everything that is not
    a solid (loose faces and shells, a body with ``solids`` 0), in the file's coordinates. ``source_units`` is the
    length unit the FILE declared when it can be told (``mm``, ``inch``...), ``None`` otherwise; the geometry is in
    millimetres either way. ``notes`` say how the file was read when that is not the usual way; ``sewing`` is set when
    ``ImportGeometryRequest.sew`` asked for it."""

    document: DocumentInfo
    bodies: tuple[ImportedBody, ...] = ()
    source_format: str = ""
    source_units: str | None = None
    warnings: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    sewing: SewingReport | None = None


class ListHolesRequest(Request):
    """The holes of one body, or of every live body of the active part (``None``)."""

    body: BodyHandle | None = None


class HoleInfo(BaseModel, frozen=True):
    """A cylindrical hole: concave cylindrical faces (material outside the cylinder) on one axis, joined when a hole is
    made of several faces (two half cylinders). ``depth_mm`` is its length along the axis. ``through``: both ends open
    (``True``), one end closed by a bottom (``False``, blind) or not determinable (``None``). ``axis.origin`` is the
    centre of the open end of a blind hole (the direction runs into the material), otherwise of the end with the lower
    coordinate; ``axis.direction`` is a unit vector. ``fitted``: the faces are not analytic cylinders (B-spline surfaces
    of some exporters) and were recognised by fitting a cylinder to them; ``fit_deviation_mm`` is the largest radial
    distance of the sampled surface from the fitted cylinder, so diameter and axis are good to about that."""

    body: BodyHandle
    diameter_mm: float
    axis: Axis
    depth_mm: float
    through: bool | None = None
    faces: tuple[FaceHandle, ...] = ()
    arc_deg: float = Field(default=360.0, gt=0, le=360.0 + 1e-6, description="Angle of the circle the faces cover")
    fitted: bool = False
    fit_deviation_mm: float | None = Field(default=None, ge=0)
    countersink_diameter_mm: float | None = Field(default=None, gt=0)
    """A countersink at an open end of the hole (a cone that widens outward from it): its diameter at the entry face and
    its included angle. A counterbore is not here: it is a second, wider hole on the same axis."""
    countersink_angle_deg: float | None = Field(default=None, gt=0, lt=180)


class ListHolesResult(BaseModel, frozen=True):
    holes: tuple[HoleInfo, ...] = ()
    warnings: tuple[str, ...] = ()
