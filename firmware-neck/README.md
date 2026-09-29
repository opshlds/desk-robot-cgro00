# firmware-neck: HAIL-E's pan/tilt neck

Firmware for an **ESP32-C3 dev board** (DevKitM-1, SuperMini, ...) that drives the two neck servos (SG90 class). It joins the brain on ai1 as role `neck`. The brain already knows how to use a neck: the `look` tool ("Rocky, look left"), face tracking ("Rocky, track me") and the Head sliders on the web console.

| Version | What |
|---|---|
| **0.1.0** | role `neck`; smooth, speed-capped moves; calibration saved on the board (limits, trim, direction, speed, relax, pins); limits reported to the brain; head bows while he sleeps |

## Wiring
**Never power the servos from the board's 5V or 3V3 pins.** A stalled SG90 pulls about 0.7 A and browns out the board.

```
servo supply +5 V  ──────────────── both servos' RED
servo supply GND   ──┬───────────── both servos' BROWN
                     └───────────── ESP32-C3 GND   (common ground is required)
ESP32-C3 GPIO4     ──────────────── pan servo  ORANGE (signal)
ESP32-C3 GPIO5     ──────────────── tilt servo ORANGE (signal)
ESP32-C3 USB       ──────────────── PC or USB charger (powers the board only)
```
- Bench: the Korad at **5.0 V, current limit 1.5 A**. Robot: the fused 1.35 A servo branch with 1000 µF (see `claude/robot-power.md` in the project).
- Other pins: `pins <pan> <tilt>` (any two of 0, 1, 3, 4, 5, 6, 7, 10).

## Flash (esptool, from the PC)
```
esptool --port COMx write-flash 0x0 bin\neck-fw-0.1.0-factory.bin        (first time: full image)
esptool --port COMx write-flash 0x10000 bin\neck-fw-0.1.0-app.bin       (updates: keeps the settings)
```
If the port doesn't connect: hold **BOOT**, tap **RESET**, let go of BOOT. The console (PuTTY, 115200) works on either the native USB port or the USB-serial chip, whichever the board has.

## First setup (console)
```
token <ROBOT_TOKEN>            the value in ~/desk-robot/server/.env on ai1
wifi IOTNSFW <password>        saves and reboots
net                            WiFi + brain status
txpower 8.5                    only if WiFi won't connect (common on C3 SuperMini boards); txpower 0 = default
```
The brain console's `status` then lists `neck (fw 0.1.0, ...) [neck]`. The servos stay limp until the first move.

## Calibration (servo power on, watch the mechanism)
Head degrees: 0 = straight ahead / level; pan − = his left (your right as you face him); tilt − = down.
1. `center`: the first pulse snaps both servos to 0. If the head isn't straight/level, `trim pan <deg>` / `trim tilt <deg>` until it is.
2. `pan 20`: he should turn to *his* right. If not, `invert pan on`. Same for `tilt -20` (should look down; else `invert tilt on`).
3. Find each end stop with small steps: `raw pan 45`, `raw pan 50`, ... stop one step **before** the bracket binds, the servo buzzes, or the Korad's current jumps. Same for `raw pan -…`, `raw tilt -…` (down) and `raw tilt …` (up, if the bracket allows it).
4. Save them: `limits pan <min> <max>`, `limits tilt <min> <max>` (min ≤ 0 ≤ max). The brain gets them at once.
5. `sweep pan`, `sweep tilt`: a slow min → max → 0 pass to confirm.
6. `rest <tilt>`: how far the head bows while he sleeps (default −20).
7. With the face + camera on the head: if the tilt sags when the servo goes limp, `relax tilt off` (it holds, at a small current cost). `speed pan|tilt <deg/s>` if moves look too fast.

`info` shows everything; `defaults` resets the calibration (WiFi and token stay).

## How it behaves
- Moves ease out toward the target, capped at the axis speed (180 / 120 deg/s by default), and the servo stops being pulsed 1.5 s after it arrives (no buzz, no heat), unless `relax … off`.
- `asleep` on: pan 0, tilt to the rest pose, then both go limp. Awake: back to 0/0.
- Idle head glances exist (`glance on`), but the brain turns them off on connect; the eyes on the face do the idle glancing.
