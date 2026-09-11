# CLAUDE.md

Guidance for Claude Code (and other agents) working in this repository.

## What this project is

**G.A.I.A.** — *General AI Assistant* (v0.1.0). A **private, local, zero-telemetry
personal AI assistant** with a dark "cyberpunk" terminal UI. It is a **Textual**
TUI that talks to a **local LLM backend** (llama.cpp / `llama-server`, Ollama, or
any OpenAI-compatible endpoint) through **pydantic-ai**. The design goal is that
everything runs on the user's own machine — no cloud calls, no telemetry.

The UI copy leans into that identity ("PRIVATE. OPEN. YOURS.", "SQLCipher Vault",
"Zero-Telemetry Mode"). Treat the tone of user-facing strings as intentional.

- Language: **Python 3.14** (see `.python-version`)
- Package/dep manager: **uv** (`uv.lock` is committed)
- License: **Apache 2.0**

## Repository layout

```
gaia/
├── main.py                     # LIVE entrypoint — the full Textual TUI (GaiaTUIApp)
├── settings.json               # Runtime config (auto-created on first run)
├── pyproject.toml              # Project metadata + dependencies (uv)
├── uv.lock                     # Locked dependencies
├── README.md                   # One-liner: "G.A.I.A / General AI Assistant"
└── src/
    ├── gaia/                   # The "gaia" package (implicit namespace package —
    │   │                       #   there is NO src/gaia/__init__.py)
    │   └── core/               # Core engine (this is where the agent lives)
    │       └── agent/
    │           ├── __init__.py # Re-exports: `from gaia.core.agent.base import Gaia`
    │           └── base.py     # `Gaia` class — the pydantic-ai agent harness
    │       ├── memory/         # Memory layer — SCAFFOLDED BUT EMPTY (stubs)
    │       │   ├── base.py
    │       │   ├── shortterm.py
    │       │   └── longterm.py
    │       └── base/           # Placeholder for shared base types (empty)
    ├── app/
    │   └── __init__.py         # STALE: an older, simpler iteration of the TUI
    │                           #   (uses mock/simulated tokens, no real agent call)
    │                           #   — superseded by main.py
    └── plugins/
        └── __init__.py         # Empty — reserved for a future plugin system
```

> **Important:** `main.py` (at the repo root) is the real, current entrypoint and
> the one that calls the agent. `src/app/__init__.py` is an **earlier version of the
> same UI** left in the tree — don't treat it as the source of truth; it has
> hardcoded "simulated" responses and predates the real `Gaia` integration.

## How it fits together

- `main.py` → `GaiaTUIApp` (a `textual.app.App`). On startup it builds a `Gaia()`
  core agent and a config dict loaded from `settings.json`.
- User types a message → `on_input_submitted` mounts a "You:" turn, then
  `run_agent_execution` (a `@work` task) streams the reply token-by-token by
  iterating `Gaia.ainteract(prompt)`.
- `Gaia` (`src/gaia/core/agent/base.py`) wraps a pydantic-ai `Agent` backed by an
  `OpenAIChatModel` pointed at a local OpenAI-compatible endpoint. `ainteract` is an
  async generator that yields text deltas via `run_stream` / `stream_text(delta=True)`.
- A background `@work` `ping_loop` polls the configured endpoint and updates the
  "LLM: ONLINE / UNREACHABLE" status badge via a `reactive` attribute.

## How to run

Dependencies are managed with **uv**.

```bash
uv sync                 # install the locked dependencies into the venv
uv run python main.py   # launch the TUI
```

Notes on the import path:
- `main.py` does `from gaia.core.agent import Gaia`, but `gaia` lives under `src/`
  and is an **implicit namespace package** (no `src/gaia/__init__.py`, and
  `pyproject.toml` defines no build backend / package layout). So the code relies on
  `src/` being on `sys.path`. If `uv run python main.py` doesn't resolve `gaia`,
  run it with the source dir on the path, e.g. `PYTHONPATH=src uv run python main.py`.

The app needs a **local LLM endpoint already running** (see `settings.json`). The
header shows `CHECKING…` → `ONLINE` / `UNREACHABLE` based on the live ping.

## Configuration

`settings.json` (created with defaults if absent) drives the UI:

```json
{
  "model": "...",            // model name shown in the UI / passed to /model
  "endpoint": "http://...",  // base URL of the local LLM server
  "encrypted": true,         // SQLCipher vault toggle (see Current state)
  "system_prompt": "..."     // system prompt for the agent
}
```

- `DEFAULT_SETTINGS` in `main.py` is the fallback; on load, disk values are merged
  over defaults.
- Editable at runtime via `/settings` (a modal) or `/model <name>`.

## Coding conventions

Match the existing style rather than introducing new patterns:

- **Modern Python 3.14.** Use `X | None` (not `Optional[X]`), f-strings, and
  `match`/`case` for command dispatch. Keep type hints on function/method
  signatures (e.g. `-> None`, `-> Dict[str, Any]`).
- **Naming:** `PascalCase` classes (`GaiaTUIApp`, `SettingsModal`, `Gaia`),
  `snake_case` functions/methods, `UPPER_SNAKE` module-level constants
  (`SETTINGS_FILE`, `DEFAULT_SETTINGS`, `AVAILABLE_MODELS`, `SLASH_COMMANDS`).
  Private state uses double-underscore name mangling (e.g. `self.__core_agent`).
- **Textual idioms:** build UI in `compose()` with `yield`; put styling in a
  class-level `CSS = """..."""` string; keybindings in a class-level `BINDINGS`
  list; lifecycle in `on_mount`; background/async work with `@work(exclusive=...,
  thread=...)`; UI state that should reactively update with `reactive(...)`.
- **Rich/Textual markup strings** are used inline for styled text, e.g.
  `"[bold #ff00ff]You:[/bold #ff00ff] ..."`. The palette is consistent:
  cyan `#00f0ff` (agent/positive), magenta `#ff007f` (user/accent),
  muted `#8b949e`, backgrounds `#0d1117`/`#161b22`/`#21262d`.
- **Async-first.** Streaming and I/O are `async`/`await`; blocking work (e.g. the
  `urllib` connection ping) is pushed to a thread via
  `loop.run_in_executor(...)`.
- **Docstrings:** present on some public methods but sparse overall — keep them
  short and optional; don't feel obligated to add them to trivial helpers.
- **Slash commands** live in the `SLASH_COMMANDS` list (for autocomplete + the help
  overlay) and are dispatched in `handle_slash_command` (`match`/`case`). Keep the
  two in sync when adding a command.

## Current state / known gaps

This is an early, mid-refactor codebase. Be aware before assuming features exist:

- **The agent is not yet wired to settings.** `Gaia.__init__` hardcodes the model
  (`'local-model'`), endpoint (`http://localhost:8080/v1`), and `api_key='not-needed'`,
  and there's a `# todo use a model router`. The UI's `settings["model"]` /
  `settings["endpoint"]` are not currently passed into the agent, so `/model`
  switching affects display only.
- **Endpoint defaults are inconsistent** across the code: `Gaia` uses `:8080/v1`,
  `DEFAULT_SETTINGS` uses `http://localhost:11434` (Ollama), and the checked-in
  `settings.json` uses `:8080`. Pick one source of truth when touching this.
- **Memory is a stub.** `src/gaia/core/memory/` (base/shortterm/longterm) is
  scaffolded but empty — no persistence is implemented yet.
- **Encryption / SQLCipher is not implemented.** The `encrypted` toggle and
  "SQLCipher Vault" / "AES-256" copy exist in the UI and `/status`, but there is no
  actual encryption code. It's aspirational for now.
- **No test suite, linter, or formatter config** is set up yet.
- **`src/app/__init__.py`** is a stale duplicate of the UI — a candidate for
  deletion once confirmed unused.

## Suggested conventions for new work

- Add agent/memory functionality under `src/gaia/core/...` (that's the intended
  home), not in `main.py`.
- Keep `main.py` focused on the Textual UI and keep LLM logic in the `Gaia` agent.
- When adding a slash command, update both `SLASH_COMMANDS` and the `match` dispatch.
- Prefer reading config from `settings.json` over hardcoding endpoint/model.
