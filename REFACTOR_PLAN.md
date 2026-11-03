# G.A.I.A. Refactor Plan

> Phased refactoring for extensibility, maintainability, and LLM-agent readability.
> Each phase is independently completable and tested. Run `uv run pytest` after each phase.

---

## Architecture decision: drop SQLite

SQLite was scaffolded but never worked in practice — the `type: ignore` soup (24 occurrences), broken `asyncio.run()` in `__init__`, the SQLCipher encryption pragma that does nothing without a SQLCipher dependency. For a "private, local, zero-telemetry" assistant, flat markdown files are more aligned:

- **Transparent** — human-readable, editable, version-controllable
- **Zero dependencies** — no database engine, no schema migrations
- **Searchable** — `grep`/`ripgrep` works out of the box
- **ChromaDB optional** — vector search as a plugin, not a requirement

---

## Phase 0 — Housekeeping

**Goal:** Remove dead code so we're not accidentally touching abandoned artifacts.

### Changes

| Action | Where | Why |
|---|---|---|
| Delete `src/app/__init__.py` | `src/app/` | Stale, empty |
| Delete `src/gaia/core/base/__init__.py` | `src/gaia/core/base/` | Empty, reserved |
| Delete `src/gaia/core/__init__.py` | `src/gaia/core/` | Empty, reserved |
| Delete `src/gaia/core/memory/sqlite_store.py` | `src/gaia/core/memory/` | **Dropped** — SQLite memory store never worked |
| Delete `src/gaia/core/memory/episodic.py` | `src/gaia/core/memory/` | Empty |
| Delete `src/gaia/core/memory/shortterm.py` | `src/gaia/core/memory/` | Empty |
| Delete `src/gaia/core/memory/longterm.py` | `src/gaia/core/memory/` | Empty |
| Delete `src/gaia/core/memory/management.py` | `src/gaia/core/memory/` | 8-line stub |
| Delete `fibonacci.py` | repo root | Development experiment |
| Delete `pong.py` | repo root | Development experiment |
| Delete `test_fts_fix.py` | repo root | Superseded by `test_memory_store.py` |
| Delete `gaia-memory.db` | repo root | Generated runtime file |
| Delete `test_memory_store.py` | `tests/` | Tests SQLite store only — will be replaced |
| Add `*.db` to `.gitignore` | repo root | Prevent future committed databases |

### Keep

| File | Why |
|---|---|
| `src/gaia/core/memory/base.py` | `MemoryStore` ABC — kept, but simplified for flat-file backend |
| `src/gaia/core/memory/chroma_store.py` | ChromaDB backend — kept as optional plugin |
| `src/gaia/core/memory/__init__.py` | Re-exports — updated to export `FileMemoryStore` |
| `src/gaia/core/agent/__init__.py` | Re-exports `Gaia` |

**Tests:** No functional impact. All deleted files are empty stubs, experiments, or tests for the now-removed SQLite store.

---

## Phase 1 — Replace SQLite with markdown file-based memory store

**Goal:** Default memory store is a flat markdown/YAML directory. ChromaDB is optional.

### 1a. New file: `src/gaia/core/memory/file_store.py`

A flat-file `MemoryStore` implementation using the `~/.gaia/memory/` directory:

```
~/.gaia/memory/
├── semantic/
│   ├── <fact_id>.md          # Each fact is one file
│   └── ...
├── episodic/
│   ├── <event_id>.md         # Each event is one file
│   └── ...
├── procedures/
│   ├── <procedure_id>.md     # Each procedure is one file
│   └── ...
└── audit/
    ├── <entry_id>.md         # Audit entries, append-only
    └── ...
```

**File format (YAML frontmatter + markdown body):**

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

**Search strategy (no database = no FTS5):**
- **Semantic search:** Load all `semantic/*.md` files, parse frontmatter, do keyword matching across the body text. Return top-N by score (simple TF-ish: count matching keywords, rank by recency). Fast for <1000 facts; degrades gracefully.
- **Episodic search:** Load `episodic/*.md` files, filter by event_type, time_range, keyword match on summary. Sort by timestamp.
- **Procedural search:** Load `procedures/*.md`, keyword match on name/description.
- **Future enhancement:** Hook into ChromaDB's `semantic_search` if the user enables it (hybrid mode: keyword + vector).

**Implementation notes:**
- Use `pathlib` for all file operations
- Parse YAML frontmatter with a simple regex (no `pyyaml` dependency — keep it light)
- Write files atomically (write to temp, then rename)
- All methods are sync (file I/O is fast for flat files), wrap in `asyncio.to_thread()` in the caller if needed

### 1b. Simplify `MemoryStore` ABC (`base.py`)

The current ABC has 16 abstract methods across 4 domains. Keep them all — the protocol stays the same. But remove the FTS5-specific assumptions from docstrings and method signatures.

### 1c. Update `chroma_store.py`

ChromaDB backend stays, but change its import path context. It's now one of two options, not "the alternative to SQLite." The `__init__.py` re-exports both.

### 1d. Update config defaults

**Before:**
```python
DEFAULT_SETTINGS: Dict[str, Any] = {
    ...
    "memory_db": "gaia-memory.db",
    "memory": {
        "backend": "sqlite",
    },
}
```

**After:**
```python
DEFAULT_SETTINGS: dict[str, Any] = {
    ...
    "memory_dir": "~/.gaia/memory",
    "memory": {
        "backend": "file",       # "file" or "chroma"
    },
}
```

The `memory_db` key goes away. `memory_dir` points to the flat-file directory. ChromaDB stores to `~/.gaia/chroma` as before.

### Tests to add:
- `test_file_store_insert_and_list` — create a fact file, list all facts
- `test_file_store_search_keyword` — keyword match across fact bodies
- `test_file_store_search_empty_query` — returns all (or raises)
- `test_file_store_update_modifies_file` — update a fact, verify file content
- `test_file_store_delete_removes_file` — delete, verify file gone
- `test_file_store_episodic_lifecycle` — record, retrieve, prune events
- `test_file_store_procedure_lifecycle` — register, get, search
- `test_file_store_audit_append_only` — append entries, verify order
- `test_file_store_concurrent_safety` — two inserts, verify no corruption
- `test_chroma_store_still_works` — verify ChromaDB backend unchanged

---

## Phase 2 — Centralize config with validation

**Goal:** One source of truth for config. No more duplicated `SETTINGS_FILE`.

### 2a. Move `SETTINGS_FILE` into `config.py`

**Before:**
```python
# gaia_tui_app.py
SETTINGS_FILE = Path("settings.json")  # DUPLICATE
```

**After:** Export `SETTINGS_FILE = Path("settings.json")` from `config.py`. Delete the line from `gaia_tui_app.py`.

### 2b. Add a `Settings` dataclass

```python
from dataclasses import dataclass, field

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

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Settings":
        """Merge dict over defaults, validate, return Settings."""
        mem_cfg = d.get("memory", {})
        backend = mem_cfg.get("backend", "file")
        if backend not in ("file", "chroma"):
            backend = "file"
        return cls(
            model=d.get("model", cls.model),
            endpoint=d.get("endpoint", cls.endpoint),
            encrypted=d.get("encrypted", cls.encrypted),
            system_prompt=d.get("system_prompt", cls.system_prompt),
            memory=MemoryConfig(
                backend=backend,
                memory_dir=d.get("memory_dir", "~/.gaia/memory"),
            ),
        )
```

### 2c. Wire `load_config()` → `Settings`

Change return type from `Dict[str, Any]` to `Settings`. Keep JSON disk format identical.

### 2d. Update `save_config()` → accepts `Settings`

Dataclass → dict on write. JSON format unchanged.

**Migration note:** `GaiaTUIApp.__init__` uses dict-style access everywhere:
```python
# Before
self.settings.get("model")
# After
self.settings.model
```

**Tests to add:**
- `test_settings_from_dict_valid` — round-trips known-good dict
- `test_settings_from_dict_invalid_backend_defaults_to_file`
- `test_settings_from_dict_missing_keys_uses_defaults`
- `test_settings_save_roundtrip`

---

## Phase 3 — Wire agent to settings (dependency injection)

**Goal:** `/model` actually changes the model. No more hardcoded endpoints in Gaia.

### 3a. Make Gaia accept config parameters

**Before:**
```python
class Gaia:
    DEFAULT_MODEL = "local-model"
    DEFAULT_ENDPOINT = "http://localhost:8080/v1"

    def __init__(self, system_prompt: str, model_name: str = "", memory: MemoryStore | None = None):
```

**After:**
```python
class Gaia:
    def __init__(
        self,
        system_prompt: str,
        model_name: str = "local-model",
        endpoint: str = "http://localhost:8080/v1",
        api_key: str = "not-needed",
        memory: MemoryStore | None = None,
    ):
```

### 3b. Wire GaiaTUIApp

**Before:**
```python
self.__core_agent = Gaia(
    self.settings.get("system_prompt"),
    memory=self._memory,
)
```

**After:**
```python
self.__core_agent = Gaia(
    system_prompt=self.settings.system_prompt,
    model_name=self.settings.model,
    endpoint=self.settings.endpoint,
    memory=self._memory,
)
```

### 3c. Hot-reload model on `/model`

```python
class Gaia:
    def reconfigure(self, model_name: str = "", endpoint: str = "") -> None:
        """Swap the underlying model without rebuilding the agent."""
        model = OpenAIChatModel(
            model_name or self._model_name,
            provider=OpenAIProvider(
                base_url=endpoint or self._endpoint,
                api_key=self._api_key,
            ),
        )
        self.__core_agent = Agent(model, instructions=self._system_prompt)
```

### Tests to add:
- `test_gaia_accepts_custom_model_and_endpoint`
- `test_gaia_reconfigure_changes_model`
- `test_gaiatuiapp_model_switch_reconfigures_agent`

---

## Phase 4 — Memory factory with lazy initialization

**Goal:** Remove `asyncio.run()` from `__init__`. Defer store creation to the event loop.

### 4a. Create `MemoryFactory`

```python
class MemoryFactory:
    @staticmethod
    def create(settings: Settings) -> MemoryStore | None:
        """Create a store but do NOT initialize it."""
        match settings.memory.backend:
            case "chroma":
                return ChromaMemoryStore()
            case "file":
                memory_dir = Path(settings.memory.memory_dir).expanduser()
                return FileMemoryStore(memory_dir)
            case _:
                return None
```

### 4b. Defer initialization to `on_mount`

```python
# In __init__:
self._memory = MemoryFactory.create(self.settings)

# In on_mount:
if self._memory is not None:
    await self._memory.initialize()
```

This eliminates both `asyncio.run()` calls.

### Tests to add:
- `test_memory_factory_creates_file_by_default`
- `test_memory_factory_creates_chroma_when_configured`
- `test_memory_factory_returns_none_for_unknown_backend`

---

## Phase 5 — Split the monolith (gaia_tui_app.py)

**Goal:** `gaia_tui_app.py` goes from 857 lines to ~200.

### 5a. Extract slash command handlers → `src/gaia/slash_commands.py`

```python
class SlashCommandHandler:
    def __init__(self, ctx: CommandContext): ...
    async def handle(self, cmd: str, arg: str): ...
```

`CommandContext` dataclass holds references: `chat_box`, `settings`, `agent`, `memory`, `debug_mode`, `debug_mode_setter`.

Each command gets its own method: `_handle_model`, `_handle_settings`, `_handle_clear`, `_handle_debug`, `_handle_status`, `_handle_remember`, `_handle_extract`, `_handle_extract_debug`, `_handle_exit`.

### 5b. Extract streaming loop → `src/gaia/streaming.py`

```python
class AgentStreamer:
    def __init__(self, chat_box, agent, model_name): ...
    async def run(self, prompt, on_extract): ...
```

### 5c. Extract memory lifecycle → `src/gaia/memory_lifecycle.py`

```python
class MemoryLifecycle:
    @staticmethod
    async def initialize(store): ...
    @staticmethod
    async def close(store): ...
```

### 5d. What stays in `gaia_tui_app.py` (~200 lines)

- `ChatTurn` class
- `GaiaTUIApp` skeleton: `compose()`, `on_mount`, `BINDINGS`, `CSS`
- `watch_connection_status`, turn navigation
- `force_exit()`, `action_quit()`
- `on_input_submitted` (dispatches to slash or streaming)

### Tests:
- `test_slash_command_dispatch_model`
- `test_slash_command_dispatch_remember`
- `test_agent_streamer_animates_waiting`

---

## Phase 6 — Type safety improvements

**Goal:** Reduce `type: ignore` count from 24 to <5.

### 6a. chroma_store.py — unwrap `_client`

Add `_require_client()` helper pattern (same as described before).

### 6b. command_input.py — fix `_popup` typing

Replace `reactive(None)` with `self._popup: OptionList | None = None` in `__init__`, then check `is not None` before each access.

### 6c. config.py — fix `urlopen` type

Add `assert response.status is not None` instead of `type: ignore`.

### 6d. Verify with ruff

```bash
uv run ruff check src/
```

Target: zero warnings.

---

## Phase 7 — System prompt from AGENT.md

**Goal:** Load the version-controlled system prompt from `AGENT.md` instead of hardcoding a shorter inline version.

```python
def _load_system_prompt() -> str:
    prompt_path = Path(__file__).resolve().parent.parent.parent / "AGENT.md"
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8").strip()
    return "..."  # inline fallback
```

---

## Phase 8 — Logging improvements

**Goal:** No logging side effects in module import.

Move `logging.basicConfig()` from `gaia_tui_app.py` to `main()` in `cli.py`:

```python
def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )
    GaiaTUIApp().run()
```

---

## Target state

After all phases:

| Metric | Before | After |
|---|---|---|
| `gaia_tui_app.py` | 857 lines | ~200 lines |
| `type: ignore` comments | 24 | <5 |
| `asyncio.run()` in app | 2 | 0 |
| Empty stub files | 7 | 0 |
| Config duplication | 2 sources | 1 source |
| Agent hardcoded values | 3 (model, endpoint, api_key) | 0 (all from Settings) |
| Memory default backend | Broken SQLite | Markdown files |
| Memory optional backend | ChromaDB | ChromaDB (unchanged) |
| Files per module | Monolithic TUI | 6 focused modules |

---

## Execution order

```
Phase 0 → Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5 → Phase 6 → Phase 7 → Phase 8
```

- Phase 0 is prerequisite cleanup.
- Phase 1 is the biggest architectural change (drop SQLite, add flat-file store).
- Phases 2–4 are config + wiring (functional correctness).
- Phase 5 is the biggest structural change (split the monolith).
- Phases 6–8 are quality polish.

---

## Verification

After each phase:

```bash
uv run pytest                    # all tests pass
uv run ruff check src/           # no lint errors
uv run python main.py            # app still launches
```

The app should functionally behave the same after each phase — only internal structure changes.
