"""The session record kept next to a saved file: ``<name>.cotaforge.json``.

The native file is the progress of the design; what it cannot carry is the harness' own bookkeeping, because the CAD
file is not ours to extend: the ``adapter_state`` the adapter needs to re-bind its handles (the contract says it is
stored next to the file, never inside it) and the executor's history -- plan, trace, handles by step id -- that makes
the step ids the agent knows (``s3``, ``base``) mean the same entities after a reopen.

The record is written atomically after every ``doc save`` and trusted on ``doc open`` only when the SHA-256 of the file
it names is the file's own: a file saved again by anyone else (the engineer in the CAD, another program) has moved on,
so the record is ignored and the file is adopted as a foreign one.
"""

import contextlib
import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cotaforge.contract import Adapter, FeatureHandle
from cotaforge.executor import ExecutionContext, ExecutionTrace, Executor, ExecutorState
from cotaforge.ir import Plan, parse_plan
from cotaforge.verification.verifier import Verifier
from pydantic import TypeAdapter

SCHEMA = "cotaforge-session/1"
READABLE_LIMIT_CHARS = 200_000
"""A record longer than this (compact) is written without indentation."""
SUFFIX = ".cotaforge.json"

_CONTEXT = TypeAdapter(ExecutionContext)
_FEATURES = TypeAdapter(dict[str, FeatureHandle])


class CheckpointError(ValueError):
    """The session record next to a file cannot be used (missing parts, another schema, damaged JSON)."""


def sidecar_path(file: Path) -> Path:
    return file.with_name(file.stem + SUFFIX)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def plan_sha256(plan: Plan) -> str:
    return sha256_bytes(json.dumps(plan.model_dump(mode="json"), sort_keys=True).encode("utf-8"))


# --- the executor ----------------------------------------------------------------------------------------------


def dump_executor(executor: Executor) -> dict[str, Any]:
    """Everything ``Executor.reattach`` needs, as JSON-safe data."""
    context = executor.context
    if context is None:
        msg = "the executor has not started"
        raise CheckpointError(msg)
    return {
        "plan": executor.plan.model_dump(mode="json"),
        "trace": executor.trace().model_dump(mode="json"),
        "context": _CONTEXT.dump_python(context, mode="json"),
        "cursor": executor.cursor,
        "state": executor.state.value,
        "reverted_cursor": executor.reverted_cursor,
        "edited_requests": executor.edited_requests,
        "external_features": _FEATURES.dump_python(dict(executor.external_features), mode="json"),
        "drifted_ops": sorted(executor.drifted_ops),
    }


def pin_legacy_hole_points(plan: dict[str, Any]) -> dict[str, Any]:
    """A plan saved before ``hole`` had a ``drill_point`` built flat-bottomed holes; FreeCAD's default (angled) is for
    new holes only. A stored hole without the field is pinned to ``flat``: reopening never changes its holes."""
    operations = [
        {**op, "drill_point": "flat"}
        if isinstance(op, dict) and op.get("op") == "hole" and "drill_point" not in op
        else op
        for op in plan.get("operations", ())
    ]
    return {**plan, "operations": operations}


def load_executor(data: dict[str, Any], adapter: Adapter, drifted: frozenset[str] = frozenset()) -> Executor:
    """The executor of a saved session over the REOPENED document: nothing runs (``Executor.reattach``). The handles
    of the document and of its parts are the ones of the session that saved it: the caller binds the current ones
    (``Executor.bind_documents``)."""
    try:
        external = _FEATURES.validate_python(data["external_features"])
        plan = parse_plan(pin_legacy_hole_points(data["plan"]), external_ids=set(external))
        trace = ExecutionTrace.model_validate(data["trace"])
        context = _CONTEXT.validate_python(data["context"])
        state = ExecutorState(data["state"])
        if state not in (ExecutorState.paused, ExecutorState.completed):
            state = ExecutorState.completed if data["cursor"] >= len(plan.operations) else ExecutorState.paused
        executor = Executor.reattach(
            plan,
            adapter,
            Verifier(),
            trace=trace,
            context=context,
            cursor=int(data["cursor"]),
            state=state,
            reverted_cursor=data["reverted_cursor"],
            edited_requests=data["edited_requests"],
            external_features=external,
            drifted_ops=sorted({*data.get("drifted_ops", ()), *drifted}),
        )
    except (KeyError, TypeError, ValueError) as exc:
        msg = f"the saved session cannot be restored: {type(exc).__name__}: {exc}"
        raise CheckpointError(msg) from exc
    return executor


# --- the record ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Record:
    adapter: str
    file_name: str
    file_sha256: str
    design_id: str
    save_seq: int
    adapter_state: dict[str, Any]
    executor: dict[str, Any]
    facts: dict[str, Any]
    """What was measured at the save (``volume_mm3``, ``bbox_min``, ``bbox_max``): lets a later open say how far a
    file that was edited elsewhere has drifted."""
    attributes: dict[str, Any] = field(default_factory=dict)
    """The material and the process route at the save (``material``, ``route``): the native file is where they live;
    this copy puts them back when the adapter's file came back without them."""
    parts: dict[str, str] = field(default_factory=dict)
    """An assembly: the name each part document goes by in its steps (``insert_component``'s ``part``) -> its file,
    relative to the assembly's own. Reopening the assembly finds its parts there (and opens the ones that are not open),
    and puts the names back on whatever handles the CAD gives them now."""
    purpose: str = "detail"
    """What the document is for, declared by the engineer or the agent: sketch (a concept model) or detail."""
    memory: dict[str, Any] = field(default_factory=dict)
    """The memory state of the design: the stage, the answers given at milestones and the actions seen."""

    def to_json(self) -> str:
        document = {
            "schema": SCHEMA,
            "adapter": self.adapter,
            "file": self.file_name,
            "file_sha256": self.file_sha256,
            "design_id": self.design_id,
            "save_seq": self.save_seq,
            "adapter_state": self.adapter_state,
            "executor": self.executor,
            "facts": self.facts,
            "attributes": self.attributes,
            "parts": self.parts,
            "purpose": self.purpose,
            "memory": self.memory,
        }
        compact = json.dumps(document, sort_keys=True, separators=(",", ":"))
        if len(compact) > READABLE_LIMIT_CHARS:
            return compact
        # ``indent`` makes ``json`` use its pure-Python encoder, about ten times slower: only for a record a person may
        # open (the registry of a document with thousands of faces is not meant to be read)
        return json.dumps(document, sort_keys=True, indent=1)


def write(path: Path, record: Record) -> str:
    """Write the record atomically (temporary file in the same folder, then ``os.replace``); returns its SHA-256."""
    data = (record.to_json() + "\n").encode("utf-8")
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()
    return sha256_bytes(data)


def read(path: Path) -> Record | None:
    """The record next to a file; ``None`` when there is none; ``CheckpointError`` when it is unusable."""
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("schema") != SCHEMA:
            msg = f"schema {raw.get('schema')!r} is not {SCHEMA!r}"
            raise CheckpointError(msg)
        return Record(
            adapter=str(raw["adapter"]),
            file_name=str(raw["file"]),
            file_sha256=str(raw["file_sha256"]),
            design_id=str(raw["design_id"]),
            save_seq=int(raw["save_seq"]),
            adapter_state=dict(raw["adapter_state"]),
            executor=dict(raw["executor"]),
            facts=dict(raw.get("facts", {})),
            attributes=dict(raw.get("attributes", {})),
            parts={str(k): str(v) for k, v in dict(raw.get("parts", {})).items()},
            purpose=str(raw.get("purpose", "detail")),
            memory=dict(raw.get("memory", {})),
        )
    except CheckpointError:
        raise
    except (OSError, ValueError, KeyError, TypeError) as exc:
        msg = f"{path.name} is unreadable ({type(exc).__name__}: {exc})"
        raise CheckpointError(msg) from exc
