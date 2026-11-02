"""Timestamps integration tests."""

from unittest.mock import patch

from editor_test_case import EditorTestCase
from vim import Gdk


class TimestampEditorTests(EditorTestCase):
    def test_timestamp_shortcut_inserts_active_timestamp_and_undoes_once(self):
        import org

        tab = self.app.new_tab()
        tab.buffer.set_text("* Meeting\nNotes: ")
        tab.buffer.place_cursor(tab.buffer.get_end_iter())
        tab.present()
        self.assertTrue(tab.window._on_key_pressed(
            None, Gdk.KEY_c, 0, Gdk.ModifierType.CONTROL_MASK))
        self.assertTrue(tab.window._on_key_pressed(None, Gdk.KEY_period, 0, Gdk.ModifierType(0)))
        popover = tab._active_date_popover
        self.assertIsNotNone(popover)
        popover.entry.set_text("2026-11-05 10:00-11:00 +1w")
        timestamp = popover.timestamp
        self.assertTrue(timestamp.active)
        popover._apply()
        self.wait_for(lambda: tab._active_date_popover is None)
        self.assertEqual(tab.get_text(), "* Meeting\nNotes: <2026-11-05 Thu 10:00-11:00 +1w>")
        self.assertTrue(tab.buffer.get_modified())
        self.assertEqual(
            tab.buffer.get_iter_at_mark(tab.buffer.get_insert()).get_offset(), len(tab.get_text()))
        self.assertEqual(org.parse_entries(tab.get_text())[0].timestamps, [timestamp])
        tab.buffer.undo()
        self.assertEqual(tab.get_text(), "* Meeting\nNotes: ")

    def test_timestamp_edits_existing_active_timestamp_without_remove_option(self):
        tab = self.app.new_tab()
        tab.buffer.set_text("Before <2026-11-05 Thu 10:00> after")
        tab.buffer.place_cursor(tab.buffer.get_iter_at_offset(12))
        tab.present()
        tab.insert_timestamp()
        popover = tab._active_date_popover
        self.assertEqual(popover.entry.get_text(), "2026-11-05 10:00")
        buttons = popover.get_child().get_last_child()
        self.assertIs(buttons.get_first_child(), popover.set_button)
        popover.entry.set_text("2026-11-06 12:30")
        popover._apply()
        self.wait_for(lambda: tab._active_date_popover is None)
        self.assertEqual(tab.get_text(), "Before <2026-11-06 Fri 12:30> after")
        tab.buffer.undo()
        self.assertEqual(tab.get_text(), "Before <2026-11-05 Thu 10:00> after")

    def test_timestamp_cancel_and_invalid_input_leave_buffer_unchanged(self):
        tab = self.app.new_tab()
        tab.buffer.set_text("Plain text without a heading")
        tab.buffer.set_modified(False)
        tab.present()
        tab.insert_timestamp()
        popover = tab._active_date_popover
        popover.entry.set_text("not a date")
        self.assertFalse(popover.set_button.get_sensitive())
        popover._apply()
        self.assertEqual(tab.get_text(), "Plain text without a heading")
        tab._keyboard_quit()
        self.wait_for(lambda: tab._active_date_popover is None)
        self.assertFalse(tab.buffer.get_modified())

    def test_shift_arrows_adjust_timestamp_and_preserve_cursor_and_undo(self):
        tab = self.app.new_tab()
        original = "* Meeting\n<2026-11-02 Mon 09:30>"
        tab.buffer.set_text(original)
        tab.buffer.place_cursor(tab.buffer.get_iter_at_offset(28))
        tab.present()
        self.assertTrue(tab.window._on_key_pressed(
            None, Gdk.KEY_Up, 0, Gdk.ModifierType.SHIFT_MASK))
        self.assertEqual(tab.get_text(), "* Meeting\n<2026-11-02 Mon 10:30>")
        self.assertEqual(tab.buffer.get_iter_at_mark(tab.buffer.get_insert()).get_offset(), 28)
        self.assertTrue(tab.window._on_key_pressed(
            None, Gdk.KEY_Down, 0, Gdk.ModifierType.SHIFT_MASK))
        self.assertEqual(tab.get_text(), original)
        tab.buffer.undo()
        self.assertEqual(tab.get_text(), "* Meeting\n<2026-11-02 Mon 10:30>")
        tab.buffer.undo()
        self.assertEqual(tab.get_text(), original)
        tab.buffer.place_cursor(tab.buffer.get_iter_at_offset(15))
        self.assertTrue(tab.window._on_key_pressed(
            None, Gdk.KEY_Up, 0, Gdk.ModifierType.SHIFT_MASK))
        self.assertEqual(tab.get_text(), "* Meeting\n<2026-11-03 Tue 09:30>")
        tab.buffer.place_cursor(tab.buffer.get_start_iter())
        self.assertFalse(tab.window._on_key_pressed(
            None, Gdk.KEY_Up, 0, Gdk.ModifierType.SHIFT_MASK))

    def test_timestamp_reports_conflicting_changes_instead_of_overwriting(self):
        tab = self.app.new_tab()
        tab.buffer.set_text("<2026-11-05 Thu>")
        tab.buffer.place_cursor(tab.buffer.get_iter_at_offset(5))
        tab.present()
        tab.insert_timestamp()
        popover = tab._active_date_popover
        popover.entry.set_text("2026-11-06")
        tab.buffer.insert(tab.buffer.get_iter_at_offset(7), "changed")
        text = tab.get_text()
        with patch.object(tab, "toast") as toast:
            popover._apply()
            toast.assert_called_once_with("The text has changed. Insert the timestamp again.")
        self.assertEqual(tab.get_text(), text)
        self.wait_for(lambda: tab._active_date_popover is None)
