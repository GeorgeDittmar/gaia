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
├── fibonacci.py                # Development experiment / test file
├── pong.py                     # Development experiment / test file
├── test_fts_fix.py             # FTS5 apostrophe/dot handling regression test
├── gaia-memory.db              # SQLite memory database (generated at runtime)
├── src/
│   ├── gaia/                   # The "gaia" package (hatchling-managed)
│   │   ├── __init__.py         # Re-exports: `GaiaTUIApp` from gaia_tui_app
│   │   ├── cli.py              # CLI entry point — installed as `gaia` console script
│   │   ├── config.py           # Config loading, endpoint checking, slash parsing
│   │   ├── gaia_tui_app.py     # Main Textual app (GaiaTUIApp) — the TUI entrypoint
│   │   ├── command_input.py    # CommandInput widget — slash-command autocomplete
│   │   ├── help_modal.py       # HelpModal — F1/? keyboard shortcut reference
│   │   └── settings_modal.py   # SettingsModal — /settings pop-up dialog
│   │   ├── core/               # Core engine
│   │   │   ├── __init__.py     # (empty — reserved)
│   │   │   ├── agent/
│   │   │   │   ├── __init__.py # Re-exports: `Gaia` from base
│   │   │   │   └── base.py     # `Gaia` class — pydantic-ai agent harness + fact extraction
│   │   │   ├── base/           # Placeholder for shared base types (empty — reserved)
│   │   │   └── memory/         # Memory layer — TWO FULL BACKENDS IMPLEMENTED
│   │   │       ├── __init__.py # Exports: MemoryStore, SQLiteMemoryStore, ChromaMemoryStore
│   │   │       ├── base.py     # Abstract MemoryStore protocol (ABC)
│   │   │       ├── sqlite_store.py   # Default backend — SQLite + FTS5
│   │   │       ├── chroma_store.py   # Optional backend — ChromaDB vector search
│   │   │       ├── management.py     # Memory management / consolidation (scaffolded)
│   │   │       ├── episodic.py       # (empty — reserved)
│   │   │       ├── shortterm.py      # (empty — reserved)
│   │   │       └── longterm.py       # (empty — reserved)
│   ├── app/
│   │   └── __init__.py         # STALE — empty file, left as deletion candidate
│   └── plugins/
│       └── __init__.py         # Empty — reserved for a future plugin system
```

---

## How it fits together

### Entry points

- `uv run python main.py` or `gaia` (installed console script) both launch
  `GaiaTUIApp()`.
- `src/cli.py` is the installed entry point; `main.py` in the repo root is a
  13-line stub delegating to `GaiaTUIApp`.

### Config flow

`GaiaTUIApp.__init__` calls `load_config()` from `gaia.config`, which merges
`settings.json` on disk over `DEFAULT_SETTINGS` (disk values win). Config
keys relevant to memory:

| Key | Type | Default | Description |
|---|---|---|---|
| `memory_db` | `str` | `"gaia-memory.db"` | Path to the SQLite database |
| `memory.backend` | `"sqlite"` or `"chroma"` | `"sqlite"` | Storage backend to use |

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

The agent hard-codes the model name (`local-model`) and endpoint
(`http://localhost:8080/v1`). The UI's `settings["model"]` / `settings["endpoint"]`
are **not** currently passed into the agent — `/model` switching affects display
only. This is a known gap.

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
- `initialize()` — Open connection, create tables/collections
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

### Backend: SQLite + FTS5 (default)

File: `src/gaia/core/memory/sqlite_store.py`

The default storage backend. Stores all four data types (semantic, episodic,
procedural, audit) in a single SQLite database with FTS5 virtual tables for
full-text search.

**Schema:**

| Table | Purpose |
|---|---|
| `semantic_facts` | Facts with category, confidence, source; FTS5 on content |
| `episodic_events` | Conversation turns with event_type enum; FTS5 on indexed_content |
| `procedure_entries` | Skills/routines with safety_level enum; FTS5 on name+description |
| `audit_log` | Append-only audit trail |

**FTS5 details:**
- The `_fts_query(query)` function sanitizes user queries: dots are wrapped in
  double quotes (phrase match), apostrophes are replaced with spaces (splitting
  into separate terms), and trailing question marks are stripped.
- `semantic_search` uses FTS5 BM25 ranking with a keyword-match fallback when the
  query consists entirely of stopwords (e.g. "what is my name?").

**Encryption:**
When `encrypted=True` in settings and SQLCipher is available, the database is
encrypted with AES-256 via `PRAGMA key = 'gaia-local-encrypt-v1'`. In practice,
this uses the standard `sqlite3` module — SQLCipher is not in the dependency list,
so encryption is currently aspirational (the PRAGMA is accepted but has no effect
without SQLCipher).

**Path:** Configured via `settings["memory_db"]` (default: `gaia-memory.db`).

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

`GaiaTUIApp._init_memory()` reads `settings["memory"]["backend"]` and instantiates
the matching store. Falls back to `"sqlite"` if unset or invalid. Uses
`asyncio.run()` for the one-shot `initialize()` call since no event loop exists
during `__init__`. Memory is optional — if initialization fails, the app degrades
gracefully with `self._memory = None`.

### Memory management

File: `src/gaia/core/memory/management.py`

Scaffolded but not yet implemented. Contains a `MemoryManagement` class with a
placeholder `consolidation()` method intended for future memory compaction and
optimization.

---

## Configuration

`settings.json` (created with defaults if absent) drives the UI:

```json
{
  "model": "qwen2.5-coder:32b",
  "endpoint": "http://localhost:11434",
  "encrypted": true,
  "memory_db": "gaia-memory.db",
  "memory": {
    "backend": "sqlite"
  },
  "system_prompt": "..."
}
```

- `DEFAULT_SETTINGS` and `AVAILABLE_MODELS` are defined in `gaia.config`.
- On load, `load_config()` merges `settings.json` on disk over `DEFAULT_SETTINGS`
  (disk values win).
- Editable at runtime via `/settings` (a modal) or `/model <name>`.
- The system prompt in `AGENT.md` is the versioned copy; `DEFAULT_SETTINGS`
  contains a shorter inline version.

---

## Key files

### `src/gaia/core/agent/base.py`

The heart of the AI logic. Contains:
- `FactEntry` / `FactResult` pydantic models for structured extraction output
- `_make_extraction_agent()` — builds a dedicated pydantic-ai agent with
  structured output for fact extraction (same model, different instructions)
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
- `DEFAULT_SETTINGS` — dict of defaults
- `AVAILABLE_MODELS` — list of `{arg, desc}` dicts
- `SLASH_COMMANDS` — list of `{cmd, desc}` dicts
- `load_config()` / `save_config()` — persistence
- `check_endpoint()` — async network ping (threaded via `run_in_executor`)
- `parse_slash_command()` — splits `/cmd arg` into `(cmd, arg)`
- `is_exit_command()` / `is_slash_input()` — helpers

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
   `api_key='not-needed'`. The UI's `settings["model"]` / `settings["endpoint"]`
   are not passed into the agent.
2. **Endpoint defaults inconsistent.** `Gaia` uses `http://localhost:8080/v1`;
   `DEFAULT_SETTINGS` uses `http://localhost:11434` (Ollama).
3. **Encryption is aspirational.** The `encrypted` toggle and "SQLCipher Vault"
   copy exist in the UI, but SQLCipher is not a dependency — the PRAGMA has no
   effect with the standard `sqlite3` module.
4. **Memory management stub.** `management.py` has a placeholder `consolidation()`
   — no compaction, pruning, or optimization yet.
5. **`shortterm.py`, `longterm.py`, `episodic.py`** are empty files — reserved
   for future specialized memory layers.
6. **`src/gaia/core/base/` and `src/gaia/core/__init__.py`** are empty —
   reserved for future shared types.
7. **`src/app/__init__.py`** is stale and empty — candidate for deletion.
8. **`fibonacci.py` and `pong.py`** in the repo root are development experiments
   — unclear if they belong in the committed tree.

---

## Change history

| Date | What changed |
|---|---|
| 2025-10-08 | Added memory backend documentation — SQLite+FTS5 (`sqlite_store.py`) and ChromaDB (`chroma_store.py`) backends, `MemoryStore` ABC protocol, memory management (`management.py`), and backend selection logic in `GaiaTUIApp._init_memory()`. Updated config table with `memory_db` and `memory.backend` keys. Updated slash commands table with `/remember`, `/extract`, `/extract-debug`, `/debug`. Updated Gaia agent section to document `post_turn_extract`, `_extract_facts`, `extract_from_history`, and `FactResult` structured output. Added `AGENT.md` and `test_fts_fix.py` to repo layout. Marked memory as "two full backends implemented" instead of "scaffolded but empty". |
