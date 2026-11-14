"""The op vocabulary the agent sees is generated from the IR models and covers ALL of them.

Adding an operation to the kernel without a purpose line, a minimal example that validates and a commercial CAD term makes
these tests fail: the guide is the only place the agent learns the vocabulary from."""

import re

import pytest
from cotaforge.ir import OP_NAMES, IntentLink

from cotaforge_mcp import catalog
from cotaforge_mcp.guide import guide
from cotaforge_mcp.session import Session
from cotaforge_mcp.terms import TERMS, terms_of, terms_table

# Commercial CAD software words that must lead to the operation (Spanish and English), where commercial CAD software has the concept.
COMMERCIAL_CAD_WORDS: dict[str, tuple[str, ...]] = {
    "sketch": ("croquis", "sketch"),
    "extrude": ("extruir saliente", "boss extrude"),
    "cut": ("extruir corte", "cut-extrude"),
    "revolve": ("saliente por revolución",),
    "sweep": ("barrido saliente", "swept boss"),
    "ref_plane": ("plano de referencia", "reference plane"),
    "ref_axis": ("eje de referencia", "reference axis"),
    "hole": ("asistente para taladro", "hole wizard"),
    "fillet": ("redondeo", "fillet"),
    "chamfer": ("chaflán", "chamfer"),
    "linear_pattern": ("matriz lineal", "linear pattern"),
    "circular_pattern": ("matriz circular", "circular pattern"),
    "shell": ("vaciado", "shell"),
    "draft": ("extracción", "draft"),
    "mirror": ("simetría", "mirror"),
    "mirror_body": ("simetría de cuerpo", "mirror bodies"),
    "split": ("dividir", "split"),
    "copy_body": ("copiar cuerpo", "mover/copiar cuerpos"),
    "boolean_union": ("combinar", "union"),
    "boolean_subtract": ("sustraer", "subtract"),
    "boolean_intersect": ("intersección", "common"),
    "translate": ("mover cuerpo", "move body"),
    "rotate": ("girar cuerpo", "rotate body"),
    "insert_component": ("insertar componentes", "insert components"),
    "fix_component": ("fijo", "fix component"),
    "place_component": ("mover componente", "move component"),
    "add_joint": ("junta", "joint"),
    "set_joint": ("mover junta", "set joint"),
    "mate_concentric": ("relación concéntrica", "concentric mate"),
    "mate_coincident": ("relación coincidente", "coincident mate"),
    "mate_distance": ("relación de distancia", "distance mate"),
    "check_interference": ("detección de interferencia", "interference detection"),
    "base_flange": ("brida base", "base flange"),
    "edge_flange": ("brida de arista", "labio", "edge flange"),
    "unfold": ("desplegar", "patrón plano", "flat pattern"),
}

FULL_VOCABULARY = {
    "box", "cylinder", "boolean_union", "boolean_subtract", "boolean_intersect", "translate", "rotate", "copy_body",
    "sketch", "ref_plane", "ref_axis", "extrude", "revolve", "sweep", "cut", "hole", "linear_pattern",
    "circular_pattern", "chamfer", "fillet", "shell", "draft", "mirror", "mirror_body", "split", "insert_component",
    "fix_component", "place_component", "shrink_body", "mould_split", "mate_concentric", "mate_coincident", "mate_distance", "check_interference",
    "add_joint", "set_joint", "base_flange", "edge_flange", "unfold",
}  # fmt: skip


def test_the_ir_has_the_full_vocabulary() -> None:
    assert set(OP_NAMES) == FULL_VOCABULARY
    assert set(catalog.op_models()) == FULL_VOCABULARY
    assert set(catalog.adapter_method(n) is not None and n for n in FULL_VOCABULARY) == FULL_VOCABULARY


def test_every_operation_has_a_purpose_and_an_example_and_nothing_extra() -> None:
    assert set(catalog.PURPOSE) == set(OP_NAMES)
    assert set(catalog.EXAMPLES) == set(OP_NAMES)
    assert all(catalog.purpose(name) for name in OP_NAMES)


@pytest.mark.parametrize("name", sorted(OP_NAMES))
def test_every_operation_has_a_reference_and_a_valid_example(session: Session, name: str) -> None:
    reference = catalog.op_reference(name)
    assert reference.startswith(f"{name}:") and "params:" in reference and f'op(name="{name}"' in reference
    assert reference.endswith(catalog.example_call(name))
    assert catalog.op_reference(name) in guide(session, f"op:{name}")  # the guide serves it (flagged if unsupported)
    data = {**catalog.EXAMPLES[name], "op": name, "id": "x1", "intent": "example"}
    catalog.OPERATION_ADAPTER.validate_python(data)  # the example must parse as the real IR operation


@pytest.mark.parametrize("name", sorted(OP_NAMES))
def test_every_operation_has_a_commercial_cad_term(name: str) -> None:
    assert terms_of(name), f"{name} has no commercial CAD term in terms.TERMS"


def test_the_commercial_cad_words_map_to_their_operation() -> None:
    table = terms_table(list(OP_NAMES))  # an adapter that lacks an op shows its term as unavailable instead
    lines = table.splitlines()[1:]
    for op, words in COMMERCIAL_CAD_WORDS.items():
        keys = [key.lower() for term in terms_of(op) for key in term.keys]
        for word in words:
            assert word in keys, f"{word!r} should map to {op}"
        assert any(f"-> op {op} " in line for line in lines), op
    assert all(t.op is None or t.op in OP_NAMES for t in TERMS)


def test_the_listing_of_ops_names_all_of_them(session: Session) -> None:
    listing = guide(session, "ops")
    available = catalog.available_ops(session.capabilities)
    for name in available:
        assert re.search(rf"^{name}\(", listing, re.MULTILINE), name


def test_reference_follows_the_model_fields(session: Session) -> None:
    text = guide(session, "op:extrude")
    for field in catalog.op_models()["extrude"].model_fields:
        if field not in catalog.HIDDEN_FIELDS:
            assert f"  {field}:" in text
    assert "step-id" in text and ">0" in text
    hole = guide(session, "op:hole")
    assert "FaceRef{" in hole and "FaceSelector{" in hole


def test_kernel_concepts_that_mean_nothing_to_the_agent_are_hidden(session: Session) -> None:
    banned = ("IntentLink", "LinkKind", "ValidationStamp", "validated", "template", "links", "brief", "calc")
    for name in OP_NAMES:
        reference = guide(session, f"op:{name}")
        usage = catalog.op_usage(name)
        for word in banned:
            assert word not in reference, f"{name}: {word!r} leaks into the reference"
            assert word not in usage, f"{name}: {word!r} leaks into the usage"
    assert "links" in catalog.HIDDEN_FIELDS and IntentLink is not None
    assert "intent" in guide(session, "op:box")  # intent is shown as plain free text
