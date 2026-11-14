"""Memory layer for G.A.I.A.

Memory is the agent's persistent identity — semantic, episodic, and
procedural types, all accessed through a single storage protocol.
"""

from gaia.core.memory.base import MemoryStore
from gaia.core.memory.embedding import generate_embedding, embedding_dim
from gaia.core.memory.frontmatter import build_frontmatter, parse_frontmatter

__all__ = [
    "MemoryStore",
    "build_frontmatter",
    "parse_frontmatter",
    "generate_embedding",
    "embedding_dim",
]

# ChromaDB-backed store is optional — only expose if chromadb is installed
try:
    from gaia.core.memory.chroma_store import ChromaMemoryStore  # noqa: F401

    __all__.append("ChromaMemoryStore")
except ImportError:
    pass
