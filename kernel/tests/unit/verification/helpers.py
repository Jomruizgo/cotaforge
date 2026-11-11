"""Hand-built descriptor factories shared by the verification and executor tests."""

from typing import TYPE_CHECKING

from cotaforge.contract import (
    Axis,
    BodyHandle,
    BoundingBox,
    ComponentHandle,
    CurveType,
    EdgeHandle,
    EdgeInfo,
    FaceHandle,
    FaceInfo,
    FeatureHandle,
    MassProperties,
    Provenance,
    SurfaceType,
    Vec3,
)

if TYPE_CHECKING:
    from cotaforge.verification.expected import ExpectedComputer

PLATE_BBOX = BoundingBox(min=(0.0, 0.0, 0.0), max=(100.0, 60.0, 8.0))
PLATE_MASS = MassProperties(volume_mm3=48000.0, centroid=(50.0, 30.0, 4.0), surface_mm2=14560.0)


def mass(volume: float, centroid: Vec3 = (0.0, 0.0, 0.0)) -> MassProperties:
    return MassProperties(volume_mm3=volume, centroid=centroid)


def planar_face(
    face_id: str,
    normal: Vec3,
    center: Vec3,
    area: float,
    bbox: BoundingBox,
    created_by: str = "feature:1",
    modified_by: tuple[str, ...] = (),
    component: str | None = None,
) -> FaceInfo:
    return FaceInfo(
        handle=FaceHandle(id=face_id),
        body=BodyHandle(id="body:1"),
        component=ComponentHandle(id=component) if component else None,
        surface=SurfaceType.plane,
        normal=normal,
        center=center,
        area_mm2=area,
        bbox=bbox,
        provenance=Provenance(
            created_by=FeatureHandle(id=created_by), modified_by=tuple(FeatureHandle(id=m) for m in modified_by)
        ),
    )


def cylindrical_face(
    face_id: str,
    axis: Axis,
    radius: float,
    center: Vec3,
    bbox: BoundingBox,
    area: float = 1.0,
    created_by: str = "feature:1",
    component: str | None = None,
) -> FaceInfo:
    return FaceInfo(
        handle=FaceHandle(id=face_id),
        body=BodyHandle(id="body:1"),
        component=ComponentHandle(id=component) if component else None,
        surface=SurfaceType.cylinder,
        axis=axis,
        center=center,
        radius=radius,
        area_mm2=area,
        bbox=bbox,
        provenance=Provenance(created_by=FeatureHandle(id=created_by)),
    )


def line_edge(
    edge_id: str,
    faces: tuple[str, str],
    start: Vec3,
    end: Vec3,
    convex: bool | None = True,
    dihedral: float | None = 90.0,
) -> EdgeInfo:
    length = sum((e - s) ** 2 for s, e in zip(start, end, strict=True)) ** 0.5
    mins = tuple(min(s, e) for s, e in zip(start, end, strict=True))
    maxs = tuple(max(s, e) for s, e in zip(start, end, strict=True))
    return EdgeInfo(
        handle=EdgeHandle(id=edge_id),
        curve=CurveType.line,
        faces=(FaceHandle(id=faces[0]), FaceHandle(id=faces[1])),
        start=start,
        end=end,
        length_mm=length,
        bbox=BoundingBox(min=mins, max=maxs),  # type: ignore[arg-type]
        convex=convex,
        dihedral_deg=dihedral,
    )


def plate_top_face(created_by: str = "feature:1") -> FaceInfo:
    return planar_face(
        "face:top",
        (0.0, 0.0, 1.0),
        (50.0, 30.0, 8.0),
        6000.0,
        BoundingBox(min=(0.0, 0.0, 8.0), max=(100.0, 60.0, 8.0)),
        created_by=created_by,
    )


def plate_faces(created_by: str = "feature:1") -> list[FaceInfo]:
    """The six faces of the 100 x 60 x 8 plate, ids face:top/bottom/xmin/xmax/ymin/ymax."""
    return [
        plate_top_face(created_by),
        planar_face(
            "face:bottom",
            (0.0, 0.0, -1.0),
            (50.0, 30.0, 0.0),
            6000.0,
            BoundingBox(min=(0.0, 0.0, 0.0), max=(100.0, 60.0, 0.0)),
            created_by,
        ),
        planar_face(
            "face:xmin",
            (-1.0, 0.0, 0.0),
            (0.0, 30.0, 4.0),
            480.0,
            BoundingBox(min=(0.0, 0.0, 0.0), max=(0.0, 60.0, 8.0)),
            created_by,
        ),
        planar_face(
            "face:xmax",
            (1.0, 0.0, 0.0),
            (100.0, 30.0, 4.0),
            480.0,
            BoundingBox(min=(100.0, 0.0, 0.0), max=(100.0, 60.0, 8.0)),
            created_by,
        ),
        planar_face(
            "face:ymin",
            (0.0, -1.0, 0.0),
            (50.0, 0.0, 4.0),
            800.0,
            BoundingBox(min=(0.0, 0.0, 0.0), max=(100.0, 0.0, 8.0)),
            created_by,
        ),
        planar_face(
            "face:ymax",
            (0.0, 1.0, 0.0),
            (50.0, 60.0, 4.0),
            800.0,
            BoundingBox(min=(0.0, 60.0, 0.0), max=(100.0, 60.0, 8.0)),
            created_by,
        ),
    ]


PLATE_RECT = {"kind": "rect", "name": "outline", "center": [50, 30], "width": 100, "height": 60}
"""The outline of the 100 x 60 plate whose box is ``PLATE_BBOX``."""


def computer_with_pad(entities: list[dict], depth: float = 8.0) -> "ExpectedComputer":
    """An ``ExpectedComputer`` that has seen a base pad over ``entities`` on XY: the host a pattern or a mirror works on
    (it knows the outline of the material, which is what lets a cut instance be vouched for)."""
    from cotaforge.contract import ExtrudeRequest, PlaneName, SketchHandle, SketchRequest
    from cotaforge.ir.ops_features import ExtrudeOperation, SketchOperation
    from cotaforge.ir.refs import PlaneRef
    from cotaforge.verification.expected import ExpectedComputer
    from cotaforge.verification.observe import StateSnapshot
    from cotaforge.verification.thresholds import Thresholds
    from tests.unit.executor.stub import EMPTY_BBOX, EMPTY_MASS, StubAdapter

    empty = StateSnapshot(mass=EMPTY_MASS, bbox=EMPTY_BBOX)
    computer = ExpectedComputer(Thresholds())
    stub = StubAdapter()
    sketch = SketchOperation(id="base_sketch", intent="i", on=PlaneRef(name="XY"), entities=entities)
    computer.compute(
        sketch, SketchRequest(operation_id="base_sketch", on=PlaneName.XY, entities=sketch.entities), empty, stub
    )
    computer.compute(
        ExtrudeOperation(id="base", intent="i", sketch="base_sketch", depth_mm=depth),
        ExtrudeRequest(operation_id="base", sketch=SketchHandle(id="s"), depth_mm=depth),
        empty,
        stub,
    )
    return computer
