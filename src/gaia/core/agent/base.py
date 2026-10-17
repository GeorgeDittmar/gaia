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

from pydantic import BaseModel, Field
from pydantic_ai import Agent
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


class PlannerResult(BaseModel):
    """Planner decision: whether to retrieve from memory, what to search, and observations."""

    retrieve: bool = Field(
        description=(
            "Whether this query benefits from looking up user memory. "
            "Use false for simple queries (greetings, one-word, obvious answers)."
        ),
    )
    queries: list[str] = Field(
        default_factory=list,
        description=(
            "Search phrases for memory retrieval. 1-3 short phrases, "
            "or empty list if retrieve=False."
        ),
    )
    note: str = Field(
        default="",
        description=(
            "A new observation about the user to save for later. "
            "Leave empty if nothing noteworthy."
        ),
    )


# ── Extraction agent (reusable, structured output) ─────────────────

class Gaia:
    """Memory-aware agent harness.

    Before each user message, a lightweight planner decides whether to
    retrieve from semantic memory and what to search for. Retrieved facts
    are injected into the prompt. After responding, both the user message
    and agent response are recorded as episodic memory. The planner also
    saves observations about the user to episodic memory when noteworthy.

    Args:
        system_prompt: Instructions that shape the agent's behaviour.
        model_name: Model identifier passed to the LLM provider.
        endpoint: Base URL of the OpenAI-compatible API endpoint.
        api_key: API key for the endpoint (default ``'not-needed'``).
        memory: Optional :class:`MemoryStore` — if provided, semantic
            retrieval and episodic logging are enabled.
    """

    def __init__(
        self,
        system_prompt: str,
        model_name: str = "local-model",
        endpoint: str = "http://localhost:8080/v1",
        api_key: str = "not-needed",
        memory: MemoryStore | None = None,
    ) -> None:
        self._system_prompt = system_prompt
        self._model_name = model_name
        self._endpoint = endpoint
        self._api_key = api_key

        model = OpenAIChatModel(
            model_name,
            provider=OpenAIProvider(
                base_url=endpoint,
                api_key=api_key,
            ),
        )
        self.__core_agent = Agent(model, instructions=system_prompt)
        self.__message_hist: list[dict] = []
        self._memory = memory
        self.__conv_id = _generate_conversation_id()
        self.__extraction_agent = self._make_extraction_agent()
        self.__planner_agent = self._make_planner_agent()

    def _make_planner_agent(self) -> Agent[PlannerResult]:
        """Build a lightweight planning agent with structured output.

        Decides whether the current query benefits from memory retrieval,
        what to search for, and whether to note anything new.
        """
        model = OpenAIChatModel(
            self._model_name,
            provider=OpenAIProvider(
                base_url=self._endpoint,
                api_key=self._api_key,
            ),
        )
        return Agent(
            model,
            instructions=(
                "You are a memory-aware planning assistant. "
                "Given the user's message and recent conversation context, "
                "decide whether to look up relevant facts from the user's memory.\n\n"
                "Rules:\n"
                "  - retrieve: true for questions about tools, preferences, recommendations,\n"
                "    or anything that benefits from knowing the user. false for greetings,\n"
                "    simple one-word queries, or obvious factual questions.\n"
                "  - queries: 1-3 short search phrases (e.g. 'CLI tool preferences', "
                "'Python tech stack'). Keep them general — they are vector-searched.\n"
                "  - note: save a brief observation about the user if they revealed "
                "something new (preferences, context, tech stack changes). Leave empty "
                "for routine messages.\n"
            ),
            output_type=PlannerResult,
        )

    def _make_extraction_agent(self) -> Agent[FactResult]:
        """Build a dedicated pydantic-ai agent with structured output for fact extraction.

        Uses the same model and endpoint as the main agent.
        """
        model = OpenAIChatModel(
            self._model_name,
            provider=OpenAIProvider(
                base_url=self._endpoint,
                api_key=self._api_key,
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
                "  - If a fact is clearly repeated from earlier in the conversation, skip it\n"
                "  - If nothing extractable is found, return an empty facts list\n"
                "  - Set confidence high (0.8-1.0) for explicit statements, lower (0.5-0.7) for inferred\n"
            ),
            output_type=FactResult,
        )

    def reconfigure(self, model_name: str = "", endpoint: str = "", api_key: str = "") -> None:
        """Swap the underlying model and/or endpoint.

        Only non-empty values are applied, so callers can pass
        ``self.reconfigure(model_name=new_model)`` without
        worrying about clobbering the endpoint.

        The main agent, extraction agent, and planner agent are rebuilt
        with the new configuration.
        """
        if model_name:
            self._model_name = model_name
        if endpoint:
            self._endpoint = endpoint
        if api_key:
            self._api_key = api_key

        model = OpenAIChatModel(
            self._model_name,
            provider=OpenAIProvider(
                base_url=self._endpoint,
                api_key=self._api_key,
            ),
        )
        self.__core_agent = Agent(model, instructions=self._system_prompt)
        self.__extraction_agent = self._make_extraction_agent()
        self.__planner_agent = self._make_planner_agent()
        self.__message_hist = []

    # ── Public API ─────────────────────────────────────────────────

    async def ainteract(self, prompt: str) -> AsyncGenerator[str, None]:
        """Interact with the agent in a single turn.

        1. Plan: decide if memory retrieval would help and what to search.
        2. Retrieve relevant semantic facts (if planner says so).
        3. Assemble an augmented prompt.
        4. Stream the model response.
        5. Record the turn as episodic memory.
        6. Save any planner-observed notes to episodic memory.

        Yields:
            Text deltas from the model.
        """
        # 1. Planner — decide whether to retrieve from memory
        context_blocks: list[str] = []
        planner_note: str = ""
        if self._memory is not None and self.__message_hist:
            try:
                recent = self.__message_hist[-10:]
                history_text = ""
                for msg in recent:
                    role = msg.get("role", "unknown")
                    content = msg.get("content", "")
                    history_text += f"{role}: {content[:200]}\n"

                planner_input = (
                    f"User message: {prompt}\n"
                    f"Recent conversation context:\n{history_text}"
                )
                planner_result = await self.__planner_agent.run(planner_input)
                planner_output: PlannerResult = planner_result.output

                if planner_output.retrieve and planner_output.queries:
                    # 2. Retrieve relevant semantic facts for each query
                    for query in planner_output.queries:
                        facts = await self._memory.semantic_search(query, limit=3)
                        for fact in facts:
                            content = fact.get("content", "")
                            confidence = fact.get("confidence", 1.0)
                            if content and confidence >= 0.5:
                                context_blocks.append(f"- {content}")
                    # Deduplicate by content
                    seen = set()
                    unique_blocks: list[str] = []
                    for block in context_blocks:
                        if block not in seen:
                            seen.add(block)
                            unique_blocks.append(block)
                    context_blocks = unique_blocks

                planner_note = planner_output.note
            except Exception:
                logger.exception(
                    "planner failed — continuing without memory retrieval"
                )

        # 3. Build augmented prompt
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

        # 4. Stream response
        response_chunks: list[str] = []
        response_text = ""
        try:
            async with self.__core_agent.run_stream(
                augmented_prompt, message_history=self.__message_hist
            ) as result:
                async for chunk in result.stream_text(delta=True):
                    response_chunks.append(chunk)
                    yield chunk

                self.__message_hist = result.all_messages()
        except Exception:
            response_text = "".join(response_chunks)
            raise
        else:
            response_text = "".join(response_chunks)

        # 5. Record episodic memory
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
                pass

        # 6. Save planner-observed notes to episodic memory
        if planner_note and self._memory is not None:
            try:
                await self._memory.episodic_record(
                    "planner_note",
                    self.__conv_id,
                    summary=planner_note[:80],
                    payload={"note": planner_note},
                )
            except Exception:
                logger.exception("failed to save planner note")

    # ── Post-turn fact extraction ──────────────────────────────────────

    async def post_turn_extract(
        self, user_prompt: str, agent_response: str
    ) -> int:
        """Extract facts from a completed conversation turn and save to semantic memory.

        Uses a dedicated pydantic-ai agent with structured output (result_type=FactResult)
        so the LLM is contractually required to return a valid FactResult.

        Before saving, checks if a similar fact already exists in semantic memory.
        Skips if the top search result has token overlap > 75% (Jaccard similarity).

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
            skipped = 0
            for fact_text, category, confidence in facts:
                # Dedup check: query memory for similar facts
                if await self._memory.semantic_similarity_search(
                    fact_text,
                    limit=5,
                    distance_threshold=0.75,
                ):
                    skipped += 1
                    logger.info("skipping duplicate — %s", fact_text[:60])
                    continue

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
            logger.info("saved %d/%d facts (%d skipped as duplicates)",
                        saved, len(facts), skipped)
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
