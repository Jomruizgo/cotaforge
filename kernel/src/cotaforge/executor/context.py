"""ExecutionContext and StateSnapshot."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from cotaforge.contract import (
    Axis,
    BodyHandle,
    ComponentHandle,
    DatumPlaneTarget,
    DocumentHandle,
    FeatureHandle,
    JointHandle,
    SketchEntity,
    SketchFrame,
    SketchHandle,
)
from cotaforge.verification.observe import StateSnapshot

__all__ = ["ExecutionContext", "StateSnapshot"]


@dataclass
class ExecutionContext:
    document: DocumentHandle
    documents: Mapping[str, DocumentHandle] = field(default_factory=dict)
    feature_by_op: dict[str, FeatureHandle] = field(default_factory=dict)
    sketch_by_op: dict[str, SketchHandle] = field(default_factory=dict)
    sketch_frame_by_op: dict[str, SketchFrame] = field(default_factory=dict)
    sketch_entities_by_op: dict[str, tuple[SketchEntity, ...]] = field(default_factory=dict)
    body_by_op: dict[str, BodyHandle] = field(default_factory=dict)
    body_sides: dict[str, dict[str, BodyHandle]] = field(default_factory=dict)
    """``split`` op id -> the pieces it made by side (``positive`` / ``negative``): only those it kept. A ref with a
    ``BodyRef.side`` resolves here; ``body_by_op`` holds the first kept piece for a ref without one."""
    component_by_op: dict[str, ComponentHandle] = field(default_factory=dict)
    joint_by_op: dict[str, JointHandle] = field(default_factory=dict)
    """``add_joint`` op id -> the joint it made (``set_joint`` and the sweep name a joint by that step)."""
    datum_plane_by_op: dict[str, DatumPlaneTarget] = field(default_factory=dict)
    """``ref_plane`` op id -> the datum handle plus the frame the executor computed for it."""
    datum_axis_by_op: dict[str, Axis] = field(default_factory=dict)
    """``ref_axis`` op id -> the axis the executor computed for it."""
    place_by_op: dict[str, SketchFrame] = field(default_factory=dict)
    """``hole`` / ``sketch`` op id -> the frame on its planar face that the CAD built it in (its origin is where the
    coordinates of the op are measured from: the centre of the face when it was built)."""
    held_places: dict[str, SketchFrame] = field(default_factory=dict)
    """The places a rebuild keeps (``Executor.hold_places``): an op named here is built in the frame it had, not in
    the one of the centre of its face as the face is now."""
