"""The CAD's own features through the server (a stand-in CAD that lists, edits and deletes features), the file that a
fragment saved itself, ``doc save force`` and ``doc reload`` (the fake CAD)."""

from pathlib import Path
from typing import Any

import pytest
from cotaforge.contract import (
    AdapterError,
    BoxRequest,
    ErrorCode,
    ErrorInfo,
    FeatureBody,
    FeatureEntry,
    FeatureList,
    FocusResult,
    NativeError,
    NativeObject,
    RunNativeResult,
)
from cotaforge.contract.handles import BodyHandle, FeatureHandle

from cotaforge_mcp.schemas import TOOLS_BY_NAME, input_schema, visible_tools
from tests.conftest import Caller, MakeSession, NativeFake, plate


class FeatureFake(NativeFake):
    """The native fake that also keeps a small list of CAD features (Pad, Fillet, Chamfer in one body) and edits it the
    way the worker answers: a property set / a delete is one history entry (a 1 mm box stands in for it)."""

    def __init__(self) -> None:
        super().__init__()
        self.cad: dict[str, dict[str, Any]] = {
            "Pad": {"type": "PartDesign::Pad", "properties": {"Length": 8.0}, "owner": None},
            "Fillet": {"type": "PartDesign::Fillet", "properties": {"Radius": 5.0}, "refs": ["Edge1", "Edge2"]},
            "Chamfer": {"type": "PartDesign::Chamfer", "properties": {"Size": 1.0}, "refs": ["Edge3"]},
        }
        self.requests: list[Any] = []
        self.focused = 0
        self.failure: str | None = None
        self.owner: FeatureHandle | None = None

    def run_native(self, request: Any) -> Any:
        if request.code.startswith("touch "):  # a fragment that saved the document's file itself
            Path(request.code.removeprefix("touch ").strip()).write_text("{}", encoding="utf-8")
            return RunNativeResult(ok=True)
        return super().run_native(request)

    def _entry(self, name: str, tip: str, detail: bool) -> FeatureEntry:
        item = self.cad[name]
        refs = item.get("refs")
        return FeatureEntry(
            name=name,
            label=name,
            type_id=item["type"],
            body_name="Body",
            body=BodyHandle(id="body:1"),
            is_tip=name == tip,
            base=None,
            properties=dict(item["properties"]),
            ref_count=len(refs) if refs is not None else None,
            ref_names=tuple(refs or ()),
            used_by=("Chamfer",) if name == "Fillet" else (),
            feature=self.owner if name == "Pad" else None,
            editable_properties=tuple(f"{k}:length" for k in item["properties"]) if detail else (),
        )

    def list_features(self, request: Any) -> FeatureList:
        tip = list(self.cad)[-1]
        if request.feature is not None:
            if request.feature not in self.cad:
                raise AdapterError(
                    ErrorInfo(
                        code=ErrorCode.invalid_request,
                        message=f"no feature called {request.feature!r} in the document; features: "
                        + ", ".join(self.cad),
                        recoverable=False,
                    )
                )
            return FeatureList(
                bodies=(FeatureBody(name="Body", label="Body", tip=tip),),
                features=(self._entry(request.feature, tip, True),),
            )
        return FeatureList(
            bodies=(FeatureBody(name="Body", label="Body", body=BodyHandle(id="body:1"), tip=tip),),
            features=tuple(self._entry(n, tip, False) for n in self.cad),
        )

    def _step(self, request: Any, changed: bool = True) -> RunNativeResult:
        if self.failure is not None:
            return RunNativeResult(
                ok=False,
                rolled_back=True,
                error=NativeError(type="BrokenResult", message=self.failure, hint="pick another value"),
            )
        made = self.box(BoxRequest(operation_id=request.operation_id, size=(1, 1, 1))) if changed else None
        return RunNativeResult(ok=True, feature=made.feature if made else None, volume_delta_mm3=1.0)

    def set_feature_properties(self, request: Any) -> RunNativeResult:
        self.requests.append(request)
        result = self._step(request)
        if result.ok:
            for key, value in request.properties.items():
                self.cad[request.feature]["properties"][key] = value
        return result

    def remove_feature(self, request: Any) -> RunNativeResult:
        self.requests.append(request)
        result = self._step(request)
        if result.ok:
            name = request.feature
            self.cad.pop(name)
            return result.model_copy(
                update={"removed": (NativeObject(name=name, label=name, type_id="PartDesign::X"),)}
            )
        return result

    def focus_model(self, request: Any) -> FocusResult:
        self.focused += 1
        return FocusResult(shown=("Fillet",), hidden=("Sketch",))


@pytest.fixture
def cad(make: MakeSession) -> Caller:
    call = Caller(make(FeatureFake()))
    plate(call)
    return call


def fake(call: Caller) -> FeatureFake:
    adapter = call.session.adapter
    assert isinstance(adapter, FeatureFake)
    return adapter


def test_the_tree_lists_the_cad_features_with_their_tip_and_parameters(cad: Caller) -> None:
    tree = cad.ok("inspect", action="tree")

    assert "features: 3" in tree and "made outside the steps" in tree
    assert "body Body (body:1): tip Chamfer" in tree
    assert "Fillet (Radius=5, 2 edges/faces)" in tree and "Pad (Length=8)" in tree
    assert "Chamfer (Size=1, 1 edges/faces) [tip]" in tree
    detail = cad.ok("inspect", action="tree", id="Fillet")
    assert "needed by: Chamfer" in detail and "Radius:length" in detail
    assert "no feature called 'Nope'" in cad.err("inspect", action="tree", id="Nope")


def test_a_property_of_a_feature_made_outside_the_steps_is_set_and_recorded_as_a_step(cad: Caller) -> None:
    done = cad.ok("op", edit="Fillet", params={"Radius": 4}, intent="smaller")

    assert done.startswith("ok: s3 edited Fillet: Radius 5 -> 4")
    assert "tip: Body -> Chamfer" in done
    assert fake(cad).requests[0].properties == {"Radius": 4} and fake(cad).requests[0].intent == "smaller"
    assert [s.id for s in cad.session.steps()][-1] == "s3" and cad.session.dirty
    assert cad.ok("undo").startswith("undone: s3 set_properties")


def test_a_feature_a_plan_step_made_is_edited_through_that_step(cad: Caller) -> None:
    fake(cad).owner = cad.session.steps()[1].feature

    refused = cad.err("op", edit="Pad", params={"Length": 9})

    assert "was made by step s2" in refused and 'op(edit="s2"' in refused
    assert fake(cad).requests == []


def test_an_unknown_feature_is_refused_with_what_exists(cad: Caller) -> None:
    refused = cad.err("op", edit="Gone", params={"Radius": 1})

    assert "no feature called 'Gone'" in refused and "steps: s1, s2" in refused and "unknown_step" in refused
    assert "needs params" in cad.err("op", edit="Fillet", params={})


def test_a_change_the_cad_cannot_rebuild_is_reported_and_no_step_is_recorded(cad: Caller) -> None:
    fake(cad).failure = "Fillet: recompute error: BRep_API: command not done"

    refused = cad.err("op", edit="Fillet", params={"Radius": 200})

    assert "was not applied" in refused and "state: unchanged" in refused and "BRep_API" in refused
    assert [s.id for s in cad.session.steps()] == ["s1", "s2"]


def test_a_delete_names_the_feature_and_records_a_step_that_undo_takes_back(cad: Caller) -> None:
    deleted = cad.ok("op", delete="Chamfer", params={"cascade": True})

    assert "ok: s3 deleted Chamfer" in deleted and "removed: Chamfer" in deleted
    assert "view: tips shown, sketches and intermediate features hidden" in deleted
    assert fake(cad).requests[0].cascade and fake(cad).requests[0].feature == "Chamfer"
    assert cad.ok("undo").startswith("undone: s3 delete_feature")


def test_a_delete_is_alone_and_takes_only_cascade(cad: Caller) -> None:
    assert "delete is alone" in cad.err("op", delete="Chamfer", edit="Fillet")
    assert "only params" in cad.err("op", delete="Chamfer", params={"force": True})
    assert "adopted from the opened file" not in cad.err("op", delete="s1")  # a sketch step: the CAD answers


def test_view_focus_exists_when_the_cad_can_do_it(cad: Caller) -> None:
    assert "focus" in input_schema(TOOLS_BY_NAME["view"], cad.session)["properties"]["action"]["enum"]
    assert "view" in visible_tools(cad.session)
    assert cad.ok("view", action="focus") == "focus: shown Fillet; hidden Sketch (display only)"


# --- save: the file a fragment wrote itself, force, reload ----------------------------------------------------------


def test_a_fragment_that_saves_the_file_itself_is_not_a_conflict_for_the_next_save(cad: Caller, tmp_path: Path) -> None:
    cad.ok("doc", action="save", path="plate.fake")
    target = tmp_path / "plate.fake"

    note = cad.ok("run_native", code=f"touch {target}", intent="save from the CAD")
    saved = cad.ok("doc", action="save")

    assert "saved plate.fake itself" in note and "saved:" in saved


def test_a_file_changed_by_somebody_else_is_a_conflict_unless_forced(cad: Caller, tmp_path: Path) -> None:
    cad.ok("doc", action="save", path="plate.fake")
    (tmp_path / "plate.fake").write_text("{}", encoding="utf-8")  # not through this server

    refused = cad.err("doc", action="save")
    assert "file_conflict" in refused and "force=true" in refused and "reload" in refused

    forced = cad.ok("doc", action="save", force=True)
    assert "saved:" in forced
    assert cad.session.adapter.save_calls[-1].expected_sha256 is None  # type: ignore[attr-defined]


def test_reload_reads_the_file_again_and_drops_unsaved_work_only_when_forced(cad: Caller) -> None:
    cad.ok("doc", action="save", path="plate.fake")
    cad.ok("op", name="box", params={"size": [1, 1, 1]})

    refused = cad.err("doc", action="reload")
    assert "unsaved_changes" in refused and "force=true" in refused

    reloaded = cad.ok("doc", action="reload", force=True)
    assert reloaded.startswith("reloaded: plate.fake from disk (3 steps of the session dropped)")
    assert "UNSAVED" not in cad.ok("doc", action="status")
    assert cad.ok("doc", action="reload").startswith("reloaded: plate.fake")  # nothing unsaved: no force needed


def test_reload_needs_a_file(cad: Caller) -> None:
    assert "no file on disk" in cad.err("doc", action="reload")
    assert "reload" in input_schema(TOOLS_BY_NAME["doc"], cad.session)["properties"]["action"]["enum"]
