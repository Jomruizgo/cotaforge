"""The engineer's own catalog, each entry with a date and one of two roles.

``compra``: a supplier the engineer buys from (local availability and prices). ``medidas``: a table of commercial
sizes taken from a catalog that is only a size reference (the part is bought elsewhere or machined); it has no supplier
and no price.

One JSON file per entry in ``<COTAFORGE_HOME>/catalog/`` (``~/.cotaforge/catalog/`` without it), written atomically
and meant to be readable and editable by hand. Entries are the engineer's data: the tool shows them as data, and a
file that cannot be read is skipped and reported, never fatal. A file without ``role`` is a ``compra``.
"""

import datetime as dt
import json
import os
import re
import tempfile
import unicodedata
from pathlib import Path
from typing import Any, Literal

from cotaforge import paths
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

USER_CATALOG_DIRNAME = paths.CATALOG
SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]{1,79}$")
MAX_COLUMNS = 8
MAX_ROWS = 200
MAX_CELL_CHARS = 300

# Fields each role refuses, with the way out; a medidas entry is a size table only.
COMPRA_REFUSES = {
    "standard": "the standard belongs to a medidas entry (a size table)",
    "source": "the source of a table belongs to a medidas entry; a compra entry is a supplier",
    "columns": "a table belongs to a medidas entry; a compra entry is a quote",
    "rows": "a table belongs to a medidas entry; a compra entry is a quote",
}
SIZES_ONLY = "a medidas entry is a size table only:"
BUY_ELSEWHERE = "if you buy it somewhere, add a compra entry"
MEDIDAS_REFUSES = {
    "supplier": f"{SIZES_ONLY} no supplier; {BUY_ELSEWHERE}",
    "price": f"{SIZES_ONLY} no price; {BUY_ELSEWHERE}",
    "unit": f"{SIZES_ONLY} no unit (it has no price)",
    "currency": f"{SIZES_ONLY} no currency (it has no price)",
    "availability": f"{SIZES_ONLY} no availability; {BUY_ELSEWHERE}",
    "part": f"{SIZES_ONLY} do not link it to a catalog part; put the standard in standard",
}


class UserEntryError(Exception):
    """The entry cannot be saved (invalid fields, existing id)."""


class UserEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(default=None, description="slug; default supplier-name-date (medidas: source-name-date)")
    role: Literal["compra", "medidas"] = "compra"
    name: str = Field(min_length=3, max_length=120)
    supplier: str | None = Field(default=None, min_length=2, max_length=120)
    date: dt.date
    price: float | None = Field(default=None, ge=0)
    currency: str = Field(default="COP", pattern=r"^[A-Z]{3}$")
    unit: str | None = Field(default=None, max_length=40)
    availability: str | None = Field(default=None, max_length=200)
    part: str | None = Field(default=None, max_length=80)
    url: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=500)
    standard: str | None = Field(default=None, min_length=2, max_length=120)
    source: str | None = Field(default=None, min_length=2, max_length=200)
    columns: list[str] | None = Field(default=None, min_length=1, max_length=MAX_COLUMNS)
    rows: list[list[str]] | None = Field(default=None, min_length=1, max_length=MAX_ROWS)

    @field_validator("id")
    @classmethod
    def _slug(cls, value: str | None) -> str | None:
        if value is not None and not SLUG.match(value):
            msg = "id must be a slug: lowercase letters, digits, '.', '_' or '-' (2 to 80 characters)"
            raise ValueError(msg)
        return value

    @field_validator("date")
    @classmethod
    def _not_future(cls, value: dt.date) -> dt.date:
        if value > dt.date.today() + dt.timedelta(days=1):
            msg = "date is in the future; use the date of the quote or of the check (YYYY-MM-DD)"
            raise ValueError(msg)
        return value

    @field_validator("columns", "rows", mode="before")
    @classmethod
    def _cells_are_text(cls, value: Any) -> Any:
        """A size table is text; a bare number in a cell (8, 2.5) is taken as its text."""
        if not isinstance(value, list):
            return value
        return [_text_cells(item) if isinstance(item, list) else _text_cell(item) for item in value]

    @field_validator("columns")
    @classmethod
    def _short_headers(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and any(not h.strip() or len(h) > MAX_CELL_CHARS for h in value):
            msg = f"each column header must be text of 1 to {MAX_CELL_CHARS} characters"
            raise ValueError(msg)
        return value

    @field_validator("rows")
    @classmethod
    def _short_cells(cls, value: list[list[str]] | None) -> list[list[str]] | None:
        if value is not None and any(len(cell) > MAX_CELL_CHARS for row in value for cell in row):
            msg = f"each cell of a row must be at most {MAX_CELL_CHARS} characters"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _by_role(self) -> "UserEntry":
        sent = self.model_fields_set
        if self.role == "medidas":
            # currency defaults to COP, so only one the caller sent counts as given
            problems = [
                why for field, why in MEDIDAS_REFUSES.items() if field in sent and getattr(self, field) is not None
            ]
            missing = [f for f in ("standard", "source", "columns", "rows") if getattr(self, f) is None]
            if missing:
                problems.append(
                    f"a medidas entry needs {', '.join(missing)} (standard e.g. 'DIN 7979 / ISO 8735'; source: who "
                    "published the table and where; columns: 1 to 8 headers; rows: 1 to 200 lists of texts)"
                )
            elif self.columns is not None and self.rows is not None:
                width = len(self.columns)
                bad = [n + 1 for n, row in enumerate(self.rows) if len(row) != width]
                if bad:
                    problems.append(
                        f"every row needs exactly {width} cells, one per column; wrong rows (1-based): "
                        f"{', '.join(str(n) for n in bad[:5])}"
                    )
        else:
            problems = [why for field, why in COMPRA_REFUSES.items() if getattr(self, field) is not None]
            if self.supplier is None:
                problems.append(
                    "a compra entry needs the supplier it is bought from; if it is only a table of sizes, use "
                    "role='medidas' (with standard, source, columns, rows)"
                )
            if self.price is not None and not self.unit:
                problems.append("a price needs its unit (per metre, per piece, per 6 m bar, per kg...)")
        if problems:
            raise ValueError("; ".join(problems))
        return self

    def provenance(self) -> str:
        if self.role == "medidas":
            return f"catálogo del usuario, solo medidas ({self.source}, {self.date.isoformat()})"
        return f"catálogo del usuario ({self.supplier}, {self.date.isoformat()})"


def _text_cell(value: Any) -> Any:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value:g}" if isinstance(value, float) else str(value)
    return value


def _text_cells(row: list[Any]) -> list[Any]:
    return [_text_cell(cell) for cell in row]


def _ascii_slug(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", plain).strip("-")


class UserCatalog:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.problems: list[str] = []

    def entries(self) -> list[UserEntry]:
        self.problems = []
        found: list[UserEntry] = []
        if not self.directory.is_dir():
            return found
        for path in sorted(self.directory.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                entry = UserEntry.model_validate({**data, "id": path.stem})
            except (OSError, ValueError, ValidationError) as error:
                self.problems.append(f"{path.name}: {str(error).splitlines()[0][:120]}")
                continue
            found.append(entry)
        return found

    def get(self, entry_id: str) -> UserEntry | None:
        return next((e for e in self.entries() if e.id == entry_id), None)

    def add(self, entry: UserEntry) -> UserEntry:
        """Save ``entry`` as a new file; an explicit existing id is refused (a new quote is a new entry)."""
        taken = {path.stem for path in self.directory.glob("*.json")} if self.directory.is_dir() else set()
        if entry.id is not None:
            if entry.id in taken:
                msg = f"entry {entry.id!r} already exists; use another id (a new price or date is a new entry)"
                raise UserEntryError(msg)
            entry_id = entry.id
        else:
            who = entry.source if entry.role == "medidas" else entry.supplier
            base = _ascii_slug(f"{who}-{entry.name}")[:60].strip("-") or "entrada"
            base = f"{base}-{entry.date.isoformat()}"
            entry_id, n = base, 2
            while entry_id in taken:
                entry_id, n = f"{base}-{n}", n + 1
        saved = entry.model_copy(update={"id": entry_id})
        # a compra file stays as it always was (no role: that is the default); a size table has no price, so no currency
        skip = {"id", "currency"} if saved.role == "medidas" else {"id", "role"}
        payload = saved.model_dump(mode="json", exclude=skip, exclude_none=True)
        self._write(self.directory / f"{entry_id}.json", json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        return saved

    @staticmethod
    def _write(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(text.encode("utf-8"))
            os.replace(temp_name, path)
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise
