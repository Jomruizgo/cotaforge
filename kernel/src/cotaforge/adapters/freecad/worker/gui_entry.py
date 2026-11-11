"""Entry script of the visible worker: ``usr/bin/freecad gui_entry.py`` (see ``rpc.worker_command``).

The GUI binary needs a positional script (``-c`` would switch it to console mode) and executes it as a
module named after the file with its directory on ``sys.path``, from inside the running Qt event loop.
``main.run()`` sends the hello, arms the stdin reader and returns; the event loop then services the
requests. The headless bootstrap (``freecadcmd -c "import main; main.run()"``) does not use this file.

Any failure while bootstrapping would otherwise surface as a modal dialog in the FreeCAD window and hang
the client until its connect timeout: log it and exit hard instead.
"""

import os
import sys
import traceback

try:
    if os.environ.get("COTAFORGE_WORKER_ISOLATED") == "1":
        # a profile of its own, empty: no recent files and no autosave (so no recovery data)
        import FreeCAD

        FreeCAD.ParamGet("User parameter:BaseApp/Preferences/RecentFiles").SetInt("RecentFiles", 0)
        FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Document").SetBool("AutoSaveEnabled", False)
    import main

    main.run()
except Exception:  # noqa: BLE001 - the client sees the traceback in the worker log, then session_lost
    sys.stderr.write(traceback.format_exc())
    sys.stderr.flush()
    os._exit(1)
