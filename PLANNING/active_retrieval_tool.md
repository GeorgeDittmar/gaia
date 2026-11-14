# Active Retrieval Tool — Design

## Problem

The agent can only access memory passively:
- **User summary** is injected as static text in the system prompt
- **Planner** decides per-turn whether to inject memory context into the prompt
- **No way** for the agent to proactively look up information during a conversation

This means the agent can't answer "what did we work on yesterday?" or "remind me about that bug" — it has no tool for it.

## Solution

Add a `memory_query` tool to the core agent. The agent calls it when it needs information it doesn't already have.

---

## Tool Spec

```python
@agent.tool
async def memory_query(query: str) -> str:
    """Look up information from the user's memory store.

    Use this when you need to recall facts about the user,
    past conversations, or stored knowledge before answering.
    """
```

### Query format

Simple free-text query — the LLM formulates it. No structured planner output.

```
memory_query("what does George prefer for music production")
memory_query("what did we discuss about the Pydantic AI agent")
memory_query("George's tech stack and current projects")
```

### Return value

A formatted string that the agent can read and use to answer. No tool call chaining — the agent gets everything it needs in one response.

### Implementation

```python
@agent.tool
async def memory_query(self, query: str) -> str:
    """Look up information from the user's memory store."""
    results = await self._memory.semantic_search(query, limit=3)
    if not results:
        return "No relevant facts found in memory."
    
    parts = []
    for fact in results:
        content = fact.get("content", "")
        category = fact.get("category", "general")
        confidence = fact.get("confidence", 1.0)
        if confidence >= 0.5 and content:
            parts.append(f"[{category}] {content}")
    return "\n".join(parts)
```

**Why semantic only?** The planner already handles episodic retrieval per-turn. The tool is for when the agent needs to look something up *during* a conversation, which is almost always about stored facts/preferences/projects (semantic). If the agent needs episodic info, the planner will handle it on the next turn.

---

## Design Decisions

### 1. Simple tool, no structured output

The tool returns a plain string. The agent parses it naturally. No need for a `MemoryQueryResult` — the agent is the consumer.

**Trade-off**: Less structured, harder to post-process, but simpler and more LLM-friendly.

### 2. Semantic-only for the tool, episodic for the planner

- **Tool** → semantic facts (what the user *is*, *wants*, *uses*)
- **Planner** → both semantic and episodic (what the user *said*, *discussed*, *did*)

Rationale: The agent uses the tool to recall facts. Episodic recall is handled automatically by the planner each turn. Separation of concerns.

### 3. No separate tool per memory type

One `memory_query` tool that covers both sources, or two separate tools (`memory_facts`, `memory_conversations`)?

| Option | Pros | Cons |
|--------|------|------|
| One tool, semantic-only | Simple, matches the actual need | Can't look up episodic via tool |
| One tool, both sources | Most flexible | More complex, planner already covers episodic |
| Two tools | Clear intent | More tokens in tool schema, agent may overuse |

**Recommendation: semantic-only single tool.** Episodic is handled by the planner. If the user asks about past conversations, the planner retrieves them. The tool is for *facts* the agent needs to recall mid-conversation.

### 4. When to call it

Instructions for the agent:
```
You have a memory tool: memory_query(query). Use it whenever:
- You need to recall a fact about the user
- You're unsure about a detail and want to check memory
- The user asks "do you remember..." or "what did I tell you about..."
- You need context to give an accurate answer

Don't use it for:
- Simple factual questions (math, coding, general knowledge)
- Questions about the immediate conversation history
- Greetings or small talk
```

---

## Costs

- **One extra tool schema** added to the core agent's system prompt (~150 tokens)
- **One LLM call** per tool invocation (the tool itself runs locally — no extra LLM cost)
- **Tool use token**: ~50 tokens per invocation in the conversation history
- No streaming overhead — tool results are injected as plain text

The planner already costs one LLM call per turn. This adds ~0 extra LLM calls. The tool call itself is just a function call + query + formatting.

---

## Alternative: Agent-as-Tool (future)

A more ambitious approach: the agent *becomes* the memory system. Instead of a tool, the agent could be asked to "look it up yourself" — but that's the same as calling the tool. No meaningful difference here.

## Alternative: Direct prompt injection (current)

Could we just inject all semantic facts into every prompt? No — the fact store can grow to hundreds of facts, and injecting all of them wastes tokens and confuses the model with irrelevant context. The planner's per-turn retrieval + the tool's on-demand retrieval is the right balance.

---

## Implementation Plan

1. Add `memory_query` method to `Gaia` class (in `base.py`)
2. Register it as a tool on `__core_agent` in `_rebuild_agents()`
3. Add agent instructions for when to use it
4. Write tests:
   - Tool is registered and callable
   - Returns formatted results for matching queries
   - Returns "no results" message when nothing matches
   - Respects confidence threshold

## Files Changed

- `src/gaia/core/agent/base.py` — new method + tool registration + instructions
- `tests/test_agent.py` — tool registration and behavior tests
