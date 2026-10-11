"""Slash command handler for the G.A.I.A. TUI application."""

import json
from typing import TYPE_CHECKING, Any

from textual.containers import ScrollableContainer
from textual.widgets import Static

from gaia.config import SETTINGS_FILE, Settings
from gaia.settings_modal import SettingsModal

if TYPE_CHECKING:
    from gaia.gaia_tui_app import GaiaTUIApp


class SlashCommandHandler:
    """Handles slash-prefixed commands typed by the user in the chat input.

    Dispatches commands via ``match``/``case`` onto methods/behaviour
    that live on the owning ``GaiaTUIApp`` instance.
    """

    def __init__(self, app: "GaiaTUIApp") -> None:
        self._app = app

    async def handle_slash_command(self, raw_command: str) -> None:
        """Dispatch a single slash command.

        Parses *raw_command* (e.g. ``/model llama3``) into a command name
        and argument, then routes to the appropriate handler branch.
        """
        from gaia.gaia_tui_app import ChatTurn

        parts = raw_command[1:].split(maxsplit=1)
        cmd = parts[0].lower() if parts else ""
        arg = parts[1].strip() if len(parts) > 1 else ""

        chat_box = self._app.query_one("#chat-container", ScrollableContainer)

        match cmd:
            case "exit" | "quit" | "close":
                self._app.force_exit()

            case "help":
                self._app.action_show_help()

            case "model":
                if arg:
                    self._app.settings = Settings.from_dict(
                        {**self._app.settings.to_dict(), "model": arg}
                    )
                    from gaia.config import save_config
                    save_config(self._app.settings)
                    self._app._GaiaTUIApp__core_agent.reconfigure(model_name=arg)
                    self._app._refresh_config_info()
                    await chat_box.mount(
                        ChatTurn(
                            "[bold #00f0ff]Active Model Updated:[/bold #00f0ff] "
                            f"{arg}",
                            classes="agent-msg",
                        )
                    )
                else:
                    await chat_box.mount(
                        ChatTurn(
                            f"[bold #00f0ff]Current Model:[/bold #00f0ff] "
                            f"{self._app.settings.model}\n"
                            "Usage: [bold]/model <model_name>[/bold]",
                            classes="agent-msg",
                        )
                    )

            case "settings":

                def on_settings_closed(
                    new_config: dict[str, Any] | None,
                ) -> None:
                    if new_config:
                        merged = {
                            **self._app.settings.to_dict(),
                            **new_config,
                        }
                        self._app.settings = Settings.from_dict(merged)
                        from gaia.config import save_config
                        save_config(self._app.settings)
                        self._app._GaiaTUIApp__core_agent.reconfigure(
                            model_name=self._app.settings.model,
                            endpoint=self._app.settings.endpoint,
                        )
                        self._app._refresh_config_info()

                        self._app.call_after_refresh(
                            chat_box.mount,
                            ChatTurn(
                                f"[bold #00f0ff]System Config Saved:[/bold #00f0ff]\n"
                                f"  • Persisted to: "
                                f"[bold]{SETTINGS_FILE.resolve()}[/bold]\n"
                                f"  • Model: "
                                f"[bold]{self._app.settings.model}[/bold]\n"
                                f"  • Endpoint: "
                                f"{self._app.settings.endpoint}\n"
                                f"  • System Prompt: "
                                f"[dim]{self._app.settings.system_prompt[:60]}...[/dim]\n"
                                f"  • Vault Encryption: "
                                f"{'Enabled' if self._app.settings.encrypted else 'Disabled'}",
                                classes="agent-msg",
                            ),
                        )

                self._app.push_screen(
                    SettingsModal(self._app.settings),
                    on_settings_closed,
                )

            case "clear":
                await chat_box.remove_children()
                await chat_box.mount(
                    Static(
                        "[bold #ff007f]G.A.I.A. SYSTEM RESET[/bold #ff007f]\n"
                        "[dim #8b949e]Terminal buffer cleared.[/dim #8b949e]\n",
                        id="welcome",
                    )
                )

            case "debug":
                if arg.lower() == "on":
                    self._app._debug_mode = True
                    await chat_box.mount(
                        ChatTurn(
                            "[bold #ff007f]Debug:[/bold #ff007f] "
                            "[bold #00f0ff]ON[/bold #00f0ff] — extraction status "
                            "will appear in chat",
                            classes="agent-msg",
                        )
                    )
                elif arg.lower() == "off":
                    self._app._debug_mode = False
                    await chat_box.mount(
                        ChatTurn(
                            "[bold #ff007f]Debug:[/bold #ff007f] "
                            "[bold #ff007f]OFF[/bold #ff007f] — extraction status hidden",
                            classes="agent-msg",
                        )
                    )
                else:
                    state = "ON" if self._app._debug_mode else "OFF"
                    await chat_box.mount(
                        ChatTurn(
                            f"[bold #ff007f]Debug:[/bold #ff007f] {state} — "
                            "usage: [bold]/debug on[/bold] or [bold]/debug off[/bold]",
                            classes="agent-msg",
                        )
                    )

            case "status":
                status_info = (
                    "[bold #00f0ff]System Diagnostics:[/bold #00f0ff]\n"
                    f"  • LLM Endpoint Status: "
                    f"{self._app.connection_status}\n"
                    f"  • Model Runtime: {self._app.settings.model}\n"
                    f"  • Host Endpoint: "
                    f"{self._app.settings.endpoint}\n"
                    f"  • System Prompt Length: "
                    f"{len(self._app.settings.system_prompt)} chars\n"
                    f"  • Vault State: "
                    f"{'AES-256 (SQLCipher)' if self._app.settings.encrypted else 'Unencrypted Plaintext'}\n"
                    f"  • Config File: {SETTINGS_FILE.resolve()}"
                )
                await chat_box.mount(
                    ChatTurn(status_info, classes="agent-msg")
                )

            case "remember":
                if arg:
                    if self._app._memory is not None:
                        await self._app._memory.semantic_insert(
                            content=arg,
                            category="preference",
                            confidence=1.0,
                            source="user",
                        )
                        await chat_box.mount(
                            ChatTurn(
                                "[bold #00f0ff]Fact Stashed:[/bold #00f0ff] "
                                f"[dim #8b949e]{arg}[/dim #8b949e]",
                                classes="agent-msg",
                            )
                        )
                    else:
                        await chat_box.mount(
                            ChatTurn(
                                "[bold #ff007f]Memory Unavailable:[/bold #ff007f] "
                                "Memory store is not initialized.",
                                classes="agent-msg",
                            )
                        )
                else:
                    await chat_box.mount(
                        ChatTurn(
                            "[bold #ff007f]Usage:[/bold #ff007f] "
                            "/remember <fact to store>\n"
                            "Example: [bold]/remember user prefers Python[/bold]",
                            classes="agent-msg",
                        )
                    )

            case "extract":
                if (
                    self._app._memory is not None
                    and self._app._GaiaTUIApp__core_agent is not None
                ):
                    await chat_box.mount(
                        ChatTurn(
                            "[bold #00f0ff]Extracting facts...[/bold #00f0ff]",
                            classes="agent-msg",
                            id="extract-status",
                        )
                    )
                    chat_box.scroll_end()

                    count = await (
                        self._app._GaiaTUIApp__core_agent.extract_from_history(
                            max_turns=10,
                        )
                    )
                    await chat_box.remove_children(
                        predicate=lambda t: getattr(
                            t, "default_attrs", {}
                        ).get("id") == "extract-status"
                    )
                    await chat_box.mount(
                        ChatTurn(
                            f"[bold #00f0ff]Extraction complete:[/bold #00f0ff] "
                            f"[bold]{count}[/bold] facts extracted from recent history.",
                            classes="agent-msg",
                            id="extract-status",
                        )
                    )
                else:
                    await chat_box.mount(
                        ChatTurn(
                            "[bold #ff007f]Memory Unavailable:[/bold #ff007f] "
                            "Memory store is not initialized.",
                            classes="agent-msg",
                        )
                    )

            case "extract-debug":
                # Debug mode: do one extraction on the latest turn and show
                # raw output
                if (
                    self._app._memory is not None
                    and self._app._GaiaTUIApp__core_agent is not None
                ):
                    await chat_box.mount(
                        ChatTurn(
                            "[bold #00f0ff]Debug extraction...[/bold #00f0ff]",
                            classes="agent-msg",
                            id="extract-debug-status",
                        )
                    )
                    chat_box.scroll_end()

                    # Get the latest turn from episodic memory
                    results = await self._app._memory.episodic_search(
                        query="conversation turn", limit=1
                    )
                    if results:
                        latest = results[0]
                        raw_payload = latest.get("payload", "{}")
                        if isinstance(raw_payload, str):
                            try:
                                payload = json.loads(raw_payload)
                            except (json.JSONDecodeError, ValueError):
                                payload = {}
                        else:
                            payload = raw_payload
                        user_msg = payload.get("user_prompt", "(none)")
                        agent_resp = payload.get("agent_response", "(none)")
                        status_text = (
                            f"[dim #8b949e]=== DEBUG EXTRACTION ==="
                            f"[/dim #8b949e]\n"
                            f"[dim]User: {user_msg[:200]}[/dim]\n"
                            f"[dim]Agent: "
                            f"{agent_resp[:200]}[/dim]"
                        )
                        await chat_box.remove_children(
                            predicate=lambda t: getattr(
                                t, "default_attrs", {}
                            ).get("id") == "extract-debug-status"
                        )
                        await chat_box.mount(
                            ChatTurn(
                                status_text,
                                classes="agent-msg",
                                id="extract-debug-status",
                            )
                        )
                        # Now actually run extraction
                        count = (
                            await self._app._GaiaTUIApp__core_agent.post_turn_extract(
                                user_msg, agent_resp
                            )
                        )
                        # Show result
                        await chat_box.mount(
                            ChatTurn(
                                f"[bold #00f0ff]Extracted & saved: {count} fact(s)[/bold #00f0ff]",
                                classes="agent-msg",
                            )
                        )
                    else:
                        await chat_box.remove_children(
                            predicate=lambda t: getattr(
                                t, "default_attrs", {}
                            ).get("id") == "extract-debug-status"
                        )
                        await chat_box.mount(
                            ChatTurn(
                                "[bold #ff007f]No conversation turns found in episodic memory.[/bold #ff007f]",
                                classes="agent-msg",
                            )
                        )
                    chat_box.scroll_end()
                else:
                    await chat_box.mount(
                        ChatTurn(
                            "[bold #ff007f]Memory Unavailable:[/bold #ff007f] "
                            "Memory store is not initialized.",
                            classes="agent-msg",
                        )
                    )

            case _:
                await chat_box.mount(
                    ChatTurn(
                        f"[bold #ff007f]Unknown Command:[/bold #ff007f] "
                        f"/{cmd}. Type [bold]/[/bold] to browse valid commands.",
                        classes="agent-msg",
                    )
                )

        chat_box.scroll_end()
