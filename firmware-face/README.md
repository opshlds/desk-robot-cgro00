# firmware-face: HAIL-E's round AMOLED face

Firmware for the **Waveshare ESP32-S3-Touch-AMOLED-1.43** (466x466 AMOLED, FT3168 touch). This is Part 3 of the desk robot. The board becomes the robot's face and joins the brain on ai1 as role `face`.

| Milestone | State |
|---|---|
| M0 check screen (driver chip, colours, touch, COM port, MAC) | in this build (first 3 s after boot, or `test on`) |
| M1 standalone face, console control | done (0.1.1, verified on the board: CO5300 panel, ~45 fps) |
| M2 WiFi + brain link (role `face`), touches to the brain | **this build (0.2.0)** |
| M3 mouth sync (`mouth` levels from the brain) | firmware ready (0.2.0 obeys `mouth`); brain side next |
| M4 gaze, burn-in care | later |

## Layout
```
design/face_params.json   the face: layout + 7 emotions (single source)
design/face_engine.js     reference engine used by the Face Lab page
design/face_lab.html      Face Lab: open in a browser to preview and tune
tools/gen_params.py       face_params.json -> include/face_params.h
tools/make_lv_conf.py     regenerates include/lv_conf.h from LVGL's template
include/                  lv_conf.h (LVGL 8.4), face_params.h (generated)
src/face_engine.*         port of face_engine.js: animation + draw list
src/face_view.*           draws that list with LVGL, redraws only what changed
src/display.*             QSPI panel (SH8601 or CO5300) + LVGL driver
src/touch.*               FT3168 -> tap / long / swipe_l / swipe_r
src/link.*                WiFi + WebSocket to the brain (role face), settings in NVS
src/main.cpp              boot, loop, serial console
```
Face Lab and the firmware run the same engine. A host-side test compares their draw lists frame by frame and they match. So a face tuned in Face Lab looks the same on the board.

## Quickest: flash the prebuilt image with esptool
`bin/face-fw-0.2.0-factory.bin` is a complete image (bootloader, partitions and app), built from this source. esptool is already on the PC.

```
esptool --port COMx read-flash 0 ALL waveshare-amoled-factory.bin     (one-time backup, 16 MB)
esptool --port COMx write-flash 0x0 bin\face-fw-0.2.0-factory.bin
```
To restore the backup: `esptool --port COMx write-flash 0x0 waveshare-amoled-factory.bin`.

## Build and flash from source (Windows PC)
1. Install VS Code and its **PlatformIO IDE** extension. Or, from a terminal: `pip install platformio`.
2. Open this folder (`F:\tmp\desktop-haile-bot\firmware-face`).
3. Plug in the board with USB-C. If Windows doesn't show a new COM port, hold **BOOT**, tap **RESET**, then let go of BOOT.
4. Build and flash with `pio run -t upload`, or the arrow at the bottom of VS Code. The first build downloads the ESP32 toolchain and core from GitHub (about 700 MB), which takes a few minutes.
5. Open the console with `pio device monitor` (115200), or the plug icon.

To upload to a specific port: `pio run -t upload --upload-port COM9`.

## M0: first light check (about 5 minutes)
When the board boots it shows a **check screen** for 3 s, then the face. The console prints the firmware version, panel driver, touch state and **MAC**. Note the COM port and MAC.

- **Screen stays black:** the board may have the other driver chip. Type `panel co5300` (or `panel sh8601`). It saves the choice and reboots.
- **Check screen:** the thin white ring should sit right on the glass edge and be evenly centred. The three thick arcs should read **red** (lower left), **green** (top) and **blue** (right). If red and blue are swapped, tell Claude.
- **Touch:** type `test on`, then drag a finger. A white dot should follow it. `test off` goes back to the face.
- The console prints `-> {"type":"touch","gesture":"tap"}` for each tap, long press and swipe.

## M1: the face
Try these commands:
```
emo happy        emo sad      emo angry     emo surprised
emo sleepy       emo thinking emo neutral
sleep on         sleep off
talk on          talk off     (synthetic voice drives the mouth)
demo             the whole "Computer, what time is it?" ... "go to sleep" sequence
look 1 0         idle off     blink
bright 120       bright auto
stats on         frame rate and redraw load every 5 s
info
```
Tap the screen while he's asleep to wake him. Hold it to put him to sleep. For now the board does this itself. From M2 on it only reports the gesture and the brain decides.

Things to report back:
- The `stats on` numbers during `talk on` and during `emo thinking` (the spinning ring redraws most of the screen).
- Any seams, jagged edges or tearing on the mouth and eyelids.
- Whether the brightness levels suit the room: 190 awake, 45 asleep, out of 255.

## M2: on WiFi with the brain
Set these once in the console. They're saved on the board, not in the source.
```
token <ROBOT_TOKEN>            the value in ~/desk-robot/server/.env on ai1
wifi IOTNSFW <password>        saves and reboots (the SSID may contain spaces; the password is the last word)
brain 192.168.1.99 8765        only if the brain moves (this is the default)
net                            WiFi + brain status
```
Once it's connected, the brain console's `status` lists `amoled-face (fw 0.2.0) [face]`. From then on the brain drives the face: emotions, sleep, and mouth levels (M3). Touches go to the brain (needs patch 0008). Tap wakes him (the Yahboom still needs "Computer" before it listens). Long press puts him to sleep. When the brain isn't connected, the board handles touches itself.

## Changing the face
1. Tune in Face Lab (the published page, or `design/face_lab.html`), then **Copy all parameters**.
2. Paste the result over `design/face_params.json`.
3. Run `python tools/gen_params.py`, then build and flash.

## Notes
- LVGL 8.4.0 and Arduino_GFX 1.6.8 on the Arduino-ESP32 3.3.11 core (pioarduino 55.03.311). GFX 1.6.2 no longer compiles on this core.
- AMOLED burn-in: the background stays black (pixels off), and the face dims when he's asleep. M4 adds slow whole-face drift and dims the screen when nobody is around.
- The panel wants even start and odd end coordinates. The LVGL rounder handles this.
