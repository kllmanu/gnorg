"""Shared heading search for Files, Agenda, and Calendar."""

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk, Pango  # noqa: E402

import org
from agenda import agenda_line_attributes


class HeadingSearch(Gtk.Popover):
    def __init__(self, window):
        super().__init__(autohide=True, position=Gtk.PositionType.BOTTOM, width_request=400)
        self.window = window
        self.sources = []
        self.results = []
        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=8,
            margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        self.entry = Gtk.SearchEntry(placeholder_text="Search Org headings", hexpand=True)
        self.entry.connect("search-changed", lambda *_: self.refresh_results())
        self.entry.connect("activate", self._activate_first)
        self.entry.connect("stop-search", lambda *_: self.popdown())
        self.entry.connect("next-match", self._focus_first)
        content.append(self.entry)
        self.status = Gtk.Label(label="Type to search headings.", xalign=0, wrap=True)
        content.append(self.status)
        self.errors = Gtk.Label(xalign=0, wrap=True, selectable=True, css_classes=["error"])
        content.append(self.errors)
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.listbox.connect("row-activated", self._activate_row)
        self.scroller = Gtk.ScrolledWindow(
            child=self.listbox, min_content_width=340, min_content_height=160,
            max_content_height=400, propagate_natural_height=True,
            hscrollbar_policy=Gtk.PolicyType.NEVER)
        content.append(self.scroller)
        self.set_child(content)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key_pressed)
        self.add_controller(keys)
        self.connect("closed", self._closed)
        style_manager = Adw.StyleManager.get_default()
        dark_handler = style_manager.connect("notify::dark", lambda *_: self._style_titles())
        self.connect("destroy", lambda *_: style_manager.disconnect(dark_handler))

    def _on_key_pressed(self, _controller, keyval, _keycode, state):
        modifiers = state & Gtk.accelerator_get_default_mod_mask()
        if modifiers == Gdk.ModifierType.CONTROL_MASK and keyval in (Gdk.KEY_n, Gdk.KEY_p):
            self._move_result(1 if keyval == Gdk.KEY_n else -1)
            return True
        if (keyval == Gdk.KEY_Escape or
                keyval == Gdk.KEY_g and state & Gtk.accelerator_get_default_mod_mask()
                == Gdk.ModifierType.CONTROL_MASK):
            self.popdown()
            return True
        focus = self.window.get_focus()
        if keyval == Gdk.KEY_Down and focus is not None and focus.is_ancestor(self.entry):
            self._focus_first()
            return True
        first = self.listbox.get_row_at_index(0)
        if keyval == Gdk.KEY_Up and focus is first:
            self.entry.grab_focus()
            return True
        return False

    def _move_result(self, direction):
        if not self.results:
            return
        focus = self.window.get_focus()
        index = next((i for i in range(len(self.results))
                      if focus is self.listbox.get_row_at_index(i)), -1)
        if index == 0 and direction < 0:
            self.entry.grab_focus()
            return
        index = (0 if direction > 0 else len(self.results) - 1) if index < 0 else min(
            len(self.results) - 1, index + direction)
        self.listbox.get_row_at_index(index).grab_focus()

    def show(self, button):
        if self.get_parent() is not button:
            if self.get_parent() is not None:
                self.unparent()
            self.set_parent(button)
        self.load_sources()
        self.entry.set_text("")
        self.refresh_results()
        self.popup()
        self.entry.grab_focus()

    def load_sources(self):
        app = self.window.get_application()
        self.sources = []
        errors = []
        for path in app.agenda_files():
            tab = app.window_for_path(path)
            if tab:
                text = tab.get_text()
            else:
                try:
                    with open(path, encoding="utf-8", errors="replace") as source:
                        text = source.read()
                except OSError as error:
                    errors.append(f"Could not search {path}: {error}")
                    continue
            self.sources.extend((entry, tab) for entry in org.parse_entries(text, path))
        for tab in app.editor_tabs():
            if tab.path is None:
                self.sources.extend((entry, tab) for entry in org.parse_entries(tab.get_text(), None))
        self.errors.set_label("\n".join(errors))
        self.errors.set_visible(bool(errors))

    def refresh_results(self):
        self.listbox.remove_all()
        words = self.entry.get_text().casefold().split()
        self.results = []
        for entry, tab in self.sources:
            text = " ".join((
                entry.display_title, entry.keyword or "", " ".join(entry.tags),
                entry.category or "", entry.path or "Untitled")).casefold()
            if words and all(word in text for word in words):
                self.results.append((entry, tab))
        self.status.set_label(
            "Type to search headings." if not words else
            "No matching headings." if not self.results else
            f"{len(self.results)} matching heading{'s' if len(self.results) != 1 else ''}")
        for entry, _tab in self.results:
            content = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL, spacing=4,
                margin_top=8, margin_bottom=8, margin_start=8, margin_end=8)
            title = f"{entry.keyword} {entry.display_title}" if entry.keyword else entry.display_title
            label = Gtk.Label(label=title, xalign=0, ellipsize=Pango.EllipsizeMode.END)
            content.append(label)
            details = [os.path.basename(entry.path) if entry.path else "Untitled"]
            timestamp = entry.scheduled or entry.deadline or next(iter(entry.timestamps), None)
            if timestamp:
                details.append(timestamp.describe())
            if entry.tags:
                details.append(" ".join(entry.tags))
            content.append(Gtk.Label(
                label=" | ".join(details), xalign=0, ellipsize=Pango.EllipsizeMode.END,
                css_classes=["caption", "dim-label"]))
            row = Gtk.ListBoxRow(child=content, tooltip_text=entry.path or "Untitled")
            row.set_cursor_from_name("pointer")
            self.listbox.append(row)
        self.scroller.set_visible(bool(self.results))
        self._style_titles()

    def _style_titles(self):
        for index, (entry, _tab) in enumerate(self.results):
            label = self.listbox.get_row_at_index(index).get_child().get_first_child()
            spans = [(0, len(entry.keyword), "done" if entry.is_done else "todo")] if entry.keyword else []
            label.set_attributes(agenda_line_attributes(org.AgendaLine(label.get_label(), spans=spans)))

    def _activate_first(self, *_):
        row = self.listbox.get_row_at_index(0)
        if row is not None:
            self._activate_row(self.listbox, row)

    def _focus_first(self, *_):
        row = self.listbox.get_row_at_index(0)
        if row is not None:
            row.grab_focus()

    def _activate_row(self, _listbox, row):
        entry, tab = self.results[row.get_index()]
        self.popdown()
        app = self.window.get_application()
        if tab is not None:
            if tab not in app.editor_tabs():
                self.window.active_view.toast("The source buffer is no longer open. Search again.")
                return
            tab.present()
            tab.goto_line(entry.line)
        else:
            app.open_location(entry.path, entry.line)

    def _closed(self, *_):
        view = self.window.active_view
        if view is not None:
            view.view.grab_focus()
