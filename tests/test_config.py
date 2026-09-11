"""Tests for config.py — load_config and save_config."""

import json
from pathlib import Path
from typing import Dict, Any

from gaia.config import DEFAULT_SETTINGS, load_config, save_config


class TestSaveConfig:
    """Tests for save_config()."""

    def test_writes_json_to_file(self, tmp_path: Path) -> None:
        """save_config writes a valid JSON file."""
        settings = tmp_path / "settings.json"
        config: Dict[str, Any] = {
            "model": "test-model",
            "endpoint": "http://test.local",
            "encrypted": False,
        }

        # Patch SETTINGS_FILE so we write to our temp file
        import gaia.config as config_mod
        original = config_mod.SETTINGS_FILE
        config_mod.SETTINGS_FILE = settings

        try:
            save_config(config)

            with open(settings, "r", encoding="utf-8") as f:
                written = json.load(f)

            assert written == config
        finally:
            config_mod.SETTINGS_FILE = original

    def test_preserves_all_keys(self, tmp_path: Path) -> None:
        """save_config round-trips all DEFAULT_SETTINGS keys."""
        settings = tmp_path / "settings.json"
        import gaia.config as config_mod
        original = config_mod.SETTINGS_FILE
        config_mod.SETTINGS_FILE = settings

        try:
            save_config(DEFAULT_SETTINGS)
            with open(settings, "r", encoding="utf-8") as f:
                written = json.load(f)

            for key in DEFAULT_SETTINGS:
                assert key in written, f"Key '{key}' missing from saved config"
        finally:
            config_mod.SETTINGS_FILE = original


class TestLoadConfig:
    """Tests for load_config()."""

    def test_returns_defaults_when_file_missing(
        self, settings_path: Path, tmp_path: Path
    ) -> None:
        """load_config returns defaults when settings.json does not exist."""
        # settings_path exists (tmp_path / "settings.json") but is empty
        # — actually it's just a Path, the file doesn't exist yet
        result = load_config()

        assert result == DEFAULT_SETTINGS

    def test_creates_file_when_missing(
        self, settings_path: Path, tmp_path: Path
    ) -> None:
        """load_config creates settings.json with defaults if absent."""
        # settings_path is a Path to tmp_path/settings.json which doesn't exist
        assert not settings_path.exists()

        load_config()

        assert settings_path.exists()
        with open(settings_path, "r", encoding="utf-8") as f:
            written = json.load(f)
        assert written == DEFAULT_SETTINGS

    def test_reads_existing_settings(self, settings_path: Path) -> None:
        """load_config reads and returns custom settings from file."""
        import gaia.config as config_mod
        custom = {
            "model": "llama3.3:70b",
            "endpoint": "http://custom.local:9999",
            "encrypted": False,
            "system_prompt": "custom prompt",
        }
        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump(custom, f)

        result = load_config()

        assert result["model"] == "llama3.3:70b"
        assert result["endpoint"] == "http://custom.local:9999"
        assert result["encrypted"] is False
        assert result["system_prompt"] == "custom prompt"

    def test_merges_over_defaults(self, settings_path: Path) -> None:
        """load_config merges file values over defaults, not replacing them."""
        import gaia.config as config_mod
        # Only set model, leave others out — should get defaults for those
        partial = {"model": "gpt-4o"}
        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump(partial, f)

        result = load_config()

        assert result["model"] == "gpt-4o"
        assert result["endpoint"] == DEFAULT_SETTINGS["endpoint"]
        assert result["encrypted"] is DEFAULT_SETTINGS["encrypted"]

    def test_returns_defaults_on_corrupt_file(
        self, settings_path: Path
    ) -> None:
        """load_config returns defaults when the file is corrupt JSON."""
        with open(settings_path, "w", encoding="utf-8") as f:
            f.write("{ this is not valid json }}}}")

        result = load_config()

        assert result == DEFAULT_SETTINGS
