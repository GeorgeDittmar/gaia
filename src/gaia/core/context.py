"""Bounded context window with rollup for conversation history."""

from __future__ import annotations

import json
import logging
import math

logger = logging.getLogger("gaia.context")

_DEFAULT_MAX_TOKENS = 4000
_DEFAULT_MIN_AGE_TURNS = 8


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~1 token per 4 characters."""
    return max(1, len(text) // 4)


class ContextWindow:
    """Token-budgeted wrapper around the LLM message history.

    When adding a message would exceed the budget, the oldest half of
    the conversation can be rolled into a single summary message.
    """

    def __init__(
        self,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        min_age_turns: int = _DEFAULT_MIN_AGE_TURNS,
    ) -> None:
        self._max_tokens = max_tokens
        self._min_age_turns = min_age_turns
        self._turn_count: int = 0
        self._tokens: int = 0

    @property
    def current_estimate(self) -> int:
        """Current token estimate of the window contents."""
        return self._tokens

    @property
    def turn_count(self) -> int:
        """Number of turns added to the window."""
        return self._turn_count

    def add(self, message: dict) -> bool:
        """Add a message to the window and update the token budget.

        Args:
            message: A message dict (role, content).

        Returns:
            True if the message was added within budget, False if overflow.
        """
        token_count = estimate_tokens(json.dumps(message))
        self._tokens += token_count
        self._turn_count += 1
        return self._tokens <= self._max_tokens

    def should_rollup(self) -> bool:
        """Return True if the window has exceeded its budget and has enough turns."""
        return (
            self._tokens > self._max_tokens
            and self._turn_count >= self._min_age_turns
        )

    def remove(self, messages: list[dict]) -> None:
        """Remove messages from the window and update the token budget."""
        for msg in messages:
            self._tokens -= estimate_tokens(json.dumps(msg))
            self._turn_count -= 1
        if self._turn_count < 0:
            self._turn_count = 0
        if self._tokens < 0:
            self._tokens = 0

    def sync(self, messages: list[dict]) -> None:
        """Reset token estimate and turn count based on the full message list."""
        self._tokens = sum(estimate_tokens(json.dumps(m)) for m in messages)
        self._turn_count = len(messages)

    def oldest_half(self, messages: list[dict]) -> list[dict]:
        """Return the oldest half of *messages* (at least 2 turns worth)."""
        n = max(2, math.ceil(len(messages) / 2))
        return messages[:n]
