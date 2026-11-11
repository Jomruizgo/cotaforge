"""An assembly needs its parts LOADED, not shown: the parts it opens are loaded without a window, a saved part can be
inserted from its file without opening it, the parts this session loaded that nothing uses any more close with their
assembly, and a document the engineer opened by hand is never closed here."""

from pathlib import Path

import pytest
from cotaforge.adapters.fake import FakeAdapter

from tests.conftest import Caller, MakeSession
from tests.unit.test_part_documents import assembly_of_two_cubes, cube_part, insert
from tests.unit.test_tools import volume_of


def reopened(make: MakeSession, adapter: FakeAdapter, path: str = "asm.fake") -> tuple[Caller, str]:
    """A new server (nothing open) that opens the saved assembly."""
    fresh = Caller(make(adapter))
    return fresh, fresh.ok("doc", action="open", path=path)


def saved_assembly(call: Caller) -> None:
    assembly_of_two_cubes(call)
    call.ok("doc", action="save")


# --- reopening an assembly --------------------------------------------------------------------------------------------


def test_the_parts_of_a_reopened_assembly_are_loaded_without_a_window(call: Caller, make: MakeSession) -> None:
    saved_assembly(call)
    adapter = FakeAdapter()

    fresh, opened = reopened(make, adapter)

    assert volume_of(opened) == pytest.approx(2000)
    asked = {Path(r.path).name: r.hidden for r in adapter.open_calls}
    assert asked == {"asm.fake": False, "cube.fake": True}  # the assembly is the engineer's window; the part is not
    assert "cube (part)" in fresh.ok("doc", action="status")  # still an open document: switch brings it back


def test_switching_to_a_part_without_a_window_asks_for_its_window(call: Caller, make: MakeSession) -> None:
    saved_assembly(call)
    adapter = FakeAdapter()
    fresh, _ = reopened(make, adapter)

    fresh.ok("doc", action="switch", name="cube")

    assert adapter.activate_calls[-1].window is True  # the CAD gives a part that has none its window to be edited in


# --- closing what is no longer needed ---------------------------------------------------------------------------------


def test_closing_the_assembly_closes_the_parts_it_loaded_that_nothing_uses_now(call: Caller, make: MakeSession) -> None:
    saved_assembly(call)
    adapter = FakeAdapter()
    fresh, _ = reopened(make, adapter)

    closed = fresh.ok("doc", action="close")

    assert closed.startswith("closed: asm") and "also closed" in closed and "cube" in closed
    assert "other open documents" not in closed and "document: none" in closed
    assert len(adapter.closed_ids) == 2  # the assembly and the part
    assert adapter.activate_calls[-1].window is False  # reached to close it, not to show it
    assert "documents:" not in fresh.ok("doc", action="status")


def test_a_part_with_unsaved_changes_is_not_closed_with_its_assembly(call: Caller, make: MakeSession) -> None:
    saved_assembly(call)
    adapter = FakeAdapter()
    fresh, _ = reopened(make, adapter)
    fresh.ok("doc", action="switch", name="cube")
    fresh.ok("op", name="box", id="boss", params={"size": [5, 5, 5], "placement": {"origin": [20, 0, 0]}})
    fresh.ok("doc", action="switch", name="asm")
    fresh.ok("doc", action="save")  # the assembly follows the part it links to

    closed = fresh.ok("doc", action="close")

    assert "also closed" not in closed and "other open documents: cube (part)" in closed
    assert len(adapter.closed_ids) == 1  # only the assembly


def test_a_part_another_open_assembly_uses_is_not_closed(call: Caller, make: MakeSession) -> None:
    saved_assembly(call)
    call.ok("doc", action="new", name="second", kind="assembly")
    insert(call, "c", "cube")  # saves "second" and links it to the same part file
    call.ok("doc", action="save")
    adapter = FakeAdapter()
    fresh, _ = reopened(make, adapter)
    fresh.ok("doc", action="open", path="second.fakecad.json")
    fresh.ok("doc", action="switch", name="asm")
    fresh.ok("doc", action="save")

    closed = fresh.ok("doc", action="close")

    assert "also closed" not in closed and len(adapter.closed_ids) == 1
    assert "cube (part)" in closed and "second (assembly)" in closed


# --- a document the engineer opened by hand -------------------------------------------------------------------------


def test_a_part_the_engineer_had_open_is_never_closed_with_the_assembly(
    call: Caller, make: MakeSession, tmp_path: Path
) -> None:
    saved_assembly(call)
    adapter = FakeAdapter()
    adapter.borrowed_paths.add(str(tmp_path / "cube.fake"))
    fresh, _ = reopened(make, adapter)

    closed = fresh.ok("doc", action="close")

    assert "also closed" not in closed and "other open documents: cube (part)" in closed
    assert len(adapter.closed_ids) == 1
    fresh.ok("doc", action="switch", name="cube")
    told = fresh.ok("doc", action="close")
    assert "stays open in the CAD" in told  # closing it here forgets it, the engineer's window stays


# --- insert from the file of a part that is not open ----------------------------------------------------------------


def test_a_saved_part_is_inserted_from_its_file_without_opening_it(call: Caller, make: MakeSession) -> None:
    cube_part(call, "cube")
    call.ok("doc", action="close")  # saved and closed: nothing of it is open
    adapter = FakeAdapter()
    fresh = Caller(make(adapter))
    fresh.ok("doc", action="new", name="asm", kind="assembly")

    text = insert(fresh, "a", "cube.fake")

    assert "loaded cube from its file without a window" in text and volume_of(text) == pytest.approx(1000)
    assert [(Path(r.path).name, r.hidden) for r in adapter.open_calls] == [("cube.fake", True)]
    status = fresh.ok("doc", action="status")
    assert "asm (assembly) [active]" in status and "cube (part)" in status
    again = insert(fresh, "b", "cube.fake", (30, 0, 0))  # already loaded: the same document, no second open
    assert "loaded cube from its file" not in again and volume_of(again) == pytest.approx(2000)
    assert len(adapter.open_calls) == 1


def test_a_part_inserted_from_its_file_closes_with_the_assembly(call: Caller, make: MakeSession) -> None:
    cube_part(call, "cube")
    call.ok("doc", action="close")
    adapter = FakeAdapter()
    fresh = Caller(make(adapter))
    fresh.ok("doc", action="new", name="asm", kind="assembly")
    insert(fresh, "a", "cube.fake")
    fresh.ok("doc", action="save")

    closed = fresh.ok("doc", action="close")

    assert "also closed" in closed and "cube" in closed and len(adapter.closed_ids) == 2


def test_a_file_that_does_not_exist_is_not_a_part_to_load(call: Caller) -> None:
    call.ok("doc", action="new", name="asm", kind="assembly")

    text = call.err(
        "op", name="insert_component", id="a", params={"part": "ghost.fake", "placement": {"origin": [0, 0, 0]}}
    )

    assert "'ghost.fake' is not an open part document" in text and "file of a saved part" in text
