"""Clipboard helpers and mixins for the Textual TUI."""

from __future__ import annotations

import inspect
from typing import Optional, TypeVar, Generic

from textual.app import App
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Input


async def copy_text_to_clipboard(app: App, text: str) -> bool:
    """Copy text to the system clipboard via Textual's API.

    Args:
        app: Running Textual App instance.
        text: Plain text payload to copy.

    Returns:
        True if the text was copied successfully.
    """

    copy_method = getattr(app, "copy_to_clipboard", None)
    if copy_method is None:
        app.notify("Clipboard copy is not supported in this terminal", severity="warning")
        return False

    try:
        result = copy_method(text)
        if inspect.isawaitable(result):
            await result
    except Exception as exc:  # pragma: no cover - platform specific errors
        app.notify(f"Copy failed: {exc}", severity="error")
        return False

    return True


class ClipboardMixin:
    """Provide reusable helpers for copying text from widgets."""

    async def _copy_text(self, text: str, *, success_message: Optional[str] = None) -> bool:
        """Copy arbitrary text and show success feedback when needed."""

        if not text:
            self.app.notify("Nothing to copy", severity="warning")
            return False

        copied = await copy_text_to_clipboard(self.app, text)
        if copied and success_message:
            self.app.notify(success_message)
        return copied

    async def _copy_input_if_focused(self, *, success_message: str = "Copied input text") -> bool:
        """Copy the selection from the focused Input widget if available."""

        focused = self.app.focused
        if isinstance(focused, Input):
            text = self._get_input_selection(focused)
            if text:
                await self._copy_text(text, success_message=success_message)
            else:
                self.app.notify("Input is empty", severity="warning")
            return True
        return False

    @staticmethod
    def _get_input_selection(widget: Input) -> str:
        """Extract text from the current selection (or the entire value)."""

        selection = widget.selection
        start = min(selection.start, selection.end)
        end = max(selection.start, selection.end)
        if start != end:
            return widget.value[start:end]
        return widget.value


TModal = TypeVar("TModal")


class ClipboardModalScreen(ClipboardMixin, ModalScreen[TModal], Generic[TModal]):
    """Modal screen base class with Ctrl+Shift+C support."""

    BINDINGS = [Binding("ctrl+shift+c", "copy_selection", "Copy", show=False)]

    async def action_copy_selection(self) -> None:  # pragma: no cover - UI only
        handled = await self._copy_input_if_focused()
        if not handled:
            self.app.notify("No selectable text in this dialog", severity="warning")
