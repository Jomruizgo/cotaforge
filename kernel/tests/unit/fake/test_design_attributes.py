"""``design_attributes`` / ``set_design_attributes`` on the fake: set, clear, and kept in the saved file."""

from pathlib import Path

from cotaforge.contract import Empty, OpenDocumentRequest, SaveDocumentRequest, SetDesignAttributesRequest
from tests.unit.fake.conftest import Session
from tests.unit.fake.test_open_document import _fresh, _stamp


def test_set_read_and_clear(session: Session) -> None:
    session.new_part("p")
    assert session.adapter.design_attributes(Empty(operation_id="r")).material is None
    set_ = SetDesignAttributesRequest(operation_id="s", material=" Acero ", route=("torno",))
    result = session.adapter.set_design_attributes(set_)
    assert (result.material, result.route) == ("Acero", ("torno",))
    kept = session.adapter.set_design_attributes(SetDesignAttributesRequest(operation_id="s2", route=()))
    assert (kept.material, kept.route) == ("Acero", ())
    assert (
        session.adapter.set_design_attributes(SetDesignAttributesRequest(operation_id="s3", material="")).material
        is None
    )


def test_they_are_part_of_the_saved_file(session: Session, tmp_path: Path) -> None:
    session.new_part("p")
    session.plate()
    session.adapter.set_design_attributes(
        SetDesignAttributesRequest(operation_id="s", material="Acero", route=("a", "b"))
    )
    path = tmp_path / "p.fakecad.json"
    saved = session.adapter.save_document(SaveDocumentRequest(operation_id="save", path=str(path), stamp=_stamp()))
    fresh = _fresh()
    fresh.open_document(OpenDocumentRequest(operation_id="open", path=str(path), adapter_state=saved.adapter_state))
    reopened = fresh.design_attributes(Empty(operation_id="r"))
    assert (reopened.material, reopened.route) == ("Acero", ("a", "b"))


def test_roles_are_set_replaced_kept_and_saved(session: Session, tmp_path: Path) -> None:
    session.new_part("p")
    session.plate()
    roles = {"s2": "soldado"}
    assert session.adapter.set_design_attributes(SetDesignAttributesRequest(operation_id="r", roles=roles)).roles == roles
    kept = session.adapter.set_design_attributes(SetDesignAttributesRequest(operation_id="m", material="Acero"))
    assert kept.roles == roles  # None leaves them
    path = tmp_path / "p.fakecad.json"
    saved = session.adapter.save_document(SaveDocumentRequest(operation_id="save", path=str(path), stamp=_stamp()))
    fresh = _fresh()
    fresh.open_document(OpenDocumentRequest(operation_id="open", path=str(path), adapter_state=saved.adapter_state))
    assert fresh.design_attributes(Empty(operation_id="r")).roles == roles
    assert session.adapter.set_design_attributes(SetDesignAttributesRequest(operation_id="c", roles={})).roles == {}
