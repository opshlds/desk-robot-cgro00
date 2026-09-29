"""Firmware updates over the air: the image store, the one-time download
links, the `ota` console command and a fake board going through a whole update."""

import asyncio
import contextlib
import hashlib
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("ROBOT_TOKEN", "test-robot-token")

import websockets
from websockets.asyncio.client import connect

from brain import config as brainconfig
from brain import firmware
from brain import main as brainmain
from tools import fw_add

WS_PORT = 18771
OTA_PORT = 18772


def app_image(n=40000, seed=1) -> bytes:
    body = hashlib.sha256(str(seed).encode()).digest() * (n // 32)
    return b"\xe9" + body[1:]


def put(root: Path, board: str, name: str, data: bytes) -> Path:
    p = root / board / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


async def http_get(host: str, port: int, path: str) -> tuple[int, bytes]:
    reader, writer = await asyncio.open_connection(host, port)
    writer.write(f"GET {path} HTTP/1.1\r\nHost: {host}\r\n\r\n".encode())
    await writer.drain()
    raw = await asyncio.wait_for(reader.read(), 5)
    writer.close()
    head, _, body = raw.partition(b"\r\n\r\n")
    return int(head.split(b" ")[1]), body


class Versions(unittest.TestCase):
    def test_order(self):
        v = firmware.parse_version
        self.assertLess(v("0.3.0"), v("0.4.0"))
        self.assertLess(v("0.4.2-rbtest"), v("0.4.2"))
        self.assertLess(v("0.4.1"), v("0.4.2-rbtest"))
        self.assertLess(v("0.9.0"), v("0.10.0"))
        for bad in ("", "0.4", "v0.4.1", "0.4.1-Bad!", "0.4.1.2"):
            self.assertIsNone(v(bad), bad)

    def test_board_names(self):
        self.assertEqual([firmware.board_name(n) for n in ("face", "Camera", "cam", "neck", "yahboom")],
                         ["face", "camera", "camera", "neck", None])


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = firmware.Store(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_records_sha_and_size(self):
        src = put(self.root / "in", "x", "face-fw-0.4.1-app.bin", app_image())
        img = self.store.add("face", src)
        manifest = json.loads((self.root / "manifest.json").read_text())
        self.assertEqual(manifest["images"][0]["sha256"], hashlib.sha256(src.read_bytes()).hexdigest())
        self.assertEqual(manifest["images"][0]["size"], src.stat().st_size)
        self.assertEqual(self.store.find("face"), img)

    def test_changed_file_is_refused(self):
        src = put(self.root / "in", "x", "cam-fw-0.2.0-app.bin", app_image())
        img = self.store.add("camera", src)
        img.path.write_bytes(app_image(seed=2))                     # corrupted / swapped after it was recorded
        self.assertIsNone(self.store.find("camera"))
        self.assertTrue(any("differ from manifest" in p for p in self.store.problems))

    def test_dropped_in_files_and_newest(self):
        put(self.root, "neck", "neck-fw-0.1.1-app.bin", app_image(seed=1))
        put(self.root, "neck", "neck-fw-0.1.2-app.bin", app_image(seed=2))
        put(self.root, "neck", "neck-fw-0.1.3-rbtest-app.bin", app_image(seed=3))
        put(self.root, "neck", "face-fw-0.9.0-app.bin", app_image(seed=4))   # wrong board's prefix
        self.assertEqual(self.store.find("neck").version, "0.1.2")           # test builds only by name
        self.assertEqual(self.store.find("neck", "0.1.3-rbtest").version, "0.1.3-rbtest")
        self.assertIsNone(self.store.find("neck", "0.1.9"))
        self.assertTrue(any("face-fw-0.9.0" in p for p in self.store.problems))

    def test_fw_add_refuses_a_factory_image(self):
        factory = bytearray(app_image(0x9000))
        factory[0x8000:0x8002] = b"\xaa\x50"
        src = put(self.root / "in", "x", "face-fw-0.4.1-app.bin", bytes(factory))
        with contextlib.redirect_stderr(io.StringIO()) as err, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(fw_add.main(["face", str(src), "--dir", str(self.root)]), 1)
        self.assertIn("factory image", err.getvalue())
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(fw_add.main(["camera", str(put(self.root / "in", "y", "x.bin", app_image())),
                                          "--version", "0.2.0", "--dir", str(self.root)]), 0)
        self.assertIn("added camera 0.2.0", out.getvalue())


class Links(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        put(root, "face", "face-fw-0.4.1-app.bin", app_image())
        self.image = firmware.Store(root).find("face")
        self.server = firmware.ImageServer(OTA_PORT, token_seconds=0.6, log=lambda s: None)
        self.served = []
        self.server.on_served = lambda g, ok, why: self.served.append((g.image.version, ok, why))

    async def asyncTearDown(self):
        await self.server.close()
        self.tmp.cleanup()

    async def test_one_download_then_gone(self):
        url, _ = await self.server.grant(self.image, "127.0.0.1", "127.0.0.1")
        path = url.split(f":{OTA_PORT}", 1)[1]
        code, body = await http_get("127.0.0.1", OTA_PORT, path)
        self.assertEqual((code, hashlib.sha256(body).hexdigest()), (200, self.image.sha256))
        self.assertEqual(self.served, [("0.4.1", True, "")])
        await asyncio.sleep(0.05)
        self.assertEqual(self.server.listening(), [])                # port closed: nothing left to serve
        with self.assertRaises(OSError):
            await http_get("127.0.0.1", OTA_PORT, path)

    async def test_wrong_token_and_wrong_board(self):
        url, _ = await self.server.grant(self.image, "192.168.1.205", "127.0.0.1")
        self.assertEqual((await http_get("127.0.0.1", OTA_PORT, "/fw/guess"))[0], 404)
        self.assertEqual((await http_get("127.0.0.1", OTA_PORT, "/"))[0], 404)
        self.assertEqual((await http_get("127.0.0.1", OTA_PORT, url.split(f":{OTA_PORT}", 1)[1]))[0], 403)
        self.assertEqual(self.served, [])                            # still waiting for its own board

    async def test_expires(self):
        url, _ = await self.server.grant(self.image, "127.0.0.1", "127.0.0.1")
        await asyncio.sleep(1.3)
        self.assertEqual(self.served, [("0.4.1", False, "the link expired before the board fetched it")])
        self.assertEqual(self.server.listening(), [])

    async def test_file_changed_since_listing(self):
        self.image.path.write_bytes(app_image(seed=9))
        with self.assertRaises(ValueError):
            await self.server.grant(self.image, "127.0.0.1", "127.0.0.1")


class FakeBoard:
    """A board as the firmware behaves: hello, then on `ota` fetch, check the
    sha256, report progress and the result, "reboot" into the new version."""

    def __init__(self, test, who, role, fw, ota=True):
        self.test, self.who, self.role, self.fw, self.ota = test, who, role, fw, ota
        self.ws = None

    async def connect(self, rolled_back=None):
        self.ws = await connect(f"ws://127.0.0.1:{WS_PORT}")
        hello = {"type": "hello", "who": self.who, "fw": self.fw, "token": os.environ["ROBOT_TOKEN"], "roles": [self.role]}
        if self.ota:
            hello["ota"] = True
        if rolled_back:
            hello["ota_rollback"] = rolled_back
        await self.ws.send(json.dumps(hello))
        await asyncio.wait_for(self.ws.recv(), 3)                   # the brain's first message (emotion / stream / glance)

    async def next_ota(self, timeout=3):
        while True:
            m = json.loads(await asyncio.wait_for(self.ws.recv(), timeout))
            if m.get("type") == "ota":
                return m

    async def update(self):
        m = await self.next_ota()
        host, _, rest = m["url"][len("http://"):].partition(":")
        port, _, path = rest.partition("/")
        code, body = await http_get(host, int(port), "/" + path)
        ok = code == 200 and len(body) == m["size"] and hashlib.sha256(body).hexdigest() == m["sha256"]
        for pct in (50, 100):
            await self.ws.send(json.dumps({"type": "ota_progress", "pct": pct}))
        await self.ws.send(json.dumps({"type": "ota_result", "ok": ok, "error": None if ok else "sha256 mismatch"}))
        await asyncio.sleep(0.1)
        return m, ok


class Console(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.saved = (brainmain.fw_store, brainmain.fw_server, brainconfig.OTA_PORT, brainconfig.FIRMWARE_DIR)
        brainmain.fw_store = firmware.Store(self.root)
        brainmain.fw_server = None
        brainconfig.OTA_PORT = OTA_PORT
        brainconfig.FIRMWARE_DIR = self.root
        brainmain.updates.clear()
        brainmain.devices = brainmain.Registry()
        brainmain.main_loop = asyncio.get_running_loop()
        self.server = await websockets.serve(brainmain.handle_robot, "127.0.0.1", WS_PORT)
        put(self.root, "face", "face-fw-0.4.0-app.bin", app_image(seed=1))
        put(self.root, "face", "face-fw-0.4.1-app.bin", app_image(seed=2))
        put(self.root, "camera", "cam-fw-0.2.0-app.bin", app_image(seed=3))

    async def asyncTearDown(self):
        if brainmain.fw_server is not None:
            await brainmain.fw_server.close()
        brainmain.fw_store, brainmain.fw_server, brainconfig.OTA_PORT, brainconfig.FIRMWARE_DIR = self.saved
        brainmain.updates.clear()
        self.server.close()
        await self.server.wait_closed()
        self.tmp.cleanup()

    async def console(self, line):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            await brainmain.handle_console_line(line)
        return out.getvalue()

    async def test_full_update_goes_to_that_board_only(self):
        face = FakeBoard(self, "amoled-face", "face", "0.4.0")
        cam = FakeBoard(self, "xiao-camera", "camera", "0.2.0")
        await face.connect()
        await cam.connect()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            await brainmain.handle_console_line("ota face")
            m, ok = await face.update()
            self.assertTrue(ok)
            self.assertEqual((m["version"], m["size"]), ("0.4.1", len(app_image(seed=2))))
            self.assertEqual(brainmain.updates["face"]["stage"], "written")
            await face.ws.close()                                    # reboot...
            face.fw = "0.4.1"
            await face.connect()                                     # ...into the new version
        text = out.getvalue()
        for want in ("0.4.0 -> 0.4.1", "image sent to 127.0.0.1", "50%", "rebooting into 0.4.1", "now running 0.4.1. Done."):
            self.assertIn(want, text)
        self.assertEqual(brainmain.updates, {})
        with self.assertRaises(asyncio.TimeoutError):                # the camera never heard a thing
            await cam.next_ota(timeout=0.3)
        await face.ws.close()
        await cam.ws.close()

    async def test_board_refuses_a_bad_sha(self):
        face = FakeBoard(self, "amoled-face", "face", "0.4.0")
        await face.connect()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            await brainmain.handle_console_line("ota face 0.4.1 badsha")
            m, ok = await face.update()
        self.assertFalse(ok)
        self.assertIn("REFUSED by the board: sha256 mismatch; still on 0.4.0 (expected", out.getvalue())
        self.assertEqual(brainmain.updates, {})
        await face.ws.close()

    async def test_rollback_is_reported(self):
        face = FakeBoard(self, "amoled-face", "face", "0.4.0")
        await face.connect()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            await brainmain.handle_console_line("ota face")
            await face.update()
            await face.ws.close()
            await face.connect(rolled_back="0.4.1")                  # the new image never reached us; old one back
        self.assertIn("rolled back from 0.4.1", out.getvalue())
        self.assertIn("came back on 0.4.0, not 0.4.1", out.getvalue())
        self.assertEqual(brainmain.updates, {})
        await face.ws.close()

    async def test_refusals(self):
        self.assertIn("no face board connected", await self.console("ota face"))
        self.assertIn("which board?", await self.console("ota yahboom"))
        old = FakeBoard(self, "amoled-face", "face", "0.3.0", ota=False)
        await old.connect()
        self.assertIn("flash the OTA firmware over USB once", await self.console("ota face"))
        await old.ws.close()
        await asyncio.sleep(0.1)
        face = FakeBoard(self, "amoled-face", "face", "0.4.1")
        await face.connect()
        self.assertIn("already runs 0.4.1", await self.console("ota face"))
        self.assertIn("a downgrade", await self.console("ota face 0.4.0"))
        self.assertIn("not in", await self.console("ota face 0.5.0"))
        self.assertIn("0.4.1 -> 0.4.0", await self.console("ota face 0.4.0 force"))
        self.assertIn("already updating to 0.4.0", await self.console("ota face 0.4.0 force"))
        self.assertIn("cancelled", await self.console("ota cancel face"))
        self.assertEqual(brainmain.updates, {})
        await face.ws.close()

    async def test_multi_role_board_is_refused(self):
        ws = await connect(f"ws://127.0.0.1:{WS_PORT}")
        await ws.send(json.dumps({"type": "hello", "who": "desk-robot", "fw": "0.3.0", "ota": True,
                                  "token": os.environ["ROBOT_TOKEN"], "roles": ["camera", "face"]}))
        await asyncio.sleep(0.1)
        self.assertIn("only for single-role boards", await self.console("ota face"))
        await ws.close()

    async def test_listing(self):
        face = FakeBoard(self, "amoled-face", "face", "0.4.0")
        await face.connect()
        text = await self.console("ota")
        self.assertIn("amoled-face fw 0.4.0", text)
        self.assertIn("0.4.0, 0.4.1*", text)
        self.assertIn("camera  not connected", text)
        await face.ws.close()

    async def test_stale_update_is_forgotten(self):
        brainmain.updates["neck"] = {"version": "0.1.1", "from": "0.1.0", "token": "", "stage": "sent",
                                     "pct": None, "since": time.time() - 10_000, "board": "neck"}
        self.assertIn("giving up", await self.console("ota"))
        self.assertEqual(brainmain.updates, {})


if __name__ == "__main__":
    unittest.main()
