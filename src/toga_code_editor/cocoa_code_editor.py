from bisect import bisect_right

from rubicon.objc import (
    SEL,
    NSPoint,
    NSRange,
    NSRect,
    ObjCClass,
    objc_method,
    objc_property,
)
from toga_cocoa.colors import native_color
from toga_cocoa.libs import (
    NSAttributedString,
    NSBezelBorder,
    NSColor,
    NSFontAttributeName,
    NSFontManager,
    NSForegroundColorAttributeName,
    NSMutableDictionary,
    NSNotificationCenter,
    NSScrollView,
    NSViewBoundsDidChangeNotification,
    NSViewHeightSizable,
    NSViewWidthSizable,
)
from toga_cocoa.widgets.multilinetextinput import MultilineTextInput, TogaTextView

from .highlighting import to_utf16_spans, utf16_line_starts

NSRulerView = ObjCClass("NSRulerView")

# NSRulerOrientation
NSVerticalRuler = 1
# NSFontTraitMask
NSItalicFontMask = 1 << 0
NSBoldFontMask = 1 << 1

GUTTER_PADDING = 6


class TogaCodeTextView(TogaTextView):
    @objc_method
    def textDidChange_(self, notification) -> None:
        self.interface._schedule_rehighlight()
        self.interface.on_change()
        self.impl.text_changed()

    @objc_method
    def paste_(self, sender) -> None:
        # Rich text from another app would keep attributes that the re-highlight
        # never resets, so paste the plain text only.
        self.pasteAsPlainText(sender)


class TogaLineNumberView(NSRulerView):
    impl = objc_property(object, weak=True)

    @objc_method
    def boundsDidChange_(self, notification) -> None:
        # The text scrolled; the visible line numbers changed with it.
        self.setNeedsDisplay(True)

    @objc_method
    def drawHashMarksAndLabelsInRect_(self, rect: NSRect) -> None:
        self.impl.draw_line_numbers()


class CodeEditor(MultilineTextInput):
    def create(self):
        # Mirrors toga_cocoa's MultilineTextInput.create(), swapping in
        # TogaCodeTextView, turning off prose features, and adding a ruler.
        self.native = NSScrollView.alloc().init()
        self.native.hasVerticalScroller = True
        self.native.hasHorizontalScroller = False
        self.native.autohidesScrollers = False
        self.native.borderType = NSBezelBorder

        self.native_text = TogaCodeTextView.alloc().init()
        self.native_text.interface = self.interface
        self.native_text.impl = self
        self.native_text.delegate = self.native_text

        self.native_text.editable = True
        self.native_text.selectable = True
        self.native_text.allowsUndo = True
        self.native_text.verticallyResizable = True
        self.native_text.horizontallyResizable = False
        self.native_text.usesAdaptiveColorMappingForDarkAppearance = True
        self.native_text.setAutomaticQuoteSubstitutionEnabled(False)
        self.native_text.setAutomaticDashSubstitutionEnabled(False)
        # Code is not prose.
        self.native_text.setContinuousSpellCheckingEnabled(False)
        self.native_text.setGrammarCheckingEnabled(False)
        self.native_text.setAutomaticSpellingCorrectionEnabled(False)
        self.native_text.setAutomaticTextReplacementEnabled(False)
        self.native_text.setAutomaticTextCompletionEnabled(False)
        # Smart insert and delete add and remove spaces around words.
        self.native_text.setSmartInsertDeleteEnabled(False)

        self.native_text.autoresizingMask = NSViewWidthSizable | NSViewHeightSizable
        self.native.documentView = self.native_text

        # Reading layoutManager opts the view into TextKit 1, which is what the
        # gutter's line-fragment queries need. Do it once, up front.
        self.layout_manager = self.native_text.layoutManager

        self.ruler = TogaLineNumberView.alloc().initWithScrollView(
            self.native, orientation=NSVerticalRuler
        )
        self.ruler.impl = self
        self.ruler.clientView = self.native_text
        self.ruler.reservedThicknessForMarkers = 0
        self.ruler.reservedThicknessForAccessoryView = 0
        # Since macOS 14 views no longer clip by default, and AppKit hands the ruler
        # a dirty rect that reaches the window's top edge, so its rule line would be
        # drawn above the scroll view. Earlier systems always clip.
        if self.ruler.respondsToSelector_(SEL("setClipsToBounds:")):
            self.ruler.clipsToBounds = True
        self.native.hasVerticalRuler = True
        self.native.verticalRulerView = self.ruler
        self.native.rulersVisible = True

        # Redraw the gutter whenever the clip view scrolls.
        self.native.contentView.postsBoundsChangedNotifications = True
        NSNotificationCenter.defaultCenter.addObserver(
            self.ruler,
            selector=SEL("boundsDidChange:"),
            name=NSViewBoundsDidChangeNotification,
            object=self.native.contentView,
        )

        # NSTextView.font and .textColor read back the *first character's* attributes
        # once the storage is styled, so the base style is tracked here instead.
        self.base_font = self.native_text.font
        self.base_color = NSColor.textColor
        self.rebuild_gutter_attributes()

        self.theme = {}
        self.attributes = {}
        self.spans = []
        self.line_starts = [0]

        self.add_constraints()

    # Inherited MultilineTextInput methods that must keep the gutter and colors in sync

    def set_value(self, value):
        super().set_value(value)
        self.text_changed()

    def set_font(self, font):
        super().set_font(font)
        self.base_font = font._impl.native
        self.rebuild_attributes()
        self.rebuild_gutter_attributes()
        self.apply_highlights()
        self.text_changed()

    def set_color(self, value):
        super().set_color(value)
        self.base_color = native_color(value) or NSColor.textColor
        self.apply_highlights()

    # CodeEditor backend contract

    def set_theme(self, theme):
        self.theme = theme
        self.rebuild_attributes()

    def set_highlights(self, spans):
        self.spans = spans
        self.apply_highlights()

    def set_show_line_numbers(self, value):
        self.native.rulersVisible = value

    # Highlighting

    def rebuild_attributes(self):
        base_font = self.base_font
        manager = NSFontManager.sharedFontManager
        self.attributes = {}
        for kind, style in self.theme.items():
            attributes = NSMutableDictionary.alloc().init()
            attributes[NSForegroundColorAttributeName] = native_color(style.color)
            traits = 0
            if style.bold:
                traits |= NSBoldFontMask
            if style.italic:
                traits |= NSItalicFontMask
            if traits:
                attributes[NSFontAttributeName] = manager.convertFont(
                    base_font, toHaveTrait=traits
                )
            self.attributes[kind] = attributes

    def apply_highlights(self):
        storage = self.native_text.textStorage
        length = storage.length()
        full_range = NSRange(0, length)
        # Between a native edit and the debounced re-highlight the spans describe the
        # previous text, so clamp them: a range past the end raises NSRangeException
        # inside AppKit, which cannot be caught from Python. Convert before editing
        # so a Python error cannot leave the storage mid-edit either.
        spans = [
            (span.start, min(span.end, length), self.attributes.get(span.kind))
            for span in to_utf16_spans(self.get_value(), self.spans)
            if span.start < length
        ]
        storage.beginEditing()
        # Reset to the base font and color, then paint each span.
        storage.addAttribute(
            NSFontAttributeName, value=self.base_font, range=full_range
        )
        storage.addAttribute(
            NSForegroundColorAttributeName, value=self.base_color, range=full_range
        )
        for start, end, attributes in spans:
            if attributes is not None and end > start:
                storage.addAttributes(attributes, range=NSRange(start, end - start))
        storage.endEditing()

    # Gutter

    def text_changed(self):
        self.line_starts = utf16_line_starts(self.get_value())
        digits = len(str(len(self.line_starts)))
        label = self.gutter_label("0" * digits)
        thickness = label.size().width + 2 * GUTTER_PADDING
        if self.ruler.ruleThickness != thickness:
            self.ruler.ruleThickness = thickness
        self.ruler.setNeedsDisplay(True)

    def rebuild_gutter_attributes(self):
        # Built once per font, rather than once per visible line per draw.
        attributes = NSMutableDictionary.alloc().init()
        attributes[NSFontAttributeName] = self.base_font
        attributes[NSForegroundColorAttributeName] = NSColor.secondaryLabelColor
        self.gutter_attributes = attributes

    def gutter_label(self, text):
        return NSAttributedString.alloc().initWithString(
            text, attributes=self.gutter_attributes
        )

    def draw_line_numbers(self):
        layout = self.layout_manager
        container = self.native_text.textContainer
        visible = self.native_text.visibleRect
        inset = self.native_text.textContainerInset
        text_length = self.native_text.textStorage.length()

        # The layout manager works in container coordinates, which sit inset.width
        # and inset.height inside the view's own.
        container_rect = NSRect(
            NSPoint(visible.origin.x - inset.width, visible.origin.y - inset.height),
            visible.size,
        )
        glyph_range = layout.glyphRangeForBoundingRect(
            container_rect, inTextContainer=container
        )
        char_range = layout.characterRangeForGlyphRange(
            glyph_range, actualGlyphRange=None
        )
        first_line = max(bisect_right(self.line_starts, char_range.location) - 1, 0)
        last_char = char_range.location + char_range.length
        thickness = self.ruler.ruleThickness

        for number, start in enumerate(
            self.line_starts[first_line:], start=first_line + 1
        ):
            if start > last_char:
                break
            if start == text_length:
                # The empty line after a trailing newline, or an empty document.
                fragment = layout.extraLineFragmentRect
            else:
                glyph = layout.glyphIndexForCharacterAtIndex(start)
                fragment = layout.lineFragmentRectForGlyphAtIndex(
                    glyph, effectiveRange=None
                )
            label = self.gutter_label(str(number))
            x = thickness - GUTTER_PADDING - label.size().width
            y = fragment.origin.y + inset.height - visible.origin.y
            label.drawAtPoint(NSPoint(x, y))
