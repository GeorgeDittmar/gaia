"""Agent harness for G.A.I.A.

Wraps a pydantic-ai agent with memory-aware prompt assembly and
episodic logging so every conversation turn is persisted.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry
from pydantic_ai.agent import RunContext
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

if TYPE_CHECKING:
    from gaia.core.memory.base import MemoryStore

# Auto-classification keywords for extracted facts
_PREFERENCE_KEYWORDS = frozenset([
    "name", "location", "lives", "born", "from", "job", "role",
    "title", "prefers", "likes", "hates", "dislikes", "city",
    "state", "country", "age", "gender", "pronoun", "email",
])
_PROJECT_KEYWORDS = frozenset([
    "project", "repo", "code", "bug", "feature", "task",
    "working on", "building", "coding", "pr", "debug", "issue",
    "github", "gitlab", "branch", "deploy", "pipeline", "ci",
    "cd", "docker", "kubernetes", "infra", "infrastructure",
])

logger = logging.getLogger("gaia.extract")


def _generate_conversation_id() -> str:
    """Return a short UUID for grouping conversation turns."""
    return str(uuid.uuid4())[:12]


# ── Structured output types ──────────────────────────────────────────

class FactEntry(BaseModel):
    """A single fact extracted from conversation context."""
    text: str = Field(description="The fact as a complete declarative sentence")
    confidence: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="How confident we are this is a real fact (0.0-1.0)",
    )


class FactResult(BaseModel):
    """Structured LLM result: a list of extracted facts."""
    facts: list[FactEntry]


# ── Extraction agent (reusable, structured output) ─────────────────

def _make_extraction_agent() -> Agent[FactResult]:
    """Build a dedicated pydantic-ai agent with structured output for fact extraction.

    Uses pydantic-ai's result_type so the LLM is **forced** to return a FactResult.
    If the model returns malformed output, pydantic-ai will retry automatically
    with a ModelRetry instruction.
    """
    model = OpenAIChatModel(
        "local-model",
        provider=OpenAIProvider(
            base_url="http://localhost:8080/v1",
            api_key="not-needed",
        ),
    )
    return Agent(
        model,
        instructions=(
            "You extract factual statements about the USER from the conversation below.\n"
            "Return ONLY facts the user has stated or strongly implied about themselves:\n"
            "  - Preferences (what they like/dislike)\n"
            "  - Personal info (name, location, job, etc.)\n"
            "  - Project details (what they're building, tech stack, bugs)\n\n"
            "Rules:\n"
            "  - Each fact must be a complete declarative sentence about the user\n"
            "  - Skip greetings, pleasantries, or conversational filler\n"
            "  - Skip facts that are obvious or generic\n"
            "  - If nothing extractable is found, return an empty facts list\n"
            "  - Set confidence high (0.8-1.0) for explicit statements, lower (0.5-0.7) for inferred\n"
        ),
        output_type=FactResult,
    )


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
        # Cached extraction agent (same model, structured output)
        self.__extraction_agent = _make_extraction_agent()

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

    # ── Post-turn fact extraction ──────────────────────────────────────

    async def post_turn_extract(
        self, user_prompt: str, agent_response: str
    ) -> int:
        """Extract facts from a completed conversation turn and save to semantic memory.

        Uses a dedicated pydantic-ai agent with structured output (result_type=FactResult)
        so the LLM is contractually required to return a valid FactResult.

        Returns the number of facts actually saved. Never raises.
        """
        if self._memory is None:
            logger.info("SKIPPED — no memory store")
            return 0

        logger.info("start — prompt=%s", user_prompt[:80])
        try:
            facts = await self._extract_facts(user_prompt, agent_response)
            logger.info("extracted %d facts", len(facts))
            if not facts:
                return 0

            saved = 0
            for fact_text, category, confidence in facts:
                try:
                    await self._memory.semantic_insert(
                        content=fact_text,
                        category=category,
                        confidence=confidence,
                        source="auto_extract",
                    )
                    saved += 1
                    logger.info("saved fact [%s] %s", category, fact_text[:80])
                except Exception:
                    logger.exception("INSERT FAILED — %s", fact_text[:80])
            logger.info("saved %d/%d facts", saved, len(facts))
            return saved
        except Exception:
            logger.exception("extraction FAILED")
            return 0

    async def _extract_facts(
        self,
        user_prompt: str,
        agent_response: str,
    ) -> list[tuple[str, str, float]]:
        """Run the extraction agent with structured output.

        pydantic-ai's result_type=FactResult forces the LLM to return a
        FactResult object. If the model's raw output doesn't match,
        pydantic-ai raises ModelRetry and retries automatically (up to 3 times).

        Returns list of (fact_text, category, confidence) tuples.
        """
        # Brief delay to avoid competing with the main streaming API call
        await asyncio.sleep(2)

        conversation = (
            f"User: {user_prompt[:800]}\n"
            f"Agent: {agent_response[:800]}"
        )

        logger.debug("calling extraction agent (%d chars of conversation)", len(conversation))
        result = await self.__extraction_agent.run(conversation)
        fact_result: FactResult = result.output

        logger.debug("agent returned %d fact entries", len(fact_result.facts))
        return [
            (f.text, self._classify_fact(f.text), f.confidence)
            for f in fact_result.facts
            if f.text.strip()
        ]

    @staticmethod
    def _classify_fact(fact_text: str) -> str:
        """Auto-classify a fact as 'preference', 'project', or 'general'."""
        lower = fact_text.lower()
        for kw in _PROJECT_KEYWORDS:
            if kw in lower:
                return "project"
        for kw in _PREFERENCE_KEYWORDS:
            if kw in lower:
                return "preference"
        return "general"

    async def extract_from_history(self, max_turns: int = 10) -> int:
        """Re-process the last N conversation turns from episodic memory.

        Queries episodic memory for the most recent turns, extracts facts from
        user_prompt + agent_response pairs, and saves them.

        Args:
            max_turns: Maximum number of conversation turns to process.

        Returns:
            Total number of facts saved across all processed turns.
        """
        if self._memory is None:
            return 0

        try:
            results = await self._memory.episodic_search(
                query="conversation turn",
                limit=max_turns,
            )
        except Exception:
            return 0

        total_saved = 0
        for entry in results:
            raw_payload = entry.get("payload", {})
            if isinstance(raw_payload, str):
                try:
                    payload = json.loads(raw_payload)
                except (json.JSONDecodeError, ValueError):
                    payload = {}
            else:
                payload = raw_payload
            user_prompt = payload.get("user_prompt", "")
            agent_response = payload.get("agent_response", "")
            if user_prompt and agent_response:
                saved = await self.post_turn_extract(user_prompt, agent_response)
                total_saved += saved

        return total_saved
