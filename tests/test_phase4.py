"""Tests for Phase 4: MemoryFactory and lazy initialization."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gaia.config import MemoryConfig, Settings
from gaia.gaia_tui_app import GaiaTUIApp


class TestMemoryFactoryCreatesFileByDefault:
    """MemoryFactory.create returns FileMemoryStore for 'file' backend."""

    def test_creates_file_store_with_default_settings(self):
        """Default settings produce a FileMemoryStore."""
        with (
            patch("gaia.gaia_tui_app.MemoryFactory"),
        ):
            from gaia.core.memory.factory import MemoryFactory
            from gaia.core.memory.file_store import FileMemoryStore

            settings = Settings()  # backend="file" by default
            store = MemoryFactory.create(settings)

        assert isinstance(store, FileMemoryStore)

    def test_creates_file_store_with_explicit_backend(self):
        """Explicit backend='file' also produces FileMemoryStore."""
        from gaia.core.memory.factory import MemoryFactory
        from gaia.core.memory.file_store import FileMemoryStore

        settings = Settings(
            memory=MemoryConfig(backend="file", memory_dir="~/.gaia/memory")
        )
        store = MemoryFactory.create(settings)

        assert isinstance(store, FileMemoryStore)

    def test_file_store_dir_is_resolved(self):
        """memory_dir is expanded via Path.expanduser()."""
        from gaia.core.memory.factory import MemoryFactory
        from gaia.core.memory.file_store import FileMemoryStore

        settings = Settings(
            memory=MemoryConfig(backend="file", memory_dir="~/.gaia/memory")
        )
        store = MemoryFactory.create(settings)

        assert isinstance(store, FileMemoryStore)
        # The store's _memory_dir should be an expanded absolute path
        assert str(store._memory_dir).startswith("/")


class TestMemoryFactoryCreatesChromaWhenConfigured:
    """MemoryFactory.create returns ChromaMemoryStore for 'chroma' backend."""

    def test_creates_chroma_store(self):
        """backend='chroma' produces a ChromaMemoryStore."""
        with (
            patch("gaia.gaia_tui_app.MemoryFactory"),
        ):
            from gaia.core.memory.chroma_store import ChromaMemoryStore
            from gaia.core.memory.factory import MemoryFactory

            settings = Settings(
                memory=MemoryConfig(backend="chroma", memory_dir="~/.gaia/memory")
            )
            store = MemoryFactory.create(settings)

        assert isinstance(store, ChromaMemoryStore)


class TestMemoryFactoryReturnsNoneForUnknownBackend:
    """MemoryFactory.create returns None for unknown backends."""

    def test_unknown_backend_returns_none(self):
        """backend='sqlite' (or anything else) returns None."""
        from gaia.core.memory.factory import MemoryFactory

        settings = Settings(
            memory=MemoryConfig(backend="sqlite", memory_dir="~/.gaia/memory")
        )
        store = MemoryFactory.create(settings)

        assert store is None


class TestMemoryLazyInit:
    """Memory initialization is deferred to on_mount, not __init__."""

    def test_memory_not_initialized_in_init(self):
        """__init__ does not call initialize() — no asyncio.run()."""
        with (
            patch("gaia.gaia_tui_app.load_config", return_value=Settings()),
            patch("gaia.gaia_tui_app.Gaia"),
        ):
            # Patch asyncio.run to detect if it's called
            with patch("asyncio.run") as mock_run:
                GaiaTUIApp()

        # asyncio.run must NOT be called during __init__
        mock_run.assert_not_called()

    @pytest.mark.asyncio
    async def test_memory_initialized_in_on_mount(self):
        """on_mount schedules initialize() via asyncio.create_task."""
        with (
            patch("gaia.gaia_tui_app.load_config", return_value=Settings()),
            patch("gaia.gaia_tui_app.Gaia"),
            patch("gaia.gaia_tui_app.MemoryFactory.create") as mock_create,
        ):
            mock_store = MagicMock()
            mock_store.initialize = AsyncMock()
            mock_create.return_value = mock_store

            app = GaiaTUIApp()
            app.on_mount()

            # Give the task a chance to run
            await asyncio.sleep(0.05)
            mock_store.initialize.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_memory_init_failure_sets_memory_none(self):
        """If initialize() fails, __on_memory_init_done sets _memory to None."""
        with (
            patch("gaia.gaia_tui_app.load_config", return_value=Settings()),
            patch("gaia.gaia_tui_app.Gaia"),
        ):
            from gaia.core.memory.file_store import FileMemoryStore

            app = GaiaTUIApp()
            app._memory = FileMemoryStore()

            # Call initialize to create a real store, then fail it
            await app._memory.initialize()
            # Force a failure by corrupting state, then simulate init_done
            app._memory = FileMemoryStore()
            app._initialized = True  # Pretend it was initialized

            # Simulate the done callback on a failing task

            async def failing_init():
                raise RuntimeError("disk full")

            task = asyncio.create_task(failing_init())
            app._memory = FileMemoryStore()  # fresh store
            app._GaiaTUIApp__memory_init_task = task
            app._GaiaTUIApp__on_memory_init_done(task)

            # _memory should be None after callback retrieves exception
            assert app._memory is None
