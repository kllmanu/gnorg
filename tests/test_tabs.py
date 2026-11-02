"""Tabs integration tests."""

import os
import tempfile
from unittest.mock import patch

from editor_test_case import EditorTestCase
from vim import Gio


class TabTests(EditorTestCase):
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

    def test_tab_creation_and_reopening_have_no_keyboard_accelerators(self):
        self.assertEqual(self.app.get_accels_for_action("win.new"), [])
        self.assertEqual(self.app.get_accels_for_action("win.reopen"), [])
        for shortcut in ("<Control>t", "<Control><Shift>t"):
            self.assertEqual(self.app.get_actions_for_accel(shortcut), [])
        self.assertEqual(self.app.get_accels_for_action("win.open"), ["<Control>o"])
        self.assertEqual(self.app.get_accels_for_action("win.save"), ["<Control>s"])
        self.assertEqual(self.app.get_accels_for_action("win.close"), ["<Control>w"])

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
