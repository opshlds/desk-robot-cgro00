"""time_in: the exact time somewhere else (brain/clock.py), and Rocky using it."""

import json
import unittest
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from brain import clock, main
from brain.thinking import RobotBrain, Situation

# 7:34 PM on a Friday in New York, summer time (the evening Rocky said 6:34 in Dhaka).
NOW = datetime(2026, 9, 25, 19, 34, tzinfo=ZoneInfo("America/New_York"))


class TimeIn(unittest.TestCase):
    def test_dhaka_is_ten_hours_ahead(self):
        for place in ("Bangladesh", "Asia/Dhaka", "dhaka", "Dhaka, Bangladesh"):
            text = clock.time_in(place, NOW)
            self.assertIn("Saturday, September 26, 2026, 5:34 AM (UTC+6)", text, place)
            self.assertIn("10 hours ahead", text)
            self.assertIn("already tomorrow", text)

    def test_odd_offsets(self):
        self.assertIn("9 hours 30 minutes ahead", clock.time_in("India", NOW))
        self.assertIn("5:19 AM (UTC+5:45)", clock.time_in("Kathmandu", NOW))

    def test_behind_and_same(self):
        self.assertIn("3 hours behind", clock.time_in("Los Angeles", NOW))
        self.assertIn("4:34 PM", clock.time_in("los_angeles", NOW))
        self.assertIn("the same as your human's", clock.time_in("New York", NOW))

    def test_winter_shifts_the_difference(self):
        winter = datetime(2026, 1, 15, 12, 0, tzinfo=ZoneInfo("America/New_York"))
        self.assertIn("11 hours ahead", clock.time_in("Dhaka", winter))  # New York on EST (UTC-5)
        self.assertIn("5 hours ahead", clock.time_in("London", winter))

    def test_unknown_place_asks_for_a_zone(self):
        self.assertIn("IANA time zone", clock.time_in("Atlantis", NOW))
        self.assertIn("IANA time zone", clock.time_in("", NOW))


def chunk(content=None, tool=None):
    tc = None
    if tool:
        tc = [SimpleNamespace(index=0, id="call_1",
                              function=SimpleNamespace(name=tool[0], arguments=json.dumps(tool[1])))]
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=content, tool_calls=tc))])


class Stream(list):
    def close(self):
        pass


class RockyUsesIt(unittest.TestCase):
    def test_tool_round_trip(self):
        rounds = [
            Stream([chunk(tool=("time_in", {"place": "Asia/Dhaka"}))]),
            Stream([chunk("[thinking] Dhaka ten hours ahead. Five thirty-four in morning, Saturday.")]),
        ]
        requests = []

        def create(**kw):
            requests.append(kw)
            return rounds[len(requests) - 1]

        brain = RobotBrain({"time_in": lambda a: (clock.time_in(a["place"], NOW), None)},
                           lambda: Situation(note="(Now: test.)", abilities=frozenset({"time_in"})))
        brain.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        said = list(brain.reply("What time is it in Bangladesh?"))

        self.assertEqual(said, ["Dhaka ten hours ahead.", "Five thirty-four in morning, Saturday."])
        self.assertEqual([t["function"]["name"] for t in requests[0]["tools"]], ["time_in"])
        tool_msg = requests[1]["messages"][-1]
        self.assertEqual(tool_msg["role"], "tool")
        self.assertIn("5:34 AM (UTC+6)", tool_msg["content"])

    def test_offered_even_with_no_body(self):
        self.assertIn("time_in", main.situation(NOW, set()).abilities)
        self.assertIn("time_in", main.ABILITIES)


if __name__ == "__main__":
    unittest.main()
