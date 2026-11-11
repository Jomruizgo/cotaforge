"""Names of the folders and files the harness keeps, and how an old project is still read.

Every name is English. Before 0.10 the names were Spanish (``memoria/``, ``planos/``, ``supuestos-y-decisiones.md``...)
and an engineer's folder still has them. One rule covers all of it: :func:`resolve` answers the English path when it
exists or when neither exists (a new project), and the Spanish one when only that exists. The harness never renames what
the engineer has: it reads and writes where his files already are.
"""

from pathlib import Path

MEMORY = "memory"
CALCULATIONS = "calculations"
DRAWINGS = "drawings"
REPORTS = "reports"
CATALOG = "catalog"
LIBRARY = "library"
RECOVERY = "recovery"
INDEXES = "indexes"
ASSUMPTIONS = "assumptions-and-decisions.md"
FRICTION = "friction.md"
LIBRARY_TOPICS = "topics.yaml"
LIBRARY_INDEX = "library.sqlite"
CONDUCT_PROPOSAL_SUFFIX = ".new"

INTENT_SUFFIX = "-intent.md"
QUESTIONS_STEM = "-questions"
WINDOW_SUFFIX = "-window.json"

_WHOLE = {
    MEMORY: "memoria",
    CALCULATIONS: "calculos",
    DRAWINGS: "planos",
    REPORTS: "informes",
    CATALOG: "catalogo",
    LIBRARY: "biblioteca",
    RECOVERY: "recuperacion",
    INDEXES: "indices",
    ASSUMPTIONS: "supuestos-y-decisiones.md",
    FRICTION: "friccion.md",
    LIBRARY_TOPICS: "temas.yaml",
    LIBRARY_INDEX: "biblioteca.sqlite",
    "CLAUDE.md.new": "CLAUDE.md.nuevo",
}
_SUFFIXES = {
    INTENT_SUFFIX: "-intencion.md",
    QUESTIONS_STEM + ".": "-preguntas.",  # -questions.jsonl / .md / .lock
    WINDOW_SUFFIX: "-ventana.json",
    ".copy.json": ".copia.json",  # the manifest of a recovery copy
}

LEGACY_NAMES: dict[str, str] = {**_WHOLE, **_SUFFIXES}
"""Every English name (or suffix) with the Spanish one it replaced: the whole record of the rename."""

# The role and author tokens of the drawing channel and the ``by`` of a reply: English now, the Spanish ones still read.
TOKEN_ALIASES: dict[str, str] = {
    "disenador": "designer",
    "diseñador": "designer",
    "dibujante": "drafter",
    "ingeniero": "engineer",
    "fabricante": "fabricator",
    "revisor": "reviewer",
}


def legacy_name(name: str) -> str | None:
    """The Spanish name that ``name`` replaced, or ``None`` when ``name`` is not one of the renamed ones."""
    if name in _WHOLE:
        return _WHOLE[name]
    for new, old in _SUFFIXES.items():
        if new in name:
            return name.replace(new, old, 1)
    return None


def resolve(parent: Path, name: str) -> Path:
    """``parent / name`` when it exists or when the old Spanish name does not; the Spanish one when only it exists."""
    path = parent / name
    old = legacy_name(name)
    if old is not None and not path.exists() and (parent / old).exists():
        return parent / old
    return path


def alias_token(value: str) -> str:
    """The English token for a role, author or agent name given in either language (anything else comes back as is)."""
    return TOKEN_ALIASES.get(value.strip().lower(), value)
