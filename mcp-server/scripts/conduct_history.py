"""Add the current design-space template to the list of released template versions.

``src/cotaforge_mcp/data/conduct-history.json`` holds the SHA-256 of every ``CLAUDE.md`` template ever shipped; the
server uses it to tell a design space whose conduct file nobody edited (it is replaced by the current template) from one
the engineer edited (it is kept). Change the template, then run this script, or the unit test that checks the list
fails. Never remove an entry: a design space made with that version would then look edited.

Usage: ``python mcp-server/scripts/conduct_history.py [--since VERSION]`` (default: the package version)
"""

import argparse
from pathlib import Path

from cotaforge_mcp import __version__, conduct

DATA = Path(__file__).resolve().parents[1] / "src" / "cotaforge_mcp" / "data"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", default=__version__, help="the release that carries this template")
    args = parser.parse_args()
    template = (DATA / "design-space" / conduct.CLAUDE_MD).read_bytes()
    added = conduct.add_version(DATA / conduct.HISTORY_FILE, template, args.since)
    print("added" if added else "already in the list")


if __name__ == "__main__":
    main()
