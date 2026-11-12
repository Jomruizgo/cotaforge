"""A customer's part in: ``doc(action=import)`` over the fake (a JSON stand-in for the STEP/IGES file) and
``inspect(action=holes)``."""

import json
from pathlib import Path
from typing import Any

import pytest
from cotaforge.adapters.fake import FakeAdapter
from cotaforge.contract import ImportGeometryResult

from cotaforge_mcp.schemas import TOOLS, TOOLS_BY_NAME, ToolDef, input_schema
from cotaforge_mcp.session import Session
from tests.conftest import TOP, Caller, MakeSession, fake_without, plate

CHASSIS = {"units": "inch", "solids": [{"box": [40, 30, 10], "at": [1000, 2000, 300]}]}


def supplier_file(tmp_path: Path, name: str = "chassis.step", spec: dict[str, Any] | None = None) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(spec or CHASSIS), encoding="utf-8")
    return path


# --- import ---------------------------------------------------------------------------------------------------------


def test_import_makes_a_saved_part_document_that_keeps_the_coordinates_of_the_file(
    call: Caller, tmp_path: Path
) -> None:
    call.ok("doc", action="new", name="work")
    supplier_file(tmp_path)

    text = call.ok("doc", action="import", path="chassis.step", name="chassis")

    assert text.startswith("document: chassis (part) from chassis.step")
    assert "units: the file declares inch; converted to mm" in text
    assert "coordinates: kept exactly as in the file (no recentring)" in text
    assert "content: 1 solids, 1 shells, 6 faces" in text
    assert "volume 12000 mm3" in text and "min (1000, 2000, 300)" in text and "valid, closed" in text
    assert "no feature history" in text and "op(edit)" in text and "insert_component" in text
    assert (tmp_path / "chassis.fakecad.json").is_file() and (tmp_path / "chassis.fakecad.cotaforge.json").is_file()
    assert "other open documents: work (part)" in text  # the document that was open is parked, as doc new does
    assert "UNSAVED" not in call.ok("doc", action="status")


def test_an_imported_part_goes_into_an_assembly_at_its_own_coordinates(call: Caller, tmp_path: Path) -> None:
    supplier_file(tmp_path)
    call.ok("doc", action="import", path="chassis.step", name="chassis")
    call.ok("doc", action="new", name="cell", kind="assembly")

    call.ok("op", name="insert_component", id="part", params={"part": "chassis", "placement": {}})

    measured = call.ok("inspect", action="measure")
    assert "min (1000, 2000, 300)" in measured and "max (1040, 2030, 310)" in measured


def test_the_imported_bodies_are_references_without_history(call: Caller, tmp_path: Path) -> None:
    supplier_file(tmp_path)
    call.ok("doc", action="import", path="chassis.step", name="chassis")

    assert call.ok("doc", action="status").count("steps: 0") == 1
    refused = call.err("op", edit="box", params={"size": [1, 1, 1]})
    assert "box" in refused
    assert call.err("undo").startswith("error: nothing_to_undo")


def test_import_reads_every_solid_as_a_body(call: Caller, tmp_path: Path) -> None:
    spec = {"solids": [{"box": [10, 10, 10]}, {"box": [20, 20, 20], "at": [50, 0, 0]}]}
    supplier_file(tmp_path, "pair.stp", spec)

    text = call.ok("doc", action="import", path="pair.stp", name="pair")

    assert "2 bodies" in text and "content: 2 solids" in text
    assert "units: the file does not declare" in text
    assert call.ok("inspect", action="measure").count("faces") >= 2


def test_import_refuses_to_replace_a_file_unless_asked(call: Caller, tmp_path: Path) -> None:
    supplier_file(tmp_path)
    (tmp_path / "chassis.fakecad.json").write_text("{}", encoding="utf-8")

    refused = call.err("doc", action="import", path="chassis.step", name="chassis")

    assert "already exists" in refused and "overwrite=true" in refused
    assert (tmp_path / "chassis.fakecad.json").read_text(encoding="utf-8") == "{}"
    assert "document: none" in call.ok("doc", action="status")
    assert call.ok("doc", action="import", path="chassis.step", name="chassis", overwrite=True).startswith("document:")


def test_a_failed_import_leaves_the_active_document_active(call: Caller, tmp_path: Path) -> None:
    call.ok("doc", action="new", name="work")
    (tmp_path / "broken.step").write_text("not even json", encoding="utf-8")
    (tmp_path / "empty.step").write_text(json.dumps({"solids": []}), encoding="utf-8")

    assert "could not be read" in call.err("doc", action="import", path="broken.step", name="broken")
    assert "no solid, shell or face" in call.err("doc", action="import", path="empty.step", name="empty")
    assert "not_found" in call.err("doc", action="import", path="missing.step", name="x")
    assert "document: work (part)" in call.ok("doc", action="status")


def test_import_needs_a_path_and_a_usable_name(call: Caller, tmp_path: Path) -> None:
    supplier_file(tmp_path)
    assert "import needs the path" in call.err("doc", action="import", name="x")
    assert "cannot name a document" in call.err("doc", action="import", path="chassis.step", name="../escape")
    call.ok("doc", action="new", name="chassis")
    assert "already open" in call.err("doc", action="import", path="chassis.step", name="chassis")
    supplier_file(tmp_path, "bracket_v2.step")
    assert call.ok("doc", action="import", path="bracket_v2.step").startswith("document: bracket_v2 (part)")


@pytest.mark.parametrize("suffix", [".x_t", ".x_b", ".X_T"])
def test_parasolid_is_refused_with_what_to_ask_for(call: Caller, tmp_path: Path, suffix: str) -> None:
    (tmp_path / f"bracket{suffix}").write_text("**PART1", encoding="utf-8")

    text = call.err("doc", action="import", path=f"bracket{suffix}", name="bracket")

    assert "error: unsupported_format" in text
    assert "no Parasolid reader" in text and "STEP" in text and "AP214" in text and "commercial CAD" in text
    assert "document: none" in call.ok("doc", action="status")
    assert "reads .step" in call.err("doc", action="import", path="mesh.stl", name="m")


class SurfaceFake(FakeAdapter):
    """A CAD that reads a file of surfaces: the body has no solid and an open shell."""

    def import_geometry(self, request: Any) -> ImportGeometryResult:
        result = super().import_geometry(request)
        body = result.bodies[0].model_copy(update={"solids": 0, "shells": 1, "volume_mm3": 0.0, "closed": False})
        return result.model_copy(update={"bodies": (body,), "warnings": ("2 edge object(s) were left out",)})


def test_surfaces_without_a_solid_are_said_to_have_no_meaningful_volume(make: MakeSession, tmp_path: Path) -> None:
    call = Caller(make(SurfaceFake()))
    supplier_file(tmp_path)

    text = call.ok("doc", action="import", path="chassis.step", name="skin")

    assert "NO solid (surfaces)" in text and "NOT closed" in text
    assert "has surfaces or open shells only, no solid: its volume is not meaningful" in text
    assert "warning: 2 edge object(s) were left out" in text


def test_import_is_offered_only_by_a_cad_that_can_read_files(make: MakeSession, tmp_path: Path) -> None:
    session = make(fake_without("import_geometry"))
    assert "import" not in input_schema(TOOLS_BY_NAME["doc"], session)["properties"]["action"]["enum"]
    supplier_file(tmp_path)
    assert "not available with this CAD" in Caller(session).err("doc", action="import", path="chassis.step", name="c")


# --- holes ----------------------------------------------------------------------------------------------------------


def drill(call: Caller, op_id: str, diameter: float, position: list[float], **end: Any) -> None:
    hole = {
        "face": TOP,
        "position": position,
        "standard": "simple",
        "diameter_mm": diameter,
        "end_condition": "through_all",
        **end,
    }
    call.ok("op", name="hole", id=op_id, params=hole)


def test_holes_are_listed_by_diameter_with_axis_depth_and_faces(call: Caller) -> None:
    plate(call)  # 100 x 60 x 8
    drill(call, "m8", 8, [20, 0])
    drill(call, "m6a", 6, [-20, 10])
    drill(call, "m6b", 6, [-20, -10])
    drill(call, "pocket", 10, [0, 0], end_condition="blind", depth_mm=3)

    text = call.ok("inspect", action="holes")

    lines = text.splitlines()
    assert lines[0].startswith("holes: 4 in 1 body; by diameter (mm): 2 x D6 (2 through), 1 x D8 (1 through), 1 x D10")
    assert [line.split()[0] for line in lines[1:]] == ["D6", "D6", "D8", "D10"]  # sorted by diameter
    assert "D6 through depth 8" in lines[1] and "D10 blind depth 3" in lines[4]
    assert "axis from (" in lines[1] and "along (0, 0, " in lines[1] and "faces face:" in lines[1]
    blind_axis = lines[4].split("axis from ")[1]
    assert "along (0, 0, -1)" in blind_axis  # from the open end into the material (the top face looks up)


def test_a_boss_is_not_a_hole(call: Caller) -> None:
    plate(call)
    call.ok("op", name="box", id="stub", params={"size": [10, 10, 10]})
    sketch = {
        "on": {"kind": "face", "from_op": "s2", "select": {"normal": [0, 0, 1]}},
        "entities": [{"kind": "circle", "name": "boss", "center": [0, 0], "diameter": 12}],
    }
    call.ok("op", name="sketch", id="bs", params=sketch)
    call.ok("op", name="extrude", id="boss", params={"sketch": "bs", "depth_mm": 5})

    assert call.ok("inspect", action="holes").startswith("holes: 0")


def test_holes_of_one_body_and_a_long_list_goes_to_a_file(call: Caller, tmp_path: Path) -> None:
    plate(call)
    for index in range(5):
        drill(call, f"h{index}", 4, [-40 + 20 * index, 0])

    text = call.ok("inspect", action="holes", limit=2)

    assert text.splitlines()[0].startswith("holes: 5 in 1 body; by diameter (mm): 5 x D4 (5 through)")
    assert len([line for line in text.splitlines() if line.startswith("D4 ")]) == 2
    assert "3 more holes omitted; full report in" in text
    report = next(tmp_path.glob("reports/holes-*.txt"))
    assert report.read_text(encoding="utf-8").count("D4 through") == 5
    body = call.session.named_bodies()
    first_body = next(iter(body))
    assert call.ok("inspect", action="holes", body=first_body).startswith("holes: 5")


def test_holes_is_offered_only_when_the_cad_can_list_them(make: MakeSession) -> None:
    session = make(fake_without("list_holes"))
    assert "holes" not in input_schema(TOOLS_BY_NAME["inspect"], session)["properties"]["action"]["enum"]


# --- what it costs the agent to know ----------------------------------------------------------------------------------


def test_the_schema_cost_of_import_and_holes(session: Session) -> None:
    def cost(tool: ToolDef) -> int:
        return len(json.dumps(input_schema(tool, session), separators=(",", ":"))) + len(tool.description)

    # measured before: doc 1364, inspect 1228, all visible tools 7501; after import and holes: doc 1438 (+74),
    # inspect 1231 (+3), all 7578 (+77); the action validity (the deep check of an imported file) added 11 to inspect
    assert (
        cost(TOOLS_BY_NAME["doc"]) < 2000
    )  # 1964 with the convention action (values, +204); sew and sew_tolerance_mm of import added 187; reload and force 157
    assert cost(TOOLS_BY_NAME["inspect"]) < 1330  # 1293 with the edge-set preview; the CAD feature of tree on top
    assert (
        sum(cost(t) for t in TOOLS if t.visible(session)) < 9800
    )  # 9394 with the convention action; +56 brief language
    # 8220 with feature edit, reload, focus; 8724 with memory v2; 9025 with view on=, sheet= and sheet items;
    # 9025 measured with the native drawing sheet (unchanged:
    # the drawing tool is not in this session; on its own it went 1932 -> 2184 characters);
    # +21 with memory M1.1 (related, replaces, sources and two provenance sources in the memory tool): 9046


def test_the_assembly_guide_walks_the_import_workflow(call: Caller, make: MakeSession) -> None:
    text = call.ok("cad_guide", topic="assembly")
    for needle in (
        'doc(action="import"',
        "no recentring",
        "Parasolid",
        'inspect(action="holes")',
        'view(action="show"',
        "insert_component",
        "OWN coordinates",
        "op(edit)",
        "run_native",
    ):
        assert needle in text, needle
    assert "customer" not in Caller(make(fake_without("import_geometry"))).ok("cad_guide", topic="assembly")
