"""``FakeAdapter.edit_feature``: in-place editing by deterministic replay with stable ids.

The fake is the CI-without-licence double of the contract: the same eligibility policy as the FreeCAD worker (box,
cylinder and move_body edits; live dependents only), the same result shape, and a failed edit that leaves the document
exactly as it was.
"""

import math

import pytest

from cotaforge.adapters.fake import FakeAdapter
from cotaforge.contract import (
    Axis,
    BodyHandle,
    BooleanKind,
    BooleanRequest,
    BoxRequest,
    ConnectRequest,
    CopyBodyRequest,
    CylinderRequest,
    DocumentKind,
    EditFeatureRequest,
    Empty,
    FeatureEdit,
    FeatureHandle,
    MassRequest,
    MoveBodyRequest,
    NewDocumentRequest,
    OperationResult,
    Transform,
)

Z = (0.0, 0.0, 1.0)


class Part:
    """block 100 x 60 x 20 minus a D30 through bore, then moved +10 along X."""

    def __init__(self) -> None:
        self.adapter = FakeAdapter()
        self.adapter.connect(ConnectRequest(operation_id="c"))
        self.adapter.new_document(NewDocumentRequest(operation_id="d", kind=DocumentKind.part, name="block"))
        self.block = self.adapter.box(BoxRequest(operation_id="block", size=(100.0, 60.0, 20.0)))
        self.tool = self.adapter.cylinder(
            CylinderRequest(
                operation_id="tool", diameter_mm=30.0, height_mm=20.0, axis=Axis(origin=(50.0, 30.0, 0.0), direction=Z)
            )
        )
        self.bore = self.adapter.boolean(
            BooleanRequest(
                operation_id="bore",
                kind=BooleanKind.subtract,
                target=self.block.created_bodies[0],
                tools=(self.tool.created_bodies[0],),
            )
        )
        self.place = self.adapter.move_body(
            MoveBodyRequest(
                operation_id="place", body=self.block.created_bodies[0], transform=Transform(origin=(10.0, 0.0, 0.0))
            )
        )

    def volume(self) -> float:
        return self.adapter.mass_properties(MassRequest(operation_id="q")).volume_mm3

    def fingerprint(self) -> str:
        return self.adapter._active.fingerprint()  # type: ignore[union-attr]

    def edit(self, *edits: FeatureEdit, dry_run: bool = False):  # noqa: ANN201
        return self.adapter.edit_feature(EditFeatureRequest(operation_id="edit", edits=edits, dry_run=dry_run))


def _box(part: Part, size: tuple[float, float, float]) -> FeatureEdit:
    assert isinstance(part.block, OperationResult) and part.block.feature is not None
    return FeatureEdit(feature=part.block.feature, kind="box", request=BoxRequest(operation_id="block", size=size))


def _tool(part: Part, diameter: float, height: float = 20.0) -> FeatureEdit:
    assert part.tool.feature is not None
    return FeatureEdit(
        feature=part.tool.feature,
        kind="cylinder",
        request=CylinderRequest(
            operation_id="tool",
            diameter_mm=diameter,
            height_mm=height,
            axis=Axis(origin=(50.0, 30.0, 0.0), direction=Z),
        ),
    )


def test_the_fake_advertises_edit_feature() -> None:
    assert "edit_feature" in FakeAdapter().capabilities().tools


def test_a_dry_run_reports_what_would_recompute_and_touches_nothing() -> None:
    part = Part()
    before = part.fingerprint()

    result = part.edit(_tool(part, 40.0), dry_run=True)

    assert result.dry_run and not result.applied and result.rebuild_ok and result.refused == {}
    assert [f.id for f in result.recomputed] == [part.tool.feature.id, part.bore.feature.id, part.place.feature.id]  # type: ignore[union-attr]
    assert part.fingerprint() == before


def test_editing_the_tool_diameter_recomputes_the_bore_and_keeps_every_id() -> None:
    part = Part()
    assert part.volume() == pytest.approx(100 * 60 * 20 - math.pi * 15**2 * 20, rel=1e-9)
    faces_before = set(part.adapter._active.faces)  # type: ignore[union-attr]
    features_before = [n.handle.id for n in part.adapter.feature_tree(Empty(operation_id="t")).nodes]

    result = part.edit(_tool(part, 40.0))

    assert result.applied and result.rebuild_ok and result.failure is None and not result.refused
    assert part.volume() == pytest.approx(100 * 60 * 20 - math.pi * 20**2 * 20, rel=1e-9)
    assert [n.handle.id for n in part.adapter.feature_tree(Empty(operation_id="t")).nodes] == features_before
    # stable ids: no face was renamed, the bore wall is the same handle with a new radius
    assert faces_before == set(part.adapter._active.faces)  # type: ignore[union-attr]
    (delta,) = result.delta
    assert delta.before is not None and delta.after is not None
    assert delta.after.volume_mm3 < delta.before.volume_mm3
    assert result.refs.modified  # the bore wall moved
    assert result.refs.removed == () and result.refs.created == ()


def test_the_feature_tree_parameters_follow_the_edit() -> None:
    part = Part()
    part.edit(_box(part, (120.0, 60.0, 20.0)))
    node = next(n for n in part.adapter.feature_tree(Empty(operation_id="t")).nodes if n.name.startswith("box"))
    assert "120" in str(node.parameters["size"])


def test_a_batch_edits_several_features_in_one_call() -> None:
    part = Part()
    result = part.edit(_box(part, (120.0, 60.0, 20.0)), _tool(part, 20.0))
    assert result.applied
    assert part.volume() == pytest.approx(120 * 60 * 20 - math.pi * 10**2 * 20, rel=1e-9)


def test_editing_a_move_changes_the_final_position() -> None:
    part = Part()
    assert part.place.feature is not None
    edit = FeatureEdit(
        feature=part.place.feature,
        kind="move_body",
        request=MoveBodyRequest(
            operation_id="place", body=part.block.created_bodies[0], transform=Transform(origin=(30.0, 5.0, 0.0))
        ),
    )
    result = part.edit(edit)
    assert result.applied
    box = part.adapter.bounding_box(MassRequest(operation_id="q"))
    assert box.min[0] == pytest.approx(30.0) and box.min[1] == pytest.approx(5.0)


def test_a_failed_recompute_is_rolled_back_and_reported() -> None:
    """A tool laid across the block is outside the fake's containment rules, so the recompute of the bore fails: the
    document must be exactly what it was, the failure named."""
    part = Part()
    before, volume = part.fingerprint(), part.volume()
    assert part.tool.feature is not None
    across = FeatureEdit(
        feature=part.tool.feature,
        kind="cylinder",
        request=CylinderRequest(
            operation_id="tool",
            diameter_mm=30.0,
            height_mm=20.0,
            axis=Axis(origin=(0.0, 30.0, 10.0), direction=(1.0, 0.0, 0.0)),
        ),
    )

    result = part.edit(across, _box(part, (120.0, 60.0, 20.0)))

    assert not result.applied and not result.rebuild_ok and result.failure
    assert "bore" in result.failure and result.health and result.health[0].feature is not None
    assert part.fingerprint() == before and part.volume() == pytest.approx(volume)


def test_a_cut_whose_tool_misses_the_body_is_applied_with_a_warning() -> None:
    """Silent in the CAD, a flag here: the bore no longer removes anything. It may be what the engineer wanted, so the
    edit stands and the flag says it."""
    part = Part()
    assert part.tool.feature is not None and part.bore.feature is not None
    away = FeatureEdit(
        feature=part.tool.feature,
        kind="cylinder",
        request=CylinderRequest(
            operation_id="tool", diameter_mm=30.0, height_mm=20.0, axis=Axis(origin=(500.0, 30.0, 0.0), direction=Z)
        ),
    )

    result = part.edit(away)

    assert result.applied and result.rebuild_ok
    (flag,) = [f for f in result.flags if f.kind == "cut_without_effect"]
    assert flag.severity == "warning" and flag.features == (part.bore.feature,)
    assert part.volume() == pytest.approx(100 * 60 * 20, rel=1e-9)


def test_an_op_that_is_not_editable_in_place_is_refused_with_a_reason() -> None:
    part = Part()
    assert part.bore.feature is not None
    edit = FeatureEdit(
        feature=part.bore.feature,
        kind="boolean",
        request=BooleanRequest(
            operation_id="bore",
            kind=BooleanKind.union,
            target=part.block.created_bodies[0],
            tools=(part.tool.created_bodies[0],),
        ),
    )
    before = part.fingerprint()
    result = part.edit(edit)
    assert not result.applied and result.rebuild_ok
    assert "not supported yet" in result.refused[part.bore.feature.id]
    assert part.fingerprint() == before


def test_a_move_cannot_be_retargeted() -> None:
    part = Part()
    assert part.place.feature is not None
    edit = FeatureEdit(
        feature=part.place.feature,
        kind="move_body",
        request=MoveBodyRequest(operation_id="place", body=BodyHandle(id="body:99"), transform=Transform()),
    )
    assert "retargeting" in part.edit(edit).refused[part.place.feature.id]


def test_a_static_copy_among_the_dependents_refuses_the_edit() -> None:
    part = Part()
    part.adapter.copy_body(
        CopyBodyRequest(
            operation_id="twin", source=part.block.created_bodies[0], transform=Transform(origin=(0.0, 100.0, 0.0))
        )
    )
    result = part.edit(_box(part, (120.0, 60.0, 20.0)))
    assert not result.applied
    reason = result.refused[part.block.feature.id]  # type: ignore[union-attr]
    assert "twin" not in reason and "copy_body" in reason and "static copy" in reason


def test_an_unrelated_later_copy_does_not_block_the_edit() -> None:
    part = Part()
    other = part.adapter.box(
        BoxRequest(operation_id="other", size=(5.0, 5.0, 5.0), transform=Transform(origin=(500, 0, 0)))
    )
    part.adapter.copy_body(
        CopyBodyRequest(operation_id="twin", source=other.created_bodies[0], transform=Transform(origin=(0, 20, 0)))
    )
    assert part.edit(_box(part, (120.0, 60.0, 20.0))).applied


def test_an_op_of_another_kind_after_the_edited_one_refuses_conservatively() -> None:
    from cotaforge.contract import ExtrudeRequest, PlaneName, RectEntity, SketchRequest

    part = Part()
    sketch = part.adapter.sketch(
        SketchRequest(
            operation_id="sk",
            on=PlaneName.XY,
            entities=(RectEntity(name="r", center=(0.0, 0.0), width=5.0, height=5.0),),
        )
    )
    part.adapter.extrude(ExtrudeRequest(operation_id="boss", sketch=sketch.sketch, depth_mm=3.0))
    result = part.edit(_box(part, (120.0, 60.0, 20.0)))
    assert "boss" in result.refused[part.block.feature.id] or "extrude" in result.refused[part.block.feature.id]  # type: ignore[union-attr]


def test_a_hand_edited_document_cannot_be_edited_in_place() -> None:
    part = Part()
    part.adapter.inject_manual_feature("Pocket", "hand pocket")
    result = part.edit(_box(part, (120.0, 60.0, 20.0)))
    assert not result.applied and "hand-made" in next(iter(result.refused.values()))


def test_undo_forgets_the_undone_ops_and_the_edit_still_works() -> None:
    part = Part()
    part.adapter.undo_last(Empty(operation_id="u"))  # the move
    result = part.edit(_tool(part, 40.0))
    assert result.applied
    assert [f.id for f in result.recomputed] == [part.tool.feature.id, part.bore.feature.id]  # type: ignore[union-attr]


def test_an_edit_followed_by_undo_restores_the_previous_op() -> None:
    part = Part()
    part.edit(_tool(part, 40.0))
    part.adapter.undo_last(Empty(operation_id="u"))  # the move
    part.adapter.undo_last(Empty(operation_id="u"))  # the bore
    # the tool and the block are back as separate bodies: the block is whole again
    block = part.adapter.mass_properties(MassRequest(operation_id="q", body=part.block.created_bodies[0]))
    assert block.volume_mm3 == pytest.approx(100 * 60 * 20, rel=1e-9)


def test_ids_stay_deterministic_after_a_failed_attempt_consumed_some() -> None:
    """A failed op consumes ids; the replay must still give every later entity the id it has now."""
    part = Part()
    with pytest.raises(Exception):  # noqa: B017, PT011 - any AdapterError: the point is the consumed counters
        part.adapter.boolean(
            BooleanRequest(
                operation_id="bad",
                kind=BooleanKind.subtract,
                target=part.block.created_bodies[0],
                tools=(BodyHandle(id="body:77"),),
            )
        )
    extra = part.adapter.box(
        BoxRequest(operation_id="extra", size=(5.0, 5.0, 5.0), transform=Transform(origin=(300, 0, 0)))
    )
    faces = sorted(part.adapter._active.faces)  # type: ignore[union-attr]
    assert part.edit(_tool(part, 40.0)).applied
    assert sorted(part.adapter._active.faces) == faces  # type: ignore[union-attr]
    assert extra.feature is not None
    assert FeatureHandle(id=extra.feature.id).id in [
        n.handle.id for n in part.adapter.feature_tree(Empty(operation_id="t")).nodes
    ]


def test_the_fake_measures_every_body_the_cuts_and_stays_read_only() -> None:
    part = Part()
    assert "measure_bodies" in FakeAdapter().capabilities().tools
    before = part.fingerprint()
    reading = part.adapter.measure_bodies(Empty(operation_id="m"))
    assert part.fingerprint() == before
    (body,) = reading.bodies
    assert body.measure.solids == 1 and body.valid and body.measure.volume_mm3 == pytest.approx(part.volume())
    (cut,) = reading.cuts
    assert cut.feature == part.bore.feature and cut.removed_mm3 == pytest.approx(math.pi * 15.0**2 * 20.0)
    assert reading.overlaps == ()
