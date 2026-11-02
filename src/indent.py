"""Display-only Org indentation and heading folding for editor buffers."""

from gi.repository import GLib, Gtk, GtkSource, Pango

import re

import org


def _set_pointer_hover(view, owner, active):
    owners = getattr(view, "_org_pointer_owners", set())
    if active:
        owners.add(owner)
    else:
        owners.discard(owner)
    view._org_pointer_owners = owners
    view.set_cursor_from_name("pointer" if owners else "text")


class OrgLineNumbers(GtkSource.GutterRendererText):
    """Keep native gutter selection behavior while skipping invisible Org lines."""

    def __init__(self, view):
        super().__init__(xalign=1, xpad=6, css_classes=["dim-label"])
        self.view = view
        self.native = None
        self._size = None
        self.gutter = view.get_gutter(Gtk.TextWindowType.LEFT)
        self.gutter.insert(self, -31)
        view.connect("notify::show-line-numbers", self._sync)
        view.add_tick_callback(self._resize)
        self._sync()

    def _sync(self, *_):
        child = self.gutter.get_first_child()
        while child is not None:
            if child is not self and child.__gtype__.name == "GtkSourceGutterRendererLines":
                self.native = child
                child.set_visible(False)
            child = child.get_next_sibling()
        self.set_visible(self.view.get_show_line_numbers())

    def _resize(self, *_):
        if self.get_visible():
            width, _height = self.measure(str(self.view.get_buffer().get_line_count()))
            if width != self._size:
                self._size = width
                self.set_size_request(width + 12, -1)
        return GLib.SOURCE_CONTINUE

    def do_snapshot_line(self, snapshot, lines, line):
        iterator = lines.get_iter_at_line(line)
        folded = iterator.get_buffer().get_tag_table().lookup("org-fold-hidden")
        if folded is not None and iterator.has_tag(folded):
            return
        self.set_text(str(line + 1), -1)
        GtkSource.GutterRendererText.do_snapshot_line(self, snapshot, lines, line)

    def do_query_activatable(self, iterator, area):
        return self.native is not None and self.native.query_activatable(iterator, area)

    def do_activate(self, iterator, area, button, state, n_presses):
        if self.native is not None:
            self.native.emit("activate", iterator, area, button, state, n_presses)


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
        self._visual_cursor = None
        self._visual_cursor_offset = 0
        view.add_tick_callback(self._layout_indicators)
        self.buffer.connect("changed", lambda *_: self.queue_refresh())
        self.buffer.connect("notify::cursor-position", self._skip_hidden_cursor)
        self.buffer.connect("mark-set", self._skip_hidden_visual_cursor)
        view.connect("destroy", self._destroy)

    def track_visual_cursor(self, active):
        if not active:
            self._visual_cursor = None
            return
        if self._visual_cursor and not self._visual_cursor.get_deleted():
            return
        cursor = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        # GtkSource visual modes move a private right-gravity mark, not "insert".
        candidates = []
        for iterator in (cursor, *self.buffer.get_selection_bounds()):
            start = iterator.copy()
            start.set_line_offset(0)
            while True:
                candidates.extend(start.get_marks())
                if start.ends_line() or not start.forward_char():
                    break
        self._visual_cursor = next(
            (mark for mark in candidates
             if mark.get_name() is None and not mark.get_left_gravity()), None)
        if self._visual_cursor:
            self._visual_cursor_offset = self.buffer.get_iter_at_mark(
                self._visual_cursor).get_offset()

    def _skip_hidden_visual_cursor(self, _buffer, iterator, mark):
        if self._moving_cursor or mark is not self._visual_cursor:
            return
        offset = iterator.get_offset()
        if iterator.has_tag(self.hidden):
            target = iterator.copy()
            if offset >= self._visual_cursor_offset:
                target.forward_to_tag_toggle(self.hidden)
            else:
                target.backward_char()
                if target.has_tag(self.hidden):
                    target.backward_to_tag_toggle(self.hidden)
                    target.backward_char()
                target.set_line_offset(0)
            if target.is_end() or target.has_tag(self.hidden):
                target = iterator.copy()
                target.backward_char()
                if target.has_tag(self.hidden):
                    target.backward_to_tag_toggle(self.hidden)
                    target.backward_char()
                target.set_line_offset(0)
            self._moving_cursor = True
            self.buffer.move_mark(mark, target)
            self._moving_cursor = False
            offset = target.get_offset()
        self._visual_cursor_offset = offset

    def _skip_hidden_cursor(self, *_):
        if self._moving_cursor:
            return
        cursor = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        offset = cursor.get_offset()
        if cursor.has_tag(self.hidden):
            # A visual endpoint includes the folded body of its last selected heading,
            # even when the private visual cursor is moving upward.
            forward = self._visual_cursor is not None or offset >= self._cursor_offset
            target = cursor.copy()
            if forward:
                target.forward_to_tag_toggle(self.hidden)
            else:
                target.backward_to_tag_toggle(self.hidden)
                target.backward_char()
            if ((target.is_end() and self._visual_cursor is None)
                    or target.has_tag(self.hidden)):
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
            start = self.buffer.get_iter_at_line(line)[1]
            end = start.copy()
            if not end.ends_line():
                end.forward_to_line_end()
            text = self.buffer.get_text(start, end, True)
            match = org.TAGS_RE.search(text.rstrip())
            if match:
                end = self.buffer.get_iter_at_line_offset(line, match.start())[1]
            while end.get_line_offset() > 0:
                previous = end.copy()
                previous.backward_char()
                if not previous.get_char().isspace():
                    break
                end = previous
            last = end.copy()
            last.backward_char()
            rect = self.view.get_iter_location(last if match else end)
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
            x = rect.x
            if match:
                # Measure the unstretched title; separator spacing can affect its last glyph.
                title = self.buffer.get_text(start, end, False)
                layout = self.view.create_pango_layout(title)
                layout.set_attributes(label.get_attributes())
                x = self.view.get_iter_location(start).x + layout.get_pixel_size()[0]
            position = (x, rect.y)
            if self._indicator_positions.get(line) != position:
                self.view.move_overlay(label, *position)
                self._indicator_positions[line] = position
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

    def toggle(self, line):
        self.refresh()
        lines, headings = self._headings()
        level = next((depth for i, depth in headings if i == line), None)
        if level is None:
            return False
        end = next((i for i, depth in headings if i > line and depth <= level), len(lines))
        children = [(i, depth) for i, depth in headings if line < i < end]
        child_level = min((depth for _, depth in children), default=level + 1)
        child_lines = {i for i, depth in children if depth == child_level}
        visible = child_lines or set(range(line + 1, end))
        if lines[-1] == "":
            visible.discard(len(lines) - 1)
        folded = bool(visible) and all(
            self.buffer.get_iter_at_line(i)[1].has_tag(self.hidden) for i in visible)
        for entry in list(self.states):
            mark, _state = entry
            if line <= self.buffer.get_iter_at_mark(mark).get_line() < end:
                self.states.remove(entry)
                self.buffer.delete_mark(mark)
        self.buffer.place_cursor(self.buffer.get_iter_at_line(line)[1])
        mark = self.buffer.create_mark(None, self.buffer.get_iter_at_line(line)[1], True)
        self.states.append((mark, ("children" if child_lines else "subtree")
                            if folded else "folded"))
        self.refresh()
        return True

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
            hidden_lines = {i for i in range(line + 1, end)
                            if self.buffer.get_iter_at_line(i)[1].has_tag(self.hidden)}
            children = [(i, depth) for i, depth in headings if line < i < end]
            child_level = min((depth for _, depth in children), default=level + 1)
            child_lines = {i for i, depth in children if depth == child_level}
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
                state = "children" if hidden_lines else "folded"
            if state == "children" and (
                    not child_lines
                    or hidden_lines == set(range(line + 1, end)) - child_lines):
                state = "subtree"
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


class OrgStartupEdit:
    """Place an edit button beside STARTUP directives outside literal blocks."""

    def __init__(self, view, edit):
        self.view = view
        self.edit = edit
        self.buttons = {}
        self._pool = []
        self._dirty = True
        self._pointer = None
        self._regions = {}
        motion = Gtk.EventControllerMotion(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        motion.connect("enter", self._motion)
        motion.connect("motion", self._motion)
        motion.connect("leave", self._leave)
        view.add_controller(motion)
        click = Gtk.GestureClick(button=1, propagation_phase=Gtk.PropagationPhase.CAPTURE)
        click.connect("pressed", self._pressed)
        view.add_controller(click)
        view.get_buffer().connect("changed", self._changed)
        view.add_tick_callback(self._layout)

    def _changed(self, *_):
        self._dirty = True

    def _clicked(self, button):
        line = next(line for line, widget in self.buttons.items() if widget is button)
        self.edit(line, button)

    def _pressed(self, gesture, _n_press, x, y):
        for line, (left, top, right, bottom) in self._regions.items():
            if left <= x < right and top <= y < bottom:
                gesture.set_state(Gtk.EventSequenceState.CLAIMED)
                self.edit(line, self.buttons[line])
                return

    def _motion(self, _controller, x, y):
        self._pointer = (x, y)
        self._update_hover()

    def _leave(self, *_):
        self._pointer = None
        self._update_hover()

    def _update_hover(self):
        hovering = False
        for line, button in self.buttons.items():
            active = False
            if self._pointer is not None and line in self._regions:
                x, y = self._pointer
                left, top, right, bottom = self._regions[line]
                active = left <= x < right and top <= y < bottom
            hovering = hovering or active
            button.set_opacity(1 if active else 0)
            button.set_can_target(active)
        _set_pointer_hover(self.view, self, hovering)

    def _layout(self, *_):
        buffer = self.view.get_buffer()
        if self._dirty:
            self._dirty = False
            for button in self._pool:
                button.set_visible(False)
            self.buttons.clear()
            in_block = False
            for line, text in enumerate(buffer.get_text(*buffer.get_bounds(), True).split("\n")):
                if in_block:
                    in_block = not org.BLOCK_END_RE.match(text)
                    continue
                if org.BLOCK_START_RE.match(text):
                    in_block = True
                    continue
                if not org.STARTUP_RE.match(text):
                    continue
                index = len(self.buttons)
                if index == len(self._pool):
                    button = Gtk.Button(icon_name="document-edit-symbolic",
                                        tooltip_text="Edit startup folding",
                                        css_classes=["flat", "tag-edit-button"])
                    button.set_cursor_from_name("pointer")
                    button.set_opacity(0)
                    button.set_can_target(False)
                    button.connect("clicked", self._clicked)
                    self.view.add_overlay(button, 0, 0)
                    self._pool.append(button)
                self.buttons[line] = self._pool[index]
        folded = buffer.get_tag_table().lookup("org-fold-hidden")
        self._regions.clear()
        for line, button in self.buttons.items():
            end = buffer.get_iter_at_line(line)[1]
            button.set_visible(folded is None or not end.has_tag(folded))
            if not button.get_visible():
                continue
            first = self.view.get_iter_location(end)
            if not end.ends_line():
                end.forward_to_line_end()
            rect = self.view.get_iter_location(end)
            self.view.move_overlay(button, rect.x + 6, rect.y)
            x, y = self.view.buffer_to_window_coords(
                Gtk.TextWindowType.WIDGET, first.x, rect.y)
            right, _ = self.view.buffer_to_window_coords(
                Gtk.TextWindowType.WIDGET, rect.x + 6 + max(button.get_width(), 24), rect.y)
            self._regions[line] = (x, y, right, y + max(rect.height, 24))
        self._update_hover()
        return GLib.SOURCE_CONTINUE


class OrgTodoInteraction:
    """Handle heading, task keyword, checkbox, and timestamp pointer interactions."""

    def __init__(self, view, cycle, toggle_checkbox, toggle_heading, edit_timestamp):
        self.view = view
        self.cycle = cycle
        self.toggle_checkbox = toggle_checkbox
        self.toggle_heading = toggle_heading
        self.edit_timestamp = edit_timestamp
        self._hovering = False
        motion = Gtk.EventControllerMotion(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        motion.connect("enter", self._motion)
        motion.connect("motion", self._motion)
        motion.connect("leave", self._leave)
        view.add_controller(motion)
        click = Gtk.GestureClick(button=1, propagation_phase=Gtk.PropagationPhase.CAPTURE)
        click.connect("pressed", self._pressed)
        view.add_controller(click)

    def _keyword_at(self, x, y):
        target = self._target_at(x, y)
        return target[0] if target is not None and target[1] != "heading" else None

    def _target_at(self, x, y):
        bx, by = self.view.window_to_buffer_coords(Gtk.TextWindowType.WIDGET, int(x), int(y))
        buffer = self.view.get_buffer()
        _ok, iterator = self.view.get_iter_at_location(bx, by)
        start = buffer.get_iter_at_line(iterator.get_line())[1]
        folded = buffer.get_tag_table().lookup("org-fold-hidden")
        if folded is not None and start.has_tag(folded):
            return None
        end = start.copy()
        if not end.ends_line():
            end.forward_to_line_end()
        text = buffer.get_text(start, end, True)
        for timestamp in org.SINGLE_TS_RE.finditer(text):
            if org.Timestamp.parse(timestamp[0]) is not None and self._span_contains(
                    start.get_line(), timestamp.start(), timestamp[0], bx, by):
                return start.get_offset() + timestamp.start(), "timestamp"
        match = org.HEADING_KEYWORD_RE.match(text)
        if match is not None:
            if match[2] is not None and self._span_contains(
                    start.get_line(), match.start(2), match[2], bx, by):
                return start.get_line(), "keyword"
            offset, text = match.start(3), match[3] or ""
            if offset < 0:
                return None
            if priority := org.PRIORITY_RE.match(text):
                offset += priority.end()
                text = text[priority.end():]
            if tags := org.TAGS_RE.search(text):
                text = text[:tags.start()]
            kind = "heading"
        elif checkbox := org.CHECKBOX_RE.match(text):
            offset = checkbox.start(2) - 1
            text = text[offset:checkbox.end()]
            kind = "checkbox"
        else:
            return None
        if self._span_contains(start.get_line(), offset, text.rstrip(), bx, by):
            return start.get_line(), kind
        return None

    def _span_contains(self, line, offset, text, bx, by):
        if not text:
            return False
        keyword = self.view.get_buffer().get_iter_at_line_offset(line, offset)[1]
        rect = self.view.get_iter_location(keyword)
        layout = self.view.create_pango_layout(text)
        attributes = Pango.AttrList()
        for tag in sorted(keyword.get_tags(), key=lambda tag: tag.get_priority()):
            if tag.props.weight_set:
                attributes.change(Pango.attr_weight_new(tag.props.weight))
        layout.set_attributes(attributes)
        return (rect.x <= bx < rect.x + layout.get_pixel_size()[0]
                and rect.y <= by < rect.y + rect.height)

    def _motion(self, _controller, x, y):
        hovering = self._target_at(x, y) is not None
        if hovering or self._hovering:
            _set_pointer_hover(self.view, self, hovering)
        self._hovering = hovering

    def _leave(self, *_):
        if self._hovering:
            _set_pointer_hover(self.view, self, False)
        self._hovering = False

    def _pressed(self, gesture, _n_press, x, y):
        target = self._target_at(x, y)
        if target is not None:
            line, kind = target
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            if kind == "timestamp":
                self.edit_timestamp(line)
            elif kind == "checkbox":
                self.toggle_checkbox(line)
            elif kind == "heading":
                self.toggle_heading(line)
            else:
                self.cycle(line)


class OrgTagAlign:
    """Stretch heading tag separators to the viewport edge without editing text."""

    BUTTON_SPACE = 28

    def __init__(self, view, edit_tags):
        self.view = view
        self.edit_tags = edit_tags
        self.buttons = {}
        self._button_pool = []
        self._pointer = None
        self._hover_regions = {}
        self._tag_starts = {}
        self._hovering_tags = False
        motion = Gtk.EventControllerMotion(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        motion.connect("enter", self._on_motion)
        motion.connect("motion", self._on_motion)
        motion.connect("leave", self._on_leave)
        view.add_controller(motion)
        click = Gtk.GestureClick(button=1, propagation_phase=Gtk.PropagationPhase.CAPTURE)
        click.connect("pressed", self._on_tag_pressed)
        view.add_controller(click)
        css = Gtk.CssProvider()
        css.load_from_string(
            ".tag-edit-button { padding: 0; min-width: 24px; min-height: 24px; }")
        Gtk.StyleContext.add_provider_for_display(
            view.get_display(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.buffer = view.get_buffer()
        self._tags = []
        self._lines = []
        self._dirty = True
        self._layout_size = None
        self.buffer.connect("changed", self._changed)
        view.add_tick_callback(self._layout)

    def _changed(self, *_):
        self._dirty = True

    def _layout(self, *_):
        layout_size = (self.view.get_visible_rect().width,
                       self.view.create_pango_layout(" ").get_pixel_size(),
                       self.view.get_left_margin(), self.view.get_right_margin())
        if layout_size != self._layout_size:
            self._layout_size = layout_size
            self._dirty = True
        if self._dirty:
            self._dirty = False
            for button in self._button_pool:
                button.set_visible(False)
            self.buttons.clear()
            self._tag_starts.clear()
            for tag in self._tags:
                self.buffer.remove_tag(tag, *self.buffer.get_bounds())
            self._lines = []
            text = self.buffer.get_text(*self.buffer.get_bounds(), True)
            for line, text in enumerate(text.split("\n")):
                if org.parse_heading(text) is None:
                    continue
                match = org.TAGS_RE.search(text.rstrip())
                if match is None:
                    continue
                count = len(match[0]) - len(match[1])
                index = len(self._lines)
                if index == len(self._tags):
                    self._tags.append(self.buffer.create_tag(None, letter_spacing=0))
                tag = self._tags[index]
                tag.props.letter_spacing = 0
                start = self.buffer.get_iter_at_line_offset(line, match.start())[1]
                end = self.buffer.get_iter_at_line_offset(line, match.start() + count)[1]
                self.buffer.apply_tag(tag, start, end)
                self._lines.append((line, len(text.rstrip()), count, tag))
                self._tag_starts[line] = match.start() + count
                if index == len(self._button_pool):
                    button = Gtk.Button(icon_name="document-edit-symbolic",
                                        tooltip_text="Edit heading tags",
                                        css_classes=["flat", "tag-edit-button"])
                    button.set_size_request(24, 24)
                    button.set_cursor_from_name("pointer")
                    button.set_opacity(0)
                    button.set_can_target(False)
                    button.connect("clicked", self._edit_clicked)
                    self.view.add_overlay(button, 0, 0)
                    self._button_pool.append(button)
                button = self._button_pool[index]
                self.buttons[line] = button
        # Leave room for TextView's end-of-line caret in the scrollable extent.
        right = (self.view.get_visible_rect().width - self.view.get_right_margin()
                 - 2 - self.BUTTON_SPACE)
        self._hover_regions.clear()
        for line, end, count, tag in self._lines:
            iterator = self.buffer.get_iter_at_line_offset(line, end)[1]
            folded = self.buffer.get_tag_table().lookup("org-fold-hidden")
            if folded is not None and iterator.has_tag(folded):
                self.buttons[line].set_visible(False)
                continue
            button = self.buttons[line]
            button.set_visible(True)
            x = self.view.get_iter_location(iterator).x
            delta = right - x
            if abs(delta) > 1:
                spacing = max(0, tag.props.letter_spacing + round(delta * Pango.SCALE / count))
                if spacing != tag.props.letter_spacing:
                    tag.props.letter_spacing = spacing
            rect = self.view.get_iter_location(iterator)
            self.view.move_overlay(button, rect.x + 4, rect.y)
            first = self.view.get_iter_location(self.buffer.get_iter_at_line_offset(
                line, self._tag_starts[line])[1])
            x, y = self.view.buffer_to_window_coords(
                Gtk.TextWindowType.WIDGET, first.x, rect.y)
            last_x, _ = self.view.buffer_to_window_coords(
                Gtk.TextWindowType.WIDGET, rect.x + 4 + max(button.get_width(), 24), rect.y)
            self._hover_regions[line] = (x, y, last_x, y + max(rect.height, 24))
        self._update_hover()
        return GLib.SOURCE_CONTINUE

    def _on_motion(self, _controller, x, y):
        self._pointer = (x, y)
        self._update_hover()

    def _on_leave(self, *_):
        self._pointer = None
        self._update_hover()

    def _on_tag_pressed(self, gesture, _n_press, x, y):
        for line, (left, top, right, bottom) in self._hover_regions.items():
            if left <= x < right and top <= y < bottom:
                gesture.set_state(Gtk.EventSequenceState.CLAIMED)
                self.edit_tags(line)
                return

    def _update_hover(self):
        hovered = None
        if self._pointer is not None:
            x, y = self._pointer
            hovered = next((line for line, (left, top, right, bottom)
                            in self._hover_regions.items()
                            if left <= x < right and top <= y < bottom), None)
        for line, button in self.buttons.items():
            active = line == hovered
            button.set_opacity(1 if active else 0)
            button.set_can_target(active)
        hovering = hovered is not None
        if hovering != self._hovering_tags:
            _set_pointer_hover(self.view, self, hovering)
            self._hovering_tags = hovering

    def _edit_clicked(self, button):
        line = next(line for line, widget in self.buttons.items() if widget is button)
        self.edit_tags(line)


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
