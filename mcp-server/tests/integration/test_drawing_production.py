"""A production drawing end to end through the real server on the real FreeCAD: a plate with four holes (one tapped)
and a slot, drawn A3 at 1:1 with hole callouts, datum dimensions, dimensions between features, a note, a revision and
the Spanish title block; looked at, written as PDF and DXF, and the DXF read back with a real DXF reader.

Set ``COTAFORGE_ACCEPT_DIR`` to keep the files (the acceptance run does, outside any repository)."""

import base64
import os
import re
import shutil
from pathlib import Path

import pytest

from tests.integration.conftest import Cad, finish_drawing, running_server

XY = {"kind": "plane", "name": "XY"}
TOP = {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, 1]}}
THICKNESS = 16
HOLES = {"a": (-38, 22), "b": (38, 22), "c": (-38, -22), "d": (38, -22)}


async def make_plate(cad: Cad) -> None:
    """100 x 60 x 16 plate, an 8 mm wide 40 mm long slot across the middle, two D8 through holes, a tapped M6 blind hole
    (thread 10 of 12) and a D6 blind one."""
    await cad.ok("doc", action="new", name="placa")
    entities = [{"kind": "rect", "name": "plate", "center": [0, 0], "width": 100, "height": 60}]
    await cad.ok("op", name="sketch", id="outline", params={"on": XY, "entities": entities})
    await cad.ok("op", name="extrude", id="base", params={"sketch": "outline", "depth_mm": THICKNESS})
    slot = {"kind": "slot", "name": "slot", "start": [-20, 0, THICKNESS], "end": [20, 0, THICKNESS], "width": 8}
    await cad.ok("op", name="sketch", id="slot_sk", params={"on": TOP, "frame": "world", "entities": [slot]})
    await cad.ok("op", name="cut", id="slot", params={"sketch": "slot_sk", "end_condition": "through_all"})
    face = {"face": TOP, "standard": "simple"}
    for name in ("a", "b"):
        plain = {**face, "at": [*HOLES[name], THICKNESS], "diameter_mm": 8, "end_condition": "through_all"}
        await cad.ok("op", name="hole", id=f"hole_{name}", params=plain)
    tapped = {
        "face": TOP,
        "at": [*HOLES["c"], THICKNESS],
        "standard": "iso_tapped",
        "size": "M6",
        "end_condition": "blind",
        "depth_mm": 12,
        "thread_depth_mm": 10,
    }
    await cad.ok("op", name="hole", id="hole_c", params=tapped)
    blind = {**face, "at": [*HOLES["d"], THICKNESS], "diameter_mm": 6, "end_condition": "blind", "depth_mm": 6}
    await cad.ok("op", name="hole", id="hole_d", params=blind)


def slot_ends(holes_text: str) -> list[str]:
    """The faces of the two round ends of the slot, left to right: ``inspect(action=holes)`` lists them with an arc
    (they are half circles, not holes)."""
    found = re.findall(r"arc \d+deg .*?axis from \((-?[\d.]+),.*?faces (face:\d+)", holes_text)
    return [face for _, face in sorted(found, key=lambda pair: float(pair[0]))]


def keep(workspace: Path, *names: str) -> None:
    target = os.environ.get("COTAFORGE_ACCEPT_DIR")
    if target:
        Path(target).mkdir(parents=True, exist_ok=True)
        for name in names:
            shutil.copy2(workspace / name, Path(target) / name)


async def test_a_plate_drawing_with_callouts_datum_dimensions_and_the_spanish_title_block(workspace: Path) -> None:
    async with running_server(workspace) as (cad, init):
        assert init.instructions is not None
        await make_plate(cad)
        ends = slot_ends(await cad.ok("inspect", action="holes"))
        assert len(ends) == 2, ends
        created = await cad.ok(
            "drawing",
            action="create",
            template="A3",
            scale="1:1",
            title="Placa de anclaje",
            author="JR",
            material="Acero 1045",
            drawing_number="PA-001",
            revision="A",
            company="Taller de prueba",
        )
        assert "A3 sheet at 1:1, es" in created and "width 100 mm, height 16 mm, depth 60 mm" in created

        added = await cad.ok(
            "drawing",
            action="add",
            items=[
                {"type": "hole_dim", "all": True},
                {"type": "datum_dims", "x_from": "min", "y_from": "min"},
                {
                    "type": "dim",
                    "kind": "distance",
                    "from": {"at": [*HOLES["a"], THICKNESS]},
                    "to": {"at": [*HOLES["b"], THICKNESS]},
                },
                {
                    "type": "dim",
                    "kind": "distance",
                    "from": {"at": [*HOLES["a"], THICKNESS]},
                    "to": {"at": [*HOLES["c"], THICKNESS]},
                },
                {"type": "dim", "kind": "radius", "face": ends[0]},
                {"type": "dim", "kind": "distance", "from": {"face": ends[0]}, "to": {"face": ends[1]}},
                {"type": "note", "text": "Romper aristas vivas 0,3 mm. Dimensiones en mm."},
                {"type": "revision", "index": "A", "description": "Emisión inicial", "date": "2026-10-25", "by": "JR"},
            ],
        )
        assert "refused" not in added and " error" not in added, added
        assert "hole_dim ok" in added  # every hole was made by a hole operation: nothing is "rosca no declarada"
        assert "2× Ø8 pasante" in added and "M6 ↧ 10" in added and "Ø6 ↧ 6" in added
        assert "76 mm along x" in added and "44 mm along y" in added
        assert "R4 in the top view" in added and "40 mm along x" in added  # the slot: its end radius and its length

        shot = await cad("drawing", action="capture")
        assert not shot.is_error and base64.b64decode(shot.images[0].data)[:8] == b"\x89PNG\r\n\x1a\n"
        (workspace / "placa.png").write_bytes(base64.b64decode(shot.images[0].data))
        await finish_drawing(cad)
        await cad.ok("export", action="pdf", path="placa.pdf")
        await cad.ok("export", action="dxf", path="placa.dxf")
        assert (workspace / "placa.pdf").read_bytes().startswith(b"%PDF")
        keep(workspace, "placa.png", "placa.pdf", "placa.dxf")

        data = (workspace / "placa.dxf").read_bytes()
        assert data.decode("cp1252") and b"%%c8 pasante" in data and "Título:".encode("cp1252") in data

    ezdxf = pytest.importorskip("ezdxf")
    doc = ezdxf.readfile(str(workspace / "placa.dxf"))
    audit = doc.audit()
    assert not audit.has_errors and not audit.has_fixes, [e.message for e in audit.errors]
    texts = {e.plain_text() for e in doc.modelspace().query("TEXT")}
    assert {
        "Placa de anclaje",
        "PA-001",
        "Taller de prueba",
        "ISO 2768-mK",
        "1. Romper aristas vivas 0,3 mm. Dimensiones en mm.",
    } <= texts
    assert "2× Ø8 pasante" in texts and "M6 ↧ 10" in texts


async def test_the_items_follow_an_edit_and_survive_save_close_and_open(workspace: Path) -> None:
    async with running_server(workspace) as (cad, _):
        await make_plate(cad)
        await cad.ok("drawing", action="create", template="A3", scale="1:1")
        await cad.ok("drawing", action="add", items=[{"type": "hole_dim", "all": True}, {"type": "datum_dims"}])
        # the part changes (a hole gets bigger): the sheet is drawn from the part as it is now
        await cad.ok("op", edit="hole_a", params={"diameter_mm": 9})
        again = await cad.ok("drawing", action="update")
        assert "Ø9 pasante" in again and "Ø8 pasante" in again and "hole_dim ok" in again
        await cad.ok("doc", action="save", path="placa.FCStd")
        await cad.ok("doc", action="close")
        await cad.ok("doc", action="open", path="placa.FCStd")
        listed = await cad.ok("drawing", action="list")
        assert "hole_dim" in listed and "datum_dims" in listed and "sheet Drawing: A3 at 1:1, es" in listed
        await finish_drawing(cad)
        await cad.ok("export", action="pdf", path="reopened.pdf")
        assert (workspace / "reopened.pdf").read_bytes().startswith(b"%PDF")


async def test_two_sheets_are_two_independent_drawings_each_in_its_own_file(workspace: Path) -> None:
    async with running_server(workspace) as (cad, _):
        await make_plate(cad)
        await cad.ok("drawing", action="create", template="A3", scale="1:1")
        await cad.ok("drawing", action="create", template="A4", sheet="Detalle", scale="2:1")
        await cad.ok("drawing", action="add", sheet="Detalle", items=[{"type": "hole_dim", "all": True}])
        await finish_drawing(cad, "Drawing")
        await finish_drawing(cad, "Detalle")
        refused = await cad.err("export", action="dxf", path="dos.dxf")  # one drawing, one sheet, one file
        assert "2 sheets (Drawing, Detalle)" in refused and not (workspace / "dos.dxf").exists()
        text = await cad.ok("export", action="dxf", path="dos.dxf", sheet="Drawing")
        assert "files" not in text and (workspace / "dos.dxf").is_file()
        await cad.ok("export", action="dxf", path="detalle.dxf", sheet="Detalle")
        assert (workspace / "detalle.dxf").is_file() and not (workspace / "dos-2.dxf").exists()
        await cad.ok("export", action="pdf", path="dos.pdf", sheet="Drawing")
        assert (workspace / "dos.pdf").read_bytes().startswith(b"%PDF")
