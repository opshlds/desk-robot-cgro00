"""Rocky is told the time and which parts of his body are connected, and
abilities that need a missing part aren't offered (brain/main.py situation,
brain/thinking.py)."""

import unittest
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from brain import main, personality
from brain.devices import ROLES
from brain.thinking import RobotBrain, Situation

NY = ZoneInfo("America/New_York")


class SituationNote(unittest.TestCase):
    def test_voice_only(self):
        s = main.situation(datetime(2026, 9, 25, 23, 14, tzinfo=NY), {"mic", "speaker"})
        self.assertIn("Friday, September 25, 2026, 11:14 PM EDT (UTC-4)", s.note)
        self.assertIn("connected: voice, ears", s.note)
        self.assertIn("not connected: camera", s.note)
        self.assertIn("cannot see anything", s.note)
        self.assertEqual(s.abilities, frozenset())

    def test_whole_robot(self):
        s = main.situation(datetime(2026, 12, 1, 9, 5, tzinfo=NY), set(ROLES))
        self.assertIn("9:05 AM EST (UTC-5)", s.note)
        self.assertNotIn("not connected", s.note)
        self.assertNotIn("cannot see", s.note)
        self.assertEqual(s.abilities, {"look", "track_face"})

    def test_camera_without_neck_can_see_but_not_look(self):
        s = main.situation(datetime(2026, 9, 25, 12, 0, tzinfo=NY), {"mic", "speaker", "camera"})
        self.assertNotIn("cannot see", s.note)
        self.assertEqual(s.abilities, frozenset())
        self.assertIn("12:00 PM", s.note)

    def test_prompt_explains_the_note(self):
        self.assertIn("which parts are connected", personality.SYSTEM_PROMPT)
        self.assertIn("camera is not connected", personality.SYSTEM_PROMPT)


class FakeCompletions:
    def __init__(self):
        self.requests = []

    def create(self, **kw):
        self.requests.append(kw)
        text = "[neutral] No eyes right now."
        return FakeStream([SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text, tool_calls=None))])])


class FakeStream(list):
    """What the OpenAI client returns for stream=True: iterable, closeable."""

    def close(self):
        pass


class WhatTheModelGets(unittest.TestCase):
    def ask(self, sit):
        brain = RobotBrain({"look": lambda a: ("", None), "track_face": lambda a: ("", None)}, lambda: sit)
        fake = FakeCompletions()
        brain.client = SimpleNamespace(chat=SimpleNamespace(completions=fake))
        said = list(brain.reply("What am I doing?"))
        return fake.requests[0], said, brain

    def test_note_rides_on_the_question_and_tools_follow_the_body(self):
        req, said, brain = self.ask(Situation(note="(Now: test. Your parts connected: voice, ears.)", abilities=frozenset()))
        self.assertEqual(said, ["No eyes right now."])
        system, question = req["messages"][0], req["messages"][-1]
        self.assertEqual(system["content"], personality.SYSTEM_PROMPT)  # unchanged turn to turn: cache-friendly
        self.assertEqual(question["content"][-1]["text"], "(Now: test. Your parts connected: voice, ears.)")
        self.assertNotIsInstance(req["tools"], list)                    # no abilities offered at all

    def test_full_body_offers_both_abilities(self):
        req, _, _ = self.ask(Situation(note="(Now: test.)", abilities=frozenset({"look", "track_face"})))
        self.assertEqual({t["function"]["name"] for t in req["tools"]}, {"look", "track_face"})

    def test_no_situation_means_everything(self):
        brain = RobotBrain({"look": lambda a: ("", None)})
        fake = FakeCompletions()
        brain.client = SimpleNamespace(chat=SimpleNamespace(completions=fake))
        list(brain.reply("hi"))
        self.assertEqual([t["function"]["name"] for t in fake.requests[0]["tools"]], ["look"])
        self.assertEqual(len(fake.requests[0]["messages"][-1]["content"]), 1)  # no note added


if __name__ == "__main__":
    unittest.main()
