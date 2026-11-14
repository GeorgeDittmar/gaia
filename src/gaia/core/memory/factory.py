"""Factory for creating memory store instances.

Creates stores based on the configured backend but does NOT call
``initialize()`` — that is deferred to the event loop.
"""

from __future__ import annotations

import os
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
    def create(settings: Settings) -> MemoryStore | None:
        """Create a memory store without initializing it."""
        match settings.memory.backend:
            case "chroma":
                from gaia.core.memory.chroma_store import ChromaMemoryStore

                endpoint = settings.memory.endpoint
                if endpoint is None:
                    host = os.environ.get("CHROMA_HOST", "localhost")
                    port = os.environ.get("CHROMA_PORT", "8000")
                    endpoint = f"http://{host}:{port}"
                return ChromaMemoryStore(endpoint=endpoint)

            case "postgres":
                from gaia.core.memory.postgres_store import PostgresMemoryStore

                endpoint = settings.memory.endpoint
                return PostgresMemoryStore(endpoint=endpoint, pool_size=5)

            case _:
                return None
