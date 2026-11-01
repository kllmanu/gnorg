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
                "The agenda folder /missing-org does not exist. Choose a folder in Preferences.")

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
        self.assertEqual(pages.get_n_items(), 2)
        self.assertEqual([pages.get_item(i).get_title() for i in range(2)], ["Files", "Agenda"])
        self.assertTrue(all(pages.get_item(i).get_icon_name() for i in range(2)))
        tab.present()
        window.view_switcher.set_view_switcher_enabled(False)
        self.wait_for(window.view_switcher.get_title_visible)
        self.assertTrue(window.switcher_bar.get_reveal())
        window.stack.set_visible_child_name("agenda")
        self.assertIs(window._header.get_title_widget(), window.view_switcher)
        self.assertEqual(window.view_switcher.get_title(), "Agenda")
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
        self.assertEqual(event_minutes(items[0]), (540, 570))
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
            text = (f"* TODO Dated task\nSCHEDULED: <{today.isoformat()} 10:00>\n"
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
                self.assertEqual(tab.buffer.get_iter_at_mark(tab.buffer.get_insert()).get_line(), 0)
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
