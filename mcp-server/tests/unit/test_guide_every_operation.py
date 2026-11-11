"""The agent reaches every operation of the CAD through ``cad_guide`` and no reply is cut without saying so."""

import re

import pytest
from cotaforge.adapters.fake import FakeAdapter
from cotaforge.contract import OperationSpec
from pydantic import BaseModel

from cotaforge_mcp import catalog
from cotaforge_mcp.guide import GUIDE_REPLY_LIMIT, guide, server_instructions
from cotaforge_mcp.guide_ops import group_ops
from tests.conftest import Caller, MakeSession

CATEGORIES = ("sketches", "curves", "surfaces", "sheet metal", "weldments", "assemblies", "drawings", "machining")


class Plain(BaseModel):
    """An operation with nothing to say."""

    depth_mm: float = 1.0


def _spec(name: str, category: str, purpose: str) -> OperationSpec:
    return OperationSpec(name=name, params=Plain, purpose=purpose, guide="", method="probe", category=category)


class HundredOps(FakeAdapter):
    """A CAD that declares 100 operations of its own, in eight categories, with purposes of realistic length."""

    def extra_operations(self) -> tuple[OperationSpec, ...]:
        specs = [
            _spec(
                f"{CATEGORIES[i % len(CATEGORIES)].replace(' ', '_')}_op_{i:03d}",
                CATEGORIES[i % len(CATEGORIES)],
                f"does the number {i} thing of the {CATEGORIES[i % len(CATEGORIES)]} family with a long enough purpose",
            )
            for i in range(97)
        ]
        specs.append(_spec("taper_walls", "machining", "draft angle on the walls of a mould cavity"))
        specs.append(_spec("scribe_helix", "curves", "helix curve along an axis, for a spring or a thread"))
        specs.append(_spec("one_more", "", "an operation that declares no category"))
        return tuple(specs)


def test_the_full_table_is_unchanged_when_it_fits_one_reply(session) -> None:  # noqa: ANN001
    old = catalog.ops_table(session.available_ops(), session.extensions())
    assert len(old) <= GUIDE_REPLY_LIMIT
    assert guide(session, "ops") == old


def test_with_a_hundred_operations_the_first_answer_is_a_short_index(make: MakeSession) -> None:
    session = make(HundredOps())
    names = session.available_ops()
    assert len(names) > 100
    text = guide(session, "ops")
    assert len(text) < 2500
    assert f"{len(names)} operations" in text
    for category in CATEGORIES:
        assert re.search(rf"^- {category} \(\d+\):", text, re.M), category
    assert 'topic="ops:<category>"' in text and 'topic="find:<word>"' in text


def test_every_operation_is_reachable_by_its_category_and_no_reply_is_over_the_limit(make: MakeSession) -> None:
    session = make(HundredOps())
    call = Caller(session)
    names = set(session.available_ops())
    index = call.ok("cad_guide", topic="ops")
    reached: set[str] = set()
    for label in re.findall(r"^- (.+?) \(\d+\):", index, re.M):
        topic = f"ops:{label}"
        while True:
            reply = call.ok("cad_guide", topic=topic)
            assert len(reply) <= GUIDE_REPLY_LIMIT
            reached.update(re.findall(r"^(\w+)\(", reply, re.M))
            more = re.search(r'cad_guide\(topic="([^"]+)"\)$', reply)
            if "... page" not in reply or more is None:
                break
            topic = more.group(1)
    assert names - reached == set()


def test_the_counts_of_the_index_add_up_to_every_operation(make: MakeSession) -> None:
    session = make(HundredOps())
    text = guide(session, "ops")
    counts = [int(n) for n in re.findall(r"^- .+? \((\d+)\):", text, re.M)]
    assert sum(counts) == len(session.available_ops())


def test_all_in_pages_is_a_way_to_read_everything(make: MakeSession) -> None:
    session = make(HundredOps())
    names = set(session.available_ops())
    seen: set[str] = set()
    topic = "ops:all"
    for _ in range(20):
        reply = guide(session, topic)
        assert len(reply) <= GUIDE_REPLY_LIMIT
        seen.update(re.findall(r"^(\w+)\(", reply, re.M))
        more = re.search(r'the rest: cad_guide\(topic="([^"]+)"\)$', reply)
        if more is None:
            break
        topic = more.group(1)
    assert names - seen == set()


def test_a_category_is_found_however_it_is_spelled(make: MakeSession) -> None:
    session = make(HundredOps())
    # an adapter's "sheet metal" joins the common one of the same name
    assert "base_flange(" in guide(session, "ops:Sheet_Metal")
    assert "base_flange(" in guide(session, "ops: sheet metal")
    with pytest.raises(Exception, match="unknown category"):
        guide(session, "ops:nonsense")


def test_an_operation_with_no_declared_category_falls_in_other(make: MakeSession) -> None:
    session = make(HundredOps())
    assert "- other (1):" in guide(session, "ops")
    assert guide(session, "ops:other").startswith("one_more(")


@pytest.mark.parametrize(
    ("query", "expected"),
    [("draft", "taper_walls"), ("desmoldeo", "taper_walls"), ("hélice", "scribe_helix"), ("resorte", "scribe_helix")],
)
def test_a_word_in_english_or_spanish_finds_the_operation(make: MakeSession, query: str, expected: str) -> None:
    session = make(HundredOps())
    assert expected in guide(session, f"find:{query}")


def test_find_on_the_common_vocabulary_understands_spanish(session) -> None:  # noqa: ANN001
    assert "chamfer(" in guide(session, "find:chaflán")
    assert "fillet(" in guide(session, "find:redondeo")
    assert "hole(" in guide(session, "find:agujeros")
    assert "no operation matches" in guide(session, "find:zzzz")


def test_find_never_floods_the_reply(make: MakeSession) -> None:
    session = make(HundredOps())
    text = guide(session, "find:family")
    assert len(text) <= GUIDE_REPLY_LIMIT
    assert "more match" in text


def test_any_long_guide_text_comes_in_pages_and_says_so(make: MakeSession) -> None:
    class Wordy(HundredOps):
        def extra_operations(self) -> tuple[OperationSpec, ...]:
            spec = super().extra_operations()[0]
            return (OperationSpec(**{**spec.__dict__, "guide": "word " * 3000}),)

    session = make(Wordy())
    first = guide(session, "op:sketches_op_000")
    assert len(first) <= GUIDE_REPLY_LIMIT
    assert 'the rest: cad_guide(topic="op:sketches_op_000#2")' in first
    pieces = [first]
    for page in range(2, 10):
        text = guide(session, f"op:sketches_op_000#{page}")
        assert len(text) <= GUIDE_REPLY_LIMIT
        pieces.append(text)
        if text.endswith("(last)"):
            break
    assert pieces[-1].endswith("(last)")
    assert sum(p.count("word") for p in pieces) == 3000
    with pytest.raises(Exception, match="pages"):
        guide(session, "op:sketches_op_000#99")


def test_the_profile_stays_inside_the_instructions_and_points_to_the_categories(make: MakeSession) -> None:
    session = make(HundredOps())
    instructions = server_instructions(session)
    assert len(instructions) <= 4096
    assert 'ops (sketches): 13; cad_guide(topic="ops:sketches") names them' in instructions
    # a small vocabulary still names its operations
    small = server_instructions(make(FakeAdapter()))
    assert "ops (features): sketch, " in small


class CasedCategory(FakeAdapter):
    """An adapter that writes the common category with a capital, and another with spaces."""

    def extra_operations(self) -> tuple[OperationSpec, ...]:
        return (
            _spec("cased_op", "Features", "an operation under a capitalised common category"),
            _spec("spaced_op", " Sheet  Metal ", "an operation under a category with stray spaces"),
        )


def test_a_category_written_with_another_case_or_spaces_joins_the_common_group(make: MakeSession) -> None:
    session = make(CasedCategory())
    extensions = {spec.name: spec for spec in CasedCategory().extra_operations()}
    groups = group_ops(["fillet", "base_flange", "cased_op", "spaced_op"], extensions)
    assert groups == {"features": ["fillet", "cased_op"], "sheet metal": ["base_flange", "spaced_op"]}
    features = guide(session, "ops:features")
    assert "cased_op(" in features and "fillet(" in features  # reachable together, none left out
    assert "spaced_op(" in guide(session, "ops:sheet metal")


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("orificio", "hole("),
        ("orificios", "hole("),
        ("vaciar", "shell("),
        ("redondear", "fillet("),
        ("pestaña", "flange("),
    ],
)
def test_find_understands_the_spanish_words_a_designer_uses(session, query: str, expected: str) -> None:  # noqa: ANN001
    assert expected in guide(session, f"find:{query}")


@pytest.mark.parametrize(
    ("spanish", "english"),
    [
        ("movimiento", "motion"),
        ("mecanismo", "mechanism"),
        ("resorte", "spring"),
        ("amortiguador", "damper"),
        ("gravedad", "gravity"),
        ("carrera", "stroke"),
        ("manivela", "crank"),
        ("biela", "rod"),
        ("corredera", "slider"),
        ("motor", "motor"),
        ("contacto", "contact"),
    ],
)
def test_spanish_words_of_motion_studies_reach_their_english_purposes(spanish: str, english: str) -> None:
    from cotaforge_mcp.guide_ops import _expand  # noqa: PLC0415

    assert english in _expand(spanish)
