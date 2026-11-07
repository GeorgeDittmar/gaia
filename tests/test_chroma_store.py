"""Tests for the ChromaDB-backed MemoryStore (HTTP client).

The adapter talks to an external ChromaDB server via HttpClient.  These
tests mock the ChromaDB client so they run fast and without a running
server.

Each test verifies the adapter's logic — parameter passing, metadata
handling, query formatting, lifecycle — rather than ChromaDB's vector
index.
"""

from unittest.mock import MagicMock, patch

import pytest

from gaia.core.memory.chroma_store import ChromaMemoryStore

# ── Helpers ─────────────────────────────────────────────────────────


def _make_mock_collection() -> MagicMock:
    """Return a ChromaDB collection mock with realistic return values."""
    coll = MagicMock()
    coll.query.return_value = {
        "ids": [["mock-id"]],
        "documents": [["mock document"]],
        "metadatas": [[{
            "fact_id": "mock-id",
            "category": "general",
            "name": "mock document",
            "procedure_id": "mock-id",
        }]],
    }
    coll.get.return_value = {
        "ids": ["mock-id"],
        "documents": ["mock document"],
        "metadatas": [{
            "fact_id": "mock-id",
            "category": "general",
            "source": "test",
            "source_id": None,
            "created_at": "2024-01-01T00:00:00+00:00",
            "updated_at": "2024-01-01T00:00:00+00:00",
            "metadata": None,
        }],
    }
    coll.upsert.return_value = None
    return coll


def _make_mock_client() -> tuple[MagicMock, dict[str, MagicMock]]:
    """Return a ChromaDB HttpClient mock and its collections dict."""
    collections = {name: _make_mock_collection() for name in (
        "gaia_semantic", "gaia_episodic", "gaia_procedure", "gaia_audit"
    )}
    client_mock = MagicMock()
    client_mock.get_or_create_collection.side_effect = lambda n: collections.get(
        n, _make_mock_collection()
    )
    return client_mock, collections


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def chroma_mock() -> tuple[MagicMock, dict[str, MagicMock]]:
    """Patch chromadb.HttpClient and yield the mock client + collections."""
    client_mock, collections = _make_mock_client()
    with patch("gaia.core.memory.chroma_store.HttpClient", return_value=client_mock):
        with patch("gaia.core.memory.chroma_store.chromadb", MagicMock()):
            yield client_mock, collections


@pytest.fixture
def store(chroma_mock) -> ChromaMemoryStore:
    """Create a store initialized with the mocked client."""
    client_mock, collections = chroma_mock
    s = ChromaMemoryStore(endpoint="http://mock-chroma:8000")
    s._client = client_mock
    s._collections = collections
    return s


# ── Semantic memory ────────────────────────────────────────────────


class TestSemanticMemory:
    async def test_insert_calls_collection_add(
        self, store: ChromaMemoryStore, chroma_mock,
    ) -> None:
        _, collections = chroma_mock
        fact_id = await store.semantic_insert(
            content="test fact", category="test", confidence=0.9, source="test",
        )
        semantic_coll = collections["gaia_semantic"]
        assert semantic_coll.add.call_count == 1
        call_kwargs = semantic_coll.add.call_args.kwargs
        assert call_kwargs["documents"] == ["test fact"]
        assert len(call_kwargs["metadatas"][0]) > 0

    async def test_search_returns_formatted_result(
        self, store: ChromaMemoryStore, chroma_mock,
    ) -> None:
        await store.semantic_search("test query")
        results = await store.semantic_search("test query")
        assert len(results) == 1
        assert results[0]["fact_id"] == "mock-id"
        assert results[0]["content"] == "mock document"
        assert results[0]["category"] == "general"

    async def test_search_empty_result(
        self, store: ChromaMemoryStore, chroma_mock,
    ) -> None:
        collections = chroma_mock[1]
        collections["gaia_semantic"].query.return_value = {
            "ids": [[], [], []], "documents": [[], []], "metadatas": [[], []],
        }
        results = await store.semantic_search("nothing")
        assert results == []

    async def test_update_deletes_and_readds(
        self, store: ChromaMemoryStore, chroma_mock,
    ) -> None:
        collections = chroma_mock[1]
        await store.semantic_update("mock-id", "new content", 0.95)
        semantic_coll = collections["gaia_semantic"]
        assert semantic_coll.delete.call_count >= 1
        assert semantic_coll.add.call_count >= 1

    async def test_delete_calls_delete_by_id(
        self, store: ChromaMemoryStore, chroma_mock,
    ) -> None:
        collections = chroma_mock[1]
        await store.semantic_delete("fact-123")
        collections["gaia_semantic"].delete.assert_called_with(ids=["fact-123"])

    async def test_search_respects_limit(
        self, store: ChromaMemoryStore, chroma_mock,
    ) -> None:
        await store.semantic_search("query", limit=5)
        collections = chroma_mock[1]
        call_kwargs = collections["gaia_semantic"].query.call_args.kwargs
        assert call_kwargs["n_results"] == 5

    async def test_insert_with_optional_fields(
        self, store: ChromaMemoryStore, chroma_mock,
    ) -> None:
        fact_id = await store.semantic_insert(
            content="extras", category="test", confidence=0.5,
            source="bot", source_id="ext-123",
        )
        assert isinstance(fact_id, str)


# ── Episodic memory ────────────────────────────────────────────────


class TestEpisodicMemory:
    async def test_record_event(self, store: ChromaMemoryStore, chroma_mock) -> None:
        eid = await store.episodic_record("conversation_turn", "conv-1", "user asked")
        collections = chroma_mock[1]
        episodic_coll = collections["gaia_episodic"]
        assert episodic_coll.add.call_count == 1

    async def test_get_conversation(self, store: ChromaMemoryStore, chroma_mock) -> None:
        collections = chroma_mock[1]
        collections["gaia_episodic"].get.return_value = {
            "ids": ["e1", "e2"],
            "documents": ["turn 1", "turn 2"],
            "metadatas": [
                {"event_id": "e1", "event_type": "conversation_turn", "conversation_id": "c1",
                 "timestamp": "2024-01-01T00:00:01+00:00", "payload": "{}", "created_at": "2024-01-01T00:00:01+00:00"},
                {"event_id": "e2", "event_type": "conversation_turn", "conversation_id": "c1",
                 "timestamp": "2024-01-01T00:00:02+00:00", "payload": "{}", "created_at": "2024-01-01T00:00:02+00:00"},
            ],
        }
        events = await store.episodic_get_conversation("c1")
        assert len(events) == 2
        assert events[0]["summary"] == "turn 1"
        assert events[1]["summary"] == "turn 2"

    async def test_empty_conversation(self, store: ChromaMemoryStore, chroma_mock) -> None:
        collections = chroma_mock[1]
        collections["gaia_episodic"].get.return_value = {"ids": [], "documents": [], "metadatas": []}
        events = await store.episodic_get_conversation("nope")
        assert events == []

    async def test_search_with_query(self, store: ChromaMemoryStore, chroma_mock) -> None:
        await store.episodic_search(query="hello")
        collections = chroma_mock[1]
        assert collections["gaia_episodic"].query.call_count == 1

    async def test_prune_removes_old(self, store: ChromaMemoryStore, chroma_mock) -> None:
        collections = chroma_mock[1]
        collections["gaia_episodic"].get.return_value = {
            "ids": ["e1", "e2"],
            "documents": ["old", "new"],
            "metadatas": [
                {"event_id": "e1", "timestamp": "2020-01-01T00:00:00+00:00", "payload": "{}"},
                {"event_id": "e2", "timestamp": "2100-01-01T00:00:00+00:00", "payload": "{}"},
            ],
        }
        count = await store.episodic_prune("2025-01-01T00:00:00+00:00")
        assert count == 1  # only "e1" is before 2025
        collections["gaia_episodic"].delete.assert_called_with(ids=["e1"])


# ── Procedural memory ──────────────────────────────────────────────


class TestProceduralMemory:
    async def test_register(self, store: ChromaMemoryStore, chroma_mock) -> None:
        await store.procedure_register(
            "p1", "Test Proc", "A description", "safe",
        )
        collections = chroma_mock[1]
        assert collections["gaia_procedure"].upsert.call_count == 1

    async def test_get_returns_metadata(self, store: ChromaMemoryStore, chroma_mock) -> None:
        collections = chroma_mock[1]
        collections["gaia_procedure"].get.return_value = {
            "ids": ["p1"],
            "metadatas": [{"name": "Test Proc", "description": "A description"}],
        }
        proc = await store.procedure_get("p1")
        assert proc is not None
        assert proc["name"] == "Test Proc"

    async def test_get_nonexistent(self, store: ChromaMemoryStore, chroma_mock) -> None:
        collections = chroma_mock[1]
        collections["gaia_procedure"].get.return_value = {"ids": [], "metadatas": []}
        assert await store.procedure_get("nope") is None

    async def test_search(self, store: ChromaMemoryStore, chroma_mock) -> None:
        results = await store.procedure_search("debug")
        assert len(results) == 1
        assert results[0]["name"] == "mock document"

    async def test_register_with_steps(self, store: ChromaMemoryStore, chroma_mock) -> None:
        steps = [{"action": "do thing", "check": "verify"}]
        await store.procedure_register(
            "p2", "Steps", "desc", "safe",
            steps=steps, source_file="test.py",
        )
        collections = chroma_mock[1]
        call_kwargs = collections["gaia_procedure"].upsert.call_args.kwargs
        meta = call_kwargs["metadatas"][0]
        assert meta["steps"] == '[{"action": "do thing", "check": "verify"}]'
        assert meta["source_file"] == "test.py"


# ── Audit log ──────────────────────────────────────────────────────


class TestAuditLog:
    async def test_append(self, store: ChromaMemoryStore, chroma_mock) -> None:
        await store.audit_log("user_login", "system")
        collections = chroma_mock[1]
        assert collections["gaia_audit"].add.call_count == 1
        call_kwargs = collections["gaia_audit"].add.call_args.kwargs
        assert call_kwargs["documents"] == ["user_login"]

    async def test_append_with_detail(self, store: ChromaMemoryStore, chroma_mock) -> None:
        await store.audit_log(
            "memory_insert", "agent",
            target_type="semantic_facts", target_id="f1",
            detail={"content": "hello"},
        )
        collections = chroma_mock[1]
        meta = collections["gaia_audit"].add.call_args.kwargs["metadatas"][0]
        assert meta["detail"] == '{"content": "hello"}'
        assert meta["action"] == "memory_insert"
        assert meta["actor"] == "agent"


# ── Lifecycle ──────────────────────────────────────────────────────


class TestLifecycle:
    async def test_initialize_creates_collections(
        self, store: ChromaMemoryStore, chroma_mock,
    ) -> None:
        client_mock, collections = chroma_mock
        await store.initialize()
        assert client_mock.get_or_create_collection.call_count == 4
        calls = [c[0][0] for c in client_mock.get_or_create_collection.call_args_list]
        assert "gaia_semantic" in calls
        assert "gaia_episodic" in calls
        assert "gaia_procedure" in calls
        assert "gaia_audit" in calls

    async def test_close_clears_state(self, store: ChromaMemoryStore) -> None:
        await store.initialize()
        assert store._collections != {}
        await store.close()
        assert store._collections == {}

    async def test_close_without_initialize(self, store: ChromaMemoryStore) -> None:
        await store.close()  # Should not raise


# ── Endpoint resolution ────────────────────────────────────────────


class TestEndpointResolution:
    async def test_custom_endpoint(self) -> None:
        store = ChromaMemoryStore(endpoint="http://my-chroma:9999")
        assert store._endpoint == "http://my-chroma:9999"

    @patch.dict("os.environ", {"CHROMA_HOST": "custom-host", "CHROMA_PORT": "7777"})
    async def test_env_override(self) -> None:
        store = ChromaMemoryStore()
        assert store._endpoint == "http://custom-host:7777"

    @patch.dict("os.environ", {"CHROMA_HOST": "host.containers.internal"})
    async def test_default_host(self) -> None:
        store = ChromaMemoryStore()
        assert store._endpoint == "http://host.containers.internal:8000"


# ── Generic delete_memory ──────────────────────────────────────────


class TestDeleteMemory:
    """Tests for the generic delete_memory method on ChromaMemoryStore."""

    async def test_delete_semantic(self, store, chroma_mock) -> None:
        collections = chroma_mock[1]
        await store.semantic_insert(
            "will be deleted via generic method",
            "test",
            1.0,
            "test",
        )
        await store.delete_memory("semantic", "test-fact-id")
        collections["gaia_semantic"].delete.assert_called()

    async def test_delete_unknown_type_ignores(self, store, chroma_mock) -> None:
        """Unknown target_type should not raise."""
        await store.delete_memory("nonexistent", "any-id")  # should not raise

    async def test_delete_nonexistent_semantic(self, store, chroma_mock) -> None:
        """Deleting a non-existent semantic entry should not raise."""
        await store.delete_memory(
            "semantic", "00000000-0000-0000-0000-000000000000"
        )
