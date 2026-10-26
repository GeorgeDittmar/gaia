"""Tests for the batch-only fact-extraction policy (Issue #5).

Extraction used to fire in three overlapping places — inline after every
turn in ``Gaia.ainteract``, periodically every N turns, and "at shutdown"
(a no-op race). Issue #5 consolidates it to a single batch path: the
periodic extractor that re-processes episodic history via
``extract_from_history``.

These tests assert the two halves of that policy:

* ``ainteract`` no longer extracts inline (even when a memory store is
  present) — using ``_make_mock_run_result`` so the streaming result is a
  real mock.
* ``extract_from_history`` still drives the extraction agent, so the
  periodic path keeps working.
* The app's ``run_agent_execution`` schedules that periodic extraction
  once every N turns (every 3 by default), and only then.
"""

import asyncio
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from conftest import _make_mock_run_result
from gaia.config import Settings


# ── Gaia-agent level (inline removed, periodic works) ───────────────────


@pytest.fixture()
def counter_gaia() -> tuple:
    """Build a Gaia agent whose extraction agent counts how often it runs.

    Returns ``(agent, calls)`` where *calls* is a list that receives one
    entry each time the extraction agent's ``run`` coroutine is invoked.
    """
    mock_core_agent = MagicMock()
    mock_core_agent.run_stream = MagicMock(
        return_value=_make_mock_run_result(["hello world"])
    )

    calls: list[str] = []

    async def _counted_run(message: str):
        calls.append(message)
        result = MagicMock()
        result.output.facts = []  # no facts -> nothing saved
        return result

    mock_extraction_agent = MagicMock()
    mock_extraction_agent.run = _counted_run

    with (
        patch("gaia.core.agent.base.OpenAIChatModel", return_value=MagicMock()),
        patch("gaia.core.agent.base.OpenAIProvider"),
        patch("gaia.core.agent.base.Agent", return_value=mock_core_agent),
    ):
        from gaia.core.agent.base import Gaia

        agent = Gaia("You are a helpful assistant.")
        agent._Gaia__extraction_agent = mock_extraction_agent
        agent._Gaia__message_hist = [
            {"role": "user", "content": "hi"},
        ]
        return agent, calls


@pytest.mark.asyncio
async def test_acteract_does_not_inline_extract(counter_gaia) -> None:
    """Interacting a few turns must NOT call the extraction agent inline.

    Issue #5: inline extraction was removed. The periodic extractor owns
    extraction now.
    """
    agent, calls = counter_gaia
    agent._memory = AsyncMock()

    tokens: list[str] = []
    async for token in agent.ainteract("hello there"):
        tokens.append(token)

    assert tokens == ["hello world"]
    assert calls == [], "inline extraction must not fire on a completed turn"

    # Drive a few more turns — still no inline extraction.
    async for _ in agent.ainteract("again"):
        pass
    async for _ in agent.ainteract("and again"):
        pass

    assert calls == [], "inline extraction must not fire repeatedly either"


@pytest.mark.asyncio
async def test_extract_from_history_fires_once_per_turn(counter_gaia) -> None:
    """Periodic extraction (extract_from_history) extracts one pass per turn."""
    agent, calls = counter_gaia
    agent._memory = AsyncMock()
    agent._memory.episodic_search = AsyncMock(
        return_value=[
            {"payload": {"user_prompt": "p1", "agent_response": "r1"}},
            {"payload": {"user_prompt": "p2", "agent_response": "r2"}},
            {"payload": {"user_prompt": "p3", "agent_response": "r3"}},
        ]
    )

    saved = await agent.extract_from_history(max_turns=10)

    assert len(calls) == 3, "one extraction agent call per episodic turn"
    assert saved == 0  # the counter returns empty facts, so nothing saved


@pytest.mark.asyncio
async def test_extract_from_history_no_memory(counter_gaia) -> None:
    """extract_from_history returns 0 when there is no memory store."""
    agent, calls = counter_gaia
    agent._memory = None

    saved = await agent.extract_from_history(max_turns=10)

    assert saved == 0
    assert calls == []


# ── App-level: periodic trigger fires every N turns ─────────────────────


@pytest.fixture()
def app_with_core() -> tuple:
    """A GaiaTUIApp with a mocked core agent whose extract_from_history counts."""
    with (
        patch("gaia.gaia_tui_app.load_config", return_value=Settings()),
        patch("gaia.gaia_tui_app.MemoryFactory.create", return_value=None),
        patch("gaia.gaia_tui_app.run_agent_stream", new=AsyncMock()),
    ):
        from gaia.gaia_tui_app import GaiaTUIApp

        app = GaiaTUIApp()
        core = MagicMock()
        core.extract_from_history = AsyncMock(return_value=0)
        app._GaiaTUIApp__core_agent = core
        app._memory = MagicMock()

        # Route the periodic worker through the (mocked) core agent.
        async def _fake_periodic_extraction() -> int:
            return await core.extract_from_history(max_turns=10)

        app._run_periodic_extraction = _fake_periodic_extraction
        yield app, core


def _drive_run_agent_execution(app: "GaiaTUIApp", prompt: str) -> None:
    """Invoke run_agent_execution's real body (bypassing @work) and let any
    scheduled periodic callback run to completion.

    ``run_agent_execution`` is a Textual ``@work`` task and schedules the
    periodic extractor via ``asyncio.get_event_loop().call_later(5, ...)``;
    we patch ``get_event_loop`` so ``call_later`` schedules a real task.
    """

    def _fake_call_later(delay: float, cb) -> asyncio.Task:
        return asyncio.ensure_future(cb())

    async def _run_one() -> None:
        with patch("asyncio.get_event_loop") as mock_loop:
            mock_loop.return_value.call_later = _fake_call_later
            # @work-wrapped; call the underlying coroutine directly.
            await app.run_agent_execution.__wrapped__(app, prompt)
            # Let the scheduled periodic callback finish.
            await asyncio.sleep(0)

    asyncio.run(_run_one())


def test_run_agent_execution_triggers_periodic_every_n_turns(
    app_with_core,
) -> None:
    """Periodic extraction is scheduled only when turn % N == 0."""
    app, core = app_with_core

    for turn in range(4):
        app._turn_count = turn
        _drive_run_agent_execution(app, "hi")

    # _EXTRACT_EVERY_N_TURNS == 3, so turns 0 and 3 trigger extraction.
    assert core.extract_from_history.await_count == 2


def test_run_agent_execution_skips_non_multiples(app_with_core) -> None:
    """Turns not divisible by N do not schedule extraction."""
    app, core = app_with_core

    for turn in (1, 2, 4, 5):
        app._turn_count = turn
        _drive_run_agent_execution(app, "hi")

    assert core.extract_from_history.await_count == 0
