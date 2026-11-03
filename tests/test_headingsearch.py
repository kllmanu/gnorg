"""Global Org heading search integration tests."""

import os
import tempfile
from unittest.mock import patch

from editor_test_case import EditorTestCase
from vim import Adw, Gdk, Gio, Pango


class HeadingSearchTests(EditorTestCase):
    def test_result_keywords_are_bold_and_colored_in_both_themes(self):
        tab = self.app.new_tab()
        tab.buffer.set_text("* TODO Match <literal>\n* DONE Match\n* Match plain\n")
        search = tab.window.heading_search
        with patch.object(self.app, "agenda_files", return_value=[]):
            search.load_sources()
        manager = Adw.StyleManager.get_default()
        self.addCleanup(manager.set_color_scheme, manager.get_color_scheme())
        self.search(tab.window, "match")
        for theme in (Adw.ColorScheme.FORCE_LIGHT, Adw.ColorScheme.FORCE_DARK):
            manager.set_color_scheme(theme)
            for index, (entry, _tab) in enumerate(search.results):
                label = search.listbox.get_row_at_index(index).get_child().get_first_child()
                attributes = label.get_attributes()
                iterator = attributes.get_iterator()
                foreground = iterator.get(Pango.AttrType.FOREGROUND)
                weight = iterator.get(Pango.AttrType.WEIGHT)
                if entry.keyword is None:
                    self.assertIsNone(foreground)
                    self.assertIsNone(weight)
                    continue
                color = foreground.as_color().color
                self.assertGreater(color.green if entry.is_done else color.red,
                                   color.red if entry.is_done else color.green)
                self.assertEqual(weight.as_int().value, Pango.Weight.BOLD)
                self.assertEqual(iterator.range()[1], len(entry.keyword))
                self.assertTrue(iterator.next())
                self.assertIsNone(iterator.get(Pango.AttrType.FOREGROUND))
                self.assertIsNone(iterator.get(Pango.AttrType.WEIGHT))
            self.assertIn("<literal>", search.listbox.get_row_at_index(0).get_child()
                          .get_first_child().get_label())

    def test_control_n_p_navigate_results_and_activate_focused_heading(self):
        tab = self.app.new_tab()
        tab.buffer.set_text("".join(f"* Match {i}\n" for i in range(12)))
        window = tab.window
        window.present()
        with patch.object(self.app, "agenda_files", return_value=[]):
            search = window.heading_search
            search.show(window._header.search_button)
            self.wait_for(search.get_mapped)
            self.search(window, "match")
            for index in range(12):
                self.assertTrue(search._on_key_pressed(
                    None, Gdk.KEY_n, 0, Gdk.ModifierType.CONTROL_MASK))
                self.assertIs(window.get_focus(), search.listbox.get_row_at_index(index))
            search._on_key_pressed(None, Gdk.KEY_n, 0, Gdk.ModifierType.CONTROL_MASK)
            self.assertIs(window.get_focus(), search.listbox.get_row_at_index(11))
            for index in range(10, -1, -1):
                search._on_key_pressed(None, Gdk.KEY_p, 0, Gdk.ModifierType.CONTROL_MASK)
                self.assertIs(window.get_focus(), search.listbox.get_row_at_index(index))
            search._on_key_pressed(None, Gdk.KEY_p, 0, Gdk.ModifierType.CONTROL_MASK)
            self.assertIs(window.get_focus(), search.entry.get_delegate())
            search._on_key_pressed(None, Gdk.KEY_p, 0, Gdk.ModifierType.CONTROL_MASK)
            self.assertIs(window.get_focus(), search.listbox.get_row_at_index(11))
            with patch.object(tab, "goto_line") as goto:
                search.listbox.emit("row-activated", window.get_focus())
                goto.assert_called_once_with(11)
            self.search(window, "missing")
            self.assertTrue(search._on_key_pressed(
                None, Gdk.KEY_n, 0, Gdk.ModifierType.CONTROL_MASK))

    def search(self, window, query):
        search = window.heading_search
        search.entry.set_text(query)
        search.refresh_results()
        return search

    def test_search_button_and_keyboard_work_in_all_views_without_agenda_commands(self):
        tab = self.app.new_tab()
        window = tab.window
        tab.buffer.set_text("* TODO Find me :@Lab:\n")
        with patch.object(self.app, "agenda_files", return_value=[]):
            for show in (window.show_files,
                         lambda: self.app.show_weekly_agenda(window),
                         lambda: self.app.show_agenda(window)):
                show()
                window.present()
                window._header.search_button.emit("clicked")
                search = window.heading_search
                self.wait_for(search.get_mapped)
                self.assertIs(search.get_parent(), window._header.search_button)
                self.assertIs(window.get_focus(), search.entry.get_delegate())
                with patch.object(window.active_view._org_key_sequences, "_on_key_pressed") as commands:
                    self.assertFalse(window._on_key_pressed(None, Gdk.KEY_f, 0, Gdk.ModifierType(0)))
                    commands.assert_not_called()
                self.assertTrue(window._on_key_pressed(None, Gdk.KEY_Escape, 0, Gdk.ModifierType(0)))
                self.wait_for(lambda: not search.get_visible())
                self.assertTrue(window._on_key_pressed(
                    None, Gdk.KEY_f, 0, Gdk.ModifierType.CONTROL_MASK))
                self.wait_for(search.get_mapped)
                self.assertEqual(len(self.search(window, "find @lab").results), 1)
                self.assertTrue(search._on_key_pressed(None, Gdk.KEY_Down, 0, Gdk.ModifierType(0)))
                self.assertIs(window.get_focus(), search.listbox.get_row_at_index(0))
                self.assertTrue(search._on_key_pressed(None, Gdk.KEY_Up, 0, Gdk.ModifierType(0)))
                self.assertIs(window.get_focus(), search.entry.get_delegate())
                search.popdown()
                self.wait_for(lambda: not search.get_visible())

    def test_search_covers_undated_and_dated_headings_tags_and_unsaved_sources(self):
        tab = self.app.new_tab()
        window = tab.window
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "Hidden.org")
            with open(path, "w") as source:
                source.write("#+CATEGORY: Work\n* TODO Design :@U328:\n"
                             "<2030-01-02 10:00>\n* Undated notes\n")
            with patch.object(self.app, "agenda_files", return_value=[path]), patch.object(
                    self.app, "agenda_file_visible", return_value=False):
                search = window.heading_search
                search.load_sources()
                self.assertEqual(len(self.search(window, "design work @u328").results), 1)
                self.assertEqual(len(self.search(window, "hidden undated").results), 1)
                self.assertEqual(self.search(window, "missing").status.get_label(), "No matching headings.")
                self.assertEqual(len(self.search(window, " ").results), 0)
                tab.file = Gio.File.new_for_path(path)
                tab.buffer.set_text("* DONE Changed unsaved :@Lab:\n")
                other = self.app.new_window().new_tab()
                other.buffer.set_text("* Caf\u00e9 notes\n")
                search.load_sources()
                self.assertEqual(len(self.search(window, "design").results), 0)
                self.assertEqual(len(self.search(window, "done changed @lab").results), 1)
                self.assertEqual(len(self.search(window, "CAF\u00c9").results), 1)

    def test_select_result_opens_disk_heading_and_unsaved_buffer_at_source_line(self):
        tab = self.app.new_tab()
        window = tab.window
        tab.buffer.set_text("#+TITLE: Test\n* First\n* Target\n")
        with patch.object(self.app, "agenda_files", return_value=[]):
            window.heading_search.load_sources()
            search = self.search(window, "target")
            with patch.object(tab, "goto_line", wraps=tab.goto_line) as goto:
                search.entry.emit("activate")
                goto.assert_called_once_with(2)
            self.assertIs(window.active_view, tab)
            self.assertEqual(tab.buffer.get_iter_at_mark(tab.buffer.get_insert()).get_line(), 2)
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "Events.org")
            with open(path, "w") as source:
                source.write("#+TITLE: Events\n* Disk target\n")
            with patch.object(self.app, "agenda_files", return_value=[path]):
                window.heading_search.load_sources()
                search = self.search(window, "disk")
                with patch.object(self.app, "open_location") as open_location:
                    search.listbox.emit("row-activated", search.listbox.get_row_at_index(0))
                    open_location.assert_called_once_with(path, 1)
                search.entry.emit("activate")
                self.wait_for(lambda: self.app.window_for_path(path) is not None
                              and self.app.window_for_path(path).get_text().startswith("#+TITLE: Events"))
                opened = self.app.window_for_path(path)
                self.wait_for(lambda: opened.buffer.get_iter_at_mark(
                    opened.buffer.get_insert()).get_line() == 1)

    def test_search_surfaces_unreadable_sources(self):
        tab = self.app.new_tab()
        search = tab.window.heading_search
        tab.buffer.set_text("* Accessible\n")
        with patch.object(self.app, "agenda_files", return_value=["/missing/search.org"]):
            search.load_sources()
        self.assertTrue(search.errors.get_visible())
        self.assertIn("Could not search /missing/search.org", search.errors.get_label())
        self.assertEqual(len(self.search(tab.window, "accessible").results), 1)
