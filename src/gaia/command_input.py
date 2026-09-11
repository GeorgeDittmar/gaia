"""Custom input widget with slash-command autocomplete."""

from typing import Dict, List

from textual.reactive import reactive
from textual.widgets import Input, OptionList
from textual.widgets.option_list import Option

from gaia.config import SLASH_COMMANDS, AVAILABLE_MODELS


class CommandInput(Input):
    """Text input with inline slash-command / model autocomplete.

    The autocomplete popup is created in on_mount but must be
    mounted into a container by the parent app (GaiaTUIApp)
    after the widget tree is fully built.  Call
    ``popup_ready()`` from the app's ``on_mount`` once the
    popup is mounted into the DOM.
    """

    _popup: OptionList | None = reactive(None)

    def on_mount(self) -> None:
        self._popup = OptionList(id="autocomplete-popup")
        self._popup.display = False

    def on_input_changed(self, event: Input.Changed) -> None:
        val = event.value

        if not val.startswith("/"):
            self._hide_popup()
            return

        if " " not in val:
            matching = [
                {
                    "id": c["cmd"],
                    "label": (
                        f"[bold #ff007f]{c['cmd']}[/bold #ff007f]  "
                        f"[dim #8b949e]{c['desc']}[/dim #8b949e]"
                    ),
                }
                for c in SLASH_COMMANDS
                if c["cmd"].lower().startswith(val.lower())
            ]
            self._render_suggestions(matching)
            return

        cmd, arg_query = val.split(" ", 1)
        cmd = cmd.lower()

        if cmd == "/model":
            matching = [
                {
                    "id": f"/model {item['arg']}",
                    "label": (
                        f"[bold #00f0ff]{item['arg']}[/bold #00f0ff]  "
                        f"[dim #8b949e]{item['desc']}[/dim #8b949e]"
                    ),
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
        self._popup.clear_options()  # type: ignore[reportOptionalMemberAccess]
        for item in items:
            self._popup.add_option(Option(item["label"], id=item["id"]))  # type: ignore[reportOptionalMemberAccess]
        self._popup.highlighted = 0  # type: ignore[reportOptionalMemberAccess]
        self._popup.display = True  # type: ignore[reportOptionalMemberAccess]

    def _hide_popup(self) -> None:
        if self._popup:
            self._popup.display = False

    def on_key(self, event) -> None:  # type: ignore[reportUnusedCoroutine]
        if not (self._popup and self._popup.display):
            return

        if event.key in ("up", "down"):
            if event.key == "up":
                self._popup.action_cursor_up()  # type: ignore[reportOptionalMemberAccess]
            else:
                self._popup.action_cursor_down()  # type: ignore[reportOptionalMemberAccess]
            event.stop()
            event.prevent_default()

        elif event.key in ("tab", "enter"):
            if self._popup.highlighted is not None:  # type: ignore[reportOptionalMemberAccess]
                selected_option = self._popup.get_option_at_index(  # type: ignore[reportOptionalMemberAccess]
                    self._popup.highlighted  # type: ignore[reportOptionalMemberAccess]
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
