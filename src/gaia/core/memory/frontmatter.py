"""Simple YAML-style frontmatter parsing for Markdown files.

Used by the agent harness and slash commands to read/write the
user summary file (`user_summary.md`) and by ``/summary`` display.
"""

from __future__ import annotations

import re
from typing import Any

_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(.*?)\n---\s*\n?(.*)", re.DOTALL
)

_YAML_PAIR_RE = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$", re.MULTILINE
)


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse YAML-like frontmatter + body.

    Returns ``(metadata_dict, body_text)``.
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text

    meta: dict[str, Any] = {}
    for ym in _YAML_PAIR_RE.finditer(m.group(1)):
        key = ym.group(1)
        val = ym.group(2).strip()
        if val == "null":
            meta[key] = None
        elif val in ("true", "false"):
            meta[key] = val == "true"
        else:
            meta[key] = val

    body = m.group(2)
    return meta, body


def build_frontmatter(meta: dict[str, Any]) -> str:
    """Build a ``---``-delimited frontmatter block."""
    lines = ["---"]
    for k, v in meta.items():
        if v is None:
            lines.append(f"{k}: null")
        elif isinstance(v, bool):
            lines.append(f"{k}: {'true' if v else 'false'}")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines) + "\n"
