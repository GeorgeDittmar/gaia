"""Application configuration and persistence helpers."""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Respect GAIA_HOME env var (set by Docker/Podman) to override the
# default user-data directory; falls back to ~/.gaia otherwise.
_GAIA_HOME = Path(os.environ.get("GAIA_HOME", Path.home() / ".gaia"))
SETTINGS_DIR = _GAIA_HOME
SETTINGS_FILE = SETTINGS_DIR / "settings.json"


@dataclass(frozen=True)
class MemoryConfig:
    """In-memory configuration for the memory store backend.

    Direct construction is not recommended — use
    ``Settings.from_dict()`` which validates the backend value.
    """

    backend: str = "chroma"  # "chroma" | "postgres"
    memory_dir: str = "~/.gaia/memory"
    retention_days: int = 30
    endpoint: str | None = None  # Backend endpoint URL (ChromaDB or PostgreSQL)


@dataclass(frozen=True)
class Settings:
    """Immutable application settings loaded from ``settings.json``."""

    model: str = "qwen2.5-coder:32b"
    endpoint: str = "http://127.0.0.1:8080"
    encrypted: bool = True
    system_prompt: str = (
        "You are G.A.I.A., an advanced, secure local AI agent. "
        "Provide clear, direct, and technically rigorous assistance. "
        "Your sole task is to be the best personal assistant to the user. "
        "They are your best friend and ally. "
        "You will never harm the user, you must do your best to assist the user "
        "whenever they ask or with whatever they need."
    )
    max_context_tokens: int = 4000
    memory: MemoryConfig = field(default_factory=MemoryConfig)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Settings":
        """Merge *d* over defaults, validate, return ``Settings``."""
        mem_raw = d.get("memory")
        if isinstance(mem_raw, str):
            # Bare string: treat as backend name
            backend = mem_raw
        elif isinstance(mem_raw, dict):
            backend = mem_raw.get("backend", "chroma")
        else:
            # None, int, or other non-dict → use default
            backend = "chroma"
        if backend not in ("chroma", "postgres"):
            backend = "chroma"
        return cls(
            model=d.get("model", cls.model),
            endpoint=d.get("endpoint", cls.endpoint),
            encrypted=d.get("encrypted", cls.encrypted),
            system_prompt=d.get("system_prompt", cls.system_prompt),
            max_context_tokens=d.get(
                "max_context_tokens", cls.max_context_tokens
            ),
            memory=MemoryConfig(
                backend=backend,
                memory_dir=d.get("memory_dir", "~/.gaia/memory"),
                retention_days=(mem_raw.get("retention_days", 30)
                                if isinstance(mem_raw, dict) else 30),
                endpoint=(mem_raw.get("endpoint")
                          if isinstance(mem_raw, dict) else None),
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize back to a plain dict for JSON persistence.

        ``memory_dir`` is kept at the top level to match the on-disk
        ``DEFAULT_SETTINGS`` shape; ``load_config()`` always merges
        over that dict before calling ``from_dict()``, so the round-trip
        is safe in practice.
        """
        return {
            "model": self.model,
            "endpoint": self.endpoint,
            "encrypted": self.encrypted,
            "system_prompt": self.system_prompt,
            "max_context_tokens": self.max_context_tokens,
            "memory_dir": self.memory.memory_dir,
            "memory": {
                "backend": self.memory.backend,
                "retention_days": self.memory.retention_days,
                "endpoint": self.memory.endpoint,
            },
        }

DEFAULT_SETTINGS: dict[str, Any] = {
    "model": "qwen2.5-coder:32b",
    "endpoint": "http://127.0.0.1:8080",  # OpenAI-compatible default; override with LLM_ENDPOINT or /settings
    "encrypted": True,
    "max_context_tokens": 4000,
    "memory_dir": "~/.gaia/memory",
    "memory": {
        "backend": "chroma",  # ChromaDB HTTP server
        "retention_days": 30,
    },
    "system_prompt": (
        "You are G.A.I.A., an advanced, secure local AI agent. "
        "Provide clear, direct, and technically rigorous assistance. "
        "Your sole task is to be the best personal assistant to the user. "
        "They are your best friend and ally. "
        "You will never harm the user, you must do your best to assist the user "
        "whenever they ask or with whatever they need."
    ),
}

AVAILABLE_MODELS = [
    {"arg": "qwen2.5-coder:32b", "desc": "Local Ollama - Code Specialist"},
    {"arg": "llama3.3:70b", "desc": "Local Ollama - General Reasoning"},
    {"arg": "deepseek-r1:14b", "desc": "Local Ollama - Chain-of-Thought"},
    {"arg": "gpt-4o", "desc": "Remote Endpoint - OpenAI API"},
]

SLASH_COMMANDS = [
    {"cmd": "/model", "desc": "Switch active LLM runtime model"},
    {"cmd": "/settings", "desc": "Open persistent setup pop-up"},
    {"cmd": "/clear", "desc": "Clear terminal chat buffer"},
    {"cmd": "/status", "desc": "Display active runtime diagnostics"},
    {"cmd": "/help", "desc": "Show interactive keybindings & commands overlay"},
    {"cmd": "/remember", "desc": "Store a fact in semantic memory"},
    {"cmd": "/exit", "desc": "Exit the application"},
    {"cmd": "/close", "desc": "Exit the application"},
    {"cmd": "/extract", "desc": "Auto-extract facts from recent conversation history"},
    {"cmd": "/extract-debug", "desc": "Extract facts and show raw LLM output in chat"},
    {"cmd": "/debug", "desc": "Toggle debug mode (on/off) — controls whether extraction status shows in chat"},
    {"cmd": "/summary", "desc": "View the current user summary"},
    {"cmd": "/regenerate", "desc": "Regenerate the user summary from conversation data"},
    {"cmd": "/memory-stats", "desc": "Show diagnostic counts of stored memory entries"},
]


def save_config(settings: Settings) -> None:
    """Persist *settings* to the settings file on disk (atomically)."""
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_FILE.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(settings.to_dict(), f, indent=4)
    os.replace(tmp, SETTINGS_FILE)


def load_config() -> Settings:
    """Read settings from disk.

    Creates *settings.json* with defaults if it is missing.
    Returns defaults on any read error.
    """
    if not SETTINGS_FILE.exists():
        try:
            save_config(Settings())
        except Exception:
            pass
        return Settings()

    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            raw = {**DEFAULT_SETTINGS, **json.load(f)}
            return Settings.from_dict(raw)
    except Exception:
        return Settings()


def parse_slash_command(raw_command: str) -> tuple[str, str]:
    """Parse a raw slash command string into (cmd, arg).

    Strips the leading ``/`` and splits on the first whitespace.
    Returns an empty string for *arg* when none is present.

    Examples:
        >>> parse_slash_command("/model gpt-4o")
        ('model', 'gpt-4o')
        >>> parse_slash_command("/help")
        ('help', '')
        >>> parse_slash_command("/remember that thing")
        ('remember', 'that thing')
    """
    command = raw_command.lstrip("/")
    parts = command.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""
    return cmd, arg


async def check_endpoint(endpoint: str, timeout: float = 1.5) -> str:
    """Check whether *endpoint* is reachable.

    Returns one of:

    - ``"ONLINE"`` — HTTP 2xx/3xx response
    - ``"HTTP <code>"`` — non-2xx/3xx response
    - ``"UNREACHABLE"`` — network error or timeout

    This function is **network-bound only** — no Textual dependency —
    so it can be unit-tested by patching ``urllib.request.urlopen``.
    """
    import asyncio
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        endpoint, headers={"User-Agent": "GAIA-TUI/1.0"}, method="GET"
    )

    loop = asyncio.get_running_loop()

    def _ping() -> int:
        with urllib.request.urlopen(req, timeout=timeout) as response:  # type: ignore[reportOptionalMemberContext]
            return response.status

    try:
        code = await loop.run_in_executor(None, _ping)
        if 200 <= code < 400:
            return "ONLINE"
        return f"HTTP {code}"
    except urllib.error.HTTPError as e:
        # urllib raises HTTPError for 4xx/5xx — server is alive
        code = e.code
        if 200 <= code < 500:
            return "ONLINE"
        return f"HTTP {code}"
    except Exception:
        return "UNREACHABLE"


EXIT_COMMANDS = frozenset({"exit", "quit", "close"})


def is_exit_command(cmd: str) -> bool:
    """Return True if *cmd* should terminate the application."""
    return cmd in EXIT_COMMANDS


def is_slash_input(text: str) -> bool:
    """Return True if *text* should be treated as a slash command."""
    return text.strip().startswith("/")
