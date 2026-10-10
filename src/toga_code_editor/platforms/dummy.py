from toga_dummy.widgets.multilinetextinput import MultilineTextInput


class CodeEditor(MultilineTextInput):
    def create(self):
        self._action("create CodeEditor")

    def set_theme(self, theme):
        self._set_value("theme", theme)

    def set_highlights(self, spans):
        # Record the theme in force at paint time, as a real backend would use it.
        self._action("paint", theme=self._get_value("theme", None))
        self._set_value("highlights", spans)

    def set_show_line_numbers(self, value):
        self._set_value("show_line_numbers", value)

    def simulate_change(self):
        # Mirror the real backends: the re-highlight is scheduled before the
        # user's handler runs.
        self.interface._schedule_rehighlight()
        self.interface.on_change()
