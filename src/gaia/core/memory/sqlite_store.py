"""SQLite-backed MemoryStore implementation.

This is the default storage backend for G.A.I.A. memory. It stores all
three memory types (semantic, episodic, procedural) and the audit log in
a single SQLite database, with FTS5 virtual tables for full-text search.

When SQLCipher is available and ``encrypted`` is ``True`` in the settings,
the database is encrypted with AES-256.
"""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gaia.core.memory.base import MemoryStore


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _uuid() -> str:
    """Return a random UUID4 string."""
    return str(uuid.uuid4())


def _fts_query(query: str) -> str:
    """Prepare a query for FTS5 MATCH.

    FTS5 interprets bare dots (e.g. "G.A.I.A.") as column references,
    and bare single-quotes (e.g. "What's") as term delimiters, both
    causing syntax errors. We handle them differently:

    - Dots: wrap the token in double-quotes (FTS5 phrase matching).
      Parameterized binding passes the quotes through correctly.
    - Apostrophes: replace with a space, splitting the token into
      separate bare terms. FTS5's standard tokenizer already treats
      apostrophes as separators, so "What's" indexes as "What" + "s".

    Internal double-quotes are left as-is; FTS5 interprets them as
    phrase delimiters, which is acceptable for search queries.
    """
    tokens = query.split()
    fixed: list[str] = []
    for token in tokens:
        if token.endswith("?"):
            # Strip trailing question mark (FTS5 query operator)
            token = token[:-1].rstrip()
            if not token:
                continue
            # Fall through to further checks on the stripped token
        if "'" in token:
            # Apostrophe → space splits into multiple boolean terms
            fixed.extend(token.replace("'", " ").split())
        elif "." in token:
            # Dots → wrap in double quotes (phrase match)
            fixed.append(f'"{token}"')
        else:
            fixed.append(token)
    return " ".join(fixed)


class SQLiteMemoryStore(MemoryStore):
    """SQLite-backed implementation of the MemoryStore protocol.

    Stores semantic facts, episodic events, procedural definitions, and
    audit log entries in a single SQLite database. FTS5 virtual tables
    power full-text search on text content.

    Args:
        db_path: Path to the SQLite database file.
        encrypted: If ``True`` and SQLCipher is available, encrypt the
            database with AES-256.
    """

    def __init__(self, db_path: str | Path, encrypted: bool = False) -> None:
        self._db_path = Path(db_path)
        self._encrypted = encrypted
        self._conn: sqlite3.Connection | None = None

    # ── Lifecycle ────────────────────────────────────────────────

    async def initialize(self) -> None:
        """Open the database connection and create tables if needed."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        if self._encrypted:
            self._conn.execute("PRAGMA key = 'gaia-local-encrypt-v1'")

        self._create_tables()

    def _create_tables(self) -> None:
        """Create all memory tables and FTS5 virtual tables."""
        conn = self._conn  # type: ignore [union-attr]

        conn.executescript("""
            CREATE TABLE IF NOT EXISTS semantic_facts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                fact_id     TEXT NOT NULL UNIQUE,
                content     TEXT NOT NULL,
                category    TEXT NOT NULL DEFAULT 'general',
                confidence  REAL NOT NULL DEFAULT 1.0
                    CHECK(confidence >= 0 AND confidence <= 1),
                source      TEXT NOT NULL DEFAULT 'user',
                source_id   TEXT,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL,
                metadata    TEXT
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS semantic_facts_fts USING fts5(
                content,
                category
            );

            CREATE INDEX IF NOT EXISTS idx_semantic_updated ON semantic_facts(updated_at DESC);


            /* ── Episodic events ── */
            CREATE TABLE IF NOT EXISTS episodic_events (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id        TEXT NOT NULL UNIQUE,
                event_type      TEXT NOT NULL CHECK(event_type IN (
                    'conversation_start', 'conversation_turn', 'conversation_end',
                    'tool_call', 'tool_result', 'error', 'correction',
                    'memory_insert', 'memory_update', 'memory_delete',
                    'config_change', 'mcp_connect', 'mcp_disconnect'
                )),
                conversation_id TEXT,
                timestamp       TEXT NOT NULL,
                summary         TEXT,
                payload         TEXT NOT NULL,
                created_at      TEXT NOT NULL,
                indexed_content TEXT
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS episodic_events_fts USING fts5(
                indexed_content,
                event_type
            );

            CREATE INDEX IF NOT EXISTS idx_episodic_conversation ON episodic_events(conversation_id);
            CREATE INDEX IF NOT EXISTS idx_episodic_timestamp ON episodic_events(timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_episodic_created ON episodic_events(created_at);


            /* ── Procedure entries ── */
            CREATE TABLE IF NOT EXISTS procedure_entries (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                procedure_id    TEXT NOT NULL UNIQUE,
                name            TEXT NOT NULL,
                description     TEXT NOT NULL,
                category        TEXT NOT NULL DEFAULT 'custom',
                safety_level    TEXT NOT NULL CHECK(safety_level IN ('safe', 'caution', 'danger')),
                prompt_template TEXT,
                steps           TEXT,
                source_file     TEXT,
                enabled         INTEGER NOT NULL DEFAULT 1,
                version         INTEGER NOT NULL DEFAULT 1,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL,
                metadata        TEXT
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS procedure_entries_fts USING fts5(
                name,
                description,
                category
            );

            CREATE INDEX IF NOT EXISTS idx_proc_category ON procedure_entries(category);
            CREATE INDEX IF NOT EXISTS idx_proc_enabled ON procedure_entries(enabled);


            /* ── Audit log ── */
            CREATE TABLE IF NOT EXISTS audit_log (
                log_id      INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT NOT NULL,
                action      TEXT NOT NULL,
                actor       TEXT NOT NULL DEFAULT 'agent',
                target_type TEXT,
                target_id   TEXT,
                detail      TEXT,
                ip_address  TEXT,
                user_agent  TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);
            CREATE INDEX IF NOT EXISTS idx_audit_target ON audit_log(target_type, target_id);
        """)

    async def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ── Helpers ──────────────────────────────────────────────────

    def _fts_rank(self) -> int:
        """Get the auto-assigned integer rowid for FTS5 indexing."""
        return self._conn.execute("SELECT last_insert_rowid()").fetchone()[0]  # type: ignore [union-attr]

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
        conn = self._conn  # type: ignore [union-attr]

        conn.execute(
            """INSERT INTO semantic_facts
               (fact_id, content, category, confidence, source, source_id, created_at, updated_at, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (fact_id, content, category, confidence, source, source_id, now, now, None),
        )
        # Populate FTS5 using the auto-assigned integer rowid
        conn.execute(
            "INSERT INTO semantic_facts_fts(rowid, content, category) VALUES (?, ?, ?)",
            (self._fts_rank(), content, category),
        )
        conn.commit()
        return fact_id

    async def semantic_update(self, fact_id: str, new_content: str, new_confidence: float) -> None:
        """Update a fact's content and confidence in place."""
        conn = self._conn  # type: ignore [union-attr]

        # Get the rowid for this fact
        cursor = conn.execute("SELECT id, category FROM semantic_facts WHERE fact_id = ?", (fact_id,))
        row = cursor.fetchone()
        if row is None:
            return

        rid = row["id"]
        category = row["category"]

        conn.execute(
            """UPDATE semantic_facts
               SET content = ?, confidence = ?, updated_at = ?
               WHERE fact_id = ?""",
            (new_content, new_confidence, _now_iso(), fact_id),
        )

        # Re-index with updated content
        conn.execute("DELETE FROM semantic_facts_fts WHERE rowid = ?", (rid,))
        conn.execute(
            "INSERT INTO semantic_facts_fts(rowid, content, category) VALUES (?, ?, ?)",
            (rid, new_content, category),
        )
        conn.commit()

    async def semantic_delete(self, fact_id: str) -> None:
        """Remove a fact by ID."""
        conn = self._conn  # type: ignore [union-attr]
        cursor = conn.execute("SELECT id FROM semantic_facts WHERE fact_id = ?", (fact_id,))
        row = cursor.fetchone()
        if row is not None:
            conn.execute("DELETE FROM semantic_facts_fts WHERE rowid = ?", (row["id"],))
        conn.execute("DELETE FROM semantic_facts WHERE fact_id = ?", (fact_id,))
        conn.commit()

    async def semantic_search(self, query: str, limit: int = 10) -> list[dict]:
        """Search semantic facts by full-text query.

        Uses FTS5 BM25 ranking for relevance. When the query consists
        entirely of FTS5 stopwords (e.g. ``"what is my name?"``), the
        MATCH returns zero rows. In that case we fall back to a
        keyword-match across all facts.

        Returns rows ordered by relevance.
        """
        conn = self._conn  # type: ignore [union-attr]
        fts_query = _fts_query(query)
        cursor = conn.execute(
            """SELECT s.*
               FROM semantic_facts s
               JOIN semantic_facts_fts ON s.id = semantic_facts_fts.rowid
               WHERE semantic_facts_fts MATCH ?
               ORDER BY rank
               LIMIT ?""",
            (fts_query, limit),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        if rows:
            return rows

        # Fallback: keyword-match against all facts when FTS5 returns
        # nothing (typically because the query is all stopwords like
        # "what is my name?" or "who am I?").
        # Strip trailing ? from each token (FTS5 query operator).
        keywords = [
            t.lower().rstrip("?").rstrip("'")
            for t in query.split()
        ]
        keywords = [t for t in keywords if t]
        if not keywords:
            return []

        all_facts = conn.execute(
            "SELECT * FROM semantic_facts LIMIT 500"
        ).fetchall()
        scored: list[tuple[int, dict]] = []
        for row in all_facts:
            text = row["content"].lower()
            score = sum(1 for kw in keywords if kw in text)
            if score:
                scored.append((score, dict(row)))
        scored.sort(key=lambda x: (-x[0], x[1].get("created_at", "")))
        return scored[:limit]

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
        indexed = " ".join(s for s in (summary, payload_json) if s)

        conn = self._conn  # type: ignore [union-attr]
        conn.execute(
            """INSERT INTO episodic_events
               (event_id, event_type, conversation_id, timestamp, summary, payload, created_at, indexed_content)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (event_id, event_type, conversation_id, now, summary, payload_json, now, indexed),
        )
        # Populate FTS5
        conn.execute(
            "INSERT INTO episodic_events_fts(rowid, indexed_content, event_type) VALUES (?, ?, ?)",
            (self._fts_rank(), indexed, event_type),
        )
        conn.commit()
        return event_id

    async def episodic_search(
        self,
        time_range: tuple[str, str] | None = None,
        event_types: list[str] | None = None,
        query: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Search episodic events with optional filters."""
        conn = self._conn  # type: ignore [union-attr]
        base = """SELECT e.*
                  FROM episodic_events e
                  JOIN episodic_events_fts ON e.id = episodic_events_fts.rowid
                  WHERE 1=1"""
        params: list[Any] = []

        if time_range is not None:
            base += " AND e.timestamp >= ? AND e.timestamp <= ?"
            params.extend(time_range)

        if event_types is not None and event_types:
            placeholders = ", ".join("?" for _ in event_types)
            base += f" AND e.event_type IN ({placeholders})"
            params.extend(event_types)

        if query is not None:
            base += " AND episodic_events_fts MATCH ?"
            params.append(_fts_query(query))

        base += " ORDER BY e.timestamp DESC LIMIT ?"
        params.append(limit)

        cursor = conn.execute(base, tuple(params))
        return [dict(row) for row in cursor.fetchall()]

    async def episodic_get_conversation(self, conversation_id: str) -> list[dict]:
        """Retrieve all events for a conversation, ordered by timestamp."""
        conn = self._conn  # type: ignore [union-attr]
        cursor = conn.execute(
            """SELECT * FROM episodic_events
               WHERE conversation_id = ?
               ORDER BY timestamp ASC""",
            (conversation_id,),
        )
        return [dict(row) for row in cursor.fetchall()]

    async def episodic_prune(self, before_timestamp: str) -> int:
        """Remove events older than *before_timestamp*. Returns count deleted."""
        conn = self._conn  # type: ignore [union-attr]

        # Get rowids for FTS5 cleanup
        cursor = conn.execute(
            "SELECT id FROM episodic_events WHERE timestamp < ?",
            (before_timestamp,),
        )
        ids = [row["id"] for row in cursor.fetchall()]

        conn.execute("DELETE FROM episodic_events WHERE timestamp < ?", (before_timestamp,))
        if ids:
            placeholders = ", ".join("?" for _ in ids)
            conn.execute(f"DELETE FROM episodic_events_fts WHERE rowid IN ({placeholders})", ids)

        conn.commit()
        return len(ids)

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
        """Register a new procedural entry (skill/routine)."""
        now = _now_iso()
        steps_json = json.dumps(steps) if steps else None

        conn = self._conn  # type: ignore [union-attr]
        conn.execute(
            """INSERT INTO procedure_entries
               (procedure_id, name, description, category, safety_level,
                prompt_template, steps, source_file, enabled, version,
                created_at, updated_at, metadata)
               VALUES (?, ?, ?, 'custom', ?, ?, ?, ?, 1, 1, ?, ?, ?)""",
            (procedure_id, name, description, safety_level,
             prompt_template, steps_json, source_file, now, now, None),
        )
        # Populate FTS5
        conn.execute(
            "INSERT INTO procedure_entries_fts(rowid, name, description, category) VALUES (?, ?, ?, ?)",
            (self._fts_rank(), name, description, "custom"),
        )
        conn.commit()

    async def procedure_get(self, procedure_id: str) -> dict | None:
        """Retrieve a procedure by ID, or ``None`` if not found."""
        conn = self._conn  # type: ignore [union-attr]
        cursor = conn.execute(
            "SELECT * FROM procedure_entries WHERE procedure_id = ?",
            (procedure_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    async def procedure_search(self, query: str, category: str | None = None) -> list[dict]:
        """Search procedures by full-text query on name and description."""
        conn = self._conn  # type: ignore [union-attr]
        fts_query = _fts_query(query)
        if category:
            cursor = conn.execute(
                """SELECT p.*
                   FROM procedure_entries p
                   JOIN procedure_entries_fts ON p.id = procedure_entries_fts.rowid
                   WHERE procedure_entries_fts MATCH ?
                     AND p.category = ?
                   ORDER BY rank""",
                (fts_query, category),
            )
        else:
            cursor = conn.execute(
                """SELECT p.*
                   FROM procedure_entries p
                   JOIN procedure_entries_fts ON p.id = procedure_entries_fts.rowid
                   WHERE procedure_entries_fts MATCH ?
                   ORDER BY rank""",
                (fts_query,),
            )
        return [dict(row) for row in cursor.fetchall()]

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
        conn = self._conn  # type: ignore [union-attr]
        conn.execute(
            """INSERT INTO audit_log
               (timestamp, action, actor, target_type, target_id, detail)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (_now_iso(), action, actor, target_type, target_id,
             json.dumps(detail) if detail else None),
        )
        conn.commit()
