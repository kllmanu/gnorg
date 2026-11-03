"""Startup directive editing through the inline button."""

from editor_test_case import EditorTestCase
from unittest.mock import Mock
from vim import Gdk, Graphene, Gtk


class StartupTests(EditorTestCase):
    def test_edit_popovers_point_to_line_end_without_moving_cursor(self):
        tab = self.app.new_tab()
        tab.buffer.set_text("#+STARTUP: showall\n* TODO Heading\n"
                            "SCHEDULED: <2026-11-04> extra text\n"
                            "<2026-11-05> trailing text\n")
        tab.present()
        self.wait_for(lambda: 0 in tab.org_startup_edit.buttons)
        for line, open_popover, attribute in (
                (0, lambda: tab.edit_startup(0, tab.org_startup_edit.buttons[0]),
                 "_active_startup_popover"),
                (1, tab.edit_tags, "_active_tag_popover"),
                (2, lambda: tab.edit_planning("SCHEDULED"), "_active_date_popover"),
                (3, tab.insert_timestamp, "_active_date_popover")):
            iterator = tab.buffer.get_iter_at_line(line)[1]
            tab.buffer.place_cursor(iterator)
            end = iterator.copy()
            end.forward_to_line_end()
            location = tab.view.get_iter_location(end)
            x, y = tab.view.buffer_to_window_coords(
                Gtk.TextWindowType.WIDGET, location.x, location.y)
            _ok, point = tab.view.compute_point(tab.editor_box, Graphene.Point().init(x, y))
            open_popover()
            _ok, anchor = getattr(tab, attribute).get_pointing_to()
            self.assertAlmostEqual(anchor.x, point.x, delta=1)
            self.assertAlmostEqual(anchor.y, point.y, delta=1)
            self.assertEqual(tab.buffer.get_iter_at_mark(tab.buffer.get_insert()).get_line_offset(), 0)
            tab._keyboard_quit()
    def test_startup_text_click_and_control_c_control_c_open_same_popover(self):
        tab = self.app.new_tab()
        tab.buffer.set_text("#+STARTUP: overview\n* Heading\nBody\n")
        tab.present()
        editor = tab.org_startup_edit
        self.wait_for(lambda: 0 in editor._regions)
        left, top, _right, bottom = editor._regions[0]
        gesture = Mock()
        editor._pressed(gesture, 1, left + 2, (top + bottom) / 2)
        gesture.set_state.assert_called_once_with(Gtk.EventSequenceState.CLAIMED)
        self.assertIsNotNone(tab._active_startup_popover)
        tab._keyboard_quit()
        tab.buffer.place_cursor(tab.buffer.get_start_iter())
        for _ in range(2):
            tab._org_key_sequences._on_key_pressed(
                None, Gdk.KEY_c, 0, Gdk.ModifierType.CONTROL_MASK)
        self.assertIsNotNone(tab._active_startup_popover)
        dropdown = tab._active_startup_popover.get_child().get_first_child().get_next_sibling()
        self.assertEqual(dropdown.get_selected(), 0)

    def test_startup_icon_changes_folding_preserves_options_and_undoes(self):
        tab = self.app.new_tab()
        text = "#+STARTUP: overview indent\n* Parent\nBody\n** Child\nDetails\n"
        tab.buffer.set_text(text)
        tab.present()
        self.wait_for(lambda: 0 in tab.org_startup_edit.buttons)
        tab.org_startup_edit.buttons[0].emit("clicked")
        dialog = tab._active_startup_popover
        dropdown = dialog.get_child().get_first_child().get_next_sibling()
        self.assertIsInstance(dropdown, Gtk.DropDown)
        self.assertEqual(dropdown.get_selected(), 0)
        dropdown.set_selected(2)
        dialog.get_child().get_last_child().get_last_child().emit("clicked")
        self.assertEqual(tab.get_text(), text.replace("overview", "showall"))
        self.wait_for(lambda: tab.org_fold.mode == "showall")
        self.assertFalse(tab.buffer.get_iter_at_line(2)[1].has_tag(tab.org_fold.hidden))
        tab.buffer.undo()
        self.assertEqual(tab.get_text(), text)

    def test_startup_dialog_cancel_and_literal_block(self):
        tab = self.app.new_tab()
        text = "#+startup: content\n#+BEGIN_SRC org\n#+STARTUP: overview\n#+END_SRC\n"
        tab.buffer.set_text(text)
        tab.present()
        self.wait_for(lambda: len(tab.org_startup_edit.buttons) == 1)
        self.assertEqual(set(tab.org_startup_edit.buttons), {0})
        editor = tab.org_startup_edit
        editor._leave()
        button = editor.buttons[0]
        self.assertEqual(button.get_opacity(), 0)
        self.assertFalse(button.get_can_target())
        self.wait_for(lambda: 0 in editor._regions)
        left, top, right, bottom = editor._regions[0]
        editor._motion(None, left + 1, (top + bottom) / 2)
        self.assertEqual(tab.view.get_cursor().get_name(), "pointer")
        self.assertEqual(button.get_opacity(), 1)
        self.assertTrue(button.get_can_target())
        editor._motion(None, right - 1, (top + bottom) / 2)
        self.assertEqual(button.get_opacity(), 1)
        editor._leave()
        self.assertEqual(button.get_opacity(), 0)
        self.assertEqual(tab.view.get_cursor().get_name(), "text")
        tab.org_startup_edit.buttons[0].emit("clicked")
        dialog = tab._active_startup_popover
        dialog.get_child().get_first_child().get_next_sibling().set_selected(3)
        tab._keyboard_quit()
        self.assertIsNone(tab._active_startup_popover)
        self.assertEqual(tab.get_text(), text)
