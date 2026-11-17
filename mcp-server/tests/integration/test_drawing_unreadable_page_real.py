# ruff: noqa: E501
"""A page made by hand in plain FreeCAD (no cotaforge spec) is never thrown away by a ``create``: the call is refused, says why, and
the page is as it was; only ``fresh`` with the engineer's order rebuilds it (real FreeCAD, Windows, VISIBLE)."""

import sys
from pathlib import Path

import pytest

from tests.integration.conftest import running_server, run_freecad_script

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the visible FreeCAD window is exercised on Windows")

SCRIPT = """
import FreeCAD as App
import Part
import os
doc = App.newDocument("manual")
box = doc.addObject("Part::Box", "Block")
box.Length, box.Width, box.Height = 60, 40, 20
doc.recompute()
page = doc.addObject("TechDraw::DrawPage", "Plano")
template = doc.addObject("TechDraw::DrawSVGTemplate", "PlanoTemplate")
template.Template = os.path.join(App.getResourceDir(), "Mod", "TechDraw", "Templates", "A3_Landscape_blank.svg")
page.Template = template
doc.recompute()
doc.saveAs(r"{path}")
App.closeDocument("manual")
"""


async def test_a_page_made_by_hand_is_refused_a_create_and_kept_until_the_engineer_orders_it_thrown_away(workspace: Path) -> None:
    script = workspace.parent / "make_manual.py"
    target = workspace / "manual.FCStd"
    script.write_text(SCRIPT.format(path=str(target)), encoding="utf-8")
    run_freecad_script(script)
    assert target.is_file()
    async with running_server(workspace, visible=True) as (cad, _):
        await cad.ok("doc", action="open", path="manual.FCStd")
        before = await cad.ok("drawing", action="list")
        assert "Plano" in before, before
        refused = await cad.err("drawing", action="create", sheet="Plano")
        print("REFUSED", refused)
        assert "cannot read it" in refused and "no cotaforge spec" in refused and "fresh=true" in refused, refused
        assert await cad.ok("drawing", action="list") == before, "the refused create changed the page"
        lenient = await cad.err("drawing", action="create", sheet="Plano", fresh="yes", by="Ana", reason="la rehago")  # he did not say it: the dialog is declined
        assert "rejected" in lenient, lenient
        assert await cad.ok("drawing", action="list") == before
        words = cad.says("tira la hoja a mano y hazla de nuevo")
        made = await cad.ok("drawing", action="create", sheet="Plano", fresh="yes", by="Ana", reason=words)
        print("MADE", made)
        assert "tirar la hoja" in cad.engineer.dialogs[-1], cad.engineer.dialogs[-1]  # type: ignore[union-attr]
        assert "Plano" in await cad.ok("drawing", action="list")
