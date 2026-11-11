"""Entry model: parsing, validation rules and file round-trip."""

from typing import Any

import pytest
from pydantic import ValidationError

from cotaforge.memory import EntryError, MemoryEntry, parse_entry, render_entry
from tests.unit.memory.builders import PROVENANCE, capability, make_entry, make_material, make_process

SAMPLE = """\
---
id: torno-cnc-taller-a
type: process
title: Torno CNC del taller A
applies_when: Piezas de revolución hasta Ø200 mm
not_when: Piezas prismáticas
tags: [revolución, torneado, eje]
status: active
provenance:
  source: shop_measurement
  reference: "Tabla de herramientas 2026-03"
  by: Ana
  date: 2026-10-11
process:
  tag: turning
  capabilities:
    - {name: max_diameter_mm, value: 200, placeholder: false}
---
Texto libre: contexto, por qué, excepciones.
"""


def _entry_data(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": "some-entry",
        "type": "practice",
        "title": "T",
        "applies_when": "A",
        "not_when": "B",
    }
    data.update(overrides)
    return data


def test_design_sample_parses_and_round_trips() -> None:
    entry = parse_entry(SAMPLE, expected_id="torno-cnc-taller-a")
    assert entry.process is not None and entry.process.known_tag == "turning"
    assert entry.provenance is not None and entry.provenance.reference == "Tabla de herramientas 2026-03"
    assert entry.body == "Texto libre: contexto, por qué, excepciones."
    again = parse_entry(render_entry(entry), expected_id="torno-cnc-taller-a")
    assert again == entry


@pytest.mark.parametrize(
    "entry",
    [
        make_entry(),
        make_entry(status="inactive", body="Línea uno\n\nLínea dos: con ñ y Ø"),
        make_process(
            tag="fdm", capabilities=[capability("build_volume_mm", [300, 200, 100]), capability("materials", ["steel"])]
        ),
        make_material(),
        make_entry(type="correction", title="Use [brackets] and: colons", applies_when="When 'quoted' text appears"),
    ],
)
def test_round_trip_is_lossless(entry: MemoryEntry) -> None:
    assert parse_entry(render_entry(entry), expected_id=entry.id) == entry


def test_material_with_properties_round_trips() -> None:
    entry = make_material(
        provenance=PROVENANCE,
        material={
            "name": "Steel 1045",
            "designations": ["AISI 1045"],
            "properties": {"yield_mpa": 530, "density": 7.85, "heat_treatment": "normalized", "grades": ["a", 1]},
        },
    )
    assert parse_entry(render_entry(entry), expected_id=entry.id) == entry


def test_body_is_whatever_follows_the_frontmatter() -> None:
    text = render_entry(make_entry(body="A body with --- inside\n---\nand a rule above"))
    assert parse_entry(text).body == "A body with --- inside\n---\nand a rule above"


def test_windows_line_endings_are_accepted() -> None:
    entry = parse_entry(SAMPLE.replace("\n", "\r\n"), expected_id="torno-cnc-taller-a")
    assert entry.title == "Torno CNC del taller A"


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("no frontmatter at all", "must start with"),
        ("---\nid: x\n", "not closed"),
        ("---\n- just\n- a list\n---\n", "mapping"),
        ("---\nid: [unclosed\n---\n", "invalid YAML"),
    ],
)
def test_malformed_files_are_entry_errors(text: str, fragment: str) -> None:
    with pytest.raises(EntryError, match=fragment):
        parse_entry(text)


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden|Extra inputs"):
        MemoryEntry.model_validate(_entry_data(colour="red"))
    text = render_entry(make_entry()).replace("status: active", "status: active\nmystery: 1")
    with pytest.raises(EntryError, match="mystery"):
        parse_entry(text)


def test_unknown_field_inside_a_block_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_process(process={"tag": "turning", "capabilities": [], "extra": 1})


@pytest.mark.parametrize("missing", ["applies_when", "not_when"])
@pytest.mark.parametrize("entry_type", ["practice", "principle", "correction", "cad_trap"])
def test_applies_when_and_not_when_are_required(missing: str, entry_type: str) -> None:
    data = _entry_data(type=entry_type)
    del data[missing]
    with pytest.raises(ValidationError, match=missing):
        MemoryEntry.model_validate(data)


@pytest.mark.parametrize("missing", ["applies_when", "not_when"])
def test_applies_when_and_not_when_are_required_on_structured_types_too(missing: str) -> None:
    data = make_process().model_dump(mode="json")
    del data[missing]
    with pytest.raises(ValidationError, match=missing):
        MemoryEntry.model_validate(data)
    data = make_material().model_dump(mode="json")
    data[missing] = "   "
    with pytest.raises(ValidationError, match=missing):
        MemoryEntry.model_validate(data)


def test_numeric_value_without_provenance_is_rejected() -> None:
    with pytest.raises(ValidationError, match="provenance"):
        make_process(provenance=None)
    with pytest.raises(ValidationError, match="provenance"):
        make_process(tag="fdm", capabilities=[capability("build_volume_mm", [1, 2, 3])], provenance=None)
    with pytest.raises(ValidationError, match="provenance"):
        make_material(material={"name": "X", "properties": {"density": 7.85}})


def test_placeholder_numbers_still_need_provenance() -> None:
    with pytest.raises(ValidationError, match="provenance"):
        make_process(capabilities=[capability("max_diameter_mm", 1, placeholder=True)], provenance=None)


def test_entry_without_numbers_needs_no_provenance() -> None:
    assert make_process(capabilities=[capability("materials", ["steel"])], provenance=None).provenance is None
    assert make_process(capabilities=[], provenance=None).provenance is None
    assert make_material().provenance is None
    assert make_entry().provenance is None


def test_provenance_requires_all_four_parts() -> None:
    for missing in ("source", "reference", "by", "date"):
        broken = {key: value for key, value in PROVENANCE.items() if key != missing}
        with pytest.raises(ValidationError, match=missing):
            make_process(provenance=broken)


def test_provenance_source_is_an_enum() -> None:
    with pytest.raises(ValidationError, match="source"):
        make_process(provenance={**PROVENANCE, "source": "gut_feeling"})


@pytest.mark.parametrize("entry_type", ["practice", "principle", "correction", "cad_trap", "material"])
def test_process_block_only_on_process_type(entry_type: str) -> None:
    data = _entry_data(type=entry_type, process={"tag": "turning"})
    if entry_type == "material":
        data["material"] = {"name": "X"}
    with pytest.raises(ValidationError, match="process"):
        MemoryEntry.model_validate(data)


@pytest.mark.parametrize("entry_type", ["practice", "principle", "correction", "cad_trap", "process"])
def test_material_block_only_on_material_type(entry_type: str) -> None:
    data = _entry_data(type=entry_type, material={"name": "X"})
    if entry_type == "process":
        data["process"] = {"tag": "turning"}
    with pytest.raises(ValidationError, match="material"):
        MemoryEntry.model_validate(data)


def test_structured_types_need_their_block() -> None:
    with pytest.raises(ValidationError, match="'process' block"):
        MemoryEntry.model_validate(_entry_data(type="process"))
    with pytest.raises(ValidationError, match="'material' block"):
        MemoryEntry.model_validate(_entry_data(type="material"))


def test_material_needs_only_a_name() -> None:
    entry = make_material(material={"name": "Brass"})
    assert entry.material is not None
    assert entry.material.designations == ()
    with pytest.raises(ValidationError, match="name"):
        make_material(material={"designations": ["x"]})


@pytest.mark.parametrize("bad_id", ["Upper", "-leading", "has space", "", "a" * 65, "under_score", "ñandú"])
def test_id_must_be_a_slug(bad_id: str) -> None:
    with pytest.raises(ValidationError, match="id"):
        make_entry(bad_id)


def test_id_must_equal_the_file_stem() -> None:
    text = render_entry(make_entry("real-id"))
    assert parse_entry(text, expected_id="real-id").id == "real-id"
    with pytest.raises(EntryError, match="must equal the file name"):
        parse_entry(text, expected_id="other-name")


def test_status_defaults_to_active_and_is_an_enum() -> None:
    assert make_entry().is_active
    assert not make_entry(status="inactive").is_active
    with pytest.raises(ValidationError):
        make_entry(status="deleted")


def test_entry_is_frozen() -> None:
    with pytest.raises(ValidationError):
        make_entry().title = "other"  # type: ignore[misc]


# ---- known and free process tags -------------------------------------------------------------


def test_known_tag_rejects_an_unknown_capability_name() -> None:
    with pytest.raises(ValidationError, match="not a capability of the known process 'turning'"):
        make_process(capabilities=[capability("max_rpm", 4000)])


@pytest.mark.parametrize(
    "bad",
    [
        capability("max_diameter_mm", [1, 2, 3]),  # scalar given as a set
        capability("build_volume_mm", 300),  # set given as a scalar
        capability("build_volume_mm", [300, 200]),  # wrong set size
        capability("materials", 5),  # names given as a number
        capability("max_diameter_mm", 200, unit="inch"),  # wrong unit
        capability("max_diameter_mm", -5),  # not positive
        capability("k_factor", 1.5),  # above the plausible range
        capability("max_diameter_mm", True),  # a boolean is not a number
    ],
)
def test_known_tag_rejects_a_wrong_shape(bad: dict[str, Any]) -> None:
    tag = "fdm" if bad["name"] == "build_volume_mm" else "sheet_bending" if bad["name"] == "k_factor" else "turning"
    if bad["name"] == "materials":
        tag = "turning"
    with pytest.raises(ValidationError):
        make_process(tag=tag, capabilities=[bad])


def test_known_tag_accepts_the_declared_shapes() -> None:
    entry = make_process(
        tag="sheet_bending",
        capabilities=[
            capability("sheet_thicknesses_mm", [1, 1.5, 2], unit="mm"),
            capability("min_bend_radius_mm", 2),
            capability("k_factor", 0.33),
            capability("materials", ["steel", "aluminium"]),
        ],
    )
    assert entry.process is not None and entry.process.known_tag == "sheet_bending"


def test_known_tag_is_matched_case_insensitively() -> None:
    with pytest.raises(ValidationError, match="not a capability"):
        make_process(tag="Turning", capabilities=[capability("max_rpm", 4000)])


def test_duplicate_capability_names_are_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate"):
        make_process(capabilities=[capability("max_diameter_mm", 200), capability("max_diameter_mm", 100)])


def test_placeholder_number_skips_the_range_check_but_not_the_shape() -> None:
    entry = make_process(capabilities=[capability("max_diameter_mm", 0, placeholder=True)])
    assert entry.process is not None and entry.process.capabilities[0].placeholder
    with pytest.raises(ValidationError):
        make_process(capabilities=[capability("max_diameter_mm", [1, 2], placeholder=True)])


def test_free_tag_accepts_any_capability() -> None:
    entry = make_process(
        entry_id="foundry",
        tag="sand_casting",
        capabilities=[
            capability("max_pour_kg", 50, unit="kg"),
            capability("alloys", ["gray iron"]),
            capability("anything", [1, 2, 3, 4]),
        ],
    )
    assert entry.process is not None and entry.process.known_tag is None
    assert parse_entry(render_entry(entry), expected_id="foundry") == entry


def test_capability_needs_exactly_one_value_form() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        make_process(tag="forge", capabilities=[{"name": "x"}])
    with pytest.raises(ValidationError, match="exactly one"):
        make_process(tag="forge", capabilities=[{"name": "x", "value": 1, "value_set": [1]}])
    with pytest.raises(ValidationError, match="empty"):
        make_process(tag="forge", capabilities=[{"name": "x", "value_set": []}])
    with pytest.raises(ValidationError):
        make_process(tag="forge", capabilities=[{"name": "x", "value": float("nan")}])
