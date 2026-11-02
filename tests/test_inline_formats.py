"""Org inline formats retain their markers and respect literal contexts."""

from editor_test_case import EditorTestCase
from vim import GtkSource, Pango


class InlineFormatTests(EditorTestCase):
    formats = {
        "bold": "*bold*",
        "italic": "/italic/",
        "underline": "_underline_",
        "strikethrough": "+strikethrough+",
        "verbatim": "=verbatim=",
        "code": "~code~",
    }

    def highlighted(self, text):
        tab = self.app.new_tab()
        tab.buffer.set_text(text)
        tab.buffer.ensure_highlight(*tab.buffer.get_bounds())
        return tab

    def test_formats_in_body_headings_lists_and_tables_preserve_text(self):
        for prefix in ("", "* Heading ", "** TODO Heading ", "* DONE Heading ", "- ", "| "):
            text = prefix + " ".join(self.formats.values()) + "\n"
            tab = self.highlighted(text)
            for name, markup in self.formats.items():
                with self.subTest(prefix=prefix, name=name):
                    iterator = tab.buffer.get_iter_at_offset(text.index(markup) + 1)
                    self.assertTrue(tab.buffer.iter_has_context_class(iterator, "org-" + name))
            self.assertEqual(tab.get_text(), text)

    def test_effective_styles_in_light_and_dark_themes(self):
        text = " ".join(self.formats.values()) + "\n"
        tab = self.highlighted(text)
        for scheme_id in ("gnorg", "gnorg-dark"):
            tab.buffer.set_style_scheme(
                GtkSource.StyleSchemeManager.get_default().get_scheme(scheme_id))
            tab.buffer.ensure_highlight(*tab.buffer.get_bounds())
            for name, prop, expected in (
                ("bold", "weight", Pango.Weight.BOLD),
                ("italic", "style", Pango.Style.ITALIC),
                ("underline", "underline", Pango.Underline.SINGLE),
                ("strikethrough", "strikethrough", True),
            ):
                iterator = tab.buffer.get_iter_at_offset(text.index(self.formats[name]) + 1)
                values = [tag.get_property(prop) for tag in
                          sorted(iterator.get_tags(), key=lambda tag: tag.get_priority())
                          if tag.get_property(prop + "-set")]
                with self.subTest(scheme=scheme_id, name=name):
                    self.assertTrue(values)
                    self.assertEqual(values[-1], expected)
            family = tab.view.get_pango_context().get_font_description().get_family().lower()
            self.assertTrue("iosevka" in family or "monospace" in family, family)

    def test_literal_spans_do_not_interpret_nested_formats(self):
        for marker, name in (("=", "verbatim"), ("~", "code")):
            text = marker + "*bold* /italic/ _underline_ +strike+" + marker + "\n"
            tab = self.highlighted(text)
            for token in ("bold", "italic", "underline", "strike"):
                iterator = tab.buffer.get_iter_at_offset(text.index(token))
                self.assertTrue(tab.buffer.iter_has_context_class(iterator, "org-" + name))
                for nested in ("bold", "italic", "underline", "strikethrough"):
                    self.assertFalse(tab.buffer.iter_has_context_class(iterator, "org-" + nested))

    def test_boundaries_and_literal_regions_are_not_emphasis(self):
        text = (
            "word*bold* / leading/ _trailing _ x+y+ file_name_part a/b/c\n"
            "# *comment* /comment/\n"
            "#+TITLE: *metadata*\n"
            "#+BEGIN_SRC org\n*literal* /literal/ =literal=\n#+END_SRC\n"
            ":PROPERTIES:\n:VALUE: *literal*\n:END:\n"
        )
        tab = self.highlighted(text)
        for offset in range(len(text)):
            iterator = tab.buffer.get_iter_at_offset(offset)
            for name in self.formats:
                self.assertFalse(tab.buffer.iter_has_context_class(iterator, "org-" + name),
                                 (offset, name))

    def test_punctuation_and_single_character_spans(self):
        text = '(*b*), "/i/"; _u_! +s+? =v=. ~c~\n'
        tab = self.highlighted(text)
        for name, token in zip(self.formats, ("b", "i", "u", "s", "v", "c")):
            iterator = tab.buffer.get_iter_at_offset(text.index(token))
            self.assertTrue(tab.buffer.iter_has_context_class(iterator, "org-" + name), name)

    def test_editing_delimiters_updates_highlighting_and_undo(self):
        tab = self.highlighted("*bold*\n")
        tab.buffer.begin_user_action()
        tab.buffer.delete(tab.buffer.get_iter_at_offset(5), tab.buffer.get_iter_at_offset(6))
        tab.buffer.end_user_action()
        tab.buffer.ensure_highlight(*tab.buffer.get_bounds())
        self.assertFalse(tab.buffer.iter_has_context_class(
            tab.buffer.get_iter_at_offset(2), "org-bold"))
        tab.buffer.undo()
        tab.buffer.ensure_highlight(*tab.buffer.get_bounds())
        self.assertEqual(tab.get_text(), "*bold*\n")
        self.assertTrue(tab.buffer.iter_has_context_class(
            tab.buffer.get_iter_at_offset(2), "org-bold"))
