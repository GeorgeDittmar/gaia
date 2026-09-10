"""Main G.A.I.A. terminal UI application (GaiaTUIApp)."""

import asyncio
from pathlib import Path
from typing import Any, Dict

from textual.app import App, ComposeResult
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Input, Static, Button, Switch, Label, TextArea
from textual.widgets.option_list import Option
from textual.containers import ScrollableContainer, Horizontal, Vertical
from textual import work

from gaia.core.agent import Gaia
from gaia.config import (
    DEFAULT_SETTINGS,
    load_config,
    save_config,
    check_endpoint,
    parse_slash_command,
)
from gaia.command_input import CommandInput
from gaia.help_modal import HelpModal
from gaia.settings_modal import SettingsModal

SETTINGS_FILE = Path("settings.json")


class ChatTurn(Static):
    """A single message turn in the chat transcript."""

    can_focus = True

    def on_focus(self) -> None:
        self.scroll_visible()


class GaiaTUIApp(App):
    """G.A.I.A. Dark Cyberpunk Terminal UI."""

    connection_status = reactive("CHECKING...")

    CSS = """
    Screen {
        background: #0d1117;
        color: #c9d1d9;
        layout: vertical;
        overflow: hidden;
    }

    #custom-header {
        height: 3;
        min-height: 3;
        background: #161b22;
        border-bottom: solid #00f0ff;
        padding: 0 2;
        align-vertical: middle;
    }

    #custom-header Static {
        height: 1;
        content-align: left middle;
    }

    .header-title {
        color: #00f0ff;
        text-style: bold;
    }

    .llm-badge {
        text-style: bold;
        padding: 0 1;
        margin-right: 2;
        content-align: center middle;
    }

    .status-online {
        background: #238636;
        color: #ffffff;
    }

    .status-checking {
        background: #d29922;
        color: #0d1117;
    }

    .status-unreachable {
        background: #da3633;
        color: #ffffff;
    }

    .header-info {
        color: #8b949e;
        content-align: right middle;
    }

    #chat-container {
        height: 1fr;
        border: solid #30363d;
        background: #090d12;
        padding: 1 3;
        overflow-y: scroll;
    }

    #welcome {
        color: #8b949e;
        margin-bottom: 2;
        padding: 1 0;
    }

    #input-container {
        height: auto;
        position: relative;
    }

    CommandInput {
        height: 4;
        min-height: 4;
        margin: 0;
        border: tall #ff007f;
        background: #161b22;
        color: #f0f6fc;
        padding: 0 1;
    }

    CommandInput:focus {
        border: tall #00f0ff;
    }

    #autocomplete-popup {
        background: #161b22;
        border: solid #00f0ff;
        max-height: 8;
        margin-bottom: 0;
    }

    #footer-bar {
        height: 2;
        min-height: 2;
        background: #161b22;
        border-top: solid #30363d;
        padding: 0 2;
        align-vertical: middle;
    }

    #footer-status {
        margin-left: 2;
        text-style: bold;
        color: #00f0ff;
    }

    ChatTurn {
        margin: 1 0;
        padding: 1 2;
        border-left: solid #30363d;
        min-height: 3;
    }

    ChatTurn:focus {
        background: #161b22;
        border-left: wide #00f0ff;
    }

    .user-msg {
        color: #f0f6fc;
        background: #21262d;
        border-left: solid #ff007f;
    }

    .user-msg:focus {
        background: #30363d;
        border-left: wide #ff007f;
    }

    .agent-msg {
        color: #00f0ff;
        background: #0d1117;
        border-left: solid #00f0ff;
    }

    .agent-msg:focus {
        background: #161b22;
        border-left: wide #00f0ff;
    }

    .footer-key {
        color: #ff007f;
        text-style: bold;
    }

    .footer-text {
        color: #8b949e;
    }

    .spacer {
        width: 1fr;
    }
    """

    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("ctrl+l", "clear", "Clear Screen"),
        ("f1", "show_help", "Show Help"),
        ("question_mark", "show_help", "Show Help"),
        ("pageup", "select_prev_turn", "Select Prev Turn"),
        ("pagedown", "select_next_turn", "Select Next Turn"),
    ]

    def __init__(self):
        super().__init__()
        self.settings = load_config()
        self.__core_agent = Gaia()
        self.is_shutting_down = False

    def watch_connection_status(self, new_status: str) -> None:
        """Reactive watcher that safely updates the status badge once mounted."""
        if not self.is_mounted:
            return
        try:
            badge = self.query_one("#llm-status-badge", Static)
            badge.update(f"LLM: {new_status}")
            badge.remove_class(
                "status-online", "status-checking", "status-unreachable"
            )

            if new_status == "ONLINE":
                badge.add_class("status-online")
            elif new_status == "CHECKING...":
                badge.add_class("status-checking")
            else:
                badge.add_class("status-unreachable")

            footer_status = self.query_one("#footer-status", Static)
            footer_status.update(f"[STATUS: {new_status}]")
        except Exception:
            pass

    def force_exit(self) -> None:
        """Graceful shutdown: stop workers and exit."""
        self.is_shutting_down = True
        self.workers.cancel_all()
        self.exit()

    def action_quit(self) -> None:
        self.force_exit()

    def compose(self) -> ComposeResult:
        with Horizontal(id="custom-header"):
            yield Static(
                " G.A.I.A. // LOCAL ENGINE v0.1.0", classes="header-title"
            )
            yield Static("", classes="spacer")
            yield Static(
                f"LLM: {self.connection_status}",
                classes="llm-badge status-checking",
                id="llm-status-badge",
            )
            yield Static("", id="header-config-info", classes="header-info")

        with ScrollableContainer(id="chat-container"):
            yield Static(
                "[bold #ff007f]G.A.I.A. SYSTEM INITIALIZED[/bold #ff007f]\n"
                "[dim #8b949e]Encryption layers validated | Zero-Telemetry Mode[/dim #8b949e]\n"
                "[dim #8b949e]Type [bold #ff007f]/[/bold #ff007f] for commands "
                "or press [bold #00f0ff]F1 / ?[/bold #00f0ff] for help.[/dim #8b949e]\n",
                id="welcome",
            )

        with Vertical(id="input-container"):
            cmd_input = CommandInput(
                placeholder=(
                    "[G.A.I.A.] > Ask G.A.I.A. or type / for commands..."
                ),
                id="prompt-input",
            )
            yield cmd_input
            # Position the autocomplete popup inside this container
            cmd_input.set_parent_container(self.query_one("#input-container", Vertical))

        with Horizontal(id="footer-bar"):
            yield Static(
                "[footer-key]F1/? [/footer-key][footer-text]Help[/footer-text]  |  "
                "[footer-key]PgUp/PgDn[/footer-key] [footer-text]"
                "Navigate[/footer-text]  |  "
                "[footer-key]Ctrl+C[/footer-key] [footer-text]Quit[/footer-text]",
                classes="footer-text",
            )
            yield Static(
                f"[STATUS: {self.connection_status}]", id="footer-status"
            )
            yield Static("", classes="spacer")
            yield Static("PRIVATE. OPEN. YOURS.", classes="header-title")

    def on_mount(self) -> None:
        self._refresh_config_info()
        self.watch_connection_status(self.connection_status)
        self.ping_loop()

    def _refresh_config_info(self) -> None:
        try:
            config_info = self.query_one("#header-config-info", Static)
            enc_label = (
                "SQLCipher" if self.settings.get("encrypted", True) else "DISABLED"
            )
            config_info.update(
                f"MODEL: [bold #00f0ff]{self.settings.get('model', 'qwen2.5-coder:32b')}"
                "[/bold #00f0ff] | VAULT: {enc_label}"
            )
        except Exception:
            pass

    @work(exclusive=True, thread=True)
    async def ping_loop(self) -> None:
        while not self.is_shutting_down:
            endpoint = self.settings.get("endpoint", "http://localhost:11434")
            try:
                status = await self._check_connection(endpoint)
            except Exception:
                status = "UNREACHABLE"

            if self.is_shutting_down:
                break

            if status != self.connection_status:
                self.connection_status = status

            for _ in range(30):
                if self.is_shutting_down:
                    return
                await asyncio.sleep(0.1)

    async def _check_connection(self, endpoint: str) -> str:
        """Ping *endpoint* and return 'ONLINE' / 'UNREACHABLE' / 'HTTP <code>'."""
        return await check_endpoint(endpoint)

    def action_show_help(self) -> None:
        self.push_screen(HelpModal())

    def action_select_prev_turn(self) -> None:
        turns = list(self.query(ChatTurn))
        if not turns:
            return

        current = self.focused
        if isinstance(current, ChatTurn) and current in turns:
            idx = turns.index(current)
            new_idx = max(0, idx - 1)
        else:
            new_idx = len(turns) - 1

        turns[new_idx].focus()

    def action_select_next_turn(self) -> None:
        turns = list(self.query(ChatTurn))
        if not turns:
            return

        current = self.focused
        if isinstance(current, ChatTurn) and current in turns:
            idx = turns.index(current)
            if idx + 1 < len(turns):
                turns[idx + 1].focus()
            else:
                self.query_one("#prompt-input", CommandInput).focus()
        else:
            turns[0].focus()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        user_text = event.value.strip()
        if not user_text:
            return

        input_widget = self.query_one("#prompt-input", CommandInput)
        input_widget.value = ""

        if user_text.lower() in ("exit", "quit", "close"):
            self.force_exit()
            return

        if user_text.startswith("/"):
            await self.handle_slash_command(user_text)
            return

        chat_box = self.query_one("#chat-container", ScrollableContainer)
        await chat_box.mount(
            ChatTurn(
                f"[bold #ff007f]You:[/bold #ff007f] {user_text}",
                classes="user-msg",
            )
        )
        chat_box.scroll_end()

        self.run_agent_execution(user_text)

    async def handle_slash_command(self, raw_command: str) -> None:
        parts = raw_command[1:].split(maxsplit=1)
        cmd = parts[0].lower() if parts else ""
        arg = parts[1].strip() if len(parts) > 1 else ""

        chat_box = self.query_one("#chat-container", ScrollableContainer)

        match cmd:
            case "exit" | "quit" | "close":
                self.force_exit()

            case "help":
                self.action_show_help()

            case "model":
                if arg:
                    self.settings["model"] = arg
                    save_config(self.settings)
                    self._refresh_config_info()
                    await chat_box.mount(
                        ChatTurn(
                            f"[bold #00f0ff]Active Model Updated:[/bold #00f0ff] {arg}",
                            classes="agent-msg",
                        )
                    )
                else:
                    await chat_box.mount(
                        ChatTurn(
                            f"[bold #00f0ff]Current Model:[/bold #00f0ff] "
                            f"{self.settings['model']}\n"
                            "Usage: [bold]/model <model_name>[/bold]",
                            classes="agent-msg",
                        )
                    )

            case "settings":

                def on_settings_closed(
                    new_config: Dict[str, Any] | None,
                ) -> None:
                    if new_config:
                        self.settings.update(new_config)
                        save_config(self.settings)
                        self._refresh_config_info()

                        self.call_after_refresh(
                            chat_box.mount,
                            ChatTurn(
                                f"[bold #00f0ff]System Config Saved:[/bold #00f0ff]\n"
                                f"  • Persisted to: "
                                f"[bold]{SETTINGS_FILE.resolve()}[/bold]\n"
                                f"  • Model: "
                                f"[bold]{self.settings['model']}[/bold]\n"
                                f"  • Endpoint: {self.settings['endpoint']}\n"
                                f"  • System Prompt: "
                                f"[dim]{self.settings['system_prompt'][:60]}...[/dim]\n"
                                f"  • Vault Encryption: "
                                f"{'Enabled' if self.settings['encrypted'] else 'Disabled'}",
                                classes="agent-msg",
                            ),
                        )

                self.push_screen(
                    SettingsModal(self.settings), on_settings_closed
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

            case "status":
                status_info = (
                    "[bold #00f0ff]System Diagnostics:[/bold #00f0ff]\n"
                    f"  • LLM Endpoint Status: {self.connection_status}\n"
                    f"  • Model Runtime: {self.settings['model']}\n"
                    f"  • Host Endpoint: {self.settings['endpoint']}\n"
                    f"  • System Prompt Length: "
                    f"{len(self.settings.get('system_prompt', ''))} chars\n"
                    f"  • Vault State: "
                    f"{'AES-256 (SQLCipher)' if self.settings['encrypted'] else 'Unencrypted Plaintext'}\n"
                    f"  • Config File: {SETTINGS_FILE.resolve()}"
                )
                await chat_box.mount(
                    ChatTurn(status_info, classes="agent-msg")
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

    @work(exclusive=True, thread=False)
    async def run_agent_execution(self, prompt: str) -> None:
        chat_box = self.query_one("#chat-container", ScrollableContainer)

        agent_widget = ChatTurn(
            "[bold #00f0ff]G.A.I.A.:[/bold #00f0ff] ",
            classes="agent-msg",
        )
        await chat_box.mount(agent_widget)

        base_prefix = (
            f"[bold #00f0ff]G.A.I.A. ({self.settings['model']}):[/bold #00f0ff] "
        )
        response_accumulator = ""
        dots_states = [".  ", ".. ", "..."]
        dot_index = 0
        first_token_received = False

        async def animate_waiting() -> None:
            nonlocal dot_index
            while not first_token_received and not self.is_shutting_down:
                dot_text = dots_states[dot_index % len(dots_states)]
                agent_widget.update(
                    base_prefix + f"[dim #8b949e]{dot_text}[/dim #8b949e]"
                )
                dot_index += 1
                await asyncio.sleep(0.2)

        anim_task = asyncio.create_task(animate_waiting())

        try:
            async for token in self.__core_agent.ainteract(prompt=prompt):
                if self.is_shutting_down:
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
