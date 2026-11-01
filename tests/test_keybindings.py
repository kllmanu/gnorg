import os
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir, "src"))

from keybindings import Gdk, OrgKeySequences  # noqa: E402


class KeySequenceTests(unittest.TestCase):
    def setUp(self):
        self.commands = {key: Mock() for key in (
            "C-c C-t", "C-c C-c", "C-c C-s", "C-c C-d", "C-c a",
            "C-x C-c", "C-x C-f", "C-g")}
        self.handler = OrgKeySequences(Mock(), self.commands)
        self.addCleanup(self.handler._cancel)

    def press(self, key):
        control = key.startswith("C-")
        name = key[2:] if control else key
        return self.handler._on_key_pressed(
            None, Gdk.keyval_from_name(name), 0,
            Gdk.ModifierType.CONTROL_MASK if control else Gdk.ModifierType(0))

    def test_supported_sequences(self):
        for sequence, callback in self.commands.items():
            if sequence == "C-g":
                continue
            with self.subTest(sequence=sequence):
                for key in sequence.split():
                    self.assertTrue(self.press(key))
                callback.assert_called_once_with()
                self.assertIsNone(self.handler._pending)

    def test_wrong_prefix_does_not_dispatch(self):
        self.press("C-x")
        self.press("C-s")
        self.commands["C-c C-s"].assert_not_called()
        self.assertIsNone(self.handler._pending)

    def test_keyboard_quit_cancels_both_prefixes(self):
        for prefix in ("C-c", "C-x"):
            self.press(prefix)
            self.assertTrue(self.press("C-g"))
            self.assertIsNone(self.handler._pending)
            self.assertEqual(self.handler._timeout_id, 0)
        self.assertEqual(self.commands["C-g"].call_count, 2)

    def test_expired_prefix_does_not_dispatch(self):
        self.press("C-x")
        self.handler._cancel()
        self.handler._expire()
        self.assertFalse(self.press("C-f"))
        self.commands["C-x C-f"].assert_not_called()

    def test_repeated_prefix_restarts_sequence(self):
        self.press("C-x")
        self.press("C-x")
        self.press("C-f")
        self.commands["C-x C-f"].assert_called_once_with()

    def test_unrelated_keys_pass_through(self):
        self.assertFalse(self.press("a"))
        self.assertFalse(self.press("C-s"))
