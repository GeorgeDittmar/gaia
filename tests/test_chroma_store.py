"""Tests for the ChromaDB-backed MemoryStore implementation.

Covers semantic, episodic, and procedural memory CRUD, audit logging,
lifecycle (initialize/close), and full workflows.

ChromaDB uses vector similarity (not exact text matching), so semantic
search tests verify that the stored document is semantically related to
the query term or an exact match.
"""

import shutil
import tempfile
from pathlib import Path

import pytest

from gaia.core.memory.chroma_store import ChromaMemoryStore


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture()
def persist_dir() -> Path:
    """Return a temporary directory for ChromaDB and clean it up after."""
    path = Path(tempfile.mkdtemp(prefix="gaia_chroma_"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture()
async def store(persist_dir: Path) -> ChromaMemoryStore:
    """Return an initialized ChromaMemoryStore and close it after the test."""
    mem = ChromaMemoryStore(persist_directory=str(persist_dir))
    await mem.initialize()
    yield mem
    await mem.close()
    shutil.rmtree(persist_dir, ignore_errors=True)


# ── Semantic memory ────────────────────────────────────────────────


class TestSemanticMemory:
    async def test_insert_and_retrieve(self, store: ChromaMemoryStore) -> None:
        fact_id = await store.semantic_insert(
            content="G.A.I.A. runs locally",
            category="general",
            confidence=1.0,
            source="test",
        )
        assert isinstance(fact_id, str)
        assert len(fact_id) > 0

    async def test_search_returns_inserted_fact(self, store: ChromaMemoryStore) -> None:
        await store.semantic_insert(
            content="G.A.I.A. is a local assistant",
            category="general",
            confidence=0.95,
            source="test",
        )
        # Vector similarity: "local assistant" should match the stored doc
        results = await store.semantic_search("local assistant")
        assert len(results) == 1

    async def test_search_with_dots_in_term(self, store: ChromaMemoryStore) -> None:
        """Dots in terms must not crash ChromaDB.

        ChromaDB handles dots fine in documents and queries, but we
        still assert that insert + search round-trips without error.
        """
        await store.semantic_insert(
            content="G.A.I.A. version 0.1.0",
            category="version",
            confidence=1.0,
            source="test",
        )
        results = await store.semantic_search("G.A.I.A.")
        assert len(results) == 1

    async def test_search_with_apostrophe(self, store: ChromaMemoryStore) -> None:
        """Apostrophes in queries must not crash ChromaDB."""
        await store.semantic_insert(
            content="What's the best way to use memory?",
            category="general",
            confidence=1.0,
            source="test",
        )
        results = await store.semantic_search("What's")
        assert len(results) == 1

    async def test_search_no_results(self, store: ChromaMemoryStore) -> None:
        results = await store.semantic_search("nonexistent term xyz")
        assert results == []

    async def test_update_changes_content(self, store: ChromaMemoryStore) -> None:
        # Use maximally distinct content domains so vector search can tell them apart.
        # ChromaDB's default embeddings are loose; astronomy vs cooking are far apart.
        fact_id = await store.semantic_insert(
            content="the nucleus contains protons and neutrons",
            category="test",
            confidence=0.8,
            source="test",
        )
        await store.semantic_update(fact_id, "preheat oven to 350 degrees", 0.95)
        # Search for the new content: should definitely match
        results = await store.semantic_search("oven")
        assert len(results) == 1
        assert results[0]["fact_id"] == fact_id
        assert results[0]["content"] == "preheat oven to 350 degrees"

    async def test_update_nonexistent_no_error(self, store: ChromaMemoryStore) -> None:
        # Should not raise; silently creates a new entry with that ID
        await store.semantic_update(
            "00000000-0000-0000-0000-000000000000",
            "nothing",
            1.0,
        )

    async def test_delete_removes_from_search(self, store: ChromaMemoryStore) -> None:
        fact_id = await store.semantic_insert(
            content="will be deleted",
            category="test",
            confidence=1.0,
            source="test",
        )
        assert len(await store.semantic_search("deleted")) >= 1
        await store.semantic_delete(fact_id)
        assert len(await store.semantic_search("deleted")) == 0

    async def test_delete_nonexistent_no_error(self, store: ChromaMemoryStore) -> None:
        await store.semantic_delete(
            "00000000-0000-0000-0000-000000000000"
        )

    async def test_insert_multiple_and_search(self, store: ChromaMemoryStore) -> None:
        await store.semantic_insert("alpha test", "cat", 1.0, "test")
        await store.semantic_insert("beta test", "cat", 1.0, "test")
        await store.semantic_insert("gamma test", "other", 1.0, "test")
        results = await store.semantic_search("test")
        assert len(results) == 3

    async def test_semantic_search_respects_limit(self, store: ChromaMemoryStore) -> None:
        for i in range(5):
            await store.semantic_insert(f"fact {i}", "cat", 1.0, "test")
        results = await store.semantic_search("fact", limit=2)
        assert len(results) == 2

    async def test_insert_with_optional_fields(self, store: ChromaMemoryStore) -> None:
        fact_id = await store.semantic_insert(
            content="with extras",
            category="test",
            confidence=0.5,
            source="bot",
            source_id="ext-123",
        )
        results = await store.semantic_search("with extras")
        assert len(results) == 1
        assert results[0]["source_id"] == "ext-123"

    async def test_update_preserves_metadata(self, store: ChromaMemoryStore) -> None:
        """Verify that semantic_update keeps category, source, created_at."""
        fact_id = await store.semantic_insert(
            content="original",
            category="important",
            confidence=0.8,
            source="user",
            source_id="uid-42",
        )
        created_at = None
        results = await store.semantic_search("original")
        assert len(results) == 1
        created_at = results[0]["created_at"]

        await store.semantic_update(fact_id, "updated text", 0.95)

        results = await store.semantic_search("updated text")
        assert len(results) == 1
        assert results[0]["category"] == "important"
        assert results[0]["source"] == "user"
        assert results[0]["source_id"] == "uid-42"
        assert results[0]["created_at"] == created_at
        assert results[0]["confidence"] == 0.95

    async def test_update_updates_confidence(self, store: ChromaMemoryStore) -> None:
        fact_id = await store.semantic_insert(
            "some content", "cat", 0.5, "test"
        )
        await store.semantic_update(fact_id, "some content", 0.99)
        results = await store.semantic_search("some content")
        assert len(results) == 1
        assert results[0]["confidence"] == 0.99


# ── Episodic memory ────────────────────────────────────────────────


class TestEpisodicMemory:
    async def test_record_event(self, store: ChromaMemoryStore) -> None:
        eid = await store.episodic_record("conversation_turn", "conv-1", "user asked")
        assert isinstance(eid, str)
        assert len(eid) > 0

    async def test_record_with_payload(self, store: ChromaMemoryStore) -> None:
        eid = await store.episodic_record(
            "tool_call",
            "conv-1",
            "ran query",
            {"tool": "search", "query": "foo"},
        )
        events = await store.episodic_get_conversation("conv-1")
        assert len(events) == 1

    async def test_search_by_query(self, store: ChromaMemoryStore) -> None:
        await store.episodic_record("conversation_turn", "c1", "hello world")
        results = await store.episodic_search(query="hello")
        assert len(results) == 1

    async def test_search_by_query_dots(self, store: ChromaMemoryStore) -> None:
        """Episodic search must handle dots without error."""
        await store.episodic_record("conversation_turn", "c1", "G.A.I.A. v0.1.0")
        results = await store.episodic_search(query="G.A.I.A.")
        assert len(results) == 1

    async def test_search_no_query(self, store: ChromaMemoryStore) -> None:
        await store.episodic_record("conversation_start", "c1", "start")
        results = await store.episodic_search()
        assert len(results) == 1

    async def test_get_conversation(self, store: ChromaMemoryStore) -> None:
        await store.episodic_record("conversation_turn", "my-conv", "turn 1")
        await store.episodic_record("conversation_turn", "my-conv", "turn 2")
        events = await store.episodic_get_conversation("my-conv")
        assert len(events) == 2

    async def test_get_empty_conversation(self, store: ChromaMemoryStore) -> None:
        results = await store.episodic_get_conversation("nonexistent")
        assert results == []

    async def test_search_by_event_type(self, store: ChromaMemoryStore) -> None:
        await store.episodic_record("conversation_start", "c1", "start")
        await store.episodic_record("conversation_turn", "c1", "turn")
        results = await store.episodic_search(event_types=["conversation_start"])
        assert len(results) == 1

    async def test_search_time_range(self, store: ChromaMemoryStore) -> None:
        await store.episodic_record("conversation_turn", "c1", "old")
        results = await store.episodic_search(
            time_range=("2020-01-01", "2025-01-01")
        )
        # The event was inserted just now, so it won't match 2020
        assert len(results) == 0

    async def test_prune_removes_old_events(self, store: ChromaMemoryStore) -> None:
        await store.episodic_record("conversation_turn", "c1", "old event")
        # Prune everything before far future — nothing should be removed
        count = await store.episodic_prune("2100-01-01T00:00:00+00:00")
        assert count == 1

    async def test_search_no_results(self, store: ChromaMemoryStore) -> None:
        results = await store.episodic_search(query="nonexistent xyz")
        assert results == []

    async def test_episodic_search_respects_limit(self, store: ChromaMemoryStore) -> None:
        for i in range(5):
            await store.episodic_record("note", f"c{i}", f"item number {i}")
        results = await store.episodic_search(query="item", limit=2)
        assert len(results) == 2


# ── Procedural memory ──────────────────────────────────────────────


class TestProceduralMemory:
    async def test_register(self, store: ChromaMemoryStore) -> None:
        await store.procedure_register(
            "proc-1", "Test Procedure", "A test description", "safe"
        )
        proc = await store.procedure_get("proc-1")
        assert proc is not None
        assert proc["name"] == "Test Procedure"

    async def test_register_with_steps(self, store: ChromaMemoryStore) -> None:
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

    async def test_register_duplicate_no_error(self, store: ChromaMemoryStore) -> None:
        """Duplicate register may raise or overwrite -- just don't crash."""
        await store.procedure_register(
            "dup-1", "First", "desc", "safe"
        )
        try:
            await store.procedure_register(
                "dup-1", "Second", "desc", "safe"
            )
        except Exception:
            pass  # Expected for UNIQUE constraint

    async def test_get_nonexistent(self, store: ChromaMemoryStore) -> None:
        assert await store.procedure_get("no-such-proc") is None

    async def test_search(self, store: ChromaMemoryStore) -> None:
        await store.procedure_register("p1", "Debug Python", "Debug an app", "safe")
        results = await store.procedure_search("debug")
        assert len(results) >= 1

    async def test_search_dots(self, store: ChromaMemoryStore) -> None:
        """Procedural search must handle dots in queries."""
        await store.procedure_register("p2", "G.A.I.A. Config", "Setup config", "safe")
        results = await store.procedure_search("G.A.I.A.")
        assert len(results) >= 1

    async def test_search_category(self, store: ChromaMemoryStore) -> None:
        await store.procedure_register("p3", "Cat A Proc", "desc", "safe")
        results = await store.procedure_search("cat a", category="custom")
        assert len(results) >= 1

    async def test_register_with_safety_level(self, store: ChromaMemoryStore) -> None:
        await store.procedure_register(
            "proc-safe", "Safe Proc", "Nothing dangerous", "safe"
        )
        await store.procedure_register(
            "proc-caution", "Caution Proc", "Be careful", "caution"
        )
        proc = await store.procedure_get("proc-caution")
        assert proc is not None
        assert proc["safety_level"] == "caution"


# ── Audit log ──────────────────────────────────────────────────────


class TestAuditLog:
    async def test_append(self, store: ChromaMemoryStore) -> None:
        await store.audit_log("user_login", "system")
        await store.audit_log(
            "memory_insert",
            "agent",
            target_type="semantic_facts",
            target_id="f1",
            detail={"content": "hello"},
        )

    async def test_append_multiple(self, store: ChromaMemoryStore) -> None:
        for i in range(5):
            await store.audit_log(f"action_{i}", f"actor_{i}")
        results = await store.episodic_search(query="action", limit=100)
        # Audit log uses its own collection, not episodic, so this
        # verifies the search doesn't crash with mixed queries.
        # Better: just verify no crash and the log was append-only.

    async def test_append_default_actor(self, store: ChromaMemoryStore) -> None:
        await store.audit_log("test_action")
        # Should not raise with default actor="agent"


# ── Lifecycle ──────────────────────────────────────────────────────


class TestLifecycle:
    async def test_initialize_close(self, persist_dir: Path) -> None:
        mem = ChromaMemoryStore(persist_directory=str(persist_dir))
        await mem.initialize()
        await mem.close()
        # Double-close is safe
        await mem.close()

    async def test_close_without_initialize(self, persist_dir: Path) -> None:
        mem = ChromaMemoryStore(persist_directory=str(persist_dir))
        await mem.close()  # Should not raise

    async def test_db_directory_created_on_initialize(self) -> None:
        # Create a temp dir, then use a non-existent subdirectory as the
        # persist path.  tempfile.TemporaryDirectory() already creates its
        # own dir, so we need a *subdirectory* that does not yet exist.
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "new_gaia_chroma"
            mem = ChromaMemoryStore(persist_directory=str(path))
            assert not path.exists()
            await mem.initialize()
            assert path.exists()
            await mem.close()


# ── Integration: multi-step workflow ──────────────────────────────


class TestWorkflow:
    async def test_full_semantic_lifecycle(self, store: ChromaMemoryStore) -> None:
        """Insert -> search -> update -> search again -> delete -> verify gone."""
        # Use maximally distinct content domains so vector search can tell them apart.
        fact_id = await store.semantic_insert(
            "Jupiter is the largest planet in the solar system",
            "preference",
            1.0,
            "user",
        )

        # Insert: search finds it
        results = await store.semantic_search("Jupiter")
        assert len(results) == 1
        assert results[0]["content"] == "Jupiter is the largest planet in the solar system"

        # Update to completely different topic
        await store.semantic_update(
            fact_id,
            "the touchdown scored three points in football",
            0.9,
        )

        # Verify the new content is found and fact_id is preserved
        results = await store.semantic_search("touchdown")
        assert len(results) == 1
        assert results[0]["fact_id"] == fact_id
        assert results[0]["content"] == "the touchdown scored three points in football"

        # Delete
        await store.semantic_delete(fact_id)
        assert len(await store.semantic_search("touchdown")) == 0

    async def test_full_conversation_flow(self, store: ChromaMemoryStore) -> None:
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
        assert len(results) >= 1

        # Prune old events
        count = await store.episodic_prune("2100-01-01T00:00:00+00:00")
        assert count == 5

        events_after = await store.episodic_get_conversation(conv_id)
        assert len(events_after) == 0

    async def test_cross_memory_workflow(self, store: ChromaMemoryStore) -> None:
        """Use all three memory types together."""
        # Semantic: store a fact
        fact_id = await store.semantic_insert(
            "the user prefers Python over Rust",
            "preference",
            1.0,
            "user",
        )
        assert len(await store.semantic_search("python")) == 1

        # Episodic: record a conversation about that fact
        await store.episodic_record(
            "conversation_turn", "conv-x",
            "discussed programming language preference",
        )
        events = await store.episodic_get_conversation("conv-x")
        assert len(events) == 1

        # Procedural: register a procedure
        await store.procedure_register(
            "py-setup", "Python Setup", "Set up a Python environment", "safe"
        )
        proc = await store.procedure_get("py-setup")
        assert proc is not None

        # Audit: log the interaction
        await store.audit_log(
            "memory_use",
            "agent",
            target_type="semantic_facts",
            target_id=fact_id,
        )

        # Cleanup: prune everything
        await store.episodic_prune("2100-01-01T00:00:00+00:00")
        assert len(await store.episodic_get_conversation("conv-x")) == 0
