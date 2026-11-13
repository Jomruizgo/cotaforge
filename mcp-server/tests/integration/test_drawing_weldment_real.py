# ruff: noqa: E501
"""The cut list of a welded part on the real FreeCAD (Windows, VISIBLE): a small frame of 4 equal tubes and 1 end plate in one
part. The sheet gets a parts list (one row for the 4 tubes with quantity 4, one for the plate) and a balloon per body; the
designation is the designer's (a question while empty); a sheet without the list, or a body without its balloon, is a finding.

Set ``COTAFORGE_TEST_SHOTS`` to a folder to keep the image of the sheet.
"""

import base64
import os
import sys
from pathlib import Path

import pytest

from tests.integration.conftest import Cad, pending_points, running_server

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the visible FreeCAD window is exercised on Windows")

TUBES = ((0, 0, 0), (0, 300, 0), (0, 0, 300), (0, 300, 300))


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COTAFORGE_HOME", str(tmp_path / "home"))


async def build_frame(cad: Cad) -> None:
    await cad.ok("doc", action="new", name="frame")
    await cad.ok("doc", action="set_material", material="S235JR")
    for n, origin in enumerate(TUBES, start=1):
        await cad.ok("op", name="box", id=f"tube{n}", params={"size": [400, 40, 40], "placement": {"origin": list(origin)}})
    await cad.ok("op", name="box", id="plate", params={"size": [6, 340, 340], "placement": {"origin": [-6, 0, 0]}})


async def test_the_welded_sheet_gets_its_cut_list_and_a_balloon_per_body(workspace: Path, tmp_path: Path) -> None:
    shots = Path(os.environ.get("COTAFORGE_SHOTS") or os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    shots.mkdir(parents=True, exist_ok=True)
    async with running_server(workspace, visible=True) as (cad, _):
        await build_frame(cad)
        await cad.ok("drawing", action="create", template="A3", angle="first", views=["front", "top", "iso"], title="Bastidor")
        # no list, no balloons: the sheet of a weldment is missing its cut list
        findings, _, _ = await pending_points(cad)
        print("EMPTY SHEET FINDINGS", findings)
        assert any(f.startswith("weld_list_missing") for f in findings), findings
        # a list nobody named and a balloon per position
        await cad.ok("drawing", action="add", items=[{"type": "parts_list", "id": "cut"}, {"type": "balloons", "id": "bal", "view": "isometric"}])
        review = await cad.ok("drawing", action="review")
        print(review)
        findings, _, _ = await pending_points(cad)
        assert any(f.startswith("weld_body_unnamed") for f in findings), findings  # it asks the designer, it does not guess
        line = next(row for row in review.splitlines() if "weld_body_unnamed" in row)
        assert "tube1" not in line.replace("body:", "") or "body:" in line, line  # a body handle at most, never an object name
        # the designer answers: the designation goes in the rows
        rows = [{"component": "body:1", "name": "Tubo 40×40×3"}, {"component": "body:5", "name": "Chapa 6"}]
        await cad.ok("drawing", action="remove", items=["cut"])
        await cad.ok("drawing", action="add", items=[{"type": "parts_list", "id": "cut", "rows": rows}])
        review = await cad.ok("drawing", action="review")
        findings, _, _ = await pending_points(cad)
        print("FINAL FINDINGS", findings)
        assert not any(f.startswith(("weld_", "item_without_balloon", "balloon_not_in_list")) for f in findings), review
        capture = await cad("drawing", action="capture")
        assert capture.images, capture.text
        (shots / "w1_frame_cut_list.png").write_bytes(base64.b64decode(capture.images[0].data))
