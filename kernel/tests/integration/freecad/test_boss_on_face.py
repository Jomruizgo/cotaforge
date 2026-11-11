"""A boss extruded on an existing face against the real worker.

PR #1 flagged this as a documented-but-unvalidated theoretical item: a boss whose sketch lies on an
existing planar face and joins the body (extrude, merge=True the default) yields 5 new faces for a
rectangular profile (4 side walls + 1 top cap) and 2 for a circular one (1 cylindrical side + 1 top
cap), not 6/3 — the base merges into the host face, which is reported as *modified*, not duplicated.
Reproduced and fixed in ``verification/expected.py::ExpectedComputer._op_extrude`` and
``adapters/fake/adapter.py::FakeAdapter.extrude``; this test is the real-FreeCAD half of that fix,
mirrored fake-side by ``tests/unit/fake/test_builders.py``.

The ``covers`` case below (a boss whose footprint exactly equals its host face, not just lies inside
it) is a second, distinct coverage class: on real
FreeCAD 1.0.2 it yields 1 new face (not 5), the host face is removed outright (a stale handle
afterwards, not merely shrunk), and the body's 4 side faces are modified (extended) instead of new side
walls being created.
"""

import math

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.contract import (
    AdapterError,
    CircleEntity,
    DocumentKind,
    EdgeRequest,
    EdgesBetweenRequest,
    ErrorCode,
    ExtrudeRequest,
    FaceInfo,
    FaceRequest,
    FeatureHandle,
    FeatureRequest,
    MassRequest,
    NewDocumentRequest,
    OperationResult,
    PlaneName,
    RectEntity,
    SketchRequest,
    Vec3,
    VertexAtRequest,
)
from cotaforge.executor.config import ExecutorConfig
from cotaforge.executor.executor import Executor
from cotaforge.executor.trace import ExecutorState, StepStatus
from cotaforge.ir import parse_plan
from cotaforge.verification.verifier import Verifier

pytestmark = pytest.mark.freecad

PLATE_VOLUME = 100.0 * 60.0 * 8.0  # 48000
BLOCK_VOLUME = 20.0 * 20.0 * 8.0  # 3200


def _close(a: Vec3, b: Vec3, tol: float = 1e-6) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b, strict=True))


def _plate(adapter: FreeCADAdapter, name: str) -> OperationResult:
    adapter.new_document(NewDocumentRequest(operation_id=f"{name}.new_document", kind=DocumentKind.part, name=name))
    sketch = adapter.sketch(
        SketchRequest(
            operation_id=f"{name}.sketch",
            on=PlaneName.XY,
            entities=(RectEntity(name="outline", center=(50, 30), width=100, height=60),),
        )
    )
    return adapter.extrude(ExtrudeRequest(operation_id=f"{name}.base", sketch=sketch.sketch, depth_mm=8.0))


def _block(adapter: FreeCADAdapter, name: str) -> OperationResult:
    """20 x 20 x 8 block, corner at the origin -- small enough that a same-footprint boss on its top
    face exactly *covers* it (contrast ``_plate``'s 100 x 60 footprint, where a 20x20 boss is strictly
    inside the top face)."""
    adapter.new_document(NewDocumentRequest(operation_id=f"{name}.new_document", kind=DocumentKind.part, name=name))
    sketch = adapter.sketch(
        SketchRequest(
            operation_id=f"{name}.sketch",
            on=PlaneName.XY,
            entities=(RectEntity(name="outline", center=(10, 10), width=20, height=20),),
        )
    )
    return adapter.extrude(ExtrudeRequest(operation_id=f"{name}.base", sketch=sketch.sketch, depth_mm=8.0))


def _faces(adapter: FreeCADAdapter, feature: FeatureHandle | None, op: str) -> tuple[FaceInfo, ...]:
    assert feature is not None
    return adapter.faces_of(FeatureRequest(operation_id=op, feature=feature)).faces


def _face_with_normal(faces: tuple[FaceInfo, ...], normal: Vec3) -> FaceInfo:
    matches = [f for f in faces if f.normal is not None and _close(f.normal, normal)]
    assert len(matches) == 1, f"expected one face with normal {normal}, got {[f.handle.id for f in matches]}"
    return matches[0]


def test_rect_boss_on_the_top_face_yields_five_new_faces(adapter: FreeCADAdapter) -> None:
    base = _plate(adapter, "rect_boss")
    top = _face_with_normal(_faces(adapter, base.feature, "faces"), (0, 0, 1))
    boss_sketch = adapter.sketch(
        SketchRequest(
            operation_id="rect_boss.boss_sketch",
            on=top.handle,
            entities=(RectEntity(name="boss", center=(0, 0), width=20, height=20),),
        )
    )
    boss = adapter.extrude(ExtrudeRequest(operation_id="rect_boss.boss", sketch=boss_sketch.sketch, depth_mm=10.0))
    assert boss.rebuild_ok
    assert boss.feature is not None
    assert len(boss.created_faces) == 5  # 4 side walls + 1 top cap: the base merges into the host face
    assert {f.id for f in boss.modified_faces} >= {top.handle.id}
    created = _faces(adapter, boss.feature, "created_faces")
    assert all(f.normal is None or not _close(f.normal, (0, 0, -1)) for f in created)  # no duplicate base cap
    mass = adapter.mass_properties(MassRequest(operation_id="mass"))
    assert mass.volume_mm3 == pytest.approx(PLATE_VOLUME + 20.0 * 20.0 * 10.0, abs=1e-3)  # 48000 + 4000


def test_cylindrical_boss_on_the_top_face_yields_two_new_faces(adapter: FreeCADAdapter) -> None:
    base = _plate(adapter, "cyl_boss")
    top = _face_with_normal(_faces(adapter, base.feature, "faces"), (0, 0, 1))
    boss_sketch = adapter.sketch(
        SketchRequest(
            operation_id="cyl_boss.boss_sketch",
            on=top.handle,
            entities=(CircleEntity(name="boss", center=(0, 0), diameter=20),),
        )
    )
    boss = adapter.extrude(ExtrudeRequest(operation_id="cyl_boss.boss", sketch=boss_sketch.sketch, depth_mm=10.0))
    assert boss.rebuild_ok
    assert boss.feature is not None
    assert len(boss.created_faces) == 2  # 1 cylindrical side + 1 top cap
    assert {f.id for f in boss.modified_faces} >= {top.handle.id}
    mass = adapter.mass_properties(MassRequest(operation_id="mass"))
    assert mass.volume_mm3 == pytest.approx(PLATE_VOLUME + math.pi * 10.0**2 * 10.0, abs=1e-3)


def test_rect_boss_that_covers_the_host_face_yields_one_new_face_and_a_stale_host_handle(
    adapter: FreeCADAdapter,
) -> None:
    """The ``covers`` coverage class: a 20x20 boss
    on the 20x20 top face of a 20x20x8 block matches the host face's footprint exactly, so FreeCAD
    consumes the host face entirely instead of shrinking it. Observed on FreeCAD 1.0.2: created_faces
    == 1 (only the boss's own top cap), modified_faces == the block's 4 side faces (extended), the old
    top face's handle becomes stale (``AdapterError``), and volume == 3200 + 2000 == 5200."""
    base = _block(adapter, "cover_boss")
    top = _face_with_normal(_faces(adapter, base.feature, "faces"), (0, 0, 1))
    boss_sketch = adapter.sketch(
        SketchRequest(
            operation_id="cover_boss.boss_sketch",
            on=top.handle,
            entities=(RectEntity(name="boss", center=(0, 0), width=20, height=20),),
        )
    )
    boss = adapter.extrude(ExtrudeRequest(operation_id="cover_boss.boss", sketch=boss_sketch.sketch, depth_mm=5.0))
    assert boss.rebuild_ok
    assert boss.feature is not None
    assert len(boss.created_faces) == 1  # only the far (top) cap; no new side walls
    assert len(boss.modified_faces) == 4  # the block's 4 side faces, extended instead of duplicated
    assert top.handle.id not in {f.id for f in boss.modified_faces}  # the host face itself is gone
    mass = adapter.mass_properties(MassRequest(operation_id="mass"))
    assert mass.volume_mm3 == pytest.approx(BLOCK_VOLUME + 20.0 * 20.0 * 5.0, abs=1e-3)  # 3200 + 2000
    with pytest.raises(AdapterError) as excinfo:
        adapter.face_info(FaceRequest(operation_id="cover_boss.probe_stale_top", face=top.handle))
    assert excinfo.value.info.code is ErrorCode.invalid_reference

    # Judgment Day R3 residual: real FreeCAD gives the cap's 4 corners actual vertices (unlike the fake
    # before the fix, where `vertex_at`/`vertices_of` returned nothing for them) and closes each extended
    # side face's edge loop instead of leaving its verticals dangling at the old z=8 height.
    cap = _face_with_normal(_faces(adapter, boss.feature, "cover_boss.cap"), (0, 0, 1))
    side_x = _face_with_normal(_faces(adapter, base.feature, "cover_boss.side_x"), (1, 0, 0))
    side_ny = _face_with_normal(_faces(adapter, base.feature, "cover_boss.side_ny"), (0, -1, 0))
    corner = adapter.vertex_at(
        VertexAtRequest(operation_id="cover_boss.corner", faces=(cap.handle, side_x.handle, side_ny.handle))
    ).vertices
    assert len(corner) == 1
    assert _close(corner[0].point, (20.0, 0.0, 13.0))
    cap_edges = adapter.edges_between(
        EdgesBetweenRequest(operation_id="cover_boss.cap_edge", a=cap.handle, b=side_x.handle)
    ).edges
    assert len(cap_edges) == 1
    cap_edge_vertices = adapter.vertices_of(
        EdgeRequest(operation_id="cover_boss.cap_edge_vertices", edge=cap_edges[0].handle)
    ).vertices
    assert len(cap_edge_vertices) == 2


def test_circle_boss_that_covers_the_host_face_is_a_plan_error_without_touching_the_worker(
    adapter: FreeCADAdapter,
) -> None:
    """The ``covers`` coverage class is only supported for a
    rectangular profile congruent with (or containing) a rectangular host face. Mirrors
    ``tests/unit/executor/test_with_fake.py::test_circle_boss_that_covers_the_host_face_is_a_plan_error``
    on real FreeCAD -- ``ExpectedComputer`` raises before the adapter is ever called (``executor/
    executor.py::next_operation`` computes expectations first), so the ``boss`` step has
    ``attempts == ()``: the worker process is never asked to build this case."""
    corner_distance = math.hypot(10.0, 10.0)
    plan = {
        "id": "block_circle_cover_freecad",
        "kind": "part",
        "description": "20x20x8 block with a circle boss covering its top face",
        "operations": [
            {
                "id": "base_sketch",
                "op": "sketch",
                "intent": "Outline of the block on XY",
                "on": {"kind": "plane", "name": "XY"},
                "entities": [{"kind": "rect", "name": "outline", "center": [10, 10], "width": 20, "height": 20}],
            },
            {"id": "base", "op": "extrude", "intent": "Block thickness", "sketch": "base_sketch", "depth_mm": 8},
            {
                "id": "boss_sketch",
                "op": "sketch",
                "intent": "Circle covering the top face",
                "on": {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, 1]}},
                "entities": [
                    {"kind": "circle", "name": "boss", "center": [0, 0], "diameter": 2 * (corner_distance + 1.0)}
                ],
            },
            {
                "id": "boss",
                "op": "extrude",
                "intent": "Boss covering the top face",
                "sketch": "boss_sketch",
                "depth_mm": 5,
            },
        ],
    }
    ex = Executor(parse_plan(plan), adapter, Verifier(), ExecutorConfig())
    ex.start()
    trace = ex.run_to_end()
    assert trace.final_state == ExecutorState.failed
    boss = trace.step("boss")
    assert boss is not None and boss.status == StepStatus.plan_error and boss.attempts == ()
    assert boss.plan_error is not None and "only a rectangular profile" in boss.plan_error
