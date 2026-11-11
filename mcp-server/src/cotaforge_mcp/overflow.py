"""Long texts a tool result cannot carry whole. The result shows the start and says, every time, how much it left out
and where the whole text is: a file in ``reports/`` of the design space, written only when something was cut. A
result never shortens anything silently."""

import contextlib
import itertools
from datetime import datetime
from pathlib import Path

from cotaforge import paths

REPORTS_DIRNAME = paths.REPORTS
_sequence = itertools.count(1)  # two reports of the same second never share a file


def reports_dir(workspace: Path) -> Path:
    return paths.resolve(workspace, REPORTS_DIRNAME)


class Overflow:
    """The whole of what ONE tool result shows in part. Every section passed through ``text``/``items`` is kept; when
    any of them is cut, ``write`` puts them all in one file, the one every cut names."""

    def __init__(self, workspace: Path, name: str, title: str) -> None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.path = reports_dir(workspace) / f"{name}-{stamp}-{next(_sequence)}.txt"
        self.title = title
        self.cut = False
        self._sections: list[tuple[str, str]] = []

    def text(self, label: str, text: str, limit: int) -> str:
        """``text`` when it has at most ``limit`` characters, else its start and what was left out."""
        self._sections.append((label, text))
        return self.shorten(label, text, limit)

    def shorten(self, label: str, text: str, limit: int) -> str:
        """Like ``text`` for a piece of a section already kept (one item of an ``items`` list)."""
        if len(text) <= limit:
            return text
        self.cut = True
        shown = text[:limit]
        lines = text.count("\n") - shown.count("\n")
        return (
            f"{shown}\n[{label}: {len(text) - limit} more characters ({lines} more lines) omitted; "
            f"full text in {self.path}]"
        )

    def keep(self, label: str, text: str) -> None:
        """Keep ``text`` whole in the file, which will be written: the caller cut its result by itself."""
        self._sections.append((label, text))
        self.cut = True

    def items(self, label: str, items: list[str], limit: int) -> tuple[list[str], str | None]:
        """The first ``limit`` of ``items`` and, when there are more, the line that says how many and where."""
        self._sections.append((label, "\n".join(items)))
        if len(items) <= limit:
            return items, None
        self.cut = True
        return items[:limit], f"{len(items) - limit} more {label} omitted; full report in {self.path}"

    def write(self) -> str | None:
        """Write the file when something was cut; ``None``, or the warning that it could not be written."""
        if not self.cut:
            return None
        body = [self.title]
        for label, text in self._sections:
            body.extend(("", f"== {label} ==", text))
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("\n".join(body) + "\n", encoding="utf-8")
        except OSError as exc:
            with contextlib.suppress(OSError):
                self.path.unlink()
            return f"the full report could not be written to {self.path} ({exc}); only the part shown here is known"
        return None
