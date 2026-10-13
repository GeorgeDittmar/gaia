"""Agent streaming — extracted from GaiaTUIApp.run_agent_execution."""

import asyncio
import logging
from typing import TYPE_CHECKING

from textual.containers import ScrollableContainer

if TYPE_CHECKING:
    from gaia.gaia_tui_app import GaiaTUIApp

# Seconds before we give up waiting for the first token
_FIRST_TOKEN_TIMEOUT = 30

logger = logging.getLogger("gaia.tui")


async def _animate_waiting(
    agent_widget: "ChatTurn",
    base_prefix: str,
    first_token: asyncio.Event,
    shutting_down: asyncio.Event,
) -> None:
    """Cycle through '.  '.. '...' waiting dots until first token or shutdown."""
    dots_states = [".  ", ".. ", "..."]
    dot_index = 0
    while not first_token.is_set() and not shutting_down.is_set():
        dot_text = dots_states[dot_index % len(dots_states)]
        agent_widget.update(
            base_prefix + f"[dim #8b949e]{dot_text}[/dim #8b949e]"
        )
        dot_index += 1
        await asyncio.sleep(0.2)


async def run_agent_stream(app: "GaiaTUIApp", prompt: str) -> str:
    """Run the agent streaming loop and return the accumulated response.

    Shows a visible error in the chat if the LLM times out, returns no
    tokens, or raises an exception — never leaves the user staring at
    waiting dots forever.
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
    first_token = asyncio.Event()
    shutting_down = asyncio.Event()

    # Track whether app.is_shutting_down so animate_waiting can check it
    # (Event.is_set() is faster than calling a method in a tight loop)
    orig_is_shutting_down = app.is_shutting_down

    def _check_shutdown() -> bool:
        nonlocal orig_is_shutting_down
        if app.is_shutting_down:
            shutting_down.set()
        return app.is_shutting_down

    anim_task = asyncio.create_task(
        _animate_waiting(agent_widget, base_prefix, first_token, shutting_down)
    )

    try:
        # wrap the generator in wait_for so we don't hang forever
        agen = app._GaiaTUIApp__core_agent.ainteract(prompt=prompt)
        try:
            token = await asyncio.wait_for(
                anext(agen), timeout=_FIRST_TOKEN_TIMEOUT
            )
        except asyncio.TimeoutError:
            # No first token within timeout — surface a visible error
            if not anim_task.done():
                anim_task.cancel()
                try:
                    await anim_task
                except asyncio.CancelledError:
                    pass
            error_msg = (
                f"[bold #ff007f]⚠ No Response Received[/bold #ff007f]\n"
                f"[dim #8b949e]"
                f"The agent didn't respond within {_FIRST_TOKEN_TIMEOUT}s. "
                f"This usually means the endpoint is unreachable, the model "
                f"isn't loaded, or the server is hung.\n\n"
                f"Check the LLM status indicator in the top-right, "
                f"then verify the endpoint and model in your settings.\n"
                f"Endpoint: {app.settings.endpoint}\n"
                f"Model: {app.settings.model}"
                f"[/dim #8b949e]"
            )
            agent_widget.update(error_msg)
            chat_box.scroll_end()
            logger.warning(
                "first-token timeout after %ss — endpoint unreachable: %s",
                _FIRST_TOKEN_TIMEOUT,
                app.settings.endpoint,
            )
            response_accumulator = ""
            # close the generator to avoid ResourceWarning
            await agen.aclose()
            return response_accumulator
        except StopAsyncIteration:
            # Generator finished without yielding anything (shouldn't happen,
            # but handle it the same way as a timeout)
            if not anim_task.done():
                anim_task.cancel()
                try:
                    await anim_task
                except asyncio.CancelledError:
                    pass
            error_msg = (
                f"[bold #ff007f]⚠ No Response Received[/bold #ff007f]\n"
                f"[dim #8b949e]"
                f"The agent returned no tokens — "
                f"check that the endpoint is reachable and the model is loaded.\n"
                f"Endpoint: {app.settings.endpoint} | "
                f"Model: {app.settings.model}"
                f"[/dim #8b949e]"
            )
            agent_widget.update(error_msg)
            chat_box.scroll_end()
            logger.warning(
                "agent returned zero tokens — endpoint unreachable: %s",
                app.settings.endpoint,
            )
            return response_accumulator

        # First token arrived — cancel waiting animation and stream the rest
        first_token.set()
        anim_task.cancel()
        try:
            await anim_task
        except asyncio.CancelledError:
            pass
        response_accumulator = token

        agent_widget.update(base_prefix + response_accumulator)
        chat_box.scroll_end()

        # Stream remaining tokens (no timeout — first token already arrived)
        async for token in agen:
            if app.is_shutting_down:
                break
            response_accumulator += token
            agent_widget.update(base_prefix + response_accumulator)
            chat_box.scroll_end()

    except Exception as exc:
        # Connection error or other exception during streaming
        if not anim_task.done():
            anim_task.cancel()
            try:
                await anim_task
            except asyncio.CancelledError:
                pass
        error_msg = (
            f"[bold #ff007f]⚠ Agent Error[/bold #ff007f]\n"
            f"[dim #8b949e]{type(exc).__name__}: {exc}[/dim #8b949e]\n"
            f"[dim #8b949e]Endpoint: {app.settings.endpoint} | "
            f"Model: {app.settings.model}"
        )
        agent_widget.update(error_msg)
        chat_box.scroll_end()
        response_accumulator = ""
        logger.exception("agent streaming failed")
    else:
        if not first_token.is_set():
            # Shouldn't reach here if timeout handling works, but guard anyway
            if not anim_task.done():
                anim_task.cancel()
                try:
                    await anim_task
                except asyncio.CancelledError:
                    pass
            error_msg = (
                f"[bold #ff007f]⚠ No Response Received[/bold #ff007f]\n"
                f"[dim #8b949e]The agent returned no tokens.\n"
                f"Endpoint: {app.settings.endpoint} | "
                f"Model: {app.settings.model}"
                f"[/dim #8b949e]"
            )
            agent_widget.update(error_msg)
            chat_box.scroll_end()
        else:
            agent_widget.update(base_prefix + response_accumulator)
            chat_box.scroll_end()

    # Post-turn fact extraction
    app.call_later(app._start_fact_extraction, prompt, response_accumulator)

    return response_accumulator
