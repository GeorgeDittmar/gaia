"""Post-turn fact extraction pipeline.

Extracted from ``gaia_tui_app.py`` so the TUI code is leaner.

This module handles the background extraction of facts from conversation
turns after each agent response is streamed.
"""

import asyncio
import logging
from typing import TYPE_CHECKING

from textual.containers import ScrollableContainer

if TYPE_CHECKING:
    from gaia.gaia_tui_app import GaiaTUIApp

logger = logging.getLogger("gaia.tui")


def start_fact_extraction(
    app: "GaiaTUIApp", user_prompt: str, agent_response: str
) -> None:
    """Launch post-turn fact extraction as a background task.

    Parameters
    ----------
    app:
        The running ``GaiaTUIApp`` instance.
    user_prompt:
        The user's input that triggered the agent response.
    agent_response:
        The full accumulated agent response text.
    """
    logger.info("extraction callback fired — prompt=%s", user_prompt[:80])
    try:
        asyncio.create_task(_run_extraction(app, user_prompt, agent_response))
    except Exception:
        logger.exception("failed to create extraction task")


async def _run_extraction(
    app: "GaiaTUIApp", user_prompt: str, agent_response: str
) -> None:
    """Run extraction and schedule debug status display.

    Parameters
    ----------
    app:
        The running ``GaiaTUIApp`` instance.
    user_prompt:
        The user's input.
    agent_response:
        The full accumulated agent response.
    """
    logger.info("extraction running")
    try:
        count = await app._GaiaTUIApp__core_agent.post_turn_extract(
            user_prompt, agent_response
        )
        logger.info("extraction complete — %d facts", count)
        app.call_after_refresh(
            lambda c=count: _do_mount_extraction_status(app, c)
        )
    except Exception:
        logger.exception("extraction task failed")


def _do_mount_extraction_status(app: "GaiaTUIApp", count: int) -> None:
    """Show a persistent message in the chat after extraction — only when debug is on.

    Parameters
    ----------
    app:
        The running ``GaiaTUIApp`` instance.
    count:
        Number of facts extracted.
    """
    if not app._debug_mode:
        return
    try:
        from gaia.gaia_tui_app import ChatTurn

        chat_box = app.query_one("#chat-container", ScrollableContainer)
        if count > 0:
            msg = (
                f"[bold #00f0ff]🧠 Memory[/bold #00f0ff] "
                f"[dim #8b949e]— {count} fact{'s' if count > 1 else ''} "
                f"auto-saved ✓[/dim #8b949e]"
            )
        else:
            msg = (
                "[bold #ff007f]🧠 Memory[/bold #ff007f] "
                "[dim #8b949e]— no facts extracted ✗ "
                "(run /extract-debug to see why)[/dim #8b949e]"
            )
        chat_box.mount(
            ChatTurn(
                msg,
                classes="agent-msg",
                id="_auto-save-status",
            )
        )
        chat_box.scroll_end()
    except Exception:
        pass
