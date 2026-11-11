"""What the agent sends to file a document or to update its ficha (``catalog`` with ``action="file"``)."""

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from cotaforge_mcp.library.ficha import PageNote

PAGE_RANGE = re.compile(r"^\s*(\d+)\s*(?:[-–—]\s*(\d+))?\s*$")
MAX_NOTES = 200


class PageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    p: str | int
    what: str = Field(max_length=300)

    @field_validator("p")
    @classmethod
    def _page(cls, value: str | int) -> str | int:
        match = PAGE_RANGE.match(str(value))
        if match is None or int(match.group(1)) < 1 or int(match.group(2) or match.group(1)) < int(match.group(1)):
            msg = "p is a page ('12') or a range ('2-3'), counted from 1"
            raise ValueError(msg)
        return value

    def note(self) -> PageNote:
        match = PAGE_RANGE.match(str(self.p))
        first = int(match.group(1)) if match else 1
        last = int(match.group(2)) if match and match.group(2) else first
        return PageNote(first, last, " ".join(self.what.split()))


class LibraryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(default=None, max_length=300)
    path: str | None = Field(default=None, max_length=1000)
    topic: str | None = Field(default=None, min_length=2, max_length=80)
    title: str | None = Field(default=None, min_length=3, max_length=200)
    standard: str | None = Field(default=None, max_length=120)
    edition: str | None = Field(default=None, max_length=60)
    author: str | None = Field(default=None, max_length=120)
    pages: list[PageIn] | None = Field(default=None, max_length=MAX_NOTES)
    topic_aliases: list[str] | None = Field(default=None, max_length=20)

    @field_validator("edition", "standard", "author", mode="before")
    @classmethod
    def _numbers_are_text(cls, value: Any) -> Any:  # noqa: ANN401
        """An edition given as a bare year (1997) is taken as its text."""
        return str(value) if isinstance(value, int) and not isinstance(value, bool) else value

    def notes(self) -> list[PageNote]:
        return [page.note() for page in self.pages or []]
