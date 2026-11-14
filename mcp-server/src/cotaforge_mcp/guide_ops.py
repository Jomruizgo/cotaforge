"""How the agent reaches every operation of the CAD without one reply carrying them all: a short index by category,
one category in full, a word search, and pages for any guide text longer than a reply.

``cad_guide`` answers at most ``GUIDE_REPLY_LIMIT`` characters. A text that is longer is never cut: it is split in pages
and the reply says which page it is and how to ask for the next (``#2`` after the topic).
"""

import re
import unicodedata
from collections.abc import Mapping

from cotaforge.contract import OperationSpec
from cotaforge.ir import OP_CATEGORY

from cotaforge_mcp import catalog
from cotaforge_mcp.reply import ToolError

GUIDE_REPLY_LIMIT = 6000
"""Characters of one ``cad_guide`` reply: the context the agent spends on a text is bounded; the rest is on a page."""
PAGE_FOOTER_ROOM = 200
SEARCH_ROWS = 30

CATEGORY_LABELS = {
    "everyday": "features",
    "multibody": "bodies",
    "sheet_metal": "sheet metal",
    "assembly": "assembly",
    "other": "other",
}
"""The words the agent sees for the common categories; an adapter's own category is shown as written."""
COVERS = {
    "features": "sketch, extrude, cut, revolve, sweep, hole, patterns, fillet, chamfer, shell, draft, mirror",
    "bodies": "boxes, cylinders, booleans, move/rotate/copy/split bodies",
    "sheet metal": "base flange, edge flange, flat pattern",
    "assembly": "insert/place components, mates, joints, interference",
}

# What a designer may type in Spanish -> the English root the names and purposes are written with.
SPANISH_TERMS = {
    "desmoldeo": "draft",
    "contraccion": "shrink_body",
    "contracción": "shrink_body",
    "merma": "shrink_body",
    "shrinkage": "shrink_body",
    "cavidad": "shrink_body",
    "particion": "mould_split",
    "partición": "mould_split",
    "postizo": "mould_split",
    "macho": "mould_split",
    "molde": "mould_split",
    "corredera": "slide_mechanism",
    "expulsor": "mould_component",
    "columna": "mould_component",
    "casquillo": "mould_component",
    "desmold": "draft",
    "salida": "draft",
    "agujero": "hole",
    "orificio": "hole",
    "taladro": "hole",
    "barreno": "hole",
    "perforacion": "hole",
    "avellanado": "countersink",
    "refrentado": "counterbore",
    "rosca": "thread",
    "roscado": "thread",
    "redondeo": "fillet",
    "redondear": "fillet",
    "chaflan": "chamfer",
    "bisel": "chamfer",
    "vaciado": "shell",
    "vaciar": "shell",
    "ranura": "slot",
    "nervio": "rib",
    "pestana": "flange",
    "refuerzo": "rib",
    "cascara": "shell",
    "espejo": "mirror",
    "simetria": "mirror",
    "simetrico": "mirror",
    "barrido": "sweep",
    "revolucion": "revolve",
    "revolucionar": "revolve",
    "extrusion": "extrude",
    "extruir": "extrude",
    "corte": "cut",
    "cortar": "cut",
    "bolsillo": "pocket",
    "boceto": "sketch",
    "croquis": "sketch",
    "perfil": "profile",
    "plano": "plane",
    "eje": "axis shaft",
    "patron": "pattern",
    "matriz": "pattern",
    "repetir": "pattern",
    "union": "union",
    "fusion": "union",
    "restar": "subtract",
    "interseccion": "intersect",
    "mover": "move",
    "trasladar": "translate",
    "girar": "rotate",
    "rotar": "rotate",
    "copiar": "copy",
    "dividir": "split",
    "chapa": "sheet",
    "lamina": "sheet",
    "plegado": "flange",
    "pliegue": "bend",
    "doblez": "bend",
    "desplegado": "unfold",
    "desarrollo": "flat",
    "soldadura": "weld",
    "cordon": "weld",
    "estructura": "weldment",
    "ensamble": "assembly",
    "ensamblaje": "assembly",
    "conjunto": "assembly",
    "componente": "component",
    "pieza": "part",
    "restriccion": "mate",
    "relacion": "mate",
    "concentrico": "concentric",
    "coincidente": "coincident",
    "distancia": "distance",
    "articulacion": "joint",
    "interferencia": "interference",
    "colision": "interference",
    "superficie": "surface",
    "curva": "curve",
    "espiral": "helix",
    "helice": "helix",
    "resorte": "spring",
    "amortiguador": "damper",
    "gravedad": "gravity",
    "carrera": "stroke",
    "manivela": "crank",
    "biela": "rod",
    "corredera": "slider",
    "motor": "motor torque",  # "par motor": the lookup is word by word
    "contacto": "contact",
    "tornillo": "screw bolt",
    "perno": "bolt",
    "tuerca": "nut",
    "arandela": "washer",
    "cojinete": "bearing",
    "rodamiento": "bearing",
    "vista": "view",
    "cota": "dimension",
    "mecanizado": "machining",
    "fresado": "milling",
    "torneado": "turning",
    "paralela": "parallel",
    "paralelo": "parallel",
    "perpendicular": "perpendicular",
    "tangente": "tangent",
    "angulo": "angle",
    "simetrica": "symmetric",
    "engranaje": "gear",
    "pinon": "pinion",
    "cremallera": "rack",
    "leva": "cam",
    "bisagra": "hinge",
    "polea": "pulley",
    "cadena": "chain",
    "chaveta": "key",
    "chavetero": "keyway key",
    "hexagono": "hexagon polygon",
    "poligono": "polygon",
    "elipse": "ellipse",
    "texto": "text",
    "grabado": "engrave text",
    "tolerancia": "tolerance",
    "ajuste": "fit",
    "espesor": "thickness",
    "movimiento": "motion",
    "mecanismo": "mechanism motion",
    "simulacion": "simulation motion",
    "tubo": "tube pipe",
    "tuberia": "pipe tube",
    "anillo": "ring",
    "garganta": "groove",
    "moleteado": "knurl",
    "hueco": "shell hollow",
    "explosion": "explode",
    "lista": "list",
    "cara": "face",
    "arista": "edge",
    "exterior": "external",
    "interior": "internal",
}
"""A word may stand for several English words (separated by spaces): the operation holds any of them."""

STOP_WORDS = frozenset(
    "de del la las el los lo un una unos unas en y o u a al con sin por para que se es son su sus entre sobre desde "
    "hasta como mas muy dos tres hacer hago hazme crear crea quiero necesito poner pon agregar anadir dibujar "
    "the an of in on for to with and or by at from into is are how do does i want need make create add draw it this "
    "that one two three some".split()
)
"""Spanish and English words that say nothing about the operation: a request is mostly made of them."""
NATIVE_HINT = "If none does it, run_native reaches what the CAD software has and no operation offers."


def _fold(text: str) -> str:
    """Lowercase without accents: 'Revolución' and 'revolucion' are the same word."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def _key(text: str) -> str:
    """What two spellings of one category share: 'Sheet metal', 'sheet_metal' and 'sheet-metal'."""
    return re.sub(r"[^a-z0-9]", "", _fold(text))


def category_of(name: str, extensions: Mapping[str, OperationSpec] | None = None) -> str:
    """The category an operation is listed under: the one its adapter declared, else the common vocabulary's
    (``features``, ``bodies``, ``sheet metal``, ``assembly``), else ``other``."""
    spec = (extensions or {}).get(name)
    if spec is not None and spec.category.strip():
        return spec.category.strip()
    return CATEGORY_LABELS[OP_CATEGORY.get(name, "other")]


def group_ops(names: list[str], extensions: Mapping[str, OperationSpec] | None = None) -> dict[str, list[str]]:
    """Category -> its operations, the common categories first, ``other`` last, the rest as they appear."""
    groups: dict[str, list[str]] = {}
    spelled = {_key(label): label for label in CATEGORY_LABELS.values()}  # 'Features' and 'features' are one group
    for name in names:
        label = category_of(name, extensions)
        groups.setdefault(spelled.setdefault(_key(label), label), []).append(name)
    known = list(CATEGORY_LABELS.values())
    rank = {label: i for i, label in enumerate(known)}
    middle = len(known)
    order = sorted(groups, key=lambda label: rank.get(label, middle) if label != "other" else middle + 1)
    # sorted() is stable: custom categories keep their order of appearance between the common ones and ``other``
    return {label: groups[label] for label in order}


def _missing_note(missing: Mapping[str, str]) -> str:
    if not missing:
        return ""
    why = dict.fromkeys(missing.values())
    return f"\nnot available here: {', '.join(missing)}. " + " ".join(why)


def ops_table_text(
    names: list[str], extensions: Mapping[str, OperationSpec] | None, missing: Mapping[str, str], limit: int
) -> str:
    """Every operation, one row each, when that fits one reply (the text the agent always had); else the index."""
    text = catalog.ops_table(names, extensions) + _missing_note(missing)
    if len(text) <= limit:
        return text
    return ops_index(names, extensions, missing)


def ops_index(names: list[str], extensions: Mapping[str, OperationSpec] | None, missing: Mapping[str, str]) -> str:
    groups = group_ops(names, extensions)
    lines = [
        f"{len(names)} operations in {len(groups)} categories. One category in full: "
        'cad_guide(topic="ops:<category>"); by a word, English or Spanish: cad_guide(topic="find:<word>"); '
        'one operation: cad_guide(topic="op:<name>"); all, in pages: cad_guide(topic="ops:all").'
    ]
    for label, members in groups.items():
        covers = COVERS.get(label) or ", ".join(members[:6]) + (", ..." if len(members) > 6 else "")
        lines.append(f"- {label} ({len(members)}): {covers}")
    return "\n".join(lines) + _missing_note(missing)


def ops_of_category(
    names: list[str], extensions: Mapping[str, OperationSpec] | None, wanted: str, missing: Mapping[str, str]
) -> str:
    """One category in full (``ops:<category>``), or every operation (``ops:all``); the pages are cut by the caller."""
    if _key(wanted) == "all":
        return catalog.ops_table(names, extensions) + _missing_note(missing)
    groups = group_ops(names, extensions)
    aliases = {_key(raw): label for raw, label in CATEGORY_LABELS.items()}  # 'everyday' also names 'features'
    wanted_key = _key(wanted)
    for label, members in groups.items():
        if _key(label) == wanted_key or aliases.get(wanted_key) == label:
            return catalog.ops_table(members, extensions)
    raise ToolError(
        f"unknown category {wanted!r}; categories: {', '.join(groups)}",
        code="unknown_topic",
        field_name="topic",
        usage='cad_guide(topic="ops:<category>")',
        example=f'cad_guide(topic="ops:{next(iter(groups), "features")}")',
    )


def _words(text: str) -> list[str]:
    """The words of a request that say something: no stop words, no numbers, nothing under three letters."""
    found = re.findall(r"[a-z0-9]+", _fold(text))
    return [w for w in found if len(w) >= 3 and not w.isdigit() and w not in STOP_WORDS]


def _expand(word: str) -> set[str]:
    """The word as typed, without a plural, and the English words a Spanish word stands for."""
    found = {word}
    if len(word) > 3 and word.endswith("s"):
        found.add(word[:-1])
    for spanish, english in SPANISH_TERMS.items():
        # 'roscas', 'roscado' say rosca; 'revolucion' is the start of 'revolucionar'; 'ejecutar' is not 'eje'
        if (word.startswith(spanish) and len(word) - len(spanish) <= 3) or (
            len(word) >= 5 and spanish.startswith(word) and len(spanish) - len(word) <= 3
        ):
            found.update(english.split())
    return found


def _is_word_of(term: str, word: str) -> bool:
    """``term`` is ``word``, its plural or (from five letters) its start: not 'mate' in 'material'."""
    return word == term or word in (f"{term}s", f"{term}es") or (len(term) >= 5 and word.startswith(term))


def find_ops(names: list[str], extensions: Mapping[str, OperationSpec] | None, query: str) -> str:
    """The operations whose name, purpose or category holds the words of ``query`` (English or Spanish), best first:
    the more words an operation holds the better, a word in its name before one in its purpose."""
    words = list(dict.fromkeys(_words(query)))
    if not _fold(query).strip():
        raise ToolError(
            "find: say a word to look for",
            code="missing_field",
            field_name="topic",
            usage='cad_guide(topic="find:<word>")',
            example='cad_guide(topic="find:draft")',
        )
    concepts = [_expand(w) for w in words]
    rows: list[tuple[int, list[bool], list[bool]]] = []  # position, concept held by the purpose or name, by the name
    for position, name in enumerate(names):
        in_name = _fold(name.replace("_", " ")).split()
        purpose = f"{catalog.purpose(name, extensions)} {category_of(name, extensions)}"
        in_text = in_name + re.findall(r"[a-z0-9]+", _fold(purpose))
        hits = [any(_is_word_of(t, w) for t in terms for w in in_text) for terms in concepts]
        if any(hits):
            named = [any(_is_word_of(t, w) for t in terms for w in in_name) for terms in concepts]
            rows.append((position, hits, named))
    reached = [any(hits[i] for _, hits, _ in rows) for i in range(len(concepts))]
    # a word only a few operations hold says more than one that most hold ('paralela' against 'cara')
    weight = [1 / max(1, sum(hits[i] for _, hits, _ in rows)) for i in range(len(concepts))]
    scored = sorted(
        (
            -sum(hits),
            -sum(w for w, h in zip(weight, hits, strict=True) if h),
            -sum(named),
            position,
            names[position],
        )
        for position, hits, named in rows
    )
    if not scored:
        groups = ", ".join(group_ops(names, extensions))
        return (
            f'no operation matches {query!r}. Categories: {groups}; cad_guide(topic="ops:<category>") lists one. '
            f"{NATIVE_HINT}"
        )
    best = -scored[0][0]
    found = [row[-1] for row in scored if -row[0] == best]
    shown = found[:SEARCH_ROWS]
    text = catalog.ops_table(shown, extensions)
    if len(found) > len(shown):
        text += f'\n... {len(found) - len(shown)} more match: use more words, or cad_guide(topic="ops:<category>").'
    if best < len(words):
        lost = [w for w, ok in zip(words, reached, strict=True) if not ok]
        gap = f"; no operation has: {', '.join(lost)}" if lost else ""
        text = (
            f"No operation holds all of {', '.join(words)} ({best} of {len(words)} at best{gap}). The rows below hold "
            f"{best}: they may not do what was asked. {NATIVE_HINT}\n{text}"
        )
    return text


def split_page(topic: str) -> tuple[str, int]:
    """``ops:features#2`` -> (``ops:features``, 2); a topic without ``#N`` is page 1."""
    head, sep, tail = topic.rpartition("#")
    if sep and tail.isdigit() and head:
        return head, int(tail)
    return topic, 1


def paginate(text: str, topic: str, page: int, limit: int = GUIDE_REPLY_LIMIT) -> str:
    """``text`` when it fits ``limit``; else page ``page`` of it, cut at line ends, saying how to ask for the next."""
    if len(text) <= limit:
        if page != 1:
            raise ToolError(
                f"{topic!r} has no page {page}: it is one reply",
                code="unknown_topic",
                field_name="topic",
                usage=f'cad_guide(topic="{topic}")',
            )
        return text
    room = limit - PAGE_FOOTER_ROOM
    pages: list[list[str]] = [[]]
    used = 0
    for line in text.split("\n"):
        while len(line) > room:  # one line longer than a page: split it where it must
            if pages[-1]:
                pages.append([])
                used = 0
            pages[-1].append(line[:room])
            pages.append([])
            used = 0
            line = line[room:]  # noqa: PLW2901
        if used + len(line) + 1 > room and pages[-1]:
            pages.append([])
            used = 0
        pages[-1].append(line)
        used += len(line) + 1
    pages = [p for p in pages if p]
    if not 1 <= page <= len(pages):
        raise ToolError(
            f"{topic!r} has {len(pages)} pages, not page {page}",
            code="unknown_topic",
            field_name="topic",
            usage=f'cad_guide(topic="{topic}#<1..{len(pages)}>")',
        )
    body = "\n".join(pages[page - 1])
    if page < len(pages):
        return f'{body}\n... page {page}/{len(pages)}; the rest: cad_guide(topic="{topic}#{page + 1}")'
    return f"{body}\n... page {page}/{len(pages)} (last)"
