import pytest

from toga_code_editor.highlighting import (
    DEFAULT_THEME,
    NullHighlighter,
    PygmentsHighlighter,
    Span,
    Style,
    TokenKind,
    language_for_filename,
    merge_spans,
    to_utf16_spans,
    utf16_line_starts,
)


def test_python_snippet():
    """A Python snippet lexes into merged, offset-correct spans; names are dropped."""
    spans = PygmentsHighlighter("python").highlight(
        "def f(x):\n    return x + 1 and 2  # hi\n"
    )
    assert spans == [
        Span(0, 3, TokenKind.KEYWORD),
        Span(4, 5, TokenKind.DEFINITION),
        Span(5, 6, TokenKind.PUNCTUATION),
        Span(7, 9, TokenKind.PUNCTUATION),  # ")" and ":" merged into one span
        Span(14, 20, TokenKind.KEYWORD),
        Span(23, 24, TokenKind.OPERATOR),
        Span(25, 26, TokenKind.NUMBER),
        Span(27, 30, TokenKind.KEYWORD),  # "and" is Operator.Word, not an operator
        Span(31, 32, TokenKind.NUMBER),
        Span(34, 38, TokenKind.COMMENT),
    ]


def test_merge_spans_drops_empty_spans():
    """A zero-length span is dropped, and its neighbors merge across it."""
    spans = [
        Span(0, 1, TokenKind.NUMBER),
        Span(1, 1, TokenKind.KEYWORD),
        Span(1, 2, TokenKind.NUMBER),
    ]
    assert merge_spans(spans) == [Span(0, 2, TokenKind.NUMBER)]


def test_default_theme_is_read_only():
    """One editor cannot change the default theme under another."""
    with pytest.raises(TypeError):
        DEFAULT_THEME[TokenKind.KEYWORD] = Style("red")


def test_unknown_language():
    with pytest.raises(ValueError, match="nope"):
        PygmentsHighlighter("nope")


def test_null_highlighter():
    assert NullHighlighter().highlight("def f(): pass") == []


def test_utf16_offsets():
    """An astral character shifts every later UTF-16 offset by one."""
    text = "x = '\U0001f600'  # c\n"
    spans = [Span(4, 7, TokenKind.STRING), Span(9, 12, TokenKind.COMMENT)]
    assert to_utf16_spans(text, spans) == [
        Span(4, 8, TokenKind.STRING),
        Span(10, 13, TokenKind.COMMENT),
    ]
    assert utf16_line_starts("") == [0]
    assert utf16_line_starts("a\n") == [0, 2]
    assert utf16_line_starts("\U0001f600\r\nb") == [0, 4]


class FSPath:
    """A PathLike whose str() is not its path; only os.fspath() sees "foo.py"."""

    def __fspath__(self):
        return "foo.py"


@pytest.mark.parametrize(
    "name, expected",
    [
        ("foo.py", "python"),
        ("Makefile", "make"),
        ("notes.xyz", None),
        (FSPath(), "python"),
    ],
)
def test_language_for_filename(name, expected):
    assert language_for_filename(name) == expected
