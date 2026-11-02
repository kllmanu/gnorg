"""Navigation integration tests."""

import os
import tempfile
from unittest.mock import patch

from editor_test_case import EditorTestCase
from vim import Gio
from vim import Gdk
from vim import Adw


class NavigationTests(EditorTestCase):
    def test_alt_file_navigation_uses_sidebar_order_and_stops_at_edges(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = [os.path.join(directory, name + ".org") for name in ("a", "b", "c")]
            for path in paths:
                with open(path, "w") as stream:
                    stream.write("* Event\n")
            with patch.object(self.app, "agenda_directory", return_value=directory):
                self.app.utility_file_order = [Gio.File.new_for_path(path).get_uri()
                                              for path in reversed(paths)]
                tab = self.app.open_file(Gio.File.new_for_path(paths[2]))
                window = tab.window

                def press(key, modifiers=Gdk.ModifierType.ALT_MASK):
                    return window._on_key_pressed(None, key, 0, modifiers)

                for key, expected in (
                        (Gdk.KEY_k, paths[2]), (Gdk.KEY_j, paths[1]),
                        (Gdk.KEY_j, paths[0]), (Gdk.KEY_j, paths[0]),
                        (Gdk.KEY_k, paths[1]), (Gdk.KEY_k, paths[2]),
                        (Gdk.KEY_k, paths[2])):
                    self.assertTrue(press(key))
                    self.assertEqual(window.active_tab.path, expected)
                self.app.show_agenda(window)
                self.assertTrue(press(Gdk.KEY_j))
                self.assertEqual(window.stack.get_visible_child_name(), "files")
                self.assertEqual(window.active_tab.path, paths[1])
                self.app.show_weekly_agenda(window)
                self.assertTrue(press(Gdk.KEY_k))
                self.assertEqual(window.active_tab.path, paths[2])
                self.assertEqual(window.stack.get_visible_child_name(), "files")
                for key in (Gdk.KEY_j, Gdk.KEY_k):
                    self.assertFalse(press(
                        key, Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.CONTROL_MASK))
                self.wait_for(lambda: all(tab.get_text() for tab in window.documents()))
                self.assertEqual(len(window.documents()), 3)

    def test_alt_file_navigation_handles_empty_list_and_unlisted_empty_document(self):
        with patch.object(self.app, "agenda_files", return_value=[]):
            tab = self.app.new_tab()
            window = tab.window
            for key in (Gdk.KEY_j, Gdk.KEY_k):
                self.assertTrue(window._on_key_pressed(
                    None, key, 0, Gdk.ModifierType.ALT_MASK))
                self.assertIs(window.active_tab, tab)
            tab.buffer.set_text("* Draft")
            empty = window.new_tab()
            self.assertTrue(window._on_key_pressed(
                None, Gdk.KEY_j, 0, Gdk.ModifierType.ALT_MASK))
            self.assertIs(window.active_tab, tab)
            empty.present()
            self.assertTrue(window._on_key_pressed(
                None, Gdk.KEY_k, 0, Gdk.ModifierType.ALT_MASK))
            self.assertIs(window.active_tab, tab)

    def test_alt_navigation_stops_at_edges_without_switching_documents(self):
        first = self.app.new_tab()
        second = self.app.new_tab()
        window = first.window
        second.present()

        def press(key, modifiers=Gdk.ModifierType.ALT_MASK):
            return window._on_key_pressed(None, key, 0, modifiers)

        for key, expected_views in (
                (Gdk.KEY_h, ("files", "files")),
                (Gdk.KEY_l, ("weekly-agenda", "agenda", "agenda", "agenda")),
                (Gdk.KEY_h, ("weekly-agenda", "files", "files", "files"))):
            for expected in expected_views:
                self.assertTrue(press(key))
                self.assertEqual(window.stack.get_visible_child_name(), expected)
                self.assertIs(window.active_tab, second)
        self.assertIs(window.active_view, second)
        self.assertIn(first, window.documents())
        self.assertFalse(press(Gdk.KEY_o))
        for key in (Gdk.KEY_h, Gdk.KEY_l):
            self.assertFalse(press(
                key, Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.CONTROL_MASK))
        self.assertEqual(window.stack.get_visible_child_name(), "files")

    def test_alt_navigation_stops_at_edges_with_single_document(self):
        tab = self.app.new_tab()
        for key, expected in (
                (Gdk.KEY_h, "files"), (Gdk.KEY_l, "weekly-agenda"),
                (Gdk.KEY_l, "agenda"), (Gdk.KEY_l, "agenda"),
                (Gdk.KEY_h, "weekly-agenda"), (Gdk.KEY_h, "files"),
                (Gdk.KEY_h, "files")):
            self.assertTrue(tab.window._on_key_pressed(None, key, 0, Gdk.ModifierType.ALT_MASK))
            self.assertEqual(tab.window.stack.get_visible_child_name(), expected)
            self.assertIs(tab.window.active_tab, tab)

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
            self.assertEqual(window.get_title(), "Calendar – Gnorg")
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
