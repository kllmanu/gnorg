"""Opening the current local document in Emacs."""

import os
import tempfile
from unittest.mock import Mock, patch

from editor_test_case import EditorTestCase
from vim import Gdk, Gio, GLib


class EmacsTests(EditorTestCase):
    def document(self):
        tab = self.app.new_tab()
        tab._set_file(Gio.File.new_for_path("/tmp/notes with spaces.org"),
                      "* Heading\nBody café\n")
        tab.buffer.set_modified(False)
        tab.buffer.place_cursor(tab.buffer.get_iter_at_line_offset(1, 7)[1])
        return tab

    def test_menu_action_and_shortcut_open_at_cursor_without_a_shell(self):
        tab = self.document()
        process = Mock()
        process.communicate_utf8_finish.return_value = (True, "", "")
        process.get_successful.return_value = True
        with (patch("vim.GLib.find_program_in_path", return_value="/usr/bin/emacsclient"),
              patch("vim.Gio.Subprocess.new", return_value=process) as launch):
            tab.actions.lookup_action("open-in-emacs").activate(None)
            argv, flags = launch.call_args.args
            self.assertEqual(argv, ["emacsclient", "--no-wait", "--create-frame",
                                    "--alternate-editor=emacs", "+2:8",
                                    "/tmp/notes with spaces.org"])
            self.assertTrue(flags & Gio.SubprocessFlags.STDERR_PIPE)
            callback = process.communicate_utf8_async.call_args.args[-1]
            callback(process, None)
            self.assertIn("<Control><Alt>e",
                          self.app.get_accels_for_action("win.open-in-emacs"))
            with patch.object(tab, "open_in_emacs") as open_file:
                self.assertTrue(tab.window._on_key_pressed(
                    None, Gdk.KEY_e, 0,
                    Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK))
                open_file.assert_called_once()

    def test_unsaved_document_requires_save_and_cancel_does_not_launch(self):
        tab = self.document()
        tab.buffer.set_modified(True)
        with (patch("vim.GLib.find_program_in_path", return_value="emacsclient"),
              patch("vim.Gio.Subprocess.new") as launch,
              patch.object(tab, "save") as save):
            tab.open_in_emacs()
            tab.window.get_visible_dialog().emit("response", "cancel")
            save.assert_not_called()
            launch.assert_not_called()
            tab.open_in_emacs()
            tab.window.get_visible_dialog().emit("response", "save")
            launch.assert_not_called()
            save.assert_called_once()
            tab.buffer.set_modified(False)
            save.call_args.kwargs["then"]()
            launch.assert_called_once()

    def test_untitled_document_uses_save_as_before_opening(self):
        tab = self.app.new_tab()
        with (patch("vim.GLib.find_program_in_path", return_value="emacsclient"),
              patch.object(tab, "save_as") as save_as,
              patch("vim.Gio.Subprocess.new") as launch):
            tab.open_in_emacs()
            tab.window.get_visible_dialog().emit("response", "save")
            save_as.assert_called_once()
            launch.assert_not_called()

    def test_save_finishes_before_launch_and_failure_does_not_launch(self):
        tab = self.document()
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "current.org")
            tab.file = Gio.File.new_for_path(path)
            tab.buffer.set_text("* Latest changes\n")

            def launch_after_save(*_):
                with open(path) as stream:
                    self.assertEqual(stream.read(), "* Latest changes\n")
                return Mock()

            with (patch("vim.GLib.find_program_in_path", return_value="emacsclient"),
                  patch("vim.Gio.Subprocess.new", side_effect=launch_after_save) as launch):
                tab.open_in_emacs()
                tab.window.get_visible_dialog().emit("response", "save")
                self.wait_for(lambda: launch.called)
                self.assertFalse(tab.buffer.get_modified())
            tab.file = Gio.File.new_for_path(os.path.join(directory, "missing", "current.org"))
            tab.buffer.set_text("* Unsaved changes\n")
            with (patch("vim.GLib.find_program_in_path", return_value="emacsclient"),
                  patch("vim.Gio.Subprocess.new") as launch,
                  patch.object(tab, "toast") as toast):
                tab.open_in_emacs()
                tab.window.get_visible_dialog().emit("response", "save")
                self.wait_for(lambda: toast.called)
                self.assertIn("Could not save file", toast.call_args.args[0])
                launch.assert_not_called()
                self.assertTrue(tab.buffer.get_modified())

    def test_missing_client_remote_file_and_launch_errors_are_reported(self):
        tab = self.document()
        with (patch.object(tab, "toast") as toast,
              patch("vim.GLib.find_program_in_path", return_value=None),
              patch("vim.Gio.Subprocess.new") as launch):
            tab.open_in_emacs()
            self.assertIn("not installed", toast.call_args.args[0])
            launch.assert_not_called()
        with (patch.object(tab, "toast") as toast,
              patch("vim.GLib.find_program_in_path", return_value="emacsclient"),
              patch("vim.Gio.Subprocess.new", side_effect=GLib.Error("Launch failed"))):
            tab.open_in_emacs()
            self.assertIn("Launch failed", toast.call_args.args[0])
        tab.file = Gio.File.new_for_uri("sftp://example.org/notes.org")
        with patch.object(tab, "toast") as toast:
            tab.open_in_emacs()
            self.assertIn("local file", toast.call_args.args[0])

    def test_failed_client_and_async_errors_are_reported(self):
        tab = self.document()
        for error in (None, GLib.Error("Communication failed")):
            process = Mock()
            process.get_successful.return_value = False
            process.communicate_utf8_finish.return_value = (True, "", "Server unavailable")
            process.communicate_utf8_finish.side_effect = error
            with (patch.object(tab, "toast") as toast,
                  patch("vim.GLib.find_program_in_path", return_value="emacsclient"),
                  patch("vim.Gio.Subprocess.new", return_value=process)):
                tab.open_in_emacs()
                callback = process.communicate_utf8_async.call_args.args[-1]
                callback(process, None)
                self.assertIn("Communication failed" if error else "Server unavailable",
                              toast.call_args.args[0])
