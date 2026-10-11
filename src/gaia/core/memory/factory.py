"""Factory for creating memory store instances.

Creates stores based on the configured backend but does NOT call
``initialize()`` — that is deferred to the event loop.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from gaia.config import Settings

if TYPE_CHECKING:
    from gaia.core.memory.base import MemoryStore


class MemoryFactory:
    """Creates memory stores without initializing them.

    Reads ``settings.memory.backend`` and returns the matching store
    instance.  Returns ``None`` for unknown backends.

    Initialization is deferred to the event loop so no ``asyncio.run()``
    call is needed in ``__init__``.
    """

    @staticmethod
    def create(settings: Settings) -> "MemoryStore | None":
        """Create a memory store without initializing it."""
        match settings.memory.backend:
            case "file":
                from gaia.core.memory.file_store import FileMemoryStore

                memory_dir = Path(settings.memory.memory_dir).expanduser()
                return FileMemoryStore(memory_dir)
            case "chroma":
                from gaia.core.memory.chroma_store import ChromaMemoryStore

                return ChromaMemoryStore()
            case _:
                return None
