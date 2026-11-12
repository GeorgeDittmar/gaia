"""PostgreSQL-backed MemoryStore with pgvector.

Connects to a PostgreSQL instance with the ``pgvector`` extension enabled.
Semantic and episodic data live in separate tables.  Vector similarity
uses pgvector's ``<=>`` cosine distance operator, with embeddings
generated client-side via ``sentence-transformers`` (all-MiniLM-L6-v2).

Usage with docker-compose:

    postgres:
      image: pgvector/pgvector:pg17
      environment:
        POSTGRES_USER: gaia
        POSTGRES_PASSWORD: gaia
        POSTGRES_DB: gaia
      volumes:
        - pg-data:/var/lib/postgresql/data

Configure in settings:

    {
        "memory": {
            "backend": "postgres",
            "chroma_endpoint": "postgresql://gaia:gaia@localhost:5432/gaia"
        }
    }
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from gaia.core.memory.embedding import embedding_dim, generate_embedding

logger = logging.getLogger("gaia.memory")

# pgvector uses the same dimension as our embeddings
_EMBEDDING_DIM = embedding_dim()

# ── Schema ─────────────────────────────────────────────────────────

_CREATE_SCHEMA = f"""
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
        CREATE EXTENSION vector;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS semantic (
    fact_id     TEXT PRIMARY KEY,
    content     TEXT NOT NULL,
    embedding   vector({_EMBEDDING_DIM}) NOT NULL,
    category    TEXT NOT NULL DEFAULT 'general',
    confidence  DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    source      TEXT NOT NULL DEFAULT 'unknown',
    source_id   TEXT,
    metadata    JSONB,
    created_at  TIMESTAMPTZ NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_semantic_category ON semantic(category);
CREATE INDEX IF NOT EXISTS idx_semantic_source ON semantic(source);

CREATE TABLE IF NOT EXISTS episodic (
    event_id        TEXT PRIMARY KEY,
    event_type      TEXT NOT NULL,
    conversation_id TEXT,
    summary         TEXT NOT NULL DEFAULT '',
    payload         JSONB NOT NULL DEFAULT '{{}}',
    embedding       vector({_EMBEDDING_DIM}),
    timestamp       TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_episodic_event_type ON episodic(event_type);
CREATE INDEX IF NOT EXISTS idx_episodic_conversation ON episodic(conversation_id);
CREATE INDEX IF NOT EXISTS idx_episodic_timestamp ON episodic(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_episodic_ts_type ON episodic(timestamp DESC, event_type);

CREATE TABLE IF NOT EXISTS procedure_def (
    procedure_id      TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    description       TEXT NOT NULL,
    embedding         vector({_EMBEDDING_DIM}),
    category          TEXT NOT NULL DEFAULT 'custom',
    safety_level      TEXT NOT NULL DEFAULT 'safe',
    enabled           INTEGER NOT NULL DEFAULT 1,
    version           INTEGER NOT NULL DEFAULT 1,
    steps             JSONB,
    prompt_template   TEXT,
    source_file       TEXT,
    created_at        TIMESTAMPTZ NOT NULL,
    updated_at        TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_procedure_category ON procedure_def(category);

CREATE TABLE IF NOT EXISTS audit (
    entry_id    TEXT PRIMARY KEY,
    timestamp   TIMESTAMPTZ NOT NULL,
    action      TEXT NOT NULL,
    actor       TEXT NOT NULL DEFAULT 'agent',
    target_type TEXT,
    target_id   TEXT,
    detail      JSONB
);

CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit(action);
"""


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(UTC).isoformat()


def _sanitize_meta(meta: dict[str, Any]) -> dict[str, Any]:
    """Remove ``None`` values from metadata for JSONB safety."""
    return {k: v for k, v in meta.items() if v is not None}


def _embedding_vec(text: str) -> list[float]:
    """Generate an embedding vector for *text*."""
    return generate_embedding(text)


class PostgresMemoryStore:
    """PostgreSQL-backed implementation of the MemoryStore protocol.

    Uses pgvector for vector similarity search on semantic and episodic
    data.  Embeddings are generated client-side via sentence-transformers.

    Args:
        endpoint: PostgreSQL connection URI
            (e.g. ``postgresql://gaia:gaia@localhost:5432/gaia``).
        pool_size: Maximum pool size (default 5).
    """

    def __init__(
        self,
        endpoint: str | None = None,
        pool_size: int = 5,
    ) -> None:
        if endpoint is None:
            import os

            host = os.environ.get("POSTGRES_HOST", "localhost")
            port = os.environ.get("POSTGRES_PORT", "5432")
            user = os.environ.get("POSTGRES_USER", "gaia")
            password = os.environ.get("POSTGRES_PASSWORD", "gaia")
            db = os.environ.get("POSTGRES_DB", "gaia")
            endpoint = f"postgresql://{user}:{password}@{host}:{port}/{db}"

        self._conn_str = endpoint
        self._pool: Any | None = None

    # ── Lifecycle ────────────────────────────────────────────────

    async def initialize(self) -> None:
        """Connect to PostgreSQL and create schema if needed."""
        try:
            from psycopg import Connection
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool
        except ImportError:
            raise RuntimeError(
                "psycopg_pool is not installed. "
                "Install it with: uv add psycopg[binary,pool] pgvector"
            )

        self._pool = ConnectionPool(
            self._conn_str,
            min_size=1,
            max_size=pool_size,
            timeout=10,
            row_factory=dict_row,
        )

        # Wait for the pool to be ready
        with self._pool.connection() as conn:
            conn.execute("SELECT 1")

        # Create schema
        with self._pool.connection() as conn:
            conn.execute(_CREATE_SCHEMA)

        # Create IVFFlat index for faster vector search on episodic (optional, large tables)
        with self._pool.connection() as conn:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_episodic_embedding ON episodic "
                "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
            )
        logger.info("PostgreSQL memory store initialized (dim=%d)", _EMBEDDING_DIM)

    async def close(self) -> None:
        """Close the connection pool."""
        if self._pool:
            self._pool.close()
            self._pool = None

    # ── Helpers ──────────────────────────────────────────────────

    def _exec(self, sql: str, params: dict | None = None) -> None:
        """Execute a statement within the pool."""
        with self._pool.connection() as conn:
            conn.execute(sql, params or {})

    def _fetch(self, sql: str, params: dict | None = None) -> list[dict]:
        """Execute a query and return rows."""
        with self._pool.connection() as conn:
            return conn.execute(sql, params or {}).fetchall()

    # ── Semantic operations ──────────────────────────────────────

    async def semantic_insert(
        self,
        content: str,
        category: str,
        confidence: float,
        source: str,
        source_id: str | None = None,
    ) -> str:
        """Insert a semantic fact with embedding and return its UUID."""
        fact_id = str(uuid4())
        now = _now_iso()
        metadata = _sanitize_meta({
            "fact_id": fact_id,
            "category": category,
            "confidence": confidence,
            "source": source,
            "source_id": source_id,
        })

        self._exec(
            """INSERT INTO semantic
               (fact_id, content, embedding, category, confidence, source,
                source_id, metadata, created_at, updated_at)
               VALUES (%(fact_id)s, %(content)s, %(embedding)s,
                       %(category)s, %(confidence)s, %(source)s,
                       %(source_id)s, %(metadata)s, %(created_at)s, %(updated_at)s)""",
            {
                "fact_id": fact_id,
                "content": content,
                "embedding": _embedding_vec(content),
                "category": category,
                "confidence": confidence,
                "source": source,
                "source_id": source_id,
                "metadata": json.dumps(metadata),
                "created_at": now,
                "updated_at": now,
            },
        )
        return fact_id

    async def semantic_update(
        self, fact_id: str, new_content: str, new_confidence: float
    ) -> None:
        """Update a fact's content, confidence, and re-embed."""
        now = _now_iso()
        self._exec(
            """UPDATE semantic
               SET content = %(content)s, embedding = %(embedding)s,
                   confidence = %(confidence)s, updated_at = %(updated_at)s
               WHERE fact_id = %(fact_id)s""",
            {
                "fact_id": fact_id,
                "content": new_content,
                "embedding": _embedding_vec(new_content),
                "confidence": new_confidence,
                "updated_at": now,
            },
        )

    async def semantic_delete(self, fact_id: str) -> None:
        """Remove a fact by ID."""
        self._exec(
            "DELETE FROM semantic WHERE fact_id = %(fact_id)s",
            {"fact_id": fact_id},
        )

    async def semantic_search(self, query: str, limit: int = 10) -> list[dict]:
        """Search semantic facts by vector similarity."""
        query_vec = _embedding_vec(query)
        rows = self._fetch(
            """SELECT fact_id, content, category, confidence, source,
                      source_id, created_at, updated_at, metadata
               FROM semantic
               ORDER BY embedding <=> %(query_vec)s::vector
               LIMIT %(limit)s""",
            {"query_vec": query_vec, "limit": limit},
        )
        return [
            {
                "fact_id": r["fact_id"],
                "content": r["content"] or "",
                "category": r["category"] or "general",
                "confidence": float(r.get("confidence", 0.0)),
                "source": r["source"] or "unknown",
                "source_id": r.get("source_id"),
                "created_at": r["created_at"] or "",
                "updated_at": r.get("updated_at", ""),
                "metadata": json.loads(r["metadata"]) if r.get("metadata") else None,
            }
            for r in rows
        ]

    async def semantic_similarity_search(
        self, query: str, limit: int = 5, distance_threshold: float = 0.3
    ) -> list[dict]:
        """Find facts semantically similar to *query*.

        Returns a list of matching fact dicts (empty list if none), sorted
        by cosine distance ascending.  Each dict contains ``fact_id``,
        ``content``, ``distance`` (cosine), and other fields.
        Results are filtered to only include facts with distance <= threshold.
        """
        query_vec = _embedding_vec(query)
        rows = self._fetch(
            """SELECT fact_id, content, category, confidence, source,
                      source_id, created_at, updated_at, metadata,
                      (embedding <=> %(query_vec)s::vector) AS dist
               FROM semantic
               ORDER BY embedding <=> %(query_vec)s::vector
               LIMIT %(limit)s""",
            {"query_vec": query_vec, "limit": limit},
        )

        records: list[dict] = []
        for r in rows:
            dist = float(r["dist"])
            if dist > distance_threshold:
                break  # results are sorted by distance — stop early

            records.append({
                "fact_id": r["fact_id"],
                "content": r["content"] or "",
                "category": r["category"] or "general",
                "confidence": float(r.get("confidence", 0.0)),
                "source": r["source"] or "unknown",
                "source_id": r.get("source_id"),
                "created_at": r["created_at"] or "",
                "updated_at": r.get("updated_at", ""),
                "metadata": json.loads(r["metadata"]) if r.get("metadata") else None,
                "distance": dist,
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
        """Record an episodic event with embedding and return its UUID."""
        event_id = str(uuid4())
        now = _now_iso()
        self._exec(
            """INSERT INTO episodic
               (event_id, event_type, conversation_id, summary, payload,
                embedding, timestamp, created_at)
               VALUES (%(event_id)s, %(event_type)s, %(conversation_id)s,
                       %(summary)s, %(payload)s, %(embedding)s,
                       %(timestamp)s, %(created_at)s)""",
            {
                "event_id": event_id,
                "event_type": event_type,
                "conversation_id": conversation_id,
                "summary": summary,
                "payload": json.dumps(payload or {}),
                "embedding": _embedding_vec(summary) if summary else None,
                "timestamp": now,
                "created_at": now,
            },
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
        - If a text *query* is provided, use pgvector cosine distance
          on the summary column, then filter by *event_types* and
          *time_range*.
        - Otherwise, fetch by metadata filters ordered by timestamp.
        """
        where_clauses: list[str] = []
        where_params: dict[str, Any] = {"limit": limit}

        if time_range:
            where_clauses.append("timestamp >= %(time_start)s AND timestamp <= %(time_end)s")
            where_params["time_start"] = time_range[0]
            where_params["time_end"] = time_range[1]

        if event_types is not None and len(event_types) > 0:
            placeholders = ",".join(f"%(etype_{i})s" for i in range(len(event_types)))
            where_clauses.append(f"event_type IN ({placeholders})")
            for i, et in enumerate(event_types):
                where_params[f"etype_{i}"] = et

        where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        where_params["limit"] = limit

        if query:
            # Vector search with metadata filtering
            query_vec = _embedding_vec(query)
            where_params["query_vec"] = query_vec
            # Add distance filter — only include results with reasonable similarity
            where_clauses.append("(embedding <=> %(query_vec)s::vector) < 1.5")
            where_sql = " WHERE " + " AND ".join(where_clauses)
            sql = (
                f"SELECT event_id, event_type, conversation_id, summary, "
                f"payload, timestamp, created_at "
                f"FROM episodic{where_sql} "
                f"ORDER BY (embedding <=> %(query_vec)s::vector) ASC "
                f"LIMIT %(limit)s"
            )
        else:
            # Metadata-only query (fast, indexed)
            sql = (
                f"SELECT event_id, event_type, conversation_id, summary, "
                f"payload, timestamp, created_at "
                f"FROM episodic{where_sql} "
                f"ORDER BY timestamp DESC LIMIT %(limit)s"
            )

        rows = self._fetch(sql, where_params)
        return [
            {
                "event_id": r["event_id"],
                "event_type": r.get("event_type", "unknown"),
                "conversation_id": r.get("conversation_id"),
                "timestamp": r.get("timestamp", "") or "",
                "summary": r.get("summary", "") or "",
                "payload": r.get("payload", "{}"),
                "created_at": r.get("created_at", "") or "",
            }
            for r in rows
        ]

    async def episodic_get_conversation(self, conversation_id: str) -> list[dict]:
        """Retrieve all events for a conversation, ordered by time."""
        rows = self._fetch(
            """SELECT event_id, event_type, conversation_id, summary,
                      payload, timestamp, created_at
               FROM episodic
               WHERE conversation_id = %(cid)s
               ORDER BY timestamp ASC""",
            {"cid": conversation_id},
        )
        return [
            {
                "event_id": r["event_id"],
                "event_type": r.get("event_type", "unknown"),
                "conversation_id": r.get("conversation_id"),
                "timestamp": r.get("timestamp", "") or "",
                "summary": r.get("summary", "") or "",
                "payload": r.get("payload", "{}"),
                "created_at": r.get("created_at", "") or "",
            }
            for r in rows
        ]

    async def episodic_prune(self, before_timestamp: str) -> int:
        """Remove events older than *before_timestamp*. Returns count deleted."""
        result = self._fetch(
            "DELETE FROM episodic WHERE timestamp < %(before)s RETURNING event_id",
            {"before": before_timestamp},
        )
        return len(result)

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
        self._exec(
            """INSERT INTO procedure_def
               (procedure_id, name, description, embedding, category,
                safety_level, enabled, version, steps, prompt_template,
                source_file, created_at, updated_at)
               VALUES (%(procedure_id)s, %(name)s, %(description)s,
                       %(embedding)s, %(category)s, %(safety_level)s,
                       %(enabled)s, %(version)s, %(steps)s, %(prompt_template)s,
                       %(source_file)s, %(created_at)s, %(updated_at)s)
               ON CONFLICT (procedure_id) DO UPDATE
               SET name = EXCLUDED.name,
                   description = EXCLUDED.description,
                   updated_at = EXCLUDED.updated_at,
                   safety_level = EXCLUDED.safety_level,
                   steps = COALESCE(EXCLUDED.steps, procedure_def.steps),
                   prompt_template = COALESCE(EXCLUDED.prompt_template, procedure_def.prompt_template),
                   source_file = COALESCE(EXCLUDED.source_file, procedure_def.source_file)""",
            {
                "procedure_id": procedure_id,
                "name": name,
                "description": description,
                "embedding": _embedding_vec(f"{name} {description}"),
                "category": "custom",
                "safety_level": safety_level,
                "enabled": 1,
                "version": 1,
                "steps": json.dumps(steps) if steps else None,
                "prompt_template": prompt_template,
                "source_file": source_file,
                "created_at": now,
                "updated_at": now,
            },
        )

    async def procedure_get(self, procedure_id: str) -> dict | None:
        """Retrieve a procedure by ID, or ``None`` if not found."""
        rows = self._fetch(
            """SELECT procedure_id, name, description, category, safety_level,
                      enabled, version, source_file, created_at, updated_at
               FROM procedure_def WHERE procedure_id = %(pid)s""",
            {"pid": procedure_id},
        )
        if not rows:
            return None
        r = rows[0]
        return {
            "procedure_id": r["procedure_id"],
            "name": r["name"],
            "description": r["description"],
            "category": r.get("category", "custom"),
            "safety_level": r.get("safety_level", "safe"),
            "enabled": r.get("enabled", 1),
            "version": r.get("version", 1),
            "source_file": r.get("source_file"),
            "created_at": r.get("created_at", ""),
            "updated_at": r.get("updated_at", ""),
        }

    async def procedure_search(
        self, query: str, category: str | None = None
    ) -> list[dict]:
        """Search procedures by vector similarity on name + description."""
        query_vec = _embedding_vec(query)
        where_clauses = ["(name || ' ' || description) ILIKE %(q)s"]
        params: dict[str, Any] = {"query_vec": query_vec, "q": f"%{query}%"}
        if category:
            where_clauses.append("category = %(cat)s")
            params["cat"] = category

        where_sql = " AND ".join(where_clauses)
        rows = self._fetch(
            f"""SELECT procedure_id, name, description, category,
                      safety_level, enabled, version, source_file
               FROM procedure_def
               WHERE {where_sql}
               ORDER BY embedding <=> %(query_vec)s::vector
               LIMIT 100""",
            params,
        )
        return [
            {
                "procedure_id": r["procedure_id"],
                "name": r["name"],
                "description": r["description"],
                "category": r.get("category", "custom"),
                "safety_level": r.get("safety_level", "safe"),
                "enabled": r.get("enabled", 1),
                "version": r.get("version", 1),
                "source_file": r.get("source_file"),
                "created_at": r.get("created_at", ""),
            }
            for r in rows
        ]

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
        entry_id = str(uuid4())
        self._exec(
            """INSERT INTO audit
               (entry_id, timestamp, action, actor, target_type, target_id, detail)
               VALUES (%(entry_id)s, %(timestamp)s, %(action)s, %(actor)s,
                       %(target_type)s, %(target_id)s, %(detail)s)""",
            {
                "entry_id": entry_id,
                "timestamp": now,
                "action": action,
                "actor": actor,
                "target_type": target_type,
                "target_id": target_id,
                "detail": json.dumps(detail) if detail else None,
            },
        )

    # ── Generic deletion ─────────────────────────────────────────

    async def delete_memory(self, target_type: str, target_id: str) -> None:
        """Delete a memory entry by type and ID."""
        match target_type:
            case "semantic":
                self._exec("DELETE FROM semantic WHERE fact_id = %(id)s", {"id": target_id})
            case "episodic":
                self._exec("DELETE FROM episodic WHERE event_id = %(id)s", {"id": target_id})
            case "procedure":
                self._exec(
                    "DELETE FROM procedure_def WHERE procedure_id = %(id)s", {"id": target_id}
                )
            case "audit":
                self._exec("DELETE FROM audit WHERE entry_id = %(id)s", {"id": target_id})
            case _:
                pass  # Unknown type — silently ignore
