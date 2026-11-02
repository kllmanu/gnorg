"""Subtree movement and promotion/demotion with Alt+Arrow shortcuts."""

from editor_test_case import EditorTestCase
import org
from vim import Gdk


class HeadingEditTests(EditorTestCase):
    def test_move_keeps_subtree_folded(self):
        tab = self.app.new_tab()
        tab.buffer.set_text("* First\nBody\n** Child\nDetails\n* Second\nOther\n")
        tab.buffer.place_cursor(tab.buffer.get_start_iter())
        tab.org_fold.cycle()
        tab.move_or_relevel_heading(1, move=True)
        self.assertTrue(tab.buffer.get_iter_at_line(3)[1].has_tag(tab.org_fold.hidden))
        self.assertTrue(tab.buffer.get_iter_at_line(4)[1].has_tag(tab.org_fold.hidden))
        self.assertEqual(tab.buffer.get_iter_at_mark(tab.buffer.get_insert()).get_line(), 2)

    def test_subtree_moves_among_siblings_without_crossing_parent(self):
        lines = ["* Parent", "** First", "Body", "*** Child", "Details",
                 "** Second", "Other body", "* Next"]
        edit, target = org.move_heading(lines, 1, 1)
        moved = org.apply_edit(lines, edit)
        self.assertEqual(target, 3)
        self.assertEqual(moved, ["* Parent", "** Second", "Other body",
                                 "** First", "Body", "*** Child", "Details", "* Next"])
        edit, target = org.move_heading(moved, 3, -1)
        self.assertEqual(org.apply_edit(moved, edit), lines)
        self.assertEqual(target, 1)
        self.assertIsNone(org.move_heading(lines, 1, -1))
        self.assertIsNone(org.move_heading(lines, 5, 1))

    def test_relevel_subtree_keeps_body_and_literal_stars_unchanged(self):
        lines = ["* Parent", "** Previous", "** Task", "Body", "#+BEGIN_SRC org", "* Literal",
                 "#+END_SRC", "*** Child", "* Next"]
        updated = org.apply_edit(lines, org.relevel_heading(lines, 2, 1))
        self.assertEqual(updated, ["* Parent", "** Previous", "*** Task", "Body", "#+BEGIN_SRC org",
                                   "* Literal", "#+END_SRC", "**** Child", "* Next"])
        self.assertIsNone(org.relevel_heading(lines, 0, -1))

    def test_demote_requires_a_preceding_sibling_parent(self):
        lines = ["* Parent", "** First", "*** Child", "** Second", "* Next", "** Only"]
        for line in (0, 1, 2, 5):
            with self.subTest(line=line):
                self.assertIsNone(org.relevel_heading(lines, line, 1))
        updated = org.apply_edit(lines, org.relevel_heading(lines, 3, 1))
        self.assertEqual(updated[3], "*** Second")
        self.assertEqual(org.apply_edit(updated, org.relevel_heading(updated, 3, -1)), lines)

    def test_alt_arrows_move_and_relevel_with_cursor_and_undo(self):
        tab = self.app.new_tab()
        text = "* First\nBody\n** Child\nDetails\n* Second\nOther\n"
        tab.buffer.set_text(text)
        tab.buffer.place_cursor(tab.buffer.get_iter_at_line_offset(0, 3)[1])
        tab.present()
        self.wait_for(lambda: tab.window.get_focus() is tab.view)
        self.assertTrue(tab.window._on_key_pressed(
            None, Gdk.KEY_Down, 0, Gdk.ModifierType.ALT_MASK))
        self.assertEqual(tab.get_text(), "* Second\nOther\n* First\nBody\n** Child\nDetails\n")
        cursor = tab.buffer.get_iter_at_mark(tab.buffer.get_insert())
        self.assertEqual((cursor.get_line(), cursor.get_line_offset()), (2, 3))
        tab.buffer.undo()
        self.assertEqual(tab.get_text(), text)
        tab.buffer.place_cursor(tab.buffer.get_iter_at_line_offset(0, 3)[1])
        tab.window._on_key_pressed(None, Gdk.KEY_Right, 0, Gdk.ModifierType.ALT_MASK)
        self.assertEqual(tab.get_text(), text)
        tab.buffer.place_cursor(tab.buffer.get_iter_at_line_offset(4, 3)[1])
        tab.window._on_key_pressed(None, Gdk.KEY_Right, 0, Gdk.ModifierType.ALT_MASK)
        self.assertEqual(tab.get_text(), "* First\nBody\n** Child\nDetails\n** Second\nOther\n")
        tab.window._on_key_pressed(None, Gdk.KEY_Left, 0, Gdk.ModifierType.ALT_MASK)
        self.assertEqual(tab.get_text(), text)
