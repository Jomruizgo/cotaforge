"""``open_document`` without ``adapter_state`` on the real FreeCAD worker: ANY file is adopted, the registry mints new
handles for what the file holds, and ``close_document`` lets the same file be opened again.

The foreign file is made inside the worker through ``run_native`` (a PartDesign body with a pad) and written with the
engineer's own ``saveAs`` (``test_save_document``): no harness stamp, no registry state, exactly what someone else's
file looks like to the harness.
"""

from pathlib import Path

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.contract import (
    AdapterError,
    DocumentKind,
    Empty,
    FeatureRequest,
    MassRequest,
    NewDocumentRequest,
    OpenDocumentRequest,
    ProvenanceRole,
    RunNativeRequest,
)
from tests.integration.freecad.worker_tools import call_hook

pytestmark = pytest.mark.freecad

PAD = """
body = doc.addObject("PartDesign::Body", "Body")
sk = body.newObject("Sketcher::SketchObject", "Sketch")
sk.AttachmentSupport = (body.Origin.OriginFeatures[3], [""])
sk.MapMode = "FlatFace"
pts = [Vector(0, 0, 0), Vector(50, 0, 0), Vector(50, 20, 0), Vector(0, 20, 0)]
for i in range(4):
    sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]), False)
for i in range(4):
    sk.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1))
for i in (0, 2):
    sk.addConstraint(Sketcher.Constraint("Horizontal", i))
for i in (1, 3):
    sk.addConstraint(Sketcher.Constraint("Vertical", i))
sk.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, pts[1].x - pts[0].x))
sk.addConstraint(Sketcher.Constraint("DistanceY", 1, 1, 1, 2, pts[2].y - pts[1].y))
sk.addConstraint(Sketcher.Constraint("DistanceX", -1, 1, 0, 1, pts[0].x))
sk.addConstraint(Sketcher.Constraint("DistanceY", -1, 1, 0, 1, pts[0].y))
pad = body.newObject("PartDesign::Pad", "Pad")
pad.Profile = sk
pad.Length = 8
"""


@pytest.fixture
def foreign(freecad_root: Path, tmp_path: Path) -> tuple[FreeCADAdapter, Path]:
    adapter = FreeCADAdapter(
        freecad_root=freecad_root, timeout_s=120.0, log_dir=tmp_path / "logs", enable_test_hooks=True
    )
    adapter.__enter__()
    adapter.new_document(NewDocumentRequest(operation_id="n", kind=DocumentKind.part, name="made_elsewhere"))
    made = adapter.run_native(RunNativeRequest(operation_id="r", code=PAD, intent="a foreign pad"))
    assert made.ok, made.error
    path = tmp_path / "foreign.FCStd"
    call_hook(adapter, "test_save_document", {"path": str(path)})
    adapter.close_document(Empty(operation_id="close"))
    yield adapter, path
    adapter.__exit__(None, None, None)


def test_a_file_without_stamp_or_state_is_adopted_with_fresh_handles(foreign: tuple[FreeCADAdapter, Path]) -> None:
    adapter, path = foreign
    with pytest.raises(AdapterError):  # closed: nothing is active any more
        adapter.mass_properties(MassRequest(operation_id="m"))

    opened = adapter.open_document(OpenDocumentRequest(operation_id="open", path=str(path)))

    assert opened.stamp is None
    assert opened.rebind.clean and "adopted" in opened.rebind.notes[0]
    assert adapter.mass_properties(MassRequest(operation_id="m")).volume_mm3 == pytest.approx(50 * 20 * 8)
    nodes = adapter.feature_tree(Empty(operation_id="tree")).nodes
    adopted = [n for n in nodes if n.kind == "adopted"]
    assert len(adopted) == 1
    inner = {n.name for n in nodes if n.parameters.get("manual")}
    assert "Pad" in inner  # the features inside the body stay visible, as hand-made
    faces = adapter.faces_of(FeatureRequest(operation_id="f", feature=adopted[0].handle, role=ProvenanceRole.any))
    assert len(faces.faces) == 6
    bodies = adapter.bodies_of(FeatureRequest(operation_id="b", feature=adopted[0].handle)).bodies
    assert [b.volume_mm3 for b in bodies] == [pytest.approx(8000.0)]


def test_nothing_of_an_adopted_file_can_be_undone(foreign: tuple[FreeCADAdapter, Path]) -> None:
    adapter, path = foreign
    adapter.open_document(OpenDocumentRequest(operation_id="open", path=str(path)))
    with pytest.raises(AdapterError) as refused:
        adapter.undo_last(Empty(operation_id="undo"))
    assert "before the document was reopened" in refused.value.info.message
    assert adapter.mass_properties(MassRequest(operation_id="m")).volume_mm3 == pytest.approx(8000.0)


def test_closing_and_opening_the_same_file_again_gives_the_same_part(foreign: tuple[FreeCADAdapter, Path]) -> None:
    adapter, path = foreign
    for _ in range(2):
        adapter.open_document(OpenDocumentRequest(operation_id="open", path=str(path)))
        assert adapter.mass_properties(MassRequest(operation_id="m")).volume_mm3 == pytest.approx(8000.0)
        adapter.close_document(Empty(operation_id="close"))


def test_a_truncated_file_is_a_recoverable_error_and_the_worker_stays_usable(
    foreign: tuple[FreeCADAdapter, Path], tmp_path: Path
) -> None:
    adapter, path = foreign
    broken = tmp_path / "broken.FCStd"
    broken.write_bytes(path.read_bytes()[: path.stat().st_size // 2])
    with pytest.raises(AdapterError) as refused:
        adapter.open_document(OpenDocumentRequest(operation_id="open", path=str(broken)))
    assert refused.value.info.recoverable and refused.value.info.details["reason"] == "corrupt"
    adapter.open_document(OpenDocumentRequest(operation_id="open", path=str(path)))
    assert adapter.mass_properties(MassRequest(operation_id="m")).volume_mm3 == pytest.approx(8000.0)
