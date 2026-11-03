"""Calendar integration tests."""

from unittest.mock import Mock, patch

from editor_test_case import EditorTestCase
from agenda import event_is_past, event_lanes, event_minutes
from vim import Gdk, Gio, GLib, Gtk, Pango


class CalendarTests(EditorTestCase):
    def test_calendar_menu_toggles_saved_weekends_preference_in_all_windows(self):
        import datetime as dt
        import org

        self.addCleanup(self.app.set_hide_weekends, True)
        first = self.app.new_tab()
        second = self.app.new_window().new_tab()
        self.app.show_agenda(first.window)
        self.app.show_agenda(second.window)
        calendars = [first.window.agenda, second.window.agenda]

        def find_menu(widget):
            if isinstance(widget, Gtk.MenuButton):
                return widget
            child = widget.get_first_child()
            while child:
                found = find_menu(child)
                if found is not None:
                    return found
                child = child.get_next_sibling()
            return None

        menu_button = find_menu(calendars[0].header)
        menu = menu_button.get_menu_model().get_item_link(0, Gio.MENU_LINK_SECTION)
        self.assertEqual(menu.get_item_attribute_value(
            0, Gio.MENU_ATTRIBUTE_LABEL, None).get_string(), "Hide Weekends")
        self.assertEqual(menu.get_item_attribute_value(
            0, Gio.MENU_ATTRIBUTE_ACTION, None).get_string(), "app.hide-weekends")
        action = self.app.lookup_action("hide-weekends")
        self.assertTrue(action.get_state().get_boolean())
        saturday = calendars[0].start + dt.timedelta(days=5)
        entries = org.parse_entries(
            f"* Weekend event :@Park:\n<{saturday.isoformat()} 10:00>\n", "/org/events.org")
        with patch.object(self.app, "agenda_entries",
                          return_value=("/org", ["/org/events.org"], entries)):
            settings = Mock()
            with patch.object(self.app, "settings", settings):
                action.activate(None)
                settings.set_boolean.assert_called_once_with("hide-weekends", False)
            self.assertFalse(self.app.hide_weekends)
            self.assertFalse(action.get_state().get_boolean())
            for calendar in calendars:
                self.assertIsNotNone(calendar.time_grid.get_child_at(6, 0))
                self.assertEqual(len(calendar.entry_buttons), 1)
            self.app.set_hide_weekends(True)
            self.assertTrue(action.get_state().get_boolean())
            for calendar in calendars:
                self.assertIsNone(calendar.time_grid.get_child_at(5, 0))
            action.change_state(GLib.Variant.new_boolean(False))
            self.assertFalse(action.get_state().get_boolean())
            self.assertFalse(self.app.hide_weekends)

    def test_event_is_past_uses_occurrence_end_and_keeps_ongoing_events_visible(self):
        import datetime as dt
        import org

        day = dt.date(2026, 11, 3)
        now = dt.datetime(2026, 11, 3, 12)
        entries = org.parse_entries(
            "* Repeating\n<2026-10-27 11:00-12:00 +1w>\n"
            "* Ongoing\n<2026-11-03 11:30-12:30>\n"
            "* Default duration\n<2026-11-03 11:30>\n"
            "* Overnight\n<2026-11-03 23:00-01:00>\n"
            "* All day\n<2026-11-03>\n"
            "* Yesterday\n<2026-11-02>\n"
            "* Tomorrow\n<2026-11-04 09:00>\n")
        days = org.build_agenda(entries, day - dt.timedelta(days=1), 3, day)
        items = {item.entry.title: item for items in days.values() for item in items}
        self.assertTrue(event_is_past(items["Repeating"], now))
        self.assertFalse(event_is_past(items["Repeating"], now - dt.timedelta(seconds=1)))
        self.assertFalse(event_is_past(items["Ongoing"], now))
        self.assertTrue(event_is_past(items["Ongoing"], now.replace(minute=30)))
        self.assertFalse(event_is_past(items["Default duration"], now))
        self.assertTrue(event_is_past(items["Default duration"], now.replace(minute=30)))
        self.assertFalse(event_is_past(items["Overnight"], now.replace(hour=23, minute=59)))
        self.assertFalse(event_is_past(items["All day"], now))
        self.assertTrue(event_is_past(items["Yesterday"], now))
        self.assertFalse(event_is_past(items["Tomorrow"], now))

    def test_calendar_displays_location_tags_without_changing_org_tags(self):
        import org

        self.app.show_agenda()
        calendar = self.app.agenda
        date = calendar.start.isoformat()
        entries = org.parse_entries(
            f"* Lecture :work:@U328:@Lab2:\n<{date} 10:00>\n"
            f"* All day :@U407:\n<{date}>\n"
            f"* No location :work:@:\n<{date} 11:00>\n", "/org/events.org")
        with patch.object(self.app, "agenda_entries",
                          return_value=("/org", ["/org/events.org"], entries)):
            calendar.refresh()
        buttons = {item.entry.title: button for item, button in calendar.entry_buttons}
        lecture = buttons["Lecture"]
        self.assertTrue(all(button.get_cursor().get_name() == "pointer"
                            for button in buttons.values()))
        self.assertEqual(lecture.get_child().get_last_child().get_label(), "@U328, @Lab2")
        self.assertEqual(lecture.get_tooltip_text(), "Lecture\n@U328, @Lab2\n/org/events.org")
        self.assertEqual(buttons["All day"].get_child().get_last_child().get_label(), "@U407")
        self.assertEqual(buttons["No location"].get_child().get_last_child().get_label(),
                         "No location")
        self.assertEqual(entries[0].tags, ["work", "@U328", "@Lab2"])

    def test_calendar_event_labels_use_compact_regular_type_and_muted_location(self):
        import org

        self.app.show_agenda()
        calendar = self.app.agenda
        date = calendar.start.isoformat()
        entries = org.parse_entries(
            f"* Lecture :@U328:\n<{date}>\n", "/org/events.org")
        with patch.object(self.app, "agenda_entries",
                          return_value=("/org", ["/org/events.org"], entries)):
            calendar.refresh()
        calendar.window.present()
        button = calendar.entry_buttons[0][1]
        self.wait_for(button.get_mapped)
        content = button.get_child()
        self.assertEqual(content.get_orientation(), Gtk.Orientation.VERTICAL)
        time = content.get_first_child()
        title = time.get_next_sibling()
        location = content.get_last_child()
        self.assertIs(title.get_next_sibling(), location)
        self.assertEqual(location.get_css_classes(), time.get_css_classes())
        self.assertGreater(location.get_margin_top(), 0)
        sizes = []
        label = time
        while label:
            layout = label.get_layout()
            run = layout.get_iter().get_run_readonly()
            self.assertIsNotNone(run)
            font = run.item.analysis.font.describe()
            self.assertEqual(font.get_weight(), Pango.Weight.NORMAL)
            sizes.append(font.get_size())
            label = label.get_next_sibling()
        self.assertEqual(sizes, [sizes[0]] * 3)
        self.assertLess(sizes[0], content.get_pango_context().get_font_description().get_size())

    def test_calendar_past_style_keeps_background_opaque_with_custom_file_color(self):
        import datetime as dt
        import org

        self.app.show_agenda()
        calendar = self.app.agenda
        calendar.start -= dt.timedelta(days=7)
        self.app.file_colors = {Gio.File.new_for_path("/org/events.org").get_uri(): "#9141ac"}
        entries = org.parse_entries(
            f"* Lecture :@U328:\n<{calendar.start.isoformat()}>\n", "/org/events.org")
        with patch.object(self.app, "agenda_entries",
                          return_value=("/org", ["/org/events.org"], entries)):
            calendar.refresh()
        calendar.window.present()
        button = calendar.entry_buttons[0][1]
        self.wait_for(button.get_mapped)
        self.assertTrue(button.has_css_class("past"))
        self.assertTrue(button.has_css_class("custom-color-9141ac"))
        paintable = Gtk.WidgetPaintable.new(button)
        node = None

        def rendered_event():
            nonlocal node
            snapshot = Gtk.Snapshot.new()
            paintable.snapshot(snapshot, button.get_width(), button.get_height())
            node = snapshot.to_node()
            return node is not None

        self.wait_for(rendered_event)
        rendered = bytes(node.serialize().get_data()).decode()
        self.assertNotRegex(rendered, r"opacity \{\s+opacity: 0\.55;\s+child: container")
        self.assertRegex(
            rendered, r"color \{\s+bounds: [^;]+;\s+color: (?:rgb\([^)]*\)|color\(srgb [^/)]*\));")
        self.assertRegex(rendered, r"opacity \{\s+opacity: 0\.55;\s+child: text")

    def test_calendar_clock_updates_past_style_without_rebuilding_events(self):
        import datetime as dt
        import org

        self.app.show_agenda()
        calendar = self.app.agenda
        day = calendar.start
        entries = org.parse_entries(
            f"* Event :@U328:\n<{day.isoformat()} 11:00-12:00>\n", "/org/events.org")

        class Clock(dt.datetime):
            current = dt.datetime.combine(day, dt.time(11, 59))

            @classmethod
            def now(cls):
                return cls.current

        with patch.object(self.app, "agenda_entries",
                          return_value=("/org", ["/org/events.org"], entries)), patch(
                              "agenda.dt.datetime", Clock):
            calendar.refresh()
            item, button = calendar.entry_buttons[0]
            self.assertFalse(button.has_css_class("past"))
            Clock.current += dt.timedelta(minutes=1)
            with patch.object(calendar, "refresh") as refresh:
                calendar._refresh_clock()
                refresh.assert_not_called()
            self.assertTrue(button.has_css_class("past"))
            self.assertIs(calendar.entry_buttons[0][1], button)
            self.assertIs(calendar.entry_buttons[0][0], item)
            calendar.refresh()
            self.assertTrue(calendar.entry_buttons[0][1].has_css_class("past"))

    def test_period_in_current_calendar_week_does_not_refresh_or_change_selection(self):
        import datetime as dt
        import org

        tab = self.app.new_tab()
        self.app.show_agenda(tab.window)
        calendar = tab.window.agenda
        calendar.start = org.week_start(dt.date.today())
        with patch.object(calendar, "refresh") as refresh, patch.object(
                tab.window.utility_pane, "select_week") as select_week:
            self.assertTrue(calendar._on_key_pressed(
                None, Gdk.KEY_period, 0, Gdk.ModifierType(0)))
            refresh.assert_not_called()
            select_week.assert_not_called()
            calendar.start -= dt.timedelta(days=7)
            calendar._on_key_pressed(None, Gdk.KEY_period, 0, Gdk.ModifierType(0))
            self.assertEqual(calendar.start, org.week_start(dt.date.today()))
            refresh.assert_called_once_with()
            select_week.assert_called_once_with(calendar.start)

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
