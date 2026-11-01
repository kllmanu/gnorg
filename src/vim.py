#!/usr/bin/env python3
import datetime as dt
import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GtkSource", "5")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Graphene, Gtk, GtkSource, Pango  # noqa: E402

import org  # noqa: E402
from agenda import AgendaWindow  # noqa: E402
from datepicker import DatePopover  # noqa: E402
from keybindings import OrgKeySequences  # noqa: E402
from indent import OrgIndent  # noqa: E402
from bufferpicker import BufferPicker  # noqa: E402

APP_ID = "io.github.kllmanu.Organizer"
APP_NAME = "Organizer"
VERSION = "0.2.0"

HERE = os.path.dirname(os.path.realpath(__file__))
DEFAULT_AGENDA_DIRECTORY = "~/org"


def data_path(*parts):
    """Find a data file, both when running from the source tree and when installed."""
    for base in (os.path.join(HERE, os.pardir, "data"), HERE):
        path = os.path.normpath(os.path.join(base, *parts))
        if os.path.exists(path):
            return path
    return None


def short_path(path):
    home = GLib.get_home_dir()
    if path and (path == home or path.startswith(home + os.sep)):
        return "~" + path[len(home):]
    return path or ""


def locate_heading(lines, line, heading_text):
    """Find a heading again after the text may have changed: same line, else the nearest identical one."""
    if 0 <= line < len(lines) and lines[line] == heading_text:
        return line
    matches = [i for i, text in enumerate(lines) if text == heading_text]
    return min(matches, key=lambda i: abs(i - line)) if matches else None


def file_filters():
    org_filter = Gtk.FileFilter(name="Org Files")
    org_filter.add_pattern("*.org")
    all_filter = Gtk.FileFilter(name="All Files")
    all_filter.add_pattern("*")
    filters = Gio.ListStore.new(Gtk.FileFilter)
    filters.append(org_filter)
    filters.append(all_filter)
    return filters


def same_file(a, b):
    return bool(a and b) and os.path.realpath(a) == os.path.realpath(b)

BASE_FONT_SIZE = 16  # pt, at 100% zoom
ZOOM_STEP = 10
ZOOM_MIN = 30
ZOOM_MAX = 400


class EditorTab(Adw.Bin):
    def __init__(self, window):
        super().__init__()
        self.window = window
        app = window.get_application()
        self.actions = Gio.SimpleActionGroup()
        self.insert_action_group("win", self.actions)
        self.page = None
        self.file = None
        self._force_close = False
        self._zoom_toast = None
        self._active_date_popover = None

        self._build_editor()
        self._build_ui()
        self._setup_vim()
        self._setup_actions()
        self._org_key_sequences = OrgKeySequences(self, {
            "C-c C-t": self.cycle_todo,
            "C-c C-c": self.toggle_checkbox,
            "C-c C-s": lambda: self.edit_planning("SCHEDULED"),
            "C-c C-d": lambda: self.edit_planning("DEADLINE"),
            "C-c a": app.show_agenda,
            "C-x C-c": lambda: app.activate_action("quit", None),
            "C-x C-f": self.open_dialog,
            "C-x C-s": self.save,
            "C-x b": app.show_buffers,
            "C-g": self._keyboard_quit,
            "M-Return": self.insert_item,
        })
        self._setup_style()
        self.org_indent = OrgIndent(self.view, app.org_indent_enabled)

        self.buffer.connect("modified-changed", lambda *_: self._update_title())
        self.buffer.connect("notify::cursor-position", lambda *_: self._update_position())
        self.buffer.connect("changed", lambda *_: app.queue_agenda_refresh())
        self._update_title()
        self._update_position()

    # ---------- UI ----------

    def _build_editor(self):
        self.buffer = GtkSource.Buffer(
            highlight_syntax=True, highlight_matching_brackets=True,
            language=GtkSource.LanguageManager.get_default().get_language("org"))
        self.view = GtkSource.View(
            buffer=self.buffer,
            monospace=True,
            css_classes=["organizer-editor"],
            show_line_numbers=True,
            highlight_current_line=True,
            auto_indent=True,
            smart_backspace=True,
            tab_width=4,
            indent_width=4,
            insert_spaces_instead_of_tabs=True,
            top_margin=8,
            bottom_margin=8,
            left_margin=6,
            right_margin=6,
            wrap_mode=Gtk.WrapMode.NONE,
        )

    def _build_ui(self):
        self.title_widget = Adw.WindowTitle()
        header = Adw.HeaderBar(title_widget=self.title_widget)
        self.header = header

        self.open_button = Gtk.Button(label="Open", tooltip_text="Open documents")
        open_content = Gtk.Box(spacing=6)
        open_content.append(Gtk.Label(label="Open"))
        open_content.append(Gtk.Image(icon_name="pan-down-symbolic"))
        self.open_button.set_child(open_content)
        self.open_button.connect("clicked", lambda *_: self.get_application().show_buffers())
        header.pack_start(self.open_button)
        header.pack_start(Gtk.Button(icon_name="tab-new-symbolic",
                                     tooltip_text="New Tab (Ctrl+T)", action_name="win.new"))
        header.pack_start(Gtk.Button(icon_name="x-office-calendar-symbolic",
                                     tooltip_text="Agenda (C-c a)", action_name="app.agenda"))

        menu = Gio.Menu()
        file_section = Gio.Menu()
        file_section.append("Save As…", "win.save-as")
        menu.append_section(None, file_section)
        view_section = Gio.Menu()
        view_section.append("Vim Mode", "win.vim-mode")
        view_section.append("Wrap Lines", "win.wrap")
        menu.append_section(None, view_section)
        app_section = Gio.Menu()
        app_section.append("Preferences", "app.preferences")
        app_section.append(f"About {APP_NAME}", "app.about")
        menu.append_section(None, app_section)

        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                       primary=True, tooltip_text="Main Menu"))

        scroller = Gtk.ScrolledWindow(child=self.view, vexpand=True)
        self.get_application().setup_scroll_zoom(scroller)
        # Popovers can't be parented to the text view itself, so they hang off this box
        self.editor_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.editor_box.append(scroller)

        # Status bar: vim command bar on the left, pending keys + cursor position on the right
        self.cmdbar_label = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END,
                                      css_classes=["monospace"])
        self.cmdtext_label = Gtk.Label(css_classes=["monospace", "dim-label"])
        self.position_label = Gtk.Label(css_classes=["monospace", "dim-label"])
        status = Gtk.Box(spacing=18, margin_start=12, margin_end=12, margin_top=4, margin_bottom=4)
        status.append(self.cmdbar_label)
        status.append(self.cmdtext_label)
        status.append(self.position_label)

        toolbar_view = Adw.ToolbarView(content=self.editor_box)
        toolbar_view.add_bottom_bar(status)

        self.toasts = Adw.ToastOverlay(child=toolbar_view)
        self.set_child(self.toasts)

    def get_application(self):
        return self.window.get_application()

    def present(self):
        self.window.tabs.set_selected_page(self.page)
        self.window.present()
        self.view.grab_focus()

    def close(self):
        self.window.tabs.close_page(self.page)

    def _setup_vim(self):
        self.vim = GtkSource.VimIMContext()
        self.vim.set_client_widget(self.view)
        flags = GObject.BindingFlags.SYNC_CREATE
        self.vim.bind_property("command-bar-text", self.cmdbar_label, "label", flags)
        self.vim.bind_property("command-text", self.cmdtext_label, "label", flags)
        self.vim.connect("write", self._on_vim_write)
        self.vim.connect("edit", self._on_vim_edit)
        self.vim.connect("execute-command", self._on_vim_execute_command)

        self.vim_controller = Gtk.EventControllerKey(
            propagation_phase=Gtk.PropagationPhase.CAPTURE)
        self.vim_controller.set_im_context(self.vim)
        self.view.add_controller(self.vim_controller)
        self.vim_enabled = True

    def _set_vim_enabled(self, enabled):
        if enabled == self.vim_enabled:
            return
        self.vim_enabled = enabled
        if enabled:
            self.view.add_controller(self.vim_controller)
        else:
            self.vim.reset()
            self.view.remove_controller(self.vim_controller)
            self.view.set_overwrite(False)
        self.cmdbar_label.set_visible(enabled)
        self.cmdtext_label.set_visible(enabled)
        self.view.grab_focus()

    def _setup_actions(self):
        def add(name, callback):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda *_: callback())
            self.actions.add_action(action)

        add("new", lambda: self.window.new_tab().present())
        add("reopen", self.window.reopen_tab)
        add("close", self.close)
        add("open", self.open_dialog)
        add("save", self.save)
        add("save-as", self.save_as)
        vim_action = Gio.SimpleAction.new_stateful("vim-mode", None, GLib.Variant.new_boolean(True))
        vim_action.connect("change-state", self._on_vim_mode_changed)
        self.actions.add_action(vim_action)

        wrap_action = Gio.SimpleAction.new_stateful("wrap", None, GLib.Variant.new_boolean(False))
        wrap_action.connect("change-state", self._on_wrap_changed)
        self.actions.add_action(wrap_action)

    def _on_vim_mode_changed(self, action, value):
        action.set_state(value)
        self._set_vim_enabled(value.get_boolean())

    def _on_wrap_changed(self, action, value):
        action.set_state(value)
        self.view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR if value.get_boolean() else Gtk.WrapMode.NONE)

    def _setup_style(self):
        style_manager = Adw.StyleManager.get_default()
        self._dark_handler = style_manager.connect("notify::dark", lambda *_: self._update_scheme())
        self.connect("destroy", lambda *_: style_manager.disconnect(self._dark_handler))
        self._update_scheme()

    def _update_scheme(self):
        dark = Adw.StyleManager.get_default().get_dark()
        schemes = GtkSource.StyleSchemeManager.get_default()
        scheme = schemes.get_scheme("organizer-dark" if dark else "organizer") or \
            schemes.get_scheme("Adwaita-dark" if dark else "Adwaita")
        self.buffer.set_style_scheme(scheme)

    def _update_title(self):
        if self.file:
            name = self.file.get_basename()
            parent = self.file.get_parent()
            subtitle = parent.get_path() if parent else ""
            home = GLib.get_home_dir()
            if subtitle and subtitle.startswith(home):
                subtitle = "~" + subtitle[len(home):]
        else:
            name, subtitle = "Untitled", ""
        modified = self.buffer.get_modified()
        self.title_widget.set_title(("• " if modified else "") + name)
        self.title_widget.set_subtitle(subtitle)
        if self.page is not None:
            self.page.set_title(("• " if modified else "") + name)
            self.page.set_tooltip(short_path(self.path) or name)
        if self.window.active_tab is self:
            self.window.set_title(f"{name} – {APP_NAME}")

    def _update_position(self):
        it = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        self.position_label.set_label(f"{it.get_line() + 1}:{self.view.get_visual_column(it) + 1}")

    def toast(self, message):
        self.toasts.add_toast(Adw.Toast(title=message, timeout=2))

    def show_zoom(self, zoom):
        # Reuse one toast so rapid zoom changes update it instantly instead of queueing.
        title = f"Zoom {zoom}%"
        if self._zoom_toast is None:
            self._zoom_toast = Adw.Toast(title=title, timeout=1, priority=Adw.ToastPriority.HIGH)
            self._zoom_toast.connect("dismissed", self._on_zoom_toast_dismissed)
        else:
            self._zoom_toast.set_title(title)
        # Re-adding a shown toast resets its timeout
        self.toasts.add_toast(self._zoom_toast)

    def _on_zoom_toast_dismissed(self, toast):
        if self._zoom_toast is toast:
            self._zoom_toast = None

    @property
    def is_pristine(self):
        return self.file is None and not self.buffer.get_modified() and self.buffer.get_char_count() == 0

    @property
    def path(self):
        return self.file.get_path() if self.file else None

    def get_text(self):
        start, end = self.buffer.get_bounds()
        return self.buffer.get_text(start, end, True)

    def goto_line(self, line):
        ok, it = self.buffer.get_iter_at_line(line)
        if not ok:
            it = self.buffer.get_end_iter()
        self.buffer.place_cursor(it)
        self.view.grab_focus()
        # Scroll once the (possibly freshly loaded) text has been laid out
        GLib.idle_add(lambda: self.view.scroll_to_mark(self.buffer.get_insert(), 0.1, True, 0, 0.3) and False)

    # ---------- Org commands ----------

    def toggle_checkbox(self):
        line = self._cursor_line()
        text = self.get_text().split("\n")[line]
        updated = org.toggle_checkbox(text)
        if updated is None:
            self.toast("Not on a list item with a checkbox")
            return
        self.apply_edit((line, line + 1, [updated]))

    def insert_item(self):
        line = self._cursor_line()
        lines = self.get_text().split("\n")
        prefix = org.new_item_prefix(lines[line])
        if prefix is None:
            self.toast("Not on an Org heading or list item")
            return
        self.apply_edit((line, line + 1, [lines[line], prefix]))
        self.buffer.place_cursor(self.buffer.get_iter_at_line_offset(line + 1, len(prefix))[1])
        self.view.scroll_to_mark(self.buffer.get_insert(), 0.1, False, 0, 0)
        self.view.grab_focus()

    def _cursor_line(self):
        return self.buffer.get_iter_at_mark(self.buffer.get_insert()).get_line()

    def _current_heading(self):
        lines = self.get_text().split("\n")
        idx = org.find_heading(lines, self._cursor_line())
        if idx is None:
            self.toast("Not inside an Org heading")
        return lines, idx

    def cycle_todo(self):
        lines, idx = self._current_heading()
        if idx is None:
            return
        edit, message = org.cycle_todo(lines, idx, dt.date.today())
        self.apply_edit(edit)
        if message:
            self.toast(message)

    def edit_heading(self, line, heading_text, change):
        """Used by the agenda; change(lines, idx) returns (edit, message). Returns (ok, message)."""
        lines = self.get_text().split("\n")
        idx = locate_heading(lines, line, heading_text)
        if idx is None:
            return False, None
        edit, message = change(lines, idx)
        self.apply_edit(edit)
        return True, message

    def edit_planning(self, kind):
        lines, idx = self._current_heading()
        if idx is None:
            return
        heading_text = lines[idx]
        today = dt.date.today()
        popover = DatePopover("Schedule" if kind == "SCHEDULED" else "Deadline",
                              org.get_planning(lines, idx, kind), today)
        self._active_date_popover = popover
        popover.set_parent(self.editor_box)
        popover.set_pointing_to(self._cursor_rect())

        def on_apply(_popover, ts):
            current = self.get_text().split("\n")
            i = locate_heading(current, idx, heading_text)
            if i is None:
                self.toast("The heading has changed")
                return
            self.apply_edit(org.set_planning(current, i, kind, ts))
            what = "Scheduled" if kind == "SCHEDULED" else "Deadline"
            if ts:
                self.toast(f"{what}: {ts.describe(today)}")
            else:
                self.toast(f"{what} removed")

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
        _ok, point = self.view.compute_point(self.editor_box, Graphene.Point().init(x, y))
        rect = Gdk.Rectangle()
        rect.x, rect.y = int(point.x), int(point.y)
        rect.width, rect.height = max(location.width, 1), location.height
        return rect

    def apply_edit(self, edit):
        """Replace whole lines as a single undoable action, keeping the cursor in place."""
        start, end, new_lines = edit
        buf = self.buffer
        cursor = buf.get_iter_at_mark(buf.get_insert())
        cur_line, cur_col = cursor.get_line(), cursor.get_line_offset()
        old_first = self.get_text().split("\n")[start]

        line_count = buf.get_line_count()
        text = "\n".join(new_lines)
        start_iter = buf.get_iter_at_line(start)[1]
        if end < line_count:
            end_iter = buf.get_iter_at_line(end)[1]
            text += "\n"
        else:
            end_iter = buf.get_end_iter()

        buf.begin_user_action()
        buf.delete(start_iter, end_iter)
        buf.insert(buf.get_iter_at_line(start)[1], text)
        buf.end_user_action()

        if cur_line >= end:
            cur_line += len(new_lines) - (end - start)
        elif cur_line == start and new_lines:
            stars = len(old_first) - len(old_first.lstrip("*"))
            if cur_col > stars:
                cur_col = max(stars, cur_col + len(new_lines[0]) - len(old_first))
        elif cur_line >= start + len(new_lines):
            cur_line = start + max(len(new_lines) - 1, 0)
        ok, it = buf.get_iter_at_line(cur_line)
        if ok:
            line_end = it.copy()
            if not line_end.ends_line():
                line_end.forward_to_line_end()
            it.set_line_offset(min(cur_col, line_end.get_line_offset()))
            buf.place_cursor(it)

    # ---------- Vim command handling ----------

    def _on_vim_write(self, _vim, _view, path):
        if path:
            self.save_to(Gio.File.new_for_path(os.path.expanduser(path)))
        else:
            self.save()

    def _on_vim_edit(self, _vim, _view, path):
        if not path:
            self.open_dialog()
        elif self.buffer.get_modified():
            self.toast("No write since last change")
        else:
            self.load(Gio.File.new_for_path(os.path.expanduser(path)))

    def _on_vim_execute_command(self, _vim, command):
        cmd = command.strip().lstrip(":").strip()
        if cmd in ("q", "quit", "clo", "close"):
            if self.buffer.get_modified():
                self.toast("No write since last change (add ! to override)")
            else:
                self.close()
            return True
        if cmd in ("q!", "quit!"):
            self.force_close()
            return True
        if cmd in ("wq", "x", "xit", "exit"):
            if cmd != "wq" and not self.buffer.get_modified():
                self.close()
            else:
                self.save(then=self.close)
            return True
        if cmd in ("qa", "qall", "qa!", "qall!"):
            if cmd.endswith("!"):
                for tab in self.get_application().editor_tabs():
                    tab._force_close = True
            for win in list(self.get_application().get_windows()):
                win.close()
            return True
        return False

    # ---------- File handling ----------

    def open_dialog(self):
        dialog = Gtk.FileDialog(title="Open File", filters=file_filters())
        directory = self.get_application().agenda_directory()
        if os.path.isdir(directory):
            dialog.set_initial_folder(Gio.File.new_for_path(directory))

        def on_done(dlg, res):
            try:
                file = dlg.open_finish(res)
            except GLib.Error:
                return
            if self.is_pristine:
                self.load(file)
            else:
                self.get_application().open_file(file)

        dialog.open(self.window, None, on_done)

    def load(self, file, then=None):
        def on_loaded(f, res):
            try:
                _ok, contents, _etag = f.load_contents_finish(res)
            except GLib.Error as e:
                if e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.NOT_FOUND):
                    # Like vim: editing a non-existent path starts a new file
                    self._set_file(f, "")
                    self.toast(f"New file “{f.get_basename()}”")
                else:
                    self.toast(f"Could not open file: {e.message}")
                return
            self._set_file(f, contents.decode("utf-8", errors="replace"), contents)
            if then:
                then()

        file.load_contents_async(None, on_loaded)

    def _set_file(self, file, text, raw=None):
        self.file = file
        self.buffer.begin_irreversible_action()
        self.buffer.set_text(text)
        self.buffer.end_irreversible_action()
        self.buffer.place_cursor(self.buffer.get_start_iter())
        self.buffer.set_modified(False)
        self._update_language(raw)
        self._update_title()
        self.view.grab_focus()

    def _update_language(self, raw=None):
        name = self.file.get_basename()
        content_type, _uncertain = Gio.content_type_guess(name, raw)
        manager = GtkSource.LanguageManager.get_default()
        # Org is the default for anything that isn't recognised as another language
        language = manager.guess_language(name, content_type) or manager.get_language("org")
        self.buffer.set_language(language)

    def save(self, then=None, on_cancel=None):
        if self.file is None:
            self.save_as(then=then, on_cancel=on_cancel)
        else:
            self.save_to(self.file, then=then, on_cancel=on_cancel)

    def save_as(self, then=None, on_cancel=None):
        dialog = Gtk.FileDialog(title="Save File", filters=file_filters())
        if self.file:
            dialog.set_initial_file(self.file)
        else:
            dialog.set_initial_name("Untitled.org")
            directory = self.get_application().agenda_directory()
            if os.path.isdir(directory):
                dialog.set_initial_folder(Gio.File.new_for_path(directory))

        def on_done(dlg, res):
            try:
                file = dlg.save_finish(res)
            except GLib.Error:
                if on_cancel:
                    on_cancel()
                return
            self.save_to(file, then=then, on_cancel=on_cancel)

        dialog.save(self.window, None, on_done)

    def save_to(self, file, then=None, on_cancel=None):
        start, end = self.buffer.get_bounds()
        text = self.buffer.get_text(start, end, True)
        if text and not text.endswith("\n"):
            text += "\n"
        data = text.encode("utf-8")

        def on_saved(f, res):
            try:
                f.replace_contents_finish(res)
            except GLib.Error as e:
                self.toast(f"Could not save file: {e.message}")
                if on_cancel:
                    on_cancel()
                return
            if self.file is None or not self.file.equal(f):
                self.file = f
                self._update_language()
            self.buffer.set_modified(False)
            self._update_title()
            self.toast(f"“{f.get_basename()}” written, {len(data)} bytes")
            self.get_application().queue_agenda_refresh()
            if then:
                then()

        file.replace_contents_async(data, None, False, Gio.FileCreateFlags.NONE, None, on_saved)

    # ---------- Closing ----------

    def force_close(self):
        self._force_close = True
        self.close()

    def request_close(self, finish):
        if self._force_close or not self.buffer.get_modified():
            finish(True)
            return

        dialog = Adw.AlertDialog(
            heading="Save Changes?",
            body="This document contains unsaved changes. "
                 "Changes which are not saved will be permanently lost.",
            close_response="cancel",
            default_response="save",
        )
        dialog.add_response("cancel", "_Cancel")
        dialog.add_response("discard", "_Discard")
        dialog.add_response("save", "_Save")
        dialog.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)

        def on_response(_dlg, response):
            if response == "discard":
                finish(True)
            elif response == "save":
                self.save(then=lambda: finish(True), on_cancel=lambda: finish(False))
            else:
                finish(False)

        dialog.connect("response", on_response)
        dialog.present(self.window)


class EditorWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, default_width=960, default_height=680)
        if app.settings:
            self.set_default_size(
                app.settings.get_int("window-width"),
                app.settings.get_int("window-height"))
            if app.settings.get_boolean("window-maximized"):
                self.maximize()
        self._closing = False
        self._force_close = False
        self._closed_tabs = []
        self.tabs = Adw.TabView()
        bar = Adw.TabBar(view=self.tabs, autohide=True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(bar)
        box.append(self.tabs)
        self.tabs.set_vexpand(True)
        self.toolbar_view = Adw.ToolbarView(content=box)
        self._header = None
        self.set_content(self.toolbar_view)
        self.tabs.connect("notify::selected-page", self._on_selected)
        self.tabs.connect("close-page", self._on_close_page)
        self.connect("close-request", self._on_close_request)

    @property
    def active_tab(self):
        page = self.tabs.get_selected_page()
        return page.get_child() if page else None

    def documents(self):
        return [self.tabs.get_nth_page(i).get_child() for i in range(self.tabs.get_n_pages())]

    def new_tab(self):
        tab = EditorTab(self)
        tab.page = self.tabs.append(tab)
        self.tabs.set_selected_page(tab.page)
        tab._update_title()
        return tab

    def reopen_tab(self):
        if not self._closed_tabs:
            return
        file, text, modified, offset = self._closed_tabs.pop()
        tab = self.new_tab()
        if file is not None:
            tab._set_file(file, text)
        else:
            tab.buffer.set_text(text)
        tab.buffer.set_modified(modified)
        tab.buffer.place_cursor(tab.buffer.get_iter_at_offset(offset))
        tab.present()

    def _on_selected(self, *_):
        tab = self.active_tab
        if tab is None:
            return
        if self._header is not tab.header:
            if self._header is not None:
                self.toolbar_view.remove(self._header)
            self._header = tab.header
            self.toolbar_view.add_top_bar(self._header)
        for name in tab.actions.list_actions():
            self.add_action(tab.actions.lookup_action(name))
        tab._update_title()
        tab.view.grab_focus()

    def _on_close_page(self, _tabs, page):
        def finish(confirmed):
            if confirmed:
                tab = page.get_child()
                offset = tab.buffer.get_iter_at_mark(tab.buffer.get_insert()).get_offset()
                self._closed_tabs.append(
                    (tab.file, tab.get_text(), tab.buffer.get_modified(), offset))
            self.tabs.close_page_finish(page, confirmed)
            if not self.tabs.get_n_pages() and not self._closing:
                self._force_close = True
                self.close()
        page.get_child().request_close(finish)
        return True

    def _on_close_request(self, *_):
        if self._force_close:
            return False
        self.get_application()._on_quit()
        return True


class OrganizerApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self.zoom = None
        self.zoom_css = None
        self.settings = None
        self.agenda = None
        self._agenda_refresh_id = 0
        self.org_indent_enabled = True
        self._quitting = False
        self._session_restored = False

    def do_startup(self):
        Adw.Application.do_startup(self)
        GLib.set_application_name(APP_NAME)

        self.settings = self._load_settings()
        if self.settings:
            self.org_indent_enabled = self.settings.get_boolean("org-indent-mode")

        language_specs = data_path("language-specs")
        if language_specs:
            languages = GtkSource.LanguageManager.get_default()
            languages.set_search_path([language_specs] + list(languages.get_search_path()))
        styles = data_path("styles")
        if styles:
            GtkSource.StyleSchemeManager.get_default().prepend_search_path(styles)

        self.zoom_css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), self.zoom_css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        for name, callback in (
            ("about", self._on_about),
            ("quit", self._on_quit),
            ("agenda", lambda *_: self.show_agenda()),
            ("preferences", lambda *_: self.show_preferences()),
            ("zoom-in", lambda *_: self.set_zoom(self.zoom + ZOOM_STEP)),
            ("zoom-out", lambda *_: self.set_zoom(self.zoom - ZOOM_STEP)),
            ("zoom-reset", lambda *_: self.set_zoom(100)),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)

        self.set_accels_for_action("app.zoom-in", ["<Ctrl>plus", "<Ctrl>equal", "<Ctrl>KP_Add"])
        self.set_accels_for_action("app.zoom-out", ["<Ctrl>minus", "<Ctrl>KP_Subtract"])
        self.set_accels_for_action("app.zoom-reset", ["<Ctrl>0", "<Ctrl>KP_0"])
        self.set_zoom(self.settings.get_int("zoom") if self.settings else 100, notify=False)

        self.set_accels_for_action("app.quit", ["<Ctrl>q"])
        self.set_accels_for_action("win.new", ["<Ctrl>t"])
        self.set_accels_for_action("win.reopen", ["<Ctrl><Shift>t"])
        self.set_accels_for_action("win.open", ["<Ctrl>o"])
        self.set_accels_for_action("win.save", ["<Ctrl>s"])
        self.set_accels_for_action("win.save-as", ["<Ctrl><Shift>s"])
        self.set_accels_for_action("win.close", ["<Ctrl>w"])
        self.set_accels_for_action("app.preferences", ["<Ctrl>comma"])

    def do_activate(self):
        self.restore_session()
        windows = self.editor_windows()
        if windows:
            windows[0].present()
        else:
            self.new_tab().present()

    def do_open(self, files, _n_files, _hint):
        self.restore_session()
        for file in files:
            self.open_file(file)

    def restore_session(self):
        if self._session_restored:
            return
        self._session_restored = True
        if self.settings:
            for uri in self.settings.get_strv("session-files"):
                self.open_file(Gio.File.new_for_uri(uri))

    def save_session(self):
        if self.settings:
            uris = [tab.file.get_uri() for tab in self.editor_tabs() if tab.file is not None]
            self.settings.set_strv("session-files", uris)
            Gio.Settings.sync()

    def new_window(self):
        win = EditorWindow(self)
        return win

    def editor_windows(self):
        return [w for w in self.get_windows() if isinstance(w, EditorWindow)]

    def editor_tabs(self):
        return [tab for window in self.editor_windows() for tab in window.documents()]

    def new_tab(self):
        active = self.get_active_window()
        windows = self.editor_windows()
        window = active if isinstance(active, EditorWindow) else (windows[0] if windows else self.new_window())
        return window.new_tab()

    def window_for_path(self, path):
        for win in self.editor_tabs():
            if same_file(win.path, path):
                return win
        return None

    def open_file(self, file, then=None):
        win = self.window_for_path(file.get_path()) if file.get_path() else None
        if win is None:
            for w in self.editor_tabs():
                if w.file and w.file.equal(file):
                    win = w
        if win:
            win.present()
            if then:
                then(win)
            return win
        win = self.new_tab()
        win.file = file
        win.load(file, (lambda: then(win)) if then else None)
        win.present()
        return win

    # ---------- Org agenda ----------

    def agenda_directory(self):
        directory = self.settings.get_string("agenda-directory") if self.settings else ""
        return os.path.abspath(os.path.expanduser(directory or DEFAULT_AGENDA_DIRECTORY))

    def agenda_entries(self):
        """All entries of the .org files in the agenda folder; unsaved buffers win over disk."""
        directory = self.agenda_directory()
        files = org.find_org_files(directory) if os.path.isdir(directory) else []
        entries = []
        for path in files:
            win = self.window_for_path(path)
            if win:
                text = win.get_text()
            else:
                try:
                    with open(path, encoding="utf-8", errors="replace") as f:
                        text = f.read()
                except OSError:
                    continue
            entries.extend(org.parse_entries(text, path))
        return directory, files, entries

    def open_location(self, path, line):
        self.open_file(Gio.File.new_for_path(path), lambda win: win.goto_line(line))

    def edit_entry(self, entry, change):
        """Edit an agenda entry's heading, in its open window or on disk.

        change(lines, idx) returns (edit, message). Returns a message to show, if any.
        """
        win = self.window_for_path(entry.path)
        if win:
            ok, message = win.edit_heading(entry.line, entry.heading_text, change)
            return message if ok else "The heading was not found, refresh the agenda"
        try:
            with open(entry.path, encoding="utf-8") as f:
                lines = f.read().split("\n")
            idx = locate_heading(lines, entry.line, entry.heading_text)
            if idx is None:
                return "The heading was not found, refresh the agenda"
            edit, message = change(lines, idx)
            lines = org.apply_edit(lines, edit)
            tmp = entry.path + ".organizer-tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            os.replace(tmp, entry.path)
        except OSError as e:
            return f"Could not update {os.path.basename(entry.path)}: {e.strerror}"
        return message

    def show_agenda(self):
        if self.agenda is None:
            self.agenda = AgendaWindow(self)
            # The window is only disposed once this reference is dropped, so let go on close
            self.agenda.connect("close-request", self._on_agenda_closed)
        self.agenda.refresh(keep_scroll=False)
        self.agenda.present()
        self.agenda.view.grab_focus()

    def _on_agenda_closed(self, win):
        if self.agenda is win:
            self.agenda = None
        return False

    def queue_agenda_refresh(self):
        if self.agenda is None or self._agenda_refresh_id:
            return

        def refresh():
            self._agenda_refresh_id = 0
            if self.agenda is not None:
                self.agenda.refresh()
            return False

        self._agenda_refresh_id = GLib.timeout_add(300, refresh)

    # ---------- Dialogs ----------

    def show_buffers(self):
        window = self.get_active_window()
        current = window.active_tab if isinstance(window, EditorWindow) else None
        BufferPicker(self.editor_tabs(), current).present(window)

    def show_preferences(self):
        dialog = Adw.PreferencesDialog(title="Preferences")
        page = Adw.PreferencesPage()
        editor_group = Adw.PreferencesGroup(title="Editor")
        indent_row = Adw.SwitchRow(
            title="Org Indent Mode",
            subtitle="Visually indent sections and hide extra heading stars without changing the file.",
            active=self.org_indent_enabled)
        indent_row.connect("notify::active", lambda row, *_: self.set_org_indent(row.get_active()))
        editor_group.add(indent_row)
        page.add(editor_group)
        group = Adw.PreferencesGroup(
            title="Agenda",
            description="All .org files in this folder and its subfolders appear in the agenda.")
        row = Adw.ActionRow(title="Agenda Folder", subtitle=short_path(self.agenda_directory()))
        choose = Gtk.Button(icon_name="folder-open-symbolic", valign=Gtk.Align.CENTER,
                            tooltip_text="Choose Folder", css_classes=["flat"])
        row.add_suffix(choose)
        row.set_activatable_widget(choose)
        group.add(row)
        page.add(group)
        dialog.add(page)

        def on_chosen(file_dialog, res):
            try:
                folder = file_dialog.select_folder_finish(res)
            except GLib.Error:
                return
            path = folder.get_path() if folder else None
            if not path:
                return
            if self.settings:
                self.settings.set_string("agenda-directory", short_path(path))
            row.set_subtitle(short_path(path))
            if self.agenda is not None:
                self.agenda.refresh(keep_scroll=False)

        def on_choose(_button):
            file_dialog = Gtk.FileDialog(title="Choose Agenda Folder")
            if os.path.isdir(self.agenda_directory()):
                file_dialog.set_initial_folder(Gio.File.new_for_path(self.agenda_directory()))
            file_dialog.select_folder(dialog.get_root(), None, on_chosen)

        choose.connect("clicked", on_choose)
        dialog.present(self.get_active_window())

    def set_org_indent(self, enabled):
        self.org_indent_enabled = enabled
        if self.settings:
            self.settings.set_boolean("org-indent-mode", enabled)
        for window in self.editor_tabs():
            window.org_indent.set_enabled(enabled)

    def _on_about(self, *_):
        about = Adw.AboutDialog(
            application_name=APP_NAME,
            application_icon="accessories-text-editor",
            version=VERSION,
            comments="An Org mode editor with Vim keybindings",
            license_type=Gtk.License.GPL_3_0,
        )
        about.present(self.get_active_window())

    @staticmethod
    def _load_settings():
        """Use the installed schema, or the one compiled next to the sources when run uncompiled."""
        default = Gio.SettingsSchemaSource.get_default()
        schema = default.lookup(APP_ID, True) if default else None
        compiled = data_path("gschemas.compiled")
        if schema is None and compiled:
            source = Gio.SettingsSchemaSource.new_from_directory(os.path.dirname(compiled), default, False)
            schema = source.lookup(APP_ID, False)
        if schema is None:
            print(f"{APP_NAME}: settings schema {APP_ID} not found, preferences won't be saved "
                  "(run `make schemas` or `make install`)", file=sys.stderr)
            return None
        return Gio.Settings.new_full(schema, None, None)

    def setup_scroll_zoom(self, widget):
        controller = Gtk.EventControllerScroll(
            flags=Gtk.EventControllerScrollFlags.VERTICAL | Gtk.EventControllerScrollFlags.DISCRETE,
            propagation_phase=Gtk.PropagationPhase.CAPTURE)
        controller.connect("scroll", self._on_zoom_scroll)
        widget.add_controller(controller)

    def _on_zoom_scroll(self, controller, _dx, dy):
        if not controller.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK or not dy:
            return False
        self.set_zoom(self.zoom + (ZOOM_STEP if dy < 0 else -ZOOM_STEP))
        return True

    def set_zoom(self, zoom, notify=True):
        zoom = max(ZOOM_MIN, min(ZOOM_MAX, zoom))
        if zoom == self.zoom:
            return
        self.zoom = zoom
        font_size = BASE_FONT_SIZE * zoom / 100
        self.zoom_css.load_from_string(
            f'textview.organizer-editor {{ font-family: "Iosevka", monospace; '
            f"font-size: {font_size:g}pt; }}")
        for window in self.editor_tabs():
            window.org_indent.queue_refresh()
        self.lookup_action("zoom-out").set_enabled(zoom > ZOOM_MIN)
        self.lookup_action("zoom-in").set_enabled(zoom < ZOOM_MAX)
        if self.settings and self.settings.get_int("zoom") != zoom:
            self.settings.set_int("zoom", zoom)
        win = self.get_active_window()
        if notify and isinstance(win, EditorWindow) and win.active_tab:
            win.active_tab.show_zoom(zoom)

    def _on_quit(self, *_):
        if self._quitting:
            return
        self._quitting = True
        tabs = iter(self.editor_tabs())

        def check_next():
            tab = next(tabs, None)
            if tab is None:
                if self.settings:
                    windows = self.editor_windows()
                    if windows:
                        width, height = windows[0].get_default_size()
                        self.settings.set_int("window-width", width)
                        self.settings.set_int("window-height", height)
                        self.settings.set_boolean("window-maximized", windows[0].is_maximized())
                self.save_session()
                for window in list(self.get_windows()):
                    window.destroy()
                self.agenda = None
                self._quitting = False
                self.quit()
                return
            tab.present()

            def finish(confirmed):
                if confirmed:
                    check_next()
                else:
                    self._quitting = False
            tab.request_close(finish)

        check_next()
