"""Memory storage protocol and base types.

All memory backends implement the ``MemoryStore`` protocol, so the
orchestrator and agent don't care which storage system is in use.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class MemoryStore(ABC):
    """Protocol for all memory storage backends.

    Every memory type (semantic, episodic, procedural) and the audit
    log are accessed through these async methods. Implementations swap
    without changing the caller.
    """

    # ── Lifecycle ────────────────────────────────────────────────

    @abstractmethod
    async def initialize(self) -> None:
        """Open the connection and create tables if needed."""

    @abstractmethod
    async def close(self) -> None:
        """Close the connection and release resources."""

    # ── Semantic memory ──────────────────────────────────────────

    @abstractmethod
    async def semantic_insert(
        self,
        content: str,
        category: str,
        confidence: float,
        source: str,
        source_id: str | None = None,
    ) -> str:
        """Insert a semantic fact. Returns the new fact ID."""

    @abstractmethod
    async def semantic_update(self, fact_id: str, new_content: str, new_confidence: float) -> None:
        """Update a fact's content and confidence."""

    @abstractmethod
    async def semantic_delete(self, fact_id: str) -> None:
        """Remove a fact by ID."""

    @abstractmethod
    async def semantic_search(self, query: str, limit: int = 10) -> list[dict]:
        """Full-text search on semantic facts."""

    # ── Episodic memory ──────────────────────────────────────────

    @abstractmethod
    async def episodic_record(
        self,
        event_type: str,
        conversation_id: str | None = None,
        summary: str = "",
        payload: dict | None = None,
    ) -> str:
        """Record an episodic event. Returns the event ID."""

    @abstractmethod
    async def episodic_search(
        self,
        time_range: tuple[str, str] | None = None,
        event_types: list[str] | None = None,
        query: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Search episodic events with optional filters."""

    @abstractmethod
    async def episodic_get_conversation(self, conversation_id: str) -> list[dict]:
        """Retrieve all events for a conversation, ordered by time."""

    @abstractmethod
    async def episodic_prune(self, before_timestamp: str) -> int:
        """Remove events older than the given timestamp. Returns count deleted."""

    # ── Procedural memory ────────────────────────────────────────

    @abstractmethod
    async def procedure_register(
        self,
        procedure_id: str,
        name: str,
        description: str,
        safety_level: str,
        prompt_template: str | None = None,
        steps: list[dict] | None = None,
        source_file: str | None = None,
    ) -> None:
        """Register a new procedure (skill/routine/policy)."""

    @abstractmethod
    async def procedure_get(self, procedure_id: str) -> dict | None:
        """Retrieve a procedure by ID, or ``None``."""

    @abstractmethod
    async def procedure_search(self, query: str, category: str | None = None) -> list[dict]:
        """Search procedures by full-text query."""

    # ── Audit log ────────────────────────────────────────────────

    @abstractmethod
    async def audit_log(
        self,
        action: str,
        actor: str = "agent",
        target_type: str | None = None,
        target_id: str | None = None,
        detail: dict | None = None,
    ) -> None:
        """Append an audit entry (append-only)."""
