"""FreeCADSession: one worker's lifecycle as a context manager."""

from __future__ import annotations

from typing import Any

from cotaforge.adapters.freecad.adapter import FreeCADAdapter
from cotaforge.contract import ConnectRequest, Empty

__all__ = ["FreeCADSession"]


class FreeCADSession:
    def __init__(self, **adapter_kwargs: Any) -> None:
        self._adapter = FreeCADAdapter(**adapter_kwargs)

    @property
    def adapter(self) -> FreeCADAdapter:
        return self._adapter

    def __enter__(self) -> FreeCADAdapter:
        self._adapter.connect(ConnectRequest(operation_id="connect", timeout_s=self._adapter.timeout_s))
        return self._adapter

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        try:
            self._adapter.disconnect(Empty(operation_id="disconnect"))
        except Exception:
            # disconnect() already killed the worker; never mask the body's own exception with ours.
            if exc_type is None:
                raise
