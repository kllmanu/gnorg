"""Emacs-style Org key sequences."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402


class OrgKeySequences:
    def __init__(self, window, commands):
        self.commands = commands
        self._pending = False
        self._timeout_id = 0

        self.controller = Gtk.EventControllerKey(
            propagation_phase=Gtk.PropagationPhase.CAPTURE)
        self.controller.connect("key-pressed", self._on_key_pressed)
        window.add_controller(self.controller)
        window.connect("destroy", lambda *_: self._cancel())

    def _cancel(self):
        self._pending = False
        if self._timeout_id:
            GLib.source_remove(self._timeout_id)
            self._timeout_id = 0

    def _expire(self):
        self._pending = False
        self._timeout_id = 0
        return GLib.SOURCE_REMOVE

    def _start(self):
        self._pending = True
        self._timeout_id = GLib.timeout_add(1500, self._expire)

    @staticmethod
    def _sequence_key(keyval, state):
        modifiers = (Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.ALT_MASK
                     | Gdk.ModifierType.SUPER_MASK | Gdk.ModifierType.HYPER_MASK
                     | Gdk.ModifierType.META_MASK)
        key = (Gdk.keyval_name(keyval) or "").lower()
        if state & Gdk.ModifierType.CONTROL_MASK:
            if state & modifiers:
                return None
            return f"C-{key}"
        if state & modifiers:
            return None
        return key

    def _on_key_pressed(self, _controller, keyval, _keycode, state):
        key = self._sequence_key(keyval, state)
        control = Gdk.ModifierType.CONTROL_MASK
        modifiers = (Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.ALT_MASK
                     | Gdk.ModifierType.SUPER_MASK | Gdk.ModifierType.HYPER_MASK
                     | Gdk.ModifierType.META_MASK)

        if self._pending:
            self._cancel()
            if keyval == Gdk.KEY_c and state & control and not state & modifiers:
                self._start()
            elif key in self.commands:
                self.commands[key]()
            return True

        if keyval == Gdk.KEY_c and state & control and not state & modifiers:
            self._start()
            return True
        return False
