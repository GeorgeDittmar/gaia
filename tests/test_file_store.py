"""Tests for the flat-file MemoryStore implementation."""

import asyncio
from pathlib import Path

import pytest

from gaia.core.memory.file_store import (
    FileMemoryStore,
    _build_frontmatter,
    _parse_frontmatter,
    _score_query,
    _tokenize,
)

# ── Frontmatter helpers ─────────────────────────────────────────────


class TestFrontmatterHelpers:
    """Unit tests for the YAML-like frontmatter parser."""

    def test_parse_simple_frontmatter(self) -> None:
        text = "---\nfact_id: abc123\nconfidence: 0.9\n---\nUser likes Python."
        meta, body = _parse_frontmatter(text)
        assert meta["fact_id"] == "abc123"
        assert meta["confidence"] == "0.9"
        assert body == "User likes Python."

    def test_parse_null_value(self) -> None:
        text = "---\nsource_id: null\n---\nbody text"
        meta, _ = _parse_frontmatter(text)
        assert meta["source_id"] is None

    def test_parse_bool_values(self) -> None:
        text = "---\nenabled: true\ndeleted: false\n---\nbody"
        meta, _ = _parse_frontmatter(text)
        assert meta["enabled"] is True
        assert meta["deleted"] is False

    def test_parse_no_frontmatter(self) -> None:
        text = "just plain text"
        meta, body = _parse_frontmatter(text)
        assert meta == {}
        assert body == "just plain text"

    def test_build_roundtrip(self) -> None:
        meta = {"key": "val", "num": 42, "flag": True, "nothing": None}
        block = _build_frontmatter(meta)
        parsed, _ = _parse_frontmatter(block)
        assert parsed["key"] == "val"
        assert parsed["num"] == "42"
        assert parsed["flag"] is True
        assert parsed["nothing"] is None

    def test_tokenize_drops_stopwords(self) -> None:
        tokens = _tokenize("the quick brown fox")
        assert "the" not in tokens
        assert "quick" in tokens
        assert "brown" in tokens
        assert "fox" in tokens

    def test_score_query_positive(self) -> None:
        score = _score_query("python programming", "User prefers Python for scripting")
        assert score > 0

    def test_score_query_no_match(self) -> None:
        score = _score_query("quantum computing", "User likes Python")
        assert score == 0


# ── Semantic memory ─────────────────────────────────────────────────


@pytest.fixture()
def store(tmp_path: Path) -> FileMemoryStore:
    """Yield an initialized FileMemoryStore in a temp directory."""
    s = FileMemoryStore(tmp_path / "memory")
    asyncio.run(s.initialize())
    return s


class TestSemanticMemory:
    """Tests for semantic (fact) insert, update, delete, search."""

    @pytest.mark.asyncio
    async def test_insert_and_retrieve(self, store: FileMemoryStore) -> None:
        fact_id = await store.semantic_insert(
            content="User prefers Python over Rust",
            category="preference",
            confidence=0.95,
            source="user",
        )
        assert isinstance(fact_id, str) and len(fact_id) > 0
        # Verify the file exists
        assert (store._memory_dir / "semantic" / f"{fact_id}.md").exists()

    @pytest.mark.asyncio
    async def test_insert_creates_file(self, store: FileMemoryStore) -> None:
        fact_id = await store.semantic_insert(
            "User lives in Portland.",
            "preference", 0.8, "auto_extract",
        )
        path = store._memory_dir / "semantic" / f"{fact_id}.md"
        text = path.read_text(encoding="utf-8")
        assert "User lives in Portland." in text
        assert "fact_id:" in text
        assert "preference" in text

    @pytest.mark.asyncio
    async def test_list_facts_via_search(self, store: FileMemoryStore) -> None:
        ids = []
        for i in range(3):
            fid = await store.semantic_insert(
                f"Fact number {i}", "general", 0.5, "test"
            )
            ids.append(fid)

        results = await store.semantic_search("fact", limit=10)
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_update_modifies_file(self, store: FileMemoryStore) -> None:
        fact_id = await store.semantic_insert(
            "Old content", "general", 0.5, "test"
        )
        await store.semantic_update(fact_id, "New content", 0.9)
        path = store._memory_dir / "semantic" / f"{fact_id}.md"
        text = path.read_text(encoding="utf-8")
        assert "New content" in text
        # Verify old content is gone
        assert "Old content" not in text

    @pytest.mark.asyncio
    async def test_delete_removes_file(self, store: FileMemoryStore) -> None:
        fact_id = await store.semantic_insert(
            "To be deleted", "general", 0.5, "test"
        )
        path = store._memory_dir / "semantic" / f"{fact_id}.md"
        assert path.exists()

        await store.semantic_delete(fact_id)
        assert not path.exists()

    @pytest.mark.asyncio
    async def test_delete_nonexistent_does_not_raise(self, store: FileMemoryStore) -> None:
        await store.semantic_delete("does-not-exist")  # should not raise

    @pytest.mark.asyncio
    async def test_search_keyword_match(self, store: FileMemoryStore) -> None:
        await store.semantic_insert(
            "George lives in Portland, Oregon",
            "preference", 1.0, "user",
        )
        results = await store.semantic_search("Portland", limit=10)
        assert len(results) == 1
        assert "George" in results[0]["content"]
        assert results[0]["category"] == "preference"

    @pytest.mark.asyncio
    async def test_search_empty_query(self, store: FileMemoryStore) -> None:
        """Empty query returns no results (stopwords-only)."""
        await store.semantic_insert(
            "A fact about the and a", "general", 0.5, "test"
        )
        results = await store.semantic_search("", limit=10)
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_search_returns_max_limit(self, store: FileMemoryStore) -> None:
        for i in range(5):
            await store.semantic_insert(
                f"Token word {i}", "general", 0.5, "test"
            )
        results = await store.semantic_search("word", limit=3)
        assert len(results) <= 3

    @pytest.mark.asyncio
    async def test_update_nonexistent_does_not_raise(self, store: FileMemoryStore) -> None:
        await store.semantic_update(
            "nonexistent", "content", 0.5
        )  # should not raise


# ── Episodic memory ─────────────────────────────────────────────────


class TestEpisodicMemory:
    """Tests for episodic (event) record, search, get, prune."""

    @pytest.fixture()
    def store(self, tmp_path: Path) -> FileMemoryStore:
        s = FileMemoryStore(tmp_path / "memory")
        asyncio.run(s.initialize())
        return s

    @pytest.mark.asyncio
    async def test_record_event(self, store: FileMemoryStore) -> None:
        event_id = await store.episodic_record(
            event_type="conversation_turn",
            conversation_id="conv-1",
            summary="User asked a question",
            payload={"user_prompt": "hello", "agent_response": "hi there"},
        )
        assert isinstance(event_id, str) and len(event_id) > 0
        assert (store._memory_dir / "episodic" / f"{event_id}.md").exists()

    @pytest.mark.asyncio
    async def test_record_and_retrieve_by_conversation(self, store: FileMemoryStore) -> None:
        for i in range(3):
            await store.episodic_record(
                "conversation_turn",
                "conv-1",
                f"Turn {i}",
                {"turn": i},
            )

        events = await store.episodic_get_conversation("conv-1")
        assert len(events) == 3
        assert events[0]["conversation_id"] == "conv-1"

    @pytest.mark.asyncio
    async def test_episodic_search_by_query(self, store: FileMemoryStore) -> None:
        await store.episodic_record(
            "conversation_turn",
            "conv-1",
            "User asked about Python",
            {"user_prompt": "python is fun"},
        )
        results = await store.episodic_search(query="python", limit=10)
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_episodic_search_by_event_type(self, store: FileMemoryStore) -> None:
        await store.episodic_record("conversation_turn", "c1", "msg1", {})
        await store.episodic_record("fact_extracted", "c1", "msg2", {})

        results = await store.episodic_search(
            event_types=["conversation_turn"], limit=10
        )
        assert len(results) == 1
        assert results[0]["event_type"] == "conversation_turn"

    @pytest.mark.asyncio
    async def test_episodic_prune(self, store: FileMemoryStore) -> None:
        old_time = "2020-01-01T00:00:00+00:00"
        new_time = "2030-01-01T00:00:00+00:00"

        await store.episodic_record("conversation_turn", "c1", "old", {})
        await store.episodic_record("conversation_turn", "c2", "new", {})

        # Manually fix timestamps for old event
        old_files = list((store._memory_dir / "episodic").glob("*.md"))
        if old_files:
            old_files[0].write_text(
                _build_frontmatter({"timestamp": old_time})
                + "old event\n"
            )

        count = await store.episodic_prune("2025-01-01T00:00:00+00:00")
        assert count >= 1

    @pytest.mark.asyncio
    async def test_episodic_get_empty_conversation(self, store: FileMemoryStore) -> None:
        results = await store.episodic_get_conversation("nonexistent")
        assert results == []


# ── Procedural memory ───────────────────────────────────────────────


class TestProceduralMemory:
    """Tests for procedural (procedure) register, get, search."""

    @pytest.fixture()
    def store(self, tmp_path: Path) -> FileMemoryStore:
        s = FileMemoryStore(tmp_path / "memory")
        asyncio.run(s.initialize())
        return s

    @pytest.mark.asyncio
    async def test_register_and_get(self, store: FileMemoryStore) -> None:
        await store.procedure_register(
            procedure_id="greet",
            name="Greeter",
            description="Greets the user warmly",
            safety_level="safe",
        )
        result = await store.procedure_get("greet")
        assert result is not None
        assert result["name"] == "Greeter"

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, store: FileMemoryStore) -> None:
        result = await store.procedure_get("no-such-procedure")
        assert result is None

    @pytest.mark.asyncio
    async def test_procedure_search(self, store: FileMemoryStore) -> None:
        await store.procedure_register(
            "greet", "Greeter", "Greets the user", "safe"
        )
        results = await store.procedure_search("greets", category=None)
        assert len(results) == 1
        assert results[0]["name"] == "Greeter"


# ── Audit log ───────────────────────────────────────────────────────


class TestAuditLog:
    """Tests for append-only audit logging."""

    @pytest.fixture()
    def store(self, tmp_path: Path) -> FileMemoryStore:
        s = FileMemoryStore(tmp_path / "memory")
        asyncio.run(s.initialize())
        return s

    @pytest.mark.asyncio
    async def test_audit_append(self, store: FileMemoryStore) -> None:
        await store.audit_log("semantic_insert", actor="agent", target_id="f1")
        files = list((store._memory_dir / "audit").glob("*.md"))
        assert len(files) == 1

    @pytest.mark.asyncio
    async def test_audit_multiple_entries_ordered(self, store: FileMemoryStore) -> None:
        for i in range(3):
            await store.audit_log(f"action_{i}", actor="agent")
        files = list((store._memory_dir / "audit").glob("*.md"))
        assert len(files) == 3


# ── Lifecycle ───────────────────────────────────────────────────────


class TestLifecycle:
    """Tests for initialize() and close()."""

    @pytest.mark.asyncio
    async def test_initialize_creates_subdirs(self, tmp_path: Path) -> None:
        store = FileMemoryStore(tmp_path / "memory")
        await store.initialize()
        for sub in ("semantic", "episodic", "procedures", "audit"):
            assert (tmp_path / "memory" / sub).is_dir()

    @pytest.mark.asyncio
    async def test_close_noop(self, tmp_path: Path) -> None:
        store = FileMemoryStore(tmp_path / "memory")
        await store.initialize()
        await store.close()  # should not raise

    @pytest.mark.asyncio
    async def test_concurrent_safety(self, tmp_path: Path) -> None:
        """Two inserts should not corrupt either file."""
        store = FileMemoryStore(tmp_path / "memory")
        await store.initialize()

        await asyncio.gather(
            store.semantic_insert("fact A", "general", 0.5, "test"),
            store.semantic_insert("fact B", "general", 0.5, "test"),
        )

        results = await store.semantic_search("fact", limit=10)
        assert len(results) == 2
