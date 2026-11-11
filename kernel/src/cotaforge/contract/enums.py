"""Enumerations shared across the contract, IR, executor, verification and adapters.

All enums are ``str, Enum`` with values equal to the lowercase member name, .
"""

from enum import Enum


class SurfaceType(str, Enum):  # noqa: UP042
    plane = "plane"
    cylinder = "cylinder"
    cone = "cone"
    sphere = "sphere"
    torus = "torus"
    other = "other"


class CurveType(str, Enum):  # noqa: UP042
    line = "line"
    circle = "circle"
    arc = "arc"
    other = "other"


class PlaneName(str, Enum):  # noqa: UP042
    XY = "XY"
    YZ = "YZ"
    XZ = "XZ"


class AxisName(str, Enum):  # noqa: UP042
    X = "X"
    Y = "Y"
    Z = "Z"


class Direction(str, Enum):  # noqa: UP042
    positive = "positive"
    negative = "negative"


class EndCondition(str, Enum):  # noqa: UP042
    blind = "blind"
    through_all = "through_all"
    up_to_face = "up_to_face"
    """Hole only: ends at the plane of another face (``PartDesign::Hole`` has no such depth type, so the adapter
    measures the distance to that face and drills it blind)."""


class DrillPoint(str, Enum):  # noqa: UP042
    """The bottom of a blind hole (FreeCAD's ``DrillPoint``: ``Flat`` or ``Angled``)."""

    flat = "flat"
    angled = "angled"


class HoleStandard(str, Enum):  # noqa: UP042
    simple = "simple"
    iso_clearance = "iso_clearance"
    iso_tapped = "iso_tapped"


class MateAlignment(str, Enum):  # noqa: UP042
    aligned = "aligned"
    anti_aligned = "anti_aligned"


class JointType(str, Enum):  # noqa: UP042
    fixed = "fixed"
    revolute = "revolute"
    slider = "slider"


class Side(str, Enum):  # noqa: UP042
    positive = "positive"
    negative = "negative"


class BooleanKind(str, Enum):  # noqa: UP042
    union = "union"
    subtract = "subtract"
    intersect = "intersect"


class DocumentKind(str, Enum):  # noqa: UP042
    part = "part"
    assembly = "assembly"


class ProvenanceRole(str, Enum):  # noqa: UP042
    created = "created"
    modified = "modified"
    any = "any"


class ErrorCode(str, Enum):  # noqa: UP042
    not_supported = "not_supported"
    invalid_request = "invalid_request"
    invalid_reference = "invalid_reference"
    geometry_failed = "geometry_failed"
    rebuild_failed = "rebuild_failed"
    timeout = "timeout"
    session_lost = "session_lost"
    no_active_document = "no_active_document"
    degraded_session = "degraded_session"
    mate_failed = "mate_failed"
    internal = "internal"
