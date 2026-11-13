"""Every finding that holds the export says so in the review, in one place: ``warning`` alone reads as optional."""

import sys
from pathlib import Path

from cotaforge.contract import DrawingFinding

from cotaforge_mcp import drawing_gate as gate
from cotaforge_mcp import drawing_requirements as reqs

WORKER_DIR = Path(__file__).resolve().parents[3] / "kernel" / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import sheet_checks  # noqa: E402


def test_every_measured_finding_is_reported_as_blocking_the_export() -> None:
    for code in sorted(reqs.MEASURED_FINDINGS):
        assert code not in sheet_checks.NON_BLOCKING, f"{code} is a measurement the export waits for"
        row = {"code": code}
        sheet_checks.assign_ids([row])
        assert row["blocking"] is True, code
        for severity in ("warning", "info"):
            finding = DrawingFinding(code=code, severity=severity, message="m", blocking=row["blocking"])
            assert gate.severity_label(finding) == "BLOCKS EXPORT", (code, severity)


def test_a_notice_that_does_not_hold_the_export_says_that_too() -> None:
    finding = DrawingFinding(code="window_differs", severity="info", message="m", blocking=False)
    assert gate.severity_label(finding) == "info (does not block export)"
    assert "window_differs" in sheet_checks.NON_BLOCKING
