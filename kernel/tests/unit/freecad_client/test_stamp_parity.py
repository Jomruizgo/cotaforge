"""``worker/stamp.py`` writes exactly the ``Document.Meta`` keys ``contract.DocumentStamp.to_meta`` defines: the worker
cannot import the contract, so the two mappings are pinned to each other here."""

import importlib.util
import sys
from pathlib import Path
from typing import Any

from cotaforge.contract import DocumentStamp

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"


def _load_stamp() -> Any:
    spec = importlib.util.spec_from_file_location("worker_stamp", WORKER_DIR / "stamp.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


stamp = _load_stamp()


def _contract_stamp(sidecar: str | None) -> DocumentStamp:
    return DocumentStamp(
        design_id="demo",
        plan_id="part_a",
        plan_revision=4,
        save_seq=12,
        last_step_index=78,
        harness_version="0.4.0",
        plan_sha256="a" * 64,
        sidecar_sha256=sidecar,
    )


def test_the_worker_writes_the_contract_mapping() -> None:
    for sidecar in ("b" * 64, None):
        contract = _contract_stamp(sidecar)
        assert stamp.to_meta(contract.model_dump(mode="json")) == contract.to_meta()
        assert DocumentStamp.from_meta(stamp.to_meta(contract.model_dump(mode="json"))) == contract


def test_merging_keeps_foreign_metadata_and_replaces_the_previous_stamp() -> None:
    old = stamp.to_meta(_contract_stamp(None).model_dump(mode="json"))
    existing = {**old, "CreatedBy": "engineer", "cotaforge.stale_key": "x"}
    merged = stamp.merged(existing, _contract_stamp("c" * 64).model_dump(mode="json"))
    assert merged["CreatedBy"] == "engineer"
    assert "cotaforge.stale_key" not in merged
    assert merged["cotaforge.sidecar_sha256"] == "c" * 64
