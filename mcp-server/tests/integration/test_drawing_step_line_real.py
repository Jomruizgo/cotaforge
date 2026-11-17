# ruff: noqa: E501
"""The step line and the engineer's gate on the REAL FreeCAD (Windows, VISIBLE), on the transmission shaft: what a full read of the
sheet left open stays in the line of an unrelated add (the add's own state holds only the step's few rules), and a compose that
throws the sheet away (``fresh=true``) is refused without the engineer's order and the sheet stays as it was."""

import re
import sys
from pathlib import Path

import pytest

from tests.integration.conftest import running_server
from tests.integration.test_drawing_compose_real import compose_the_rest, in_one_order
from tests.integration.test_drawing_shaft_sheet_real import DIAMETERS, INTENT, shaft

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the visible FreeCAD window is exercised on Windows")

DECLARED = """
## Proceso
- Torneado desde barra Ø35, rectificado de los asientos, fresado del chavetero y roscado.

## Material
- Acero C45, barra laminada.

## Cantidad
- 10 unidades.

## Relaciones funcionales
- Coaxialidad entre los dos asientos de rodamiento Ø0,02 respecto del eje común.
"""
RULE = re.compile(r"\b[a-z]+\.[a-z_]+\b")


def open_rules(text: str) -> tuple[str, set[str]]:
    """The step the last line of a reply is at, and the catalog rules it names as open there (the part before the first " | ")."""
    head = text.splitlines()[-1].split(" | ")[0]
    found = re.match(r"step (\d)/8 [^:]+: \d+ open \((.*)\)$", head)
    assert found, head
    return found.group(1), set(RULE.findall(found.group(2)))


async def test_an_add_keeps_what_the_read_left_open_and_fresh_compose_needs_the_order(workspace: Path) -> None:
    async with running_server(workspace, visible=True) as (cad, _):
        await in_one_order(cad)
        await shaft(cad, workspace)
        (workspace / "drawings" / "drive-shaft-intent.md").write_text(INTENT + DECLARED, encoding="utf-8")  # nothing left to ask at step 1

        # 1. the plan reads the bare sheet in full: its views step has findings of the review open; an unrelated add keeps them
        planned = await cad.ok("drawing", action="plan")
        step, standing = open_rules(planned)
        print("PLAN LINE", planned.splitlines()[-1])
        assert step == "2" and any(rule.startswith(("views.", "turned.")) for rule in standing), planned.splitlines()[-1]
        added = await cad.ok("drawing", action="add", items=[{"type": "note", "text": "Romper aristas vivas 0,5 x 45"}])
        print("ADD LINE", added.splitlines()[-1])
        assert "step checks only" in added.splitlines()[-1] and open_rules(added) == (step, standing), added.splitlines()[-1]

        # 2. compose(fresh=true) throws the sheet away: no order, no throw
        decisions = {
            "sheet": {"title": "Eje de transmision", "template": "A3", "language": "es"},
            "relations": [{"type": "baseline_dims", "from": "face:6", "view": "front", "side": "below"}, DIAMETERS[0], DIAMETERS[2]],
            "fits": [DIAMETERS[1], DIAMETERS[3], DIAMETERS[4]],
            "notes": ["Cantidad 10. Proceso: torneado, rectificado de los asientos, fresado del chavetero y roscado."],
        }
        composed = await compose_the_rest(cad, decisions, keep=("req.surface_texture",))
        assert "composed sheet" in composed, composed
        before = await cad.ok("drawing", action="list")
        changing = {**decisions, "sheet": {**decisions["sheet"], "template": "A2"}}
        refused = await cad.err("drawing", action="compose", sheet="Drawing", decisions=changing, fresh=True)
        print("REFUSED", refused)
        assert "engineer's order" in refused and "fresh=true" in refused, refused
        assert await cad.ok("drawing", action="list") == before, "the refused compose changed the sheet"
        declined = await cad.err("drawing", action="compose", sheet="Drawing", decisions=changing, fresh=True, by="Ana", reason="la hoja no me gusta")
        assert "rejected" in declined, declined  # he did not say it: Claude Code's dialog is declined
        assert await cad.ok("drawing", action="list") == before
