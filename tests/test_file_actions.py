"""File actions integration tests."""

import os
import tempfile
from unittest.mock import patch
from unittest.mock import Mock

from editor_test_case import EditorTestCase
from vim import Gio, GLib
from vim import Gdk, Gtk


class FileActionTests(EditorTestCase):
    def test_file_context_remove_preserves_disk_and_can_be_reopened(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "events.org")
            file = Gio.File.new_for_path(path)
            with open(path, "w") as stream:
                stream.write("* Event <2026-11-02>\n")
            with patch.object(self.app, "agenda_directory", return_value=directory):
                tab = self.app.open_file(file)
                self.wait_for(lambda: bool(tab.get_text()))
                window = tab.window
                pane = window.utility_pane
                pane.refresh()
                row = next(row for row in pane.rows if row.file.equal(file))
                pane._show_file_menu(row, 10, 10)
                menu = pane._context_menu
                buttons = []
                child = menu.get_child().get_first_child()
                while child:
                    buttons.append(child)
                    child = child.get_next_sibling()
                self.assertEqual([button.get_child().get_label() for button in buttons],
                                 ["Rename…", "Change Color…", "Remove", "Move to Trash…"])
                self.assertTrue(all(button.get_child().get_xalign() == 0 for button in buttons))
                self.assertTrue(all(button.get_child().has_css_class("file-action-label")
                                    for button in buttons))
                buttons[2].emit("clicked")
                self.wait_for(lambda: not pane.rows and pane._context_menu is None)
                self.assertTrue(os.path.isfile(path))
                self.assertTrue(self.app.file_removed(file))
                self.assertEqual(self.app.agenda_entries()[2], [])
                self.assertNotIn(tab, window.documents())
                reopened = self.app.open_file(file)
                self.wait_for(lambda: bool(reopened.get_text()))
                self.assertFalse(self.app.file_removed(file))
                pane.refresh()
                self.assertEqual(len(pane.rows), 1)

    def test_file_remove_prompts_for_unsaved_changes_and_cancel_keeps_file(self):
        tab = self.app.new_tab()
        tab._set_file(Gio.File.new_for_path("/org/test.org"), "* Original")
        tab.buffer.set_text("* Unsaved")
        self.app.remove_document(tab.file, tab, tab.window)
        dialog = tab.window.get_visible_dialog()
        self.assertIsNotNone(dialog)
        dialog.emit("response", "cancel")
        self.assertFalse(self.app.file_removed(tab.file))
        self.assertIn(tab, self.app.editor_tabs())
        self.assertEqual(tab.get_text(), "* Unsaved")

    def test_file_trash_requires_confirmation_and_failure_keeps_document(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "test.org")
            with open(path, "w") as stream:
                stream.write("* Original")
            tab = self.app.open_file(Gio.File.new_for_path(path))
            self.wait_for(lambda: bool(tab.get_text()))
            pane = tab.window.utility_pane
            pane.refresh()
            row = next(row for row in pane.rows if row.document is tab)
            with patch.object(Gio.File, "trash_async") as trash:
                pane._confirm_trash(row)
                tab.window.get_visible_dialog().emit("response", "cancel")
                trash.assert_not_called()
            source = Mock()
            source.trash_finish.side_effect = GLib.Error("Trash unavailable")
            with (patch.object(Gio.File, "trash_async",
                               side_effect=lambda _priority, _cancel, callback: callback(source, None)),
                  patch.object(tab, "toast") as toast):
                self.app.remove_document(tab.file, tab, tab.window, trash=True)
                toast.assert_called_once()
            self.assertTrue(os.path.isfile(path))
            self.assertIn(tab, self.app.editor_tabs())
            self.assertFalse(self.app.file_removed(tab.file))
            source.trash_finish.side_effect = None
            source.trash_finish.return_value = True
            with patch.object(Gio.File, "trash_async",
                              side_effect=lambda _priority, _cancel, callback: callback(source, None)):
                self.app.remove_document(tab.file, tab, tab.window, trash=True)
            self.assertNotIn(tab, self.app.editor_tabs())
            self.assertTrue(self.app.file_removed(tab.file))

    def test_new_file_save_appends_org_extension_and_list_hides_it(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(self.app, "agenda_directory", return_value=directory):
                tab = self.app.new_tab()
                tab.buffer.set_text("* New")
                tab.save_to(Gio.File.new_for_path(os.path.join(directory, "notes")))
                self.wait_for(lambda: tab.file is not None)
                self.assertEqual(tab.path, os.path.join(directory, "notes.org"))
                self.assertFalse(os.path.exists(os.path.join(directory, "notes")))
                pane = tab.window.utility_pane
                pane.refresh()
                row = next(row for row in pane.rows if row.document is tab)
                self.assertEqual(row.get_title(), "notes")
                self.assertEqual(row.get_tooltip_text(), tab.path)
                tab.buffer.set_text("* Updated")
                tab.save()
                self.wait_for(lambda: not tab.buffer.get_modified())
                self.assertFalse(os.path.exists(tab.path + ".org"))
                other = tab.window.new_tab()
                other.buffer.set_text("* Do not overwrite")
                with patch.object(other, "toast") as toast:
                    other.save_to(Gio.File.new_for_path(os.path.join(directory, "notes")))
                    toast.assert_called_once()
                self.assertIsNone(other.file)
                with open(tab.path) as stream:
                    self.assertEqual(stream.read(), "* Updated\n")

    def test_file_rename_preserves_unsaved_text_order_color_and_visibility(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "before.org")
            renamed = os.path.join(directory, "after.org")
            with open(path, "w") as stream:
                stream.write("* Disk")
            with patch.object(self.app, "agenda_directory", return_value=directory):
                tab = self.app.open_file(Gio.File.new_for_path(path))
                self.wait_for(lambda: bool(tab.get_text()))
                tab.buffer.set_text("* Unsaved")
                pane = tab.window.utility_pane
                pane.refresh()
                old_key = tab.file.get_uri()
                self.app.set_calendar_file_color(tab.file, "#123456")
                self.app.set_agenda_file_visible(path, False)
                self.app.rename_document(tab.file, "after", tab.window)
                self.wait_for(lambda: tab.path == renamed)
                self.assertFalse(os.path.exists(path))
                with open(renamed) as stream:
                    self.assertEqual(stream.read(), "* Disk")
                self.assertEqual(tab.get_text(), "* Unsaved")
                self.assertTrue(tab.buffer.get_modified())
                self.assertEqual(self.app.calendar_file_color(renamed), "#123456")
                self.assertFalse(self.app.agenda_file_visible(renamed))
                self.assertNotIn(old_key, self.app.utility_file_order)
                self.assertIn(tab.file.get_uri(), self.app.utility_file_order)
                collision = os.path.join(directory, "existing.org")
                with open(collision, "w") as stream:
                    stream.write("* Keep")
                with patch.object(tab, "toast") as toast:
                    self.app.rename_document(tab.file, "existing.org", tab.window)
                    self.wait_for(lambda: toast.called)
                self.assertEqual(tab.path, renamed)
                with open(collision) as stream:
                    self.assertEqual(stream.read(), "* Keep")
                pane.refresh()
                row = next(row for row in pane.rows if row.document is tab)
                pane._rename_file(row)
                dialog = tab.window.get_visible_dialog()
                entry = dialog.get_extra_child()
                self.assertEqual(entry.get_text(), "after")
                self.assertFalse(dialog.get_response_enabled("rename"))
                entry.set_text("../invalid.org")
                self.assertFalse(dialog.get_response_enabled("rename"))
                entry.set_text("valid.org")
                self.assertTrue(dialog.get_response_enabled("rename"))
                dialog.emit("response", "cancel")
                self.assertEqual(tab.path, renamed)

    def test_custom_file_color_updates_dot_and_event_and_is_saved(self):
        import datetime as dt
        from gi.repository import GdkPixbuf

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "event.org")
            with open(path, "w") as stream:
                stream.write(f"* Event <{dt.date.today().isoformat()}>\n")
            with patch.object(self.app, "agenda_directory", return_value=directory):
                tab = self.app.open_file(Gio.File.new_for_path(path))
                self.wait_for(lambda: bool(tab.get_text()))
                self.app.show_agenda(tab.window)
                settings = Mock()
                with patch.object(self.app, "settings", settings):
                    self.app.set_calendar_file_color(tab.file, "#123456")
                    settings.set_value.assert_called_once()
                    self.assertEqual(settings.set_value.call_args.args[1].unpack(),
                                     {tab.file.get_uri(): "#123456"})
                self.wait_for(lambda: tab.window.agenda.entry_buttons[0][1].has_css_class(
                    "custom-color-123456"))
                button = tab.window.agenda.entry_buttons[0][1]
                self.wait_for(lambda: button.get_mapped() and button.get_width() > 0)
                self.wait_for(lambda: Gtk.WidgetPaintable.new(button).get_current_image()
                              .get_intrinsic_width() > 0)
                image_path = os.path.join(directory, "event.png")

                def rendered_color_matches():
                    snapshot = Gtk.Snapshot.new()
                    Gtk.WidgetPaintable.new(button).snapshot(
                        snapshot, button.get_width(), button.get_height())
                    texture = tab.window.get_renderer().render_texture(snapshot.to_node(), None)
                    texture.save_to_png(image_path)
                    pixels = GdkPixbuf.Pixbuf.new_from_file(image_path)
                    data = pixels.get_pixels()
                    return any(
                        data[y * pixels.get_rowstride() + x * pixels.get_n_channels():
                             y * pixels.get_rowstride() + x * pixels.get_n_channels() + 3]
                        == b"\x12\x34\x56"
                        for y in range(pixels.get_height()) for x in range(pixels.get_width()))

                self.wait_for(rendered_color_matches)
                pane = tab.window.utility_pane
                pane.refresh()
                row = next(row for row in pane.rows if row.document is tab)
                with patch("utilitypane.Gtk.ColorDialog") as chooser:
                    pane._choose_file_color(row)
                    color = Gdk.RGBA()
                    color.parse("#abcdef")
                    chooser.return_value.choose_rgba_finish.return_value = color
                    callback = chooser.return_value.choose_rgba.call_args.args[3]
                    callback(chooser.return_value, None)
                self.assertEqual(self.app.calendar_file_color(path), "#abcdef")
