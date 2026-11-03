# syntax=docker/dockerfile:1
#
# G.A.I.A. — Containerfile (compatible with Podman & Docker)
#
# Builds a runtime image containing only the gaia TUI package and its
# dependencies.  No build tools, no git history, no tests ship into the
# final image.
#
# Usage:
#   podman build -t gaia .
#   podman run -it --rm \
#     --network=host \
#     -v ~/.gaia:/home/gaia/data \
#     gaia
#
#   # or with Docker:
#   docker build -t gaia .
#   docker run -it --rm \
#     --add-host=host.docker.internal:host-gateway \
#     -v ~/.gaia:/home/gaia/data \
#     gaia
#

# ── Stage 1: build wheels ──────────────────────────────────────────
FROM python:3.12-slim AS builder

RUN pip install --no-cache-dir uv

WORKDIR /build
COPY pyproject.toml uv.lock ./

# Compile dependencies into wheels (cached if lockfile unchanged)
RUN uv sync --frozen --no-install-project

COPY . .

# Build a wheel for the runtime stage (non-editable, installable)
RUN uv build --wheel --out-dir /wheels

# ── Stage 2: runtime image ─────────────────────────────────────────
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="G.A.I.A." \
      org.opencontainers.image.description="A private, local, zero-telemetry personal AI assistant" \
      org.opencontainers.image.source="https://github.com/GeorgeDittmar/gaia"

# Non-root user
RUN groupadd --gid 1000 gaia && \
    useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash gaia

# venv sits outside the data mount point
COPY --from=builder /build/.venv /home/gaia/.venv

# Remove the editable install marker (left by uv sync in builder), then
# install from the pre-built wheel so the package is self-contained.
COPY --from=builder /wheels/*.whl /tmp/
RUN rm -rf \
    /home/gaia/.venv/lib/python3.12/site-packages/gaia \
    /home/gaia/.venv/lib/python3.12/site-packages/gaia*.egg-link \
    /home/gaia/.venv/lib/python3.12/site-packages/gaia*.dist-info && \
    pip install --no-deps /tmp/*.whl

USER gaia

# venv is outside user data dir so bind-mounts to /home/gaia/data don't hide it
ENV VIRTUAL_ENV=/home/gaia/.venv \
    PATH="/home/gaia/.venv/bin:$PATH" \
    PYTHONPATH="/home/gaia/.venv/lib/python3.12/site-packages"

# Working dir is the app home. Data lives at /home/gaia/data
WORKDIR /home/gaia
RUN mkdir -p /home/gaia/data

# Default GAIA_HOME for the container — where data bind-mount lands
ENV GAIA_HOME=/home/gaia/data

# Default endpoint — override at runtime via -e LLM_ENDPOINT=...
ENV LLM_ENDPOINT=http://host.docker.internal:11434

ENTRYPOINT ["python", "-m", "gaia.cli"]
CMD []
