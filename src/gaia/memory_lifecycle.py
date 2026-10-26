"""Memory lifecycle helpers — initialise and close a ``MemoryStore``.

Extracted from ``gaia_tui_app.py`` so the TUI code is leaner and the
memory helpers can be reused or tested independently.
"""

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gaia.core.memory.base import MemoryStore

logger = logging.getLogger("gaia.tui")


class MemoryLifecycle:
    """Static helpers for memory-store initialisation and shutdown."""

    @staticmethod
    def create_init_task(store: "MemoryStore") -> asyncio.Task:
        """Create a background task that initialises *store*.

        Parameters
        ----------
        store:
            A ``MemoryStore`` instance (must not be ``None``).

        Returns
        -------
        ``asyncio.Task``
            The created task. Callers should attach a done callback
            (typically :meth:`on_memory_init_done`) to retrieve exceptions.
        """
        return asyncio.create_task(store.initialize())

    @staticmethod
    async def close(store: "MemoryStore | None") -> None:
        """Close the memory store.

        Parameters
        ----------
        store:
            A ``MemoryStore`` instance, or ``None``.
        """
        if store is not None:
            await store.close()
