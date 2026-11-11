"""Scripted StubAdapter for the plate plan (tests/conftest.py plate_plan) — the executor's happy path."""

import math

from cotaforge.contract import (
    Axis,
    BodyHandle,
    BoundingBox,
    EdgeHandle,
    FaceHandle,
    FeatureHandle,
    MassProperties,
    OperationResult,
    PlaneName,
    SketchHandle,
    SketchResult,
    frame_for_plane,
)
from tests.unit.verification.helpers import PLATE_BBOX, PLATE_MASS, cylindrical_face, plate_faces

from .stub import Step, StubAdapter

HOLE_VOLUME = math.pi * 3.3**2 * 8.0  # 273.6955...


def hole_face(face_id: str, x: float, y: float, created_by: str) -> object:
    return cylindrical_face(
        face_id,
        Axis(origin=(x, y, 8.0), direction=(0.0, 0.0, -1.0)),
        3.3,
        (x, y, 4.0),
        BoundingBox(min=(x - 3.3, y - 3.3, 0.0), max=(x + 3.3, y + 3.3, 8.0)),
        area=2 * math.pi * 3.3 * 8.0,
        created_by=created_by,
    )


def sketch_step() -> Step:
    return Step(
        result=SketchResult(
            sketch=SketchHandle(id="sk:1"), frame=frame_for_plane(PlaneName.XY), profile_areas_mm2=(6000.0,)
        )
    )


def extrude_step() -> Step:
    return Step(
        result=OperationResult(
            operation_id="base",
            feature=FeatureHandle(id="feature:1"),
            rebuild_ok=True,
            created_faces=tuple(FaceHandle(id=f"face:{n}") for n in ("top", "bottom", "xmin", "xmax", "ymin", "ymax")),
            created_edges=(EdgeHandle(id="edge:1"),),
            created_bodies=(BodyHandle(id="body:1"),),
        ),
        mass_after=PLATE_MASS,
        bbox_after=PLATE_BBOX,
    )


def hole_step(volume_factor: float = 1.0, rebuild_ok: bool = True) -> Step:
    return Step(
        result=OperationResult(
            operation_id="hole_1",
            feature=FeatureHandle(id="feature:2"),
            rebuild_ok=rebuild_ok,
            created_faces=(FaceHandle(id="face:cyl"),),
            modified_faces=(FaceHandle(id="face:top"),),
        ),
        mass_after=MassProperties(volume_mm3=48000.0 - HOLE_VOLUME * volume_factor, centroid=(50.2, 30.1, 4.0)),
        bbox_after=PLATE_BBOX,
    )


def pattern_step(instances: int = 4) -> Step:
    return Step(
        result=OperationResult(
            operation_id="holes",
            feature=FeatureHandle(id="feature:3"),
            rebuild_ok=True,
            created_faces=tuple(FaceHandle(id=f"face:cyl{i}") for i in range(1, instances)),
            instance_count=instances,
        ),
        mass_after=MassProperties(volume_mm3=48000.0 - instances * HOLE_VOLUME, centroid=(50.0, 30.0, 4.0)),
        bbox_after=PLATE_BBOX,
    )


def plate_stub() -> StubAdapter:
    """Provenance tables + a one-attempt happy-path script for every op of the plate plan."""
    stub = StubAdapter()
    for face in plate_faces("feature:1"):
        stub.add_face(face, feature_id="feature:1")
    top = stub.faces["face:top"]
    stub.faces["face:top"] = top.model_copy(
        update={"provenance": top.provenance.model_copy(update={"modified_by": (FeatureHandle(id="feature:2"),)})}
    )
    stub.faces_of_feature["feature:1"] = [stub.faces[f.handle.id] for f in stub.faces_of_feature["feature:1"]]
    stub.add_face(hole_face("face:cyl", 10.0, 10.0, "feature:2"), feature_id="feature:2")  # type: ignore[arg-type]
    stub.faces_of_feature["feature:2"].append(stub.faces["face:top"])
    for i, (x, y) in enumerate(((90.0, 10.0), (10.0, 50.0), (90.0, 50.0)), start=1):
        stub.add_face(hole_face(f"face:cyl{i}", x, y, "feature:3"), feature_id="feature:3")  # type: ignore[arg-type]
    from tests.unit.verification.helpers import line_edge

    stub.add_edge(line_edge("edge:1", ("face:top", "face:xmax"), (100.0, 0.0, 8.0), (100.0, 60.0, 8.0)))
    stub.script("base_sketch", sketch_step())
    stub.script("base", extrude_step())
    stub.script("hole_1", hole_step())
    stub.script("holes", pattern_step())
    return stub
