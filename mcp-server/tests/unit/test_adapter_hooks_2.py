"""The four small hooks an adapter with other conventions needs: a hint in the reply of ``sketch``, a mate distance that
``edit_feature`` can carry, a file suffix per kind of document, and the guide of the CAD."""

from pathlib import Path
from typing import Any

import pytest
from cotaforge.adapters.fake import FakeAdapter
from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.contract import DocumentKind

from cotaforge_mcp import catalog
from cotaforge_mcp.guide import guide
from cotaforge_mcp.reply import ToolError
from cotaforge_mcp.session import Session
from tests.conftest import PLATE_SKETCH, Caller, MakeSession
from tests.unit.test_joints import gate_and_post


class HintFake(FakeAdapter):
    """A CAD whose sketch comes back with a hint for the agent."""

    def sketch(self, request: Any) -> Any:
        result = super().sketch(request)
        return result.model_copy(update={"warnings": ("two holes of one size: say equal if they are the same",)})


class SuffixFake(FakeAdapter):
    """A CAD whose assemblies are other files than its parts."""

    def suffix_for(self, kind: DocumentKind) -> str:
        return ".asmfake" if kind is DocumentKind.assembly else ".partfake"


class GuideFake(FakeAdapter):
    """A CAD that writes its own drawing guide and the sentences that are true of it only."""

    def guide_topic(self, topic: str) -> str | None:
        return {
            "drawing": "OWN DRAWING GUIDE",
            "drawing:items": "OWN ITEMS",
            "note:joint": " The joint is a mate of the CAD.",
            "note:add_joint": " Own add_joint note.",
            "note:hole.up_to_face": "; own up_to_face note",
        }.get(topic)


def test_a_hint_of_the_adapter_reaches_the_agent_in_the_reply_of_sketch(make: MakeSession) -> None:
    call = Caller(make(HintFake()))
    call.ok("doc", action="new", name="plate")
    reply = call.ok("op", name="sketch", params=PLATE_SKETCH)
    assert reply.startswith("ok: s1 sketch")
    assert "warnings: two holes of one size: say equal if they are the same" in reply


def test_a_sketch_without_a_hint_says_no_warnings(call: Caller) -> None:
    call.ok("doc", action="new", name="plate")
    assert "warnings" not in call.ok("op", name="sketch", params=PLATE_SKETCH)


def test_an_assembly_is_saved_with_the_suffix_of_an_assembly_and_a_part_with_its_own(make: MakeSession) -> None:
    call = Caller(make(SuffixFake()))
    call.ok("doc", action="new", name="bracket")
    assert call.ok("doc", action="save").startswith(f"saved: {call.session.settings.workspace / 'bracket.partfake'}")
    call.ok("doc", action="new", name="frame", kind="assembly")
    assert "frame.asmfake" in call.ok("doc", action="save")
    assert (call.session.settings.workspace / "frame.asmfake").is_file()
    assert not (call.session.settings.workspace / "frame.partfake").exists()


def test_a_name_without_suffix_in_save_takes_the_suffix_of_the_kind(make: MakeSession) -> None:
    call = Caller(make(SuffixFake()))
    call.ok("doc", action="new", name="frame", kind="assembly")
    assert "other.asmfake" in call.ok("doc", action="save", path="other")


def test_the_default_suffix_is_the_adapters_one_for_every_kind(session: Session, tmp_path: Path) -> None:
    call = Caller(session)
    call.ok("doc", action="new", name="frame", kind="assembly")
    assert f"frame{FakeAdapter.document_suffix}" in call.ok("doc", action="save")


def test_the_guide_of_the_cad_replaces_the_default_of_that_topic_only(make: MakeSession) -> None:
    session = make(GuideFake())
    assert guide(session, "drawing") == "OWN DRAWING GUIDE" and guide(session, "drawing:items") == "OWN ITEMS"
    assert guide(session, "drawing:marks") == guide(make(FakeAdapter()), "drawing:marks")  # not overridden: default
    assert guide(session, "memory") == guide(make(FakeAdapter()), "memory")


def test_the_sentences_of_one_cad_sit_inside_the_default_text(make: MakeSession) -> None:
    session = make(GuideFake())
    assert "or mated. The joint is a mate of the CAD." in guide(session, "assembly")
    assert "Own add_joint note. cad_guide" in guide(session, "op:add_joint")
    assert "other side); own up_to_face note" in guide(session, "op:hole")


def test_a_note_key_is_never_a_topic_of_its_own(make: MakeSession) -> None:
    with pytest.raises(ToolError, match="unknown topic"):
        guide(make(GuideFake()), "note:joint")


def test_a_cad_with_nothing_to_add_gets_a_guide_that_names_no_other_cad(session: Session) -> None:
    for topic in ("assembly", "traps", "op:add_joint", "op:hole", "op:fillet", "op:chamfer", "recipe", "ops"):
        assert "FreeCAD" not in guide(session, topic), topic
    assert "<cad-note>" not in guide(session, "op:add_joint") and "<suffix>" not in guide(session, "assembly")


def test_freecad_keeps_its_own_sentences_word_for_word() -> None:
    freecad = FreeCADAdapter()
    reference = catalog.op_reference("add_joint", freecad.guide_topic)
    assert "use set_joint. In FreeCAD the joint is a real Assembly joint when a is a component. cad_guide" in reference
    assert (
        "(e.g. the floor of a counterbore you drill into from the other side); FreeCAD's Hole has no such end: "
        "drilled blind to that distance" in catalog.op_reference("hole", freecad.guide_topic)
    )
    assert freecad.guide_topic("note:joint") == (
        " In FreeCAD the joint is a real Assembly joint when a is an instance (the engineer sees it in the model "
        "tree); a joint to the ground exists in the harness only."
    )
    assert freecad.guide_topic("drawing") is None  # FreeCAD's drawing guide is the server's own text
    assert freecad.suffix_for(DocumentKind.assembly) == freecad.suffix_for(DocumentKind.part) == ".FCStd"


def test_the_assembly_guide_names_the_suffix_of_the_part_of_the_cad(make: MakeSession) -> None:
    session = make(SuffixFake())
    assert "saved at once as <name>.partfake" in guide(session, "assembly")


def test_a_joint_note_of_the_cad_needs_the_joint_tools(make: MakeSession) -> None:
    call = Caller(make(GuideFake()))
    gate_and_post(call)  # the fixture of the joint tests: the guide of an assembly with joints is built from them
    assert "The joint is a mate of the CAD." in call.ok("cad_guide", topic="assembly")


def test_the_adapter_can_word_the_description_of_a_tool(make: MakeSession) -> None:
    from cotaforge_mcp.schemas import TOOLS_BY_NAME, tool_description

    class Worded(FakeAdapter):
        def guide_topic(self, topic: str) -> str | None:
            return "OWN DRAWING TOOL" if topic == "tool:drawing" else None

    drawing = TOOLS_BY_NAME["drawing"]
    assert tool_description(drawing, make(Worded())) == "OWN DRAWING TOOL"
    assert tool_description(drawing, make(FakeAdapter())) == drawing.description
