"""Shared fixtures: fake(), verifier(), plate_plan(), thresholds()."""

from typing import Any

import pytest

from cotaforge.verification.thresholds import Thresholds
from cotaforge.verification.verifier import Verifier


def plate_plan_dict() -> dict[str, Any]:
    """Bench part 01: 100 x 60 x 8 plate, M6 clearance hole through the top
    face, patterned 2 x 2 on 80 x 40 mm.

    The hole position is in the top-face frame of 3.6 (origin at the face centre (50, 30, 8)):
    (-40, -20) is world (10, 10, 8); the pattern then covers (10|90, 10|50).
    Expected final volume: 100*60*8 - 4*pi*3.3^2*8 = 46905.2178.
    """
    return {
        "id": "plate",
        "kind": "part",
        "description": "Mounting plate with four clearance holes",
        "operations": [
            {
                "id": "base_sketch",
                "op": "sketch",
                "intent": "Outline of the plate on XY",
                "on": {"kind": "plane", "name": "XY"},
                "entities": [{"kind": "rect", "name": "outline", "center": [50, 30], "width": 100, "height": 60}],
            },
            {"id": "base", "op": "extrude", "intent": "Plate thickness", "sketch": "base_sketch", "depth_mm": 8},
            {
                "id": "hole_1",
                "op": "hole",
                "intent": "First M6 clearance hole on the top face, seed of the pattern",
                "face": {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, 1]}},
                "position": [-40, -20],
                "standard": "iso_clearance",
                "size": "M6",
                "diameter_mm": 6.6,
                "end_condition": "through_all",
            },
            {
                "id": "holes",
                "op": "linear_pattern",
                "intent": "2 x 2 pattern on 80 x 40 mm",
                "features": ["hole_1"],
                "direction": "X",
                "spacing_mm": 80,
                "count": 2,
                "direction2": "Y",
                "spacing2_mm": 40,
                "count2": 2,
            },
        ],
    }


@pytest.fixture
def plate_plan() -> dict[str, Any]:
    return plate_plan_dict()


@pytest.fixture
def thresholds() -> Thresholds:
    return Thresholds()


@pytest.fixture
def verifier(thresholds: Thresholds) -> Verifier:
    return Verifier(thresholds)


@pytest.fixture
def fake() -> Any:
    """A fresh FakeAdapter (imported lazily so this conftest loads before implementer D lands)."""
    from cotaforge.adapters.fake.adapter import FakeAdapter

    return FakeAdapter()
