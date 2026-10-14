"""Main G.A.I.A. terminal UI application (GaiaTUIApp).

This module defines the Textual application, chat turns, and UI layout.
Business logic is extracted into separate modules:

- ``slash_commands`` — slash command dispatch
- ``streaming`` — agent streaming loop
- ``memory_lifecycle`` — memory store initialisation and shutdown
"""

import asyncio
import logging
from datetime import datetime
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.reactive import reactive
from textual.widgets import Input, Static

from gaia.command_input import CommandInput
from gaia.config import (
    AVAILABLE_MODELS,
    check_endpoint,
    load_config,
)
from gaia.core.agent import Gaia
from gaia.core.memory.factory import MemoryFactory
from gaia.help_modal import HelpModal
from gaia.memory_lifecycle import MemoryLifecycle
from gaia.slash_commands import SlashCommandHandler
from gaia.streaming import run_agent_stream

logger = logging.getLogger("gaia.tui")


def _setup_logging() -> None:
    """Configure file-based logging with a session timestamped log file."""
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"gaia-{ts}.log"

    formatter = logging.Formatter(
        "%(asctime)s [%(name)-20s] %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(formatter)
    fh.setLevel(logging.DEBUG)

    # Attach to root logger so gaia.* loggers inherit it
    root = logging.getLogger()
    for h in root.handlers:
        if isinstance(h, logging.FileHandler):
            root.removeHandler(h)
    root.addHandler(fh)
    root.setLevel(logging.DEBUG)

    logger.debug("Logging to %s", log_file)


class ChatTurn(Static):
    """A single message turn in the chat transcript."""

    can_focus = True

    def on_focus(self) -> None:
        self.scroll_visible()


class GaiaTUIApp(App):
    """G.A.I.A. Dark Cyberpunk Terminal UI."""

    connection_status = reactive("CHECKING...")
    _debug_mode = False

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
        padding: 0 2;
        margin-right: 2;
        margin-left: 2;
        content-align: center middle;
        min-width: 30;
    }

    #llm-status-indicator {
        height: 1;
        min-height: 1;
        padding: 0 2;
        content-align: center middle;
        min-width: 40;
    }

    .indicator-online {
        background: #238636;
        color: #ffffff;
        text-style: bold;
    }

    .indicator-checking {
        background: #d29922;
        color: #0d1117;
        text-style: bold;
    }

    .indicator-unreachable {
        background: #da3633;
        color: #ffffff;
        text-style: bold;
        border-left: wide #f85149;
    }

    #offline-banner {
        height: 1;
        background: #da3633;
        color: #ffffff;
        content-align: center middle;
        text-style: bold;
        dock: top;
        visibility: hidden;
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
        _setup_logging()
        logger.info("GaiaTUIApp starting up")
        self.settings = load_config()
        try:
            self._memory = MemoryFactory.create(self.settings)
        except Exception:
            logger.exception("memory factory failed — memory disabled")
            self._memory = None
        self.__core_agent = Gaia(
            system_prompt=self.settings.system_prompt,
            model_name=self.settings.model,
            endpoint=self.settings.endpoint,
            memory=self._memory,
        )
        self._slash_handler = SlashCommandHandler(self)
        self.is_shutting_down = False
        self._turn_count = 0
        self.__warn_if_unknown_model()
        self._schedule_startup_extraction()

    async def _close_memory(self) -> None:
        """Close the memory store on shutdown."""
        await MemoryLifecycle.close(self._memory)

    def watch_connection_status(self, new_status: str) -> None:
        """Reactive watcher that safely updates the status indicator and banner."""
        if not self.is_mounted:
            return
        try:
            indicator = self.query_one("#llm-status-indicator", Static)
            indicator.remove_class(
                "indicator-online", "indicator-checking", "indicator-unreachable"
            )
            indicator.update(f"[bold]{new_status}[/bold]")

            if new_status == "ONLINE":
                indicator.add_class("indicator-online")
                # Hide offline banner
                banner = self.query_one("#offline-banner", Static)
                banner.set_visible(False)
            elif new_status == "CHECKING...":
                indicator.add_class("indicator-checking")
                banner = self.query_one("#offline-banner", Static)
                banner.set_visible(False)
            else:
                indicator.add_class("indicator-unreachable")
                banner = self.query_one("#offline-banner", Static)
                banner.set_visible(True)

            footer_status = self.query_one("#footer-status", Static)
            footer_status.update(f"[STATUS: {new_status}]")
        except Exception:
            pass

    def __warn_if_unknown_model(self) -> None:
        """Warn the user if the configured model is not in the known model list."""
        known = {m["arg"] for m in AVAILABLE_MODELS}
        if self.settings.model not in known:
            logger.warning(
                "UNRECOGNIZED MODEL '%s' — not in known models: %s",
                self.settings.model,
                ", ".join(sorted(known)),
            )

    # ── Fact extraction scheduling ─────────────────────────────────

    _EXTRACT_EVERY_N_TURNS = 3

    def _schedule_startup_extraction(self) -> None:
        """Extract facts from any episodic memory that hasn't been processed yet."""
        if self._memory is None:
            return
        self.call_after_refresh(self._run_periodic_extraction)

    @work(exclusive=False, thread=False)
    async def _run_periodic_extraction(self) -> None:
        """Batch-extract facts from episodic memory every N turns."""
        if self._memory is None:
            return
        try:
            count = await self._GaiaTUIApp__core_agent.extract_from_history(
                max_turns=10,
            )
            if count > 0:
                logger.info("periodic extraction saved %d facts", count)
        except Exception:
            logger.exception("periodic extraction failed")

    def force_exit(self) -> None:
        """Graceful shutdown: close memory, stop workers and exit.

        When called during normal app shutdown the event loop is already
        running, so ``run_until_complete`` would raise.  We use
        ``create_task`` (keeping a reference to prevent GC) when a loop
        is active, falling back to ``asyncio.run()`` otherwise.
        """
        self.is_shutting_down = True
        # Final periodic extraction before shutdown
        self._schedule_shutdown_extraction()
        if self._memory is not None:
            try:
                loop = asyncio.get_running_loop()
                task = loop.create_task(self._close_memory())
                self.__close_task = task  # keep reference to prevent GC
            except RuntimeError:
                asyncio.run(self._close_memory())
        self.workers.cancel_all()
        self.exit()

    def _schedule_shutdown_extraction(self) -> None:
        """Run batch extraction one last time before the session ends."""
        if self._memory is None:
            return
        try:
            # @work methods are started by calling them directly
            self._run_periodic_extraction()
        except Exception:
            pass  # Best effort during shutdown

    def action_quit(self) -> None:
        self.force_exit()

    def compose(self) -> ComposeResult:
        with Horizontal(id="custom-header"):
            yield Static(
                " G.A.I.A. // LOCAL ENGINE v0.1.0", classes="header-title"
            )
            yield Static("", classes="spacer")
            yield Static(
                f"[bold]{self.connection_status}[/bold]",
                classes="indicator-checking",
                id="llm-status-indicator",
            )
            yield Static("", id="header-config-info", classes="header-info")

        yield Static(
            "[bold red]⚠ LLM UNREACHABLE — cannot connect to the configured endpoint. "
            "Check your settings or verify the server is running.[/bold red]",
            id="offline-banner",
        )

        with ScrollableContainer(id="chat-container"):
            yield Static(
                "[bold #ff007f]G.A.I.A. SYSTEM INITIALIZED[/bold #ff007f]\n"
                "[dim #8b949e]Encryption layers validated | Zero-Telemetry Mode[/dim #8b949e]\n"
                "[dim #8b949e]Type [bold #ff007f]/[/bold #ff007f] for commands "
                "or press [bold #00f0ff]F1 / ?[/bold #00f0ff] for help.[/dim #8b949e]\n",
                id="welcome",
            )

        with Vertical(id="input-container"):
            yield CommandInput(
                placeholder=(
                    "[G.A.I.A.] > Ask G.A.I.A. or type / for commands..."
                ),
                id="prompt-input",
            )

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
        self._mount_autocomplete_popup()
        if self._memory is not None:
            try:
                task = MemoryLifecycle.create_init_task(self._memory)
                self.__memory_init_task = task
                task.add_done_callback(self.__on_memory_init_done)
            except RuntimeError:
                logger.warning("no event loop — memory init deferred")
            except Exception:
                logger.exception("memory init failed — memory disabled")
                self._memory = None

    def __on_memory_init_done(self, task: asyncio.Task) -> None:
        """Retrieve the memory-initialisation result to suppress warnings."""
        try:
            task.result()
        except Exception:
            logger.exception("memory initialization failed — memory disabled")
            self._memory = None

    def _mount_autocomplete_popup(self) -> None:
        """Create and mount the autocomplete popup into the input container.

        Must be called in on_mount() after the widget tree is fully built
        so that query_one() can find the input container and CommandInput.
        """
        try:
            container = self.query_one("#input-container")
            cmd_input = self.query_one("#prompt-input", CommandInput)
            if cmd_input._popup:
                container.mount(cmd_input._popup)
        except Exception:
            pass  # Gracefully degrade if anything goes wrong

    def _refresh_config_info(self) -> None:
        try:
            config_info = self.query_one("#header-config-info", Static)
            enc_label = (
                "SQLCipher" if self.settings.encrypted else "DISABLED"
            )
            config_info.update(
                f"MODEL: [bold #00f0ff]{self.settings.model}"
                f"[/bold #00f0ff] | VAULT: {enc_label}"
            )
        except Exception:
            pass

    @work(exclusive=True)
    async def ping_loop(self) -> None:
        while not self.is_shutting_down:
            endpoint = self.settings.endpoint
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
            await self._slash_handler.handle_slash_command(user_text)
            return

        # Track turns for periodic fact extraction
        self._turn_count += 1

        chat_box = self.query_one("#chat-container", ScrollableContainer)
        await chat_box.mount(
            ChatTurn(
                f"[bold #ff007f]You:[/bold #ff007f] {user_text}",
                classes="user-msg",
            )
        )
        chat_box.scroll_end()

        self.run_agent_execution(user_text)

    @work(exclusive=True, thread=False)
    async def run_agent_execution(self, prompt: str) -> None:
        """Delegate to the streaming module, then trigger post-turn extraction."""
        await run_agent_stream(self, prompt)

        # Periodic batch extraction after every N agent responses
        if self._memory is not None and (
            self._turn_count % self._EXTRACT_EVERY_N_TURNS == 0
        ):
            logger.info(
                "turn %d — triggering periodic fact extraction", self._turn_count
            )
            self.call_later(self._run_periodic_extraction)
