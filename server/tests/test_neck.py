"""The neck board (role "neck"): it reports its calibrated limits, and the
brain keeps every head move inside them."""

import asyncio
import json
import os
import unittest

os.environ.setdefault("ROBOT_TOKEN", "test-robot-token")

import websockets
from websockets.asyncio.client import connect

from brain import config as brainconfig
from brain import main as brainmain
from brain.tracker import Tracker

PORT = 18769
DEFAULTS = (-brainconfig.TRACK_PAN_LIMIT, brainconfig.TRACK_PAN_LIMIT,
            brainconfig.TRACK_TILT_MIN, brainconfig.TRACK_TILT_MAX)


async def recv_json(ws, timeout=3):
    return json.loads(await asyncio.wait_for(ws.recv(), timeout))


class Limits(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(brainmain.parse_limits({"pan": [-50, 45], "tilt": [-35, 15]}),
                         {"pan": (-50.0, 45.0), "tilt": (-35.0, 15.0)})
        for bad in (None, [], {"pan": [-50, 45]}, {"pan": [-50, 45], "tilt": [5, 15]},
                    {"pan": [-50, 45], "tilt": [-10, -5]}, {"pan": [-95, 45], "tilt": [-10, 0]},
                    {"pan": ["a", 45], "tilt": [-10, 0]}, {"pan": [-50, 45, 1], "tilt": [-10, 0]}):
            self.assertIsNone(brainmain.parse_limits(bad), bad)

    def test_head_words(self):
        brainmain.neck_limits = {"pan": (-50.0, 45.0), "tilt": (-35.0, 15.0)}
        try:
            self.assertIn("right, as far as it turns", brainmain.head_words(45, 0))
            self.assertIn("up, as high as it goes", brainmain.head_words(0, 15))
            self.assertIn("(level)", brainmain.head_words(0, 0))
            self.assertIn("down, as low as it goes", brainmain.head_words(0, -35))
        finally:
            brainmain.neck_limits = None

    def test_tracker_uses_the_limits(self):
        t = Tracker(eyes=None, on_pose=lambda *a: None)
        t.limits = lambda: (-20.0, 20.0, -10.0, 10.0)
        for _ in range(20):                                   # a face far up-right, again and again
            t._steer((600, 0, 40, 40), 640, 480, now=_ * 1.0)
        self.assertEqual((t.pan * brainconfig.TRACK_PAN_SIGN > 0, abs(t.pan), abs(t.tilt)), (True, 20.0, 10.0))


class NeckBoard(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        brainmain.main_loop = asyncio.get_running_loop()
        brainmain.devices = brainmain.Registry()
        brainmain.neck_limits = None
        self.server = await websockets.serve(brainmain.handle_robot, "127.0.0.1", PORT)

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()
        brainmain.neck_limits = None

    async def connect_neck(self, limits=None):
        neck = await connect(f"ws://127.0.0.1:{PORT}")
        hello = {"type": "hello", "who": "neck", "fw": "0.1.0", "token": os.environ["ROBOT_TOKEN"], "roles": ["neck"]}
        if limits is not None:
            hello["limits"] = limits
        await neck.send(json.dumps(hello))
        self.assertEqual(await recv_json(neck), {"type": "glance", "on": False})
        return neck

    async def test_limits_from_the_hello_then_forgotten(self):
        self.assertEqual(brainmain.head_limits(), DEFAULTS)
        neck = await self.connect_neck({"pan": [-50, 45], "tilt": [-35, 15]})
        self.assertEqual(brainmain.head_limits(), (-50.0, 45.0, -35.0, 15.0))
        self.assertEqual(brainmain.console_state()["head_limits"],
                         {"pan_min": -50.0, "pan_max": 45.0, "tilt_min": -35.0, "tilt_max": 15.0})
        await neck.close()
        await asyncio.sleep(0.1)
        self.assertEqual(brainmain.head_limits(), DEFAULTS)

    async def test_recalibrated_on_the_board(self):
        neck = await self.connect_neck({"pan": [-40, 40], "tilt": [-30, 0]})
        await neck.send(json.dumps({"type": "limits", "limits": {"pan": [-55, 55], "tilt": [-40, 20]}}))
        await asyncio.sleep(0.1)
        self.assertEqual(brainmain.head_limits(), (-55.0, 55.0, -40.0, 20.0))
        await neck.send(json.dumps({"type": "limits", "limits": {"pan": [10, 55], "tilt": [-40, 20]}}))  # nonsense: kept the last good
        await asyncio.sleep(0.1)
        self.assertEqual(brainmain.head_limits(), (-55.0, 55.0, -40.0, 20.0))
        await neck.close()

    async def test_only_the_neck_sets_limits(self):
        face = await connect(f"ws://127.0.0.1:{PORT}")
        await face.send(json.dumps({"type": "hello", "who": "amoled-face", "token": os.environ["ROBOT_TOKEN"],
                                    "roles": ["face"], "limits": {"pan": [-10, 10], "tilt": [-5, 5]}}))
        await recv_json(face)                                  # its emotion
        await face.send(json.dumps({"type": "limits", "limits": {"pan": [-10, 10], "tilt": [-5, 5]}}))
        await asyncio.sleep(0.1)
        self.assertEqual(brainmain.head_limits(), DEFAULTS)
        await face.close()

    async def test_console_head_stays_inside(self):
        neck = await self.connect_neck({"pan": [-50, 45], "tilt": [-35, 15]})
        await brainmain._console_command("head", {"pan": 45, "tilt": 15})
        self.assertEqual(await recv_json(neck), {"type": "pan", "deg": 45.0})
        self.assertEqual(await recv_json(neck), {"type": "tilt", "deg": 15.0})
        with self.assertRaises(ValueError):
            await brainmain._console_command("head", {"pan": 50, "tilt": 0})
        await neck.close()

    async def test_up_needs_a_neck_that_can(self):
        text, jpeg = await brainmain.look({"direction": "up"})     # default limits: tilt max 0
        self.assertIn("can't tilt above level", text)
        self.assertIsNone(jpeg)


if __name__ == "__main__":
    unittest.main()
