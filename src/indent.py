"""Display-only Org indentation and heading folding for editor buffers."""

from gi.repository import GLib, Gtk, Pango

import re

import org


class OrgFold:
    """Tab cycles subtrees; #+STARTUP sets overview/content/showall/showeverything."""

    def __init__(self, view):
        self.view = view
        self.buffer = view.get_buffer()
        self.hidden = self.buffer.create_tag("org-fold-hidden", invisible=True)
        self.states = []
        self.mode = "showall"
        self._startup = None
        self._idle_id = 0
        self.indicators = {}
        self._indicator_positions = {}
        self._indicator_styles = {}
        self._cursor_offset = 0
        self._moving_cursor = False
        view.add_tick_callback(self._layout_indicators)
        self.buffer.connect("changed", lambda *_: self.queue_refresh())
        self.buffer.connect("notify::cursor-position", self._skip_hidden_cursor)
        view.connect("destroy", self._destroy)

    def _skip_hidden_cursor(self, *_):
        if self._moving_cursor:
            return
        cursor = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        offset = cursor.get_offset()
        if cursor.has_tag(self.hidden):
            forward = offset >= self._cursor_offset
            target = cursor.copy()
            if forward:
                target.forward_to_tag_toggle(self.hidden)
            else:
                target.backward_to_tag_toggle(self.hidden)
                target.backward_char()
            if target.is_end() or target.has_tag(self.hidden):
                target = cursor.copy()
                target.backward_to_tag_toggle(self.hidden)
                target.backward_char()
            self._moving_cursor = True
            if self.buffer.get_has_selection():
                self.buffer.move_mark(self.buffer.get_insert(), target)
            else:
                self.buffer.place_cursor(target)
            self._moving_cursor = False
            offset = target.get_offset()
        self._cursor_offset = offset

    def _destroy(self, *_):
        if self._idle_id:
            GLib.source_remove(self._idle_id)
            self._idle_id = 0

    def _layout_indicators(self, *_):
        for line, label in self.indicators.items():
            end = self.buffer.get_iter_at_line(line)[1]
            if not end.ends_line():
                end.forward_to_line_end()
            while end.get_line_offset() > 0:
                previous = end.copy()
                previous.backward_char()
                if not previous.get_char().isspace():
                    break
                end = previous
            rect = self.view.get_iter_location(end)
            position = (rect.x, rect.y)
            if self._indicator_positions.get(line) != position:
                self.view.move_overlay(label, *position)
                self._indicator_positions[line] = position
            last = end.copy()
            last.backward_char()
            color = None
            weight = Pango.Weight.NORMAL
            for tag in sorted(last.get_tags(), key=lambda tag: tag.get_priority()):
                if tag.props.foreground_set:
                    color = tag.props.foreground_rgba
                if tag.props.weight_set:
                    weight = tag.props.weight
            style = (color.to_string() if color else None, weight)
            if self._indicator_styles.get(line) != style:
                attributes = Pango.AttrList()
                if color:
                    attributes.insert(Pango.attr_foreground_new(
                        round(color.red * 65535), round(color.green * 65535),
                        round(color.blue * 65535)))
                attributes.insert(Pango.attr_weight_new(weight))
                label.set_attributes(attributes)
                self._indicator_styles[line] = style
        return GLib.SOURCE_CONTINUE

    def queue_refresh(self):
        if not self._idle_id:
            self._idle_id = GLib.idle_add(self.refresh)

    def reset(self):
        self._clear_states()
        self._startup = None
        self.mode = "showall"
        self.refresh()

    def _clear_states(self):
        for mark, _state in self.states:
            self.buffer.delete_mark(mark)
        self.states = []

    def _headings(self):
        lines = self.buffer.get_text(*self.buffer.get_bounds(), True).split("\n")
        headings = []
        in_block = False
        for i, line in enumerate(lines):
            if in_block:
                in_block = not org.BLOCK_END_RE.match(line)
            elif org.BLOCK_START_RE.match(line):
                in_block = True
            elif heading := org.parse_heading(line):
                headings.append((i, heading[0]))
        return lines, headings

    def cycle(self, global_cycle=False):
        self.refresh()
        lines, headings = self._headings()
        if global_cycle:
            self._clear_states()
            modes = ("overview", "content", "showall")
            self.mode = modes[(modes.index(self.mode) + 1) % 3] if self.mode in modes else "overview"
        else:
            line = self.buffer.get_iter_at_mark(self.buffer.get_insert()).get_line()
            if not any(i == line for i, _level in headings):
                return False
            level = next(depth for i, depth in headings if i == line)
            end = next((i for i, depth in headings if i > line and depth <= level), len(lines))
            for entry in list(self.states):
                mark, _state = entry
                if line < self.buffer.get_iter_at_mark(mark).get_line() < end:
                    self.states.remove(entry)
                    self.buffer.delete_mark(mark)
            existing = next(((mark, state) for mark, state in self.states
                             if self.buffer.get_iter_at_mark(mark).get_line() == line), None)
            if existing:
                mark, state = existing
                self.states.remove(existing)
                self.buffer.delete_mark(mark)
                state = {"folded": "children", "children": "subtree", "subtree": "folded"}[state]
            else:
                state = "children" if self.mode == "overview" else "folded"
            mark = self.buffer.create_mark(None, self.buffer.get_iter_at_line(line)[1], True)
            self.states.append((mark, state))
        self.refresh()
        cursor = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        if cursor.has_tag(self.hidden):
            line = cursor.get_line()
            visible = [i for i, _level in headings if i <= line
                       and not self.buffer.get_iter_at_line(i)[1].has_tag(self.hidden)]
            if visible:
                self.buffer.place_cursor(self.buffer.get_iter_at_line(visible[-1])[1])
        return True

    def reveal(self, line):
        self.refresh()
        _lines, headings = self._headings()
        ancestors = []
        for i, level in headings:
            if i > line:
                break
            while ancestors and ancestors[-1][1] >= level:
                ancestors.pop()
            ancestors.append((i, level))
        for i, _level in ancestors:
            for entry in list(self.states):
                if self.buffer.get_iter_at_mark(entry[0]).get_line() == i:
                    self.states.remove(entry)
                    self.buffer.delete_mark(entry[0])
            mark = self.buffer.create_mark(None, self.buffer.get_iter_at_line(i)[1], True)
            self.states.append((mark, "subtree"))
        self.refresh()

    def refresh(self):
        if self._idle_id:
            GLib.source_remove(self._idle_id)
            self._idle_id = 0
        lines, headings = self._headings()
        options = [word.lower() for line in lines
                   if re.match(r"^\s*#\+STARTUP:", line, re.IGNORECASE)
                   for word in line.split(":", 1)[1].split()
                   if word.lower() in ("overview", "content", "showall", "showeverything")]
        startup = options[-1] if options else "showall"
        if startup != self._startup:
            self._startup = startup
            self.mode = startup
            self._clear_states()
        hidden = set()
        if self.mode in ("overview", "content"):
            visible = {i for i, level in headings if self.mode == "content" or level == 1}
            hidden = set(range(len(lines))) - visible
            if headings:
                hidden.difference_update(range(headings[0][0]))
        for mark, state in self.states:
            line = self.buffer.get_iter_at_mark(mark).get_line()
            index = next((j for j, (i, _) in enumerate(headings) if i == line), None)
            if index is None:
                continue
            level = headings[index][1]
            end = next((i for i, depth in headings[index + 1:] if depth <= level), len(lines))
            hidden.difference_update(range(line + 1, end))
            if state == "folded":
                hidden.update(range(line + 1, end))
            elif state == "children":
                children = [(i, depth) for i, depth in headings[index + 1:] if i < end]
                child_level = min((depth for _, depth in children), default=level + 1)
                visible = {i for i, depth in children if depth == child_level}
                hidden.update(set(range(line + 1, end)) - visible)
        self.buffer.remove_tag(self.hidden, *self.buffer.get_bounds())
        hidden_lines = sorted(hidden)
        index = 0
        while index < len(hidden_lines):
            first = last = hidden_lines[index]
            index += 1
            while index < len(hidden_lines) and hidden_lines[index] == last + 1:
                last = hidden_lines[index]
                index += 1
            start = self.buffer.get_iter_at_line(first)[1]
            end = (self.buffer.get_iter_at_line(last + 1)[1]
                   if last + 1 < len(lines) else self.buffer.get_end_iter())
            self.buffer.apply_tag(self.hidden, start, end)
        indicator_lines = {i for i, _level in headings
                           if i not in hidden and i + 1 in hidden}
        for line in self.indicators.keys() - indicator_lines:
            label = self.indicators.pop(line)
            label.unparent()
            self._indicator_positions.pop(line, None)
            self._indicator_styles.pop(line, None)
        for line in indicator_lines - self.indicators.keys():
            label = Gtk.Label(label="...", can_target=False,
                              css_classes=["fold-indicator"])
            self.indicators[line] = label
            self.view.add_overlay(label, 0, 0)
        self._layout_indicators()
        return GLib.SOURCE_REMOVE


class OrgIndent:
    def __init__(self, view, enabled):
        self.view = view
        self.buffer = view.get_buffer()
        self.enabled = enabled
        self._idle_id = 0
        self._margins = {}
        self._hidden = self.buffer.create_tag(None, invisible=True)
        self.buffer.connect("changed", lambda *_: self.queue_refresh())
        self.buffer.connect("apply-tag", self._fold_changed)
        self.buffer.connect("remove-tag", self._fold_changed)
        view.connect("destroy", self._on_destroy)
        self.queue_refresh()

    def _fold_changed(self, _buffer, tag, *_):
        if tag.props.name == "org-fold-hidden":
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
        folded = self.buffer.get_tag_table().lookup("org-fold-hidden")
        for i, (columns, stars) in enumerate(org.indent_layout(lines)):
            a = self.buffer.get_iter_at_line(i)[1]
            if folded is not None and a.has_tag(folded):
                continue
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
