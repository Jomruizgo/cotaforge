"""What import, status and properties say about a file that mixes solids and surfaces, a file of many bodies, and a
surface body whose area the CAD could not compute. Over the fake (the CAD itself is covered by the worker checks and the
real-FreeCAD integration tests)."""

import json
from pathlib import Path
from typing import Any

from cotaforge.adapters.fake import FakeAdapter
from cotaforge.contract import ImportGeometryResult, MassProperties

from tests.conftest import Caller, MakeSession


def supplier_file(tmp_path: Path, solids: int = 3) -> None:
    body = {"solids": [{"box": [10, 10, 10], "at": [i * 20, 0, 0]} for i in range(solids)]}
    (tmp_path / "part.step").write_text(json.dumps(body), encoding="utf-8")


class ReshapedFake(FakeAdapter):
    """A CAD whose import says some of its bodies are surfaces only, open or invalid, and whose measures can be set."""

    def __init__(
        self,
        flat: tuple[int, ...] = (),
        open_shell: tuple[int, ...] = (),
        invalid: tuple[int, ...] = (),
        mass: dict[str, Any] | None = None,
    ) -> None:
        super().__init__()
        self._flat, self._open, self._invalid, self._mass = flat, open_shell, invalid, mass or {}

    def import_geometry(self, request: Any) -> ImportGeometryResult:
        result = super().import_geometry(request)
        bodies = []
        for index, body in enumerate(result.bodies):
            update: dict[str, Any] = {}
            if index in self._flat:
                update.update(solids=0, shells=0, volume_mm3=0.0, closed=False)
            if index in self._open:
                update.update(closed=False)
            if index in self._invalid:
                update.update(valid=False)
            bodies.append(body.model_copy(update=update))
        return result.model_copy(update={"bodies": tuple(bodies)})

    def mass_properties(self, request: Any) -> MassProperties:
        return super().mass_properties(request).model_copy(update=self._mass)


def test_a_file_with_one_surface_body_among_solids_does_not_say_it_holds_no_solids(
    make: MakeSession, tmp_path: Path
) -> None:
    supplier_file(tmp_path, solids=3)
    call = Caller(make(ReshapedFake(flat=(1,), mass={"surface_only": ("body:2",)})))

    text = call.ok("doc", action="import", path="part.step", name="part")

    assert "the file holds surfaces, not solids" not in text
    assert "note: 1 of 3 bodies hold surfaces only, no solid: the total volume counts the solids only" in text
    assert "sew=true" in text  # sewing is still on offer for the surface body
    assert "adds no volume (the total volume below counts the solids only)" in text
    assert "neither is the total volume" not in text
    assert "(counts solids only; 1 surface-only body adds none)" in text


def test_a_file_of_surfaces_only_still_says_so(make: MakeSession, tmp_path: Path) -> None:
    supplier_file(tmp_path, solids=2)
    mass = {"surface_only": ("body:1", "body:2"), "volume_mm3": 0.0, "centroid_meaningful": False}
    call = Caller(make(ReshapedFake(flat=(0, 1), mass=mass)))

    text = call.ok("doc", action="import", path="part.step", name="part")

    assert "note: the file holds surfaces, not solids (their volume is not meaningful)" in text
    assert "have surfaces or open shells only, no solid: their volume is not meaningful" in text
    assert "and neither is the total volume below" in text
    assert "(no solid in the document: surfaces have no volume)" in text


def test_a_large_import_keeps_its_warnings_and_totals_and_writes_the_rest_to_informes(
    make: MakeSession, tmp_path: Path
) -> None:
    count = 60
    supplier_file(tmp_path, solids=count)
    fake = ReshapedFake(flat=(0,), open_shell=tuple(range(1, 40)), invalid=(41, 42))
    call = Caller(make(fake))

    text = call.ok("doc", action="import", path="part.step", name="part")

    assert "chars cut" not in text and "narrow the request" not in text
    assert f"imported: part.step (STEP): {count} bodies" in text
    assert f"content: {count - 1} solids, " in text
    assert "warning: " in text and "a shell that is not closed" in text and "fail the CAD's geometry check" in text
    assert "volume: " in text and "bbox: " in text and "valid: " in text
    assert "adopted: " in text and len(text) < 5000
    reports = sorted((tmp_path / "reports").glob("import-*.txt"))
    assert len(reports) == 1 and reports[0].name in text, text
    whole = reports[0].read_text(encoding="utf-8")
    rows = [line for line in whole.split("== bodies ==")[1].split("== ")[0].splitlines() if " faces, volume " in line]
    assert len(rows) == count  # every body, not the few shown
    assert len(whole.split("== adopted ==")[1].split()) == count  # every adopted name


def test_status_of_a_document_of_many_bodies_names_the_file_with_the_whole_list(
    make: MakeSession, tmp_path: Path
) -> None:
    supplier_file(tmp_path, solids=30)
    call = Caller(make(FakeAdapter()))
    call.ok("doc", action="import", path="part.step", name="part")

    text = call.ok("doc", action="status")

    assert "chars cut" not in text
    assert "adopted: " in text and "more adopted omitted; full report in" in text
    report = next((tmp_path / "reports").glob("status-*.txt"))
    assert report.name in text
    assert len(report.read_text(encoding="utf-8").split("== adopted ==")[1].split()) == 30


def test_a_small_import_writes_no_report_file(make: MakeSession, tmp_path: Path) -> None:
    supplier_file(tmp_path, solids=3)
    call = Caller(make(FakeAdapter()))
    text = call.ok("doc", action="import", path="part.step", name="part")
    assert "reports" not in text and not (tmp_path / "reports").exists()


def test_properties_leave_out_a_garbage_area_and_say_so(make: MakeSession, tmp_path: Path) -> None:
    supplier_file(tmp_path, solids=2)
    mass = {"surface_mm2": 4800.0, "area_left_out": ("body:2",), "surface_only": ("body:2",)}
    call = Caller(make(ReshapedFake(flat=(1,), mass=mass)))
    call.ok("doc", action="import", path="part.step", name="part")

    text = call.ok("inspect", action="properties")

    assert "surface: 4800 mm2 (without body:2" in text and "returned an area that cannot be real" in text
    assert "e+" not in text
    assert "centroid: (" in text and "(of the solids only)" in text
    assert "volume: " in text and "(counts solids only; 1 surface-only body adds none)" in text


def test_properties_of_a_document_without_solids_do_not_print_a_zero_centroid(
    make: MakeSession, tmp_path: Path
) -> None:
    supplier_file(tmp_path, solids=1)
    mass = {"volume_mm3": 0.0, "centroid": (0.0, 0.0, 0.0), "centroid_meaningful": False, "surface_only": ("body:1",)}
    call = Caller(make(ReshapedFake(flat=(0,), mass=mass)))
    call.ok("doc", action="import", path="part.step", name="part")

    text = call.ok("inspect", action="properties")

    assert "centroid: not meaningful" in text and "centroid: (0" not in text
    assert "volume: 0 mm3 (no solid in the document: surfaces have no volume)" in text


def test_a_result_that_still_does_not_fit_is_cut_with_the_whole_text_in_informes(
    make: MakeSession, tmp_path: Path
) -> None:
    from cotaforge_mcp.tools import _fitted

    session = make(FakeAdapter())
    lines = [f"line {i} " + "x" * 80 for i in range(100)]

    reply = _fitted(session, "import", lines, 1000)

    assert len(reply.text) < 1400 and "chars cut" not in reply.text
    report = next((tmp_path / "reports").glob("import-*.txt"))
    assert report.name in reply.text
    assert all(line in report.read_text(encoding="utf-8") for line in lines)
    assert _fitted(session, "import", lines[:3], 1000).text == "\n".join(lines[:3])
