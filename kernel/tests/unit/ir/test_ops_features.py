"""Tests for cotaforge.ir.ops_features: detail-only operation classes and their per-op validators."""

import pytest
from pydantic import ValidationError

from cotaforge.contract import (
    Axis,
    Direction,
    EndCondition,
    HoleStandard,
    MateAlignment,
    RectEntity,
    Side,
    SquareEntity,
)
from cotaforge.ir.ops_features import (
    OP_CATEGORY,
    OP_NAMES,
    ChamferOperation,
    CheckInterferenceOperation,
    CircularPatternOperation,
    CutOperation,
    ExtrudeOperation,
    FixComponentOperation,
    HoleOperation,
    InsertComponentOperation,
    LinearPatternOperation,
    MateCoincidentOperation,
    MateConcentricOperation,
    MateDistanceOperation,
    RevolveOperation,
    SketchOperation,
)
from cotaforge.ir.ops_primitives import Placement
from cotaforge.ir.refs import BodyRef, EdgeRef, FaceRef, PlaneRef


def _rect() -> RectEntity:
    return RectEntity(name="r1", center=(0.0, 0.0), width=10.0, height=10.0)


def test_sketch_operation_requires_at_least_one_entity() -> None:
    with pytest.raises(ValidationError):
        SketchOperation(id="s1", intent="base sketch", on=PlaneRef(name="XY"), entities=())


def test_sketch_operation_ok() -> None:
    op = SketchOperation(id="s1", intent="base sketch", on=PlaneRef(name="XY"), entities=(_rect(),))
    assert op.constraints == ()
    assert op.named_dimensions == ()


def test_sketch_operation_parses_a_square_with_a_named_side() -> None:
    op = SketchOperation.model_validate(
        {
            "id": "s1",
            "op": "sketch",
            "intent": "square boss",
            "on": {"kind": "plane", "name": "XY"},
            "entities": [{"kind": "square", "name": "boss", "center": [0.0, 0.0], "side": 20.0}],
            "named_dimensions": [{"name": "boss_side", "entity": "boss", "attribute": "side"}],
        }
    )
    assert op.entities == (SquareEntity(name="boss", center=(0.0, 0.0), side=20.0),)
    assert op.named_dimensions[0].attribute == "side"


def test_extrude_operation_defaults() -> None:
    op = ExtrudeOperation(id="e1", intent="extrude base", sketch="s1", depth_mm=8.0)
    assert op.direction == Direction.positive
    assert op.merge is True


def test_extrude_operation_rejects_nonpositive_depth() -> None:
    with pytest.raises(ValidationError):
        ExtrudeOperation(id="e1", intent="extrude base", sketch="s1", depth_mm=0.0)


def test_revolve_operation_defaults() -> None:
    op = RevolveOperation(id="r1", intent="revolve", sketch="s1", axis="u")
    assert op.angle_deg == 360.0


def test_cut_blind_requires_depth() -> None:
    with pytest.raises(ValidationError):
        CutOperation(id="c1", intent="cut hole", sketch="s1", end_condition=EndCondition.blind)


def test_cut_blind_with_depth_ok() -> None:
    op = CutOperation(id="c1", intent="cut hole", sketch="s1", end_condition=EndCondition.blind, depth_mm=5.0)
    assert op.depth_mm == 5.0


def test_cut_through_all_without_depth_ok() -> None:
    op = CutOperation(id="c1", intent="cut through", sketch="s1", end_condition=EndCondition.through_all)
    assert op.depth_mm is None


def test_cut_scope_defaults_to_none() -> None:
    op = CutOperation(id="c1", intent="cut hole", sketch="s1", end_condition=EndCondition.through_all)
    assert op.scope is None


def test_cut_scope_settable() -> None:
    op = CutOperation(
        id="c1",
        intent="cut hole",
        sketch="s1",
        end_condition=EndCondition.through_all,
        scope=(BodyRef(from_op="block"),),
    )
    assert op.scope == (BodyRef(from_op="block"),)


def test_cut_scope_rejects_empty_tuple() -> None:
    with pytest.raises(ValidationError):
        CutOperation(id="c1", intent="cut hole", sketch="s1", end_condition=EndCondition.through_all, scope=())


def test_extrude_operation_rejects_nonpositive_depth2() -> None:
    with pytest.raises(ValidationError):
        ExtrudeOperation(
            id="e1", intent="extrude base", sketch="s1", depth_mm=8.0, both_directions=True, depth2_mm=-1.0
        )


def test_cut_blind_rejects_nonpositive_depth() -> None:
    with pytest.raises(ValidationError):
        CutOperation(id="c1", intent="cut hole", sketch="s1", end_condition=EndCondition.blind, depth_mm=-5.0)


def test_cut_rejects_nonpositive_depth2() -> None:
    with pytest.raises(ValidationError):
        CutOperation(
            id="c1",
            intent="cut hole",
            sketch="s1",
            end_condition=EndCondition.blind,
            depth_mm=5.0,
            both_directions=True,
            depth2_mm=-2.0,
        )


def test_hole_blind_requires_depth() -> None:
    face = FaceRef(from_op="e1")
    with pytest.raises(ValidationError):
        HoleOperation(
            id="h1",
            intent="drill",
            face=face,
            position=(10.0, 10.0),
            standard=HoleStandard.simple,
            diameter_mm=6.6,
            end_condition=EndCondition.blind,
        )


def test_hole_blind_rejects_nonpositive_depth() -> None:
    face = FaceRef(from_op="e1")
    with pytest.raises(ValidationError):
        HoleOperation(
            id="h1",
            intent="drill",
            face=face,
            position=(10.0, 10.0),
            standard=HoleStandard.simple,
            diameter_mm=6.6,
            end_condition=EndCondition.blind,
            depth_mm=-5.0,
        )


def test_hole_iso_requires_size() -> None:
    face = FaceRef(from_op="e1")
    with pytest.raises(ValidationError):
        HoleOperation(
            id="h1",
            intent="drill",
            face=face,
            position=(10.0, 10.0),
            standard=HoleStandard.iso_clearance,
            diameter_mm=6.6,
            end_condition=EndCondition.through_all,
        )


def test_hole_iso_with_size_ok() -> None:
    face = FaceRef(from_op="e1")
    op = HoleOperation(
        id="h1",
        intent="drill",
        face=face,
        position=(10.0, 10.0),
        standard=HoleStandard.iso_clearance,
        size="M6",
        diameter_mm=6.6,
        end_condition=EndCondition.through_all,
    )
    assert op.size == "M6"


def test_hole_simple_without_size_ok() -> None:
    face = FaceRef(from_op="e1")
    op = HoleOperation(
        id="h1",
        intent="drill",
        face=face,
        position=(10.0, 10.0),
        standard=HoleStandard.simple,
        diameter_mm=6.6,
        end_condition=EndCondition.through_all,
    )
    assert op.size is None


def test_linear_pattern_second_axis_all_or_none() -> None:
    with pytest.raises(ValidationError):
        LinearPatternOperation(
            id="p1",
            intent="pattern holes",
            features=("h1",),
            direction="X",
            spacing_mm=20.0,
            count=3,
            spacing2_mm=10.0,
        )


def test_linear_pattern_second_axis_all_set_ok() -> None:
    op = LinearPatternOperation(
        id="p1",
        intent="pattern holes",
        features=("h1",),
        direction="X",
        spacing_mm=20.0,
        count=2,
        direction2="Y",
        spacing2_mm=10.0,
        count2=2,
    )
    assert op.count2 == 2


def test_linear_pattern_single_axis_ok() -> None:
    op = LinearPatternOperation(
        id="p1", intent="pattern holes", features=("h1",), direction="X", spacing_mm=20.0, count=2
    )
    assert op.direction2 is None


def test_linear_pattern_requires_at_least_one_feature() -> None:
    with pytest.raises(ValidationError):
        LinearPatternOperation(id="p1", intent="pattern holes", features=(), direction="X", spacing_mm=20.0, count=2)


def test_circular_pattern_with_face_axis() -> None:
    op = CircularPatternOperation(
        id="p1", intent="pattern around", features=("h1",), axis=FaceRef(from_op="cyl1"), count=4
    )
    assert op.equal_spacing is True


def test_circular_pattern_with_edge_axis() -> None:
    op = CircularPatternOperation(
        id="p1",
        intent="pattern around",
        features=("h1",),
        axis=EdgeRef(of=FaceRef(from_op="cyl1"), curve="circle"),
        count=4,
    )
    assert op.count == 4


def test_circular_pattern_with_world_axis() -> None:
    op = CircularPatternOperation(
        id="p1",
        intent="pattern around",
        features=("h1",),
        axis=Axis(origin=(0.0, 0.0, 0.0), direction=(0.0, 0.0, 1.0)),
        count=4,
    )
    assert isinstance(op.axis, Axis)


def test_chamfer_distance2_requires_distance1_on() -> None:
    with pytest.raises(ValidationError):
        ChamferOperation(
            id="ch1",
            intent="chamfer edge",
            edges=(EdgeRef(of=FaceRef(from_op="e1"), curve="line"),),
            distance1_mm=1.0,
            distance2_mm=2.0,
        )


def test_chamfer_distance2_with_distance1_on_ok() -> None:
    op = ChamferOperation(
        id="ch1",
        intent="chamfer edge",
        edges=(EdgeRef(of=FaceRef(from_op="e1"), curve="line"),),
        distance1_mm=1.0,
        distance2_mm=2.0,
        distance1_on=FaceRef(from_op="e1"),
    )
    assert op.distance1_on is not None


def test_chamfer_equal_distance_ok() -> None:
    op = ChamferOperation(
        id="ch1",
        intent="chamfer edge",
        edges=(EdgeRef(of=FaceRef(from_op="e1"), curve="line"),),
        distance1_mm=1.0,
    )
    assert op.distance2_mm is None


def test_chamfer_requires_at_least_one_edge() -> None:
    with pytest.raises(ValidationError):
        ChamferOperation(id="ch1", intent="chamfer edge", edges=(), distance1_mm=1.0)


def test_insert_component_requires_placement() -> None:
    with pytest.raises(ValidationError):
        InsertComponentOperation(id="ins1", intent="insert bracket", part="bracket")  # type: ignore[call-arg]


def test_insert_component_ok() -> None:
    op = InsertComponentOperation(id="ins1", intent="insert bracket", part="bracket", placement=Placement())
    assert op.part == "bracket"


def test_fix_component_ok() -> None:
    op = FixComponentOperation(id="fix1", intent="fix bracket", component="ins1")
    assert op.component == "ins1"


def test_mate_concentric_defaults() -> None:
    op = MateConcentricOperation(id="m1", intent="mate", a=FaceRef(from_op="ins1"), b=FaceRef(from_op="ins2"))
    assert op.alignment == MateAlignment.aligned


def test_mate_coincident_defaults() -> None:
    op = MateCoincidentOperation(id="m1", intent="mate", a=FaceRef(from_op="ins1"), b=FaceRef(from_op="ins2"))
    assert op.alignment == MateAlignment.anti_aligned


def test_mate_distance_defaults() -> None:
    op = MateDistanceOperation(
        id="m1", intent="mate", a=FaceRef(from_op="ins1"), b=FaceRef(from_op="ins2"), distance_mm=5.0
    )
    assert op.side == Side.positive
    assert op.alignment == MateAlignment.anti_aligned


def test_mate_distance_rejects_negative_distance() -> None:
    with pytest.raises(ValidationError):
        MateDistanceOperation(
            id="m1", intent="mate", a=FaceRef(from_op="ins1"), b=FaceRef(from_op="ins2"), distance_mm=-1.0
        )


def test_check_interference_defaults() -> None:
    op = CheckInterferenceOperation(id="ck1", intent="check")
    assert op.components is None
    assert op.expect_zero is True


def test_refs_collects_chamfer_distance1_on_and_nested_edge_faces() -> None:
    edge_face = FaceRef(from_op="e1")
    on_face = FaceRef(from_op="e1")
    op = ChamferOperation(
        id="ch1",
        intent="chamfer edge",
        edges=(EdgeRef(of=edge_face, curve="line"),),
        distance1_mm=1.0,
        distance2_mm=2.0,
        distance1_on=on_face,
    )
    refs = op.refs()
    # The EdgeRef itself, its nested `of` FaceRef, and the top-level distance1_on
    # FaceRef must all be reachable from refs().
    assert op.edges[0] in refs
    assert edge_face in refs
    assert on_face in refs
    assert len(refs) == 3


def test_refs_collects_circular_pattern_face_ref_axis() -> None:
    axis_face = FaceRef(from_op="cyl1")
    op = CircularPatternOperation(id="p1", intent="pattern around", features=("h1",), axis=axis_face, count=4)
    refs = op.refs()
    assert refs == (axis_face,)


def test_refs_ignores_non_ref_axis() -> None:
    op = CircularPatternOperation(
        id="p1",
        intent="pattern around",
        features=("h1",),
        axis=Axis(origin=(0.0, 0.0, 0.0), direction=(0.0, 0.0, 1.0)),
        count=4,
    )
    assert op.refs() == ()


def test_op_names_lists_the_whole_vocabulary() -> None:
    expected = {
        "box",
        "cylinder",
        "boolean_union",
        "boolean_subtract",
        "boolean_intersect",
        "translate",
        "rotate",
        "copy_body",
        "sketch",
        "ref_plane",
        "ref_axis",
        "extrude",
        "revolve",
        "sweep",
        "cut",
        "hole",
        "linear_pattern",
        "circular_pattern",
        "chamfer",
        "fillet",
        "shell",
        "draft",
        "mirror",
        "mirror_body",
        "shrink_body",
        "mould_split",
        "split",
        "base_flange",
        "edge_flange",
        "unfold",
        "insert_component",
        "fix_component",
        "place_component",
        "add_joint",
        "set_joint",
        "mate_concentric",
        "mate_coincident",
        "mate_distance",
        "check_interference",
    }
    assert OP_NAMES == expected


# --- extra='forbid' cascades from OperationBase to every detail op ----------------------


def test_hole_operation_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        HoleOperation(
            id="h1",
            intent="drill",
            face=FaceRef(from_op="base"),
            position=(0.0, 0.0),
            standard=HoleStandard.simple,
            diameter_mm=5.0,
            end_condition=EndCondition.through_all,
            diamter_mm=5.0,  # type: ignore[call-arg]
        )


# --- Operation tiers (fish-mouth coping session, 2026-09-25): OP_CATEGORY classifies the detail
# vocabulary into everyday (default) / multibody / assembly so a UI can render an operations toolbar. ---


def test_op_category_covers_exactly_the_vocabulary() -> None:
    assert set(OP_CATEGORY) == OP_NAMES


def test_op_category_everyday_ops() -> None:
    everyday = {
        "sketch",
        "ref_plane",
        "ref_axis",
        "extrude",
        "cut",
        "revolve",
        "sweep",
        "hole",
        "fillet",
        "chamfer",
        "shell",
        "draft",
        "mirror",
        "linear_pattern",
        "circular_pattern",
    }
    assert {op for op, tier in OP_CATEGORY.items() if tier == "everyday"} == everyday


def test_op_category_multibody_ops() -> None:
    multibody = {
        "box",
        "cylinder",
        "boolean_union",
        "boolean_subtract",
        "boolean_intersect",
        "translate",
        "rotate",
        "copy_body",
        "mirror_body",
        "shrink_body",
        "mould_split",
        "split",
    }
    assert {op for op, tier in OP_CATEGORY.items() if tier == "multibody"} == multibody


def test_op_category_assembly_ops() -> None:
    assembly = {
        "insert_component",
        "fix_component",
        "place_component",
        "add_joint",
        "set_joint",
        "mate_concentric",
        "mate_coincident",
        "mate_distance",
        "check_interference",
    }
    assert {op for op, tier in OP_CATEGORY.items() if tier == "assembly"} == assembly
