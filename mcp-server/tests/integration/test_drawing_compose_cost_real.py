# ruff: noqa: E501
"""The time of a ``compose`` of a welded part on the real FreeCAD (Windows, VISIBLE): a bracket of four bodies (a base plate with
four holes, a hub tube standing on it and two ribs), the part that took 4 to 15 minutes per ``compose`` in the drawing bench of
2026-11-13 (the plate, the tube and the ribs make 8 joints in 3 groups, and every request that reviews the sheet draws it again).

The decisions are the ones the plan itself proposes (bodies, a weld per group, the process), so the test does not depend on how
the bodies are numbered. What is asserted is the time: the sheet is drawn a handful of times, each in a couple of seconds.
"""

import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from tests.integration.conftest import running_server

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the visible FreeCAD window is exercised on Windows")

BRACKET = """
import Part
plate = Part.makeBox(160, 100, 12, Vector(-80, -50, 0))
for x in (-65, 65):
    for y in (-35, 35):
        plate = plate.cut(Part.makeCylinder(6.5, 12, Vector(x, y, 0)))
plate = plate.cut(Part.makeCylinder(19, 12, Vector(0, 0, 0)))
hub = Part.makeCylinder(35, 55, Vector(0, 0, 12)).cut(Part.makeCylinder(19, 55, Vector(0, 0, 12)))
rib_right = Part.makeBox(40, 10, 40, Vector(34, -5, 12))
rib_left = Part.makeBox(40, 10, 40, Vector(-74, -5, 12))
for name, shape in (("Plate", plate), ("Hub", hub), ("RibRight", rib_right), ("RibLeft", rib_left)):
    doc.addObject("Part::Feature", name).Shape = shape
"""
CEILING_S = 55.0
"""This bracket took 83 s to compose before the sheet was made cheap to draw and 27 s afterwards (the bracket of the bench: 148 s
and 17 s); the ceiling leaves room for a slower machine and fails the code that took 83 s."""


def _json_at(text: str, start: int) -> Any:
    """The JSON value (an object or a list) that starts at ``start`` of the plan, wherever it ends."""
    return json.JSONDecoder().raw_decode(text, start)[0]


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COTAFORGE_HOME", str(tmp_path / "home"))


async def test_compose_of_a_welded_bracket_takes_seconds_and_not_minutes(workspace: Path) -> None:
    async with running_server(workspace, visible=True) as (cad, _):
        await cad.ok("memory", action="brief", drawing_flow="sheet")
        await cad.ok("doc", action="new", name="bracket")
        await cad.ok("doc", action="set_material", material="S355J2")
        await cad.ok("run_native", code=BRACKET, intent="a welded bracket: base plate, hub tube and two ribs")
        plan = await cad.ok("drawing", action="plan")
        bodies = [{"component": c, "name": f"Pieza {c.split(':')[1]}"} for c in dict.fromkeys(re.findall(r'"component":"(body:\d+)"', plan))]
        welds = [_json_at(plan, m.start()) for m in re.finditer(r'\{"type":"weld"', plan)]
        positions = [_json_at(plan, m.end() - 1) for m in re.finditer(r"positions: \[", plan)]  # the dimensions that locate the bodies on the plate
        rows = sorted(set(re.findall(r"\((req\.[a-z_]+)\)", plan.split("decisions it needs")[-1])))
        assert bodies and welds, plan  # the plan of a welded part asks for the designations and a weld per group of joints
        decisions = {
            "sheet": {"template": "A3", "symmetry": True},
            "bodies": bodies,
            "welds": welds,
            **({"positions": positions[0]} if positions else {}),
            "welding": {"process": "MAG", "quality": "C", "tolerance": "B"},
            "answers": {r: "no aplica: la ménsula de esta prueba no lo declara" for r in rows},
        }
        started = time.monotonic()
        composed = await cad.ok("drawing", action="compose", decisions=decisions, by="la prueba", reason="la prueba mide el tiempo")
        elapsed = time.monotonic() - started
        print(f"COMPOSE_SECONDS {elapsed:.1f}")
        assert "composed sheet" in composed, composed
        assert elapsed < CEILING_S, f"compose took {elapsed:.0f} s (the ceiling is {CEILING_S:.0f} s)"
