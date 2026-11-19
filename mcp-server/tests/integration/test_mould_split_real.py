"""The parting line, the parting surface and the core / cavity split through the real stdio server and FreeCAD, on three
parts of made-up values: a shell with ribs and bosses (a planar parting), a shell whose rim steps (a run-off surface),
and a shell with a hole across the pull (an undercut: a slide).

Every number is derived by hand. The shell is two drafted boxes (the outer 80 x 60 x 30, the inner 75 x 55 x 27.5, both
wider at the top, 1.5 degrees) subtracted from one another: the outer walls look down (the cavity's), the inner ones up
(the core's), the rim and the floor look up, the bottom looks down, so the parting line is
the one loop of the rim's outer
edge. Core + cavity + slides + part must add up to the block (checked by the CAD itself, within 0.01 %).

Set ``COTAFORGE_TEST_SHOTS`` to a folder to keep the pictures of the halves. Runs in a VISIBLE FreeCAD window."""

import base64
import os
import re
from pathlib import Path
from typing import Any

from tests.integration.conftest import Cad, running_server

BODY = "body"
PULL_DOWN = [0, 0, -1]  # the cavity moves down: its walls are drafted so their outward normals tilt that way


def _body(op: str) -> dict[str, Any]:
    return {"kind": BODY, "from_op": op}


def _face(op: str, normal: list[int]) -> dict[str, Any]:
    return {"kind": "face", "from_op": op, "select": {"normal": normal}}


async def _drafted_box(cad: Cad, name: str, size: list[float], origin: list[float], intent: str) -> None:
    await cad.ok("op", name="box", id=name, intent=intent, params={"size": size, "placement": {"origin": origin}})
    await cad.ok(
        "op",
        name="draft",
        id=f"{name}_draft",
        intent=f"1.5 degrees on the walls of {name}: the cavity half moves down",
        params={
            "faces": [_face(name, n) for n in ([1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0])],
            "neutral": {"kind": "plane", "name": "XY"},
            "pull": PULL_DOWN,
            "angle_deg": 1.5,
        },
    )


async def _shell(cad: Cad) -> None:
    """The open-top shell: wall 2.5, floor 2.5, drafted walls (outer 80 x 60 x 30, inner 75 x 55 x 27.5)."""
    await _drafted_box(cad, "outer", [80, 60, 30], [-40, -30, 0], "outside of the shell")
    await _drafted_box(cad, "inner", [75, 55, 27.5], [-37.5, -27.5, 2.5], "inside of the shell")
    await cad.ok(
        "op",
        name="copy_body",
        id="outer_copy",
        intent="a boolean works on a plain body: the drafted outside, copied",
        params={"source": _body("outer")},
    )
    await cad.ok(
        "op",
        name="copy_body",
        id="inner_copy",
        intent="the drafted inside, copied for the same reason",
        params={"source": _body("inner")},
    )
    await cad.ok(
        "op",
        name="boolean_subtract",
        id="shell_part",
        intent="the shell: the outside less the inside",
        params={"target": _body("outer_copy"), "tools": [_body("inner_copy")]},
    )


async def _shrink_and_look(cad: Cad, source: str) -> None:
    await cad.ok(
        "op",
        name="shrink_body",
        id="shrunk",
        intent="the part as the cavity must be cut",
        params={"source": _body(source), "shrinkage_percent": 0.6, "factor_source": "test value (made up)"},
    )


def _gap(text: str) -> float:
    found = re.search(r"gap ([\d.eE+-]+) \(limit", text)
    assert found, text
    return float(found.group(1))


async def _picture(cad: Cad, shots: Path, name: str) -> None:
    reply = await cad("view", action="capture")
    if reply.images:
        shots.mkdir(parents=True, exist_ok=True)
        (shots / f"{name}.png").write_bytes(base64.b64decode(reply.images[0].data))


async def _pictures(cad: Cad, tmp_path: Path, name: str, hide: list[str], cavity: str, core: list[str]) -> None:
    """Three pictures: the block closed (the part and its shrunk copy hidden), the cavity alone, and the core (with
    the slides) alone, seen from below where it forms the part. The body ids are those the steps above made."""
    shots = _shots(tmp_path)
    await cad.ok("view", action="style", ids=hide, visible=False)
    await cad.ok("view", action="set_view", name="iso")
    await _picture(cad, shots, f"{name}_closed")
    await cad.ok("view", action="style", ids=core, visible=False)
    await _picture(cad, shots, f"{name}_cavity")
    await cad.ok("view", action="style", ids=core, visible=True)
    await cad.ok("view", action="style", ids=[cavity], visible=False)
    await cad.ok("view", action="set_view", name="bottom")
    await _picture(cad, shots, f"{name}_core")


def _record(tmp_path: Path, name: str, text: str) -> None:
    """Keep what the split answered next to the pictures."""
    folder = _shots(tmp_path)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.txt").write_text(text, encoding="utf-8")


def _shots(tmp_path: Path) -> Path:
    return Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")


async def test_a_shell_with_ribs_and_bosses_splits_along_one_plane(workspace: Path, tmp_path: Path) -> None:
    async with running_server(workspace, visible=True) as (cad, _):
        await cad.ok("doc", action="new", name="shell")
        await _shell(cad)
        for n, y in enumerate((-12, 12)):
            await cad.ok(
                "op",
                name="box",
                id=f"rib{n}",
                intent="a rib on the floor",
                params={"size": [60, 2, 12.5], "placement": {"origin": [-30, y - 1, 2.0]}},
            )
        await cad.ok(
            "op",
            name="boolean_union",
            id="ribbed",
            intent="the ribs on the shell",
            params={"target": _body("shell_part"), "tools": [_body("rib0"), _body("rib1")]},
        )
        for n, (x, y) in enumerate(((-30, -20), (30, -20), (-30, 20), (30, 20))):
            await cad.ok(
                "op",
                name="cylinder",
                id=f"boss{n}",
                intent="a screw boss",
                params={"diameter_mm": 8, "height_mm": 14, "origin": [x, y, 2.0]},
            )
        await cad.ok(
            "op",
            name="boolean_union",
            id="part",
            intent="the bosses on the ribbed shell",
            params={"target": _body("ribbed"), "tools": [_body(f"boss{n}") for n in range(4)]},
        )
        await _shrink_and_look(cad, "part")

        parting = await cad.ok("inspect", action="parting", pull=[0, 0, 1], body="shrunk")
        assert parting.splitlines()[0].startswith("parting against the pull (0, 0, 1): 1 loop(s), 0 group(s) of walls")
        assert "loop 1 (outer): 4 edges" in parting and "planar, at" in parting
        assert "0 undercut region(s)" in parting

        split = await cad.ok(
            "op",
            name="mould_split",
            id="inserts",
            intent="cavity and core of the ribbed shell",
            params={"source": _body("shrunk"), "pull": "Z", "margin_mm": 25},
        )
        _record(tmp_path, "shell_split", split)
        assert "a plane through the line" in split and "leave: core ok" in split and "cavity ok" in split
        assert _gap(split) < 1e-4

        await _pictures(cad, tmp_path, "shell", ["body:3", "body:11"], "body:12", ["body:13"])


async def test_a_rim_that_steps_gets_a_run_off_surface_and_the_halves_still_add_up(
    workspace: Path, tmp_path: Path
) -> None:
    async with running_server(workspace, visible=True) as (cad, _):
        await cad.ok("doc", action="new", name="stepped")
        await _shell(cad)
        await cad.ok(
            "op",
            name="box",
            id="notch",
            intent="a 20 mm wide, 6 mm deep notch through the rim of the +X wall",
            params={"size": [10, 20, 8], "placement": {"origin": [35, -10, 24]}},
        )
        await cad.ok(
            "op",
            name="boolean_subtract",
            id="part",
            intent="the notch cut in the rim",
            params={"target": _body("shell_part"), "tools": [_body("notch")]},
        )
        await _shrink_and_look(cad, "part")

        asked = await cad.ok("inspect", action="parting", pull=[0, 0, 1], body="shrunk")
        assert "DECIDE:" in asked and "id=" in asked  # the notch's sides have no draft and touch both halves
        stepped = await cad.ok("inspect", action="parting", pull=[0, 0, 1], body="shrunk", id="core")
        assert "steps across heights" in stepped and "0 group(s) of walls to decide" in stepped

        refused = await cad.err(
            "op",
            name="mould_split",
            id="inserts",
            intent="cavity and core of the notched shell",
            params={"source": _body("shrunk"), "pull": "Z", "margin_mm": 25},
        )
        assert "zero_draft_to=core or cavity" in refused  # the engineer decides, the tool does not guess

        split = await cad.ok(
            "op",
            name="mould_split",
            id="inserts",
            intent="cavity and core of the notched shell",
            params={"source": _body("shrunk"), "pull": "Z", "margin_mm": 25, "zero_draft_to": "core"},
        )
        _record(tmp_path, "stepped_split", split)
        assert "a run-off surface" in split and "leave: core ok" in split
        assert _gap(split) < 1e-4
        await _pictures(cad, tmp_path, "stepped", ["body:3", "body:6"], "body:7", ["body:8"])


async def test_a_hole_across_the_pull_asks_for_a_slide_and_the_slide_leaves_sideways(
    workspace: Path, tmp_path: Path
) -> None:
    async with running_server(workspace, visible=True) as (cad, _):
        await cad.ok("doc", action="new", name="hole")
        await _shell(cad)
        await cad.ok(
            "op",
            name="cylinder",
            id="entry",
            intent="a cable entry through the +X wall",
            params={"diameter_mm": 8, "height_mm": 20, "axis": "X", "origin": [30, 0, 16]},
        )
        await cad.ok(
            "op",
            name="boolean_subtract",
            id="part",
            intent="the cable entry",
            params={"target": _body("shell_part"), "tools": [_body("entry")]},
        )
        await _shrink_and_look(cad, "part")

        parting = await cad.ok("inspect", action="parting", pull=[0, 0, 1], body="shrunk")
        assert "1 undercut region(s)" in parting
        assert "needs a SLIDE along (1, 0, 0)" in parting  # out through the wall, along the axis of the hole

        trapped = await cad.err(
            "op",
            name="mould_split",
            id="inserts",
            intent="cavity and core with the cable entry",
            params={"source": _body("shrunk"), "pull": "Z", "margin_mm": 25},
        )
        assert "need a slide: the part would be trapped" in trapped

        split = await cad.ok(
            "op",
            name="mould_split",
            id="inserts",
            intent="cavity and core with the cable entry",
            params={"source": _body("shrunk"), "pull": "Z", "margin_mm": 25, "slides": "all"},
        )
        _record(tmp_path, "slide_split", split)
        assert "slide 1" in split and "leave: core ok" in split and "slide 1 ok" in split
        assert _gap(split) < 1e-4
        await _pictures(cad, tmp_path, "slide", ["body:3", "body:6"], "body:7", ["body:8", "body:9"])
