"""Calendar integration tests."""

from unittest.mock import patch

from editor_test_case import EditorTestCase
from agenda import event_lanes, event_minutes


class CalendarTests(EditorTestCase):
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
