"""The document stamp as ``Document.Meta`` keys. Pure: stdlib only, so the unit
suite checks it against ``cotaforge.contract.DocumentStamp.to_meta`` (the worker cannot import the contract).

``Meta`` creates no object in the tree: ``feature_tree`` and the manual-edit diff never see it, nothing recomputes and
the engineer cannot delete it by accident."""

from typing import Any

META_PREFIX = "cotaforge."
SCHEMA = 1
FIELDS = (
    "design_id",
    "plan_id",
    "plan_revision",
    "save_seq",
    "last_step_index",
    "harness_version",
    "plan_sha256",
    "sidecar_sha256",
)


def to_meta(stamp: dict[str, Any]) -> dict[str, str]:
    """``cotaforge.<field>`` -> string, the same mapping as ``DocumentStamp.to_meta`` (an absent value is ``""``)."""
    meta = {f"{META_PREFIX}schema": str(SCHEMA)}
    for name in FIELDS:
        value = stamp.get(name)
        meta[f"{META_PREFIX}{name}"] = "" if value is None else str(value)
    return meta


def merged(existing: dict[str, str], stamp: dict[str, Any]) -> dict[str, str]:
    """``existing`` metadata (the CAD's own keys and anything else the engineer set) with the stamp keys replaced."""
    out = {key: value for key, value in existing.items() if not key.startswith(META_PREFIX)}
    out.update(to_meta(stamp))
    return out
