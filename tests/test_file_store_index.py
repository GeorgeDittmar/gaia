"""Tests for the file-based memory store index sidecar.

Verifies that ``_build_index``, ``_persist_index``, and the index-aware
versions of ``episodic_prune``, ``episodic_get_conversation``, and
``episodic_search`` behave correctly.
"""

import json
from pathlib import Path

import pytest

from gaia.core.memory.file_store import FileMemoryStore


# ── Helpers ──────────────────────────────────────────────────────────

_EPISODE_TEMPLATE = """---
event_id: {event_id}
event_type: {event_type}
conversation_id: {conversation_id}
timestamp: {timestamp}
payload: "{payload}"
created_at: {created_at}
---
{body}
"""


def _write_episode(dir_: Path, **kwargs) -> Path:
    """Write a single episodic .md file and return its path."""
    path = dir_ / f"{kwargs['event_id']}.md"
    path.write_text(
        _EPISODE_TEMPLATE.format(**kwargs),
        encoding="utf-8",
    )
    return path


# ── TestIndexBuilding ────────────────────────────────────────────────

class TestIndexBuilding:
    def test_build_index_populates_index(self, tmp_path: Path) -> None:
        """_build_index scans .md files and indexes them by subdir/event_id."""
        memory_dir = tmp_path / "memory"
        episodic_dir = memory_dir / "episodic"
        episodic_dir.mkdir(parents=True)

        _write_episode(
            episodic_dir,
            event_id="ev-1",
            event_type="user_message",
            conversation_id="conv-a",
            timestamp="2025-01-01T00:00:00+00:00",
            payload='{"text": "hello"}',
            created_at="2025-01-01T00:00:00+00:00",
            body="First message.",
        )
        _write_episode(
            episodic_dir,
            event_id="ev-2",
            event_type="agent_response",
            conversation_id="conv-a",
            timestamp="2025-01-01T01:00:00+00:00",
            payload='{"text": "hi back"}',
            created_at="2025-01-01T01:00:00+00:00",
            body="Second message.",
        )

        store = FileMemoryStore(memory_dir=memory_dir)
        store._build_index()

        assert "episodic/ev-1" in store._index
        assert "episodic/ev-2" in store._index
        assert len(store._index) == 2

        _, meta1 = store._index["episodic/ev-1"]
        assert meta1["event_type"] == "user_message"
        assert meta1["conversation_id"] == "conv-a"
        assert str(episodic_dir / "ev-1.md") in store._index["episodic/ev-1"][0]

        _, meta2 = store._index["episodic/ev-2"]
        assert meta2["event_type"] == "agent_response"
        assert str(episodic_dir / "ev-2.md") in store._index["episodic/ev-2"][0]

    def test_index_persists_and_reloads(self, tmp_path: Path) -> None:
        """_persist_index writes to disk and reloaded data matches."""
        memory_dir = tmp_path / "memory"
        episodic_dir = memory_dir / "episodic"
        episodic_dir.mkdir(parents=True)

        _write_episode(
            episodic_dir,
            event_id="persist-1",
            event_type="user_message",
            conversation_id="conv-b",
            timestamp="2025-02-01T00:00:00+00:00",
            payload="{}",
            created_at="2025-02-01T00:00:00+00:00",
            body="Persist test.",
        )

        store = FileMemoryStore(memory_dir=memory_dir)
        store._build_index()
        store._persist_index()

        index_path = memory_dir.parent / ".gaia_memory_index.json"
        assert index_path.exists()

        with open(index_path, "r", encoding="utf-8") as f:
            saved = json.load(f)

        assert "episodic/persist-1" in saved
        assert saved["episodic/persist-1"][0] == str(episodic_dir / "persist-1.md")
        assert saved["episodic/persist-1"][1]["event_type"] == "user_message"

        # Simulate a fresh store loading from disk
        store2 = FileMemoryStore(memory_dir=memory_dir)
        store2._index = {}
        with open(index_path, "r", encoding="utf-8") as f:
            store2._index = {
                k: (v[0], dict(v[1]))
                for k, v in json.load(f).items()
            }

        assert "episodic/persist-1" in store2._index
        assert store2._index["episodic/persist-1"][1]["event_type"] == "user_message"


# ── TestPruneWithIndex ──────────────────────────────────────────────

class TestPruneWithIndex:
    @pytest.mark.asyncio
    async def test_prune_uses_index_not_full_scan(
        self, tmp_path: Path
    ) -> None:
        """episodic_prune deletes only old entries and updates the index."""
        memory_dir = tmp_path / "memory"
        episodic_dir = memory_dir / "episodic"
        episodic_dir.mkdir(parents=True)

        _write_episode(
            episodic_dir,
            event_id="old-1",
            event_type="user_message",
            conversation_id="conv-a",
            timestamp="2024-01-01T00:00:00+00:00",
            payload="{}",
            created_at="2024-01-01T00:00:00+00:00",
            body="Old message.",
        )
        _write_episode(
            episodic_dir,
            event_id="new-1",
            event_type="agent_response",
            conversation_id="conv-a",
            timestamp="2025-06-01T00:00:00+00:00",
            payload="{}",
            created_at="2025-06-01T00:00:00+00:00",
            body="New message.",
        )

        store = FileMemoryStore(memory_dir=memory_dir)
        await store.initialize()

        # Prune everything before 2025
        deleted = await store.episodic_prune("2025-01-01T00:00:00+00:00")
        assert deleted == 1

        # Old file should be gone, new file should survive
        assert not (episodic_dir / "old-1.md").exists()
        assert (episodic_dir / "new-1.md").exists()

        # Index should only have the new entry
        assert "episodic/old-1" not in store._index
        assert "episodic/new-1" in store._index


# ── TestGetConversationWithIndex ────────────────────────────────────

class TestGetConversationWithIndex:
    @pytest.mark.asyncio
    async def test_get_conversation_filters_by_id(
        self, tmp_path: Path
    ) -> None:
        """episodic_get_conversation returns only events for the given ID."""
        memory_dir = tmp_path / "memory"
        episodic_dir = memory_dir / "episodic"
        episodic_dir.mkdir(parents=True)

        _write_episode(
            episodic_dir,
            event_id="ca-1",
            event_type="user_message",
            conversation_id="conv-alpha",
            timestamp="2025-03-01T00:00:00+00:00",
            payload='{"text": "hi alpha"}',
            created_at="2025-03-01T00:00:00+00:00",
            body="Alpha message 1.",
        )
        _write_episode(
            episodic_dir,
            event_id="ca-2",
            event_type="agent_response",
            conversation_id="conv-alpha",
            timestamp="2025-03-01T01:00:00+00:00",
            payload='{"text": "hi back alpha"}',
            created_at="2025-03-01T01:00:00+00:00",
            body="Alpha message 2.",
        )
        _write_episode(
            episodic_dir,
            event_id="cb-1",
            event_type="user_message",
            conversation_id="conv-beta",
            timestamp="2025-03-01T00:30:00+00:00",
            payload='{"text": "hi beta"}',
            created_at="2025-03-01T00:30:00+00:00",
            body="Beta message.",
        )

        store = FileMemoryStore(memory_dir=memory_dir)
        await store.initialize()

        result = await store.episodic_get_conversation("conv-alpha")
        assert len(result) == 2
        assert all(r["conversation_id"] == "conv-alpha" for r in result)
        assert result[0]["event_id"] == "ca-1"
        assert result[1]["event_id"] == "ca-2"

        result_beta = await store.episodic_get_conversation("conv-beta")
        assert len(result_beta) == 1
        assert result_beta[0]["event_id"] == "cb-1"


# ── TestSearchWithIndex ─────────────────────────────────────────────

class TestSearchWithIndex:
    @pytest.mark.asyncio
    async def test_no_query_filtered_scan_uses_index(
        self, tmp_path: Path
    ) -> None:
        """episodic_search with filters uses index instead of full scan."""
        memory_dir = tmp_path / "memory"
        episodic_dir = memory_dir / "episodic"
        episodic_dir.mkdir(parents=True)

        _write_episode(
            episodic_dir,
            event_id="ts-1",
            event_type="user_message",
            conversation_id="conv-ts",
            timestamp="2025-04-01T00:00:00+00:00",
            payload="{}",
            created_at="2025-04-01T00:00:00+00:00",
            body="April message.",
        )
        _write_episode(
            episodic_dir,
            event_id="ts-2",
            event_type="agent_response",
            conversation_id="conv-ts",
            timestamp="2025-04-01T01:00:00+00:00",
            payload="{}",
            created_at="2025-04-01T01:00:00+00:00",
            body="April response.",
        )
        _write_episode(
            episodic_dir,
            event_id="ts-3",
            event_type="user_message",
            conversation_id="conv-ts",
            timestamp="2025-06-15T00:00:00+00:00",
            payload="{}",
            created_at="2025-06-15T00:00:00+00:00",
            body="June message.",
        )

        store = FileMemoryStore(memory_dir=memory_dir)
        await store.initialize()

        # Filter by event_type only — should get all user_message events
        result = await store.episodic_search(
            event_types=["user_message"],
            limit=10,
        )
        assert len(result) == 2
        assert all(r["event_type"] == "user_message" for r in result)

        # Filter by time_range
        result = await store.episodic_search(
            time_range=("2025-05-01T00:00:00+00:00", "2025-12-31T23:59:59+00:00"),
            limit=10,
        )
        assert len(result) == 1
        assert result[0]["event_id"] == "ts-3"

        # Combined filters — no match
        result = await store.episodic_search(
            event_types=["agent_response"],
            time_range=("2025-01-01T00:00:00+00:00", "2025-03-31T23:59:59+00:00"),
            limit=10,
        )
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_search_read_bodies_only_for_top_results(
        self, tmp_path: Path
    ) -> None:
        """Only bodies for the top N results are read into summary."""
        memory_dir = tmp_path / "memory"
        episodic_dir = memory_dir / "episodic"
        episodic_dir.mkdir(parents=True)

        # Seed 5 events — same type, all in range
        for i in range(5):
            _write_episode(
                episodic_dir,
                event_id=f"body-{i}",
                event_type="user_message",
                conversation_id="conv-body",
                timestamp=f"2025-04-{i+1:02d}T00:00:00+00:00",
                payload="{}",
                created_at=f"2025-04-{i+1:02d}T00:00:00+00:00",
                body=f"Body content for event {i}.",
            )

        store = FileMemoryStore(memory_dir=memory_dir)
        await store.initialize()

        result = await store.episodic_search(
            time_range=("2025-04-01T00:00:00+00:00", "2025-04-30T23:59:59+00:00"),
            limit=3,
        )
        assert len(result) == 3
        assert all(r["summary"] for r in result)  # Bodies read for top 3
