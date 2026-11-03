# Memory Architecture

The memory system gives G.A.I.A. the ability to remember facts, conversation
history, and learned procedures across sessions. Everything flows through a
single protocol — swap backends without changing the agent.

## Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        GaiaTUIApp                              │
│                         ainteract()                             │
└───────────────┬─────────────────────────────────────────────────┘
                │
                ▼
┌─────────────────────────────────────────────────────────────────┐
│                        Gaia (agent)                             │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────────┐  │
│  │ Planner     │  │ Extraction   │  │ Summary                │  │
│  │ Agent       │  │ Agent        │  │ Agent                  │  │
│  │             │  │              │  │                        │  │
│  │ decide:     │  │ extract:     │  │ synthesize:            │  │
│  │  retrieve?  │  │  facts       │  │  user profile          │  │
│  │  queries:[] │  │  + classify  │  │  from accumulated      │  │
│  │  note: str  │  │  categories  │  │  data                  │  │
│  └──────┬──────┘  └──────┬───────┘  └──────────┬─────────────┘  │
└─────────┼───────────────┼──────────────────────┼────────────────┘
          │               │                      │
          │               │                      │
          ▼               ▼                      ▼
┌─────────────────────────────────────────────────────────────────┐
│                    MemoryStore protocol                         │
│                   (ABC — abstract base)                         │
│                                                                 │
│  semantic_insert / update / delete / search / similarity_search│
│  episodic_record / search / get_conversation / prune           │
│  procedure_register / get / search                             │
│  audit_log                                                     │
└──┬──────────────────┬──────────────────┬──────────────────────┘
   │                  │                  │
   ▼                  ▼                  ▼
┌──────────┐    ┌──────────┐     ┌──────────────┐
│ ChromaDB │    │ File     │     │ (future)     │
│ backend  │    │ backend  │     │ (Redis, etc) │
│          │    │          │     │              │
│ Vector   │    │ Flat     │     │              │
│ embeds   │    │ .md files│     │              │
└──────────┘    └──────────┘     └──────────────┘

  MemoryFactory.create(Settings) → MemoryStore | None
```

## Three Memory Types

| Type | Purpose | Backend | Search |
|------|---------|---------|--------|
| **Semantic** | Facts about the user (preferences, projects, personal info) | ChromaDB embeddings or file keywords | Vector similarity or Jaccard overlap |
| **Episodic** | Conversation turns, planner notes (what happened and when) | ChromaDB or file keywords | Time-range filter + keyword scoring + fallback scan |
| **Procedural** | Learned routines / policies (skills, how-to) | ChromaDB or file keywords | Vector or keyword scoring |

### Semantic Memory

Stores individual facts as declarative sentences:

```
"User prefers Python 3.14 for new projects"
"User lives in Portland, OR"
"Working on a Gaia TUI refactor branch"
```

Each fact has a category (`preference`, `project`, `general`), confidence
(`0.0–1.0`), and source (`auto_extract`, `manual`, etc.).

**Deduplication**: before saving, `semantic_similarity_search()` checks if
a similar fact already exists. ChromaDB uses cosine distance; file store
uses Jaccard token overlap. Default threshold: **0.75** (75% overlap = skip).

**Conflict detection**: when a new fact overlaps topics with an existing one
(Jaccard 0.3–0.75) but differs in content, both are saved — the new one
gets [CONFLICT] prefix and confidence=0.5 so the user can review.

### Episodic Memory

Stores conversation turns and planner observations with timestamps:

```json
{
  "event_type": "conversation_turn",
  "conversation_id": "abc123",
  "summary": "User: hello",
  "payload": {
    "user_prompt": "hello",
    "agent_response": "Hi! How can I help?",
    "context_facts_used": 2
  }
}
```

Two event types:
- **`conversation_turn`** — every user/agent exchange
- **`planner_note`** — brief observation saved by the planner agent

Episodic search supports time-range filtering, event type filtering, and
text query. Both backends fall back to a full scan if vector/keyword search
returns no results.

### Procedural Memory

Reserved for learned procedures (skills, policies, routines). Not yet used
in the active flow. Each procedure has a safety level and optional step
definitions.

## Data Flow

### Per-Turn Flow (`Gaia.ainteract`)

```
User message
    │
    ▼
┌─────────────────────────────────────────────┐
│ 1. PLANNER                                  │
│    Decide: should I retrieve memory?        │
│    Generate 1-3 search queries              │
│    Note any new observations                │
└──────────────┬──────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────┐
│ 2. RETRIEVE (if planner says retrieve)      │
│    For each query → semantic_search(limit=3)│
│    Filter: confidence >= 0.5                │
│    Deduplicate by content                   │
└──────────────┬──────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────┐
│ 3. AUGMENT PROMPT                           │
│    Inject retrieved facts into user message │
│    "[Relevant stored facts]" block          │
└──────────────┬─────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────┐
│ 4. STREAM RESPONSE                          │
│    Main LLM generates response              │
│    Message history updated                  │
└──────────────┬──────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────┐
│ 5. RECORD EPISODIC                          │
│    episodic_record("conversation_turn", ...) │
│    episodic_record("planner_note", ...) if │
│      planner produced a note                │
└──────────────┬──────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────┐
│ 6. INLINE FACT EXTRACTION                   │
│    post_turn_extract(prompt, response)      │
│    ├─ _extract_facts() → LLM extracts facts│
│    ├─ _classify_fact() → category (preference│
│    │                 project, general)      │
│    ├─ _detect_facts_conflict() → check for │
│    │                 contradictions         │
│    ├─ semantic_similarity_search() → dedup │
│    └─ semantic_insert() → save fact        │
└─────────────────────────────────────────────┘
```

### Background Summary Timer

`GaiaTUIApp.on_mount()` schedules a 600-second (10-minute) interval:

```python
self.set_interval(600, self._background_summary_check)
```

`_background_summary_check` fires a background task that:
1. Checks if the user summary is older than 30 minutes
2. If so, calls `Gaia.generate_user_summary()`
3. Summary agent gathers planner notes, semantic facts, and episodic events
4. Produces structured markdown → saves to `~/.gaia/user_summary.md`

### Batch Extraction (`/extract` slash command)

`Gaia.extract_from_history(max_turns=10)`:
1. Queries episodic memory for last N conversation turns
2. For each turn, calls `post_turn_extract()`
3. Saves any new facts (same dedup/conflict logic)

## The MemoryStore Protocol

All backends implement this ABC. The agent code only imports from
`gaia.core.memory.base`, never from a specific backend.

```python
class MemoryStore(ABC):
    # Lifecycle
    async def initialize(self) -> None
    async def close(self) -> None

    # Semantic — facts about the user
    async def semantic_insert(self, content, category, confidence,
                              source, source_id=None) -> str
    async def semantic_update(self, fact_id, new_content, new_confidence) -> None
    async def semantic_delete(self, fact_id) -> None
    async def semantic_search(self, query, limit=10) -> list[dict]
    async def semantic_similarity_search(self, query, limit=5,
                                         distance_threshold=0.8) -> bool

    # Episodic — conversation history
    async def episodic_record(self, event_type, conversation_id,
                              summary, payload) -> str
    async def episodic_search(self, time_range, event_types, query,
                              limit=20) -> list[dict]
    async def episodic_get_conversation(self, conversation_id) -> list[dict]
    async def episodic_prune(self, before_timestamp) -> int

    # Procedural — learned routines (not yet active)
    async def procedure_register(self, ...) -> None
    async def procedure_get(self, procedure_id) -> dict | None
    async def procedure_search(self, query, category) -> list[dict]

    # Audit — append-only log of memory operations
    async def audit_log(self, action, actor, target_type, target_id,
                        detail) -> None
```

Both backends return the same dict shape from search methods:
```python
{
    "fact_id": "...",         # or "event_id" / "procedure_id"
    "content": "...",         # or "summary" / "description"
    "category": "preference", # or "project" / "general"
    "confidence": 0.9,
    "source": "auto_extract",
    "source_id": "...",       # links conflicting facts
    "created_at": "2025-01-01T...",
    "updated_at": "2025-01-01T...",
    "payload": "...",         # episodic: JSON string of structured data
}
```

## Backends

### File-based Store (`FileMemoryStore`)

**Storage**: flat Markdown files with YAML frontmatter.

```
~/.gaia/memory/
├── semantic/
│   ├── <uuid>.md          # Each fact is one file
│   └── ...
├── episodic/
│   ├── <uuid>.md
│   └── ...
├── procedures/
│   └── <id>.md
└── audit/
    └── <uuid>.md
```

**Search**: keyword token scoring.

1. `_tokenize(text)` — lowercase, split on non-alphanumeric, drop stopwords
2. `_score_query(query, body)` — count matching tokens
3. Results sorted by score (highest first), then recency

**Similarity**: Jaccard token overlap instead of vector embeddings.

```
query_tokens = {"user", "prefer", "python"}
body_tokens  = {"user", "prefers", "python", "3.14"}
jaccard = |intersection| / |union| = 2/7 = 0.29
```

**Async safety**: all filesystem I/O wrapped in `asyncio.to_thread()`.

### ChromaDB Store (`ChromaMemoryStore`)

**Storage**: 4 ChromaDB collections (`semantic_facts`, `episodic_events`,
`procedure_entries`, `audit_log`) backed by a persistent SQLite database
at `~/.gaia/chroma`.

**Search**: vector similarity using ChromaDB's built-in embeddings.

- `semantic_search()` → `collection.query(query_texts=...)`
- `semantic_similarity_search()` → returns cosine distance, checks
  `any(d <= distance_threshold)`

**Fallback**: when episodic vector search returns no results, falls back
to `collection.get()` (full scan) so metadata filters still work.

**Async safety**: all ChromaDB calls wrapped in `asyncio.to_thread()`.

**Update workaround**: ChromaDB doesn't support in-place document updates.
`semantic_update()` reads old metadata, deletes by fact_id, re-inserts.

## Fact Extraction Pipeline

### Agent Definition

Three pydantic-ai agents, each with structured output:

1. **Planner** (`PlannerResult`) — `retrieve: bool`, `queries: list[str]`, `note: str`
2. **Extractor** (`FactResult`) — `facts: list[FactEntry]` where each entry has
   `text: str` and `confidence: float`
3. **Summarizer** (unstructured) — produces raw markdown user profile

### Classification

After extraction, each fact is classified by keyword matching:

```
_PROJECT_KEYWORDS   = {"project", "repo", "code", "bug", "feature", ...}
_PREFERENCE_KEYWORDS = {"name", "location", "lives", "prefers", "likes", ...}

classify(fact):
    if any kw in _PROJECT_KEYWORDS: return "project"
    if any kw in _PREFERENCE_KEYWORDS: return "preference"
    return "general"
```

### Conflict Detection

`_detect_facts_conflict(new_fact)`:

1. Extract topic keywords from new fact (lowercase, alphanumeric)
2. `semantic_search()` with those keywords, limit=5
3. For each result, compute Jaccard similarity on topic keywords
4. If **0.3 < jaccard < 0.75** and content differs → conflict

The 0.75 upper bound prevents catching near-duplicates (those are handled
by the dedup check *before* conflict detection). The 0.3 lower bound
prevents false positives from unrelated facts that happen to share a word.

### Summary Generation

`generate_user_summary()`:

1. Gather planner notes (last 20) from episodic memory
2. Gather top facts (20) from semantic memory
3. Gather recent episodic events (15) as fallback
4. Pass all to summary agent with existing summary for incremental update
5. Save to `~/.gaia/user_summary.md` with frontmatter metadata

## Configuration

```python
# In settings.json
{
    "memory": {
        "backend": "file"       # "file" or "chroma"
    },
    "memory_dir": "~/.gaia/memory"
}
```

`MemoryFactory.create(Settings)`:
- `"file"` → `FileMemoryStore(Path(settings.memory.memory_dir))`
- `"chroma"` → `ChromaMemoryStore()`
- unknown → `None` (memory disabled)

Initialization is deferred to the event loop — the factory creates the
instance but does not call `initialize()`.

## Key Files

| File | Purpose |
|------|---------|
| `src/gaia/core/memory/base.py` | `MemoryStore` ABC — the protocol |
| `src/gaia/core/memory/file_store.py` | File-based backend |
| `src/gaia/core/memory/chroma_store.py` | ChromaDB backend |
| `src/gaia/core/memory/factory.py` | `MemoryFactory` — creates stores |
| `src/gaia/core/agent/base.py` | `Gaia` agent — orchestrates all memory ops |
| `src/gaia/config.py` | `MemoryConfig` dataclass in Settings |

## Extension Points

### Adding a New Backend

1. Implement all `@abstractmethod` methods from `MemoryStore`
2. Return the instance from `MemoryFactory.create()` in a new `case` arm
3. Register the new backend name in `MemoryConfig.backend` validation

### Adding a New Memory Type

1. Add abstract methods to `MemoryStore` in `base.py`
2. Implement in both backends
3. Wire into `Gaia` agent in `base.py` (extraction, retrieval, summary)

### Changing Dedup/Conflict Thresholds

- **Dedup threshold**: `distance_threshold=0.75` in `post_turn_extract()` →
  `semantic_similarity_search()` call
- **Conflict range**: `0.3 < jaccard < 0.75` in `_detect_facts_conflict()`
- **Conflict confidence**: `confidence=0.5` when saving `[CONFLICT]` facts
- **Classification keywords**: `_PROJECT_KEYWORDS` and `_PREFERENCE_KEYWORDS`
  at module level in `src/gaia/core/agent/base.py`

## Known Gaps

1. **No TTL/cleanup** — episodic memory grows unbounded; `episodic_prune()`
   exists but is never called automatically
2. **Procedural memory** — not wired into the agent flow
3. **No vector model customization** — ChromaDB uses default embeddings
4. **No encryption** — memory files on disk are plain text
5. **Agent hardcodes model/endpoint** — `Gaia.__init__` defaults to
   `local-model` / `http://localhost:8080/v1`; settings are not yet passed
   in from the UI
6. **No memory migration** — switching backends requires manual data export
   and re-import
