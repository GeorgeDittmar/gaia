# G.A.I.A. Container Deployment

Run the G.A.I.A. TUI inside a container (Podman or Docker). The container holds
the Python app and all its dependencies; the LLM runtime stays on your host
machine and the container reaches it over the network.

Two deployment modes:

| Mode | Services | Command |
|------|----------|---------|
| Single-container | Gaia only (SQLite memory) | `make docker-run` or `gaia` |
| Compose | Gaia + ChromaDB (+ optional Ollama) | `make compose-up` or `gaia --compose` |

## Quick start

```bash
# 1. Build
make docker-build

# 2. Run (single-container, SQLite memory)
make docker-run

# 3. Or use the installable wrapper
sudo cp scripts/gaia-docker /usr/local/bin/gaia
gaia

# 4. Full stack with ChromaDB memory
gaia --compose
make compose-up-ollama     # + local LLM
```

## How data persistence works

### Single-container

User data (settings.json, memory DB, logs) is stored in `~/.gaia` on the host
and bind-mounted into the container at `/home/gaia/data`. The container's
`GAIA_HOME` env var points to `/home/gaia/data`.

The `.venv` (Python environment) lives at `/home/gaia/.venv` **outside** the
data mount, so the bind-mount never hides it.

```
Host                              Container
──────                              ─────────
~/.gaia/ ──bind-mount──→ /home/gaia/data/
  settings.json                /home/gaia/.venv/  (Python environment)
  gaia-memory.db               /home/gaia/        (working dir)
  logs/
```

### Compose

Named Docker/Podman volumes persist data across stack restarts:

```
Volume          Container Path        Contents
──────          ──────────────        ────────
gaia-data       /home/gaia/data/      settings.json, memory DB, logs
chroma-data     /chroma/chroma/       ChromaDB persistent store
```

## Networking

### Single-container

| Runtime       | Hostname                  | Network mode |
|---------------|---------------------------|--------------|
| Podman        | `host.containers.internal`| `--network=host` |
| Docker Desktop| `host.docker.internal`    | `--add-host` |

### Compose

Services talk via internal DNS — the hostname is the service name:

| Service | Hostname | Port |
|---------|----------|------|
| ChromaDB | `chroma` | 8000 |
| Ollama | `llama` | 11434 |

## Wrapper script options

```bash
gaia                           # single-container, build if needed
gaia --build                   # rebuild then run
gaia --endpoint http://...     # custom LLM URL
gaia --home ~/.config/gaia     # custom data directory
gaia --compose                 # multi-container (Gaia + ChromaDB)
gaia --compose --build         # rebuild compose images first
gaia --compose --ollama        # + start Ollama as LLM backend
```

The wrapper **auto-detects** Podman or Docker. Force a specific one with:
```bash
CONTAINER_RUNTIME=podman gaia
CONTAINER_RUNTIME=docker gaia
```

## LLM endpoints by runtime

| Host LLM              | Podman Endpoint                             | Docker Endpoint                            |
|-----------------------|---------------------------------------------|--------------------------------------------|
| Ollama (default)      | `http://host.containers.internal:11434`    | `http://host.docker.internal:11434`       |
| llama.cpp             | `http://host.containers.internal:8080`     | `http://host.docker.internal:8080`        |
| Custom OpenAI compat. | `http://host.containers.internal:PORT`     | `http://host.docker.internal:PORT`        |
| WSL2 / Linux          | `http://host.containers.internal:11434`    | `http://host.docker.internal:11434`       |
| Custom                | `--endpoint http://...`                     | `--endpoint http://...`                    |

## Data persistence

### Single-container

Your settings and memory database live in `~/.gaia` (or wherever `--home`
points):

```
~/.gaia/
├── settings.json        # your config
├── gaia-memory.db       # SQLite memory store
└── logs/                # session log files
```

### Compose

```
docker compose volumes ls          # list volumes
docker compose run --rm gaia bash  # inspect container
```

## Environment variables

| Variable        | Default                              | Description                    |
|-----------------|--------------------------------------|--------------------------------|
| GAIA_HOME       | `$HOME/.gaia`                        | Data directory                 |
| LLM_ENDPOINT    | `http://host.containers.internal:11434` | LLM server URL (Podman)    |
| CHROMA_HOST     | `host.containers.internal`           | ChromaDB host (compose: `chroma`) |
| CHROMA_PORT     | `8000`                               | ChromaDB port                 |
| CONTAINER_RUNTIME | auto-detect                       | Force `podman` or `docker`    |

## Container image structure

### Gaia image

```
python:3.12-slim (runtime stage)
├── home/gaia/        # working directory
│   ├── .venv/        # Python environment (from builder, stays outside mount)
│   ├── data/         # ← bind-mounted from host ~/.gaia
│   │   ├── settings.json
│   │   ├── gaia-memory.db
│   │   └── logs/
│   └── entrypoint    # python -m gaia.cli
```

The image is built in two stages — dependencies are compiled in the builder
stage, only the runtime site-packages ship into the final image.

### ChromaDB image

```
FROM ghcr.io/chroma-core/chroma:latest
├── /chroma/chroma/   ← mounted as named volume
└── :8000             ← HTTP API
```

## Troubleshooting

**"UNREACHABLE" status badge** — the container can't reach the LLM endpoint.
Check that the container hostname resolves:

```bash
# Podman
podman run --rm alpine ping -c 1 host.containers.internal

# Docker
docker run --rm alpine ping -c 1 host.docker.internal

# Compose
docker compose exec gaia ping -c 1 chroma
```

**Terminal looks wrong** — Textual needs accurate terminal dimensions. The
wrapper captures `tput cols`/`tput lines` and passes them via
`COLUMNS`/`LINES` env vars. If you resize your window mid-session, the
terminal may still report the old size (this is a container limitation).

**"not found" error** — ensure Podman or Docker is installed:
```bash
# Podman
brew install podman          # macOS
sudo dnf install podman      # Fedora
sudo apt install podman      # Ubuntu (may need repo)

# Docker
brew install --cask docker   # macOS
sudo apt install docker.io   # Ubuntu
```

**Compose not found** — install the compose plugin:
```bash
# Docker
docker compose version       # bundled with Docker Desktop
# or
brew install docker-compose  # standalone

# Podman
podman compose version       # podman-compose plugin
```

## Podman vs Docker

|                 | Podman                              | Docker                          |
|-----------------|-------------------------------------|---------------------------------|
| Daemon          | None (rootless, no background proc) | `dockerd` daemon                |
| Rootless by default | Yes                            | Optional (rootless mode)        |
| Image format    | OCI (same as Docker)                | Docker format (OCI compatible)  |
| Interchangeable | Yes — same Dockerfile, same images  | Yes                             |
| Recommended     | Prefer for privacy-focused setups   | Prefer for Mac/Windows desktop  |

G.A.I.A. ships with zero telemetry and runs fully offline — Podman's daemonless,
rootless model aligns well with that philosophy.
