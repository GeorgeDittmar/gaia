"""G.A.I.A. — entry point.

Run with:
    uv run python main.py
or after install:
    gaia
"""

from gaia import GaiaTUIApp

if __name__ == "__main__":
    app = GaiaTUIApp()
    app.run()
