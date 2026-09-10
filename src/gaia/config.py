"""Application configuration and persistence helpers."""

import json
from pathlib import Path
from typing import Any, Dict

SETTINGS_FILE = Path("settings.json")

DEFAULT_SETTINGS: Dict[str, Any] = {
    "model": "qwen2.5-coder:32b",
    "endpoint": "http://localhost:11434",
    "encrypted": True,
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
    {"cmd": "/exit", "desc": "Exit the application"},
    {"cmd": "/close", "desc": "Exit the application"},
]


def save_config(config: Dict[str, Any]) -> None:
    """Persist *config* to the settings file on disk."""
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4)


def load_config() -> Dict[str, Any]:
    """Read settings from disk.

    Creates *settings.json* with defaults if it is missing.
    Returns *DEFAULT_SETTINGS* on any read error.
    """
    if not SETTINGS_FILE.exists():
        try:
            save_config(DEFAULT_SETTINGS)
        except Exception:
            pass
        return DEFAULT_SETTINGS.copy()

    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            return {**DEFAULT_SETTINGS, **json.load(f)}
    except Exception:
        return DEFAULT_SETTINGS.copy()


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
    except Exception:
        return "UNREACHABLE"


EXIT_COMMANDS = frozenset({"exit", "quit", "close"})


def is_exit_command(cmd: str) -> bool:
    """Return True if *cmd* should terminate the application."""
    return cmd in EXIT_COMMANDS


def is_slash_input(text: str) -> bool:
    """Return True if *text* should be treated as a slash command."""
    return text.strip().startswith("/")
