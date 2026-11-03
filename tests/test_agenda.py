"""Agenda integration tests."""

import os
import tempfile
from unittest.mock import patch

from editor_test_case import EditorTestCase
from vim import Gio
from vim import Gdk, Gtk
from vim import Adw


class AgendaTests(EditorTestCase):
    def test_agenda_clicks_select_before_opening_even_when_focus_selects_first(self):
        import datetime as dt
        import org

        self.app.show_weekly_agenda()
        agenda = self.app.editor_windows()[0].weekly_agenda
        date = dt.date.today()
        entries = org.parse_entries(
            f"* First\n<{date}>\n* Second\n<{date}>\n", "/org/events.org")
        with patch.object(self.app, "agenda_entries",
                          return_value=("/org", ["/org/events.org"], entries)):
            agenda.refresh()
        first, first_button = agenda.entry_buttons[0]
        second, second_button = agenda.entry_buttons[1]
        with patch.object(self.app, "open_location") as open_location:
            first_button.emit("clicked")
            open_location.assert_not_called()
            self.assertEqual(agenda.selected_item, first)
            self.assertTrue(first_button.has_css_class("agenda-cursor"))
            agenda._select(second)
            second_button.emit("clicked")
            open_location.assert_not_called()
            self.assertTrue(second_button.has_css_class("agenda-cursor"))
            self.assertFalse(first_button.has_css_class("agenda-cursor"))
            agenda._select(first)
            first_button.emit("clicked")
            open_location.assert_not_called()
            first_button.emit("clicked")
            open_location.assert_called_once_with("/org/events.org", first.timestamp_line)
            open_location.reset_mock()
            second_button.emit("clicked")
            open_location.assert_not_called()
            agenda.open_entry()
            open_location.assert_called_once_with("/org/events.org", second.timestamp_line)

    def test_save_key_sequence_saves_all_files_across_windows_in_agenda_and_calendar(self):
        import datetime as dt

        with tempfile.TemporaryDirectory() as directory:
            paths = [os.path.join(directory, name) for name in ("first.org", "second.org")]
            original = f"* TODO Event\n<{dt.date.today().isoformat()}>\n"
            for path in paths:
                with open(path, "w") as stream:
                    stream.write(original)
            first = self.app.open_file(Gio.File.new_for_path(paths[0]))
            self.wait_for(lambda: first.get_text() == original)
            second = self.app.open_file(Gio.File.new_for_path(paths[1]))
            self.wait_for(lambda: second.get_text() == original)
            first.buffer.set_text(original.replace("TODO Event", "DONE Selected"))
            second.buffer.set_text(original.replace("TODO Event", "TODO Other"))
            third = self.app.new_window().new_tab()
            third.file = Gio.File.new_for_path(os.path.join(directory, "third.org"))
            third.buffer.set_text("* TODO Other window\n")
            with patch.object(self.app, "agenda_directory", return_value=directory):
                for show, name in ((self.app.show_weekly_agenda, "weekly_agenda"),
                                   (self.app.show_agenda, "agenda")):
                    for tab in (first, second, third):
                        tab.buffer.set_modified(True)
                    show(second.window)
                    agenda = getattr(second.window, name)
                    agenda.selected_item = None
                    with patch.object(agenda, "toast") as toast:
                        for key in (Gdk.KEY_x, Gdk.KEY_s):
                            self.assertTrue(second.window._on_key_pressed(
                                None, key, 0, Gdk.ModifierType.CONTROL_MASK))
                        self.wait_for(lambda: all(
                            not tab.buffer.get_modified() for tab in (first, second, third)))
                        messages = [call.args[0] for call in toast.call_args_list]
                        self.assertEqual(len(messages), 3)
                        self.assertTrue(all("written" in message for message in messages))
                    self.assertIs(second.window.active_view, agenda)
                with open(paths[0]) as stream:
                    self.assertIn("DONE Selected", stream.read())
                with open(paths[1]) as stream:
                    self.assertIn("TODO Other", stream.read())
                with open(third.path) as stream:
                    self.assertEqual(stream.read(), "* TODO Other window\n")

    def test_save_all_reports_no_unsaved_changes_without_requiring_selection(self):
        import datetime as dt
        import org

        self.app.show_weekly_agenda()
        agenda = self.app.editor_windows()[0].weekly_agenda
        with patch.object(agenda, "toast") as toast:
            agenda.selected_item = None
            agenda.save()
            toast.assert_called_once_with("No unsaved changes.")
            entries = org.parse_entries(
                f"* Event\n<{dt.date.today().isoformat()}>\n", "/org/closed.org")
            agenda.selected_item = org.build_agenda(
                entries, dt.date.today(), 1, dt.date.today())[dt.date.today()][0]
            agenda.save()
            self.assertIn("No unsaved changes", toast.call_args.args[0])

    def test_save_all_prompts_for_untitled_and_continues_after_cancellation(self):
        untitled = self.app.new_tab()
        untitled.buffer.set_text("* Unsaved\n")
        other = self.app.new_tab()
        other.file = Gio.File.new_for_path("/org/other.org")
        other.buffer.set_text("* Changed\n")
        self.app.show_weekly_agenda(other.window)
        agenda = other.window.weekly_agenda
        with patch.object(untitled, "save_as") as save_as, patch.object(other, "save") as save:
            agenda.save()
            save_as.assert_called_once()
            self.assertEqual(save_as.call_args.kwargs["notify"], agenda.toast)
            save.assert_not_called()
            save_as.call_args.kwargs["on_cancel"]()
            save.assert_called_once()
            self.assertEqual(save.call_args.kwargs["notify"], agenda.toast)

    def test_agenda_save_failure_is_reported_in_agenda_and_keeps_unsaved_edits(self):
        import datetime as dt
        import org

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "missing", "event.org")
            tab = self.app.new_tab()
            tab.file = Gio.File.new_for_path(path)
            text = f"* TODO Event\n<{dt.date.today().isoformat()}>\n"
            tab.buffer.set_text(text)
            other = tab.window.new_tab()
            other.file = Gio.File.new_for_path(os.path.join(directory, "other.org"))
            other.buffer.set_text("* Saved after failure\n")
            self.app.show_weekly_agenda(tab.window)
            agenda = tab.window.weekly_agenda
            entry = org.parse_entries(text, path)[0]
            agenda.selected_item = org.build_agenda(
                [entry], dt.date.today(), 1, dt.date.today())[dt.date.today()][0]
            with patch.object(agenda, "toast") as toast:
                agenda.actions.lookup_action("save").activate(None)
                self.wait_for(lambda: toast.called)
                self.assertIn("Could not save file:", toast.call_args_list[0].args[0])
                self.wait_for(lambda: not other.buffer.get_modified())
            self.assertTrue(tab.buffer.get_modified())
            self.assertIs(tab.window.active_view, agenda)

    def test_agenda_tags_align_to_right_after_window_resize(self):
        import datetime as dt
        import org

        tab = self.app.new_tab()
        self.app.show_weekly_agenda(tab.window)
        agenda = tab.window.weekly_agenda
        entries = org.parse_entries(
            f"* TODO Event :work:home:\n<{dt.date.today().isoformat()}>\n", "/org/test.org")
        with patch.object(self.app, "agenda_entries", return_value=("/org", ["test.org"], entries)):
            agenda.refresh()
            tab.window.present()
            button = agenda.entry_buttons[0][1]
            content = button.get_child()
            tags = content.get_last_child()
            self.assertEqual(tags.get_label(), ":work:home:")

            def aligned():
                content = agenda.entry_buttons[0][1].get_child()
                tags = content.get_last_child()
                ok, bounds = tags.compute_bounds(content)
                return (ok and content.get_width() > 100
                        and abs(bounds.get_x() + bounds.get_width()
                                - content.get_width() + tags.get_margin_end()) <= 2)

            self.wait_for(aligned)
            width = tab.window.get_width()
            tab.window.set_default_size(width + 180, tab.window.get_height())
            self.wait_for(lambda: tab.window.get_width() != width)
            self.wait_for(aligned)

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
        self.assertIs(agenda.window.lookup_action("save"), agenda.actions.lookup_action("save"))
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
        footer = content.get_last_child()
        navigation = footer.get_start_widget()
        self.assertIs(footer.get_end_widget(), agenda.week_label)
        self.assertIsNone(footer.get_center_widget())
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

    def test_weekly_agenda_includes_all_seven_days_and_no_global_todos(self):
        import datetime as dt
        import org

        tab = self.app.new_tab()
        tab.present()
        self.assertTrue(tab.window._on_key_pressed(
            None, Gdk.KEY_c, 0, Gdk.ModifierType.CONTROL_MASK))
        self.assertTrue(tab.window._on_key_pressed(None, Gdk.KEY_a, 0, Gdk.ModifierType(0)))
        tab.window.get_visible_dialog().activate("a")
        agenda = tab.window.weekly_agenda
        self.assertIs(tab.window.active_view, agenda)
        self.assertEqual(tab.window.get_title(), "Agenda – Gnorg")
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
                open_location.assert_not_called()
                self.assertEqual(agenda.selected_item, item)
                button.emit("clicked")
                open_location.assert_called_once_with("/org/test.org", 0)
            agenda.selected_item = item
            agenda.shift(1)
            self.assertEqual((agenda.start - start).days, 7)
            agenda.goto_today()
            self.assertEqual(agenda.start, start)
        agenda.close()
        self.assertIs(tab.window.active_view, tab)

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

    def test_agenda_colors_entire_rows_and_split_tags_in_light_and_dark_themes(self):
        import datetime as dt
        import org
        from gi.repository import GtkSource, Pango

        self.app.show_weekly_agenda()
        agenda = self.app.editor_windows()[0].weekly_agenda
        day = agenda.start + dt.timedelta(days=2)
        yesterday = day - dt.timedelta(days=1)
        forecast = day + dt.timedelta(days=3)
        entries = org.parse_entries(
            f"* TODO [#A] Scheduled café :work:@U328:\nSCHEDULED: <{day} 09:00>\n"
            f"* TODO Previously scheduled :work:\nSCHEDULED: <{yesterday}>\n"
            f"* TODO Deadline résumé :required:\nDEADLINE: <{day} 10:00>\n"
            f"* TODO Overdue :required:\nDEADLINE: <{yesterday}>\n"
            f"* TODO Forecast :required:\nDEADLINE: <{forecast}>\n"
            f"* DONE Finished deadline :work:\nDEADLINE: <{day}>\n"
            f"* DONE Finished scheduled :work:\nSCHEDULED: <{day}>\n"
            f"* DONE Finished event :work:\n<{day}>\n"
            f"* DONE Body event :work:\n<{day}> Adjacent title\n"
            f"* DONE Without tags\nDEADLINE: <{day}>\n", "/org/test.org")
        manager = Adw.StyleManager.get_default()

        class Today(dt.date):
            @classmethod
            def today(cls):
                return day

        with patch.object(self.app, "agenda_entries",
                          return_value=("/org", ["/org/test.org"], entries)), patch(
                              "agenda.dt.date", Today):
            for dark, scheme_name in ((False, "gnorg"), (True, "gnorg-dark")):
                with patch.object(manager, "get_dark", return_value=dark):
                    agenda.refresh()
                scheme = GtkSource.StyleSchemeManager.get_default().get_scheme(scheme_name)

                def color_for(style):
                    color = Gdk.RGBA()
                    self.assertTrue(color.parse(scheme.get_style(f"org:{style}").props.foreground))
                    return tuple(round(value * 65535)
                                 for value in (color.red, color.green, color.blue))

                kinds = set()
                for item, button in agenda.entry_buttons:
                    kinds.add(item.kind)
                    row_style = ("agenda-done" if item.entry.is_done else
                                 "agenda-deadline" if item.kind.startswith("deadline") else
                                 "agenda-scheduled")
                    content = button.get_child()
                    labels = ([content.get_first_child(), content.get_last_child()]
                              if isinstance(content, Gtk.Box) else [content])
                    for label in labels:
                        text = label.get_text()
                        attributes = label.get_attributes()
                        iterator = attributes.get_iterator()
                        keyword_start = (text.find(item.entry.keyword)
                                         if label is labels[0] and item.timestamp_title is None
                                         else -1)
                        for index, character in enumerate(text):
                            if character.isspace():
                                continue
                            byte_index = len(text[:index].encode("utf-8"))
                            while iterator.range()[1] <= byte_index:
                                self.assertTrue(iterator.next())
                            foreground = iterator.get(Pango.AttrType.FOREGROUND)
                            color = foreground.as_color().color
                            style = ("tag" if label is not labels[0] else
                                     "todo" if item.entry.keyword == "TODO"
                                     and keyword_start <= index < keyword_start + 4
                                     and keyword_start >= 0 else row_style)
                            self.assertEqual(
                                (color.red, color.green, color.blue), color_for(style),
                                (scheme_name, item.kind, text, index))
                self.assertTrue({
                    "scheduled", "scheduled-overdue", "deadline", "deadline-overdue",
                    "deadline-upcoming", "timestamp"} <= kinds)

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
