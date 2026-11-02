"""Keyboard-first completion for open tabs and recent files."""

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402


def matches_buffer(query, name, path):
    text = f"{name} {path}".casefold()
    return all(term in text for term in query.casefold().split())


class BufferPicker(Gtk.Popover):
    def __init__(self, tabs, current=None, recent_files=()):
        super().__init__(position=Gtk.PositionType.BOTTOM)
        self.tabs = [tab for tab in tabs if tab is not current]
        if current in tabs:
            self.tabs.append(current)
        self.recent_files = [
            file for file in recent_files
            if not any(tab.file is not None and tab.file.equal(file) for tab in tabs)]
        self.results = []
        self.search = Gtk.SearchEntry(placeholder_text="Search documents", hexpand=True)
        self.search.connect("changed", self._filter)
        self.search.connect("activate", lambda *_: self._activate_selected())
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE,
                                   css_classes=["navigation-sidebar"])
        self.listbox.connect("row-activated", lambda _list, row: self._activate(row.get_index()))
        self.empty = Gtk.Label(label="No matching buffers", css_classes=["dim-label"],
                               margin_top=12, margin_bottom=12)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        search_box = Gtk.Box(spacing=6, margin_top=6, margin_bottom=6,
                             margin_start=6, margin_end=6)
        search_box.append(self.search)
        open_button = Gtk.Button(icon_name="document-open-symbolic", tooltip_text="Open file")
        open_button.connect("clicked", lambda *_: self._open_file())
        search_box.append(open_button)
        content.append(search_box)
        content.append(Gtk.Separator())
        content.append(Gtk.ScrolledWindow(child=self.listbox,
                                          min_content_width=340, max_content_height=520,
                                          propagate_natural_height=True,
                                          hscrollbar_policy=Gtk.PolicyType.NEVER))
        content.append(self.empty)
        self.set_child(content)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key_pressed)
        self.add_controller(keys)
        self.connect("closed", self._on_closed)
        self._filter()

    def present(self, window):
        self.window = window
        anchor = (window.utility_pane.open_button if window.split_view.get_show_sidebar()
                  else window._header.sidebar_toggle)
        self.set_parent(anchor)
        self.popup()
        self.search.grab_focus()

    def close(self, restore_focus=True):
        self._restore_focus = restore_focus
        self.popdown()

    def _on_closed(self, *_):
        GLib.idle_add(self._unparent)

    def _unparent(self):
        self.unparent()
        if getattr(self, "_restore_focus", True):
            tab = getattr(self.window, "active_view", None)
            if tab:
                tab.view.grab_focus()
            else:
                self.window.view.grab_focus()
        return GLib.SOURCE_REMOVE

    def _open_file(self):
        self.close(restore_focus=False)
        tab = getattr(self.window, "active_view", None)
        if tab:
            tab.open_dialog()
        else:
            self.window._find_file()

    def _close_tab(self, tab):
        self.close(restore_focus=False)
        tab.close()

    def _filter(self, *_):
        query = self.search.get_text()
        self.listbox.remove_all()
        self.results = []
        for tab in self.tabs:
            name = tab.file.get_basename() if tab.file else "Untitled"
            path = tab.path or ""
            if not matches_buffer(query, name, path):
                continue
            self.results.append(tab)
            directory = os.path.dirname(path)
            home = GLib.get_home_dir()
            if directory == home or directory.startswith(home + os.sep):
                directory = "~" + directory[len(home):]
            row = Adw.ActionRow(title=name, subtitle=directory, use_markup=False,
                                title_lines=1, subtitle_lines=1,
                                activatable=True)
            if tab.buffer.get_modified():
                row.set_title("• " + name)
            close_button = Gtk.Button(icon_name="window-close-symbolic",
                                      tooltip_text="Close document", valign=Gtk.Align.CENTER,
                                      css_classes=["flat"])
            close_button.connect("clicked", lambda _button, document=tab: self._close_tab(document))
            row.add_suffix(close_button)
            self.listbox.append(row)
        for file in self.recent_files:
            name = file.get_basename()
            path = file.get_path() or file.get_uri()
            if not matches_buffer(query, name, path):
                continue
            self.results.append(file)
            directory = os.path.dirname(path)
            home = GLib.get_home_dir()
            if directory == home or directory.startswith(home + os.sep):
                directory = "~" + directory[len(home):]
            row = Adw.ActionRow(title=name, subtitle=directory, use_markup=False,
                                title_lines=1, subtitle_lines=1, activatable=True)
            row.add_suffix(Gtk.Label(label="Recent", css_classes=["dim-label"]))
            self.listbox.append(row)
        self.empty.set_visible(not self.results)
        if self.results:
            self.listbox.select_row(self.listbox.get_row_at_index(0))

    def _activate_selected(self):
        row = self.listbox.get_selected_row()
        if row:
            self._activate(row.get_index())

    def _activate(self, index):
        tab = self.results[index]
        self.close(restore_focus=False)
        if isinstance(tab, Gio.File):
            self.window.get_application().open_file(tab)
        else:
            tab.present()

    def _on_key_pressed(self, _controller, keyval, _keycode, state):
        modifiers = state & Gtk.accelerator_get_default_mod_mask()
        if keyval == Gdk.KEY_Escape or (
                keyval == Gdk.KEY_g and modifiers == Gdk.ModifierType.CONTROL_MASK):
            self.close()
            return True
        control_navigation = (modifiers == Gdk.ModifierType.CONTROL_MASK
                              and keyval in (Gdk.KEY_n, Gdk.KEY_p))
        if modifiers and not control_navigation:
            return False
        if keyval in (Gdk.KEY_Up, Gdk.KEY_Down) or control_navigation:
            row = self.listbox.get_selected_row()
            if row and self.results:
                index = row.get_index() + (1 if keyval in (Gdk.KEY_Down, Gdk.KEY_n) else -1)
                target = self.listbox.get_row_at_index(max(0, min(index, len(self.results) - 1)))
                self.listbox.select_row(target)
                target.grab_focus()
                self.search.grab_focus()
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self._activate_selected()
            return True
        return False
