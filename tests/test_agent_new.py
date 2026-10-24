"""Tests for new Gaia agent features: inline extraction, conflict detection, AGENT.md loading."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from conftest import _make_mock_run_result


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture()
def mock_openai_chat_model() -> MagicMock:
    """Return a mock OpenAIChatModel."""
    return MagicMock()


@pytest.fixture()
def gaia_agent(mock_openai_chat_model):
    """Build a Gaia instance with mocked dependencies."""
    mock_core_agent = MagicMock()
    mock_core_agent.run_stream = MagicMock(
        return_value=_make_mock_run_result(["hello world"])
    )

    with (
        patch("gaia.core.agent.base.OpenAIChatModel", return_value=mock_openai_chat_model),
        patch("gaia.core.agent.base.OpenAIProvider"),
        patch("gaia.core.agent.base.Agent", return_value=mock_core_agent),
    ):
        from gaia.core.agent.base import Gaia

        agent = Gaia("You are a helpful assistant.")
        agent._Gaia__core_agent = mock_core_agent
        return agent


# ── Inline fact extraction ──────────────────────────────────────────────


class TestPostTurnExtract:
    """Tests for Gaia.post_turn_extract()."""

    @pytest.mark.asyncio
    async def test_saves_facts_to_semantic_memory(self, gaia_agent):
        """post_turn_extract saves extracted facts to semantic memory."""
        mock_memory = AsyncMock()
        mock_memory.semantic_similarity_search = AsyncMock(return_value=False)
        gaia_agent._memory = mock_memory

        # Mock the extraction agent to return a fact
        mock_extraction_agent = MagicMock()
        mock_result = MagicMock()
        mock_result.output.facts = [
            MagicMock(text="User prefers Python", confidence=0.8),
        ]
        mock_extraction_agent.run = AsyncMock(return_value=mock_result)
        gaia_agent._Gaia__extraction_agent = mock_extraction_agent

        with (
            patch("gaia.core.agent.base.asyncio.sleep", return_value=None),
            patch("gaia.core.agent.base.Gaia._classify_fact", return_value="preference"),
        ):
            saved = await gaia_agent.post_turn_extract(
                "I use Python for everything",
                "Got it! Python is a great choice.",
            )

        mock_memory.semantic_insert.assert_awaited_once()
        call_kwargs = mock_memory.semantic_insert.await_args.kwargs
        assert call_kwargs["content"] == "User prefers Python"
        assert call_kwargs["category"] == "preference"
        assert saved == 1

    @pytest.mark.asyncio
    async def test_skips_duplicate_facts(self, gaia_agent):
        """post_turn_extract skips facts that already exist (Jaccard dedup)."""
        mock_memory = AsyncMock()
        mock_memory.semantic_similarity_search = AsyncMock(return_value=True)
        gaia_agent._memory = mock_memory

        mock_extraction_agent = MagicMock()
        mock_result = MagicMock()
        mock_result.output.facts = [
            MagicMock(text="User prefers Python", confidence=0.8),
        ]
        mock_extraction_agent.run = AsyncMock(return_value=mock_result)
        gaia_agent._Gaia__extraction_agent = mock_extraction_agent

        with (
            patch("gaia.core.agent.base.asyncio.sleep", return_value=None),
            patch("gaia.core.agent.base.Gaia._classify_fact", return_value="preference"),
        ):
            saved = await gaia_agent.post_turn_extract(
                "I use Python",
                "Python is great.",
            )

        mock_memory.semantic_insert.assert_not_called()
        assert saved == 0

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_memory(self, gaia_agent):
        """post_turn_extract returns 0 when _memory is None."""
        gaia_agent._memory = None

        result = await gaia_agent.post_turn_extract(
            "test prompt",
            "test response",
        )

        assert result == 0

    @pytest.mark.asyncio
    async def test_returns_zero_when_extraction_fails(self, gaia_agent):
        """post_turn_extract returns 0 and never raises."""
        mock_memory = AsyncMock()
        gaia_agent._memory = mock_memory

        mock_extraction_agent = MagicMock()
        mock_extraction_agent.run = AsyncMock(side_effect=RuntimeError("model timeout"))
        gaia_agent._Gaia__extraction_agent = mock_extraction_agent

        with patch("gaia.core.agent.base.asyncio.sleep", return_value=None):
            result = await gaia_agent.post_turn_extract(
                "test",
                "response",
            )

        assert result == 0

    @pytest.mark.asyncio
    async def test_returns_zero_when_extraction_empty(self, gaia_agent):
        """post_turn_extract returns 0 when extraction yields no facts."""
        mock_memory = AsyncMock()
        gaia_agent._memory = mock_memory

        mock_extraction_agent = MagicMock()
        mock_result = MagicMock()
        mock_result.output.facts = []  # extraction succeeded but no facts
        mock_extraction_agent.run = AsyncMock(return_value=mock_result)
        gaia_agent._Gaia__extraction_agent = mock_extraction_agent

        with patch("gaia.core.agent.base.asyncio.sleep", return_value=None):
            result = await gaia_agent.post_turn_extract(
                "test",
                "response",
            )

        assert result == 0
        mock_memory.semantic_insert.assert_not_called()


# ── Conflict detection ──────────────────────────────────────────────────


class TestDetectFactsConflict:
    """Tests for Gaia._detect_facts_conflict()."""

    @pytest.mark.asyncio
    async def test_returns_conflict_when_topic_overlap(self, gaia_agent):
        """_detect_facts_conflict returns existing fact when topics overlap."""
        mock_memory = AsyncMock()
        # Use similar but not identical topics to trigger 0.3-0.75 Jaccard
        mock_memory.semantic_search = AsyncMock(return_value=[
            {
                "fact_id": "existing-123",
                "content": "User prefers TypeScript over JavaScript",
                "category": "preference",
                "confidence": 0.8,
            }
        ])
        gaia_agent._memory = mock_memory

        result = await gaia_agent._detect_facts_conflict(
            "User prefers Java over TypeScript"
        )

        assert result is not None
        assert result["fact_id"] == "existing-123"
        assert 0.3 < result["jaccard"] < 0.75, (
            f"Jaccard {result['jaccard']:.2f} outside expected conflict range"
        )

    @pytest.mark.asyncio
    async def test_returns_none_when_no_overlap(self, gaia_agent):
        """_detect_facts_conflict returns None when topics don't overlap."""
        mock_memory = AsyncMock()
        mock_memory.semantic_search = AsyncMock(return_value=[])
        gaia_agent._memory = mock_memory

        result = await gaia_agent._detect_facts_conflict(
            "User enjoys hiking on weekends"
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_memory(self, gaia_agent):
        """_detect_facts_conflict returns None when _memory is None."""
        gaia_agent._memory = None

        result = await gaia_agent._detect_facts_conflict(
            "some fact"
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_very_short_facts(self, gaia_agent):
        """_detect_facts_conflict returns None for facts with fewer than 3 tokens."""
        mock_memory = AsyncMock()
        mock_memory.semantic_search = AsyncMock(return_value=[])
        gaia_agent._memory = mock_memory

        result = await gaia_agent._detect_facts_conflict("foo bar")

        assert result is None


class TestPostTurnExtractConflict:
    """Tests for post_turn_extract with conflicting facts."""

    @pytest.mark.asyncio
    async def test_saves_conflicting_facts_with_prefix(self, gaia_agent):
        """Conflicting facts are saved with [CONFLICT] prefix and confidence 0.5."""
        mock_memory = AsyncMock()
        # First call: conflict detected
        # Second call: not a duplicate (different content)
        mock_memory.semantic_similarity_search = AsyncMock(return_value=False)
        mock_memory.semantic_search = AsyncMock(return_value=[
            {
                "fact_id": "fact-1",
                "content": "User prefers Coffee",
                "category": "preference",
            }
        ])
        gaia_agent._memory = mock_memory

        mock_extraction_agent = MagicMock()
        mock_result = MagicMock()
        mock_result.output.facts = [
            MagicMock(text="User prefers Tea", confidence=0.8),
        ]
        mock_extraction_agent.run = AsyncMock(return_value=mock_result)
        gaia_agent._Gaia__extraction_agent = mock_extraction_agent

        with (
            patch("gaia.core.agent.base.asyncio.sleep", return_value=None),
            patch("gaia.core.agent.base.Gaia._classify_fact", return_value="preference"),
        ):
            saved = await gaia_agent.post_turn_extract(
                "I switched from Coffee to Tea",
                "Got it, Tea it is.",
            )

        # The conflict call should be saved with [CONFLICT] prefix
        calls = mock_memory.semantic_insert.call_args_list
        assert len(calls) == 1
        assert "[CONFLICT]" in calls[0].kwargs["content"]
        assert calls[0].kwargs["confidence"] == 0.5
        assert calls[0].kwargs["source_id"] == "fact-1"


# ── ainteract inline extraction ──────────────────────────────────────────


class TestAinteractInlineExtraction:
    """Tests for inline fact extraction in ainteract()."""

    @pytest.mark.asyncio
    async def test_calls_inline_extraction_after_turn(self, gaia_agent):
        """ainteract calls post_turn_extract after receiving a response."""
        mock_memory = AsyncMock()
        gaia_agent._memory = mock_memory

        mock_extraction_agent = MagicMock()
        mock_result = MagicMock()
        mock_result.output.facts = []
        mock_extraction_agent.run = AsyncMock(return_value=mock_result)
        gaia_agent._Gaia__extraction_agent = mock_extraction_agent
        gaia_agent._Gaia__message_hist = [
            {"role": "user", "content": "hello"},
        ]

        # Patch post_turn_extract on the class to verify it gets called
        with (
            patch("gaia.core.agent.base.asyncio.sleep", return_value=None),
            patch.object(
                type(gaia_agent),
                "post_turn_extract",
                new=AsyncMock(return_value=0),
            ) as mock_extract,
        ):
            tokens = []
            async for token in gaia_agent.ainteract("hello there"):
                tokens.append(token)

        # post_turn_extract should have been called after the turn
        mock_extract.assert_awaited_once()


# ── reconfigure with system_prompt ──────────────────────────────────────


class TestReconfigure:
    """Tests for Gaia.reconfigure()."""

    def test_reconfigure_accepts_system_prompt(self, gaia_agent):
        """reconfigure accepts system_prompt parameter."""
        old_prompt = gaia_agent._system_prompt

        gaia_agent.reconfigure(system_prompt="New system prompt text")

        assert gaia_agent._system_prompt == "New system prompt text"
        assert gaia_agent._system_prompt != old_prompt

    def test_reconfigure_preserves_other_fields(self, gaia_agent):
        """reconfigure with only system_prompt doesn't change model/endpoint."""
        old_model = gaia_agent._model_name
        old_endpoint = gaia_agent._endpoint

        gaia_agent.reconfigure(system_prompt="new prompt")

        assert gaia_agent._model_name == old_model
        assert gaia_agent._endpoint == old_endpoint

    def test_reconfigure_empty_values_skip(self, gaia_agent):
        """reconfigure ignores empty string values."""
        old_model = gaia_agent._model_name
        old_endpoint = gaia_agent._endpoint

        gaia_agent.reconfigure(model_name="", endpoint="")

        assert gaia_agent._model_name == old_model
        assert gaia_agent._endpoint == old_endpoint

    def test_reconfigure_clears_message_history(self, gaia_agent):
        """reconfigure() unconditionally clears message history."""
        gaia_agent._Gaia__message_hist = [{"role": "user", "content": "hello"}]
        assert len(gaia_agent._Gaia__message_hist) == 1

        gaia_agent.reconfigure(model_name="different")

        # reconfigure resets __message_hist = [] regardless of what changed
        assert gaia_agent._Gaia__message_hist == []


# ── Classification ──────────────────────────────────────────────────────


class TestClassifyFact:
    """Tests for Gaia._classify_fact()."""

    def test_classifies_preference(self):
        """Facts with preference keywords are classified as 'preference'."""
        from gaia.core.agent.base import Gaia

        # "likes" triggers preference
        assert Gaia._classify_fact("User likes Python") == "preference"
        # "dislikes" triggers preference
        assert Gaia._classify_fact("User dislikes coffee") == "preference"
        # "city" triggers preference
        assert Gaia._classify_fact("User lives in NYC") == "preference"
        # "name" triggers preference
        assert Gaia._classify_fact("User name is John") == "preference"

    def test_classifies_project(self):
        """Facts with project keywords are classified as 'project'."""
        from gaia.core.agent.base import Gaia

        assert Gaia._classify_fact("User is building a web app") == "project"
        assert Gaia._classify_fact("User fixed a Docker bug") == "project"
        assert Gaia._classify_fact("User deployed to Kubernetes") == "project"

    def test_classifies_general(self):
        """Facts with no keywords are classified as 'general'."""
        from gaia.core.agent.base import Gaia

        assert Gaia._classify_fact("User says hello") == "general"
        assert Gaia._classify_fact("User enjoys music") == "general"
