"""``export(action="dxf")``: a face or flat pattern -> the cutting outline; neither -> the drawing sheet, if any."""

import re
from pathlib import Path
from typing import Any

from cotaforge.contract import Ack, AdapterError, ErrorCode, ErrorInfo

from cotaforge_mcp.config import Settings
from tests.conftest import Caller, DrawingFake, MakeSession, plate, set_review


class SheetAndOutlineFake(DrawingFake):
    """A CAD that writes both kinds of DXF; ``has_sheet`` False answers like a document with no drawing sheet."""

    def __init__(self) -> None:
        super().__init__()
        self.has_sheet = True
        self.outlines: list[Any] = []

    def export_drawing(self, request: Any) -> Any:
        if not self.has_sheet:
            message = "the document has no drawing sheet: create it first with drawing(action=create)"
            raise AdapterError(ErrorInfo(code=ErrorCode.invalid_request, message=message, recoverable=True))
        return super().export_drawing(request)

    def export_dxf(self, request: Any) -> Any:
        self.outlines.append(request)
        Path(request.path).write_text("0\nSECTION\n999\noutline\n", encoding="utf-8")
        return Ack()


def test_dxf_goes_to_the_outline_with_a_face_or_flat_and_to_the_sheet_with_neither(
    make: MakeSession, settings: Settings
) -> None:
    set_review(settings, "ninguno")
    adapter = SheetAndOutlineFake()
    call = Caller(make(adapter))
    plate(call)
    match = re.search(r"(face:\d+) plane", call.ok("inspect", action="faces", normal=[0, 0, 1]))
    assert match is not None
    call.ok("drawing", action="create")

    call.ok("export", action="dxf", face=match.group(1), path="outline.dxf")
    assert len(adapter.outlines) == 1 and adapter.exported == []  # face -> the outline, not the sheet

    call.err("export", action="dxf", flat="s1", path="flat.dxf")  # not an unfold step: refused before any writing
    assert len(adapter.outlines) == 1 and adapter.exported == []

    call.ok("export", action="dxf", path="sheet.dxf")  # neither -> the sheet
    assert [(r.format, Path(r.path).name) for r in adapter.exported] == [("dxf", "sheet.dxf")]
    assert len(adapter.outlines) == 1

    (settings.workspace / "sheet.dxf").write_text("mine", encoding="utf-8")  # the overwrite rule holds for the sheet
    assert "already exists" in call.err("export", action="dxf", path="sheet.dxf")
    assert (settings.workspace / "sheet.dxf").read_text(encoding="utf-8") == "mine"


def test_dxf_with_neither_and_no_sheet_names_both_ways(make: MakeSession) -> None:
    adapter = SheetAndOutlineFake()
    adapter.has_sheet = False
    call = Caller(make(adapter))
    plate(call)
    error = call.err("export", action="dxf")
    assert "face=" in error and "flat=" in error and "drawing sheet" in error
    assert 'export(action="dxf", face="face:1"' in error and 'drawing(action="create")' in error
    assert adapter.exported == [] and adapter.outlines == []
