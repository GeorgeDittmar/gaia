"""Memory layer for G.A.I.A.

Memory is the agent's persistent identity — semantic, episodic, and
procedural types, all accessed through a single storage protocol.
"""

from gaia.core.memory.base import MemoryStore
from gaia.core.memory.file_store import FileMemoryStore

__all__ = ["MemoryStore", "FileMemoryStore"]

# ChromaDB-backed store is optional — only expose if chromadb is installed
try:
    from gaia.core.memory.chroma_store import ChromaMemoryStore  # noqa: F401

    __all__.append("ChromaMemoryStore")
except ImportError:
    pass
