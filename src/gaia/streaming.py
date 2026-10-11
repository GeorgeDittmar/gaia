"""Agent streaming — extracted from GaiaTUIApp.run_agent_execution."""

import asyncio
from typing import TYPE_CHECKING

from textual.containers import ScrollableContainer

if TYPE_CHECKING:
    from gaia.gaia_tui_app import GaiaTUIApp


async def run_agent_stream(app: "GaiaTUIApp", prompt: str) -> str:
    """Run the agent streaming loop and return the accumulated response.

    Replicates GaiaTUIApp.run_agent_execution() exactly, but returns the
    response string so callers (tests, other modules) can reuse the
    streaming logic without needing the full TUI.
    """
    chat_box = app.query_one("#chat-container", ScrollableContainer)

    from gaia.gaia_tui_app import ChatTurn

    agent_widget = ChatTurn(
        "[bold #00f0ff]G.A.I.A.:[/bold #00f0ff] ",
        classes="agent-msg",
    )
    await chat_box.mount(agent_widget)

    base_prefix = (
        f"[bold #00f0ff]G.A.I.A. ({app.settings.model}):[/bold #00f0ff] "
    )
    response_accumulator = ""
    dots_states = [".  ", ".. ", "..."]
    dot_index = 0
    first_token_received = False

    async def animate_waiting() -> None:
        nonlocal dot_index
        while not first_token_received and not app.is_shutting_down:
            dot_text = dots_states[dot_index % len(dots_states)]
            agent_widget.update(
                base_prefix + f"[dim #8b949e]{dot_text}[/dim #8b949e]"
            )
            dot_index += 1
            await asyncio.sleep(0.2)

    anim_task = asyncio.create_task(animate_waiting())

    try:
        async for token in app._GaiaTUIApp__core_agent.ainteract(prompt=prompt):
            if app.is_shutting_down:
                break

            if not first_token_received:
                first_token_received = True
                anim_task.cancel()
                try:
                    await anim_task
                except asyncio.CancelledError:
                    pass
                response_accumulator = ""

            response_accumulator += token
            agent_widget.update(base_prefix + response_accumulator)
            chat_box.scroll_end()
    finally:
        if not anim_task.done():
            anim_task.cancel()
            try:
                await anim_task
            except asyncio.CancelledError:
                pass

    agent_widget.update(base_prefix + response_accumulator)
    chat_box.scroll_end()

    # Post-turn fact extraction
    app.call_later(app._start_fact_extraction, prompt, response_accumulator)

    return response_accumulator
