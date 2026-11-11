"""A system-Python stand-in for the FreeCAD worker.

Run as ``python fake_worker.py <mode>``. Responses go to the fd named by ``COTAFORGE_RPC_FD`` when it is
set, otherwise to stdout prefixed with ``@cadh `` (the stdout fallback). Modes script one misbehaviour
each so that ``WorkerProcess`` and ``FreeCADAdapter`` can be tested without FreeCAD.
"""

import json
import os
import sys
import time
from typing import Any

HELLO = {
    "hello": "cotaforge-freecad-worker",
    "freecad": "0.0-fake",
    "python": sys.version.split()[0],
    "pid": os.getpid(),
}

INSTALL = "Install it in FreeCAD: Tools > Addon manager > search 'SheetMetal' > Install, then restart FreeCAD."
SHEET_METAL_HELLOS = {  # what the real worker's hello says about the add-on, one mode per answer
    "sheet_metal": {"available": True, "version": "0.8.26", "reason": "", "install": INSTALL},
    "no_sheet_metal": {
        "available": False,
        "version": None,
        "reason": "the SheetMetal add-on is not installed",
        "install": INSTALL,
    },
    "broken_sheet_metal": {
        "available": False,
        "version": "0.8.26",
        "reason": "the SheetMetal add-on is installed but cannot be loaded (ImportError: No module named PySide)",
        "install": INSTALL,
    },
}

PLATE_VOLUME = 48000.0

SELECTION_NOTIFICATION = {
    "entries": [
        {
            "ref": {
                "adapter": "freecad",
                "token": "doc:1|body:1|face:3",
                "signature": {"kind": "face", "surface": "plane", "normal": [0.0, 0.0, 1.0], "area_mm2": 100.0},
            },
            "kind": "face",
            "label": "Pad · Face6",
            "owner_label": "Pad",
            "body": {"id": "body:1"},
            "feature": {"id": "feature:1"},
        }
    ]
}

MANUAL_EDIT_NOTIFICATION = {
    "artefact_kind": "feature",
    "ref": "feature:1",
    "by": "engineer",
    "reason": "hand edit detected in the FreeCAD GUI (Pad)",
    "changes": ["Length: 10.0 -> 15.0"],
    "document": "plate",
    "name": "Pad",
    "action": "edited",
}


def canned_result(method: str, params: dict[str, Any]) -> dict[str, Any]:
    """A result of the right JSON shape for every protocol method used by the client tests."""
    operation_id = params.get("operation_id", "op")
    if method == "canary":
        return {
            "healthy": True,
            "checks": {"plane": True, "box": True, "normal": True, "undo": True},
            "duration_ms": 1.5,
        }
    if method == "validate_api":
        return {"ok": True, "problems": []}
    if method == "new_document":
        return {"handle": {"id": "doc:1"}, "kind": params["kind"], "name": params["name"], "units": "mm"}
    if method in ("set_active_document", "active_document"):
        return {"handle": {"id": "doc:1"}, "kind": "part", "name": "plate", "units": "mm"}
    if method == "sketch":
        return {
            "sketch": {"id": "sketch:1"},
            "frame": {"origin": [0, 0, 0], "u": [1, 0, 0], "v": [0, 1, 0], "n": [0, 0, 1]},
            "profile_areas_mm2": [6000.0],
        }
    if method == "extrude":
        return {
            "operation_id": operation_id,
            "feature": {"id": "feature:1"},
            "rebuild_ok": True,
            "created_faces": [{"id": f"face:{i}"} for i in range(1, 7)],
            "created_edges": [{"id": f"edge:{i}"} for i in range(1, 13)],
            "created_bodies": [{"id": "body:1"}],
            "duration_ms": 12.0,
        }
    if method == "mass_properties":
        return {"volume_mm3": PLATE_VOLUME, "centroid": [50.0, 30.0, 4.0], "surface_mm2": 14560.0}
    if method == "bounding_box":
        return {"min": [0, 0, 0], "max": [100, 60, 8]}
    if method == "undo_last":
        if operation_id == "warn":  # a revert that could not honour some hand work: the worker adds ``warnings``
            return {"reverted": {"id": "feature:1"}, "warnings": ["hand edit of Box.Placement discarded"]}
        return {"reverted": {"id": "feature:1"}}
    if method == "edit_feature":  # echoes what it received, so the client test can see the request survive the wire
        return {
            "rebuild_ok": True,
            "applied": not params.get("dry_run", False),
            "dry_run": bool(params.get("dry_run", False)),
            "recomputed": [e["feature"] for e in params["edits"]],
            "refused": {},
            "health": [],
            "delta": [],
            "flags": [],
            "refs": {"created": [], "removed": [], "modified": []},
            "duration_ms": 3.0,
            "warnings": [f"{e['kind']}:{','.join(sorted(e['request']))}" for e in params["edits"]],
        }
    if method == "save_document":  # echoes the stamp it received, so the client test sees it survive the wire
        return {
            "path": params["path"],
            "sha256": "0" * 64,
            "size_bytes": 10,
            "duration_ms": 1.0,
            "document_uid": "uid",
            "cad_version": "1.1.3",
            "adapter_state": {"schema": "freecad-registry/1"},
            "warnings": [f"{key}={value}" for key, value in sorted(params["stamp"].items())],
        }
    if method == "open_document":  # the worker hands the raw stamp metadata back; the client builds the stamp
        meta = {
            "cotaforge.schema": "1",
            "cotaforge.design_id": "d",
            "cotaforge.plan_id": "p",
            "cotaforge.plan_revision": "2",
            "cotaforge.save_seq": "3",
            "cotaforge.last_step_index": "4",
            "cotaforge.harness_version": "0.0.0",
            "cotaforge.plan_sha256": "a" * 64,
            "cotaforge.sidecar_sha256": "",
        }
        if params["path"].endswith("future.FCStd"):
            meta["cotaforge.schema"] = "99"
        if params["path"].endswith("plain.FCStd"):
            meta = {}
        return {
            "document": {"handle": {"id": "doc:1"}, "kind": "part", "name": "p", "units": "mm"},
            "stamp_meta": meta,
            "sha256": "1" * 64,
            "size_bytes": 20,
            "document_uid": "uid",
            "cad_version": "1.1.3",
            "rebind": {
                "bodies_ok": ["body:1"],
                "bodies_drifted": [],
                "records_unmatched": [],
                "objects_invalid": [],
                "foreign_objects": [],
                "notes": [params["adapter_state"]["schema"]],
            },
            "duration_ms": 2.0,
            "warnings": [],
        }
    if method == "measure_bodies":
        return {
            "bodies": [
                {
                    "body": {"id": "body:1"},
                    "label": "Cut",
                    "measure": {
                        "volume_mm3": 8.0,
                        "bbox": {"min": [0, 0, 0], "max": [2, 2, 2]},
                        "solids": 2,
                        "faces": 6,
                        "edges": 12,
                    },
                    "valid": True,
                }
            ],
            "cuts": [{"feature": {"id": "feature:3"}, "label": "Cut", "removed_mm3": 0.0}],
            "overlaps": [],
            "duration_ms": 1.0,
            "warnings": [],
        }
    if method == "export_macro":
        return {"script": "import FreeCAD as App, Part, Sketcher\ndoc = App.newDocument('plate')\n"}
    if method == "feature_tree":
        return {"nodes": []}
    if method == "import_geometry":
        return {
            "document": {"handle": {"id": "doc:1"}, "kind": "part", "name": params["name"], "units": "mm"},
            "bodies": [],
            "source_format": "step",
            "warnings": [],
        }
    if method == "show_entities":
        return {"shown": "highlight"}
    if method == "sketch_status":
        return {
            "sketch": {"id": "sketch:1"},
            "dof": 0,
            "geometry_count": 4,
            "constraint_count": 8,
            "fully_constrained": True,
            "over_constrained": False,
            "conflicts": [],
            "free_geometry": [],
            "solver_message": "",
        }
    return {}


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
        sys.stdout.write("Recompute...... (11 %)\n")
        sys.stdout.flush()
        sys.stderr.write("Looking for thread definitions in: /fake\n")
        sys.stderr.flush()


LONG_IMPORT_MODES = ("import_busy", "import_idle", "import_busy_forever")


def spin(seconds: float) -> None:
    """Use CPU for ``seconds`` of wall time."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        sum(range(2000))


def long_import(channel: Channel, mode: str, request_id: int, params: dict[str, Any]) -> None:
    """A long ``import_geometry``: ``import_busy`` reads for FAKE_WORKER_BUSY_S using CPU, then adopts 3 bodies (a
    ``progress`` notification each) and answers; ``import_idle`` sleeps FAKE_WORKER_DELAY_S without using any CPU;
    ``import_busy_forever`` never answers and never stops using CPU."""
    if mode == "import_idle":
        time.sleep(float(os.environ.get("FAKE_WORKER_DELAY_S", "5")))
    elif mode == "import_busy_forever":
        while True:
            spin(1.0)
    else:
        spin(float(os.environ.get("FAKE_WORKER_BUSY_S", "1.5")))
        for index in range(1, 4):
            progress = {"phase": "adopting", "current": index, "total": 3, "message": f"adopting body {index}/3"}
            channel.send({"method": "progress", "params": {"operation_id": params.get("operation_id"), **progress}})
            spin(0.1)
    channel.send({"id": request_id, "ok": True, "result": canned_result("import_geometry", params)})


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "ok"
    delay_s = float(os.environ.get("FAKE_WORKER_DELAY_S", "5"))
    noisy = mode in ("noise", "stdout_fallback")
    channel = Channel()
    noise(noisy)
    if mode == "no_hello":
        time.sleep(delay_s)
        return
    hello = dict(HELLO)
    if mode in SHEET_METAL_HELLOS:
        hello["sheet_metal"] = SHEET_METAL_HELLOS[mode]
    channel.send({"id": 0, "ok": True, "result": hello})
    noise(noisy)
    first_request = True
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request = json.loads(line)
        request_id, method, params = request["id"], request["method"], request.get("params", {})
        operation_id = params.get("operation_id")
        if method == "disconnect":
            channel.send({"id": request_id, "ok": True, "result": {}})
            return
        handshake = method in ("validate_api", "canary")
        if mode in LONG_IMPORT_MODES and method == "import_geometry":
            long_import(channel, mode, request_id, params)
            continue
        if mode == "unhealthy" and method == "canary":
            channel.send(
                {
                    "id": request_id,
                    "ok": True,
                    "result": {"healthy": False, "checks": {"plane": True, "box": False}, "duration_ms": 2.0},
                }
            )
            continue
        if mode == "problems" and method == "validate_api":
            channel.send(
                {"id": request_id, "ok": True, "result": {"ok": False, "problems": ["untested FreeCAD version 0.21"]}}
            )
            continue
        if first_request and not handshake:
            first_request = False
            if mode == "garbage":
                channel.send_raw("this is not json")
                continue
            if mode == "delay":
                time.sleep(delay_s)
            if mode == "exit_mid_call":
                sys.exit(3)
            if mode == "wrong_id":
                channel.send({"id": request_id + 1, "ok": True, "result": canned_result(method, params)})
                continue
            if mode == "error":
                channel.send(
                    {
                        "id": request_id,
                        "ok": False,
                        "error": {
                            "code": "invalid_reference",
                            "message": "unknown handle face:99",
                            "operation_id": operation_id,
                            "details": {"handle": "face:99"},
                            "recoverable": False,
                        },
                    }
                )
                continue
            if mode == "bad_error":
                channel.send({"id": request_id, "ok": False, "error": {"message": "no code here"}})
                continue
            if mode == "malformed_result":
                channel.send({"id": request_id, "ok": True, "result": {"unexpected": "shape"}})
                continue
        channel.send({"id": request_id, "ok": True, "result": canned_result(method, params)})
        if mode == "notify" and method == "new_document":
            channel.send({"method": "selection_changed", "params": SELECTION_NOTIFICATION})
        if mode == "notify" and method == "extrude":
            channel.send({"method": "manual_edit", "params": MANUAL_EDIT_NOTIFICATION})
        noise(noisy)


if __name__ == "__main__":
    main()
