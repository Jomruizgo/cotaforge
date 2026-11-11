"""What a crash of the CAD must not take with it: a recovery copy of the unsaved work and a journal of the drawing
review.

FreeCAD can close without warning (a heap corruption inside TechDraw ended it twice during ``run_native`` scripts), and
a document that was never saved, or saved long ago, goes with it. Two things are kept in ``recuperacion/`` of the design
space, outside the document, so a crash loses nothing:

* a RECOVERY COPY, taken before a ``run_native`` fragment that works with TechDraw (the one call that has taken the
  CAD down) when the document has unsaved changes: ``saveCopy`` of the live document plus the session record next to
  it, so opening it restores the step ids like a normal save. The engineer's file, the document's name and its
  modified flag are not touched, and an ordinary fragment pays nothing. The copy is replaced by the next one and
  removed by a clean save; when the CAD dies it is renamed ``<name>.crash-<time>`` so no later copy replaces it, and
  the reply says where it is;
* a JOURNAL of the review of the drawing (the answers, who the engineer chose, the reports): the sheet holds them, but
  the sheet is in the document, which a crash loses. Every one is also written to ``<name>.revision.jsonl`` and,
  when the file is opened again and its sheet lacks it, put back.
"""

import contextlib
import json
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from cotaforge import paths
from cotaforge.contract import (
    AdapterError,
    AnnotateDrawingRequest,
    DescribeDrawingRequest,
    DocumentStamp,
    SaveDocumentRequest,
)
from pydantic import ValidationError

from cotaforge_mcp import __version__, checkpoint
from cotaforge_mcp.config import Settings
from cotaforge_mcp.reply import ToolError

if TYPE_CHECKING:
    from cotaforge_mcp.session import Session

DIRNAME = paths.RECOVERY
SCHEMA = "cotaforge-recovery/1"
MANIFEST_SUFFIX = ".copy.json"
LEGACY_MANIFEST_SUFFIX = ".copia.json"
JOURNAL_SUFFIX = ".revision.jsonl"
CRASH_TAG = ".crash-"

# What takes TechDraw in a fragment: its module, the page and view objects, the cosmetic and format calls.
_TECHDRAW = re.compile(
    r"TechDraw|Draw(?:Page|View|Proj|Template|SVG|Hatch|Rich|Leader|Tile|Weld|Gdt)|makeCosmetic\w*|formatGeometricEdge"
    r"|Cosmetic(?:Edge|Vertex|Line)|CenterLine|GeomFormat"
)

# Windows exit codes that say what ended the process (the exception code the system reports), and POSIX signals.
_EXIT_MEANINGS = {
    0xC0000374: "a corrupted heap inside FreeCAD",
    0xC0000005: "an access violation inside FreeCAD",
    0xC00000FD: "a stack overflow inside FreeCAD",
    0xC0000409: "a stack buffer overrun inside FreeCAD",
    0xC0000094: "an integer division by zero inside FreeCAD",
    0xC000001D: "an illegal instruction",
}
_SIGNALS = {-11: "SIGSEGV (a segmentation fault inside FreeCAD)", -6: "SIGABRT (FreeCAD aborted)", -9: "SIGKILL"}


def touches_techdraw(code: str) -> list[str]:
    """The TechDraw names a fragment uses (each once, in order): empty when it does not work with TechDraw."""
    found: list[str] = []
    for match in _TECHDRAW.finditer(code):
        if match.group(0) not in found:
            found.append(match.group(0))
    return found


def exit_meaning(code: object) -> str:
    """The exit code of a CAD process that died, in words (``""`` when it says nothing)."""
    if not isinstance(code, int) or code in (0, 1):
        return ""
    if code in _SIGNALS:
        return _SIGNALS[code]
    unsigned = code & 0xFFFFFFFF
    meaning = _EXIT_MEANINGS.get(unsigned)
    return f"exit code {code} = 0x{unsigned:08X}" + (f": {meaning}" if meaning else "")


def directory(settings: Settings) -> Path:
    return paths.resolve(settings.workspace, DIRNAME)


def suffix_of(session: "Session") -> str:
    """The suffix of the recovery copy: the one of the CAD's document of the same kind as the active one."""
    executor = session.executor
    if executor is None:
        return session.adapter.document_suffix
    return session.adapter.suffix_for(executor.plan.kind)


def key_of(session: "Session") -> str:
    """What the files of this document are named: its file's name when it has one, else the name it was made with."""
    from cotaforge_mcp.session import slug  # noqa: PLC0415 - session imports this module

    return slug(session.doc_path.stem if session.doc_path is not None else session.doc_name, "design")


@dataclass(frozen=True)
class Copy:
    """A recovery copy: where it is, when it was taken, what was about to run and what it holds."""

    path: Path
    at: float
    before: str
    steps: int
    last_step: str
    document: str
    source: str | None
    crashed: bool = False

    def lines(self, engineer_file: Path | None) -> list[str]:
        """What the reply says about it: what it holds and how to go on from it."""
        taken = time.strftime("%H:%M:%S", time.localtime(self.at))
        holds = f"{self.steps} step(s)" + (f", the last {self.last_step}" if self.last_step else "")
        home = f" the engineer's file ({engineer_file.name})" if engineer_file is not None else " a file of its own"
        resume = [
            f"recovery copy: {self.path} (taken at {taken}, just before {self.before}); it holds {self.document} "
            f"with the work that was not saved: {holds}",
            "to go on from it: "
            + ('doc(action="close", discard=true) if the last save was reopened, then ' if engineer_file else "")
            + f'doc(action="open", path="{self.path}"), and '
            'doc(action="save", path="<where it belongs>", overwrite=true) '
            f"to put it in{home}",
        ]
        return resume


def _manifest(directory_: Path, stem: str) -> Path:
    return paths.resolve(directory_, f"{stem}{MANIFEST_SUFFIX}")


def _read(path: Path) -> Copy | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("schema") != SCHEMA:
            return None
        copy = path.with_name(str(raw["copy"]))
        if not copy.is_file():
            return None
        return Copy(
            path=copy,
            at=float(raw["at"]),
            before=str(raw["before"]),
            steps=int(raw["steps"]),
            last_step=str(raw.get("last_step", "")),
            document=str(raw.get("document", "")),
            source=raw.get("source"),
            crashed=bool(raw.get("crashed", False)),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


# --- the recovery copy -----------------------------------------------------------------------------------------------


def take(session: "Session", before: str) -> tuple[Copy | None, str | None]:
    """A recovery copy of the active document as it is now, for the fragment about to run (``before`` says which).
    ``(copy, None)``; ``(None, None)`` when there is nothing unsaved to protect; ``(None, why)`` when it could not
    be made (the fragment runs all the same: the copy is a courtesy, never a gate)."""
    executor = session.executor
    if executor is None or "save_document" not in session.capabilities.tools:
        return None, None
    if not session.dirty and session.doc_path is not None and session.doc_path.is_file():
        return None, None  # the last save is the copy
    folder = directory(session.settings)
    key = key_of(session)
    suffix = suffix_of(session)
    target = folder / f"{key}{suffix}"
    steps = session.steps()
    try:
        plan = executor.plan
        stamp = DocumentStamp(
            design_id=session.design_id,
            plan_id=plan.id,
            plan_revision=max(1, session.save_seq),
            save_seq=session.save_seq,
            last_step_index=len(steps),
            harness_version=__version__,
            plan_sha256=checkpoint.plan_sha256(plan),
        )
        saved = session.adapter.save_document(
            SaveDocumentRequest(operation_id="recovery", path=str(target), stamp=stamp, recovery_copy=True)
        )
        mass, bbox = session.measure(quick=True)
        record = checkpoint.Record(
            adapter=session.adapter.name,
            file_name=target.name,
            file_sha256=saved.sha256,
            design_id=session.design_id,
            save_seq=session.save_seq,
            adapter_state=saved.adapter_state,
            executor=checkpoint.dump_executor(executor),
            facts={"volume_mm3": mass.volume_mm3, "bbox_min": list(bbox.min), "bbox_max": list(bbox.max)},
            attributes=session.design_attributes().model_dump(mode="json"),
            parts={},
            purpose=session.purpose,
            memory=session.mem.to_json(),
        )
        checkpoint.write(checkpoint.sidecar_path(target), record)
        at = time.time()
        _manifest(folder, key).write_text(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "copy": target.name,
                    "at": at,
                    "before": before,
                    "steps": len(steps),
                    "last_step": f"{steps[-1].id} {steps[-1].op}" if steps else "",
                    "document": session.doc_name,
                    "source": str(session.doc_path) if session.doc_path is not None else None,
                },
                indent=1,
            )
            + "\n",
            encoding="utf-8",
        )
    except (AdapterError, ToolError, OSError, checkpoint.CheckpointError, ValidationError) as exc:
        reason = exc.info.message if isinstance(exc, AdapterError) else getattr(exc, "message", None) or str(exc)
        return None, f"no recovery copy was made ({str(reason).splitlines()[0][:160]})"
    return Copy(
        target,
        at,
        before,
        len(steps),
        f"{steps[-1].id} {steps[-1].op}" if steps else "",
        session.doc_name,
        str(session.doc_path) if session.doc_path is not None else None,
    ), None


def _move(folder: Path, old_stem: str, new_stem: str, document_suffix: str) -> None:
    """Rename a copy, its session record and its manifest from ``old_stem`` to ``new_stem``."""
    for suffix in (document_suffix, ".cotaforge.json", MANIFEST_SUFFIX, LEGACY_MANIFEST_SUFFIX):
        source = folder / f"{old_stem}{suffix}"
        if source.is_file():
            shutil.move(str(source), str(folder / f"{new_stem}{suffix}"))


def after_crash(session: "Session") -> Copy | None:
    """The recovery copy the dead CAD left, kept: the live copy is renamed ``<key>.crash-<time>`` (so no later copy
    replaces it) when it holds more than the last save, and the newest such copy is returned; ``None`` when there is
    none. Safe to call again for the same crash."""
    folder = directory(session.settings)
    key = key_of(session)
    live = _read(_manifest(folder, key))
    saved_at = session.doc_path.stat().st_mtime if session.doc_path is not None and session.doc_path.is_file() else 0.0
    if live is not None:
        if live.at > saved_at:
            stem = f"{key}{CRASH_TAG}{time.strftime('%Y%m%d-%H%M%S', time.localtime(live.at))}"
            with contextlib.suppress(OSError):
                _move(folder, key, stem, suffix_of(session))
                path = _manifest(folder, stem)
                data = json.loads(path.read_text(encoding="utf-8"))
                data.update(copy=f"{stem}{suffix_of(session)}", crashed=True)
                path.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
        else:
            clear(session)
    kept = (
        sorted(
            [
                *folder.glob(f"{key}{CRASH_TAG}*{MANIFEST_SUFFIX}"),
                *folder.glob(f"{key}{CRASH_TAG}*{LEGACY_MANIFEST_SUFFIX}"),
            ]
        )
        if folder.is_dir()
        else []
    )
    for path in reversed(kept):
        copy = _read(path)
        if copy is not None and copy.at > saved_at:
            return copy
    return None


def clear(session: "Session") -> None:
    """A clean save makes the live recovery copy unnecessary."""
    folder = directory(session.settings)
    key = key_of(session)
    for suffix in (suffix_of(session), ".cotaforge.json", MANIFEST_SUFFIX, LEGACY_MANIFEST_SUFFIX):
        with contextlib.suppress(OSError):
            (folder / f"{key}{suffix}").unlink()


def rekey(session: "Session", old_key: str, new_key: str) -> None:
    """A document saved for the first time under a file of its own: what was kept under its name follows the file."""
    if old_key == new_key:
        return
    folder = directory(session.settings)
    old, new = folder / f"{old_key}{JOURNAL_SUFFIX}", folder / f"{new_key}{JOURNAL_SUFFIX}"
    if old.is_file():
        with contextlib.suppress(OSError):
            with new.open("a", encoding="utf-8") as out:
                out.write(old.read_text(encoding="utf-8"))
            old.unlink()


# --- the journal of the drawing review -------------------------------------------------------------------------


def note(session: "Session", request: AnnotateDrawingRequest) -> None:
    """One review action (an answer, the engineer's choice, a report) kept outside the document; never fails."""
    folder = directory(session.settings)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"at": time.time(), "request": request.model_dump(mode="json")}, ensure_ascii=False)
        with (folder / f"{key_of(session)}{JOURNAL_SUFFIX}").open("a", encoding="utf-8") as out:
            out.write(line + "\n")
    except OSError:
        pass


def _journal(session: "Session") -> list[AnnotateDrawingRequest]:
    path = directory(session.settings) / f"{key_of(session)}{JOURNAL_SUFFIX}"
    if not path.is_file():
        return []
    found: list[AnnotateDrawingRequest] = []
    for text in path.read_text(encoding="utf-8", errors="replace").splitlines():
        with contextlib.suppress(ValueError, ValidationError, KeyError, TypeError):
            found.append(AnnotateDrawingRequest.model_validate(json.loads(text)["request"]))
    return found


def restore_review(session: "Session") -> list[str]:
    """Put back into the sheets of the document what the journal holds and they lack (the answers, the choice of
    reviewer, the reports, the request): what a crash took with the unsaved sheet. Lines for the reply; none when
    nothing was missing."""
    if "describe_drawing" not in session.capabilities.tools or "annotate_drawing" not in session.capabilities.tools:
        return []
    requests = _journal(session)
    if not requests:
        return []
    try:
        sheets = session.adapter.describe_drawing(DescribeDrawingRequest(operation_id="recovery", review=False)).sheets
    except AdapterError:
        return []  # the document has no sheet (yet): the journal waits
    if not sheets:
        return []
    only = sheets[0].sheet if len(sheets) == 1 else None
    given: dict[str, list[str]] = {}
    for info in sheets:
        mine = [r for r in requests if (r.sheet or only) == info.sheet]
        latest_answer = {r.id: r for r in mine if r.action == "answer" and r.id}
        todo: list[AnnotateDrawingRequest] = []
        asked = [r for r in mine if r.action == "request_review"]
        if asked and info.review_request is None:
            todo.append(asked[0])
        chosen = [r for r in mine if r.action == "review_choice"]
        if chosen and not info.review_choice:
            todo.append(chosen[-1])
        for who in ("fabricator", "engineer"):
            reports = [r for r in mine if r.action == "review_report" and r.by == who]
            held = info.reports.get(who)
            if reports and (held is None or (reports[-1].at or 0.0) > held.at):
                todo.append(reports[-1])
        todo.extend(r for key, r in latest_answer.items() if key not in info.answers)
        for request in todo:
            try:
                session.adapter.annotate_drawing(request.model_copy(update={"sheet": info.sheet}))
            except AdapterError:
                continue
            given.setdefault(info.sheet, []).append(request.action)
    if not given:
        return []
    session.dirty = True  # they are in the sheet again, which is not saved yet
    words = {
        "request_review": "review request",
        "review_choice": "reviewer choice",
        "review_report": "report",
        "answer": "answer",
    }
    lines = []
    for sheet, actions in given.items():
        parts = []
        for action, word in words.items():
            count = actions.count(action)
            if count:
                parts.append(
                    f"{count} {word}{'s' if count > 1 else ''}" if action in ("answer", "review_report") else word
                )
        lines.append(
            f"sheet {sheet}: put back from the review journal (the unsaved document lost them): {', '.join(parts)}"
        )
    journal = directory(session.settings) / f"{key_of(session)}{JOURNAL_SUFFIX}"
    lines.append(f"doc(action=save) keeps them in the document; the journal stays in {journal}")
    return lines
