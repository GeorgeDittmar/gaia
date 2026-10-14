"""Tests for the Settings and MemoryConfig dataclasses."""

from gaia.config import DEFAULT_SETTINGS, MemoryConfig, Settings


class TestMemoryConfig:
    def test_defaults(self) -> None:
        cfg = MemoryConfig()
        assert cfg.backend == "file"
        assert cfg.memory_dir == "~/.gaia/memory"

    def test_custom_values(self) -> None:
        cfg = MemoryConfig(backend="chroma", memory_dir="/custom/path")
        assert cfg.backend == "chroma"
        assert cfg.memory_dir == "/custom/path"

    def test_is_frozen(self) -> None:
        cfg = MemoryConfig()
        try:
            cfg.backend = "something"
            assert False, "Should not allow mutation"
        except Exception:
            pass


class TestSettings:
    def test_defaults(self) -> None:
        s = Settings()
        assert s.model == "qwen2.5-coder:32b"
        assert s.endpoint == "http://localhost:11434"
        assert s.encrypted is True

    def test_custom_values(self) -> None:
        s = Settings(model="gpt-4o", endpoint="http://x.local")
        assert s.model == "gpt-4o"
        assert s.endpoint == "http://x.local"

    def test_memory_config_default(self) -> None:
        s = Settings()
        assert s.memory.backend == "file"

    def test_from_dict_valid(self) -> None:
        d = {"model": "llama3.3:70b", "endpoint": "http://test.local", "encrypted": False}
        s = Settings.from_dict(d)
        assert s.model == "llama3.3:70b"
        assert s.endpoint == "http://test.local"
        assert s.encrypted is False

    def test_from_dict_invalid_backend_defaults_to_file(self) -> None:
        d = {"memory": {"backend": "postgres"}}
        s = Settings.from_dict(d)
        assert s.memory.backend == "file"

    def test_from_dict_missing_keys_uses_defaults(self) -> None:
        s = Settings.from_dict({})
        assert s.model == DEFAULT_SETTINGS["model"]
        assert s.endpoint == DEFAULT_SETTINGS["endpoint"]

    def test_from_dict_memory_none_defaults_to_file(self) -> None:
        """When memory is None (not absent), from_dict should not crash."""
        s = Settings.from_dict({"memory": None})
        assert s.memory.backend == "file"

    def test_from_dict_memory_string_defaults_to_file(self) -> None:
        """When memory is a bare string, from_dict should not crash."""
        s = Settings.from_dict({"memory": "file"})
        assert s.memory.backend == "file"

    def test_from_dict_memory_int_defaults_to_file(self) -> None:
        """Any non-dict memory value falls back gracefully."""
        s = Settings.from_dict({"memory": 42})
        assert s.memory.backend == "file"

    def test_save_roundtrip(self) -> None:
        original = Settings(
            model="test-model",
            endpoint="http://roundtrip.local",
            encrypted=False,
            system_prompt="test prompt",
        )
        d = original.to_dict()
        restored = Settings.from_dict(d)
        assert restored.model == original.model
        assert restored.endpoint == original.endpoint
        assert restored.encrypted == original.encrypted
        assert restored.system_prompt == original.system_prompt
        assert restored.memory.backend == original.memory.backend

    def test_to_dict_contains_memory_backend(self) -> None:
        d = Settings().to_dict()
        assert d["memory"]["backend"] == "file"
        assert d["memory_dir"] == "~/.gaia/memory"
