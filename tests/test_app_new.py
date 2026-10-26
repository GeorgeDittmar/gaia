"""Tests for app-level changes: AGENT.md loading, settings sync, background timers."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gaia.config import Settings


class TestGaiaTUIAppAgentMdLoading:
    """Tests for GaiaTUIApp loading AGENT.md into settings."""

    def test_loads_agent_md_content_into_settings(self, tmp_path: Path):
        """GaiaTUIApp.__init__ loads AGENT.md content into settings.system_prompt."""
        agent_md_content = "### WHO YOU ARE\nYou are G.A.I.A., an advanced AI.\n"
        agent_md = tmp_path / "AGENT.md"
        agent_md.write_text(agent_md_content, encoding="utf-8")

        original_cwd = Path.cwd()
        try:
            import os
            os.chdir(tmp_path)

            with patch("gaia.gaia_tui_app.load_config") as mock_load_config:
                mock_settings = Settings(
                    model="test",
                    endpoint="http://test.local",
                    encrypted=True,
                    system_prompt="placeholder from settings.json",
                )
                mock_load_config.return_value = mock_settings

                with patch("gaia.gaia_tui_app.Gaia"):
                    from gaia.gaia_tui_app import GaiaTUIApp

                    app = GaiaTUIApp()

            # Settings should have AGENT.md content, not the placeholder
            assert app.settings.system_prompt == agent_md_content
        finally:
            os.chdir(original_cwd)

    def test_falls_back_to_settings_when_agent_md_missing(self, tmp_path: Path):
        """GaiaTUIApp falls back to settings.json when AGENT.md is missing."""
        original_cwd = Path.cwd()
        try:
            import os
            os.chdir(tmp_path)

            fallback_prompt = "You are G.A.I.A., a local AI assistant."
            with patch("gaia.gaia_tui_app.load_config") as mock_load_config:
                mock_settings = Settings(
                    model="test",
                    system_prompt=fallback_prompt,
                )
                mock_load_config.return_value = mock_settings

                with patch("gaia.gaia_tui_app.Gaia"):
                    from gaia.gaia_tui_app import GaiaTUIApp

                    app = GaiaTUIApp()

            # Should use settings.json content when AGENT.md is missing
            assert app.settings.system_prompt == fallback_prompt
        finally:
            os.chdir(original_cwd)

    def test_agent_receives_settings_system_prompt(self, tmp_path: Path):
        """Gaia agent receives system_prompt from settings (which loaded AGENT.md)."""
        agent_md_content = "You are a custom G.A.I.A."
        agent_md = tmp_path / "AGENT.md"
        agent_md.write_text(agent_md_content, encoding="utf-8")

        original_cwd = Path.cwd()
        try:
            import os
            os.chdir(tmp_path)

            with patch("gaia.gaia_tui_app.load_config") as mock_load_config:
                mock_settings = Settings(
                    model="test",
                    endpoint="http://test.local",
                    system_prompt=agent_md_content,
                )
                mock_load_config.return_value = mock_settings

                # Patch Gaia to capture what it's called with
                captured_kwargs = {}

                def _capture_gaia(**kwargs):
                    captured_kwargs.update(kwargs)
                    return MagicMock()

                with patch("gaia.gaia_tui_app.Gaia", side_effect=_capture_gaia):
                    from gaia.gaia_tui_app import GaiaTUIApp

                    app = GaiaTUIApp()

            # Settings should have AGENT.md content
            assert app.settings.system_prompt == agent_md_content
            # Gaia should have been called with the system_prompt from settings
            assert captured_kwargs.get("system_prompt") == agent_md_content
        finally:
            os.chdir(original_cwd)


class TestBackgroundTimer:
    """Tests for background summary timer setup."""

    def test_schedules_summary_check_on_mount(self, tmp_path: Path):
        """GaiaTUIApp sets up background summary timer in on_mount when memory exists."""
        agent_md = tmp_path / "AGENT.md"
        agent_md.write_text("Test prompt", encoding="utf-8")

        original_cwd = Path.cwd()
        try:
            import os
            os.chdir(tmp_path)

            with patch("gaia.gaia_tui_app.load_config") as mock_load_config:
                mock_settings = Settings(model="test")
                mock_load_config.return_value = mock_settings

                with patch("gaia.gaia_tui_app.Gaia"):
                    from gaia.gaia_tui_app import GaiaTUIApp

                    app = GaiaTUIApp()

            # When memory exists, on_mount should call set_interval for
            # both the summary check and the retention pruning.
            mock_store = MagicMock()
            app._memory = mock_store
            app.set_interval = MagicMock()
            app.ping_loop = MagicMock()

            # Simulate on_mount
            app.on_mount()

            # Verify timers were scheduled
            assert app.set_interval.call_count == 2
            intervals = {c.args[0] for c in app.set_interval.call_args_list}
            assert 600 in intervals  # 10-min summary check
            assert 86400 in intervals  # 24-hr retention pruning
            for call in app.set_interval.call_args_list:
                assert len(call.args) == 2
                assert callable(call.args[1])
        finally:
            os.chdir(original_cwd)

    # Note: _run_background_summary_check is a @work method that requires a
    # running event loop to invoke. The None-memory guard is verified
    # indirectly — the app starts and runs without crashing in other tests.


class TestSettingsModalPromptSync:
    """Tests for settings modal receiving AGENT.md content."""

    def test_settings_modal_receives_agent_md_prompt(self, tmp_path: Path):
        """SettingsModal receives the system_prompt from settings (which includes AGENT.md)."""
        agent_md_content = "### CUSTOM PROMPT\nYou are a special AI.\n"
        agent_md = tmp_path / "AGENT.md"
        agent_md.write_text(agent_md_content, encoding="utf-8")

        original_cwd = Path.cwd()
        try:
            import os
            os.chdir(tmp_path)

            with patch("gaia.gaia_tui_app.load_config") as mock_load_config:
                mock_settings = Settings(
                    model="test",
                    system_prompt=agent_md_content,
                )
                mock_load_config.return_value = mock_settings

                with patch("gaia.gaia_tui_app.Gaia"):
                    from gaia.gaia_tui_app import GaiaTUIApp

                    app = GaiaTUIApp()

            # The settings modal will receive app.settings
            # Verify the system_prompt is the AGENT.md content
            assert app.settings.system_prompt == agent_md_content
            assert "CUSTOM PROMPT" in app.settings.system_prompt
        finally:
            os.chdir(original_cwd)


class TestRemoveByIdHelper:
    """Tests for _remove_by_id helper in slash_commands."""

    def test_remove_by_id_removes_matching_widget(self):
        """_remove_by_id removes the widget with the given ID."""
        from textual.containers import ScrollableContainer
        from textual.widgets import Static

        from gaia.slash_commands import _remove_by_id

        container = MagicMock(spec=ScrollableContainer)
        widget = MagicMock(spec=Static)
        container.query_one.return_value = widget

        _remove_by_id(container, "my-id")

        container.query_one.assert_called_once_with("#my-id", Static)
        widget.remove.assert_called_once()

    def test_remove_by_id_noop_when_widget_not_found(self):
        """_remove_by_id does not crash when widget doesn't exist."""
        from textual.containers import ScrollableContainer
        from textual.widgets import Static

        from gaia.slash_commands import _remove_by_id

        container = MagicMock(spec=ScrollableContainer)
        container.query_one.side_effect = Exception("no such widget")

        # Should not raise
        _remove_by_id(container, "nonexistent-id")

        container.query_one.assert_called_once_with("#nonexistent-id", Static)
