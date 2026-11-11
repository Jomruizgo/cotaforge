"""A tiny system-Python echo worker for generic ``WorkerProcess`` tests (CAD-agnostic transport,
``cotaforge.adapters.rpc``).

Run as ``python echo_worker.py <mode>``. Responses go to the fd named by ``COTAFORGE_RPC_FD`` when it is
set, otherwise to stdout prefixed with ``@cadh `` — the fallback channel used when a pipe fd cannot cross
a process boundary (e.g. WSL interop to a Windows worker). Modes
script one behaviour each so ``WorkerProcess`` can be exercised without any real CAD application.
"""

import json
import os
import subprocess
import sys
import time
from typing import Any

HELLO = {
    "hello": "echo-worker",
    "python": sys.version.split()[0],
    "pid": os.getpid(),
    "rpc_fd_env": os.environ.get("COTAFORGE_RPC_FD"),
}


class Channel:
    def __init__(self) -> None:
        fd = os.environ.get("COTAFORGE_RPC_FD")
        self._prefix = "" if fd else "@cadh "
        self._out = os.fdopen(int(fd), "w", buffering=1, encoding="utf-8") if fd else sys.stdout

    def send_raw(self, text: str) -> None:
        self._out.write(self._prefix + text + "\n")
        self._out.flush()

    def send(self, message: dict[str, Any]) -> None:
        self.send_raw(json.dumps(message))


def noise(enabled: bool) -> None:
    if enabled:
        sys.stdout.write("worker noise on stdout\n")
        sys.stdout.flush()
        sys.stderr.write("worker noise on stderr\n")
        sys.stderr.flush()


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "ok"
    delay_s = float(os.environ.get("ECHO_WORKER_DELAY_S", "5"))
    noisy = mode in ("noise", "stdout_fallback")
    channel = Channel()
    noise(noisy)
    if mode == "no_hello":
        time.sleep(delay_s)
        return
    channel.send({"id": 0, "ok": True, "result": HELLO})
    noise(noisy)
    if mode == "orphan_pipe":
        # a process the worker started inherits the reply pipe (stdout): when the worker dies the pipe does not end
        subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])  # noqa: S603
    if mode == "notify":
        # Pushed before any request arrives: exercises a notification listener registered before start()
        # still catching one that lands ahead of the first call().
        channel.send({"method": "tick", "params": {"n": 1}})
    first_request = True
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        request = json.loads(line)
        request_id, method, params = request["id"], request["method"], request.get("params", {})
        if method == "disconnect":
            channel.send({"id": request_id, "ok": True, "result": {}})
            return
        if mode == "notify" and first_request:
            # Interleaved between the request and its reply: must not disturb call()'s id matching.
            channel.send({"method": "tick", "params": {"n": 2}})
        if first_request:
            first_request = False
            if mode == "garbage":
                channel.send_raw("this is not json")
                continue
            if mode == "delay":
                time.sleep(delay_s)
            if mode == "orphan_pipe":
                time.sleep(60)  # still working when the test kills it
            if mode == "exit_mid_call":
                sys.exit(3)
            if mode == "wrong_id":
                channel.send({"id": request_id + 1, "ok": True, "result": {"echo": params}})
                continue
            if mode == "error":
                channel.send(
                    {
                        "id": request_id,
                        "ok": False,
                        "error": {
                            "code": "invalid_reference",
                            "message": "unknown handle",
                            "operation_id": params.get("operation_id"),
                            "details": {},
                            "recoverable": False,
                        },
                    }
                )
                continue
        channel.send({"id": request_id, "ok": True, "result": {"echo": params}})
        noise(noisy)
    # stdin closed: "hang_on_stop" ignores EOF so the caller's stop() must force-kill the process.
    if mode == "hang_on_stop":
        time.sleep(delay_s)


if __name__ == "__main__":
    main()
