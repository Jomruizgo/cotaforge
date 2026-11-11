"""A CAD that reads and writes Parasolid: the server lets the file through and offers the export; a CAD that does not
keeps refusing as before."""

import json
from pathlib import Path
from typing import Any

import pytest
from cotaforge.adapters.fake import FakeAdapter
from cotaforge.contract import Ack, ImportGeometryResult

from cotaforge_mcp import foreign
from cotaforge_mcp.reply import ToolError
from cotaforge_mcp.schemas import TOOLS_BY_NAME, input_schema
from tests.conftest import Caller, ExportFake, MakeSession, plate


class ParasolidFake(ExportFake):
    """The fake with a Parasolid reader (a JSON stand-in handed to the STEP one) and writer (a stub file)."""

    def import_suffixes(self) -> tuple[str, ...]:
        return (".x_t", ".x_b")

    def import_geometry(self, request: Any) -> ImportGeometryResult:
        source = Path(request.path)
        stand_in = source.with_suffix(".step")
        stand_in.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        result = super().import_geometry(request.model_copy(update={"path": str(stand_in)}))
        return result.model_copy(update={"source_format": "parasolid"})

    def export_parasolid(self, request: Any) -> Ack:
        self.__dict__.setdefault("parasolid_requests", []).append(request)
        Path(request.path).write_text("**PART1", encoding="utf-8")
        return Ack()


def test_the_suffixes_a_cad_adds_pass_the_check_and_the_rest_do_not() -> None:
    foreign.check_suffix(Path("a.x_t"), (".x_t", ".x_b"))
    foreign.check_suffix(Path("a.X_B"), (".x_t", ".x_b"))
    foreign.check_suffix(Path("a.step"))
    with pytest.raises(ToolError, match="no Parasolid reader"):
        foreign.check_suffix(Path("a.x_t"))
    with pytest.raises(ToolError, match="reads .step"):
        foreign.check_suffix(Path("a.stl"), (".x_t",))


def test_the_default_adapter_adds_no_import_suffix() -> None:
    assert FakeAdapter().import_suffixes() == ()


def test_a_parasolid_file_is_imported_and_reported_as_parasolid(make: MakeSession, tmp_path: Path) -> None:
    call = Caller(make(ParasolidFake()))
    spec = {"units": "mm", "solids": [{"box": [40, 30, 10], "at": [0, 0, 0]}]}
    (tmp_path / "bracket.x_t").write_text(json.dumps(spec), encoding="utf-8")

    text = call.ok("doc", action="import", path="bracket.x_t", name="bracket")

    assert text.startswith("document: bracket (part) from bracket.x_t")
    assert "Parasolid" in text and "volume 12000 mm3" in text


def test_parasolid_export_is_offered_only_when_the_cad_can_write_it(make: MakeSession) -> None:
    offered = input_schema(TOOLS_BY_NAME["export"], make(ParasolidFake()))["properties"]["action"]["enum"]
    assert "parasolid" in offered
    assert "parasolid" not in input_schema(TOOLS_BY_NAME["export"], make(ExportFake()))["properties"]["action"]["enum"]


def test_parasolid_export_defaults_to_text_and_keeps_the_path_the_engineer_gave(
    make: MakeSession, tmp_path: Path
) -> None:
    adapter = ParasolidFake()
    call = Caller(make(adapter))
    plate(call)

    assert "exported parasolid:" in call.ok("export", action="parasolid")
    assert "exported parasolid:" in call.ok("export", action="parasolid", path="plate.x_b")

    names = [Path(r.path).name for r in adapter.parasolid_requests]
    assert names == ["plate.x_t", "plate.x_b"]


def test_parasolid_export_is_refused_when_the_cad_cannot(make: MakeSession) -> None:
    call = Caller(make(ExportFake()))
    plate(call)
    assert "error" in call.err("export", action="parasolid")


def test_the_refusal_of_parasolid_names_the_cad_that_refuses() -> None:
    with pytest.raises(ToolError, match=r"FreeCAD has no Parasolid reader \(OpenCASCADE has no free one\)"):
        foreign.check_suffix(Path("a.x_t"))
    with pytest.raises(ToolError, match=r"^a.x_t is a Parasolid file \(.x_t\): this CAD has no Parasolid reader, so"):
        foreign.check_suffix(Path("a.x_t"), (), "this CAD")
    with pytest.raises(ToolError, match="ExternalCAD reads .x_t but not .x_b"):
        foreign.check_suffix(Path("a.x_b"), (".x_t",), "ExternalCAD")


def test_the_import_texts_name_what_the_adapter_reads_and_stay_as_they_were_for_one_that_adds_nothing() -> None:
    both = (".x_t", ".x_b")
    assert foreign.import_usage().endswith('path="<file>.step|.stp|.iges|.igs", name="<document name>")')
    assert 'path="<file>.step|.stp|.iges|.igs|.x_t|.x_b"' in foreign.import_usage(both)
    with pytest.raises(ToolError, match=r"import reads .step, .stp, .iges, .igs files, not .stl"):
        foreign.check_suffix(Path("a.stl"))
    with pytest.raises(ToolError, match=r"import reads .step, .stp, .iges, .igs, .x_t, .x_b files, not .stl") as err:
        foreign.check_suffix(Path("a.stl"), both)
    assert "|.x_t|.x_b" in str(err.value.usage)


def test_the_doc_tool_and_the_guide_say_parasolid_only_for_a_cad_that_reads_it(make: MakeSession) -> None:
    from cotaforge_mcp.guide import guide  # noqa: PLC0415
    from cotaforge_mcp.schemas import tool_description  # noqa: PLC0415

    plain, reads = make(ExportFake()), make(ParasolidFake())
    assert "import(path: .step/.stp/.iges/.igs, name" in tool_description(TOOLS_BY_NAME["doc"], plain)
    assert "import(path: .step/.stp/.iges/.igs/.x_t/.x_b, name" in tool_description(TOOLS_BY_NAME["doc"], reads)
    assert "A customer part (STEP or IGES)" in guide(plain, "assembly")
    assert "A customer part (STEP, IGES or Parasolid)" in guide(reads, "assembly")
