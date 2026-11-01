"""Display-only Org indentation for editor buffers."""

from gi.repository import GLib

import org


class OrgIndent:
    def __init__(self, view, enabled):
        self.view = view
        self.buffer = view.get_buffer()
        self.enabled = enabled
        self._idle_id = 0
        self._margins = {}
        self._hidden = self.buffer.create_tag(None, invisible=True)
        self.buffer.connect("changed", lambda *_: self.queue_refresh())
        view.connect("destroy", self._on_destroy)
        self.queue_refresh()

    def _on_destroy(self, *_):
        if self._idle_id:
            GLib.source_remove(self._idle_id)
            self._idle_id = 0

    def set_enabled(self, enabled):
        self.enabled = enabled
        self.queue_refresh()

    def queue_refresh(self):
        if not self._idle_id:
            self._idle_id = GLib.idle_add(self._refresh)

    def _refresh(self):
        self._idle_id = 0
        start, end = self.buffer.get_bounds()
        for tag in (*self._margins.values(), self._hidden):
            self.buffer.remove_tag(tag, start, end)
        if not self.enabled:
            return GLib.SOURCE_REMOVE

        width = self.view.create_pango_layout(" ").get_pixel_size()[0]
        lines = self.buffer.get_text(start, end, True).split("\n")
        for i, (columns, stars) in enumerate(org.indent_layout(lines)):
            a = self.buffer.get_iter_at_line(i)[1]
            b = self.buffer.get_iter_at_line(i + 1)[1] if i + 1 < len(lines) else end
            if columns:
                if columns not in self._margins:
                    self._margins[columns] = self.buffer.create_tag(None)
                tag = self._margins[columns]
                tag.set_property("left-margin", self.view.get_left_margin() + columns * width)
                self.buffer.apply_tag(tag, a, b)
            if stars:
                self.buffer.apply_tag(
                    self._hidden, a, self.buffer.get_iter_at_line_offset(i, stars)[1])
        return GLib.SOURCE_REMOVE
