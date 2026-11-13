"""The live session: one adapter, the active document with its executor, and the step history that keeps undo coherent.

Coherence rule: the kernel ``Executor`` is the only thing that drives the adapter for construction. Every ``op`` call
appends exactly ONE operation to the executor's plan (``extend``) and runs it (``next_operation``); every ``undo``
goes through ``Executor.undo_to``, which asks the adapter to undo one feature per reverted step and checks that both
agree. A step that fails is dropped from the plan (``abandon_failed``) so the session never gets stuck behind it.

The session holds several documents at once: the active one (the fields below) and the others parked beside it, each
with its own executor, so a part can be modelled in its own document and axes and placed in an assembly later. Only the
active document answers ``op``, ``undo`` and ``inspect``; ``doc(action=switch)`` changes which one it is.

The native file is the progress of the design (``save_document``: atomic, stamped); the server keeps the rest of the
session -- the adapter's handle state and the executor's history -- in a record next to it (``checkpoint``), so:

* a file this server saved and nobody touched reopens EXACTLY: same step ids, same handles, history intact (steps made
  before the reopen cannot be undone: the CAD kept no undo journal for them);
* any other file -- a stamp-less one made elsewhere, or one this server saved and somebody edited afterwards -- is
  ADOPTED: the adapter mints new handles for what it holds and each solid body becomes an alias the new steps can
  build on.

An assembly names its parts by file (relative to its own): reopening it opens the part documents it needs first.
"""

import contextlib
import dataclasses
import json
import math
import os
import re
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cotaforge.contract import (
    DEFAULT_SEW_TOLERANCE_MM,
    MAX_SEW_TOLERANCE_MM,
    Adapter,
    AdapterError,
    BodyHandle,
    BodyInfo,
    BoundingBox,
    Capabilities,
    ConnectCancellable,
    ConnectRequest,
    DesignAttributes,
    DocumentHandle,
    DocumentInfo,
    DocumentKind,
    DocumentStamp,
    EdgeHandle,
    EdgeRequest,
    Empty,
    FaceHandle,
    FaceRequest,
    FeatureHandle,
    FeatureRequest,
    HoleStandard,
    ImportGeometryRequest,
    ImportGeometryResult,
    JointInfo,
    ManualEditProducer,
    MassProperties,
    MassRequest,
    NativeObject,
    OpenDocumentRequest,
    OpenDocumentResult,
    OperationSpec,
    ProgressNotice,
    ProgressProducer,
    RunNativeRequest,
    RunNativeResult,
    SaveDocumentRequest,
    SessionInfo,
    SetActiveDocumentRequest,
    SetDesignAttributesRequest,
    SketchFrame,
    ValidityRequest,
    ValidityResult,
    extension_op_name,
    plain_op_name,
    sketch_profiles,
)
from cotaforge.conventions import ConventionError, Conventions
from cotaforge.executor import (
    ExecutionContext,
    ExecutionTrace,
    Executor,
    ExecutorState,
    ExecutorStateError,
    StepRecord,
    StepStatus,
)
from cotaforge.executor.config import DEFAULT_CONFIG
from cotaforge.executor.errors import ResolutionError, UndoFloorError, UndoInterruptedError
from cotaforge.ir import ExtensionOperation, Plan, PlanError, parse_plan
from cotaforge.ir.refs import FaceRef, PlaneRef
from cotaforge.verification.verifier import Verifier
from pydantic import BaseModel, TypeAdapter, ValidationError

from cotaforge_mcp import (
    __version__,
    bearing_seats,
    catalog,
    checkpoint,
    design_memory,
    foreign,
    hints,
    pocket_corners,
    recovery,
    rules,
    turned_ends,
    usage,
)
from cotaforge_mcp.config import Settings
from cotaforge_mcp.hand_edits import HandEdits
from cotaforge_mcp.overflow import Overflow
from cotaforge_mcp.progress import Reporter, starting_message
from cotaforge_mcp.reply import ToolError, first_validation_issues, num, vec

STEP_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
AUTO_ID = re.compile(r"^s(\d+)$")
MAX_LISTED = 12
PLACE_FIELDS = frozenset({"face", "at", "on"})
"""The fields of a step that say where it goes: an edit of one of them is a new place, not the old one kept."""
MAX_ALIASES_LISTED = 8
# What a result shows of a long text or list before it says how much it left out and where the whole is.
MAX_PROBLEMS = 3
MAX_TEXT_LINES = 200
MAX_NATIVE_ITEMS = 5
MAX_NATIVE_OBJECTS = 8
NATIVE_OUTPUT_CHARS = 600
NATIVE_ERROR_CHARS = 1000
NATIVE_WARNING_CHARS = 300
CONNECT_WAIT_MAX_S = 180.0
"""The longest a call waits for the CAD to start (its own limits are shorter: a hello within 60 s, then two checks)."""
CONNECT_TICK_S = 2.0
"""How often a call waiting for the CAD says so (a progress notice, when the client asked for them)."""
CONNECT_CANCEL_JOIN_S = 10.0
CALL_WAIT_S = 45.0
"""The longest a call waits for an earlier call to leave the CAD (about the time an ordinary operation is given)."""
CANCELLED_WAIT_S = 15.0
"""The same, when the earlier call was given up by its client: nobody waits for it, so waiting for it is shorter."""
BUSY_SLICE_S = 1.0
RELAUNCH_RETRY_S = 30.0
"""After a failed relaunch of a crashed CAD, the first retry waits this long; each further failure doubles the wait."""
RELAUNCH_RETRY_MAX_S = 300.0
"""The longest wait between two relaunch attempts of a CAD that stays broken."""
PURPOSES = ("sketch", "detail")
DIMENSION_ATTRIBUTES = ("width", "height", "side", "diameter", "radius", "length", "chamfer", "u", "v")
"""What an entity's own dimension is called in params={dimensions:{"<entity>.<attribute>": mm}}."""
DEFAULT_PURPOSE = "detail"
_ON = TypeAdapter(PlaneRef | FaceRef)
_POINT_FIELDS = ("center", "start", "end", "through")


def slug(text: str, default: str = "part") -> str:
    cleaned = re.sub(r"[^a-z0-9_]+", "_", text.strip().lower()).strip("_")
    if not cleaned or not cleaned[0].isalpha():
        cleaned = f"{default}_{cleaned}".strip("_")
    return cleaned[:60] or default


@dataclass(frozen=True)
class StepView:
    """A successful, still-active step as the agent sees it."""

    id: str
    op: str
    feature: FeatureHandle | None


@dataclass
class ParkedDoc:
    """A document that is open in the CAD but not the active one: everything the session keeps for the active document,
    set aside until ``doc(action=switch)`` brings it back."""

    executor: Executor
    name: str
    path: Path | None
    dirty: bool
    design_id: str
    save_seq: int
    file_sha: str | None
    aliases: dict[str, FeatureHandle]
    counter: int
    volume_mm3: float | None = None
    """An assembly's volume when it was set aside: what a refresh from its parts is compared with."""
    file_stat: tuple[int, int] | None = None
    mem: Any = None
    """The memory state (roles, answers, stage) of the document."""

    @property
    def kind(self) -> DocumentKind:
        return self.executor.plan.kind

    @property
    def handle(self) -> DocumentHandle:
        context = self.executor.context
        assert context is not None
        return context.document


@dataclass(frozen=True)
class ActiveCall:
    """The call that holds the CAD now (``Session.holding``): which tool, since when, and whether its client gave up."""

    tool: str
    started: float
    gave_up: threading.Event | None = None

    @property
    def cancelled(self) -> bool:
        return self.gave_up is not None and self.gave_up.is_set()


@dataclass(frozen=True)
class OpenDoc:
    """One open document, active or not, as ``status`` and the part lookups see it."""

    name: str
    kind: DocumentKind
    path: Path | None
    dirty: bool
    handle: DocumentHandle
    executor: Executor
    active: bool


class Session:
    def __init__(self, adapter: Adapter, settings: Settings) -> None:
        self.adapter = adapter
        # the memory is shared by every harness: the adapter's own key says which entries are for this software
        self.settings = dataclasses.replace(settings, cad=getattr(adapter, "cad_key", None))
        self.conventions = Conventions(settings.home, settings.workspace)
        """The conventions of the harness (the colour of holes, ...): defaults in code, overridden by
        ``convenciones.yaml`` in the data folder and in the design space. Not memory. The adapter reads them when it
        makes a hole."""
        adapter.conventions = self.conventions
        self.lock = threading.RLock()
        self._active: ActiveCall | None = None
        """The call holding ``lock`` through ``holding`` (what ``busy_error`` tells whoever waits for it)."""
        self.info: SessionInfo | None = None
        self.connect_error: str | None = None
        self.capabilities: Capabilities = adapter.capabilities()
        """What the adapter declares without a process until ``connect`` returns, then what the connected CAD has."""
        self._connect_lock = threading.Lock()
        self._connect_thread: threading.Thread | None = None
        self._connect_done = threading.Event()
        self._closing = False
        self.executor: Executor | None = None
        self.doc_name: str = ""
        self.doc_path: Path | None = None
        self.dirty = False
        self.design_id = ""
        self.save_seq = 0
        self.file_sha: str | None = None
        """SHA-256 of the file as this server last wrote or read it (the adapter refuses to overwrite another)."""
        self.file_stat: tuple[int, int] | None = None
        """(mtime_ns, size) of the file when ``file_sha`` was taken: a change of it while a ``run_native`` fragment ran
        is that fragment saving the document itself, not somebody else."""
        self.purpose = DEFAULT_PURPOSE
        """What the document is for: ``sketch`` (a concept model) or ``detail`` (features); kept in the record."""
        self.mem = design_memory.MemState()
        """Roles, stage and the answers given at milestones: kept in the document and in the record."""
        self._aliases: dict[str, FeatureHandle] = {}
        self._parked: dict[str, ParkedDoc] = {}
        """The open documents that are not the active one, by name (oldest first)."""
        self._windowless: set[str] = set()
        """The documents this session had the CAD load without a window (the parts of an assembly it opened): closed
        again, once nothing uses them and they have no unsaved changes, when their assembly closes."""
        self._borrowed: set[str] = set()
        """The open documents that the engineer had open by hand already: closing one here only forgets it."""
        self._counter = 0
        self._calls = 0
        self._relaunch_failed: str | None = None
        """Why the CAD could not be relaunched after it crashed: the calls before ``_relaunch_retry_at`` are refused
        with it, the first one after tries again (``recover_if_lost``)."""
        self._relaunch_attempts = 0
        self._clock = time.monotonic
        self._relaunch_retry_at = 0.0
        """``time.monotonic()`` before which no new relaunch is tried: ``RELAUNCH_RETRY_S`` after the first failure,
        twice as long after each next one up to ``RELAUNCH_RETRY_MAX_S``. Only a call tries (never a timer), one attempt
        per call, so a CAD that stays broken is asked at most once per interval however often the agent calls."""
        self._crash_state: tuple[Any, ...] | None = None
        """What the dead session held (document, steps, recovery copy...), kept while a relaunch has not succeeded: the
        retry reports it, not the empty state the first attempt left behind."""
        self.hand_edits = HandEdits()
        """What the engineer changed by hand since the last call: ``tools.call_tool`` puts it at the start of the next
        reply. Registered once here, on the adapter object, which keeps it across a relaunch of the CAD. Only changes
        of the design are pushed; the selection is not (``SelectionProducer`` stays unregistered on purpose: the agent
        reads it with ``view(action=selection)`` when the engineer asks)."""
        if isinstance(adapter, ManualEditProducer):
            adapter.set_manual_edit_listener(self._on_hand_edit)

    # --- plumbing ----------------------------------------------------------------------------------------------

    def _on_hand_edit(self, notice: Any) -> None:
        """A change of the design made in the CAD: it goes to the next reply, and the executor stops reusing the measure
        its last step left (the part is no longer what that step measured). Runs on the adapter's reader thread."""
        self.hand_edits.push(notice)
        executor = self.executor
        if executor is not None:
            executor.invalidate_measure()

    def _op_id(self, what: str) -> str:
        self._calls += 1
        return f"{what}.{self._calls}"

    def connect(self) -> None:
        try:
            self.info = self.adapter.connect(ConnectRequest(operation_id="connect"))
            self.capabilities = self.adapter.capabilities()
            self.connect_error = None
        except AdapterError as exc:
            self.connect_error = exc.info.message

    # --- the connection in the background ----------------------------------------------------------------------------
    # Starting the CAD takes seconds (minutes on a cold machine), far more than an MCP client waits for the handshake:
    # the server answers ``initialize`` and ``tools/list`` from what the adapter declares without a process
    # (``capabilities``), connects in a thread, and a call that needs the CAD waits for that thread.

    @property
    def connecting(self) -> bool:
        """The background connect (``begin_connect``) has started and is not over."""
        return self._connect_thread is not None and not self._connect_done.is_set()

    def begin_connect(self, on_failure: Callable[[str], None] | None = None) -> None:
        """Connect in a thread and return at once; once only. ``on_failure`` hears why the CAD did not start.
        ``disconnect`` stops it."""
        with self._connect_lock:
            if self._connect_thread is not None or self._closing:
                return
            thread = threading.Thread(
                target=self._connect_in_background, args=(on_failure,), name="cad-connect", daemon=True
            )
            self._connect_thread = thread
            try:
                thread.start()
            except RuntimeError as exc:  # no thread to be had: every call answers why, none waits for a connect
                self.connect_error = f"could not start the connection to the CAD: {exc}"
                self._connect_done.set()
                failure = self.connect_error
            else:
                return
        if on_failure is not None:
            on_failure(failure)

    def _connect_in_background(self, on_failure: Callable[[str], None] | None) -> None:
        try:
            self.connect()
        except Exception as exc:  # noqa: BLE001 - a thread must report, not die: every call answers this
            self.connect_error = f"{type(exc).__name__}: {exc}"
        finally:
            if self._closing:  # the server went away meanwhile: nothing may stay running
                self._release_adapter()
            self._connect_done.set()
        if self.connect_error is not None and on_failure is not None and not self._closing:
            on_failure(self.connect_error)

    def wait_for_cad(self, progress: Reporter | None = None, timeout_s: float = CONNECT_WAIT_MAX_S) -> None:
        """For a call that needs the CAD: wait for the background connect (``progress`` hears every few seconds that
        the CAD is starting), then refuse with the reason when the CAD did not start. A no-op once connected."""
        if self.connecting:
            started = time.monotonic()
            while not self._connect_done.wait(CONNECT_TICK_S):
                elapsed = time.monotonic() - started
                if progress is not None:
                    progress(ProgressNotice(phase="connecting", message=starting_message(elapsed), elapsed_s=elapsed))
                if elapsed >= timeout_s or self._closing:
                    raise self.still_starting(elapsed)
        self.require_cad()

    def still_starting(self, waited_s: float) -> ToolError:
        return ToolError(
            f"the CAD software is still starting after {waited_s:.0f} s (a cold start can take a minute or more): "
            "repeat the call in a moment",
            code="cad_starting",
        )

    def require_cad(self) -> None:
        """Refuse a call that needs the CAD when it did not start (the reason, and that only a restart retries)."""
        if self.connect_error is not None:
            raise ToolError(
                f"the CAD software did not start: {self.connect_error}",
                code="cad_unavailable",
                usage="fix the cause above, then restart the cad server (in Claude Code: /mcp, reconnect cad)",
            )

    def disconnect(self) -> None:
        """Release the CAD. A connect still running in its thread is cancelled (the process it is starting is stopped,
        by its own handle) and the thread is waited for briefly: no CAD process is left behind."""
        with self._connect_lock:  # after this no begin_connect starts a thread
            self._closing = True
            thread = self._connect_thread
        if thread is not None and not self._connect_done.is_set():
            if isinstance(self.adapter, ConnectCancellable):
                try:
                    self.adapter.cancel_connect()
                except Exception:  # noqa: BLE001 - shutting down
                    pass
            thread.join(timeout=CONNECT_CANCEL_JOIN_S)
        self._release_adapter()

    def _release_adapter(self) -> None:
        try:
            self.adapter.disconnect(Empty(operation_id="disconnect"))
        except Exception:  # noqa: BLE001 - shutting down; nothing useful to do with a late failure
            pass

    @staticmethod
    def adapter_failure(exc: AdapterError, what: str = "") -> ToolError:
        info = exc.info
        hint = ""
        if info.code.value == "no_active_document":
            hint = "open one with doc(action=new) or doc(action=open, path=...)"
        elif info.code.value == "session_lost" and info.message == "not connected":  # it never started
            hint = "the CAD software is not connected; restart the server"
        elif info.code.value == "session_lost":
            hint = "the CAD software stopped; the next call relaunches it and reopens the last saved file"
        elif info.code.value == "degraded_session":
            hint = "the CAD session is not usable any more; restart the server"
        elif info.code.value == "not_supported":
            hint = "this CAD does not support it; see cad_guide(topic=traps)"
        message = f"{what}{': ' if what else ''}{info.message}"
        return ToolError(f"{message}\nhint: {hint}" if hint else message, code=info.code.value)

    def _stat_of_file(self) -> tuple[int, int] | None:
        if self.doc_path is None:
            return None
        try:
            info = self.doc_path.stat()
        except OSError:
            return None
        return info.st_mtime_ns, info.st_size

    def _own_native_write(self, before: tuple[int, int] | None) -> list[str]:
        """The file of the document was rewritten while a ``run_native`` fragment ran (``doc.saveAs`` or ``doc.save``
        in it) and nobody had changed it since this server last read or wrote it: that write is this session's, so its
        hash becomes the one the next save expects instead of a ``file_conflict``."""
        if before is None or before != self.file_stat or self.doc_path is None:
            return []
        now = self._stat_of_file()
        if now is None or now == before:
            return []
        try:
            self.file_sha = checkpoint.sha256_file(self.doc_path)
        except OSError:
            return []
        self.file_stat = now
        return [
            f"note: the fragment saved {self.doc_path.name} itself; the server took that file as its own (the next "
            "doc(action=save) does not report a conflict)"
        ]

    def supports(self, *tools: str) -> bool:
        return any(t in self.capabilities.tools for t in tools)

    def need(self, tool: str, what: str) -> None:
        if tool not in self.capabilities.tools:
            raise ToolError(f"{what} is not available with this CAD ({self.adapter.name})", code="not_supported")

    @property
    def language(self) -> str:
        """The language this project speaks to the engineer (``es`` / ``en``): ``brief.yaml``'s, read when asked."""
        return design_memory.project_language(self.settings)

    def require_executor(self) -> Executor:
        if self.executor is None:
            raise ToolError(
                "no document is open",
                code="no_document",
                usage='doc(action="new", name=...) | doc(action="open", path=...)',
                example='doc(action="new", name="bracket")',
            )
        if self.executor.state is ExecutorState.degraded:
            raise ToolError(
                "the CAD session is degraded after a failure; save if you can, then doc(action=close) and reopen",
                code="degraded_session",
            )
        return self.executor

    # --- crash recovery ------------------------------------------------------------------------------------------

    def recover_if_lost(self, tool: str) -> None:
        """Before every tool call: when the CAD process this server launched has ended (a crash, or the kill that
        follows a timeout), relaunch it in the same mode, reopen the last saved file and refuse THIS call with the
        report of what was lost, so the agent reads it before anything else runs. At most one relaunch per call. When
        it fails (the dead instance may still show its error dialog), that failure answers the calls that come before
        ``_relaunch_retry_at``, and the first call after it tries again; the wait is ``RELAUNCH_RETRY_S`` and doubles
        with every failure up to ``RELAUNCH_RETRY_MAX_S``, so a CAD that stays broken is asked at most once per
        interval and nothing retries on its own."""
        if self._relaunch_failed is None and (self.info is None or self.adapter.session_alive()):
            return  # the common case takes no lock: a call busy in the CAD does not hold up the check
        with self.holding(tool):
            if self._relaunch_failed is not None:
                wait = self._relaunch_retry_at - self._clock()
                if wait > 0:
                    raise ToolError(
                        f"{self._relaunch_failed}\nnext attempt: the first call in {wait:.0f} s relaunches it again",
                        code="cad_relaunch_failed",
                    )
            elif self.info is None or self.adapter.session_alive():
                return
            raise ToolError("\n".join(self._relaunch(tool)), code="cad_relaunched")

    # --- one call at a time in the CAD ------------------------------------------------------------------------

    @contextlib.contextmanager
    def holding(self, tool: str, cancelled: threading.Event | None = None) -> Iterator[None]:
        """Hold the session for one call that works in the CAD. The wait for an earlier call is bounded: it ends in
        the error ``cad_busy`` that names what is still running (``busy_error``), so no call waits behind another for
        as long as that one takes. Re-entrant: a call that already holds it goes on. ``cancelled`` is set by the server
        when the client gave the call up: its thread still runs to its end (a running CAD operation cannot be
        interrupted), but whoever waits for it stops waiting sooner and is told so."""
        started = time.monotonic()
        waited = 0.0
        while not self.lock.acquire(timeout=BUSY_SLICE_S):
            waited = time.monotonic() - started
            earlier = self._active
            limit = CANCELLED_WAIT_S if earlier is not None and earlier.cancelled else CALL_WAIT_S
            if waited >= limit:
                raise self.busy_error(tool, waited)
        outer = self._active
        self._active = ActiveCall(tool, time.monotonic(), cancelled)
        try:
            yield
        finally:
            self._active = outer
            self.lock.release()

    def busy_error(self, tool: str, waited_s: float) -> ToolError:
        earlier = self._active
        lines = []
        if earlier is None:
            lines.append(f"the CAD software is still busy with an earlier call; {tool} waited {waited_s:.0f} s for it")
        else:
            lines.append(
                f"the CAD software is still busy with an earlier call: {earlier.tool}, running for "
                f"{time.monotonic() - earlier.started:.0f} s; {tool} waited {waited_s:.0f} s for it"
            )
            if earlier.cancelled:
                lines.append("the client gave that call up, but the CAD cannot interrupt an operation that is running")
        lines.append(
            self.crash_note("busy")
            or "it ends by itself, or when FreeCAD is closed: the next call then relaunches it and reopens the last "
            "saved file (what was not saved is lost)"
        )
        lines.append("next: repeat the call in a moment")
        return ToolError("\n".join(lines), code="cad_busy")

    def lost_note(self) -> str:
        """For a call the CAD process ended in the middle of: what the session held that went with it and how to go on.
        Nothing is asked of the CAD (it is gone); the next call relaunches it and says what it reopened."""
        lines = []
        copy = recovery.after_crash(self) if self.executor is not None else None
        if copy is not None:
            lines.append("lost: nothing that the recovery copy below does not hold (only what the fragment did itself)")
        elif self.executor is None:
            lines.append("lost: nothing, no document was open")
        elif self.doc_path is None or not self.doc_path.is_file():
            lines.append(
                f"lost: {self.doc_name} was never saved; its {len(self.steps())} step(s) went with the process"
            )
        elif self.dirty:
            lines.append(f"lost: everything in {self.doc_name} after its last save ({self.doc_path})")
        else:
            lines.append(f"lost: nothing, {self.doc_name} had no unsaved changes ({self.doc_path})")
        unsaved = [p.name for p in self._parked.values() if p.dirty]
        if unsaved:
            lines.append(f"also lost, open and unsaved: {', '.join(unsaved)}")
        if copy is not None:
            lines.extend(copy.lines(self.doc_path if self.doc_path is not None and self.doc_path.is_file() else None))
        lines.append(
            self.crash_note("resume")
            or "resume: repeat any call; it relaunches FreeCAD, reopens the last saved file and says what it found"
        )
        return "\n".join(lines)

    def crash_note(self, key: str) -> str | None:
        """The adapter's own wording of a sentence about a crash (``guide_topic("note:crash.<key>")``), whole, or
        ``None`` for the server's default (the FreeCAD one). Keys: ``busy`` (how a long call ends), ``resume`` (the
        line that says how to go on after a crash), ``native`` (what follows the fragment that took the CAD down; the
        empty text says nothing more). Nothing is asked of the CAD: the text is the adapter's, not a call to it."""
        return self.adapter.guide_topic(f"note:crash.{key}")

    def _relaunch(self, tool: str) -> list[str]:
        if self._crash_state is None:  # the first attempt: read what the dead session held, then forget it
            had_document = self.executor is not None
            name, path, dirty, before = self.doc_name, self.doc_path, self.dirty, self.steps()
            # before the state of the dead CAD is forgotten
            kept = recovery.after_crash(self) if had_document else None
            lost = {p.name: p.dirty for p in self._parked.values()}
            self._crash_state = (had_document, name, path, dirty, before, kept, lost)
            self._forget_document()  # the process that held it is gone, and its undo journal with it
            self._parked.clear()
            self._windowless.clear()
            self._borrowed.clear()
            self.hand_edits.clear()  # they described a model that no longer exists
        had_document, name, path, dirty, before, kept, lost = self._crash_state  # a retry reports the same loss
        mode = "visible" if getattr(self.adapter, "visible", self.settings.visible) else "headless"
        try:
            self.info = self.adapter.connect(ConnectRequest(operation_id="relaunch"))
            self.capabilities = self.adapter.capabilities()
        except AdapterError as exc:
            saved = f"; {path} keeps the last save" if path is not None and path.is_file() else ""
            self._relaunch_attempts += 1
            wait = min(RELAUNCH_RETRY_S * 2 ** (self._relaunch_attempts - 1), RELAUNCH_RETRY_MAX_S)
            self._relaunch_retry_at = self._clock() + wait
            self._relaunch_failed = (
                f"the CAD software crashed (its process ended) and could not be relaunched: {exc.info.message}\n"
                f"nothing was reopened{saved}; everything not saved is lost\n"
                "what to do: fix the cause above (the CAD log is named in it when there is one); the first call "
                f"after {wait:.0f} s relaunches it again (the wait doubles with every failure, up to "
                f"{RELAUNCH_RETRY_MAX_S:.0f} s; calls before that answer this), or restart the cad server (in "
                "Claude Code: /mcp, reconnect cad)"
            )
            raise ToolError(self._relaunch_failed, code="cad_relaunch_failed") from exc
        self._relaunch_failed = None
        self._relaunch_attempts = 0
        self._crash_state = None
        lines = [
            f"the CAD software crashed (its process ended) and was relaunched ({mode}); your {tool} call was NOT run"
        ]
        if not had_document:
            lines.append("no document was open: nothing was lost")
        elif path is None or not path.is_file():
            if kept is not None:
                lines.append(f"no saved document: {name} was never saved; no document is open")
                lines.extend(kept.lines(None))
            else:
                lines.append(
                    f"no saved document: {name} was never saved, its {len(before)} step(s) are lost; "
                    "no document is open"
                )
                lines.append('next: doc(action="new", name=...) and build it again')
        else:
            lines.extend(self._reopen_after_crash(path, dirty, before))
            if kept is not None:
                lines.extend(kept.lines(path))
        gone = [
            f"{n}{' (UNSAVED changes lost)' if unsaved else ''}"
            for n, unsaved in lost.items()
            if self._open_doc_named(n) is None
        ]
        if gone:  # the parts of a reopened assembly come back with it; the rest were open in the process that died
            lines.append(
                f"also open and gone with the process: {', '.join(gone)} (what was saved stays on disk; "
                "doc(action=open, path=...) reopens each one)"
            )
        lines.append("undo: the undo history does not survive a relaunch; no step from before it can be undone")
        lines.append("next: check the state above, then repeat your call if it still applies")
        return lines

    def _reopen_after_crash(self, path: Path, dirty: bool, before: list[StepView]) -> list[str]:
        try:
            opened = self.open_document(str(path))
        except ToolError as exc:
            again = " (it crashed the CAD software again)" if exc.code == "session_lost" else ""
            return [
                f"reopening {path} failed{again}: {exc.message.splitlines()[0]}",
                f'no document is open; everything since the last save is lost; doc(action="open", path="{path}") '
                "retries",
            ]
        after = self.steps()
        last = f"; last saved step {after[-1].id} {after[-1].op}" if after else ""
        lines = [f"reopened: {path} (save {self.save_seq}{last})"]
        if not dirty:
            lines.append("lost: nothing, the document had no unsaved changes")
        else:
            kept = {step.id for step in after}
            gone = [f"{step.id} {step.op}" for step in before if step.id not in kept]
            lines.append(
                "lost: everything after that save"
                + (f": {', '.join(gone)}" if gone else " (changes made since then that were not steps)")
            )
        return [*lines, *opened]

    # --- measuring -----------------------------------------------------------------------------------------------

    def measure(self, quick: bool = False) -> tuple[MassProperties, BoundingBox]:
        """Volume and box of the document. The surface area is not measured (``inspect properties`` asks for it); with
        ``quick`` a body a file brought in may answer its quick box (``BoundingBox.exact`` says so) instead of the exact
        one, which is a pass over every curved surface."""
        op = self._op_id("measure")
        try:
            return (
                self.adapter.mass_properties(MassRequest(operation_id=op, surface=False, centroid=False)),
                self.adapter.bounding_box(MassRequest(operation_id=op, exact_box=not quick)),
            )
        except AdapterError as exc:
            raise self.adapter_failure(exc, "measure") from exc

    def validity_result(self, deep: bool = False) -> ValidityResult:
        try:
            return self.adapter.is_valid(ValidityRequest(operation_id=self._op_id("valid"), deep=deep))
        except AdapterError as exc:
            return ValidityResult(valid=False, problems=(exc.info.message,))

    def validity(self, deep: bool = False) -> tuple[bool, tuple[str, ...]]:
        """Is the geometry valid. ``deep`` also runs the CAD's slow self-intersection check on the bodies of a
        customer's file that nothing has changed (``inspect(action=validity)``); by default those get the cheap one."""
        result = self.validity_result(deep)
        return result.valid, result.problems

    def state_lines(self, before: MassProperties | None = None, *, just_measured: bool = False) -> list[str]:
        """The volume, mass, box and validity lines of the part. ``just_measured``: the step that ended a moment ago
        measured the part and nothing has touched it since, so its measure is used and not asked again."""
        known = self.executor.last_measure(quick=True) if just_measured and self.executor is not None else None
        mass, bbox = known if known is not None else self.measure(quick=True)
        volume = f"volume: {num(mass.volume_mm3)} mm3"
        if before is not None:
            delta = mass.volume_mm3 - before.volume_mm3
            volume += f" ({'+' if delta >= 0 else ''}{num(delta)})"
        if mass.surface_only:
            volume += f" ({foreign.surface_only_wording(len(mass.surface_only), mass.centroid_meaningful)})"
        size = bbox.size()
        box = f"bbox: size {vec(size)} mm, min {vec(bbox.min)}, max {vec(bbox.max)}"
        if not bbox.exact:
            box += " (quick box: it may enclose a little more than the shape; inspect measure gives the exact one)"
        if bbox.left_out:
            box += f"; without faces that have no real size: {'; '.join(bbox.left_out[:4])}"
        measured = [measured_mass_line(mass)] if mass.mass_kg is not None else []
        return [volume, *measured, box, self._validity_line(self.validity_result())]

    def _validity_line(self, checked: ValidityResult) -> str:
        """The ``valid:`` line. A body whose check was left for later is never counted as valid: when none was
        checked the line says so, and when some were it says for how many."""
        if not checked.valid:
            verdict = "valid: NO - " + self.problems_text(checked.problems)
        elif checked.unchecked and not checked.checked:
            verdict = "valid: not checked"
        elif checked.unchecked:
            verdict = f"valid: yes for the {checked.checked} checked bodies"
        else:
            verdict = "valid: yes"
        if checked.unchecked:
            labels = ", ".join(self.body_label(b) for b in checked.unchecked[:4])
            more = f" and {len(checked.unchecked) - 4} more" if len(checked.unchecked) > 4 else ""
            verdict += f" (not checked: {labels}{more}, thousands of loose faces; inspect validity checks them)"
        return verdict

    def problems_text(self, problems: tuple[str, ...]) -> str:
        """The validity problems as a result shows them: all of them when they are few; otherwise how many, the first
        ones and the file with the whole report."""
        report = Overflow(self.settings.workspace, "validity", f"validity report of {self.doc_name or 'the document'}")
        shown, more = report.items("problems", list(problems), MAX_PROBLEMS)
        if more is None:
            return "; ".join(shown)
        warning = report.write()
        return f"{len(problems)} problems: {'; '.join(shown)}; {more}" + (f" ({warning})" if warning else "")

    # --- history -------------------------------------------------------------------------------------------------

    def steps(self) -> list[StepView]:
        """Successful steps that are still in effect, oldest first."""
        if self.executor is None:
            return []
        out = []
        for record in self.executor.trace().active_steps():
            if record.status is StepStatus.success:
                out.append(StepView(record.op_id, plain_op_name(record.op), _feature_of(record)))
        return out

    def known_ids(self) -> set[str]:
        return {s.id for s in self.steps()} | set(self._aliases)

    def next_id(self) -> str:
        used = self.known_ids()
        while True:
            self._counter += 1
            candidate = f"s{self._counter}"
            if candidate not in used:
                return candidate

    @property
    def aliases(self) -> dict[str, FeatureHandle]:
        """Features adopted from an opened file, by the alias the agent uses for them."""
        return dict(self._aliases)

    def step_of_feature(self, feature_id: str) -> str | None:
        for alias, feature in self._aliases.items():
            if feature.id == feature_id:
                return alias
        for step in self.steps():
            if step.feature is not None and step.feature.id == feature_id:
                return step.id
        return None

    # --- bodies: by step or by id ----------------------------------------------------------------------------------

    def named_bodies(self) -> dict[str, dict[str, str]]:
        """Body id -> the reference by step that names it now, ``{kind: body, from_op[, side]}``: the LAST step in
        effect that names the body (a later move of it is what a new op builds on). Oldest body first."""
        ctx = self.executor.context if self.executor is not None else None
        if ctx is None:
            return {}
        named: dict[str, dict[str, str]] = {}
        for op_id in [*self._aliases, *(s.id for s in self.steps())]:
            sides = ctx.body_sides.get(op_id)
            if sides and len(sides) > 1:
                for side, body in sides.items():
                    named[body.id] = {"kind": "body", "from_op": op_id, "side": side}
            elif op_id in ctx.body_by_op:
                named[ctx.body_by_op[op_id].id] = {"kind": "body", "from_op": op_id}
        return named

    @staticmethod
    def _label_of(body_id: str, ref: dict[str, str] | None) -> str:
        if ref is None:
            return body_id
        return f"{body_id} [{ref['from_op']}{' ' + ref['side'] if 'side' in ref else ''}]"

    def body_label(self, body_id: str) -> str:
        """``body:2 [s3]``: the id and the step that names the body (``[s4 negative]`` for one piece of a split)."""
        return self._label_of(body_id, self.named_bodies().get(body_id))

    def body_labels(self, body_ids: list[str]) -> dict[str, str]:
        """``body_label`` of many bodies from one look at the steps (not one per body: a file of hundreds of bodies)."""
        named = self.named_bodies()
        return {body_id: self._label_of(body_id, named.get(body_id)) for body_id in body_ids}

    def component_label(self, component_id: str) -> str:
        """``comp:2 [ins_pin]``: the id and the step that inserted the component."""
        context = self.executor.context if self.executor is not None else None
        if context is not None:
            for op_id, handle in context.component_by_op.items():
                if handle.id == component_id:
                    return f"{component_id} [{op_id}]"
        return component_id

    def bodies_listing(self) -> str:
        return ", ".join(
            f"{body} = {ref['from_op']}{' ' + ref['side'] if 'side' in ref else ''}"
            for body, ref in self.named_bodies().items()
        )

    def step_naming(self, text: str) -> str | None:
        """The step that ``text`` stands for: a step id or adopted alias itself, or the step that names a body id."""
        if text in self.known_ids():
            return text
        ref = self.named_bodies().get(text)
        return ref["from_op"] if ref is not None else None

    def body_handle(self, text: str, field_name: str) -> BodyHandle:
        """A body named by the step that made it (or an adopted alias) or by its id: both mean the same body."""
        executor = self.require_executor()
        if text not in self.known_ids():
            return BodyHandle(id=text)
        ctx = executor.context
        sides = ctx.body_sides.get(text) if ctx is not None else None
        if sides and len(sides) > 1:
            pieces = ", ".join(f"{side} = {body.id}" for side, body in sides.items())
            raise ToolError(
                f"step {text!r} split the body in two ({pieces}): name the piece by its id",
                code="invalid_input",
                field_name=field_name,
                example=f'{field_name}="{next(iter(sides.values())).id}"',
            )
        body = ctx.body_by_op.get(text) if ctx is not None else None
        if body is None:
            listing = self.bodies_listing()
            raise ToolError(
                f"step {text!r} does not name a body" + (f"; bodies: {listing}" if listing else ""),
                code="invalid_input",
                field_name=field_name,
                usage=f'{field_name}="<step that made the body>" or {field_name}="<body id>"',
            )
        return body

    # --- documents -----------------------------------------------------------------------------------------------

    def _fresh_executor(self, name: str, kind: DocumentKind) -> Executor:
        return Executor(Plan(id=slug(name), kind=kind, operations=()), self.adapter, Verifier())

    # --- several open documents ----------------------------------------------------------------------------------

    def open_docs(self) -> list[OpenDoc]:
        """Every open document, the active one first, then the others in the order they were set aside."""
        docs: list[OpenDoc] = []
        if self.executor is not None and self.executor.context is not None:
            docs.append(
                OpenDoc(
                    self.doc_name,
                    self.executor.plan.kind,
                    self.doc_path,
                    self.dirty,
                    self.executor.context.document,
                    self.executor,
                    True,
                )
            )
        docs.extend(
            OpenDoc(p.name, p.kind, p.path, p.dirty, p.handle, p.executor, False) for p in self._parked.values()
        )
        return docs

    def _open_doc_named(self, name: str) -> OpenDoc | None:
        return next((d for d in self.open_docs() if d.name == name), None)

    def _open_doc_of_file(self, path: Path) -> OpenDoc | None:
        return next((d for d in self.open_docs() if d.path is not None and _same_path(d.path, path)), None)

    def _others_lines(self) -> list[str]:
        others = [f"{d.name} ({d.kind.value})" for d in self.open_docs() if not d.active]
        if not others:
            return []
        return [f"other open documents: {', '.join(others)} (doc(action=switch, name=...) changes the active one)"]

    def documents_lines(self) -> list[str]:
        docs = self.open_docs()
        lines = [f"documents: {len(docs)} open"]
        for d in docs:
            file = d.path.name if d.path is not None else "not saved yet"
            active = " [active]" if d.active else ""
            unsaved = " (UNSAVED changes)" if d.dirty else ""
            lines.append(f"  {d.name} ({d.kind.value}){active}: {file}{unsaved}")
        return lines

    def _users_of(self, handle: DocumentHandle) -> list[str]:
        """The open assemblies whose steps insert the document ``handle``."""
        return [
            d.name for d in self.open_docs() if handle in d.executor.documents.values() and d.handle.id != handle.id
        ]

    def _park_active(self, measure: bool = True) -> ParkedDoc | None:
        """Set the active document aside (it stays open in the CAD with its history); no document is active after.
        ``measure=False``: an assembly's volume is not taken (it comes back at once, nothing changes in between)."""
        executor = self.executor
        if executor is None:
            return None
        volume: float | None = None
        if measure and executor.plan.kind is DocumentKind.assembly:
            try:
                volume = self.measure()[0].volume_mm3
            except (AdapterError, ToolError):
                volume = None
        parked = ParkedDoc(
            executor,
            self.doc_name,
            self.doc_path,
            self.dirty,
            self.design_id,
            self.save_seq,
            self.file_sha,
            dict(self._aliases),
            self._counter,
            volume,
            file_stat=self.file_stat,
            mem=self.mem,
        )
        self._parked[parked.name] = parked
        self._forget_document()
        return parked

    def _resume(self, parked: ParkedDoc) -> None:
        """Make a parked document the active one again, in the CAD and here. An assembly is brought up to date with the
        part documents it uses first (a part may have been saved again since)."""
        self.adapter.set_active_document(
            SetActiveDocumentRequest(
                operation_id=self._op_id("switch"),
                document=parked.handle,
                refresh=parked.kind is DocumentKind.assembly,
            )
        )
        self._parked.pop(parked.name, None)
        self.executor = parked.executor
        self.doc_name = parked.name
        self.doc_path = parked.path
        self.dirty = parked.dirty
        self.design_id = parked.design_id
        self.save_seq = parked.save_seq
        self.file_sha = parked.file_sha
        self.file_stat = parked.file_stat
        self._aliases = parked.aliases
        self._counter = parked.counter
        self.mem = parked.mem or design_memory.MemState()

    def _back(self, previous: ParkedDoc | None) -> None:
        """After a failure that set the active document aside: make it the active one again."""
        if previous is None:
            return
        try:
            self._resume(previous)
        except (AdapterError, ToolError):
            pass  # the CAD lost it: the next call says so

    def _refuse_taken_name(self, name: str) -> None:
        if self._open_doc_named(name) is not None:
            raise ToolError(
                f"a document named {name!r} is already open: names identify documents (doc switch, the part of "
                "insert_component), so each open document needs its own",
                code="name_taken",
                field_name="name",
                usage='doc(action="new", name="<another name>") | doc(action="switch", name="<open document>")',
            )

    def switch_document(self, name: str | None) -> list[str]:
        with self.lock:
            if not name:
                raise ToolError(
                    "switch needs the name of an open document",
                    code="invalid_input",
                    field_name="name",
                    usage='doc(action="switch", name="<open document>")',
                    example='doc(action="switch", name="bracket")',
                )
            if self.executor is not None and name == self.doc_name:
                return [f"{name} is already the active document", *self.status()]
            target = self._parked.get(name)
            if target is None:
                open_names = [d.name for d in self.open_docs()]
                raise ToolError(
                    f"no open document named {name!r}" + (f"; open: {', '.join(open_names)}" if open_names else ""),
                    code="unknown_document",
                    field_name="name",
                    usage='doc(action="switch", name="<open document>") | doc(action="open", path=...)',
                )
            previous = self._park_active()
            try:
                self._resume(target)
            except AdapterError as exc:
                self._back(previous)
                raise self.adapter_failure(exc, "switch") from exc
            lines = [f"active: {name} ({target.kind.value})"]
            if target.kind is DocumentKind.assembly:
                lines.extend(self._refreshed_lines(target))
            lines.extend(self.state_lines())
            return [*lines, *self._others_lines()]

    def _refreshed_lines(self, parked: ParkedDoc) -> list[str]:
        """What bringing an assembly up to date with its parts did: its volume against the one it had when it was set
        aside."""
        now = self.measure()[0].volume_mm3
        if parked.volume_mm3 is None or abs(now - parked.volume_mm3) <= 1e-6:
            return ["components: up to date with their part documents"]
        self.dirty = True
        return [
            f"components updated from their part documents: volume was {num(parked.volume_mm3)} mm3, "
            f"now {num(now)} mm3 (a part was changed after it was inserted)"
        ]

    def new_document(self, name: str, kind: str) -> list[str]:
        with self.lock:
            self._refuse_taken_name(name)
            doc_kind = DocumentKind(kind)
            previous = self._park_active()
            executor = self._fresh_executor(name, doc_kind)
            try:
                executor.start()
            except AdapterError as exc:
                self._back(previous)
                raise self.adapter_failure(exc, "new document") from exc
            if executor.state is ExecutorState.degraded:
                self._back(previous)
                raise ToolError("the CAD canary failed; the session is not healthy", code="degraded_session")
            self._begin(executor, name, None, uuid.uuid4().hex, 0, None)
            return [f"document: {name} ({kind})", *self.state_lines(), *self._others_lines()]

    def _begin(
        self, executor: Executor, name: str, path: Path | None, design_id: str, save_seq: int, file_sha: str | None
    ) -> None:
        self.executor = executor
        self.doc_name = name
        self.doc_path = path
        self.dirty = False
        self.design_id = design_id
        self.save_seq = save_seq
        self.file_sha = file_sha
        self.file_stat = self._stat_of_file()
        self._aliases = {}
        self._counter = 0
        self.purpose = DEFAULT_PURPOSE
        self.mem = design_memory.MemState()

    # --- open ----------------------------------------------------------------------------------------------------

    def open_document(self, path: str) -> list[str]:
        self.need("open_document", "doc(action=open)")
        with self.lock:
            target = self.settings.resolve(path)
            if not target.is_file():
                raise ToolError(f"{target} does not exist or is not a file", code="not_found", field_name="path")
            already = self._open_doc_of_file(target)
            if already is not None:
                raise ToolError(
                    f"{target.name} is already open as {already.name!r}"
                    + ("" if already.active else ': doc(action="switch", name="' + already.name + '")'),
                    code="document_open",
                    field_name="path",
                )
            previous = self._park_active()
            try:
                lines = self._open_active(target)
            except BaseException:
                self._back(previous)
                raise
            return [*lines, *self._others_lines()]

    @contextlib.contextmanager
    def listening(self, progress: Reporter | None) -> Iterator[None]:
        """While a call that may take long runs (an insert, a measure of hundreds of bodies), the adapter's heartbeat
        and progress reach the client as progress notices, as for an import (nothing is sent if the client did not ask
        for progress)."""
        wired = progress is not None and isinstance(self.adapter, ProgressProducer)
        if wired:
            self.adapter.set_progress_listener(progress)  # type: ignore[attr-defined]
        try:
            yield
        finally:
            if wired:
                self.adapter.set_progress_listener(None)  # type: ignore[attr-defined]

    def import_document(
        self,
        path: str | None,
        name: str | None,
        overwrite: bool = False,
        progress: Reporter | None = None,
        sew: bool = False,
        sew_tolerance_mm: float = DEFAULT_SEW_TOLERANCE_MM,
    ) -> list[str]:
        """A customer's STEP/IGES file (or what the CAD reads besides) as a NEW part document of reference bodies, saved
        at once as ``<name>.<ext>`` in the design space so an assembly can insert it. The current document is set
        aside, as ``new`` does. ``progress``
        hears how far the work is while it runs (reading, adopting and checking the bodies, saving)."""
        self.need("import_geometry", "doc(action=import)")
        usage_text = foreign.import_usage(self.adapter.import_suffixes())
        with self.lock:
            if not path:
                raise ToolError(
                    "import needs the path of the file",
                    code="invalid_input",
                    field_name="path",
                    usage=usage_text,
                    example='doc(action="import", path="chassis_bracket.step", name="bracket")',
                )
            source = self.settings.resolve(path)
            foreign.check_suffix(source, self.adapter.import_suffixes(), self.adapter.display_name or "this CAD")
            if not source.is_file():
                raise ToolError(f"{source} does not exist or is not a file", code="not_found", field_name="path")
            if sew and not 0 < sew_tolerance_mm <= MAX_SEW_TOLERANCE_MM:
                raise ToolError(
                    f"sew_tolerance_mm must be above 0 and at most {MAX_SEW_TOLERANCE_MM:g} mm, "
                    f"got {sew_tolerance_mm:g}",
                    code="invalid_input",
                    field_name="sew_tolerance_mm",
                    usage=usage_text[:-1] + ", sew=true, sew_tolerance_mm=0.01)",
                )
            name = name or slug(source.stem)
            if not re.fullmatch(r"[\w][\w .-]*", name):
                raise ToolError(
                    f"{name!r} cannot name a document: it also names the file "
                    f"{name}{self.adapter.suffix_for(DocumentKind.part)} "
                    "(letters, digits, space, _ . -)",
                    code="invalid_input",
                    field_name="name",
                    usage=usage_text,
                )
            self._refuse_taken_name(name)
            target = self.settings.workspace / f"{name}{self.adapter.suffix_for(DocumentKind.part)}"
            if target.exists() and not overwrite:
                raise ToolError(
                    f"{target.name} already exists in the design space; importing would overwrite it",
                    code="file_exists",
                    field_name="name",
                    usage=f'doc(action="import", path=..., name="<another name>") | doc(action="import", path=..., '
                    f'name="{name}", overwrite=true) to replace that file | doc(action="open", path="{target.name}")',
                )
            if self._open_doc_of_file(target) is not None:
                raise ToolError(
                    f"{target.name} is open as a document: close it before importing over its file",
                    code="document_open",
                    field_name="name",
                )
            previous = self._park_active()
            listening = progress is not None and isinstance(self.adapter, ProgressProducer)
            try:
                if listening:
                    self.adapter.set_progress_listener(progress)  # type: ignore[attr-defined]
                result = self.adapter.import_geometry(
                    ImportGeometryRequest(
                        operation_id=self._op_id("import"),
                        path=str(source),
                        name=name,
                        sew=sew,
                        sew_tolerance_mm=sew_tolerance_mm,
                    )
                )
            except AdapterError as exc:
                self._back(previous)
                raise self._import_failure(exc, source) from exc
            finally:
                if listening:
                    self.adapter.set_progress_listener(None)  # type: ignore[attr-defined]
            try:
                if progress is not None:
                    progress.say("linking", f"linking the {len(result.bodies)} bodies to steps")
                executor, listing = self._adopt(result.document, self._bodies_by_feature(result))
                self._begin(executor, name, None, uuid.uuid4().hex, 0, None)
                self._aliases = {alias: feature for alias, feature, _ in listing}
                if progress is not None:
                    progress.say("saving", f"saving {target.name} and the session record next to it")
                saved = self.save_document(str(target), overwrite)
            except BaseException:
                self._close_quietly()
                self._forget_document()
                self._back(previous)
                raise
            labels = self.body_labels([body.id for _alias, _feature, body in listing if body is not None])
            report = Overflow(self.settings.workspace, "import", f"import of {source.name} as {name}")
            head, rows = foreign.report_lines(source, result, labels, report)
            lines = [f"document: {name} (part) from {source.name}", *head]
            lines.extend(self.state_lines())
            lines.append(self._adopted_line(report))
            lines.append(saved[0])
            lines.append(f"note: {foreign.NO_HISTORY}")
            lines.extend(rows)
            warning = report.write()
            if warning:
                lines.append(warning)
            return [*lines, *self._others_lines()]

    def _adopted_line(self, report: Overflow) -> str:
        """``adopted: <aliases>``; the first few when there are many, and where the rest is."""
        shown, more = report.items("adopted", list(self._aliases), MAX_ALIASES_LISTED)
        return f"adopted: {', '.join(shown)}" + (f" ... {more}" if more else "")

    @staticmethod
    def _import_failure(exc: AdapterError, source: Path) -> ToolError:
        reason = exc.info.details.get("reason")
        usage_text = "check the file opens in a CAD, or ask the supplier for a STEP file (AP214 or AP242)"
        if reason == "unreadable":
            return ToolError(
                f"{source.name} could not be read: {exc.info.message}", code="unreadable_file", usage=usage_text
            )
        if reason == "empty":
            return ToolError(exc.info.message, code="empty_file", usage=usage_text)
        return Session.adapter_failure(exc, f"import {source.name}")

    def _open_active(self, target: Path, hidden: bool = False) -> list[str]:
        """Open ``target`` as the active document (none is active when this runs). ``hidden``: without a window (a part
        an assembly links to needs to be loaded, not shown)."""
        with self.lock:
            file_sha = checkpoint.sha256_file(target)
            record, notes, stale = self._usable_record(target, file_sha)
            opened: OpenDocumentResult | None = None
            executor: Executor | None = None
            if record is not None:
                try:
                    opened = self._open(target, record.adapter_state, hidden)
                    executor = self._restore(record, opened, target)
                except (AdapterError, checkpoint.CheckpointError) as exc:
                    if isinstance(exc, AdapterError) and exc.info.details.get("reason") != "adapter_state":
                        raise self.adapter_failure(exc, f"open {target.name}") from exc
                    if opened is not None:
                        self._close_quietly()
                    opened = None
                    notes.append(
                        f"the saved session could not be restored ({_short(exc)}): the file is adopted instead"
                    )
                    record = None
            if opened is None or executor is None or record is None:
                opened = self._open_or_fail(target, None, hidden)
                self._refuse_taken_name_after_open(opened.document.name)
                executor, listing = self._adopt(opened.document)
                name = opened.document.name
                self._note_opened(name, opened)
                design_id = opened.stamp.design_id if opened.stamp is not None else uuid.uuid4().hex
                seq = opened.stamp.save_seq if opened.stamp is not None else 0
                self._begin(executor, name, target, design_id, seq, opened.sha256)
                self._aliases = {alias: feature for alias, feature, _ in listing}
                lines = self._adopted_lines(target, name, opened, listing, notes)
                record = None
            else:
                name = opened.document.name
                self._refuse_taken_name_after_open(name)
                self._note_opened(name, opened)
                self._begin(executor, name, target, record.design_id, record.save_seq, opened.sha256)
                lines = self._restored_lines(target, name, opened, record, executor)
                self._restore_attributes(record)
            self.purpose = self._saved_purpose(target)
            self._load_memory(target)
            lines.extend(self.state_lines())
            if "design_attributes" in self.capabilities.tools:
                lines.extend(self._attributes_lines(self.design_attributes()))
            if self.purpose != DEFAULT_PURPOSE:
                lines.append(f"purpose: {self.purpose}")
            lines.extend(self._drift_lines(opened, record, stale))
            lines.extend(recovery.restore_review(self))  # what the review of the drawing had that a crash took with it
            return lines

    def _load_memory(self, target: Path) -> None:
        """The memory state of the design: answers and stage from the record (the engineer's, so they survive a file
        edited since, like the purpose), roles from the native document or, if it lost them, from the record."""
        try:
            record = checkpoint.read(checkpoint.sidecar_path(target))
        except checkpoint.CheckpointError:
            record = None
        self.mem = design_memory.MemState.from_json(record.memory if record is not None else None)
        saved_roles = dict(record.attributes.get("roles") or {}) if record is not None else {}
        try:
            design_memory.load_roles(self, saved_roles)
        except AdapterError:
            self.mem.roles = saved_roles

    @staticmethod
    def _saved_purpose(target: Path) -> str:
        """The purpose in the record next to ``target``. It is the engineer's declaration, so it survives a file that
        was edited since (the rest of that record is not trusted then)."""
        try:
            record = checkpoint.read(checkpoint.sidecar_path(target))
        except checkpoint.CheckpointError:
            return DEFAULT_PURPOSE
        return record.purpose if record is not None and record.purpose in PURPOSES else DEFAULT_PURPOSE

    def _restore_attributes(self, record: checkpoint.Record) -> None:
        """The material and route come back with the native file; when it came back without them (a CAD that cannot
        keep them, or a copy that lost them) the record next to it puts them back."""
        saved = record.attributes
        if not saved or "set_design_attributes" not in self.capabilities.tools:
            return
        current = self.design_attributes()
        material = saved.get("material") if current.material is None else None
        route = tuple(saved.get("route") or ()) if not current.route else None
        if material or route:
            self.adapter.set_design_attributes(
                SetDesignAttributesRequest(operation_id=self._op_id("restore"), material=material, route=route)
            )

    def _usable_record(
        self, target: Path, file_sha: str
    ) -> tuple[checkpoint.Record | None, list[str], dict[str, Any] | None]:
        """The saved session record when it exists and describes THIS file. Otherwise why not, and (for a file that was
        edited after the server saved it) the measures taken at that save."""
        try:
            record = checkpoint.read(checkpoint.sidecar_path(target))
        except checkpoint.CheckpointError as exc:
            return None, [f"session record ignored: {exc}"], None
        if record is None:
            return None, [], None
        if record.adapter != self.adapter.name:
            return None, [f"session record ignored: it was written for the {record.adapter} adapter"], None
        if record.file_sha256 != file_sha:
            note = (
                f"{target.name} changed since the last save made by this server: the saved step ids and handles "
                "are not trusted, the file is adopted as it is"
            )
            return None, [note], record.facts
        return record, [], None

    def _refuse_taken_name_after_open(self, name: str) -> None:
        """The CAD loaded the file and it calls itself like an open document: close it again and say so."""
        if self._open_doc_named(name) is None:
            return
        self._close_quietly()
        self._refuse_taken_name(name)

    def _open(self, target: Path, state: dict[str, Any] | None, hidden: bool = False) -> OpenDocumentResult:
        """Ask the adapter to open ``target``. An assembly whose part documents are not open says which files it needs:
        they are opened first, without a window (each set aside as it is loaded), and the assembly is asked again."""
        try:
            return self._open_raw(target, state, hidden)
        except AdapterError as exc:
            if exc.info.details.get("reason") != "parts_missing":
                raise
            self._open_parts(target, str(exc.info.details.get("parts") or ""))
            return self._open_raw(target, state, hidden)

    def _open_raw(self, target: Path, state: dict[str, Any] | None, hidden: bool = False) -> OpenDocumentResult:
        return self.adapter.open_document(
            OpenDocumentRequest(operation_id="open", path=str(target), adapter_state=state, hidden=hidden)
        )

    def _note_opened(self, name: str, opened: OpenDocumentResult) -> None:
        """What the CAD said about how it opened ``name``: without a window (this session closes it when nothing uses
        it) or already open by the engineer (never closed here)."""
        self._windowless.discard(name)
        self._borrowed.discard(name)
        if opened.borrowed:
            self._borrowed.add(name)
        elif opened.windowless:
            self._windowless.add(name)

    def _open_parts(self, assembly: Path, listed: str) -> None:
        """Open, one by one, the part documents (files relative to the assembly) that it links to, without a window (the
        assembly needs them loaded, not shown); each stays open and is not the active document."""
        for relative in [r for r in listed.split("|") if r]:
            part = Path(os.path.normpath(assembly.parent / relative))
            if self._open_doc_of_file(part) is not None:
                continue
            if not part.is_file():
                raise ToolError(
                    f"{assembly.name} uses the part {relative}, which is not at {part}",
                    code="part_not_found",
                    field_name="path",
                    usage="put the part file back where the assembly expects it, or open the assembly from a copy "
                    "that keeps its parts next to it",
                )
            self._open_active(part, hidden=True)
            self._park_active()

    def _open_or_fail(self, target: Path, state: dict[str, Any] | None, hidden: bool = False) -> OpenDocumentResult:
        try:
            return self._open(target, state, hidden)
        except AdapterError as exc:
            info = exc.info
            reason = info.details.get("reason")
            if reason == "corrupt":
                raise ToolError(
                    f"{target.name} could not be opened: {info.message}",
                    code="corrupt_file",
                    field_name="path",
                    usage="the file is damaged or truncated; open an intact copy or a backup",
                ) from exc
            raise self.adapter_failure(exc, f"open {target.name}") from exc

    def _restore(self, record: checkpoint.Record, opened: OpenDocumentResult, target: Path) -> Executor:
        drifted: set[str] = set()
        context = record.executor.get("context", {})
        for op_id, body in (context.get("body_by_op") or {}).items():
            if body.get("id") in opened.rebind.bodies_drifted:
                drifted.add(op_id)
        executor = checkpoint.load_executor(record.executor, self.adapter, frozenset(drifted))
        # the handles of the document and of its parts belong to the session that saved it: the CAD gave new ones
        parts: dict[str, DocumentHandle] = {}
        for name, relative in record.parts.items():
            part = self._open_doc_of_file(Path(os.path.normpath(target.parent / relative)))
            if part is not None:
                parts[name] = part.handle
        executor.bind_documents(parts, opened.document.handle)
        return executor

    def _close_quietly(self) -> None:
        try:
            self.adapter.close_document(Empty(operation_id="close"))
        except Exception:  # noqa: BLE001 - best effort while reporting the real problem
            pass

    @staticmethod
    def _bodies_by_feature(result: ImportGeometryResult) -> dict[str, BodyHandle] | None:
        """The body of each feature, when the adapter said (``ImportedBody.feature``): then adopting the document needs
        no ``bodies_of`` query per feature."""
        known = {b.feature.id: b.body for b in result.bodies if b.feature is not None}
        return known if len(known) == len(result.bodies) else None

    def _adopt(
        self, document: DocumentInfo, bodies_by_feature: dict[str, BodyHandle] | None = None
    ) -> tuple[Executor, list[tuple[str, FeatureHandle, BodyHandle | None]]]:
        """An executor over a document it did not build: every feature record the adapter minted becomes an alias.
        ``bodies_by_feature`` spares one ``bodies_of`` query per feature when the caller already knows them."""
        nodes = [
            n for n in self.adapter.feature_tree(Empty(operation_id="adopt")).nodes if not n.parameters.get("manual")
        ]
        context = ExecutionContext(document=document.handle)
        listing: list[tuple[str, FeatureHandle, BodyHandle | None]] = []
        taken: set[str] = set()
        for node in nodes:
            base = slug(node.name or node.kind, "feature")
            alias, number = base, 1
            while alias in taken:
                number += 1
                alias = f"{base}_{number}"
            taken.add(alias)
            if bodies_by_feature is not None:
                body = bodies_by_feature.get(node.handle.id)
            else:
                bodies = self.adapter.bodies_of(FeatureRequest(operation_id="adopt", feature=node.handle)).bodies
                body = bodies[0].handle if bodies else None
            context.feature_by_op[alias] = node.handle
            if body is not None:
                context.body_by_op[alias] = body
            listing.append((alias, node.handle, body))
        external = {alias: feature for alias, feature, _ in listing}
        plan = parse_plan(
            Plan(id=slug(document.name), kind=document.kind, operations=()).model_dump(mode="json"),
            external_ids=set(external),
        )
        trace = ExecutionTrace(
            plan_id=plan.id,
            adapter=self.adapter.name,
            config=DEFAULT_CONFIG,
            steps=(),
            health=(),
            final_state=ExecutorState.completed,
            final_mass=None,
            final_bbox=None,
        )
        executor = Executor.reattach(
            plan,
            self.adapter,
            Verifier(),
            trace=trace,
            context=context,
            cursor=0,
            state=ExecutorState.completed,
            external_features=external,
        )
        return executor, listing

    def _restored_lines(
        self, target: Path, name: str, opened: OpenDocumentResult, record: checkpoint.Record, executor: Executor
    ) -> list[str]:
        steps = [s for s in executor.trace().active_steps() if s.status is StepStatus.success]
        stamp = opened.stamp
        lines = [
            f"document: {name} ({opened.document.kind.value}) from {target.name}",
            f"file: saved by this server (save {stamp.save_seq if stamp else record.save_seq}); "
            "session restored: step ids and handles kept",
            f"steps: {len(steps)}"
            + (f", last {steps[-1].op_id} {plain_op_name(steps[-1].op)}" if steps else "")
            + " (steps from before the reopen cannot be undone)",
        ]
        for step in steps[:MAX_LISTED]:
            lines.append(f"  {step.op_id}: {plain_op_name(step.op)}")
        if len(steps) > MAX_LISTED:
            lines.append(f"  ... {len(steps) - MAX_LISTED} more (inspect tree)")
        return lines

    def _adopted_lines(
        self,
        target: Path,
        name: str,
        opened: OpenDocumentResult,
        listing: list[tuple[str, FeatureHandle, BodyHandle | None]],
        notes: list[str],
    ) -> list[str]:
        stamp = "stamped by the harness" if opened.stamp is not None else "no harness stamp (foreign file)"
        lines = [
            f"document: {name} ({opened.document.kind.value}) from {target.name}",
            f"file: {stamp}; ADOPTED: handles minted fresh, no step history; bodies: {len(listing)}",
        ]
        lines.extend(f"note: {n}" for n in notes)
        for alias, feature, _body in listing[:MAX_LISTED]:
            lines.append(f"  {alias}: {feature.id} (build on it with from_op={alias!r}; not undoable)")
        if len(listing) > MAX_LISTED:
            lines.append(f"  ... {len(listing) - MAX_LISTED} more (inspect tree)")
        return lines

    def _drift_lines(
        self, opened: OpenDocumentResult, record: checkpoint.Record | None, stale: dict[str, Any] | None
    ) -> list[str]:
        """What no longer matches: the adapter's re-bind report (a restored session) and, for a file this server saved
        and somebody edited since, the measures at that save against the measures now."""
        report = opened.rebind
        lines: list[str] = []
        if record is not None and not report.clean:
            lines.append("drift: the file is not what was saved:")
            if report.bodies_drifted:
                lines.append(f"  - geometry changed in {', '.join(report.bodies_drifted)}")
            if report.records_unmatched:
                lines.append(f"  - recorded entities no longer found: {', '.join(report.records_unmatched[:6])}")
            if report.objects_invalid:
                lines.append(f"  - objects in error: {', '.join(report.objects_invalid[:6])}")
            if report.foreign_objects:
                lines.append(f"  - made outside the steps: {', '.join(report.foreign_objects[:6])}")
        elif record is None and report.objects_invalid:
            lines.append(f"warning: objects in error after loading: {', '.join(report.objects_invalid[:6])}")
        lines.extend(f"warning: {w}" for w in opened.warnings)
        if stale:
            lines.extend(self._edited_since_save(stale))
        return lines

    def _edited_since_save(self, before: dict[str, Any]) -> list[str]:
        mass, bbox = self.measure(quick=True)
        lines = ["drift: the file was edited after this server saved it:"]
        was = before.get("volume_mm3")
        if isinstance(was, int | float):
            lines.append(f"  - volume was {num(was)} mm3, now {num(mass.volume_mm3)} mm3")
        if before.get("bbox_max") and before.get("bbox_min"):
            was_size = tuple(b - a for a, b in zip(before["bbox_min"], before["bbox_max"], strict=True))
            lines.append(f"  - size was {vec(was_size)} mm, now {vec(bbox.size())} mm")
        return lines

    # --- save / close ----------------------------------------------------------------------------------------------

    def save_document(self, path: str | None, overwrite: bool = False, force: bool = False) -> list[str]:
        self.need("save_document", "doc(action=save)")
        with self.lock:
            executor = self.require_executor()
            suffix = self.adapter.suffix_for(executor.plan.kind)
            if path:
                target = self.settings.resolve(path)
            elif self.doc_path is not None:
                target = self.doc_path
            else:
                target = self.settings.workspace / f"{self.doc_name}{suffix}"
            if target.suffix == "":
                target = target.with_suffix(suffix)
            same_file = self.doc_path is not None and _same_path(target, self.doc_path)
            if target.exists() and not same_file and not overwrite:
                raise ToolError(
                    f"{target.name} already exists and is not this document's file; saving would overwrite it",
                    code="file_exists",
                    field_name="path",
                    usage='doc(action="save", path="<another name>"), doc(action="save", path=..., overwrite=true) '
                    'to replace that file, or doc(action="open", path=...) to work on it',
                )
            seq = self.save_seq + 1
            plan = executor.plan
            previous = checkpoint.sidecar_path(target)
            stamp = DocumentStamp(
                design_id=self.design_id,
                plan_id=plan.id,
                plan_revision=seq,
                save_seq=seq,
                last_step_index=len(self.steps()),
                harness_version=__version__,
                plan_sha256=checkpoint.plan_sha256(plan),
                sidecar_sha256=checkpoint.sha256_file(previous) if previous.is_file() else None,
            )
            try:
                saved = self.adapter.save_document(
                    SaveDocumentRequest(
                        operation_id="save",
                        path=str(target),
                        stamp=stamp,
                        overwrite=True,
                        expected_sha256=self.file_sha if same_file and not force else None,
                    )
                )
            except AdapterError as exc:
                if exc.info.details.get("reason") == "conflict":
                    raise ToolError(
                        f"{target.name} was changed outside this server since it was last saved or opened; "
                        "saving would overwrite that work",
                        code="file_conflict",
                        usage=f'save under another name: doc(action="save", path="<new name>'
                        f'{suffix}"); to replace that '
                        'file with this document: doc(action="save", force=true); to take the file as it is: '
                        'doc(action="reload")',
                    ) from exc
                if exc.info.details.get("reason") == "part_unsaved":
                    raise ToolError(
                        exc.info.message,
                        code="part_unsaved",
                        usage='save each part first: doc(action="switch", name="<part>"), doc(action="save"), then '
                        "switch back to the assembly and save it",
                    ) from exc
                raise self.adapter_failure(exc, "save") from exc
            lines = [f"saved: {saved.path} ({saved.size_bytes} bytes)", f"steps: {len(self.steps())}"]
            if self.doc_path is not None and not same_file:
                lines.append(
                    f"saved as a new file: the current file is now {saved.path}; {self.doc_path} stays as it was "
                    "last saved"
                )
            lines.extend(f"warning: {w}" for w in saved.warnings)
            lines.extend(self._part_lines(executor, Path(saved.path)))
            try:
                mass, bbox = self.measure(quick=True)
                record = checkpoint.Record(
                    adapter=self.adapter.name,
                    file_name=Path(saved.path).name,
                    file_sha256=saved.sha256,
                    design_id=self.design_id,
                    save_seq=seq,
                    adapter_state=saved.adapter_state,
                    executor=checkpoint.dump_executor(executor),
                    facts={
                        "volume_mm3": mass.volume_mm3,
                        "bbox_min": list(bbox.min),
                        "bbox_max": list(bbox.max),
                    },
                    attributes=self.design_attributes().model_dump(mode="json"),
                    parts=self._part_files(executor, Path(saved.path)),
                    purpose=self.purpose,
                    memory=self.mem.to_json(),
                )
                checkpoint.write(checkpoint.sidecar_path(Path(saved.path)), record)
            except (OSError, checkpoint.CheckpointError, ToolError) as exc:
                lines.append(
                    f"warning: the session record next to the file was not written ({exc}); reopening this file "
                    "will adopt it instead of restoring the step ids"
                )
            old_key = recovery.key_of(self)
            recovery.clear(self)  # a clean save makes the recovery copy unnecessary
            self.doc_path = Path(saved.path)
            recovery.rekey(self, old_key, recovery.key_of(self))  # the review journal follows the file's name
            self.file_sha = saved.sha256
            self.file_stat = self._stat_of_file()
            self.save_seq = seq
            self.dirty = False
            return lines

    def reload_document(self, force: bool = False) -> list[str]:
        """Read the active document's file again from disk, dropping the session's copy (its steps, handles and anything
        unsaved): what a file changed outside this server, or a fragment that saved over it, needs. Unsaved changes
        are only dropped with ``force``."""
        self.need("open_document", "doc(action=reload)")
        with self.lock:
            suffix = self.adapter.suffix_for(self.require_executor().plan.kind)
            if self.doc_path is None or not self.doc_path.is_file():
                raise ToolError(
                    f"{self.doc_name} has no file on disk to read again: doc(action=save) first",
                    code="not_found",
                    usage=f'doc(action="save", path="<name>{suffix}")',
                )
            if self.dirty and not force:
                raise ToolError(
                    f"{self.doc_name} has unsaved changes; reloading drops them (and the step history)",
                    code="unsaved_changes",
                    usage='doc(action="save") first, or doc(action="reload", force=true)',
                )
            target = self.doc_path
            steps = len(self.steps())
            lines = self.close_document(discard=True)
            try:
                opened = self._open_active(target)
            except (ToolError, AdapterError) as exc:
                failure = exc if isinstance(exc, ToolError) else self.adapter_failure(exc, f"reload {target.name}")
                raise ToolError(
                    f"{lines[0]}, but {target.name} could not be opened again: {failure.message}",
                    code=failure.code,
                    usage=f'doc(action="open", path="{target.name}") once the file is readable',
                ) from exc
            return [f"reloaded: {target.name} from disk ({steps} steps of the session dropped)", *opened]

    def _part_files(self, executor: Executor, assembly: Path) -> dict[str, str]:
        """Part name -> its file relative to the assembly's, for the part documents the steps of ``executor`` use."""
        files: dict[str, str] = {}
        for name, handle in executor.documents.items():
            part = next((d for d in self.open_docs() if d.handle.id == handle.id), None)
            if part is not None and part.path is not None:
                try:
                    files[name] = Path(os.path.relpath(part.path, assembly.parent)).as_posix()
                except ValueError:  # another drive on Windows: there is no relative form
                    files[name] = str(part.path)
        return files

    def _part_lines(self, executor: Executor, saved: Path) -> list[str]:
        """After a save: for an assembly, parts with changes that are not on disk (the assembly links to their files);
        for a part, the open assemblies that use it (they pick the change up when they are switched to)."""
        lines: list[str] = []
        if executor.plan.kind is DocumentKind.assembly:
            for name, handle in executor.documents.items():
                part = next((d for d in self.open_docs() if d.handle.id == handle.id), None)
                if part is not None and part.dirty:
                    lines.append(
                        f"warning: part {name!r} has unsaved changes: {saved.name} links to its saved file, "
                        f'not to what is open (doc(action="switch", name="{name}"), doc(action="save"))'
                    )
            return lines
        users = self._users_of(executor.context.document) if executor.context is not None else []
        if users:
            lines.append(
                f'used by the open assembly {", ".join(users)}: doc(action="switch", name=...) brings it up to date'
            )
        return lines

    def close_document(self, discard: bool) -> list[str]:
        self.need("close_document", "doc(action=close)")
        with self.lock:
            if self.executor is None:
                raise ToolError("no document is open", code="no_document")
            if self.dirty and not discard:
                raise ToolError(
                    f"{self.doc_name} has unsaved changes; closing discards them",
                    code="unsaved_changes",
                    usage='doc(action="save") first, or doc(action="close", discard=true)',
                )
            name = self.doc_name
            users = self._users_of(self.executor.context.document) if self.executor.context is not None else []
            if users:
                raise ToolError(
                    f"{name} is a part of the open assembly {', '.join(users)}: closing it would break the links",
                    code="document_in_use",
                    usage=f'doc(action="switch", name="{users[0]}"), doc(action="close") first',
                )
            try:
                self.adapter.close_document(Empty(operation_id="close"))
            except AdapterError as exc:
                raise self.adapter_failure(exc, "close") from exc
            self._forget_document()
            lines = [f"closed: {name}"]
            if name in self._borrowed:
                self._borrowed.discard(name)
                lines = [f"closed: {name} here; it was open already (you opened it) and stays open in the CAD"]
            self._windowless.discard(name)
            idle = self._close_idle_parts()
            if idle:
                lines.append(f"also closed, loaded only for it and used by nothing now: {', '.join(idle)}")
            return [*lines, "document: none", *self._others_lines()]

    def _close_idle_parts(self) -> list[str]:
        """Close the parts this session loaded without a window that no open assembly uses any more and that have no
        unsaved changes. A part the engineer opened himself, one he changed, or one another assembly still links to
        stays. Returns their names. None is the active document when this runs (the caller just closed one)."""
        closed: list[str] = []
        for name in sorted(self._windowless):
            parked = self._parked.get(name)
            if parked is None:
                self._windowless.discard(name)
                continue
            if parked.dirty or name in self._borrowed or self._users_of(parked.handle):
                continue
            try:
                self.adapter.set_active_document(
                    SetActiveDocumentRequest(operation_id=self._op_id("idle"), document=parked.handle, window=False)
                )
                self.adapter.close_document(Empty(operation_id="close"))
            except AdapterError:
                continue  # it stays open: the next call says what the CAD thinks of it
            self._parked.pop(name, None)
            self._windowless.discard(name)
            closed.append(name)
        return closed

    def _forget_document(self) -> None:
        self.executor = None
        self.doc_name = ""
        self.doc_path = None
        self.dirty = False
        self.file_sha = None
        self.file_stat = None
        self._aliases = {}
        self.purpose = DEFAULT_PURPOSE
        self.hand_edits.forget()

    def status(self) -> list[str]:
        with self.lock:
            version = (
                f"{self.adapter.name} {self.info.version}" if self.info else f"{self.adapter.name} (not connected)"
            )
            if self.executor is None:
                return [f"cad: {version}", "document: none", *self._others_lines()]
            steps = self.steps()
            lines = [
                f"cad: {version}",
                f"document: {self.doc_name} ({self.executor.plan.kind.value})",
                f"file: {self.doc_path or 'not saved yet'}{' (UNSAVED changes)' if self.dirty else ''}",
                f"steps: {len(steps)}" + (f", last {steps[-1].id} {steps[-1].op}" if steps else ""),
            ]
            report = Overflow(self.settings.workspace, "status", f"status of {self.doc_name}")
            if self._aliases:
                lines.append(self._adopted_line(report))
            lines.append(f"purpose: {self.purpose}")
            if "design_attributes" in self.capabilities.tools:
                lines.extend(self._attributes_lines(self.design_attributes()))
            lines.extend(design_memory.status_lines(self))
            if self.executor.state is ExecutorState.degraded:
                lines.append("state: DEGRADED")
            lines.extend(self.state_lines())
            if self._parked:
                lines.extend(self.documents_lines())
            warning = report.write()
            if warning:
                lines.append(warning)
            return lines

    # --- operations ----------------------------------------------------------------------------------------------

    def run_op(self, name: str, params: dict[str, Any], intent: str | None, op_id: str | None) -> list[str]:
        with self.lock:
            executor = self.require_executor()
            read = self.extensions().get(name)
            if read is not None and read.read_only:
                return self._run_read(executor, read, params, op_id)
            role = ""
            if "role" in params:  # not a parameter of the op: the role of the body or component the step makes
                params = dict(params)
                role = design_memory.checked_role(self.settings, str(params.pop("role")))
            extensions = self.extensions()
            if name not in catalog.op_models(extensions):
                raise self._unknown_op(name)
            if name not in self.available_ops():
                reason = catalog.unavailable_ops(self.adapter.unavailable_tools()).get(name)
                raise ToolError(
                    f"op {name!r} is not available: {reason}"
                    if reason
                    else f"op {name!r} is not supported by {self.adapter.name}",
                    code="not_supported",
                    usage='cad_guide(topic="ops") lists what this CAD can do',
                )
            step_id = op_id or params.get("id") or self.next_id()
            if not isinstance(step_id, str) or not STEP_ID.match(step_id):
                raise ToolError(
                    f"id {step_id!r} is not a valid step id (lowercase letters, digits, _ ; start with a letter)",
                    code="invalid_input",
                    field_name="id",
                    example='op(name="extrude", id="base", params={...})',
                )
            if step_id in self.known_ids():
                raise ToolError(
                    f"id {step_id!r} is already used by a step in effect; pick another or omit id",
                    code="duplicate_id",
                    field_name="id",
                )
            if "op" in params and params["op"] != name:
                raise ToolError(
                    "params must not carry 'op'; the name argument selects the operation",
                    code="invalid_input",
                    field_name="params.op",
                )
            if "links" in params:
                raise ToolError(
                    "'links' is not a parameter of any op; say why in intent",
                    code="invalid_input",
                    field_name="params.links",
                    usage=catalog.op_usage(name, extensions),
                )
            data = {k: v for k, v in params.items() if k not in ("op", "id", "intent")}
            spec = extensions.get(name)
            world_frame = None if spec is not None else self._to_face_frame(name, data)
            wording = intent or params.get("intent") or f"{name} {step_id}"
            data = self._bodies_by_step(name, data)
            if spec is not None:
                data = self._components_by_step(name, data)
                operation = self._validate_extension(spec, data, step_id, wording, executor.plan.kind)
            else:
                data.update(op=name, id=step_id, intent=wording)
                operation = self._validate_operation(name, data)
            before_lines: list[str] = []
            after_lines: list[str] = []
            if name == "insert_component" and spec is None:
                asked = str(getattr(operation, "part", ""))
                before_lines, after_lines, part_name = self._prepare_insert(executor, asked)
                if part_name != asked:  # named by its file: from here on, by the name of the document
                    operation = operation.model_copy(update={"part": part_name})

            if executor.state is ExecutorState.failed:
                executor.abandon_failed()
            hole_hint = hints.drilled_holes(self, operation)  # read from the state BEFORE the tools are consumed
            try:
                executor.extend([operation])
            except PlanError as exc:
                raise ToolError(exc.message, code=f"plan_{exc.rule}", usage=catalog.op_usage(name, extensions)) from exc
            except ExecutorStateError as exc:
                raise ToolError(str(exc), code="session_state") from exc
            try:
                record = executor.next_operation()
            except AdapterError as exc:
                self._recover(executor)
                raise self.adapter_failure(exc, f"{name} {step_id}") from exc
            if record.status is not StepStatus.success:
                reason = describe_failure(record)
                self._recover(executor)
                lines = [f"{step_id} {name} was not applied", reason, "state: unchanged", *self.state_lines()]
                raise ToolError("\n".join(lines), code="step_failed")
            self.dirty = True
            # the part before the step is what the step itself measured to compare its volume with (not asked twice)
            lines = self._success_lines(record, operation, record.mass_before or self.measure()[0])
            if world_frame is not None:
                axes = f"u={vec(world_frame.u)} v={vec(world_frame.v)}"
                lines.insert(1, f"world frame: {axes} (rect width runs along u, height along v)")
            try:
                remembered = design_memory.after_op(self, step_id, name, params, role)
            except AdapterError as exc:
                remembered = [f"note: the role could not be stored in the document ({exc.info.message})"]
            remembered = [
                *remembered,
                *bearing_seats.after_op(self, step_id, name, role),
                *pocket_corners.after_op(self, step_id, name),
                *turned_ends.after_op(self, step_id, name, role),
            ]
            return [
                *before_lines,
                *lines,
                *after_lines,
                *hole_hint,
                *hints.widened_holes(self, step_id, name),
                *remembered,
                *self._convention_lines(name, operation),
            ]

    def _run_read(
        self, executor: Executor, spec: OperationSpec, params: dict[str, Any], op_id: str | None
    ) -> list[str]:
        """An adapter operation that only reads (``OperationSpec.read_only``): validated and resolved like a step, run
        now, and printed. It is not a step: no id, not in the plan, nothing to undo, the document is not marked changed
        and the design memory hears nothing of it."""
        refused = ["id"] if op_id else [k for k in ("id", "role") if k in params]
        if refused:
            raise ToolError(
                f"{spec.name} only reads: it is not a step, so it takes no {refused[0]}",
                code="invalid_input",
                field_name="id" if op_id else f"params.{refused[0]}",
            )
        data = {k: v for k, v in params.items() if k not in ("op", "intent")}
        data = self._components_by_step(spec.name, self._bodies_by_step(spec.name, data))
        self._calls += 1
        operation = self._validate_extension(spec, data, f"read_{self._calls}", f"read {spec.name}", executor.plan.kind)
        try:
            result = executor.read_extension(operation)
        except ResolutionError as exc:
            raise ToolError(
                exc.message,
                code="unresolved_reference",
                field_name=f"params.{exc.path.split('.')[0].split('[')[0]}",
                usage=catalog.op_usage(spec.name, {spec.name: spec}),
            ) from exc
        except ExecutorStateError as exc:
            raise ToolError(str(exc), code="session_state") from exc
        except AdapterError as exc:
            raise self.adapter_failure(exc, spec.name) from exc
        return [
            f"ok: {spec.name} (read: not a step, the document is unchanged)",
            *_text_lines(result.text),
            *(f"warning: {w}" for w in result.warnings),
        ]

    def extensions(self) -> dict[str, OperationSpec]:
        """The operations this CAD's adapter declared, by the name the agent writes; empty for an adapter that declares
        none (then everything the agent sees is the common vocabulary, exactly as before)."""
        specs: dict[str, OperationSpec] = {}
        for spec in self.adapter.extra_operations():
            if spec.name in specs:
                msg = f"the adapter {self.adapter.name!r} declares the operation {spec.name!r} twice"
                raise ValueError(msg)
            specs[spec.name] = spec
        return specs

    def available_ops(self) -> list[str]:
        """The operations the agent can call here: the common ones this CAD supports and the ones it declared."""
        return catalog.available_ops(self.capabilities, self.extensions())

    def _validate_extension(
        self, spec: OperationSpec, data: dict[str, Any], step_id: str, intent: str, kind: DocumentKind
    ) -> ExtensionOperation:
        """The arguments of an operation the adapter declared, checked against its own model with the errors of a common
        operation (field, what is wrong, the right shape and an example); the step the plan keeps."""
        extensions = {spec.name: spec}
        if kind not in spec.kinds:
            allowed = " or ".join(k.value for k in spec.kinds)
            raise ToolError(
                f"{spec.name} works in documents of kind {allowed}; {self.doc_name} is of kind {kind.value}",
                code="invalid_input",
                field_name="name",
            )
        try:
            model = spec.params.model_validate(data)
        except ValidationError as exc:
            refusal = self._live_id_refusal(spec.name, data)
            if refusal is not None:
                raise refusal from exc
            detail = "\n".join(f"field: params.{path} -> {msg}" for path, msg in first_validation_issues(exc))
            raise ToolError(
                detail,
                code="invalid_params",
                usage=catalog.op_usage(spec.name, extensions),
                example=catalog.example_call(spec.name, extensions),
            ) from exc
        return ExtensionOperation(
            id=step_id,
            op=extension_op_name(spec.name),
            intent=intent,
            params=model.model_dump(mode="json", by_alias=True),
        )

    def _to_face_frame(self, name: str, data: dict[str, Any]) -> SketchFrame | None:
        """``frame="world"`` on a sketch: the entity points are world [x, y, z] mm; map them into the (u, v) frame of
        ``on`` before the step is validated, so the stored step stays in the plane's own frame. Returns that frame
        (``None`` when nothing was mapped)."""
        mode = data.pop("frame", "face")
        if mode not in ("face", "world") or (mode == "world" and name != "sketch"):
            raise ToolError(
                'frame is a parameter of sketch only: "face" (default, points are [u, v] from the face centre) or '
                '"world" (points are [x, y, z] mm)',
                code="invalid_params",
                field_name="params.frame",
            )
        if mode == "face":
            return None
        executor = self.require_executor()
        try:
            on = _ON.validate_python(data.get("on"))
        except ValidationError as exc:
            raise ToolError(
                "frame=world needs a valid on (plane, datum plane or face) to map the points into",
                code="invalid_params",
                field_name="params.on",
                usage=catalog.op_usage(name),
            ) from exc
        try:
            frame = executor.sketch_frame_for(on)
        except ResolutionError as exc:
            raise ToolError(exc.message, code="unresolved_reference", field_name="params.on") from exc
        tolerance = executor.config.thresholds.position_mm
        entities = []
        for entity in data.get("entities") or ():
            mapped = dict(entity) if isinstance(entity, dict) else entity
            for key in _POINT_FIELDS if isinstance(mapped, dict) else ():
                if key in mapped:
                    mapped[key] = _world_point(frame, mapped[key], f"{mapped.get('name', '?')}.{key}", tolerance)
            entities.append(mapped)
        data["entities"] = entities
        return frame

    def _load_part_file(self, part_name: str) -> tuple[OpenDoc | None, bool]:
        """``part_name`` as the file of a saved part that is not open: it is loaded WITHOUT a window (the assembly links
        to the document, which needs to be loaded, not shown) and set aside like any other open document. ``None`` when
        it does not name a part file that exists. A file that is open already is that document. Also says whether the
        file was loaded now."""
        if not part_name.strip():
            return None, False
        target = self.settings.resolve(part_name)
        if not target.is_file():
            return None, False
        found = self._open_doc_of_file(target)
        if found is not None:
            return (None if found.active else found), False
        previous = self._park_active(measure=False)  # the assembly goes aside and comes back as it was
        try:
            self._open_active(target, hidden=True)
            self._park_active(measure=False)
        except BaseException:
            self._back(previous)
            raise
        if previous is not None:
            self._resume(previous)
        return self._open_doc_of_file(target), True

    def _prepare_insert(self, executor: Executor, part_name: str) -> tuple[list[str], list[str], str]:
        """``insert_component`` takes a part from ANOTHER open document, saved in a file the assembly can link to, or
        the file of a saved part that is not open (loaded for the insert, without a window). Check that, save the
        assembly itself when it has no file yet (the links name files relative to its own), and let the executor know
        the part by its name. Returns the lines to show before and after the step's result, and the part's name."""
        if executor.plan.kind is not DocumentKind.assembly:
            raise ToolError(
                f"insert_component works in an assembly; {self.doc_name} is a part",
                code="not_assembly",
                usage='doc(action="new", name="<assembly name>", kind="assembly"), then insert_component there',
            )
        parts = [d for d in self.open_docs() if not d.active and d.kind is DocumentKind.part]
        part = next((d for d in parts if d.name == part_name), None)
        loaded: list[str] = []
        if part is None:
            part, now = self._load_part_file(part_name)
            if part is not None and part.kind is not DocumentKind.part:
                part = None
            elif part is not None and now:
                loaded = [f"loaded {part.name} from its file without a window: the assembly needs it loaded, not shown"]
        if part is None:
            names = ", ".join(d.name for d in self.open_docs() if not d.active and d.kind is DocumentKind.part)
            raise ToolError(
                f"insert_component: {part_name!r} is not an open part document"
                + (f"; open parts: {names}" if names else "; no part document is open"),
                code="unknown_part",
                field_name="params.part",
                usage='model the part in its own document (doc(action="new", name=...)), save it, then open or create '
                "the assembly; part = that document's name (or the file of a saved part, loaded for the insert with "
                "no window of its own)",
                example=catalog.example_call("insert_component"),
            )
        if part.path is None:
            raise ToolError(
                f"the part {part_name!r} has never been saved: the assembly links to its file",
                code="part_unsaved",
                field_name="params.part",
                usage=f'doc(action="switch", name="{part_name}"), doc(action="save"), doc(action="switch", '
                f'name="{self.doc_name}"), then insert it',
            )
        part_name = part.name
        before_lines: list[str] = list(loaded)
        if self.doc_path is None:
            self.save_document(None)
            before_lines.append(
                f"saved the assembly first ({self.doc_path}): its components link to the part files, which needs "
                "the assembly's own file"
            )
        after_lines: list[str] = []
        if part.dirty:
            after_lines.append(
                f"warning: part {part_name!r} has unsaved changes: the assembly links to its saved file "
                f'(doc(action="switch", name="{part_name}"), doc(action="save"))'
            )
        executor.bind_documents({**executor.documents, part_name: part.handle})
        return before_lines, after_lines, part_name

    def _recover(self, executor: Executor) -> None:
        if executor.state is ExecutorState.failed:
            executor.abandon_failed()

    def _unknown_op(self, name: str) -> ToolError:
        from cotaforge_mcp.terms import term_hits  # noqa: PLC0415 - terms import the catalog

        available = self.available_ops()
        close = catalog.close_names(name, available, term_hits(name))
        hint = f"close names: {', '.join(close)}" if close else f"ops: {', '.join(available)}"
        return ToolError(
            f"unknown op {name!r}; {hint}",
            code="unknown_op",
            field_name="name",
            usage='cad_guide(topic="ops") lists them; cad_guide(topic="op:<name>") gives the parameters',
        )

    def _ref_fields(self, name: str, ref_type: str) -> list[str]:
        """The fields of op ``name`` that take a ``ref_type`` (``BodyRef``, ``FaceRef``...), alone or in a list."""
        fields = catalog.op_models(self.extensions())[name].model_fields.items()
        return [n for n, f in fields if ref_type in catalog.describe_type(f.rebuild_annotation(), {})]

    def _bodies_by_step(self, name: str, data: dict[str, Any]) -> dict[str, Any]:
        """Where an op takes a body reference, a body named by its id (``"body:2"``, or ``from_op: "body:2"``) or a bare
        step id means the same body as the reference by step: it becomes that reference. Anything else is left to the
        validation."""
        out = dict(data)
        for field_name in self._ref_fields(name, "BodyRef"):
            if field_name in out:
                out[field_name] = self._body_param(name, field_name, out[field_name])
        return out

    def _body_param(self, name: str, field_name: str, value: Any) -> Any:
        if isinstance(value, list):
            return [self._body_param(name, field_name, item) for item in value]
        if isinstance(value, str):
            text = value
        elif (
            isinstance(value, dict)
            and value.get("kind", "body") == "body"
            and isinstance(value.get("from_op"), str)
            and not STEP_ID.match(value["from_op"])
        ):
            text = value["from_op"]
        else:
            return value
        if text in self.known_ids():
            return {"kind": "body", "from_op": text}
        named = self.named_bodies()
        if text in named:
            return dict(named[text])
        first = next(iter(named.values()), {"kind": "body", "from_op": "s1"})
        listing = self.bodies_listing()
        form = json.dumps(first, separators=(",", ":"))
        raise ToolError(
            f"{text!r} is not a body made by a step in effect; an op names a body by the step that made it: {form} "
            "(a body id works too)" + (f"\nbodies: {listing}" if listing else ""),
            code="invalid_params",
            field_name=f"params.{field_name}",
            usage=catalog.op_usage(name, self.extensions()),
            example=catalog.example_call(name, self.extensions()),
        )

    def _components_by_step(self, name: str, data: dict[str, Any]) -> dict[str, Any]:
        """Where an adapter operation takes a component reference, a component named by the step that inserted it (or by
        its id, ``"comp:1"``) means the same as the reference by step: it becomes that reference."""
        out = dict(data)
        for field_name in self._ref_fields(name, "ComponentRef"):
            if field_name in out:
                out[field_name] = self._component_param(name, field_name, out[field_name])
        return out

    def _component_param(self, name: str, field_name: str, value: Any) -> Any:
        if isinstance(value, list):
            return [self._component_param(name, field_name, item) for item in value]
        if not isinstance(value, str):
            return value
        if value in self.known_ids():
            return {"kind": "component", "from_op": value}
        context = self.executor.context if self.executor is not None else None
        by_id = {handle.id: step for step, handle in context.component_by_op.items()} if context is not None else {}
        if value in by_id:
            return {"kind": "component", "from_op": by_id[value]}
        raise ToolError(
            f'{value!r} is not a component made by a step in effect; name it by the step that inserted it: {{"kind":'
            '"component","from_op":"<step id>"} (a component id works too)',
            code="invalid_params",
            field_name=f"params.{field_name}",
            usage=catalog.op_usage(name, self.extensions()),
        )

    def _live_id_refusal(self, name: str, data: dict[str, Any]) -> ToolError | None:
        """A face or an edge named by its id where an op takes a reference: refused with the forms an op takes (an id
        changes as the part changes; a reference by step is re-resolved every time)."""
        for ref_type in ("FaceRef", "EdgeRef"):
            for field_name in self._ref_fields(name, ref_type):
                value = data.get(field_name)
                texts = [v for v in (value if isinstance(value, list) else [value]) if isinstance(v, str)]
                if not texts:
                    continue
                what = ref_type[:-3].lower()
                if what == "face":
                    reference: dict[str, Any] = self._face_ref_example(texts[0])
                else:
                    reference = {"kind": "edge", "between": self._edge_faces_example(texts[0])}
                form = json.dumps(reference, separators=(",", ":"))
                picked = f'{{"kind":"{what}","from_selection":"<id from view(action=selection)>"}}'
                return ToolError(
                    f"{texts[0]!r} is an id; an op names a{'n' if what == 'edge' else ''} {what} by the step that made "
                    f"it: {form}, or one the engineer picked: {picked}",
                    code="invalid_params",
                    field_name=f"params.{field_name}",
                    usage=catalog.op_usage(name, self.extensions()),
                    example=catalog.example_call(name, self.extensions()),
                )
        return None

    def _edge_faces_example(self, edge_id: str) -> list[dict[str, Any]]:
        """The references by step of the two faces a live edge lies between, or two generic ones."""
        try:
            edge = self.adapter.edge_info(EdgeRequest(operation_id=self._op_id("hint"), edge=EdgeHandle(id=edge_id)))
        except AdapterError:
            return [self._face_ref_example(None), self._face_ref_example(None, (1, 0, 0))]
        return [self._face_ref_example(face.id) for face in edge.faces]

    def _face_ref_example(self, face_id: str | None, normal: tuple[int, int, int] = (0, 0, 1)) -> dict[str, Any]:
        """The reference by step of a live face (its creating step and normal), or a generic one."""
        steps = self.steps()
        generic = {"kind": "face", "from_op": steps[-1].id if steps else "s2", "select": {"normal": list(normal)}}
        if face_id is None:
            return generic
        try:
            face = self.adapter.face_info(FaceRequest(operation_id=self._op_id("hint"), face=FaceHandle(id=face_id)))
        except AdapterError:
            return generic
        step = self.step_of_feature(face.provenance.created_by.id)
        if step is None:
            return generic
        if face.normal is not None:
            select: dict[str, Any] = {"normal": [_plain(x) for x in face.normal]}
        else:
            select = {"center": [_plain(x) for x in face.center]}
        return {"kind": "face", "from_op": step, "select": select}

    def _validate_operation(self, name: str, data: dict[str, Any]) -> BaseModel:
        try:
            return catalog.OPERATION_ADAPTER.validate_python(data)
        except ValidationError as exc:
            refusal = self._live_id_refusal(name, data)
            if refusal is not None:
                raise refusal from exc
            issues = first_validation_issues(exc, skip=1)
            detail = "\n".join(
                f"field: {path if path in ('id', 'intent') else 'params.' + path} -> {msg}" for path, msg in issues
            )
            raise ToolError(
                detail,
                code="invalid_params",
                usage=catalog.op_usage(name),
                example=catalog.example_call(name),
            ) from exc

    def _success_lines(self, record: StepRecord, operation: BaseModel, before: MassProperties) -> list[str]:
        result = record.attempts[-1].result
        lines = [f"ok: {record.op_id} {plain_op_name(record.op)}"]
        lines.extend(record.notes)
        if record.op == "sketch":
            lines.append(_sketch_line(operation))
        if result is not None:
            created = []
            for label, count in (
                ("faces", len(result.created_faces) or result.face_count or 0),
                ("edges", len(result.created_edges) or result.edge_count or 0),
                ("bodies", len(result.created_bodies)),
            ):
                if count:
                    created.append(f"{count} {label}")
            if created:
                lines.append("created: " + ", ".join(created))
            if result.face_count is not None and result.component is not None:
                lines.append(
                    f"component {self.component_label(result.component.id)}: the faces and edges are the part's own, "
                    f"counted here and not listed; their ids are the part's with {result.component.id}/ in front "
                    f"({result.component.id}/face:12)"
                )
            if result.modified_faces:
                lines.append(f"modified: {len(result.modified_faces)} faces")
            if result.instance_count:
                lines.append(f"instances: {result.instance_count}")
            if result.joint is not None:
                lines.append(self._joint_line(record.op_id, result.joint))
            lines.extend(_text_lines(result.text))
            if result.warnings:
                lines.append("warnings: " + "; ".join(result.warnings[:3]))
            flat = result.flat_pattern
            if flat is not None:
                lines.append(
                    f"flat pattern: {num(flat.size_mm[0])} x {num(flat.size_mm[1])} mm, thickness "
                    f"{num(flat.thickness_mm)}, blank area {num(flat.area_mm2)} mm2, "
                    f"volume {num(flat.volume_mm3)} mm3, valid: {'yes' if flat.valid else 'NO'}"
                )
                lines.append("(volume and bbox below are the bent part; the flat pattern is a separate solid)")
        for check in record.last_level2():
            if check.ok is False and check.kind.value == "asserted":
                lines.append(f"warning: check {check.name} expected {check.expected} observed {check.observed}")
            elif check.ok and check.basis and isinstance(check.observed, int | float):
                lines.append(f"verified: {check.name} {num(check.observed)} mm3 {check.basis}")
        lines.extend(self.state_lines(before, just_measured=True))
        return lines

    def _joint_line(self, step_id: str, joint: JointInfo) -> str:
        """``joint hinge (joint:1): revolute lid to plate, value 30 deg, limits [0, 120], axis ...``: where the joint
        stands, under the id of the ``add_joint`` step that made it (what ``set_joint`` and the sweep take)."""
        context = self.executor.context if self.executor is not None else None
        if context is not None:
            step_id = next((op for op, handle in context.joint_by_op.items() if handle == joint.handle), step_id)
        unit = f" {joint.unit}" if joint.unit else ""
        parts = [
            f"{joint.joint_type.value} {self.component_label(joint.b.id)} to "
            + (self.component_label(joint.a.id) if joint.a is not None else "the ground")
        ]
        if joint.unit:
            low = "-inf" if joint.min_value is None else num(joint.min_value)
            high = "inf" if joint.max_value is None else num(joint.max_value)
            parts.append(f"value {num(joint.value)}{unit}, limits [{low}, {high}]")
        if joint.axis is not None:
            parts.append(f"axis origin {vec(joint.axis.origin)} direction {vec(joint.axis.direction)}")
        return f"joint {step_id} ({joint.handle.id}): " + ", ".join(parts)

    # --- editing a step --------------------------------------------------------------------------------------------

    def edit_step(self, step_id: str, changes: dict[str, Any], intent: str | None) -> list[str]:
        """Change parameters of an earlier step and regenerate what depends on it, as one call.

        First the CAD edits the feature in place (the executor's ``edit_in_place``: the dependents are recomputed, the
        history stays as it was). When the CAD cannot (PartDesign features, sketches, a pending undo) the steps from
        this one on are rebuilt with the change: undo back to the step before it, run it edited, run the later ones
        again with their own ids, every one verified like a fresh op. A rebuild that fails anywhere puts the original
        steps back and says which step could not be rebuilt."""
        with self.lock:
            executor = self.require_executor()
            ids = [s.id for s in self.steps()]
            if step_id in self._aliases:
                raise ToolError(
                    f"{step_id} came from the opened file: it has no parameters to edit (build on it with a new step"
                    + (
                        '; its features have: inspect(action="tree") lists them by CAD name, '
                        'op(edit="<feature>", params={...}) changes one)'
                        if self.supports("set_feature_properties")
                        else ")"
                    ),
                    code="not_editable",
                    field_name="edit",
                )
            if step_id not in ids:
                raise ToolError(
                    f"no step in effect with id {step_id!r}; steps: {', '.join(ids) or 'none'}",
                    code="unknown_step",
                    field_name="edit",
                )
            ops = {op.id: op for op in executor.plan.operations}
            index = ids.index(step_id)
            native = [i for i in ids[index:] if i not in ops]
            kinds = {s.id: "run_native" if s.op == "native" else s.op for s in self.steps()}
            if native:
                raise ToolError(
                    f"{native[0]} is a {kinds[native[0]]} step, not a plan op: it cannot be edited"
                    if native[0] == step_id
                    else f"{native[0]} is a {kinds[native[0]]} step after {step_id}: regenerating would drop it. "
                    f'undo(to="{ids[index - 1] if index else "start"}") first, edit, and run it again',
                    code="not_editable",
                    field_name="edit",
                )
            old = ops[step_id]
            if isinstance(old, ExtensionOperation):
                operation = self._edited_extension(old, dict(changes), intent, executor.plan.kind)
                diffs = _field_diffs(old.params, operation.params)  # type: ignore[attr-defined]
            else:
                data = old.model_dump(mode="json")
                _apply_changes(old.op, data, dict(changes))
                if intent:
                    data["intent"] = intent
                operation = self._validate_operation(old.op, data)
                diffs = _field_diffs(old.model_dump(mode="json"), operation.model_dump(mode="json"))
            if not diffs and not intent:
                raise ToolError(
                    f"nothing to change: params {sorted(changes)} give the values {step_id} already has",
                    code="invalid_input",
                    field_name="params",
                )
            plan_data = executor.plan.model_dump(mode="python")
            plan_data["operations"] = [
                operation.model_dump(mode="python") if op["id"] == step_id else op for op in plan_data["operations"]
            ]
            try:
                new_plan = parse_plan(plan_data, external_ids=set(executor.external_features))
            except PlanError as exc:
                raise ToolError(exc.message, code=f"plan_{exc.rule}", usage=catalog.op_usage(old.op)) from exc
            if executor.state is ExecutorState.failed:
                executor.abandon_failed()
            before, _ = self.measure()
            head = f"ok: {step_id} edited ({old.op})" + (": " + "; ".join(diffs) if diffs else "")
            try:
                record = executor.edit_in_place(new_plan, [step_id], by="agent", reason=intent or "edit")
            except ExecutorStateError as exc:
                raise ToolError(str(exc), code="session_state") from exc
            if record.status == "unchanged":
                self.dirty = True
                return [head, "no change to the model (the CAD does not see these fields)", *self.state_lines(before)]
            if record.status == "applied":
                self.dirty = True
                lines = [head, "method: changed in place; recomputed: " + ", ".join(record.recomputed_ops)]
                lines.append(f"note: {record.verification}")
                lines += [f"followed: {note}" for note in record.followed]
                if record.result is not None:  # what the CAD could not do after the edit (a sheet not redrawn)
                    lines.extend(f"warning: {w}" for w in record.result.warnings)
                stale = [h.id for h in record.result.refs.removed] if record.result is not None else []
                if stale:
                    more = f" and {len(stale) - MAX_LISTED} more" if len(stale) > MAX_LISTED else ""
                    lines.append(
                        f"stale face handles (those faces no longer exist; ask for them again): "
                        f"{', '.join(stale[:MAX_LISTED])}{more}"
                    )
                return [*lines, *self.state_lines(before)]
            if record.status == "rolled_back" and record.failure_kind == "semantic":
                raise ToolError(
                    f"edit {step_id} was not applied\nreason: {record.failure}\nstate: unchanged\n"
                    "hint: pick another value; the part would break with this one",
                    code="edit_failed",
                )
            gone = [d for d in record.reference_drift if "no longer resolves" in d]
            if record.status == "rolled_back" and gone:
                # the same geometry is what a rebuild would meet: say what it breaks now, before touching anything
                more = f"\n... and {len(gone) - MAX_LISTED} more" if len(gone) > MAX_LISTED else ""
                raise ToolError(
                    f"edit {step_id} was not applied: it would break the steps after it, whose face is gone\n"
                    + "\n".join(gone[:MAX_LISTED])
                    + more
                    + "\nstate: unchanged (nothing was rebuilt)\n"
                    "hint: edit those steps first to name their face without a position (from_op + normal, "
                    "largest or smallest), or pick another value",
                    code="edit_failed",
                )
            first_line = (record.failure or "the CAD cannot change it in place").splitlines()[0]
            why = re.sub(r"^\w+:\d+: ", "", first_line)[:160]
            tail = [ops[i] for i in ids[index + 1 :]]
            back_to = ids[index - 1] if index else None
            hand_made = self._hand_made()
            if hand_made:
                raise ToolError(
                    f"edit {step_id} was not applied: it cannot be set in place ({why}) and rebuilding the steps "
                    f"from it would drop what the engineer made by hand ({', '.join(hand_made[:MAX_LISTED])})\n"
                    "state: unchanged\n"
                    "hint: add the change as a new step after the last one (the hand change stays); or ask the "
                    "engineer to put the hand change back with undo (it stops blocking as soon as the model is as "
                    "the steps left it), then repeat the edit",
                    code="would_lose_hand_edits",
                )
            # the steps built from the centre of a face stay where they are: the centre moves with every hole in the
            # face, so asking for the same thing again would put them somewhere else. The edited step too, unless the
            # edit names another face for it.
            moves_it = any(d.split(" ", 1)[0] in PLACE_FIELDS for d in diffs)
            executor.hold_places([*([] if moves_it else [step_id]), *[s.id for s in self.steps()[index + 1 :]]])
            try:
                failed = self._replay_edit(executor, back_to, [old, *tail], [operation, *tail])
            except AdapterError as exc:
                failure = self.adapter_failure(exc, f"edit {step_id}")
                raise ToolError(
                    f"{failure.message}\nWARNING: the steps from {step_id} on may be only partly regenerated; "
                    "inspect(action=tree), then undo or run them again",
                    code=failure.code,
                ) from exc
            finally:
                moved = executor.release_places()
            if failed is not None:
                what = f"{failed.op_id} {plain_op_name(failed.op)}"
                self_failed = failed.op_id == step_id
                raise ToolError(
                    "\n".join(
                        [
                            f"edit {step_id} was not applied: "
                            + ("the edited step itself fails" if self_failed else f"{what} cannot be rebuilt after it"),
                            describe_failure(failed),
                            "state: unchanged (the original steps were rebuilt)",
                            *self.state_lines(),
                            "hint: "
                            + (
                                "pick another value"
                                if self_failed
                                else f"edit {failed.op_id} to fit first (same call) or pick another value"
                            ),
                        ]
                    ),
                    code="edit_failed",
                )
            self.dirty = True
            regenerated = ", ".join([step_id, *[s.id for s in tail]])
            return [
                head,
                f"method: rebuilt from {step_id} (not changeable in place: {why}); regenerated: {regenerated}",
                *[
                    f"warning: {op_id} could not stay where it was: it moved {num(away)} mm (its face is not the one "
                    "it was drilled or sketched on any more); check its position"
                    for op_id, away in moved.items()
                ],
                *self.state_lines(before),
            ]

    def _hand_made(self) -> list[str]:
        """What the engineer made or changed by hand in the CAD that the plan does not describe: a rebuild of the
        steps would drop it."""
        names = self.hand_edits.touched(self.doc_name)  # what was done in another document is not at stake here
        try:
            tree = self.adapter.feature_tree(Empty(operation_id=self._op_id("tree")))
        except AdapterError:
            return list(dict.fromkeys(names))
        names += [node.name for node in tree.nodes if node.parameters.get("manual")]
        return list(dict.fromkeys(names))

    def _edited_extension(
        self, old: ExtensionOperation, changes: dict[str, Any], intent: str | None, kind: DocumentKind
    ) -> ExtensionOperation:
        """``old`` with ``changes`` merged into its arguments, checked against the adapter's model again."""
        name = plain_op_name(old.op)
        specs = self.extensions()
        spec = specs.get(name)
        if spec is None:
            raise ToolError(
                f"{old.id} is {name}, an operation this CAD no longer declares: it cannot be edited",
                code="not_editable",
                field_name="edit",
            )
        unknown = [k for k in changes if k not in old.params]
        if unknown:
            raise ToolError(
                f"{name} has no parameter {', '.join(unknown)}; parameters: {', '.join(old.params)}",
                code="invalid_params",
                field_name="params." + unknown[0],
                usage=catalog.op_usage(name, specs),
            )
        return self._validate_extension(spec, {**old.params, **changes}, old.id, intent or old.intent, kind)

    def _replay_edit(
        self, executor: Executor, back_to: str | None, original: list[BaseModel], edited: list[BaseModel]
    ) -> StepRecord | None:
        """Undo to ``back_to`` and run ``edited``; on a failing step put ``original`` back and return that step's
        record. ``None`` = the edited steps all ran."""
        try:
            executor.undo_to(back_to, by="agent", reason="edit")
        except UndoFloorError as exc:
            raise ToolError(
                f"{exc.floor_op!r} and everything before it were made before this document was reopened: the CAD "
                "kept no history for them, so a step among them cannot be edited (add a new step instead)",
                code="not_editable",
            ) from exc
        except (UndoInterruptedError, ExecutorStateError) as exc:
            raise ToolError(str(exc), code="undo_failed") from exc
        failed = _run_ops(executor, edited)
        if failed is None:
            return None
        if executor.state is ExecutorState.failed:
            executor.abandon_failed()
        try:
            executor.undo_to(back_to, by="agent", reason="edit rolled back")
            again = _run_ops(executor, original)
        except (UndoFloorError, UndoInterruptedError, ExecutorStateError) as exc:
            again = failed
            detail = str(exc)
        else:
            detail = describe_failure(again) if again is not None else ""
        if again is not None:
            # the record must say what the model is now, not what it was: drop the steps that never ran and name the
            # ones the document holds and the ones it lost
            if executor.state is ExecutorState.failed:
                executor.abandon_failed()
            holds = [s.id for s in self.steps()]
            lost = [op.id for op in original if op.id not in holds]  # type: ignore[attr-defined]
            lines = [
                f"edit was not applied and the original steps could not be rebuilt ({detail})",
                "WARNING: the document is not what it was",
                f"the model now holds: {', '.join(holds) or 'nothing'}",
                f"lost (not rebuilt): {', '.join(lost) or 'none'}",
            ]
            try:
                lines += self.state_lines()
            except (AdapterError, ToolError):
                lines.append("state: could not be measured; inspect(action=tree)")
            lines.append(
                "hint: make the lost steps again with a selector that does not depend on a position "
                "(from_op + largest/smallest/normal), or undo, or reopen the saved file"
            )
            raise ToolError("\n".join(lines), code="edit_restore_failed")
        return failed

    # --- purpose ---------------------------------------------------------------------------------------------------

    def set_purpose(self, purpose: str | None) -> list[str]:
        """Mark the document as a ``sketch`` (concept model: primitives and booleans are fine) or ``detail`` (the
        default: features). Kept in the session record, so it survives save and reopen."""
        with self.lock:
            self.require_executor()
            if purpose not in PURPOSES:
                raise ToolError(
                    "set_purpose needs purpose: sketch (a concept model, primitives and booleans are fine) or "
                    "detail (features: sketch + extrude/cut, hole, fillet)",
                    code="invalid_input",
                    field_name="purpose",
                    usage='doc(action="set_purpose", purpose="sketch|detail")',
                    example='doc(action="set_purpose", purpose="sketch")',
                )
            self.purpose = purpose
            self.dirty = True
            return [f"purpose: {purpose}"]

    # --- conventions -----------------------------------------------------------------------------------------------

    def _convention_lines(self, name: str, operation: Any) -> list[str]:
        """After a ``hole``: the class the convention gave it and the colour of its faces, and what is wrong with a
        convention file (the default applies meanwhile)."""
        if name != "hole":
            return []
        resolved = self.conventions.resolve()
        lines: list[str] = []
        chosen = resolved.holes.colour_for(
            threaded=getattr(operation, "standard", None) == HoleStandard.iso_tapped,
            fit=getattr(operation, "fit", None),
        )
        if chosen is not None:
            lines.append(f"hole class: {chosen[0]}, faces coloured {list(chosen[1])}")
        lines += [f"convention file problem: {p}" for p in resolved.problems]
        return lines

    def convention(self, values: dict[str, Any] | None) -> list[str]:
        """Show the convention of holes, or change it when the engineer asked: ``values`` are keys of ``agujeros`` (see
        ``cotaforge.conventions.holes``); ``{"reiniciar": true}`` removes the section of that scope. ``alcance`` is
        ``proyecto`` (default: the ``convenciones.yaml`` of the design space) or ``usuario`` (the one in the data folder);
        never the memory."""
        with self.lock:
            if values:
                scope = str(values.get("alcance", "proyecto"))
                reset = bool(values.get("reiniciar"))
                changes = {k: v for k, v in values.items() if k not in ("reiniciar", "alcance")}
                if reset and changes:
                    raise ToolError(
                        "reiniciar is alone: do not pass other keys with it",
                        code="invalid_input",
                        field_name="values",
                    )
                try:
                    path = self.conventions.set_holes(scope, changes, reset=reset)
                except ConventionError as exc:
                    raise ToolError(
                        str(exc),
                        code="invalid_input",
                        field_name="values",
                        usage='doc(action="convention", values={"colores": {"liso": [0, 192, 0]}, "alcance": "usuario"})',
                        example='doc(action="convention", values={"grado_it_preciso": 7})',
                    ) from exc
                head = [f"convention file {'reset' if reset else 'written'}: {path}"]
                head.append(
                    "holes made or edited from now on take it; the holes already made keep their colour until edited"
                )
            else:
                head = []
            resolved = self.conventions.resolve()
            lines = [*head, "agujeros: " + json.dumps(resolved.holes.describe(), ensure_ascii=False)]
            lines.append(
                "keys: activa, colores{preciso,liso,roscado}, grado_it_preciso, isometrico_en_color; "
                'alcance "proyecto"|"usuario"; reiniciar true drops the section'
            )
            lines.append(
                "files: " + (", ".join(resolved.sources) if resolved.sources else "none (the defaults of the harness)")
            )
            lines += [f"problem: {p}" for p in resolved.problems]
            return lines

    # --- material and route ----------------------------------------------------------------------------------------

    def design_attributes(self) -> DesignAttributes:
        """What the document says about its material and route (empty when the CAD cannot store them)."""
        if "design_attributes" not in self.capabilities.tools:
            return DesignAttributes()
        try:
            return self.adapter.design_attributes(Empty(operation_id=self._op_id("attributes")))
        except AdapterError as exc:
            raise self.adapter_failure(exc, "read the material and route") from exc

    def _attributes_lines(self, attributes: DesignAttributes) -> list[str]:
        declared = rules.declared_set(self.settings)
        lines = [f"material: {attributes.material or 'none'}" + self._declared_note(declared, attributes.material)]
        lines.append(f"route: {', '.join(attributes.route) or 'none'}")
        if attributes.title:
            lines.append(f"title: {attributes.title}")
        return lines

    @staticmethod
    def _declared_note(declared: Any, material: str | None) -> str:
        if not declared.materials_declared:
            return ""
        if not material:
            return f" (declared: {', '.join(m.name for m in declared.materials)})"
        result = declared.validate_material(material)
        return f" (declared: {result.matched})" if result.ok else " (NOT in the declared set)"

    def set_material(self, material: str | None) -> list[str]:
        self.need("set_design_attributes", "doc(action=set_material)")
        with self.lock:
            self.require_executor()
            name = (material or "").strip()
            if material is None:
                raise ToolError(
                    "set_material needs the material (an empty string clears it)",
                    code="invalid_input",
                    field_name="material",
                    usage='doc(action="set_material", material="<name or designation>")',
                    example='doc(action="set_material", material="Acero 1045")',
                )
            if name:
                reason = rules.material_rejection(rules.declared_set(self.settings), name)
                if reason:
                    raise ToolError(
                        reason,
                        code="outside_declared",
                        field_name="material",
                        usage="pick a declared material, or declare this one first: memory(action=save, type=material)",
                    )
            try:
                attributes = self.adapter.set_design_attributes(
                    SetDesignAttributesRequest(operation_id=self._op_id("material"), material=name)
                )
            except AdapterError as exc:
                raise self.adapter_failure(exc, "set_material") from exc
            self.dirty = True
            return self._attributes_lines(attributes)

    def set_title(self, title: str | None) -> list[str]:
        """The designation of the part (the name an assembly's parts list prints), kept in the document."""
        self.need("set_design_attributes", "doc(action=set_title)")
        with self.lock:
            self.require_executor()
            if title is None:
                raise ToolError(
                    "set_title needs the designation in the project language (an empty string clears it)",
                    code="invalid_input",
                    field_name="name",
                    usage='doc(action="set_title", name="<designation>")',
                    example='doc(action="set_title", name="Placa superior")',
                )
            try:
                attributes = self.adapter.set_design_attributes(
                    SetDesignAttributesRequest(operation_id=self._op_id("title"), title=title.strip())
                )
            except AdapterError as exc:
                raise self.adapter_failure(exc, "set_title") from exc
            self.dirty = True
            return self._attributes_lines(attributes)

    def set_route(self, route: list[str] | None) -> list[str]:
        self.need("set_design_attributes", "doc(action=set_route)")
        with self.lock:
            self.require_executor()
            if route is None:
                raise ToolError(
                    "set_route needs the route: the ids of declared processes, in order ([] clears it)",
                    code="invalid_input",
                    field_name="route",
                    usage='doc(action="set_route", route=["<process id>", ...])',
                    example='doc(action="set_route", route=["torno-cnc-taller-a"])',
                )
            steps = tuple(step.strip() for step in route if step.strip())
            reason = rules.route_rejection(rules.declared_set(self.settings), steps)
            if reason:
                raise ToolError(
                    reason,
                    code="outside_declared",
                    field_name="route",
                    usage="use ids of declared processes, or declare one first: memory(action=save, type=process)",
                )
            try:
                attributes = self.adapter.set_design_attributes(
                    SetDesignAttributesRequest(operation_id=self._op_id("route"), route=steps)
                )
            except AdapterError as exc:
                raise self.adapter_failure(exc, "set_route") from exc
            self.dirty = True
            return self._attributes_lines(attributes)

    def _refuse_undeclared_attributes(self, step_id: str, result: RunNativeResult, before: DesignAttributes) -> None:
        """After a native step that went through: if it changed the material or the route to something outside the
        declared set, take it back (the step and the attributes) and say so."""
        if "design_attributes" not in self.capabilities.tools:
            return
        after = self.design_attributes()
        if after == before:
            return
        declared = rules.declared_set(self.settings)
        reasons = []
        if after.material != before.material and after.material:
            reason = rules.material_rejection(declared, after.material)
            if reason:
                reasons.append(reason)
        if after.route != before.route:
            reason = rules.route_rejection(declared, after.route)
            if reason:
                reasons.append(reason)
        if not reasons:
            return
        if result.feature is not None:
            self.adapter.undo_last(Empty(operation_id=step_id))
        # the metadata of the document is not part of what the adapter's undo restores
        self.adapter.set_design_attributes(
            SetDesignAttributesRequest(operation_id=step_id, material=before.material or "", route=before.route)
        )
        raise ToolError(
            f"{step_id} native was undone: it changed the material or route to something that is not declared\n"
            + "\n".join(reasons)
            + "\nstate: unchanged",
            code="outside_declared",
        )

    # --- native scripting ----------------------------------------------------------------------------------------

    def run_native(self, code: str, intent: str | None, op_id: str | None, timeout_s: float) -> list[str]:
        """One native fragment as one step of the history: the adapter runs it measured and rolls it back on failure; a
        step that changed the document is recorded in the executor (``record_external_step``) so ``undo`` reverts it in
        its place among the ops."""
        self.need("run_native", "run_native")
        with self.lock:
            executor = self.require_executor()
            step_id = op_id or self.next_id()
            if not STEP_ID.match(step_id):
                raise ToolError(
                    f"id {step_id!r} is not a valid step id (lowercase letters, digits, _ ; start with a letter)",
                    code="invalid_input",
                    field_name="id",
                )
            if step_id in self.known_ids():
                raise ToolError(
                    f"id {step_id!r} is already used by a step in effect; pick another or omit id",
                    code="duplicate_id",
                    field_name="id",
                )
            if executor.state is ExecutorState.failed:
                executor.abandon_failed()
            before, _ = self.measure()
            wording = (intent or "").strip()
            attributes_before = self.design_attributes()
            stat_before = self._stat_of_file()
            techdraw = recovery.touches_techdraw(code)
            copy_note: list[str] = []
            if techdraw:  # the one kind of fragment that has taken the CAD down: what is unsaved is copied first
                what = f"run_native {step_id}" + (f" ({wording})" if wording else "")
                copy, why = recovery.take(self, what)
                if why is not None:
                    copy_note.append(f"warning: {why}; save the document before more TechDraw scripts")
            try:
                result = self.adapter.run_native(
                    RunNativeRequest(operation_id=step_id, code=code, intent=wording, timeout_s=timeout_s)
                )
                saved_note = self._own_native_write(stat_before)
            except AdapterError as exc:
                usage.record_native_call(
                    self.settings.workspace,
                    code=code,
                    intent=wording,
                    ok=False,
                    rolled_back=False,
                    duration_ms=0.0,
                    error_type=exc.info.code.value,
                )
                failure = self.adapter_failure(exc, f"run_native {step_id}")
                if exc.info.code.value == "session_lost":
                    failure.message += "\n" + "\n".join(
                        _native_crash_lines(step_id, wording, techdraw, exc, self.crash_note("native"))
                    )
                raise failure from exc
            usage.record_native_call(
                self.settings.workspace,
                code=code,
                intent=wording,
                ok=result.ok,
                rolled_back=result.rolled_back,
                duration_ms=result.duration_ms,
                error_type=result.error.type if result.error is not None else None,
            )
            report = Overflow(self.settings.workspace, f"run_native-{step_id}", f"run_native {step_id}: {wording}")
            if not result.ok:
                lines = [f"{step_id} native was not applied", *_native_failure_lines(result, report), *saved_note]
                if result.rolled_back:
                    lines.append("state: unchanged")
                else:
                    lines.append("WARNING: the rollback could not be verified; inspect the document before going on")
                lines.extend(self.state_lines())
                lines.extend(copy_note)
                lines.extend(_report_warning(report))
                raise ToolError("\n".join(lines), code="native_failed")
            self._refuse_undeclared_attributes(step_id, result, attributes_before)
            lines = [f"ok: {step_id} native"]
            if result.feature is None:
                lines.append("changes: none recorded (nothing to undo)")
            else:
                after, _ = self.measure()
                try:
                    executor.record_external_step(
                        step_id, "native", feature=result.feature, mass_before=before, mass_after=after, note=wording
                    )
                except ExecutorStateError as exc:  # keep the adapter and the history in agreement
                    self.adapter.undo_last(Empty(operation_id=step_id))
                    raise ToolError(str(exc), code="session_state") from exc
                self.dirty = True
            lines.extend(_native_change_lines(result, report))
            lines.extend(saved_note)
            lines.extend(copy_note)
            lines.extend(_report_warning(report))
            return lines

    # --- undo ---------------------------------------------------------------------------------------------------

    @staticmethod
    def _undo_floor_refusal(floor_op: str) -> ToolError:
        return ToolError(
            f"nothing was undone: the undo history does not survive reopening a file, and {floor_op!r} and everything "
            "before it were made before this document was reopened\n"
            "instead: correct it with a new step on top (cut, fillet, translate...), or rebuild: "
            'doc(action="close", discard=true), doc(action="new", name=...) and repeat the steps you want '
            "(the saved file stays as it was saved)",
            code="nothing_to_undo",
        )

    def undo(self, to: str | None) -> list[str]:
        with self.lock:
            executor = self.require_executor()
            steps = self.steps()
            if not steps:
                raise ToolError(
                    "nothing to undo (features that came from an opened file are not undoable)", code="nothing_to_undo"
                )
            ids = [s.id for s in steps]
            if to is None:
                target = ids[-2] if len(ids) > 1 else None
            elif to in self._aliases or to == "start":
                target = None
            elif to in ids:
                target = to
                if ids[-1] == to:
                    raise ToolError(f"{to} is already the last step; nothing to undo after it", code="nothing_to_undo")
            else:
                raise ToolError(
                    f"no step in effect with id {to!r}; steps: {', '.join(ids)}",
                    code="unknown_step",
                    field_name="to",
                    usage='undo() | undo(to="<step id>") | undo(to="start")',
                )
            if target is None:  # "everything": silently doing nothing would read as success
                floor = {s.op_id for s in executor.trace().steps[: executor.undo_floor]}
                if not [i for i in ids if i not in floor]:
                    raise self._undo_floor_refusal(ids[-1])
            before, _ = self.measure()
            if executor.state is ExecutorState.failed:
                executor.abandon_failed()
            try:
                reverted = executor.undo_to(target, by="agent", reason="undo tool")
            except UndoFloorError as exc:
                raise self._undo_floor_refusal(exc.floor_op) from exc
            except UndoInterruptedError as exc:
                raise ToolError(str(exc), code="undo_interrupted") from exc
            except ExecutorStateError as exc:
                raise ToolError(str(exc), code="undo_failed") from exc
            except AdapterError as exc:
                raise self.adapter_failure(exc, "undo") from exc
            names = {s.id: s.op for s in steps}
            undone = [f"{i} {names[i]}" for i in reverted if i in names]
            self.dirty = True
            remaining = self.steps()
            lines = ["undone: " + ", ".join(undone)]
            lines.extend(f"warning: {w}" for w in executor.last_undo_warnings)
            lines.append(
                f"steps now: {len(remaining)}" + (f", last {remaining[-1].id} {remaining[-1].op}" if remaining else "")
            )
            lines.extend(self.state_lines(before))
            return lines


def _objects(label: str, items: tuple[NativeObject, ...], report: Overflow) -> str:
    shown, more = report.items(label, [f"{item.name} ({item.type_id})" for item in items], MAX_NATIVE_OBJECTS)
    return f"{label}: {', '.join(shown)}" + (f"; {more}" if more else "")


def _world_point(frame: SketchFrame, point: Any, label: str, tolerance: float) -> list[float]:
    """A world [x, y, z] point as [u, v] in ``frame``; refused when it is not on the plane of the frame."""
    if not (
        isinstance(point, list | tuple)
        and len(point) == 3
        and all(isinstance(c, int | float) and not isinstance(c, bool) for c in point)
    ):
        raise ToolError(
            f"frame=world needs [x, y, z] mm points; {label} is {_show(point)}",
            code="invalid_params",
            field_name="params.entities",
        )
    u, v, off = frame.to_local((float(point[0]), float(point[1]), float(point[2])))
    if abs(off) > tolerance:
        raise ToolError(
            f"{label} {vec(point)} is {num(abs(off))} mm off the sketch plane (through {vec(frame.origin)}, normal "
            f"{vec(frame.n)}): give points on it",
            code="invalid_params",
            field_name="params.entities",
        )
    return [round(u, 9), round(v, 9)]


def _native_change_lines(result: RunNativeResult, report: Overflow) -> list[str]:
    lines = []
    for label, items in (("created", result.created), ("modified", result.modified), ("removed", result.removed)):
        if items:
            lines.append(_objects(label, items, report))
    # the numbers of the whole document (everything solid in it, not only what the steps built)
    delta = result.volume_delta_mm3
    lines.append(f"volume change: {'+' if delta >= 0 else ''}{num(delta)} mm3")
    if result.bbox_after is not None:
        box = result.bbox_after
        lines.append(f"bbox: size {vec(box.size())} mm, min {vec(box.min)}, max {vec(box.max)}")
    lines.extend(_native_output_lines(result, report))
    return lines


def _native_failure_lines(result: RunNativeResult, report: Overflow) -> list[str]:
    lines = []
    error = result.error
    if error is not None:
        where = f" (line {error.line})" if error.line else ""
        lines.append(f"error: {error.type}{where}: {report.text('error', error.message, NATIVE_ERROR_CHARS)}")
        if error.hint:
            lines.append(f"hint: {error.hint}")
    broken = [f"{item.name}: {item.reason}" for item in result.broken]
    shown, more = report.items("broken objects", broken, MAX_NATIVE_ITEMS)
    lines.extend(f"broken: {report.shorten('broken', b, NATIVE_WARNING_CHARS)}" for b in shown)
    if more:
        lines.append(f"broken: {more}")
    lines.extend(_native_output_lines(result, report))
    return lines


def _native_crash_lines(
    step_id: str, intent: str, techdraw: list[str], exc: AdapterError, adapter_text: str | None = None
) -> list[str]:
    """What a ``run_native`` call that ended the CAD says about it: which fragment was running, what ended the process
    when it is known, and that the fragment's own logic is not what is wrong. ``adapter_text`` is the adapter's own
    wording of all but the first line (``note:crash.native``); the empty text says nothing more."""
    running = f"the fragment that was running: run_native {step_id}" + (f" ({intent})" if intent else "")
    if adapter_text is not None:
        return [running, adapter_text] if adapter_text else [running]
    lines = [running + (f"; it works with TechDraw ({', '.join(techdraw)})" if techdraw else "")]
    meaning = recovery.exit_meaning(exc.info.details.get("exit_code"))
    if meaning:
        lines.append(f"FreeCAD ended with {meaning}")
    if techdraw:
        lines.append(
            "the TechDraw calls of the fragment are the likely cause (a fault of FreeCAD, not of the script's logic): "
            "do not repeat them as they are; cad_guide(topic=native) lists the known ones and "
            "drawing(action=add, items=[{type: line, ...}]) draws ISO 128 lines without them"
        )
    return lines


def _report_warning(report: Overflow) -> list[str]:
    """Write the full report when a line was cut; the warning line when that failed."""
    warning = report.write()
    return [f"warning: {warning}"] if warning else []


def _native_output_lines(result: RunNativeResult, report: Overflow) -> list[str]:
    """What the fragment printed and the warnings: the start of each, and what was left out and where it is."""
    lines = []
    text = result.stdout.strip()
    if text:
        lines.append("output: " + report.text("output", text, NATIVE_OUTPUT_CHARS))
    if result.stdout_dropped_chars:
        lines.append(
            f"output: {result.stdout_dropped_chars} more characters were printed but not kept at all (the CAD keeps "
            f"the first {len(result.stdout)}); print less"
        )
    shown, more = report.items("warnings", list(result.warnings), MAX_NATIVE_ITEMS)
    lines.extend(f"warning: {report.shorten('warning', w, NATIVE_WARNING_CHARS)}" for w in shown)
    if more:
        lines.append(f"warning: {more}")
    return lines


def _plain(value: float) -> float | int:
    """A coordinate as it reads best in a JSON example: ``1`` rather than ``1.0``, no ``-0``."""
    rounded = round(float(value), 6) + 0.0
    return int(rounded) if rounded.is_integer() else rounded


def _short(exc: Exception) -> str:
    text = exc.info.message if isinstance(exc, AdapterError) else str(exc)
    return text if len(text) <= 160 else text[:157] + "..."


def _same_path(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return a == b


def measured_mass_line(item: MassProperties | BodyInfo) -> str:
    """``mass: 4.512 kg (ExternalCAD measured it)``: a mass the CAD itself measured with the material it knows; the
    server's own density is not used for it. ``item.mass_kg`` must be set."""
    return f"mass: {num(item.mass_kg or 0.0, 4)} kg ({item.mass_source} measured it)"


def _text_lines(text: str) -> list[str]:
    """What an operation reported as free text (``OperationResult.text``), line by line, as it is: a list is not a
    warning. A very long one is cut and says how much it left out."""
    lines = text.splitlines()
    if len(lines) > MAX_TEXT_LINES:
        lines = [*lines[:MAX_TEXT_LINES], f"... {len(lines) - MAX_TEXT_LINES} more lines not shown"]
    return lines


def _feature_of(record: StepRecord) -> FeatureHandle | None:
    if not record.attempts:
        return None
    result = record.attempts[-1].result
    return result.feature if result is not None else None


def _sketch_line(operation: BaseModel) -> str:
    entities = getattr(operation, "entities", ())
    try:
        profiles = sketch_profiles(entities)
    except ValueError as exc:
        return f"profiles: none closed ({exc})"
    total = sum(p.area_mm2 for p in profiles)
    return f"profiles: {len(profiles)} closed, area {num(total)} mm2 (from the entities)"


def _run_ops(executor: Executor, ops: list[BaseModel]) -> StepRecord | None:
    """Append ``ops`` to the plan and run them one by one: the record of the first that fails, else ``None``."""
    executor.extend(ops)
    for _ in ops:
        record = executor.next_operation()
        if record.status is not StepStatus.success:
            return record
    return None


def _apply_changes(op: str, data: dict[str, Any], changes: dict[str, Any]) -> None:
    """Merge the changed parameters into ``data`` (the full dump of the step); ``dimensions`` edits named dimensions."""
    if "frame" in changes:
        raise ToolError(
            "frame applies to a new sketch step; to edit one, give params.entities in the plane's own [u, v] frame "
            "or change a named dimension",
            code="invalid_params",
            field_name="params.frame",
        )
    dimensions = changes.pop("dimensions", None)
    if dimensions is not None:
        if op != "sketch" or not isinstance(dimensions, dict):
            raise ToolError(
                'dimensions={"<name>": value} edits the named dimensions of a sketch step',
                code="invalid_params",
                field_name="params.dimensions",
            )
        _set_dimensions(data, dimensions)
    editable = [k for k in data if k not in ("id", "op", "intent", "links")]
    unknown = [k for k in changes if k not in editable]
    if unknown:
        raise ToolError(
            f"{op} has no parameter {', '.join(unknown)}; parameters: {', '.join(editable)}",
            code="invalid_params",
            field_name="params." + unknown[0],
            usage=catalog.op_usage(op),
        )
    data.update(changes)
    if op == "hole" and ("position" in changes) != ("at" in changes):
        data["at" if "position" in changes else "position"] = None  # one way of placing it: the one just given
    if op == "hole" and {"size", "standard"} & changes.keys() and "diameter_mm" not in changes:
        if data.get("standard") in ("iso_clearance", "iso_tapped"):
            data.pop("diameter_mm", None)  # derived from the size: let the thread table fill it in again


def _set_dimensions(data: dict[str, Any], dimensions: dict[str, Any]) -> None:
    named = {nd["name"]: nd for nd in data.get("named_dimensions", ())}
    entities = {e["name"]: e for e in data["entities"]}
    for name, value in dimensions.items():
        dimension = named.get(name)
        if dimension is None:
            # an entity's own dimension, by "<entity>.<attribute>" (r.width, c.diameter, r.chamfer)
            entity_name, _, attribute = name.partition(".")
            if entity_name in entities and attribute in DIMENSION_ATTRIBUTES:
                dimension = {"entity": entity_name, "attribute": attribute}
        if dimension is None:
            known = ", ".join(named) or "none"
            raise ToolError(
                f"the sketch has no dimension {name!r}; named: {known}; or an entity's own as "
                f"<entity>.<{'|'.join(DIMENSION_ATTRIBUTES)}> (entities: {', '.join(entities)})",
                code="invalid_params",
                field_name="params.dimensions",
            )
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ToolError(
                f"dimension {name!r} needs a number in mm", code="invalid_params", field_name="params.dimensions"
            )
        entity = entities[dimension["entity"]]
        ends = _line_ends(entity)
        _set_dimension(entity, dimension["attribute"], float(value), name)
        _keep_joined(data["entities"], entity, ends, name)


_JOIN_TOLERANCE_MM = 1e-6


def _line_ends(entity: dict[str, Any]) -> dict[str, tuple[float, float]]:
    """Where the ends of a line are (the points other lines and arcs of a closed profile are joined at)."""
    if entity["kind"] != "line":
        return {}
    return {key: (float(entity[key][0]), float(entity[key][1])) for key in ("start", "end")}


def _arc_ends(entity: dict[str, Any]) -> list[tuple[float, float]]:
    """The two end points of an arc entity, whatever way it is given."""
    if entity["kind"] == "arc":
        cx, cy = entity["center"]
        radius = entity["radius"]
        return [
            (cx + radius * math.cos(math.radians(entity[key])), cy + radius * math.sin(math.radians(entity[key])))
            for key in ("start_deg", "end_deg")
        ]
    return [(float(entity[key][0]), float(entity[key][1])) for key in ("start", "end")]


def _keep_joined(
    entities: list[dict[str, Any]], moved: dict[str, Any], ends: dict[str, tuple[float, float]], label: str
) -> None:
    """A line whose end was moved by its dimension drags along the lines joined to that end, so a closed profile stays
    closed (the end of a neighbour is the same point). An arc joined there cannot follow (its radius and angles would
    change): the dimension is refused and says what to do instead. Never leaves an open wire."""
    for key, was in ends.items():
        now = (float(moved[key][0]), float(moved[key][1]))
        if math.dist(was, now) <= _JOIN_TOLERANCE_MM:
            continue
        for other in entities:
            if other is moved:
                continue
            if other["kind"] == "line":
                for end in ("start", "end"):
                    if math.dist(was, (float(other[end][0]), float(other[end][1]))) > _JOIN_TOLERANCE_MM:
                        continue
                    other[end] = [now[0], now[1]]
                    if math.dist(other["start"], other["end"]) <= _JOIN_TOLERANCE_MM:
                        raise ToolError(
                            f"dimension {label!r} would bring line {other['name']} to zero length; "
                            "change the entities themselves in params.entities",
                            code="invalid_params",
                            field_name="params.dimensions",
                        )
            elif other["kind"] in ("arc", "arc3", "arc_ends") and any(
                math.dist(was, point) <= _JOIN_TOLERANCE_MM for point in _arc_ends(other)
            ):
                raise ToolError(
                    f"dimension {label!r} moves the {key} of line {moved['name']}, where arc {other['name']} is "
                    "joined: the arc cannot follow by name; change both entities in params.entities so the profile "
                    "stays closed",
                    code="invalid_params",
                    field_name="params.dimensions",
                )


def _set_dimension(entity: dict[str, Any], attribute: str, value: float, label: str) -> None:
    kind = entity["kind"]
    if attribute in ("width", "height", "side", "diameter") and attribute in entity:
        entity[attribute] = value
    elif attribute == "chamfer" and kind == "rect":
        entity["chamfer_mm"] = value
    elif attribute == "radius" and kind == "circle":
        entity["diameter"] = 2 * value
    elif attribute == "radius" and "radius" in entity:
        entity["radius"] = value
    elif attribute == "length" and "start" in entity and "end" in entity:
        sx, sy = entity["start"]
        ex, ey = entity["end"]
        was = math.dist((sx, sy), (ex, ey))
        entity["end"] = [sx + (ex - sx) / was * value, sy + (ey - sy) / was * value] if was else [sx + value, sy]
    elif attribute in ("u", "v"):
        axis = 0 if attribute == "u" else 1
        if "center" in entity:
            entity["center"][axis] = value
        elif "start" in entity and "end" in entity:  # a line or slot moves as a whole; the dimension is its start
            shift = value - entity["start"][axis]
            for key in ("start", "end"):
                entity[key][axis] += shift
        else:
            raise _not_by_name(label, attribute, entity)
    else:
        raise _not_by_name(label, attribute, entity)


def _not_by_name(label: str, attribute: str, entity: dict[str, Any]) -> ToolError:
    return ToolError(
        f"dimension {label!r} ({attribute} of {entity['kind']} {entity['name']}) cannot be set by name; "
        "change the entity itself in params.entities",
        code="invalid_params",
        field_name="params.dimensions",
    )


def _show(value: Any) -> str:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return num(value)
    if isinstance(value, list | tuple) and all(isinstance(v, int | float) for v in value):
        return vec(value)
    text = json.dumps(value, separators=(",", ":"))
    return text if len(text) <= 60 else text[:57] + "..."


def _field_diffs(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """What changed between two dumps of a step, as ``field was -> now`` (a sketch: per entity, by the name of its
    dimension when it has one)."""
    out: list[str] = []
    dimension_of = {(nd["entity"], nd["attribute"]): nd["name"] for nd in new.get("named_dimensions", ())}
    for key in new:
        if key in ("id", "op", "intent", "links") or old.get(key) == new[key]:
            continue
        if key == "entities":
            was = {e["name"]: e for e in old.get("entities", ())}
            for entity in new[key]:
                before = was.get(entity["name"])
                if before == entity:
                    continue
                for field_name, value in entity.items():
                    if before is None or before.get(field_name) != value:
                        label = dimension_of.get((entity["name"], field_name)) or f"{entity['name']}.{field_name}"
                        out.append(f"{label} {_show(before.get(field_name)) if before else 'new'} -> {_show(value)}")
        else:
            out.append(f"{key} {_show(old.get(key))} -> {_show(new[key])}")
    return out


def describe_failure(record: StepRecord) -> str:
    """Why a step did not succeed, in a few lines the agent can act on."""
    if record.plan_error:
        return f"reason: {record.plan_error}"
    last = record.attempts[-1] if record.attempts else None
    if last is None:
        return "reason: no attempt was made"
    if last.error is not None:
        text = f"reason: {last.error.code.value}: {last.error.message}"
        if last.diagnosis is not None:
            text += f"\ndiagnosis: {last.diagnosis.kind.value}: {last.diagnosis.evidence[:300]}"
        return text
    if not last.level1.ok:
        return f"reason: {last.level1.name}: {last.level1.message}"
    failing = [c for c in last.level2 if c.kind.value == "asserted" and c.ok is False]
    if failing:
        parts = [f"{c.name} expected {c.expected} observed {c.observed}" for c in failing[:3]]
        return "reason: measured result differs from the expectation (step undone): " + "; ".join(parts)
    return "reason: the step did not verify"
