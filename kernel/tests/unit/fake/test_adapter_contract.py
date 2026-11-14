"""Session, health, empty-document semantics and contract conformance."""

import inspect

import pytest

import cotaforge
from cotaforge.adapters.fake import FakeAdapter
from cotaforge.contract import (
    TOOL_REGISTRY,
    Adapter,
    AdapterError,
    BoundingBox,
    ConnectRequest,
    DocumentHandle,
    DocumentKind,
    Empty,
    ErrorCode,
    FeatureHandle,
    MassRequest,
    NamedDimension,
    PlaneName,
    RectEntity,
    SetActiveDocumentRequest,
    SetTimeoutRequest,
    SketchConstraint,
    SketchRequest,
    SketchStatusRequest,
    SquareEntity,
)
from tests.unit.fake.conftest import Session


def test_fake_adapter_implements_every_abstract_method() -> None:
    assert not inspect.isabstract(FakeAdapter)
    adapter = FakeAdapter()
    assert isinstance(adapter, Adapter)
    assert adapter.name == "fake"


def test_capabilities_are_the_required_tools_plus_the_optional_ones_it_implements() -> None:
    required = {name for name, spec in TOOL_REGISTRY.items() if not spec.optional}
    assert FakeAdapter().capabilities().tools == frozenset(required) | {
        "sketch_status",
        "get_selection",
        "show_entities",
        "datum_plane",
        "datum_axis",
        "copy_body",
        "shrink_body",
        "draft_analysis",
        "base_flange",
        "edge_flange",
        "unfold",
        "sweep",
        "shell",
        "mirror",
        "mirror_body",
        "split",
        "edit_feature",
        "measure_bodies",
        "body_interference",
        "place_component",
        "add_joint",
        "set_joint",
        "joint_info",
        "sweep_joint",
        "save_document",
        "open_document",
        "close_document",
        "design_attributes",
        "set_design_attributes",
        "import_geometry",
        "list_holes",
    }
    assert "capture" not in FakeAdapter().capabilities().tools


def test_connect_reports_adapter_version_and_units() -> None:
    info = FakeAdapter().connect(ConnectRequest(operation_id="connect"))
    assert info.adapter == "fake"
    assert info.version == cotaforge.__version__
    assert info.units == "mm"


def test_session_noops_return_ack(session: Session) -> None:
    adapter = session.adapter
    adapter.set_timeout(SetTimeoutRequest(operation_id="t", timeout_s=5.0))
    adapter.suppress_interaction(Empty(operation_id="s"))
    adapter.restore_interaction(Empty(operation_id="r"))
    adapter.disconnect(Empty(operation_id="d"))


def test_queries_without_document_raise_no_active_document(session: Session) -> None:
    with pytest.raises(AdapterError) as excinfo:
        session.adapter.mass_properties(MassRequest(operation_id="m"))
    assert excinfo.value.info.code is ErrorCode.no_active_document
    assert excinfo.value.info.recoverable is False


def test_empty_document_mass_and_bbox(session: Session) -> None:
    session.new_part()
    mass = session.adapter.mass_properties(MassRequest(operation_id="m"))
    assert mass.volume_mm3 == 0.0
    assert mass.centroid == (0.0, 0.0, 0.0)
    assert mass.surface_mm2 == 0.0
    assert mass.inertia is None
    assert session.adapter.bounding_box(MassRequest(operation_id="b")) == BoundingBox(
        min=(0.0, 0.0, 0.0), max=(0.0, 0.0, 0.0)
    )
    assert session.adapter.feature_tree(Empty(operation_id="t")).nodes == ()


def test_new_document_and_active_document_switching(session: Session) -> None:
    first = session.new_part("a")
    second = session.new_part("b", DocumentKind.assembly)
    assert first != second
    assert session.adapter.active_document(Empty(operation_id="q")).handle.id == second
    info = session.adapter.set_active_document(
        SetActiveDocumentRequest(operation_id="s", document=DocumentHandle(id=first))
    )
    assert info.handle.id == first
    assert info.kind is DocumentKind.part
    with pytest.raises(AdapterError) as excinfo:
        session.adapter.set_active_document(
            SetActiveDocumentRequest(operation_id="s", document=DocumentHandle(id="doc:99"))
        )
    assert excinfo.value.info.code is ErrorCode.invalid_reference


def test_canary_and_validate_api_are_healthy_by_default(session: Session) -> None:
    canary = session.adapter.canary(Empty(operation_id="c"))
    assert canary.healthy is True
    assert canary.checks == {"plane": True, "render": True, "tree": True}
    assert session.adapter.validate_api(Empty(operation_id="v")).ok is True


def test_ids_are_deterministic_across_adapters() -> None:
    def build() -> list[str]:
        s = Session(FakeAdapter())
        s.new_part()
        result = s.plate()
        return [f.id for f in result.created_faces] + [e.id for e in result.created_edges]

    assert build() == build()
    assert build()[0] == "face:1"


def test_undo_on_empty_document_returns_none(session: Session) -> None:
    session.new_part()
    assert session.adapter.undo_last(Empty(operation_id="u")).reverted is None


def test_sketch_status_underdefined_without_named_dimensions(session: Session) -> None:
    session.new_part()
    result = session.adapter.sketch(
        SketchRequest(
            operation_id="s", on=PlaneName.XY, entities=(SquareEntity(name="boss", center=(0.0, 0.0), side=20.0),)
        )
    )
    status = session.adapter.sketch_status(
        SketchStatusRequest(operation_id="q", sketch=FeatureHandle(id=result.sketch.id))
    )
    assert status.dof > 0
    assert status.free_geometry == (0,)
    assert status.fully_constrained is False
    assert status.over_constrained is False
    assert status.geometry_count == 1
    assert status.sketch == FeatureHandle(id=result.sketch.id)


def test_sketch_status_fully_constrained_with_dimension_and_origin_constraint(session: Session) -> None:
    session.new_part()
    result = session.adapter.sketch(
        SketchRequest(
            operation_id="s",
            on=PlaneName.XY,
            entities=(SquareEntity(name="boss", center=(0.0, 0.0), side=20.0),),
            constraints=(SketchConstraint(kind="coincident", entities=("boss", "origin")),),
            named_dimensions=(NamedDimension(name="boss_side", entity="boss", attribute="side"),),
        )
    )
    status = session.adapter.sketch_status(
        SketchStatusRequest(operation_id="q", sketch=FeatureHandle(id=result.sketch.id))
    )
    assert status.dof == 0
    assert status.fully_constrained is True
    assert status.over_constrained is False
    assert status.free_geometry == ()


def test_sketch_status_duplicate_named_dimension_is_over_constrained(session: Session) -> None:
    session.new_part()
    result = session.adapter.sketch(
        SketchRequest(
            operation_id="s",
            on=PlaneName.XY,
            entities=(SquareEntity(name="boss", center=(0.0, 0.0), side=20.0),),
            named_dimensions=(
                NamedDimension(name="side_a", entity="boss", attribute="side"),
                NamedDimension(name="side_b", entity="boss", attribute="side"),
            ),
        )
    )
    status = session.adapter.sketch_status(
        SketchStatusRequest(operation_id="q", sketch=FeatureHandle(id=result.sketch.id))
    )
    assert status.over_constrained is True
    assert len(status.conflicts) == 1
    assert status.conflicts[0].kind == "redundant"
    assert status.conflicts[0].involved == (0,)


def test_sketch_status_on_unknown_sketch_raises_invalid_reference(session: Session) -> None:
    session.new_part()
    with pytest.raises(AdapterError) as excinfo:
        session.adapter.sketch_status(SketchStatusRequest(operation_id="q", sketch=FeatureHandle(id="sketch:99")))
    assert excinfo.value.info.code is ErrorCode.invalid_reference


def test_chamfered_rect_extrudes_with_the_octagon_volume_and_says_the_chamfer(session: Session) -> None:
    session.new_part()
    result = session.adapter.sketch(
        SketchRequest(
            operation_id="s",
            on=PlaneName.XY,
            entities=(RectEntity(name="plate", center=(50.0, 30.0), width=100.0, height=60.0, chamfer_mm=4.0),),
        )
    )
    assert result.profile_areas_mm2 == (pytest.approx(6000.0 - 32.0),)
    assert result.warnings == ("rect plate: four corners chamfered 4 mm at 45 degrees",)
    session.extrude("e", result.sketch.id, 8.0)
    mass = session.adapter.mass_properties(MassRequest(operation_id="m"))
    assert mass.volume_mm3 == pytest.approx((6000.0 - 32.0) * 8.0)


def test_chamfered_rect_needs_its_chamfer_dimension_to_be_fully_constrained(session: Session) -> None:
    session.new_part()
    entities = (RectEntity(name="plate", center=(0.0, 0.0), width=100.0, height=60.0, chamfer_mm=4.0),)
    dims = tuple(NamedDimension(name=f"p_{a}", entity="plate", attribute=a) for a in ("width", "height", "u", "v"))
    open_status = session.adapter.sketch_status(
        SketchStatusRequest(
            operation_id="q",
            sketch=FeatureHandle(
                id=session.adapter.sketch(
                    SketchRequest(operation_id="s1", on=PlaneName.XY, entities=entities, named_dimensions=dims)
                ).sketch.id
            ),
        )
    )
    assert open_status.dof == 1  # the chamfer size alone is left open
    named = (*dims, NamedDimension(name="p_chamfer", entity="plate", attribute="chamfer"))
    closed = session.adapter.sketch(
        SketchRequest(operation_id="s2", on=PlaneName.XY, entities=entities, named_dimensions=named)
    )
    status = session.adapter.sketch_status(
        SketchStatusRequest(operation_id="q2", sketch=FeatureHandle(id=closed.sketch.id))
    )
    assert status.dof == 0 and status.fully_constrained
