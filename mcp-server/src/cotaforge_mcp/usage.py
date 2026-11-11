"""The native-usage counter: one JSON line per ``run_native`` call in ``native-usage.jsonl`` of the design space.

It exists to decide what deserves an optimised op: how often the escape hatch is used, how often it fails and what
for. The code itself is NOT stored (only its size and a hash, to tell repeated fragments apart); the intent is."""

import contextlib
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

USAGE_FILENAME = "native-usage.jsonl"


def usage_path(workspace: Path) -> Path:
    return workspace / USAGE_FILENAME


def record_native_call(
    workspace: Path,
    *,
    code: str,
    intent: str,
    ok: bool,
    rolled_back: bool,
    duration_ms: float,
    error_type: str | None = None,
) -> None:
    """Append one line; a counter must never break the call it counts, so a write failure is swallowed."""
    line = {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "ok": ok,
        "rolled_back": rolled_back,
        "intent": intent,
        "code_sha256": hashlib.sha256(code.encode("utf-8")).hexdigest(),
        "code_chars": len(code),
        "duration_ms": round(duration_ms, 1),
    }
    if error_type:
        line["error"] = error_type
    with contextlib.suppress(OSError):
        workspace.mkdir(parents=True, exist_ok=True)
        with usage_path(workspace).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, sort_keys=True) + "\n")
