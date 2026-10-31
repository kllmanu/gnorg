"""The agenda window: a week view of scheduled items/deadlines/events and a global TODO list."""

import datetime as dt
import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

import org  # noqa: E402

KIND_CLASSES = {
    "deadline": "error",
    "deadline-overdue": "error",
    "deadline-upcoming": "warning",
    "scheduled-overdue": "warning",
    "scheduled": "accent",
}


class AgendaWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Agenda", default_width=720, default_height=780)
        self.app = app
        self.week = org.week_start(dt.date.today())

        self.stack = Adw.ViewStack()
        self.week_box, week_page = self._scrolled_page()
        self.todo_box, todo_page = self._scrolled_page()
        self.stack.add_titled_with_icon(week_page, "week", "Week", "x-office-calendar-symbolic")
        self.stack.add_titled_with_icon(todo_page, "todo", "To Do", "object-select-symbolic")
        self.stack.connect("notify::visible-child-name", lambda *_: self._update_nav())

        header = Adw.HeaderBar(title_widget=Adw.ViewSwitcher(
            stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE))
        self.nav = Gtk.Box(spacing=6)
        self.nav.append(Gtk.Button(icon_name="go-previous-symbolic", tooltip_text="Previous Week (B)",
                                   action_name="agenda.prev"))
        self.nav.append(Gtk.Button(label="Today", tooltip_text="This Week (.)", action_name="agenda.today"))
        self.nav.append(Gtk.Button(icon_name="go-next-symbolic", tooltip_text="Next Week (F)",
                                   action_name="agenda.next"))
        header.pack_start(self.nav)

        self.status = Adw.StatusPage(icon_name="folder-documents-symbolic", title="No Org Files")
        choose = Gtk.Button(label="Choose Folder…", halign=Gtk.Align.CENTER,
                            css_classes=["pill", "suggested-action"], action_name="app.preferences")
        self.status.set_child(choose)

        self.content = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.content.add_named(self.stack, "agenda")
        self.content.add_named(self.status, "empty")

        toolbar_view = Adw.ToolbarView(content=self.content)
        toolbar_view.add_top_bar(header)
        self.toasts = Adw.ToastOverlay(child=toolbar_view)
        self.set_content(self.toasts)

        self._setup_actions()
        self.connect("notify::is-active", lambda *_: self.is_active() and self.refresh())
        self.refresh()

    def _scrolled_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                      margin_top=18, margin_bottom=24, margin_start=12, margin_end=12)
        clamp = Adw.Clamp(child=box, maximum_size=760)
        return box, Gtk.ScrolledWindow(child=clamp, hscrollbar_policy=Gtk.PolicyType.NEVER)

    def _setup_actions(self):
        group = Gio.SimpleActionGroup()
        for name, callback in (
            ("prev", lambda: self._move_week(-7)),
            ("next", lambda: self._move_week(7)),
            ("today", lambda: self._move_week(None)),
            ("refresh", self.refresh),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, cb=callback: cb())
            group.add_action(action)
        self.insert_action_group("agenda", group)

        shortcuts = Gtk.ShortcutController(scope=Gtk.ShortcutScope.LOCAL)
        for trigger, action in (
            ("<Alt>Left|b", "agenda.prev"),
            ("<Alt>Right|f", "agenda.next"),
            ("period|t|<Alt>Home", "agenda.today"),
            ("r|F5", "agenda.refresh"),
            ("Escape", "window.close"),
        ):
            shortcuts.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string(trigger),
                                                    Gtk.NamedAction.new(action)))
        self.add_controller(shortcuts)

    def _move_week(self, days):
        self.week = org.week_start(dt.date.today()) if days is None else self.week + dt.timedelta(days=days)
        self.stack.set_visible_child_name("week")
        self.refresh(keep_scroll=False)

    def _update_nav(self):
        self.nav.set_visible(self.stack.get_visible_child_name() == "week")

    # ---------- Rendering ----------

    def refresh(self, keep_scroll=True):
        directory, files, entries = self.app.agenda_entries()
        if not files:
            if directory and os.path.isdir(directory):
                self.status.set_description(f"There are no .org files in {self._short(directory)} yet.")
            else:
                self.status.set_description(f"The agenda folder {self._short(directory)} doesn’t exist.")
            self.content.set_visible_child_name("empty")
            self.nav.set_visible(False)
            return
        self.content.set_visible_child_name("agenda")
        self._update_nav()
        self.directory = directory

        scrolls = [self._scroll_of(self.week_box), self._scroll_of(self.todo_box)]
        positions = [s.get_value() for s in scrolls]
        today = dt.date.today()
        self._render_week(entries, today)
        self._render_todos(entries, today)
        if keep_scroll:
            def restore():
                for s, v in zip(scrolls, positions):
                    s.set_value(v)
                return False
            GLib.idle_add(restore)
        else:
            scrolls[0].set_value(0)

    @staticmethod
    def _scroll_of(box):
        return box.get_ancestor(Gtk.ScrolledWindow).get_vadjustment()

    @staticmethod
    def _clear(box):
        while child := box.get_first_child():
            box.remove(child)

    def _render_week(self, entries, today):
        self._clear(self.week_box)
        start, end = self.week, self.week + dt.timedelta(days=6)
        agenda = org.build_agenda(entries, start, 7, today)

        title = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        if start.month == end.month:
            span = f"{start.day} – {end.day} {org.MONTH_NAMES[end.month - 1]} {end.year}"
        elif start.year == end.year:
            span = f"{start.day} {org.MONTH_ABBR[start.month - 1]} – {end.day} {org.MONTH_ABBR[end.month - 1]} {end.year}"
        else:
            span = f"{org.format_date(start, with_year=True)} – {org.format_date(end, with_year=True)}"
        title.append(Gtk.Label(label=span, xalign=0, css_classes=["title-2"]))
        title.append(Gtk.Label(label=f"Week {start.isocalendar().week}", xalign=0, css_classes=["dim-label"]))
        self.week_box.append(title)

        for date, items in agenda.items():
            section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            header = Gtk.Box(spacing=8)
            header.append(Gtk.Label(label=org.DAY_NAMES[date.weekday()], xalign=0,
                                    css_classes=["heading", "accent"] if date == today else ["heading"]))
            header.append(Gtk.Label(label=f"{date.day} {org.MONTH_NAMES[date.month - 1]}",
                                    xalign=0, css_classes=["dim-label"]))
            if date == today:
                header.append(Gtk.Label(label="Today", css_classes=["caption-heading", "accent"]))
            section.append(header)

            if items:
                listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
                for item in items:
                    listbox.append(self._agenda_row(item, today))
                section.append(listbox)
            else:
                section.append(Gtk.Label(label="Nothing scheduled", xalign=0,
                                         css_classes=["dim-label", "caption"], margin_start=2))
            self.week_box.append(section)

    def _render_todos(self, entries, today):
        self._clear(self.todo_box)
        todos = org.todo_list(entries)
        if not todos:
            self.todo_box.append(Adw.StatusPage(icon_name="object-select-symbolic", title="Nothing To Do",
                                                description="Open tasks marked TODO show up here",
                                                vexpand=True))
            return
        by_file = {}
        for e in todos:
            by_file.setdefault(e.path, []).append(e)
        for path in sorted(by_file, key=lambda p: self._relative(p).lower()):
            section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            section.append(Gtk.Label(label=self._relative(path), xalign=0, css_classes=["heading"]))
            listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
            for e in by_file[path]:
                parts = []
                if e.scheduled:
                    parts.append(f"Scheduled {org.format_date(e.scheduled.date, today)}")
                if e.deadline:
                    parts.append(f"Deadline {org.format_date(e.deadline.date, today)}")
                badge = None
                if e.deadline and e.deadline.date < today:
                    badge = ("Overdue", "error")
                elif e.deadline and e.deadline.date == today:
                    badge = ("Due today", "error")
                listbox.append(self._row(e, parts, badge, show_file=False))
            section.append(listbox)
            self.todo_box.append(section)

    def _agenda_row(self, item, today):
        parts = []
        if t := item.ts.time_label():
            parts.append(t)
        badge = (item.label, KIND_CLASSES[item.kind]) if item.label else None
        if item.kind == "deadline-upcoming":
            parts.append(f"Deadline {org.format_date(item.ts.date, today)}")
        return self._row(item.entry, parts, badge)

    def _row(self, entry, parts, badge=None, show_file=True):
        title = GLib.markup_escape_text(entry.display_title or "(untitled)")
        if entry.is_done:
            title = f"<s>{title}</s>"
        row = Adw.ActionRow(title=title, activatable=True, use_markup=True,
                            title_lines=2, subtitle_lines=1)
        if entry.tags:
            parts = [*parts, " ".join(f"#{t}" for t in entry.tags)]
        if show_file:
            parts = [*parts, self._relative(entry.path)]
        row.set_subtitle(GLib.markup_escape_text(" · ".join(parts)))
        if entry.is_done:
            row.add_css_class("dim-label")

        if entry.keyword:
            check = Gtk.CheckButton(active=entry.is_done, valign=Gtk.Align.CENTER,
                                    tooltip_text="Mark as Not Done" if entry.is_done else "Mark as Done")
            check.connect("toggled", self._on_check_toggled, entry)
            row.add_prefix(check)
        else:
            row.add_prefix(Gtk.Image(icon_name="x-office-calendar-symbolic", css_classes=["dim-label"],
                                     margin_start=2, margin_end=2))

        if entry.priority:
            row.add_suffix(Gtk.Label(label=entry.priority, valign=Gtk.Align.CENTER,
                                     tooltip_text=f"Priority {entry.priority}",
                                     css_classes=["caption-heading", "warning" if entry.priority == "A" else "dim-label"]))
        if badge:
            row.add_suffix(Gtk.Label(label=badge[0], valign=Gtk.Align.CENTER,
                                     css_classes=["caption-heading", badge[1]]))
        row.add_suffix(Gtk.Image(icon_name="go-next-symbolic", css_classes=["dim-label"]))
        row.connect("activated", lambda *_: self.app.open_location(entry.path, entry.line))
        return row

    def _on_check_toggled(self, check, entry):
        keyword = org.DONE_KEYWORDS[0] if check.get_active() else org.TODO_KEYWORDS[0]
        message = self.app.set_entry_keyword(entry, keyword)
        if message:
            self.toasts.add_toast(Adw.Toast(title=message, timeout=3))
        # Rows are rebuilt on refresh, so don't destroy this one while its signal runs
        GLib.idle_add(self._refresh_idle)

    def _refresh_idle(self):
        self.refresh()
        return False

    def _relative(self, path):
        if not path:
            return ""
        directory = getattr(self, "directory", None)
        if directory and path.startswith(directory.rstrip(os.sep) + os.sep):
            return os.path.relpath(path, directory)
        return self._short(path)

    @staticmethod
    def _short(path):
        home = GLib.get_home_dir()
        if path and path.startswith(home):
            return "~" + path[len(home):]
        return path or ""
