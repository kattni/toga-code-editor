from pathlib import Path

import toga
from toga.constants import COLUMN, ROW
from toga.platform import current_platform

from toga_code_editor import CodeEditor, language_for_filename

SAMPLES = Path(__file__).parent / "resources" / "samples"
MOBILE = current_platform in {"iOS", "android"}
NO_LANGUAGE = "none"
LANGUAGES = ["python", "json", "html", NO_LANGUAGE]


class Editor(toga.App):
    def startup(self):
        self.editor = CodeEditor(flex=1, on_change=self.on_edit)
        self.sample = toga.Selection(
            items=sorted(
                path.name
                for path in SAMPLES.iterdir()
                if path.is_file() and not path.name.startswith(".")
            ),
            on_change=self.load_sample,
        )
        self.language = toga.Selection(items=LANGUAGES, on_change=self.set_language)
        self.line_numbers = toga.Switch(
            "Line numbers", value=True, on_change=self.toggle_line_numbers
        )
        self.status = toga.Label("", flex=1)

        # Two rows on mobile, so the controls fit a phone's width.
        if MOBILE:
            rows = [[self.sample, self.language], [self.line_numbers, self.status]]
        else:
            rows = [[self.sample, self.language, self.line_numbers, self.status]]
        toolbar = toga.Box(
            children=[
                toga.Box(children=row, direction=ROW, align_items="center", gap=5)
                for row in rows
            ],
            direction=COLUMN,
            margin=5,
            gap=5,
        )
        self.main_window = toga.MainWindow()
        self.main_window.content = toga.Box(
            children=[toolbar, self.editor], direction=COLUMN
        )
        self.load_sample(self.sample)
        self.main_window.show()

    def load_sample(self, widget, **kwargs):
        path = SAMPLES / self.sample.value
        self.editor.value = path.read_text(encoding="utf-8")
        self.language.value = language_for_filename(path) or NO_LANGUAGE
        self.status.text = ""

    def set_language(self, widget, **kwargs):
        value = self.language.value
        self.editor.language = None if value == NO_LANGUAGE else value

    def toggle_line_numbers(self, widget, **kwargs):
        self.editor.show_line_numbers = self.line_numbers.value

    def on_edit(self, widget, **kwargs):
        self.status.text = "Modified"


def main():
    return Editor("Editor", "org.beeware.examples.editor")
