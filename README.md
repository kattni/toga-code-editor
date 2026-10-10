# toga-code-editor

A [Toga](https://toga.beeware.org) widget for editing code, with line numbers and syntax highlighting. `CodeEditor` is a `toga.MultilineTextInput` that colors its text with [Pygments](https://pygments.org) and shows a line-number gutter. It supports macOS, iOS, and Android.

## Installation

```console
pip install toga-code-editor
```

The package depends on `pygments` and on `toga-core` 0.5.7 or later in the 0.5 series, because it subclasses Toga backend internals that can change between minor versions. Your app installs the Toga backend for its platform as usual; `toga-code-editor` contributes its implementation for that backend through entry points, the same way Toga's own widgets are found.

## Usage

```python
from toga_code_editor import CodeEditor, language_for_filename

editor = CodeEditor(
    value=source,
    language="python",  # any Pygments lexer alias; None turns highlighting off
    show_line_numbers=True,
    on_change=handle_edit,
    flex=1,
)

# Pick the language from a file name. Unknown files get None, which means no highlighting.
editor.language = language_for_filename(path)
```

`CodeEditor` inherits everything from `toga.MultilineTextInput`, including `value`, `readonly`, `placeholder`, `on_change`, `scroll_to_top`, and `scroll_to_bottom`. It adds three properties:

- `language`: a Pygments lexer alias such as `"python"`, `"rust"`, or `"json"`. Setting an unknown alias raises `ValueError`. `None` disables highlighting. The default is `None`.
- `show_line_numbers`: shows or hides the gutter. Default `True`.
- `theme`: a mapping from `TokenKind` to `Style`. `None` selects the built-in `DEFAULT_THEME`.

Unless you give the widget a font family, it uses a monospace font. Autocorrect, smart quotes, smart dashes, smart insert and delete, auto-capitalization, and spell checking are always off.

### Themes

A theme is a plain mapping. Kinds you leave out are drawn in the widget's normal text color. `DEFAULT_THEME` is read-only, so unpack it into a new dict to change it, as below.

```python
from toga_code_editor import DEFAULT_THEME, Style, TokenKind

theme = {
    **DEFAULT_THEME,
    TokenKind.COMMENT: Style("#6a737d", italic=True),
    TokenKind.KEYWORD: Style("rebeccapurple", bold=True),
}
editor.theme = theme
```

`Style.color` accepts anything Toga's color properties accept. The token kinds are `KEYWORD`, `BUILTIN`, `DEFINITION`, `DECORATOR`, `STRING`, `NUMBER`, `COMMENT`, `OPERATOR`, `PUNCTUATION`, `TAG`, `ATTRIBUTE`, and `VARIABLE`. Everything else, including plain names, is `TEXT`, which is never styled.

## Platform notes

- **macOS** uses an `NSRulerView` for the gutter and the system's adaptive color mapping, so theme colors follow dark mode.
- **Android** 12 and later are supported. The example app is checked on Android 12 and Android 15 emulators.
- **iOS** and **Android** draw the gutter themselves. Theme colors do not change with the system appearance; the default theme is chosen to be legible on both light and dark backgrounds.
- Highlighting re-lexes the whole buffer after a short pause in typing. Files of a few thousand lines are fine on a desktop; very large files are slower on phones.
- Some third-party Android keyboards ignore the flag that disables suggestions.
- On Android, the widget asks the window to shrink its content when the soft keyboard appears, so the editor scrolls internally and the rest of the layout stays put. It only does this when the app has not chosen a soft-input mode itself. Android deprecated that request in API 30 in favour of handling window insets, so a future release may stop honouring it. With Briefcase's current template, which targets SDK 36 and enables edge-to-edge drawing, the layout still shrinks for the keyboard on Android 12 and Android 15 emulators.

## Developing

```console
uv venv
uv pip install -e . --group dev
.venv/bin/tox -m test
```

The test suite runs against Toga's dummy backend. Real backends are checked with the example app in `examples/editor`:

```console
cd examples/editor
briefcase dev            # macOS
briefcase run iOS
briefcase run android
```

On macOS and iOS the example installs the widget straight from this repository; add `-r` after changing the widget source so Briefcase reinstalls it. On Android the example installs the widget from a wheel in `dist/`, because Gradle rejects a path requirement whose directory contains the Android build tree. The example's Android requirement names that wheel file, version and all, so a version bump must update `examples/editor/pyproject.toml` too, and a stale wheel in `dist/` ships the old code without complaint. Build the wheel from the repository root before each Android run that should pick up widget changes:

```console
uv build --wheel
cd examples/editor
briefcase run android -r
```

Every user-visible change needs a fragment in `changes/`, named `<issue>.<kind>.md` where kind is `feature`, `bugfix`, `doc`, or `misc`. Release notes are assembled with `towncrier build`.

## Community

`toga-code-editor` is part of the [BeeWare suite](http://beeware.org). You can talk to the community through:

- [@beeware@fosstodon.org on Mastodon](https://fosstodon.org/@beeware)
- [Discord](https://beeware.org/bee/chat/)

We foster a welcoming and respectful community as described in our [BeeWare Community Code of Conduct](http://beeware.org/community/behavior/).

## Contributing

If you experience problems with the `CodeEditor` widget, [log them on GitHub](https://github.com/beeware/toga-code-editor/issues). If you want to contribute code, please [fork the code](https://github.com/beeware/toga-code-editor) and [submit a pull request](https://github.com/beeware/toga-code-editor/pulls).
