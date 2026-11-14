# ruff: noqa: E501
"""The hybrid flow on the real FreeCAD (Windows, VISIBLE): the first proposal of a sheet is composed in one order, and every
correction afterwards is LOCAL: ``drawing(action="update", items=[...])`` changes ONE item in place and nothing else on the
sheet moves. It is measured, not looked at: the sheet is captured before and after and the pixels that changed are compared
with the pixels the edited item alone can change (what it draws at its old place and at its new one).

Needs Pillow (the measurement reads the captures); set ``COTAFORGE_TEST_SHOTS`` to a folder to keep the images.
"""

import base64
import io
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.integration.conftest import ORDER, Cad, running_server
from tests.integration.test_drawing_compose_real import compose_the_rest, in_one_order
from tests.integration.test_drawing_requirements_real import TOP, XY, make_plate

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the visible FreeCAD window is exercised on Windows")

Image = pytest.importorskip("PIL.Image")
ImageChops = pytest.importorskip("PIL.ImageChops")


async def make_block(cad: Cad) -> None:
    """A milled block 80 x 60 x 40 with two blind holes in its top face."""
    await cad.ok("doc", action="new", name="bloque")
    await cad.ok("doc", action="set_material", material="AISI 1045")
    rect = [{"kind": "rect", "name": "block", "center": [0, 0], "width": 80, "height": 60}]
    await cad.ok("op", name="sketch", id="outline", params={"on": XY, "entities": rect})
    await cad.ok("op", name="extrude", id="base", params={"sketch": "outline", "depth_mm": 40})
    for number, x in enumerate((-20, 20), start=1):
        params = {"face": TOP, "at": [x, 0, 40], "standard": "simple", "diameter_mm": 8, "end_condition": "blind", "depth_mm": 25}
        await cad.ok("op", name="hole", id=f"h{number}", params=params)


async def capture(cad: Cad, shots: Path, name: str) -> Any:
    reply = await cad("drawing", action="capture")
    assert reply.images, reply.text
    raw = base64.b64decode(reply.images[0].data)
    shots.mkdir(parents=True, exist_ok=True)
    (shots / f"{name}.png").write_bytes(raw)
    return Image.open(io.BytesIO(raw)).convert("RGB")


def changed(a: Any, b: Any) -> Any:
    """The pixels that differ between two captures, as an 8-bit mask (255 where they differ)."""
    return ImageChops.difference(a, b).convert("L").point(lambda v: 255 if v else 0)


def count(mask: Any) -> int:
    return int(mask.histogram()[255])


async def only_this_changes(cad: Cad, shots: Path, tag: str, item: dict[str, Any], patch: dict[str, Any]) -> dict[str, int]:
    """Add ``item`` to the composed sheet, edit it in place with ``patch`` and measure that nothing but it changed: the pixels
    that differ between the two states must lie inside what the item draws at its two places (the sheet without it is the
    reference). Then the edit is undone by another edit and the capture must be identical to the first one, pixel by pixel."""
    base = await capture(cad, shots, f"{tag}_0_without")
    await cad.ok("drawing", action="add", items=[item])
    before = await capture(cad, shots, f"{tag}_1_placed")
    edited = await cad.ok("drawing", action="update", items=[{"id": item["id"], **patch}])
    print("EDITED", tag, edited)
    assert "edited in place" in edited and item["id"] in edited, edited
    after = await capture(cad, shots, f"{tag}_2_moved")
    here, there = changed(base, before), changed(base, after)
    both = ImageChops.lighter(here, there)
    moved = changed(before, after)
    outside = ImageChops.subtract(moved, both)
    undo = {key: item.get(key) for key in patch}
    back = await cad.ok("drawing", action="update", items=[{"id": item["id"], **undo}])
    print("UNDONE", tag, back)
    again = await capture(cad, shots, f"{tag}_3_back")
    measured = {
        "drawn_before": count(here), "drawn_after": count(there), "changed": count(moved), "changed_outside_the_item": count(outside),
        "differs_from_the_first_after_undo": count(changed(before, again)),
    }
    print("MEASURED", tag, measured)
    assert measured["changed"] > 0, "the edit changed nothing on the sheet"
    assert measured["changed_outside_the_item"] == 0, measured
    assert measured["differs_from_the_first_after_undo"] == 0, measured
    return measured


async def hybrid(cad: Cad, shots: Path, tag: str, decisions: dict[str, Any], keep: tuple[str, ...], item: dict[str, Any], patch: dict[str, Any], **sheet: Any) -> None:
    await in_one_order(cad)
    composed = await compose_the_rest(cad, decisions, keep=keep, **sheet)
    print("COMPOSED", tag, composed)
    assert "composed sheet" in composed and "update" in composed.split("next:")[-1], composed
    await capture(cad, shots, f"{tag}_composed")
    # a second compose for a local fix is refused and changes nothing
    refused = await cad.err("drawing", action="compose", decisions={**decisions, "notes": ["otra nota"]}, **ORDER)
    print("REFUSED", tag, refused)
    assert "already composed" in refused and "nothing was changed" in refused, refused
    measured = await only_this_changes(cad, shots, tag, item, patch)
    (shots / f"{tag}_measured.txt").write_text("\n".join(f"{k}: {v}" for k, v in measured.items()), encoding="utf-8")
    # the edit refuses a place that lies on another object, says so and changes nothing
    first = await capture(cad, shots, f"{tag}_4_before_collision")
    for offset in (4, 8, 12, 13, 16, 20):
        reply = await cad("drawing", action="update", items=[{"id": item["id"], "offset_mm": offset}])
        if reply.is_error:
            print("COLLISION", tag, offset, reply.text)
            assert "nothing was changed" in reply.text, reply.text
            after = await capture(cad, shots, f"{tag}_5_after_collision")
            assert count(changed(first, after)) == 0, "a refused edit changed the sheet"
            return
        await cad.ok("drawing", action="update", items=[{"id": item["id"], **{k: item.get(k) for k in ("offset_mm",)}}])
    pytest.fail("no offset tried made the edit collide")


async def test_a_plate_is_composed_once_and_one_dimension_is_moved_alone(workspace: Path, tmp_path: Path) -> None:
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    async with running_server(workspace, visible=True) as (cad, _):
        await make_plate(cad)
        item = {"type": "dim", "id": "d_test", "kind": "distance", "along": "y", "from": {"at": [50, -30, 8]}, "to": {"at": [50, -20, 8]}, "view": "top", "side": "right", "offset_mm": 24}
        decisions = {"sheet": {"symmetry": True, "title": "Placa"}, "finish": [{"general": True, "ra": 3.2}], "notes": ["Romper aristas vivas 0,5 × 45°"]}
        await hybrid(cad, shots, "plate", decisions, ("req.surface_texture",), item, {"offset_mm": 34})


async def test_a_block_is_composed_once_and_one_dimension_is_moved_alone(workspace: Path, tmp_path: Path) -> None:
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    async with running_server(workspace, visible=True) as (cad, _):
        await make_block(cad)
        item = {"type": "dim", "id": "d_test", "kind": "distance", "along": "z", "from": {"at": [40, -30, 0]}, "to": {"at": [40, -30, 15]}, "view": "front", "side": "right", "offset_mm": 24}
        decisions = {"sheet": {"symmetry": True, "title": "Bloque"}, "finish": [{"general": True, "ra": 3.2}], "notes": ["Romper aristas vivas 0,5 × 45°"]}
        await hybrid(cad, shots, "block", decisions, ("req.surface_texture",), item, {"offset_mm": 34})
