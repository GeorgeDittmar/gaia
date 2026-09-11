"""Tests for the Gaia core agent (src/gaia/core/agent/base.py)."""

from unittest.mock import AsyncMock, MagicMock, patch
from typing import AsyncIterator

import pytest


def _make_mock_run_result(
    tokens: list[str],
) -> MagicMock:
    """Build a StreamedRunResult mock that streams *tokens*.

    The real pydantic-ai result has:
        async with agent.run_stream(prompt) as result:
            async for chunk in result.stream_text(delta=True):
                yield chunk

    So ``run_stream`` must be an async context manager, and
    ``result.stream_text(delta=True)`` must return an async iterator.
    """

    async def _stream_text(delta: bool = False) -> AsyncIterator[str]:
        for token in tokens:
            yield token

    mock_result = MagicMock()
    mock_result.stream_text = MagicMock(return_value=_stream_text(delta=True))

    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_result
    mock_cm.__aexit__.return_value = None

    return mock_cm


@pytest.fixture()
def mock_openai_chat_model() -> MagicMock:
    """Return a mock OpenAIChatModel."""
    return MagicMock()


@pytest.fixture()
def gaia_agent(mock_openai_chat_model):
    """Build a Gaia instance with mocked dependencies.

    Patch pydantic-ai classes before importing Gaia so the agent
    constructor uses our mocks instead of real model providers.
    """
    mock_agent = MagicMock()
    mock_cm = _make_mock_run_result(["hello", " ", "world"])
    mock_agent.run_stream = MagicMock(return_value=mock_cm)

    with (
        patch("gaia.core.agent.base.OpenAIChatModel", return_value=mock_openai_chat_model),
        patch("gaia.core.agent.base.OpenAIProvider"),
        patch("gaia.core.agent.base.Agent", return_value=mock_agent),
    ):
        from gaia.core.agent.base import Gaia

        agent = Gaia()
        agent._Gaia__core_agent = mock_agent  # inject our mock
        return agent


class TestGaiaInit:
    """Tests for Gaia.__init__."""

    def test_creates_agent_with_model(self, mock_openai_chat_model):
        """Gaia.__init__ creates an Agent instance."""
        with (
            patch("gaia.core.agent.base.OpenAIChatModel", return_value=mock_openai_chat_model),
            patch("gaia.core.agent.base.OpenAIProvider"),
            patch("gaia.core.agent.base.Agent") as mock_agent_cls,
        ):
            from gaia.core.agent.base import Gaia
            Gaia()

        mock_agent_cls.assert_called_once()


class TestGaiaAinteract:
    """Tests for Gaia.ainteract()."""

    @pytest.mark.asyncio
    async def test_streams_tokens(self, gaia_agent):
        """ainteract yields tokens from the agent stream."""
        tokens = []
        async for token in gaia_agent.ainteract(prompt="hello"):
            tokens.append(token)

        assert tokens == ["hello", " ", "world"]

    @pytest.mark.asyncio
    async def test_yields_empty_when_agent_returns_nothing(self):
        """ainteract yields nothing if the agent produces no output."""
        mock_agent = MagicMock()
        mock_cm = _make_mock_run_result([])
        mock_agent.run_stream = MagicMock(return_value=mock_cm)

        from gaia.core.agent.base import Gaia
        with (
            patch("gaia.core.agent.base.OpenAIChatModel"),
            patch("gaia.core.agent.base.OpenAIProvider"),
            patch("gaia.core.agent.base.Agent", return_value=mock_agent),
        ):
            agent = Gaia()
            agent._Gaia__core_agent = mock_agent

        tokens = [t async for t in agent.ainteract(prompt="")]
        assert tokens == []

    @pytest.mark.asyncio
    async def test_calls_run_stream_with_prompt(self, gaia_agent):
        """ainteract passes the prompt to run_stream."""
        tokens = []
        async for token in gaia_agent.ainteract(prompt="test prompt"):
            tokens.append(token)

        gaia_agent._Gaia__core_agent.run_stream.assert_called_once_with("test prompt")

    @pytest.mark.asyncio
    async def test_is_async_generator(self):
        """ainteract returns an async generator (not a list)."""
        from gaia.core.agent.base import Gaia
        with (
            patch("gaia.core.agent.base.OpenAIChatModel"),
            patch("gaia.core.agent.base.OpenAIProvider"),
            patch("gaia.core.agent.base.Agent") as mock_agent_cls,
        ):
            mock_cm = _make_mock_run_result(["x"])
            mock_agent = MagicMock()
            mock_agent.run_stream = MagicMock(return_value=mock_cm)
            mock_agent_cls.return_value = mock_agent
            agent = Gaia()
            agent._Gaia__core_agent = mock_agent

        result = agent.ainteract(prompt="x")
        assert hasattr(result, "__aiter__")
