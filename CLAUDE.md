# CLAUDE.md

Guidance for Claude Code (and other agents) working in this repository.

## Agent Pipeline

When the user asks you to build, fix, or modify code, **do not write code yourself**. Route the task through the multi-agent pipeline:

1. **Coder** — writes simple, maintainable Python code. Runs first.
2. **Code Reviewer** — checks for clarity, simplicity, and no over-engineering. Rejects or approves.
3. **QA** — runs tests, tries to break the code, verifies against the spec. Final approval gate.

**How to invoke:**
```
Agent(subagent_type="coder", prompt="...the task...")
```
Then send the output to:
```
Agent(subagent_type="code-reviewer", prompt="Review this: ...")
```
Then send the approved code to:
```
Agent(subagent_type="qa", prompt="QA this: ...")
```

The full pipeline is defined in `.claude/agents/`. Agents are available project-locally and globally. If a subagent rejects, fix the issues and resubmit through the full pipeline again.

> **Rule of thumb**: If you ask "would I be embarrassed if my name was on this?" and the answer is yes, it failed.

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
- Build system: **hatchling** (see `pyproject.toml`)
- License: **Apache 2.0**

## Repository layout

```
gaia/
├── main.py                     # Minimal stub entrypoint — delegates to GaiaTUIApp
├── settings.json               # Runtime config (auto-created on first run)
├── spec.md                     # Project specification / requirements doc
├── qwen-fixed.jinja            # Qwen template (used by some local backends)
├── docs/                       # Documentation directory (reserved)
├── tests/                      # Test suite (pytest, see below)
├── pyproject.toml              # Project metadata + dependencies + hatchling build
├── uv.lock                     # Locked dependencies
├── README.md                   # One-liner: "G.A.I.A / General AI Assistant"
└── src/
    ├── gaia/                   # The "gaia" package (hatchling-managed)
    │   ├── __init__.py         # Re-exports: `GaiaTUIApp` from gaia_tui_app
    │   ├── cli.py              # CLI entry point — installed as `gaia` console script
    │   ├── config.py           # Config loading, endpoint checking, slash parsing
    │   ├── gaia_tui_app.py     # Main Textual app (GaiaTUIApp) — the TUI entrypoint
    │   ├── command_input.py    # CommandInput widget — slash-command autocomplete
    │   ├── help_modal.py       # HelpModal — F1/? keyboard shortcut reference
    │   └── settings_modal.py   # SettingsModal — /settings pop-up dialog
    │   ├── core/               # Core engine
    │   │   ├── __init__.py     # (empty)
    │   │   └── agent/
    │   │       ├── __init__.py # Re-exports: `Gaia` from base
    │   │       └── base.py     # `Gaia` class — pydantic-ai agent harness
    │   │   ├── memory/         # Memory layer — SCAFFOLDED BUT EMPTY (stubs)
    │   │   │   ├── __init__.py # (empty)
    │   │   │   ├── base.py
    │   │   │   ├── shortterm.py
    │   │   │   └── longterm.py
    │   │   └── base/           # Placeholder for shared base types (empty)
    ├── app/
    │   └── __init__.py         # STALE — empty file, left as a deletion candidate
    └── plugins/
        └── __init__.py         # Empty — reserved for a future plugin system
```

> **Important:** The entrypoint is `src/gaia/cli.py` (installed as the `gaia` console
> script via `pyproject.toml` → `[project.scripts]`). The root `main.py` is a 13-line
> stub that delegates to `GaiaTUIApp`. `src/app/__init__.py` is now empty — it was
> an older TUI iteration, superseded by the module-split in `src/gaia/`.

## How it fits together

- **Entry points:** `uv run python main.py` or `gaia` (installed console script) both
  launch `GaiaTUIApp()`.
- **Config flow:** `GaiaTUIApp.__init__` calls `load_config()` from `gaia.config`,
  which merges `settings.json` on disk over `DEFAULT_SETTINGS`.
- **User message:** `on_input_submitted` mounts a "You:" turn, then
  `run_agent_execution` (a `@work` task) streams the reply token-by-token by
  iterating `Gaia.ainteract(prompt)`.
- **Slash commands:** typed input starting with `/` is handled by `handle_slash_command`
  (`match`/`case`). The `SLASH_COMMANDS` list in `gaia.config` drives both the
  autocomplete in `CommandInput` and the help overlay in `HelpModal`.
- **`Gaia` agent** (`src/gaia/core/agent/base.py`) wraps a pydantic-ai `Agent` backed by
  an `OpenAIChatModel` pointed at a local OpenAI-compatible endpoint. `ainteract` is an
  async generator that yields text deltas via `run_stream` / `stream_text(delta=True)`.
- **Endpoint polling:** a background `@work` `ping_loop` polls the configured endpoint
  via `check_endpoint()` (pure function in `gaia.config`, no Textual dependency) and
  updates the `connection_status` `reactive` attribute, which triggers the badge colour
  change via `watch_connection_status`.

## How to run

Dependencies are managed with **uv**.

```bash
uv sync                 # install the locked dependencies into the venv
uv run python main.py   # launch the TUI
gaia                    # same — uses the installed console script
```

The `gaia` package is built via **hatchling** from `pyproject.toml` and is installed
in-editable by `uv sync`, so `from gaia.core.agent import Gaia` and
`from gaia.gaia_tui_app import GaiaTUIApp` both resolve without any `PYTHONPATH`
shenanigans.

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

- `DEFAULT_SETTINGS` and `AVAILABLE_MODELS` are defined in `gaia.config`.
- On load, `load_config()` merges `settings.json` on disk over `DEFAULT_SETTINGS`
  (disk values win).
- Editable at runtime via `/settings` (a modal) or `/model <name>`.

## Coding conventions

Match the existing style rather than introducing new patterns:

- **Modern Python 3.14.** Use `X | None` (not `Optional[X]`), f-strings, and
  `match`/`case` for command dispatch. Keep type hints on function/method
  signatures (e.g. `-> None`, `-> Dict[str, Any]`).
- **Naming:** `PascalCase` classes (`GaiaTUIApp`, `SettingsModal`, `Gaia`,
  `CommandInput`, `HelpModal`), `snake_case` functions/methods, `UPPER_SNAKE`
  module-level constants (`SETTINGS_FILE`, `DEFAULT_SETTINGS`, `AVAILABLE_MODELS`,
  `SLASH_COMMANDS`, `EXIT_COMMANDS`). Private state uses double-underscore name
  mangling (e.g. `self.__core_agent`).
- **Textual idioms:** build UI in `compose()` with `yield`; put styling in a
  class-level `CSS = """..."""` string; keybindings in a class-level `BINDINGS`
  list; lifecycle in `on_mount`; background/async work with `@work(exclusive=...,
  thread=...)`; UI state that should reactively update with `reactive(...)`.
- **Rich/Textual markup strings** are used inline for styled text, e.g.
  `"[bold #ff00ff]You:[/bold #ff00ff] ..."`. The palette is consistent:
  cyan `#00f0ff` (agent/positive), magenta `#ff007f` (user/accent),
  muted `#8b949e`, backgrounds `#0d1117`/`#161b22`/`#21262d`.
- **Async-first.** Streaming and I/O are `async`/`await`; blocking work (e.g. the
  `urllib` connection ping) is pushed to a thread via `loop.run_in_executor(...)`.
  `check_endpoint()` in `gaia.config` is **network-bound only** (no Textual import)
  so it can be unit-tested by patching `urllib.request.urlopen`.
- **Docstrings:** present on some public methods but sparse overall — keep them
  short and optional; don't feel obligated to add them to trivial helpers.
- **Slash commands** live in the `SLASH_COMMANDS` list (for autocomplete + the help
  overlay) and are dispatched in `GaiaTUIApp.handle_slash_command` (`match`/`case`).
  Keep the two in sync when adding a command. `EXIT_COMMANDS` is a `frozenset` helper
  that `is_exit_command()` checks against.

## Testing

Tests use **pytest** with `pytest-asyncio` (auto mode). Run them with:

```bash
uv run pytest         # from the repo root
uv run pytest -v      # verbose
```

Test files live in `tests/`:

- `test_config.py` — tests for `load_config`, `save_config`, `check_endpoint`,
  `parse_slash_command`, etc.
- `test_slash_commands.py` — slash command parsing and dispatch logic.
- `test_app.py` — Textual UI integration tests (screen rendering, widget queries).
- `test_agent.py` — agent harness tests.
- `conftest.py` — shared fixtures.

## Current state / known gaps

This is an early, mid-refactor codebase. Be aware before assuming features exist:

- **The agent is not yet wired to settings.** `Gaia.__init__` hardcodes the model
  (`'local-model'`), endpoint (`http://localhost:8080/v1`), and `api_key='not-needed'`,
  and there's a `# todo use a model router`. The UI's `settings["model"]` /
  `settings["endpoint"]` are not currently passed into the agent, so `/model`
  switching affects display only.
- **Endpoint defaults are inconsistent** across the code: `Gaia` uses `http://localhost:8080/v1`,
  `DEFAULT_SETTINGS` uses `http://localhost:11434` (Ollama). Pick one source of truth
  when touching this.
- **Memory is a stub.** `src/gaia/core/memory/` (base/shortterm/longterm) is
  scaffolded but empty — no persistence is implemented yet.
- **`src/gaia/core/base/` and memory `__init__.py` files are empty** — reserved for
  future shared types.
- **Encryption / SQLCipher is not implemented.** The `encrypted` toggle and
  "SQLCipher Vault" / "AES-256" copy exist in the UI and `/status`, but there is no
  actual encryption code. It's aspirational for now.
- **`src/app/__init__.py`** is now an empty file (was a stale TUI iteration). It's
  a candidate for deletion once confirmed unused.

## Suggested conventions for new work

- Add agent/memory functionality under `src/gaia/core/...` (that's the intended
  home), not in `gaia_tui_app.py`.
- Keep the TUI in `src/gaia/` modules; keep LLM logic in the `Gaia` agent.
- When adding a slash command, update both `SLASH_COMMANDS` and the `match` dispatch
  in `gaia_tui_app.py`.
- Prefer reading config from `settings.json` (via `gaia.config`) over hardcoding
  endpoint/model.
- Keep new functions free of Textual imports when possible (like `check_endpoint`) so
  they can be unit-tested independently.
