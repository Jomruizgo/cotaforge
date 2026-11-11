"""Sketches in the 3D window of the REAL visible worker: the ones a feature consumed are hidden in the GUI's own
``ViewObject`` (not only in the ``Visibility`` property of the document object), also in a document made before
sketches were hidden once it is opened, and doing so does not turn a document without unsaved changes into one with.

Needs a display (Windows opens its own window); carries the ``visible`` marker. ``COTAFORGE_TEST_SHOTS`` names a folder
that receives a screenshot of the window.
"""

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.adapters.freecad.rpc import _binaries
from cotaforge.contract import CaptureRequest, DocumentKind, NewDocumentRequest, OpenDocumentRequest, RunNativeRequest
from tests.integration.freecad.test_feature_edit import PLATE
from tests.integration.freecad.worker_tools import call_hook

pytestmark = [
    pytest.mark.freecad,
    pytest.mark.visible,
    pytest.mark.skipif(
        sys.platform != "win32" and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")),
        reason="the visible worker needs a display (DISPLAY / WAYLAND_DISPLAY)",
    ),
]

SKETCHES = ("Sketch", "Sketch001")
SKETCH_ONLY = """
body = doc.addObject("PartDesign::Body", "Body")
sk = body.newObject("Sketcher::SketchObject", "Sketch")
sk.AttachmentSupport = (body.Origin.OriginFeatures[3], [""])
sk.MapMode = "FlatFace"
pts = [Vector(0, 0, 0), Vector(20, 0, 0), Vector(20, 10, 0), Vector(0, 10, 0)]
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
doc.recompute()
"""
PAD_ON_IT = """
body = doc.getObject("Body")
pad = body.newObject("PartDesign::Pad", "Pad")
pad.Profile = doc.getObject("Sketch")
pad.Length = 5
doc.recompute()
"""


@pytest.fixture
def window(freecad_root: Path, tmp_path: Path) -> Iterator[FreeCADAdapter]:
    with FreeCADAdapter(
        freecad_root=freecad_root, timeout_s=120.0, log_dir=tmp_path / "logs", visible=True, enable_test_hooks=True
    ) as adapter:
        yield adapter


def state(adapter: FreeCADAdapter, **params: object) -> object:
    return call_hook(adapter, "test_view_state", params)["value"]


def shown(adapter: FreeCADAdapter, name: str) -> bool:
    return bool(state(adapter, what="property", object=name, property="Visibility"))


def old_document(freecad_root: Path, tmp_path: Path) -> Path:
    """A plate as a version of the harness that left its sketches showing made it: FreeCAD's own headless run builds
    it (the sketches are shown, as FreeCAD creates them) and saves it."""
    path = tmp_path / "old.FCStd"
    macro = tmp_path / "old.py"
    macro.write_text(
        "import FreeCAD as App, Part, Sketcher\nfrom FreeCAD import Vector, Placement, Rotation\n"
        f"doc = App.newDocument('old')\n{PLATE}\ndoc.saveAs({str(path)!r})\n",
        encoding="utf-8",
    )
    done = subprocess.run(  # noqa: S603
        [str(_binaries(freecad_root)[0]), "-c", f"exec(open({str(macro)!r}).read())"],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert path.is_file(), done.stdout + done.stderr
    return path


def test_the_window_hides_the_sketches_of_a_document_made_before_and_does_not_mark_it_modified(
    window: FreeCADAdapter, freecad_root: Path, tmp_path: Path
) -> None:
    path = old_document(freecad_root, tmp_path)
    window.open_document(OpenDocumentRequest(operation_id="open", path=str(path)))
    assert [shown(window, name) for name in SKETCHES] == [False, False]
    assert state(window, what="modified") is False  # tidying the display is not an edit of the document
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path)
    shots.mkdir(parents=True, exist_ok=True)
    target = shots / "sketches_hidden.png"
    window.capture(CaptureRequest(operation_id="cap", path=str(target), width=800, height=500))
    assert target.stat().st_size > 1000


def test_a_native_pad_hides_its_sketch_in_the_window_and_a_sketch_with_no_feature_stays_shown(
    window: FreeCADAdapter,
) -> None:
    window.new_document(NewDocumentRequest(operation_id="n", kind=DocumentKind.part, name="built"))
    assert window.run_native(RunNativeRequest(operation_id="r1", code=SKETCH_ONLY, intent="a sketch")).ok
    assert shown(window, "Sketch")  # nobody uses it yet: the agent is still working on it
    assert window.run_native(RunNativeRequest(operation_id="r2", code=PAD_ON_IT, intent="a pad on it")).ok
    assert not shown(window, "Sketch")  # consumed by the pad
