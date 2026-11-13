# ruff: noqa: E501
"""The two sheets the owner watched, on the real FreeCAD (Windows, VISIBLE): an assembly of two plates held apart by two
spacer sleeves, and a threaded shaft with a chamfered end. The harness must put by itself what it is sure of (the parts list
and the balloons on the isometric view, the axes of the sleeves, the callout of the chamfer, the width below the view where
its extension lines would cross the gap) and the review must flag what is left (a section with no dimension).

Set ``COTAFORGE_TEST_SHOTS`` to a folder to keep the images of the sheets.
"""

import base64
import os
import re
import sys
from pathlib import Path

import pytest

from tests.integration.conftest import Cad, Reply, pending_points, running_server
from tests.integration.test_external_thread_real import END, SHAFT, make_shaft

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the visible FreeCAD window is exercised on Windows")

XY = {"kind": "plane", "name": "XY"}


async def plate(cad: Cad, name: str, thick: float, tapped: bool) -> None:
    await cad.ok("doc", action="new", name=name)
    await cad.ok("doc", action="set_material", material="S355J2")
    entities = [{"kind": "rect", "name": "plate", "center": [0, 0], "width": 120, "height": 60, "chamfer_mm": 4}]
    await cad.ok("op", name="sketch", id="outline", params={"on": XY, "entities": entities})
    await cad.ok("op", name="extrude", id="base", params={"sketch": "outline", "depth_mm": thick})
    face = {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, 1]}}
    for i, x in enumerate((-40, 40), start=1):
        if tapped:
            hole = {"standard": "iso_tapped", "size": "M8", "diameter_mm": 6.8, "end_condition": "through_all", "thread_depth_mm": 10}
        else:
            hole = {"standard": "iso_clearance", "size": "M8", "diameter_mm": 9, "end_condition": "through_all"}
        await cad.ok("op", name="hole", id=f"h{i}", params={"face": face, "at": [x, 0, thick], **hole})
    await cad.ok("doc", action="save", path=f"{name}.FCStd")


async def build_assembly(cad: Cad) -> None:
    await plate(cad, "base-plate", 10, True)
    await plate(cad, "top-plate", 8, False)
    await cad.ok("doc", action="new", name="spacer")
    await cad.ok("doc", action="set_material", material="C45")
    ring = [
        {"kind": "circle", "name": "od", "center": [0, 0], "diameter": 16},
        {"kind": "circle", "name": "id", "center": [0, 0], "diameter": 9},
    ]
    await cad.ok("op", name="sketch", id="ring", params={"on": XY, "entities": ring})
    await cad.ok("op", name="extrude", id="tube", params={"sketch": "ring", "depth_mm": 18})
    await cad.ok("doc", action="save", path="spacer.FCStd")
    await cad.ok("doc", action="new", name="clamp-assembly", kind="assembly")
    await cad.ok("op", name="insert_component", id="base", params={"part": "base-plate", "placement": {"origin": [0, 0, 0]}})
    await cad.ok("op", name="fix_component", id="base_fix", params={"component": "base"})
    for n, x in ((1, -40), (2, 40)):
        await cad.ok("op", name="insert_component", id=f"spacer_{n}", params={"part": "spacer", "placement": {"origin": [x, 0, 10]}})
    await cad.ok("op", name="insert_component", id="top", params={"part": "top-plate", "placement": {"origin": [0, 0, 28]}})


def shot(shots: Path, name: str, reply: Reply) -> None:
    assert reply.images, reply.text
    (shots / f"{name}.png").write_bytes(base64.b64decode(reply.images[0].data))


async def test_the_assembly_sheet_gets_its_list_balloons_and_axes_and_a_width_clear_of_the_gap(workspace: Path, tmp_path: Path) -> None:
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    shots.mkdir(parents=True, exist_ok=True)
    async with running_server(workspace, visible=True) as (cad, _):
        await build_assembly(cad)
        made = await cad.ok("drawing", action="create", template="A3", angle="first", views=["front", "top", "iso"], title="Conjunto")
        assert "rq_parts_list" in made and "rq_balloons" in made, made  # the harness added the list and the balloons
        section = {"type": "section", "id": "secB", "at": [0, 0, 18], "normal": [0, 1, 0], "view": "top", "letter": "B"}
        await cad.ok("drawing", action="add", items=[section])
        review = await cad.ok("drawing", action="review")
        findings, _, _ = await pending_points(cad)
        assert not any(f.startswith("dimension_side") for f in findings), review  # the width went below: nothing crosses the gap
        assert "view_adds_nothing:B-B" in findings, review  # a section with no dimension is flagged ...
        assert not any(f.startswith(("cylinder_no_axis", "balloons_off_isometric")) for f in findings), review  # ... the sleeves have axes
        shot(shots, "rv1_assembly", await cad("drawing", action="capture"))
        own_word = await cad("drawing", action="answer", id="view_adds_nothing:B-B", text="no aplica: el corte solo enseña el apilado")
        assert own_word.is_error or "order" in own_word.text.lower(), own_word.text  # the agent cannot dismiss it on its own word


async def test_the_threaded_shaft_says_its_diameter_once_and_dimensions_its_chamfer(workspace: Path, tmp_path: Path) -> None:
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    shots.mkdir(parents=True, exist_ok=True)
    async with running_server(workspace, visible=True) as (cad, _):
        await make_shaft(cad)
        await cad.ok("op", name="external_thread", id="end_thread", params={"face": SHAFT, "size": "M12", "length_mm": 20, "end": END})
        faces = await cad.ok("inspect", action="faces", surface="cylinder")
        cylinder = re.search(r"face:\d+", faces)
        assert cylinder, faces
        created = await cad.ok("drawing", action="create", template="A3", scale="2:1", title="Eje M12", views=["front", "top"])
        assert "width Ø12" in created, created  # the overall diameter is a diameter (it was a bare 12, twice)
        item = {"type": "dim", "id": "thr", "kind": "diameter", "face": cylinder.group(0), "view": "front"}
        added = await cad.ok("drawing", action="add", items=[item])
        assert "thread M12" in added, added
        review = await cad.ok("drawing", action="review")
        assert "rq_chamfers" in created + added + review, review  # the callout of the chamfer is the harness's
        assert "chamfer_no_dimension" not in review, review
        shot(shots, "rv1_shaft", await cad("drawing", action="capture"))
