# ruff: noqa: E501
"""The functional dimension the intent file declares, on the real FreeCAD (Windows, VISIBLE): two plates held apart by two
spacer sleeves, and an intent file that says the gap between the plates is critical (18 mm, the spacer length). The sheet
must not go out without that distance given directly; once it is, the row is met.

Set ``COTAFORGE_TEST_SHOTS`` to a folder to keep the image of the sheet.
"""

import base64
import os
import sys
from pathlib import Path

import pytest

from tests.integration.conftest import running_server
from tests.integration.test_drawing_review_gaps_real import build_assembly

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the visible FreeCAD window is exercised on Windows")

INTENT = """# clamp-assembly

## Function
Two plates held apart by two spacer sleeves.

## Functional relations
- Gap between the base plate and the top plate: 18 mm, critical (the length of the spacers)
"""


async def test_the_gap_between_the_plates_must_be_on_the_sheet_directly(workspace: Path, tmp_path: Path) -> None:
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    shots.mkdir(parents=True, exist_ok=True)
    (workspace / "drawings").mkdir(exist_ok=True)
    (workspace / "drawings" / "clamp-assembly-intent.md").write_text(INTENT, encoding="utf-8")
    async with running_server(workspace, visible=True) as (cad, _):
        await build_assembly(cad)
        made = await cad.ok("drawing", action="create", template="A3", angle="first", views=["front", "top", "iso"], title="Conjunto")
        text = await cad.ok("drawing", action="requirements")
        print("REQUIREMENTS", text)
        line = next(ln for ln in text.splitlines() if "req.functional_dimensions" in ln)
        assert "[MISSING]" in line, text  # the sheet has no 18 mm distance yet
        assert "Gap between the base plate and the top plate" in text and "18 mm" in text, text
        assert "req.functional_dimensions" in await cad.err("export", action="pdf", path="f1_gap.pdf"), made
        gap = {"type": "dim", "kind": "distance", "id": "gap", "from": {"at": [0, 0, 10]}, "to": {"at": [0, 0, 28]}, "along": "z", "view": "front"}
        await cad.ok("drawing", action="add", items=[gap])
        after = await cad.ok("drawing", action="requirements")
        print("AFTER", after)
        met = next(ln for ln in after.splitlines() if "req.functional_dimensions" in ln)
        assert "[met]" in met, after  # the distance is given directly between the two plates
        shot = await cad("drawing", action="capture")
        assert shot.images, shot.text
        (shots / "f1_gap.png").write_bytes(base64.b64decode(shot.images[0].data))
