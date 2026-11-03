"""Custom agenda filtering, persistence, sidebar management and key chooser."""

import datetime as dt
import os
import tempfile
from unittest.mock import Mock, patch

from editor_test_case import EditorTestCase
from customagenda import AgendaChooser, AgendaEditor, CustomAgenda, decode_views, encode_views
import org
from vim import APP_ID, Gdk, Gio, GnorgApplication, Gtk


class CustomAgendaTests(EditorTestCase):
    def test_list_row_activation_opens_builtin_and_custom_agendas(self):
        tab = self.app.new_tab()
        view = CustomAgenda("x", "Example")
        self.app.save_custom_agenda(view)
        pane = tab.window.utility_pane
        pane.agenda_listbox.emit("row-activated", pane.agenda_rows[1])
        self.assertIs(tab.window.active_view, tab.window.weekly_agenda)
        self.assertEqual(tab.window.weekly_agenda.custom, view)
        tab.window.show_files()
        pane.agenda_listbox.emit("row-activated", pane.agenda_rows[0])
        self.assertIs(tab.window.active_view, tab.window.weekly_agenda)
        self.assertIsNone(tab.window.weekly_agenda.custom)

    def test_agenda_rows_share_file_row_padding_and_spacing(self):
        tab = self.app.new_tab()
        tab.buffer.set_text("* Example")
        pane = tab.window.utility_pane
        pane.refresh()
        self.app.save_custom_agenda(CustomAgenda("x", "Example"))
        file_content = pane.rows[0].get_child()
        for row in pane.agenda_rows:
            content = row.get_child()
            self.assertEqual(content.get_margin_start(), file_content.get_margin_start())
            self.assertEqual(content.get_margin_end(), file_content.get_margin_end())
            self.assertEqual(content.get_spacing(), file_content.get_spacing())
            self.assertEqual(content.get_first_child().get_size_request()[0], 16)

    def test_agenda_context_menu_edit_and_confirmed_delete(self):
        tab = self.app.new_tab()
        tab.present()
        view = CustomAgenda("x", "Example")
        self.app.save_custom_agenda(view)
        pane = tab.window.utility_pane
        row = pane.agenda_rows[1]
        gesture = Mock()
        pane._on_agenda_context_pressed(gesture, 1, 10, 10, row, view)
        gesture.set_state.assert_called_once_with(Gtk.EventSequenceState.CLAIMED)

        def buttons():
            result = []
            child = pane._context_menu.get_child().get_first_child()
            while child:
                result.append(child)
                child = child.get_next_sibling()
            return result

        self.assertEqual([button.get_child().get_label() for button in buttons()],
                         ["Edit…", "Delete…"])
        self.assertTrue(all(button.get_child().get_xalign() == 0 for button in buttons()))
        with patch.object(self.app, "edit_custom_agenda") as edit:
            buttons()[0].emit("clicked")
            edit.assert_called_once_with(tab.window, view)
        self.wait_for(lambda: pane._context_menu is None)
        row = pane.agenda_rows[1]
        self.assertTrue(pane._on_agenda_context_key(
            None, Gdk.KEY_F10, 0, Gdk.ModifierType.SHIFT_MASK, row, view))
        buttons()[1].emit("clicked")
        tab.window.get_visible_dialog().emit("response", "cancel")
        self.assertEqual(self.app.custom_agendas, [view])
        self.wait_for(lambda: pane._context_menu is None)
        pane._show_agenda_menu(pane.agenda_rows[1], view, 10, 10)
        buttons()[1].emit("clicked")
        tab.window.get_visible_dialog().emit("response", "delete")
        self.wait_for(lambda: pane._context_menu is None and len(pane.agenda_rows) == 1)
        self.assertEqual(self.app.custom_agendas, [])

    def settings(self):
        directory = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))
        source = Gio.SettingsSchemaSource.new_from_directory(
            directory, Gio.SettingsSchemaSource.get_default(), False)
        return Gio.Settings.new_full(source.lookup(APP_ID, False),
                                     Gio.memory_settings_backend_new(), None)

    def test_filter_groups_combine_with_and_and_choices_with_or(self):
        entries = org.parse_entries(
            "#+CATEGORY: Office\n"
            "* TODO [#A] Write report :work:urgent:\nSCHEDULED: <2026-11-02>\n"
            "* DONE [#B] Review report :work:review:\nDEADLINE: <2026-11-03>\n"
            "* TODO [#A] Private report :work:urgent:private:\n"
            "* TODO [#C] Write notes :home:\n"
            "* Untitled project\n", "/org/tasks.org")
        view = CustomAgenda("r", "Reports", all_tags=("work",), any_tags=("urgent", "review"),
                            excluded_tags=("private",), states=("TODO", "DONE"),
                            priorities=("A", "B"), text="REPORT", category="office")
        self.assertEqual([e.title for e in entries if view.matches(e)],
                         ["Write report", "Review report"])
        for planning, expected in (("scheduled", ["Write report"]),
                                   ("deadline", ["Review report"]), ("unplanned", [])):
            filtered = CustomAgenda("p", "Planned", planning=planning, text="report",
                                    excluded_tags=("private",))
            self.assertEqual([e.title for e in entries if filtered.matches(e)], expected)
        none = CustomAgenda("n", "No state", states=("NONE",))
        self.assertEqual([e.title for e in entries if none.matches(e)], ["Untitled project"])
        self.assertEqual(sum(CustomAgenda("e", "Everything").matches(e) for e in entries), 5)

    def test_serialization_and_invalid_saved_definitions(self):
        view = CustomAgenda("x", "Work", "day", all_tags=("work",), states=("TODO",))
        self.assertEqual(decode_views(encode_views([view])), [view])
        for raw in ('{}', '[null]', '[{"key":"a","title":"Reserved"}]',
                    '[{"key":"x","title":"Bad","all_tags":"work"}]',
                    '[{"key":"x","title":"Bad","states":[null]}]',
                    '[{"key":"x","title":"Bad","kind":"unknown"}]',
                    encode_views([view, view]), '{'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                decode_views(raw)
        for kwargs in ({"key": "aa"}, {"key": " "}, {"title": ""},
                       {"priorities": ("AB",)}, {"all_tags": ("bad!",)},
                       {"states": ("INVALID",)}, {"planning": "unknown"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                CustomAgenda(**{"key": "x", "title": "Test", **kwargs})

    def test_sidebar_add_editor_validation_and_settings_persistence(self):
        tab = self.app.new_tab()
        tab.present()
        settings = self.settings()
        with patch.object(self.app, "settings", settings):
            pane = tab.window.utility_pane
            pane.new_agenda_button.emit("clicked")
            editor = tab.window.get_visible_dialog()
            self.assertIsInstance(editor, AgendaEditor)
            editor.fields["key"].set_text("a")
            editor.fields["title"].set_text("My Work")
            editor._save()
            self.assertTrue(editor.error.get_visible())
            self.assertEqual(self.app.custom_agendas, [])
            editor.fields["key"].set_text("x")
            editor.fields["all_tags"].set_text(":work:urgent:")
            editor.fields["states"].set_text("todo done")
            editor.kind.set_selected(1)
            editor._save()
            view = self.app.custom_agendas[0]
            self.assertEqual(view.all_tags, ("work", "urgent"))
            self.assertEqual(view.states, ("TODO", "DONE"))
            self.assertEqual(view.kind, "week")
            self.assertEqual(decode_views(settings.get_string("custom-agenda-views")), [view])
            self.assertEqual([row.get_title() for row in pane.agenda_rows], ["Agenda", "My Work"])
            pane.agenda_listbox.emit("row-activated", pane.agenda_rows[1])
            self.assertEqual(tab.window.weekly_agenda.custom, view)
            self.assertEqual(tab.window.get_title(), "My Work – Gnorg")
            with self.assertRaisesRegex(ValueError, "already in use"):
                self.app.save_custom_agenda(CustomAgenda("x", "Duplicate"))

    def test_saved_views_restore_at_startup(self):
        settings = self.settings()
        view = CustomAgenda("t", "Tasks", states=("TODO",))
        settings.set_string("custom-agenda-views", encode_views([view]))
        app = GnorgApplication()
        app.set_application_id(APP_ID + ".CustomAgendaTests")
        app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
        with (patch.object(GnorgApplication, "_load_settings", return_value=settings),
              patch("vim.data_path", return_value=None)):
            app.register(None)
        self.addCleanup(app.quit)
        self.assertEqual(app.custom_agendas, [view])

    def test_edit_and_delete_update_multiple_windows_and_active_views(self):
        first = self.app.new_tab()
        second = self.app.new_window().new_tab()
        old = CustomAgenda("x", "Old")
        self.app.save_custom_agenda(old)
        self.app.show_weekly_agenda(first.window, custom=old)
        changed = CustomAgenda("y", "New", "day")
        self.app.save_custom_agenda(changed, old)
        self.assertEqual(first.window.weekly_agenda.custom, changed)
        self.assertTrue(first.window.weekly_agenda.day_view)
        self.assertEqual(first.window.get_title(), "New – Gnorg")
        for window in (first.window, second.window):
            self.assertEqual(window.utility_pane.agenda_rows[1].get_title(), "New")
        self.assertTrue(self.app.delete_custom_agenda(changed))
        self.assertIsNone(first.window.weekly_agenda.custom)
        self.assertEqual(first.window.get_title(), "Agenda – Gnorg")
        self.assertEqual(len(second.window.utility_pane.agenda_rows), 1)

    def test_switching_custom_views_updates_title_and_retains_view_on_return(self):
        tab = self.app.new_tab()
        first = CustomAgenda("x", "First")
        second = CustomAgenda("y", "Second", "day")
        self.app.save_custom_agenda(first)
        self.app.save_custom_agenda(second)
        self.app.show_weekly_agenda(tab.window, custom=first)
        self.app.show_weekly_agenda(tab.window, custom=second)
        self.assertEqual(tab.window.get_title(), "Second – Gnorg")
        tab.window.show_files()
        tab.window.stack.set_visible_child_name("weekly-agenda")
        self.assertEqual(tab.window.weekly_agenda.custom, second)
        self.assertEqual(tab.window.get_title(), "Second – Gnorg")
        self.app.show_weekly_agenda(tab.window)
        self.assertEqual(tab.window.get_title(), "Agenda – Gnorg")

    def test_editor_delete_requires_confirmation(self):
        tab = self.app.new_tab()
        view = CustomAgenda("x", "Example")
        self.app.save_custom_agenda(view)
        editor = self.app.edit_custom_agenda(tab.window, view)
        editor._delete()
        tab.window.get_visible_dialog().emit("response", "cancel")
        self.assertEqual(self.app.custom_agendas, [view])
        editor._delete()
        tab.window.get_visible_dialog().emit("response", "delete")
        self.assertEqual(self.app.custom_agendas, [])

    def test_chooser_shortcut_dispatch_and_cancel(self):
        tab = self.app.new_tab()
        view = CustomAgenda("x", "Example")
        self.app.save_custom_agenda(view)
        for context in ("editor", "agenda", "calendar"):
            if context == "agenda":
                self.app.show_weekly_agenda(tab.window)
            elif context == "calendar":
                self.app.show_agenda(tab.window)
            window = tab.window
            self.assertTrue(window._on_key_pressed(
                None, Gdk.KEY_c, 0, Gdk.ModifierType.CONTROL_MASK))
            self.assertTrue(window._on_key_pressed(None, Gdk.KEY_a, 0, Gdk.ModifierType(0)))
            chooser = window.get_visible_dialog()
            self.assertIsInstance(chooser, AgendaChooser)
            self.assertFalse(chooser._key(None, Gdk.KEY_z, 0, Gdk.ModifierType(0)))
            self.assertTrue(chooser._key(None, Gdk.KEY_x, 0, Gdk.ModifierType(0)))
            self.assertEqual(window.weekly_agenda.custom, view)
        chooser = self.app.choose_agenda(tab.window)
        chooser.activate("a")
        self.assertIsNone(tab.window.weekly_agenda.custom)
        chooser = self.app.choose_agenda(tab.window)
        self.assertTrue(chooser._key(None, Gdk.KEY_g, 0, Gdk.ModifierType.CONTROL_MASK))

    def test_modal_chooser_keys_are_not_consumed_by_agenda_commands(self):
        tab = self.app.new_tab()
        view = CustomAgenda("t", "Tasks", states=("TODO",))
        self.app.save_custom_agenda(view)
        self.app.show_weekly_agenda(tab.window)
        chooser = self.app.choose_agenda(tab.window)
        with patch.object(tab.window.weekly_agenda, "cycle_todo") as cycle:
            self.assertFalse(tab.window._on_key_pressed(
                None, Gdk.KEY_t, 0, Gdk.ModifierType(0)))
            cycle.assert_not_called()
            self.assertTrue(chooser._key(None, Gdk.KEY_t, 0, Gdk.ModifierType(0)))
        self.assertEqual(tab.window.weekly_agenda.custom, view)

    def test_headline_list_includes_undated_entries_and_supports_actions(self):
        tab = self.app.new_tab()
        today = dt.date.today()
        entries = org.parse_entries(
            f"* TODO First :work:\n* DONE Second :work:\n"
            f"* TODO Other :home:\n<{today.isoformat()}>\n", "/org/tasks.org")
        view = CustomAgenda("t", "Work tasks", all_tags=("work",))
        with patch.object(self.app, "agenda_entries",
                          return_value=("/org", ["/org/tasks.org"], entries)):
            self.app.show_weekly_agenda(tab.window, custom=view)
            agenda = tab.window.weekly_agenda
            self.assertEqual([item.entry.title for item, _ in agenda.entry_buttons],
                             ["First", "Second"])
            self.assertEqual(agenda.days, {})
            self.assertFalse(agenda.previous_button.get_sensitive())
            item, button = agenda.entry_buttons[0]
            with patch.object(self.app, "open_location") as opened:
                button.emit("clicked")
                opened.assert_not_called()
                button.emit("clicked")
                opened.assert_called_once_with("/org/tasks.org", 0)
            agenda.selected_item = item
            with patch.object(self.app, "edit_entry", return_value=None) as edit:
                agenda.cycle_todo()
                self.assertEqual(edit.call_args.args[0], entries[0])
            agenda._move(1)
            self.assertIsNotNone(agenda.selected_item)
            self.app.show_weekly_agenda(tab.window)
            self.assertFalse(agenda.headline_view)
            self.assertEqual(len(agenda.days), 7)

    def test_dated_views_filter_before_building_day_and_week_agendas(self):
        tab = self.app.new_tab()
        today = dt.date.today()
        entries = org.parse_entries(
            f"* TODO Work :work:\nSCHEDULED: <{today.isoformat()}>\n"
            f"* Home :home:\n<{today.isoformat()}>\n"
            "* TODO Undated :work:\n", "/org/tasks.org")
        with patch.object(self.app, "agenda_entries",
                          return_value=("/org", ["/org/tasks.org"], entries)):
            for kind, count in (("day", 1), ("week", 7)):
                self.app.show_weekly_agenda(
                    tab.window, custom=CustomAgenda("x", "Work", kind, all_tags=("work",)))
                agenda = tab.window.weekly_agenda
                self.assertEqual(len(agenda.days), count)
                self.assertEqual([item.entry.title for item, _ in agenda.entry_buttons], ["Work"])
                agenda.shift(1)
                self.assertEqual(len(agenda.days), count)

    def test_file_visibility_unsaved_buffers_and_literal_headings(self):
        tab = self.app.new_tab()
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "tasks.org")
            with open(path, "w") as stream:
                stream.write("* TODO Disk\n")
            tab._set_file(Gio.File.new_for_path(path), "* TODO Unsaved :work:\n"
                          "#+BEGIN_SRC org\n* TODO Literal\n#+END_SRC\n"
                          "* TODO Actual :work:\n")
            with patch.object(self.app, "agenda_directory", return_value=directory):
                view = CustomAgenda("t", "Tasks", states=("TODO",))
                self.app.show_weekly_agenda(tab.window, custom=view)
                agenda = tab.window.weekly_agenda
                self.assertEqual([item.entry.title for item, _ in agenda.entry_buttons],
                                 ["Unsaved", "Actual"])
                self.app.hidden_agenda_files.add(tab.file.get_uri())
                agenda.refresh()
                self.assertEqual(agenda.entry_buttons, [])
                self.assertIn("No matching headlines.", [line.text for line in agenda.lines])

    def test_persistence_errors_do_not_mutate_views(self):
        tab = self.app.new_tab()
        tab.present()
        settings = Mock()
        settings.set_string.return_value = False
        view = CustomAgenda("x", "Example")
        with patch.object(self.app, "settings", settings):
            with self.assertRaisesRegex(ValueError, "Could not save"):
                self.app.save_custom_agenda(view)
            self.assertEqual(self.app.custom_agendas, [])
            self.app.custom_agendas = [view]
            with patch.object(tab, "toast") as toast:
                self.assertFalse(self.app.delete_custom_agenda(view))
                toast.assert_called_once()
            self.assertEqual(self.app.custom_agendas, [view])
