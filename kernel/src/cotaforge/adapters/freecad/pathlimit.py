"""Windows path-length guard for paths handed to FreeCAD.

FreeCAD writes its temporary and backup files next to the target, and its file layer is bound by ``MAX_PATH``
(260 characters including the terminating NUL) on Windows: a target that itself fits can still fail with
``OSError: Failed to open file`` once the temporary name is appended (seen with a 230-character path).

Measured on Windows 11 with FreeCAD 1.1.3 (``doc.saveAs`` then ``doc.save`` at growing lengths): 220 characters
save, 223 fail with ``Failed to open file``. The worker first writes ``.<name>.tmp.FCStd`` (5 more characters than
``<name>.FCStd``, see ``worker/persist.py``), so its targets fail from about 216; the margin of 50 refuses from
``SAFE_PATH_LENGTH`` (210), a few characters short of that. Whether FreeCAD honours Windows long paths when they are
enabled was not verified, so the advice is only to shorten the path.
"""

from __future__ import annotations

import sys
from pathlib import Path

MAX_PATH = 260
SUFFIX_MARGIN = 50
SAFE_PATH_LENGTH = MAX_PATH - SUFFIX_MARGIN


def _is_windows() -> bool:
    return sys.platform == "win32"


def path_length_problem(path: str | Path, what: str, *, windows: bool | None = None) -> str | None:
    """A message when ``path`` is too long to hand to FreeCAD on Windows, else ``None``.

    ``windows`` forces the platform (tests); by default it is the running OS. Never fires elsewhere."""
    if not (_is_windows() if windows is None else windows):
        return None
    text = str(path)
    if len(text) < SAFE_PATH_LENGTH:
        return None
    return (
        f"{what} is too long for FreeCAD on Windows: {text} ({len(text)} characters; the limit is "
        f"{SAFE_PATH_LENGTH - 1}, because FreeCAD writes temporary and backup files next to it and Windows stops at "
        f"{MAX_PATH}). Use a shorter folder or file name, or move the work to a shorter path "
        "(for example a subst drive, keeping that drive letter in the path)."
    )
