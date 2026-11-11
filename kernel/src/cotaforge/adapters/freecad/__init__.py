"""FreeCAD adapter: the client half runs here, the worker half runs inside
``freecadcmd`` (``worker/``) and is never imported by the harness."""

from cotaforge.adapters.freecad.adapter import FreeCADAdapter
from cotaforge.adapters.freecad.rpc import (
    RpcFailure,
    WorkerLost,
    WorkerProcess,
    WorkerTimeout,
    find_freecad_root,
)
from cotaforge.adapters.freecad.session import FreeCADSession

__all__ = [
    "FreeCADAdapter",
    "FreeCADSession",
    "RpcFailure",
    "WorkerLost",
    "WorkerProcess",
    "WorkerTimeout",
    "find_freecad_root",
]
