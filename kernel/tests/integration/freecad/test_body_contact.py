"""Bodies and solids of one part that touch, overlap or stand apart, measured by the real FreeCAD worker.

``is_valid`` ran the BOP check on a body made of several solids as a whole, and that check reports two solids that only
share a face or an edge as a self-intersection: a valid part was called invalid. ``body_interference`` measures the
bodies of a part against each other (common volume and minimum distance), which ``check_interference`` (the components
of an assembly only) never did. ``resolve_reference`` takes the bare entity id of the active document as well, the id
the agent is shown for a selected face.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter, FreeCADSession
from cotaforge.contract import (
    BodyHandle,
    BodyInterferenceRequest,
    BoxRequest,
    DocumentKind,
    Empty,
    FaceHandle,
    FeatureRequest,
    GeometricSignature,
    NewDocumentRequest,
    PersistentRef,
    ProvenanceRole,
    ResolveRequest,
    RunNativeRequest,
    Transform,
)

pytestmark = pytest.mark.freecad


def compound(name: str, *origins: tuple[float, float, float]) -> str:
    """A ``Part::Feature`` holding ONE compound of 10 mm cubes at ``origins`` (a body made of several solids)."""
    cubes = ", ".join(f"Part.makeBox(10, 10, 10, Vector{origin})" for origin in origins)
    return f'obj = doc.addObject("Part::Feature", "{name}")\nobj.Shape = Part.makeCompound([{cubes}])\n'


@pytest.fixture
def part(freecad_root: Path, tmp_path: Path) -> Iterator[FreeCADAdapter]:
    with FreeCADSession(freecad_root=freecad_root, timeout_s=120.0, log_dir=tmp_path / "logs") as adapter:
        adapter.new_document(NewDocumentRequest(operation_id="new", kind=DocumentKind.part, name="contact"))
        yield adapter


def box(adapter: FreeCADAdapter, origin: tuple[float, float, float]) -> BodyHandle:
    made = adapter.box(BoxRequest(operation_id="box", size=(10, 10, 10), transform=Transform(origin=origin)))
    return made.created_bodies[0]


def test_solids_that_only_touch_are_not_a_self_intersection(part: FreeCADAdapter) -> None:
    code = compound("Touching", (0, 0, 0), (10, 0, 0), (10, 10, 0))  # a shared face, then a shared edge only
    assert part.run_native(RunNativeRequest(operation_id="n", code=code, intent="three cubes in contact")).ok

    validity = part.is_valid(Empty(operation_id="valid"))

    assert validity.valid, validity.problems


def test_solids_that_overlap_are_still_reported_and_named_as_an_overlap(part: FreeCADAdapter) -> None:
    code = compound("Overlapping", (0, 0, 0), (5, 0, 0))
    assert part.run_native(RunNativeRequest(operation_id="n", code=code, intent="two cubes that overlap")).ok

    validity = part.is_valid(Empty(operation_id="valid"))

    assert not validity.valid
    assert any("overlap" in problem and "500" in problem for problem in validity.problems), validity.problems


def test_body_interference_tells_overlap_contact_and_clearance_apart(part: FreeCADAdapter) -> None:
    a = box(part, (0, 0, 0))
    b = box(part, (5, 0, 0))  # overlaps a by 5 x 10 x 10
    c = box(part, (15, 0, 0))  # touches b on a face, 5 mm from a
    d = box(part, (40, 0, 0))  # 15 mm from c

    result = part.body_interference(BodyInterferenceRequest(operation_id="bi"))

    readings = {(p.a.id, p.b.id): p for p in result.pairs}
    assert len(readings) == 6
    assert readings[(a.id, b.id)].common_mm3 == pytest.approx(500.0)
    assert readings[(a.id, b.id)].distance_mm == pytest.approx(0.0)
    assert readings[(b.id, c.id)].common_mm3 == pytest.approx(0.0, abs=1e-6)
    assert readings[(b.id, c.id)].distance_mm == pytest.approx(0.0, abs=1e-6)
    assert readings[(a.id, c.id)].distance_mm == pytest.approx(5.0)
    assert readings[(c.id, d.id)].distance_mm == pytest.approx(15.0)
    assert all(p.note == "" for p in result.pairs)

    only = part.body_interference(BodyInterferenceRequest(operation_id="bi2", bodies=(a, d)))
    assert [(p.a.id, p.b.id) for p in only.pairs] == [(a.id, d.id)]
    assert only.pairs[0].distance_mm == pytest.approx(30.0)


def test_a_bare_entity_id_resolves_in_the_active_document(part: FreeCADAdapter) -> None:
    made = part.box(BoxRequest(operation_id="box", size=(10, 10, 10)))
    assert made.feature is not None
    face = part.faces_of(FeatureRequest(operation_id="f", feature=made.feature, role=ProvenanceRole.any)).faces[0]
    bare = PersistentRef(adapter="freecad", token=face.handle.id, signature=GeometricSignature(kind="face"))

    resolved = part.resolve_reference(ResolveRequest(operation_id="r", ref=bare))

    assert resolved.entity == FaceHandle(id=face.handle.id)
