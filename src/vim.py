#!/usr/bin/env python3
import datetime as dt
from dataclasses import replace
import os
import sys
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GtkSource", "5")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Graphene, Gtk, GtkSource, Pango  # noqa: E402

import org  # noqa: E402
from agenda import AgendaView, show_zoom_toast  # noqa: E402
from datepicker import DatePopover  # noqa: E402
from keybindings import OrgKeySequences  # noqa: E402
from indent import OrgFold, OrgIndent, OrgLineNumbers, OrgStartupEdit, OrgTagAlign, OrgTodoInteraction  # noqa: E402
from bufferpicker import BufferPicker  # noqa: E402
from customagenda import AgendaChooser, AgendaEditor, decode_views, encode_views  # noqa: E402
from utilitypane import UtilityPane, file_color, file_order_key, org_filename  # noqa: E402

APP_ID = "io.github.kllmanu.Gnorg"
LEGACY_APP_ID = "io.github.kllmanu.Organizer"
APP_NAME = "Gnorg"
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
        self._active_tag_popover = None
        self._active_startup_popover = None
        self._file_monitor = None
        self._revert_timeout = 0
        self._revert_generation = 0
        self._disk_contents = None
        self._saving = 0
        self._external_change_warned = False
        self._escape_timeout = 0
        self._escape_started = 0
        self._escape_mark = None
        self._escape_prefix = ""
        self.connect("destroy", lambda *_: self.stop_auto_revert())
        self.connect("destroy", lambda *_: self._flush_escape_prefix())

        self._build_editor()
        self._build_ui()
        self._setup_vim()
        self._setup_actions()
        self._org_key_sequences = OrgKeySequences(self, {
            "C-c C-t": self.cycle_todo,
            "C-c C-c": self.confirm_org_item,
            "C-c C-s": lambda: self.edit_planning("SCHEDULED"),
            "C-c C-d": lambda: self.edit_planning("DEADLINE"),
            "C-c c": app.show_agenda,
            "C-c a": lambda: app.choose_agenda(self.window),
            "C-c f": self.window.show_files,
            "C-c period": self.insert_timestamp,
            "C-x C-c": lambda: app.activate_action("quit", None),
            "C-x C-f": self.open_dialog,
            "C-x C-s": self.save,
            "C-x b": app.show_buffers,
            "C-g": self._keyboard_quit,
            "M-Return": self.insert_item,
        })
        self._setup_style()
        self.org_indent = OrgIndent(self.view, app.org_indent_enabled)
        self.org_fold = OrgFold(self.view)
        self.org_line_numbers = OrgLineNumbers(self.view)
        self.org_tag_align = OrgTagAlign(self.view, self._edit_tags_at_line)
        self.org_startup_edit = OrgStartupEdit(self.view, self.edit_startup)
        self.org_todo_interaction = OrgTodoInteraction(
            self.view, self._cycle_todo_at_line, self._toggle_checkbox_at_line,
            self.org_fold.toggle, self._edit_timestamp_at_offset)
        self.vim.connect("notify::command-bar-text", lambda *_: self.org_fold.track_visual_cursor(
            self.vim.get_command_bar_text() in (
                GLib.dgettext("gtksourceview-5", "-- VISUAL LINE --"),
                GLib.dgettext("gtksourceview-5", "-- VISUAL --"))))

        self.buffer.connect("modified-changed", lambda *_: self._update_title())
        self.buffer.connect("notify::cursor-position", lambda *_: self._update_position())
        self.buffer.connect("changed", lambda *_: app.queue_agenda_refresh())
        self._progress_idle = 0
        self._updating_progress = False
        self._progress_undo = False
        self._progress_done_tag = self.buffer.create_tag("org-progress-done")
        self.buffer.connect("notify::style-scheme", self._queue_progress)
        self.buffer.connect("changed", self._queue_progress)
        self.buffer.connect("end-user-action", self._update_progress)
        for signal in ("undo", "redo"):
            self.buffer.connect(signal, self._before_progress_undo)
            self.buffer.connect_after(signal, self._after_progress_undo)
        self.connect("destroy", self._cancel_progress)
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
            css_classes=["gnorg-editor"],
            show_line_numbers=self.get_application().show_line_numbers,
            highlight_current_line=True,
            auto_indent=True,
            smart_backspace=True,
            tab_width=4,
            indent_width=4,
            insert_spaces_instead_of_tabs=True,
            top_margin=self.get_application().editor_padding,
            bottom_margin=self.get_application().editor_padding,
            left_margin=self.get_application().editor_padding,
            right_margin=self.get_application().editor_padding,
            wrap_mode=Gtk.WrapMode.NONE,
        )

    def _build_ui(self):
        self.title_widget = Adw.WindowTitle()
        header = Adw.HeaderBar(title_widget=self.title_widget)
        self.header = header
        self.window.add_sidebar_toggle(header)

        menu = Gio.Menu()
        file_section = Gio.Menu()
        file_section.append("Save As…", "win.save-as")
        file_section.append("Open in Emacs", "win.open-in-emacs")
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
        self.window.show_files()
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
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", lambda *_: self._flush_escape_prefix())
        self.view.add_controller(focus)

    def _flush_escape_prefix(self):
        if self._escape_timeout:
            GLib.source_remove(self._escape_timeout)
            self._escape_timeout = 0
        if self._escape_mark:
            mark = self._escape_mark
            self._escape_mark = None
            iterator = self.buffer.get_iter_at_mark(mark)
            self.buffer.insert(iterator, self._escape_prefix)
            self._escape_prefix = ""
            self.buffer.delete_mark(mark)
        return GLib.SOURCE_REMOVE

    def _on_escape_sequence(self, keyval, state):
        app = self.get_application()
        insert_mode = self.vim.get_command_bar_text() == GLib.dgettext(
            "gtksourceview-5", "-- INSERT --")
        modifiers = state & Gtk.accelerator_get_default_mod_mask()
        codepoint = Gdk.keyval_to_unicode(keyval)
        character = chr(codepoint) if codepoint else ""
        eligible = (app.jj_escape and self.vim_enabled and insert_mode
                    and not modifiers & ~Gdk.ModifierType.SHIFT_MASK)
        if self._escape_mark:
            if (eligible and character == app.escape_sequence[1]
                    and time.monotonic() - self._escape_started <= app.jj_escape_delay / 1000):
                GLib.source_remove(self._escape_timeout)
                self._escape_timeout = 0
                self.buffer.delete_mark(self._escape_mark)
                self._escape_mark = None
                self._escape_prefix = ""
                self.vim.reset()
                return True
            self._flush_escape_prefix()
        if not eligible or character != app.escape_sequence[0]:
            return False
        self._escape_prefix = character
        self._escape_started = time.monotonic()
        self._escape_mark = self.buffer.create_mark(
            None, self.buffer.get_iter_at_mark(self.buffer.get_insert()), True)
        self._escape_timeout = GLib.timeout_add(app.jj_escape_delay, self._flush_escape_prefix)
        return True

    def _set_vim_enabled(self, enabled):
        self._flush_escape_prefix()
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
        add("open-in-emacs", self.open_in_emacs)
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
        scheme = schemes.get_scheme("gnorg-dark" if dark else "gnorg") or \
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
        if self.window.active_view is self:
            self.window.set_title(f"{name} – {APP_NAME}")
            app = self.get_application()
            if self.file is not None and not app._quitting:
                app._last_selected_file = self.file.get_uri()
        self.get_application().queue_agenda_refresh()

    def _update_position(self):
        it = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        self.position_label.set_label(f"{it.get_line() + 1}:{self.view.get_visual_column(it) + 1}")

    def toast(self, message):
        self.toasts.add_toast(Adw.Toast(title=message, timeout=2))

    def show_zoom(self, zoom):
        show_zoom_toast(self, zoom)

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
        self.org_fold.reveal(line)
        ok, it = self.buffer.get_iter_at_line(line)
        if not ok:
            it = self.buffer.get_end_iter()
        self.buffer.place_cursor(it)
        self.view.grab_focus()
        # Scroll once the (possibly freshly loaded) text has been laid out
        GLib.idle_add(lambda: self.view.scroll_to_mark(self.buffer.get_insert(), 0.1, True, 0, 0.3) and False)

    # ---------- Org commands ----------

    def move_or_relevel_heading(self, direction, move=False):
        lines, heading = self._current_heading()
        if heading is None:
            return
        cursor = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        relative, column = cursor.get_line() - heading, cursor.get_line_offset()
        if move:
            result = org.move_heading(lines, heading, direction)
            if result is None:
                self.toast("No sibling heading in that direction")
                return
            edit, target = result
        else:
            edit = org.relevel_heading(lines, heading, direction)
            if edit is None:
                self.toast("No preceding sibling to demote under" if direction > 0
                           else "Cannot promote a top-level heading")
                return
            target = heading
            if org.parse_heading(lines[cursor.get_line()]):
                column = max(0, column + direction)
        fold_states = [(self.buffer.get_iter_at_mark(mark).get_line(), state)
                       for mark, state in self.org_fold.states]
        if move:
            depth = org.parse_heading(lines[heading])[0]
            subtree_end = next((index for index, level in org._structural_headings(lines)
                                if index > heading and level <= depth), len(lines))
            if subtree_end == len(lines) and lines[-1] == "":
                subtree_end -= 1

            def moved_line(line):
                if heading <= line < subtree_end:
                    return line + target - heading
                if edit[0] <= line < edit[1]:
                    return line + (subtree_end - heading) * (-1 if direction > 0 else 1)
                return line

            fold_states = [(moved_line(line), state) for line, state in fold_states]
        self.apply_edit(edit)
        self.org_fold._clear_states()
        for line, state in fold_states:
            mark = self.buffer.create_mark(None, self.buffer.get_iter_at_line(line)[1], True)
            self.org_fold.states.append((mark, state))
        self.org_fold.refresh()
        iterator = self.buffer.get_iter_at_line(target + relative)[1]
        if iterator.has_tag(self.org_fold.hidden):
            self.org_fold.reveal(target + relative)
            iterator = self.buffer.get_iter_at_line(target + relative)[1]
        end = iterator.copy()
        if not end.ends_line():
            end.forward_to_line_end()
        iterator.set_line_offset(min(column, end.get_line_offset()))
        self.buffer.place_cursor(iterator)
        self.view.scroll_to_mark(self.buffer.get_insert(), 0.1, False, 0, 0)

    def _cancel_progress(self, *_):
        if self._progress_idle:
            GLib.source_remove(self._progress_idle)
            self._progress_idle = 0

    def _before_progress_undo(self, *_):
        self._progress_undo = True
        self._cancel_progress()

    def _after_progress_undo(self, *_):
        self._progress_undo = False
        self._highlight_progress()

    def _queue_progress(self, *_):
        if not self._updating_progress and not self._progress_undo and not self._progress_idle:
            self._progress_idle = GLib.idle_add(self._update_progress)

    def _update_progress(self, *_):
        self._cancel_progress()
        if self._updating_progress or self._progress_undo:
            return GLib.SOURCE_REMOVE
        updates = org.progress_updates(self.get_text().split("\n"))
        if updates:
            self._updating_progress = True
            self.buffer.begin_user_action()
            for line, text in reversed(updates):
                self.apply_edit((line, line + 1, [text]))
            self.buffer.end_user_action()
            self._updating_progress = False
        self._highlight_progress()
        return GLib.SOURCE_REMOVE

    def _highlight_progress(self):
        buf = self.buffer
        buf.remove_tag(self._progress_done_tag, *buf.get_bounds())
        scheme = buf.get_style_scheme()
        style = scheme.get_style("org:done") if scheme else None
        if style is None:
            return
        self._progress_done_tag.props.foreground = style.props.foreground
        self._progress_done_tag.props.weight = Pango.Weight.BOLD
        self._progress_done_tag.set_priority(buf.get_tag_table().get_size() - 1)
        for match in org.PROGRESS_RE.finditer(self.get_text()):
            value = match[0][1:-1]
            complete = value == "100%"
            if "/" in value:
                done, total = value.split("/")
                complete = bool(done and total and int(total) > 0 and int(done) == int(total))
            if complete:
                start = buf.get_iter_at_offset(match.start())
                if not any(buf.iter_has_context_class(start, name)
                           for name in ("comment", "no-spell-check")):
                    buf.apply_tag(self._progress_done_tag, start,
                                  buf.get_iter_at_offset(match.end()))

    def confirm_org_item(self):
        line = self._cursor_line()
        text = self.get_text().split("\n")[line]
        if org.STARTUP_RE.match(text):
            self.edit_startup(line, self.org_startup_edit.buttons.get(line))
        elif org.parse_heading(text) is not None:
            self.edit_tags()
        else:
            self.toggle_checkbox()

    def edit_startup(self, line, button):
        if self._active_startup_popover is not None:
            self._active_startup_popover.popdown()
            return
        original = self.get_text().split("\n")[line]
        match = org.STARTUP_RE.match(original)
        if match is None:
            self.toast("The STARTUP option has changed")
            return
        modes = [word.lower() for word in match[2].split()
                 if word.lower() in org.STARTUP_MODES]
        dropdown = Gtk.DropDown.new_from_strings(list(org.STARTUP_MODES))
        dropdown.set_selected(org.STARTUP_MODES.index(modes[-1] if modes else "showall"))
        dropdown.update_property([Gtk.AccessibleProperty.LABEL], ["Startup folding mode"])
        popover = Gtk.Popover(position=Gtk.PositionType.BOTTOM)
        self._active_startup_popover = popover
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                      margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        box.append(Gtk.Label(label="Startup Folding", xalign=0, css_classes=["heading"]))
        box.append(dropdown)
        buttons = Gtk.Box(spacing=6, halign=Gtk.Align.END)
        cancel = Gtk.Button(label="_Cancel", use_underline=True)
        cancel.connect("clicked", lambda *_: popover.popdown())
        apply = Gtk.Button(label="_Apply", use_underline=True,
                           css_classes=["suggested-action"])
        buttons.append(cancel)
        buttons.append(apply)
        box.append(buttons)
        popover.set_child(box)
        popover.set_parent(self.editor_box)
        anchor = self._cursor_rect()
        if button is not None:
            _ok, bounds = button.compute_bounds(self.editor_box)
            anchor.x, anchor.y = int(bounds.get_x()), int(bounds.get_y())
            anchor.width, anchor.height = max(button.get_width(), 1), max(button.get_height(), 1)
        popover.set_pointing_to(anchor)
        mark = self.buffer.create_mark(None, self.buffer.get_iter_at_line(line)[1], True)

        def on_apply(*_):
            idx = self.buffer.get_iter_at_mark(mark).get_line()
            current = self.get_text().split("\n")[idx]
            if current != original:
                self.toast("The STARTUP option has changed. Open the editor again.")
                popover.popdown()
                return
            updated = org.set_startup_mode(
                current, org.STARTUP_MODES[dropdown.get_selected()])
            if updated != current:
                self.apply_edit((idx, idx + 1, [updated]))
            popover.popdown()

        def on_closed(p):
            self.buffer.delete_mark(mark)
            self._active_startup_popover = None
            self.view.grab_focus()
            GLib.idle_add(lambda: p.unparent() and False)

        apply.connect("clicked", on_apply)
        popover.connect("closed", on_closed)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", lambda _c, key, _code, state:
                     (popover.popdown() or True) if key == Gdk.KEY_Escape or (
                         key == Gdk.KEY_g and state & Gdk.ModifierType.CONTROL_MASK) else False)
        popover.add_controller(keys)
        popover.popup()
        dropdown.grab_focus()

    def _edit_tags_at_line(self, line):
        self.buffer.place_cursor(self.buffer.get_iter_at_line(line)[1])
        self.edit_tags()

    def edit_tags(self):
        if self._active_tag_popover is not None:
            self._active_tag_popover.popdown()
            return
        line = self._cursor_line()
        heading = self.get_text().split("\n")[line]
        parsed = org.parse_heading(heading)
        if parsed is None:
            self.toast("Not on an Org heading")
            return
        mark = self.buffer.create_mark(None, self.buffer.get_iter_at_line(line)[1], True)
        popover = Gtk.Popover(has_arrow=True, position=Gtk.PositionType.BOTTOM)
        self._active_tag_popover = popover
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                      margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        box.append(Gtk.Label(label="Heading Tags", xalign=0, css_classes=["heading"]))
        entry = Gtk.Entry(text=":".join(parsed[4]), placeholder_text="work:personal",
                          width_chars=34)
        entry.update_property([Gtk.AccessibleProperty.LABEL], ["Heading tags"])
        box.append(entry)
        hint = Gtk.Label(label="Separate tags with colons or spaces. Leave empty to remove.",
                         xalign=0, wrap=True, max_width_chars=40,
                         css_classes=["caption", "dim-label"])
        box.append(hint)
        buttons = Gtk.Box(spacing=6, halign=Gtk.Align.END)
        cancel = Gtk.Button(label="_Cancel", use_underline=True)
        cancel.connect("clicked", lambda *_: popover.popdown())
        buttons.append(cancel)
        apply = Gtk.Button(label="_Apply", use_underline=True,
                           css_classes=["suggested-action"])
        buttons.append(apply)
        box.append(buttons)
        popover.set_child(box)
        popover.set_parent(self.editor_box)
        anchor = self._cursor_rect()
        if match := org.TAGS_RE.search(heading.rstrip()):
            iterator = self.buffer.get_iter_at_line_offset(line, match.start(1))[1]
            location = self.view.get_iter_location(iterator)
            x, y = self.view.buffer_to_window_coords(
                Gtk.TextWindowType.WIDGET, location.x, location.y)
            _ok, point = self.view.compute_point(
                self.editor_box, Graphene.Point().init(x, y))
            anchor.x, anchor.y = int(point.x), int(point.y)
            anchor.width, anchor.height = max(location.width, 1), location.height
        popover.set_pointing_to(anchor)

        def validate(*_):
            try:
                org.parse_tags(entry.get_text())
            except ValueError as error:
                hint.set_label(str(error))
                hint.set_css_classes(["caption", "error"])
                apply.set_sensitive(False)
            else:
                hint.set_label("Separate tags with colons or spaces. Leave empty to remove.")
                hint.set_css_classes(["caption", "dim-label"])
                apply.set_sensitive(True)

        def on_apply(*_):
            if not apply.get_sensitive():
                return
            idx = self.buffer.get_iter_at_mark(mark).get_line()
            current = self.get_text().split("\n")[idx]
            if current != heading:
                self.toast("The heading has changed. Open the tag editor again.")
                popover.popdown()
                return
            updated = org.set_heading_tags(current, org.parse_tags(entry.get_text()))
            if updated != current:
                self.apply_edit((idx, idx + 1, [updated]))
            popover.popdown()

        def on_closed(p):
            self.buffer.delete_mark(mark)
            if self._active_tag_popover is p:
                self._active_tag_popover = None
            self.view.grab_focus()
            GLib.idle_add(lambda: p.unparent() and False)

        def on_key(_controller, keyval, _keycode, state):
            if keyval == Gdk.KEY_Escape or (
                    keyval == Gdk.KEY_g and state & Gdk.ModifierType.CONTROL_MASK):
                popover.popdown()
                return True
            return False

        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", on_key)
        popover.add_controller(keys)
        entry.connect("changed", validate)
        entry.connect("activate", on_apply)
        apply.connect("clicked", on_apply)
        popover.connect("closed", on_closed)
        popover.popup()
        entry.grab_focus()
        entry.select_region(0, -1)

    def _toggle_checkbox_at_line(self, line):
        self.buffer.place_cursor(self.buffer.get_iter_at_line(line)[1])
        self.toggle_checkbox()

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

    def _cycle_todo_at_line(self, line):
        self.buffer.place_cursor(self.buffer.get_iter_at_line(line)[1])
        self.cycle_todo()

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

    def _edit_timestamp_at_offset(self, offset):
        iterator = self.buffer.get_iter_at_offset(offset)
        self.buffer.place_cursor(iterator)
        line = iterator.get_line()
        lines = self.get_text().split("\n")
        heading = org.find_heading(lines, line)
        if heading is not None and line == heading + 1 and org.parse_planning(lines[line]):
            column = iterator.get_line_offset()
            for match in org.PLANNING_ITEM_RE.finditer(lines[line]):
                if (match[1] in ("SCHEDULED", "DEADLINE")
                        and match.start(2) <= column < match.end(2)):
                    self.edit_planning(match[1])
                    return
        self.insert_timestamp()

    def insert_timestamp(self):
        if self._active_date_popover is not None:
            self._active_date_popover.popdown()
            return
        buf = self.buffer
        cursor = buf.get_iter_at_mark(buf.get_insert())
        start = end = cursor.get_offset()
        initial = None
        for match in org.SINGLE_TS_RE.finditer(self.get_text()):
            if match.start() <= start < match.end():
                initial = org.Timestamp.parse(match.group(0))
                if initial is not None:
                    start, end = match.span()
                break
        original = buf.get_text(buf.get_iter_at_offset(start), buf.get_iter_at_offset(end), True)
        start_mark = buf.create_mark(None, buf.get_iter_at_offset(start), True)
        end_mark = buf.create_mark(None, buf.get_iter_at_offset(end), False)
        popover = DatePopover("Timestamp", initial, allow_remove=False)
        self._active_date_popover = popover
        popover.set_parent(self.editor_box)
        popover.set_pointing_to(self._cursor_rect())

        def on_apply(_popover, timestamp):
            first = buf.get_iter_at_mark(start_mark)
            last = buf.get_iter_at_mark(end_mark)
            if buf.get_text(first, last, True) != original:
                self.toast("The text has changed. Insert the timestamp again.")
                return
            buf.begin_user_action()
            buf.delete(first, last)
            if initial is not None:
                timestamp = replace(timestamp, active=initial.active)
            buf.insert(buf.get_iter_at_mark(start_mark), timestamp.format())
            buf.place_cursor(buf.get_iter_at_mark(end_mark))
            buf.end_user_action()
            self.view.scroll_to_mark(buf.get_insert(), 0.1, False, 0, 0)

        def on_closed(p):
            buf.delete_mark(start_mark)
            buf.delete_mark(end_mark)
            if self._active_date_popover is p:
                self._active_date_popover = None
            self.view.grab_focus()
            GLib.idle_add(lambda: p.unparent() and False)

        popover.connect("apply", on_apply)
        popover.connect("closed", on_closed)
        popover.popup()

    def shift_timestamp(self, direction):
        buf = self.buffer
        cursor = buf.get_iter_at_mark(buf.get_insert())
        offset = cursor.get_offset()
        try:
            edit = org.shift_timestamp(self.get_text(), offset, direction)
        except OverflowError:
            self.toast("The date cannot be shifted beyond the supported year range.")
            return True
        if edit is None:
            return False
        start, end, text = edit
        buf.begin_user_action()
        buf.delete(buf.get_iter_at_offset(start), buf.get_iter_at_offset(end))
        buf.insert(buf.get_iter_at_offset(start), text)
        buf.place_cursor(buf.get_iter_at_offset(start + min(offset - start, len(text) - 1)))
        buf.end_user_action()
        return True

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
        if self._active_startup_popover is not None:
            self._active_startup_popover.popdown()
        if self._active_tag_popover is not None:
            self._active_tag_popover.popdown()
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
        app = self.get_application()

        def on_loaded(f, res):
            if self.get_application() is not app:
                return
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
        self._flush_escape_prefix()
        self.file = file
        self.get_application().remember_file(file)
        self.buffer.begin_irreversible_action()
        self.buffer.set_text(text)
        self.buffer.end_irreversible_action()
        self.org_fold.reset()
        self.buffer.place_cursor(self.buffer.get_start_iter())
        self.buffer.set_modified(False)
        self._disk_contents = raw if raw is not None else text.encode("utf-8")
        self._external_change_warned = False
        self.setup_auto_revert()
        self._update_language(raw)
        self._update_title()
        self.view.grab_focus()

    def stop_auto_revert(self):
        self._revert_generation += 1
        if self._revert_timeout:
            GLib.source_remove(self._revert_timeout)
            self._revert_timeout = 0
        if self._file_monitor:
            self._file_monitor.cancel()
            self._file_monitor = None

    def setup_auto_revert(self, check=False):
        self.stop_auto_revert()
        if self.file is None or not self.get_application().auto_revert:
            return
        parent = self.file.get_parent()
        try:
            self._file_monitor = (
                parent.monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None) if parent
                else self.file.monitor_file(Gio.FileMonitorFlags.WATCH_MOVES, None))
        except GLib.Error as error:
            self.toast(f"Could not watch file for changes: {error.message}")
            return
        self._file_monitor.connect("changed", self._on_disk_changed)
        if check:
            self._queue_auto_revert()

    def _on_disk_changed(self, _monitor, file, other_file, _event):
        if self.file and (file.equal(self.file) or other_file and other_file.equal(self.file)):
            self._queue_auto_revert()

    def _queue_auto_revert(self):
        if self._revert_timeout:
            GLib.source_remove(self._revert_timeout)
        self._revert_timeout = GLib.timeout_add(200, self._reload_from_disk)

    def _reload_from_disk(self):
        self._revert_timeout = 0
        if not self._file_monitor or self._saving:
            return GLib.SOURCE_REMOVE
        self._revert_generation += 1
        generation = self._revert_generation
        file = self.file

        def loaded(source, result):
            try:
                _ok, contents, _etag = source.load_contents_finish(result)
            except GLib.Error as error:
                if generation == self._revert_generation and not self._external_change_warned:
                    self._external_change_warned = True
                    self.toast(f"Could not reload file: {error.message}")
                return
            if generation != self._revert_generation or self._saving or not source.equal(self.file):
                return
            if contents == self._disk_contents:
                return
            self._flush_escape_prefix()
            if self.buffer.get_modified():
                if not self._external_change_warned:
                    self.toast("File changed on disk. Your unsaved edits have been kept.")
                    self._external_change_warned = True
                return
            text = contents.decode("utf-8", errors="replace")
            cursor = self.buffer.get_iter_at_mark(self.buffer.get_insert())
            line, offset = cursor.get_line(), cursor.get_line_offset()
            if text != self.get_text():
                self.buffer.begin_irreversible_action()
                self.buffer.set_text(text)
                self.buffer.end_irreversible_action()
                self.org_fold.reset()
                line = min(line, self.buffer.get_line_count() - 1)
                iterator = self.buffer.get_iter_at_line(line)[1]
                end = iterator.copy()
                end.forward_to_line_end()
                iterator.forward_chars(min(offset, end.get_offset() - iterator.get_offset()))
                self.buffer.place_cursor(iterator)
                self.buffer.set_modified(False)
                self._update_language(contents)
            self._disk_contents = contents
            self._external_change_warned = False
            self.get_application().queue_agenda_refresh()

        file.load_contents_async(None, loaded)
        return GLib.SOURCE_REMOVE

    def _update_language(self, raw=None):
        name = self.file.get_basename()
        content_type, _uncertain = Gio.content_type_guess(name, raw)
        manager = GtkSource.LanguageManager.get_default()
        # Org is the default for anything that isn't recognised as another language
        language = manager.guess_language(name, content_type) or manager.get_language("org")
        self.buffer.set_language(language)

    def open_in_emacs(self):
        self._flush_escape_prefix()
        if self.file is not None and self.file.get_path() is None:
            self.toast("Open in Emacs requires a local file")
            return
        if not GLib.find_program_in_path("emacsclient"):
            self.toast("Could not open in Emacs: emacsclient is not installed")
            return
        if self.file is None or self.buffer.get_modified():
            dialog = Adw.AlertDialog(
                heading="Save Before Opening in Emacs?",
                body="Save this document so Emacs opens the latest changes.",
                close_response="cancel", default_response="save")
            dialog.add_response("cancel", "_Cancel")
            dialog.add_response("save", "_Save and Open")
            dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
            dialog.connect("response", lambda _dialog, response:
                           self.save(then=self.open_in_emacs) if response == "save" else None)
            dialog.present(self.window)
            return
        cursor = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        argv = ["emacsclient", "--no-wait", "--create-frame", "--alternate-editor=emacs",
                f"+{cursor.get_line() + 1}:{cursor.get_line_offset() + 1}", self.file.get_path()]
        try:
            process = Gio.Subprocess.new(
                argv, Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE)
        except GLib.Error as error:
            self.toast(f"Could not open in Emacs: {error.message}")
            return

        def finished(source, result):
            try:
                _success, _stdout, stderr = source.communicate_utf8_finish(result)
            except GLib.Error as error:
                self.toast(f"Could not open in Emacs: {error.message}")
                return
            if not source.get_successful():
                detail = (stderr or "").strip() or "emacsclient exited unsuccessfully"
                self.toast(f"Could not open in Emacs: {detail}")

        process.communicate_utf8_async(None, None, finished)

    def save(self, then=None, on_cancel=None, notify=None):
        if self.file is None:
            self.save_as(then=then, on_cancel=on_cancel, notify=notify)
        else:
            self.save_to(self.file, then=then, on_cancel=on_cancel, notify=notify)

    def save_as(self, then=None, on_cancel=None, notify=None):
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
            self.save_to(file, then=then, on_cancel=on_cancel, notify=notify)

        dialog.save(self.window, None, on_done)

    def save_to(self, file, then=None, on_cancel=None, notify=None):
        notify = notify if notify is not None else self.toast
        self._flush_escape_prefix()
        self._update_progress()
        if self.file is None or not self.file.equal(file):
            parent = file.get_parent()
            if parent:
                destination = parent.get_child(org_filename(file.get_basename()))
                if not destination.equal(file) and destination.query_exists(None):
                    notify(f"“{destination.get_basename()}” already exists. "
                               "Choose that file explicitly to replace it.")
                    if on_cancel:
                        on_cancel()
                    return
                file = destination
        start, end = self.buffer.get_bounds()
        text = self.buffer.get_text(start, end, True)
        saved_text = text
        if text and not text.endswith("\n"):
            text += "\n"
        data = text.encode("utf-8")
        self._saving += 1
        self._revert_generation += 1

        def on_saved(f, res):
            self._saving -= 1
            try:
                f.replace_contents_finish(res)
            except GLib.Error as e:
                notify(f"Could not save file: {e.message}")
                if on_cancel:
                    on_cancel()
                if self._file_monitor:
                    self._queue_auto_revert()
                return
            if self.file is None or not self.file.equal(f):
                self.file = f
                self._update_language()
            self.buffer.set_modified(self.get_text() != saved_text)
            self._disk_contents = data
            self._external_change_warned = False
            self.setup_auto_revert(check=True)
            self.get_application().remember_file(f)
            self._update_title()
            notify(f"“{f.get_basename()}” written, {len(data)} bytes")
            self.get_application().queue_agenda_refresh()
            if then:
                then()

        file.replace_contents_async(data, None, False, Gio.FileCreateFlags.NONE, None, on_saved)

    # ---------- Closing ----------

    def force_close(self):
        self._force_close = True
        self.close()

    def request_close(self, finish):
        self._flush_escape_prefix()
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
        self.agenda = None
        self.weekly_agenda = None
        self.tabs = Adw.TabView()
        self.tabs.set_vexpand(True)
        self.stack = Adw.ViewStack()
        self.stack.add_titled_with_icon(self.tabs, "files", "Files", "text-x-generic-symbolic")
        self.weekly_agenda_container = Adw.Bin()
        self.stack.add_titled_with_icon(
            self.weekly_agenda_container, "weekly-agenda", "Agenda", "view-list-symbolic")
        self.agenda_container = Adw.Bin()
        self.stack.add_titled_with_icon(
            self.agenda_container, "agenda", "Calendar", "x-office-calendar-symbolic")
        self.view_switcher = Adw.ViewSwitcherTitle(stack=self.stack, title=APP_NAME)
        self.switcher_bar = Adw.ViewSwitcherBar(stack=self.stack)
        self.view_switcher.bind_property(
            "title-visible", self.switcher_bar, "reveal", GObject.BindingFlags.SYNC_CREATE)
        self.toolbar_view = Adw.ToolbarView(content=self.stack)
        self.toolbar_view.add_bottom_bar(self.switcher_bar)
        self._header = None
        self._title_bindings = []
        self.split_view = Adw.OverlaySplitView(
            content=self.toolbar_view, show_sidebar=True,
            min_sidebar_width=280, max_sidebar_width=320, sidebar_width_fraction=0.3)
        self.utility_pane = UtilityPane(self)
        self.split_view.set_sidebar(self.utility_pane)
        self.set_content(self.split_view)
        breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 850px"))
        breakpoint.add_setter(self.split_view, "collapsed", True)
        breakpoint.add_setter(self.split_view, "show-sidebar", False)
        self.add_breakpoint(breakpoint)
        self.tabs.connect("notify::selected-page", self._on_selected)
        self.stack.connect("notify::visible-child", self._on_view_changed)
        self.tabs.connect("close-page", self._on_close_page)
        self.connect("close-request", self._on_close_request)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key_pressed)
        self.add_controller(keys)
        self.utility_pane.refresh()

    def add_sidebar_toggle(self, header):
        button = Gtk.ToggleButton(icon_name="sidebar-show-symbolic",
                                  tooltip_text="Toggle utility pane")
        self.split_view.bind_property(
            "show-sidebar", button, "active",
            GObject.BindingFlags.SYNC_CREATE | GObject.BindingFlags.BIDIRECTIONAL)
        header.pack_start(button)
        header.sidebar_toggle = button

    def _on_key_pressed(self, controller, keyval, keycode, state):
        if self.get_visible_dialog() is not None:
            return False
        tab = self.active_view
        if isinstance(tab, EditorTab) and self.get_focus() is tab.view:
            if tab._on_escape_sequence(keyval, state):
                return True
        modifiers = state & Gtk.accelerator_get_default_mod_mask()
        if (isinstance(tab, EditorTab) and keyval == Gdk.KEY_e
                and modifiers == (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK)):
            tab.open_in_emacs()
            return True
        if modifiers == Gdk.ModifierType.ALT_MASK:
            if (isinstance(tab, EditorTab) and self.get_focus() is tab.view
                    and keyval in (Gdk.KEY_Up, Gdk.KEY_Down, Gdk.KEY_Left, Gdk.KEY_Right)):
                tab.move_or_relevel_heading(
                    -1 if keyval in (Gdk.KEY_Up, Gdk.KEY_Left) else 1,
                    move=keyval in (Gdk.KEY_Up, Gdk.KEY_Down))
                return True
            if keyval in (Gdk.KEY_j, Gdk.KEY_k):
                self.utility_pane.navigate_files(1 if keyval == Gdk.KEY_j else -1)
                return True
            if keyval in (Gdk.KEY_h, Gdk.KEY_l):
                pages = self.stack.get_pages()
                current = next(i for i in range(pages.get_n_items())
                               if pages.get_item(i).get_child() is self.stack.get_visible_child())
                direction = -1 if keyval == Gdk.KEY_h else 1
                page = pages.get_item(max(0, min(current + direction, pages.get_n_items() - 1)))
                self.stack.set_visible_child(page.get_child())
                return True
        tab = self.active_view
        if tab is None:
            return False
        if (isinstance(tab, EditorTab) and self.get_focus() is tab.view
                and keyval in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab)
                and modifiers in (Gdk.ModifierType(0), Gdk.ModifierType.SHIFT_MASK)):
            return tab.org_fold.cycle(
                bool(modifiers & Gdk.ModifierType.SHIFT_MASK) or keyval == Gdk.KEY_ISO_Left_Tab)
        if (isinstance(tab, EditorTab) and self.get_focus() is tab.view
                and state & Gtk.accelerator_get_default_mod_mask() == Gdk.ModifierType.SHIFT_MASK
                and keyval in (Gdk.KEY_Up, Gdk.KEY_Down)):
            return tab.shift_timestamp(1 if keyval == Gdk.KEY_Up else -1)
        if tab._org_key_sequences._on_key_pressed(controller, keyval, keycode, state):
            return True
        if isinstance(tab, AgendaView):
            return tab._on_key_pressed(controller, keyval, keycode, state)
        return False

    @property
    def active_tab(self):
        page = self.tabs.get_selected_page()
        return page.get_child() if page else None

    @property
    def active_view(self):
        if self.stack.get_visible_child_name() == "agenda":
            return self.agenda
        if self.stack.get_visible_child_name() == "weekly-agenda":
            return self.weekly_agenda
        return self.active_tab

    def show_files(self):
        if self.active_tab is None:
            self.new_tab()
            return
        self.stack.set_visible_child_name("files")
        self._on_selected()

    def documents(self):
        return [self.tabs.get_nth_page(i).get_child() for i in range(self.tabs.get_n_pages())]

    def new_tab(self):
        tab = EditorTab(self)
        tab.remove_controller(tab._org_key_sequences.controller)
        tab.page = self.tabs.append(tab)
        self.tabs.set_selected_page(tab.page)
        self.show_files()
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

    def _on_view_changed(self, *_):
        if self.stack.get_visible_child_name() == "agenda":
            self.get_application().show_agenda(self)
        elif self.stack.get_visible_child_name() == "weekly-agenda":
            self.get_application().show_weekly_agenda(
                self, custom=self.weekly_agenda.custom if self.weekly_agenda else None)
        elif self.stack.get_visible_child_name() == "files" and self.active_tab is None:
            self.new_tab()
        self._on_selected()

    def _on_selected(self, *_):
        tab = self.active_view
        if tab is None:
            return
        if self._header is not tab.header:
            if self._header is not None:
                for binding in self._title_bindings:
                    binding.unbind()
                self._header.set_title_widget(None)
                self.toolbar_view.remove(self._header)
            self._header = tab.header
            self._header.set_title_widget(self.view_switcher)
            self._title_bindings = [
                tab.title_widget.bind_property(
                    name, self.view_switcher, name, GObject.BindingFlags.SYNC_CREATE)
                for name in ("title", "subtitle")]
            self.toolbar_view.add_top_bar(self._header)
        for name in self.list_actions():
            self.remove_action(name)
        for name in tab.actions.list_actions():
            self.add_action(tab.actions.lookup_action(name))
        tab._update_title()
        tab.view.grab_focus()
        self.utility_pane.refresh()

    def _on_close_page(self, _tabs, page):
        def finish(confirmed):
            if confirmed:
                tab = page.get_child()
                tab.stop_auto_revert()
                offset = tab.buffer.get_iter_at_mark(tab.buffer.get_insert()).get_offset()
                self._closed_tabs.append(
                    (tab.file, tab.get_text(), tab.buffer.get_modified(), offset))
            self.tabs.close_page_finish(page, confirmed)
            self.get_application().queue_agenda_refresh()
            if (not self.tabs.get_n_pages() and not self._closing
                    and self.stack.get_visible_child_name() == "files"):
                self._force_close = True
                self.close()
        page.get_child().request_close(finish)
        return True

    def _on_close_request(self, *_):
        if self._force_close:
            return False
        self.get_application()._on_quit()
        return True


class GnorgApplication(Adw.Application):
    def __init__(self):
        self.agenda_directory_override = os.environ.get("GNORG_AGENDA_DIRECTORY", "")
        flags = Gio.ApplicationFlags.HANDLES_OPEN
        if self.agenda_directory_override:
            flags |= Gio.ApplicationFlags.NON_UNIQUE
        super().__init__(application_id=APP_ID, flags=flags)
        self.zoom = None
        self.zoom_css = None
        self.settings = None
        self.agenda = None
        self._agenda_refresh_id = 0
        self.org_indent_enabled = True
        self.show_line_numbers = False
        self.editor_padding = 16
        self.agenda_padding = 16
        self.auto_revert = True
        self.jj_escape = True
        self.jj_escape_delay = 200
        self.escape_sequence = "jj"
        self.hide_weekends = True
        self._quitting = False
        self._session_restored = False
        self._last_selected_file = ""
        self.recent_files = []
        self.hidden_agenda_files = set()
        self.removed_files = set()
        self.file_colors = {}
        self.custom_agendas = []
        self._custom_agenda_load_error = None
        self.utility_file_order = []
        self._saved_utility_file_order = []
        self.connect("window-removed", self._on_window_removed)

    def _on_window_removed(self, _app, window):
        self.utility_file_order = [
            key for key in self.utility_file_order
            if isinstance(key, str) or key.window is not window]
        if self.agenda is not None and self.agenda.window is window:
            self.agenda = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        GLib.set_application_name(APP_NAME)

        self.settings = self._load_settings()
        if self.settings:
            try:
                self.custom_agendas = decode_views(self.settings.get_string("custom-agenda-views"))
            except ValueError as error:
                self._custom_agenda_load_error = str(error)
                print(f"{APP_NAME}: {error}", file=sys.stderr)
            self.org_indent_enabled = self.settings.get_boolean("org-indent-mode")
            self.show_line_numbers = self.settings.get_boolean("show-line-numbers")
            self.editor_padding = self.settings.get_int("editor-padding")
            self.agenda_padding = self.settings.get_int("agenda-padding")
            self.auto_revert = self.settings.get_boolean("auto-revert")
            self.jj_escape = self.settings.get_boolean("jj-escape")
            self.jj_escape_delay = self.settings.get_int("jj-escape-delay")
            sequence = self.settings.get_string("escape-sequence")
            if len(sequence) == 2 and all(char.isprintable() and not char.isspace()
                                          for char in sequence):
                self.escape_sequence = sequence
            else:
                print(f"{APP_NAME}: invalid escape sequence; using jj", file=sys.stderr)
            self.hide_weekends = self.settings.get_boolean("hide-weekends")
            self.recent_files = self.settings.get_strv("recent-files")
            self.hidden_agenda_files = set(self.settings.get_strv("hidden-agenda-files"))
            self.removed_files = set(self.settings.get_strv("removed-files"))
            self.file_colors = self.settings.get_value("file-colors").unpack()
            self.utility_file_order = self.settings.get_strv("utility-file-order")
            self._saved_utility_file_order = list(self.utility_file_order)

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
            ("weekly-agenda", lambda *_: self.show_weekly_agenda()),
            ("preferences", lambda *_: self.show_preferences()),
            ("zoom-in", lambda *_: self.set_zoom(self.zoom + ZOOM_STEP)),
            ("zoom-out", lambda *_: self.set_zoom(self.zoom - ZOOM_STEP)),
            ("zoom-reset", lambda *_: self.set_zoom(100)),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)

        weekends_action = Gio.SimpleAction.new_stateful(
            "hide-weekends", None, GLib.Variant.new_boolean(self.hide_weekends))
        weekends_action.connect(
            "change-state", lambda _action, value: self.set_hide_weekends(value.get_boolean()))
        self.add_action(weekends_action)

        self.set_accels_for_action("app.zoom-in", ["<Ctrl>plus", "<Ctrl>equal", "<Ctrl>KP_Add"])
        self.set_accels_for_action("app.zoom-out", ["<Ctrl>minus", "<Ctrl>KP_Subtract"])
        self.set_accels_for_action("app.zoom-reset", ["<Ctrl>0", "<Ctrl>KP_0"])
        self.set_zoom(self.settings.get_int("zoom") if self.settings else 100, notify=False)

        self.set_accels_for_action("app.quit", ["<Ctrl>q"])
        self.set_accels_for_action("win.open", ["<Ctrl>o"])
        self.set_accels_for_action("win.save", ["<Ctrl>s"])
        self.set_accels_for_action("win.save-as", ["<Ctrl><Shift>s"])
        self.set_accels_for_action("win.open-in-emacs", ["<Ctrl><Alt>e"])
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
            positions = self.settings.get_value("session-cursor-positions").unpack()

            def restore_cursor(tab):
                position = positions.get(tab.file.get_uri())
                if position is not None:
                    line, column = position
                    line = min(line, tab.buffer.get_line_count() - 1)
                    tab.org_fold.reveal(line)
                    iterator = tab.buffer.get_iter_at_line(line)[1]
                    end = iterator.copy()
                    if not end.ends_line():
                        end.forward_to_line_end()
                    iterator.set_line_offset(min(column, end.get_line_offset()))
                    tab.buffer.place_cursor(iterator)

                def focus_restored_editor():
                    if tab.get_root() is not None and tab.window.active_view is tab:
                        tab.view.grab_focus()
                        tab.view.scroll_to_mark(tab.buffer.get_insert(), 0.1, False, 0, 0)
                    return GLib.SOURCE_REMOVE

                GLib.idle_add(focus_restored_editor)

            selected = self.settings.get_string("session-active-file")
            for uri in self.settings.get_strv("session-files"):
                file = Gio.File.new_for_uri(uri)
                if not self.file_removed(file) and file.query_exists(None):
                    self.open_file(file, then=restore_cursor)
            if selected:
                file = Gio.File.new_for_uri(selected)
                if not self.file_removed(file) and file.query_exists(None):
                    self.open_file(file)

    def save_session(self):
        if self.settings:
            uris = [tab.file.get_uri() for tab in self.editor_tabs() if tab.file is not None]
            self.settings.set_strv("session-files", uris)
            self.settings.set_string("session-active-file", self._last_selected_file)
            positions = {}
            for tab in self.editor_tabs():
                if tab.file is not None:
                    cursor = tab.buffer.get_iter_at_mark(tab.buffer.get_insert())
                    positions[tab.file.get_uri()] = (cursor.get_line(), cursor.get_line_offset())
            self.settings.set_value(
                "session-cursor-positions", GLib.Variant("a{s(uu)}", positions))
            Gio.Settings.sync()

    def new_window(self):
        win = EditorWindow(self)
        return win

    def editor_windows(self):
        return [w for w in self.get_windows() if isinstance(w, EditorWindow)]

    def editor_tabs(self):
        return [tab for window in self.editor_windows() for tab in window.documents()
                if isinstance(tab, EditorTab)]

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
        key = file_order_key(file, None)
        if key in self.removed_files:
            self.removed_files.remove(key)
            self._save_removed_files()
        win = self.window_for_path(file.get_path()) if file.get_path() else None
        if win is None:
            for w in self.editor_tabs():
                if w.file and w.file.equal(file):
                    win = w
        if win:
            self.remember_file(file)
            win.present()
            if then:
                then(win)
            return win

        win = self.new_tab()
        win.file = file
        win.load(file, (lambda: then(win)) if then else None)
        win.present()
        return win

    def remember_file(self, file):
        uri = file.get_uri()
        self.recent_files = [uri] + [item for item in self.recent_files if item != uri][:19]
        if self.settings:
            self.settings.set_strv("recent-files", self.recent_files)

    # ---------- Org agenda ----------

    def agenda_directory(self):
        if self.agenda_directory_override:
            return os.path.abspath(os.path.expanduser(self.agenda_directory_override))
        directory = self.settings.get_string("agenda-directory") if self.settings else ""
        return os.path.abspath(os.path.expanduser(directory or DEFAULT_AGENDA_DIRECTORY))

    def agenda_entries(self):
        """Visible folder and open Org sources; unsaved buffers win over disk."""
        directory = self.agenda_directory()
        files = self.agenda_files()
        entries = []
        for path in files:
            if not self.agenda_file_visible(path):
                continue
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

    def agenda_files(self):
        directory = self.agenda_directory()
        files = org.find_org_files(directory) if os.path.isdir(directory) else []
        seen = {os.path.realpath(path) for path in files}
        for tab in self.editor_tabs():
            if tab.path and tab.path.endswith(".org") and os.path.realpath(tab.path) not in seen:
                files.append(tab.path)
                seen.add(os.path.realpath(tab.path))
        return [path for path in files if not self.file_removed(Gio.File.new_for_path(path))]

    def file_removed(self, file):
        return file_order_key(file, None) in self.removed_files

    def _save_removed_files(self):
        if self.settings:
            self.settings.set_strv("removed-files", sorted(self.removed_files))

    def calendar_file_color(self, path):
        key = file_order_key(Gio.File.new_for_path(path), None)
        return self.file_colors.get(key, file_color(path))

    def set_calendar_file_color(self, file, color):
        self.file_colors[file_order_key(file, None)] = color
        if self.settings:
            self.settings.set_value("file-colors", GLib.Variant("a{ss}", self.file_colors))
        self.queue_agenda_refresh()

    def rename_document(self, file, name, window):
        name = org_filename(name)
        old_key = file_order_key(file, None)
        documents = [tab for tab in self.editor_tabs()
                     if tab.file and file_order_key(tab.file, None) == old_key]
        old_color = self.calendar_file_color(file.get_path()) if file.get_path() else None

        def renamed(source, result):
            try:
                renamed_file = source.set_display_name_finish(result)
            except GLib.Error as error:
                window.active_view.toast(f"Could not rename file: {error.message}")
                return
            new_key = file_order_key(renamed_file, None)
            for keys in (self.hidden_agenda_files, self.removed_files):
                if old_key in keys:
                    keys.remove(old_key)
                    keys.add(new_key)
            self.file_colors.pop(old_key, None)
            if old_color:
                self.file_colors[new_key] = old_color
            self.utility_file_order = [new_key if key == old_key else key
                                       for key in self.utility_file_order]
            self.recent_files = [uri for uri in self.recent_files if uri != file.get_uri()]
            if self.settings:
                self.settings.set_strv("hidden-agenda-files", sorted(self.hidden_agenda_files))
                self._save_removed_files()
                self.settings.set_value("file-colors", GLib.Variant("a{ss}", self.file_colors))
            self.save_utility_file_order()
            self.remember_file(renamed_file)
            if self._last_selected_file == file.get_uri():
                self._last_selected_file = renamed_file.get_uri()
            for tab in documents:
                if tab in self.editor_tabs() and tab.file.equal(file):
                    tab.file = renamed_file
                    tab.setup_auto_revert(check=True)
                    tab._update_language()
                    tab._update_title()
            self.queue_agenda_refresh()

        file.set_display_name_async(name, GLib.PRIORITY_DEFAULT, None, renamed)

    def remove_document(self, file, document, window, trash=False):
        def finish(confirmed):
            if not confirmed:
                return

            def remove():
                if file:
                    self.removed_files.add(file_order_key(file, None))
                    self._save_removed_files()
                if document and document in self.editor_tabs():
                    if document.window.tabs.get_n_pages() == 1:
                        document.window.new_tab()
                    document.force_close()
                if file and self._last_selected_file:
                    selected = Gio.File.new_for_uri(self._last_selected_file)
                    if file_order_key(selected, None) == file_order_key(file, None):
                        active = window.active_tab
                        self._last_selected_file = (
                            active.file.get_uri() if active and active.file else "")
                self.queue_agenda_refresh()

            if trash:
                def trashed(source, result):
                    try:
                        source.trash_finish(result)
                    except GLib.Error as error:
                        window.active_view.toast(f"Could not move file to Trash: {error.message}")
                        return
                    remove()
                file.trash_async(GLib.PRIORITY_DEFAULT, None, trashed)
            else:
                remove()

        if document:
            document.request_close(finish)
        else:
            finish(True)

    def agenda_file_visible(self, path):
        return Gio.File.new_for_path(os.path.realpath(path)).get_uri() not in self.hidden_agenda_files

    def save_utility_file_order(self):
        uris = [key for key in self.utility_file_order if isinstance(key, str)]
        if uris != self._saved_utility_file_order:
            if self.settings:
                self.settings.set_strv("utility-file-order", uris)
            self._saved_utility_file_order = uris
    def set_agenda_file_visible(self, path, visible):
        uri = Gio.File.new_for_path(os.path.realpath(path)).get_uri()
        if visible:
            self.hidden_agenda_files.discard(uri)
        else:
            self.hidden_agenda_files.add(uri)
        if self.settings:
            self.settings.set_strv("hidden-agenda-files", sorted(self.hidden_agenda_files))
        self.queue_agenda_refresh()

    def open_location(self, path, line):
        self.open_file(Gio.File.new_for_path(path), lambda win: win.goto_line(line))

    def edit_entry(self, entry, change):
        """Edit an agenda entry's heading, in its open window or on disk.

        change(lines, idx) returns (edit, message). Returns a message to show, if any.
        """
        win = self.window_for_path(entry.path)
        if win:
            ok, message = win.edit_heading(entry.line, entry.heading_text, change)
            return message if ok else "The heading was not found, refresh the calendar"
        try:
            with open(entry.path, encoding="utf-8") as f:
                lines = f.read().split("\n")
            idx = locate_heading(lines, entry.line, entry.heading_text)
            if idx is None:
                return "The heading was not found, refresh the calendar"
            edit, message = change(lines, idx)
            lines = org.apply_edit(lines, edit)
            tmp = entry.path + ".gnorg-tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            os.replace(tmp, entry.path)
        except OSError as e:
            return f"Could not update {os.path.basename(entry.path)}: {e.strerror}"
        return message

    def show_agenda(self, window=None):
        if window is None:
            active = self.get_active_window()
            windows = self.editor_windows()
            window = (active if isinstance(active, EditorWindow)
                      else windows[0] if windows else self.new_window())
        if window.agenda is None:
            window.agenda = AgendaView(window)
            window.agenda_container.set_child(window.agenda)
            window.agenda.connect("destroy", self._on_agenda_destroyed)
        self.agenda = window.agenda
        self.agenda.refresh()
        self.agenda.present()

    def _on_agenda_destroyed(self, agenda):
        agenda.window.agenda = None
        if self.agenda is agenda:
            self.agenda = None

    def choose_agenda(self, window=None):
        window = window or self.get_active_window()
        if window is None:
            window = self.new_window()
        chooser = AgendaChooser(self, window)
        chooser.present(window)
        return chooser

    def edit_custom_agenda(self, window, original=None):
        if self._custom_agenda_load_error:
            window.active_view.toast(self._custom_agenda_load_error)
            return None
        dialog = AgendaEditor(self, window, original)
        dialog.present(window)
        return dialog

    def save_custom_agenda(self, view, original=None):
        if self._custom_agenda_load_error:
            raise ValueError(self._custom_agenda_load_error)
        if any(saved.key == view.key and saved != original for saved in self.custom_agendas):
            raise ValueError("That agenda key is already in use.")
        views = [view if saved == original else saved for saved in self.custom_agendas]
        if original is None:
            views.append(view)
        elif original not in self.custom_agendas:
            raise ValueError("This agenda view no longer exists.")
        if self.settings and not self.settings.set_string("custom-agenda-views", encode_views(views)):
            raise ValueError("Could not save custom agenda views.")
        self.custom_agendas = views
        self._refresh_custom_agendas(original, view)

    def confirm_delete_custom_agenda(self, window, view, then=None):
        dialog = Adw.AlertDialog(
            heading="Delete Agenda View?", body=f"Delete “{view.title}”? "
            "Your Org files will not be changed.", close_response="cancel")
        dialog.add_response("cancel", "_Cancel")
        dialog.add_response("delete", "_Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)

        def response(_dialog, result):
            if result == "delete" and self.delete_custom_agenda(view) and then:
                then()

        dialog.connect("response", response)
        dialog.present(window)

    def delete_custom_agenda(self, view):
        views = [saved for saved in self.custom_agendas if saved != view]
        if self.settings and not self.settings.set_string("custom-agenda-views", encode_views(views)):
            self.get_active_window().active_view.toast("Could not delete custom agenda view.")
            return False
        self.custom_agendas = views
        self._refresh_custom_agendas(view, None)
        return True

    def _refresh_custom_agendas(self, original, replacement):
        for window in self.editor_windows():
            window.utility_pane.refresh_agendas()
            agenda = window.weekly_agenda
            if agenda is not None and original is not None and agenda.custom == original:
                agenda.set_custom(replacement)
                agenda.refresh()
                if window.active_view is agenda:
                    agenda._update_title()

    def show_weekly_agenda(self, window=None, custom=None):
        if window is None:
            active = self.get_active_window()
            windows = self.editor_windows()
            window = (active if isinstance(active, EditorWindow)
                      else windows[0] if windows else self.new_window())
        if window.weekly_agenda is None:
            window.weekly_agenda = AgendaView(window, weekly=True)
            window.weekly_agenda_container.set_child(window.weekly_agenda)
        window.weekly_agenda.set_custom(custom)
        window.weekly_agenda.refresh()
        window.weekly_agenda.present()

    def queue_agenda_refresh(self):
        if self._agenda_refresh_id:
            return

        def refresh():
            self._agenda_refresh_id = 0
            for window in self.editor_windows():
                window.utility_pane.refresh()
                if window.agenda is not None:
                    window.agenda.refresh()
                if window.weekly_agenda is not None:
                    window.weekly_agenda.refresh()
            return False

        self._agenda_refresh_id = GLib.timeout_add(300, refresh)

    # ---------- Dialogs ----------

    def show_buffers(self):
        window = self.get_active_window()
        current = window.active_tab if isinstance(window, EditorWindow) else None
        files = [file for uri in self.recent_files
                 if (file := Gio.File.new_for_uri(uri)).query_exists(None)]
        uris = [file.get_uri() for file in files]
        if uris != self.recent_files:
            self.recent_files = uris
            if self.settings:
                self.settings.set_strv("recent-files", uris)
        BufferPicker(self.editor_tabs(), current, files).present(window)

    def set_editor_padding(self, padding):
        self.editor_padding = padding
        for tab in self.editor_tabs():
            tab.view.set_top_margin(padding)
            tab.view.set_bottom_margin(padding)
            tab.view.set_left_margin(padding)
            tab.view.set_right_margin(padding)
            tab.org_indent.queue_refresh()
        if self.settings:
            self.settings.set_int("editor-padding", padding)

    def set_agenda_padding(self, padding):
        self.agenda_padding = padding
        for window in self.get_windows():
            if isinstance(window, EditorWindow) and window.weekly_agenda is not None:
                window.weekly_agenda.set_padding(padding)
        if self.settings:
            self.settings.set_int("agenda-padding", padding)

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
        line_numbers_row = Adw.SwitchRow(
            title="Show Line Numbers",
            subtitle="Display line numbers beside the editor text.",
            active=self.show_line_numbers)
        line_numbers_row.connect(
            "notify::active", lambda row, *_: self.set_show_line_numbers(row.get_active()))
        editor_group.add(line_numbers_row)
        padding_row = Adw.ActionRow(
            title="Editor Padding", subtitle="Space around the editor text, in pixels.")
        padding_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 64, 1)
        padding_scale.set_size_request(180, -1)
        padding_scale.set_valign(Gtk.Align.CENTER)
        padding_scale.set_draw_value(True)
        padding_scale.set_digits(0)
        padding_scale.set_value(self.editor_padding)
        padding_scale.update_property([Gtk.AccessibleProperty.LABEL], ["Editor padding"])
        padding_scale.connect(
            "value-changed", lambda scale: self.set_editor_padding(round(scale.get_value())))
        padding_row.add_suffix(padding_scale)
        editor_group.add(padding_row)
        agenda_padding_row = Adw.ActionRow(
            title="Agenda Padding", subtitle="Space around the agenda entries, in pixels.")
        agenda_padding_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 64, 1)
        agenda_padding_scale.set_size_request(180, -1)
        agenda_padding_scale.set_valign(Gtk.Align.CENTER)
        agenda_padding_scale.set_draw_value(True)
        agenda_padding_scale.set_digits(0)
        agenda_padding_scale.set_value(self.agenda_padding)
        agenda_padding_scale.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Agenda padding"])
        agenda_padding_scale.connect(
            "value-changed", lambda scale: self.set_agenda_padding(round(scale.get_value())))
        agenda_padding_row.add_suffix(agenda_padding_scale)
        editor_group.add(agenda_padding_row)
        auto_revert_row = Adw.SwitchRow(
            title="Automatically Reload Files",
            subtitle="Reload files changed on disk. Unsaved edits are kept and a warning is shown.",
            active=self.auto_revert)
        auto_revert_row.connect(
            "notify::active", lambda row, *_: self.set_auto_revert(row.get_active()))
        editor_group.add(auto_revert_row)
        escape_row = Adw.SwitchRow(
            title="Key Sequence to Escape",
            subtitle="Type the escape sequence quickly to leave Vim insert mode.",
            active=self.jj_escape)
        escape_row.connect(
            "notify::active", lambda row, *_: self.set_jj_escape(row.get_active()))
        editor_group.add(escape_row)
        sequence_row = Adw.EntryRow(
            title="Escape Sequence", text=self.escape_sequence, show_apply_button=True,
            sensitive=self.jj_escape)
        sequence_row.set_tooltip_text("Exactly two visible characters, for example jj or jk.")

        def apply_sequence(row):
            sequence = row.get_text()
            if len(sequence) != 2 or not all(
                    char.isprintable() and not char.isspace() for char in sequence):
                row.add_css_class("error")
                row.set_tooltip_text("Enter exactly two visible characters without spaces.")
                return
            row.remove_css_class("error")
            row.set_tooltip_text("Exactly two visible characters, for example jj or jk.")
            self.set_escape_sequence(sequence)

        sequence_row.connect("apply", apply_sequence)
        escape_row.bind_property(
            "active", sequence_row, "sensitive", GObject.BindingFlags.SYNC_CREATE)
        editor_group.add(sequence_row)
        delay_row = Adw.SpinRow(
            title="Escape Sequence Delay",
            subtitle="Maximum interval between the two keys, in milliseconds.",
            adjustment=Gtk.Adjustment(
                value=self.jj_escape_delay, lower=50, upper=1000, step_increment=10,
                page_increment=50), digits=0, sensitive=self.jj_escape)
        delay_row.connect(
            "notify::value", lambda row, *_: self.set_jj_escape_delay(int(row.get_value())))
        escape_row.bind_property("active", delay_row, "sensitive", GObject.BindingFlags.SYNC_CREATE)
        editor_group.add(delay_row)
        page.add(editor_group)
        group = Adw.PreferencesGroup(
            title="Calendar",
            description="Active timestamps in these .org files appear in the calendar. "
                        "Open Org files are also available. Check files in the utility pane "
                        "to show them in Calendar and Agenda.")
        row = Adw.ActionRow(title="Calendar Folder", subtitle=short_path(self.agenda_directory()))
        choose = Gtk.Button(icon_name="folder-open-symbolic", valign=Gtk.Align.CENTER,
                            tooltip_text="Choose Folder", css_classes=["flat"])
        if self.agenda_directory_override:
            choose.set_sensitive(False)
            row.set_subtitle(short_path(self.agenda_directory()) + " (launch override)")
            choose.set_tooltip_text("Folder overridden by GNORG_AGENDA_DIRECTORY for this run")
        row.add_suffix(choose)
        row.set_activatable_widget(choose)
        group.add(row)
        weekends_row = Adw.SwitchRow(
            title="Hide Weekends",
            subtitle="Show only Monday through Friday in the calendar.",
            active=self.hide_weekends)
        weekends_row.connect(
            "notify::active", lambda row, *_: self.set_hide_weekends(row.get_active()))
        group.add(weekends_row)
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
            self.queue_agenda_refresh()
            for window in self.editor_windows():
                if window.agenda is not None:
                    window.agenda.refresh(keep_scroll=False)
                if window.weekly_agenda is not None:
                    window.weekly_agenda.refresh(keep_scroll=False)

        def on_choose(_button):
            file_dialog = Gtk.FileDialog(title="Choose Calendar Folder")
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

    def set_show_line_numbers(self, enabled):
        self.show_line_numbers = enabled
        if self.settings:
            self.settings.set_boolean("show-line-numbers", enabled)
        for tab in self.editor_tabs():
            tab.view.set_show_line_numbers(enabled)

    def set_auto_revert(self, enabled):
        self.auto_revert = enabled
        if self.settings:
            self.settings.set_boolean("auto-revert", enabled)
        for tab in self.editor_tabs():
            tab.setup_auto_revert(check=enabled)

    def set_jj_escape(self, enabled):
        for tab in self.editor_tabs():
            tab._flush_escape_prefix()
        self.jj_escape = enabled
        if self.settings:
            self.settings.set_boolean("jj-escape", enabled)

    def set_jj_escape_delay(self, delay):
        for tab in self.editor_tabs():
            tab._flush_escape_prefix()
        self.jj_escape_delay = delay
        if self.settings:
            self.settings.set_int("jj-escape-delay", delay)

    def set_escape_sequence(self, sequence):
        for tab in self.editor_tabs():
            tab._flush_escape_prefix()
        self.escape_sequence = sequence
        if self.settings:
            self.settings.set_string("escape-sequence", sequence)

    def set_hide_weekends(self, enabled):
        self.hide_weekends = enabled
        action = self.lookup_action("hide-weekends")
        if action is not None:
            action.set_state(GLib.Variant.new_boolean(enabled))
        if self.settings:
            self.settings.set_boolean("hide-weekends", enabled)
        for window in self.editor_windows():
            if window.agenda is not None:
                window.agenda.refresh()

    def _on_about(self, *_):
        about = Adw.AboutDialog(
            application_name=APP_NAME,
            application_icon="accessories-text-editor",
            version=VERSION,
            comments="Gnorg is not org\nAn Org mode editor with Vim keybindings",
            license_type=Gtk.License.GPL_3_0,
        )
        about.present(self.get_active_window())

    @staticmethod
    def _migrate_settings(settings, legacy):
        if settings.get_boolean("legacy-settings-migrated"):
            return
        for key in settings.props.settings_schema.list_keys():
            if key == "legacy-settings-migrated" or settings.get_user_value(key) is not None:
                continue
            if not legacy.props.settings_schema.has_key(key):
                continue
            value = legacy.get_user_value(key)
            if value is not None and not settings.set_value(key, value):
                raise RuntimeError(f"{APP_NAME}: could not migrate setting {key}")
        if not settings.set_boolean("legacy-settings-migrated", True):
            raise RuntimeError(f"{APP_NAME}: could not mark settings migration complete")
        Gio.Settings.sync()

    @staticmethod
    def _load_settings():
        """Use the installed schema, or the one compiled next to the sources when run uncompiled."""
        default = Gio.SettingsSchemaSource.get_default()
        source = default
        schema = default.lookup(APP_ID, True) if default else None
        compiled = data_path("gschemas.compiled")
        if schema is None and compiled:
            source = Gio.SettingsSchemaSource.new_from_directory(os.path.dirname(compiled), default, False)
            schema = source.lookup(APP_ID, False)
        if schema is None:
            print(f"{APP_NAME}: settings schema {APP_ID} not found, preferences won't be saved "
                  "(run `make schemas` or `make install`)", file=sys.stderr)
            return None
        settings = Gio.Settings.new_full(schema, None, None)
        legacy_schema = source.lookup(LEGACY_APP_ID, True)
        if not settings.get_boolean("legacy-settings-migrated"):
            if legacy_schema is None:
                raise RuntimeError(f"{APP_NAME}: legacy settings schema {LEGACY_APP_ID} not found")
            GnorgApplication._migrate_settings(
                settings, Gio.Settings.new_full(legacy_schema, None, None))
        return settings

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
            f'textview.gnorg-editor, .gnorg-agenda label, .fold-indicator {{ '
            f'font-family: "Iosevka", monospace; '
            f"font-size: {font_size:g}pt; }}")
        for window in self.editor_tabs():
            window.org_indent.queue_refresh()
        self.lookup_action("zoom-out").set_enabled(zoom > ZOOM_MIN)
        self.lookup_action("zoom-in").set_enabled(zoom < ZOOM_MAX)
        if self.settings and self.settings.get_int("zoom") != zoom:
            self.settings.set_int("zoom", zoom)
        win = self.get_active_window()
        if (notify and isinstance(win, EditorWindow)
                and isinstance(win.active_view, (EditorTab, AgendaView))):
            win.active_view.show_zoom(zoom)

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
