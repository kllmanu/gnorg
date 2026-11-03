"""Preferences integration tests."""

import os
from unittest.mock import patch
from unittest.mock import Mock

from editor_test_case import EditorTestCase
from vim import Gio
from vim import Gtk
from vim import GnorgApplication, APP_ID, LEGACY_APP_ID, GLib


class PreferenceTests(EditorTestCase):
    def test_legacy_settings_migrate_once_without_overwriting_gnorg(self):
        directory = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "data"))
        source = Gio.SettingsSchemaSource.new_from_directory(
            directory, Gio.SettingsSchemaSource.get_default(), False)
        backend = Gio.memory_settings_backend_new()
        settings = Gio.Settings.new_full(source.lookup(APP_ID, False), backend, None)
        legacy = Gio.Settings.new_full(source.lookup(LEGACY_APP_ID, False), backend, None)
        legacy.set_int("editor-padding", 28)
        legacy.set_int("agenda-padding", 32)
        legacy.set_strv("session-files", ["file:///tmp/tasks.org"])
        legacy.set_value("session-cursor-positions",
                         GLib.Variant("a{s(uu)}", {"file:///tmp/tasks.org": (12, 3)}))
        settings.set_int("editor-padding", 20)
        GnorgApplication._migrate_settings(settings, legacy)
        self.assertEqual(settings.get_int("editor-padding"), 20)
        self.assertEqual(settings.get_int("agenda-padding"), 32)
        self.assertEqual(settings.get_strv("session-files"), ["file:///tmp/tasks.org"])
        self.assertEqual(settings.get_value("session-cursor-positions").unpack(),
                         {"file:///tmp/tasks.org": (12, 3)})
        self.assertIsNone(settings.get_user_value("zoom"))
        self.assertTrue(settings.get_boolean("legacy-settings-migrated"))
        settings.reset("agenda-padding")
        legacy.set_int("agenda-padding", 40)
        GnorgApplication._migrate_settings(settings, legacy)
        self.assertEqual(settings.get_int("agenda-padding"), 16)
        self.assertEqual(legacy.get_int("editor-padding"), 28)

    def test_settings_loader_migrates_with_the_bundled_schema(self):
        directory = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "data"))
        backend = Gio.memory_settings_backend_new()
        new_full = Gio.Settings.new_full
        with (patch("vim.Gio.SettingsSchemaSource.get_default", return_value=None),
              patch("vim.data_path", return_value=os.path.join(directory, "gschemas.compiled")),
              patch("vim.Gio.Settings.new_full",
                    side_effect=lambda schema, _backend, path: new_full(schema, backend, path))):
            settings = GnorgApplication._load_settings()
        self.assertEqual(settings.props.settings_schema.get_id(), APP_ID)
        self.assertTrue(settings.get_boolean("legacy-settings-migrated"))

    def test_editor_has_padding_on_all_sides(self):
        tab = self.app.new_tab()
        self.assertEqual((tab.view.get_top_margin(), tab.view.get_bottom_margin(),
                          tab.view.get_left_margin(), tab.view.get_right_margin()),
                         (16, 16, 16, 16))

    def test_padding_slider_updates_open_and_new_editors_and_persists(self):
        tab = self.app.new_tab()
        tab.present()
        self.app.show_preferences()
        dialog = tab.window.get_visible_dialog()

        def find_scale(widget):
            if isinstance(widget, Gtk.Scale):
                return widget
            child = widget.get_first_child()
            while child is not None:
                result = find_scale(child)
                if result is not None:
                    return result
                child = child.get_next_sibling()
            return None

        scale = find_scale(dialog)
        self.assertIsNotNone(scale)
        self.assertEqual(scale.get_value(), 16)
        with patch.object(self.app, "settings", Mock()) as settings:
            scale.set_value(28)
            settings.set_int.assert_called_once_with("editor-padding", 28)
        dialog.close()
        other = self.app.new_tab()
        for editor in (tab, other):
            self.assertEqual((editor.view.get_top_margin(), editor.view.get_bottom_margin(),
                              editor.view.get_left_margin(), editor.view.get_right_margin()),
                             (28, 28, 28, 28))
        self.app.set_editor_padding(0)
        self.assertEqual(tab.view.get_left_margin(), 0)

    def test_agenda_padding_updates_existing_and_new_views(self):
        tab = self.app.new_tab()
        self.app.show_weekly_agenda(tab.window)
        agenda = tab.window.weekly_agenda
        self.assertEqual(agenda.listbox.get_margin_top(), 16)
        with patch.object(self.app, "settings", Mock()) as settings:
            self.app.set_agenda_padding(32)
            settings.set_int.assert_called_once_with("agenda-padding", 32)
        for margin in (agenda.listbox.get_margin_top(), agenda.listbox.get_margin_bottom(),
                       agenda.listbox.get_margin_start(), agenda.listbox.get_margin_end()):
            self.assertEqual(margin, 32)
        self.assertEqual(tab.view.get_left_margin(), 16)
        self.app.set_agenda_padding(0)
        self.assertEqual(agenda.listbox.get_margin_start(), 0)

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
        schema = source.lookup(APP_ID, False)
        settings = Gio.Settings.new_full(schema, Gio.memory_settings_backend_new(), None)
        self.assertFalse(settings.get_boolean("show-line-numbers"))
        self.assertTrue(settings.get_boolean("hide-weekends"))
        self.assertTrue(settings.get_boolean("auto-revert"))
        self.assertTrue(settings.get_boolean("jj-escape"))
        self.assertEqual(settings.get_int("jj-escape-delay"), 200)
        self.assertEqual(settings.get_string("escape-sequence"), "jj")
        settings.set_boolean("show-line-numbers", True)
        settings.set_boolean("hide-weekends", False)
        settings.set_boolean("auto-revert", False)
        settings.set_boolean("jj-escape", False)
        settings.set_int("jj-escape-delay", 300)
        settings.set_string("escape-sequence", "jk")
        app = GnorgApplication()
        app.set_application_id("io.github.kllmanu.Gnorg.LineNumberTests")
        app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
        with (patch.object(GnorgApplication, "_load_settings", return_value=settings),
              patch("vim.data_path", return_value=None)):
            app.register(None)
        self.addCleanup(app.quit)
        tab = app.new_tab()
        self.addCleanup(tab.window.destroy)
        self.assertTrue(app.show_line_numbers)
        self.assertTrue(tab.view.get_show_line_numbers())
        self.assertFalse(app.hide_weekends)
        self.assertFalse(app.auto_revert)
        self.assertFalse(app.jj_escape)
        self.assertEqual(app.jj_escape_delay, 300)
        self.assertEqual(app.escape_sequence, "jk")
        app.show_agenda(tab.window)
        self.assertIsNotNone(tab.window.agenda.time_grid.get_child_at(6, 0))
        app.set_hide_weekends(True)
        self.assertTrue(settings.get_boolean("hide-weekends"))
        self.assertIsNone(tab.window.agenda.time_grid.get_child_at(5, 0))
        app.set_show_line_numbers(False)
        self.assertFalse(settings.get_boolean("show-line-numbers"))

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
                self.assertEqual(agenda.week_label.get_label(),
                                 f"{org.MONTH_NAMES[start.month - 1]} {start.year}")
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
