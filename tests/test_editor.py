"""Editor integration tests."""

from unittest.mock import Mock, patch

from editor_test_case import EditorTestCase
from vim import GLib, Gtk


class EditorTests(EditorTestCase):
    def test_clicking_last_heading_toggles_body_at_end_of_file(self):
        for suffix in ("", "\n"):
            with self.subTest(suffix=suffix):
                tab = self.app.new_tab()
                text = "* First\nBody\n* Last\nLast body" + suffix
                tab.buffer.set_text(text)
                tab.present()
                self.wait_for(tab.view.get_mapped)
                interaction = tab.org_todo_interaction

                def point():
                    rect = tab.view.get_iter_location(
                        tab.buffer.get_iter_at_line_offset(2, 3)[1])
                    return tab.view.buffer_to_window_coords(
                        Gtk.TextWindowType.WIDGET, rect.x + 2, rect.y + rect.height // 2)

                self.wait_for(lambda: interaction._target_at(*point()) == (2, "heading"))
                for hidden in (True, False, True, False):
                    interaction._pressed(Mock(), 1, *point())
                    self.assertEqual(tab.buffer.get_iter_at_line(3)[1].has_tag(
                        tab.org_fold.hidden), hidden)
                self.assertEqual(tab.get_text(), text)

    def test_clicking_heading_title_toggles_immediate_children_without_cycling(self):
        tab = self.app.new_tab()
        text = "* TODO Parent :work:\nBody\n** Child\nDetails\n* Next\nTail\n"
        tab.buffer.set_text(text)
        tab.buffer.set_modified(False)
        tab.present()
        self.wait_for(tab.view.get_mapped)
        interaction = tab.org_todo_interaction

        def point():
            rect = tab.view.get_iter_location(tab.buffer.get_iter_at_line_offset(0, 7)[1])
            return tab.view.buffer_to_window_coords(
                Gtk.TextWindowType.WIDGET, rect.x + 2, rect.y + rect.height // 2)

        self.wait_for(lambda: interaction._target_at(*point()) == (0, "heading"))
        for expected_hidden in (True, False, True, False):
            gesture = Mock()
            interaction._pressed(gesture, 1, *point())
            gesture.set_state.assert_called_once_with(Gtk.EventSequenceState.CLAIMED)
            self.assertEqual(tab.buffer.get_iter_at_line(2)[1].has_tag(
                tab.org_fold.hidden), expected_hidden)
            for line in (1, 3):
                self.assertTrue(tab.buffer.get_iter_at_line(line)[1].has_tag(
                    tab.org_fold.hidden))
            self.assertFalse(tab.buffer.get_iter_at_line(4)[1].has_tag(tab.org_fold.hidden))
        self.assertEqual(tab.get_text(), text)
        self.assertFalse(tab.buffer.get_modified())
        tab.org_fold.cycle(global_cycle=True)
        tab.org_fold.cycle(global_cycle=True)
        tab.org_fold.toggle(0)
        self.assertTrue(all(tab.buffer.get_iter_at_line(line)[1].has_tag(
            tab.org_fold.hidden) for line in (1, 2, 3)))
        tab.org_fold.toggle(0)
        self.assertFalse(tab.buffer.get_iter_at_line(2)[1].has_tag(tab.org_fold.hidden))
        self.assertTrue(tab.buffer.get_iter_at_line(3)[1].has_tag(tab.org_fold.hidden))

    def test_clicking_list_checkboxes_toggles_with_pointer_and_undo(self):
        tab = self.app.new_tab()
        text = "* Tasks\n- [ ] First\n  1. [X] Second\n+ [-] Third\nPlain [ ] text\n"
        tab.buffer.set_text(text)
        tab.present()
        self.wait_for(tab.view.get_mapped)
        interaction = tab.org_todo_interaction
        for line, column, expected in ((1, 2, "- [X] First"),
                                       (2, 5, "  1. [ ] Second"),
                                       (3, 2, "+ [X] Third")):
            with self.subTest(line=line):
                def point():
                    rect = tab.view.get_iter_location(
                        tab.buffer.get_iter_at_line_offset(line, column)[1])
                    return tab.view.buffer_to_window_coords(
                        Gtk.TextWindowType.WIDGET, rect.x + 2, rect.y + rect.height // 2)

                self.wait_for(lambda: interaction._keyword_at(*point()) == line)
                interaction._motion(None, *point())
                self.assertEqual(tab.view.get_cursor().get_name(), "pointer")
                gesture = Mock()
                interaction._pressed(gesture, 1, *point())
                gesture.set_state.assert_called_once_with(Gtk.EventSequenceState.CLAIMED)
                self.assertEqual(tab.get_text().splitlines()[line], expected)
                tab.buffer.undo()
                self.assertEqual(tab.get_text(), text)
        rect = tab.view.get_iter_location(tab.buffer.get_iter_at_line_offset(4, 6)[1])
        point = tab.view.buffer_to_window_coords(
            Gtk.TextWindowType.WIDGET, rect.x + 2, rect.y + rect.height // 2)
        self.assertIsNone(interaction._keyword_at(*point))

    def test_clicking_todo_keyword_cycles_and_supports_undo(self):
        tab = self.app.new_tab()
        original = "* Parent\n** TODO Task :work:\nBody TODO is not a heading\n"
        tab.buffer.set_text(original)
        tab.present()
        self.wait_for(tab.view.get_mapped)
        interaction = tab.org_todo_interaction

        def keyword_point():
            rect = tab.view.get_iter_location(tab.buffer.get_iter_at_line_offset(1, 3)[1])
            return tab.view.buffer_to_window_coords(
                Gtk.TextWindowType.WIDGET, rect.x + 2, rect.y + rect.height // 2)

        self.wait_for(lambda: interaction._keyword_at(*keyword_point()) == 1)
        interaction._motion(None, *keyword_point())
        self.assertEqual(tab.view.get_cursor().get_name(), "pointer")
        gesture = Mock()
        interaction._pressed(gesture, 1, *keyword_point())
        gesture.set_state.assert_called_once_with(Gtk.EventSequenceState.CLAIMED)
        self.assertIn("** DONE Task :work:", tab.get_text())
        tab.buffer.undo()
        self.assertEqual(tab.get_text(), original)
        interaction._leave()
        self.assertEqual(tab.view.get_cursor().get_name(), "text")
        rect = tab.view.get_iter_location(tab.buffer.get_iter_at_line_offset(2, 5)[1])
        point = tab.view.buffer_to_window_coords(
            Gtk.TextWindowType.WIDGET, rect.x + 2, rect.y + rect.height // 2)
        self.assertIsNone(interaction._keyword_at(*point))
        gesture.reset_mock()
        interaction._pressed(gesture, 1, *point)
        gesture.set_state.assert_not_called()

    def test_application_branding(self):
        tab = self.app.new_tab()
        self.assertEqual(GLib.get_application_name(), "Gnorg")
        self.assertEqual(tab.window.get_title(), "Untitled – Gnorg")
        with patch("vim.Adw.AboutDialog") as about:
            self.app._on_about()
            self.assertEqual(about.call_args.kwargs["application_name"], "Gnorg")
            self.assertIn("Gnorg is not org", about.call_args.kwargs["comments"])
            about.return_value.present.assert_called_once_with(self.app.get_active_window())

    def test_confirm_org_item_still_toggles_checkboxes(self):
        tab = self.app.new_tab()
        tab.buffer.set_text("* Report\n- [ ] Task\n")
        tab.buffer.place_cursor(tab.buffer.get_iter_at_line(1)[1])
        tab.confirm_org_item()
        self.assertEqual(tab.get_text(), "* Report\n- [X] Task\n")
        self.assertIsNone(tab._active_tag_popover)
