import os
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir, "src"))

from vim import Gdk, Gio, GLib, Gtk, OrganizerApplication, ZOOM_MAX, ZOOM_MIN  # noqa: E402
from bufferpicker import BufferPicker, matches_buffer  # noqa: E402


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
            self.wait_for(lambda: first.file is not None and second.file is not None)
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
