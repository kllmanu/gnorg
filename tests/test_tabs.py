import os
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir, "src"))

from vim import Adw, Gdk, Gio, GLib, Gtk, OrganizerApplication, ZOOM_MAX, ZOOM_MIN  # noqa: E402
from bufferpicker import BufferPicker, matches_buffer  # noqa: E402
from agenda import event_lanes, event_minutes  # noqa: E402


@unittest.skipUnless(Gtk.init_check(), "GTK display required")
class TabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = OrganizerApplication()
        cls.app.set_application_id("io.github.kllmanu.Organizer.TabTests")
        cls.app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
        with patch.object(OrganizerApplication, "_load_settings", return_value=None):
            cls.app.register(None)

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

    def test_application_branding(self):
        tab = self.app.new_tab()
        self.assertEqual(GLib.get_application_name(), "GNORG")
        self.assertEqual(tab.window.get_title(), "Untitled – GNORG")
        with patch("vim.Adw.AboutDialog") as about:
            self.app._on_about()
            self.assertEqual(about.call_args.kwargs["application_name"], "GNORG")
            self.assertIn("Gnorg is not org", about.call_args.kwargs["comments"])
            about.return_value.present.assert_called_once_with(self.app.get_active_window())

    def open_heading_tags(self, tab):
        tab.present()
        self.wait_for(tab.view.get_mapped)
        for _ in range(2):
            tab._org_key_sequences._on_key_pressed(
                None, Gdk.KEY_c, 0, Gdk.ModifierType.CONTROL_MASK)
        popover = tab._active_tag_popover
        self.assertIsNotNone(popover)
        return popover, popover.get_child().get_first_child().get_next_sibling()

    def test_heading_tag_editor_applies_removes_and_undoes(self):
        tab = self.app.new_tab()
        original = "** TODO [#A] Report :work:\nBody\n"
        tab.buffer.set_text(original)
        tab.buffer.place_cursor(tab.buffer.get_start_iter())
        popover, entry = self.open_heading_tags(tab)
        self.assertEqual(entry.get_text(), "work")
        entry.set_text("home:project:home")
        entry.emit("activate")
        self.assertIsNone(tab._active_tag_popover)
        self.assertEqual(tab.get_text(), "** TODO [#A] Report :home:project:\nBody\n")
        tab.buffer.undo()
        self.assertEqual(tab.get_text(), original)
        popover, entry = self.open_heading_tags(tab)
        entry.set_text("")
        entry.emit("activate")
        self.assertEqual(tab.get_text(), "** TODO [#A] Report\nBody\n")

    def test_heading_tag_editor_validation_cancel_and_stale_heading(self):
        tab = self.app.new_tab()
        tab.buffer.set_text("* Report :work:\n")
        tab.buffer.place_cursor(tab.buffer.get_start_iter())
        original = tab.get_text()
        popover, entry = self.open_heading_tags(tab)
        entry.set_text("bad/tag")
        entry.emit("activate")
        self.assertEqual(tab.get_text(), original)
        self.assertIs(tab._active_tag_popover, popover)
        entry.set_text("home")
        tab._keyboard_quit()
        self.assertEqual(tab.get_text(), original)
        popover, entry = self.open_heading_tags(tab)
        tab.buffer.insert(tab.buffer.get_start_iter(), "changed")
        with patch.object(tab, "toast") as toast:
            entry.emit("activate")
            toast.assert_called_once()
        self.assertEqual(tab.get_text(), "changed" + original)

    def test_confirm_org_item_still_toggles_checkboxes(self):
        tab = self.app.new_tab()
        tab.buffer.set_text("* Report\n- [ ] Task\n")
        tab.buffer.place_cursor(tab.buffer.get_iter_at_line(1)[1])
        tab.confirm_org_item()
        self.assertEqual(tab.get_text(), "* Report\n- [X] Task\n")
        self.assertIsNone(tab._active_tag_popover)

    def test_editor_has_padding_on_all_sides(self):
        tab = self.app.new_tab()
        self.assertEqual((tab.view.get_top_margin(), tab.view.get_bottom_margin(),
                          tab.view.get_left_margin(), tab.view.get_right_margin()),
                         (16, 16, 16, 16))

    def test_padding_slider_updates_open_and_new_editors_and_persists(self):
        tab = self.app.new_tab()
        tab.present()
        self.app.show_preferences()
        dialog = tab.window.get_visible_dialog()

        def find_scale(widget):
            if isinstance(widget, Gtk.Scale):
                return widget
            child = widget.get_first_child()
            while child is not None:
                result = find_scale(child)
                if result is not None:
                    return result
                child = child.get_next_sibling()
            return None

        scale = find_scale(dialog)
        self.assertIsNotNone(scale)
        self.assertEqual(scale.get_value(), 16)
        with patch.object(self.app, "settings", Mock()) as settings:
            scale.set_value(28)
            settings.set_int.assert_called_once_with("editor-padding", 28)
        dialog.close()
        other = self.app.new_tab()
        for editor in (tab, other):
            self.assertEqual((editor.view.get_top_margin(), editor.view.get_bottom_margin(),
                              editor.view.get_left_margin(), editor.view.get_right_margin()),
                             (28, 28, 28, 28))
        self.app.set_editor_padding(0)
        self.assertEqual(tab.view.get_left_margin(), 0)

    def test_agenda_padding_updates_existing_and_new_views(self):
        tab = self.app.new_tab()
        self.app.show_weekly_agenda(tab.window)
        agenda = tab.window.weekly_agenda
        self.assertEqual(agenda.listbox.get_margin_top(), 16)
        with patch.object(self.app, "settings", Mock()) as settings:
            self.app.set_agenda_padding(32)
            settings.set_int.assert_called_once_with("agenda-padding", 32)
        for margin in (agenda.listbox.get_margin_top(), agenda.listbox.get_margin_bottom(),
                       agenda.listbox.get_margin_start(), agenda.listbox.get_margin_end()):
            self.assertEqual(margin, 32)
        self.assertEqual(tab.view.get_left_margin(), 16)
        self.app.set_agenda_padding(0)
        self.assertEqual(agenda.listbox.get_margin_start(), 0)

    def test_heading_tags_align_right_after_resize_without_changing_text(self):
        tab = self.app.new_tab()
        text = "* First :work:\n** Second :home:project:\nBody :not_a_heading:\n"
        tab.buffer.set_text(text)
        tab.buffer.set_modified(False)
        tab.present()
        self.wait_for(tab.view.get_mapped)

        def aligned():
            right = tab.view.get_visible_rect().width - tab.view.get_right_margin() - 2
            return right > 100 and all(abs(tab.view.get_iter_location(
                tab.buffer.get_iter_at_line_offset(line, len(heading))[1]).x - right) <= 2
                for line, heading in enumerate(text.splitlines()[:2]))

        self.wait_for(aligned)
        adjustment = tab.view.get_hadjustment()
        self.wait_for(lambda: adjustment.get_upper() <= adjustment.get_page_size())
        old_width = tab.view.get_width()
        tab.window.set_default_size(tab.window.get_width() + 180, tab.window.get_height())
        self.wait_for(lambda: tab.view.get_width() != old_width)
        self.wait_for(aligned)
        self.wait_for(lambda: adjustment.get_upper() <= adjustment.get_page_size())
        old_width = tab.view.get_width()
        tab.window.set_default_size(tab.window.get_width() - 260, tab.window.get_height())
        self.wait_for(lambda: tab.view.get_width() != old_width)
        self.wait_for(aligned)
        self.wait_for(lambda: adjustment.get_upper() <= adjustment.get_page_size())
        self.assertEqual(tab.get_text(), text)
        previous_zoom = self.app.zoom
        self.addCleanup(lambda: self.app.set_zoom(previous_zoom, notify=False))
        display = Gdk.Display.get_default()
        Gtk.StyleContext.remove_provider_for_display(display, self.app.zoom_css)
        Gtk.StyleContext.add_provider_for_display(
            display, self.app.zoom_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        for zoom in (150, 200, 100):
            if self.app.zoom == zoom:
                continue
            old_font_size = tab.view.create_pango_layout(" ").get_pixel_size()
            self.app.set_zoom(zoom, notify=False)
            self.wait_for(lambda: tab.view.create_pango_layout(" ").get_pixel_size()
                          != old_font_size)
            self.wait_for(aligned)
            self.wait_for(lambda: adjustment.get_upper() <= adjustment.get_page_size())
        self.assertFalse(tab.buffer.get_modified())
        text = "* First :longer_tag:another:\n** Second :home:project:\n"
        tab.buffer.set_text(text)
        self.wait_for(aligned)
        self.assertEqual(tab.get_text(), text)
        tab.buffer.insert(tab.buffer.get_end_iter(), "A genuinely long line " * 100)
        self.wait_for(lambda: adjustment.get_upper() > adjustment.get_page_size())

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

    def enter_vim_insert_mode(self, tab):
        tab.present()
        self.wait_for(tab.view.get_mapped)
        display = Gdk.Display.get_default()
        key = display.map_keyval(Gdk.KEY_i)[1][0]
        tab.vim.filter_key(True, tab.window.get_surface(),
                           display.get_default_seat().get_keyboard(), 0, key.keycode,
                           Gdk.ModifierType(0), key.group)
        self.assertIn("INSERT", tab.vim.get_command_bar_text())

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

    def test_visual_line_selection_skips_folded_bodies_in_both_directions(self):
        tab = self.app.new_tab()
        text = "* First\nBody one\nBody two\n* Second\nBody three\n* Third\n"
        tab.buffer.set_text(text)
        tab.buffer.set_modified(False)
        tab.buffer.place_cursor(tab.buffer.get_start_iter())
        tab.org_fold.cycle(global_cycle=True)
        tab.present()
        self.wait_for(tab.view.get_mapped)
        display = Gdk.Display.get_default()

        def press(keyval, modifiers=Gdk.ModifierType(0)):
            key = display.map_keyval(keyval)[1][0]
            tab.vim.filter_key(True, tab.window.get_surface(),
                               display.get_default_seat().get_keyboard(), 0, key.keycode,
                               modifiers, key.group)

        press(Gdk.KEY_V, Gdk.ModifierType.SHIFT_MASK)
        for keyval, expected_line in (
                (Gdk.KEY_j, 3), (Gdk.KEY_j, 5),
                (Gdk.KEY_k, 3), (Gdk.KEY_k, 0), (Gdk.KEY_j, 3)):
            press(keyval)
            cursor = tab.org_fold._visual_cursor
            self.assertIsNotNone(cursor)
            self.assertEqual(tab.buffer.get_iter_at_mark(cursor).get_line(), expected_line)
            self.assertTrue(tab.buffer.get_has_selection())
            self.assertIn("VISUAL LINE", tab.vim.get_command_bar_text())
        self.assertEqual(tab.get_text(), text)
        self.assertFalse(tab.buffer.get_modified())
        press(Gdk.KEY_d)
        self.assertEqual(tab.get_text(), "* Third\n")
        self.assertTrue(tab.buffer.get_modified())
        tab.buffer.undo()
        self.assertEqual(tab.get_text(), text)

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

    def test_file_context_remove_preserves_disk_and_can_be_reopened(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "events.org")
            file = Gio.File.new_for_path(path)
            with open(path, "w") as stream:
                stream.write("* Event <2026-11-02>\n")
            with patch.object(self.app, "agenda_directory", return_value=directory):
                tab = self.app.open_file(file)
                self.wait_for(lambda: bool(tab.get_text()))
                window = tab.window
                pane = window.utility_pane
                pane.refresh()
                row = next(row for row in pane.rows if row.file.equal(file))
                pane._show_file_menu(row, 10, 10)
                menu = pane._context_menu
                buttons = []
                child = menu.get_child().get_first_child()
                while child:
                    buttons.append(child)
                    child = child.get_next_sibling()
                self.assertEqual([button.get_child().get_label() for button in buttons],
                                 ["Rename…", "Change Color…", "Remove", "Move to Trash…"])
                self.assertTrue(all(button.get_child().get_xalign() == 0 for button in buttons))
                self.assertTrue(all(button.get_child().has_css_class("file-action-label")
                                    for button in buttons))
                buttons[2].emit("clicked")
                self.wait_for(lambda: not pane.rows and pane._context_menu is None)
                self.assertTrue(os.path.isfile(path))
                self.assertTrue(self.app.file_removed(file))
                self.assertEqual(self.app.agenda_entries()[2], [])
                self.assertNotIn(tab, window.documents())
                reopened = self.app.open_file(file)
                self.wait_for(lambda: bool(reopened.get_text()))
                self.assertFalse(self.app.file_removed(file))
                pane.refresh()
                self.assertEqual(len(pane.rows), 1)

    def test_file_remove_prompts_for_unsaved_changes_and_cancel_keeps_file(self):
        tab = self.app.new_tab()
        tab._set_file(Gio.File.new_for_path("/org/test.org"), "* Original")
        tab.buffer.set_text("* Unsaved")
        self.app.remove_document(tab.file, tab, tab.window)
        dialog = tab.window.get_visible_dialog()
        self.assertIsNotNone(dialog)
        dialog.emit("response", "cancel")
        self.assertFalse(self.app.file_removed(tab.file))
        self.assertIn(tab, self.app.editor_tabs())
        self.assertEqual(tab.get_text(), "* Unsaved")

    def test_file_trash_requires_confirmation_and_failure_keeps_document(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "test.org")
            with open(path, "w") as stream:
                stream.write("* Original")
            tab = self.app.open_file(Gio.File.new_for_path(path))
            self.wait_for(lambda: bool(tab.get_text()))
            pane = tab.window.utility_pane
            pane.refresh()
            row = next(row for row in pane.rows if row.document is tab)
            with patch.object(Gio.File, "trash_async") as trash:
                pane._confirm_trash(row)
                tab.window.get_visible_dialog().emit("response", "cancel")
                trash.assert_not_called()
            source = Mock()
            source.trash_finish.side_effect = GLib.Error("Trash unavailable")
            with (patch.object(Gio.File, "trash_async",
                               side_effect=lambda _priority, _cancel, callback: callback(source, None)),
                  patch.object(tab, "toast") as toast):
                self.app.remove_document(tab.file, tab, tab.window, trash=True)
                toast.assert_called_once()
            self.assertTrue(os.path.isfile(path))
            self.assertIn(tab, self.app.editor_tabs())
            self.assertFalse(self.app.file_removed(tab.file))
            source.trash_finish.side_effect = None
            source.trash_finish.return_value = True
            with patch.object(Gio.File, "trash_async",
                              side_effect=lambda _priority, _cancel, callback: callback(source, None)):
                self.app.remove_document(tab.file, tab, tab.window, trash=True)
            self.assertNotIn(tab, self.app.editor_tabs())
            self.assertTrue(self.app.file_removed(tab.file))

    def test_new_file_save_appends_org_extension_and_list_hides_it(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(self.app, "agenda_directory", return_value=directory):
                tab = self.app.new_tab()
                tab.buffer.set_text("* New")
                tab.save_to(Gio.File.new_for_path(os.path.join(directory, "notes")))
                self.wait_for(lambda: tab.file is not None)
                self.assertEqual(tab.path, os.path.join(directory, "notes.org"))
                self.assertFalse(os.path.exists(os.path.join(directory, "notes")))
                pane = tab.window.utility_pane
                pane.refresh()
                row = next(row for row in pane.rows if row.document is tab)
                self.assertEqual(row.get_title(), "notes")
                self.assertEqual(row.get_tooltip_text(), tab.path)
                tab.buffer.set_text("* Updated")
                tab.save()
                self.wait_for(lambda: not tab.buffer.get_modified())
                self.assertFalse(os.path.exists(tab.path + ".org"))
                other = tab.window.new_tab()
                other.buffer.set_text("* Do not overwrite")
                with patch.object(other, "toast") as toast:
                    other.save_to(Gio.File.new_for_path(os.path.join(directory, "notes")))
                    toast.assert_called_once()
                self.assertIsNone(other.file)
                with open(tab.path) as stream:
                    self.assertEqual(stream.read(), "* Updated\n")

    def test_file_rename_preserves_unsaved_text_order_color_and_visibility(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "before.org")
            renamed = os.path.join(directory, "after.org")
            with open(path, "w") as stream:
                stream.write("* Disk")
            with patch.object(self.app, "agenda_directory", return_value=directory):
                tab = self.app.open_file(Gio.File.new_for_path(path))
                self.wait_for(lambda: bool(tab.get_text()))
                tab.buffer.set_text("* Unsaved")
                pane = tab.window.utility_pane
                pane.refresh()
                old_key = tab.file.get_uri()
                self.app.set_calendar_file_color(tab.file, "#123456")
                self.app.set_agenda_file_visible(path, False)
                self.app.rename_document(tab.file, "after", tab.window)
                self.wait_for(lambda: tab.path == renamed)
                self.assertFalse(os.path.exists(path))
                with open(renamed) as stream:
                    self.assertEqual(stream.read(), "* Disk")
                self.assertEqual(tab.get_text(), "* Unsaved")
                self.assertTrue(tab.buffer.get_modified())
                self.assertEqual(self.app.calendar_file_color(renamed), "#123456")
                self.assertFalse(self.app.agenda_file_visible(renamed))
                self.assertNotIn(old_key, self.app.utility_file_order)
                self.assertIn(tab.file.get_uri(), self.app.utility_file_order)
                collision = os.path.join(directory, "existing.org")
                with open(collision, "w") as stream:
                    stream.write("* Keep")
                with patch.object(tab, "toast") as toast:
                    self.app.rename_document(tab.file, "existing.org", tab.window)
                    self.wait_for(lambda: toast.called)
                self.assertEqual(tab.path, renamed)
                with open(collision) as stream:
                    self.assertEqual(stream.read(), "* Keep")
                pane.refresh()
                row = next(row for row in pane.rows if row.document is tab)
                pane._rename_file(row)
                dialog = tab.window.get_visible_dialog()
                entry = dialog.get_extra_child()
                self.assertEqual(entry.get_text(), "after")
                self.assertFalse(dialog.get_response_enabled("rename"))
                entry.set_text("../invalid.org")
                self.assertFalse(dialog.get_response_enabled("rename"))
                entry.set_text("valid.org")
                self.assertTrue(dialog.get_response_enabled("rename"))
                dialog.emit("response", "cancel")
                self.assertEqual(tab.path, renamed)

    def test_custom_file_color_updates_dot_and_event_and_is_saved(self):
        import datetime as dt
        from gi.repository import GdkPixbuf

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "event.org")
            with open(path, "w") as stream:
                stream.write(f"* Event <{dt.date.today().isoformat()}>\n")
            with patch.object(self.app, "agenda_directory", return_value=directory):
                tab = self.app.open_file(Gio.File.new_for_path(path))
                self.wait_for(lambda: bool(tab.get_text()))
                self.app.show_agenda(tab.window)
                settings = Mock()
                with patch.object(self.app, "settings", settings):
                    self.app.set_calendar_file_color(tab.file, "#123456")
                    settings.set_value.assert_called_once()
                    self.assertEqual(settings.set_value.call_args.args[1].unpack(),
                                     {tab.file.get_uri(): "#123456"})
                self.wait_for(lambda: tab.window.agenda.entry_buttons[0][1].has_css_class(
                    "custom-color-123456"))
                button = tab.window.agenda.entry_buttons[0][1]
                self.wait_for(lambda: button.get_mapped() and button.get_width() > 0)
                self.wait_for(lambda: Gtk.WidgetPaintable.new(button).get_current_image()
                              .get_intrinsic_width() > 0)
                image_path = os.path.join(directory, "event.png")

                def rendered_color_matches():
                    snapshot = Gtk.Snapshot.new()
                    Gtk.WidgetPaintable.new(button).snapshot(
                        snapshot, button.get_width(), button.get_height())
                    texture = tab.window.get_renderer().render_texture(snapshot.to_node(), None)
                    texture.save_to_png(image_path)
                    pixels = GdkPixbuf.Pixbuf.new_from_file(image_path)
                    data = pixels.get_pixels()
                    return any(
                        data[y * pixels.get_rowstride() + x * pixels.get_n_channels():
                             y * pixels.get_rowstride() + x * pixels.get_n_channels() + 3]
                        == b"\x12\x34\x56"
                        for y in range(pixels.get_height()) for x in range(pixels.get_width()))

                self.wait_for(rendered_color_matches)
                pane = tab.window.utility_pane
                pane.refresh()
                row = next(row for row in pane.rows if row.document is tab)
                with patch("utilitypane.Gtk.ColorDialog") as chooser:
                    pane._choose_file_color(row)
                    color = Gdk.RGBA()
                    color.parse("#abcdef")
                    chooser.return_value.choose_rgba_finish.return_value = color
                    callback = chooser.return_value.choose_rgba.call_args.args[3]
                    callback(chooser.return_value, None)
                self.assertEqual(self.app.calendar_file_color(path), "#abcdef")

    def test_utility_pane_replaces_tab_bar_and_selects_documents(self):
        first = self.app.new_tab()
        first.buffer.set_text("* First")
        second = self.app.new_tab()
        window = first.window
        pane = window.utility_pane
        self.assertIs(window.split_view.get_sidebar(), pane)
        self.assertIs(window.stack.get_child_by_name("files"), window.tabs)
        pane.refresh()
        row = next(row for row in pane.rows if row.document is first)
        self.assertEqual(row.get_title(), "• Untitled")
        self.assertIsNone(row.visibility)
        self.app.show_agenda(window)
        pane.listbox.emit("row-activated", row)
        self.assertIs(window.active_tab, first)
        self.assertEqual(window.stack.get_visible_child_name(), "files")
        self.assertEqual(first.get_text(), "* First")
        self.assertIn(second, window.documents())

    def test_utility_pane_highlights_only_current_document_across_views(self):
        first = self.app.new_tab()
        first.buffer.set_text("* First")
        second = self.app.new_tab()
        second.buffer.set_text("* Second")
        window = first.window
        pane = window.utility_pane
        pane.refresh()

        def highlighted():
            return [row.document for row in pane.rows if row.has_css_class("current-document")]

        self.assertEqual(highlighted(), [second])
        first.present()
        self.assertEqual(highlighted(), [first])
        self.app.show_agenda(window)
        self.assertEqual(highlighted(), [first])
        self.app.show_weekly_agenda(window)
        self.assertEqual(highlighted(), [first])
        row = next(row for row in pane.rows if row.document is second)
        pane.listbox.emit("row-activated", row)
        self.assertEqual(highlighted(), [second])
        second.buffer.set_text("* Changed")
        pane.refresh()
        self.assertEqual(highlighted(), [second])

    def test_utility_pane_new_file_button_is_beside_heading_and_opens_editor(self):
        tab = self.app.new_tab()
        window = tab.window
        pane = window.utility_pane
        self.assertIs(pane.new_file_button.get_prev_sibling(), pane.open_button)
        self.assertEqual(pane.open_button.get_prev_sibling().get_label(), "Files")
        for show in (self.app.show_agenda, self.app.show_weekly_agenda):
            show(window)
            count = len(window.documents())
            pane.new_file_button.emit("clicked")
            self.assertEqual(len(window.documents()), count + 1)
            self.assertEqual(window.stack.get_visible_child_name(), "files")
            self.assertTrue(window.active_tab.is_pristine)
        window.split_view.set_collapsed(True)
        window.split_view.set_show_sidebar(True)
        pane.new_file_button.emit("clicked")
        self.assertFalse(window.split_view.get_show_sidebar())

    def test_utility_pane_appends_new_files_and_keeps_saved_drafts_in_place(self):
        with tempfile.TemporaryDirectory() as directory:
            def create(name):
                path = os.path.join(directory, name)
                with open(path, "w") as stream:
                    stream.write("* Event\n")
                return path

            existing = create("z-existing.org")
            with patch.object(self.app, "agenda_directory", return_value=directory):
                tab = self.app.new_tab()
                pane = tab.window.utility_pane
                pane.refresh()
                added = create("a-new.org")
                pane.refresh()
                self.assertEqual([row.file.get_path() for row in pane.rows], [existing, added])
                tab.buffer.set_text("* Draft")
                pane.refresh()
                self.assertIs(pane.rows[-1].document, tab)
                saved = create("0-saved.org")
                tab._set_file(Gio.File.new_for_path(saved), "* Draft")
                pane.refresh()
                self.assertEqual([row.file.get_path() for row in pane.rows],
                                 [existing, added, saved])
                self.assertIs(pane.rows[-1].document, tab)
                self.app.set_agenda_file_visible(existing, False)
                pane.refresh()
                self.assertEqual([row.file.get_path() for row in pane.rows],
                                 [existing, added, saved])
                os.unlink(added)
                pane.refresh()
                self.assertEqual([row.file.get_path() for row in pane.rows], [existing, saved])
                create("a-new.org")
                pane.refresh()
                self.assertEqual([row.file.get_path() for row in pane.rows],
                                 [existing, saved, added])

    def test_utility_drag_reorders_rows_and_persists_file_order(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = [os.path.join(directory, name + ".org") for name in ("a", "b", "c")]
            for path in paths:
                with open(path, "w") as stream:
                    stream.write("* Event\n")
            with patch.object(self.app, "agenda_directory", return_value=directory):
                tab = self.app.new_tab()
                tab.present()
                pane = tab.window.utility_pane
                self.wait_for(pane.get_mapped)
                pane.refresh()
                first, second, third = pane.rows
                self.wait_for(lambda: first.get_height() > 0)
                controllers = third.observe_controllers()
                drag = next(controllers.get_item(i) for i in range(controllers.get_n_items())
                            if isinstance(controllers.get_item(i), Gtk.DragSource))
                targets = first.observe_controllers()
                drop = next(targets.get_item(i) for i in range(targets.get_n_items())
                            if isinstance(targets.get_item(i), Gtk.DropTarget))
                provider = drag.emit("prepare", 8.0, 8.0)
                self.assertIsInstance(provider, Gdk.ContentProvider)
                self.assertTrue(pane._dragging)
                drop.emit("motion", 0.0, 0.0)
                self.assertTrue(first.has_css_class("drop-before"))
                settings = Mock()
                with patch.object(self.app, "settings", settings):
                    self.assertTrue(drop.emit("drop", third, 0.0, 0.0))
                    settings.set_strv.assert_called_once_with(
                        "utility-file-order",
                        [Gio.File.new_for_path(paths[i]).get_uri() for i in (2, 0, 1)])
                pane._on_drag_end()
                self.assertEqual([row.file.get_path() for row in pane.rows],
                                 [paths[2], paths[0], paths[1]])
                first, second, third = pane.rows
                self.assertTrue(pane._on_drop(None, first, 0, third.get_height(), third))
                pane.refresh()
                self.assertEqual([row.file.get_path() for row in pane.rows], paths)
                self.assertFalse(pane._on_drop(None, Adw.PreferencesRow(), 0, 0, pane.rows[0]))
                self.assertIs(tab.window.active_tab, tab)
                self.assertTrue(all(row.visibility.get_active() for row in pane.rows))

    def test_utility_file_order_is_restored_and_shared_across_windows(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = [os.path.join(directory, name + ".org") for name in ("a", "b")]
            for path in paths:
                with open(path, "w") as stream:
                    stream.write("* Event\n")
            self.app.utility_file_order = [Gio.File.new_for_path(path).get_uri()
                                          for path in reversed(paths)]
            with patch.object(self.app, "agenda_directory", return_value=directory):
                first = self.app.new_tab().window.utility_pane
                second = self.app.new_window().new_tab().window.utility_pane
                self.assertEqual([row.file.get_path() for row in first.rows], paths[::-1])
                self.assertEqual([row.file.get_path() for row in second.rows], paths[::-1])
                self.assertTrue(first._on_drop(None, first.rows[1], 0, -1, first.rows[0]))
                self.wait_for(lambda: [row.file.get_path() for row in second.rows] == paths)

    def test_utility_sources_toggle_both_views_without_closing_or_modifying_files(self):
        import datetime as dt

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "events.org")
            text = f"* Event <{dt.date.today().isoformat()}>\n"
            with open(path, "w") as stream:
                stream.write(text)
            with patch.object(self.app, "agenda_directory", return_value=directory):
                tab = self.app.new_tab()
                window = tab.window
                pane = window.utility_pane
                pane.refresh()
                row = next(row for row in pane.rows if row.file and row.file.get_path() == path)
                self.assertIsNone(row.document)
                self.assertTrue(row.visibility.get_active())
                pane.listbox.emit("row-activated", row)
                self.wait_for(lambda: self.app.window_for_path(path).get_text() == text)
                document = self.app.window_for_path(path)
                self.app.show_agenda(window)
                self.app.show_weekly_agenda(window)
                self.assertEqual(len(window.agenda.entry_buttons), 1)
                self.assertEqual(len(window.weekly_agenda.entry_buttons), 1)
                pane.refresh()
                row = next(row for row in pane.rows if row.document is document)
                self.assertEqual(row.get_tooltip_text(), path)
                self.assertFalse(row.has_css_class("accent"))
                self.assertTrue(row.visibility.has_css_class("utility-file-visibility"))
                self.assertTrue(Gtk.IconTheme.get_for_display(
                    Gdk.Display.get_default()).has_icon("object-select-symbolic"))
                self.assertIsNone(row.visibility.get_next_sibling())
                self.assertIsInstance(row.visibility.get_prev_sibling(), Gtk.Label)
                self.assertIs(row.get_child().get_first_child(), row.color_dot)
                self.assertEqual(row.get_child().get_spacing(), 12)
                self.assertEqual(row.color_dot.get_size_request(), (16, 16))
                reference = Gtk.ListBoxRow()
                reference_content = Gtk.Box(spacing=12, margin_start=6, margin_end=6)
                reference_content.append(Gtk.DrawingArea(width_request=16, height_request=16))
                reference_content.append(Gtk.Label(label="Calendar", hexpand=True))
                reference_content.append(Gtk.Image(icon_name="object-select-symbolic"))
                reference.set_child(reference_content)
                pane.listbox.append(reference)
                self.assertEqual(
                    row.measure(Gtk.Orientation.VERTICAL, -1)[1],
                    reference.measure(Gtk.Orientation.VERTICAL, -1)[1])
                pane.listbox.remove(reference)
                settings = Mock()
                with patch.object(self.app, "settings", settings):
                    row.visibility.set_active(False)
                    settings.set_strv.assert_called_once_with(
                        "hidden-agenda-files", [Gio.File.new_for_path(path).get_uri()])
                self.wait_for(lambda: not window.agenda.entry_buttons
                              and not window.weekly_agenda.entry_buttons)
                self.assertFalse(document.buffer.get_modified())
                self.assertEqual(document.get_text(), text)
                self.assertIn(document, window.documents())
                row = next(row for row in pane.rows if row.document is document)
                self.assertFalse(row.visibility.get_active())
                row.visibility.set_active(True)
                self.wait_for(lambda: len(window.agenda.entry_buttons) == 1
                              and len(window.weekly_agenda.entry_buttons) == 1)

    def test_file_colors_are_stable_and_match_calendar_events(self):
        import datetime as dt
        from utilitypane import FILE_COLORS, file_color, file_color_index

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "events.org")
            alias = os.path.join(directory, "alias.org")
            with open(path, "w") as stream:
                stream.write(f"* Event <{dt.date.today().isoformat()}>\n")
            os.symlink(path, alias)
            self.assertEqual(file_color(path), file_color(alias))
            self.assertIn(file_color(path), FILE_COLORS)
            with patch.object(self.app, "agenda_directory", return_value=directory):
                tab = self.app.new_tab()
                self.app.show_agenda(tab.window)
                for item, button in tab.window.agenda.entry_buttons:
                    self.assertTrue(button.has_css_class(
                        f"file-color-{file_color_index(item.entry.path)}"))
                self.assertTrue(tab.window.agenda.entry_buttons)

    def test_utility_pane_collapses_and_its_header_button_toggles_the_overlay(self):
        tab = self.app.new_tab()
        tab.buffer.set_text("* Draft")
        window = tab.window
        tab.present()
        window.set_default_size(700, 680)
        self.wait_for(window.split_view.get_collapsed)
        self.assertFalse(window.split_view.get_show_sidebar())

        def find_toggle(widget):
            if widget.get_tooltip_text() == "Toggle utility pane":
                return widget
            child = widget.get_first_child()
            while child is not None:
                found = find_toggle(child)
                if found is not None:
                    return found
                child = child.get_next_sibling()
            return None

        toggle = find_toggle(tab.header)
        self.assertIsNotNone(toggle)
        toggle.set_active(True)
        self.assertTrue(window.split_view.get_show_sidebar())
        pane = window.utility_pane
        row = next(row for row in pane.rows if row.document is tab)
        pane.listbox.emit("row-activated", row)
        self.assertFalse(window.split_view.get_show_sidebar())
        self.assertFalse(toggle.get_active())
        window.set_default_size(960, 680)
        self.wait_for(lambda: not window.split_view.get_collapsed())
        self.assertTrue(window.split_view.get_show_sidebar())

    def test_utility_pane_hides_empty_untitled_documents_but_keeps_drafts_and_named_files(self):
        tab = self.app.new_tab()
        pane = tab.window.utility_pane
        with patch.object(self.app, "agenda_files", return_value=[]):
            pane.refresh()
            self.assertEqual(pane.rows, [])
            self.assertTrue(pane.empty.get_visible())
            tab.buffer.set_text("* Draft")
            self.wait_for(lambda: any(row.document is tab for row in pane.rows))
            self.assertFalse(pane.empty.get_visible())
            tab.buffer.set_text("")
            self.assertTrue(tab.buffer.get_modified())
            self.wait_for(lambda: not pane.rows)
            self.assertIn(tab, tab.window.documents())
            tab._set_file(Gio.File.new_for_path("/org/empty.org"), "")
            pane.refresh()
            self.assertEqual(len(pane.rows), 1)
            self.assertIs(pane.rows[0].document, tab)
            self.assertEqual(pane.rows[0].get_title(), "empty")

    def test_utility_visibility_toggle_preserves_keyboard_focus_and_does_not_open_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "event.org")
            with open(path, "w") as stream:
                stream.write("* Event <2026-11-02>\n")
            with patch.object(self.app, "agenda_directory", return_value=directory):
                tab = self.app.new_tab()
                tab.present()
                self.wait_for(tab.view.get_mapped)
                pane = tab.window.utility_pane
                pane.refresh()
                row = next(row for row in pane.rows if row.visibility)
                row.visibility.grab_focus()
                row.visibility.activate()
                self.wait_for(lambda: not next(
                    row for row in pane.rows if row.visibility).visibility.get_active())
                pane.refresh()
                row = next(row for row in pane.rows if row.visibility)
                self.assertIs(tab.window.get_focus(), row.visibility)
                self.assertIsNone(self.app.window_for_path(path))
                self.assertIs(tab.window.active_tab, tab)

    def test_open_org_sources_outside_folder_are_deduplicated_and_follow_unsaved_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = os.path.join(directory, "calendar")
            os.mkdir(folder)
            path = os.path.join(directory, "outside.org")
            with open(path, "w") as stream:
                stream.write("* Disk <2026-11-02>\n")
            with patch.object(self.app, "agenda_directory", return_value=folder):
                tab = self.app.open_file(Gio.File.new_for_path(path))
                self.wait_for(lambda: bool(tab.get_text()))
                tab.buffer.set_text("* Unsaved <2026-11-03>\n")
                _, files, entries = self.app.agenda_entries()
                self.assertEqual(files, [path])
                self.assertEqual(entries[0].heading_text, "* Unsaved <2026-11-03>")
                self.app.set_agenda_file_visible(path, False)
                self.assertEqual(self.app.agenda_entries()[2], [])
                with patch.object(self.app, "agenda_directory", return_value=directory):
                    self.assertEqual(self.app.agenda_files(), [path])
                pane = tab.window.utility_pane
                pane.refresh()
                self.assertEqual(len([row for row in pane.rows
                                      if row.file and row.file.get_path() == path]), 1)

    def test_utility_calendar_navigates_views_and_marks_only_visible_sources(self):
        import datetime as dt
        import org

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "event.org")
            with open(path, "w") as stream:
                stream.write("* Event <2026-11-17>\n")
            with patch.object(self.app, "agenda_directory", return_value=directory):
                tab = self.app.new_tab()
                window = tab.window
                pane = window.utility_pane
                pane.calendar.set_date(GLib.DateTime.new_local(2026, 11, 17, 0, 0, 0))
                self.assertEqual(window.stack.get_visible_child_name(), "agenda")
                self.assertEqual(window.agenda.start, org.week_start(dt.date(2026, 11, 17)))
                self.assertEqual(pane.calendar.get_date().get_day_of_month(), 17)
                pane.update_marks()
                self.assertTrue(pane.calendar.get_day_is_marked(17))
                self.app.set_agenda_file_visible(path, False)
                pane.update_marks()
                self.assertFalse(pane.calendar.get_day_is_marked(17))
                self.app.show_weekly_agenda(window)
                pane.calendar.set_date(GLib.DateTime.new_local(2026, 12, 9, 0, 0, 0))
                self.assertEqual(window.stack.get_visible_child_name(), "weekly-agenda")
                self.assertEqual(window.weekly_agenda.start, org.week_start(dt.date(2026, 12, 9)))
                window.weekly_agenda.shift(1)
                selected = pane.calendar.get_date()
                date = dt.date(selected.get_year(), selected.get_month(),
                               selected.get_day_of_month())
                self.assertEqual(org.week_start(date), window.weekly_agenda.start)
                window.agenda.refresh()
                self.assertEqual(pane.calendar.get_date().get_day_of_month(), date.day)

    def test_visibility_choices_are_restored_at_startup(self):
        directory = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "data"))
        source = Gio.SettingsSchemaSource.new_from_directory(
            directory, Gio.SettingsSchemaSource.get_default(), False)
        schema = source.lookup("io.github.kllmanu.Organizer", False)
        settings = Gio.Settings.new_full(schema, Gio.memory_settings_backend_new(), None)
        settings.set_strv("hidden-agenda-files", ["file:///org/hidden.org"])
        settings.set_strv("utility-file-order", ["file:///org/b.org", "file:///org/a.org"])
        settings.set_strv("removed-files", ["file:///org/removed.org"])
        settings.set_value("file-colors", GLib.Variant("a{ss}", {"file:///org/a.org": "#123456"}))
        with (patch.object(OrganizerApplication, "_load_settings", return_value=settings),
              patch("vim.data_path", return_value=None)):
            app = OrganizerApplication()
            app.set_application_id("io.github.kllmanu.Organizer.VisibilityTests")
            app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
            app.register(None)
        self.addCleanup(app.quit)
        self.assertFalse(app.agenda_file_visible("/org/hidden.org"))
        self.assertTrue(app.agenda_file_visible("/org/visible.org"))
        self.assertEqual(app.utility_file_order, ["file:///org/b.org", "file:///org/a.org"])
        self.assertTrue(app.file_removed(Gio.File.new_for_path("/org/removed.org")))
        self.assertEqual(app.calendar_file_color("/org/a.org"), "#123456")

    def test_alt_file_navigation_uses_sidebar_order_and_stops_at_edges(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = [os.path.join(directory, name + ".org") for name in ("a", "b", "c")]
            for path in paths:
                with open(path, "w") as stream:
                    stream.write("* Event\n")
            with patch.object(self.app, "agenda_directory", return_value=directory):
                self.app.utility_file_order = [Gio.File.new_for_path(path).get_uri()
                                              for path in reversed(paths)]
                tab = self.app.open_file(Gio.File.new_for_path(paths[2]))
                window = tab.window

                def press(key, modifiers=Gdk.ModifierType.ALT_MASK):
                    return window._on_key_pressed(None, key, 0, modifiers)

                for key, expected in (
                        (Gdk.KEY_k, paths[2]), (Gdk.KEY_j, paths[1]),
                        (Gdk.KEY_j, paths[0]), (Gdk.KEY_j, paths[0]),
                        (Gdk.KEY_k, paths[1]), (Gdk.KEY_k, paths[2]),
                        (Gdk.KEY_k, paths[2])):
                    self.assertTrue(press(key))
                    self.assertEqual(window.active_tab.path, expected)
                self.app.show_agenda(window)
                self.assertTrue(press(Gdk.KEY_j))
                self.assertEqual(window.stack.get_visible_child_name(), "files")
                self.assertEqual(window.active_tab.path, paths[1])
                self.app.show_weekly_agenda(window)
                self.assertTrue(press(Gdk.KEY_k))
                self.assertEqual(window.active_tab.path, paths[2])
                self.assertEqual(window.stack.get_visible_child_name(), "files")
                for key in (Gdk.KEY_j, Gdk.KEY_k):
                    self.assertFalse(press(
                        key, Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.CONTROL_MASK))
                self.wait_for(lambda: all(tab.get_text() for tab in window.documents()))
                self.assertEqual(len(window.documents()), 3)

    def test_alt_file_navigation_handles_empty_list_and_unlisted_empty_document(self):
        with patch.object(self.app, "agenda_files", return_value=[]):
            tab = self.app.new_tab()
            window = tab.window
            for key in (Gdk.KEY_j, Gdk.KEY_k):
                self.assertTrue(window._on_key_pressed(
                    None, key, 0, Gdk.ModifierType.ALT_MASK))
                self.assertIs(window.active_tab, tab)
            tab.buffer.set_text("* Draft")
            empty = window.new_tab()
            self.assertTrue(window._on_key_pressed(
                None, Gdk.KEY_j, 0, Gdk.ModifierType.ALT_MASK))
            self.assertIs(window.active_tab, tab)
            empty.present()
            self.assertTrue(window._on_key_pressed(
                None, Gdk.KEY_k, 0, Gdk.ModifierType.ALT_MASK))
            self.assertIs(window.active_tab, tab)

    def test_agenda_displays_file_category_and_unsaved_category_changes(self):
        import datetime as dt

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "tasks.org")
            text = f"#+CATEGORY: Work\n* Event <{dt.date.today().isoformat()}>\n"
            with open(path, "w") as stream:
                stream.write(text)
            with patch.object(self.app, "agenda_directory", return_value=directory):
                tab = self.app.open_file(Gio.File.new_for_path(path))
                self.wait_for(lambda: tab.get_text() == text)
                self.app.show_weekly_agenda(tab.window)
                agenda = tab.window.weekly_agenda
                self.assertTrue(agenda.entry_buttons[0][1].get_child().get_label()
                                .startswith("  Work:"))
                tab.buffer.set_text(text.replace("#+CATEGORY: Work\n", ""))
                self.wait_for(lambda: agenda.entry_buttons[0][1].get_child().get_label()
                              .startswith("  tasks:"))

    def test_agenda_g_refreshes_entries_from_disk(self):
        import datetime as dt

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "events.org")
            with open(path, "w") as stream:
                stream.write(f"* Before <{dt.date.today().isoformat()}>\n")
            with patch.object(self.app, "agenda_directory", return_value=directory):
                tab = self.app.new_tab()
                self.app.show_weekly_agenda(tab.window)
                agenda = tab.window.weekly_agenda
                self.assertEqual(len(agenda.entry_buttons), 1)
                with open(path, "w") as stream:
                    stream.write(f"* After <{dt.date.today().isoformat()}>\n"
                                 f"* Added <{dt.date.today().isoformat()}>\n")
                self.assertTrue(tab.window._on_key_pressed(
                    None, Gdk.KEY_g, 0, Gdk.ModifierType(0)))
                self.assertEqual(len(agenda.entry_buttons), 2)
                self.assertTrue(any("After" in item.entry.title
                                    for item, _button in agenda.entry_buttons))
                self.assertIsNone(self.app.window_for_path(path))
                self.assertEqual(tab.window.stack.get_visible_child_name(), "weekly-agenda")

    def test_alt_navigation_stops_at_edges_without_switching_documents(self):
        first = self.app.new_tab()
        second = self.app.new_tab()
        window = first.window
        second.present()

        def press(key, modifiers=Gdk.ModifierType.ALT_MASK):
            return window._on_key_pressed(None, key, 0, modifiers)

        for key, expected_views in (
                (Gdk.KEY_h, ("files", "files")),
                (Gdk.KEY_l, ("weekly-agenda", "agenda", "agenda", "agenda")),
                (Gdk.KEY_h, ("weekly-agenda", "files", "files", "files"))):
            for expected in expected_views:
                self.assertTrue(press(key))
                self.assertEqual(window.stack.get_visible_child_name(), expected)
                self.assertIs(window.active_tab, second)
        self.assertIs(window.active_view, second)
        self.assertIn(first, window.documents())
        self.assertFalse(press(Gdk.KEY_o))
        for key in (Gdk.KEY_h, Gdk.KEY_l):
            self.assertFalse(press(
                key, Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.CONTROL_MASK))
        self.assertEqual(window.stack.get_visible_child_name(), "files")

    def test_alt_navigation_stops_at_edges_with_single_document(self):
        tab = self.app.new_tab()
        for key, expected in (
                (Gdk.KEY_h, "files"), (Gdk.KEY_l, "weekly-agenda"),
                (Gdk.KEY_l, "agenda"), (Gdk.KEY_l, "agenda"),
                (Gdk.KEY_h, "weekly-agenda"), (Gdk.KEY_h, "files"),
                (Gdk.KEY_h, "files")):
            self.assertTrue(tab.window._on_key_pressed(None, key, 0, Gdk.ModifierType.ALT_MASK))
            self.assertEqual(tab.window.stack.get_visible_child_name(), expected)
            self.assertIs(tab.window.active_tab, tab)

    def test_tab_creation_and_reopening_have_no_keyboard_accelerators(self):
        self.assertEqual(self.app.get_accels_for_action("win.new"), [])
        self.assertEqual(self.app.get_accels_for_action("win.reopen"), [])
        for shortcut in ("<Control>t", "<Control><Shift>t"):
            self.assertEqual(self.app.get_actions_for_accel(shortcut), [])
        self.assertEqual(self.app.get_accels_for_action("win.open"), ["<Control>o"])
        self.assertEqual(self.app.get_accels_for_action("win.save"), ["<Control>s"])
        self.assertEqual(self.app.get_accels_for_action("win.close"), ["<Control>w"])

    def test_heading_cycle_and_startup_visibility(self):
        text = "* Parent\nBody\n** Child\nChild body\n*** Grandchild\nDetails\n* Next\nTail"
        tab = self.app.new_tab()
        tab.buffer.set_text(text)
        tab.buffer.set_modified(False)
        tab.buffer.place_cursor(tab.buffer.get_start_iter())
        tab.present()

        def hidden(line):
            return tab.buffer.get_iter_at_line(line)[1].has_tag(tab.org_fold.hidden)

        for expected in ((True, True, True), (True, False, True), (False, False, False)):
            self.assertTrue(tab.window._on_key_pressed(
                None, Gdk.KEY_Tab, 0, Gdk.ModifierType(0)))
            self.assertEqual(tuple(hidden(i) for i in (1, 2, 4)), expected)
        self.assertFalse(tab.buffer.get_modified())
        self.assertEqual(tab.get_text(), text)
        for option, expected in (
                ("overview", (True, True, True)),
                ("content", (True, False, False)),
                ("showall", (False, False, False)),
                ("showeverything", (False, False, False))):
            tab._set_file(Gio.File.new_for_path("/org/folding.org"),
                          f"#+STARTUP: {option}\n{text}")
            self.assertEqual(tuple(hidden(i) for i in (2, 3, 5)), expected)
            self.assertFalse(tab.buffer.get_modified())
        tab.org_fold.cycle(global_cycle=True)
        self.assertTrue(hidden(3))
        tab.goto_line(4)
        self.assertFalse(hidden(4))

    def test_folding_preserves_state_after_body_edits_and_reads_startup_changes(self):
        tab = self.app.new_tab()
        tab.buffer.set_text("* Parent\nBody\n** Child\nDetails\n")
        tab.buffer.place_cursor(tab.buffer.get_start_iter())
        self.assertTrue(tab.org_fold.cycle())
        tab.buffer.insert(tab.buffer.get_iter_at_line(1)[1], "More ")
        tab.org_fold.refresh()
        self.assertTrue(tab.buffer.get_iter_at_line(2)[1].has_tag(tab.org_fold.hidden))
        tab.buffer.insert(tab.buffer.get_start_iter(), "#+STARTUP: content\n")
        tab.org_fold.refresh()
        self.assertFalse(tab.buffer.get_iter_at_line(3)[1].has_tag(tab.org_fold.hidden))
        self.assertTrue(tab.buffer.get_iter_at_line(4)[1].has_tag(tab.org_fold.hidden))
        tab.buffer.place_cursor(tab.buffer.get_start_iter())
        self.assertFalse(tab.org_fold.cycle())

    def test_fold_indicators_follow_hidden_content_without_changing_text(self):
        tab = self.app.new_tab()
        text = "* Parent\nBody\n** Child\nDetails\n* Empty"
        tab.buffer.set_text(text)
        tab.buffer.set_modified(False)
        tab.buffer.place_cursor(tab.buffer.get_start_iter())
        tab.present()
        tab.org_fold.refresh()
        self.assertEqual(tab.org_fold.indicators, {})
        tab.org_fold.cycle()
        self.assertEqual(set(tab.org_fold.indicators), {0})
        self.assertEqual(tab.org_fold.indicators[0].get_label(), "...")
        indicator = tab.org_fold.indicators[0]
        self.assertFalse(indicator.has_css_class("dim-label"))
        tag = tab.buffer.create_tag(None, foreground="#26a269", weight=700)
        end = tab.buffer.get_iter_at_line(0)[1]
        end.forward_to_line_end()
        tab.buffer.apply_tag(tag, tab.buffer.get_start_iter(), end)
        tab.org_fold._layout_indicators()
        self.assertEqual(tab.org_fold._indicator_styles[0], ("rgb(38,162,105)", 700))
        rect = tab.view.get_iter_location(end)
        self.assertEqual(tab.org_fold._indicator_positions[0], (rect.x, rect.y))
        tab.org_fold.cycle()
        self.assertEqual(set(tab.org_fold.indicators), {0, 2})
        tab.org_fold.cycle()
        self.assertEqual(tab.org_fold.indicators, {})
        tab.org_fold.cycle(global_cycle=True)
        self.assertEqual(set(tab.org_fold.indicators), {0})
        self.assertEqual(tab.get_text(), text)
        self.assertFalse(tab.buffer.get_modified())

    def test_fold_indicator_actual_bounds_align_with_heading_without_trailing_spaces(self):
        tab = self.app.new_tab()
        tab.buffer.set_text("* Parent   \nBody\n* Next")
        tab.buffer.place_cursor(tab.buffer.get_start_iter())
        tab.present()
        tab.org_fold.cycle()
        label = tab.org_fold.indicators[0]
        self.wait_for(lambda: label.get_width() > 0)
        end = tab.buffer.get_iter_at_line_offset(0, len("* Parent"))[1]
        rect = tab.view.get_iter_location(end)
        x, y = tab.view.buffer_to_window_coords(Gtk.TextWindowType.WIDGET, rect.x, rect.y)

        def aligned():
            ok, bounds = label.compute_bounds(tab.view)
            return ok and abs(bounds.get_x() - x) <= 1 and abs(bounds.get_y() - y) <= 1

        self.wait_for(aligned)
        self.assertEqual(tab.get_text(), "* Parent   \nBody\n* Next")

    def test_line_numbers_skip_folded_bodies_and_restore_when_expanded(self):
        from gi.repository import GtkSource

        tab = self.app.new_tab()
        tab.buffer.set_text("* Parent\nBody\nMore body\n* Next\nTail\n")
        tab.buffer.place_cursor(tab.buffer.get_start_iter())
        self.addCleanup(self.app.set_show_line_numbers, False)
        self.app.set_show_line_numbers(True)
        tab.present()
        self.wait_for(tab.org_line_numbers.get_mapped)
        self.assertFalse(tab.org_line_numbers.native.get_visible())
        lines = Mock()
        lines.get_iter_at_line.side_effect = lambda line: tab.buffer.get_iter_at_line(line)[1]

        def drawn_lines():
            drawn = []
            with patch.object(GtkSource.GutterRendererText, "do_snapshot_line",
                              side_effect=lambda *args: drawn.append(args[-1])):
                for line in range(tab.buffer.get_line_count()):
                    tab.org_line_numbers.do_snapshot_line(Mock(), lines, line)
            return drawn

        tab.org_fold.cycle(global_cycle=True)
        self.assertEqual(drawn_lines(), [0, 3, 5])
        tab.org_fold.cycle(global_cycle=True)
        tab.org_fold.cycle(global_cycle=True)
        self.assertEqual(drawn_lines(), list(range(6)))
        self.app.set_show_line_numbers(False)
        self.assertFalse(tab.org_line_numbers.get_visible())
        self.app.set_show_line_numbers(True)
        self.assertTrue(tab.org_line_numbers.get_visible())
        self.assertFalse(tab.org_line_numbers.native.get_visible())

    def test_fold_dots_stay_after_title_before_right_aligned_tags(self):
        tab = self.app.new_tab()
        text = "* Parent :work:home:\nBody\n* Next\n"
        tab.buffer.set_text(text)
        tab.buffer.set_modified(False)
        tab.buffer.place_cursor(tab.buffer.get_start_iter())
        tab.present()
        tab.org_fold.cycle()
        label = tab.org_fold.indicators[0]

        def aligned():
            last = tab.buffer.get_iter_at_line_offset(0, len("* Parent") - 1)[1]
            rect = tab.view.get_iter_location(last)
            x, y = tab.view.buffer_to_window_coords(
                Gtk.TextWindowType.WIDGET, rect.x + rect.width, rect.y)
            ok, bounds = label.compute_bounds(tab.view)
            tags = tab.view.get_iter_location(
                tab.buffer.get_iter_at_line_offset(0, len("* Parent "))[1])
            return (ok and label.get_width() > 0
                    and abs(bounds.get_x() - x) <= 1
                    and abs(bounds.get_y() - y) <= 1
                    and rect.x + rect.width + label.get_width() < tags.x)

        self.wait_for(aligned)
        old_width = tab.view.get_width()
        tab.window.set_default_size(tab.window.get_width() + 180, tab.window.get_height())
        self.wait_for(lambda: tab.view.get_width() != old_width)
        self.wait_for(aligned)
        self.assertEqual(tab.get_text(), text)
        self.assertFalse(tab.buffer.get_modified())

    def test_cursor_skips_folded_lines_in_both_directions(self):
        tab = self.app.new_tab()
        tab.buffer.set_text("* Parent\nBody\n** Child\nDetails\n* Next\nTail")
        tab.buffer.place_cursor(tab.buffer.get_start_iter())
        tab.org_fold.cycle()
        tab.buffer.place_cursor(tab.buffer.get_iter_at_line(1)[1])
        cursor = tab.buffer.get_iter_at_mark(tab.buffer.get_insert())
        self.assertEqual(cursor.get_line(), 4)
        self.assertFalse(cursor.has_tag(tab.org_fold.hidden))
        self.assertFalse(tab.buffer.get_has_selection())
        tab.buffer.place_cursor(tab.buffer.get_iter_at_line(3)[1])
        cursor = tab.buffer.get_iter_at_mark(tab.buffer.get_insert())
        self.assertEqual(cursor.get_line(), 0)
        self.assertFalse(cursor.has_tag(tab.org_fold.hidden))
        tab.buffer.place_cursor(tab.buffer.get_iter_at_line(4)[1])
        tab.org_fold.cycle()
        tab.buffer.place_cursor(tab.buffer.get_iter_at_line(5)[1])
        self.assertEqual(tab.buffer.get_iter_at_mark(tab.buffer.get_insert()).get_line(), 4)

    def test_collapsed_subtree_has_no_gap_with_org_indentation(self):
        tab = self.app.new_tab()
        text = "* Parent\nBody\n** Child\nDetails\n\n\n* Next\nTail"
        tab.buffer.set_text(text)
        tab.buffer.place_cursor(tab.buffer.get_start_iter())
        tab.present()
        tab.org_fold.cycle()

        def compact():
            first = tab.view.get_iter_location(tab.buffer.get_start_iter())
            following = tab.view.get_iter_location(tab.buffer.get_iter_at_line(6)[1])
            return following.y == first.y + first.height

        self.wait_for(compact)
        tab.org_fold.cycle()
        self.wait_for(lambda: not tab.buffer.get_iter_at_line(2)[1].has_tag(tab.org_fold.hidden))
        tab.org_fold.cycle()
        tab.org_fold.cycle()
        self.wait_for(compact)
        self.assertEqual(tab.get_text(), text)

    def test_tab_opens_folded_leaf_immediately_and_skips_unchanged_children_state(self):
        tab = self.app.new_tab()
        tab.buffer.set_text("* Parent\nBody\n* Next")
        tab.buffer.place_cursor(tab.buffer.get_start_iter())
        for expected_hidden in (True, False, True, False):
            tab.org_fold.cycle()
            self.assertEqual(tab.buffer.get_iter_at_line(1)[1].has_tag(tab.org_fold.hidden),
                             expected_hidden)
        tab._set_file(Gio.File.new_for_path("/org/folding.org"),
                      "#+STARTUP: content\n* Parent\nBody\n** Child\nDetails\n* Next")
        tab.buffer.place_cursor(tab.buffer.get_iter_at_line(1)[1])
        tab.org_fold.cycle()
        self.assertFalse(tab.buffer.get_iter_at_line(2)[1].has_tag(tab.org_fold.hidden))
        self.assertFalse(tab.buffer.get_iter_at_line(4)[1].has_tag(tab.org_fold.hidden))

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

    def test_line_numbers_default_off_and_update_existing_and_new_tabs(self):
        first = self.app.new_tab()
        second = self.app.new_window().new_tab()
        self.assertFalse(first.view.get_show_line_numbers())
        self.assertFalse(second.view.get_show_line_numbers())
        self.addCleanup(self.app.set_show_line_numbers, False)
        settings = Mock()
        with patch.object(self.app, "settings", settings):
            self.app.set_show_line_numbers(True)
            settings.set_boolean.assert_called_once_with("show-line-numbers", True)
        third = self.app.new_tab()
        for tab in (first, second, third):
            self.assertTrue(tab.view.get_show_line_numbers())
        self.app.set_show_line_numbers(False)
        for tab in (first, second, third):
            self.assertFalse(tab.view.get_show_line_numbers())

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

    def test_line_numbers_preference_switch_updates_editor(self):
        tab = self.app.new_tab()
        tab.present()
        self.addCleanup(self.app.set_show_line_numbers, False)
        self.app.show_preferences()
        dialog = tab.window.get_visible_dialog()

        row = self.find_switch(dialog, "Show Line Numbers")
        self.assertIsNotNone(row)
        self.assertFalse(row.get_active())
        row.set_active(True)
        self.assertTrue(tab.view.get_show_line_numbers())
        row.set_active(False)
        self.assertFalse(tab.view.get_show_line_numbers())
        dialog.close()

    def test_editor_and_agenda_settings_are_restored_at_startup(self):
        directory = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "data"))
        source = Gio.SettingsSchemaSource.new_from_directory(
            directory, Gio.SettingsSchemaSource.get_default(), False)
        schema = source.lookup("io.github.kllmanu.Organizer", False)
        settings = Gio.Settings.new_full(schema, Gio.memory_settings_backend_new(), None)
        self.assertFalse(settings.get_boolean("show-line-numbers"))
        self.assertTrue(settings.get_boolean("hide-weekends"))
        self.assertTrue(settings.get_boolean("auto-revert"))
        self.assertTrue(settings.get_boolean("jj-escape"))
        self.assertEqual(settings.get_int("jj-escape-delay"), 200)
        self.assertEqual(settings.get_string("escape-sequence"), "jj")
        settings.set_boolean("show-line-numbers", True)
        settings.set_boolean("hide-weekends", False)
        settings.set_boolean("auto-revert", False)
        settings.set_boolean("jj-escape", False)
        settings.set_int("jj-escape-delay", 300)
        settings.set_string("escape-sequence", "jk")
        app = OrganizerApplication()
        app.set_application_id("io.github.kllmanu.Organizer.LineNumberTests")
        app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
        with (patch.object(OrganizerApplication, "_load_settings", return_value=settings),
              patch("vim.data_path", return_value=None)):
            app.register(None)
        self.addCleanup(app.quit)
        tab = app.new_tab()
        self.addCleanup(tab.window.destroy)
        self.assertTrue(app.show_line_numbers)
        self.assertTrue(tab.view.get_show_line_numbers())
        self.assertFalse(app.hide_weekends)
        self.assertFalse(app.auto_revert)
        self.assertFalse(app.jj_escape)
        self.assertEqual(app.jj_escape_delay, 300)
        self.assertEqual(app.escape_sequence, "jk")
        app.show_agenda(tab.window)
        self.assertIsNotNone(tab.window.agenda.time_grid.get_child_at(6, 0))
        app.set_hide_weekends(True)
        self.assertTrue(settings.get_boolean("hide-weekends"))
        self.assertIsNone(tab.window.agenda.time_grid.get_child_at(5, 0))
        app.set_show_line_numbers(False)
        self.assertFalse(settings.get_boolean("show-line-numbers"))

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

    def test_quit_action_closes_agenda_and_editor(self):
        self.app.new_tab().present()
        self.app.show_agenda()
        with patch.object(self.app, "quit") as quit_app:
            self.app.lookup_action("quit").activate(None)
            quit_app.assert_called_once_with()
        self.assertEqual(self.app.get_windows(), [])

    def test_agenda_replaces_files_without_adding_a_tab(self):
        tab = self.app.new_tab()
        tab.present()
        self.app.show_agenda()
        agenda = self.app.agenda
        self.assertIs(agenda.window, tab.window)
        self.assertEqual(len(self.app.get_windows()), 1)
        self.assertEqual(agenda.window.tabs.get_n_pages(), 1)
        self.assertIs(agenda.window.active_view, agenda)
        self.assertIs(agenda.window.active_tab, tab)
        self.assertEqual(agenda.window.stack.get_visible_child_name(), "agenda")
        self.assertIs(agenda.window.view_switcher.get_stack(), agenda.window.stack)
        self.wait_for(lambda: not tab.get_mapped())
        self.assertFalse(tab.window.tabs.get_mapped())
        self.assertEqual(len(agenda.days), 7)
        self.assertEqual(len(self.app.editor_tabs()), 1)
        self.assertIsNone(agenda.window.lookup_action("save"))
        self.app.show_agenda()
        self.assertIs(self.app.agenda, agenda)
        self.assertEqual(agenda.window.tabs.get_n_pages(), 1)
        start = agenda.start
        agenda.shift(1)
        self.assertEqual((agenda.start - start).days, 7)
        agenda.goto_today()
        self.assertEqual(agenda.start, start)
        agenda.close()
        self.assertIs(self.app.agenda, agenda)
        self.assertIs(tab.window.active_tab, tab)
        self.assertIs(tab.window.active_view, tab)
        self.assertEqual(tab.window.stack.get_visible_child_name(), "files")
        self.assertIsNotNone(tab.window.lookup_action("save"))

    def test_agenda_week_controls_are_at_the_bottom_and_navigate(self):
        import datetime as dt
        import org

        self.app.show_agenda()
        agenda = self.app.agenda
        content = agenda.toasts.get_child()
        self.assertIs(content.get_first_child(), agenda.view)
        self.assertIsInstance(agenda.view.get_next_sibling(), Gtk.Separator)
        navigation = content.get_last_child()
        self.assertIs(agenda.week_label.get_parent(), navigation)
        previous = navigation.get_first_child()
        following = previous.get_next_sibling()
        today = following.get_next_sibling()
        self.assertEqual(previous.get_icon_name(), "go-previous-symbolic")
        self.assertEqual(following.get_icon_name(), "go-next-symbolic")
        self.assertIs(today, agenda.today_button)
        self.assertEqual(today.get_child().get_first_child().get_icon_name(),
                         "x-office-calendar-symbolic")
        self.assertEqual(today.get_child().get_last_child().get_label(), "Today")
        self.assertFalse(today.get_sensitive())
        self.assertFalse(today.get_child().get_first_child().is_sensitive())
        self.assertFalse(today.get_child().get_last_child().is_sensitive())
        start = agenda.start
        previous.emit("clicked")
        self.assertEqual(agenda.start, start - dt.timedelta(days=7))
        self.assertTrue(today.get_sensitive())
        following.emit("clicked")
        self.assertEqual(agenda.start, start)
        self.assertFalse(today.get_sensitive())
        following.emit("clicked")
        today.emit("clicked")
        self.assertEqual(agenda.start, org.week_start(dt.date.today()))
        self.assertFalse(today.get_sensitive())

    def test_today_button_is_disabled_in_current_week_even_with_hidden_weekend(self):
        import datetime as dt

        self.app.show_agenda()
        agenda = self.app.agenda

        class Sunday(dt.date):
            @classmethod
            def today(cls):
                return cls(2026, 11, 1)

        with patch("agenda.dt.date", Sunday):
            agenda.start = Sunday(2026, 10, 26)
            agenda.refresh()
            self.assertFalse(agenda.today_button.get_sensitive())
            agenda.shift(1)
            self.assertTrue(agenda.today_button.get_sensitive())
            agenda.goto_today()
            self.assertFalse(agenda.today_button.get_sensitive())

    def test_agenda_messages_use_toasts_without_a_status_bar(self):
        self.app.show_agenda()
        agenda = self.app.agenda
        self.assertIsInstance(agenda.get_child(), Adw.ToastOverlay)
        with patch.object(agenda.toasts, "add_toast") as add_toast:
            agenda.selected_item = None
            agenda.cycle_todo()
            self.assertEqual(add_toast.call_args.args[0].get_title(), "Select an entry first.")
            agenda.edit_planning("SCHEDULED")
            self.assertEqual(add_toast.call_count, 2)
        with (patch.object(self.app, "agenda_entries", return_value=("/missing-org", [], [])),
              patch.object(agenda, "toast") as toast):
            agenda.refresh()
            agenda.refresh()
            toast.assert_called_once_with(
                "The calendar folder /missing-org does not exist. Choose a folder in Preferences.")

    def test_hide_weekends_preference_updates_agendas_and_preserves_entries(self):
        import datetime as dt
        import org

        first = self.app.new_tab()
        second = self.app.new_window().new_tab()
        self.app.show_agenda(first.window)
        self.app.show_agenda(second.window)
        agendas = [first.window.agenda, second.window.agenda]
        start = agendas[0].start
        saturday = start + dt.timedelta(days=5)
        entries = org.parse_entries(
            f"* Weekend event\n<{saturday.isoformat()} 10:00>\n", "/org/tasks.org")
        self.addCleanup(self.app.set_hide_weekends, True)
        with patch.object(self.app, "agenda_entries", return_value=("/org", ["/org/tasks.org"], entries)):
            for agenda in agendas:
                agenda.refresh()
                self.assertTrue(self.app.hide_weekends)
                self.assertIsNotNone(agenda.time_grid.get_child_at(4, 0))
                self.assertIsNone(agenda.time_grid.get_child_at(5, 0))
                self.assertIsNone(agenda.day_headers.get_child_at(5, 0))
                self.assertEqual(len(agenda.days[saturday]), 1)
                self.assertEqual(agenda.entry_buttons, [])
                self.assertTrue(agenda.week_label.get_label().endswith(
                    org.format_date(start + dt.timedelta(days=4), with_year=True)))
            first.present()
            self.app.show_preferences()
            dialog = self.app.get_active_window().get_visible_dialog()
            row = self.find_switch(dialog, "Hide Weekends")
            self.assertIsNotNone(row)
            self.assertTrue(row.get_active())
            settings = Mock()
            with patch.object(self.app, "settings", settings):
                row.set_active(False)
                settings.set_boolean.assert_called_once_with("hide-weekends", False)
            for agenda in agendas:
                self.assertIsNotNone(agenda.time_grid.get_child_at(6, 0))
                self.assertIsNotNone(agenda.day_headers.get_child_at(6, 0))
                self.assertEqual(len(agenda.entry_buttons), 1)
                self.assertEqual(agenda.start, start)
            row.set_active(True)
            for agenda in agendas:
                self.assertIsNone(agenda.time_grid.get_child_at(5, 0))
                self.assertEqual(agenda.entry_buttons, [])
                agenda.shift(1)
                self.assertEqual((agenda.start - start).days, 7)
                self.assertIsNone(agenda.time_grid.get_child_at(5, 0))
            dialog.close()

    def test_view_switcher_preserves_selected_file_and_unsaved_edits(self):
        first = self.app.new_tab()
        second = self.app.new_tab()
        second.buffer.set_text("* Unsaved")
        second.buffer.place_cursor(second.buffer.get_iter_at_offset(5))
        second.present()
        window = second.window
        window.stack.set_visible_child_name("agenda")
        agenda = self.app.agenda
        start = agenda.start
        agenda.shift(1)
        window.stack.set_visible_child_name("files")
        self.assertIs(window.active_view, second)
        self.assertEqual(second.get_text(), "* Unsaved")
        self.assertTrue(second.buffer.get_modified())
        self.assertEqual(second.buffer.get_iter_at_mark(second.buffer.get_insert()).get_offset(), 5)
        self.assertEqual(window.tabs.get_n_pages(), 2)
        window.stack.set_visible_child_name("agenda")
        self.assertIs(self.app.agenda, agenda)
        self.assertEqual((agenda.start - start).days, 7)
        first.present()
        self.assertIs(window.active_view, first)
        self.assertEqual(window.stack.get_visible_child_name(), "files")

    def test_new_tab_from_agenda_switches_to_files(self):
        tab = self.app.new_tab()
        self.app.show_agenda()
        tab.window.lookup_action("new").activate(None)
        self.assertEqual(tab.window.tabs.get_n_pages(), 2)
        self.assertIs(tab.window.active_view, tab.window.active_tab)
        self.assertEqual(tab.window.stack.get_visible_child_name(), "files")

    def test_view_switchers_target_their_own_window(self):
        first = self.app.new_tab()
        second = self.app.new_window().new_tab()
        first.window.stack.set_visible_child_name("agenda")
        first_agenda = first.window.agenda
        second.window.stack.set_visible_child_name("agenda")
        self.assertIs(first.window.active_view, first_agenda)
        self.assertIs(second.window.active_view, second.window.agenda)
        self.assertIsNot(first_agenda, second.window.agenda)
        first.window.stack.set_visible_child_name("files")
        self.assertIs(first.window.active_view, first)
        self.assertIs(second.window.active_view, second.window.agenda)

    def test_agenda_quit_key_returns_to_selected_file(self):
        tab = self.app.new_tab()
        self.app.show_agenda()
        self.assertTrue(tab.window._on_key_pressed(None, Gdk.KEY_q, 0, Gdk.ModifierType(0)))
        self.assertIs(tab.window.active_view, tab)
        self.assertIsNotNone(tab.window.lookup_action("save"))

    def test_files_shortcut_from_agenda_and_editor(self):
        tab = self.app.new_tab()
        tab.buffer.set_text("* Unsaved")
        tab.buffer.place_cursor(tab.buffer.get_iter_at_offset(5))
        self.app.show_agenda()
        window = tab.window
        for _ in range(2):
            self.assertTrue(window._on_key_pressed(
                None, Gdk.KEY_c, 0, Gdk.ModifierType.CONTROL_MASK))
            self.assertTrue(window._on_key_pressed(None, Gdk.KEY_f, 0, Gdk.ModifierType(0)))
            self.assertIs(window.active_view, tab)
            self.assertEqual(window.stack.get_visible_child_name(), "files")
            self.assertEqual(tab.get_text(), "* Unsaved")
            self.assertTrue(tab.buffer.get_modified())
            self.assertEqual(
                tab.buffer.get_iter_at_mark(tab.buffer.get_insert()).get_offset(), 5)

    def test_calendar_shortcut_from_editor_and_calendar(self):
        tab = self.app.new_tab()
        tab.present()
        window = tab.window
        for _ in range(2):
            self.assertTrue(window._on_key_pressed(
                None, Gdk.KEY_c, 0, Gdk.ModifierType.CONTROL_MASK))
            self.assertTrue(window._on_key_pressed(None, Gdk.KEY_c, 0, Gdk.ModifierType(0)))
            self.assertIs(window.active_view, window.agenda)
            self.assertEqual(window.get_title(), "Calendar – GNORG")
            self.assertIn("C-c a", window.active_view._org_key_sequences.commands)
        self.assertIn("C-c a", tab._org_key_sequences.commands)
        self.assertIn("C-c C-c", tab._org_key_sequences.commands)

    def test_files_shortcut_without_open_files_creates_editor(self):
        self.app.show_agenda()
        window = self.app.agenda.window
        self.assertTrue(window._on_key_pressed(
            None, Gdk.KEY_c, 0, Gdk.ModifierType.CONTROL_MASK))
        self.assertTrue(window._on_key_pressed(None, Gdk.KEY_f, 0, Gdk.ModifierType(0)))
        self.assertEqual(window.tabs.get_n_pages(), 1)
        self.assertIs(window.active_view, window.active_tab)

    def test_closing_last_file_from_agenda_keeps_agenda_open(self):
        tab = self.app.new_tab()
        self.app.show_agenda()
        window = tab.window
        tab.close()
        self.assertIn(window, self.app.get_windows())
        self.assertEqual(window.tabs.get_n_pages(), 0)
        self.assertIs(window.active_view, window.agenda)
        window.stack.set_visible_child_name("files")
        self.assertEqual(window.tabs.get_n_pages(), 1)

    def test_agenda_without_files_can_switch_to_a_new_editor(self):
        self.app.show_agenda()
        window = self.app.agenda.window
        self.assertEqual(window.tabs.get_n_pages(), 0)
        window.stack.set_visible_child_name("files")
        self.assertEqual(window.tabs.get_n_pages(), 1)
        self.assertIs(window.active_view, window.active_tab)
        self.assertIsNotNone(window.lookup_action("save"))

    def test_native_view_switcher_has_named_views_and_responsive_bar(self):
        tab = self.app.new_tab()
        window = tab.window
        self.assertIsInstance(window.stack, Adw.ViewStack)
        self.assertIsInstance(window.view_switcher, Adw.ViewSwitcherTitle)
        self.assertIs(window._header.get_title_widget(), window.view_switcher)
        self.assertIs(window.switcher_bar.get_stack(), window.stack)
        pages = window.stack.get_pages()
        self.assertEqual(pages.get_n_items(), 3)
        self.assertEqual([pages.get_item(i).get_title() for i in range(3)],
                         ["Files", "Agenda", "Calendar"])
        self.assertTrue(all(pages.get_item(i).get_icon_name() for i in range(3)))
        tab.present()
        window.view_switcher.set_view_switcher_enabled(False)
        self.wait_for(window.view_switcher.get_title_visible)
        self.assertTrue(window.switcher_bar.get_reveal())
        window.stack.set_visible_child_name("agenda")
        self.assertIs(window._header.get_title_widget(), window.view_switcher)
        self.assertEqual(window.view_switcher.get_title(), "Calendar")
        window.stack.set_visible_child_name("files")
        tab.buffer.set_text("* Unsaved")
        self.assertEqual(window.view_switcher.get_title(), tab.title_widget.get_title())

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

    def test_calendar_event_duration_and_overlap_lanes(self):
        import datetime as dt
        import org

        day = dt.date.today()
        text = (f"* First\nSCHEDULED: <{day.isoformat()} 09:00>\n"
                f"* Second\nSCHEDULED: <{day.isoformat()} 09:15-10:00>\n"
                f"* Third\nSCHEDULED: <{day.isoformat()} 11:00>\n")
        items = org.build_agenda(org.parse_entries(text), day, 1, day)[day]
        self.assertEqual(event_minutes(items[0]), (540, 600))
        self.assertEqual(event_minutes(items[1]), (555, 600))
        self.assertEqual(event_lanes(items), {0: (0, 2), 1: (1, 2), 2: (0, 1)})

    def test_agenda_entries_open_source_and_follow_unsaved_edits(self):
        import datetime as dt
        import org

        self.app.set_hide_weekends(False)
        self.addCleanup(self.app.set_hide_weekends, True)
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "tasks.org")
            today = dt.date.today()
            text = (f"* TODO Dated task\n<{today.isoformat()} 10:00>\n"
                    "* TODO Undated task\n")
            with open(path, "w") as stream:
                stream.write(text)
            tab = self.app.open_file(Gio.File.new_for_path(path))
            self.wait_for(lambda: bool(tab.get_text()))
            with patch.object(self.app, "agenda_directory", return_value=directory):
                self.app.show_agenda()
                agenda = self.app.agenda
                self.assertEqual(len(agenda.entry_buttons), 1)
                item, button = agenda.entry_buttons[0]
                self.assertEqual(item.entry.display_title, "Dated task")
                button.emit("clicked")
                self.assertIs(tab.window.active_tab, tab)
                self.assertEqual(len(self.app.editor_tabs()), 1)
                self.assertEqual(tab.buffer.get_iter_at_mark(tab.buffer.get_insert()).get_line(), 1)
                agenda.present()
                agenda_title = tab.window.get_title()
                agenda._move(1)
                agenda.cycle_todo()
                self.assertEqual(tab.window.get_title(), agenda_title)
                self.assertTrue(tab.buffer.get_modified())
                self.assertTrue(org.parse_entries(tab.get_text())[0].is_done)
                self.assertTrue(agenda.entry_buttons[0][0].entry.is_done)
                self.assertEqual(len(agenda.days), 7)
                agenda._move(1)
                agenda.edit_planning("SCHEDULED")
                self.assertIsNotNone(agenda._active_date_popover)
                text_before = tab.get_text()
                agenda._keyboard_quit()
                self.wait_for(lambda: agenda._active_date_popover is None)
                self.assertEqual(tab.get_text(), text_before)
                with patch.object(self.app, "save_session") as save_session:
                    with patch.object(self.app, "quit"):
                        tab.buffer.set_modified(False)
                        self.app._on_quit()
                    save_session.assert_called_once_with()

    def test_calendar_shows_only_standalone_active_timestamps(self):
        import datetime as dt
        import org

        self.app.show_agenda()
        agenda = self.app.agenda
        day = agenda.start
        overdue = day - dt.timedelta(days=7)
        upcoming = dt.date.today() + dt.timedelta(days=2)
        text = (
            f"* TODO Other stuff SCHEDULED: <{day.isoformat()}>\n"
            f"* TODO Inline deadline DEADLINE: <{day.isoformat()}>\n"
            f"* TODO Separated planning\n\nSCHEDULED: <{day.isoformat()}>\n"
            f"* TODO Scheduled\nSCHEDULED: <{day.isoformat()} 09:00>\n"
            f"* TODO Deadline\nDEADLINE: <{day.isoformat()} 10:00>\n"
            f"* TODO Overdue\nSCHEDULED: <{overdue.isoformat()}>\n"
            f"DEADLINE: <{overdue.isoformat()}>\n"
            f"* TODO Upcoming\nDEADLINE: <{upcoming.isoformat()}>\n"
            f"* Inactive\n[{day.isoformat()}]\n"
            f"* All day\n<{day.isoformat()}>\n"
            f"* Repeating\n<{day.isoformat()} 11:00 +1w>\n"
            f"* TODO Mixed\nSCHEDULED: <{day.isoformat()} 08:00>\n"
            f"DEADLINE: <{day.isoformat()}>\n<{day.isoformat()} 12:00>\n")
        entries = org.parse_entries(text, "/org/events.org")
        with patch.object(self.app, "agenda_entries",
                          return_value=("/org", ["/org/events.org"], entries)):
            agenda.refresh()
            self.assertEqual(
                {item.entry.display_title for item, _ in agenda.entry_buttons},
                {"All day", "Repeating", "Mixed"})
            self.assertTrue(all(item.kind == "timestamp"
                                for items in agenda.days.values() for item in items))
            agenda.shift(1)
            self.assertEqual(
                [item.entry.display_title for item, _ in agenda.entry_buttons], ["Repeating"])

    def test_calendar_labels_timestamps_with_adjacent_text_and_opens_source_line(self):
        import org

        self.app.show_agenda()
        agenda = self.app.agenda
        date = agenda.start.isoformat()
        text = (f"* TODO Other stuff\nSCHEDULED: <{date}>\n\n"
                f"<{date}> Asdf\n"
                f"Another event <{date} 10:00>\n"
                f"<{date} 11:00>\n")
        entries = org.parse_entries(text, "/org/events.org")
        with patch.object(self.app, "agenda_entries",
                          return_value=("/org", ["/org/events.org"], entries)):
            agenda.refresh()
            labels = {button.get_child().get_last_child().get_label(): (item, button)
                      for item, button in agenda.entry_buttons}
            self.assertEqual(set(labels), {"Asdf", "Another event", "TODO Other stuff"})
            timed_item, timed_button = labels["Another event"]
            self.assertEqual(event_minutes(timed_item), (600, 660))
            self.assertEqual(timed_button.get_size_request()[1], 59)
            item, button = labels["Asdf"]
            self.assertEqual(item.timestamp_line, 3)
            with patch.object(self.app, "open_location") as open_location:
                button.emit("clicked")
                open_location.assert_called_once_with("/org/events.org", 3)
                open_location.reset_mock()
                agenda.selected_item = item
                agenda.open_entry()
                open_location.assert_called_once_with("/org/events.org", 3)

    def test_weekly_agenda_includes_all_seven_days_and_no_global_todos(self):
        import datetime as dt
        import org

        tab = self.app.new_tab()
        tab.present()
        self.assertTrue(tab.window._on_key_pressed(
            None, Gdk.KEY_c, 0, Gdk.ModifierType.CONTROL_MASK))
        self.assertTrue(tab.window._on_key_pressed(None, Gdk.KEY_a, 0, Gdk.ModifierType(0)))
        agenda = tab.window.weekly_agenda
        self.assertIs(tab.window.active_view, agenda)
        self.assertEqual(tab.window.get_title(), "Agenda – GNORG")
        start = agenda.start
        saturday = start + dt.timedelta(days=5)
        text = (f"* TODO Scheduled task\nSCHEDULED: <{start.isoformat()} 09:00>\n"
                f"* TODO Deadline task\nDEADLINE: <{start.isoformat()}>\n"
                f"* Weekend event\n<{saturday.isoformat()} 12:00>\n"
                "* TODO Undated task\n")
        entries = org.parse_entries(text, "/org/test.org")
        with patch.object(self.app, "agenda_entries",
                          return_value=("/org", ["/org/test.org"], entries)):
            agenda.refresh()
            self.assertEqual(sum(line.kind == "date" for line in agenda.lines), 7)
            output = "\n".join(line.text for line in agenda.lines)
            self.assertIn("Scheduled:", output)
            self.assertIn("Deadline:", output)
            self.assertIn("Weekend event", output)
            self.assertNotIn("Undated task", output)
            self.assertNotIn("Global list", output)
            self.assertFalse(any(line.kind == "separator" for line in agenda.lines))
            self.assertEqual(tab.window.tabs.get_n_pages(), 1)
            item, button = next((item, button) for item, button in agenda.entry_buttons
                                if item.entry.display_title == "Scheduled task")
            with patch.object(self.app, "open_location") as open_location:
                button.emit("clicked")
                open_location.assert_called_once_with("/org/test.org", 0)
            agenda.selected_item = item
            agenda.shift(1)
            self.assertEqual((agenda.start - start).days, 7)
            agenda.goto_today()
            self.assertEqual(agenda.start, start)
        agenda.close()
        self.assertIs(tab.window.active_view, tab)

    def test_weekly_agenda_refreshes_unsaved_edits_without_opening_calendar(self):
        import datetime as dt

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "tasks.org")
            text = f"* TODO First\nSCHEDULED: <{dt.date.today().isoformat()} 09:00>\n"
            with open(path, "w") as stream:
                stream.write(text)
            tab = self.app.open_file(Gio.File.new_for_path(path))
            self.wait_for(lambda: bool(tab.get_text()))
            with patch.object(self.app, "agenda_directory", return_value=directory):
                self.app.show_weekly_agenda(tab.window)
                agenda = tab.window.weekly_agenda
                self.assertIsNone(tab.window.agenda)
                self.assertEqual(len(agenda.entry_buttons), 1)
                tab.buffer.set_text(text.replace("First", "Changed"))
                self.wait_for(lambda: any(
                    item.entry.display_title == "Changed" for item, _ in agenda.entry_buttons))
                agenda._move(1)
                agenda.cycle_todo()
                self.assertIn("* DONE Changed", tab.get_text())
                self.assertTrue(tab.buffer.get_modified())
                self.assertIs(tab.window.active_view, agenda)
                agenda._move(1)
                agenda.edit_planning("DEADLINE")
                self.assertIsNotNone(agenda._active_date_popover)
                agenda._keyboard_quit()
                self.wait_for(lambda: agenda._active_date_popover is None)

    def test_agenda_j_k_navigate_entries_without_wrapping(self):
        import datetime as dt

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "events.org")
            with open(path, "w") as stream:
                stream.write(f"* First <{dt.date.today().isoformat()}>\n"
                             f"* Second <{dt.date.today().isoformat()}>\n")
            with patch.object(self.app, "agenda_directory", return_value=directory):
                tab = self.app.new_tab()
                self.app.show_weekly_agenda(tab.window)
                agenda = tab.window.weekly_agenda
                first, second = [item for item, _button in agenda.entry_buttons]
                for key, expected in (
                        (Gdk.KEY_k, first), (Gdk.KEY_j, second),
                        (Gdk.KEY_j, second), (Gdk.KEY_k, first), (Gdk.KEY_k, first)):
                    self.assertTrue(tab.window._on_key_pressed(
                        None, key, 0, Gdk.ModifierType(0)))
                    self.assertEqual(agenda.selected_item, expected)
                self.assertEqual(tab.window.stack.get_visible_child_name(), "weekly-agenda")
                self.assertFalse(agenda._on_key_pressed(
                    None, Gdk.KEY_j, 0, Gdk.ModifierType.CONTROL_MASK))
                for key in (Gdk.KEY_space, Gdk.KEY_Return):
                    with patch.object(self.app, "open_location") as open_location:
                        self.assertTrue(tab.window._on_key_pressed(
                            None, key, 0, Gdk.ModifierType(0)))
                        open_location.assert_called_once_with(path, first.entry.line)

    def test_agenda_cursor_navigates_and_keeps_selection_when_cycling_todo(self):
        import datetime as dt

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "tasks.org")
            date = dt.date.today().isoformat()
            with open(path, "w") as stream:
                stream.write(f"* TODO First\n<{date} 09:00>\n"
                             f"* TODO Second\n<{date} 10:00>\n")
            tab = self.app.open_file(Gio.File.new_for_path(path))
            self.wait_for(lambda: bool(tab.get_text()))
            with patch.object(self.app, "agenda_directory", return_value=directory):
                self.app.show_weekly_agenda(tab.window)
                agenda = tab.window.weekly_agenda
                self.assertEqual(agenda.selected_item.entry.display_title, "First")
                self.assertTrue(agenda.entry_buttons[0][1].has_css_class("agenda-cursor"))
                for key in (Gdk.KEY_Down, Gdk.KEY_t, Gdk.KEY_t):
                    self.assertTrue(tab.window._on_key_pressed(
                        None, key, 0, Gdk.ModifierType(0)))
                    self.assertEqual(agenda.selected_item.entry.display_title, "Second")
                    self.assertTrue(agenda.entry_buttons[1][1].has_css_class("agenda-cursor"))
                    self.assertFalse(agenda.entry_buttons[0][1].has_css_class("agenda-cursor"))
                self.assertNotIn("* TODO Second", tab.get_text())
                self.assertTrue(tab.buffer.get_modified())
                self.assertTrue(tab.window._on_key_pressed(
                    None, Gdk.KEY_Up, 0, Gdk.ModifierType(0)))
                self.assertEqual(agenda.selected_item.entry.display_title, "First")
                agenda.refresh()
                self.assertEqual(agenda.selected_item.entry.display_title, "First")
                self.assertTrue(agenda.entry_buttons[0][1].has_css_class("agenda-cursor"))

    def test_weekly_agenda_colors_keywords_and_uses_compact_rows(self):
        import datetime as dt
        import org
        from gi.repository import Pango

        self.app.show_weekly_agenda()
        window = self.app.editor_windows()[0]
        agenda = window.weekly_agenda
        date = dt.date.today().isoformat()
        entries = org.parse_entries(
            f"* TODO Café <{date}>\n* DONE Résumé <{date}>\n", "/org/test.org")
        manager = Adw.StyleManager.get_default()
        original_scheme = manager.get_color_scheme()
        self.addCleanup(manager.set_color_scheme, original_scheme)
        with patch.object(self.app, "agenda_entries",
                          return_value=("/org", ["/org/test.org"], entries)):
            for mode in (Adw.ColorScheme.FORCE_LIGHT, Adw.ColorScheme.FORCE_DARK):
                manager.set_color_scheme(mode)
                agenda.refresh()
                colors = {}
                for item, button in agenda.entry_buttons:
                    label = button.get_child()
                    self.assertEqual(
                        label.get_pango_context().get_font_description().get_weight(),
                        Pango.Weight.NORMAL)
                    self.assertLessEqual(label.get_margin_top() + label.get_margin_bottom(), 2)
                    attributes = label.get_attributes()
                    iterator = attributes.get_iterator()
                    index = len(label.get_text().split(item.entry.keyword)[0].encode("utf-8"))
                    while iterator.range()[1] <= index:
                        self.assertTrue(iterator.next())
                    foreground = iterator.get(Pango.AttrType.FOREGROUND)
                    color = foreground.as_color().color
                    colors[item.entry.keyword] = (color.red, color.green, color.blue)
                    if item.entry.keyword == "TODO":
                        self.assertGreater(color.red, color.green)
                    else:
                        self.assertGreater(color.green, color.red)
                    weight_attribute = iterator.get(Pango.AttrType.WEIGHT)
                    weight = weight_attribute.as_int().value
                    self.assertEqual(weight, Pango.Weight.BOLD)
                self.assertNotEqual(colors["TODO"], colors["DONE"])

    def test_agenda_highlights_today_and_updates_now_marker(self):
        import datetime as dt

        self.app.show_weekly_agenda()
        agenda = self.app.editor_windows()[0].weekly_agenda
        highlighted = []
        row = agenda.listbox.get_first_child()
        while row is not None:
            if row.has_css_class("agenda-today"):
                highlighted.append(row)
            row = row.get_next_sibling()
        self.assertEqual(len(highlighted), 1)
        self.assertEqual(len([line for line in agenda.lines if line.kind == "time-grid"]), 7)
        self.assertEqual(len([line for line in agenda.lines if line.kind == "now"]), 1)
        with patch.object(agenda, "refresh") as refresh:
            self.assertTrue(agenda._refresh_clock())
            refresh.assert_called_once_with()
        agenda.shift(1)
        self.assertFalse(any(line.kind == "now" for line in agenda.lines))
        agenda.goto_today()
        now = next(line for line in agenda.lines if line.kind == "now")
        self.assertIn(f"{dt.datetime.now().hour:2d}:", now.text)

    def test_weekly_agenda_font_size_matches_editor_at_shared_zoom_levels(self):
        import datetime as dt
        import org

        tab = self.app.new_tab()
        self.app.show_weekly_agenda(tab.window)
        agenda = tab.window.weekly_agenda
        entries = org.parse_entries(f"* Event <{dt.date.today().isoformat()}>\n")
        display = Gdk.Display.get_default()
        Gtk.StyleContext.remove_provider_for_display(display, self.app.zoom_css)
        Gtk.StyleContext.add_provider_for_display(
            display, self.app.zoom_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        original_zoom = self.app.zoom
        self.addCleanup(self.app.set_zoom, original_zoom, False)
        with patch.object(self.app, "agenda_entries", return_value=("/org", ["test.org"], entries)):
            agenda.refresh()
            label = agenda.entry_buttons[0][1].get_child()
            heading = agenda.listbox.get_first_child().get_child()
            sizes = []
            for zoom in (100, 150, 70):
                tab.present()
                self.wait_for(tab.view.get_mapped)
                self.app.set_zoom(zoom, notify=False)
                if sizes:
                    self.wait_for(lambda: tab.view.get_pango_context().get_font_description().get_size()
                                  != sizes[-1])
                editor_size = tab.view.get_pango_context().get_font_description().get_size()
                agenda.present()
                label = agenda.entry_buttons[0][1].get_child()
                heading = agenda.listbox.get_first_child().get_child()
                if sizes:
                    self.wait_for(lambda: label.get_pango_context().get_font_description().get_size()
                                  != sizes[-1])
                self.wait_for(lambda: label.get_pango_context().get_font_description().get_size()
                              == editor_size)
                self.assertEqual(heading.get_pango_context().get_font_description().get_size(),
                                 editor_size)
                sizes.append(editor_size)
            self.assertGreater(sizes[1], sizes[0])
            self.assertLess(sizes[2], sizes[0])

    def test_zoom_notification_targets_active_view_and_reuses_toast(self):
        tab = self.app.new_tab()
        tab.present()
        self.app.show_weekly_agenda(tab.window)
        agenda = tab.window.weekly_agenda
        self.wait_for(lambda: self.app.get_active_window() is tab.window)
        original_zoom = self.app.zoom
        self.addCleanup(self.app.set_zoom, original_zoom, False)
        self.app.set_zoom(100, notify=False)
        self.app.set_zoom(110)
        toast = agenda._zoom_toast
        self.assertIsNotNone(toast)
        self.assertEqual(toast.get_title(), "Zoom 110%")
        self.app.set_zoom(120)
        self.assertIs(agenda._zoom_toast, toast)
        self.assertEqual(toast.get_title(), "Zoom 120%")
        self.assertIsNone(tab._zoom_toast)
        toast.dismiss()
        self.assertIsNone(agenda._zoom_toast)
        self.app.set_zoom(130, notify=False)
        self.assertIsNone(agenda._zoom_toast)
        tab.present()
        self.app.set_zoom(140)
        self.assertEqual(tab._zoom_toast.get_title(), "Zoom 140%")
        self.assertIsNone(agenda._zoom_toast)

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
                self.assertIsNone(self.app.window_for_path(missing.get_path()))

    def test_session_restore_with_only_missing_files_leaves_pristine_editor(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Gio.File.new_for_path(os.path.join(directory, "missing.org"))
            settings = Mock()
            settings.get_strv.return_value = [missing.get_uri()]
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
                        stream.write("* " + name)
                    files.append(Gio.File.new_for_path(path))
                    self.app.open_file(files[-1])
                self.wait_for(lambda: all(tab.get_text() for tab in self.app.editor_tabs()))
                first = self.app.window_for_path(files[0].get_path())
                first.present()
                self.app.new_tab().present()
                with patch.object(self.app, "quit"):
                    self.app._on_quit()
                self.assertEqual(settings.get_string("session-active-file"), files[0].get_uri())
                self.app._session_restored = False
                self.app.do_activate()
                window = self.app.editor_windows()[0]
                self.assertEqual(window.active_tab.file.get_uri(), files[0].get_uri())
                self.wait_for(lambda: window.active_tab.get_text() == "* first.org")
                self.assertEqual(len(window.documents()), 2)
                self.assertEqual(window.stack.get_visible_child_name(), "files")
                self.app.do_open([files[1]], 1, "")
                self.assertEqual(window.active_tab.file.get_uri(), files[1].get_uri())

    def test_control_scroll_zooms_and_respects_limits(self):
        controller = Mock()
        controller.get_current_event_state.return_value = Gdk.ModifierType.CONTROL_MASK
        self.app.set_zoom(100, notify=False)
        self.assertTrue(self.app._on_zoom_scroll(controller, 0, -1))
        self.assertEqual(self.app.zoom, 110)
        self.assertTrue(self.app._on_zoom_scroll(controller, 0, 1))
        self.assertEqual(self.app.zoom, 100)
        for limit, delta in ((ZOOM_MAX, -1), (ZOOM_MIN, 1)):
            self.app.set_zoom(limit, notify=False)
            self.assertTrue(self.app._on_zoom_scroll(controller, 0, delta))
            self.assertEqual(self.app.zoom, limit)
        self.app.set_zoom(100, notify=False)
        controller.get_current_event_state.return_value = Gdk.ModifierType(0)
        self.assertFalse(self.app._on_zoom_scroll(controller, 0, -1))
        self.assertEqual(self.app.zoom, 100)

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
