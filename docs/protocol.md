# Robot ↔ brain WebSocket protocol (v0)

The robot connects to `ws://<mac-ip>:8765`. The first message must be a
`hello` carrying the shared `ROBOT_TOKEN` (server/.env, firmware secrets.h);
the server drops anything else without replying.

## Several boards, one robot

A robot can be several boards, each doing some of the jobs. The hello says
which with `"roles"`: any of `mic`, `speaker`, `camera`, `neck`, `face`.
No `roles` = the classic single board, which has all of them. Each role
belongs to one board at a time: a board asking for a role that another
connected board holds is closed with code 1013 (it should retry; that is
also what happens while a rebooted board's old connection is still
timing out). The server sends each command only to the boards whose roles
it concerns (`server/brain/devices.py`, ROUTES):

| Server -> board | Goes to |
|---|---|
| `speak_begin`, `speak_end`, `volume`, TTS audio | speaker |
| `mic` | mic |
| `stream` | camera |
| `pan`, `tilt`, `glance` | neck |
| `mouth` | face |
| `emotion`, `asleep` | every board |

and only accepts board messages from the matching role: mic audio from the
mic, camera frames from the camera, `speak_done`, `abort` and `mouth` from
the speaker, `wake` from the mic, `touch` from the face.

HAIL-E's layout: the Yahboom voice board (through `server/bridge/`, which
translates the Xiaozhi protocol) = `mic` + `speaker`; the XIAO = `camera`
(+ `neck` later); the AMOLED = `face`. Plain `ws://` on the home LAN — no TLS in v1. Text frames are JSON. Binary
frames start with one type byte: `0x01` = mic audio, `0x02` = camera JPEG.
The rest of the frame is the payload.

## Robot → server

```json
{"type": "hello", "who": "desk-robot", "fw": "0.3.0", "token": "..."}  // must be the first message; token = ROBOT_TOKEN
{"type": "hello", "who": "xiaozhi-bridge", "fw": "2.2.6", "token": "...", "roles": ["mic", "speaker"]}  // a board with some roles
{"type": "wake", "word": "Computer"}   // mic: the board's own wake word fired; stay awake, the question follows
{"type": "abort"}                 // speaker: the human interrupted; stop the reply that is playing
{"type": "state", "pan": 12.5, "emotion": "neutral"}
{"type": "temp", "c": 52.0}       // chip temperature, sent every ~10 s
{"type": "speak_done"}            // finished playing the last reply
{"type": "touch", "gesture": "tap"}   // face: tap | long | swipe_l | swipe_r. Tap wakes him, long press = sleep
{"type": "mouth", "level": 0.62}  // speaker: how open his mouth is right now, 0..1 (see Mouth sync)
```

Binary `0x01` frames: microphone audio, 16 kHz mono signed 16-bit PCM,
little-endian, 30 ms (480 samples) per frame.

Binary `0x02` frames: one camera JPEG per frame, QVGA (320×240) by
default, sent continuously at `fps` while streaming is on. The server keeps
the latest frame for the live-view page, the face tracker, and the language model.

## Server → robot

```json
{"type": "emotion", "name": "happy"}
{"type": "pan", "deg": -20}
{"type": "tilt", "deg": 15}
{"type": "speak_begin", "bytes": 0}      // binary TTS audio frames follow; bytes = total if known, 0 = streaming (robot starts after 200 ms of audio)
{"type": "speak_end"}             // no more audio; robot replies speak_done when played out
{"type": "volume", "level": 0.4}  // speaker volume 0.0-1.0
{"type": "asleep", "on": true}    // eyes shut + Z's; false = wake up. Asleep = no idle glances.
{"type": "glance", "on": false}   // allow/forbid the firmware's idle head glances; the server sends off on connect and leaves them off
{"type": "mic", "on": true}       // stream the microphone to the server
{"type": "stream", "on": true, "fps": 10}   // start/stop the camera stream, set rate
{"type": "mouth", "level": 0.62}  // face: open the mouth this far (0 = closed); it closes by itself 300 ms after the last one
```

The server sends `stream on` at `CAMERA_FPS` when the robot connects. The
live view is served by `server/brain/eyes.py` at http://localhost:8766/.

Binary frames: type byte `0x01` then TTS audio for the speaker, same
PCM format as above, ~100 ms per frame. The server streams each reply as
the voice generates it (`bytes: 0`); the robot starts playing after 200 ms
of audio is buffered and keeps a 32 s ring buffer in PSRAM. The server
waits for `speak_done` before it un-mutes the microphone.

## Mouth sync (face screen)

The brain sends TTS audio faster than real time, so only the speaker end
knows when each bit of it plays. For the Yahboom that is the bridge
(`server/bridge/xiaozhi.py`), which sends one 60 ms Opus frame every 60 ms,
5 frames ahead:

1. As it cuts a reply into 60 ms frames, the bridge measures each frame's
   loudness: RMS in dBFS, mapped linearly from −45 dBFS (0, closed) to
   −10 dBFS (1, wide open), then `** curve` (1.0). `BRIDGE_MOUTH_FLOOR_DB`,
   `BRIDGE_MOUTH_CEIL_DB` and `BRIDGE_MOUTH_CURVE` in `server/.env` override these.
2. Frame n of a reply is reported at `t0 + n × 60 ms` (t0 = when the first
   frame went out), the time it would play with no buffering. This is
   independent of the 5-ahead send pacing. A frame that turns up after its
   nominal time (the voice fell behind) moves the clock to when it arrives.
3. The bridge sends `{"type": "mouth", "level": x}` to the brain (rounded to
   2 decimals) when the level changes, or every 180 ms while it doesn't. After
   the last frame it sends `0`, and also at once on `abort` or a new
   `speak_begin`. That's at most about 17 messages a second.
4. The brain accepts `mouth` only from the board with the speaker role, and
   passes it to the face `MOUTH_DELAY_SECONDS` later (config.py, 0.15 s by
   default: the voice board's own playback latency). Change it live with
   `mouthdelay <s>` in the console (`mouthdelay` alone prints it) or the
   slider on the live-view page. Nothing is sent when no face is connected.

The face (firmware 0.2.x) eases toward each level (25 ms attack / 70 ms
release) and closes its mouth 300 ms after the last message.

Firmware side: `firmware/src/link.cpp` maps each server message onto the
same text commands the USB console uses (`emo`, `pan`, `tilt`), so both
paths behave identically. The robot sends `state` every 5 s.

Keep this file in sync with `firmware/src/link.cpp` and `server/brain/main.py`
whenever a message type is added.
