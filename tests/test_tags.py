"""Tags integration tests."""

from unittest.mock import Mock, patch

from editor_test_case import EditorTestCase
from vim import Gdk, Graphene, Gtk


class TagTests(EditorTestCase):
    def test_nested_tags_remain_clickable_when_body_is_folded(self):
        tab = self.app.new_tab()
        tab.buffer.set_text(
            "#+STARTUP: content\n* Project :work:\n"
            "** DONE Writing :writing:\nCLOSED: [2026-11-02]\n"
            "** TODO Testing :testing:\nSCHEDULED: <2026-11-09>\n"
            "** TODO Review :review:\nDEADLINE: <2026-11-06>\n")
        tab.present()
        align = tab.org_tag_align
        self.wait_for(lambda: all(line in align._hover_regions for line in (2, 4, 6))
                      and tab.org_fold.mode == "content"
                      and align._hover_regions[2][1] < align._hover_regions[4][1]
                      < align._hover_regions[6][1])
        for line, tag in ((2, "writing"), (4, "testing"), (6, "review")):
            left, top, right, bottom = align._hover_regions[line]
            self.assertGreater(bottom, top)
            align._on_motion(None, (left + right) / 2, (top + bottom) / 2)
            self.assertEqual(tab.view.get_cursor().get_name(), "pointer")
            gesture = Mock()
            align._on_tag_pressed(gesture, 1, (left + right) / 2, (top + bottom) / 2)
            gesture.set_state.assert_called_once_with(Gtk.EventSequenceState.CLAIMED)
            entry = tab._active_tag_popover.get_child().get_first_child().get_next_sibling()
            self.assertEqual(entry.get_text(), tag)
            tab._keyboard_quit()
            self.wait_for(lambda: tab._active_tag_popover is None)

    def test_no_edit_icons_and_only_visible_tag_text_is_clickable(self):
        tab = self.app.new_tab()
        tab.buffer.set_text("* Parent :work:\n** Child :home:\n")
        tab.present()
        align = tab.org_tag_align
        self.wait_for(lambda: len(align._hover_regions) == 2)
        child = tab.view.get_first_child()
        while child is not None:
            self.assertFalse(isinstance(child, Gtk.Button))
            child = child.get_next_sibling()
        left, top, right, bottom = align._hover_regions[1]
        align._on_motion(None, left + 1, (top + bottom) / 2)
        self.assertEqual(tab.view.get_cursor().get_name(), "pointer")
        gesture = Mock()
        align._on_tag_pressed(gesture, 1, right + 1, (top + bottom) / 2)
        gesture.set_state.assert_not_called()
        tab.buffer.place_cursor(tab.buffer.get_start_iter())
        tab.org_fold.cycle()
        self.wait_for(lambda: 1 not in align._hover_regions)
        self.assertIn(0, align._hover_regions)


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
                     - 2)
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

    def test_keyboard_tag_editor_anchors_to_line_end_not_cursor(self):
        tab = self.app.new_tab()
        tab.buffer.set_text("* Heading :work:\n")
        tab.buffer.place_cursor(tab.buffer.get_start_iter())
        tab.present()
        self.wait_for(lambda: 0 in tab.org_tag_align._hover_regions
                      and tab.org_tag_align._hover_regions[0][0] > 100)
        popover, _entry = self.open_heading_tags(tab)
        _ok, anchor = popover.get_pointing_to()
        _left, top, right, _bottom = tab.org_tag_align._hover_regions[0]
        _ok, point = tab.view.compute_point(
            tab.editor_box, Graphene.Point().init(right, top))
        self.assertAlmostEqual(anchor.x, point.x, delta=2)
        self.assertAlmostEqual(anchor.y, point.y, delta=2)
        self.assertGreater(anchor.x, tab._cursor_rect().x + 100)

    def test_clicking_tag_text_opens_its_heading_editor(self):
        tab = self.app.new_tab()
        tab.buffer.set_text("* First :work:\n* Second :home:\n")
        tab.present()
        align = tab.org_tag_align
        self.wait_for(lambda: 1 in align._hover_regions
                      and align._hover_regions[1][1] > align._hover_regions[0][1])
        left, top, right, bottom = align._hover_regions[1]
        gesture = Mock()
        align._on_tag_pressed(gesture, 1, left + 1, (top + bottom) / 2)
        gesture.set_state.assert_called_once_with(Gtk.EventSequenceState.CLAIMED)
        self.assertIsNotNone(tab._active_tag_popover)
        entry = tab._active_tag_popover.get_child().get_first_child().get_next_sibling()
        self.assertEqual(entry.get_text(), "home")
        _ok, anchor = tab._active_tag_popover.get_pointing_to()
        _ok, point = tab.view.compute_point(
            tab.editor_box, Graphene.Point().init(right, top))
        self.assertAlmostEqual(anchor.x, point.x, delta=2)
        self.assertAlmostEqual(anchor.y, point.y, delta=2)
        self.assertGreater(anchor.x, tab._cursor_rect().x + 100)
        tab._keyboard_quit()
        gesture.reset_mock()
        align._on_tag_pressed(gesture, 1, left - 10, (top + bottom) / 2)
        gesture.set_state.assert_not_called()
        self.assertIsNone(tab._active_tag_popover)
