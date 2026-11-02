"""Project demo sources and temporary launch-time folder overrides."""

import datetime as dt
import os
from unittest.mock import Mock, patch

from editor_test_case import EditorTestCase
import org
from vim import Gio, GnorgApplication


class DemoTests(EditorTestCase):
    def test_launch_override_takes_precedence_without_writing_preferences(self):
        directory = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "org"))
        with patch.dict(os.environ, {"GNORG_AGENDA_DIRECTORY": directory}):
            app = GnorgApplication()
        settings = Mock()
        settings.get_string.return_value = "/personal/org"
        app.settings = settings
        self.assertEqual(app.agenda_directory(), directory)
        self.assertTrue(app.get_flags() & Gio.ApplicationFlags.NON_UNIQUE)
        settings.get_string.assert_not_called()
        settings.set_string.assert_not_called()
        app.agenda_directory_override = ""
        self.assertEqual(app.agenda_directory(), "/personal/org")

    def test_demo_files_are_discovered_and_populate_agenda(self):
        directory = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "org"))
        with patch.object(self.app, "agenda_directory_override", directory):
            files = self.app.agenda_files()
            self.assertEqual({os.path.basename(path) for path in files},
                             {"welcome.org", "projects.org", "calendar.org"})
            _, _, entries = self.app.agenda_entries()
        self.assertTrue(any(entry.tags for entry in entries))
        days = org.build_agenda(entries, dt.date(2026, 11, 2), 7, dt.date(2026, 11, 2))
        items = [item for day in days.values() for item in day]
        for kind in ("timestamp", "scheduled", "deadline"):
            self.assertTrue(any(item.kind == kind for item in items), kind)
        self.assertTrue(any(item.ts.time and item.ts.end_time for item in items))

    def test_override_is_visible_and_folder_chooser_disabled(self):
        directory = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "org"))
        tab = self.app.new_tab()
        tab.present()
        with patch.object(self.app, "agenda_directory_override", directory):
            self.app.show_preferences()
            dialog = tab.window.get_visible_dialog()

            def find(widget):
                if getattr(widget, "get_title", lambda: None)() == "Calendar Folder":
                    return widget
                child = widget.get_first_child()
                while child:
                    found = find(child)
                    if found:
                        return found
                    child = child.get_next_sibling()
                return None

            row = find(dialog)
            self.assertIsNotNone(row)
            self.assertIn("launch override", row.get_subtitle())
            self.assertFalse(row.get_activatable_widget().get_sensitive())
            dialog.close()
