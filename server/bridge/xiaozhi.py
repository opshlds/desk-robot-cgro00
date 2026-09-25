"""Xiaozhi bridge: lets a board running stock xiaozhi-esp32 firmware (the
Yahboom voice board) be the brain's mic and speaker.

Run with:  python -m bridge.xiaozhi   (from server/, venv active; see start-bridge.sh)

    Yahboom ──HTTP POST──▶ :8004 /xiaozhi/ota/   "your WebSocket is ws://<host>:8003/xiaozhi/v1/"
    Yahboom ◀─WebSocket──▶ :8003  ◀──WebSocket──▶ brain :8765
              Opus 16 kHz 60 ms          PCM 16 kHz s16le, roles mic + speaker

One brain connection per board session: when the board opens its channel
(after its wake word) the bridge connects to the brain with roles mic +
speaker; when the channel closes, so does the brain connection.

What maps to what:
  board listen detect        -> brain {"type": "wake"}   (the board's own wake word)
  board listen start         -> mic audio flows; also = "finished playing" after a reply
  board Opus audio           -> decoded -> brain 0x01 PCM (only while listening)
  board abort                -> brain {"type": "abort"}, queued audio dropped
  brain speak_begin          -> board tts start
  brain 0x01 PCM             -> Opus 60 ms frames, sent in real time (5 frames ahead)
  brain speak_end            -> board tts stop after the last frame
  brain emotion              -> board llm emotion
  brain volume               -> board MCP tool self.audio_speaker.set_volume
  brain asleep on            -> close the board's channel (it goes back to waiting for its wake word)

Settings (server/.env): ROBOT_TOKEN (shared with the brain), and optionally
  BRIDGE_HOST       address the board is told to connect to (default 192.168.1.99)
  XIAOZHI_TOKEN     token the board must present (sent to it by the OTA reply)
  XIAOZHI_DEVICES   comma-separated MACs allowed to connect (empty = any)
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
import uuid
from http import HTTPStatus

import websockets
from websockets.asyncio.client import connect
from websockets.asyncio.server import ServerConnection, serve

from brain import config  # loads server/.env

SAMPLE_RATE = 16_000
FRAME_MS = 60
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000      # 960
FRAME_BYTES = FRAME_SAMPLES * 2                     # 1920 bytes of s16le
PREBUFFER_FRAMES = 5          # sent at once so the board starts smoothly; the rest in real time
PLAYBACK_GRACE = 2.0          # seconds after tts stop to assume playback ended if the board stays quiet
MAX_DECODE_SAMPLES = 2880     # 180 ms: a safe upper bound for one incoming Opus packet

BRIDGE_HOST = os.environ.get("BRIDGE_HOST", "192.168.1.99")
OTA_PORT = int(os.environ.get("BRIDGE_OTA_PORT", "8004"))
WS_PORT = int(os.environ.get("BRIDGE_WS_PORT", "8003"))
WS_PATH = "/xiaozhi/v1/"
BRAIN_URL = os.environ.get("BRIDGE_BRAIN_URL", f"ws://127.0.0.1:{config.PORT}")
XIAOZHI_TOKEN = os.environ.get("XIAOZHI_TOKEN", "")
ALLOWED = {m.strip().lower() for m in os.environ.get("XIAOZHI_DEVICES", "").split(",") if m.strip()}

END = object()  # end-of-reply marker in the outgoing frame queue


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def device_allowed(device_id: str) -> bool:
    return not ALLOWED or device_id.strip().lower() in ALLOWED


# Firmware version of each board, learned from its OTA check at boot: the
# board only reports it there (body + User-Agent), not on the WebSocket.
known_fw: dict[str, str] = {}


def firmware_version(body: dict, user_agent: str = "") -> str:
    """The version a board reports: application.version in the OTA body,
    else the tail of its User-Agent ("esp-box-lite/2.2.6"), else "?"."""
    version = (body.get("application") or {}).get("version") if isinstance(body.get("application"), dict) else None
    if version:
        return str(version)
    if "/" in user_agent:
        return user_agent.rsplit("/", 1)[-1].strip() or "?"
    return "?"


def ota_reply(body: dict, now: float | None = None) -> dict:
    """What the board gets back from its OTA check at boot: where the
    WebSocket is, the time, and "your firmware is current" (we never update
    it over the air: it's our own build)."""
    now = time.time() if now is None else now
    version = str((body.get("application") or {}).get("version") or "0.0.0")
    ws = {"url": f"ws://{BRIDGE_HOST}:{WS_PORT}{WS_PATH}"}
    if XIAOZHI_TOKEN:
        ws["token"] = XIAOZHI_TOKEN
    return {
        "server_time": {"timestamp": int(now * 1000),
                        "timezone_offset": time.localtime(now).tm_gmtoff // 60},
        "firmware": {"version": version, "url": ""},
        "websocket": ws,
    }


class Framer:
    """Collects PCM of any length and hands out whole 60 ms frames."""

    def __init__(self, frame_bytes: int = FRAME_BYTES) -> None:
        self.frame_bytes = frame_bytes
        self.buf = bytearray()

    def push(self, pcm: bytes) -> list[bytes]:
        self.buf += pcm
        out = []
        while len(self.buf) >= self.frame_bytes:
            out.append(bytes(self.buf[:self.frame_bytes]))
            del self.buf[:self.frame_bytes]
        return out

    def flush(self) -> bytes | None:
        """The last partial frame, padded with silence (None if empty)."""
        if not self.buf:
            return None
        frame = bytes(self.buf) + b"\x00" * (self.frame_bytes - len(self.buf))
        self.buf.clear()
        return frame

    def clear(self) -> None:
        self.buf.clear()


def make_codecs():
    import opuslib_next as opus
    enc = opus.Encoder(SAMPLE_RATE, 1, opus.APPLICATION_VOIP)
    dec = opus.Decoder(SAMPLE_RATE, 1)
    return enc, dec


# ── OTA (plain HTTP, port 8004) ─────────────────────────────────────────────

async def handle_http(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    peer = writer.get_extra_info("peername")
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10)
        lines = head.decode("latin-1").split("\r\n")
        method, path, _ = (lines[0].split(" ", 2) + ["", ""])[:3]
        headers = {}
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        length = min(int(headers.get("content-length", "0") or 0), 64 * 1024)
        raw = await asyncio.wait_for(reader.readexactly(length), timeout=10) if length else b""
        device_id = headers.get("device-id", "")
        if not path.startswith("/xiaozhi/ota"):
            status, payload = HTTPStatus.NOT_FOUND, {"error": "not found"}
        elif not device_allowed(device_id):
            log(f"OTA: refused device {device_id or '?'} from {peer[0] if peer else '?'}")
            status, payload = HTTPStatus.FORBIDDEN, {"error": "device not allowed"}
        else:
            try:
                body = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                body = {}
            body = body if isinstance(body, dict) else {}
            payload = ota_reply(body)
            fw = firmware_version(body, headers.get("user-agent", ""))
            if fw != "?":
                known_fw[device_id.lower()] = fw
            status = HTTPStatus.OK
            log(f"OTA: {device_id} ({headers.get('user-agent', '?')}) -> {payload['websocket']['url']}")
        data = json.dumps(payload).encode()
        writer.write(f"HTTP/1.1 {status.value} {status.phrase}\r\nContent-Type: application/json\r\n"
                     f"Content-Length: {len(data)}\r\nConnection: close\r\n\r\n".encode() + data)
        await writer.drain()
    except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError, ValueError):
        pass
    finally:
        writer.close()


# ── WebSocket (port 8003) ────────────────────────────────────────────────────

def check_request(connection: ServerConnection, request):
    """Refuse before the WebSocket opens: wrong path, unknown board, bad token."""
    h = request.headers
    device_id = h.get("Device-Id", "")
    if not request.path.startswith(WS_PATH.rstrip("/")):
        return connection.respond(HTTPStatus.NOT_FOUND, "not found\n")
    if not device_allowed(device_id):
        log(f"WS: refused device {device_id or '?'} (not in XIAOZHI_DEVICES)")
        return connection.respond(HTTPStatus.FORBIDDEN, "device not allowed\n")
    if XIAOZHI_TOKEN:
        offered = h.get("Authorization", "").removeprefix("Bearer ").strip()
        if not secrets.compare_digest(offered.encode(), XIAOZHI_TOKEN.encode()):
            log(f"WS: refused device {device_id}: bad token")
            return connection.respond(HTTPStatus.UNAUTHORIZED, "bad token\n")
    return None


sessions: dict[str, "Session"] = {}  # device id -> its live session


class Session:
    """One open channel from a board, and its matching brain connection."""

    def __init__(self, dev: ServerConnection, device_id: str) -> None:
        self.dev = dev
        self.device_id = device_id
        self.session_id = str(uuid.uuid4())
        self.brain = None
        self.fw = "?"
        self.encoder, self.decoder = make_codecs()
        self.framer = Framer()
        self.out: asyncio.Queue = asyncio.Queue()
        self.listening = False        # board is streaming the mic to us
        self.speaking = False         # a reply is being sent to the board
        self.discard = False          # drop brain audio until the next speak_begin (after an abort)
        self.awaiting_done = False    # tts stop sent; waiting for the board to finish playing
        self.done_timer: asyncio.TimerHandle | None = None
        self.mic_on = True
        self.new_reply = True        # restart the real-time clock for the next frame sent
        self.tools: set[str] = set()
        self.mcp_id = 0
        self.frames_up = 0
        self.frames_down = 0

    # ── to the board ──
    async def to_dev(self, obj: dict) -> None:
        obj.setdefault("session_id", self.session_id)
        try:
            await self.dev.send(json.dumps(obj))
        except websockets.ConnectionClosed:
            pass

    async def mcp(self, method: str, params: dict) -> None:
        self.mcp_id += 1
        await self.to_dev({"type": "mcp", "payload": {"jsonrpc": "2.0", "id": self.mcp_id,
                                                        "method": method, "params": params}})

    # ── to the brain ──
    async def to_brain(self, data) -> None:
        if self.brain is None:
            return
        try:
            await self.brain.send(json.dumps(data) if isinstance(data, dict) else data)
        except websockets.ConnectionClosed:
            pass

    def playback_finished(self) -> None:
        if not self.awaiting_done:
            return
        self.awaiting_done = False
        if self.done_timer is not None:
            self.done_timer.cancel()
            self.done_timer = None
        asyncio.ensure_future(self.to_brain({"type": "speak_done"}))

    def drop_output(self) -> None:
        while not self.out.empty():
            self.out.get_nowait()
        self.framer.clear()

    # ── the three loops ──
    async def from_device(self) -> None:
        async for msg in self.dev:
            if isinstance(msg, bytes):
                if self.listening and not self.speaking and self.mic_on:
                    try:
                        pcm = self.decoder.decode(msg, MAX_DECODE_SAMPLES)
                    except Exception:
                        continue  # a corrupt packet; skip it
                    self.frames_up += 1
                    await self.to_brain(b"\x01" + pcm)
                continue
            try:
                ev = json.loads(msg)
            except json.JSONDecodeError:
                continue
            if not isinstance(ev, dict):
                continue
            kind = ev.get("type")
            if kind == "listen":
                state = ev.get("state")
                if state == "detect":
                    log(f"[{self.device_id}] wake word: {ev.get('text', '')}")
                    await self.to_brain({"type": "wake", "word": str(ev.get("text", ""))})
                elif state == "start":
                    self.listening = True
                    self.playback_finished()  # in auto mode the board listens again once playback drained
                elif state == "stop":
                    self.listening = False
            elif kind == "abort":
                log(f"[{self.device_id}] abort ({ev.get('reason', 'button')})")
                self.drop_output()
                self.speaking = False
                self.discard = True
                self.new_reply = True
                self.awaiting_done = False
                await self.to_brain({"type": "abort"})
            elif kind == "mcp":
                result = (ev.get("payload") or {}).get("result") or {}
                if isinstance(result, dict) and isinstance(result.get("tools"), list):
                    self.tools = {t.get("name") for t in result["tools"] if isinstance(t, dict)}
                    log(f"[{self.device_id}] board tools: {', '.join(sorted(self.tools))}")
                err = (ev.get("payload") or {}).get("error")
                if err:
                    log(f"[{self.device_id}] board MCP error: {err}")
            elif kind == "hello":
                pass  # a repeat hello on an open channel; nothing to do
            else:
                log(f"[{self.device_id}] board: {ev}")

    async def from_brain(self) -> None:
        async for msg in self.brain:
            if isinstance(msg, bytes):
                if msg[:1] == b"\x01" and self.speaking and not self.discard:
                    for frame in self.framer.push(msg[1:]):
                        await self.out.put(self.encoder.encode(frame, FRAME_SAMPLES))
                continue
            try:
                ev = json.loads(msg)
            except json.JSONDecodeError:
                continue
            kind = ev.get("type") if isinstance(ev, dict) else None
            if kind == "speak_begin":
                self.drop_output()
                self.discard = False
                self.new_reply = True
                self.speaking = True
                self.listening = False
                self.awaiting_done = False
                await self.to_dev({"type": "tts", "state": "start"})
            elif kind == "speak_end":
                if self.discard or not self.speaking:
                    continue
                last = self.framer.flush()
                if last is not None:
                    await self.out.put(self.encoder.encode(last, FRAME_SAMPLES))
                await self.out.put(END)
            elif kind == "emotion":
                await self.to_dev({"type": "llm", "text": "", "emotion": str(ev.get("name", "neutral"))})
            elif kind == "volume":
                try:
                    level = round(max(0.0, min(1.0, float(ev.get("level")))) * 100)
                except (TypeError, ValueError):
                    continue
                if "self.audio_speaker.set_volume" in self.tools:
                    await self.mcp("tools/call", {"name": "self.audio_speaker.set_volume",
                                                  "arguments": {"volume": level}})
                    log(f"[{self.device_id}] volume -> {level}")
            elif kind == "mic":
                self.mic_on = bool(ev.get("on", True))
            elif kind == "asleep" and ev.get("on"):
                log(f"[{self.device_id}] brain dozed off: closing the board's channel")
                await self.dev.close(1000, "asleep")
                return

    async def to_device_paced(self) -> None:
        """Send reply frames in real time (a few ahead), then tts stop."""
        loop = asyncio.get_running_loop()
        start = None
        n = 0
        while True:
            item = await self.out.get()
            if item is END:
                await self.to_dev({"type": "tts", "state": "stop"})
                self.speaking = False
                self.awaiting_done = True
                # If the board never says it's listening again, don't leave the brain waiting.
                wait = PLAYBACK_GRACE + (max(0.0, start + n * FRAME_MS / 1000 - loop.time()) if start else 0.0)
                self.done_timer = loop.call_later(wait, self.playback_finished)
                start, n = None, 0
                continue
            if self.discard:
                continue
            if start is None or self.new_reply:
                start, n = loop.time(), 0
                self.new_reply = False
            due = start + max(0, n - PREBUFFER_FRAMES) * FRAME_MS / 1000
            delay = due - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
            try:
                await self.dev.send(item)
            except websockets.ConnectionClosed:
                return
            n += 1
            self.frames_down += 1

    async def run(self) -> None:
        try:
            first = await asyncio.wait_for(self.dev.recv(), timeout=10)
            hello = json.loads(first) if isinstance(first, str) else {}
        except (asyncio.TimeoutError, json.JSONDecodeError, websockets.ConnectionClosed):
            hello = {}
        if not isinstance(hello, dict) or hello.get("type") != "hello":
            log(f"[{self.device_id}] no hello; closing")
            await self.dev.close(1002)
            return
        params = hello.get("audio_params") or {}
        if params.get("format", "opus") != "opus" or int(params.get("sample_rate", SAMPLE_RATE)) != SAMPLE_RATE:
            log(f"[{self.device_id}] unexpected audio params {params}; carrying on at 16 kHz Opus")
        # The WebSocket handshake doesn't carry the version; the OTA check did.
        self.fw = firmware_version({}, self.dev.request.headers.get("User-Agent", ""))
        if self.fw == "?":
            self.fw = known_fw.get(self.device_id.lower(), "?")

        token = os.environ.get("ROBOT_TOKEN", "")
        try:
            self.brain = await connect(BRAIN_URL, max_size=256 * 1024, open_timeout=5)
            await self.brain.send(json.dumps({"type": "hello", "who": "xiaozhi-bridge", "fw": self.fw,
                                              "token": token, "roles": ["mic", "speaker"],
                                              "device": self.device_id}))
        except (OSError, websockets.WebSocketException, asyncio.TimeoutError) as e:
            log(f"[{self.device_id}] can't reach the brain at {BRAIN_URL}: {e}")
            await self.dev.close(1011, "brain unavailable")
            return

        await self.to_dev({"type": "hello", "transport": "websocket", "session_id": self.session_id,
                           "audio_params": {"format": "opus", "sample_rate": SAMPLE_RATE,
                                            "channels": 1, "frame_duration": FRAME_MS}})
        await self.mcp("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                      "clientInfo": {"name": "hail-e-bridge", "version": "1"}})
        await self.mcp("tools/list", {"cursor": ""})
        log(f"[{self.device_id}] session open (fw {self.fw}) <-> brain")

        tasks = [asyncio.create_task(c) for c in (self.from_device(), self.from_brain(), self.to_device_paced())]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                if t.exception() and not isinstance(t.exception(), websockets.ConnectionClosed):
                    log(f"[{self.device_id}] error: {t.exception()!r}")
        finally:
            for t in tasks:
                t.cancel()
            if self.done_timer is not None:
                self.done_timer.cancel()
            code = getattr(self.brain, "close_code", None)
            await self.brain.close()
            await self.dev.close()
            if code == 1013:
                log(f"[{self.device_id}] the brain refused: another board already has the mic or speaker")
            log(f"[{self.device_id}] session closed ({self.frames_up} frames heard, {self.frames_down} spoken)")


async def handle_device(dev: ServerConnection) -> None:
    device_id = dev.request.headers.get("Device-Id", "?")
    old = sessions.get(device_id)
    if old is not None:  # the board reconnected before its old channel timed out
        await old.dev.close(1000, "replaced")
    session = Session(dev, device_id)
    sessions[device_id] = session
    try:
        await session.run()
    finally:
        if sessions.get(device_id) is session:
            del sessions[device_id]


async def main() -> None:
    if not os.environ.get("ROBOT_TOKEN"):
        log("WARNING: ROBOT_TOKEN is not set in server/.env — the brain will refuse the bridge")
    if not XIAOZHI_TOKEN:
        log("note: XIAOZHI_TOKEN not set — any board that can reach this port may connect")
    if ALLOWED:
        log(f"boards allowed: {', '.join(sorted(ALLOWED))}")
    ota = await asyncio.start_server(handle_http, "0.0.0.0", OTA_PORT)
    log(f"OTA      http://{BRIDGE_HOST}:{OTA_PORT}/xiaozhi/ota/")
    log(f"WebSocket ws://{BRIDGE_HOST}:{WS_PORT}{WS_PATH}  <->  brain {BRAIN_URL}")
    async with ota, serve(handle_device, "0.0.0.0", WS_PORT, process_request=check_request,
                          max_size=64 * 1024, ping_interval=20, ping_timeout=20):
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
