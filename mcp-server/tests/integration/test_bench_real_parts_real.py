# ruff: noqa: E501
"""Bench parts a real shop would make, on the REAL FreeCAD (Windows, VISIBLE): a hardened die plate whose dowel holes are finished
square to ground datum A, and a block with its holes on the edges. Each part is designed through the harness's own flow and
passes its design review; its sheet is drawn the way a drafter session does it and its review is read.

What the harness gets wrong on these sheets is listed in ``bench_parts.KNOWN_DEFECTS``: any other blocking code fails the test, the
known ones stay visible there until the harness stops raising them (the test says which ones it no longer sees).

Set ``COTAFORGE_TEST_SHOTS`` to a folder to keep the images of the sheets.
"""

import base64
import os
import sys
from pathlib import Path

import pytest

from tests.integration.bench_parts import (
    KNOWN_DEFECTS,
    blocking_codes,
    build_die_plate,
    build_edge_block,
    draw_die_plate,
    draw_edge_block,
    reopen_for_drawing,
)
from tests.integration.conftest import Cad, running_server

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the visible FreeCAD window is exercised on Windows")


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The engineer's own memory never loads in a test part: the harness runs on its shipped defaults."""
    monkeypatch.setenv("COTAFORGE_HOME", str(tmp_path / "home"))


async def look(cad: Cad, name: str) -> None:
    shot = await cad("drawing", action="capture")
    assert not shot.is_error and shot.images, shot.text
    target = os.environ.get("COTAFORGE_TEST_SHOTS")
    if target:
        Path(target).mkdir(parents=True, exist_ok=True)
        (Path(target) / f"{name}.png").write_bytes(base64.b64decode(shot.images[0].data))


async def assert_only_known(cad: Cad, part: str) -> None:
    held = await blocking_codes(cad)
    unknown = held - set(KNOWN_DEFECTS[part])
    assert not unknown, f"the review of the {part} sheet holds codes that are not known defects: {sorted(unknown)}"
    gone = sorted(set(KNOWN_DEFECTS[part]) - held)
    if gone:
        print(f"{part}: known defects no longer raised (remove them from KNOWN_DEFECTS): {gone}")


@pytest.mark.expected_findings(
    "collision", "dimension_crossing", "view_adds_nothing", "paper_too_small", "text_outside_frame"
)
async def test_a_hardened_die_plate_is_designed_with_its_route_and_drawn(workspace: Path) -> None:
    async with running_server(workspace, visible=True) as (cad, _):
        await build_die_plate(cad, workspace)
        holes = await cad.ok("inspect", action="holes")
        assert "2 x D6" in holes and "4 x D6.6" in holes and "4 x D11" in holes, holes
        await cad.ok("doc", action="save")
        await reopen_for_drawing(cad, workspace, "die_plate")
        await draw_die_plate(cad)
        await look(cad, "die_plate")
        listing = await cad.ok("drawing", action="list")
        # what the sheet says about the route (the shop reads it here, nowhere else)
        assert "Ø5,7" in listing and "rectificar A (primero)" in listing, listing
        assert "perpendicularity 0,005 to A" in listing and "parallelism 0,005 to A" in listing, listing
        assert "Ø6 H7" in listing and "datum A" in listing
        await assert_only_known(cad, "die_plate")


@pytest.mark.expected_findings("collision")
async def test_a_block_with_holes_on_its_edges_is_designed_and_drawn(workspace: Path) -> None:
    async with running_server(workspace, visible=True) as (cad, _):
        await build_edge_block(cad, workspace)
        holes = await cad.ok("inspect", action="holes")
        assert "2 x D5" in holes and "2 x D6.8" in holes and "1 x D12" in holes, holes
        await cad.ok("doc", action="save")
        await reopen_for_drawing(cad, workspace, "edge_block")
        await draw_edge_block(cad)
        await look(cad, "edge_block")
        listing = await cad.ok("drawing", action="list")
        assert "datum B" in listing and "datum C" in listing and "datum A" in listing, listing
        assert "M8" in listing and "M6" in listing, listing
        await assert_only_known(cad, "edge_block")
