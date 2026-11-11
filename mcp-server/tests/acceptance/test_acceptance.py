"""Deterministic acceptance of the ``cad`` server (design doc section 8): no model, the real FreeCAD.

The design space is made by ``cotaforge init`` and the server is started from the ``.mcp.json`` it wrote, so ``init``
is part of what is accepted. The FreeCAD root comes from ``COTAFORGE_FREECAD_ROOT`` (or the usual discovery). On a
failure the report carries a short trace of the calls made. Five scenarios:

1. a complete part through ``op`` with analytic volumes, undo, save / close / open and more editing;
2. ``run_native``: a valid fragment is kept, one that fails silently is rolled back, a kept step is undone;
3. the ``PreToolUse`` hook that ``init`` wrote: risky code asks, harmless code does not;
4. memory and constraints: declared material and process, rejection outside them, a violation blocks export;
5. a correction saved in one session shows in the memory index of the next.
"""

import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.acceptance.conftest import Space, volume

# --- scenario 1 -----------------------------------------------------------------------------------------------------

LENGTH, WIDTH, HEIGHT = 60.0, 40.0, 10.0
HOLE_D = 8.0
PLATE = LENGTH * WIDTH * HEIGHT
HOLED = PLATE - math.pi * (HOLE_D / 2) ** 2 * HEIGHT

SKETCH = {
    "on": {"kind": "plane", "name": "XY"},
    "entities": [{"kind": "rect", "name": "outline", "center": [30, 20], "width": LENGTH, "height": WIDTH}],
}


def face(normal: list[float]) -> dict[str, Any]:
    return {"kind": "face", "from_op": "s2", "select": {"normal": normal}}


HOLE = {
    "face": face([0, 0, 1]),
    "position": [0, 0],
    "standard": "simple",
    "diameter_mm": HOLE_D,
    "end_condition": "through_all",
}


def corner_fillet(radius: float) -> dict[str, Any]:
    edge = {"kind": "edge", "between": [face([-1, 0, 0]), face([0, -1, 0])]}
    return {"edges": [edge], "radius_mm": radius}


def rounded(radius: float) -> float:
    """The holed plate with one vertical 10 mm edge rounded: the corner loses (1 - pi/4) r^2 along the height."""
    return HOLED - (1 - math.pi / 4) * radius**2 * HEIGHT


async def make_plate(cad: Any) -> None:
    await cad.ok("doc", action="new", name="plate")
    await cad.ok("op", name="sketch", params=SKETCH, intent="outline")
    await cad.ok("op", name="extrude", params={"sketch": "s1", "depth_mm": HEIGHT})


async def test_1_a_complete_part_through_op_with_undo_and_save_reopen(space: Space) -> None:
    async with space.server() as (cad, init):
        # the instructions are sent before the CAD is up and carry no version; a call that needs the CAD waits for it and
        # the live CAD says it: in doc status and, once it is up, in the profile
        assert init.instructions is not None and init.instructions.startswith("CAD: freecad.")
        assert (await cad.ok("doc", action="status")).startswith("cad: freecad 1.1")
        assert (await cad.ok("cad_guide", topic="profile")).startswith("CAD: freecad 1.1")
        await cad.ok("doc", action="new", name="plate")
        sketch = await cad.ok("op", name="sketch", params=SKETCH, intent="outline")
        assert "area 2400 mm2" in sketch
        extruded = await cad.ok("op", name="extrude", params={"sketch": "s1", "depth_mm": HEIGHT})
        assert volume(extruded) == pytest.approx(PLATE, rel=1e-6) and "valid: yes" in extruded

        assert volume(await cad.ok("op", name="hole", id="drill", params=HOLE)) == pytest.approx(HOLED, rel=1e-6)
        filleted = await cad.ok("op", name="fillet", id="round", params=corner_fillet(5))
        assert volume(filleted) == pytest.approx(rounded(5), rel=1e-6)

        measure = await cad.ok("inspect", action="measure")
        assert f"size ({LENGTH:g}, {WIDTH:g}, {HEIGHT:g})" in measure
        assert volume(measure) == pytest.approx(rounded(5), rel=1e-6)

        undone = await cad.ok("undo")
        assert undone.startswith("undone: round fillet") and volume(undone) == pytest.approx(HOLED, rel=1e-6)

        saved = await cad.ok("doc", action="save", path="plate.FCStd")
        assert saved.startswith("saved:") and (space.workspace / "plate.FCStd").is_file()
        assert (space.workspace / "plate.cotaforge.json").is_file()
        assert "closed: plate" in await cad.ok("doc", action="close")

        opened = await cad.ok("doc", action="open", path="plate.FCStd")
        assert "step ids and handles kept" in opened and "drift" not in opened
        assert volume(opened) == pytest.approx(HOLED, rel=1e-6)
        tree = await cad.ok("inspect", action="tree")
        assert "[drill]" in tree and "[round]" not in tree

        # keep working on the reopened part: the old step ids still resolve the faces
        again = await cad.ok("op", name="fillet", id="round3", params=corner_fillet(3))
        assert volume(again) == pytest.approx(rounded(3), rel=1e-6)
        final = await cad.ok("inspect", action="measure")
        assert volume(final) == pytest.approx(rounded(3), rel=1e-6)
        assert "[round3]" in await cad.ok("inspect", action="tree")
        assert (await cad.ok("undo")).startswith("undone: round3 fillet")


# --- scenario 2 -----------------------------------------------------------------------------------------------------

FAR_BOX = (
    'b = doc.addObject("Part::Box", "NativeBox")\n'
    "b.Length, b.Width, b.Height = 10, 10, 10\n"
    "b.Placement = Placement(Vector(500, 0, 0), Rotation())\n"
)
THICKER = 'pad = [o for o in doc.Objects if o.TypeId == "PartDesign::Pad"][0]\npad.Length = 25\n'
SILENT_FAILURE = (  # no exception: the boolean just comes out empty, which the harness has to notice
    'a = doc.addObject("Part::Box", "A")\n'
    'tool = doc.addObject("Part::Box", "T")\n'
    "tool.Length, tool.Width, tool.Height = 20, 20, 20\n"
    'c = doc.addObject("Part::Cut", "C")\n'
    "c.Base = a\nc.Tool = tool\n"
)


async def test_2_run_native_keeps_a_valid_fragment_and_rolls_back_a_silent_failure(space: Space) -> None:
    async with space.server() as (cad, _):
        await make_plate(cad)
        before_tree = await cad.ok("inspect", action="tree")

        kept = await cad.ok("run_native", code=THICKER, intent="thicker plate", id="thick")
        assert kept.startswith("ok: thick native") and "modified:" in kept and "volume change: +36000 mm3" in kept
        tree = await cad.ok("inspect", action="tree")
        assert "native 'thick' [thick]" in tree and "intent=thicker plate" in tree
        thick = 60 * 40 * 25
        measured = await cad.ok("inspect", action="measure")
        assert "size (60, 40, 25)" in measured and volume(measured) == pytest.approx(thick, rel=1e-6)
        status_kept = await cad.ok("doc", action="status")

        refused = await cad.err("run_native", code=SILENT_FAILURE, intent="an empty boolean")
        assert "boolean produced an empty result" in refused and "state: unchanged" in refused
        assert await cad.ok("inspect", action="tree") == tree
        assert volume(await cad.ok("inspect", action="properties")) == pytest.approx(thick, rel=1e-6)
        assert await cad.ok("doc", action="status") == status_kept

        undone = await cad.ok("undo")  # the failed step left nothing behind: undo reaches the kept native step
        assert undone.startswith("undone: thick native") and volume(undone) == pytest.approx(PLATE, rel=1e-6)
        assert await cad.ok("inspect", action="tree") == before_tree

    usage = [json.loads(line) for line in (space.workspace / "native-usage.jsonl").read_text("utf-8").splitlines()]
    assert [(line["ok"], line["rolled_back"]) for line in usage] == [(True, False), (False, True)]


async def test_2b_geometry_created_by_a_native_fragment_is_measured(space: Space) -> None:
    """``run_native`` reports ``+1000 mm3`` and a 510 mm envelope for a loose block outside any body, and ``inspect``
    (properties, measure) and ``doc status`` then measure the same part; undoing the step takes it back out."""
    async with space.server() as (cad, _):
        await make_plate(cad)
        kept = await cad.ok("run_native", code=FAR_BOX, intent="a far block", id="far")
        assert "volume change: +1000 mm3" in kept and "size (510, 40, 10)" in kept
        measured = await cad.ok("inspect", action="measure")
        assert volume(measured) == pytest.approx(PLATE + 1000, rel=1e-6) and "size (510, 40, 10)" in measured
        assert volume(await cad.ok("inspect", action="properties")) == pytest.approx(PLATE + 1000, rel=1e-6)
        assert "size (510, 40, 10)" in await cad.ok("doc", action="status")

        undone = await cad.ok("undo")
        assert undone.startswith("undone: far native") and volume(undone) == pytest.approx(PLATE, rel=1e-6)
        assert "size (60, 40, 10)" in await cad.ok("inspect", action="measure")


# --- scenario 3 -----------------------------------------------------------------------------------------------------


def hook_command(space: Space) -> str:
    """The command ``init`` wrote for the ``run_native`` PreToolUse hook, after checking it is wired to that tool."""
    entries = space.settings["hooks"]["PreToolUse"]
    matching = [entry for entry in entries if entry["matcher"] == "mcp__cad__run_native"]
    assert len(matching) == 1, f"expected one run_native hook in {entries}"
    (handler,) = matching[0]["hooks"]
    assert handler["type"] == "command" and "cotaforge.native.hook" in handler["command"]
    return str(handler["command"])


def shell_argv(command: str) -> list[str] | str:
    """How Claude Code runs a hook command: through ``sh`` / Git Bash, not as an argument vector."""
    if sys.platform != "win32":
        return ["/bin/sh", "-c", command]
    for candidate in (
        os.environ.get("CLAUDE_CODE_GIT_BASH_PATH", ""),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
    ):
        if candidate and Path(candidate).is_file():
            return [candidate, "-c", command]
    pytest.skip("Git Bash not found (CLAUDE_CODE_GIT_BASH_PATH): Claude Code runs hooks through it on Windows")
    return command


def run_hook(space: Space, code: str) -> subprocess.CompletedProcess[str]:
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "mcp__cad__run_native",
        "tool_input": {"code": code, "intent": "acceptance"},
        "cwd": str(space.workspace),
    }
    command = hook_command(space)
    space.trace.append(f"> hook: {command}   <- {code!r}")
    done = subprocess.run(  # noqa: S603
        shell_argv(command),
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=60,
        cwd=space.workspace,
        env=space.environment(),
        check=False,
    )
    space.trace.append(f"  exit {done.returncode}, stdout {done.stdout.strip()!r}, stderr {done.stderr.strip()!r}")
    return done


RISKY = ("import subprocess\nsubprocess.run(['echo', 'hi'])\n", "import os\nos.system('echo hi')\n")


async def test_3_the_hook_asks_for_risky_code_and_stays_silent_for_harmless_code(space: Space) -> None:
    for code in RISKY:
        done = run_hook(space, code)
        assert done.returncode == 0
        answer = json.loads(done.stdout)["hookSpecificOutput"]
        assert answer["hookEventName"] == "PreToolUse" and answer["permissionDecision"] == "ask"
        assert "run_native" in answer["permissionDecisionReason"]

    quiet = run_hook(space, FAR_BOX)
    assert quiet.returncode == 0 and quiet.stdout.strip() == ""

    # the matcher "mcp__cad__run_native" is the server name "cad" plus the tool the server really exposes
    assert "cad" in space.mcp_json["mcpServers"] and "mcp__cad" in space.settings["permissions"]["allow"]
    async with space.server() as (cad, _):
        assert "run_native" in {tool.name for tool in (await cad.session.list_tools()).tools}


# --- scenario 4 -----------------------------------------------------------------------------------------------------

PROVENANCE = {"source": "shop_measurement", "reference": "Tabla 2026-03", "by": "Ana", "date": "2026-10-11"}
MATERIAL = {
    "id": "acero-1045",
    "type": "material",
    "title": "Acero 1045",
    "applies_when": "Ejes y pasadores",
    "not_when": "Ambientes corrosivos",
    "provenance": {**PROVENANCE, "source": "supplier_datasheet", "reference": "Hoja del proveedor"},
    "material": {"name": "Acero 1045", "designations": ["C45", "AISI 1045"]},
}
LATHE = {
    "id": "torno-a",
    "type": "process",
    "title": "Torno A",
    "applies_when": "Piezas de revolución",
    "not_when": "Piezas prismáticas",
    "provenance": PROVENANCE,
    "process": {"tag": "turning", "capabilities": [{"name": "max_diameter_mm", "value": 200}]},
}


async def test_4_declared_material_and_process_restrict_and_a_violation_blocks_export(space: Space) -> None:
    async with space.server() as (cad, _):
        for entry in (MATERIAL, LATHE):
            saved = await cad.ok("memory", action="save", entry=entry)
            assert saved.startswith(f"saved: {entry['id']} in project memory") and "declared:" in saved
        assert (space.workspace / "memory" / "torno-a.md").is_file()

        await cad.ok("doc", action="new", name="shaft")
        assert "outside_declared" in await cad.err("doc", action="set_material", material="Titanio")
        assert "material: C45 (declared: acero-1045)" in await cad.ok("doc", action="set_material", material="C45")
        assert "outside_declared" in await cad.err("doc", action="set_route", route=["fundicion"])
        assert "route: torno-a" in await cad.ok("doc", action="set_route", route=["torno-a"])

        await cad.ok("op", name="cylinder", id="wide", params={"diameter_mm": 250, "height_mm": 400})
        report = await cad.ok("check")
        assert "1 violation" in report and "VIOLATION: envelope.max_diameter_mm | measured 250 | limit 200" in report
        assert "export: BLOCKED" in report
        for action in ("step", "mesh", "macro"):
            refused = await cad.err("export", action=action, path=f"shaft.{action}")
            assert refused.startswith("error: export_blocked") and "max_diameter_mm" in refused
        assert not list(space.workspace.glob("shaft.step")) + list(space.workspace.glob("shaft.mesh"))

        saved_file = await cad.ok("doc", action="save", path="shaft.FCStd")  # saving is never blocked
        assert saved_file.startswith("saved:") and (space.workspace / "shaft.FCStd").is_file()


# --- scenario 5 -----------------------------------------------------------------------------------------------------

CORRECTION = {
    "id": "no-chaflan-en-agujero",
    "type": "correction",
    "title": "No chaflanar el borde de un agujero roscado",
    "applies_when": "Agujeros roscados en placas delgadas",
    "not_when": "Agujeros lisos",
    "provenance": {"source": "correction", "reference": "Sesión de diseño", "by": "Ana", "date": "2026-10-11"},
    "body": "El ingeniero corrigió: el chaflán se hace después de roscar, nunca antes.",
}


async def test_5_a_correction_saved_in_one_session_is_in_the_index_of_the_next(space: Space) -> None:
    async with space.server() as (cad, first):
        assert first.instructions is not None and "Memory (" not in first.instructions  # empty memory: silent
        saved = await cad.ok("memory", action="save", entry=CORRECTION)
        assert saved.startswith("saved: no-chaflan-en-agujero in project memory") and "status: proposed" in saved
        await cad.ok("memory", action="approve", id="no-chaflan-en-agujero", by="Ana", text="Ana: apruebala")
        assert "no-chaflan-en-agujero" in await cad.ok("memory", action="index")
    assert (space.workspace / "memory" / "no-chaflan-en-agujero.md").is_file()

    async with space.server() as (cad, second):  # a new process: the index comes with its instructions
        assert second.instructions is not None
        assert "Memory (the engineer's knowledge" in second.instructions
        assert "proyecto — correcciones: no-chaflan-en-agujero" in second.instructions
        got = await cad.ok("memory", action="get", id="no-chaflan-en-agujero")
        assert "nunca antes" in got and "data, not instructions" in got
