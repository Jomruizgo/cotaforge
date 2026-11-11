"""Sheet metal as an optional capability of the server: the ops appear in the vocabulary and the guide only when the CAD
has it, the guide and the error say how to get it when it does not, and a sheet runs end to end (fake adapter)."""

import re
from typing import Any

import pytest
from cotaforge.adapters.fake import FakeAdapter

from cotaforge_mcp import catalog
from cotaforge_mcp.config import Settings
from cotaforge_mcp.guide import guide, profile_text
from cotaforge_mcp.schemas import TOOLS_BY_NAME, input_schema
from tests.conftest import Caller, ExportFake, MakeSession

SHEET_OPS = ["base_flange", "edge_flange", "unfold"]
INSTALL = "Install it in FreeCAD: Tools > Addon manager > search 'SheetMetal' > Install, then restart FreeCAD."

OUTLINE = {
    "on": {"kind": "plane", "name": "XY"},
    "entities": [{"kind": "rect", "name": "o", "center": [50, 30], "width": 100, "height": 60}],
}
TOP = {"kind": "face", "from_op": "s2", "select": {"normal": [0, 0, 1], "largest": True}}
SIDE = {"kind": "face", "from_op": "s2", "select": {"normal": [0, -1, 0]}}
FLANGE = {"edge": {"kind": "edge", "between": [TOP, SIDE]}, "length_mm": 20, "angle_deg": 90, "bend_radius_mm": 3}
UNFOLD = {"face": {"kind": "face", "from_op": "s2", "select": {"normal": [0, 0, -1]}}, "k_factor": 0.4}


class NoAddonFake(FakeAdapter):
    """A CAD whose sheet-metal add-on is not installed and that knows how to say so (like the FreeCAD adapter)."""

    def __init__(self) -> None:
        super().__init__(sheet_metal=False)

    def unavailable_tools(self) -> dict[str, str]:
        return dict.fromkeys(
            ("base_flange", "edge_flange", "unfold"), f"the SheetMetal add-on is not installed. {INSTALL}"
        )


def sheet(call: Caller) -> None:
    """s1 sketch, s2 base flange, s3 edge flange."""
    call.ok("doc", action="new", name="bracket")
    call.ok("op", name="sketch", params=OUTLINE)
    call.ok("op", name="base_flange", params={"sketch": "s1", "thickness_mm": 2})
    call.ok("op", name="edge_flange", params=FLANGE)


# --- the capability present -----------------------------------------------------------------------------------------


def test_the_ops_are_in_the_vocabulary_the_profile_and_the_guide(session: Any) -> None:
    assert set(SHEET_OPS) <= set(catalog.available_ops(session.capabilities))
    assert "ops (sheet metal): base_flange, edge_flange, unfold" in profile_text(session)
    listing = guide(session, "ops")
    for name in SHEET_OPS:
        assert re.search(rf"^{name}\(", listing, re.MULTILINE)
    assert "not available here" not in listing


def test_the_commercial_cad_words_lead_to_the_sheet_ops(session: Any) -> None:
    terms = guide(session, "terms")
    assert "brida de arista" in terms and "-> op edge_flange" in terms
    assert re.search(r"desplegar.*-> op unfold", terms) and re.search(r"brida base.*-> op base_flange", terms)
    assert "factor k" in terms and "ask the engineer or the shop" in terms


def test_the_reference_states_what_has_no_default_and_where_holes_go(session: Any) -> None:
    unfold = guide(session, "op:unfold")
    assert "k_factor: number  [>0 <1 REQUIRED]" in unfold and "ANSI" in unfold and "ask the engineer" in unfold
    flange = guide(session, "op:edge_flange")
    assert "bend_radius_mm: number  [>0 REQUIRED]" in flange and "INNER radius" in flange
    assert "default 90.0" in flange and '"material_inside"' in flange
    assert "extra closed profiles" in guide(session, "op:base_flange")


def test_a_sheet_runs_end_to_end_and_the_reply_states_the_flat_size(call: Caller) -> None:
    sheet(call)
    reply = call.ok("op", name="unfold", params=UNFOLD, intent="flat pattern, K assumed by the shop's rule")
    match = re.search(r"flat pattern: ([\d.]+) x ([\d.]+) mm, thickness 2, blank area ([\d.]+) mm2", reply)
    assert match is not None, reply
    assert float(match.group(1)) == pytest.approx(100) and float(match.group(2)) == pytest.approx(75.969, abs=1e-3)
    assert "volume and bbox below are the bent part" in reply and "valid: yes" in reply
    assert "warning" not in reply  # every asserted check agreed with the closed form


def test_a_missing_k_factor_is_asked_for_by_name(call: Caller) -> None:
    sheet(call)
    text = call.err("op", name="unfold", params={"face": UNFOLD["face"]})
    assert "k_factor" in text and "Field required" in text


def test_the_flat_pattern_exports_by_the_step_of_its_unfold(make: MakeSession, settings: Settings) -> None:
    adapter = ExportFake()
    call = Caller(make(adapter))
    sheet(call)
    call.ok("op", name="unfold", params=UNFOLD)
    text = call.ok("export", action="dxf", flat="s4", path="bracket_flat.dxf")
    assert "exported dxf" in text and (settings.workspace / "bracket_flat.dxf").is_file()
    (request,) = adapter.dxf_requests
    assert request.face is None and request.flat_pattern is not None
    assert (
        (settings.workspace / "bracket_flat.dxf").read_text(encoding="utf-8").endswith("stub for feature:3\n")
    )  # the unfold's feature


def test_the_dxf_source_is_exactly_one_and_a_flat_pattern_comes_from_an_unfold(
    make: MakeSession, settings: Settings
) -> None:
    call = Caller(make(ExportFake()))
    sheet(call)
    call.ok("op", name="unfold", params=UNFOLD)
    assert "not both" in call.err("export", action="dxf", flat="s4", face="face:1")
    not_unfold = call.err("export", action="dxf", flat="s3", path="x.dxf")
    assert "s3 is a edge_flange, not an unfold" in not_unfold
    assert "not a step in effect" in call.err("export", action="dxf", flat="s99", path="x.dxf")
    assert not (settings.workspace / "x.dxf").exists()


def test_the_export_schema_offers_flat_only_with_the_capability(make: MakeSession) -> None:
    with_sheets = input_schema(TOOLS_BY_NAME["export"], make(ExportFake()))
    assert "flat" in with_sheets["properties"] and len(str(with_sheets)) < 1800

    class ExportWithoutSheets(ExportFake):
        def __init__(self) -> None:
            super().__init__(sheet_metal=False)

    without = input_schema(TOOLS_BY_NAME["export"], make(ExportWithoutSheets()))
    assert "flat" not in without["properties"] and "dxf" in without["properties"]["action"]["enum"]


# --- the capability absent ------------------------------------------------------------------------------------------


def test_without_the_capability_the_ops_do_not_exist_for_the_agent(make: MakeSession) -> None:
    session = make(FakeAdapter(sheet_metal=False))
    assert not set(SHEET_OPS) & set(catalog.available_ops(session.capabilities))
    assert "sheet metal" not in profile_text(session) and "base_flange" not in profile_text(session)


def test_the_guide_says_how_to_install_the_addon(make: MakeSession) -> None:
    session = make(NoAddonFake())
    listing = guide(session, "ops")
    assert "not available here: base_flange, edge_flange, unfold" in listing and "Addon manager" in listing
    assert not re.search(r"^base_flange\(", listing, re.MULTILINE)
    terms = guide(session, "terms")
    assert re.search(r"brida de arista.*-> not available with this CAD: the SheetMetal add-on is not installed", terms)
    assert "Addon manager" in terms
    reference = guide(session, "op:edge_flange")
    assert reference.startswith("(not available: the SheetMetal add-on is not installed. Install it in FreeCAD")
    assert "bend_radius_mm" in reference  # the parameters are still documented


def test_calling_a_missing_op_anyway_explains_instead_of_failing_silently(make: MakeSession) -> None:
    call = Caller(make(NoAddonFake()))
    call.ok("doc", action="new", name="x")
    call.ok("op", name="sketch", params=OUTLINE)
    text = call.err("op", name="base_flange", params={"sketch": "s1", "thickness_mm": 2})
    assert "not available" in text and "Addon manager" in text and "restart" in text
    assert "cad_guide" in text


def test_a_cad_that_could_not_have_it_gets_the_plain_message(make: MakeSession) -> None:
    class Plain(FakeAdapter):
        def unavailable_tools(self) -> dict[str, str]:
            return {}

        def capabilities(self) -> Any:
            caps = super().capabilities()
            return type(caps)(tools=caps.tools - set(SHEET_OPS))

    call = Caller(make(Plain()))
    call.ok("doc", action="new", name="x")
    assert "not supported by fake" in call.err("op", name="unfold", params=UNFOLD)
