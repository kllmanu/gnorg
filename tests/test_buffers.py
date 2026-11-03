"""Buffers integration tests."""

import os
import tempfile
from unittest.mock import patch
from unittest.mock import Mock

from editor_test_case import EditorTestCase
from vim import Gio
from vim import Gdk
from bufferpicker import BufferPicker, matches_buffer


class BufferPickerTests(EditorTestCase):
    def test_buffer_picker_has_no_remove_or_close_buttons(self):
        from vim import Gtk

        first = self.app.new_tab()
        first.file = Gio.File.new_for_path("/org/hidden.org")
        first.buffer.set_text("* Unsaved edits")
        second = self.app.new_tab()
        second.present()
        picker = BufferPicker(self.app.editor_tabs(), second)
        picker.present(second.window)
        row = picker.listbox.get_row_at_index(0)

        def find_remove(widget):
            if isinstance(widget, Gtk.Button) and widget.get_icon_name() == "window-close-symbolic":
                return widget
            child = widget.get_first_child()
            while child:
                found = find_remove(child)
                if found is not None:
                    return found
                child = child.get_next_sibling()
            return None

        self.assertIsNone(find_remove(row))
        self.assertIsNone(find_remove(picker.listbox))
        self.assertIn(first, picker.results)
        self.assertIn(first, self.app.editor_tabs())
        self.assertEqual(first.get_text(), "* Unsaved edits")
        self.assertTrue(first.buffer.get_modified())
        self.assertIs(second.window.active_tab, second)
        self.assertIsNotNone(picker.get_parent())
        picker.close()
        self.wait_for(lambda: picker.get_parent() is None)
        reopened = BufferPicker(self.app.editor_tabs(), second, [first.file])
        self.assertIn(first, reopened.results)
        self.assertEqual(first.get_text(), "* Unsaved edits")
        self.assertTrue(first.buffer.get_modified())

    def test_buffer_picker_in_agenda_uses_utility_pane_and_restores_focus(self):
        tab = self.app.new_tab()
        tab.present()
        self.app.show_agenda()
        agenda = self.app.agenda
        picker = BufferPicker(self.app.editor_tabs(), tab)
        picker.present(tab.window)
        self.assertIs(picker.get_parent(), tab.window.utility_pane.open_button)
        picker.close()
        self.wait_for(lambda: picker.get_parent() is None)
        self.assertIs(tab.window.get_focus(), agenda.view)

    def test_buffer_picker_with_hidden_pane_uses_sidebar_toggle(self):
        tab = self.app.new_tab()
        tab.present()
        tab.window.split_view.set_show_sidebar(False)
        picker = BufferPicker(self.app.editor_tabs(), tab)
        picker.present(tab.window)
        self.assertIs(picker.get_parent(), tab.header.sidebar_toggle)
        picker.close()
        self.wait_for(lambda: picker.get_parent() is None)
        self.assertIs(tab.window.get_focus(), tab.view)

    def test_buffer_completion_matches_terms_in_any_order(self):
        self.assertTrue(matches_buffer("WORK test", "test.org", "/org/work/test.org"))
        self.assertTrue(matches_buffer("test work", "test.org", "/org/work/test.org"))
        self.assertFalse(matches_buffer("work absent", "test.org", "/org/work/test.org"))

    def test_buffer_shortcut_after_closing_agenda(self):
        tab = self.app.new_tab()
        tab.present()
        self.app.show_agenda()
        self.app.agenda.close()
        self.assertIsNotNone(self.app.agenda)
        self.assertIs(tab.window.get_focus(), tab.view)
        tab.window.utility_pane.open_button.grab_focus()
        with patch.object(tab._org_key_sequences, "commands",
                          {**tab._org_key_sequences.commands, "C-x b": Mock()}) as commands:
            self.assertTrue(tab.window._on_key_pressed(
                None, Gdk.KEY_x, 0, Gdk.ModifierType.CONTROL_MASK))
            self.assertTrue(tab.window._on_key_pressed(
                None, Gdk.KEY_b, 0, Gdk.ModifierType(0)))
            commands["C-x b"].assert_called_once_with()

    def test_recent_history_limit_order_and_persistence(self):
        settings = Mock()
        with patch.object(self.app, "settings", settings):
            for i in range(25):
                self.app.remember_file(Gio.File.new_for_path(f"/org/{i}.org"))
            file = Gio.File.new_for_path("/org/10.org")
            self.app.remember_file(file)
            self.assertEqual(len(self.app.recent_files), 20)
            self.assertEqual(self.app.recent_files[0], file.get_uri())
            self.assertEqual(len(set(self.app.recent_files)), 20)
            settings.set_strv.assert_called_with("recent-files", self.app.recent_files)

    def test_picker_recent_files_filter_and_open(self):
        with tempfile.TemporaryDirectory() as directory:
            recent = Gio.File.new_for_path(os.path.join(directory, "work-recent.org"))
            with open(recent.get_path(), "w") as stream:
                stream.write("* Recent")
            self._check_picker_recent_file(recent)

    def _check_picker_recent_file(self, recent):
        tab = self.app.new_tab()
        tab.file = Gio.File.new_for_path("/org/open.org")
        picker = BufferPicker([tab], tab, [tab.file, recent])
        tab.present()
        picker.present(tab.window)
        self.assertEqual(picker.results, [tab, recent])
        picker.search.set_text("work recent")
        self.assertEqual(picker.results, [recent])
        with patch.object(self.app, "open_file") as open_file:
            picker._activate_selected()
            open_file.assert_called_once_with(recent)

    def test_buffer_switcher_prunes_deleted_recent_files_and_keeps_open_buffers(self):
        with tempfile.TemporaryDirectory() as directory:
            existing = Gio.File.new_for_path(os.path.join(directory, "existing.org"))
            missing = Gio.File.new_for_path(os.path.join(directory, "missing.org"))
            with open(existing.get_path(), "w") as stream:
                stream.write("* Existing")
            tab = self.app.new_tab()
            tab.file = missing
            tab.buffer.set_text("* Unsaved")
            tab.present()
            self.app.recent_files = [missing.get_uri(), existing.get_uri()]
            settings = Mock()
            with (patch.object(self.app, "settings", settings),
                  patch("vim.BufferPicker") as picker):
                self.app.show_buffers()
                settings.set_strv.assert_called_once_with("recent-files", [existing.get_uri()])
                self.assertEqual([file.get_uri() for file in picker.call_args.args[2]],
                                 [existing.get_uri()])
                self.assertIn(tab, picker.call_args.args[0])
            self.assertEqual(self.app.recent_files, [existing.get_uri()])
            picker = BufferPicker([tab], tab, [existing, missing])
            self.assertEqual(picker.results, [tab, existing])
            os.unlink(existing.get_path())
            picker.search.set_text("existing")
            self.assertEqual(picker.results, [])
            picker.search.set_text("")
            self.assertEqual(picker.results, [tab])

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
        picker._on_key_pressed(None, Gdk.KEY_n, 0, Gdk.ModifierType.CONTROL_MASK)
        self.assertEqual(picker.listbox.get_selected_row().get_index(), 1)
        picker._on_key_pressed(None, Gdk.KEY_p, 0, Gdk.ModifierType.CONTROL_MASK)
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
        self.wait_for(lambda: picker.get_parent() is None)
        self.assertIs(first.window.get_focus(), first.view)
