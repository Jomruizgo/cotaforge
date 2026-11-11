"""Assemblies of part documents on the fake: instances placed by a pose, interference with distance, and the file of an
assembly that names its parts by relative path (a part changed after it was inserted updates on refresh and reopen)."""

import json
from pathlib import Path

import pytest

from cotaforge.adapters.fake import FakeAdapter
from cotaforge.contract import (
    AdapterError,
    BoundingBox,
    BoxRequest,
    ComponentHandle,
    ComponentRequest,
    DocumentHandle,
    DocumentKind,
    DocumentStamp,
    Empty,
    InsertComponentRequest,
    InterferenceRequest,
    MassRequest,
    OpenDocumentRequest,
    PlaceComponentRequest,
    SaveDocumentRequest,
    SetActiveDocumentRequest,
    Transform,
)
from tests.unit.fake.conftest import Session


def _stamp() -> DocumentStamp:
    return DocumentStamp(
        design_id="d",
        plan_id="p",
        plan_revision=1,
        save_seq=1,
        last_step_index=1,
        harness_version="0.0.0",
        plan_sha256="a" * 64,
    )


def _save(session: Session, path: Path) -> None:
    session.adapter.save_document(SaveDocumentRequest(operation_id="save", path=str(path), stamp=_stamp()))


def _cube_part(session: Session, name: str, side: float = 10.0) -> str:
    doc = session.new_part(name)
    session.adapter.box(BoxRequest(operation_id=f"{name}_box", size=(side, side, side)))
    return doc


def _insert(session: Session, op: str, part: str, origin: tuple[float, float, float] = (0, 0, 0)) -> object:
    return session.adapter.insert_component(
        InsertComponentRequest(operation_id=op, part=DocumentHandle(id=part), transform=Transform(origin=origin))
    )


def _bbox(session: Session, component: object) -> BoundingBox:
    return session.adapter.component_bounding_box(ComponentRequest(operation_id="q", component=component))  # type: ignore[arg-type]


def test_place_component_sets_the_pose_and_does_not_add_to_it(session: Session) -> None:
    part = _cube_part(session, "cube")
    session.new_part("asm", DocumentKind.assembly)
    comp = _insert(session, "ins", part, (5, 0, 0)).component  # type: ignore[attr-defined]

    first = session.adapter.place_component(
        PlaceComponentRequest(operation_id="p1", component=comp, transform=Transform(origin=(100, 0, 0)))
    )
    session.adapter.place_component(
        PlaceComponentRequest(operation_id="p2", component=comp, transform=Transform(origin=(100, 0, 0)))
    )

    assert first.component == comp
    assert _bbox(session, comp) == BoundingBox(min=(100, 0, 0), max=(110, 10, 10))


def test_place_component_is_undone_with_undo_last(session: Session) -> None:
    part = _cube_part(session, "cube")
    session.new_part("asm", DocumentKind.assembly)
    comp = _insert(session, "ins", part, (5, 0, 0)).component  # type: ignore[attr-defined]
    session.adapter.place_component(
        PlaceComponentRequest(operation_id="p1", component=comp, transform=Transform(origin=(100, 0, 0)))
    )

    session.adapter.undo_last(Empty(operation_id="u"))

    assert _bbox(session, comp).min == (5, 0, 0)


def test_the_same_part_can_be_inserted_several_times(session: Session) -> None:
    part = _cube_part(session, "cube")
    session.new_part("asm", DocumentKind.assembly)
    a = _insert(session, "a", part).component  # type: ignore[attr-defined]
    b = _insert(session, "b", part, (30, 0, 0)).component  # type: ignore[attr-defined]

    assert a != b
    mass = session.adapter.mass_properties(MassRequest(operation_id="m"))
    assert mass.volume_mm3 == pytest.approx(2000)


def test_interference_carries_the_minimum_distance_of_each_pair(session: Session) -> None:
    part = _cube_part(session, "cube")
    session.new_part("asm", DocumentKind.assembly)
    _insert(session, "a", part)
    _insert(session, "b", part, (5, 0, 0))  # overlaps a
    _insert(session, "c", part, (40, 0, 0))  # 30 mm from b? no: 25 from b (b ends at 15)

    result = session.adapter.check_interference(InterferenceRequest(operation_id="i"))

    by_pair = {(p.a.id, p.b.id): p for p in result.pairs}
    assert by_pair[("comp:1", "comp:2")].volume_mm3 == pytest.approx(500)
    assert by_pair[("comp:1", "comp:2")].distance_mm == 0
    assert by_pair[("comp:2", "comp:3")].distance_mm == pytest.approx(25)
    assert by_pair[("comp:1", "comp:3")].distance_mm == pytest.approx(30)


def test_a_part_changed_after_it_was_inserted_updates_when_the_assembly_is_made_active(session: Session) -> None:
    part = _cube_part(session, "cube")
    asm = session.new_part("asm", DocumentKind.assembly)
    comp = _insert(session, "a", part).component  # type: ignore[attr-defined]
    assert session.adapter.mass_properties(MassRequest(operation_id="m")).volume_mm3 == pytest.approx(1000)

    session.adapter.set_active_document(SetActiveDocumentRequest(operation_id="s", document=DocumentHandle(id=part)))
    session.adapter.box(BoxRequest(operation_id="more", size=(10, 10, 10), transform=Transform(origin=(20, 0, 0))))
    session.adapter.set_active_document(SetActiveDocumentRequest(operation_id="s", document=DocumentHandle(id=asm)))
    # a plain switch does not refresh
    assert session.adapter.mass_properties(MassRequest(operation_id="m")).volume_mm3 == pytest.approx(1000)
    session.adapter.set_active_document(
        SetActiveDocumentRequest(operation_id="s", document=DocumentHandle(id=asm), refresh=True)
    )

    assert session.adapter.mass_properties(MassRequest(operation_id="m")).volume_mm3 == pytest.approx(2000)
    assert _bbox(session, comp).max == (30, 10, 10)


def test_an_assembly_is_saved_with_its_parts_by_relative_path(session: Session, tmp_path: Path) -> None:
    part = _cube_part(session, "cube")
    _save(session, tmp_path / "parts" / "cube.fakecad.json")
    session.new_part("asm", DocumentKind.assembly)
    _insert(session, "a", part)

    _save(session, tmp_path / "asm.fakecad.json")

    payload = json.loads((tmp_path / "asm.fakecad.json").read_text(encoding="utf-8"))
    assert payload["parts"] == {part: "parts/cube.fakecad.json"}


def test_an_assembly_with_a_part_that_was_never_saved_cannot_be_saved(session: Session, tmp_path: Path) -> None:
    part = _cube_part(session, "cube")
    session.new_part("asm", DocumentKind.assembly)
    _insert(session, "a", part)

    with pytest.raises(AdapterError) as info:
        _save(session, tmp_path / "asm.fakecad.json")

    assert info.value.info.details["reason"] == "part_unsaved" and not (tmp_path / "asm.fakecad.json").exists()


def _reopen(tmp_path: Path, *, parts_first: bool, edit_part: bool = False) -> Session:
    first = Session(FakeAdapter())
    part = _cube_part(first, "cube")
    _save(first, tmp_path / "cube.fakecad.json")
    first.new_part("asm", DocumentKind.assembly)
    comp = _insert(first, "a", part).component  # type: ignore[attr-defined]
    first.adapter.place_component(
        PlaceComponentRequest(operation_id="p", component=comp, transform=Transform(origin=(7, 0, 0)))
    )
    _save(first, tmp_path / "asm.fakecad.json")
    if edit_part:  # the part changes on disk while the assembly is closed
        first.adapter.set_active_document(
            SetActiveDocumentRequest(operation_id="s", document=DocumentHandle(id=part))
        )
        first.adapter.box(BoxRequest(operation_id="more", size=(10, 10, 10), transform=Transform(origin=(0, 20, 0))))
        _save(first, tmp_path / "cube.fakecad.json")
    second = Session(FakeAdapter())
    if parts_first:
        second.adapter.open_document(OpenDocumentRequest(operation_id="o", path=str(tmp_path / "cube.fakecad.json")))
    return second


def test_an_assembly_whose_parts_are_not_open_says_which_files_it_needs(tmp_path: Path) -> None:
    second = _reopen(tmp_path, parts_first=False)

    with pytest.raises(AdapterError) as info:
        second.adapter.open_document(OpenDocumentRequest(operation_id="o", path=str(tmp_path / "asm.fakecad.json")))

    assert info.value.info.details["reason"] == "parts_missing"
    assert info.value.info.details["parts"] == "cube.fakecad.json"


def test_reopening_the_assembly_finds_its_parts_and_keeps_the_pose(tmp_path: Path) -> None:
    second = _reopen(tmp_path, parts_first=True)

    opened = second.adapter.open_document(
        OpenDocumentRequest(operation_id="o", path=str(tmp_path / "asm.fakecad.json"))
    )

    assert opened.document.kind is DocumentKind.assembly
    mass = second.adapter.mass_properties(MassRequest(operation_id="m"))
    assert mass.volume_mm3 == pytest.approx(1000)
    assert _bbox(second, ComponentHandle(id="comp:1")).min == (7, 0, 0)


def test_a_part_saved_again_while_the_assembly_was_closed_is_what_the_assembly_shows_on_reopen(tmp_path: Path) -> None:
    second = _reopen(tmp_path, parts_first=True, edit_part=True)

    second.adapter.open_document(OpenDocumentRequest(operation_id="o", path=str(tmp_path / "asm.fakecad.json")))

    # the part was saved with a second cube before this session opened it: the assembly shows both
    assert second.adapter.mass_properties(MassRequest(operation_id="m")).volume_mm3 == pytest.approx(2000)


def test_a_document_id_that_is_taken_gets_a_new_one_on_open(tmp_path: Path) -> None:
    other = Session(FakeAdapter())
    _cube_part(other, "cube")  # doc:1 as well
    _save(other, tmp_path / "cube.fakecad.json")
    second = Session(FakeAdapter())
    second.new_part("squatter")  # takes doc:1 in this adapter

    opened = second.adapter.open_document(
        OpenDocumentRequest(operation_id="o", path=str(tmp_path / "cube.fakecad.json"))
    )

    assert opened.document.handle.id != "doc:1"
    assert second.adapter.active_document(Empty(operation_id="a")).handle == opened.document.handle
