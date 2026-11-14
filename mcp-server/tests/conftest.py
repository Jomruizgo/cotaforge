"""Shared fixtures: a session over the fake adapter, a direct tool caller and fake variants with fewer capabilities."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from cotaforge.adapters.fake import FakeAdapter
from cotaforge.contract import TOOL_REGISTRY, Capabilities

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
        self.output: dict[str, Any] = {}  # fields of the capture / export result (views, native, unrendered...)
        self.rebuilt: dict[str, tuple[str, ...]] | None = None  # kept / unresolved / discarded of the next create
        self.layout_report: dict[str, Any] = {}  # what the next layout call reports (layout_placed...)
        self.layout_fails: str | None = None  # the next layout call fails with this message
        self.weldment: Any = None  # what a review of a welded part says of its bodies and joints (``DrawingWeldPlan``)
        self.sheet_views: tuple[str, ...] = ()  # the views the described sheet says it has
        self.collides: set[str] = set()  # item ids whose edit the CAD refuses: the new place lies on another object

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
            projection=sheet.get("projection", "techdraw"),
            changed=changed,
            window="1 new" if changed else "",
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
            "projection": request.projection,
            "edits": 0,  # counts the changes of the sheet, like the CAD's: a report or a request belongs to one value
            "reports": {},
            "report_n": 0,
            "request": None,
            "choice": None,
        }
        return self._drawing_result(name, ("a note",) if request.scale == 7.0 else (), rebuilt=self.rebuilt)

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
                for i, r in enumerate(request.remarks, start=1)
            )
            sheet["reports"][request.by] = {"n": n, "at": request.at, "edits": sheet["edits"], "remarks": remarks}
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
            if not request.memory and request.id not in known and not request.id.startswith("req."):
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
            return self._drawing_result(name, rebuilt=self.layout_report or None)
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
            return self._drawing_result(name, changed=tuple(p["id"] for p in request.items))
        sheet["approved_by"] = ""  # any change of the sheet empties "Aprobó" and makes the reports old
        sheet["edits"] += 1
        if request.action == "update":
            return self._drawing_result(name)
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
            return self._drawing_result(name, changed=tuple(request.ids))
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
        return self._drawing_result(name, refused=tuple(refused), changed=tuple(added))

    def describe_drawing(self, request: Any) -> Any:
        from cotaforge.contract import (
            DescribeDrawingResult,
            DrawingItemInfo,
            DrawingQuestion,
            DrawingReport,
            DrawingReviewRequest,
            DrawingSheetInfo,
        )

        self.described.append(request)
        names = ([self._need_sheet(request.sheet)] if request.sheet else list(self.drawings)) if self.drawings else []
        if request.sheet is None and self.drawings:
            self._need_sheet(None)

        def answers(n: str) -> dict[str, Any]:
            return self.drawings[n].get("answers", {})

        def reports(n: str) -> dict[str, Any]:
            sheet = self.drawings[n]
            return {
                who: DrawingReport(n=r["n"], by=who, at=r["at"], remarks=r["remarks"], edit=r["edits"])
                for who, r in sheet["reports"].items()
            }

        def asked(n: str) -> Any:
            sheet = self.drawings[n]
            r = sheet["request"]
            return None if r is None else DrawingReviewRequest(at=r["at"])

        def chosen(n: str) -> str:
            sheet = self.drawings[n]
            c = sheet["choice"]
            return c["who"] if c is not None else ""

        return DescribeDrawingResult(
            sheets=tuple(
                DrawingSheetInfo(
                    sheet=n,
                    template=self.drawings[n]["template"],
                    language=self.drawings[n]["language"],
                    page_mm=(297.0, 210.0),
                    scale=self.drawings[n]["scale"],
                    projection=self.drawings[n].get("projection", "techdraw"),
                    fields={"title": "plate"},
                    findings=tuple(
                        f.model_copy(update={"answer": answers(n).get(f.id or f.code)}) for f in self.findings
                    )
                    if request.review
                    else (),
                    questions=tuple(
                        DrawingQuestion(id=q.id, text=q.text, answer=answers(n).get(q.id)) for q in self.questions
                    )
                    if request.review
                    else (),
                    answers=answers(n),
                    facts=dict(self.facts) if request.review else {},
                    weldment=self.weldment if request.review else None,
                    views=self.sheet_views,
                    approved_by=self.drawings[n].get("approved_by", ""),
                    edits=self.drawings[n]["edits"],
                    reports=reports(n),
                    review_request=asked(n),
                    review_choice=chosen(n),
                    composed=dict(self.drawings[n].get("composed", {})),
                    printed=("Fabrication notes", "19 de espesor"),
                    items=tuple(
                        DrawingItemInfo(id=i["id"], type=i["type"], status="ok", detail="drawn", spec=i)
                        for i in self.drawings[n]["items"]
                    ),
                )
                for n in names
            )
        )

    def capture_drawing(self, request: Any) -> Any:
        from cotaforge.contract import CaptureDrawingResult

        self.captured.append(request)
        Path(request.path).write_bytes(PNG)
        return CaptureDrawingResult(size_px=(1, 1), region=request.region, **self.output)

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


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        adapter="fake", workspace=tmp_path, home=tmp_path / "home", base_memory=False
    )  # never the real ~/.cotaforge


@pytest.fixture
def session(settings: Settings) -> Session:
    return make_session(settings, FakeAdapter())


@pytest.fixture
def make(settings: Settings) -> MakeSession:
    return lambda adapter: make_session(settings, adapter)


class Caller:
    """Direct tool calls with the error flag kept on the reply."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.last: Reply | None = None

    def __call__(self, tool: str, **arguments: Any) -> Reply:
        self.last = call_tool(self.session, tool, arguments)
        return self.last

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
