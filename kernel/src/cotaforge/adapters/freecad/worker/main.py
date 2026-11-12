"""Worker entry point: hello line, request loop on stdin, responses on the pipe fd
``COTAFORGE_RPC_FD`` (fallback: ``@cadh `` prefix on the original stdout, see ``Output``), dispatch of
every protocol method.

Bootstraps used by the client (``rpc.worker_command``):
headless ``freecadcmd -c "import sys; sys.path.insert(0, <worker_dir>); import main; main.run()"`` and
visible ``freecad gui_entry.py``. Both run ``run()``; it branches on ``App.GuiUp`` (``gui.py``).

The visible worker also pushes two kinds of unsolicited notification (no ``id``, ``Output.notify``,
the CAD-selection/manual-edit seam) that the client's reader (``adapters/rpc.py``) routes
around the request/response queue: ``selection_changed`` (``gui.start_selection_watch``, debounced) and
``manual_edit`` (``gui.start_document_watch``, suppressed while ``fc.harness_busy()``). ``gui.
start_highlight_watch`` arms a third, silent observer (no notification of its own): it restores a
``show_entities`` highlight before a document is saved and drops it outright when a document is deleted,
so a highlight never reaches disk and never outlives its document.

Either worker also pushes ``progress`` notifications (``reporting.Reporter``) from inside a long request
(``import_geometry``): the phase it is in and how many items of it are done.
"""

import contextlib
import json
import os
import sys
import threading
import traceback
from collections.abc import Callable
from typing import Any

import changes
import components as comps
import design_attrs
import drawing
import edit
import fc
import features
import foreign
import FreeCAD as App
import geometry as g
import gui
import hole_colours
import holes
import ops_assembly
import ops_features
import ops_joints
import ops_native
import ops_primitives
import ops_sheetmetal
import ops_threads
import overlap
import persist
import queries
import recorder
import registry as reg
import reporting
import sketches
import validate
import watch
import windows
from protocol import WorkerError, error, no_active_document, operation_id_of

HELLO_NAME = "cotaforge-freecad-worker"
STDOUT_PREFIX = "@cadh "
ENV_RPC_FD = "COTAFORGE_RPC_FD"
ENV_WORKER_LOG = "COTAFORGE_WORKER_LOG"
ENV_TEST_HOOKS = "COTAFORGE_WORKER_TEST_HOOKS"
# Read once at import: the parent process (``adapters/freecad/rpc.py``'s ``worker_env``) sets this before
# spawning, never after -- a production worker never has it, so ``TEST_HOOK_METHODS`` below is simply
# absent from ``handlers()`` and calling one is an ordinary ``unknown method`` (``not_supported``).
TEST_HOOKS_ENABLED = os.environ.get(ENV_TEST_HOOKS) == "1"

# Methods after which the visible worker repaints its 3D view (``gui.refresh``); queries never do.
MUTATING_METHODS = frozenset(
    {
        "set_active_document",
        "open_document",
        "import_geometry",
        "sketch",
        "extrude",
        "cut",
        "revolve",
        "sweep",
        "hole",
        "external_thread",
        "linear_pattern",
        "circular_pattern",
        "chamfer",
        "fillet",
        "shell",
        "draft",
        "mirror",
        "base_flange",
        "edge_flange",
        "unfold",
        "box",
        "cylinder",
        "boolean",
        "move_body",
        "copy_body",
        "mirror_body",
        "split",
        "edit_feature",
        "remove_feature",
        "set_feature_properties",
        "focus_model",
        "set_reference_visibility",
        "insert_component",
        "fix_component",
        "place_component",
        "add_joint",
        "set_joint",
        "mate_concentric",
        "mate_coincident",
        "mate_distance",
        "run_native",
        "undo_last",
    }
)

# Test-only RPC hooks (visible integration suite; not part of the public contract, see queries.py): never
# suppressed by ``fc.harness_operation()`` -- ``test_edit_property``'s recompute must be classified as a
# manual edit exactly like a real one, not swallowed as if the harness itself had caused it; ``test_save_document``
# is the engineer's Ctrl+S, which ``gui._HighlightWatcher.slotFinishSaveDocument`` must record as the engineer's.
# Methods after which the sketches that a feature has consumed are hidden (``sketches``): a document made before the
# ops did it is tidied when it is opened or brought back, and a native fragment that built features is tidied too.
TIDY_METHODS = frozenset({"open_document", "set_active_document", "run_native"})

TEST_HOOK_METHODS = frozenset({"test_select_entity", "test_edit_property", "test_hand_edit", "test_save_document"})

Handler = Callable[[dict[str, Any]], dict[str, Any]]

# The visible worker's manual-edit watch (``_serve_gui`` arms it); ``None`` headless, where nobody edits by hand.
WATCH: watch.ManualEditWatch | None = None

# Where the progress of a long request goes (``reporting.Reporter``): ``run`` points it at the reply channel as a
# ``progress`` notification; the check suite sets its own, and with ``None`` nobody hears.
PROGRESS_EMITTER: reporting.Emit | None = None


class Worker:
    def __init__(self) -> None:
        self.documents: dict[str, reg.DocumentRec] = {}
        self.active: reg.DocumentRec | None = None
        self.doc_counter = 0
        self.stopping = False

    # --- session ------------------------------------------------------------------------------------------

    def doc(self, params: dict[str, Any]) -> reg.DocumentRec:
        if self.active is None:
            raise no_active_document(operation_id_of(params))
        return self.active

    def document_info(self, doc: reg.DocumentRec) -> dict[str, Any]:
        return {"handle": {"id": doc.id}, "kind": doc.kind, "name": doc.name, "units": "mm"}

    def new_document(self, params: dict[str, Any]) -> dict[str, Any]:
        kind = str(params.get("kind", "part"))
        if kind not in ("part", "assembly"):
            raise error("invalid_request", f"unknown document kind {kind}", operation_id_of(params))
        self.doc_counter += 1
        doc_id = f"doc:{self.doc_counter}"
        name = str(params.get("name", doc_id))
        fc_doc = fc.new_document(name, doc_id)
        fc_doc.Meta = {**dict(fc_doc.Meta), "cotaforge_doc.name": name}  # what an assembly's parts list calls the part
        gui.on_new_document(fc_doc)
        doc = reg.DocumentRec(doc_id, kind, name, fc_doc)
        self.documents[doc_id] = doc
        self.active = doc
        return self.document_info(doc)

    def open_document(self, params: dict[str, Any]) -> dict[str, Any]:
        """Reopen a saved part or assembly and re-bind its handle registry (``persist.open_document``); it becomes the
        active document under the id it was saved with (a new one when another file saved by an earlier session already
        holds that id here), and later ``new_document`` ids never collide with it. Without ``adapter_state`` any file
        is ADOPTED under the next free document id (``persist.adopt``). An assembly needs the documents of its parts
        open already (they are found by file)."""
        doc, result = persist.open_document(
            params, lambda doc_id: doc_id in self.documents, f"doc:{self.doc_counter + 1}", self.documents
        )
        self.documents[doc.id] = doc  # the manual-edit watch takes its baseline when this request ends
        self.active = doc
        number = doc.id.rsplit(":", 1)[-1]
        if number.isdigit():
            self.doc_counter = max(self.doc_counter, int(number))
        return result

    def import_geometry(self, params: dict[str, Any]) -> dict[str, Any]:
        """Read a STEP or IGES file into a NEW document of adopted reference bodies (``foreign.import_geometry``); it
        becomes the active document."""
        reporter = reporting.Reporter(PROGRESS_EMITTER, operation_id_of(params))
        doc, result = foreign.import_geometry(params, f"doc:{self.doc_counter + 1}", reporter)
        self.doc_counter += 1
        gui.on_new_document(doc.fc_doc)
        self.documents[doc.id] = doc
        self.active = doc
        return result

    def close_document(self, params: dict[str, Any]) -> dict[str, Any]:
        """Close the active document (unsaved changes are discarded) and drop its registry; nothing is active
        afterwards. Other open documents stay open."""
        doc = self.doc(params)
        name = str(doc.fc_doc.Name)
        if doc.borrowed:
            pass  # the engineer opened this file by hand: it is forgotten here and stays open (and shown) in FreeCAD
        else:
            fc.close_document(doc.fc_doc)
        self.documents.pop(doc.id, None)
        if WATCH is not None:
            WATCH.forget(name)
        self.active = None
        return {}

    def set_active_document(self, params: dict[str, Any]) -> dict[str, Any]:
        op = operation_id_of(params)
        doc_id = reg.handle_id(params.get("document"), op, "document")
        doc = self.documents.get(doc_id)
        if doc is None:
            raise error("invalid_reference", f"unknown document {doc_id}", op, handle=doc_id)
        self.active = doc
        if params.get("window", True):
            windows.show(doc)  # a part that lost its window (inside an assembly) gets it back to be edited
        if params.get("refresh") and doc.kind == "assembly":
            comps.refresh(doc, self.documents)  # the parts it uses may have changed since
        if params.get("window", True):
            windows.release(self.documents, doc)  # the parts left behind that an assembly uses need no window
        return self.document_info(doc)

    def insert_component(self, params: dict[str, Any]) -> dict[str, Any]:
        """Insert an instance of a part (``ops_assembly``); the part, saved and inside the assembly, needs no window of
        its own any more (``windows.release``): the document stays loaded and its handle inserts it again."""
        doc = self.doc(params)
        result = ops_assembly.insert_component(doc, self.documents, params)
        released = windows.release(self.documents, doc)
        if released:
            names = ", ".join(repr(n) for n in released)
            result["warnings"] = [
                *result.get("warnings", []),
                f"the window of {names} was closed: it is in the assembly now and stays loaded "
                '(doc(action="switch", name=...) opens it again to edit it)',
            ]
        return result

    def active_document(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.document_info(self.doc(params))

    def disconnect(self, params: dict[str, Any]) -> dict[str, Any]:
        self.stopping = True
        return {}

    def hello(self) -> dict[str, Any]:
        return {
            "hello": HELLO_NAME,
            "freecad": g.freecad_version(),
            "python": ".".join(str(x) for x in sys.version_info[:3]),
            "pid": os.getpid(),
            "gui": gui.available(),
            "sheet_metal": ops_sheetmetal.status(),
        }

    # --- dispatch --------------------------------------------------------------------------------------------

    def handlers(self) -> dict[str, Handler]:
        d = self.doc
        handlers = {
            "disconnect": self.disconnect,
            "new_document": self.new_document,
            "set_active_document": self.set_active_document,
            "active_document": self.active_document,
            "suppress_interaction": lambda p: {},
            "restore_interaction": lambda p: {},
            "canary": validate.canary,
            "validate_api": validate.validate_api,
            "sketch": lambda p: ops_features.sketch(d(p), p),
            "datum_plane": lambda p: ops_features.datum_plane(d(p), p),
            "datum_axis": lambda p: ops_features.datum_axis(d(p), p),
            "extrude": lambda p: ops_features.extrude(d(p), p),
            "cut": lambda p: ops_features.cut(d(p), p),
            "revolve": lambda p: ops_features.revolve(d(p), p),
            "sweep": lambda p: ops_features.sweep(d(p), p),
            "hole": lambda p: ops_features.hole(d(p), p),
            "external_thread": lambda p: ops_threads.external_thread(d(p), p),
            "linear_pattern": lambda p: ops_features.linear_pattern(d(p), p),
            "circular_pattern": lambda p: ops_features.circular_pattern(d(p), p),
            "chamfer": lambda p: ops_features.chamfer(d(p), p),
            "fillet": lambda p: ops_features.fillet(d(p), p),
            "shell": lambda p: ops_features.shell(d(p), p),
            "draft": lambda p: ops_features.draft(d(p), p),
            "mirror": lambda p: ops_features.mirror(d(p), p),
            "base_flange": lambda p: ops_sheetmetal.base_flange(d(p), p),
            "edge_flange": lambda p: ops_sheetmetal.edge_flange(d(p), p),
            "unfold": lambda p: ops_sheetmetal.unfold(d(p), p),
            "box": lambda p: ops_primitives.box(d(p), p),
            "cylinder": lambda p: ops_primitives.cylinder(d(p), p),
            "boolean": lambda p: ops_primitives.boolean(d(p), p),
            "move_body": lambda p: ops_primitives.move_body(d(p), p),
            "copy_body": lambda p: ops_primitives.copy_body(d(p), p),
            "mirror_body": lambda p: ops_primitives.mirror_body(d(p), p),
            "split": lambda p: ops_primitives.split(d(p), p),
            "edit_feature": lambda p: edit.edit_feature(d(p), p),
            "measure_bodies": lambda p: edit.measure_bodies(d(p), p),
            "list_features": lambda p: features.list_features(d(p), p),
            "remove_feature": lambda p: features.remove_feature(d(p), p),
            "set_feature_properties": lambda p: features.set_feature_properties(d(p), p),
            "focus_model": lambda p: features.focus_model(d(p), p),
            "set_reference_visibility": lambda p: features.set_reference_visibility(d(p), p),
            "insert_component": self.insert_component,
            "fix_component": lambda p: ops_assembly.fix_component(d(p), p),
            "place_component": lambda p: ops_assembly.place_component(d(p), p),
            "add_joint": lambda p: ops_joints.add_joint(d(p), p),
            "set_joint": lambda p: ops_joints.set_joint(d(p), p),
            "joint_info": lambda p: ops_joints.joint_info(d(p), p),
            "sweep_joint": lambda p: ops_joints.sweep_joint(d(p), p),
            "mate_concentric": lambda p: ops_assembly.mate_concentric(d(p), p),
            "mate_coincident": lambda p: ops_assembly.mate_coincident(d(p), p),
            "mate_distance": lambda p: ops_assembly.mate_distance(d(p), p),
            "check_interference": lambda p: ops_assembly.check_interference(d(p), p),
            "component_bounding_box": lambda p: ops_assembly.component_bounding_box(d(p), p),
            "component_transform": lambda p: ops_assembly.component_transform(d(p), p),
            "faces_of": lambda p: queries.faces_of(d(p), p),
            "edges_between": lambda p: queries.edges_between(d(p), p),
            "edges_of": lambda p: queries.edges_of(d(p), p),
            "vertices_of": lambda p: queries.vertices_of(d(p), p),
            "vertex_at": lambda p: queries.vertex_at(d(p), p),
            "bodies_of": lambda p: queries.bodies_of(d(p), p),
            "is_valid": lambda p: queries.is_valid(d(p), p),
            "body_interference": lambda p: queries.body_interference(d(p), p),
            "list_holes": lambda p: holes.list_holes(d(p), p),
            "tool_overlap": lambda p: overlap.tool_overlap(d(p), p),
            "mass_properties": lambda p: queries.mass_properties(d(p), p),
            "bounding_box": lambda p: queries.bounding_box(d(p), p),
            "feature_tree": lambda p: queries.feature_tree(d(p), p),
            "face_info": lambda p: queries.face_info(d(p), p),
            "edge_info": lambda p: queries.edge_info(d(p), p),
            "sketch_status": lambda p: queries.sketch_status(d(p), p),
            "sketch_notes": lambda p: queries.sketch_notes(d(p), p),
            "get_selection": lambda p: queries.get_selection(d(p), p),
            "show_entities": lambda p: queries.show_entities(d(p), p),
            "persistent_reference": lambda p: queries.persistent_reference(d(p), p),
            "resolve_reference": lambda p: queries.resolve_reference(self.documents, p, self.active),
            "signature": lambda p: queries.signature(d(p), p),
            "undo_last": lambda p: queries.undo_last(d(p), p),
            "export_macro": queries.export_macro,
            "export_step": lambda p: queries.export_step(d(p), p),
            "export_mesh": lambda p: queries.export_mesh(d(p), p),
            "export_dxf": lambda p: queries.export_dxf(d(p), p),
            "save_document": lambda p: persist.save_document(d(p), p),
            "open_document": self.open_document,
            "import_geometry": self.import_geometry,
            "close_document": self.close_document,
            "design_attributes": lambda p: design_attrs.read(d(p)),
            "set_design_attributes": lambda p: design_attrs.write(d(p), p),
            "set_view": lambda p: queries.set_view(d(p), p),
            "capture": lambda p: queries.capture(d(p), p),
            "style_bodies": lambda p: queries.style_bodies(d(p), p),
            "section_plane": lambda p: queries.section_plane(d(p), p),
            "create_drawing": lambda p: drawing.create(d(p), p),
            "annotate_drawing": lambda p: drawing.annotate(d(p), p),
            "describe_drawing": lambda p: drawing.describe(d(p), p),
            "capture_drawing": lambda p: drawing.capture(d(p), p),
            "export_drawing": lambda p: drawing.export(d(p), p),
            "run_native": lambda p: ops_native.run_native(d(p), p),
        }
        if TEST_HOOKS_ENABLED:
            handlers["test_document_state"] = lambda p: ops_native.document_state(d(p), p)
            handlers["test_select_entity"] = lambda p: queries.test_select_entity(d(p), p)
            handlers["test_edit_property"] = lambda p: queries.test_edit_property(d(p), p)
            handlers["test_hand_edit"] = lambda p: queries.test_hand_edit(d(p), p)
            handlers["test_save_document"] = lambda p: queries.test_save_document(d(p), p)
            handlers["test_shape_signature"] = lambda p: queries.test_shape_signature(d(p), p)
            handlers["test_reopen_document"] = queries.test_reopen_document
            handlers["test_inspect_saved_document"] = queries.test_inspect_saved_document
            handlers["test_document_summary"] = lambda p: queries.test_document_summary(d(p), p)
            handlers["test_view_color"] = queries.test_view_color
            handlers["test_set_view_color"] = queries.test_set_view_color
            handlers["test_view_state"] = queries.test_view_state
        return handlers

    def dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Run one method; raises ``WorkerError`` on any failure (internal errors are wrapped).

        Wrapped in ``fc.harness_operation()`` for its whole duration (not just the methods that open an
        ``fc`` transaction: ``undo_last`` recomputes without one) so the document-change observer (the
        live manual-edit seam) never mistakes a harness-issued recompute for one the engineer triggered
        by hand -- a query method that recomputes nothing is a harmless no-op wrap.
        """
        handler = self.handlers().get(method)
        if handler is None:
            raise error("not_supported", f"unknown method {method}", operation_id_of(params), method=method)
        harness = method not in TEST_HOOK_METHODS
        suppress = fc.harness_operation() if harness else contextlib.nullcontext()
        watching = harness and WATCH is not None
        if watching:
            _watching(WATCH.before_harness)  # a hand edit made just before this request is reported first
        try:
            with suppress:
                try:
                    known = self._consumed_before(method)
                    result = handler(params)
                    if method in TIDY_METHODS and self.active is not None:
                        # datums only after a fragment: the planes the engineer left showing in his own file stay
                        _watching(
                            lambda: sketches.hide_consumed(self.active.fc_doc, known, datums=method == "run_native")
                        )  # never fails the call
                    return result
                except WorkerError:
                    fc.abort_open_transactions()
                    raise
                except Exception as exc:  # noqa: BLE001 - every other exception becomes an ``internal`` error
                    fc.abort_open_transactions()
                    raise error(
                        "internal",
                        f"{type(exc).__name__}: {exc}",
                        operation_id_of(params),
                        traceback=traceback.format_exc(),
                    ) from exc
                finally:
                    if method in MUTATING_METHODS:  # also after a failure: the view must show the rolled-back state
                        if self.active is not None:
                            hole_colours.sync(self.active)  # the holes keep the colour of their class (never fails)
                        gui.refresh(self.active.fc_doc if self.active is not None else None)
        finally:
            if watching:
                _watching(WATCH.after_harness)  # what this request changed is the new baseline, never a "hand edit"

    def _consumed_before(self, method: str) -> set[str] | None:
        """The sketches a fragment found consumed already: what it shows of them is its own business. Opening or
        bringing back a document tidies all of them."""
        if method != "run_native" or self.active is None:
            return None
        try:
            return sketches.consumed_names(self.active.fc_doc)
        except Exception:  # noqa: BLE001 - the tidy-up never fails the request
            return None

    def close_documents(self) -> None:
        for doc in list(self.documents.values()):
            try:
                App.closeDocument(doc.fc_doc.Name)
            except Exception:  # noqa: BLE001 - already closed by hand in the GUI, for instance
                pass
        self.documents.clear()
        self.active = None


WORKER = Worker()


def reset() -> None:
    """Fresh worker state (used by the check suite between checks)."""
    global WORKER  # noqa: PLW0603
    for doc in list(WORKER.documents.values()):
        try:
            App.closeDocument(doc.fc_doc.Name)
        except Exception:  # noqa: BLE001
            pass
    fc.recorder.lines.clear()
    fc.transaction_open.clear()
    recorder.clear_documents()
    WORKER = Worker()


def dispatch(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return WORKER.dispatch(method, params)


def handle_line(line: str) -> dict[str, Any] | None:
    """One request line -> one response dict (``None`` for blank lines)."""
    line = line.strip()
    if not line:
        return None
    try:
        request = json.loads(line)
    except json.JSONDecodeError as exc:
        return {"id": None, "ok": False, "error": error("internal", f"bad request line: {exc}").to_dict()}
    request_id = request.get("id")
    method = str(request.get("method", ""))
    params = request.get("params") or {}
    try:
        result = WORKER.dispatch(method, params)
    except WorkerError as exc:
        return {"id": request_id, "ok": False, "error": exc.to_dict()}
    return {"id": request_id, "ok": True, "result": result}


class Output:
    """The reply channel: the inherited pipe fd ``COTAFORGE_RPC_FD`` when the client passed one, else
    the process' original stdout with the ``@cadh `` prefix.

    The fallback never writes through ``sys.stdout``: in the GUI binary that object is FreeCAD's
    Report-view redirect (nothing would reach the client), and in ``freecadcmd`` the C++ recompute
    progress writes to fd 1 unbuffered and would interleave with a reply. Instead fd 1 is duplicated
    for the replies and then pointed at the worker log (``COTAFORGE_WORKER_LOG``, else ``os.devnull``),
    so every other writer of fd 1 lands in the log. ``os.dup``/``os.dup2`` exist on the Windows CRT.
    """

    def __init__(self) -> None:
        fd = os.environ.get(ENV_RPC_FD)
        self.prefix = ""
        self.stream = None
        self.reply_fd: int | None = None
        self._lock = threading.Lock()
        if fd:
            self.stream = os.fdopen(int(fd), "w", buffering=1)
            return
        self.prefix = STDOUT_PREFIX
        self.reply_fd = os.dup(1)
        log_fd = os.open(os.environ.get(ENV_WORKER_LOG) or os.devnull, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        os.dup2(log_fd, 1)
        os.close(log_fd)

    def send(self, message: dict[str, Any]) -> None:
        with self._lock:
            self._write(message)

    def notify(self, method: str, params: dict[str, Any]) -> None:
        """Unsolicited notification frame (no ``id``): ``adapters/rpc.py``'s reader routes any message
        with a ``method`` and no ``id`` straight to the registered listener instead of the response
        queue, so this never disturbs an in-flight request/response pair. Same channel, same lock as
        ``send`` -- selection/document-observer callbacks and request replies both end up here, and on
        Windows this is the same stdout-fallback fd the replies use (never ``print``/``sys.stdout``,
        which is FreeCAD's Report-view redirect in the GUI binary)."""
        with self._lock:
            self._write({"method": method, "params": params})

    def _write(self, message: dict[str, Any]) -> None:
        line = self.prefix + json.dumps(message) + "\n"
        if self.stream is not None:
            self.stream.write(line)
            self.stream.flush()
            return
        assert self.reply_fd is not None
        data = memoryview(line.encode("utf-8"))
        while data:
            data = data[os.write(self.reply_fd, data) :]

    def flush(self) -> None:
        if self.stream is not None:
            self.stream.flush()


def _emit_progress(out: Output, payload: dict[str, Any]) -> None:
    try:
        out.notify("progress", payload)
    except OSError:  # the client went away: nobody is left to hear it
        pass


def run() -> None:
    global PROGRESS_EMITTER  # noqa: PLW0603
    out = Output()
    PROGRESS_EMITTER = lambda payload: _emit_progress(out, payload)  # noqa: E731
    out.send({"id": 0, "ok": True, "result": WORKER.hello()})
    if gui.available():
        _serve_gui(out)
        return  # FreeCAD's event loop takes over; ``gui.shutdown`` ends the process
    for line in sys.stdin:
        response = handle_line(line)
        if response is None:
            continue
        out.send(response)
        if WORKER.stopping:
            break
    try:
        out.flush()
    except Exception:  # noqa: BLE001
        pass


def _on_selection_settled(out: Output) -> None:
    """``gui.start_selection_watch``'s debounced callback: re-read the whole live selection and push it,
    if there is an active document to read it from and a live client left to read it (``OSError``: the
    client went away, same tolerance ``_serve_gui``'s ``on_line`` already has for a reply)."""
    if WORKER.active is None:
        return
    try:
        result = queries.get_selection(WORKER.active, {"operation_id": "selection.notify"})
    except WorkerError:
        return
    try:
        out.notify("selection_changed", result)
    except OSError:
        pass


def _capture_document(doc_name: str, names: set[str] | None) -> changes.Baseline | None:
    """The design of the objects called ``names`` (all of them when ``None``) of one open FreeCAD document, as the
    manual-edit watch compares it; ``None``: the document is gone. A name no object answers to is simply not in it."""
    fc_doc = App.listDocuments().get(doc_name)
    if fc_doc is None:
        return None
    if names is None:
        return changes.capture(list(fc_doc.Objects))
    found = (fc_doc.getObject(name) for name in sorted(names))
    return changes.capture([obj for obj in found if obj is not None])


def _live_name(doc_rec: Any) -> str | None:
    """The FreeCAD name of a harness document, ``None`` when FreeCAD has already closed it (by hand, or a test)."""
    try:
        return str(doc_rec.fc_doc.Name)
    except Exception:  # noqa: BLE001 - a deleted App::Document raises on any attribute
        return None


def _document_rec(doc_name: str) -> Any | None:
    return next((d for d in list(WORKER.documents.values()) if _live_name(d) == doc_name), None)


def _watching(call: Callable[[], None]) -> None:
    """The manual-edit watch never fails a request of the harness."""
    try:
        call()
    except Exception:  # noqa: BLE001
        sys.stderr.write(traceback.format_exc())


def _report_edits(doc_name: str, edits: list[watch.Edit], out: Output) -> None:
    """``watch.ManualEditWatch``'s report: one ``manual_edit`` notification per object the engineer changed, with
    the harness feature that owns it (its id as ``ref``) or, for an object the harness never made, its CAD name."""
    doc_rec = _document_rec(doc_name)
    if doc_rec is None:
        return
    for item in edits:
        feature = queries.feature_for_object(doc_rec, item.object) if item.object else None
        try:
            out.notify(
                "manual_edit",
                {
                    "artefact_kind": "feature" if feature is not None else "object",
                    "ref": feature.id if feature is not None else item.object,
                    "by": "engineer",
                    "reason": item.reason,
                    "changes": item.lines,
                    "document": doc_rec.name,
                    "name": item.object,
                    "action": item.action,
                },
            )
        except OSError:  # the client went away: nobody is left to hear it
            return


def _serve_gui(out: Output) -> None:
    """Visible worker: requests arrive as queued Qt signals; EOF or ``disconnect`` closes the docs and quits."""

    def stop() -> None:
        gui.shutdown(WORKER.close_documents, out.flush)

    def on_line(line: str) -> None:
        response = handle_line(line)
        if response is None:
            return
        try:
            out.send(response)
        except OSError:  # the client went away: nobody is left to reply to
            stop()
            return
        if WORKER.stopping:
            stop()

    gui.start_selection_watch(lambda: _on_selection_settled(out))
    global WATCH  # noqa: PLW0603
    schedule, cancel = gui.timer_scheduler()
    WATCH = watch.ManualEditWatch(
        capture=_capture_document,
        known=lambda name: _document_rec(name) is not None,
        known_docs=lambda: [n for n in map(_live_name, list(WORKER.documents.values())) if n is not None],
        busy=fc.harness_busy,
        report=lambda name, edits: _report_edits(name, edits, out),
        schedule=schedule,
        cancel=cancel,
    )
    gui.start_document_watch(WATCH.on_event)
    gui.start_highlight_watch()
    gui.serve(on_line, stop)
