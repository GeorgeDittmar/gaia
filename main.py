import json
import asyncio
from pathlib import Path
from typing import Dict, Any, List

from textual.app import App, ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Input, Static, Button, Switch, Label, OptionList
from textual.widgets.option_list import Option
from textual.containers import ScrollableContainer, Horizontal, Vertical
from textual.events import Key
from textual import work

SETTINGS_FILE = Path("settings.json")

DEFAULT_SETTINGS: Dict[str, Any] = {
    "model": "qwen2.5-coder:32b",
    "endpoint": "http://localhost:11434",
    "encrypted": True,
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
    {"cmd": "/help", "desc": "Show available commands & shortcuts"},
]


def save_config(config: Dict[str, Any]) -> None:
    """Persists settings dictionary to JSON file."""
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4)


def load_config() -> Dict[str, Any]:
    """Reads settings from disk or creates default settings.json if missing."""
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


class CommandInput(Input):
    """Input widget supporting command-level and dynamic argument-level autocomplete."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.popup: OptionList | None = None

    def on_mount(self) -> None:
        self.popup = OptionList(id="autocomplete-popup")
        self.popup.display = False
        self.app.query_one("#input-container").mount(self.popup)

    def on_input_changed(self, event: Input.Changed) -> None:
        val = event.value

        if not val.startswith("/"):
            self._hide_popup()
            return

        # Case A: Slash Command completion (e.g., "/mod")
        if " " not in val:
            matching = [
                {
                    "id": c["cmd"],
                    "label": f"[bold #ff007f]{c['cmd']}[/bold #ff007f]  [dim #8b949e]{c['desc']}[/dim #8b949e]",
                }
                for c in SLASH_COMMANDS
                if c["cmd"].lower().startswith(val.lower())
            ]
            self._render_suggestions(matching)
            return

        # Case B: Sub-command argument completion (e.g., "/model qwe")
        cmd, arg_query = val.split(" ", 1)
        cmd = cmd.lower()

        if cmd == "/model":
            matching = [
                {
                    "id": f"/model {item['arg']}",
                    "label": f"[bold #00f0ff]{item['arg']}[/bold #00f0ff]  [dim #8b949e]{item['desc']}[/dim #8b949e]",
                }
                for item in AVAILABLE_MODELS
                if item["arg"].lower().startswith(arg_query.lower())
            ]
            self._render_suggestions(matching)
        else:
            self._hide_popup()

    def _render_suggestions(self, items: List[Dict[str, str]]) -> None:
        if not items:
            self._hide_popup()
            return

        self.popup.clear_options()
        for item in items:
            self.popup.add_option(Option(item["label"], id=item["id"]))

        self.popup.highlighted = 0
        self.popup.display = True

    def _hide_popup(self) -> None:
        if self.popup:
            self.popup.display = False

    def on_key(self, event: Key) -> None:
        """Intercept popup navigation and selection."""
        if not (self.popup and self.popup.display):
            return

        if event.key in ("up", "down"):
            if event.key == "up":
                self.popup.action_cursor_up()
            else:
                self.popup.action_cursor_down()
            event.stop()
            event.prevent_default()

        elif event.key in ("tab", "enter"):
            if self.popup.highlighted is not None:
                selected_option = self.popup.get_option_at_index(
                    self.popup.highlighted
                )
                if selected_option and selected_option.id:
                    self.value = selected_option.id + " "
                    self.cursor_position = len(self.value)
                    self._hide_popup()
                    event.stop()
                    event.prevent_default()

        elif event.key == "escape":
            self._hide_popup()
            event.stop()
            event.prevent_default()


class SettingsModal(ModalScreen[Dict[str, Any]]):
    """Interactive popup window for system configuration."""

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
    ]

    CSS = """
    SettingsModal {
        align: center middle;
        background: rgba(13, 17, 23, 0.85);
    }

    #dialog {
        padding: 1 2;
        background: #161b22;
        border: thick #00f0ff;
        width: 70;
        height: auto;
        max-height: 90%;
    }

    #dialog-title {
        color: #00f0ff;
        text-style: bold;
        margin-bottom: 1;
        content-align: center middle;
        width: 100%;
    }

    .field-label {
        color: #ff007f;
        margin-top: 1;
        text-style: bold;
    }

    Input {
        border: tall #30363d;
        background: #0d1117;
        color: #f0f6fc;
        height: 3;
    }

    Input:focus {
        border: tall #00f0ff;
    }

    .switch-row {
        height: 3;
        align-vertical: middle;
        margin-top: 1;
    }

    .switch-label {
        color: #c9d1d9;
        width: 1fr;
    }

    Switch:focus {
        border: solid #00f0ff;
    }

    #button-bar {
        margin-top: 2;
        height: 3;
        align-horizontal: right;
    }

    Button {
        margin-left: 1;
        border: none;
    }

    Button:focus {
        background: #00f0ff;
        color: #0d1117;
        text-style: bold;
    }
    """

    def __init__(self, current_settings: Dict[str, Any]):
        super().__init__()
        self.current_settings = current_settings

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("— SYSTEM CONFIGURATION —", id="dialog-title")

            yield Label("Active Model Name:", classes="field-label")
            yield Input(
                value=str(self.current_settings.get("model", "qwen2.5-coder:32b")),
                placeholder="e.g. qwen2.5-coder:32b, llama3.3:70b",
                id="model-input",
            )

            yield Label("API Base URL:", classes="field-label")
            yield Input(
                value=str(
                    self.current_settings.get(
                        "endpoint", "http://localhost:11434"
                    )
                ),
                placeholder="http://localhost:11434",
                id="endpoint-input",
            )

            with Horizontal(classes="switch-row"):
                yield Label("SQLCipher Vault Encryption:", classes="switch-label")
                yield Switch(
                    value=bool(self.current_settings.get("encrypted", True)),
                    id="encrypt-switch",
                )

            with Horizontal(id="button-bar"):
                yield Button("Cancel [Esc]", id="cancel-btn", variant="error")
                yield Button("Save & Apply", id="save-btn", variant="success")

    def on_mount(self) -> None:
        self.query_one("#model-input", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit_settings()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-btn":
            self._submit_settings()
        else:
            self.dismiss(None)

    def _submit_settings(self) -> None:
        updated_config = {
            "model": self.query_one("#model-input", Input).value.strip()
            or "qwen2.5-coder:32b",
            "endpoint": self.query_one("#endpoint-input", Input).value.strip()
            or "http://localhost:11434",
            "encrypted": self.query_one("#encrypt-switch", Switch).value,
        }
        self.dismiss(updated_config)


class GaiaTUIApp(App):
    """G.A.I.A. Terminal UI with Persistent Settings & Dynamic Autocomplete."""

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
        padding: 0 1;
        align-vertical: middle;
    }

    .header-title {
        color: #00f0ff;
        text-style: bold;
    }

    .header-status {
        color: #ff007f;
        text-style: bold;
    }

    #chat-container {
        height: 1fr;
        border: solid #30363d;
        background: #090d12;
        padding: 1 2;
        overflow-y: scroll;
    }

    #welcome {
        color: #8b949e;
        margin-bottom: 1;
    }

    #input-container {
        height: auto;
        position: relative;
    }

    CommandInput {
        height: 3;
        min-height: 3;
        margin: 0;
        border: tall #ff007f;
        background: #161b22;
        color: #f0f6fc;
    }

    CommandInput:focus {
        border: tall #00f0ff;
    }

    #autocomplete-popup {
        background: #161b22;
        border: solid #00f0ff;
        max-height: 6;
        margin-bottom: 0;
    }

    #footer-bar {
        height: 1;
        min-height: 1;
        background: #161b22;
        border-top: solid #30363d;
        padding: 0 1;
        align-vertical: middle;
    }

    .user-msg {
        color: #f0f6fc;
        background: #21262d;
        border-left: solid #ff007f;
        margin: 1 0;
        padding: 0 1;
    }

    .agent-msg {
        color: #00f0ff;
        background: #0d1117;
        border-left: solid #00f0ff;
        margin: 1 0;
        padding: 0 1;
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
    ]

    def __init__(self):
        super().__init__()
        self.settings = load_config()

    def compose(self) -> ComposeResult:
        with Horizontal(id="custom-header"):
            yield Static(" G.A.I.A. // LOCAL ENGINE v0.1.0", classes="header-title")
            yield Static("", classes="spacer")
            yield Static(
                self._format_status(), classes="header-status", id="header-status"
            )

        with ScrollableContainer(id="chat-container"):
            yield Static(
                "[bold #ff007f]G.A.I.A. SYSTEM INITIALIZED[/bold #ff007f]\n"
                "[dim #8b949e]Encryption layers validated | Zero-Telemetry Mode[/dim #8b949e]\n"
                "[dim #8b949e]Type [bold #ff007f]/[/bold #ff007f] to trigger slash command recommendations.[/dim #8b949e]\n",
                id="welcome",
            )

        with Vertical(id="input-container"):
            yield CommandInput(
                placeholder="[G.A.I.A.] > Ask G.A.I.A. or type / for commands...",
                id="prompt-input",
            )

        with Horizontal(id="footer-bar"):
            yield Static(
                "[footer-key]Ctrl+C[/footer-key] [footer-text]Quit[/footer-text]  |  "
                "[footer-key]Ctrl+L[/footer-key] [footer-text]Clear[/footer-text]  |  "
                "[footer-key]Tab/Enter[/footer-key] [footer-text]Autocomplete[/footer-text]",
                classes="footer-text",
            )
            yield Static("", classes="spacer")
            yield Static("PRIVATE. OPEN. YOURS.", classes="header-title")

    def _format_status(self) -> str:
        enc_label = (
            "SQLCipher" if self.settings.get("encrypted", True) else "RAW-DISABLED"
        )
        return f"[ENCRYPTED: {enc_label}]  [MODEL: {self.settings.get('model', 'qwen2.5-coder:32b')}]"

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        user_text = event.value.strip()
        if not user_text:
            return

        input_widget = self.query_one("#prompt-input", CommandInput)
        input_widget.value = ""

        if user_text.startswith("/"):
            await self.handle_slash_command(user_text)
            return

        chat_box = self.query_one("#chat-container", ScrollableContainer)
        await chat_box.mount(
            Static(f"[bold #ff007f]You:[/bold #ff007f] {user_text}", classes="user-msg")
        )
        chat_box.scroll_end()

        self.run_agent_execution(user_text)

    async def handle_slash_command(self, raw_command: str) -> None:
        parts = raw_command[1:].split(maxsplit=1)
        cmd = parts[0].lower() if parts else ""
        arg = parts[1].strip() if len(parts) > 1 else ""

        chat_box = self.query_one("#chat-container", ScrollableContainer)

        match cmd:
            case "model":
                if arg:
                    self.settings["model"] = arg
                    save_config(self.settings)
                    header = self.query_one("#header-status", Static)
                    header.update(self._format_status())
                    await chat_box.mount(
                        Static(
                            f"[bold #00f0ff]Active Model Updated:[/bold #00f0ff] {arg}",
                            classes="agent-msg",
                        )
                    )
                else:
                    await chat_box.mount(
                        Static(
                            f"[bold #00f0ff]Current Model:[/bold #00f0ff] {self.settings['model']}\n"
                            "Usage: [bold]/model <model_name>[/bold]",
                            classes="agent-msg",
                        )
                    )

            case "settings":

                def on_settings_closed(new_config: Dict[str, Any] | None) -> None:
                    if new_config:
                        self.settings.update(new_config)
                        save_config(self.settings)

                        header = self.query_one("#header-status", Static)
                        header.update(self._format_status())

                        self.call_after_refresh(
                            chat_box.mount,
                            Static(
                                f"[bold #00f0ff]System Config Saved:[/bold #00f0ff]\n"
                                f"  • Persisted to: [bold]{SETTINGS_FILE.resolve()}[/bold]\n"
                                f"  • Model: [bold]{self.settings['model']}[/bold]\n"
                                f"  • Endpoint: {self.settings['endpoint']}\n"
                                f"  • Vault Encryption: {'Enabled' if self.settings['encrypted'] else 'Disabled'}",
                                classes="agent-msg",
                            ),
                        )

                self.push_screen(SettingsModal(self.settings), on_settings_closed)

            case "clear":
                await chat_box.remove_children()
                await chat_box.mount(
                    Static(
                        "[bold #ff007f]G.A.I.A. SYSTEM RESET[/bold #ff007f]\n"
                        "[dim #8b949e]Terminal buffer cleared.[/dim #8b949e]\n",
                        id="welcome",
                    )
                )

            case "help":
                help_text = "[bold #00f0ff]Available Slash Commands:[/bold #00f0ff]\n" + "\n".join(
                    [
                        f"  [bold #ff007f]{item['cmd']:<12}[/bold #ff007f] - {item['desc']}"
                        for item in SLASH_COMMANDS
                    ]
                )
                await chat_box.mount(Static(help_text, classes="agent-msg"))

            case "status":
                status_info = (
                    "[bold #00f0ff]System Diagnostics:[/bold #00f0ff]\n"
                    f"  • Model Runtime: {self.settings['model']}\n"
                    f"  • Host Endpoint: {self.settings['endpoint']}\n"
                    f"  • Vault State: {'AES-256 (SQLCipher)' if self.settings['encrypted'] else 'Unencrypted Plaintext'}\n"
                    f"  • Config File: {SETTINGS_FILE.resolve()}"
                )
                await chat_box.mount(Static(status_info, classes="agent-msg"))

            case _:
                await chat_box.mount(
                    Static(
                        f"[bold #ff007f]Unknown Command:[/bold #ff007f] /{cmd}. Type [bold]/[/bold] to browse valid commands.",
                        classes="agent-msg",
                    )
                )

        chat_box.scroll_end()

    @work(exclusive=True, thread=False)
    async def run_agent_execution(self, prompt: str) -> None:
        chat_box = self.query_one("#chat-container", ScrollableContainer)

        agent_widget = Static(
            "[bold #00f0ff]G.A.I.A.:[/bold #00f0ff] ", classes="agent-msg"
        )
        await chat_box.mount(agent_widget)

        response_accumulator = (
            f"[bold #00f0ff]G.A.I.A. ({self.settings['model']}):[/bold #00f0ff] "
        )
        simulated_tokens = [
            "Routing ",
            "request ",
            f"via {self.settings['endpoint']}...\n\n",
            "```python\n",
            "# Pydantask async workflow execution\n",
            "async def handle():\n",
            "    return True\n",
            "```\n",
            "Task complete.",
        ]

        for token in simulated_tokens:
            await asyncio.sleep(0.06)
            response_accumulator += token
            agent_widget.update(response_accumulator)
            chat_box.scroll_end()


if __name__ == "__main__":
    app = GaiaTUIApp()
    app.run()