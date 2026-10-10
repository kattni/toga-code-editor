import asyncio

import pytest
from toga.fonts import MONOSPACE, SERIF, SYSTEM
from toga.style import Pack
from toga_dummy.utils import (
    EventLog,
    assert_action_performed,
    assert_action_performed_with,
    attribute_value,
)

from toga_code_editor import DEFAULT_THEME, CodeEditor, Span, Style, TokenKind
from toga_code_editor.code_editor import REHIGHLIGHT_DELAY

X_EQUALS_ONE = [Span(2, 3, TokenKind.OPERATOR), Span(4, 5, TokenKind.NUMBER)]


def test_defaults(app):
    editor = CodeEditor()

    assert_action_performed(editor, "create CodeEditor")
    assert editor.language is None
    assert editor.theme is DEFAULT_THEME
    assert editor.show_line_numbers
    assert editor.style.font_family == [MONOSPACE]
    assert attribute_value(editor, "highlights") == []
    assert attribute_value(editor, "theme") is DEFAULT_THEME
    assert attribute_value(editor, "show_line_numbers") is True


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"font_family": SERIF}, [SERIF]),
        ({"style": Pack(font_family=SERIF)}, [SERIF]),
        ({"style": Pack(font_family=SYSTEM)}, [SYSTEM]),
        ({"style": Pack(flex=1)}, [MONOSPACE]),
    ],
)
def test_font_family(app, kwargs, expected):
    """Monospace is the default unless the caller chose a font family."""
    assert CodeEditor(**kwargs).style.font_family == expected


def test_language(app):
    editor = CodeEditor(value="x = 1")

    editor.language = "python"
    assert editor.language == "python"
    assert attribute_value(editor, "highlights") == X_EQUALS_ONE

    with pytest.raises(ValueError, match="nope"):
        editor.language = "nope"
    assert editor.language == "python"

    editor.language = None
    assert attribute_value(editor, "highlights") == []


def test_theme_and_line_numbers(app):
    editor = CodeEditor(value="x = 1", language="python")

    theme = {TokenKind.NUMBER: Style("red")}
    editor.theme = theme
    assert editor.theme is theme
    assert attribute_value(editor, "theme") is theme

    editor.theme = None
    assert editor.theme is DEFAULT_THEME

    editor.show_line_numbers = False
    assert not editor.show_line_numbers
    assert attribute_value(editor, "show_line_numbers") is False


def test_construction_highlights_once(app):
    """The initial value, language, and theme are painted in a single pass."""
    theme = {TokenKind.NUMBER: Style("red")}
    editor = CodeEditor(value="x = 1", language="python", theme=theme)

    assert EventLog.values(editor, "highlights") == [X_EQUALS_ONE]
    # The backend had the theme before that paint.
    assert_action_performed_with(editor, "paint", theme=theme)


def test_value_rehighlights(app):
    editor = CodeEditor(language="python")
    editor.value = "x = 1"
    assert attribute_value(editor, "highlights") == X_EQUALS_ONE


async def test_native_change_debounces(app):
    changes = []
    editor = CodeEditor(
        language="python",
        on_change=lambda widget, **kwargs: changes.append(widget.value),
    )

    # Simulate typing: the native value changes without going through the interface,
    # then the backend's change callback fires, twice in quick succession.
    editor._impl._set_value("value", "x = 1")
    editor._impl.simulate_change()
    editor._impl.simulate_change()

    assert changes == ["x = 1", "x = 1"]
    assert attribute_value(editor, "highlights") == []
    # A task, not a bare call_later: Toga's Android loop only wakes for the former.
    assert isinstance(editor._pending_rehighlight, asyncio.Task)

    await asyncio.sleep(REHIGHLIGHT_DELAY * 2)
    assert attribute_value(editor, "highlights") == X_EQUALS_ONE

    # A programmatic assignment re-highlights now and cancels the pending pass.
    editor._impl.simulate_change()
    editor.value = "x = 2"
    assert editor._pending_rehighlight is None


async def test_rehighlight_failure_is_reported(app):
    """A failure in the debounced re-highlight reaches the loop's exception handler."""

    class BrokenHighlighter:
        def highlight(self, text):
            raise RuntimeError("lexer failed")

    reported = []
    asyncio.get_running_loop().set_exception_handler(
        lambda loop, context: reported.append(context)
    )
    editor = CodeEditor()
    editor._highlighter = BrokenHighlighter()

    editor._impl.simulate_change()
    # Hold the task, so garbage collection cannot be what reports the failure.
    task = editor._pending_rehighlight
    await asyncio.sleep(REHIGHLIGHT_DELAY * 2)
    assert [type(context["exception"]) for context in reported] == [RuntimeError]
    assert reported[0]["message"] == "Re-highlighting the code editor failed"
    assert reported[0]["task"] is task
