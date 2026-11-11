"""Nothing is cut silently: a long ``run_native`` output, error or list of warnings, and a long validity report, show
their start and say how much was left out and in which file of the design space the whole text is."""

import re
from pathlib import Path
from typing import Any

from cotaforge.adapters.fake import FakeAdapter
from cotaforge.contract import NativeError, RunNativeResult, ValidityResult

from cotaforge_mcp.overflow import reports_dir
from tests.conftest import Caller, MakeSession, NativeFake, plate

LONG = "".join(f"line {i:04d} " + "x" * 40 + "\n" for i in range(300))


class ChattyFake(NativeFake):
    """``"long"`` prints 300 lines, ``"flood"`` more than the CAD keeps, ``"warn"`` gives 9 warnings, ``"fail-long"``
    fails with a long message; anything else is the plain stand-in."""

    def run_native(self, request: Any) -> Any:
        code = request.code.strip()
        if code == "long":
            return RunNativeResult(ok=True, stdout=LONG)
        if code == "flood":
            return RunNativeResult(ok=True, stdout="y" * 65536, stdout_dropped_chars=1234)
        if code == "warn":
            return RunNativeResult(ok=True, warnings=tuple(f"warning number {i}" for i in range(9)))
        if code == "fail-long":
            error = NativeError(type="ValueError", message="m" * 3000, line=2)
            return RunNativeResult(ok=False, error=error, rolled_back=True)
        return super().run_native(request)


class InvalidFake(FakeAdapter):
    """A part that reports ``problems`` once a test sets them."""

    problems: tuple[str, ...] = ()

    def is_valid(self, request: Any) -> ValidityResult:
        if not self.problems:
            return super().is_valid(request)
        return ValidityResult(valid=False, problems=self.problems)


def report_path(text: str) -> Path:
    # Non-greedy up to ".txt": a Windows temp path can hold spaces (C:\Users\First Last\...).
    match = re.search(r"full (?:text|report) in (.+?\.txt)", text)
    assert match, text
    return Path(match.group(1))


def test_a_long_output_shows_its_start_and_says_how_much_is_left_and_where(make: MakeSession, tmp_path: Path) -> None:
    call = Caller(make(ChattyFake()))
    plate(call)
    text = call.ok("run_native", code="long", intent="dump")
    assert "output: line 0000" in text and "line 0299" not in text
    full = LONG.strip()
    lines_left = full.count("\n") - full[:600].count("\n")
    assert f"[output: {len(full) - 600} more characters ({lines_left} more lines) omitted; full text in " in text
    path = report_path(text)
    assert path.parent == reports_dir(tmp_path) and LONG.strip() in path.read_text(encoding="utf-8")
    assert len(text) < 2000  # the default answer stays short


def test_output_the_cad_did_not_keep_is_counted(make: MakeSession) -> None:
    call = Caller(make(ChattyFake()))
    plate(call)
    text = call.ok("run_native", code="flood")
    assert "1234 more characters were printed but not kept at all (the CAD keeps the first 65536)" in text
    assert "y" * 65536 in report_path(text).read_text(encoding="utf-8")


def test_warnings_beyond_the_first_ones_are_counted_and_kept(make: MakeSession) -> None:
    call = Caller(make(ChattyFake()))
    plate(call)
    text = call.ok("run_native", code="warn")
    assert "warning: warning number 4" in text and "warning number 5" not in text
    assert "warning: 4 more warnings omitted; full report in" in text
    assert "warning number 8" in report_path(text).read_text(encoding="utf-8")


def test_a_long_error_message_is_shortened_with_a_pointer(make: MakeSession) -> None:
    call = Caller(make(ChattyFake()))
    plate(call)
    text = call.err("run_native", code="fail-long")
    assert "error: ValueError (line 2): " + "m" * 1000 + "\n[error: 2000 more characters" in text
    assert "m" * 3000 in report_path(text).read_text(encoding="utf-8")


def test_a_short_result_writes_no_report(make: MakeSession, tmp_path: Path) -> None:
    call = Caller(make(ChattyFake()))
    plate(call)
    text = call.ok("run_native", code="box 2")
    assert "omitted" not in text and not reports_dir(tmp_path).exists()


def test_a_long_validity_report_is_summarised_with_the_count_and_the_file(make: MakeSession) -> None:
    cad = InvalidFake()
    call = Caller(make(cad))
    plate(call)
    cad.problems = tuple(f"body:{i}: shape is not valid" for i in range(1, 8))
    for text in (call.ok("doc", action="status"), call.ok("check")):
        assert "7 problems: body:1: shape is not valid; body:2: shape is not valid; body:3: shape is not valid" in text
        assert "4 more problems omitted; full report in" in text
        assert "body:7: shape is not valid" in report_path(text).read_text(encoding="utf-8")
