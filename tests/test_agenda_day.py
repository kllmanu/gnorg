"""Single-day textual agenda navigation."""

import datetime as dt
from unittest.mock import patch

from editor_test_case import EditorTestCase
import org
from vim import Gdk, GLib


class AgendaDayTests(EditorTestCase):
    def test_month_groups_weeks_and_keeps_dated_appointments_and_planning(self):
        agenda = self.agenda()
        agenda.start = dt.date(2026, 11, 2)
        entries = org.parse_entries(
            "* Appointment :@U328:\n<2026-11-17 12:00>\n"
            "* TODO Scheduled\nSCHEDULED: <2026-11-30>\n"
            "* TODO Deadline\nDEADLINE: <2026-11-20>\n"
            "* Outside\n<2026-12-01>\n", "/org/events.org")
        with patch.object(self.app, "agenda_entries",
                          return_value=("/org", ["/org/events.org"], entries)):
            agenda.refresh()
            self.press(agenda, Gdk.KEY_m)
            self.assertTrue(agenda.month_view)
            self.assertFalse(agenda.day_view)
            self.assertEqual(agenda.start, dt.date(2026, 11, 1))
            self.assertEqual(len(agenda.days), 30)
            self.assertEqual(sum(line.kind == "date" for line in agenda.lines), 0)
            self.assertEqual(sum(line.kind == "week" for line in agenda.lines), 6)
            self.assertFalse(any(line.kind in ("time-grid", "now") for line in agenda.lines))
            appointment_line = next(line for line in agenda.lines
                                    if line.kind == "item" and "Appointment" in line.text)
            self.assertTrue(appointment_line.text.startswith("  Tue 17 Nov  "))
            self.assertTrue(any(line.text.startswith("Week 44: Sun 1 Nov 2026")
                                for line in agenda.lines))
            self.assertTrue(any(line.text.startswith("Week 49: Mon 30 Nov 2026")
                                for line in agenda.lines))
            self.assertEqual(agenda.lines[0].text, "Month-agenda (November 2026):")
            self.assertEqual(agenda.previous_button.get_tooltip_text(), "Previous month")
            self.assertNotIn("Outside", {item.entry.title for item, _ in agenda.entry_buttons})
            self.assertTrue({"timestamp", "scheduled", "deadline"} <= {
                item.kind for item, _ in agenda.entry_buttons})
            appointment, button = next((item, button) for item, button in agenda.entry_buttons
                                       if item.entry.title == "Appointment")
            agenda.selected_item = appointment
            self.press(agenda, Gdk.KEY_d)
            self.assertEqual(agenda.start, dt.date(2026, 11, 17))
            self.assertTrue(agenda.day_view)
            self.assertFalse(agenda.month_view)
            self.press(agenda, Gdk.KEY_m)
            self.press(agenda, Gdk.KEY_w)
            self.assertEqual(len(agenda.days), 7)
            self.assertFalse(agenda.month_view)

    def test_month_navigation_handles_year_boundaries_and_leap_years(self):
        agenda = self.agenda()
        agenda.start = dt.date(2027, 12, 6)
        agenda.selected_item = None
        self.press(agenda, Gdk.KEY_m)
        self.press(agenda, Gdk.KEY_f)
        self.assertEqual(agenda.start, dt.date(2028, 1, 1))
        self.assertEqual(len(agenda.days), 31)
        agenda.next_button.emit("clicked")
        self.assertEqual(agenda.start, dt.date(2028, 2, 1))
        self.assertEqual(len(agenda.days), 29)
        agenda.previous_button.emit("clicked")
        self.assertEqual(agenda.start, dt.date(2028, 1, 1))
        self.press(agenda, Gdk.KEY_b)
        self.assertEqual(agenda.start, dt.date(2027, 12, 1))
        agenda.window.utility_pane.calendar.set_date(
            GLib.DateTime.new_local(2027, 2, 17, 0, 0, 0))
        self.assertTrue(agenda.month_view)
        self.assertEqual(agenda.start, dt.date(2027, 2, 1))
        self.assertEqual(len(agenda.days), 28)
        self.press(agenda, Gdk.KEY_period)
        self.assertEqual(agenda.start, dt.date.today().replace(day=1))
        self.assertFalse(agenda.today_button.get_sensitive())

    def agenda(self):
        tab = self.app.new_tab()
        self.app.show_weekly_agenda(tab.window)
        return tab.window.weekly_agenda

    def press(self, agenda, key):
        return agenda.window._on_key_pressed(None, key, 0, Gdk.ModifierType(0))

    def test_day_filters_entries_and_week_restores_seven_days(self):
        agenda = self.agenda()
        start = agenda.start
        day = start + dt.timedelta(days=5)
        entries = org.parse_entries(
            f"* First\n<{start.isoformat()}>\n"
            f"* Selected\n<{day.isoformat()} 12:00>\n", "/org/events.org")
        with patch.object(self.app, "agenda_entries",
                          return_value=("/org", ["/org/events.org"], entries)):
            agenda.refresh()
            agenda.selected_item = agenda.entry_buttons[-1][0]
            self.assertTrue(self.press(agenda, Gdk.KEY_d))
            self.assertEqual(agenda.start, day)
            self.assertEqual(list(agenda.days), [day])
            self.assertEqual(sum(line.kind == "date" for line in agenda.lines), 1)
            self.assertTrue(agenda.lines[0].text.startswith("Day-agenda"))
            self.assertEqual([item.entry.title for item, _ in agenda.entry_buttons], ["Selected"])
            self.assertEqual(agenda.previous_button.get_tooltip_text(), "Previous day")
            self.assertEqual(agenda.week_label.get_label(),
                             f"{org.MONTH_NAMES[day.month - 1]} {day.year}")
            self.press(agenda, Gdk.KEY_w)
            self.assertEqual(agenda.start, start)
            self.assertEqual(len(agenda.days), 7)
            self.assertTrue(agenda.lines[0].text.startswith("Week-agenda"))
            self.assertEqual(len(agenda.entry_buttons), 2)

    def test_day_uses_calendar_without_selection_and_navigation_moves_one_day(self):
        agenda = self.agenda()
        day = dt.date(2026, 12, 31)
        agenda.window.utility_pane.select_day(day)
        agenda.selected_item = None
        self.press(agenda, Gdk.KEY_d)
        self.assertEqual(agenda.start, day)
        self.press(agenda, Gdk.KEY_f)
        self.assertEqual(agenda.start, dt.date(2027, 1, 1))
        selected = agenda.window.utility_pane.calendar.get_date()
        self.assertEqual((selected.get_year(), selected.get_month(), selected.get_day_of_month()),
                         (2027, 1, 1))
        self.press(agenda, Gdk.KEY_b)
        self.assertEqual(agenda.start, day)
        agenda.next_button.emit("clicked")
        self.assertEqual(agenda.start, dt.date(2027, 1, 1))
        self.press(agenda, Gdk.KEY_period)
        self.assertEqual(agenda.start, dt.date.today())
        self.assertFalse(agenda.today_button.get_sensitive())
        self.assertTrue(agenda.day_view)
        self.press(agenda, Gdk.KEY_w)
        start = agenda.start
        self.press(agenda, Gdk.KEY_f)
        self.assertEqual(agenda.start, start + dt.timedelta(days=7))

    def test_calendar_click_preserves_day_mode_and_refresh_keeps_single_day(self):
        agenda = self.agenda()
        self.press(agenda, Gdk.KEY_d)
        day = dt.date(2027, 2, 7)
        agenda.window.utility_pane.calendar.set_date(
            GLib.DateTime.new_local(day.year, day.month, day.day, 0, 0, 0))
        agenda.window.utility_pane._on_day_selected(agenda.window.utility_pane.calendar)
        self.assertEqual(agenda.start, day)
        self.press(agenda, Gdk.KEY_g)
        self.assertEqual(list(agenda.days), [day])
        agenda.window.show_files()
        agenda.present()
        self.assertTrue(agenda.day_view)
        self.assertEqual(list(agenda.days), [day])

    def test_graphical_calendar_and_modified_shortcuts_are_unchanged(self):
        agenda = self.agenda()
        self.assertFalse(agenda.window._on_key_pressed(
            None, Gdk.KEY_d, 0, Gdk.ModifierType.CONTROL_MASK))
        self.assertFalse(agenda.day_view)
        self.app.show_agenda(agenda.window)
        calendar = agenda.window.agenda
        self.assertFalse(self.press(calendar, Gdk.KEY_d))
        self.assertFalse(calendar.day_view)
