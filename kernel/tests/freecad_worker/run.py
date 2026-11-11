"""Worker check suite runner. Run from the repository root:

    freecadcmd -c "exec(open('tests/freecad_worker/run.py').read())"

Imports every ``check_*.py`` in this directory, runs each ``check_*`` function with a fresh worker state, prints
``PASS``/``FAIL`` per check and exits 1 on any failure. Plain ``assert``; no pytest (not importable in the bundled
Python). ``COTAFORGE_REPO`` overrides the repository root discovery (cwd and its parents).
"""

import importlib
import os
import sys
import time
import traceback

WORKER_REL = os.path.join("src", "cotaforge", "adapters", "freecad", "worker")
TESTS_REL = os.path.join("tests", "freecad_worker")


def find_repo_root() -> str:
    env = os.environ.get("COTAFORGE_REPO")
    candidates = [env] if env else []
    here = os.getcwd()
    while True:
        candidates.append(here)
        parent = os.path.dirname(here)
        if parent == here:
            break
        here = parent
    for c in candidates:
        if c and os.path.isfile(os.path.join(c, WORKER_REL, "main.py")):
            return c
    raise SystemExit("cannot find the cotaforge repository root (set COTAFORGE_REPO)")


def run_suite() -> int:
    root = find_repo_root()
    worker_dir = os.path.join(root, WORKER_REL)
    tests_dir = os.path.join(root, TESTS_REL)
    for path in (tests_dir, worker_dir):
        if path not in sys.path:
            sys.path.insert(0, path)
    os.environ["COTAFORGE_REPO"] = root
    import main  # noqa: PLC0415 - worker entry point

    only = os.environ.get("COTAFORGE_CHECK", "")
    failures = 0
    total = 0
    started_all = time.perf_counter()
    for filename in sorted(os.listdir(tests_dir)):
        if not (filename.startswith("check_") and filename.endswith(".py")):
            continue
        module = importlib.import_module(filename[:-3])
        for name in sorted(dir(module)):
            if not name.startswith("check_"):
                continue
            func = getattr(module, name)
            if not callable(func):
                continue
            label = f"{filename[:-3]}.{name}"
            if only and only not in label:
                continue
            total += 1
            main.reset()
            started = time.perf_counter()
            try:
                func()
            except Exception as exc:  # noqa: BLE001 - report and continue
                failures += 1
                sys.stderr.write(f"FAIL {label}: {type(exc).__name__}: {exc}\n")
                sys.stderr.write(traceback.format_exc())
                details = getattr(exc, "details", None)
                if isinstance(details, dict) and details.get("traceback"):
                    sys.stderr.write(str(details["traceback"]))
            else:
                sys.stderr.write(f"PASS {label} ({(time.perf_counter() - started) * 1000:.0f} ms)\n")
            sys.stderr.flush()
    sys.stderr.write(
        f"{total - failures}/{total} checks passed in {time.perf_counter() - started_all:.1f} s"
        + (f", {failures} FAILED\n" if failures else "\n")
    )
    sys.stderr.flush()
    return 1 if failures else 0


if run_suite():  # executed through exec(open(...).read()) from freecadcmd -c
    sys.exit(1)
