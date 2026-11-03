# G.A.I.A
General AI Assistant

A private, local, zero-telemetry personal AI assistant with a cyberpunk terminal UI. Runs on your machine — no cloud calls, no telemetry.

## Quick Start

```bash
# Install (requires Python 3.12+ and uv)
git clone https://github.com/GeorgeDittmar/gaia.git
cd gaia
uv tool install --python 3.14 .

# Run
gaia
```

See [INSTALL.md](INSTALL.md) for full install instructions (Homebrew, editable installs, containers).

## Container (Podman / Docker)

Run the TUI inside a container — no local Python install needed. The container
auto-detects Podman (preferred) or Docker, and mounts `~/.gaia` for settings
and memory persistence.

### Single-container mode

```bash
# Build and run (auto-detects podman → docker)
podman run -it --rm --network=host \
  -v ~/.gaia:/home/gaia/data \
  -e LLM_ENDPOINT="http://host.containers.internal:11434" \
  -w /home/gaia \
  gaia:latest

# Or install the wrapper script and use `gaia` as normal:
cp scripts/gaia-docker /usr/local/bin/gaia
gaia                          # runs the container
gaia --compose                # multi-container (Gaia + ChromaDB)
gaia --compose --ollama       # + local LLM
```

### Compose mode (full stack)

Orchestrate Gaia + ChromaDB memory server with docker-compose:

```bash
make compose-up               # start Gaia + ChromaDB
make compose-up-ollama        # also start Ollama as LLM
make compose-down             # tear down stack
```

Memory backend is selected in `settings.json`:
```json
{ "memory": { "backend": "chroma" } }
```

## Architecture

G.A.I.A. follows a plugin memory architecture: the app connects to external
services by URL — the same pattern used for the LLM backend.

```
┌──────────────────────┐     ┌───────────────┐     ┌───────────────┐
│ Gaia container       │ HTTP│ LLM (Ollama)  │ HTTP│ ChromaDB      │
│                      │────▶│ :11434        │────▶│ :8000         │
│ ┌──────────────────┐ │     └───────────────┘     └───────────────┘
│ │ TUI (Textual)    │ │
│ ├──────────────────┤ │
│ │ Agent (pydantic) │ │
│ ├──────────────────┤ │
│ │ MemoryStore      │ │
│ │ ├─ SQLite (def)  │ │
│ │ └─ ChromaDB (opt)│ │
│ └──────────────────┘ │
└──────────────────────┘
```

See [docs/architecture.md](docs/architecture.md) for full diagrams and data flow.

## Requirements

- **Python 3.12+** (3.14 recommended)
- **uv** ≥ 0.4.0 — [install guide](https://docs.astral.sh/uv/getting-started/installation/)
- A local LLM endpoint (Ollama, llama.cpp, or any OpenAI-compatible server)

## Features

- Dark "cyberpunk" terminal UI with real-time connection status
- Local LLM integration (Ollama / llama.cpp / OpenAI-compatible)
- Slash commands: `/model`, `/settings`, `/clear`, `/status`, `/help`, `/remember`, `/extract`
- Memory system with SQLite FTS5 (default) and ChromaDB (optional) backends
- Pluggable memory protocol — swap backends without changing the agent
- Post-turn fact extraction from conversation history
- Container-ready: single-container or full compose stack

## License

Apache 2.0
