"""An integrated weekly calendar for dated Org entries."""

import datetime as dt
import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

import org  # noqa: E402
from datepicker import DatePopover  # noqa: E402
from keybindings import OrgKeySequences  # noqa: E402


def event_minutes(item):
    start = item.ts.time.hour * 60 + item.ts.time.minute
    end = (item.ts.end_time.hour * 60 + item.ts.end_time.minute
           if item.ts.end_time else start + 30)
    if end <= start:
        end = 1440
    return start, min(end, 1440)


def event_lanes(items):
    """Assign overlapping events equal-width lanes within each overlap group."""
    events = sorted(((*event_minutes(item), i) for i, item in enumerate(items)))
    result = {}
    group = []
    ends = []
    group_end = -1

    def finish():
        for index, lane in group:
            result[index] = (lane, len(ends))

    for start, end, index in events:
        if start >= group_end:
            finish()
            group = []
            ends = []
        lane = next((i for i, value in enumerate(ends) if value <= start), len(ends))
        if lane == len(ends):
            ends.append(end)
        else:
            ends[lane] = end
        group.append((index, lane))
        group_end = max(group_end, end)
    finish()
    return result


class AgendaTab(Adw.Bin):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.app = window.get_application()
        self.page = None
        self.start = org.week_start(dt.date.today())
        self.days = {}
        self.entry_buttons = []
        self.selected_item = None
        self._active_date_popover = None
        self.actions = Gio.SimpleActionGroup()
        self.insert_action_group("win", self.actions)

        self.title_widget = Adw.WindowTitle(title="Agenda")
        self.header = Adw.HeaderBar(title_widget=self.title_widget)
        self.open_button = Gtk.Button(tooltip_text="Open documents")
        open_content = Gtk.Box(spacing=6)
        open_content.append(Gtk.Label(label="Open"))
        open_content.append(Gtk.Image(icon_name="pan-down-symbolic"))
        self.open_button.set_child(open_content)
        self.open_button.connect("clicked", lambda *_: self.app.show_buffers())
        self.header.pack_start(self.open_button)
        self.header.pack_start(Gtk.Button(icon_name="tab-new-symbolic",
                                          tooltip_text="New Tab (Ctrl+T)", action_name="win.new"))
        menu = Gio.Menu()
        menu.append("Preferences", "app.preferences")
        menu.append("About Organizer", "app.about")
        self.header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                            tooltip_text="Main Menu"))

        navigation = Gtk.Box(spacing=6, margin_top=12, margin_bottom=12,
                             margin_start=12, margin_end=12)
        previous = Gtk.Button(icon_name="go-previous-symbolic", tooltip_text="Previous week")
        previous.connect("clicked", lambda *_: self.shift(-1))
        following = Gtk.Button(icon_name="go-next-symbolic", tooltip_text="Next week")
        following.connect("clicked", lambda *_: self.shift(1))
        today = Gtk.Button(label="Today")
        today.connect("clicked", lambda *_: self.goto_today())
        navigation.append(previous)
        navigation.append(today)
        navigation.append(following)
        self.week_label = Gtk.Label(hexpand=True, xalign=1, css_classes=["heading"])
        navigation.append(self.week_label)
        self.view = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                            css_classes=["calendar-week"], focusable=True)
        self.day_headers = Gtk.Grid(column_homogeneous=True, hexpand=True)
        header_row = Gtk.Box()
        header_row.append(Gtk.Label(label="All day", width_request=52,
                                    valign=Gtk.Align.END, margin_bottom=8,
                                    css_classes=["dim-label", "caption"]))
        header_row.append(self.day_headers)
        self.view.append(header_row)
        self.time_grid = Gtk.Grid(column_homogeneous=True, hexpand=True)
        self.hour_labels = Gtk.Fixed(width_request=52, height_request=1440)
        for hour in range(24):
            self.hour_labels.put(
                Gtk.Label(label=f"{hour:02d}:00", css_classes=["dim-label", "caption"]),
                4, hour * 60 + 4)
        time_row = Gtk.Box()
        time_row.append(self.hour_labels)
        time_row.append(self.time_grid)
        self.scroller = Gtk.ScrolledWindow(child=time_row, vexpand=True,
                                           hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.view.append(self.scroller)
        scroller = self.scroller
        self.app.setup_scroll_zoom(scroller)
        css = Gtk.CssProvider()
        css.load_from_string("""
            .calendar-week { background: @view_bg_color; }
            .calendar-day { border-left: 1px solid alpha(@view_fg_color, 0.12); }
            .calendar-event { background: alpha(@accent_bg_color, 0.18);
                border: none; border-left: 3px solid @accent_bg_color;
                border-radius: 4px; padding: 3px 5px; min-height: 0; min-width: 0; }
            .calendar-event.deadline { background: alpha(@error_color, 0.15);
                border-left-color: @error_color; }
            .calendar-event:hover { background: alpha(@accent_bg_color, 0.28); }
        """)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.status = Gtk.Label(xalign=0, wrap=True, margin_start=12, margin_end=12,
                                margin_bottom=8, css_classes=["dim-label"])
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(navigation)
        content.append(self.view)
        content.append(self.status)
        self.set_child(content)

        for name, callback in (
                ("new", lambda: self.window.new_tab().present()),
                ("close", self.close), ("reopen", self.window.reopen_tab),
                ("open", self._find_file)):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, cb=callback: cb())
            self.actions.add_action(action)
        self._org_key_sequences = OrgKeySequences(self, {
            "C-c C-t": self.cycle_todo,
            "C-c C-s": lambda: self.edit_planning("SCHEDULED"),
            "C-c C-d": lambda: self.edit_planning("DEADLINE"),
            "C-c a": self.app.show_agenda,
            "C-x C-c": lambda: self.app.activate_action("quit", None),
            "C-x C-f": self._find_file,
            "C-x b": self.app.show_buffers,
            "C-g": self._keyboard_quit,
        })
        self.remove_controller(self._org_key_sequences.controller)
        self.refresh()
        GLib.idle_add(self._scroll_morning)

    def _scroll_morning(self):
        self.scroller.get_vadjustment().set_value(8 * 60)
        return GLib.SOURCE_REMOVE

    def get_application(self):
        return self.app

    def present(self):
        self.window.tabs.set_selected_page(self.page)
        self.window.present()
        self.view.grab_focus()

    def close(self):
        self.window.tabs.close_page(self.page)

    def request_close(self, finish):
        finish(True)

    def _update_title(self):
        if self.page:
            self.page.set_title("Agenda")
        self.window.set_title("Agenda – Organizer")

    def refresh(self, keep_scroll=True):
        if self._active_date_popover is not None:
            return
        directory, files, entries = self.app.agenda_entries()
        today = dt.date.today()
        self.days = org.build_agenda(entries, self.start, 7, today)
        end = self.start + dt.timedelta(days=6)
        self.week_label.set_label(
            f"{org.format_date(self.start, with_year=True)} – "
            f"{org.format_date(end, with_year=True)}")
        if files:
            self.status.set_label("")
        else:
            reason = "does not exist" if not os.path.isdir(directory) else "has no .org files"
            self.status.set_label(
                f"The agenda folder {directory} {reason}. Choose a folder in Preferences.")
        focus = self.window.get_focus()
        restore_focus = focus is not None and focus.is_ancestor(self.view)
        selected = self.selected_item
        selected_button = None
        for grid in (self.day_headers, self.time_grid):
            child = grid.get_first_child()
            while child:
                grid.remove(child)
                child = grid.get_first_child()
        self.entry_buttons = []
        self.selected_item = None
        for column_index, (date, items) in enumerate(self.days.items()):
            heading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                              hexpand=True, width_request=90,
                              margin_top=8, margin_bottom=8, css_classes=["calendar-day"])
            heading.append(Gtk.Label(label=org.DAY_ABBR[date.weekday()], xalign=0,
                                      margin_start=8, css_classes=["caption", "dim-label"]))
            heading.append(Gtk.Label(label=str(date.day), xalign=0, margin_start=8,
                                      css_classes=["heading", "accent"] if date == today else ["heading"]))
            self.day_headers.attach(heading, column_index, 0, 1, 1)
            all_day = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                              hexpand=True, width_request=90, css_classes=["calendar-day"])
            all_day_scroll = Gtk.ScrolledWindow(
                child=all_day, min_content_height=26, max_content_height=96,
                propagate_natural_height=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
            self.day_headers.attach(all_day_scroll, column_index, 1, 1, 1)
            canvas = Gtk.DrawingArea(height_request=1440, hexpand=True)
            canvas.set_draw_func(lambda _area, cr, width, height, day=date:
                                 self._draw_hours(cr, width, height, day))
            fixed = Gtk.Fixed()
            overlay = Gtk.Overlay(child=canvas, hexpand=True, width_request=90,
                                   css_classes=["calendar-day"])
            overlay.add_overlay(fixed)
            self.time_grid.attach(overlay, column_index, 0, 1, 1)
            timed = [item for item in items if item.ts.time
                     and item.kind not in ("deadline-upcoming", "deadline-overdue", "scheduled-overdue")]
            lanes = event_lanes(timed)
            blocks = []
            for item in items:
                button = self._entry_button(item)
                if item in timed:
                    index = timed.index(item)
                    start, end = event_minutes(item)
                    lane, count = lanes[index]
                    fixed.put(button, 0, start)
                    button.set_size_request(1, max(20, end - start - 1))
                    blocks.append((button, start, lane, count))
                else:
                    all_day.append(button)
                self.entry_buttons.append((item, button))
                if item == selected:
                    self.selected_item = item
                    selected_button = button
            fixed.add_tick_callback(self._layout_events, blocks)
        if restore_focus:
            (selected_button or self.view).grab_focus()

    def _entry_button(self, item):
        entry = item.entry
        button = Gtk.Button(tooltip_text=f"{entry.display_title}\n{entry.path}",
                            css_classes=["calendar-event", "deadline"] if "deadline" in item.kind
                            else ["calendar-event"])
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1,
                          valign=Gtk.Align.START)
        detail = item.label or "Event"
        if item.ts.time and item.kind != "deadline-upcoming":
            time = item.ts.time.strftime("%H:%M")
            if item.ts.end_time:
                time += "–" + item.ts.end_time.strftime("%H:%M")
            detail = time
        content.append(Gtk.Label(label=detail, xalign=0, ellipsize=Pango.EllipsizeMode.END,
                                 css_classes=["caption", "dim-label"]))
        title = f"{entry.keyword} {entry.display_title}" if entry.keyword else entry.display_title
        content.append(Gtk.Label(label=title, xalign=0, ellipsize=Pango.EllipsizeMode.END,
                                 css_classes=["dim-label"] if entry.is_done else []))
        button.set_child(content)
        button.connect("notify::has-focus",
                       lambda widget, *_: self._select(item) if widget.has_focus() else None)
        button.connect("clicked", lambda *_: self.app.open_location(entry.path, entry.line))
        return button

    def _draw_hours(self, cr, width, height, date):
        dark = Adw.StyleManager.get_default().get_dark()
        cr.set_source_rgba(1 if dark else 0, 1 if dark else 0, 1 if dark else 0, 0.09)
        cr.set_line_width(1)
        for minute in range(0, 1440, 30):
            cr.move_to(0, minute + 0.5)
            cr.line_to(width, minute + 0.5)
        cr.stroke()
        now = dt.datetime.now()
        if date == now.date():
            y = now.hour * 60 + now.minute
            cr.set_source_rgb(0.88, 0.18, 0.18)
            cr.set_line_width(2)
            cr.move_to(0, y)
            cr.line_to(width, y)
            cr.stroke()

    def _layout_events(self, fixed, _clock, blocks):
        width = fixed.get_width()
        for button, start, lane, count in blocks:
            block_width = max(1, int(width / count) - 2)
            requested_width, requested_height = button.get_size_request()
            if requested_width != block_width:
                button.set_size_request(block_width, requested_height)
                fixed.move(button, lane * width / count + 1, start)
        return GLib.SOURCE_CONTINUE

    def _select(self, item):
        self.selected_item = item

    def _on_key_pressed(self, _controller, keyval, _keycode, state):
        if state & Gtk.accelerator_get_default_mod_mask():
            return False
        commands = {
            "f": lambda: self.shift(1), "b": lambda: self.shift(-1),
            "period": self.goto_today, "r": self.refresh, "g": self.refresh,
            "t": self.cycle_todo, "q": self.close,
            "n": lambda: self._move(1), "p": lambda: self._move(-1),
            "Return": self.open_entry,
        }
        command = commands.get(Gdk.keyval_name(keyval))
        if command is None:
            return False
        command()
        return True

    def _move(self, delta):
        if not self.entry_buttons:
            return
        index = next((i for i, (item, _) in enumerate(self.entry_buttons)
                      if item == self.selected_item), -1 if delta > 0 else len(self.entry_buttons))
        item, button = self.entry_buttons[max(0, min(index + delta, len(self.entry_buttons) - 1))]
        self.selected_item = item
        button.grab_focus()

    def shift(self, direction):
        self.start += dt.timedelta(days=7 * direction)
        self.refresh()

    def goto_today(self):
        self.start = org.week_start(dt.date.today())
        self.refresh()

    def _find_file(self):
        tab = next(iter(self.app.editor_tabs()), None) or self.app.new_tab()
        tab.present()
        tab.open_dialog()

    def open_dialog(self):
        self._find_file()

    def open_entry(self):
        if self.selected_item:
            entry = self.selected_item.entry
            self.app.open_location(entry.path, entry.line)

    def cycle_todo(self):
        if self.selected_item is None:
            self.status.set_label("Select an entry first.")
            return
        entry = self.selected_item.entry
        message = self.app.edit_entry(
            entry, lambda lines, i: org.cycle_todo(lines, i, dt.date.today()))
        self.refresh()
        if message:
            self.status.set_label(message)

    def edit_planning(self, kind):
        if self.selected_item is None:
            self.status.set_label("Select an entry first.")
            return
        entry = self.selected_item.entry
        button = next(button for item, button in self.entry_buttons if item == self.selected_item)
        initial = entry.scheduled if kind == "SCHEDULED" else entry.deadline
        popover = DatePopover("Schedule" if kind == "SCHEDULED" else "Deadline", initial)
        self._active_date_popover = popover
        popover.set_parent(button)

        def on_apply(_popover, timestamp):
            message = self.app.edit_entry(
                entry, lambda lines, i: (org.set_planning(lines, i, kind, timestamp), None))
            if message:
                self.status.set_label(message)

        def on_closed(p):
            self._active_date_popover = None

            def finish():
                p.unparent()
                self.refresh()
                self.view.grab_focus()
                return GLib.SOURCE_REMOVE
            GLib.idle_add(finish)

        popover.connect("apply", on_apply)
        popover.connect("closed", on_closed)
        popover.popup()

    def _keyboard_quit(self):
        if self._active_date_popover:
            self._active_date_popover.popdown()
        else:
            self.view.grab_focus()
