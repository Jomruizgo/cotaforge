"""Assembly requests and responses (contract/assembly.py)."""

from pydantic import BaseModel, Field

from cotaforge.contract.enums import JointType, MateAlignment, Side
from cotaforge.contract.geometry import Axis, Transform
from cotaforge.contract.handles import ComponentHandle, DocumentHandle, FaceHandle, JointHandle
from cotaforge.contract.requests import Request
from cotaforge.contract.responses import JointInfo

MAX_SWEEP_POSITIONS = 721
"""The most positions one joint sweep measures (0 to 360 degrees every 0.5 degree); a longer one is refused."""


class InsertComponentRequest(Request):
    part: DocumentHandle
    transform: Transform


class FixComponentRequest(Request):
    component: ComponentHandle


class PlaceComponentRequest(Request):
    """Set the pose of a component in assembly space: transform replaces its placement."""

    component: ComponentHandle
    transform: Transform


class AddJointRequest(Request):
    """Join component ``b`` to component ``a`` (``None``: the ground of the assembly). The joint is made at the pose
    ``b`` has now, which is its value 0. ``axis`` (assembly axes, as things are now) is the rotation axis of a revolute
    joint and the direction of a slider; a fixed joint has none. The value runs from ``min_value`` to ``max_value``
    (degrees of a revolute, mm of a slider); ``None`` is unlimited."""

    joint_type: JointType
    a: ComponentHandle | None = None
    b: ComponentHandle
    axis: Axis | None = None
    min_value: float | None = None
    max_value: float | None = None


class SetJointRequest(Request):
    """Move the joint to ``value`` (absolute, from the pose it was made at): ``b`` is placed by the joint."""

    joint: JointHandle
    value: float


class JointRequest(Request):
    joint: JointHandle


class SweepJointRequest(Request):
    """Drive a joint from ``start`` to ``end`` every ``step`` and measure the components that move against the rest at
    each position. Read-only: the joint is back at its value afterwards."""

    joint: JointHandle
    start: float
    end: float
    step: float = Field(gt=0)


def sweep_values(start: float, end: float, step: float) -> list[float]:
    """The positions of a sweep: ``start``, then every ``step`` towards ``end`` and ``end`` itself (the last interval
    may be shorter). One list for every adapter, so the count is the same everywhere."""
    span = abs(end - start)
    count = int(span / step + 1e-9)
    sign = 1.0 if end >= start else -1.0
    values = [round(start + sign * step * i, 9) for i in range(count + 1)]
    if span - count * step > 1e-9:
        values.append(float(end))
    return values


class MateConcentricRequest(Request):
    a: FaceHandle
    b: FaceHandle
    alignment: MateAlignment = MateAlignment.aligned
    flip: bool = False


class MateCoincidentRequest(Request):
    a: FaceHandle
    b: FaceHandle
    alignment: MateAlignment = MateAlignment.anti_aligned
    flip: bool = False


class MateDistanceRequest(Request):
    a: FaceHandle
    b: FaceHandle
    distance_mm: float = Field(ge=0)
    alignment: MateAlignment = MateAlignment.anti_aligned
    side: Side = Side.positive
    flip: bool = False


class InterferenceRequest(Request):
    components: tuple[ComponentHandle, ...] | None = None


class ComponentRequest(Request):
    component: ComponentHandle


class InterferencePair(BaseModel, frozen=True):
    """Two components measured against each other: the volume they share and, when the adapter measures it, their
    minimum distance (0: they touch or overlap). A distance that could not be measured is None and note says
    why: it is never reported as 0."""

    a: ComponentHandle
    b: ComponentHandle
    volume_mm3: float
    distance_mm: float | None = None
    note: str = ""


class InterferenceResult(BaseModel, frozen=True):
    pairs: tuple[InterferencePair, ...]
    total_volume_mm3: float


class JointSweepClash(BaseModel, frozen=True):
    """The first position of a sweep where two components share volume (the pair with the most volume there)."""

    value: float
    a: ComponentHandle
    b: ComponentHandle
    volume_mm3: float


class JointSweepPair(BaseModel, frozen=True):
    """One moving component against one that stays, over the positions measured: the first position where they overlap
    (``None``: never at a measured position), the least distance between them up to that position (all of them when
    they never overlap) and where (``None``: they overlap from the first position), and how many positions could not
    be measured."""

    a: ComponentHandle
    b: ComponentHandle
    min_distance_mm: float | None = None
    at_value: float | None = None
    first_overlap_value: float | None = None
    unmeasured: int = 0


class JointSweepResult(BaseModel, frozen=True):
    joint: JointInfo
    start: float
    end: float
    step: float
    positions: int
    first_clash: JointSweepClash | None = None
    pairs: tuple[JointSweepPair, ...] = ()
    notes: tuple[str, ...] = ()


def joint_delta(joint_type: JointType, axis: Axis | None, value: float) -> Transform:
    """The motion a joint makes at ``value`` (degrees of a revolute about ``axis``, right-handed; mm of a slider along
    ``axis.direction``), in assembly axes: the pose of the joined component at that value is ``joint_delta(...)``
    composed after its pose at value 0. One definition for the adapters that compute it and the checks that verify
    it."""
    if joint_type is JointType.fixed or axis is None:
        return Transform.identity()
    if joint_type is JointType.slider:
        d = axis.direction
        return Transform(origin=(d[0] * value, d[1] * value, d[2] * value))
    rotation = Transform.from_axis_angle(axis.direction, value).rotation
    o = axis.origin
    turned = Transform(rotation=rotation).apply_direction(o)
    return Transform(origin=(o[0] - turned[0], o[1] - turned[1], o[2] - turned[2]), rotation=rotation)
