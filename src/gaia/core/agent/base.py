"""Agent harness for G.A.I.A.

Wraps a pydantic-ai agent with memory-aware prompt assembly and
episodic logging so every conversation turn is persisted.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from openai import AsyncOpenAI
from pydantic_ai.agent import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

if TYPE_CHECKING:
    from gaia.core.memory.base import MemoryStore


def _generate_conversation_id() -> str:
    """Return a short UUID for grouping conversation turns."""
    return str(uuid.uuid4())[:12]


class Gaia:
    """Memory-aware agent harness.

    Before each user message, the agent queries semantic memory for
    relevant facts and injects them into the prompt. After responding,
    both the user message and agent response are recorded as episodic
    memory.

    Args:
        system_prompt: Instructions that shape the agent's behaviour.
        model_name: Optional override for the model identifier (default
            ``'local-model'``).
        memory: Optional :class:`MemoryStore` — if provided, semantic
            retrieval and episodic logging are enabled.
    """

    # Hard-coded defaults — these should eventually read from settings
    DEFAULT_MODEL = "local-model"
    DEFAULT_ENDPOINT = "http://localhost:8080/v1"

    def __init__(
        self,
        system_prompt: str,
        model_name: str = "",
        memory: MemoryStore | None = None,
    ) -> None:
        model = OpenAIChatModel(
            model_name or self.DEFAULT_MODEL,
            provider=OpenAIProvider(
                base_url=self.DEFAULT_ENDPOINT,
                api_key="not-needed",
            ),
        )
        self.__core_agent = Agent(model, instructions=system_prompt)
        self.__message_hist: list[dict] = []
        self._memory = memory
        self.__conv_id = _generate_conversation_id()

    # ── Public API ─────────────────────────────────────────────────

    async def ainteract(self, prompt: str) -> AsyncGenerator[str, None]:
        """Interact with the agent in a single turn.

        1. Query semantic memory for relevant facts.
        2. Assemble an augmented prompt.
        3. Stream the model response.
        4. Record the turn as episodic memory.

        Yields:
            Text deltas from the model.
        """
        # 1. Retrieve relevant semantic facts
        context_blocks: list[str] = []
        if self._memory is not None:
            facts = await self._memory.semantic_search(prompt, limit=5)
            for fact in facts:
                content = fact.get("content", "")
                confidence = fact.get("confidence", 1.0)
                if content and confidence >= 0.5:
                    context_blocks.append(f"- {content}")

        # 2. Build augmented prompt
        if context_blocks:
            augmented_prompt = (
                "[Relevant stored facts]\n"
                + "\n".join(context_blocks)
                + "\n\n"
                f"User: {prompt}\n"
                "Please use the stored facts above when relevant."
            )
        else:
            augmented_prompt = prompt

        # 3. Stream response
        response_chunks: list[str] = []
        try:
            async with self.__core_agent.run_stream(
                augmented_prompt, message_history=self.__message_hist
            ) as result:
                async for chunk in result.stream_text(delta=True):
                    response_chunks.append(chunk)
                    yield chunk

                self.__message_hist = result.all_messages()
        except Exception:
            # If streaming fails, still record what we have
            response_text = "".join(response_chunks)
        else:
            response_text = "".join(response_chunks)

        # 4. Record episodic memory
        if self._memory is not None and response_text:
            try:
                await self._memory.episodic_record(
                    "conversation_turn",
                    self.__conv_id,
                    summary=f"User: {prompt[:80]}",
                    payload={
                        "user_prompt": prompt,
                        "agent_response": response_text,
                        "context_facts_used": len(context_blocks),
                    },
                )
            except Exception:
                # Memory recording is best-effort; never block the response
                pass
