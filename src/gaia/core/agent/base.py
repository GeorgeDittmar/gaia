"""Agent harness for G.A.I.A.

Wraps a pydantic-ai agent with memory-aware prompt assembly and
episodic logging so every conversation turn is persisted.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

if TYPE_CHECKING:
    from gaia.core.memory.base import MemoryStore

from gaia.core.context import ContextWindow, _DEFAULT_MAX_TOKENS, _DEFAULT_MIN_AGE_TURNS

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

_USER_SUMMARY_PATH = Path.home() / ".gaia" / "user_summary.md"


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split markdown text into frontmatter dict and body.

    Returns (metadata_dict, body_string). If no frontmatter found,
    returns ({}, full_text).
    """
    body = text
    metadata: dict[str, str] = {}

    if text.startswith("---"):
        end_marker = text.find("---", 3)
        if end_marker != -1:
            fm_lines = text[3:end_marker].strip().splitlines()
            for line in fm_lines:
                if ":" in line:
                    key, _, value = line.partition(":")
                    metadata[key.strip()] = value.strip().strip('"')
            body = text[end_marker + 3:].lstrip("\n")

    return metadata, body


def _build_frontmatter(metadata: dict[str, str]) -> str:
    """Build a YAML frontmatter block surrounded by --- markers."""
    lines = []
    for key, value in metadata.items():
        lines.append(f"{key}: {value}")
    return "---\n" + "\n".join(lines) + "\n---"


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
    """Planner decision: whether to retrieve from memory, where to search, and observations."""

    retrieve: bool = Field(
        default=False,
        description=(
            "Whether this query benefits from looking up user memory. "
            "Use false for simple queries (greetings, one-word, obvious answers)."
        ),
    )
    source: Literal["semantic", "episodic", "both"] = Field(
        default="semantic",
        description=(
            "Where to look for relevant information. Use 'episodic' for "
            "questions about past conversations, dates, or events. Use "
            "'semantic' for user facts/preferences. Use 'both' if query "
            "might need either or both."
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
        max_context_tokens: int = _DEFAULT_MAX_TOKENS,
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
        self.__message_hist: list[dict] = []
        self._memory = memory
        self._context_window = ContextWindow(
            max_tokens=max_context_tokens,
            min_age_turns=_DEFAULT_MIN_AGE_TURNS,
        )
        self.__conv_id = _generate_conversation_id()
        self.__extraction_agent = self._make_extraction_agent()
        self.__planner_agent = self._make_planner_agent()
        self.__user_summary = self._load_user_summary()
        self.__last_summary_time: datetime | None = None
        full_instructions = self._system_prompt
        if self.__user_summary:
            full_instructions = (
                "USER PROFILE\n"
                "Below is a profile of the user, built from previous conversations.\n"
                "Use this to personalize your responses and maintain context.\n\n"
                + self.__user_summary
                + "\n\n"
                + self._system_prompt
            )
        self.__core_agent = Agent(model, instructions=full_instructions)

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
                "You are a memory-aware planning assistant. Given the user's message and recent "
                "conversation context, decide whether to look up relevant information from the "
                "user's memory.\n\n"
                "Rules:\n"
                "  - retrieve: true for questions about tools, preferences, recommendations,\n"
                "    past conversations, dates, or anything that benefits from looking up\n"
                "    the user's memory. false for greetings, simple one-word queries,\n"
                "    or obviously factual questions.\n"
                "  - source: 'episodic' for questions about what was discussed, when,\n"
                "    or specific events (e.g. 'what did we talk about?', 'did we discuss\n"
                "    Python last week?'). 'semantic' for questions about user facts,\n"
                "    preferences, tech stack, or stored knowledge. 'both' when the query\n"
                "    might need either or both.\n"
                "  - queries: 1-3 short search phrases tailored to the source:\n"
                "    - For 'semantic' or 'both': general phrases like 'CLI tools', 'Python\n"
                "      tech stack'. Keep them general for vector search.\n"
                "    - For 'episodic' or 'both': topic keywords (e.g. 'Python debugging').\n"
                "  - note: save a brief observation about the user if they revealed\n"
                "    something new. Leave empty for routine messages.\n"
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

    def _make_summary_agent(self) -> Agent:
        """Build a dedicated agent for generating user summaries.

        Produces raw markdown output — no structured output type.
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
                "You are a memory synthesis assistant. Given extracted facts "
                "and planner observations, produce a concise, well-structured "
                "user summary in markdown.\n\n"
                "Produce a structured user summary in markdown with these sections:\n\n"
                "# User Summary\n\n"
                "## Identity\n"
                "- **Name:** <name or \"(unknown)\">\n"
                "- **Location:** <location or \"(unknown)\">\n\n"
                "## Preferences\n"
                "- <each preference as a bullet>\n\n"
                "## Active Projects\n"
                "- <each project as a bullet>\n\n"
                "## Context\n"
                "- <recent interaction context>\n\n"
                "---\n"
                "*Auto-generated by G.A.I.A. — updated periodically as new information is learned.*"
            ),
        )

    async def _summarize_turns(self, conversation_text: str) -> str:
        """Summarize a set of turns into a single compact message.

        Uses the summary agent to compress the oldest half of a conversation
        into 3-5 bullet points, preserving facts, decisions, and preferences.

        Args:
            conversation_text: Raw text from concatenating the oldest messages.

        Returns:
            A summary string, or empty string on failure.
        """
        try:
            summary_agent = self._make_summary_agent()
            result = await summary_agent.run(
                "Summarize this conversation into 3-5 concise bullet points. "
                "Preserve any specific facts, decisions, or preferences mentioned. "
                "Omit pleasantries and filler.\n\n" + conversation_text
            )
            return result.output.strip()
        except Exception:
            logger.exception("summarize_turns failed")
            return ""

    def _load_user_summary(self) -> str:
        """Load the user summary from disk.

        Returns the body content (after frontmatter), or empty string
        if the file does not exist or parsing fails.
        """
        try:
            if _USER_SUMMARY_PATH.exists():
                content = _USER_SUMMARY_PATH.read_text(encoding="utf-8")
                _, body = _parse_frontmatter(content)
                return body.strip()
        except Exception:
            logger.exception("failed to load user summary")
        return ""

    def _save_user_summary(
        self, text: str, *, facts_count: int = 0, conversation_count: int = 0
    ) -> None:
        """Persist the summary markdown to disk with frontmatter metadata."""
        try:
            _USER_SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
            now = datetime.now().isoformat(timespec="seconds")
            metadata = {
                "version": "1",
                "last_updated": now,
                "facts_count": str(facts_count),
                "conversation_count": str(conversation_count),
            }
            frontmatter = _build_frontmatter(metadata)
            _USER_SUMMARY_PATH.write_text(
                frontmatter + text, encoding="utf-8"
            )
        except Exception:
            logger.exception("failed to save user summary")

    def reconfigure(
        self,
        model_name: str = "",
        endpoint: str = "",
        api_key: str = "",
        system_prompt: str = "",
        max_context_tokens: int = _DEFAULT_MAX_TOKENS,
    ) -> None:
        """Swap the underlying model, endpoint, and/or system prompt.

        Only non-empty values are applied, so callers can pass
        ``self.reconfigure(model_name=new_model)`` without
        worrying about clobbering other fields.

        The main agent, extraction agent, and planner agent are rebuilt
        with the new configuration.
        """
        if model_name:
            self._model_name = model_name
        if endpoint:
            self._endpoint = endpoint
        if api_key:
            self._api_key = api_key
        if system_prompt:
            self._system_prompt = system_prompt

        model = OpenAIChatModel(
            self._model_name,
            provider=OpenAIProvider(
                base_url=self._endpoint,
                api_key=self._api_key,
            ),
        )
        full_instructions = self._system_prompt
        if self.__user_summary:
            full_instructions = (
                "USER PROFILE\n"
                "Below is a profile of the user, built from previous conversations.\n"
                "Use this to personalize your responses and maintain context.\n\n"
                + self.__user_summary
                + "\n\n"
                + self._system_prompt
            )
        self.__core_agent = Agent(model, instructions=full_instructions)
        self.__extraction_agent = self._make_extraction_agent()
        self.__planner_agent = self._make_planner_agent()
        self.__message_hist = []
        self._context_window = ContextWindow(
            max_tokens=max_context_tokens,
            min_age_turns=_DEFAULT_MIN_AGE_TURNS,
        )

    def get_user_summary(self) -> str:
        """Return the currently loaded user summary text."""
        return self.__user_summary

    async def generate_user_summary(self) -> str:
        """Generate a user summary from accumulated conversation data.

        Queries episodic memory for recent planner notes and semantic
        memory for top facts, then uses the summary agent to produce
        a structured markdown summary. Writes the result to disk and
        updates the in-memory summary.

        Returns the new summary text, or empty string if there is no
        data to summarize. May raise on LLM or network errors.
        """
        if self._memory is None:
            return ""

        # Gather episodic data — recent planner notes
        planner_notes: list[str] = []
        try:
            notes_results = await self._memory.episodic_search(
                query="planner note observation",
                limit=20,
            )
            for note in notes_results:
                raw = note.get("payload", {})
                if isinstance(raw, str):
                    try:
                        raw = json.loads(raw)
                    except (json.JSONDecodeError, ValueError):
                        pass
                if isinstance(raw, dict):
                    n = raw.get("note", "")
                    if n:
                        planner_notes.append(n)
                elif raw:
                    planner_notes.append(str(raw)[:200])
        except Exception:
            logger.exception("failed to query planner notes")

        # Gather semantic data — top facts
        semantic_facts: list[str] = []
        try:
            facts = await self._memory.semantic_search("general", limit=20)
            for f in facts:
                content = f.get("content", "")
                if content:
                    semantic_facts.append(content)
        except Exception:
            logger.exception("failed to query semantic facts")

        # Gather recent episodic events — raw conversation turns
        # This is the fallback when planner notes / extracted facts are empty
        episodic_events: list[str] = []
        try:
            events = await self._memory.episodic_search(
                query="conversation turn",
                event_types=["conversation_turn"],
                limit=30,
            )
            for evt in events:
                # Try payload first (structured), fall back to summary
                payload_raw = evt.get("payload", "{}")
                if isinstance(payload_raw, str):
                    try:
                        payload = json.loads(payload_raw)
                    except (json.JSONDecodeError, ValueError):
                        payload = {}
                else:
                    payload = payload_raw or {}
                user_msg = payload.get("user_prompt", "")
                agent_resp = payload.get("agent_response", "")
                if user_msg or agent_resp:
                    episodic_events.append(
                        f"User: {user_msg[:300]}\nAgent: {agent_resp[:300]}"
                    )
        except Exception:
            logger.exception("failed to query episodic events for summary")

        # Build the input for the summary agent
        input_parts = []
        if planner_notes:
            input_parts.append("PLANNER NOTES:")
            input_parts.extend(f"- {n}" for n in planner_notes[-20:])
        if semantic_facts:
            input_parts.append("EXTRACTED FACTS:")
            input_parts.extend(f"- {f}" for f in semantic_facts[:20])
        if episodic_events:
            input_parts.append("RECENT CONVERSATION:")
            input_parts.extend(episodic_events[-15:])

        if not input_parts:
            logger.info("nothing to summarize yet")
            return ""

        input_text = "\n".join(input_parts)

        # Pass existing summary for incremental update
        if hasattr(self, "_Gaia__user_summary") and self._Gaia__user_summary:
            input_text += (
                "\n\nEXISTING SUMMARY (update, don't start from scratch):\n"
                + self.__user_summary
            )

        # Brief delay to avoid competing with the main streaming API call
        await asyncio.sleep(2)

        summary_agent = self._make_summary_agent()
        result = await summary_agent.run(input_text)
        new_text = result.output.strip()

        # Save to disk
        self._save_user_summary(new_text, facts_count=len(semantic_facts),
                                conversation_count=len(planner_notes))

        # Update in-memory summary
        self.__user_summary = new_text
        self.__last_summary_time = datetime.now()

        logger.info(
            "user summary generated (%d chars, %d facts, %d notes, %d events)",
            len(new_text), len(semantic_facts), len(planner_notes),
            len(episodic_events),
        )
        return new_text

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
                    source = planner_output.source
                    for query in planner_output.queries:
                        if source in ("semantic", "both"):
                            facts = await self._memory.semantic_search(query, limit=3)
                            for fact in facts:
                                content = fact.get("content", "")
                                confidence = fact.get("confidence", 1.0)
                                if content and confidence >= 0.5:
                                    context_blocks.append(f"[fact] {content}")

                        if source in ("episodic", "both"):
                            events = await self._memory.episodic_search(query, limit=5)
                            for evt in events:
                                payload_raw = evt.get("payload", "{}")
                                if isinstance(payload_raw, str):
                                    try:
                                        payload_raw = json.loads(payload_raw)
                                    except (json.JSONDecodeError, ValueError):
                                        payload_raw = {}
                                user_prompt = payload_raw.get("user_prompt", "")
                                agent_response = payload_raw.get("agent_response", "")
                                if user_prompt and agent_response:
                                    summary = (
                                        f"[conversation] You: {user_prompt[:120]} ... "
                                        f"Agent: {agent_response[:200]} ..."
                                    )
                                    context_blocks.append(summary)
                                elif user_prompt:
                                    context_blocks.append(f"[conversation] You: {user_prompt[:200]}")

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
        response_text = "".join(response_chunks)

        # Track this turn in the context window and rollup if needed
        self._context_window.add({
            "role": "user",
            "content": prompt,
        })
        self._context_window.add({
            "role": "assistant",
            "content": response_text,
        })

        if self._context_window.should_rollup():
            messages = self.__message_hist
            rollup_msgs = self._context_window.oldest_half(messages)
            if rollup_msgs:
                rollup_text = "\n".join(
                    f"{m.get('role', 'unknown')}: {str(m.get('content', ''))[:500]}"
                    for m in rollup_msgs
                )
                try:
                    summary = await self._summarize_turns(rollup_text)
                    if summary:
                        # Remove rolled-up messages from the window
                        self._context_window.remove(rollup_msgs)
                        # Add the new summary message
                        self._context_window.add({
                            "role": "system",
                            "content": f"[Conversation Summary]\n{summary}",
                        })
                        # Replace oldest messages with the summary
                        self.__message_hist = (
                            [{"role": "system", "content": f"[Conversation Summary]\n{summary}"}]
                            + messages[len(rollup_msgs):]
                        )
                        # Sync window estimate to actual history
                        self._context_window.sync(self.__message_hist)
                except Exception:
                    logger.exception("rollup failed — keeping full history")

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

        If a new fact potentially conflicts with an existing one (e.g., "I prefer A"
        vs "I prefer B"), both are saved — the old one keeps its confidence, the new
        one gets confidence=0.5 to mark uncertainty.

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
                # Check for potential conflicts first
                conflict = await self._detect_facts_conflict(fact_text)

                if conflict is not None:
                    # New fact contradicts an existing one — save both with
                    # lower confidence to mark the uncertainty.
                    await self._memory.semantic_insert(
                        content=f"[CONFLICT] {fact_text}",
                        category=category,
                        confidence=0.5,
                        source="auto_extract",
                        source_id=conflict.get("fact_id"),
                    )
                    saved += 1
                    logger.info(
                        "saved CONFLICTING fact [%s] %s (conflict with %s)",
                        category, fact_text[:80], conflict.get("fact_id", "?"),
                    )
                    continue

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
            logger.info("saved %d/%d facts (%d skipped as duplicates, %d conflicted)",
                        saved, len(facts), skipped, len(facts) - saved - skipped)
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

    async def _detect_facts_conflict(self, new_fact: str) -> dict | None:
        """Check if a new fact potentially conflicts with existing semantic memory.

        Searches for facts with significant token overlap but different content.
        If found, returns the conflicting fact's metadata (including fact_id).
        Returns None if no conflict detected.

        Conflicts are fuzzy — e.g. "User prefers A" vs "User prefers B" share
        the same topic but have opposing content. We use Jaccard similarity
        on topic keywords to detect overlaps, then check if the content differs.
        """
        if self._memory is None:
            return None

        # Extract topic keywords from the new fact
        new_tokens = set(t for t in re.split(r"[^a-z0-9]+", new_fact.lower()) if t)
        if len(new_tokens) < 3:
            return None  # Too short to detect meaningful conflicts

        # Search for facts with overlapping topic keywords
        try:
            results = await self._memory.semantic_search(
                " ".join(list(new_tokens)[:10]),
                limit=5,
            )
        except Exception:
            return None

        for existing in results:
            existing_content = existing.get("content", "")
            if not existing_content:
                continue

            existing_tokens = set(
                t for t in re.split(r"[^a-z0-9]+", existing_content.lower()) if t
            )
            if not existing_tokens:
                continue

            # Jaccard similarity on topic keywords
            intersection = new_tokens & existing_tokens
            union = new_tokens | existing_tokens
            jaccard = len(intersection) / len(union) if union else 0.0

            # High overlap but different content → potential conflict
            # (Dedup handles near-identical facts at >0.75)
            if 0.3 < jaccard < 0.75:
                # Content differs but topics overlap — likely a conflict
                # (e.g., "User prefers A" vs "User prefers B")
                return {
                    "fact_id": existing.get("fact_id"),
                    "content": existing_content,
                    "jaccard": jaccard,
                }

        return None

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
