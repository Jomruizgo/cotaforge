"""Shared fixtures: a session over the fake adapter, a direct tool caller and fake variants with fewer capabilities."""

import json
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from cotaforge.adapters.fake import FakeAdapter
from cotaforge.contract import TOOL_REGISTRY, Capabilities

from cotaforge_mcp import engineer_orders
from cotaforge_mcp.config import Settings
from cotaforge_mcp.reply import Reply
from cotaforge_mcp.server import make_session
from cotaforge_mcp.session import Session
from cotaforge_mcp.tools import call_tool
from tests.pdf_support import minimal_pdf

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002000001e221bc330000000049454e44ae426082"
)


MakeSession = Callable[[FakeAdapter], Session]


class VisibleFake(FakeAdapter):
    """The fake with a 3D view: set_view, style, section and capture exist (a 1x1 PNG), like a visible CAD window.
    What each one was asked is kept in ``views`` / ``styles`` / ``sections``."""

    def __init__(self) -> None:
        super().__init__()
        self.views: list[Any] = []
        self.styles: list[Any] = []
        self.sections: list[Any] = []

    def set_view(self, request: Any) -> Any:
        from cotaforge.contract import Ack

        self.views.append(request)
        return Ack()

    def style_bodies(self, request: Any) -> Any:
        from cotaforge.contract import Ack

        self.styles.append(request)
        return Ack()

    def section_plane(self, request: Any) -> Any:
        from cotaforge.contract import Ack

        self.sections.append(request)
        return Ack()

    def capture(self, request: Any) -> Any:
        from cotaforge.contract import Ack

        Path(request.path).write_bytes(PNG)
        return Ack()


class DrawingFake(FakeAdapter):
    """The fake with drawing sheets: create / annotate / describe / capture / export exist. What was asked is kept in
    ``created``, ``annotated``, ``described``, ``captured`` and ``exported``; ``drawings`` holds each sheet's items (a
    stand-in for the CAD: an item is drawn unless its type is in ``refuse``, which answers like an item that cannot be
    drawn against the part); ``fail_with`` (a message) makes the next create fail like a view that never finished
    computing."""

    def __init__(self) -> None:
        super().__init__()
        self.created: list[Any] = []
        self.annotated: list[Any] = []
        self.described: list[Any] = []
        self.captured: list[Any] = []
        self.exported: list[Any] = []
        self.fail_with: str | None = None
        self.drawings: dict[str, dict[str, Any]] = {}
        self.refuse: set[str] = set()
        self.findings: tuple[Any, ...] = ()  # what a review of any sheet finds
        self.questions: tuple[Any, ...] = ()  # the questions of judgement of any sheet
        self.facts: dict[str, int] = {}  # what the review counts for the table of requirements (empty: none)
        self.on_add: Any = None  # called with the items an add stored: a test makes the sheet carry what they print
        self.pdf: bytes | None = None  # the bytes the next PDF export writes (default: a sound one-page PDF)
        self.crops: list[tuple[str, tuple[int, ...]]] = []  # the crops a whole-sheet capture with crops=True returns: (label, findings)
        self.output: dict[str, Any] = {}  # fields of the capture / export result (views, native, unrendered...)
        self.rebuilt: dict[str, tuple[str, ...]] | None = None  # kept / unresolved / discarded of the next create
        self.layout_report: dict[str, Any] = {}  # what the next layout call reports (layout_placed...)
        self.layout_fails: str | None = None  # the next layout call fails with this message
        self.weldment: Any = None  # what a review of a welded part says of its bodies and joints (``DrawingWeldPlan``)
        self.sheet_views: tuple[str, ...] = ()  # the views the described sheet says it has
        self.collides: set[str] = set()  # item ids whose edit the CAD refuses: the new place lies on another object
        self.measures: tuple[Any, ...] = ()  # what the dimensions of the sheet state (``DrawingMeasure``), read by a review
        self.step_rows: tuple[Any, ...] = ()  # the findings of the step's own rules (``page_state``): what an add stands on
        self.composes = 0  # how many times a call composed the sheet again (a describe or an annotate that does not give its state)

    def _need_sheet(self, sheet: str | None) -> str:
        from cotaforge.contract import AdapterError, ErrorCode, ErrorInfo

        if not self.drawings:
            message = "the document has no drawing sheet: create it first with drawing(action=create)"
            raise AdapterError(ErrorInfo(code=ErrorCode.invalid_request, message=message, recoverable=True))
        if sheet is not None and sheet not in self.drawings:
            message = f"the document has no drawing sheet called {sheet!r}; its sheets are: {', '.join(self.drawings)}"
            raise AdapterError(ErrorInfo(code=ErrorCode.invalid_request, message=message, recoverable=True))
        return sheet or next(iter(self.drawings))

    def _drawing_result(
        self,
        name: str,
        warnings: tuple[str, ...] = (),
        removed: bool = False,
        refused: tuple[Any, ...] = (),
        changed: tuple[str, ...] = (),
        rebuilt: dict[str, tuple[str, ...]] | None = None,
        state: bool = False,
    ) -> Any:
        from cotaforge.contract import DrawingItemResult, DrawingResult

        sheet = self.drawings.get(name, {"template": "A4", "language": "es", "scale": 0.5, "items": []})
        items = tuple(
            DrawingItemResult(id=i["id"], type=i["type"], status="ok", detail=f"{i['type']} drawn")
            for i in sheet["items"]
        )
        return DrawingResult(
            sheet=name,
            template=sheet["template"],
            language=sheet["language"],
            page_mm=(297.0, 210.0) if sheet["template"] == "A4" else (420.0, 297.0),
            scale=sheet["scale"],
            views=("front", "top", "right", "isometric"),
            dimensions=("width 60 mm", "height 20 mm", "depth 40 mm"),
            sheets=tuple(self.drawings),
            items=items + refused,
            removed=removed,
            waited_s=0.25,
            warnings=warnings,
            changed=changed,
            window="1 new" if changed else "",
            page_state=self._info(name, True, self.step_rows) if state and name in self.drawings else None,
            **(rebuilt or {}),
        )

    def create_drawing(self, request: Any) -> Any:
        from cotaforge.contract import AdapterError, ErrorCode, ErrorInfo

        if self.fail_with is not None:
            raise AdapterError(ErrorInfo(code=ErrorCode.timeout, message=self.fail_with, recoverable=True))
        self.created.append(request)
        name = request.sheet or next(iter(self.drawings), "Drawing")
        composed = self.drawings.get(name, {}).get("composed")  # a rebuild keeps what the sheet recorded of its compose
        self.drawings[name] = {
            **({"composed": composed} if composed else {}),
            "template": request.template,
            "language": request.language,
            "scale": request.scale or 0.5,
            "items": [{"id": "o1", "type": "overall"}],
            "next": 2,
            "edits": 0,  # counts the changes of the sheet, like the CAD's: a report or a request belongs to one value
            "reports": {},
            "report_n": 0,
            "request": None,
            "choice": None,
        }
        return self._drawing_result(name, ("a note",) if request.scale == 7.0 else (), rebuilt=self.rebuilt, state=True)

    def annotate_drawing(self, request: Any) -> Any:
        from cotaforge.contract import (
            AdapterError,
            DrawingAnswer,
            DrawingItemResult,
            DrawingRemark,
            ErrorCode,
            ErrorInfo,
        )

        self.annotated.append(request)
        name = self._need_sheet(request.sheet)
        sheet = self.drawings[name]
        if request.action == "review_report":
            held = sheet["reports"].get(request.by) if request.append else None  # append: the pieces of one report
            kept = tuple(held["remarks"]) if held else ()
            if held:
                n = held["n"]
            else:
                sheet["report_n"] += 1
                n = sheet["report_n"]
            prefix = {"fabricator": "fab", "engineer": "eng"}[request.by]
            remarks = tuple(
                DrawingRemark(
                    id=f"{prefix}{n}.{i}",
                    kind=r.get("kind", "other"),
                    where=r.get("where", ""),
                    text=r["text"],
                    fix=r.get("fix", ""),
                )
                for i, r in enumerate(request.remarks, start=len(kept) + 1)
            )
            sheet["reports"][request.by] = {"n": n, "at": request.at, "edits": sheet["edits"], "remarks": (*kept, *remarks)}
            return self._drawing_result(name, changed=tuple(r.id for r in remarks))
        if request.action == "request_review":
            sheet["request"] = {"at": request.at}
            return self._drawing_result(name)
        if request.action == "review_choice":
            sheet["choice"] = {"who": request.by}
            return self._drawing_result(name)
        if request.action == "answer":
            known = {f.id or f.code for f in self.findings} | {q.id for q in self.questions}
            known |= {r.id for rep in sheet["reports"].values() for r in rep["remarks"]}
            if not request.memory and request.id not in known and not request.id.startswith(("req.", "plan.")):
                message = f"sheet {name} has no finding or question {request.id!r} now"
                raise AdapterError(ErrorInfo(code=ErrorCode.invalid_request, message=message, recoverable=True))
            sheet.setdefault("answers", {})[request.id] = DrawingAnswer(
                verdict=request.verdict, text=request.text, ordered_by=request.by or ""
            )
            return self._drawing_result(name, changed=(request.id,))
        if request.action == "approve":
            sheet["approved_by"] = request.by
            return self._drawing_result(name)
        if request.action == "layout":
            if self.layout_fails is not None:
                info = ErrorInfo(code=ErrorCode.invalid_request, message=self.layout_fails, recoverable=True)
                raise AdapterError(info)
            return self._drawing_result(name, rebuilt=self.layout_report or None, state=True)
        if request.action == "record":  # what the order that composed the sheet decided; the pins of the hand stay
            pinned = sheet.get("composed", {}).get("pinned")
            sheet["composed"] = {**request.composed, **({"pinned": pinned} if pinned else {})}
            return self._drawing_result(name)
        if request.action == "edit":  # one item changed in place, and pinned; a collision (``collides``) refuses it
            known = {i["id"]: i for i in sheet["items"]}
            missing = [p["id"] for p in request.items if p["id"] not in known]
            if missing:
                message = f"no item {', '.join(missing)} on sheet {name}; it has: {', '.join(sorted(known))}"
                raise AdapterError(ErrorInfo(code=ErrorCode.invalid_request, message=message, recoverable=True))
            for patch in request.items:
                if patch["id"] in self.collides:
                    message = f"the edit would put {patch['id']} on d9 (text over text); nothing was changed"
                    raise AdapterError(ErrorInfo(code=ErrorCode.invalid_request, message=message, recoverable=True))
            for patch in request.items:
                item = known[patch["id"]]
                for key, value in patch.items():
                    if key in ("id", "type"):
                        continue
                    if value is None:
                        item.pop(key, None)
                    else:
                        item[key] = value
            pinned = {*sheet.get("composed", {}).get("pinned", ()), *(p["id"] for p in request.items)}
            sheet["composed"] = {**sheet.get("composed", {}), "pinned": sorted(pinned)}
            sheet["approved_by"] = ""
            sheet["edits"] += 1
            return self._drawing_result(name, changed=tuple(p["id"] for p in request.items), state=True)
        sheet["approved_by"] = ""  # any change of the sheet empties "Aprobó" and makes the reports old
        sheet["edits"] += 1
        if request.action == "update":
            return self._drawing_result(name, state=True)
        if request.action == "remove_sheet":
            del self.drawings[name]
            return self._drawing_result(name, removed=True)
        if request.action == "remove":
            known = {i["id"] for i in sheet["items"]}
            missing = [i for i in request.ids if i not in known]
            if missing:
                message = f"no item {', '.join(missing)} on sheet {name}; it has: {', '.join(sorted(known))}"
                raise AdapterError(ErrorInfo(code=ErrorCode.invalid_request, message=message, recoverable=True))
            sheet["items"] = [i for i in sheet["items"] if i["id"] not in request.ids]
            return self._drawing_result(name, changed=tuple(request.ids), state=True)
        refused = []
        added = []
        for item in request.items:
            if item["type"] in self.refuse:
                refused.append(
                    DrawingItemResult(
                        id=item.get("id", "x"), type=item["type"], status="refused", detail="not stored: no"
                    )
                )
                continue
            sheet["items"].append({**item, "id": item.get("id") or f"{item['type'][0]}{sheet['next']}"})
            added.append(sheet["items"][-1]["id"])
            sheet["next"] += 1
        if self.on_add is not None:
            self.on_add(self, [i for i in sheet["items"] if i["id"] in added])
        return self._drawing_result(name, refused=tuple(refused), changed=tuple(added), state=True)

    def _info(self, n: str, review: bool, findings: tuple[Any, ...] | None = None) -> Any:
        """One sheet as a describe states it (``findings``: those it lists with a review, default every finding of the fake)."""
        from cotaforge.contract import (
            DrawingItemInfo,
            DrawingQuestion,
            DrawingReport,
            DrawingReviewRequest,
            DrawingSheetInfo,
        )

        sheet = self.drawings[n]
        answered = sheet.get("answers", {})
        listed = self.findings if findings is None else findings
        r = sheet["request"]
        c = sheet["choice"]
        return DrawingSheetInfo(
            sheet=n,
            template=sheet["template"],
            language=sheet["language"],
            page_mm=(297.0, 210.0),
            scale=sheet["scale"],
            fields={"title": "plate"},
            findings=tuple(f.model_copy(update={"answer": answered.get(f.id or f.code)}) for f in listed) if review else (),
            questions=tuple(DrawingQuestion(id=q.id, text=q.text, answer=answered.get(q.id)) for q in self.questions) if review else (),
            answers=answered,
            facts=dict(self.facts) if review else {},
            measures=tuple(self.measures) if review else (),
            weldment=self.weldment if review else None,
            views=self.sheet_views,
            approved_by=sheet.get("approved_by", ""),
            edits=sheet["edits"],
            reports={
                who: DrawingReport(n=rep["n"], by=who, at=rep["at"], remarks=rep["remarks"], edit=rep["edits"])
                for who, rep in sheet["reports"].items()
            },
            review_request=None if r is None else DrawingReviewRequest(at=r["at"]),
            review_choice=c["who"] if c is not None else "",
            composed=dict(sheet.get("composed", {})),
            printed=("Fabrication notes", "19 de espesor"),
            items=tuple(
                DrawingItemInfo(id=i["id"], type=i["type"], status="ok", detail="drawn", spec=i) for i in sheet["items"]
            ),
        )

    def describe_drawing(self, request: Any) -> Any:
        from cotaforge.contract import DescribeDrawingResult

        self.described.append(request)
        names = ([self._need_sheet(request.sheet)] if request.sheet else list(self.drawings)) if self.drawings else []
        if request.sheet is None and self.drawings:
            self._need_sheet(None)
        self.composes += len(names)  # a describe composes the sheet, as the CAD's does
        return DescribeDrawingResult(sheets=tuple(self._info(n, bool(request.review)) for n in names))

    def capture_drawing(self, request: Any) -> Any:
        from cotaforge.contract import CaptureDrawingResult, SheetCrop

        self.captured.append(request)
        Path(request.path).write_bytes(PNG)
        name = request.sheet or next(iter(self.drawings), "")
        zoomed: list[Any] = []
        edits = self.drawings.get(name, {}).get("edits", 0)
        if request.crops and request.region is None and request.crops_given_at != edits:
            for n, (label, held) in enumerate(self.crops, start=1):
                target = Path(request.path).with_name(f"{Path(request.path).stem}-crop{n}.png")
                target.write_bytes(PNG)
                zoomed.append(
                    SheetCrop(label=label, view="top", region=(0, 0, 50, 40), path=str(target), size_px=(1, 1), px_per_mm=10.0, findings=held)
                )
        return CaptureDrawingResult(
            size_px=(1, 1),
            region=request.region,
            sheet=name,
            edits=edits,
            zoomed=tuple(zoomed),
            **self.output,
        )

    def export_drawing(self, request: Any) -> Any:
        from cotaforge.contract import ExportDrawingResult

        self.exported.append(request)
        count = len(self.drawings) if request.format == "dxf" and request.sheet is None else 1
        first = Path(request.path)
        paths = [first, *[first.with_name(f"{first.stem}-{n}{first.suffix}") for n in range(2, count + 1)]]
        for path in paths:
            path.write_bytes((self.pdf or minimal_pdf()) if request.format == "pdf" else b"0\nSECTION\n")
        return ExportDrawingResult(paths=tuple(str(p) for p in paths), page_mm=(297.0, 210.0), **self.output)


class NativeFake(FakeAdapter):
    """The fake with a stand-in for ``run_native``: ``"box <side>"`` makes a box (one history entry, like a native
    step that creates a body), ``"noop"`` changes nothing, anything else fails and is rolled back."""

    def run_native(self, request: Any) -> Any:
        from cotaforge.contract import BoxRequest, NativeError, NativeObject, RunNativeResult

        code = request.code.strip()
        if code == "noop":
            return RunNativeResult(ok=True)
        if code.startswith("set-material "):  # changes the document metadata only: no history entry
            self._doc(request).material = code.removeprefix("set-material ").strip()
            return RunNativeResult(ok=True)
        if code.startswith("box-and-material "):  # a box AND a new material, like a fragment that re-labels a body
            side, material = code.removeprefix("box-and-material ").split(" ", 1)
            self._doc(request).material = material.strip()
            code = f"box {side}"
        if not code.startswith("box "):
            error = NativeError(type="RuntimeError", message="boom", line=1, hint="no such thing")
            return RunNativeResult(ok=False, error=error, rolled_back=True, stdout="before\n")
        side = float(code.split()[1])
        made = self.box(BoxRequest(operation_id=request.operation_id, size=(side, side, side)))
        return RunNativeResult(
            ok=True,
            feature=made.feature,
            created=(NativeObject(name="Box", label="Box", type_id="Part::Box"),),
            volume_delta_mm3=side**3,
            stdout="hi\n",
            warnings=() if request.intent else ("No intent given",),
        )

    def native_environment(self) -> tuple[str, ...]:
        return ("  Preloaded: doc, App",)


class ExportFake(NativeFake):
    """The fake that can export a STEP file (a stub file: only its existence matters) and run native stand-ins."""

    def export_step(self, request: Any) -> Any:
        from cotaforge.contract import Ack

        Path(request.path).write_text("ISO-10303-21; stub\n", encoding="utf-8")
        return Ack()

    def export_dxf(self, request: Any) -> Any:
        from cotaforge.contract import Ack

        self.__dict__.setdefault("dxf_requests", []).append(request)
        source = request.face.id if request.face is not None else request.flat_pattern.id
        Path(request.path).write_text(f"0\nSECTION\n999\nstub for {source}\n", encoding="utf-8")
        return Ack()


class LossyFake(FakeAdapter):
    """A CAD whose file does not keep the design attributes (the session record has to put them back)."""

    def open_document(self, request: Any) -> Any:
        opened = super().open_document(request)
        doc = self._doc(request)
        doc.material, doc.route = None, ()
        return opened


def set_review(settings: Settings, mode: str, wait_min: int | None = None) -> None:
    """The project's ``drawing_review`` (brief.yaml of the design space): who reviews a drawing before it is exported.
    A test of something else than the gate sets ``ninguno`` so an export is not held by the review."""
    from cotaforge_mcp import design_memory

    design_memory.save_brief(settings, None, None, mode, wait_min, "test setting")


def set_flow(settings: Settings, flow: str) -> None:
    """The project's ``drawing_flow`` (brief.yaml): ``steps`` for a test of the small-steps flow (the default builds the
    first proposal of a sheet in one order and fixes it locally)."""
    from cotaforge_mcp import design_memory

    design_memory.save_brief(settings, None, None, None, None, None, None, flow)


def declare(settings: Settings, *entries: dict[str, Any], scope: str = "project") -> None:
    """Write memory entries (plain dicts validated by the real model) into a scope of the design's memory."""
    from cotaforge.memory import MemoryEntry, Scope

    from cotaforge_mcp import rules

    store = rules.memory_store(settings)
    directory = store.directory(Scope(scope))
    assert directory is not None
    directory.mkdir(parents=True, exist_ok=True)
    for data in entries:
        store.save(MemoryEntry.model_validate(data), Scope(scope))


def material_entry(entry_id: str = "acero-1045", name: str = "Acero 1045", *designations: str) -> dict[str, Any]:
    return {
        "id": entry_id,
        "type": "material",
        "title": name,
        "applies_when": "Ejes y pasadores",
        "not_when": "Ambientes corrosivos",
        "material": {"name": name, "designations": list(designations or ("C45", "AISI 1045"))},
    }


def process_entry(entry_id: str = "torno-a", max_diameter: float = 200.0, tag: str = "turning") -> dict[str, Any]:
    return {
        "id": entry_id,
        "type": "process",
        "title": f"Torno {entry_id}",
        "applies_when": "Piezas de revolución",
        "not_when": "Piezas prismáticas",
        "provenance": {"source": "shop_measurement", "reference": "Tabla 2026-03", "by": "Ana", "date": "2026-10-11"},
        "process": {"tag": tag, "capabilities": [{"name": "max_diameter_mm", "value": max_diameter}]},
    }


class BareFake(FakeAdapter):
    """Only the required tools: no optional capability at all."""

    def capabilities(self) -> Capabilities:
        return Capabilities(tools=frozenset(n for n, spec in TOOL_REGISTRY.items() if not spec.optional))


def fake_without(*tools: str) -> FakeAdapter:
    class Reduced(FakeAdapter):
        def capabilities(self) -> Capabilities:
            return Capabilities(tools=super().capabilities().tools - set(tools))

    return Reduced()


SESSION_ID = "unit-session"
REJECTED = "The user doesn't want to proceed with this tool use. The tool use was rejected"
"""What Claude Code tells the agent when the person rejects a permission dialog."""
ACCEPTS_UNASKED = frozenset({"brief", "review_choice"})
"""The orders the engineer of a test accepts whatever their words: facts of the project he states (its sector, its
processes, who reviews). An approval, a dismissal, a report or a decision he accepts only with the words the test made
him say (``Engineer.says``)."""


class Engineer:
    """The person at Claude Code, as far as the harness sees him (measured on Claude Code 2.1.325): what he types goes
    into the session's transcript as a ``human`` turn; before every tool call Claude Code runs the ``PreToolUse`` hook
    that ``cotaforge init`` wrote, and when the hook answers ``ask`` it shows him its permission dialog, which only he
    answers; accepted, the call reaches the server with the id of the call in ``_meta``; rejected or denied, it never
    does. The hook is the real one (``engineer_orders.decide``). He accepts a dialog that shows words he said."""

    def __init__(self, home: Path, workspace: Path, session_id: str = SESSION_ID) -> None:
        self.home, self.workspace, self.session_id = home, workspace, session_id
        self.transcript = home / "claude-code" / f"{session_id}.jsonl"
        self.mode = "default"
        self.present = True
        """He stays at the screen for the whole test (however long the CAD takes) until he ``leaves``."""
        self.agreed: list[str] = []
        self.dialogs: list[str] = []
        """Every dialog Claude Code showed him, in order."""
        self.denied: list[str] = []
        """Every call the hook denied without asking (he was away, or the mode asks nobody), with its reason."""
        self.types("hola")  # he is at the screen

    def types(self, text: str, minutes_ago: float = 0.0, origin: str = "human") -> str:
        """He types ``text`` (``origin``: another source of the turn, for what is NOT his)."""
        self.transcript.parent.mkdir(parents=True, exist_ok=True)
        at = datetime.fromtimestamp(time.time() - minutes_ago * 60, tz=UTC).isoformat().replace("+00:00", "Z")
        entry = {
            "type": "user",
            "promptId": str(uuid.uuid4()),
            "timestamp": at,
            "turnOrigin": origin,
            "message": {"role": "user", "content": text},
        }
        with self.transcript.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return text

    def says(self, text: str) -> str:
        """He types ``text`` and will accept the dialog that shows it. Returns the text, to carry it in the order."""
        self.agreed.append(text)
        return self.types(text)

    def leaves(self, minutes: float = 60.0) -> None:
        """He went away: his last message is ``minutes`` old."""
        self.present = False
        self.transcript.unlink(missing_ok=True)
        self.types("vuelvo luego", minutes_ago=minutes)

    def environ(self) -> dict[str, str]:
        return {engineer_orders.ENV_HOME: str(self.home), engineer_orders.ENV_PROJECT: str(self.workspace)}

    def payload(self, tool: str, arguments: dict[str, Any], call_id: str) -> dict[str, Any]:
        return {
            "hook_event_name": engineer_orders.PRE_TOOL_EVENT,
            "session_id": self.session_id,
            "transcript_path": str(self.transcript),
            "cwd": str(self.workspace),
            "permission_mode": self.mode,
            "tool_name": f"{engineer_orders.TOOL_PREFIX}{tool}",
            "tool_input": arguments,
            "tool_use_id": call_id,
        }

    def hook(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Run the ``PreToolUse`` hook on ``payload`` (here in this process; the acceptance tests run the command
        ``cotaforge init`` generated)."""
        return engineer_orders.decide(payload, self.environ())

    def before(self, tool: str, arguments: dict[str, Any], call_id: str) -> str | None:
        """What happens before the call reaches the server: ``None`` when it goes on (no order in it, or he accepted
        the dialog), else what Claude Code tells the agent instead of running the tool."""
        order = engineer_orders.order_of(tool, arguments)
        if order is not None and self.present:
            self.types("sigo aquí")
        answer = self.hook(self.payload(tool, arguments, call_id))
        if answer is None:
            return None
        said = answer["hookSpecificOutput"]
        if said["permissionDecision"] == "deny":
            self.denied.append(said["permissionDecisionReason"])
            return said["permissionDecisionReason"]
        shown = said["permissionDecisionReason"]
        self.dialogs.append(shown)
        assert order is not None
        if order.kind in ACCEPTS_UNASKED:
            return None
        plain = " ".join(shown.split()).casefold()
        for phrase in self.agreed:
            if " ".join(phrase.split()).casefold() in plain:
                self.agreed.remove(phrase)
                return None
        return REJECTED

    def back(self, text: str) -> str | None:
        """He writes again: what the ``UserPromptSubmit`` side of the hook adds to the conversation (``None``: nothing)."""
        self.present = True
        self.types(text)
        payload = {"hook_event_name": engineer_orders.PROMPT_EVENT, "session_id": self.session_id, "cwd": str(self.workspace), "prompt": text}
        answer = engineer_orders.back(payload, self.environ())
        return None if answer is None else answer["hookSpecificOutput"]["additionalContext"]

    def call(self, session: Session, tool: str, arguments: dict[str, Any]) -> Reply:
        """One tool call of the agent, the way Claude Code makes it."""
        call_id = f"toolu_{uuid.uuid4().hex}"
        refusal = self.before(tool, arguments, call_id)
        if refusal is not None:
            return Reply(refusal, is_error=True)
        return call_tool(session, tool, arguments, call_id=call_id)


def engineer_of(settings: Settings) -> Engineer:
    assert settings.home is not None, "a test never uses the real data folder"
    return Engineer(settings.home, settings.workspace)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        adapter="fake",
        workspace=tmp_path,
        home=tmp_path / "home",
        base_memory=False,
    )  # never the real ~/.cotaforge


@pytest.fixture
def session(settings: Settings) -> Session:
    return make_session(settings, FakeAdapter())


@pytest.fixture
def make(settings: Settings) -> MakeSession:
    return lambda adapter: make_session(settings, adapter)


class Caller:
    """Direct tool calls with the error flag kept on the reply."""

    def __init__(self, session: Session, looks: bool = True) -> None:
        self.session = session
        self.last: Reply | None = None
        self._engineer: Engineer | None = None
        self.looks = looks
        """Look at the sheet before an export of it, like the agent does (the export waits for a capture taken after the last
        change): the tests of the export are about something else; the ones about the look itself pass looks=False."""

    def __call__(self, tool: str, **arguments: Any) -> Reply:
        if (
            self.looks
            and tool == "export"
            and arguments.get("action") in ("pdf", "dxf")
            and arguments.get("face") is None
            and arguments.get("flat") is None
            and "capture_drawing" in self.session.capabilities.tools
        ):
            call_tool(self.session, "drawing", {"action": "capture", "sheet": arguments.get("sheet")})
        self.last = self.engineer.call(self.session, tool, arguments)
        return self.last

    def says(self, text: str) -> str:
        """The engineer types ``text`` and will accept the dialog that shows it (see ``Engineer``)."""
        return self.engineer.says(text)

    @property
    def engineer(self) -> Engineer:
        """The person at this session's Claude Code (made when first needed)."""
        if self._engineer is None:
            self._engineer = engineer_of(self.session.settings)
        return self._engineer

    def ok(self, tool: str, **arguments: Any) -> str:
        reply = self(tool, **arguments)
        assert not reply.is_error, reply.text
        return reply.text

    def err(self, tool: str, **arguments: Any) -> str:
        reply = self(tool, **arguments)
        assert reply.is_error, reply.text
        return reply.text


@pytest.fixture
def call(session: Session) -> Caller:
    return Caller(session)


PLATE_SKETCH = {
    "on": {"kind": "plane", "name": "XY"},
    "entities": [{"kind": "rect", "name": "r", "center": [50, 30], "width": 100, "height": 60}],
}
TOP = {"kind": "face", "from_op": "s2", "select": {"normal": [0, 0, 1]}}


def plate(call: Caller) -> None:
    """100 x 60 x 8 plate: s1 sketch, s2 extrude."""
    call.ok("doc", action="new", name="plate")
    call.ok("op", name="sketch", params=PLATE_SKETCH)
    call.ok("op", name="extrude", params={"sketch": "s1", "depth_mm": 8})
