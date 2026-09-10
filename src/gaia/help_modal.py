"""Help / reference overlay for the G.A.I.A. TUI."""

from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Static, Button, Label
from textual.containers import Vertical, Horizontal

from gaia.config import SLASH_COMMANDS


class HelpModal(ModalScreen[None]):
    """Interactive keyboard shortcut and command reference overlay."""

    BINDINGS = [
        ("escape", "dismiss_help", "Close Help"),
        ("q", "dismiss_help", "Close Help"),
        ("f1", "dismiss_help", "Close Help"),
    ]

    CSS = """
    HelpModal {
        align: center middle;
        background: rgba(13, 17, 23, 0.88);
    }

    #help-dialog {
        padding: 1 3;
        background: #161b22;
        border: thick #00f0ff;
        width: 86;
        height: auto;
        max-height: 85%;
    }

    #help-title {
        color: #00f0ff;
        text-style: bold;
        margin-bottom: 1;
        content-align: center middle;
        width: 100%;
        height: 2;
    }

    .help-section-header {
        color: #ff007f;
        text-style: bold;
        margin-top: 1;
        margin-bottom: 0;
        border-bottom: solid #30363d;
        height: 2;
    }

    .help-row {
        height: 2;
        margin: 0;
        align-vertical: middle;
    }

    .key-col {
        width: 28;
        color: #00f0ff;
        text-style: bold;
    }

    .desc-col {
        width: 1fr;
        color: #c9d1d9;
    }

    #help-button-bar {
        margin-top: 2;
        height: 3;
        align-horizontal: center;
    }

    #close-help-btn {
        border: none;
        background: #ff007f;
        color: #0d1117;
        text-style: bold;
        height: 3;
        padding: 0 3;
    }

    #close-help-btn:focus {
        background: #00f0ff;
        color: #0d1117;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="help-dialog"):
            yield Static(
                "— G.A.I.A. SYSTEM REFERENCE —", id="help-title"
            )
            yield Static(
                "GLOBAL KEYBOARD SHORTCUTS", classes="help-section-header"
            )

            keyboard_shortcuts = [
                ("F1 / Shift+?", "Toggle this reference overlay"),
                ("PageUp / PageDown", "Navigate and highlight chat turns"),
                ("Ctrl + C", "Force quit the application"),
                ("Ctrl + L", "Clear the active conversation buffer"),
                ("Up / Down", "Navigate autocomplete popup options"),
                ("Tab / Enter", "Select & confirm highlighted autocomplete"),
                ("Escape", "Dismiss active popups or modals"),
            ]

            for key, desc in keyboard_shortcuts:
                with Horizontal(classes="help-row"):
                    yield Static(key, classes="key-col")
                    yield Static(desc, classes="desc-col")

            yield Static(
                "SLASH COMMANDS", classes="help-section-header"
            )

            for cmd_info in SLASH_COMMANDS:
                with Horizontal(classes="help-row"):
                    yield Static(cmd_info["cmd"], classes="key-col")
                    yield Static(cmd_info["desc"], classes="desc-col")

            with Horizontal(id="help-button-bar"):
                yield Button("Close [Esc]", id="close-help-btn")

    def on_mount(self) -> None:
        self.query_one("#close-help-btn", Button).focus()

    def action_dismiss_help(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close-help-btn":
            self.dismiss(None)
