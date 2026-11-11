"""Parts an assembly links to need to be LOADED, not shown: ``open_document`` with ``hidden``, the links that
resolve, the assembly that saves relative names and reopens, and a document the engineer already had open (never
closed here).

Plain asserts inside ``freecadcmd`` (see ``run.py``): there is no window to leave out headless, so the window counts are
the integration test ``test_windowless_parts_visible.py`` (the GUI worker); this proves the rest on the same code path.
"""

import os
import zipfile
from typing import Any

import FreeCAD as App
import main
from check_save_document import STAMP, workdir
from harness import call, new_document, op

SIZES = {"a": (10, 10, 10), "b": (20, 10, 10), "c": (30, 10, 10)}


def _save(path: str) -> None:
    call("save_document", op("s", path=path, stamp=STAMP))


def _make_parts(directory: str) -> dict[str, str]:
    """Three saved parts, each closed again: only their files remain."""
    files: dict[str, str] = {}
    for name, size in SIZES.items():
        doc = new_document(name)
        call("box", op(f"{name}_box", size=list(size)))
        files[name] = os.path.join(directory, f"{name}.FCStd")
        _save(files[name])
        _close(doc["handle"]["id"])
    return files


def _close(doc_id: str) -> None:
    call("set_active_document", op("act", document={"id": doc_id}, window=False))
    call("close_document", op("close"))


def _open_hidden(path: str) -> dict[str, Any]:
    return call("open_document", op("open", path=path, hidden=True))


def _loaded(path: str) -> bool:
    return any(os.path.normcase(str(d.FileName)) == os.path.normcase(path) for d in App.listDocuments().values())


def _volume() -> float:
    return float(call("mass_properties", op("m"))["volume_mm3"])


def check_parts_opened_hidden_are_inserted_linked_saved_and_reopened() -> None:
    with workdir() as directory:
        files = _make_parts(directory)
        assert not any(_loaded(p) for p in files.values())
        opened = {name: _open_hidden(path) for name, path in files.items()}
        assert all(r["borrowed"] is False for r in opened.values())  # nobody had them open
        assert all(r["windowless"] is False for r in opened.values())  # headless: there is no window to leave out
        asm_path = os.path.join(directory, "asm.FCStd")
        new_document("asm", kind="assembly")
        _save(asm_path)
        for i, (name, result) in enumerate(opened.items()):
            inserted = call(
                "insert_component",
                op(f"ins_{name}", part=result["document"]["handle"], transform={"origin": [i * 40, 0, 0]}),
            )
            assert not [w for w in inserted["warnings"] if "window" in w], inserted["warnings"]
        expected = sum(x * y * z for x, y, z in SIZES.values())
        assert abs(_volume() - expected) < 1e-6
        fc_asm = main.WORKER.active.fc_doc
        links = [o for o in fc_asm.Objects if o.TypeId == "App::Link"]
        assert len(links) == 3 and all(o.LinkedObject is not None for o in links)  # the links resolve
        assert {os.path.basename(o.LinkedObject.Document.FileName) for o in links} == {"a.FCStd", "b.FCStd", "c.FCStd"}
        saved = call("save_document", op("s", path=asm_path, stamp=STAMP))
        state = saved["adapter_state"]
        assert sorted(c["part_file"] for c in state["components"]) == ["a.FCStd", "b.FCStd", "c.FCStd"]  # relative
        xml = zipfile.ZipFile(asm_path).read("Document.xml").decode()
        assert all(f'file="{n}.FCStd"' in xml for n in SIZES)

        # a new server: parts first (hidden), then the assembly with its recorded state
        for rec in list(main.WORKER.documents.values()):
            _close(rec.id)
        assert not any(_loaded(p) for p in [*files.values(), asm_path])
        for path in files.values():
            _open_hidden(path)
        reopened = call("open_document", op("open", path=asm_path, adapter_state=state))
        assert reopened["document"]["kind"] == "assembly" and abs(_volume() - expected) < 1e-6
        again = [o for o in main.WORKER.active.fc_doc.Objects if o.TypeId == "App::Link"]
        assert len(again) == 3 and all(o.LinkedObject is not None for o in again)


def check_a_file_the_engineer_has_open_is_borrowed_and_closing_it_leaves_it_open() -> None:
    with workdir() as directory:
        files = _make_parts(directory)
        path = files["a"]
        by_hand = App.openDocument(path)  # the engineer's File > Open: not through the worker
        try:
            result = _open_hidden(path)
            assert result["borrowed"] is True and result["windowless"] is False
            assert main.WORKER.documents[result["document"]["handle"]["id"]].borrowed is True
            call("close_document", op("close"))  # the harness lets go of it
            assert by_hand.Name in App.listDocuments(), "the engineer's document was closed"
            assert result["document"]["handle"]["id"] not in main.WORKER.documents
        finally:
            App.closeDocument(by_hand.Name)


def check_a_document_the_harness_opened_is_closed_with_close_document() -> None:
    with workdir() as directory:
        files = _make_parts(directory)
        result = _open_hidden(files["b"])
        assert result["borrowed"] is False and _loaded(files["b"])
        call("close_document", op("close"))
        assert not _loaded(files["b"])


def check_an_active_document_asked_without_a_window_is_still_the_active_one() -> None:
    with workdir() as directory:
        files = _make_parts(directory)
        first = _open_hidden(files["a"])
        second = _open_hidden(files["b"])
        info = call("set_active_document", op("act", document=first["document"]["handle"], window=False))
        assert info["handle"] == first["document"]["handle"] and main.WORKER.active.name == "a"
        assert second["document"]["handle"]["id"] in main.WORKER.documents
