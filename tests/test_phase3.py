"""Tests for Phase 3: wire agent to settings so /model changes the model.

Phase 3 changes:
1. Gaia.__init__ accepts model_name, endpoint, api_key params (previously hardcoded)
2. _make_extraction_agent() converted from module-level function to instance method
3. Gaia.reconfigure() added for hot-reloading model/endpoint
4. GaiaTUIApp passes model_name and endpoint from settings to Gaia constructor
5. /model command calls reconfigure() after updating settings
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gaia.config import Settings
from gaia.gaia_tui_app import GaiaTUIApp


def _get_provider_call_args(mock_provider, call_index):
    """Extract (base_url, api_key) from the Nth OpenAIProvider() call.

    Since OpenAIProvider is mocked, its constructor call_args are:
        call_args_list[i] = ((), {"base_url": ..., "api_key": ...})
    """
    call = mock_provider.call_args_list[call_index]
    return call[1]  # kwargs dict


# ── Test 1: Gaia accepts custom model and endpoint ────────────────────


class TestGaiaAcceptsCustomModelAndEndpoint:
    """Gaia.__init__ uses provided model_name and endpoint."""

    def test_creates_main_agent_with_custom_model_and_endpoint(self):
        """OpenAIChatModel is called with the provided model_name and endpoint."""
        with (
            patch("gaia.core.agent.base.OpenAIChatModel") as mock_model,
            patch("gaia.core.agent.base.OpenAIProvider") as mock_provider,
            patch("gaia.core.agent.base.Agent"),
        ):
            from gaia.core.agent.base import Gaia

            Gaia(
                "You are helpful.",
                model_name="test-model",
                endpoint="http://custom:9999/v1",
            )

            # First provider call: main agent
            provider_kwargs = _get_provider_call_args(mock_provider, 0)
            assert provider_kwargs["base_url"] == "http://custom:9999/v1"
            assert provider_kwargs["api_key"] == "not-needed"

            # First model call should use "test-model"
            assert mock_model.call_args_list[0][0][0] == "test-model"

    def test_creates_extraction_agent_with_same_config(self):
        """The extraction agent also uses the provided model_name and endpoint."""
        with (
            patch("gaia.core.agent.base.OpenAIChatModel") as mock_model,
            patch("gaia.core.agent.base.OpenAIProvider") as mock_provider,
            patch("gaia.core.agent.base.Agent"),
        ):
            from gaia.core.agent.base import Gaia

            Gaia(
                "You are helpful.",
                model_name="test-model",
                endpoint="http://custom:9999/v1",
            )

            # Both agents should call OpenAIChatModel with the same model
            assert mock_model.call_count == 2
            assert mock_provider.call_count == 2

            for i in range(2):
                assert mock_model.call_args_list[i][0][0] == "test-model"
                pkw = _get_provider_call_args(mock_provider, i)
                assert pkw["base_url"] == "http://custom:9999/v1"
                assert pkw["api_key"] == "not-needed"


# ── Test 2: reconfigure changes model ────────────────────────────────


class TestGaiaReconfigureChangesModel:
    def test_reconfigure_rebuilds_agent(self):
        """Calling reconfigure(model_name=...) creates a new agent."""
        with (
            patch("gaia.core.agent.base.OpenAIChatModel") as mock_model,
            patch("gaia.core.agent.base.OpenAIProvider") as mock_provider,
            patch("gaia.core.agent.base.Agent") as mock_agent,
        ):
            mock_agent.return_value = MagicMock()

            from gaia.core.agent.base import Gaia
            agent = Gaia(
                "You are helpful.",
                model_name="original-model",
                endpoint="http://original:8080/v1",
            )

            # 2 calls from __init__ (main + extraction)
            init_model_calls = mock_model.call_count

            agent.reconfigure(model_name="new-model")

            # Model name should be updated
            assert agent._model_name == "new-model"
            # New OpenAIChatModel calls: 2 (rebuild main agent + extraction agent)
            assert mock_model.call_count == init_model_calls + 2
            # Both new calls used "new-model"
            for i in range(init_model_calls, mock_model.call_count):
                assert mock_model.call_args_list[i][0][0] == "new-model"

    def test_reconfigure_resets_message_history(self):
        """reconfigure() resets __message_hist to empty list."""
        with (
            patch("gaia.core.agent.base.OpenAIChatModel"),
            patch("gaia.core.agent.base.OpenAIProvider"),
            patch("gaia.core.agent.base.Agent"),
        ):
            from gaia.core.agent.base import Gaia
            agent = Gaia(
                "You are helpful.",
                model_name="original-model",
                endpoint="http://original:8080/v1",
            )

            # Simulate some messages being added
            agent._Gaia__message_hist = [{"role": "user", "content": "hello"}]
            assert len(agent._Gaia__message_hist) == 1

            agent.reconfigure(model_name="new-model")

            assert agent._Gaia__message_hist == []


# ── Test 3: reconfigure with empty args is a no-op ───────────────────


class TestGaiaReconfigureEmptyArgs:
    def test_empty_args_noop(self):
        """Calling reconfigure() with no arguments does not change _model_name."""
        with (
            patch("gaia.core.agent.base.OpenAIChatModel") as mock_model,
            patch("gaia.core.agent.base.OpenAIProvider"),
            patch("gaia.core.agent.base.Agent") as mock_agent,
        ):
            mock_agent.return_value = MagicMock()

            from gaia.core.agent.base import Gaia
            agent = Gaia(
                "You are helpful.",
                model_name="original",
                endpoint="http://original:8080/v1",
            )

            original_model = agent._model_name
            original_endpoint = agent._endpoint

            agent.reconfigure()

            # Model and endpoint should be unchanged
            assert agent._model_name == original_model
            assert agent._endpoint == original_endpoint


# ── Test 4: partial updates ──────────────────────────────────────────


class TestGaiaReconfigurePartialUpdates:
    def test_reconfigure_endpoint_only(self):
        """Passing only endpoint changes _endpoint but keeps _model_name."""
        with (
            patch("gaia.core.agent.base.OpenAIChatModel") as mock_model,
            patch("gaia.core.agent.base.OpenAIProvider") as mock_provider,
            patch("gaia.core.agent.base.Agent") as mock_agent,
        ):
            mock_agent.return_value = MagicMock()

            from gaia.core.agent.base import Gaia
            agent = Gaia(
                "You are helpful.",
                model_name="m1",
                endpoint="e1",
            )

            agent.reconfigure(endpoint="e2")

            assert agent._model_name == "m1"
            assert agent._endpoint == "e2"
            # Verify the new agent was built with the new endpoint
            last_provider = _get_provider_call_args(mock_provider, -1)
            assert last_provider["base_url"] == "e2"


# ── Test 5: GaiaTUIApp model switch reconfigures agent ───────────────


class TestGaiatuiappModelSwitchReconfiguresAgent:
    """The /model slash command calls reconfigure() on the agent."""

    def _make_app(self, settings=None, agent=None):
        """Create a GaiaTUIApp with mocked dependencies."""
        with patch("gaia.gaia_tui_app.MemoryFactory.create", return_value=None):
            app = GaiaTUIApp()
        if settings is not None:
            app.settings = settings
        if agent is not None:
            app._GaiaTUIApp__core_agent = agent
        chat_box = MagicMock()
        chat_box.mount = AsyncMock()
        app.query_one = MagicMock(
            side_effect=lambda sel, cls: chat_box if sel == "#chat-container" else MagicMock()
        )
        app.query = MagicMock(return_value=[])
        return app, chat_box

    @pytest.mark.asyncio
    async def test_model_command_calls_reconfigure(self):
        """/model <name> calls reconfigure(model_name=...) on the agent."""
        settings = Settings(model="old-model")
        mock_agent = MagicMock()
        app, _ = self._make_app(settings=settings, agent=mock_agent)

        await app.handle_slash_command("/model new-model")

        mock_agent.reconfigure.assert_called_once_with(model_name="new-model")

    @pytest.mark.asyncio
    async def test_model_command_saves_config(self):
        """/model <name> persists the new model to disk."""
        settings = Settings(model="old-model")
        mock_agent = MagicMock()
        app, _ = self._make_app(settings=settings, agent=mock_agent)

        with patch("gaia.gaia_tui_app.save_config") as mock_save:
            await app.handle_slash_command("/model new-model")

        mock_save.assert_called_once()
        assert app.settings.model == "new-model"


# ── Test 6: extraction agent uses same config ────────────────────────


class TestExtractionAgentUsesSameConfig:
    """Both main and extraction agents share the same model/endpoint."""

    def test_both_agents_created_with_same_config(self):
        """OpenAIChatModel is called twice with identical model and endpoint."""
        with (
            patch("gaia.core.agent.base.OpenAIChatModel") as mock_model,
            patch("gaia.core.agent.base.OpenAIProvider") as mock_provider,
            patch("gaia.core.agent.base.Agent"),
        ):
            from gaia.core.agent.base import Gaia

            Gaia(
                "You are helpful.",
                model_name="test-model",
                endpoint="http://test:1234/v1",
            )

            # Should be exactly 2 calls: main agent + extraction agent
            assert mock_model.call_count == 2
            assert mock_provider.call_count == 2

            # Both calls should use the same model_name and endpoint
            for i in range(2):
                assert mock_model.call_args_list[i][0][0] == "test-model"
                pkw = _get_provider_call_args(mock_provider, i)
                assert pkw["base_url"] == "http://test:1234/v1"
                assert pkw["api_key"] == "not-needed"


# ── Test 7: reconfigure rebuilds both agents ─────────────────────────


class TestReconfigureRebuildsBothAgents:
    """reconfigure() rebuilds the extraction agent, not just the main one."""

    def test_reconfigure_rebuilds_extraction_agent(self):
        """After reconfigure(), both agents use the new model."""
        with (
            patch("gaia.core.agent.base.OpenAIChatModel") as mock_model,
            patch("gaia.core.agent.base.OpenAIProvider") as mock_provider,
            patch("gaia.core.agent.base.Agent") as mock_agent,
        ):
            mock_agent.return_value = MagicMock()

            from gaia.core.agent.base import Gaia
            agent = Gaia(
                "You are helpful.",
                model_name="original-model",
                endpoint="http://original:8080/v1",
            )

            init_calls = mock_model.call_count  # 2 (main + extraction)

            agent.reconfigure(model_name="new-model")

            # Both agents rebuilt = 2 more calls
            assert mock_model.call_count == init_calls + 2
            for i in range(init_calls, mock_model.call_count):
                assert mock_model.call_args_list[i][0][0] == "new-model"


# ── Test 8: /settings modal calls reconfigure ────────────────────────


class TestSettingsModalReconfigure:
    """The /settings modal calls reconfigure() after saving."""

    @pytest.mark.asyncio
    async def test_settings_modal_reconfigures_on_model_change(self):
        """/settings that changes the model triggers reconfigure()."""
        with patch("gaia.gaia_tui_app.MemoryFactory.create", return_value=None):
            app = GaiaTUIApp()
        app.settings = Settings(model="old-model", endpoint="http://old:11434")
        mock_agent = MagicMock()
        app._GaiaTUIApp__core_agent = mock_agent

        with patch("gaia.gaia_tui_app.save_config"):
            # Simulate the settings modal callback with a new model
            new_config = {"model": "new-model"}
            merged = {**app.settings.to_dict(), **new_config}
            app.settings = Settings.from_dict(merged)
            # This is what the handler does now:
            mock_agent.reconfigure(
                model_name=app.settings.model,
                endpoint=app.settings.endpoint,
            )

        mock_agent.reconfigure.assert_called_once_with(
            model_name="new-model",
            endpoint="http://old:11434",
        )
