# ruff: noqa: E501
"""The cosmetic external thread on a sheet (``worker/thread_records.py``, ``sheet_thread.py``, the diameter ``dim`` of
``items_dims.py``) against a hand-made shaft along z: a Ø24 journal from z 0 to 20 (an M24 thread of 15 mm from its top
end) at 1:1 in a front, a top and a right view. No FreeCAD: the callout and the ISO 6410 lines come out of the record the
operation leaves in the document."""

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import sheet_context as sc  # noqa: E402
import sheet_items as items  # noqa: E402
import sheet_model as model  # noqa: E402
import sheet_thread  # noqa: E402
import thread_records as records  # noqa: E402

Z = (0.0, 0.0, 1.0)


class ShaftGeometry(sc.Geometry):
    def __init__(self) -> None:
        self.faces = {
            "face:journal": sc.FaceInfo(
                "face:journal",
                "cylinder",
                (50.0, 30.0, 10.0),
                radius=12.0,
                axis_point=(50.0, 30.0, 0.0),
                axis_dir=Z,
                surface_point=(62.0, 30.0, 10.0),
            ),
            "face:renamed": sc.FaceInfo(
                "face:renamed",
                "cylinder",
                (50.0, 30.0, 10.0),
                radius=12.0,
                axis_point=(50.0, 30.0, 0.0),
                axis_dir=Z,
                surface_point=(62.0, 30.0, 10.0),
            ),
            "face:neck": sc.FaceInfo(
                "face:neck",
                "cylinder",
                (50.0, 30.0, 25.0),
                radius=8.0,
                axis_point=(50.0, 30.0, 0.0),
                axis_dir=Z,
                surface_point=(58.0, 30.0, 25.0),
            ),
        }

    def face(self, face_id: str) -> sc.FaceInfo:
        if face_id not in self.faces:
            raise sc.ItemError(f"{face_id} is not a face of the part")
        return self.faces[face_id]


def record(**over: Any) -> dict[str, Any]:
    base = {
        "name": "thread_op3",
        "size": "M24",
        "major_mm": 24.0,
        "pitch_mm": 3.0,
        "minor_mm": 20.752,
        "length_mm": 15.0,
        "start": [50.0, 30.0, 20.0],
        "direction": [0.0, 0.0, -1.0],
        "cylinder": "face:journal",
    }
    return {**base, **over}


def make_context(*found: dict[str, Any], language: str = "es") -> sc.Context:
    views = {
        "front": sc.ViewInfo(
            "front", 150.0, 160.0, 1.0, (0, -1, 0), (1, 0, 0), (50, 30, 10), (138.0, 150.0, 162.0, 170.0)
        ),
        "top": sc.ViewInfo("top", 150.0, 100.0, 1.0, (0, 0, 1), (1, 0, 0), (50, 30, 10), (138.0, 88.0, 162.0, 112.0)),
        "right": sc.ViewInfo("right", 50.0, 160.0, 1.0, (1, 0, 0), (0, 1, 0), (50, 30, 10), (38.0, 150.0, 62.0, 170.0)),
    }
    return sc.Context(
        sheet=model.Sheet(420.0, 297.0),
        views=views,
        holes=[],
        geometry=ShaftGeometry(),
        area=(25.0, 80.0, 405.0, 282.0),
        language=language,
        threads=sheet_thread.external_threads(list(found)),
    )


def draw(ctx: sc.Context, *specs: dict[str, Any]) -> list[sc.ItemResult]:
    taken: set[str] = set()
    normalised = []
    for number, spec in enumerate(specs, start=1):
        done = items.normalise(spec, taken, number)
        taken.add(done["id"])
        normalised.append(done)
    return items.draw_all(ctx, normalised)


DIAMETER = {"type": "dim", "kind": "diameter", "face": "face:journal", "id": "od"}


# --- the record in the document -----------------------------------------------------------------------------------------


class FakeObject:
    def __init__(self, type_id: str, **props: Any) -> None:
        self.TypeId = type_id
        self.Label = props.pop("Label", "thread_op3")
        self.PropertiesList = list(props)
        for name, value in props.items():
            setattr(self, name, value)


def stored(**over: Any) -> FakeObject:
    props: dict[str, Any] = {
        "ThreadSize": "M12",
        "ThreadMajor": 12.0,
        "ThreadPitch": 1.75,
        "ThreadMinor": 10.106,
        "ThreadLength": 20.0,
        "ThreadStart": SimpleNamespace(x=0.0, y=0.0, z=60.0),
        "ThreadDirection": SimpleNamespace(x=0.0, y=0.0, z=-1.0),
        "ThreadCylinder": "face:4",
        "ThreadEndFace": "face:5",
    }
    return FakeObject(records.OBJECT_TYPE, **{**props, **over})


def test_the_record_is_read_back_from_the_objects_of_the_document() -> None:
    plain = FakeObject("Part::Feature", Label="box")
    other_python_object = FakeObject(records.OBJECT_TYPE, Label="something else", Other="x")
    doc = SimpleNamespace(Objects=[plain, other_python_object, stored()])
    assert records.read_threads(doc) == [
        {
            "name": "thread_op3",
            "size": "M12",
            "major_mm": 12.0,
            "pitch_mm": 1.75,
            "minor_mm": 10.106,
            "length_mm": 20.0,
            "start": [0.0, 0.0, 60.0],
            "direction": [0.0, 0.0, -1.0],
            "cylinder": "face:4",
            "tolerance_class": "",  # a record made before the class existed has none
        }
    ]


def test_a_record_becomes_the_thread_the_sheet_works_with() -> None:
    (thread,) = sheet_thread.external_threads([records.read(stored())])
    assert (thread.size, thread.major, thread.minor, thread.length) == ("M12", 12.0, 10.106, 20.0)
    assert thread.direction == (0.0, 0.0, -1.0) and thread.cylinder == "face:4"


# --- the callout --------------------------------------------------------------------------------------------------------


def test_the_diameter_of_a_threaded_shaft_end_is_its_thread_callout() -> None:
    ctx = make_context(record())
    (r,) = draw(ctx, DIAMETER)
    assert r.status == "ok", r.detail
    assert "M24 long. útil 15" in ctx.sheet.texts()
    assert "Ø24" not in " ".join(ctx.sheet.texts())  # the plain diameter is not written beside it
    assert "thread M24" in r.detail


def test_the_callout_reads_in_english_and_a_fine_pitch_with_a_decimal_comma() -> None:
    en = make_context(record(), language="en")
    draw(en, DIAMETER)
    assert "M24 useful length 15" in en.sheet.texts()
    fine = make_context(record(size="M24x1.5", pitch_mm=1.5, minor_mm=22.376))
    draw(fine, DIAMETER)
    assert "M24x1,5 long. útil 15" in fine.sheet.texts()


def test_a_cylinder_with_no_thread_keeps_its_plain_diameter() -> None:
    ctx = make_context(record())
    (r,) = draw(ctx, {**DIAMETER, "face": "face:neck"})
    assert r.status == "ok", r.detail
    assert "Ø16" in ctx.sheet.texts() and not any(t.startswith("M") for t in ctx.sheet.texts())


def test_a_face_renamed_by_an_edit_is_found_by_its_axis_and_diameter() -> None:
    ctx = make_context(record(cylinder="face:gone"))
    (r,) = draw(ctx, {**DIAMETER, "face": "face:renamed"})
    assert r.status == "ok", r.detail
    assert "M24 long. útil 15" in ctx.sheet.texts()


def test_equal_threads_on_one_cylinder_share_a_count() -> None:
    both_ends = [record(), record(start=[50.0, 30.0, 0.0], direction=[0.0, 0.0, 1.0])]
    ctx = make_context(*both_ends)
    draw(ctx, DIAMETER)
    assert "2× M24 long. útil 15" in ctx.sheet.texts()


@pytest.mark.parametrize("extra", [{"fit": "g6"}, {"tol": 0.1}])
def test_a_threaded_diameter_takes_no_fit_or_tolerance(extra: dict[str, Any]) -> None:
    ctx = make_context(record())
    (r,) = draw(ctx, {**DIAMETER, **extra})
    assert r.status == "error" and "thread class" in r.detail
    assert ctx.sheet.entities == []


# --- ISO 6410 -----------------------------------------------------------------------------------------------------------


def lines(ctx: sc.Context) -> list[model.Line]:
    return [e for e in ctx.sheet.entities if isinstance(e, model.Line) and e.layer == model.THREAD]


def arcs(ctx: sc.Context) -> list[model.Arc]:
    return [e for e in ctx.sheet.entities if isinstance(e, model.Arc) and e.layer == model.THREAD]


def test_the_thin_lines_run_at_the_minor_diameter_along_the_thread_in_the_views_across_the_axis() -> None:
    ctx = make_context(record())
    ctx.sheet.extend(sheet_thread.thread_entities(ctx))
    assert len(lines(ctx)) == 4  # the front and the right view both see the axis across: two lines each
    front = [ln for ln in lines(ctx) if ln.x1 > 100.0]
    assert sorted(round(ln.x1, 3) for ln in front) == [139.624, 160.376]  # 150 -/+ half of d1 = 20.752
    # from the end face (z 20) down the thread length (15): 170 to 155 on the page
    assert all(sorted((ln.y1, ln.y2)) == [pytest.approx(155.0), pytest.approx(170.0)] for ln in front)


def test_the_view_along_the_axis_gets_the_open_arc_at_the_minor_diameter() -> None:
    ctx = make_context(record())
    ctx.sheet.extend(sheet_thread.thread_entities(ctx))
    (arc,) = arcs(ctx)
    assert (arc.cx, arc.cy) == pytest.approx((150.0, 100.0))
    assert arc.r == pytest.approx(10.376)
    assert (arc.start, arc.end) == (sheet_thread.OPEN_FROM_DEG, sheet_thread.OPEN_TO_DEG)


def test_a_part_without_threads_adds_nothing() -> None:
    ctx = make_context()
    assert sheet_thread.thread_entities(ctx) == []
