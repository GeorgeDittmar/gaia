# G.A.I.A. Architecture

> Architecture overview for the G.A.I.A. (General AI Assistant) codebase.
> Updated regularly — see the change history at the bottom.

---

## Overview

G.A.I.A. is a private, local, zero-telemetry personal AI assistant built as a
Textual terminal UI that talks to a local LLM backend (llama.cpp / `llama-server`,
Ollama, or any OpenAI-compatible endpoint) through pydantic-ai. Everything runs on
the user's own machine — no cloud calls, no telemetry.

---

## Repository layout

```
gaia/
├── main.py                     # Minimal stub entrypoint → delegates to GaiaTUIApp
├── settings.json               # Runtime config (auto-created on first run)
├── AGENT.md                    # Core system prompt (version-controlled)
├── spec.md                     # Project specification / requirements doc
├── qwen-fixed.jinja            # Qwen template for some local backends
├── ARCHITECTURE.md             # This file
├── CLAUDE.md                   # Agent workflow guidance for contributors
├── pyproject.toml              # Project metadata + dependencies + hatchling build
├── uv.lock                     # Locked dependencies
├── README.md                   # One-liner: "G.A.I.A / General AI Assistant"
├── src/
│   ├── gaia/                   # The "gaia" package (hatchling-managed)
│   │   ├── __init__.py         # Re-exports: `GaiaTUIApp` from gaia_tui_app
│   │   ├── cli.py              # CLI entry point — installed as `gaia` console script
│   │   ├── config.py           # Settings dataclass, config loading, endpoint checking
│   │   ├── gaia_tui_app.py     # Main Textual app (GaiaTUIApp) — the TUI entrypoint
│   │   ├── command_input.py    # CommandInput widget — slash-command autocomplete
│   │   ├── help_modal.py       # HelpModal — F1/? keyboard shortcut reference
│   │   └── settings_modal.py   # SettingsModal — /settings pop-up dialog
│   │   ├── core/               # Core engine
│   │   │   ├── agent/
│   │   │   │   ├── __init__.py # Re-exports: `Gaia` from base
│   │   │   │   └── base.py     # `Gaia` class — pydantic-ai agent harness + fact extraction
│   │   │   └── memory/         # Memory layer — flat-file store + ChromaDB optional
│   │   │       ├── __init__.py # Exports: MemoryStore, FileMemoryStore, ChromaMemoryStore
│   │   │       ├── base.py     # Abstract MemoryStore protocol (ABC)
│   │   │       ├── file_store.py   # Default backend — flat markdown files + keyword search
│   │   │       └── chroma_store.py # Optional backend — ChromaDB vector search
│   └── plugins/
│       └── __init__.py         # Empty — reserved for a future plugin system
```

---

## How it fits together

### Entry points

- `uv run python main.py` or `gaia` (installed console script) both launch
  `GaiaTUIApp()`.
- `src/gaia/cli.py` is the installed entry point; `main.py` in the repo root is a
  stub delegating to `GaiaTUIApp`.

### Config flow

`GaiaTUIApp.__init__` calls `load_config()` from `gaia.config`, which merges
`settings.json` on disk over `DEFAULT_SETTINGS` (disk values win). The return
type is now the `Settings` dataclass.

| Key | Type | Default | Description |
|---|---|---|---|
| `memory_dir` | `str` | `"~/.gaia/memory"` | Directory for flat-file memory |
| `memory.backend` | `"file"` or `"chroma"` | `"file"` | Storage backend to use |

### User message

`on_input_submitted` mounts a "You:" turn, then
`run_agent_execution` (a `@work` task) streams the reply token-by-token by
iterating `Gaia.ainteract(prompt)`.

### Slash commands

Typed input starting with `/` is handled by `handle_slash_command`
(`match`/`case`). The `SLASH_COMMANDS` list in `gaia.config` drives both the
autocomplete in `CommandInput` and the help overlay in `HelpModal`.

| Command | Description |
|---|---|
| `/model <name>` | Switch active LLM runtime model |
| `/settings` | Open persistent setup pop-up |
| `/clear` | Clear terminal chat buffer |
| `/status` | Display active runtime diagnostics |
| `/help` | Show interactive keybindings & commands overlay |
| `/remember <fact>` | Store a fact in semantic memory |
| `/extract` | Auto-extract facts from recent conversation history |
| `/extract-debug` | Extract facts and show raw LLM output in chat |
| `/debug on\|off` | Toggle debug mode (controls whether extraction status shows) |
| `/exit` / `/close` | Exit the application |

### `Gaia` agent (`src/gaia/core/agent/base.py`)

Wraps a pydantic-ai `Agent` backed by an `OpenAIChatModel` pointed at a local
OpenAI-compatible endpoint. Key capabilities:

- **`ainteract(prompt)`** — Async generator that yields text deltas via
  `run_stream` / `stream_text(delta=True)`. Before streaming, queries semantic
  memory for relevant facts and injects them into an augmented prompt. After
  streaming, records the turn as episodic memory.
- **`post_turn_extract(user_prompt, agent_response)`** — Runs a dedicated
  pydantic-ai extraction agent (structured output via `FactResult`) to pull
  factual statements about the user from conversation text, auto-classifies
  each fact as `preference` / `project` / `general`, and saves to semantic
  memory.
- **`extract_from_history(max_turns)`** — Re-processes the last N conversation
  turns from episodic memory, extracting facts from `user_prompt` +
  `agent_response` pairs.
- **`_classify_fact(fact_text)`** — Static keyword-based classifier using
  `_PREFERENCE_KEYWORDS` and `_PROJECT_KEYWORDS` sets.

The agent currently hard-codes the model name (`local-model`) and endpoint
(`http://localhost:8080/v1`). The UI's `settings.model` / `settings.endpoint`
are **not** passed into the agent yet — `/model` switching affects display
only. This is tracked for Phase 3.

### Endpoint polling

A background `@work` `ping_loop` polls the configured endpoint via
`check_endpoint()` (pure function in `gaia.config`, no Textual dependency) and
updates the `connection_status` `reactive` attribute, which triggers the badge
colour change via `watch_connection_status`.

---

## Memory architecture

Memory is the agent's persistent identity — semantic facts, episodic events,
procedural definitions, and an audit log. All are accessed through the
`MemoryStore` abstract protocol (`base.py`).

### Protocol: `MemoryStore`

Defined in `src/gaia/core/memory/base.py`. Every backend implements:

**Lifecycle:**
- `initialize()` — Open connection, create tables/collections/directories
- `close()` — Release resources

**Semantic memory** (facts about the user):
- `semantic_insert(content, category, confidence, source, source_id)` → fact UUID
- `semantic_update(fact_id, new_content, new_confidence)`
- `semantic_delete(fact_id)`
- `semantic_search(query, limit)` → list of fact dicts

**Episodic memory** (conversation turns):
- `episodic_record(event_type, conversation_id, summary, payload)` → event UUID
- `episodic_search(time_range, event_types, query, limit)` → list of event dicts
- `episodic_get_conversation(conversation_id)` → ordered event list
- `episodic_prune(before_timestamp)` → count deleted

**Procedural memory** (skills, routines, policies):
- `procedure_register(procedure_id, name, description, safety_level, ...)`
- `procedure_get(procedure_id)` → dict or None
- `procedure_search(query, category)` → list of procedure dicts

**Audit log:**
- `audit_log(action, actor, target_type, target_id, detail)` — append-only

### Backend: FileMemoryStore (default)

File: `src/gaia/core/memory/file_store.py`

The default storage backend. Stores all four data types as individual Markdown
files with YAML-style frontmatter. No external dependencies.

**Directory layout** under `memory_dir` (default: `~/.gaia/memory`):

```
~/.gaia/memory/
├── semantic/      <fact_id>.md
├── episodic/      <event_id>.md
├── procedures/    <procedure_id>.md
└── audit/         <entry_id>.md
```

**File format:**

```yaml
---
fact_id: abc123
category: preference
confidence: 0.95
source: auto_extract
source_id: null
created_at: 2025-10-07T13:23:00+00:00
updated_at: 2025-10-07T13:23:00+00:00
---

User prefers Python over Rust for scripting tasks.
```

**Search strategy:** Simple keyword token matching (TF-ish, ranked by recency).
Stopwords are filtered. Fast for <1000 entries; degrades gracefully.

**Thread safety:** All file I/O is wrapped in `asyncio.to_thread()` so the
event loop stays responsive.

### Backend: ChromaDB (optional)

File: `src/gaia/core/memory/chroma_store.py`

Stores all three memory types and the audit log in a persistent ChromaDB instance.
ChromaDB handles vector embeddings internally, powering semantic search out of the box.

**Collections:**

| Collection | Purpose |
|---|---|
| `semantic_facts` | Fact documents with metadata |
| `episodic_events` | Event summaries with metadata |
| `procedure_entries` | Procedure name+description for vector search |
| `audit_log` | Action strings (append-only) |

**Path:** `~/.gaia/chroma` (created on first `initialize()`).

**Thread safety:** All ChromaDB calls are wrapped in `asyncio.to_thread()` so the
event loop stays responsive.

**Update limitation:** ChromaDB doesn't support in-place document updates, so
`semantic_update` reads current metadata, deletes by `fact_id`, then re-inserts
with the same ID.

**Episodic search strategy:** When a text query is provided, uses ChromaDB vector
search first then filters by `event_types` and `time_range`. Without a query,
fetches all documents and filters in Python.

### Backend selection

`GaiaTUIApp._init_memory()` reads `settings.memory.backend` and instantiates
the matching store (`FileMemoryStore` or `ChromaMemoryStore`). Falls back to
`None` if unset, invalid, or initialization fails — the app degrades gracefully.
The store is created in `__init__` and initialized on `on_mount()` via
`await self._memory.initialize()`.

---

## Configuration

`settings.json` (created with defaults if absent) drives the UI. The format:

```json
{
  "model": "qwen2.5-coder:32b",
  "endpoint": "http://localhost:11434",
  "encrypted": true,
  "memory_dir": "~/.gaia/memory",
  "memory": {
    "backend": "file"
  },
  "system_prompt": "..."
}
```

- `DEFAULT_SETTINGS` is the fallback dict; `load_config()` merges disk values over it.
- `load_config()` returns a `Settings` frozen dataclass (with nested `MemoryConfig`).
- `save_config()` accepts a `Settings` dataclass and serialises via `to_dict()`.
- `Settings.from_dict()` validates the memory backend (invalid values default to `"file"`).
- `AVAILABLE_MODELS` and `SLASH_COMMANDS` drive the `/model` dropdown and help overlay.
- Editable at runtime via `/settings` (a modal) or `/model <name>`.

### `Settings` dataclass

```python
@dataclass(frozen=True)
class MemoryConfig:
    backend: str = "file"        # "file" or "chroma"
    memory_dir: str = "~/.gaia/memory"

@dataclass(frozen=True)
class Settings:
    model: str = "qwen2.5-coder:32b"
    endpoint: str = "http://localhost:11434"
    encrypted: bool = True
    system_prompt: str = "..."
    memory: MemoryConfig = field(default_factory=MemoryConfig)
```

Immutable (frozen) — callers reassign the whole `self.settings` object when
config changes (`/model` reassigns via `Settings.from_dict()`).

---

## Key files

### `src/gaia/core/agent/base.py`

The heart of the AI logic. Contains:
- `FactEntry` / `FactResult` pydantic models for structured extraction output
- `_make_extraction_agent()` — builds a dedicated pydantic-ai agent with
  structured output for fact extraction
- `Gaia` class — the memory-aware agent harness

### `src/gaia/gaia_tui_app.py`

The Textual application. Key classes and methods:
- `ChatTurn` — wrapper around `Static` for chat messages
- `GaiaTUIApp` — main app with `connection_status` reactive, CSS, and bindings
- `_init_memory()` — backend selection on startup
- `ping_loop()` — `@work` background endpoint polling
- `run_agent_execution()` — `@work(exclusive=True)` streaming loop
- `handle_slash_command()` — `match`/`case` dispatch for all slash commands
- `_start_fact_extraction()` / `_run_extraction()` / `_do_mount_extraction_status()`
  — post-turn background fact extraction pipeline

### `src/gaia/config.py`

Pure config helpers (no Textual dependency):
- `DEFAULT_SETTINGS` — fallback dict of defaults
- `AVAILABLE_MODELS` — list of `{arg, desc}` dicts
- `SLASH_COMMANDS` — list of `{cmd, desc}` dicts
- `Settings` / `MemoryConfig` — frozen dataclasses with `from_dict()` / `to_dict()`
- `load_config()` → `Settings` — reads and validates settings from disk
- `save_config(settings: Settings)` → `None` — persists to JSON
- `check_endpoint()` — async network ping (threaded via `run_in_executor`)
- `parse_slash_command()` — splits `/cmd arg` into `(cmd, arg)`
- `is_exit_command()` / `is_slash_input()` — helpers

### `src/gaia/core/memory/file_store.py`

Flat-file `MemoryStore` implementation. Each fact/event/procedure/audit entry is
a separate Markdown file with YAML frontmatter. Search uses keyword token
matching. No external dependencies.

### `src/gaia/core/memory/chroma_store.py`

ChromaDB-based `MemoryStore` implementation. Uses vector embeddings for semantic
search. Thread-safe via `asyncio.to_thread()` wrapping.

---

## Dependencies

From `pyproject.toml`:

| Dependency | Purpose |
|---|---|
| `chromadb>=1.5.9` | Optional vector store (ChromaDB backend) |
| `pydantic>=2.13.5` | Data validation, structured LLM output |
| `pydantic-ai>=2.40.0` | Agent framework, streaming, structured output |
| `pydantic-ai-harness>=0.29.0` | Testing harness for pydantic-ai agents |
| `ruff>=0.16.9` | Linter (dev) |
| `textual>=8.2.8` | Terminal UI framework |

Dev: `pytest`, `pytest-asyncio`

---

## Known gaps / TODO

1. **Agent not wired to settings.** `Gaia.__init__` hardcodes the model
   (`'local-model'`), endpoint (`http://localhost:8080/v1`), and
   `api_key='not-needed'`. The UI's `settings.model` / `settings.endpoint`
   are not passed into the agent. *(Tracked for Phase 3.)*
2. **Endpoint defaults inconsistent.** `Gaia` uses `http://localhost:8080/v1`;
   `DEFAULT_SETTINGS` uses `http://localhost:11434` (Ollama).
3. **Encryption is aspirational.** The `encrypted` toggle and "SQLCipher Vault"
   copy exist in the UI, but SQLCipher is not a dependency — encryption is
   not yet implemented.
4. **`src/app/__init__.py`** is stale and empty — candidate for deletion.
5. **`src/plugins/__init__.py`** is empty — reserved for a future plugin system.
6. **Memory management.** No compaction, pruning, or optimization of stored
   facts/events yet.

---

## Change history

| Date | What changed |
|---|---|
| 2025-10-08 (Phase 0-1) | Dropped SQLite backend. Added `FileMemoryStore` — flat markdown files with YAML frontmatter and keyword search. Replaced `memory_db`/`"sqlite"` config with `memory_dir`/`"file"`. |
| 2025-10-08 (Phase 2) | Added `Settings` and `MemoryConfig` frozen dataclasses. `load_config()` now returns `Settings`, `save_config()` accepts `Settings`. All callers updated to dot-notation. `/model` command reassigns settings via `Settings.from_dict()`. |
| 2025-10-08 | Initial architecture doc — documented SQLite+FTS5 and ChromaDB backends, `MemoryStore` ABC protocol, config flow, Gaia agent, slash commands. |
