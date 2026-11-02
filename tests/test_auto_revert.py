"""Auto revert integration tests."""

import os
import tempfile
from unittest.mock import patch
from unittest.mock import Mock

from editor_test_case import EditorTestCase
from vim import Gio


class AutoRevertTests(EditorTestCase):
    def test_auto_revert_reloads_external_and_atomic_changes_and_preserves_cursor(self):
        import datetime as dt

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "events.org")
            with open(path, "w") as stream:
                stream.write(f"* Before <{dt.date.today().isoformat()}>\nBody\n")
            with patch.object(self.app, "agenda_directory", return_value=directory):
                tab = self.app.open_file(Gio.File.new_for_path(path))
                self.wait_for(lambda: tab._file_monitor is not None)
                self.app.show_weekly_agenda(tab.window)
                agenda = tab.window.weekly_agenda
                tab.buffer.place_cursor(tab.buffer.get_iter_at_line_offset(1, 2)[1])
                with open(path, "w") as stream:
                    stream.write(f"* After <{dt.date.today().isoformat()}>\nBody updated\n")
                self.wait_for(lambda: "* After" in tab.get_text())
                cursor = tab.buffer.get_iter_at_mark(tab.buffer.get_insert())
                self.assertEqual((cursor.get_line(), cursor.get_line_offset()), (1, 2))
                self.assertFalse(tab.buffer.get_modified())
                self.assertEqual(tab.window.stack.get_visible_child_name(), "weekly-agenda")
                self.wait_for(lambda: any("After" in item.entry.title
                                         for item, _button in agenda.entry_buttons))
                replacement = path + ".tmp"
                with open(replacement, "w") as stream:
                    stream.write("* Atomic\n")
                os.replace(replacement, path)
                self.wait_for(lambda: tab.get_text() == "* Atomic\n")
                self.assertFalse(tab.buffer.get_modified())

    def test_auto_revert_keeps_unsaved_edits_and_warns(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "events.org")
            with open(path, "w") as stream:
                stream.write("* Original")
            tab = self.app.open_file(Gio.File.new_for_path(path))
            self.wait_for(lambda: tab._file_monitor is not None)
            tab.buffer.set_text("* Unsaved")
            with patch.object(tab, "toast") as toast:
                with open(path, "w") as stream:
                    stream.write("* External")
                self.wait_for(lambda: toast.called)
                toast.assert_called_once_with(
                    "File changed on disk. Your unsaved edits have been kept.")
            self.assertEqual(tab.get_text(), "* Unsaved")
            self.assertTrue(tab.buffer.get_modified())

    def test_auto_revert_preference_disables_monitor_and_rechecks_when_enabled(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "events.org")
            with open(path, "w") as stream:
                stream.write("* Original")
            tab = self.app.open_file(Gio.File.new_for_path(path))
            self.wait_for(lambda: tab._file_monitor is not None)
            tab.present()
            self.app.show_preferences()
            dialog = tab.window.get_visible_dialog()
            row = self.find_switch(dialog, "Automatically Reload Files")
            self.assertTrue(row.get_active())
            settings = Mock()
            with patch.object(self.app, "settings", settings):
                row.set_active(False)
                settings.set_boolean.assert_called_once_with("auto-revert", False)
            self.assertIsNone(tab._file_monitor)
            with open(path, "w") as stream:
                stream.write("* External")
            self.assertEqual(tab.get_text(), "* Original")
            row.set_active(True)
            self.wait_for(lambda: tab.get_text() == "* External")
            dialog.close()
            monitor = tab._file_monitor
            tab.force_close()
            self.assertTrue(monitor.is_cancelled())
            self.assertIsNone(tab._file_monitor)

    def test_auto_revert_watches_new_saved_file_and_handles_deletion(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "new.org")
            tab = self.app.new_tab()
            self.assertIsNone(tab._file_monitor)
            tab.buffer.set_text("* Saved")
            tab.save_to(Gio.File.new_for_path(path))
            self.wait_for(lambda: tab._file_monitor is not None)
            with patch.object(tab, "toast") as toast:
                os.unlink(path)
                self.wait_for(lambda: toast.called)
                self.assertIn("Could not reload file:", toast.call_args.args[0])
            self.assertEqual(tab.get_text(), "* Saved")
            with open(path, "w") as stream:
                stream.write("* Recreated")
            self.wait_for(lambda: tab.get_text() == "* Recreated")

    def test_auto_revert_keeps_edits_made_while_save_is_in_flight(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "new.org")
            tab = self.app.new_tab()
            tab.buffer.set_text("* Saved")
            tab.save_to(Gio.File.new_for_path(path))
            tab.buffer.set_text("* Typed during save")
            self.wait_for(lambda: tab._file_monitor is not None)
            self.assertTrue(tab.buffer.get_modified())
            tab._reload_from_disk()
            self.assertEqual(tab.get_text(), "* Typed during save")
            with open(path) as stream:
                self.assertEqual(stream.read(), "* Saved\n")

    def test_auto_revert_follows_renamed_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "before.org")
            renamed = os.path.join(directory, "after.org")
            with open(path, "w") as stream:
                stream.write("* Original")
            tab = self.app.open_file(Gio.File.new_for_path(path))
            self.wait_for(lambda: tab._file_monitor is not None)
            self.app.rename_document(tab.file, "after", tab.window)
            self.wait_for(lambda: tab.path == renamed)
            with open(renamed, "w") as stream:
                stream.write("* Renamed and changed")
            self.wait_for(lambda: tab.get_text() == "* Renamed and changed")
