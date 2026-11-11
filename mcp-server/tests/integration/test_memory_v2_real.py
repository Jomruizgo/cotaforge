"""Memory v2 against the real FreeCAD through the real server: the acceptance walkthrough. A design space declares the
sector, the engineer approved a principle with recognise / examples / triggers, and the agent's steps are scripted. The
whole transcript of what the agent sees is written to the file named by ``COTAFORGE_WALKTHROUGH`` (if set)."""

import json
import os
from pathlib import Path
from typing import Any

import pytest
from cotaforge.memory import MemoryEntry, MemoryStore, Scope

from tests.integration.conftest import Cad, running_server

PRINCIPLE: dict[str, Any] = {
    "id": "precision-del-ensamble",
    "type": "principle",
    "title": "No pedir una precisión que la pieza sola no puede garantizar",
    "applies_when": "Una pieza soldada o plegada lleva un patrón de agujeros con tolerancia fina",
    "not_when": "La pieza se mecaniza completa después de unirla",
    "recognize": "Una placa soldada en voladizo que lleva un patrón de agujeros con tolerancia ajustada",
    "typical_error": "Soldar la placa con el patrón tolerado y esperar que la soldadura conserve la posición",
    "when": {"roles": ["soldado"], "stages": ["detail"]},
    "examples": [
        {
            "sector": "estructuras",
            "text": "El soporte de un tanque: la posición fina de los apoyos se logra en obra, no al soldar.",
            "validated": False,
        },
        {
            "sector": "fixtures",
            "text": "La placa de ubicación se mecaniza después de soldar o se ubica con pasadores en el ensamble.",
            "validated": True,
        },
        {
            "sector": "chapa",
            "text": "Los agujeros de ubicación van en una pieza mecanizada, no en la chapa doblada.",
            "validated": False,
        },
        {"sector": "mobiliario", "text": "Un herraje de bisagra con patrón fino.", "validated": False},
    ],
}
BAD_PRINCIPLE: dict[str, Any] = {
    "id": "agujeros-de-festolux",
    "type": "principle",
    "title": "Los agujeros de los soportes Festolux X200 miden 19 mm",
    "applies_when": "Soportes de fixtures soldados",
    "not_when": "Cualquier otro caso",
    "recognize": "Un soporte con agujeros de 19 mm",
    "examples": [{"sector": "fixtures", "text": "Un soporte.", "validated": False}],
}
GOOD_PRINCIPLE: dict[str, Any] = {
    "id": "agujero-de-paso-con-holgura",
    "type": "principle",
    "title": "El agujero de paso lleva holgura respecto al tornillo",
    "applies_when": "Cuando un tornillo atraviesa una pieza hacia otra",
    "not_when": "Cuando el agujero ubica la pieza con un pasador",
    "recognize": "Un agujero de paso con el mismo diámetro nominal que el tornillo que lo atraviesa",
    "typical_error": "Hacer el agujero del diámetro nominal y no poder ensamblar por la desviación de posición",
    "when": {"ops": ["insert_component"], "stages": ["detail"]},
    "examples": [
        {
            "sector": "fixtures",
            "text": "Los agujeros de la base llevan holgura; los de ubicación, no.",
            "validated": False,
        }
    ],
}
PLACE = {"part": "soporte", "placement": {"origin": [0, 0, 0]}}
SKETCH = {
    "on": {"kind": "plane", "name": "XY"},
    "entities": [{"kind": "rect", "name": "outline", "center": [0, 0], "width": 120, "height": 60}],
}
TOP = {"kind": "face", "from_op": "s2", "select": {"normal": [0, 0, 1]}}


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COTAFORGE_HOME", str(tmp_path / "home"))  # never the engineer's memory
    monkeypatch.setenv("COTAFORGE_BASE_MEMORY", "0")  # the walkthrough is about the engineer's own entry


class Transcript:
    def __init__(self, cad: Cad) -> None:
        self.cad = cad
        self.parts: list[str] = ["# Recorrido de aceptación: memoria v2 (FreeCAD real)\n"]

    def note(self, text: str) -> None:
        self.parts.append(f"\n## {text}\n")

    async def step(self, tool: str, *, refused: bool = False, **arguments: Any) -> str:
        reply = await self.cad(tool, **arguments)
        assert reply.is_error is refused, reply.text
        call = f"{tool}({', '.join(f'{k}={json.dumps(v, ensure_ascii=False)}' for k, v in arguments.items())})"
        self.parts.append(f"\n**El agente llama:** `{call}`\n\n**El agente ve** ({'rechazo' if refused else 'ok'}):\n")
        self.parts.append("```\n" + reply.text + "\n```\n")
        return reply.text

    def write(self) -> None:
        target = os.environ.get("COTAFORGE_WALKTHROUGH")
        if target:
            Path(target).parent.mkdir(parents=True, exist_ok=True)
            Path(target).write_text("\n".join(self.parts), encoding="utf-8")


async def test_the_acceptance_walkthrough(workspace: Path) -> None:
    store = MemoryStore(project_dir=workspace / "memory")
    store.save(MemoryEntry.model_validate(PRINCIPLE), Scope.project)  # the engineer approved it (status active)

    async with running_server(workspace) as (cad, init):
        t = Transcript(cad)
        t.note("0. Proyecto: sector declarado")
        await t.step("memory", action="brief", sector="fixtures", processes=["soldadura"])

        t.note("1. Una placa soldada con un patrón de agujeros con tolerancia (rol soldado)")
        await t.step("doc", action="new", name="soporte")
        await t.step("op", name="sketch", params=SKETCH)
        first = await t.step("op", name="extrude", params={"sketch": "s1", "depth_mm": 10, "role": "soldado"})
        assert "memory: precision-del-ensamble — recognize:" in first and "role: s2 = soldado" in first
        holes = await t.step(
            "op",
            name="hole",
            params={
                "face": TOP,
                "position": [-30, 0],
                "standard": "simple",
                "diameter_mm": 8,
                "end_condition": "through_all",
            },
            intent="patron de ubicacion, tolerancia H7",
        )
        assert "memory: precision-del-ensamble" in holes  # the hole is the decision moment: said again for this op
        second = await t.step(
            "op",
            name="hole",
            params={
                "face": TOP,
                "position": [30, 0],
                "standard": "simple",
                "diameter_mm": 8,
                "end_condition": "through_all",
            },
            intent="segundo agujero del patron",
        )
        assert "memoria:" not in second  # the same entry for the same op is said once
        await t.step("doc", action="status")
        await t.step("doc", action="save", path="soporte.FCStd")

        t.note("2. El soporte entra al ensamble con el rol soldado")
        await t.step("doc", action="new", name="conjunto", kind="assembly")
        inserted = await t.step("op", name="insert_component", params={**PLACE, "role": "soldado"})
        assert "memory: precision-del-ensamble" in inserted

        t.note("3. Hito de detalle: el servidor rechaza y lista la entrada")
        refused = await t.step("memory", action="review", stage="detail", refused=True)
        assert "pending_answers" in refused and "precision-del-ensamble" in refused

        t.note("4. Responder sin haber leído la entrada: rechazado")
        early = await t.step(
            "memory",
            action="answer",
            id="precision-del-ensamble",
            text="applies: la placa s2 lleva el patron",
            refused=True,
        )
        assert "not_read" in early

        t.note("5. Leer la entrada: ejemplos del sector del proyecto primero, luego analogías (máximo dos)")
        got = await t.step("memory", action="get", id="precision-del-ensamble")
        assert got.index("[fixtures] (validated)") < got.index("analogy of")
        assert got.count("analogy of") == 2

        t.note("6. Una respuesta vacía no vale; una con el cómo y el dónde sí")
        await t.step("memory", action="answer", id="precision-del-ensamble", text="applies: sí", refused=True)
        await t.step(
            "memory",
            action="answer",
            id="precision-del-ensamble",
            text="applies: el soporte (s1) se suelda; el patron se mecaniza despues de soldar, no se pide a la soldadura",
        )

        t.note("7. El hito pasa y el ingeniero ve las respuestas")
        passed = await t.step("memory", action="review", stage="detail")
        assert "ready - 1 entry answered" in passed

        t.note("8. Guardar, cerrar y reabrir: las respuestas y el rol sobreviven")
        await t.step("doc", action="save", path="conjunto.FCStd")
        await t.step("doc", action="close")
        reopened = await t.step("doc", action="open", path="conjunto.FCStd")
        assert "roles: s1=soldado" in await cad.ok("doc", action="status"), reopened
        again = await t.step("memory", action="review", stage="detail")
        assert "ready - 1 entry answered" in again

        t.note("9. Guardar un principio con marca, código y medida: rechazado con la propuesta de dividirlo")
        split = await t.step("memory", action="save", entry=BAD_PRINCIPLE, refused=True)
        assert "split_entry" in split and "19 mm" in split

        t.note("10. Un guardado correcto queda propuesto: pendiente, no se empuja ni se exige")
        saved = await t.step("memory", action="save", entry=GOOD_PRINCIPLE)
        assert "status: proposed" in saved
        await t.step("memory", action="index")
        silent = await t.step(
            "op", name="insert_component", params={"part": "soporte", "placement": {"origin": [200, 0, 0]}}
        )
        assert "agujero-de-paso-con-holgura" not in silent

        t.note("11. El ingeniero lo aprueba (puerta del ingeniero) y entonces se empuja")
        await t.step(
            "memory",
            action="approve",
            id="agujero-de-paso-con-holgura",
            by="Ana",
            text="Ana: apruébalo como regla del proyecto",
        )
        pushed = await t.step(
            "op", name="insert_component", params={"part": "soporte", "placement": {"origin": [400, 0, 0]}}
        )
        assert "memory: agujero-de-paso-con-holgura" in pushed
        t.write()


async def test_a_role_is_stored_in_the_fcstd_and_colours_the_body(workspace: Path) -> None:
    async with running_server(workspace) as (cad, _):
        await cad.ok("doc", action="new", name="placa")
        await cad.ok("op", name="sketch", params=SKETCH)
        await cad.ok("op", name="extrude", params={"sketch": "s1", "depth_mm": 10, "role": "contacto"})
        await cad.ok("doc", action="save", path="placa.FCStd")
        await cad.ok("doc", action="close")
    async with running_server(workspace) as (cad, _):  # a new server: the role comes from the file
        await cad.ok("doc", action="open", path="placa.FCStd")
        assert "roles: s2=contacto" in await cad.ok("doc", action="status")
