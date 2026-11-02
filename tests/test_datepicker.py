"""Optional date-picker time controls and free-form input synchronization."""

import datetime as dt

from editor_test_case import EditorTestCase
from datepicker import DatePopover
from org import Timestamp
from vim import GLib


class DatePickerTests(EditorTestCase):
    def test_optional_start_and_end_times_preserve_timestamp_metadata(self):
        initial = Timestamp(dt.date(2026, 11, 2), repeater=("+", 1, "w"), warning=(2, "d"))
        picker = DatePopover("Timestamp", initial)
        self.addCleanup(picker.run_dispose)
        self.assertFalse(picker.time_enabled.get_active())
        self.assertFalse(picker.time_controls.get_visible())
        picker.time_enabled.set_active(True)
        picker.start_hour.set_value(9)
        picker.start_minute.set_value(30)
        self.assertEqual(picker.timestamp.time, dt.time(9, 30))
        picker.end_enabled.set_active(True)
        picker.end_hour.set_value(11)
        picker.end_minute.set_value(45)
        self.assertEqual(picker.timestamp.end_time, dt.time(11, 45))
        self.assertEqual(picker.timestamp.repeater, initial.repeater)
        self.assertEqual(picker.timestamp.warning, initial.warning)
        applied = []
        picker.connect("apply", lambda _picker, timestamp: applied.append(timestamp))
        picker._apply()
        self.assertEqual(applied[0].time, dt.time(9, 30))
        self.assertEqual(applied[0].end_time, dt.time(11, 45))
        picker.end_enabled.set_active(False)
        self.assertIsNone(picker.timestamp.end_time)
        picker.time_enabled.set_active(False)
        self.assertIsNone(picker.timestamp.time)
        self.assertFalse(picker.time_controls.get_visible())

    def test_typed_times_update_controls_and_calendar_keeps_times(self):
        picker = DatePopover("Timestamp", today=dt.date(2026, 11, 2))
        self.addCleanup(picker.run_dispose)
        picker.entry.set_text("2026-11-03 14:15-16:30 +1w")
        self.assertTrue(picker.time_enabled.get_active())
        self.assertTrue(picker.end_enabled.get_active())
        self.assertEqual(picker.start_hour.get_value_as_int(), 14)
        self.assertEqual(picker.start_minute.get_value_as_int(), 15)
        self.assertEqual(picker.end_hour.get_value_as_int(), 16)
        self.assertEqual(picker.end_minute.get_value_as_int(), 30)
        picker.calendar.set_date(GLib.DateTime.new_local(2026, 11, 5, 0, 0, 0))
        picker._on_day_selected(picker.calendar)
        self.assertEqual(picker.timestamp.date, dt.date(2026, 11, 5))
        self.assertEqual(picker.timestamp.time, dt.time(14, 15))
        self.assertEqual(picker.timestamp.end_time, dt.time(16, 30))
        picker.entry.set_text("not a date")
        self.assertFalse(picker.time_enabled.get_sensitive())
        self.assertFalse(picker.set_button.get_sensitive())
        picker.entry.set_text("2026-11-06")
        self.assertTrue(picker.time_enabled.get_sensitive())
        self.assertFalse(picker.time_enabled.get_active())
