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


class FakeFace:
    """The AMOLED face, as the brain sees it: records every mouth level."""

    def __init__(self):
        self.mouth = []           # (arrival time, level)
        self.ws = None

    async def open(self):
        self.ws = await connect("ws://127.0.0.1:18765")
        await self.ws.send(json.dumps({"type": "hello", "who": "amoled-face", "fw": "0.2.0",
                                       "token": "test-robot-token", "roles": ["face"]}))
        self.reader = asyncio.create_task(self._read())

    async def _read(self):
        try:
            async for msg in self.ws:
                ev = json.loads(msg)
                if ev.get("type") == "mouth":
                    self.mouth.append((time.monotonic(), ev["level"]))
        except websockets.ConnectionClosed:
            pass

    async def close(self):
        await self.ws.close()


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

    async def test_mouth_follows_the_voice(self):
        """Mouth levels reach the face while he talks, at the audio's pace,
        end at 0, and stop when he's interrupted."""
        delay = brainconfig.MOUTH_DELAY_SECONDS
        brainconfig.MOUTH_DELAY_SECONDS = 0.05
        self.addCleanup(setattr, brainconfig, "MOUTH_DELAY_SECONDS", delay)
        face = FakeFace()
        await face.open()
        board = FakeBoard()
        ota = await board.ota()
        await board.open(ota["websocket"]["url"], ota["websocket"]["token"])
        await board.send({"type": "listen", "state": "detect", "text": "Computer"})
        await board.send({"type": "listen", "state": "start", "mode": "auto"})
        await board.speak(1.2)
        await asyncio.wait_for(board.tts_stop.wait(), 10)
        await asyncio.sleep(0.5)                       # the last frames play out, then the 0

        levels = [lv for _, lv in face.mouth]
        self.assertGreater(len(levels), 5)
        self.assertGreater(max(levels), 0.8)           # the tone opens the mouth wide
        self.assertEqual(levels[-1], 0.0)              # and it closes at the end
        self.assertTrue(all(0.0 <= lv <= 1.0 for lv in levels))
        # From the first level to the closing 0 = the reply's length (20 frames of 60 ms).
        expected = len(SENTENCES) * SENTENCE_SECONDS
        span = face.mouth[-1][0] - face.mouth[0][0]
        self.assertAlmostEqual(span, expected, delta=0.25)
        # Never more often than once a frame; never silent for longer than the keepalive while talking.
        gaps = [b[0] - a[0] for a, b in zip(face.mouth, face.mouth[1:])]
        self.assertLess(max(gaps[:-1]), bridge.MOUTH_KEEPALIVE + 0.1)
        self.assertLessEqual(len(levels), expected / 0.06 + 3)
        # The brain holds each level back MOUTH_DELAY_SECONDS behind the board's audio.
        self.assertGreater(face.mouth[0][0], board.frames[0][0] + 0.03)

        # Interrupted: a 0 right away, and nothing after it.
        await board.send({"type": "listen", "state": "start", "mode": "auto"})
        board.tts_start.clear()
        self.ears.bytes = 0
        self.ears.question = "tell me a story"
        await board.speak(1.2)
        await asyncio.wait_for(board.tts_start.wait(), 5)
        await asyncio.sleep(0.4)
        before = len(face.mouth)
        await board.send({"type": "abort", "reason": "wake_word_detected"})
        await asyncio.sleep(0.05 + 0.15)               # the brain's delay, plus a little
        self.assertGreater(len(face.mouth), before)    # he was talking, and the 0 arrived
        self.assertEqual(face.mouth[-1][1], 0.0)
        settled = len(face.mouth)
        await asyncio.sleep(1.0)
        self.assertEqual(face.mouth[settled:], [])
        await face.close()

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

    def test_frame_level(self):
        silence = b"\x00" * bridge.FRAME_BYTES
        self.assertEqual(bridge.frame_level(silence), 0.0)
        self.assertEqual(bridge.frame_level(b""), 0.0)
        loud = tone(0.06)                               # 0.3 full scale: about -13.5 dBFS
        self.assertGreater(bridge.frame_level(loud), 0.85)
        full = (np.ones(960) * 32767).astype(np.int16).tobytes()
        self.assertEqual(bridge.frame_level(full), 1.0)
        hiss = (np.sin(np.arange(960)) * 100).astype(np.int16).tobytes()   # about -53 dBFS
        self.assertEqual(bridge.frame_level(hiss), 0.0)
        # Louder never opens it less; the curve shapes the middle.
        amps = [0.005, 0.01, 0.03, 0.1, 0.2, 0.3]
        got = [bridge.frame_level((np.sin(np.arange(960) / 3) * a * 32767).astype(np.int16).tobytes())
               for a in amps]
        self.assertEqual(got, sorted(got))
        mid = (np.sin(np.arange(960) / 3) * 0.05 * 32767).astype(np.int16).tobytes()   # about -29 dBFS
        self.assertAlmostEqual(bridge.frame_level(mid, curve=1.0), (-29.0 + 45) / 35, delta=0.03)
        self.assertLess(bridge.frame_level(mid, curve=1.5), bridge.frame_level(mid, curve=1.0))

    def test_allowlist(self):
        self.assertTrue(bridge.device_allowed("80:45:6B:24:76:30"))
        self.assertFalse(bridge.device_allowed("aa:bb:cc:dd:ee:ff"))


class MouthClockTiming(unittest.IsolatedAsyncioTestCase):
    async def test_levels_at_play_time_then_zero(self):
        loop = asyncio.get_running_loop()
        sent = []
        clock = bridge.MouthClock(lambda lv: sent.append((loop.time(), lv)))
        t0 = loop.time()
        levels = [0.5, 0.5, 0.5, 0.5, 0.9, 0.2]
        for n, lv in enumerate(levels):                 # all handed over at once, like the prebuffer
            clock.frame(n, lv)
        clock.finish(len(levels))
        await asyncio.sleep(0.5)
        # 0.5 at 0 ms, (unchanged 60/120 ms skipped), 0.5 again at 180 ms, 0.9, 0.2, then 0 at 360 ms.
        self.assertEqual([lv for _, lv in sent], [0.5, 0.5, 0.9, 0.2, 0.0])
        at = [round((t - t0) / 0.06) for t, _ in sent]
        self.assertEqual(at, [0, 3, 4, 5, 6])

    async def test_stop_cancels_what_is_due(self):
        loop = asyncio.get_running_loop()
        sent = []
        clock = bridge.MouthClock(lambda lv: sent.append(lv))
        for n in range(10):
            clock.frame(n, 0.7)
        await asyncio.sleep(0.1)
        clock.stop()
        await asyncio.sleep(0.7)
        self.assertEqual(sent, [0.7, 0.0])
        clock.stop()                                    # already closed: no second 0
        self.assertEqual(sent, [0.7, 0.0])

    async def test_a_late_frame_moves_the_clock(self):
        loop = asyncio.get_running_loop()
        sent = []
        clock = bridge.MouthClock(lambda lv: sent.append((loop.time(), lv)))
        clock.frame(0, 0.3)
        await asyncio.sleep(0.3)                        # the voice fell behind: frame 1 is 240 ms late
        t1 = loop.time()
        clock.frame(1, 0.8)
        clock.frame(2, 0.4)
        await asyncio.sleep(0.2)
        self.assertEqual([lv for _, lv in sent], [0.3, 0.8, 0.4])
        self.assertAlmostEqual(sent[1][0] - t1, 0.0, delta=0.02)
        self.assertAlmostEqual(sent[2][0] - t1, 0.06, delta=0.02)


if __name__ == "__main__":
    unittest.main()
