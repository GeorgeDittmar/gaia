# CHANGELOG

All notable changes to this project.

## [Unreleased]

### Added

#### AGENT.md System Prompt Integration
- `GaiaTUIApp.__init__` reads `AGENT.md` and merges its content into `settings.system_prompt`
- Settings modal now displays the actual AGENT.md prompt content instead of a placeholder
- When the system prompt is saved via `/settings`, changes are written to `AGENT.md` on disk
- Agent is reconfigured with the new prompt when settings change

#### Timestamped Backups
- `/settings` creates a timestamped backup of `AGENT.md` before overwriting it
- Backup format: `AGENT.backup.YYYYMMDD-HHMMSS.md`
- Backups are only created when the system prompt has actually changed
- `_backup_system_prompt()` helper in `slash_commands.py` handles atomic rename

#### Background Fact Extraction
- Inline fact extraction after every conversation turn (via `post_turn_extract()`)
- Extraction agent uses pydantic-ai structured output (`FactResult`) for consistent fact formatting
- Automatic fact classification into `preference`, `project`, or `general` categories based on keyword matching
- Deduplication: Jaccard token similarity search (threshold 0.75) prevents duplicate facts
- **Conflict detection**: New facts with overlapping topic keywords but different content are flagged with `[CONFLICT]` prefix and saved with `confidence=0.5`, preserving both old and new versions
- Conflict metadata tracks `source_id` linking conflicting facts
- `/extract` — batch-extract facts from recent history (last 10 turns)
- `/extract-debug` — show raw user/agent message pair used for extraction
- Background periodic extraction every N turns catches facts inline extraction may miss

#### Planner Agent
- Lightweight pydantic-ai agent (`PlannerResult`) runs on every user message before the main LLM
- Three decisions per turn:
  - `retrieve`: Should memory lookup happen? (false for greetings/simple questions)
  - `queries`: 1-3 search phrases for semantic memory retrieval
  - `note`: One-sentence observation about the user to save
- Retrieved facts injected into augmented prompt before main response
- Planner notes saved as episodic events for later summary generation

#### Memory Store Improvements
- Episodic search now falls back to full scan when vector search returns no results (both Chroma and File stores)
- Event type filter corrected: `"conversation_turn"` used consistently for conversation turn events
- `_remove_by_id()` helper replaces `remove_children(predicate=...)` for slash command status cleanup

### Fixed

- **Startup crash**: `call_later()` with no delay created infinite event loop; replaced with `set_interval()` in `on_mount()`
- **`/regenerate` crash**: `remove_children(predicate=...)` not supported by Textual; replaced with `_remove_by_id()` querying by CSS ID
- **Empty summary**: Summary query filtered by wrong event type (`"user_turn", "agent_turn"` vs `"conversation_turn"`); fixed filter
- **Empty summary (vector search)**: ChromaDB vector search for `"conversation turn"` didn't match `"User: hello"` summaries; added fallback to full scan when query returns zero results
- **No inline facts**: Facts only extracted on background timer; now extracted immediately after each turn so next turn can use them

### Architecture

- `Gaia.__init__` loads user summary from disk, prepends to system prompt
- `Gaia.ainteract()` flow:
  1. Planner agent decides on memory retrieval
  2. Semantic memory retrieval (if planner says retrieve)
  3. Augmented prompt assembly with retrieved facts
  4. Main LLM response streaming
  5. Episodic event recording (raw turn data)
  6. Planner note recording (if observation)
  7. Inline fact extraction (saves to semantic memory immediately)
- Background summary timer: `set_interval(600, ...)` in `on_mount()`, checks 30-min age before regenerating
- Fact extraction: every N turns via background task, plus inline after every turn

---

## [0.1.0] — Initial Release

- Basic Textual TUI with cyberpunk theme
- LLM integration via OpenAI-compatible endpoint
- Settings modal (`/settings`) and model switching (`/model`)
- Basic slash commands: `/status`, `/help`, `/settings`, `/model`
- File-based memory store (flat markdown files)
- ChromaDB memory store (vector embeddings)
- Endpoint polling with connection status badge
- Chat interface with message history
