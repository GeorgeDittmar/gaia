"""Tests for the bounded context window (src/gaia/core/context.py)."""

import json

import pytest

from gaia.core.context import ContextWindow, estimate_tokens


# ── estimate_tokens ──────────────────────────────────────────────────

class TestEstimateTokens:
    """Tests for the _estimate_tokens helper."""

    def test_estimates_nonzero_for_short_text(self):
        """Short text should return at least 1 token."""
        assert estimate_tokens("Hi") == 1

    def test_estimates_proportionally(self):
        """Token estimate scales with text length."""
        short = "Hello world"           # 11 chars → 2 tokens
        long = "Hello world, this is a test with more characters"  # 51 chars → 12 tokens
        assert estimate_tokens(short) < estimate_tokens(long)

    def test_specific_length(self):
        """len('A'*80) = 80 → 80//4 = 20 tokens."""
        assert estimate_tokens("A" * 80) == 20


# ── ContextWindow budget ─────────────────────────────────────────────

class TestContextWindowBudget:
    """Tests for budget tracking and rollup triggering."""

    def test_add_within_budget(self):
        """Messages under the budget should not trigger rollup."""
        cw = ContextWindow(max_tokens=2000)
        for i in range(50):
            cw.add({"role": "user", "content": f"Message {i}"})
            cw.add({"role": "assistant", "content": f"Response {i}"})
        assert not cw.should_rollup()

    def test_add_exceeds_budget(self):
        """Tiny budget should trigger rollup after enough turns."""
        cw = ContextWindow(max_tokens=10, min_age_turns=2)
        cw.add({"role": "user", "content": "This is a message with some content"})
        cw.add({"role": "assistant", "content": "This is a response with some content"})
        assert cw.should_rollup()

    def test_turn_count_increments(self):
        """Each add() call should increment the turn counter."""
        cw = ContextWindow()
        assert cw.turn_count == 0
        cw.add({"role": "user", "content": "hello"})
        assert cw.turn_count == 1
        cw.add({"role": "assistant", "content": "hi"})
        assert cw.turn_count == 2

    def test_current_estimate_returns_tokens(self):
        """current_estimate should reflect the tracked token count."""
        cw = ContextWindow()
        assert cw.current_estimate == 0
        cw.add({"role": "user", "content": "A" * 40})
        assert cw.current_estimate == 17  # json.dumps adds overhead: 71 // 4 = 17


# ── ContextWindow rollup helpers ─────────────────────────────────────

class TestContextWindowRollup:
    """Tests for oldest_half and should_rollup interaction."""

    def test_oldest_half_returns_first_half(self):
        """Half of 8 messages should be the first 4."""
        cw = ContextWindow()
        messages = [{"role": "user", "content": str(i)} for i in range(8)]
        result = cw.oldest_half(messages)
        assert len(result) == 4
        assert result == messages[:4]

    def test_oldest_half_minimum_two(self):
        """With 3 messages, oldest_half should return at least 2."""
        cw = ContextWindow()
        messages = [{"role": "user", "content": str(i)} for i in range(3)]
        result = cw.oldest_half(messages)
        assert len(result) == 2
        assert result == messages[:2]

    def test_oldest_half_with_even_list(self):
        """With 4 messages, oldest_half should return 2."""
        cw = ContextWindow()
        messages = [{"role": "user", "content": str(i)} for i in range(4)]
        result = cw.oldest_half(messages)
        assert len(result) == 2

    def test_should_rollup_requires_min_age(self):
        """should_rollup is False when under min_age_turns, even if over budget."""
        cw = ContextWindow(max_tokens=10, min_age_turns=8)
        cw.add({"role": "user", "content": "A" * 50})
        cw.add({"role": "assistant", "content": "B" * 50})
        # Over budget but only 2 turns, min is 8
        assert not cw.should_rollup()


# ── Integration ──────────────────────────────────────────────────────

class TestContextWindowIntegration:
    """Integration: context window triggers rollup after enough turns."""

    def test_rollup_workflow(self):
        """After enough turns, should_rollup triggers and oldest messages are replaced."""
        # Feed enough turns to exceed a tiny budget and hit min_age_turns
        tiny_window = ContextWindow(max_tokens=5, min_age_turns=4)

        # Simulate 6 user/assistant turns (12 total adds)
        for i in range(6):
            user_msg = f"User message number {i} with enough content to consume tokens"
            assistant_msg = f"Assistant response number {i} with content"
            tiny_window.add({"role": "user", "content": user_msg})
            tiny_window.add({"role": "assistant", "content": assistant_msg})

        assert tiny_window.should_rollup()
        # oldest_half of 12 messages = 6
        rollup_msgs = tiny_window.oldest_half(
            [{"role": "user", "content": f"msg{i}"} for i in range(12)]
        )
        assert len(rollup_msgs) == 6

    def test_remove_subtracts_tokens(self):
        """remove() actually subtracts per-message token estimates."""
        cw = ContextWindow()
        cw.add({"role": "user", "content": "A" * 100})
        cw.add({"role": "assistant", "content": "B" * 100})
        before = cw._tokens
        cw.remove([{"role": "user", "content": "A" * 100}])
        # Tokens should have decreased by roughly 25 (100 // 4)
        assert cw._tokens == before - estimate_tokens(json.dumps({"role": "user", "content": "A" * 100}))

    def test_remove_does_not_underflow(self):
        """remove() clamps tokens and turn_count to zero."""
        cw = ContextWindow()
        cw.remove([{"role": "user", "content": "X"}])
        assert cw.turn_count == 0
        assert cw._tokens == 0

    def test_remove_clamps_on_overflow_removal(self):
        """remove() does not go negative when removing more than added."""
        cw = ContextWindow()
        cw.add({"role": "user", "content": "short"})
        cw.remove([
            {"role": "user", "content": "a" * 1000},
            {"role": "assistant", "content": "b" * 1000},
        ])
        assert cw.turn_count == 0
        assert cw._tokens == 0

    def test_sync_resets_estimate_from_messages(self):
        """sync() rebuilds _tokens and _turn_count from the full message list."""
        cw = ContextWindow()
        cw.add({"role": "user", "content": "A" * 100})
        cw.add({"role": "assistant", "content": "B" * 100})
        cw.add({"role": "user", "content": "C" * 100})
        before_tokens = cw._tokens
        cw.sync([{"role": "system", "content": "hello"}])
        assert cw.turn_count == 1
        assert cw._tokens != before_tokens  # fully rebuilt from scratch
        # The new estimate should match one short message
        assert cw._tokens == estimate_tokens(json.dumps({"role": "system", "content": "hello"}))
