"""Smoke test of a staged or installed cotaforge. Run it with the interpreter under test:

    <app>\\python\\python.exe -I packaging\\windows\\smoke.py [--adapter fake|freecad] [--freecad-root DIR]

It uses only temporary folders (the design space gets a space in its name on purpose, and COTAFORGE_HOME is
temporary, so the real user profile is never touched) and checks, in order:

1. ``cotaforge version`` runs;
2. ``cotaforge init`` writes a ``.mcp.json`` that points at this very interpreter;
3. the ``cad`` server declared there starts over stdio and lists its tools;
4. with ``--adapter freecad``: a real operation (new document, a 10 x 20 x 30 box, its volume) runs in FreeCAD;
5. with ``--hook-shell BASH``: the ``run_native`` hook command written to ``settings.local.json`` runs in that shell.

Exits non-zero at the first failure.
"""

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def fail(message: str) -> None:
    print(f"SMOKE FAILED: {message}", file=sys.stderr)
    raise SystemExit(1)


def run_cli(*args: str, env: dict[str, str]) -> str:
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-X", "utf8", "-m", "cotaforge_mcp.cli", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )
    if result.returncode != 0:
        fail(f"cotaforge {' '.join(args)} exited {result.returncode}: {result.stderr.strip()}")
    return result.stdout


def same_file(a: str, b: str) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


async def talk_to_server(declared: dict, env: dict[str, str], adapter: str) -> None:
    params = StdioServerParameters(command=declared["command"], args=declared["args"], env=env)
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        names = {tool.name for tool in (await session.list_tools()).tools}
        if not {"op", "doc", "memory", "cad_guide"} <= names:
            fail(f"the server lists unexpected tools: {sorted(names)}")
        print(f"ok: the cad server started and lists {len(names)} tools")
        if adapter != "freecad":
            return
        made = await session.call_tool("doc", {"action": "new", "name": "smoke"})
        if made.isError:
            fail(f"doc new: {made.content}")
        box = await session.call_tool("op", {"name": "box", "params": {"size": [10, 20, 30]}, "intent": "smoke test"})
        text = "".join(getattr(item, "text", "") for item in box.content)
        if box.isError or "6000" not in text:
            fail(f"op box did not report a volume of 6000 mm3: {text}")
        print("ok: FreeCAD built a 10 x 20 x 30 box, volume 6000 mm3")


def check_hook(settings_path: Path, shell: str, folder: Path, env: dict[str, str]) -> None:
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    (entry,) = [e for e in settings["hooks"]["PreToolUse"] if e["matcher"] == "mcp__cad__run_native"]
    command = entry["hooks"][0]["command"]
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "mcp__cad__run_native",
        "cwd": str(folder),
        "tool_input": {"code": "import os; os.system('x')", "intent": "smoke test"},
    }
    result = subprocess.run(  # noqa: S603
        [shell, "-c", command],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )
    if result.returncode != 0:
        fail(f"the hook command {command!r} exited {result.returncode}: {result.stderr.strip()}")
    decision = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"]
    if decision != "ask":
        fail(f"the hook should ask about dangerous code, it said {decision!r}")
    print(f"ok: the hook command runs in {shell}: {command}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--adapter", choices=["fake", "freecad"], default="fake")
    parser.add_argument("--freecad-root", default="")
    parser.add_argument("--hook-shell", default="", help="path of a bash (Git Bash) to run the hook command with")
    args = parser.parse_args()

    scratch = Path(tempfile.mkdtemp(prefix="cotaforge smoke "))
    try:
        env = {**os.environ, "COTAFORGE_HOME": str(scratch / "home"), "HOME": str(scratch / "home")}
        env["USERPROFILE"] = env["HOME"]
        print(run_cli("version", env=env).strip())

        folder = scratch / "design space"
        cmd = ["init", str(folder), "--adapter", args.adapter]
        if args.freecad_root:
            cmd += ["--freecad-root", args.freecad_root]
        run_cli(*cmd, env=env)
        declared = json.loads((folder / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["cad"]
        if not same_file(declared["command"], sys.executable):
            fail(f".mcp.json points at {declared['command']!r}, not at {sys.executable!r}")
        print(f"ok: .mcp.json points at this interpreter ({declared['command']})")

        server_env = {**env, **declared["env"]}
        asyncio.run(talk_to_server(declared, server_env, args.adapter))
        if args.hook_shell:
            check_hook(folder / ".claude" / "settings.local.json", args.hook_shell, folder, env)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
