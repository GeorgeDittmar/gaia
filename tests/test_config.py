"""Tests for config.py — load_config and save_config."""

import json
from pathlib import Path

from gaia.config import DEFAULT_SETTINGS, Settings, load_config, save_config


class TestSaveConfig:
    """Tests for save_config()."""

    def test_writes_json_to_file(self, tmp_path: Path) -> None:
        """save_config writes a valid JSON file from a Settings object."""
        settings_file = tmp_path / "settings.json"
        settings_obj = Settings(
            model="test-model",
            endpoint="http://test.local",
            encrypted=False,
        )

        # Patch SETTINGS_FILE so we write to our temp file
        import gaia.config as config_mod
        original = config_mod.SETTINGS_FILE
        config_mod.SETTINGS_FILE = settings_file

        try:
            save_config(settings_obj)

            with open(settings_file, "r", encoding="utf-8") as f:
                written = json.load(f)

            assert written["model"] == "test-model"
            assert written["endpoint"] == "http://test.local"
            assert written["encrypted"] is False
        finally:
            config_mod.SETTINGS_FILE = original

    def test_roundtrips_all_keys(self, tmp_path: Path) -> None:
        """save_config preserves all Settings fields through to_dict."""
        settings_file = tmp_path / "settings.json"
        import gaia.config as config_mod
        original = config_mod.SETTINGS_FILE
        config_mod.SETTINGS_FILE = settings_file

        try:
            saved = Settings(
                model="llama3.3:70b",
                endpoint="http://custom.local",
                encrypted=True,
                system_prompt="custom prompt text",
            )
            save_config(saved)

            with open(settings_file, "r", encoding="utf-8") as f:
                written = json.load(f)

            for key in saved.to_dict():
                assert key in written, f"Key '{key}' missing from saved config"
        finally:
            config_mod.SETTINGS_FILE = original


class TestLoadConfig:
    """Tests for load_config()."""

    def test_returns_defaults_when_file_missing(
        self, settings_path: Path, tmp_path: Path
    ) -> None:
        """load_config returns a Settings object with default values."""
        result = load_config()

        assert isinstance(result, Settings)
        assert result.model == "qwen2.5-coder:32b"
        assert result.endpoint == DEFAULT_SETTINGS["endpoint"]
        assert result.encrypted is True

    def test_creates_file_when_missing(
        self, settings_path: Path, tmp_path: Path
    ) -> None:
        """load_config creates settings.json with defaults if absent."""
        assert not settings_path.exists()

        load_config()

        assert settings_path.exists()
        with open(settings_path, "r", encoding="utf-8") as f:
            written = json.load(f)
        assert written["model"] == DEFAULT_SETTINGS["model"]
        assert written["endpoint"] == DEFAULT_SETTINGS["endpoint"]

    def test_reads_existing_settings(self, settings_path: Path) -> None:
        """load_config reads and returns custom settings from file."""
        custom = {
            "model": "llama3.3:70b",
            "endpoint": "http://custom.local:9999",
            "encrypted": False,
            "system_prompt": "custom prompt",
        }
        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump(custom, f)

        result = load_config()

        assert result.model == "llama3.3:70b"
        assert result.endpoint == "http://custom.local:9999"
        assert result.encrypted is False
        assert result.system_prompt == "custom prompt"

    def test_merges_over_defaults(self, settings_path: Path) -> None:
        """load_config merges file values over defaults, not replacing them."""
        partial = {"model": "gpt-4o"}
        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump(partial, f)

        result = load_config()

        assert result.model == "gpt-4o"
        assert result.endpoint == DEFAULT_SETTINGS["endpoint"]
        assert result.encrypted is DEFAULT_SETTINGS["encrypted"]

    def test_returns_defaults_on_corrupt_file(
        self, settings_path: Path
    ) -> None:
        """load_config returns defaults when the file is corrupt JSON."""
        with open(settings_path, "w", encoding="utf-8") as f:
            f.write("{ this is not valid json }}}}")

        result = load_config()

        assert isinstance(result, Settings)
        assert result.model == DEFAULT_SETTINGS["model"]
        assert result.endpoint == DEFAULT_SETTINGS["endpoint"]
