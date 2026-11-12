"""File-backed memory store with two scopes (project and user).

Every call reads the folders, so a change made by another process (or by hand) is seen immediately. What is kept
between calls is only the parse of a file that has not changed (same path, modification time and size): parsing the
YAML of the ~65 base entries on every call cost about 0.3 s each time on Windows. A broken file never breaks the store:
it is collected as a load problem and skipped.
"""

import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from cotaforge import paths
from cotaforge.memory.index import INDEX_FILENAME, SectorView, combined_index, render_index
from cotaforge.memory.legacy_ids import LEGACY_BY_NEW, LEGACY_IDS
from cotaforge.memory.model import (
    EntryError,
    EntryStatus,
    MemoryEntry,
    Scope,
    StoredEntry,
    parse_entry,
    render_entry,
)
from cotaforge.memory.sectors import SectorTable, base_sector_table, load_sector_table
from cotaforge.memory.text import normalize_text

HOME_ENV_VAR = "COTAFORGE_HOME"
USER_MEMORY_DIRNAME = paths.MEMORY
PROJECT_MEMORY_DIRNAME = paths.MEMORY
BASE_MEMORY_DIRNAME = "base"

_PARSED: dict[tuple[str, str], tuple[tuple[int, int, int], MemoryEntry]] = {}
"""The last good parse of each entry file: ``(path, expected id)`` -> ``((mtime_ns, size, inode), entry)``. Entries are
frozen, so sharing one between calls is safe; a file that changed (or fails to parse) is parsed again."""


def _parsed(path: Path) -> MemoryEntry:
    """``parse_entry`` of ``path``, reusing the last parse while the file's modification time and size are the same."""
    stat = path.stat()
    stamp = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
    key = (str(path), path.stem)
    kept = _PARSED.get(key)
    if kept is not None and kept[0] == stamp:
        return kept[1]
    entry = parse_entry(path.read_text(encoding="utf-8"), expected_id=path.stem)
    _PARSED[key] = (stamp, entry)
    return entry


class MemoryStoreError(Exception):
    """An operation cannot be done (no folder for the scope, unknown id, ...)."""


class LoadProblem(BaseModel):
    """A file of a memory folder that could not be loaded."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scope: Scope
    file: Path
    message: str


def user_memory_dir(env: Mapping[str, str] | None = None) -> Path:
    """``<COTAFORGE_HOME>/memory`` when ``COTAFORGE_HOME`` is set, else ``~/.cotaforge/memory`` (an old ``memoria``
    folder is used while there is no ``memory``)."""
    environment = os.environ if env is None else env
    home = environment.get(HOME_ENV_VAR, "").strip()
    base = Path(home) if home else Path.home() / ".cotaforge"
    return paths.resolve(base, USER_MEMORY_DIRNAME)


def project_memory_dir(design_root: Path) -> Path:
    """The project memory folder: ``memory/`` at the root of the design space (``memoria/`` in an old project)."""
    return paths.resolve(design_root, PROJECT_MEMORY_DIRNAME)


def base_memory_dir() -> Path:
    """The base set shipped with the product (read-only)."""
    return Path(__file__).resolve().parent / BASE_MEMORY_DIRNAME


def _require_canonical_sectors(entry: MemoryEntry, table: SectorTable) -> None:
    """Every sector a base entry names (its examples and its own ``sector``) must be a canonical sector id."""
    named = [example.sector for example in entry.examples] + ([entry.sector] if entry.sector else [])
    unknown = sorted({text for text in named if not table.is_canonical(text)})
    if unknown:
        known = ", ".join(table.ids)
        raise EntryError(f"base sector(s) {unknown} are not canonical ids of sectors.yaml (ids: {known})")


def _atomic_write(path: Path, text: str) -> None:
    """Write ``text`` (UTF-8, ``\\n`` line ends) to ``path`` via a temp file in the same folder."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(text.encode("utf-8"))
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


_STOP = frozenset("de la el los las un una y o en con para por que del al se es a the of and to in for".split())


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"\w+", normalize_text(text)) if len(w) > 3 and w not in _STOP}


class MemoryStore:
    """The memory of a design: ``project_dir`` and ``user_dir`` are the memory folders themselves
    (``<design>/memory`` and ``~/.cotaforge/memory``); either may be None (that scope is absent)."""

    def __init__(
        self,
        project_dir: Path | None = None,
        user_dir: Path | None = None,
        base_dir: Path | None = None,
        cad: str | None = None,
    ) -> None:
        self.cad = cad
        """The key of the CAD software this store serves (``Adapter.cad_key``): an entry marked for another one, or
        for any when this is ``None``, is hidden from ``entries``, ``search`` and ``similar``."""
        self._dirs: dict[Scope, Path | None] = {Scope.project: project_dir, Scope.user: user_dir, Scope.base: base_dir}

    @classmethod
    def for_design(
        cls,
        design_root: Path | None,
        env: Mapping[str, str] | None = None,
        *,
        base: bool = True,
        cad: str | None = None,
    ) -> "MemoryStore":
        """The usual store: ``<design_root>/memory``, the user folder (see ``user_memory_dir``) and the base set
        that ships with the product (``base=False`` leaves it out)."""
        project = project_memory_dir(design_root) if design_root is not None else None
        return cls(project, user_memory_dir(env), base_memory_dir() if base else None, cad)

    def directory(self, scope: Scope) -> Path | None:
        return self._dirs[scope]

    # ---- reading -------------------------------------------------------------------------------

    def _load_scope(self, scope: Scope) -> tuple[list[StoredEntry], list[LoadProblem]]:
        directory = self._dirs[scope]
        entries: list[StoredEntry] = []
        problems: list[LoadProblem] = []
        if directory is None or not directory.is_dir():
            return entries, problems
        canonical = base_sector_table(directory) if scope is Scope.base else None
        for path in sorted(directory.glob("*.md")):
            if path.name == INDEX_FILENAME:
                continue
            try:
                entry = _parsed(path)
                if canonical is not None:
                    _require_canonical_sectors(entry, canonical)
            except (EntryError, OSError, UnicodeDecodeError) as error:
                problems.append(LoadProblem(scope=scope, file=path, message=str(error)))
                continue
            if scope is not Scope.base and entry.id in LEGACY_IDS:  # an override or a switch-off by the old base id
                entry = entry.model_copy(update={"id": LEGACY_IDS[entry.id]})
            entries.append(StoredEntry(entry=entry, scope=scope, path=path))
        return entries, problems

    def problems(self) -> list[LoadProblem]:
        """Files that could not be loaded (and were skipped), project first, then user, then the base set."""
        found: list[LoadProblem] = []
        for scope in (Scope.project, Scope.user, Scope.base):
            found.extend(self._load_scope(scope)[1])
        return found

    # ---- sectors and warnings ------------------------------------------------------------------

    def sector_table_and_problems(self) -> tuple[SectorTable, list[str]]:
        """The sector table (base, then the user's and the project's ``sectors.yaml``, which win in that order) and
        the problems of those engineer files."""
        return load_sector_table(
            self._dirs[Scope.base], [("user", self._dirs[Scope.user]), ("project", self._dirs[Scope.project])]
        )

    def sector_table(self) -> SectorTable:
        return self.sector_table_and_problems()[0]

    def warnings(self) -> list[str]:
        """Things that load fine but look wrong: a ``related`` id that does not exist, a ``replaces`` that names no base
        entry, an example sector the table does not know (it is then 'not covered'), a broken ``sectors.yaml``. A
        warning never skips a file."""
        table, found = self.sector_table_and_problems()
        merged = self._merged()
        for item in merged.values():
            where = f"{item.scope.value}/{item.path.name}"
            found.extend(f"{where}: {text}" for text in self.entry_warnings(item, merged, table))
        return found

    def entry_warnings(
        self, item: StoredEntry, merged: dict[str, StoredEntry] | None = None, table: SectorTable | None = None
    ) -> list[str]:
        """The warnings of one entry (see ``warnings``)."""
        merged = self._merged() if merged is None else merged
        table = self.sector_table() if table is None else table
        entry = item.entry
        found = [f"related '{ref}': no entry with that id" for ref in entry.related if ref not in merged]
        if entry.replaces is not None:
            target = merged.get(entry.replaces)
            if item.scope is Scope.base:
                found.append("a base entry cannot replace another one")
            elif self._dirs[Scope.base] is not None and (target is None or target.scope is not Scope.base):
                found.append(f"replaces '{entry.replaces}': no base entry with that id")
        if item.scope is not Scope.base:
            texts = [example.sector for example in entry.examples] + ([entry.sector] if entry.sector else [])
            for text in dict.fromkeys(texts):
                if not table.resolve(text).resolved:
                    found.append(f"sector '{text}' is not in the sector table: it counts as a sector not covered")
        return found

    def _merged(self) -> dict[str, StoredEntry]:
        merged: dict[str, StoredEntry] = {}
        for scope in (Scope.base, Scope.user, Scope.project):  # project last, so it wins
            for item in self._load_scope(scope)[0]:
                merged[item.entry.id] = item
        return merged

    def replacements(self, merged: dict[str, StoredEntry] | None = None) -> dict[str, str]:
        """``{base id: id of the engineer entry that replaces it}``: only an active entry of the user or the project
        replaces, and only a base entry that is still the effective one (the same id in the project already wins)."""
        merged = self._merged() if merged is None else merged
        found: dict[str, str] = {}
        for item in sorted(merged.values(), key=lambda row: row.entry.id):
            target = merged.get(item.entry.replaces) if item.entry.replaces else None
            if target is None or target.scope is not Scope.base:
                continue
            if item.scope is not Scope.base and item.entry.is_active:
                found[target.entry.id] = item.entry.id
        return found

    def scope_entries(self, scope: Scope) -> list[StoredEntry]:
        """Valid entries of one scope, sorted by type then id (no merging)."""
        return sorted(self._load_scope(scope)[0], key=lambda item: (item.entry.type.value, item.entry.id))

    def belongs_elsewhere(self, entry: MemoryEntry) -> bool:
        """True when ``entry`` is marked for a CAD software other than the one this store serves."""
        return entry.cad is not None and entry.cad != self.cad

    def entries(self, *, include_inactive: bool = True, all_cad: bool = False) -> list[StoredEntry]:
        """The merged view: the same id in both scopes resolves to the project one. Sorted by type then id. Entries of
        another CAD software are left out unless ``all_cad``."""
        merged = self._merged()
        replaced = set() if include_inactive else set(self.replacements(merged))  # a replaced base entry is no rule
        result = [
            item
            for item in merged.values()
            if (include_inactive or item.entry.is_active)
            and item.entry.id not in replaced
            and (all_cad or not self.belongs_elsewhere(item.entry))
        ]
        return sorted(result, key=lambda item: (item.entry.type.value, item.entry.id))

    def get(self, entry_id: str) -> StoredEntry | None:
        """The effective entry for an id (project wins), active or not; None when absent."""
        for item in self.entries(all_cad=True):
            if item.entry.id == entry_id:
                return item
        return None

    def search(self, text: str) -> list[StoredEntry]:
        """Active entries matching any word of ``text`` in title, tags, id or applies_when.

        Case and accent insensitive. Ranked by a simple score (title/tag hit 3, id 2, applies_when 1 per
        word); ties by id. No match returns an empty list."""
        words = re.findall(r"\w+", normalize_text(text))
        if not words:
            return []
        scored: list[tuple[int, str, StoredEntry]] = []
        for item in self.entries(include_inactive=False):
            entry = item.entry
            title = normalize_text(entry.title)
            tags = [normalize_text(tag) for tag in entry.tags]
            identifier = normalize_text(entry.id)
            applies = normalize_text(entry.applies_when)
            score = 0
            for word in words:
                score += 3 if word in title else 0
                score += 3 if any(word in tag for tag in tags) else 0
                score += 2 if word in identifier else 0
                score += 1 if word in applies else 0
            if score:
                scored.append((-score, entry.id, item))
        return [item for _, _, item in sorted(scored, key=lambda row: (row[0], row[1]))]

    # ---- writing -------------------------------------------------------------------------------

    def _require_dir(self, scope: Scope) -> Path:
        if scope is Scope.base:
            raise MemoryStoreError(
                "the base set is read-only: override an entry by saving the same id in the project or user memory"
            )
        directory = self._dirs[scope]
        if directory is None:
            raise MemoryStoreError(f"no {scope.value} memory folder is configured")
        return directory

    def save(self, entry: MemoryEntry, scope: Scope) -> str:
        """Validate, write ``<id>.md`` atomically, regenerate that scope's INDEX.md; return the text written.

        Saving an existing id in the same scope replaces it."""
        directory = self._require_dir(scope)
        old_id = LEGACY_BY_NEW.get(entry.id)
        if old_id and (directory / f"{old_id}.md").exists() and not (directory / f"{entry.id}.md").exists():
            entry = entry.model_copy(update={"id": old_id})  # the engineer's file keeps the name it has
        text = render_entry(entry)
        try:
            parse_entry(text, expected_id=entry.id)  # re-validate what is about to be written
        except EntryError as error:
            raise MemoryStoreError(f"entry '{entry.id}' is not valid: {error}") from error
        _atomic_write(directory / f"{entry.id}.md", text)
        self.reindex(scope)
        return text

    def deactivate(self, entry_id: str, scope: Scope) -> str:
        """Set the entry of ``scope`` to inactive (the file is kept, never deleted); return the new text.

        To switch off a user entry for one project, save an inactive entry with the same id in the project."""
        self._require_dir(scope)
        for item in self.scope_entries(scope):
            if item.entry.id == entry_id:
                inactive = item.entry.model_copy(update={"status": EntryStatus.inactive})
                return self.save(inactive, scope)
        raise MemoryStoreError(f"no entry '{entry_id}' in the {scope.value} memory")

    def approve(self, entry_id: str, scope: Scope) -> MemoryEntry:
        """Turn a ``proposed`` entry of ``scope`` into an active one (the engineer's act); return it."""
        self._require_dir(scope)
        for item in self.scope_entries(scope):
            if item.entry.id == entry_id:
                if not item.entry.is_proposed:
                    raise MemoryStoreError(f"entry '{entry_id}' is {item.entry.status.value}, not proposed")
                approved = item.entry.model_copy(update={"status": EntryStatus.active})
                self.save(approved, scope)
                return approved
        raise MemoryStoreError(f"no entry '{entry_id}' in the {scope.value} memory")

    def move(self, entry_id: str, source: Scope, target: Scope, status: EntryStatus | None = None) -> MemoryEntry:
        """Move an entry from one writable scope to another (the file leaves ``source``), optionally with a new status.

        Used when the engineer says a proposed user entry only holds for this project. Refuses when the target
        already has that id: nothing is overwritten."""
        self._require_dir(source)
        self._require_dir(target)
        if source is target:
            raise MemoryStoreError(f"entry '{entry_id}' is already in the {target.value} memory")
        found = next((item for item in self.scope_entries(source) if item.entry.id == entry_id), None)
        if found is None:
            raise MemoryStoreError(f"no entry '{entry_id}' in the {source.value} memory")
        if any(item.entry.id == entry_id for item in self.scope_entries(target)):
            raise MemoryStoreError(f"the {target.value} memory already has an entry '{entry_id}'; nothing was moved")
        moved = found.entry if status is None else found.entry.model_copy(update={"status": status})
        self.save(moved, target)
        found.path.unlink()
        self.reindex(source)
        return moved

    def similar(self, entry: MemoryEntry, limit: int = 5) -> list[StoredEntry]:
        """Other entries (any status but inactive) that look like ``entry``: shared tags, title or recognize words."""
        wanted = _words(" ".join([entry.title, entry.recognize or "", *entry.tags]))
        wanted_tags = {normalize_text(tag) for tag in entry.tags}
        found: list[tuple[int, str, StoredEntry]] = []
        replaced = set(self.replacements())
        for item in self.entries():
            other = item.entry
            if other.id == entry.id or other.status is EntryStatus.inactive or other.id in replaced:
                continue
            have = _words(" ".join([other.title, other.recognize or "", *other.tags]))
            shared_tags = wanted_tags & {normalize_text(tag) for tag in other.tags}
            score = len(wanted & have) + 2 * len(shared_tags)
            if score >= 3:
                found.append((-score, other.id, item))
        return [item for _, _, item in sorted(found, key=lambda row: (row[0], row[1]))][:limit]

    def reindex(self, scope: Scope) -> str:
        """Regenerate the INDEX.md of one scope from its valid entries; return its text."""
        directory = self._require_dir(scope)
        text = render_index(item.entry for item in self.scope_entries(scope))
        _atomic_write(directory / INDEX_FILENAME, text)
        return text

    def combined_index(self, view: SectorView | None = None, *, all_cad: bool = False) -> str:
        """The index: project entries first, then user, then base; inactive ones omitted. With a ``view``, the
        practices of the base set collapse to one line per sector except the listed sectors."""
        return combined_index(self.entries(all_cad=all_cad), view)
