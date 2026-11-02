"""Calendar navigation and document sources shared by all workspace views."""

import datetime as dt
import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

import org  # noqa: E402


class UtilityPane(Adw.Bin):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.app = window.get_application()
        self._syncing = False
        self._signature = None
        self.rows = []

        self.calendar = Gtk.Calendar(show_week_numbers=True, margin_top=12,
                                     margin_bottom=12, margin_start=12, margin_end=12)
        self.calendar.connect("day-selected", self._on_day_selected)
        for signal in ("next-month", "prev-month", "next-year", "prev-year"):
            self.calendar.connect(signal, lambda *_: self.update_marks())
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(self.calendar)
        content.append(Gtk.Separator())
        content.append(Gtk.Label(label="Files", xalign=0, css_classes=["heading"],
                                 margin_start=18, margin_top=18, margin_bottom=6))
        content.append(Gtk.Label(
            label="Check to show in Calendar and Agenda",
            xalign=0, wrap=True, css_classes=["caption", "dim-label"],
            margin_start=18, margin_end=18, margin_bottom=12))
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE,
                                   css_classes=["navigation-sidebar"])
        self.listbox.connect("row-activated", self._on_file_activated)
        content.append(self.listbox)
        self.empty = Gtk.Label(
            label="Open an Org file or choose a calendar folder in Preferences.",
            wrap=True, margin_start=18, margin_end=18, margin_top=12,
            css_classes=["dim-label"])
        content.append(self.empty)
        header = Adw.HeaderBar(
            title_widget=Gtk.Label(label="Calendar", css_classes=["title"]),
            show_end_title_buttons=False)
        toolbar = Adw.ToolbarView(content=Gtk.ScrolledWindow(
            child=content, hscrollbar_policy=Gtk.PolicyType.NEVER))
        toolbar.add_top_bar(header)
        self.set_child(toolbar)

    def refresh(self):
        sources = self.app.agenda_files()
        documents = self.window.documents()
        items = []
        seen = set()
        for path in sources:
            tab = self.app.window_for_path(path)
            items.append((Gio.File.new_for_path(path), tab, True))
            seen.add(os.path.realpath(path))
        for tab in documents:
            if tab.path and os.path.realpath(tab.path) in seen:
                continue
            items.append((tab.file, tab, False))
        signature = tuple(
            (file.get_uri() if file else None, tab, source,
             tab.buffer.get_modified() if tab else False,
             self.app.agenda_file_visible(file.get_path()) if source else False,
             tab is self.window.active_tab)
            for file, tab, source in items)
        if signature != self._signature:
            self._signature = signature
            focused = next(
                ((row.file.get_uri() if row.file else None, row.document) for row in self.rows
                 if row.visibility and row.visibility.is_focus()), None)
            self.listbox.remove_all()
            self.rows = []
            directory = self.app.agenda_directory()
            for file, tab, source in items:
                name = file.get_basename() if file else "Untitled"
                if tab and tab.buffer.get_modified():
                    name = "• " + name
                path = file.get_path() if file else None
                subtitle = ""
                if path:
                    subtitle = (os.path.dirname(os.path.relpath(path, directory)) if
                                os.path.commonpath((directory, path)) == directory
                                else os.path.dirname(path))
                row = Adw.ActionRow(title=name, subtitle=subtitle, use_markup=False,
                                    title_lines=1, subtitle_lines=1, activatable=True)
                row.document = tab
                row.file = file
                row.visibility = None
                row.set_tooltip_text(path or "Untitled")
                if tab is not None and tab is self.window.active_tab:
                    row.add_css_class("accent")
                if source:
                    check = Gtk.CheckButton(
                        active=self.app.agenda_file_visible(path),
                        tooltip_text=f"Show {file.get_basename()} in Calendar and Agenda",
                        valign=Gtk.Align.CENTER)
                    check.connect("toggled", lambda button, source_path=path:
                                  self.app.set_agenda_file_visible(
                                      source_path, button.get_active()))
                    row.add_prefix(check)
                    row.visibility = check
                if tab:
                    close = Gtk.Button(icon_name="window-close-symbolic",
                                       tooltip_text="Close document", css_classes=["flat"],
                                       valign=Gtk.Align.CENTER)
                    close.connect("clicked", lambda _button, document=tab: document.close())
                    row.add_suffix(close)
                self.listbox.append(row)
                self.rows.append(row)
                if focused == (file.get_uri() if file else None, tab) and row.visibility:
                    row.visibility.grab_focus()
            self.empty.set_visible(not items)
        self.update_marks()

    def _on_file_activated(self, _listbox, row):
        if row.document:
            row.document.present()
        else:
            self.app.open_file(row.file)
        if self.window.split_view.get_collapsed():
            self.window.split_view.set_show_sidebar(False)

    def _on_day_selected(self, calendar):
        if self._syncing:
            return
        date = calendar.get_date()
        selected = dt.date(date.get_year(), date.get_month(), date.get_day_of_month())
        weekly = self.window.stack.get_visible_child_name() == "weekly-agenda"
        self._syncing = True
        try:
            if weekly:
                self.app.show_weekly_agenda(self.window)
                view = self.window.weekly_agenda
            else:
                self.app.show_agenda(self.window)
                view = self.window.agenda
            view.start = org.week_start(selected)
            view.refresh(keep_scroll=False)
        finally:
            self._syncing = False
        self.update_marks()
        if self.window.split_view.get_collapsed():
            self.window.split_view.set_show_sidebar(False)

    def select_week(self, start):
        if self._syncing:
            return
        current = self.calendar.get_date()
        selected = dt.date(current.get_year(), current.get_month(), current.get_day_of_month())
        if org.week_start(selected) != start:
            self._syncing = True
            self.calendar.set_date(GLib.DateTime.new_local(
                start.year, start.month, start.day, 0, 0, 0))
            self._syncing = False
        self.update_marks()

    def update_marks(self):
        current = self.calendar.get_date()
        start = dt.date(current.get_year(), current.get_month(), 1)
        end = (start.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        _, _, entries = self.app.agenda_entries()
        self.calendar.clear_marks()
        for date, items in org.build_agenda(
                entries, start, (end - start).days, dt.date.today()).items():
            if items:
                self.calendar.mark_day(date.day)
