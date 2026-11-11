"""``worker/native_exec.py`` in isolation (pure Python, no FreeCAD): compile, run with captured output and a soft
deadline, error line numbers and hints, advisories."""

import importlib.util
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("worker_native_exec", WORKER_DIR / "native_exec.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


native_exec = _load()
NAMES = ("doc", "App", "Part", "Vector")


def run(code: str, namespace: dict[str, object] | None = None, timeout_s: float = 5.0):  # noqa: ANN201
    code_obj, error = native_exec.compile_user_code(code)
    assert code_obj is not None, error
    return native_exec.execute(code_obj, dict(namespace or {}), timeout_s, NAMES)


# --- running -------------------------------------------------------------------------------------------------


def test_stdout_is_captured_and_restored() -> None:
    before = sys.stdout
    outcome = run("print('hello')\nprint(1 + 1)")
    assert outcome.ok
    assert outcome.stdout == "hello\n2\n"
    assert sys.stdout is before


def test_stderr_is_captured_in_order_and_restored() -> None:
    before = sys.stderr
    outcome = run("import sys\nprint('out')\nprint('err', file=sys.stderr)\nprint('out2')")
    assert outcome.stdout == "out\nerr\nout2\n"
    assert sys.stderr is before


def test_input_fails_with_eof_instead_of_reading_the_request_pipe() -> None:
    before = sys.stdin
    outcome = run("name = input('?')")
    assert not outcome.ok
    assert outcome.error is not None
    assert outcome.error["type"] == "EOFError"
    assert sys.stdin is before


def test_namespace_is_shared_between_functions_and_comprehensions_of_the_fragment() -> None:
    outcome = run("n = 3\ndef f(i):\n    return i * n\nprint([f(i) for i in range(n)])")
    assert outcome.ok, outcome.error
    assert outcome.stdout == "[0, 3, 6]\n"


def test_trace_hook_is_removed_afterwards() -> None:
    before = sys.gettrace()
    run("x = 1")
    run("raise ValueError('x')")
    assert sys.gettrace() is before


def test_long_output_comes_back_whole_for_the_client_to_shorten() -> None:
    outcome = run("print('x' * 10000)")
    assert outcome.ok
    assert outcome.stdout == "x" * 10000 + "\n"  # the worker no longer cuts at 4096 without saying where the rest is
    assert outcome.dropped == 0


def test_a_print_flood_does_not_grow_without_bound_and_counts_what_it_dropped() -> None:
    outcome = run("for _ in range(2000):\n    print('y' * 1000)", timeout_s=20)
    assert outcome.ok
    assert len(outcome.stdout) == native_exec.CAPTURE_HARD_LIMIT
    assert outcome.dropped == 2000 * 1001 - native_exec.CAPTURE_HARD_LIMIT


def test_a_long_error_message_says_how_much_of_it_was_cut() -> None:
    outcome = run(f"raise ValueError('m' * {native_exec.MESSAGE_LIMIT + 500})")
    assert outcome.error is not None
    assert outcome.error["message"].endswith("...[500 more characters cut]")


# --- errors --------------------------------------------------------------------------------------------------


def test_exception_reports_type_message_and_the_users_line() -> None:
    outcome = run("a = 1\nb = 2\nc = a / 0\n")
    assert not outcome.ok
    assert outcome.error is not None
    assert outcome.error["type"] == "ZeroDivisionError"
    assert outcome.error["line"] == 3
    assert "division" in outcome.error["message"]
    assert outcome.error["hint"]


def test_line_is_the_innermost_line_of_the_users_code() -> None:
    outcome = run("def inner():\n    return missing_name\n\ndef outer():\n    return inner()\n\nouter()\n")
    assert outcome.error is not None
    assert outcome.error["type"] == "NameError"
    assert outcome.error["line"] == 2


def test_output_printed_before_the_failure_is_kept() -> None:
    outcome = run("print('before')\nraise RuntimeError('boom')")
    assert outcome.stdout == "before\n"
    assert outcome.error is not None
    assert outcome.error["message"] == "boom"


def test_syntax_error_is_reported_at_compile_time_with_its_line() -> None:
    code_obj, error = native_exec.compile_user_code("x = 1\ny = (\n")
    assert code_obj is None
    assert error is not None
    assert error["type"] == "SyntaxError"
    assert error["line"] == 2 or error["line"] == 3
    assert "syntax" in error["hint"].lower()


def test_null_bytes_do_not_crash_compilation() -> None:
    code_obj, error = native_exec.compile_user_code("x = 1\x00")
    assert code_obj is None
    assert error is not None


def test_system_exit_in_the_fragment_is_an_error_not_a_worker_exit() -> None:
    outcome = run("import sys\nsys.exit(3)")
    assert not outcome.ok
    assert outcome.error is not None
    assert outcome.error["type"] == "SystemExit"


def test_name_error_hint_lists_preloaded_names_and_suggests_close_ones() -> None:
    outcome = run("Vectr(1, 2, 3)")
    assert outcome.error is not None
    assert "Vector" in outcome.error["hint"]
    assert "Preloaded names" in outcome.error["hint"]


def test_gui_name_error_says_there_is_no_gui() -> None:
    outcome = run("FreeCADGui.getMainWindow()")
    assert outcome.error is not None
    assert "GUI" in outcome.error["hint"]


def test_with_the_gui_up_a_gui_name_error_says_to_import_it_not_that_there_is_no_gui() -> None:
    code_obj, _ = native_exec.compile_user_code("FreeCADGui.getMainWindow()")
    assert code_obj is not None
    outcome = native_exec.execute(code_obj, {}, 5.0, NAMES, gui=True)
    assert outcome.error is not None
    assert "import FreeCADGui" in outcome.error["hint"] and "not available" not in outcome.error["hint"]
    view = native_exec.hint_for(AttributeError("'Part.Feature' object has no attribute 'ViewObject'"), NAMES, True)
    assert "no GUI" not in view


class _Sketch:
    pass


def test_attribute_error_hint_knows_the_renamed_support_property() -> None:
    outcome = run("sk.Support", {"sk": _Sketch()})
    assert outcome.error is not None
    assert outcome.error["type"] == "AttributeError"
    assert "AttachmentSupport" in outcome.error["hint"]


def test_type_error_has_a_generic_signature_hint() -> None:
    outcome = run("abs('a')")
    assert outcome.error is not None
    assert outcome.error["type"] == "TypeError"
    assert "help(" in outcome.error["hint"]


# --- deadline ---------------------------------------------------------------------------------------------


def test_an_infinite_loop_is_interrupted_at_the_deadline() -> None:
    started = time.monotonic()
    outcome = run("while True:\n    pass", timeout_s=0.3)
    assert time.monotonic() - started < 5
    assert outcome.timed_out
    assert not outcome.ok
    assert outcome.error is not None
    assert outcome.error["type"] == "Timeout"
    assert outcome.error["line"] in (1, 2)  # the loop header or its body, depending on the bytecode layout
    assert "timeout_s" in outcome.error["hint"]


def test_the_deadline_is_not_swallowed_by_except_exception() -> None:
    code = "while True:\n    try:\n        pass\n    except Exception:\n        pass"
    outcome = run(code, timeout_s=0.3)
    assert outcome.timed_out


def test_a_loop_inside_a_function_of_the_fragment_is_interrupted() -> None:
    outcome = run("def spin():\n    while True:\n        pass\nspin()", timeout_s=0.3)
    assert outcome.timed_out
    assert outcome.error is not None
    assert outcome.error["line"] in (2, 3)


def test_fast_code_is_not_affected_by_the_deadline() -> None:
    outcome = run("s = sum(range(1000))\nprint(s)", timeout_s=0.5)
    assert outcome.ok
    assert outcome.stdout == "499500\n"


# --- advisories ---------------------------------------------------------------------------------------------


def test_missing_intent_is_only_a_warning() -> None:
    warnings = native_exec.advisories("x = 1", "")
    assert any("intent" in w.lower() for w in warnings)
    assert not any("intent" in w.lower() for w in native_exec.advisories("x = 1", "make a box"))


def test_large_code_is_only_a_warning() -> None:
    big = "\n".join(f"x{i} = {i}" for i in range(400))
    assert any("Large fragment" in w for w in native_exec.advisories(big, "i"))
    assert not any("Large fragment" in w for w in native_exec.advisories("x = 1", "i"))


@pytest.mark.parametrize(
    ("code", "needle"),
    [
        ("o.Placement.Base.x = 5", "Placement"),
        ("sk.Support = (plane, [''])", "AttachmentSupport"),
        ("pad.Type = 'TwoLengths'", "SideType"),
        ("doc.openTransaction('x')", "undo"),
        ("doc.undo()", "undo"),
        ("App.closeDocument('x')", "documents"),
        ("import FreeCADGui", "GUI"),
        ("view.makeCosmeticLine(a, b, 0)", "type: line"),
        ("view.formatGeometricEdge(3, 1, 0.5, color, True)", "0xC0000374"),
    ],
)
def test_known_traps_produce_advisories(code: str, needle: str) -> None:
    warnings = native_exec.advisories(code, "i")
    assert any(needle in w for w in warnings), warnings


@pytest.mark.parametrize("code", ["import FreeCADGui", "import FreeCADGui as Gui\nGui.SendMsgToActiveView('ViewFit')"])
def test_the_gui_module_is_only_a_trap_when_there_is_no_gui(code: str) -> None:
    """The visible worker runs inside the FreeCAD GUI (``App.GuiUp``): there the GUI module imports fine, so warning
    "not available" about it is false. Only the headless worker warns."""
    assert any("GUI module is not available" in w for w in native_exec.advisories(code, "i", gui=False))
    assert native_exec.advisories(code, "i", gui=True) == []


def test_clean_code_with_intent_has_no_advisories() -> None:
    assert native_exec.advisories("b = doc.addObject('Part::Box', 'B')\ndoc.recompute()", "a box") == []


def test_equality_comparison_on_placement_is_not_a_trap() -> None:
    assert native_exec.advisories("ok = o.Placement.Base.x == 5", "i") == []


def test_the_techdraw_traps_offer_the_sheets_own_line_item() -> None:
    """Both calls closed FreeCAD in real use: the advisory says it was not reproduced and offers a way out."""
    cosmetic = native_exec.advisories("view.makeCosmeticLine(a, b, 0)", "i")
    assert len(cosmetic) == 1 and "not reproduced" in cosmetic[0] and "drawing(action=add" in cosmetic[0]
    format_edge = native_exec.advisories("view.formatGeometricEdge(1, 0, 1, c, True)", "i")
    assert len(format_edge) == 1 and "recovery copy" in format_edge[0] and "drawing(action=add" in format_edge[0]
    assert native_exec.advisories("view.makeCosmeticVertex(v)", "i") == []
