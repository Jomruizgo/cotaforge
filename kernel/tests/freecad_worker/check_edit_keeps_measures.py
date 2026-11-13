"""``edit_feature`` keeps what it measured with the body (``worker/edit.py`` + ``registry.reread_shape``).

The checks of an edit measure every body before and after (volume, centre, box, validity). Those measures stay with the
body, and the next reading (``mass_properties``, ``bounding_box``, ``is_valid``) and the next edit find them there: a
shape that did not change is not measured again. The risk of that is a measure given back for a shape that is no longer
the one it was taken from, so every case compares what the body keeps with a measure taken now on the shape the body
has, and a hand edit or a rollback in between must never leave an old one behind.

Plain asserts run inside ``freecadcmd`` (see ``run.py``).
"""

from typing import Any

import geometry as g
import main
import registry as reg
from harness import call, close, extrude, faces_of, new_document, op, rect, select_face, sketch, volume


def _hole_request(top: dict[str, Any], position: tuple[float, float]) -> dict[str, Any]:
    return op(
        "hole_1",
        face=top["handle"],
        position=list(position),
        standard="simple",
        size=None,
        diameter_mm=6.0,
        end_condition="through_all",
        depth_mm=None,
        thread=None,
        counterbore=None,
        countersink=None,
        drill_point="flat",
        drill_point_angle_deg=118.0,
    )


def _plate() -> dict[str, Any]:
    new_document("plate")
    sk = sketch("base_sketch", "XY", [rect("outline", (50, 30), 100.0, 60.0)])
    base = extrude("base", sk, 8.0)
    top = select_face(faces_of(base["feature"]), normal=(0, 0, 1))
    hole = call("hole", _hole_request(top, (-30, -20)))
    return {"base": base, "top": top, "hole": hole}


def _move_hole(ids: dict[str, Any], position: tuple[float, float]) -> dict[str, Any]:
    edit = {"feature": ids["hole"]["feature"], "kind": "hole", "request": _hole_request(ids["top"], position)}
    return call("edit_feature", op("edit", edits=[edit], dry_run=False))


def _body() -> Any:
    return main.WORKER.active.live_bodies()[0]


def _assert_kept_is_what_the_shape_measures_now() -> None:
    """What the body keeps against a measure taken now, on the shape the document has."""
    doc = main.WORKER.active
    body = _body()
    shape = reg.world_shape(doc, body)
    fresh_volumes = [float(s.Volume) for s in shape.Solids]
    assert body._vols is not None and len(body._vols) == len(fresh_volumes)
    assert all(close(a, b, 1e-6) for a, b in zip(body._vols, fresh_volumes, strict=True)), (body._vols, fresh_volumes)
    if body._centres is not None:
        fresh = [g.vec(s.CenterOfMass) for s in shape.Solids]
        assert all(close(c[i], f[i], 1e-6) for c, f in zip(body._centres, fresh, strict=True) for i in range(3))
    if body._bbox is not None:
        box = g.shape_bbox(shape)
        assert all(close(body._bbox[0]["min"][i], box["min"][i], 1e-6) for i in range(3)), (body._bbox, box)
        assert all(close(body._bbox[0]["max"][i], box["max"][i], 1e-6) for i in range(3)), (body._bbox, box)
    if body._valid is not None:
        assert body._valid == bool(shape.isValid())


def check_a_hole_move_leaves_the_body_with_the_measures_of_the_shape_it_has() -> None:
    ids = _plate()
    result = _move_hole(ids, (-20, -10))
    assert result["applied"], result
    body = _body()
    assert body._vols is not None and body._valid is True and body._bbox is not None, "the checks measured nothing"
    _assert_kept_is_what_the_shape_measures_now()
    before = volume()  # the reading after the edit finds them there
    assert close(before, volume()), "a reading changed what the body keeps"
    _assert_kept_is_what_the_shape_measures_now()


def check_a_second_move_after_a_hand_edit_is_not_compared_with_the_old_shape() -> None:
    ids = _plate()
    assert _move_hole(ids, (-20, -10))["applied"]
    doc = main.WORKER.active
    pad = doc.fc_doc.getObject(doc.features[0].fc_main)
    pad.Length = 12.0  # by hand: the part is another one than the last edit left
    doc.fc_doc.recompute()
    result = _move_hole(ids, (-25, -12))
    assert result["applied"], result
    assert result["delta"] == [], "the part before the edit was measured as it was before the hand edit"
    assert not result["flags"], result["flags"]
    _assert_kept_is_what_the_shape_measures_now()
    # the reading of the part is the volume of the shape the document has, the hand edit included (12 mm of plate
    # less the hole cut from the face it was drilled in: the 4 mm above that face are not cut)
    shape = reg.world_shape(doc, _body())
    assert close(volume(), sum(float(s.Volume) for s in shape.Solids)), "the reading is of another shape"
    assert close(volume(), 100.0 * 60.0 * 12.0 - 3.141592653589793 * 9.0 * 8.0, 1e-3)


def check_a_move_that_is_rolled_back_does_not_leave_a_measure_of_the_shape_it_tried() -> None:
    ids = _plate()
    assert _move_hole(ids, (-20, -10))["applied"]
    kept = volume()
    off = _move_hole(ids, (400, 400))  # off the plate: the check refuses it and the document is given back
    assert not off.get("applied"), off
    assert close(volume(), kept), "the rollback did not give the part back"
    _assert_kept_is_what_the_shape_measures_now()
    again = _move_hole(ids, (-22, -11))
    assert again["applied"] and again["delta"] == [], again
    _assert_kept_is_what_the_shape_measures_now()
