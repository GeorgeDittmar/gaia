"""Tests for app-level logic (connection checker, input classification)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from gaia.config import (
    is_exit_command,
    is_slash_input,
    check_endpoint,
    EXIT_COMMANDS,
    SLASH_COMMANDS,
)
from gaia.gaia_tui_app import GaiaTUIApp


class TestIsExitCommand:
    """Tests for is_exit_command()."""

    def test_exit(self) -> None:
        assert is_exit_command("exit") is True

    def test_quit(self) -> None:
        assert is_exit_command("quit") is True

    def test_close(self) -> None:
        assert is_exit_command("close") is True

    def test_model_is_not_exit(self) -> None:
        assert is_exit_command("model") is False

    def test_unknown_is_not_exit(self) -> None:
        assert is_exit_command("nonexistent") is False

    def test_lowercase_only(self) -> None:
        """Commands are compared lowercase — caller handles lowercasing."""
        assert is_exit_command("exit") is True
        assert is_exit_command("quit") is True
        assert is_exit_command("close") is True

    def test_uppercase_not_matched(self) -> None:
        """Uppercase commands are not matched (caller lowercases first)."""
        assert is_exit_command("EXIT") is False
        assert is_exit_command("Quit") is False


class TestIsSlashInput:
    """Tests for is_slash_input()."""

    def test_prefixed_with_slash(self) -> None:
        assert is_slash_input("/model") is True
        assert is_slash_input("/help") is True

    def test_strips_leading_spaces(self) -> None:
        """Leading spaces are stripped before checking."""
        assert is_slash_input("  /model") is True

    def test_no_slash(self) -> None:
        assert is_slash_input("hello world") is False
        assert is_slash_input("") is False

    def test_slash_only(self) -> None:
        assert is_slash_input("/") is True


class TestCheckEndpoint:
    """Tests for the extracted check_endpoint() function."""

    @pytest.fixture(autouse=True)
    def _mock_urllib(self):
        """Mock urllib.request for all check_endpoint tests."""
        import urllib.request

        # Patch at the module level where it's used
        with patch.object(urllib.request, "Request") as mock_req:
            with patch.object(
                urllib.request, "urlopen"
            ) as mock_urlopen:
                self._mock_req = mock_req
                self._mock_urlopen = mock_urlopen
                yield

    @pytest.mark.asyncio
    async def test_returns_online_on_success(self) -> None:
        """HTTP 200 → ONLINE."""
        mock_response = MagicMock()
        mock_response.status = 200
        self._mock_urlopen.return_value.__enter__ = MagicMock(
            return_value=mock_response
        )
        self._mock_urlopen.return_value.__exit__ = MagicMock(
            return_value=None
        )

        result = await check_endpoint("http://localhost:11434")
        assert result == "ONLINE"

    @pytest.mark.asyncio
    async def test_returns_online_on_redirect(self) -> None:
        """HTTP 301 → ONLINE."""
        mock_response = MagicMock()
        mock_response.status = 301
        self._mock_urlopen.return_value.__enter__ = MagicMock(
            return_value=mock_response
        )
        self._mock_urlopen.return_value.__exit__ = MagicMock(
            return_value=None
        )

        result = await check_endpoint("http://localhost:11434")
        assert result == "ONLINE"

    @pytest.mark.asyncio
    async def test_returns_http_code_on_error(self) -> None:
        """HTTP 500 → HTTP 500."""
        mock_response = MagicMock()
        mock_response.status = 500
        self._mock_urlopen.return_value.__enter__ = MagicMock(
            return_value=mock_response
        )
        self._mock_urlopen.return_value.__exit__ = MagicMock(
            return_value=None
        )

        result = await check_endpoint("http://localhost:11434")
        assert result == "HTTP 500"

    @pytest.mark.asyncio
    async def test_returns_unreachable_on_network_error(self) -> None:
        """Connection refused → UNREACHABLE."""
        self._mock_urlopen.side_effect = OSError("Connection refused")

        result = await check_endpoint("http://localhost:99999")
        assert result == "UNREACHABLE"

    @pytest.mark.asyncio
    async def test_returns_unreachable_on_timeout(self) -> None:
        """Timeout → UNREACHABLE."""
        self._mock_urlopen.side_effect = TimeoutError()

        result = await check_endpoint("http://localhost:11434")
        assert result == "UNREACHABLE"


class TestGaiaTUIAppInit:
    """Tests for GaiaTUIApp initialization."""

    def test_loads_settings(self) -> None:
        """App.__init__ loads settings via load_config."""
        with patch(
            "gaia.gaia_tui_app.load_config", return_value={"model": "test"}
        ) as mock_load:
            app = GaiaTUIApp()

        mock_load.assert_called_once()
        assert app.settings == {"model": "test"}

    def test_creates_agent(self) -> None:
        """App.__init__ creates a Gaia agent."""
        with patch("gaia.gaia_tui_app.load_config", return_value={}):
            with patch("gaia.gaia_tui_app.Gaia") as mock_gaia:
                GaiaTUIApp()

        mock_gaia.assert_called_once()

    def test_initializes_shutting_down_false(self) -> None:
        """is_shutting_down starts as False."""
        with patch("gaia.gaia_tui_app.load_config", return_value={}):
            app = GaiaTUIApp()

        assert app.is_shutting_down is False

    def test_has_expected_bindings(self) -> None:
        """BINDINGS contains the standard keybindings."""
        commands = [b[0] for b in GaiaTUIApp.BINDINGS]
        assert "ctrl+c" in commands
        assert "ctrl+l" in commands
        assert "f1" in commands

    def test_init_memory_creates_store(self) -> None:
        """_init_memory creates and initializes a store when memory_db is set."""
        import tempfile
        from pathlib import Path

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)

        try:
            with patch(
                "gaia.gaia_tui_app.load_config",
                return_value={"model": "test", "memory_db": str(db_path)},
            ):
                with patch("gaia.gaia_tui_app.Gaia"):
                    app = GaiaTUIApp()

            assert app._memory is not None
            assert app._memory._db_path == db_path.resolve()
        finally:
            db_path.unlink(missing_ok=True)


class TestForceExit:
    """Tests for GaiaTUIApp.force_exit()."""

    def test_sets_shutting_down_flag(self) -> None:
        """force_exit sets is_shutting_down = True."""
        with patch("gaia.gaia_tui_app.load_config", return_value={}):
            app = GaiaTUIApp()
            app.force_exit()

        assert app.is_shutting_down is True

    def test_cancels_workers(self) -> None:
        """force_exit calls cancel_all on the workers manager."""
        with (
            patch("gaia.gaia_tui_app.load_config", return_value={}),
            patch.object(GaiaTUIApp, "workers") as mock_workers,
        ):
            app = GaiaTUIApp()
            app.force_exit()

        mock_workers.cancel_all.assert_called_once()

    def test_calls_exit(self) -> None:
        """force_exit calls App.exit()."""
        with patch("gaia.gaia_tui_app.load_config", return_value={}):
            app = GaiaTUIApp()
            app.exit = MagicMock()
            app.force_exit()

        app.exit.assert_called_once()

    @pytest.mark.asyncio
    async def test_force_exit_closes_memory(self) -> None:
        """force_exit calls _close_memory when memory is present."""
        with patch("gaia.gaia_tui_app.load_config", return_value={}):
            app = GaiaTUIApp()
            mock_store = AsyncMock()
            app._memory = mock_store
            app.exit = MagicMock()
            app.force_exit()

            # create_task schedules but doesn't run immediately; await it
            task = getattr(app, "_GaiaTUIApp__close_task", None)
            if task is not None:
                await task
            mock_store.close.assert_awaited_once()


class TestRememberCommand:
    """Tests for the /remember slash command."""

    def _make_app_and_chatbox(self, memory=None):
        """Create a GaiaTUIApp with mocked memory and chat_box."""
        app = GaiaTUIApp()
        app._memory = AsyncMock() if memory is False else memory
        chat_box = MagicMock()
        chat_box.mount = AsyncMock()  # mount is an async operation
        app.query_one = MagicMock(
            side_effect=lambda sel, cls: chat_box if sel == "#chat-container" else MagicMock()
        )
        app.query = MagicMock(return_value=[])
        return app, chat_box

    @pytest.mark.asyncio
    async def test_remember_inserts_fact(self) -> None:
        """/remember <fact> calls semantic_insert with correct args."""
        with patch("gaia.gaia_tui_app.load_config", return_value={}):
            app, chat_box = self._make_app_and_chatbox(memory=AsyncMock())

            await app.handle_slash_command("/remember user likes python")

            app._memory.semantic_insert.assert_awaited_once_with(
                content="user likes python",
                category="preference",
                confidence=1.0,
                source="user",
            )
            chat_box.mount.assert_called()

    @pytest.mark.asyncio
    async def test_remember_no_arg_shows_usage(self) -> None:
        """/remember without an arg shows usage help."""
        with patch("gaia.gaia_tui_app.load_config", return_value={}):
            app, chat_box = self._make_app_and_chatbox(memory=AsyncMock())

            await app.handle_slash_command("/remember")

            app._memory.semantic_insert.assert_not_called()
            chat_box.mount.assert_called_once()

    @pytest.mark.asyncio
    async def test_remember_without_memory_shows_message(self) -> None:
        """/remember when _memory is None does not raise."""
        with patch("gaia.gaia_tui_app.load_config", return_value={}):
            app, chat_box = self._make_app_and_chatbox(memory=None)

            await app.handle_slash_command("/remember test fact")

            chat_box.mount.assert_called_once()

    def test_remember_in_slash_commands_list(self) -> None:
        """/remember is listed in SLASH_COMMANDS for autocomplete."""
        cmd_names = {entry["cmd"] for entry in SLASH_COMMANDS}
        assert "/remember" in cmd_names, "Missing /remember from SLASH_COMMANDS"

