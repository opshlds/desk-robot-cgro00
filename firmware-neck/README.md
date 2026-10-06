# firmware-neck: HAIL-E's pan/tilt neck

Firmware for an **ESP32-C3 dev board** (DevKitM-1, SuperMini, ...) that drives the two neck servos (SG90 class). It joins the brain on ai1 as role `neck`. The brain already knows how to use a neck: the `look` tool ("Rocky, look left"), face tracking ("Rocky, track me") and the Head sliders on the web console.

| Version | What |
|---|---|
| **0.1.1** | OTA from the brain (two app slots: `min_spiffs.csv`, sha256, rollback unless the brain answers), `ota` on the console; `wifi` refuses a network name with quotes/brackets; `rest`, `trim`, `speed`, `limits`, `raw`, `pan`/`tilt`, `txpower` refuse anything but a number (pasted status lines); `invert`, `limits`, `trim`, `speed`, `relax` print the axis's full state afterwards, and `invert` releases the servo (a powered servo would otherwise swing to its mirror position); `rest` stays inside the tilt limits and the clamp is saved; defaults suit an up-only tilt (`limits tilt 0 20`, `rest 0`) |
| 0.1.0 | role `neck`; smooth, speed-capped moves; calibration saved on the board (limits, trim, direction, speed, relax, pins); limits reported to the brain; head bows while he sleeps |

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
esptool --port COMx write-flash 0x0 bin\neck-fw-0.1.1-factory.bin        (new board: full image, blanks the settings)
```
**From 0.1.0 (one app slot, `huge_app.csv`) to 0.1.1 (two slots, `min_spiffs.csv`)**, keeping WiFi, token, txpower and calibration: write the partition table, reset the boot choice and write the app; the bootloader stays.
```
esptool --port COM11 write-flash 0x8000 bin\neck-fw-0.1.1-partitions.bin 0xe000 bin\boot_app0.bin 0x10000 bin\neck-fw-0.1.1-app.bin
```
(NVS at 0x9000 is in the same place in both tables. Don't use the factory image for this: it pads 0x9000 with 0xFF and wipes the settings.) After that, updates come from the brain (below).
If the port doesn't connect: hold **BOOT**, tap **RESET**, let go of BOOT. The console (PuTTY, 115200) works on either the native USB port or the USB-serial chip, whichever the board has.

## Over the air (from the brain console on ai1)
Since 0.1.1 the board updates itself from the brain (brain patch 0018; docs/protocol.md "Firmware updates"). One USB flash of 0.1.1 first (below), then:
```
python -m tools.fw_add neck ~/neck-fw-<x.y.z>-app.bin     on ai1, in ~/desk-robot/server with the venv active
ota                                                       brain console: boards, versions, images
ota neck                                                 installs the newest image (ota neck <version> [force])
```
The board downloads into its spare slot, checks the size and sha256, reports progress and reboots into it. The new image is **on trial**: it keeps itself once the brain answers its hello and rolls back by itself if that hasn't happened within 60 s. `ota` on the board's console shows the running slot, its state and the last update. While it downloads, the head holds still: moves from the brain and the console are refused.
- **After an OTA the board may run from `ota_1`.** A later USB app-only flash must also reset the boot choice, or the board keeps booting the other slot: `esptool --port COMx write-flash 0xe000 boot_app0.bin 0x10000 bin\neck-fw-<version>-app.bin` (`boot_app0.bin` is in `bin\`).
- Test builds from the same source: `pio run -e neck_bump` (a trivial version bump, 0.1.2) and `pio run -e neck_rbtest` (0.1.3-rbtest, can never reach the brain, so it must roll back).

## First setup (console)
```
token <ROBOT_TOKEN>            the value in ~/desk-robot/server/.env on ai1
wifi IOTNSFW <password>        saves and reboots
net                            WiFi + brain status
txpower 8.5                    only if WiFi won't connect (common on C3 SuperMini boards); txpower 0 = default
```
The brain console's `status` then lists `neck (fw 0.1.0, ...) [neck]`. The servos stay limp until the first move.

## Calibration (servo power on, watch the mechanism)
Head degrees: 0 = straight ahead / level; pan − = his left (your right as you face him); tilt + = up, tilt − = down.
The factory defaults (`defaults`) suit the mini FPV kit: pan −40..40, tilt **0..20, up only**, rest 0. That kit's tilt cannot bow below level (level is its mechanical bottom). A bracket that can also look down: `limits tilt -30 20` and `rest -20`.
1. `center`: the first pulse snaps both servos to 0. If the head isn't straight/level, `trim pan <deg>` / `trim tilt <deg>` until it is. A horn that sits more than about 20° off (the `trim` range) cannot be trimmed away: with the Korad **off**, lift the head off the horn and reseat it a tooth or two (SG90: 21 teeth, about 17° each).
2. `pan 20`: he should turn to *his* right. If not, `invert pan on`. Tilt: if level is the bottom stop, the wrong direction pushes *into* it, so test small: `limits tilt -5 5`, then `tilt 5` and `tilt -5`; + must be the free (up) one, else `invert tilt on`. **`invert` releases the servo** (limp) because a powered servo would swing to its mirror position; run `center` afterwards (watch it). Read the line it prints (`normal` or `inverted`) before moving on.
3. Find each end stop with small steps: `raw pan 45`, `raw pan 50`, ... stop one step **before** the bracket binds, the servo buzzes, or the Korad's current jumps. Same for `raw pan -…` and `raw tilt …` (up); on a bracket that looks down also `raw tilt -…`.
4. Save them: `limits pan <min> <max>`, `limits tilt <min> <max>` (min ≤ 0 ≤ max; an up-only tilt is `limits tilt 0 <max>`). The board prints the axis's new state and the brain gets the limits at once.
5. `sweep pan`, `sweep tilt`: a slow min → max → 0 pass to confirm.
6. `rest <tilt>`: where the head goes while he sleeps (default 0; it is kept inside the tilt limits, and a `limits tilt` that excludes it moves it and saves that).
7. With the face + camera on the head: if the tilt sags when the servo goes limp, `relax tilt off` (it holds, at a small current cost). `speed pan|tilt <deg/s>` if moves look too fast.

`info` shows everything; `defaults` resets the calibration (WiFi and token stay).

## How it behaves
- Moves ease out toward the target, capped at the axis speed (180 / 120 deg/s by default), and the servo stops being pulsed 1.5 s after it arrives (no buzz, no heat), unless `relax … off`.
- `asleep` on: pan 0, tilt to the rest pose, then both go limp. Awake: back to 0/0.
- Idle head glances exist (`glance on`), but the brain turns them off on connect; the eyes on the face do the idle glancing.
