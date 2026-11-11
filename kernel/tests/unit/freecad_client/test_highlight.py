"""``worker/highlight.py``'s colour bookkeeping, in isolation: no FreeCAD needed (same reasoning
``test_debounce.py`` documents for ``worker/debounce.py`` -- this module has no FreeCAD/Qt import at
all). Also a static (AST) check that ``gui.py``'s highlight functions never call ``Gui.Selection`` --
that module needs the real ``FreeCAD``/``FreeCADGui`` to import at all, so this is source-level, not a
live call."""

import ast
import importlib.util
import sys
from pathlib import Path
from typing import Any

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"


def _load_highlight() -> Any:
    spec = importlib.util.spec_from_file_location("worker_highlight", WORKER_DIR / "highlight.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


highlight = _load_highlight()

RED = (1.0, 0.0, 0.0, 0.0)
GREEN = (0.0, 1.0, 0.0, 0.0)
BLUE = (0.0, 0.0, 1.0, 0.0)
ORANGE = (1.0, 0.55, 0.0, 0.0)


def test_recolor_replaces_only_the_named_indices() -> None:
    current = [RED, GREEN, BLUE]
    result = highlight.recolor(current, [1], ORANGE)
    assert result == [RED, ORANGE, BLUE]
    assert current == [RED, GREEN, BLUE]  # the input is never mutated


def test_recolor_accepts_several_indices() -> None:
    current = [RED, GREEN, BLUE, RED]
    result = highlight.recolor(current, [0, 3], ORANGE)
    assert result == [ORANGE, GREEN, BLUE, ORANGE]


def test_recolor_skips_an_out_of_range_index_without_raising() -> None:
    current = [RED, GREEN]
    result = highlight.recolor(current, [5, -1, 0], ORANGE)
    assert result == [ORANGE, GREEN]


def test_highlight_state_remembers_and_lists_entries_for_its_document() -> None:
    state = highlight.HighlightState()
    state.remember("doc1", "Pad", "DiffuseColor", [RED, GREEN, BLUE])
    assert bool(state) is True

    entries = state.entries_for("doc1")
    assert entries == [("Pad", "DiffuseColor", (RED, GREEN, BLUE))]
    assert bool(state) is True  # entries_for is read-only -- nothing forgotten yet


def test_highlight_state_keeps_the_first_remember_per_key_this_round() -> None:
    """The FIRST read of an object's colours is the true original; a highlight already applied earlier in
    the same round must never overwrite it with the highlight colour itself."""
    state = highlight.HighlightState()
    state.remember("doc1", "Pad", "DiffuseColor", [RED, GREEN, BLUE])
    state.remember("doc1", "Pad", "DiffuseColor", [ORANGE, GREEN, BLUE])  # would-be second read, ignored

    (entry,) = state.entries_for("doc1")
    assert entry == ("Pad", "DiffuseColor", (RED, GREEN, BLUE))


def test_highlight_state_tracks_multiple_objects_independently() -> None:
    state = highlight.HighlightState()
    state.remember("doc1", "Pad", "DiffuseColor", [RED])
    state.remember("doc1", "Pocket", "LineColorArray", [GREEN, BLUE])

    entries = {name: (attr, colors) for name, attr, colors in state.entries_for("doc1")}
    assert entries == {
        "Pad": ("DiffuseColor", (RED,)),
        "Pocket": ("LineColorArray", (GREEN, BLUE)),
    }


def test_entries_for_an_empty_state_is_a_no_op() -> None:
    state = highlight.HighlightState()
    assert state.entries_for("doc1") == []
    assert bool(state) is False


def test_discard_removes_only_the_named_entry() -> None:
    state = highlight.HighlightState()
    state.remember("doc1", "Pad", "DiffuseColor", [RED])
    state.remember("doc1", "Pocket", "DiffuseColor", [GREEN])

    state.discard("doc1", "Pad")

    assert state.entries_for("doc1") == [("Pocket", "DiffuseColor", (GREEN,))]


def test_discard_of_an_unknown_entry_is_a_no_op() -> None:
    state = highlight.HighlightState()
    state.discard("doc1", "Nope")  # never raises
    assert bool(state) is False


# --- (document, object) keying, never a single global namespace ------------------------


def test_two_documents_with_the_same_object_name_never_collide() -> None:
    """Fresh review CRITICAL: a single ``object_name``-only key let doc B's ``"Pad"`` clobber doc A's
    ``"Pad"`` entry (or vice versa). Keyed by ``(document_name, object_name)``, each document's entry for
    the same object name is tracked, restored and discarded completely independently."""
    state = highlight.HighlightState()
    state.remember("docA", "Pad", "DiffuseColor", [RED])
    state.remember("docB", "Pad", "DiffuseColor", [BLUE])

    assert state.entries_for("docA") == [("Pad", "DiffuseColor", (RED,))]
    assert state.entries_for("docB") == [("Pad", "DiffuseColor", (BLUE,))]

    state.discard("docA", "Pad")

    assert state.entries_for("docA") == []
    assert state.entries_for("docB") == [("Pad", "DiffuseColor", (BLUE,))]  # untouched, still clearable


def test_discard_document_drops_only_that_documents_entries() -> None:
    """Fresh review CRITICAL #1: a document closed while highlighted must drop its own entries safely,
    without touching any other open document's still-pending highlight."""
    state = highlight.HighlightState()
    state.remember("docA", "Pad", "DiffuseColor", [RED])
    state.remember("docB", "Pad", "DiffuseColor", [BLUE])
    state.remember("docB", "Pocket", "LineColorArray", [GREEN])

    state.discard_document("docA")

    assert state.entries_for("docA") == []
    assert bool(state) is True  # docB's entries survive
    assert set(state.entries_for("docB")) == {
        ("Pad", "DiffuseColor", (BLUE,)),
        ("Pocket", "LineColorArray", (GREEN,)),
    }


def test_discard_document_on_an_unknown_document_is_a_no_op() -> None:
    state = highlight.HighlightState()
    state.remember("docA", "Pad", "DiffuseColor", [RED])
    state.discard_document("docB")  # never raises, never touches docA
    assert state.entries_for("docA") == [("Pad", "DiffuseColor", (RED,))]


# --- restore_value's safe topology-change fallback --------------------------------------


def test_restore_value_returns_the_original_when_the_length_still_matches() -> None:
    assert highlight.restore_value([RED, GREEN, BLUE], 3) == [RED, GREEN, BLUE]


def test_restore_value_falls_back_to_empty_when_the_topology_changed() -> None:
    """A plan operation, undo or hand edit between ``highlight_entities`` and the restore can change the
    object's face/edge count; assigning a mismatched-length list either raises inside FreeCAD or silently
    corrupts the per-element colouring, so the empty list (FreeCAD's own "use the single base colour"
    convention) is the one value that is always valid regardless of the current count."""
    assert highlight.restore_value([RED, GREEN, BLUE], 5) == []
    assert highlight.restore_value([RED, GREEN, BLUE], 1) == []
    assert highlight.restore_value([RED, GREEN], 3) == []


def test_restore_value_always_restores_a_length_one_uniform_colour() -> None:
    """Confirmed against real FreeCAD: a length-1 ``DiffuseColor``/``LineColorArray`` ("uniform colour for
    the whole shape") is exactly what a never-recoloured object starts with REGARDLESS of its face/edge
    count, so it is always safe to restore verbatim -- no topology check needed for this one case, unlike
    a longer, genuinely per-element list."""
    assert highlight.restore_value([RED], 0) == [RED]
    assert highlight.restore_value([RED], 1) == [RED]
    assert highlight.restore_value([RED], 6) == [RED]
    assert highlight.restore_value([RED], 7) == [RED]


def test_restore_value_never_mutates_its_input() -> None:
    original = [RED, GREEN, BLUE]
    highlight.restore_value(original, 3)
    assert original == [RED, GREEN, BLUE]


# --- expand_to_length (bug found running the real visible worker) -----------------------


def test_expand_to_length_returns_the_input_unchanged_when_already_the_right_length() -> None:
    assert highlight.expand_to_length([RED, GREEN, BLUE], 3) == [RED, GREEN, BLUE]


def test_expand_to_length_repeats_the_single_uniform_colour() -> None:
    """The actual bug ``highlight_entities`` shipped with, confirmed against real FreeCAD: a fresh
    object's ``DiffuseColor`` is ``[colour]`` (length 1) regardless of its face count; painting index 0
    directly into that list sets the shape's ONE colour, recolouring the WHOLE object instead of one
    face. Expanding first repeats that colour once per face so ``recolor`` can target a single index."""
    assert highlight.expand_to_length([RED], 6) == [RED] * 6


def test_expand_to_length_of_a_mismatched_multi_entry_list_repeats_its_last_entry() -> None:
    """Not a state real FreeCAD actually produces (only length-1 or length-N are ever observed), but the
    function must still do something sane and never raise: fills the WHOLE requested length with the
    last entry, the same "one entry to repeat" rule as the length-1 case, rather than trying to guess
    which of the existing entries to keep."""
    assert highlight.expand_to_length([RED, GREEN], 4) == [GREEN, GREEN, GREEN, GREEN]


def test_expand_to_length_of_an_empty_list_uses_a_default_colour() -> None:
    """Never actually seen from a real ``ViewObject`` (FreeCAD always reports at least one colour), but
    must not raise (an ``IndexError`` on ``current[-1]``) if it ever were."""
    assert highlight.expand_to_length([], 3) == [(1.0, 1.0, 1.0, 1.0)] * 3


def test_expand_to_length_never_mutates_its_input() -> None:
    current = [RED]
    highlight.expand_to_length(current, 6)
    assert current == [RED]


# --- static check: gui.py's highlight functions never touch Gui.Selection --------------------------------------
#
# ``worker/gui.py`` does ``import FreeCAD as App`` unconditionally, so it cannot be imported at all without
# the real FreeCAD -- this check reads its SOURCE and inspects the two function bodies with ``ast``,
# never a live call, but it is exact (not a text substring match anywhere in the file, which would also
# trip on ``get_selection``'s and the selection observer's own legitimate uses a few lines above/below).


def _function_node(source: str, name: str) -> ast.FunctionDef:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    msg = f"function {name!r} not found in gui.py"
    raise AssertionError(msg)


def _references_gui_selection(node: ast.FunctionDef) -> bool:
    """True if any CODE (not the docstring) in ``node`` accesses ``Gui.Selection`` -- an ``ast.Attribute``
    node ``.attr == \"Selection\"`` whose value is the ``Gui`` name, wherever it appears in an expression
    (``Gui.Selection.addSelection(...)``, passed as a bare reference, etc.)."""
    body = node.body[1:] if ast.get_docstring(node) is not None else node.body
    for statement in body:
        for child in ast.walk(statement):
            if (
                isinstance(child, ast.Attribute)
                and child.attr == "Selection"
                and isinstance(child.value, ast.Name)
                and child.value.id == "Gui"
            ):
                return True
    return False


def test_highlight_entities_never_calls_gui_selection() -> None:
    source = (WORKER_DIR / "gui.py").read_text(encoding="utf-8")
    assert not _references_gui_selection(_function_node(source, "highlight_entities"))


def test_clear_highlight_never_calls_gui_selection() -> None:
    source = (WORKER_DIR / "gui.py").read_text(encoding="utf-8")
    assert not _references_gui_selection(_function_node(source, "clear_highlight"))
