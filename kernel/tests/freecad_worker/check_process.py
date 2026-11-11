"""Process model: spawn the worker like the client does, hello on the pipe fd, requests on
stdin, exit on EOF / disconnect; stdout fallback with the ``@cadh `` prefix when the fd variable is absent."""

import json
import os
import subprocess
import sys
import tempfile
import time

from harness import is_supported_freecad_version

REPO = os.environ.get("COTAFORGE_REPO", os.getcwd())
WORKER_DIR = os.path.join(REPO, "src", "cotaforge", "adapters", "freecad", "worker")
_CMD_NAME = "freecadcmd.exe" if sys.platform == "win32" else "freecadcmd"
FREECADCMD = os.path.join(os.path.dirname(sys.executable), _CMD_NAME)


def _freecadcmd() -> str:
    if os.path.isfile(FREECADCMD):
        return FREECADCMD
    root = os.environ.get("COTAFORGE_FREECAD_ROOT", os.path.expanduser("~/Applications/freecad-1.1.3/squashfs-root"))
    rel = ("bin", "FreeCADCmd.exe") if sys.platform == "win32" else ("usr", "bin", "freecadcmd")
    return os.path.join(root, *rel)


BOOTSTRAP = f"import sys; sys.path.insert(0, {WORKER_DIR!r}); import main; main.run()"


def _request(i: int, method: str, params: dict) -> bytes:  # type: ignore[type-arg]
    return (json.dumps({"id": i, "method": method, "params": params}) + "\n").encode()


def check_rpc_over_pipe_fd_and_exit_on_disconnect() -> None:
    if sys.platform == "win32":
        # A pipe fd cannot be inherited by a child process on Windows (Python's own `subprocess.Popen`
        # documents `pass_fds` as POSIX-only), which is exactly why the production client
        # (`adapters/freecad/adapter.py::connect`) never uses this transport there
        # (`use_fd_channel=sys.platform != "win32"`) -- it always takes the stdout-fallback path this
        # module's other check already covers. Nothing to verify here on this platform.
        return
    r, w = os.pipe()
    log = tempfile.TemporaryFile()
    started = time.perf_counter()
    proc = subprocess.Popen(
        [_freecadcmd(), "-c", BOOTSTRAP],
        stdin=subprocess.PIPE,
        stdout=log,
        stderr=subprocess.STDOUT,
        pass_fds=(w,),
        env={**os.environ, "COTAFORGE_RPC_FD": str(w)},
        cwd=WORKER_DIR,
    )
    os.close(w)
    reader = os.fdopen(r, "r")
    try:
        hello = json.loads(reader.readline())
        startup_ms = (time.perf_counter() - started) * 1000
        assert hello["id"] == 0 and hello["ok"] is True, hello
        assert hello["result"]["hello"] == "cotaforge-freecad-worker" and hello["result"]["pid"] == proc.pid
        assert is_supported_freecad_version(hello["result"]["freecad"])
        assert hello["result"]["python"].startswith("3.11")
        assert proc.stdin is not None
        proc.stdin.write(_request(1, "validate_api", {"operation_id": "v"}))
        proc.stdin.write(_request(2, "canary", {"operation_id": "c"}))
        proc.stdin.write(_request(3, "new_document", {"operation_id": "d", "kind": "part", "name": "p"}))
        proc.stdin.write(_request(4, "mass_properties", {"operation_id": "m"}))
        proc.stdin.write(_request(5, "face_info", {"operation_id": "q", "face": {"id": "face:1"}}))
        proc.stdin.write(_request(6, "box", {"operation_id": "b", "size": [1, 2, 3]}))
        proc.stdin.flush()
        responses = [json.loads(reader.readline()) for _ in range(6)]
        assert [r["id"] for r in responses] == [1, 2, 3, 4, 5, 6]
        assert responses[0]["result"] == {"ok": True, "problems": []}
        assert responses[1]["result"]["healthy"] is True
        assert responses[2]["result"]["handle"] == {"id": "doc:1"}
        assert responses[3]["result"]["volume_mm3"] == 0.0
        assert responses[4]["ok"] is False and responses[4]["error"]["code"] == "invalid_reference"
        assert responses[5]["ok"] is True and len(responses[5]["result"]["created_faces"]) == 6
        proc.stdin.write(_request(7, "disconnect", {"operation_id": "x"}))
        proc.stdin.flush()
        assert json.loads(reader.readline()) == {"id": 7, "ok": True, "result": {}}
        assert proc.wait(timeout=30) == 0
        assert reader.readline() == ""  # pipe closed by the worker exit
        log.seek(0)
        noise = log.read().decode(errors="replace")
        assert "@cadh" not in noise  # responses never travel on stdout when the fd is set
        sys.stderr.write(f"  worker startup {startup_ms:.0f} ms, log noise {len(noise)} bytes\n")
    finally:
        reader.close()
        if proc.poll() is None:
            proc.kill()


def check_stdout_fallback_prefix_and_eof_exit() -> None:
    env = {k: v for k, v in os.environ.items() if k != "COTAFORGE_RPC_FD"}
    proc = subprocess.Popen(
        [_freecadcmd(), "-c", BOOTSTRAP],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=env,
        cwd=WORKER_DIR,
    )
    payload = _request(1, "suppress_interaction", {"operation_id": "s"}) + b"\ngarbage line\n"
    out, _ = proc.communicate(payload, timeout=60)  # stdin EOF after the payload: the worker must exit on its own
    assert proc.returncode == 0
    lines = [line[len("@cadh ") :] for line in out.decode(errors="replace").splitlines() if line.startswith("@cadh ")]
    messages = [json.loads(line) for line in lines]
    assert messages[0]["id"] == 0 and messages[0]["result"]["hello"] == "cotaforge-freecad-worker"
    assert messages[1] == {"id": 1, "ok": True, "result": {}}
    assert messages[2]["ok"] is False and messages[2]["error"]["code"] == "internal" and messages[2]["id"] is None
    assert len(messages) == 3
