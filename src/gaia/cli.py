"""CLI entry point for G.A.I.A.

Installed as the ``gaia`` console script via pyproject.toml.
When you type ``gaia`` in the terminal, this function runs.
"""

from gaia.gaia_tui_app import GaiaTUIApp


def main() -> None:
    """Start the G.A.I.A. TUI application."""
    app = GaiaTUIApp()
    app.run()


if __name__ == "__main__":
    main()
