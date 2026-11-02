"""Folding integration tests."""

from unittest.mock import patch
from unittest.mock import Mock

from editor_test_case import EditorTestCase
from vim import Gio
from vim import Gdk, Gtk


class FoldingTests(EditorTestCase):
    def test_visual_line_selection_skips_folded_bodies_in_both_directions(self):
        self.addCleanup(self.app.set_show_line_numbers, False)
        self.app.set_show_line_numbers(True)
        tab = self.app.new_tab()
        text = "* First\nBody one\nBody two\n* Second\nBody three\n* Third\n"
        tab.buffer.set_text(text)
        tab.buffer.set_modified(False)
        tab.buffer.place_cursor(tab.buffer.get_start_iter())
        tab.org_fold.cycle(global_cycle=True)
        tab.present()
        self.wait_for(tab.view.get_mapped)
        display = Gdk.Display.get_default()

        def press(keyval, modifiers=Gdk.ModifierType(0)):
            key = display.map_keyval(keyval)[1][0]
            tab.vim.filter_key(True, tab.window.get_surface(),
                               display.get_default_seat().get_keyboard(), 0, key.keycode,
                               modifiers, key.group)

        press(Gdk.KEY_V, Gdk.ModifierType.SHIFT_MASK)
        for keyval, expected_line in (
                (Gdk.KEY_j, 3), (Gdk.KEY_j, 5),
                (Gdk.KEY_k, 3), (Gdk.KEY_k, 0), (Gdk.KEY_j, 3)):
            press(keyval)
            cursor = tab.org_fold._visual_cursor
            self.assertIsNotNone(cursor)
            self.assertEqual(tab.buffer.get_iter_at_mark(cursor).get_line(), expected_line)
            self.assertTrue(tab.buffer.get_has_selection())
            self.assertIn("VISUAL LINE", tab.vim.get_command_bar_text())
        self.assertEqual(tab.get_text(), text)
        self.assertFalse(tab.buffer.get_modified())
        press(Gdk.KEY_d)
        self.assertEqual(tab.get_text(), "* Third\n")
        self.assertTrue(tab.buffer.get_modified())
        tab.buffer.undo()
        self.assertEqual(tab.get_text(), text)

    def test_character_visual_selection_skips_folded_bodies_with_line_numbers(self):
        self.addCleanup(self.app.set_show_line_numbers, False)
        self.app.set_show_line_numbers(True)
        tab = self.app.new_tab()
        text = "* First\nBody one\nBody two\n* Second\nBody three\n* Third\n"
        tab.buffer.set_text(text)
        tab.buffer.set_modified(False)
        tab.buffer.place_cursor(tab.buffer.get_start_iter())
        tab.org_fold.cycle(global_cycle=True)
        tab.present()
        self.wait_for(tab.view.get_mapped)
        display = Gdk.Display.get_default()
        for keyval, expected in ((Gdk.KEY_v, 0), (Gdk.KEY_j, 3), (Gdk.KEY_j, 5),
                                  (Gdk.KEY_k, 3), (Gdk.KEY_k, 0)):
            key = display.map_keyval(keyval)[1][0]
            tab.vim.filter_key(True, tab.window.get_surface(),
                               display.get_default_seat().get_keyboard(), 0, key.keycode,
                               Gdk.ModifierType(0), key.group)
            self.assertIsNotNone(tab.org_fold._visual_cursor)
            cursor = tab.buffer.get_iter_at_mark(tab.org_fold._visual_cursor)
            self.assertEqual(cursor.get_line(), expected)
            self.assertFalse(cursor.has_tag(tab.org_fold.hidden))
            self.assertTrue(tab.buffer.get_has_selection())
        self.assertEqual(tab.get_text(), text)
        self.assertFalse(tab.buffer.get_modified())

    def test_visual_selection_from_mid_heading_in_startup_overview(self):
        text = ("#+TITLE: Overview\n#+STARTUP: overview\n\n"
                "* TODO First task\n** Child\nBody\n\n"
                "* Second task\nSCHEDULED: <2026-11-03 Tue>\n"
                "* Third task\nBody\n* Last task\nHidden tail\n")
        for mode, modifiers in ((Gdk.KEY_v, Gdk.ModifierType(0)),
                                (Gdk.KEY_V, Gdk.ModifierType.SHIFT_MASK)):
            for column in (0, 3, 10):
                with self.subTest(mode=mode, column=column):
                    tab = self.app.new_tab()
                    tab.buffer.set_text(text)
                    tab.buffer.set_modified(False)
                    tab.org_fold.refresh()
                    tab.buffer.place_cursor(tab.buffer.get_iter_at_line_offset(3, column)[1])
                    tab.present()
                    self.wait_for(tab.view.get_mapped)
                    display = Gdk.Display.get_default()

                    def press(keyval, state=Gdk.ModifierType(0)):
                        key = display.map_keyval(keyval)[1][0]
                        tab.vim.filter_key(True, tab.window.get_surface(),
                                           display.get_default_seat().get_keyboard(),
                                           0, key.keycode, state, key.group)

                    press(mode, modifiers)
                    for keyval, expected in ((Gdk.KEY_j, 7), (Gdk.KEY_j, 9),
                                             (Gdk.KEY_j, 11), (Gdk.KEY_k, 9),
                                             (Gdk.KEY_k, 7), (Gdk.KEY_k, 3)):
                        press(keyval)
                        mark = tab.org_fold._visual_cursor
                        self.assertIsNotNone(mark)
                        self.assertEqual(tab.buffer.get_iter_at_mark(mark).get_line(), expected)
                        if mode == Gdk.KEY_V:
                            first, last = tab.buffer.get_selection_bounds()
                            self.assertEqual((first.get_line(), first.get_line_offset()), (3, 0))
                            self.assertEqual((last.get_line(), last.get_line_offset()),
                                             ({3: 4, 7: 9, 9: 11, 11: 13}[expected], 0))
                    self.assertEqual(tab.get_text(), text)
                    self.assertFalse(tab.buffer.get_modified())

    def test_heading_cycle_and_startup_visibility(self):
        text = "* Parent\nBody\n** Child\nChild body\n*** Grandchild\nDetails\n* Next\nTail"
        tab = self.app.new_tab()
        tab.buffer.set_text(text)
        tab.buffer.set_modified(False)
        tab.buffer.place_cursor(tab.buffer.get_start_iter())
        tab.present()

        def hidden(line):
            return tab.buffer.get_iter_at_line(line)[1].has_tag(tab.org_fold.hidden)

        for expected in ((True, True, True), (True, False, True), (False, False, False)):
            self.assertTrue(tab.window._on_key_pressed(
                None, Gdk.KEY_Tab, 0, Gdk.ModifierType(0)))
            self.assertEqual(tuple(hidden(i) for i in (1, 2, 4)), expected)
        self.assertFalse(tab.buffer.get_modified())
        self.assertEqual(tab.get_text(), text)
        for option, expected in (
                ("overview", (True, True, True)),
                ("content", (True, False, False)),
                ("showall", (False, False, False)),
                ("showeverything", (False, False, False))):
            tab._set_file(Gio.File.new_for_path("/org/folding.org"),
                          f"#+STARTUP: {option}\n{text}")
            self.assertEqual(tuple(hidden(i) for i in (2, 3, 5)), expected)
            self.assertFalse(tab.buffer.get_modified())
        tab.org_fold.cycle(global_cycle=True)
        self.assertTrue(hidden(3))
        tab.goto_line(4)
        self.assertFalse(hidden(4))

    def test_folding_preserves_state_after_body_edits_and_reads_startup_changes(self):
        tab = self.app.new_tab()
        tab.buffer.set_text("* Parent\nBody\n** Child\nDetails\n")
        tab.buffer.place_cursor(tab.buffer.get_start_iter())
        self.assertTrue(tab.org_fold.cycle())
        tab.buffer.insert(tab.buffer.get_iter_at_line(1)[1], "More ")
        tab.org_fold.refresh()
        self.assertTrue(tab.buffer.get_iter_at_line(2)[1].has_tag(tab.org_fold.hidden))
        tab.buffer.insert(tab.buffer.get_start_iter(), "#+STARTUP: content\n")
        tab.org_fold.refresh()
        self.assertFalse(tab.buffer.get_iter_at_line(3)[1].has_tag(tab.org_fold.hidden))
        self.assertTrue(tab.buffer.get_iter_at_line(4)[1].has_tag(tab.org_fold.hidden))
        tab.buffer.place_cursor(tab.buffer.get_start_iter())
        self.assertFalse(tab.org_fold.cycle())

    def test_fold_indicators_follow_hidden_content_without_changing_text(self):
        tab = self.app.new_tab()
        text = "* Parent\nBody\n** Child\nDetails\n* Empty"
        tab.buffer.set_text(text)
        tab.buffer.set_modified(False)
        tab.buffer.place_cursor(tab.buffer.get_start_iter())
        tab.present()
        tab.org_fold.refresh()
        self.assertEqual(tab.org_fold.indicators, {})
        tab.org_fold.cycle()
        self.assertEqual(set(tab.org_fold.indicators), {0})
        self.assertEqual(tab.org_fold.indicators[0].get_label(), "...")
        indicator = tab.org_fold.indicators[0]
        self.assertFalse(indicator.has_css_class("dim-label"))
        tag = tab.buffer.create_tag(None, foreground="#26a269", weight=700)
        end = tab.buffer.get_iter_at_line(0)[1]
        end.forward_to_line_end()
        tab.buffer.apply_tag(tag, tab.buffer.get_start_iter(), end)
        tab.org_fold._layout_indicators()
        self.assertEqual(tab.org_fold._indicator_styles[0], ("rgb(38,162,105)", 700))
        rect = tab.view.get_iter_location(end)
        self.assertEqual(tab.org_fold._indicator_positions[0], (rect.x, rect.y))
        tab.org_fold.cycle()
        self.assertEqual(set(tab.org_fold.indicators), {0, 2})
        tab.org_fold.cycle()
        self.assertEqual(tab.org_fold.indicators, {})
        tab.org_fold.cycle(global_cycle=True)
        self.assertEqual(set(tab.org_fold.indicators), {0})
        self.assertEqual(tab.get_text(), text)
        self.assertFalse(tab.buffer.get_modified())

    def test_fold_indicator_actual_bounds_align_with_heading_without_trailing_spaces(self):
        tab = self.app.new_tab()
        tab.buffer.set_text("* Parent   \nBody\n* Next")
        tab.buffer.place_cursor(tab.buffer.get_start_iter())
        tab.present()
        tab.org_fold.cycle()
        label = tab.org_fold.indicators[0]
        self.wait_for(lambda: label.get_width() > 0)
        end = tab.buffer.get_iter_at_line_offset(0, len("* Parent"))[1]
        rect = tab.view.get_iter_location(end)
        x, y = tab.view.buffer_to_window_coords(Gtk.TextWindowType.WIDGET, rect.x, rect.y)

        def aligned():
            ok, bounds = label.compute_bounds(tab.view)
            return ok and abs(bounds.get_x() - x) <= 1 and abs(bounds.get_y() - y) <= 1

        self.wait_for(aligned)
        self.assertEqual(tab.get_text(), "* Parent   \nBody\n* Next")

    def test_line_numbers_skip_folded_bodies_and_restore_when_expanded(self):
        from gi.repository import GtkSource

        tab = self.app.new_tab()
        tab.buffer.set_text("* Parent\nBody\nMore body\n* Next\nTail\n")
        tab.buffer.place_cursor(tab.buffer.get_start_iter())
        self.addCleanup(self.app.set_show_line_numbers, False)
        self.app.set_show_line_numbers(True)
        tab.present()
        self.wait_for(tab.org_line_numbers.get_mapped)
        self.assertFalse(tab.org_line_numbers.native.get_visible())
        lines = Mock()
        lines.get_iter_at_line.side_effect = lambda line: tab.buffer.get_iter_at_line(line)[1]

        def drawn_lines():
            drawn = []
            with patch.object(GtkSource.GutterRendererText, "do_snapshot_line",
                              side_effect=lambda *args: drawn.append(args[-1])):
                for line in range(tab.buffer.get_line_count()):
                    tab.org_line_numbers.do_snapshot_line(Mock(), lines, line)
            return drawn

        tab.org_fold.cycle(global_cycle=True)
        self.assertEqual(drawn_lines(), [0, 3, 5])
        tab.org_fold.cycle(global_cycle=True)
        tab.org_fold.cycle(global_cycle=True)
        self.assertEqual(drawn_lines(), list(range(6)))
        self.app.set_show_line_numbers(False)
        self.assertFalse(tab.org_line_numbers.get_visible())
        self.app.set_show_line_numbers(True)
        self.assertTrue(tab.org_line_numbers.get_visible())
        self.assertFalse(tab.org_line_numbers.native.get_visible())

    def test_fold_dots_stay_after_title_before_right_aligned_tags(self):
        tab = self.app.new_tab()
        text = "* Parent :work:home:\nBody\n* Next\n"
        tab.buffer.set_text(text)
        tab.buffer.set_modified(False)
        tab.buffer.place_cursor(tab.buffer.get_start_iter())
        tab.present()
        tab.org_fold.cycle()
        label = tab.org_fold.indicators[0]

        def aligned():
            last = tab.buffer.get_iter_at_line_offset(0, len("* Parent") - 1)[1]
            rect = tab.view.get_iter_location(last)
            x, y = tab.view.buffer_to_window_coords(
                Gtk.TextWindowType.WIDGET, rect.x + rect.width, rect.y)
            ok, bounds = label.compute_bounds(tab.view)
            tags = tab.view.get_iter_location(
                tab.buffer.get_iter_at_line_offset(0, len("* Parent "))[1])
            return (ok and label.get_width() > 0
                    and abs(bounds.get_x() - x) <= 1
                    and abs(bounds.get_y() - y) <= 1
                    and rect.x + rect.width + label.get_width() < tags.x)

        self.wait_for(aligned)
        old_width = tab.view.get_width()
        tab.window.set_default_size(tab.window.get_width() + 180, tab.window.get_height())
        self.wait_for(lambda: tab.view.get_width() != old_width)
        self.wait_for(aligned)
        self.assertEqual(tab.get_text(), text)
        self.assertFalse(tab.buffer.get_modified())

    def test_cursor_skips_folded_lines_in_both_directions(self):
        tab = self.app.new_tab()
        tab.buffer.set_text("* Parent\nBody\n** Child\nDetails\n* Next\nTail")
        tab.buffer.place_cursor(tab.buffer.get_start_iter())
        tab.org_fold.cycle()
        tab.buffer.place_cursor(tab.buffer.get_iter_at_line(1)[1])
        cursor = tab.buffer.get_iter_at_mark(tab.buffer.get_insert())
        self.assertEqual(cursor.get_line(), 4)
        self.assertFalse(cursor.has_tag(tab.org_fold.hidden))
        self.assertFalse(tab.buffer.get_has_selection())
        tab.buffer.place_cursor(tab.buffer.get_iter_at_line(3)[1])
        cursor = tab.buffer.get_iter_at_mark(tab.buffer.get_insert())
        self.assertEqual(cursor.get_line(), 0)
        self.assertFalse(cursor.has_tag(tab.org_fold.hidden))
        tab.buffer.place_cursor(tab.buffer.get_iter_at_line(4)[1])
        tab.org_fold.cycle()
        tab.buffer.place_cursor(tab.buffer.get_iter_at_line(5)[1])
        self.assertEqual(tab.buffer.get_iter_at_mark(tab.buffer.get_insert()).get_line(), 4)

    def test_tab_opens_folded_leaf_immediately_and_skips_unchanged_children_state(self):
        tab = self.app.new_tab()
        tab.buffer.set_text("* Parent\nBody\n* Next")
        tab.buffer.place_cursor(tab.buffer.get_start_iter())
        for expected_hidden in (True, False, True, False):
            tab.org_fold.cycle()
            self.assertEqual(tab.buffer.get_iter_at_line(1)[1].has_tag(tab.org_fold.hidden),
                             expected_hidden)
        tab._set_file(Gio.File.new_for_path("/org/folding.org"),
                      "#+STARTUP: content\n* Parent\nBody\n** Child\nDetails\n* Next")
        tab.buffer.place_cursor(tab.buffer.get_iter_at_line(1)[1])
        tab.org_fold.cycle()
        self.assertFalse(tab.buffer.get_iter_at_line(2)[1].has_tag(tab.org_fold.hidden))
        self.assertFalse(tab.buffer.get_iter_at_line(4)[1].has_tag(tab.org_fold.hidden))

    def test_collapsed_subtree_has_no_gap_with_org_indentation(self):
        tab = self.app.new_tab()
        text = "* Parent\nBody\n** Child\nDetails\n\n\n* Next\nTail"
        tab.buffer.set_text(text)
        tab.buffer.place_cursor(tab.buffer.get_start_iter())
        tab.present()
        tab.org_fold.cycle()

        def compact():
            first = tab.view.get_iter_location(tab.buffer.get_start_iter())
            following = tab.view.get_iter_location(tab.buffer.get_iter_at_line(6)[1])
            return following.y == first.y + first.height

        self.wait_for(compact)
        tab.org_fold.cycle()
        self.wait_for(lambda: not tab.buffer.get_iter_at_line(2)[1].has_tag(tab.org_fold.hidden))
        tab.org_fold.cycle()
        tab.org_fold.cycle()
        self.wait_for(compact)
        self.assertEqual(tab.get_text(), text)
