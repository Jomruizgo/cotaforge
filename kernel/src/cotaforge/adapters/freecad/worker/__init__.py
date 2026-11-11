"""FreeCAD worker: runs inside ``freecadcmd`` (bundled Python 3.11) and answers the newline-delimited JSON-RPC protocol.

The package imports only FreeCAD (``FreeCAD``, ``Part``, ``Sketcher``) and the standard library: no pydantic,
no ``cotaforge``. The client (``cotaforge.adapters.freecad``) never imports it; it only needs the directory.
"""
