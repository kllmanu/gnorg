"""Session integration tests."""

import os
import tempfile
from unittest.mock import patch
from unittest.mock import Mock

from editor_test_case import EditorTestCase
from vim import Gio, GLib


class SessionTests(EditorTestCase):
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
            settings.get_string.return_value = ""
            settings.get_value.return_value = GLib.Variant("a{s(uu)}", {})
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
        settings.get_string.return_value = ""
        with patch.object(self.app, "settings", settings):
            self.app._on_quit()
            tab.window.get_visible_dialog().emit("response", "cancel")
            settings.set_strv.assert_not_called()

    def test_session_restore_skips_missing_files_including_selected_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "existing.org")
            with open(path, "w") as stream:
                stream.write("* Existing")
            existing = Gio.File.new_for_path(path)
            missing = Gio.File.new_for_path(os.path.join(directory, "missing.org"))
            settings = Mock()
            settings.get_strv.return_value = [missing.get_uri(), existing.get_uri()]
            settings.get_value.return_value = GLib.Variant("a{s(uu)}", {})
            settings.get_string.side_effect = lambda key: (
                missing.get_uri() if key == "session-active-file" else directory)
            settings.get_int.side_effect = lambda key: {
                "window-width": 960, "window-height": 680}[key]
            settings.get_boolean.return_value = False
            with patch.object(self.app, "settings", settings):
                self.app._session_restored = False
                self.app.do_activate()
                self.assertEqual(len(self.app.editor_tabs()), 1)
                tab = self.app.editor_tabs()[0]
                self.assertTrue(tab.file.equal(existing))
                self.wait_for(lambda: tab.get_text() == "* Existing")
                self.wait_for(lambda: tab.window.get_focus() is tab.view)
                self.assertIsNone(self.app.window_for_path(missing.get_path()))

    def test_session_restore_with_only_missing_files_leaves_pristine_editor(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Gio.File.new_for_path(os.path.join(directory, "missing.org"))
            settings = Mock()
            settings.get_strv.return_value = [missing.get_uri()]
            settings.get_value.return_value = GLib.Variant("a{s(uu)}", {})
            settings.get_string.side_effect = lambda key: (
                missing.get_uri() if key == "session-active-file" else directory)
            settings.get_int.side_effect = lambda key: {
                "window-width": 960, "window-height": 680}[key]
            settings.get_boolean.return_value = False
            with patch.object(self.app, "settings", settings):
                self.app._session_restored = False
                self.app.do_activate()
                self.assertEqual(len(self.app.editor_tabs()), 1)
                tab = self.app.editor_tabs()[0]
                self.assertTrue(tab.is_pristine)
                self.assertIsNone(tab.file)
                self.assertEqual(tab.window.utility_pane.rows, [])

    def test_startup_reselects_last_file_after_empty_document_and_quit_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Gio.SettingsSchemaSource.new_from_directory(
                os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "data")),
                Gio.SettingsSchemaSource.get_default(), False)
            settings = Gio.Settings.new_full(
                source.lookup("io.github.kllmanu.Organizer", False),
                Gio.memory_settings_backend_new(), None)
            settings.set_string("agenda-directory", directory)
            files = []
            with patch.object(self.app, "settings", settings):
                for name in ("first.org", "second.org"):
                    path = os.path.join(directory, name)
                    with open(path, "w") as stream:
                        stream.write("* " + name + "\nBody with text\n")
                    files.append(Gio.File.new_for_path(path))
                    self.app.open_file(files[-1])
                self.wait_for(lambda: all(tab.get_text() for tab in self.app.editor_tabs()))
                first = self.app.window_for_path(files[0].get_path())
                first.buffer.place_cursor(first.buffer.get_iter_at_line_offset(1, 7)[1])
                first.present()
                self.app.new_tab().present()
                with patch.object(self.app, "quit"):
                    self.app._on_quit()
                self.assertEqual(settings.get_string("session-active-file"), files[0].get_uri())
                self.app._session_restored = False
                self.app.do_activate()
                window = self.app.editor_windows()[0]
                self.assertEqual(window.active_tab.file.get_uri(), files[0].get_uri())
                self.wait_for(lambda: window.active_tab.get_text() == "* first.org\nBody with text\n")
                cursor = window.active_tab.buffer.get_iter_at_mark(
                    window.active_tab.buffer.get_insert())
                self.assertEqual((cursor.get_line(), cursor.get_line_offset()), (1, 7))
                self.wait_for(lambda: window.get_focus() is window.active_tab.view)
                self.assertEqual(len(window.documents()), 2)
                self.assertEqual(window.stack.get_visible_child_name(), "files")
                self.app.do_open([files[1]], 1, "")
                self.assertEqual(window.active_tab.file.get_uri(), files[1].get_uri())

    def test_session_cursor_restore_reveals_folds_and_clamps_changed_files(self):
        source = Gio.SettingsSchemaSource.new_from_directory(
            os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "data")),
            Gio.SettingsSchemaSource.get_default(), False)
        settings = Gio.Settings.new_full(
            source.lookup("io.github.kllmanu.Organizer", False),
            Gio.memory_settings_backend_new(), None)
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "folded.org")
            text = "#+STARTUP: overview\n* Heading\nBody text"
            with open(path, "w") as stream:
                stream.write(text)
            file = Gio.File.new_for_path(path)
            settings.set_strv("session-files", [file.get_uri()])
            settings.set_value("session-cursor-positions",
                               GLib.Variant("a{s(uu)}", {file.get_uri(): (200, 300)}))
            with patch.object(self.app, "settings", settings):
                self.app._session_restored = False
                self.app.restore_session()
                tab = self.app.window_for_path(path)
                self.wait_for(lambda: tab.get_text() == text)
                cursor = tab.buffer.get_iter_at_mark(tab.buffer.get_insert())
                self.assertEqual((cursor.get_line(), cursor.get_line_offset()), (2, 9))
                self.assertFalse(cursor.has_tag(tab.org_fold.hidden))
                self.assertFalse(tab.buffer.get_modified())
