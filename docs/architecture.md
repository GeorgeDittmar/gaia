# G.A.I.A. Architecture

## System Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          Host Machine                                   │
│                                                                         │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │  G.A.I.A. Application (container or local)                       │  │
│  │                                                                   │  │
│  │  ┌─────────────┐  ┌──────────────┐  ┌────────────────────────┐   │  │
│  │  │  TUI         │  │  Agent       │  │  Memory Layer          │   │  │
│  │  │  (Textual)   │  │  (pydantic-ai)│ │  ┌──────────────────┐  │   │  │
│  │  │              │  │              │  │  │ MemoryStore (ABC)│  │   │  │
│  │  │  Chat UI     │──▶│  Gaia        │──▶│  ├────────────────┤  │   │  │
│  │  │  Slash cmds  │  │  ──────────  │  │  │ SQLiteMemory   │  │   │  │
│  │  │  Status badge│  │  ainteract() │  │  │ Store          │  │   │  │
│  │  │  Settings    │  │              │  │  │ (default)      │  │   │  │
│  │  └─────────────┘  │              │  │  ├────────────────┤  │   │  │
│  │                    │              │  │  │ ChromaDBAdapter│  │   │  │
│  │                    │              │  │  │ (optional)     │  │   │  │
│  │                    │              │  │  └────────────────┘  │   │  │
│  │                    └──────────────┘  └────────────────────────┘   │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│                      │           │                                       │
│                      │ HTTP      │ HTTP                                  │
│                      ▼           ▼                                       │
│  ┌──────────────────────────┐  ┌─────────────────────┐                  │
│  │  LLM Backend             │  │  ChromaDB Memory     │                  │
│  │  (Ollama / llama.cpp /   │  │  Server (optional)   │                  │
│  │   OpenAI-compatible)     │  │                      │                  │
│  │                          │  │  ┌────────────────┐  │                  │
│  │  :11434 (Ollama default) │  │  │ semantic_facts │  │                  │
│  │                          │  │  │ episodic_events│  │                  │
│  │                          │  │  │ procedure_entries│ │                  │
│  │                          │  │  │ audit_log      │  │                  │
│  │                          │  │  └────────────────┘  │                  │
│  │                          │  │  :8000               │                  │
│  └──────────────────────────┘  └─────────────────────┘                  │
│                                                                         │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │  Persistent Data (~/.gaia or Docker volume)                       │  │
│  │  ├─ settings.json   ─ Runtime configuration                      │  │
│  │  ├─ gaia-memory.db ─ SQLite memory store                         │  │
│  │  ├─ logs/           ─ Application logs                           │  │
│  │  └─ user_summary.md ─ Generated user profile                     │  │
│  └───────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

## Deployment Topologies

### Single-Container (App Only)

```
┌─────────────────────┐     ┌──────────────────┐
│ Gaia container      │     │ LLM (host/remote)│
│                     │ HTTP│                  │
│  - TUI + Agent      │────▶│  Ollama :11434   │
│  - SQLite memory    │     │  or llama.cpp    │
│  - All deps bundled │     └──────────────────┘
└─────────────────────┘
```

Best for: quick start, no ChromaDB, minimal setup.

### Multi-Container (Full Stack)

```
┌──────────────────────────────────────────────────┐
│  docker compose / podman compose stack           │
│                                                  │
│  ┌──────────────┐    HTTP     ┌───────────────┐  │
│  │   gaia       │────────────▶│  chroma:8000  │  │
│  │   :TUI       │             │  ChromaDB     │  │
│  │              │             │  Memory       │  │
│  │  pydantic-ai │             └───────────────┘  │
│  │  + Textual   │                                 │
│  │              │    HTTP     ┌───────────────┐  │
│  │  SQLite or   │────────────▶│  llama:11434  │  │
│  │  ChromaDB    │             │  Ollama       │  │
│  │  (config)    │             └───────────────┘  │
│  └──────────────┘                                 │
│                                                  │
│  Volumes:                                        │
│    gaia-data  → ~/.gaia (settings, logs, db)     │
│    chroma-data → /chroma/chroma (vector store)   │
└──────────────────────────────────────────────────┘
```

Best for: vector search, ChromaDB backend, full isolation.

## Component Details

### Gaia Container

| Layer | Package | Purpose |
|-------|---------|---------|
| TUI | `textual` | Cyberpunk terminal UI, chat interface |
| Agent | `pydantic-ai` | LLM interaction, fact extraction |
| Config | stdlib `json` | settings.json read/write |
| Memory | stdlib `sqlite3` | Default SQLite memory store |
| Memory* | `chromadb` | Optional ChromaDB adapter |

*ChromaDB is an **optional dependency** (`uv tool install . --extra memory` or `pip install gaia[memory]`).

### Memory Protocol

```
MemoryStore (ABC — src/gaia/core/memory/base.py)
    │
    ├── SQLiteMemoryStore (bundled, default)
    │       └── SQLite + FTS5 full-text search
    │
    └── ChromaMemoryStore (optional)
            └── HttpClient → external ChromaDB server
```

To add a new backend: implement `MemoryStore` in a new file, register in `GaiaTUIApp._init_memory()`. No changes to TUI or agent needed.

## Data Flow — Per Turn

```
User types message
       │
       ▼
┌──────────────────────────┐
│ GaiaTUIApp               │
│ on_input_submitted()     │
│  1. Render "You:" bubble │
│  2. Mount Gaia.ainteract│
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│ Gaia.ainteract()         │
│ (pydantic-ai agent)      │
│                          │
│  [future: planner]       │
│  ┌─ retrieve memory?     │
│  ├─ generate queries     │
│  └─ note observations    │
│                          │
│  [future: augmentation]  │
│  ┌─ semantic_search()    │
│  ├─ inject into prompt   │
│  └─ deduplicate          │
│                          │
│  ┌─ run_stream()         │
│  └─ stream tokens → TUI │
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│ Post-turn                │
│                          │
│  1. episodic_record()    │
│     → conversation turn  │
│                          │
│  2. post_turn_extract()  │
│     ├─ LLM extracts facts│
│     ├─ classify()        │
│     ├─ dedup / conflict  │
│     └─ semantic_insert() │
│                          │
│  3. background summary   │
│     (10-min interval)    │
└──────────────────────────┘
```

## Configuration

`settings.json` (at `~/.gaia/settings.json` or bind-mounted path):

```json
{
  "model": "qwen2.5-coder:32b",
  "endpoint": "http://host.docker.internal:11434",
  "encrypted": true,
  "system_prompt": "...",
  "memory_db": "gaia-memory.db",
  "memory": {
    "backend": "sqlite",
    "endpoint": null
  }
}
```

| Setting | Default | Description |
|---------|---------|-------------|
| `model` | `qwen2.5-coder:32b` | Model name passed to LLM |
| `endpoint` | `http://localhost:11434` | LLM server URL |
| `memory.backend` | `sqlite` | Memory store: `sqlite` or `chroma` |
| `memory.endpoint` | `null` | ChromaDB URL (auto from `CHROMA_HOST`) |
| `memory_db` | `gaia-memory.db` | SQLite filename |

## Networking

| Service | Docker host | Podman host | Port |
|---------|-------------|-------------|------|
| LLM | `host.docker.internal` | `host.containers.internal` | 11434 |
| ChromaDB | `host.docker.internal` | `host.containers.internal` | 8000 |
| (in compose) | service name | service name | container port |

## Build Process

### Dockerfile (multi-stage)

```
Stage 1: builder
  ├─ python:3.12-slim
  ├─ install uv
  ├─ uv sync (resolve deps)
  ├─ uv build --wheel --out-dir /wheels
  └─ output: /wheels/*.whl

Stage 2: runtime
  ├─ python:3.12-slim
  ├─ copy .venv from builder
  ├─ remove editable install artifacts
  ├─ pip install /tmp/*.whl (non-editable)
  ├─ USER gaia
  ├─ ENV GAIA_HOME=/home/gaia/data
  └─ ENTRYPOINT: python -m gaia.cli
```

### Dockerfile.chroma (ChromaDB)

```
FROM ghcr.io/chroma-core/chroma:latest
EXPOSE 8000
VOLUME ["/chroma/chroma"]
CMD ["--host", "0.0.0.0", "--port", "8000"]
```

## Key Files

| File | Purpose |
|------|---------|
| `src/gaia/gaia_tui_app.py` | Main Textual app |
| `src/gaia/core/agent/base.py` | Gaia agent (pydantic-ai) |
| `src/gaia/core/memory/base.py` | MemoryStore ABC |
| `src/gaia/core/memory/sqlite_store.py` | SQLite backend |
| `src/gaia/core/memory/chroma_store.py` | ChromaDB HTTP adapter |
| `src/gaia/config.py` | Config loading, defaults |
| `Dockerfile` | Gaia container image |
| `Dockerfile.chroma` | ChromaDB container image |
| `docker-compose.yml` | Full stack orchestration |
| `scripts/gaia-docker` | Auto-detecting run wrapper |
| `pyproject.toml` | Package metadata, deps |
