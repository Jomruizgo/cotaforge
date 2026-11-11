"""Feature ops on moved bodies: a PartDesign body keeps its pose in Body.Placement (world-frame values written into
body-local objects must have it taken out), a Part body is baked into world geometry when it becomes PartDesign."""

import math

from harness import (
    IDENTITY,
    bbox,
    call,
    close,
    extrude,
    face_info,
    faces_of,
    new_document,
    op,
    rect,
    select_face,
    sketch,
    vclose,
    volume,
)

PI = math.pi
PLATE = 100 * 60 * 8
ROT90X = [[1, 0, 0], [0, 0, -1], [0, 1, 0]]
C30, S30 = math.cos(math.radians(30)), math.sin(math.radians(30))
ROT30Z = [[C30, -S30, 0], [S30, C30, 0], [0, 0, 1]]


def _plate() -> dict:
    sk = sketch("s0", "XY", [rect("r", (50, 30), 100, 60)])
    return extrude("base", sk, 8)


def _move(body: dict, origin: list, rotation: list) -> None:
    call("move_body", op("mv", body=body["created_bodies"][0], transform={"origin": origin, "rotation": rotation}))


def _boss_on(base: dict, normal: tuple, depth: float = 5) -> None:
    top = select_face(faces_of(base["feature"]), normal=normal)
    sk = sketch("bs", top["handle"], [rect("boss", (0, 0), 20, 10)])
    extrude("boss", sk, depth)


def check_boss_on_a_translated_partdesign_body() -> None:
    new_document("mv_t")
    base = _plate()
    _move(base, [0, 100, 0], IDENTITY["rotation"])
    _boss_on(base, (0, 0, 1))
    assert close(volume(), PLATE + 1000, 1e-6), volume()
    box = bbox()
    assert vclose(box["min"], (0, 100, 0), 1e-9) and vclose(box["max"], (100, 160, 13), 1e-9), box


def check_boss_on_a_rotated_partdesign_body() -> None:
    new_document("mv_r")
    base = _plate()
    _move(base, [0, 0, 0], ROT90X)  # the plate's +Z face now looks along -Y
    _boss_on(base, (0, -1, 0))
    assert close(volume(), PLATE + 1000, 1e-6), volume()
    box = bbox()  # plate: y in [-8, 0], z in [0, 60]; the boss stands 5 mm further out along -Y
    assert vclose(box["min"], (0, -13, 0), 1e-9) and vclose(box["max"], (100, 0, 60), 1e-9), box


def check_boss_on_an_obliquely_rotated_partdesign_body() -> None:
    new_document("mv_o")
    base = _plate()
    _move(base, [0, 0, 0], ROT30Z)
    _boss_on(base, (0, 0, 1))
    assert close(volume(), PLATE + 1000, 1e-6), volume()
    boss_faces = faces_of(call("feature_tree", op("q"))["nodes"][-1]["handle"])
    cap = select_face(boss_faces, normal=(0, 0, 1), center=(50 * C30 - 30 * S30, 50 * S30 + 30 * C30, 13.0))
    assert close(cap["area_mm2"], 200.0, 1e-6), cap  # the 20 x 10 boss cap, 13 mm above the moved plate's underside


def check_hole_and_cut_on_a_moved_partdesign_body_keep_the_axis_sense() -> None:
    new_document("mv_h")
    base = _plate()
    _move(base, [0, 0, 0], [[1, 0, 0], [0, -1, 0], [0, 0, -1]])  # 180 about X: the top face looks along -Z
    top = select_face(faces_of(base["feature"]), normal=(0, 0, -1))
    hole = call(
        "hole",
        op(
            "bore",
            face=top["handle"],
            position=[0, 0],
            standard="simple",
            diameter_mm=10,
            end_condition="through_all",
        ),
    )
    assert close(volume(), PLATE - PI * 25 * 8, 1e-6), volume()
    wall = select_face(faces_of(hole["feature"]), surface="cylinder")
    assert vclose(wall["axis"]["direction"], (0, 0, 1), 1e-6), wall["axis"]  # into the material: -normal = +Z


def check_hole_on_the_original_face_ref_after_a_part_body_moved() -> None:
    """The bake keeps the face identity: the id taken BEFORE the move still resolves, in the moved place."""
    new_document("mv_p")
    box = call("box", op("b", size=[10, 20, 30], transform=IDENTITY))
    top = select_face(faces_of(box["feature"]), normal=(0, 0, 1))
    before = face_info(top["handle"])
    call(
        "move_body",
        op("mv", body=box["created_bodies"][0], transform={"origin": [0, 100, 0], "rotation": IDENTITY["rotation"]}),
    )
    assert vclose(face_info(top["handle"])["center"], (5, 110, 30), 1e-9)
    hole = call(
        "hole",
        op("bore", face=top["handle"], position=[0, 0], standard="simple", diameter_mm=6, end_condition="through_all"),
    )
    assert close(volume(), 6000 - PI * 9 * 30, 1e-6), volume()
    assert vclose(face_info(top["handle"])["center"], (5, 110, 30), 1e-9)
    assert close(face_info(top["handle"])["area_mm2"], before["area_mm2"] - PI * 9, 1e-6)  # only the bore was cut
    assert vclose(select_face(faces_of(hole["feature"]), surface="cylinder")["center"], (5, 110, 15), 1e-6)


def check_extrude_on_a_rotated_part_body() -> None:
    new_document("mv_pr")
    box = call("box", op("b", size=[10, 20, 30], transform=IDENTITY))
    call(
        "move_body",
        op(
            "mv",
            body=box["created_bodies"][0],
            transform={"origin": [0, 0, 0], "rotation": [[0, -1, 0], [1, 0, 0], [0, 0, 1]]},
        ),
    )
    top = select_face(faces_of(box["feature"]), normal=(0, 0, 1))
    sk = sketch("bs", top["handle"], [rect("boss", (0, 0), 4, 4)])
    extrude("boss", sk, 5)
    assert close(volume(), 6000 + 80, 1e-6), volume()
    assert vclose(bbox()["min"], (-20, 0, 0), 1e-9), bbox()


def check_unmoved_bodies_take_the_old_path() -> None:
    new_document("mv_u")
    base = _plate()
    _boss_on(base, (0, 0, 1))
    assert close(volume(), PLATE + 1000, 1e-6), volume()
    box = bbox()
    assert vclose(box["min"], (0, 0, 0), 1e-9) and vclose(box["max"], (100, 60, 13), 1e-9), box
    new_document("mv_u2")
    part = call("box", op("b", size=[10, 20, 30], transform=IDENTITY))
    top = select_face(faces_of(part["feature"]), normal=(0, 0, 1))
    extrude("boss", sketch("bs", top["handle"], [rect("boss", (0, 0), 4, 4)]), 5)
    assert close(volume(), 6000 + 80, 1e-6) and vclose(bbox()["max"], (10, 20, 35), 1e-9), bbox()
