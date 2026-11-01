import datetime as dt
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir, "src"))

import org  # noqa: E402
from org import Timestamp  # noqa: E402

D = dt.date
TODAY = D(2026, 10, 31)  # a Saturday


class NewItemTests(unittest.TestCase):
    def test_toggle_checkbox(self):
        for line, expected in (
            ("- [ ] task", "- [X] task"),
            ("  + [X] task", "  + [ ] task"),
            ("1) [x] task", "1) [ ] task"),
            ("\t* [-] task", "\t* [X] task"),
            ("- task", None),
            ("text [ ] task", None),
        ):
            with self.subTest(line=line):
                self.assertEqual(org.toggle_checkbox(line), expected)

    def test_heading_and_list_prefixes(self):
        for line, expected in (
            ("** TODO Heading", "** "),
            ("* DONE Heading", "* "),
            ("- item", "- "),
            ("  + item", "  + "),
            ("\t* item", "\t* "),
            ("9. item", "10. "),
            ("  2) [X] item", "  3) [ ] "),
            ("- [-] item", "- [ ] "),
            ("- [ ] item", "- [ ] "),
            ("ordinary text", None),
        ):
            with self.subTest(line=line):
                self.assertEqual(org.new_item_prefix(line), expected)

class TimestampTests(unittest.TestCase):
    def test_parse_and_format_roundtrip(self):
        for raw in ("<2026-11-05 Thu>", "<2026-11-05 Thu 10:00>", "<2026-11-05 Thu 10:00-11:30 +1w>",
                    "<2026-11-05 Thu .+2d -3d>", "[2026-11-05 Thu]"):
            self.assertEqual(Timestamp.parse(raw).format(), raw)

    def test_parse_fields(self):
        ts = Timestamp.parse("<2026-11-05 Thu 9:05 ++1m -2d>")
        self.assertEqual(ts.date, D(2026, 11, 5))
        self.assertEqual(ts.time, dt.time(9, 5))
        self.assertEqual(ts.repeater, ("++", 1, "m"))
        self.assertEqual(ts.warning, (2, "d"))
        self.assertIsNone(Timestamp.parse("<not a date>"))

    def test_next_repeat(self):
        base = D(2026, 10, 1)
        self.assertEqual(Timestamp(base, repeater=("+", 1, "w")).next_repeat(TODAY), D(2026, 10, 8))
        self.assertEqual(Timestamp(base, repeater=("++", 1, "w")).next_repeat(TODAY), D(2026, 11, 5))
        self.assertEqual(Timestamp(base, repeater=(".+", 3, "d")).next_repeat(TODAY), D(2026, 11, 3))
        self.assertEqual(Timestamp(D(2026, 1, 31), repeater=("+", 1, "m")).next_repeat(TODAY), D(2026, 2, 28))

    def test_occurrences(self):
        ts = Timestamp(D(2026, 10, 1), repeater=("+", 1, "w"))  # Thursdays
        self.assertEqual(ts.occurrences(D(2026, 10, 26), D(2026, 11, 8)), [D(2026, 10, 29), D(2026, 11, 5)])
        self.assertEqual(ts.occurrences(D(2026, 10, 26), D(2026, 11, 8), from_date=TODAY), [D(2026, 11, 5)])
        self.assertEqual(Timestamp(D(2026, 11, 2)).occurrences(D(2026, 10, 26), D(2026, 11, 1)), [])
        monthly = Timestamp(D(2025, 1, 15), repeater=("+", 1, "m"))
        self.assertEqual(monthly.occurrences(D(2026, 11, 1), D(2026, 11, 30)), [D(2026, 11, 15)])


class DateInputTests(unittest.TestCase):
    def p(self, text):
        return org.parse_date_input(text, TODAY)

    def test_relative(self):
        self.assertEqual(self.p("").date, TODAY)
        self.assertEqual(self.p("today").date, TODAY)
        self.assertEqual(self.p("tomorrow").date, D(2026, 11, 1))
        self.assertEqual(self.p("+3d").date, D(2026, 11, 3))
        self.assertEqual(self.p("+2w").date, D(2026, 11, 14))
        self.assertEqual(self.p("+1m").date, D(2026, 11, 30))
        self.assertEqual(self.p("+5").date, D(2026, 11, 5))
        self.assertEqual(self.p("-1").date, D(2026, 10, 30))

    def test_weekdays(self):
        self.assertEqual(self.p("sat").date, TODAY)
        self.assertEqual(self.p("fri").date, D(2026, 11, 6))
        self.assertEqual(self.p("Monday").date, D(2026, 11, 2))

    def test_absolute(self):
        self.assertEqual(self.p("2026-11-05").date, D(2026, 11, 5))
        self.assertEqual(self.p("2026-11-05 Thu").date, D(2026, 11, 5))
        self.assertEqual(self.p("11-05").date, D(2026, 11, 5))
        self.assertEqual(self.p("5.11.").date, D(2026, 11, 5))
        self.assertEqual(self.p("5.11.2027").date, D(2027, 11, 5))
        self.assertEqual(self.p("nov 5").date, D(2026, 11, 5))
        self.assertEqual(self.p("5 nov").date, D(2026, 11, 5))
        self.assertEqual(self.p("jan 3").date, D(2027, 1, 3))  # past → next year
        self.assertEqual(self.p("3").date, D(2026, 11, 3))  # past day → next month
        self.assertEqual(self.p("31").date, TODAY)

    def test_time_repeater_warning(self):
        ts = self.p("fri 14:00 +1w -2d")
        self.assertEqual((ts.date, ts.time, ts.repeater, ts.warning),
                         (D(2026, 11, 6), dt.time(14), ("+", 1, "w"), (2, "d")))
        ts = self.p("tomorrow 9:30-10:15 .+1d")
        self.assertEqual((ts.time, ts.end_time, ts.repeater), (dt.time(9, 30), dt.time(10, 15), (".+", 1, "d")))
        self.assertEqual(self.p("3pm").time, dt.time(15))
        self.assertEqual(self.p("12am").time, dt.time(0))
        self.assertEqual(self.p("++1w").repeater, ("++", 1, "w"))

    def test_invalid(self):
        for text in ("blah", "2026-02-30", "25:00", "31.2."):
            self.assertIsNone(self.p(text), text)


DOC = """\
#+TITLE: Test
* TODO [#A] Write report :work:urgent:
DEADLINE: <2026-11-03 Tue> SCHEDULED: <2026-10-28 Wed>
Some text <2026-11-02 Mon 10:00>
** DONE Old thing
CLOSED: [2026-10-20 Tue]
** Meeting <2026-11-04 Wed 14:00-15:00>
:PROPERTIES:
:CREATED: <2026-01-01 Thu>
:END:
* TODO Water plants
SCHEDULED: <2026-10-01 Thu +1w>
*bold* is not a heading
*** 
"""


class ParseTests(unittest.TestCase):
    def setUp(self):
        self.entries = org.parse_entries(DOC, "test.org")

    def test_headings(self):
        self.assertEqual([(e.line, e.level, e.keyword) for e in self.entries],
                         [(1, 1, "TODO"), (4, 2, "DONE"), (6, 2, None), (10, 1, "TODO"), (13, 3, None)])
        first = self.entries[0]
        self.assertEqual((first.priority, first.title, first.tags), ("A", "Write report", ["work", "urgent"]))

    def test_planning_and_timestamps(self):
        first, done, meeting, plants, _ = self.entries
        self.assertEqual(first.deadline.date, D(2026, 11, 3))
        self.assertEqual(first.scheduled.date, D(2026, 10, 28))
        self.assertEqual([t.date for t in first.timestamps], [D(2026, 11, 2)])
        self.assertEqual(done.closed.date, D(2026, 10, 20))
        self.assertEqual([t.time for t in meeting.timestamps], [dt.time(14)])  # drawer timestamp skipped
        self.assertEqual(meeting.display_title, "Meeting")
        self.assertEqual(plants.scheduled.repeater, ("+", 1, "w"))

    def test_heading_variants(self):
        self.assertEqual(org.parse_heading("* TODO"), (1, "TODO", None, "", []))
        self.assertEqual(org.parse_heading("** TODOS are fun"), (2, None, None, "TODOS are fun", []))
        self.assertEqual(org.parse_heading("* :tag:"), (1, None, None, "", ["tag"]))
        self.assertIsNone(org.parse_heading("*bold*"))
        self.assertIsNone(org.parse_heading(" * not heading"))


class EditTests(unittest.TestCase):
    def test_cycle(self):
        lines = ["* Task", "body"]
        states = []
        for _ in range(3):
            edit, _msg = org.cycle_todo(lines, 0, TODAY)
            lines = org.apply_edit(lines, edit)
            states.append(lines[0])
        self.assertEqual(states, ["* TODO Task", "* DONE Task", "* Task"])

    def test_cycle_empty_heading(self):
        lines = ["**"]
        lines = org.apply_edit(lines, org.cycle_todo(lines, 0, TODAY)[0])
        self.assertEqual(lines, ["** TODO"])
        lines = org.apply_edit(lines, org.cycle_todo(lines, 0, TODAY)[0])
        lines = org.apply_edit(lines, org.cycle_todo(lines, 0, TODAY)[0])
        self.assertEqual(lines, ["** "])

    def test_repeating_done(self):
        lines = ["* TODO Water plants", "SCHEDULED: <2026-10-01 Thu ++1w> DEADLINE: <2026-10-02 Fri .+1d>"]
        edit, msg = org.set_todo_keyword(lines, 0, "DONE", TODAY)
        lines = org.apply_edit(lines, edit)
        self.assertEqual(lines, ["* TODO Water plants",
                                 "SCHEDULED: <2026-11-05 Thu ++1w> DEADLINE: <2026-11-01 Sun .+1d>"])
        self.assertEqual(msg, "Repeating task, next on Tomorrow")

    def test_set_planning(self):
        lines = ["* TODO A", "  body", "* B"]
        ts = Timestamp(D(2026, 11, 5))
        lines = org.apply_edit(lines, org.set_planning(lines, 0, "SCHEDULED", ts))
        self.assertEqual(lines, ["* TODO A", "  SCHEDULED: <2026-11-05 Thu>", "  body", "* B"])
        lines = org.apply_edit(lines, org.set_planning(lines, 0, "DEADLINE", ts.with_date(D(2026, 11, 6))))
        self.assertEqual(lines[1], "  SCHEDULED: <2026-11-05 Thu> DEADLINE: <2026-11-06 Fri>")
        lines = org.apply_edit(lines, org.set_planning(lines, 0, "SCHEDULED", ts.with_date(D(2026, 11, 7))))
        self.assertEqual(lines[1], "  SCHEDULED: <2026-11-07 Sat> DEADLINE: <2026-11-06 Fri>")
        self.assertEqual(org.get_planning(lines, 0, "DEADLINE").date, D(2026, 11, 6))
        lines = org.apply_edit(lines, org.set_planning(lines, 0, "SCHEDULED", None))
        self.assertEqual(lines[1], "  DEADLINE: <2026-11-06 Fri>")
        lines = org.apply_edit(lines, org.set_planning(lines, 0, "DEADLINE", None))
        self.assertEqual(lines, ["* TODO A", "  body", "* B"])
        # Last line of the file, nothing after it
        self.assertEqual(org.apply_edit(["* X"], org.set_planning(["* X"], 0, "DEADLINE", ts)),
                         ["* X", "DEADLINE: <2026-11-05 Thu>"])

    def test_find_heading(self):
        lines = DOC.split("\n")
        self.assertIsNone(org.find_heading(lines, 0))
        self.assertEqual(org.find_heading(lines, 3), 1)
        self.assertEqual(org.find_heading(lines, 4), 4)


class AgendaTests(unittest.TestCase):
    def test_week(self):
        entries = org.parse_entries(DOC, "test.org")
        start = org.week_start(TODAY)  # Mon 26 Oct
        agenda = org.build_agenda(entries, start, 14, TODAY)

        def kinds(date):
            return [(it.entry.display_title, it.kind, it.days) for it in agenda[date]]

        self.assertEqual(kinds(D(2026, 10, 28)), [("Write report", "scheduled", 0)])
        self.assertEqual(kinds(D(2026, 10, 29)), [])  # past repeat dates are not projected
        self.assertEqual(kinds(TODAY), [
            ("Write report", "scheduled-overdue", 3),
            ("Water plants", "scheduled-overdue", 30),
            ("Write report", "deadline-upcoming", 3),
        ])
        self.assertEqual(kinds(D(2026, 11, 2)), [("Write report", "timestamp", 0)])
        self.assertEqual(kinds(D(2026, 11, 3)), [("Write report", "deadline", 0)])
        self.assertEqual(kinds(D(2026, 11, 4)), [("Meeting", "timestamp", 0)])
        self.assertEqual(kinds(D(2026, 11, 5)), [("Water plants", "scheduled", 0)])
        self.assertEqual(kinds(D(2026, 11, 7)), [])

    def test_done_items_not_overdue(self):
        entries = org.parse_entries("* DONE x\nDEADLINE: <2026-10-20 Tue>\n", None)
        agenda = org.build_agenda(entries, TODAY, 1, TODAY)
        self.assertEqual(agenda[TODAY], [])

    def test_todo_list(self):
        entries = org.parse_entries(DOC, "test.org")
        self.assertEqual([e.title for e in org.todo_list(entries)], ["Write report", "Water plants"])

    def test_find_org_files(self):
        with tempfile.TemporaryDirectory() as d:
            for p in ("a.org", "b.txt", ".hidden.org", "sub/c.org", ".git/d.org"):
                os.makedirs(os.path.dirname(os.path.join(d, p)), exist_ok=True)
                open(os.path.join(d, p), "w").close()
            self.assertEqual([os.path.relpath(f, d) for f in org.find_org_files(d)], ["a.org", "sub/c.org"])


class TextAgendaTests(unittest.TestCase):
    DOC = """* TODO [#A] Write report :work:
  SCHEDULED: <2026-10-31 Sat 10:00>
* TODO Pay rent
  DEADLINE: <2026-11-01 Sun>
* TODO Old task
  SCHEDULED: <2026-10-28 Wed>
* DONE Finished
  SCHEDULED: <2026-10-30 Fri>
"""

    def render(self, **kw):
        entries = org.parse_entries(self.DOC, "/x/tasks.org")
        return org.format_agenda(entries, org.week_start(TODAY), 7, TODAY, **kw)

    def test_emacs_layout(self):
        text = [l.text for l in self.render()]
        self.assertEqual(text[0], "Week-agenda (W44):")
        self.assertEqual(text[1], "Monday     26 October 2026 W44")
        sat = text.index("Saturday   31 October 2026")
        self.assertEqual(text[sat + 1],
                         "  tasks:      10:00...... Scheduled:  TODO [#A] Write report" .ljust(74) + ":work:")
        self.assertEqual(text[sat + 2], "  tasks:      Sched. 3x:  TODO Old task")
        self.assertEqual(text[sat + 3], "  tasks:      In   1 d.:  TODO Pay rent")
        self.assertIn("  tasks:      Scheduled:  DONE Finished", text)
        self.assertIn("  tasks:      Deadline:   TODO Pay rent", text)
        sep = text.index("=" * 80)
        self.assertEqual(text[sep + 1], "Global list of TODO items of type: ALL")
        self.assertEqual(text[sep + 2].rstrip(":work").rstrip(), "  tasks:      TODO [#A] Write report")
        self.assertEqual(len(text), sep + 5)

    def test_lines_carry_entries_and_styles(self):
        lines = self.render(with_todos=False)
        self.assertNotIn("separator", {l.kind for l in lines})
        today = next(l for l in lines if l.kind == "date" and l.date == TODAY)
        self.assertEqual(today.spans[0][2], "agenda-date-today")
        item = lines[lines.index(today) + 1]
        self.assertEqual(item.entry.title, "Write report")
        styles = {st: item.text[a:b] for a, b, st in item.spans}
        self.assertEqual(styles["todo"], "TODO")
        self.assertEqual(styles["priority"], "[#A]")
        self.assertEqual(styles["scheduled"], "Scheduled:")
        self.assertEqual(styles["tag"], ":work:")

    def test_day_view(self):
        entries = org.parse_entries(self.DOC, "/x/tasks.org")
        lines = org.format_agenda(entries, TODAY, 1, TODAY, with_todos=False)
        self.assertEqual(lines[0].text, "Day-agenda (W44):")
        self.assertEqual(lines[1].text, "Saturday   31 October 2026 W44")
        self.assertEqual(len(lines), 5)


if __name__ == "__main__":
    unittest.main()
