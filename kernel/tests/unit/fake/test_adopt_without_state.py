"""``FakeAdapter.open_document`` without ``adapter_state`` (adoption) and ``close_document``."""

from pathlib import Path

import pytest

from cotaforge.contract import AdapterError, Empty, ErrorCode, MassRequest, OpenDocumentRequest
from tests.unit.fake.conftest import Session
from tests.unit.fake.test_open_document import _fresh, _saved


def test_without_adapter_state_the_file_is_adopted_and_close_forgets_the_document(
    session: Session, tmp_path: Path
) -> None:
    path, _ = _saved(session, tmp_path)
    volume = session.adapter.mass_properties(MassRequest(operation_id="m")).volume_mm3
    session.adapter.close_document(Empty(operation_id="close"))
    with pytest.raises(AdapterError) as gone:
        session.adapter.mass_properties(MassRequest(operation_id="m"))
    assert gone.value.info.code is ErrorCode.no_active_document

    fresh = _fresh()
    opened = fresh.open_document(OpenDocumentRequest(operation_id="open", path=str(path)))

    assert opened.stamp is not None and opened.rebind.clean and "adopted" in opened.rebind.notes[0]
    assert fresh.mass_properties(MassRequest(operation_id="m")).volume_mm3 == pytest.approx(volume)
