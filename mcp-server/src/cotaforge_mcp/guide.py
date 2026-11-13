"""The agent's knowledge of the active CAD: the always-loaded profile and the on-demand ``cad_guide`` topics."""

from cotaforge.contract import DocumentKind

from cotaforge_mcp import catalog, design_memory, guide_ops, memory_tools, recipes
from cotaforge_mcp.foreign import PARASOLID_SUFFIXES
from cotaforge_mcp.guide_drawing import SUBTOPICS, TOPIC_COMPOSE, drawing_text, pointing_text
from cotaforge_mcp.guide_memory import MEMORY_TEXT
from cotaforge_mcp.reply import ToolError
from cotaforge_mcp.schemas import visible_tools
from cotaforge_mcp.session import Session
from cotaforge_mcp.terms import terms_table

TOPICS = (
    "profile",
    "ops",
    "ops:<category>",
    "find:<word>",
    "op:<name>",
    "terms",
    "traps",
    "native",
    "recipe",
    "drawing",
    "drawing:items|marks|views|assembly|output|done",
    "assembly",
    "memory",
    "pointing",
)

CAD_NOTE = catalog.CAD_NOTE
"""Where a sentence true of one CAD only goes inside a text: the adapter gives it (``guide_topic("note:...")``)."""
CAD_SUFFIX = "<suffix>"
"""Where the file suffix of the CAD's documents goes inside a text."""
ASSUMED_OPS = ', only if its add-on is installed: cad_guide(topic="ops") says'
"""Added to a group of ops the CAD has not confirmed yet (``Adapter.assumed_tools``)."""

GUIDE_REPLY_LIMIT = guide_ops.GUIDE_REPLY_LIMIT
"""Characters of one ``cad_guide`` reply; a longer text comes in pages (``topic#2``), never cut."""
PROFILE_OPS_MAX_CHARS = 1500
"""The profile names every operation while they take this much; past it, one line per category with its count."""

INSTRUCTIONS_MAX_CHARS = 4096
"""Claude Code cuts the server instructions at this many characters: what is past it never reaches the agent."""

CONDUCT = (
    "Work in small steps: ONE op per call, then read the measured facts it returns (or inspect) before the next.",
    "Units are mm. Never state a number (volume, size, count) that a tool did not return.",
    'Before an op you have not used here: cad_guide(topic="op:<name>"); a worked example: topic="recipe"; '
    'commercial CAD software words: topic="terms".',
    "Undo is per step (undo | undo(to=<id>)); a failed step changes nothing. To change a value of an earlier step "
    '(hole diameter, extrude depth, sketch dimension): op(edit="<id>", params={only what changes}), not undo and '
    "redo. Save: doc(action=save). Features of an opened file: inspect(action=tree) lists them by CAD name; "
    'op(edit="<name>", params={"Radius": 4}) and op(delete="<name>") change or remove them.',
)
NATIVE_CONDUCT = (
    'CAD scripting (run_native) is the escape hatch for what no op can do: read cad_guide(topic="native") first.'
)
NO_NATIVE_CONDUCT = "This CAD has no scripting escape hatch: if no op covers it, say so instead of improvising."
POINT_CONDUCT = (
    "When saying 'which one' would take a sentence, ask the engineer to select it in the window (3D or a drawing "
    'sheet) and read view(action=selection); show him with view(action=show): cad_guide(topic="pointing"). His hand '
    'edits of the model head your next reply ("hand edits in the CAD"); his selection never does: read it only when he asks.'
)


LANGUAGE_NAMES = {"es": "Spanish (es)", "en": "English (en)"}


def language_conduct(language: str) -> str:
    """Which language the engineer reads: his is the project's (memory brief language, changed per project)."""
    return (
        f"His language is {LANGUAGE_NAMES.get(language, language)}: speak it, and write in it what he reads or edits "
        "(decisions file, drawing texts, handoff, friction log, his memory); all else English."
    )


def conduct(session: Session) -> tuple[str, ...]:
    tools = session.capabilities.tools
    first = f"{CONDUCT[0]} {POINT_CONDUCT}" if "get_selection" in tools else CONDUCT[0]
    last = NATIVE_CONDUCT if "run_native" in tools else NO_NATIVE_CONDUCT
    return (first, *CONDUCT[1:], f"{last} {language_conduct(session.language)}")


def memory_index_section(session: Session, max_chars: int | None = None) -> str:
    """The memory index (project + user, one line per entry, bounded) appended to the instructions; empty without
    entries. Built when the server starts: ``memory(action=index)`` is the always-current view."""
    return memory_tools.instructions_section(session.settings, max_chars)


def _tools_line(session: Session) -> str:
    return "tools: " + ", ".join(visible_tools(session))


def _header(session: Session, live: bool) -> str:
    adapter = session.adapter
    mode = " (visible window)" if getattr(adapter, "visible", False) and adapter.name == "freecad" else ""
    if not live:  # the instructions are sent before the CAD is up and stay as sent: nothing of its process in them
        return f"CAD: {adapter.name}{mode}. Units mm. Several documents open, one active."
    if session.info is not None:
        return f"CAD: {adapter.name} {session.info.version}{mode}. Units mm. Several documents open, one active."
    if session.connecting:
        return f"CAD: {adapter.name} (starting)."
    return f"CAD: {adapter.name} (NOT connected: {session.connect_error or 'not started'})."


def profile_text(session: Session, live: bool = True) -> str:
    """The profile of the CAD. ``live`` adds what only a running CAD knows (its version, that it did not start); without
    it the text depends on the adapter's declaration alone (``server_instructions``)."""
    header = _header(session, live)
    groups = guide_ops.group_ops(session.available_ops(), session.extensions())
    lines = [header, _tools_line(session)]
    assumed = session.adapter.assumed_tools()
    op_lines = []
    for category, members in groups.items():
        unsure = bool(assumed) and all(catalog.adapter_method(name) in assumed for name in members)
        label = f"{category}{ASSUMED_OPS if unsure else ''}"
        op_lines.append(f"ops ({label}): " + ", ".join(members))
    if sum(len(line) + 1 for line in op_lines) > PROFILE_OPS_MAX_CHARS:  # the instructions are cut at 4096: say where
        op_lines = [
            f'ops ({category}): {len(members)}; cad_guide(topic="ops:{category}") names them'
            for category, members in groups.items()
        ]
    lines.extend(op_lines)
    missing = [t for t in ("capture", "set_view", "get_selection") if t not in session.capabilities.tools]
    if missing:
        lines.append(f"not available here: {', '.join(missing)} (headless CAD; COTAFORGE_VISIBLE=1 shows a window)")
    lines.extend(session.adapter.profile_hints())
    return "\n".join(lines)


def server_instructions(session: Session, notice: str = "") -> str:
    """What the server says at ``initialize``: the profile and the conduct from what the adapter declares (no CAD
    process needed), the ``notice`` about the design space's conduct file when there is one
    (``conduct.server_notice``), then the memory index in the room that is left up to ``INSTRUCTIONS_MAX_CHARS``."""
    lines = [profile_text(session, live=False), "Conduct:", *[f"- {line}" for line in conduct(session)]]
    fixed = "\n".join([*lines, notice] if notice else lines)
    memory = memory_index_section(session, INSTRUCTIONS_MAX_CHARS - len(fixed) - 1)
    return f"{fixed}\n{memory}" if memory else fixed


def _missing_ops(session: Session) -> dict[str, str]:
    """Ops this CAD could offer but cannot now (a plugin the engineer has not installed), op name -> how to get them."""
    return catalog.unavailable_ops(session.adapter.unavailable_tools())


def _ops_topic(session: Session) -> str:
    """Every operation in one reply when they fit it; else the index by category (ops:<category>, find:<word>)."""
    return guide_ops.ops_table_text(
        session.available_ops(), session.extensions(), _missing_ops(session), GUIDE_REPLY_LIMIT
    )


def _op_topic(session: Session, name: str) -> str:
    available = session.available_ops()
    if name not in catalog.op_models(session.extensions()):
        close = catalog.close_names(name, available)
        raise ToolError(
            f"unknown op {name!r}; close names: {', '.join(close) or ', '.join(available)}",
            code="unknown_op",
            field_name="topic",
            usage='cad_guide(topic="op:<name>")',
            example='cad_guide(topic="op:extrude")',
        )
    reference = catalog.op_reference(name, session.adapter.guide_topic, session.extensions())
    if name not in available:
        reason = _missing_ops(session).get(name)
        note = f"not available: {reason}" if reason else f"not supported by {session.adapter.name}"
        reference = f"({note})\n{reference}"
    return reference


TRAPS_COMMON = (
    "A face/edge reference must resolve to exactly ONE entity; on failure the error lists the candidates "
    "(centre, normal, area): narrow the selector with normal, center, area_mm2, largest/smallest or nearest_to.",
    "Reference faces by the step that created them (from_op) - ids of faces/edges change after later operations.",
    "A sketch is a step of its own; extrude/cut/revolve/sweep consume it by its step id. A feature refuses a sketch "
    "that is not fully defined by dimensions (the sketch op always makes them so; a hand or run_native sketch is "
    "dimensioned first).",
    "After undo, the undone step ids are free again; ids of the steps still in effect cannot be reused.",
    "Close profiles: rect/square/circle/slot, or lines and arcs that meet end to end.",
    "In detail, model holes with hole (at=[x, y, z] world point) and pockets with cut, not box/cylinder booleans: they "
    "stay editable with op(edit=<id>) and keep their faces. In a concept model (doc set_purpose sketch) box, cylinder "
    "and booleans are fine.",
    "hole has the counterbore, countersink, angled drill point and up_to_face (op:hole): one call, "
    "not run_native. A loop inside a loop in a sketch is a hole (ring) or an island, by the even-odd rule.",
    'The colour of a hole says how it is made: threaded, precision (hole fit="H7", IT8 or finer: a reamer) or plain. '
    "Declare fit on the hole when it needs one; hole_dim then takes it. The colours are a convention of the harness: "
    "read them with doc(action=convention), change them only when the engineer asks, never invent one.",
    'op(edit="<id>") sets a DIMENSION in place on what the CAD already has (sketch: params={dimensions:{"<name>" or '
    '"<entity>.width|height|diameter|...": mm}}; extrude/cut depth, revolve angle, hole size and depth, fillet and '
    "chamfer size, pattern count and spacing): later steps keep their ids and none runs again. What a dimension "
    "cannot say (other entities in a sketch, another plane, a moved hole, another end condition, other edges) "
    "rebuilds the steps after <id>; the reply says it rebuilt and why, and a rebuild that would drop what the "
    "engineer made by hand is refused. If a later step no longer fits (a hole off its face) the reply names it and "
    "nothing changes: edit that step first, or pick another value.",
    "A file opened from disk keeps its bodies as adopted aliases (doc(open) prints them; they cannot be undone) "
    "and its features by their CAD names: inspect(action=tree) lists every feature with its body's Tip, "
    'inspect(action=tree, id="<name>") one in detail (editable properties, the edges it works on).',
    'op(edit="<name>", params={"Radius": 4}) sets properties by the CAD\'s own names (Radius, Size, Length, '
    'Reversed, Midplane, Angle, Type, Suppressed, edges=["Edge12", ...] or edge ids); op(delete="<name>") removes a '
    'feature the CAD\'s way (the Tip moves back; refused naming what needs it unless params={"cascade": true}). '
    "Each is ONE undoable step, checked (valid, one solid, volume change reported) and rolled back when the model "
    "would not rebuild. The tips are shown and the sketches hidden after it; view(action=focus) does that on "
    "demand. A feature a plan op made is edited through its step.",
    "doc(action=save) refuses a file changed outside this server (file_conflict): force=true replaces it, "
    'doc(action="reload") reads it again (force=true drops unsaved work). A fragment that saves the file itself '
    "(doc.saveAs) is not a conflict.",
)


TRAP_SENTENCES = (
    ("A file opened from disk keeps", "trap.open_file"),
    ('op(edit="<name>", params={"Radius"', "trap.edit_by_name"),
    ("doc(action=save) refuses a file changed outside", "trap.save_conflict"),
)
NATIVE_SENTENCES = (
    ("A step is rolled back", "native.rolled_back"),
    ("A fragment that works with TechDraw", "native.recovery"),
)
"""The sentences of ``traps`` and ``native`` that describe FreeCAD (start of the sentence -> key): the adapter of
another CAD answers ``guide_topic("sentence:<key>")`` with its own wording, whole, or with an empty text to drop it."""


def _own_sentences(session: Session, items: tuple[str, ...], keys: tuple[tuple[str, str], ...]) -> list[str]:
    out: list[str] = []
    for item in items:
        key = next((key for start, key in keys if item.startswith(start)), None)
        own = session.adapter.guide_topic(f"sentence:{key}") if key is not None else None
        if own is None:
            out.append(item)
        elif own:
            out.append(own)
    return out


def _traps_topic(session: Session) -> str:
    common = _own_sentences(session, TRAPS_COMMON, TRAP_SENTENCES)
    return "\n".join(f"- {t}" for t in (*common, *session.adapter.known_traps()))


NATIVE_RULES = (
    "run_native runs a fragment of the CAD's own scripting language as ONE step: measured, undoable with undo like "
    "any op, and rolled back on its own when it fails.",
    "Use it only for what no op does; ops are verified and editable, a fragment is neither. Say why in intent. To "
    'change a property of a feature (Radius, Size, Length...) or delete one use op(edit="<CAD name>", params={...}) / '
    'op(delete="<CAD name>"), not a fragment.',
    "A step is rolled back (state unchanged) when the code raises, passes timeout_s, or leaves an object broken: "
    "null or invalid shape, empty boolean, solid of zero volume, a sketch with conflicting or redundant "
    "constraints, a recompute error, or a feature built on a sketch that is not fully defined by dimensions (the "
    "reply says how many degrees of freedom remain; a Block constraint is not a definition). A sketch no feature "
    "uses yet may stay under-defined.",
    "The reply lists what was created / modified / removed, the volume and size change, the printed output and "
    "warnings. A fragment that changed nothing is not recorded and has nothing to undo.",
    "Objects it creates are not ops: reference them from code by name; ops cannot use them as from_op.",
    "If material or route are declared, a fragment cannot change them to something undeclared: the step is undone.",
    "Every call is counted in native-usage.jsonl of the design space (size, hash and intent, not the code).",
    "A fragment that works with TechDraw (views, cosmetic lines, edge formats) gets a recovery copy of the unsaved "
    "document in recuperacion/ first: makeCosmeticLine and formatGeometricEdge closed FreeCAD in real use. Prefer the "
    "sheet's own items (drawing add: line, dim, section) and save before such a script. If FreeCAD closes the reply "
    "names the copy and how to go on from it.",
)


def _native_topic(session: Session) -> str:
    if "run_native" not in session.capabilities.tools:
        return f"run_native is not available with {session.adapter.name}. Use ops; if none covers it, say so."
    environment = session.adapter.native_environment()
    parts = _own_sentences(session, NATIVE_RULES, NATIVE_SENTENCES)
    if environment:
        parts.append(f"Environment ({session.adapter.name}):")
        parts.extend(environment)
    return "\n".join(f"- {line}" if not line.startswith(" ") else line for line in parts)


ASSEMBLY_STEPS = (
    "Each part is its own document, modelled in its OWN axes (origin at the part's datum, not where it will sit): "
    'doc(action="new", name="<part>") ... ops ... doc(action="save"). Features first (extrude/cut/hole), not booleans '
    "of loose bodies.",
    'Then the assembly: doc(action="new", name="<assembly>", kind="assembly"). Parts stay open: doc(action="status") '
    'lists the open documents, doc(action="switch", name=...) picks the one op/undo/inspect act on.',
    'op(name="insert_component", id="<instance>", params={"part": "<part name>", "placement": {"origin": [x,y,z]}}) '
    "makes one instance; insert the same part again for a second one. The part must be saved; the assembly is saved "
    "with its first insert (the links name files relative to it). A saved part that is not open needs no opening: "
    "part = its saved file (the part name with the native extension of the CAD software) loads it for the insert "
    "with no window.",
    'Move an instance: op(name="place_component", params={"component": "<instance>", "placement": {...}}) (absolute '
    "pose). Mates (mate_concentric / mate_coincident / mate_distance) move the instance that owns face b; "
    "fix_component pins one.",
    'Check the fit: inspect(action="interference") lists every pair of instances: overlap (common volume), touching '
    "or clear (minimum distance), and what could not be measured.",
    'Change a part: switch to it, op ..., doc(action="save"); then doc(action="switch", name="<assembly>") brings the '
    "instances up to date (it says when the volume changed). Save the assembly last; reopening it loads its parts.",
    "An assembly needs its parts loaded, not shown: a saved part that is in the assembly loses its window (the "
    "insert says so) and stays loaded; switch to it and its window comes back, switch to the assembly and it goes "
    "again (never while it has unsaved changes). Closing the assembly closes the parts that were loaded only for it. "
    "A file the engineer opened himself is never closed here.",
    "A part that is open cannot be closed while an open assembly uses it; an unsaved part is not what the assembly "
    "shows (it links to the saved file).",
)


JOINT_STEPS = (
    "Moving parts: place the instances at the pose that is the joint's value 0, then "
    'op(name="add_joint", id="<joint>", params={"type": "revolute|slider|fixed", "b": "<instance that moves>", '
    '"a": "<instance it hangs from; omit = ground>", "axis": <{origin, direction} | cylindrical face or circular '
    'edge ref | linear edge ref>, "min": 0, "max": 120}). Revolute value = degrees, positive right-handed about the '
    "axis direction (flip the direction for the other sense); slider value = mm along it; the limits must include 0. "
    'op(name="set_joint", params={"joint": "<joint>", "value": 30}) moves b there (absolute, from the pose the joint '
    f"was made at) and undo takes it back. A jointed instance is no longer placed by hand or mated.{CAD_NOTE}",
    'Clearance over the whole motion: inspect(action="sweep", id="<joint>", range=[from, to, step]) drives the joint '
    "through the positions, measures the moving instance against every other at each, and puts it back. It reports "
    "the FIRST position checked where they overlap (pair, common volume), for each pair the least clearance up to "
    "that position and where, and the step used. It checks only those positions (at most 721), never what lies "
    'between them: say "checked every 1 degree", not "no collision"; use a finer step near a tight case. Never write '
    "a rotation sweep by hand with run_native.",
)


PARASOLID_UNREADABLE = "Parasolid (.x_t, .x_b) cannot be read: ask the supplier for STEP (AP214/AP242). "
"""True of FreeCAD; a CAD that reads Parasolid (``Adapter.import_suffixes``) says so instead."""

NAMED_FORMATS = "(STEP or IGES)"  # what IMPORT_STEPS says it reads; a CAD that reads Parasolid says so there

IMPORT_STEPS = (
    'A customer part (STEP or IGES) comes in with doc(action="import", path="<file>.step", name="<part>"): a new part '
    f"document, saved at once as <name>{CAD_SUFFIX}, one body per solid, in the coordinates of the file (no "
    "recentring; the "
    "length unit is converted to mm and the result says which unit the file declared). The result lists solids, "
    "faces, volume, size, valid/closed per body, and warns when the file holds surfaces only (no meaningful volume). "
    f"{PARASOLID_UNREADABLE}A large assembly takes minutes "
    "(progress is reported while it runs). The imported bodies get the CAD's cheap validity check only; "
    'inspect(action="validity") runs the deep self-intersection check on all of them (slow on a large file). An IGES '
    "often arrives as loose faces: they become ONE surface body (no volume). sew=true (sew_tolerance_mm, default 0.01) "
    "sews them and makes a solid of every closed shell, and the result counts the free edges (gaps) that remain; a "
    'surface body still serves as a reference: inspect(action="interference") measures its distance to other bodies.',
    'Find the features to locate and clamp on: inspect(action="holes") lists the holes by diameter (axis, depth, '
    'through or blind, face ids); inspect(action="faces", surface="plane", normal=[...]) the flat faces; then '
    'view(action="show", ids=[...]) points the engineer at them to confirm which ones are the datums.',
    'Then doc(action="new", name="<tool>", kind="assembly") and insert_component(part="<imported name>", '
    "placement={}) at its OWN coordinates (never move the customer part to suit the tool), and build the tool around "
    "it: its own parts in their own documents, placed relative to the customer part's holes and faces; check with "
    'inspect(action="interference").',
    "Imported bodies are references with no feature history: op(edit) cannot change an imported hole or boss. To "
    "design on the solid itself, use the normal ops on it (sketch on a face + extrude/cut, hole with at=[x, y, z], "
    "fillet, chamfer); to change what is already there add material or cut on top, or defeature with run_native.",
)


def _assembly_topic(session: Session) -> str:
    tools = session.capabilities.tools
    if "place_component" not in tools and "insert_component" not in tools:
        return f"assemblies are not available with {session.adapter.name}."
    steps = list(ASSEMBLY_STEPS)
    if "add_joint" in tools:
        note = session.adapter.guide_topic("note:joint") or ""
        steps[5:5] = (step.replace(CAD_NOTE, note) for step in JOINT_STEPS)  # after the interference check
    text = "\n".join(f"{i}. {step}" for i, step in enumerate(steps, start=1))
    if "import_geometry" in tools:
        suffix = session.adapter.suffix_for(DocumentKind.part)
        reads = [s for s in session.adapter.import_suffixes() if s in PARASOLID_SUFFIXES]
        parasolid = f"Parasolid ({', '.join(reads)}) is read like STEP. " if reads else PARASOLID_UNREADABLE
        stated = "(STEP, IGES or Parasolid)" if reads else NAMED_FORMATS
        text += "\n\nWith a customer's part (the tool is built around it):\n" + "\n".join(
            f"- {step.replace(CAD_SUFFIX, suffix).replace(PARASOLID_UNREADABLE, parasolid)}".replace(
                NAMED_FORMATS, stated
            )
            for step in IMPORT_STEPS
        )
    return text


def _drawing_topic(session: Session, part: str = "") -> str:
    if "create_drawing" not in session.capabilities.tools:
        return f"drawings are not available with {session.adapter.name}."
    flow = design_memory.load_brief(session.settings).drawing_flow
    if part == TOPIC_COMPOSE and flow != "sheet":
        return (
            "drawing:compose exists only in a project that builds sheets in one order (drawing_flow: sheet in its "
            "brief.yaml: the engineer's choice); this one builds them in steps (cad_guide(topic=drawing))"
        )
    return drawing_text(session.capabilities.tools, part, flow)


def _pointing_topic(session: Session) -> str:
    tools = session.capabilities.tools
    if "get_selection" not in tools:
        return (
            "pointing needs the visible CAD window (COTAFORGE_VISIBLE=1): the engineer selects there, you show there."
        )
    return pointing_text("create_drawing" in tools)


def guide(session: Session, topic: str) -> str:
    """The answer for ``topic``, at most ``GUIDE_REPLY_LIMIT`` characters: a longer text is split in pages and the reply
    says how to ask for the next (``topic#2``)."""
    key, page = guide_ops.split_page(topic.strip())
    return guide_ops.paginate(_answer(session, key), key, page, GUIDE_REPLY_LIMIT)


def _answer(session: Session, key: str) -> str:
    own = None if key.startswith(("note:", "sentence:")) else session.adapter.guide_topic(key)
    if own is not None:  # this CAD says it its own way (a drawing guide that is not FreeCAD's, say)
        return own
    if key == "profile":
        return profile_text(session)
    if key == "ops":
        return _ops_topic(session)
    if key.startswith("ops:"):
        return guide_ops.ops_of_category(
            session.available_ops(), session.extensions(), key[4:].strip(), _missing_ops(session)
        )
    if key.startswith("find:"):
        return guide_ops.find_ops(session.available_ops(), session.extensions(), key[5:])
    if key.startswith("op:"):
        return _op_topic(session, key[3:].strip())
    if key == "terms":
        return terms_table(session.available_ops(), _missing_ops(session))
    if key == "traps":
        return _traps_topic(session)
    if key == "native":
        return _native_topic(session)
    if key == "recipe":
        return recipes.plate_text()
    if key == "drawing":
        return _drawing_topic(session)
    if key in SUBTOPICS or key == TOPIC_COMPOSE:
        return _drawing_topic(session, key)
    if key == "assembly":
        return _assembly_topic(session)
    if key == "pointing":
        return _pointing_topic(session)
    if key == "memory":
        return MEMORY_TEXT
    raise ToolError(
        f"unknown topic {key!r}; topics: {', '.join(TOPICS)}",
        code="unknown_topic",
        field_name="topic",
        usage='cad_guide(topic="profile|ops|ops:<category>|find:<word>|op:<name>|terms|traps|native|recipe|drawing|drawing:items|assembly|memory|pointing")',
        example='cad_guide(topic="op:hole")',
    )
