"""Shared fixtures for the G.A.I.A. test suite."""

import json
from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gaia.config import DEFAULT_SETTINGS, SETTINGS_FILE


def _make_mock_run_result(tokens: list[str]) -> MagicMock:
    """Build a StreamedRunResult mock that streams *tokens*.

    Returns an async-context-manager mock whose ``__aenter__`` yields a
    ``MagicMock`` with a ``stream_text(delta=True)`` async generator that
    yields each token in *tokens*.
    """

    async def _stream_text(delta: bool = False):
        for token in tokens:
            yield token

    mock_result = MagicMock()
    mock_result.stream_text = MagicMock(return_value=_stream_text(delta=True))

    mock_cm = MagicMock()
    mock_cm.__aenter__.return_value = mock_result
    mock_cm.__aexit__.return_value = None

    return mock_cm


@pytest.fixture()
def settings_path(tmp_path: Path) -> Generator[Path, None, None]:
    """Yield a temporary path to use as the settings file.

    Replaces `gaia.config.SETTINGS_FILE` so `load_config` / `save_config`
    operate on a clean temp file instead of the real ``settings.json``.
    """
    original = SETTINGS_FILE
    settings_path = tmp_path / "settings.json"

    # Monkey-patch SETTINGS_FILE used by config.py
    import gaia.config as config_mod
    config_mod.SETTINGS_FILE = settings_path

    yield settings_path

    # Restore original
    config_mod.SETTINGS_FILE = original


@pytest.fixture()
def empty_settings(settings_path: Path) -> Path:
    """Ensure the settings file exists but contains only defaults.

    Returns the path to the written file so callers can assert it was created.
    """
    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump(DEFAULT_SETTINGS, f, indent=4)
    return settings_path
