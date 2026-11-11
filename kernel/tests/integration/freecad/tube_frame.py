"""A neutral multi-body plan for the real-FreeCAD tests of editing in place, native save and resume.

Fourteen Part-workbench operations with the dependencies that make those mechanisms hard: a hollow tube (cylinder minus
cylinder), a hollow arm aimed by a rotation and trimmed by the tube, a lug (a plate with a hole) and the unions of the
three. It has the same shape as the plan ``tests/freecad_worker/check_edit_in_place.py`` drives at the worker level, but
goes through the IR, the executor builders and the verifier like a real plan.
"""

from typing import Any

__all__ = ["tube_frame_plan"]


def _body(op_id: str) -> dict[str, Any]:
    return {"kind": "body", "from_op": op_id}


def _cylinder(op_id: str, diameter: float, height: float, origin: tuple[float, float, float], axis: str = "Z") -> dict:
    return {
        "id": op_id,
        "op": "cylinder",
        "intent": f"{op_id} cylinder",
        "diameter_mm": diameter,
        "height_mm": height,
        "axis": axis,
        "origin": list(origin),
    }


def _subtract(op_id: str, target: str, tool: str) -> dict[str, Any]:
    return {
        "id": op_id,
        "op": "boolean_subtract",
        "intent": f"{op_id}: {target} minus {tool}",
        "target": _body(target),
        "tools": [_body(tool)],
    }


def _union(op_id: str, target: str, tool: str) -> dict[str, Any]:
    return {
        "id": op_id,
        "op": "boolean_union",
        "intent": f"{op_id}: {target} plus {tool}",
        "target": _body(target),
        "tools": [_body(tool)],
    }


def tube_frame_plan() -> dict[str, Any]:
    """The plan as an IR document (``parse_plan``-ready); fresh dictionaries on every call."""
    operations = [
        _cylinder("tube_o", 28.6, 150, (120, 0, 150)),
        _cylinder("tube_i", 25.4, 150, (120, 0, 150)),
        _subtract("hollow_tube", "tube_o", "tube_i"),
        _cylinder("arm_o", 19.05, 123.7, (120, 0, 150), "X"),
        _cylinder("arm_i", 15.9, 123.7, (120, 0, 150), "X"),
        _subtract("arm_hollow", "arm_o", "arm_i"),
        {
            "id": "arm_aimed",
            "op": "rotate",
            "intent": "aim the arm",
            "target": _body("arm_hollow"),
            "axis": "Z",
            "angle_deg": 165.964,
            "about": [120, 0, 150],
        },
        _cylinder("arm_cutter", 28.6, 250, (120, 0, 100)),
        _subtract("arm_trimmed", "arm_aimed", "arm_cutter"),
        {
            "id": "lug_plate",
            "op": "box",
            "intent": "lug plate",
            "size": [24, 10, 36],
            "placement": {"origin": [-12, 17, 132]},
        },
        _cylinder("lug_hole", 10, 14, (0, 15, 150), "Y"),
        _subtract("lug_drilled", "lug_plate", "lug_hole"),
        _union("arm_lug", "arm_trimmed", "lug_drilled"),
        _union("frame", "hollow_tube", "arm_lug"),
    ]
    description = "Hollow tube, aimed arm and drilled lug"
    return {"id": "tube_frame", "kind": "part", "description": description, "operations": operations}
