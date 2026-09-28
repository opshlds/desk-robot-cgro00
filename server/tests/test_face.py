"""The AMOLED face board (role "face"): what it is sent, and its touches."""

import asyncio
import json
import os
import unittest

os.environ.setdefault("ROBOT_TOKEN", "test-robot-token")

import websockets
from websockets.asyncio.client import connect

from brain import config as brainconfig
from brain import main as brainmain

PORT = 18766


async def recv_json(ws, timeout=3):
    return json.loads(await asyncio.wait_for(ws.recv(), timeout))


class FaceBoard(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        brainmain.main_loop = asyncio.get_running_loop()
        brainmain.devices = brainmain.Registry()
        brainmain.awake_until = 0.0
        brainmain.sleeping = True
        brainmain.current_reply = None
        brainmain.current_emotion = "neutral"
        self._delay = brainconfig.SLEEP_DELAY_SECONDS
        brainconfig.SLEEP_DELAY_SECONDS = 0.05
        self.server = await websockets.serve(brainmain.handle_robot, "127.0.0.1", PORT)
        self.face = await connect(f"ws://127.0.0.1:{PORT}")
        await self.face.send(json.dumps({"type": "hello", "who": "amoled-face", "fw": "0.2.0",
                                         "token": os.environ["ROBOT_TOKEN"], "roles": ["face"]}))

    async def asyncTearDown(self):
        brainconfig.SLEEP_DELAY_SECONDS = self._delay
        await self.face.close()
        self.server.close()
        await self.server.wait_closed()

    async def test_gets_the_current_emotion_on_connect(self):
        self.assertEqual(await recv_json(self.face), {"type": "emotion", "name": "neutral"})

    async def test_only_face_messages_arrive(self):
        await recv_json(self.face)
        await brainmain.send_to_robot({"type": "volume", "level": 0.5})   # speaker only: not for the face
        await brainmain.send_to_robot({"type": "emotion", "name": "happy"})
        self.assertEqual(await recv_json(self.face), {"type": "emotion", "name": "happy"})

    async def test_tap_wakes_him(self):
        await recv_json(self.face)
        await self.face.send(json.dumps({"type": "touch", "gesture": "tap"}))
        self.assertEqual(await recv_json(self.face), {"type": "asleep", "on": False})
        self.assertEqual(await recv_json(self.face), {"type": "emotion", "name": "surprised"})
        self.assertGreater(brainmain.awake_until, 0)

    async def test_tap_while_awake_changes_nothing(self):
        await recv_json(self.face)
        brainmain.awake_until = float("inf")
        await self.face.send(json.dumps({"type": "touch", "gesture": "tap"}))
        with self.assertRaises(asyncio.TimeoutError):
            await recv_json(self.face, timeout=0.3)

    async def test_long_press_puts_him_to_sleep(self):
        await recv_json(self.face)
        brainmain.awake_until = float("inf")
        brainmain.sleeping = False
        await self.face.send(json.dumps({"type": "touch", "gesture": "long"}))
        self.assertEqual(await recv_json(self.face), {"type": "emotion", "name": "sleepy"})
        self.assertEqual(await recv_json(self.face), {"type": "asleep", "on": True})
        self.assertEqual(brainmain.awake_until, 0.0)

    async def test_touch_from_another_board_is_ignored(self):
        await recv_json(self.face)
        cam = await connect(f"ws://127.0.0.1:{PORT}")
        await cam.send(json.dumps({"type": "hello", "who": "xiao", "token": os.environ["ROBOT_TOKEN"], "roles": ["camera"]}))
        await recv_json(cam)                                   # stream on
        await cam.send(json.dumps({"type": "touch", "gesture": "tap"}))
        with self.assertRaises(asyncio.TimeoutError):
            await recv_json(self.face, timeout=0.3)
        await cam.close()

    async def test_a_reconnecting_face_replaces_its_old_connection(self):
        await recv_json(self.face)
        again = await connect(f"ws://127.0.0.1:{PORT}")   # same board, old socket not yet dead
        await again.send(json.dumps({"type": "hello", "who": "amoled-face", "fw": "0.2.1",
                                     "token": os.environ["ROBOT_TOKEN"], "roles": ["face"]}))
        self.assertEqual(await recv_json(again), {"type": "emotion", "name": "neutral"})
        with self.assertRaises(websockets.ConnectionClosed):
            await asyncio.wait_for(self.face.recv(), 3)      # the old one is closed
        self.assertEqual(brainmain.devices.owner("face").fw, "0.2.1")
        await again.close()

    async def test_a_different_board_still_cannot_take_the_face(self):
        await recv_json(self.face)
        other = await connect(f"ws://127.0.0.1:{PORT}")
        await other.send(json.dumps({"type": "hello", "who": "another-screen",
                                     "token": os.environ["ROBOT_TOKEN"], "roles": ["face"]}))
        with self.assertRaises(websockets.ConnectionClosed) as cm:
            await asyncio.wait_for(other.recv(), 3)
        self.assertEqual(cm.exception.rcvd.code, 1013)


class Mouth(unittest.IsolatedAsyncioTestCase):
    """Mouth levels: from the speaker (the bridge), to the face, MOUTH_DELAY_SECONDS later."""

    async def asyncSetUp(self):
        brainmain.main_loop = asyncio.get_running_loop()
        brainmain.devices = brainmain.Registry()
        brainmain.current_emotion = "neutral"
        self._delay = brainconfig.MOUTH_DELAY_SECONDS
        self.server = await websockets.serve(brainmain.handle_robot, "127.0.0.1", PORT)
        self.speaker = await connect(f"ws://127.0.0.1:{PORT}")
        await self.speaker.send(json.dumps({"type": "hello", "who": "xiaozhi-bridge",
                                            "token": os.environ["ROBOT_TOKEN"], "roles": ["speaker"]}))
        await asyncio.sleep(0.1)

    async def asyncTearDown(self):
        brainconfig.MOUTH_DELAY_SECONDS = self._delay
        await self.speaker.close()
        self.server.close()
        await self.server.wait_closed()

    async def connect_face(self):
        face = await connect(f"ws://127.0.0.1:{PORT}")
        await face.send(json.dumps({"type": "hello", "who": "amoled-face", "fw": "0.2.0",
                                    "token": os.environ["ROBOT_TOKEN"], "roles": ["face"]}))
        await recv_json(face)                                  # the current emotion
        return face

    async def test_reaches_the_face_after_the_delay(self):
        face = await self.connect_face()
        brainconfig.MOUTH_DELAY_SECONDS = 0.2
        loop = asyncio.get_running_loop()
        t = loop.time()
        await self.speaker.send(json.dumps({"type": "mouth", "level": 0.4567}))
        self.assertEqual(await recv_json(face), {"type": "mouth", "level": 0.46})
        self.assertGreater(loop.time() - t, 0.18)
        await self.speaker.send(json.dumps({"type": "mouth", "level": 7}))       # clamped
        self.assertEqual(await recv_json(face), {"type": "mouth", "level": 1.0})
        await self.speaker.send(json.dumps({"type": "mouth", "level": "loud"}))  # ignored
        with self.assertRaises(asyncio.TimeoutError):
            await recv_json(face, timeout=0.4)
        await face.close()

    async def test_only_the_speaker_moves_the_mouth(self):
        face = await self.connect_face()
        brainconfig.MOUTH_DELAY_SECONDS = 0.0
        await face.send(json.dumps({"type": "mouth", "level": 0.5}))
        with self.assertRaises(asyncio.TimeoutError):
            await recv_json(face, timeout=0.3)
        await face.close()

    async def test_nothing_without_a_face(self):
        brainconfig.MOUTH_DELAY_SECONDS = 0.0
        await self.speaker.send(json.dumps({"type": "mouth", "level": 0.5}))
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(self.speaker.recv(), 0.3)   # the speaker never gets it either

    async def test_mouthdelay_command(self):
        await brainmain.handle_console_line("mouthdelay 0.25")
        self.assertEqual(brainconfig.MOUTH_DELAY_SECONDS, 0.25)
        await brainmain.handle_console_line("mouthdelay")      # just prints it
        self.assertEqual(brainconfig.MOUTH_DELAY_SECONDS, 0.25)
        for bad in ("mouthdelay -1", "mouthdelay 5", "mouthdelay soon"):
            await brainmain.handle_console_line(bad)
            self.assertEqual(brainconfig.MOUTH_DELAY_SECONDS, 0.25)


class SleepWords(unittest.TestCase):
    def test_sleep_phrases(self):
        for said in ["Go to sleep.", "Rocky, go to sleep", "Time for bed!", "You can fall asleep now"]:
            self.assertTrue(brainmain.wants_sleep(said), said)
        for said in ["Are you asleep?", "What time is it?", "Sleep is important, right?"]:
            self.assertFalse(brainmain.wants_sleep(said), said)


if __name__ == "__main__":
    unittest.main()
