from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from os import PathLike, fspath
from typing import Protocol

from pygments.lexers import find_lexer_class_for_filename, get_lexer_by_name
from pygments.token import (
    Comment,
    Keyword,
    Name,
    Number,
    Operator,
    Punctuation,
    String,
)
from pygments.util import ClassNotFound
from toga.colors import Color


class TokenKind(StrEnum):
    """The flat vocabulary of token kinds a theme can style."""

    TEXT = "text"
    KEYWORD = "keyword"
    BUILTIN = "builtin"
    DEFINITION = "definition"
    DECORATOR = "decorator"
    STRING = "string"
    NUMBER = "number"
    COMMENT = "comment"
    OPERATOR = "operator"
    PUNCTUATION = "punctuation"
    TAG = "tag"
    ATTRIBUTE = "attribute"
    VARIABLE = "variable"


@dataclass(frozen=True)
class Span:
    """A run of text with one token kind.

    Offsets are Python string indices: ``start`` inclusive, ``end`` exclusive.
    """

    start: int
    end: int
    kind: TokenKind


@dataclass(frozen=True)
class Style:
    """How a token kind is drawn.

    ``color`` accepts any Toga color value, including a string, and is stored as a
    :class:`~toga.colors.Color`.
    """

    color: Color
    bold: bool = False
    italic: bool = False

    def __post_init__(self):
        object.__setattr__(self, "color", Color.parse(self.color))


Theme = Mapping[TokenKind, Style]
"""A mapping from token kind to style. Kinds absent from the theme are left unstyled."""

DEFAULT_THEME: Theme = {
    TokenKind.KEYWORD: Style("#a626a4"),
    TokenKind.BUILTIN: Style("#0184bc"),
    TokenKind.DEFINITION: Style("#4078f2", bold=True),
    TokenKind.DECORATOR: Style("#986801"),
    TokenKind.STRING: Style("#50a14f"),
    TokenKind.NUMBER: Style("#986801"),
    TokenKind.COMMENT: Style("#8a8a8a", italic=True),
    TokenKind.TAG: Style("#e45649"),
    TokenKind.ATTRIBUTE: Style("#986801"),
    TokenKind.VARIABLE: Style("#e45649"),
}

# Ordered most-specific first: the first row whose Pygments type contains the token
# wins, so Operator.Word must precede Operator. Anything unmatched, including bare
# Name, is TEXT.
_TOKEN_TABLE = [
    (Comment, TokenKind.COMMENT),
    (String, TokenKind.STRING),
    (Number, TokenKind.NUMBER),
    (Keyword, TokenKind.KEYWORD),
    (Operator.Word, TokenKind.KEYWORD),
    (Operator, TokenKind.OPERATOR),
    (Punctuation, TokenKind.PUNCTUATION),
    (Name.Builtin, TokenKind.BUILTIN),
    (Name.Function, TokenKind.DEFINITION),
    (Name.Class, TokenKind.DEFINITION),
    (Name.Decorator, TokenKind.DECORATOR),
    (Name.Tag, TokenKind.TAG),
    (Name.Attribute, TokenKind.ATTRIBUTE),
    (Name.Variable, TokenKind.VARIABLE),
]


def token_kind(token_type) -> TokenKind:
    """Map a Pygments token type onto the flat vocabulary."""
    for pygments_type, kind in _TOKEN_TABLE:
        if token_type in pygments_type:
            return kind
    return TokenKind.TEXT


def merge_spans(spans: Iterable[Span]) -> list[Span]:
    """Drop TEXT and empty spans, and merge adjacent spans of the same kind."""
    merged: list[Span] = []
    for span in spans:
        if span.kind is TokenKind.TEXT or span.end <= span.start:
            continue
        if merged and merged[-1].kind is span.kind and merged[-1].end == span.start:
            merged[-1] = Span(merged[-1].start, span.end, span.kind)
        else:
            merged.append(span)
    return merged


class Highlighter(Protocol):
    def highlight(self, text: str) -> list[Span]: ...


class NullHighlighter:
    """The highlighter used when no language is set."""

    def highlight(self, text: str) -> list[Span]:
        return []


class PygmentsHighlighter:
    """Tokenize text with a Pygments lexer.

    :param language: A Pygments lexer alias, such as ``"python"``.
    :raises ValueError: If Pygments has no lexer for the alias.
    """

    def __init__(self, language: str):
        try:
            # Pygments trims and appends newlines by default, which shifts every
            # offset. Turn all of that off.
            self.lexer = get_lexer_by_name(
                language, stripnl=False, stripall=False, ensurenl=False
            )
        except ClassNotFound:
            raise ValueError(f"Unknown language {language!r}") from None

    def highlight(self, text: str) -> list[Span]:
        return merge_spans(
            Span(index, index + len(value), token_kind(token_type))
            for index, token_type, value in self.lexer.get_tokens_unprocessed(text)
        )


def language_for_filename(path: str | PathLike) -> str | None:
    """Return the Pygments lexer alias for a filename, or ``None`` if there is none.

    The whole filename is matched, so names such as ``Makefile`` resolve.
    """
    lexer_class = find_lexer_class_for_filename(fspath(path))
    return None if lexer_class is None else lexer_class.aliases[0]


def to_utf16_spans(text: str, spans: list[Span]) -> list[Span]:
    """Convert span offsets from code points to UTF-16 code units.

    Native text views on macOS, iOS, and Android index by UTF-16 code unit, so every
    character outside the Basic Multilingual Plane shifts later offsets by one. Spans
    must be in document order, which is how the highlighters produce them.
    """
    converted = []
    scanned = 0  # code-point index already accounted for
    extra = 0  # extra UTF-16 units contributed by astral characters before `scanned`

    def utf16(index: int) -> int:
        nonlocal scanned, extra
        extra += sum(1 for ch in text[scanned:index] if ord(ch) > 0xFFFF)
        scanned = index
        return index + extra

    for span in spans:
        start = utf16(span.start)
        end = utf16(span.end)
        converted.append(Span(start, end, span.kind))
    return converted


def utf16_line_starts(text: str) -> list[int]:
    """Return the UTF-16 offset of the first character of each logical line."""
    starts = [0]
    offset = 0
    for line in text.split("\n")[:-1]:
        # An astral character is two UTF-16 units. "surrogatepass" keeps a lone
        # surrogate at one unit rather than raising.
        offset += len(line.encode("utf-16-le", "surrogatepass")) // 2 + 1
        starts.append(offset)
    return starts
