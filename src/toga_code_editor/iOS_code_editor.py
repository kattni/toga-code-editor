from bisect import bisect_right

from rubicon.objc import (
    CGPoint,
    CGRect,
    NSMutableDictionary,
    NSRange,
    objc_method,
    send_message,
    send_super,
)
from rubicon.objc.types import NSInteger, UIEdgeInsets
from toga_iOS.colors import native_color
from toga_iOS.libs import (
    NSAttributedString,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSLayoutAttributeLeading,
    UIColor,
    UIFont,
    UIFontDescriptorTraitBold,
    UIFontDescriptorTraitItalic,
    UILabel,
)
from toga_iOS.widgets.multilinetextinput import (
    MultilineTextInput,
    TogaMultilineTextView,
)

from .highlighting import to_utf16_spans, utf16_line_starts

UIColor.declare_class_property("labelColor")
UIColor.declare_class_property("secondaryLabelColor")

# UIViewContentMode
UIViewContentModeRedraw = 3
# UITextAutocapitalizationType / UITextAutocorrectionType / UITextSpellCheckingType /
# UITextSmartQuotesType / UITextSmartDashesType / UITextSmartInsertDeleteType
UITextAutocapitalizationTypeNone = 0
UITextAutocorrectionTypeNo = 1
UITextSpellCheckingTypeNo = 1
UITextSmartQuotesTypeNo = 1
UITextSmartDashesTypeNo = 1
UITextSmartInsertDeleteTypeNo = 1

GUTTER_PADDING = 6


class TogaCodeTextView(TogaMultilineTextView):
    @objc_method
    def textViewDidChange_(self, text_view):
        self.interface._schedule_rehighlight()
        self.interface.on_change()
        self.impl.text_changed()

    @objc_method
    def drawRect_(self, rect: CGRect) -> None:
        send_super(__class__, self, "drawRect:", rect, argtypes=[CGRect])
        self.impl.draw_line_numbers()


class CodeEditor(MultilineTextInput):
    def create(self):
        # Mirrors toga_iOS's MultilineTextInput.create(), swapping in
        # TogaCodeTextView and turning off prose features.
        self.native = TogaCodeTextView.alloc().init()
        self.native.interface = self.interface
        self.native.impl = self
        self.native.delegate = self.native
        self.native.contentMode = UIViewContentModeRedraw
        # UITextView forwards its UITextInputTraits setters to an internal object, so
        # rubicon cannot see them as properties and attribute assignment is silently
        # dropped. Send the messages directly.
        for selector, value in (
            ("setAutocapitalizationType:", UITextAutocapitalizationTypeNone),
            ("setAutocorrectionType:", UITextAutocorrectionTypeNo),
            ("setSpellCheckingType:", UITextSpellCheckingTypeNo),
            ("setSmartQuotesType:", UITextSmartQuotesTypeNo),
            ("setSmartDashesType:", UITextSmartDashesTypeNo),
            ("setSmartInsertDeleteType:", UITextSmartInsertDeleteTypeNo),
        ):
            send_message(
                self.native, selector, value, restype=None, argtypes=[NSInteger]
            )

        # Reading layoutManager opts the view into TextKit 1, which is what the
        # gutter's line-fragment queries need. Do it once, up front.
        self.layout_manager = self.native.layoutManager

        # Placeholder isn't natively supported, so we create our own
        self.placeholder_label = UILabel.alloc().init()
        self.placeholder_label.translatesAutoresizingMaskIntoConstraints = False
        self.placeholder_label.font = self.native.font
        self.placeholder_label.alpha = 0.5
        self.native.addSubview(self.placeholder_label)
        self.constrain_placeholder_label()
        self.native.placeholder_label = self.placeholder_label

        # UITextView.font and .textColor read back the *first character's* attributes
        # once the storage is styled, so the base style is tracked here instead. Unlike
        # an NSTextView, a UITextView created with no text reports no font, so every
        # use of base_font is guarded until set_font supplies one.
        self.base_font = self.native.font
        self.base_color = UIColor.labelColor
        self.rebuild_gutter_attributes()

        self.theme = {}
        self.attributes = {}
        self.spans = []
        self.line_starts = [0]
        self.show_line_numbers = True
        self.gutter_width = 0
        self.default_inset = self.native.textContainerInset

        self.add_constraints()
        self.text_changed()

    def constrain_placeholder_label(self):
        super().constrain_placeholder_label()
        # Keep a handle on Toga's leading constraint, and its original inset, so
        # the gutter can push the placeholder right.
        label = self.placeholder_label.ptr.value
        self.placeholder_leading = next(
            constraint
            for constraint in self.native.constraints()
            if constraint.firstItem.ptr.value == label
            and constraint.firstAttribute == NSLayoutAttributeLeading
        )
        self.placeholder_inset = self.placeholder_leading.constant

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
        self.base_color = native_color(value) or UIColor.labelColor
        self.apply_highlights()

    # CodeEditor backend contract

    def set_theme(self, theme):
        self.theme = theme
        self.rebuild_attributes()

    def set_highlights(self, spans):
        self.spans = spans
        self.apply_highlights()

    def set_show_line_numbers(self, value):
        self.show_line_numbers = value
        self.text_changed()

    # Highlighting

    def rebuild_attributes(self):
        base_font = self.base_font
        self.attributes = {}
        for kind, style in self.theme.items():
            attributes = NSMutableDictionary.alloc().init()
            attributes[NSForegroundColorAttributeName] = native_color(style.color)
            traits = 0
            if style.bold:
                traits |= UIFontDescriptorTraitBold
            if style.italic:
                traits |= UIFontDescriptorTraitItalic
            if traits and base_font is not None:
                # A font without a face for the requested traits yields no descriptor,
                # and a descriptor can still yield no font.
                descriptor = base_font.fontDescriptor.fontDescriptorWithSymbolicTraits(
                    traits
                )
                font = (
                    None
                    if descriptor is None
                    else UIFont.fontWithDescriptor(descriptor, size=base_font.pointSize)
                )
                if font is not None:
                    attributes[NSFontAttributeName] = font
            self.attributes[kind] = attributes

    def apply_highlights(self):
        storage = self.native.textStorage
        length = storage.length()
        full_range = NSRange(0, length)
        # Between a native edit and the debounced re-highlight the spans describe the
        # previous text, so clamp them: a range past the end raises NSRangeException
        # inside UIKit, which cannot be caught from Python.
        spans = [
            (span.start, min(span.end, length), self.attributes.get(span.kind))
            for span in to_utf16_spans(self.get_value(), self.spans)
            if span.start < length
        ]
        storage.beginEditing()
        # Reset to the base font and color, then paint each span.
        if self.base_font is not None:
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
        if self.show_line_numbers:
            digits = len(str(len(self.line_starts)))
            width = self.gutter_label("0" * digits).size().width + 2 * GUTTER_PADDING
        else:
            width = 0
        if width != self.gutter_width:
            self.gutter_width = width
            inset = self.default_inset
            self.native.textContainerInset = UIEdgeInsets(
                inset.top, inset.left + width, inset.bottom, inset.right
            )
            self.placeholder_leading.constant = self.placeholder_inset + width
        self.native.setNeedsDisplay()

    def rebuild_gutter_attributes(self):
        # Built once per font, rather than once per visible line per draw.
        attributes = NSMutableDictionary.alloc().init()
        if self.base_font is not None:
            attributes[NSFontAttributeName] = self.base_font
        attributes[NSForegroundColorAttributeName] = UIColor.secondaryLabelColor
        self.gutter_attributes = attributes

    def gutter_label(self, text):
        return NSAttributedString.alloc().initWithString(
            text, attributes=self.gutter_attributes
        )

    def draw_line_numbers(self):
        if not self.show_line_numbers:
            return
        layout = self.layout_manager
        container = self.native.textContainer
        inset = self.native.textContainerInset
        text_length = self.native.textStorage.length()

        # bounds.origin is the scroll offset; the container is inset within it.
        bounds = self.native.bounds
        visible = CGRect(
            CGPoint(bounds.origin.x - inset.left, bounds.origin.y - inset.top),
            bounds.size,
        )
        glyph_range = layout.glyphRangeForBoundingRect(
            visible, inTextContainer=container
        )
        char_range = layout.characterRangeForGlyphRange(
            glyph_range, actualGlyphRange=None
        )
        first_line = max(bisect_right(self.line_starts, char_range.location) - 1, 0)
        last_char = char_range.location + char_range.length

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
            x = self.gutter_width - GUTTER_PADDING - label.size().width
            y = fragment.origin.y + inset.top
            label.drawAtPoint(CGPoint(x, y))
