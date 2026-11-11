"""Refresh the design-space template shipped inside the package from ``templates/design-space``.

``templates/design-space`` is the source of truth; the copy under ``src/cotaforge_mcp/data/design-space`` exists only so
that an installed package (editable or wheel) can reach the template. A unit test fails when the two differ.

Usage: ``python mcp-server/scripts/sync_template.py``
"""

import shutil
from pathlib import Path

MCP_SERVER = Path(__file__).resolve().parents[1]
SOURCE = MCP_SERVER.parent / "templates" / "design-space"
TARGET = MCP_SERVER / "src" / "cotaforge_mcp" / "data" / "design-space"


def main() -> None:
    if TARGET.exists():
        shutil.rmtree(TARGET)
    shutil.copytree(SOURCE, TARGET)
    print(f"copied {SOURCE} -> {TARGET}")


if __name__ == "__main__":
    main()
