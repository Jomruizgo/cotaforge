"""Material and process route stored INSIDE the FreeCAD document: set before and after the bodies exist, saved and
reopened (stamped and adopted), and the strict reading that no hand edit can slip past."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.contract import (
    BoxRequest,
    DesignAttributes,
    DocumentKind,
    DocumentStamp,
    Empty,
    NewDocumentRequest,
    OpenDocumentRequest,
    RunNativeRequest,
    SaveDocumentRequest,
    SetDesignAttributesRequest,
)

pytestmark = pytest.mark.freecad


@pytest.fixture
def cad(freecad_root: Path, tmp_path: Path) -> Iterator[FreeCADAdapter]:
    adapter = FreeCADAdapter(freecad_root=freecad_root, timeout_s=120.0, log_dir=tmp_path / "logs")
    adapter.__enter__()
    adapter.new_document(NewDocumentRequest(operation_id="n", kind=DocumentKind.part, name="attrs"))
    yield adapter
    adapter.__exit__(None, None, None)


def stamp() -> DocumentStamp:
    return DocumentStamp(
        design_id="d",
        plan_id="attrs",
        plan_revision=1,
        save_seq=1,
        last_step_index=1,
        harness_version="0",
        plan_sha256="a" * 64,
    )


def attributes(cad: FreeCADAdapter) -> DesignAttributes:
    return cad.design_attributes(Empty(operation_id="read"))


def test_a_new_document_has_no_attributes(cad: FreeCADAdapter) -> None:
    assert attributes(cad).material is None and attributes(cad).route == ()


def test_set_before_the_first_body_and_read_back(cad: FreeCADAdapter) -> None:
    result = cad.set_design_attributes(
        SetDesignAttributesRequest(operation_id="s", material="  Acero 1045 ", route=("torno", "fresa"))
    )
    assert (result.material, result.route) == ("Acero 1045", ("torno", "fresa"))
    cad.box(BoxRequest(operation_id="b", size=(10, 10, 10)))
    assert attributes(cad).material == "Acero 1045"  # the metadata still says so for a body made later


def test_the_material_lives_on_every_body_and_none_leaves_the_rest_alone(cad: FreeCADAdapter) -> None:
    cad.box(BoxRequest(operation_id="a", size=(10, 10, 10)))
    cad.box(BoxRequest(operation_id="b", size=(5, 5, 5)))
    cad.set_design_attributes(SetDesignAttributesRequest(operation_id="m", material="Aluminio 6061"))
    cad.set_design_attributes(SetDesignAttributesRequest(operation_id="r", route=("fresa",)))
    assert (attributes(cad).material, attributes(cad).route) == ("Aluminio 6061", ("fresa",))
    made = cad.run_native(
        RunNativeRequest(
            operation_id="p",
            code="print([o.Name for o in doc.Objects if 'CotaforgeMaterial' in o.PropertiesList])",
            intent="look",
        )
    )
    assert made.stdout.count("Box") == 2, made.stdout


def test_clearing(cad: FreeCADAdapter) -> None:
    cad.box(BoxRequest(operation_id="a", size=(10, 10, 10)))
    cad.set_design_attributes(SetDesignAttributesRequest(operation_id="m", material="Acero", route=("torno",)))
    cleared = cad.set_design_attributes(SetDesignAttributesRequest(operation_id="c", material="", route=()))
    assert cleared.material is None and cleared.route == ()


def test_a_body_changed_by_hand_makes_the_reading_say_so(cad: FreeCADAdapter) -> None:
    cad.box(BoxRequest(operation_id="a", size=(10, 10, 10)))
    cad.set_design_attributes(SetDesignAttributesRequest(operation_id="m", material="Acero 1045"))
    cad.run_native(
        RunNativeRequest(
            operation_id="n",
            code=(
                "o = [o for o in doc.Objects if 'CotaforgeMaterial' in o.PropertiesList][0]\n"
                "o.CotaforgeMaterial = 'Latón'"
            ),
            intent="re-label by hand",
        )
    )
    assert attributes(cad).material == "Acero 1045 / Latón"


def test_they_survive_save_and_reopen_and_adoption(cad: FreeCADAdapter, tmp_path: Path) -> None:
    cad.box(BoxRequest(operation_id="a", size=(10, 10, 10)))
    cad.set_design_attributes(SetDesignAttributesRequest(operation_id="m", material="Acero 1045", route=("torno",)))
    path = tmp_path / "attrs.FCStd"
    saved = cad.save_document(SaveDocumentRequest(operation_id="save", path=str(path), stamp=stamp()))
    cad.close_document(Empty(operation_id="close"))

    cad.open_document(OpenDocumentRequest(operation_id="open", path=str(path), adapter_state=saved.adapter_state))
    assert (attributes(cad).material, attributes(cad).route) == ("Acero 1045", ("torno",))
    cad.close_document(Empty(operation_id="close"))

    cad.open_document(OpenDocumentRequest(operation_id="adopt", path=str(path)))
    assert (attributes(cad).material, attributes(cad).route) == ("Acero 1045", ("torno",))


def test_the_roles_live_in_the_document_and_survive_save_reopen_and_adoption(
    cad: FreeCADAdapter, tmp_path: Path
) -> None:
    cad.box(BoxRequest(operation_id="a", size=(10, 10, 10)))
    roles = {"s1": "soldado", "ins_pin": "actuador"}
    result = cad.set_design_attributes(SetDesignAttributesRequest(operation_id="r", roles=roles))
    assert result.roles == roles
    cad.set_design_attributes(SetDesignAttributesRequest(operation_id="m", material="Acero"))  # None leaves roles
    assert attributes(cad).roles == roles
    path = tmp_path / "roles.FCStd"
    saved = cad.save_document(SaveDocumentRequest(operation_id="save", path=str(path), stamp=stamp()))
    cad.close_document(Empty(operation_id="close"))
    cad.open_document(OpenDocumentRequest(operation_id="open", path=str(path), adapter_state=saved.adapter_state))
    assert attributes(cad).roles == roles
    cad.close_document(Empty(operation_id="close"))
    cad.open_document(OpenDocumentRequest(operation_id="adopt", path=str(path)))
    assert attributes(cad).roles == roles
    assert cad.set_design_attributes(SetDesignAttributesRequest(operation_id="c", roles={})).roles == {}
