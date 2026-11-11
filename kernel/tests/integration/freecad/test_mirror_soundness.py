"""``mirror`` on a real kernel where the SEED stands on an earlier feature, and where the host moved after a feature was
recorded: the models are correct, the changes differ from what the seed did, so they are REPORTED, never asserted."""

import math
from typing import Any

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.verification.results import CheckKind
from tests.integration.freecad.test_mirror import (
    PLATE_VOLUME,
    TOP,
    _assert_green,
    _asserted_ok,
    _boss,
    _check,
    _datum,
    _datum_ref,
    _final_volume,
    _mirror,
    _plate,
    _run,
)

pytestmark = pytest.mark.freecad


def _post_with_bore() -> list[dict[str, Any]]:
    """A d20 post 5 high at (20, 30) and a d6 bore from its top face through post and plate."""
    return [
        {
            "id": "post_sketch",
            "op": "sketch",
            "intent": "post outline",
            "on": TOP,
            "entities": [{"kind": "circle", "name": "c", "center": [-30, 0], "diameter": 20}],
        },
        {"id": "post", "op": "extrude", "intent": "post", "sketch": "post_sketch", "depth_mm": 5},
        {
            "id": "bore",
            "op": "hole",
            "intent": "bore through the post and the plate",
            "face": {"kind": "face", "from_op": "post", "select": {"normal": [0, 0, 1], "center": [20, 30, 13]}},
            "position": [0, 0],
            "standard": "simple",
            "diameter_mm": 6,
            "end_condition": "through_all",
        },
    ]


def test_a_bore_through_a_post_mirrored_onto_the_bare_plate_is_reported_not_asserted(adapter: FreeCADAdapter) -> None:
    """The seed removes pi 9 13 = 367.57 mm3 (post and plate); its reflection at (80, 30) only crosses the plate,
    pi 9 8 = 226.19. Doubling the seed's change would be wrong, so the change is REPORTED."""
    _, trace = _run(adapter, *_plate(), *_post_with_bore(), _datum("mid", 50), _mirror(_datum_ref("mid"), ["bore"]))
    _assert_green(trace)
    volume = _check(trace, "twin", "volume_delta")
    assert volume.kind is CheckKind.reported
    assert volume.observed == pytest.approx(-math.pi * 9 * 8, abs=1e-6)
    assert _check(trace, "twin", "created_faces_count").kind is CheckKind.reported
    assert "instance_count" in _asserted_ok(trace, "twin")
    assert _final_volume(trace) == pytest.approx(
        PLATE_VOLUME + math.pi * 100 * 5 - math.pi * 9 * 13 - math.pi * 9 * 8, abs=1e-6
    )


def test_a_boss_mirrored_onto_a_boss_that_was_carried_by_a_move_is_reported_not_asserted(
    adapter: FreeCADAdapter,
) -> None:
    """boss2 at (88, 10) rides the plate to x = 138 when it is translated by 50; the boss at x = 65 reflected across
    x = 100 lands on top of it (the face frame of a face with a boss on it is centred on the face's centroid, so the
    exact spot is not round): it adds less than a whole boss and merges a face."""
    slide = {
        "id": "slide",
        "op": "translate",
        "intent": "carry the plate along X",
        "target": {"kind": "body", "from_op": "plate"},
        "offset": [50, 0, 0],
    }
    boss = [
        {
            "id": "boss_sketch",
            "op": "sketch",
            "intent": "boss outline",
            "on": TOP,
            "entities": [{"kind": "circle", "name": "c", "center": [65 - 100, 10 - 30], "diameter": 10}],
        },
        {"id": "boss", "op": "extrude", "intent": "boss", "sketch": "boss_sketch", "depth_mm": 5},
    ]
    ops = [*_plate(), *_boss((88.0, 10.0), "boss2"), slide, *boss, _datum("mid", 100)]
    _, trace = _run(adapter, *ops, _mirror(_datum_ref("mid"), ["boss"]))
    _assert_green(trace)
    volume = _check(trace, "twin", "volume_delta")
    assert volume.kind is CheckKind.reported
    assert 0.0 < volume.observed < math.pi * 25 * 5 - 1.0  # overlapping its neighbour: less than a whole boss
    assert _check(trace, "twin", "created_faces_count").kind is CheckKind.reported
    assert "instance_count" in _asserted_ok(trace, "twin")
