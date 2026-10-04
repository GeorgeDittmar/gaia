"""Agent harness for G.A.I.A.

Wraps a pydantic-ai agent with memory-aware prompt assembly and
episodic logging so every conversation turn is persisted.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from openai import AsyncOpenAI
from pydantic_ai.agent import Agent
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

    # ── Post-turn fact extraction ──────────────────────────────────────

    async def post_turn_extract(self, user_prompt: str, agent_response: str) -> int:
        """Extract facts from a completed conversation turn and save to semantic memory.

        Makes a lightweight LLM call to extract facts as JSON, auto-classifies them,
        and saves them. Returns the number of facts saved. Never raises — failures are
        silently ignored.

        Args:
            user_prompt: The user's message this turn.
            agent_response: The agent's full response this turn.

        Returns:
            Number of facts saved to semantic memory.
        """
        if self._memory is None:
            return 0

        try:
            facts = await self._extract_facts_json(user_prompt, agent_response)
            if not facts:
                return 0

            saved = 0
            for fact_text, category, confidence in facts:
                await self._memory.semantic_insert(
                    content=fact_text,
                    category=category,
                    confidence=confidence,
                    source="auto_extract",
                )
                saved += 1
            return saved
        except Exception:
            return 0

    async def _extract_facts_json(
        self, user_prompt: str, agent_response: str
    ) -> list[tuple[str, str, float]]:
        """Call the LLM to extract facts as a JSON array.

        The LLM response should be a JSON array of objects:
        [{"text": "...", "confidence": 0.9}, ...]

        Returns:
            List of (fact_text, category, confidence) tuples.
        """
        prompt = (
            "Extract any factual statements about the user from this conversation.\n"
            "Respond with ONLY a JSON array. No explanations, no markdown, no code blocks.\n"
            "Each entry must have 'text' (the fact as a complete sentence) and "
            "'confidence' (a float 0.0–1.0).\n\n"
            "Example:\n"
            '[{"text": "George lives in Portland, Oregon.", "confidence": 0.9}]\n\n'
            "Conversation:\n"
            f"User: {user_prompt[:500]}\n"
            f"Agent: {agent_response[:500]}"
        )

        # Brief delay to avoid competing with the main streaming API call
        await asyncio.sleep(2)

        client = AsyncOpenAI(
            base_url=self.DEFAULT_ENDPOINT,
            api_key="not-needed",
            timeout=60.0,
        )

        resp = await client.chat.completions.create(
            model="local-model",
            messages=[
                {
                    "role": "system",
                    "content": "You are a fact extraction engine. Output ONLY valid JSON arrays.",
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=512,
            temperature=0.0,
        )

        raw = resp.choices[0].message.content or ""
        parsed = self._parse_facts_json(raw)
        return [
            (p["text"], self._classify_fact(p["text"]), p.get("confidence", 0.8))
            for p in parsed
            if p.get("text")
        ]

    @staticmethod
    def _parse_facts_json(raw: str) -> list[dict]:
        """Parse a JSON array from the LLM response.

        Strips markdown code fences if present. Returns an empty list on failure.
        """
        import json

        text = raw.strip()
        # Strip ```json ... ``` or ``` ... ``` fences
        if text.startswith("```"):
            lines = text.splitlines()
            # Remove first line (```json) and last line (```)
            if len(lines) >= 2 and lines[-1].strip() == "```":
                lines = lines[1:-1]
            elif len(lines) > 1:
                lines = lines[1:]
            text = "\n".join(lines).strip()

        try:
            data = json.loads(text)
            if isinstance(data, list):
                return data
            return []
        except (json.JSONDecodeError, ValueError):
            return []

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
            payload = entry.get("payload", {})
            user_prompt = payload.get("user_prompt", "")
            agent_response = payload.get("agent_response", "")
            if user_prompt and agent_response:
                saved = await self.post_turn_extract(user_prompt, agent_response)
                total_saved += saved

        return total_saved
