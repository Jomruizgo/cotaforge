"""A face handle survives an undo that goes back past an in-place edit (``queries.undo_last``).

The registry snapshot an undo restores was taken before the edit: it still has the top face of a plate where the plate
was thinner. Undoing a feature then refreshed the handles against a plate that is thicker, and the handle of the face
the next step was drilled on was lost ("face:6 no longer exists"). Plain asserts run inside ``freecadcmd`` (see
``run.py``).
"""

from check_edit_dimension import _edit, _hole_request, _pad_edit, _plate
from harness import call, close, face_info, op


def _undo() -> None:
    call("undo_last", op("u"))


def check_the_top_face_keeps_its_handle_when_a_hole_is_undone_after_the_plate_was_made_thicker() -> None:
    ids = _plate()  # the first hole is the last feature
    assert _edit([_pad_edit(ids, 14.0)])["applied"], "the plate could not be made thicker"
    top = ids["top"]["handle"]
    assert close(face_info(top)["center"][2], 14.0, 1e-6)
    _undo()  # the hole goes: the snapshot it restores has the plate 8 mm thick
    info = face_info(top)  # raises when the face no longer exists
    assert close(info["center"][2], 14.0, 1e-6), info["center"]


def check_a_hole_is_drilled_again_on_the_same_face_after_an_edit_and_an_undo() -> None:
    ids = _plate()
    assert _edit([_pad_edit(ids, 14.0)])["applied"]
    second = _hole_request(ids["top"], 4.0, (25.0, 0.0))
    second["operation_id"] = "hole_2"
    call("hole", second)
    _undo()
    _undo()  # both holes are gone: the part is the plain plate, 14 mm thick
    again = _hole_request(ids["top"], 5.0)
    again["operation_id"] = "hole_again"
    result = call("hole", again)  # raises when the handle of the top face was lost
    assert result["rebuild_ok"], result
