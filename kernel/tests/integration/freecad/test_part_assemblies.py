"""Assemblies of part DOCUMENTS on the real FreeCAD worker: each part is modelled in its own document and axes, saved,
and placed in an assembly (an ``Assembly::AssemblyObject`` holding ``App::Part`` components that ``App::Link`` to the
part's file). The assembly is saved, killed and reopened in a NEW worker, and a part saved again shows in it."""

import zipfile
from pathlib import Path
from typing import Any

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter, FreeCADSession
from cotaforge.contract import (
    AdapterError,
    ComponentHandle,
    ComponentRequest,
    ConnectRequest,
    DocumentHandle,
    DocumentKind,
    Empty,
    ExportMeshRequest,
    ExportRequest,
    InsertComponentRequest,
    InterferenceRequest,
    MassRequest,
    NewDocumentRequest,
    OpenDocumentRequest,
    PlaceComponentRequest,
    RunNativeRequest,
    SaveDocumentRequest,
    SetActiveDocumentRequest,
    Transform,
)
from cotaforge.contract.requests import BoxRequest
from tests.integration.freecad.worker_tools import kill_worker, stamp_for

pytestmark = pytest.mark.freecad


def new_adapter(freecad_root: Path, log_dir: Path) -> FreeCADAdapter:
    return FreeCADAdapter(freecad_root=freecad_root, timeout_s=300.0, log_dir=log_dir, enable_test_hooks=True)


def part(adapter: FreeCADAdapter, name: str, size: tuple[float, float, float], at: Path) -> str:
    doc = adapter.new_document(NewDocumentRequest(operation_id=f"new_{name}", kind=DocumentKind.part, name=name))
    adapter.box(BoxRequest(operation_id=f"{name}_box", size=size))
    save(adapter, at / f"{name}.FCStd")
    return doc.handle.id


def save(adapter: FreeCADAdapter, path: Path, seq: int = 1) -> Any:
    return adapter.save_document(
        SaveDocumentRequest(operation_id="save", path=str(path), stamp=stamp_for("p", 1, save_seq=seq))
    )


def insert(adapter: FreeCADAdapter, op: str, part_id: str, origin: tuple[float, float, float] = (0, 0, 0)) -> Any:
    return adapter.insert_component(
        InsertComponentRequest(operation_id=op, part=DocumentHandle(id=part_id), transform=Transform(origin=origin))
    )


def activate(adapter: FreeCADAdapter, doc_id: str, *, refresh: bool = False) -> None:
    adapter.set_active_document(
        SetActiveDocumentRequest(operation_id="act", document=DocumentHandle(id=doc_id), refresh=refresh)
    )


def volume(adapter: FreeCADAdapter) -> float:
    return adapter.mass_properties(MassRequest(operation_id="m")).volume_mm3


def freecad_assembly(adapter: FreeCADAdapter) -> tuple[str, float, tuple[float, ...]]:
    """What FreeCAD itself holds for the assembly (not the registry): the type of the container, the volume and the
    box of its shape after its own recompute, and how many App::Link objects point into other documents."""
    code = (
        "doc.recompute()\n"
        "asm = [o for o in doc.Objects if o.TypeId == 'Assembly::AssemblyObject'][0]\n"
        "links = [o for o in doc.Objects if o.TypeId == 'App::Link' and o.LinkedObject.Document != doc]\n"
        "b = asm.Shape.BoundBox\n"
        "print(asm.TypeId, len(links), round(asm.Shape.Volume, 3), *[round(x, 3) for x in "
        "(b.XMin, b.YMin, b.ZMin, b.XMax, b.YMax, b.ZMax)])"
    )
    result = adapter.run_native(RunNativeRequest(operation_id="native", code=code))
    assert result.ok, result
    words = result.stdout.split()
    return words[0], float(words[2]), tuple(float(w) for w in words[3:9])


def new_assembly(adapter: FreeCADAdapter, at: Path, name: str = "asm") -> str:
    doc = adapter.new_document(NewDocumentRequest(operation_id="new_asm", kind=DocumentKind.assembly, name=name))
    save(adapter, at / f"{name}.FCStd")  # a cross-document link needs the owner's file
    return doc.handle.id


def test_instances_of_two_parts_are_placed_measured_and_saved(freecad_root: Path, tmp_path: Path) -> None:
    with FreeCADSession(freecad_root=freecad_root, timeout_s=300.0, log_dir=tmp_path / "logs") as adapter:
        block = part(adapter, "block", (20, 20, 10), tmp_path)
        pin = part(adapter, "pin", (4, 4, 30), tmp_path)
        asm = new_assembly(adapter, tmp_path)

        first = insert(adapter, "ins_block", block)
        second = insert(adapter, "ins_pin_a", pin, (30, 0, 0))
        third = insert(adapter, "ins_pin_b", pin, (60, 0, 0))  # the same part, a second instance
        assert first.component and second.component and third.component
        assert len({first.component.id, second.component.id, third.component.id}) == 3
        assert volume(adapter) == pytest.approx(20 * 20 * 10 + 2 * 4 * 4 * 30)

        adapter.place_component(
            PlaceComponentRequest(
                operation_id="move_pin",
                component=third.component,
                transform=Transform(origin=(5, 5, 10)),
            )
        )
        box = adapter.component_bounding_box(ComponentRequest(operation_id="q", component=third.component))
        assert box.min == pytest.approx((5, 5, 10)) and box.max == pytest.approx((9, 9, 40))

        pairs = {
            (p.a.id, p.b.id): p
            for p in adapter.check_interference(InterferenceRequest(operation_id="i")).pairs
        }
        assert pairs[(first.component.id, third.component.id)].volume_mm3 == pytest.approx(0)  # sits on the block
        assert pairs[(first.component.id, third.component.id)].distance_mm == pytest.approx(0)
        assert pairs[(first.component.id, second.component.id)].distance_mm == pytest.approx(10)
        far = pairs[(second.component.id, third.component.id)].distance_mm
        assert far == pytest.approx((21**2 + 1) ** 0.5)  # 21 apart in x, 1 in y

        kind, fc_volume, fc_box = freecad_assembly(adapter)
        assert kind == "Assembly::AssemblyObject"  # the Assembly workbench runs headless and keeps the poses
        assert fc_volume == pytest.approx(volume(adapter))
        assert fc_box == pytest.approx((0, 0, 0, 34, 20, 40))

        saved = save(adapter, tmp_path / "asm.FCStd", seq=2)
        assert saved.adapter_state["components"][0]["part_file"] == "block.FCStd"
        assert asm

        # what leaves the program is the whole assembly, links resolved
        adapter.export_step(ExportRequest(operation_id="step", path=str(tmp_path / "asm.step")))
        adapter.export_mesh(ExportMeshRequest(operation_id="mesh", path=str(tmp_path / "asm.stl")))
        step = (tmp_path / "asm.step").read_text(encoding="utf-8", errors="replace")
        assert step.count("MANIFOLD_SOLID_BREP") == 3  # the block and two pins
        assert (tmp_path / "asm.stl").stat().st_size > 1000


def test_a_part_inside_a_moved_pose_overlaps_and_the_overlap_is_measured(freecad_root: Path, tmp_path: Path) -> None:
    with FreeCADSession(freecad_root=freecad_root, timeout_s=300.0, log_dir=tmp_path / "logs") as adapter:
        cube = part(adapter, "cube", (10, 10, 10), tmp_path)
        new_assembly(adapter, tmp_path)
        a = insert(adapter, "a", cube)
        b = insert(adapter, "b", cube, (5, 0, 0))
        assert a.component and b.component

        result = adapter.check_interference(InterferenceRequest(operation_id="i"))

        assert result.total_volume_mm3 == pytest.approx(500)
        assert result.pairs[0].distance_mm == 0


def test_insert_needs_the_files_it_links_to(freecad_root: Path, tmp_path: Path) -> None:
    with FreeCADSession(freecad_root=freecad_root, timeout_s=300.0, log_dir=tmp_path / "logs") as adapter:
        doc = adapter.new_document(NewDocumentRequest(operation_id="n", kind=DocumentKind.part, name="loose"))
        adapter.box(BoxRequest(operation_id="b", size=(5, 5, 5)))
        asm = adapter.new_document(NewDocumentRequest(operation_id="n", kind=DocumentKind.assembly, name="asm"))
        with pytest.raises(AdapterError) as no_asm_file:
            insert(adapter, "i", doc.handle.id)
        save(adapter, tmp_path / "asm.FCStd")
        with pytest.raises(AdapterError) as no_part_file:
            insert(adapter, "i", doc.handle.id)
        assert "no file" in no_asm_file.value.info.message and "save it" in no_asm_file.value.info.message
        assert "has no file" in no_part_file.value.info.message and asm


def test_the_assembly_reopens_in_a_new_worker_and_a_changed_part_shows(freecad_root: Path, tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    first = new_adapter(freecad_root, logs)
    first.connect(ConnectRequest(operation_id="c"))
    block = part(first, "block", (20, 20, 10), tmp_path)
    pin = part(first, "pin", (4, 4, 30), tmp_path)
    new_assembly(first, tmp_path)
    ins_block = insert(first, "ins_block", block)
    ins_pin = insert(first, "ins_pin", pin, (30, 0, 0))
    assert ins_block.component and ins_pin.component
    first.place_component(
        PlaceComponentRequest(operation_id="p", component=ins_pin.component, transform=Transform(origin=(7, 7, 10)))
    )
    saved = save(first, tmp_path / "asm.FCStd", seq=2)
    expected_volume = volume(first)
    kill_worker(first)

    # the parts are saved in the zip as links by RELATIVE file name
    document_xml = zipfile.ZipFile(tmp_path / "asm.FCStd").read("Document.xml").decode()
    assert 'file="block.FCStd"' in document_xml and 'file="pin.FCStd"' in document_xml

    # a part is changed and saved by another worker while the assembly is closed
    editor = new_adapter(freecad_root, logs)
    editor.connect(ConnectRequest(operation_id="c"))
    editor.open_document(OpenDocumentRequest(operation_id="o", path=str(tmp_path / "pin.FCStd")))
    editor.box(BoxRequest(operation_id="more", size=(4, 4, 30), transform=Transform(origin=(10, 0, 0))))
    save(editor, tmp_path / "pin.FCStd", seq=2)
    kill_worker(editor)

    second = new_adapter(freecad_root, logs)
    second.connect(ConnectRequest(operation_id="c"))
    with pytest.raises(AdapterError) as missing:
        second.open_document(
            OpenDocumentRequest(operation_id="o", path=str(tmp_path / "asm.FCStd"), adapter_state=saved.adapter_state)
        )
    assert missing.value.info.details["reason"] == "parts_missing"
    assert set(str(missing.value.info.details["parts"]).split("|")) == {"block.FCStd", "pin.FCStd"}

    second.open_document(OpenDocumentRequest(operation_id="o", path=str(tmp_path / "block.FCStd")))
    second.open_document(OpenDocumentRequest(operation_id="o", path=str(tmp_path / "pin.FCStd")))
    opened = second.open_document(
        OpenDocumentRequest(operation_id="o", path=str(tmp_path / "asm.FCStd"), adapter_state=saved.adapter_state)
    )
    assert opened.document.kind is DocumentKind.assembly

    pose = second.component_transform(ComponentRequest(operation_id="q", component=ins_pin.component))
    assert pose.origin == pytest.approx((7, 7, 10))
    # the part has two boxes now: the assembly shows the part as it is saved
    assert volume(second) == pytest.approx(expected_volume + 4 * 4 * 30)
    assert freecad_assembly(second)[1] == pytest.approx(volume(second))  # FreeCAD's own links agree
    kill_worker(second)


def test_a_part_changed_in_the_same_session_updates_the_assembly_on_refresh(freecad_root: Path, tmp_path: Path) -> None:
    with FreeCADSession(freecad_root=freecad_root, timeout_s=300.0, log_dir=tmp_path / "logs") as adapter:
        pin = part(adapter, "pin", (4, 4, 30), tmp_path)
        asm = new_assembly(adapter, tmp_path)
        comp = insert(adapter, "ins", pin).component
        assert comp and volume(adapter) == pytest.approx(480)

        activate(adapter, pin)
        adapter.box(BoxRequest(operation_id="more", size=(4, 4, 30), transform=Transform(origin=(10, 0, 0))))
        save(adapter, tmp_path / "pin.FCStd", seq=2)
        activate(adapter, asm)
        assert volume(adapter) == pytest.approx(480)  # a plain switch does not recompute the assembly
        activate(adapter, asm, refresh=True)

        assert volume(adapter) == pytest.approx(960)
        box = adapter.component_bounding_box(ComponentRequest(operation_id="q", component=ComponentHandle(id=comp.id)))
        assert box.max == pytest.approx((14, 4, 30))


def test_undo_of_an_insert_removes_the_component_and_its_links(freecad_root: Path, tmp_path: Path) -> None:
    with FreeCADSession(freecad_root=freecad_root, timeout_s=300.0, log_dir=tmp_path / "logs") as adapter:
        pin = part(adapter, "pin", (4, 4, 30), tmp_path)
        new_assembly(adapter, tmp_path)
        insert(adapter, "ins", pin)
        assert volume(adapter) == pytest.approx(480)

        adapter.undo_last(Empty(operation_id="u"))

        assert volume(adapter) == pytest.approx(0)
        insert(adapter, "again", pin)  # the assembly object went with the first insert and comes back
        assert volume(adapter) == pytest.approx(480)
