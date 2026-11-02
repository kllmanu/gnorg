"""Zoom integration tests."""

from unittest.mock import patch
from unittest.mock import Mock

from editor_test_case import EditorTestCase
from vim import Gdk, Gtk
from vim import ZOOM_MAX, ZOOM_MIN


class ZoomTests(EditorTestCase):
    def test_weekly_agenda_font_size_matches_editor_at_shared_zoom_levels(self):
        import datetime as dt
        import org

        tab = self.app.new_tab()
        self.app.show_weekly_agenda(tab.window)
        agenda = tab.window.weekly_agenda
        entries = org.parse_entries(f"* Event <{dt.date.today().isoformat()}>\n")
        display = Gdk.Display.get_default()
        Gtk.StyleContext.remove_provider_for_display(display, self.app.zoom_css)
        Gtk.StyleContext.add_provider_for_display(
            display, self.app.zoom_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        original_zoom = self.app.zoom
        self.addCleanup(self.app.set_zoom, original_zoom, False)
        with patch.object(self.app, "agenda_entries", return_value=("/org", ["test.org"], entries)):
            agenda.refresh()
            label = agenda.entry_buttons[0][1].get_child()
            heading = agenda.listbox.get_first_child().get_child()
            sizes = []
            for zoom in (100, 150, 70):
                tab.present()
                self.wait_for(tab.view.get_mapped)
                self.app.set_zoom(zoom, notify=False)
                if sizes:
                    self.wait_for(lambda: tab.view.get_pango_context().get_font_description().get_size()
                                  != sizes[-1])
                editor_size = tab.view.get_pango_context().get_font_description().get_size()
                agenda.present()
                label = agenda.entry_buttons[0][1].get_child()
                heading = agenda.listbox.get_first_child().get_child()
                if sizes:
                    self.wait_for(lambda: label.get_pango_context().get_font_description().get_size()
                                  != sizes[-1])
                self.wait_for(lambda: label.get_pango_context().get_font_description().get_size()
                              == editor_size)
                self.assertEqual(heading.get_pango_context().get_font_description().get_size(),
                                 editor_size)
                sizes.append(editor_size)
            self.assertGreater(sizes[1], sizes[0])
            self.assertLess(sizes[2], sizes[0])

    def test_zoom_notification_targets_active_view_and_reuses_toast(self):
        tab = self.app.new_tab()
        tab.present()
        self.app.show_weekly_agenda(tab.window)
        agenda = tab.window.weekly_agenda
        self.wait_for(lambda: self.app.get_active_window() is tab.window)
        original_zoom = self.app.zoom
        self.addCleanup(self.app.set_zoom, original_zoom, False)
        self.app.set_zoom(100, notify=False)
        self.app.set_zoom(110)
        toast = agenda._zoom_toast
        self.assertIsNotNone(toast)
        self.assertEqual(toast.get_title(), "Zoom 110%")
        self.app.set_zoom(120)
        self.assertIs(agenda._zoom_toast, toast)
        self.assertEqual(toast.get_title(), "Zoom 120%")
        self.assertIsNone(tab._zoom_toast)
        toast.dismiss()
        self.assertIsNone(agenda._zoom_toast)
        self.app.set_zoom(130, notify=False)
        self.assertIsNone(agenda._zoom_toast)
        tab.present()
        self.app.set_zoom(140)
        self.assertEqual(tab._zoom_toast.get_title(), "Zoom 140%")
        self.assertIsNone(agenda._zoom_toast)

    def test_control_scroll_zooms_and_respects_limits(self):
        controller = Mock()
        controller.get_current_event_state.return_value = Gdk.ModifierType.CONTROL_MASK
        self.app.set_zoom(100, notify=False)
        self.assertTrue(self.app._on_zoom_scroll(controller, 0, -1))
        self.assertEqual(self.app.zoom, 110)
        self.assertTrue(self.app._on_zoom_scroll(controller, 0, 1))
        self.assertEqual(self.app.zoom, 100)
        for limit, delta in ((ZOOM_MAX, -1), (ZOOM_MIN, 1)):
            self.app.set_zoom(limit, notify=False)
            self.assertTrue(self.app._on_zoom_scroll(controller, 0, delta))
            self.assertEqual(self.app.zoom, limit)
        self.app.set_zoom(100, notify=False)
        controller.get_current_event_state.return_value = Gdk.ModifierType(0)
        self.assertFalse(self.app._on_zoom_scroll(controller, 0, -1))
        self.assertEqual(self.app.zoom, 100)
