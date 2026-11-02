"""Shared GTK application fixture and integration-test helpers."""

import os
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir, "src"))

from vim import Adw, Gdk, Gio, GLib, Gtk, GnorgApplication  # noqa: E402


@unittest.skipUnless(Gtk.init_check(), "GTK display required")
class EditorTestCase(unittest.TestCase):
    app = None

    @classmethod
    def setUpClass(cls):
        if EditorTestCase.app is None:
            EditorTestCase.app = GnorgApplication()
            EditorTestCase.app.set_application_id("io.github.kllmanu.Gnorg.Tests")
            EditorTestCase.app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
            with patch.object(GnorgApplication, "_load_settings", return_value=None):
                EditorTestCase.app.register(None)

    def setUp(self):
        self.app.recent_files = []
        self.app.hidden_agenda_files = set()
        self.app.removed_files = set()
        self.app.file_colors = {}
        self.app.utility_file_order = []
        self.app._saved_utility_file_order = []
        self.app._last_selected_file = ""
        self.app.auto_revert = True
        self.app.editor_padding = 16
        self.app.agenda_padding = 16
        self.app.jj_escape = True
        self.app.jj_escape_delay = 200
        self.app.escape_sequence = "jj"
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

    def enter_vim_insert_mode(self, tab):
        tab.present()
        self.wait_for(tab.view.get_mapped)
        display = Gdk.Display.get_default()
        key = display.map_keyval(Gdk.KEY_i)[1][0]
        tab.vim.filter_key(True, tab.window.get_surface(),
                           display.get_default_seat().get_keyboard(), 0, key.keycode,
                           Gdk.ModifierType(0), key.group)
        self.assertIn("INSERT", tab.vim.get_command_bar_text())

    def find_switch(self, widget, title):
        if isinstance(widget, Adw.SwitchRow) and widget.get_title() == title:
            return widget
        child = widget.get_first_child()
        while child is not None:
            found = self.find_switch(child, title)
            if found is not None:
                return found
            child = child.get_next_sibling()
        return None
