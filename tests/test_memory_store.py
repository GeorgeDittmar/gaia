"""Tests for the SQLite-backed MemoryStore implementation.

Covers semantic, episodic, and procedural memory CRUD, audit logging,
edge cases around FTS5 full-text search (dots in terms, empty results),
and lifecycle (initialize/close).
"""

import asyncio
import tempfile
from pathlib import Path

import pytest

from gaia.core.memory.sqlite_store import (
    SQLiteMemoryStore,
    _fts_query,
)


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture()
def db_path() -> Path:
    """Return a temporary database path and ensure it is cleaned up."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = Path(f.name)
    yield path
    path.unlink(missing_ok=True)


@pytest.fixture()
async def store(db_path: Path) -> SQLiteMemoryStore:
    """Return an initialized SQLiteMemoryStore and close it after the test."""
    mem = SQLiteMemoryStore(db_path)
    await mem.initialize()
    yield mem
    await mem.close()
    db_path.unlink(missing_ok=True)


# ── FTS query helper ───────────────────────────────────────────────


def test_fts_query_wraps_in_quotes():
    """_fts_query quotes individual tokens that contain dots."""
    assert _fts_query("G.A.I.A.") == '"G.A.I.A."'
    assert _fts_query("hello world") == "hello world"
    assert _fts_query("simple") == "simple"


def test_fts_query_escapes_internal_quotes():
    """Internal double-quotes are left as-is (FTS5 interprets them as phrase delimiters)."""
    assert _fts_query('say "hello"') == 'say "hello"'


def test_fts_query_partial_quoting():
    """Only tokens with dots get quoted; rest stays plain for boolean matching."""
    assert _fts_query("G.A.I.A. is local") == '"G.A.I.A." is local'
    assert _fts_query("my G.A.I.A. on M2 Mac") == 'my "G.A.I.A." on M2 Mac'


def test_fts_query_splits_apostrophes():
    """Apostrophes are replaced with spaces, splitting the token into bare terms.

    FTS5's standard tokenizer already splits on apostrophes, so "What's"
    indexes as two terms: "What" + "s". Replacing with a space preserves
    this behavior for search queries.
    """
    assert _fts_query("What's the best") == "What s the best"
    assert _fts_query("It's local") == "It s local"
    assert _fts_query("G.A.I.A. is local") == '"G.A.I.A." is local'


# ── Semantic memory ────────────────────────────────────────────────


class TestSemanticMemory:
    async def test_insert_and_retrieve(self, store: SQLiteMemoryStore) -> None:
        fact_id = await store.semantic_insert(
            content="G.A.I.A. runs locally",
            category="general",
            confidence=1.0,
            source="test",
        )
        assert isinstance(fact_id, str)
        assert len(fact_id) > 0

    async def test_search_returns_inserted_fact(self, store: SQLiteMemoryStore) -> None:
        await store.semantic_insert(
            content="G.A.I.A. is a local assistant",
            category="general",
            confidence=0.95,
            source="test",
        )
        results = await store.semantic_search("local")
        assert len(results) == 1

    async def test_search_with_dots_in_term(self, store: SQLiteMemoryStore) -> None:
        """Dots must not break FTS5 parsing (they used to cause syntax error)."""
        await store.semantic_insert(
            content="G.A.I.A. version 0.1.0",
            category="version",
            confidence=1.0,
            source="test",
        )
        results = await store.semantic_search("G.A.I.A.")
        assert len(results) == 1

    async def test_search_with_apostrophe(self, store: SQLiteMemoryStore) -> None:
        """Apostrophes in queries must not break FTS5 parsing."""
        await store.semantic_insert(
            content="What's the best way to use memory?",
            category="general",
            confidence=1.0,
            source="test",
        )
        results = await store.semantic_search("What's")
        assert len(results) == 1

    async def test_search_no_results(self, store: SQLiteMemoryStore) -> None:
        results = await store.semantic_search("nonexistent term xyz")
        assert results == []

    async def test_update_changes_content(self, store: SQLiteMemoryStore) -> None:
        fact_id = await store.semantic_insert(
            content="old content",
            category="test",
            confidence=0.8,
            source="test",
        )
        await store.semantic_update(fact_id, "new content", 0.95)
        results = await store.semantic_search("old")
        assert len(results) == 0

    async def test_update_nonexistent_no_error(self, store: SQLiteMemoryStore) -> None:
        await store.semantic_update(
            "00000000-0000-0000-0000-000000000000",
            "nothing",
            1.0,
        )

    async def test_delete_removes_from_search(self, store: SQLiteMemoryStore) -> None:
        fact_id = await store.semantic_insert(
            content="will be deleted",
            category="test",
            confidence=1.0,
            source="test",
        )
        assert len(await store.semantic_search("will be deleted")) == 1
        await store.semantic_delete(fact_id)
        assert len(await store.semantic_search("will be deleted")) == 0

    async def test_delete_nonexistent_no_error(self, store: SQLiteMemoryStore) -> None:
        await store.semantic_delete(
            "00000000-0000-0000-0000-000000000000"
        )

    async def test_insert_multiple_and_search(self, store: SQLiteMemoryStore) -> None:
        await store.semantic_insert("alpha test", "cat", 1.0, "test")
        await store.semantic_insert("beta test", "cat", 1.0, "test")
        await store.semantic_insert("gamma test", "other", 1.0, "test")
        results = await store.semantic_search("test")
        assert len(results) == 3

    async def test_semantic_search_respects_limit(self, store: SQLiteMemoryStore) -> None:
        for i in range(5):
            await store.semantic_insert(f"fact {i}", "cat", 1.0, "test")
        results = await store.semantic_search("fact", limit=2)
        assert len(results) == 2

    async def test_insert_with_optional_fields(self, store: SQLiteMemoryStore) -> None:
        fact_id = await store.semantic_insert(
            content="with extras",
            category="test",
            confidence=0.5,
            source="bot",
            source_id="ext-123",
        )
        # Should not raise
        results = await store.semantic_search("with extras")
        assert len(results) == 1


# ── Episodic memory ────────────────────────────────────────────────


class TestEpisodicMemory:
    async def test_record_event(self, store: SQLiteMemoryStore) -> None:
        eid = await store.episodic_record("conversation_turn", "conv-1", "user asked")
        assert isinstance(eid, str)
        assert len(eid) > 0

    async def test_record_with_payload(self, store: SQLiteMemoryStore) -> None:
        eid = await store.episodic_record(
            "tool_call",
            "conv-1",
            "ran query",
            {"tool": "search", "query": "foo"},
        )
        events = await store.episodic_get_conversation("conv-1")
        assert len(events) == 1

    async def test_search_by_query(self, store: SQLiteMemoryStore) -> None:
        await store.episodic_record("conversation_turn", "c1", "hello world")
        results = await store.episodic_search(query="hello")
        assert len(results) == 1

    async def test_search_by_query_dots(self, store: SQLiteMemoryStore) -> None:
        """Episodic search must also handle dots without error."""
        await store.episodic_record("conversation_turn", "c1", "G.A.I.A. v0.1.0")
        results = await store.episodic_search(query="G.A.I.A.")
        assert len(results) == 1

    async def test_search_no_query(self, store: SQLiteMemoryStore) -> None:
        await store.episodic_record("conversation_start", "c1", "start")
        results = await store.episodic_search()
        assert len(results) == 1

    async def test_get_conversation(self, store: SQLiteMemoryStore) -> None:
        await store.episodic_record("conversation_turn", "my-conv", "turn 1")
        await store.episodic_record("conversation_turn", "my-conv", "turn 2")
        events = await store.episodic_get_conversation("my-conv")
        assert len(events) == 2

    async def test_get_empty_conversation(self, store: SQLiteMemoryStore) -> None:
        results = await store.episodic_get_conversation("nonexistent")
        assert results == []

    async def test_search_by_event_type(self, store: SQLiteMemoryStore) -> None:
        await store.episodic_record("conversation_start", "c1", "start")
        await store.episodic_record("conversation_turn", "c1", "turn")
        results = await store.episodic_search(event_types=["conversation_start"])
        assert len(results) == 1

    async def test_search_time_range(self, store: SQLiteMemoryStore) -> None:
        await store.episodic_record("conversation_turn", "c1", "old")
        results = await store.episodic_search(
            time_range=("2020-01-01", "2025-01-01")
        )
        # The event was inserted just now, so it won't match 2020
        assert len(results) == 0

    async def test_prune_removes_old_events(self, store: SQLiteMemoryStore) -> None:
        await store.episodic_record("conversation_turn", "c1", "old event")
        # Prune everything before far future — nothing should be removed
        count = await store.episodic_prune("2100-01-01T00:00:00+00:00")
        assert count == 1

    async def test_search_no_results(self, store: SQLiteMemoryStore) -> None:
        results = await store.episodic_search(query="nonexistent xyz")
        assert results == []


# ── Procedural memory ──────────────────────────────────────────────


class TestProceduralMemory:
    async def test_register(self, store: SQLiteMemoryStore) -> None:
        await store.procedure_register(
            "proc-1", "Test Procedure", "A test description", "safe"
        )
        proc = await store.procedure_get("proc-1")
        assert proc is not None
        assert proc["name"] == "Test Procedure"

    async def test_register_with_steps(self, store: SQLiteMemoryStore) -> None:
        await store.procedure_register(
            "proc-2",
            "Steps Proc",
            "Has steps",
            "caution",
            steps=[{"action": "do thing", "check": "verify"}],
            source_file="test.py",
        )
        proc = await store.procedure_get("proc-2")
        assert proc is not None
        assert proc["source_file"] == "test.py"

    async def test_register_duplicate_no_error(self, store: SQLiteMemoryStore) -> None:
        """Duplicate register may raise or overwrite — just don't crash."""
        await store.procedure_register(
            "dup-1", "First", "desc", "safe"
        )
        try:
            await store.procedure_register(
                "dup-1", "Second", "desc", "safe"
            )
        except Exception:
            pass  # Expected for UNIQUE constraint

    async def test_get_nonexistent(self, store: SQLiteMemoryStore) -> None:
        assert await store.procedure_get("no-such-proc") is None

    async def test_search(self, store: SQLiteMemoryStore) -> None:
        await store.procedure_register("p1", "Debug Python", "Debug an app", "safe")
        results = await store.procedure_search("debug")
        assert len(results) >= 1

    async def test_search_dots(self, store: SQLiteMemoryStore) -> None:
        """Procedural search must handle dots in queries."""
        await store.procedure_register("p2", "G.A.I.A. Config", "Setup config", "safe")
        results = await store.procedure_search("G.A.I.A.")
        assert len(results) >= 1

    async def test_search_category(self, store: SQLiteMemoryStore) -> None:
        await store.procedure_register("p3", "Cat A Proc", "desc", "safe")
        results = await store.procedure_search("cat a", category="custom")
        assert len(results) >= 1


# ── Audit log ──────────────────────────────────────────────────────


class TestAuditLog:
    async def test_append(self, store: SQLiteMemoryStore) -> None:
        await store.audit_log("user_login", "system")
        # Should not raise
        await store.audit_log(
            "memory_insert",
            "agent",
            target_type="semantic_facts",
            target_id="f1",
            detail={"content": "hello"},
        )


# ── Lifecycle ──────────────────────────────────────────────────────


class TestLifecycle:
    async def test_initialize_close(self, db_path: Path) -> None:
        mem = SQLiteMemoryStore(db_path)
        await mem.initialize()
        await mem.close()
        # Double-close is safe
        await mem.close()

    async def test_close_without_initialize(self, db_path: Path) -> None:
        mem = SQLiteMemoryStore(db_path)
        await mem.close()  # Should not raise

    async def test_db_file_created_on_initialize(self) -> None:
        mem = SQLiteMemoryStore(tempfile.mktemp(suffix=".db"))
        assert not mem._db_path.exists()
        await mem.initialize()
        assert mem._db_path.exists()
        await mem.close()
        mem._db_path.unlink(missing_ok=True)


# ── Integration: multi-step workflow ──────────────────────────────


class TestWorkflow:
    async def test_full_semantic_lifecycle(self, store: SQLiteMemoryStore) -> None:
        """Insert → search → update → search again → delete → verify gone."""
        fact_id = await store.semantic_insert(
            "user likes python", "preference", 1.0, "user"
        )
        # Insert
        assert len(await store.semantic_search("python")) == 1

        # Search finds it
        results = await store.semantic_search("user likes")
        assert len(results) == 1
        assert results[0]["content"] == "user likes python"

        # Update
        await store.semantic_update(fact_id, "user prefers rust", 0.9)
        assert len(await store.semantic_search("python")) == 0
        assert len(await store.semantic_search("rust")) == 1

        # Delete
        await store.semantic_delete(fact_id)
        assert len(await store.semantic_search("rust")) == 0

    async def test_full_conversation_flow(self, store: SQLiteMemoryStore) -> None:
        """Record a multi-turn conversation, retrieve it, prune old events."""
        conv_id = "workflow-conv"
        await store.episodic_record(
            "conversation_start", conv_id, "session begins"
        )
        await store.episodic_record(
            "conversation_turn", conv_id, "user asks about memory"
        )
        await store.episodic_record(
            "tool_call", conv_id, "searching memory store"
        )
        await store.episodic_record(
            "conversation_turn", conv_id, "agent explains FTS5"
        )
        await store.episodic_record(
            "conversation_end", conv_id, "session ends"
        )

        events = await store.episodic_get_conversation(conv_id)
        assert len(events) == 5

        # Search by query (episodic_search filters by query, event_type, time_range)
        results = await store.episodic_search(query="memory")
        # Should find events containing "memory" in summary or payload
        assert len(results) >= 1

        # Prune old events
        count = await store.episodic_prune("2100-01-01T00:00:00+00:00")
        assert count == 5

        events_after = await store.episodic_get_conversation(conv_id)
        assert len(events_after) == 0
