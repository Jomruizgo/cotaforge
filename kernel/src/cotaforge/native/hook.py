"""``PreToolUse`` hook for ``run_native``: ``python -m cotaforge.native.hook``.

Reads the hook JSON Claude Code writes on stdin (the tool input carries ``code``), scans the fragment with
``cotaforge.native.risks.scan_risks`` and, when it finds anything, prints the hook answer that turns the call into a
permission question::

    {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                            "permissionDecisionReason": "<one line per finding>"}}

With no findings it prints nothing (the normal permission rules decide). It always exits 0 and never raises: malformed
input, a missing ``code`` or any internal error also print nothing, so a broken hook can never block a modelling step.
The scan is advisory (see ``risks``): it asks, it does not enforce.

The workspace writes are allowed in comes from ``COTAFORGE_WORKSPACE``; without it, the ``cwd`` field of the hook JSON.
"""

import json
import os
import sys
from collections.abc import Collection, Mapping, Sequence
from pathlib import Path
from typing import TextIO

from cotaforge.native.risks import scan_risks

ENV_WORKSPACE = "COTAFORGE_WORKSPACE"
HEADER = "run_native: the fragment does something worth a look (advisory scan, not a sandbox)"


SAVE_METHODS_FLAG = "--save-methods="
"""``--save-methods=a,b``: the calls that save a document in the CAD (``Adapter.save_methods``), beyond FreeCAD's."""


def decide(payload: object, environ: Mapping[str, str], save_methods: Collection[str] = ()) -> dict[str, object] | None:
    """The hook answer for one parsed hook payload, or ``None`` when there is nothing to say."""
    if not isinstance(payload, dict):
        return None
    tool_input = payload.get("tool_input")
    code = tool_input.get("code") if isinstance(tool_input, dict) else None
    if not isinstance(code, str):
        return None
    workspace_text = environ.get(ENV_WORKSPACE) or payload.get("cwd")
    workspace = Path(workspace_text) if isinstance(workspace_text, str) and workspace_text else None
    risks = scan_risks(code, workspace, save_methods)
    if not risks:
        return None
    reason = "\n".join([HEADER, *(risk.format() for risk in risks)])
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": reason,
        }
    }


def save_methods_of(argv: Sequence[str]) -> list[str]:
    """The names a ``--save-methods=a,b`` argument gives (nothing without it)."""
    names: list[str] = []
    for arg in argv:
        if arg.startswith(SAVE_METHODS_FLAG):
            names.extend(name.strip() for name in arg[len(SAVE_METHODS_FLAG) :].split(",") if name.strip())
    return names


def main(
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    environ: Mapping[str, str] | None = None,
    argv: Sequence[str] | None = None,
) -> int:
    source = stdin if stdin is not None else sys.stdin
    sink = stdout if stdout is not None else sys.stdout
    try:
        answer = decide(
            json.loads(source.read()),
            environ if environ is not None else os.environ,
            save_methods_of(sys.argv[1:] if argv is None else argv),
        )
        if answer is not None:
            sink.write(json.dumps(answer) + "\n")
            sink.flush()
    except Exception:  # noqa: BLE001 - a hook must never break the tool call it watches
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
