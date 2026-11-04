"""ChromaDB-backed MemoryStore implementation (network server).

Connects to an **external ChromaDB HTTP server** — not embedded.
All ChromaDB calls are wrapped in ``asyncio.to_thread()`` so the
event loop stays responsive.

Configuration:
    Set ``memory.backend`` to ``"chroma"`` and ``memory.endpoint`` to the
    ChromaDB server URL (default ``http://host.containers.internal:8000``
    when running in Docker/Podman, ``http://localhost:8000`` otherwise).

Usage with docker-compose::

    docker compose up -d chroma          # start ChromaDB
    docker compose up -d gaia            # start Gaia (connects to chroma)
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    import chromadb
    from chromadb import HttpClient
except ImportError:
    chromadb = None  # type: ignore[assignment]
    HttpClient = None  # type: ignore[assignment,misc]

from gaia.core.memory.base import MemoryStore


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(UTC).isoformat()


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

    Connects to an external ChromaDB HTTP server. Stores semantic facts,
    episodic events, procedural definitions, and audit log entries in
    four ChromaDB collections. Vector search is powered by ChromaDB's
    built-in embedding functions.

    Args:
        endpoint: URL of the ChromaDB HTTP server.
            Defaults to ``http://host.containers.internal:8000`` (Docker/
            Podman) or ``http://localhost:8000`` (host).
        collection: ChromaDB collection prefix — all four memory types
            share collections with suffixes (``_semantic``, ``_episodic``,
            ``_procedure``, ``_audit``).
    """

    _COLLECTIONS: tuple[str, ...] = (
        "semantic",
        "episodic",
        "procedure",
        "audit",
    )

    def __init__(
        self,
        endpoint: str | None = None,
        collection: str = "gaia",
    ) -> None:
        if endpoint is None:
            import os

            host = os.environ.get("CHROMA_HOST", "host.containers.internal")
            port = os.environ.get("CHROMA_PORT", "8000")
            endpoint = f"http://{host}:{port}"

        # Parse "http://host:port" → host + port for chromadb.HttpClient
        # Handles both "http://host.containers.internal:8000" and "http://localhost:8000"
        self._endpoint = endpoint
        self._collection_prefix = collection
        self._client: HttpClient | None = None
        self._collections: dict[str, Any] = {}

    # ── Lifecycle ────────────────────────────────────────────────

    async def initialize(self) -> None:
        """Connect to the ChromaDB HTTP server and create collections."""
        if chromadb is None or HttpClient is None:
            raise RuntimeError(
                "ChromaDB is not installed. "
                "Install it with: pip install chromadb"
            )

        # Parse "http://host:port" into host and port for HttpClient
        # e.g. "http://host.containers.internal:8000" → host, port
        parsed = self._endpoint.replace("http://", "").replace("https://", "")
        host, port = parsed.rsplit(":", 1) if ":" in parsed else (parsed, "8000")

        self._client = HttpClient(host=host, port=int(port))
        for name in self._COLLECTIONS:
            col_name = f"{self._collection_prefix}_{name}"
            self._collections[col_name] = self._client.get_or_create_collection(col_name)

    async def close(self) -> None:
        """Release the ChromaDB HTTP connection (best-effort)."""
        self._client = None
        self._collections.clear()

    # ── Helpers ──────────────────────────────────────────────────

    def _thread(self, fn: Any) -> Any:
        """Run *fn* on the event-loop thread pool and return the result."""
        return asyncio.to_thread(fn)

    def _col(self, suffix: str) -> Any:
        """Get a collection by suffix."""
        name = f"{self._collection_prefix}_{suffix}"
        return self._collections.get(name)

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
        collection = self._col("semantic")

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
        collection = self._col("semantic")

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
            lambda: collection.delete(ids=[fact_id])
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
        collection = self._col("semantic")
        await self._thread(
            lambda: collection.delete(ids=[fact_id])
        )

    async def semantic_search(self, query: str, limit: int = 10) -> list[dict]:
        """Search semantic facts by vector similarity.

        Returns a list of dicts matching the format expected by the
        agent orchestrator. None-valued documents are replaced with
        empty strings.
        """
        collection = self._col("semantic")
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

    async def semantic_similarity_search(
        self, query: str, limit: int = 5, distance_threshold: float = 0.8
    ) -> bool:
        """Check if any fact is semantically similar to *query*.

        ChromaDB returns distances in its query response — cosine
        distance where 0 = identical, 2 = opposite.  If any returned
        distance is <= threshold, the fact is a near-duplicate.
        """
        collection = self._col("semantic")
        result = await self._thread(
            lambda: collection.query(
                query_texts=[query],
                n_results=limit,
                include=["distances"],
            )
        )

        distances = result.get("distances", [])
        if not distances or not distances[0]:
            return False

        # Chroma default is cosine distance: 0 = identical, 2 = opposite
        # Lower distance = more similar.  Threshold 0.8 means
        # "anything with cosine distance <= 0.8 is considered duplicate"
        return any(d <= distance_threshold for d in distances[0])

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

        collection = self._col("episodic")
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
        collection = self._col("episodic")
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
                # Vector search found nothing — fall back to full scan
                # so metadata filters (event_types, time_range) still work.
                results = await self._thread(
                    lambda: collection.get(include=["documents", "metadatas"])
                )
            else:
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
        collection = self._col("episodic")

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
        """Remove events older than *before_timestamp*. Returns count deleted."""
        collection = self._col("episodic")

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

        collection = self._col("procedure")
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
        collection = self._col("procedure")
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
        collection = self._col("procedure")

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

        collection = self._col("audit")
        await self._thread(
            lambda: collection.add(
                documents=[action],
                metadatas=[metadata],
                ids=[entry_id],
            )
        )

    # ── Generic deletion ─────────────────────────────────────────

    async def delete_memory(self, target_type: str, target_id: str) -> None:
        """Delete a memory entry by type and ID."""
        collection_suffix: str | None
        match target_type:
            case "semantic":
                collection_suffix = "semantic"
            case "episodic":
                collection_suffix = "episodic"
            case "procedure":
                collection_suffix = "procedure"
            case "audit":
                collection_suffix = "audit"
            case _:
                return  # Unknown type — silently ignore

        collection_name = f"{self._collection_prefix}_{collection_suffix}"
        collection = self._collections[collection_name]
        await self._thread(
            lambda: collection.delete(ids=[target_id])
        )
