"""cotaforge.native: the CAD-agnostic side of ``run_native`` -- the static risk scan and the ``PreToolUse`` hook.

Standard library only (the hook starts on every ``run_native`` call and must stay fast and dependency-free).
"""

from cotaforge.native.risks import RISKY_MODULES, Risk, scan_risks

__all__ = ["RISKY_MODULES", "Risk", "scan_risks"]
