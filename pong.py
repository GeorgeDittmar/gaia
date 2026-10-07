"""Two-player terminal ping pong — cyberpunk edition."""

import asyncio
from typing import Literal

from textual.app import App, ComposeResult
from textual.containers import Container
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Static

# ── colours ────────────────────────────────────────────────────────────
BG = "#0d1117"
CYAN = "#00f0ff"
MAGENTA = "#ff007f"
MUTED = "#8b949e"
BORDER = "#30363d"

# ── constants ──────────────────────────────────────────────────────────
FIELD_WIDTH = 60
FIELD_HEIGHT = 20
PADDLE_HEIGHT = 5
PADDLE_WIDTH = 1
BALL_CHAR = "O"
PADDLE_CHAR = "█"
NET_CHAR = "│"
WIN_SCORE = 11
TICK = 0.08  # seconds per game tick


class HelpScreen(ModalScreen):
    """Controls reference overlay (press H to show)."""

    CSS = """
    HelpScreen {
        align: center middle;
        background: rgba(13, 17, 23, 0.95);
    }

    #help-box {
        width: 50;
        height: 18;
        border: round #00f0ff;
        background: #161b22;
        padding: 1 2;
    }

    #help-title {
        text-align: center;
        color: #00f0ff;
        text-style: bold;
        width: 100%;
    }

    #help-body {
        margin-top: 1;
        color: #c9d1d9;
    }
    """

    def compose(self) -> ComposeResult:
        with Container(id="help-box"):
            yield Static("CONTROLS", id="help-title")
            yield Static(
                f"[cyan]{PADDLE_CHAR} * {PADDLE_HEIGHT}  Player 1 — Left  (W / S)[/cyan]\n"
                f"[magenta]{PADDLE_CHAR} * {PADDLE_HEIGHT}  Player 2 — Right (Up / Down)[/magenta]\n"
                f"Space          Pause / Resume\n"
                f"Q              Quit\n"
                f"H              Hide this screen\n\n"
                f"First to {WIN_SCORE} points wins!",
                id="help-body",
            )

    def on_key(self, event) -> None:
        if event.key == "h" or event.key == "question_mark":
            self.dismiss()


class GameOverScreen(ModalScreen):
    """Shown when a player reaches WIN_SCORE."""

    CSS = """
    GameOverScreen {
        align: center middle;
        background: rgba(13, 17, 23, 0.95);
    }

    #game-over-box {
        width: 50;
        height: 10;
        border: round #00f0ff;
        background: #161b22;
        padding: 1 2;
    }

    #game-over-title {
        text-align: center;
        color: #00f0ff;
        text-style: bold;
        width: 100%;
    }

    #game-over-body {
        margin-top: 1;
        text-align: center;
        color: #c9d1d9;
    }
    """

    def __init__(self, winner: Literal["P1", "P2"]) -> None:
        super().__init__()
        self._winner = winner

    def compose(self) -> ComposeResult:
        winner_label = "Player 1" if self._winner == "P1" else "Player 2"
        winner_color = CYAN if self._winner == "P1" else MAGENTA
        yield Container(
            Static("GAME OVER", id="game-over-title"),
            Static(
                f"[bold {winner_color}]{winner_label} wins![/bold {winner_color}]\n\n"
                "Press Space to play again, or Q to quit.",
                id="game-over-body",
            ),
            id="game-over-box",
        )


class PongApp(App):
    """Two-player ping pong with a cyberpunk aesthetic."""

    # ── reactive state ─────────────────────────────────────────────────
    score_p1 = reactive(0)
    score_p2 = reactive(0)
    paused = reactive(False)
    game_over = reactive(False)
    field: reactive[str] = reactive("")  # character grid for rendering

    # ── game logic state (not reactive) ────────────────────────────────
    _ball_x: int = FIELD_WIDTH // 2
    _ball_y: int = FIELD_HEIGHT // 2
    _vx: int = 1
    _vy: int = 1
    _p1_y: int = FIELD_HEIGHT // 2 - PADDLE_HEIGHT // 2
    _p2_y: int = FIELD_HEIGHT // 2 - PADDLE_HEIGHT // 2
    _p1_up: bool = False
    _p1_down: bool = False
    _p2_up: bool = False
    _p2_down: bool = False

    CSS = f"""
    Screen {{
        background: {BG};
        color: {MUTED};
        layout: vertical;
    }}

    #header {{
        height: 3;
        min-height: 3;
        background: #161b22;
        border-bottom: solid {BORDER};
        align: center middle;
    }}

    #scoreboard {{
        text-align: center;
        color: {MUTED};
        width: 100%;
    }}

    #field-container {{
        height: {FIELD_HEIGHT};
        width: {FIELD_WIDTH};
        border: solid {BORDER};
        background: {BG};
        margin: 0 auto;
    }}

    #field {{
        width: 100%;
        height: 100%;
        content-align: center middle;
    }}

    #footer {{
        height: 2;
        min-height: 2;
        background: #161b22;
        border-top: solid {BORDER};
        align: center middle;
    }}

    #controls {{
        text-align: center;
        width: 100%;
        color: {MUTED};
    }}

    .cyan {{ color: {CYAN}; }}
    .magenta {{ color: {MAGENTA}; }}
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("space", "toggle_pause", "Pause"),
        ("h", "show_help", "Help"),
    ]

    # ── compose ────────────────────────────────────────────────────────
    def compose(self) -> ComposeResult:
        yield Static("", id="header")
        with Container(id="field-container"):
            yield Static("", id="field")
        yield Static("", id="footer")

    # ── lifecycle ──────────────────────────────────────────────────────
    def on_mount(self) -> None:
        self._build_field()
        self.refresh_field()
        self._start_game_loop()

    # ── key handling ───────────────────────────────────────────────────
    def on_key(self, event) -> None:
        match event.key:
            case "w":
                self._p1_up = True
            case "s":
                self._p1_down = True
            case "up":
                self._p2_up = True
            case "down":
                self._p2_down = True

    def action_quit(self) -> None:
        self.workers.cancel_all()
        self.exit()

    def action_toggle_pause(self) -> None:
        if self.game_over:
            self._reset_game()
            self.score_p1 = 0
            self.score_p2 = 0
            self.paused = False
            self._start_game_loop()
            return
        self.paused = not self.paused

    def action_show_help(self) -> None:
        self.push_screen(HelpScreen())

    def key_w(self) -> None:
        self._p1_up = True

    def key_s(self) -> None:
        self._p1_down = True

    def key_up(self) -> None:
        self._p2_up = True

    def key_down(self) -> None:
        self._p2_down = True

    # ── game loop ──────────────────────────────────────────────────────
    def _start_game_loop(self) -> None:
        self._run_game()

    @work(exclusive=True, thread=False)
    async def _run_game(self) -> None:
        while not self._quit_signal:
            if not self.paused and not self.game_over:
                self._move_paddles()
                self._move_ball()
                self.refresh_field()
            await asyncio.sleep(TICK)

    _quit_signal: bool = False  # set to True when the game ends

    # ── movement ───────────────────────────────────────────────────────
    def _move_paddles(self) -> None:
        if self._p1_up:
            self._p1_y = max(0, self._p1_y - 1)
        if self._p1_down:
            self._p1_y = min(FIELD_HEIGHT - PADDLE_HEIGHT, self._p1_y + 1)
        if self._p2_up:
            self._p2_y = max(0, self._p2_y - 1)
        if self._p2_down:
            self._p2_y = min(FIELD_HEIGHT - PADDLE_HEIGHT, self._p2_y + 1)

    def _move_ball(self) -> None:
        self._ball_x += self._vx
        self._ball_y += self._vy
        self._handle_wall_bounces()
        self._handle_paddle_bounces()
        self._handle_scoring()

    def _handle_wall_bounces(self) -> None:
        if self._ball_y <= 0 or self._ball_y >= FIELD_HEIGHT - 1:
            self._vy *= -1
            self._ball_y = max(0, min(self._ball_y, FIELD_HEIGHT - 1))

    def _handle_paddle_bounces(self) -> None:
        # P1 (left)
        p1_right = 1  # column just right of paddle
        if (self._ball_x == p1_right
                and self._p1_y <= self._ball_y <= self._p1_y + PADDLE_HEIGHT - 1):
            self._bounce_paddle(1)
        # P2 (right)
        p2_left = FIELD_WIDTH - 2
        if (self._ball_x == p2_left
                and self._p2_y <= self._ball_y <= self._p2_y + PADDLE_HEIGHT - 1):
            self._bounce_paddle(-1)

    def _bounce_paddle(self, direction: int) -> None:
        """Bounce ball off paddle, adjusting angle based on hit position."""
        paddle_center = self._p1_y + PADDLE_HEIGHT // 2 if direction > 0 else self._p2_y + PADDLE_HEIGHT // 2
        offset = (self._ball_y - paddle_center) / (PADDLE_HEIGHT / 2)
        # Clamp to [-1, 1]
        offset = max(-1.0, min(1.0, offset))
        self._vx = direction * (2 + int(abs(offset)))
        self._vy = max(-3, min(3, int(offset * 3)))
        # Clamp speeds
        self._vx = max(-5, min(5, self._vx))
        self._vy = max(-3, min(3, self._vy))

    def _handle_scoring(self) -> None:
        if self._ball_x < 0:
            self.score_p2 += 1
            self._reset_ball(direction=1)
        elif self._ball_x >= FIELD_WIDTH:
            self.score_p1 += 1
            self._reset_ball(direction=-1)

    def _reset_ball(self, direction: int) -> None:
        self._ball_x = FIELD_WIDTH // 2
        self._ball_y = FIELD_HEIGHT // 2
        self._vx = direction * 2
        self._vy = (1 if self._ball_y % 2 == 0 else -1)
        # Check for game over
        if self.score_p1 >= WIN_SCORE or self.score_p2 >= WIN_SCORE:
            self.game_over = True
            self._quit_signal = True
            winner: Literal["P1", "P2"] = "P1" if self.score_p1 >= WIN_SCORE else "P2"
            self.call_after_refresh(self._show_game_over, winner)

    def _reset_game(self) -> None:
        self._ball_x = FIELD_WIDTH // 2
        self._ball_y = FIELD_HEIGHT // 2
        self._vx = -2
        self._vy = 1
        self._p1_y = FIELD_HEIGHT // 2 - PADDLE_HEIGHT // 2
        self._p2_y = FIELD_HEIGHT // 2 - PADDLE_HEIGHT // 2
        self._p1_up = self._p1_down = False
        self._p2_up = self._p2_down = False
        self._quit_signal = False
        self.game_over = False
        self.paused = False

    def _show_game_over(self, winner: Literal["P1", "P2"]) -> None:
        self.push_screen(GameOverScreen(winner))

    # ── rendering ──────────────────────────────────────────────────────
    def _build_field(self) -> None:
        """Build an empty field grid filled with spaces."""
        grid: list[str] = [" "] * (FIELD_WIDTH * FIELD_HEIGHT)
        # Draw net (dashed vertical line at center)
        center = FIELD_WIDTH // 2
        for y in range(FIELD_HEIGHT):
            grid[y * FIELD_WIDTH + center] = NET_CHAR
        self.field = "".join(grid)

    def refresh_field(self) -> None:
        """Rebuild the field grid and update the Static widget."""
        grid: list[str] = list(self.field)

        # Draw P1 paddle (cyan)
        for dy in range(PADDLE_HEIGHT):
            idx = (self._p1_y + dy) * FIELD_WIDTH + 0
            grid[idx] = PADDLE_CHAR

        # Draw P2 paddle (magenta)
        for dy in range(PADDLE_HEIGHT):
            idx = (self._p2_y + dy) * FIELD_WIDTH + (FIELD_WIDTH - 1)
            grid[idx] = PADDLE_CHAR

        # Draw ball (cyan, overwrites net if overlapping)
        ball_idx = self._ball_y * FIELD_WIDTH + self._ball_x
        grid[ball_idx] = BALL_CHAR

        text = "\n".join("".join(grid[y * FIELD_WIDTH:(y + 1) * FIELD_WIDTH])
                         for y in range(FIELD_HEIGHT))

        # Update field display
        try:
            field_widget = self.query_one("#field", Static)
            field_widget.update(
                f"[bold {CYAN}]{text}[/bold {CYAN}]"
            )
        except Exception:
            pass

        # Update scoreboard
        try:
            header = self.query_one("#header")
            header.update(
                f"[bold {CYAN}]P1: {self.score_p1}[/bold {CYAN}]  "
                f"  [bold {MAGENTA}]  P2: {self.score_p2}[/bold {MAGENTA}]"
            )
        except Exception:
            pass

        # Update footer
        try:
            footer = self.query_one("#footer")
            pause_label = "[bold yellow]PAUSED[/bold yellow]" if self.paused else ""
            footer.update(
                f"[bold {CYAN}]W/S[/bold {CYAN}] P1  |  "
                f"[bold {MAGENTA}]Up/Down[/bold {MAGENTA}] P2  |  "
                f"Space: {'Pause' if not self.paused else 'Resume'}  |  "
                f"Q: Quit  |  "
                f"{pause_label}"
            )
        except Exception:
            pass


if __name__ == "__main__":
    app = PongApp()
    app.run()
