"""Org-mode parsing, date handling and text edits. Free of GTK so it can be unit tested."""

import calendar
import datetime as dt
import os
import re
from dataclasses import dataclass, field, replace

TODO_KEYWORDS = ("TODO",)
DONE_KEYWORDS = ("DONE",)
ALL_KEYWORDS = TODO_KEYWORDS + DONE_KEYWORDS

DEFAULT_DEADLINE_WARNING_DAYS = 14

DAY_ABBR = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTH_ABBR = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August",
               "September", "October", "November", "December")

_KW = "|".join(re.escape(k) for k in ALL_KEYWORDS)
HEADING_RE = re.compile(r"^(\*+)(?:[ \t]+(.*?))?[ \t]*$")
HEADING_KEYWORD_RE = re.compile(rf"^(\*+)(?:[ \t]+(?:({_KW})(?=[ \t]|$)[ \t]*)?(.*))?$")
PRIORITY_RE = re.compile(r"^\[#([A-Z0-9])\][ \t]*")
TAGS_RE = re.compile(r"[ \t]+(:[\w@#%:]+:)$")
PLANNING_ITEM_RE = re.compile(r"(SCHEDULED|DEADLINE|CLOSED):[ \t]*(<[^>\n]*>|\[[^\]\n]*\])")
PLANNING_LINE_RE = re.compile(
    r"^([ \t]*)(?:(?:SCHEDULED|DEADLINE|CLOSED):[ \t]*(?:<[^>\n]*>|\[[^\]\n]*\])[ \t]*)+$")
ACTIVE_TS_RE = re.compile(r"<(\d{4}-\d{2}-\d{2}[^>\n]*)>")
ANY_TS_RE = re.compile(r"<\d{4}-\d{2}-\d{2}[^>\n]*>(?:--<[^>\n]*>)?|\[\d{4}-\d{2}-\d{2}[^\]\n]*\]")
LINK_RE = re.compile(r"\[\[([^\]\n]+)\](?:\[([^\]\n]+)\])?\]")
DRAWER_START_RE = re.compile(r"^[ \t]*:[\w-]+:[ \t]*$")
DRAWER_END_RE = re.compile(r"^[ \t]*:END:[ \t]*$", re.IGNORECASE)
BLOCK_START_RE = re.compile(r"^[ \t]*#\+BEGIN_", re.IGNORECASE)
BLOCK_END_RE = re.compile(r"^[ \t]*#\+END_", re.IGNORECASE)

def indent_layout(lines):
    """Yield (visual indent columns, hidden heading stars) without changing text."""
    depth = 0
    in_block = False
    for line in lines:
        if in_block:
            in_block = not BLOCK_END_RE.match(line)
            yield depth * 2, 0
        elif BLOCK_START_RE.match(line):
            in_block = True
            yield depth * 2, 0
        elif heading := HEADING_RE.match(line):
            depth = len(heading[1])
            yield (depth - 1) * 2, depth - 1
        else:
            yield depth * 2, 0


def new_item_prefix(line):
    """Return the prefix for a new heading or list item below this line."""
    heading = HEADING_RE.match(line)
    if heading:
        return heading[1] + " "
    item = re.match(r"^([ \t]*)([-+*]|\d+[.)])[ \t]+(?:\[[ Xx-]\][ \t]*)?", line)
    if item is None:
        return None
    indent, bullet = item[1], item[2]
    if bullet[0].isdigit():
        bullet = str(int(bullet[:-1]) + 1) + bullet[-1]
    checkbox = "[ ] " if re.match(r"^[ \t]*(?:[-+*]|\d+[.)])[ \t]+\[[ Xx-]\]", line) else ""
    return indent + bullet + " " + checkbox

def toggle_checkbox(line):
    """Toggle a list checkbox, treating a partial checkbox as unchecked."""
    match = re.match(r"^([ \t]*(?:[-+*]|\d+[.)])[ \t]+\[)([ Xx-])(\])", line)
    if match is None:
        return None
    state = " " if match[2] in ("X", "x") else "X"
    return line[:match.start(2)] + state + line[match.end(2):]

_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})(?:-(\d{1,2}):(\d{2}))?$")
_AMPM_RE = re.compile(r"^(\d{1,2})(?::(\d{2}))?(am|pm)$")
_REPEATER_RE = re.compile(r"^(\+\+|\.\+|\+)(\d+)([hdwmy])$")
_WARNING_RE = re.compile(r"^--?(\d+)([hdwmy])$")
_RELATIVE_RE = re.compile(r"^([+-])(\d+)([dwmy]?)$")


# ---------- Dates ----------

def shift_timestamp(text, offset, direction):
    """Return a timestamp replacement at the cursor, or None outside an active timestamp."""
    for match in ACTIVE_TS_RE.finditer(text):
        if not match.start() <= offset < match.end():
            continue
        timestamp = Timestamp.parse(match.group(0))
        if timestamp is None:
            return None
        relative = offset - match.start()
        for index, time_match in enumerate(re.finditer(r"\d{1,2}:\d{2}", match.group(0))):
            if time_match.start() <= relative < time_match.end():
                field_name = "time" if index == 0 else "end_time"
                value = getattr(timestamp, field_name)
                timestamp = replace(timestamp, **{
                    field_name: value.replace(hour=(value.hour + direction) % 24)})
                return match.start(), match.end(), timestamp.format()
        timestamp = timestamp.with_date(timestamp.date + dt.timedelta(days=direction))
        return match.start(), match.end(), timestamp.format()
    return None


def add_interval(date, n, unit):
    """Add n days/weeks/months/years ('h' counts whole days) to a date."""
    if unit == "d":
        return date + dt.timedelta(days=n)
    if unit == "w":
        return date + dt.timedelta(weeks=n)
    if unit == "h":
        return date + dt.timedelta(days=n // 24)
    if unit == "m":
        month_index = date.year * 12 + date.month - 1 + n
        year, month = divmod(month_index, 12)
        month += 1
    elif unit == "y":
        year, month = date.year + n, date.month
    else:
        raise ValueError(f"unknown unit {unit!r}")
    day = min(date.day, calendar.monthrange(year, month)[1])
    return dt.date(year, month, day)


def interval_days(n, unit):
    return {"h": n / 24, "d": n, "w": 7 * n, "m": 30 * n, "y": 365 * n}[unit]


def format_date(date, today=None, with_year=False):
    """English, locale independent: 'Thu 5 Nov' (+ year), or 'Today'/'Tomorrow'/'Yesterday'."""
    if today is not None:
        delta = (date - today).days
        if delta == 0:
            return "Today"
        if delta == 1:
            return "Tomorrow"
        if delta == -1:
            return "Yesterday"
    text = f"{DAY_ABBR[date.weekday()]} {date.day} {MONTH_ABBR[date.month - 1]}"
    if with_year or (today is not None and date.year != today.year):
        text += f" {date.year}"
    return text


@dataclass(frozen=True)
class Timestamp:
    date: dt.date
    time: dt.time | None = None
    end_time: dt.time | None = None
    repeater: tuple | None = None  # (mark, n, unit), mark in "+", "++", ".+"
    warning: tuple | None = None   # (n, unit)
    active: bool = True

    @classmethod
    def parse(cls, text):
        """Parse '<2026-11-05 Thu 10:00 +1w>' or '[2026-11-05 Thu]' (brackets optional)."""
        text = text.strip()
        active = True
        if text[:1] in "<[" and text[-1:] in ">]":
            active = text[0] == "<"
            text = text[1:-1]
        tokens = text.split()
        if not tokens:
            return None
        try:
            date = dt.date.fromisoformat(tokens[0])
        except ValueError:
            return None
        time = end_time = repeater = warning = None
        for tok in tokens[1:]:
            if m := _TIME_RE.match(tok):
                time = dt.time(int(m[1]) % 24, int(m[2]) % 60)
                if m[3]:
                    end_time = dt.time(int(m[3]) % 24, int(m[4]) % 60)
            elif m := _REPEATER_RE.match(tok):
                repeater = (m[1], int(m[2]), m[3])
            elif m := _WARNING_RE.match(tok):
                warning = (int(m[1]), m[2])
        return cls(date, time, end_time, repeater, warning, active)

    def format(self, brackets=True, day_name=True):
        parts = [self.date.isoformat()]
        if day_name:
            parts.append(DAY_ABBR[self.date.weekday()])
        if self.time:
            t = self.time.strftime("%H:%M")
            if self.end_time:
                t += "-" + self.end_time.strftime("%H:%M")
            parts.append(t)
        if self.repeater:
            parts.append(f"{self.repeater[0]}{self.repeater[1]}{self.repeater[2]}")
        if self.warning:
            parts.append(f"-{self.warning[0]}{self.warning[1]}")
        inner = " ".join(parts)
        if not brackets:
            return inner
        return f"<{inner}>" if self.active else f"[{inner}]"

    def with_date(self, date):
        return Timestamp(date, self.time, self.end_time, self.repeater, self.warning, self.active)

    def time_label(self):
        if not self.time:
            return ""
        label = self.time.strftime("%H:%M")
        if self.end_time:
            label += "–" + self.end_time.strftime("%H:%M")
        return label

    def describe(self, today=None):
        """Human readable summary, e.g. 'Thu 5 Nov 2026, 10:00, repeats every week'."""
        parts = [format_date(self.date, with_year=True)]
        if today is not None:
            rel = format_date(self.date, today)
            if rel in ("Today", "Tomorrow", "Yesterday"):
                parts[0] = f"{rel}, {parts[0]}"
        if self.time:
            parts.append(self.time_label())
        if self.repeater:
            mark, n, unit = self.repeater
            unit_name = {"h": "hour", "d": "day", "w": "week", "m": "month", "y": "year"}[unit]
            every = f"every {unit_name}" if n == 1 else f"every {n} {unit_name}s"
            how = {"+": "", "++": " (catching up)", ".+": " after completion"}[mark]
            parts.append(f"repeats {every}{how}")
        if self.warning:
            parts.append(f"warn {self.warning[0]}{self.warning[1]} before")
        return ", ".join(parts)

    def next_repeat(self, today):
        """Date after marking a repeating task done, following org's +, ++ and .+ semantics."""
        mark, n, unit = self.repeater
        if n <= 0:
            return self.date
        if mark == ".+":
            return add_interval(today, n, unit)
        if mark == "++":
            date = self.date
            for _ in range(100000):
                date = add_interval(date, n, unit)
                if date > today:
                    return date
            return date
        return add_interval(self.date, n, unit)

    def occurrences(self, start, end, from_date=None):
        """Dates in [start, end] on which this timestamp falls (expanding repeaters).

        Repeats are only projected from `from_date` (default: the base date) onwards.
        """
        base = self.date
        if not self.repeater or self.repeater[1] <= 0:
            return [base] if start <= base <= end else []
        _mark, n, unit = self.repeater
        if unit == "h":
            unit, n = "d", max(1, n // 24)
        lower = max(start, from_date or base)
        result = [base] if start <= base <= end else []
        k = 1
        if unit in "dw":
            step = n * (7 if unit == "w" else 1)
            k = max(1, (lower - base).days // step)
        while True:
            date = add_interval(base, k * n, unit)
            if date > end:
                break
            if date >= lower and date != base:
                result.append(date)
            k += 1
        return result


def parse_date_input(text, today):
    """Parse user input like 'tomorrow', '+3d', 'fri 10:00', '2026-11-05 +1w', '5.11.', 'nov 5'.

    Returns a Timestamp, or None when the input can't be understood.
    """
    tokens = text.lower().replace(",", " ").split()
    date = None
    explicit_date = False
    day = month = year = None
    time = end_time = repeater = warning = None

    for tok in tokens:
        if tok in ("today", "."):
            date, explicit_date = today, True
        elif tok in ("tomorrow", "tom"):
            date, explicit_date = today + dt.timedelta(days=1), True
        elif tok == "yesterday":
            date, explicit_date = today - dt.timedelta(days=1), True
        elif (wd := _weekday(tok)) is not None:
            if explicit_date:
                continue  # e.g. the "Thu" in "2026-11-05 Thu"
            date = today + dt.timedelta(days=(wd - today.weekday()) % 7)
            explicit_date = True
        elif (mo := _month(tok)) is not None:
            month = mo
        elif m := _TIME_RE.match(tok):
            if int(m[1]) > 23 or int(m[2]) > 59:
                return None
            time = dt.time(int(m[1]), int(m[2]))
            if m[3]:
                if int(m[3]) > 23 or int(m[4]) > 59:
                    return None
                end_time = dt.time(int(m[3]), int(m[4]))
        elif m := _AMPM_RE.match(tok):
            hour = int(m[1])
            if not 1 <= hour <= 12:
                return None
            hour = hour % 12 + (12 if m[3] == "pm" else 0)
            time = dt.time(hour, int(m[2] or 0))
        elif tok.startswith(("++", ".+")) and (m := _REPEATER_RE.match(tok)):
            repeater = (m[1], int(m[2]), m[3])
        elif m := _RELATIVE_RE.match(tok):
            sign, n, unit = m[1], int(m[2]), m[3] or "d"
            if explicit_date or day or month or year:
                if sign == "+":
                    repeater = ("+", n, unit)
                else:
                    warning = (n, unit)
            else:
                date = add_interval(today, n if sign == "+" else -n, unit)
                explicit_date = True
        elif m := _WARNING_RE.match(tok):
            warning = (int(m[1]), m[2])
        elif m := re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", tok):
            date = _safe_date(int(m[1]), int(m[2]), int(m[3]))
            if date is None:
                return None
            explicit_date = True
        elif m := re.match(r"^(\d{1,2})-(\d{1,2})$", tok):
            month, day = int(m[1]), int(m[2])
        elif m := re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{2}|\d{4})?$", tok):
            day, month = int(m[1]), int(m[2])
            if m[3]:
                year = int(m[3]) + (2000 if len(m[3]) == 2 else 0)
        elif re.match(r"^\d{4}$", tok):
            year = int(tok)
        elif m := re.match(r"^(\d{1,2})(?:st|nd|rd|th|\.)?$", tok):
            day = int(m[1])
        else:
            return None

    if day is not None or month is not None or year is not None:
        y = year or today.year
        mth = month or today.month
        d = day or 1
        date = _safe_date(y, mth, d)
        if date is None:
            return None
        if date < today and year is None:
            # Like org, prefer the future when the year (or month) was left out
            date = add_interval(date, 1, "y" if month is not None else "m")
            if day and date.day != day:
                return None

    if date is None:
        date = today
    return Timestamp(date, time, end_time if time else None, repeater, warning)


def _weekday(tok):
    for i, name in enumerate(DAY_NAMES):
        if len(tok) >= 2 and name.lower().startswith(tok) and (len(tok) >= 3 or tok in ("mo", "tu", "we", "th", "fr", "sa", "su")):
            return i
    return None


def _month(tok):
    tok = tok.rstrip(".")
    if len(tok) < 3:
        return None
    for i, name in enumerate(MONTH_NAMES):
        if name.lower().startswith(tok):
            return i + 1
    return None


def _safe_date(y, m, d):
    try:
        return dt.date(y, m, d)
    except ValueError:
        return None


# ---------- Document model ----------

@dataclass
class Entry:
    path: str | None
    line: int
    level: int
    keyword: str | None
    priority: str | None
    title: str
    tags: list = field(default_factory=list)
    heading_text: str = ""
    scheduled: Timestamp | None = None
    deadline: Timestamp | None = None
    closed: Timestamp | None = None
    timestamps: list = field(default_factory=list)
    timestamp_sources: list = field(default_factory=list)
    category: str | None = None

    @property
    def is_todo(self):
        return self.keyword in TODO_KEYWORDS

    @property
    def is_done(self):
        return self.keyword in DONE_KEYWORDS

    @property
    def display_title(self):
        return display_text(self.title)


def display_text(text):
    """Strip timestamps and render links as their description."""
    text = LINK_RE.sub(lambda m: m[2] or m[1], text)
    text = ANY_TS_RE.sub("", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def parse_heading(line):
    """Return (level, keyword, priority, title, tags) or None if `line` isn't a heading."""
    m = HEADING_KEYWORD_RE.match(line)
    if not m or not HEADING_RE.match(line):
        return None
    level, keyword, rest = len(m[1]), m[2], (m[3] or "").rstrip()
    priority = None
    if pm := PRIORITY_RE.match(rest):
        priority, rest = pm[1], rest[pm.end():]
    tags = []
    if tm := TAGS_RE.search(rest):
        tags = [t for t in tm[1].split(":") if t]
        rest = rest[:tm.start()]
    elif re.fullmatch(r":[\w@#%:]+:", rest):
        tags, rest = [t for t in rest.split(":") if t], ""
    return level, keyword, priority, rest.strip(), tags


def parse_planning(line):
    """Return {'SCHEDULED': Timestamp, ...} if `line` is a planning line, else None."""
    if not PLANNING_LINE_RE.match(line):
        return None
    return {kind: Timestamp.parse(raw) for kind, raw in PLANNING_ITEM_RE.findall(line)}


def parse_tags(text):
    """Parse colon- or whitespace-separated tags, preserving order without duplicates."""
    tags = re.split(r"[:\s]+", text.strip())
    if any(tag and not re.fullmatch(r"[\w@#%]+", tag) for tag in tags):
        raise ValueError("Tags may contain letters, numbers, underscores, @, # and %.")
    return list(dict.fromkeys(tag for tag in tags if tag))


def set_heading_tags(line, tags):
    """Replace only the heading's trailing tag field, preserving its other text."""
    if parse_heading(line) is None:
        raise ValueError("Not an Org heading")
    if match := TAGS_RE.search(line.rstrip()):
        line = line[:match.start()]
    return line.rstrip() + (" " + ":" + ":".join(tags) + ":" if tags else "")


def parse_entries(text, path=None):
    lines = text.split("\n")
    file_category = None
    in_block = in_drawer = False
    for line in lines:
        if in_block:
            in_block = not BLOCK_END_RE.match(line)
            continue
        if in_drawer:
            in_drawer = not DRAWER_END_RE.match(line)
            continue
        if BLOCK_START_RE.match(line):
            in_block = True
        elif DRAWER_START_RE.match(line) and not DRAWER_END_RE.match(line):
            in_drawer = True
        elif match := re.match(r"^\s*#\+CATEGORY:\s*(.*?)\s*$", line, re.IGNORECASE):
            file_category = match[1] or None
    entries = []
    current = None
    in_drawer = in_block = False
    for i, line in enumerate(lines):
        heading = parse_heading(line)
        if heading:
            level, keyword, priority, title, tags = heading
            current = Entry(path, i, level, keyword, priority, title, tags,
                            heading_text=line, category=file_category)
            current.timestamps.extend(_active_timestamps(title))
            current.timestamp_sources.extend((None, i) for _ in current.timestamps)
            entries.append(current)
            in_drawer = in_block = False
            if i + 1 < len(lines) and (planning := parse_planning(lines[i + 1])):
                current.scheduled = planning.get("SCHEDULED")
                current.deadline = planning.get("DEADLINE")
                current.closed = planning.get("CLOSED")
            continue
        if current is None or (i == current.line + 1 and parse_planning(line) is not None):
            continue
        if in_block:
            in_block = not BLOCK_END_RE.match(line)
            continue
        if in_drawer:
            in_drawer = not DRAWER_END_RE.match(line)
            continue
        if BLOCK_START_RE.match(line):
            in_block = True
            continue
        if DRAWER_START_RE.match(line) and not DRAWER_END_RE.match(line):
            in_drawer = True
            continue
        timestamps = _active_timestamps(line)
        current.timestamps.extend(timestamps)
        title = display_text(PLANNING_ITEM_RE.sub("", line)) or None
        current.timestamp_sources.extend((title, i) for _ in timestamps)
    return entries


def _active_timestamps(text):
    text = PLANNING_ITEM_RE.sub("", text)
    return [ts for raw in ACTIVE_TS_RE.findall(text) if (ts := Timestamp.parse(raw))]


# ---------- Edits ----------
# An edit is (start_line, end_line_exclusive, replacement_lines).

def find_heading(lines, line_no):
    for i in range(min(line_no, len(lines) - 1), -1, -1):
        if parse_heading(lines[i]):
            return i
    return None


def _planning_index(lines, heading_idx):
    i = heading_idx + 1
    if i < len(lines) and PLANNING_LINE_RE.match(lines[i]):
        return i
    return None


def set_todo_keyword(lines, heading_idx, keyword, today):
    """Set the TODO keyword of a heading. Returns (edit, message or None).

    Marking a task with a repeating SCHEDULED/DEADLINE as done shifts those dates and keeps it open.
    """
    heading = lines[heading_idx]
    m = HEADING_KEYWORD_RE.match(heading)
    stars, old_keyword, rest = m[1], m[2], (m[3] or "")
    plan_idx = _planning_index(lines, heading_idx)
    planning_line = lines[plan_idx] if plan_idx is not None else None
    message = None

    if keyword in DONE_KEYWORDS and old_keyword not in DONE_KEYWORDS and planning_line:
        planning = parse_planning(planning_line)
        repeating = {k: ts for k, ts in planning.items()
                     if k in ("SCHEDULED", "DEADLINE") and ts and ts.repeater}
        if repeating:
            for kind, ts in repeating.items():
                new_ts = ts.with_date(ts.next_repeat(today))
                planning_line = _replace_planning_item(planning_line, kind, new_ts)
            keyword = old_keyword if old_keyword in TODO_KEYWORDS else TODO_KEYWORDS[0]
            first = min(parse_planning(planning_line)[k].date for k in repeating)
            message = f"Repeating task, next on {format_date(first, today)}"

    new_heading = stars
    if keyword:
        new_heading += f" {keyword}"
    if rest:
        new_heading += f" {rest}"
    elif not keyword:
        new_heading += " "

    new_lines = [new_heading]
    end = heading_idx + 1
    if plan_idx is not None:
        new_lines.append(planning_line)
        end = plan_idx + 1
    return (heading_idx, end, new_lines), message


def cycle_todo(lines, heading_idx, today):
    """none → TODO → DONE → none (through all configured keywords)."""
    keyword = parse_heading(lines[heading_idx])[1]
    cycle = [None, *ALL_KEYWORDS]
    next_keyword = cycle[(cycle.index(keyword) + 1) % len(cycle)]
    return set_todo_keyword(lines, heading_idx, next_keyword, today)


def _replace_planning_item(line, kind, ts):
    def repl(m):
        return f"{kind}: {ts.format()}" if m[1] == kind else m[0]
    return PLANNING_ITEM_RE.sub(repl, line)


def get_planning(lines, heading_idx, kind):
    plan_idx = _planning_index(lines, heading_idx)
    if plan_idx is None:
        return None
    return parse_planning(lines[plan_idx]).get(kind)


def set_planning(lines, heading_idx, kind, ts):
    """Set (ts) or remove (ts=None) SCHEDULED/DEADLINE on a heading. Returns an edit."""
    plan_idx = _planning_index(lines, heading_idx)
    new_lines = [lines[heading_idx]]
    end = heading_idx + 1
    if plan_idx is not None:
        end = plan_idx + 1
        line = lines[plan_idx]
        indent = PLANNING_LINE_RE.match(line)[1]
        items = [(k, raw) for k, raw in PLANNING_ITEM_RE.findall(line) if k != kind or ts]
        if ts and not any(k == kind for k, _ in items):
            items.append((kind, ts.format()))
        items = [(k, ts.format() if k == kind else raw) for k, raw in items]
        if items:
            new_lines.append(indent + " ".join(f"{k}: {raw}" for k, raw in items))
    elif ts:
        indent = ""
        nxt = lines[heading_idx + 1] if heading_idx + 1 < len(lines) else ""
        if nxt.strip() and not parse_heading(nxt):
            indent = re.match(r"[ \t]*", nxt)[0]
        new_lines.append(f"{indent}{kind}: {ts.format()}")
    return heading_idx, end, new_lines


def apply_edit(lines, edit):
    start, end, new_lines = edit
    return lines[:start] + new_lines + lines[end:]


# ---------- Agenda ----------

@dataclass
class AgendaItem:
    entry: Entry
    kind: str  # scheduled, scheduled-overdue, deadline, deadline-overdue, deadline-upcoming, timestamp
    date: dt.date
    ts: Timestamp
    days: int = 0  # days overdue, or days until the deadline
    timestamp_title: str | None = None
    timestamp_line: int | None = None

    @property
    def label(self):
        return {
            "scheduled": "Scheduled",
            "scheduled-overdue": f"Scheduled {self.days} d. ago",
            "deadline": "Deadline",
            "deadline-overdue": f"Overdue {self.days} d.",
            "deadline-upcoming": f"Due in {self.days} d.",
            "timestamp": "",
        }[self.kind]


_KIND_ORDER = {"deadline-overdue": 0, "deadline": 1, "scheduled-overdue": 2, "scheduled": 3,
               "timestamp": 4, "deadline-upcoming": 5}


def _priority_rank(p):
    return ord(p) if p else ord("B") + 0.5


def build_agenda(entries, start, days, today):
    """Return {date: [AgendaItem]} for `days` days from `start`, following org-agenda rules."""
    end = start + dt.timedelta(days=days - 1)
    agenda = {start + dt.timedelta(days=i): [] for i in range(days)}
    today_in_range = start <= today <= end

    def add(item):
        agenda[item.date].append(item)

    for e in entries:
        open_task = not e.is_done
        if s := e.scheduled:
            overdue = open_task and s.date < today
            for d in s.occurrences(start, end, from_date=today if s.date < today else None):
                if not (overdue and d == today):
                    add(AgendaItem(e, "scheduled", d, s))
            if overdue and today_in_range:
                add(AgendaItem(e, "scheduled-overdue", today, s, (today - s.date).days))
        if dl := e.deadline:
            overdue = open_task and dl.date < today
            for d in dl.occurrences(start, end, from_date=today if dl.date < today else None):
                if not (overdue and d == today):
                    add(AgendaItem(e, "deadline", d, dl))
            if today_in_range and open_task:
                if overdue:
                    add(AgendaItem(e, "deadline-overdue", today, dl, (today - dl.date).days))
                else:
                    warn = interval_days(*dl.warning) if dl.warning else DEFAULT_DEADLINE_WARNING_DAYS
                    until = (dl.date - today).days
                    if 0 < until <= warn:
                        add(AgendaItem(e, "deadline-upcoming", today, dl, until))
        for index, ts in enumerate(e.timestamps):
            title, line = (e.timestamp_sources[index]
                           if index < len(e.timestamp_sources) else (None, None))
            for d in ts.occurrences(start, end):
                add(AgendaItem(e, "timestamp", d, ts,
                               timestamp_title=title, timestamp_line=line))

    for items in agenda.values():
        items.sort(key=lambda it: (
            it.ts.time is None or it.kind == "deadline-upcoming",
            it.ts.time or dt.time(0),
            _KIND_ORDER[it.kind],
            _priority_rank(it.entry.priority),
            it.entry.display_title.lower(),
        ))
    return agenda


def todo_list(entries):
    todos = [e for e in entries if e.is_todo]
    todos.sort(key=lambda e: (_priority_rank(e.priority), e.path or "", e.line))
    return todos


# ---------- Text agenda (laid out like Emacs' org-agenda) ----------

AGENDA_WIDTH = 80
CATEGORY_WIDTH = 12
TIME_WIDTH = 12


@dataclass
class AgendaLine:
    text: str
    kind: str = "text"  # structure, date, item, separator, text
    entry: "Entry | None" = None
    date: "dt.date | None" = None
    spans: list = field(default_factory=list)  # [(start, end, style)]


def category(entry):
    if entry.category:
        return entry.category
    return os.path.splitext(os.path.basename(entry.path))[0] if entry.path else "???"


def _agenda_leader(item):
    if item.kind == "scheduled":
        return "Scheduled: ", "scheduled"
    if item.kind == "scheduled-overdue":
        return f"Sched.{item.days:2d}x: ", "scheduled-previously"
    if item.kind == "deadline":
        return "Deadline:  ", "deadline"
    if item.kind == "deadline-overdue":
        return f"{item.days:2d} d. ago: ", "deadline"
    if item.kind == "deadline-upcoming":
        return f"In {item.days:3d} d.: ", "upcoming-deadline"
    return "", None


def _agenda_time(ts):
    if not ts or not ts.time:
        return ""
    label = f"{ts.time.hour:2d}:{ts.time.minute:02d}"
    if ts.end_time:
        return f"{label}-{ts.end_time.hour}:{ts.end_time.minute:02d}"
    return label + "......"


def agenda_item_line(entry, leader="", leader_style=None, time_text="", width=AGENDA_WIDTH,
                     timestamp_title=None):
    """Format an agenda row, using standalone timestamp text when available."""
    spans = []
    text = "  " + f"{category(entry)[:CATEGORY_WIDTH - 1] + ':':<{CATEGORY_WIDTH}}"
    spans.append((2, len(text), "category"))
    if time_text:
        spans.append((len(text), len(text) + len(time_text), "time"))
        text += f"{time_text:<{TIME_WIDTH}}"
    if leader:
        spans.append((len(text), len(text) + len(leader.rstrip()), leader_style))
        text += leader + " "
    title_start = len(text)
    if entry.keyword and timestamp_title is None:
        style = "done" if entry.is_done else "todo"
        spans.append((len(text), len(text) + len(entry.keyword), style))
        text += entry.keyword + " "
    if entry.priority and timestamp_title is None:
        spans.append((len(text), len(text) + 4, "priority"))
        text += f"[#{entry.priority}] "
    text += timestamp_title if timestamp_title is not None else entry.display_title
    if entry.is_done and timestamp_title is None:
        spans.append((title_start, len(text), "agenda-done"))
    if entry.tags:
        tags = ":" + ":".join(entry.tags) + ":"
        text += " " * max(1, width - len(text) - len(tags))
        spans.append((len(text), len(text) + len(tags), "tag"))
        text += tags
    return AgendaLine(text, "item", entry, spans=spans)


def agenda_date_line(date, today, first=False):
    week = f" W{date.isocalendar().week:02d}" if first or date.weekday() == 0 else ""
    text = f"{DAY_NAMES[date.weekday()]:<10} {date.day:2d} {MONTH_NAMES[date.month - 1]} {date.year}{week}"
    style = "agenda-date-today" if date == today else (
        "agenda-date-weekend" if date.weekday() >= 5 else "agenda-date")
    return AgendaLine(text, "date", date=date, spans=[(0, len(text), style)])


def format_agenda(entries, start, days, today, with_todos=True, width=AGENDA_WIDTH, now=None):
    """The combined agenda (Emacs `C-c a n`): a day/week agenda followed by all TODO items."""
    def structure(text):
        return AgendaLine(text, "structure", spans=[(0, len(text), "agenda-structure")])

    week = start.isocalendar().week
    lines = [structure(f"{'Week' if days == 7 else 'Day' if days == 1 else f'{days} days'}-agenda (W{week:02d}):")]
    for i, (date, items) in enumerate(build_agenda(entries, start, days, today).items()):
        lines.append(agenda_date_line(date, today, first=i == 0))
        day_lines = []
        for item in items:
            leader, style = _agenda_leader(item)
            line = agenda_item_line(item.entry, leader, style, _agenda_time(item.ts), width,
                                    timestamp_title=item.timestamp_title)
            line.date = date
            minute = (item.ts.time.hour * 60 + item.ts.time.minute
                      if item.ts.time and item.kind != "deadline-upcoming" else 1440)
            day_lines.append((minute, 1, line))
        if date == today and now is not None:
            prefix = " " * (2 + CATEGORY_WIDTH)
            for hour in range(8, 21, 2):
                text = f"{prefix}{hour:2d}:00 " + "." * 10 + " " + "-" * 15
                day_lines.append((hour * 60, 0, AgendaLine(
                    text, "time-grid", date=date, spans=[(0, len(text), "agenda-separator")])))
            text = f"{prefix}{now.hour:2d}:{now.minute:02d} " + "." * 10 + " ← now "
            text += "-" * max(0, width - len(text))
            day_lines.append((now.hour * 60 + now.minute, 2, AgendaLine(
                text, "now", date=date)))
            day_lines.sort(key=lambda value: value[:2])
        lines.extend(line for _minute, _order, line in day_lines)
    if with_todos:
        lines.append(AgendaLine("=" * width, "separator", spans=[(0, width, "agenda-separator")]))
        lines.append(structure("Global list of TODO items of type: ALL"))
        lines.extend(agenda_item_line(e, width=width) for e in todo_list(entries))
    return lines


def find_org_files(directory):
    """All *.org files below `directory`, skipping hidden files and folders."""
    result = []
    if not directory or not os.path.isdir(directory):
        return result
    for root, dirs, files in os.walk(directory):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        result.extend(os.path.join(root, f) for f in sorted(files)
                      if f.endswith(".org") and not f.startswith("."))
    return result


def week_start(date):
    return date - dt.timedelta(days=date.weekday())
