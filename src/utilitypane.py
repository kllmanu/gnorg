"""Calendar navigation and document sources shared by all workspace views."""

import datetime as dt
import hashlib
import math
import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

import org  # noqa: E402

FILE_COLORS = ("#62a0ea", "#f6d32d", "#8ff0a4", "#ffbe6f",
               "#c061cb", "#f66151", "#99c1f1", "#f5a9b8")


def file_color_index(path):
    digest = hashlib.sha256(os.path.realpath(path).encode("utf-8")).digest()
    return int.from_bytes(digest[:4]) % len(FILE_COLORS)


def file_color(path):
    return FILE_COLORS[file_color_index(path)]


def file_order_key(file, document):
    if file:
        path = file.get_path()
        return Gio.File.new_for_path(os.path.realpath(path)).get_uri() if path else file.get_uri()
    return document


class UtilityPane(Adw.Bin):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.app = window.get_application()
        self._syncing = False
        self._signature = None
        self.rows = []
        self._dragging = False

        self.calendar = Gtk.Calendar(show_week_numbers=True, margin_top=12,
                                     margin_bottom=12, margin_start=12, margin_end=12)
        self.calendar.connect("day-selected", self._on_day_selected)
        for signal in ("next-month", "prev-month", "next-year", "prev-year"):
            self.calendar.connect(signal, lambda *_: self.update_marks())
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(self.calendar)
        content.append(Gtk.Separator())
        files_heading = Gtk.Box(spacing=6, margin_start=18, margin_end=12,
                                margin_top=18, margin_bottom=6)
        files_heading.append(Gtk.Label(label="Files", xalign=0, hexpand=True,
                                       css_classes=["heading"]))
        self.new_file_button = Gtk.Button(
            icon_name="list-add-symbolic", tooltip_text="New Document", css_classes=["flat"])
        self.new_file_button.connect("clicked", self._on_new_file)
        files_heading.append(self.new_file_button)
        content.append(files_heading)
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE,
                                   css_classes=["navigation-sidebar", "utility-files"])
        css = Gtk.CssProvider()
        css.load_from_string("""
            .utility-files row.drop-before { box-shadow: inset 0 2px @accent_color; }
            .utility-files row.drop-after { box-shadow: inset 0 -2px @accent_color; }
            .utility-file-visibility {
                min-height: 0;
                min-width: 0;
                padding: 0;
            }
            .utility-file-visibility check {
                min-width: 16px;
                min-height: 16px;
                -gtk-icon-size: 16px;
                padding: 0;
                background: none;
                border: none;
                box-shadow: none;
                color: inherit;
            }
            .utility-file-visibility check:checked {
                -gtk-icon-source: -gtk-icontheme("object-select-symbolic");
            }
        """)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
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
        if self._dragging:
            return
        sources = self.app.agenda_files()
        documents = self.window.documents()
        items = []
        seen = set()
        for path in sources:
            tab = self.app.window_for_path(path)
            items.append((Gio.File.new_for_path(path), tab, True))
            seen.add(os.path.realpath(path))
        for tab in documents:
            if tab.file is None and tab.buffer.get_char_count() == 0:
                continue
            if tab.path and os.path.realpath(tab.path) in seen:
                continue
            items.append((tab.file, tab, False))
        document_positions = {row.document: index for index, row in enumerate(self.rows)
                              if row.document is not None}
        file_positions = {row.file.get_uri(): index for index, row in enumerate(self.rows)
                          if row.file is not None}
        items.sort(key=lambda item: document_positions.get(
            item[1], file_positions.get(item[0].get_uri(), len(self.rows))
            if item[0] else len(self.rows)))
        order = self.app.utility_file_order
        keys = [file_order_key(file, tab) for file, tab, _source in items]
        for file, tab, _source in items:
            key = file_order_key(file, tab)
            if file and tab in order:
                index = order.index(tab)
                order.remove(tab)
                if key not in order:
                    order.insert(index, key)
        for row in self.rows:
            key = file_order_key(row.file, row.document)
            if key not in keys and key in order:
                order.remove(key)
        for key in keys:
            if key not in order:
                order.append(key)
        positions = {key: index for index, key in enumerate(order)}
        items.sort(key=lambda item: positions[file_order_key(item[0], item[1])])
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
            for file, tab, source in items:
                name = file.get_basename() if file else "Untitled"
                if tab and tab.buffer.get_modified():
                    name = "• " + name
                path = file.get_path() if file else None
                row = Adw.PreferencesRow(title=name, use_markup=False, activatable=True)
                content = Gtk.Box(spacing=12, margin_start=6, margin_end=6)
                dot = Gtk.DrawingArea(width_request=16, height_request=16,
                                      valign=Gtk.Align.CENTER,
                                      accessible_role=Gtk.AccessibleRole.PRESENTATION)
                color = Gdk.RGBA()
                color.parse(file_color(path) if path else "#9a9996")
                dot.set_draw_func(self._draw_dot, color)
                row.color_dot = dot
                content.append(dot)
                content.append(Gtk.Label(
                    label=name, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END))
                row.set_child(content)
                row.document = tab
                row.file = file
                row.visibility = None
                row.set_tooltip_text(path or "Untitled")
                drag = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
                drag.connect("prepare", self._on_drag_prepare, row)
                drag.connect("drag-end", self._on_drag_end)
                row.add_controller(drag)
                drop = Gtk.DropTarget.new(Adw.PreferencesRow, Gdk.DragAction.MOVE)
                drop.connect("motion", self._on_drop_motion, row)
                drop.connect("leave", lambda _target, target_row=row:
                             self._clear_drop_indicator(target_row))
                drop.connect("drop", self._on_drop, row)
                row.add_controller(drop)
                if source:
                    check = Gtk.CheckButton(
                        active=self.app.agenda_file_visible(path),
                        tooltip_text=f"Show {file.get_basename()} in Calendar and Agenda",
                        valign=Gtk.Align.CENTER, css_classes=["utility-file-visibility"])
                    check.connect("toggled", lambda button, source_path=path:
                                  self.app.set_agenda_file_visible(
                                      source_path, button.get_active()))
                    content.append(check)
                    row.visibility = check
                self.listbox.append(row)
                self.rows.append(row)
                if focused == (file.get_uri() if file else None, tab) and row.visibility:
                    row.visibility.grab_focus()
            self.empty.set_visible(not items)
        self.app.save_utility_file_order()
        self.update_marks()

    def _on_drag_prepare(self, source, x, y, row):
        picked = row.pick(x, y, Gtk.PickFlags.DEFAULT)
        if row.visibility and (picked is row.visibility
                               or picked and picked.is_ancestor(row.visibility)):
            return None
        self._dragging = True
        source.set_icon(Gtk.WidgetPaintable.new(row), int(x), int(y))
        return Gdk.ContentProvider.new_for_value(row)

    def _on_drag_end(self, *_):
        self._dragging = False
        for row in self.rows:
            self._clear_drop_indicator(row)
        self.refresh()

    @staticmethod
    def _clear_drop_indicator(row):
        row.remove_css_class("drop-before")
        row.remove_css_class("drop-after")

    def _on_drop_motion(self, _target, _x, y, row):
        self._clear_drop_indicator(row)
        row.add_css_class("drop-before" if y < row.get_height() / 2 else "drop-after")
        return Gdk.DragAction.MOVE

    def _on_drop(self, _target, source_row, _x, y, target_row):
        self._clear_drop_indicator(target_row)
        if source_row not in self.rows or target_row not in self.rows:
            return False
        if source_row is target_row:
            return True
        order = self.app.utility_file_order
        source = file_order_key(source_row.file, source_row.document)
        target = file_order_key(target_row.file, target_row.document)
        order.remove(source)
        index = order.index(target) + (y >= target_row.get_height() / 2)
        order.insert(index, source)
        self.app.save_utility_file_order()
        self.app.queue_agenda_refresh()
        return True

    @staticmethod
    def _draw_dot(_area, cr, width, height, color):
        cr.set_source_rgba(color.red, color.green, color.blue, color.alpha)
        cr.arc(width / 2, height / 2, min(width, height) / 2, 0, math.tau)
        cr.fill()

    def _on_file_activated(self, _listbox, row):
        if row.document:
            row.document.present()
        else:
            self.app.open_file(row.file)
        if self.window.split_view.get_collapsed():
            self.window.split_view.set_show_sidebar(False)

    def _on_new_file(self, *_):
        self.window.new_tab().present()
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
