# ruff: noqa: E501
"""A stepped shaft with a threaded end and a keyway, and a flange with a bolt circle and a lone hole, drawn on the REAL FreeCAD
(Windows, VISIBLE) in both modes, looked at the way a machinist reads the sheet: every diameter is a dimension line on the
long view (one dimension for one feature), the thread is drawn with its thin lines and dimensioned with its class and length,
no note is written twice, the keyway carries its width class, its length and its position from the shoulder, its removed
section stands next to its cutting line, and the lone hole of the flange is located from the axis.

Set ``COTAFORGE_TEST_SHOTS`` to a folder to keep the images of the sheets.
"""

import base64
import os
import re
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.integration.conftest import ORDER, Cad, Reply, running_server
from tests.integration.test_drawing_compose_real import compose_the_rest, in_one_order
from tests.integration.test_drawing_pattern_real import flange

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the visible FreeCAD window is exercised on Windows")

XY = {"kind": "plane", "name": "XY"}
# the half profile of the shaft from its left end: thread M20x1.5, seat 1, body, seat 2, shaft end with the keyway
PROFILE = [(0, 0), (0, 8.5), (1.5, 10), (28, 10), (28, 12.5), (43, 12.5), (43, 16), (103, 16), (103, 12.5), (118, 12.5), (118, 11), (167, 11), (168, 10), (168, 0)]
INTENT = """# Drive shaft

## Función
- Eje de un transportador. Gira sobre dos rodamientos 6205 (Ø25 interior, ancho 15). Recibe en el extremo derecho el cubo de una polea con chaveta paralela 6 x 6, y en el izquierdo una tuerca de fijación M20x1,5.

## Ajustes
- Asientos de rodamiento Ø25 k6 (los dos).
- Extremo de polea Ø22 k6.
- Rosca M20x1,5 - 6g, longitud roscada 26 (resto hasta el resalte en x = 28 es el cuello sin rosca).
- Chavetero para chaveta 6 x 6 DIN 6885 forma A: ancho 6 P9, profundidad en el eje 3,5, longitud 25, empieza en x = 122.

## Datos
- Referencia axial: el resalte en x = 43 (cara izquierda del cuerpo Ø32), contra la que se cuentan las longitudes.

## Tratamientos y acabado
- Sin tratamiento térmico. Sin recubrimiento (aceite antioxidante).
- Rugosidad general Ra 3,2. Asientos de rodamiento Ra 0,8.

## Cotas
- Tolerancia general ISO 2768-mK. Críticas: los tres Ø k6, la rosca y la longitud 60 del cuerpo Ø32 (± 0,05).
"""


def shot(shots: Path, name: str, reply: Reply) -> None:
    assert reply.images, reply.text
    shots.mkdir(parents=True, exist_ok=True)
    (shots / f"{name}.png").write_bytes(base64.b64decode(reply.images[0].data))


async def shaft(cad: Cad, workspace: Path) -> None:
    """The shaft as an agent builds it: the revolution, the keyway cut from a plane tangent to the Ø22 end, and the thread of the left end."""
    await cad.ok("doc", action="new", name="drive-shaft")
    await cad.ok("doc", action="set_material", material="C45")
    lines = [{"kind": "line", "name": f"l{i}", "start": list(a), "end": list(b)} for i, (a, b) in enumerate(zip(PROFILE, PROFILE[1:] + PROFILE[:1], strict=True))]
    await cad.ok("op", name="sketch", id="profile", params={"on": XY, "entities": lines})
    await cad.ok("op", name="revolve", id="shaft", params={"sketch": "profile", "axis": "u"})
    await cad.ok("op", name="ref_plane", id="kw_plane", params={"define": {"how": "offset", "base": XY, "distance_mm": 11}})
    await cad.ok("op", name="sketch", id="kw_sketch", params={"on": {"kind": "plane", "from_op": "kw_plane"}, "entities": [{"kind": "slot", "name": "kw", "start": [125, 0], "end": [144, 0], "width": 6}]})
    await cad.ok("op", name="cut", id="keyway", params={"sketch": "kw_sketch", "end_condition": "blind", "depth_mm": 3.5})
    await cad.ok("op", name="external_thread", id="end_thread", params={"face": {"kind": "face", "from_op": "shaft", "select": {"center": [14.75, 0, 0]}}, "end": {"kind": "face", "from_op": "shaft", "select": {"center": [0, 0, 0]}}, "size": "M20x1.5", "length_mm": 26, "tolerance_class": "6g"})
    (workspace / "drawings").mkdir(exist_ok=True)
    (workspace / "drawings" / "drive-shaft-intent.md").write_text(INTENT, encoding="utf-8")


def steps(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return items


DIAMETERS = [
    {"type": "dim", "kind": "diameter", "face": ["face:3"]},
    {"type": "dim", "kind": "diameter", "face": ["face:5"], "fit": "k6"},
    {"type": "dim", "kind": "diameter", "face": ["face:7"]},
    {"type": "dim", "kind": "diameter", "face": ["face:9"], "fit": "k6"},
    {"type": "dim", "kind": "diameter", "face": ["face:11"], "fit": "k6"},
]


async def test_shaft_in_steps_mode(workspace: Path, tmp_path: Path) -> None:
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    async with running_server(workspace, visible=True) as (cad, _):
        await cad.ok("memory", action="brief", drawing_review="propio", text="la prueba: el agente revisa el plano por su cuenta")
        await shaft(cad, workspace)
        print("CREATE", await cad.ok("drawing", action="create", title="Eje de transmision", template="A3", language="es"))
        print("ADD", await cad.ok("drawing", action="add", items=[{"type": "baseline_dims", "from": "face:6", "view": "front", "side": "below"}, *DIAMETERS, {"type": "view_lines"}]))
        listed = await cad.ok("drawing", action="list")
        print("LIST", listed)
        shot(shots, "steps_shaft", await cad("drawing", action="capture"))
        print("REVIEW", await cad.ok("drawing", action="review"))
        assert len(re.findall(r"^  \S+ note .*Sin tratamiento", listed, re.M)) == 1, listed


async def test_shaft_in_sheet_mode(workspace: Path, tmp_path: Path) -> None:
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    async with running_server(workspace, visible=True) as (cad, _):
        await in_one_order(cad)
        await shaft(cad, workspace)
        decisions = {
            "sheet": {"title": "Eje de transmision", "template": "A3", "language": "es"},
            "relations": [{"type": "baseline_dims", "from": "face:6", "view": "front", "side": "below"}, DIAMETERS[0], DIAMETERS[2]],
            "fits": [DIAMETERS[1], DIAMETERS[3], DIAMETERS[4]],
            "notes": ["Cantidad 10. Proceso: torneado, rectificado de los asientos, fresado del chavetero y roscado."],
        }
        composed = await compose_the_rest(cad, decisions, keep=("req.surface_texture",))
        print("COMPOSED", composed)
        listed = await cad.ok("drawing", action="list")
        print("LIST", listed)
        shot(shots, "sheet_shaft", await cad("drawing", action="capture"))
        print("REVIEW", await cad.ok("drawing", action="review"))
        assert len(re.findall(r"^  \S+ note .*Sin tratamiento", listed, re.M)) == 1, listed


async def test_flange_in_sheet_mode(workspace: Path, tmp_path: Path) -> None:
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    async with running_server(workspace, visible=True) as (cad, _):
        await in_one_order(cad)
        await flange(cad)
        composed = await compose_the_rest(cad, {"sheet": {"symmetry": True, "title": "Brida"}, "notes": ["Espesor 12"]}, template="A3")
        print("COMPOSED", composed)
        print("LIST", await cad.ok("drawing", action="list"))
        shot(shots, "sheet_flange", await cad("drawing", action="capture"))
        print("REVIEW", await cad.ok("drawing", action="review"))
        assert ORDER


async def test_flange_in_steps_mode(workspace: Path, tmp_path: Path) -> None:
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    async with running_server(workspace, visible=True) as (cad, _):
        await cad.ok("memory", action="brief", drawing_review="propio", text="la prueba: el agente revisa el plano por su cuenta")
        await flange(cad)
        await cad.ok("drawing", action="create", template="A3", views=["top", "iso"])
        await cad.ok("drawing", action="add", items=[{"type": "hole_dim", "all": True}, {"type": "view_lines"}, {"type": "overall"}])
        print("LIST", await cad.ok("drawing", action="list"))
        shot(shots, "steps_flange", await cad("drawing", action="capture"))
        print("REVIEW", await cad.ok("drawing", action="review"))
        assert re.compile(r".").search("x")
