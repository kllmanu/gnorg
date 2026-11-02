"""Progress cookies for direct child tasks and checklist items."""

import re

from editor_test_case import EditorTestCase
import org
from vim import Gdk


class ProgressTests(EditorTestCase):
    def test_completed_cookies_use_green_done_style_in_headings_and_lists(self):
        tab = self.app.new_tab()
        text = "* Project [3/3] [100%] [2/3] [0/0]\n- [ ] Items [12/12] [100%] [50%]\n"
        tab.buffer.set_text(text)
        tab.buffer.ensure_highlight(*tab.buffer.get_bounds())
        tab._highlight_progress()
        scheme = tab.buffer.get_style_scheme()
        done = scheme.get_style("org:done").props.foreground
        incomplete = scheme.get_style("org:checkbox").props.foreground

        def color_at(offset):
            color = None
            for tag in sorted(tab.buffer.get_iter_at_offset(offset).get_tags(),
                              key=lambda tag: tag.get_priority()):
                if tag.props.foreground_set:
                    color = tag.props.foreground_rgba
            return color.to_string() if color else None

        for cookie, expected in (("[3/3]", done), ("[100%]", done), ("[12/12]", done),
                                 ("[2/3]", incomplete), ("[0/0]", incomplete),
                                 ("[50%]", incomplete)):
            color = Gdk.RGBA()
            self.assertTrue(color.parse(expected))
            for match in re.finditer(re.escape(cookie), text):
                with self.subTest(cookie=cookie, offset=match.start()):
                    self.assertEqual(color_at(match.start()), color.to_string())

    def test_counts_direct_children_and_preserves_nested_scope(self):
        lines = [
            "* Project [/] [%]", "** DONE First", "*** TODO Nested",
            "** TODO Second", "* Shopping [/]",
            "- [X] Food", "- [ ] Supplies [/]", "  - [X] Pens", "  - [-] Paper",
        ]
        self.assertEqual(org.progress_updates(lines), [
            (0, "* Project [1/2] [50%]"), (4, "* Shopping [1/2]"),
            (6, "- [ ] Supplies [1/2]"),
        ])

    def test_empty_cookies_and_literal_blocks(self):
        self.assertEqual(org.progress_updates([
            "* Empty [/] [%]", "#+BEGIN_SRC org", "* Example [/]", "#+END_SRC",
        ]), [(0, "* Empty [0/0] [0%]")])

    def test_checkbox_updates_cookie_and_undo_restores_both(self):
        tab = self.app.new_tab()
        text = "* Shopping [0/2] [0%]\n- [ ] Food\n- [ ] Supplies\n"
        tab.buffer.set_text(text)
        tab.buffer.place_cursor(tab.buffer.get_iter_at_line(1)[1])
        tab.toggle_checkbox()
        self.wait_for(lambda: "[1/2] [50%]" in tab.get_text())
        tab.buffer.undo()
        self.assertEqual(tab.get_text(), text)

    def test_todo_change_updates_parent_cookie(self):
        tab = self.app.new_tab()
        tab.buffer.set_text("* Project [/]\n** TODO First\n** DONE Second\n")
        tab.buffer.place_cursor(tab.buffer.get_iter_at_line(1)[1])
        tab.cycle_todo()
        self.wait_for(lambda: "* Project [2/2]" in tab.get_text())
