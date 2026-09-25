"""The Xiaozhi bridge, end to end: a simulated Yahboom (real Opus, the real
Xiaozhi message sequence) -> the real bridge -> the brain's real connection
handling and reply pipeline. Only the models are stubbed: no LLM, TTS or
speech recognition is needed to run this.

Skipped when opuslib_next (and the system libopus) is not installed.
"""

import asyncio
import json
import os
import threading
import time
import unittest

import numpy as np

# Ports and secrets for this test, set before the bridge reads them at import.
os.environ.update({
    "ROBOT_TOKEN": "test-robot-token",
    "XIAOZHI_TOKEN": "test-board-token",
    "XIAOZHI_DEVICES": "80:45:6b:24:76:30",
    "BRIDGE_HOST": "127.0.0.1",
    "BRIDGE_WS_PORT": "18003",
    "BRIDGE_OTA_PORT": "18004",
    "BRIDGE_BRAIN_URL": "ws://127.0.0.1:18765",
})

try:
    import opuslib_next as opus
    opus.Encoder(16000, 1, opus.APPLICATION_VOIP)
    HAVE_OPUS = True
except Exception:  # module or libopus missing
    HAVE_OPUS = False

import websockets
from websockets.asyncio.client import connect

from bridge import xiaozhi as bridge
from brain import main as brainmain
from brain import config as brainconfig
from brain import mouth, personality

MAC = "80:45:6b:24:76:30"
SENTENCES = ["Hello friend.", "Rocky here, question?"]
SENTENCE_SECONDS = 0.6
GOODNIGHT_SECONDS = 2.0  # long enough that the old doze check always cut it off


def tone(seconds: float, hz: float = 440.0) -> bytes:
    t = np.arange(int(16000 * seconds)) / 16000
    return (0.3 * np.sin(2 * np.pi * hz * t) * 32767).astype(np.int16).tobytes()


class FakeEars:
    """Stands in for brain.ears.Ears: counts robot audio and, once enough
    speech has arrived, reports a question the way the real ears would."""

    def __init__(self, loop):
        self.loop = loop
        self.muted = threading.Event()
        self.source = "robot"
        self.talking = self.hearing = False
        self.level = self.speech_prob = 0.0
        self.bytes = 0
        self.question = "what time is it"
        self.trigger_at = 16000 * 2 * 1  # one second of audio

    def set_source(self, s):
        self.source = s

    def push_audio(self, pcm):
        if self.muted.is_set():
            return
        before = self.bytes
        self.bytes += len(pcm)
        if before < self.trigger_at <= self.bytes:
            now = time.time()
            self.loop.call_soon_threadsafe(brainmain.heard.put_nowait, (f"{self.question}", now - 1, now, now))


class FakeBrain:
    emotion = "happy"

    def __init__(self):
        self.questions = []

    def reply(self, question, jpeg=None, on_emotion=None, cancelled=None, camera_wanted=False):
        self.questions.append(question)
        if on_emotion:
            on_emotion("happy")
        for s in SENTENCES:
            if cancelled is not None and cancelled.is_set():
                return
            yield s

    def abandon(self):
        pass


def fake_stream(text, leveler=None):
    pcm = tone(SENTENCE_SECONDS if text != personality.LINES["sleep"] else GOODNIGHT_SECONDS)
    for i in range(0, len(pcm), 3200):  # 100 ms chunks, like Kokoro's
        time.sleep(0.01)
        yield pcm[i:i + 3200]


class FakeBoard:
    """The Yahboom, as the bridge sees it."""

    def __init__(self):
        self.enc = opus.Encoder(16000, 1, opus.APPLICATION_VOIP)
        self.dec = opus.Decoder(16000, 1)
        self.events = []          # every JSON message from the server
        self.frames = []          # (arrival time, decoded PCM) of every audio frame
        self.tts_stop = asyncio.Event()
        self.tts_start = asyncio.Event()
        self.closed = asyncio.Event()
        self.ws = None
        self.session_id = None

    async def ota(self) -> dict:
        r, w = await asyncio.open_connection("127.0.0.1", 18004)
        body = json.dumps({"application": {"version": "2.2.6"}}).encode()
        w.write(b"POST /xiaozhi/ota/ HTTP/1.1\r\nHost: x\r\nDevice-Id: " + MAC.encode() +
                b"\r\nContent-Type: application/json\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
        await w.drain()
        raw = await r.read()
        w.close()
        return json.loads(raw.split(b"\r\n\r\n", 1)[1])

    async def open(self, url, token):
        self.ws = await connect(url, additional_headers={
            "Authorization": f"Bearer {token}", "Protocol-Version": "1", "Device-Id": MAC,
            "Client-Id": "ff3721bc-7009-4e04-bbb0-96cd183f6336"}, user_agent_header=None)
        await self.ws.send(json.dumps({"type": "hello", "version": 1, "features": {"mcp": True},
                                       "transport": "websocket",
                                       "audio_params": {"format": "opus", "sample_rate": 16000,
                                                        "channels": 1, "frame_duration": 60}}))
        hello = json.loads(await self.ws.recv())
        self.session_id = hello["session_id"]
        self.reader = asyncio.create_task(self._read())
        return hello

    async def _read(self):
        try:
            async for msg in self.ws:
                if isinstance(msg, bytes):
                    self.frames.append((time.monotonic(), self.dec.decode(msg, 2880)))
                    continue
                ev = json.loads(msg)
                self.events.append(ev)
                if ev.get("type") == "tts" and ev.get("state") == "start":
                    self.tts_start.set()
                if ev.get("type") == "tts" and ev.get("state") == "stop":
                    self.tts_stop.set()
                if ev.get("type") == "mcp":
                    req = ev["payload"]
                    if req.get("method") == "initialize":
                        result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                                  "serverInfo": {"name": "esp-box-lite", "version": "2.2.6"}}
                    elif req.get("method") == "tools/list":
                        result = {"tools": [{"name": "self.get_device_status"},
                                            {"name": "self.audio_speaker.set_volume"}]}
                    else:
                        result = {"content": [{"type": "text", "text": "true"}], "isError": False}
                    await self.send({"type": "mcp", "payload": {"jsonrpc": "2.0", "id": req["id"], "result": result}})
        except websockets.ConnectionClosed:
            pass
        finally:
            self.closed.set()

    async def send(self, obj):
        obj.setdefault("session_id", self.session_id)
        await self.ws.send(json.dumps(obj))

    async def speak(self, seconds):
        pcm = tone(seconds, 220)
        for i in range(0, len(pcm) - 1919, 1920):
            await self.ws.send(self.enc.encode(pcm[i:i + 1920], 960))
            await asyncio.sleep(0.005)


@unittest.skipUnless(HAVE_OPUS, "opuslib_next / libopus not installed")
class BridgeEndToEnd(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        loop = asyncio.get_running_loop()
        brainmain.main_loop = loop
        brainmain.brain = self.fake_brain = FakeBrain()
        brainmain.ears = self.ears = FakeEars(loop)
        brainmain.heard = asyncio.Queue()
        brainmain.speech_started = asyncio.Event()
        brainmain.robot_speak_done = asyncio.Event()
        brainmain.devices = brainmain.Registry()
        brainmain.awake_until = 0.0
        brainmain.sleeping = True
        brainmain.current_reply = None
        self._stream = mouth.stream
        mouth.stream = fake_stream
        self.brain_server = await websockets.serve(brainmain.handle_robot, "127.0.0.1", 18765)
        self.voice = asyncio.create_task(brainmain.voice_loop())
        self.doze = asyncio.create_task(brainmain.doze_loop())  # the once-a-second doze check that used to cut him off
        self.bridge = asyncio.create_task(bridge.main())
        await asyncio.sleep(0.3)

    async def asyncTearDown(self):
        mouth.stream = self._stream
        self.voice.cancel()
        self.doze.cancel()
        self.bridge.cancel()
        self.brain_server.close()
        await self.brain_server.wait_closed()
        await asyncio.sleep(0.1)

    async def test_conversation(self):
        board = FakeBoard()
        ota = await board.ota()
        self.assertEqual(ota["websocket"]["url"], "ws://127.0.0.1:18003/xiaozhi/v1/")
        self.assertEqual(ota["websocket"]["token"], "test-board-token")
        self.assertEqual(ota["firmware"]["version"], "2.2.6")

        hello = await board.open(ota["websocket"]["url"], ota["websocket"]["token"])
        self.assertEqual(hello["audio_params"]["sample_rate"], 16000)
        await asyncio.sleep(0.3)
        roles = {r for d in brainmain.devices.summary() for r in d["roles"]}
        self.assertEqual([d["fw"] for d in brainmain.devices.summary()], ["2.2.6"])  # learned from the OTA check
        self.assertEqual(roles, {"mic", "speaker"})

        # Wake word audio comes before "listen detect" and must not reach the brain.
        await board.speak(0.3)
        await board.send({"type": "listen", "state": "detect", "text": "Computer"})
        await asyncio.sleep(0.2)
        self.assertEqual(self.ears.bytes, 0)
        self.assertGreater(brainmain.awake_until, time.time())

        # The question.
        await board.send({"type": "listen", "state": "start", "mode": "auto"})
        await board.speak(1.2)
        await asyncio.wait_for(board.tts_start.wait(), 5)
        self.assertEqual(self.fake_brain.questions, ["what time is it"])
        await asyncio.wait_for(board.tts_stop.wait(), 10)

        # Audio: all of both sentences, as 60 ms frames, delivered in real time.
        expected = len(SENTENCES) * SENTENCE_SECONDS
        got = sum(len(p) for _, p in board.frames) / 32000
        self.assertAlmostEqual(got, expected, delta=0.13)
        span = board.frames[-1][0] - board.frames[0][0]
        self.assertGreater(span, expected - bridge.PREBUFFER_FRAMES * 0.06 - 0.2)
        self.assertTrue(any(e.get("type") == "llm" and e.get("emotion") == "happy" for e in board.events))

        # The brain waits for the board to finish; the board says so by listening again.
        self.assertIsNotNone(brainmain.current_reply)
        await board.send({"type": "listen", "state": "start", "mode": "auto"})
        for _ in range(40):
            if brainmain.current_reply is None:
                break
            await asyncio.sleep(0.05)
        self.assertIsNone(brainmain.current_reply)

        # Volume goes through the board's own MCP tool.
        await brainmain.set_volume(0.5)
        await asyncio.sleep(0.2)
        # (the fake board answered; the bridge logged it — no error means it was accepted)

        # A second voice board can't take the speaker while this one has it.
        extra = await connect("ws://127.0.0.1:18765")
        await extra.send(json.dumps({"type": "hello", "token": "test-robot-token", "roles": ["speaker"]}))
        with self.assertRaises(websockets.ConnectionClosed) as cm:
            await asyncio.wait_for(extra.recv(), 3)
        self.assertEqual(cm.exception.rcvd.code, 1013)

        # A camera board can join alongside and only gets what's meant for it.
        cam = await connect("ws://127.0.0.1:18765")
        await cam.send(json.dumps({"type": "hello", "who": "xiao", "token": "test-robot-token", "roles": ["camera"]}))
        first = json.loads(await asyncio.wait_for(cam.recv(), 3))
        self.assertEqual(first["type"], "stream")
        await brainmain.send_to_robot({"type": "pan", "deg": 10})       # no neck anywhere: dropped
        await brainmain.send_to_robot({"type": "emotion", "name": "sad"})
        second = json.loads(await asyncio.wait_for(cam.recv(), 3))
        self.assertEqual(second, {"type": "emotion", "name": "sad"})
        await cam.close()

        # Interrupting: a reply is cut off by abort and the board stops getting audio.
        board.tts_start.clear(); board.tts_stop.clear()
        self.ears.bytes = 0
        self.ears.question = "tell me a story"
        await board.speak(1.2)
        await asyncio.wait_for(board.tts_start.wait(), 5)
        await asyncio.sleep(0.4)
        await board.send({"type": "abort", "reason": "wake_word_detected"})
        n = len(board.frames)
        await asyncio.sleep(0.8)
        self.assertLessEqual(len(board.frames) - n, 2)
        for _ in range(40):
            if brainmain.current_reply is None:
                break
            await asyncio.sleep(0.05)
        self.assertIsNone(brainmain.current_reply)

        # Dozing off ends the board's session; the brain forgets the board.
        await board.send({"type": "listen", "state": "start", "mode": "auto"})
        await brainmain.send_to_robot({"type": "asleep", "on": True})
        await asyncio.wait_for(board.closed.wait(), 3)
        await asyncio.sleep(0.3)
        self.assertEqual(len(brainmain.devices), 0)


    async def test_goodnight_is_heard_in_full(self):
        """ "Go to sleep": the whole goodnight line plays, then a pause, then the board's session ends."""
        brainconfig.SLEEP_DELAY_SECONDS = 0.5
        board = FakeBoard()
        ota = await board.ota()
        await board.open(ota["websocket"]["url"], ota["websocket"]["token"])
        await board.send({"type": "listen", "state": "detect", "text": "Computer"})
        await board.send({"type": "listen", "state": "start", "mode": "auto"})
        self.ears.question = "Rocky, go to sleep"
        await board.speak(1.2)
        await asyncio.wait_for(board.tts_stop.wait(), 10)
        stopped = time.monotonic()
        heard = sum(len(p) for _, p in board.frames) / 32000
        self.assertAlmostEqual(heard, GOODNIGHT_SECONDS, delta=0.13)   # every word arrived
        self.assertFalse(board.closed.is_set())
        await asyncio.sleep(0.3)                                        # the board drains its speaker...
        await board.send({"type": "listen", "state": "start", "mode": "auto"})  # ...and says so
        await asyncio.wait_for(board.closed.wait(), 5)
        self.assertGreater(time.monotonic() - stopped, 0.3 + brainconfig.SLEEP_DELAY_SECONDS - 0.1)
        self.assertEqual(brainmain.awake_until, 0.0)

    async def test_bridge_never_cuts_a_reply(self):
        """Even if "asleep" arrives mid-reply, the bridge lets the reply finish first."""
        board = FakeBoard()
        ota = await board.ota()
        await board.open(ota["websocket"]["url"], ota["websocket"]["token"])
        await board.send({"type": "listen", "state": "detect", "text": "Computer"})
        await board.send({"type": "listen", "state": "start", "mode": "auto"})
        await asyncio.sleep(0.2)
        speaking = asyncio.create_task(brainmain.say("A fairly long line."))
        await asyncio.wait_for(board.tts_start.wait(), 5)
        await brainmain.send_to_robot({"type": "asleep", "on": True})   # rude: mid-reply
        await asyncio.wait_for(board.tts_stop.wait(), 5)
        self.assertFalse(board.closed.is_set())
        await board.send({"type": "listen", "state": "start", "mode": "auto"})
        await asyncio.wait_for(board.closed.wait(), 3)
        await speaking
        heard = sum(len(p) for _, p in board.frames) / 32000
        self.assertAlmostEqual(heard, SENTENCE_SECONDS, delta=0.13)


class BridgePieces(unittest.TestCase):
    def test_framer(self):
        f = bridge.Framer(4)
        self.assertEqual(f.push(b"abcdef"), [b"abcd"])
        self.assertEqual(f.push(b"gh"), [b"efgh"])
        self.assertIsNone(f.flush())
        f.push(b"x")
        self.assertEqual(f.flush(), b"x\x00\x00\x00")

    def test_ota_reply(self):
        r = bridge.ota_reply({"application": {"version": "2.2.6"}}, now=1_790_000_000)
        self.assertEqual(r["firmware"], {"version": "2.2.6", "url": ""})
        self.assertEqual(r["server_time"]["timestamp"], 1_790_000_000_000)
        self.assertTrue(r["websocket"]["url"].endswith("/xiaozhi/v1/"))
        self.assertNotIn("mqtt", r)  # no MQTT section = the board uses the WebSocket

    def test_firmware_version(self):
        self.assertEqual(bridge.firmware_version({"application": {"version": "2.2.6"}}), "2.2.6")
        self.assertEqual(bridge.firmware_version({}, "esp-box-lite/2.2.6"), "2.2.6")
        self.assertEqual(bridge.firmware_version({"application": "x"}, ""), "?")
        self.assertEqual(bridge.firmware_version({}), "?")

    def test_allowlist(self):
        self.assertTrue(bridge.device_allowed("80:45:6B:24:76:30"))
        self.assertFalse(bridge.device_allowed("aa:bb:cc:dd:ee:ff"))


if __name__ == "__main__":
    unittest.main()
