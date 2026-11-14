"""Vocabulary tests: a plan accepts every operation of ``OP_NAMES`` and rejects any other name."""

import pytest

from cotaforge.ir.errors import PlanError
from cotaforge.ir.ops_features import OP_NAMES
from cotaforge.ir.plan import parse_plan


def test_the_vocabulary_has_thirty_nine_operations() -> None:
    # includes place_component, add_joint, set_joint (joints) and base_flange, edge_flange, unfold (sheet metal)
    assert len(OP_NAMES) == 39  # + shrink_body (the mould cavity) + mould_split (its core and cavity)


@pytest.mark.parametrize("op_name", ["stage", "pocket", "loft", "BOX", ""])
def test_an_operation_outside_the_vocabulary_is_rejected(op_name: str) -> None:
    data = {
        "id": "p1",
        "kind": "part",
        "operations": [{"id": "op1", "op": op_name, "intent": "test"}],
    }
    with pytest.raises(PlanError):
        parse_plan(data)


def test_a_plan_cannot_declare_a_stage() -> None:
    box = {"id": "b", "op": "box", "intent": "block", "size": [1.0, 1.0, 1.0]}
    with pytest.raises(PlanError):
        parse_plan({"id": "p1", "kind": "part", "stage": "detail", "operations": [box]})


def test_full_plan_parses_every_op_kind() -> None:
    """One plan exercising every op in OP_NAMES, in a legal dependency order."""
    data = {
        "id": "everything",
        "kind": "part",
        "operations": [
            # body-level primitives
            {"id": "box1", "op": "box", "intent": "block", "size": [40.0, 40.0, 40.0]},
            {"id": "cyl1", "op": "cylinder", "intent": "boss", "diameter_mm": 10.0, "height_mm": 40.0},
            {
                "id": "u1",
                "op": "boolean_union",
                "intent": "merge",
                "target": {"kind": "body", "from_op": "box1"},
                "tools": [{"kind": "body", "from_op": "cyl1"}],
            },
            {
                "id": "sub1",
                "op": "boolean_subtract",
                "intent": "remove",
                "target": {"kind": "body", "from_op": "u1"},
                "tools": [{"kind": "body", "from_op": "cyl1"}],
            },
            {
                "id": "int1",
                "op": "boolean_intersect",
                "intent": "keep overlap",
                "target": {"kind": "body", "from_op": "sub1"},
                "tools": [{"kind": "body", "from_op": "u1"}],
            },
            {
                "id": "tr1",
                "op": "translate",
                "intent": "move",
                "target": {"kind": "body", "from_op": "int1"},
                "offset": [1.0, 0.0, 0.0],
            },
            {
                "id": "rot1",
                "op": "rotate",
                "intent": "turn",
                "target": {"kind": "body", "from_op": "tr1"},
                "axis": "Z",
                "angle_deg": 45.0,
            },
            {
                "id": "copy1",
                "op": "copy_body",
                "intent": "twin",
                "source": {"kind": "body", "from_op": "rot1"},
                "offset": [0.0, 60.0, 0.0],
                "rotate": {"axis": "X", "angle_deg": 90.0, "about": [0.0, 0.0, 0.0]},
            },
            # detail-only ops
            {
                "id": "s1",
                "op": "sketch",
                "intent": "base sketch",
                "on": {"kind": "plane", "name": "XY"},
                "entities": [{"kind": "rect", "name": "r1", "center": [0.0, 0.0], "width": 20.0, "height": 20.0}],
            },
            {"id": "e1", "op": "extrude", "intent": "extrude base", "sketch": "s1", "depth_mm": 8.0},
            {
                "id": "s2",
                "op": "sketch",
                "intent": "cut sketch",
                "on": {"kind": "plane", "name": "XY"},
                "entities": [{"kind": "circle", "name": "c1", "center": [0.0, 0.0], "diameter": 6.0}],
            },
            {
                "id": "cut1",
                "op": "cut",
                "intent": "cut hole",
                "sketch": "s2",
                "end_condition": "blind",
                "depth_mm": 3.0,
            },
            {
                "id": "s3",
                "op": "sketch",
                "intent": "revolve sketch",
                "on": {"kind": "plane", "name": "XY"},
                # the circle sits off the u axis: a profile crossing the revolve axis is rejected by V8
                "entities": [{"kind": "circle", "name": "c2", "center": [5.0, 6.0], "diameter": 4.0}],
            },
            {"id": "rev1", "op": "revolve", "intent": "revolve profile", "sketch": "s3", "axis": "u"},
            {
                "id": "s4",
                "op": "sketch",
                "intent": "tube section",
                "on": {"kind": "plane", "name": "XY"},
                "entities": [{"kind": "circle", "name": "c3", "center": [0.0, 0.0], "diameter": 6.0}],
            },
            {
                "id": "sweep1",
                "op": "sweep",
                "intent": "bent tube",
                "sketch": "s4",
                "path": {"points": [[0, 0, 0], [0, 0, 40], [40, 0, 40]], "corner_radius_mm": 10},
            },
            {
                "id": "hole1",
                "op": "hole",
                "intent": "drill",
                "face": {"kind": "face", "from_op": "e1"},
                "position": [5.0, 5.0],
                "standard": "iso_clearance",
                "size": "M6",
                "diameter_mm": 6.6,
                "end_condition": "through_all",
            },
            {
                "id": "lp1",
                "op": "linear_pattern",
                "intent": "pattern holes",
                "features": ["hole1"],
                "direction": "X",
                "spacing_mm": 20.0,
                "count": 2,
            },
            {
                "id": "cp1",
                "op": "circular_pattern",
                "intent": "pattern around",
                "features": ["hole1"],
                "axis": {"kind": "face", "from_op": "cyl1"},
                "count": 4,
            },
            {
                "id": "ch1",
                "op": "chamfer",
                "intent": "chamfer edge",
                "edges": [{"kind": "edge", "of": {"kind": "face", "from_op": "e1"}, "curve": "line"}],
                "distance1_mm": 1.0,
            },
            {
                "id": "fi1",
                "op": "fillet",
                "intent": "fillet edge",
                "edges": [{"kind": "edge", "of": {"kind": "face", "from_op": "e1"}, "curve": "line"}],
                "radius_mm": 1.0,
            },
            {
                "id": "sh1",
                "op": "shell",
                "intent": "hollow the block",
                "faces": [{"kind": "face", "from_op": "e1", "select": {"normal": [0, 0, 1]}}],
                "thickness_mm": 2.0,
            },
            {
                "id": "dr1",
                "op": "draft",
                "intent": "taper the block walls",
                "faces": [{"kind": "face", "from_op": "e1", "select": {"normal": [1, 0, 0]}}],
                "neutral": {"kind": "plane", "name": "XY"},
                "pull": "Z",
                "angle_deg": 2.0,
            },
            {
                "id": "mi1",
                "op": "mirror",
                "intent": "mirror the extrusion",
                "features": ["e1"],
                "plane": {"kind": "plane", "name": "YZ"},
            },
            {
                "id": "mb1",
                "op": "mirror_body",
                "intent": "mirror the whole block",
                "source": {"kind": "body", "from_op": "e1"},
                "plane": {"kind": "plane", "name": "XZ"},
            },
            {
                "id": "sp1",
                "op": "split",
                "intent": "keep the upper half",
                "source": {"kind": "body", "from_op": "e1"},
                "plane": {"kind": "plane", "name": "XY"},
                "keep": "positive",
            },
        ],
    }
    plan = parse_plan(data)
    assert {op.op for op in plan.operations} == {
        "box",
        "cylinder",
        "boolean_union",
        "boolean_subtract",
        "boolean_intersect",
        "translate",
        "rotate",
        "copy_body",
        "sketch",
        "extrude",
        "cut",
        "revolve",
        "sweep",
        "hole",
        "linear_pattern",
        "circular_pattern",
        "chamfer",
        "fillet",
        "shell",
        "draft",
        "mirror",
        "mirror_body",
        "split",
    }


def test_assembly_only_ops_parse_under_detail_assembly_plan() -> None:
    """insert_component/fix_component/mate_*/check_interference need kind=assembly (V4)."""
    data = {
        "id": "asm",
        "kind": "assembly",
        "operations": [
            {
                "id": "ins1",
                "op": "insert_component",
                "intent": "insert bracket",
                "part": "bracket",
                "placement": {},
            },
            {
                "id": "ins2",
                "op": "insert_component",
                "intent": "insert pin",
                "part": "pin",
                "placement": {},
            },
            {"id": "fix1", "op": "fix_component", "intent": "fix bracket", "component": "ins1"},
            {
                "id": "mc1",
                "op": "mate_concentric",
                "intent": "align bores",
                "a": {"kind": "face", "from_op": "ins1"},
                "b": {"kind": "face", "from_op": "ins2"},
            },
            {
                "id": "mco1",
                "op": "mate_coincident",
                "intent": "seat flush",
                "a": {"kind": "face", "from_op": "ins1"},
                "b": {"kind": "face", "from_op": "ins2"},
            },
            {
                "id": "md1",
                "op": "mate_distance",
                "intent": "offset by 5mm",
                "a": {"kind": "face", "from_op": "ins1"},
                "b": {"kind": "face", "from_op": "ins2"},
                "distance_mm": 5.0,
            },
            {
                "id": "ck1",
                "op": "check_interference",
                "intent": "verify no overlap",
                "components": ["ins1", "ins2"],
            },
        ],
    }
    plan = parse_plan(data)
    assert {op.op for op in plan.operations} == {
        "insert_component",
        "fix_component",
        "mate_concentric",
        "mate_coincident",
        "mate_distance",
        "check_interference",
    }


def test_copy_body_parses_and_its_result_is_a_body_ref() -> None:
    plan = parse_plan(
        {
            "id": "twins",
            "kind": "part",
            "operations": [
                {"id": "a", "op": "cylinder", "intent": "wheel", "diameter_mm": 40.0, "height_mm": 10.0},
                {
                    "id": "b",
                    "op": "copy_body",
                    "intent": "second wheel",
                    "source": {"kind": "body", "from_op": "a"},
                    "offset": [0.0, 100.0, 0.0],
                },
                {
                    "id": "u",
                    "op": "boolean_union",
                    "intent": "fuse",
                    "target": {"kind": "body", "from_op": "a"},
                    "tools": [{"kind": "body", "from_op": "b"}],
                },
            ],
        }
    )
    assert [op.op for op in plan.operations] == ["cylinder", "copy_body", "boolean_union"]


def test_copy_body_source_must_be_a_body_producing_op() -> None:
    with pytest.raises(PlanError) as info:
        parse_plan(
            {
                "id": "bad",
                "kind": "part",
                "operations": [
                    {
                        "id": "s1",
                        "op": "sketch",
                        "intent": "s",
                        "on": {"kind": "plane", "name": "XY"},
                        "entities": [{"kind": "circle", "name": "c", "center": [0, 0], "diameter": 4}],
                    },
                    {"id": "c1", "op": "copy_body", "intent": "twin", "source": {"kind": "body", "from_op": "s1"}},
                ],
            }
        )
    assert info.value.rule == "V3"
