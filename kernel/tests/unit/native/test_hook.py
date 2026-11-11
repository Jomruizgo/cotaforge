"""The ``PreToolUse`` hook: hook JSON on stdin in, hook JSON on stdout out (and nothing at all when there is
nothing to say, or the input is not what we expect)."""

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from cotaforge.native import hook


def run_hook(payload: object, environ: dict[str, str] | None = None) -> tuple[int, str]:
    stdin = io.StringIO(payload if isinstance(payload, str) else json.dumps(payload))
    stdout = io.StringIO()
    code = hook.main(stdin, stdout, environ if environ is not None else {})
    return code, stdout.getvalue()


def payload(code: str, cwd: str = "/work/design") -> dict[str, object]:
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": "mcp__cad__run_native",
        "cwd": cwd,
        "tool_input": {"code": code, "intent": "test"},
    }


def test_risky_code_makes_the_hook_ask_with_one_line_per_finding() -> None:
    status, out = run_hook(payload("import os\nopen('/tmp/x', 'w')\n"))
    assert status == 0
    answer = json.loads(out)
    specific = answer["hookSpecificOutput"]
    assert specific["hookEventName"] == "PreToolUse"
    assert specific["permissionDecision"] == "ask"
    reason = specific["permissionDecisionReason"]
    assert "line 1:" in reason
    assert "line 2:" in reason
    assert "'/tmp/x'" in reason


def test_harmless_code_prints_nothing() -> None:
    assert run_hook(payload("from FreeCAD import Vector\nimport math\nv = Vector(1, 0, 0)\n")) == (0, "")


def test_workspace_comes_from_the_environment_first() -> None:
    code = "open('/elsewhere/a.txt', 'w')\n"
    _, out_env = run_hook(payload(code), {"COTAFORGE_WORKSPACE": "/elsewhere"})
    assert out_env == ""
    _, out_cwd = run_hook(payload(code, cwd="/work/design"))
    assert out_cwd != ""


def test_workspace_falls_back_to_the_hook_cwd() -> None:
    assert run_hook(payload("open('/work/design/a.txt', 'w')\n", cwd="/work/design"))[1] == ""


def test_no_workspace_at_all_flags_every_write() -> None:
    data = {"tool_input": {"code": "open('/work/a.txt', 'w')\n"}}
    assert run_hook(data)[1] != ""


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "not json",
        "[]",
        "null",
        '{"tool_input": null}',
        '{"tool_input": {"code": 42}}',
        '{"tool_input": {}}',
        '{"tool_input": "import os"}',
    ],
)
def test_malformed_input_prints_nothing_and_exits_zero(bad: str) -> None:
    assert run_hook(bad) == (0, "")


def test_a_fragment_that_does_not_parse_prints_nothing() -> None:
    assert run_hook(payload("import os\ndef broken(:\n")) == (0, "")


def test_module_entry_point_end_to_end(tmp_path: Path) -> None:
    """``python -m cotaforge.native.hook`` as Claude Code runs it: real stdin/stdout, exit status 0."""
    env = {"COTAFORGE_WORKSPACE": str(tmp_path), "PATH": "", "SYSTEMROOT": ""}
    risky = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "cotaforge.native.hook"],
        input=json.dumps(payload("import subprocess\n")),
        capture_output=True,
        text=True,
        env={**env, "PYTHONPATH": str(Path(hook.__file__).resolve().parents[2])},
        check=False,
    )
    assert risky.returncode == 0
    assert json.loads(risky.stdout)["hookSpecificOutput"]["permissionDecision"] == "ask"
    quiet = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "cotaforge.native.hook"],
        input="garbage",
        capture_output=True,
        text=True,
        env={**env, "PYTHONPATH": str(Path(hook.__file__).resolve().parents[2])},
        check=False,
    )
    assert (quiet.returncode, quiet.stdout) == (0, "")


def test_the_save_calls_of_the_cad_come_as_an_argument_of_the_hook_command() -> None:
    assert hook.save_methods_of(["--save-methods=SaveAs3,SaveAs4", "other"]) == ["SaveAs3", "SaveAs4"]
    assert hook.save_methods_of([]) == []
    risky = payload("model.Extension.SaveAs3('/tmp/a.part', 0, 1)\n")
    assert run_hook(risky) == (0, "")  # the hook does not know SaveAs3 without being told
    stdin, stdout = io.StringIO(json.dumps(risky)), io.StringIO()
    assert hook.main(stdin, stdout, {}, ["--save-methods=SaveAs3"]) == 0
    assert json.loads(stdout.getvalue())["hookSpecificOutput"]["permissionDecision"] == "ask"
