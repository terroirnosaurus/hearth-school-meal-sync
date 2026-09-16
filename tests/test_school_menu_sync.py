"""Offline tests: run with `python3 -m unittest discover tests`."""

import json
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import school_menu_sync as sms  # noqa: E402

CONFIG = json.loads((ROOT / "config.json").read_text())
LUNCH = next(m for m in CONFIG["meals"] if m["label"] == "Lunch")
SAMPLE = json.loads((ROOT / "tests/fixtures/lunch_2026_09_sample.json").read_text())["data"]


class ParseTests(unittest.TestCase):
    def setUp(self):
        self.days = {d.day: d for d in map(sms.parse_day, SAMPLE)}

    def test_school_day_items_grouped_by_category(self):
        day = self.days[date(2026, 9, 2)]
        self.assertIsNone(day.day_off)
        self.assertEqual(day.items["Lunch Entree"], ["Cheese Quesadilla", "Chicken and Cheese Quesadilla"])
        self.assertEqual(day.items["Fruit"], ["Fresh Bananas"])

    def test_day_off(self):
        day = self.days[date(2026, 9, 7)]
        self.assertEqual(day.day_off, "Labor Day")
        self.assertEqual(day.items, {})

    def test_build_events(self):
        events = sms.build_events(LUNCH, list(self.days.values()), CONFIG)
        self.assertEqual([e.day for e in events], [date(2026, 9, 2), date(2026, 9, 11)])
        pizza = events[1]
        self.assertEqual(pizza.summary, "🍕 Lunch: Pepperoni Smart Pizza or Cheese French Bread Pizza")
        self.assertIn("Fruit: Fresh Oranges, Sonoma County Apples", pizza.description)  # cleanup rule applied
        self.assertNotIn("Milk", pizza.description)

        off = sms.build_day_off_events(list(self.days.values()))
        self.assertEqual([e.summary for e in off], ["🏠 No School Meals: Labor Day"])


class IcsTests(unittest.TestCase):
    def test_fold_respects_75_octets_and_utf8(self):
        line = "SUMMARY:" + "🍕 pizza " * 40
        folded = sms.fold(line)
        for part in folded.split("\r\n"):
            self.assertLessEqual(len(part.encode("utf-8")), 75)
        self.assertEqual(folded.replace("\r\n ", ""), line)

    def test_escape(self):
        self.assertEqual(sms.ics_escape("a, b; c\\d\ne"), "a\\, b\\; c\\\\d\\ne")

    def test_render_timed_and_all_day(self):
        events = [
            sms.Event("x@t", date(2026, 9, 2), "Lunch", "line1\nline2", "11:45", "12:15"),
            sms.Event("y@t", date(2026, 9, 7), "No School"),
        ]
        ics = sms.render_ics(events, "Test", "America/Los_Angeles")
        self.assertIn("DTSTART;TZID=America/Los_Angeles:20260902T114500\r\n", ics)
        self.assertIn("DTSTART;VALUE=DATE:20260907\r\nDTEND;VALUE=DATE:20260908\r\n", ics)
        self.assertIn("BEGIN:VTIMEZONE", ics)
        self.assertTrue(ics.endswith("END:VCALENDAR\r\n"))

    def test_find_menu_prefers_newest(self):
        menus = [{"id": 1, "name": "Elementary Lunch 2025-2026 SY"}, {"id": 9, "name": "Elementary Lunch 2026-2027 SY"}]
        self.assertEqual(sms.find_menu(menus, "elementary lunch")["id"], 9)
        with self.assertRaises(LookupError):
            sms.find_menu(menus, "Breakfast")


if __name__ == "__main__":
    unittest.main()
