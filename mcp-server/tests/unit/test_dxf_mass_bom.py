"""DXF export of a face outline, mass from a cited density and the bill of materials (fake adapter)."""

import re
from pathlib import Path
from typing import Any

import pytest
from cotaforge.contract import ExportDxfRequest

from cotaforge_mcp.config import Settings
from cotaforge_mcp.schemas import TOOLS_BY_NAME, input_schema
from cotaforge_mcp.session import Session
from tests.conftest import TOP, Caller, ExportFake, MakeSession, declare, material_entry, plate

PLATE_MM3 = 100 * 60 * 8


def exporting(make: MakeSession) -> tuple[Caller, ExportFake]:
    adapter = ExportFake()
    call = Caller(make(adapter))
    plate(call)
    return call, adapter


def top_face(call: Caller) -> str:
    match = re.search(r"(face:\d+) plane", call.ok("inspect", action="faces", normal=[0, 0, 1]))
    assert match is not None
    return match.group(1)


# --- DXF ---------------------------------------------------------------------------------------------------------


def test_dxf_takes_a_face_id_and_writes_next_to_the_design(make: MakeSession, settings: Settings) -> None:
    call, adapter = exporting(make)
    face = top_face(call)
    text = call.ok("export", action="dxf", face=face)
    target = settings.workspace / "plate.dxf"
    assert target.is_file() and f"exported dxf: {target}" in text
    (request,) = adapter.dxf_requests
    assert isinstance(request, ExportDxfRequest) and request.face is not None and request.face.id == face
    assert [p.name for p in settings.workspace.iterdir() if p.name.startswith(".")] == []  # no partial file left


def test_dxf_takes_the_face_reference_ops_take(make: MakeSession, settings: Settings) -> None:
    call, adapter = exporting(make)
    call.ok("export", action="dxf", face=TOP, path="sub/top.dxf")
    assert (settings.workspace / "sub" / "top.dxf").is_file()
    assert adapter.dxf_requests[0].face.id == top_face(call)


def test_dxf_without_a_usable_face_says_how(make: MakeSession) -> None:
    call, _ = exporting(make)
    assert "needs the planar face" in call.err("export", action="dxf")
    assert "not a face id" in call.err("export", action="dxf", face="edge:3")
    assert "not a face reference" in call.err("export", action="dxf", face={"kind": "face"})
    ambiguous = call.err("export", action="dxf", face={"kind": "face", "from_op": "s2", "select": {}})
    assert "candidates" in ambiguous and "face:" in ambiguous


def test_dxf_never_overwrites_unless_asked(make: MakeSession, settings: Settings) -> None:
    call, _ = exporting(make)
    face = top_face(call)
    target = settings.workspace / "plate.dxf"
    target.write_text("precious", encoding="utf-8")
    refused = call.err("export", action="dxf", face=face)
    assert "already exists" in refused and "overwrite=true" in refused
    assert target.read_text(encoding="utf-8") == "precious"
    call.ok("export", action="dxf", face=face, overwrite=True)
    assert "SECTION" in target.read_text(encoding="utf-8")


def test_a_failed_dxf_leaves_the_existing_file_alone(make: MakeSession, settings: Settings) -> None:
    class Failing(ExportFake):
        def export_dxf(self, request: Any) -> Any:
            Path(request.path).write_text("half", encoding="utf-8")
            raise RuntimeError("boom")

    call = Caller(make(Failing()))
    plate(call)
    target = settings.workspace / "plate.dxf"
    target.write_text("precious", encoding="utf-8")
    assert call.err("export", action="dxf", face=top_face(call), overwrite=True)
    assert target.read_text(encoding="utf-8") == "precious"
    assert [p.name for p in settings.workspace.iterdir() if p.name.startswith(".")] == []


def test_dxf_is_offered_only_when_the_cad_can_write_it(make: MakeSession) -> None:
    assert "dxf" in input_schema(TOOLS_BY_NAME["export"], make(ExportFake()))["properties"]["action"]["enum"]


# --- mass --------------------------------------------------------------------------------------------------------


def test_mass_is_volume_times_a_cited_density(make: MakeSession) -> None:
    call, _ = exporting(make)
    call.ok("doc", action="set_material", material="Acero estructural")
    properties = call.ok("inspect", action="properties")
    assert f"mass: {PLATE_MM3 * 7850e-9:.4f} kg" in properties
    assert "density 7850 kg/m3" in properties and "EN 1993-1-1" in properties


def test_without_a_known_density_mass_is_not_available_and_says_why(make: MakeSession) -> None:
    call, _ = exporting(make)
    assert "mass: not available (no material is assigned" in call.ok("inspect", action="properties")
    call.ok("doc", action="set_material", material="Unobtainium")
    unknown = call.ok("inspect", action="properties")
    assert "mass: not available" in unknown and "density_kg_m3" in unknown and " kg (" not in unknown


def test_a_declared_density_is_used_with_its_provenance(make: MakeSession, settings: Settings) -> None:
    entry = material_entry("acero-taller", "Acero A36", "A36")
    entry["provenance"] = {
        "source": "shop_measurement",
        "reference": "Pesaje lote 7",
        "by": "Ana",
        "date": "2026-10-11",
    }
    entry["material"]["properties"] = {"density_kg_m3": 7800}
    declare(settings, entry)
    call, _ = exporting(make)
    call.ok("doc", action="set_material", material="A36")
    properties = call.ok("inspect", action="properties")
    assert f"mass: {PLATE_MM3 * 7800e-9:.4f} kg" in properties
    assert "acero-taller" in properties and "Pesaje lote 7" in properties


# --- bill of materials ---------------------------------------------------------------------------------------------


def test_bom_lists_the_part_with_material_route_volume_and_mass(make: MakeSession) -> None:
    call, _ = exporting(make)
    call.ok("doc", action="set_material", material="Acero estructural")
    bom = call.ok("inspect", action="bom")
    assert "bom: 1 line(s), 1 part(s); material: Acero estructural; route: none" in bom
    assert f"s2 x1: volume {PLATE_MM3} mm3 each, mass {PLATE_MM3 * 7850e-9:.4f} kg each" in bom
    assert "total mass:" in bom and "EN 1993-1-1" in bom


def test_bom_groups_identical_bodies_and_totals_the_mass(make: MakeSession) -> None:
    call, _ = exporting(make)
    call.ok("doc", action="set_material", material="Acero estructural")
    for step in ("b1", "b2"):
        call.ok("op", name="box", id=step, params={"size": [10, 10, 10]})
    bom = call.ok("inspect", action="bom")
    assert "bom: 2 line(s), 3 part(s)" in bom
    assert "b1,b2 x2: volume 1000 mm3 each" in bom
    assert f"total mass: {(PLATE_MM3 + 2000) * 7850e-9:.4f} kg" in bom


def test_bom_without_a_density_reports_volumes_only(make: MakeSession) -> None:
    call, _ = exporting(make)
    bom = call.ok("inspect", action="bom")
    assert "material: none" in bom and "mass: not available" in bom and "mass " not in bom.replace("mass: not", "")


def test_bom_of_an_empty_document(make: MakeSession) -> None:
    call = Caller(make(ExportFake()))
    call.ok("doc", action="new", name="empty")
    assert "bom: empty" in call.ok("inspect", action="bom")


def test_the_bom_action_is_in_the_inspect_schema(session: Session) -> None:
    assert "bom" in input_schema(TOOLS_BY_NAME["inspect"], session)["properties"]["action"]["enum"]


@pytest.mark.parametrize("tool", ["export", "inspect"])
def test_schemas_stay_small(tool: str, make: MakeSession) -> None:
    import json

    assert len(json.dumps(input_schema(TOOLS_BY_NAME[tool], make(ExportFake())))) < 1800
