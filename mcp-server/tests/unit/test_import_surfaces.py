"""A file of loose faces through ``doc(action=import)``: what the engineer is told about the surface body, the way
the file was read and the sewing, and that the sewing options reach the CAD. Over the fake (the CAD itself is covered
by the worker checks and the real-FreeCAD integration test)."""

import json
from pathlib import Path
from typing import Any

import pytest
from cotaforge.adapters.fake import FakeAdapter
from cotaforge.contract import ImportGeometryResult, SewingReport

from tests.conftest import Caller, MakeSession

PLAIN_NOTE = "read with the plain IGES reader: names, colours and assembly structure are not available"


def supplier_file(tmp_path: Path) -> None:
    (tmp_path / "skin.igs").write_text(json.dumps({"solids": [{"box": [10, 10, 10]}]}), encoding="utf-8")


class SurfaceFake(FakeAdapter):
    """A CAD that reads loose faces: one surface body (no solid, an open shell), optionally sewn."""

    def __init__(self, sewing: SewingReport | None = None, notes: tuple[str, ...] = ()) -> None:
        super().__init__()
        self.requests: list[Any] = []
        self._sewing = sewing
        self._notes = notes

    def import_geometry(self, request: Any) -> ImportGeometryResult:
        self.requests.append(request)
        result = super().import_geometry(request)
        body = result.bodies[0].model_copy(update={"solids": 0, "shells": 0, "volume_mm3": 0.0, "closed": False})
        return result.model_copy(update={"bodies": (body,), "sewing": self._sewing, "notes": self._notes})


def test_a_file_that_stayed_surfaces_says_how_it_was_read_and_that_sewing_is_available(
    make: MakeSession, tmp_path: Path
) -> None:
    supplier_file(tmp_path)
    call = Caller(make(SurfaceFake(notes=(PLAIN_NOTE,))))

    text = call.ok("doc", action="import", path="skin.igs", name="skin")

    assert f"note: {PLAIN_NOTE}" in text
    assert "NO solid (surfaces)" in text and "NOT closed" in text
    assert "sew=true" in text and "sew_tolerance_mm=0.01" in text
    assert "inspect interference measures its distance" in text
    assert "sewing:" not in text


def test_the_sewing_options_reach_the_cad_and_the_result_counts_the_outcome(
    make: MakeSession, tmp_path: Path
) -> None:
    supplier_file(tmp_path)
    sewing = SewingReport(
        tolerance_mm=0.05, faces=120, shells=9, closed_shells=7, solids=7, free_edges=14, seconds=2.5
    )
    fake = SurfaceFake(sewing=sewing)
    call = Caller(make(fake))

    text = call.ok("doc", action="import", path="skin.igs", name="skin", sew=True, sew_tolerance_mm=0.05)

    request = fake.requests[0]
    assert request.sew is True and request.sew_tolerance_mm == 0.05
    assert (
        "sewing: 120 loose faces joined at 0.05 mm in 2.5 s: 9 shells, 7 closed -> 7 solids (the volume counts these "
        "only); 14 free edges (gaps) remain in what is still surface" in text
    )
    assert "sew=true" not in text  # it was asked for: no hint to ask


def test_without_the_options_nothing_is_sewn(make: MakeSession, tmp_path: Path) -> None:
    supplier_file(tmp_path)
    fake = SurfaceFake()
    Caller(make(fake)).ok("doc", action="import", path="skin.igs", name="skin")
    assert fake.requests[0].sew is False and fake.requests[0].sew_tolerance_mm == 0.01


def test_a_solid_file_is_not_offered_sewing(call: Caller, tmp_path: Path) -> None:
    supplier_file(tmp_path)
    text = call.ok("doc", action="import", path="skin.igs", name="solid")
    assert "sew=true" not in text and "note: read with" not in text


@pytest.mark.parametrize("tolerance", [0, -1, 11])
def test_a_sewing_tolerance_that_makes_no_sense_is_refused(call: Caller, tmp_path: Path, tolerance: float) -> None:
    supplier_file(tmp_path)
    text = call.err("doc", action="import", path="skin.igs", name="skin", sew=True, sew_tolerance_mm=tolerance)
    assert "sew_tolerance_mm must be above 0 and at most 10 mm" in text
    assert "document: none" in call.ok("doc", action="status")
