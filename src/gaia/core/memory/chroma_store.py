"""ChromaDB-backed MemoryStore implementation.

Stores all three memory types (semantic, episodic, procedural) and the
audit log in a persistent ChromaDB instance. ChromaDB handles vector
embeddings internally, powering semantic search out of the box.

All ChromaDB calls are wrapped in ``asyncio.to_thread()`` so the event
loop stays responsive.

Data directory: ``~/.gaia/chroma`` (created on first ``initialize()``).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import chromadb

from gaia.core.memory.base import MemoryStore


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _uuid() -> str:
    """Return a random UUID4 string."""
    return str(uuid.uuid4())


def _sanitize_meta(meta: dict[str, Any]) -> dict[str, Any]:
    """Remove ``None`` values from metadata.

    ChromaDB 1.5+ does not accept ``None`` in metadata dicts.
    """
    return {k: v for k, v in meta.items() if v is not None}


class ChromaMemoryStore(MemoryStore):
    """ChromaDB-backed implementation of the MemoryStore protocol.

    Stores semantic facts, episodic events, procedural definitions, and
    audit log entries in four ChromaDB collections. Vector search is
    powered by ChromaDB's built-in embedding functions (or a custom
    one if the client is configured with one).

    Args:
        persist_directory: Path to the ChromaDB persistent data
            directory. Defaults to ``~/.gaia/chroma``.
    """

    _COLLECTIONS: tuple[str, ...] = (
        "semantic_facts",
        "episodic_events",
        "procedure_entries",
        "audit_log",
    )

    def __init__(self, persist_directory: str | None = None) -> None:
        if persist_directory is None:
            persist_directory = str(Path.home() / ".gaia" / "chroma")
        self._persist_directory = persist_directory
        self._client: chromadb.Client | None = None
        self._collections: dict[str, chromadb.Collection] = {}

    # ── Lifecycle ────────────────────────────────────────────────

    async def initialize(self) -> None:
        """Open the ChromaDB persistent client and create collections."""
        self._client = chromadb.PersistentClient(path=self._persist_directory)
        for name in self._COLLECTIONS:
            self._collections[name] = self._client.get_or_create_collection(name)

    async def close(self) -> None:
        """Close the ChromaDB client (if supported)."""
        if self._client is not None:
            try:
                self._client.close()  # type: ignore [union-attr]
            except Exception:
                pass  # Best-effort; ChromaDB may not support close()
            self._client = None
            self._collections.clear()

    # ── Helpers ──────────────────────────────────────────────────

    def _thread(self, fn: Any) -> Any:
        """Run *fn* on the event-loop thread pool and return the result."""
        return asyncio.to_thread(fn)

    # ── Semantic operations ──────────────────────────────────────

    async def semantic_insert(
        self,
        content: str,
        category: str,
        confidence: float,
        source: str,
        source_id: str | None = None,
    ) -> str:
        """Insert a semantic fact and return its UUID."""
        fact_id = _uuid()
        now = _now_iso()
        metadata = _sanitize_meta({
            "fact_id": fact_id,
            "category": category,
            "confidence": confidence,
            "source": source,
            "source_id": source_id,
            "created_at": now,
            "updated_at": now,
            "metadata": None,
        })
        collection = self._collections["semantic_facts"]

        await self._thread(
            lambda: collection.add(
                documents=[content],
                metadatas=[metadata],
                ids=[fact_id],
            )
        )
        return fact_id

    async def semantic_update(
        self, fact_id: str, new_content: str, new_confidence: float
    ) -> None:
        """Update a fact's content and confidence.

        ChromaDB doesn't support in-place document updates, so we
        read metadata first, delete the old document (matched by
        fact_id metadata), then re-insert with the same fact_id.
        """
        now = _now_iso()
        collection = self._collections["semantic_facts"]

        # Read current metadata BEFORE deleting
        old_meta: dict[str, Any] = {}
        get_result = await self._thread(
            lambda: collection.get(ids=[fact_id], include=["metadatas"])
        )
        if get_result and get_result["metadatas"] and get_result["metadatas"][0]:
            old = get_result["metadatas"][0]
            old_meta = {
                "fact_id": fact_id,
                "category": old.get("category", "general"),
                "source": old.get("source", "system"),
                "source_id": old.get("source_id"),
                "created_at": old.get("created_at", now),
                "updated_at": now,
                "metadata": old.get("metadata"),
            }

        # Delete old document by fact_id
        await self._thread(
            lambda: collection.delete(where={"fact_id": fact_id})
        )

        metadata = _sanitize_meta({
            "fact_id": fact_id,
            "category": old_meta.get("category", "general"),
            "confidence": new_confidence,
            "source": old_meta.get("source", "system"),
            "source_id": old_meta.get("source_id"),
            "created_at": old_meta.get("created_at", now),
            "updated_at": now,
            "metadata": old_meta.get("metadata"),
        })

        await self._thread(
            lambda: collection.add(
                documents=[new_content],
                metadatas=[metadata],
                ids=[fact_id],
            )
        )

    async def semantic_delete(self, fact_id: str) -> None:
        """Remove a fact by ID."""
        collection = self._collections["semantic_facts"]
        await self._thread(
            lambda: collection.delete(where={"fact_id": fact_id})
        )

    async def semantic_search(self, query: str, limit: int = 10) -> list[dict]:
        """Search semantic facts by vector similarity.

        Returns a list of dicts matching the format expected by the
        agent orchestrator.  None-valued documents are replaced with
        empty strings.
        """
        collection = self._collections["semantic_facts"]
        result = await self._thread(
            lambda: collection.query(
                query_texts=[query],
                n_results=min(limit, 1000),
            )
        )

        records: list[dict] = []
        if not result["ids"] or not result["ids"][0]:
            return records

        for i in range(len(result["ids"][0])):
            doc = (
                result["documents"][0][i]
                if result["documents"] and result["documents"][0][i] is not None
                else ""
            )
            meta = (
                result["metadatas"][0][i]
                if result["metadatas"] and result["metadatas"][0][i]
                else {}
            )

            records.append({
                "fact_id": result["ids"][0][i],
                "content": doc,
                "category": meta.get("category", "general"),
                "confidence": float(meta.get("confidence", 0.0)),
                "source": meta.get("source", "unknown"),
                "source_id": meta.get("source_id"),
                "created_at": meta.get("created_at", ""),
                "updated_at": meta.get("updated_at", ""),
                "metadata": meta.get("metadata"),
            })

        return records

    # ── Episodic operations ──────────────────────────────────────

    async def episodic_record(
        self,
        event_type: str,
        conversation_id: str | None = None,
        summary: str = "",
        payload: dict | None = None,
    ) -> str:
        """Record an episodic event and return its UUID."""
        event_id = _uuid()
        now = _now_iso()
        payload_json = json.dumps(payload or {})
        indexed_content = " ".join(s for s in (summary, payload_json) if s)

        metadata = _sanitize_meta({
            "event_id": event_id,
            "event_type": event_type,
            "conversation_id": conversation_id,
            "timestamp": now,
            "payload": payload_json,
            "created_at": now,
            "indexed_content": indexed_content,
        })

        collection = self._collections["episodic_events"]
        await self._thread(
            lambda: collection.add(
                documents=[summary],
                metadatas=[metadata],
                ids=[event_id],
            )
        )
        return event_id

    async def episodic_search(
        self,
        time_range: tuple[str, str] | None = None,
        event_types: list[str] | None = None,
        query: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Search episodic events with optional filters.

        Strategy:
        - If a text *query* is provided, use ChromaDB vector search
          first, then filter results by *event_types* and *time_range*.
        - Otherwise, fetch all documents and filter in Python.
        """
        collection = self._collections["episodic_events"]
        results: dict[str, list] = {}

        if query:
            # Text search first, then filter
            query_result = await self._thread(
                lambda: collection.query(
                    query_texts=[query],
                    n_results=min(limit * 5, 1000),
                )
            )
            ids = (
                query_result["ids"][0]
                if query_result.get("ids") and query_result["ids"]
                else []
            )
            if not ids:
                return []
            results = await self._thread(
                lambda: collection.get(ids=ids, include=["documents", "metadatas"])
            )
        else:
            # No text query — fetch all and filter in Python.
            results = await self._thread(
                lambda: collection.get(include=["documents", "metadatas"])
            )

        if not results or not results["ids"]:
            return []

        # Apply metadata filters
        records: list[dict] = []
        for i, event_id in enumerate(results["ids"]):
            meta = (
                results["metadatas"][i]
                if results["metadatas"] and i < len(results["metadatas"])
                else {}
            )
            doc = (
                results["documents"][i]
                if results["documents"] and i < len(results["documents"])
                else None
            )

            # Filter by event_types
            if event_types is not None:
                if meta.get("event_type", "") not in event_types:
                    continue

            # Filter by time_range
            if time_range is not None:
                ts = meta.get("timestamp", "")
                if ts < time_range[0] or ts > time_range[1]:
                    continue

            records.append({
                "event_id": event_id,
                "event_type": meta.get("event_type", "unknown"),
                "conversation_id": meta.get("conversation_id"),
                "timestamp": meta.get("timestamp", ""),
                "summary": doc or "",
                "payload": meta.get("payload", "{}"),
                "created_at": meta.get("created_at", ""),
            })

        # Sort by timestamp descending and truncate
        records.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
        return records[:limit]

    async def episodic_get_conversation(
        self, conversation_id: str
    ) -> list[dict]:
        """Retrieve all events for a conversation, ordered by time."""
        collection = self._collections["episodic_events"]

        result = await self._thread(
            lambda: collection.get(
                where={"conversation_id": conversation_id},
                include=["documents", "metadatas"],
            )
        )

        if not result or not result["ids"]:
            return []

        records: list[dict] = []
        for i, event_id in enumerate(result["ids"]):
            meta = (
                result["metadatas"][i]
                if result["metadatas"] and i < len(result["metadatas"])
                else {}
            )
            doc = (
                result["documents"][i]
                if result["documents"] and i < len(result["documents"])
                else None
            )

            cid = meta.get("conversation_id")
            records.append({
                "event_id": event_id,
                "event_type": meta.get("event_type", "unknown"),
                "conversation_id": cid,
                "timestamp": meta.get("timestamp", ""),
                "summary": doc or "",
                "payload": meta.get("payload", "{}"),
                "created_at": meta.get("created_at", ""),
            })

        records.sort(key=lambda r: r.get("timestamp", ""))
        return records

    async def episodic_prune(self, before_timestamp: str) -> int:
        """Remove events older than *before_timestamp*. Returns count deleted.

        ChromaDB's $lt operator only works on numeric values, so we
        fetch all documents, filter in Python, then delete by IDs.
        """
        collection = self._collections["episodic_events"]

        result = await self._thread(
            lambda: collection.get(include=["metadatas"])
        )

        if not result or not result["ids"]:
            return 0

        ids_to_delete: list[str] = []
        for i, event_id in enumerate(result["ids"]):
            meta = (
                result["metadatas"][i]
                if result["metadatas"] and i < len(result["metadatas"])
                else {}
            )
            ts = meta.get("timestamp", "")
            if ts < before_timestamp:
                ids_to_delete.append(event_id)

        if ids_to_delete:
            await self._thread(
                lambda: collection.delete(ids=ids_to_delete)
            )

        return len(ids_to_delete)

    # ── Procedural operations ────────────────────────────────────

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
        now = _now_iso()
        steps_json = json.dumps(steps) if steps else None

        metadata = _sanitize_meta({
            "procedure_id": procedure_id,
            "name": name,
            "description": description,
            "category": "custom",
            "safety_level": safety_level,
            "prompt_template": prompt_template,
            "steps": steps_json,
            "source_file": source_file,
            "enabled": 1,
            "version": 1,
            "created_at": now,
            "updated_at": now,
            "metadata": None,
        })

        collection = self._collections["procedure_entries"]
        search_text = f"{name} {description}".strip()

        await self._thread(
            lambda: collection.upsert(
                documents=[search_text],
                metadatas=[metadata],
                ids=[procedure_id],
            )
        )

    async def procedure_get(self, procedure_id: str) -> dict | None:
        """Retrieve a procedure by ID, or ``None`` if not found."""
        collection = self._collections["procedure_entries"]
        result = await self._thread(
            lambda: collection.get(
                ids=[procedure_id],
                include=["metadatas"],
            )
        )

        if not result or not result["ids"]:
            return None

        meta = (
            result["metadatas"][0]
            if result["metadatas"] and result["metadatas"][0]
            else {}
        )
        return dict(meta)

    async def procedure_search(
        self, query: str, category: str | None = None
    ) -> list[dict]:
        """Search procedures by vector similarity on name + description."""
        collection = self._collections["procedure_entries"]

        result = await self._thread(
            lambda: collection.query(
                query_texts=[query],
                n_results=100,
            )
        )

        if not result["ids"] or not result["ids"][0]:
            return []

        records: list[dict] = []
        for i in range(len(result["ids"][0])):
            meta = (
                result["metadatas"][0][i]
                if result["metadatas"] and result["metadatas"][0][i]
                else {}
            )

            if category is not None:
                if meta.get("category") != category:
                    continue

            records.append({
                "procedure_id": meta.get("procedure_id", result["ids"][0][i]),
                "name": meta.get("name", ""),
                "description": meta.get("description", ""),
                "category": meta.get("category", "custom"),
                "safety_level": meta.get("safety_level", "safe"),
                "enabled": meta.get("enabled", 1),
                "version": meta.get("version", 1),
                "source_file": meta.get("source_file"),
                "created_at": meta.get("created_at", ""),
            })

        return records

    # ── Audit operations ─────────────────────────────────────────

    async def audit_log(
        self,
        action: str,
        actor: str = "agent",
        target_type: str | None = None,
        target_id: str | None = None,
        detail: dict | None = None,
    ) -> None:
        """Append an audit entry. This is append-only by design."""
        now = _now_iso()
        entry_id = _uuid()

        metadata = _sanitize_meta({
            "timestamp": now,
            "action": action,
            "actor": actor,
            "target_type": target_type,
            "target_id": target_id,
            "detail": json.dumps(detail) if detail else None,
        })

        collection = self._collections["audit_log"]
        await self._thread(
            lambda: collection.add(
                documents=[action],
                metadatas=[metadata],
                ids=[entry_id],
            )
        )
