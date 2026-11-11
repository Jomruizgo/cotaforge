"""Topics of the library: one folder each, with synonyms, listed in ``topics.yaml`` at the library root.

The product ships a starting list (package data) that is copied to the root the first time a document is filed; after
that the file is the engineer's. A topic the list does not know is added with its synonyms, appended to the file so
the engineer's comments and order stay as they are.
"""

import json
from importlib import resources
from pathlib import Path

import yaml
from cotaforge import paths

from cotaforge_mcp.library.files import ascii_slug, write_atomic

TOPICS_FILE = paths.LIBRARY_TOPICS
MAX_TOPIC_CHARS = 60


class TopicsError(ValueError):
    """``topics.yaml`` cannot be read or the topic cannot be added."""


def shipped_text() -> str:
    return (resources.files("cotaforge_mcp") / "data" / "library" / TOPICS_FILE).read_text(encoding="utf-8")


def parse_topics(text: str) -> dict[str, list[str]]:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        msg = f"{TOPICS_FILE} is not valid YAML ({str(error).splitlines()[0][:80]}); fix it by hand"
        raise TopicsError(msg) from error
    if data is None:
        return {}
    if not isinstance(data, dict):
        msg = f"{TOPICS_FILE} must map each topic to its list of synonyms; fix it by hand"
        raise TopicsError(msg)
    table: dict[str, list[str]] = {}
    for name, aliases in data.items():
        slug = ascii_slug(str(name))
        if not slug:
            continue
        found = [aliases] if isinstance(aliases, str) else list(aliases or [])
        table[slug] = [str(a) for a in found if a is not None and str(a).strip()]
    return table


class Topics:
    def __init__(self, root: Path) -> None:
        self.path = paths.resolve(root, TOPICS_FILE)

    def ensure(self) -> None:
        """Copy the shipped list to the library root when there is none yet."""
        if not self.path.exists():
            write_atomic(self.path, shipped_text().encode("utf-8"))

    def table(self) -> dict[str, list[str]]:
        self.ensure()
        return parse_topics(self.path.read_text(encoding="utf-8"))

    def resolve(self, name: str, table: dict[str, list[str]] | None = None) -> str | None:
        """The canonical topic for ``name`` (its slug, or a synonym of one), or ``None`` when it is unknown."""
        table = self.table() if table is None else table
        key = ascii_slug(name)
        if key in table:
            return key
        for slug, aliases in table.items():
            if key in {ascii_slug(alias) for alias in aliases}:
                return slug
        return None

    def add(self, name: str, aliases: list[str]) -> str:
        """Add a new topic with its synonyms and return its slug."""
        slug = ascii_slug(name)[:MAX_TOPIC_CHARS].strip("-")
        if not slug:
            msg = f"{name!r} is not a topic name: use words (e.g. 'rodamientos-y-ejes')"
            raise TopicsError(msg)
        words = [a.strip() for a in aliases if a and a.strip()]
        if not words:
            msg = "a new topic needs topic_aliases: at least one synonym (Spanish, with and without accents)"
            raise TopicsError(msg)
        original = self.path.read_text(encoding="utf-8") if self.path.exists() else shipped_text()
        parse_topics(original)  # refuses to append to a file the engineer broke
        lines = [f"{slug}:", *(f"  - {json.dumps(word, ensure_ascii=False)}" for word in words)]
        updated = original.rstrip("\n") + "\n\n" + "\n".join(lines) + "\n"
        if slug not in parse_topics(updated):  # pragma: no cover - the appended block is always valid YAML
            msg = f"could not add {slug!r} to {TOPICS_FILE}"
            raise TopicsError(msg)
        write_atomic(self.path, updated.encode("utf-8"))
        return slug
