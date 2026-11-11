"""A sketch a feature has consumed is hidden; one no feature uses yet (the agent is still working on it) is shown; and a
document made before sketches were hidden is tidied when it is opened.

Plain asserts run inside ``freecadcmd`` (see ``run.py``). ``Visibility`` is what the visible worker mirrors in its
window (the window itself is checked in ``tests/integration/freecad/test_sketch_visibility_visible.py``). The ops make a
sketch only together with the feature that uses it, so a sketch that waits is one a native fragment made.
"""

import os
import tempfile
from typing import Any

import main
from harness import build_part03, call, extrude, new_document, op, rect, sketch

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


def _sketches() -> list[Any]:
    return [o for o in main.WORKER.active.fc_doc.Objects if str(o.TypeId) == "Sketcher::SketchObject"]


def _shown() -> list[bool]:
    return [bool(o.Visibility) for o in _sketches()]


def check_the_sketch_of_an_extrude_is_hidden() -> None:
    new_document("v")
    sk = sketch("s", "XY", [rect("outline", (50, 30), 100, 60)])
    extrude("e", sk, 8)
    assert _shown() == [False]


def check_every_sketch_of_a_part_with_a_pocket_is_hidden() -> None:
    build_part03()
    assert len(_sketches()) == 2
    assert _shown() == [False, False], {o.Name: o.Visibility for o in _sketches()}


def check_a_sketch_that_no_feature_uses_yet_stays_shown_until_one_does() -> None:
    new_document("v")
    call("run_native", op("n1", code=SKETCH_ONLY, intent="a sketch to work on", timeout_s=60))
    assert _shown() == [True]  # the agent is still working on it
    call("run_native", op("n2", code=PAD_ON_IT, intent="a pad on it", timeout_s=60))
    assert _shown() == [False]  # consumed by the pad


def check_a_document_made_before_is_tidied_when_it_is_opened() -> None:
    build_part03()
    fc_doc = main.WORKER.active.fc_doc
    for sk in _sketches():
        sk.Visibility = True  # how an older version of the harness left them
    path = os.path.join(tempfile.mkdtemp(prefix="cotaforge-vis-"), "old.FCStd")
    fc_doc.saveAs(path)
    call("close_document", op("close"))
    call("open_document", op("open", path=path))
    assert len(_sketches()) == 2
    assert _shown() == [False, False], {o.Name: o.Visibility for o in _sketches()}
