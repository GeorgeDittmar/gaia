# Installing G.A.I.A.

G.A.I.A. is a Python package built with **hatchling**, dependencies managed with
**uv** (v0.1.0+). Three installation paths are supported:

| Path | Use case | Command |
|------|----------|---------|
| `uv tool install` | Personal CLI tool (recommended for most users) | `uv tool install .` |
| Homebrew | macOS/Linux package manager | `brew install george/tap/gaia` |
| Editable (`-e`) | Development / contributing | `uv pip install -e .` |

## Prerequisites

- **Python 3.12+** (3.14 recommended)
- **uv** ≥ 0.4.0 — [install](https://docs.astral.sh/uv/getting-started/installation/)

```bash
# macOS
brew install uv

# Linux (install script)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
irm https://astral.sh/uv/install.ps1 | iex
```

---

## Option 1: `uv tool install` — personal CLI tool

Install `gaia` as a standalone command in a uv-managed tool directory. No
virtualenv pollution, no system Python changes.

```bash
# From the repo root (clone first if needed)
git clone https://github.com/GeorgeDittmar/gaia.git
cd gaia

uv tool install --python 3.14 --force .

# Now available as `gaia` anywhere:
gaia
gaia --help
```

**Updating:**

```bash
uv tool install --python 3.14 --force .   # reinstall with latest local code
uv tool upgrade gaia                       # upgrade from PyPI (once published)
```

**Uninstalling:**

```bash
uv tool uninstall gaia
```

---

## Option 2: Homebrew — macOS / Linux

Once the tap is published:

```bash
# Add the tap
brew tap george/tap

# Install
brew install gaia

# Update (pulls latest release from PyPI)
brew upgrade gaia
```

**Building the formula locally** (for development):

```bash
# Use the local .rb file
brew install --build-from-source ./Formula/gaia.rb
```

> The Homebrew formula uses `pip install .` which builds from the PyPI sdist.
> Python 3.12+ and hatchling are required; pip fetches dependencies from PyPI.

### Publishing to PyPI (for Homebrew)

A GitHub Actions workflow (`.github/workflows/publish.yml`) publishes to PyPI
on every `v*` tag using **trusted publishing** (OIDC, no API tokens):

```bash
git tag v0.1.0
git push origin v0.1.0  # triggers the workflow
```

**To enable trusted publishing:**

1. GitHub repo: **Settings → Environments → New environment** named `release`
2. PyPI: **Profile → API Keys → Trusted Publisher** → add GitHub repo + workflow
3. Once published, `brew upgrade gaia` picks up the new version

---

## Option 3: Editable install — development

For contributing or working on the codebase:

```bash
cd /path/to/gaia

# Create and activate a venv
uv venv --python 3.14
source .venv/bin/activate

# Install in editable mode (changes to source take effect immediately)
uv pip install -e ".[dev]"

# Run tests
uv run pytest

# Launch
gaia
```

The `-e` flag means you can edit source files and see changes immediately —
no reinstall needed.

---

## Container install (Docker / Podman)

Two deployment modes — see [docs/docker.md](docs/docker.md) for full details.

### Single-container (App + SQLite memory)

User data (`settings.json`, memory DB, logs) is bind-mounted from `~/.gaia` into
`/home/gaia/data` inside the container.

```bash
# Build
docker build -t gaia .          # or: podman build -t gaia .

# Run
docker run -it --rm \
  --add-host=host.docker.internal:host-gateway \
  -v ~/.gaia:/home/gaia/data \
  gaia

# or with Podman:
podman run -it --rm \
  --network=host \
  -v ~/.gaia:/home/gaia/data \
  gaia
```

### Compose (Gaia + ChromaDB + optional Ollama)

```bash
make compose-up                 # start Gaia + ChromaDB
make compose-up-ollama          # + local LLM
make compose-down               # tear down
```

### Convenience wrapper

```bash
sudo cp scripts/gaia-docker /usr/local/bin/gaia
gaia                           # auto-detects Podman or Docker
gaia --compose                 # multi-container stack
gaia --compose --ollama        # + Ollama
```

---

## What gets installed

| Component | Details |
|-----------|---------|
| Console script | `gaia` → `gaia.cli:main` |
| Package | `gaia` (imports from `src/gaia/`) |
| Dependencies | `textual`, `pydantic-ai`, `pydantic`, `scikit-learn` |
| Optional (`[memory]`) | `chromadb` — enables ChromaDB adapter |

## Runtime requirements

- **LLM endpoint** running on your machine (Ollama, llama.cpp, or any
  OpenAI-compatible server). The app polls the endpoint on startup and shows
  `ONLINE` / `UNREACHABLE` in the status bar.
- A terminal with true color support (most modern terminals qualify).
