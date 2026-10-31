#!/usr/bin/env python3
"""Vimtext – a small GNOME text editor with Vim keybindings."""

import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GtkSource", "5")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, GtkSource, Pango  # noqa: E402

APP_ID = "io.github.kllmanu.Vimtext"
APP_NAME = "Vimtext"
VERSION = "0.1.0"

BASE_FONT_SIZE = 16  # px, at 100% zoom
ZOOM_STEP = 10
ZOOM_MIN = 30
ZOOM_MAX = 400


class EditorWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, default_width=960, default_height=680)
        self.file = None
        self._force_close = False
        self._zoom_toast = None

        self._build_editor()
        self._build_ui()
        self._setup_vim()
        self._setup_actions()
        self._setup_style()

        self.buffer.connect("modified-changed", lambda *_: self._update_title())
        self.buffer.connect("notify::cursor-position", lambda *_: self._update_position())
        self.connect("close-request", self._on_close_request)
        self._update_title()
        self._update_position()

    # ---------- UI ----------

    def _build_editor(self):
        self.buffer = GtkSource.Buffer(highlight_syntax=True, highlight_matching_brackets=True)
        self.view = GtkSource.View(
            buffer=self.buffer,
            monospace=True,
            css_classes=["vimtext-editor"],
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

        header.pack_start(Gtk.Button(icon_name="document-open-symbolic",
                                     tooltip_text="Open (Ctrl+O)", action_name="win.open"))
        header.pack_start(Gtk.Button(icon_name="tab-new-symbolic",
                                     tooltip_text="New Window (Ctrl+N)", action_name="win.new"))

        menu = Gio.Menu()
        file_section = Gio.Menu()
        file_section.append("Save As…", "win.save-as")
        menu.append_section(None, file_section)
        view_section = Gio.Menu()
        view_section.append("Vim Mode", "win.vim-mode")
        view_section.append("Wrap Lines", "win.wrap")
        menu.append_section(None, view_section)
        app_section = Gio.Menu()
        app_section.append(f"About {APP_NAME}", "app.about")
        menu.append_section(None, app_section)

        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                       primary=True, tooltip_text="Main Menu"))
        header.pack_end(Gtk.Button(icon_name="document-save-symbolic",
                                   tooltip_text="Save (Ctrl+S)", action_name="win.save"))

        scroller = Gtk.ScrolledWindow(child=self.view, vexpand=True)

        # Status bar: vim command bar on the left, pending keys + cursor position on the right
        self.cmdbar_label = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END,
                                      css_classes=["monospace"])
        self.cmdtext_label = Gtk.Label(css_classes=["monospace", "dim-label"])
        self.position_label = Gtk.Label(css_classes=["monospace", "dim-label"])
        status = Gtk.Box(spacing=18, margin_start=12, margin_end=12, margin_top=4, margin_bottom=4)
        status.append(self.cmdbar_label)
        status.append(self.cmdtext_label)
        status.append(self.position_label)

        toolbar_view = Adw.ToolbarView(content=scroller)
        toolbar_view.add_top_bar(header)
        toolbar_view.add_bottom_bar(status)

        self.toasts = Adw.ToastOverlay(child=toolbar_view)
        self.set_content(self.toasts)

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
            self.add_action(action)

        add("new", lambda: self.get_application().new_window().present())
        add("open", self.open_dialog)
        add("save", self.save)
        add("save-as", self.save_as)

        vim_action = Gio.SimpleAction.new_stateful("vim-mode", None, GLib.Variant.new_boolean(True))
        vim_action.connect("change-state", self._on_vim_mode_changed)
        self.add_action(vim_action)

        wrap_action = Gio.SimpleAction.new_stateful("wrap", None, GLib.Variant.new_boolean(False))
        wrap_action.connect("change-state", self._on_wrap_changed)
        self.add_action(wrap_action)

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
        scheme = schemes.get_scheme("Adwaita-dark" if dark else "Adwaita") or \
            schemes.get_scheme("classic-dark" if dark else "classic")
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
        self.set_title(f"{name} – {APP_NAME}")

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
            for win in list(self.get_application().get_windows()):
                if cmd.endswith("!") and isinstance(win, EditorWindow):
                    win.force_close()
                else:
                    win.close()
            return True
        return False

    # ---------- File handling ----------

    def open_dialog(self):
        dialog = Gtk.FileDialog(title="Open File")

        def on_done(dlg, res):
            try:
                file = dlg.open_finish(res)
            except GLib.Error:
                return
            if self.is_pristine:
                self.load(file)
            else:
                self.get_application().open_file(file)

        dialog.open(self, None, on_done)

    def load(self, file):
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
        language = GtkSource.LanguageManager.get_default().guess_language(name, content_type)
        self.buffer.set_language(language)

    def save(self, then=None):
        if self.file is None:
            self.save_as(then=then)
        else:
            self.save_to(self.file, then=then)

    def save_as(self, then=None):
        dialog = Gtk.FileDialog(title="Save File")
        if self.file:
            dialog.set_initial_file(self.file)
        else:
            dialog.set_initial_name("Untitled.txt")

        def on_done(dlg, res):
            try:
                file = dlg.save_finish(res)
            except GLib.Error:
                return
            self.save_to(file, then=then)

        dialog.save(self, None, on_done)

    def save_to(self, file, then=None):
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
                return
            if self.file is None or not self.file.equal(f):
                self.file = f
                self._update_language()
            self.buffer.set_modified(False)
            self._update_title()
            self.toast(f"“{f.get_basename()}” written, {len(data)} bytes")
            if then:
                then()

        file.replace_contents_async(data, None, False, Gio.FileCreateFlags.NONE, None, on_saved)

    # ---------- Closing ----------

    def force_close(self):
        self._force_close = True
        self.close()

    def _on_close_request(self, _win):
        if self._force_close or not self.buffer.get_modified():
            return False

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
                self.force_close()
            elif response == "save":
                self.save(then=self.force_close)

        dialog.connect("response", on_response)
        dialog.present(self)
        return True


class VimtextApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self.zoom = None
        self.zoom_css = None
        self.settings = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        GLib.set_application_name(APP_NAME)

        self.settings = self._load_settings()

        self.zoom_css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), self.zoom_css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        for name, callback in (
            ("about", self._on_about),
            ("quit", self._on_quit),
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
        self.set_accels_for_action("win.new", ["<Ctrl>n"])
        self.set_accels_for_action("win.open", ["<Ctrl>o"])
        self.set_accels_for_action("win.save", ["<Ctrl>s"])
        self.set_accels_for_action("win.save-as", ["<Ctrl><Shift>s"])
        self.set_accels_for_action("window.close", ["<Ctrl>w"])

    def do_activate(self):
        self.new_window().present()

    def do_open(self, files, _n_files, _hint):
        for file in files:
            self.open_file(file)

    def new_window(self):
        win = EditorWindow(self)
        win.view.grab_focus()
        return win

    def open_file(self, file):
        for win in self.get_windows():
            if isinstance(win, EditorWindow) and win.file and win.file.equal(file):
                win.present()
                return
        win = self.new_window()
        win.load(file)
        win.present()

    def _on_about(self, *_):
        about = Adw.AboutDialog(
            application_name=APP_NAME,
            application_icon="accessories-text-editor",
            version=VERSION,
            comments="A simple text editor with Vim keybindings",
            license_type=Gtk.License.GPL_3_0,
        )
        about.present(self.get_active_window())

    @staticmethod
    def _load_settings():
        """Use the installed schema, or the one compiled next to the sources when run uncompiled."""
        default = Gio.SettingsSchemaSource.get_default()
        schema = default.lookup(APP_ID, True) if default else None
        if schema is None:
            data_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), os.pardir, "data")
            if os.path.exists(os.path.join(data_dir, "gschemas.compiled")):
                source = Gio.SettingsSchemaSource.new_from_directory(data_dir, default, False)
                schema = source.lookup(APP_ID, False)
        if schema is None:
            print(f"{APP_NAME}: settings schema {APP_ID} not found, preferences won't be saved "
                  "(run `make schemas` or `make install`)", file=sys.stderr)
            return None
        return Gio.Settings.new_full(schema, None, None)

    def set_zoom(self, zoom, notify=True):
        zoom = max(ZOOM_MIN, min(ZOOM_MAX, zoom))
        if zoom == self.zoom:
            return
        self.zoom = zoom
        font_size = BASE_FONT_SIZE * zoom / 100
        self.zoom_css.load_from_string(f"textview.vimtext-editor {{ font-size: {font_size:g}px; }}")
        self.lookup_action("zoom-out").set_enabled(zoom > ZOOM_MIN)
        self.lookup_action("zoom-in").set_enabled(zoom < ZOOM_MAX)
        if self.settings and self.settings.get_int("zoom") != zoom:
            self.settings.set_int("zoom", zoom)
        win = self.get_active_window()
        if notify and isinstance(win, EditorWindow):
            win.show_zoom(zoom)

    def _on_quit(self, *_):
        for win in list(self.get_windows()):
            win.close()


def main():
    return VimtextApplication().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
