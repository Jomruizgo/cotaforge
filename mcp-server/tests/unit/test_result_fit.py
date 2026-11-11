"""A long result keeps the line that says where the full text is, the warnings and the notes: they come before the rows
(holes) or survive at the end of the cut (any list); and the ``valid:`` line never calls a body valid that was not
checked, nor a box real that leaves faces out. Over the fake."""

import json
from pathlib import Path
from typing import Any

from cotaforge.adapters.fake import FakeAdapter
from cotaforge.contract import BoundingBox, ImportGeometryResult, ValidityResult

from cotaforge_mcp.tools import _fitted
from tests.conftest import Caller, MakeSession
from tests.unit.test_import_and_holes import drill, plate


def test_the_pointer_to_the_full_list_of_holes_comes_before_the_rows(call: Caller, tmp_path: Path) -> None:
    plate(call)
    for index in range(40):
        drill(call, f"h{index}", 2, [-45 + 5 * (index % 19), -20 + 20 * (index // 19)])

    text = call.ok("inspect", action="holes")

    lines = text.splitlines()
    assert lines[0].startswith("holes: 40 in 1 body")
    assert "chars cut" not in text and "narrow the request" not in text
    pointer = next(i for i, line in enumerate(lines) if "more holes omitted; full report in" in line)
    assert pointer < next(i for i, line in enumerate(lines) if line.startswith("D2 "))
    report = next(tmp_path.glob("reports/holes-*.txt"))
    assert report.name in text and report.read_text(encoding="utf-8").count("D2 through") == 40


def test_a_cut_keeps_the_last_lines_and_names_a_file_with_everything(make: MakeSession, tmp_path: Path) -> None:
    session = make(FakeAdapter())
    rows = [f"row {i} " + "y" * 90 for i in range(80)]
    lines = ["header", *rows, "12 more things omitted; full report in reports/other.txt", "note: read the file"]

    reply = _fitted(session, "inspect-tree", lines, 2000)

    assert reply.text.splitlines()[0] == "header"
    assert "12 more things omitted; full report in reports/other.txt" in reply.text
    assert reply.text.splitlines()[-1] == "note: read the file"
    assert len(reply.text) < 2200 and "more lines omitted here" in reply.text
    assert not (tmp_path / "reports").exists()  # the tool named its own report: no second one

    plain = _fitted(session, "inspect-tree", ["header", *rows, "note: last"], 2000)

    assert plain.text.splitlines()[-1] == "note: last" and "full text in" in plain.text
    written = next((tmp_path / "reports").glob("inspect-tree-*.txt"))
    assert all(row in written.read_text(encoding="utf-8") for row in rows)


class CheckedFake(FakeAdapter):
    """A CAD whose validity check skipped some bodies, and whose box leaves out faces."""

    def __init__(
        self, unchecked: tuple[str, ...] = (), checked: int | None = None, left_out: tuple[str, ...] = ()
    ) -> None:
        super().__init__()
        self._unchecked, self._checked, self._left_out = unchecked, checked, left_out

    def is_valid(self, request: Any) -> ValidityResult:
        real = super().is_valid(request)
        return ValidityResult(
            valid=real.valid, problems=real.problems, unchecked=self._unchecked, checked=self._checked
        )

    def bounding_box(self, request: Any) -> BoundingBox:
        real = super().bounding_box(request)
        return real.model_copy(update={"left_out": self._left_out, "exact": not self._left_out})

    def import_geometry(self, request: Any) -> ImportGeometryResult:
        result = super().import_geometry(request)
        bodies = [b.model_copy(update={"bbox_faces_left_out": 2, "bbox_exact": False}) for b in result.bodies]
        return result.model_copy(update={"bodies": tuple(bodies)}) if self._left_out else result


def supplier_file(tmp_path: Path) -> None:
    (tmp_path / "part.step").write_text(json.dumps({"solids": [{"box": [10, 10, 10]}]}), encoding="utf-8")


def test_nothing_checked_is_not_valid_yes(make: MakeSession, tmp_path: Path) -> None:
    supplier_file(tmp_path)
    call = Caller(make(CheckedFake(unchecked=("body:1",), checked=0)))

    text = call.ok("doc", action="import", path="part.step", name="part")

    assert "valid: not checked (not checked: " in text and "valid: yes" not in text


def test_some_checked_says_for_how_many(make: MakeSession, tmp_path: Path) -> None:
    supplier_file(tmp_path)
    call = Caller(make(CheckedFake(unchecked=("body:9",), checked=7)))

    text = call.ok("doc", action="import", path="part.step", name="part")

    assert "valid: yes for the 7 checked bodies (not checked: body:9" in text


def test_a_box_that_leaves_faces_out_says_so_everywhere(make: MakeSession, tmp_path: Path) -> None:
    supplier_file(tmp_path)
    call = Caller(make(CheckedFake(left_out=("body:1: 2 faces",))))

    text = call.ok("doc", action="import", path="part.step", name="part")

    assert "warning: the box of " in text and "leaves out faces that have no real size" in text
    assert "(without 2 faces of no real size)" in text
    assert "without faces that have no real size: body:1: 2 faces" in text
    assert "note: sizes and corners are quick boxes" in text
    assert "note: the box leaves out faces that have no real size: body:1: 2 faces" in call.ok(
        "inspect", action="measure"
    )
