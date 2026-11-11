"""Shared plan fixtures for tests/unit/ir.

Not a test module itself (no ``test_`` prefix): pytest will not collect it.
"""


def plate_plan_dict() -> dict:
    """A plate with 4 holes: rect sketch on XY 100x60 centred (50,30), extrude 8,
    one ISO clearance M6 hole on the top face (position (10,10), through_all),
    patterned 2x2 with spacing 80/40 along X/Y.
    """
    return {
        "id": "plate_4_holes",
        "kind": "part",
        "description": "100x60x8 plate with a 2x2 pattern of M6 clearance holes",
        "operations": [
            {
                "id": "s1",
                "op": "sketch",
                "intent": "base rectangle, 100x60 centred at (50, 30)",
                "on": {"kind": "plane", "name": "XY"},
                "entities": [
                    {"kind": "rect", "name": "plate_outline", "center": [50.0, 30.0], "width": 100.0, "height": 60.0}
                ],
            },
            {
                "id": "e1",
                "op": "extrude",
                "intent": "extrude the plate to 8mm thickness",
                "sketch": "s1",
                "depth_mm": 8.0,
            },
            {
                "id": "h1",
                "op": "hole",
                "intent": "M6 clearance hole, seed of the 2x2 pattern",
                "face": {"kind": "face", "from_op": "e1", "select": {"normal": [0.0, 0.0, 1.0]}},
                "position": [10.0, 10.0],
                "standard": "iso_clearance",
                "size": "M6",
                "diameter_mm": 6.6,
                "end_condition": "through_all",
            },
            {
                "id": "p1",
                "op": "linear_pattern",
                "intent": "pattern the hole 2x2, spacing 80mm/40mm along X/Y",
                "features": ["h1"],
                "direction": "X",
                "spacing_mm": 80.0,
                "count": 2,
                "direction2": "Y",
                "spacing2_mm": 40.0,
                "count2": 2,
            },
        ],
    }
