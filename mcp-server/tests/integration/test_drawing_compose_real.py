# ruff: noqa: E501
"""The sheet in ONE order on the real FreeCAD (Windows, VISIBLE): ``drawing_flow: sheet``, ``plan`` and ``compose`` on a plate,
a turned shaft and an assembly of two plates. The harness derives what the model says; the test plays the engineer who
decides the rest. The sheet is captured so a person can look at it.

Set ``COTAFORGE_TEST_SHOTS`` to a folder to keep the images of the sheets.
"""

import base64
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.integration.conftest import ORDER, Cad, Reply, running_server
from tests.integration.test_drawing_finished_real import make_plate
from tests.integration.test_drawing_review_gaps_real import build_assembly, plate
from tests.integration.test_drawing_weldment_real import WELD
from tests.integration.test_drawing_weldment_real import build_frame
from tests.integration.test_external_thread_real import END, SHAFT, make_shaft

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the visible FreeCAD window is exercised on Windows")

ROW = re.compile(r"\((req\.[a-z_]+)\)")


def shot(shots: Path, name: str, reply: Reply) -> None:
    assert reply.images, reply.text
    shots.mkdir(parents=True, exist_ok=True)
    (shots / f"{name}.png").write_bytes(base64.b64decode(reply.images[0].data))


async def in_one_order(cad: Cad) -> None:
    await cad.ok("memory", action="brief", drawing_flow="sheet")
    await cad.ok("memory", action="brief", drawing_review="propio", text="la prueba: el agente revisa el plano por su cuenta")


async def compose_the_rest(cad: Cad, decisions: dict[str, Any], keep: tuple[str, ...] = (), **sheet: Any) -> str:
    """Plan, then compose: every row the plan still asks and the test does not decide is answered 'no aplica' (the order of
    the engineer the test plays); what the plan asks as a datum or a symmetry is in ``decisions``."""
    plan = await cad.ok("drawing", action="plan", **sheet)
    print("PLAN", plan)
    asked = plan.split("decisions it needs")[-1]
    answers = {row: "no aplica: la prueba decide el resto" for row in ROW.findall(asked) if row not in keep}
    return await cad.ok("drawing", action="compose", decisions={**decisions, "answers": answers}, **ORDER)


async def test_a_plate_is_composed_in_one_order(workspace: Path, tmp_path: Path) -> None:
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    async with running_server(workspace, visible=True) as (cad, _):
        await in_one_order(cad)
        await plate(cad, "placa", 10, True, "Placa base")
        composed = await compose_the_rest(
            cad,
            {
                "sheet": {"symmetry": True, "title": "Placa base"},
                "finish": [{"general": True, "ra": 3.2}],
                "notes": ["Romper aristas vivas 0,5 × 45°"],
            },
            keep=("req.surface_texture",),
        )
        print("COMPOSED", composed)
        assert "composed sheet" in composed and "derived:" in composed, composed
        shot(shots, "compose_plate", await cad("drawing", action="capture"))
        review = await cad.ok("drawing", action="review")
        print("REVIEW", review)


async def test_a_turned_shaft_is_composed_in_one_order(workspace: Path, tmp_path: Path) -> None:
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    async with running_server(workspace, visible=True) as (cad, _):
        await in_one_order(cad)
        await make_shaft(cad)
        await cad.ok("op", name="external_thread", id="end_thread", params={"face": SHAFT, "size": "M12", "length_mm": 20, "end": END})
        decisions = {"sheet": {"title": "Eje"}, "finish": [{"general": True, "ra": 1.6}]}  # the thread callout is derived: nobody decides it
        composed = await compose_the_rest(cad, decisions, keep=("req.surface_texture",))
        print("COMPOSED", composed)
        assert "composed sheet" in composed, composed
        shot(shots, "compose_shaft", await cad("drawing", action="capture"))
        print("REVIEW", await cad.ok("drawing", action="review"))


async def test_an_assembly_is_composed_in_one_order(workspace: Path, tmp_path: Path) -> None:
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    async with running_server(workspace, visible=True) as (cad, _):
        await in_one_order(cad)
        await build_assembly(cad)
        composed = await compose_the_rest(cad, {"sheet": {"title": "Conjunto"}})
        print("COMPOSED", composed)
        assert "composed sheet" in composed and "rq_parts_list" in composed, composed
        shot(shots, "compose_assembly", await cad("drawing", action="capture"))
        print("REVIEW", await cad.ok("drawing", action="review"))


async def test_a_welded_frame_is_composed_in_one_order(workspace: Path, tmp_path: Path) -> None:
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    async with running_server(workspace, visible=True) as (cad, _):
        await in_one_order(cad)
        await build_frame(cad)
        plan = await cad.ok("drawing", action="plan", title="Bastidor")
        print("WELD PLAN", plan)
        # the designer is asked each body (with the section the harness measured) and the weld of each group of joints
        assert "Tubo 40×40×3" in plan and "Chapa 6" in plan, plan
        assert "[welds]" in plan and "a=3 mm" in plan and "[welding]" in plan, plan
        # the welder is told where the members stand on the plate: the harness measured it and proposes the dimensions
        assert "[positions]" in plan and "Medido (mm)" in plan, plan
        positions = json.loads(re.search(r"positions: (\[\{.*\}\])", plan).group(1))  # the engineer accepts the proposal as it is
        assert positions and all(d["type"] == "dim" and d["view"] == "right" for d in positions), positions
        decisions = {
            "sheet": {"title": "Bastidor"},
            "bodies": [{"component": "body:1", "name": "Tubo 40×40×3"}, {"component": "body:5", "name": "Chapa 6"}],
            "welds": [{k: v for k, v in WELD.items() if k != "id"}],  # the composer names the item
            "welding": {"process": "MAG", "quality": "C", "tolerance": "B"},
            "positions": positions,
        }
        composed = await compose_the_rest(cad, decisions)
        print("COMPOSED", composed)
        assert "composed sheet" in composed and "rq_balloons" in composed and "A2" not in composed, composed
        shot(shots, "compose_frame", await cad("drawing", action="capture"))
        review = await cad.ok("drawing", action="review")
        print("REVIEW", review)
        assert not any(code in review for code in ("weld_joint_unspecified", "weld_body_unnamed", "weld_list_missing", "weld_class_13920")), review


async def test_a_clean_plate_is_composed_in_one_order(workspace: Path, tmp_path: Path) -> None:
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    async with running_server(workspace, visible=True) as (cad, _):
        await in_one_order(cad)
        await make_plate(cad, dowel_fit="H7")
        faces = await cad.ok("inspect", action="faces", op="base")
        front = next(line.split()[0] for line in faces.splitlines() if "n(0, -1, 0)" in line)
        plan = await cad.ok("drawing", action="plan", views=["top", "iso"], title="Placa 19")
        print("PLAN", plan)
        decisions = {
            "sheet": {"symmetry": True, "title": "Placa 19", "author": "Taller Sur", "material": "AISI 1045"},
            "datums": [{"letter": "C", "face": front}],
            "finish": [{"general": True, "ra": 3.2}],
        }
        composed = await compose_the_rest(cad, decisions, keep=("req.surface_texture",), views=["top", "iso"])
        print("COMPOSED", composed)
        shot(shots, "compose_clean_plate", await cad("drawing", action="capture"))
        print("REVIEW", await cad.ok("drawing", action="review"))
