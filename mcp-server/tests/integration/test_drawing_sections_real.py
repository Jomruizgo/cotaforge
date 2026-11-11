# ruff: noqa: E501
"""A section view as a first-class view of the drawing, end to end: the real server over stdio against the real FreeCAD
(Windows), visible and headless.

A synthetic sleeve (a round bar D60 x 50 with a through bore M45x1.5 made by the hole operation and two flats) gets a
section A-A through the axis of the bore. The section carries its cutting line, its label, the hatching, the centre line of
the bore and the ISO 6410 thread lines; the thread callout, a length, a note and an ISO 128 chain line are items on it
(``view="A-A"``) like on any other view; the PDF is checked after it is written, with ink in the section; a callout text that
states a number the measured hole does not have is said to contradict the model, by the item and by the review.

Set ``COTAFORGE_TEST_SHOTS`` to a folder to keep the images and the PDFs.
"""

import base64
import os
import re
import sys
from pathlib import Path

import pytest

from cotaforge_mcp import pdf_check
from tests.integration.conftest import Cad, finish_drawing, running_server

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the real FreeCAD is exercised on Windows")

XY = {"kind": "plane", "name": "XY"}
TOP = {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, 1]}}
LENGTH = 50


async def make_sleeve(cad: Cad) -> str:
    """The sleeve; returns the id of its outer cylinder."""
    await cad.ok("doc", action="new", name="manguito")
    circle = [{"kind": "circle", "name": "od", "center": [0, 0], "diameter": 60}]
    await cad.ok("op", name="sketch", id="outline", params={"on": XY, "entities": circle})
    await cad.ok("op", name="extrude", id="base", params={"sketch": "outline", "depth_mm": LENGTH})
    bore = {
        "face": TOP,
        "at": [0, 0, LENGTH],
        "standard": "iso_tapped",
        "size": "M45x1.5",
        "end_condition": "through_all",
    }
    await cad.ok("op", name="hole", id="bore", params=bore)
    for number, x in enumerate((28.5, -28.5), start=1):
        rect = [{"kind": "rect", "name": "flat", "center": [x, 0], "width": 5, "height": 70}]
        await cad.ok("op", name="sketch", id=f"fs{number}", params={"on": TOP, "entities": rect})
        await cad.ok("op", name="cut", id=f"flat{number}", params={"sketch": f"fs{number}", "depth_mm": 30})
    faces = await cad.ok("inspect", action="faces", op="base", surface="cylinder")
    found = re.search(r"face:\d+", faces)
    assert found, faces
    return found.group(0)


def keep(folder: Path, name: str, reply_images: list) -> None:  # type: ignore[type-arg]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(base64.b64decode(reply_images[0].data))


async def drawing_with_a_section(cad: Cad, cylinder: str) -> None:
    await cad.ok("drawing", action="create", template="A3", scale="1:1", title="Manguito", views=["front", "top"])
    await cad.ok("drawing", action="add", items=[{"type": "view_lines"}, {"type": "hole_dim", "all": True}])
    sec = await cad.ok("drawing", action="add", items=[{"type": "section", "id": "sec", "holes": "all", "view": "top"}])
    assert "A-A in the top view" in sec and "centre line" in sec and "thread lines of 1 threaded bore" in sec, sec
    items = [
        {"type": "hole_dim", "id": "thr", "all": True, "view": "A-A", "text": "M45x1,5 - 6H"},
        {
            "type": "dim",
            "id": "len",
            "kind": "distance",
            "from": {"at": [25, 0, 0]},
            "to": {"at": [25, 0, LENGTH]},
            "view": "A-A",
            "along": "z",
        },
        {"type": "dim", "id": "od", "kind": "diameter", "face": cylinder, "view": "A-A", "offset_mm": 14},
        {"type": "note", "id": "nt", "text": "Rosca M45x1,5 en toda la longitud", "view": "A-A"},
        {"type": "line", "id": "ax", "axis": cylinder, "view": "front"},
    ]
    added = await cad.ok("drawing", action="add", items=items)
    for item in ("thr", "len", "od", "nt", "ax"):
        assert re.search(rf"\b{item}\b.*\b(ok|warning)\b", added), (item, added)


async def test_a_section_carries_its_dimensions_thread_note_and_chain_line_in_the_pdf(
    workspace: Path, tmp_path: Path
) -> None:
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    async with running_server(workspace, visible=True) as (cad, _):
        cylinder = await make_sleeve(cad)
        await drawing_with_a_section(cad, cylinder)

        shot = await cad("drawing", action="capture")
        assert not shot.is_error, shot.text
        keep(shots, "sleeve.png", shot.images)
        assert (
            "TechDraw's own scene of the sheet window" in shot.text
            and "views on the sheet: front, top, section sec" in shot.text
        )
        zoom = await cad("drawing", action="capture", region=[20, 130, 200, 250])
        assert not zoom.is_error, zoom.text
        keep(shots, "sleeve_section.png", zoom.images)

        listed = await cad.ok("drawing", action="list")
        assert "thr" in listed and "A-A" in listed

        await finish_drawing(cad)
        pdf = workspace / "manguito.pdf"
        exported = await cad.ok("export", action="pdf", path=str(pdf))
        assert "ink in all 3 view(s) of the sheet" in exported, exported
        report = pdf_check.check_pdf(pdf, pages=1)
        assert report.ok, report.summary()
        shots.mkdir(parents=True, exist_ok=True)
        (shots / "sleeve.pdf").write_bytes(pdf.read_bytes())


async def test_a_text_that_states_what_the_hole_does_not_have_is_said_to_contradict_the_model(workspace: Path) -> None:
    async with running_server(workspace, visible=False) as (cad, _):
        cylinder = await make_sleeve(cad)
        await drawing_with_a_section(cad, cylinder)
        wrong = await cad.ok(
            "drawing", action="add", items=[{"type": "hole_dim", "id": "bad", "all": True, "text": "M42x1,5 - 6H 4x"}]
        )
        assert "contradice el modelo" in wrong and "M42x1,5" in wrong and "M45x1,5" in wrong and "4x" in wrong, wrong
        review = await cad.ok("drawing", action="review")
        assert "callout_contradicts" in review, review
        await cad.ok("drawing", action="remove", items=["bad"])
        review = await cad.ok("drawing", action="review")
        assert "callout_contradicts" not in review
