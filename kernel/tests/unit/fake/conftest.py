"""Shared helpers for the fake adapter tests: small part recipes built through the public contract."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from cotaforge.adapters.fake import FakeAdapter, FaultPlan
from cotaforge.contract import (
    CircleEntity,
    ConnectRequest,
    DocumentKind,
    EdgeInfo,
    EdgesBetweenRequest,
    ExtrudeRequest,
    FaceHandle,
    FaceInfo,
    FacesResult,
    FeatureHandle,
    FeatureRequest,
    LineEntity,
    NewDocumentRequest,
    OperationResult,
    PlaneName,
    ProvenanceRole,
    RectEntity,
    SketchRequest,
    SurfaceType,
    Vec3,
)

Point2 = tuple[float, float]


def close(a: float, b: float, tol: float = 1e-6) -> bool:
    return abs(a - b) <= tol


def vec_close(a: Vec3, b: Vec3, tol: float = 1e-6) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b, strict=True))


def face_with_normal(faces: FacesResult | tuple[FaceInfo, ...], normal: Vec3) -> FaceInfo:
    items = faces.faces if isinstance(faces, FacesResult) else faces
    matches = [
        f for f in items if f.surface is SurfaceType.plane and f.normal is not None and vec_close(f.normal, normal)
    ]
    assert len(matches) == 1, f"expected one face with normal {normal}, got {len(matches)}"
    return matches[0]


def cylindrical_faces(faces: FacesResult | tuple[FaceInfo, ...]) -> list[FaceInfo]:
    items = faces.faces if isinstance(faces, FacesResult) else faces
    return [f for f in items if f.surface is SurfaceType.cylinder]


@dataclass
class Session:
    """Thin wrapper that keeps the operation ids readable in tests."""

    adapter: FakeAdapter

    def new_part(self, name: str = "part", kind: DocumentKind = DocumentKind.part) -> str:
        return self.adapter.new_document(NewDocumentRequest(operation_id=f"new_{name}", kind=kind, name=name)).handle.id

    def rect_sketch(
        self, op: str, on: PlaneName | FaceHandle, center: Point2, width: float, height: float
    ) -> tuple[str, float]:
        result = self.adapter.sketch(
            SketchRequest(
                operation_id=op,
                on=on,
                entities=(RectEntity(name="r", center=center, width=width, height=height),),
            )
        )
        return result.sketch.id, result.profile_areas_mm2[0]

    def circle_sketch(self, op: str, on: PlaneName | FaceHandle, center: Point2, diameter: float) -> str:
        result = self.adapter.sketch(
            SketchRequest(operation_id=op, on=on, entities=(CircleEntity(name="c", center=center, diameter=diameter),))
        )
        return result.sketch.id

    def polygon_sketch(self, op: str, on: PlaneName | FaceHandle, points: list[Point2]) -> str:
        entities = tuple(
            LineEntity(name=f"l{i}", start=points[i], end=points[(i + 1) % len(points)]) for i in range(len(points))
        )
        return self.adapter.sketch(SketchRequest(operation_id=op, on=on, entities=entities)).sketch.id

    def extrude(self, op: str, sketch: str, depth: float, **kwargs: object) -> OperationResult:
        from cotaforge.contract import SketchHandle

        return self.adapter.extrude(
            ExtrudeRequest(operation_id=op, sketch=SketchHandle(id=sketch), depth_mm=depth, **kwargs)
        )  # type: ignore[arg-type]

    def faces(self, feature: FeatureHandle, role: ProvenanceRole = ProvenanceRole.any) -> FacesResult:
        return self.adapter.faces_of(FeatureRequest(operation_id="q", feature=feature, role=role))

    def edges_between(self, a: FaceInfo, b: FaceInfo) -> tuple[EdgeInfo, ...]:
        return self.adapter.edges_between(EdgesBetweenRequest(operation_id="q", a=a.handle, b=b.handle)).edges

    def plate(self, op_prefix: str = "plate") -> OperationResult:
        """100 x 60 x 8 plate on XY, corner at the origin."""
        sketch, _ = self.rect_sketch(f"{op_prefix}_sketch", PlaneName.XY, (50.0, 30.0), 100.0, 60.0)
        return self.extrude(op_prefix, sketch, 8.0)

    def l_bracket(self, op_prefix: str = "bracket") -> OperationResult:
        """L profile 60 x 40, legs 10 thick, extruded 20 along +Z."""
        points: list[Point2] = [(0, 0), (60, 0), (60, 10), (10, 10), (10, 40), (0, 40)]
        sketch = self.polygon_sketch(f"{op_prefix}_sketch", PlaneName.XY, points)
        return self.extrude(op_prefix, sketch, 20.0)


@pytest.fixture
def session() -> Session:
    adapter = FakeAdapter()
    adapter.connect(ConnectRequest(operation_id="connect"))
    return Session(adapter)


@pytest.fixture
def make_session() -> type[Session]:
    return Session


def session_with_faults(plan: FaultPlan) -> Session:
    adapter = FakeAdapter(faults=plan)
    adapter.connect(ConnectRequest(operation_id="connect"))
    return Session(adapter)
