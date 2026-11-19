# ruff: noqa: E501
"""``external_thread`` on the real FreeCAD (Windows, VISIBLE) through the stdio server: a Ø12 x 60 shaft with a 1 mm
chamfer at its end gets an M12 thread of 20 mm; the part keeps its volume, a wrong size or a face that is not a shaft end is
refused in words, and the sheet carries the thread as the callout of the diameter plus the ISO 6410 lines.

Set ``COTAFORGE_TEST_SHOTS`` to a folder to keep the image of the sheet.
"""

import base64
import os
import re
import sys
from pathlib import Path

import pytest

from tests.integration.conftest import Cad, running_server

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the visible FreeCAD window is exercised on Windows")

XY = {"kind": "plane", "name": "XY"}
LENGTH = 60
END = {"kind": "face", "from_op": "end_break", "select": {"normal": [0, 0, 1]}}
SHAFT = {"kind": "face", "from_op": "end_break", "select": {"surface": "cylinder"}}
TOP_EDGE = {
    "kind": "edge",
    "between": [
        {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, 1]}},
        {"kind": "face", "from_op": "base", "select": {"surface": "cylinder"}},
    ],
}


def volume(text: str) -> float:
    match = re.search(r"volume: (-?[\d.]+) mm3", text)
    assert match, text
    return float(match.group(1))


async def make_shaft(cad: Cad) -> None:
    await cad.ok("doc", action="new", name="eje_m12")
    await cad.ok("doc", action="set_material", material="AISI 1045")
    circle = [{"kind": "circle", "name": "od", "center": [0, 0], "diameter": 12}]
    await cad.ok("op", name="sketch", id="outline", params={"on": XY, "entities": circle})
    await cad.ok("op", name="extrude", id="base", params={"sketch": "outline", "depth_mm": LENGTH})
    await cad.ok("op", name="chamfer", id="end_break", params={"edges": [TOP_EDGE], "distance1_mm": 1})


async def test_a_shaft_end_gets_an_m12_thread_that_the_sheet_calls_out(workspace: Path, tmp_path: Path) -> None:
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    shots.mkdir(parents=True, exist_ok=True)
    async with running_server(workspace, visible=True) as (cad, _):
        assert "external_thread" in await cad.ok("cad_guide", topic="ops")
        await make_shaft(cad)
        before = volume(await cad.ok("inspect", action="properties"))

        wrong = await cad.err(
            "op",
            name="external_thread",
            id="bad_size",
            params={"face": SHAFT, "size": "M16", "length_mm": 20, "end": END},
        )
        assert "major diameter of 16" in wrong and "Ø12" in wrong, wrong
        long = await cad.err(
            "op",
            name="external_thread",
            id="too_long",
            params={"face": SHAFT, "size": "M12", "length_mm": 70, "end": END},
        )
        assert "longer than the shaft" in long, long
        side = {"kind": "face", "from_op": "end_break", "select": {"surface": "cylinder"}}
        flat = await cad.err(
            "op",
            name="external_thread",
            id="not_flat",
            params={"face": SHAFT, "size": "M12", "length_mm": 20, "end": side},
        )
        assert "not a flat face" in flat, flat

        done = await cad.ok(
            "op",
            name="external_thread",
            id="end_thread",
            params={"face": SHAFT, "size": "M12", "length_mm": 20, "end": END},
        )
        assert "cosmetic thread M12 x 20 mm" in done, done
        assert volume(await cad.ok("inspect", action="properties")) == pytest.approx(before, rel=1e-9)  # no geometry
        assert "thread_end_thread" in await cad.ok("inspect", action="tree")

        faces = await cad.ok("inspect", action="faces", surface="cylinder")
        cylinder = re.search(r"face:\d+", faces)
        assert cylinder, faces
        await cad.ok("drawing", action="create", template="A3", scale="2:1", title="Eje M12", views=["front", "top"])
        added = await cad.ok(
            "drawing",
            action="add",
            items=[
                {"type": "view_lines"},
                {"type": "dim", "id": "thr", "kind": "diameter", "face": cylinder.group(0), "view": "front"},
            ],
        )
        assert re.search(r"\bthr\b.*\b(ok|warning)\b", added) and "thread M12" in added, added
        shot = await cad("drawing", action="capture")
        assert not shot.is_error, shot.text
        (shots / "t1_external_thread.png").write_bytes(base64.b64decode(shot.images[0].data))
        (shots / "t1_external_thread.txt").write_text(f"{done}\n\n{added}\n", encoding="utf-8")
        await cad.ok("doc", action="save", path="eje_m12.FCStd")
        with pytest.raises(AssertionError, match="longer than the shaft"):  # a refused op left no thread behind
            await cad.ok(
                "op",
                name="external_thread",
                id="again",
                params={"face": SHAFT, "size": "M12", "length_mm": 70, "end": END},
            )

    # the file the engineer reopens in another session still says which end is threaded
    async with running_server(workspace, visible=True) as (cad, _):
        await cad.ok("doc", action="open", path="eje_m12.FCStd")
        faces = await cad.ok("inspect", action="faces", surface="cylinder")
        cylinder = re.search(r"face:\d+", faces)
        assert cylinder, faces
        await cad.ok("drawing", action="create", template="A3", scale="2:1", title="Eje M12", views=["front", "top"])
        again = await cad.ok(
            "drawing",
            action="add",
            items=[{"type": "dim", "id": "thr2", "kind": "diameter", "face": cylinder.group(0), "view": "front"}],
        )
        assert "thread M12" in again, again
