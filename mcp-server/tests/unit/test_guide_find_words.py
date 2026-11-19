"""``find:`` takes the request the way the engineer says it: several words, Spanish, with the small words left out."""

import re

import pytest
from cotaforge.adapters.fake import FakeAdapter
from cotaforge.contract import OperationSpec
from pydantic import BaseModel

from cotaforge_mcp.guide import guide
from tests.conftest import MakeSession


class Plain(BaseModel):
    """An operation with nothing to say."""

    depth_mm: float = 1.0


def _spec(name: str, category: str, purpose: str) -> OperationSpec:
    return OperationSpec(name=name, params=Plain, purpose=purpose, guide="", method="probe", category=category)


class Shop(FakeAdapter):
    """A CAD with the kind of operations a big adapter declares: its purposes use the words of a machine shop."""

    def extra_operations(self) -> tuple[OperationSpec, ...]:
        return (
            _spec(
                "holes", "machining", "Wizard holes in one feature: countersink, counterbore, threaded, from one sketch"
            ),
            _spec("toolbox", "assemblies", "standard parts: screw, bolt, nut, washer; find one and insert a size"),
            _spec("mate", "assemblies", "any other mate: parallel, perpendicular, tangent, angle, gear, cam, hinge"),
            _spec("mate_coincident", "assemblies", "two faces or edges coincident"),
            _spec("mate_concentric", "assemblies", "two cylinders concentric"),
            _spec("mate_distance", "assemblies", "two faces at a distance"),
            _spec("material", "other", "declare the material of a part"),
            _spec("weldment_cut_list", "weldments", "the cut list of a weldment: read it, write its properties"),
            _spec("helix", "curves", "A helix whose diameter is the circle of a sketch (spring, coil, sweep path)"),
            _spec(
                "undercut_analysis", "machining", "Find the faces trapped by the rest of the body for a pull direction"
            ),
            _spec("references_repair", "other", "Point the broken references of a file at the files found in folders"),
            _spec("export_part_2d", "other", "export a part as a 2D drawing"),
            _spec("explode", "assemblies", "make an exploded view of an assembly from steps"),
        )


def _rows(reply: str) -> list[str]:
    return re.findall(r"^(\w+)\(", reply, re.M)


@pytest.fixture
def shop(make: MakeSession):  # noqa: ANN201
    return make(Shop())


def test_a_request_of_small_words_is_the_word_that_says_something(shop) -> None:  # noqa: ANN001
    # 'de', 'en', 'un', 'la' used to match every operation as a substring
    assert _rows(guide(shop, "find:resorte de compresión")) == ["helix"]
    assert guide(shop, "find:de en un la").startswith("no operation matches")


def test_1_an_external_thread_on_a_shaft_says_no_operation_has_the_thread(shop) -> None:  # noqa: ANN001
    reply = guide(shop, "find:una rosca exterior en un eje")
    assert "No operation holds all of rosca, exterior, eje" in reply and "run_native" in reply
    assert "no operation has: exterior" in reply
    assert _rows(reply)[0] == "holes" and len(_rows(reply)) < 10  # not the thirty-one rows of noise


def test_2_a_cut_by_revolution_does_not_pass_revolve_off_as_the_answer(shop) -> None:  # noqa: ANN001
    reply = guide(shop, "find:revolución corte")
    assert "No operation holds all of revolucion, corte" in reply and "run_native" in reply


def test_3_a_keyway_finds_nothing_and_says_what_to_do_next(shop) -> None:  # noqa: ANN001
    reply = guide(shop, "find:chavetero")
    assert reply.startswith("no operation matches") and "Categories:" in reply and "run_native" in reply


def test_4_a_countersunk_hole_for_a_screw_reaches_the_hole_wizard(shop) -> None:  # noqa: ANN001
    rows = _rows(guide(shop, "find:agujero avellanado para tornillo"))
    assert set(rows[:2]) == {"holes", "hole"} or rows[0] == "holes"  # the hole op names counterbore and countersink
    assert "toolbox" not in rows[:1]


def test_5_a_parallel_constraint_between_two_faces_puts_mate_first(shop) -> None:  # noqa: ANN001
    rows = _rows(guide(shop, "find:restricción paralela entre dos caras"))
    assert rows[0] == "mate"
    assert "material" not in rows  # 'mate' is not the start of 'material'


def test_6_a_spur_gear_finds_the_gear_mate_and_not_the_2d_exports(shop) -> None:  # noqa: ANN001
    reply = guide(shop, "find:engranaje recto módulo 2")
    assert _rows(reply)[0] == "mate" and "export_part_2d" not in _rows(reply)
    assert "run_native" in reply  # no operation makes the gear itself


@pytest.mark.parametrize("word", ["texto grabado", "hexágono", "elipse"])
def test_7_a_sketch_shape_without_an_operation_points_to_run_native(shop, word: str) -> None:  # noqa: ANN001
    assert "run_native" in guide(shop, f"find:{word}")


def test_8_the_cut_list_of_the_structure_reaches_the_weldment(shop) -> None:  # noqa: ANN001
    rows = _rows(guide(shop, "find:lista de corte de la estructura"))
    assert rows[0] == "weldment_cut_list"
    assert "undercut_analysis" not in rows[:1]


def test_9_a_motion_simulation_says_there_is_no_study_instead_of_noise(shop) -> None:  # noqa: ANN001
    reply = guide(shop, "find:simulación de movimiento de un mecanismo")
    assert reply.startswith("no operation matches") and "run_native" in reply
    assert "undercut_analysis" not in reply.split("Categories:")[0]


def test_10_a_compression_spring_reaches_the_helix(shop) -> None:  # noqa: ANN001
    assert _rows(guide(shop, "find:resorte de compresión"))[0] == "helix"


def test_an_exploded_view_in_spanish_reaches_explode(shop) -> None:  # noqa: ANN001
    assert "explode" in _rows(guide(shop, "find:vista explosionada"))


def test_the_common_vocabulary_keeps_its_ten_best_known_words(session) -> None:  # noqa: ANN001
    for word, expected in [("chaflán", "chamfer("), ("vaciado", "shell("), ("revolución", "revolve(")]:
        assert expected in guide(session, f"find:{word}")


class Named(FakeAdapter):
    def extra_operations(self) -> tuple[OperationSpec, ...]:
        return (
            _spec("wheel_train", "assemblies", "a train of wheels that gear into each other"),
            _spec("gear_pair", "assemblies", "two wheels"),
        )


def test_the_name_of_an_operation_comes_before_its_purpose(make: MakeSession) -> None:
    rows = _rows(guide(make(Named()), "find:engranaje"))
    assert rows[:2] == ["gear_pair", "wheel_train"]
