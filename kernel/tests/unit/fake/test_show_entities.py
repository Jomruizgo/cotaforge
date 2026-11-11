"""``show_entities`` on the fake adapter (agent-to-engineer counterpart of ``get_selection``): a test
double for callers of ``show_entities``."""

from __future__ import annotations

from cotaforge.contract import ShowEntitiesRequest
from tests.unit.fake.conftest import Session


def test_visible_by_default_reports_highlight_and_records_the_entities(session: Session) -> None:
    session.new_part()
    result = session.plate("base")
    face = result.created_faces[0]

    reply = session.adapter.show_entities(ShowEntitiesRequest(operation_id="show", entities=(face,)))

    assert reply.shown == "highlight"
    assert reply.reason is None
    assert session.adapter.highlighted == {face.id: face}


def test_headless_reports_unavailable_with_a_reason_and_never_raises(session: Session) -> None:
    session.new_part()
    result = session.plate("base")
    face = result.created_faces[0]
    session.adapter.visible = False

    reply = session.adapter.show_entities(ShowEntitiesRequest(operation_id="show", entities=(face,)))

    assert reply.shown == "unavailable"
    assert reply.reason
    assert session.adapter.highlighted == {}


def test_each_call_clears_the_previous_highlight_first(session: Session) -> None:
    session.new_part()
    result = session.plate("base")
    a, b = result.created_faces[0], result.created_faces[1]

    session.adapter.show_entities(ShowEntitiesRequest(operation_id="show1", entities=(a,)))
    assert session.adapter.highlighted == {a.id: a}

    session.adapter.show_entities(ShowEntitiesRequest(operation_id="show2", entities=(b,)))
    assert session.adapter.highlighted == {b.id: b}


def test_clear_flag_with_no_entities_leaves_nothing_highlighted(session: Session) -> None:
    session.new_part()
    result = session.plate("base")
    face = result.created_faces[0]
    session.adapter.show_entities(ShowEntitiesRequest(operation_id="show1", entities=(face,)))

    reply = session.adapter.show_entities(ShowEntitiesRequest(operation_id="clear", clear=True))

    assert reply.shown == "highlight"
    assert session.adapter.highlighted == {}


def test_note_is_recorded_on_the_request_for_test_introspection(session: Session) -> None:
    session.new_part()
    result = session.plate("base")
    face = result.created_faces[0]

    session.adapter.show_entities(
        ShowEntitiesRequest(operation_id="show", entities=(face,), note="this is the hole face")
    )

    assert session.adapter.show_entities_calls[-1].note == "this is the hole face"


def test_never_touches_the_engineers_own_ui_selection(session: Session) -> None:
    session.new_part()
    result = session.plate("base")
    face, other = result.created_faces[0], result.created_faces[1]
    session.adapter.set_selection([other])

    session.adapter.show_entities(ShowEntitiesRequest(operation_id="show", entities=(face,)))

    from cotaforge.contract import Empty

    selection = session.adapter.get_selection(Empty(operation_id="q"))
    assert [e.ref.token for e in selection.entries] == [other.id]
