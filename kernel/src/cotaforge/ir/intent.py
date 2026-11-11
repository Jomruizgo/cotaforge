"""Intent links (ir/intent.py).

``OperationBase.intent`` (the purpose text) already exists on every operation.
``OperationBase.links`` adds: typed pointers from an operation to *why* it exists — a brief field
(``"acceptance_criteria[2]"``, ``"interfaces[0]"``), a calc id, or a rule id. The refs are
plain strings: ``brief`` refs address brief fields by path, ``calc``/``rule`` refs are validated by
their owners, never here.

``NON_FEATURE_OP_NAMES`` names the ops that produce no feature (a sketch, a datum plane/axis, an interference check);
``produces_feature`` is the single predicate callers use to
decide which ops must carry links.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["NON_FEATURE_OP_NAMES", "IntentLink", "LinkKind", "produces_feature"]

LinkKind = Literal["brief", "calc", "rule", "requirement"]

NON_FEATURE_OP_NAMES: frozenset[str] = frozenset({"sketch", "ref_plane", "ref_axis", "check_interference"})


class IntentLink(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: LinkKind
    ref: str = Field(min_length=1)
    note: str = ""


def produces_feature(op_name: str) -> bool:
    """True when an operation of this name creates a feature of the model tree."""
    return op_name not in NON_FEATURE_OP_NAMES
