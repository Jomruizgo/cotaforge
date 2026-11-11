"""A feature placed on a face stays where it is when another feature changes the face (``worker/edit.py``).

The frame of a sketch on a planar face has its origin at the centre of mass of the face, and that centre moves with
every hole in it. Resizing the first hole of a plate must not move the second feature of the same face: "changing a
measure changes that dimension and nothing else". Plain asserts run inside ``freecadcmd`` (see ``run.py``); positions
are read from the CAD (the axis of the cylinder a hole leaves, the walls of a pocket and of a boss).
"""

import math
from typing import Any

import main
from check_edit_dimension import _edit, _hole_request, _pad_edit, _plate
from harness import call, close, extrude, new_document, op, sketch, volume

TOL = 1e-6  # mm
SECOND = (25.0, 0.0)  # relative to the centre of the face as the agent saw it when it asked


def _shape() -> Any:
    doc = main.WORKER.active
    body = next(o for o in doc.fc_doc.Objects if o.TypeId == "PartDesign::Body")
    return body.Shape


def _axis_xy(diameter: float) -> list[tuple[float, float]]:
    """World (x, y) of the axis of every cylindrical face of ``diameter``, sorted."""
    found = []
    for face in _shape().Faces:
        surface = face.Surface
        if type(surface).__name__ == "Cylinder" and abs(surface.Radius * 2 - diameter) < 1e-6:
            found.append((round(surface.Center.x, 9), round(surface.Center.y, 9)))
    return sorted(set(found))


def _only(points: list[tuple[float, float]]) -> tuple[float, float]:
    assert len(points) == 1, points
    return points[0]


def _top_z() -> float:
    return max(f.CenterOfMass.z for f in _shape().Faces)


def _second_hole(ids: dict[str, Any], diameter: float = 4.0) -> dict[str, Any]:
    request = _hole_request(ids["top"], diameter, SECOND)
    request["operation_id"] = "hole_2"
    return call("hole", request)


def _first_hole_edit(ids: dict[str, Any], diameter: float) -> dict[str, Any]:
    return {"feature": ids["hole"]["feature"], "kind": "hole", "request": _hole_request(ids["top"], diameter)}


def _near(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return math.dist(a, b) <= TOL


def check_a_second_hole_stays_when_the_first_one_is_resized() -> None:
    ids = _plate()
    _second_hole(ids)
    before = _only(_axis_xy(4.0))
    result = _edit([_first_hole_edit(ids, 12.0)])
    assert result["applied"], result
    assert _axis_xy(12.0), "the first hole was not resized"
    after = _only(_axis_xy(4.0))
    assert _near(before, after), f"the second hole moved {math.dist(before, after):.6f} mm: {before} -> {after}"


def check_a_hole_follows_the_top_face_but_not_the_holes_when_the_plate_is_thicker_and_a_hole_resized() -> None:
    ids = _plate()
    _second_hole(ids)
    before = _only(_axis_xy(4.0))
    assert _edit([_first_hole_edit(ids, 12.0)])["applied"]
    assert _edit([_pad_edit(ids, 14.0)])["applied"], "the plate could not be made thicker"
    assert close(_top_z(), 14.0, 1e-6), _top_z()
    after = _only(_axis_xy(4.0))
    assert _near(before, after), f"the second hole moved {math.dist(before, after):.6f} mm: {before} -> {after}"
    cut = [f for f in _shape().Faces if type(f.Surface).__name__ == "Cylinder" and abs(f.Surface.Radius - 2) < 1e-6]
    assert cut and all(close(f.BoundBox.ZMax, 14.0, 1e-6) for f in cut), "the second hole does not follow the top"


def check_a_pocket_sketched_on_the_face_stays_when_the_first_hole_is_resized() -> None:
    ids = _plate()
    top = ids["top"]
    sk = sketch("pocket_sk", top["handle"], [{"kind": "circle", "name": "c", "center": list(SECOND), "diameter": 4}])
    params = op("pocket", sketch=sk["sketch"], end_condition="blind", direction="negative")
    params.update(depth_mm=3.0, both_directions=False, depth2_mm=None, scope=None)
    call("cut", params)
    before = _only(_axis_xy(4.0))
    assert _edit([_first_hole_edit(ids, 12.0)])["applied"]
    after = _only(_axis_xy(4.0))
    assert _near(before, after), f"the pocket moved {math.dist(before, after):.6f} mm: {before} -> {after}"


def check_a_boss_sketched_on_the_face_stays_when_the_first_hole_is_resized() -> None:
    ids = _plate()
    sk = sketch(
        "boss_sk", ids["top"]["handle"], [{"kind": "circle", "name": "c", "center": list(SECOND), "diameter": 10}]
    )
    extrude("boss", sk, 5.0)
    before = _only(_axis_xy(10.0))
    assert _edit([_first_hole_edit(ids, 12.0)])["applied"]
    after = _only(_axis_xy(10.0))
    assert _near(before, after), f"the boss moved {math.dist(before, after):.6f} mm: {before} -> {after}"
    assert close(volume(), _fresh_boss_volume(12.0)), "the edited part is not the part built from scratch"


def _fresh_boss_volume(diameter: float) -> float:
    new_document("fresh")
    ids = _plate(diameter=diameter)
    sk = sketch(
        "boss_sk", ids["top"]["handle"], [{"kind": "circle", "name": "c", "center": list(SECOND), "diameter": 10}]
    )
    extrude("boss", sk, 5.0)
    return volume()


def check_a_hole_asked_for_relative_to_the_face_centre_lands_there_on_the_first_build() -> None:
    """The meaning of ``position`` is unchanged: from the centre of the face as it is when the hole is asked for."""
    ids = _plate()
    centre = call("face_info", op("q", face=ids["top"]["handle"]))["center"]
    _second_hole(ids)
    point = _only(_axis_xy(4.0))
    # the face as the second hole saw it already had the first hole in it
    assert _near(point, (centre[0] + SECOND[0], centre[1] + SECOND[1])), (point, centre)


def check_the_second_hole_survives_edits_undo_and_a_reopen_and_follows_the_top_face() -> None:
    import os
    import tempfile

    from check_save_resume import reopen, save_and_reset

    ids = _plate()
    _second_hole(ids, 4.0)
    third = _hole_request(ids["top"], 3.0, (-25.0, 15.0))
    third["operation_id"] = "hole_3"
    call("hole", third)
    before = _only(_axis_xy(4.0))
    assert _edit([_first_hole_edit(ids, 12.0)])["applied"]
    assert _edit([_pad_edit(ids, 14.0)])["applied"]
    call("undo_last", op("u"))  # the third hole goes
    assert not _axis_xy(3.0), "the third hole is still there"
    assert _near(before, _only(_axis_xy(4.0))), "the second hole moved with the edits and the undo"
    with tempfile.TemporaryDirectory(prefix="cadh-faceorigin-") as directory:
        path, state, _ = save_and_reset(directory, "plate")
        assert os.path.exists(path)
        reopen(path, state)
        assert _near(before, _only(_axis_xy(4.0))), "the second hole moved by the reopen"
        assert close(_top_z(), 14.0, 1e-6)


def check_an_edit_after_a_reopen_places_the_second_hole_where_it_was() -> None:
    import tempfile

    from check_save_resume import reopen, save_and_reset

    ids = _plate()
    _second_hole(ids, 4.0)
    before = _only(_axis_xy(4.0))
    assert _edit([_first_hole_edit(ids, 12.0)])["applied"]
    assert _edit([_pad_edit(ids, 14.0)])["applied"]
    with tempfile.TemporaryDirectory(prefix="cadh-faceorigin-") as directory:
        path, state, _ = save_and_reset(directory, "plate")
        reopen(path, state)
        # the handle of the face is the one it was, and the sketches are placed again from where the file has them
        result = _edit([_first_hole_edit(ids, 9.0)])
        assert result["applied"], result
        assert _near(before, _only(_axis_xy(4.0))), "the second hole moved by an edit after the reopen"
        assert close(_top_z(), 14.0, 1e-6)
