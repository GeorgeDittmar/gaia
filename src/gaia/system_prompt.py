"""Versioned system-prompt storage with timestamped backups.

Operates on ``AGENT.md`` in the project root.  Each save creates a
timestamped backup named ``AGENT.bk.<YYYYMMDD-HHMMSS-uuuuuu>.md``
(microsecond precision).  At most 10 backups are kept (oldest pruned
on overflow).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_PROMPT_PATH: Path = Path("AGENT.md")
MAX_BACKUPS: int = 10


class SystemPromptStore:
    """Persist a system prompt in ``AGENT.md`` with timestamped backups."""

    def __init__(self, prompt_path: Path | None = None) -> None:
        self._prompt_path = prompt_path or DEFAULT_PROMPT_PATH
        self._prompt_dir = self._prompt_path.resolve().parent
        # e.g. "AGENT.md" → "AGENT.bk.*.md"
        self._bk_pattern = f"{self._prompt_path.stem}.bk.*.{self._prompt_path.suffix.lstrip('.')}"

    # ── public API ───────────────────────────────────────────────────

    def save_prompt(self, text: str) -> dict[str, Any]:
        """Save *text* as the current system prompt, creating a backup.

        If ``AGENT.md`` already exists, it is moved to a timestamped backup
        ``AGENT.bk.<YYYYMMDD-HHMMSS>.md`` before the new text is written.
        Backups are pruned to keep at most ``MAX_BACKUPS`` entries (oldest
        removed first).

        Args:
            text: The system prompt text to save.

        Returns:
            Manifest entry describing the saved prompt.

        Raises:
            ValueError: If *text* is empty after stripping whitespace.
        """
        if not text.strip():
            raise ValueError("system prompt cannot be empty")

        self._ensure_dir()

        # Back up existing prompt if it exists
        if self._prompt_path.exists():
            ts = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            backup_name = (
                f"{self._prompt_path.stem}.bk.{ts}"
                f".{self._prompt_path.suffix.lstrip('.')}"
            )
            backup_path = self._prompt_dir / backup_name
            self._prompt_path.rename(backup_path)
            logger.info("backed up prompt → %s", backup_path)

        # Write new prompt atomically
        tmp = self._prompt_path.with_suffix(".md.tmp")
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp, self._prompt_path)

        # Prune old backups
        self._prune_backups()

        now = datetime.now().isoformat(timespec="seconds")
        return {
            "version": 1,
            "path": str(self._prompt_path),
            "timestamp": now,
            "length": len(text),
        }

    def load_prompt(self) -> str:
        """Load the current prompt from ``AGENT.md``.

        Returns:
            Prompt text, or ``""`` if the file does not exist.
        """
        if not self._prompt_path.exists():
            return ""
        with open(self._prompt_path, "r", encoding="utf-8", newline="") as f:
            return f.read()

    def rollback(self, version: int) -> dict[str, Any]:
        """Restore a numbered backup over ``AGENT.md``.

        Args:
            version: Backup number to restore (1 = most recent).

        Returns:
            Manifest entry for the restored version.

        Raises:
            ValueError: If *version* does not correspond to an existing backup.
        """
        backups = self.list_backups()
        if not backups:
            raise ValueError("no backups available")

        if not 1 <= version <= len(backups):
            available = [b["version"] for b in backups]
            raise ValueError(
                f"version must be 1-{len(backups)}, got {version}"
                f" (available: {available})"
            )

        backup_entry = backups[version - 1]
        backup_path = Path(backup_entry["path"])

        if not backup_path.exists():
            raise ValueError(
                f"backup file not found: {backup_path}"
            )

        with open(backup_path, "r", encoding="utf-8", newline="") as f:
            text = f.read()
        with open(self._prompt_path, "w", encoding="utf-8", newline="") as f:
            f.write(text)

        return {
            "version": version,
            "path": str(backup_path),
            "restored_at": datetime.now().isoformat(timespec="seconds"),
        }

    def list_backups(self) -> list[dict[str, Any]]:
        """Return backup entries sorted newest-first (most recent = version 1).

        The active prompt (``AGENT.md``) is **not** included in the list.
        """
        entries: list[dict[str, Any]] = []
        version = 1
        for backup_file in sorted(
            self._prompt_dir.glob(self._bk_pattern),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        ):
            entries.append({
                "version": version,
                "path": str(backup_file),
                "length": backup_file.stat().st_size,
                "modified": datetime.fromtimestamp(
                    backup_file.stat().st_mtime
                ).isoformat(timespec="seconds"),
            })
            version += 1
        return entries

    # ── internal helpers ─────────────────────────────────────────────

    def _ensure_dir(self) -> None:
        """Create prompt_dir if it doesn't exist."""
        try:
            self._prompt_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            logger.exception("failed to create prompt directory")
            raise

    def _prune_backups(self) -> None:
        """Remove oldest backups beyond ``MAX_BACKUPS``."""
        backups = sorted(
            self._prompt_dir.glob(self._bk_pattern),
            key=lambda p: p.stat().st_mtime,
        )
        while len(backups) > MAX_BACKUPS:
            oldest = backups.pop(0)
            try:
                oldest.unlink()
                logger.info("pruned old backup → %s", oldest)
            except OSError:
                logger.exception("failed to prune backup → %s", oldest)
