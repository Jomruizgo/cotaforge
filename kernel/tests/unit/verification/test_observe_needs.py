"""The checks that read the faces of an op in detail are the ones ``gather_observation`` describes them for."""

from cotaforge.verification import level2
from cotaforge.verification.observe import (
    CREATED_FACE_DETAIL_CHECKS,
    HOLE_ENTRY_FACE_CHECKS,
    MODIFIED_FACE_DETAIL_CHECKS,
)

# the evaluators that read FaceInfo from the observation (see level2.py)
READS_CREATED = {
    level2._hole_axis_direction,
    level2._hole_axis_position,
    level2._hole_radius,
    level2._hole_within_face,
    level2._fillet_radius,
    level2._cut_extent_along_n,
    level2._draft_angle,
}


def test_every_check_that_reads_created_faces_is_listed() -> None:
    by_evaluator = {name for name, fn in level2._EVALUATORS.items() if fn in READS_CREATED}
    assert by_evaluator == set(CREATED_FACE_DETAIL_CHECKS)


def test_only_the_draft_reads_modified_faces_and_only_the_hole_its_entry_face() -> None:
    assert MODIFIED_FACE_DETAIL_CHECKS == {"draft_angle"}
    assert HOLE_ENTRY_FACE_CHECKS == {"hole_within_face"}
