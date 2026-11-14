"""Mould components from the DME catalogue and the mechanism of a slide, through the real stdio server and FreeCAD.

Parts are made of the CAD's own sketches, pads, pockets and revolutions (no loose shapes), each carries its catalogue
reference, and the slide mechanism is refused unless the stroke clears the undercut, the heel is steeper than the pin
and nothing overlaps closed or open. Set ``COTAFORGE_TEST_SHOTS`` to a folder to keep the pictures (closed and open).
Runs in a VISIBLE FreeCAD window.
"""

import re
from pathlib import Path

from tests.integration.conftest import running_server
from tests.integration.test_mould_split_real import _body, _picture, _record, _shell, _shots, _shrink_and_look

SHOW_MECHANISM = """
keep = ('mech', 'inserts_slide', 'shrunk')
for o in doc.Objects:
    view = getattr(o, 'ViewObject', None)
    if view is None or o.TypeId in ('Sketcher::SketchObject',) or o.InList and o.TypeId != 'PartDesign::Body':
        continue
    view.Visibility = o.Label.startswith(keep)
    if o.Label == 'shrunk':
        view.Transparency = 70
"""

MOVE_TO_OPEN = """
import FreeCAD as App
def shift(label, v):
    for o in doc.Objects:
        if o.Label == label:
            o.Placement = App.Placement(o.Placement.Base + App.Vector(*v), o.Placement.Rotation)
shift('inserts_slide_1', {slide!r})
shift('mech', {slide!r})
shift('mech_angle_pin', {pin!r})
shift('mech_locking_heel', {pin!r})
"""


def _num(text: str, pattern: str) -> float:
    found = re.search(pattern, text)
    assert found, (pattern, text)
    return float(found.group(1))


def _ids(text: str) -> dict[str, str]:
    """``role=body:N`` pairs of a step's reply."""
    return dict(re.findall(r"(\w+)=(body:\d+)", text))


async def test_a_two_plate_base_kit_is_native_and_carries_its_catalogue_references(
    workspace: Path, tmp_path: Path
) -> None:
    async with running_server(workspace, visible=True) as (cad, _):
        await cad.ok("doc", action="new", name="base")
        refused = await cad.err(
            "op",
            name="mould_component",
            id="bad",
            intent="a pin that is not in the catalogue",
            params={"kind": "guide_pin", "ref": "9999GL"},
        )
        assert "not a DME guide pin" in refused  # the message says what is (the reply may cut it short)
        kit = await cad.ok(
            "op",
            name="mould_component",
            id="base",
            intent="the mould base of the test mould",
            params={"kind": "mould_base", "size": "1012", "at": [{"origin": [0, 0, 30], "axis": "Z"}]},
        )
        _record(tmp_path, "base_kit", kit)
        ids = _ids(kit)
        assert len(ids) == 23, kit  # nine plates, 4 pins, 4 bushes, 4 return pins, the ring and the sprue bushing
        assert "XPA1012-17-17-35" in kit and "5101GL" in kit and "6501" in kit and "B0271" in kit
        pins = await cad.ok(
            "op",
            name="mould_component",
            id="eject",
            intent="two ejector pins of the test mould",
            params={"kind": "ejector_pin", "size": "3x100", "at": [{"origin": [10, 0, 30]}, {"origin": [30, 0, 30]}]},
        )
        assert "EJP-EHN-030-0100" in pins
        bom = await cad.ok("inspect", action="bom")
        assert "XPA1012-17-17-35" in bom and "DME" in bom, bom  # the catalogue reference reaches the bill of materials
        _record(tmp_path, "base_bom", bom)
        shots = _shots(tmp_path)
        await cad.ok("view", action="set_view", name="iso")
        await _picture(cad, shots, "base_kit")


async def test_a_slide_gets_its_mechanism_and_it_works_closed_and_open(workspace: Path, tmp_path: Path) -> None:
    async with running_server(workspace, visible=True) as (cad, _):
        await cad.ok("doc", action="new", name="slide")
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
        split = await cad.ok(
            "op",
            name="mould_split",
            id="inserts",
            intent="cavity and core with the cable entry",
            params={"source": _body("shrunk"), "pull": "Z", "margin_mm": 25, "slides": "all"},
        )
        _record(tmp_path, "slide_split", split)
        # a heel as steep as the pin is refused, and nothing is left behind
        bad = await cad.err(
            "op",
            name="slide_mechanism",
            id="mech",
            intent="a heel that is not steeper",
            params={
                "split": "inserts",
                "slide": 1,
                "part": _body("shrunk"),
                "pull": "Z",
                "angle_deg": 15,
                "wedge_extra_deg": 0,
            },
        )
        assert "wedge_extra_deg" in bad, bad
        # a stroke that does not clear the undercut is refused too
        short = await cad.err(
            "op",
            name="slide_mechanism",
            id="mech",
            intent="a stroke too short",
            params={
                "split": "inserts",
                "slide": 1,
                "part": _body("shrunk"),
                "pull": "Z",
                "angle_deg": 15,
                "stroke_mm": 0.5,
            },
        )
        assert "does not clear the undercut" in short, short
        mech = await cad.ok(
            "op",
            name="slide_mechanism",
            id="mech",
            intent="the mechanism of the cable-entry slide",
            params={"split": "inserts", "slide": 1, "part": _body("shrunk"), "pull": "Z", "angle_deg": 15},
        )
        _record(tmp_path, "slide_mechanism", mech)
        assert "no interference" in mech and "heel" in mech and "stroke" in mech, mech
        ids = _ids(mech)
        assert {"carrier", "gib_right", "gib_left", "angle_pin", "locking_heel", "wear_plate", "detent_ball"} <= set(
            ids
        )
        shots = _shots(tmp_path)
        stroke = _num(mech, r"stroke ([\d.]+) mm \(clears")
        opening = _num(mech, r"opening travel ([\d.]+) mm")
        await cad.ok("run_native", code=SHOW_MECHANISM, intent="show only the slide and its mechanism")
        for view in ("iso", "front"):
            await cad.ok("view", action="set_view", name=view)
            await _picture(cad, shots, f"slide_closed_{view}")
        # the open position: the slide and its carrier go out by the stroke, the pin half up by the opening travel
        await cad.ok(
            "run_native",
            code=MOVE_TO_OPEN.format(slide=(stroke, 0.0, 0.0), pin=(0.0, 0.0, opening)),
            intent="show the open position (for the picture only)",
        )
        for view in ("iso", "front"):
            await cad.ok("view", action="set_view", name=view)
            await _picture(cad, shots, f"slide_open_{view}")
