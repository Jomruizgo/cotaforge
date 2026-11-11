"""The ``catalog`` tool: shipped standards tables + the engineer's own supplier/price list + his technical library.

Every line of a result says where it came from: ``catálogo (<standard, edition, publisher>)`` for the shipped tables
(prefixed ``SIN VERIFICAR`` when a row could not be confirmed) and ``catálogo del usuario (<supplier>, <date>)`` for the
user's entries. The catalog is deliberately small and is not the only source: a miss tells the agent to look further
and to label what it finds. The library (standards and manuals the agent files for him) answers with the document, the
page and the file's path, so the agent opens the page itself before citing it.
"""

import sqlite3
from typing import Any

from pydantic import ValidationError

from cotaforge_mcp.config import Settings
from cotaforge_mcp.library import Library, LibraryEntry, LibraryError
from cotaforge_mcp.library import render as render_library
from cotaforge_mcp.library.search import Hits, Overview, find_row, overview, search
from cotaforge_mcp.library.sync import Refresh, refresh
from cotaforge_mcp.parts import (
    FAMILIES,
    UserCatalog,
    UserEntry,
    UserEntryError,
    all_parts,
    family_counts,
    get_part,
    matches_all,
    normalize,
    search_parts,
)
from cotaforge_mcp.parts import render as render_part
from cotaforge_mcp.reply import Reply, ToolError, ok
from cotaforge_mcp.schemas import CatalogArgs

USAGE = 'catalog(action="search|get|add|file", text?, id?, entry?)'
SEARCH_EXAMPLE = 'catalog(action="search", text="tubo cuadrado 40x40x2")'
GET_EXAMPLE = 'catalog(action="get", id="tube-sq-40x40x2")'
FILE_EXAMPLE = (
    'catalog(action="file", entry={"path":"din-7979.pdf","topic":"pasadores-y-chavetas",'
    '"title":"Pasadores cilíndricos con rosca interior","standard":"DIN 7979","edition":"1997",'
    '"pages":[{"p":"2-3","what":"tabla de medidas d x l"}]})'
)
ADD_EXAMPLE = (
    'catalog(action="add", entry={"name":"Tubo cuadrado 40x40x2 6 m","supplier":"Aceros Medellín",'
    '"date":"2026-10-24","price":98000,"unit":"barra 6 m","availability":"en stock","part":"tube-sq-40x40x2"})'
)
MEDIDAS_EXAMPLE = (
    'catalog(action="add", entry={"role":"medidas","name":"Pasador de registro endurecido",'
    '"standard":"DIN 7979 / ISO 8735","source":"Proveedor Ejemplo, catálogo p.16","date":"2026-10-24",'
    '"columns":["d","L"],"rows":[["6","16"],["8","20"]]})'
)
MAX_HITS = 15
MAX_USER_HITS = 6
RESULT_CHARS = 5000
NOT_ONLY_SOURCE = (
    "the catalog is not the only source: search the web and mark what you find as web (URL, date), or as an "
    "assumption; never present it as catalog. A part that exists on the market is bought, not drawn: log the search "
    "and the decision under the «Commercial parts» section of assumptions-and-decisions.md"
)


def _missing(what: str, action: str, example: str) -> ToolError:
    return ToolError(
        f"{action} needs {what}", code="invalid_input", field_name=what.split()[0], usage=USAGE, example=example
    )


def _user_matches(entries: list[UserEntry], text: str) -> list[UserEntry]:
    tokens = normalize(text).split()
    hits = []
    for entry in entries:
        words = [entry.id, entry.name, entry.supplier, entry.part, entry.availability, entry.notes]
        words += [
            entry.standard,
            entry.source,
            *(entry.columns or []),
            *(cell for row in entry.rows or [] for cell in row),
        ]
        hay = normalize(" ".join(str(v) for v in words if v))
        if matches_all(tokens, hay):
            hits.append(entry)
    return sorted(hits, key=lambda e: e.date, reverse=True)  # the newest quote first


def _problems(catalog: UserCatalog) -> list[str]:
    if not catalog.problems:
        return []
    shown = catalog.problems[:3]
    return [f"{len(catalog.problems)} user catalog file(s) skipped (fix or remove them): " + "; ".join(shown)]


def _library(settings: Settings) -> Library:
    return Library(settings.library_dir(), settings.library_index_path())


def _unavailable(error: Exception) -> Refresh:
    return Refresh(problems=[f"the library could not be read ({type(error).__name__}: {str(error)[:80]})"])


def _library_hits(settings: Settings, text: str) -> Hits:
    try:
        return search(_library(settings), text)
    except (sqlite3.Error, OSError) as error:
        return Hits(refresh=_unavailable(error))


def _library_overview(settings: Settings) -> Overview:
    try:
        return overview(_library(settings))
    except (sqlite3.Error, OSError) as error:
        return Overview(refresh=_unavailable(error))


def _overview(settings: Settings, catalog: UserCatalog) -> Reply:
    counts = family_counts()
    lines = [f"catalog: {len(all_parts())} standard parts (every table cites its standard and source)"]
    for kind, name, example in FAMILIES:
        lines.append(f'- {name}: {counts.get(kind, 0)}   e.g. text="{example}"')
    unverified = sum(1 for p in all_parts() if not p.verified)
    lines.append(f"{unverified} rows are SIN VERIFICAR (dimensions not confirmed in a source that was read)")
    users = catalog.entries()
    sizes = sum(1 for e in users if e.role == "medidas")
    lines.append(
        f"user catalog: {len(users)} entries ({len(users) - sizes} compra: suppliers, prices, availability; "
        f"{sizes} medidas: size tables only; each with a date)"
    )
    lines.extend(_problems(catalog))
    lines.extend(render_library.overview_lines(_library_overview(settings)))
    lines.append(NOT_ONLY_SOURCE)
    return ok("\n".join(lines), limit=RESULT_CHARS)


def _search(settings: Settings, text: str | None) -> Reply:
    catalog = UserCatalog(settings.user_catalog_dir())
    if not text or not text.strip():
        return _overview(settings, catalog)
    users = _user_matches(catalog.entries(), text)
    hits = search_parts(text)
    shelf = _library_hits(settings, text)
    found = render_library.search_block(shelf)
    if not hits and not users and not found:
        where = "the catalog, the user catalog or the library" if shelf.documents else "the catalog or the user catalog"
        lines = [f"no match for {text!r} in {where}", NOT_ONLY_SOURCE]
        lines.extend(_problems(catalog))
        lines.extend(render_library.status(shelf.refresh))
        return ok("\n".join(lines), limit=RESULT_CHARS)
    lines = []
    if users:
        lines.append(f"user catalog ({len(users)}):")
        lines.extend(f"- {render_part.user_line(e)}" for e in users[:MAX_USER_HITS])
        if len(users) > MAX_USER_HITS:
            lines.append(f"... +{len(users) - MAX_USER_HITS} more user entries; use more words")
    lines.extend(found)
    if hits:
        lines.append(f"catalog ({len(hits)}):")
        lines.extend(f"- {render_part.line(p)}" for p in hits[:MAX_HITS])
        if len(hits) > MAX_HITS:
            lines.append(f"... +{len(hits) - MAX_HITS} more; use more words (size, thickness, standard)")
        lines.append("catalog(action=get, id=<id>) gives dimensions, properties and the full source")
    lines.extend(_problems(catalog))
    lines.extend(render_library.status(shelf.refresh))
    return ok("\n".join(lines), limit=RESULT_CHARS)


def _get(settings: Settings, part_id: str) -> Reply:
    catalog = UserCatalog(settings.user_catalog_dir())
    entries = catalog.entries()
    part = get_part(part_id)
    if part is not None:
        prices = sorted((e for e in entries if e.part == part.id), key=lambda e: e.date, reverse=True)
        return ok(render_part.detail(part, prices), limit=RESULT_CHARS)
    entry = next((e for e in entries if e.id == part_id.strip()), None)
    if entry is not None:
        return ok(render_part.user_detail(entry), limit=RESULT_CHARS)
    document = find_row(_library(settings), part_id)
    if document is not None:
        doc, ficha, problem = document
        return ok(render_library.detail(doc, ficha, problem, Refresh()), limit=RESULT_CHARS)
    raise ToolError(
        f"no part, user entry or library document {part_id!r}; catalog(action=search, text=...) finds ids. "
        f"{NOT_ONLY_SOURCE}",
        code="unknown_part",
        field_name="id",
        usage=USAGE,
        example=GET_EXAMPLE,
    )


def _issues(error: ValidationError) -> str:
    lines = []
    for item in error.errors()[:4]:
        where = ".".join(str(part) for part in item["loc"]) or "entry"
        lines.append(f"field: {where} -> {str(item['msg']).removeprefix('Value error, ')}")
    return "\n".join(lines)


def _add(settings: Settings, raw: dict[str, Any] | None) -> Reply:
    if not raw:
        raise _missing("entry (the fields of the user catalog entry)", "add", ADD_EXAMPLE)
    try:
        entry = UserEntry.model_validate(raw)
    except ValidationError as error:
        raise ToolError(
            f"the entry is not valid:\n{_issues(error)}",
            code="invalid_entry",
            field_name="entry",
            usage=USAGE,
            example=MEDIDAS_EXAMPLE if raw.get("role") == "medidas" else ADD_EXAMPLE,
        ) from error
    if entry.part is not None and get_part(entry.part) is None:
        raise ToolError(
            f"part {entry.part!r} is not a catalog id; leave it out or take the id from catalog(action=search)",
            code="unknown_part",
            field_name="entry.part",
            usage=USAGE,
            example=ADD_EXAMPLE,
        )
    catalog = UserCatalog(settings.user_catalog_dir())
    try:
        saved = catalog.add(entry)
    except UserEntryError as error:
        raise ToolError(
            str(error), code="entry_exists", field_name="entry.id", usage=USAGE, example=ADD_EXAMPLE
        ) from error
    except OSError as error:
        raise ToolError(f"could not write the user catalog: {error}", code="catalog_error", usage=USAGE) from error
    closing = (
        "add only tables the engineer gave or confirmed; this is a size reference, never a supplier"
        if saved.role == "medidas"
        else "add only what the engineer gave or confirmed: it is his data, shown with this supplier and date"
    )
    return ok(
        f"saved: {saved.id} in the user catalog ({settings.user_catalog_dir() / (saved.id or '')}.json)\n"
        f"{render_part.user_line(saved)}\n{closing}",
        limit=RESULT_CHARS,
    )


def _file(settings: Settings, raw: dict[str, Any] | None) -> Reply:
    if not raw:
        raise _missing("entry (the document and what you decided about it)", "file", FILE_EXAMPLE)
    try:
        entry = LibraryEntry.model_validate(raw)
    except ValidationError as error:
        raise ToolError(
            f"the entry is not valid:\n{_issues(error)}",
            code="invalid_entry",
            field_name="entry",
            usage=USAGE,
            example=FILE_EXAMPLE,
        ) from error
    library = _library(settings)
    try:
        filed = library.file(entry, settings.resolve(entry.path) if entry.path else None)
    except LibraryError as error:
        raise ToolError(
            error.message, code=error.code, field_name=error.field_name, usage=USAGE, example=FILE_EXAMPLE
        ) from error
    except OSError as error:
        raise ToolError(f"could not write the library: {error}", code="library_error", usage=USAGE) from error
    report = Refresh()
    if filed.kind != "duplicate":
        try:
            report = refresh(library, first=filed.id)
        except (sqlite3.Error, OSError) as error:
            report = _unavailable(error)
    reply = render_library.filed_reply(filed, report.pending)
    return ok("\n".join([reply, *render_library.status(Refresh(problems=report.problems))]), limit=RESULT_CHARS)


def run(settings: Settings, args: CatalogArgs) -> Reply:
    if args.action == "search":
        return _search(settings, args.text)
    if args.action == "get":
        if not args.id:
            raise _missing("id (a catalog, user entry or library document id from a search)", "get", GET_EXAMPLE)
        return _get(settings, args.id)
    if args.action == "file":
        return _file(settings, args.entry)
    return _add(settings, args.entry)
