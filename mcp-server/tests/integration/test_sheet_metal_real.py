"""A bent bracket through the real server, FreeCAD and the pinned SheetMetal add-on (temporary FreeCAD user home): the
ops appear, the flat size is measured, the flat pattern goes to DXF with its hole, and without the add-on the guide and
the op error say how to install it."""

import re
from pathlib import Path

import pytest

from cotaforge_mcp.guide import ASSUMED_OPS
from tests.integration.conftest import running_server, volume
from tests.integration.sheetmetal_addon import make_user_home

OUTLINE = {
    "on": {"kind": "plane", "name": "XY"},
    "entities": [
        {"kind": "rect", "name": "o", "center": [50, 30], "width": 100, "height": 60},
        {"kind": "circle", "name": "h", "center": [30, 30], "diameter": 8},
    ],
}
TOP = {"kind": "face", "from_op": "s2", "select": {"normal": [0, 0, 1], "largest": True}}
SIDE = {"kind": "face", "from_op": "s2", "select": {"normal": [0, -1, 0], "largest": True}}
FLANGE = {"edge": {"kind": "edge", "between": [TOP, SIDE]}, "length_mm": 20, "angle_deg": 90, "bend_radius_mm": 3}
UNFOLD = {"face": {"kind": "face", "from_op": "s2", "select": {"normal": [0, 0, -1], "largest": True}}, "k_factor": 0.4}


@pytest.fixture
def with_addon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = make_user_home(tmp_path, with_addon=True)
    monkeypatch.setenv("FREECAD_USER_HOME", str(home))
    monkeypatch.setenv("FREECAD_USER_DATA", str(home))  # a set FREECAD_USER_DATA would win and hide the Mod folder
    return home


@pytest.fixture
def without_addon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = make_user_home(tmp_path, with_addon=False)
    monkeypatch.setenv("FREECAD_USER_HOME", str(home))
    monkeypatch.setenv("FREECAD_USER_DATA", str(home))  # a set FREECAD_USER_DATA would win and hide the Mod folder
    return home


async def test_a_bracket_is_bent_unfolded_and_its_flat_pattern_exported(workspace: Path, with_addon: Path) -> None:
    async with running_server(workspace) as (cad, init):
        assert f"ops (sheet metal{ASSUMED_OPS}): base_flange, edge_flange, unfold" in (init.instructions or "")
        await cad.ok("doc", action="new", name="soporte")
        await cad.ok("op", name="sketch", params=OUTLINE)
        base = await cad.ok("op", name="base_flange", params={"sketch": "s1", "thickness_mm": 2}, intent="lámina 2 mm")
        assert volume(base) == pytest.approx((6000 - 3.141592653589793 * 16) * 2, rel=1e-6)
        bent = await cad.ok("op", name="edge_flange", params=FLANGE, intent="labio de 20 mm")
        assert volume(bent) > volume(base) and "warning" not in bent  # the closed form agreed with the CAD
        flat = await cad.ok("op", name="unfold", params=UNFOLD, intent="K 0.4 del taller")
        match = re.search(r"flat pattern: ([\d.]+) x ([\d.]+) mm, thickness 2, blank area ([\d.]+) mm2", flat)
        assert match is not None, flat
        assert float(match.group(1)) == pytest.approx(100.0) and float(match.group(2)) == pytest.approx(
            75.969, abs=1e-3
        )
        assert volume(flat) == pytest.approx(volume(bent))  # the solid is still the bent part
        reply = await cad.ok("export", action="dxf", flat="s4", path="soporte_plano.dxf")
        assert "exported dxf" in reply
        text = (workspace / "soporte_plano.dxf").read_text(encoding="latin-1")
        assert "CIRCLE" in text and "$INSUNITS" in text
        # the bent part is still intact and exportable as a solid
        assert "exported step" in await cad.ok("export", action="step", path="soporte.step")


async def test_a_saved_bracket_reopens_with_its_steps_and_still_exports_its_flat_pattern(
    workspace: Path, with_addon: Path
) -> None:
    async with running_server(workspace) as (cad, _):
        await cad.ok("doc", action="new", name="soporte")
        await cad.ok("op", name="sketch", params=OUTLINE)
        await cad.ok("op", name="base_flange", params={"sketch": "s1", "thickness_mm": 2})
        bent = await cad.ok("op", name="edge_flange", params=FLANGE)
        await cad.ok("op", name="unfold", params=UNFOLD)
        await cad.ok("doc", action="save")
        await cad.ok("doc", action="close")
        reopened = await cad.ok("doc", action="open", path="soporte.FCStd")
        assert "restored" in reopened.lower() or "steps" in reopened.lower(), reopened
        assert volume(reopened) == pytest.approx(volume(bent), rel=1e-6)
        await cad.ok("export", action="dxf", flat="s4", path="despues.dxf")
        assert "CIRCLE" in (workspace / "despues.dxf").read_text(encoding="latin-1")


async def test_without_the_addon_the_guide_and_the_error_say_how_to_install_it(
    workspace: Path, without_addon: Path
) -> None:
    async with running_server(workspace) as (cad, init):
        # the handshake is answered before the CAD is up and the add-on is only known to a worker: the instructions must
        # not state the ops as a fact, and the guide and the op error (below) are where their absence is said
        instructions = init.instructions or ""
        assert f"ops (sheet metal{ASSUMED_OPS}): base_flange, edge_flange, unfold" in instructions
        assert "ops (sheet metal):" not in instructions
        listing = await cad.ok("cad_guide", topic="ops")
        assert "not available here: base_flange, edge_flange, unfold" in listing and "Addon manager" in listing
        assert not re.search(r"^base_flange\(", listing, re.MULTILINE)
        terms = await cad.ok("cad_guide", topic="terms")
        assert re.search(r"brida de arista.*not available with this CAD: the SheetMetal add-on is not installed", terms)
        await cad.ok("doc", action="new", name="soporte")
        await cad.ok("op", name="sketch", params=OUTLINE)
        refused = await cad.err("op", name="base_flange", params={"sketch": "s1", "thickness_mm": 2})
        assert "not available" in refused and "Addon manager" in refused and "restart" in refused
        # the rest of the CAD works as ever
        plain = {**OUTLINE, "entities": OUTLINE["entities"][:1]}
        await cad.ok("op", name="sketch", id="plain", params=plain)
        assert volume(await cad.ok("op", name="extrude", params={"sketch": "plain", "depth_mm": 2})) == pytest.approx(
            12000
        )
