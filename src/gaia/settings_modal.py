"""Settings / configuration modal dialog."""

from typing import Any, Dict

from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Input, Static, Button, Switch, Label, TextArea
from textual.containers import ScrollableContainer, Horizontal, Vertical

from gaia.config import DEFAULT_SETTINGS


class SettingsModal(ModalScreen[Dict[str, Any]]):
    """Interactive pop-up sub-window for system configuration."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    CSS = """
    SettingsModal {
        align: center middle;
        background: rgba(13, 17, 23, 0.85);
    }

    #dialog {
        padding: 1 3;
        background: #161b22;
        border: thick #00f0ff;
        width: 78;
        height: 100%;
        max-height: 40;
    }

    #dialog-scroll {
        height: 1fr;
        overflow-y: scroll;
        padding-right: 1;
    }

    #dialog-title {
        color: #00f0ff;
        text-style: bold;
        margin-bottom: 1;
        content-align: center middle;
        width: 100%;
        height: 2;
    }

    .field-label {
        color: #ff007f;
        margin-top: 1;
        text-style: bold;
        height: 2;
    }

    Input {
        border: tall #30363d;
        background: #0d1117;
        color: #f0f6fc;
        height: 3;
        padding: 0 1;
    }

    Input:focus {
        border: tall #00f0ff;
    }

    TextArea {
        border: tall #30363d;
        background: #0d1117;
        color: #f0f6fc;
        height: 7;
    }

    TextArea:focus {
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
        min-height: 3;
        align-horizontal: right;
    }

    Button {
        margin-left: 1;
        border: none;
        height: 3;
        min-width: 12;
        padding: 0 2;
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

            with ScrollableContainer(id="dialog-scroll"):
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

                yield Label("System Prompt:", classes="field-label")
                yield TextArea(
                    text=str(
                        self.current_settings.get(
                            "system_prompt", DEFAULT_SETTINGS["system_prompt"],
                        )
                    ),
                    id="system-prompt-textarea",
                )

                with Horizontal(classes="switch-row"):
                    yield Label(
                        "SQLCipher Vault Encryption:", classes="switch-label"
                    )
                    yield Switch(
                        value=bool(self.current_settings.get("encrypted", True)),
                        id="encrypt-switch",
                    )

            with Horizontal(id="button-bar"):
                yield Button(
                    "Cancel [Esc]", id="cancel-btn", variant="error"
                )
                yield Button("Save & Apply", id="save-btn", variant="success")

    def on_mount(self) -> None:
        self.query_one("#model-input", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-btn":
            self._submit_settings()
        else:
            self.dismiss(None)

    def _submit_settings(self) -> None:
        updated_config = {
            "model": (
                self.query_one("#model-input", Input).value.strip()
                or "qwen2.5-coder:32b"
            ),
            "endpoint": (
                self.query_one("#endpoint-input", Input).value.strip()
                or "http://localhost:11434"
            ),
            "system_prompt": (
                self.query_one("#system-prompt-textarea", TextArea).text.strip()
                or DEFAULT_SETTINGS["system_prompt"]
            ),
            "encrypted": self.query_one("#encrypt-switch", Switch).value,
        }
        self.dismiss(updated_config)
