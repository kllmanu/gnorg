"""Vim integration tests."""

from unittest.mock import patch
from unittest.mock import Mock

from editor_test_case import EditorTestCase
from vim import Gdk
from vim import Adw


class VimTests(EditorTestCase):
    def test_jj_escape_exits_real_vim_insert_mode_without_inserting_text(self):
        tab = self.app.new_tab()
        self.enter_vim_insert_mode(tab)
        for _ in range(2):
            self.assertTrue(tab.window._on_key_pressed(
                None, Gdk.KEY_j, 0, Gdk.ModifierType(0)))
        self.assertNotIn("INSERT", tab.vim.get_command_bar_text())
        self.assertEqual(tab.get_text(), "")
        self.assertFalse(tab.buffer.get_modified())
        self.assertFalse(tab._on_escape_sequence(Gdk.KEY_j, Gdk.ModifierType(0)))

    def test_jj_escape_single_j_timeout_and_other_keys_keep_text(self):
        tab = self.app.new_tab()
        self.enter_vim_insert_mode(tab)
        self.assertTrue(tab._on_escape_sequence(Gdk.KEY_j, Gdk.ModifierType(0)))
        self.wait_for(lambda: tab.get_text() == "j")
        self.assertIn("INSERT", tab.vim.get_command_bar_text())
        self.assertTrue(tab._on_escape_sequence(Gdk.KEY_j, Gdk.ModifierType(0)))
        self.assertFalse(tab._on_escape_sequence(Gdk.KEY_a, Gdk.ModifierType(0)))
        self.assertEqual(tab.get_text(), "jj")
        self.assertIn("INSERT", tab.vim.get_command_bar_text())
        self.assertFalse(tab._on_escape_sequence(Gdk.KEY_j, Gdk.ModifierType.CONTROL_MASK))
        tab._on_escape_sequence(Gdk.KEY_j, Gdk.ModifierType(0))
        tab._escape_started -= 1
        self.assertTrue(tab._on_escape_sequence(Gdk.KEY_j, Gdk.ModifierType(0)))
        self.assertIn("INSERT", tab.vim.get_command_bar_text())
        tab._flush_escape_prefix()
        self.assertEqual(tab.get_text(), "jjjj")

    def test_jj_escape_disabled_non_vim_and_focus_leave(self):
        tab = self.app.new_tab()
        self.enter_vim_insert_mode(tab)
        self.app.set_jj_escape(False)
        self.assertFalse(tab._on_escape_sequence(Gdk.KEY_j, Gdk.ModifierType(0)))
        self.app.set_jj_escape(True)
        self.assertTrue(tab._on_escape_sequence(Gdk.KEY_j, Gdk.ModifierType(0)))
        tab.window.utility_pane.open_button.grab_focus()
        self.wait_for(lambda: tab.get_text() == "j")
        tab._set_vim_enabled(False)
        self.assertFalse(tab._on_escape_sequence(Gdk.KEY_j, Gdk.ModifierType(0)))

    def test_configurable_escape_sequence_supports_distinct_keys_and_pending_prefix(self):
        tab = self.app.new_tab()
        self.enter_vim_insert_mode(tab)
        settings = Mock()
        with patch.object(self.app, "settings", settings):
            self.app.set_escape_sequence("jk")
            settings.set_string.assert_called_once_with("escape-sequence", "jk")
        self.assertTrue(tab._on_escape_sequence(Gdk.KEY_j, Gdk.ModifierType(0)))
        self.assertTrue(tab._on_escape_sequence(Gdk.KEY_k, Gdk.ModifierType(0)))
        self.assertNotIn("INSERT", tab.vim.get_command_bar_text())
        self.assertEqual(tab.get_text(), "")
        self.enter_vim_insert_mode(tab)
        self.app.set_escape_sequence("XY")
        self.assertTrue(tab._on_escape_sequence(Gdk.KEY_X, Gdk.ModifierType.SHIFT_MASK))
        self.app.set_escape_sequence("jk")
        self.assertEqual(tab.get_text(), "X")
        self.assertTrue(tab._on_escape_sequence(Gdk.KEY_j, Gdk.ModifierType(0)))
        self.assertTrue(tab._on_escape_sequence(Gdk.KEY_j, Gdk.ModifierType(0)))
        self.assertEqual(tab.get_text(), "Xj")
        self.assertTrue(tab._on_escape_sequence(Gdk.KEY_k, Gdk.ModifierType(0)))
        self.assertEqual(tab.get_text(), "Xj")
        self.assertNotIn("INSERT", tab.vim.get_command_bar_text())

    def test_escape_sequence_preferences_validate_before_applying(self):
        tab = self.app.new_tab()
        tab.present()
        self.app.show_preferences()
        dialog = tab.window.get_visible_dialog()

        def find_entry(widget):
            if isinstance(widget, Adw.EntryRow) and widget.get_title() == "Escape Sequence":
                return widget
            child = widget.get_first_child()
            while child:
                found = find_entry(child)
                if found is not None:
                    return found
                child = child.get_next_sibling()
            return None

        row = find_entry(dialog)
        self.assertEqual(row.get_text(), "jj")
        for invalid in ("", "j", "jjj", "j "):
            row.set_text(invalid)
            row.emit("apply")
            self.assertTrue(row.has_css_class("error"))
            self.assertEqual(self.app.escape_sequence, "jj")
        row.set_text("jk")
        row.emit("apply")
        self.assertFalse(row.has_css_class("error"))
        self.assertEqual(self.app.escape_sequence, "jk")
        dialog.close()

    def test_jj_escape_is_inactive_in_visual_mode(self):
        tab = self.app.new_tab()
        tab.buffer.set_text("* Example")
        tab.buffer.place_cursor(tab.buffer.get_start_iter())
        tab.present()
        self.wait_for(tab.view.get_mapped)
        display = Gdk.Display.get_default()
        key = display.map_keyval(Gdk.KEY_v)[1][0]
        tab.vim.filter_key(True, tab.window.get_surface(),
                           display.get_default_seat().get_keyboard(), 0, key.keycode,
                           Gdk.ModifierType(0), key.group)
        self.assertIn("VISUAL", tab.vim.get_command_bar_text())
        self.assertFalse(tab._on_escape_sequence(Gdk.KEY_j, Gdk.ModifierType(0)))
        self.assertIsNone(tab._escape_mark)

    def test_jj_escape_preferences_and_pending_input_on_setting_change(self):
        tab = self.app.new_tab()
        self.enter_vim_insert_mode(tab)
        tab._on_escape_sequence(Gdk.KEY_j, Gdk.ModifierType(0))
        settings = Mock()
        with patch.object(self.app, "settings", settings):
            self.app.set_jj_escape_delay(350)
            settings.set_int.assert_called_once_with("jj-escape-delay", 350)
        self.assertEqual(tab.get_text(), "j")
        self.assertIsNone(tab._escape_mark)
        tab.present()
        self.app.show_preferences()
        dialog = tab.window.get_visible_dialog()
        row = self.find_switch(dialog, "Key Sequence to Escape")
        self.assertTrue(row.get_active())
        row.set_active(False)
        self.assertFalse(self.app.jj_escape)
        dialog.close()
