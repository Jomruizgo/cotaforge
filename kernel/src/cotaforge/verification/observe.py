"""Observation gathered after a successful adapter call.

``StateSnapshot`` is declared here (and re-exported by ``cotaforge.executor.context``) because the
dependency rule forbids ``verification`` from importing ``executor``, while both the executor and the
verifier need it.
"""

from typing import TYPE_CHECKING

from pydantic import BaseModel

from cotaforge.contract import (
    Adapter,
    AdapterError,
    Axis,
    BoundingBox,
    ComponentHandle,
    ComponentRequest,
    DatumAxisResult,
    DatumPlaneResult,
    FaceHandle,
    FaceInfo,
    FaceRequest,
    FacesInfoRequest,
    FlatPattern,
    InterferenceResult,
    MassProperties,
    MassRequest,
    OperationResult,
    Request,
    SketchFrame,
    Transform,
)

if TYPE_CHECKING:
    from cotaforge.ir.ops_primitives import OperationBase


class StateSnapshot(BaseModel, frozen=True):
    """Document state taken before each op."""

    mass: MassProperties
    bbox: BoundingBox
    seed_volume_delta: float | None = None
    component_bbox_before: BoundingBox | None = None
    untracked_bodies: bool = False
    """The document may hold bodies the plan did not build (features adopted from the engineer's hand, inserted
    components): the expectations then cannot count the live bodies and bound a box by the whole document's."""


class Observation(BaseModel, frozen=True):
    mass_after: MassProperties
    bbox_after: BoundingBox
    created_faces: tuple[FaceInfo, ...]
    modified_faces: tuple[FaceInfo, ...]
    # The faces no check reads in detail are described by their handle only (see ``CREATED_FACE_DETAIL_CHECKS``):
    # ``created_faces`` / ``modified_faces`` then stay empty and these hold the handles, so the counts.
    created_face_handles: tuple[FaceHandle, ...] = ()
    modified_face_handles: tuple[FaceHandle, ...] = ()
    instance_count: int | None
    component_bbox: BoundingBox | None
    component_transform: Transform | None
    interference: InterferenceResult | None
    # Added to the architecture's field list: ``volume_delta`` needs the mass before the op.
    mass_before: MassProperties | None = None
    # Added for the ``hole_entry_face_modified`` check:
    # the request's entry face, so level2 can test it against ``modified_faces``.
    hole_entry_face: FaceHandle | None = None
    # The entry face as it stands after the hole (``adapter.face_info``), for ``hole_within_face``: the
    # created cylinder's footprint on that face's plane must lie inside the face's own bounding box. ``None``
    # when the adapter no longer answers for the handle.
    hole_entry_face_info: FaceInfo | None = None
    # ``ref_plane`` / ``ref_axis``: the pose the adapter says it realised (its ``DatumPlaneResult.frame`` /
    # ``DatumAxisResult.axis``), for the ``datum_*`` pose checks. ``None`` for every other op.
    datum_frame: SketchFrame | None = None
    datum_axis: Axis | None = None
    # ``copy_body`` / ``mirror_body``: the new body's own mass properties / bounding box and the source's mass after
    # the op (body-scoped queries; the document-level ones cannot tell a copy from a move). ``None`` elsewhere, and
    # when the adapter reported no created body.
    copy_mass: MassProperties | None = None
    copy_bbox: BoundingBox | None = None
    source_mass_after: MassProperties | None = None
    # ``split``: the own mass properties of each created piece, in ``created_bodies`` order; the side each one claims
    # (``positive`` / ``negative``: both for keep=both, the one kept otherwise) and the parting plane. ``None`` / empty
    # elsewhere, and ``None`` masses when a piece could not be read.
    split_masses: tuple[MassProperties, ...] | None = None
    split_sides: tuple[str, ...] = ()
    split_plane: SketchFrame | None = None
    # ``unfold``: the flat pattern the adapter measured on the pattern itself. ``None`` elsewhere.
    flat_pattern: FlatPattern | None = None


CREATED_FACE_DETAIL_CHECKS = frozenset(
    {
        "hole_axis_direction",
        "hole_axis_position",
        "hole_radius",
        "hole_within_face",
        "cylinder_axis_direction",
        "cylinder_axis_position",
        "cylinder_radius",
        "fillet_radius",
        "cut_extent_along_n",
        "draft_angle",
    }
)
"""Checks of ``level2`` that read the surface, axis, radius or box of the faces an op created. Any other check needs
only how many there are (``created_faces_count``) or which handle (``hole_entry_face_modified``). Keep in step with
``level2``: a check that starts reading ``Observation.created_faces`` must be listed here."""
MODIFIED_FACE_DETAIL_CHECKS = frozenset({"draft_angle"})
"""Same, for the faces the op modified."""
HOLE_ENTRY_FACE_CHECKS = frozenset({"hole_within_face"})
"""Checks that read the entry face of a hole as it stands after the hole."""


ASSEMBLY_OPS = frozenset(
    {
        "insert_component",
        "fix_component",
        "place_component",
        "add_joint",
        "set_joint",
        "mate_concentric",
        "mate_coincident",
        "mate_distance",
    }
)


def mass_request(operation_id: str, op_name: str) -> MassRequest:
    """The mass an op's checks read. An assembly op checks the volume and the box (the pose of a component moves its
    centre, nothing else of it changes), so the centroid and the surface area -- a pass over every solid and every face
    of the components, hundreds of solids for a vendor assembly -- are not asked for."""
    light = op_name in ASSEMBLY_OPS
    return MassRequest(operation_id=operation_id, surface=not light, centroid=not light)


def box_request(operation_id: str, op_name: str) -> MassRequest:
    """The box an op's checks read. An assembly op compares the box of a component with the part's or with its own
    before the step, so the quick box is enough (and it is the same kind of box on both sides); the exact one is a pass
    over every curved surface of the bodies, and ``inspect measure`` gives it."""
    return MassRequest(operation_id=operation_id, exact_box=op_name not in ASSEMBLY_OPS)


def _component_of(
    op: "OperationBase", result: OperationResult, request: Request | None, adapter: Adapter
) -> ComponentHandle | None:
    if result.component is not None:
        return result.component
    if request is None:
        return None
    if op.op in ("fix_component", "place_component"):
        return getattr(request, "component", None)
    if op.op in ("mate_concentric", "mate_coincident", "mate_distance"):
        face_b = getattr(request, "b", None)
        if face_b is None:
            return None
        return adapter.face_info(FaceRequest(operation_id=op.id, face=face_b)).component
    return None


def _describe(adapter: Adapter, operation_id: str, faces: tuple[FaceHandle, ...]) -> tuple[FaceInfo, ...]:
    """The faces described in one batch (the adapter answers them in one round trip when it can)."""
    if not faces:
        return ()
    return adapter.faces_info(FacesInfoRequest(operation_id=operation_id, faces=faces)).faces


def gather_observation(
    adapter: Adapter,
    op: "OperationBase",
    result: OperationResult,
    snapshot: StateSnapshot,
    request: Request | None = None,
    raw_result: object
    | None = None,  # raw adapter return of the dispatch; interference and datum results are consumed here
    check_names: frozenset[str] | None = None,
) -> Observation:
    """Query everything the Level 2 checks need; read-only adapter calls.

    ``check_names`` are the names of the checks that will read the observation: a face is described (one adapter call
    each) only for the checks that read it in detail. ``None`` describes every created and modified face.

    ``raw_result`` is the untouched return value of the single dispatch call the executor already
    made (``executor._call``). It must be reused instead of re-invoking construction/assembly
    adapter methods here, which would desync the fake adapter's fault-engine attempt counter and
    could re-enter a fault that is not guarded by the executor's attempt try/except.
    """
    mass_after = adapter.mass_properties(mass_request(op.id, op.op))
    bbox_after = adapter.bounding_box(box_request(op.id, op.op))
    created_faces: tuple[FaceInfo, ...] = ()
    modified_faces: tuple[FaceInfo, ...] = ()
    if check_names is None or check_names & CREATED_FACE_DETAIL_CHECKS:
        created_faces = _describe(adapter, op.id, result.created_faces)
    if check_names is None or check_names & MODIFIED_FACE_DETAIL_CHECKS:
        modified_faces = _describe(adapter, op.id, result.modified_faces)

    component_bbox: BoundingBox | None = None
    component_transform: Transform | None = None
    if op.op in ASSEMBLY_OPS:
        component = _component_of(op, result, request, adapter)
        if component is not None:
            component_bbox = adapter.component_bounding_box(ComponentRequest(operation_id=op.id, component=component))
            component_transform = adapter.component_transform(ComponentRequest(operation_id=op.id, component=component))

    interference: InterferenceResult | None = None
    if op.op == "check_interference" and isinstance(raw_result, InterferenceResult):
        interference = raw_result

    datum_frame = raw_result.frame if isinstance(raw_result, DatumPlaneResult) else None
    datum_axis = raw_result.axis if isinstance(raw_result, DatumAxisResult) else None

    hole_entry_face: FaceHandle | None = None
    hole_entry_face_info: FaceInfo | None = None
    if op.op == "hole" and request is not None:
        hole_entry_face = getattr(request, "face", None)
        if hole_entry_face is not None and (check_names is None or check_names & HOLE_ENTRY_FACE_CHECKS):
            try:
                hole_entry_face_info = adapter.face_info(FaceRequest(operation_id=op.id, face=hole_entry_face))
            except AdapterError:
                hole_entry_face_info = None

    copy_mass: MassProperties | None = None
    copy_bbox: BoundingBox | None = None
    source_mass_after: MassProperties | None = None
    if op.op in ("copy_body", "mirror_body") and request is not None:
        source = getattr(request, "source", None)
        try:
            if source is not None:
                source_mass_after = adapter.mass_properties(MassRequest(operation_id=op.id, body=source))
            if result.created_bodies:
                copy = result.created_bodies[0]
                copy_mass = adapter.mass_properties(MassRequest(operation_id=op.id, body=copy))
                copy_bbox = adapter.bounding_box(MassRequest(operation_id=op.id, body=copy))
        except AdapterError:
            pass  # left unobserved: the body-scoped checks then fail with "not observed", not crash the step

    split_masses: tuple[MassProperties, ...] | None = None
    split_sides: tuple[str, ...] = ()
    split_plane: SketchFrame | None = None
    if op.op == "split" and request is not None:
        keep = getattr(request, "keep", "both")
        split_sides = ("positive", "negative") if keep == "both" else (keep,)
        split_plane = getattr(request, "plane", None)
        try:
            split_masses = tuple(
                adapter.mass_properties(MassRequest(operation_id=op.id, body=piece)) for piece in result.created_bodies
            )
        except AdapterError:
            split_masses = None  # left unobserved: the piece checks then fail with "not observed", not crash the step

    return Observation(
        mass_after=mass_after,
        bbox_after=bbox_after,
        created_faces=created_faces,
        modified_faces=modified_faces,
        created_face_handles=result.created_faces,
        modified_face_handles=result.modified_faces,
        instance_count=result.instance_count,
        component_bbox=component_bbox,
        component_transform=component_transform,
        interference=interference,
        mass_before=snapshot.mass,
        hole_entry_face=hole_entry_face,
        hole_entry_face_info=hole_entry_face_info,
        datum_frame=datum_frame,
        datum_axis=datum_axis,
        copy_mass=copy_mass,
        copy_bbox=copy_bbox,
        source_mass_after=source_mass_after,
        split_masses=split_masses,
        split_sides=split_sides,
        split_plane=split_plane,
        flat_pattern=result.flat_pattern,
    )
