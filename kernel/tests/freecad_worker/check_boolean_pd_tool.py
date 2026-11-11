"""boolean_subtract with ``keep_tools`` and a PartDesign TOOL: a featured part cut out of a Part block (the core/cavity
move). The cut uses a ToolCopy of the tool's placed shape, so the tool itself survives untouched wherever it stands.
A PartDesign tool without ``keep_tools``, and a PartDesign target, stay refused. Run on FreeCAD 1.0.2 and 1.1.3."""

import math
from typing import Any

import geometry as g
from harness import (
    IDENTITY,
    call,
    close,
    expect_error,
    extrude,
    faces_of,
    new_document,
    op,
    parallel,
    rect,
    select_face,
    sketch,
    vclose,
    volume,
)

PLATE_VOLUME = 48000.0
HOLE_VOLUME = math.pi * 9.0 * 8.0
PART_VOLUME = PLATE_VOLUME - HOLE_VOLUME
ROT90Z = [[0, -1, 0], [1, 0, 0], [0, 0, 1]]


def _mass(handle: dict[str, str]) -> dict[str, Any]:
    return call("mass_properties", op("q", body=handle))


def _part() -> dict[str, str]:
    """100 x 60 x 8 PartDesign plate with a d6 through hole at (10, 10): the featured part."""
    sk = sketch("s0", "XY", [rect("r", (50, 30), 100, 60)])
    base = extrude("base", sk, 8)
    top = select_face(faces_of(base["feature"]), normal=(0, 0, 1))
    call(
        "hole",
        op(
            "h",
            face=top["handle"],
            position=[-40.0, -20.0],
            standard="simple",
            size=None,
            diameter_mm=6,
            end_condition="through_all",
            depth_mm=None,
        ),
    )
    return base["created_bodies"][0]


def _block(
    origin: tuple[float, float, float] = (-10, -10, -10), size: tuple[float, float, float] = (120, 80, 28)
) -> dict:
    return call(
        "box", op("blk", size=list(size), transform={"origin": list(origin), "rotation": IDENTITY["rotation"]})
    )["created_bodies"][0]


def _subtract(target: dict, tool: dict, keep_tools: bool = True, operation_id: str = "cavity") -> dict[str, Any]:
    return call("boolean", op(operation_id, kind="subtract", target=target, tools=[tool], keep_tools=keep_tools))


def check_boolean_a_partdesign_tool_with_keep_tools_cuts_a_cavity_and_survives() -> None:
    new_document("bp_keep")
    part = _part()
    block = _block()
    block_volume = 120 * 80 * 28
    assert close(volume(), block_volume + PART_VOLUME, 1e-6)
    result = _subtract(block, part)
    assert result["rebuild_ok"] is True, result
    # the block loses what the part occupies; the pin left standing inside the part's hole stays with the block
    assert close(_mass(block)["volume_mm3"], block_volume - PART_VOLUME, 1e-6), _mass(block)
    assert close(_mass(part)["volume_mm3"], PART_VOLUME, 1e-6)  # the tool is untouched and still a live body
    assert close(volume(), block_volume, 1e-6)  # cavity + part fill the block exactly
    assert call("is_valid", op("q"))["valid"] is True
    assert len(faces_of(result["feature"], "created")) > 0


def check_boolean_a_moved_partdesign_tool_is_cut_where_it_stands() -> None:
    new_document("bp_moved")
    part = _part()
    call("move_body", op("mv", body=part, transform={"origin": [30, 10, 5], "rotation": IDENTITY["rotation"]}))
    block = _block(origin=(0, 0, 0), size=(150, 90, 30))
    block_volume = 150 * 90 * 30
    _subtract(block, part)
    assert close(_mass(block)["volume_mm3"], block_volume - PART_VOLUME, 1e-6)
    tool_mass = _mass(part)
    expected = [
        (block_volume * 1.0 * c_b - PART_VOLUME * c_t) / (block_volume - PART_VOLUME)
        for c_b, c_t in zip((75.0, 45.0, 15.0), tool_mass["centroid"], strict=True)
    ]
    assert vclose(_mass(block)["centroid"], expected, 1e-6), (_mass(block)["centroid"], expected)


def check_boolean_a_rotated_partdesign_tool_is_cut_where_it_stands() -> None:
    new_document("bp_rot")
    part = _part()
    call("move_body", op("mv", body=part, transform={"origin": [110, 0, 4], "rotation": ROT90Z}))
    box = call("bounding_box", op("q", body=part))
    block = _block(origin=(box["min"][0] - 5, box["min"][1] - 5, 0), size=(120, 120, 20))
    block_volume = 120 * 120 * 20
    _subtract(block, part)
    assert close(_mass(block)["volume_mm3"], block_volume - PART_VOLUME, 1e-6)
    assert close(_mass(part)["volume_mm3"], PART_VOLUME, 1e-6)


def check_boolean_the_pin_inside_the_tools_hole_keeps_the_holes_axis_sense() -> None:
    new_document("bp_axis")
    part = _part()
    block = _block()
    result = _subtract(block, part)
    pins = [f for f in faces_of(result["feature"], "created") if f["surface"] == "cylinder"]
    assert len(pins) == 1 and close(pins[0]["radius"], 3.0, 1e-9), pins
    assert parallel(pins[0]["axis"]["direction"], (0, 0, -1)), pins[0]["axis"]  # the hole's intended sense


def check_boolean_a_partdesign_tool_is_refused_unless_it_survives() -> None:
    new_document("bp_refuse")
    part = _part()
    block = _block()
    failure = expect_error(
        "not_supported", "boolean", op("c", kind="subtract", target=block, tools=[part], keep_tools=False)
    )
    assert "keep_tools" in failure.message, failure.message
    expect_error("not_supported", "boolean", op("c", kind="union", target=block, tools=[part], keep_tools=False))
    expect_error("not_supported", "boolean", op("c", kind="subtract", target=part, tools=[block], keep_tools=True))
    expect_error("invalid_request", "boolean", op("c", kind="union", target=block, tools=[part], keep_tools=True))
    assert close(volume(), 120 * 80 * 28 + PART_VOLUME, 1e-6)  # nothing changed


def check_boolean_core_and_cavity_and_part_fill_the_block() -> None:
    """Block - part keeping the part, then split at the parting plane: cavity + core + part = block (the mould flow)."""
    new_document("bp_mould")
    part = _part()
    block = _block(origin=(-10, -10, -10), size=(120, 80, 38))  # z from -10 to 28; the part occupies 0..8
    block_volume = 120 * 80 * 38
    _subtract(block, part)
    plane = {"origin": [0, 0, 4], "u": [1, 0, 0], "v": [0, 1, 0], "n": [0, 0, 1]}  # through the part's mid-height
    up, down = call("split", op("sp", source=block, plane=plane, keep="both"))["created_bodies"]
    assert close(_mass(up)["volume_mm3"] + _mass(down)["volume_mm3"] + PART_VOLUME, block_volume, 1e-6)
    assert close(volume(), block_volume, 1e-6)
    assert call("is_valid", op("q"))["valid"] is True
    g_up = _mass(up)["centroid"]
    assert g.dot(g.sub(g_up, (0, 0, 4)), (0, 0, 1)) > 0
