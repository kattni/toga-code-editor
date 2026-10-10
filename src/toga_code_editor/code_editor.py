from __future__ import annotations

import asyncio
import warnings
from functools import cached_property
from typing import Any

import toga
from toga.fonts import MONOSPACE
from toga.platform import get_factory
from toga.widgets.base import StyleT
from toga.widgets.multilinetextinput import OnChangeHandler

from .highlighting import (
    DEFAULT_THEME,
    Highlighter,
    NullHighlighter,
    PygmentsHighlighter,
    Theme,
)

REHIGHLIGHT_DELAY = 0.15
"""Seconds to wait after the last native edit before re-highlighting."""


def _highlighter_for(language: str | None) -> Highlighter:
    return NullHighlighter() if language is None else PygmentsHighlighter(language)


def _report_rehighlight_failure(task: asyncio.Task) -> None:
    # Hand a failure to the loop's exception handler as soon as the task finishes.
    # Otherwise asyncio only reports it when the task object is garbage collected.
    if not task.cancelled() and (exc := task.exception()) is not None:
        task.get_loop().call_exception_handler(
            {"message": "Re-highlighting the code editor failed", "exception": exc}
        )


class CodeEditor(toga.MultilineTextInput):
    def __init__(
        self,
        id: str | None = None,
        style: StyleT | None = None,
        value: str | None = None,
        readonly: bool = False,
        placeholder: str | None = None,
        on_change: OnChangeHandler | None = None,
        language: str | None = None,
        show_line_numbers: bool = True,
        theme: Theme | None = None,
        **kwargs,
    ):
        """Create a new code editor.

        :param id: The ID for the widget.
        :param style: A style object. If no style is provided, a default style will
            be applied to the widget.
        :param value: The initial content to display in the widget.
        :param readonly: Can the value of the widget be modified by the user?
        :param placeholder: The content to display as a placeholder when there is no
            user content to display.
        :param on_change: A handler that will be invoked when the value of the widget
            changes.
        :param language: A Pygments lexer alias such as ``"python"``, or ``None`` for
            no highlighting.
        :param show_line_numbers: Whether to show the line-number gutter.
        :param theme: A mapping from :class:`TokenKind` to :class:`Style`, or ``None``
            for the default theme.
        :param kwargs: Initial style properties. Unless a font family is given here or
            on ``style``, the editor uses a monospace font.
        """
        if "font_family" not in kwargs and (
            style is None or "font_family" not in style
        ):
            kwargs["font_family"] = MONOSPACE

        # Resolve the highlighting state before the widget exists: an unknown
        # language raises before anything is created, and the inherited constructor
        # paints the initial value once, with the highlighter and theme in hand.
        self._highlighter: Highlighter = _highlighter_for(language)
        self._language = language
        self._theme: Theme = DEFAULT_THEME if theme is None else theme
        self._show_line_numbers = True
        self._pending_rehighlight: asyncio.Task | None = None

        super().__init__(
            id=id,
            style=style,
            value=value,
            readonly=readonly,
            placeholder=placeholder,
            on_change=on_change,
            **kwargs,
        )

        self.show_line_numbers = show_line_numbers

    @cached_property
    def factory(self):
        # This is an official BeeWare external widget, so its interface group uses the
        # "toga_" prefix. Toga currently warns about any "toga_" interface it does not
        # know. Until Toga provides a way to register external widgets, swallow that
        # one warning here rather than letting every app print it at startup.
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="Unrecognized official Toga interface",
                category=RuntimeWarning,
            )
            return get_factory("toga_code_editor")

    def _create(self) -> Any:
        impl = self.factory.CodeEditor(interface=self)
        # The backend needs the theme before the inherited constructor sets the
        # initial value, which paints the first highlights.
        impl.set_theme(self._theme)
        return impl

    @toga.MultilineTextInput.value.setter
    def value(self, value: object) -> None:
        toga.MultilineTextInput.value.fset(self, value)
        self._rehighlight()

    @property
    def language(self) -> str | None:
        """The Pygments lexer alias used for highlighting, or ``None`` for none.

        Setting an alias Pygments does not know raises :exc:`ValueError` and leaves
        the previous language in place.
        """
        return self._language

    @language.setter
    def language(self, value: str | None) -> None:
        highlighter = _highlighter_for(value)
        self._language = value
        self._highlighter = highlighter
        self._rehighlight()

    @property
    def theme(self) -> Theme:
        """The mapping from token kind to style; ``None`` restores the default."""
        return self._theme

    @theme.setter
    def theme(self, value: Theme | None) -> None:
        self._theme = DEFAULT_THEME if value is None else value
        self._impl.set_theme(self._theme)
        self._rehighlight()

    @property
    def show_line_numbers(self) -> bool:
        """Whether the line-number gutter is shown."""
        return self._show_line_numbers

    @show_line_numbers.setter
    def show_line_numbers(self, value: object) -> None:
        self._show_line_numbers = bool(value)
        self._impl.set_show_line_numbers(self._show_line_numbers)

    def _rehighlight(self) -> None:
        self._cancel_pending_rehighlight()
        self._impl.set_highlights(self._highlighter.highlight(self.value))

    def _schedule_rehighlight(self) -> None:
        """Called by the backend when the user edits the text.

        Re-lexing on every keystroke would be wasteful, so wait for a short pause.
        This is a task rather than a bare ``call_later`` because Toga's Android event
        loop only arms its next wakeup for work scheduled through ``call_soon``; a
        timer added from a native callback would otherwise never fire.
        """
        self._cancel_pending_rehighlight()
        task = toga.App.app.loop.create_task(self._rehighlight_after_delay())
        task.add_done_callback(_report_rehighlight_failure)
        self._pending_rehighlight = task

    async def _rehighlight_after_delay(self) -> None:
        await asyncio.sleep(REHIGHLIGHT_DELAY)
        self._pending_rehighlight = None
        self._rehighlight()

    def _cancel_pending_rehighlight(self) -> None:
        if self._pending_rehighlight is not None:
            self._pending_rehighlight.cancel()
            self._pending_rehighlight = None
