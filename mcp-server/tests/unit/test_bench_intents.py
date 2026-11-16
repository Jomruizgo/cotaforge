"""The intent files of the bench parts (``tests/data/bench``): the designer side of the two cases a toolroom knows,
readable by the harness the way a drafter session reads them. No FreeCAD here: the real build and the sheets are in
``tests/integration/test_bench_real_parts_real.py``."""

from pathlib import Path

import pytest

from cotaforge_mcp.functional_dimensions import declared

DATA = Path(__file__).resolve().parent.parent / "data" / "bench"
HEADINGS = (
    "## Función",
    "## Caras de unión",
    "## Ajustes",
    "## Datos",
    "## Proceso",
    "## Material",
    "## Tratamientos y acabado",
    "## Cotas",
    "## Relaciones funcionales",
    "## Hoja",
)


def text(part: str) -> str:
    return (DATA / f"{part}-intent.md").read_text(encoding="utf-8")


def lines_of_sections(part: str) -> str:
    """What the harness reads: the lines under the headings, not the headings themselves."""
    return "\n".join(line for line in text(part).splitlines() if not line.startswith("#"))


@pytest.mark.parametrize("part", ["die_plate", "edge_block"])
def test_an_intent_file_has_every_heading_the_drafter_reads(part: str) -> None:
    body = text(part)
    for heading in HEADINGS:
        assert heading in body, f"{part}: {heading}"
    assert "\r" not in body


def test_the_die_plate_route_grinds_before_it_finishes_the_dowel_holes() -> None:
    """Holes located from the ground face: finished AFTER the grinding, pre-drilled under size BEFORE the hardening."""
    body = text("die_plate")
    pre_drilled = body.index("Preagujereado")
    hardening = body.index("Temple y revenido a 60")
    grinding = body.index("Rectificado de las caras A y B")
    finishing = body.index("Acabado de los agujeros de registro")
    assert pre_drilled < hardening < grinding < finishing
    assert "Ø5,7" in body and "Ø6 H7" in body
    assert "perpendicular a A" in body and "A sobre la mesa" in body


def test_the_critical_dimensions_of_the_die_plate_are_read_with_their_value_and_tolerance() -> None:
    found = {(round(item.value_mm or 0, 2), item.tolerance) for item in declared(lines_of_sections("die_plate"))}
    assert {(25.0, "±0,02"), (60.0, "±0,01"), (24.0, "±0,01"), (30.0, "±0,01"), (28.0, "±0,01")} <= found


def test_the_critical_dimensions_of_the_edge_block_are_read_with_their_value_and_tolerance() -> None:
    found = {(round(item.value_mm or 0, 2), item.tolerance) for item in declared(lines_of_sections("edge_block"))}
    assert {(40.0, "±0,02"), (30.0, "±0,02"), (25.0, "±0,05"), (44.0, "±0,1")} <= found


def test_every_critical_line_of_the_bench_intents_states_a_value() -> None:
    for part in ("die_plate", "edge_block"):
        for item in declared(lines_of_sections(part)):
            assert item.value_mm is not None or "juego" in item.text, f"{part}: {item.text}"
