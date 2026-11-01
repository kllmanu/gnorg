"""Popover for picking an org timestamp: free-form text input plus a calendar."""

import datetime as dt

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, GObject, Gtk  # noqa: E402

import org  # noqa: E402

HINT = "e.g. tomorrow, fri 10:00, +3d, 5.11., nov 5, mon +1w"


class DatePopover(Gtk.Popover):
    """Emits `apply(timestamp)` with an org.Timestamp, or None to remove the date."""

    __gsignals__ = {"apply": (GObject.SignalFlags.RUN_FIRST, None, (object,))}

    def __init__(self, title, initial=None, today=None, allow_remove=True):
        super().__init__(has_arrow=True, position=Gtk.PositionType.BOTTOM)
        self.today = today or dt.date.today()
        self.timestamp = None
        self._syncing = False

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                      margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        box.append(Gtk.Label(label=title, xalign=0, css_classes=["heading"]))

        self.entry = Gtk.Entry(placeholder_text=HINT, width_chars=34, activates_default=False)
        self.entry.connect("changed", self._on_entry_changed)
        self.entry.connect("activate", lambda *_: self._apply())
        box.append(self.entry)

        self.preview = Gtk.Label(xalign=0, wrap=True, max_width_chars=40, css_classes=["caption"])
        box.append(self.preview)

        self.calendar = Gtk.Calendar()
        self.calendar.connect("day-selected", self._on_day_selected)
        box.append(self.calendar)

        buttons = Gtk.Box(spacing=6, halign=Gtk.Align.END)
        if initial and allow_remove:
            remove = Gtk.Button(label="_Remove", use_underline=True, css_classes=["destructive-action"])
            remove.connect("clicked", lambda *_: self._emit(None))
            buttons.append(remove)
        self.set_button = Gtk.Button(label="_Set", use_underline=True, css_classes=["suggested-action"])
        self.set_button.connect("clicked", lambda *_: self._apply())
        buttons.append(self.set_button)
        box.append(buttons)

        self.set_child(box)
        self.set_default_widget(self.set_button)

        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key_pressed)
        self.add_controller(keys)

        self.entry.set_text(initial.format(brackets=False, day_name=False) if initial else "")
        self._on_entry_changed(self.entry)
        self.connect("show", lambda *_: GLib.idle_add(self._focus_entry))

    def _on_key_pressed(self, _controller, keyval, _keycode, state):
        if (keyval == Gdk.KEY_g
                and state & Gdk.ModifierType.CONTROL_MASK
                and not state & (Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.ALT_MASK
                                | Gdk.ModifierType.SUPER_MASK | Gdk.ModifierType.HYPER_MASK
                                | Gdk.ModifierType.META_MASK)):
            self.popdown()
            return True
        return False

    def _focus_entry(self):
        self.entry.grab_focus()
        self.entry.select_region(0, -1)
        return False

    def _on_entry_changed(self, entry):
        self.timestamp = org.parse_date_input(entry.get_text(), self.today)
        if self.timestamp is None:
            self.preview.set_label("Can’t understand this date")
            self.preview.set_css_classes(["caption", "error"])
            self.set_button.set_sensitive(False)
            return
        self.preview.set_label(self.timestamp.describe(self.today))
        self.preview.set_css_classes(["caption", "dim-label"])
        self.set_button.set_sensitive(True)
        d = self.timestamp.date
        self._syncing = True
        self.calendar.set_date(GLib.DateTime.new_local(d.year, d.month, d.day, 0, 0, 0))
        self._syncing = False

    def _on_day_selected(self, calendar):
        if self._syncing:
            return
        g = calendar.get_date()
        date = dt.date(g.get_year(), g.get_month(), g.get_day_of_month())
        # Keep time, repeater and warning from the current input; only replace the date
        current = self.timestamp or org.Timestamp(date)
        self.entry.set_text(current.with_date(date).format(brackets=False, day_name=False))
        self.entry.set_position(-1)

    def _apply(self):
        if self.timestamp is not None:
            self._emit(self.timestamp)

    def _emit(self, value):
        self.emit("apply", value)
        self.popdown()
