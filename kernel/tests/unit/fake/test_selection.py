"""``get_selection``/``set_selection`` and the ``SelectionProducer`` push seam on the fake adapter."""

from __future__ import annotations

from cotaforge.contract import Empty, SelectionResult
from tests.unit.fake.conftest import Session


def test_get_selection_is_empty_by_default(session: Session) -> None:
    session.new_part()
    result = session.adapter.get_selection(Empty(operation_id="q"))
    assert result.entries == ()


def test_set_selection_reports_face_with_owner_and_body(session: Session) -> None:
    session.new_part()
    result = session.plate("base")
    face = result.created_faces[0]

    session.adapter.set_selection([face])
    selection = session.adapter.get_selection(Empty(operation_id="q"))

    assert len(selection.entries) == 1
    entry = selection.entries[0]
    assert entry.kind == "face"
    assert entry.ref.adapter == "fake"
    assert entry.ref.token == face.id
    assert entry.label == f"face {face.id}"
    assert entry.owner_label == "extrude_1"  # FakeFeature.name of the extrude that created the face
    assert entry.body is not None
    assert entry.feature == result.feature


def test_set_selection_accepts_a_label_override(session: Session) -> None:
    session.new_part()
    result = session.plate()
    face = result.created_faces[0]

    session.adapter.set_selection([face], labels={face.id: "top face"})
    entry = session.adapter.get_selection(Empty(operation_id="q")).entries[0]
    assert entry.label == "top face"


def test_set_selection_with_an_edge_reports_edge_kind_and_owner(session: Session) -> None:
    session.new_part()
    result = session.plate()
    edge = result.created_edges[0]

    session.adapter.set_selection([edge])
    entry = session.adapter.get_selection(Empty(operation_id="q")).entries[0]
    assert entry.kind == "edge"
    assert entry.ref.token == edge.id


def test_set_selection_replaces_the_previous_selection(session: Session) -> None:
    session.new_part()
    result = session.plate()
    faces = result.created_faces

    session.adapter.set_selection([faces[0]])
    session.adapter.set_selection([faces[1], faces[2]])
    selection = session.adapter.get_selection(Empty(operation_id="q"))
    assert {e.ref.token for e in selection.entries} == {faces[1].id, faces[2].id}


def test_set_selection_notifies_a_registered_listener(session: Session) -> None:
    session.new_part()
    result = session.plate()
    face = result.created_faces[0]

    received: list[SelectionResult] = []
    session.adapter.set_selection_listener(received.append)
    session.adapter.set_selection([face])

    assert len(received) == 1
    assert received[0].entries[0].ref.token == face.id


def test_set_selection_with_no_listener_does_not_raise(session: Session) -> None:
    session.new_part()
    result = session.plate()
    session.adapter.set_selection([result.created_faces[0]])  # no listener registered: must not raise


# --- unresolved selection items (review finding: get_selection must not silently drop what it cannot
# resolve to a stable ref -- a sketch, a datum, an assembly component, a whole-object selection) ---------


def test_get_selection_reports_unresolved_items_alongside_resolved_entries(session: Session) -> None:
    session.new_part()
    result = session.plate()
    face = result.created_faces[0]

    session.adapter.set_selection([face], unresolved=[("Sketch", "sketch")])
    selection = session.adapter.get_selection(Empty(operation_id="q"))

    assert len(selection.entries) == 1
    assert len(selection.unresolved) == 1
    assert selection.unresolved[0].label == "Sketch"
    assert selection.unresolved[0].reason == "sketch"


def test_get_selection_unresolved_is_empty_by_default(session: Session) -> None:
    session.new_part()
    assert session.adapter.get_selection(Empty(operation_id="q")).unresolved == ()


def test_set_selection_replaces_the_previous_unresolved_items_too(session: Session) -> None:
    session.new_part()
    session.adapter.set_selection([], unresolved=[("Sketch", "sketch")])
    session.adapter.set_selection([], unresolved=[("Datum1", "datum")])
    selection = session.adapter.get_selection(Empty(operation_id="q"))
    assert [u.label for u in selection.unresolved] == ["Datum1"]


# --- notify_manual_edit / set_manual_edit_listener (contract.ManualEditProducer seam) ---------------


def test_notify_manual_edit_reaches_a_registered_listener(session: Session) -> None:
    from cotaforge.contract import ManualEditNotice

    received: list[ManualEditNotice] = []
    session.adapter.set_manual_edit_listener(received.append)
    session.adapter.notify_manual_edit(
        artefact_kind="feature",
        ref="Pad001",
        by="engineer",
        reason="hand edit",
        changes=["Length 10 -> 15 mm"],
    )
    assert len(received) == 1
    notice = received[0]
    assert notice.artefact_kind == "feature" and notice.ref == "Pad001"
    assert notice.changes == ("Length 10 -> 15 mm",)


def test_notify_manual_edit_with_no_listener_does_not_raise(session: Session) -> None:
    session.adapter.notify_manual_edit(artefact_kind="feature", ref="Pad001", by="e", reason="r", changes=[])
