"""``feature_tree`` lists the DOCUMENT's features,
hand-made ones included, and ``resolve_reference`` goes stale when a feature is deleted by hand."""

import pytest

from cotaforge.adapters.fake import FakeAdapter
from cotaforge.contract import (
    AdapterError,
    BoxRequest,
    ConnectRequest,
    DocumentKind,
    Empty,
    EntityRequest,
    ErrorCode,
    FeatureHandle,
    NewDocumentRequest,
    ResolveRequest,
)


def _adapter() -> FakeAdapter:
    adapter = FakeAdapter()
    adapter.connect(ConnectRequest(operation_id="connect"))
    adapter.new_document(NewDocumentRequest(operation_id="new", kind=DocumentKind.part, name="p"))
    return adapter


def test_feature_tree_lists_a_hand_made_feature_in_tree_order() -> None:
    adapter = _adapter()
    box = adapter.box(BoxRequest(operation_id="b", size=(10.0, 10.0, 10.0)))
    manual = adapter.inject_manual_feature("Fillet", "Fillet1", {"radius_mm": 2.0})
    nodes = adapter.feature_tree(Empty(operation_id="tree")).nodes
    assert [n.handle for n in nodes] == [box.feature, manual]
    assert nodes[1].kind == "Fillet" and nodes[1].name == "Fillet1" and nodes[1].parameters == {"radius_mm": 2.0}


def test_edit_feature_is_visible_in_the_tree() -> None:
    adapter = _adapter()
    box = adapter.box(BoxRequest(operation_id="b", size=(10.0, 10.0, 10.0)))
    assert box.feature is not None
    adapter.hand_edit_feature(box.feature, name="Base plate", parameters={"size": "(12, 10, 10)"})
    node = adapter.feature_tree(Empty(operation_id="tree")).nodes[0]
    assert node.name == "Base plate" and node.parameters == {"size": "(12, 10, 10)"}


def test_deleted_feature_disappears_and_its_reference_goes_stale() -> None:
    adapter = _adapter()
    box = adapter.box(BoxRequest(operation_id="b", size=(10.0, 10.0, 10.0)))
    assert box.feature is not None
    ref = adapter.persistent_reference(EntityRequest(operation_id="ref", entity=box.feature))
    adapter.delete_feature(box.feature)
    assert adapter.feature_tree(Empty(operation_id="tree")).nodes == ()
    with pytest.raises(AdapterError) as info:
        adapter.resolve_reference(ResolveRequest(operation_id="resolve", ref=ref))
    assert info.value.info.code is ErrorCode.invalid_reference
    with pytest.raises(AdapterError):
        adapter.delete_feature(FeatureHandle(id="feature:404"))
