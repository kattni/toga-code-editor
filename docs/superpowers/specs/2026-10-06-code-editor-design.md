# CodeEditor widget design

Date: 2026-10-06. Status: approved in conversation, awaiting written review.

## Goal

Build `CodeEditor`, a Toga widget for a code-editor app. It is a plain multi-line text input with line numbers and live syntax highlighting. Python is the first language, but the design supports every language Pygments lexes. The first backend is Cocoa for day-to-day testing, followed by iOS and Android.

## Decisions already made

- **Standalone package, not toga-core.** Toga's `Factory` loads external widgets from `<interface>.backend.<backend>` entry points, and the widget overrides its `factory` property to use them. toga_bitmap_view and Toga's `examples/customwidget` both use this mechanism, including on iOS and Android under Briefcase. A standalone package supports three backends without the parity, deprecation, and dependency obligations that Toga's constitution attaches to core widgets.
- **The interface layer tokenizes; backends paint.** Core produces a list of styled spans and backends apply them to native attributed text. Backends never see a language name. Cocoa, iOS, and Android ship no native source highlighter, so there is no native alternative to lean on.
- **Pygments is the tokenizer.** It is pure Python, BSD licensed, lexes several hundred languages, and runs under Briefcase on mobile. The package depends on it directly.
- **Subclass the existing `MultilineTextInput` implementations.** Each backend module subclasses its backend's multiline text implementation and adds highlighting and a gutter. This is the pattern Toga's extending-toga guide shows for a Qt dial built on the Slider implementation.

## Scope

In this cut: editable text, live highlighting, a line-number gutter, a language property, a theme, and a filename-to-language helper.

Out of this cut: code folding, completion, search and replace, minimap, a wrap on/off switch, cursor and selection position, indentation settings, go-to-line, and automatic light/dark theme switching. Any of these is a separate design.

## Package identity and layout

Distribution `toga-code-editor`, import package `toga_code_editor`, entry-point interface group `toga_code_editor`, widget class `CodeEditor`. The `toga_` prefix marks an official BeeWare external widget. Toga warns about any `toga_` interface it has not registered in its official set, so the widget suppresses that one warning until the Toga release that registers `toga_code_editor`.

```text
toga-code-editor/
  pyproject.toml                 # setuptools; one entry-point table per backend
  src/toga_code_editor/
    __init__.py                  # exports CodeEditor, language_for_filename, TokenKind, Style, DEFAULT_THEME, __version__
    code_editor.py               # interface: CodeEditor(toga.MultilineTextInput)
    highlighting.py              # TokenKind, Span, Style, Theme, Highlighter, PygmentsHighlighter, utf16 helper
    dummy_code_editor.py         # toga_dummy implementation, for the test suite
    cocoa_code_editor.py
    iOS_code_editor.py
    android_code_editor.py
  tests/
  examples/editor/               # Briefcase app used to verify real backends
  changes/                       # towncrier fragments
  README.md, LICENSE, CHANGELOG.md
```

Runtime dependencies: `toga-core >= 0.5.7, < 0.6` and `pygments`. The entry-point factory shipped in Toga 0.5.4, but the pin is 0.5.7 because the package subclasses internal backend classes and supports only the versions it has been tested against. Raise the floor deliberately when a new Toga release lands. No Toga backend is a dependency of the package; the app declares its platform backend as usual.

Entry points, one table per backend:

```toml
[project.entry-points."toga_code_editor.backend.toga_cocoa"]
CodeEditor = "toga_code_editor.cocoa_code_editor:CodeEditor"

[project.entry-points."toga_code_editor.backend.toga_iOS"]
CodeEditor = "toga_code_editor.iOS_code_editor:CodeEditor"

[project.entry-points."toga_code_editor.backend.toga_android"]
CodeEditor = "toga_code_editor.android_code_editor:CodeEditor"

[project.entry-points."toga_code_editor.backend.toga_dummy"]
CodeEditor = "toga_code_editor.dummy_code_editor:CodeEditor"
```

A backend with no table gets the factory's standard `NotImplementedError`, which names the backend.

Tooling: ruff for lint and format, pre-commit, tox with coverage, towncrier fragments in `changes/`, and a GitHub Actions workflow for CI. Python support matches toga-core 0.5.7. License BSD-3-Clause.

## Public API

`CodeEditor` subclasses `toga.MultilineTextInput` and inherits `value`, `readonly`, `placeholder`, `on_change`, `scroll_to_top`, `scroll_to_bottom`, and the `Widget` surface. It adds three properties and the package adds one function.

```python
import toga
from toga_code_editor import CodeEditor, language_for_filename

editor = CodeEditor(
    value=source,
    language="python",  # Pygments lexer alias; None means no highlighting
    show_line_numbers=True,
    theme=None,  # None means DEFAULT_THEME
    readonly=False,
    on_change=handle_edit,
)

editor.language = language_for_filename(path)  # "python" for foo.py, None if unknown
```

**`language: str | None`.** A Pygments lexer alias. Setting it builds a new highlighter and re-highlights synchronously. An unknown alias raises `ValueError` naming the alias, before any state changes. `None` disables highlighting. The default is `None`; the widget never guesses a language.

**`show_line_numbers: bool`.** Shows or hides the gutter. Default `True`.

**`theme: Theme`.** A mapping from `TokenKind` to `Style`. `None` selects `DEFAULT_THEME`. Setting it re-highlights synchronously.

**`language_for_filename(path) -> str | None`.** Returns the canonical alias of the Pygments lexer registered for that filename, or `None`. It matches the whole filename, so `Makefile` and `Dockerfile` resolve. No content-based guessing.

Fixed behaviors with no knob:

- If neither the `style` argument nor the keyword arguments set `font_family`, the constructor passes `font_family=MONOSPACE`.
- Autocorrect, smart quotes, smart dashes, auto-capitalization, and spell checking are always off.
- Programmatic `value` assignment re-highlights synchronously. Native edits re-highlight after a debounce.
- Soft wrap follows `MultilineTextInput` on each platform. Line numbers count logical lines; wrapped continuation lines get no number.

## Highlighting pipeline

Everything here lives in `highlighting.py` and runs in the interface layer.

### Types

```python
class TokenKind(StrEnum):
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
    start: int  # Python str index, inclusive
    end: int  # Python str index, exclusive
    kind: TokenKind


@dataclass(frozen=True)
class Style:
    color: Color  # accepts anything Toga color properties accept; normalized on construction
    bold: bool = False
    italic: bool = False


Theme = Mapping[TokenKind, Style]  # kinds absent from the theme are left unstyled
```

### Pygments token mapping

An ordered table maps Pygments' token tree onto the flat vocabulary. Lookup walks the table in order and uses Pygments' subsumption test, so the first matching row wins and specific rows come before general ones.

| Pygments type | TokenKind |
| --- | --- |
| `Comment` | `COMMENT` |
| `String` | `STRING` |
| `Number` | `NUMBER` |
| `Keyword` | `KEYWORD` |
| `Operator.Word` | `KEYWORD` |
| `Operator` | `OPERATOR` |
| `Punctuation` | `PUNCTUATION` |
| `Name.Builtin` | `BUILTIN` |
| `Name.Function`, `Name.Class` | `DEFINITION` |
| `Name.Decorator` | `DECORATOR` |
| `Name.Tag` | `TAG` |
| `Name.Attribute` | `ATTRIBUTE` |
| `Name.Variable` | `VARIABLE` |
| anything else, including `Error`, `Text`, and bare `Name` | `TEXT` |

`TEXT` spans are never emitted. Adjacent spans of the same kind are merged.

### Default theme

Mid-saturation colors that stay legible on both light and dark backgrounds, since the native text views follow the system appearance and Toga has no appearance-detection API. Adjust a value during implementation if it proves illegible on one appearance.

| TokenKind | Color | Bold | Italic |
| --- | --- | --- | --- |
| `KEYWORD` | `#a626a4` | | |
| `BUILTIN` | `#0184bc` | | |
| `DEFINITION` | `#4078f2` | yes | |
| `DECORATOR` | `#986801` | | |
| `STRING` | `#50a14f` | | |
| `NUMBER` | `#986801` | | |
| `COMMENT` | `#8a8a8a` | | yes |
| `TAG` | `#e45649` | | |
| `ATTRIBUTE` | `#986801` | | |
| `VARIABLE` | `#e45649` | | |

`OPERATOR` and `PUNCTUATION` are unstyled in the default theme.

### Highlighter protocol

```python
class Highlighter(Protocol):
    def highlight(self, text: str) -> list[Span]: ...
```

`PygmentsHighlighter(language)` builds its lexer with `stripnl=False`, `stripall=False`, and `ensurenl=False`, because the defaults trim and append newlines and shift every offset. It iterates `get_tokens_unprocessed`, the only Pygments API that yields offsets, maps each token through the table, drops `TEXT`, and merges neighbors. `NullHighlighter.highlight` returns an empty list and serves `language=None`. Tests use a hand-written fake.

### Offsets

Spans carry Python code-point indices. NSString, UITextView, and Android's `Spannable` index by UTF-16 code units, so one emoji shifts every later offset by one. `to_utf16_spans(text, spans)` converts a span list in a single pass over the text. All three real backends call it. The dummy backend does not, so the core tests assert what the interface emits.

### Flow

The interface calls two backend methods: `set_theme(theme)` when the theme changes, so the backend can build its native attribute set once per kind, and `set_highlights(spans)` to paint.

- Programmatic `value`, `language`, or `theme` assignment re-highlights synchronously and cancels any pending debounce.
- A native edit reaches the backend's existing change callback, which calls the interface hook `_schedule_rehighlight()` and then the user's `on_change()`. The hook cancels any pending `call_later` handle on the running asyncio loop and schedules a new one after `REHIGHLIGHT_DELAY`, a module constant of 150 milliseconds. The callback re-lexes the whole buffer.

Whole-buffer re-lexing is the right first version. Pygments cannot resume mid-file, and a few thousand lines re-lex well within the delay on desktop. Partial or off-thread re-lexing is a future option with a known seam.

### Edge cases

- Empty text yields no spans.
- Setting an invalid `language` raises before any state changes.
- Applying attributes does not fire the text-change callback on any of the three platforms, so painting cannot trigger another re-lex.
- On Android, programmatic `setText` does fire the text watcher. The resulting debounce is cancelled by the synchronous re-highlight that follows, so the buffer is lexed once.

## Backends

### Shared contract

Each backend module subclasses its backend's `MultilineTextInput` implementation, so value, readonly, placeholder, scrolling, and layout are inherited. On top it implements `set_theme`, `set_highlights`, and `set_show_line_numbers`, calls `interface._schedule_rehighlight()` from its native change callback before `interface.on_change()`, and forces autocorrect and smart punctuation off at creation. Cocoa and iOS keep the last span list and re-apply it after `set_font` and `set_color`, because Toga's implementations of those repaint the whole text storage. Android's spans live in the `Editable` and survive both, so it keeps only the native span objects it must remove.

### Cocoa

Subclass `toga_cocoa.widgets.multilinetextinput.MultilineTextInput`. Define a subclass of Toga's `TogaTextView` whose `textDidChange_` calls the hook, calls `on_change`, and marks the gutter for redraw. Beyond the quote and dash substitution Toga already disables, turn off continuous spell checking, automatic spelling correction, automatic text replacement, and grammar checking.

Highlights go through `native_text.textStorage` between `beginEditing` and `endEditing`: remove the foreground color attribute over the whole range, then add color and a trait font per span. Bold and italic fonts derive from the current base font through `NSFontManager` and are cached per token kind, rebuilt when the theme or font changes. Toga already sets `usesAdaptiveColorMappingForDarkAppearance`, so theme colors are remapped in dark mode.

The gutter is an `NSRulerView` subclass bound with `ObjCClass("NSRulerView")`, since toga_cocoa does not bind it. Install it as the scroll view's `verticalRulerView`, set `hasVerticalRuler`, and drive `rulersVisible` from `show_line_numbers`. Override `drawHashMarksAndLabelsInRect:`: take the visible rect, ask `layoutManager` for the glyph range in it, walk line fragments with `lineFragmentRectForGlyphAtIndex:effectiveRange:`, convert each fragment's first glyph to a character index, and look up its logical line number by bisecting a line-start table rebuilt on text change. Draw numbers right-aligned with the secondary label color in the editor's font. Set `ruleThickness` from the digit count of the line total. Accessing `layoutManager` opts the view into TextKit 1, which is the proven path for gutters. TextKit 2 is a later option.

### iOS

Subclass `toga_iOS.widgets.multilinetextinput.MultilineTextInput`. Define a subclass of Toga's `TogaMultilineTextView` whose `textViewDidChange_` calls the hook and `on_change` and then invalidates drawing. Set `autocorrectionType`, `autocapitalizationType`, `smartQuotesType`, `smartDashesType`, `smartInsertDeleteType`, and `spellCheckingType` to their "no" values.

Highlights go through `native.textStorage` exactly as on Cocoa, with bold and italic fonts derived through `UIFontDescriptor` symbolic traits and cached per kind.

There is no ruler on iOS. Reserve the gutter with `textContainerInset.left` and draw numbers in `drawRect:`. A `UITextView` is a scroll view, so that drawing is in content coordinates and scrolls with the text. Set `contentMode` to redraw so scrolling repaints. Enumerate line fragments as on Cocoa, offset by the container inset. Shift the existing placeholder label's leading constraint by the gutter width. Hiding the gutter restores UIKit's default inset and stops drawing. Custom drawing is justified by the absence of a native gutter on iOS.

### Android

Subclass `toga_android.widgets.multilinetextinput.MultilineTextInput`. Chaquopy implements Java interfaces from Python through `dynamic_proxy` but cannot subclass Java classes at runtime, so there is no `onDraw` to override. The `EditText` stays `self.native`, which keeps every inherited method working.

The gutter is a `TextView` added to `native_toplevel`, the `RelativeLayout` that `ContainedWidget` already creates. Give it a generated view id, align it to the parent's left edge, and replace the edit text's layout params with ones that add a `RIGHT_OF` rule pointing at the gutter. Its text is a column of numbers rebuilt from the edit text's `Layout`: for each visual line, the logical line number if `getLineStart` is zero or follows a newline, else a blank. It shares typeface, size, padding, and line spacing with the editor through the backend's `set_textview_font` helper so rows align, is right-aligned, and uses the secondary text color. Rebuild it from the change callback by posting a `Runnable` through `View.post`, so it runs after the pending layout pass, and on `set_show_line_numbers`, which toggles its visibility between `VISIBLE` and `GONE`. A `View.OnScrollChangeListener` on the edit text, the pattern `ScrollContainer` already uses, copies the vertical scroll offset onto the gutter.

Highlights are `ForegroundColorSpan` and `StyleSpan` objects applied to the `Editable` with `SPAN_EXCLUSIVE_EXCLUSIVE`, kept in a list so the previous set can be removed before the next is applied. Span changes do not notify the text watcher.

The input type always carries `TYPE_TEXT_FLAG_NO_SUGGESTIONS`, and `set_readonly` is overridden so toggling readonly keeps that flag. Some third-party keyboards ignore it. That is a known rough edge, not something to design around now.

### Dummy

Subclass `toga_dummy.widgets.multilinetextinput.MultilineTextInput`. Record theme, highlights, and the gutter flag with `_set_value`, so tests read them back with `attribute_value`. Override `simulate_change()` to call the hook before `on_change`, matching the real backends. No UTF-16 conversion.

### Toga internals this package depends on

A Toga release can change any of these, which is why the version pin is deliberate.

- Cocoa: `MultilineTextInput.native_text`, `TogaTextView`.
- iOS: `TogaMultilineTextView`, `MultilineTextInput.placeholder_label`, the placeholder constraint layout.
- Android: `TextInput._on_change`, `ContainedWidget.native_toplevel`, `set_textview_font`, `cache_textview_defaults`.
- Dummy: `MultilineTextInput.simulate_change`.

## Testing, verification, and the example app

Core tests run against toga_dummy. One test file for `highlighting.py` checks that a short Python snippet produces the expected spans, that UTF-16 conversion handles a non-BMP character, and that `language_for_filename` returns an alias or `None`. One test file for the widget checks that each property reaches the backend, that a bad `language` raises, that setting `value` re-highlights, and that a simulated native change fires `on_change` and schedules one re-lex. That covers the interface, highlighting, and dummy modules fully; the three platform modules are excluded from coverage because they cannot import on CI.

Real backends are checked by running the example app on each platform and typing in it. No checklist document and no probe harness.

The example app at `examples/editor` is a Briefcase app that bundles a few sample files and offers a `Selection` to pick one, a `Selection` for language, and a `Switch` for line numbers. It depends on the package by relative path, as Toga's `customwidget` example does.

CI is one Ubuntu job that runs pre-commit, then pytest with coverage against `toga_dummy`.

Docs are the README and docstrings. Changelog fragments go in `changes/` for towncrier.

## Known limitations

- Theme colors do not switch with system appearance on iOS or Android. macOS remaps them through adaptive color mapping. The default palette is chosen to be legible on both.
- Whole-buffer re-lexing on every debounce. Fine for a few thousand lines on desktop; slower on phones.
- Cocoa and iOS run the text view under TextKit 1.
- The package subclasses internal Toga backend classes and supports a pinned Toga version range.
