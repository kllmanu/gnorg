"""Tags integration tests."""

from unittest.mock import patch

from editor_test_case import EditorTestCase
from vim import Gdk, Gtk


class TagTests(EditorTestCase):
    def open_heading_tags(self, tab):
        tab.present()
        self.wait_for(tab.view.get_mapped)
        for _ in range(2):
            tab._org_key_sequences._on_key_pressed(
                None, Gdk.KEY_c, 0, Gdk.ModifierType.CONTROL_MASK)
        popover = tab._active_tag_popover
        self.assertIsNotNone(popover)
        return popover, popover.get_child().get_first_child().get_next_sibling()

    def test_heading_tag_editor_applies_removes_and_undoes(self):
        tab = self.app.new_tab()
        original = "** TODO [#A] Report :work:\nBody\n"
        tab.buffer.set_text(original)
        tab.buffer.place_cursor(tab.buffer.get_start_iter())
        popover, entry = self.open_heading_tags(tab)
        self.assertEqual(entry.get_text(), "work")
        entry.set_text("home:project:home")
        entry.emit("activate")
        self.assertIsNone(tab._active_tag_popover)
        self.assertEqual(tab.get_text(), "** TODO [#A] Report :home:project:\nBody\n")
        tab.buffer.undo()
        self.assertEqual(tab.get_text(), original)
        popover, entry = self.open_heading_tags(tab)
        entry.set_text("")
        entry.emit("activate")
        self.assertEqual(tab.get_text(), "** TODO [#A] Report\nBody\n")

    def test_heading_tag_editor_validation_cancel_and_stale_heading(self):
        tab = self.app.new_tab()
        tab.buffer.set_text("* Report :work:\n")
        tab.buffer.place_cursor(tab.buffer.get_start_iter())
        original = tab.get_text()
        popover, entry = self.open_heading_tags(tab)
        entry.set_text("bad/tag")
        entry.emit("activate")
        self.assertEqual(tab.get_text(), original)
        self.assertIs(tab._active_tag_popover, popover)
        entry.set_text("home")
        tab._keyboard_quit()
        self.assertEqual(tab.get_text(), original)
        popover, entry = self.open_heading_tags(tab)
        tab.buffer.insert(tab.buffer.get_start_iter(), "changed")
        with patch.object(tab, "toast") as toast:
            entry.emit("activate")
            toast.assert_called_once()
        self.assertEqual(tab.get_text(), "changed" + original)

    def test_heading_tags_align_right_after_resize_without_changing_text(self):
        tab = self.app.new_tab()
        text = "* First :work:\n** Second :home:project:\nBody :not_a_heading:\n"
        tab.buffer.set_text(text)
        tab.buffer.set_modified(False)
        tab.present()
        self.wait_for(tab.view.get_mapped)

        def aligned():
            right = (tab.view.get_visible_rect().width - tab.view.get_right_margin()
                     - 2 - tab.org_tag_align.BUTTON_SPACE)
            return right > 100 and all(abs(tab.view.get_iter_location(
                tab.buffer.get_iter_at_line_offset(line, len(heading))[1]).x - right) <= 2
                for line, heading in enumerate(text.splitlines()[:2]))

        self.wait_for(aligned)
        adjustment = tab.view.get_hadjustment()
        self.wait_for(lambda: adjustment.get_upper() <= adjustment.get_page_size())
        old_width = tab.view.get_width()
        tab.window.set_default_size(tab.window.get_width() + 180, tab.window.get_height())
        self.wait_for(lambda: tab.view.get_width() != old_width)
        self.wait_for(aligned)
        self.wait_for(lambda: adjustment.get_upper() <= adjustment.get_page_size())
        old_width = tab.view.get_width()
        tab.window.set_default_size(tab.window.get_width() - 260, tab.window.get_height())
        self.wait_for(lambda: tab.view.get_width() != old_width)
        self.wait_for(aligned)
        self.wait_for(lambda: adjustment.get_upper() <= adjustment.get_page_size())
        self.assertEqual(tab.get_text(), text)
        previous_zoom = self.app.zoom
        self.addCleanup(lambda: self.app.set_zoom(previous_zoom, notify=False))
        old_width = tab.view.get_width()
        tab.window.set_default_size(tab.window.get_width() + 200, tab.window.get_height())
        self.wait_for(lambda: tab.view.get_width() != old_width)
        display = Gdk.Display.get_default()
        Gtk.StyleContext.remove_provider_for_display(display, self.app.zoom_css)
        Gtk.StyleContext.add_provider_for_display(
            display, self.app.zoom_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        for zoom in (150, 200, 100):
            if self.app.zoom == zoom:
                continue
            old_font_size = tab.view.create_pango_layout(" ").get_pixel_size()
            self.app.set_zoom(zoom, notify=False)
            self.wait_for(lambda: tab.view.create_pango_layout(" ").get_pixel_size()
                          != old_font_size)
            self.wait_for(aligned)
            self.wait_for(lambda: adjustment.get_upper() <= adjustment.get_page_size())
        self.assertFalse(tab.buffer.get_modified())
        text = "* First :longer_tag:another:\n** Second :home:project:\n"
        tab.buffer.set_text(text)
        self.wait_for(aligned)
        self.assertEqual(tab.get_text(), text)
        tab.buffer.insert(tab.buffer.get_end_iter(), "A genuinely long line " * 100)
        self.wait_for(lambda: adjustment.get_upper() > adjustment.get_page_size())

    def test_tag_edit_icon_opens_correct_heading_and_hides_when_folded(self):
        tab = self.app.new_tab()
        text = "* Parent :work:\n** Child :home:\nBody\n"
        tab.buffer.set_text(text)
        tab.buffer.place_cursor(tab.buffer.get_start_iter())
        tab.present()
        self.wait_for(lambda: len(tab.org_tag_align.buttons) == 2)
        button = tab.org_tag_align.buttons[0]

        def icon_inside_padding():
            ok, bounds = button.compute_bounds(tab.view)
            return (ok and button.get_width() > 0
                    and bounds.get_x() + bounds.get_width()
                    <= tab.view.get_width() - tab.view.get_right_margin())

        self.wait_for(icon_inside_padding)
        align = tab.org_tag_align
        align._on_leave()
        self.assertEqual(button.get_opacity(), 0)
        self.assertFalse(button.get_can_target())
        left, top, right, bottom = align._hover_regions[0]
        align._on_motion(None, left + 1, (top + bottom) / 2)
        self.assertEqual(button.get_opacity(), 1)
        self.assertTrue(button.get_can_target())
        self.assertEqual(tab.view.get_cursor().get_name(), "pointer")
        align._on_motion(None, right - 1, (top + bottom) / 2)
        self.assertEqual(button.get_opacity(), 1)
        align._on_motion(None, left - 10, (top + bottom) / 2)
        self.assertEqual(button.get_opacity(), 0)
        self.assertEqual(tab.view.get_cursor().get_name(), "text")
        tab.org_tag_align.buttons[1].emit("clicked")
        self.assertIsNotNone(tab._active_tag_popover)
        entry = tab._active_tag_popover.get_child().get_first_child().get_next_sibling()
        self.assertEqual(entry.get_text(), "home")
        tab._keyboard_quit()
        tab.buffer.place_cursor(tab.buffer.get_start_iter())
        tab.org_fold.cycle()
        self.wait_for(lambda: not tab.org_tag_align.buttons[1].get_visible())
        self.assertTrue(tab.org_tag_align.buttons[0].get_visible())
        self.assertEqual(tab.get_text(), text)
