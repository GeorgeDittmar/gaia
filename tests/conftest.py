"""Shared fixtures for the G.A.I.A. test suite."""

import json
import tempfile
from pathlib import Path
from typing import Generator, Dict, Any
import pytest

from gaia.config import DEFAULT_SETTINGS, SETTINGS_FILE


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
    import gaia.config as config_mod
    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump(DEFAULT_SETTINGS, f, indent=4)
    return settings_path
