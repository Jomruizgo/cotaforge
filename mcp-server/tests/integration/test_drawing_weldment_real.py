# ruff: noqa: E501
"""The cut list of a welded part on the real FreeCAD (Windows, VISIBLE): a small frame of 4 hollow tubes (40x40x3) and 1 end
plate in one part. The sheet gets a parts list (one row for the 4 tubes with quantity 4, one for the plate) and a balloon per
body; the designation is the designer's and is checked against the measured body; the default views leave out the one that
would draw the same as another. The harness measures the joints (the four tubes stand on the plate: four identical tee joints);
the weld is the designer's: a question with a proposal until a weld item draws its ISO 2553 symbol on the joint.

Set ``COTAFORGE_TEST_SHOTS`` to a folder to keep the image of the sheet.
"""

import base64
import os
import sys
from pathlib import Path

import pytest

from tests.integration.conftest import Cad, pending_points, running_server

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the visible FreeCAD window is exercised on Windows")

TUBES = ((0, 0), (300, 0), (0, 300), (300, 300))  # (y, z) of each tube, 400 long along x
TUBE_CODE = (
    "import Part\n"
    + "".join(
        f"outer = Part.makeBox(400, 40, 40, Vector(0, {y}, {z}))\n"
        f"inner = Part.makeBox(402, 34, 34, Vector(-1, {y + 3}, {z + 3}))\n"
        f'doc.addObject("Part::Feature", "Tube{n}").Shape = outer.cut(inner)\n'
        for n, (y, z) in enumerate(TUBES, start=1)
    )
)
NOTE = {"type": "note", "id": "weld", "text": "Proceso MAG, nivel de calidad ISO 5817-C. Tolerancias generales ISO 13920-B"}
WELD = {
    "type": "weld",
    "id": "w1",
    "between": ["body:1", "body:5"],
    "also": [["body:2", "body:5"], ["body:3", "body:5"], ["body:4", "body:5"]],
    "kind": "fillet",
    "size_mm": 2,
    "size_kind": "throat",
    "sides": "arrow",
    "all_around": True,
}


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COTAFORGE_HOME", str(tmp_path / "home"))


async def build_frame(cad: Cad) -> None:
    await cad.ok("doc", action="new", name="frame")
    await cad.ok("doc", action="set_material", material="S235JR")
    await cad.ok("run_native", code=TUBE_CODE, intent="four hollow 40x40x3 tubes of a frame")
    await cad.ok("op", name="box", id="plate", params={"size": [6, 340, 340], "placement": {"origin": [-6, 0, 0]}})


async def test_the_welded_sheet_gets_its_cut_list_and_a_balloon_per_body(workspace: Path, tmp_path: Path) -> None:
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    shots.mkdir(parents=True, exist_ok=True)
    async with running_server(workspace, visible=True) as (cad, _):
        await build_frame(cad)
        made = await cad.ok("drawing", action="create", template="A3", angle="first", title="Bastidor")  # the default views
        print(made)
        assert "same as another" in made, made  # the top view would repeat the front one: left out by itself
        findings, _, _ = await pending_points(cad)
        print("EMPTY SHEET FINDINGS", findings)
        assert any(f.startswith("weld_list_missing") for f in findings), findings
        assert not any(f.startswith("view_redundant") for f in findings), findings
        # a list nobody named and a balloon per position
        await cad.ok("drawing", action="add", items=[{"type": "parts_list", "id": "cut"}, {"type": "balloons", "id": "bal", "view": "isometric"}])
        findings, _, _ = await pending_points(cad)
        assert any(f.startswith("weld_body_unnamed") for f in findings), findings  # it asks the designer, it does not guess
        assert any(f.startswith("weld_joint_unspecified") for f in findings), findings  # and which welds: the joints are measured
        assert not any(f.startswith("weld_not_specified") for f in findings), findings
        # a designation that does not agree with the body (the tubes are 40 x 40) is a blocking finding
        wrong = [{"component": "body:1", "name": "Tubo 50×50×3"}, {"component": "body:5", "name": "Chapa 6"}]
        await cad.ok("drawing", action="remove", items=["cut"])
        await cad.ok("drawing", action="add", items=[{"type": "parts_list", "id": "cut", "rows": wrong}])
        review = await cad.ok("drawing", action="review")
        findings, _, _ = await pending_points(cad)
        assert any(f.startswith("weld_designation_mismatch") for f in findings), review
        assert "BLOCKS EXPORT weld_designation_mismatch" in review, review
        # the designer answers: the right designation and the weld
        rows = [{"component": "body:1", "name": "Tubo 40×40×3"}, {"component": "body:5", "name": "Chapa 6"}]
        await cad.ok("drawing", action="remove", items=["cut"])
        await cad.ok("drawing", action="add", items=[{"type": "parts_list", "id": "cut", "rows": rows}, NOTE])
        review = await cad.ok("drawing", action="review")
        print("JOINT QUESTION", review)
        findings, _, _ = await pending_points(cad)
        # a general note does not close the joints: the four tee joints are ONE question, with the proposal a = 0.7 x 3 mm = 2 mm
        (joint,) = [f for f in findings if f.startswith("weld_joint_unspecified")]
        assert "4 iguales" in review and "a=2" in review and "BLOCKS EXPORT weld_joint_unspecified" in review, review
        # the designer accepts the proposal: one symbol for the four joints, the arrow on the seam
        added = await cad.ok("drawing", action="add", items=[WELD])
        print("WELD ADDED", added)
        assert "error" not in added.lower().split("w1")[-1][:80], added
        review = await cad.ok("drawing", action="review")
        findings, _, _ = await pending_points(cad)
        print("FINAL FINDINGS", findings)
        assert not any(f.startswith(("weld_", "item_without_balloon", "balloon_not_in_list", "view_redundant")) for f in findings), review
        capture = await cad("drawing", action="capture")
        assert capture.images, capture.text
        (shots / "w2_frame_weld_symbols.png").write_bytes(base64.b64decode(capture.images[0].data))
