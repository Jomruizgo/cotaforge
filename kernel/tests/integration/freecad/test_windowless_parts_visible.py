"""An assembly without every part open in a window, on the REAL visible worker (``FreeCADAdapter(visible=True)``: the
FreeCAD GUI binary).

An ``App::Link`` needs the part's document LOADED, not shown (``worker/gui.py`` has the measurements on 1.1.3). So a
part that an assembly uses loses its window, a part the harness loads for an assembly never gets one, a window comes
back when the part is switched to for editing, and a file the engineer opened by hand is never closed. The window
counts come from FreeCAD's own MDI area (``test_view_state what="windows"``).

Needs a display (Windows opens its own window); carries the ``visible`` marker (serially, never with xdist).
"""

from __future__ import annotations

import os
import sys
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.contract import (
    ConnectRequest,
    DocumentHandle,
    DocumentKind,
    Empty,
    InsertComponentRequest,
    OpenDocumentRequest,
    SetActiveDocumentRequest,
    Transform,
)
from cotaforge.contract.requests import BoxRequest
from tests.integration.freecad.test_part_assemblies import insert, new_assembly, part, save, volume
from tests.integration.freecad.worker_tools import call_hook, kill_worker

pytestmark = [
    pytest.mark.freecad,
    pytest.mark.visible,
    pytest.mark.skipif(
        sys.platform != "win32" and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")),
        reason="the visible worker needs a display (DISPLAY / WAYLAND_DISPLAY)",
    ),
]

SIZES = {"pa": (10, 10, 10), "pb": (20, 10, 10), "pc": (30, 10, 10)}
LINKS = (
    "import os\n"
    "links = [o for o in doc.Objects if o.TypeId == 'App::Link']\n"
    "RESULT = [len(links), all(o.LinkedObject is not None for o in links), "
    "sorted(os.path.basename(o.LinkedObject.Document.FileName) for o in links)]\n"
)
BY_HAND = "import FreeCAD\nRESULT = FreeCAD.openDocument(%r).Name\n"


def new_window(freecad_root: Path, tmp_path: Path, tag: str = "logs") -> FreeCADAdapter:
    return FreeCADAdapter(
        freecad_root=freecad_root, timeout_s=300.0, log_dir=tmp_path / tag, visible=True, enable_test_hooks=True
    )


@pytest.fixture
def window(freecad_root: Path, tmp_path: Path) -> Iterator[FreeCADAdapter]:
    with new_window(freecad_root, tmp_path) as adapter:
        yield adapter


def seen(adapter: FreeCADAdapter) -> tuple[list[str], dict[str, bool]]:
    """The titles of the document windows open, and for each FreeCAD document whether it has one."""
    state = call_hook(adapter, "test_view_state", {"what": "windows"})["value"]
    return list(state["windows"]), dict(state["documents"])


def titled(titles: list[str], name: str) -> list[str]:
    return [t for t in titles if t.startswith(f"{name} :")]


def links_of(adapter: FreeCADAdapter, tmp_path: Path) -> list[object]:
    script = tmp_path / "links.py"
    script.write_text(LINKS, encoding="utf-8")
    return list(call_hook(adapter, "test_view_state", {"what": "script", "path": str(script)})["value"])


def activate(adapter: FreeCADAdapter, doc_id: str, *, refresh: bool = False, show: bool = True) -> None:
    adapter.set_active_document(
        SetActiveDocumentRequest(operation_id="act", document=DocumentHandle(id=doc_id), refresh=refresh, window=show)
    )


def three_parts(adapter: FreeCADAdapter, at: Path) -> dict[str, str]:
    return {name: part(adapter, name, size, at) for name, size in SIZES.items()}


def test_three_parts_inserted_leave_one_window_and_the_links_resolve(window: FreeCADAdapter, tmp_path: Path) -> None:
    ids = three_parts(window, tmp_path)
    titles, _ = seen(window)
    assert [bool(titled(titles, n)) for n in SIZES] == [
        True,
        True,
        True,
    ]  # while they are modelled, each has its window

    new_assembly(window, tmp_path)
    for i, (name, doc_id) in enumerate(ids.items()):
        result = insert(window, f"ins_{name}", doc_id, (i * 40, 0, 0))
        assert any("window" in w and name in w for w in result.warnings), result.warnings  # the agent is told

    titles, documents = seen(window)
    assert len(titles) == 1 and titles[0].startswith("asm :"), titles  # three parts in an assembly, ONE window
    assert all(
        name in documents and documents[name] is False for name in SIZES
    )  # the parts are loaded, without a window
    assert volume(window) == pytest.approx(sum(x * y * z for x, y, z in SIZES.values()))
    count, resolved, files = links_of(window, tmp_path)
    assert count == 3 and resolved is True and files == ["pa.FCStd", "pb.FCStd", "pc.FCStd"]

    save(window, tmp_path / "asm.FCStd", seq=2)
    xml = zipfile.ZipFile(tmp_path / "asm.FCStd").read("Document.xml").decode()
    assert all(f'file="{name}.FCStd"' in xml for name in SIZES)  # relative file names

    # the same part inserted again: its handle still works with no window
    again = insert(window, "ins_pa_2", ids["pa"], (0, 40, 0))
    assert again.component is not None and len(seen(window)[0]) == 1


def test_a_part_is_edited_from_the_assembly_by_switching_to_it_and_loses_its_window_again(
    window: FreeCADAdapter, tmp_path: Path
) -> None:
    ids = three_parts(window, tmp_path)
    asm = new_assembly(window, tmp_path)
    for i, doc_id in enumerate(ids.values()):
        insert(window, f"ins_{i}", doc_id, (i * 40, 0, 0))
    before = volume(window)
    assert len(seen(window)[0]) == 1

    activate(window, ids["pa"])  # the agent has to change the part
    titles, documents = seen(window)
    assert titled(titles, "pa") and documents["pa"] is True  # it opens visibly to be edited
    window.box(BoxRequest(operation_id="more", size=(5, 5, 5), transform=Transform(origin=(20, 0, 0))))
    save(window, tmp_path / "pa.FCStd", seq=2)

    activate(window, asm, refresh=True)  # back to the assembly: it follows the part, the part's window goes
    titles, documents = seen(window)
    assert len(titles) == 1 and titles[0].startswith("asm :"), titles
    assert documents["pa"] is False and volume(window) == pytest.approx(before + 125)


def test_a_part_with_unsaved_changes_keeps_its_window_when_the_assembly_comes_back(
    window: FreeCADAdapter, tmp_path: Path
) -> None:
    ids = three_parts(window, tmp_path)
    asm = new_assembly(window, tmp_path)
    insert(window, "ins_pa", ids["pa"])
    activate(window, ids["pa"])
    window.box(BoxRequest(operation_id="more", size=(5, 5, 5), transform=Transform(origin=(20, 0, 0))))  # not saved

    activate(window, asm)

    titles, documents = seen(window)
    assert titled(titles, "pa") and documents["pa"] is True  # closing it would ask the engineer whether to save it


def test_the_parts_of_a_reopened_assembly_are_loaded_without_windows(freecad_root: Path, tmp_path: Path) -> None:
    first = new_window(freecad_root, tmp_path, "logs1")
    first.connect(ConnectRequest(operation_id="c"))
    ids = three_parts(first, tmp_path)
    new_assembly(first, tmp_path)
    for i, doc_id in enumerate(ids.values()):
        insert(first, f"ins_{i}", doc_id, (i * 40, 0, 0))
    saved = save(first, tmp_path / "asm.FCStd", seq=2)
    expected = volume(first)
    kill_worker(first)

    with new_window(freecad_root, tmp_path, "logs2") as second:
        for name in SIZES:
            opened = second.open_document(
                OpenDocumentRequest(operation_id="o", path=str(tmp_path / f"{name}.FCStd"), hidden=True)
            )
            assert opened.windowless is True and opened.borrowed is False
        assert seen(second)[0] == []  # three parts loaded, nothing to look at
        reopened = second.open_document(
            OpenDocumentRequest(operation_id="o", path=str(tmp_path / "asm.FCStd"), adapter_state=saved.adapter_state)
        )
        assert reopened.document.kind is DocumentKind.assembly and reopened.windowless is False
        titles, _ = seen(second)
        assert len(titles) == 1 and titles[0].startswith("asm :"), titles  # the engineer sees the assembly only
        assert volume(second) == pytest.approx(expected)
        count, resolved, _ = links_of(second, tmp_path)
        assert count == 3 and resolved is True


def test_a_part_the_engineer_opened_by_hand_stays_open_and_shown(window: FreeCADAdapter, tmp_path: Path) -> None:
    ids = three_parts(window, tmp_path)
    mine = ids["pb"]
    window.set_active_document(SetActiveDocumentRequest(operation_id="a", document=DocumentHandle(id=mine)))
    window.close_document(Empty(operation_id="close"))  # the harness lets go of it ...
    asm = new_assembly(window, tmp_path)
    script = tmp_path / "hand.py"
    script.write_text(BY_HAND % str(tmp_path / "pb.FCStd"), encoding="utf-8")  # ... and the engineer opens it himself
    call_hook(window, "test_view_state", {"what": "script", "path": str(script)})
    assert titled(seen(window)[0], "pb")

    opened = window.open_document(OpenDocumentRequest(operation_id="o", path=str(tmp_path / "pb.FCStd"), hidden=True))
    assert opened.borrowed is True and opened.windowless is False  # his document, not ours
    activate(window, asm)
    insert(window, "ins_pb", opened.document.handle.id)
    insert(window, "ins_pa", ids["pa"], (40, 0, 0))

    titles, documents = seen(window)
    assert titled(titles, "pb") and documents["pb"] is True  # the one he opened keeps its window ...
    assert documents["pa"] is False  # ... and the one the harness made loses it

    activate(window, opened.document.handle.id)
    window.close_document(Empty(operation_id="close"))  # closing it from the harness only lets go of it
    titles, documents = seen(window)
    assert titled(titles, "pb") and "pb" in documents


def test_inserting_by_handle_still_works_for_a_part_without_a_window(window: FreeCADAdapter, tmp_path: Path) -> None:
    ids = three_parts(window, tmp_path)
    new_assembly(window, tmp_path)
    insert(window, "first", ids["pc"])
    assert seen(window)[1]["pc"] is False

    second = window.insert_component(
        InsertComponentRequest(
            operation_id="second", part=DocumentHandle(id=ids["pc"]), transform=Transform(origin=(0, 30, 0))
        )
    )

    assert second.component is not None and volume(window) == pytest.approx(2 * 30 * 10 * 10)
