"""The camera board (role "camera"): picture size from the console and the live-view page."""

import asyncio
import json
import os
import unittest

os.environ.setdefault("ROBOT_TOKEN", "test-robot-token")

import websockets
from websockets.asyncio.client import connect

from brain import main as brainmain

PORT = 18768


async def recv_json(ws, timeout=3):
    return json.loads(await asyncio.wait_for(ws.recv(), timeout))


class CameraSize(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        brainmain.main_loop = asyncio.get_running_loop()
        brainmain.devices = brainmain.Registry()
        brainmain.camera_info = None
        self.server = await websockets.serve(brainmain.handle_robot, "127.0.0.1", PORT)

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()

    async def connect_camera(self, report=None):
        cam = await connect(f"ws://127.0.0.1:{PORT}")
        await cam.send(json.dumps({"type": "hello", "who": "xiao-camera", "fw": "0.1.2",
                                   "token": os.environ["ROBOT_TOKEN"], "roles": ["camera"]}))
        self.assertEqual((await recv_json(cam))["type"], "stream")        # stream on at connect
        if report:
            await cam.send(json.dumps({"type": "camera", **report}))
            await asyncio.sleep(0.1)
        return cam

    async def test_the_board_reports_its_size(self):
        cam = await self.connect_camera({"res": "vga", "w": 640, "h": 480})
        state = brainmain.console_state()["camera"]
        self.assertTrue(state["connected"])
        self.assertEqual((state["res"], state["w"], state["h"]), ("vga", 640, 480))
        await cam.close()
        await asyncio.sleep(0.1)
        state = brainmain.console_state()["camera"]
        self.assertFalse(state["connected"])
        self.assertNotIn("res", state)                                    # forgotten with the board

    async def test_page_sets_the_size(self):
        cam = await self.connect_camera({"res": "vga", "w": 640, "h": 480})
        await brainmain._console_command("camera", {"res": "SVGA"})
        self.assertEqual(await recv_json(cam), {"type": "camera", "res": "svga"})
        with self.assertRaises(ValueError):
            await brainmain._console_command("camera", {"res": "4k"})
        await cam.close()

    async def test_no_camera_no_size(self):
        with self.assertRaises(ValueError):
            await brainmain._console_command("camera", {"res": "vga"})
        self.assertFalse(brainmain.console_state()["camera"]["connected"])

    async def test_console_command(self):
        cam = await self.connect_camera()
        await brainmain.handle_console_line("camres")                    # size unknown yet: prints only
        await brainmain.handle_console_line("camres hd")
        self.assertEqual(await recv_json(cam), {"type": "camera", "res": "hd"})
        await brainmain.handle_console_line("camres huge")               # refused, nothing sent
        with self.assertRaises(asyncio.TimeoutError):
            await recv_json(cam, timeout=0.3)
        await cam.close()

    async def test_bad_reports_are_ignored(self):
        cam = await self.connect_camera({"res": "giant", "w": 9, "h": 9})
        self.assertNotIn("res", brainmain.console_state()["camera"])
        await cam.close()

    async def test_only_the_camera_reports(self):
        face = await connect(f"ws://127.0.0.1:{PORT}")
        await face.send(json.dumps({"type": "hello", "who": "amoled-face", "token": os.environ["ROBOT_TOKEN"],
                                    "roles": ["face"]}))
        await recv_json(face)
        await face.send(json.dumps({"type": "camera", "res": "hd", "w": 1280, "h": 720}))
        await asyncio.sleep(0.1)
        self.assertIsNone(brainmain.camera_info)
        await face.close()


if __name__ == "__main__":
    unittest.main()
