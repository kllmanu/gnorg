"""Utilitypane integration tests."""

import os
import tempfile
from unittest.mock import patch
from unittest.mock import Mock

from editor_test_case import EditorTestCase
from vim import Gio, GLib
from vim import Gdk, Gtk
from vim import OrganizerApplication
from vim import Adw


class UtilityPaneTests(EditorTestCase):
    def test_utility_pane_replaces_tab_bar_and_selects_documents(self):
        first = self.app.new_tab()
        first.buffer.set_text("* First")
        second = self.app.new_tab()
        window = first.window
        pane = window.utility_pane
        self.assertIs(window.split_view.get_sidebar(), pane)
        self.assertIs(window.stack.get_child_by_name("files"), window.tabs)
        pane.refresh()
        row = next(row for row in pane.rows if row.document is first)
        self.assertEqual(row.get_title(), "• Untitled")
        self.assertIsNone(row.visibility)
        self.app.show_agenda(window)
        pane.listbox.emit("row-activated", row)
        self.assertIs(window.active_tab, first)
        self.assertEqual(window.stack.get_visible_child_name(), "files")
        self.assertEqual(first.get_text(), "* First")
        self.assertIn(second, window.documents())

    def test_utility_pane_highlights_only_current_document_across_views(self):
        first = self.app.new_tab()
        first.buffer.set_text("* First")
        second = self.app.new_tab()
        second.buffer.set_text("* Second")
        window = first.window
        pane = window.utility_pane
        pane.refresh()

        def highlighted():
            return [row.document for row in pane.rows if row.has_css_class("current-document")]

        self.assertEqual(highlighted(), [second])
        first.present()
        self.assertEqual(highlighted(), [first])
        self.app.show_agenda(window)
        self.assertEqual(highlighted(), [first])
        self.app.show_weekly_agenda(window)
        self.assertEqual(highlighted(), [first])
        row = next(row for row in pane.rows if row.document is second)
        pane.listbox.emit("row-activated", row)
        self.assertEqual(highlighted(), [second])
        second.buffer.set_text("* Changed")
        pane.refresh()
        self.assertEqual(highlighted(), [second])

    def test_utility_pane_new_file_button_is_beside_heading_and_opens_editor(self):
        tab = self.app.new_tab()
        window = tab.window
        pane = window.utility_pane
        self.assertIs(pane.new_file_button.get_prev_sibling(), pane.open_button)
        self.assertEqual(pane.open_button.get_prev_sibling().get_label(), "Files")
        for show in (self.app.show_agenda, self.app.show_weekly_agenda):
            show(window)
            count = len(window.documents())
            pane.new_file_button.emit("clicked")
            self.assertEqual(len(window.documents()), count + 1)
            self.assertEqual(window.stack.get_visible_child_name(), "files")
            self.assertTrue(window.active_tab.is_pristine)
        window.split_view.set_collapsed(True)
        window.split_view.set_show_sidebar(True)
        pane.new_file_button.emit("clicked")
        self.assertFalse(window.split_view.get_show_sidebar())

    def test_utility_pane_appends_new_files_and_keeps_saved_drafts_in_place(self):
        with tempfile.TemporaryDirectory() as directory:
            def create(name):
                path = os.path.join(directory, name)
                with open(path, "w") as stream:
                    stream.write("* Event\n")
                return path

            existing = create("z-existing.org")
            with patch.object(self.app, "agenda_directory", return_value=directory):
                tab = self.app.new_tab()
                pane = tab.window.utility_pane
                pane.refresh()
                added = create("a-new.org")
                pane.refresh()
                self.assertEqual([row.file.get_path() for row in pane.rows], [existing, added])
                tab.buffer.set_text("* Draft")
                pane.refresh()
                self.assertIs(pane.rows[-1].document, tab)
                saved = create("0-saved.org")
                tab._set_file(Gio.File.new_for_path(saved), "* Draft")
                pane.refresh()
                self.assertEqual([row.file.get_path() for row in pane.rows],
                                 [existing, added, saved])
                self.assertIs(pane.rows[-1].document, tab)
                self.app.set_agenda_file_visible(existing, False)
                pane.refresh()
                self.assertEqual([row.file.get_path() for row in pane.rows],
                                 [existing, added, saved])
                os.unlink(added)
                pane.refresh()
                self.assertEqual([row.file.get_path() for row in pane.rows], [existing, saved])
                create("a-new.org")
                pane.refresh()
                self.assertEqual([row.file.get_path() for row in pane.rows],
                                 [existing, saved, added])

    def test_utility_drag_reorders_rows_and_persists_file_order(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = [os.path.join(directory, name + ".org") for name in ("a", "b", "c")]
            for path in paths:
                with open(path, "w") as stream:
                    stream.write("* Event\n")
            with patch.object(self.app, "agenda_directory", return_value=directory):
                tab = self.app.new_tab()
                tab.present()
                pane = tab.window.utility_pane
                self.wait_for(pane.get_mapped)
                pane.refresh()
                first, second, third = pane.rows
                self.wait_for(lambda: first.get_height() > 0)
                controllers = third.observe_controllers()
                drag = next(controllers.get_item(i) for i in range(controllers.get_n_items())
                            if isinstance(controllers.get_item(i), Gtk.DragSource))
                targets = first.observe_controllers()
                drop = next(targets.get_item(i) for i in range(targets.get_n_items())
                            if isinstance(targets.get_item(i), Gtk.DropTarget))
                provider = drag.emit("prepare", 8.0, 8.0)
                self.assertIsInstance(provider, Gdk.ContentProvider)
                self.assertTrue(pane._dragging)
                drop.emit("motion", 0.0, 0.0)
                self.assertTrue(first.has_css_class("drop-before"))
                settings = Mock()
                with patch.object(self.app, "settings", settings):
                    self.assertTrue(drop.emit("drop", third, 0.0, 0.0))
                    settings.set_strv.assert_called_once_with(
                        "utility-file-order",
                        [Gio.File.new_for_path(paths[i]).get_uri() for i in (2, 0, 1)])
                pane._on_drag_end()
                self.assertEqual([row.file.get_path() for row in pane.rows],
                                 [paths[2], paths[0], paths[1]])
                first, second, third = pane.rows
                self.assertTrue(pane._on_drop(None, first, 0, third.get_height(), third))
                pane.refresh()
                self.assertEqual([row.file.get_path() for row in pane.rows], paths)
                self.assertFalse(pane._on_drop(None, Adw.PreferencesRow(), 0, 0, pane.rows[0]))
                self.assertIs(tab.window.active_tab, tab)
                self.assertTrue(all(row.visibility.get_active() for row in pane.rows))

    def test_utility_file_order_is_restored_and_shared_across_windows(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = [os.path.join(directory, name + ".org") for name in ("a", "b")]
            for path in paths:
                with open(path, "w") as stream:
                    stream.write("* Event\n")
            self.app.utility_file_order = [Gio.File.new_for_path(path).get_uri()
                                          for path in reversed(paths)]
            with patch.object(self.app, "agenda_directory", return_value=directory):
                first = self.app.new_tab().window.utility_pane
                second = self.app.new_window().new_tab().window.utility_pane
                self.assertEqual([row.file.get_path() for row in first.rows], paths[::-1])
                self.assertEqual([row.file.get_path() for row in second.rows], paths[::-1])
                self.assertTrue(first._on_drop(None, first.rows[1], 0, -1, first.rows[0]))
                self.wait_for(lambda: [row.file.get_path() for row in second.rows] == paths)

    def test_utility_sources_toggle_both_views_without_closing_or_modifying_files(self):
        import datetime as dt

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "events.org")
            text = f"* Event <{dt.date.today().isoformat()}>\n"
            with open(path, "w") as stream:
                stream.write(text)
            with patch.object(self.app, "agenda_directory", return_value=directory):
                tab = self.app.new_tab()
                window = tab.window
                pane = window.utility_pane
                pane.refresh()
                row = next(row for row in pane.rows if row.file and row.file.get_path() == path)
                self.assertIsNone(row.document)
                self.assertTrue(row.visibility.get_active())
                pane.listbox.emit("row-activated", row)
                self.wait_for(lambda: self.app.window_for_path(path).get_text() == text)
                document = self.app.window_for_path(path)
                self.app.show_agenda(window)
                self.app.show_weekly_agenda(window)
                self.assertEqual(len(window.agenda.entry_buttons), 1)
                self.assertEqual(len(window.weekly_agenda.entry_buttons), 1)
                pane.refresh()
                row = next(row for row in pane.rows if row.document is document)
                self.assertEqual(row.get_tooltip_text(), path)
                self.assertFalse(row.has_css_class("accent"))
                self.assertTrue(row.visibility.has_css_class("utility-file-visibility"))
                self.assertTrue(Gtk.IconTheme.get_for_display(
                    Gdk.Display.get_default()).has_icon("object-select-symbolic"))
                self.assertIsNone(row.visibility.get_next_sibling())
                self.assertIsInstance(row.visibility.get_prev_sibling(), Gtk.Label)
                self.assertIs(row.get_child().get_first_child(), row.color_dot)
                self.assertEqual(row.get_child().get_spacing(), 12)
                self.assertEqual(row.color_dot.get_size_request(), (16, 16))
                reference = Gtk.ListBoxRow()
                reference_content = Gtk.Box(spacing=12, margin_start=6, margin_end=6)
                reference_content.append(Gtk.DrawingArea(width_request=16, height_request=16))
                reference_content.append(Gtk.Label(label="Calendar", hexpand=True))
                reference_content.append(Gtk.Image(icon_name="object-select-symbolic"))
                reference.set_child(reference_content)
                pane.listbox.append(reference)
                self.assertEqual(
                    row.measure(Gtk.Orientation.VERTICAL, -1)[1],
                    reference.measure(Gtk.Orientation.VERTICAL, -1)[1])
                pane.listbox.remove(reference)
                settings = Mock()
                with patch.object(self.app, "settings", settings):
                    row.visibility.set_active(False)
                    settings.set_strv.assert_called_once_with(
                        "hidden-agenda-files", [Gio.File.new_for_path(path).get_uri()])
                self.wait_for(lambda: not window.agenda.entry_buttons
                              and not window.weekly_agenda.entry_buttons)
                self.assertFalse(document.buffer.get_modified())
                self.assertEqual(document.get_text(), text)
                self.assertIn(document, window.documents())
                row = next(row for row in pane.rows if row.document is document)
                self.assertFalse(row.visibility.get_active())
                row.visibility.set_active(True)
                self.wait_for(lambda: len(window.agenda.entry_buttons) == 1
                              and len(window.weekly_agenda.entry_buttons) == 1)

    def test_file_colors_are_stable_and_match_calendar_events(self):
        import datetime as dt
        from utilitypane import FILE_COLORS, file_color, file_color_index

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "events.org")
            alias = os.path.join(directory, "alias.org")
            with open(path, "w") as stream:
                stream.write(f"* Event <{dt.date.today().isoformat()}>\n")
            os.symlink(path, alias)
            self.assertEqual(file_color(path), file_color(alias))
            self.assertIn(file_color(path), FILE_COLORS)
            with patch.object(self.app, "agenda_directory", return_value=directory):
                tab = self.app.new_tab()
                self.app.show_agenda(tab.window)
                for item, button in tab.window.agenda.entry_buttons:
                    self.assertTrue(button.has_css_class(
                        f"file-color-{file_color_index(item.entry.path)}"))
                self.assertTrue(tab.window.agenda.entry_buttons)

    def test_utility_pane_collapses_and_its_header_button_toggles_the_overlay(self):
        tab = self.app.new_tab()
        tab.buffer.set_text("* Draft")
        window = tab.window
        tab.present()
        window.set_default_size(700, 680)
        self.wait_for(window.split_view.get_collapsed)
        self.assertFalse(window.split_view.get_show_sidebar())

        def find_toggle(widget):
            if widget.get_tooltip_text() == "Toggle utility pane":
                return widget
            child = widget.get_first_child()
            while child is not None:
                found = find_toggle(child)
                if found is not None:
                    return found
                child = child.get_next_sibling()
            return None

        toggle = find_toggle(tab.header)
        self.assertIsNotNone(toggle)
        toggle.set_active(True)
        self.assertTrue(window.split_view.get_show_sidebar())
        pane = window.utility_pane
        row = next(row for row in pane.rows if row.document is tab)
        pane.listbox.emit("row-activated", row)
        self.assertFalse(window.split_view.get_show_sidebar())
        self.assertFalse(toggle.get_active())
        window.set_default_size(960, 680)
        self.wait_for(lambda: not window.split_view.get_collapsed())
        self.assertTrue(window.split_view.get_show_sidebar())

    def test_utility_pane_hides_empty_untitled_documents_but_keeps_drafts_and_named_files(self):
        tab = self.app.new_tab()
        pane = tab.window.utility_pane
        with patch.object(self.app, "agenda_files", return_value=[]):
            pane.refresh()
            self.assertEqual(pane.rows, [])
            self.assertTrue(pane.empty.get_visible())
            tab.buffer.set_text("* Draft")
            self.wait_for(lambda: any(row.document is tab for row in pane.rows))
            self.assertFalse(pane.empty.get_visible())
            tab.buffer.set_text("")
            self.assertTrue(tab.buffer.get_modified())
            self.wait_for(lambda: not pane.rows)
            self.assertIn(tab, tab.window.documents())
            tab._set_file(Gio.File.new_for_path("/org/empty.org"), "")
            pane.refresh()
            self.assertEqual(len(pane.rows), 1)
            self.assertIs(pane.rows[0].document, tab)
            self.assertEqual(pane.rows[0].get_title(), "empty")

    def test_utility_visibility_toggle_preserves_keyboard_focus_and_does_not_open_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "event.org")
            with open(path, "w") as stream:
                stream.write("* Event <2026-11-02>\n")
            with patch.object(self.app, "agenda_directory", return_value=directory):
                tab = self.app.new_tab()
                tab.present()
                self.wait_for(tab.view.get_mapped)
                pane = tab.window.utility_pane
                pane.refresh()
                row = next(row for row in pane.rows if row.visibility)
                row.visibility.grab_focus()
                row.visibility.activate()
                self.wait_for(lambda: not next(
                    row for row in pane.rows if row.visibility).visibility.get_active())
                pane.refresh()
                row = next(row for row in pane.rows if row.visibility)
                self.assertIs(tab.window.get_focus(), row.visibility)
                self.assertIsNone(self.app.window_for_path(path))
                self.assertIs(tab.window.active_tab, tab)

    def test_utility_calendar_navigates_views_and_marks_only_visible_sources(self):
        import datetime as dt
        import org

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "event.org")
            with open(path, "w") as stream:
                stream.write("* Event <2026-11-17>\n")
            with patch.object(self.app, "agenda_directory", return_value=directory):
                tab = self.app.new_tab()
                window = tab.window
                pane = window.utility_pane
                pane.calendar.set_date(GLib.DateTime.new_local(2026, 11, 17, 0, 0, 0))
                self.assertEqual(window.stack.get_visible_child_name(), "agenda")
                self.assertEqual(window.agenda.start, org.week_start(dt.date(2026, 11, 17)))
                self.assertEqual(pane.calendar.get_date().get_day_of_month(), 17)
                pane.update_marks()
                self.assertTrue(pane.calendar.get_day_is_marked(17))
                self.app.set_agenda_file_visible(path, False)
                pane.update_marks()
                self.assertFalse(pane.calendar.get_day_is_marked(17))
                self.app.show_weekly_agenda(window)
                pane.calendar.set_date(GLib.DateTime.new_local(2026, 12, 9, 0, 0, 0))
                self.assertEqual(window.stack.get_visible_child_name(), "weekly-agenda")
                self.assertEqual(window.weekly_agenda.start, org.week_start(dt.date(2026, 12, 9)))
                window.weekly_agenda.shift(1)
                selected = pane.calendar.get_date()
                date = dt.date(selected.get_year(), selected.get_month(),
                               selected.get_day_of_month())
                self.assertEqual(org.week_start(date), window.weekly_agenda.start)
                window.agenda.refresh()
                self.assertEqual(pane.calendar.get_date().get_day_of_month(), date.day)

    def test_visibility_choices_are_restored_at_startup(self):
        directory = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "data"))
        source = Gio.SettingsSchemaSource.new_from_directory(
            directory, Gio.SettingsSchemaSource.get_default(), False)
        schema = source.lookup("io.github.kllmanu.Organizer", False)
        settings = Gio.Settings.new_full(schema, Gio.memory_settings_backend_new(), None)
        settings.set_strv("hidden-agenda-files", ["file:///org/hidden.org"])
        settings.set_strv("utility-file-order", ["file:///org/b.org", "file:///org/a.org"])
        settings.set_strv("removed-files", ["file:///org/removed.org"])
        settings.set_value("file-colors", GLib.Variant("a{ss}", {"file:///org/a.org": "#123456"}))
        with (patch.object(OrganizerApplication, "_load_settings", return_value=settings),
              patch("vim.data_path", return_value=None)):
            app = OrganizerApplication()
            app.set_application_id("io.github.kllmanu.Organizer.VisibilityTests")
            app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
            app.register(None)
        self.addCleanup(app.quit)
        self.assertFalse(app.agenda_file_visible("/org/hidden.org"))
        self.assertTrue(app.agenda_file_visible("/org/visible.org"))
        self.assertEqual(app.utility_file_order, ["file:///org/b.org", "file:///org/a.org"])
        self.assertTrue(app.file_removed(Gio.File.new_for_path("/org/removed.org")))
        self.assertEqual(app.calendar_file_color("/org/a.org"), "#123456")

    def test_open_org_sources_outside_folder_are_deduplicated_and_follow_unsaved_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = os.path.join(directory, "calendar")
            os.mkdir(folder)
            path = os.path.join(directory, "outside.org")
            with open(path, "w") as stream:
                stream.write("* Disk <2026-11-02>\n")
            with patch.object(self.app, "agenda_directory", return_value=folder):
                tab = self.app.open_file(Gio.File.new_for_path(path))
                self.wait_for(lambda: bool(tab.get_text()))
                tab.buffer.set_text("* Unsaved <2026-11-03>\n")
                _, files, entries = self.app.agenda_entries()
                self.assertEqual(files, [path])
                self.assertEqual(entries[0].heading_text, "* Unsaved <2026-11-03>")
                self.app.set_agenda_file_visible(path, False)
                self.assertEqual(self.app.agenda_entries()[2], [])
                with patch.object(self.app, "agenda_directory", return_value=directory):
                    self.assertEqual(self.app.agenda_files(), [path])
                pane = tab.window.utility_pane
                pane.refresh()
                self.assertEqual(len([row for row in pane.rows
                                      if row.file and row.file.get_path() == path]), 1)
