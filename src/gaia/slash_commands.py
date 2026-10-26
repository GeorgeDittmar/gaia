"""Slash command handler for the G.A.I.A. TUI application."""

import json
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from pathlib import Path

from textual.containers import ScrollableContainer
from textual.widgets import Static

from gaia.config import SETTINGS_FILE, Settings
from gaia.core.agent.base import _parse_frontmatter
from gaia.settings_modal import SettingsModal

if TYPE_CHECKING:
    from gaia.gaia_tui_app import GaiaTUIApp


logger = logging.getLogger("gaia.tui")

_AGENT_MD = Path("AGENT.md")


def _backup_system_prompt() -> None:
    """Create a timestamped backup of AGENT.md before overwriting.

    Backup filename: AGENT.backup.<YYYYMMDD-HHMMSS>.md
    """
    if not _AGENT_MD.exists():
        return
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = _AGENT_MD.with_name(f"AGENT.backup.{ts}.md")
    try:
        _AGENT_MD.rename(backup_path)
        logger.info("backed up system prompt → %s", backup_path)
    except Exception:
        logger.exception("failed to back up system prompt")


def _remove_by_id(container: ScrollableContainer, widget_id: str) -> None:
    """Remove a child widget by its CSS ID.

    Textual's ``remove_children()`` does not accept a ``predicate``
    argument, so we query by ID and call ``.remove()`` on the match.
    """
    try:
        widget = container.query_one(f"#{widget_id}", Static)
        widget.remove()
    except Exception:
        pass  # widget not found — nothing to remove


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
                    self._app.core_agent.reconfigure(
                        model_name=arg,
                        max_context_tokens=self._app.settings.max_context_tokens,
                    )
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

                        # Backup and write AGENT.md when system prompt changed
                        prompt_changed = False
                        try:
                            old_agent_md = _AGENT_MD.read_text(encoding="utf-8")
                            if old_agent_md.strip() != self._app.settings.system_prompt.strip():
                                _backup_system_prompt()
                                _AGENT_MD.write_text(
                                    self._app.settings.system_prompt,
                                    encoding="utf-8",
                                )
                                prompt_changed = True
                                logger.info("updated AGENT.md with new system prompt")
                        except Exception:
                            logger.exception("failed to update AGENT.md — settings persisted anyway")

                        self._app.core_agent.reconfigure(
                            model_name=self._app.settings.model,
                            endpoint=self._app.settings.endpoint,
                            system_prompt=(
                                self._app.settings.system_prompt
                                if prompt_changed else ""
                            ),
                            max_context_tokens=self._app.settings.max_context_tokens,
                        )
                        self._app._refresh_config_info()

                        prompt_detail = ""
                        if prompt_changed:
                            prompt_detail = (
                                "\n  • AGENT.md updated (old version backed up)"
                            )

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
                                f"[dim]{self._app.settings.system_prompt[:60]}...[/dim]"
                                f"{prompt_detail}\n"
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
                    and self._app.core_agent is not None
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
                        self._app.core_agent.extract_from_history(
                            max_turns=10,
                        )
                    )
                    _remove_by_id(chat_box, "extract-status")
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
                    and self._app.core_agent is not None
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
                        _remove_by_id(chat_box, "extract-debug-status")
                        await chat_box.mount(
                            ChatTurn(
                                status_text,
                                classes="agent-msg",
                                id="extract-debug-status",
                            )
                        )
                        # Now actually run extraction
                        count = (
                            await self._app.core_agent.post_turn_extract(
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
                        _remove_by_id(chat_box, "extract-debug-status")
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

            case "summary":
                summary_path = Path.home() / ".gaia" / "user_summary.md"
                if summary_path.exists():
                    try:
                        raw = summary_path.read_text(encoding="utf-8")
                        _, content = _parse_frontmatter(raw)
                    except OSError:
                        await chat_box.mount(
                            ChatTurn(
                                "[bold #ff007f]Error:[/bold #ff007f] "
                                "Could not read summary file.",
                                classes="agent-msg",
                            )
                        )
                        return
                    await chat_box.mount(
                        ChatTurn(
                            f"[bold #00f0ff]User Summary:[/bold #00f0ff]\n"
                            f"{content}",
                            classes="agent-msg",
                        )
                    )
                else:
                    await chat_box.mount(
                        ChatTurn(
                            "[bold #ff007f]No User Summary:[/bold #ff007f] "
                            "A user summary will be generated after your first few conversations.\n"
                            "Type [bold]/regenerate[/bold] to generate one now.",
                            classes="agent-msg",
                        )
                    )

            case "regenerate":
                if (
                    self._app._memory is not None
                    and self._app.core_agent is not None
                ):
                    await chat_box.mount(
                        ChatTurn(
                            "[bold #00f0ff]Regenerating user summary...[/bold #00f0ff]",
                            classes="agent-msg",
                            id="summary-status",
                        )
                    )
                    chat_box.scroll_end()

                    try:
                        summary = await (
                            self._app.core_agent.generate_user_summary()
                        )
                    except Exception:
                        logger.exception("summary regeneration failed")
                        _remove_by_id(chat_box, "summary-status")
                        await chat_box.mount(
                            ChatTurn(
                                "[bold #ff007f]Regeneration failed:[/bold #ff007f] "
                                "Could not connect to the LLM. Check your endpoint.",
                                classes="agent-msg",
                            )
                        )
                        chat_box.scroll_end()
                        return

                    _remove_by_id(chat_box, "summary-status")
                    if summary:
                        await chat_box.mount(
                            ChatTurn(
                                f"[bold #00f0ff]User Summary regenerated:[/bold #00f0ff] "
                                f"[bold]{len(summary)}[/bold] chars.\n"
                                f"Type [bold]/summary[/bold] to view it.",
                                classes="agent-msg",
                            )
                        )
                    else:
                        await chat_box.mount(
                            ChatTurn(
                                "[bold #00f0ff]Summary regenerated[/bold #00f0ff] "
                                "(empty — no facts to summarize yet)",
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

            case _:
                await chat_box.mount(
                    ChatTurn(
                        f"[bold #ff007f]Unknown Command:[/bold #ff007f] "
                        f"/{cmd}. Type [bold]/[/bold] to browse valid commands.",
                        classes="agent-msg",
                    )
                )

        chat_box.scroll_end()
