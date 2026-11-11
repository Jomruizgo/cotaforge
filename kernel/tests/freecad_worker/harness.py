"""Helpers shared by the check modules: dispatch shortcuts, tolerant comparisons, reference part builders."""

import itertools
import math
import os
import tempfile
from typing import Any

import main
from protocol import WorkerError

TOL = 1e-6

# Dual-version support means the worker suite must accept the hello's
# ``freecad`` field on either supported line, not just the one that happened to be installed when a test
# was written.
SUPPORTED_FREECAD_LINES = ("1.0", "1.1")


def is_supported_freecad_version(freecad: str) -> bool:
    return freecad.startswith(SUPPORTED_FREECAD_LINES)


def call(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return main.dispatch(method, params)


def op(operation_id: str, **fields: Any) -> dict[str, Any]:
    fields["operation_id"] = operation_id
    return fields


def expect_error(code: str, method: str, params: dict[str, Any]) -> WorkerError:
    try:
        main.dispatch(method, params)
    except WorkerError as exc:
        assert exc.code == code, f"{method}: expected {code}, got {exc.code}: {exc.message}"
        return exc
    raise AssertionError(f"{method} did not fail with {code}")


def close(a: float, b: float, tol: float = 1e-3) -> bool:
    return abs(a - b) <= tol


def vclose(a: Any, b: Any, tol: float = 1e-6) -> bool:
    return all(abs(float(x) - float(y)) <= tol for x, y in zip(a, b, strict=True))


def parallel(a: Any, b: Any, either_sense: bool = False) -> bool:
    d = sum(float(x) * float(y) for x, y in zip(a, b, strict=True))
    return d > 1 - 1e-6 or (either_sense and d < -1 + 1e-6)


def mass() -> dict[str, Any]:
    return call("mass_properties", op("q"))


def bbox() -> dict[str, Any]:
    return call("bounding_box", op("q"))


def volume() -> float:
    return float(mass()["volume_mm3"])


def face_info(handle: dict[str, str]) -> dict[str, Any]:
    return call("face_info", op("q", face=handle))


def edge_info(handle: dict[str, str]) -> dict[str, Any]:
    return call("edge_info", op("q", edge=handle))


def faces_of(feature: dict[str, str], role: str = "any") -> list[dict[str, Any]]:
    return call("faces_of", op("q", feature=feature, role=role))["faces"]


def select_face(faces: list[dict[str, Any]], **sel: Any) -> dict[str, Any]:
    """Executor-like selection: normal (parallel same sense), surface, center (1e-2), area_min."""
    found = []
    for f in faces:
        if "normal" in sel and (f["normal"] is None or not parallel(f["normal"], sel["normal"])):
            continue
        if "surface" in sel and f["surface"] != sel["surface"]:
            continue
        if "center" in sel and not vclose(f["center"], sel["center"], 1e-2):
            continue
        if "area_min" in sel and f["area_mm2"] < sel["area_min"]:
            continue
        found.append(f)
    assert len(found) == 1, f"selector {sel} matched {len(found)} faces"
    return found[0]


def rect(name: str, center: tuple[float, float], width: float, height: float) -> dict[str, Any]:
    return {"kind": "rect", "name": name, "center": list(center), "width": width, "height": height}


def square(name: str, center: tuple[float, float], side: float) -> dict[str, Any]:
    return {"kind": "square", "name": name, "center": list(center), "side": side}


def circle(name: str, center: tuple[float, float], diameter: float) -> dict[str, Any]:
    return {"kind": "circle", "name": name, "center": list(center), "diameter": diameter}


def line(name: str, start: tuple[float, float], end: tuple[float, float]) -> dict[str, Any]:
    return {"kind": "line", "name": name, "start": list(start), "end": list(end)}


def sketch(operation_id: str, on: Any, entities: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return call(
        "sketch",
        op(
            operation_id,
            on=on,
            entities=entities,
            constraints=extra.get("constraints", []),
            named_dimensions=extra.get("named_dimensions", []),
        ),
    )


def extrude(operation_id: str, sk: dict[str, Any], depth: float, **extra: Any) -> dict[str, Any]:
    params = op(
        operation_id,
        sketch=sk["sketch"],
        depth_mm=depth,
        direction="positive",
        both_directions=False,
        depth2_mm=None,
        merge=True,
    )
    params.update(extra)
    return call("extrude", params)


def new_document(name: str, kind: str = "part") -> dict[str, Any]:
    return call("new_document", op("new", kind=kind, name=name))


_LINK_DIR = tempfile.TemporaryDirectory(prefix="cotaforge-checks-")
_LINK_FILES = itertools.count(1)


def linkable(document: dict[str, Any]) -> dict[str, Any]:
    """Give a document a file, which an assembly needs of itself and of every part it links to (a component is an
    ``App::Link`` to the part's file): saved under a temporary folder of the checks."""
    doc = main.WORKER.documents[document["handle"]["id"]]
    doc.fc_doc.saveAs(os.path.join(_LINK_DIR.name, f"{next(_LINK_FILES)}_{doc.name}.FCStd"))
    return document


# --- reference parts (the six functional references, driven exactly like the executor would) -------------------------


def build_part01() -> dict[str, Any]:
    """Plate 100x60x8 with a through hole at world (10, 10) patterned 2x2 on 80x40."""
    new_document("plate")
    sk = sketch(
        "base_sketch",
        "XY",
        [rect("outline", (50, 30), 100, 60)],
        named_dimensions=[{"name": "plate_width", "entity": "outline", "attribute": "width"}],
    )
    base = extrude("base", sk, 8)
    top = select_face(faces_of(base["feature"]), normal=(0, 0, 1))
    hole = call(
        "hole",
        op(
            "hole_1",
            face=top["handle"],
            position=[-40, -20],
            standard="iso_clearance",
            size="M6",
            diameter_mm=6.6,
            end_condition="through_all",
            depth_mm=None,
        ),
    )
    holes = call(
        "linear_pattern",
        op(
            "holes",
            features=[hole["feature"]],
            direction=[1, 0, 0],
            spacing_mm=80,
            count=2,
            direction2=[0, 1, 0],
            spacing2_mm=40,
            count2=2,
        ),
    )
    return {"sketch": sk, "base": base, "top": top, "hole": hole, "holes": holes}


def build_part02() -> dict[str, Any]:
    """Stepped shaft revolved 360 about the sketch u axis (world X)."""
    new_document("shaft")
    sk = sketch(
        "profile",
        "XY",
        [
            line("axis_seg", (0, 0), (40, 0)),
            line("end_small", (40, 0), (40, 6)),
            line("seat", (40, 6), (20, 6)),
            line("shoulder", (20, 6), (20, 10)),
            line("journal", (20, 10), (0, 10)),
            line("end_big", (0, 10), (0, 0)),
        ],
    )
    frame = sk["frame"]
    body = call(
        "revolve",
        op("body", sketch=sk["sketch"], axis={"origin": frame["origin"], "direction": frame["u"]}, angle_deg=360.0),
    )
    return {"sketch": sk, "body": body}


def build_part03() -> dict[str, Any]:
    """L-bracket: extrude 20, pocket on the end face, R5 fillet on the concave edge, 3x2 chamfer."""
    new_document("bracket")
    sk = sketch(
        "l_sketch",
        "XY",
        [
            line("bottom", (0, 0), (60, 0)),
            line("end", (60, 0), (60, 10)),
            line("base_top", (60, 10), (10, 10)),
            line("upright_side", (10, 10), (10, 40)),
            line("top", (10, 40), (0, 40)),
            line("back", (0, 40), (0, 0)),
        ],
    )
    base = extrude("base", sk, 20)
    faces = faces_of(base["feature"])
    end_face = select_face(faces, normal=(1, 0, 0), center=(60, 5, 10))
    psk = sketch("pocket_sketch", end_face["handle"], [rect("pocket", (0, 0), 6, 10)])
    pocket = call(
        "cut",
        op(
            "pocket",
            sketch=psk["sketch"],
            end_condition="blind",
            depth_mm=5,
            direction="negative",
            both_directions=False,
            depth2_mm=None,
        ),
    )
    faces = faces_of(base["feature"])
    fa = select_face(faces, normal=(0, 1, 0), center=(35, 10, 10))
    fb = select_face(faces, normal=(1, 0, 0), center=(10, 25, 10))
    inner = call("edges_between", op("q", a=fa["handle"], b=fb["handle"]))["edges"]
    assert len(inner) == 1, inner
    fillet = call("fillet", op("inner_fillet", edges=[inner[0]["handle"]], radius_mm=5))
    faces = faces_of(base["feature"])
    top = select_face(faces, normal=(0, 0, 1))
    front = select_face(faces, normal=(0, -1, 0))
    outer = call("edges_between", op("q", a=top["handle"], b=front["handle"]))["edges"]
    assert len(outer) == 1, outer
    chamfer = call(
        "chamfer",
        op("lead_in", edges=[outer[0]["handle"]], distance1_mm=3, distance2_mm=2, distance1_face=top["handle"]),
    )
    return {
        "base": base,
        "psk": psk,
        "pocket": pocket,
        "inner": inner[0],
        "fillet": fillet,
        "outer": outer[0],
        "chamfer": chamfer,
        "top": top,
        "front": front,
        "end_face": end_face,
    }


def build_part04() -> dict[str, Any]:
    """Flange: disc D120 x 10, bore D30, bolt hole D9 at (45, 0) patterned x6 about the bore axis."""
    new_document("flange")
    sk = sketch("disc_sketch", "XY", [circle("outline", (0, 0), 120)])
    disc = extrude("disc", sk, 10)
    top = select_face(faces_of(disc["feature"]), normal=(0, 0, 1))
    bore = call(
        "hole",
        op(
            "bore",
            face=top["handle"],
            position=[0, 0],
            standard="simple",
            size=None,
            diameter_mm=30,
            end_condition="through_all",
            depth_mm=None,
        ),
    )
    bolt = call(
        "hole",
        op(
            "bolt_1",
            face=top["handle"],
            position=[45, 0],
            standard="iso_clearance",
            size="M8",
            diameter_mm=9,
            end_condition="through_all",
            depth_mm=None,
        ),
    )
    bore_face = select_face(faces_of(bore["feature"], "created"), surface="cylinder")
    bolts = call(
        "circular_pattern",
        op("bolts", features=[bolt["feature"]], axis=bore_face["axis"], count=6, angle_deg=360.0, equal_spacing=True),
    )
    return {"disc": disc, "top": top, "bore": bore, "bolt": bolt, "bore_face": bore_face, "bolts": bolts}


IDENTITY = {"origin": [0, 0, 0], "rotation": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]}


def build_part05() -> dict[str, Any]:
    """Concept block 100x60x20 minus a D30 cylinder, translated +10 along X."""
    new_document("block")
    body = call("box", op("body", size=[100, 60, 20], transform=IDENTITY))
    tool = call(
        "cylinder",
        op("pocket_tool", diameter_mm=30, height_mm=20, axis={"origin": [50, 30, 0], "direction": [0, 0, 1]}),
    )
    tool_faces = faces_of(tool["feature"])
    pocket = call(
        "boolean", op("pocket", kind="subtract", target=body["created_bodies"][0], tools=[tool["created_bodies"][0]])
    )
    place = call(
        "move_body",
        op("place", body=body["created_bodies"][0], transform={"origin": [10, 0, 0], "rotation": IDENTITY["rotation"]}),
    )
    return {"body": body, "tool": tool, "tool_faces": tool_faces, "pocket": pocket, "place": place}


def build_part06() -> dict[str, Any]:
    """Block with a D6.5 bore + D6 x 40 pin, assembled with a concentric and a coincident mate."""
    block_doc = new_document("block")
    sk = sketch("block_sketch", "XY", [rect("outline", (20, 20), 40, 40)])
    block_body = extrude("block_body", sk, 20)
    top = select_face(faces_of(block_body["feature"]), normal=(0, 0, 1))
    bore = call(
        "hole",
        op(
            "bore",
            face=top["handle"],
            position=[0, 0],
            standard="simple",
            size=None,
            diameter_mm=6.5,
            end_condition="through_all",
            depth_mm=None,
        ),
    )
    linkable(block_doc)
    pin_doc = new_document("pin")
    psk = sketch("pin_sketch", "XY", [circle("section", (0, 0), 6)])
    pin_body = extrude("pin_body", psk, 40)
    linkable(pin_doc)
    asm = linkable(new_document("asm", "assembly"))
    ins_block = call("insert_component", op("ins_block", part=block_doc["handle"], transform=IDENTITY))
    fix = call("fix_component", op("fix_block", component=ins_block["component"]))
    ins_pin = call(
        "insert_component",
        op("ins_pin", part=pin_doc["handle"], transform={"origin": [100, 0, 0], "rotation": IDENTITY["rotation"]}),
    )
    a = select_face(faces_of(ins_block["feature"]), surface="cylinder")
    b = select_face(faces_of(ins_pin["feature"]), surface="cylinder")
    axis_mate = call(
        "mate_concentric", op("axis_mate", a=a["handle"], b=b["handle"], alignment="anti_aligned", flip=False)
    )
    a2 = select_face(faces_of(ins_block["feature"]), normal=(0, 0, 1), area_min=1000)
    b2 = select_face(faces_of(ins_pin["feature"]), normal=(0, 0, 1))
    seat_mate = call(
        "mate_coincident", op("seat_mate", a=a2["handle"], b=b2["handle"], alignment="aligned", flip=False)
    )
    clearance = call("check_interference", op("clearance", components=None))
    return {
        "block_doc": block_doc,
        "pin_doc": pin_doc,
        "asm": asm,
        "bore": bore,
        "pin_body": pin_body,
        "ins_block": ins_block,
        "fix": fix,
        "ins_pin": ins_pin,
        "axis_mate": axis_mate,
        "seat_mate": seat_mate,
        "clearance": clearance,
        "a": a,
        "b": b,
        "a2": a2,
        "b2": b2,
    }


PI = math.pi
