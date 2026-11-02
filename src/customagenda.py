"""Saved headline and dated agenda views.

Filter groups combine with AND; states, priorities and any-tags combine with OR.
Tags are the heading's own tags, not inherited tags. Empty filters match all.
"""

from dataclasses import asdict, dataclass
import datetime as dt
import json

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk  # noqa: E402

import org  # noqa: E402


@dataclass
class HeadlineItem:
    entry: org.Entry
    date: dt.date | None = None
    kind: str = "headline"
    timestamp_line: int | None = None


@dataclass(frozen=True)
class CustomAgenda:
    key: str
    title: str
    kind: str = "headlines"
    all_tags: tuple[str, ...] = ()
    any_tags: tuple[str, ...] = ()
    excluded_tags: tuple[str, ...] = ()
    states: tuple[str, ...] = ()
    priorities: tuple[str, ...] = ()
    text: str = ""
    category: str = ""
    planning: str = "any"

    def __post_init__(self):
        if (not isinstance(self.key, str) or len(self.key) != 1
                or not self.key.isascii() or not self.key.isalnum() or self.key == "a"):
            raise ValueError("Choose one letter or digit; a is reserved for the built-in Agenda.")
        if not isinstance(self.title, str) or not self.title.strip():
            raise ValueError("Enter a title.")
        if self.kind not in ("headlines", "week", "day"):
            raise ValueError("Unknown agenda view type.")
        for field in ("all_tags", "any_tags", "excluded_tags"):
            values = getattr(self, field)
            if not isinstance(values, tuple) or not all(isinstance(v, str) for v in values):
                raise ValueError("Tags must be a list of tag names.")
            if org.parse_tags(" ".join(values)) != list(values):
                raise ValueError("Enter distinct tag names separated by spaces.")
        if (not isinstance(self.states, tuple)
                or any(state not in (*org.ALL_KEYWORDS, "NONE") for state in self.states)):
            raise ValueError("TODO states must be TODO, DONE, or NONE (no state).")
        if (not isinstance(self.priorities, tuple)
                or any(not isinstance(p, str) or len(p) != 1 or p not in
                       "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" for p in self.priorities)):
            raise ValueError("Priorities must be single letters or digits, such as A B C.")
        if not isinstance(self.text, str) or not isinstance(self.category, str):
            raise ValueError("Title and category filters must be text.")
        if self.planning not in ("any", "scheduled", "deadline", "unplanned"):
            raise ValueError("Unknown planning filter.")

    def matches(self, entry):
        tags = set(entry.tags)
        return (
            set(self.all_tags) <= tags
            and (not self.any_tags or bool(tags.intersection(self.any_tags)))
            and not tags.intersection(self.excluded_tags)
            and (not self.states or (entry.keyword or "NONE") in self.states)
            and (not self.priorities or entry.priority in self.priorities)
            and self.text.casefold() in entry.display_title.casefold()
            and self.category.casefold() in org.category(entry).casefold()
            and (self.planning == "any"
                 or self.planning == "scheduled" and entry.scheduled is not None
                 or self.planning == "deadline" and entry.deadline is not None
                 or self.planning == "unplanned"
                 and entry.scheduled is None and entry.deadline is None)
        )


def decode_views(raw):
    try:
        values = json.loads(raw)
        if not isinstance(values, list):
            raise ValueError("Expected a list of custom agenda views.")
        views = []
        for value in values:
            if not isinstance(value, dict):
                raise ValueError("Each custom agenda view must be an object.")
            value = value.copy()
            for name in ("all_tags", "any_tags", "excluded_tags", "states", "priorities"):
                if name in value:
                    if not isinstance(value[name], list):
                        raise ValueError(f"{name} must be a list.")
                    value[name] = tuple(value[name])
            views.append(CustomAgenda(**value))
        if len({view.key for view in views}) != len(views):
            raise ValueError("Custom agenda keys must be unique.")
        return views
    except (TypeError, ValueError) as error:
        raise ValueError(f"Invalid saved custom agenda views: {error}") from error


def encode_views(views):
    return json.dumps([asdict(view) for view in views])


class AgendaEditor(Adw.Dialog):
    def __init__(self, app, window, original=None):
        super().__init__(title="Edit Agenda View" if original else "New Agenda View",
                         content_width=520, content_height=640)
        self.app, self.window, self.original = app, window, original
        self.fields = {}
        self.saved_view = None
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        save = Gtk.Button(label="Save", css_classes=["suggested-action"])
        header.pack_end(save)
        content.append(header)
        page = Adw.PreferencesPage()
        identity = Adw.PreferencesGroup(description="Use C-c a, then the key, to open this view. "
                                                   "The key a opens the built-in Agenda.")
        self._entry(identity, "key", "Key", original.key if original else "")
        self._entry(identity, "title", "Title", original.title if original else "")
        self.kind = Adw.ComboRow(
            title="View type", model=Gtk.StringList.new(["Headlines", "Week agenda", "Day agenda"]),
            selected=("headlines", "week", "day").index(original.kind) if original else 0)
        identity.add(self.kind)
        page.add(identity)
        filters = Adw.PreferencesGroup(
            title="Filters", description="Empty fields match everything. Filter groups combine "
            "with AND. Tags apply to the heading itself, without inheritance.")
        for name, title in (
            ("all_tags", "All tags"), ("any_tags", "Any tags"),
            ("excluded_tags", "Excluded tags"), ("states", "TODO states (TODO DONE NONE)"),
            ("priorities", "Priorities (A B C)"), ("text", "Title contains"),
            ("category", "Category / filename contains"),
        ):
            value = getattr(original, name) if original else ""
            self._entry(filters, name, title, " ".join(value) if isinstance(value, tuple) else value)
        self.planning = Adw.ComboRow(
            title="Planning", model=Gtk.StringList.new(
                ["Any", "Has schedule", "Has deadline", "Neither schedule nor deadline"]),
            selected=("any", "scheduled", "deadline", "unplanned").index(original.planning)
            if original else 0)
        filters.add(self.planning)
        page.add(filters)
        self.error = Gtk.Label(wrap=True, xalign=0, css_classes=["error"], visible=False,
                               margin_start=18, margin_end=18, margin_bottom=12)
        if original:
            group = Adw.PreferencesGroup()
            delete = Gtk.Button(label="Delete View", css_classes=["destructive-action"],
                                halign=Gtk.Align.CENTER)
            delete.connect("clicked", self._delete)
            group.add(delete)
            page.add(group)
        content.append(Gtk.ScrolledWindow(child=page, vexpand=True,
                                          hscrollbar_policy=Gtk.PolicyType.NEVER))
        content.append(self.error)
        self.set_child(content)
        save.connect("clicked", self._save)

    def _entry(self, group, name, title, text):
        row = Adw.EntryRow(title=title, text=text)
        self.fields[name] = row
        group.add(row)

    def _save(self, *_):
        try:
            values = {name: row.get_text().strip() for name, row in self.fields.items()}
            for name in ("all_tags", "any_tags", "excluded_tags"):
                values[name] = tuple(org.parse_tags(values[name].replace(",", " ")))
            for name in ("states", "priorities"):
                values[name] = tuple(dict.fromkeys(values[name].replace(",", " ").upper().split()))
            view = CustomAgenda(
                **values, kind=("headlines", "week", "day")[self.kind.get_selected()],
                planning=("any", "scheduled", "deadline", "unplanned")[self.planning.get_selected()])
            self.app.save_custom_agenda(view, self.original)
        except ValueError as error:
            self.error.set_label(str(error))
            self.error.set_visible(True)
            return
        self.saved_view = view
        self.close()

    def _delete(self, *_):
        self.app.confirm_delete_custom_agenda(self.window, self.original, then=self.close)


class AgendaChooser(Adw.Dialog):
    def __init__(self, app, window):
        super().__init__(title="Choose Agenda", content_width=380, content_height=360)
        self.app, self.window = app, window
        self.options = {"a": None, **{view.key: view for view in app.custom_agendas}}
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(Adw.HeaderBar())
        rows = Gtk.ListBox(css_classes=["boxed-list"], margin_start=12, margin_end=12,
                           margin_top=12, margin_bottom=12)
        for key, view in self.options.items():
            row = Adw.ActionRow(title=view.title if view else "Agenda", activatable=True,
                                use_markup=False)
            row.add_prefix(Gtk.Label(label=key, css_classes=["monospace", "dim-label"]))
            row.connect("activated", lambda _row, selected=key: self.activate(selected))
            rows.append(row)
        content.append(Gtk.ScrolledWindow(child=rows, vexpand=True))
        self.set_child(content)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)

    def activate(self, key):
        if key not in self.options:
            return False
        self.close()
        self.app.show_weekly_agenda(self.window, custom=self.options[key])
        return True

    def _key(self, _controller, keyval, _keycode, state):
        if keyval == Gdk.KEY_Escape or (
                keyval == Gdk.KEY_g and state & Gdk.ModifierType.CONTROL_MASK):
            self.close()
            return True
        if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK
                    | Gdk.ModifierType.SUPER_MASK):
            return False
        char = Gdk.keyval_to_unicode(keyval)
        return self.activate(chr(char)) if char else False
