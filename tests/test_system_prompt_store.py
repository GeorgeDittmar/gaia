"""Tests for SystemPromptStore — save/load, backup rotation, rollback."""

import time
from pathlib import Path

import pytest

from gaia.system_prompt import MAX_BACKUPS, SystemPromptStore


# ── helpers ──────────────────────────────────────────────────────────


def _make_store(tmp_path: Path) -> tuple[SystemPromptStore, Path]:
    """Create a store backed by *tmp_path* and return ``(store, agnet_md)``."""
    prompt_path = tmp_path / "AGENT.md"
    store = SystemPromptStore(prompt_path=prompt_path)
    return store, prompt_path


def _save(store: SystemPromptStore, text: str) -> None:
    """Save *text* with a small sleep to guarantee unique timestamps."""
    store.save_prompt(text)
    time.sleep(0.05)


# ── save / load ──────────────────────────────────────────────────────


class TestSaveLoad:
    """Basic save and load roundtrip."""

    def test_save_load_roundtrip(self, tmp_path: Path) -> None:
        """Save text, load it back — matches exactly."""
        store, prompt_path = _make_store(tmp_path)
        text = "You are G.A.I.A., an advanced AI."
        store.save_prompt(text)
        assert store.load_prompt() == text
        assert prompt_path.exists()

    def test_load_missing_returns_empty(self, tmp_path: Path) -> None:
        """Load before any save returns empty string."""
        store, _ = _make_store(tmp_path)
        assert store.load_prompt() == ""

    def test_empty_prompt_raises_valueerror(self, tmp_path: Path) -> None:
        """Saving whitespace-only text raises ValueError."""
        store, _ = _make_store(tmp_path)
        with pytest.raises(ValueError, match="cannot be empty"):
            store.save_prompt("   ")

    def test_empty_string_raises_valueerror(self, tmp_path: Path) -> None:
        """Saving an empty string raises ValueError."""
        store, _ = _make_store(tmp_path)
        with pytest.raises(ValueError, match="cannot be empty"):
            store.save_prompt("")

    def test_returns_manifest_entry(self, tmp_path: Path) -> None:
        """save_prompt returns a dict with expected keys."""
        store, _ = _make_store(tmp_path)
        result = store.save_prompt("test prompt")
        assert result["version"] == 1
        assert "path" in result
        assert "timestamp" in result
        assert result["length"] == 11


# ── backup rotation ──────────────────────────────────────────────────


class TestBackupRotation:
    """Timestamped backup creation and pruning."""

    def test_first_save_no_backup(self, tmp_path: Path) -> None:
        """First save creates AGENT.md with no backups."""
        store, prompt_path = _make_store(tmp_path)
        store.save_prompt("v1 text")
        assert prompt_path.exists()
        assert store.load_prompt() == "v1 text"
        assert store.list_backups() == []

    def test_second_save_creates_backup(self, tmp_path: Path) -> None:
        """Second save backs up v1, writes v2."""
        store, prompt_path = _make_store(tmp_path)
        _save(store, "first version")
        _save(store, "second version")

        assert prompt_path.read_text() == "second version"
        backups = store.list_backups()
        assert len(backups) == 1
        assert backups[0]["version"] == 1
        assert "first version" in Path(backups[0]["path"]).read_text()

    def test_multiple_saves_ordered_newest_first(self, tmp_path: Path) -> None:
        """Backups are listed newest-first (version 1 = most recent).

        The first save creates no backup (nothing to back up yet), so 3 saves
        produce 2 backups.
        """
        store, _ = _make_store(tmp_path)
        _save(store, "v1")
        _save(store, "v2")
        _save(store, "v3")

        backups = store.list_backups()
        assert len(backups) == 2
        # Version 1 should contain "v2" (backed up by the v3 save)
        assert "v2" in Path(backups[0]["path"]).read_text()
        assert "v1" in Path(backups[1]["path"]).read_text()

    def test_max_backups_prunes_oldest(self, tmp_path: Path) -> None:
        """Saving more than MAX_BACKUPS prunes oldest backups."""
        store, _ = _make_store(tmp_path)
        for i in range(MAX_BACKUPS + 3):
            _save(store, f"prompt {i}")

        backups = store.list_backups()
        assert len(backups) <= MAX_BACKUPS

    def test_prunes_to_exact_max(self, tmp_path: Path) -> None:
        """After many saves, exactly MAX_BACKUPS are kept."""
        store, _ = _make_store(tmp_path)
        for i in range(MAX_BACKUPS * 2):
            _save(store, f"prompt {i}")

        assert len(store.list_backups()) == MAX_BACKUPS


# ── rollback ─────────────────────────────────────────────────────────


class TestRollback:
    """Rolling back to a specific backup version."""

    def test_rollback_success(self, tmp_path: Path) -> None:
        """Rollback to version 1 restores the oldest backup."""
        store, prompt_path = _make_store(tmp_path)
        _save(store, "first")
        _save(store, "second")
        _save(store, "third")

        result = store.rollback(1)  # restore "second" (backed up by "third" save)
        assert prompt_path.read_text() == "second"
        assert "restored_at" in result

    def test_rollback_version_2(self, tmp_path: Path) -> None:
        """Rollback to version 2 restores the second-most-recent backup."""
        store, prompt_path = _make_store(tmp_path)
        _save(store, "v1")
        _save(store, "v2")
        _save(store, "v3")

        result = store.rollback(2)  # restore "v1"
        assert prompt_path.read_text() == "v1"
        assert result["version"] == 2

    def test_rollback_not_found_no_backups(self, tmp_path: Path) -> None:
        """Rollback with no backups raises ValueError."""
        store, _ = _make_store(tmp_path)
        with pytest.raises(ValueError, match="no backups"):
            store.rollback(1)

    def test_rollback_out_of_range(self, tmp_path: Path) -> None:
        """Rollback to a version beyond what exists raises ValueError.

        Two saves produce one backup (first save creates no backup),
        so valid range is 1-1.
        """
        store, _ = _make_store(tmp_path)
        _save(store, "first")
        _save(store, "second")

        with pytest.raises(ValueError, match="version must be 1-1"):
            store.rollback(5)

    def test_rollback_invalid_version_string(self, tmp_path: Path) -> None:
        """Rollback to version 0 raises ValueError."""
        store, _ = _make_store(tmp_path)
        _save(store, "first")
        with pytest.raises(ValueError):
            store.rollback(0)


# ── list_backups ─────────────────────────────────────────────────────


class TestListBackups:
    """Listing and ordering of backups."""

    def test_empty_list_when_no_backups(self, tmp_path: Path) -> None:
        """list_backups returns empty list before any save."""
        store, _ = _make_store(tmp_path)
        assert store.list_backups() == []

    def test_entry_fields(self, tmp_path: Path) -> None:
        """Each backup entry has version, path, length, modified."""
        store, _ = _make_store(tmp_path)
        _save(store, "text")
        _save(store, "more text")

        entries = store.list_backups()
        assert len(entries) == 1
        entry = entries[0]
        assert entry["version"] == 1
        assert "path" in entry
        assert "length" in entry
        assert "modified" in entry

    def test_entries_sorted_newest_first(self, tmp_path: Path) -> None:
        """Entries are sorted with most recent backup first."""
        store, _ = _make_store(tmp_path)
        for i in range(5):
            _save(store, f"prompt {i}")

        # First save creates no backup, so 5 saves → 4 backups
        backups = store.list_backups()
        assert len(backups) == 4
        assert backups[0]["version"] == 1  # most recent
        assert backups[-1]["version"] == 4  # oldest

    def test_active_prompt_not_in_list(self, tmp_path: Path) -> None:
        """list_backups does not include the current AGENT.md."""
        store, prompt_path = _make_store(tmp_path)
        _save(store, "current")

        backups = store.list_backups()
        backup_paths = {Path(e["path"]) for e in backups}
        assert prompt_path not in backup_paths


# ── atomic writes ────────────────────────────────────────────────────


class TestAtomicWrite:
    """Temporary files are cleaned up after save."""

    def test_no_tmp_files_after_save(self, tmp_path: Path) -> None:
        """No .md.tmp files remain after save_prompt."""
        store, prompt_path = _make_store(tmp_path)
        _save(store, "test")

        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == [], f"Leftover temp files: {tmp_files}"

    def test_no_tmp_files_after_rollback(self, tmp_path: Path) -> None:
        """No .md.tmp files remain after rollback."""
        store, prompt_path = _make_store(tmp_path)
        _save(store, "first")
        _save(store, "second")

        store.rollback(1)

        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == [], f"Leftover temp files: {tmp_files}"
