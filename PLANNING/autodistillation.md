# Autodistillation — Knowledge & Procedural Memory

## Goal

Two background processes that analyze accumulated episodic memory and produce higher-level memory:

1. **Knowledge distillation**: Episodic → Semantic. Extract patterns, correlations, and learned insights that go beyond explicit facts.
2. **Procedural memory distillation**: Episodic → Procedural. Identify repeated successful workflows and register them as reusable procedures.

## Differences from existing fact extraction

| | Fact Extraction (existing) | Knowledge Distillation (new) |
|---|---|---|
| **Scope** | Single conversation turn | New chunk of episodic events since last distillation |
| **Output** | Explicit facts ("User prefers uv") | Pattern insights ("User consistently chooses uv over pip for speed") |
| **Confidence** | 0.8 (direct statement) | 0.5-0.8 (inferred pattern) |
| **Trigger** | Every N turns + /extract | Background timer, periodic |
| **Dedup** | Per-turn Jaccard check | Seek-point cursor + versioned insights |

## Seek-point cursor — avoid reprocessing old data

The key problem: if distillation scans all episodic data every time, it will find the same patterns repeatedly and waste LLM calls.

**Solution: maintain a distillation cursor.**

A single timestamp stored in the agent (`_Gaia__last_distillation_time`), initialized to `None`. On each distillation run:

1. Only query episodic events **after** the cursor timestamp
2. Process those events through the distillation agent
3. Save results as semantic memory or procedures
4. Advance the cursor to the current time

This ensures each episodic event is analyzed **at most once** by distillation. Events that were already distilled are never re-queried.

```python
# In __init__
self.__last_distillation_time: datetime | None = None

# In distill_knowledge() / distill_procedures()
time_range = (self.__last_distillation_time.isoformat(), ) if self.__last_distillation_time else None
results = await self._memory.episodic_search(time_range=time_range, ...)
# ... process results ...
self.__last_distillation_time = datetime.now()
```

**What this means:**
- Events distilled in session 1 are not re-processed in session 2 (cursor is per-session — it resets on restart)
- During a session, the cursor advances after each run, so even a long session doesn't re-scan the same events
- On restart, the cursor resets to `None` and distillation scans all events — but the **dedup logic below** prevents duplicates

## Versioned insights — handling conflicting patterns

When new data produces a pattern that conflicts with an existing one, we **don't auto-overwrite**. Instead:

1. **New insight saved with `source="distillation"` + `updated_at` timestamp**
2. **If a conflicting existing insight is found**, the new one is saved anyway with a lower confidence (0.5) to mark it as "possibly conflicting"
3. **During conversation**, if the agent retrieves both an old and conflicting new insight, it presents ambiguity to the user naturally:
   - "I recall you preferring X, but I also noticed you've been using Y lately — which do you prefer?"
4. **The user can resolve** via `/remember "I actually prefer X"` which updates the old insight's confidence or creates a corrective fact
5. **The older version stays** but with lower effective confidence — it can be useful for tracking how the user's preferences evolve

This preserves history of how the user's patterns changed, rather than silently overwriting old knowledge.

## Semantic conflict detection

Distillation checks for potential conflicts against existing semantic memory. When a new insight is produced:

1. Search semantic memory for overlapping/contradictory insights (Jaccard token overlap on similar topic keywords)
2. If overlap found → save new insight with `confidence=0.5` (uncertain/conflicting)
3. If no overlap → save with normal confidence `0.7-0.8`
4. Both versions remain in semantic memory for retrieval

The agent's retrieval logic already ranks by confidence, so when there's a conflict, both appear in context and the agent can naturally ask for clarification.

## Files to modify

### 1. `src/gaia/core/agent/base.py` — Core distillation logic

**New Pydantic output types** (near existing `FactResult`):
```python
class DistilledInsight(BaseModel):
    """A single insight distilled from accumulated episodic data."""
    text: str = Field(description="The distilled insight as a complete sentence")
    category: str = Field(description="Category: preference, project, general, workflow, communication")
    confidence: float = Field(default=0.7, ge=0.0, le=1.0,
        description="Confidence level (0.0-1.0). Lower than extracted facts since this is inferred.")
    source_ids: list[str] = Field(default_factory=list,
        description="Episodic event IDs that this insight was derived from")

class DistilledInsightResult(BaseModel):
    """Structured LLM result: a list of distilled insights."""
    insights: list[DistilledInsight]

class DistilledProcedure(BaseModel):
    """A single procedure distilled from episodic data."""
    name: str = Field(description="Short name for the procedure")
    description: str = Field(description="What the procedure does")
    steps: list[str] = Field(default_factory=list,
        description="Ordered list of steps")
    safety_level: str = Field(default="safe",
        description="safe, caution, or danger")
    category: str = Field(default="custom",
        description="Communication, coding, research, productivity, system, custom")

class DistilledProcedureResult(BaseModel):
    """Structured LLM result: a list of distilled procedures."""
    procedures: list[DistilledProcedure]
```

**New methods:**

`_make_distillation_agent() -> Agent[DistilledInsightResult]`
- Creates a pydantic-ai agent with instructions to analyze episodic data for patterns
- Instructions: "Analyze the accumulated episodic data below. Find patterns, recurring preferences, correlations, and learned insights. Produce structured distilled insights. Skip individual facts — only produce insights that represent accumulated knowledge."

`_make_procedure_distillation_agent() -> Agent[DistilledProcedureResult]`
- Creates a pydantic-ai agent with instructions to find repeatable workflows
- Instructions: "Analyze the episodic data below. Find successful multi-step workflows that the user repeats. Convert them into structured procedures with steps and safety levels."

`async def distill_knowledge() -> int` (public)
- Queries episodic memory for events **after** `_last_distillation_time` cursor
- Builds input text for the distillation agent
- Calls `_make_distillation_agent().run()`
- For each insight:
  - Search existing semantic memory for overlapping/conflicting insights
  - If overlap found → save with `confidence=0.5` (marking uncertainty)
  - If no overlap → save with `confidence=0.7` (normal)
  - Always save with `source="distillation"` and `updated_at` timestamp
- Advances `_last_distillation_time` cursor
- Returns number of insights saved

`async def distill_procedures() -> int` (public)
- Queries episodic memory for events **after** `_last_distillation_time` cursor (shared cursor)
- Builds input text for the procedure distillation agent
- Calls `_make_procedure_distillation_agent().run()`
- For each procedure, registers via `self._memory.procedure_register()` with `safety_level=procedure.safety_level`
- Returns number of procedures registered

**Modify `reconfigure()`:**
- No change needed — distillation methods don't depend on agent instance

### 2. `src/gaia/gaia_tui_app.py` — Background scheduling

**New constants:**
```python
_DISTILL_INTERVAL_MINUTES = 30
_DISTILL_MIN_AGE_MINUTES = 60
```

**New methods:**
```python
@work(exclusive=False, thread=False)
async def _run_background_distillation(self) -> None:
    """Periodically distill knowledge and procedures from episodic memory.

    Runs every ~30 minutes. Only runs if the last distillation was more
    than 60 minutes ago (needs accumulated data to be useful).
    Reschedules itself on completion.
    """
    if self._memory is None:
        return
    try:
        await self._GaiaTUIApp__core_agent.distill_knowledge()
        await self._GaiaTUIApp__core_agent.distill_procedures()
    except Exception:
        logger.exception("background distillation failed")
    finally:
        self.call_later(self._run_background_distillation)

def _schedule_background_distillation(self) -> None:
    """Start the background distillation timer."""
    self.call_later(self._run_background_distillation)
```

**Modify `__init__`:**
- After `_schedule_background_summary()`, call `_schedule_background_distillation()`

### 3. `src/gaia/slash_commands.py` — `/distill` command

Add a handler for `/distill` that:
- Calls both `distill_knowledge()` and `distill_procedures()`
- Shows result in chat: "X insights distilled, Y procedures registered"
- If any conflicting insights were found (confidence=0.5), mention that the agent will ask for clarification in the next conversation

### 4. `src/gaia/config.py` — Config update

Add to `SLASH_COMMANDS`:
```python
{"cmd": "/distill", "desc": "Distill knowledge and procedures from episodic memory"},
```

## Background timing

| Process | Interval | Min Age | Purpose |
|---|---|---|---|
| Fact extraction | Every 3 turns | N/A | Capture explicit facts from recent turns |
| User summary | Every 10 min | 30 min | Keep user profile fresh for next session |
| Knowledge distillation | Every 30 min | 60 min | Find patterns in new data since last distillation |
| Procedural distillation | Every 30 min | 60 min | Identify new repeatable workflows |

Distillation runs less frequently because it needs accumulated data. The 60-minute minimum age means it won't fire during short sessions.

## Conflict resolution flow

```
Episodic event → Distillation agent → New insight

If insight conflicts with existing semantic insight:
  ├─ Save new insight (confidence=0.5, timestamped)
  ├─ Leave old insight (confidence=0.7-0.8) untouched
  ├─ Next time both are retrieved, agent sees both
  └─ Agent asks user naturally: "I've noticed you've been doing X, 
      but earlier you mentioned Y — which is right?"
      
User resolves:
  ├─ Confirms old → agent updates old insight's confidence up
  ├─ Confirms new → agent updates old insight's confidence down or marks as outdated
  └─ Neither → both stay, agent uses context to pick appropriate one
```

## Deduplication vs conflict detection

- **Dedup**: Identical or near-identical insights → skip entirely (Jaccard threshold 0.75 on the insight text itself)
- **Conflict**: Similar but opposing insights → save both with different confidence levels
  - Detection: search semantic memory for insights on the same topic but different content (e.g., "User prefers A" vs "User prefers B")
  - This is softer than dedup — it's "topic match but content diverge" not "same content"

## Testing

1. Start with no episodic data → `/distill` returns 0 insights, 0 procedures
2. Have 5+ conversation turns with recurring patterns → `/distill` should find insights
3. Verify distilled insights appear in semantic memory with `source="distillation"` + timestamps
4. Verify procedures are registered in the procedures/ directory
5. Verify no duplicate facts are saved (dedup)
6. Verify conflicting insights are both saved with different confidence (conflict detection)
7. Verify background timer runs at correct interval (check logs)
8. Verify cursor advances after each run (events processed in session 1 are not re-processed in session 2 unless app restarted)
