"""Pure execution half of ``run_native``: compile, run with captured stdout and a soft deadline, turn an
exception into ``{type, message, line, hint}``, and the advisory warnings. No FreeCAD import, so it is unit-testable
without FreeCAD (``tests/unit/freecad_client/test_native_exec.py``).

The user's code is compiled with the file name ``<run_native>``: every traceback frame with that file name belongs to
the caller's code and its ``tb_lineno`` is the 1-based line the caller wrote.

stdout and stderr are captured together (in order); stdin is empty so ``input()`` raises ``EOFError``.

The soft deadline is a ``sys.settrace`` hook that only watches frames of the user's code. It interrupts pure Python
loops (``while True: pass``) between two lines, raising ``NativeTimeout`` (a ``BaseException``: a snippet's own
``except Exception`` does not swallow it). It cannot interrupt a single long call into native code (an OCC boolean),
nor a snippet that swallows it with a bare ``except:``; the client-side RPC timeout is the hard backstop for those.
"""

import builtins
import difflib
import io
import re
import sys
import time
import traceback
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from types import CodeType
from typing import Any

USER_FILENAME = "<run_native>"
# Characters of output kept while running and sent back whole (the client decides how much of it to show and keeps
# the rest in a file); the rest of a print flood is counted in ``ExecOutcome.dropped``, not stored.
CAPTURE_HARD_LIMIT = 64 * 1024
MESSAGE_LIMIT = 8 * 1024  # characters of an error message sent back; a longer one says how much was cut
LARGE_CODE_LINES = 200
LARGE_CODE_CHARS = 10_000

TraceFn = Callable[[Any, str, Any], Any]


class NativeTimeout(BaseException):  # noqa: N818 - deliberately not an Exception subclass
    """The soft deadline of the step expired inside the user's code."""


class Capture(io.TextIOBase):
    """Stand-in for ``sys.stdout`` that stores at most ``CAPTURE_HARD_LIMIT`` characters (a runaway ``print``
    loop must not eat the worker's memory) and remembers how much it dropped."""

    encoding = "utf-8"

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._size = 0
        self.dropped = 0

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        room = CAPTURE_HARD_LIMIT - self._size
        if room > 0:
            kept = text[:room]
            self._parts.append(kept)
            self._size += len(kept)
            self.dropped += len(text) - len(kept)
        else:
            self.dropped += len(text)
        return len(text)

    def flush(self) -> None:
        return None

    def getvalue(self) -> str:
        return "".join(self._parts)


def capped(text: str, limit: int = MESSAGE_LIMIT) -> str:
    """At most ``limit`` characters of ``text``; a cut is never silent: the marker says how much was left out."""
    if len(text) <= limit:
        return text
    return text[:limit] + f" ...[{len(text) - limit} more characters cut]"


@dataclass(frozen=True)
class ExecOutcome:
    ok: bool
    stdout: str
    error: dict[str, Any] | None = None
    timed_out: bool = False
    exception: BaseException | None = field(default=None, repr=False)
    dropped: int = 0
    """Characters printed beyond ``CAPTURE_HARD_LIMIT``: counted, not kept."""


def compile_user_code(code: str) -> tuple[CodeType | None, dict[str, Any] | None]:
    """``(code_object, None)`` or ``(None, error_info)`` for a syntax error (nothing ran)."""
    try:
        return compile(code, USER_FILENAME, "exec"), None
    except SyntaxError as exc:
        return None, error_info(exc, ())
    except (ValueError, OverflowError, RecursionError) as exc:  # e.g. null bytes, absurd nesting
        return None, error_info(exc, ())


def _make_tracer(deadline: float) -> TraceFn:
    def local_trace(frame: Any, event: str, arg: Any) -> Any:
        if time.monotonic() > deadline:
            raise NativeTimeout
        return local_trace

    def global_trace(frame: Any, event: str, arg: Any) -> Any:
        if frame.f_code.co_filename == USER_FILENAME:
            if time.monotonic() > deadline:
                raise NativeTimeout
            return local_trace
        return None

    return global_trace


def execute(
    code_obj: CodeType,
    namespace: dict[str, Any],
    timeout_s: float,
    known_names: Iterable[str] = (),
    gui: bool = False,
) -> ExecOutcome:
    """Run ``code_obj`` in ``namespace`` with stdout captured and ``timeout_s`` as soft deadline (``gui``: see
    ``hint_for``)."""
    capture = Capture()
    old_stdout, old_stderr, old_stdin = sys.stdout, sys.stderr, sys.stdin
    old_trace = sys.gettrace()
    # stderr is merged into the captured output (in order); stdin is empty: in the worker it IS the request pipe,
    # so an ``input()`` in the fragment must fail with EOFError instead of swallowing the next request.
    sys.stdout = sys.stderr = capture
    sys.stdin = io.StringIO("")
    sys.settrace(_make_tracer(time.monotonic() + timeout_s))
    try:
        exec(code_obj, namespace)  # noqa: S102 - this is the whole point of run_native
    except NativeTimeout as exc:
        sys.settrace(old_trace)
        info = {
            "type": "Timeout",
            "message": f"the fragment did not finish within {timeout_s:g} s and was interrupted",
            "line": _user_line(exc.__traceback__),
            "hint": "Split the work into smaller steps, remove unbounded loops, or raise timeout_s.",
        }
        return ExecOutcome(False, capture.getvalue(), info, timed_out=True, exception=exc, dropped=capture.dropped)
    except BaseException as exc:  # noqa: BLE001 - the caller's code may raise anything (even SystemExit)
        sys.settrace(old_trace)
        info = error_info(exc, known_names, gui)
        return ExecOutcome(False, capture.getvalue(), info, exception=exc, dropped=capture.dropped)
    finally:
        sys.settrace(old_trace)
        sys.stdout, sys.stderr, sys.stdin = old_stdout, old_stderr, old_stdin
    return ExecOutcome(True, capture.getvalue(), dropped=capture.dropped)


# --- errors -------------------------------------------------------------------------------------------------


def _user_line(tb: Any) -> int | None:
    line = None
    while tb is not None:
        if tb.tb_frame.f_code.co_filename == USER_FILENAME:
            line = tb.tb_lineno
        tb = tb.tb_next
    return line


def error_info(exc: BaseException, known_names: Iterable[str], gui: bool = False) -> dict[str, Any]:
    """``{type, message, line, hint}`` for an exception raised by (or while compiling) the user's code."""
    line = exc.lineno if isinstance(exc, SyntaxError) else _user_line(exc.__traceback__)
    message = exc.msg if isinstance(exc, SyntaxError) else str(exc)
    if not message:
        message = type(exc).__name__
    return {
        "type": type(exc).__name__,
        "message": capped(message),
        "line": line if isinstance(line, int) and line > 0 else None,
        "hint": hint_for(exc, tuple(known_names), gui),
    }


_ATTRIBUTE_RE = re.compile(r"'([\w.]+)' object has no attribute '(\w+)'")

# Wrong attribute name on a FreeCAD object -> the name FreeCAD 1.1 uses.
_ATTRIBUTE_HINTS: dict[str, str] = {
    "Support": "FreeCAD 1.1 renamed the sketch/feature 'Support' property to 'AttachmentSupport'.",
    "Type2": "For a two-sided Pad/Pocket FreeCAD 1.1 uses SideType='Two sides' with Type/Type2='Length'.",
    "ViewObject": "There is no GUI in run_native; ViewObject is not available.",
}


def hint_for(exc: BaseException, known_names: tuple[str, ...], gui: bool = False) -> str:
    """A short, actionable pointer for the exception (empty when there is nothing useful to add). ``gui``: the worker
    runs inside the FreeCAD GUI (``App.GuiUp``), so ``FreeCADGui`` can be imported."""
    text = str(exc)
    if isinstance(exc, SyntaxError):
        return "Fix the syntax at the reported line; the document was not touched."
    if isinstance(exc, NameError):
        name = getattr(exc, "name", None) or ""
        if name in ("Gui", "FreeCADGui"):
            if gui:
                return "FreeCADGui is not preloaded: import FreeCADGui (as Gui) in the fragment first."
            return "FreeCADGui is not available inside run_native; use doc/App only (no GUI)."
        pool = [*known_names, *dir(builtins)]
        close = difflib.get_close_matches(name, pool, n=3, cutoff=0.6)
        names = ", ".join(sorted(set(known_names)))
        head = f"Did you mean {', '.join(close)}? " if close else ""
        return f"{head}Preloaded names: {names}. Import anything else explicitly."
    if isinstance(exc, AttributeError):
        match = _ATTRIBUTE_RE.search(text)
        if match and match.group(2) in _ATTRIBUTE_HINTS and not (gui and match.group(2) == "ViewObject"):
            return _ATTRIBUTE_HINTS[match.group(2)]
        return "Check the attribute name: print(obj.PropertiesList) lists a FreeCAD object's properties."
    if isinstance(exc, ZeroDivisionError):
        return "A value used as divisor is zero; check the parameters computing it."
    if isinstance(exc, TypeError):
        return (
            "FreeCAD calls are strict about argument types (Vector, float, object vs name); "
            "help(<function>) prints the expected signature."
        )
    if isinstance(exc, KeyError | IndexError):
        return "Face/edge/vertex indexes change after every recompute; look the element up again instead of reusing it."
    type_name = type(exc).__name__
    if type_name == "OCCError" or "Part.OCCError" in repr(type(exc)):
        return (
            "The geometry kernel rejected the operation (degenerate or non-intersecting inputs); "
            "check sizes and positions."
        )
    if type_name == "FreeCADError" or "FreeCAD" in repr(type(exc)):
        return "FreeCAD rejected the call; the message above is its own."
    return ""


# --- advisories ---------------------------------------------------------------------------------------------

_TRAPS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\.Placement\.(Base|Rotation)\.\w+\s*(=|\+=|-=)(?!=)"),
        "Assigning into Placement.Base/Rotation in place has no effect on the object: assign a whole Placement.",
    ),
    (
        re.compile(r"\.Support\s*="),
        "FreeCAD 1.1 renamed the 'Support' property of sketches and features to 'AttachmentSupport'.",
    ),
    (
        re.compile(r"""TwoLengths"""),
        "In FreeCAD 1.1 'TwoLengths' is hidden: use SideType='Two sides' with Type='Length' and Type2='Length'.",
    ),
    (
        re.compile(r"\b(openTransaction|commitTransaction|abortTransaction|clearUndos)\b|\.(undo|redo)\s*\("),
        "Managing transactions or undo inside the fragment can break the one-step undo of this run_native step.",
    ),
    (
        re.compile(r"\b(closeDocument|newDocument|setActiveDocument)\b"),
        "Opening, closing or switching documents inside the fragment is not tracked by the harness.",
    ),
    (
        re.compile(r"\bmakeCosmeticLine\b"),
        "makeCosmeticLine: in FreeCAD 1.1.3 every style number draws the same dashed line (none is continuous or a "
        "chain), and a call with style 0 closed FreeCAD in real use (heap corruption, not reproduced here): draw "
        "ISO 128 lines with drawing(action=add, items=[{type: line, ...}]) instead.",
    ),
    (
        re.compile(r"\bformatGeometricEdge\b"),
        "formatGeometricEdge closed FreeCAD in real use (heap corruption, exit code 0xC0000374; not reproduced here): "
        "save first (a TechDraw script gets a recovery copy of the unsaved document) and prefer the sheet's own items "
        "(drawing(action=add)).",
    ),
)

_NO_GUI_TRAP = (
    re.compile(r"\bFreeCADGui\b|\bimport\s+Gui\b|\bfrom\s+FreeCADGui\b"),
    "The GUI module is not available inside run_native (the step runs against the document only).",
)


def advisories(code: str, intent: str, gui: bool = False) -> list[str]:
    """Warnings, never blocks: large fragment, missing intent, known traps. ``gui``: the worker runs inside the
    FreeCAD GUI (``App.GuiUp``), where using the GUI module is no trap; only a headless worker warns about it."""
    out: list[str] = []
    if not intent.strip():
        out.append("No intent given: pass a one-line intent so the step is understandable later.")
    line_count = code.count("\n") + 1 if code else 0
    if line_count > LARGE_CODE_LINES or len(code) > LARGE_CODE_CHARS:
        out.append(
            f"Large fragment ({line_count} lines, {len(code)} characters): smaller steps are easier to check and undo."
        )
    if not code.strip():
        out.append("The fragment is empty: nothing was run.")
    for pattern, message in (*_TRAPS, *(() if gui else (_NO_GUI_TRAP,))):
        if pattern.search(code):
            out.append(message)
    return out


def traceback_text(exc: BaseException) -> str:
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
