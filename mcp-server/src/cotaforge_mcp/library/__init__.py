"""The engineer's technical library: standards and manuals the agent files, found again by page.

The product ships the code and a starting list of topics, never documents.
"""

from cotaforge_mcp.library.entry import LibraryEntry
from cotaforge_mcp.library.files import INDEX_DIRNAME, INDEX_FILENAME, LIBRARY_DIRNAME
from cotaforge_mcp.library.store import Filed, Library, LibraryError

__all__ = [
    "INDEX_DIRNAME",
    "INDEX_FILENAME",
    "LIBRARY_DIRNAME",
    "Filed",
    "Library",
    "LibraryEntry",
    "LibraryError",
]
