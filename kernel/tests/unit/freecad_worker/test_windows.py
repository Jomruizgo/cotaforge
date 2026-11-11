"""Which documents lose their window (``worker/windows.py``): a part that an assembly uses, saved, clean, not being
edited and not the engineer's own. The module imports only ``gui``, which is stubbed here (no FreeCAD)."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

WINDOWS = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker" / "windows.py"


class StubGui:
    """What ``gui`` answers: which documents are modified, and the windows asked to close or to open."""

    def __init__(self) -> None:
        self.modified: set[str] = set()
        self.hidden: list[str] = []
        self.shown: list[str] = []
        self.cannot_hide: set[str] = set()
        self.ACTIVE = True

    def is_modified(self, fc_doc: Any) -> bool:
        return fc_doc.Name in self.modified

    def hide_window(self, fc_doc: Any) -> bool:
        if fc_doc.Name in self.cannot_hide:
            return False
        self.hidden.append(fc_doc.Name)
        return True

    def show_window(self, fc_doc: Any) -> bool:
        self.shown.append(fc_doc.Name)
        return True


@pytest.fixture
def gui() -> StubGui:
    return StubGui()


@pytest.fixture
def windows(gui: StubGui) -> ModuleType:
    saved = sys.modules.get("gui")
    stub = ModuleType("gui")
    stub.ACTIVE = True
    for name in ("is_modified", "hide_window", "show_window"):
        setattr(stub, name, getattr(gui, name))
    sys.modules["gui"] = stub
    try:
        spec = importlib.util.spec_from_file_location("freecad_worker_windows_under_test", WINDOWS)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module
    finally:
        if saved is None:
            sys.modules.pop("gui", None)
        else:
            sys.modules["gui"] = saved


def part(name: str, *, saved: bool = True, borrowed: bool = False, windowless: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        id=f"doc:{name}",
        kind="part",
        name=name,
        fc_doc=SimpleNamespace(Name=name, FileName=f"/p/{name}.FCStd" if saved else ""),
        borrowed=borrowed,
        windowless=windowless,
        components={},
    )


def assembly(*used: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(
        id="doc:asm",
        kind="assembly",
        name="asm",
        fc_doc=SimpleNamespace(Name="asm", FileName="/p/asm.FCStd"),
        borrowed=False,
        windowless=False,
        components={f"comp:{i}": SimpleNamespace(part_doc=p.id) for i, p in enumerate(used, start=1)},
    )


def world(*docs: SimpleNamespace) -> dict[str, SimpleNamespace]:
    return {d.id: d for d in docs}


def test_the_parts_an_assembly_uses_lose_their_window_and_it_keeps_its_own(windows: ModuleType, gui: StubGui) -> None:
    a, b, c = part("a"), part("b"), part("c")
    asm = assembly(a, b, c)

    released = windows.release(world(a, b, c, asm), asm)

    assert released == ["a", "b", "c"] and gui.hidden == ["a", "b", "c"]  # three parts, one window left: the assembly's
    assert all(p.windowless for p in (a, b, c)) and not asm.windowless


def test_a_part_nothing_uses_keeps_its_window(windows: ModuleType, gui: StubGui) -> None:
    used, loose = part("used"), part("loose")
    asm = assembly(used)

    assert windows.release(world(used, loose, asm), asm) == ["used"]
    assert not loose.windowless and "loose" not in gui.hidden  # closing the last window of it would close the document


@pytest.mark.parametrize(
    ("what", "make"),
    [
        ("the engineer opened it by hand", lambda: part("p", borrowed=True)),
        ("it was never saved", lambda: part("p", saved=False)),
        ("it has no window already", lambda: part("p", windowless=True)),
    ],
)
def test_a_part_that_is_not_ours_to_close_keeps_its_window(
    windows: ModuleType, gui: StubGui, what: str, make: Any
) -> None:
    p = make()
    asm = assembly(p)

    assert windows.release(world(p, asm), asm) == [], what
    assert gui.hidden == []


def test_a_part_with_unsaved_changes_keeps_its_window(windows: ModuleType, gui: StubGui) -> None:
    p = part("p")
    asm = assembly(p)
    gui.modified.add("p")  # closing it would ask the engineer whether to save

    assert windows.release(world(p, asm), asm) == [] and not p.windowless


def test_the_active_part_is_never_released(windows: ModuleType, gui: StubGui) -> None:
    p = part("p")
    asm = assembly(p)

    assert windows.release(world(p, asm), p) == []  # being edited from the assembly: switch gave it the window


def test_a_window_the_cad_refused_to_close_is_not_recorded_as_closed(windows: ModuleType, gui: StubGui) -> None:
    p = part("p")
    asm = assembly(p)
    gui.cannot_hide.add("p")

    assert windows.release(world(p, asm), asm) == [] and not p.windowless


def test_show_gives_a_window_back_only_to_a_document_that_has_none(windows: ModuleType, gui: StubGui) -> None:
    shown, plain = part("shown"), part("plain", windowless=False)
    gone = part("gone", windowless=True)

    assert windows.show(gone) is True and not gone.windowless and gui.shown == ["gone"]
    assert windows.show(plain) is False and windows.show(shown) is False
    assert gui.shown == ["gone"]
