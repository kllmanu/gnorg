"""Editor integration tests."""

from unittest.mock import patch

from editor_test_case import EditorTestCase
from vim import GLib


class EditorTests(EditorTestCase):
    def test_application_branding(self):
        tab = self.app.new_tab()
        self.assertEqual(GLib.get_application_name(), "GNORG")
        self.assertEqual(tab.window.get_title(), "Untitled – GNORG")
        with patch("vim.Adw.AboutDialog") as about:
            self.app._on_about()
            self.assertEqual(about.call_args.kwargs["application_name"], "GNORG")
            self.assertIn("Gnorg is not org", about.call_args.kwargs["comments"])
            about.return_value.present.assert_called_once_with(self.app.get_active_window())

    def test_confirm_org_item_still_toggles_checkboxes(self):
        tab = self.app.new_tab()
        tab.buffer.set_text("* Report\n- [ ] Task\n")
        tab.buffer.place_cursor(tab.buffer.get_iter_at_line(1)[1])
        tab.confirm_org_item()
        self.assertEqual(tab.get_text(), "* Report\n- [X] Task\n")
        self.assertIsNone(tab._active_tag_popover)
