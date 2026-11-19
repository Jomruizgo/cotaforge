# ruff: noqa: E501
"""The shaft sheet the owner watched, on the real FreeCAD (Windows, VISIBLE): a turned part goes on the sheet with its axis
horizontal, as it is machined, in ONE main view that carries the diameter, the length, the thread and the chamfer; the
harness adds no end view that would only repeat circles, and chooses the paper and the scale that fill the sheet.

Set ``COTAFORGE_TEST_SHOTS`` to a folder to keep the image of the sheet.
"""

import base64
import os
import re
import sys
from pathlib import Path

import pytest

from tests.integration.conftest import Reply, pending_points, running_server
from tests.integration.test_external_thread_real import END, SHAFT, make_shaft

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the visible FreeCAD window is exercised on Windows")


def shot(shots: Path, name: str, reply: Reply) -> None:
    assert reply.images, reply.text
    (shots / f"{name}.png").write_bytes(base64.b64decode(reply.images[0].data))


async def test_the_turned_shaft_is_one_horizontal_view_with_everything_on_it(workspace: Path, tmp_path: Path) -> None:
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    shots.mkdir(parents=True, exist_ok=True)
    async with running_server(workspace, visible=True) as (cad, _):
        await make_shaft(cad)
        await cad.ok("op", name="external_thread", id="end_thread", params={"face": SHAFT, "size": "M12", "length_mm": 20, "end": END})
        faces = await cad.ok("inspect", action="faces", surface="cylinder")
        cylinder = re.search(r"face:\d+", faces)
        assert cylinder, faces
        created = await cad.ok("drawing", action="create", title="Eje M12")  # nobody chose views, paper or scale
        print("CREATED", created)
        assert "views: front, iso" in created, created  # the main view and the isometric: no end view that repeats circles
        assert "axis horizontal" in created and "paper chosen from the part: A4" in created, created
        item = {"type": "dim", "id": "thr", "kind": "diameter", "face": cylinder.group(0), "view": "front"}
        assert "thread M12" in await cad.ok("drawing", action="add", items=[item])
        review = await cad.ok("drawing", action="review")
        findings, _, _ = await pending_points(cad)
        print("FINDINGS", findings)
        for code in ("view_adds_nothing", "sheet_underused", "no_isometric", "callouts_crowded", "chamfer_no_dimension", "collision"):
            assert not any(f.startswith(code) for f in findings), review
        shot(shots, "rv2_shaft", await cad("drawing", action="capture"))


async def test_a_sheet_made_without_the_isometric_view_is_flagged(workspace: Path, tmp_path: Path) -> None:
    async with running_server(workspace, visible=True) as (cad, _):
        await make_shaft(cad)
        await cad.ok("drawing", action="create", template="A3", scale="2:1", title="Eje M12", views=["front"])
        findings, _, _ = await pending_points(cad)
        assert any(f.startswith("no_isometric") for f in findings), findings
        assert any(f.startswith("sheet_underused") for f in findings), findings  # the shaft in a corner of an A3 at 2:1
        refused = await cad.err("export", action="pdf", path="rv2_shaft.pdf")
        assert "no_isometric" in refused and "sheet_underused" in refused, refused
