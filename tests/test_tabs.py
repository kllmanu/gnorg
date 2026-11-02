import os
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir, "src"))

from vim import Adw, Gdk, Gio, GLib, Gtk, OrganizerApplication, ZOOM_MAX, ZOOM_MIN  # noqa: E402
from bufferpicker import BufferPicker, matches_buffer  # noqa: E402
from agenda import event_lanes, event_minutes  # noqa: E402


@unittest.skipUnless(Gtk.init_check(), "GTK display required")
class TabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = OrganizerApplication()
        cls.app.set_application_id("io.github.kllmanu.Organizer.TabTests")
        cls.app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
        with patch.object(OrganizerApplication, "_load_settings", return_value=None):
            cls.app.register(None)

    def setUp(self):
        self.app.recent_files = []
        self.addCleanup(self.cleanup_windows)

    def cleanup_windows(self):
        for window in self.app.get_windows():
            window.destroy()

    def wait_for(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            while GLib.MainContext.default().pending():
                GLib.MainContext.default().iteration(False)
            time.sleep(0.01)
        self.assertTrue(predicate())

    def test_new_and_close_actions_target_selected_tab(self):
        first = self.app.new_tab()
        first.buffer.set_text("* First")
        second = self.app.new_tab()
        window = first.window
        self.assertIs(second.window, window)
        window.lookup_action("new").activate(None)
        self.assertEqual(len(self.app.editor_tabs()), 3)
        second.present()
        window.lookup_action("close").activate(None)
        self.assertEqual(len(self.app.editor_tabs()), 2)
        self.assertEqual(first.get_text(), "* First")
        self.assertIn(first, self.app.editor_tabs())

    def test_alt_navigation_wraps_tabs_and_cycles_views_without_changing_hidden_tabs(self):
        first = self.app.new_tab()
        second = self.app.new_tab()
        window = first.window
        second.present()

        def press(key, modifiers=Gdk.ModifierType.ALT_MASK):
            return window._on_key_pressed(None, key, 0, modifiers)

        self.assertTrue(press(Gdk.KEY_l))
        self.assertIs(window.active_tab, first)
        self.assertTrue(press(Gdk.KEY_h))
        self.assertIs(window.active_tab, second)
        self.assertTrue(press(Gdk.KEY_h))
        self.assertIs(window.active_tab, first)
        self.assertTrue(press(Gdk.KEY_l))
        self.assertIs(window.active_tab, second)
        for expected in ("weekly-agenda", "agenda", "files"):
            self.assertTrue(press(Gdk.KEY_o))
            self.assertEqual(window.stack.get_visible_child_name(), expected)
            if expected != "files":
                self.assertFalse(press(Gdk.KEY_h))
                self.assertFalse(press(Gdk.KEY_l))
                self.assertEqual(window.stack.get_visible_child_name(), expected)
                self.assertIs(window.active_tab, second)
        self.assertIs(window.active_view, second)
        self.assertFalse(press(Gdk.KEY_o, Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.CONTROL_MASK))

    def test_alt_navigation_with_single_tab(self):
        tab = self.app.new_tab()
        for key in (Gdk.KEY_h, Gdk.KEY_l):
            self.assertTrue(tab.window._on_key_pressed(None, key, 0, Gdk.ModifierType.ALT_MASK))
            self.assertIs(tab.window.active_tab, tab)

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

    def test_timestamp_shortcut_inserts_active_timestamp_and_undoes_once(self):
        import org

        tab = self.app.new_tab()
        tab.buffer.set_text("* Meeting\nNotes: ")
        tab.buffer.place_cursor(tab.buffer.get_end_iter())
        tab.present()
        self.assertTrue(tab.window._on_key_pressed(
            None, Gdk.KEY_c, 0, Gdk.ModifierType.CONTROL_MASK))
        self.assertTrue(tab.window._on_key_pressed(None, Gdk.KEY_period, 0, Gdk.ModifierType(0)))
        popover = tab._active_date_popover
        self.assertIsNotNone(popover)
        popover.entry.set_text("2026-11-05 10:00-11:00 +1w")
        timestamp = popover.timestamp
        self.assertTrue(timestamp.active)
        popover._apply()
        self.wait_for(lambda: tab._active_date_popover is None)
        self.assertEqual(tab.get_text(), "* Meeting\nNotes: <2026-11-05 Thu 10:00-11:00 +1w>")
        self.assertTrue(tab.buffer.get_modified())
        self.assertEqual(
            tab.buffer.get_iter_at_mark(tab.buffer.get_insert()).get_offset(), len(tab.get_text()))
        self.assertEqual(org.parse_entries(tab.get_text())[0].timestamps, [timestamp])
        tab.buffer.undo()
        self.assertEqual(tab.get_text(), "* Meeting\nNotes: ")

    def test_timestamp_edits_existing_active_timestamp_without_remove_option(self):
        tab = self.app.new_tab()
        tab.buffer.set_text("Before <2026-11-05 Thu 10:00> after")
        tab.buffer.place_cursor(tab.buffer.get_iter_at_offset(12))
        tab.present()
        tab.insert_timestamp()
        popover = tab._active_date_popover
        self.assertEqual(popover.entry.get_text(), "2026-11-05 10:00")
        buttons = popover.get_child().get_last_child()
        self.assertIs(buttons.get_first_child(), popover.set_button)
        popover.entry.set_text("2026-11-06 12:30")
        popover._apply()
        self.wait_for(lambda: tab._active_date_popover is None)
        self.assertEqual(tab.get_text(), "Before <2026-11-06 Fri 12:30> after")
        tab.buffer.undo()
        self.assertEqual(tab.get_text(), "Before <2026-11-05 Thu 10:00> after")

    def test_timestamp_cancel_and_invalid_input_leave_buffer_unchanged(self):
        tab = self.app.new_tab()
        tab.buffer.set_text("Plain text without a heading")
        tab.buffer.set_modified(False)
        tab.present()
        tab.insert_timestamp()
        popover = tab._active_date_popover
        popover.entry.set_text("not a date")
        self.assertFalse(popover.set_button.get_sensitive())
        popover._apply()
        self.assertEqual(tab.get_text(), "Plain text without a heading")
        tab._keyboard_quit()
        self.wait_for(lambda: tab._active_date_popover is None)
        self.assertFalse(tab.buffer.get_modified())

    def test_shift_arrows_adjust_timestamp_and_preserve_cursor_and_undo(self):
        tab = self.app.new_tab()
        original = "* Meeting\n<2026-11-02 Mon 09:30>"
        tab.buffer.set_text(original)
        tab.buffer.place_cursor(tab.buffer.get_iter_at_offset(28))
        tab.present()
        self.assertTrue(tab.window._on_key_pressed(
            None, Gdk.KEY_Up, 0, Gdk.ModifierType.SHIFT_MASK))
        self.assertEqual(tab.get_text(), "* Meeting\n<2026-11-02 Mon 10:30>")
        self.assertEqual(tab.buffer.get_iter_at_mark(tab.buffer.get_insert()).get_offset(), 28)
        self.assertTrue(tab.window._on_key_pressed(
            None, Gdk.KEY_Down, 0, Gdk.ModifierType.SHIFT_MASK))
        self.assertEqual(tab.get_text(), original)
        tab.buffer.undo()
        self.assertEqual(tab.get_text(), "* Meeting\n<2026-11-02 Mon 10:30>")
        tab.buffer.undo()
        self.assertEqual(tab.get_text(), original)
        tab.buffer.place_cursor(tab.buffer.get_iter_at_offset(15))
        self.assertTrue(tab.window._on_key_pressed(
            None, Gdk.KEY_Up, 0, Gdk.ModifierType.SHIFT_MASK))
        self.assertEqual(tab.get_text(), "* Meeting\n<2026-11-03 Tue 09:30>")
        tab.buffer.place_cursor(tab.buffer.get_start_iter())
        self.assertFalse(tab.window._on_key_pressed(
            None, Gdk.KEY_Up, 0, Gdk.ModifierType.SHIFT_MASK))

    def test_timestamp_reports_conflicting_changes_instead_of_overwriting(self):
        tab = self.app.new_tab()
        tab.buffer.set_text("<2026-11-05 Thu>")
        tab.buffer.place_cursor(tab.buffer.get_iter_at_offset(5))
        tab.present()
        tab.insert_timestamp()
        popover = tab._active_date_popover
        popover.entry.set_text("2026-11-06")
        tab.buffer.insert(tab.buffer.get_iter_at_offset(7), "changed")
        text = tab.get_text()
        with patch.object(tab, "toast") as toast:
            popover._apply()
            toast.assert_called_once_with("The text has changed. Insert the timestamp again.")
        self.assertEqual(tab.get_text(), text)
        self.wait_for(lambda: tab._active_date_popover is None)

    def test_line_numbers_default_off_and_update_existing_and_new_tabs(self):
        first = self.app.new_tab()
        second = self.app.new_window().new_tab()
        self.assertFalse(first.view.get_show_line_numbers())
        self.assertFalse(second.view.get_show_line_numbers())
        self.addCleanup(self.app.set_show_line_numbers, False)
        settings = Mock()
        with patch.object(self.app, "settings", settings):
            self.app.set_show_line_numbers(True)
            settings.set_boolean.assert_called_once_with("show-line-numbers", True)
        third = self.app.new_tab()
        for tab in (first, second, third):
            self.assertTrue(tab.view.get_show_line_numbers())
        self.app.set_show_line_numbers(False)
        for tab in (first, second, third):
            self.assertFalse(tab.view.get_show_line_numbers())

    def find_switch(self, widget, title):
        if isinstance(widget, Adw.SwitchRow) and widget.get_title() == title:
            return widget
        child = widget.get_first_child()
        while child is not None:
            found = self.find_switch(child, title)
            if found is not None:
                return found
            child = child.get_next_sibling()
        return None

    def test_line_numbers_preference_switch_updates_editor(self):
        tab = self.app.new_tab()
        tab.present()
        self.addCleanup(self.app.set_show_line_numbers, False)
        self.app.show_preferences()
        dialog = tab.window.get_visible_dialog()

        row = self.find_switch(dialog, "Show Line Numbers")
        self.assertIsNotNone(row)
        self.assertFalse(row.get_active())
        row.set_active(True)
        self.assertTrue(tab.view.get_show_line_numbers())
        row.set_active(False)
        self.assertFalse(tab.view.get_show_line_numbers())
        dialog.close()

    def test_editor_and_agenda_settings_are_restored_at_startup(self):
        directory = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "data"))
        source = Gio.SettingsSchemaSource.new_from_directory(
            directory, Gio.SettingsSchemaSource.get_default(), False)
        schema = source.lookup("io.github.kllmanu.Organizer", False)
        settings = Gio.Settings.new_full(schema, Gio.memory_settings_backend_new(), None)
        self.assertFalse(settings.get_boolean("show-line-numbers"))
        self.assertTrue(settings.get_boolean("hide-weekends"))
        settings.set_boolean("show-line-numbers", True)
        settings.set_boolean("hide-weekends", False)
        app = OrganizerApplication()
        app.set_application_id("io.github.kllmanu.Organizer.LineNumberTests")
        app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
        with (patch.object(OrganizerApplication, "_load_settings", return_value=settings),
              patch("vim.data_path", return_value=None)):
            app.register(None)
        self.addCleanup(app.quit)
        tab = app.new_tab()
        self.addCleanup(tab.window.destroy)
        self.assertTrue(app.show_line_numbers)
        self.assertTrue(tab.view.get_show_line_numbers())
        self.assertFalse(app.hide_weekends)
        app.show_agenda(tab.window)
        self.assertIsNotNone(tab.window.agenda.time_grid.get_child_at(6, 0))
        app.set_hide_weekends(True)
        self.assertTrue(settings.get_boolean("hide-weekends"))
        self.assertIsNone(tab.window.agenda.time_grid.get_child_at(5, 0))
        app.set_show_line_numbers(False)
        self.assertFalse(settings.get_boolean("show-line-numbers"))

    def test_reopen_closed_tab_restores_text_and_cursor(self):
        self.app.new_tab()
        tab = self.app.new_tab()
        tab.file = Gio.File.new_for_path("/org/test.org")
        tab.buffer.set_text("* Saved heading")
        tab.buffer.set_modified(False)
        tab.buffer.place_cursor(tab.buffer.get_iter_at_offset(5))
        window = tab.window
        tab.close()
        window.lookup_action("reopen").activate(None)
        restored = window.active_tab
        self.assertEqual(restored.path, "/org/test.org")
        self.assertEqual(restored.get_text(), "* Saved heading")
        self.assertFalse(restored.buffer.get_modified())
        self.assertEqual(restored.buffer.get_iter_at_mark(restored.buffer.get_insert()).get_offset(), 5)

    def test_open_files_and_reuse_existing_tab(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for name in ("first.org", "second.org"):
                path = os.path.join(directory, name)
                with open(path, "w") as stream:
                    stream.write("* " + name)
                paths.append(path)
            first = self.app.open_file(Gio.File.new_for_path(paths[0]))
            second = self.app.open_file(Gio.File.new_for_path(paths[1]))
            self.wait_for(lambda: bool(first.get_text()) and bool(second.get_text()))
            self.assertIs(first.window, second.window)
            self.assertIs(self.app.open_file(Gio.File.new_for_path(paths[0])), first)
            self.assertIs(first.window.active_tab, first)
            self.assertEqual(len(self.app.editor_tabs()), 2)

    def test_close_modified_tab_can_be_cancelled(self):
        tab = self.app.new_tab()
        tab.buffer.set_text("* Unsaved")
        tab.window.tabs.close_page(tab.page)
        dialog = tab.window.get_visible_dialog()
        self.assertIsNotNone(dialog)
        dialog.emit("response", "cancel")
        self.assertIn(tab, self.app.editor_tabs())
        self.assertTrue(tab.buffer.get_modified())

    def test_cancelled_save_releases_pending_close(self):
        tab = self.app.new_tab()
        tab.buffer.set_text("* Unsaved")
        with patch.object(tab, "save", side_effect=lambda then, on_cancel: on_cancel()):
            tab.window.tabs.close_page(tab.page)
            tab.window.get_visible_dialog().emit("response", "save")
        self.assertIn(tab, self.app.editor_tabs())
        tab.force_close()
        self.assertNotIn(tab, self.app.editor_tabs())

    def test_window_close_checks_every_modified_tab(self):
        first = self.app.new_tab()
        second = self.app.new_tab()
        first.buffer.set_text("* First")
        second.buffer.set_text("* Second")
        window = first.window
        window.present()
        window.close()
        window.get_visible_dialog().emit("response", "discard")
        self.assertIn(first, self.app.editor_tabs())
        self.assertIn(second, self.app.editor_tabs())
        self.wait_for(lambda: window.get_visible_dialog() is not None)
        window.get_visible_dialog().emit("response", "cancel")
        self.assertFalse(window._closing)
        self.assertFalse(self.app._quitting)
        self.assertIn(second, self.app.editor_tabs())

    def test_window_close_quits_application(self):
        tab = self.app.new_tab()
        tab.present()
        with patch.object(self.app, "quit") as quit_app:
            tab.window.close()
            quit_app.assert_called_once_with()
        self.assertEqual(self.app.get_windows(), [])

    def test_quit_action_closes_agenda_and_editor(self):
        self.app.new_tab().present()
        self.app.show_agenda()
        with patch.object(self.app, "quit") as quit_app:
            self.app.lookup_action("quit").activate(None)
            quit_app.assert_called_once_with()
        self.assertEqual(self.app.get_windows(), [])

    def test_agenda_replaces_files_without_adding_a_tab(self):
        tab = self.app.new_tab()
        tab.present()
        self.app.show_agenda()
        agenda = self.app.agenda
        self.assertIs(agenda.window, tab.window)
        self.assertEqual(len(self.app.get_windows()), 1)
        self.assertEqual(agenda.window.tabs.get_n_pages(), 1)
        self.assertIs(agenda.window.active_view, agenda)
        self.assertIs(agenda.window.active_tab, tab)
        self.assertEqual(agenda.window.stack.get_visible_child_name(), "agenda")
        self.assertIs(agenda.window.view_switcher.get_stack(), agenda.window.stack)
        self.wait_for(lambda: not tab.get_mapped())
        self.assertFalse(tab.window.tabs.get_parent().get_mapped())
        self.assertEqual(len(agenda.days), 7)
        self.assertEqual(len(self.app.editor_tabs()), 1)
        self.assertIsNone(agenda.window.lookup_action("save"))
        self.app.show_agenda()
        self.assertIs(self.app.agenda, agenda)
        self.assertEqual(agenda.window.tabs.get_n_pages(), 1)
        start = agenda.start
        agenda.shift(1)
        self.assertEqual((agenda.start - start).days, 7)
        agenda.goto_today()
        self.assertEqual(agenda.start, start)
        agenda.close()
        self.assertIs(self.app.agenda, agenda)
        self.assertIs(tab.window.active_tab, tab)
        self.assertIs(tab.window.active_view, tab)
        self.assertEqual(tab.window.stack.get_visible_child_name(), "files")
        self.assertIsNotNone(tab.window.lookup_action("save"))

    def test_agenda_week_controls_are_at_the_bottom_and_navigate(self):
        import datetime as dt
        import org

        self.app.show_agenda()
        agenda = self.app.agenda
        content = agenda.toasts.get_child()
        self.assertIs(content.get_first_child(), agenda.view)
        self.assertIsInstance(agenda.view.get_next_sibling(), Gtk.Separator)
        navigation = content.get_last_child()
        self.assertIs(agenda.week_label.get_parent(), navigation)
        previous = navigation.get_first_child()
        following = previous.get_next_sibling()
        today = following.get_next_sibling()
        self.assertEqual(previous.get_icon_name(), "go-previous-symbolic")
        self.assertEqual(following.get_icon_name(), "go-next-symbolic")
        self.assertIs(today, agenda.today_button)
        self.assertEqual(today.get_child().get_first_child().get_icon_name(),
                         "x-office-calendar-symbolic")
        self.assertEqual(today.get_child().get_last_child().get_label(), "Today")
        self.assertFalse(today.get_sensitive())
        self.assertFalse(today.get_child().get_first_child().is_sensitive())
        self.assertFalse(today.get_child().get_last_child().is_sensitive())
        start = agenda.start
        previous.emit("clicked")
        self.assertEqual(agenda.start, start - dt.timedelta(days=7))
        self.assertTrue(today.get_sensitive())
        following.emit("clicked")
        self.assertEqual(agenda.start, start)
        self.assertFalse(today.get_sensitive())
        following.emit("clicked")
        today.emit("clicked")
        self.assertEqual(agenda.start, org.week_start(dt.date.today()))
        self.assertFalse(today.get_sensitive())

    def test_today_button_is_disabled_in_current_week_even_with_hidden_weekend(self):
        import datetime as dt

        self.app.show_agenda()
        agenda = self.app.agenda

        class Sunday(dt.date):
            @classmethod
            def today(cls):
                return cls(2026, 11, 1)

        with patch("agenda.dt.date", Sunday):
            agenda.start = Sunday(2026, 10, 26)
            agenda.refresh()
            self.assertFalse(agenda.today_button.get_sensitive())
            agenda.shift(1)
            self.assertTrue(agenda.today_button.get_sensitive())
            agenda.goto_today()
            self.assertFalse(agenda.today_button.get_sensitive())

    def test_agenda_messages_use_toasts_without_a_status_bar(self):
        self.app.show_agenda()
        agenda = self.app.agenda
        self.assertIsInstance(agenda.get_child(), Adw.ToastOverlay)
        with patch.object(agenda.toasts, "add_toast") as add_toast:
            agenda.selected_item = None
            agenda.cycle_todo()
            self.assertEqual(add_toast.call_args.args[0].get_title(), "Select an entry first.")
            agenda.edit_planning("SCHEDULED")
            self.assertEqual(add_toast.call_count, 2)
        with (patch.object(self.app, "agenda_entries", return_value=("/missing-org", [], [])),
              patch.object(agenda, "toast") as toast):
            agenda.refresh()
            agenda.refresh()
            toast.assert_called_once_with(
                "The calendar folder /missing-org does not exist. Choose a folder in Preferences.")

    def test_hide_weekends_preference_updates_agendas_and_preserves_entries(self):
        import datetime as dt
        import org

        first = self.app.new_tab()
        second = self.app.new_window().new_tab()
        self.app.show_agenda(first.window)
        self.app.show_agenda(second.window)
        agendas = [first.window.agenda, second.window.agenda]
        start = agendas[0].start
        saturday = start + dt.timedelta(days=5)
        entries = org.parse_entries(
            f"* Weekend event\n<{saturday.isoformat()} 10:00>\n", "/org/tasks.org")
        self.addCleanup(self.app.set_hide_weekends, True)
        with patch.object(self.app, "agenda_entries", return_value=("/org", ["/org/tasks.org"], entries)):
            for agenda in agendas:
                agenda.refresh()
                self.assertTrue(self.app.hide_weekends)
                self.assertIsNotNone(agenda.time_grid.get_child_at(4, 0))
                self.assertIsNone(agenda.time_grid.get_child_at(5, 0))
                self.assertIsNone(agenda.day_headers.get_child_at(5, 0))
                self.assertEqual(len(agenda.days[saturday]), 1)
                self.assertEqual(agenda.entry_buttons, [])
                self.assertTrue(agenda.week_label.get_label().endswith(
                    org.format_date(start + dt.timedelta(days=4), with_year=True)))
            first.present()
            self.app.show_preferences()
            dialog = self.app.get_active_window().get_visible_dialog()
            row = self.find_switch(dialog, "Hide Weekends")
            self.assertIsNotNone(row)
            self.assertTrue(row.get_active())
            settings = Mock()
            with patch.object(self.app, "settings", settings):
                row.set_active(False)
                settings.set_boolean.assert_called_once_with("hide-weekends", False)
            for agenda in agendas:
                self.assertIsNotNone(agenda.time_grid.get_child_at(6, 0))
                self.assertIsNotNone(agenda.day_headers.get_child_at(6, 0))
                self.assertEqual(len(agenda.entry_buttons), 1)
                self.assertEqual(agenda.start, start)
            row.set_active(True)
            for agenda in agendas:
                self.assertIsNone(agenda.time_grid.get_child_at(5, 0))
                self.assertEqual(agenda.entry_buttons, [])
                agenda.shift(1)
                self.assertEqual((agenda.start - start).days, 7)
                self.assertIsNone(agenda.time_grid.get_child_at(5, 0))
            dialog.close()

    def test_view_switcher_preserves_selected_file_and_unsaved_edits(self):
        first = self.app.new_tab()
        second = self.app.new_tab()
        second.buffer.set_text("* Unsaved")
        second.buffer.place_cursor(second.buffer.get_iter_at_offset(5))
        second.present()
        window = second.window
        window.stack.set_visible_child_name("agenda")
        agenda = self.app.agenda
        start = agenda.start
        agenda.shift(1)
        window.stack.set_visible_child_name("files")
        self.assertIs(window.active_view, second)
        self.assertEqual(second.get_text(), "* Unsaved")
        self.assertTrue(second.buffer.get_modified())
        self.assertEqual(second.buffer.get_iter_at_mark(second.buffer.get_insert()).get_offset(), 5)
        self.assertEqual(window.tabs.get_n_pages(), 2)
        window.stack.set_visible_child_name("agenda")
        self.assertIs(self.app.agenda, agenda)
        self.assertEqual((agenda.start - start).days, 7)
        first.present()
        self.assertIs(window.active_view, first)
        self.assertEqual(window.stack.get_visible_child_name(), "files")

    def test_new_tab_from_agenda_switches_to_files(self):
        tab = self.app.new_tab()
        self.app.show_agenda()
        tab.window.lookup_action("new").activate(None)
        self.assertEqual(tab.window.tabs.get_n_pages(), 2)
        self.assertIs(tab.window.active_view, tab.window.active_tab)
        self.assertEqual(tab.window.stack.get_visible_child_name(), "files")

    def test_view_switchers_target_their_own_window(self):
        first = self.app.new_tab()
        second = self.app.new_window().new_tab()
        first.window.stack.set_visible_child_name("agenda")
        first_agenda = first.window.agenda
        second.window.stack.set_visible_child_name("agenda")
        self.assertIs(first.window.active_view, first_agenda)
        self.assertIs(second.window.active_view, second.window.agenda)
        self.assertIsNot(first_agenda, second.window.agenda)
        first.window.stack.set_visible_child_name("files")
        self.assertIs(first.window.active_view, first)
        self.assertIs(second.window.active_view, second.window.agenda)

    def test_agenda_quit_key_returns_to_selected_file(self):
        tab = self.app.new_tab()
        self.app.show_agenda()
        self.assertTrue(tab.window._on_key_pressed(None, Gdk.KEY_q, 0, Gdk.ModifierType(0)))
        self.assertIs(tab.window.active_view, tab)
        self.assertIsNotNone(tab.window.lookup_action("save"))

    def test_files_shortcut_from_agenda_and_editor(self):
        tab = self.app.new_tab()
        tab.buffer.set_text("* Unsaved")
        tab.buffer.place_cursor(tab.buffer.get_iter_at_offset(5))
        self.app.show_agenda()
        window = tab.window
        for _ in range(2):
            self.assertTrue(window._on_key_pressed(
                None, Gdk.KEY_c, 0, Gdk.ModifierType.CONTROL_MASK))
            self.assertTrue(window._on_key_pressed(None, Gdk.KEY_f, 0, Gdk.ModifierType(0)))
            self.assertIs(window.active_view, tab)
            self.assertEqual(window.stack.get_visible_child_name(), "files")
            self.assertEqual(tab.get_text(), "* Unsaved")
            self.assertTrue(tab.buffer.get_modified())
            self.assertEqual(
                tab.buffer.get_iter_at_mark(tab.buffer.get_insert()).get_offset(), 5)

    def test_calendar_shortcut_from_editor_and_calendar(self):
        tab = self.app.new_tab()
        tab.present()
        window = tab.window
        for _ in range(2):
            self.assertTrue(window._on_key_pressed(
                None, Gdk.KEY_c, 0, Gdk.ModifierType.CONTROL_MASK))
            self.assertTrue(window._on_key_pressed(None, Gdk.KEY_c, 0, Gdk.ModifierType(0)))
            self.assertIs(window.active_view, window.agenda)
            self.assertEqual(window.get_title(), "Calendar – Organizer")
            self.assertIn("C-c a", window.active_view._org_key_sequences.commands)
        self.assertIn("C-c a", tab._org_key_sequences.commands)
        self.assertIn("C-c C-c", tab._org_key_sequences.commands)

    def test_files_shortcut_without_open_files_creates_editor(self):
        self.app.show_agenda()
        window = self.app.agenda.window
        self.assertTrue(window._on_key_pressed(
            None, Gdk.KEY_c, 0, Gdk.ModifierType.CONTROL_MASK))
        self.assertTrue(window._on_key_pressed(None, Gdk.KEY_f, 0, Gdk.ModifierType(0)))
        self.assertEqual(window.tabs.get_n_pages(), 1)
        self.assertIs(window.active_view, window.active_tab)

    def test_closing_last_file_from_agenda_keeps_agenda_open(self):
        tab = self.app.new_tab()
        self.app.show_agenda()
        window = tab.window
        tab.close()
        self.assertIn(window, self.app.get_windows())
        self.assertEqual(window.tabs.get_n_pages(), 0)
        self.assertIs(window.active_view, window.agenda)
        window.stack.set_visible_child_name("files")
        self.assertEqual(window.tabs.get_n_pages(), 1)

    def test_agenda_without_files_can_switch_to_a_new_editor(self):
        self.app.show_agenda()
        window = self.app.agenda.window
        self.assertEqual(window.tabs.get_n_pages(), 0)
        window.stack.set_visible_child_name("files")
        self.assertEqual(window.tabs.get_n_pages(), 1)
        self.assertIs(window.active_view, window.active_tab)
        self.assertIsNotNone(window.lookup_action("save"))

    def test_native_view_switcher_has_named_views_and_responsive_bar(self):
        tab = self.app.new_tab()
        window = tab.window
        self.assertIsInstance(window.stack, Adw.ViewStack)
        self.assertIsInstance(window.view_switcher, Adw.ViewSwitcherTitle)
        self.assertIs(window._header.get_title_widget(), window.view_switcher)
        self.assertIs(window.switcher_bar.get_stack(), window.stack)
        pages = window.stack.get_pages()
        self.assertEqual(pages.get_n_items(), 3)
        self.assertEqual([pages.get_item(i).get_title() for i in range(3)],
                         ["Files", "Agenda", "Calendar"])
        self.assertTrue(all(pages.get_item(i).get_icon_name() for i in range(3)))
        tab.present()
        window.view_switcher.set_view_switcher_enabled(False)
        self.wait_for(window.view_switcher.get_title_visible)
        self.assertTrue(window.switcher_bar.get_reveal())
        window.stack.set_visible_child_name("agenda")
        self.assertIs(window._header.get_title_widget(), window.view_switcher)
        self.assertEqual(window.view_switcher.get_title(), "Calendar")
        window.stack.set_visible_child_name("files")
        tab.buffer.set_text("* Unsaved")
        self.assertEqual(window.view_switcher.get_title(), tab.title_widget.get_title())

    def test_buffer_picker_in_agenda_uses_visible_header_and_restores_focus(self):
        tab = self.app.new_tab()
        tab.present()
        self.app.show_agenda()
        agenda = self.app.agenda
        picker = BufferPicker(self.app.editor_tabs(), tab)
        picker.present(tab.window)
        self.assertIs(picker.get_parent(), agenda.open_button)
        picker.close()
        self.wait_for(lambda: picker.get_parent() is None)
        self.assertIs(tab.window.get_focus(), agenda.view)

    def test_calendar_event_duration_and_overlap_lanes(self):
        import datetime as dt
        import org

        day = dt.date.today()
        text = (f"* First\nSCHEDULED: <{day.isoformat()} 09:00>\n"
                f"* Second\nSCHEDULED: <{day.isoformat()} 09:15-10:00>\n"
                f"* Third\nSCHEDULED: <{day.isoformat()} 11:00>\n")
        items = org.build_agenda(org.parse_entries(text), day, 1, day)[day]
        self.assertEqual(event_minutes(items[0]), (540, 600))
        self.assertEqual(event_minutes(items[1]), (555, 600))
        self.assertEqual(event_lanes(items), {0: (0, 2), 1: (1, 2), 2: (0, 1)})

    def test_agenda_entries_open_source_and_follow_unsaved_edits(self):
        import datetime as dt
        import org

        self.app.set_hide_weekends(False)
        self.addCleanup(self.app.set_hide_weekends, True)
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "tasks.org")
            today = dt.date.today()
            text = (f"* TODO Dated task\n<{today.isoformat()} 10:00>\n"
                    "* TODO Undated task\n")
            with open(path, "w") as stream:
                stream.write(text)
            tab = self.app.open_file(Gio.File.new_for_path(path))
            self.wait_for(lambda: bool(tab.get_text()))
            with patch.object(self.app, "agenda_directory", return_value=directory):
                self.app.show_agenda()
                agenda = self.app.agenda
                self.assertEqual(len(agenda.entry_buttons), 1)
                item, button = agenda.entry_buttons[0]
                self.assertEqual(item.entry.display_title, "Dated task")
                button.emit("clicked")
                self.assertIs(tab.window.active_tab, tab)
                self.assertEqual(len(self.app.editor_tabs()), 1)
                self.assertEqual(tab.buffer.get_iter_at_mark(tab.buffer.get_insert()).get_line(), 1)
                agenda.present()
                agenda_title = tab.window.get_title()
                agenda._move(1)
                agenda.cycle_todo()
                self.assertEqual(tab.window.get_title(), agenda_title)
                self.assertTrue(tab.buffer.get_modified())
                self.assertTrue(org.parse_entries(tab.get_text())[0].is_done)
                self.assertTrue(agenda.entry_buttons[0][0].entry.is_done)
                self.assertEqual(len(agenda.days), 7)
                agenda._move(1)
                agenda.edit_planning("SCHEDULED")
                self.assertIsNotNone(agenda._active_date_popover)
                text_before = tab.get_text()
                agenda._keyboard_quit()
                self.wait_for(lambda: agenda._active_date_popover is None)
                self.assertEqual(tab.get_text(), text_before)
                with patch.object(self.app, "save_session") as save_session:
                    with patch.object(self.app, "quit"):
                        tab.buffer.set_modified(False)
                        self.app._on_quit()
                    save_session.assert_called_once_with()

    def test_calendar_shows_only_standalone_active_timestamps(self):
        import datetime as dt
        import org

        self.app.show_agenda()
        agenda = self.app.agenda
        day = agenda.start
        overdue = day - dt.timedelta(days=7)
        upcoming = dt.date.today() + dt.timedelta(days=2)
        text = (
            f"* TODO Other stuff SCHEDULED: <{day.isoformat()}>\n"
            f"* TODO Inline deadline DEADLINE: <{day.isoformat()}>\n"
            f"* TODO Separated planning\n\nSCHEDULED: <{day.isoformat()}>\n"
            f"* TODO Scheduled\nSCHEDULED: <{day.isoformat()} 09:00>\n"
            f"* TODO Deadline\nDEADLINE: <{day.isoformat()} 10:00>\n"
            f"* TODO Overdue\nSCHEDULED: <{overdue.isoformat()}>\n"
            f"DEADLINE: <{overdue.isoformat()}>\n"
            f"* TODO Upcoming\nDEADLINE: <{upcoming.isoformat()}>\n"
            f"* Inactive\n[{day.isoformat()}]\n"
            f"* All day\n<{day.isoformat()}>\n"
            f"* Repeating\n<{day.isoformat()} 11:00 +1w>\n"
            f"* TODO Mixed\nSCHEDULED: <{day.isoformat()} 08:00>\n"
            f"DEADLINE: <{day.isoformat()}>\n<{day.isoformat()} 12:00>\n")
        entries = org.parse_entries(text, "/org/events.org")
        with patch.object(self.app, "agenda_entries",
                          return_value=("/org", ["/org/events.org"], entries)):
            agenda.refresh()
            self.assertEqual(
                {item.entry.display_title for item, _ in agenda.entry_buttons},
                {"All day", "Repeating", "Mixed"})
            self.assertTrue(all(item.kind == "timestamp"
                                for items in agenda.days.values() for item in items))
            agenda.shift(1)
            self.assertEqual(
                [item.entry.display_title for item, _ in agenda.entry_buttons], ["Repeating"])

    def test_calendar_labels_timestamps_with_adjacent_text_and_opens_source_line(self):
        import org

        self.app.show_agenda()
        agenda = self.app.agenda
        date = agenda.start.isoformat()
        text = (f"* TODO Other stuff\nSCHEDULED: <{date}>\n\n"
                f"<{date}> Asdf\n"
                f"Another event <{date} 10:00>\n"
                f"<{date} 11:00>\n")
        entries = org.parse_entries(text, "/org/events.org")
        with patch.object(self.app, "agenda_entries",
                          return_value=("/org", ["/org/events.org"], entries)):
            agenda.refresh()
            labels = {button.get_child().get_last_child().get_label(): (item, button)
                      for item, button in agenda.entry_buttons}
            self.assertEqual(set(labels), {"Asdf", "Another event", "TODO Other stuff"})
            timed_item, timed_button = labels["Another event"]
            self.assertEqual(event_minutes(timed_item), (600, 660))
            self.assertEqual(timed_button.get_size_request()[1], 59)
            item, button = labels["Asdf"]
            self.assertEqual(item.timestamp_line, 3)
            with patch.object(self.app, "open_location") as open_location:
                button.emit("clicked")
                open_location.assert_called_once_with("/org/events.org", 3)
                open_location.reset_mock()
                agenda.selected_item = item
                agenda.open_entry()
                open_location.assert_called_once_with("/org/events.org", 3)

    def test_weekly_agenda_includes_all_seven_days_and_no_global_todos(self):
        import datetime as dt
        import org

        tab = self.app.new_tab()
        tab.present()
        self.assertTrue(tab.window._on_key_pressed(
            None, Gdk.KEY_c, 0, Gdk.ModifierType.CONTROL_MASK))
        self.assertTrue(tab.window._on_key_pressed(None, Gdk.KEY_a, 0, Gdk.ModifierType(0)))
        agenda = tab.window.weekly_agenda
        self.assertIs(tab.window.active_view, agenda)
        self.assertEqual(tab.window.get_title(), "Agenda – Organizer")
        start = agenda.start
        saturday = start + dt.timedelta(days=5)
        text = (f"* TODO Scheduled task\nSCHEDULED: <{start.isoformat()} 09:00>\n"
                f"* TODO Deadline task\nDEADLINE: <{start.isoformat()}>\n"
                f"* Weekend event\n<{saturday.isoformat()} 12:00>\n"
                "* TODO Undated task\n")
        entries = org.parse_entries(text, "/org/test.org")
        with patch.object(self.app, "agenda_entries",
                          return_value=("/org", ["/org/test.org"], entries)):
            agenda.refresh()
            self.assertEqual(sum(line.kind == "date" for line in agenda.lines), 7)
            output = "\n".join(line.text for line in agenda.lines)
            self.assertIn("Scheduled:", output)
            self.assertIn("Deadline:", output)
            self.assertIn("Weekend event", output)
            self.assertNotIn("Undated task", output)
            self.assertNotIn("Global list", output)
            self.assertFalse(any(line.kind == "separator" for line in agenda.lines))
            self.assertEqual(tab.window.tabs.get_n_pages(), 1)
            item, button = next((item, button) for item, button in agenda.entry_buttons
                                if item.entry.display_title == "Scheduled task")
            with patch.object(self.app, "open_location") as open_location:
                button.emit("clicked")
                open_location.assert_called_once_with("/org/test.org", 0)
            agenda.selected_item = item
            agenda.shift(1)
            self.assertEqual((agenda.start - start).days, 7)
            agenda.goto_today()
            self.assertEqual(agenda.start, start)
        agenda.close()
        self.assertIs(tab.window.active_view, tab)

    def test_weekly_agenda_refreshes_unsaved_edits_without_opening_calendar(self):
        import datetime as dt

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "tasks.org")
            text = f"* TODO First\nSCHEDULED: <{dt.date.today().isoformat()} 09:00>\n"
            with open(path, "w") as stream:
                stream.write(text)
            tab = self.app.open_file(Gio.File.new_for_path(path))
            self.wait_for(lambda: bool(tab.get_text()))
            with patch.object(self.app, "agenda_directory", return_value=directory):
                self.app.show_weekly_agenda(tab.window)
                agenda = tab.window.weekly_agenda
                self.assertIsNone(tab.window.agenda)
                self.assertEqual(len(agenda.entry_buttons), 1)
                tab.buffer.set_text(text.replace("First", "Changed"))
                self.wait_for(lambda: any(
                    item.entry.display_title == "Changed" for item, _ in agenda.entry_buttons))
                agenda._move(1)
                agenda.cycle_todo()
                self.assertIn("* DONE Changed", tab.get_text())
                self.assertTrue(tab.buffer.get_modified())
                self.assertIs(tab.window.active_view, agenda)
                agenda._move(1)
                agenda.edit_planning("DEADLINE")
                self.assertIsNotNone(agenda._active_date_popover)
                agenda._keyboard_quit()
                self.wait_for(lambda: agenda._active_date_popover is None)

    def test_agenda_cursor_navigates_and_keeps_selection_when_cycling_todo(self):
        import datetime as dt

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "tasks.org")
            date = dt.date.today().isoformat()
            with open(path, "w") as stream:
                stream.write(f"* TODO First\n<{date} 09:00>\n"
                             f"* TODO Second\n<{date} 10:00>\n")
            tab = self.app.open_file(Gio.File.new_for_path(path))
            self.wait_for(lambda: bool(tab.get_text()))
            with patch.object(self.app, "agenda_directory", return_value=directory):
                self.app.show_weekly_agenda(tab.window)
                agenda = tab.window.weekly_agenda
                self.assertEqual(agenda.selected_item.entry.display_title, "First")
                self.assertTrue(agenda.entry_buttons[0][1].has_css_class("agenda-cursor"))
                for key in (Gdk.KEY_Down, Gdk.KEY_t, Gdk.KEY_t):
                    self.assertTrue(tab.window._on_key_pressed(
                        None, key, 0, Gdk.ModifierType(0)))
                    self.assertEqual(agenda.selected_item.entry.display_title, "Second")
                    self.assertTrue(agenda.entry_buttons[1][1].has_css_class("agenda-cursor"))
                    self.assertFalse(agenda.entry_buttons[0][1].has_css_class("agenda-cursor"))
                self.assertNotIn("* TODO Second", tab.get_text())
                self.assertTrue(tab.buffer.get_modified())
                self.assertTrue(tab.window._on_key_pressed(
                    None, Gdk.KEY_Up, 0, Gdk.ModifierType(0)))
                self.assertEqual(agenda.selected_item.entry.display_title, "First")
                agenda.refresh()
                self.assertEqual(agenda.selected_item.entry.display_title, "First")
                self.assertTrue(agenda.entry_buttons[0][1].has_css_class("agenda-cursor"))

    def test_weekly_agenda_colors_keywords_and_uses_compact_rows(self):
        import datetime as dt
        import org
        from gi.repository import Pango

        self.app.show_weekly_agenda()
        window = self.app.editor_windows()[0]
        agenda = window.weekly_agenda
        date = dt.date.today().isoformat()
        entries = org.parse_entries(
            f"* TODO Café <{date}>\n* DONE Résumé <{date}>\n", "/org/test.org")
        manager = Adw.StyleManager.get_default()
        original_scheme = manager.get_color_scheme()
        self.addCleanup(manager.set_color_scheme, original_scheme)
        with patch.object(self.app, "agenda_entries",
                          return_value=("/org", ["/org/test.org"], entries)):
            for mode in (Adw.ColorScheme.FORCE_LIGHT, Adw.ColorScheme.FORCE_DARK):
                manager.set_color_scheme(mode)
                agenda.refresh()
                colors = {}
                for item, button in agenda.entry_buttons:
                    label = button.get_child()
                    self.assertEqual(
                        label.get_pango_context().get_font_description().get_weight(),
                        Pango.Weight.NORMAL)
                    self.assertLessEqual(label.get_margin_top() + label.get_margin_bottom(), 2)
                    attributes = label.get_attributes()
                    iterator = attributes.get_iterator()
                    index = len(label.get_text().split(item.entry.keyword)[0].encode("utf-8"))
                    while iterator.range()[1] <= index:
                        self.assertTrue(iterator.next())
                    foreground = iterator.get(Pango.AttrType.FOREGROUND)
                    color = foreground.as_color().color
                    colors[item.entry.keyword] = (color.red, color.green, color.blue)
                    if item.entry.keyword == "TODO":
                        self.assertGreater(color.red, color.green)
                    else:
                        self.assertGreater(color.green, color.red)
                    weight_attribute = iterator.get(Pango.AttrType.WEIGHT)
                    weight = weight_attribute.as_int().value
                    self.assertEqual(weight, Pango.Weight.BOLD)
                self.assertNotEqual(colors["TODO"], colors["DONE"])

    def test_weekly_agenda_font_size_matches_editor_at_shared_zoom_levels(self):
        import datetime as dt
        import org

        tab = self.app.new_tab()
        self.app.show_weekly_agenda(tab.window)
        agenda = tab.window.weekly_agenda
        entries = org.parse_entries(f"* Event <{dt.date.today().isoformat()}>\n")
        display = Gdk.Display.get_default()
        Gtk.StyleContext.remove_provider_for_display(display, self.app.zoom_css)
        Gtk.StyleContext.add_provider_for_display(
            display, self.app.zoom_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        original_zoom = self.app.zoom
        self.addCleanup(self.app.set_zoom, original_zoom, False)
        with patch.object(self.app, "agenda_entries", return_value=("/org", ["test.org"], entries)):
            agenda.refresh()
            label = agenda.entry_buttons[0][1].get_child()
            heading = agenda.listbox.get_first_child().get_child()
            sizes = []
            for zoom in (100, 150, 70):
                tab.present()
                self.wait_for(tab.view.get_mapped)
                self.app.set_zoom(zoom, notify=False)
                if sizes:
                    self.wait_for(lambda: tab.view.get_pango_context().get_font_description().get_size()
                                  != sizes[-1])
                editor_size = tab.view.get_pango_context().get_font_description().get_size()
                agenda.present()
                label = agenda.entry_buttons[0][1].get_child()
                heading = agenda.listbox.get_first_child().get_child()
                if sizes:
                    self.wait_for(lambda: label.get_pango_context().get_font_description().get_size()
                                  != sizes[-1])
                self.wait_for(lambda: label.get_pango_context().get_font_description().get_size()
                              == editor_size)
                self.assertEqual(heading.get_pango_context().get_font_description().get_size(),
                                 editor_size)
                sizes.append(editor_size)
            self.assertGreater(sizes[1], sizes[0])
            self.assertLess(sizes[2], sizes[0])

    def test_zoom_notification_targets_active_view_and_reuses_toast(self):
        tab = self.app.new_tab()
        tab.present()
        self.app.show_weekly_agenda(tab.window)
        agenda = tab.window.weekly_agenda
        self.wait_for(lambda: self.app.get_active_window() is tab.window)
        original_zoom = self.app.zoom
        self.addCleanup(self.app.set_zoom, original_zoom, False)
        self.app.set_zoom(100, notify=False)
        self.app.set_zoom(110)
        toast = agenda._zoom_toast
        self.assertIsNotNone(toast)
        self.assertEqual(toast.get_title(), "Zoom 110%")
        self.app.set_zoom(120)
        self.assertIs(agenda._zoom_toast, toast)
        self.assertEqual(toast.get_title(), "Zoom 120%")
        self.assertIsNone(tab._zoom_toast)
        toast.dismiss()
        self.assertIsNone(agenda._zoom_toast)
        self.app.set_zoom(130, notify=False)
        self.assertIsNone(agenda._zoom_toast)
        tab.present()
        self.app.set_zoom(140)
        self.assertEqual(tab._zoom_toast.get_title(), "Zoom 140%")
        self.assertIsNone(agenda._zoom_toast)

    def test_session_restores_file_tabs_in_order_without_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            files = []
            for name in ("first.org", "second.org"):
                path = os.path.join(directory, name)
                with open(path, "w") as stream:
                    stream.write("* " + name)
                files.append(Gio.File.new_for_path(path))
            for file in files:
                self.app.open_file(file)
            self.wait_for(lambda: all(tab.get_text() for tab in self.app.editor_tabs()))
            self.app.new_tab()
            settings = Mock()
            settings.get_int.side_effect = lambda key: {"window-width": 960, "window-height": 680}[key]
            settings.get_boolean.return_value = False
            uris = [file.get_uri() for file in files]
            settings.get_strv.return_value = uris
            with patch.object(self.app, "settings", settings):
                with patch.object(self.app, "quit"):
                    self.app._on_quit()
                settings.set_strv.assert_called_once_with("session-files", uris)
                self.app._session_restored = False
                self.app.do_open([files[0]], 1, "")
                self.app.restore_session()
                self.assertEqual(len(self.app.editor_tabs()), 2)
                self.wait_for(lambda: all(tab.get_text() for tab in self.app.editor_tabs()))
                self.assertEqual([tab.file.get_uri() for tab in self.app.editor_tabs()], uris)

    def test_cancelled_quit_does_not_save_session(self):
        tab = self.app.new_tab()
        tab.buffer.set_text("* Unsaved")
        settings = Mock()
        with patch.object(self.app, "settings", settings):
            self.app._on_quit()
            tab.window.get_visible_dialog().emit("response", "cancel")
            settings.set_strv.assert_not_called()

    def test_control_scroll_zooms_and_respects_limits(self):
        controller = Mock()
        controller.get_current_event_state.return_value = Gdk.ModifierType.CONTROL_MASK
        self.app.set_zoom(100, notify=False)
        self.assertTrue(self.app._on_zoom_scroll(controller, 0, -1))
        self.assertEqual(self.app.zoom, 110)
        self.assertTrue(self.app._on_zoom_scroll(controller, 0, 1))
        self.assertEqual(self.app.zoom, 100)
        for limit, delta in ((ZOOM_MAX, -1), (ZOOM_MIN, 1)):
            self.app.set_zoom(limit, notify=False)
            self.assertTrue(self.app._on_zoom_scroll(controller, 0, delta))
            self.assertEqual(self.app.zoom, limit)
        self.app.set_zoom(100, notify=False)
        controller.get_current_event_state.return_value = Gdk.ModifierType(0)
        self.assertFalse(self.app._on_zoom_scroll(controller, 0, -1))
        self.assertEqual(self.app.zoom, 100)

    def test_buffer_completion_matches_terms_in_any_order(self):
        self.assertTrue(matches_buffer("WORK test", "test.org", "/org/work/test.org"))
        self.assertTrue(matches_buffer("test work", "test.org", "/org/work/test.org"))
        self.assertFalse(matches_buffer("work absent", "test.org", "/org/work/test.org"))

    def test_buffer_shortcut_after_closing_agenda(self):
        tab = self.app.new_tab()
        tab.present()
        self.app.show_agenda()
        self.app.agenda.close()
        self.assertIsNotNone(self.app.agenda)
        self.assertIs(tab.window.get_focus(), tab.view)
        tab.open_button.grab_focus()
        with patch.object(tab._org_key_sequences, "commands",
                          {**tab._org_key_sequences.commands, "C-x b": Mock()}) as commands:
            self.assertTrue(tab.window._on_key_pressed(
                None, Gdk.KEY_x, 0, Gdk.ModifierType.CONTROL_MASK))
            self.assertTrue(tab.window._on_key_pressed(
                None, Gdk.KEY_b, 0, Gdk.ModifierType(0)))
            commands["C-x b"].assert_called_once_with()

    def test_recent_history_limit_order_and_persistence(self):
        settings = Mock()
        with patch.object(self.app, "settings", settings):
            for i in range(25):
                self.app.remember_file(Gio.File.new_for_path(f"/org/{i}.org"))
            file = Gio.File.new_for_path("/org/10.org")
            self.app.remember_file(file)
            self.assertEqual(len(self.app.recent_files), 20)
            self.assertEqual(self.app.recent_files[0], file.get_uri())
            self.assertEqual(len(set(self.app.recent_files)), 20)
            settings.set_strv.assert_called_with("recent-files", self.app.recent_files)

    def test_picker_recent_files_filter_and_open(self):
        tab = self.app.new_tab()
        tab.file = Gio.File.new_for_path("/org/open.org")
        recent = Gio.File.new_for_path("/org/work/recent.org")
        picker = BufferPicker([tab], tab, [tab.file, recent])
        tab.present()
        picker.present(tab.window)
        self.assertEqual(picker.results, [tab, recent])
        picker.search.set_text("work recent")
        self.assertEqual(picker.results, [recent])
        with patch.object(self.app, "open_file") as open_file:
            picker._activate_selected()
            open_file.assert_called_once_with(recent)

    def test_buffer_picker_switch_and_cancel(self):
        first = self.app.new_tab()
        first.file = Gio.File.new_for_path("/org/work/test.org")
        second = self.app.new_tab()
        second.file = Gio.File.new_for_path("/org/home/notes.org")
        second.present()
        picker = BufferPicker(self.app.editor_tabs(), second)
        picker.present(second.window)
        self.assertEqual(picker.listbox.get_selected_row().get_index(), 0)
        picker._on_key_pressed(None, Gdk.KEY_Down, 0, Gdk.ModifierType(0))
        self.assertEqual(picker.listbox.get_selected_row().get_index(), 1)
        picker._on_key_pressed(None, Gdk.KEY_Up, 0, Gdk.ModifierType(0))
        self.assertEqual(picker.listbox.get_selected_row().get_index(), 0)
        picker._on_key_pressed(None, Gdk.KEY_n, 0, Gdk.ModifierType.CONTROL_MASK)
        self.assertEqual(picker.listbox.get_selected_row().get_index(), 1)
        picker._on_key_pressed(None, Gdk.KEY_p, 0, Gdk.ModifierType.CONTROL_MASK)
        self.assertEqual(picker.listbox.get_selected_row().get_index(), 0)
        picker.search.set_text("missing")
        self.assertEqual(picker.results, [])
        self.assertTrue(picker.empty.get_visible())
        picker._on_key_pressed(None, Gdk.KEY_Return, 0, Gdk.ModifierType(0))
        self.assertIs(second.window.active_tab, second)
        picker.search.set_text("test work")
        self.assertEqual(picker.results, [first])
        picker._on_key_pressed(None, Gdk.KEY_Return, 0, Gdk.ModifierType(0))
        self.assertIs(first.window.active_tab, first)
        picker = BufferPicker(self.app.editor_tabs(), first)
        picker.present(first.window)
        picker._on_key_pressed(None, Gdk.KEY_g, 0, Gdk.ModifierType.CONTROL_MASK)
        self.assertIs(first.window.active_tab, first)
        self.wait_for(lambda: picker.get_parent() is None)
        self.assertIs(first.window.get_focus(), first.view)
