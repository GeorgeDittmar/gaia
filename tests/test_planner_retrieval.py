"""Tests for the planner retrieval branching and PlannerResult schema."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from conftest import _make_mock_run_result
from gaia.core.agent.base import PlannerResult


# ── Schema tests ──────────────────────────────────────────────────────


class TestPlannerResultSchema:
    """Tests for PlannerResult field defaults and validation."""

    def test_default_source_is_semantic(self):
        """source defaults to 'semantic' when not provided."""
        result = PlannerResult()
        assert result.source == "semantic"

    def test_source_accepts_valid_values(self):
        """All three literal values are accepted."""
        for value in ("semantic", "episodic", "both"):
            result = PlannerResult(source=value)
            assert result.source == value

    def test_source_invalid_rejected(self):
        """Invalid values raise a validation error."""
        with pytest.raises(Exception):  # pydantic.ValidationError
            PlannerResult(source="unknown")

    def test_valid_with_empty_queries(self):
        """retrieve=False with an empty queries list is valid."""
        result = PlannerResult(retrieve=False, queries=[])
        assert result.retrieve is False
        assert result.queries == []


# ── Retrieval branching tests ─────────────────────────────────────────


class TestAinteractRetrievalBranching:
    """Tests that ainteract branches on PlannerResult.source."""

    @pytest.fixture()
    def _gaia_with_memory(self):
        """Build a Gaia instance with mocked memory and agent."""
        mock_core_agent = MagicMock()
        mock_cm = _make_mock_run_result(["ok"])
        mock_core_agent.run_stream = MagicMock(return_value=mock_cm)

        mock_planner_agent = MagicMock()

        mock_memory = MagicMock()
        mock_memory.semantic_search = AsyncMock(return_value=[])
        mock_memory.episodic_search = AsyncMock(return_value=[])

        with (
            patch("gaia.core.agent.base.OpenAIChatModel"),
            patch("gaia.core.agent.base.OpenAIProvider"),
            patch("gaia.core.agent.base.Agent") as mock_agent_cls,
        ):
            mock_agent_cls.side_effect = [
                mock_core_agent,  # _make_extraction_agent (first call)
                mock_planner_agent,  # _make_planner_agent (second call)
                mock_core_agent,  # __core_agent (third call)
            ]

            from gaia.core.agent.base import Gaia

            agent = Gaia("You are a helpful assistant.", memory=mock_memory)
            agent._Gaia__core_agent = mock_core_agent
            agent._Gaia__planner_agent = mock_planner_agent
            yield agent

    async def test_semantic_only(
        self, _gaia_with_memory
    ):
        """source='semantic' calls semantic_search, not episodic_search."""
        gaia = _gaia_with_memory
        gaia._Gaia__message_hist = [{"role": "user", "content": "hello"}]

        mock_result = MagicMock()
        mock_result.output = PlannerResult(
            retrieve=True, source="semantic", queries=["test query"]
        )
        gaia._Gaia__planner_agent.run = AsyncMock(return_value=mock_result)

        tokens = [t async for t in gaia.ainteract(prompt="hello")]
        assert len(tokens) == 1

        gaia._memory.semantic_search.assert_awaited_once_with("test query", limit=3)
        gaia._memory.episodic_search.assert_not_awaited()

    async def test_episodic_only(
        self, _gaia_with_memory
    ):
        """source='episodic' calls episodic_search, not semantic_search."""
        gaia = _gaia_with_memory
        gaia._Gaia__message_hist = [{"role": "user", "content": "hello"}]

        mock_result = MagicMock()
        mock_result.output = PlannerResult(
            retrieve=True, source="episodic", queries=["test query"]
        )
        gaia._Gaia__planner_agent.run = AsyncMock(return_value=mock_result)

        tokens = [t async for t in gaia.ainteract(prompt="hello")]
        assert len(tokens) == 1

        gaia._memory.episodic_search.assert_awaited_once_with("test query", limit=5)
        gaia._memory.semantic_search.assert_not_awaited()

    async def test_both_sources(
        self, _gaia_with_memory
    ):
        """source='both' calls both semantic_search and episodic_search."""
        gaia = _gaia_with_memory
        gaia._Gaia__message_hist = [{"role": "user", "content": "hello"}]

        mock_result = MagicMock()
        mock_result.output = PlannerResult(
            retrieve=True, source="both", queries=["test query"]
        )
        gaia._Gaia__planner_agent.run = AsyncMock(return_value=mock_result)

        tokens = [t async for t in gaia.ainteract(prompt="hello")]
        assert len(tokens) == 1

        gaia._memory.semantic_search.assert_awaited_once_with("test query", limit=3)
        gaia._memory.episodic_search.assert_awaited_once_with("test query", limit=5)

    async def test_no_retrieve_calls_no_searches(
        self, _gaia_with_memory
    ):
        """retrieve=False skips all memory calls."""
        gaia = _gaia_with_memory
        gaia._Gaia__message_hist = [{"role": "user", "content": "hello"}]

        mock_result = MagicMock()
        mock_result.output = PlannerResult(retrieve=False)
        gaia._Gaia__planner_agent.run = AsyncMock(return_value=mock_result)

        tokens = [t async for t in gaia.ainteract(prompt="hello")]
        assert len(tokens) == 1

        gaia._memory.semantic_search.assert_not_awaited()
        gaia._memory.episodic_search.assert_not_awaited()
