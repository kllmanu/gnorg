"""The agenda: a read-only text buffer laid out like Emacs' org-agenda (`C-c a n`)."""

import datetime as dt
import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GtkSource", "5")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gtk, GtkSource, Pango  # noqa: E402

import org  # noqa: E402
from datepicker import DatePopover  # noqa: E402
from keybindings import OrgKeySequences  # noqa: E402

STYLES = ("agenda-structure", "agenda-date", "agenda-date-weekend", "agenda-date-today",
          "agenda-separator", "agenda-done", "scheduled", "scheduled-previously", "deadline",
          "upcoming-deadline", "todo", "done", "priority", "tag")

HINTS = "⏎ open  t todo  f/b later/earlier  . today  d/w day/week  r redo  q quit"


class AgendaWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Agenda", default_width=780, default_height=720)
        self.app = app
        self.span = 7
        self.start = org.week_start(dt.date.today())
        self.lines = []
        self.tags = {}
        self._message_id = 0
        self._active_date_popover = None

        self.buffer = GtkSource.Buffer(highlight_syntax=False, highlight_matching_brackets=False)
        self.view = GtkSource.View(
            buffer=self.buffer, editable=False, cursor_visible=True, monospace=True,
            highlight_current_line=True, css_classes=["vimtext-editor"],
            top_margin=8, bottom_margin=8, left_margin=8, right_margin=8)
        scroller = Gtk.ScrolledWindow(child=self.view, vexpand=True)
        self.view_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.view_box.append(scroller)

        self.title_widget = Adw.WindowTitle(title="Agenda")
        header = Adw.HeaderBar(title_widget=self.title_widget)
        self.status = Gtk.Label(label=HINTS, xalign=0, ellipsize=Pango.EllipsizeMode.END,
                                css_classes=["monospace", "dim-label"])
        status_box = Gtk.Box(margin_start=12, margin_end=12, margin_top=4, margin_bottom=4)
        status_box.append(self.status)

        toolbar_view = Adw.ToolbarView(content=self.view_box)
        toolbar_view.add_top_bar(header)
        toolbar_view.add_bottom_bar(status_box)
        self.set_content(toolbar_view)

        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key_pressed)
        self.view.add_controller(keys)
        self._org_key_sequences = OrgKeySequences(self, {
            "C-c C-t": self.cycle_todo,
            "C-c C-s": lambda: self.edit_planning("SCHEDULED"),
            "C-c C-d": lambda: self.edit_planning("DEADLINE"),
            "C-c a": self.app.show_agenda,
            "C-x C-c": lambda: self.app.activate_action("quit", None),
            "C-x C-f": self._find_file,
            "C-g": self._keyboard_quit,
        })
        clicks = Gtk.GestureClick()
        clicks.connect("pressed", lambda _g, n, _x, _y: n == 2 and self.open_entry())
        self.view.add_controller(clicks)

        for name, callback in (("todo", self.cycle_todo),
                               ("schedule", lambda: self.edit_planning("SCHEDULED")),
                               ("deadline", lambda: self.edit_planning("DEADLINE"))):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, cb=callback: cb())
            self.add_action(action)

        style_manager = Adw.StyleManager.get_default()
        handler = style_manager.connect("notify::dark", lambda *_: self._update_scheme())
        self.connect("destroy", lambda *_: style_manager.disconnect(handler))
        self.connect("notify::is-active", lambda *_: self.is_active() and self.refresh())
        self._update_scheme(render=False)
        self.refresh(keep_scroll=False)

    # ---------- Rendering ----------

    def _update_scheme(self, render=True):
        dark = Adw.StyleManager.get_default().get_dark()
        schemes = GtkSource.StyleSchemeManager.get_default()
        scheme = schemes.get_scheme("vimtext-dark" if dark else "vimtext") or \
            schemes.get_scheme("Adwaita-dark" if dark else "Adwaita")
        self.buffer.set_style_scheme(scheme)
        table = self.buffer.get_tag_table()
        for tag in self.tags.values():
            table.remove(tag)
        self.tags = {}
        for name in STYLES:
            tag = self.buffer.create_tag(None)
            style = scheme.get_style("org:" + name) if scheme else None
            if style:
                style.apply(tag)
            self.tags[name] = tag
        if render:
            self.lines = []  # the tags were recreated, so re-render even if nothing changed
            self.refresh()

    def refresh(self, keep_scroll=True):
        directory, files, entries = self.app.agenda_entries()
        today = dt.date.today()
        self.title_widget.set_subtitle(self._short(directory))
        if files:
            lines = org.format_agenda(entries, self.start, self.span, today)
        else:
            reason = "doesn’t exist" if not os.path.isdir(directory) else "has no .org files"
            lines = [org.AgendaLine(f"The agenda folder {self._short(directory)} {reason}."),
                     org.AgendaLine(""),
                     org.AgendaLine("Choose another one in Preferences (Ctrl+,).")]
        if lines == self.lines and keep_scroll:
            return
        self.lines = lines

        cursor = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        line, column = cursor.get_line(), cursor.get_line_offset()
        adjustment = self.view.get_vadjustment()
        scroll = adjustment.get_value() if adjustment else 0

        self.buffer.set_text("\n".join(l.text for l in self.lines))
        for i, agenda_line in enumerate(self.lines):
            for start, end, style in agenda_line.spans:
                if style in self.tags:
                    a = self.buffer.get_iter_at_line_offset(i, start)[1]
                    b = self.buffer.get_iter_at_line_offset(i, end)[1]
                    self.buffer.apply_tag(self.tags[style], a, b)

        if keep_scroll:
            self._place_cursor(min(line, len(self.lines) - 1), column)
            if adjustment:
                GLib.idle_add(lambda: adjustment.set_value(scroll) and False)
        else:
            target = next((i for i, l in enumerate(self.lines) if l.kind == "date" and l.date == today), 0)
            self._place_cursor(target, 0)
            GLib.idle_add(self._scroll_to_cursor)

    def _place_cursor(self, line, column=0):
        ok, it = self.buffer.get_iter_at_line(max(line, 0))
        if ok:
            end = it.copy()
            if not end.ends_line():
                end.forward_to_line_end()
            it.set_line_offset(min(column, end.get_line_offset()))
            self.buffer.place_cursor(it)

    def _scroll_to_cursor(self):
        self.view.scroll_to_mark(self.buffer.get_insert(), 0.1, False, 0, 0)
        return False

    def message(self, text):
        """Show a message in the status line for a while, like Emacs' echo area."""
        self.status.set_label(text)
        if self._message_id:
            GLib.source_remove(self._message_id)

        def reset():
            self._message_id = 0
            self.status.set_label(HINTS)
            return False

        self._message_id = GLib.timeout_add_seconds(4, reset)

    # ---------- Commands ----------

    def _find_file(self):
        window = next(iter(self.app.editor_windows()), None) or self.app.new_window()
        window.present()
        window.open_dialog()

    def _on_key_pressed(self, _controller, keyval, _keycode, state):
        if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK):
            return False
        name = Gdk.keyval_name(keyval)
        commands = {
            "n": lambda: self._move(1), "j": lambda: self._move(1),
            "p": lambda: self._move(-1), "k": lambda: self._move(-1),
            "Return": self.open_entry, "KP_Enter": self.open_entry, "Tab": self.open_entry,
            "o": self.open_entry,
            "t": self.cycle_todo,
            "f": lambda: self.shift(1), "b": lambda: self.shift(-1),
            "period": self.goto_today,
            "d": lambda: self.set_span(1), "w": lambda: self.set_span(7),
            "r": self.refresh, "g": self.refresh,
            "q": self.close, "x": self.close, "Escape": self.close,
        }
        command = commands.get(name)
        if command is None:
            return False
        command()
        return True

    def _move(self, delta):
        line = self.buffer.get_iter_at_mark(self.buffer.get_insert()).get_line()
        self._place_cursor(max(0, min(len(self.lines) - 1, line + delta)), 0)
        self._scroll_to_cursor()

    def shift(self, direction):
        self.start += dt.timedelta(days=self.span * direction)
        self.refresh(keep_scroll=False)

    def goto_today(self):
        today = dt.date.today()
        self.start = org.week_start(today) if self.span == 7 else today
        self.refresh(keep_scroll=False)

    def set_span(self, days):
        self.span = days
        self.goto_today()

    def current_entry(self):
        line = self.buffer.get_iter_at_mark(self.buffer.get_insert()).get_line()
        entry = self.lines[line].entry if 0 <= line < len(self.lines) else None
        if entry is None:
            self.message("Command not allowed in this line")
        return entry

    def open_entry(self):
        if entry := self.current_entry():
            self.app.open_location(entry.path, entry.line)

    def cycle_todo(self):
        entry = self.current_entry()
        if entry is None:
            return
        cycle = [None, *org.ALL_KEYWORDS]
        keyword = cycle[(cycle.index(entry.keyword) + 1) % len(cycle)]
        message = self.app.edit_entry(
            entry, lambda lines, i: org.set_todo_keyword(lines, i, keyword, dt.date.today()))
        self.refresh()
        self.message(message or f"{entry.display_title}: {keyword or 'no keyword'}")

    def edit_planning(self, kind):
        entry = self.current_entry()
        if entry is None:
            return
        initial = entry.scheduled if kind == "SCHEDULED" else entry.deadline
        today = dt.date.today()
        popover = DatePopover("Schedule" if kind == "SCHEDULED" else "Deadline", initial, today)
        self._active_date_popover = popover
        popover.set_parent(self.view_box)
        popover.set_pointing_to(self._cursor_rect())

        def on_apply(_popover, ts):
            message = self.app.edit_entry(entry, lambda lines, i: (org.set_planning(lines, i, kind, ts), None))
            self.refresh()
            what = "Scheduled" if kind == "SCHEDULED" else "Deadline"
            self.message(message or (f"{what}: {ts.describe(today)}" if ts else f"{what} removed"))

        def on_closed(p):
            if self._active_date_popover is p:
                self._active_date_popover = None
            self.view.grab_focus()
            GLib.idle_add(lambda: p.unparent() and False)

        popover.connect("apply", on_apply)
        popover.connect("closed", on_closed)
        popover.popup()

    def _keyboard_quit(self):
        if self._active_date_popover is not None:
            self._active_date_popover.popdown()

    def _cursor_rect(self):
        location = self.view.get_iter_location(self.buffer.get_iter_at_mark(self.buffer.get_insert()))
        x, y = self.view.buffer_to_window_coords(Gtk.TextWindowType.WIDGET, location.x, location.y)
        _ok, point = self.view.compute_point(self.view_box, Graphene.Point().init(x, y))
        rect = Gdk.Rectangle()
        rect.x, rect.y = int(point.x), int(point.y)
        rect.width, rect.height = max(location.width, 1), location.height
        return rect

    @staticmethod
    def _short(path):
        home = GLib.get_home_dir()
        if path and (path == home or path.startswith(home + os.sep)):
            return "~" + path[len(home):]
        return path or ""
