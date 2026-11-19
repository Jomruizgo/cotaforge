# ruff: noqa: E501
"""The two sheets the owner watched, on the real FreeCAD (Windows, VISIBLE): an assembly of two plates held apart by two
spacer sleeves, and a threaded shaft with a chamfered end. The harness must put by itself what it is sure of (the parts list
and the balloons on the isometric view, the axes of the sleeves, the callout of the chamfer, the width below the view where
its extension lines would cross the gap) and the review must flag what is left (a section with no dimension).

Set ``COTAFORGE_TEST_SHOTS`` to a folder to keep the images of the sheets.
"""

import base64
import os
import sys
from pathlib import Path

import pytest

from tests.integration.conftest import Cad, Reply, pending_points, running_server

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the visible FreeCAD window is exercised on Windows")

XY = {"kind": "plane", "name": "XY"}


async def plate(cad: Cad, name: str, thick: float, tapped: bool, title: str | None) -> None:
    await cad.ok("doc", action="new", name=name)
    await cad.ok("doc", action="set_material", material="S355J2")
    if title:
        await cad.ok("doc", action="set_title", name=title)  # the designation the parts list prints, not the file name
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


async def build_assembly(cad: Cad, titled: bool = True) -> None:
    await plate(cad, "base-plate", 10, True, "Placa base" if titled else None)
    await plate(cad, "top-plate", 8, False, "Placa superior" if titled else None)
    await cad.ok("doc", action="new", name="spacer")
    await cad.ok("doc", action="set_material", material="C45")
    if titled:
        await cad.ok("doc", action="set_title", name="Separador")
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
        assert "plano de conjunto" in review and "los ajustes" in review, review  # ... and the review asks what it shows
        assert "part_unnamed" not in "".join(findings), review  # every part has its designation
        refused = await cad.err("export", action="pdf", path="rv2_assembly.pdf")  # the measured gate holds the export
        print("EXPORT REFUSED", refused)
        assert "view_adds_nothing:B-B" in refused, refused
        assert not any(f.startswith(("cylinder_no_axis", "balloons_off_isometric")) for f in findings), review  # ... the sleeves have axes
        shot(shots, "rv1_assembly", await cad("drawing", action="capture"))
        own_word = await cad("drawing", action="answer", id="view_adds_nothing:B-B", text="no aplica: el corte solo enseña el apilado")
        assert own_word.is_error or "order" in own_word.text.lower(), own_word.text  # the agent cannot dismiss it on its own word


async def test_a_part_with_no_designation_is_a_question_and_the_file_name_is_never_printed(workspace: Path, tmp_path: Path) -> None:
    async with running_server(workspace, visible=True) as (cad, _):
        await build_assembly(cad, titled=False)
        await cad.ok("drawing", action="create", template="A3", angle="first", views=["front", "top", "iso"], title="Conjunto")
        review = await cad.ok("drawing", action="review")
        findings, _, _ = await pending_points(cad)
        assert any(f.startswith("part_unnamed") for f in findings), review  # it asks the designer, it does not guess
        line = next(row for row in review.splitlines() if "part_unnamed" in row)
        assert "top_plate" not in line and "top-plate" not in line and "set_title" in line, line
        refused = await cad.err("export", action="pdf", path="rv2_unnamed.pdf")
        assert "part_unnamed" in refused, refused


async def test_an_assembly_with_no_views_asked_is_the_section_through_its_holes_and_the_top_view_with_the_hole_positions(
    workspace: Path, tmp_path: Path
) -> None:
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    shots.mkdir(parents=True, exist_ok=True)
    async with running_server(workspace, visible=True) as (cad, _):
        await build_assembly(cad)
        made = await cad.ok("drawing", action="create", angle="first", title="Conjunto")
        print("CREATED", made)
        assert "section A-A through their axes" in made and "views: top, iso" in made.replace("views chosen from the assembly", "views: top, iso"), made
        review = await cad.ok("drawing", action="review")
        print("REVIEW", review)
        findings, _, _ = await pending_points(cad)
        print("FINDINGS", findings)
        assert not any(f.startswith("view_adds_nothing") for f in findings), review  # the section is the main view and carries the height
        assert not any(f.startswith("hole_not_located") for f in findings), review  # Ø, centre distance and edge distance are on the sheet
        assert any(f.startswith("assembly_not_joined") for f in findings), review  # two plates and two spacers, nothing joins them
        assert "BLOCKS EXPORT" in review, review
        shot(shots, "rv3_assembly", await cad("drawing", action="capture"))
