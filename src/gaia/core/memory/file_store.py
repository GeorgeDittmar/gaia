"""Flat-file ``MemoryStore`` implementation.

Each fact, event, procedure, and audit entry is a separate Markdown file
with YAML-style frontmatter.  The directory layout under the configured
``memory_dir`` is:

``memory_dir/``
├── semantic/      <fact_id>.md
├── episodic/      <event_id>.md
├── procedures/    <procedure_id>.md
└── audit/         <entry_id>.md

Search uses simple keyword token matching (TF-ish, ranked by recency).
No external dependencies — all I/O is ``asyncio.to_thread()`` wrapped.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gaia.core.memory.base import MemoryStore

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────

_STOPWORDS = frozenset([
    "a", "an", "the", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "shall", "can",
    "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "as", "into", "through", "during", "before", "after", "above",
    "below", "between", "and", "but", "or", "not", "no", "nor",
    "if", "then", "than", "too", "very", "just", "about",
    "what", "which", "who", "whom", "this", "that", "these",
    "those", "i", "me", "my", "we", "us", "our", "you", "your",
    "he", "him", "his", "she", "her", "it", "its", "they",
])


# ── Helpers ────────────────────────────────────────────────────────

def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(UTC).isoformat()


def _uuid() -> str:
    """Return a random UUID4 string."""
    return str(uuid.uuid4())


# ── Frontmatter parsing ───────────────────────────────────────────

_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(.*?)\n---\s*\n?(.*)", re.DOTALL
)

_YAML_PAIR_RE = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$", re.MULTILINE
)


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse YAML-like frontmatter + body.

    Returns ``(metadata_dict, body_text)``.
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text

    meta: dict[str, str] = {}
    for ym in _YAML_PAIR_RE.finditer(m.group(1)):
        key = ym.group(1)
        val = ym.group(2).strip()
        if val == "null":
            meta[key] = None
        elif val in ("true", "false"):
            meta[key] = val == "true"
        else:
            meta[key] = val

    body = m.group(2)
    return meta, body


def _build_frontmatter(meta: dict[str, Any]) -> str:
    """Build a ``---``-delimited frontmatter block."""
    lines = ["---"]
    for k, v in meta.items():
        if v is None:
            lines.append(f"{k}: null")
        elif isinstance(v, bool):
            lines.append(f"{k}: {'true' if v else 'false'}")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines) + "\n"


# ── Search scoring ─────────────────────────────────────────────────

def _tokenize(text: str) -> list[str]:
    """Lowercase, split on non-alphanumeric chars, drop stopwords."""
    return [
        t for t in re.split(r"[^a-z0-9]+", text.lower())
        if t and t not in _STOPWORDS
    ]


def _score_query(query: str, body: str) -> int:
    """Return an integer score: number of matching keyword tokens."""
    q_tokens = set(_tokenize(query))
    if not q_tokens:
        return 0
    body_tokens = _tokenize(body)
    return sum(1 for t in body_tokens if t in q_tokens)


# ── Main store ─────────────────────────────────────────────────────


class FileMemoryStore(MemoryStore):
    """Flat-file implementation of the ``MemoryStore`` protocol.

    Each entry is one Markdown file with YAML frontmatter.
    Search is keyword-token-based, ranked by match count then recency.

    Args:
        memory_dir: Root directory for all memory files.
            Defaults to ``~/.gaia/memory``.
    """

    _SUBDIRS: tuple[str, ...] = (
        "semantic",
        "episodic",
        "procedures",
        "audit",
    )

    def __init__(self, memory_dir: str | Path | None = None) -> None:
        if memory_dir is None:
            memory_dir = Path.home() / ".gaia" / "memory"
        self._memory_dir = Path(memory_dir)
        self._initialized = False
        self._index: dict[str, tuple[str, dict[str, str]]] = {}  # key -> (path_str, meta)
        self._index_path = self._memory_dir.parent / ".gaia_memory_index.json"

    # ── Lifecycle ────────────────────────────────────────────────

    async def initialize(self) -> None:
        """Create subdirectories if they do not exist and build the index."""
        def _ensure() -> None:
            for sub in self._SUBDIRS:
                (self._memory_dir / sub).mkdir(parents=True, exist_ok=True)

        await asyncio.to_thread(_ensure)
        self._build_index()
        self._initialized = True

    async def close(self) -> None:
        """No external resources to release."""

    # ── Helpers ──────────────────────────────────────────────────

    def _list_md(self, subdir: str) -> list[Path]:
        """Return all ``.md`` files in *subdir*, newest first."""
        d = self._memory_dir / subdir
        if not d.is_dir():
            return []
        files = list(d.glob("*.md"))
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return files

    def _read_file(self, path: Path) -> tuple[dict[str, Any], str]:
        """Read a single Markdown file, return ``(meta, body)``."""
        text = path.read_text(encoding="utf-8")
        return _parse_frontmatter(text)

    def _write_file(self, path: Path, meta: dict[str, Any], body: str) -> None:
        """Atomically write a Markdown file (temp + rename)."""
        tmp = path.with_suffix(".tmp")
        content = _build_frontmatter(meta) + body.rstrip() + "\n"
        tmp.write_text(content, encoding="utf-8")
        tmp.rename(path)

    def _build_index(self) -> None:
        """Scan all .md files and build the in-memory metadata index."""
        for subdir in self._SUBDIRS:
            files = self._list_md(subdir)
            for path in files:
                try:
                    meta, _body = self._read_file(path)
                    # Build index key: subdirectory/event_id
                    key = f"{subdir}/{meta.get('event_id', '')}"
                    if key.endswith("/"):
                        continue  # skip entries without an event_id
                    self._index[key] = (str(path), {
                        "event_id": meta.get("event_id", ""),
                        "event_type": meta.get("event_type", ""),
                        "conversation_id": meta.get("conversation_id", ""),
                        "timestamp": meta.get("timestamp", ""),
                    })
                except Exception:
                    continue

    def _persist_index(self) -> None:
        """Write the in-memory index to disk atomically."""
        if not self._index_path:
            return
        try:
            serializable = {
                key: (path, meta)
                for key, (path, meta) in self._index.items()
            }
            self._index_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._index_path.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(serializable, f)
            os.replace(tmp, self._index_path)
        except Exception:
            logger.debug("failed to persist index sidecar")

    async def _rebuild_index(self) -> None:
        """Rebuild the in-memory index from disk (runs in a thread)."""
        await asyncio.to_thread(self._build_index)

    def _scoring_search(
        self, subdir: str, query: str, limit: int, **filters: Any
    ) -> list[dict]:
        """Keyword-token scoring search with metadata filters."""
        files = self._list_md(subdir)
        scored: list[tuple[int, float, dict[str, Any], str]] = []

        for path in files:
            try:
                meta, body = self._read_file(path)
            except Exception:
                continue

            # Apply metadata filters
            match = True
            for k, v in filters.items():
                if v is not None and meta.get(k) != v:
                    match = False
                    break

            if not match:
                continue

            score = _score_query(query, body)
            if score > 0:
                created = meta.get("created_at", "") or meta.get("timestamp", "")
                scored.append((score, self._parse_ts(created), meta, body))

        # Sort: highest score first, then newest
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return scored[:limit]

    @staticmethod
    def _parse_ts(s: str) -> float:
        """Parse an ISO-8601 timestamp to a sortable float."""
        try:
            return datetime.fromisoformat(s).timestamp()
        except (ValueError, TypeError):
            return 0.0

    # ── Semantic operations ──────────────────────────────────────

    async def semantic_insert(
        self,
        content: str,
        category: str,
        confidence: float,
        source: str,
        source_id: str | None = None,
    ) -> str:
        """Insert a semantic fact. Returns the new fact ID."""
        fact_id = _uuid()
        now = _now_iso()
        meta: dict[str, Any] = {
            "fact_id": fact_id,
            "category": category,
            "confidence": confidence,
            "source": source,
            "source_id": source_id,
            "created_at": now,
            "updated_at": now,
        }
        path = self._memory_dir / "semantic" / f"{fact_id}.md"
        await asyncio.to_thread(self._write_file, path, meta, content)
        return fact_id

    async def semantic_update(
        self, fact_id: str, new_content: str, new_confidence: float
    ) -> None:
        """Update a fact's content and confidence."""
        path = self._memory_dir / "semantic" / f"{fact_id}.md"
        if not path.exists():
            return

        def _update() -> None:
            meta, _ = self._read_file(path)
            meta["content"] = new_content
            meta["confidence"] = new_confidence
            meta["updated_at"] = _now_iso()
            # Body is just the content now
            self._write_file(path, meta, new_content)

        await asyncio.to_thread(_update)

    async def semantic_delete(self, fact_id: str) -> None:
        """Remove a fact by ID."""
        path = self._memory_dir / "semantic" / f"{fact_id}.md"
        if path.exists():
            await asyncio.to_thread(path.unlink)
        # Clean up stale index entry
        self._index.pop(f"semantic/{fact_id}", None)

    async def semantic_search(self, query: str, limit: int = 10) -> list[dict]:
        """Keyword-token search on semantic facts, ranked by relevance."""
        results = self._scoring_search("semantic", query, limit)
        records: list[dict] = []
        for _score, _ts, meta, body in results:
            records.append({
                "fact_id": meta.get("fact_id", ""),
                "content": body,
                "category": meta.get("category", "general"),
                "confidence": float(meta.get("confidence", 0.0)),
                "source": meta.get("source", "unknown"),
                "source_id": meta.get("source_id"),
                "created_at": meta.get("created_at", ""),
                "updated_at": meta.get("updated_at", ""),
            })
        return records

    async def semantic_similarity_search(
        self, query: str, limit: int = 5, distance_threshold: float = 0.8
    ) -> bool:
        """Check if any fact is similar to *query*.

        Falls back to Jaccard token overlap since the file store
        has no embeddings.  distance_threshold here is the minimum
        Jaccard score to consider a match.
        """
        results = self._scoring_search("semantic", query, limit)
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return False

        for _score, _ts, _meta, body in results:
            body_tokens = self._tokenize(body)
            if not body_tokens:
                continue
            intersection = query_tokens & body_tokens
            union = query_tokens | body_tokens
            jaccard = len(intersection) / len(union) if union else 0.0
            if jaccard >= distance_threshold:
                return True
        return False

    # ── Episodic operations ──────────────────────────────────────

    async def episodic_record(
        self,
        event_type: str,
        conversation_id: str | None = None,
        summary: str = "",
        payload: dict | None = None,
    ) -> str:
        """Record an episodic event. Returns the event ID."""
        event_id = _uuid()
        now = _now_iso()
        payload_json = json.dumps(payload or {})
        meta: dict[str, Any] = {
            "event_id": event_id,
            "event_type": event_type,
            "conversation_id": conversation_id,
            "timestamp": now,
            "payload": payload_json,
            "created_at": now,
        }
        path = self._memory_dir / "episodic" / f"{event_id}.md"
        await asyncio.to_thread(self._write_file, path, meta, summary)
        await self._rebuild_index()
        return event_id

    async def episodic_search(
        self,
        time_range: tuple[str, str] | None = None,
        event_types: list[str] | None = None,
        query: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Search episodic events with optional filters."""
        if query:
            # Scoring search
            scored = self._scoring_search("episodic", query, limit * 5)

            if scored:
                records: list[dict] = []
                for _score, _ts, meta, body in scored:
                    if event_types is not None:
                        if meta.get("event_type", "") not in event_types:
                            continue
                    if time_range is not None:
                        ts = meta.get("timestamp", "")
                        if ts < time_range[0] or ts > time_range[1]:
                            continue
                    payload_raw = meta.get("payload", "{}")
                    records.append({
                        "event_id": meta.get("event_id", ""),
                        "event_type": meta.get("event_type", "unknown"),
                        "conversation_id": meta.get("conversation_id"),
                        "timestamp": meta.get("timestamp", ""),
                        "summary": body,
                        "payload": payload_raw,
                        "created_at": meta.get("created_at", ""),
                    })
                return records[:limit]

            # Vector/token search found nothing — fall back to full scan
            # so metadata filters still work.

        # No query — use index for filtered scan
        candidates: list[tuple[float, dict, str]] = []
        for key, (path_str, meta) in self._index.items():
            if not key.startswith("episodic/"):
                continue
            if event_types is not None and meta.get("event_type", "") not in event_types:
                continue
            ts = meta.get("timestamp", "")
            if time_range is not None:
                if ts < time_range[0] or ts > time_range[1]:
                    continue
            candidates.append((self._parse_ts(ts), meta, path_str))

        candidates.sort(key=lambda x: x[0], reverse=True)

        # Read bodies for the top N only
        result: list[dict] = []
        for _ts, meta, path_str in candidates[:limit]:
            try:
                _, body = self._read_file(Path(path_str))
            except Exception:
                body = ""
            result.append({
                "event_id": meta.get("event_id", ""),
                "event_type": meta.get("event_type", "unknown"),
                "conversation_id": meta.get("conversation_id"),
                "timestamp": meta.get("timestamp", ""),
                "summary": body,
                "payload": meta.get("payload", "{}"),
                "created_at": meta.get("created_at", ""),
            })
        return result

    async def episodic_get_conversation(
        self, conversation_id: str
    ) -> list[dict]:
        """Retrieve all events for a conversation, ordered by time."""
        matches: list[tuple[float, dict, str]] = []

        for key, (path_str, meta) in self._index.items():
            if not key.startswith("episodic/"):
                continue
            if meta.get("conversation_id") != conversation_id:
                continue
            matches.append((self._parse_ts(meta.get("timestamp", "")), meta, path_str))

        matches.sort(key=lambda x: x[0])

        result: list[dict] = []
        for _ts, meta, path_str in matches:
            try:
                _, body = self._read_file(Path(path_str))
            except Exception:
                body = ""
            result.append({
                "event_id": meta.get("event_id", ""),
                "event_type": meta.get("event_type", "unknown"),
                "conversation_id": meta.get("conversation_id"),
                "timestamp": meta.get("timestamp", ""),
                "summary": body,
                "payload": meta.get("payload", "{}"),
                "created_at": meta.get("created_at", ""),
            })
        return result

    async def episodic_prune(self, before_timestamp: str) -> int:
        """Delete episodic events older than *before_timestamp*.

        Uses the in-memory index to filter by timestamp without
        reading all file bodies.  Rebuilds the index first to
        pick up any out-of-band file changes.
        """
        await self._rebuild_index()
        to_delete: list[tuple[str, str]] = []  # (path_str, key)

        for key, (path_str, meta) in self._index.items():
            if not key.startswith("episodic/"):
                continue
            if meta.get("timestamp", "") < before_timestamp:
                to_delete.append((path_str, key))

        if not to_delete:
            return 0

        # Delete files and remove from index
        count = 0
        for path_str, key in to_delete:
            try:
                Path(path_str).unlink()
                del self._index[key]
                count += 1
            except Exception:
                continue

        # Persist updated index
        self._persist_index()
        return count

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
        meta: dict[str, Any] = {
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
        }
        body = description
        path = self._memory_dir / "procedures" / f"{procedure_id}.md"
        await asyncio.to_thread(self._write_file, path, meta, body)

    async def procedure_get(self, procedure_id: str) -> dict | None:
        """Retrieve a procedure by ID, or ``None`` if not found."""
        path = self._memory_dir / "procedures" / f"{procedure_id}.md"
        if not path.exists():
            return None

        def _get() -> dict | None:
            meta, _ = self._read_file(path)
            return dict(meta)

        return await asyncio.to_thread(_get)

    async def procedure_search(
        self, query: str, category: str | None = None
    ) -> list[dict]:
        """Keyword-token search on procedure name + description."""
        files = self._list_md("procedures")
        scored: list[tuple[int, float, dict[str, Any], str]] = []

        for path in files:
            try:
                meta, body = self._read_file(path)
            except Exception:
                continue

            if category is not None:
                if meta.get("category") != category:
                    continue

            searchable = f"{meta.get('name', '')} {body}"
            score = _score_query(query, searchable)
            if score > 0:
                scored.append((score, self._parse_ts(meta.get("created_at", "")), meta, body))

        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        results = scored[:100]

        records: list[dict] = []
        for _score, _ts, meta, _body in results:
            records.append({
                "procedure_id": meta.get("procedure_id", ""),
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
        meta: dict[str, Any] = {
            "entry_id": entry_id,
            "timestamp": now,
            "action": action,
            "actor": actor,
            "target_type": target_type,
            "target_id": target_id,
            "detail": json.dumps(detail) if detail else None,
        }
        path = self._memory_dir / "audit" / f"{entry_id}.md"
        await asyncio.to_thread(self._write_file, path, meta, action)

    # ── Generic deletion ─────────────────────────────────────────

    async def delete_memory(self, target_type: str, target_id: str) -> None:
        """Delete a memory entry by type and ID."""
        subdir: str | None
        match target_type:
            case "semantic":
                subdir = "semantic"
            case "episodic":
                subdir = "episodic"
            case "procedure":
                subdir = "procedures"
            case "audit":
                subdir = "audit"
            case _:
                return  # Unknown type — silently ignore

        path = self._memory_dir / subdir / f"{target_id}.md"
        if path.exists():
            await asyncio.to_thread(path.unlink)
        # Clean up stale index entry if it exists
        self._index.pop(f"{subdir}/{target_id}", None)
