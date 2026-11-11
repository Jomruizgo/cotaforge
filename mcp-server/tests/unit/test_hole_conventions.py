"""The colour convention of holes at the server: defaults with no file, a file in the data folder and one in the design
space, a wrong file that never stops the work, the agent changing it through doc (not the memory) and the
class the reply of a hole names. Over the fake adapter."""

from cotaforge.conventions import FILENAME

from tests.conftest import TOP, Caller, plate

HOLE = {"face": TOP, "position": [30, 0], "standard": "simple", "diameter_mm": 6, "end_condition": "through_all"}


def test_a_fresh_install_has_the_convention_with_no_file(call: Caller) -> None:
    text = call.ok("doc", action="convention")
    assert "[255, 128, 128]" in text and "[0, 192, 0]" in text and "[128, 128, 255]" in text
    assert "none (the defaults of the harness)" in text
    assert not (call.session.settings.workspace / FILENAME).exists()  # reading writes nothing


def test_the_reply_of_a_hole_names_its_class_and_colour(call: Caller) -> None:
    plate(call)
    assert "hole class: plain, faces coloured [0, 192, 0]" in call.ok("op", name="hole", params=HOLE)
    fitted = call.ok("op", name="hole", id="h2", params={**HOLE, "position": [-30, 0], "fit": "H7"})
    assert "hole class: precision, faces coloured [255, 128, 128]" in fitted
    tapped = {**HOLE, "position": [0, 20], "standard": "iso_tapped", "size": "M6"}
    tapped.pop("diameter_mm")
    assert "hole class: threaded, faces coloured [128, 128, 255]" in call.ok("op", name="hole", id="h3", params=tapped)


def test_a_fit_that_is_not_a_tolerance_class_is_refused(call: Caller) -> None:
    plate(call)
    assert "fit" in call.err("op", name="hole", params={**HOLE, "fit": "tight please"})


def test_the_agent_changes_the_colours_when_asked_and_it_is_a_file_not_memory(call: Caller) -> None:
    text = call.ok("doc", action="convention", values={"colores": {"liso": "#0000ff"}, "grado_it_preciso": 7})
    path = call.session.settings.workspace / FILENAME
    assert path.is_file() and str(path) in text and "[0, 0, 255]" in text
    plate(call)
    assert "hole class: plain, faces coloured [0, 0, 255]" in call.ok("op", name="hole", params=HOLE)
    # it did not go to the memory system
    assert not list((call.session.settings.workspace / "memory").glob("*.md"))
    assert not list((call.session.settings.home / "memory").glob("*"))


def test_the_user_scope_goes_to_the_data_folder_and_the_project_wins(call: Caller) -> None:
    call.ok("doc", action="convention", values={"alcance": "usuario", "colores": {"liso": [1, 2, 3]}})
    assert (call.session.settings.home / FILENAME).is_file()
    assert "[1, 2, 3]" in call.ok("doc", action="convention")
    call.ok("doc", action="convention", values={"colores": {"liso": [9, 9, 9]}})
    assert "[9, 9, 9]" in call.ok("doc", action="convention")


def test_it_can_be_switched_off_and_reset(call: Caller) -> None:
    call.ok("doc", action="convention", values={"activa": False})
    plate(call)
    assert "hole class" not in call.ok("op", name="hole", params=HOLE)
    call.ok("doc", action="convention", values={"reiniciar": True})
    assert '"activa": true' in call.ok("doc", action="convention")


def test_a_wrong_change_is_refused_with_the_right_shape(call: Caller) -> None:
    text = call.err("doc", action="convention", values={"colores": {"liso": [1, 2]}})
    assert "colour" in text and "[r, g, b]" in text
    assert not (call.session.settings.workspace / FILENAME).exists()
    assert "alone" in call.err("doc", action="convention", values={"reiniciar": True, "activa": False})
    assert "scope" in call.err("doc", action="convention", values={"activa": False, "alcance": "nube"})


def test_a_wrong_file_never_stops_the_work_and_the_agent_is_told(call: Caller) -> None:
    (call.session.settings.workspace / FILENAME).write_text("agujeros: [\n", encoding="utf-8")
    plate(call)
    text = call.ok("op", name="hole", params=HOLE)
    assert "hole class: plain, faces coloured [0, 192, 0]" in text  # the default applied
    assert "convention file problem" in text and FILENAME in text
    assert "problem:" in call.ok("doc", action="convention")
