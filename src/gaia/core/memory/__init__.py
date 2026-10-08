"""Memory layer for G.A.I.A.

Memory is the agent's persistent identity — semantic, episodic, and
procedural types, all accessed through a single storage protocol.
"""

from gaia.core.memory.base import MemoryStore
from gaia.core.memory.chroma_store import ChromaMemoryStore

__all__ = ["MemoryStore", "ChromaMemoryStore"]
